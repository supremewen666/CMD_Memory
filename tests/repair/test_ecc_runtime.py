from __future__ import annotations

import pytest

from cmd_audit.repair.ecc import Contract, EccRepairReceipt, MemAuditEccAdapter
from cmd_audit.repair.incident_store import IncidentLedger
from cmd_audit.repair.incident_triage import (
    IncidentMechanism,
    ProcessFaultSubtype,
    RepairFamily,
)


def _observation(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "observation_id": "obs-1",
        "incident_id": "incident-1",
        "observed_at_event_index": 4,
        "state_root": "state-before",
        "source_manifest_root": "manifest-root",
        "process_fault_subtype": "retrieval",
        "observed_order": [],
        "superseding_memory_id": None,
        "superseded_memory_id": None,
        "cas_anomaly": False,
        "influence_anomaly": False,
        "suspect_ids": [],
        "signal_ids": ["retrieval-miss"],
        "provenance": {"detector": "runtime-telemetry-v1"},
    }
    value.update(updates)
    return value


def test_runtime_observation_with_gold_field_fails_before_decode() -> None:
    observation = _observation(gold_answer="sealed")
    with pytest.raises(ValueError, match="runtime evidence boundary"):
        MemAuditEccAdapter().decode(observation)

    with pytest.raises(ValueError, match="gold-free"):
        MemAuditEccAdapter().decode(
            _observation(provenance={"audit": {"gold_answer": "sealed"}})
        )


def test_process_fault_signals_decode_to_one_typed_ecc_syndrome() -> None:
    syndrome = MemAuditEccAdapter().decode(_observation())
    assert isinstance(syndrome, Contract)
    assert syndrome.mechanism is IncidentMechanism.PROCESS_FAULT
    assert syndrome.repair_family is RepairFamily.PIPELINE_PATCH
    assert syndrome.process_fault_subtype is ProcessFaultSubtype.RETRIEVAL
    assert syndrome.content_hash == Contract.from_mapping(
        syndrome.to_mapping()
    ).content_hash


def test_three_incident_mechanisms_are_exclusive_at_decode_boundary() -> None:
    adapter = MemAuditEccAdapter()
    drift = adapter.decode(
        _observation(
            process_fault_subtype=None,
            observed_order=["new", "old"],
            superseding_memory_id="new",
            superseded_memory_id="old",
            signal_ids=["trusted-order"],
        )
    )
    assert drift.mechanism is IncidentMechanism.STATE_DRIFT
    assert drift.repair_family is RepairFamily.SUPERSEDE_AND_LOG

    poison = adapter.decode(
        _observation(
            process_fault_subtype=None,
            cas_anomaly=True,
            influence_anomaly=True,
            suspect_ids=["bad-memory"],
            signal_ids=["cas", "ecc-influence"],
        )
    )
    assert poison.mechanism is IncidentMechanism.ADVERSARIAL_POISON
    assert poison.repair_family is RepairFamily.QUARANTINE_AND_AUDIT

    with pytest.raises(ValueError, match="exactly one incident mechanism"):
        adapter.decode(
            _observation(
                observed_order=["new", "old"],
                superseding_memory_id="new",
                superseded_memory_id="old",
            )
        )


def test_adapter_persists_poison_only_to_quarantine_sink(tmp_path) -> None:
    ledger = IncidentLedger(tmp_path / "incidents.jsonl")
    event = MemAuditEccAdapter().append_incident(
        ledger,
        _observation(
            process_fault_subtype=None,
            cas_anomaly=True,
            influence_anomaly=True,
            suspect_ids=["bad-memory"],
            signal_ids=["cas", "ecc-influence"],
        ),
    )
    assert event["mechanism"] == "adversarial_poison"
    assert ledger.views.quarantined_ids == frozenset({"bad-memory"})
    assert ledger.views.process_faults == ()
    assert ledger.views.lineage == ()


def _receipt_mapping(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "receipt_id": "receipt-1",
        "syndrome_id": "syndrome-1",
        "incident_id": "incident-1",
        "selection_id": "selection-1",
        "selected_skill_revision_id": "skill-1",
        "probe_id": "ecc-parity-v1",
        "observed_after_event_index": 5,
        "before_root": "root-before",
        "shadow_root": "root-shadow",
        "after_root": "root-shadow",
        "resolved_syndrome": True,
        "invariants_passed": True,
        "committed": True,
        "rolled_back": False,
        "safety_violation": False,
        "locality_cost": 0.1,
        "recurrence_after_commit": False,
        "provenance": {"evaluator": "ecc-v1"},
    }
    value.update(updates)
    return value


def test_repair_receipt_is_closed_root_bound_and_gold_free() -> None:
    receipt = EccRepairReceipt.from_mapping(_receipt_mapping())
    assert receipt.after_root == receipt.shadow_root
    assert receipt.reward == pytest.approx(0.9)
    assert EccRepairReceipt.from_mapping(receipt.to_mapping()).content_hash == receipt.content_hash

    for forbidden in ("gold_answer", "perturbation_label", "answer_replay"):
        with pytest.raises(ValueError, match="closed"):
            EccRepairReceipt.from_mapping(_receipt_mapping(**{forbidden: "sealed"}))
    with pytest.raises(ValueError, match="gold-free"):
        EccRepairReceipt.from_mapping(
            _receipt_mapping(provenance={"audit": {"perturbation_label": "poison"}})
        )


def test_repair_receipt_refuses_commit_without_ecc_acceptance() -> None:
    for updates in (
        {"resolved_syndrome": False},
        {"invariants_passed": False},
        {"safety_violation": True},
        {"after_root": "root-before"},
        {"rolled_back": True},
    ):
        with pytest.raises(ValueError, match="committed receipt"):
            EccRepairReceipt.from_mapping(_receipt_mapping(**updates))

    rollback = EccRepairReceipt.from_mapping(
        _receipt_mapping(
            resolved_syndrome=False,
            invariants_passed=False,
            committed=False,
            rolled_back=True,
            after_root="root-before",
        )
    )
    assert rollback.reward == -1.0


class _ShadowStore:
    def __init__(self) -> None:
        self.root = "state-before"
        self.commits = 0
        self.rollbacks = 0

    def snapshot_root(self) -> str:
        return self.root

    def apply_shadow(
        self, syndrome: Contract, selected_skill_revision_id: str
    ) -> None:
        assert syndrome.state_root == "state-before"
        assert selected_skill_revision_id == "skill-1"
        self.root = "state-shadow"

    def commit_shadow(self) -> None:
        self.commits += 1

    def rollback_shadow(self, before_root: str) -> None:
        self.rollbacks += 1
        self.root = before_root


class _EccEvaluator:
    def __init__(self, *, passes: bool) -> None:
        self.passes = passes
        self.ecc_calls = 0
        self.answer_replay_calls = 0

    def evaluate_ecc(
        self, syndrome: Contract, *, before_root: str, shadow_root: str
    ) -> dict[str, object]:
        self.ecc_calls += 1
        assert syndrome.syndrome_id
        assert before_root == "state-before"
        assert shadow_root == "state-shadow"
        return {
            "resolved_syndrome": self.passes,
            "invariants_passed": self.passes,
            "safety_violation": False,
            "locality_cost": 0.05,
            "recurrence_after_commit": False,
            "provenance": {"checker": "ecc-v1"},
        }

    def replay_answer(self, *_args: object, **_kwargs: object) -> None:
        self.answer_replay_calls += 1
        raise AssertionError("same-trace answer replay is forbidden")


def test_shadow_ecc_pass_commits_without_same_trace_answer_replay() -> None:
    adapter = MemAuditEccAdapter()
    syndrome = adapter.decode(_observation(state_root="state-before"))
    store = _ShadowStore()
    evaluator = _EccEvaluator(passes=True)

    receipt = adapter.execute_shadow_repair(
        syndrome,
        selection_id="selection-1",
        selected_skill_revision_id="skill-1",
        probe_id="probe:skill-1",
        observed_after_event_index=5,
        store=store,
        evaluator=evaluator,
    )

    assert receipt.committed is True
    assert store.root == "state-shadow" and store.commits == 1
    assert store.rollbacks == 0
    assert evaluator.ecc_calls == 1 and evaluator.answer_replay_calls == 0


def test_shadow_ecc_failure_rolls_back_and_cannot_commit() -> None:
    adapter = MemAuditEccAdapter()
    syndrome = adapter.decode(_observation(state_root="state-before"))
    store = _ShadowStore()
    evaluator = _EccEvaluator(passes=False)

    receipt = adapter.execute_shadow_repair(
        syndrome,
        selection_id="selection-1",
        selected_skill_revision_id="skill-1",
        probe_id="probe:skill-1",
        observed_after_event_index=5,
        store=store,
        evaluator=evaluator,
    )

    assert receipt.committed is False and receipt.rolled_back is True
    assert receipt.after_root == receipt.before_root == "state-before"
    assert store.commits == 0 and store.rollbacks == 1
