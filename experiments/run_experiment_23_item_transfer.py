#!/usr/bin/env python3
"""Experiment 23b: do item-layer operators transfer?

Consumes the Exp23a operator bank and evaluates leave-one-out transfer with the
same content-fingerprint retrieval story as Exp22.

Run:
    export LLM_TIMEOUT=120
    python -m experiments.run_experiment_23_item_transfer \
        --operator-bank artifacts/sandbox/item_operator_headroom_detail.csv
Smoke:
    python -m experiments.run_experiment_23_item_transfer --limit 8
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.labels import ITEM_LABELS
from cmd_audit.data_io import load_probe_cases_v1
from cmd_audit.eval.writers import (
    best_scored_pair,
    format_recovery_value,
    is_timeout_value,
    recovery_case_outcomes,
    write_csv_table,
)
from cmd_audit.counterfactual.actions import PipelineAction, get_legal_actions
from cmd_audit.repair.failure_memory import (
    _memory_fingerprint,
    _query_signature_similarity,
)
from experiments.experiment_runner_common import (
    DATA,
    OUT,
    assert_g_eval_available,
    build_answer_verifier,
)
from experiments.experiment_runner_common import build_clients
from experiments.probe_exhaustive import _step_context, _own_recovery

_ACTION_BY_NAME = {action.value: action for action in PipelineAction}
ITEM_TRANSFER_ARMS = (
    "no_repair",
    "single_xfer",
    "item_oracle",
    "item_global",
    "item_fp",
    "item_fp_topN",
    "random_topN",
)
ITEM_TRANSFER_DETAIL_FIELDS = [
    "case_id",
    "gold_label",
    "status",
    "excluded",
    "timeout_count",
    *[f"{arm}_net" for arm in ITEM_TRANSFER_ARMS],
    *[f"{arm}_rec" for arm in ITEM_TRANSFER_ARMS],
    "topn_cost",
    "topn_candidates",
    "random_topn_cost",
    "random_topn_candidates",
    "item_oracle_op",
    "item_fp_op",
    "item_fp_topN_op",
    "random_topN_op",
]


def _parse_op(op_str: str):
    op_str = (op_str or "").split("|")[0].strip()
    if not op_str:
        return []
    out = []
    for token in op_str.split("+"):
        token = token.strip()
        if not token.startswith("gp") or ":" not in token:
            continue
        gp_raw, action_raw = token[2:].split(":", 1)
        action = _ACTION_BY_NAME.get(action_raw)
        if action is not None:
            out.append((int(gp_raw), action))
    return out


def _op_shape(op):
    return tuple((gp, action.value) for gp, action in op)


def _load_bank(path: Path):
    bank = {}
    for row in csv.DictReader(path.open(encoding="utf-8")):
        best_name = "richer_op"
        if not row.get(best_name):
            best_name = "single_op"
        bank[row["case_id"]] = {
            "op": _parse_op(row.get(best_name, "")),
            "single": _parse_op(row.get("single_op", "")),
            "label": row.get("gold_label", ""),
        }
    return bank


def _modal_shape(shapes):
    if not shapes:
        return None
    return Counter(shapes).most_common(1)[0][0]


def _distinct_shapes(shapes):
    out = []
    seen = set()
    for shape in shapes:
        if not shape or shape in seen:
            continue
        seen.add(shape)
        out.append(shape)
    return out


def _random_topn_shapes(shapes, *, case_id: str, seed: int, topn: int):
    candidates = _distinct_shapes(shapes)
    rng = random.Random(f"{seed}:{case_id}")
    rng.shuffle(candidates)
    return candidates[: max(0, topn)]


def _shape_to_ops(shape, recall, max_depth, cfg):
    if not shape:
        return []
    legal_by_gp = {
        gp: set(
            get_legal_actions(
                recall,
                gp,
                include_item_actions=True,
                intervention_config=cfg,
            )
        )
        for gp in range(max_depth)
    }
    ops = []
    for gp, action_value in shape:
        action = _ACTION_BY_NAME.get(action_value)
        if action is None or action == PipelineAction.IDENTITY:
            continue
        if 0 <= gp < max_depth and action in legal_by_gp[gp]:
            ops.append((gp, action))
    return ops


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cases", default=str(DATA / "real_item_layer_cases.json"))
    p.add_argument("--operator-bank", default=str(OUT / "item_operator_headroom_detail.csv"))
    p.add_argument("--out", default=str(OUT / "item_operator_transfer_detail.csv"))
    p.add_argument("--labels", default=",".join(sorted(ITEM_LABELS)))
    p.add_argument("--neighbors", type=int, default=10)
    p.add_argument("--topn", type=int, default=5)
    p.add_argument("--random-seed", type=int, default=23)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--recovered-threshold", type=float, default=0.1)
    p.add_argument(
        "--max-depth",
        type=int,
        default=1,
        help=(
            "generation points to execute. Default 1 matches the STALE single-item "
            "operator bank; set 2+ only for composite diagnostics."
        ),
    )
    args = p.parse_args()

    bank_path = Path(args.operator_bank)
    if not bank_path.exists():
        raise SystemExit(f"operator bank not found: {bank_path} (run Exp23a first).")
    bank = _load_bank(bank_path)
    labels = {label.strip() for label in args.labels.split(",") if label.strip()}

    all_cases = {
        case.case_id: case
        for case in load_probe_cases_v1(args.cases)
        if case.perturbation_label in labels
    }
    cases = [all_cases[cid] for cid in bank if cid in all_cases and bank[cid]["op"]]
    if args.limit:
        cases = cases[: args.limit]

    client, judge_client = build_clients()
    assert_g_eval_available(judge_client, role="item-operator-transfer")
    verifier = build_answer_verifier(judge_client, answer_mode="answer-rubric")

    from cmd_audit.harness import _initial_mcts_context, _retrieved_memory_items

    queries = {cid: case.query for cid, case in all_cases.items()}
    fingerprints = {
        cid: _memory_fingerprint(tuple(item.text for item in case.extracted_memory))
        for cid, case in all_cases.items()
    }

    arms = ITEM_TRANSFER_ARMS
    tally = defaultdict(lambda: [0, 0])
    rows = []
    excluded_cases: list[str] = []
    print(f"Item operator transfer (LOO) over {len(cases)} cases\n")
    print(
        "Config: "
        f"max_depth={args.max_depth}, "
        f"neighbors={args.neighbors}, "
        f"topn={args.topn}\n"
    )

    for i, case in enumerate(cases, start=1):
        cid = case.case_id
        recall = _retrieved_memory_items(case)
        max_depth = max(1, min(args.max_depth, len(recall) or 1))
        base_ctx = _initial_mcts_context(case, recall)
        cfg = {"candidate_items": case.extracted_memory, "raw_events": case.raw_events}
        gold = case.gold_answer
        baseline = case.primary_baseline.answer_score

        def run_ops(ops):
            ctx = base_ctx
            by_gp = {gp: action for gp, action in ops}
            for gp in range(max_depth):
                ctx = _step_context(
                    client,
                    ctx,
                    by_gp.get(gp, PipelineAction.IDENTITY),
                    recall,
                    gp,
                    cfg,
                )
            return _own_recovery(
                client,
                ctx,
                max_depth,
                max_depth,
                recall,
                gold,
                verifier,
                baseline,
            )

        base_gain = run_ops([])
        if is_timeout_value(base_gain):
            # Every arm net would be score - NaN == NaN and would therefore be
            # tallied as False. The baseline is unmeasured, so exclude the case
            # from every arm, top-N, and random-control denominator instead.
            excluded_cases.append(cid)
            rows.append(_excluded_detail_row(case))
            print(
                f"  [{i}/{len(cases)}] {case.perturbation_label:28s} "
                "EXCLUDED (identity-backbone rollout timed out)"
            )
            continue

        arm_timeouts = 0

        def net_of(score):
            """Net gain with arm/candidate timeouts counted separately."""
            nonlocal arm_timeouts
            if is_timeout_value(score):
                arm_timeouts += 1
                return float("nan")
            return score - base_gain

        others = [(other_id, row) for other_id, row in bank.items() if other_id != cid]
        item_shapes_all = [_op_shape(row["op"]) for _other_id, row in others if row["op"]]
        single_shapes_all = [
            _op_shape(row["single"]) for _other_id, row in others if row["single"]
        ]

        held_fp = fingerprints.get(cid, "")
        cand_ids = [other_id for other_id, _row in others if other_id in all_cases]
        fp_shapes = item_shapes_all
        fp_topn_shapes = []
        if held_fp and cand_ids:
            sims = sorted(
                (
                    (
                        other_id,
                        _query_signature_similarity(
                            held_fp,
                            fingerprints.get(other_id, ""),
                        ),
                    )
                    for other_id in cand_ids
                ),
                key=lambda item: item[1],
                reverse=True,
            )
            near_ids = {other_id for other_id, score in sims[: args.neighbors] if score > 0.0}
            near_shapes = [
                _op_shape(bank[other_id]["op"])
                for other_id in near_ids
                if bank[other_id]["op"]
            ]
            if near_shapes:
                fp_shapes = near_shapes
            seen = set()
            for other_id, score in sims:
                if score <= 0.0:
                    break
                shape = _op_shape(bank[other_id]["op"]) if bank[other_id]["op"] else None
                if shape and shape not in seen:
                    seen.add(shape)
                    fp_topn_shapes.append(shape)
                if len(fp_topn_shapes) >= args.topn:
                    break

        arm_ops = {
            "no_repair": [],
            "single_xfer": _shape_to_ops(
                _modal_shape(single_shapes_all),
                recall,
                max_depth,
                cfg,
            ),
            "item_oracle": bank[cid]["op"],
            "item_global": _shape_to_ops(
                _modal_shape(item_shapes_all),
                recall,
                max_depth,
                cfg,
            ),
            "item_fp": _shape_to_ops(
                _modal_shape(fp_shapes),
                recall,
                max_depth,
                cfg,
            ),
        }

        per = {}
        for arm in ("no_repair", "single_xfer", "item_oracle", "item_global", "item_fp"):
            net = net_of(run_ops(arm_ops[arm])) if arm_ops[arm] else 0.0
            per[arm] = (net, False)

        def run_topn_shapes(candidate_shapes):
            cost = 0
            candidates = []
            for rank, shape in enumerate(candidate_shapes, start=1):
                ops = _shape_to_ops(shape, recall, max_depth, cfg)
                if not ops:
                    continue
                cost = rank
                candidate_net = net_of(run_ops(ops))
                candidates.append((candidate_net, ops))
                if candidate_net > args.recovered_threshold:
                    break
            best_net, best_op = _best_topn_candidate(candidates)
            return best_net, cost, best_op

        topn_net, topn_cost, topn_op = run_topn_shapes(fp_topn_shapes)
        per["item_fp_topN"] = (topn_net, False)

        random_shapes = _random_topn_shapes(
            item_shapes_all,
            case_id=cid,
            seed=args.random_seed,
            topn=args.topn,
        )
        random_net, random_cost, random_op = run_topn_shapes(random_shapes)
        per["random_topN"] = (random_net, False)

        outcomes = recovery_case_outcomes(
            base_gain,
            {arm: per[arm][0] for arm in arms},
            threshold=args.recovered_threshold,
        )
        assert outcomes is not None  # NaN base_gain was excluded above.
        for arm in arms:
            per[arm] = (per[arm][0], outcomes[arm])
            tally[arm][0] += int(outcomes[arm])
            tally[arm][1] += 1
        topn_recovered = outcomes["item_fp_topN"]
        random_recovered = outcomes["random_topN"]

        rows.append(
            {
                "case_id": cid,
                "gold_label": case.perturbation_label,
                "status": "ok",
                "excluded": "false",
                "timeout_count": str(arm_timeouts),
                **{
                    f"{arm}_net": format_recovery_value(per[arm][0], digits=4)
                    for arm in arms
                },
                **{f"{arm}_rec": str(per[arm][1]).lower() for arm in arms},
                "topn_cost": str(topn_cost),
                "topn_candidates": str(len(fp_topn_shapes)),
                "random_topn_cost": str(random_cost),
                "random_topn_candidates": str(len(random_shapes)),
                "item_oracle_op": _fmt_ops(arm_ops["item_oracle"]),
                "item_fp_op": _fmt_ops(arm_ops["item_fp"]),
                "item_fp_topN_op": _fmt_ops(topn_op),
                "random_topN_op": _fmt_ops(random_op),
            }
        )
        print(
            f"  [{i}/{len(cases)}] {case.perturbation_label:28s} "
            f"oracle={'Y' if per['item_oracle'][1] else '.'} "
            f"topN={'Y' if topn_recovered else '.'}(c{topn_cost}) "
            f"rand={'Y' if random_recovered else '.'}(c{random_cost}) "
            f"fp={'Y' if per['item_fp'][1] else '.'} "
            f"single={'Y' if per['single_xfer'][1] else '.'}"
        )

    _summary(tally, arms, excluded_cases)
    path = write_csv_table(
        args.out,
        ITEM_TRANSFER_DETAIL_FIELDS,
        rows,
        sandbox_root=OUT,
        judge_client=judge_client,
    )
    print(f"\nWrote {path}")


def _fmt_ops(ops):
    return "+".join(f"gp{gp}:{action.value}" for gp, action in ops)


def _best_topn_candidate(candidates):
    """Return the best finite candidate, retaining the legacy 0.0 floor."""
    score, ops = best_scored_pair([(0.0, []), *candidates])
    assert ops is not None
    return score, ops


def _excluded_detail_row(case) -> dict[str, str]:
    """Detail row for an unmeasured identity-backbone baseline."""
    row = {
        field: ""
        for field in ITEM_TRANSFER_DETAIL_FIELDS
    }
    row.update(
        case_id=case.case_id,
        gold_label=case.perturbation_label,
        status="base_gain_timeout",
        excluded="true",
        timeout_count="1",
        topn_cost="0",
        topn_candidates="0",
        random_topn_cost="0",
        random_topn_candidates="0",
    )
    for arm in ITEM_TRANSFER_ARMS:
        row[f"{arm}_rec"] = "false"
    return row


def _summary(tally, arms, excluded_cases=()):
    print("\n=== Item transfer recovery rate ===")
    print(f"{'arm':14s} {'recovered':>9s} {'total':>6s} {'rate':>8s} {'vs_oracle':>10s}")
    oracle_rate = (
        tally["item_oracle"][0] / tally["item_oracle"][1]
        if tally["item_oracle"][1]
        else 0.0
    )
    for arm in arms:
        recovered, total = tally[arm]
        rate = recovered / total if total else 0.0
        frac = f"{rate / oracle_rate:.2f}" if oracle_rate and arm != "no_repair" else "-"
        print(f"{arm:14s} {recovered:>9d} {total:>6d} {rate:>8.4f} {frac:>10s}")
    if excluded_cases:
        print(
            "\nEXCLUDED (identity-backbone rollout timed out): "
            f"{len(excluded_cases)} -- not counted as recovered or "
            "not-recovered in any arm."
        )
    print("\nDECISION ARM = item_fp_topN; random_topN controls for execution budget.")
    print("GO: item_fp_topN captures most item_oracle recovery and beats random_topN.")


if __name__ == "__main__":
    main()
