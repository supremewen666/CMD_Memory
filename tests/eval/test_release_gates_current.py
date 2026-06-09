from __future__ import annotations

import csv
from pathlib import Path

from cmd_audit.core.labels import PIPELINE_LABEL_ORDER
from cmd_audit.eval import release_gates
from cmd_audit.eval.release_gates import (
    _check_step_level_metrics,
    check_attribution_release_gate,
    check_runtime_integration_gate,
)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_release_gate_symbols_do_not_use_version_identifiers() -> None:
    old_attribution_symbol = "check_" + "v0" + "_to_" + "v1" + "_gate"
    old_runtime_symbol = "check_" + "v1" + "_to_" + "v2" + "_gate"
    old_criterion_symbol = "V" + "0" + "V" + "1" + "_CRITERION_IDS"

    assert not hasattr(release_gates, old_attribution_symbol)
    assert not hasattr(release_gates, old_runtime_symbol)
    assert not hasattr(release_gates, old_criterion_symbol)


def test_step_level_metrics_gate_passes_when_required_metrics_meet_thresholds(
    tmp_path: Path,
) -> None:
    path = tmp_path / "step_level_metrics.csv"
    _write_csv(
        path,
        [
            {"metric_name": "step_attribution_coverage", "value": "0.95"},
            {"metric_name": "identity_baseline_coverage", "value": "1.0"},
            {"metric_name": "positive_credit_rate", "value": "0.80"},
            {"metric_name": "primary_label_correctness", "value": "0.80"},
        ],
    )

    criterion = _check_step_level_metrics(path)

    assert criterion.passed is True
    assert criterion.criterion_id == "step_level_attribution_metrics"


def test_step_level_metrics_gate_fails_when_metric_is_missing(tmp_path: Path) -> None:
    path = tmp_path / "step_level_metrics.csv"
    _write_csv(
        path,
        [
            {"metric_name": "step_attribution_coverage", "value": "0.95"},
            {"metric_name": "identity_baseline_coverage", "value": "1.0"},
        ],
    )

    criterion = _check_step_level_metrics(path)

    assert criterion.passed is False
    assert "positive_credit_rate" in criterion.missing


def test_attribution_release_gate_includes_step_level_metrics(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    sandbox = artifacts / "sandbox"
    _write_csv(
        artifacts / "comparison_metrics.csv",
        [
            {
                "system_name": "CMD-Audit",
                "attribution_accuracy": "0.95",
                "macro_f1": "0.94",
                "top2_accuracy": "1.0",
            },
            {
                "system_name": "evidence_recall",
                "attribution_accuracy": "0.70",
                "macro_f1": "0.65",
                "top2_accuracy": "0.80",
            },
            {
                "system_name": "subagent_judge",
                "attribution_accuracy": "0.72",
                "macro_f1": "0.68",
                "top2_accuracy": "0.82",
            },
            {
                "system_name": "random_label",
                "attribution_accuracy": "0.20",
                "macro_f1": "0.20",
                "top2_accuracy": "0.40",
            },
        ],
    )
    confusion_rows = []
    for label in PIPELINE_LABEL_ORDER:
        row = {"gold_label": label}
        row.update({candidate: "0" for candidate in PIPELINE_LABEL_ORDER})
        row[label] = "3"
        confusion_rows.append(row)
    _write_csv(artifacts / "attribution_confusion_matrix.csv", confusion_rows)
    _write_csv(
        sandbox / "post_repair_table.csv",
        [
            {"case_id": "case-1", "repair_assessment": "recovered"},
            {"case_id": "case-2", "repair_assessment": "recovered"},
            {"case_id": "case-3", "repair_assessment": "partial"},
        ],
    )
    _write_csv(
        artifacts / "step_level_metrics.csv",
        [
            {"metric_name": "step_attribution_coverage", "value": "0.95"},
            {"metric_name": "identity_baseline_coverage", "value": "1.0"},
            {"metric_name": "positive_credit_rate", "value": "0.80"},
            {"metric_name": "primary_label_correctness", "value": "0.80"},
        ],
    )

    result = check_attribution_release_gate(
        artifacts_dir=artifacts,
        sandbox_dir=sandbox,
    )

    assert result.gate_id == "attribution_evidence"
    assert result.all_passed is True
    assert "step_level_attribution_metrics" in {
        criterion.criterion_id for criterion in result.criteria
    }


def test_runtime_integration_gate_uses_semantic_gate_id() -> None:
    result = check_runtime_integration_gate(
        mem0_integrated=True,
        letta_integrated=True,
    )

    assert result.gate_id == "runtime_integration"
    assert result.all_passed is True
