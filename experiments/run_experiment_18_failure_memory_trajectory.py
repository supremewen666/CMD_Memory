#!/usr/bin/env python3
"""Experiment 18: online FailureMemory trajectory.

Prequential stream test for the "self-evolution" claim. Each case may only use
active priors learned from earlier recovered cases. The full case ledger records
all outcomes, but only recovered cases become future automatic seeds.

Run after vLLM is up:
    python -m experiments.run_experiment_18_failure_memory_trajectory
"""

from __future__ import annotations

import argparse
import random
import sys
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
from cmd_audit.repair.failure_memory import FailureMemoryStore, StepLevelRecord
from experiments.experiment_runner_common import (
    DATA,
    OUT,
    assert_g_eval_available,
    build_answer_verifier,
)
from experiments.experiment_runner_common import build_clients
from experiments.probe_exhaustive import _evaluate_case


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "real_multihop_cases.json"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--topk", type=int, default=2)
    parser.add_argument("--neighbors", type=int, default=10)
    parser.add_argument("--bin-size", type=int, default=15)
    parser.add_argument("--min-credit", type=float, default=0.05)
    parser.add_argument("--recovered-threshold", type=float, default=0.1)
    args = parser.parse_args()

    client, judge_client = build_clients()
    assert_g_eval_available(judge_client, role="failure-memory-trajectory")
    verifier = build_answer_verifier(judge_client, answer_mode="answer-rubric")

    cases = [
        c
        for c in load_probe_cases(args.cases)
        if c.perturbation_label in PIPELINE_STEP_ACTIONS
    ]
    random.Random(args.seed).shuffle(cases)
    if args.limit:
        cases = cases[: args.limit]

    from cmd_audit.harness import _initial_mcts_context, _retrieved_memory_items

    active_priors = FailureMemoryStore()
    detail_rows: list[dict[str, str]] = []

    print(
        "FailureMemory trajectory: "
        f"{len(cases)} cases, seed={args.seed}, topk={args.topk}\n"
    )
    for case_index, case in enumerate(cases, start=1):
        recall_set = _retrieved_memory_items(case)
        memory_texts = tuple(getattr(it, "text", str(it)) for it in recall_set)
        max_depth = max(1, min(3, len(recall_set) or 1))
        base_ctx = _initial_mcts_context(case, recall_set)
        cfg = {"candidate_items": case.extracted_memory, "raw_events": case.raw_events}
        baseline_gain = run_single_repair(
            case,
            None,
            client=client,
            answer_verifier=verifier,
            base_context=base_ctx,
            recall_set=recall_set,
            max_depth=max_depth,
            intervention_config=cfg,
        ).recovery_gain
        if is_timeout_value(baseline_gain):
            detail_rows.append(
                _excluded_detail_row(
                    case_index,
                    case,
                    prior_total_count=len(active_priors),
                )
            )
            print(
                f"  [{case_index}/{len(cases)}] "
                f"{case.perturbation_label:20s} EXCLUDED "
                "(identity-backbone rollout timed out)"
            )
            continue

        seed_pairs, prior_source_count = _retrieve_seed_pairs(
            active_priors,
            case.query,
            max_depth=max_depth,
            topk=args.topk,
            neighbors=args.neighbors,
            memory_texts=memory_texts,
        )
        seed_choices = _pairs_to_choices(seed_pairs, recall_set, max_depth)

        def runner(choice):
            return run_single_repair(
                case,
                choice,
                client=client,
                answer_verifier=verifier,
                base_context=base_ctx,
                recall_set=recall_set,
                max_depth=max_depth,
                intervention_config=cfg,
            )

        seed_result = _run_seed_stage(
            seed_choices,
            runner,
            baseline_gain,
            recovered_threshold=args.recovered_threshold,
        )

        fallback_choice = None
        fallback_action = ""
        fallback_net = 0.0
        fallback_rollouts = 0
        recovered_choice = seed_result["recovered_choice"]
        recovered_net = float(seed_result["best_net_gain"])
        recovery_source = "seed" if seed_result["seed_rank_recovered"] else ""

        if recovered_choice is None:
            fallback_rollouts = _fallback_rollout_budget(recall_set)
            _credits, culprit = _evaluate_case(
                case,
                client,
                verifier,
                min_credit=args.min_credit,
            )
            if culprit is not None:
                gp, action, _credit = culprit
                candidate = (gp, action)
                fallback_res = runner(candidate)
                fallback_net = fallback_res.recovery_gain - baseline_gain
                fallback_choice = candidate
                fallback_action = fallback_res.selected_action or action.value
                if (
                    not is_timeout_value(fallback_net)
                    and (
                        is_timeout_value(recovered_net)
                        or fallback_net > recovered_net
                    )
                ):
                    recovered_net = fallback_net
                if fallback_net > args.recovered_threshold:
                    recovered_choice = candidate
                    recovery_source = "fallback"

        recovered = recovered_choice is not None and recovered_net > args.recovered_threshold
        active_prior_written = False
        if recovered and recovered_choice is not None:
            gp, action = recovered_choice
            record = StepLevelRecord.from_mcts_result(
                query=case.query,
                hop_index=gp + 1,
                label=action.value,
                cause=f"{recovery_source}: {action.value} at hop {gp + 1}",
                corrected_memory="",
                repair_guidance=(
                    f"Prioritize {action.value} repairs at hop {gp + 1} "
                    "for similar future queries."
                ),
                recovery_success=True,
                recovery_gain=recovered_net,
                memory_texts=memory_texts,
            )
            active_priors.add_if_recovered(record, "recovered")
            active_prior_written = True

        seed_rollouts = int(seed_result["seed_rollouts_used"])
        timeout_count = int(seed_result["timeout_count"]) + int(
            is_timeout_value(fallback_net)
        )
        total_rollouts = seed_rollouts + fallback_rollouts
        rollouts_to_recovery = (
            seed_result["seed_rank_recovered"]
            if recovery_source == "seed"
            else total_rollouts if recovery_source == "fallback"
            else 0
        )
        ledger = _ledger_fields(
            case,
            recovered_choice,
            recovered_net,
            recovery_source=recovery_source or "unrecovered",
        )

        detail_rows.append({
            "case_index": str(case_index),
            "case_id": case.case_id,
            "gold_label": case.perturbation_label,
            "status": "ok",
            "excluded": "false",
            "timeout_count": str(timeout_count),
            "prior_total_count": str(len(active_priors) - int(active_prior_written)),
            "prior_source_count": str(prior_source_count),
            "seed_choices": _format_choices(seed_choices),
            "seed_rank_recovered": str(seed_result["seed_rank_recovered"]),
            "seed_selected_choice": _format_choice(seed_result["recovered_choice"]),
            "seed_best_net_gain": format_recovery_value(
                float(seed_result["best_net_gain"]), digits=4
            ),
            "seed_rollouts_used": str(seed_rollouts),
            "fallback_choice": _format_choice(fallback_choice),
            "fallback_action": fallback_action,
            "fallback_net_gain": format_recovery_value(
                fallback_net, digits=4
            ),
            "fallback_rollouts_used": str(fallback_rollouts),
            "total_rollouts_used": str(total_rollouts),
            "rollouts_used_to_recovery": str(rollouts_to_recovery),
            "best_net_gain": format_recovery_value(
                recovered_net, digits=4
            ),
            "recovered": str(recovered).lower(),
            "recovery_source": recovery_source or "",
            "ledger_written": "true",
            "active_prior_written": str(active_prior_written).lower(),
            **ledger,
        })
        print(
            f"  [{case_index}/{len(cases)}] {case.perturbation_label:20s} "
            f"priors={prior_source_count:02d} seeds={_format_choices(seed_choices) or '-'} "
            f"recovered={str(recovered).lower()} source={recovery_source or '-'} "
            f"rollouts={rollouts_to_recovery or total_rollouts}"
        )

    detail_path = write_csv_table(
        OUT / "failure_memory_trajectory_detail.csv",
        _detail_fieldnames(),
        detail_rows,
        sandbox_root=OUT,
        judge_client=judge_client,
    )
    summary_rows = _summary_rows(detail_rows, bin_size=args.bin_size)
    summary_path = write_csv_table(
        OUT / "failure_memory_trajectory_summary.csv",
        _summary_fieldnames(),
        summary_rows,
        sandbox_root=OUT,
        judge_client=judge_client,
    )

    _print_summary(summary_rows)
    print(f"\nWrote {detail_path}")
    print(f"Wrote {summary_path}")


def _retrieve_seed_pairs(
    active_priors: FailureMemoryStore,
    query: str,
    *,
    max_depth: int,
    topk: int,
    neighbors: int,
    memory_texts: tuple[str, ...] = (),
) -> tuple[list[tuple[int, str]], int]:
    """Return top-K (gen_point, action) pairs from prior cases only.

    Seeds are the (gen_point, action) of the most fingerprint-similar prior
    failures, retrieved nearest-first. We deliberately do NOT gate on
    ``get_label_prior`` success rate: in a recurrent stream every stored case
    recovered, so every label's success rate saturates at ~1.0 and the >0.5
    gate passes all four actions, washing out the fingerprint signal (the
    structural cause of the flat Exp18 curve). Ranking the seed by recall
    fingerprint similarity instead mirrors the tier-2 ``retrieve_seed_pairs``
    and lets a recurring chain reuse its own prior repair point.

    ``memory_texts`` keys retrieval by the recall content fingerprint
    (paraphrase-invariant) instead of query keywords when supplied.
    """
    if len(active_priors) == 0:
        return [], 0

    near = active_priors.retrieve(
        query=query, top_k=neighbors, memory_texts=memory_texts
    )
    if not near:
        return [], 0

    pairs: list[tuple[int, str]] = []
    seen = set()
    for record in near:  # already ordered nearest-first by fingerprint similarity
        key = getattr(record, "key", None)
        if key is None:
            continue
        gen_point = key.hop_index - 1
        if not (0 <= gen_point < max_depth):
            continue
        pair = (gen_point, record.error_type)
        if pair in seen:
            continue
        pairs.append(pair)
        seen.add(pair)
        if len(pairs) >= topk:
            break
    return pairs, len(near)


def _pairs_to_choices(
    pairs: list[tuple[int, str]],
    recall_set,
    max_depth: int,
) -> list[tuple[int, PipelineAction]]:
    choices = []
    seen = set()
    for gp, action_str in pairs:
        action = LABEL_TO_ACTION.get(action_str)
        if action is None or action == PipelineAction.IDENTITY:
            continue
        if not (0 <= gp < max_depth):
            continue
        if action not in get_legal_actions(recall_set, gp):
            continue
        choice = (gp, action)
        if choice not in seen:
            choices.append(choice)
            seen.add(choice)
    return choices


def _run_seed_stage(
    seed_choices: list[tuple[int, PipelineAction]],
    runner,
    baseline_gain: float,
    *,
    recovered_threshold: float,
) -> dict[str, object]:
    best_net = 0.0
    best_choice = None
    recovered_choice = None
    seed_rank_recovered = 0
    rollouts_used = 0
    candidates = []

    for rank, choice in enumerate(seed_choices, start=1):
        rollouts_used += 1
        res = runner(choice)
        net = res.recovery_gain - baseline_gain
        candidates.append((net, choice))
        if net > recovered_threshold:
            recovered_choice = choice
            seed_rank_recovered = rank
            best_net = net
            break

    if recovered_choice is None and candidates:
        best_net, best_choice = best_scored_pair(candidates)
        if best_choice is None:
            best_net = float("nan")

    return {
        "best_net_gain": best_net,
        "best_choice": best_choice,
        "recovered_choice": recovered_choice,
        "seed_rank_recovered": seed_rank_recovered,
        "seed_rollouts_used": rollouts_used,
        "timeout_count": recovery_timeout_count(
            score for score, _choice in candidates
        ),
    }


def _fallback_rollout_budget(recall_set) -> int:
    """Approximate exhaustive fallback cost in single-point repair rollouts."""
    total = 1  # identity baseline
    for gp in (0, 1):
        for action in get_legal_actions(recall_set, gp):
            if action != PipelineAction.IDENTITY:
                total += 1
    return total


def _ledger_fields(case, recovered_choice, recovered_net: float, *, recovery_source: str):
    action = recovered_choice[1].value if recovered_choice else case.perturbation_label
    gp_text = "" if recovered_choice is None else str(recovered_choice[0])
    corrected_memory = " | ".join(ev.text for ev in case.gold_evidence)
    wrong_context = case.primary_baseline.injected_context
    cause = (
        f"{recovery_source}: {action} at generation point {gp_text}"
        if gp_text
        else f"{recovery_source}: no recovered active prior"
    )
    repair_guidance = (
        f"Prioritize {action} repairs at generation point {gp_text} "
        "for similar future queries."
        if gp_text
        else "Do not promote this case into the active prior bank."
    )
    return {
        "ledger_error_type": action,
        "ledger_cause": cause,
        "ledger_wrong_context": wrong_context,
        "ledger_original_evidence": corrected_memory,
        "ledger_corrected_memory": corrected_memory,
        "ledger_repair_guidance": repair_guidance,
        "ledger_recovery_gain": format_recovery_value(
            recovered_net, digits=4
        ),
    }


def _format_choice(choice) -> str:
    if choice is None:
        return ""
    gp, action = choice
    action_name = action.value if hasattr(action, "value") else str(action)
    return f"gp{gp}:{action_name}"


def _format_choices(choices) -> str:
    return "|".join(_format_choice(choice) for choice in choices)


def _excluded_detail_row(case_index, case, *, prior_total_count: int):
    """Stream row whose identity-backbone score is unmeasured."""
    return {
        "case_index": str(case_index),
        "case_id": case.case_id,
        "gold_label": case.perturbation_label,
        "status": "base_gain_timeout",
        "excluded": "true",
        "timeout_count": "1",
        "prior_total_count": str(prior_total_count),
        "recovered": "false",
        "ledger_written": "false",
        "active_prior_written": "false",
    }


def _detail_fieldnames() -> list[str]:
    return [
        "case_index",
        "case_id",
        "gold_label",
        "status",
        "excluded",
        "timeout_count",
        "prior_total_count",
        "prior_source_count",
        "seed_choices",
        "seed_rank_recovered",
        "seed_selected_choice",
        "seed_best_net_gain",
        "seed_rollouts_used",
        "fallback_choice",
        "fallback_action",
        "fallback_net_gain",
        "fallback_rollouts_used",
        "total_rollouts_used",
        "rollouts_used_to_recovery",
        "best_net_gain",
        "recovered",
        "recovery_source",
        "ledger_written",
        "active_prior_written",
        "ledger_error_type",
        "ledger_cause",
        "ledger_wrong_context",
        "ledger_original_evidence",
        "ledger_corrected_memory",
        "ledger_repair_guidance",
        "ledger_recovery_gain",
    ]


def _summary_rows(rows: list[dict[str, str]], *, bin_size: int) -> list[dict[str, str]]:
    rows = [
        row for row in rows
        if row.get("excluded", "false") != "true"
    ]
    if not rows:
        return []
    out = []
    for start in range(0, len(rows), bin_size):
        chunk = rows[start:start + bin_size]
        recovered = [r for r in chunk if r["recovered"] == "true"]
        seed_recovered = [r for r in chunk if int(r["seed_rank_recovered"]) > 0]
        out.append({
            "bin_start": chunk[0]["case_index"],
            "bin_end": chunk[-1]["case_index"],
            "cases": str(len(chunk)),
            "recovered": str(len(recovered)),
            "recovery_rate": _rate(len(recovered), len(chunk)),
            "seed_recovered": str(len(seed_recovered)),
            "seed_recovery_rate": _rate(len(seed_recovered), len(chunk)),
            "avg_prior_source_count": _avg(chunk, "prior_source_count"),
            "avg_seed_rollouts_used": _avg(chunk, "seed_rollouts_used"),
            "avg_fallback_rollouts_used": _avg(chunk, "fallback_rollouts_used"),
            "avg_total_rollouts_used": _avg(chunk, "total_rollouts_used"),
            "avg_rollouts_to_recovery": _avg(
                recovered, "rollouts_used_to_recovery"
            ) if recovered else "0.0000",
            "active_prior_written": str(
                sum(1 for r in chunk if r["active_prior_written"] == "true")
            ),
        })
    return out


def _summary_fieldnames() -> list[str]:
    return [
        "bin_start",
        "bin_end",
        "cases",
        "recovered",
        "recovery_rate",
        "seed_recovered",
        "seed_recovery_rate",
        "avg_prior_source_count",
        "avg_seed_rollouts_used",
        "avg_fallback_rollouts_used",
        "avg_total_rollouts_used",
        "avg_rollouts_to_recovery",
        "active_prior_written",
    ]


def _rate(num: int, den: int) -> str:
    return f"{(num / den if den else 0.0):.4f}"


def _avg(rows: list[dict[str, str]], key: str) -> str:
    if not rows:
        return "0.0000"
    return f"{(sum(float(r[key]) for r in rows) / len(rows)):.4f}"


def _print_summary(rows: list[dict[str, str]]) -> None:
    print("\n=== FailureMemory trajectory summary ===")
    print(
        f"{'bin':>9s} {'recov':>9s} {'seed_rec':>9s} "
        f"{'avg_total':>10s} {'avg_to_rec':>10s} {'priors':>8s}"
    )
    for row in rows:
        bin_label = f"{row['bin_start']}-{row['bin_end']}"
        print(
            f"{bin_label:>9s} {row['recovery_rate']:>9s} "
            f"{row['seed_recovery_rate']:>9s} "
            f"{row['avg_total_rollouts_used']:>10s} "
            f"{row['avg_rollouts_to_recovery']:>10s} "
            f"{row['active_prior_written']:>8s}"
        )


if __name__ == "__main__":
    main()
