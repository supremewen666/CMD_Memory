"""Gold-free runtime and evaluator-only data contracts for spec v0.3."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Mapping


SCHEMA_VERSION = "cmd-spec-v03-contracts-v1"
_DECISION_FIELDS = frozenset({
    "schema_version", "case_id", "source_dataset_id", "source_episode_id",
    "family_id", "lineage_id", "event_index", "observation", "provenance",
    "unsupported_fields",
})
_FORBIDDEN_RUNTIME_MARKERS = (
    "gold", "label", "ground_truth", "groundtruth", "answer_key",
    "legal_operator", "oracle", "evaluator_only", "root_ground_truth",
    "synthetic", "intervention", "constructor", "template_id", "target_event_id",
    "expected_effect", "intervention_visible", "synthetic_event_count",
)
_FORBIDDEN_RUNTIME_VALUES = frozenset({
    "synthetic", "synthetic_intervention", "intervention_visible",
    "clean", "drop", "duplicate", "reorder", "truncate", "wrong_index",
    "wrong_scope", "stale_cache", "explicit_supersede", "implicit_invalidation",
    "dependent_invalidation", "untrusted_injection", "authority_crossing", "sleeper_trigger",
})


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _reject_evaluator_fields(value: object, path: str = "decision_view") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key).casefold()
            if any(marker in key_text for marker in _FORBIDDEN_RUNTIME_MARKERS):
                raise ValueError(f"runtime decision view rejects evaluator field at {path}.{key}")
            _reject_evaluator_fields(nested, f"{path}.{key}")
    elif isinstance(value, (tuple, list)):
        for index, nested in enumerate(value):
            _reject_evaluator_fields(nested, f"{path}[{index}]")
    elif isinstance(value, str):
        text = value.casefold()
        if text in _FORBIDDEN_RUNTIME_VALUES:
            raise ValueError(f"runtime decision view rejects sealed value at {path}")


@dataclass(frozen=True)
class DecisionView:
    """The complete data visible to a runtime router or repair executor."""

    case_id: str
    source_dataset_id: str
    source_episode_id: str
    family_id: str
    lineage_id: str
    event_index: int
    observation: Mapping[str, Any]
    provenance: Mapping[str, Any]
    unsupported_fields: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field in (
            "case_id", "source_dataset_id", "source_episode_id", "family_id", "lineage_id",
        ):
            _require_text(getattr(self, field), field)
        if isinstance(self.event_index, bool) or not isinstance(self.event_index, int) or self.event_index < 0:
            raise ValueError("event_index must be a non-negative integer")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported decision view schema")
        if not isinstance(self.observation, Mapping) or not isinstance(self.provenance, Mapping):
            raise ValueError("observation and provenance must be mappings")
        if len(set(self.unsupported_fields)) != len(self.unsupported_fields):
            raise ValueError("unsupported fields must be unique")
        _reject_evaluator_fields(asdict(self))

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)

    @property
    def content_sha256(self) -> str:
        return canonical_sha256(self.to_mapping())


@dataclass(frozen=True)
class EvaluatorOnly:
    """Sealed fields that cannot be deserialized by runtime code."""

    incident_type: str | None = None
    root_ground_truth: str | None = None
    legal_operator_ids: tuple[str, ...] = ()
    expected_answer: str | None = None
    safety_oracle: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class RepairCase:
    """A case has two namespaces and only ``decision`` crosses into serving."""

    decision: DecisionView
    evaluator_only: EvaluatorOnly


@dataclass(frozen=True)
class SkillSpec:
    """Portable procedural content only; posterior evidence is deliberately absent."""

    skill_id: str
    version: str
    operator_id: str
    incident_type: str
    preconditions: Mapping[str, Any]
    repair_action: Mapping[str, Any]
    invariant_checks: tuple[str, ...]
    locality_bound: int
    rollback_action: Mapping[str, Any]
    content_sha256: str

    def __post_init__(self) -> None:
        for field in ("skill_id", "version", "operator_id", "incident_type", "content_sha256"):
            _require_text(getattr(self, field), field)
        if self.locality_bound < 0:
            raise ValueError("locality_bound must be non-negative")
        if len(self.content_sha256) != 64:
            raise ValueError("skill content_sha256 must be SHA-256")


@dataclass(frozen=True)
class SkillEvidenceState:
    """Non-portable, separately versioned evidence for a skill revision."""

    skill_id: str
    valid_after_event: int
    support_count: float
    success_summary: Mapping[str, float]
    rollback_summary: Mapping[str, float]
    safety_summary: Mapping[str, float]
    locality_summary: Mapping[str, float]
    evidence_state_sha256: str


def deserialize_decision_view(value: Mapping[str, object]) -> DecisionView:
    """Closed, recursive runtime deserializer which fails before routing."""
    if set(value) != _DECISION_FIELDS:
        raise ValueError("decision view must use the closed spec-v0.3 schema")
    _reject_evaluator_fields(value)
    unsupported = value["unsupported_fields"]
    if not isinstance(unsupported, list):
        raise ValueError("unsupported_fields must be a JSON list")
    return DecisionView(
        case_id=_require_text(value["case_id"], "case_id"),
        source_dataset_id=_require_text(value["source_dataset_id"], "source_dataset_id"),
        source_episode_id=_require_text(value["source_episode_id"], "source_episode_id"),
        family_id=_require_text(value["family_id"], "family_id"),
        lineage_id=_require_text(value["lineage_id"], "lineage_id"),
        event_index=value["event_index"],
        observation=value["observation"],  # type: ignore[arg-type]
        provenance=value["provenance"],  # type: ignore[arg-type]
        unsupported_fields=tuple(_require_text(item, "unsupported_fields[]") for item in unsupported),
        schema_version=_require_text(value["schema_version"], "schema_version"),
    )
