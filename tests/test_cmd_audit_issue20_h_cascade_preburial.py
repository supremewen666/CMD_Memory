"""V2 Cascade Pre-Burial tests — Issue 0020-H."""

from pathlib import Path
import unittest

from cmd_audit import (
    ECSDraft,
    build_repaired_context,
    draft_ecs,
    load_probe_cases,
    run_case_v1,
)


RETRIEVAL_FIXTURE = Path("data/probe_cases/v0_retrieval_error_case.json")


class CascadeCandidatesFieldTest(unittest.TestCase):
    """AC: ECSDraft has cascade_candidates field, V1 always empty."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.case = load_probe_cases(RETRIEVAL_FIXTURE)[0]
        cls.audit = run_case_v1(cls.case)

    def test_ecs_draft_has_cascade_candidates_field(self) -> None:
        ecs = draft_ecs(self.case, self.audit)
        self.assertTrue(hasattr(ecs, "cascade_candidates"))

    def test_cascade_candidates_defaults_to_empty_tuple(self) -> None:
        ecs = draft_ecs(self.case, self.audit)
        self.assertEqual(ecs.cascade_candidates, ())

    def test_cascade_candidates_is_tuple_of_strings(self) -> None:
        ecs = draft_ecs(self.case, self.audit)
        self.assertIsInstance(ecs.cascade_candidates, tuple)

    def test_explicit_cascade_candidates_accepted(self) -> None:
        ecs = draft_ecs(self.case, self.audit)
        ecs_with_candidates = ECSDraft(
            case_id=ecs.case_id,
            predicted_label=ecs.predicted_label,
            cause=ecs.cause,
            corrected_memory=ecs.corrected_memory,
            repair_guidance=ecs.repair_guidance,
            repaired_evidence_block=ecs.repaired_evidence_block,
            cascade_candidates=("mem_001", "mem_002"),
        )
        self.assertEqual(ecs_with_candidates.cascade_candidates, ("mem_001", "mem_002"))

    def test_repaired_context_ignores_cascade_candidates(self) -> None:
        ecs = draft_ecs(self.case, self.audit)
        ctx = build_repaired_context(self.case, ecs)
        self.assertFalse(hasattr(ctx, "cascade_candidates"))
        self.assertEqual(ctx.case_id, self.case.case_id)

    def test_all_v1_labels_produce_empty_cascade_candidates(self) -> None:
        ecs = draft_ecs(self.case, self.audit)
        self.assertEqual(
            ecs.cascade_candidates,
            (),
            f"Label {ecs.predicted_label!r} should produce empty cascade_candidates in V1",
        )


class ECSDraftImmutabilityTest(unittest.TestCase):
    """AC: ECSDraft remains frozen; cascade_candidates participates."""

    def test_ecs_draft_is_frozen(self) -> None:
        ecs = ECSDraft(
            case_id="test_001",
            predicted_label="retrieval_error",
            cause="test cause",
            corrected_memory="test memory",
            repair_guidance="test guidance",
            repaired_evidence_block="test evidence",
            cascade_candidates=("mem_001",),
        )
        with self.assertRaises(AttributeError):
            ecs.cascade_candidates = ("mem_002",)


if __name__ == "__main__":
    unittest.main()
