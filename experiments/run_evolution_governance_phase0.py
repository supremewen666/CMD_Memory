#!/usr/bin/env python3
"""Phase 0: zero-call replay for selector evolution and deposition gate v2."""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import random
from statistics import fmean
import sys
from typing import Iterable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.counterfactual.actions import PipelineAction
from cmd_audit.counterfactual.operators import OperatorSpec
from cmd_audit.eval.evolution_gates import permutation_p_value
from cmd_audit.repair.chain_dynamics import (
    ChainObserver,
    DepositionCandidate,
)
from cmd_audit.repair.skill_ecology import ChainExecution, SkillCandidate


DEFAULT_INPUTS = (
    "artifacts/arena/memtrace_seed24.jsonl",
    "artifacts/arena/memtrace_seed124.jsonl",
    "artifacts/arena/memtrace_seed224.jsonl",
    "artifacts/arena/memtrace_llama.jsonl",
)


@dataclass(frozen=True)
class OfflineArenaRun:
    run_id: str
    path: Path
    manifest: Mapping[str, object]
    observations: tuple[Mapping[str, object], ...]
    attempts: tuple[Mapping[str, object], ...]
    deposition_after_case: int
    artifact_sha256: str


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="+", default=DEFAULT_INPUTS)
    parser.add_argument(
        "--output-dir",
        default="artifacts/evolution_governance/phase0",
    )
    parser.add_argument("--seed", type=int, default=24)
    parser.add_argument("--permutations", type=int, default=9_999)
    parser.add_argument("--bootstrap-samples", type=int, default=2_000)
    args = parser.parse_args()

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    runs = tuple(_load_run(Path(value)) for value in args.inputs)
    selector_rows: list[dict[str, object]] = []
    curve_rows: list[dict[str, object]] = []
    d1_rows: list[dict[str, object]] = []
    threshold_rows: list[dict[str, object]] = []
    passed_by_run: dict[str, set[tuple[str, str]]] = {}

    for run in runs:
        run_seed = int(run.manifest.get("seed", args.seed))
        selector, curve = replay_selector(
            run,
            seed=run_seed,
            permutations=args.permutations,
        )
        selector_rows.append(selector)
        curve_rows.extend(curve)
        candidates, benefits = replay_d1(
            run,
            seed=run_seed,
            bootstrap_samples=args.bootstrap_samples,
        )
        passed_by_run[run.run_id] = {
            (row.first_skill_id, row.second_skill_id)
            for row in candidates
            if row.passed
        }
        for candidate in candidates:
            row = candidate.to_dict()
            row.pop("composite_spec", None)
            d1_rows.append({"run_id": run.run_id, **row})
        threshold_rows.extend(
            _threshold_scan_rows(run.run_id, candidates, benefits)
        )

    pair_counts: dict[tuple[str, str], int] = {}
    for pairs in passed_by_run.values():
        for pair in pairs:
            pair_counts[pair] = pair_counts.get(pair, 0) + 1
    reproducible = sorted(
        pair for pair, count in pair_counts.items() if count >= 3
    )
    selector_passes = sum(
        bool(row["passed"]) for row in selector_rows
    )
    phase1_gate_passed = selector_passes >= 2 or bool(reproducible)
    ge1_passed = bool(reproducible) or not any(passed_by_run.values())

    _write_csv(output / "selector_replay.csv", selector_rows)
    _write_csv(output / "selector_cumulative_curve.csv", curve_rows)
    _write_csv(output / "d1_candidates.csv", d1_rows)
    _write_csv(output / "threshold_calibration.csv", threshold_rows)
    summary = {
        "phase": 0,
        "model_calls": 0,
        "run_count": len(runs),
        "inputs": [
            {
                "run_id": run.run_id,
                "path": str(run.path),
                "artifact_sha256": run.artifact_sha256,
                "seed": run.manifest.get("seed"),
            }
            for run in runs
        ],
        "selector_runs_p_lt_0_05": selector_passes,
        "d1_passed_pairs_by_run": {
            run_id: [list(pair) for pair in sorted(pairs)]
            for run_id, pairs in passed_by_run.items()
        },
        "d1_pair_run_counts": {
            " -> ".join(pair): count
            for pair, count in sorted(pair_counts.items())
        },
        "reproducible_pairs_3_of_4": [
            list(pair) for pair in reproducible
        ],
        "g_e1_passed": ge1_passed,
        "phase1_gate_passed": phase1_gate_passed,
        "phase1_gate_rule": (
            "selector p<0.05 in >=2/4 runs OR D1 pair reproduced in >=3/4"
        ),
        "decision": (
            "phase1_authorized_by_offline_evidence"
            if phase1_gate_passed
            else "stop_before_phase1_negative_result"
        ),
    }
    (output / "phase0_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    print(f"[RESULT] model_calls=0")
    print(f"[RESULT] selector_runs_p_lt_0_05={selector_passes}/{len(runs)}")
    print(f"[RESULT] reproducible_d1_pairs={len(reproducible)}")
    print(f"[RESULT] g_e1_passed={int(ge1_passed)}")
    print(f"[RESULT] phase1_gate_passed={int(phase1_gate_passed)}")
    print(f"[RESULT] summary={output / 'phase0_summary.json'}")
    return 0


def _load_run(path: Path) -> OfflineArenaRun:
    observations = []
    attempts = []
    depositions = []
    manifest: Mapping[str, object] | None = None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            record_type = row.get("record_type")
            if record_type == "arena_manifest":
                if manifest is not None:
                    raise ValueError(f"{path}: duplicate manifest")
                manifest = row
            elif record_type == "gold_free_observation":
                observations.append(row)
            elif record_type == "chain_attempt":
                attempts.append(row)
            elif record_type == "chain_deposition_event":
                depositions.append(row)
    if manifest is None:
        raise ValueError(f"{path}: missing manifest")
    if manifest.get("runtime_uses_gold") is not False:
        raise ValueError(f"{path}: runtime_uses_gold must be false")
    cutoff = (
        int(depositions[0]["deposited_after_case"])
        if depositions
        else math.ceil(int(manifest["case_count"]) / 2)
    )
    return OfflineArenaRun(
        run_id=path.stem,
        path=path.resolve(),
        manifest=manifest,
        observations=tuple(observations),
        attempts=tuple(attempts),
        deposition_after_case=cutoff,
        artifact_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def replay_selector(
    run: OfflineArenaRun,
    *,
    seed: int,
    permutations: int,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Test-then-update family priors; shadow scores are outcome-only."""
    history: dict[tuple[str, str], list[float]] = {}
    family_effects: dict[str, float] = {}
    evolving_cumulative = 0.0
    frozen_cumulative = 0.0
    aulc_contrast = 0.0
    curve = []
    for position, row in enumerate(run.observations, start=1):
        family_id = str(row["family_id"])
        gold_free = _score_mapping(row.get("gold_free_scores"))
        shadow = _score_mapping(row.get("shadow_gold_scores"))
        skills = sorted(set(gold_free) & set(shadow))
        if not skills:
            continue
        frozen_skill = skills[0]
        evolving_skill = min(
            skills,
            key=lambda skill_id: (
                -(
                    fmean(history[(family_id, skill_id)])
                    if history.get((family_id, skill_id))
                    else 0.0
                ),
                skill_id,
            ),
        )
        evolving_gain = shadow[evolving_skill]
        frozen_gain = shadow[frozen_skill]
        evolving_cumulative += evolving_gain
        frozen_cumulative += frozen_gain
        contrast = evolving_gain - frozen_gain
        family_effects[family_id] = (
            family_effects.get(family_id, 0.0) + contrast
        )
        aulc_contrast += evolving_cumulative - frozen_cumulative
        curve.append(
            {
                "run_id": run.run_id,
                "stream_position": position,
                "case_id": row["case_id"],
                "family_id": family_id,
                "evolving_skill_id": evolving_skill,
                "frozen_skill_id": frozen_skill,
                "evolving_cumulative_shadow_gain": evolving_cumulative,
                "frozen_cumulative_shadow_gain": frozen_cumulative,
            }
        )
        # Strict prequential order: only now may current exhaustive evidence update.
        for skill_id, value in gold_free.items():
            history.setdefault((family_id, skill_id), []).append(value)

    observed = sum(family_effects.values())
    rng = random.Random(
        int(
            hashlib.sha256(
                f"{seed}\0{run.run_id}".encode()
            ).hexdigest()[:16],
            16,
        )
    )
    effects = tuple(family_effects.values())
    null = [
        sum(value if rng.random() < 0.5 else -value for value in effects)
        for _ in range(permutations)
    ]
    p_value = (
        permutation_p_value(observed, null) if null else 1.0
    )
    return (
        {
            "run_id": run.run_id,
            "case_count": len(curve),
            "family_count": len(family_effects),
            "evolving_endpoint_shadow_gain": evolving_cumulative,
            "frozen_endpoint_shadow_gain": frozen_cumulative,
            "endpoint_contrast": observed,
            "aulc_contrast": (
                aulc_contrast / len(curve) if curve else 0.0
            ),
            "family_blocked_permutation_p": p_value,
            "passed": observed > 0.0 and p_value < 0.05,
            "runtime_update_signal": "gold_free_scores",
            "outcome_signal": "shadow_gold_scores",
            "prequential": True,
        },
        curve,
    )


def replay_d1(
    run: OfflineArenaRun,
    *,
    seed: int,
    bootstrap_samples: int,
) -> tuple[
    tuple[DepositionCandidate, ...],
    Mapping[tuple[str, str], tuple[float, ...]],
]:
    family_by_case = {
        str(row["case_id"]): str(row["family_id"])
        for row in run.observations
    }
    grouped: dict[int, list[Mapping[str, object]]] = {}
    benefits: dict[tuple[str, str], list[float]] = {}
    candidate_ids = set()
    for row in run.attempts:
        position = int(row["stream_position"])
        if position > run.deposition_after_case:
            continue
        grouped.setdefault(position, []).append(row)
        pair = (
            str(row["first_skill_id"]),
            str(row["second_skill_id"]),
        )
        candidate_ids.update(pair)
        value = row.get("chain_benefit")
        if _finite(value):
            benefits.setdefault(pair, []).append(float(value))
    candidates = {}
    for skill_id in sorted(candidate_ids):
        try:
            action = PipelineAction(skill_id.rsplit(":", 1)[-1])
        except ValueError:
            continue
        candidates[skill_id] = SkillCandidate(
            skill_id,
            OperatorSpec.single(0, action),
        )
    observer = ChainObserver(arena_id=str(run.manifest["arena_id"]))
    for position, rows in sorted(grouped.items()):
        case_id = str(rows[0]["case_id"])
        activated = sorted(
            {
                str(row[key])
                for row in rows
                for key in ("first_skill_id", "second_skill_id")
            }
        )
        executions = tuple(
            ChainExecution(
                first_skill_id=str(row["first_skill_id"]),
                second_skill_id=str(row["second_skill_id"]),
                chained_context="offline-replay",
                chained_gain=_optional_float(row.get("chained_gain")),
                standalone_max=_optional_float(row.get("standalone_max")),
                chain_benefit=_optional_float(row.get("chain_benefit")),
                beneficial=(
                    _finite(row.get("chain_benefit"))
                    and float(row["chain_benefit"]) > 0.05
                ),
                execution_cost=0.0,
                status=str(row.get("status", "offline_replay")),
            )
            for row in rows
        )
        observer.record_case(
            case_id=case_id,
            family_id=family_by_case.get(case_id, ""),
            failure_type=str(rows[0].get("failure_type", "")),
            stream_position=position,
            activated_skill_ids=activated,
            chain_executions=executions,
        )
    events = observer.promote_candidates(
        candidates=candidates,
        checkpoint=f"offline:{run.deposition_after_case}",
        min_support=10,
        min_clusters=3,
        sign_alpha=0.05,
        direction_alpha=0.10,
        bootstrap_samples=bootstrap_samples,
        seed=seed,
        source_sha256=run.artifact_sha256,
    )
    return events, {
        pair: tuple(values) for pair, values in benefits.items()
    }


def _threshold_scan_rows(
    run_id: str,
    candidates: Sequence[DepositionCandidate],
    benefits: Mapping[tuple[str, str], tuple[float, ...]],
) -> list[dict[str, object]]:
    rows = []
    for n_min in (6, 8, 10, 12):
        for confirmation_k in (6, 8, 10):
            for dominance_threshold in (0.5, 0.6, 0.7):
                passed = 0
                for candidate in candidates:
                    values = benefits.get(
                        (
                            candidate.first_skill_id,
                            candidate.second_skill_id,
                        ),
                        (),
                    )
                    sampled = values[:confirmation_k]
                    dominance = (
                        sum(value > 0.0 for value in sampled) / len(sampled)
                        if sampled
                        else 0.0
                    )
                    base_reasons = set(candidate.rejection_reasons) - {
                        "insufficient_support"
                    }
                    if (
                        candidate.n_support >= n_min
                        and not base_reasons
                        and len(sampled) == confirmation_k
                        and dominance >= dominance_threshold
                    ):
                        passed += 1
                rows.append(
                    {
                        "run_id": run_id,
                        "n_min": n_min,
                        "confirmation_k": confirmation_k,
                        "dominance_threshold": dominance_threshold,
                        "passed_pair_count": passed,
                        "calibration_only": True,
                    }
                )
    return rows


def _score_mapping(value: object) -> dict[str, float]:
    output = {}
    if not isinstance(value, list):
        return output
    for item in value:
        if (
            isinstance(item, list)
            and len(item) == 2
            and _finite(item[1])
        ):
            output[str(item[0])] = float(item[1])
    return output


def _optional_float(value: object) -> float | None:
    return float(value) if _finite(value) else None


def _finite(value: object) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _write_csv(path: Path, rows: Iterable[Mapping[str, object]]) -> Path:
    materialized = [dict(row) for row in rows]
    fieldnames = sorted(
        {key for row in materialized for key in row}
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in materialized:
            writer.writerow(
                {
                    key: (
                        json.dumps(value, ensure_ascii=False, sort_keys=True)
                        if isinstance(value, (dict, list, tuple))
                        else value
                    )
                    for key, value in row.items()
                }
            )
    return path


if __name__ == "__main__":
    raise SystemExit(main())
