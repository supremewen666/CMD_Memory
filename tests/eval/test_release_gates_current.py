from __future__ import annotations

import csv
from pathlib import Path

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
                "cases": "3",
                "triggered_cases": "3",
                "positive_recovery_rate": "0.67",
                "mean_recovery_gain": "0.42",
                "cost_per_diagnosis": "1.20",
                "provenance_completeness": "1.0",
            },
        ],
    )
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
    criterion_ids = {criterion.criterion_id for criterion in result.criteria}
    assert "operator_recovery_gain_metrics" in criterion_ids
    assert "step_level_attribution_metrics" in criterion_ids
    assert "macro_f1_exceeds_baselines" not in criterion_ids
    assert "confusion_diagonal_dominance" not in criterion_ids
    assert "accuracy_top2_exceeds_baselines" not in criterion_ids


def test_runtime_integration_gate_uses_semantic_gate_id() -> None:
    result = check_runtime_integration_gate(
        mem0_integrated=True,
        letta_integrated=True,
    )

    assert result.gate_id == "runtime_integration"
    assert result.all_passed is True
