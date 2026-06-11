#!/usr/bin/env python3
"""Experiment 12: distill MCTS action-credit traces into action priors."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.labels import PIPELINE_STEP_ACTIONS
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.data_io import load_probe_cases
from cmd_audit.eval.writers import write_csv_table
from cmd_audit.mcts.distill import (
    distill_action_priors,
    flatten_action_priors,
    oracle_action_priors,
    prior_alignment,
)
from experiments.experiment_runner_common import DATA, OUT, action_name, run_mcts_for_case
from experiments.experiment_runner_common import assert_g_eval_available, build_answer_verifier


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "real_three_source_cases.json"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--iterations", type=int, default=32)
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--max-retries", type=int, default=1)
    args = parser.parse_args()

    client = LLMClient(LLMClientConfig())
    assert_g_eval_available(client, role="mcts-distill")
    answer_verifier = build_answer_verifier(
        client,
        answer_mode="answer-rubric",
        max_workers=1,
        max_retries=args.max_retries,
    )

    cases = [
        case
        for case in load_probe_cases(args.cases)
        if case.perturbation_label in PIPELINE_STEP_ACTIONS
    ]
    if args.limit:
        cases = cases[: args.limit]
    split = max(1, len(cases) // 2)
    train_cases = cases[:split]
    test_cases = cases[split:] or cases[:split]
    print(f"Loaded {len(cases)} pipeline cases; train={len(train_cases)} test={len(test_cases)}")

    cold_results = _run_batch(
        train_cases,
        client,
        answer_verifier,
        iterations=args.iterations,
        max_depth=args.max_depth,
        action_priors=None,
    )
    prior_map = distill_action_priors(cold_results)
    flat_priors = flatten_action_priors(prior_map)
    guided_results = _run_batch(
        test_cases,
        client,
        answer_verifier,
        iterations=args.iterations,
        max_depth=args.max_depth,
        action_priors=flat_priors,
    )
    oracle_results = _run_batch(
        test_cases,
        client,
        answer_verifier,
        iterations=args.iterations,
        max_depth=args.max_depth,
        action_priors="oracle",
    )

    rows = [
        _summary_row("cold_start", cold_results, prior_alignment_value=None),
        _summary_row("prior_guided", guided_results, prior_alignment_value=prior_alignment(prior_map)),
        _summary_row("oracle_prior", oracle_results, prior_alignment_value=1.0),
    ]
    for row in rows:
        print(row)

    out_path = OUT / "experiment_mcts_distill.csv"
    write_csv_table(
        out_path,
        ["round", "avg_rollouts", "label_correctness", "prior_alignment"],
        rows,
    )
    print(f"Wrote {out_path}")


def _run_batch(
    cases,
    client,
    answer_verifier,
    *,
    iterations: int,
    max_depth: int,
    action_priors,
):
    results = []
    for case in cases:
        priors = action_priors
        if priors == "oracle":
            priors = oracle_action_priors(case.perturbation_label)
        result = run_mcts_for_case(
            case,
            client,
            answer_verifier,
            max_iterations=iterations,
            max_depth=max_depth,
            action_priors=priors,
        )
        results.append(SimpleNamespace(perturbation_label=case.perturbation_label, mcts_result=result))
    return results


def _summary_row(round_name: str, results: list, *, prior_alignment_value: float | None) -> dict[str, str]:
    if not results:
        return {
            "round": round_name,
            "avg_rollouts": "0.0000",
            "label_correctness": "0.0000",
            "prior_alignment": "N/A" if prior_alignment_value is None else f"{prior_alignment_value:.4f}",
        }
    rollouts = sum(row.mcts_result.terminal_rollouts for row in results)
    correct = sum(
        action_name(row.mcts_result.primary_attribution_label) == row.perturbation_label
        for row in results
    )
    return {
        "round": round_name,
        "avg_rollouts": f"{rollouts / len(results):.4f}",
        "label_correctness": f"{correct / len(results):.4f}",
        "prior_alignment": "N/A" if prior_alignment_value is None else f"{prior_alignment_value:.4f}",
    }


if __name__ == "__main__":
    main()
