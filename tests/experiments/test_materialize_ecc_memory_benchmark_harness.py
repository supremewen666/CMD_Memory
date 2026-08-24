from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.materialize_ecc_memory_benchmark_harness import materialize_bundle
from experiments.locomo_arena import load_locomo_arena_cases
from experiments.run_ecc_memory_runtime import main as run_runtime


def test_materialized_controlled_harness_runs_through_receipt_runtime(tmp_path: Path) -> None:
    source = Path("data/ghost_live_v2/raw_sources/locomo10.json")
    bundle = tmp_path / "bundle"
    manifest = materialize_bundle(
        benchmark="locomo",
        dataset_path=source,
        output=bundle,
        limit=4,
    )

    assert manifest["benchmark_track"] == "controlled-structural-stress-not-native-official"
    assert manifest["native_memaudit_telemetry"] is False
    assert manifest["runtime_uses_reference_targets"] is False
    runtime_inputs = "\n".join(
        (bundle / name).read_text(encoding="utf-8")
        for name in (
            "memaudit_cases.jsonl",
            "ghost_bindings.jsonl",
            "shadow_states.jsonl",
        )
    ).casefold()
    assert '"gold' not in runtime_inputs
    assert '"answer' not in runtime_inputs
    assert {json.loads(line)["observation"]["process_fault_subtype"] for line in (
        bundle / "memaudit_cases.jsonl"
    ).read_text(encoding="utf-8").splitlines()} == {
        "retrieval", "injection", "granularity", "safety"
    }
    state_rows = [
        json.loads(line)
        for line in (bundle / "shadow_states.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    # JSON mappings are serialized with sort_keys=True, but the explicit list
    # remains the authoritative retrieval order.
    selected = load_locomo_arena_cases(
        source,
        include_adversarial=True,
        seed=24,
        limit=4,
        retrieval_top_k=5,
        candidate_pool_k=10,
    )
    expected_orders = {
        case.case_id: case.raw["baseline_outputs"][0]["retrieved_memory_ids"]
        for case in selected
    }
    assert all(
        row["state"]["memory_order"] == expected_orders[row["case_id"]]
        for row in state_rows
    )
    assert all(
        set(row["state"]["memory_order"]) == set(row["state"]["memories"])
        for row in state_rows
    )

    runtime = tmp_path / "runtime"
    assert run_runtime([
        "--cases", str(bundle / "memaudit_cases.jsonl"),
        "--bindings", str(bundle / "ghost_bindings.jsonl"),
        "--states", str(bundle / "shadow_states.jsonl"),
        "--ecology-ledger", str(bundle / "frozen_ecology.jsonl"),
        "--output", str(runtime),
    ]) == 0
    report = json.loads((runtime / "report.json").read_text(encoding="utf-8"))
    assert report["committed"] == 4
    assert report["rolled_back"] == 0
    assert report["schema_version"] == "cmd-ecc-memory-runtime-report-v3"
    assert report["mechanism_counts"] == {"process_fault": 4}
    assert report["answer_contrast_ready"] is True
    assert report["harness_binding_root"] == manifest["binding_root"]
    assert report["benchmark_track"] == manifest["benchmark_track"]


@pytest.mark.parametrize("mechanism", ("state_drift", "adversarial_poison"))
def test_materialized_nonprocess_harnesses_are_independent_and_typed(
    tmp_path: Path, mechanism: str,
) -> None:
    source = Path("data/ghost_live_v2/raw_sources/locomo10.json")
    bundle = tmp_path / mechanism / "bundle"
    manifest = materialize_bundle(
        benchmark="locomo",
        dataset_path=source,
        output=bundle,
        limit=2,
        mechanism=mechanism,
    )
    assert manifest["mechanism"] == mechanism
    assert manifest["controlled_fault_assignment"] == f"single-{mechanism}-track"

    runtime = tmp_path / mechanism / "runtime"
    assert run_runtime([
        "--cases", str(bundle / "memaudit_cases.jsonl"),
        "--bindings", str(bundle / "ghost_bindings.jsonl"),
        "--states", str(bundle / "shadow_states.jsonl"),
        "--ecology-ledger", str(bundle / "frozen_ecology.jsonl"),
        "--output", str(runtime),
    ]) == 0
    report = json.loads((runtime / "report.json").read_text(encoding="utf-8"))
    assert report["mechanism"] == mechanism
    assert report["mechanism_counts"] == {mechanism: 2}
    assert report["causal_experiment_kind"] == "single-mechanism"
    rows = [
        json.loads(line)
        for line in (runtime / "causal_states.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {row["mechanism"] for row in rows} == {mechanism}
    for row in rows:
        before = row["before_state"]
        after = row["after_state"]
        if mechanism == "state_drift":
            new_id = row["superseding_memory_id"]
            old_id = row["superseded_memory_id"]
            assert before["memories"][old_id]["active"] is True
            assert before["memories"][new_id]["active"] is False
            assert after["memories"][old_id]["active"] is False
            assert after["memories"][new_id]["active"] is True
            assert [old_id, new_id] in after["lineage"]
        else:
            suspect_id = row["suspect_ids"][0]
            assert before["memories"][suspect_id]["active"] is True
            assert after["memories"][suspect_id]["active"] is False
            assert suspect_id in after["quarantine"]


def test_placeholder_paths_produce_materializer_hint(tmp_path: Path) -> None:
    try:
        run_runtime([
            "--cases", "/path/to/harness/memaudit_cases.jsonl",
            "--bindings", "/path/to/harness/ghost_bindings.jsonl",
            "--states", "/path/to/harness/shadow_states.jsonl",
            "--ecology-ledger", "/path/to/harness/frozen_ecology.jsonl",
            "--output", str(tmp_path / "runtime"),
        ])
    except FileNotFoundError as exc:
        assert "values in documentation are placeholders" in str(exc)
        assert "materialize_ecc_memory_benchmark_harness" in str(exc)
    else:
        raise AssertionError("placeholder paths must be rejected")
