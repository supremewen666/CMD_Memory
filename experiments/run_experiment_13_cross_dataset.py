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
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.data_io import load_probe_cases
from cmd_audit.eval.writers import write_csv_table
from cmd_audit.counterfactual.actions import PipelineAction, get_legal_actions
from cmd_audit.repair.efficacy import LABEL_TO_ACTION, run_single_repair
from cmd_audit.scoring.retrieval import compute_bm25_scores, tokenize
from experiments.experiment_runner_common import (
    DATA,
    OUT,
    assert_g_eval_available,
    build_answer_verifier,
)

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

    client = LLMClient(LLMClientConfig())
    assert_g_eval_available(client, role="cross-source-recovery")
    verifier = build_answer_verifier(client, answer_mode="answer-rubric")

    from cmd_audit.harness import _initial_mcts_context, _retrieved_memory_items

    tallies = defaultdict(_new_tally)
    detail_rows = []

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
            else:
                choices = arm_choices[arm]
                net, choice, action = _best_net(choices, runner, baseline_gain)
                seed_available = bool(choices)

            recovered = net > args.recovered_threshold
            _add_tally(tallies[(source, arm)], recovered, net, seed_available)
            _add_tally(tallies[("ALL", arm)], recovered, net, seed_available)
            detail_rows.append({
                "source": source,
                "case_id": case.case_id,
                "gold_label": case.perturbation_label,
                "arm": arm,
                "seed_choice": "" if choice is None else f"gp{choice[0]}:{choice[1].value}",
                "selected_action": action or "",
                "net_gain": f"{net:.4f}",
                "recovered": str(recovered).lower(),
            })

    summary_rows = _summary_rows(tallies, sources, arms)
    _print_summary(summary_rows)

    summary_path = write_csv_table(
        OUT / "experiment_cross_dataset.csv",
        [
            "source",
            "arm",
            "recovered",
            "total",
            "recovery_rate",
            "avg_net_gain",
            "seed_available",
            "seed_available_rate",
            "vs_oracle",
        ],
        summary_rows,
        sandbox_root=OUT,
    )
    detail_path = write_csv_table(
        OUT / "experiment_cross_dataset_detail.csv",
        [
            "source",
            "case_id",
            "gold_label",
            "arm",
            "seed_choice",
            "selected_action",
            "net_gain",
            "recovered",
        ],
        detail_rows,
        sandbox_root=OUT,
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
    best_net = 0.0
    best_choice = None
    best_action = None
    for choice in choices:
        result = runner(choice)
        net = result.recovery_gain - baseline_gain
        if best_choice is None or net > best_net:
            best_net = net
            best_choice = choice
            best_action = result.selected_action
    return best_net, best_choice, best_action


def _new_tally():
    return {"recovered": 0, "total": 0, "net_sum": 0.0, "seed_available": 0}


def _add_tally(tally, recovered, net, seed_available):
    tally["recovered"] += int(recovered)
    tally["total"] += 1
    tally["net_sum"] += net
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
                "avg_net_gain": f"{(tally['net_sum'] / tally['total'] if tally['total'] else 0.0):.4f}",
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
