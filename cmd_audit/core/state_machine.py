"""Typed, side-effect-free state-machine boundary for CMD durable layers.

It is intentionally an adapter layer: old ledgers retain their schemas while
new code can validate the same roots and transitions before persistence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol, runtime_checkable

from .state_codec import append_jsonl_fsync, content_sha256, require_closed_mapping

GENESIS_ROOT = "0" * 64
_PHASES = frozenset({"prepared", "committed"})
_FAMILIES = {
    "process_fault": "pipeline_patch", "state_drift": "supersede_and_log",
    "adversarial_poison": "quarantine_and_audit",
}


def _roots(value: Mapping[str, str], name: str) -> Mapping[str, str]:
    if not isinstance(value, Mapping) or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in value.items()):
        raise ValueError(f"{name} must map non-empty strings to non-empty roots")
    return MappingProxyType(dict(sorted(value.items())))


@dataclass(frozen=True)
class ControlRegisters:
    epoch: int
    event_watermark: int
    router_id: str
    router_snapshot_root: str
    registry_root: str
    memory_roots: Mapping[str, str] = field(default_factory=dict)
    repository_roots: Mapping[str, str] = field(default_factory=dict)
    manifest_root: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.epoch, bool) or not isinstance(self.epoch, int) or self.epoch < 0:
            raise ValueError("epoch must be non-negative")
        if isinstance(self.event_watermark, bool) or not isinstance(self.event_watermark, int) or self.event_watermark < -1:
            raise ValueError("event_watermark must be >= -1")
        for name in ("router_id", "router_snapshot_root", "registry_root", "manifest_root"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"{name} is required")
        object.__setattr__(self, "memory_roots", _roots(self.memory_roots, "memory_roots"))
        object.__setattr__(self, "repository_roots", _roots(self.repository_roots, "repository_roots"))

    @property
    def root(self) -> str:
        return content_sha256({
            "epoch": self.epoch, "event_watermark": self.event_watermark,
            "router_id": self.router_id, "router_snapshot_root": self.router_snapshot_root,
            "registry_root": self.registry_root, "memory_roots": dict(self.memory_roots),
            "repository_roots": dict(self.repository_roots), "manifest_root": self.manifest_root,
        }, ensure_ascii=False, allow_nan=False)


@dataclass(frozen=True)
class StateTransition:
    operator_id: str
    kind: str
    phase: str
    event_index: int
    before_root: str
    after_root: str
    operand_sha256: str
    parent_transition_hash: str = GENESIS_ROOT
    incident_mechanism: str | None = None
    repair_family: str | None = None
    transition_hash: str = ""

    def __post_init__(self) -> None:
        if not all(isinstance(getattr(self, n), str) and getattr(self, n) for n in ("operator_id", "kind", "before_root", "after_root", "operand_sha256", "parent_transition_hash")):
            raise ValueError("transition identities and roots are required")
        if self.phase not in _PHASES:
            raise ValueError("transition phase is invalid")
        if isinstance(self.event_index, bool) or not isinstance(self.event_index, int) or self.event_index < 0:
            raise ValueError("transition event_index must be non-negative")
        if (self.incident_mechanism is None) != (self.repair_family is None):
            raise ValueError("incident mechanism and repair family are one-hot together")
        if self.incident_mechanism is not None and _FAMILIES.get(self.incident_mechanism) != self.repair_family:
            raise ValueError("incident mechanism and repair family are not one-to-one")
        expected = content_sha256(self.mapping(include_hash=False), ensure_ascii=False, allow_nan=False)
        if self.transition_hash and self.transition_hash != expected:
            raise ValueError("transition hash mismatch")
        if not self.transition_hash:
            object.__setattr__(self, "transition_hash", expected)

    def mapping(self, *, include_hash: bool = True) -> dict[str, object]:
        value = {
            "operator_id": self.operator_id, "kind": self.kind, "phase": self.phase,
            "event_index": self.event_index, "before_root": self.before_root,
            "after_root": self.after_root, "operand_sha256": self.operand_sha256,
            "parent_transition_hash": self.parent_transition_hash,
            "incident_mechanism": self.incident_mechanism, "repair_family": self.repair_family,
        }
        if include_hash:
            value["transition_hash"] = self.transition_hash
        return value


@dataclass(frozen=True)
class OperatorResult:
    registers: ControlRegisters
    output: Mapping[str, object] = field(default_factory=dict)


@runtime_checkable
class StateOperator(Protocol):
    operator_id: str
    def apply(self, registers: ControlRegisters, operand: Mapping[str, object]) -> OperatorResult: ...


@dataclass(frozen=True)
class DescriptorOperator:
    """A pure router descriptor; selection is data, never a memory mutation."""
    operator_id: str
    def apply(self, registers: ControlRegisters, operand: Mapping[str, object]) -> OperatorResult:
        require_closed_mapping(operand, {"selection"}, "router operand")
        if not isinstance(operand["selection"], str) or not operand["selection"]:
            raise ValueError("router selection is required")
        return OperatorResult(registers, MappingProxyType({"selection": operand["selection"]}))


class OperatorRegistry:
    def __init__(self) -> None: self._operators: dict[str, StateOperator] = {}
    def register(self, operator: StateOperator) -> None:
        if not operator.operator_id or operator.operator_id in self._operators: raise ValueError("operator id must be unique")
        self._operators[operator.operator_id] = operator
    def get(self, operator_id: str) -> StateOperator:
        try: return self._operators[operator_id]
        except KeyError as exc: raise ValueError(f"unknown operator: {operator_id}") from exc
    @property
    def ids(self) -> tuple[str, ...]: return tuple(sorted(self._operators))


def default_router_registry() -> OperatorRegistry:
    registry = OperatorRegistry()
    for operator_id in ("global_policy", "ghost_hierarchy", "observable_residual_ghost"):
        registry.register(DescriptorOperator(operator_id))
    return registry


class TransitionJournal:
    """Small hash-chained journal usable beside legacy ledgers."""
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path); self._events: list[StateTransition] = []
        if self.path.exists(): self.replay()
    @property
    def head(self) -> str: return self._events[-1].transition_hash if self._events else GENESIS_ROOT
    def append(self, transition: StateTransition) -> None:
        self._validate(transition)
        append_jsonl_fsync(self.path, transition.mapping(), ensure_ascii=False, allow_nan=False)
        self._events.append(transition)
    def replay(self) -> tuple[StateTransition, ...]:
        events: list[StateTransition] = []
        original = self._events
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line: continue
                import json
                row = json.loads(line)
                require_closed_mapping(row, {"operator_id", "kind", "phase", "event_index", "before_root", "after_root", "operand_sha256", "parent_transition_hash", "incident_mechanism", "repair_family", "transition_hash"}, "transition")
                event = StateTransition(**row)
                self._events = events
                self._validate(event)
                events.append(event)
        except Exception:
            self._events = original
            raise
        self._events = events
        return tuple(events)
    def _validate(self, transition: StateTransition) -> None:
        if transition.event_index != len(self._events): raise ValueError("transition event index is not monotonic")
        if transition.parent_transition_hash != self.head: raise ValueError("transition parent hash mismatch")
        if transition.phase == "committed":
            prepared = [e for e in self._events if e.phase == "prepared" and e.operator_id == transition.operator_id and e.kind == transition.kind and e.before_root == transition.before_root and e.after_root == transition.after_root and e.operand_sha256 == transition.operand_sha256]
            if not prepared: raise ValueError("committed transition lacks prepared transition")


def incident_transition_from_event(event: Mapping[str, object]) -> StateTransition:
    """Validate/describe a legacy incident event without changing its schema."""
    mechanism, family = str(event["mechanism"]), str(event["repair_family"])
    payload = {k: v for k, v in event.items() if k not in {"previous_hash", "event_hash"}}
    return StateTransition(
        operator_id="incident_triage", kind="incident", phase="committed",
        event_index=0, before_root=str(event["previous_hash"]), after_root=str(event["event_hash"]),
        operand_sha256=content_sha256(payload, ensure_ascii=False, allow_nan=False),
        incident_mechanism=mechanism, repair_family=family,
    )


def checkpoint_transition_from_event(event: Mapping[str, object]) -> StateTransition:
    """Typed view of a v4 checkpoint event (without extending its JSON schema)."""
    checkpoint = event.get("checkpoint")
    if not isinstance(checkpoint, Mapping):
        raise ValueError("checkpoint transition requires checkpoint mapping")
    phase = str(event.get("phase", ""))
    if phase not in {"checkpoint_prepared", "checkpoint_committed"}:
        raise ValueError("checkpoint transition phase is invalid")
    return StateTransition(
        operator_id="v4_checkpoint", kind="checkpoint",
        phase="prepared" if phase == "checkpoint_prepared" else "committed",
        event_index=int(event["event_index"]) - 1,
        before_root=str(event["previous_event_hash"]), after_root=str(event["event_hash"]),
        operand_sha256=content_sha256(dict(checkpoint)),
    )


def settlement_transition_from_event(event: Mapping[str, object]) -> StateTransition:
    """Typed view of a v4 settlement audit record."""
    kind = str(event.get("event_type", ""))
    phase = "prepared" if kind == "policy_update_prepared" else "committed"
    if kind not in {"policy_update_prepared", "policy_update_committed", "settlement_accepted", "settlement_rejected"}:
        raise ValueError("settlement event has no state transition phase")
    payload = event.get("payload")
    if not isinstance(payload, Mapping): raise ValueError("settlement payload is required")
    return StateTransition(
        operator_id="v4_settlement", kind=kind, phase=phase,
        event_index=int(event["event_index"]) - 1,
        before_root=str(event["previous_event_hash"]), after_root=str(event["event_hash"]),
        operand_sha256=content_sha256(dict(payload)),
    )
