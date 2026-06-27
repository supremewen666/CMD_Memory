"""Repair executor context-composition contract.

The answer-time executor is fixed to `corrected_memory` only. Guidance, failure
memory prose, and the repaired evidence block are retained as metadata but must
not be injected as answer hints.
"""

import unittest

from cmd_audit.core.models import BaselineOutput, GoldEvidence, ProbeCase
from cmd_audit.repair import ECSDraft
from cmd_audit.repair.post_repair import (
    RepairedContext,
    _combine_context,
    detect_gold_answer_leak,
    run_post_repair_context_replay,
)


class CombineContextExecutorTest(unittest.TestCase):
    def _ctx(self, **kw) -> RepairedContext:
        base = dict(
            case_id="c1",
            corrected_memory="EVIDENCE_TEXT",
            repaired_evidence_block="EVIDENCE_TEXT",
            original_query="q",
            operator_metadata="OPERATOR_METADATA_TEXT",
        )
        base.update(kw)
        return RepairedContext(**base)

    def test_combined_contains_corrected_memory_only(self) -> None:
        combined = _combine_context(self._ctx())
        self.assertIn("EVIDENCE_TEXT", combined)
        self.assertNotIn("OPERATOR_METADATA_TEXT", combined)

    def test_duplicate_evidence_block_not_reinjected(self) -> None:
        # corrected_memory == repaired_evidence_block (the production case).
        # The evidence text must appear exactly once, not twice.
        combined = _combine_context(self._ctx())
        self.assertEqual(combined.count("EVIDENCE_TEXT"), 1)

    def test_fm_context_not_injected_into_answer_context(self) -> None:
        combined = _combine_context(self._ctx(fm_context="FM_TEXT"))
        self.assertNotIn("FM_TEXT", combined)

    def test_no_fm_context_by_default(self) -> None:
        combined = _combine_context(self._ctx())
        self.assertNotIn("FM_TEXT", combined)

    def test_post_repair_replay_does_not_send_guidance_to_answerer(self) -> None:
        case = ProbeCase(
            case_id="c1",
            query="Where is key K?",
            raw_events=(),
            extracted_memory=(),
            gold_evidence=(
                GoldEvidence("ev1", "Key K resolves to PARIS", "m1"),
            ),
            gold_answer="PARIS",
            baseline_outputs=(
                BaselineOutput(
                    baseline_name="vector_memory",
                    answer="BERLIN",
                    retrieved_memory_ids=(),
                    answer_score=0.0,
                    evidence_score=0.0,
                    injected_context="old context",
                ),
            ),
        )
        captured_contexts: list[str] = []

        def agent_generate(_query: str, context: str) -> str:
            captured_contexts.append(context)
            return "PARIS"

        run_post_repair_context_replay(
            case,
            self._ctx(
                corrected_memory="EVIDENCE_TEXT PARIS",
                operator_metadata="OPERATOR_METADATA_SENTINEL",
                fm_context="FM_SENTINEL",
            ),
            agent_generate=agent_generate,
        )

        self.assertEqual(len(captured_contexts), 1)
        self.assertIn("EVIDENCE_TEXT", captured_contexts[0])
        self.assertNotIn("OPERATOR_METADATA_SENTINEL", captured_contexts[0])
        self.assertNotIn("FM_SENTINEL", captured_contexts[0])


class GoldAnswerLeakGuardTest(unittest.TestCase):
    def _ctx(self, corrected: str) -> RepairedContext:
        return RepairedContext(
            case_id="c1",
            corrected_memory=corrected,
            repaired_evidence_block=corrected,
            original_query="q",
            operator_metadata="OPERATOR_METADATA_TEXT",
        )

    def test_flags_when_gold_answer_present_verbatim(self) -> None:
        # Simulates any repaired memory that already contains the answer.
        ctx = self._ctx("Bridge key resolves to: road bike preference")
        self.assertTrue(detect_gold_answer_leak(ctx, "road bike preference"))

    def test_case_insensitive_match(self) -> None:
        ctx = self._ctx("The answer is ROAD BIKE.")
        self.assertTrue(detect_gold_answer_leak(ctx, "road bike"))

    def test_clean_when_gold_answer_absent(self) -> None:
        ctx = self._ctx("Generic repair guidance with no answer text.")
        self.assertFalse(detect_gold_answer_leak(ctx, "road bike preference"))

    def test_empty_gold_never_leaks(self) -> None:
        ctx = self._ctx("anything")
        self.assertFalse(detect_gold_answer_leak(ctx, "   "))


class ECSSchemaSemanticsTest(unittest.TestCase):
    def test_predicted_label_is_compatibility_alias_for_recovered_action(self) -> None:
        draft = ECSDraft(
            case_id="c1",
            predicted_label="retrieval_error",
            cause="Counterfactual action recovered the case.",
            corrected_memory="corrected",
            repair_guidance="guidance",
            repaired_evidence_block="corrected",
            recovery_delta=0.75,
        )

        self.assertEqual(draft.recovered_action, "retrieval_error")
        self.assertEqual(draft.recovery_delta, 0.75)

    def test_recovered_action_must_match_compatibility_label(self) -> None:
        with self.assertRaises(ValueError):
            ECSDraft(
                case_id="c1",
                predicted_label="retrieval_error",
                recovered_action="injection_error",
                cause="cause",
                corrected_memory="corrected",
                repair_guidance="guidance",
                repaired_evidence_block="corrected",
            )


if __name__ == "__main__":
    unittest.main()
