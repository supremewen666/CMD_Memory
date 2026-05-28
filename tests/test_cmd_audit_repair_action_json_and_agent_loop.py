"""RepairAction JSON output and real agent replay loop tests."""

from pathlib import Path
import unittest

from cmd_audit import (
    RepairAction,
    RepairActionOutputError,
    RepairExecutor,
    load_probe_cases,
    parse_repair_action_response,
    run_case_v1,
    run_v1_replay_portfolio,
)
from cmd_audit.adapters.base import Mem0Trace
from cmd_audit.adapters.mem0 import Mem0Adapter
from cmd_audit.core.labels import PIPELINE_LABELS
from cmd_audit.repair import draft_ecs


RETRIEVAL_FIXTURE = Path("data/probe_cases/v0_retrieval_error_case.json")


class FakeLLMClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[tuple[str, str | None]] = []

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        self.prompts.append((prompt, system))
        return self.response


class RepairActionJsonContractTest(unittest.TestCase):
    def test_parse_strict_json_action(self) -> None:
        action = parse_repair_action_response(
            """
            {
              "action_type": "append",
              "target_item_id": null,
              "target_store": "episodic",
              "content": "Mira chose Lisbon for the Q3 offsite.",
              "label": "retrieval_error",
              "reasoning": "Append corrected evidence to the reachable store."
            }
            """,
            supported_actions=("append", "replace"),
            expected_label="retrieval_error",
        )

        self.assertIsInstance(action, RepairAction)
        self.assertEqual(action.action_type, "append")
        self.assertIsNone(action.target_item_id)

    def test_rejects_markdown_fenced_json(self) -> None:
        with self.assertRaises(RepairActionOutputError):
            parse_repair_action_response(
                '```json\n{"action_type":"append"}\n```',
                supported_actions=("append",),
            )

    def test_rejects_unsupported_action(self) -> None:
        with self.assertRaises(RepairActionOutputError):
            parse_repair_action_response(
                """
                {
                  "action_type": "relocate",
                  "target_item_id": null,
                  "target_store": "episodic",
                  "content": "content",
                  "label": "retrieval_error"
                }
                """,
                supported_actions=("append", "replace"),
                expected_label="retrieval_error",
            )

    def test_rejects_label_mismatch(self) -> None:
        with self.assertRaises(RepairActionOutputError):
            parse_repair_action_response(
                """
                {
                  "action_type": "append",
                  "target_item_id": null,
                  "target_store": "episodic",
                  "content": "content",
                  "label": "write_error"
                }
                """,
                supported_actions=("append",),
                expected_label="retrieval_error",
            )


class RepairExecutorLLMActionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.case = load_probe_cases(RETRIEVAL_FIXTURE)[0]
        self.audit = run_case_v1(self.case)
        self.ecs = draft_ecs(self.case, self.audit)
        self.adapter = Mem0Adapter(
            Mem0Trace(
                case_id=self.case.case_id,
                add_inputs=["original"],
                search_query=self.case.query,
                search_results=tuple(self.case.extracted_memory),
                store_checksum="abc123",
            ),
            gold_evidence=self.case.gold_evidence,
            extracted_memory=self.case.extracted_memory,
            raw_events=self.case.raw_events,
        )

    def test_executor_uses_llm_json_action_when_client_present(self) -> None:
        llm = FakeLLMClient(
            """
            {
              "action_type": "append",
              "target_item_id": null,
              "target_store": "episodic",
              "content": "Mira chose Lisbon for the Q3 offsite.",
              "label": "retrieval_error",
              "reasoning": "Put the corrected evidence into the default store."
            }
            """
        )

        result = RepairExecutor(llm_client=llm).run(
            ecs_draft=self.ecs,
            adapter=self.adapter,
            case=self.case,
        )

        self.assertEqual(result.action_selection_source, "llm")
        self.assertIsNotNone(result.applied_action)
        self.assertEqual(result.applied_action.action_type, "append")
        self.assertTrue(llm.prompts)
        self.assertIn("ADAPTER SUPPORTED ACTIONS", llm.prompts[0][0])

    def test_strict_llm_mode_reports_action_selection_failed(self) -> None:
        result = RepairExecutor(
            llm_client=FakeLLMClient("use append"),
            require_llm_action=True,
        ).run(
            ecs_draft=self.ecs,
            adapter=self.adapter,
            case=self.case,
        )

        self.assertEqual(result.assessment, "action_selection_failed")
        self.assertIsNone(result.applied_action)
        self.assertEqual(result.action_selection_source, "llm_error")


class AttributionAgentLoopTest(unittest.TestCase):
    def test_replay_context_leak_free_invariant(self) -> None:
        case = load_probe_cases(RETRIEVAL_FIXTURE)[0]
        calls: list[tuple[str, str]] = []

        def agent_generate(query: str, context: str) -> str:
            calls.append((query, context))
            return context

        def scorer(gold_evidence, text: str) -> float:
            return 1.0 if "Lisbon" in text else 0.0

        replays = run_v1_replay_portfolio(
            case, agent_generate=agent_generate, scorer=scorer
        )
        retrieval = next(r for r in replays if r.replay_name == "oracle_retrieval")

        self.assertTrue(calls)
        first_context = calls[0][1]
        self.assertIn("BASELINE CONTEXT", first_context)
        self.assertIn("COUNTERFACTUAL EVIDENCE BLOCK", first_context)
        self.assertNotIn("CMD ATTRIBUTION LABEL", first_context)
        for label_name in PIPELINE_LABELS:
            self.assertNotIn(label_name, first_context)
        self.assertIn("COUNTERFACTUAL EVIDENCE BLOCK", retrieval.answer)
        self.assertIn("Lisbon", retrieval.answer)
        self.assertEqual(retrieval.recovery_gain, retrieval.evidence_score)


if __name__ == "__main__":
    unittest.main()
