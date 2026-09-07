"""Durable, replayable incident ledger.

The ledger is deliberately a small event store rather than another mutable
incident cache.  Its JSONL entries are hash chained, have a closed schema, and
can reproduce the repair-facing views without trusting a side index.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
import json

from cmd_audit.core.state_codec import append_jsonl_fsync, canonical_json, content_sha256, require_closed_mapping
from cmd_audit.core.state_machine import incident_transition_from_event

from cmd_audit.repair.incident_triage import (
    ClassificationStatus,
    IncidentMechanism,
    MECHANISM_REPAIR_FAMILY,
    ProcessFaultSubtype,
    RepairFamily,
    TriageDecision,
    TriageError,
)


INCIDENT_LEDGER_SCHEMA_VERSION = "cmd-incident-ledger-v1"
GENESIS_HASH = "0" * 64
_EVENT_FIELDS = frozenset({
    "schema_version", "event_id", "incident_id", "mechanism", "repair_family",
    "classification_status", "process_fault_subtype", "reason", "provenance",
    "syndrome", "source_manifest_root", "observed_order", "superseding_memory_id",
    "superseded_memory_id", "suspect_ids", "previous_hash", "event_hash",
})


def _canonical(value: object) -> str:
    """Compatibility wrapper: incident v1 is deliberately UTF-8 canonical."""
    return canonical_json(value, ensure_ascii=False)


def _event_hash(event: Mapping[str, object]) -> str:
    payload = {key: value for key, value in event.items() if key != "event_hash"}
    return content_sha256(payload, ensure_ascii=False)


@dataclass(frozen=True)
class IncidentMaterializedViews:
    """Derived, replaceable repair views.  Provisional events never enter them."""

    process_faults: tuple[Mapping[str, object], ...] = ()
    lineage: tuple[Mapping[str, object], ...] = ()
    quarantined_ids: frozenset[str] = frozenset()


class IncidentLedgerError(TriageError):
    """A malformed, ambiguous, or tampered durable incident ledger."""


class IncidentLedger:
    """Append-only JSONL ledger with fail-closed replay and idempotent append."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._events: list[dict[str, object]] = []
        self._event_payloads: dict[str, str] = {}
        self._views = IncidentMaterializedViews()
        if self.path.exists():
            self.replay()

    @property
    def events(self) -> tuple[Mapping[str, object], ...]:
        return tuple(dict(row) for row in self._events)

    @property
    def views(self) -> IncidentMaterializedViews:
        return self._views

    @property
    def head_hash(self) -> str:
        return str(self._events[-1]["event_hash"]) if self._events else GENESIS_HASH

    def append(
        self,
        *,
        event_id: str,
        incident_id: str,
        decision: TriageDecision,
        provenance: Mapping[str, object],
        syndrome: Mapping[str, bool | str],
        source_manifest_root: str,
        superseding_memory_id: str | None = None,
        superseded_memory_id: str | None = None,
        suspect_ids: tuple[str, ...] = (),
    ) -> Mapping[str, object]:
        """Persist one incident event; the same event id is exactly-once.

        An event-id collision with a non-identical codeword is an integrity
        violation, not a request to overwrite history.
        """
        event = self._make_event(
            event_id=event_id, incident_id=incident_id, decision=decision,
            provenance=provenance, syndrome=syndrome,
            source_manifest_root=source_manifest_root,
            superseding_memory_id=superseding_memory_id,
            superseded_memory_id=superseded_memory_id, suspect_ids=suspect_ids,
            previous_hash=self.head_hash,
        )
        canonical = _canonical({k: v for k, v in event.items() if k not in {"previous_hash", "event_hash"}})
        prior = self._event_payloads.get(event_id)
        if prior is not None:
            if prior != canonical:
                raise IncidentLedgerError(f"event id collision for {event_id}")
            return next(dict(row) for row in self._events if row["event_id"] == event_id)
        self._validate_event(event, self._events)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        append_jsonl_fsync(self.path, event, ensure_ascii=False)
        self._events.append(event)
        self._event_payloads[event_id] = canonical
        self._views = self._materialize(self._events)
        return dict(event)

    def replay(self) -> IncidentMaterializedViews:
        events: list[dict[str, object]] = []
        payloads: dict[str, str] = {}
        if self.path.exists():
            for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise IncidentLedgerError(f"invalid JSON at ledger line {line_number}") from exc
                if not isinstance(row, dict):
                    raise IncidentLedgerError(f"non-object event at ledger line {line_number}")
                self._validate_event(row, events)
                event_id = str(row["event_id"])
                payload = _canonical({k: v for k, v in row.items() if k not in {"previous_hash", "event_hash"}})
                if event_id in payloads:
                    raise IncidentLedgerError(f"duplicate event id in ledger: {event_id}")
                payloads[event_id] = payload
                events.append(row)
        self._events, self._event_payloads = events, payloads
        self._views = self._materialize(events)
        return self._views

    def _make_event(self, **kwargs: Any) -> dict[str, object]:
        decision: TriageDecision = kwargs["decision"]
        decision.assert_exclusive()
        if not kwargs["event_id"] or not kwargs["incident_id"] or not kwargs["source_manifest_root"]:
            raise IncidentLedgerError("event_id, incident_id, and source_manifest_root are required")
        if not all(isinstance(value, (bool, str)) for value in kwargs["syndrome"].values()):
            raise IncidentLedgerError("syndrome evidence values must be boolean or string")
        if not isinstance(kwargs["provenance"], Mapping):
            raise IncidentLedgerError("provenance must be a mapping")
        event: dict[str, object] = {
            "schema_version": INCIDENT_LEDGER_SCHEMA_VERSION,
            "event_id": kwargs["event_id"], "incident_id": kwargs["incident_id"],
            "mechanism": decision.mechanism.value,
            "repair_family": decision.repair_family.value,
            "classification_status": decision.classification_status.value,
            "process_fault_subtype": (decision.process_fault_subtype.value if decision.process_fault_subtype else None),
            "reason": decision.reason, "provenance": dict(kwargs["provenance"]),
            "syndrome": dict(kwargs["syndrome"]),
            "source_manifest_root": kwargs["source_manifest_root"],
            "observed_order": list(decision.observed_order),
            "superseding_memory_id": kwargs["superseding_memory_id"],
            "superseded_memory_id": kwargs["superseded_memory_id"],
            "suspect_ids": list(kwargs["suspect_ids"]),
            "previous_hash": kwargs["previous_hash"],
        }
        event["event_hash"] = _event_hash(event)
        return event

    def _validate_event(self, event: Mapping[str, object], prior: list[dict[str, object]]) -> None:
        try:
            require_closed_mapping(event, _EVENT_FIELDS, "incident event")
        except ValueError as exc:
            raise IncidentLedgerError("incident event violates the closed schema") from exc
        if event.get("schema_version") != INCIDENT_LEDGER_SCHEMA_VERSION:
            raise IncidentLedgerError("unsupported incident ledger schema")
        if event.get("previous_hash") != (prior[-1]["event_hash"] if prior else GENESIS_HASH):
            raise IncidentLedgerError("broken incident event hash chain")
        if event.get("event_hash") != _event_hash(event):
            raise IncidentLedgerError("incident event hash mismatch")
        try:
            mechanism = IncidentMechanism(str(event["mechanism"]))
            family = RepairFamily(str(event["repair_family"]))
            status = ClassificationStatus(str(event["classification_status"]))
        except ValueError as exc:
            raise IncidentLedgerError("unknown mechanism, repair family, or status") from exc
        if MECHANISM_REPAIR_FAMILY[mechanism] is not family:
            raise IncidentLedgerError("mechanism and repair family are not one-to-one")
        # Typed adapter is deliberately schema-neutral: legacy incident JSONL
        # remains byte-for-byte compatible while crossing the state boundary.
        try:
            incident_transition_from_event(event)
        except ValueError as exc:
            raise IncidentLedgerError("incident transition invariant failed") from exc
        subtype = event["process_fault_subtype"]
        if mechanism is IncidentMechanism.PROCESS_FAULT:
            try:
                ProcessFaultSubtype(str(subtype))
            except ValueError as exc:
                raise IncidentLedgerError("process_fault subtype is required") from exc
        elif subtype is not None:
            raise IncidentLedgerError("non-process event contains a fault subtype")
        if not isinstance(event["provenance"], dict) or not isinstance(event["syndrome"], dict):
            raise IncidentLedgerError("provenance and syndrome must be objects")
        if not all(isinstance(v, (bool, str)) for v in event["syndrome"].values()):
            raise IncidentLedgerError("syndrome evidence values must be boolean or string")
        if status is ClassificationStatus.PROVISIONAL:
            return
        if mechanism is IncidentMechanism.STATE_DRIFT:
            old, new = event["superseded_memory_id"], event["superseding_memory_id"]
            order = event["observed_order"]
            if not isinstance(old, str) or not isinstance(new, str) or not old or not new or old == new:
                raise IncidentLedgerError("confirmed drift needs distinct supersession ids")
            if not isinstance(order, list) or len(order) < 2 or len(order) != len(set(order)):
                raise IncidentLedgerError("confirmed drift needs a verified observed_order")
            self._validate_lineage_edge(old, new, prior)
        elif event["superseding_memory_id"] is not None or event["superseded_memory_id"] is not None:
            raise IncidentLedgerError("only confirmed drift may contain lineage ids")
        if mechanism is IncidentMechanism.ADVERSARIAL_POISON:
            ids = event["suspect_ids"]
            if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)) or not all(isinstance(x, str) and x for x in ids):
                raise IncidentLedgerError("confirmed poison needs unique detector suspect ids")
        elif event["suspect_ids"]:
            raise IncidentLedgerError("only poison incidents may contain suspect ids")

    @staticmethod
    def _validate_lineage_edge(old: str, new: str, prior: list[dict[str, object]]) -> None:
        edges: dict[str, str] = {}
        for row in prior:
            if row["classification_status"] == ClassificationStatus.CONFIRMED.value and row["mechanism"] == IncidentMechanism.STATE_DRIFT.value:
                edges[str(row["superseded_memory_id"])] = str(row["superseding_memory_id"])
        if old in edges:
            raise IncidentLedgerError(f"{old} was already superseded")
        cursor, seen = new, {old}
        while cursor in edges:
            if cursor in seen:
                raise IncidentLedgerError("supersession cycle")
            seen.add(cursor)
            cursor = edges[cursor]
        if cursor == old:
            raise IncidentLedgerError("supersession cycle")

    @staticmethod
    def _materialize(events: list[dict[str, object]]) -> IncidentMaterializedViews:
        faults: list[Mapping[str, object]] = []
        lineage: list[Mapping[str, object]] = []
        quarantined: set[str] = set()
        for event in events:
            if event["classification_status"] != ClassificationStatus.CONFIRMED.value:
                continue
            if event["mechanism"] == IncidentMechanism.PROCESS_FAULT.value:
                faults.append(dict(event))
            elif event["mechanism"] == IncidentMechanism.STATE_DRIFT.value:
                lineage.append(dict(event))
            else:
                quarantined.update(str(x) for x in event["suspect_ids"])
        return IncidentMaterializedViews(tuple(faults), tuple(lineage), frozenset(quarantined))


__all__ = [
    "GENESIS_HASH", "INCIDENT_LEDGER_SCHEMA_VERSION", "IncidentLedger",
    "IncidentLedgerError", "IncidentMaterializedViews",
]
