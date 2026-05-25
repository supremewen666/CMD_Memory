from pathlib import Path
import tempfile
import unittest

from scripts import build_surrogate_gap_summary as sg_summary


class SurrogateGapSummaryTest(unittest.TestCase):
    def test_summarize_by_label_marks_top_two_online_recoverable(self) -> None:
        rows = [
            sg_summary.SurrogateRetentionRow("w1", "write_error", 1.0, 0.2),
            sg_summary.SurrogateRetentionRow("c1", "compression_error", 1.0, 0.9),
            sg_summary.SurrogateRetentionRow("p1", "premature_extraction_error", 1.0, 0.8),
            sg_summary.SurrogateRetentionRow("i1", "injection_error", 1.0, 0.1),
        ]

        summaries = sg_summary.summarize_by_label(
            rows,
            bootstrap_iterations=25,
            random_state=42,
        )
        designations = {row["label"]: row["designation"] for row in summaries}

        self.assertEqual(designations["compression_error"], "online_recoverable")
        self.assertEqual(
            designations["premature_extraction_error"],
            "online_recoverable",
        )
        self.assertEqual(designations["write_error"], "ecs_reporting_only")

    def test_write_summary_uses_retention_not_accuracy_framing(self) -> None:
        rows = [
            {
                "label": "compression_error",
                "n": "1",
                "retention": "0.900000",
                "ci_low": "0.900000",
                "ci_high": "0.900000",
                "designation": "online_recoverable",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "summary.txt"
            sg_summary.write_summary(out, rows)

            text = out.read_text(encoding="utf-8")

        self.assertIn("retention rate, not online accuracy", text)
        self.assertIn("Paper fragment", text)


if __name__ == "__main__":
    unittest.main()
