"""Repair executor context-composition contract (Phase 1 pivot).

The executor is fixed to `corrected_memory + guidance`. The repaired evidence
block must NOT be re-injected as a second copy: in both production callers it
duplicates corrected_memory, so re-emitting it only adds a repetition/recency
artifact that would inflate recovery for the wrong reason.
"""

import unittest

from cmd_audit.repair import ECSDraft
from cmd_audit.repair.post_repair import (
    RepairedContext,
    _combine_context,
    detect_gold_answer_leak,
)


class CombineContextExecutorTest(unittest.TestCase):
    def _ctx(self, **kw) -> RepairedContext:
        base = dict(
            case_id="c1",
            corrected_memory="EVIDENCE_TEXT",
            repair_guidance="GUIDANCE_TEXT",
            repaired_evidence_block="EVIDENCE_TEXT",
            original_query="q",
        )
        base.update(kw)
        return RepairedContext(**base)

    def test_combined_contains_corrected_memory_and_guidance(self) -> None:
        combined = _combine_context(self._ctx())
        self.assertIn("EVIDENCE_TEXT", combined)
        self.assertIn("GUIDANCE_TEXT", combined)

    def test_duplicate_evidence_block_not_reinjected(self) -> None:
        # corrected_memory == repaired_evidence_block (the production case).
        # The evidence text must appear exactly once, not twice.
        combined = _combine_context(self._ctx())
        self.assertEqual(combined.count("EVIDENCE_TEXT"), 1)

    def test_fm_context_appended_when_present(self) -> None:
        combined = _combine_context(self._ctx(fm_context="FM_TEXT"))
        self.assertIn("FM_TEXT", combined)

    def test_no_fm_context_by_default(self) -> None:
        combined = _combine_context(self._ctx())
        self.assertNotIn("FM_TEXT", combined)


class GoldAnswerLeakGuardTest(unittest.TestCase):
    def _ctx(self, corrected: str) -> RepairedContext:
        return RepairedContext(
            case_id="c1",
            corrected_memory=corrected,
            repair_guidance="GUIDANCE_TEXT",
            repaired_evidence_block=corrected,
            original_query="q",
        )

    def test_flags_when_gold_answer_present_verbatim(self) -> None:
        # Simulates the _replay_for_label fallback: evidence_block fabricated
        # from gold and flowed into corrected_memory -> answer is in context.
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
