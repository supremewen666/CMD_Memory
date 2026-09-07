"""Contract tests for the durable incident kernel."""

from __future__ import annotations

import json

import pytest

from cmd_audit.repair.incident_store import IncidentLedger, IncidentLedgerError
from cmd_audit.repair.incident_triage import (
    ClassificationStatus, IncidentMechanism, ProcessFaultSubtype, RepairFamily,
    TriageDecision,
)


def decision(mechanism, *, status=ClassificationStatus.CONFIRMED, order=()):
    return TriageDecision(
        mechanism=mechanism,
        repair_family={
            IncidentMechanism.PROCESS_FAULT: RepairFamily.PIPELINE_PATCH,
            IncidentMechanism.STATE_DRIFT: RepairFamily.SUPERSEDE_AND_LOG,
            IncidentMechanism.ADVERSARIAL_POISON: RepairFamily.QUARANTINE_AND_AUDIT,
        }[mechanism],
        reason="test", drift_sensor_available=bool(order),
        admits_to_failure_memory=mechanism is IncidentMechanism.PROCESS_FAULT,
        classification_status=status,
        process_fault_subtype=(ProcessFaultSubtype.SAFETY if mechanism is IncidentMechanism.PROCESS_FAULT else None),
        observed_order=tuple(order),
    )


def append(ledger, event_id, d, **kwargs):
    return ledger.append(
        event_id=event_id, incident_id="i-" + event_id, decision=d,
        provenance={"producer": "test"}, syndrome={"S0": True, "why": "ok"},
        source_manifest_root="manifest-root", **kwargs,
    )


def test_replay_three_branches_and_targeted_quarantine(tmp_path):
    path = tmp_path / "incidents.jsonl"
    ledger = IncidentLedger(path)
    append(ledger, "fault", decision(IncidentMechanism.PROCESS_FAULT))
    append(ledger, "drift", decision(IncidentMechanism.STATE_DRIFT, order=("new", "old")),
           superseding_memory_id="new", superseded_memory_id="old")
    append(ledger, "poison", decision(IncidentMechanism.ADVERSARIAL_POISON), suspect_ids=("bad",))
    replayed = IncidentLedger(path)
    assert replayed.views.process_faults[0]["process_fault_subtype"] == "safety"
    assert replayed.views.lineage[0]["observed_order"] == ["new", "old"]
    assert replayed.views.quarantined_ids == frozenset({"bad"})


def test_provisional_is_audited_but_never_enters_sink(tmp_path):
    ledger = IncidentLedger(tmp_path / "incidents.jsonl")
    append(ledger, "p", decision(IncidentMechanism.ADVERSARIAL_POISON,
                                  status=ClassificationStatus.PROVISIONAL))
    assert len(ledger.events) == 1
    assert ledger.views.quarantined_ids == frozenset()


def test_idempotency_collision_and_tamper_fail_closed(tmp_path):
    path = tmp_path / "incidents.jsonl"
    ledger = IncidentLedger(path)
    first = append(ledger, "same", decision(IncidentMechanism.PROCESS_FAULT))
    assert append(ledger, "same", decision(IncidentMechanism.PROCESS_FAULT)) == first
    with pytest.raises(IncidentLedgerError, match="collision"):
        ledger.append(event_id="same", incident_id="other", decision=decision(IncidentMechanism.PROCESS_FAULT),
                      provenance={"producer": "test"}, syndrome={"S0": False}, source_manifest_root="manifest-root")
    row = json.loads(path.read_text().strip())
    row["reason"] = "tampered"
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(IncidentLedgerError, match="hash mismatch"):
        IncidentLedger(path)


def test_lineage_duplicate_and_cycle_rejected(tmp_path):
    ledger = IncidentLedger(tmp_path / "incidents.jsonl")
    d = decision(IncidentMechanism.STATE_DRIFT, order=("new", "old"))
    append(ledger, "one", d, superseding_memory_id="new", superseded_memory_id="old")
    with pytest.raises(IncidentLedgerError, match="already superseded"):
        append(ledger, "two", d, superseding_memory_id="other", superseded_memory_id="old")
    with pytest.raises(IncidentLedgerError, match="cycle"):
        append(ledger, "three", decision(IncidentMechanism.STATE_DRIFT, order=("old", "new")),
               superseding_memory_id="old", superseded_memory_id="new")


def test_mechanism_constraints_are_closed_schema(tmp_path):
    ledger = IncidentLedger(tmp_path / "incidents.jsonl")
    with pytest.raises(IncidentLedgerError, match="suspect"):
        append(ledger, "wrong", decision(IncidentMechanism.PROCESS_FAULT), suspect_ids=("bad",))
