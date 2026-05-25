"""RepairExecutor + RepairOrchestrator tests — Issue 0020-A."""

from pathlib import Path
import unittest

from cmd_audit import (
    RepairAction,
    RepairExecutor,
    RepairExecutorResult,
    RepairOrchestrator,
    RepairOrchestratorResult,
    FailureMemoryRecord,
    FailureMemoryStoreV1,
    load_probe_cases,
    run_case_v1,
)
from cmd_audit.adapters.base import Mem0Trace
from cmd_audit.adapters.mem0_adapter import Mem0Adapter


RETRIEVAL_FIXTURE = Path("data/probe_cases/v0_retrieval_error_case.json")


# ── RepairExecutor Tests ────────────────────────────────────────────────


class RepairExecutorBasicTest(unittest.TestCase):
    """AC: RepairExecutor runs single repair from ECS draft."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.case = load_probe_cases(RETRIEVAL_FIXTURE)[0]
        cls.audit = run_case_v1(cls.case)
        cls.trace = Mem0Trace(
            case_id=cls.case.case_id,
            add_inputs=["original"],
            search_query=cls.case.query,
            search_results=tuple(cls.case.extracted_memory),
            store_checksum="abc123",
        )
        cls.adapter = Mem0Adapter(
            cls.trace,
            gold_evidence=cls.case.gold_evidence,
            extracted_memory=cls.case.extracted_memory,
            raw_events=cls.case.raw_events,
        )

    def test_executor_returns_result(self) -> None:
        from cmd_audit import draft_ecs
        ecs = draft_ecs(self.case, self.audit)
        executor = RepairExecutor()
        result = executor.run(
            ecs_draft=ecs,
            adapter=self.adapter,
            case=self.case,
        )
        self.assertIsInstance(result, RepairExecutorResult)
        self.assertIn(result.assessment, ("recovered", "partial", "failed"))
        self.assertEqual(result.label, "retrieval_error")

    def test_result_has_scores(self) -> None:
        from cmd_audit import draft_ecs
        ecs = draft_ecs(self.case, self.audit)
        executor = RepairExecutor()
        result = executor.run(
            ecs_draft=ecs,
            adapter=self.adapter,
            case=self.case,
        )
        self.assertGreaterEqual(result.post_repair_answer_score, 0.0)
        self.assertLessEqual(result.post_repair_answer_score, 1.0)
        self.assertGreaterEqual(result.post_repair_evidence_score, 0.0)
        self.assertLessEqual(result.post_repair_evidence_score, 1.0)

    def test_result_has_repair_context(self) -> None:
        from cmd_audit import draft_ecs
        ecs = draft_ecs(self.case, self.audit)
        executor = RepairExecutor()
        result = executor.run(
            ecs_draft=ecs,
            adapter=self.adapter,
            case=self.case,
        )
        self.assertTrue(result.repair_context)
        self.assertIn("retrieval_error", result.repair_context)

    def test_applied_action_is_repair_action(self) -> None:
        from cmd_audit import draft_ecs
        ecs = draft_ecs(self.case, self.audit)
        executor = RepairExecutor()
        result = executor.run(
            ecs_draft=ecs,
            adapter=self.adapter,
            case=self.case,
        )
        self.assertIsInstance(result.applied_action, RepairAction)
        self.assertEqual(result.applied_action.label, "retrieval_error")

    def test_executor_with_fm_context(self) -> None:
        from cmd_audit import draft_ecs
        ecs = draft_ecs(self.case, self.audit)
        executor = RepairExecutor()
        result = executor.run(
            ecs_draft=ecs,
            adapter=self.adapter,
            case=self.case,
            fm_context="[Diagnostic] past error about retrieval",
        )
        self.assertIn("Diagnostic", result.repair_context)


class RepairExecutorActionTypeTest(unittest.TestCase):
    """AC: Action type selection respects adapter.supported_actions."""

    def test_select_action_type_maps_labels(self) -> None:
        self.assertEqual(
            RepairExecutor._select_action_type("write_error", ("append", "replace")),
            "append",
        )
        self.assertEqual(
            RepairExecutor._select_action_type("retrieval_error", ("update_routing", "append")),
            "update_routing",
        )
        self.assertEqual(
            RepairExecutor._select_action_type("compression_error", ("replace", "append")),
            "replace",
        )

    def test_select_action_type_fallback(self) -> None:
        # When preferred action not supported, fallback to first supported
        self.assertEqual(
            RepairExecutor._select_action_type("write_error", ("replace",)),
            "replace",
        )

    def test_select_action_type_empty_fallback(self) -> None:
        # Empty supported_actions falls back to append
        self.assertEqual(
            RepairExecutor._select_action_type("write_error", ()),
            "append",
        )

    def test_unsupported_action_sets_skip_reason(self) -> None:
        from cmd_audit import draft_ecs

        class NoActionAdapter:
            supported_actions = ()

            def apply_repair(self, action):
                from cmd_audit import UnsupportedActionError

                raise UnsupportedActionError("no actions supported")

        case = load_probe_cases(RETRIEVAL_FIXTURE)[0]
        audit = run_case_v1(case)
        ecs = draft_ecs(case, audit)
        result = RepairExecutor().run(
            ecs_draft=ecs,
            adapter=NoActionAdapter(),
            case=case,
        )
        self.assertIsNone(result.applied_action)
        self.assertEqual(result.action_skipped_reason, "no actions supported")


# ── RepairOrchestrator Tests ────────────────────────────────────────────


class RepairOrchestratorBasicTest(unittest.TestCase):
    """AC: Orchestrator walks close_deltas, stops at recovered."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.case = load_probe_cases(RETRIEVAL_FIXTURE)[0]
        cls.audit = run_case_v1(cls.case)
        cls.trace = Mem0Trace(
            case_id=cls.case.case_id,
            add_inputs=["original"],
            search_query=cls.case.query,
            search_results=tuple(cls.case.extracted_memory),
            store_checksum="abc123",
        )
        cls.adapter = Mem0Adapter(
            cls.trace,
            gold_evidence=cls.case.gold_evidence,
            extracted_memory=cls.case.extracted_memory,
            raw_events=cls.case.raw_events,
        )

    def test_orchestrator_returns_result(self) -> None:
        orchestrator = RepairOrchestrator()
        result = orchestrator.run(
            attribution=self.audit.attribution,
            case=self.case,
            adapter=self.adapter,
        )
        self.assertIsInstance(result, RepairOrchestratorResult)
        self.assertEqual(result.case_id, self.case.case_id)
        self.assertIn(result.final_assessment, ("recovered", "partial", "failed"))

    def test_orchestrator_tries_predicted_label_first(self) -> None:
        orchestrator = RepairOrchestrator()
        result = orchestrator.run(
            attribution=self.audit.attribution,
            case=self.case,
            adapter=self.adapter,
        )
        self.assertTrue(len(result.labels_tried) >= 1)
        self.assertEqual(result.labels_tried[0], self.audit.attribution.predicted_label)

    def test_orchestrator_tracks_attempts(self) -> None:
        orchestrator = RepairOrchestrator()
        result = orchestrator.run(
            attribution=self.audit.attribution,
            case=self.case,
            adapter=self.adapter,
        )
        self.assertEqual(len(result.attempts), len(result.labels_tried))
        for attempt in result.attempts:
            self.assertIsInstance(attempt, RepairExecutorResult)

    def test_recovered_stops_iteration(self) -> None:
        orchestrator = RepairOrchestrator()
        result = orchestrator.run(
            attribution=self.audit.attribution,
            case=self.case,
            adapter=self.adapter,
        )
        if result.final_assessment == "recovered":
            self.assertTrue(result.recovered)
            self.assertFalse(result.exhausted)

    def test_exhausted_when_no_recovery(self) -> None:
        # Create a case where no repair will recover
        # This is hard to guarantee, so we test structure instead
        orchestrator = RepairOrchestrator()
        result = orchestrator.run(
            attribution=self.audit.attribution,
            case=self.case,
            adapter=self.adapter,
        )
        if result.final_assessment != "recovered":
            self.assertTrue(result.exhausted)
            self.assertFalse(result.recovered)

    def test_orchestrator_with_threshold(self) -> None:
        orchestrator = RepairOrchestrator()
        result = orchestrator.run(
            attribution=self.audit.attribution,
            case=self.case,
            adapter=self.adapter,
            close_deltas_threshold=0.1,
        )
        self.assertIsInstance(result, RepairOrchestratorResult)


class RepairOrchestratorEdgeCaseTest(unittest.TestCase):
    """AC: Orchestrator handles edge cases."""

    def test_empty_close_deltas(self) -> None:
        from cmd_audit.attribution import AttributionResult
        attr = AttributionResult(
            predicted_label="retrieval_error",
            top_replay="oracle_retrieval",
            recovery_gain=0.5,
            top2_labels=(),
            is_ambiguous=False,
            close_deltas=(),
        )
        case = load_probe_cases(RETRIEVAL_FIXTURE)[0]
        trace = Mem0Trace(
            case_id=case.case_id,
            add_inputs=["original"],
            search_query=case.query,
            search_results=tuple(case.extracted_memory),
            store_checksum="abc123",
        )
        adapter = Mem0Adapter(
            trace,
            gold_evidence=case.gold_evidence,
            extracted_memory=case.extracted_memory,
            raw_events=case.raw_events,
        )
        orchestrator = RepairOrchestrator()
        result = orchestrator.run(
            attribution=attr,
            case=case,
            adapter=adapter,
        )
        self.assertFalse(result.exhausted)
        self.assertFalse(result.recovered)
        self.assertEqual(result.labels_tried, ())
        self.assertEqual(result.skipped_reason, "v0_attribution_no_close_deltas")

    def test_invalid_close_delta_is_reported_not_misaligned(self) -> None:
        from cmd_audit.attribution import AttributionResult

        class FailingExecutor:
            def run(self, *, ecs_draft, adapter, case, fm_context=""):
                return RepairExecutorResult(
                    assessment="failed",
                    post_repair_answer_score=0.0,
                    post_repair_evidence_score=0.0,
                    applied_action=None,
                    repair_context=fm_context,
                    label=ecs_draft.predicted_label,
                )

        attr = AttributionResult(
            predicted_label="retrieval_error",
            top_replay="oracle_retrieval",
            recovery_gain=1.0,
            top2_labels=("retrieval_error",),
            is_ambiguous=True,
            close_deltas=(("retrieval_error", 0.0), ("bad_label", 0.1)),
        )
        case = load_probe_cases(RETRIEVAL_FIXTURE)[0]
        orchestrator = RepairOrchestrator(executor=FailingExecutor())
        result = orchestrator.run(
            attribution=attr,
            case=case,
            adapter=object(),
        )
        self.assertEqual(result.labels_tried, ("retrieval_error",))
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.labels_skipped[0][0], "bad_label")

    def test_orchestrator_builds_fm_context_from_store(self) -> None:
        from cmd_audit.attribution import AttributionResult

        class CapturingExecutor:
            def __init__(self):
                self.fm_contexts = []

            def run(self, *, ecs_draft, adapter, case, fm_context=""):
                self.fm_contexts.append(fm_context)
                return RepairExecutorResult(
                    assessment="failed",
                    post_repair_answer_score=0.0,
                    post_repair_evidence_score=0.0,
                    applied_action=None,
                    repair_context=fm_context,
                    label=ecs_draft.predicted_label,
                )

        case = load_probe_cases(RETRIEVAL_FIXTURE)[0]
        record = FailureMemoryRecord(
            error_type="retrieval_error",
            wrong_memory="past wrong retrieval context",
            original_evidence="past evidence",
            cause="retrieval missed",
            corrected_memory="corrected",
            repair_action="oracle_retrieval",
            repair_guidance="route correctly",
            trigger_signature="retrieval_error lisbon travel",
            memory_top_terms=("lisbon", "travel"),
        )
        executor = CapturingExecutor()
        attr = AttributionResult(
            predicted_label="retrieval_error",
            top_replay="oracle_retrieval",
            recovery_gain=1.0,
            top2_labels=("retrieval_error",),
            is_ambiguous=False,
            close_deltas=(("retrieval_error", 0.0),),
        )
        orchestrator = RepairOrchestrator(
            executor=executor,
            fm_store=FailureMemoryStoreV1().add(record),
        )
        orchestrator.run(attribution=attr, case=case, adapter=object())
        self.assertIn("past wrong retrieval context", executor.fm_contexts[0])


if __name__ == "__main__":
    unittest.main()
