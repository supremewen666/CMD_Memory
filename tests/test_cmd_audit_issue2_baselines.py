from pathlib import Path
import tempfile
import unittest

from cmd_audit import load_probe_cases, run_case
from cmd_audit.baselines.comparators import (
    FORBIDDEN_MONITOR_FIELDS,
    LeakSafeMonitorError,
    run_baseline_suite,
    validate_monitor_payload,
)
from cmd_audit.harness import diagnosis_predictions, write_comparison_metrics_table
from cmd_audit.eval.metrics import compute_diagnosis_metrics


FIXTURE = Path("data/probe_cases/v0_retrieval_error_case.json")


class BaselineAndComparatorTest(unittest.TestCase):
    def test_issue2_baseline_suite_keeps_comparators_separate_from_cmd(self) -> None:
        case = load_probe_cases(FIXTURE)[0]

        suite = run_baseline_suite(case)

        self.assertEqual(
            {baseline.baseline_name for baseline in suite.memory_baselines},
            {"fixed_summary", "vector_memory"},
        )
        self.assertTrue(all(baseline.failed for baseline in suite.memory_baselines))
        self.assertEqual(
            suite.evidence_recall_heuristic.comparator_name, "evidence_recall"
        )
        self.assertEqual(
            suite.evidence_recall_heuristic.predicted_label, "retrieval_error"
        )
        self.assertFalse(suite.evidence_recall_heuristic.uses_counterfactual_replay)
        self.assertEqual(suite.subagent_judge.comparator_name, "subagent_judge")
        self.assertIn("post-hoc", suite.subagent_judge.explanation)
        self.assertFalse(suite.subagent_judge.uses_counterfactual_replay)
        self.assertEqual(suite.random_label.comparator_name, "random_label")

    def test_run_case_exposes_baseline_suite_but_cmd_label_still_comes_from_replay(
        self,
    ) -> None:
        result = run_case(load_probe_cases(FIXTURE)[0])

        self.assertEqual(result.attribution.predicted_label, "retrieval_error")
        self.assertEqual(result.attribution.top_replay, "oracle_retrieval")
        self.assertEqual(
            result.baseline_suite.subagent_judge.predicted_label, "retrieval_error"
        )
        self.assertNotEqual(
            result.baseline_suite.subagent_judge.comparator_name, "CMD-Audit"
        )


class SubagentJudgeMonitorBoundaryTest(unittest.TestCase):
    def test_monitor_payload_can_trigger_replay_without_forbidden_outputs(self) -> None:
        case = load_probe_cases(FIXTURE)[0]

        payload = run_baseline_suite(case).monitor.to_payload()

        self.assertTrue(payload["should_trigger_replay"])
        self.assertTrue(set(payload).isdisjoint(FORBIDDEN_MONITOR_FIELDS))
        self.assertNotIn(case.gold_answer, str(payload))
        self.assertNotIn("retrieval_error", str(payload))

    def test_monitor_rejects_final_labels_ecs_memory_writes_gold_answers_and_full_traces(
        self,
    ) -> None:
        for forbidden_key in (
            "final_label",
            "ecs",
            "memory_writes",
            "gold_answer",
            "full_failed_trace",
        ):
            with self.subTest(forbidden_key=forbidden_key):
                with self.assertRaises(LeakSafeMonitorError):
                    validate_monitor_payload(
                        {"should_trigger_replay": True, forbidden_key: "not allowed"}
                    )


class ComparisonMetricsTest(unittest.TestCase):
    def test_comparison_metrics_include_accuracy_macro_f1_top2_and_cost(self) -> None:
        result = run_case(load_probe_cases(FIXTURE)[0])
        predictions = diagnosis_predictions(result)

        metrics = compute_diagnosis_metrics(predictions)

        self.assertIn("CMD-Audit", metrics)
        self.assertIn("evidence_recall", metrics)
        self.assertIn("subagent_judge", metrics)
        self.assertIn("random_label", metrics)
        self.assertEqual(metrics["CMD-Audit"].attribution_accuracy, 1.0)
        self.assertEqual(metrics["CMD-Audit"].top2_accuracy, 1.0)
        self.assertGreaterEqual(metrics["CMD-Audit"].macro_f1, 0.0)
        self.assertGreater(metrics["CMD-Audit"].cost_per_diagnosis, 0.0)

    def test_comparison_metrics_table_can_be_written(self) -> None:
        result = run_case(load_probe_cases(FIXTURE)[0])

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "comparison_metrics.csv"
            write_comparison_metrics_table([result], output)
            text = output.read_text(encoding="utf-8")

        self.assertIn(
            "system_name,attribution_accuracy,macro_f1,top2_accuracy,cost_per_diagnosis",
            text,
        )
        self.assertIn("CMD-Audit", text)


if __name__ == "__main__":
    unittest.main()
