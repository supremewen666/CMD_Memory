#!/usr/bin/env python3
"""Experiment 24: online operator trajectory -- the evolution gate.

Exp21 (operator headroom) and Exp22 (operator transfer) are static tests.  This
runner closes the remaining causal gap by running an initially empty operator
library prequentially over the Exp21 residual stream:

1. Retrieve top-N operator shapes learned from earlier cases by the
   C7-validated recall-content fingerprint.
2. Execute them in fingerprint-similarity / recovery-track order and accept the
   first operator whose net gain exceeds ``--recovered-threshold``.
3. If no library operator recovers, run the Exp21-style richer discovery scan.
4. Add each accepted shape to the online library.  A fingerprint may retain
   multiple distinct shapes; the library never collapses a cluster to one
   template.

The stream order is part of the experiment, so cases are intentionally
sequential.  Run at least three different ``--seed`` values before making the
Gate 2 decision.

Gold is used only by the answer verifier.  Operator construction reads recall
content, candidate memory, and raw events, never ``case.gold_*``.

Prerequisite:
    artifacts/sandbox/operator_headroom_detail.csv (Exp21 residual case ids)

Smoke:
    python -m experiments.run_experiment_24_operator_trajectory \
        --limit 8 --fallback-classes single
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.labels import PIPELINE_STEP_ACTIONS
from cmd_audit.counterfactual.actions import PipelineAction, get_legal_actions
from cmd_audit.counterfactual.operators import OperatorSpec
from cmd_audit.data_io import load_probe_cases
from cmd_audit.eval.writers import (
    format_recovery_value,
    is_timeout_value,
    write_csv_table,
)
from cmd_audit.repair.failure_memory import (
    _memory_fingerprint,
    _query_signature_similarity,
)
from experiments.experiment_runner_common import (
    DATA,
    OUT,
    assert_g_eval_available,
    build_answer_verifier,
    build_clients,
)
from experiments.probe_exhaustive import _own_recovery, _step_context


def _load_residual_ids(bank_path: Path) -> set[str]:
    """Return residual case ids from Exp21 without reading its operators."""
    with bank_path.open(newline="", encoding="utf-8") as handle:
        return {
            case_id
            for row in csv.DictReader(handle)
            if (case_id := str(row.get("case_id") or "").strip())
        }


def _legalize(
    spec: OperatorSpec,
    recall: tuple[Any, ...],
    max_depth: int,
    item_pool: set[str],
) -> OperatorSpec | None:
    """Re-ground a stored spec on the current case.

    Steps illegal at the current depth/recall shape and item hints whose ids do
    not exist in the current memory pool are removed.  An operator with no
    remaining structural step is not executable.
    """
    actions = []
    for generation_point, action in sorted(
        spec.action_by_generation_point().items()
    ):
        if (
            0 <= generation_point < max_depth
            and action in get_legal_actions(recall, generation_point)
        ):
            actions.append((generation_point, action))
    if not actions:
        return None
    hints = {
        memory_id: weight
        for memory_id, weight in spec.item_signal_hints_dict().items()
        if memory_id in item_pool
    }
    return OperatorSpec.from_actions(
        actions,
        item_signal_hints=hints or None,
    )


def _retrieve_library(
    library: list[dict[str, Any]],
    fingerprint: str,
    *,
    topn: int,
) -> list[OperatorSpec]:
    """Retrieve distinct shapes by fingerprint, then recovery track record.

    Similarity is the primary key.  Within the same fingerprint similarity,
    shapes with the strongest observed accepted net gain are tried first.  This
    is the minimum explicit track-record ordering required by Spec A2 while
    retaining every distinct shape learned for a cluster.
    """
    if not library or topn <= 0:
        return []
    scored = sorted(
        (
            (
                entry,
                _query_signature_similarity(fingerprint, str(entry["fp"])),
            )
            for entry in library
        ),
        key=lambda item: (item[1], float(item[0].get("net", 0.0))),
        reverse=True,
    )
    specs: list[OperatorSpec] = []
    seen: set[str] = set()
    for entry, similarity in scored:
        if similarity <= 0.0:
            break
        spec = entry["spec"]
        key = spec.format()
        if key in seen:
            continue
        seen.add(key)
        specs.append(spec)
        if len(specs) >= topn:
            break
    return specs


def _random_pool(
    library: list[dict[str, Any]], *, case_id: str
) -> list[OperatorSpec]:
    """Distinct library shapes not contributed by ``case_id`` itself.

    Excluding the case's own shapes keeps the control leave-one-out clean: an
    arm that can replay the shape this case just accepted is contaminated.
    """
    pool: list[OperatorSpec] = []
    seen: set[str] = set()
    for entry in library:
        if str(entry.get("case_id")) == case_id:
            continue
        spec = entry["spec"]
        key = spec.format()
        if key in seen:
            continue
        seen.add(key)
        pool.append(spec)
    return pool


def _sample_random_shapes(
    library: list[dict[str, Any]],
    *,
    budget: int,
    case_id: str,
    rng: random.Random,
) -> list[OperatorSpec]:
    """Draw ``budget`` distinct shapes uniformly from the leave-one-out pool.

    The same-budget random control isolates the fingerprint key from the retry
    budget. Exp22 measured it at 52/115 against fingerprint retrieval's 70/115
    (discordant 26/8, p=2.9e-3) on a fixed pre-built bank, so random selection
    is a real but beatable baseline -- most shapes do NOT repair a given case.

    Caveat this control carries in Exp24 specifically: the library grows from
    empty, so early in the stream ``budget >= len(pool)`` and the "random" arm
    executes the ENTIRE library. Exhaustive-over-library is strictly stronger
    than any ranked top-N, so those comparisons are degenerate rather than
    same-strength. ``_random_coverage`` records budget/pool so the analyzer can
    exclude them; otherwise the control's discriminating power rises along the
    stream and manufactures an apparent "live arm pulls ahead" trend out of
    nothing but pool growth.
    """
    if budget <= 0:
        return []
    pool = _random_pool(library, case_id=case_id)
    if not pool:
        return []
    rng.shuffle(pool)
    return pool[:budget]


def _random_coverage(pool_size: int, budget: int) -> float:
    """Fraction of the pool the random arm could reach. 1.0 == exhaustive."""
    if pool_size <= 0:
        return 0.0
    return min(1.0, budget / pool_size)


def _score_candidates(
    candidates: list[OperatorSpec],
    *,
    execute,
    net_gain,
    recall: tuple[Any, ...],
    max_depth: int,
    item_pool: tuple[str, ...],
) -> list[tuple[OperatorSpec, float]]:
    """Execute every legalizable candidate once and cache ``(spec, net)``.

    Scoring the whole retrieved set lets the live arm and the random-ORDER
    control be derived from one set of executions, so the ordering control costs
    no extra LLM calls. Illegal candidates are dropped, not scored.
    """
    scored: list[tuple[OperatorSpec, float]] = []
    for candidate in candidates:
        grounded = _legalize(candidate, recall, max_depth, set(item_pool))
        if grounded is None:
            continue
        scored.append((grounded, net_gain(execute(grounded))))
    return scored


def _replay_early_stop(
    scored: list[tuple[OperatorSpec, float]], *, threshold: float
) -> tuple[float, OperatorSpec | None, int, int]:
    """Re-derive accept-if-improves over pre-scored candidates.

    Mirrors ``_run_library_stage``'s semantics exactly -- best-so-far tracking,
    NaN skipped, stop at the first candidate clearing ``threshold`` -- but reads
    cached scores instead of paying for executions. ``rollouts`` is what a
    deployment would actually spend (it stops early), not what the experiment
    spent to also score the tail. ``rank`` is the 1-based index within the
    legalized candidates.
    """
    best_net = 0.0
    best_spec: OperatorSpec | None = None
    rollouts = 0
    rank = 0
    for position, (spec, net) in enumerate(scored, start=1):
        rollouts += 1
        if is_timeout_value(net):
            continue
        if net > best_net:
            best_net, best_spec = net, spec
        if net >= threshold:
            rank = position
            break
    return best_net, best_spec, rollouts, rank


def _run_library_stage(
    candidates: list[OperatorSpec],
    *,
    execute,
    net_gain,
    recall: tuple[Any, ...],
    max_depth: int,
    item_pool: tuple[str, ...],
    threshold: float,
) -> tuple[float, OperatorSpec | None, int, int]:
    """Execute retrieved shapes in order, accepting the first that recovers.

    Returns ``(best_net, best_spec, rollouts, rank_recovered)``.  Shared by the
    live arm and both controls so budget accounting and the accept-if-improves
    rule cannot drift between them.
    """
    best_net = 0.0
    best_spec: OperatorSpec | None = None
    rollouts = 0
    rank_recovered = 0
    for rank, candidate in enumerate(candidates, start=1):
        grounded = _legalize(candidate, recall, max_depth, set(item_pool))
        if grounded is None:
            continue
        rollouts += 1
        candidate_net = net_gain(execute(grounded))
        if is_timeout_value(candidate_net):
            continue
        if candidate_net > best_net:
            best_net, best_spec = candidate_net, grounded
        if candidate_net >= threshold:
            rank_recovered = rank
            break
    return best_net, best_spec, rollouts, rank_recovered


def _discovery_scan(
    execute,
    net_gain,
    recall: tuple[Any, ...],
    max_depth: int,
    item_pool: tuple[str, ...],
    *,
    classes: str,
) -> tuple[float, OperatorSpec | None, int]:
    """Run the Exp21-style richer scan.

    Returns ``(best_net_gain, best_spec, rollout_count)``.  Timeout NaNs never
    win the scan; the caller counts them in its per-case timeout ledger.
    """
    rollouts = 0
    best_net = -1.0
    best_spec: OperatorSpec | None = None

    def consider(spec: OperatorSpec) -> float:
        nonlocal rollouts, best_net, best_spec
        rollouts += 1
        gain = net_gain(execute(spec))
        if not is_timeout_value(gain) and gain > best_net:
            best_net, best_spec = gain, spec
        return gain

    single_best = -1.0
    single_spec: OperatorSpec | None = None
    for generation_point in range(max_depth):
        for action in get_legal_actions(recall, generation_point):
            if action == PipelineAction.IDENTITY:
                continue
            spec = OperatorSpec.single(generation_point, action)
            gain = consider(spec)
            if not is_timeout_value(gain) and gain > single_best:
                single_best, single_spec = gain, spec
    if classes == "single":
        return best_net, best_spec, rollouts

    double_best = -1.0
    double_spec: OperatorSpec | None = None
    if max_depth >= 2:
        for first_point in range(max_depth):
            for second_point in range(first_point + 1, max_depth):
                for first_action in get_legal_actions(recall, first_point):
                    if first_action == PipelineAction.IDENTITY:
                        continue
                    for second_action in get_legal_actions(recall, second_point):
                        if second_action == PipelineAction.IDENTITY:
                            continue
                        spec = OperatorSpec.from_actions(
                            (
                                (first_point, first_action),
                                (second_point, second_action),
                            )
                        )
                        gain = consider(spec)
                        if not is_timeout_value(gain) and gain > double_best:
                            double_best, double_spec = gain, spec

    # Parameter scans are deliberately attached only to the best structural
    # single and double shape, matching Exp21's bounded richer scan.
    for base in (single_spec, double_spec):
        if base is None:
            continue
        for memory_id in item_pool:
            for weight in (-1.0, 1.0):
                consider(base.with_item_signal_hint(memory_id, weight))
    return best_net, best_spec, rollouts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        default=str(DATA / "real_multihop_cases.json"),
    )
    parser.add_argument(
        "--residual-from",
        default=str(OUT / "operator_headroom_detail.csv"),
        help="Exp21 detail CSV; only case ids are used (the library starts empty).",
    )
    parser.add_argument(
        "--out",
        default=str(OUT / "operator_trajectory_detail.csv"),
        help="Use a run-specific path for each of the required >=3 shuffle seeds.",
    )
    parser.add_argument("--seed", type=int, default=24)
    parser.add_argument(
        "--topn",
        type=int,
        default=5,
        help="Maximum fingerprint-matched library shapes executed per case.",
    )
    parser.add_argument("--bin-size", type=int, default=15)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--recovered-threshold", type=float, default=0.1)
    parser.add_argument(
        "--fallback-classes",
        choices=("single", "all"),
        default="all",
        help="'all' enables single, double, parameterized, and double+parameter scans.",
    )
    parser.add_argument(
        "--controls",
        choices=("on", "off"),
        default="on",
        help=(
            "Run the fixed-library and same-budget random control arms "
            "alongside the growing library. Required for the Gate 2 verdict: "
            "a climbing live arm is not evidence of evolution unless it also "
            "beats both controls (Exp22 random_topN reached 0.84x oracle)."
        ),
    )
    parser.add_argument(
        "--fixed-library-warmup",
        type=int,
        default=None,
        help=(
            "Cases after which the fixed-library control stops growing "
            "(default: one --bin-size prefix). Isolates library growth from "
            "stream position."
        ),
    )
    args = parser.parse_args()

    if args.bin_size < 1:
        parser.error("--bin-size must be >= 1")
    warmup = (
        args.bin_size
        if args.fixed_library_warmup is None
        else args.fixed_library_warmup
    )
    if warmup < 0:
        parser.error("--fixed-library-warmup must be >= 0")
    controls_on = args.controls == "on"
    if args.topn < 1:
        parser.error("--topn must be >= 1")

    bank_path = Path(args.residual_from)
    if not bank_path.exists():
        raise SystemExit(
            f"residual source not found: {bank_path} (run Exp21 first)."
        )
    residual_ids = _load_residual_ids(bank_path)

    answer_client, judge_client = build_clients()
    assert_g_eval_available(judge_client, role="operator-trajectory-judge")
    verifier = build_answer_verifier(
        judge_client,
        answer_mode="answer-rubric",
    )

    cases = [
        case
        for case in load_probe_cases(args.cases)
        if (
            case.perturbation_label in PIPELINE_STEP_ACTIONS
            and case.case_id in residual_ids
        )
    ]
    random.Random(args.seed).shuffle(cases)
    if args.limit:
        cases = cases[: args.limit]

    from cmd_audit.harness import _initial_mcts_context, _retrieved_memory_items

    # Each entry is one accepted shape.  Exact (fingerprint, shape) duplicates
    # are suppressed, but several distinct shapes may share a fingerprint.
    library: list[dict[str, Any]] = []
    # Each control keeps its OWN library list.  Sharing one mutable library
    # across arms would silently couple them and invalidate every comparison.
    fixed_library: list[dict[str, Any]] = []
    detail_rows: list[dict[str, str]] = []
    control_rng = random.Random(args.seed + 1)
    print(
        f"Operator trajectory: {len(cases)} residual cases, seed={args.seed}, "
        f"topn={args.topn}, fallback={args.fallback_classes}, "
        f"controls={'on' if controls_on else 'off'}"
        + (f", fixed_warmup={warmup}" if controls_on else "")
        + "\n"
    )

    for case_index, case in enumerate(cases, start=1):
        recall = _retrieved_memory_items(case)
        max_depth = max(1, min(3, len(recall) or 1))
        base_context = _initial_mcts_context(case, recall)
        config = {
            "candidate_items": case.extracted_memory,
            "raw_events": case.raw_events,
        }
        fingerprint = _memory_fingerprint(
            tuple(item.text for item in case.extracted_memory)
        )
        item_pool = tuple(
            dict.fromkeys(
                item.memory_id
                for item in recall + tuple(config.get("candidate_items") or ())
            )
        )

        def execute(spec: OperatorSpec) -> float:
            context = base_context
            actions = spec.action_by_generation_point()
            operator_config = spec.intervention_config(config)
            for generation_point in range(max_depth):
                context = _step_context(
                    answer_client,
                    context,
                    actions.get(generation_point, PipelineAction.IDENTITY),
                    recall,
                    generation_point,
                    operator_config,
                )
            return _own_recovery(
                answer_client,
                context,
                max_depth,
                max_depth,
                recall,
                case.gold_answer,
                verifier,
                case.primary_baseline.answer_score,
            )

        base_gain = execute(OperatorSpec())
        library_size_before = len(library)
        generation_bin = ((case_index - 1) // args.bin_size) + 1

        if is_timeout_value(base_gain):
            detail_rows.append(
                _excluded_detail_row(
                    case_index=case_index,
                    generation_bin=generation_bin,
                    case=case,
                    library_size_before=library_size_before,
                )
            )
            print(
                f"  [{case_index}/{len(cases)}] "
                f"{case.perturbation_label:16s} EXCLUDED "
                "(identity-backbone rollout timed out)"
            )
            continue

        timeout_count = 0

        def net_gain(score: float) -> float:
            nonlocal timeout_count
            if is_timeout_value(score):
                timeout_count += 1
                return float("nan")
            return score - base_gain

        # 1. Library stage: only shapes accepted from earlier stream cases.
        # Score the whole retrieved set once so the random-ORDER control below
        # is free; the live arm's reported cost still reflects early stopping.
        candidates = _retrieve_library(library, fingerprint, topn=args.topn)
        scored_candidates = _score_candidates(
            candidates,
            execute=execute,
            net_gain=net_gain,
            recall=recall,
            max_depth=max_depth,
            item_pool=item_pool,
        )
        (
            library_net,
            library_spec,
            library_rollouts,
            library_rank_recovered,
        ) = _replay_early_stop(
            scored_candidates, threshold=args.recovered_threshold
        )

        recovered_spec: OperatorSpec | None = None
        recovered_net = 0.0
        recovery_source = ""
        if (
            library_spec is not None
            and library_net >= args.recovered_threshold
        ):
            recovered_spec = library_spec
            recovered_net = library_net
            recovery_source = "library"

        # 2. Discovery fallback: discover a new shape only after a library miss.
        discovery_net = 0.0
        discovery_rollouts = 0
        if recovered_spec is None:
            (
                discovery_net,
                discovery_spec,
                discovery_rollouts,
            ) = _discovery_scan(
                execute,
                net_gain,
                recall,
                max_depth,
                item_pool,
                classes=args.fallback_classes,
            )
            if (
                discovery_spec is not None
                and not is_timeout_value(discovery_net)
                and discovery_net >= args.recovered_threshold
            ):
                recovered_spec = discovery_spec
                recovered_net = discovery_net
                recovery_source = "discovery"

        # 3. Accept-if-improves and retain multiple shapes per fingerprint.
        library_written = False
        if recovered_spec is not None:
            key = (fingerprint, recovered_spec.format())
            existing = {
                (str(entry["fp"]), entry["spec"].format())
                for entry in library
            }
            if key not in existing:
                library.append(
                    {
                        "fp": fingerprint,
                        "spec": recovered_spec,
                        "net": recovered_net,
                        "case_id": case.case_id,
                    }
                )
                library_written = True

        # 4. Control arms.  Both reuse this case's ``execute`` (same context and
        # same identity baseline) so the only difference is which shapes are
        # tried.  Neither may read or mutate the live ``library``.
        fixed_recovered = ""
        fixed_net_out = ""
        fixed_rollouts = 0
        fixed_size_before = len(fixed_library)
        random_recovered = ""
        random_net_out = ""
        random_rollouts = 0
        random_pool_size = 0
        random_coverage = 0.0
        order_recovered = ""
        order_rank = 0
        order_rollouts = 0
        if controls_on:
            # 4c. random-ORDER: same candidate set, shuffled. Isolates ranking
            # quality from candidate-set quality, and is immune to the coverage
            # degeneracy because the set is identical to the live arm's.
            # Recovery is IDENTICAL to the live arm by construction (both walk
            # the same set until the first hit), so this arm is a COST
            # comparison -- never report it as a recovery-rate win.
            shuffled = list(scored_candidates)
            control_rng.shuffle(shuffled)
            (
                _order_net,
                order_spec,
                order_rollouts,
                order_rank,
            ) = _replay_early_stop(
                shuffled, threshold=args.recovered_threshold
            )
            order_recovered = str(order_spec is not None).lower()
            # 4a. fixed-library: frozen after the warm-up prefix.
            fixed_candidates = _retrieve_library(
                fixed_library, fingerprint, topn=args.topn
            )
            (
                fixed_net,
                fixed_spec,
                fixed_rollouts,
                _fixed_rank,
            ) = _run_library_stage(
                fixed_candidates,
                execute=execute,
                net_gain=net_gain,
                recall=recall,
                max_depth=max_depth,
                item_pool=item_pool,
                threshold=args.recovered_threshold,
            )
            fixed_hit = (
                fixed_spec is not None
                and fixed_net >= args.recovered_threshold
            )
            # The frozen arm still needs a discovery path, otherwise it measures
            # "no library" rather than "library that stopped growing".  Reuse the
            # live arm's discovery outcome instead of paying for a second scan.
            if not fixed_hit and recovery_source == "discovery":
                fixed_hit = True
                fixed_net = discovery_net
                fixed_spec = recovered_spec
            if fixed_hit and case_index <= warmup and fixed_spec is not None:
                fixed_key = (fingerprint, fixed_spec.format())
                if fixed_key not in {
                    (str(e["fp"]), e["spec"].format()) for e in fixed_library
                }:
                    fixed_library.append(
                        {
                            "fp": fingerprint,
                            "spec": fixed_spec,
                            "net": fixed_net,
                            "case_id": case.case_id,
                        }
                    )
            fixed_recovered = str(bool(fixed_hit)).lower()
            fixed_net_out = format_recovery_value(fixed_net, digits=4)

            # 4b. random-variation: same realized budget, shapes drawn at random.
            random_pool_size = len(
                _random_pool(library, case_id=case.case_id)
            )
            random_coverage = _random_coverage(
                random_pool_size, library_rollouts
            )
            random_shapes = _sample_random_shapes(
                library,
                budget=library_rollouts,
                case_id=case.case_id,
                rng=control_rng,
            )
            (
                random_net,
                random_spec,
                random_rollouts,
                _random_rank,
            ) = _run_library_stage(
                random_shapes,
                execute=execute,
                net_gain=net_gain,
                recall=recall,
                max_depth=max_depth,
                item_pool=item_pool,
                threshold=args.recovered_threshold,
            )
            random_hit = (
                random_spec is not None
                and random_net >= args.recovered_threshold
            )
            random_recovered = str(bool(random_hit)).lower()
            random_net_out = format_recovery_value(random_net, digits=4)

        total_rollouts = library_rollouts + discovery_rollouts
        recovered = recovered_spec is not None
        detail_rows.append(
            {
                "case_index": str(case_index),
                "generation_bin": str(generation_bin),
                "case_id": case.case_id,
                "gold_label": case.perturbation_label,
                "status": "ok",
                "excluded": "false",
                "timeout_count": str(timeout_count),
                "fingerprint": fingerprint,
                "fingerprint_hit": str(bool(candidates)).lower(),
                "library_size_before": str(library_size_before),
                "library_candidates": str(len(candidates)),
                "library_rollouts": str(library_rollouts),
                "library_rank_recovered": str(library_rank_recovered),
                "library_net": format_recovery_value(
                    library_net,
                    digits=4,
                ),
                "discovery_rollouts": str(discovery_rollouts),
                "discovery_net": format_recovery_value(
                    discovery_net,
                    digits=4,
                ),
                "total_rollouts": str(total_rollouts),
                "recovered": str(recovered).lower(),
                "accepted": str(recovered).lower(),
                "recovery_source": recovery_source or "unrecovered",
                "recovered_op": (
                    recovered_spec.format() if recovered_spec else ""
                ),
                "best_net_gain": format_recovery_value(
                    recovered_net,
                    digits=4,
                ),
                "delta_k": format_recovery_value(
                    recovered_net,
                    digits=4,
                ),
                "library_written": str(library_written).lower(),
                "fixed_recovered": fixed_recovered,
                "fixed_net": fixed_net_out,
                "fixed_rollouts": str(fixed_rollouts),
                "fixed_library_size_before": str(fixed_size_before),
                "random_recovered": random_recovered,
                "random_net": random_net_out,
                "random_rollouts": str(random_rollouts),
                "random_pool_size": str(random_pool_size),
                "random_coverage": (
                    f"{random_coverage:.4f}" if controls_on else ""
                ),
                "random_order_recovered": order_recovered,
                "random_order_rank": str(order_rank),
                "random_order_rollouts": str(order_rollouts),
                "library_rank": str(library_rank_recovered),
            }
        )
        print(
            f"  [{case_index}/{len(cases)}] "
            f"{case.perturbation_label:16s} "
            f"lib={library_size_before:03d} "
            f"src={recovery_source or '-':9s} "
            f"rollouts={total_rollouts:3d} net={recovered_net:+.2f}"
        )

    detail_path = write_csv_table(
        Path(args.out),
        _detail_fieldnames(),
        detail_rows,
        sandbox_root=OUT.parent,
        judge_client=judge_client,
    )
    summary_rows = _summary_rows(detail_rows, bin_size=args.bin_size)
    summary_path = write_csv_table(
        _summary_path(Path(args.out)),
        _summary_fieldnames(),
        summary_rows,
        sandbox_root=OUT.parent,
        judge_client=judge_client,
    )
    _print_summary(summary_rows)
    print(f"\nWrote {detail_path}\nWrote {summary_path}")
    print(
        "\nVERDICT guide: Gate 2 passes only if the live arm (recovery_rate) "
        "climbs across bins AND beats BOTH controls -- fixed_recovery_rate "
        "(library frozen after warm-up) and random_recovery_rate (same "
        "realized budget, shapes drawn at random). Climbing alone is not "
        "evidence: Exp22's same-budget random arm reached 0.84x the oracle "
        "ceiling, so a bare climb is consistent with later cases simply "
        "getting more attempts."
    )
    print(
        "Flat recovery with falling avg_total_rollouts is NOT a null result -- "
        "it is the 'warm-up reuse' finding (Exp18's real outcome). Flat on "
        "both axes = use Exp23's item-layer finding instead."
    )
    print(
        "Require >=3 seeds agreeing in direction (37% churn); run "
        "analyze_significance.py for the paired McNemar and trend tests."
    )


def _excluded_detail_row(
    *,
    case_index: int,
    generation_bin: int,
    case: Any,
    library_size_before: int,
) -> dict[str, str]:
    return {
        "case_index": str(case_index),
        "generation_bin": str(generation_bin),
        "case_id": case.case_id,
        "gold_label": case.perturbation_label,
        "status": "base_gain_timeout",
        "excluded": "true",
        "timeout_count": "1",
        "fingerprint": "",
        "fingerprint_hit": "false",
        "library_size_before": str(library_size_before),
        "library_candidates": "0",
        "library_rollouts": "0",
        "library_rank_recovered": "0",
        "library_net": "",
        "discovery_rollouts": "0",
        "discovery_net": "",
        "total_rollouts": "0",
        "recovered": "false",
        "accepted": "false",
        "recovery_source": "excluded",
        "recovered_op": "",
        "best_net_gain": "",
        "delta_k": "",
        "library_written": "false",
        # A NaN identity baseline makes every arm's net NaN, so the case is
        # excluded from the live arm AND both controls -- never counted as a
        # failure in any of them.
        "fixed_recovered": "",
        "fixed_net": "",
        "fixed_rollouts": "0",
        "fixed_library_size_before": "0",
        "random_recovered": "",
        "random_net": "",
        "random_rollouts": "0",
        "random_pool_size": "0",
        "random_coverage": "",
        "random_order_recovered": "",
        "random_order_rank": "0",
        "random_order_rollouts": "0",
        "library_rank": "0",
    }


def _summary_rows(
    rows: list[dict[str, str]],
    *,
    bin_size: int,
) -> list[dict[str, str]]:
    """Aggregate fixed stream-position bins, excluding timed-out base cases."""
    summaries: list[dict[str, str]] = []
    for start in range(0, len(rows), bin_size):
        chunk = rows[start : start + bin_size]
        included = [row for row in chunk if row["excluded"] != "true"]
        excluded = [row for row in chunk if row["excluded"] == "true"]
        recovered = [
            row for row in included if row["recovered"] == "true"
        ]
        library_recovered = [
            row
            for row in included
            if row["recovery_source"] == "library"
        ]
        # Controls are blank when --controls off, so their denominators count
        # only rows where the arm actually ran.
        fixed_measured = [
            row for row in included if row.get("fixed_recovered", "") != ""
        ]
        fixed_recovered = [
            row for row in fixed_measured if row["fixed_recovered"] == "true"
        ]
        random_measured = [
            row for row in included if row.get("random_recovered", "") != ""
        ]
        random_recovered = [
            row for row in random_measured if row["random_recovered"] == "true"
        ]
        denominator = len(included)
        summaries.append(
            {
                "generation_bin": chunk[0]["generation_bin"],
                "bin_start": chunk[0]["case_index"],
                "bin_end": chunk[-1]["case_index"],
                "cases": str(denominator),
                "excluded_cases": str(len(excluded)),
                "timeout_count": str(
                    sum(int(row["timeout_count"]) for row in chunk)
                ),
                "recovered": str(len(recovered)),
                "recovery_rate": (
                    f"{len(recovered) / denominator:.4f}"
                    if denominator
                    else "0.0000"
                ),
                "library_recovered": str(len(library_recovered)),
                "library_recovery_rate": (
                    f"{len(library_recovered) / denominator:.4f}"
                    if denominator
                    else "0.0000"
                ),
                "fixed_recovered": str(len(fixed_recovered)),
                "fixed_recovery_rate": (
                    f"{len(fixed_recovered) / len(fixed_measured):.4f}"
                    if fixed_measured
                    else ""
                ),
                "random_recovered": str(len(random_recovered)),
                "random_recovery_rate": (
                    f"{len(random_recovered) / len(random_measured):.4f}"
                    if random_measured
                    else ""
                ),
                "avg_library_size": _avg(
                    included,
                    "library_size_before",
                ),
                "avg_total_rollouts": _avg(
                    included,
                    "total_rollouts",
                ),
                "avg_rollouts_recovered_cases": _avg(
                    recovered,
                    "total_rollouts",
                ),
            }
        )
    return summaries


def _avg(rows: list[dict[str, str]], key: str) -> str:
    if not rows:
        return "0.0000"
    return f"{sum(float(row[key]) for row in rows) / len(rows):.4f}"


def _summary_path(detail_path: Path) -> Path:
    stem = detail_path.stem
    summary_stem = (
        stem.replace("detail", "summary", 1)
        if "detail" in stem
        else f"{stem}_summary"
    )
    return detail_path.with_name(f"{summary_stem}.csv")


def _detail_fieldnames() -> list[str]:
    return [
        "case_index",
        "generation_bin",
        "case_id",
        "gold_label",
        "status",
        "excluded",
        "timeout_count",
        "fingerprint",
        "fingerprint_hit",
        "library_size_before",
        "library_candidates",
        "library_rollouts",
        "library_rank_recovered",
        "library_net",
        "discovery_rollouts",
        "discovery_net",
        "total_rollouts",
        "recovered",
        "accepted",
        "recovery_source",
        "recovered_op",
        "best_net_gain",
        "delta_k",
        "library_written",
        "fixed_recovered",
        "fixed_net",
        "fixed_rollouts",
        "fixed_library_size_before",
        "random_recovered",
        "random_net",
        "random_rollouts",
        "random_pool_size",
        "random_coverage",
        "random_order_recovered",
        "random_order_rank",
        "random_order_rollouts",
        "library_rank",
    ]


def _summary_fieldnames() -> list[str]:
    return [
        "generation_bin",
        "bin_start",
        "bin_end",
        "cases",
        "excluded_cases",
        "timeout_count",
        "recovered",
        "recovery_rate",
        "library_recovered",
        "library_recovery_rate",
        "fixed_recovered",
        "fixed_recovery_rate",
        "random_recovered",
        "random_recovery_rate",
        "avg_library_size",
        "avg_total_rollouts",
        "avg_rollouts_recovered_cases",
    ]


def _print_summary(rows: list[dict[str, str]]) -> None:
    print("\n=== Operator trajectory summary ===")
    print(
        f"{'bin':>9s} {'live':>7s} {'lib_rec':>8s} {'fixed':>7s} "
        f"{'random':>7s} {'lib_size':>9s} {'rollouts':>9s} {'excl':>5s}"
    )
    for row in rows:
        print(
            f"{row['bin_start'] + '-' + row['bin_end']:>9s} "
            f"{row['recovery_rate']:>7s} "
            f"{row['library_recovery_rate']:>8s} "
            f"{(row.get('fixed_recovery_rate') or '-'):>7s} "
            f"{(row.get('random_recovery_rate') or '-'):>7s} "
            f"{row['avg_library_size']:>9s} "
            f"{row['avg_total_rollouts']:>9s} "
            f"{row['excluded_cases']:>5s}"
        )


if __name__ == "__main__":
    main()
