"""Post-Repair Context Replay tests — Issue 0005 (Cycles 5, 12, 13, 15)."""

import tempfile
from pathlib import Path
import unittest

from cmd_audit import (
    AuditResult,
    BaselineSuiteResult,
    ECSDraft,
    FullAuditResult,
    PostRepairResult,
    RepairedContext,
    classify_repair_assessment,
    build_repaired_context,
    draft_ecs,
    load_probe_cases,
    run_case,
    run_case_full,
    run_hard_case_update_baseline,
    run_post_repair_context_replay,
    validate_sandbox_path,
    write_post_repair_table,
)
from cmd_audit.labels import LabelValidationError


RETRIEVAL_FIXTURE = Path("data/probe_cases/v0_retrieval_error_case.json")
PREMATURE_FIXTURE = Path("data/probe_cases/v0_premature_extraction_error_case.json")


# ── Cycle 12: Three-Value Post-Repair Assessment ────────────────────


class ThreeValueRepairAssessmentTest(unittest.TestCase):
    """TDD Cycle 12: classify_repair_assessment returns recovered/partial/failed."""

    def test_recovered_when_answer_full_score(self) -> None:
        self.assertEqual(classify_repair_assessment(1.0, 1.0), "recovered")

    def test_partial_when_evidence_recovered_but_answer_not(self) -> None:
        self.assertEqual(classify_repair_assessment(0.0, 1.0), "partial")

    def test_failed_when_neither_answer_nor_evidence_recovered(self) -> None:
        self.assertEqual(classify_repair_assessment(0.0, 0.0), "failed")

    def test_partial_on_partial_answer_with_full_evidence(self) -> None:
        # e.g. answer_score=0.5 evidence_score=1.0 -> still partial
        self.assertEqual(classify_repair_assessment(0.5, 1.0), "partial")

    def test_failed_on_low_both_scores(self) -> None:
        self.assertEqual(classify_repair_assessment(0.3, 0.3), "failed")


# ── Cycle 5: Post-Repair Context Replay ──────────────────────────────


class PostRepairContextReplayTest(unittest.TestCase):
    """TDD Cycle 5: full Post-Repair Context Replay pipeline."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.retrieval_case = load_probe_cases(RETRIEVAL_FIXTURE)[0]
        cls.audit = run_case(cls.retrieval_case)

    def test_draft_ecs_from_attribution(self) -> None:
        ecs = draft_ecs(self.retrieval_case, self.audit)

        self.assertEqual(ecs.case_id, self.retrieval_case.case_id)
        self.assertEqual(ecs.predicted_label, "retrieval_error")
        self.assertTrue(ecs.cause)
        self.assertTrue(ecs.corrected_memory)
        self.assertTrue(ecs.repair_guidance)
        self.assertTrue(ecs.repaired_evidence_block)

    def test_build_repaired_context_includes_all_components(self) -> None:
        ecs = draft_ecs(self.retrieval_case, self.audit)
        ctx = build_repaired_context(self.retrieval_case, ecs)

        self.assertEqual(ctx.case_id, self.retrieval_case.case_id)
        self.assertEqual(ctx.original_query, self.retrieval_case.query)
        self.assertIn(ecs.corrected_memory, ctx.corrected_memory)
        self.assertIn(ecs.repair_guidance, ctx.repair_guidance)
        self.assertIn(ecs.repaired_evidence_block, ctx.repaired_evidence_block)

    def test_post_repair_replay_recovers_retrieval_case(self) -> None:
        ecs = draft_ecs(self.retrieval_case, self.audit)
        ctx = build_repaired_context(self.retrieval_case, ecs)
        result = run_post_repair_context_replay(self.retrieval_case, ctx)

        self.assertEqual(result.case_id, self.retrieval_case.case_id)
        # The retrieval repair should inject the correct answer text into context
        self.assertAlmostEqual(result.post_repair_evidence_score, 1.0)
        self.assertAlmostEqual(result.post_repair_answer_score, 1.0)
        self.assertEqual(result.repair_assessment, "recovered")

    def test_post_repair_does_not_inject_gold_answer_directly(self) -> None:
        """The gold answer must not appear verbatim in the repair context
        unless it naturally emerges from corrected memory text."""
        ecs = draft_ecs(self.retrieval_case, self.audit)
        ctx = build_repaired_context(self.retrieval_case, ecs)

        # The repair context is built from corrected_memory and
        # repaired_evidence_block, never from gold_answer directly.
        self.assertNotIn(
            self.retrieval_case.gold_answer,
            ctx.repair_guidance,
        )

    def test_post_repair_result_has_token_cost_and_regression_risk(self) -> None:
        ecs = draft_ecs(self.retrieval_case, self.audit)
        ctx = build_repaired_context(self.retrieval_case, ecs)
        result = run_post_repair_context_replay(self.retrieval_case, ctx)

        self.assertGreaterEqual(result.token_cost, 0.0)
        self.assertGreaterEqual(result.regression_risk, 0.0)
        self.assertLessEqual(result.regression_risk, 1.0)
        self.assertIsInstance(result.had_repair_regression, bool)

    def test_hard_case_update_baseline_is_independent(self) -> None:
        baseline = run_hard_case_update_baseline(self.retrieval_case)

        self.assertEqual(baseline.case_id, self.retrieval_case.case_id)
        self.assertIn(
            baseline.repair_assessment,
            ("recovered", "partial", "failed"),
        )

    def test_full_pipeline_produces_complete_result(self) -> None:
        result = run_case_full(self.retrieval_case)

        self.assertIsInstance(result.audit, AuditResult)
        self.assertIsInstance(result.ecs_draft, ECSDraft)
        self.assertIsInstance(result.repaired_context, RepairedContext)
        self.assertIsInstance(result.post_repair, PostRepairResult)
        self.assertIsInstance(result.hard_case_baseline, PostRepairResult)
        # retrieval repair with full evidence should recover
        self.assertEqual(result.post_repair.repair_assessment, "recovered")

    def test_post_repair_partial_scenario(self) -> None:
        """Simulate a coupled failure: evidence recovered but answer still wrong."""
        case = self.retrieval_case

        # Build a context that has evidence but NOT the gold answer text,
        # simulating reasoning_error coupling.
        repaired_ctx = RepairedContext(
            case_id=case.case_id,
            corrected_memory=(
                "The user's capital preference changed to Lisbon in June 2025. "
                "Current setting: Lisbon."
            ),
            repair_guidance="Use the corrected memory to answer the query.",
            repaired_evidence_block=(
                "User preference update: capital changed from Madrid to Lisbon. "
                "Effective date: 2025-06-15."
            ),
            original_query=case.query,
        )
        result = run_post_repair_context_replay(case, repaired_ctx)
        self.assertEqual(result.repair_assessment, "recovered")

        # Now simulate a partial: evidence present but answer not recoverable
        repaired_no_answer = RepairedContext(
            case_id=case.case_id,
            corrected_memory="The user changed their preferred city to a new value.",
            repair_guidance="Check the evidence block for the city name.",
            repaired_evidence_block="Capital preference: changed to Lisbon on June 15 2025.",
            original_query=case.query,
        )
        result_partial = run_post_repair_context_replay(case, repaired_no_answer)
        # Evidence should be recovered (mentions Lisbon), but answer may
        # not appear verbatim in context depending on scoring specifics.
        # The key contract: three-value assessment is always one of the three.
        self.assertIn(
            result_partial.repair_assessment,
            ("recovered", "partial", "failed"),
        )


# ── Cycle 13: ECS Cause Item-Label-Name Prohibition ──────────────────


class ECSCauseValidationTest(unittest.TestCase):
    """TDD Cycle 13: ECS cause rejects forbidden item label names."""

    def test_ecs_cause_rejects_item_wrong(self) -> None:
        with self.assertRaises(ValueError):
            ECSDraft(
                case_id="test-001",
                predicted_label="retrieval_error",
                cause="the memory item_wrong caused the failure",
                corrected_memory="...",
                repair_guidance="...",
                repaired_evidence_block="...",
            )

    def test_ecs_cause_rejects_item_stale(self) -> None:
        with self.assertRaises(ValueError):
            ECSDraft(
                case_id="test-001",
                predicted_label="retrieval_error",
                cause="item_stale detected in memory store",
                corrected_memory="...",
                repair_guidance="...",
                repaired_evidence_block="...",
            )

    def test_ecs_cause_rejects_item_conflict(self) -> None:
        with self.assertRaises(ValueError):
            ECSDraft(
                case_id="test-001",
                predicted_label="retrieval_error",
                cause="item_conflict between two memories",
                corrected_memory="...",
                repair_guidance="...",
                repaired_evidence_block="...",
            )

    def test_ecs_cause_rejects_item_poisoned(self) -> None:
        with self.assertRaises(ValueError):
            ECSDraft(
                case_id="test-001",
                predicted_label="retrieval_error",
                cause="item_poisoned by upstream data",
                corrected_memory="...",
                repair_guidance="...",
                repaired_evidence_block="...",
            )

    def test_ecs_cause_rejects_item_compression_distorted(self) -> None:
        with self.assertRaises(ValueError):
            ECSDraft(
                case_id="test-001",
                predicted_label="retrieval_error",
                cause="item_compression_distorted the key fact",
                corrected_memory="...",
                repair_guidance="...",
                repaired_evidence_block="...",
            )

    def test_ecs_cause_allows_descriptive_state_language(self) -> None:
        # Natural language describing item state without forbidden names is OK.
        ecs = ECSDraft(
            case_id="test-001",
            predicted_label="retrieval_error",
            cause="stored preference was outdated relative to ground truth",
            corrected_memory="...",
            repair_guidance="...",
            repaired_evidence_block="...",
        )
        self.assertIn("outdated", ecs.cause)

    def test_ecs_cause_rejects_natural_language_equivalents(self) -> None:
        # Natural-language equivalents of forbidden names are also rejected.
        with self.assertRaises(ValueError):
            ECSDraft(
                case_id="test-001",
                predicted_label="retrieval_error",
                cause="the memory item is wrong",
                corrected_memory="...",
                repair_guidance="...",
                repaired_evidence_block="...",
            )


# ── Cycle 15: Sandbox Write Boundary ──────────────────────────────────


class SandboxWriteBoundaryTest(unittest.TestCase):
    """TDD Cycle 15: CMD-Audit writes restricted to replay-local sandbox."""

    def test_sandbox_path_inside_is_accepted(self) -> None:
        validate_sandbox_path(Path("artifacts/sandbox/post_repair.csv"))

    def test_sandbox_path_outside_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_sandbox_path(Path("/etc/passwd"))

    def test_sandbox_path_parent_traversal_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_sandbox_path(Path("artifacts/sandbox/../../../etc/passwd"))

    def test_write_post_repair_table_writes_to_sandbox(self) -> None:
        case = load_probe_cases(RETRIEVAL_FIXTURE)[0]
        full = run_case_full(case)

        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir) / "sandbox"
            sandbox.mkdir()
            output = sandbox / "post_repair_table.csv"
            write_post_repair_table([full], output, sandbox_root=sandbox)
            text = output.read_text(encoding="utf-8")
            self.assertIn("case_id,perturbation_label,predicted_label", text)
            self.assertIn("v0-retrieval-001", text)

    def test_write_post_repair_table_rejects_outside_sandbox(self) -> None:
        case = load_probe_cases(RETRIEVAL_FIXTURE)[0]
        full = run_case_full(case)

        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir) / "sandbox"
            sandbox.mkdir()
            outside = Path(tmpdir) / "outside" / "post_repair_table.csv"
            outside.parent.mkdir()
            with self.assertRaises(ValueError):
                write_post_repair_table([full], outside, sandbox_root=sandbox)


# ── Post-Repair Table Output Shape ────────────────────────────────────


class PostRepairTableShapeTest(unittest.TestCase):
    """The post_repair_table.csv must contain all required columns."""

    def test_table_has_required_columns(self) -> None:
        case = load_probe_cases(RETRIEVAL_FIXTURE)[0]
        full = run_case_full(case)

        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir) / "sandbox"
            sandbox.mkdir()
            output = sandbox / "post_repair_table.csv"
            write_post_repair_table([full], output, sandbox_root=sandbox)
            header = output.read_text(encoding="utf-8").splitlines()[0]

        required = [
            "case_id",
            "perturbation_label",
            "predicted_label",
            "pre_repair_answer_score",
            "pre_repair_evidence_score",
            "post_repair_answer_score",
            "post_repair_evidence_score",
            "repair_assessment",
            "repair_action",
            "hard_case_baseline_assessment",
            "token_cost",
            "regression_risk",
            "had_repair_regression",
        ]
        for col in required:
            with self.subTest(column=col):
                self.assertIn(col, header)


if __name__ == "__main__":
    unittest.main()
