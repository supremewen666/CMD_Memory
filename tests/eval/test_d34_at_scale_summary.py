from pathlib import Path
import tempfile
import unittest

from scripts import build_at_scale_retest_summary as summary


class AtScaleRetestSummaryTest(unittest.TestCase):
    def test_build_scale_predictions_maps_top_replay_to_label(self) -> None:
        retest = {
            "c1": [
                {"case_id": "c1", "replay_name": "oracle_write", "recovery_gain": "0.1"},
                {"case_id": "c1", "replay_name": "oracle_retrieval", "recovery_gain": "0.9"},
            ]
        }
        meta = {
            "c1": summary.CaseMeta(
                case_id="c1",
                source="unit",
                gold_label="retrieval_error",
                has_ingestion_trace=True,
            )
        }

        rows = summary.build_scale_predictions(retest, meta)

        self.assertEqual(rows[0].predicted_label, "retrieval_error")
        self.assertEqual(rows[0].top_replay, "oracle_retrieval")

    def test_zero_gain_sets_attribution_failed(self) -> None:
        retest = {
            "c1": [
                {"case_id": "c1", "replay_name": "oracle_retrieval", "recovery_gain": "0.0"}
            ]
        }
        meta = {
            "c1": summary.CaseMeta(
                case_id="c1",
                source="unit",
                gold_label="retrieval_error",
                has_ingestion_trace=True,
            )
        }

        rows = summary.build_scale_predictions(retest, meta)

        self.assertTrue(rows[0].attribution_failed)
        self.assertEqual(rows[0].failure_reason, "zero_gain")

    def test_write_summary_outputs_scale_sanity_framing(self) -> None:
        rows = [
            {
                "group": "aggregate",
                "n": "1",
                "macro_f1": "1.000000",
                "attribution_accuracy": "1.000000",
                "top2_accuracy": "1.000000",
                "attribution_failed": "0",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "summary.txt"
            summary.write_summary(out, rows)

            text = out.read_text(encoding="utf-8")

        self.assertIn("supplementary agreement", text)
        self.assertIn("macro_f1: 1.000000", text)


if __name__ == "__main__":
    unittest.main()
