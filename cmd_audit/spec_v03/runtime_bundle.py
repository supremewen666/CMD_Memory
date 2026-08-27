"""Closed disk codec for the serving-visible CMD v0.3 runtime bundle.

The codec intentionally carries only a decision observation and its
materialized memory state.  Evaluation-side construction metadata is not a
runtime dependency and is not represented here.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from .contracts import DecisionView, canonical_sha256, deserialize_decision_view
from .repair_stream import MemoryState, PublicEvent


RUNTIME_BUNDLE_SCHEMA = "cmd-spec-v03-runtime-bundle-v1"
_BUNDLE_FIELDS = frozenset({
    "schema_version", "case_id", "source_dataset_id", "source_episode_id",
    "family_id", "lineage_id", "source_event_ids", "decision_view",
    "memory_state",
})
_STATE_FIELDS = frozenset({
    "immutable_source_log", "audit_log", "projection_order",
    "projection_index", "scope_projection", "cache_event_ids",
    "supersession_edges", "quarantine_set", "state_root",
})
_EVENT_FIELDS = frozenset({
    "event_id", "source_ref", "ordinal", "timestamp", "actor_scope",
    "payload", "payload_sha256", "source_payload_sha256",
})
_FORBIDDEN_KEYS = frozenset({
    "template_id", "target_event_id", "expected_effect", "incident_type",
    "constructor_family", "intervention_id", "legal_operator_ids",
    "root_ground_truth", "safety_oracle",
})
_FORBIDDEN_LABELS = frozenset({"process_fault", "state_drift", "poison"})


@dataclass(frozen=True)
class RuntimeBundle:
    """Fully materialized serving input, validated as one closed object."""

    case_id: str
    source_dataset_id: str
    source_episode_id: str
    family_id: str
    lineage_id: str
    source_event_ids: tuple[str, ...]
    decision_view: DecisionView
    memory_state: MemoryState


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_sha256(value: object, field: str) -> str:
    text = _require_text(value, field)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{field} must be a lowercase SHA-256")
    return text


def _reject_sealed(value: object, path: str = "runtime_bundle") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).casefold() in _FORBIDDEN_KEYS:
                raise ValueError(f"runtime bundle rejects sealed field at {path}.{key}")
            _reject_sealed(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_sealed(nested, f"{path}[{index}]")
    elif isinstance(value, str) and value.casefold() in _FORBIDDEN_LABELS:
        raise ValueError(f"runtime bundle rejects sealed incident label at {path}")


def _event_mapping(event: PublicEvent) -> dict[str, object]:
    """Encode audit/source events without exposing their internal flag.

    Membership in the separate audit log is sufficient to recover the flag on
    load, while preserving the exact root computation after deserialization.
    """
    return {
        "event_id": event.event_id,
        "source_ref": event.source_ref,
        "ordinal": event.ordinal,
        "timestamp": event.timestamp,
        "actor_scope": event.actor_scope,
        "payload": dict(event.payload),
        "payload_sha256": event.payload_sha256,
        "source_payload_sha256": event.source_payload_sha256,
    }


def _state_mapping(state: MemoryState) -> dict[str, object]:
    return {
        "immutable_source_log": [_event_mapping(event) for event in state.immutable_source_log],
        "audit_log": [_event_mapping(event) for event in state.audit_log],
        "projection_order": list(state.projection_order),
        "projection_index": [list(item) for item in state.projection_index],
        "scope_projection": [list(item) for item in state.scope_projection],
        "cache_event_ids": list(state.cache_event_ids),
        "supersession_edges": [list(item) for item in state.supersession_edges],
        "quarantine_set": list(state.quarantine_set),
        "state_root": state.root,
    }


def serialize(*, case_id: str, source_dataset_id: str, source_episode_id: str,
              family_id: str, lineage_id: str, source_event_ids: tuple[str, ...],
              decision_view: DecisionView, memory_state: MemoryState) -> dict[str, object]:
    """Serialize one runtime bundle and prove its internal bindings first."""
    bundle = {
        "schema_version": RUNTIME_BUNDLE_SCHEMA,
        "case_id": case_id,
        "source_dataset_id": source_dataset_id,
        "source_episode_id": source_episode_id,
        "family_id": family_id,
        "lineage_id": lineage_id,
        "source_event_ids": list(source_event_ids),
        # ``DecisionView.to_mapping`` is a Python mapping and retains tuples;
        # disk bundles use JSON lists, exactly as the closed decoder expects.
        "decision_view": json.loads(json.dumps(decision_view.to_mapping(), sort_keys=True)),
        "memory_state": _state_mapping(memory_state),
    }
    deserialize(bundle)
    return bundle


def _deserialize_event(value: object, *, audit: bool) -> PublicEvent:
    if not isinstance(value, Mapping) or set(value) != _EVENT_FIELDS:
        raise ValueError("runtime state event must use the closed schema")
    payload = value["payload"]
    if not isinstance(payload, Mapping):
        raise ValueError("runtime state event payload must be a mapping")
    ordinal = value["ordinal"]
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise ValueError("runtime state event ordinal must be a non-negative integer")
    timestamp, actor_scope = value["timestamp"], value["actor_scope"]
    if timestamp is not None and not isinstance(timestamp, str):
        raise ValueError("runtime state event timestamp must be string or null")
    if actor_scope is not None and not isinstance(actor_scope, str):
        raise ValueError("runtime state event actor_scope must be string or null")
    payload_copy = dict(payload)
    payload_sha256 = _require_sha256(value["payload_sha256"], "runtime state event payload_sha256")
    if payload_sha256 != canonical_sha256(payload_copy):
        raise ValueError("runtime state event payload hash mismatch")
    return PublicEvent(
        event_id=_require_text(value["event_id"], "runtime state event event_id"),
        source_ref=_require_text(value["source_ref"], "runtime state event source_ref"),
        ordinal=ordinal,
        timestamp=timestamp,
        actor_scope=actor_scope,
        payload=payload_copy,
        payload_sha256=payload_sha256,
        source_payload_sha256=_require_sha256(value["source_payload_sha256"], "runtime state event source_payload_sha256"),
        synthetic=audit,
    )


def _id_list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a JSON list")
    return tuple(_require_text(item, f"{field}[]") for item in value)


def _pair_list(value: object, field: str, *, nullable_right: bool = False) -> tuple[tuple[str, str | None], ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a JSON list")
    result: list[tuple[str, str | None]] = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError(f"{field} entries must be pairs")
        left = _require_text(item[0], f"{field}[].left")
        right = item[1]
        if nullable_right and right is None:
            result.append((left, None))
        else:
            result.append((left, _require_text(right, f"{field}[].right")))
    return tuple(result)


def _index_pairs(value: object) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, list):
        raise ValueError("projection_index must be a JSON list")
    result: list[tuple[str, int]] = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError("projection_index entries must be pairs")
        event_id, index = _require_text(item[0], "projection_index[].event_id"), item[1]
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError("projection_index values must be non-negative integers")
        result.append((event_id, index))
    return tuple(result)


def _deserialize_state(value: object) -> MemoryState:
    if not isinstance(value, Mapping) or set(value) != _STATE_FIELDS:
        raise ValueError("runtime memory state must use the closed schema")
    source_raw, audit_raw = value["immutable_source_log"], value["audit_log"]
    if not isinstance(source_raw, list) or not isinstance(audit_raw, list):
        raise ValueError("runtime memory logs must be JSON lists")
    source = tuple(_deserialize_event(event, audit=False) for event in source_raw)
    audit = tuple(_deserialize_event(event, audit=True) for event in audit_raw)
    state = MemoryState(
        immutable_source_log=source,
        audit_log=audit,
        projection_order=_id_list(value["projection_order"], "projection_order"),
        projection_index=_index_pairs(value["projection_index"]),
        scope_projection=_pair_list(value["scope_projection"], "scope_projection", nullable_right=True),
        cache_event_ids=_id_list(value["cache_event_ids"], "cache_event_ids"),
        supersession_edges=tuple((left, _require_text(right, "supersession_edges[].right")) for left, right in _pair_list(value["supersession_edges"], "supersession_edges")),
        quarantine_set=_id_list(value["quarantine_set"], "quarantine_set"),
    )
    if len({event.event_id for event in source + audit}) != len(source) + len(audit):
        raise ValueError("runtime memory state event IDs must be globally unique")
    state_ids = {event.event_id for event in source + audit}
    referenced = (
        set(state.projection_order) | {event_id for event_id, _ in state.projection_index}
        | {event_id for event_id, _ in state.scope_projection} | set(state.cache_event_ids)
        | {event_id for edge in state.supersession_edges for event_id in edge} | set(state.quarantine_set)
    )
    if not referenced <= state_ids:
        raise ValueError("runtime memory state references an unknown event ID")
    if _require_sha256(value["state_root"], "state_root") != state.root:
        raise ValueError("runtime memory state root mismatch")
    return state


def deserialize(value: Mapping[str, object]) -> RuntimeBundle:
    """Fail closed on unknown fields, malformed values, or broken bindings."""
    if set(value) != _BUNDLE_FIELDS:
        raise ValueError("runtime bundle must use the closed spec-v0.3 schema")
    _reject_sealed(value)
    if value["schema_version"] != RUNTIME_BUNDLE_SCHEMA:
        raise ValueError("unsupported runtime bundle schema")
    decision_raw = value["decision_view"]
    if not isinstance(decision_raw, Mapping):
        raise ValueError("runtime bundle decision_view must be a mapping")
    decision = deserialize_decision_view(decision_raw)
    state = _deserialize_state(value["memory_state"])
    source_event_ids = _id_list(value["source_event_ids"], "source_event_ids")
    fields = {
        "case_id": _require_text(value["case_id"], "case_id"),
        "source_dataset_id": _require_text(value["source_dataset_id"], "source_dataset_id"),
        "source_episode_id": _require_text(value["source_episode_id"], "source_episode_id"),
        "family_id": _require_text(value["family_id"], "family_id"),
        "lineage_id": _require_text(value["lineage_id"], "lineage_id"),
    }
    for name, expected in fields.items():
        if getattr(decision, name) != expected:
            raise ValueError(f"runtime bundle {name} does not match DecisionView")
    if source_event_ids != tuple(event.event_id for event in state.immutable_source_log):
        raise ValueError("runtime bundle source_event_ids do not match immutable log")
    observation = decision.observation
    current = observation.get("current_state") if isinstance(observation, Mapping) else None
    event_log = observation.get("event_log") if isinstance(observation, Mapping) else None
    if not isinstance(current, Mapping) or current.get("state_root") != state.root:
        raise ValueError("runtime bundle state root is not bound to DecisionView")
    if not isinstance(event_log, list) or tuple(
        row.get("event_id") for row in event_log if isinstance(row, Mapping)
    ) != tuple(event.event_id for event in state.immutable_source_log + state.audit_log):
        raise ValueError("runtime bundle event log IDs are not bound to memory state")
    return RuntimeBundle(source_event_ids=source_event_ids, decision_view=decision, memory_state=state, **fields)


def load(path: str | Path) -> RuntimeBundle:
    """Load one JSON runtime bundle from disk through the closed decoder."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("runtime bundle file must contain a JSON object")
    return deserialize(raw)


def load_many(path: str | Path) -> tuple[RuntimeBundle, ...]:
    """Load a freeze runtime-case JSON list with closed per-row validation."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("runtime case file must contain a JSON list")
    bundles: list[RuntimeBundle] = []
    case_ids: set[str] = set()
    for index, row in enumerate(raw):
        if not isinstance(row, Mapping):
            raise ValueError(f"runtime case row {index} must be a JSON object")
        bundle = deserialize(row)
        if bundle.case_id in case_ids:
            raise ValueError(f"runtime case file contains duplicate case_id: {bundle.case_id}")
        case_ids.add(bundle.case_id)
        bundles.append(bundle)
    return tuple(bundles)


def load_runtime_cases(path: str | Path) -> tuple[RuntimeBundle, ...]:
    """Serving-oriented name for loading a freeze runtime_cases.json file."""
    return load_many(path)
