"""Case-first Failure Memory markdown contract."""

from pathlib import Path
import tempfile
import unittest

from cmd_audit.repair import (
    FailureMemoryDiagnosis,
    FailureMemoryOutcome,
    FailureMemorySkill,
    MarkdownFailureMemoryStore,
)
from cmd_audit.counterfactual import OperatorSpec, PipelineAction


class FailureMemorySkillContractTest(unittest.TestCase):
    def test_format_case_records_diagnosis_repair_and_outcome(self) -> None:
        diagnosis = FailureMemoryDiagnosis(
            query="What is my afternoon schedule?",
            label="item_conflict",
            cause="Two retrieved events overlap for the same time window.",
            corrected_memory="14:00 meeting; 14:30 dentist appointment.",
            repair_guidance="List the conflict and ask the user to clarify priority.",
            retrieved_items=(
                "item_1: 14:00 meeting",
                "item_2: 14:30 dentist appointment",
            ),
            signature="schedule overlap item_conflict",
            problem_item="item_1 and item_2 conflict",
            pattern="[[pattern_temporal_conflict]]",
            operator_spec=OperatorSpec.single(1, PipelineAction.INJECTION_ERROR),
        )
        outcome = FailureMemoryOutcome(
            assessment="recovered",
            recovered=True,
            recovery_gain=1.0,
        )

        markdown = FailureMemorySkill().format_case(diagnosis, outcome)

        self.assertIn("## Retrieved Items", markdown)
        self.assertIn("**Label**: item_conflict", markdown)
        self.assertIn("## Repair", markdown)
        self.assertIn("### Operator Spec", markdown)
        self.assertIn("hop=2 action=injection_error", markdown)
        self.assertIn("**Assessment**: recovered", markdown)
        self.assertIn("[[pattern_temporal_conflict]]", markdown)

    def test_format_pattern_emits_operator_spec_skill(self) -> None:
        skill = FailureMemorySkill()
        case = skill.format_case(
            FailureMemoryDiagnosis(
                query="Where is the workshop?",
                label="retrieval_error",
                cause="Retriever missed the venue memory.",
                corrected_memory="Workshop venue is Madrid.",
                repair_guidance="Add the missed venue memory.",
                operator_spec=OperatorSpec.single(0, PipelineAction.RETRIEVAL_ERROR),
            ),
            FailureMemoryOutcome(
                assessment="recovered",
                recovered=True,
                recovery_gain=0.7,
            ),
        )

        pattern = skill.format_pattern(
            (case,),
            trigger_fingerprint="bridge key madrid",
            source_case_ids=("case_bridge_1",),
            operator_spec=OperatorSpec.single(0, PipelineAction.RETRIEVAL_ERROR),
            recovery_track={"recovered": 1, "total": 1, "avg_recovery_gain": 0.7},
        )

        self.assertIn("**Trigger Fingerprint**: bridge key madrid", pattern)
        self.assertIn("## Operator Spec", pattern)
        self.assertIn("hop=1 action=retrieval_error", pattern)
        self.assertIn("select=missed_candidates", pattern)
        self.assertIn("## Recovery Track Record", pattern)
        self.assertIn("- case_bridge_1", pattern)
        self.assertIn("Acceptance gate", pattern)

    def test_validate_pattern_prefers_case_truth(self) -> None:
        skill = FailureMemorySkill()
        case = skill.format_case(
            FailureMemoryDiagnosis(
                query="Where should I go?",
                label="item_stale",
                cause="Old address remains in memory.",
                corrected_memory="Use the new address.",
                repair_guidance="Prefer the newer timestamped item.",
            ),
            FailureMemoryOutcome(assessment="recovered", recovered=True),
        )
        pattern = (
            "# Pattern: Conflict\n\n"
            "**Diagnosis**: item_conflict\n\n"
            "**Source Cases**:\n- case_1\n"
        )

        result = skill.validate_pattern(pattern, (case,))

        self.assertFalse(result.valid)
        self.assertIn("pattern label does not match", result.inconsistencies[0])

    def test_diagnose_without_llm_returns_prompt_not_keyword_lookup(self) -> None:
        result = FailureMemorySkill().diagnose(
            "Query and retrieved memory context",
            memory_path=Path("FAILURE_MEMORY"),
        )

        self.assertTrue(result["requires_llm"])
        self.assertIn("read the index, then concrete", result["prompt"])


class MarkdownFailureMemoryStoreTest(unittest.TestCase):
    def test_three_layer_store_writes_index_cases_and_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MarkdownFailureMemoryStore(tmpdir)

            case_path = store.write_case(
                "case_schedule_conflict",
                "# Case: schedule conflict\n",
                summary="schedule overlap, item_conflict",
            )
            pattern_path = store.write_pattern(
                "pattern_temporal_conflict",
                "# Pattern: temporal conflict\n",
                summary="same entity has conflicting time events",
            )
            index = store.read_index()

        self.assertEqual(case_path.name, "case_schedule_conflict.md")
        self.assertEqual(pattern_path.name, "pattern_temporal_conflict.md")
        self.assertIn("cases/case_schedule_conflict.md", index)
        self.assertIn("patterns/pattern_temporal_conflict.md", index)

    def test_reuse_prompt_is_case_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MarkdownFailureMemoryStore(tmpdir)
            store.write_case(
                "case_stale_address",
                "# Case: stale address\n",
                summary="address query, item_stale",
            )

            prompt = store.reuse_prompt(
                query="Where is the office?",
                retrieved_items=("old office address",),
                failure_signal="answer contradicts latest evidence",
            )

        self.assertIn("cases/case_stale_address.md", prompt)
        self.assertIn("Do not use patterns until", prompt)
        self.assertIn("prefer the case", prompt)


if __name__ == "__main__":
    unittest.main()
