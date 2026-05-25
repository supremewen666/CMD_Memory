import inspect
import unittest
from unittest.mock import patch

from cmd_audit import (
    run_case_full_v1,
    run_case_v1,
    run_case_v1_with_hook,
    run_full_real_suite,
)
from cmd_audit.cli import main as cli_main


class Decision34TieMarginDefaultsTest(unittest.TestCase):
    def test_v1_entry_points_default_to_zero_tie_margin(self) -> None:
        for func in (
            run_case_v1,
            run_case_full_v1,
            run_case_v1_with_hook,
            run_full_real_suite,
        ):
            with self.subTest(func=func.__name__):
                signature = inspect.signature(func)
                self.assertEqual(signature.parameters["tie_margin"].default, 0.0)

    def test_cli_threads_tie_margin_to_real_suite(self) -> None:
        with patch("cmd_audit.cli.run_full_real_suite", return_value=[]) as run_suite:
            exit_code = cli_main(["run-v1", "--real-data", "--tie-margin", "0.0"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(run_suite.call_args.kwargs["tie_margin"], 0.0)

    def test_cli_threads_legacy_tie_margin_for_fixture_runs(self) -> None:
        with (
            patch("cmd_audit.cli.load_probe_cases_v1", return_value=[]) as load_cases,
            patch("cmd_audit.cli.run_cases_v1", return_value=[]) as run_cases,
            patch("cmd_audit.cli.write_attribution_table"),
            patch("cmd_audit.cli.write_comparison_metrics_table"),
            patch("cmd_audit.cli.write_confusion_matrix_table"),
        ):
            exit_code = cli_main(
                [
                    "run-v1",
                    "--cases",
                    "fixture.json",
                    "--no-hook",
                    "--tie-margin",
                    "0.05",
                ]
            )

        self.assertEqual(exit_code, 0)
        load_cases.assert_called_once_with("fixture.json")
        self.assertEqual(run_cases.call_args.kwargs["tie_margin"], 0.05)


if __name__ == "__main__":
    unittest.main()
