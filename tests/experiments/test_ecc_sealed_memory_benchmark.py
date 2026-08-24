from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from cmd_audit.repair.ecc import EccRepairReceipt
import experiments.run_ecc_sealed_memory_benchmark as run


class _Backend:
    def answer_context(self, _case, context: str, *, purpose: str) -> str:
        return f"{purpose}:{context}"


def test_prediction_reads_committed_state_without_static_seed_candidates(
    tmp_path: Path, monkeypatch,
) -> None:
    dataset = tmp_path / "dataset.json"
    dataset.write_text("[]\n", encoding="utf-8")
    case = SimpleNamespace(
        case_id="case-1",
        raw={
            "query": "Where is the note?",
            "extracted_memory": [
                {"memory_id": "m1", "text": "old note"},
                {"memory_id": "m2", "text": "current note"},
            ],
            "baseline_outputs": [{"retrieved_memory_ids": ["m1"]}],
        },
    )
    monkeypatch.setattr(run, "VLLMDualScoreArenaBackend", lambda **_kwargs: _Backend())
    monkeypatch.setattr(run, "preflight_openai_endpoint", lambda _backend: {})
    monkeypatch.setattr(run, "load_longmemeval_arena_cases", lambda *_args, **_kwargs: (case,))

    runtime_dir = tmp_path / "runtime-bundle"
    runtime_run = runtime_dir / "runtime"
    runtime_run.mkdir(parents=True)
    state = {
        "pipeline": {"retrieval": True, "injection": True},
        "memories": {"m1": {"active": False}, "m2": {"active": True}},
        "lineage": [["m1", "m2"]],
        "quarantine": [],
    }
    state_root = "state-after"
    receipt = EccRepairReceipt(
        receipt_id="receipt-1", syndrome_id="syndrome-1", incident_id="incident-1",
        selection_id="selection-1", selected_skill_revision_id="skill-1",
        probe_id="probe-1", observed_after_event_index=2,
        before_root="state-before", shadow_root=state_root, after_root=state_root,
        resolved_syndrome=True, invariants_passed=True, committed=True,
        rolled_back=False, safety_violation=False, locality_cost=0.1,
        recurrence_after_commit=False, provenance={"checker": "ecc"},
    )
    committed_path = runtime_dir / "committed_states.jsonl"
    committed_path.write_text(json.dumps({
        "schema_version": "cmd-ecc-committed-state-v1",
        "case_id": "case-1", "state_root": state_root, "state": state,
    }) + "\n", encoding="utf-8")
    (runtime_run / "repair_receipts.jsonl").write_text(
        json.dumps(receipt.to_mapping()) + "\n", encoding="utf-8"
    )
    (runtime_run / "case_completions.jsonl").write_text(json.dumps({
        "case_id": "case-1", "receipt_sha256": receipt.content_hash,
    }) + "\n", encoding="utf-8")
    (runtime_dir / "report.json").write_text(json.dumps({
        "committed_states_sha256": hashlib.sha256(committed_path.read_bytes()).hexdigest(),
        "binding_root": "runtime-binding", "receipt_root": "receipt-root",
    }), encoding="utf-8")
    output = tmp_path / "predictions"

    assert run.main([
        "--benchmark", "longmemeval", "--cases", str(dataset),
        "--runtime-dir", str(runtime_dir), "--output", str(output),
    ]) == 0

    seal = json.loads((output / "prediction_seal.json").read_text())
    ecc_prediction = json.loads((output / "predictions" / "cmd_ecc.jsonl").read_text())
    assert seal["arms"] == ["bm25", "cmd_ecc"]
    assert seal["ecc_incident_case_count"] == 1
    assert "current note" in ecc_prediction["hypothesis"]
    assert "seed:" not in (output / "runtime_ledger.jsonl").read_text()
