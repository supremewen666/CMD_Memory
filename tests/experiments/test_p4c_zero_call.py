from __future__ import annotations

from pathlib import Path

import pytest

from cmd_audit.repair.ecc import MemAuditEccAdapter
from experiments.p4c_ecc_runner import P4cEccCase, P4cEccRunner
from experiments.p4c_zero_call import (
    P4cZeroCallScenario,
    P4cZeroCallSuite,
    StructuralEccEvaluator,
    StructuralMemoryStore,
)


def _process_fault_case() -> P4cEccCase:
    return P4cEccCase.from_mapping(
        {
            "schema_version": "cmd-p4c-ecc-case-v1",
            "case_id": "process-1",
            "event_index": 2,
            "observation": {
                "observation_id": "observation-process-1",
                "incident_id": "incident-process-1",
                "observed_at_event_index": 1,
                "state_root": "PLACEHOLDER",
                "source_manifest_root": "manifest-root",
                "process_fault_subtype": "retrieval",
                "observed_order": [],
                "superseding_memory_id": None,
                "superseded_memory_id": None,
                "cas_anomaly": False,
                "influence_anomaly": False,
                "suspect_ids": [],
                "signal_ids": ["retrieval-unavailable"],
                "provenance": {"detector": "structural-zero-call-v1"},
            },
            "candidates": [
                {
                    "skill_revision_id": "pipeline-patch",
                    "probe_id": "probe:pipeline-patch",
                    "operator_sha256": "a" * 64,
                }
            ],
        }
    )


class _Decision:
    selection_id = "selection-process-1"
    selected_skill_revision_id = "pipeline-patch"


class _ReceiptRouter:
    def select(self, case: object, syndrome: object) -> _Decision:
        return _Decision()

    def observe_receipt(
        self, decision: object, receipt: object, *, event_index: int
    ) -> dict[str, object]:
        return {"snapshot_sha256": "router-after-process"}


def test_zero_call_process_patch_commits_only_after_structural_ecc(
    tmp_path: Path,
) -> None:
    store = StructuralMemoryStore(
        state={
            "pipeline": {
                "retrieval": False,
                "injection": True,
                "granularity": True,
                "safety": True,
            },
            "memories": {},
            "lineage": [],
            "quarantine": [],
            "protected_ids": [],
        },
        operators={"pipeline-patch": {"kind": "pipeline_patch"}},
    )
    raw = _process_fault_case().to_mapping()
    raw["observation"]["state_root"] = store.snapshot_root()
    case = P4cEccCase.from_mapping(raw)

    result = P4cEccRunner(
        (case,),
        output_dir=tmp_path / "run",
        router=_ReceiptRouter(),
        store_factory=lambda _case: store,
        evaluator_factory=lambda _case: StructuralEccEvaluator(store),
    ).run()

    assert result["committed"] == 1
    assert result["rolled_back"] == 0
    assert store.state["pipeline"]["retrieval"] is True
    assert store.snapshot_root() != case.observation["state_root"]


def test_zero_call_state_drift_supersedes_old_memory_and_records_lineage(
    tmp_path: Path,
) -> None:
    store = StructuralMemoryStore(
        state={
            "pipeline": {
                "retrieval": True,
                "injection": True,
                "granularity": True,
                "safety": True,
            },
            "memories": {
                "memory-old": {"active": True},
                "memory-new": {"active": True},
            },
            "lineage": [],
            "quarantine": [],
            "protected_ids": [],
        },
        operators={"lineage-supersede": {"kind": "supersede_lineage"}},
    )
    case = P4cEccCase.from_mapping(
        {
            "schema_version": "cmd-p4c-ecc-case-v1",
            "case_id": "drift-1",
            "event_index": 4,
            "observation": {
                "observation_id": "observation-drift-1",
                "incident_id": "incident-drift-1",
                "observed_at_event_index": 3,
                "state_root": store.snapshot_root(),
                "source_manifest_root": "manifest-root",
                "process_fault_subtype": None,
                "observed_order": ["memory-old", "memory-new"],
                "superseding_memory_id": "memory-new",
                "superseded_memory_id": "memory-old",
                "cas_anomaly": False,
                "influence_anomaly": False,
                "suspect_ids": [],
                "signal_ids": ["active-version-conflict"],
                "provenance": {"detector": "structural-zero-call-v1"},
            },
            "candidates": [
                {
                    "skill_revision_id": "lineage-supersede",
                    "probe_id": "probe:lineage-supersede",
                    "operator_sha256": "b" * 64,
                }
            ],
        }
    )

    result = P4cEccRunner(
        (case,),
        output_dir=tmp_path / "run",
        router=type(
            "Router",
            (),
            {
                "select": lambda self, case, syndrome: type(
                    "Decision",
                    (),
                    {
                        "selection_id": "selection-drift-1",
                        "selected_skill_revision_id": "lineage-supersede",
                    },
                )(),
                "observe_receipt": lambda self, decision, receipt, event_index: {
                    "snapshot_sha256": "router-after-drift"
                },
            },
        )(),
        store_factory=lambda _case: store,
        evaluator_factory=lambda _case: StructuralEccEvaluator(store),
    ).run()

    assert result["committed"] == 1
    assert store.state["memories"]["memory-old"]["active"] is False
    assert store.state["memories"]["memory-new"]["active"] is True
    assert store.state["lineage"] == [["memory-old", "memory-new"]]


def _poison_case(store: StructuralMemoryStore, *, case_id: str) -> P4cEccCase:
    return P4cEccCase.from_mapping(
        {
            "schema_version": "cmd-p4c-ecc-case-v1",
            "case_id": case_id,
            "event_index": 6,
            "observation": {
                "observation_id": f"observation-{case_id}",
                "incident_id": f"incident-{case_id}",
                "observed_at_event_index": 5,
                "state_root": store.snapshot_root(),
                "source_manifest_root": "manifest-root",
                "process_fault_subtype": None,
                "observed_order": [],
                "superseding_memory_id": None,
                "superseded_memory_id": None,
                "cas_anomaly": True,
                "influence_anomaly": True,
                "suspect_ids": ["memory-suspect"],
                "signal_ids": ["cas-mismatch", "influence-spike"],
                "provenance": {"detector": "structural-zero-call-v1"},
            },
            "candidates": [
                {
                    "skill_revision_id": "poison-quarantine",
                    "probe_id": "probe:poison-quarantine",
                    "operator_sha256": "c" * 64,
                }
            ],
        }
    )


class _PoisonRouter:
    def select(self, case: object, syndrome: object) -> object:
        return type(
            "Decision",
            (),
            {
                "selection_id": "selection-poison",
                "selected_skill_revision_id": "poison-quarantine",
            },
        )()

    def observe_receipt(
        self, decision: object, receipt: object, *, event_index: int
    ) -> dict[str, object]:
        return {"snapshot_sha256": "router-after-poison"}


def test_zero_call_poison_is_deactivated_and_quarantined(tmp_path: Path) -> None:
    store = StructuralMemoryStore(
        state={
            "pipeline": {
                "retrieval": True,
                "injection": True,
                "granularity": True,
                "safety": True,
            },
            "memories": {"memory-suspect": {"active": True}},
            "lineage": [],
            "quarantine": [],
            "protected_ids": [],
        },
        operators={"poison-quarantine": {"kind": "quarantine_poison"}},
    )
    result = P4cEccRunner(
        (_poison_case(store, case_id="poison-1"),),
        output_dir=tmp_path / "run",
        router=_PoisonRouter(),
        store_factory=lambda _case: store,
        evaluator_factory=lambda _case: StructuralEccEvaluator(store),
    ).run()

    assert result["committed"] == 1
    assert store.state["memories"]["memory-suspect"]["active"] is False
    assert store.state["quarantine"] == ["memory-suspect"]


def test_zero_call_safety_violation_rolls_shadow_repair_back(tmp_path: Path) -> None:
    store = StructuralMemoryStore(
        state={
            "pipeline": {
                "retrieval": True,
                "injection": True,
                "granularity": True,
                "safety": True,
            },
            "memories": {"memory-suspect": {"active": True}},
            "lineage": [],
            "quarantine": [],
            "protected_ids": ["memory-suspect"],
        },
        operators={"poison-quarantine": {"kind": "quarantine_poison"}},
    )
    before_root = store.snapshot_root()

    result = P4cEccRunner(
        (_poison_case(store, case_id="poison-protected"),),
        output_dir=tmp_path / "run",
        router=_PoisonRouter(),
        store_factory=lambda _case: store,
        evaluator_factory=lambda _case: StructuralEccEvaluator(store),
    ).run()

    assert result["committed"] == 0
    assert result["rolled_back"] == 1
    assert store.snapshot_root() == before_root
    assert store.state["memories"]["memory-suspect"]["active"] is True
    assert store.state["quarantine"] == []


def test_zero_call_suite_writes_manifest_bound_mechanism_metrics(
    tmp_path: Path,
) -> None:
    process_state = {
        "pipeline": {
            "retrieval": False,
            "injection": True,
            "granularity": True,
            "safety": True,
        },
        "memories": {},
        "lineage": [],
        "quarantine": [],
        "protected_ids": [],
    }
    process_store = StructuralMemoryStore(
        state=process_state,
        operators={"pipeline-patch": {"kind": "pipeline_patch"}},
    )
    process_raw = _process_fault_case().to_mapping()
    process_raw["observation"]["state_root"] = process_store.snapshot_root()
    process_case = P4cEccCase.from_mapping(process_raw)

    poison_state = {
        "pipeline": {
            "retrieval": True,
            "injection": True,
            "granularity": True,
            "safety": True,
        },
        "memories": {"memory-suspect": {"active": True}},
        "lineage": [],
        "quarantine": [],
        "protected_ids": ["memory-suspect"],
    }
    poison_store = StructuralMemoryStore(
        state=poison_state,
        operators={"poison-quarantine": {"kind": "quarantine_poison"}},
    )
    poison_case = _poison_case(poison_store, case_id="poison-protected")

    class SuiteRouter:
        def select(self, case: P4cEccCase, syndrome: object) -> object:
            selected = {
                "process-1": "pipeline-patch",
                "poison-protected": "poison-quarantine",
            }[case.case_id]
            return type(
                "Decision",
                (),
                {
                    "selection_id": f"selection-{case.case_id}",
                    "selected_skill_revision_id": selected,
                },
            )()

        def observe_receipt(
            self, decision: object, receipt: object, *, event_index: int
        ) -> dict[str, object]:
            return {"snapshot_sha256": f"router-after-{event_index}"}

    report = P4cZeroCallSuite(
        (
            P4cZeroCallScenario(
                process_case,
                state=process_state,
                operators={"pipeline-patch": {"kind": "pipeline_patch"}},
            ),
            P4cZeroCallScenario(
                poison_case,
                state=poison_state,
                operators={"poison-quarantine": {"kind": "quarantine_poison"}},
            ),
        ),
        output_dir=tmp_path / "suite",
        router=SuiteRouter(),
    ).run()

    assert report["model_call_count"] == 0
    assert report["same_trace_answer_replay"] is False
    assert report["case_count"] == 2
    assert report["syndrome_resolution_rate"] == 1.0
    assert report["commit_rate"] == 0.5
    assert report["rollback_rate"] == 0.5
    assert report["safety_violation_rate"] == 0.5
    assert report["recurrence_rate"] == 0.0
    assert (tmp_path / "suite" / "zero_call_report.json").exists()


def test_zero_call_failed_shadow_application_leaves_store_retryable() -> None:
    store = StructuralMemoryStore(
        state={
            "pipeline": {
                "retrieval": False,
                "injection": True,
                "granularity": True,
                "safety": True,
            },
            "memories": {},
            "lineage": [],
            "quarantine": [],
            "protected_ids": [],
        },
        operators={
            "wrong-family": {"kind": "supersede_lineage"},
            "pipeline-patch": {"kind": "pipeline_patch"},
        },
    )
    raw = _process_fault_case().to_mapping()
    raw["observation"]["state_root"] = store.snapshot_root()
    syndrome = MemAuditEccAdapter().decode(
        P4cEccCase.from_mapping(raw).observation
    )

    with pytest.raises(ValueError, match="state-drift"):
        store.apply_shadow(syndrome, "wrong-family")

    store.apply_shadow(syndrome, "pipeline-patch")
    assert store.snapshot_root() != syndrome.state_root
    store.rollback_shadow(syndrome.state_root)
    assert store.snapshot_root() == syndrome.state_root
