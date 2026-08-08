"""§7 E1.5 state-to-answer bridge.

The bridge validates the *metric*, not the artifact, so the tests are about the
places where a bridge could pass for the wrong reason:

  * the support gate (§7.4) -- below 30 state-improved cases across 10 families
    the answer is `BRIDGE_INSUFFICIENT_SUPPORT`, which is explicitly *not*
    evidence against the state metric;
  * the specificity contrast `S_f = I_f - U_f` -- a bridge whose answer gain
    appears on state-unchanged cases too is measuring something other than state
    convergence, and §7.4 says a nonpositive `S_f` blocks causal wording even
    when the primary test passes;
  * stratum assignment -- a case that is neither improved nor unchanged
    (state_success 1 -> 0) is *worsened*, and folding it into either stratum
    would move the estimate.

§7.2's "LLM judge scores are secondary and never become the bridge target" is
pinned too, because the target is the one thing a later edit could quietly swap.
"""

import unittest

from experiments.run_state_answer_bridge import (
    MATERIAL_NEGATIVE_DIRECTION,
    MINIMUM_STATE_IMPROVED_CASES,
    MINIMUM_STATE_IMPROVED_FAMILIES,
    PRIMARY_METRIC,
    BridgeInsufficientSupport,
    Stratum,
    bridge_decision,
    classify_stratum,
    family_improved_means,
    specificity_contrast,
    summarize_support,
)


def _row(case_id, family, frozen_state, candidate_state, gain, domain="d"):
    """One candidate/frozen pair, already scored on both arms."""
    return {
        "case_id": case_id,
        "family_id": family,
        "domain": domain,
        "state_success_frozen": frozen_state,
        "state_success_candidate": candidate_state,
        "answer_gain": gain,
    }


class FrozenParameterTest(unittest.TestCase):
    """§7.3/§7.4 are preregistered, so each value is pinned individually."""

    def test_the_primary_metric_is_the_deterministic_answer_score(self) -> None:
        """§7.2: judge scores are secondary and never the target. Swapping this
        constant is the quiet way to make the bridge an LLM-judged test."""
        self.assertEqual(PRIMARY_METRIC, "answer_score")

    def test_the_support_minimums_match_the_spec(self) -> None:
        self.assertEqual(MINIMUM_STATE_IMPROVED_CASES, 30)
        self.assertEqual(MINIMUM_STATE_IMPROVED_FAMILIES, 10)


class StratumTest(unittest.TestCase):
    """§7.4's two named strata, plus the third case the spec implies."""

    def test_zero_to_one_is_state_improved(self) -> None:
        self.assertEqual(classify_stratum(0, 1), Stratum.IMPROVED)

    def test_equal_scores_are_state_unchanged(self) -> None:
        self.assertEqual(classify_stratum(0, 0), Stratum.UNCHANGED)
        self.assertEqual(classify_stratum(1, 1), Stratum.UNCHANGED)

    def test_one_to_zero_is_worsened_not_unchanged(self) -> None:
        """§7.4 defines improved and unchanged; a regression is neither. Folding
        it into `unchanged` would put a case whose state got worse into the
        specificity contrast's reference stratum."""
        self.assertEqual(classify_stratum(1, 0), Stratum.WORSENED)


    def test_a_fractional_state_score_is_refused(self) -> None:
        """`state_success` is a boolean check, so 0.5 has no stratum.

        The value can only reach here from a malformed pairs file. Silently
        sorting it -- into improved, because the candidate ended at 1 -- would
        add a partial-credit case to the count the support gate bounds and to the
        mean the bridge reports.
        """
        with self.assertRaises(ValueError) as caught:
            classify_stratum(0.5, 1)
        self.assertIn("state_success_frozen", str(caught.exception))
        with self.assertRaises(ValueError):
            classify_stratum(0, 0.5)


class SupportGateTest(unittest.TestCase):
    """§7.4: at least 30 state-improved cases across at least 10 families."""

    def test_ample_support_passes(self) -> None:
        rows = [
            _row(f"c{i}", f"f{i // 3}", 0, 1, 0.5)
            for i in range(30)
        ]
        support = summarize_support(rows)
        self.assertEqual(support.state_improved_cases, 30)
        self.assertEqual(support.state_improved_families, 10)
        self.assertTrue(support.sufficient)

    def test_one_case_short_is_insufficient(self) -> None:
        rows = [_row(f"c{i}", f"f{i // 3}", 0, 1, 0.5) for i in range(29)]
        self.assertFalse(summarize_support(rows).sufficient)

    def test_enough_cases_in_too_few_families_is_insufficient(self) -> None:
        """40 improved cases in 4 families gives the family-blocked interval
        four resampling units, not 40."""
        rows = [_row(f"c{i}", f"f{i % 4}", 0, 1, 0.5) for i in range(40)]
        support = summarize_support(rows)
        self.assertEqual(support.state_improved_cases, 40)
        self.assertEqual(support.state_improved_families, 4)
        self.assertFalse(support.sufficient)

    def test_unchanged_and_worsened_cases_do_not_count_toward_support(self) -> None:
        """Support is transition support: only the improved stratum carries the
        transition the bridge measures."""
        rows = [_row(f"c{i}", f"f{i}", 1, 1, 0.5) for i in range(40)]
        rows += [_row(f"w{i}", f"g{i}", 1, 0, 0.5) for i in range(40)]
        support = summarize_support(rows)
        self.assertEqual(support.state_improved_cases, 0)
        self.assertFalse(support.sufficient)

    def test_families_are_counted_only_where_the_transition_happened(self) -> None:
        """The family minimum is a breadth requirement on the *transition*.

        30 improved cases sit in 5 families here, alongside 40 unchanged cases
        spread over 40 more. Counting every family that appears reports 45 and
        seals the gate, while the estimate would still rest on 5 families -- the
        breadth §7.4 asks for would be supplied by cases the estimand excludes.
        The case count is sufficient on purpose, so the family term is the only
        thing that can refuse.
        """
        rows = [
            _row(f"i{f}_{c}", f"f{f}", 0, 1, 0.5) for f in range(5) for c in range(6)
        ]
        rows += [_row(f"u{i}", f"g{i}", 1, 1, 0.5) for i in range(40)]
        support = summarize_support(rows)
        self.assertGreaterEqual(support.state_improved_cases, MINIMUM_STATE_IMPROVED_CASES)
        self.assertEqual(support.state_improved_families, 5)
        self.assertFalse(support.sufficient)

    def test_insufficient_support_is_not_a_failed_bridge(self) -> None:
        """§7.4 is explicit: insufficient support "is not evidence that the state
        metric is invalid". The decision must be distinguishable from FAIL, or a
        state-only claim gets reported as a refuted bridge."""
        rows = [_row("c1", "f1", 0, 1, 0.9)]
        with self.assertRaises(BridgeInsufficientSupport):
            bridge_decision(rows, seed=24)


class FamilyMeanTest(unittest.TestCase):
    """§7.4 `I_f = mean(answer_gain | state-improved)`."""

    def test_i_f_averages_within_the_family_then_across(self) -> None:
        """Two families of unequal size: the family mean is the unit, so the
        larger family does not get more weight. Worked by hand -- f1 mean 0.2,
        f2 mean 0.8."""
        rows = [
            _row("c1", "f1", 0, 1, 0.1),
            _row("c2", "f1", 0, 1, 0.3),
            _row("c3", "f2", 0, 1, 0.8),
        ]
        means = dict(family_improved_means(rows))
        self.assertAlmostEqual(means["f1"], 0.2, places=9)
        self.assertAlmostEqual(means["f2"], 0.8, places=9)

    def test_unchanged_cases_are_excluded_from_i_f(self) -> None:
        """A family's `I_f` conditions on the improved stratum. Including an
        unchanged case would dilute the transition's own effect."""
        rows = [
            _row("c1", "f1", 0, 1, 1.0),
            _row("c2", "f1", 1, 1, 0.0),
        ]
        means = dict(family_improved_means(rows))
        self.assertAlmostEqual(means["f1"], 1.0, places=9)

    def test_a_family_with_no_improved_case_is_absent(self) -> None:
        """§7.4 says "for each family with state-improved cases". A family with
        none has no `I_f`, and emitting 0.0 would add a null family effect to the
        interval."""
        rows = [_row("c1", "f1", 1, 1, 0.5)]
        self.assertEqual(family_improved_means(rows), ())


class SpecificityContrastTest(unittest.TestCase):
    """§7.4 `S_f = I_f - U_f`, over families containing *both* strata."""

    def test_s_f_is_the_difference_of_the_two_stratum_means(self) -> None:
        rows = [
            _row("c1", "f1", 0, 1, 0.9),
            _row("c2", "f1", 1, 1, 0.2),
        ]
        contrast = dict(specificity_contrast(rows))
        self.assertAlmostEqual(contrast["f1"], 0.7, places=9)

    def test_a_family_with_only_one_stratum_is_excluded(self) -> None:
        """§7.4: "for families containing both strata". A family with no
        unchanged case has no `U_f` to subtract, and treating the missing one as
        zero would report `I_f` as if it were specific."""
        rows = [
            _row("c1", "f1", 0, 1, 0.9),
            _row("c2", "f2", 1, 1, 0.2),
        ]
        self.assertEqual(specificity_contrast(rows), ())

    def test_worsened_cases_are_in_neither_stratum_of_the_contrast(self) -> None:
        rows = [
            _row("c1", "f1", 0, 1, 1.0),
            _row("c2", "f1", 1, 1, 0.0),
            _row("c3", "f1", 1, 0, -5.0),
        ]
        contrast = dict(specificity_contrast(rows))
        self.assertAlmostEqual(contrast["f1"], 1.0, places=9)

    def test_a_uniform_gain_yields_a_zero_contrast(self) -> None:
        """The defect the contrast exists to catch: the candidate answers better
        everywhere, so the gain is not attributable to state convergence."""
        rows = [
            _row("c1", "f1", 0, 1, 0.5),
            _row("c2", "f1", 1, 1, 0.5),
        ]
        contrast = dict(specificity_contrast(rows))
        self.assertAlmostEqual(contrast["f1"], 0.0, places=9)


def _sufficient_rows(gain_improved, gain_unchanged=0.0, *, domain="d"):
    """30 improved cases over 10 families, each family also carrying one
    unchanged case so the specificity contrast is defined."""
    rows = []
    for family_index in range(10):
        family = f"f{family_index}"
        for case_index in range(3):
            rows.append(
                _row(f"i{family_index}_{case_index}", family, 0, 1, gain_improved, domain)
            )
        rows.append(_row(f"u{family_index}", family, 1, 1, gain_unchanged, domain))
    return rows


class BridgeDecisionTest(unittest.TestCase):
    """§7.4's primary test and the wording constraints around it."""

    def test_a_positive_specific_gain_passes(self) -> None:
        result = bridge_decision(_sufficient_rows(0.5), seed=24)
        self.assertEqual(result["decision"], "PASS")
        self.assertGreater(result["primary_lower_bound"], 0.0)

    def test_a_zero_gain_fails_the_primary_test(self) -> None:
        result = bridge_decision(_sufficient_rows(0.0), seed=24)
        self.assertEqual(result["decision"], "FAIL")

    def test_a_negative_gain_fails(self) -> None:
        result = bridge_decision(_sufficient_rows(-0.5), seed=24)
        self.assertEqual(result["decision"], "FAIL")

    def test_a_nonpositive_contrast_blocks_causal_wording_on_a_pass(self) -> None:
        """§7.4: "A nonpositive specificity contrast prevents causal or
        mediational wording even when the primary bridge passes." So the pass and
        the wording permission are two separate outputs."""
        result = bridge_decision(_sufficient_rows(0.5, gain_unchanged=0.5), seed=24)
        self.assertEqual(result["decision"], "PASS")
        self.assertFalse(result["causal_wording_permitted"])

    def test_a_positive_contrast_permits_causal_wording(self) -> None:
        result = bridge_decision(_sufficient_rows(0.5, gain_unchanged=0.0), seed=24)
        self.assertTrue(result["causal_wording_permitted"])

    def test_a_materially_negative_domain_fails_a_bridge_the_interval_would_pass(
        self,
    ) -> None:
        """§7.4's primary test is a conjunction, so the veto has to be the
        binding term in at least one test.

        The harmful domain is deliberately small: two families at -0.5 against
        ten at +0.9 leaves the pooled interval comfortably above zero, so
        `interval_pass` is asserted True and the FAIL can only come from the
        second term. A harmful domain large enough to drag the interval negative
        would fail on the interval alone and would not test the veto at all.
        """
        rows = _sufficient_rows(0.9, domain="good")
        rows += [_row(f"b{i}", f"bf{i}", 0, 1, -0.5, "bad") for i in range(2)]
        result = bridge_decision(rows, seed=24)
        self.assertTrue(result["interval_pass"], result["primary_lower_bound"])
        self.assertEqual(result["decision"], "FAIL")
        self.assertIn("bad", result["negative_direction_domains"])
        # The good domain carries both strata with a positive contrast, so the
        # contrast alone would permit causal wording. A FAIL must not.
        self.assertGreater(result["specificity_contrast_estimate"], 0.0)
        self.assertFalse(result["causal_wording_permitted"])

    def test_a_domain_exactly_at_the_threshold_is_material(self) -> None:
        """`MATERIAL_NEGATIVE_DIRECTION` is inclusive.

        One family with one case is the only way to land a float mean exactly on
        -0.1, and the boundary is the whole point: an exclusive comparison would
        let a domain sitting precisely at the registered materiality threshold
        through, and this constant is a late registration (see the module
        docstring) so its edge deserves a test rather than a convention.
        """
        rows = _sufficient_rows(0.9, domain="good")
        rows.append(_row("b0", "bf0", 0, 1, MATERIAL_NEGATIVE_DIRECTION, "bad"))
        result = bridge_decision(rows, seed=24)
        self.assertEqual(result["domain_directions"]["bad"], MATERIAL_NEGATIVE_DIRECTION)
        self.assertIn("bad", result["negative_direction_domains"])
        self.assertEqual(result["decision"], "FAIL")

    def test_a_domain_just_inside_the_threshold_is_not_material(self) -> None:
        """The other side of the same edge. Without this, a threshold set to
        -0.0 -- vetoing any domain that declines at all -- would pass every
        other test, because no other fixture has a mildly negative domain."""
        rows = _sufficient_rows(0.6, domain="good")
        rows += [_row(f"b{i}", f"bf{i}", 0, 1, -0.05, "mild") for i in range(2)]
        result = bridge_decision(rows, seed=24)
        self.assertEqual(result["negative_direction_domains"], [])
        self.assertEqual(result["decision"], "PASS")

    def test_the_domain_direction_averages_families_not_cases(self) -> None:
        """§4.4's resampling unit is the family, and the veto has to agree.

        The harmful domain has one family with a single bad case and one family
        with nine mildly good ones. By family the direction is
        (-0.9 + 0.05) / 2 = -0.425, material; by case it is
        (-0.9 + 9*0.05) / 10 = -0.045, not material. A case mean lets a family
        that happens to have more sibling cases outvote one that does not, which
        is the same clustering error the family-blocked interval exists to avoid.
        """
        rows = _sufficient_rows(0.9, domain="good")
        rows.append(_row("b0", "bf0", 0, 1, -0.9, "bad"))
        rows += [_row(f"b1_{i}", "bf1", 0, 1, 0.05, "bad") for i in range(9)]
        result = bridge_decision(rows, seed=24)
        self.assertAlmostEqual(result["domain_directions"]["bad"], -0.425)
        self.assertIn("bad", result["negative_direction_domains"])

    def test_the_domain_direction_reads_only_the_improved_stratum(self) -> None:
        """The estimand is conditioned on the transition, so the veto that
        guards it must condition the same way.

        The harmful domain's improved cases run at -0.5 while its unchanged
        cases run at +5.0. Averaging the strata together reports the domain as
        strongly positive and drops the veto -- the domain would look safe
        precisely because the candidate answers well where it changed nothing.
        """
        rows = _sufficient_rows(0.9, domain="good")
        rows += [_row(f"b{i}", f"bf{i}", 0, 1, -0.5, "bad") for i in range(2)]
        rows += [_row(f"bu{i}", f"bf{i + 2}", 1, 1, 5.0, "bad") for i in range(2)]
        result = bridge_decision(rows, seed=24)
        self.assertAlmostEqual(result["domain_directions"]["bad"], -0.5)
        self.assertEqual(result["decision"], "FAIL")

    def test_an_undefined_contrast_does_not_permit_causal_wording(self) -> None:
        """§7.4 blocks causal wording on a nonpositive contrast; an undefined one
        is not positive evidence either.

        No family here carries an unchanged case, so `S_f` has nothing to average
        and the specificity question was never asked. Treating "not measured" as
        "measured favourably" would let the strongest available wording rest on
        the absence of the check that constrains it.
        """
        rows = [
            _row(f"i{f}_{c}", f"f{f}", 0, 1, 0.5)
            for f in range(10)
            for c in range(3)
        ]
        result = bridge_decision(rows, seed=24)
        self.assertEqual(result["decision"], "PASS")
        self.assertIsNone(result["specificity_contrast_estimate"])
        self.assertFalse(result["causal_wording_permitted"])

    def test_the_primary_estimate_is_the_family_mean_not_the_bound(self) -> None:
        """Two numbers that a uniform fixture cannot tell apart.

        Every other decision fixture gives all families the same gain, where the
        bootstrap lower bound equals the mean and reporting either satisfies the
        assertion. Here family `f_i` runs at gain `0.1*i`, so the mean is 0.45
        and the one-sided bound is strictly below it -- an artifact that
        published the bound as the point estimate would understate the effect it
        reports.
        """
        rows = [
            _row(f"i{f}_{c}", f"f{f}", 0, 1, round(0.1 * f, 6))
            for f in range(10)
            for c in range(3)
        ]
        result = bridge_decision(rows, seed=24)
        self.assertAlmostEqual(result["primary_estimate"], 0.45)
        self.assertLess(result["primary_lower_bound"], result["primary_estimate"])

    def test_the_judge_score_is_recorded_but_not_the_target(self) -> None:
        """§7.2: secondary and never the target. A judge column that disagrees
        with the deterministic metric must not change the decision."""
        rows = [dict(row, judge_gain=-1.0) for row in _sufficient_rows(0.5)]
        result = bridge_decision(rows, seed=24)
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["primary_metric"], PRIMARY_METRIC)

    def test_the_decision_is_reproducible_under_a_fixed_seed(self) -> None:
        rows = _sufficient_rows(0.3)
        first = bridge_decision(rows, seed=24)
        second = bridge_decision(rows, seed=24)
        self.assertEqual(first["primary_lower_bound"], second["primary_lower_bound"])


if __name__ == "__main__":
    unittest.main()
