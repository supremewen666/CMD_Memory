#!/usr/bin/env python3
"""Experiment 14: repair-efficacy four-arm comparison (Phase 1, E2/E3).

Runs four arms over the pipeline-step cases, each selecting a repair action by a
different policy but sharing one **gold-free** executor, and reports recovered
rate per arm:

  no_repair   floor — base context, no action.
  random      noise floor — case_id-seeded action pick.
  llm_judge   E3 competitor — LLM names the fault -> its action.
  cmd         this method — MCTS recovery-gain pick.

E2 = cmd vs no_repair (does the loop recover at all).
E3 = cmd vs random / llm_judge (does CMD select the recovering action).

Because construction is gold-free (apply_pipeline_action over recall only), a
recovered answer cannot come from copying gold into the context.

Run after vLLM is up:
    python -m experiments.run_experiment_14_repair_efficacy --limit 0
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.baselines.comparators import run_llm_judge_baseline
from cmd_audit.core.labels import PIPELINE_STEP_ACTIONS
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.data_io import load_probe_cases
from cmd_audit.eval.writers import write_csv_table
from cmd_audit.repair.efficacy import REPAIR_ARMS, run_repair_arm
from experiments.experiment_runner_common import (
    DATA,
    OUT,
    assert_g_eval_available,
    build_answer_verifier,
    run_mcts_for_case,
)

ROOT = Path(__file__).resolve().parent.parent


def _cmd_label(case, client, verifier, max_iterations, max_depth):
    result = run_mcts_for_case(
        case, client, verifier,
        max_iterations=max_iterations, max_depth=max_depth,
    )
    action = result.primary_attribution_label
    return action.value if action is not None else None


def _llm_label_selector(client):
    def select(case):
        comparator = run_llm_judge_baseline(case, llm_client=client)
        return comparator.predicted_label
    return select


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "real_multihop_cases.json"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-iterations", type=int, default=12)
    parser.add_argument("--recovered-threshold", type=float, default=0.1)
    args = parser.parse_args()

    client = LLMClient(LLMClientConfig())
    assert_g_eval_available(client, role="repair-efficacy")
    verifier = build_answer_verifier(client, answer_mode="answer-rubric")

    cases = [
        c
        for c in load_probe_cases(args.cases)
        if c.perturbation_label in PIPELINE_STEP_ACTIONS
    ]
    if args.limit:
        cases = cases[: args.limit]

    from cmd_audit.harness import _initial_mcts_context, _retrieved_memory_items

    llm_selector = _llm_label_selector(client)

    # arm -> gold_label -> [recovered_count, total]
    tally = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    detail_rows = []

    print(f"Repair efficacy: 4 arms over {len(cases)} cases\n")
    for i, case in enumerate(cases):
        recall_set = _retrieved_memory_items(case)
        max_depth = max(1, min(3, len(recall_set) or 1))
        base_ctx = _initial_mcts_context(case, recall_set)
        cfg = {"candidate_items": case.extracted_memory, "raw_events": case.raw_events}
        gold = case.perturbation_label

        # CMD label is computed once and reused by the cmd arm.
        cmd_label = _cmd_label(case, client, verifier, args.max_iterations, max_depth)

        for arm in REPAIR_ARMS:
            res = run_repair_arm(
                case, arm,
                client=client, answer_verifier=verifier,
                base_context=base_ctx, recall_set=recall_set, max_depth=max_depth,
                intervention_config=cfg,
                cmd_label=cmd_label,
                llm_label_selector=llm_selector,
                recovered_threshold=args.recovered_threshold,
            )
            tally[arm][gold][0] += int(res.recovered)
            tally[arm][gold][1] += 1
            detail_rows.append({
                "case_id": case.case_id,
                "gold_label": gold,
                "arm": arm,
                "selected_action": res.selected_action or "",
                "generation_point": "" if res.generation_point is None else str(res.generation_point),
                "recovery_gain": f"{res.recovery_gain:.4f}",
                "recovered": str(res.recovered).lower(),
            })
        print(f"  [{i+1}/{len(cases)}] {gold:20s} cmd_label={cmd_label}")

    _print_summary(tally, len(cases))

    detail_path = write_csv_table(
        OUT / "repair_efficacy_detail.csv",
        ["case_id", "gold_label", "arm", "selected_action",
         "generation_point", "recovery_gain", "recovered"],
        detail_rows,
        sandbox_root=OUT,
    )
    print(f"\nWrote {detail_path}")

    summary_rows = []
    for arm in REPAIR_ARMS:
        rec = sum(v[0] for v in tally[arm].values())
        tot = sum(v[1] for v in tally[arm].values())
        summary_rows.append({
            "arm": arm,
            "recovered": str(rec),
            "total": str(tot),
            "recovered_rate": f"{rec / tot:.4f}" if tot else "0.0000",
        })
    summary_path = write_csv_table(
        OUT / "repair_efficacy_summary.csv",
        ["arm", "recovered", "total", "recovered_rate"],
        summary_rows,
        sandbox_root=OUT,
    )
    print(f"Wrote {summary_path}")


def _print_summary(tally, n_cases) -> None:
    print("\n=== Recovered rate per arm (overall) ===")
    print(f"{'arm':12s} {'recovered':>9s} {'total':>6s} {'rate':>8s}")
    for arm in REPAIR_ARMS:
        rec = sum(v[0] for v in tally[arm].values())
        tot = sum(v[1] for v in tally[arm].values())
        rate = rec / tot if tot else 0.0
        print(f"{arm:12s} {rec:>9d} {tot:>6d} {rate:>8.4f}")

    print("\n=== Recovered rate per arm x gold label ===")
    labels = sorted(PIPELINE_STEP_ACTIONS)
    header = f"{'arm':12s} " + " ".join(f"{l[:10]:>11s}" for l in labels)
    print(header)
    for arm in REPAIR_ARMS:
        cells = []
        for label in labels:
            rec, tot = tally[arm][label]
            cells.append(f"{(rec/tot if tot else 0.0):>11.3f}")
        print(f"{arm:12s} " + " ".join(cells))


if __name__ == "__main__":
    main()
