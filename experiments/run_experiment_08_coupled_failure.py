#!/usr/bin/env python3
"""Experiment 8: coupled failure boundary, single-point vs joint recovery."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.eval.writers import write_csv_table
from cmd_audit.mcts import PipelineAction
from experiments.experiment_runner_common import (
    DATA,
    OUT,
    load_cases_with_raw,
    max_tree_q,
    run_mcts_for_case,
)
from experiments.experiment_runner_common import assert_g_eval_available, build_answer_verifier


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        default=str(DATA / "real_coupled_failure_boundary_cases.json"),
        help="Full coupled case file. The inspected subset is metadata-only.",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--iterations", type=int, default=32)
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--threshold", type=float, default=0.8)
    parser.add_argument("--max-retries", type=int, default=1)
    args = parser.parse_args()

    client = LLMClient(LLMClientConfig())
    assert_g_eval_available(client, role="coupled-failure")
    answer_verifier = build_answer_verifier(
        client,
        answer_mode="answer-rubric",
        max_workers=1,
        max_retries=args.max_retries,
    )

    entries = load_cases_with_raw(args.cases)
    if args.limit:
        entries = entries[: args.limit]

    rows = []
    for entry in entries:
        labels = tuple(entry.raw.get("coupled_labels") or entry.raw.get("coupled_failure", {}).get("labels") or ())
        if len(labels) < 2:
            continue
        hop1 = run_mcts_for_case(
            entry.case,
            client,
            answer_verifier,
            max_iterations=args.iterations,
            max_depth=args.max_depth,
            restrict_to_hop=1,
        )
        hop2 = run_mcts_for_case(
            entry.case,
            client,
            answer_verifier,
            max_iterations=args.iterations,
            max_depth=args.max_depth,
            restrict_to_hop=2,
        )
        joint = run_mcts_for_case(
            entry.case,
            client,
            answer_verifier,
            max_iterations=args.iterations,
            max_depth=args.max_depth,
        )
        credit_hop1 = _credit_for(hop1, 1, labels[0])
        credit_hop2 = _credit_for(hop2, 2, labels[1])
        credit_joint = max_tree_q(joint)
        is_coupled = max(credit_hop1, credit_hop2) < args.threshold and credit_joint >= args.threshold
        row = {
            "case_id": entry.case.case_id,
            "credit_hop1": f"{credit_hop1:.4f}",
            "credit_hop2": f"{credit_hop2:.4f}",
            "credit_joint": f"{credit_joint:.4f}",
            "is_coupled": str(is_coupled),
        }
        rows.append(row)
        print(row)

    out_path = OUT / "experiment_coupled_failure.csv"
    write_csv_table(
        out_path,
        ["case_id", "credit_hop1", "credit_hop2", "credit_joint", "is_coupled"],
        rows,
    )
    print(f"Wrote {out_path}")


def _credit_for(result, hop_index: int, label: str) -> float:
    try:
        action = PipelineAction(label)
    except ValueError:
        return 0.0
    return float(result.action_credits.get(hop_index - 1, {}).get(action, 0.0))


if __name__ == "__main__":
    main()
