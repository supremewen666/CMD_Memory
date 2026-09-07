"""Neutral probe suite and behavioral identity (BUILD SPEC §8.3, §14.3).

The suite is a preregistered artifact, so these tests assert its frozen
properties -- exact count, coverage of every primitive, and absence of any
copied case literal -- as well as the fingerprint's behavioral (not syntactic)
discrimination.
"""

import unittest

from cmd_audit.counterfactual.behavior_fingerprint import (
    NEUTRAL_PROBE_COUNT,
    PROBE_SUITE_VERSION,
    CoverageGap,
    NeutralProbe,
    behavior_fingerprint,
    coverage_matrix,
    deduplicate_by_behavior,
    neutral_probe_suite,
    probe_suite_sha256,
    verify_coverage,
)
from cmd_audit.counterfactual.program_ir import (
    Action,
    ActionKind,
    If,
    Predicate,
    PredicateKind,
    Sequence,
)
from cmd_audit.eval.state_intent import TEMPLATE_HINT_MARKERS

CONTRADICTS = Predicate(kind=PredicateKind.CONTRADICTS)
RELEVANT = Predicate(kind=PredicateKind.QUERY_RELEVANT)
MISSING = Predicate(kind=PredicateKind.EVIDENCE_MISSING)


class SuiteShapeTest(unittest.TestCase):
    """§8.3 the suite contains exactly 64 deterministic synthetic microcases."""

    def test_suite_has_exactly_sixty_four_probes(self) -> None:
        self.assertEqual(NEUTRAL_PROBE_COUNT, 64)
        self.assertEqual(len(neutral_probe_suite()), 64)

    def test_probe_ids_are_unique_and_ordered(self) -> None:
        suite = neutral_probe_suite()
        ids = [probe.probe_id for probe in suite]
        self.assertEqual(len(set(ids)), 64)
        self.assertEqual(ids, sorted(ids))

    def test_suite_is_byte_identical_across_calls(self) -> None:
        self.assertEqual(probe_suite_sha256(), probe_suite_sha256())

    def test_digest_changes_when_an_item_moves_between_recall_and_pool(self) -> None:
        """Pool membership decides what every retrieval predicate sees, so a
        digest blind to it would let the suite change under a frozen hash."""
        from dataclasses import replace

        from cmd_audit.counterfactual.behavior_fingerprint import suite_sha256

        suite = neutral_probe_suite()
        probe = suite[0]
        flipped = replace(
            probe,
            case=replace(
                probe.case,
                items=(replace(probe.case.items[0], retrieved=False),)
                + probe.case.items[1:],
            ),
        )
        self.assertNotEqual(
            suite_sha256(suite), suite_sha256((flipped,) + suite[1:])
        )

    def test_every_probe_is_a_neutral_probe_with_a_runtime_case(self) -> None:
        for probe in neutral_probe_suite():
            with self.subTest(probe=probe.probe_id):
                self.assertIsInstance(probe, NeutralProbe)
                self.assertTrue(probe.case.items or probe.case.query)
                self.assertGreater(probe.case.token_budget, 0)


class NoCopiedLiteralTest(unittest.TestCase):
    """§8.3 no copied case text, phrases, IDs, or injector template literals."""

    def test_no_injector_template_marker_appears_anywhere(self) -> None:
        for probe in neutral_probe_suite():
            blob = " ".join(
                [probe.case.query]
                + [item.text for item in probe.case.items]
                + [item.item_id for item in probe.case.items]
                + [event.text for event in probe.case.raw_events]
            )
            for marker in TEMPLATE_HINT_MARKERS:
                with self.subTest(probe=probe.probe_id, marker=marker):
                    self.assertNotIn(marker, blob)

    def test_item_ids_follow_the_synthetic_probe_convention(self) -> None:
        """Probe IDs are positional, so no dataset ID convention can leak in."""
        for probe in neutral_probe_suite():
            for item in probe.case.items:
                with self.subTest(probe=probe.probe_id, item=item.item_id):
                    self.assertRegex(item.item_id, r"^p\d+$")

    def test_probe_vocabulary_is_a_closed_synthetic_word_list(self) -> None:
        """Every token comes from the registered neutral vocabulary."""
        from cmd_audit.counterfactual.behavior_fingerprint import PROBE_VOCABULARY

        for probe in neutral_probe_suite():
            tokens = set()
            for text in [probe.case.query] + [i.text for i in probe.case.items]:
                tokens |= {token.casefold() for token in text.split()}
            unexpected = tokens - set(PROBE_VOCABULARY)
            with self.subTest(probe=probe.probe_id):
                self.assertEqual(unexpected, set())


class CoverageTest(unittest.TestCase):
    """§8.3 per-primitive coverage matrix, verified rather than asserted."""

    def test_every_predicate_has_a_true_and_a_false_probe(self) -> None:
        matrix = coverage_matrix()
        for kind in PredicateKind:
            with self.subTest(kind=kind.value):
                self.assertGreaterEqual(matrix.predicate_true[kind.value], 1)
                self.assertGreaterEqual(matrix.predicate_false[kind.value], 1)

    def test_every_action_has_an_applied_and_a_noop_probe(self) -> None:
        matrix = coverage_matrix()
        for kind in ActionKind:
            with self.subTest(kind=kind.value):
                self.assertGreaterEqual(matrix.action_applied[kind.value], 1)
                self.assertGreaterEqual(matrix.action_noop[kind.value], 1)

    def test_every_registered_threshold_value_is_exercised(self) -> None:
        from cmd_audit.counterfactual.program_ir import (
            AGE_GAP_THRESHOLDS,
            SIMILARITY_THRESHOLDS,
        )

        matrix = coverage_matrix()
        for threshold in SIMILARITY_THRESHOLDS:
            with self.subTest(similarity=threshold):
                self.assertGreaterEqual(
                    matrix.threshold_values[("similarity_above", threshold)], 1
                )
        for threshold in AGE_GAP_THRESHOLDS:
            with self.subTest(age_gap=threshold):
                self.assertGreaterEqual(
                    matrix.threshold_values[("age_gap_above", threshold)], 1
                )

    def test_composition_ordering_and_boundary_families_are_present(self) -> None:
        matrix = coverage_matrix()
        for family in (
            "composition",
            "ordering",
            "token_boundary",
            "action_count_boundary",
            "logical_cost_boundary",
            "null_preservation",
            "fail_closed",
        ):
            with self.subTest(family=family):
                self.assertGreaterEqual(matrix.families[family], 1)

    def test_verify_coverage_passes_on_the_frozen_suite(self) -> None:
        self.assertEqual(verify_coverage(), ())

    def test_verify_coverage_reports_a_gap_on_a_truncated_suite(self) -> None:
        gaps = verify_coverage(neutral_probe_suite()[:4])
        self.assertTrue(gaps)
        self.assertIsInstance(gaps[0], CoverageGap)


class FingerprintTest(unittest.TestCase):
    """§8.3 syntactic novelty without behavioral novelty is not variation."""

    def test_fingerprint_is_deterministic(self) -> None:
        program = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        self.assertEqual(behavior_fingerprint(program), behavior_fingerprint(program))

    def test_commutative_reordering_shares_a_fingerprint(self) -> None:
        left = If(
            predicate=Predicate(
                kind=PredicateKind.AND, operands=(RELEVANT, CONTRADICTS)
            ),
            action=Action(ActionKind.DEMOTE),
        )
        right = If(
            predicate=Predicate(
                kind=PredicateKind.AND, operands=(CONTRADICTS, RELEVANT)
            ),
            action=Action(ActionKind.DEMOTE),
        )
        self.assertEqual(behavior_fingerprint(left), behavior_fingerprint(right))

    def test_semantic_noop_variants_share_a_fingerprint(self) -> None:
        """A rule appended after an equivalent one changes nothing observable."""
        rule = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        padded = Sequence((rule, If(predicate=RELEVANT, action=Action(ActionKind.KEEP))))
        self.assertEqual(behavior_fingerprint(rule), behavior_fingerprint(padded))

    def test_different_action_gives_a_different_fingerprint(self) -> None:
        demote = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        suppress = If(predicate=CONTRADICTS, action=Action(ActionKind.SUPPRESS))
        self.assertNotEqual(
            behavior_fingerprint(demote), behavior_fingerprint(suppress)
        )

    def test_different_predicate_gives_a_different_fingerprint(self) -> None:
        by_conflict = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        by_relevance = If(predicate=RELEVANT, action=Action(ActionKind.DEMOTE))
        self.assertNotEqual(
            behavior_fingerprint(by_conflict), behavior_fingerprint(by_relevance)
        )

    def test_threshold_change_that_alters_matching_is_visible(self) -> None:
        loose = If(
            predicate=Predicate(kind=PredicateKind.SIMILARITY_ABOVE, threshold=0.25),
            action=Action(ActionKind.DEMOTE),
        )
        tight = If(
            predicate=Predicate(kind=PredicateKind.SIMILARITY_ABOVE, threshold=0.75),
            action=Action(ActionKind.DEMOTE),
        )
        self.assertNotEqual(behavior_fingerprint(loose), behavior_fingerprint(tight))

    def test_null_program_has_its_own_stable_fingerprint(self) -> None:
        self.assertEqual(len(behavior_fingerprint(Sequence(()))), 64)

    def test_fingerprint_is_bound_to_the_suite_it_was_measured_on(self) -> None:
        """§8.3 forbids changing the suite once a candidate is fingerprinted.
        Binding the digest in makes a violation a universal mismatch rather
        than a silent comparison against different evidence."""
        program = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        truncated = neutral_probe_suite()[:8]
        self.assertNotEqual(
            behavior_fingerprint(program), behavior_fingerprint(program, truncated)
        )

    def test_identical_observations_on_a_changed_suite_differ(self) -> None:
        """The load-bearing half of suite binding.

        Two suites that produce byte-identical observations must still give
        different fingerprints if the suites themselves differ -- otherwise the
        digest contributes nothing and an edited suite compares clean.
        """
        from dataclasses import replace

        program = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        suite = neutral_probe_suite()[:4]
        # `note` is documentation: no predicate or action reads it, so every
        # observation is unchanged while the suite content is not.
        edited = (replace(suite[0], note=suite[0].note + " (edited)"),) + suite[1:]
        self.assertNotEqual(
            behavior_fingerprint(program, suite),
            behavior_fingerprint(program, edited),
        )

    def test_a_program_that_fails_a_bound_still_fingerprints(self) -> None:
        """Fail-closed is behavior. A program that busts a bound on some probes
        must receive a fingerprint recording that, not raise out of the suite."""
        greedy = Sequence(
            tuple(
                If(predicate=MISSING, action=Action(ActionKind.RETRIEVE_FILL))
                for _ in range(3)
            )
        )
        self.assertEqual(len(behavior_fingerprint(greedy)), 64)


class DeduplicationTest(unittest.TestCase):
    def test_behaviorally_identical_programs_collapse_to_one(self) -> None:
        rule = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        padded = Sequence((rule, If(predicate=RELEVANT, action=Action(ActionKind.KEEP))))
        kept = deduplicate_by_behavior([rule, padded])
        self.assertEqual(len(kept), 1)

    def test_behaviorally_distinct_programs_are_both_kept(self) -> None:
        demote = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        suppress = If(predicate=CONTRADICTS, action=Action(ActionKind.SUPPRESS))
        self.assertEqual(len(deduplicate_by_behavior([demote, suppress])), 2)

    def test_first_occurrence_wins_so_the_result_is_order_stable(self) -> None:
        rule = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        padded = Sequence((rule, If(predicate=RELEVANT, action=Action(ActionKind.KEEP))))
        self.assertEqual(deduplicate_by_behavior([rule, padded])[0], rule)
        self.assertEqual(deduplicate_by_behavior([padded, rule])[0], padded)


class ManifestTest(unittest.TestCase):
    """§8.3 the suite manifest is frozen before E0b/E3."""

    def test_manifest_records_version_count_and_digest(self) -> None:
        from cmd_audit.counterfactual.behavior_fingerprint import probe_manifest

        manifest = probe_manifest()
        self.assertEqual(manifest["probe_suite_version"], PROBE_SUITE_VERSION)
        self.assertEqual(manifest["probe_count"], 64)
        self.assertEqual(manifest["suite_sha256"], probe_suite_sha256())
        self.assertIn("coverage", manifest)
        self.assertIn("construction_code_sha256", manifest)

    def test_manifest_is_json_serializable_without_nan(self) -> None:
        import json

        from cmd_audit.counterfactual.behavior_fingerprint import probe_manifest

        json.dumps(probe_manifest(), allow_nan=False, sort_keys=True)


if __name__ == "__main__":
    unittest.main()
