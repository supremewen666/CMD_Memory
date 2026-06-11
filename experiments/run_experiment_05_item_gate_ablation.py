#!/usr/bin/env python3
"""Experiment 5: item gate component ablation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.data_io import load_probe_cases
from cmd_audit.eval.writers import write_csv_table
from cmd_audit.item_gate import ItemGateStatus, run_item_gate
from experiments.experiment_runner_common import (
    DATA,
    OUT,
    action_name,
    run_mcts_for_case,
    target_item_for_case,
)
from experiments.experiment_runner_common import assert_g_eval_available, build_answer_verifier


TARGET_LABELS = (
    "item_stale",
    "item_conflict",
    "item_wrong",
    "item_compression_distorted",
)

CONFIGS = (
    ("full", True, True),
    ("no_collision", False, True),
    ("no_loo", True, False),
    ("gate_off", False, False),
)

STATUS_TO_LABEL = {
    ItemGateStatus.ITEM_STALE: "item_stale",
    ItemGateStatus.ITEM_CONFLICT: "item_conflict",
    ItemGateStatus.ITEM_WRONG: "item_wrong",
    ItemGateStatus.ITEM_COMPRESSION_DISTORTED: "item_compression_distorted",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "real_three_source_cases.json"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--mcts-iterations", type=int, default=10)
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--max-retries", type=int, default=1)
    args = parser.parse_args()

    client = LLMClient(LLMClientConfig())
    assert_g_eval_available(client, role="item-gate-ablation")
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
    print(f"Loaded {len(cases)} item-label cases")

    rows = []
    for name, enable_collision, enable_loo in CONFIGS:
        correct = {label: 0 for label in TARGET_LABELS}
        total = {label: 0 for label in TARGET_LABELS}
        contaminated = 0
        wasted_rollouts = 0

        for case in cases:
            gold = case.perturbation_label
            total[gold] += 1
            predicted_item = None
            target = target_item_for_case(case)
            if target is not None and (enable_collision or enable_loo):
                gate = run_item_gate(
                    client,
                    target,
                    case.extracted_memory,
                    case.query,
                    enable_collision=enable_collision,
                    enable_loo=enable_loo,
                )
                predicted_item = STATUS_TO_LABEL.get(gate.status)

            if predicted_item is not None:
                if predicted_item == gold:
                    correct[gold] += 1
                continue

            mcts_result = run_mcts_for_case(
                case,
                client,
                answer_verifier,
                max_iterations=args.mcts_iterations,
                max_depth=args.max_depth,
            )
            wasted_rollouts += mcts_result.terminal_rollouts
            if action_name(mcts_result.primary_attribution_label):
                contaminated += 1

        n_cases = sum(total.values())
        row = {
            "config": name,
            "stale_recall": _rate(correct["item_stale"], total["item_stale"]),
            "conflict_recall": _rate(correct["item_conflict"], total["item_conflict"]),
            "wrong_recall": _rate(correct["item_wrong"], total["item_wrong"]),
            "compression_recall": _rate(
                correct["item_compression_distorted"],
                total["item_compression_distorted"],
            ),
            "tier3_contamination": _rate(contaminated, n_cases),
            "wasted_rollouts": str(wasted_rollouts),
        }
        rows.append(row)
        print(row)

    out_path = OUT / "experiment_item_gate_ablation.csv"
    write_csv_table(
        out_path,
        [
            "config",
            "stale_recall",
            "conflict_recall",
            "wrong_recall",
            "compression_recall",
            "tier3_contamination",
            "wasted_rollouts",
        ],
        rows,
    )
    print(f"Wrote {out_path}")


def _rate(numerator: int, denominator: int) -> str:
    return f"{(numerator / denominator if denominator else 0.0):.4f}"


if __name__ == "__main__":
    main()
