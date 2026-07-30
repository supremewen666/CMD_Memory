#!/usr/bin/env python3
"""Experiment 24B: Gate-controlled verified-feedback prequential evolution."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from cmd_audit.eval.evolution_gates import (
    build_family_split,
    permutation_p_value,
    write_family_split,
)
from cmd_audit.repair.failure_memory import bootstrap_frozen_pattern_catalog
from experiments.evolution_runner_common import (
    PrequentialEvolutionRunner,
    write_prequential_artifacts,
)
from experiments.experiment_runner_common import DATA, load_raw_rows
from experiments.run_experiment_24a_offline_evolution import FixtureBackend


OFFLINE_SCORER_VERSION = "g-eval-frozen-v1"


def _experiment_a_gate_status(path: Path) -> tuple[bool, bool]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    try:
        primary = bool(payload["primary"]["passed"])
        safety = bool(payload["safety"]["passed"])
        within_family = bool(
            payload["within_family"]["combined"]["passed"]
        )
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"{path}: expected Experiment A gate_results.json"
        ) from exc
    return primary and within_family, safety


def _experiment_a_scorer_version(gate_path: Path) -> str:
    """Read the scorer frozen by Experiment A from its sibling manifest."""

    manifest_path = gate_path.with_name("run_manifest.json")
    if not manifest_path.exists():
        raise ValueError(
            f"{manifest_path}: Experiment A run manifest is required to "
            "verify the frozen scorer identity"
        )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    scorer_version = str(payload.get("scorer_version") or "")
    if not scorer_version:
        raise ValueError(
            f"{manifest_path}: missing non-empty scorer_version"
        )
    return scorer_version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        default=str(DATA / "real_recurrent_cases.json"),
    )
    parser.add_argument(
        "--experiment-a-gates",
        required=True,
        help="Experiment 24A gate_results.json; both Gates must pass.",
    )
    parser.add_argument(
        "--out",
        default="artifacts/exp_runs/exp24b",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--fixture",
        help="Pre-scored JSON fixture; omit for Gate/split validation only.",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="Execute with the configured answer model and frozen judge.",
    )
    parser.add_argument("--seed", type=int, default=24)
    parser.add_argument("--permutations", type=int, default=100)
    parser.add_argument(
        "--scorer-version",
        default=None,
        help=(
            f"Fixture/dry-run modes default to {OFFLINE_SCORER_VERSION!r}. "
            "On --live, an explicit value is only an assertion and must match "
            "the identity derived from the configured judge."
        ),
    )
    args = parser.parse_args()

    primary_passed, safety_passed = _experiment_a_gate_status(
        Path(args.experiment_a_gates)
    )
    if not primary_passed or not safety_passed:
        raise SystemExit(
            "Experiment B disabled: Experiment A primary and safety Gates "
            "must both pass."
        )
    case_path = Path(args.cases)
    rows = load_raw_rows(case_path)
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    scorer_version = args.scorer_version or OFFLINE_SCORER_VERSION
    scorer_identity: dict[str, str] = {
        "scorer_version": scorer_version,
        "identity_source": "fixture_or_dry_run_label",
    }
    fixture_payload = (
        json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        if args.fixture
        else None
    )
    shared_live_backend = None
    if args.live:
        from experiments.live_evolution_backend import LiveEvolutionBackend

        shared_live_backend = LiveEvolutionBackend(
            scorer_version=args.scorer_version,
            judge_seed=args.seed,
        )
        scorer_version = shared_live_backend.scorer_version
        scorer_identity = dict(shared_live_backend.scorer_identity)
    experiment_a_scorer = _experiment_a_scorer_version(
        Path(args.experiment_a_gates)
    )
    if scorer_version != experiment_a_scorer:
        raise SystemExit(
            "Experiment B scorer identity mismatch: Experiment A used "
            f"{experiment_a_scorer!r}, but this run resolved "
            f"{scorer_version!r}."
        )

    manifest = {
        "experiment": "24B",
        "claim_boundary": "verified-feedback prequential online simulation",
        "dataset": str(case_path),
        "dataset_sha256": hashlib.sha256(case_path.read_bytes()).hexdigest(),
        "experiment_a_gates": str(args.experiment_a_gates),
        "experiment_a_primary_passed": primary_passed,
        "experiment_a_safety_passed": safety_passed,
        "experiment_a_scorer_version": experiment_a_scorer,
        "seed": args.seed,
        "permutations": args.permutations,
        "threshold": 0.1,
        "scorer_version": scorer_version,
        "scorer_identity": scorer_identity,
        "initial_skill_library": "empty",
        "evaluate_before_update": True,
        "fixture_mechanics_only": bool(args.fixture),
        "live_execution": bool(args.live),
    }
    if not args.fixture and not args.live:
        split = build_family_split(rows)
        catalog = bootstrap_frozen_pattern_catalog(rows)
        write_family_split(output / "family_split.json", split)
        (output / "pattern_catalog.json").write_text(
            json.dumps(catalog.to_json(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (output / "run_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            f"Experiment A Gates verified. Prepared Experiment B in {output}; "
            "no model calls were made."
        )
        return

    def execute(seed: int):
        if args.live:
            backend = shared_live_backend
            discovery_config = {
                "source": "live",
                "classes": "all",
                "max_candidates": 128,
                "judge_seed": args.seed,
            }
        else:
            backend = FixtureBackend(fixture_payload)
            discovery_config = {
                "source": "fixture",
                "path": str(args.fixture),
            }
        runner = PrequentialEvolutionRunner(
            rows,
            experiment_a_primary_passed=primary_passed,
            experiment_a_safety_passed=safety_passed,
            case_evaluator=backend.evaluate,
            shadow_discoverer=backend.shadow,
            probe_evaluator=backend.probe,
            direct_revision_evaluator=backend.direct_revision_gain,
            scorer_version=scorer_version,
            discovery_config=discovery_config,
            seed=seed,
        )
        if args.live:
            backend.bind_store(runner.store)
        return runner.run_prequential(), backend

    observed, observed_backend = execute(args.seed)
    permutation_contrasts = [
        execute(args.seed + index + 1)[0].represented_aulc_contrast
        for index in range(args.permutations)
    ]
    permutation_p = (
        permutation_p_value(
            observed.represented_aulc_contrast,
            permutation_contrasts,
        )
        if permutation_contrasts
        else None
    )
    write_prequential_artifacts(
        observed,
        output,
        run_manifest=manifest,
        permutation_contrasts=permutation_contrasts,
        permutation_p=permutation_p,
    )
    if args.live:
        observed_backend.cache.write_jsonl(output / "rollout_cache.jsonl")
    print(
        f"Experiment 24B fixture complete: AULC contrast="
        f"{observed.represented_aulc_contrast:+.4f}, "
        f"permutation p={permutation_p}; wrote {output}"
    )


if __name__ == "__main__":
    main()
