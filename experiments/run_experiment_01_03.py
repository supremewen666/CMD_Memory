#!/usr/bin/env python3
"""Experiment 1-3: CMD + 2 baselines (evidence_recall, random_label) + Memory-Probe on 596 real-data cases.

Excludes subagent_judge (LLM-as-judge not integrated).
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.harness import (
    diagnosis_predictions,
    run_cases_v1,
)
from baselines.memory_probe import run_memory_probe_baselines
from cmd_audit.metrics import compute_diagnosis_metrics
from cmd_audit.core.models import load_probe_cases_v1
from cmd_audit.writers import write_csv_table

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "probe_cases"
SAND = ROOT / "artifacts" / "sandbox"
SAND.mkdir(parents=True, exist_ok=True)

DATASETS = [
    ("real_longmemeval_cases.json", "LongMemEval"),
    ("real_memoryarena_cases.json", "MemoryArena"),
    ("real_toolbench_cases.json", "ToolBench"),
]

# LLM-as-judge not integrated — only 2 non-LLM comparison baselines
INCLUDED_SYSTEMS = frozenset({"CMD-Audit", "evidence_recall", "random_label"})


def filter_predictions(predictions: list) -> list:
    return [p for p in predictions if p.system_name in INCLUDED_SYSTEMS]


def _sys_sort(name: str) -> tuple:
    order = {"CMD-Audit": 0, "evidence_recall": 1, "random_label": 2}
    return (order.get(name, 9), name)


def main() -> None:
    all_cases = []
    all_cmd_results = []
    all_predictions = []

    for fname, label in DATASETS:
        cases = load_probe_cases_v1(str(DATA / fname))
        print(f"{'='*65}")
        print(f"  {label}: {len(cases)} cases")
        print(f"{'='*65}")

        # ── CMD V1 pipeline ──
        results = run_cases_v1(cases)
        all_cases.extend(cases)
        all_cmd_results.extend(results)

        correct = sum(1 for r in results if r.attribution_correct)
        total_labeled = sum(1 for r in results if r.attribution_correct is not None)
        print(f"  CMD accuracy: {correct}/{total_labeled} = {correct/total_labeled:.4f}")

        # ── 2 baselines only ──
        predictions = filter_predictions(
            [p for r in results for p in diagnosis_predictions(r)]
        )
        all_predictions.extend(predictions)

        systems = sorted(set(p.system_name for p in predictions), key=_sys_sort)
        all_metrics = compute_diagnosis_metrics(tuple(predictions))
        print(f"  {'System':25s} {'Macro F1':>8s}  {'Accuracy':>7s}  {'Cost':>6s}")
        print(f"  {'-'*48}")
        for sys in systems:
            m = all_metrics[sys]
            print(f"  {sys:25s} {m.macro_f1:>8.4f}  {m.attribution_accuracy:>7.4f}  {m.cost_per_diagnosis:>6.2f}")

        # Confusion matrix
        lbls = sorted(set(r.perturbation_label for r in results if r.perturbation_label is not None))
        conf = defaultdict(lambda: defaultdict(int))
        for r in results:
            if r.perturbation_label is not None:
                conf[r.perturbation_label][r.attribution.predicted_label] += 1

        print(f"\n  CMD Confusion Matrix (row=actual, col=predicted):")
        header = f"  {'':28s}"
        for lb in lbls:
            header += f"{lb:>18s}"
        print(header)
        for actual in lbls:
            row_str = f"  {actual:28s}"
            for pred in lbls:
                row_str += f"{conf[actual][pred]:>18d}"
            print(row_str)

        # Per-label accuracy
        print(f"\n  Per-label:")
        for lb in lbls:
            n = sum(1 for r in results if r.perturbation_label == lb)
            c = sum(1 for r in results if r.perturbation_label == lb and r.attribution_correct)
            print(f"    {lb:28s} {c}/{n}  {c/n:.4f}")

    # ── Memory-Probe on all 596 cases ──
    print(f"\n{'='*65}")
    print(f"  Memory-Probe (3x2 grid) on all {len(all_cases)} cases")
    print(f"{'='*65}")
    mp_result = run_memory_probe_baselines(all_cases)
    print(f"  Best cell: {mp_result.best_write_strategy} x {mp_result.best_retrieval_method}")
    print(f"  Best accuracy: {mp_result.best_cell_accuracy:.4f}")

    # ── Combined ──
    print(f"\n{'='*65}")
    print(f"  COMBINED: {len(all_cmd_results)} cases ({len(DATASETS)} datasets)")
    print(f"{'='*65}")
    total_correct = sum(1 for r in all_cmd_results if r.attribution_correct)
    total_labeled = sum(1 for r in all_cmd_results if r.attribution_correct is not None)
    null_count = sum(1 for r in all_cmd_results if r.perturbation_label is None)
    print(f"  Labeled cases: {total_labeled}, Null-label: {null_count}")
    print(f"  CMD accuracy: {total_correct}/{total_labeled} = {total_correct/total_labeled:.4f}")

    systems = sorted(set(p.system_name for p in all_predictions), key=_sys_sort)
    all_metrics = compute_diagnosis_metrics(tuple(all_predictions))
    for sys in systems:
        m = all_metrics[sys]
        print(f"  {sys:25s} Macro F1={m.macro_f1:.4f}  Accuracy={m.attribution_accuracy:.4f}  "
              f"Top2={m.top2_accuracy:.4f}  Cost={m.cost_per_diagnosis:.3f}")

    print(f"  Memory-Probe best accuracy: {mp_result.best_cell_accuracy:.4f}")

    # ── Write comparison metrics with 2 baselines + memory-probe ──
    output_path = SAND / "experiment1-3_comparison_metrics.csv"
    _write_comparison_metrics_2baselines(
        all_predictions, output_path,
        memory_probe_best_accuracy=mp_result.best_cell_accuracy,
    )
    print(f"\n  Comparison metrics written to {output_path}")


def _write_comparison_metrics_2baselines(
    predictions,
    output_path: Path,
    *,
    memory_probe_best_accuracy: float | None = None,
) -> None:
    metrics = compute_diagnosis_metrics(tuple(predictions))

    fieldnames = [
        "system_name",
        "attribution_accuracy",
        "macro_f1",
        "top2_accuracy",
        "cost_per_diagnosis",
    ]
    if memory_probe_best_accuracy is not None:
        fieldnames.append("memory_probe_best_accuracy")

    rows = [
        {
            "system_name": row.system_name,
            "attribution_accuracy": f"{row.attribution_accuracy:.4f}",
            "macro_f1": f"{row.macro_f1:.4f}",
            "top2_accuracy": f"{row.top2_accuracy:.4f}",
            "cost_per_diagnosis": f"{row.cost_per_diagnosis:.3f}",
            **(
                {"memory_probe_best_accuracy": f"{memory_probe_best_accuracy:.4f}"}
                if memory_probe_best_accuracy is not None
                else {}
            ),
        }
        for system_name in sorted(metrics, key=_sys_sort)
        for row in [metrics[system_name]]
    ]
    write_csv_table(output_path, fieldnames, rows)


if __name__ == "__main__":
    main()
