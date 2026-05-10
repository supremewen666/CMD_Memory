"""Probe-case contract for CMD-Audit V0."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .labels import validate_v0_label


class ProbeCaseError(ValueError):
    """Raised when a probe case does not satisfy the V0 contract."""


@dataclass(frozen=True)
class RawEvent:
    event_id: str
    text: str

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "RawEvent":
        return cls(event_id=_required_str(value, "event_id"), text=_required_str(value, "text"))


@dataclass(frozen=True)
class MemoryItem:
    memory_id: str
    text: str
    source_event_ids: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "MemoryItem":
        return cls(
            memory_id=_required_str(value, "memory_id"),
            text=_required_str(value, "text"),
            source_event_ids=tuple(value.get("source_event_ids", ())),
        )


@dataclass(frozen=True)
class GoldEvidence:
    evidence_id: str
    text: str
    source_memory_id: str | None = None
    source_event_id: str | None = None
    required_phrases: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "GoldEvidence":
        return cls(
            evidence_id=_required_str(value, "evidence_id"),
            text=_required_str(value, "text"),
            source_memory_id=value.get("source_memory_id"),
            source_event_id=value.get("source_event_id"),
            required_phrases=tuple(value.get("required_phrases", ())),
        )


@dataclass(frozen=True)
class BaselineOutput:
    baseline_name: str
    answer: str
    retrieved_memory_ids: tuple[str, ...]
    answer_score: float
    evidence_score: float
    injected_context: str = ""

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "BaselineOutput":
        return cls(
            baseline_name=_required_str(value, "baseline_name"),
            answer=_required_str(value, "answer"),
            retrieved_memory_ids=tuple(value.get("retrieved_memory_ids", ())),
            answer_score=float(value.get("answer_score", 0.0)),
            evidence_score=float(value.get("evidence_score", 0.0)),
            injected_context=str(value.get("injected_context", "")),
        )


@dataclass(frozen=True)
class ScoringSpec:
    answer_metric: str = "casefold_exact_match"
    evidence_metric: str = "gold_evidence_recall"

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "ScoringSpec":
        if value is None:
            return cls()
        return cls(
            answer_metric=str(value.get("answer_metric", cls.answer_metric)),
            evidence_metric=str(value.get("evidence_metric", cls.evidence_metric)),
        )


@dataclass(frozen=True)
class ProbeCase:
    """One labeled memory-failure case.

    The contract keeps raw events, extracted memory, gold evidence, baseline output,
    and perturbation label separate so replay deltas can identify the failed memory
    operation rather than simply restating a wrong final answer.
    """

    case_id: str
    query: str
    raw_events: tuple[RawEvent, ...]
    extracted_memory: tuple[MemoryItem, ...]
    gold_evidence: tuple[GoldEvidence, ...]
    gold_answer: str
    baseline_outputs: tuple[BaselineOutput, ...]
    perturbation_label: str
    scoring: ScoringSpec

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "ProbeCase":
        case = cls(
            case_id=_required_str(value, "case_id"),
            query=_required_str(value, "query"),
            raw_events=tuple(RawEvent.from_mapping(item) for item in value.get("raw_events", ())),
            extracted_memory=tuple(
                MemoryItem.from_mapping(item) for item in value.get("extracted_memory", ())
            ),
            gold_evidence=tuple(
                GoldEvidence.from_mapping(item) for item in value.get("gold_evidence", ())
            ),
            gold_answer=_required_str(value, "gold_answer"),
            baseline_outputs=tuple(
                BaselineOutput.from_mapping(item) for item in value.get("baseline_outputs", ())
            ),
            perturbation_label=validate_v0_label(_required_str(value, "perturbation_label")),
            scoring=ScoringSpec.from_mapping(value.get("scoring")),
        )
        case.validate()
        return case

    @property
    def primary_baseline(self) -> BaselineOutput:
        return self.baseline_outputs[0]

    def validate(self) -> None:
        if not self.raw_events:
            raise ProbeCaseError(f"{self.case_id}: raw_events must not be empty")
        if not self.extracted_memory:
            raise ProbeCaseError(f"{self.case_id}: extracted_memory must not be empty")
        if not self.gold_evidence:
            raise ProbeCaseError(f"{self.case_id}: gold_evidence must not be empty")
        if not self.baseline_outputs:
            raise ProbeCaseError(f"{self.case_id}: baseline_outputs must not be empty")

        memory_ids = {item.memory_id for item in self.extracted_memory}
        event_ids = {event.event_id for event in self.raw_events}
        for evidence in self.gold_evidence:
            if evidence.source_memory_id and evidence.source_memory_id not in memory_ids:
                raise ProbeCaseError(
                    f"{self.case_id}: gold evidence {evidence.evidence_id!r} points to "
                    f"missing extracted memory {evidence.source_memory_id!r}"
                )
            if evidence.source_event_id and evidence.source_event_id not in event_ids:
                raise ProbeCaseError(
                    f"{self.case_id}: gold evidence {evidence.evidence_id!r} points to "
                    f"missing raw event {evidence.source_event_id!r}"
                )


def load_probe_cases(path: str | Path) -> list[ProbeCase]:
    """Load a JSON file containing one case object or a list of case objects."""

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        cases = [raw]
    elif isinstance(raw, list):
        cases = raw
    else:
        raise ProbeCaseError("probe case JSON must contain an object or a list of objects")
    return [ProbeCase.from_mapping(item) for item in cases]


def _required_str(value: dict[str, Any], key: str) -> str:
    try:
        raw = value[key]
    except KeyError as exc:
        raise ProbeCaseError(f"missing required field {key!r}") from exc
    if not isinstance(raw, str) or not raw.strip():
        raise ProbeCaseError(f"field {key!r} must be a non-empty string")
    return raw
