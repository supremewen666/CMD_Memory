"""Behavior tests for Issue 0019 Phase C: rubric-based continuous scoring."""

from __future__ import annotations

import math
import unittest

from cmd_audit.core.models import GoldEvidence
from cmd_audit.scoring.llm import (
    _expected_score_from_logprobs,
    _find_score_digit_logprobs,
    _parse_rubric_output,
)
from cmd_audit.core.llm_client import LLMResponse, TokenLogprob
from cmd_audit.scoring import ContextLeakError, RUBRIC_MAX_SCORE, RubricParseError, RubricScorer, RubricVerifier


class _FakeClient:
    """Records prompts and returns scripted responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def generate(self, prompt, *, system=None):
        self.calls.append((prompt, system))
        if not self._responses:
            raise RuntimeError("no scripted response left")
        return self._responses.pop(0)


class _ScoreByFactClient:
    """Returns a JSON rubric verdict keyed off the FACT line in the prompt."""

    def __init__(self, score_for):
        self._score_for = score_for
        self.calls = 0

    def generate(self, prompt, *, system=None):
        self.calls += 1
        # Prompt format: "FACT:\n  <fact>\n\nTEXT:\n  <text>" — match against
        # the FACT block only so TEXT contents can't confuse the dispatcher.
        fact_block, _, _ = prompt.partition("\n\nTEXT:")
        for fact, score in self._score_for.items():
            if fact in fact_block:
                return f'{{"reasoning": "matched {fact}", "score": {score}}}'
        return '{"reasoning": "no match", "score": 0}'


class _ExplodingClient:
    def generate(self, prompt, *, system=None):
        raise RuntimeError("network exploded")


# ── _parse_rubric_output ───────────────────────────────────────────────


class ParseRubricOutputTest(unittest.TestCase):
    def test_clean_json(self):
        score = _parse_rubric_output('{"reasoning": "ok", "score": 3}')
        self.assertEqual(score, 3)

    def test_score_only_no_reasoning(self):
        self.assertEqual(_parse_rubric_output('{"score": 0}'), 0)

    def test_score_max(self):
        self.assertEqual(
            _parse_rubric_output('{"reasoning": "x", "score": 4}'), 4
        )

    def test_extra_prose_around_json(self):
        raw = 'Sure, here is the verdict: {"reasoning": "y", "score": 2} done.'
        self.assertEqual(_parse_rubric_output(raw), 2)

    def test_score_as_string_coerces(self):
        self.assertEqual(
            _parse_rubric_output('{"reasoning": "z", "score": "1"}'), 1
        )

    def test_rejects_out_of_range_high(self):
        with self.assertRaises(RubricParseError):
            _parse_rubric_output('{"score": 5}')

    def test_rejects_out_of_range_low(self):
        with self.assertRaises(RubricParseError):
            _parse_rubric_output('{"score": -1}')

    def test_rejects_non_json(self):
        with self.assertRaises(RubricParseError):
            _parse_rubric_output("score is 3")

    def test_rejects_missing_score_field(self):
        with self.assertRaises(RubricParseError):
            _parse_rubric_output('{"reasoning": "no score key"}')

    def test_rejects_non_integer_score(self):
        with self.assertRaises(RubricParseError):
            _parse_rubric_output('{"score": "not-a-number"}')


# ── RubricVerifier ─────────────────────────────────────────────────────


class RubricVerifierTest(unittest.TestCase):
    def test_returns_int_in_range(self):
        client = _FakeClient(['{"reasoning": "exact", "score": 4}'])
        verifier = RubricVerifier(client)
        self.assertEqual(verifier.verify("fact", "fact"), 4)

    def test_unavailable_when_no_client(self):
        self.assertFalse(RubricVerifier(None).is_available)

    def test_no_client_returns_zero(self):
        self.assertEqual(RubricVerifier(None).verify("a", "b"), 0)

    def test_llm_failure_returns_zero(self):
        verifier = RubricVerifier(_ExplodingClient())
        self.assertEqual(verifier.verify("a", "b"), 0)

    def test_retries_on_parse_error(self):
        client = _FakeClient([
            "garbage no json",
            '{"reasoning": "ok", "score": 2}',
        ])
        verifier = RubricVerifier(client, max_retries=1)
        self.assertEqual(verifier.verify("fact", "text"), 2)
        self.assertEqual(len(client.calls), 2)

    def test_exhausted_retries_returns_zero(self):
        client = _FakeClient(["junk", "still junk"])
        verifier = RubricVerifier(client, max_retries=1)
        self.assertEqual(verifier.verify("fact", "text"), 0)

    def test_uses_rubric_system_prompt(self):
        client = _FakeClient(['{"score": 4}'])
        RubricVerifier(client).verify("fact", "text")
        _, system = client.calls[0]
        self.assertIn("0–4", system)
        self.assertIn("RUBRIC ANCHORS", system)

    def test_rejects_context_leak(self):
        client = _FakeClient(['{"score": 4}'])
        verifier = RubricVerifier(client)
        with self.assertRaises(ContextLeakError):
            verifier.verify("gold_label=write_error", "text")


# ── RubricScorer ───────────────────────────────────────────────────────


def _ev(eid, text):
    return GoldEvidence(evidence_id=eid, text=text, source_memory_id=None)


class RubricScorerTest(unittest.TestCase):
    def test_empty_evidence_returns_zero(self):
        scorer = RubricScorer(None)
        self.assertEqual(scorer.score_evidence((), "anything"), 0.0)

    def test_empty_text_returns_zero(self):
        scorer = RubricScorer(None)
        self.assertEqual(scorer.score_evidence((_ev("e1", "fact"),), ""), 0.0)

    def test_no_client_uses_phrase_fallback(self):
        scorer = RubricScorer(None)
        score = scorer.score_evidence((_ev("e1", "hello world"),), "hello world")
        self.assertEqual(score, 1.0)

    def test_no_client_with_explicit_fallback(self):
        called = {"n": 0}

        def fallback(_evidence, _text):
            called["n"] += 1
            return 0.42

        scorer = RubricScorer(None, fallback_scorer=fallback)
        score = scorer.score_evidence((_ev("e1", "fact"),), "text")
        self.assertEqual(score, 0.42)
        self.assertEqual(called["n"], 1)

    def test_single_evidence_full_score(self):
        client = _ScoreByFactClient({"capital of Portugal": 4})
        scorer = RubricScorer(client)
        score = scorer.score_evidence(
            (_ev("e1", "capital of Portugal"),),
            "Lisbon is the capital and largest city of Portugal.",
        )
        self.assertEqual(score, 1.0)

    def test_single_evidence_partial_score(self):
        client = _ScoreByFactClient({"capital of Portugal": 2})
        scorer = RubricScorer(client)
        score = scorer.score_evidence(
            (_ev("e1", "capital of Portugal"),),
            "Lisbon is mentioned somewhere.",
        )
        self.assertAlmostEqual(score, 0.5)

    def test_mixed_evidence_averages_normalised_scores(self):
        client = _ScoreByFactClient({"fact one": 4, "fact two": 1})
        scorer = RubricScorer(client)
        score = scorer.score_evidence(
            (_ev("e1", "fact one"), _ev("e2", "fact two")),
            "fact one full match, fact two faint allusion",
        )
        # (4/4 + 1/4) / 2 = 0.625
        self.assertAlmostEqual(score, 0.625)

    def test_scores_are_continuous_not_binary(self):
        client = _ScoreByFactClient({"fact one": 3})
        scorer = RubricScorer(client)
        score = scorer.score_evidence((_ev("e1", "fact one"),), "paraphrase")
        self.assertAlmostEqual(score, 0.75)
        self.assertNotIn(score, (0.0, 1.0))

    def test_callable_contract_matches_score_evidence(self):
        client = _ScoreByFactClient({"fact one": 3})
        scorer = RubricScorer(client)
        ev = (_ev("e1", "fact one"),)
        self.assertEqual(scorer(ev, "text fact one"), scorer.score_evidence(ev, "text fact one"))

    def test_returns_float_in_zero_one(self):
        client = _ScoreByFactClient({"f": 4})
        scorer = RubricScorer(client)
        score = scorer.score_evidence((_ev("e1", "f"),), "f")
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_per_item_failure_treated_as_zero(self):
        class _FlakyClient:
            def __init__(self):
                self._n = 0

            def generate(self, prompt, *, system=None):
                self._n += 1
                if "fact two" in prompt:
                    raise RuntimeError("boom")
                return '{"reasoning": "ok", "score": 4}'

        scorer = RubricScorer(_FlakyClient())
        score = scorer.score_evidence(
            (_ev("e1", "fact one"), _ev("e2", "fact two")),
            "irrelevant",
        )
        # fact one → 4/4 = 1.0, fact two → 0 (LLM failed) → mean 0.5
        self.assertAlmostEqual(score, 0.5)


# ── Logprob expectation (G-Eval style) ─────────────────────────────────


def _tlp(token, logprob, alts=()):
    return TokenLogprob(token=token, logprob=logprob, alternatives=tuple(alts))


def _build_score_stream(score_token_logprob, alternatives):
    """Mimic a vLLM-style token stream ending in JSON `"score": <digit>}`."""
    return (
        _tlp("{", math.log(0.99)),
        _tlp('"', math.log(0.99)),
        _tlp("score", math.log(0.99)),
        _tlp('"', math.log(0.99)),
        _tlp(":", math.log(0.99)),
        _tlp(" ", math.log(0.99)),
        _tlp("3", score_token_logprob, alternatives),
        _tlp("}", math.log(0.99)),
    )


class FindScoreDigitLogprobsTest(unittest.TestCase):
    def test_returns_none_when_empty(self):
        self.assertIsNone(_find_score_digit_logprobs(()))

    def test_returns_none_when_no_score_key(self):
        stream = (_tlp("hello", -0.1), _tlp("3", -0.5))
        self.assertIsNone(_find_score_digit_logprobs(stream))

    def test_finds_digit_with_alternatives(self):
        alts = [
            ("3", math.log(0.6)),
            ("4", math.log(0.25)),
            ("2", math.log(0.10)),
            ("1", math.log(0.04)),
            ("0", math.log(0.01)),
        ]
        stream = _build_score_stream(math.log(0.6), alts)
        digits = _find_score_digit_logprobs(stream)
        self.assertIsNotNone(digits)
        self.assertEqual(set(digits.keys()), {0, 1, 2, 3, 4})
        self.assertAlmostEqual(digits[3], math.log(0.6))

    def test_chosen_digit_wins_over_alternatives_with_same_token(self):
        # Alt list also has "3" at a different logprob — chosen token's
        # logprob takes precedence (setdefault behaviour).
        alts = [("3", -99.0), ("4", math.log(0.25))]
        stream = _build_score_stream(math.log(0.6), alts)
        digits = _find_score_digit_logprobs(stream)
        self.assertAlmostEqual(digits[3], math.log(0.6))
        self.assertAlmostEqual(digits[4], math.log(0.25))

    def test_ignores_alternatives_outside_rubric(self):
        alts = [("3", math.log(0.6)), ("9", math.log(0.3)), ("hello", -1.0)]
        stream = _build_score_stream(math.log(0.6), alts)
        digits = _find_score_digit_logprobs(stream)
        self.assertEqual(set(digits.keys()), {3})

    def test_finds_digit_when_vllm_token_contains_json_punctuation(self):
        alts = [(" 3}", math.log(0.6)), (" 4}", math.log(0.25))]
        stream = (
            _tlp('"score"', math.log(0.99)),
            _tlp(":", math.log(0.99)),
            _tlp(" 3}", math.log(0.6), alts),
        )
        digits = _find_score_digit_logprobs(stream)

        self.assertEqual(set(digits.keys()), {3, 4})
        self.assertAlmostEqual(digits[3], math.log(0.6))

    def test_returns_none_when_chosen_token_is_word(self):
        # Score got emitted as multi-char fragment (e.g. "three") — bail.
        stream = (
            _tlp('"', math.log(0.99)),
            _tlp("score", math.log(0.99)),
            _tlp('"', math.log(0.99)),
            _tlp(":", math.log(0.99)),
            _tlp(" three", math.log(0.99)),
        )
        self.assertIsNone(_find_score_digit_logprobs(stream))


class ExpectedScoreFromLogprobsTest(unittest.TestCase):
    def test_single_certain_digit(self):
        digits = {3: 0.0}  # logprob 0 = probability 1
        self.assertAlmostEqual(_expected_score_from_logprobs(digits), 3.0)

    def test_uniform_distribution(self):
        # Equal logprobs over 0..4 → uniform → mean = 2.0
        digits = {i: 0.0 for i in range(5)}
        self.assertAlmostEqual(_expected_score_from_logprobs(digits), 2.0)

    def test_two_digit_split(self):
        # 70% on 4, 30% on 2 → 4*0.7 + 2*0.3 = 3.4
        digits = {4: math.log(0.7), 2: math.log(0.3)}
        self.assertAlmostEqual(_expected_score_from_logprobs(digits), 3.4, places=4)

    def test_returns_float(self):
        digits = {3: math.log(0.6), 4: math.log(0.4)}
        self.assertIsInstance(_expected_score_from_logprobs(digits), float)

    def test_empty_digits_raises(self):
        with self.assertRaises(ValueError):
            _expected_score_from_logprobs({})

    def test_numerical_stability_with_large_negatives(self):
        # Logprobs around -1000 — log-sum-exp must not overflow/underflow.
        digits = {3: -1000.0, 4: -1001.0}
        result = _expected_score_from_logprobs(digits)
        self.assertGreater(result, 3.0)
        self.assertLess(result, 4.0)


# ── RubricVerifier.verify_continuous ───────────────────────────────────


class _LogprobClient:
    """Fake client implementing both generate and generate_with_logprobs."""

    def __init__(self, response):
        self._response = response
        self.calls = 0

    def generate(self, prompt, *, system=None):
        # Discrete fallback path — emit JSON consistent with the chosen digit.
        if isinstance(self._response, LLMResponse):
            return self._response.text
        return self._response

    def generate_with_logprobs(self, prompt, *, system=None, top_logprobs=10):
        self.calls += 1
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _DiscreteOnlyClient:
    """Client without generate_with_logprobs — forces discrete fallback."""

    def generate(self, prompt, *, system=None):
        return '{"reasoning": "x", "score": 2}'


class VerifyContinuousTest(unittest.TestCase):
    def test_returns_expectation_when_logprobs_present(self):
        alts = [("3", math.log(0.7)), ("4", math.log(0.3))]
        stream = _build_score_stream(math.log(0.7), alts)
        response = LLMResponse(
            text='{"reasoning": "ok", "score": 3}',
            token_logprobs=stream,
        )
        verifier = RubricVerifier(_LogprobClient(response))
        result = verifier.verify_continuous("fact", "text")
        self.assertAlmostEqual(result, 3.3, places=4)

    def test_falls_back_to_discrete_when_no_logprobs(self):
        response = LLMResponse(
            text='{"reasoning": "ok", "score": 2}',
            token_logprobs=None,
        )
        verifier = RubricVerifier(_LogprobClient(response))
        result = verifier.verify_continuous("fact", "text")
        self.assertEqual(result, 2.0)

    def test_falls_back_when_client_lacks_logprob_method(self):
        verifier = RubricVerifier(_DiscreteOnlyClient())
        result = verifier.verify_continuous("fact", "text")
        self.assertEqual(result, 2.0)

    def test_returns_zero_when_client_none(self):
        verifier = RubricVerifier(None)
        self.assertEqual(verifier.verify_continuous("fact", "text"), 0.0)

    def test_logprob_call_failure_falls_back_to_discrete(self):
        client = _LogprobClient(RuntimeError("logprob endpoint exploded"))
        # Discrete generate still works on this client and returns valid JSON
        # via a side patch — simulate by giving the client a working generate.
        client.generate = lambda prompt, *, system=None: '{"score": 4}'
        verifier = RubricVerifier(client)
        result = verifier.verify_continuous("fact", "text")
        self.assertEqual(result, 4.0)

    def test_score_position_unparseable_falls_back(self):
        # Logprobs present but `_find_score_digit_logprobs` returns None
        # because the score key never appears in the stream.
        stream = (_tlp("hello", -0.1), _tlp("world", -0.2))
        response = LLMResponse(
            text='{"score": 1}', token_logprobs=stream
        )
        verifier = RubricVerifier(_LogprobClient(response))
        result = verifier.verify_continuous("fact", "text")
        self.assertEqual(result, 1.0)

    def test_continuous_score_in_valid_range(self):
        alts = [
            ("0", math.log(0.05)),
            ("1", math.log(0.10)),
            ("2", math.log(0.20)),
            ("3", math.log(0.40)),
            ("4", math.log(0.25)),
        ]
        stream = _build_score_stream(math.log(0.40), alts)
        response = LLMResponse(text='{"score": 3}', token_logprobs=stream)
        verifier = RubricVerifier(_LogprobClient(response))
        result = verifier.verify_continuous("fact", "text")
        self.assertGreaterEqual(result, 0.0)
        self.assertLessEqual(result, RUBRIC_MAX_SCORE)


# ── RubricScorer.score_continuous ──────────────────────────────────────


class ScoreContinuousTest(unittest.TestCase):
    def test_empty_evidence_returns_zero(self):
        scorer = RubricScorer(None)
        self.assertEqual(scorer.score_continuous((), "text"), 0.0)

    def test_empty_text_returns_zero(self):
        scorer = RubricScorer(None)
        self.assertEqual(
            scorer.score_continuous((_ev("e1", "fact"),), ""), 0.0
        )

    def test_no_client_uses_phrase_fallback(self):
        scorer = RubricScorer(None)
        score = scorer.score_continuous(
            (_ev("e1", "hello"),), "hello world"
        )
        self.assertEqual(score, 1.0)

    def test_aggregates_continuous_per_item_scores(self):
        alts = [("3", math.log(0.7)), ("4", math.log(0.3))]
        stream = _build_score_stream(math.log(0.7), alts)
        response = LLMResponse(text='{"score": 3}', token_logprobs=stream)
        scorer = RubricScorer(_LogprobClient(response))
        score = scorer.score_continuous((_ev("e1", "fact"),), "text")
        # E[score] = 3.3, normalised = 3.3/4 = 0.825
        self.assertAlmostEqual(score, 0.825, places=4)

    def test_falls_back_to_discrete_when_logprobs_missing(self):
        response = LLMResponse(text='{"score": 2}', token_logprobs=None)
        scorer = RubricScorer(_LogprobClient(response))
        score = scorer.score_continuous((_ev("e1", "fact"),), "text")
        self.assertAlmostEqual(score, 0.5)

    def test_returns_float_in_zero_one(self):
        alts = [("4", math.log(0.99)), ("3", math.log(0.01))]
        stream = _build_score_stream(math.log(0.99), alts)
        # Must override the chosen-token spot too — it's "3" by default.
        stream = stream[:6] + (_tlp("4", math.log(0.99), alts), stream[7])
        response = LLMResponse(text='{"score": 4}', token_logprobs=stream)
        scorer = RubricScorer(_LogprobClient(response))
        score = scorer.score_continuous((_ev("e1", "fact"),), "text")
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)


if __name__ == "__main__":
    unittest.main()
