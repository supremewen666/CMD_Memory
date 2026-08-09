"""Pre-evolution gates for the semantic/actionability successor protocol."""

import unittest

from cmd_audit.eval.successor_instrument_gates import (
    ActionabilityObservation,
    GateThresholds,
    PredicateActivity,
    RelationObservation,
    ShortcutItem,
    evaluate_actionability_gate,
    evaluate_predicate_activity_gate,
    evaluate_relation_gate,
    audit_item_field_shortcuts,
)


def thresholds(**overrides) -> GateThresholds:
    values = {
        "min_relation_precision": 0.80,
        "min_relation_recall": 0.75,
        "max_permutation_false_positive_rate": 0.10,
        "min_canary_recall": 0.75,
        "max_relation_abstention_rate": 0.10,
        "relation_confidence_level": 0.95,
        "relation_bootstrap_iterations": 100,
        "relation_bootstrap_seed": 1,
        "min_relation_pairs": 1,
        "min_positive_pairs": 1,
        "min_negative_pairs": 1,
        "min_relation_families": 1,
        "min_target_precision": 0.90,
        "min_target_recall": 0.25,
        "min_ordering_coverage": 0.25,
        "min_destructive_coverage": 0.25,
        "max_unknown_rate": 1.0,
        "max_conflict_rate": 1.0,
        "actionability_confidence_level": 0.95,
        "actionability_bootstrap_iterations": 100,
        "actionability_bootstrap_seed": 2,
        "min_actionability_pairs": 1,
        "min_directional_pairs": 1,
        "min_actionability_families": 1,
        "min_predicate_fires": 3,
        "min_predicate_families": 2,
        "max_null_false_fire_rate": 0.0,
        "max_shortcut_alignment": 0.80,
        "max_shortcut_nmi": 0.80,
        "max_permutation_target_precision": 0.50,
        "max_shortcut_unique_ratio": 0.50,
    }
    values.update(overrides)
    return GateThresholds(**values)


class ThresholdContractTest(unittest.TestCase):
    def test_thresholds_are_explicit_and_bounded(self) -> None:
        self.assertEqual(thresholds().min_target_precision, 0.90)
        with self.assertRaises(ValueError):
            thresholds(min_relation_precision=1.1)
        with self.assertRaises(ValueError):
            thresholds(min_predicate_fires=0)


class RelationGateTest(unittest.TestCase):
    def test_relation_gate_checks_calibration_permutation_and_canaries(self) -> None:
        rows = (
            RelationObservation("f1", True, True, "calibration"),
            RelationObservation("f2", True, True, "calibration"),
            RelationObservation("f3", False, False, "calibration"),
            RelationObservation("p1", False, False, "permutation"),
            RelationObservation("p2", False, False, "permutation"),
            RelationObservation("c1", True, True, "canary"),
            RelationObservation("c2", True, True, "canary"),
        )
        result = evaluate_relation_gate(rows, thresholds=thresholds())
        self.assertTrue(result.passed)
        self.assertEqual(result.measurements["relation_precision"], 1.0)
        self.assertEqual(result.measurements["permutation_false_positive_rate"], 0.0)

    def test_style_shortcut_refuses_even_when_calibration_is_perfect(self) -> None:
        rows = (
            RelationObservation("f1", True, True, "calibration"),
            RelationObservation("f2", False, False, "calibration"),
            RelationObservation("p1", False, True, "permutation"),
            RelationObservation("p2", False, True, "permutation"),
            RelationObservation("c1", True, True, "canary"),
        )
        result = evaluate_relation_gate(rows, thresholds=thresholds())
        self.assertFalse(result.passed)
        self.assertIn("permutation_false_positive_rate", result.failures)

    def test_missing_lane_refuses_instead_of_vacuously_passing(self) -> None:
        result = evaluate_relation_gate(
            (RelationObservation("f1", True, True, "calibration"),),
            thresholds=thresholds(),
        )
        self.assertFalse(result.passed)
        self.assertIn("missing_permutation_lane", result.failures)
        self.assertIn("missing_canary_lane", result.failures)

    def test_abstention_and_registered_support_are_enforced(self) -> None:
        rows = (
            RelationObservation("f1", True, None, "calibration"),
            RelationObservation("f2", False, False, "calibration"),
            RelationObservation("p", False, False, "permutation"),
            RelationObservation("c", True, True, "canary"),
        )
        result = evaluate_relation_gate(
            rows,
            thresholds=thresholds(
                max_relation_abstention_rate=0.0,
                min_relation_pairs=3,
            ),
        )
        self.assertFalse(result.passed)
        self.assertIn("relation_abstention_rate", result.failures)
        self.assertIn("relation_min_pairs", result.failures)


class ActionabilityGateTest(unittest.TestCase):
    def test_pair_detection_does_not_count_as_target_identification(self) -> None:
        rows = (
            ActionabilityObservation("f1", "old-a", None, False),
            ActionabilityObservation("f2", "old-b", None, False),
        )
        result = evaluate_actionability_gate(rows, thresholds=thresholds())
        self.assertFalse(result.passed)
        self.assertEqual(result.measurements["target_precision"], 0.0)
        self.assertEqual(result.measurements["destructive_coverage"], 0.0)

    def test_wrong_destructive_target_fails_precision(self) -> None:
        rows = (
            ActionabilityObservation("f1", "old-a", "old-a", True),
            ActionabilityObservation("f2", "old-b", "new-b", True),
            ActionabilityObservation("f3", "old-c", "old-c", True),
            ActionabilityObservation("f4", "old-d", "old-d", True),
        )
        result = evaluate_actionability_gate(rows, thresholds=thresholds())
        self.assertFalse(result.passed)
        self.assertIn("target_precision", result.failures)

    def test_unknown_or_untrusted_direction_cannot_emit_a_target(self) -> None:
        result = evaluate_actionability_gate(
            (
                ActionabilityObservation(
                    "f1", "old", "old", False, "unknown", True, True
                ),
                ActionabilityObservation(
                    "f2", "old", "old", True, "conflicting", True, True
                ),
            ),
            thresholds=thresholds(min_destructive_coverage=0.0),
        )
        self.assertFalse(result.passed)
        self.assertIn("unsafe_target_emission", result.failures)
        self.assertIn("unsafe_destructive_authorization", result.failures)

    def test_target_recall_and_ordering_coverage_cannot_hide_behind_precision(self) -> None:
        rows = (
            ActionabilityObservation("f1", "old-1", "old-1", True, "resolved", True, True),
            ActionabilityObservation("f2", "old-2", None, False),
            ActionabilityObservation("f3", "old-3", None, False),
            ActionabilityObservation("f4", "old-4", None, False),
        )
        result = evaluate_actionability_gate(
            rows,
            thresholds=thresholds(
                min_target_recall=0.5,
                min_ordering_coverage=0.5,
                min_destructive_coverage=0.0,
            ),
        )
        self.assertEqual(result.measurements["target_precision"], 1.0)
        self.assertFalse(result.passed)
        self.assertIn("target_recall", result.failures)
        self.assertIn("ordering_coverage", result.failures)


class PredicateActivityGateTest(unittest.TestCase):
    def test_dead_predicate_refuses_before_headroom_is_read(self) -> None:
        result = evaluate_predicate_activity_gate(
            (PredicateActivity("superseded_item", 0, 0),),
            thresholds=thresholds(),
        )
        self.assertFalse(result.passed)
        self.assertIn("superseded_item:fires", result.failures)


class ShortcutAuditTest(unittest.TestCase):
    def test_safe_fields_need_explicit_passing_permutation_evidence(self) -> None:
        rows = tuple(
            ShortcutItem(
                case_id=f"c{index // 2}",
                item_id=f"i{index}",
                is_target=index % 2 == 0,
                fields={"safe": "same"},
                permutation_predicted_target=True,
            )
            for index in range(4)
        )
        result = audit_item_field_shortcuts(rows, thresholds=thresholds())
        self.assertTrue(result.passed)
        self.assertEqual(result.permutation_target_precision, 0.5)

    def test_bijective_store_marker_is_refused(self) -> None:
        rows = tuple(
            ShortcutItem(
                case_id=f"c{case_index}",
                item_id=item_id,
                is_target=item_id == "old",
                fields={"store": store, "rank": rank},
            )
            for case_index in range(4)
            for item_id, store, rank in (
                ("old", "2026-01-01", 0),
                ("new", "2026-02-01", 1),
                ("hay", "haystack", 2),
            )
        )
        result = audit_item_field_shortcuts(rows, thresholds=thresholds())
        self.assertFalse(result.passed)
        self.assertIn("store", result.flagged_fields)

    def test_high_cardinality_field_is_unresolved_and_cannot_silently_pass(self) -> None:
        rows = tuple(
            ShortcutItem(
                case_id=f"c{index}",
                item_id=f"i{index}",
                is_target=index % 2 == 0,
                fields={"opaque": f"unique-{index}"},
            )
            for index in range(10)
        )
        result = audit_item_field_shortcuts(rows, thresholds=thresholds())
        self.assertFalse(result.passed)
        self.assertIn("opaque", result.high_cardinality_fields)


if __name__ == "__main__":
    unittest.main()
