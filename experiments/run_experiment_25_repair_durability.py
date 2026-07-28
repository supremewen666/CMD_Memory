#!/usr/bin/env python3
"""Experiment 25: repair durability -- read-time vs durable state.

The live repair path ``apply_pipeline_action`` is a PURE function: it builds a
repaired context and never touches the store, so every recurrence of the same
query family pays the full repair cost again (read-time repair). The adapter
path can instead persist the correction (write-back repair). Their relapse rate,
amortized cost, and regression risk have never been measured, yet that
comparison is what underwrites the deployment story (store-level repair) and the
skill mode's "how long does one repair last" question.

Three arms over `real_recurrent_cases.json` (120 families x 5 variants):

  no-repair   control; never repairs.
  read-time   repairs every variant; the store is never mutated.
  write-back  persists an action-specific rule after governed cluster replay;
              later variants materialize repaired retrieval/pipeline state.

Metrics:
  relapse rate    after a family's first accepted repair, the share of its
                  later variants that still fail.
  amortized cost  rollouts per variant by ``recurrent_variant_index``; the
                  write-back arm should trend down within a family.
  net regression  write-back's effect on OTHER families. Reported as NET, not
                  repair-on-failure: a write-back that fixes its own family
                  while degrading others is a net loss, and only the net number
                  shows it.

Repair construction is gold-free. This offline experiment uses the gold answer
verifier for candidate selection and governance replay, and records that oracle
admission mode explicitly; it does not claim a deployable gold-free selector.
Timeouts are NaN and a NaN identity baseline excludes the case from every arm.
The durable store is snapshotted and actually restored after each family.

Run after vLLM is up:
    python -m experiments.run_experiment_25_repair_durability \
        --out artifacts/sandbox/repair_durability_detail.csv
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.counterfactual.actions import (  # noqa: E402
    PipelineAction,
    get_legal_actions,
)
from cmd_audit.counterfactual.operators import OperatorSpec  # noqa: E402
from cmd_audit.data_io import load_probe_cases  # noqa: E402
from cmd_audit.eval.writers import (  # noqa: E402
    format_recovery_value,
    is_timeout_value,
    write_csv_table,
)
from cmd_audit.repair.efficacy import run_single_repair  # noqa: E402
from cmd_audit.repair.durable_store import DurableRepairStore  # noqa: E402
from cmd_audit.repair.failure_memory import _memory_fingerprint  # noqa: E402
from cmd_audit.repair.governance import OperatorGovernance  # noqa: E402
from experiments.experiment_runner_common import (  # noqa: E402
    OUT,
    assert_g_eval_available,
    build_answer_verifier,
    build_clients,
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "probe_cases"

ARMS = ("no_repair", "read_time", "write_back")


def _family_key(case: Any) -> str:
    """Group by the recurrent family, falling back to the content fingerprint.

    Query-word keys are deliberately NOT used: C7 established they cannot lock
    chain identity, which is why the fingerprint is the retrieval key elsewhere.
    """
    family = getattr(case, "recurrent_family_id", None)
    if family:
        return str(family)
    from cmd_audit.repair.failure_memory import _memory_fingerprint

    return _memory_fingerprint(tuple(i.text for i in case.extracted_memory))


def _variant_index(case: Any, fallback: int) -> int:
    value = getattr(case, "recurrent_variant_index", None)
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _candidate_choices(
    recall: tuple[Any, ...],
    max_depth: int,
    intervention_config: dict[str, Any] | None = None,
) -> list[tuple[int, PipelineAction]]:
    """Gold-free, structurally legal repair candidates.

    The label is never consulted -- selection is decided by recovery, per the
    standing rule that labels stay out of the diagnostic loop.
    """
    choices: list[tuple[int, PipelineAction]] = []
    for generation_point in range(max_depth):
        legal = get_legal_actions(
            recall,
            generation_point,
            intervention_config=intervention_config,
        )
        choices.extend(
            (generation_point, action)
            for action in legal
            if action != PipelineAction.IDENTITY and not action.is_item_level
        )
    return choices


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "real_recurrent_cases.json"))
    parser.add_argument(
        "--out",
        default=str(OUT / "repair_durability_detail.csv"),
        help="Explicit output path (never hardcoded; batch runs need per-run files).",
    )
    parser.add_argument("--seed", type=int, default=25)
    parser.add_argument("--limit", type=int, default=0, help="Cap families; 0 = all.")
    parser.add_argument("--recovered-threshold", type=float, default=0.1)
    parser.add_argument(
        "--replay-k",
        type=int,
        default=5,
        help="Same-family cases used by the A4 admission replay.",
    )
    parser.add_argument(
        "--sentinel-count",
        type=int,
        default=8,
        help="Other-family sentinels evaluated after each accepted write-back.",
    )
    parser.add_argument(
        "--fingerprint-threshold",
        type=float,
        default=0.8,
        help="Minimum recall-fingerprint Jaccard match for durable rule reuse.",
    )
    args = parser.parse_args()

    answer_client, judge_client = build_clients()
    assert_g_eval_available(judge_client, role="exp25-judge")
    verifier = build_answer_verifier(judge_client)

    from cmd_audit.harness import _initial_mcts_context, _retrieved_memory_items

    cases = list(load_probe_cases(args.cases))
    families: dict[str, list[Any]] = defaultdict(list)
    for position, case in enumerate(cases):
        families[_family_key(case)].append(case)
    ordered_families = sorted(families)
    random.Random(args.seed).shuffle(ordered_families)
    if args.limit:
        ordered_families = ordered_families[: args.limit]

    print(
        f"Repair durability: {len(ordered_families)} families, "
        f"{sum(len(families[f]) for f in ordered_families)} variants, "
        f"arms={'/'.join(ARMS)}\n"
    )

    detail_rows: list[dict[str, str]] = []
    durable_store = DurableRepairStore(
        similarity_threshold=args.fingerprint_threshold
    )
    governance = OperatorGovernance(seed=args.seed)

    def score_case(
        target_case: Any,
        choice: tuple[int, PipelineAction] | None,
        *,
        items: tuple[Any, ...] | None = None,
        context: str | None = None,
    ) -> float:
        target_recall = (
            items if items is not None else _retrieved_memory_items(target_case)
        )
        target_context = (
            context
            if context is not None
            else _initial_mcts_context(target_case, target_recall)
        )
        target_depth = max(1, min(3, len(target_recall) or 1))
        target_config = {
            "candidate_items": target_case.extracted_memory,
            "raw_events": target_case.raw_events,
        }
        return run_single_repair(
            target_case,
            choice,
            client=answer_client,
            answer_verifier=verifier,
            base_context=target_context,
            recall_set=target_recall,
            max_depth=target_depth,
            intervention_config=target_config,
        ).recovery_gain

    def cluster_replay_gains(
        variants: list[Any],
        choice: tuple[int, PipelineAction],
    ) -> tuple[float, ...]:
        gains: list[float] = []
        for replay_case in variants[: max(1, args.replay_k)]:
            replay_recall = _retrieved_memory_items(replay_case)
            replay_depth = max(1, min(3, len(replay_recall) or 1))
            replay_config = {
                "candidate_items": replay_case.extracted_memory,
                "raw_events": replay_case.raw_events,
            }
            if choice not in _candidate_choices(
                replay_recall, replay_depth, replay_config
            ):
                continue
            identity = score_case(replay_case, None)
            repaired = score_case(replay_case, choice)
            if is_timeout_value(identity) or is_timeout_value(repaired):
                continue
            gains.append(repaired - identity)
        return tuple(gains)

    def evaluate_sentinels(current_family: str) -> tuple[int, int, int]:
        if args.sentinel_count <= 0:
            return 0, 0, 0
        helped = hurt = measured = 0
        for other_family in ordered_families:
            if other_family == current_family:
                continue
            sentinel = min(
                families[other_family],
                key=lambda item: _variant_index(item, 0),
            )
            recall = _retrieved_memory_items(sentinel)
            base_context = _initial_mcts_context(sentinel, recall)
            before = score_case(sentinel, None, items=recall, context=base_context)
            if is_timeout_value(before):
                continue
            materialized = durable_store.materialize(
                sentinel,
                recall,
                base_context=base_context,
            )
            after = (
                score_case(
                    sentinel,
                    None,
                    items=materialized.items,
                    context=materialized.context,
                )
                if materialized.changed
                else before
            )
            if is_timeout_value(after):
                continue
            measured += 1
            delta = after - before
            if delta >= args.recovered_threshold:
                helped += 1
            elif delta <= -args.recovered_threshold:
                hurt += 1
            if measured >= max(0, args.sentinel_count):
                break
        return helped, hurt, measured

    for family_index, family in enumerate(ordered_families, start=1):
        variants = sorted(
            families[family],
            key=lambda c: _variant_index(c, 0),
        )
        snapshot = durable_store.snapshot()
        first_repair_done = False
        family_row_start = len(detail_rows)

        for order, case in enumerate(variants):
            recall = _retrieved_memory_items(case)
            max_depth = max(1, min(3, len(recall) or 1))
            config = {
                "candidate_items": case.extracted_memory,
                "raw_events": case.raw_events,
            }

            def repair(
                choice: tuple[int, PipelineAction] | None,
                *,
                items: tuple[Any, ...],
                context: str | None = None,
            ) -> float:
                return score_case(
                    case,
                    choice,
                    items=items,
                    context=context,
                )

            base_gain = repair(None, items=recall)
            variant_index = _variant_index(case, order)

            if is_timeout_value(base_gain):
                # A NaN identity baseline makes every arm's net NaN, so this
                # case is unmeasured in ALL arms -- never a failure in any.
                detail_rows.append(
                    _excluded_row(family, family_index, case, variant_index)
                )
                print(
                    f"  [{family_index}] {family} v{variant_index} "
                    "EXCLUDED (identity rollout timed out)"
                )
                continue

            timeouts = 0

            def net(score: float) -> float:
                nonlocal timeouts
                if is_timeout_value(score):
                    timeouts += 1
                    return float("nan")
                return score - base_gain

            # --- read-time arm: full search every variant, store untouched ---
            read_net = 0.0
            read_choice: tuple[int, PipelineAction] | None = None
            read_rollouts = 0
            for choice in _candidate_choices(recall, max_depth, config):
                read_rollouts += 1
                gain = net(repair(choice, items=recall))
                if is_timeout_value(gain):
                    continue
                if gain > read_net:
                    read_net, read_choice = gain, choice
                if gain >= args.recovered_threshold:
                    break
            read_recovered = (
                read_choice is not None and read_net >= args.recovered_threshold
            )
            assert durable_store.matches_snapshot(snapshot), (
                "read-time arm mutated the durable store"
            )

            # --- write-back arm: governed admission, then durable materialization ---
            write_rollouts = 0
            write_net = 0.0
            write_recovered = False
            governance_reason = ""
            governance_low_evidence = ""
            governance_ci_lower = ""
            operator_hash = ""
            sentinel_helped = sentinel_hurt = sentinel_measured = 0
            if first_repair_done:
                write_rollouts = 1
                base_context = _initial_mcts_context(case, recall)
                materialized = durable_store.materialize(
                    case,
                    recall,
                    base_context=base_context,
                )
                gain = net(
                    repair(
                        None,
                        items=materialized.items,
                        context=materialized.context,
                    )
                )
                if not is_timeout_value(gain):
                    write_net = gain
                    write_recovered = gain >= args.recovered_threshold
            else:
                write_rollouts = read_rollouts
                write_net = read_net
                write_recovered = read_recovered
                if read_recovered and read_choice is not None:
                    operator = OperatorSpec.single(*read_choice)
                    fingerprint = _memory_fingerprint(
                        tuple(item.text for item in recall)
                    )
                    replay_gains = cluster_replay_gains(variants, read_choice)
                    decision = governance.admit_with_cluster_replay(
                        fingerprint,
                        operator,
                        replay_gains,
                        generation=family_index,
                    )
                    governance_reason = decision.reason
                    governance_low_evidence = str(
                        decision.low_evidence
                    ).lower()
                    governance_ci_lower = (
                        ""
                        if decision.ci_lower is None
                        else format_recovery_value(decision.ci_lower, digits=4)
                    )
                    operator_hash = decision.operator_hash
                    if decision.admitted:
                        first_repair_done = durable_store.write_back(
                            fingerprint=fingerprint,
                            operator=operator,
                            source_family=family,
                        )
                        if first_repair_done:
                            (
                                sentinel_helped,
                                sentinel_hurt,
                                sentinel_measured,
                            ) = evaluate_sentinels(family)

            detail_rows.append(
                {
                    "family": family,
                    "family_index": str(family_index),
                    "case_id": case.case_id,
                    "gold_label": case.perturbation_label or "",
                    "variant_index": str(variant_index),
                    "status": "ok",
                    "excluded": "false",
                    "timeout_count": str(timeouts),
                    "base_gain": format_recovery_value(base_gain, digits=4),
                    "no_repair_recovered": str(
                        base_gain >= args.recovered_threshold
                    ).lower(),
                    "read_time_recovered": str(read_recovered).lower(),
                    "read_time_net": format_recovery_value(read_net, digits=4),
                    "read_time_rollouts": str(read_rollouts),
                    "write_back_recovered": str(write_recovered).lower(),
                    "write_back_net": format_recovery_value(write_net, digits=4),
                    "write_back_rollouts": str(write_rollouts),
                    "write_back_active": str(first_repair_done).lower(),
                    "admission_mode": "oracle_gold_cluster_replay",
                    "governance_reason": governance_reason,
                    "governance_low_evidence": governance_low_evidence,
                    "governance_ci_lower": governance_ci_lower,
                    "operator_hash": operator_hash,
                    "cross_family_measured": str(sentinel_measured),
                    "cross_family_helped": str(sentinel_helped),
                    "cross_family_hurt": str(sentinel_hurt),
                    "store_rolled_back": "false",
                }
            )
            print(
                f"  [{family_index}] {family} v{variant_index} "
                f"read={'Y' if read_recovered else '.'}(c{read_rollouts}) "
                f"write={'Y' if write_recovered else '.'}(c{write_rollouts})"
            )

        # Restore the actual durable store and record whether rollback succeeded.
        durable_store.restore(snapshot)
        rolled_back = durable_store.matches_snapshot(snapshot)
        for row in detail_rows[family_row_start:]:
            row["store_rolled_back"] = str(rolled_back).lower()
        if not rolled_back:
            raise RuntimeError(f"durable store rollback failed for {family}")

    detail_path = write_csv_table(
        Path(args.out),
        _detail_fieldnames(),
        detail_rows,
        sandbox_root=OUT.parent,
        judge_client=judge_client,
    )
    summary_rows = _summary_rows(detail_rows)
    summary_path = write_csv_table(
        _summary_path(Path(args.out)),
        _summary_fieldnames(),
        summary_rows,
        sandbox_root=OUT.parent,
        judge_client=judge_client,
    )
    _print_summary(summary_rows, detail_rows)
    print(f"\nWrote {detail_path}\nWrote {summary_path}")


def _excluded_row(
    family: str, family_index: int, case: Any, variant_index: int
) -> dict[str, str]:
    return {
        "family": family,
        "family_index": str(family_index),
        "case_id": case.case_id,
        "gold_label": case.perturbation_label or "",
        "variant_index": str(variant_index),
        "status": "base_gain_timeout",
        "excluded": "true",
        "timeout_count": "1",
        "base_gain": "",
        "no_repair_recovered": "",
        "read_time_recovered": "",
        "read_time_net": "",
        "read_time_rollouts": "0",
        "write_back_recovered": "",
        "write_back_net": "",
        "write_back_rollouts": "0",
        "write_back_active": "false",
        "admission_mode": "oracle_gold_cluster_replay",
        "governance_reason": "",
        "governance_low_evidence": "",
        "governance_ci_lower": "",
        "operator_hash": "",
        "cross_family_measured": "0",
        "cross_family_helped": "0",
        "cross_family_hurt": "0",
        "store_rolled_back": "true",
    }


def _detail_fieldnames() -> list[str]:
    return [
        "family",
        "family_index",
        "case_id",
        "gold_label",
        "variant_index",
        "status",
        "excluded",
        "timeout_count",
        "base_gain",
        "no_repair_recovered",
        "read_time_recovered",
        "read_time_net",
        "read_time_rollouts",
        "write_back_recovered",
        "write_back_net",
        "write_back_rollouts",
        "write_back_active",
        "admission_mode",
        "governance_reason",
        "governance_low_evidence",
        "governance_ci_lower",
        "operator_hash",
        "cross_family_measured",
        "cross_family_helped",
        "cross_family_hurt",
        "store_rolled_back",
    ]


def _summary_fieldnames() -> list[str]:
    return [
        "variant_index",
        "cases",
        "excluded_cases",
        "no_repair_rate",
        "read_time_rate",
        "write_back_rate",
        "avg_read_time_rollouts",
        "avg_write_back_rollouts",
    ]


def relapse_rate(rows: list[dict[str, str]], arm: str) -> tuple[int, int]:
    """Relapse = a family's post-first-repair variant that still fails.

    Computed strictly WITHIN a family: comparing across families would measure
    case difficulty, not durability.
    """
    by_family: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row.get("excluded") == "true":
            continue
        by_family[row["family"]].append(row)
    relapses = considered = 0
    column = f"{arm}_recovered"
    for family_rows in by_family.values():
        ordered = sorted(family_rows, key=lambda r: int(r["variant_index"]))
        seen_repair = False
        for row in ordered:
            if seen_repair:
                considered += 1
                if row.get(column) != "true":
                    relapses += 1
            if row.get(column) == "true":
                seen_repair = True
    return relapses, considered


def net_regression(rows: list[dict[str, str]]) -> tuple[int, int]:
    """Write-back's measured effect on other-family sentinels.

    Reporting repair-on-failure alone would hide a write-back that fixes its
    own family while degrading others.
    """
    if any("cross_family_helped" in row for row in rows):
        helped = sum(int(row.get("cross_family_helped") or 0) for row in rows)
        hurt = sum(int(row.get("cross_family_hurt") or 0) for row in rows)
        return helped, hurt

    # Legacy artifact compatibility: older rows had no sentinel columns.
    helped = hurt = 0
    for row in rows:
        if row.get("excluded") == "true":
            continue
        read = row.get("read_time_recovered") == "true"
        write = row.get("write_back_recovered") == "true"
        if write and not read:
            helped += 1
        elif read and not write:
            hurt += 1
    return helped, hurt


def _summary_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    by_variant: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_variant[int(row["variant_index"])].append(row)
    summaries: list[dict[str, str]] = []
    for variant in sorted(by_variant):
        chunk = by_variant[variant]
        included = [r for r in chunk if r.get("excluded") != "true"]
        excluded = [r for r in chunk if r.get("excluded") == "true"]
        n = len(included)
        summaries.append(
            {
                "variant_index": str(variant),
                "cases": str(n),
                "excluded_cases": str(len(excluded)),
                "no_repair_rate": _rate(included, "no_repair_recovered", n),
                "read_time_rate": _rate(included, "read_time_recovered", n),
                "write_back_rate": _rate(included, "write_back_recovered", n),
                "avg_read_time_rollouts": _avg(included, "read_time_rollouts"),
                "avg_write_back_rollouts": _avg(included, "write_back_rollouts"),
            }
        )
    return summaries


def _rate(rows: list[dict[str, str]], column: str, n: int) -> str:
    if not n:
        return ""
    return f"{sum(1 for r in rows if r.get(column) == 'true') / n:.4f}"


def _avg(rows: list[dict[str, str]], column: str) -> str:
    values = [float(r[column]) for r in rows if (r.get(column) or "").strip()]
    if not values:
        return ""
    return f"{sum(values) / len(values):.4f}"


def _summary_path(detail: Path) -> Path:
    return detail.with_name(detail.stem.replace("detail", "summary") + ".csv")


def _print_summary(
    summary: list[dict[str, str]], detail: list[dict[str, str]]
) -> None:
    print("\n=== Repair durability summary (by variant index) ===")
    print(
        f"{'v':>3s} {'n':>4s} {'no_rep':>7s} {'read':>7s} {'write':>7s} "
        f"{'c_read':>7s} {'c_write':>8s}"
    )
    for row in summary:
        print(
            f"{row['variant_index']:>3s} {row['cases']:>4s} "
            f"{row['no_repair_rate'] or '-':>7s} "
            f"{row['read_time_rate'] or '-':>7s} "
            f"{row['write_back_rate'] or '-':>7s} "
            f"{row['avg_read_time_rollouts'] or '-':>7s} "
            f"{row['avg_write_back_rollouts'] or '-':>8s}"
        )
    for arm in ("read_time", "write_back"):
        relapses, considered = relapse_rate(detail, arm)
        rate = f"{relapses / considered:.4f}" if considered else "n/a"
        print(f"relapse {arm:11s} {relapses}/{considered} = {rate}")
    helped, hurt = net_regression(detail)
    print(
        f"write-back NET: helped={helped} hurt={hurt} net={helped - hurt:+d}"
    )
    print(
        "Report NET, not repair-on-failure: a write-back that fixes its own "
        "family while degrading others is a net loss."
    )


if __name__ == "__main__":
    main()
