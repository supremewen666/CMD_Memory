"""Probe-case contract for CMD-Audit V0."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .labels import validate_label_base


class ProbeCaseError(ValueError):
    """Raised when a probe case does not satisfy the V0 contract."""


@dataclass(frozen=True)
class RawEvent:
    event_id: str
    text: str

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "RawEvent":
        return cls(
            event_id=_required_str(value, "event_id"), text=_required_str(value, "text")
        )


@dataclass(frozen=True)
class MemoryItem:
    memory_id: str
    text: str
    source_event_ids: tuple[str, ...] = ()
    store: str = "default"
    is_graph_expanded: bool = False
    provenance: tuple = ()

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "MemoryItem":
        provenance_raw = value.get("provenance")
        if provenance_raw is not None:
            provenance = tuple(
                ProvenanceEdge(
                    source_id=_required_str(e, "source_id"),
                    target_id=_required_str(e, "target_id"),
                    operation=_required_str(e, "operation"),
                    citation=Citation(
                        trajectory_turn=int(e["citation"]["trajectory_turn"]),
                        char_span=tuple(e["citation"]["char_span"]),
                        content_hash=_required_str(e["citation"], "content_hash"),
                    ),
                    timestamp=float(e["timestamp"]),
                    tamper_detected=bool(e.get("tamper_detected", False)),
                    source_text=str(e.get("source_text", "")),
                )
                for e in provenance_raw
            )
        else:
            provenance = ()
        return cls(
            memory_id=_required_str(value, "memory_id"),
            text=_required_str(value, "text"),
            source_event_ids=tuple(value.get("source_event_ids", ())),
            store=str(value.get("store", "default")),
            is_graph_expanded=bool(value.get("is_graph_expanded", False)),
            provenance=provenance,
        )


@dataclass(frozen=True)
class RetrievedItem:
    """Lightweight online contract for Pre-CMD Hook (issue 0018).

    Minimal subset of MemoryItem fields available at retrieval time —
    zero gold dependency. Adapters convert from MemoryItem to this type
    before calling post_retrieve_hook.
    """

    memory_id: str
    text: str


@dataclass(frozen=True)
class Citation:
    """trace-mem HMAC citation to originating trajectory evidence."""
    trajectory_turn: int
    char_span: tuple[int, int]
    content_hash: str


@dataclass(frozen=True)
class ProvenanceEdge:
    """In-edge derivation record: which item+operation influenced this item."""
    source_id: str
    target_id: str
    operation: str  # write|compress|extract|inject|retrieve|route|reason
    citation: Citation
    timestamp: float
    tamper_detected: bool = False
    source_text: str = ""


@dataclass(frozen=True)
class GoldEvidence:
    evidence_id: str
    text: str
    source_memory_id: str | None = None
    source_event_id: str | None = None
    required_phrases: tuple[str, ...] = ()
    granularity_level: str | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "GoldEvidence":
        return cls(
            evidence_id=_required_str(value, "evidence_id"),
            text=_required_str(value, "text"),
            source_memory_id=value.get("source_memory_id"),
            source_event_id=value.get("source_event_id"),
            required_phrases=tuple(value.get("required_phrases", ())),
            granularity_level=value.get("granularity_level"),
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
    perturbation_label: str | None = None
    scoring: ScoringSpec = ScoringSpec()
    has_ingestion_trace: bool = True
    default_store: str = "episodic"
    granularity_levels: tuple[str, ...] = (
        "raw",
        "event",
        "session",
        "persona",
        "procedure",
        "graph",
    )
    current_granularity: str = "session"
    safety_filter_blocked: bool = False

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "ProbeCase":
        case = cls(
            case_id=_required_str(value, "case_id"),
            query=_required_str(value, "query"),
            raw_events=tuple(
                RawEvent.from_mapping(item) for item in value.get("raw_events", ())
            ),
            extracted_memory=tuple(
                MemoryItem.from_mapping(item)
                for item in value.get("extracted_memory", ())
            ),
            gold_evidence=tuple(
                GoldEvidence.from_mapping(item)
                for item in value.get("gold_evidence", ())
            ),
            gold_answer=_required_str(value, "gold_answer"),
            baseline_outputs=tuple(
                BaselineOutput.from_mapping(item)
                for item in value.get("baseline_outputs", ())
            ),
            perturbation_label=_optional_label_v0(value.get("perturbation_label")),
            scoring=ScoringSpec.from_mapping(value.get("scoring")),
            has_ingestion_trace=bool(value.get("has_ingestion_trace", True)),
            default_store=str(value.get("default_store", "episodic")),
            granularity_levels=tuple(
                value.get(
                    "granularity_levels",
                    ("raw", "event", "session", "persona", "procedure", "graph"),
                )
            ),
            current_granularity=str(value.get("current_granularity", "session")),
            safety_filter_blocked=bool(value.get("safety_filter_blocked", False)),
        )
        case.validate()
        return case

    @classmethod
    def from_mapping_v1(cls, value: dict[str, Any]) -> "ProbeCase":
        from .labels import validate_label

        case = cls(
            case_id=_required_str(value, "case_id"),
            query=_required_str(value, "query"),
            raw_events=tuple(
                RawEvent.from_mapping(item) for item in value.get("raw_events", ())
            ),
            extracted_memory=tuple(
                MemoryItem.from_mapping(item)
                for item in value.get("extracted_memory", ())
            ),
            gold_evidence=tuple(
                GoldEvidence.from_mapping(item)
                for item in value.get("gold_evidence", ())
            ),
            gold_answer=_required_str(value, "gold_answer"),
            baseline_outputs=tuple(
                BaselineOutput.from_mapping(item)
                for item in value.get("baseline_outputs", ())
            ),
            perturbation_label=_optional_label_v1(value.get("perturbation_label")),
            scoring=ScoringSpec.from_mapping(value.get("scoring")),
            has_ingestion_trace=bool(value.get("has_ingestion_trace", True)),
            default_store=str(value.get("default_store", "episodic")),
            granularity_levels=tuple(
                value.get(
                    "granularity_levels",
                    ("raw", "event", "session", "persona", "procedure", "graph"),
                )
            ),
            current_granularity=str(value.get("current_granularity", "session")),
            safety_filter_blocked=bool(value.get("safety_filter_blocked", False)),
        )
        case.validate()
        return case

    # Name of the baseline CMD uses for recovery_gain and replay deltas.
    # All attribution-layer code reads this; comparator/monitor code also
    # delegates to it via _select_comparison_baseline, so the two layers
    # can never silently diverge.
    _cmd_baseline_name: str = "vector_memory"

    @property
    def primary_baseline(self) -> BaselineOutput:
        for output in self.baseline_outputs:
            if output.baseline_name == self._cmd_baseline_name:
                return output
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
            if (
                evidence.source_memory_id
                and evidence.source_memory_id not in memory_ids
            ):
                raise ProbeCaseError(
                    f"{self.case_id}: gold evidence {evidence.evidence_id!r} points to "
                    f"missing extracted memory {evidence.source_memory_id!r}"
                )
            if evidence.source_event_id and evidence.source_event_id not in event_ids:
                raise ProbeCaseError(
                    f"{self.case_id}: gold evidence {evidence.evidence_id!r} points to "
                    f"missing raw event {evidence.source_event_id!r}"
                )


def _required_str(value: dict[str, Any], key: str) -> str:
    try:
        raw = value[key]
    except KeyError as exc:
        raise ProbeCaseError(f"missing required field {key!r}") from exc
    if not isinstance(raw, str) or not raw.strip():
        raise ProbeCaseError(f"field {key!r} must be a non-empty string")
    return raw


def _optional_label_v0(raw: Any) -> str | None:
    """Accept None or a valid V0 pipeline label; reject invalid labels."""
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ProbeCaseError("perturbation_label must be a non-empty string or null")
    return validate_label_base(raw)


def _optional_label_v1(raw: Any) -> str | None:
    """Accept None or a valid V1 pipeline label; reject invalid labels."""
    from .labels import validate_label

    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ProbeCaseError("perturbation_label must be a non-empty string or null")
    return validate_label(raw)
