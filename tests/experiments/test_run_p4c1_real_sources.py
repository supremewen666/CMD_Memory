from __future__ import annotations

import json
from pathlib import Path

from experiments.run_p4c1_real_sources import (
    build_p4c1_plan,
    run_p4c1_zero_call,
)
from experiments.run_p4c3_native_detection import (
    audit_p4c3_detection,
    prepare_detection_sidecar,
    run_p4c3_detection,
)


ROOT = Path(__file__).resolve().parents[2]
LONGMEM = ROOT / "data/external/longmemeval/input/longmemeval_s_cleaned.json"
MEMFAIL = ROOT / "data/external/memfail/datasets"


def test_p4c1_plan_projects_three_real_sources_without_runtime_sealed_fields() -> None:
    before = {
        "longmem": LONGMEM.read_bytes(),
        "memfail": tuple(
            (path, path.read_bytes()) for path in sorted(MEMFAIL.rglob("*.csv"))
        ),
    }

    plan = build_p4c1_plan(
        longmemeval_path=LONGMEM,
        memfail_root=MEMFAIL,
        limit_per_source=2,
        poison_recall_size=10,
        poison_count=3,
    )

    assert len(plan.source_projection) == 6
    assert {row["source"] for row in plan.source_projection} == {
        "longmemeval",
        "memfail",
        "poison_sweep",
    }
    assert {row["mechanism"] for row in plan.incident_overlay} == {
        "state_drift",
        "process_fault",
        "adversarial_poison",
    }
    runtime_material = json.dumps(
        {
            "projection": plan.source_projection,
            "overlay": plan.incident_overlay,
        },
        sort_keys=True,
    ).casefold()
    assert all(
        marker not in runtime_material
        for marker in ("gold", "label", "answer_replay", "same_trace")
    )
    assert len(plan.source_roots) == 3
    assert LONGMEM.read_bytes() == before["longmem"]
    assert tuple(
        (path, path.read_bytes()) for path in sorted(MEMFAIL.rglob("*.csv"))
    ) == before["memfail"]


def test_p4c1_real_source_suite_runs_receipt_only_and_zero_call(
    tmp_path: Path,
) -> None:
    result = run_p4c1_zero_call(
        longmemeval_path=LONGMEM,
        memfail_root=MEMFAIL,
        output_dir=tmp_path / "p4c1",
        limit_per_source=2,
        poison_recall_size=10,
        poison_count=3,
    )

    assert result["status"] == "success"
    assert result["case_count"] == 6
    assert result["source_counts"] == {
        "longmemeval": 2,
        "memfail": 2,
        "poison_sweep": 2,
    }
    assert result["mechanism_counts"] == {
        "state_drift": 2,
        "process_fault": 2,
        "adversarial_poison": 2,
    }
    assert result["commit_rate"] == 1.0
    assert result["model_call_count"] == 0
    assert result["external_call_count"] == 0
    assert result["runtime_uses_gold"] is False
    assert result["same_trace_answer_replay"] is False
    assert (tmp_path / "p4c1" / "source_projection.jsonl").exists()
    assert (tmp_path / "p4c1" / "incident_overlay.jsonl").exists()
    visible_path = tmp_path / "p4c1" / "visible_telemetry.jsonl"
    assert visible_path.exists()
    visible_rows = [json.loads(line) for line in visible_path.read_text().splitlines()]
    assert len(visible_rows) == 12
    visible_text = json.dumps(visible_rows, sort_keys=True).casefold()
    assert all(marker not in visible_text for marker in ("gold", "label", "mechanism"))
    detector = run_p4c3_detection(
        visible_telemetry=visible_path,
        output_dir=tmp_path / "p4c3",
    )
    assert detector["mechanism_counts"] == {
        "process_fault": 2,
        "state_drift": 2,
        "adversarial_poison": 2,
    }
    assert detector["abstain_count"] == 6
    sidecar = tmp_path / "sealed" / "p4c3.jsonl"
    prepare_detection_sidecar(
        output_dir=tmp_path / "p4c3",
        incident_overlay=tmp_path / "p4c1" / "detection_audit_overlay.jsonl",
        sealed_sidecar=sidecar,
    )
    audit = audit_p4c3_detection(
        output_dir=tmp_path / "p4c3", sealed_sidecar=sidecar
    )
    assert audit["accuracy"] == 1.0
    assert audit["false_repair_rate"] == 0.0
