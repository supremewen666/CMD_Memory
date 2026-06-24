#!/usr/bin/env python3
"""Experiment 19: two-tier FailureMemory skill abstraction.

Prequential stream test for the Hermes-style second tier:
recovered step-action cases are first written as concrete markdown cases; once
an action has enough recovered cases, a reusable pattern is formatted,
validated, written to markdown, and used as a later ``(hop, action)`` seed.

Run after vLLM is up:
    python -m experiments.run_experiment_19_skill_abstraction
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.labels import PIPELINE_STEP_ACTIONS
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.data_io import load_probe_cases
from cmd_audit.eval.writers import write_csv_table
from cmd_audit.counterfactual.actions import PipelineAction, get_legal_actions
from cmd_audit.repair.efficacy import LABEL_TO_ACTION, run_single_repair
from cmd_audit.repair.failure_memory import (
    FailureMemorySkillLoop,
    FailureMemoryStore,
    MarkdownFailureMemoryStore,
    StepLevelRecord,
)
from experiments.experiment_runner_common import (
    DATA,
    OUT,
    assert_g_eval_available,
    build_answer_verifier,
)
from experiments.probe_exhaustive import _evaluate_case
from experiments.run_experiment_18_failure_memory_trajectory import (
    _fallback_rollout_budget,
    _format_choice,
    _format_choices,
    _retrieve_seed_pairs as _retrieve_store_seed_pairs,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "real_multihop_cases.json"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--topk", type=int, default=2)
    parser.add_argument("--neighbors", type=int, default=10)
    parser.add_argument("--bin-size", type=int, default=15)
    parser.add_argument("--pattern-threshold", type=int, default=3)
    parser.add_argument("--min-credit", type=float, default=0.05)
    parser.add_argument("--recovered-threshold", type=float, default=0.1)
    args = parser.parse_args()

    client = LLMClient(LLMClientConfig())
    assert_g_eval_available(client, role="skill-abstraction")
    verifier = build_answer_verifier(client, answer_mode="answer-rubric")

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
    markdown_store = MarkdownFailureMemoryStore(OUT / "failure_memory_skill")
    skill_loop = FailureMemorySkillLoop(
        markdown_store,
        threshold=args.pattern_threshold,
    )
    detail_rows: list[dict[str, str]] = []

    print(
        "FailureMemory skill abstraction: "
        f"{len(cases)} cases, pattern_threshold={args.pattern_threshold}\n"
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

        pattern_pairs, pattern_source_count = skill_loop.retrieve_seed_pairs(
            case.query,
            max_depth=max_depth,
            top_k=args.topk,
            memory_texts=memory_texts,
        )
        pattern_count_before = skill_loop.pattern_count
        store_pairs, store_source_count = _retrieve_store_seed_pairs(
            active_priors,
            case.query,
            max_depth=max_depth,
            topk=args.topk,
            neighbors=args.neighbors,
            memory_texts=memory_texts,
        )
        seed_pairs = _merge_pairs(pattern_pairs, store_pairs, top_k=args.topk)
        source_by_pair = {
            **{pair: "store" for pair in store_pairs},
            **{pair: "pattern" for pair in pattern_pairs},
        }
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
            source_by_pair=source_by_pair,
            recovered_threshold=args.recovered_threshold,
        )

        recovered_choice = seed_result["recovered_choice"]
        recovered_net = float(seed_result["best_net_gain"])
        recovery_source = str(seed_result["recovery_source"])
        fallback_choice = None
        fallback_net = 0.0
        fallback_rollouts = 0

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
                if fallback_net > recovered_net:
                    recovered_net = fallback_net
                if fallback_net > args.recovered_threshold:
                    recovered_choice = candidate
                    recovery_source = "fallback"

        recovered = recovered_choice is not None and recovered_net > args.recovered_threshold
        active_prior_written = False
        pattern_written = False
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
            )
            active_priors.add_if_recovered(record, "recovered")
            active_prior_written = True
            pattern_record = skill_loop.record_recovered_case(
                case_id=case.case_id,
                query=case.query,
                hop_index=gp + 1,
                label=action.value,
                cause=record.cause,
                corrected_memory=record.corrected_memory,
                repair_guidance=record.repair_guidance,
                retrieved_items=tuple(item.text for item in recall_set),
                memory_texts=memory_texts,
                recovery_gain=recovered_net,
            )
            pattern_written = pattern_record is not None

        seed_rollouts = int(seed_result["seed_rollouts_used"])
        total_rollouts = seed_rollouts + fallback_rollouts
        detail_rows.append({
            "case_index": str(case_index),
            "case_id": case.case_id,
            "gold_label": case.perturbation_label,
            "prior_total_count": str(len(active_priors) - int(active_prior_written)),
            "pattern_count": str(pattern_count_before),
            "pattern_source_count": str(pattern_source_count),
            "store_source_count": str(store_source_count),
            "seed_choices": _format_choices(seed_choices),
            "seed_sources": "|".join(
                source_by_pair.get((gp, action.value), "")
                for gp, action in seed_choices
            ),
            "seed_rank_recovered": str(seed_result["seed_rank_recovered"]),
            "seed_selected_choice": _format_choice(seed_result["recovered_choice"]),
            "seed_recovery_source": recovery_source if recovered_choice else "",
            "seed_best_net_gain": f"{float(seed_result['best_net_gain']):.4f}",
            "seed_rollouts_used": str(seed_rollouts),
            "fallback_choice": _format_choice(fallback_choice),
            "fallback_net_gain": f"{fallback_net:.4f}",
            "fallback_rollouts_used": str(fallback_rollouts),
            "total_rollouts_used": str(total_rollouts),
            "best_net_gain": f"{recovered_net:.4f}",
            "recovered": str(recovered).lower(),
            "recovery_source": recovery_source if recovered else "",
            "active_prior_written": str(active_prior_written).lower(),
            "pattern_written": str(pattern_written).lower(),
        })
        print(
            f"  [{case_index}/{len(cases)}] {case.perturbation_label:20s} "
            f"patterns={pattern_source_count:02d} store={store_source_count:02d} "
            f"seeds={_format_choices(seed_choices) or '-'} "
            f"recovered={str(recovered).lower()} source={recovery_source or '-'}"
        )

    detail_path = write_csv_table(
        OUT / "skill_abstraction_detail.csv",
        _detail_fieldnames(),
        detail_rows,
        sandbox_root=OUT,
    )
    summary_rows = _summary_rows(detail_rows, bin_size=args.bin_size)
    summary_path = write_csv_table(
        OUT / "skill_abstraction_summary.csv",
        _summary_fieldnames(),
        summary_rows,
        sandbox_root=OUT,
    )

    _print_summary(summary_rows)
    print(f"\nWrote {detail_path}")
    print(f"Wrote {summary_path}")


def _merge_pairs(
    pattern_pairs: list[tuple[int, str]],
    store_pairs: list[tuple[int, str]],
    *,
    top_k: int,
) -> list[tuple[int, str]]:
    pairs: list[tuple[int, str]] = []
    seen = set()
    for pair in [*pattern_pairs, *store_pairs]:
        if pair in seen:
            continue
        pairs.append(pair)
        seen.add(pair)
        if len(pairs) >= top_k:
            break
    return pairs


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
    source_by_pair: dict[tuple[int, str], str],
    recovered_threshold: float,
) -> dict[str, object]:
    best_net = 0.0
    best_choice = None
    recovered_choice = None
    seed_rank_recovered = 0
    rollouts_used = 0
    recovery_source = ""

    for rank, choice in enumerate(seed_choices, start=1):
        rollouts_used += 1
        res = runner(choice)
        net = res.recovery_gain - baseline_gain
        if best_choice is None or net > best_net:
            best_net = net
            best_choice = choice
        if net > recovered_threshold:
            recovered_choice = choice
            seed_rank_recovered = rank
            best_net = net
            recovery_source = source_by_pair.get((choice[0], choice[1].value), "seed")
            break

    return {
        "best_net_gain": best_net,
        "best_choice": best_choice,
        "recovered_choice": recovered_choice,
        "seed_rank_recovered": seed_rank_recovered,
        "seed_rollouts_used": rollouts_used,
        "recovery_source": recovery_source,
    }


def _detail_fieldnames() -> list[str]:
    return [
        "case_index",
        "case_id",
        "gold_label",
        "prior_total_count",
        "pattern_count",
        "pattern_source_count",
        "store_source_count",
        "seed_choices",
        "seed_sources",
        "seed_rank_recovered",
        "seed_selected_choice",
        "seed_recovery_source",
        "seed_best_net_gain",
        "seed_rollouts_used",
        "fallback_choice",
        "fallback_net_gain",
        "fallback_rollouts_used",
        "total_rollouts_used",
        "best_net_gain",
        "recovered",
        "recovery_source",
        "active_prior_written",
        "pattern_written",
    ]


def _summary_rows(rows: list[dict[str, str]], *, bin_size: int) -> list[dict[str, str]]:
    if not rows:
        return []
    out = []
    for start in range(0, len(rows), bin_size):
        chunk = rows[start:start + bin_size]
        recovered = [r for r in chunk if r["recovered"] == "true"]
        pattern_seed = [r for r in chunk if r["recovery_source"] == "pattern"]
        out.append({
            "bin_start": chunk[0]["case_index"],
            "bin_end": chunk[-1]["case_index"],
            "cases": str(len(chunk)),
            "recovered": str(len(recovered)),
            "recovery_rate": _rate(len(recovered), len(chunk)),
            "pattern_seed_recovered": str(len(pattern_seed)),
            "pattern_seed_recovery_rate": _rate(len(pattern_seed), len(chunk)),
            "avg_pattern_source_count": _avg(chunk, "pattern_source_count"),
            "avg_store_source_count": _avg(chunk, "store_source_count"),
            "avg_total_rollouts_used": _avg(chunk, "total_rollouts_used"),
            "patterns_written": str(
                sum(1 for r in chunk if r["pattern_written"] == "true")
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
        "pattern_seed_recovered",
        "pattern_seed_recovery_rate",
        "avg_pattern_source_count",
        "avg_store_source_count",
        "avg_total_rollouts_used",
        "patterns_written",
    ]


def _rate(num: int, den: int) -> str:
    return f"{(num / den if den else 0.0):.4f}"


def _avg(rows: list[dict[str, str]], key: str) -> str:
    if not rows:
        return "0.0000"
    return f"{(sum(float(r[key]) for r in rows) / len(rows)):.4f}"


def _print_summary(rows: list[dict[str, str]]) -> None:
    print("\n=== Skill abstraction summary ===")
    print(
        f"{'bin':>9s} {'recov':>9s} {'pattern':>9s} "
        f"{'avg_patterns':>12s} {'avg_store':>10s} {'written':>8s}"
    )
    for row in rows:
        bin_label = f"{row['bin_start']}-{row['bin_end']}"
        print(
            f"{bin_label:>9s} {row['recovery_rate']:>9s} "
            f"{row['pattern_seed_recovery_rate']:>9s} "
            f"{row['avg_pattern_source_count']:>12s} "
            f"{row['avg_store_source_count']:>10s} "
            f"{row['patterns_written']:>8s}"
        )


if __name__ == "__main__":
    main()
