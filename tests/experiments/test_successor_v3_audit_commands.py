"""TDD coverage for the standalone, outcome-blind v3 audit commands."""

from __future__ import annotations

import json
from pathlib import Path

from cmd_audit.eval.successor_protocol_freeze import (
    VALIDATOR_VERSION,
    canonical_manifest_sha256,
    validate_protocol_freeze,
)

from experiments.audit_predicate_activity import main as activity_main
from experiments.audit_runtime_shortcuts import main as shortcuts_main
from experiments.check_successor_v3_gates import main as gates_main
from experiments.run_actionability_audit import main as actionability_main
from experiments.run_relation_calibration import main as relation_main
from tests.eval.test_successor_protocol_freeze import _make_fixture


def _manifest() -> dict[str, object]:
    return {
        "schema_version": "route-a-successor-v3-freeze-schema-v2",
        "protocol_id": "route-a-successor-semantic-actionability-v3",
        "freeze_stage": "F1",
        "gates": {
            "g0": {
                "relation_precision_min": 0.8,
                "relation_recall_min": 0.8,
                "permutation_fpr_max": 0.1,
                "canary_recall_min": 0.8,
                "abstention_rate_max": 0.1,
                "confidence_level": 0.95,
                "bootstrap_iterations": 100,
                "bootstrap_seed": 1,
                "min_pairs": 2,
                "min_positive_pairs": 1,
                "min_negative_pairs": 1,
                "min_families": 2,
            },
            "g1": {
                "target_precision_min": 0.9,
                "target_recall_min": 0.5,
                "ordering_coverage_min": 0.5,
                "destructive_coverage_min": 0.25,
                "unknown_rate_max": 0.5,
                "conflict_rate_max": 0.1,
                "confidence_level": 0.95,
                "bootstrap_iterations": 100,
                "bootstrap_seed": 2,
                "min_pairs": 1,
                "min_directional_pairs": 1,
                "min_families": 1,
            },
            "g2": {
                "min_firing_cases": 1,
                "min_firing_families": 1,
                "null_false_fire_max": 0.0,
                "field_alignment_max": 0.8,
                "nmi_alarm_max": 0.8,
                "permutation_target_precision_max": 0.5,
                "reusable_value_unique_ratio_max": 0.5,
            },
        },
    }


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _write_validation(path: Path, manifest: dict[str, object]) -> None:
    path.write_text(
        json.dumps({
            "valid": True,
            "reasons": [],
            "validator_version": VALIDATOR_VERSION,
            "manifest_sha256": canonical_manifest_sha256(manifest),
            "recomputed_hashes": {},
        }),
        encoding="utf-8",
    )


def test_relation_calibration_is_jsonl_backed_and_refuses_unfrozen_thresholds(tmp_path: Path) -> None:
    manifest = tmp_path / "thresholds.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    observations = tmp_path / "relation.jsonl"
    _write_jsonl(observations, [
        {"family_id": "cal-p", "expected_positive": True, "predicted_positive": True, "lane": "calibration"},
        {"family_id": "cal-n", "expected_positive": False, "predicted_positive": False, "lane": "calibration"},
        {"family_id": "perm", "expected_positive": False, "predicted_positive": False, "lane": "permutation"},
        {"family_id": "canary", "expected_positive": True, "predicted_positive": True, "lane": "canary"},
    ])
    output = tmp_path / "relation_gate.json"
    assert relation_main(["--threshold-manifest", str(manifest), "--observations-jsonl", str(observations), "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["decision"] == "GO"
    assert report["runtime_uses_gold"] is False
    assert len(report["report_sha256"]) == 64

    frozen = _manifest()
    frozen["freeze_stage"] = "F0"
    manifest.write_text(json.dumps(frozen), encoding="utf-8")
    assert relation_main(["--threshold-manifest", str(manifest), "--observations-jsonl", str(observations), "--output", str(output)]) == 2


def test_actionability_audit_keeps_pair_detection_separate_from_target_accuracy(tmp_path: Path) -> None:
    manifest = tmp_path / "thresholds.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    rows = tmp_path / "actionability.jsonl"
    _write_jsonl(rows, [
        {"family_id": "f1", "expected_target_id": "old", "predicted_target_id": "old", "destructive_authorized": True, "ordering_state": "resolved", "evidence_deployment_visible": True, "evidence_trusted": True},
        {"family_id": "f2", "expected_target_id": "old2", "predicted_target_id": None, "destructive_authorized": False, "ordering_state": "unknown", "evidence_deployment_visible": False, "evidence_trusted": False},
    ])
    output = tmp_path / "actionability_gate.json"
    assert actionability_main(["--threshold-manifest", str(manifest), "--observations-jsonl", str(rows), "--output", str(output)]) == 2
    assert json.loads(output.read_text())["gate"]["measurements"]["destructive_coverage"] == 0.5

    _write_jsonl(rows, [{"family_id": "f", "expected_target_id": "old", "predicted_target_id": "old", "destructive_authorized": False}])
    assert actionability_main(["--threshold-manifest", str(manifest), "--observations-jsonl", str(rows), "--output", str(output)]) == 2


def test_activity_audit_counts_only_action_compatible_fires(tmp_path: Path) -> None:
    manifest = tmp_path / "thresholds.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    events = tmp_path / "activity.jsonl"
    _write_jsonl(events, [
        {"predicate": "superseded_item", "case_id": "c1", "family_id": "f1", "fired": True, "action_compatible": True, "null_case": False},
        {"predicate": "divergent_pair_member", "case_id": "c1", "family_id": "f1", "fired": True, "action_compatible": True, "null_case": False},
        {"predicate": "divergent_pair_member", "case_id": "c2", "family_id": "f2", "fired": False, "action_compatible": False, "null_case": False},
    ])
    output = tmp_path / "activity_gate.json"
    assert activity_main(["--threshold-manifest", str(manifest), "--events-jsonl", str(events), "--output", str(output)]) == 0
    predicates = {row["predicate"] for row in json.loads(output.read_text())["activities"]}
    assert predicates == {"divergent_pair_member", "superseded_item"}


def test_shortcut_audit_fails_on_a_reusable_hidden_target_marker(tmp_path: Path) -> None:
    manifest = tmp_path / "thresholds.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    items = tmp_path / "items.jsonl"
    _write_jsonl(items, [
        {"case_id": "c1", "item_id": "old", "is_target": True, "fields": {"marker": "old"}, "permutation_predicted_target": True},
        {"case_id": "c1", "item_id": "new", "is_target": False, "fields": {"marker": "new"}, "permutation_predicted_target": True},
        {"case_id": "c2", "item_id": "old", "is_target": True, "fields": {"marker": "old"}, "permutation_predicted_target": True},
        {"case_id": "c2", "item_id": "new", "is_target": False, "fields": {"marker": "new"}, "permutation_predicted_target": True},
    ])
    output = tmp_path / "shortcut_gate.json"
    assert shortcuts_main(["--threshold-manifest", str(manifest), "--items-jsonl", str(items), "--output", str(output)]) == 2
    assert "marker" in json.loads(output.read_text())["gate"]["flagged_fields"]


def test_gate_combiner_verifies_content_and_manifest_binding(tmp_path: Path) -> None:
    manifest_payload, dataset_path, prompt_path = _make_fixture(tmp_path)
    manifest_payload["gates"]["g0"].update({
        "min_pairs": 2,
        "min_positive_pairs": 1,
        "min_negative_pairs": 1,
        "min_families": 2,
    })
    manifest_payload["gates"]["g1"].update({
        "min_pairs": 1,
        "min_directional_pairs": 1,
        "min_families": 1,
    })
    manifest = tmp_path / "protocol_freeze.json"
    manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
    validation = tmp_path / "protocol_validation.json"
    validation.write_text(
        json.dumps(validate_protocol_freeze(
            manifest_payload,
            dataset_path=dataset_path,
            repo_root=tmp_path,
            prompt_path=prompt_path,
        ).as_dict()),
        encoding="utf-8",
    )
    reports = {name: tmp_path / f"{name}.json" for name in ("relation", "actionability", "activity", "shortcuts")}
    relation_rows = tmp_path / "relation.jsonl"
    actionability_rows = tmp_path / "actionability.jsonl"
    activity_rows = tmp_path / "activity.jsonl"
    shortcut_rows = tmp_path / "shortcuts.jsonl"
    _write_jsonl(relation_rows, [
        {"family_id": "f1", "expected_positive": True, "predicted_positive": True, "lane": "calibration"},
        {"family_id": "f2", "expected_positive": False, "predicted_positive": False, "lane": "calibration"},
        {"family_id": "p", "expected_positive": False, "predicted_positive": False, "lane": "permutation"},
        {"family_id": "c", "expected_positive": True, "predicted_positive": True, "lane": "canary"},
    ])
    _write_jsonl(actionability_rows, [
        {"family_id": "f1", "expected_target_id": "old", "predicted_target_id": "old", "destructive_authorized": True, "ordering_state": "resolved", "evidence_deployment_visible": True, "evidence_trusted": True},
    ])
    _write_jsonl(activity_rows, [
        {"predicate": "superseded_item", "case_id": "c1", "family_id": "f1", "fired": True, "action_compatible": True, "null_case": False},
        {"predicate": "divergent_pair_member", "case_id": "c1", "family_id": "f1", "fired": True, "action_compatible": True, "null_case": False},
    ])
    _write_jsonl(shortcut_rows, [
        {"case_id": "c1", "item_id": "old", "is_target": True, "fields": {"safe": "same"}, "permutation_predicted_target": True},
        {"case_id": "c1", "item_id": "new", "is_target": False, "fields": {"safe": "same"}, "permutation_predicted_target": True},
        {"case_id": "c2", "item_id": "old", "is_target": True, "fields": {"safe": "same"}, "permutation_predicted_target": True},
        {"case_id": "c2", "item_id": "new", "is_target": False, "fields": {"safe": "same"}, "permutation_predicted_target": True},
    ])
    assert relation_main(["--threshold-manifest", str(manifest), "--observations-jsonl", str(relation_rows), "--output", str(reports["relation"])]) == 0
    assert actionability_main(["--threshold-manifest", str(manifest), "--observations-jsonl", str(actionability_rows), "--output", str(reports["actionability"])]) == 0
    assert activity_main(["--threshold-manifest", str(manifest), "--events-jsonl", str(activity_rows), "--output", str(reports["activity"])]) == 0
    assert shortcuts_main(["--threshold-manifest", str(manifest), "--items-jsonl", str(shortcut_rows), "--output", str(reports["shortcuts"])]) == 0
    bundle = tmp_path / "bundle.json"
    args = [
        "--threshold-manifest",
        str(manifest),
        "--protocol-validation",
        str(validation),
        "--output",
        str(bundle),
    ]
    for name, path in reports.items():
        args.extend([f"--{name}-report", str(path)])
    assert gates_main(args) == 0
    assert json.loads(bundle.read_text())["open_synthesis_authorized"] is False

    manifest_payload["gates"]["g0"]["relation_precision_min"] = 0.81
    manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
    assert gates_main(args) == 2
    assert json.loads(bundle.read_text())["decision"] == "REFUSE"
