"""A text-only, direction-free instrument for same-slot different-value pairs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Iterable, Protocol

from .relation_cache import (
    NORMALIZATION_VERSION,
    RelationCache,
    RelationCacheKey,
    canonical_text,
)

SLOT_RELATION_VERSION = "route-a-relation-instrument-v1"
PARSER_VERSION = "slot-relation-json-v1"
PROMPT_TEMPLATE = (
    "Assess the semantic relationship between the two statements below. "
    "Return exactly one JSON object with relation equal to either "
    '\"same_slot_different_value\" or \"unrelated\". Include a short slot only '
    "when the relation is same_slot_different_value."
)
PROMPT_TEMPLATE_SHA256 = hashlib.sha256(PROMPT_TEMPLATE.encode("utf-8")).hexdigest()


class RelationType(str, Enum):
    UNRELATED = "unrelated"
    SAME_SLOT_DIFFERENT_VALUE = "same_slot_different_value"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class RelationVerdict:
    relation: RelationType
    slot: str | None
    abstained: bool
    prompt_sha256: str
    parser_version: str
    model_id: str


@dataclass(frozen=True)
class CalibrationPair:
    """A pure-data fixture for permutation and planted-canary calibration."""

    pair_id: str
    left_case_id: str
    right_case_id: str
    left_text: str
    right_text: str
    expected_relation: bool = False


class TextJudge(Protocol):
    def generate(self, prompt: str, *, system: str | None = None) -> str: ...


def relation_prompt(left_text: str, right_text: str) -> str:
    """Build a symmetric prompt from text alone, excluding direction signals."""
    left, right = sorted((canonical_text(left_text), canonical_text(right_text)))
    return f"{PROMPT_TEMPLATE}\nStatement A: {left}\nStatement B: {right}"


def _instrument_verdict(
    relation: RelationType, *, slot: str | None, abstained: bool,
    prompt_sha256: str, model_id: str,
) -> RelationVerdict:
    return RelationVerdict(
        relation=relation, slot=slot, abstained=abstained,
        prompt_sha256=prompt_sha256, parser_version=PARSER_VERSION, model_id=model_id,
    )


def parse_judge_response(
    response: str, *, prompt_sha256: str = "", model_id: str = ""
) -> RelationVerdict:
    """Parse one closed-schema JSON verdict; anything else is uncertainty."""
    try:
        parsed = json.loads(response)
        if not isinstance(parsed, dict):
            raise TypeError("verdict must be an object")
        if set(parsed) - {"relation", "slot"}:
            raise ValueError("verdict carries unregistered fields")
        relation = RelationType(parsed["relation"])
        if relation is RelationType.UNCERTAIN:
            raise ValueError("judge cannot return uncertain as a positive measurement")
        slot = parsed.get("slot")
        if relation is RelationType.SAME_SLOT_DIFFERENT_VALUE:
            if slot is not None:
                if not isinstance(slot, str) or not slot.strip():
                    raise ValueError("slot must be a non-empty string when present")
                slot = slot.strip()
        elif slot is not None:
            raise ValueError("unrelated relation must not carry a slot")
        return _instrument_verdict(relation, slot=slot, abstained=False, prompt_sha256=prompt_sha256, model_id=model_id)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return _instrument_verdict(RelationType.UNCERTAIN, slot=None, abstained=True, prompt_sha256=prompt_sha256, model_id=model_id)


def judge_relation(
    left_text: str,
    right_text: str,
    *,
    judge: TextJudge,
    cache: RelationCache | None = None,
    model_id: str = "unspecified",
    model_config_hash: str = "unspecified",
) -> RelationVerdict:
    """Measure one unordered text pair, fail closed, and optionally cache it."""
    prompt = relation_prompt(left_text, right_text)
    key = RelationCacheKey.build(
        left_text, right_text, prompt_sha256=PROMPT_TEMPLATE_SHA256,
        parser_version=PARSER_VERSION, model_id=model_id,
        model_config_hash=model_config_hash,
        normalization_version=NORMALIZATION_VERSION,
        instrument_version=SLOT_RELATION_VERSION,
    )
    def measure() -> RelationVerdict:
        if not canonical_text(left_text) or not canonical_text(right_text):
            return _instrument_verdict(RelationType.UNCERTAIN, slot=None, abstained=True, prompt_sha256=PROMPT_TEMPLATE_SHA256, model_id=model_id)
        try:
            return parse_judge_response(judge.generate(prompt), prompt_sha256=PROMPT_TEMPLATE_SHA256, model_id=model_id)
        except Exception:
            return _instrument_verdict(RelationType.UNCERTAIN, slot=None, abstained=True, prompt_sha256=PROMPT_TEMPLATE_SHA256, model_id=model_id)

    return cache.resolve(key, measure) if cache is not None else measure()


def style_permutation_false_positive_rate(
    pairs: Iterable[CalibrationPair], *, judge: TextJudge, model_id: str, cache: RelationCache | None = None,
) -> float:
    """Rate at which style-only permutation controls are called relations."""
    rows = tuple(pairs)
    if any(row.expected_relation for row in rows):
        raise ValueError("style permutation controls must be negative relation pairs")
    if any(
        not row.left_case_id
        or not row.right_case_id
        or row.left_case_id == row.right_case_id
        for row in rows
    ):
        raise ValueError("style permutation controls must pair different cases")
    if not rows:
        return 0.0
    positives = sum(
        judge_relation(row.left_text, row.right_text, judge=judge, model_id=model_id, cache=cache).relation
        is RelationType.SAME_SLOT_DIFFERENT_VALUE
        for row in rows
    )
    return positives / len(rows)


def planted_canary_recall(
    pairs: Iterable[CalibrationPair], *, judge: TextJudge, model_id: str, cache: RelationCache | None = None,
) -> float:
    """Recall on explicitly marked semantic positive canaries, without a network call."""
    positives = [row for row in pairs if row.expected_relation]
    if not positives:
        return 0.0
    hits = sum(
        judge_relation(row.left_text, row.right_text, judge=judge, model_id=model_id, cache=cache).relation
        is RelationType.SAME_SLOT_DIFFERENT_VALUE
        for row in positives
    )
    return hits / len(positives)
