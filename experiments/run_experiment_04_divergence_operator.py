#!/usr/bin/env python3
"""Experiment 4: directed divergence vs symmetric distance for LOO item typing."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.data_io import load_probe_cases
from cmd_audit.eval.writers import write_csv_table
from cmd_audit.item_gate import compute_directed_divergence, compute_symmetric_divergence
from cmd_audit.item_gate.loo import compute_loo_divergence
from experiments.experiment_runner_common import DATA, OUT, stable_coin, target_item_for_case
from experiments.experiment_runner_common import assert_g_eval_available


TARGET_LABELS = ("item_wrong", "item_compression_distorted")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "real_three_source_cases.json"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()

    client = LLMClient(LLMClientConfig())
    assert_g_eval_available(client, role="divergence-operator")
    cases = [
        case
        for case in load_probe_cases(args.cases)
        if case.perturbation_label in TARGET_LABELS
    ]
    if args.limit:
        cases = cases[: args.limit]
    print(f"Loaded {len(cases)} wrong/compression cases")

    rows = []
    for operator, divergence_fn in (
        ("directed", compute_directed_divergence),
        ("symmetric", compute_symmetric_divergence),
    ):
        correct = {label: 0 for label in TARGET_LABELS}
        total = {label: 0 for label in TARGET_LABELS}
        for case in cases:
            total[case.perturbation_label] += 1
            target = target_item_for_case(case)
            if target is None:
                continue
            result = compute_loo_divergence(
                client,
                target,
                case.extracted_memory,
                case.query,
                divergence_threshold=args.threshold,
                divergence_fn=divergence_fn,
            )
            predicted = result.item_label
            if (
                operator == "symmetric"
                and predicted is None
                and result.divergence is not None
                and result.divergence.max_divergence > args.threshold
            ):
                predicted = stable_coin(case.case_id, TARGET_LABELS)
            if predicted == case.perturbation_label:
                correct[case.perturbation_label] += 1

        total_correct = sum(correct.values())
        total_cases = sum(total.values())
        row = {
            "operator": operator,
            "wrong_recall": _rate(correct["item_wrong"], total["item_wrong"]),
            "compression_recall": _rate(
                correct["item_compression_distorted"],
                total["item_compression_distorted"],
            ),
            "accuracy": _rate(total_correct, total_cases),
        }
        rows.append(row)
        print(row)

    out_path = OUT / "experiment_divergence_operator.csv"
    write_csv_table(
        out_path,
        ["operator", "wrong_recall", "compression_recall", "accuracy"],
        rows,
    )
    print(f"Wrote {out_path}")


def _rate(numerator: int, denominator: int) -> str:
    return f"{(numerator / denominator if denominator else 0.0):.4f}"


if __name__ == "__main__":
    main()
