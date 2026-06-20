from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace

from cmd_audit import write_step_level_metrics_table
from cmd_audit.counterfactual import PipelineAction


def _mcts(primary, credit: float = 0.7, *, include_identity: bool = True):
    action_credits = {0: {primary: credit}}
    if include_identity:
        action_credits[0][PipelineAction.IDENTITY] = 0.0
    return SimpleNamespace(
        primary_attribution_label=primary,
        main_culprit=(0, primary, credit),
        action_credits=action_credits,
    )


def _audit(runtime_branch: str, perturbation_label, mcts_result):
    return SimpleNamespace(
        runtime_branch=runtime_branch,
        perturbation_label=perturbation_label,
        mcts_result=mcts_result,
    )


def _read_metrics(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            row["metric_name"]: row
            for row in csv.DictReader(handle)
        }


def test_step_level_metrics_writer_uses_mcts_pipeline_denominator(tmp_path: Path) -> None:
    path = tmp_path / "step_level_metrics.csv"
    results = [
        _audit("fix", "retrieval_error", _mcts(PipelineAction.RETRIEVAL_ERROR)),
        _audit("fix", "injection_error", _mcts(PipelineAction.RETRIEVAL_ERROR, 0.2)),
        _audit("fix", "safety_error", None),
        _audit("fill", "retrieval_error", _mcts(PipelineAction.RETRIEVAL_ERROR)),
        _audit("fix", "item_wrong", _mcts(PipelineAction.RETRIEVAL_ERROR)),
        _audit("fix", None, _mcts(PipelineAction.RETRIEVAL_ERROR)),
    ]

    write_step_level_metrics_table(results, path)

    metrics = _read_metrics(path)
    coverage = metrics["step_attribution_coverage"]
    correctness = metrics["primary_label_correctness"]

    assert coverage["numerator"] == "2"
    assert coverage["denominator"] == "3"
    assert coverage["value"] == "0.666667"
    assert correctness["numerator"] == "1"
    assert correctness["denominator"] == "2"
    assert correctness["value"] == "0.500000"


def test_step_level_metrics_writer_tracks_identity_and_positive_credit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "step_level_metrics.csv"
    results = [
        _audit("fix", "retrieval_error", _mcts(PipelineAction.RETRIEVAL_ERROR, 0.3)),
        _audit(
            "fix",
            "injection_error",
            _mcts(PipelineAction.INJECTION_ERROR, 0.0, include_identity=False),
        ),
    ]

    write_step_level_metrics_table(results, path)

    metrics = _read_metrics(path)
    assert metrics["identity_baseline_coverage"]["numerator"] == "1"
    assert metrics["identity_baseline_coverage"]["denominator"] == "2"
    assert metrics["positive_credit_rate"]["numerator"] == "1"
    assert metrics["positive_credit_rate"]["denominator"] == "2"
