"""A text-only, direction-free instrument for same-slot different-value pairs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import json
import re
from typing import Iterable, Protocol

from .relation_cache import (
    NORMALIZATION_VERSION,
    RelationCache,
    RelationCacheKey,
    canonical_text,
)


SLOT_RELATION_VERSION = "route-a-relation-instrument-v2-structured-json"
PARSER_VERSION = "slot-relation-json-v2-single-object"
PROMPT_TEMPLATE = (
    "Assess the semantic relationship between the two statements below. "
    "Return exactly one JSON object with relation equal to either "
    '"same_slot_different_value" or "unrelated". Include a short slot only '
    "when the relation is same_slot_different_value. Return no explanation, "
    "additional fields, or Markdown."
)
PROMPT_TEMPLATE_SHA256 = hashlib.sha256(PROMPT_TEMPLATE.encode("utf-8")).hexdigest()
RELATION_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["relation"],
    "properties": {
        "relation": {
            "type": "string",
            "enum": ["same_slot_different_value", "unrelated"],
        },
        "slot": {"type": ["string", "null"]},
    },
}
RELATION_RESPONSE_SCHEMA_SHA256 = hashlib.sha256(
    json.dumps(
        RELATION_RESPONSE_SCHEMA,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
_FENCED_JSON = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n(?P<body>.*?)\r?\n```[ \t]*\Z",
    re.IGNORECASE | re.DOTALL,
)


class RelationType(str, Enum):
    UNRELATED = "unrelated"
    SAME_SLOT_DIFFERENT_VALUE = "same_slot_different_value"
    UNCERTAIN = "uncertain"


class RelationReason(str, Enum):
    ACCEPTED = "accepted"
    ACCEPTED_FENCED_JSON = "accepted_fenced_json"
    EMPTY_INPUT = "empty_input"
    MALFORMED_JSON = "malformed_json"
    INVALID_SCHEMA = "invalid_schema"
    TRANSPORT_ERROR = "transport_error"


@dataclass(frozen=True)
class RelationAttempt:
    attempt_index: int
    reason_code: RelationReason
    raw_response: str | None
    raw_response_sha256: str | None
    structured_output_used: bool


@dataclass(frozen=True)
class RelationVerdict:
    relation: RelationType
    slot: str | None
    abstained: bool
    prompt_sha256: str
    parser_version: str
    model_id: str
    reason_code: RelationReason = RelationReason.ACCEPTED
    raw_response_sha256: str | None = None
    attempts: tuple[RelationAttempt, ...] = ()


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
    relation: RelationType,
    *,
    slot: str | None,
    abstained: bool,
    prompt_sha256: str,
    model_id: str,
    reason_code: RelationReason,
    raw_response_sha256: str | None = None,
    attempts: tuple[RelationAttempt, ...] = (),
) -> RelationVerdict:
    return RelationVerdict(
        relation=relation,
        slot=slot,
        abstained=abstained,
        prompt_sha256=prompt_sha256,
        parser_version=PARSER_VERSION,
        model_id=model_id,
        reason_code=reason_code,
        raw_response_sha256=raw_response_sha256,
        attempts=attempts,
    )


def _raw_sha256(response: str) -> str:
    return hashlib.sha256(response.encode("utf-8")).hexdigest()


def parse_judge_response(
    response: str,
    *,
    prompt_sha256: str = "",
    model_id: str = "",
) -> RelationVerdict:
    """Parse one exact JSON object, tolerating only a single JSON code fence."""
    if not isinstance(response, str):
        return _instrument_verdict(
            RelationType.UNCERTAIN,
            slot=None,
            abstained=True,
            prompt_sha256=prompt_sha256,
            model_id=model_id,
            reason_code=RelationReason.MALFORMED_JSON,
        )
    raw_response_sha256 = _raw_sha256(response)
    fenced = _FENCED_JSON.fullmatch(response.strip())
    payload = fenced.group("body") if fenced else response
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return _instrument_verdict(
            RelationType.UNCERTAIN,
            slot=None,
            abstained=True,
            prompt_sha256=prompt_sha256,
            model_id=model_id,
            reason_code=RelationReason.MALFORMED_JSON,
            raw_response_sha256=raw_response_sha256,
        )
    try:
        if not isinstance(parsed, dict):
            raise TypeError("verdict must be an object")
        if set(parsed) - {"relation", "slot"}:
            raise ValueError("verdict carries unregistered fields")
        relation = RelationType(parsed["relation"])
        if relation is RelationType.UNCERTAIN:
            raise ValueError("judge cannot return uncertain")
        slot = parsed.get("slot")
        if relation is RelationType.SAME_SLOT_DIFFERENT_VALUE:
            if slot is not None:
                if not isinstance(slot, str) or not slot.strip():
                    raise ValueError("slot must be a non-empty string when present")
                slot = slot.strip()
        elif slot is not None:
            raise ValueError("unrelated relation must not carry a slot")
        return _instrument_verdict(
            relation,
            slot=slot,
            abstained=False,
            prompt_sha256=prompt_sha256,
            model_id=model_id,
            reason_code=(
                RelationReason.ACCEPTED_FENCED_JSON
                if fenced
                else RelationReason.ACCEPTED
            ),
            raw_response_sha256=raw_response_sha256,
        )
    except (ValueError, KeyError, TypeError):
        return _instrument_verdict(
            RelationType.UNCERTAIN,
            slot=None,
            abstained=True,
            prompt_sha256=prompt_sha256,
            model_id=model_id,
            reason_code=RelationReason.INVALID_SCHEMA,
            raw_response_sha256=raw_response_sha256,
        )


def judge_relation(
    left_text: str,
    right_text: str,
    *,
    judge: TextJudge,
    cache: RelationCache | None = None,
    model_id: str = "unspecified",
    model_config_hash: str = "unspecified",
    max_attempts: int = 3,
) -> RelationVerdict:
    """Measure one unordered text pair with structured output and bounded retry."""
    if (
        isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or max_attempts < 1
    ):
        raise ValueError("max_attempts must be a positive integer")
    prompt = relation_prompt(left_text, right_text)
    key = RelationCacheKey.build(
        left_text,
        right_text,
        prompt_sha256=PROMPT_TEMPLATE_SHA256,
        parser_version=PARSER_VERSION,
        model_id=model_id,
        model_config_hash=model_config_hash,
        normalization_version=NORMALIZATION_VERSION,
        instrument_version=SLOT_RELATION_VERSION,
    )

    def measure() -> RelationVerdict:
        if not canonical_text(left_text) or not canonical_text(right_text):
            return _instrument_verdict(
                RelationType.UNCERTAIN,
                slot=None,
                abstained=True,
                prompt_sha256=PROMPT_TEMPLATE_SHA256,
                model_id=model_id,
                reason_code=RelationReason.EMPTY_INPUT,
            )
        attempts: list[RelationAttempt] = []
        structured_generate = getattr(judge, "generate_json", None)
        structured_output_used = callable(structured_generate)
        verdict: RelationVerdict | None = None
        for attempt_index in range(1, max_attempts + 1):
            try:
                if structured_output_used:
                    response = structured_generate(
                        prompt,
                        schema=RELATION_RESPONSE_SCHEMA,
                        schema_name="slot_relation",
                    )
                else:
                    response = judge.generate(prompt)
                verdict = parse_judge_response(
                    response,
                    prompt_sha256=PROMPT_TEMPLATE_SHA256,
                    model_id=model_id,
                )
                attempts.append(
                    RelationAttempt(
                        attempt_index=attempt_index,
                        reason_code=verdict.reason_code,
                        raw_response=response,
                        raw_response_sha256=verdict.raw_response_sha256,
                        structured_output_used=structured_output_used,
                    )
                )
            except Exception:
                verdict = _instrument_verdict(
                    RelationType.UNCERTAIN,
                    slot=None,
                    abstained=True,
                    prompt_sha256=PROMPT_TEMPLATE_SHA256,
                    model_id=model_id,
                    reason_code=RelationReason.TRANSPORT_ERROR,
                )
                attempts.append(
                    RelationAttempt(
                        attempt_index=attempt_index,
                        reason_code=RelationReason.TRANSPORT_ERROR,
                        raw_response=None,
                        raw_response_sha256=None,
                        structured_output_used=structured_output_used,
                    )
                )
            if not verdict.abstained:
                return replace(verdict, attempts=tuple(attempts))
        if verdict is None:  # pragma: no cover
            raise AssertionError("relation instrument made no attempts")
        return replace(verdict, attempts=tuple(attempts))

    return (
        cache.resolve(key, measure, cache_if=lambda verdict: not verdict.abstained)
        if cache is not None
        else measure()
    )


def style_permutation_false_positive_rate(
    pairs: Iterable[CalibrationPair],
    *,
    judge: TextJudge,
    model_id: str,
    cache: RelationCache | None = None,
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
        judge_relation(
            row.left_text,
            row.right_text,
            judge=judge,
            model_id=model_id,
            cache=cache,
        ).relation
        is RelationType.SAME_SLOT_DIFFERENT_VALUE
        for row in rows
    )
    return positives / len(rows)


def planted_canary_recall(
    pairs: Iterable[CalibrationPair],
    *,
    judge: TextJudge,
    model_id: str,
    cache: RelationCache | None = None,
) -> float:
    """Recall on explicitly marked semantic positive canaries."""
    positives = [row for row in pairs if row.expected_relation]
    if not positives:
        return 0.0
    hits = sum(
        judge_relation(
            row.left_text,
            row.right_text,
            judge=judge,
            model_id=model_id,
            cache=cache,
        ).relation
        is RelationType.SAME_SLOT_DIFFERENT_VALUE
        for row in positives
    )
    return hits / len(positives)
