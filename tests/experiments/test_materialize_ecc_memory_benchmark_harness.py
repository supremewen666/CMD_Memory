from __future__ import annotations

import json
from pathlib import Path

from experiments.materialize_ecc_memory_benchmark_harness import materialize_bundle
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
    assert report["harness_binding_root"] == manifest["binding_root"]
    assert report["benchmark_track"] == manifest["benchmark_track"]


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
