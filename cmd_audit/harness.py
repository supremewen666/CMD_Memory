"""Public CMD-Audit V0 harness entry points."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .attribution import AttributionResult, assign_attribution
from .baselines import BaselineSuiteResult, run_baseline_suite
from .metrics import DiagnosisPrediction, compute_diagnosis_metrics
from .models import ProbeCase
from .post_repair import (
    ECSDraft,
    PostRepairResult,
    RepairedContext,
    build_repaired_context,
    draft_ecs,
    run_hard_case_update_baseline,
    run_post_repair_context_replay,
)
from .repairs import (
    RepairComparisonRow,
    make_repair_comparison,
    write_repair_success_table,
)
from .replays import ReplayResult, run_v0_replay_portfolio
from .writers import (
    REPLAY_TABLE_ORDER,
    write_attribution_table,
    write_confusion_matrix_table,
    write_csv_table,
    write_post_repair_table,
    write_retrieval_metrics_table,
    write_retrieval_trace_table,
)


@dataclass(frozen=True)
class AuditResult:
    case_id: str
    perturbation_label: str
    baseline_name: str
    baseline_answer_score: float
    baseline_evidence_score: float
    replays: tuple[ReplayResult, ...]
    attribution: AttributionResult
    baseline_suite: BaselineSuiteResult

    @property
    def attribution_correct(self) -> bool:
        return self.attribution.predicted_label == self.perturbation_label

    @property
    def replay(self) -> ReplayResult:
        return self.replay_by_name(self.attribution.top_replay)

    @property
    def diagnosis_cost(self) -> float:
        return self.baseline_suite.monitor.cost_per_decision + sum(
            replay.cost_units for replay in self.replays
        )

    def replay_by_name(self, replay_name: str) -> ReplayResult:
        for replay in self.replays:
            if replay.replay_name == replay_name:
                return replay
        raise KeyError(f"{self.case_id}: replay {replay_name!r} did not run")


@dataclass(frozen=True)
class FullAuditResult:
    """Complete CMD-Audit pipeline result including Post-Repair Context Replay."""

    audit: AuditResult
    ecs_draft: ECSDraft
    repaired_context: RepairedContext
    post_repair: PostRepairResult
    hard_case_baseline: PostRepairResult


def run_case_full(case: ProbeCase) -> FullAuditResult:
    """Run the complete V0 pipeline: attribution -> ECS -> repair -> post-repair replay."""
    audit = run_case(case)
    ecs_draft = draft_ecs(case, audit)
    repaired_context = build_repaired_context(case, ecs_draft)
    post_repair = run_post_repair_context_replay(case, repaired_context)
    hard_case_baseline = run_hard_case_update_baseline(case)
    return FullAuditResult(
        audit=audit,
        ecs_draft=ecs_draft,
        repaired_context=repaired_context,
        post_repair=post_repair,
        hard_case_baseline=hard_case_baseline,
    )


def run_case(case: ProbeCase) -> AuditResult:
    baseline_suite = run_baseline_suite(case)
    replays = run_v0_replay_portfolio(case)
    attribution = assign_attribution(replays)
    baseline = case.primary_baseline
    return AuditResult(
        case_id=case.case_id,
        perturbation_label=case.perturbation_label,
        baseline_name=baseline.baseline_name,
        baseline_answer_score=baseline.answer_score,
        baseline_evidence_score=baseline.evidence_score,
        replays=replays,
        attribution=attribution,
        baseline_suite=baseline_suite,
    )


def run_cases(cases: list[ProbeCase]) -> list[AuditResult]:
    return [run_case(case) for case in cases]


def run_cases_full(cases: list[ProbeCase]) -> list[FullAuditResult]:
    return [run_case_full(case) for case in cases]


def write_repair_success_table_from_full(
    results: list[FullAuditResult],
    output_path: str | Path,
    *,
    sandbox_root: str | Path | None = None,
) -> list[RepairComparisonRow]:
    """Build repair comparison rows from full pipeline results and write the table."""
    rows = [make_repair_comparison(fr) for fr in results]
    write_repair_success_table(rows, output_path, sandbox_root=sandbox_root)
    return rows


def diagnosis_predictions(result: AuditResult) -> tuple[DiagnosisPrediction, ...]:
    predictions = [
        DiagnosisPrediction(
            system_name="CMD-Audit",
            case_id=result.case_id,
            gold_label=result.perturbation_label,
            predicted_label=result.attribution.predicted_label,
            top2_labels=result.attribution.top2_labels,
            cost_per_diagnosis=result.diagnosis_cost,
        )
    ]
    for comparator in result.baseline_suite.comparator_results:
        predictions.append(
            DiagnosisPrediction(
                system_name=comparator.comparator_name,
                case_id=result.case_id,
                gold_label=result.perturbation_label,
                predicted_label=comparator.predicted_label,
                top2_labels=comparator.top2_labels,
                cost_per_diagnosis=comparator.cost_per_diagnosis,
            )
        )
    return tuple(predictions)


def write_comparison_metrics_table(
    results: list[AuditResult],
    output_path: str | Path,
) -> None:
    predictions = [
        prediction
        for result in results
        for prediction in diagnosis_predictions(result)
    ]
    metrics = compute_diagnosis_metrics(predictions)

    fieldnames = [
        "system_name",
        "attribution_accuracy",
        "macro_f1",
        "top2_accuracy",
        "cost_per_diagnosis",
    ]
    rows = [
        {
            "system_name": row.system_name,
            "attribution_accuracy": f"{row.attribution_accuracy:.3f}",
            "macro_f1": f"{row.macro_f1:.3f}",
            "top2_accuracy": f"{row.top2_accuracy:.3f}",
            "cost_per_diagnosis": f"{row.cost_per_diagnosis:.3f}",
        }
        for system_name in sorted(metrics)
        for row in [metrics[system_name]]
    ]
    write_csv_table(output_path, fieldnames, rows)
