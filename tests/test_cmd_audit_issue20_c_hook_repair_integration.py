"""run_case_v1_with_hook + Repair integration tests — Issue 0020-C."""

from pathlib import Path
import unittest

from cmd_audit import load_probe_cases
from cmd_audit.adapters.base import Mem0Trace
from cmd_audit.adapters.mem0 import Mem0Adapter
from cmd_audit.harness import run_case_v1_with_hook_and_repair


RETRIEVAL_FIXTURE = Path("data/probe_cases/v0_retrieval_error_case.json")


class HookRepairIntegrationTest(unittest.TestCase):
    """AC: run_case_v1_with_hook_and_repair runs full pipeline."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.case = load_probe_cases(RETRIEVAL_FIXTURE)[0]
        cls.trace = Mem0Trace(
            case_id=cls.case.case_id,
            add_inputs=["original"],
            search_query=cls.case.query,
            search_results=tuple(cls.case.extracted_memory),
            store_checksum="abc123",
        )
        cls.adapter = Mem0Adapter(
            cls.trace,
            gold_evidence=cls.case.gold_evidence,
            extracted_memory=cls.case.extracted_memory,
            raw_events=cls.case.raw_events,
        )

    def test_integration_returns_dict(self) -> None:
        result = run_case_v1_with_hook_and_repair(self.case, adapter=self.adapter)
        self.assertIsInstance(result, dict)
        self.assertIn("case_id", result)
        self.assertIn("audit", result)
        self.assertIn("orchestrator_result", result)
        self.assertIn("repaired", result)

    def test_case_id_matches(self) -> None:
        result = run_case_v1_with_hook_and_repair(self.case, adapter=self.adapter)
        self.assertEqual(result["case_id"], self.case.case_id)

    def test_audit_present(self) -> None:
        result = run_case_v1_with_hook_and_repair(self.case, adapter=self.adapter)
        self.assertIsNotNone(result["audit"])
        self.assertEqual(result["audit"].case_id, self.case.case_id)

    def test_orchestrator_result_when_attribution_exists(self) -> None:
        result = run_case_v1_with_hook_and_repair(self.case, adapter=self.adapter)
        if result["audit"].attribution is not None:
            self.assertIsNotNone(result["orchestrator_result"])
            self.assertIsInstance(result["repaired"], bool)

    def test_fm_context_parameter(self) -> None:
        result = run_case_v1_with_hook_and_repair(
            self.case, adapter=self.adapter, fm_context="[Diagnostic] past error"
        )
        self.assertIsInstance(result, dict)

    def test_close_deltas_threshold(self) -> None:
        result = run_case_v1_with_hook_and_repair(
            self.case, adapter=self.adapter, close_deltas_threshold=0.05
        )
        self.assertIsInstance(result, dict)


if __name__ == "__main__":
    unittest.main()
