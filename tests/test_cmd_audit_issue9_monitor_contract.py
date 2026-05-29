"""Behavior-level tests for issue 0009: Harden Subagent Judge Monitor contract."""

from __future__ import annotations

import unittest

from cmd_audit import (
    MONITOR_ANOMALY_REASON_VALUES,
    MonitorAnomalyReasonError,
    validate_monitor_anomaly_reason,
)
from cmd_audit.baselines.comparators import LeakSafeMonitorError
from cmd_audit.baselines.comparators import SubagentJudgeMonitorDecision, validate_evidence_pointers, validate_monitor_payload


class MonitorAnomalyReasonEnumTest(unittest.TestCase):
    def test_all_four_enum_values_accepted(self) -> None:
        for reason in MONITOR_ANOMALY_REASON_VALUES:
            with self.subTest(reason=reason):
                result = validate_monitor_anomaly_reason(reason)
                self.assertEqual(result, reason)

    def test_free_form_natural_language_rejected(self) -> None:
        bad_reasons = [
            "the answer looks wrong compared to stored facts",
            "baseline evidence score is below success threshold",
            "possible retrieval failure detected",
            "suspicious context injection",
        ]
        for reason in bad_reasons:
            with self.subTest(reason=reason):
                with self.assertRaises(MonitorAnomalyReasonError):
                    validate_monitor_anomaly_reason(reason)

    def test_misspelled_enum_value_rejected(self) -> None:
        bad_reasons = [
            "answer_vs_evidence_mismach",
            "retrieved_context_incompletee",
            "evidence_recall_low ",
            "Confidence_Anomaly",
            "",
        ]
        for reason in bad_reasons:
            with self.subTest(reason=reason):
                with self.assertRaises(MonitorAnomalyReasonError):
                    validate_monitor_anomaly_reason(reason)

    def test_none_or_empty_rejected(self) -> None:
        with self.assertRaises(MonitorAnomalyReasonError):
            validate_monitor_anomaly_reason("")

    def test_decision_construction_rejects_bad_reason(self) -> None:
        with self.assertRaises(MonitorAnomalyReasonError):
            SubagentJudgeMonitorDecision(
                should_trigger_replay=True,
                risk_score=0.9,
                anomaly_reason="not a valid enum value",
                evidence_pointers=(),
                trace_summary="test",
            )

    def test_decision_construction_accepts_valid_reason(self) -> None:
        decision = SubagentJudgeMonitorDecision(
            should_trigger_replay=True,
            risk_score=0.9,
            anomaly_reason="evidence_recall_low",
            evidence_pointers=("mem_003",),
            trace_summary="test",
        )
        self.assertEqual(decision.anomaly_reason, "evidence_recall_low")


class MonitorEvidencePointerTest(unittest.TestCase):
    def test_opaque_ids_accepted(self) -> None:
        valid = ("mem_001", "mem_002", "evt_301")
        result = validate_evidence_pointers(valid)
        self.assertEqual(result, valid)

    def test_empty_pointers_accepted(self) -> None:
        result = validate_evidence_pointers(())
        self.assertEqual(result, ())

    def test_content_bearing_pointers_rejected(self) -> None:
        bad_pointers = [
            ("mem_003: user lives in Berlin",),
            ("memory item #4 contains stale data",),
            ("mem_001\nevidence leaked",),
        ]
        for pointers in bad_pointers:
            with self.subTest(pointers=pointers):
                with self.assertRaises(LeakSafeMonitorError):
                    validate_evidence_pointers(pointers)

    def test_pointer_with_colon_rejected(self) -> None:
        with self.assertRaises(LeakSafeMonitorError):
            validate_evidence_pointers(("mem_003:Berlin",))

    def test_decision_construction_rejects_bad_pointer(self) -> None:
        with self.assertRaises(LeakSafeMonitorError):
            SubagentJudgeMonitorDecision(
                should_trigger_replay=True,
                risk_score=0.9,
                anomaly_reason="evidence_recall_low",
                evidence_pointers=("mem_003: user lives in Berlin",),
                trace_summary="test",
            )


class MonitorForbiddenFieldsTest(unittest.TestCase):
    def test_forbidden_field_names_rejected_in_payload(self) -> None:
        forbidden_keys = (
            "label",
            "labels",
            "final_label",
            "predicted_label",
            "diagnosis_label",
            "cmd_label",
            "attribution",
            "attribution_label",
            "replay_label",
            "top2_labels",
            "ecs",
            "error_cause_solution",
            "repair_guidance",
            "corrected_memory",
            "memory_write",
            "memory_writes",
            "gold_answer",
            "gold_evidence",
            "raw_events",
            "extracted_memory",
            "baseline_outputs",
            "full_trace",
            "full_failed_trace",
            "failed_trace",
        )
        for key in forbidden_keys:
            with self.subTest(forbidden_key=key):
                with self.assertRaises(LeakSafeMonitorError):
                    validate_monitor_payload(
                        {"should_trigger_replay": True, key: "not allowed"}
                    )

    def test_clean_payload_with_anomaly_reason_accepted(self) -> None:
        payload = {
            "should_trigger_replay": True,
            "risk_score": 0.9,
            "anomaly_reason": "evidence_recall_low",
            "evidence_pointers": ["mem_001"],
            "trace_summary": "test",
            "cost_per_decision": 0.2,
        }
        result = validate_monitor_payload(payload)
        self.assertEqual(result["anomaly_reason"], "evidence_recall_low")

    def test_payload_with_forbidden_field_nested_rejected(self) -> None:
        with self.assertRaises(LeakSafeMonitorError):
            validate_monitor_payload(
                {
                    "should_trigger_replay": True,
                    "nested": {"gold_answer": "Berlin"},
                }
            )


class MonitorEndToEndContractTest(unittest.TestCase):
    def test_monitor_payload_exposes_anomaly_reason_and_pointers(self) -> None:
        from cmd_audit import load_probe_cases, run_baseline_suite

        case = load_probe_cases("data/probe_cases/v0_issue3_cases.json")[0]
        payload = run_baseline_suite(case).monitor.to_payload()

        self.assertIn("anomaly_reason", payload)
        self.assertIn(payload["anomaly_reason"], MONITOR_ANOMALY_REASON_VALUES)
        self.assertIn("evidence_pointers", payload)
        self.assertIsInstance(payload["evidence_pointers"], list)
        for ptr in payload["evidence_pointers"]:
            self.assertIsInstance(ptr, str)
            self.assertNotIn(":", ptr)
            self.assertNotIn(" ", ptr)
