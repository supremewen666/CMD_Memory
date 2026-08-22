"""Durable, identity-bound settlement records for V4 prospective feedback.

This module deliberately stores only the selection/follow-up lineage needed to
decide whether an update is permitted.  It never reads a shadow reward.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Mapping

from cmd_audit.core.state_codec import append_jsonl_fsync, content_sha256


SETTLEMENT_SCHEMA_VERSION = "cmd-v4-feedback-settlement-v2-audit"
_LEGACY_SETTLEMENT_SCHEMA_VERSION = "cmd-v4-feedback-settlement-v1"
_SHADOW_PROVENANCE_MARKERS = ("shadow", "gold", "proxy")


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _event_index(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True)
class PendingSelection:
    arm_id: str
    selection_id: str
    case_id: str
    family_id: str
    intent_id: str
    graph_sha256: str
    pre_policy_snapshot_sha256: str
    probe_id: str
    selected_at_event_index: int
    effect: str
    decision_mapping: Mapping[str, object]
    evidence_contract_sha256: str

    def __post_init__(self) -> None:
        for name in (
            "arm_id", "selection_id", "case_id", "family_id", "intent_id",
            "graph_sha256", "pre_policy_snapshot_sha256", "probe_id", "evidence_contract_sha256",
        ):
            _required(getattr(self, name), name)
        _event_index(self.selected_at_event_index, "selected_at_event_index")
        if not isinstance(self.decision_mapping, Mapping):
            raise ValueError("decision_mapping must be a mapping")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "PendingSelection":
        if set(value) != set(cls.__dataclass_fields__):
            raise ValueError("pending selection mapping is not closed")
        return cls(**value)


@dataclass(frozen=True)
class TypedFollowup:
    feedback_id: str
    arm_id: str
    selection_id: str
    case_id: str
    family_id: str
    intent_id: str
    graph_sha256: str
    pre_policy_snapshot_sha256: str
    probe_id: str
    selected_at_event_index: int
    effect: str
    observed_after_event_index: int
    provenance: str
    exposure_state_sha256: str
    post_state_sha256: str
    evidence_contract_sha256: str
    typed_reward: float
    locality_cost: float
    changed_item_count: int
    valid: bool
    rolled_back: bool

    def __post_init__(self) -> None:
        for name in (
            "feedback_id", "arm_id", "selection_id", "case_id", "family_id", "intent_id", "graph_sha256",
            "pre_policy_snapshot_sha256", "probe_id", "effect", "provenance",
            "exposure_state_sha256", "post_state_sha256",
            "evidence_contract_sha256",
        ):
            _required(getattr(self, name), name)
        _event_index(self.selected_at_event_index, "selected_at_event_index")
        observed = _event_index(self.observed_after_event_index, "observed_after_event_index")
        if observed <= self.selected_at_event_index:
            raise ValueError("follow-up must be observed after its selection")
        if any(marker in self.provenance.lower() for marker in _SHADOW_PROVENANCE_MARKERS):
            raise ValueError("shadow-derived provenance cannot settle prospective feedback")
        for name in ("typed_reward", "locality_cost"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be numeric")
        if isinstance(self.changed_item_count, bool) or not isinstance(self.changed_item_count, int) or self.changed_item_count < 0:
            raise ValueError("changed_item_count must be a non-negative integer")
        if not isinstance(self.valid, bool) or not isinstance(self.rolled_back, bool):
            raise ValueError("valid and rolled_back must be booleans")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "TypedFollowup":
        if set(value) != set(cls.__dataclass_fields__):
            raise ValueError("typed follow-up mapping is not closed")
        return cls(**value)


@dataclass(frozen=True)
class SettlementReceipt:
    feedback_id: str
    selection_id: str
    status: str
    reason: str | None
    feedback_sha256: str


@dataclass(frozen=True)
class PreparedSettlement:
    feedback: TypedFollowup
    arm_id: str
    before_root: str
    after_root: str | None


class FeedbackSettlementLedger:
    """Append-only, hash-chained settlement truth source.

    ``path`` is a JSONL audit artifact (the suffix is deliberately not
    significant).  A v1 JSON snapshot is rejected rather than silently used:
    it has no manifest binding or tamper-evident history.
    """

    def __init__(self, path: Path, *, manifest_root: str = "legacy-unbound") -> None:
        self.path = Path(path)
        self.manifest_root = _required(manifest_root, "manifest_root")
        self._pending: dict[str, PendingSelection] = {}
        self._receipts: dict[str, SettlementReceipt] = {}
        self._consumed_selection_ids: set[str] = set()
        self._events: list[dict[str, object]] = []
        self._logical_events: dict[str, str] = {}
        self._head = "0" * 64
        if self.path.exists():
            self._load()

    def register(self, selection: PendingSelection) -> None:
        if selection.selection_id in self._consumed_selection_ids:
            raise ValueError("consumed selection cannot be registered again")
        existing = self._pending.get(selection.selection_id)
        if existing is not None and existing != selection:
            raise ValueError("selection identity was reused with different lineage")
        if existing is None:
            self._append("selection_registered", selection.selection_id, {"selection": asdict(selection)})
            self._pending[selection.selection_id] = selection

    def settle(self, feedback: TypedFollowup) -> SettlementReceipt:
        previous = self._receipts.get(feedback.feedback_id)
        if previous is not None:
            if previous.feedback_sha256 != self._feedback_sha256(feedback):
                return SettlementReceipt(
                    feedback.feedback_id, feedback.selection_id, "rejected",
                    "feedback_id_payload_collision", previous.feedback_sha256,
                )
            return SettlementReceipt(
                feedback.feedback_id, feedback.selection_id, "duplicate", previous.reason,
                previous.feedback_sha256,
            )
        self._append("feedback_received", feedback.feedback_id, {"feedback": asdict(feedback)})
        pending = self._pending.get(feedback.selection_id)
        if pending is None:
            return self._record(feedback, "rejected", "unknown_or_consumed_selection")
        for name in (
            "case_id", "intent_id", "graph_sha256", "pre_policy_snapshot_sha256",
            "probe_id", "selected_at_event_index", "arm_id", "family_id",
            "effect", "evidence_contract_sha256",
        ):
            if getattr(pending, name) != getattr(feedback, name):
                return self._record(feedback, "rejected", f"mismatched_{name}")
        # Compatibility convenience for unit callers: still records a complete
        # no-op transaction rather than bypassing the audit state machine.
        self._append("policy_update_prepared", feedback.feedback_id, {"before_root": feedback.pre_policy_snapshot_sha256, "arm_id": feedback.arm_id, "feedback": asdict(feedback)})
        self._append("policy_update_committed", feedback.feedback_id, {"after_root": feedback.pre_policy_snapshot_sha256})
        del self._pending[feedback.selection_id]
        self._consumed_selection_ids.add(feedback.selection_id)
        return self._record(feedback, "accepted", None)

    def prepare_settlement(self, feedback: TypedFollowup, *, before_root: str, arm_id: str) -> SettlementReceipt:
        """Validate but retain pending until the caller durably commits learning."""
        previous = self._receipts.get(feedback.feedback_id)
        if previous is not None:
            return SettlementReceipt(feedback.feedback_id, feedback.selection_id, "duplicate", previous.reason, previous.feedback_sha256)
        self._append("feedback_received", feedback.feedback_id, {"feedback": asdict(feedback)})
        pending = self._pending.get(feedback.selection_id)
        if pending is None:
            return self._record(feedback, "rejected", "unknown_or_consumed_selection")
        for name in ("case_id", "intent_id", "graph_sha256", "pre_policy_snapshot_sha256", "probe_id", "selected_at_event_index", "arm_id", "family_id", "effect", "evidence_contract_sha256"):
            if getattr(pending, name) != getattr(feedback, name): return self._record(feedback, "rejected", f"mismatched_{name}")
        self._append("policy_update_prepared", feedback.feedback_id, {"before_root": _required(before_root, "before_root"), "arm_id": _required(arm_id, "arm_id"), "feedback": asdict(feedback)})
        return SettlementReceipt(feedback.feedback_id, feedback.selection_id, "prepared", None, self._feedback_sha256(feedback))

    def accept_prepared(self, feedback: TypedFollowup) -> SettlementReceipt:
        if f"policy_update_prepared:{feedback.feedback_id}" not in self._logical_events:
            raise ValueError("accepted settlement lacks prepared update")
        if f"policy_update_committed:{feedback.feedback_id}" not in self._logical_events:
            raise ValueError("accepted settlement lacks committed update")
        if feedback.selection_id not in self._pending:
            previous = self._receipts.get(feedback.feedback_id)
            if previous is None: raise ValueError("prepared selection was unexpectedly consumed")
            return SettlementReceipt(feedback.feedback_id, feedback.selection_id, "duplicate", previous.reason, previous.feedback_sha256)
        self._pending.pop(feedback.selection_id); self._consumed_selection_ids.add(feedback.selection_id)
        return self._record(feedback, "accepted", None)

    def reject(self, feedback: TypedFollowup, reason: str) -> SettlementReceipt:
        """Persist a fail-closed receipt without consuming a pending selection."""
        _required(reason, "reason")
        previous = self._receipts.get(feedback.feedback_id)
        if previous is not None:
            if previous.feedback_sha256 != self._feedback_sha256(feedback):
                return SettlementReceipt(
                    feedback.feedback_id, feedback.selection_id, "rejected",
                    "feedback_id_payload_collision", previous.feedback_sha256,
                )
            return SettlementReceipt(
                feedback.feedback_id, feedback.selection_id, "duplicate", previous.reason,
                previous.feedback_sha256,
            )
        self._append("feedback_received", feedback.feedback_id, {"feedback": asdict(feedback)})
        return self._record(feedback, "rejected", reason)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def pending(self) -> tuple[PendingSelection, ...]:
        return tuple(sorted(self._pending.values(), key=lambda item: item.selection_id))

    @property
    def head(self) -> str:
        return self._head

    @property
    def pending_root(self) -> str:
        return self._sha256([asdict(row) for row in self.pending])

    @property
    def prepared_transactions(self) -> tuple[PreparedSettlement, ...]:
        prepared: dict[str, PreparedSettlement] = {}
        accepted: set[str] = set()
        for event in self._events:
            payload = event["payload"]
            if event["event_type"] == "policy_update_prepared":
                feedback = TypedFollowup.from_mapping(payload["feedback"])
                prepared[feedback.feedback_id] = PreparedSettlement(feedback, str(payload["arm_id"]), str(payload["before_root"]), None)
            elif event["event_type"] == "policy_update_committed":
                current = prepared.get(str(event["logical_id"]))
                if current is not None:
                    prepared[current.feedback.feedback_id] = PreparedSettlement(current.feedback, current.arm_id, current.before_root, str(payload["after_root"]))
            elif event["event_type"] == "settlement_accepted": accepted.add(str(event["logical_id"]))
        return tuple(value for key, value in sorted(prepared.items()) if key not in accepted)

    def policy_update_prepared(self, feedback_id: str, before_root: str) -> None:
        self._append("policy_update_prepared", feedback_id, {"before_root": _required(before_root, "before_root")})

    def policy_update_committed(self, feedback_id: str, after_root: str) -> None:
        self._append("policy_update_committed", feedback_id, {"after_root": _required(after_root, "after_root")})

    def checkpoint_committed(self, checkpoint_id: str, checkpoint_root: str) -> None:
        self._append("checkpoint_committed", _required(checkpoint_id, "checkpoint_id"), {"checkpoint_root": _required(checkpoint_root, "checkpoint_root")})

    def _record(self, feedback: TypedFollowup, status: str, reason: str | None) -> SettlementReceipt:
        receipt = SettlementReceipt(
            feedback.feedback_id, feedback.selection_id, status, reason,
            self._feedback_sha256(feedback),
        )
        self._append(
            "settlement_accepted" if status == "accepted" else "settlement_rejected",
            feedback.feedback_id,
            {"receipt": asdict(receipt)},
        )
        return receipt

    def _load(self) -> None:
        text = self.path.read_text(encoding="utf-8")
        if text.lstrip().startswith("{") and "\n" not in text.strip():
            raw = json.loads(text)
            if raw.get("schema_version") == _LEGACY_SETTLEMENT_SCHEMA_VERSION:
                raise ValueError("legacy v1 settlement snapshot requires explicit migration; refusing unverified resume")
        for line in text.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            self._replay_event(event)

    def _append(self, event_type: str, logical_id: str, payload: Mapping[str, object]) -> None:
        _required(event_type, "event_type")
        logical_id = _required(logical_id, "logical_id")
        payload_dict = dict(payload)
        digest = self._sha256({"event_type": event_type, "logical_id": logical_id, "payload": payload_dict})
        prior = self._logical_events.get(f"{event_type}:{logical_id}")
        if prior is not None:
            if prior != digest:
                raise ValueError("audit logical-id collision")
            return
        event = {
            "schema_version": SETTLEMENT_SCHEMA_VERSION,
            "event_index": len(self._events) + 1,
            "event_type": event_type,
            "logical_id": logical_id,
            "manifest_root": self.manifest_root,
            "previous_event_hash": self._head,
            "payload": payload_dict,
        }
        event["event_hash"] = self._sha256(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        append_jsonl_fsync(self.path, event)
        self._replay_event(event)

    def _replay_event(self, event: Mapping[str, object]) -> None:
        required = {"schema_version", "event_index", "event_type", "logical_id", "manifest_root", "previous_event_hash", "payload", "event_hash"}
        if set(event) != required or event.get("schema_version") != SETTLEMENT_SCHEMA_VERSION:
            raise ValueError("settlement audit schema mismatch")
        if event["manifest_root"] != self.manifest_root:
            raise ValueError("settlement audit manifest mismatch")
        if event["event_index"] != len(self._events) + 1 or event["previous_event_hash"] != self._head:
            raise ValueError("settlement audit chain discontinuity")
        unhashed = {key: value for key, value in event.items() if key != "event_hash"}
        if event["event_hash"] != self._sha256(unhashed):
            raise ValueError("settlement audit hash mismatch")
        typ, logical_id = event["event_type"], event["logical_id"]
        if not isinstance(typ, str) or not isinstance(logical_id, str) or not isinstance(event["payload"], Mapping):
            raise ValueError("settlement audit event is invalid")
        digest = self._sha256({"event_type": typ, "logical_id": logical_id, "payload": dict(event["payload"])})
        key = f"{typ}:{logical_id}"
        if key in self._logical_events:
            raise ValueError("settlement audit duplicate event")
        self._logical_events[key] = digest
        payload = event["payload"]
        if typ == "selection_registered":
            selection = PendingSelection.from_mapping(payload["selection"])
            if selection.selection_id != logical_id or selection.selection_id in self._pending or selection.selection_id in self._consumed_selection_ids:
                raise ValueError("invalid registered selection")
            self._pending[selection.selection_id] = selection
        elif typ in {"settlement_accepted", "settlement_rejected"}:
            receipt = SettlementReceipt(**payload["receipt"])
            if receipt.feedback_id != logical_id or receipt.feedback_id in self._receipts:
                raise ValueError("invalid settlement receipt")
            self._receipts[receipt.feedback_id] = receipt
            if receipt.status == "accepted":
                if f"policy_update_prepared:{receipt.feedback_id}" not in self._logical_events or f"policy_update_committed:{receipt.feedback_id}" not in self._logical_events:
                    raise ValueError("accepted settlement lacks committed update")
                self._pending.pop(receipt.selection_id, None)
                self._consumed_selection_ids.add(receipt.selection_id)
        elif typ == "policy_update_committed":
            if f"policy_update_prepared:{logical_id}" not in self._logical_events:
                raise ValueError("committed update lacks prepared record")
        elif typ not in {"feedback_received", "policy_update_prepared", "checkpoint_committed"}:
            raise ValueError("unregistered settlement audit event")
        self._events.append(dict(event))
        self._head = event["event_hash"]  # type: ignore[assignment]

    @staticmethod
    def _sha256(value: object) -> str:
        # v2 used ASCII JSON; keep that byte-level hash contract.
        return content_sha256(value)

    @staticmethod
    def _feedback_sha256(feedback: TypedFollowup) -> str:
        return content_sha256(asdict(feedback))


def ingest_followups(path: Path, source: Path, *, manifest_root: str) -> int:
    """Validate closed JSON/JSONL follow-ups and append receipt evidence.

    Ingestion deliberately records only ``feedback_received``; the runner is
    the sole component permitted to settle and learn from it.
    """
    ledger = FeedbackSettlementLedger(path, manifest_root=manifest_root)
    raw = source.read_text(encoding="utf-8")
    rows = json.loads(raw) if raw.lstrip().startswith("[") else [json.loads(line) for line in raw.splitlines() if line.strip()]
    if not isinstance(rows, list):
        raise ValueError("follow-up input must be a JSON array or JSONL")
    for row in rows:
        feedback = TypedFollowup.from_mapping(row)
        ledger._append("feedback_received", feedback.feedback_id, {"feedback": asdict(feedback)})
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Append closed typed follow-ups to a V4 settlement audit")
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--followups", type=Path, required=True)
    parser.add_argument("--manifest-root", required=True)
    args = parser.parse_args(argv)
    ingest_followups(args.ledger, args.followups, manifest_root=args.manifest_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
