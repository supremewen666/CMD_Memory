#!/usr/bin/env python3
"""Probe Exp1 MCTS coordinates (A+C diagnostic, NOT a runner).

For a few multihop cases, print:
  - main_culprit  (generation_point, action, credit)
  - action_credits key distribution (which generation_points carry credit)
  - iterations_completed / nodes_explored

Run after vLLM is up:
    python -m experiments.probe_exp1_coords --limit 3 --max-iterations 40
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.labels import PIPELINE_STEP_ACTIONS
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.data_io import load_probe_cases
from experiments.experiment_runner_common import (
    assert_g_eval_available,
    build_answer_verifier,
    load_raw_rows,
    run_mcts_for_case,
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "probe_cases"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "real_multihop_cases.json"))
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--max-iterations", type=int, default=40)
    parser.add_argument(
        "--aggregate",
        action="store_true",
        help="Run all cases and print per-label label/hop accuracy instead of per-case trees.",
    )
    args = parser.parse_args()

    client = LLMClient(LLMClientConfig())
    assert_g_eval_available(client, role="probe-exp1")
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

    if args.aggregate:
        _run_aggregate(cases, client, verifier, expected_hops, args.max_iterations)
        return

    print(f"Probing {len(cases)} cases, max_iterations={args.max_iterations}\n")
    for case in cases:
        search = run_mcts_for_case(
            case, client, verifier, max_iterations=args.max_iterations
        )
        culprit = search.main_culprit
        credit_keys = sorted(search.action_credits.keys())
        print(f"case_id          : {case.case_id}")
        print(f"gold label       : {case.perturbation_label}")
        print(f"expected hop     : {expected_hops.get(case.case_id)}  (1-based)")
        print(f"main_culprit     : {culprit}")
        if culprit:
            gp, action, credit = culprit
            print(f"  -> gen_point   : {gp}  (credit-key view)")
            print(f"  -> action      : {getattr(action, 'value', action)}")
            print(f"  -> credit      : {credit:.4f}")
        print(f"credit keys      : {credit_keys}")
        for k in credit_keys:
            inner = {
                getattr(a, "value", str(a)): round(v, 3)
                for a, v in search.action_credits[k].items()
            }
            print(f"  credit[{k}]     : {inner}")
        print(f"iterations       : {search.iterations_completed}")
        print(f"nodes_explored   : {search.nodes_explored}")
        print(f"early_stops      : {search.early_stops}")
        print(f"terminal_rollouts: {search.terminal_rollouts}")
        print("tree:")
        _dump_tree(search.tree.root)
        print("-" * 60)

def _dump_tree(node, indent: int = 1) -> None:
    seq = "->".join(getattr(a, "value", str(a)) for a in node.action_sequence) or "ROOT"
    print(
        f"{'  ' * indent}gp={node.generation_point} "
        f"q_max={node.q_max:.3f} visits={node.visit_count} "
        f"term={node.is_terminal} [{seq}]"
    )
    for child in node.children.values():
        _dump_tree(child, indent + 1)


def _run_aggregate(cases, client, verifier, expected_hops, max_iterations) -> None:
    from collections import Counter, defaultdict

    print(f"Aggregate over {len(cases)} cases, max_iterations={max_iterations}\n")
    # per gold label: [n, label_correct, hop_correct]
    by_label: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    pred_by_label: dict[str, Counter] = defaultdict(Counter)
    genpoint_by_label: dict[str, Counter] = defaultdict(Counter)
    key1_present = 0  # cases where credit key 1 (hop2) carries a non-identity action

    for i, case in enumerate(cases):
        gold = case.perturbation_label
        search = run_mcts_for_case(case, client, verifier, max_iterations=max_iterations)
        by_label[gold][0] += 1
        culprit = search.main_culprit
        if culprit:
            gp, action, _credit = culprit
            pred = getattr(action, "value", str(action))
            pred_by_label[gold][pred] += 1
            genpoint_by_label[gold][gp] += 1
            if pred == gold:
                by_label[gold][1] += 1
            expected_hop = expected_hops.get(case.case_id)
            if expected_hop is not None and gp + 1 == expected_hop:
                by_label[gold][2] += 1
        else:
            pred_by_label[gold]["<none>"] += 1
        # did hop2 (credit key 1) ever expand a non-identity action?
        key1 = search.action_credits.get(1, {})
        if any(getattr(a, "value", str(a)) != "identity" for a in key1):
            key1_present += 1
        print(f"  [{i+1}/{len(cases)}] {gold:20s} -> culprit={culprit}")

    print("\n=== Per-label accuracy ===")
    print(f"{'gold label':22s} {'n':>3s} {'label_acc':>10s} {'hop_acc':>10s}")
    tot = [0, 0, 0]
    for label in sorted(by_label):
        n, lc, hc = by_label[label]
        tot[0] += n; tot[1] += lc; tot[2] += hc
        print(f"{label:22s} {n:>3d} {lc/n:>10.4f} {hc/n:>10.4f}")
    print(f"{'TOTAL':22s} {tot[0]:>3d} {tot[1]/tot[0]:>10.4f} {tot[2]/tot[0]:>10.4f}")

    print("\n=== Predicted-label distribution per gold ===")
    for label in sorted(pred_by_label):
        print(f"  {label:22s} -> {dict(pred_by_label[label])}")

    print("\n=== main_culprit gen_point distribution per gold (want gp=1) ===")
    for label in sorted(genpoint_by_label):
        print(f"  {label:22s} -> {dict(genpoint_by_label[label])}")

    print(f"\nhop2 (credit key=1) had a non-identity action in {key1_present}/{len(cases)} cases")


if __name__ == "__main__":
    main()
