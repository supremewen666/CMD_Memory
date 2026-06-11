#!/usr/bin/env python3
"""Experiment 3: nested ceiling value vs naive weighted value."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.data_io import load_probe_cases
from cmd_audit.eval.writers import write_csv_table
from experiments.experiment_runner_common import DATA, OUT, action_name, run_mcts_for_case
from experiments.experiment_runner_common import assert_g_eval_available, build_answer_verifier


TARGET_LABELS = ("injection_error", "granularity_error")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "real_three_source_cases.json"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--iterations", type=int, default=16)
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--max-retries", type=int, default=1)
    args = parser.parse_args()

    client = LLMClient(LLMClientConfig())
    assert_g_eval_available(client, role="value-ablation")
    answer_verifier = build_answer_verifier(
        client,
        answer_mode="answer-rubric",
        max_workers=1,
        max_retries=args.max_retries,
    )

    cases = [
        case
        for case in load_probe_cases(args.cases)
        if case.perturbation_label in TARGET_LABELS
    ]
    if args.limit:
        cases = cases[: args.limit]
    print(f"Loaded {len(cases)} injection/granularity cases")

    rows = []
    for value_type in ("nested", "naive"):
        correct_by_label = {label: 0 for label in TARGET_LABELS}
        total_by_label = {label: 0 for label in TARGET_LABELS}
        for case in cases:
            total_by_label[case.perturbation_label] += 1
            result = run_mcts_for_case(
                case,
                client,
                answer_verifier,
                max_iterations=args.iterations,
                max_depth=args.max_depth,
                value_function_type=value_type,
            )
            predicted = action_name(result.primary_attribution_label)
            if predicted == case.perturbation_label:
                correct_by_label[case.perturbation_label] += 1

        total_correct = sum(correct_by_label.values())
        total_cases = sum(total_by_label.values())
        row = {
            "value_function": value_type,
            "injection_recall": _rate(correct_by_label["injection_error"], total_by_label["injection_error"]),
            "granularity_recall": _rate(correct_by_label["granularity_error"], total_by_label["granularity_error"]),
            "total_recall": _rate(total_correct, total_cases),
        }
        rows.append(row)
        print(row)

    out_path = OUT / "experiment_value_ablation.csv"
    write_csv_table(
        out_path,
        ["value_function", "injection_recall", "granularity_recall", "total_recall"],
        rows,
    )
    print(f"Wrote {out_path}")


def _rate(numerator: int, denominator: int) -> str:
    return f"{(numerator / denominator if denominator else 0.0):.4f}"


if __name__ == "__main__":
    main()
