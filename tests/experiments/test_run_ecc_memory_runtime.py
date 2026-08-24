from __future__ import annotations

import json
from pathlib import Path

from cmd_audit.core.state_codec import content_sha256
from cmd_audit.repair.ghost_ecology import (
    EcologyLedger,
    FailureDeposit,
    GhostEcology,
    PatternRevision,
    RegistrySnapshot,
    SkillRevision,
)
from experiments.p4c_zero_call import StructuralMemoryStore
from experiments.run_ecc_memory_runtime import main


def test_runtime_cli_uses_memaudit_ghost_receipt_and_exports_committed_state(
    tmp_path: Path,
) -> None:
    ecology_path = tmp_path / "source_ecology.jsonl"
    ecology = GhostEcology(EcologyLedger(ecology_path))
    failure = FailureDeposit(
        "failure-1", "case-1", "audit-only", "failure-memory-root",
        (("retrieval-down", 1.0),), "context-root", "provenance-root",
    )
    pattern = PatternRevision.create(
        pattern_id="retrieval-process-fault",
        predicate={"kind": "runtime_signal", "signal": "retrieval-down"},
        feature_signature=("retrieval-down",),
        derivation_kind="seed",
        state="stable",
    )
    skill = SkillRevision.create(
        skill_id="repair-retrieval-pipeline",
        program={"kind": "typed_repair_program", "operator_kind": "pipeline_patch"},
        parameter_schema={"type": "object", "additionalProperties": False},
        preconditions=({"signal": "retrieval-down"},),
        postconditions=({"predicate": "syndrome_resolved"},),
        success_probe={"probe_id": "probe:retrieval", "kind": "ecc_parity"},
        mutation_budget={"max_locality_cost": 1.0},
        rollback_program={"kind": "restore_before_root"},
        producing_failure_id=failure.failure_id,
        derivation_kind="seed",
        state="stable",
    )
    ecology.deposit_failure(failure, event_index=1)
    ecology.propose_pattern(pattern, event_index=2)
    ecology.propose_skill(skill, event_index=3)
    registry = RegistrySnapshot.create(
        epoch=1,
        stable_pattern_revision_ids=(pattern.pattern_revision_id,),
        stable_skill_revision_ids=(skill.skill_revision_id,),
        config_sha256="test-config",
    )
    ecology.freeze_registry(registry, event_index=4)

    state = {
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
    state_root = StructuralMemoryStore(
        state=state,
        operators={skill.skill_revision_id: {"kind": "pipeline_patch"}},
    ).snapshot_root()
    cases = tmp_path / "cases.jsonl"
    cases.write_text(json.dumps({
        "schema_version": "cmd-p4c-ecc-case-v1",
        "case_id": "case-1",
        "event_index": 6,
        "observation": {
            "observation_id": "observation-1",
            "incident_id": "incident-1",
            "observed_at_event_index": 5,
            "state_root": state_root,
            "source_manifest_root": "source-root",
            "process_fault_subtype": "retrieval",
            "observed_order": [],
            "superseding_memory_id": None,
            "superseded_memory_id": None,
            "cas_anomaly": False,
            "influence_anomaly": False,
            "suspect_ids": [],
            "signal_ids": ["retrieval-down"],
            "provenance": {"detector": "memaudit-v1"},
        },
        "candidates": [{
            "skill_revision_id": skill.skill_revision_id,
            "probe_id": "probe:retrieval",
            "operator_sha256": skill.program_sha256,
        }],
    }) + "\n", encoding="utf-8")
    bindings = tmp_path / "bindings.jsonl"
    bindings.write_text(json.dumps({
        "schema_version": "cmd-ecc-ghost-binding-v1",
        "case_id": "case-1",
        "failure_id": failure.failure_id,
        "responsibilities": [[pattern.pattern_revision_id, 1.0]],
        "registry_id": registry.registry_id,
        "skill_priors": [[skill.skill_revision_id, 0.0]],
    }) + "\n", encoding="utf-8")
    states = tmp_path / "states.jsonl"
    states.write_text(json.dumps({
        "schema_version": "cmd-ecc-structural-scenario-v1",
        "case_id": "case-1",
        "state": state,
        "operators": {skill.skill_revision_id: {"kind": "pipeline_patch"}},
    }) + "\n", encoding="utf-8")
    output = tmp_path / "output"

    assert main([
        "--cases", str(cases),
        "--bindings", str(bindings),
        "--states", str(states),
        "--ecology-ledger", str(ecology_path),
        "--output", str(output),
    ]) == 0

    report = json.loads((output / "report.json").read_text())
    committed = json.loads((output / "committed_states.jsonl").read_text())
    receipt = json.loads((output / "runtime" / "repair_receipts.jsonl").read_text())
    assert report["runtime_uses_gold"] is False
    assert report["same_trace_answer_replay"] is False
    assert receipt["committed"] is True
    assert committed["state"]["pipeline"]["retrieval"] is True
    assert committed["state_root"] == receipt["after_root"]


def test_state_root_helper_matches_runtime_codec() -> None:
    state = {
        "pipeline": {"retrieval": True, "injection": True, "granularity": True, "safety": True},
        "memories": {}, "lineage": [], "quarantine": [], "protected_ids": [],
    }
    store = StructuralMemoryStore(state=state, operators={"skill": {"kind": "pipeline_patch"}})
    assert store.snapshot_root() == content_sha256(state, ensure_ascii=False, allow_nan=False)
