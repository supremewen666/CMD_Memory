"""Runtime-only structural diagnosis for the CMD spec v0.3 repair stream.

This module deliberately consumes only a :class:`DecisionView` and its
materialized :class:`MemoryState`.  It has no dependency on intervention
constructors, evaluator sidecars, or shadow outcome matrices.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from cmd_audit.repair.ecc import EccSyndrome, MemAuditEccAdapter
from cmd_audit.repair.incident_triage import ProcessFaultSubtype

from .contracts import DecisionView, canonical_sha256
from .repair_stream import MemoryState


@dataclass(frozen=True)
class RootLocalization:
    """The runtime-visible objects that justify a structural diagnosis."""

    source_event_ids: tuple[str, ...]
    active_audit_event_ids: tuple[str, ...]
    suspect_event_ids: tuple[str, ...]
    affected_projection_ids: tuple[str, ...]


@dataclass(frozen=True)
class SyndromeDescriptor:
    """A closed structural description before ECC's typed decoding."""

    classification: str
    confidence: float
    signal_ids: tuple[str, ...]
    root: RootLocalization
    state_root: str

    def __post_init__(self) -> None:
        if self.classification not in {"clean", "unknown", "process_fault", "state_drift", "poison"}:
            raise ValueError("unknown runtime syndrome classification")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("runtime syndrome confidence must be in [0, 1]")
        if len(set(self.signal_ids)) != len(self.signal_ids):
            raise ValueError("runtime syndrome signals must be unique")


@dataclass(frozen=True)
class RuntimeSyndrome:
    """A descriptor plus an optional ECC syndrome; clean/unknown abstain."""

    descriptor: SyndromeDescriptor
    ecc_syndrome: EccSyndrome | None

    @property
    def abstains(self) -> bool:
        return self.ecc_syndrome is None


def _source_ids(state: MemoryState) -> tuple[str, ...]:
    return tuple(event.event_id for event in state.immutable_source_log)


def _source_by_ref(state: MemoryState) -> Mapping[str, str]:
    return {event.source_ref: event.event_id for event in state.immutable_source_log}


def _validate_runtime_inputs(decision: DecisionView, state: MemoryState) -> None:
    observation = decision.observation
    current = observation.get("current_state")
    events = observation.get("event_log")
    if not isinstance(current, Mapping) or not isinstance(events, list):
        raise ValueError("runtime decision lacks the closed state observation")
    if current.get("state_root") != state.root:
        raise ValueError("runtime state root is not bound to DecisionView")
    visible_ids = tuple(row.get("event_id") for row in events if isinstance(row, Mapping))
    expected_ids = tuple(event.event_id for event in state.immutable_source_log + state.audit_log)
    if visible_ids != expected_ids or len(visible_ids) != len(events):
        raise ValueError("runtime event log is not bound to MemoryState")


def _lineage(state: MemoryState) -> tuple[str, str] | None:
    """Return the source old/new pair only when the state contract is exact."""
    active = [
        event for event in state.audit_log
        if event.actor_scope == "source-derived" and event.event_id in state.projection_order
    ]
    if len(active) != 1:
        return None
    source_ref = active[0].payload.get("supersedes_source_ref")
    if not isinstance(source_ref, str):
        return None
    old = _source_by_ref(state).get(source_ref)
    if old is None or old not in state.projection_order:
        return None
    return old, active[0].event_id


def _validated_supersession_old_ids(
    state: MemoryState, active_audit: tuple[str, ...],
) -> tuple[str, ...] | None:
    """Validate committed lineage edges before they alter membership accounting."""
    if not state.supersession_edges:
        return ()
    source = {event.event_id: event for event in state.immutable_source_log}
    active = {
        event.event_id: event for event in state.audit_log
        if event.event_id in active_audit
    }
    olds: set[str] = set()
    news: set[str] = set()
    for edge in state.supersession_edges:
        if not isinstance(edge, tuple) or len(edge) != 2:
            return None
        old, new = edge
        old_event = source.get(old)
        new_event = active.get(new)
        if (
            old in olds or new in news or old_event is None or new_event is None
            or new_event.actor_scope != "source-derived"
            or new_event.payload.get("supersedes_source_ref") != old_event.source_ref
        ):
            return None
        olds.add(old)
        news.add(new)
    return tuple(sorted(olds))


def audit_structural_telemetry(decision: DecisionView, state: MemoryState) -> SyndromeDescriptor:
    """Derive one conservative diagnosis from materialized runtime structure."""
    _validate_runtime_inputs(decision, state)
    source_ids = _source_ids(state)
    order = state.projection_order
    source_scope = {event.event_id: event.actor_scope for event in state.immutable_source_log}
    active_audit = tuple(event.event_id for event in state.audit_log if event.event_id in order)
    process_signals: list[str] = []
    affected: list[str] = []
    superseded = _validated_supersession_old_ids(state, active_audit)
    if superseded is None:
        process_signals.append("invalid-supersession-edge")
        superseded = ()
    expected_members = tuple(event_id for event_id in source_ids if event_id not in superseded) + active_audit
    if len(order) != len(set(order)) or set(order) != set(expected_members) or len(order) != len(expected_members):
        process_signals.append("projection-membership")
        affected.extend(event_id for event_id in expected_members if event_id not in order)
    elif not active_audit and order != source_ids:
        process_signals.append("projection-order")
        affected.extend(order)
    expected_index = tuple((event_id, index) for index, event_id in enumerate(order))
    if state.projection_index != expected_index:
        process_signals.append("projection-index")
        affected.extend(event_id for event_id, _index in state.projection_index)
    scope = dict(state.scope_projection)
    scope_mismatches = tuple(event_id for event_id, value in source_scope.items() if scope.get(event_id) != value)
    if scope_mismatches:
        process_signals.append("projection-scope")
        affected.extend(scope_mismatches)
    if state.cache_event_ids:
        process_signals.append("stale-cache")
        affected.extend(state.cache_event_ids)

    lineage = _lineage(state)
    poison_ids = tuple(
        event.event_id for event in state.audit_log
        if event.actor_scope == "untrusted" and event.event_id in order and event.event_id not in state.quarantine_set
    )
    categories = sum((bool(process_signals), lineage is not None, bool(poison_ids)))
    root = RootLocalization(source_ids, active_audit, poison_ids, tuple(sorted(set(affected))))
    if categories == 0:
        return SyndromeDescriptor("clean", 1.0, (), root, state.root)
    if categories != 1:
        return SyndromeDescriptor("unknown", 0.0, tuple(sorted(process_signals + (["lineage"] if lineage else []) + (["untrusted-active"] if poison_ids else []))), root, state.root)
    if lineage is not None:
        return SyndromeDescriptor("state_drift", 1.0, ("supersession-lineage",), root, state.root)
    if poison_ids:
        return SyndromeDescriptor("poison", 1.0, ("untrusted-active",), root, state.root)
    return SyndromeDescriptor("process_fault", 1.0, tuple(sorted(process_signals)), root, state.root)


def decode_ecc_syndrome(decision: DecisionView, state: MemoryState) -> RuntimeSyndrome:
    """Convert unambiguous MemAudit structure into an ECC syndrome, or abstain."""
    descriptor = audit_structural_telemetry(decision, state)
    if descriptor.classification in {"clean", "unknown"}:
        return RuntimeSyndrome(descriptor, None)
    incident_id = "runtime-incident-" + canonical_sha256({
        "case_id": decision.case_id, "state_root": state.root, "signals": descriptor.signal_ids,
    })
    observation: dict[str, object] = {
        "observation_id": "runtime-observation-" + decision.content_sha256,
        "incident_id": incident_id,
        "observed_at_event_index": decision.event_index,
        "state_root": state.root,
        "source_manifest_root": canonical_sha256(descriptor.root.source_event_ids),
        "process_fault_subtype": None,
        "observed_order": [],
        "superseding_memory_id": None,
        "superseded_memory_id": None,
        "cas_anomaly": False,
        "influence_anomaly": False,
        "suspect_ids": [],
        "signal_ids": list(descriptor.signal_ids),
        "provenance": {"detector": "cmd-spec-v03-structural-memaudit-v1"},
    }
    if descriptor.classification == "process_fault":
        observation["process_fault_subtype"] = ProcessFaultSubtype.RETRIEVAL.value
    elif descriptor.classification == "state_drift":
        old, new = _lineage(state) or (None, None)
        if old is None or new is None:
            raise ValueError("state drift descriptor lost its runtime lineage")
        observation.update({"observed_order": [old, new], "superseding_memory_id": new, "superseded_memory_id": old})
    else:
        observation.update({"cas_anomaly": True, "influence_anomaly": True, "suspect_ids": list(descriptor.root.suspect_event_ids)})
    return RuntimeSyndrome(descriptor, MemAuditEccAdapter().decode(observation))
