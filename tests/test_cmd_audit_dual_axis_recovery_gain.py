import unittest

from cmd_audit.attribution import assign_attribution_v1
from cmd_audit.harness import _apply_dual_axis_recovery_gain
from cmd_audit.core.models import (
    BaselineOutput,
    GoldEvidence,
    MemoryItem,
    ProbeCase,
    RawEvent,
)
from cmd_audit.replays import (
    ReplayResult,
    run_evidence_given_reasoning,
    run_oracle_write,
)


def _replay(
    replay_name: str,
    *,
    answer_score: float = 0.0,
    evidence_score: float = 0.0,
    recovery_gain: float = 0.0,
) -> ReplayResult:
    return ReplayResult(
        replay_name=replay_name,
        answer="",
        answer_score=answer_score,
        evidence_score=evidence_score,
        evidence_block="",
        recovery_gain=recovery_gain,
    )


class DualAxisRecoveryGainTest(unittest.TestCase):
    def test_evidence_axis_replay_uses_evidence_baseline(self) -> None:
        updated = _apply_dual_axis_recovery_gain(
            (_replay("oracle_retrieval", evidence_score=0.9),),
            baseline_evidence_llm=0.25,
            baseline_answer_llm=0.8,
        )

        self.assertAlmostEqual(updated[0].recovery_gain, 0.65)

    def test_reasoning_replay_uses_answer_baseline(self) -> None:
        updated = _apply_dual_axis_recovery_gain(
            (_replay("evidence_given_reasoning", answer_score=1.0),),
            baseline_evidence_llm=0.9,
            baseline_answer_llm=0.25,
        )

        self.assertAlmostEqual(updated[0].recovery_gain, 0.75)

    def test_attribution_fallback_triggers_when_only_reasoning_recovers(self) -> None:
        attribution = assign_attribution_v1(
            (
                _replay("oracle_retrieval", recovery_gain=0.0),
                _replay("oracle_write", recovery_gain=-0.1),
                _replay("evidence_given_reasoning", recovery_gain=0.7),
            ),
            positive_gain_threshold=0.0,
        )

        self.assertEqual(attribution.predicted_label, "reasoning_error")
        self.assertEqual(attribution.top_replay, "evidence_given_reasoning")
        self.assertEqual(attribution.close_deltas, (("reasoning_error", 0.0),))

    def test_attribution_fallback_skipped_when_top_replay_is_positive(self) -> None:
        attribution = assign_attribution_v1(
            (
                _replay("oracle_retrieval", recovery_gain=0.2),
                _replay("evidence_given_reasoning", recovery_gain=0.9),
            ),
            positive_gain_threshold=0.0,
        )

        self.assertEqual(attribution.predicted_label, "retrieval_error")
        self.assertEqual(attribution.top_replay, "oracle_retrieval")
        self.assertNotIn("reasoning_error", attribution.top_k_labels)


# ── Regression: replay answer-axis goes through AnswerVerifier ─────────


def _build_reasoning_case(
    *,
    baseline_evidence_score: float = 1.0,
    baseline_answer_score: float = 0.0,
    baseline_injected_context: str = "Lisbon is the capital of Portugal.",
    gold_answer: str = "Lisbon",
    baseline_answer: str = "I am not sure.",
) -> ProbeCase:
    """Probe case shaped to trigger evidence_given_reasoning."""
    return ProbeCase(
        case_id="regression-reasoning-case",
        query="Where is the capital of Portugal?",
        raw_events=(),
        extracted_memory=(
            MemoryItem(
                memory_id="m1",
                text=baseline_injected_context,
                store="episodic",
            ),
        ),
        gold_evidence=(
            GoldEvidence(
                evidence_id="ev1",
                text=baseline_injected_context,
                source_memory_id="m1",
            ),
        ),
        gold_answer=gold_answer,
        baseline_outputs=(
            BaselineOutput(
                baseline_name="primary",
                answer=baseline_answer,
                retrieved_memory_ids=("m1",),
                answer_score=baseline_answer_score,
                evidence_score=baseline_evidence_score,
                injected_context=baseline_injected_context,
            ),
        ),
        has_ingestion_trace=False,
    )


class _StubAnswerVerifier:
    """Returns scripted EQUIVALENT/NOT_EQUIVALENT verdicts per ANSWER text."""

    def __init__(self, equivalents: tuple[str, ...]) -> None:
        self._equivalents = equivalents
        self.calls: list[tuple[str, str]] = []

    def verify(self, answer: str, gold_answer: str) -> str:
        self.calls.append((answer, gold_answer))
        for eq in self._equivalents:
            if eq.lower() in answer.lower():
                return "EQUIVALENT"
        return "NOT_EQUIVALENT"


class ReplayAnswerAxisVerifierSymmetryTest(unittest.TestCase):
    """Replay answer_score must go through the same verifier as the baseline.

    Before this fix the baseline path used AnswerVerifier (LLM-judged) while
    the replay path used substring answer_score().  On the answer axis (the
    evidence_given_reasoning replay) that asymmetry zeroed out the recovery
    gain whenever the LLM rephrased the gold answer in a way the substring
    matcher rejected — which is most of the time.
    """

    def test_evidence_given_reasoning_uses_verifier_not_substring(self) -> None:
        # Agent paraphrases gold "Lisbon" as "The capital is Lisbon city."
        # Substring answer_score treats this as 0.0 (case-folded exact match
        # only). The verifier judges it EQUIVALENT → 1.0.
        case = _build_reasoning_case(
            gold_answer="lisbon",  # all-lowercase to make the casefold gap visible
        )
        agent_response = "The capital is Lisbon city."

        def stub_agent(query, context):
            return agent_response

        verifier = _StubAnswerVerifier(equivalents=("lisbon",))
        replay = run_evidence_given_reasoning(
            case,
            agent_generate=stub_agent,
            answer_verifier=verifier,
        )

        self.assertEqual(replay.answer, agent_response)
        self.assertEqual(replay.answer_score, 1.0)
        self.assertEqual(len(verifier.calls), 1)
        self.assertEqual(verifier.calls[0][1], "lisbon")

    def test_falls_back_to_substring_when_no_verifier(self) -> None:
        case = _build_reasoning_case(gold_answer="lisbon")
        # An exact substring match — keeps the legacy behaviour intact.
        agent_response = "lisbon"

        def stub_agent(query, context):
            return agent_response

        replay = run_evidence_given_reasoning(
            case,
            agent_generate=stub_agent,
            answer_verifier=None,
        )

        self.assertEqual(replay.answer_score, 1.0)

    def test_substring_zero_but_verifier_one_breaks_asymmetry(self) -> None:
        case = _build_reasoning_case(gold_answer="lisbon")
        agent_response = "The Portuguese capital is Lisbon."

        def stub_agent(query, context):
            return agent_response

        # Without verifier — substring miss, replay.answer_score = 0
        no_verifier = run_evidence_given_reasoning(
            case, agent_generate=stub_agent, answer_verifier=None
        )

        # With verifier — verdict EQUIVALENT, replay.answer_score = 1
        with_verifier = run_evidence_given_reasoning(
            case,
            agent_generate=stub_agent,
            answer_verifier=_StubAnswerVerifier(equivalents=("lisbon",)),
        )

        self.assertEqual(no_verifier.answer_score, 0.0)
        self.assertEqual(with_verifier.answer_score, 1.0)

    def test_oracle_write_replay_also_threads_verifier(self) -> None:
        """Smoke test: every leaf entry-point honours answer_verifier."""
        case = _build_reasoning_case(
            gold_answer="lisbon",
            baseline_evidence_score=0.0,
        )

        def stub_agent(query, context):
            return "The capital is Lisbon."

        replay = run_oracle_write(
            case,
            agent_generate=stub_agent,
            answer_verifier=_StubAnswerVerifier(equivalents=("lisbon",)),
        )

        self.assertEqual(replay.answer_score, 1.0)


if __name__ == "__main__":
    unittest.main()
