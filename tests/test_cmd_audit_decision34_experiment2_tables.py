import unittest

from cmd_audit.metrics import DiagnosisPrediction
from scripts import build_experiment_02_tables as exp2


class FakeBaseline:
    injected_context = ""
    retrieved_memory_ids = ()


class FakeCase:
    has_ingestion_trace = True
    primary_baseline = FakeBaseline()


class Experiment2TablesTest(unittest.TestCase):
    def test_build_cmd_predictions_uses_top_gain_replay(self) -> None:
        researcher = {
            "c1": exp2.ResearcherCase(
                case_id="c1",
                gold_label="retrieval_error",
                confidence="high",
                source="unit",
            )
        }
        retest = {
            "c1": [
                {"case_id": "c1", "replay_name": "oracle_write", "recovery_gain": "0.1"},
                {"case_id": "c1", "replay_name": "oracle_retrieval", "recovery_gain": "0.9"},
            ]
        }

        rows = exp2.build_cmd_predictions(researcher, retest, {"c1": FakeCase()})

        self.assertEqual(rows[0].predicted_label, "retrieval_error")
        self.assertFalse(rows[0].attribution_failed)

    def test_zero_gain_becomes_attribution_failed(self) -> None:
        researcher = {
            "c1": exp2.ResearcherCase(
                case_id="c1",
                gold_label="retrieval_error",
                confidence="medium",
                source="unit",
            )
        }
        retest = {
            "c1": [
                {"case_id": "c1", "replay_name": "oracle_retrieval", "recovery_gain": "0.0"},
                {"case_id": "c1", "replay_name": "oracle_write", "recovery_gain": "0.0"},
            ]
        }

        rows = exp2.build_cmd_predictions(researcher, retest, {"c1": FakeCase()})

        self.assertIsNone(rows[0].predicted_label)
        self.assertTrue(rows[0].attribution_failed)
        self.assertEqual(rows[0].failure_reason, "zero_gain")

    def test_headline_rows_include_high_medium_and_all(self) -> None:
        predictions = [
            exp2.CmdPrediction(
                case_id="c1",
                source="unit",
                gold_label="retrieval_error",
                confidence="high",
                predicted_label="retrieval_error",
                top2_labels=("retrieval_error", "write_error"),
                attribution_failed=False,
                failure_reason="",
                top_replay="oracle_retrieval",
                top_gain=1.0,
                cost=exp2.CostLatency(),
            ),
            exp2.CmdPrediction(
                case_id="c2",
                source="unit",
                gold_label="write_error",
                confidence="low",
                predicted_label=None,
                top2_labels=("write_error", "retrieval_error"),
                attribution_failed=True,
                failure_reason="zero_gain",
                top_replay="oracle_write",
                top_gain=0.0,
                cost=exp2.CostLatency(),
            ),
        ]

        rows = exp2.build_headline_rows(predictions)

        self.assertEqual([row["group"] for row in rows], ["high_medium", "all_130"])
        self.assertEqual(rows[0]["n"], "1")
        self.assertEqual(rows[1]["attribution_failed"], "1")

    def test_comparison_rows_include_cmd_and_baselines(self) -> None:
        cmd = [
            exp2.CmdPrediction(
                case_id="c1",
                source="unit",
                gold_label="retrieval_error",
                confidence="high",
                predicted_label="retrieval_error",
                top2_labels=("retrieval_error",),
                attribution_failed=False,
                failure_reason="",
                top_replay="oracle_retrieval",
                top_gain=1.0,
                cost=exp2.CostLatency(
                    agent_tokens=100.0,
                    scorer_tokens=20.0,
                    verifier_tokens=5.0,
                    total_tokens=125.0,
                    wallclock_sec=3.0,
                    usd_cost=0.012,
                ),
            )
        ]
        baselines = [
            DiagnosisPrediction(
                system_name="random_label",
                case_id="c1",
                gold_label="retrieval_error",
                predicted_label="write_error",
                top2_labels=("write_error",),
                cost_per_diagnosis=0.01,
            )
        ]

        rows = exp2.build_comparison_rows(cmd, baselines)
        systems = {row["system_name"] for row in rows}

        self.assertIn("CMD-Audit", systems)
        self.assertIn("random_label", systems)
        self.assertTrue(all("macro_f1_ci_low" in row for row in rows))
        cmd_row = next(
            row
            for row in rows
            if row["group"] == "high_medium" and row["system_name"] == "CMD-Audit"
        )
        self.assertEqual(cmd_row["tokens_per_case"], "125.000000")
        self.assertEqual(cmd_row["usd_per_case"], "0.012000")
        self.assertEqual(cmd_row["cost_metadata_status"], "measured")

    def test_missing_cost_metadata_is_explicit_not_faked(self) -> None:
        researcher = {
            "c1": exp2.ResearcherCase(
                case_id="c1",
                gold_label="retrieval_error",
                confidence="high",
                source="unit",
            )
        }
        retest = {
            "c1": [
                {"case_id": "c1", "replay_name": "oracle_retrieval", "recovery_gain": "0.9"}
            ]
        }

        rows = exp2.build_cmd_predictions(researcher, retest, {"c1": FakeCase()})
        cost_rows = exp2.build_cost_latency_rows(rows)

        self.assertEqual(rows[0].cost.status, "missing_cost_metadata")
        self.assertEqual(cost_rows[0]["tokens_total"], "")
        self.assertEqual(cost_rows[0]["usd_cost"], "")
        self.assertEqual(cost_rows[0]["cost_metadata_status"], "missing_cost_metadata")


if __name__ == "__main__":
    unittest.main()
