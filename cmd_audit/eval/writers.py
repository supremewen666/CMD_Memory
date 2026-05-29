"""Shared artifact writers for CMD-Audit.

Consolidates the CSV table-writing pattern duplicated across harness, repairs,
failure_memory, and version_gates modules. Also provides shared text-file
writers for summary and ledger artifacts.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from cmd_audit.core.labels import PIPELINE_LABEL_ORDER
from cmd_audit.core.models import MemoryItem
from .provenance import compute_provenance_completeness

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


# ── Shared primitives ────────────────────────────────────────────────────


def write_csv_table(
    path: str | Path,
    fieldnames: list[str],
    rows: Iterable[dict[str, str]],
    *,
    sandbox_root: str | Path | None = None,
) -> Path:
    """Write a CSV table, optionally enforcing the sandbox write boundary."""
    output = Path(path)
    if sandbox_root is not None:
        from cmd_audit.repair.post_repair import validate_sandbox_path
        validate_sandbox_path(output, sandbox_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
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


# ── Attribution table ────────────────────────────────────────────────────


def write_attribution_table(
    results: list[AuditResult],
    output_path: str | Path,
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
        ]
    )

    rows: list[dict[str, str]] = []
    for result in results:
        attribution = result.attribution
        replay = result.replay if attribution is not None else None
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
            "recovery_gain": f"{attribution.recovery_gain:.3f}" if attribution else "",
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
            row[f"{replay_name}_recovery_gain"] = f"{replay.recovery_gain:.3f}"
        rows.append(row)

    write_csv_table(output_path, fieldnames, rows)


# ── Confusion matrix ─────────────────────────────────────────────────────


def write_confusion_matrix_table(
    results: list[AuditResult],
    output_path: str | Path,
) -> None:
    """Write the CMD-Audit attribution confusion matrix CSV."""
    counts = {
        gold_label: {predicted_label: 0 for predicted_label in PIPELINE_LABEL_ORDER}
        for gold_label in PIPELINE_LABEL_ORDER
    }
    for result in results:
        if result.attribution is None or result.perturbation_label is None:
            continue
        counts[result.perturbation_label][result.attribution.predicted_label] += 1

    fieldnames = ["gold_label", *PIPELINE_LABEL_ORDER]
    rows: list[dict[str, str]] = []
    for gold_label in PIPELINE_LABEL_ORDER:
        row: dict[str, str] = {"gold_label": gold_label}
        row.update({k: str(v) for k, v in counts[gold_label].items()})
        rows.append(row)

    write_csv_table(output_path, fieldnames, rows)


def write_provenance_completeness_summary(
    results: list[AuditResult],
    output_path: str | Path,
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

    write_csv_table(output_path, fieldnames, rows)


# ── Post-repair table ────────────────────────────────────────────────────


def write_post_repair_table(
    results: list[AuditResult],
    output_path: str | Path,
    *,
    sandbox_root: str | Path | None = None,
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

    write_csv_table(output_path, fieldnames, rows)
