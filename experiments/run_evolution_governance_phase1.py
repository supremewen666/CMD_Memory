#!/usr/bin/env python3
"""Phase 1: budget-aligned evolution-on versus all-frozen live arenas."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import random
from statistics import fmean
import sys
from typing import Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.eval.evolution_gates import permutation_p_value
from cmd_audit.repair.governance import _bootstrap_lower_bound
from experiments.arena_backends import VLLMDualScoreArenaBackend
from experiments.arena_runner_common import (
    ArenaRunResult,
    ObservationalArenaRunner,
    load_memtrace_arena_cases,
    load_stale_arena_cases,
    write_arena_artifacts,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase0-summary",
        default="artifacts/evolution_governance/phase0/phase0_summary.json",
    )
    parser.add_argument(
        "--memtrace-cases",
        default="data/probe_cases/memtrace_kp_cases.json",
    )
    parser.add_argument(
        "--stale-cases",
        default="data/probe_cases/stale_item_cases.json",
    )
    parser.add_argument(
        "--output-dir",
        default="artifacts/evolution_governance/phase1",
    )
    parser.add_argument("--seed", type=int, default=24)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--candidate-budget", type=int, default=2)
    parser.add_argument("--permutations", type=int, default=9_999)
    parser.add_argument("--bootstrap-samples", type=int, default=2_000)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Actually execute both live arms. Omit for a zero-call validation.",
    )
    args = parser.parse_args()

    phase0_path = Path(args.phase0_summary)
    phase0 = json.loads(phase0_path.read_text(encoding="utf-8"))
    if phase0.get("phase1_gate_passed") is not True:
        raise SystemExit("Phase 0 gate did not authorize Phase 1")
    if args.candidate_budget < 2:
        parser.error("--candidate-budget must be >=2 for directed chains")

    datasets = (
        (
            "memtrace",
            Path(args.memtrace_cases),
            load_memtrace_arena_cases,
        ),
        ("stale", Path(args.stale_cases), load_stale_arena_cases),
    )
    loaded = [
        (
            arena_id,
            path,
            loader(path, seed=args.seed, limit=args.limit),
        )
        for arena_id, path, loader in datasets
    ]
    if not args.live:
        print("[RESULT] validation_only=1")
        print("[RESULT] model_calls=0")
        print("[RESULT] phase0_gate_passed=1")
        for arena_id, _path, cases in loaded:
            print(f"[RESULT] {arena_id}_cases={len(cases)}")
        print(
            "[RESULT] candidate_budget_per_case="
            f"{args.candidate_budget}"
        )
        print(
            "[RESULT] live_run_requires_explicit_flag=--live"
        )
        return 0

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    arena_summaries = []
    all_curve_rows = []
    for arena_id, dataset_path, cases in loaded:
        evolution = _run_arm(
            cases,
            dataset_path=dataset_path,
            seed=args.seed,
            candidate_budget=args.candidate_budget,
            evolution_enabled=True,
        )
        frozen = _run_arm(
            cases,
            dataset_path=dataset_path,
            seed=args.seed,
            candidate_budget=args.candidate_budget,
            evolution_enabled=False,
        )
        write_arena_artifacts(
            evolution,
            output / f"{arena_id}_evolution_on.jsonl",
        )
        write_arena_artifacts(
            frozen,
            output / f"{arena_id}_all_frozen.jsonl",
        )
        summary, curve = compare_arms(
            arena_id,
            evolution,
            frozen,
            seed=args.seed,
            permutations=args.permutations,
            bootstrap_samples=args.bootstrap_samples,
        )
        arena_summaries.append(summary)
        all_curve_rows.extend(curve)

    _write_csv(output / "phase1_cumulative_curve.csv", all_curve_rows)
    _write_csv(output / "phase1_arena_summary.csv", arena_summaries)
    combined_passed = all(bool(row["g_e3_passed"]) for row in arena_summaries)
    manifest = {
        "phase": 1,
        "seed": args.seed,
        "candidate_budget": args.candidate_budget,
        "phase0_summary": str(phase0_path.resolve()),
        "phase0_summary_sha256": hashlib.sha256(
            phase0_path.read_bytes()
        ).hexdigest(),
        "arenas": arena_summaries,
        "g_e3_passed": combined_passed,
        "decision": (
            "positive_evolution_result"
            if combined_passed
            else "negative_result_chapter"
        ),
    }
    (output / "phase1_summary.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    print(f"[RESULT] g_e3_passed={int(combined_passed)}")
    print(f"[RESULT] summary={output / 'phase1_summary.json'}")
    return 0


def _run_arm(
    cases,
    *,
    dataset_path: Path,
    seed: int,
    candidate_budget: int,
    evolution_enabled: bool,
) -> ArenaRunResult:
    backend = VLLMDualScoreArenaBackend()
    return ObservationalArenaRunner(
        cases,
        backend=backend,
        candidate_limit=candidate_budget,
        seed=seed,
        enable_chains=True,
        evolve_selection_priors=evolution_enabled,
        deposition_after_fraction=0.5 if evolution_enabled else None,
        deposition_min_support=10,
        deposition_min_clusters=3,
        deposition_confirmation_cases=8,
        deposition_max_candidates=2,
        deposition_marginal_dominance=0.60,
        deposition_confirmation_budget=50,
        dataset_source_path=dataset_path,
    ).run()


def compare_arms(
    arena_id: str,
    evolution: ArenaRunResult,
    frozen: ArenaRunResult,
    *,
    seed: int,
    permutations: int,
    bootstrap_samples: int,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Paired endpoint, first-try, cost, permutation, and bootstrap report."""
    evo_observations = {
        row.case_id: row for row in evolution.gold_free_observations
    }
    frozen_observations = {
        row.case_id: row for row in frozen.gold_free_observations
    }
    evo_events = {row.case_id: row for row in evolution.saturation_events}
    frozen_events = {row.case_id: row for row in frozen.saturation_events}
    case_ids = [row.case_id for row in evolution.saturation_events]
    if case_ids != [row.case_id for row in frozen.saturation_events]:
        raise ValueError("Phase 1 arms do not share the same case stream")
    family_effects: dict[str, float] = {}
    evo_cumulative = 0.0
    frozen_cumulative = 0.0
    aligned = 0
    evo_first_try = 0
    frozen_first_try = 0
    curve = []
    for position, case_id in enumerate(case_ids, start=1):
        evo_obs = evo_observations[case_id]
        frozen_obs = frozen_observations[case_id]
        evo_event = evo_events[case_id]
        frozen_event = frozen_events[case_id]
        evo_gain = _finite_or_zero(evo_event.shadow_selected_cumulative_gain)
        frozen_gain = _finite_or_zero(
            frozen_event.shadow_selected_cumulative_gain
        )
        evo_cumulative += evo_gain
        frozen_cumulative += frozen_gain
        family_effects[evo_obs.family_id] = (
            family_effects.get(evo_obs.family_id, 0.0)
            + evo_gain
            - frozen_gain
        )
        aligned += (
            len(evo_event.attempted_skill_ids)
            == len(frozen_event.attempted_skill_ids)
        )
        evo_first_try += bool(
            evo_event.attempted_skill_ids
            and evo_event.attempted_skill_ids[0]
            == evo_obs.oracle_skill_id
        )
        frozen_first_try += bool(
            frozen_event.attempted_skill_ids
            and frozen_event.attempted_skill_ids[0]
            == frozen_obs.oracle_skill_id
        )
        curve.append(
            {
                "arena_id": arena_id,
                "stream_position": position,
                "case_id": case_id,
                "family_id": evo_obs.family_id,
                "evolution_on_cumulative_shadow_gain": evo_cumulative,
                "all_frozen_cumulative_shadow_gain": frozen_cumulative,
            }
        )
    observed = sum(family_effects.values())
    effects = tuple(family_effects.values())
    rng = random.Random(seed)
    null = [
        sum(value if rng.random() < 0.5 else -value for value in effects)
        for _ in range(permutations)
    ]
    p_value = permutation_p_value(observed, null)
    ci_lower = _bootstrap_lower_bound(
        effects,
        confidence=0.95,
        samples=bootstrap_samples,
        seed=seed,
    )
    case_count = len(case_ids)
    evo_cost = (
        sum(len(row.attempted_skill_ids) for row in evolution.saturation_events)
        + len(evolution.chain_attempts)
    )
    frozen_cost = (
        sum(len(row.attempted_skill_ids) for row in frozen.saturation_events)
        + len(frozen.chain_attempts)
    )
    return (
        {
            "arena_id": arena_id,
            "case_count": case_count,
            "family_count": len(effects),
            "evolution_on_endpoint_shadow_gain": evo_cumulative,
            "all_frozen_endpoint_shadow_gain": frozen_cumulative,
            "endpoint_contrast": observed,
            "family_mean_contrast_ci_lower": ci_lower,
            "family_blocked_permutation_p": p_value,
            "evolution_on_first_try_oracle_top1_rate": (
                evo_first_try / case_count if case_count else 0.0
            ),
            "all_frozen_first_try_oracle_top1_rate": (
                frozen_first_try / case_count if case_count else 0.0
            ),
            "evolution_on_logical_cost_per_case": (
                evo_cost / case_count if case_count else 0.0
            ),
            "all_frozen_logical_cost_per_case": (
                frozen_cost / case_count if case_count else 0.0
            ),
            "budget_aligned_case_rate": (
                aligned / case_count if case_count else 0.0
            ),
            "deposition_confirmation_calls": (
                evolution.manifest.deposition_confirmation_calls
            ),
            "g_e2_passed": (
                evolution.manifest.deposition_confirmation_calls <= 50
            ),
            "g_e3_passed": observed > 0.0 and p_value < 0.05,
        },
        curve,
    )


def _finite_or_zero(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
