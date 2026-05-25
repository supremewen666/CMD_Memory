"""PreCmdDecision signals → AuditResult tests — Issue 0020-F."""

from pathlib import Path
import unittest

from cmd_audit import (
    AuditResult,
    load_probe_cases,
    run_case_v1_with_hook,
)


RETRIEVAL_FIXTURE = Path("data/probe_cases/v0_retrieval_error_case.json")


class HookSignalsInAuditResultTest(unittest.TestCase):
    """AC: AuditResult carries Pre-CMD Hook signals."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.case = load_probe_cases(RETRIEVAL_FIXTURE)[0]

    def test_audit_result_has_hook_fields(self) -> None:
        result = run_case_v1_with_hook(self.case)
        self.assertTrue(hasattr(result, "hook_stage"))
        self.assertTrue(hasattr(result, "selected_replays"))
        self.assertTrue(hasattr(result, "per_replay_scores"))

    def test_hook_stage_is_string(self) -> None:
        result = run_case_v1_with_hook(self.case)
        self.assertIsInstance(result.hook_stage, str)

    def test_selected_replays_is_tuple(self) -> None:
        result = run_case_v1_with_hook(self.case)
        self.assertIsInstance(result.selected_replays, tuple)

    def test_per_replay_scores_is_tuple(self) -> None:
        result = run_case_v1_with_hook(self.case)
        self.assertIsInstance(result.per_replay_scores, tuple)

    def test_run_case_without_hook_has_empty_signals(self) -> None:
        from cmd_audit import run_case_v1
        result = run_case_v1(self.case)
        self.assertEqual(result.hook_stage, "")
        self.assertEqual(result.selected_replays, ())
        self.assertEqual(result.per_replay_scores, ())

    def test_audit_result_is_frozen(self) -> None:
        result = run_case_v1_with_hook(self.case)
        with self.assertRaises(AttributeError):
            result.hook_stage = "modified"


if __name__ == "__main__":
    unittest.main()
