from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.run_p4c3_native_detection import (
    audit_p4c3_detection,
    prepare_detection_sidecar,
    run_p4c3_detection,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _visible(case_id: str, **changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_version": "cmd-p4c3-visible-telemetry-v1",
        "case_id": case_id,
        "observed_at_event_index": 7,
        "state_root": f"state-{case_id}",
        "source_manifest_root": "source-root",
        "pipeline_checks": {
            "retrieval": True,
            "injection": True,
            "granularity": True,
            "safety": True,
        },
        "active_versions": [],
        "integrity_signals": [],
    }
    row.update(changes)
    return row


def test_runtime_decodes_three_exclusive_mechanisms_and_abstains_without_fault(
    tmp_path: Path,
) -> None:
    visible = tmp_path / "visible.jsonl"
    output = tmp_path / "run"
    _write_jsonl(
        visible,
        [
            _visible("clean"),
            _visible(
                "fault",
                pipeline_checks={
                    "retrieval": False,
                    "injection": True,
                    "granularity": True,
                    "safety": True,
                },
            ),
            _visible(
                "drift",
                active_versions=[
                    {"slot": "meal", "memory_id": "old", "observed_at": 1},
                    {"slot": "meal", "memory_id": "new", "observed_at": 2},
                ],
            ),
            _visible(
                "poison",
                integrity_signals=[
                    {
                        "memory_id": "bad",
                        "cas_valid": False,
                        "influence_score": 0.9,
                        "influence_threshold": 0.8,
                    }
                ],
            ),
            _visible(
                "ambiguous",
                pipeline_checks={
                    "retrieval": False,
                    "injection": True,
                    "granularity": True,
                    "safety": True,
                },
                integrity_signals=[
                    {
                        "memory_id": "bad-2",
                        "cas_valid": False,
                        "influence_score": 0.1,
                        "influence_threshold": 0.8,
                    }
                ],
            ),
        ],
    )

    manifest = run_p4c3_detection(visible_telemetry=visible, output_dir=output)

    assert manifest["external_call_count"] == 0
    assert manifest["runtime_gold_free"] is True
    assert manifest["status"] == "prediction_sealed"
    rows = [json.loads(line) for line in (output / "detections.jsonl").read_text().splitlines()]
    by_case = {row["case_id"]: row for row in rows}
    assert by_case["clean"]["decision"] == "abstain"
    assert by_case["clean"]["repair_admitted"] is False
    assert by_case["clean"]["abstain_reason"] == "no_fault"
    assert by_case["fault"]["syndrome"]["mechanism"] == "process_fault"
    assert by_case["drift"]["syndrome"]["mechanism"] == "state_drift"
    assert by_case["poison"]["syndrome"]["mechanism"] == "adversarial_poison"
    assert by_case["poison"]["repair_admitted"] is True
    assert by_case["ambiguous"]["decision"] == "abstain"
    assert by_case["ambiguous"]["abstain_reason"] == "ambiguous_mechanisms"


def test_runtime_rejects_label_or_gold_in_visible_input(tmp_path: Path) -> None:
    visible = tmp_path / "visible.jsonl"
    row = _visible("leak")
    row["label"] = "process_fault"
    _write_jsonl(visible, [row])

    with pytest.raises(ValueError, match="gold-free"):
        run_p4c3_detection(visible_telemetry=visible, output_dir=tmp_path / "run")


def test_sealed_sidecar_is_opened_only_by_post_runtime_audit(tmp_path: Path) -> None:
    visible = tmp_path / "visible.jsonl"
    output = tmp_path / "run"
    sidecar = tmp_path / "sealed.jsonl"
    _write_jsonl(
        visible,
        [
            _visible("clean"),
            _visible(
                "fault",
                pipeline_checks={
                    "retrieval": False,
                    "injection": True,
                    "granularity": True,
                    "safety": True,
                },
            ),
        ],
    )
    _write_jsonl(
        sidecar,
        [
            {"case_id": "clean", "label": "no_fault"},
            {"case_id": "fault", "label": "process_fault"},
        ],
    )
    runtime = run_p4c3_detection(visible_telemetry=visible, output_dir=output)
    assert "sidecar_sha256" not in runtime

    report = audit_p4c3_detection(output_dir=output, sealed_sidecar=sidecar)

    assert report["runtime_manifest_sha256"] == runtime["manifest_sha256"]
    assert report["accuracy"] == 1.0
    assert report["false_repair_rate"] == 0.0
    assert report["per_class"]["process_fault"]["precision"] == 1.0
    assert report["per_class"]["process_fault"]["recall"] == 1.0
    assert report["per_class"]["no_fault"]["recall"] == 1.0
    assert report["confusion"]["no_fault"]["abstain"] == 1


def test_public_overlay_can_prepare_sidecar_only_after_runtime_seal(tmp_path: Path) -> None:
    visible = tmp_path / "visible.jsonl"
    output = tmp_path / "run"
    overlay = tmp_path / "overlay.jsonl"
    sidecar = tmp_path / "sealed" / "labels.jsonl"
    _write_jsonl(
        visible,
        [_visible("fault", pipeline_checks={"retrieval": False, "injection": True, "granularity": True, "safety": True})],
    )
    _write_jsonl(
        overlay,
        [{
            "schema_version": "cmd-p4c1-incident-overlay-v1",
            "case_id": "fault",
            "mechanism": "process_fault",
        }],
    )
    with pytest.raises((FileNotFoundError, ValueError)):
        prepare_detection_sidecar(
            output_dir=output, incident_overlay=overlay, sealed_sidecar=sidecar
        )
    run_p4c3_detection(visible_telemetry=visible, output_dir=output)
    report = prepare_detection_sidecar(
        output_dir=output, incident_overlay=overlay, sealed_sidecar=sidecar
    )
    assert report["status"] == "sealed_sidecar_ready"
    assert json.loads(sidecar.read_text())["label"] == "process_fault"
