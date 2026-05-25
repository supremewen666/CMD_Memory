from pathlib import Path
import csv
import tempfile
import unittest

import numpy as np

from cmd_audit import load_probe_cases
from cmd_audit.hook.constants import V1_REPLAY_NAME_ORDER
from scripts import calibrate_hook


FIXTURE = Path("data/probe_cases/v0_retrieval_error_case.json")


class Decision34CalibrateHookRetestTest(unittest.TestCase):
    def test_training_set_can_be_built_from_llm_retest_csv(self) -> None:
        case = load_probe_cases(FIXTURE)[0]

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "at_scale_llm_retest.csv"
            _write_retest_csv(csv_path, case.case_id, positive_replay="oracle_retrieval")

            gains = calibrate_hook.load_retest_recovery_gains(csv_path)
            training_set = calibrate_hook.build_training_set_from_retest([case], gains)
            output = Path(tmpdir) / "training_set_llm.npz"
            calibrate_hook.save_training_set(training_set, output)

            persisted = np.load(output)

        self.assertEqual(training_set.features.shape, (10, 16))
        self.assertEqual(int(training_set.labels.sum()), 1)
        self.assertIn("oracle_retrieval", training_set.replay_names)
        self.assertEqual(int(persisted["labels"].sum()), 1)

    def test_threshold_grid_can_use_retest_labels(self) -> None:
        case = load_probe_cases(FIXTURE)[0]

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "at_scale_llm_retest.csv"
            _write_retest_csv(csv_path, case.case_id, positive_replay="oracle_write")

            gains = calibrate_hook.load_retest_recovery_gains(csv_path)
            best, rows = calibrate_hook.calibrate_thresholds_from_retest(
                [case],
                retest_gains=gains,
                weights=(0.0,) * 16,
                intercept=0.0,
                grid_top_k=(2,),
            )

        self.assertGreater(len(rows), 0)
        self.assertEqual(best.f2, 1.0)

    def test_threshold_grid_artifacts_keep_decision34_name_and_legacy_name(self) -> None:
        rows = [
            {
                "top_k": "2",
                "fallback_threshold": "0.00",
                "precision": "1.000000",
                "recall": "1.000000",
                "f2": "1.000000",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            calibrate_hook.write_threshold_grid_artifacts(rows, tmpdir)

            grid_path = Path(tmpdir) / "grid_search.csv"
            legacy_path = Path(tmpdir) / "threshold_grid.csv"

            self.assertTrue(grid_path.exists())
            self.assertTrue(legacy_path.exists())
            self.assertEqual(grid_path.read_text(), legacy_path.read_text())


def _write_retest_csv(path: Path, case_id: str, *, positive_replay: str) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["case_id", "replay_name", "recovery_gain"],
        )
        writer.writeheader()
        for replay_name in V1_REPLAY_NAME_ORDER:
            writer.writerow(
                {
                    "case_id": case_id,
                    "replay_name": replay_name,
                    "recovery_gain": "0.75" if replay_name == positive_replay else "0.0",
                }
            )


if __name__ == "__main__":
    unittest.main()
