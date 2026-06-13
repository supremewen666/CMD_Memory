#!/usr/bin/env python3
"""Exhaustive single-point counterfactual probe (diagnostic C, NOT a runner).

Bypasses MCTS/UCB entirely. For each case, deterministically evaluates every
single-point intervention along the identity backbone:

  credit[0][a] = own_recovery(hop1=a,        hop2=identity)
               - own_recovery(hop1=identity, hop2=identity)
  credit[1][a] = own_recovery(hop1=identity, hop2=a)
               - own_recovery(hop1=identity, hop2=identity)

This is the UPPER BOUND of the counterfactual signal: every (hop, action) path
is rolled out, so any low accuracy here is a signal-quality problem, not a
search-coverage problem. main_culprit = argmax credit over non-identity actions.

Run after vLLM is up:
    python -m experiments.probe_exhaustive --limit 0 --aggregate
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.labels import PIPELINE_STEP_ACTIONS
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.data_io import load_probe_cases
from cmd_audit.mcts.actions import (
    PipelineAction,
    apply_pipeline_action,
    get_legal_actions,
)
from cmd_audit.mcts.rollout import rollout_to_terminal
from cmd_audit.mcts.search import _generate_conditioned_context
from experiments.experiment_runner_common import (
    assert_g_eval_available,
    build_answer_verifier,
    load_raw_rows,
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "probe_cases"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "real_multihop_cases.json"))
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--aggregate", action="store_true")
    args = parser.parse_args()

    client = LLMClient(LLMClientConfig())
    assert_g_eval_available(client, role="probe-exhaustive")
    verifier = build_answer_verifier(client, answer_mode="answer-rubric")

    cases = [
        c
        for c in load_probe_cases(args.cases)
        if c.perturbation_label in PIPELINE_STEP_ACTIONS
    ]
    if args.limit:
        cases = cases[: args.limit]

    expected_hops = {}
    for row in load_raw_rows(args.cases):
        expected = row.get("expected_fault") or {}
        if expected.get("hop_index") is not None:
            expected_hops[str(row["case_id"])] = int(expected["hop_index"])

    by_label = defaultdict(lambda: [0, 0, 0])
    pred_by_label = defaultdict(Counter)
    genpoint_by_label = defaultdict(Counter)

    print(f"Exhaustive single-point over {len(cases)} cases\n")
    for i, case in enumerate(cases):
        credits, culprit = _evaluate_case(case, client, verifier)
        gold = case.perturbation_label
        by_label[gold][0] += 1
        if culprit:
            gp, action, credit = culprit
            pred = action.value
            pred_by_label[gold][pred] += 1
            genpoint_by_label[gold][gp] += 1
            if pred == gold:
                by_label[gold][1] += 1
            expected_hop = expected_hops.get(case.case_id)
            if expected_hop is not None and gp + 1 == expected_hop:
                by_label[gold][2] += 1
        else:
            pred_by_label[gold]["<none>"] += 1
        if not args.aggregate:
            print(f"case_id    : {case.case_id}")
            print(f"gold       : {gold}  expected_hop={expected_hops.get(case.case_id)}")
            print(f"culprit    : {culprit}")
            for k in sorted(credits):
                inner = {a.value: round(v, 3) for a, v in credits[k].items()}
                print(f"  credit[{k}]: {inner}")
            print("-" * 60)
        else:
            print(f"  [{i+1}/{len(cases)}] {gold:20s} -> {culprit}")

    _print_aggregate(by_label, pred_by_label, genpoint_by_label, len(cases))


PLACEHOLDER_TAIL = None


def _step_context(client, parent_context, action, recall_set, gen_point, intervention_config):
    """Replicate search.py's context_generator: apply action then re-generate prefix."""
    intervened = apply_pipeline_action(
        action, parent_context, recall_set, gen_point,
        intervention_config=intervention_config,
    )
    return _generate_conditioned_context(client, intervened, gen_point + 1)


def _own_recovery(client, context, start_gp, max_depth, recall_set, gold_answer, verifier, baseline):
    """Single rollout from a node: remaining hops identity, score terminal."""
    result = rollout_to_terminal(
        client, context, start_gp, max_depth, recall_set, gold_answer,
        answer_verifier=verifier, baseline_answer_score=baseline,
    )
    return result.recovery_gain if result.rollout_successful else 0.0


def _evaluate_case(case, client, verifier):
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

    # hop1 actions (gen_point 0), then hop2 actions along identity backbone (gen_point 1).
    hop1_actions = get_legal_actions(recall_set, 0)
    # identity-backbone context at hop2: hop1 = identity
    id1_ctx = _step_context(client, base_ctx, PipelineAction.IDENTITY, recall_set, 0, cfg)
    hop2_actions = get_legal_actions(recall_set, 1)

    credits = {0: {}, 1: {}}
    # baseline = identity everywhere
    id_id_recovery = own(
        _step_context(client, id1_ctx, PipelineAction.IDENTITY, recall_set, 1, cfg), 2
    )

    # credit[0]: vary hop1, hop2 = identity (rollout fills identity for remaining)
    id1_recovery = own(id1_ctx, 1)  # hop1=identity, then rollout identity
    for a in hop1_actions:
        if a == PipelineAction.IDENTITY:
            credits[0][a] = 0.0
            continue
        ctx = _step_context(client, base_ctx, a, recall_set, 0, cfg)
        credits[0][a] = own(ctx, 1) - id1_recovery

    # credit[1]: hop1 = identity, vary hop2
    for a in hop2_actions:
        if a == PipelineAction.IDENTITY:
            credits[1][a] = 0.0
            continue
        ctx = _step_context(client, id1_ctx, a, recall_set, 1, cfg)
        credits[1][a] = own(ctx, 2) - id_id_recovery

    culprit = None
    best = float("-inf")
    for gp in (0, 1):
        for a, c in credits[gp].items():
            if a != PipelineAction.IDENTITY and c > best:
                best = c
                culprit = (gp, a, c)
    return credits, culprit


def _print_aggregate(by_label, pred_by_label, genpoint_by_label, n_cases):
    print("\n=== Per-label accuracy (exhaustive single-point) ===")
    print(f"{'gold label':22s} {'n':>3s} {'label_acc':>10s} {'hop_acc':>10s}")
    tot = [0, 0, 0]
    for label in sorted(by_label):
        n, lc, hc = by_label[label]
        tot[0] += n; tot[1] += lc; tot[2] += hc
        print(f"{label:22s} {n:>3d} {lc/n:>10.4f} {hc/n:>10.4f}")
    if tot[0]:
        print(f"{'TOTAL':22s} {tot[0]:>3d} {tot[1]/tot[0]:>10.4f} {tot[2]/tot[0]:>10.4f}")
    print("\n=== Predicted-label distribution per gold ===")
    for label in sorted(pred_by_label):
        print(f"  {label:22s} -> {dict(pred_by_label[label])}")
    print("\n=== gen_point distribution per gold (want gp=1) ===")
    for label in sorted(genpoint_by_label):
        print(f"  {label:22s} -> {dict(genpoint_by_label[label])}")


if __name__ == "__main__":
    main()
