from __future__ import annotations

import math

import pytest

from cmd_audit.core.llm_client import LLMResponse, TokenLogprob
from cmd_audit.eval.gold_free_observer import ProbeCoordinates
from experiments.arena_backends import (
    ReferenceFreeAnswerScorer,
    VLLMDualScoreArenaBackend,
    parse_reference_free_score,
)
from experiments.arena_runner_common import ArenaCase


class AnswerClient:
    def __init__(self):
        self.prompts = []

    def generate(self, prompt, *, system=None):
        self.prompts.append((prompt, system))
        return (
            "The repaired answer is grounded."
            if "missing grounded fact" in prompt
            else "Unknown"
        )


class JudgeClient:
    def __init__(self):
        self.prompts = []

    def generate(self, prompt, *, system=None):
        self.prompts.append((prompt, system))
        score = 4 if "repaired answer" in prompt else 0
        return f'{{"reasoning":"fixture", "score":{score}}}'


class ShadowVerifier:
    def __init__(self):
        self.calls = []

    def __call__(self, answer, gold_answer):
        self.calls.append((answer, gold_answer))
        assert gold_answer == "SECRET-GOLD-REFERENCE"
        return 1.0 if "repaired answer" in answer else 0.0


def _case():
    return ArenaCase(
        arena_id="fixture",
        case_id="case-1",
        family_id="family-1",
        failure_type="retrieval_error",
        base_context="Query: Where?\n\nRetrieved Memory:\nknown context",
        coordinates=ProbeCoordinates(),
        subset="fixture",
        raw={
            "query": "Where?",
            "raw_events": [],
            "extracted_memory": [
                {
                    "memory_id": "m-known",
                    "text": "known context",
                    "source_event_ids": [],
                    "store": "episodic",
                    "passed_safety_filter": False,
                },
                {
                    "memory_id": "m-missing",
                    "text": "missing grounded fact",
                    "source_event_ids": [],
                    "store": "episodic",
                    "passed_safety_filter": False,
                },
            ],
            "baseline_outputs": [
                {
                    "baseline_name": "fixed",
                    "answer": "Unknown",
                    "retrieved_memory_ids": ["m-known"],
                    "answer_score": 0.0,
                    "evidence_score": 0.0,
                    "injected_context": "known context",
                }
            ],
            "gold_answer": "SECRET-GOLD-REFERENCE",
        },
    )


def test_real_backend_executes_operator_and_isolates_reference_free_prompts():
    answer_client = AnswerClient()
    judge_client = JudgeClient()
    shadow = ShadowVerifier()
    backend = VLLMDualScoreArenaBackend(
        answer_client=answer_client,
        judge_client=judge_client,
        shadow_verifier=shadow,
        validate_endpoints=False,
    )
    case = _case()
    candidate_ids = {row.skill_id for row in backend.candidates(case)}
    assert not candidate_ids & {
        "seed:item_wrong",
        "seed:item_compression_distorted",
        "seed:item_poisoned",
    }
    retrieval = next(
        candidate
        for candidate in backend.candidates(case)
        if candidate.skill_id == "seed:retrieval_error"
    )
    result = backend.evaluate(
        case,
        retrieval,
        input_context=case.base_context,
        origin_context=case.base_context,
    )
    assert result.gold_free_gain == 1.0
    assert result.shadow_gold_gain == 1.0
    assert "missing grounded fact" in result.repaired_context
    assert shadow.calls
    assert all(
        "SECRET-GOLD-REFERENCE" not in prompt
        for prompt, _system in judge_client.prompts
    )
    assert all(
        "SECRET-GOLD-REFERENCE" not in prompt
        for prompt, _system in answer_client.prompts
    )
    # Cached baseline/repaired answers and scores make replay deterministic.
    before = (len(answer_client.prompts), len(judge_client.prompts), len(shadow.calls))
    assert backend.evaluate(
        case,
        retrieval,
        input_context=case.base_context,
        origin_context=case.base_context,
    ) == result
    assert before == (
        len(answer_client.prompts),
        len(judge_client.prompts),
        len(shadow.calls),
    )


def test_reference_free_parser_is_strict_and_bounded():
    assert parse_reference_free_score('{"reasoning":"ok","score":3}') == 3
    assert parse_reference_free_score('prefix {"score": 4} suffix') == 4
    assert parse_reference_free_score('{"score": 5}') is None
    assert parse_reference_free_score("FOUR") is None


def test_reference_free_scorer_uses_logprob_expectation_before_discrete_fallback():
    class ContinuousJudge:
        def __init__(self):
            self.discrete_calls = 0

        def generate_with_logprobs(self, prompt, *, system=None, top_logprobs=10):
            del prompt, system, top_logprobs
            return LLMResponse(
                text='{"reasoning":"fixture","score":3}',
                token_logprobs=(
                    TokenLogprob('"score"', -0.1),
                    TokenLogprob(":", -0.1),
                    TokenLogprob(
                        "3",
                        math.log(0.6),
                        (
                            ("2", math.log(0.3)),
                            ("4", math.log(0.1)),
                        ),
                    ),
                ),
            )

        def generate(self, prompt, *, system=None):
            del prompt, system
            self.discrete_calls += 1
            return '{"reasoning":"fallback","score":0}'

    judge = ContinuousJudge()
    score = ReferenceFreeAnswerScorer(judge).score(
        query="Where?",
        context="Grounded context",
        answer="Grounded answer",
    )
    assert score == pytest.approx((3 * 0.6 + 2 * 0.3 + 4 * 0.1) / 4)
    assert judge.discrete_calls == 0


def test_reference_free_scorer_falls_back_when_logprobs_are_missing():
    class StrippedJudge:
        def generate_with_logprobs(self, prompt, *, system=None, top_logprobs=10):
            del prompt, system, top_logprobs
            return LLMResponse(
                text='{"reasoning":"fixture","score":3}',
                token_logprobs=None,
            )

        def generate(self, prompt, *, system=None):
            del prompt, system
            return '{"reasoning":"fallback","score":3}'

    assert ReferenceFreeAnswerScorer(StrippedJudge()).score(
        query="Where?",
        context="Context",
        answer="Answer",
    ) == pytest.approx(0.75)


def test_shadow_failure_does_not_poison_runtime_gain():
    class BrokenShadow:
        def __call__(self, _answer, _gold):
            raise RuntimeError("shadow unavailable")

    backend = VLLMDualScoreArenaBackend(
        answer_client=AnswerClient(),
        judge_client=JudgeClient(),
        shadow_verifier=BrokenShadow(),
        validate_endpoints=False,
    )
    case = _case()
    candidate = next(
        row
        for row in backend.candidates(case)
        if row.skill_id == "seed:retrieval_error"
    )
    result = backend.evaluate(
        case,
        candidate,
        input_context=case.base_context,
        origin_context=case.base_context,
    )
    assert result.gold_free_gain == 1.0
    assert result.shadow_gold_gain is None
    assert "shadow_error" in result.status
