"""Incident triage: three mutually exclusive repair mechanisms.

A memory incident is not one kind of thing, and the three kinds demand different
and *partly irreversible* actions:

* ``process_fault`` — the pipeline genuinely failed (retrieval / injection /
  granularity / safety).  Repair family: pipeline patch, re-run.  These are the
  only incidents that belong in ``FailureMemory``.
* ``state_drift`` — the world or the user changed; the memory was *correct when
  written* (burger -> pizza).  Repair family: supersede + lineage log.  Nothing
  here is a failure, and calling it one corrupts the failure store.
* ``adversarial_poison`` — someone wrote something bad in.  Repair family:
  quarantine + audit.

"Failure" always means "the system failed" and never "the world changed".  That
naming rule is the point of this module: ``FailureMemory`` keeps its name and
narrows its admission, drift goes to a lineage log, poison goes to an audit
queue.

**Vocabulary.** Drift uses AGM belief-revision terms, which already exist for
this: ``expansion`` (add, no conflict), ``revision`` (supersede a conflicting
prior), ``contraction`` (retract).  ``supersession`` is revision.

**What this module does NOT claim.** Triage is a *routing* decision over signals
that are visible without gold.  It is not a drift detector, and the distinction
matters because this project has already retired two drift sensors:

1. ``CONTRADICTS`` — 0/900 firings across three domains; the negation-polarity
   gate intercepts same-slot updates before the predicate sees them.
2. ``slot_divergence`` via ``MemoryItem.store`` — withdrawn: ``store`` is a
   *construction marker* bijective with ``memory_id`` on every probe fixture.

A third candidate, ``ProvenanceEdge.timestamp``, is a real write-time field but
is **unpopulated**: 0 of 1272 sampled items across ``evolution_v4`` runtime cases
and two probe suites carry any provenance edge at all.  ``TEMPORAL_DOMINATES``
in the frozen ``state_executor`` reads *rank order*, not time, for the same
reason — the runtime surface has no timestamp.

So there is no live temporal sensor, and this module does not invent one.
:func:`triage_incident` routes on an **explicitly supplied** ``observed_at``
ordering when the substrate provides one, and otherwise **abstains into
``process_fault``** — the conservative branch, because a mis-routed drift merely
stays in the failure store where today's pipeline already handles it, whereas a
mis-routed fault would be silently superseded and never repaired.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Sequence
import hashlib
import json

from cmd_audit.core.models import MemoryItem
from cmd_audit.repair.failure_memory import FailureMemoryRecord, FailureMemoryStore


TRIAGE_SCHEMA_VERSION = "cmd-incident-triage-v1"

#: Fields whose values are construction artifacts on the current fixtures and
#: must never be read as evidence of when a memory was written.
FORBIDDEN_TEMPORAL_SOURCES = ("store", "memory_id", "source_event_ids")


class IncidentMechanism(str, Enum):
    """The three mutually exclusive mechanisms."""

    PROCESS_FAULT = "process_fault"
    STATE_DRIFT = "state_drift"
    ADVERSARIAL_POISON = "adversarial_poison"


class RepairFamily(str, Enum):
    """The action family each mechanism admits.

    Distinct because the actions differ in reversibility: re-running a pipeline
    is cheap and repeatable, superseding demotes a prior that must stay
    auditable, and quarantine removes content.
    """

    PIPELINE_PATCH = "pipeline_patch"
    SUPERSEDE_AND_LOG = "supersede_and_log"
    QUARANTINE_AND_AUDIT = "quarantine_and_audit"


#: Mechanism -> repair family.  One-to-one and total, so a mechanism can never
#: be routed to an action family it does not admit.
MECHANISM_REPAIR_FAMILY: Mapping[IncidentMechanism, RepairFamily] = {
    IncidentMechanism.PROCESS_FAULT: RepairFamily.PIPELINE_PATCH,
    IncidentMechanism.STATE_DRIFT: RepairFamily.SUPERSEDE_AND_LOG,
    IncidentMechanism.ADVERSARIAL_POISON: RepairFamily.QUARANTINE_AND_AUDIT,
}


class RevisionKind(str, Enum):
    """AGM belief-revision operation for the drift lineage log."""

    EXPANSION = "expansion"
    REVISION = "revision"
    CONTRACTION = "contraction"


class TriageError(ValueError):
    """Raised when a triage input cannot be routed honestly."""


@dataclass(frozen=True)
class TriageDecision:
    """One routing decision, with the reason it was routed that way."""

    mechanism: IncidentMechanism
    repair_family: RepairFamily
    reason: str
    #: True when the drift branch was unavailable because no observed ordering
    #: was supplied.  A reader must be able to tell "not drift" from "could not
    #: tell", and these are different claims.
    drift_sensor_available: bool
    admits_to_failure_memory: bool

    def __post_init__(self) -> None:
        expected = MECHANISM_REPAIR_FAMILY[self.mechanism]
        if self.repair_family is not expected:
            raise TriageError(
                f"{self.mechanism.value} does not admit {self.repair_family.value}"
            )
        admits = self.mechanism is IncidentMechanism.PROCESS_FAULT
        if self.admits_to_failure_memory is not admits:
            raise TriageError(
                "only process_fault may enter FailureMemory; "
                f"{self.mechanism.value} may not"
            )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": TRIAGE_SCHEMA_VERSION,
            "mechanism": self.mechanism.value,
            "repair_family": self.repair_family.value,
            "reason": self.reason,
            "drift_sensor_available": self.drift_sensor_available,
            "admits_to_failure_memory": self.admits_to_failure_memory,
        }

    def assert_exclusive(self) -> None:
        """Fail closed if a caller tries to attach a second mechanism."""
        if self.repair_family is not MECHANISM_REPAIR_FAMILY[self.mechanism]:
            raise TriageError("incident mechanism has a cross-type repair family")


@dataclass(frozen=True)
class IncidentAuditRecord:
    """Append-only provenance envelope for one triage decision."""
    incident_id: str
    decision: TriageDecision
    provenance: Mapping[str, object]
    audit_sha256: str

    @classmethod
    def create(cls, incident_id: str, decision: TriageDecision,
               provenance: Mapping[str, object]) -> "IncidentAuditRecord":
        decision.assert_exclusive()
        payload = {"schema_version": TRIAGE_SCHEMA_VERSION, "incident_id": incident_id,
                   "decision": decision.to_mapping(), "provenance": dict(provenance)}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return cls(incident_id, decision, dict(provenance), digest)

    def to_mapping(self) -> dict[str, object]:
        return {"schema_version": TRIAGE_SCHEMA_VERSION, "incident_id": self.incident_id,
                "decision": self.decision.to_mapping(), "provenance": dict(self.provenance),
                "audit_sha256": self.audit_sha256}


@dataclass(frozen=True)
class LineageEntry:
    """One append-only drift record: what superseded what, and why.

    This is the second vertical DAG — a *supersession* chain, not a failure
    record.  The superseded item is retained: drift means the prior was correct
    when written, so deleting it would destroy the audit trail that justifies
    the revision.
    """

    superseding_memory_id: str
    superseded_memory_id: str
    revision_kind: RevisionKind
    observed_order: tuple[str, ...]
    reason: str
    parent_entry_id: str | None = None

    def __post_init__(self) -> None:
        if not self.superseding_memory_id or not self.superseded_memory_id:
            raise TriageError("a lineage entry needs both memory ids")
        if self.superseding_memory_id == self.superseded_memory_id:
            raise TriageError("an item cannot supersede itself")
        if self.revision_kind is RevisionKind.EXPANSION:
            raise TriageError(
                "expansion adds without conflict, so it has nothing to supersede"
            )

    def to_mapping(self) -> dict[str, object]:
        return {
            "superseding_memory_id": self.superseding_memory_id,
            "superseded_memory_id": self.superseded_memory_id,
            "revision_kind": self.revision_kind.value,
            "observed_order": list(self.observed_order),
            "reason": self.reason,
            "parent_entry_id": self.parent_entry_id,
        }


def _poisoned_ids(recall_set: Sequence[MemoryItem]) -> tuple[str, ...]:
    """Items carrying a poison signature, reusing the live detector."""
    from cmd_audit.counterfactual.actions import _is_poisoned_item

    return tuple(item.memory_id for item in recall_set if _is_poisoned_item(item))


def validate_observed_order(
    observed_order: Sequence[str], recall_set: Sequence[MemoryItem]
) -> tuple[str, ...]:
    """Check a caller-supplied write ordering is usable and not a shortcut.

    The ordering must come from the substrate's own observation record.  An
    ordering that merely reproduces fixture construction order would make the
    drift branch fire on the fixture rather than on the world, which is exactly
    how the previous two sensors died — so an ordering identical to the recall
    set's own order is refused.
    """
    order = tuple(observed_order)
    if not order:
        return ()
    known = {item.memory_id for item in recall_set}
    unknown = [row for row in order if row not in known]
    if unknown:
        raise TriageError(f"observed_order names unknown items: {sorted(unknown)}")
    if len(set(order)) != len(order):
        raise TriageError("observed_order must not repeat an item")
    if len(order) < 2:
        raise TriageError("an ordering needs at least two items to be informative")
    recall_order = tuple(item.memory_id for item in recall_set)
    if order == recall_order[: len(order)]:
        raise TriageError(
            "observed_order duplicates recall order, so it carries no "
            "information beyond how the case was constructed"
        )
    return order


def triage_incident(
    recall_set: Sequence[MemoryItem],
    *,
    pipeline_recovered: bool,
    observed_order: Sequence[str] = (),
) -> TriageDecision:
    """Route one incident to exactly one mechanism.

    Order of precedence is by irreversibility of the mistake, not by likelihood:

    1. **poison** first — leaving injected content in place while a pipeline
       patch is attempted would let it influence the next generation.
    2. **drift** next, but only when an observed ordering is supplied *and*
       the pipeline could not recover; a recovered case had no incident to
       explain.
    3. **process_fault** otherwise, including whenever the drift sensor is
       unavailable.  Abstaining into this branch is the conservative choice: a
       mis-routed drift stays in the failure store where the existing pipeline
       handles it, whereas a mis-routed fault would be silently superseded.

    ``pipeline_recovered`` is a gold-free runtime observation (did the repaired
    context change the outcome), not a label.
    """
    if not recall_set:
        raise TriageError("triage requires a non-empty recall set")

    poisoned = _poisoned_ids(recall_set)
    if poisoned:
        return TriageDecision(
            mechanism=IncidentMechanism.ADVERSARIAL_POISON,
            repair_family=RepairFamily.QUARANTINE_AND_AUDIT,
            reason=f"poison signature on {len(poisoned)} item(s): {list(poisoned)}",
            drift_sensor_available=bool(observed_order),
            admits_to_failure_memory=False,
        )

    order = validate_observed_order(observed_order, recall_set)
    if order and not pipeline_recovered:
        return TriageDecision(
            mechanism=IncidentMechanism.STATE_DRIFT,
            repair_family=RepairFamily.SUPERSEDE_AND_LOG,
            reason=(
                "observed write ordering separates a later item from an earlier "
                f"one ({order[-1]} after {order[0]}) and the pipeline did not recover"
            ),
            drift_sensor_available=True,
            admits_to_failure_memory=False,
        )

    return TriageDecision(
        mechanism=IncidentMechanism.PROCESS_FAULT,
        repair_family=RepairFamily.PIPELINE_PATCH,
        reason=(
            "no poison signature and no observed write ordering available, so "
            "the incident is treated as a pipeline fault"
            if not order
            else "no poison signature and the pipeline recovered on its own"
        ),
        drift_sensor_available=bool(order),
        admits_to_failure_memory=True,
    )


@dataclass
class LineageLog:
    """Append-only supersession chain for ``state_drift`` incidents."""

    _entries: list[LineageEntry] = field(default_factory=list)

    @property
    def entries(self) -> tuple[LineageEntry, ...]:
        return tuple(self._entries)

    def append(self, entry: LineageEntry) -> str:
        """Record a supersession; returns the new entry id.

        A superseded item may not be superseded twice: the second revision
        should chain off the item that replaced it, or the log would no longer
        describe a single ordered chain.
        """
        for existing in self._entries:
            if existing.superseded_memory_id == entry.superseded_memory_id:
                raise TriageError(
                    f"{entry.superseded_memory_id} was already superseded; "
                    "chain from its successor instead"
                )
        entry_id = f"lineage-{len(self._entries)}"
        self._entries.append(entry)
        return entry_id

    def current_head(self, memory_id: str) -> str:
        """Follow the chain from ``memory_id`` to the item still in force."""
        successors = {
            row.superseded_memory_id: row.superseding_memory_id
            for row in self._entries
        }
        seen = {memory_id}
        head = memory_id
        while head in successors:
            head = successors[head]
            if head in seen:
                raise TriageError(f"supersession cycle at {head}")
            seen.add(head)
        return head

    def superseded_ids(self) -> frozenset[str]:
        return frozenset(row.superseded_memory_id for row in self._entries)


@dataclass
class IncidentTriageStores:
    """Thin adapter from the exclusive triage decision to live stores.

    The adapter deliberately accepts only gold-free text/ids.  Each branch has
    one concrete sink and rejects arguments belonging to another mechanism.
    """

    failure_memory: FailureMemoryStore = field(default_factory=FailureMemoryStore)
    lineage: LineageLog = field(default_factory=LineageLog)
    quarantined_ids: list[str] = field(default_factory=list)
    audit_records: list[IncidentAuditRecord] = field(default_factory=list)
    pipeline_patches: list[str] = field(default_factory=list)

    def apply(
        self,
        decision: TriageDecision,
        *,
        incident_id: str,
        recall_set: Sequence[MemoryItem],
        provenance: Mapping[str, object],
        patch_name: str | None = None,
        superseding_memory_id: str | None = None,
        superseded_memory_id: str | None = None,
    ) -> IncidentAuditRecord:
        decision.assert_exclusive()
        audit = IncidentAuditRecord.create(incident_id, decision, provenance)
        if decision.mechanism is IncidentMechanism.PROCESS_FAULT:
            if any(value is not None for value in (superseding_memory_id, superseded_memory_id)):
                raise TriageError("process_fault cannot carry lineage ids")
            record = FailureMemoryRecord(
                error_type="retrieval_error",
                wrong_memory=" | ".join(item.text for item in recall_set),
                original_evidence="",
                cause=decision.reason,
                corrected_memory="",
                repair_action=patch_name or "pipeline_patch",
                repair_guidance=patch_name or "apply the registered pipeline patch",
                trigger_signature=incident_id,
            )
            self.failure_memory.add(record)
            self.pipeline_patches.append(patch_name or "pipeline_patch")
        elif decision.mechanism is IncidentMechanism.STATE_DRIFT:
            if not superseding_memory_id or not superseded_memory_id:
                raise TriageError("state_drift requires supersession ids")
            if patch_name is not None:
                raise TriageError("state_drift cannot carry a pipeline patch")
            self.lineage.append(LineageEntry(
                superseding_memory_id=superseding_memory_id,
                superseded_memory_id=superseded_memory_id,
                revision_kind=RevisionKind.REVISION,
                observed_order=tuple(item.memory_id for item in recall_set),
                reason=decision.reason,
            ))
        else:
            if any(value is not None for value in (patch_name, superseding_memory_id, superseded_memory_id)):
                raise TriageError("adversarial_poison cannot carry patch or lineage ids")
            self.quarantined_ids.extend(item.memory_id for item in recall_set)
        self.audit_records.append(audit)
        return audit


__all__ = [
    "FORBIDDEN_TEMPORAL_SOURCES",
    "MECHANISM_REPAIR_FAMILY",
    "TRIAGE_SCHEMA_VERSION",
    "IncidentMechanism",
    "IncidentAuditRecord",
    "LineageEntry",
    "LineageLog",
    "IncidentTriageStores",
    "RepairFamily",
    "RevisionKind",
    "TriageDecision",
    "TriageError",
    "triage_incident",
    "validate_observed_order",
]
