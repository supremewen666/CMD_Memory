#!/usr/bin/env python3
"""Experiment 15: LOO cross-case prior transfer (validates the online seed).

A2 proved the recovering (gen_point, action) is recoverable when the correct
point is seeded, and the offline exhaustive scan shows it is concentrated
WITHIN a fault type. But online we do NOT know the fault type — we retrieve a
prior by query signature from past cases. This experiment closes that gap with
leave-one-out: for each held-out case, build the seed from the OTHER 74 cases
only (never its own row, never its gold type), apply the top-K (gp, action) as
repairs, and take the best net recovery.

Arms (all share the gold-free executor run_single_repair):
  no_repair  baseline floor (choice=None).
  oracle     UPPER BOUND — the held-out case's OWN exhaustive culprit. This is
             the A2 single-point ceiling; transfer arms are judged against it.
  global     mode C — top-K most frequent (gp, action) over all other decided
             rows, blind to query. The "does query even matter" floor.
  bm25       mode A — top-K modal (gp, action) among the K-nearest other cases
             by BM25 query similarity. The real online retrieval surrogate.

The prior bank is the offline exhaustive CSV (probe_exhaustive --out), so this
runner pays only the cheap repair rollouts, not another b*d scan.

Run after vLLM is up:
    python -m experiments.run_experiment_15_prior_transfer \
        --prior-bank artifacts/sandbox/exhaustive_detail_mincredit05.csv \
        --mode both
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.labels import PIPELINE_STEP_ACTIONS
from cmd_audit.data_io import load_probe_cases
from cmd_audit.eval.writers import (
    best_scored_pair,
    format_recovery_value,
    is_timeout_value,
    recovery_timeout_count,
    write_csv_table,
)
from cmd_audit.counterfactual.actions import PipelineAction, get_legal_actions
from cmd_audit.repair.efficacy import LABEL_TO_ACTION, run_single_repair
from cmd_audit.scoring.retrieval import compute_bm25_scores, tokenize
from experiments.experiment_runner_common import (
    DATA,
    OUT,
    assert_g_eval_available,
    build_answer_verifier,
)
from experiments.experiment_runner_common import build_clients

ROOT = Path(__file__).resolve().parent.parent

ABSTAIN = "<abstain>"


def _load_prior_bank(path):
    """case_id -> (gen_point|None, action_str|None). Abstain rows -> (None, None)."""
    bank = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            action = row["culprit_action"]
            gp_raw = row["culprit_gen_point"]
            if action == ABSTAIN or gp_raw == "":
                bank[row["case_id"]] = (None, None)
            else:
                bank[row["case_id"]] = (int(gp_raw), action)
    return bank


def _decided_pairs(bank, exclude_case_id):
    """All decided (gp, action_str) culprits from the bank except one case."""
    pairs = []
    for cid, (gp, action) in bank.items():
        if cid == exclude_case_id:
            continue
        if gp is not None and action is not None:
            pairs.append((gp, action))
    return pairs


def _global_topk(bank, exclude_case_id, k):
    """Mode C: top-k most frequent (gp, action) over all other decided rows."""
    counter = Counter(_decided_pairs(bank, exclude_case_id))
    return [pair for pair, _n in counter.most_common(k)]


def _bm25_topk(bank, exclude_case_id, k, *, queries, neighbors):
    """Mode A: top-k modal (gp, action) among the `neighbors`-nearest other
    cases by BM25 query similarity (ranked over decided cases only)."""
    held_q = queries.get(exclude_case_id, "")
    cand_ids = [
        cid for cid, (gp, _a) in bank.items()
        if cid != exclude_case_id and gp is not None
    ]
    if not held_q or not cand_ids:
        return _global_topk(bank, exclude_case_id, k)  # fall back to global
    query_tokens = tokenize(held_q)
    doc_tokens = [tokenize(queries.get(cid, "")) for cid in cand_ids]
    scores = compute_bm25_scores(query_tokens, doc_tokens)
    ranked = sorted(range(len(cand_ids)), key=lambda i: scores[i], reverse=True)
    near_ids = [cand_ids[i] for i in ranked[:neighbors]]
    counter = Counter(bank[cid] for cid in near_ids)
    return [pair for pair, _n in counter.most_common(k)]


def _pairs_to_choices(pairs, recall_set, max_depth):
    """Map (gp, action_str) prior pairs to legal (gp, PipelineAction) choices."""
    choices = []
    for gp, action_str in pairs:
        action = LABEL_TO_ACTION.get(action_str)
        if action is None or action == PipelineAction.IDENTITY:
            continue
        if 0 <= gp < max_depth and action in get_legal_actions(recall_set, gp):
            choices.append((gp, action))
    return choices


def _best_net(choices, runner, baseline_gain):
    """Run each seeded choice, return (best_net, best_choice). Empty -> (0, None)."""
    candidates = []
    for choice in choices:
        res = runner(choice)
        net = res.recovery_gain - baseline_gain
        candidates.append((net, (choice, res.selected_action)))
    timeout_count = recovery_timeout_count(score for score, _payload in candidates)
    if not candidates:
        return 0.0, None, None, timeout_count
    best_net, payload = best_scored_pair(candidates)
    if payload is None:
        return float("nan"), None, None, timeout_count
    best_choice, best_action = payload
    return best_net, best_choice, best_action, timeout_count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "real_multihop_cases.json"))
    parser.add_argument(
        "--prior-bank",
        default=str(OUT / "exhaustive_detail_mincredit05.csv"),
        help="Offline exhaustive CSV (probe_exhaustive --out) used as the prior bank.",
    )
    parser.add_argument("--mode", choices=("global", "bm25", "both"), default="both")
    parser.add_argument("--topk", type=int, default=2, help="Seeds per case (top-K prior pairs).")
    parser.add_argument("--neighbors", type=int, default=10, help="BM25 K-nearest cases (mode A).")
    parser.add_argument("--recovered-threshold", type=float, default=0.1)
    args = parser.parse_args()

    bank = _load_prior_bank(args.prior_bank)

    client, judge_client = build_clients()
    assert_g_eval_available(judge_client, role="prior-transfer")
    verifier = build_answer_verifier(judge_client, answer_mode="answer-rubric")

    cases = [
        c
        for c in load_probe_cases(args.cases)
        if c.perturbation_label in PIPELINE_STEP_ACTIONS
    ]
    queries = {c.case_id: c.query for c in cases}

    from cmd_audit.harness import _initial_mcts_context, _retrieved_memory_items

    arms = ["no_repair", "oracle"]
    if args.mode in ("global", "both"):
        arms.append("global")
    if args.mode in ("bm25", "both"):
        arms.append("bm25")

    tally = defaultdict(lambda: [0, 0])  # arm -> [recovered, total]
    detail_rows = []
    excluded_cases = []

    print(f"Prior transfer (LOO): arms={arms} over {len(cases)} cases, topk={args.topk}\n")
    for i, case in enumerate(cases):
        recall_set = _retrieved_memory_items(case)
        max_depth = max(1, min(3, len(recall_set) or 1))
        base_ctx = _initial_mcts_context(case, recall_set)
        cfg = {"candidate_items": case.extracted_memory, "raw_events": case.raw_events}
        gold = case.perturbation_label

        def runner(choice):
            return run_single_repair(
                case, choice,
                client=client, answer_verifier=verifier,
                base_context=base_ctx, recall_set=recall_set, max_depth=max_depth,
                intervention_config=cfg,
            )

        baseline_gain = runner(None).recovery_gain
        if is_timeout_value(baseline_gain):
            excluded_cases.append(case.case_id)
            detail_rows.extend(_excluded_rows(case, arms))
            print(
                f"  [{i+1}/{len(cases)}] {gold:20s} "
                "EXCLUDED (identity-backbone rollout timed out)"
            )
            continue

        # oracle: the held-out case's OWN exhaustive culprit (upper bound).
        own_gp, own_action = bank.get(case.case_id, (None, None))
        oracle_choices = _pairs_to_choices(
            [(own_gp, own_action)] if own_gp is not None else [], recall_set, max_depth
        )
        # transfer seeds (LOO: never the case's own row).
        global_choices = _pairs_to_choices(
            _global_topk(bank, case.case_id, args.topk), recall_set, max_depth
        )
        bm25_choices = _pairs_to_choices(
            _bm25_topk(bank, case.case_id, args.topk, queries=queries, neighbors=args.neighbors),
            recall_set, max_depth,
        )

        arm_choices = {"global": global_choices, "bm25": bm25_choices, "oracle": oracle_choices}

        for arm in arms:
            if arm == "no_repair":
                net, choice, action = 0.0, None, ""
                timeout_count = 0
            else:
                net, choice, action, timeout_count = _best_net(
                    arm_choices[arm], runner, baseline_gain
                )
            recovered = (
                not is_timeout_value(net)
                and net > args.recovered_threshold
            )
            tally[arm][0] += int(recovered)
            tally[arm][1] += 1
            detail_rows.append({
                "case_id": case.case_id,
                "gold_label": gold,
                "status": "ok",
                "excluded": "false",
                "timeout_count": str(timeout_count),
                "arm": arm,
                "seed_choice": "" if choice is None else f"gp{choice[0]}:{choice[1].value}",
                "selected_action": action or "",
                "net_gain": format_recovery_value(net, digits=4),
                "recovered": str(recovered).lower(),
            })
        print(
            f"  [{i+1}/{len(cases)}] {gold:20s} baseline={baseline_gain:.3f} "
            f"oracle={'Y' if oracle_choices else '-'}"
        )

    _print_summary(tally, arms)
    if excluded_cases:
        print(
            "\nEXCLUDED (identity-backbone rollout timed out): "
            f"{len(excluded_cases)} -- absent from every arm denominator."
        )

    detail_path = write_csv_table(
        OUT / "prior_transfer_detail.csv",
        ["case_id", "gold_label", "status", "excluded", "timeout_count",
         "arm", "seed_choice",
         "selected_action", "net_gain", "recovered"],
        detail_rows,
        sandbox_root=OUT,
        judge_client=judge_client,
    )
    print(f"\nWrote {detail_path}")


def _excluded_rows(case, arms):
    return [
        {
            "case_id": case.case_id,
            "gold_label": case.perturbation_label,
            "status": "base_gain_timeout",
            "excluded": "true",
            "timeout_count": "1",
            "arm": arm,
            "seed_choice": "",
            "selected_action": "",
            "net_gain": "",
            "recovered": "false",
        }
        for arm in arms
    ]


def _print_summary(tally, arms) -> None:
    print("\n=== Recovered rate per arm (net of no_repair) ===")
    print(f"{'arm':12s} {'recovered':>9s} {'total':>6s} {'rate':>8s} {'vs oracle':>10s}")
    oracle_rate = (tally['oracle'][0] / tally['oracle'][1]) if tally['oracle'][1] else 0.0
    for arm in arms:
        rec, tot = tally[arm]
        rate = rec / tot if tot else 0.0
        frac = f"{rate / oracle_rate:.2f}" if oracle_rate and arm != "no_repair" else "-"
        print(f"{arm:12s} {rec:>9d} {tot:>6d} {rate:>8.4f} {frac:>10s}")
    print(
        "\n(oracle = each case's own single-point ceiling; transfer arms judged "
        "as rate/oracle_rate. GO if bm25 >= ~0.8 of oracle.)"
    )


if __name__ == "__main__":
    main()
