#!/usr/bin/env python3
"""Experiment 13: cross-source recovery and prior-transfer stability.

This runner is the recovery-framed replacement for the old cross-dataset
macro-F1 diagnostic. It treats Exp13 as a generalization check for the current
paper story:

  - Does the single-point oracle remain recoverable on each data source?
  - Do top-K priors transfer within a source and across held-out sources?

Run after building a three-source exhaustive prior bank:

    python -m experiments.probe_exhaustive \
        --cases data/probe_cases/real_three_source_cases.json \
        --limit 0 \
        --aggregate \
        --min-credit 0.05 \
        --out artifacts/sandbox/exhaustive_three_source_detail_mincredit05.csv

Then:

    python -m experiments.run_experiment_13_cross_dataset \
        --prior-bank artifacts/sandbox/exhaustive_three_source_detail_mincredit05.csv
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

ABSTAIN = "<abstain>"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "real_three_source_cases.json"))
    parser.add_argument(
        "--prior-bank",
        default=str(OUT / "exhaustive_three_source_detail_mincredit05.csv"),
        help="Three-source exhaustive CSV produced by probe_exhaustive.",
    )
    parser.add_argument(
        "--source-mode",
        choices=("all", "xsource", "both"),
        default="both",
        help=(
            "all = leave-one-case-out priors from any source; xsource = priors "
            "only from other sources; both = report both arms."
        ),
    )
    parser.add_argument("--topk", type=int, default=2, help="Seeds per case.")
    parser.add_argument("--neighbors", type=int, default=10, help="BM25 nearest prior rows.")
    parser.add_argument("--limit-per-source", type=int, default=0)
    parser.add_argument("--recovered-threshold", type=float, default=0.1)
    args = parser.parse_args()

    bank = _load_prior_bank(args.prior_bank)
    cases = [
        case
        for case in load_probe_cases(args.cases)
        if case.perturbation_label in PIPELINE_STEP_ACTIONS
    ]
    if args.limit_per_source:
        cases = _limit_by_source(cases, args.limit_per_source)

    id_to_source = {case.case_id: _source_of(case.case_id) for case in cases}
    queries = {case.case_id: case.query for case in cases}
    sources = sorted(set(id_to_source.values()))
    arms = _arms(args.source_mode)

    client, judge_client = build_clients()
    assert_g_eval_available(judge_client, role="cross-source-recovery")
    verifier = build_answer_verifier(judge_client, answer_mode="answer-rubric")

    from cmd_audit.harness import _initial_mcts_context, _retrieved_memory_items

    tallies = defaultdict(_new_tally)
    detail_rows = []
    excluded_cases = []

    print(
        f"Cross-source recovery transfer over {len(cases)} pipeline cases "
        f"(sources={sources}, topk={args.topk}, mode={args.source_mode})\n"
    )

    for i, case in enumerate(cases):
        source = _source_of(case.case_id)
        recall_set = _retrieved_memory_items(case)
        max_depth = max(1, min(3, len(recall_set) or 1))
        base_context = _initial_mcts_context(case, recall_set)
        cfg = {"candidate_items": case.extracted_memory, "raw_events": case.raw_events}

        def runner(choice):
            return run_single_repair(
                case,
                choice,
                client=client,
                answer_verifier=verifier,
                base_context=base_context,
                recall_set=recall_set,
                max_depth=max_depth,
                intervention_config=cfg,
            )

        baseline_gain = runner(None).recovery_gain
        if is_timeout_value(baseline_gain):
            excluded_cases.append(case.case_id)
            detail_rows.extend(_excluded_rows(case, source, arms))
            print(
                f"  [{i + 1}/{len(cases)}] {case.case_id:38s} "
                "EXCLUDED (identity-backbone rollout timed out)"
            )
            continue
        arm_choices = _choices_for_case(
            case,
            source=source,
            bank=bank,
            id_to_source=id_to_source,
            queries=queries,
            recall_set=recall_set,
            max_depth=max_depth,
            topk=args.topk,
            neighbors=args.neighbors,
        )

        print(
            f"  [{i + 1}/{len(cases)}] {case.case_id:38s} "
            f"src={source:12s} label={case.perturbation_label:20s} "
            f"baseline={baseline_gain:.3f}"
        )

        for arm in arms:
            if arm == "no_repair":
                net, choice, action = 0.0, None, ""
                seed_available = False
                timeout_count = 0
            else:
                choices = arm_choices[arm]
                net, choice, action, timeout_count = _best_net(
                    choices, runner, baseline_gain
                )
                seed_available = bool(choices)

            recovered = (
                not is_timeout_value(net)
                and net > args.recovered_threshold
            )
            _add_tally(tallies[(source, arm)], recovered, net, seed_available)
            _add_tally(tallies[("ALL", arm)], recovered, net, seed_available)
            detail_rows.append({
                "source": source,
                "case_id": case.case_id,
                "gold_label": case.perturbation_label,
                "status": "ok",
                "excluded": "false",
                "timeout_count": str(timeout_count),
                "arm": arm,
                "seed_choice": "" if choice is None else f"gp{choice[0]}:{choice[1].value}",
                "selected_action": action or "",
                "net_gain": format_recovery_value(net, digits=4),
                "recovered": str(recovered).lower(),
            })

    summary_rows = _summary_rows(tallies, sources, arms)
    _print_summary(summary_rows)
    if excluded_cases:
        print(
            "\nEXCLUDED (identity-backbone rollout timed out): "
            f"{len(excluded_cases)} -- absent from every arm denominator."
        )

    summary_path = write_csv_table(
        OUT / "experiment_cross_dataset.csv",
        [
            "source",
            "arm",
            "recovered",
            "total",
            "recovery_rate",
            "avg_net_gain",
            "timeout_count",
            "seed_available",
            "seed_available_rate",
            "vs_oracle",
        ],
        summary_rows,
        sandbox_root=OUT,
        judge_client=judge_client,
    )
    detail_path = write_csv_table(
        OUT / "experiment_cross_dataset_detail.csv",
        [
            "source",
            "case_id",
            "gold_label",
            "status",
            "excluded",
            "timeout_count",
            "arm",
            "seed_choice",
            "selected_action",
            "net_gain",
            "recovered",
        ],
        detail_rows,
        sandbox_root=OUT,
        judge_client=judge_client,
    )
    print(f"\nWrote {summary_path}")
    print(f"Wrote {detail_path}")


def _load_prior_bank(path: str | Path) -> dict[str, tuple[int | None, str | None]]:
    bank: dict[str, tuple[int | None, str | None]] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            action = row["culprit_action"]
            gp_raw = row["culprit_gen_point"]
            if action == ABSTAIN or gp_raw == "":
                bank[row["case_id"]] = (None, None)
            else:
                bank[row["case_id"]] = (int(gp_raw), action)
    return bank


def _source_of(case_id: str) -> str:
    return case_id.split("-", 1)[0]


def _limit_by_source(cases, limit):
    groups = defaultdict(list)
    for case in cases:
        groups[_source_of(case.case_id)].append(case)
    limited = []
    for source in sorted(groups):
        limited.extend(groups[source][:limit])
    return limited


def _arms(source_mode: str) -> list[str]:
    arms = ["no_repair", "oracle"]
    if source_mode in ("all", "both"):
        arms.extend(["global_all", "bm25_all"])
    if source_mode in ("xsource", "both"):
        arms.extend(["global_xsource", "bm25_xsource"])
    return arms


def _choices_for_case(
    case,
    *,
    source,
    bank,
    id_to_source,
    queries,
    recall_set,
    max_depth,
    topk,
    neighbors,
):
    own_gp, own_action = bank.get(case.case_id, (None, None))
    oracle = _pairs_to_choices(
        [(own_gp, own_action)] if own_gp is not None else [],
        recall_set,
        max_depth,
    )
    all_ids = _candidate_ids(bank, case.case_id, id_to_source)
    xsource_ids = _candidate_ids(
        bank,
        case.case_id,
        id_to_source,
        allowed=lambda cid: id_to_source.get(cid, _source_of(cid)) != source,
    )
    return {
        "oracle": oracle,
        "global_all": _pairs_to_choices(_global_topk(bank, all_ids, topk), recall_set, max_depth),
        "bm25_all": _pairs_to_choices(
            _bm25_topk(bank, all_ids, case.case_id, queries, topk, neighbors),
            recall_set,
            max_depth,
        ),
        "global_xsource": _pairs_to_choices(
            _global_topk(bank, xsource_ids, topk),
            recall_set,
            max_depth,
        ),
        "bm25_xsource": _pairs_to_choices(
            _bm25_topk(bank, xsource_ids, case.case_id, queries, topk, neighbors),
            recall_set,
            max_depth,
        ),
    }


def _candidate_ids(bank, exclude_case_id, id_to_source, allowed=None):
    ids = []
    for cid, (gp, action) in bank.items():
        if cid == exclude_case_id or gp is None or action is None:
            continue
        if cid not in id_to_source:
            continue
        if allowed is not None and not allowed(cid):
            continue
        ids.append(cid)
    return ids


def _global_topk(bank, candidate_ids, k):
    counter = Counter(bank[cid] for cid in candidate_ids)
    return [pair for pair, _n in counter.most_common(k)]


def _bm25_topk(bank, candidate_ids, heldout_id, queries, k, neighbors):
    held_q = queries.get(heldout_id, "")
    if not held_q or not candidate_ids:
        return _global_topk(bank, candidate_ids, k)
    query_tokens = tokenize(held_q)
    doc_tokens = [tokenize(queries.get(cid, "")) for cid in candidate_ids]
    scores = compute_bm25_scores(query_tokens, doc_tokens)
    ranked = sorted(range(len(candidate_ids)), key=lambda i: scores[i], reverse=True)
    near_ids = [candidate_ids[i] for i in ranked[:neighbors]]
    return _global_topk(bank, near_ids, k)


def _pairs_to_choices(pairs, recall_set, max_depth):
    choices = []
    for gp, action_str in pairs:
        if gp is None or action_str is None:
            continue
        action = LABEL_TO_ACTION.get(action_str)
        if action is None or action == PipelineAction.IDENTITY:
            continue
        if 0 <= gp < max_depth and action in get_legal_actions(recall_set, gp):
            choices.append((gp, action))
    return choices


def _best_net(choices, runner, baseline_gain):
    candidates = []
    for choice in choices:
        result = runner(choice)
        net = result.recovery_gain - baseline_gain
        candidates.append((net, (choice, result.selected_action)))
    timeout_count = recovery_timeout_count(score for score, _payload in candidates)
    if not candidates:
        return 0.0, None, None, timeout_count
    best_net, payload = best_scored_pair(candidates)
    if payload is None:
        return float("nan"), None, None, timeout_count
    best_choice, best_action = payload
    return best_net, best_choice, best_action, timeout_count


def _excluded_rows(case, source, arms):
    return [
        {
            "source": source,
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


def _new_tally():
    return {
        "recovered": 0,
        "total": 0,
        "net_sum": 0.0,
        "net_count": 0,
        "timeout_count": 0,
        "seed_available": 0,
    }


def _add_tally(tally, recovered, net, seed_available):
    tally["recovered"] += int(recovered)
    tally["total"] += 1
    if is_timeout_value(net):
        tally["timeout_count"] += 1
    else:
        tally["net_sum"] += net
        tally["net_count"] += 1
    tally["seed_available"] += int(seed_available)


def _summary_rows(tallies, sources, arms):
    rows = []
    for source in sources + ["ALL"]:
        oracle_tally = tallies[(source, "oracle")]
        oracle_rate = _rate(oracle_tally["recovered"], oracle_tally["total"])
        for arm in arms:
            tally = tallies[(source, arm)]
            rate = _rate(tally["recovered"], tally["total"])
            rows.append({
                "source": source,
                "arm": arm,
                "recovered": str(tally["recovered"]),
                "total": str(tally["total"]),
                "recovery_rate": f"{rate:.4f}",
                "avg_net_gain": f"{(
                    tally['net_sum'] / tally['net_count']
                    if tally['net_count'] else 0.0
                ):.4f}",
                "timeout_count": str(tally["timeout_count"]),
                "seed_available": str(tally["seed_available"]),
                "seed_available_rate": f"{_rate(tally['seed_available'], tally['total']):.4f}",
                "vs_oracle": (
                    "-"
                    if arm == "no_repair" or oracle_rate == 0.0
                    else f"{(rate / oracle_rate):.2f}"
                ),
            })
    return rows


def _rate(num, den):
    return num / den if den else 0.0


def _print_summary(rows):
    print("\n=== Cross-source recovery summary ===")
    print(
        f"{'source':14s} {'arm':15s} {'recov':>7s} {'total':>6s} "
        f"{'rate':>8s} {'avg_net':>8s} {'seed%':>8s} {'vs_oracle':>10s}"
    )
    for row in rows:
        print(
            f"{row['source']:14s} {row['arm']:15s} "
            f"{row['recovered']:>7s} {row['total']:>6s} "
            f"{row['recovery_rate']:>8s} {row['avg_net_gain']:>8s} "
            f"{row['seed_available_rate']:>8s} {row['vs_oracle']:>10s}"
        )


if __name__ == "__main__":
    main()
