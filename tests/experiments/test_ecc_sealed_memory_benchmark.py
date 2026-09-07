from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmd_audit.core.state_codec import content_sha256
from cmd_audit.repair.ecc import EccRepairReceipt
import experiments.run_ecc_sealed_memory_benchmark as run


class _Backend:
    def answer_context(self, _case, context: str, *, purpose: str) -> str:
        return f"{purpose}:{context}"


def _write_v3_runtime(
    root: Path,
    *,
    mechanism: str,
    before_state: dict[str, object],
    after_state: dict[str, object],
) -> None:
    runtime_run = root / "runtime"
    runtime_run.mkdir(parents=True)
    before_root = content_sha256(before_state, ensure_ascii=False, allow_nan=False)
    after_root = content_sha256(after_state, ensure_ascii=False, allow_nan=False)
    receipt = EccRepairReceipt(
        receipt_id=f"receipt-{mechanism}", syndrome_id=f"syndrome-{mechanism}",
        incident_id=f"incident-{mechanism}", selection_id="selection-1",
        selected_skill_revision_id="skill-1", probe_id="probe-1",
        observed_after_event_index=2, before_root=before_root,
        shadow_root=after_root, after_root=after_root, resolved_syndrome=True,
        invariants_passed=True, committed=True, rolled_back=False,
        safety_violation=False, locality_cost=0.1,
        recurrence_after_commit=False, provenance={"checker": "ecc"},
    )
    row = {
        "schema_version": "cmd-ecc-causal-state-pair-v3",
        "case_id": "case-1",
        "mechanism": mechanism,
        "process_fault_subtype": None,
        "repair_semantics": (
            "supersession-active-memory-v1"
            if mechanism == "state_drift"
            else "quarantine-active-memory-v1"
        ),
        "superseding_memory_id": "m2" if mechanism == "state_drift" else None,
        "superseded_memory_id": "m1" if mechanism == "state_drift" else None,
        "suspect_ids": ["m1"] if mechanism == "adversarial_poison" else [],
        "memory_text_overrides": {},
        "before_root": before_root,
        "before_state": before_state,
        "after_root": after_root,
        "after_state": after_state,
        "receipt_sha256": receipt.content_hash,
    }
    causal_path = root / "causal_states.jsonl"
    causal_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    (runtime_run / "repair_receipts.jsonl").write_text(
        json.dumps(receipt.to_mapping()) + "\n", encoding="utf-8"
    )
    (runtime_run / "case_completions.jsonl").write_text(json.dumps({
        "case_id": "case-1", "receipt_sha256": receipt.content_hash,
    }) + "\n", encoding="utf-8")
    report = {
        "schema_version": "cmd-ecc-memory-runtime-report-v3",
        "answer_contrast_ready": True,
        "causal_states_sha256": hashlib.sha256(causal_path.read_bytes()).hexdigest(),
        "receipt_root": "receipt-root",
        "mechanism": mechanism,
        "mechanism_counts": {mechanism: 1},
        "causal_experiment_kind": "single-mechanism",
    }
    report["binding_root"] = content_sha256(report)
    (root / "report.json").write_text(json.dumps(report), encoding="utf-8")


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
    before_state = {
        "pipeline": {
            "retrieval": False, "injection": True,
            "granularity": True, "safety": True,
        },
        "memories": {"m1": {"active": True}, "m2": {"active": True}},
        "memory_order": ["m2", "m1"],
        "lineage": [],
        "quarantine": [],
        "protected_ids": [],
    }
    after_state = json.loads(json.dumps(before_state))
    after_state["pipeline"]["retrieval"] = True
    before_root = content_sha256(before_state, ensure_ascii=False, allow_nan=False)
    after_root = content_sha256(after_state, ensure_ascii=False, allow_nan=False)
    receipt = EccRepairReceipt(
        receipt_id="receipt-1", syndrome_id="syndrome-1", incident_id="incident-1",
        selection_id="selection-1", selected_skill_revision_id="skill-1",
        probe_id="probe-1", observed_after_event_index=2,
        before_root=before_root, shadow_root=after_root, after_root=after_root,
        resolved_syndrome=True, invariants_passed=True, committed=True,
        rolled_back=False, safety_violation=False, locality_cost=0.1,
        recurrence_after_commit=False, provenance={"checker": "ecc"},
    )
    causal_path = runtime_dir / "causal_states.jsonl"
    causal_path.write_text(json.dumps({
        "schema_version": "cmd-ecc-causal-state-pair-v2",
        "case_id": "case-1",
        "process_fault_subtype": "retrieval",
        "operator_semantics": "retrieval-outage-empty-result-v1",
        "before_root": before_root,
        "before_state": before_state,
        "after_root": after_root,
        "after_state": after_state,
        "receipt_sha256": receipt.content_hash,
    }) + "\n", encoding="utf-8")
    (runtime_run / "repair_receipts.jsonl").write_text(
        json.dumps(receipt.to_mapping()) + "\n", encoding="utf-8"
    )
    (runtime_run / "case_completions.jsonl").write_text(json.dumps({
        "case_id": "case-1", "receipt_sha256": receipt.content_hash,
    }) + "\n", encoding="utf-8")
    report = {
        "schema_version": "cmd-ecc-memory-runtime-report-v2",
        "answer_contrast_ready": True,
        "causal_states_sha256": hashlib.sha256(causal_path.read_bytes()).hexdigest(),
        "binding_root": None,
        "receipt_root": "receipt-root",
    }
    report.pop("binding_root")
    report["binding_root"] = content_sha256(report)
    (runtime_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    output = tmp_path / "predictions"

    assert run.main([
        "--benchmark", "longmemeval", "--cases", str(dataset),
        "--runtime-dir", str(runtime_dir), "--output", str(output),
    ]) == 0

    seal = json.loads((output / "prediction_seal.json").read_text())
    assert run.validate_ecc_prediction_seal(output)["binding_root"] == seal["binding_root"]
    before_prediction = json.loads((output / "predictions" / "incident_before.jsonl").read_text())
    after_prediction = json.loads((output / "predictions" / "repaired_after.jsonl").read_text())
    assert seal["arms"] == ["incident_before", "repaired_after"]
    assert seal["mechanism_counts"] == {"process_fault": 1}
    assert seal["ecc_incident_case_count"] == 1
    assert "(empty)" in before_prediction["hypothesis"]
    assert after_prediction["hypothesis"].index("current note") < after_prediction["hypothesis"].index("old note")
    ledger_text = (output / "runtime_ledger.jsonl").read_text()
    ledger = json.loads(ledger_text)
    assert ledger["heading"] == "Retrieved memory"
    assert ledger["generation_config_sha256"] == seal["generation_config_sha256"]
    assert ledger["before_source_memory_order"] == ["m2", "m1"]
    assert ledger["after_source_memory_order"] == ["m2", "m1"]
    assert ledger["before_root"] == before_root
    assert ledger["after_root"] == after_root
    assert ledger["before_input_tokens"] > 2
    assert ledger["after_input_tokens"] > ledger["before_input_tokens"]
    assert "seed:" not in ledger_text


def test_old_runtime_is_not_silently_accepted(tmp_path: Path) -> None:
    runtime = tmp_path / "old-runtime"
    runtime.mkdir()
    (runtime / "report.json").write_text(json.dumps({
        "schema_version": "cmd-ecc-memory-runtime-report-v1",
        "answer_contrast_ready": False,
    }), encoding="utf-8")
    try:
        run._load_runtime(runtime)
    except ValueError as exc:
        assert "requires a causal-state runtime" in str(exc)
    else:
        raise AssertionError("v1 runtime must not enter the v2 causal answer runner")


@pytest.mark.parametrize("mechanism", ("state_drift", "adversarial_poison"))
def test_nonprocess_mechanisms_produce_separate_v3_seals(
    tmp_path: Path, monkeypatch, mechanism: str,
) -> None:
    dataset = tmp_path / f"{mechanism}.json"
    dataset.write_text("[]\n", encoding="utf-8")
    case = SimpleNamespace(
        case_id="case-1",
        raw={
            "query": "Which note is valid?",
            "extracted_memory": [
                {"memory_id": "m1", "text": "old or suspect note"},
                {"memory_id": "m2", "text": "superseding note"},
            ],
            "baseline_outputs": [{"retrieved_memory_ids": ["m1", "m2"]}],
        },
    )
    monkeypatch.setattr(run, "VLLMDualScoreArenaBackend", lambda **_kwargs: _Backend())
    monkeypatch.setattr(run, "preflight_openai_endpoint", lambda _backend: {})
    monkeypatch.setattr(
        run, "load_longmemeval_arena_cases", lambda *_args, **_kwargs: (case,)
    )
    before_state = {
        "pipeline": {name: True for name in ("retrieval", "injection", "granularity", "safety")},
        "memories": {"m1": {"active": True}, "m2": {"active": mechanism != "state_drift"}},
        "memory_order": ["m1", "m2"],
        "lineage": [], "quarantine": [], "protected_ids": [],
    }
    after_state = json.loads(json.dumps(before_state))
    if mechanism == "state_drift":
        after_state["memories"]["m1"]["active"] = False
        after_state["memories"]["m2"]["active"] = True
        after_state["lineage"] = [["m1", "m2"]]
    else:
        after_state["memories"]["m1"]["active"] = False
        after_state["quarantine"] = ["m1"]
    runtime_dir = tmp_path / f"runtime-{mechanism}"
    _write_v3_runtime(
        runtime_dir,
        mechanism=mechanism,
        before_state=before_state,
        after_state=after_state,
    )
    output = tmp_path / f"seal-{mechanism}"
    assert run.main([
        "--benchmark", "longmemeval", "--cases", str(dataset),
        "--runtime-dir", str(runtime_dir), "--output", str(output),
    ]) == 0
    seal = run.validate_ecc_prediction_seal(output)
    assert seal["schema_version"] == "cmd-ecc-memory-benchmark-prediction-seal-v3"
    assert seal["mechanism"] == mechanism
    assert seal["mechanism_counts"] == {mechanism: 1}
    assert seal["arms"] == ["incident_before", "repaired_after"]
    ledger = json.loads((output / "runtime_ledger.jsonl").read_text(encoding="utf-8"))
    assert ledger["mechanism"] == mechanism
    assert ledger["process_fault_subtype"] is None
    assert ledger["before_input_tokens"] > 2
    assert ledger["after_input_tokens"] > 2


def test_token_ledger_gate_rejects_two_token_regression() -> None:
    broken = SimpleNamespace(
        budgeted=SimpleNamespace(input_tokens=2, included_ids=())
    )
    with pytest.raises(ValueError, match="implausible"):
        run._validate_token_ledger_pair(
            before=broken,
            after=broken,
            mechanism="process_fault",
            process_fault_subtype="retrieval",
            max_input_tokens=32_000,
        )
