#!/usr/bin/env python3
"""Experiment 23a: item-layer repair headroom.

This is the item-failure analogue of Exp21. It scans the item-level repair
action space over item_stale/conflict/poisoned/wrong/compression cases and
writes an operator bank that Exp23b can test for transfer.

Run:
    export LLM_TIMEOUT=120
    python -m experiments.run_experiment_23_item_headroom \
        --cases data/probe_cases/real_item_layer_cases.json
Smoke:
    python -m experiments.run_experiment_23_item_headroom --limit 6 --no-ec-test
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.labels import ITEM_LABELS
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.data_io import load_probe_cases_v1
from cmd_audit.eval.writers import write_csv_table
from cmd_audit.counterfactual.actions import PipelineAction, get_legal_actions
from cmd_audit.counterfactual.operators import OperatorSpec, apply_operator_static
from cmd_audit.repair.actions import get_targeted_repair_action_v1
from cmd_audit.scoring import score_answer_with_verifier
from experiments.experiment_runner_common import (
    DATA,
    OUT,
    AGENT_SYSTEM_PROMPT,
    assert_g_eval_available,
    build_answer_verifier,
)
from experiments.probe_exhaustive import _step_context, _own_recovery


def _chain_recovery(
    client,
    base_ctx,
    recall,
    max_depth,
    cfg,
    operator,
    gold,
    verifier,
    baseline,
):
    ctx = base_ctx
    action_by_gp = operator.action_by_generation_point()
    op_cfg = operator.intervention_config(cfg)
    for gp in range(max_depth):
        action = action_by_gp.get(gp, PipelineAction.IDENTITY)
        ctx = _step_context(client, ctx, action, recall, gp, op_cfg)
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


def _agent_answer(client, query: str, context: str) -> str:
    prompt = "\n\n".join(("CONTEXT:", context or "(empty)", "QUERY:", query, "ANSWER:"))
    out = client.generate(prompt, system=AGENT_SYSTEM_PROMPT)
    return out.strip() if out else ""


def _parameter_item_ids(recall: tuple, cfg: dict) -> tuple[str, ...]:
    seen: set[str] = set()
    memory_ids: list[str] = []
    for item in recall + tuple(cfg.get("candidate_items") or ()):
        if item.memory_id in seen:
            continue
        seen.add(item.memory_id)
        memory_ids.append(item.memory_id)
    return tuple(memory_ids)


def _legal_repair_actions(recall, gp, cfg, *, include_pipeline: bool) -> list[PipelineAction]:
    actions = get_legal_actions(
        recall,
        gp,
        include_item_actions=True,
        intervention_config=cfg,
    )
    return [
        action
        for action in actions
        if action != PipelineAction.IDENTITY
        and (include_pipeline or action.is_item_level)
    ]


def _data_floor_ok(case) -> bool:
    memory_ids = {item.memory_id for item in case.extracted_memory}
    event_ids = {event.event_id for event in case.raw_events}
    for evidence in case.gold_evidence:
        if evidence.source_memory_id and evidence.source_memory_id not in memory_ids:
            return False
        if evidence.source_event_id and evidence.source_event_id not in event_ids:
            return False
    return True


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cases", default=str(DATA / "real_item_layer_cases.json"))
    p.add_argument("--labels", default=",".join(sorted(ITEM_LABELS)))
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--out", default=str(OUT / "item_operator_headroom_detail.csv"))
    p.add_argument("--recovered-threshold", type=float, default=0.1)
    p.add_argument("--answer-recovered-threshold", type=float, default=0.8)
    p.add_argument("--include-pipeline-actions", action="store_true")
    p.add_argument("--allow-data-floor-failures", action="store_true")
    p.add_argument("--no-ec-test", action="store_true")
    args = p.parse_args()

    labels = {label.strip() for label in args.labels.split(",") if label.strip()}
    all_cases = [
        case
        for case in load_probe_cases_v1(args.cases)
        if case.perturbation_label in labels
    ]
    if args.limit:
        all_cases = all_cases[: args.limit]
    floor_failures = [case.case_id for case in all_cases if not _data_floor_ok(case)]
    if floor_failures and not args.allow_data_floor_failures:
        raise SystemExit(
            "item data-floor check failed for "
            f"{len(floor_failures)} cases; first={floor_failures[0]}"
        )

    client = LLMClient(LLMClientConfig())
    assert_g_eval_available(client, role="item-operator-headroom")
    verifier = build_answer_verifier(client, answer_mode="answer-rubric")

    from cmd_audit.harness import _initial_mcts_context, _retrieved_memory_items

    arms = ("single", "double", "param", "double_param", "richer")
    tally = defaultdict(lambda: [0, 0])
    headroom_cases = []
    ec_on = ec_off = ec_both_rec = 0
    rows = []
    print(f"Item operator headroom over {len(all_cases)} cases, labels={sorted(labels)}\n")

    for i, case in enumerate(all_cases, start=1):
        recall = _retrieved_memory_items(case)
        max_depth = max(1, min(3, len(recall) or 1))
        base_ctx = _initial_mcts_context(case, recall)
        cfg = {"candidate_items": case.extracted_memory, "raw_events": case.raw_events}
        parameter_item_ids = _parameter_item_ids(recall, cfg)
        gold = case.gold_answer
        baseline = case.primary_baseline.answer_score

        def rec(operator=None):
            op = operator or OperatorSpec()
            return _chain_recovery(
                client,
                base_ctx,
                recall,
                max_depth,
                cfg,
                op,
                gold,
                verifier,
                baseline,
            )

        base_gain = rec()

        def net(score):
            return score - base_gain

        single_best, single_op = -1.0, None
        for gp in range(max_depth):
            for action in _legal_repair_actions(
                recall,
                gp,
                cfg,
                include_pipeline=args.include_pipeline_actions,
            ):
                op = OperatorSpec.single(gp, action)
                gain = net(rec(op))
                if gain > single_best:
                    single_best, single_op = gain, op

        double_best, double_op = -1.0, None
        if max_depth >= 2:
            for gp0 in range(max_depth):
                for gp1 in range(gp0 + 1, max_depth):
                    for a0 in _legal_repair_actions(
                        recall,
                        gp0,
                        cfg,
                        include_pipeline=args.include_pipeline_actions,
                    ):
                        for a1 in _legal_repair_actions(
                            recall,
                            gp1,
                            cfg,
                            include_pipeline=args.include_pipeline_actions,
                        ):
                            op = OperatorSpec.from_actions(((gp0, a0), (gp1, a1)))
                            gain = net(rec(op))
                            if gain > double_best:
                                double_best, double_op = gain, op

        param_best, param_op = single_best, single_op
        if single_op is not None:
            for memory_id in parameter_item_ids:
                for weight in (-1.0, 1.0):
                    op = single_op.with_item_signal_hint(memory_id, weight)
                    gain = net(rec(op))
                    if gain > param_best:
                        param_best, param_op = gain, op

        double_param_best, double_param_op = double_best, double_op
        if double_op is not None:
            for memory_id in parameter_item_ids:
                for weight in (-1.0, 1.0):
                    op = double_op.with_item_signal_hint(memory_id, weight)
                    gain = net(rec(op))
                    if gain > double_param_best:
                        double_param_best, double_param_op = gain, op

        richer_candidates = [
            (single_best, single_op),
            (double_best, double_op),
            (param_best, param_op),
            (double_param_best, double_param_op),
        ]
        richer_best, richer_op = max(
            (pair for pair in richer_candidates if pair[1] is not None),
            key=lambda pair: pair[0],
        )
        recovered = {
            "single": single_best > args.recovered_threshold,
            "double": double_best > args.recovered_threshold,
            "param": param_best > args.recovered_threshold,
            "double_param": double_param_best > args.recovered_threshold,
            "richer": richer_best > args.recovered_threshold,
        }
        for arm in arms:
            tally[arm][0] += int(recovered[arm])
            tally[arm][1] += 1

        is_headroom = recovered["richer"] and not recovered["single"]
        if is_headroom:
            headroom_cases.append(case.case_id)

        ec_decision = ""
        if not args.no_ec_test and recovered["richer"] and richer_op is not None:
            corrected = apply_operator_static(
                base_ctx,
                recall,
                richer_op,
                intervention_config=cfg,
            )
            last_action = richer_op.last_action
            if last_action is not None:
                cause = get_targeted_repair_action_v1(last_action.value).cause
                ec_block = f"[Error]\nThe previous memory context failed.\n\n[Cause]\n{cause}"
                s_off = score_answer_with_verifier(
                    verifier,
                    _agent_answer(client, case.query, corrected),
                    gold,
                )
                s_on = score_answer_with_verifier(
                    verifier,
                    _agent_answer(client, case.query, ec_block + "\n\n" + corrected),
                    gold,
                )
                r_off = s_off >= args.answer_recovered_threshold
                r_on = s_on >= args.answer_recovered_threshold
                ec_off += int(r_off)
                ec_on += int(r_on)
                ec_both_rec += 1
                ec_decision = f"off={s_off:.2f}/{r_off} on={s_on:.2f}/{r_on}"

        rows.append(
            {
                "case_id": case.case_id,
                "gold_label": case.perturbation_label,
                "base_gain": f"{base_gain:.4f}",
                "single_net": f"{single_best:.4f}",
                "double_net": f"{double_best:.4f}",
                "param_net": f"{param_best:.4f}",
                "double_param_net": f"{double_param_best:.4f}",
                "richer_net": f"{richer_best:.4f}",
                "single_op": _fmt(single_op),
                "double_op": _fmt(double_op),
                "param_op": _fmt(param_op),
                "double_param_op": _fmt(double_param_op),
                "richer_op": _fmt(richer_op),
                "headroom": str(is_headroom).lower(),
                "data_floor_ok": str(_data_floor_ok(case)).lower(),
                "ec_test": ec_decision,
            }
        )
        flag = "  <== HEADROOM" if is_headroom else ""
        print(
            f"  [{i}/{len(all_cases)}] {case.perturbation_label:28s} "
            f"single={single_best:+.2f} double={double_best:+.2f} "
            f"param={param_best:+.2f} double+param={double_param_best:+.2f}{flag}"
        )

    _summary(tally, arms, headroom_cases, ec_off, ec_on, ec_both_rec)
    path = write_csv_table(
        args.out,
        [
            "case_id",
            "gold_label",
            "base_gain",
            "single_net",
            "double_net",
            "param_net",
            "double_param_net",
            "richer_net",
            "single_op",
            "double_op",
            "param_op",
            "double_param_op",
            "richer_op",
            "headroom",
            "data_floor_ok",
            "ec_test",
        ],
        rows,
        sandbox_root=OUT.parent,
    )
    print(f"\nWrote {path}")


def _fmt(op):
    return op.format() if op else ""


def _summary(tally, arms, headroom_cases, ec_off, ec_on, ec_both):
    print("\n=== Item recovery rate per operator class ===")
    print(f"{'arm':12s} {'recovered':>9s} {'total':>6s} {'rate':>8s}")
    for arm in arms:
        recovered, total = tally[arm]
        print(f"{arm:12s} {recovered:>9d} {total:>6d} {(recovered / total if total else 0):>8.4f}")
    print(f"\nITEM HEADROOM (richer recovers, single does NOT): {len(headroom_cases)}/{tally['single'][1]}")
    if ec_both:
        print(f"\n=== EC-framing on best item operator (n={ec_both}) ===")
        print(f"  ec_off recovered {ec_off}/{ec_both}")
        print(f"  ec_on  recovered {ec_on}/{ec_both}")
    print("\nNOTE: single run; repeat and analyze paired results before paper claims.")


if __name__ == "__main__":
    main()
