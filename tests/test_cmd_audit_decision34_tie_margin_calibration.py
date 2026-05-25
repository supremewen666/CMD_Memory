import csv
from pathlib import Path
import tempfile
import unittest

from scripts import calibrate_tie_margin


class Decision34TieMarginCalibrationTest(unittest.TestCase):
    def test_compute_case_gaps_from_retest_csv(self) -> None:
        rows = [
            _row("c1", "oracle_retrieval", "retrieval_error", 0.90),
            _row("c1", "oracle_compression", "retrieval_error", 0.86),
            _row("c2", "oracle_write", "write_error", 0.70),
            _row("c2", "oracle_retrieval", "write_error", 0.20),
        ]

        gaps = calibrate_tie_margin.compute_case_gaps(rows)

        self.assertEqual(len(gaps), 2)
        self.assertEqual(gaps[0].case_id, "c1")
        self.assertAlmostEqual(gaps[0].top2_gap, 0.04)

    def test_sample_near_ties_respects_gap_threshold(self) -> None:
        gaps = calibrate_tie_margin.compute_case_gaps(
            [
                _row("c1", "oracle_retrieval", "retrieval_error", 0.90),
                _row("c1", "oracle_compression", "retrieval_error", 0.86),
                _row("c2", "oracle_write", "write_error", 0.90),
                _row("c2", "oracle_retrieval", "write_error", 0.20),
            ]
        )

        selected = calibrate_tie_margin.sample_near_ties(
            gaps,
            max_gap=0.10,
            target_cases=10,
            seed=45,
        )

        self.assertEqual([gap.case_id for gap in selected], ["c1"])

    def test_calibrate_margin_from_inspection(self) -> None:
        gaps = calibrate_tie_margin.compute_case_gaps(
            [
                _row("c1", "oracle_retrieval", "retrieval_error", 0.90),
                _row("c1", "oracle_compression", "retrieval_error", 0.86),
                _row("c2", "oracle_write", "write_error", 0.90),
                _row("c2", "oracle_retrieval", "write_error", 0.70),
            ]
        )

        best, rows = calibrate_tie_margin.calibrate_margin(
            gaps,
            {"c1": "genuine_coupled", "c2": "scorer_noise"},
        )

        self.assertGreaterEqual(best, 0.04)
        self.assertTrue(any(row["valid"] == "True" for row in rows))

    def test_case_meta_fills_missing_label_and_source(self) -> None:
        rows = [
            {"case_id": "c1", "replay_name": "oracle_retrieval", "recovery_gain": "0.9"},
            {"case_id": "c1", "replay_name": "oracle_write", "recovery_gain": "0.8"},
        ]
        meta = {
            "c1": calibrate_tie_margin.CaseMeta(
                gold_label="retrieval_error",
                source="unit",
            )
        }

        gaps = calibrate_tie_margin.compute_case_gaps(rows, meta)

        self.assertEqual(gaps[0].gold_label, "retrieval_error")
        self.assertEqual(gaps[0].source, "unit")

    def test_distribution_rows_count_flagged_by_label(self) -> None:
        gaps = calibrate_tie_margin.compute_case_gaps(
            [
                _row("c1", "oracle_retrieval", "retrieval_error", 0.90),
                _row("c1", "oracle_compression", "retrieval_error", 0.86),
                _row("c2", "oracle_write", "write_error", 0.90),
                _row("c2", "oracle_retrieval", "write_error", 0.20),
            ]
        )

        rows = calibrate_tie_margin.build_distribution_rows(gaps, tie_margin=0.05)
        by_label = {row["label"]: row for row in rows}

        self.assertEqual(by_label["retrieval_error"]["flagged_coupled_signature"], "1")
        self.assertEqual(by_label["write_error"]["flagged_coupled_signature"], "0")

    def test_cli_help_and_csv_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "retest.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "case_id",
                        "source",
                        "gold_label",
                        "replay_name",
                        "recovery_gain",
                    ],
                )
                writer.writeheader()
                writer.writerow(_row("c1", "oracle_retrieval", "retrieval_error", 0.90))
                writer.writerow(_row("c1", "oracle_compression", "retrieval_error", 0.86))

            rows = calibrate_tie_margin.load_retest_rows(csv_path)

        self.assertEqual(len(rows), 2)


def _row(case_id: str, replay_name: str, label: str, gain: float) -> dict[str, str]:
    return {
        "case_id": case_id,
        "source": "unit",
        "gold_label": label,
        "replay_name": replay_name,
        "recovery_gain": str(gain),
    }


if __name__ == "__main__":
    unittest.main()
