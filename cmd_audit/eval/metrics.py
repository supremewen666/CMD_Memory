"""Comparison metrics for CMD-Audit and non-CMD baselines."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from cmd_audit.core.labels import validate_label


@dataclass(frozen=True)
class DiagnosisPrediction:
    system_name: str
    case_id: str
    gold_label: str | None
    predicted_label: str | None
    top2_labels: tuple[str, ...]
    cost_per_diagnosis: float

    def __post_init__(self) -> None:
        if self.gold_label is not None:
            validate_label(self.gold_label)
        if self.predicted_label is not None:
            validate_label(self.predicted_label)
        for label in self.top2_labels:
            validate_label(label)


@dataclass(frozen=True)
class DiagnosisMetrics:
    system_name: str
    attribution_accuracy: float
    macro_f1: float
    top2_accuracy: float
    cost_per_diagnosis: float


def compute_diagnosis_metrics(
    predictions: Iterable[DiagnosisPrediction],
    *,
    labels: tuple[str, ...] | None = None,
) -> dict[str, DiagnosisMetrics]:
    by_system: dict[str, list[DiagnosisPrediction]] = defaultdict(list)
    for prediction in predictions:
        by_system[prediction.system_name].append(prediction)

    metrics: dict[str, DiagnosisMetrics] = {}
    for system_name, rows in by_system.items():
        label_set = labels or _observed_labels(rows)
        # Null gold_label cases: excluded from accuracy/F1 (no ground truth),
        # included in cost (pipeline still ran).
        labeled_rows = [r for r in rows if r.gold_label is not None]
        total = len(rows)
        total_labeled = len(labeled_rows)
        correct = sum(
            r.predicted_label == r.gold_label for r in labeled_rows
        )
        top2_correct = sum(_top2_correct(r) for r in labeled_rows)
        total_cost = sum(r.cost_per_diagnosis for r in rows)
        metrics[system_name] = DiagnosisMetrics(
            system_name=system_name,
            attribution_accuracy=correct / total_labeled if total_labeled else 0.0,
            macro_f1=_macro_f1(labeled_rows, label_set),
            top2_accuracy=top2_correct / total_labeled if total_labeled else 0.0,
            cost_per_diagnosis=total_cost / total if total else 0.0,
        )
    return metrics


def _observed_labels(rows: list[DiagnosisPrediction]) -> tuple[str, ...]:
    labels = sorted(
        {
            label
            for row in rows
            for label in (row.gold_label, row.predicted_label)
            if label is not None
        }
    )
    return tuple(labels)


def _top2_correct(row: DiagnosisPrediction) -> bool:
    top2 = row.top2_labels or (row.predicted_label,)
    return row.gold_label in top2


def _macro_f1(rows: list[DiagnosisPrediction], labels: tuple[str, ...]) -> float:
    if not labels:
        return 0.0
    return sum(_label_f1(rows, label) for label in labels) / len(labels)


def _label_f1(rows: list[DiagnosisPrediction], label: str) -> float:
    true_positive = sum(
        row.gold_label == label and row.predicted_label == label for row in rows
    )
    false_positive = sum(
        row.gold_label != label and row.predicted_label == label for row in rows
    )
    false_negative = sum(
        row.gold_label == label and row.predicted_label != label for row in rows
    )
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    precision = true_positive / precision_denominator if precision_denominator else 0.0
    recall = true_positive / recall_denominator if recall_denominator else 0.0
    if precision + recall == 0.0:
        return 0.0
    return 2 * precision * recall / (precision + recall)
