"""ECS Iterative Repair tests — Issue 0020-G."""

from pathlib import Path
import unittest

from cmd_audit import (
    ECSDraft,
    draft_ecs,
    draft_ecs_for_label,
    load_probe_cases,
    run_case_v1,
    PIPELINE_LABEL_ORDER,
)
from cmd_audit.core.labels import LabelValidationError


RETRIEVAL_FIXTURE = Path("data/probe_cases/v0_retrieval_error_case.json")
WRITE_FIXTURE = Path("data/probe_cases/v0_issue3_cases.json")


class DraftECSForLabelTest(unittest.TestCase):
    """AC: draft_ecs_for_label creates ECS for any valid label."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.case = load_probe_cases(RETRIEVAL_FIXTURE)[0]
        cls.audit = run_case_v1(cls.case)

    def test_draft_for_predicted_label(self) -> None:
        label = self.audit.attribution.predicted_label
        ecs = draft_ecs_for_label(self.case, self.audit, label)
        self.assertIsInstance(ecs, ECSDraft)
        self.assertEqual(ecs.predicted_label, label)
        self.assertTrue(ecs.cause)
        self.assertTrue(ecs.corrected_memory)

    def test_draft_returns_same_as_draft_ecs_for_top_label(self) -> None:
        label = self.audit.attribution.predicted_label
        ecs_direct = draft_ecs(self.case, self.audit)
        ecs_for_label = draft_ecs_for_label(self.case, self.audit, label)
        self.assertEqual(ecs_direct.predicted_label, ecs_for_label.predicted_label)
        self.assertEqual(ecs_direct.cause, ecs_for_label.cause)
        self.assertEqual(ecs_direct.corrected_memory, ecs_for_label.corrected_memory)
        self.assertEqual(ecs_direct.repair_guidance, ecs_for_label.repair_guidance)

    def test_draft_for_each_v1_label(self) -> None:
        """Every V1 label can be drafted (some may fallback to minimal replay)."""
        for label in PIPELINE_LABEL_ORDER:
            with self.subTest(label=label):
                ecs = draft_ecs_for_label(self.case, self.audit, label)
                self.assertIsInstance(ecs, ECSDraft)
                self.assertEqual(ecs.predicted_label, label)
                self.assertTrue(ecs.cause)
                self.assertTrue(ecs.repair_guidance)

    def test_rejects_invalid_label(self) -> None:
        with self.assertRaises(LabelValidationError):
            draft_ecs_for_label(self.case, self.audit, "invalid_label")

    def test_rejects_item_label(self) -> None:
        with self.assertRaises(LabelValidationError):
            draft_ecs_for_label(self.case, self.audit, "item_wrong")

    def test_ecs_has_evidence_block(self) -> None:
        label = self.audit.attribution.predicted_label
        ecs = draft_ecs_for_label(self.case, self.audit, label)
        self.assertTrue(ecs.repaired_evidence_block)

    def test_ecs_has_empty_cascade_candidates(self) -> None:
        label = self.audit.attribution.predicted_label
        ecs = draft_ecs_for_label(self.case, self.audit, label)
        self.assertEqual(ecs.cascade_candidates, ())


class DraftECSForLabelWithCloseDeltasTest(unittest.TestCase):
    """AC: draft_ecs_for_label works with close_deltas labels."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.case = load_probe_cases(WRITE_FIXTURE)[0]
        cls.audit = run_case_v1(cls.case)

    def test_close_deltas_labels_produce_valid_ecs(self) -> None:
        for label, delta in self.audit.attribution.close_deltas:
            with self.subTest(label=label, delta=delta):
                ecs = draft_ecs_for_label(self.case, self.audit, label)
                self.assertIsInstance(ecs, ECSDraft)
                self.assertEqual(ecs.predicted_label, label)


class DraftECSWithoutAuditResultTest(unittest.TestCase):
    """AC: draft_ecs_for_label works with None audit_result (fallback)."""

    def test_fallback_with_none_audit(self) -> None:
        case = load_probe_cases(RETRIEVAL_FIXTURE)[0]
        ecs = draft_ecs_for_label(case, None, "retrieval_error")
        self.assertIsInstance(ecs, ECSDraft)
        self.assertEqual(ecs.predicted_label, "retrieval_error")


if __name__ == "__main__":
    unittest.main()
