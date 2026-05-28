#!/usr/bin/env python3
"""Experiment 2: CMD Attribution Effectiveness — full run on all 3 datasets."""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.harness import (
    diagnosis_predictions,
    run_case_full_v1,
    run_cases_v1,
    write_comparison_metrics_table,
    write_repair_success_table_from_full,
)
from cmd_audit.metrics import compute_diagnosis_metrics
from cmd_audit.core.models import load_probe_cases_v1
from cmd_audit.writers import (
    write_attribution_table,
    write_confusion_matrix_table,
    write_post_repair_table,
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "probe_cases"
OUT = ROOT / "artifacts"
SAND = OUT / "sandbox"
SAND.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

DATASETS = [
    ("real_memoryarena_cases.json", "MemoryArena"),
    ("real_longmemeval_cases.json", "LongMemEval"),
    ("real_toolbench_cases.json", "ToolBench"),
]


def main() -> None:
    all_cmd_results = []
    all_predictions = []

    for fname, label in DATASETS:
        cases = load_probe_cases_v1(str(DATA / fname))
        print(f"{'='*65}")
        print(f"  {label}: {len(cases)} cases")
        print(f"{'='*65}")

        # ── CMD attribution + comparators ──
        results = run_cases_v1(cases)
        all_cmd_results.extend(results)

        correct = sum(1 for r in results if r.attribution_correct)
        print(f"  CMD accuracy: {correct}/{len(results)} = {correct/len(results):.4f}")

        # ── Per-system metrics ──
        predictions = [p for r in results for p in diagnosis_predictions(r)]
        all_predictions.extend(predictions)
        systems = sorted(set(p.system_name for p in predictions), key=_sys_sort)
        print(f"  {'System':25s} {'Macro F1':>8s}  {'Accuracy':>7s}  {'Cost':>6s}")
        print(f"  {'-'*48}")
        all_metrics = compute_diagnosis_metrics(tuple(predictions))
        for sys in systems:
            m = all_metrics[sys]
            print(f"  {sys:25s} {m.macro_f1:>8.4f}  {m.attribution_accuracy:>7.4f}  {m.cost_per_diagnosis:>6.2f}")

        # ── Confusion matrix ──
        lbls = sorted(set(r.perturbation_label for r in results))
        conf = defaultdict(lambda: defaultdict(int))
        for r in results:
            conf[r.perturbation_label][r.attribution.predicted_label] += 1

        print(f"\n  CMD Confusion Matrix (row=actual, col=predicted):")
        header = f"  {'':28s}"
        for lb in lbls:
            header += f"{lb:>18s}"
        print(header)
        for actual in lbls:
            row = f"  {actual:28s}"
            for pred in lbls:
                row += f"{conf[actual][pred]:>18d}"
            print(row)

        # Per-label accuracy
        print(f"\n  Per-label:")
        for lb in lbls:
            n = sum(1 for r in results if r.perturbation_label == lb)
            c = sum(1 for r in results if r.perturbation_label == lb and r.attribution_correct)
            print(f"    {lb:28s} {c}/{n}  {c/n:.4f}")

        # ── Write artifacts ──
        suffix = label.lower()
        write_attribution_table(results, OUT / f"attribution_table_{suffix}.csv")
        try:
            write_confusion_matrix_table(results, OUT / f"attribution_confusion_matrix_{suffix}.csv")
        except KeyError:
            pass  # V1 labels not in V0 label order
        write_comparison_metrics_table(results, OUT / f"comparison_metrics_{suffix}.csv")

        # ── Post-Repair Context Replay ──
        full_results = [run_case_full_v1(c) for c in cases]
        rec = sum(1 for f in full_results if f.post_repair.repair_assessment == "recovered")
        part = sum(1 for f in full_results if f.post_repair.repair_assessment == "partial")
        fail = sum(1 for f in full_results if f.post_repair.repair_assessment == "failed")
        print(f"\n  Post-Repair: recovered={rec}, partial={part}, failed={fail}  "
              f"(recovered_rate={rec/len(full_results):.4f})")

        write_post_repair_table(full_results, SAND / f"post_repair_table_{suffix}.csv")
        try:
            write_repair_success_table_from_full(full_results, SAND / f"repair_success_table_{suffix}.csv")
        except Exception:
            pass  # V1 labels not supported by V0 repair comparison

    # ── Combined ──
    print(f"\n{'='*65}")
    print(f"  COMBINED: {len(all_cmd_results)} cases ({len(DATASETS)} datasets)")
    print(f"{'='*65}")
    total_correct = sum(1 for r in all_cmd_results if r.attribution_correct)
    print(f"  CMD accuracy: {total_correct}/{len(all_cmd_results)} = {total_correct/len(all_cmd_results):.4f}")

    systems = sorted(set(p.system_name for p in all_predictions), key=_sys_sort)
    all_metrics = compute_diagnosis_metrics(tuple(all_predictions))
    for sys in systems:
        m = all_metrics[sys]
        print(f"  {sys:25s} Macro F1={m.macro_f1:.4f}  Accuracy={m.attribution_accuracy:.4f}  "
              f"Top2={m.top2_accuracy:.4f}")

    # Write combined artifacts
    write_attribution_table(all_cmd_results, OUT / "attribution_table.csv")
    write_comparison_metrics_table(all_cmd_results, OUT / "comparison_metrics.csv")
    print(f"\n  Artifacts written to {OUT}/ and {SAND}/")
    print(f"  - attribution_table.csv")
    print(f"  - attribution_confusion_matrix.csv")
    print(f"  - comparison_metrics.csv")


def _sys_sort(name: str) -> tuple:
    order = {"CMD-Audit": 0, "evidence_recall": 1, "subagent_judge": 2, "random_label": 3}
    return (order.get(name, 9), name)


if __name__ == "__main__":
    main()
