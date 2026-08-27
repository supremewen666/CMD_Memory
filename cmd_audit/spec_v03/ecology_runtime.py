"""Spec v0.3 ecology coordination around the existing GHOST implementations.

This module deliberately does not implement a router.  It supplies the temporal
and audit boundary around ``ghost_ecology``: receipt maturation, immutable skill
content, separately-versioned evidence, and an append-only replay log.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any, Mapping, Sequence

from cmd_audit.repair.ecc import EccRepairReceipt
from cmd_audit.repair.ghost_ecology import (
    DelayedOutcomeFeedback,
    EcologySelection,
    FailureDeposit,
    GHOSTEcologyRouter,
    ObservableResidualGHOSTRouter,
    ObservableResidualSelection,
    PatternResponsibility,
    RegistrySnapshot,
    SkillRevision,
    content_sha256,
    skill_promotion_decision,
    PromotionEvidence,
)


SCHEMA_VERSION = "cmd-spec-v03-ecology-runtime-v2"
GENESIS_SHA256 = content_sha256({"schema_version": SCHEMA_VERSION, "genesis": True})


class SkillStatus(str, Enum):
    PROBATIONARY = "probationary"
    ACTIVE = "active"
    QUARANTINED = "quarantined"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


@dataclass(frozen=True)
class EcologyRules:
    """Frozen, content-addressed governance triggers for receipt settlement."""

    quarantine_on_safety_violation: bool = True
    quarantine_on_provenance_violation: bool = True
    quarantine_on_integrity_violation: bool = True
    provenance_violation_field: str = "provenance_violation"
    integrity_violation_field: str = "integrity_violation"

    def __post_init__(self) -> None:
        if not self.provenance_violation_field or not self.integrity_violation_field:
            raise ValueError("quarantine rule fields must be non-empty")

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)

    @property
    def rules_sha256(self) -> str:
        return content_sha256(self.to_mapping())


@dataclass(frozen=True)
class SkillEvidence:
    """Outcome state kept out of portable ``SkillRevision`` content."""

    skill_revision_id: str
    valid_after_event: int
    settled_receipt_ids: tuple[str, ...] = ()
    successful_receipt_ids: tuple[str, ...] = ()
    rollback_receipt_ids: tuple[str, ...] = ()
    safety_receipt_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.valid_after_event < 0:
            raise ValueError("valid_after_event must be non-negative")
        rows = (
            self.settled_receipt_ids,
            self.successful_receipt_ids,
            self.rollback_receipt_ids,
            self.safety_receipt_ids,
        )
        if any(len(set(row)) != len(row) for row in rows):
            raise ValueError("receipt evidence ids must be unique")

    @property
    def evidence_state_sha256(self) -> str:
        return content_sha256(asdict(self))


@dataclass(frozen=True)
class SkillLifecycleRecord:
    content: SkillRevision
    status: SkillStatus
    effective_after_event: int
    evidence: SkillEvidence
    transition_effective_after_event: int
    previous_status: SkillStatus | None = None
    superseded_by: str | None = None
    quarantine_reason: str | None = None
    retirement_reason: str | None = None

    def __post_init__(self) -> None:
        if self.effective_after_event < 0:
            raise ValueError("effective_after_event must be non-negative")
        if self.transition_effective_after_event < self.effective_after_event:
            raise ValueError("transition cannot predate skill creation")
        if self.evidence.skill_revision_id != self.content.skill_revision_id:
            raise ValueError("skill content and evidence revision ids disagree")
        if self.evidence.valid_after_event < self.effective_after_event:
            raise ValueError("evidence cannot predate skill eligibility")

    def to_mapping(self) -> dict[str, object]:
        return {
            "content": self.content.to_mapping(),
            "status": self.status.value,
            "effective_after_event": self.effective_after_event,
            "evidence": asdict(self.evidence),
            "transition_effective_after_event": self.transition_effective_after_event,
            "previous_status": None if self.previous_status is None else self.previous_status.value,
            "superseded_by": self.superseded_by,
            "quarantine_reason": self.quarantine_reason,
            "retirement_reason": self.retirement_reason,
        }

    def status_at(self, event_index: int) -> SkillStatus:
        if event_index < self.transition_effective_after_event and self.previous_status is not None:
            return self.previous_status
        return self.status


@dataclass(frozen=True)
class RegisteredSelection:
    selection: EcologySelection | ObservableResidualSelection
    failure: FailureDeposit
    skills: tuple[SkillRevision, ...]
    pre_action_prior: float

    def __post_init__(self) -> None:
        selected = self.selection.selected_skill_revision_id
        if selected is None:
            raise ValueError("abstentions cannot receive repair receipts")
        if selected not in {skill.skill_revision_id for skill in self.skills}:
            raise ValueError("selection does not bind a supplied skill")
        if not -1.0 <= self.pre_action_prior <= 1.0:
            raise ValueError("pre_action_prior must be in [-1, 1]")

    @property
    def selected_at_event_index(self) -> int:
        return self.selection.event_index


@dataclass(frozen=True)
class Settlement:
    receipt_id: str
    selection_id: str
    settled_at_event: int
    router_snapshot_sha256: str


class EcologyRuntime:
    """Fail-closed delayed receipt and skill-lifecycle coordinator.

    A caller routes with Mix GHOST first, then registers that exact selection.
    At every event ``t`` it must call :meth:`settle_before` before routing event
    ``t``.  This is intentionally a small adapter, leaving Mix GHOST's scoring
    and posterior math entirely in ``ghost_ecology``.
    """

    def __init__(
        self,
        router: GHOSTEcologyRouter | ObservableResidualGHOSTRouter,
        *,
        model_id: str = "unconfigured",
        rules: EcologyRules | None = None,
    ) -> None:
        if not isinstance(router, (GHOSTEcologyRouter, ObservableResidualGHOSTRouter)):
            raise TypeError("runtime requires an existing GHOST router")
        self.router = router
        self.model_id = model_id
        self.rules = rules or EcologyRules()
        self._initial_router_snapshot = dict(router.snapshot)
        self._events: list[dict[str, object]] = []
        self._failures: dict[str, FailureDeposit] = {}
        self._skills: dict[str, SkillLifecycleRecord] = {}
        self._selections: dict[str, RegisteredSelection] = {}
        self._receipts: dict[str, EccRepairReceipt] = {}
        self._settled_receipt_ids: set[str] = set()
        self._censored_selection_ids: set[str] = set()

    @property
    def head_sha256(self) -> str:
        return GENESIS_SHA256 if not self._events else str(self._events[-1]["event_sha256"])

    @property
    def events(self) -> tuple[Mapping[str, object], ...]:
        return tuple(dict(row) for row in self._events)

    @property
    def skills(self) -> Mapping[str, SkillLifecycleRecord]:
        return dict(self._skills)

    def deposit_failure(self, failure: FailureDeposit, *, event_index: int) -> str:
        existing = self._failures.get(failure.failure_id)
        if existing is not None and existing != failure:
            raise ValueError("FailureMemory deposit is immutable")
        if existing is None:
            self._failures[failure.failure_id] = failure
            self._append("failure_deposit", event_index, failure.to_mapping())
        return failure.failure_id

    def deposit_failure_memory(
        self, record: object, *, failure_id: str, case_id: str,
        family_id_audit_only: str, features: Mapping[str, float],
        context_sha256: str, provenance_sha256: str, event_index: int,
    ) -> str:
        """Sediment a legacy FailureMemory record through its canonical adapter."""
        return self.deposit_failure(
            FailureDeposit.from_failure_memory(
                record, failure_id=failure_id, case_id=case_id,
                family_id_audit_only=family_id_audit_only, features=features,
                context_sha256=context_sha256, provenance_sha256=provenance_sha256,
            ),
            event_index=event_index,
        )

    def birth(self, skill: SkillRevision, *, event_index: int) -> SkillLifecycleRecord:
        if skill.skill_revision_id in self._skills:
            raise ValueError("skill revision is immutable and already exists")
        if skill.producing_failure_id not in self._failures:
            raise ValueError("skill birth requires an existing FailureMemory deposit")
        effective = event_index + 1
        record = SkillLifecycleRecord(
            content=skill, status=SkillStatus.PROBATIONARY,
            effective_after_event=effective,
            evidence=SkillEvidence(skill.skill_revision_id, effective),
            transition_effective_after_event=effective,
        )
        self._skills[skill.skill_revision_id] = record
        self._append("skill_birth", event_index, record.to_mapping())
        return record

    def seed_frozen_skill(self, skill: SkillRevision, *, event_index: int) -> SkillLifecycleRecord:
        """Import audited frozen content; it is not an ecology birth."""
        if skill.state != "stable":
            raise ValueError("only stable frozen skill content may be seeded")
        if skill.skill_revision_id in self._skills:
            raise ValueError("frozen skill revision is already registered")
        record = SkillLifecycleRecord(
            content=skill, status=SkillStatus.ACTIVE, effective_after_event=event_index,
            evidence=SkillEvidence(skill.skill_revision_id, event_index),
            transition_effective_after_event=event_index,
        )
        self._skills[skill.skill_revision_id] = record
        self._append("frozen_skill_seed", event_index, record.to_mapping())
        return record

    def import_frozen_registry(
        self, registry: RegistrySnapshot, skills: Sequence[SkillRevision], *, event_index: int,
    ) -> None:
        """Audit a sealed registry import and explicitly seed its frozen content."""
        if not registry.sealed:
            raise PermissionError("only a sealed registry may be imported")
        supplied = {skill.skill_revision_id: skill for skill in skills}
        if set(registry.stable_skill_revision_ids) != set(supplied):
            raise ValueError("registry and supplied frozen skills disagree")
        for skill in supplied.values():
            self.seed_frozen_skill(skill, event_index=event_index)
        self._append("frozen_registry_import", event_index, registry.to_mapping())

    def eligible_skills(self, event_index: int) -> tuple[SkillRevision, ...]:
        """Return active content only; same-event births/revisions cannot serve."""
        return tuple(
            record.content for _id, record in sorted(self._skills.items())
            if record.status_at(event_index) is SkillStatus.ACTIVE
            and record.effective_after_event <= event_index
        )

    def register_selection(
        self, selection: EcologySelection | ObservableResidualSelection,
        *, failure: FailureDeposit, skills: Sequence[SkillRevision],
        pre_action_prior: float,
    ) -> None:
        registered = RegisteredSelection(selection, failure, tuple(skills), float(pre_action_prior))
        if registered.selection.selection_id in self._selections:
            raise ValueError("selection is already registered")
        selected = registered.selection.selected_skill_revision_id
        assert selected is not None
        registered_ids = {skill.skill_revision_id for skill in registered.skills}
        if registered_ids != set(selection.candidate_skill_revision_ids):
            raise ValueError("registered candidates disagree with router selection")
        unknown = registered_ids - set(self._skills)
        if unknown:
            raise PermissionError("selection used an unregistered frozen skill")
        eligible = {row.skill_revision_id for row in self.eligible_skills(selection.event_index)}
        if not registered_ids <= eligible:
            raise PermissionError("selection used a lifecycle-ineligible skill")
        self._selections[selection.selection_id] = registered
        self._append("selection_registered", selection.event_index, self._selection_mapping(registered))

    def submit_receipt(self, receipt: EccRepairReceipt) -> None:
        selection = self._selections.get(receipt.selection_id)
        if selection is None:
            raise ValueError("receipt refers to an unregistered selection")
        if receipt.receipt_id in self._receipts:
            if self._receipts[receipt.receipt_id] != receipt:
                raise ValueError("receipt id is immutable")
            return
        self._validate_receipt_binding(selection, receipt)
        self._receipts[receipt.receipt_id] = receipt
        self._append("receipt_received", receipt.observed_after_event_index, receipt.to_mapping())

    def router_feedback_eligible(self, receipt: EccRepairReceipt, *, event_index: int) -> bool:
        selection = self._selections.get(receipt.selection_id)
        return (
            selection is not None
            and receipt.receipt_id in self._receipts
            and receipt.receipt_id not in self._settled_receipt_ids
            and receipt.selection_id not in self._censored_selection_ids
            and receipt.observed_after_event_index <= event_index
            and selection.selected_at_event_index < event_index
        )

    def settle_before(self, event_index: int) -> tuple[Settlement, ...]:
        """Settle mature receipts in the frozen `(selected_at, selection_id)` order."""
        ready = [
            receipt for receipt in self._receipts.values()
            if self.router_feedback_eligible(receipt, event_index=event_index)
        ]
        ready.sort(key=lambda receipt: (
            self._selections[receipt.selection_id].selected_at_event_index,
            receipt.selection_id,
        ))
        settled: list[Settlement] = []
        for receipt in ready:
            selection = self._selections[receipt.selection_id]
            feedback = DelayedOutcomeFeedback(
                selection_id=receipt.selection_id,
                selected_skill_revision_id=receipt.selected_skill_revision_id,
                probe_id=receipt.probe_id,
                selected_at_event_index=selection.selected_at_event_index,
                observed_after_event_index=receipt.observed_after_event_index,
                pre_action_prior=selection.pre_action_prior,
                delayed_utility=receipt.reward,
                valid=receipt.committed and receipt.resolved_syndrome and receipt.invariants_passed,
                rolled_back=receipt.rolled_back,
                delayed_regression=receipt.recurrence_after_commit,
                provenance="cmd-spec-v03-receipt-settlement-v1",
                development_proxy=getattr(self.router, "allow_development_proxy", False),
            )
            self.router.observe(selection.selection, feedback)  # type: ignore[arg-type]
            self._settled_receipt_ids.add(receipt.receipt_id)
            self._record_evidence(selection, receipt, settled_at_event=event_index)
            snapshot = self.router.snapshot
            settlement = Settlement(receipt.receipt_id, receipt.selection_id, event_index, str(snapshot["snapshot_sha256"]))
            self._append("receipt_settled", event_index, {
                "receipt_id": receipt.receipt_id,
                "selection_id": receipt.selection_id,
                "feedback": asdict(feedback),
                "router_snapshot_sha256": settlement.router_snapshot_sha256,
            })
            automatic_reason = self._automatic_quarantine_reason(receipt)
            if automatic_reason is not None:
                self.quarantine(
                    receipt.selected_skill_revision_id, reason=automatic_reason,
                    event_index=event_index, automatic=True,
                )
            settled.append(settlement)
        return tuple(settled)

    def right_censor(self, scored_horizon_event: int) -> tuple[str, ...]:
        """Mark unresolved or immature outcomes without assigning any reward."""
        ids = tuple(sorted(
            selection_id for selection_id, selection in self._selections.items()
            if selection.selected_at_event_index <= scored_horizon_event
            and not any(
                receipt.selection_id == selection_id and receipt.receipt_id in self._settled_receipt_ids
                for receipt in self._receipts.values()
            )
        ))
        self._censored_selection_ids.update(ids)
        self._append("right_censored", scored_horizon_event, {"censored_selection_ids": list(ids)})
        return ids

    def promote(self, skill_revision_id: str, *, event_index: int, anchor_non_regression: bool) -> SkillLifecycleRecord:
        record = self._require_status(skill_revision_id, SkillStatus.PROBATIONARY)
        evidence = self._promotion_evidence(record.content.skill_revision_id)
        decision = skill_promotion_decision(record.content, evidence, anchor_non_regression=anchor_non_regression)
        if not decision.eligible:
            raise PermissionError(f"promotion rejected: {decision.reason}")
        updated = replace(
            record, status=SkillStatus.ACTIVE, previous_status=record.status,
            transition_effective_after_event=event_index + 1,
        )
        self._skills[skill_revision_id] = updated
        self._append("skill_promotion", event_index, {
            "skill_revision_id": skill_revision_id,
            "supporting_feedback_ids": list(decision.supporting_feedback_ids),
            "transition_effective_after_event": event_index + 1,
            "record": updated.to_mapping(),
        })
        return updated

    def supersede(self, parent_revision_id: str, successor: SkillRevision, *, event_index: int) -> SkillLifecycleRecord:
        parent = self._skills.get(parent_revision_id)
        if parent is None:
            raise ValueError("unknown parent skill revision")
        if parent_revision_id not in successor.parent_revision_ids:
            raise ValueError("superseding revision must name its parent")
        born = self.birth(successor, event_index=event_index)
        parent_updated = replace(
            parent, status=SkillStatus.SUPERSEDED, previous_status=parent.status,
            transition_effective_after_event=event_index + 1,
            superseded_by=successor.skill_revision_id,
        )
        self._skills[parent_revision_id] = parent_updated
        self._append("skill_supersede", event_index, {
            "parent_revision_id": parent_revision_id,
            "successor_revision_id": successor.skill_revision_id,
            "transition_effective_after_event": event_index + 1,
            "record": parent_updated.to_mapping(),
        })
        return born

    def quarantine(self, skill_revision_id: str, *, reason: str, event_index: int, automatic: bool = False) -> SkillLifecycleRecord:
        if not reason:
            raise ValueError("quarantine requires a reason")
        record = self._skills.get(skill_revision_id)
        if record is None or record.status is SkillStatus.RETIRED:
            raise ValueError("cannot quarantine an unknown or retired skill")
        updated = replace(
            record, status=SkillStatus.QUARANTINED, previous_status=record.status,
            transition_effective_after_event=event_index + 1, quarantine_reason=reason,
        )
        self._skills[skill_revision_id] = updated
        self._append("skill_quarantine", event_index, {"skill_revision_id": skill_revision_id, "reason": reason, "automatic": automatic, "transition_effective_after_event": event_index + 1, "record": updated.to_mapping()})
        return updated

    def retire(self, skill_revision_id: str, *, reason: str, event_index: int) -> SkillLifecycleRecord:
        if not reason:
            raise ValueError("retirement requires a reason")
        record = self._skills.get(skill_revision_id)
        if record is None:
            raise ValueError("unknown skill revision")
        updated = replace(
            record, status=SkillStatus.RETIRED, previous_status=record.status,
            transition_effective_after_event=event_index + 1, retirement_reason=reason,
        )
        self._skills[skill_revision_id] = updated
        self._append("skill_retire", event_index, {"skill_revision_id": skill_revision_id, "reason": reason, "transition_effective_after_event": event_index + 1, "record": updated.to_mapping()})
        return updated

    @property
    def snapshot(self) -> dict[str, object]:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "model_id": self.model_id,
            "rules": self.rules.to_mapping(),
            "rules_sha256": self.rules.rules_sha256,
            "initial_router_snapshot": self._initial_router_snapshot,
            "events": self._events,
            "failures": [row.to_mapping() for _, row in sorted(self._failures.items())],
            "skills": [row.to_mapping() for _, row in sorted(self._skills.items())],
            "settled_receipt_ids": sorted(self._settled_receipt_ids),
            "censored_selection_ids": sorted(self._censored_selection_ids),
            "router_snapshot": self.router.snapshot,
            "head_sha256": self.head_sha256,
        }
        return {**payload, "snapshot_sha256": content_sha256(payload)}

    @classmethod
    def from_snapshot(cls, value: Mapping[str, object]) -> "EcologyRuntime":
        required = {
            "schema_version", "model_id", "rules", "rules_sha256", "initial_router_snapshot", "events", "failures", "skills",
            "settled_receipt_ids", "censored_selection_ids", "router_snapshot", "head_sha256", "snapshot_sha256",
        }
        if set(value) != required:
            raise ValueError("ecology runtime snapshot must use a closed schema")
        payload = dict(value)
        claimed = payload.pop("snapshot_sha256")
        if value["schema_version"] != SCHEMA_VERSION or content_sha256(payload) != claimed:
            raise ValueError("ecology runtime snapshot hash/schema mismatch")
        initial = value["initial_router_snapshot"]
        if not isinstance(initial, Mapping):
            raise ValueError("initial router snapshot is invalid")
        rules_raw = value["rules"]
        if not isinstance(rules_raw, Mapping):
            raise ValueError("ecology rules are invalid")
        rules = EcologyRules(**dict(rules_raw))
        if rules.rules_sha256 != value["rules_sha256"]:
            raise ValueError("ecology rules hash mismatch")
        runtime = cls(cls._router_from_snapshot(initial), model_id=str(value["model_id"]), rules=rules)
        runtime._initial_router_snapshot = dict(initial)
        events = value["events"]
        if not isinstance(events, list):
            raise ValueError("events must be a list")
        runtime._events = [dict(row) for row in events if isinstance(row, Mapping)]
        if len(runtime._events) != len(events):
            raise ValueError("event ledger rows must be mappings")
        runtime._verify_event_chain()
        runtime._replay_state()
        expected_failures = [row.to_mapping() for _, row in sorted(runtime._failures.items())]
        expected_skills = [row.to_mapping() for _, row in sorted(runtime._skills.items())]
        if expected_failures != value["failures"] or expected_skills != value["skills"]:
            raise ValueError("snapshot materialized state disagrees with replay")
        if sorted(runtime._settled_receipt_ids) != value["settled_receipt_ids"]:
            raise ValueError("snapshot settled receipts disagree with replay")
        if sorted(runtime._censored_selection_ids) != value["censored_selection_ids"]:
            raise ValueError("snapshot censor state disagrees with replay")
        router_snapshot = value["router_snapshot"]
        if not isinstance(router_snapshot, Mapping):
            raise ValueError("router snapshot is invalid")
        if runtime.router.snapshot != dict(router_snapshot):
            raise ValueError("router snapshot disagrees with deterministic replay")
        if runtime.head_sha256 != value["head_sha256"]:
            raise ValueError("snapshot head does not match event chain")
        return runtime

    def _replay_state(self) -> None:
        """Rebuild coordinator state from the chain; no snapshot field is trusted."""
        self._failures = {}
        self._skills = {}
        self._selections = {}
        self._receipts = {}
        self._settled_receipt_ids = set()
        self._censored_selection_ids = set()
        for event in self._events:
            payload = event["payload"]
            if not isinstance(payload, Mapping):
                raise ValueError("event payload must be a mapping")
            kind = event["event_type"]
            if kind == "failure_deposit":
                failure = self._failure_from_mapping(payload)
                if failure.failure_id in self._failures:
                    raise ValueError("replay repeats a FailureMemory deposit")
                self._failures[failure.failure_id] = failure
            elif kind in {"skill_birth", "frozen_skill_seed"}:
                record = self._record_from_mapping(payload)
                if record.content.skill_revision_id in self._skills:
                    raise ValueError("replay repeats a skill birth")
                self._skills[record.content.skill_revision_id] = record
            elif kind in {"skill_promotion", "skill_quarantine", "skill_retire"}:
                record_raw = payload.get("record")
                if not isinstance(record_raw, Mapping):
                    raise ValueError("lifecycle event lacks its immutable record")
                record = self._record_from_mapping(record_raw)
                if record.content.skill_revision_id not in self._skills:
                    raise ValueError("lifecycle event precedes skill birth")
                self._skills[record.content.skill_revision_id] = record
            elif kind == "skill_supersede":
                record_raw = payload.get("record")
                if not isinstance(record_raw, Mapping):
                    raise ValueError("supersession lacks its transition record")
                record = self._record_from_mapping(record_raw)
                if record.content.skill_revision_id not in self._skills:
                    raise ValueError("supersession lacks its parent")
                self._skills[record.content.skill_revision_id] = record
            elif kind == "selection_registered":
                registered = self._registered_from_mapping(payload)
                key = registered.selection.selection_id
                if key in self._selections:
                    raise ValueError("replay repeats a selection")
                self._selections[key] = registered
                self._restore_one_pending_router_selection(registered)
            elif kind == "receipt_received":
                receipt = EccRepairReceipt.from_mapping(payload)
                selection = self._selections.get(receipt.selection_id)
                if selection is None or receipt.receipt_id in self._receipts:
                    raise ValueError("replay receipt binding is invalid")
                self._validate_receipt_binding(selection, receipt)
                self._receipts[receipt.receipt_id] = receipt
            elif kind == "receipt_settled":
                receipt_id = str(payload.get("receipt_id"))
                if receipt_id not in self._receipts or receipt_id in self._settled_receipt_ids:
                    raise ValueError("replay settlement is invalid")
                feedback_raw = payload.get("feedback")
                if not isinstance(feedback_raw, Mapping):
                    raise ValueError("replay settlement lacks feedback")
                feedback = DelayedOutcomeFeedback(**dict(feedback_raw))
                receipt = self._receipts[receipt_id]
                if feedback.selection_id != receipt.selection_id:
                    raise ValueError("replay feedback receipt binding is invalid")
                self.router.observe(self._selections[receipt.selection_id].selection, feedback)  # type: ignore[arg-type]
                self._settled_receipt_ids.add(receipt_id)
                self._record_evidence(
                    self._selections[receipt.selection_id], receipt,
                    settled_at_event=int(event["event_index"]),
                )
            elif kind == "right_censored":
                ids = payload.get("censored_selection_ids")
                if not isinstance(ids, list):
                    raise ValueError("right-censor event is invalid")
                self._censored_selection_ids.update(str(row) for row in ids)

    def _restore_one_pending_router_selection(self, registered: RegisteredSelection) -> None:
        if isinstance(self.router, GHOSTEcologyRouter):
            self.router.restore_pending(registered.selection, registered.failure, registered.skills)  # type: ignore[arg-type]
        else:
            # ObservableResidualGHOSTRouter has no public restore hook, but
            # this is its documented pending-selection representation.
            self.router._pending[registered.selection.selection_id] = (registered.selection, registered.failure, registered.skills)  # type: ignore[attr-defined]

    def _append(self, event_type: str, event_index: int, payload: Mapping[str, object]) -> None:
        if isinstance(event_index, bool) or not isinstance(event_index, int) or event_index < 0:
            raise ValueError("event_index must be a non-negative integer")
        body = {
            "schema_version": SCHEMA_VERSION, "ordinal": len(self._events), "event_type": event_type,
            "event_index": event_index, "previous_event_sha256": self.head_sha256, "payload": dict(payload),
        }
        self._events.append({**body, "event_sha256": content_sha256(body)})

    def _verify_event_chain(self) -> None:
        previous = GENESIS_SHA256
        for ordinal, row in enumerate(self._events):
            body = {key: row.get(key) for key in ("schema_version", "ordinal", "event_type", "event_index", "previous_event_sha256", "payload")}
            if (
                set(row) != set(body) | {"event_sha256"}
                or body["schema_version"] != SCHEMA_VERSION or body["ordinal"] != ordinal
                or body["previous_event_sha256"] != previous or row["event_sha256"] != content_sha256(body)
            ):
                raise ValueError("ecology event hash-chain integrity failure")
            previous = str(row["event_sha256"])

    def _validate_receipt_binding(self, selection: RegisteredSelection, receipt: EccRepairReceipt) -> None:
        selected = selection.selection.selected_skill_revision_id
        if (
            receipt.selected_skill_revision_id != selected
            or receipt.probe_id != next(skill.success_probe["probe_id"] for skill in selection.skills if skill.skill_revision_id == selected)
            or receipt.observed_after_event_index <= selection.selected_at_event_index
        ):
            raise ValueError("receipt is not bound to the original selected action")

    def _record_evidence(
        self, selection: RegisteredSelection, receipt: EccRepairReceipt, *,
        settled_at_event: int,
    ) -> None:
        record = self._skills.get(receipt.selected_skill_revision_id)
        if record is None:
            return
        evidence = record.evidence
        successful = receipt.committed and not receipt.rolled_back and not receipt.safety_violation and not receipt.recurrence_after_commit
        updated = SkillEvidence(
            evidence.skill_revision_id, max(evidence.valid_after_event, settled_at_event + 1),
            tuple(sorted((*evidence.settled_receipt_ids, receipt.receipt_id))),
            tuple(sorted((*evidence.successful_receipt_ids, receipt.receipt_id))) if successful else evidence.successful_receipt_ids,
            tuple(sorted((*evidence.rollback_receipt_ids, receipt.receipt_id))) if receipt.rolled_back else evidence.rollback_receipt_ids,
            tuple(sorted((*evidence.safety_receipt_ids, receipt.receipt_id))) if receipt.safety_violation else evidence.safety_receipt_ids,
        )
        self._skills[receipt.selected_skill_revision_id] = replace(record, evidence=updated)

    def _automatic_quarantine_reason(self, receipt: EccRepairReceipt) -> str | None:
        if self.rules.quarantine_on_safety_violation and receipt.safety_violation:
            return "frozen_rule:safety_violation"
        if self.rules.quarantine_on_provenance_violation and bool(
            receipt.provenance.get(self.rules.provenance_violation_field)
        ):
            return "frozen_rule:provenance_violation"
        if self.rules.quarantine_on_integrity_violation and bool(
            receipt.provenance.get(self.rules.integrity_violation_field)
        ):
            return "frozen_rule:integrity_violation"
        return None

    def _promotion_evidence(self, skill_revision_id: str) -> tuple[PromotionEvidence, ...]:
        rows: list[PromotionEvidence] = []
        for receipt_id in self._settled_receipt_ids:
            receipt = self._receipts[receipt_id]
            selection = self._selections[receipt.selection_id]
            if receipt.selected_skill_revision_id == skill_revision_id:
                rows.append(PromotionEvidence(
                    feedback_id=receipt_id, failure_id=selection.failure.failure_id,
                    family_id_audit_only=selection.failure.family_id_audit_only,
                    skill_revision_id=skill_revision_id,
                    success=receipt.committed and not receipt.safety_violation and not receipt.recurrence_after_commit,
                    rolled_back=receipt.rolled_back, gold_derived=False,
                ))
        return tuple(rows)

    def _require_status(self, skill_revision_id: str, status: SkillStatus) -> SkillLifecycleRecord:
        record = self._skills.get(skill_revision_id)
        if record is None or record.status is not status:
            raise PermissionError(f"skill must be {status.value}")
        return record

    @staticmethod
    def _router_from_snapshot(snapshot: Mapping[str, object]) -> GHOSTEcologyRouter | ObservableResidualGHOSTRouter:
        schema = snapshot.get("schema_version")
        if schema == "cmd-ghost-ecology-posterior-v3":
            return GHOSTEcologyRouter.from_snapshot(snapshot)
        if schema == "cmd-observable-residual-ghost-posterior-v1":
            return ObservableResidualGHOSTRouter.from_snapshot(snapshot)
        raise ValueError("unsupported GHOST router snapshot")

    @staticmethod
    def _failure_from_mapping(value: Mapping[str, object]) -> FailureDeposit:
        return FailureDeposit(
            str(value["failure_id"]), str(value["case_id"]), str(value["family_id_audit_only"]),
            str(value["failure_memory_sha256"]), tuple((str(row[0]), float(row[1])) for row in value["features"]),  # type: ignore[index]
            str(value["context_sha256"]), str(value["provenance_sha256"]),
        )

    @staticmethod
    def _record_from_mapping(value: Mapping[str, Any]) -> SkillLifecycleRecord:
        raw = dict(value["content"])
        raw.pop("program_sha256", None)
        skill = SkillRevision(
            raw["skill_revision_id"], raw["skill_id"], raw["program"], raw["parameter_schema"],
            tuple(raw["preconditions"]), tuple(raw["postconditions"]), raw["success_probe"], raw["mutation_budget"],
            raw["rollback_program"], tuple(raw["parent_revision_ids"]), raw["derivation_kind"], raw["producing_failure_id"], raw["state"],
        )
        evidence = SkillEvidence(**value["evidence"])
        previous = value["previous_status"]
        return SkillLifecycleRecord(
            skill, SkillStatus(value["status"]), int(value["effective_after_event"]), evidence,
            int(value["transition_effective_after_event"]),
            None if previous is None else SkillStatus(previous), value["superseded_by"],
            value["quarantine_reason"], value["retirement_reason"],
        )

    @staticmethod
    def _selection_mapping(registered: RegisteredSelection) -> dict[str, object]:
        return {
            "selection_kind": type(registered.selection).__name__,
            "selection": asdict(registered.selection),
            "failure": registered.failure.to_mapping(),
            "skills": [skill.to_mapping() for skill in registered.skills],
            "pre_action_prior": registered.pre_action_prior,
        }

    @classmethod
    def _registered_from_mapping(cls, value: Mapping[str, object]) -> RegisteredSelection:
        raw_selection = value.get("selection")
        raw_failure = value.get("failure")
        raw_skills = value.get("skills")
        kind = value.get("selection_kind")
        if not isinstance(raw_selection, Mapping) or not isinstance(raw_failure, Mapping) or not isinstance(raw_skills, list):
            raise ValueError("registered selection payload is invalid")
        responsibilities = tuple(
            PatternResponsibility(
                str(row["pattern_revision_id"]), float(row["responsibility"])
            )
            for row in raw_selection["pattern_responsibilities"]  # type: ignore[index]
        )
        scores = tuple((str(row[0]), float(row[1])) for row in raw_selection["scores"])  # type: ignore[index]
        if kind == "EcologySelection":
            selection: EcologySelection | ObservableResidualSelection = EcologySelection(
                str(raw_selection["selection_id"]), int(raw_selection["event_index"]), str(raw_selection["failure_id"]),
                str(raw_selection["registry_id"]), tuple(raw_selection["candidate_skill_revision_ids"]),
                str(raw_selection["selected_skill_revision_id"]), responsibilities, scores,
                str(raw_selection["posterior_before_sha256"]),
            )
        elif kind == "ObservableResidualSelection":
            selected = raw_selection["selected_skill_revision_id"]
            selection = ObservableResidualSelection(
                str(raw_selection["selection_id"]), int(raw_selection["event_index"]), str(raw_selection["failure_id"]),
                str(raw_selection["registry_id"]), tuple(raw_selection["candidate_skill_revision_ids"]),
                None if selected is None else str(selected), responsibilities, scores,
                str(raw_selection["posterior_before_sha256"]), raw_selection["base_selected_skill_revision_id"],
                str(raw_selection["selection_mode"]), bool(raw_selection["exploration_activated"]),
                tuple(raw_selection["active_levels"]),
            )
        else:
            raise ValueError("unknown registered selection type")
        skills = tuple(cls._record_from_mapping({
            "content": row, "status": SkillStatus.ACTIVE.value, "effective_after_event": 0,
            "evidence": {"skill_revision_id": row["skill_revision_id"], "valid_after_event": 0},
            "transition_effective_after_event": 0, "previous_status": None,
            "superseded_by": None, "quarantine_reason": None, "retirement_reason": None,
        }).content for row in raw_skills if isinstance(row, Mapping))
        if len(skills) != len(raw_skills):
            raise ValueError("registered selection skills are invalid")
        return RegisteredSelection(selection, cls._failure_from_mapping(raw_failure), skills, float(value["pre_action_prior"]))


__all__ = [
    "EcologyRuntime", "RegisteredSelection", "SCHEMA_VERSION", "Settlement",
    "SkillEvidence", "SkillLifecycleRecord", "SkillStatus",
]
