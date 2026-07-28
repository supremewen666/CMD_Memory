#!/usr/bin/env python3
"""Experiment 16: coupled-failure exhaustive (the MCTS existence decider).

Exp8 judged coupling with two different rulers: the single-point legs used the
MCTS ``action_credits`` (the SAME defective UCB credit A2 showed collapses to
gp=0), while the joint leg used ``max_tree_q``. So Exp8's "single < 0.8, joint
>= 0.8" cannot tell real coupling from a search-coverage artifact.

This probe removes that confound. For each coupled case it measures ABSOLUTE
terminal recovery under FULL coverage on both legs (no UCB, no budget):

  floor        = recovery(identity everywhere)
  best_single  = max recovery over every single (gen_point, action),
                 identity at the other hop
  best_combo   = max recovery over every (hop0 action) x (hop1 action) pair

Same rollout, same gold, same scale on both legs. The verdict is then clean:

  is_true_coupled = best_single < threshold AND best_combo >= threshold

  TRUE  -> no single point recovers even with full coverage, but a coalition
           does. Linear single-point Delta-k genuinely fails here; this is the
           b^d residual that is MCTS's only reason to exist.
  FALSE (best_single >= threshold) -> a single point DID recover under full
           coverage. Exp8's "coupling" on this case was the A2 coverage defect,
           not real coupling. MCTS earns nothing here; cheap exhaustive /
           directed-seed single-point is the whole deliverable.

Run after vLLM is up (heavier than Exp8 -- ~b^2 rollouts/case; start small):
    python -m experiments.run_experiment_16_coupled_exhaustive --limit 3
    python -m experiments.run_experiment_16_coupled_exhaustive
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.eval.writers import write_csv_table
from cmd_audit.counterfactual.actions import PipelineAction, get_legal_actions
from experiments.experiment_runner_common import (
    OUT,
    assert_g_eval_available,
    build_answer_verifier,
    load_cases_with_raw,
)
from experiments.experiment_runner_common import build_clients
from experiments.probe_exhaustive import DATA, _own_recovery, _step_context

ROOT = Path(__file__).resolve().parent.parent


def _non_identity(actions):
    return [a for a in actions if a != PipelineAction.IDENTITY]


def _evaluate_coupled_case(case, client, verifier):
    """Return (floor, best_single, best_single_choice, best_combo, best_combo_choice).

    Recovery is ABSOLUTE terminal score (rollout recovery_gain with baseline
    subtraction disabled), measured identically for floor / single / combo so
    the threshold comparison is apples-to-apples. Identity backbone construction
    mirrors probe_exhaustive single-point logic, extended to 2-point coalitions.
    """
    from cmd_audit.harness import _initial_mcts_context, _retrieved_memory_items

    recall_set = _retrieved_memory_items(case)
    max_depth = max(1, min(3, len(recall_set) or 1))
    base_ctx = _initial_mcts_context(case, recall_set)
    cfg = {"candidate_items": case.extracted_memory, "raw_events": case.raw_events}
    gold_answer = case.gold_answer
    baseline = case.primary_baseline.answer_score

    def own(context, start_gp):
        return _own_recovery(
            client, context, start_gp, max_depth, recall_set, gold_answer, verifier, baseline
        )

    # identity backbone contexts
    id0_ctx = _step_context(client, base_ctx, PipelineAction.IDENTITY, recall_set, 0, cfg)
    floor = own(
        _step_context(client, id0_ctx, PipelineAction.IDENTITY, recall_set, 1, cfg), 2
    )

    hop0_actions = _non_identity(get_legal_actions(recall_set, 0))
    hop1_actions = _non_identity(get_legal_actions(recall_set, 1))

    best_single = floor
    best_single_choice = None

    # single point at hop0 (hop1 = identity)
    for a in hop0_actions:
        ctx0 = _step_context(client, base_ctx, a, recall_set, 0, cfg)
        ctx1 = _step_context(client, ctx0, PipelineAction.IDENTITY, recall_set, 1, cfg)
        rec = own(ctx1, 2)
        if rec > best_single:
            best_single, best_single_choice = rec, (0, a.value)

    # single point at hop1 (hop0 = identity)
    for b in hop1_actions:
        ctx1 = _step_context(client, id0_ctx, b, recall_set, 1, cfg)
        rec = own(ctx1, 2)
        if rec > best_single:
            best_single, best_single_choice = rec, (1, b.value)

    # 2-point coalitions: hop0 action x hop1 action
    best_combo = floor
    best_combo_choice = None
    for a in hop0_actions:
        ctx0 = _step_context(client, base_ctx, a, recall_set, 0, cfg)
        for b in hop1_actions:
            ctx1 = _step_context(client, ctx0, b, recall_set, 1, cfg)
            rec = own(ctx1, 2)
            if rec > best_combo:
                best_combo, best_combo_choice = rec, (a.value, b.value)

    return floor, best_single, best_single_choice, best_combo, best_combo_choice


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases", default=str(DATA / "real_coupled_failure_boundary_cases.json")
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.8,
        help="Recovery bar. single < threshold AND combo >= threshold => true coupling.",
    )
    parser.add_argument("--out", default=str(OUT / "coupled_exhaustive_detail.csv"))
    args = parser.parse_args()

    client, judge_client = build_clients()
    assert_g_eval_available(judge_client, role="coupled-exhaustive")
    verifier = build_answer_verifier(judge_client, answer_mode="answer-rubric")

    # Coupled cases carry coupled_labels (>=2) in raw, not a single
    # perturbation_label. Mirror Exp8's load_cases_with_raw access path.
    cases = []
    for entry in load_cases_with_raw(args.cases):
        labels = (
            entry.raw.get("coupled_labels")
            or entry.raw.get("coupled_failure", {}).get("labels")
            or ()
        )
        if len(labels) >= 2:
            cases.append(entry.case)
    if args.limit:
        cases = cases[: args.limit]

    detail_rows = []
    n_true_coupled = 0
    n_single_recovers = 0

    print(f"Coupled exhaustive over {len(cases)} cases (threshold={args.threshold})\n")
    for i, case in enumerate(cases):
        floor, best_single, single_choice, best_combo, combo_choice = _evaluate_coupled_case(
            case, client, verifier
        )
        single_recovers = best_single >= args.threshold
        true_coupled = (best_single < args.threshold) and (best_combo >= args.threshold)
        n_single_recovers += int(single_recovers)
        n_true_coupled += int(true_coupled)

        verdict = (
            "single_ok" if single_recovers
            else "TRUE_COUPLED" if true_coupled
            else "neither"
        )
        detail_rows.append({
            "case_id": case.case_id,
            "floor": f"{floor:.4f}",
            "best_single": f"{best_single:.4f}",
            "single_choice": "" if single_choice is None else f"gp{single_choice[0]}:{single_choice[1]}",
            "best_combo": f"{best_combo:.4f}",
            "combo_choice": "" if combo_choice is None else f"{combo_choice[0]}+{combo_choice[1]}",
            "verdict": verdict,
        })
        print(
            f"  [{i+1}/{len(cases)}] {case.case_id:40s} "
            f"floor={floor:.2f} single={best_single:.2f} combo={best_combo:.2f} -> {verdict}"
        )

    n = len(cases)
    print("\n=== Coupled-failure exhaustive verdict ===")
    print(f"  cases                       : {n}")
    print(f"  single-point recovers (>=t) : {n_single_recovers}/{n}"
          f"  ({n_single_recovers/n:.3f})" if n else "")
    print(f"  TRUE coupled (single<t,combo>=t): {n_true_coupled}/{n}"
          f"  ({n_true_coupled/n:.3f})" if n else "")
    print(
        "\n  TRUE coupled rate is MCTS's reason to exist. If it is ~0 and "
        "single-point recovers most cases, Exp8's coupling was the A2 coverage "
        "artifact -> cut MCTS. If TRUE coupled is substantial, MCTS earns the "
        "b^d residual."
    )

    out_path = write_csv_table(
        Path(args.out),
        ["case_id", "floor", "best_single", "single_choice",
         "best_combo", "combo_choice", "verdict"],
        detail_rows,
        sandbox_root=OUT,
        judge_client=judge_client,
    )
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
