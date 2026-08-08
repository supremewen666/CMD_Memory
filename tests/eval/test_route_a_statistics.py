"""Route A inference primitives (BUILD SPEC §2.1, §4.2-§4.4, §3.7, §6.3).

Expected values here come from an independent source: the split buckets and the
sample sizes were computed by hand from the frozen formulas in the spec, not by
calling the implementation.
"""

import math
import unittest

from cmd_audit.eval.route_a_statistics import (
    SPLIT_SALT,
    EffectWording,
    InsufficientVarianceBasis,
    assign_split_tier,
    crossfit_family_folds,
    design_variance,
    effect_wording,
    family_paired_differences,
    family_blocked_lower_bound,
    sign_flip_p_value,
    tier3_sample_size,
)


class SplitTest(unittest.TestCase):
    """§2.1 burned-data split. Buckets 0-5 dev, 6-7 search, 8-9 select."""

    def test_salt_is_frozen(self) -> None:
        self.assertEqual(SPLIT_SALT, "route-a-split-v1")

    def test_bucket_matches_hand_computed_sha256(self) -> None:
        # Independently computed: sha256("route-a-split-v1|user-beta") % 10 == 4.
        self.assertEqual(assign_split_tier("user-beta"), "D_dev")
        # sha256("route-a-split-v1|user-alpha") % 10 == 9.
        self.assertEqual(assign_split_tier("user-alpha"), "D_select")

    def test_assignment_is_stable_across_calls(self) -> None:
        first = [assign_split_tier(f"group-{index}") for index in range(20)]
        second = [assign_split_tier(f"group-{index}") for index in range(20)]
        self.assertEqual(first, second)

    def test_every_tier_is_one_of_three(self) -> None:
        tiers = {assign_split_tier(f"group-{index}") for index in range(200)}
        self.assertEqual(tiers, {"D_dev", "D_search", "D_select"})


class FamilyPairingTest(unittest.TestCase):
    """§3.7 D_f is a within-family paired mean difference."""

    def test_paired_difference_is_computed_within_family(self) -> None:
        # family A: artifact mean 1.0, baseline mean 0.5 -> +0.5
        # family B: artifact mean 0.0, baseline mean 1.0 -> -1.0
        rows = (
            ("A", "c1", 1, 1),
            ("A", "c2", 1, 0),
            ("B", "c3", 0, 1),
        )
        differences = family_paired_differences(
            [
                {
                    "family_id": family,
                    "case_id": case,
                    "artifact": artifact,
                    "baseline": baseline,
                }
                for family, case, artifact, baseline in rows
            ],
            artifact_key="artifact",
            baseline_key="baseline",
        )
        self.assertEqual(dict(differences), {"A": 0.5, "B": -1.0})

    def test_missing_arm_for_a_case_is_an_explicit_failure(self) -> None:
        with self.assertRaises(ValueError):
            family_paired_differences(
                [{"family_id": "A", "case_id": "c1", "artifact": 1}],
                artifact_key="artifact",
                baseline_key="baseline",
            )

    def test_nonfinite_outcome_is_an_explicit_failure(self) -> None:
        with self.assertRaises(ValueError):
            family_paired_differences(
                [
                    {
                        "family_id": "A",
                        "case_id": "c1",
                        "artifact": float("nan"),
                        "baseline": 0,
                    }
                ],
                artifact_key="artifact",
                baseline_key="baseline",
            )


class FamilyBlockedIntervalTest(unittest.TestCase):
    """§4.4 one-sided family-blocked bootstrap over family effects."""

    def test_all_positive_families_give_a_positive_lower_bound(self) -> None:
        values = [("f%d" % index, 0.5) for index in range(40)]
        lower = family_blocked_lower_bound(values, samples=2000, seed=24)
        self.assertAlmostEqual(lower, 0.5, places=9)

    def test_mixed_families_can_straddle_zero(self) -> None:
        values = [("f%d" % index, 1.0 if index % 2 else -1.0) for index in range(40)]
        lower = family_blocked_lower_bound(values, samples=2000, seed=24)
        self.assertLess(lower, 0.0)

    def test_lower_bound_is_seed_reproducible(self) -> None:
        values = [("f%d" % index, index / 40.0 - 0.4) for index in range(40)]
        first = family_blocked_lower_bound(values, samples=2000, seed=24)
        second = family_blocked_lower_bound(values, samples=2000, seed=24)
        self.assertEqual(first, second)

    def test_empty_input_is_an_explicit_failure(self) -> None:
        with self.assertRaises(ValueError):
            family_blocked_lower_bound([], samples=2000, seed=24)


class SignFlipTest(unittest.TestCase):
    """§4.4 randomization check by family-level sign flip."""

    def test_uniform_positive_effect_is_extreme_under_the_null(self) -> None:
        values = [("f%d" % index, 0.5) for index in range(20)]
        p_value = sign_flip_p_value(values, draws=999, seed=24)
        self.assertLess(p_value, 0.01)

    def test_zero_effect_is_not_extreme(self) -> None:
        values = [("f%d" % index, 0.0) for index in range(20)]
        self.assertGreater(sign_flip_p_value(values, draws=999, seed=24), 0.5)

    def test_p_value_is_never_zero(self) -> None:
        """The observed assignment counts as one draw, so p >= 1/(draws+1)."""
        values = [("f%d" % index, 1.0) for index in range(30)]
        p_value = sign_flip_p_value(values, draws=99, seed=24)
        self.assertGreaterEqual(p_value, 1.0 / 100.0)


class DesignVarianceTest(unittest.TestCase):
    """§4.2 sigma_design over hard-pass top-decile spec pairs only."""

    def _spec_rows(self, spec_id: str, values: dict[str, float], *, hard_pass: bool):
        return [
            {
                "domain": "memtrace",
                "spec_id": spec_id,
                "family_id": family,
                "state_success": value,
                "hard_gates_pass": hard_pass,
            }
            for family, value in values.items()
        ]

    def test_failed_specs_cannot_inflate_the_variance(self) -> None:
        """A wild-variance spec that fails its hard gates is excluded."""
        rows = []
        rows += self._spec_rows("good_a", {"f1": 1.0, "f2": 1.0, "f3": 0.0}, hard_pass=True)
        rows += self._spec_rows("good_b", {"f1": 1.0, "f2": 0.0, "f3": 0.0}, hard_pass=True)
        rows += self._spec_rows(
            "broken", {"f1": 0.0, "f2": 1.0, "f3": 0.0}, hard_pass=False
        )
        result = design_variance(rows)
        # good_a - good_b family differences are (0, 1, 0): SD = sqrt(1/3).
        self.assertAlmostEqual(result.sigma_design, math.sqrt(1.0 / 3.0), places=9)
        self.assertNotIn("broken", result.eligible_spec_ids)

    def test_no_eligible_pair_raises_insufficient_variance_basis(self) -> None:
        rows = self._spec_rows("only", {"f1": 1.0, "f2": 1.0}, hard_pass=True)
        with self.assertRaises(InsufficientVarianceBasis):
            design_variance(rows)

    def test_domain_with_one_eligible_spec_is_reported_not_used(self) -> None:
        rows = []
        rows += self._spec_rows("good_a", {"f1": 1.0, "f2": 1.0, "f3": 0.0}, hard_pass=True)
        rows += self._spec_rows("good_b", {"f1": 1.0, "f2": 0.0, "f3": 0.0}, hard_pass=True)
        rows += [
            {
                "domain": "stale",
                "spec_id": "lonely",
                "family_id": "g1",
                "state_success": 1.0,
                "hard_gates_pass": True,
            }
        ]
        result = design_variance(rows)
        excluded = dict(result.excluded_domains)
        self.assertIn("stale", excluded)
        self.assertTrue(excluded["stale"])  # exclusion carries a reason code
        self.assertNotIn("stale", result.contributing_domains)

    def test_sigma_is_the_maximum_across_domains(self) -> None:
        rows = []
        rows += self._spec_rows("a", {"f1": 1.0, "f2": 1.0}, hard_pass=True)
        rows += self._spec_rows("b", {"f1": 1.0, "f2": 1.0}, hard_pass=True)
        wide = [
            {
                "domain": "stale",
                "spec_id": spec_id,
                "family_id": family,
                "state_success": value,
                "hard_gates_pass": True,
            }
            for spec_id, values in (
                ("c", {"g1": 1.0, "g2": 0.0}),
                ("d", {"g1": 0.0, "g2": 1.0}),
            )
            for family, value in values.items()
        ]
        result = design_variance(rows + wide)
        # memtrace pair differences are (0, 0) -> SD 0; stale pair is (1, -1) -> SD sqrt(2).
        self.assertAlmostEqual(result.sigma_design, math.sqrt(2.0), places=9)


class SampleSizeTest(unittest.TestCase):
    """§4.3. Values below are hand-computed from the frozen formula."""

    def test_matches_hand_calculated_fixture(self) -> None:
        # ((1.644854 + 1.281552) * 0.2 / 0.10) ** 2 = 34.24... -> ceil 35
        # ceil(35 / 0.90) = 39
        result = tier3_sample_size(sigma_design=0.2)
        self.assertEqual(result.n_raw, 35)
        self.assertEqual(result.n_tier3, 39)

    def test_minimum_floor_of_thirty_applies(self) -> None:
        result = tier3_sample_size(sigma_design=0.05)
        self.assertEqual(result.n_raw, 3)
        self.assertEqual(result.n_tier3, 30)

    def test_larger_variance_needs_more_families(self) -> None:
        self.assertEqual(tier3_sample_size(sigma_design=0.5).n_tier3, 239)

    def test_frozen_constants_are_recorded(self) -> None:
        result = tier3_sample_size(sigma_design=0.2)
        self.assertEqual(result.mde, 0.10)
        self.assertEqual(result.power, 0.90)
        self.assertEqual(result.one_sided_alpha, 0.05)
        self.assertEqual(result.expected_usable_family_rate, 0.90)


class EffectWordingTest(unittest.TestCase):
    """§3.7 wording is mechanical, keyed on the point estimate."""

    def test_at_or_above_mde_permits_substantial(self) -> None:
        self.assertEqual(effect_wording(0.10), EffectWording.SUBSTANTIAL_POSITIVE)
        self.assertEqual(effect_wording(0.25), EffectWording.SUBSTANTIAL_POSITIVE)

    def test_between_zero_and_mde_is_below_threshold(self) -> None:
        self.assertEqual(effect_wording(0.05), EffectWording.POSITIVE_BELOW_THRESHOLD)

    def test_zero_or_negative_permits_no_positive_wording(self) -> None:
        self.assertEqual(effect_wording(0.0), EffectWording.NO_POSITIVE_EFFECT)
        self.assertEqual(effect_wording(-0.3), EffectWording.NO_POSITIVE_EFFECT)


class CrossfitFoldsTest(unittest.TestCase):
    """§6.3 five-fold family-grouped cross-fitting."""

    def test_siblings_stay_in_one_fold(self) -> None:
        folds = crossfit_family_folds(
            [f"family-{index}" for index in range(25)], folds=5, seed=24
        )
        self.assertEqual(len(set(folds.values())), 5)
        for family, fold in folds.items():
            self.assertEqual(folds[family], fold)

    def test_fewer_families_than_folds_is_an_explicit_failure(self) -> None:
        with self.assertRaises(ValueError):
            crossfit_family_folds(["a", "b"], folds=5, seed=24)

    def test_folds_are_seed_reproducible(self) -> None:
        families = [f"family-{index}" for index in range(30)]
        first = crossfit_family_folds(families, folds=5, seed=24)
        second = crossfit_family_folds(families, folds=5, seed=24)
        self.assertEqual(first, second)

    def test_folds_are_balanced_within_one_family(self) -> None:
        families = [f"family-{index}" for index in range(30)]
        folds = crossfit_family_folds(families, folds=5, seed=24)
        sizes = [sum(1 for f in folds.values() if f == fold) for fold in range(5)]
        self.assertLessEqual(max(sizes) - min(sizes), 1)


if __name__ == "__main__":
    unittest.main()
