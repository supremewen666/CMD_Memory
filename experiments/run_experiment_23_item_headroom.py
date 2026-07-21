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
    python -m experiments.run_experiment_23_item_headroom --limit 6
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


def _legal_repair_actions(
    recall,
    gp,
    cfg,
    *,
    include_pipeline: bool,
    allowed_item_actions: set[str],
) -> list[PipelineAction]:
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
        and (
            (action.is_item_level and action.value in allowed_item_actions)
            or (include_pipeline and not action.is_item_level)
        )
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
    p.add_argument(
        "--headroom-margin",
        type=float,
        default=0.02,
        help="minimum extra net recovery over single to count a composite as useful.",
    )
    p.add_argument("--answer-recovered-threshold", type=float, default=0.8)
    p.add_argument(
        "--max-depth",
        type=int,
        default=1,
        help=(
            "generation points to scan. Default 1 is the STALE single-item protocol; "
            "set 2+ only when running composite diagnostics."
        ),
    )
    p.add_argument(
        "--operator-classes",
        choices=("single", "single-param", "all"),
        default="single",
        help=(
            "operator families to evaluate. Default single is the STALE main protocol; "
            "single-param adds item_signal_hints; all also runs double/double_param."
        ),
    )
    p.add_argument(
        "--action-space",
        choices=("auto", "stale-conflict", "all-item"),
        default="auto",
        help=(
            "item actions to scan. auto narrows STALE T1/T2 runs to "
            "item_stale+item_conflict and uses all item actions otherwise."
        ),
    )
    p.add_argument("--include-pipeline-actions", action="store_true")
    p.add_argument("--allow-data-floor-failures", action="store_true")
    p.add_argument(
        "--ec-test",
        action="store_true",
        help="run the expensive EC-on/off comparison for recovered cases.",
    )
    p.add_argument(
        "--no-ec-test",
        action="store_true",
        help="deprecated compatibility flag; EC tests are skipped unless --ec-test is set.",
    )
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
    double_extra_cases = []
    double_param_extra_cases = []
    ec_on = ec_off = ec_both_rec = 0
    rows = []
    print(f"Item operator headroom over {len(all_cases)} cases, labels={sorted(labels)}\n")
    allowed_item_actions = _allowed_item_actions(labels, args.action_space)
    print(
        "Config: "
        f"max_depth={args.max_depth}, "
        f"operator_classes={args.operator_classes}, "
        f"action_space={args.action_space}, "
        f"allowed_item_actions={sorted(allowed_item_actions)}, "
        f"ec_test={args.ec_test and not args.no_ec_test}, "
        f"include_pipeline_actions={args.include_pipeline_actions}\n"
    )

    for i, case in enumerate(all_cases, start=1):
        recall = _retrieved_memory_items(case)
        max_depth = max(1, min(args.max_depth, len(recall) or 1))
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
                allowed_item_actions=allowed_item_actions,
            ):
                op = OperatorSpec.single(gp, action)
                gain = net(rec(op))
                if gain > single_best:
                    single_best, single_op = gain, op

        double_best, double_op = -1.0, None
        if args.operator_classes == "all" and max_depth >= 2:
            for gp0 in range(max_depth):
                for gp1 in range(gp0 + 1, max_depth):
                    for a0 in _legal_repair_actions(
                        recall,
                        gp0,
                        cfg,
                        include_pipeline=args.include_pipeline_actions,
                        allowed_item_actions=allowed_item_actions,
                    ):
                        for a1 in _legal_repair_actions(
                            recall,
                            gp1,
                            cfg,
                            include_pipeline=args.include_pipeline_actions,
                            allowed_item_actions=allowed_item_actions,
                        ):
                            op = OperatorSpec.from_actions(((gp0, a0), (gp1, a1)))
                            gain = net(rec(op))
                            if gain > double_best:
                                double_best, double_op = gain, op

        param_best, param_op = -1.0, None
        if args.operator_classes in {"single-param", "all"} and single_op is not None:
            param_best, param_op = single_best, single_op
            for memory_id in parameter_item_ids:
                for weight in (-1.0, 1.0):
                    op = single_op.with_item_signal_hint(memory_id, weight)
                    gain = net(rec(op))
                    if gain > param_best:
                        param_best, param_op = gain, op

        double_param_best, double_param_op = -1.0, None
        if args.operator_classes == "all" and double_op is not None:
            double_param_best, double_param_op = double_best, double_op
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
            "double": double_op is not None and double_best > args.recovered_threshold,
            "param": param_best > args.recovered_threshold,
            "double_param": (
                double_param_op is not None
                and double_param_best > args.recovered_threshold
            ),
            "richer": richer_best > args.recovered_threshold,
        }
        evaluated = {
            "single": single_op is not None,
            "double": double_op is not None,
            "param": param_op is not None,
            "double_param": double_param_op is not None,
            "richer": richer_op is not None,
        }
        for arm in arms:
            if not evaluated[arm]:
                continue
            tally[arm][0] += int(recovered[arm])
            tally[arm][1] += 1

        is_headroom = recovered["richer"] and not recovered["single"]
        if is_headroom:
            headroom_cases.append(case.case_id)
        double_extra = _extra_over_single(double_best, double_op, single_best)
        double_param_extra = _extra_over_single(
            double_param_best,
            double_param_op,
            single_best,
        )
        double_has_extra = double_extra is not None and double_extra > args.headroom_margin
        double_param_has_extra = (
            double_param_extra is not None
            and double_param_extra > args.headroom_margin
        )
        if double_has_extra:
            double_extra_cases.append(case.case_id)
        if double_param_has_extra:
            double_param_extra_cases.append(case.case_id)

        ec_decision = ""
        if args.ec_test and not args.no_ec_test and recovered["richer"] and richer_op is not None:
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
                "single_net": _fmt_score(single_best, single_op),
                "double_net": _fmt_score(double_best, double_op),
                "param_net": _fmt_score(param_best, param_op),
                "double_param_net": _fmt_score(double_param_best, double_param_op),
                "richer_net": _fmt_score(richer_best, richer_op),
                "double_extra_over_single": _fmt_optional_score(double_extra),
                "double_param_extra_over_single": _fmt_optional_score(double_param_extra),
                "double_headroom_over_single": str(double_has_extra).lower(),
                "double_param_headroom_over_single": str(double_param_has_extra).lower(),
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
            f"single={_fmt_signed(single_best, single_op)} "
            f"double={_fmt_signed(double_best, double_op)} "
            f"(extra={_fmt_optional_signed(double_extra)}) "
            f"param={_fmt_signed(param_best, param_op)} "
            f"double+param={_fmt_signed(double_param_best, double_param_op)} "
            f"(extra={_fmt_optional_signed(double_param_extra)}){flag}"
        )

    _summary(
        tally,
        arms,
        headroom_cases,
        double_extra_cases,
        double_param_extra_cases,
        args.headroom_margin,
        ec_off,
        ec_on,
        ec_both_rec,
    )
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
            "double_extra_over_single",
            "double_param_extra_over_single",
            "double_headroom_over_single",
            "double_param_headroom_over_single",
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


def _allowed_item_actions(labels: set[str], action_space: str) -> set[str]:
    stale_conflict = {"item_stale", "item_conflict"}
    if action_space == "stale-conflict":
        return stale_conflict
    if action_space == "all-item":
        return set(ITEM_LABELS)
    if labels and labels.issubset(stale_conflict):
        return stale_conflict
    return set(ITEM_LABELS)


def _fmt_score(score: float, op) -> str:
    return f"{score:.4f}" if op is not None else "NA"


def _fmt_optional_score(score: float | None) -> str:
    return f"{score:.4f}" if score is not None else "NA"


def _fmt_signed(score: float, op) -> str:
    return f"{score:+.2f}" if op is not None else "NA"


def _fmt_optional_signed(score: float | None) -> str:
    return f"{score:+.2f}" if score is not None else "NA"


def _extra_over_single(
    score: float,
    op,
    single_best: float,
) -> float | None:
    if op is None:
        return None
    return score - single_best


def _summary(
    tally,
    arms,
    headroom_cases,
    double_extra_cases,
    double_param_extra_cases,
    headroom_margin,
    ec_off,
    ec_on,
    ec_both,
):
    print("\n=== Item recovery rate per operator class ===")
    print(f"{'arm':12s} {'recovered':>9s} {'total':>6s} {'rate':>8s}")
    for arm in arms:
        recovered, total = tally[arm]
        print(f"{arm:12s} {recovered:>9d} {total:>6d} {(recovered / total if total else 0):>8.4f}")
    print(f"\nITEM HEADROOM (richer recovers, single does NOT): {len(headroom_cases)}/{tally['single'][1]}")
    total_single = tally["single"][1]
    print(
        "COMPOSITE EXTRA over single "
        f"(margin>{headroom_margin:g}): "
        f"double={len(double_extra_cases)}/{total_single}, "
        f"double_param={len(double_param_extra_cases)}/{total_single}"
    )
    print("  Use these EXTRA columns to judge whether double adds information beyond single.")
    if ec_both:
        print(f"\n=== EC-framing on best item operator (n={ec_both}) ===")
        print(f"  ec_off recovered {ec_off}/{ec_both}")
        print(f"  ec_on  recovered {ec_on}/{ec_both}")
    print("\nNOTE: single run; repeat and analyze paired results before paper claims.")


if __name__ == "__main__":
    main()
