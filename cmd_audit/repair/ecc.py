"""Gold-free ECC boundary for runtime memory repair.

The runtime ABI is deliberately closed.  Benchmark answers, labels, replay
artifacts, and arbitrary sidecars are rejected before incident decoding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from types import MappingProxyType
from typing import Mapping

from cmd_audit.core.state_codec import content_sha256
from cmd_audit.repair.incident_triage import (
    ClassificationStatus,
    IncidentMechanism,
    MECHANISM_REPAIR_FAMILY,
    ProcessFaultSubtype,
    RepairFamily,
    TriageDecision,
)


_OBSERVATION_FIELDS = frozenset({
    "observation_id",
    "incident_id",
    "observed_at_event_index",
    "state_root",
    "source_manifest_root",
    "process_fault_subtype",
    "observed_order",
    "superseding_memory_id",
    "superseded_memory_id",
    "cas_anomaly",
    "influence_anomaly",
    "suspect_ids",
    "signal_ids",
    "provenance",
})

_SYNDROME_FIELDS = frozenset({
    "syndrome_id",
    "observation_id",
    "incident_id",
    "observed_at_event_index",
    "state_root",
    "source_manifest_root",
    "mechanism",
    "repair_family",
    "classification_status",
    "process_fault_subtype",
    "observed_order",
    "superseding_memory_id",
    "superseded_memory_id",
    "cas_anomaly",
    "influence_anomaly",
    "suspect_ids",
    "signal_ids",
    "provenance",
})

_RECEIPT_FIELDS = frozenset({
    "receipt_id",
    "syndrome_id",
    "incident_id",
    "selection_id",
    "selected_skill_revision_id",
    "probe_id",
    "observed_after_event_index",
    "before_root",
    "shadow_root",
    "after_root",
    "resolved_syndrome",
    "invariants_passed",
    "committed",
    "rolled_back",
    "safety_violation",
    "locality_cost",
    "recurrence_after_commit",
    "provenance",
})

_PARITY_REPORT_FIELDS = frozenset({
    "resolved_syndrome",
    "invariants_passed",
    "safety_violation",
    "locality_cost",
    "recurrence_after_commit",
    "provenance",
})

_FORBIDDEN_RUNTIME_MARKERS = (
    "gold",
    "label",
    "answer_replay",
    "answer-replay",
    "same_trace",
    "same-trace",
)


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is required")
    return value


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise ValueError(f"{name} must be a sequence")
    result = tuple(value)
    if any(not isinstance(item, str) or not item for item in result):
        raise ValueError(f"{name} must contain non-empty strings")
    if len(set(result)) != len(result):
        raise ValueError(f"{name} must be unique")
    return result


def _require_gold_free(value: object, path: str = "runtime") -> None:
    """Reject sealed evaluator concepts anywhere in runtime provenance."""
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key).casefold()
            if any(marker in key_text for marker in _FORBIDDEN_RUNTIME_MARKERS):
                raise ValueError(f"gold-free runtime boundary rejects {path}.{key}")
            _require_gold_free(nested, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _require_gold_free(nested, f"{path}[{index}]")
    elif isinstance(value, str):
        text = value.casefold()
        if any(marker in text for marker in _FORBIDDEN_RUNTIME_MARKERS):
            raise ValueError(f"gold-free runtime boundary rejects {path}")


@dataclass(frozen=True)
class Contract:
    """A decoded, deployment-visible repair contract.

    It is already one-hot at the incident-mechanism boundary.  Evaluation
    labels are neither represented nor accepted by this type.
    """

    syndrome_id: str
    observation_id: str
    incident_id: str
    observed_at_event_index: int
    state_root: str
    source_manifest_root: str
    mechanism: IncidentMechanism
    repair_family: RepairFamily
    classification_status: ClassificationStatus
    process_fault_subtype: ProcessFaultSubtype | None = None
    observed_order: tuple[str, ...] = ()
    superseding_memory_id: str | None = None
    superseded_memory_id: str | None = None
    cas_anomaly: bool = False
    influence_anomaly: bool = False
    suspect_ids: tuple[str, ...] = ()
    signal_ids: tuple[str, ...] = ()
    provenance: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "syndrome_id", "observation_id", "incident_id", "state_root",
            "source_manifest_root",
        ):
            _required(getattr(self, name), name)
        if (
            isinstance(self.observed_at_event_index, bool)
            or not isinstance(self.observed_at_event_index, int)
            or self.observed_at_event_index < 0
        ):
            raise ValueError("observed_at_event_index must be non-negative")
        if MECHANISM_REPAIR_FAMILY[self.mechanism] is not self.repair_family:
            raise ValueError("incident mechanism and repair family must be one-to-one")
        object.__setattr__(self, "observed_order", _strings(self.observed_order, "observed_order"))
        object.__setattr__(self, "suspect_ids", _strings(self.suspect_ids, "suspect_ids"))
        object.__setattr__(self, "signal_ids", _strings(self.signal_ids, "signal_ids"))
        if not isinstance(self.provenance, Mapping):
            raise ValueError("provenance must be a mapping")
        _require_gold_free(self.provenance, "syndrome.provenance")
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))

        process = self.mechanism is IncidentMechanism.PROCESS_FAULT
        drift = self.mechanism is IncidentMechanism.STATE_DRIFT
        poison = self.mechanism is IncidentMechanism.ADVERSARIAL_POISON
        if process != (self.process_fault_subtype is not None):
            raise ValueError("only process_fault carries a process fault subtype")
        has_lineage = bool(self.observed_order) or self.superseding_memory_id is not None or self.superseded_memory_id is not None
        if drift:
            if (
                len(self.observed_order) < 2
                or not self.superseding_memory_id
                or not self.superseded_memory_id
                or self.superseding_memory_id == self.superseded_memory_id
            ):
                raise ValueError("state_drift requires an ordered supersession pair")
        elif has_lineage:
            raise ValueError("only state_drift carries lineage evidence")
        has_poison = self.cas_anomaly or self.influence_anomaly or bool(self.suspect_ids)
        if poison:
            if not (self.cas_anomaly or self.influence_anomaly) or not self.suspect_ids:
                raise ValueError("adversarial_poison requires anomaly evidence and suspects")
        elif has_poison:
            raise ValueError("only adversarial_poison carries anomaly evidence")

    def to_mapping(self) -> dict[str, object]:
        return {
            "syndrome_id": self.syndrome_id,
            "observation_id": self.observation_id,
            "incident_id": self.incident_id,
            "observed_at_event_index": self.observed_at_event_index,
            "state_root": self.state_root,
            "source_manifest_root": self.source_manifest_root,
            "mechanism": self.mechanism.value,
            "repair_family": self.repair_family.value,
            "classification_status": self.classification_status.value,
            "process_fault_subtype": (
                None if self.process_fault_subtype is None else self.process_fault_subtype.value
            ),
            "observed_order": list(self.observed_order),
            "superseding_memory_id": self.superseding_memory_id,
            "superseded_memory_id": self.superseded_memory_id,
            "cas_anomaly": self.cas_anomaly,
            "influence_anomaly": self.influence_anomaly,
            "suspect_ids": list(self.suspect_ids),
            "signal_ids": list(self.signal_ids),
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "Contract":
        if not isinstance(value, Mapping) or set(value) != _SYNDROME_FIELDS:
            raise ValueError("ECC syndrome mapping is not closed")
        raw = dict(value)
        raw["mechanism"] = IncidentMechanism(str(raw["mechanism"]))
        raw["repair_family"] = RepairFamily(str(raw["repair_family"]))
        raw["classification_status"] = ClassificationStatus(str(raw["classification_status"]))
        raw["process_fault_subtype"] = (
            None
            if raw["process_fault_subtype"] is None
            else ProcessFaultSubtype(str(raw["process_fault_subtype"]))
        )
        return cls(**raw)

    @property
    def content_hash(self) -> str:
        return content_sha256(self.to_mapping(), ensure_ascii=False, allow_nan=False)


@dataclass(frozen=True)
class EccRepairReceipt:
    """Gold-free, root-bound outcome of one shadow repair transition."""

    receipt_id: str
    syndrome_id: str
    incident_id: str
    selection_id: str
    selected_skill_revision_id: str
    probe_id: str
    observed_after_event_index: int
    before_root: str
    shadow_root: str
    after_root: str
    resolved_syndrome: bool
    invariants_passed: bool
    committed: bool
    rolled_back: bool
    safety_violation: bool
    locality_cost: float
    recurrence_after_commit: bool
    provenance: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "receipt_id", "syndrome_id", "incident_id", "selection_id",
            "selected_skill_revision_id", "probe_id", "before_root",
            "shadow_root", "after_root",
        ):
            _required(getattr(self, name), name)
        if (
            isinstance(self.observed_after_event_index, bool)
            or not isinstance(self.observed_after_event_index, int)
            or self.observed_after_event_index < 0
        ):
            raise ValueError("observed_after_event_index must be non-negative")
        for name in (
            "resolved_syndrome", "invariants_passed", "committed", "rolled_back",
            "safety_violation", "recurrence_after_commit",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be boolean")
        if isinstance(self.locality_cost, bool) or not isinstance(
            self.locality_cost, (int, float)
        ) or not math.isfinite(float(self.locality_cost)) or self.locality_cost < 0:
            raise ValueError("locality_cost must be a finite non-negative number")
        object.__setattr__(self, "locality_cost", float(self.locality_cost))
        if not isinstance(self.provenance, Mapping):
            raise ValueError("provenance must be a mapping")
        _require_gold_free(self.provenance, "receipt.provenance")
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))

        if self.committed:
            if (
                self.rolled_back
                or not self.resolved_syndrome
                or not self.invariants_passed
                or self.safety_violation
                or self.after_root != self.shadow_root
            ):
                raise ValueError("committed receipt requires complete ECC acceptance")
        else:
            if not self.rolled_back or self.after_root != self.before_root:
                raise ValueError("non-committed receipt must prove rollback to before_root")
        if self.recurrence_after_commit and not self.committed:
            raise ValueError("recurrence_after_commit requires a committed receipt")

    @property
    def reward(self) -> float:
        """Bounded router update target derived only from repair telemetry."""
        if (
            not self.committed
            or self.rolled_back
            or not self.resolved_syndrome
            or not self.invariants_passed
            or self.safety_violation
            or self.recurrence_after_commit
        ):
            return -1.0
        return max(-1.0, min(1.0, 1.0 - self.locality_cost))

    def to_mapping(self) -> dict[str, object]:
        return {
            "receipt_id": self.receipt_id,
            "syndrome_id": self.syndrome_id,
            "incident_id": self.incident_id,
            "selection_id": self.selection_id,
            "selected_skill_revision_id": self.selected_skill_revision_id,
            "probe_id": self.probe_id,
            "observed_after_event_index": self.observed_after_event_index,
            "before_root": self.before_root,
            "shadow_root": self.shadow_root,
            "after_root": self.after_root,
            "resolved_syndrome": self.resolved_syndrome,
            "invariants_passed": self.invariants_passed,
            "committed": self.committed,
            "rolled_back": self.rolled_back,
            "safety_violation": self.safety_violation,
            "locality_cost": self.locality_cost,
            "recurrence_after_commit": self.recurrence_after_commit,
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "EccRepairReceipt":
        if not isinstance(value, Mapping) or set(value) != _RECEIPT_FIELDS:
            raise ValueError("ECC repair receipt mapping is not closed")
        return cls(**dict(value))

    @property
    def content_hash(self) -> str:
        return content_sha256(self.to_mapping(), ensure_ascii=False, allow_nan=False)


class MemAuditEccAdapter:
    """Decode deployment-visible MemAudit signals into ECC state."""

    def decode(self, observation: Mapping[str, object]) -> Contract:
        if not isinstance(observation, Mapping) or set(observation) != _OBSERVATION_FIELDS:
            raise ValueError("runtime evidence boundary rejects non-closed observation")
        process_subtype = observation["process_fault_subtype"]
        order = _strings(observation["observed_order"], "observed_order")
        superseding = observation["superseding_memory_id"]
        superseded = observation["superseded_memory_id"]
        suspects = _strings(observation["suspect_ids"], "suspect_ids")
        cas = observation["cas_anomaly"]
        influence = observation["influence_anomaly"]
        if not isinstance(cas, bool) or not isinstance(influence, bool):
            raise ValueError("anomaly channels must be booleans")
        process_active = process_subtype is not None
        drift_active = bool(order) or superseding is not None or superseded is not None
        poison_active = cas or influence or bool(suspects)
        if sum((process_active, drift_active, poison_active)) != 1:
            raise ValueError("runtime evidence must identify exactly one incident mechanism")
        if process_active:
            mechanism = IncidentMechanism.PROCESS_FAULT
            subtype = ProcessFaultSubtype(str(process_subtype))
            status = ClassificationStatus.CONFIRMED
        elif drift_active:
            mechanism = IncidentMechanism.STATE_DRIFT
            subtype = None
            status = ClassificationStatus.CONFIRMED
        else:
            mechanism = IncidentMechanism.ADVERSARIAL_POISON
            subtype = None
            status = (
                ClassificationStatus.CONFIRMED
                if cas and influence
                else ClassificationStatus.PROVISIONAL
            )
        body = {
            "observation_id": _required(observation["observation_id"], "observation_id"),
            "incident_id": _required(observation["incident_id"], "incident_id"),
            "observed_at_event_index": observation["observed_at_event_index"],
            "state_root": _required(observation["state_root"], "state_root"),
            "source_manifest_root": _required(observation["source_manifest_root"], "source_manifest_root"),
            "mechanism": mechanism,
            "repair_family": MECHANISM_REPAIR_FAMILY[mechanism],
            "classification_status": status,
            "process_fault_subtype": subtype,
            "observed_order": order,
            "superseding_memory_id": superseding,
            "superseded_memory_id": superseded,
            "cas_anomaly": cas,
            "influence_anomaly": influence,
            "suspect_ids": suspects,
            "signal_ids": _strings(observation["signal_ids"], "signal_ids"),
            "provenance": observation["provenance"],
        }
        syndrome_id = "syndrome-" + content_sha256(
            {
                key: (
                    value.value
                    if isinstance(value, (IncidentMechanism, RepairFamily, ClassificationStatus, ProcessFaultSubtype))
                    else value
                )
                for key, value in body.items()
            },
            ensure_ascii=False,
            allow_nan=False,
        )
        return Contract(syndrome_id=syndrome_id, **body)

    def append_incident(
        self,
        ledger: object,
        observation: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Decode and durably route one syndrome to its exclusive sink."""
        from cmd_audit.repair.incident_store import IncidentLedger

        if not isinstance(ledger, IncidentLedger):
            raise TypeError("ledger must be an IncidentLedger")
        syndrome = self.decode(observation)
        return self.append_syndrome(ledger, syndrome)

    def append_syndrome(
        self,
        ledger: object,
        syndrome: Contract,
    ) -> Mapping[str, object]:
        """Durably route an already decoded syndrome to its exclusive sink."""
        from cmd_audit.repair.incident_store import IncidentLedger

        if not isinstance(ledger, IncidentLedger):
            raise TypeError("ledger must be an IncidentLedger")
        if not isinstance(syndrome, Contract):
            raise TypeError("syndrome must be a Contract")
        decision = TriageDecision(
            mechanism=syndrome.mechanism,
            repair_family=syndrome.repair_family,
            reason=f"decoded runtime ECC syndrome {syndrome.syndrome_id}",
            drift_sensor_available=bool(syndrome.observed_order),
            admits_to_failure_memory=(
                syndrome.mechanism is IncidentMechanism.PROCESS_FAULT
            ),
            classification_status=syndrome.classification_status,
            process_fault_subtype=syndrome.process_fault_subtype,
            observed_order=syndrome.observed_order,
        )
        return ledger.append(
            event_id=syndrome.syndrome_id,
            incident_id=syndrome.incident_id,
            decision=decision,
            provenance=syndrome.provenance,
            syndrome={
                "syndrome_id": syndrome.syndrome_id,
                "state_root": syndrome.state_root,
                "cas_anomaly": syndrome.cas_anomaly,
                "influence_anomaly": syndrome.influence_anomaly,
            },
            source_manifest_root=syndrome.source_manifest_root,
            superseding_memory_id=syndrome.superseding_memory_id,
            superseded_memory_id=syndrome.superseded_memory_id,
            suspect_ids=syndrome.suspect_ids,
        )

    def settle_repair(
        self,
        syndrome: Contract,
        receipt: EccRepairReceipt,
        *,
        ledger: object,
        ecology: object,
        decision: object,
        event_index: int,
    ) -> tuple[Mapping[str, object], Mapping[str, object]]:
        """Settle one root-bound receipt into incident and router ledgers.

        Binding validation occurs before either durable append.  Incident
        append is idempotent, so a crash before router observation can safely
        resume from the same syndrome and receipt.
        """
        if not isinstance(syndrome, Contract) or not isinstance(
            receipt, EccRepairReceipt
        ):
            raise TypeError("settlement requires typed syndrome and repair receipt")
        if receipt.syndrome_id != syndrome.syndrome_id:
            raise ValueError("repair receipt syndrome binding mismatch")
        if receipt.incident_id != syndrome.incident_id:
            raise ValueError("repair receipt incident binding mismatch")
        if receipt.before_root != syndrome.state_root:
            raise ValueError("repair receipt state root binding mismatch")
        if receipt.observed_after_event_index != event_index:
            raise ValueError("repair receipt event index binding mismatch")
        if receipt.selection_id != getattr(decision, "selection_id", None):
            raise ValueError("repair receipt selection binding mismatch")
        if receipt.selected_skill_revision_id != getattr(
            decision, "selected_skill_revision_id", None
        ):
            raise ValueError("repair receipt selected skill binding mismatch")
        observe = getattr(ecology, "observe_receipt", None)
        if not callable(observe):
            raise TypeError("ecology must expose receipt-only observe_receipt()")

        incident_event = self.append_syndrome(ledger, syndrome)
        snapshot = observe(decision, receipt, event_index=event_index)
        if not isinstance(snapshot, Mapping):
            raise TypeError("router observation must return a snapshot mapping")
        return incident_event, snapshot

    def execute_shadow_repair(
        self,
        syndrome: Contract,
        *,
        selection_id: str,
        selected_skill_revision_id: str,
        probe_id: str,
        observed_after_event_index: int,
        store: object,
        evaluator: object,
    ) -> EccRepairReceipt:
        """Apply one candidate in a shadow lane and commit only after ECC checks.

        The evaluator seam intentionally exposes only ``evaluate_ecc``.  This
        path never calls an answer generator, answer verifier, or trace replay.
        """
        if not isinstance(syndrome, Contract):
            raise TypeError("syndrome must be a Contract")
        for name in (
            "snapshot_root", "apply_shadow", "commit_shadow", "rollback_shadow",
        ):
            if not callable(getattr(store, name, None)):
                raise TypeError(f"shadow store requires {name}()")
        evaluate_ecc = getattr(evaluator, "evaluate_ecc", None)
        if not callable(evaluate_ecc):
            raise TypeError("ECC evaluator requires evaluate_ecc()")

        before_root = _required(store.snapshot_root(), "before_root")
        if before_root != syndrome.state_root:
            raise ValueError("syndrome state_root does not match shadow snapshot")
        mutated = False
        try:
            store.apply_shadow(syndrome, selected_skill_revision_id)
            mutated = True
            shadow_root = _required(store.snapshot_root(), "shadow_root")
            report = evaluate_ecc(
                syndrome, before_root=before_root, shadow_root=shadow_root
            )
            if not isinstance(report, Mapping) or set(report) != _PARITY_REPORT_FIELDS:
                raise ValueError("ECC parity report mapping is not closed")
            for name in (
                "resolved_syndrome", "invariants_passed", "safety_violation",
                "recurrence_after_commit",
            ):
                if not isinstance(report[name], bool):
                    raise ValueError(f"ECC parity report {name} must be boolean")
            locality_cost = report["locality_cost"]
            if (
                isinstance(locality_cost, bool)
                or not isinstance(locality_cost, (int, float))
                or not math.isfinite(float(locality_cost))
                or locality_cost < 0
            ):
                raise ValueError("ECC parity locality_cost must be non-negative")
            if not isinstance(report["provenance"], Mapping):
                raise ValueError("ECC parity provenance must be a mapping")
            _require_gold_free(report["provenance"], "parity.provenance")
            accepted = bool(
                report["resolved_syndrome"]
                and report["invariants_passed"]
                and not report["safety_violation"]
                and not report["recurrence_after_commit"]
            )
            if accepted:
                store.commit_shadow()
                after_root = shadow_root
                rolled_back = False
            else:
                store.rollback_shadow(before_root)
                after_root = _required(store.snapshot_root(), "after_root")
                rolled_back = True
            body: dict[str, object] = {
                "syndrome_id": syndrome.syndrome_id,
                "incident_id": syndrome.incident_id,
                "selection_id": _required(selection_id, "selection_id"),
                "selected_skill_revision_id": _required(
                    selected_skill_revision_id, "selected_skill_revision_id"
                ),
                "probe_id": _required(probe_id, "probe_id"),
                "observed_after_event_index": observed_after_event_index,
                "before_root": before_root,
                "shadow_root": shadow_root,
                "after_root": after_root,
                "resolved_syndrome": report["resolved_syndrome"],
                "invariants_passed": report["invariants_passed"],
                "committed": accepted,
                "rolled_back": rolled_back,
                "safety_violation": report["safety_violation"],
                "locality_cost": float(locality_cost),
                "recurrence_after_commit": report["recurrence_after_commit"],
                "provenance": {
                    **dict(report["provenance"]),
                    "syndrome_sha256": syndrome.content_hash,
                },
            }
            receipt_id = "receipt-" + content_sha256(
                body, ensure_ascii=False, allow_nan=False
            )
            return EccRepairReceipt(receipt_id=receipt_id, **body)
        except Exception:
            if mutated and store.snapshot_root() != before_root:
                store.rollback_shadow(before_root)
            raise


__all__ = ["Contract", "EccRepairReceipt", "MemAuditEccAdapter"]
