"""Mechanical tier-3 sample size (BUILD SPEC §4.1-§4.3).

The load-bearing claim is that `n_tier3` is a function of D_dev variance and
nothing else. Two failure modes matter more than arithmetic slips:

  * a no-op or gate-failing spec entering the variance basis, which shrinks
    `sigma_design` and understates the required n;
  * a silent fallback to all-spec variance when no domain qualifies, which §4.2
    forbids outright.

Both get their own test. The frozen §4.1 constants are pinned separately, since
an edit there changes every downstream number without changing any logic.
"""

import math
import unittest

from experiments.compute_tier3_power import (
    GOOD_SPEC_PERCENTILE,
    MDE,
    MINIMUM_FAMILIES,
    ONE_SIDED_ALPHA,
    POWER,
    USABLE_FAMILY_RATE,
    Z_ALPHA,
    Z_POWER,
    InsufficientVarianceBasis,
    _percentile,
    aggregate_design_sd,
    domain_design_sd,
    mechanical_sample_size,
)


class FrozenParameterTest(unittest.TestCase):
    """§4.1 is a preregistration, so each value is pinned individually."""

    def test_every_frozen_parameter_matches_the_spec(self) -> None:
        self.assertEqual(MDE, 0.10)
        self.assertEqual(POWER, 0.90)
        self.assertEqual(ONE_SIDED_ALPHA, 0.05)
        self.assertEqual(Z_ALPHA, 1.644854)
        self.assertEqual(Z_POWER, 1.281552)
        self.assertEqual(USABLE_FAMILY_RATE, 0.90)
        self.assertEqual(MINIMUM_FAMILIES, 30)
        self.assertEqual(GOOD_SPEC_PERCENTILE, 90)


class SampleSizeTest(unittest.TestCase):
    """§4.3's two ceilings, against hand-worked values."""

    def test_sample_size_matches_a_hand_worked_example(self) -> None:
        """sigma=0.25: ((1.644854+1.281552)*0.25/0.10)^2 = 53.51 -> 54 -> 60.

        The expected numbers are worked from the spec formula by hand rather
        than recomputed here, so this test can disagree with the code.
        """
        result = mechanical_sample_size(0.25)
        self.assertEqual(result["n_raw"], 54)
        self.assertEqual(result["n_tier3"], 60)

    def test_the_thirty_family_floor_binds_for_small_variance(self) -> None:
        """§4.3's `max(30, ...)` is the only place a tiny SD cannot shrink n."""
        result = mechanical_sample_size(0.01)
        self.assertEqual(result["n_raw"], 1)
        self.assertEqual(result["n_tier3"], MINIMUM_FAMILIES)

    def test_the_usable_family_rate_inflates_rather_than_deflates(self) -> None:
        """n_tier3 must exceed n_raw whenever the floor is not binding, or the
        90% usable-family assumption has been applied in the wrong direction."""
        result = mechanical_sample_size(0.5)
        self.assertGreater(result["n_tier3"], result["n_raw"])
        self.assertEqual(
            result["n_tier3"], math.ceil(result["n_raw"] / USABLE_FAMILY_RATE)
        )

    def test_sample_size_grows_with_variance(self) -> None:
        sizes = [mechanical_sample_size(sd)["n_tier3"] for sd in (0.2, 0.4, 0.8)]
        self.assertEqual(sizes, sorted(sizes))
        self.assertLess(sizes[0], sizes[-1])


class PercentileTest(unittest.TestCase):
    """The 90th-percentile cut has to survive a one-element sample.

    `statistics.quantiles` interpolates and needs two points; a domain with one
    hard-passing spec must reach §4.2's "fewer than two eligible specs" branch
    rather than raise from inside the percentile.
    """

    def test_single_value_sample_returns_that_value(self) -> None:
        self.assertEqual(_percentile([0.42], 90), 0.42)

    def test_nearest_rank_selects_an_observed_value(self) -> None:
        """Nearest-rank, so the cut is always one of the sample's own values."""
        sample = [0.1, 0.4, 0.9]
        self.assertIn(_percentile(sample, 90), sample)
        self.assertEqual(_percentile(sample, 90), 0.9)

    def test_empty_sample_is_an_error_not_a_zero(self) -> None:
        with self.assertRaises(ValueError):
            _percentile([], 90)

    def test_the_rank_is_pinned_away_from_the_clamp_boundary(self) -> None:
        """At small n the 90th percentile lands on the top element, so the
        upper clamp absorbs an off-by-one in the rank and hides it. Ten evenly
        spaced values put the cut at the 9th of 10 (ceil(0.9*10) = 9), one below
        the maximum, where the arithmetic is observable.

        Worked by hand: ranks are 1-indexed, so rank 9 is index 8 = 8/9.
        """
        sample = [index / 9 for index in range(10)]
        self.assertAlmostEqual(_percentile(sample, 90), 8 / 9, places=9)
        self.assertNotEqual(_percentile(sample, 90), max(sample))

    def test_the_median_rank_matches_a_hand_worked_value(self) -> None:
        """A second percentile, far from either clamp, so the rank formula is
        checked rather than just its endpoints: ceil(0.5*4) = 2 -> index 1."""
        self.assertAlmostEqual(_percentile([0.0, 0.25, 0.5, 1.0], 50), 0.25)


def _table(entries):
    """`spec -> case_id -> row` from `(spec, case, success, gates, family)`."""
    table: dict[str, dict[str, dict]] = {}
    for spec, case_id, success, gates, family in entries:
        table.setdefault(spec, {})[case_id] = {
            "state_success": success,
            "hard_gates_pass": gates,
            "family_id": family,
            "domain": "d",
        }
    return table


def _rows(case_family_pairs):
    return [
        {"case_id": case_id, "family_id": family, "domain": "d"}
        for case_id, family in case_family_pairs
    ]


class VarianceBasisTest(unittest.TestCase):
    """§4.2's filter is what keeps the design variance conservative."""

    def test_a_gate_failing_spec_is_excluded_from_the_basis(self) -> None:
        """A spec that fails a hard gate on any covered case cannot contribute.

        Without this filter a gate-failing spec pairs at near-zero variance and
        drags `sigma_design` down, understating the confirmation's sample size.
        """
        rows = _rows([("c1", "f1"), ("c2", "f2")])
        table = _table(
            [
                ("good_a", "c1", 1, True, "f1"),
                ("good_a", "c2", 0, True, "f2"),
                ("good_b", "c1", 0, True, "f1"),
                ("good_b", "c2", 1, True, "f2"),
                ("gate_fail", "c1", 1, False, "f1"),
                ("gate_fail", "c2", 1, True, "f2"),
            ]
        )
        result = domain_design_sd(table, rows, "d")
        self.assertNotIn("gate_fail", result.get("eligible_specs", []))
        self.assertEqual(result["hard_pass_spec_count"], 2)

    def test_a_domain_with_one_eligible_spec_is_reported_not_raised(self) -> None:
        """§4.2: exclude the domain from the maximum and report it."""
        rows = _rows([("c1", "f1"), ("c2", "f2")])
        table = _table(
            [
                ("only", "c1", 1, True, "f1"),
                ("only", "c2", 1, True, "f2"),
            ]
        )
        result = domain_design_sd(table, rows, "d")
        self.assertIsNone(result["design_sd"])
        self.assertIn("fewer than two eligible specs", result["excluded_reason"])

    def test_a_domain_where_no_spec_passes_a_gate_is_excluded(self) -> None:
        rows = _rows([("c1", "f1")])
        table = _table([("bad", "c1", 1, False, "f1")])
        result = domain_design_sd(table, rows, "d")
        self.assertIsNone(result["design_sd"])
        self.assertEqual(result["eligible_spec_count"], 0)

    def test_design_sd_is_the_paired_family_difference_sd(self) -> None:
        """Two specs that disagree on two families: differences are +1 and -1,
        so the sample SD is sqrt(2). Worked by hand, not recomputed."""
        rows = _rows([("c1", "f1"), ("c2", "f2")])
        table = _table(
            [
                ("a", "c1", 1, True, "f1"),
                ("a", "c2", 0, True, "f2"),
                ("b", "c1", 0, True, "f1"),
                ("b", "c2", 1, True, "f2"),
            ]
        )
        result = domain_design_sd(table, rows, "d")
        self.assertAlmostEqual(result["design_sd"], math.sqrt(2), places=9)

    def test_identical_specs_yield_zero_variance(self) -> None:
        """The degenerate case the eligibility filter exists to keep out of the
        maximum: two specs that never differ produce SD 0."""
        rows = _rows([("c1", "f1"), ("c2", "f2")])
        table = _table(
            [
                ("a", "c1", 1, True, "f1"),
                ("a", "c2", 1, True, "f2"),
                ("b", "c1", 1, True, "f1"),
                ("b", "c2", 1, True, "f2"),
            ]
        )
        result = domain_design_sd(table, rows, "d")
        self.assertEqual(result["design_sd"], 0.0)

    def test_the_maximum_pair_is_recorded_for_audit(self) -> None:
        """§4.2 takes a maximum, so which pair supplied it must be inspectable."""
        rows = _rows([("c1", "f1"), ("c2", "f2")])
        table = _table(
            [
                ("a", "c1", 1, True, "f1"),
                ("a", "c2", 0, True, "f2"),
                ("b", "c1", 0, True, "f1"),
                ("b", "c2", 1, True, "f2"),
            ]
        )
        result = domain_design_sd(table, rows, "d")
        self.assertEqual(sorted(result["max_sd_pair"]), ["a", "b"])

    def test_the_widest_disagreeing_pair_wins_not_the_narrowest(self) -> None:
        """§4.2 says *max* over eligible pairs, and the direction is the whole
        conservatism of the estimate: taking the minimum would pick the pair
        that agrees most and understate `sigma_design`.

        Three specs over four families, all with dev `state_success` 0.5 so all
        three clear the 90th-percentile cut, but disagreeing by different
        amounts:

            a: 1 1 0 0
            b: 0 0 1 1   -> differences +1 +1 -1 -1, sample SD sqrt(4/3)
            c: 1 0 1 0   -> vs a: 0 +1 -1 0,         sample SD sqrt(2/3)

        Worked by hand. The maximum must be the `a`/`b` pair.
        """
        rows = _rows([("c1", "f1"), ("c2", "f2"), ("c3", "f3"), ("c4", "f4")])
        pattern = {
            "a": (1, 1, 0, 0),
            "b": (0, 0, 1, 1),
            "c": (1, 0, 1, 0),
        }
        table = _table(
            [
                (spec, f"c{index}", value, True, f"f{index}")
                for spec, values in pattern.items()
                for index, value in enumerate(values, start=1)
            ]
        )
        result = domain_design_sd(table, rows, "d")
        self.assertEqual(result["eligible_spec_count"], 3)
        self.assertEqual(result["pair_count"], 3)
        self.assertAlmostEqual(result["design_sd"], math.sqrt(4 / 3), places=9)
        self.assertEqual(sorted(result["max_sd_pair"]), ["a", "b"])


class AggregationTest(unittest.TestCase):
    """§4.2 step 5: the cross-domain maximum, and the stop when none qualifies."""

    def test_the_widest_domain_supplies_sigma_design(self) -> None:
        """A minimum here would silently pick the most agreeable domain and
        understate the confirmation's sample size."""
        per_domain = [
            {"domain": "narrow", "design_sd": 0.1},
            {"domain": "wide", "design_sd": 0.7},
            {"domain": "mid", "design_sd": 0.4},
        ]
        sigma, source = aggregate_design_sd(per_domain)
        self.assertEqual(sigma, 0.7)
        self.assertEqual(source["domain"], "wide")

    def test_excluded_domains_do_not_participate(self) -> None:
        per_domain = [
            {"domain": "excluded", "design_sd": None, "excluded_reason": "x"},
            {"domain": "kept", "design_sd": 0.3},
        ]
        sigma, source = aggregate_design_sd(per_domain)
        self.assertEqual(sigma, 0.3)
        self.assertEqual(source["domain"], "kept")

    def test_no_finite_domain_stops_rather_than_falling_back(self) -> None:
        """§4.2: emit INSUFFICIENT_VARIANCE_BASIS and stop. A zero or an
        all-spec fallback would size the confirmation off a variance the spec
        explicitly rejects."""
        per_domain = [
            {"domain": "a", "design_sd": None, "excluded_reason": "x"},
            {"domain": "b", "design_sd": None, "excluded_reason": "y"},
        ]
        with self.assertRaises(InsufficientVarianceBasis):
            aggregate_design_sd(per_domain)

    def test_the_stop_names_the_forbidden_fallback(self) -> None:
        """The message is the only place a future reader learns why the command
        refuses to produce a number here."""
        with self.assertRaises(InsufficientVarianceBasis) as caught:
            aggregate_design_sd([{"domain": "a", "design_sd": None}])
        self.assertIn("all-spec variance", str(caught.exception))


class NoFallbackTest(unittest.TestCase):
    """§4.2 forbids widening to all specs when the basis is empty."""

    def test_insufficient_variance_basis_is_a_distinct_error_type(self) -> None:
        """It has to be catchable as its own condition, because §4.2 requires a
        stop rather than a degraded estimate."""
        self.assertTrue(issubclass(InsufficientVarianceBasis, RuntimeError))


if __name__ == "__main__":
    unittest.main()
