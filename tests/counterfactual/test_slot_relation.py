"""Public-contract tests for the text-only same-slot relation instrument."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import inspect

import pytest

from cmd_audit.counterfactual.slot_relation import (
    SLOT_RELATION_VERSION,
    CalibrationPair,
    PROMPT_TEMPLATE_SHA256,
    RelationType,
    RelationVerdict,
    judge_relation,
    parse_judge_response,
    planted_canary_recall,
    relation_prompt,
    style_permutation_false_positive_rate,
)


EARLY = "I try to keep a consistent schedule, so I'm usually in bed by 10:30 PM."
LATE = "My bakery alarm goes off at 3:45, so I prepare everything the night before."
UNRELATED = "I am choosing a coffee table for my living room."


class RecordingJudge:
    def __init__(self, response: str = '{"relation":"same_slot_different_value", "slot":"sleep"}'):
        self.response = response
        self.prompts: list[str] = []

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        self.prompts.append(prompt)
        return self.response


def test_prompt_is_text_only_and_removes_construction_prefixes():
    prompt = relation_prompt(f"M_old: {EARLY}", f"M_new: {LATE}").casefold()
    assert EARLY.casefold() in prompt and LATE.casefold() in prompt
    for forbidden in ("m_old", "m_new", "memory_id", "item id", "store", "rank", "older", "newer", "stale", "current"):
        assert forbidden not in prompt
    assert list(inspect.signature(judge_relation).parameters)[:2] == ["left_text", "right_text"]


def test_positive_negative_and_unparseable_responses_have_closed_semantics():
    positive = parse_judge_response('{"relation":"same_slot_different_value", "slot":"sleep schedule"}')
    assert positive.relation is RelationType.SAME_SLOT_DIFFERENT_VALUE
    assert positive.slot == "sleep schedule"
    assert not positive.abstained
    assert parse_judge_response('{"relation":"unrelated"}').relation is RelationType.UNRELATED
    slot_optional = parse_judge_response('{"relation":"same_slot_different_value"}')
    assert slot_optional.relation is RelationType.SAME_SLOT_DIFFERENT_VALUE
    assert slot_optional.slot is None
    for broken in (
        "",
        "not json",
        '{"relation":"maybe"}',
        'prefix {"relation":"unrelated"}',
        '{"relation":"unrelated"} suffix',
        '{"relation":"unrelated", "unexpected": true}',
        '[{"relation":"unrelated"}]',
    ):
        verdict = parse_judge_response(broken)
        assert verdict.relation is RelationType.UNCERTAIN
        assert verdict.abstained


def test_relation_is_symmetric_and_transport_failure_is_uncertain():
    judge = RecordingJudge()
    forward = judge_relation(EARLY, LATE, judge=judge, model_id="test-model")
    backward = judge_relation(LATE, EARLY, judge=judge, model_id="test-model")
    assert forward.relation is RelationType.SAME_SLOT_DIFFERENT_VALUE
    assert backward.relation is RelationType.SAME_SLOT_DIFFERENT_VALUE
    assert forward.slot == backward.slot == "sleep"
    assert forward.prompt_sha256 == backward.prompt_sha256 == PROMPT_TEMPLATE_SHA256

    class FailingJudge:
        def generate(self, prompt: str, *, system: str | None = None) -> str:
            raise RuntimeError("transport failure")

    failed = judge_relation(EARLY, LATE, judge=FailingJudge(), model_id="test-model")
    assert failed.relation is RelationType.UNCERTAIN
    assert failed.abstained


def test_verdict_is_frozen_and_includes_measurement_versions():
    verdict = RelationVerdict(
        relation=RelationType.UNRELATED,
        slot=None,
        abstained=False,
        prompt_sha256="a" * 64,
        parser_version="parser-v1",
        model_id="model-v1",
    )
    with pytest.raises(FrozenInstanceError):
        verdict.slot = "location"  # type: ignore[misc]
    assert SLOT_RELATION_VERSION


def test_calibration_helpers_support_style_permutation_and_planted_canary_fixtures():
    judge = RecordingJudge()
    permutations = (
        CalibrationPair("a", "case-1", "case-2", "M_old: I prefer tea.", "M_new: The workshop starts at noon."),
        CalibrationPair("b", "case-2", "case-1", "M_new: I prefer tea.", "M_old: The workshop starts at noon."),
    )
    assert style_permutation_false_positive_rate(permutations, judge=judge, model_id="test") == 1.0

    canaries = (
        CalibrationPair("c1", "canary-1", "canary-1", "I am vegetarian.", "I now eat meat.", expected_relation=True),
        CalibrationPair("c2", "canary-2", "canary-2", "I work remotely.", "My dog likes tennis balls.", expected_relation=False),
    )
    assert planted_canary_recall(canaries, judge=judge, model_id="test") == 1.0

    with pytest.raises(ValueError):
        style_permutation_false_positive_rate(canaries, judge=judge, model_id="test")

    with pytest.raises(ValueError):
        style_permutation_false_positive_rate(
            (CalibrationPair("same", "case", "case", "M_old: A", "M_new: B"),),
            judge=judge,
            model_id="test",
        )
