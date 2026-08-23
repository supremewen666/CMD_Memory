from __future__ import annotations

import json
from pathlib import Path

import pytest

from cmd_audit.core.state_codec import content_sha256
from cmd_audit.repair.ecc import EccRepairReceipt
from experiments.run_longmemeval_e2e import AnswerResult
from experiments.run_p4c2_live_efficacy import (
    P4C2_INPUT_SCHEMA,
    build_plan,
    prepare_inputs,
    preflight,
    run_p4c2,
)
from experiments.run_p4c1_real_sources import (
    P4C1_MANIFEST_SCHEMA,
    P4C1_PROJECTION_SCHEMA,
    SESSION_PROJECTION_SCHEMA,
    project_gold_free_session,
)


class _RecordingAnswerer:
    model_id = "recording-answerer-v1"

    def __init__(self) -> None:
        self.calls: list[object] = []

    def answer(self, request):
        self.calls.append(request)
        return AnswerResult(f"seen:{request.memories[0]['content']}", 1)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _fixture(tmp_path: Path, *, forbidden: bool = False) -> tuple[Path, Path]:
    p4c1 = tmp_path / "p4c1"
    runtime = p4c1 / "runtime"
    runtime.mkdir(parents=True)
    before = content_sha256({"state": "faulty"})
    after = content_sha256({"state": "repaired"})
    receipt = EccRepairReceipt(
        receipt_id="receipt-1",
        syndrome_id="syndrome-1",
        incident_id="incident-1",
        selection_id="selection-1",
        selected_skill_revision_id="repair-1",
        probe_id="probe-1",
        observed_after_event_index=2,
        before_root=before,
        shadow_root=after,
        after_root=after,
        resolved_syndrome=True,
        invariants_passed=True,
        committed=True,
        rolled_back=False,
        safety_violation=False,
        locality_cost=0.1,
        recurrence_after_commit=False,
    )
    projection = {
        "schema_version": P4C1_PROJECTION_SCHEMA,
        "source": "longmemeval",
        "source_case_id": "q1",
        "source_root": "a" * 64,
        "visible_fields": ["question"],
        "memory_records": [],
        "state_root": before,
    }
    overlay = {
        "schema_version": "cmd-p4c1-incident-overlay-v1",
        "source": "longmemeval",
        "case_id": "case-1",
        "mechanism": "state_drift",
        "injection_kind": "test",
        "source_root": "a" * 64,
        "state_root": before,
        "signal_ids": ["drift"],
    }
    _write_jsonl(p4c1 / "source_projection.jsonl", [projection])
    _write_jsonl(p4c1 / "incident_overlay.jsonl", [overlay])
    _write_jsonl(runtime / "repair_receipts.jsonl", [receipt.to_mapping()])
    (runtime / "manifest.json").write_text(
        json.dumps({"schema_version": "cmd-p4c-ecc-run-v1", "status": "success", "case_count": 1}),
        encoding="utf-8",
    )
    (p4c1 / "p4c1_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": P4C1_MANIFEST_SCHEMA,
                "status": "success",
                "case_count": 1,
                "runtime_uses_gold": False,
                "runtime_uses_labels": False,
                "router_feedback": "EccRepairReceipt",
                "model_call_count": 0,
                "paper_role": "mainline",
                "session_projection_schema": SESSION_PROJECTION_SCHEMA,
            }
        ),
        encoding="utf-8",
    )
    inputs = tmp_path / "inputs.jsonl"
    row: dict[str, object] = {
        "schema_version": P4C2_INPUT_SCHEMA,
        "case_id": "case-1",
        "source": "longmemeval",
        "query": "What is current?",
        "control_state_root": before,
        "repaired_state_root": after,
        "control_memories": [{"memory_id": "m-old", "content": "old", "source_hash": content_sha256("old")}],
        "repaired_memories": [{"memory_id": "m-new", "content": "new", "source_hash": content_sha256("new")}],
        "incident_overlay_sha256": content_sha256(overlay, ensure_ascii=False, allow_nan=False),
        "repair_receipt_sha256": receipt.content_hash,
    }
    if forbidden:
        row["gold"] = "must fail"
    _write_jsonl(inputs, [row])
    return p4c1, inputs


def test_plan_is_zero_call_and_does_not_open_inputs(tmp_path: Path) -> None:
    plan = build_plan(limit=3, output=tmp_path / "out", run_mode="fresh")
    assert plan["external_calls_authorized"] is False
    assert plan["planned_calls"] == 6
    assert plan["arms"] == ["control", "repaired"]
    assert not (tmp_path / "out").exists()


def test_preflight_binds_p4c1_and_rejects_gold(tmp_path: Path) -> None:
    p4c1, inputs = _fixture(tmp_path)
    report, cases = preflight(p4c1_run=p4c1, inputs=inputs, limit=1)
    assert report["runtime_gold_free"] is True
    assert report["eligible_case_count"] == 1
    assert len(cases) == 1

    p4c1_bad, inputs_bad = _fixture(tmp_path / "bad", forbidden=True)
    with pytest.raises(ValueError, match="gold-free"):
        preflight(p4c1_run=p4c1_bad, inputs=inputs_bad, limit=1)


def test_fake_or_injected_execution_writes_paired_seal_and_resumes(tmp_path: Path) -> None:
    p4c1, inputs = _fixture(tmp_path)
    output = tmp_path / "out"
    answerer = _RecordingAnswerer()
    seal = run_p4c2(
        p4c1_run=p4c1,
        inputs=inputs,
        output=output,
        answerer=answerer,
        limit=1,
    )
    assert len(answerer.calls) == 2
    assert seal["schema_version"] == "cmd-p4c2-prediction-seal-v1"
    assert seal["paired_prediction_count"] == 2
    assert seal["paired_case_count"] == 1
    assert seal["gold_opened"] is False
    rows = [json.loads(line) for line in (output / "paired_predictions.jsonl").read_text().splitlines()]
    assert [row["arm"] for row in rows] == ["control", "repaired"]
    assert all("hypothesis" in row and "gold" not in row for row in rows)
    assert rows[0]["arm_state_root"] != rows[1]["arm_state_root"]
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "prediction_sealed"
    assert manifest["paired_prediction_head"] == seal["paired_prediction_head"]
    assert manifest["paired_predictions_sha256"] == seal["paired_predictions_sha256"]
    assert manifest["prediction_seal_sha256"]

    resumed = run_p4c2(
        p4c1_run=p4c1,
        inputs=inputs,
        output=output,
        answerer=_RecordingAnswerer(),
        limit=1,
        run_mode="resume",
    )
    assert resumed == seal
    assert len((output / "paired_predictions.jsonl").read_text().splitlines()) == 2


def test_prepare_inputs_projects_longmemeval_without_gold(tmp_path: Path) -> None:
    p4c1, _inputs = _fixture(tmp_path)
    sessions = [
        [{"role": "user", "content": "old fact"}],
        [{"role": "user", "content": "new fact"}],
    ]
    data = tmp_path / "long.json"
    data.write_text(
        json.dumps(
            [
                {
                    "question_id": "q1",
                    "question": "Which fact is current?",
                    "answer": "TOP SECRET GOLD",
                    "answer_session_ids": ["s2"],
                    "question_type": "sealed label",
                    "haystack_session_ids": ["s1", "s2"],
                    "haystack_sessions": sessions,
                }
            ]
        ),
        encoding="utf-8",
    )
    projection_path = p4c1 / "source_projection.jsonl"
    projection = json.loads(projection_path.read_text())
    projection["memory_records"] = [
        {
            "memory_id": "m-old",
            "source_event_id": "s1",
            "content_sha256": content_sha256(project_gold_free_session(sessions[0])),
            "content_projection_schema": SESSION_PROJECTION_SCHEMA,
        },
        {
            "memory_id": "m-new",
            "source_event_id": "s2",
            "content_sha256": content_sha256(project_gold_free_session(sessions[1])),
            "content_projection_schema": SESSION_PROJECTION_SCHEMA,
        },
    ]
    _write_jsonl(projection_path, [projection])
    manifest_path = p4c1 / "p4c1_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    import hashlib
    manifest["source_roots"] = {"longmemeval": hashlib.sha256(data.read_bytes()).hexdigest()}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    prepared = tmp_path / "prepared.jsonl"
    report = prepare_inputs(
        p4c1_run=p4c1,
        longmemeval_data=data,
        output=prepared,
        limit=1,
    )
    text = prepared.read_text()
    row = json.loads(text)
    assert report["case_count"] == 1
    assert "TOP SECRET GOLD" not in text
    assert "answer" not in row
    assert len(row["control_memories"]) == 2
    assert len(row["repaired_memories"]) == 1
    assert row["repaired_memories"][0]["memory_id"] == "m-new"
