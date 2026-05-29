from pathlib import Path
import csv
import json
import tempfile
import unittest

from cmd_audit.eval.surrogate_gap import SurrogateGapRow
from scripts.run_surrogate_gap_llm import load_holdout_case_ids, write_rows


class SurrogateGapRunnerTest(unittest.TestCase):
    def test_load_holdout_case_ids_accepts_json_cases(self) -> None:
        payload = {"cases": [{"case_id": "c1"}, {"case_id": "c2"}]}

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "holdout.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            ids = load_holdout_case_ids(path)

        self.assertEqual(ids, ["c1", "c2"])

    def test_write_rows_outputs_required_summary_columns(self) -> None:
        rows = (
            SurrogateGapRow(
                case_id="c1",
                label="write_error",
                gold_recovery_gain=0.8,
                surrogate_recovery_gain=0.4,
                gap=0.4,
                surrogate_found=True,
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "surrogate.csv"
            write_rows(path, rows)
            with path.open(newline="", encoding="utf-8") as handle:
                loaded = list(csv.DictReader(handle))

        self.assertEqual(loaded[0]["gold_recovery_gain"], "0.800000")
        self.assertEqual(loaded[0]["surrogate_recovery_gain"], "0.400000")
        self.assertEqual(loaded[0]["surrogate_found"], "true")


if __name__ == "__main__":
    unittest.main()
