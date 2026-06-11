#!/usr/bin/env python3
"""Experiment 2 (UMem 5.2-analogue): MCTS vs exhaustive cost-quality.

Sweeps the MCTS rollout budget over {4, 8, 16, 32, exhaustive} and measures
``primary_label_correctness`` on the pipeline-action subset. The exhaustive
horizontal line is MCTS with the budget set to the full branching factor
b^d so every action sequence is reachable; the early-convergence curve is the
lower budgets. Reports rollouts-to-90%-of-oracle.

Requires a logprob-capable LLM endpoint: set LLM_BASE_URL and LLM_MODEL.
Without it the MCTS value function cannot score, so the run aborts rather than
emit phrase-match noise.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.labels import PIPELINE_STEP_ACTIONS
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.core.models import ProbeCase
from cmd_audit.data_io import load_probe_cases
from cmd_audit.eval.writers import write_csv_table
from cmd_audit.mcts.search import run_mcts_attribution
from cmd_audit.harness import _initial_mcts_context, _retrieved_memory_items
from experiments.experiment_runner_common import assert_g_eval_available, build_answer_verifier

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "probe_cases"
OUT = ROOT / "artifacts" / "sandbox"

# Branching = 5 step actions + identity = 6; multihop depth d=2 -> 36 reachable.
EXHAUSTIVE_BUDGET = 200
DEFAULT_BUDGETS = (4, 8, 16, 32, EXHAUSTIVE_BUDGET)


@dataclass
class BudgetPoint:
    budget: int
    n_cases: int
    primary_correct: int
    total_rollouts: int

    @property
    def correctness(self) -> float:
        return self.primary_correct / self.n_cases if self.n_cases else 0.0

    @property
    def avg_rollouts(self) -> float:
        return self.total_rollouts / self.n_cases if self.n_cases else 0.0


def _run_one_budget(
    cases: list[ProbeCase],
    budget: int,
    client,
    answer_verifier,
) -> BudgetPoint:
    correct = 0
    rollouts = 0
    for case in cases:
        recall_set = _retrieved_memory_items(case)
        result = run_mcts_attribution(
            client,
            _initial_mcts_context(case, recall_set),
            recall_set,
            case.gold_evidence,
            case.gold_answer,
            max_iterations=budget,
            answer_verifier=answer_verifier,
        )
        rollouts += result.terminal_rollouts
        if result.primary_attribution_label is not None:
            name = getattr(
                result.primary_attribution_label,
                "value",
                str(result.primary_attribution_label),
            )
            if name == case.perturbation_label:
                correct += 1
    return BudgetPoint(budget, len(cases), correct, rollouts)

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "real_multihop_cases.json"))
    parser.add_argument("--limit", type=int, default=0, help="0 = all cases")
    parser.add_argument(
        "--budgets",
        default=",".join(str(b) for b in DEFAULT_BUDGETS),
        help="comma-separated rollout budgets; last is treated as exhaustive",
    )
    parser.add_argument("--max-retries", type=int, default=1)
    args = parser.parse_args()

    budgets = tuple(int(b) for b in args.budgets.split(","))

    agent_client = LLMClient(LLMClientConfig())
    assert_g_eval_available(agent_client, role="mcts-value")
    answer_verifier = build_answer_verifier(
        agent_client,
        answer_mode="answer-rubric",
        max_workers=1,
        max_retries=args.max_retries,
    )

    cases = [
        c
        for c in load_probe_cases(args.cases)
        if c.perturbation_label in PIPELINE_STEP_ACTIONS
    ]
    if args.limit:
        cases = cases[: args.limit]
    print(f"Loaded {len(cases)} pipeline-action cases from {args.cases}")

    points: list[BudgetPoint] = []
    for budget in budgets:
        tag = "exhaustive" if budget == budgets[-1] else str(budget)
        pt = _run_one_budget(cases, budget, agent_client, answer_verifier)
        points.append(pt)
        print(
            f"  budget={tag:>10s}  correctness={pt.correctness:.4f}  "
            f"avg_rollouts={pt.avg_rollouts:.1f}"
        )

    oracle = points[-1].correctness
    target = 0.9 * oracle
    rollouts_to_90 = next(
        (p.avg_rollouts for p in points if p.correctness >= target), None
    )
    print(
        f"\nOracle (exhaustive) correctness = {oracle:.4f}; "
        f"90%-oracle target = {target:.4f}; "
        f"reached at avg_rollouts = "
        f"{rollouts_to_90 if rollouts_to_90 is not None else 'never'}"
    )

    OUT.mkdir(parents=True, exist_ok=True)
    out_path = OUT / "experiment_mcts_cost_quality.csv"
    write_csv_table(
        out_path,
        ["budget", "is_exhaustive", "n_cases", "primary_label_correctness", "avg_rollouts"],
        [
            {
                "budget": p.budget,
                "is_exhaustive": p is points[-1],
                "n_cases": p.n_cases,
                "primary_label_correctness": f"{p.correctness:.4f}",
                "avg_rollouts": f"{p.avg_rollouts:.2f}",
            }
            for p in points
        ],
    )
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
