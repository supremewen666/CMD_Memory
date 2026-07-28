"""Shared artifact writers for CMD-Audit.

Consolidates the CSV table-writing pattern duplicated across harness, repairs,
failure_memory, and version_gates modules. Also provides shared text-file
writers for summary and ledger artifacts.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from cmd_audit.core.labels import ITEM_LABELS, PIPELINE_LABEL_ORDER, PIPELINE_STEP_ACTIONS
from cmd_audit.core.models import MemoryItem
from .provenance import compute_provenance_completeness, judge_provenance_fields

if TYPE_CHECKING:
    from cmd_audit.harness import AuditResult
    from cmd_audit.scoring import RetrievalBaselineSuiteResult


REPLAY_TABLE_ORDER = (
    "oracle_write",
    "oracle_compression",
    "verbatim_event_oracle",
    "oracle_retrieval",
    "injection_oracle",
    "evidence_given_reasoning",
)


# ── Recovery-value aggregation (NaN = timed-out rollout) ─────────────────
#
# A timed-out rollout carries ``recovery_gain = NaN`` (see
# ``counterfactual.rollout.RolloutResult.status``). NaN must never reach a
# mean/rate as if it were 0.0 — that understates recovery for the whole batch —
# and must never be written into a CSV cell as a bare float, where downstream
# averaging could silently coerce it. Every recovery aggregation in this module
# goes through these helpers, and every recovery table carries the matching
# ``timeout_count`` so an excluded value is always visible.

RECOVERY_TIMEOUT_TOKEN = "nan"


def is_timeout_value(value: object) -> bool:
    """True if ``value`` is the NaN sentinel written by a timed-out rollout."""
    return isinstance(value, float) and math.isnan(value)


def format_recovery_value(value: float | None, *, digits: int = 3) -> str:
    """Format a recovery value for a CSV cell.

    ``None`` (no value) becomes an empty string; a timed-out (NaN) value
    becomes the explicit ``nan`` token so it is never mistaken for 0.0.
    """
    if value is None:
        return ""
    if is_timeout_value(value):
        return RECOVERY_TIMEOUT_TOKEN
    return f"{float(value):.{digits}f}"


def recovery_timeout_count(values: Iterable[float]) -> int:
    """Number of timed-out (NaN) recovery values."""
    return sum(1 for value in values if is_timeout_value(value))


def finite_recovery_values(values: Iterable[float]) -> list[float]:
    """Recovery values with timed-out (NaN) entries dropped."""
    return [float(value) for value in values if not is_timeout_value(value)]


def recovery_mean(values: Iterable[float]) -> float:
    """Mean recovery gain over non-timed-out values (0.0 when none remain)."""
    finite = finite_recovery_values(values)
    return sum(finite) / len(finite) if finite else 0.0


def recovery_positive_rate(values: Iterable[float]) -> float:
    """Share of non-timed-out recovery values that are strictly positive."""
    finite = finite_recovery_values(values)
    return sum(1 for value in finite if value > 0.0) / len(finite) if finite else 0.0


def nan_safe_max(*values: float) -> float:
    """Max over recovery values, excluding timeouts unless every value is NaN."""
    finite = finite_recovery_values(values)
    return max(finite) if finite else float("nan")


def best_scored_pair(
    candidates: Iterable[tuple[float, object]],
) -> tuple[float, object | None]:
    """NaN-safe argmax over ``(score, payload)`` pairs, skipping ``payload=None``.

    ``max(..., key=...)`` is order-dependent once NaN scores are present, so it
    cannot be trusted to keep a timed-out operator from winning. This mirrors the
    semantics of a plain ``if score > best`` loop.
    """
    best_score: float | None = None
    best_payload: object | None = None
    for score, payload in candidates:
        if payload is None or is_timeout_value(score):
            continue
        if best_score is None or score > best_score:
            best_score, best_payload = score, payload
    if best_payload is None:
        return float("nan"), None
    return best_score, best_payload


def recovery_case_outcomes(
    base_gain: float,
    arm_scores: dict[str, float],
    *,
    threshold: float,
) -> dict[str, bool] | None:
    """Per-arm recovered flags for one case, or ``None`` when it must be excluded.

    ``None`` means the case's identity-backbone rollout timed out
    (``base_gain`` is NaN). Every net gain for the case is then
    ``score - NaN == NaN``, so every ``net > threshold`` test is False and every
    seeded maximisation keeps its ``-1.0`` sentinel: the case would be tallied
    as "not recovered" instead of excluded — reintroducing exactly the downward
    bias the NaN timeout sentinel exists to remove. Callers must drop such cases
    from recovered/headroom tallies and record them distinctly in their detail
    table.
    """
    if is_timeout_value(base_gain):
        return None
    return {
        arm: (not is_timeout_value(score)) and score > threshold
        for arm, score in arm_scores.items()
    }


# ── Shared primitives ────────────────────────────────────────────────────


def write_csv_table(
    path: str | Path,
    fieldnames: list[str],
    rows: Iterable[dict[str, str]],
    *,
    sandbox_root: str | Path | None = None,
    judge_client: object | None = None,
    rubric_version: str | None = None,
) -> Path:
    """Write a CSV table, optionally stamping the frozen judge identity."""
    output = Path(path)
    if sandbox_root is not None:
        from cmd_audit.repair.post_repair import validate_sandbox_path
        validate_sandbox_path(output, sandbox_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output_fieldnames = list(fieldnames)
    output_rows = list(rows)
    if judge_client is not None:
        judge_fields = judge_provenance_fields(
            judge_client,
            rubric_version=rubric_version,
        )
        output_fieldnames.extend(["judge_base_url", "judge_model", "rubric_version"])
        output_rows = [{**row, **judge_fields} for row in output_rows]
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_fieldnames)
        writer.writeheader()
        for row in output_rows:
            writer.writerow(row)
    return output


def write_text_artifact(
    path: str | Path,
    lines: Iterable[str],
    *,
    sandbox_root: str | Path | None = None,
) -> Path:
    """Write a text artifact (summary, ledger, status), optionally sandbox-validated."""
    output = Path(path)
    if sandbox_root is not None:
        from cmd_audit.repair.post_repair import validate_sandbox_path
        validate_sandbox_path(output, sandbox_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def _append_judge_provenance(
    fieldnames: list[str],
    rows: list[dict[str, str]],
    *,
    judge_client: object | None,
    rubric_version: str | None,
) -> None:
    """Append frozen-judge identity to every row when explicitly requested."""
    if judge_client is None:
        return
    fields = judge_provenance_fields(
        judge_client,
        rubric_version=rubric_version,
    )
    fieldnames.extend(["judge_base_url", "judge_model", "rubric_version"])
    for row in rows:
        row.update(fields)


# ── Attribution table ────────────────────────────────────────────────────


def write_attribution_table(
    results: list[AuditResult],
    output_path: str | Path,
    *,
    judge_client: object | None = None,
    rubric_version: str | None = None,
) -> None:
    """Write the attribution table CSV."""
    fieldnames = [
        "case_id",
        "perturbation_label",
        "predicted_label",
        "top_replay",
        "baseline_name",
        "baseline_answer_score",
        "baseline_evidence_score",
        "baseline_evidence_score_llm",
        "baseline_answer_score_llm",
        "replay_answer_score",
        "replay_evidence_score",
        "recovery_gain",
    ]
    for replay_name in REPLAY_TABLE_ORDER:
        fieldnames.extend(
            [
                f"{replay_name}_answer_score",
                f"{replay_name}_evidence_score",
                f"{replay_name}_recovery_gain",
            ]
        )
    fieldnames.extend(
        [
            "top2_labels",
            "is_ambiguous",
            "top_k_labels",
            "close_deltas",
            "distractor_provenance_ids",
            "diagnosis_cost",
            "attribution_correct",
            # Per-row count of recovery values that timed out (written as the
            # explicit `nan` token, never as 0.0), so downstream averaging over
            # this table can drop them deliberately instead of silently.
            "timeout_count",
        ]
    )

    rows: list[dict[str, str]] = []
    for result in results:
        attribution = result.attribution
        replay = None
        if attribution is not None:
            try:
                replay = result.replay
            except KeyError:
                replay = None
        row_recovery_values: list[float] = []
        if attribution is not None:
            row_recovery_values.append(float(attribution.recovery_gain))
        row = {
            "case_id": result.case_id,
            "perturbation_label": result.perturbation_label,
            "predicted_label": attribution.predicted_label if attribution else "",
            "top_replay": attribution.top_replay if attribution else "",
            "baseline_name": result.baseline_name,
            "baseline_answer_score": f"{result.baseline_answer_score:.3f}",
            "baseline_evidence_score": f"{result.baseline_evidence_score:.3f}",
            "baseline_evidence_score_llm": (
                ""
                if result.baseline_evidence_score_llm is None
                else f"{result.baseline_evidence_score_llm:.3f}"
            ),
            "baseline_answer_score_llm": (
                ""
                if result.baseline_answer_score_llm is None
                else f"{result.baseline_answer_score_llm:.3f}"
            ),
            "replay_answer_score": f"{replay.answer_score:.3f}" if replay else "",
            "replay_evidence_score": f"{replay.evidence_score:.3f}" if replay else "",
            "recovery_gain": format_recovery_value(
                attribution.recovery_gain if attribution else None
            ),
            "top2_labels": "|".join(attribution.top2_labels) if attribution else "",
            "is_ambiguous": str(attribution.is_ambiguous).lower() if attribution else "",
            "top_k_labels": "|".join(attribution.top_k_labels) if attribution else "",
            "close_deltas": "|".join(
                f"{label}:{delta:.4f}"
                for label, delta in attribution.close_deltas
            ) if attribution else "",
            "distractor_provenance_ids": "|".join(
                attribution.distractor_provenance_ids
            ) if attribution else "",
            "diagnosis_cost": f"{result.diagnosis_cost:.3f}",
            "attribution_correct": str(result.attribution_correct).lower(),
        }
        replays_by_name = {replay.replay_name: replay for replay in result.replays}
        for replay_name in REPLAY_TABLE_ORDER:
            replay = replays_by_name.get(replay_name)
            if replay is None:
                row[f"{replay_name}_answer_score"] = ""
                row[f"{replay_name}_evidence_score"] = ""
                row[f"{replay_name}_recovery_gain"] = ""
                continue
            row[f"{replay_name}_answer_score"] = f"{replay.answer_score:.3f}"
            row[f"{replay_name}_evidence_score"] = f"{replay.evidence_score:.3f}"
            row[f"{replay_name}_recovery_gain"] = format_recovery_value(
                replay.recovery_gain
            )
            row_recovery_values.append(float(replay.recovery_gain))
        row["timeout_count"] = str(recovery_timeout_count(row_recovery_values))
        rows.append(row)

    _append_judge_provenance(
        fieldnames,
        rows,
        judge_client=judge_client,
        rubric_version=rubric_version,
    )
    write_csv_table(output_path, fieldnames, rows)


# ── Confusion matrix ─────────────────────────────────────────────────────


def write_confusion_matrix_table(
    results: list[AuditResult],
    output_path: str | Path,
    *,
    judge_client: object | None = None,
    rubric_version: str | None = None,
) -> None:
    """Write the CMD-Audit attribution confusion matrix CSV."""
    diagnosis_order = (*PIPELINE_LABEL_ORDER, *tuple(sorted(ITEM_LABELS)))
    counts = {
        gold_label: {predicted_label: 0 for predicted_label in diagnosis_order}
        for gold_label in diagnosis_order
    }
    for result in results:
        if result.attribution is None or result.perturbation_label is None:
            continue
        if (
            result.perturbation_label not in counts
            or result.attribution.predicted_label not in counts[result.perturbation_label]
        ):
            continue
        counts[result.perturbation_label][result.attribution.predicted_label] += 1

    fieldnames = ["gold_label", *diagnosis_order]
    rows: list[dict[str, str]] = []
    for gold_label in diagnosis_order:
        row: dict[str, str] = {"gold_label": gold_label}
        row.update({k: str(v) for k, v in counts[gold_label].items()})
        rows.append(row)

    _append_judge_provenance(
        fieldnames,
        rows,
        judge_client=judge_client,
        rubric_version=rubric_version,
    )
    write_csv_table(output_path, fieldnames, rows)


def write_provenance_completeness_summary(
    results: list[AuditResult],
    output_path: str | Path,
    *,
    judge_client: object | None = None,
    rubric_version: str | None = None,
) -> None:
    """Write per-case provenance completeness over replay evidence artifacts."""
    fieldnames = [
        "case_id",
        "replay_count",
        "replays_with_provenance",
        "provenance_completeness",
    ]
    rows: list[dict[str, str]] = []
    for result in results:
        replay_items = tuple(
            MemoryItem(
                memory_id=replay.replay_name,
                text=replay.evidence_block,
                provenance=replay.provenance_edges,
            )
            for replay in result.replays
        )
        replays_with_provenance = sum(1 for item in replay_items if item.provenance)
        rows.append(
            {
                "case_id": result.case_id,
                "replay_count": str(len(replay_items)),
                "replays_with_provenance": str(replays_with_provenance),
                "provenance_completeness": (
                    f"{compute_provenance_completeness(replay_items):.3f}"
                ),
            }
        )

    _append_judge_provenance(
        fieldnames,
        rows,
        judge_client=judge_client,
        rubric_version=rubric_version,
    )
    write_csv_table(output_path, fieldnames, rows)


# ── Step-level metrics ─────────────────────────────────────────────────────


def write_step_level_metrics_table(
    results: list[AuditResult],
    output_path: str | Path,
    *,
    judge_client: object | None = None,
    rubric_version: str | None = None,
) -> None:
    """Write aggregate step-level attribution metrics."""
    step_fix_cases = [
        result
        for result in results
        if result.runtime_branch == "fix"
        and result.perturbation_label in PIPELINE_STEP_ACTIONS
    ]
    mcts_primary_cases = [
        result
        for result in step_fix_cases
        if _mcts_primary_action_name(result) is not None
    ]

    identity_baseline_count = sum(
        1 for result in mcts_primary_cases if _mcts_has_identity_baseline(result)
    )
    primary_credits = [_mcts_primary_credit(result) for result in mcts_primary_cases]
    timeout_count = recovery_timeout_count(primary_credits)
    finite_credits = finite_recovery_values(primary_credits)
    positive_credit_count = sum(1 for credit in finite_credits if credit > 0.0)
    primary_correct_count = sum(
        1
        for result in mcts_primary_cases
        if _mcts_primary_action_name(result) == result.perturbation_label
    )

    rows = [
        _metric_row(
            "step_attribution_coverage",
            len(mcts_primary_cases),
            len(step_fix_cases),
            "Share of fix-branch pipeline-gold cases where attribution produced a primary step action.",
        ),
        _metric_row(
            "identity_baseline_coverage",
            identity_baseline_count,
            len(mcts_primary_cases),
            "Share of attributed cases with an identity sibling baseline in every credited generation point.",
        ),
        _metric_row(
            "positive_credit_rate",
            positive_credit_count,
            len(finite_credits),
            "Share of attributed cases whose primary action has positive credit "
            "(timed-out credits excluded from both numerator and denominator; "
            "see timeout_count).",
            timeout_count=timeout_count,
        ),
        _metric_row(
            "primary_label_correctness",
            primary_correct_count,
            len(mcts_primary_cases),
            "Share of attributed pipeline-gold cases whose primary action matches the gold step label.",
        ),
    ]

    fieldnames = [
        "metric_name",
        "value",
        "numerator",
        "denominator",
        "timeout_count",
        "description",
    ]
    _append_judge_provenance(
        fieldnames,
        rows,
        judge_client=judge_client,
        rubric_version=rubric_version,
    )
    write_csv_table(output_path, fieldnames, rows)


def _metric_row(
    metric_name: str,
    numerator: int,
    denominator: int,
    description: str,
    *,
    timeout_count: int = 0,
) -> dict[str, str]:
    value = numerator / denominator if denominator else 0.0
    return {
        "metric_name": metric_name,
        "value": f"{value:.6f}",
        "numerator": str(numerator),
        "denominator": str(denominator),
        "timeout_count": str(timeout_count),
        "description": description,
    }


def _mcts_primary_action_name(result: AuditResult) -> str | None:
    mcts_result = getattr(result, "mcts_result", None)
    if mcts_result is None:
        return None
    primary = getattr(mcts_result, "primary_attribution_label", None)
    if primary is None:
        return None
    return _action_name(primary)


def _mcts_primary_credit(result: AuditResult) -> float:
    mcts_result = getattr(result, "mcts_result", None)
    culprit = getattr(mcts_result, "main_culprit", None)
    if culprit is not None and len(culprit) >= 3:
        return float(culprit[2])
    return 0.0


def _mcts_has_identity_baseline(result: AuditResult) -> bool:
    mcts_result = getattr(result, "mcts_result", None)
    action_credits = getattr(mcts_result, "action_credits", {})
    if not action_credits:
        return False
    return all(
        any(_action_name(action) == "identity" for action in credits)
        for credits in action_credits.values()
    )


def _action_name(action: object) -> str:
    return str(getattr(action, "value", action))


# ── Post-repair table ────────────────────────────────────────────────────


def write_post_repair_table(
    results: list[AuditResult],
    output_path: str | Path,
    *,
    sandbox_root: str | Path | None = None,
    judge_client: object | None = None,
    rubric_version: str | None = None,
) -> None:
    """Write the Post-Repair Context Replay table to the sandbox."""
    fieldnames = [
        "case_id",
        "perturbation_label",
        "predicted_label",
        "pre_repair_answer_score",
        "pre_repair_evidence_score",
        "post_repair_answer_score",
        "post_repair_evidence_score",
        "repair_assessment",
        "repair_action",
        "hard_case_baseline_assessment",
        "token_cost",
        "regression_risk",
        "had_repair_regression",
    ]
    rows: list[dict[str, str]] = []
    for result in results:
        rows.append(
            {
                "case_id": result.case_id,
                "perturbation_label": result.perturbation_label,
                "predicted_label": result.attribution.predicted_label,
                "pre_repair_answer_score": f"{result.baseline_answer_score:.3f}",
                "pre_repair_evidence_score": f"{result.baseline_evidence_score:.3f}",
                "post_repair_answer_score": f"{result.post_repair.post_repair_answer_score:.3f}",
                "post_repair_evidence_score": f"{result.post_repair.post_repair_evidence_score:.3f}",
                "repair_assessment": result.post_repair.repair_assessment,
                "repair_action": result.attribution.predicted_label,
                "hard_case_baseline_assessment": result.hard_case_baseline.repair_assessment,
                "token_cost": f"{result.post_repair.token_cost:.1f}",
                "regression_risk": f"{result.post_repair.regression_risk:.3f}",
                "had_repair_regression": str(
                    result.post_repair.had_repair_regression
                ).lower(),
            }
        )

    _append_judge_provenance(
        fieldnames,
        rows,
        judge_client=judge_client,
        rubric_version=rubric_version,
    )
    write_csv_table(output_path, fieldnames, rows, sandbox_root=sandbox_root)


# ── Retrieval baseline tables ────────────────────────────────────────────


def write_retrieval_trace_table(
    suite_results: list[RetrievalBaselineSuiteResult],
    output_path: str | Path,
) -> None:
    """Write the full ranked retrieval trace table across all cases and retrievers."""
    fieldnames = [
        "case_id",
        "run_id",
        "retriever_name",
        "memory_id",
        "rank",
        "score",
        "token_cost",
        "retrieved_text",
        "matched_gold_evidence_units",
        "is_gold_support",
        "is_distractor",
    ]
    rows: list[dict[str, str]] = []
    for suite in suite_results:
        for result in suite.baseline_results:
            for trace in result.traces:
                rows.append(
                    {
                        "case_id": trace.case_id,
                        "run_id": trace.run_id,
                        "retriever_name": trace.retriever_name,
                        "memory_id": trace.memory_id,
                        "rank": str(trace.rank),
                        "score": f"{trace.score:.6f}",
                        "token_cost": f"{trace.token_cost:.1f}",
                        "retrieved_text": trace.retrieved_text,
                        "matched_gold_evidence_units": str(
                            trace.matched_gold_evidence_units
                        ),
                        "is_gold_support": str(trace.is_gold_support).lower(),
                        "is_distractor": str(trace.is_distractor).lower(),
                    }
                )

    write_csv_table(output_path, fieldnames, rows)


def write_retrieval_metrics_table(
    suite_results: list[RetrievalBaselineSuiteResult],
    output_path: str | Path,
    *,
    judge_client: object | None = None,
    rubric_version: str | None = None,
) -> None:
    """Write retrieval metrics table comparing both retrievers across all cases."""
    fieldnames = [
        "case_id",
        "retriever_name",
        "recall_at_1",
        "recall_at_3",
        "recall_at_5",
        "recall_at_10",
        "mrr",
        "ndcg_at_10",
        "precision_at_1",
        "precision_at_3",
        "precision_at_5",
        "context_noise_ratio",
        "answer_accuracy",
        "answer_f1",
    ]
    rows: list[dict[str, str]] = []
    for suite in suite_results:
        for result in suite.baseline_results:
            m = result.metrics
            rows.append(
                {
                    "case_id": m.case_id,
                    "retriever_name": m.retriever_name,
                    "recall_at_1": f"{m.recall_at_1:.4f}",
                    "recall_at_3": f"{m.recall_at_3:.4f}",
                    "recall_at_5": f"{m.recall_at_5:.4f}",
                    "recall_at_10": f"{m.recall_at_10:.4f}",
                    "mrr": f"{m.mrr:.4f}",
                    "ndcg_at_10": f"{m.ndcg_at_10:.4f}",
                    "precision_at_1": f"{m.precision_at_1:.4f}",
                    "precision_at_3": f"{m.precision_at_3:.4f}",
                    "precision_at_5": f"{m.precision_at_5:.4f}",
                    "context_noise_ratio": f"{m.context_noise_ratio:.4f}",
                    "answer_accuracy": f"{m.answer_accuracy:.4f}",
                    "answer_f1": f"{m.answer_f1:.4f}",
                }
            )

    _append_judge_provenance(
        fieldnames,
        rows,
        judge_client=judge_client,
        rubric_version=rubric_version,
    )
    write_csv_table(output_path, fieldnames, rows)
