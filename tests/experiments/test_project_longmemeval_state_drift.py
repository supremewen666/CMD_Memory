import json
from pathlib import Path

from experiments.project_longmemeval_state_drift import project


def test_projection_separates_runtime_and_scorer_fields(tmp_path: Path) -> None:
    dataset = Path("data/external/longmemeval/input/longmemeval_s_cleaned.json")
    interventions = tmp_path / "interventions.jsonl"
    labels = tmp_path / "labels.jsonl"
    manifest_path = tmp_path / "manifest.json"
    manifest = project(
        dataset=dataset, interventions_output=interventions,
        labels_output=labels, manifest_output=manifest_path, limit=2,
    )
    runtime_rows = [json.loads(line) for line in interventions.read_text().splitlines()]
    label_rows = [json.loads(line) for line in labels.read_text().splitlines()]
    forbidden = {"answer", "answer_session_ids", "question_type", "has_answer", "old_value", "new_value"}
    assert len(runtime_rows) == len(label_rows) == manifest["runtime_case_count"] == 2
    assert all(not (set(row) & forbidden) for row in runtime_rows)
    assert all(row["schema_version"] == "cmd-ecc-state-drift-evaluator-label-v2" for row in label_rows)
    assert all(row["old_value"] is None for row in label_rows)
    assert manifest["runtime_projection_uses_reference_targets"] is False


def test_full_projection_records_frozen_recall_exclusion(tmp_path: Path) -> None:
    manifest = project(
        dataset=Path("data/external/longmemeval/input/longmemeval_s_cleaned.json"),
        interventions_output=tmp_path / "i.jsonl", labels_output=tmp_path / "l.jsonl",
        manifest_output=tmp_path / "m.json",
    )
    assert manifest["runtime_case_count"] == 77
    assert len(manifest["excluded_cases"]) == 1
