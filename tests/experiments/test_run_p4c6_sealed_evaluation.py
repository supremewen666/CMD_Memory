from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cmd_audit.core.state_codec import content_sha256
from experiments.run_p4c6_sealed_evaluation import (
    evaluate,
    prepare_sidecar,
    preflight,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _p4c2_fixture(tmp_path: Path) -> Path:
    run = tmp_path / "p4c2"
    run.mkdir()
    rows = []
    head = "0" * 64
    cases = [
        ("p4c1-longmemeval-q1", "old", "new"),
        ("p4c1-longmemeval-q2", "right", "wrong"),
    ]
    for case_id, control, repaired in cases:
        for arm, hypothesis in (("control", control), ("repaired", repaired)):
            row = {
                "schema_version": "cmd-p4c2-paired-prediction-v1",
                "event_index": len(rows) + 1,
                "case_id": case_id,
                "source": "longmemeval",
                "mechanism": "state_drift",
                "arm": arm,
                "initial_state_root": f"before-{case_id}",
                "arm_state_root": f"{arm}-{case_id}",
                "incident_overlay_sha256": "a" * 64,
                "repair_receipt_sha256": "b" * 64,
                "hypothesis": hypothesis,
                "previous_hash": head,
            }
            row["event_hash"] = content_sha256(row, ensure_ascii=False, allow_nan=False)
            head = row["event_hash"]
            rows.append(row)
    journal = run / "paired_predictions.jsonl"
    journal.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    seal = {
        "schema_version": "cmd-p4c2-prediction-seal-v1",
        "binding_schema_version": "cmd-p4c2-live-efficacy-v1",
        "case_stream_sha256": "c" * 64,
        "roots": {},
        "prompt_sha256": "d" * 64,
        "answerer_model": "answerer",
        "temperature": 0.0,
        "arms": ["control", "repaired"],
        "binding_root": "e" * 64,
        "paired_prediction_head": head,
        "paired_prediction_count": 4,
        "paired_case_count": 2,
        "paired_predictions_sha256": hashlib.sha256(journal.read_bytes()).hexdigest(),
        "gold_opened": False,
        "router_updated_from_predictions": False,
        "sealed": True,
    }
    _write_json(run / "prediction_seal.json", seal)
    _write_json(
        run / "manifest.json",
        {
            "schema_version": "cmd-p4c2-live-efficacy-v1",
            "status": "prediction_sealed",
            "runtime_gold_free": True,
            "external_calls_authorized": True,
            "paired_case_count": 2,
            "paired_prediction_count": 4,
            "paired_prediction_head": head,
            "paired_predictions_sha256": hashlib.sha256(journal.read_bytes()).hexdigest(),
            "binding_root": "e" * 64,
            "roots": {},
            "prediction_seal_sha256": hashlib.sha256((run / "prediction_seal.json").read_bytes()).hexdigest(),
            "router_feedback": "EccRepairReceipt-only",
            "router_updated_from_predictions": False,
            "sealed_evaluator": "not_opened_by_runtime",
        },
    )
    return run


def _sidecar(path: Path) -> Path:
    rows = [
        {"schema_version": "cmd-p4c6-sealed-sidecar-v1", "case_id": "p4c1-longmemeval-q1", "question": "current?", "reference": "new"},
        {"schema_version": "cmd-p4c6-sealed-sidecar-v1", "case_id": "p4c1-longmemeval-q2", "question": "number?", "reference": "right"},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def test_preflight_validates_seal_chain_roots_and_complete_pairs(tmp_path: Path) -> None:
    run = _p4c2_fixture(tmp_path)
    report, pairs = preflight(p4c2_run=run, sealed_sidecar=_sidecar(tmp_path / "sidecar.jsonl"))
    assert report["preflight_passed"] is True
    assert report["paired_case_count"] == 2
    assert len(pairs) == 2

    lines = (run / "paired_predictions.jsonl").read_text().splitlines()
    (run / "paired_predictions.jsonl").write_text("\n".join(lines[:-1]) + "\n")
    with pytest.raises(ValueError, match="root|count|pair"):
        preflight(p4c2_run=run, sealed_sidecar=tmp_path / "sidecar.jsonl")


def test_prepare_sidecar_requires_seal_then_projects_string_and_integer_answers(tmp_path: Path) -> None:
    run = _p4c2_fixture(tmp_path)
    raw = tmp_path / "long.json"
    _write_json(
        raw,
        [
            {"question_id": "q1", "question": "current?", "answer": "new"},
            {"question_id": "q2", "question": "number?", "answer": 7},
        ],
    )
    target = tmp_path / "sealed" / "sidecar.jsonl"
    result = prepare_sidecar(p4c2_run=run, longmemeval_data=raw, output=target)
    rows = [json.loads(line) for line in target.read_text().splitlines()]
    assert result["case_count"] == 2
    assert rows[1]["reference"] == 7
    assert all(set(row) == {"schema_version", "case_id", "question", "reference"} for row in rows)


def test_exact_evaluation_is_append_only_resumable_and_never_outputs_reference(tmp_path: Path) -> None:
    run = _p4c2_fixture(tmp_path)
    sidecar = _sidecar(tmp_path / "sidecar.jsonl")
    output = tmp_path / "evaluation"
    runtime_before = {path.name: path.read_bytes() for path in run.iterdir() if path.is_file()}

    report = evaluate(p4c2_run=run, sealed_sidecar=sidecar, output=output, backend="exact")

    assert report["external_call_count"] == 0
    assert report["metrics"]["control_accuracy"] == 0.5
    assert report["metrics"]["repaired_accuracy"] == 0.5
    assert report["metrics"]["paired_delta"] == 0.0
    assert report["metrics"]["recovery_count"] == 1
    assert report["metrics"]["harm_count"] == 1
    artifacts = (output / "evaluation_outcomes.jsonl").read_text() + (output / "evaluation_report.json").read_text()
    assert '"reference"' not in artifacts
    assert "current?" not in artifacts
    before = (output / "evaluation_outcomes.jsonl").read_bytes()
    resumed = evaluate(p4c2_run=run, sealed_sidecar=sidecar, output=output, backend="exact", run_mode="resume")
    assert resumed == report
    assert (output / "evaluation_outcomes.jsonl").read_bytes() == before
    assert {path.name: path.read_bytes() for path in run.iterdir() if path.is_file()} == runtime_before


def test_semantic_evaluation_requires_explicit_judge_and_records_only_verdicts(tmp_path: Path) -> None:
    class Judge:
        model_id = "sealed-semantic-judge-v1"

        def __init__(self) -> None:
            self.calls = 0

        def verdict(self, *, question: str, hypothesis: str, reference: str) -> bool:
            self.calls += 1
            return hypothesis == reference

    run = _p4c2_fixture(tmp_path)
    sidecar = _sidecar(tmp_path / "sidecar.jsonl")
    with pytest.raises(ValueError, match="explicit config and execute"):
        evaluate(
            p4c2_run=run,
            sealed_sidecar=sidecar,
            output=tmp_path / "blocked",
            backend="openai-compatible",
        )

    judge = Judge()
    report = evaluate(
        p4c2_run=run,
        sealed_sidecar=sidecar,
        output=tmp_path / "semantic",
        backend="openai-compatible",
        judge=judge,
    )
    assert judge.calls == 4
    assert report["external_call_count"] == 4
    assert report["judge_model"] == "sealed-semantic-judge-v1"
