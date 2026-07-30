#!/usr/bin/env python3
"""Experiment 24A: offline, closed-loop Skill evolution.

The runner defaults to preparation/validation only so a local build never
starts costly model calls. ``--fixture`` executes the complete state machine
from deterministic pre-scored outcomes; ``--live`` binds the existing answer
model, frozen judge, executor, and bounded discovery implementation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from cmd_audit.counterfactual.operators import OperatorSpec
from cmd_audit.data_io import (
    DEFAULT_MEMTRACE_CASES,
    load_memtrace_dataset,
    load_memtrace_family_net_gains,
)
from cmd_audit.eval.evolution_gates import build_family_split, write_family_split
from cmd_audit.repair.failure_memory import bootstrap_frozen_pattern_catalog
from cmd_audit.repair.operator_library import TapeCandidate
from experiments.evolution_runner_common import (
    ArmEvaluation,
    OfflineEvolutionRunner,
    write_run_artifacts,
)
from experiments.experiment_runner_common import DATA, load_raw_rows


OFFLINE_SCORER_VERSION = "g-eval-frozen-v1"


class FixtureBackend:
    """Deterministic backend for mechanics validation and cached remote scores."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.cases = dict(payload.get("cases") or {})

    def evaluate(
        self,
        row: Mapping[str, Any],
        arm_id: str,
        _version,
        revisions: Sequence[Any],
    ) -> ArmEvaluation:
        case = self._case(row)
        arm = dict(case.get("arms", {}).get(arm_id, {}))
        working = set(str(item) for item in arm.get("working_spec_hashes", ()))
        gains = {
            str(key): float(value)
            for key, value in arm.get("spec_gains", {}).items()
        }
        attempted: list[str] = []
        executed: list[tuple[str, float, float]] = []
        recovered = False
        recovery_gain = 0.0
        for revision in revisions:
            attempted.append(revision.revision_id)
            gain = gains.get(
                revision.spec_hash,
                0.2 if revision.spec_hash in working else 0.0,
            )
            executed.append((revision.revision_id, gain, 1.0))
            recovery_gain = max(recovery_gain, gain)
            if gain >= 0.1:
                recovered = True
                break
        discovery_hit = bool(arm.get("discovery_recovered", False))
        discovery_gain = float(arm.get("discovery_gain", 0.2 if discovery_hit else 0.0))
        if not recovered and discovery_hit:
            recovered = True
            recovery_gain = discovery_gain
        return ArmEvaluation(
            attempted_revision_ids=tuple(attempted),
            executed_revision_gains=tuple(executed),
            recovered=recovered,
            recovery_gain=recovery_gain,
            library_rollouts=len(attempted),
            discovery_rollouts=(
                int(arm.get("discovery_rollouts", 1))
                if not any(gain >= 0.1 for _revision, gain, _cost in executed)
                else 0
            ),
        )

    def probe(
        self,
        row: Mapping[str, Any],
        arm_id: str,
        version,
        revisions: Sequence[Any],
    ) -> bool:
        return self.evaluate(row, arm_id, version, revisions).recovered

    def direct_revision_gain(
        self,
        row: Mapping[str, Any],
        arm_id: str,
        revision,
    ) -> float:
        """Return the fixture's library-stage gain for exactly one revision."""
        case = self._case(row)
        arm = dict(case.get("arms", {}).get(arm_id, {}))
        gains = {
            str(key): float(value)
            for key, value in arm.get("spec_gains", {}).items()
        }
        working = set(
            str(item) for item in arm.get("working_spec_hashes", ())
        )
        return gains.get(
            revision.spec_hash,
            0.2 if revision.spec_hash in working else 0.0,
        )

    def shadow(self, row: Mapping[str, Any]) -> tuple[TapeCandidate, ...]:
        values = self._case(row).get("shadow_candidates", ())
        return tuple(
            TapeCandidate(
                spec=OperatorSpec.from_dict(dict(item["spec"])),
                recovery_gain=float(item["recovery_gain"]),
                rollout_cost=float(item.get("rollout_cost", 1.0)),
                accepted=bool(item.get("accepted", True)),
                rejection_reason=str(item.get("rejection_reason", "")),
            )
            for item in values
        )

    def _case(self, row: Mapping[str, Any]) -> Mapping[str, Any]:
        return dict(self.cases.get(str(row["case_id"]), {}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        default=str(DATA / "real_recurrent_cases.json"),
    )
    parser.add_argument(
        "--out",
        default="artifacts/exp_runs/exp24a",
    )
    parser.add_argument(
        "--memtrace-cases",
        default=str(DEFAULT_MEMTRACE_CASES),
        help="Family-keyed MemTrace dataset used by the mandatory marginal-utility Gate.",
    )
    parser.add_argument(
        "--within-family-gains",
        help=(
            "Complete MemTrace JSON net-gain artifact. Required with --fixture "
            "or --live; omission fails closed instead of dropping the family Gate."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--fixture",
        help="Pre-scored JSON fixture; omit to validate and materialize the split only.",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="Execute with the configured answer model and frozen judge.",
    )
    parser.add_argument("--seed", type=int, default=24)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument(
        "--scorer-version",
        default=None,
        help=(
            f"Fixture/dry-run modes default to {OFFLINE_SCORER_VERSION!r}. On --live, "
            "this is validated against (and if omitted, replaced by) the "
            "identity hash derived from the real judge endpoint; see "
            "cmd_audit.eval.provenance.require_scorer_version."
        ),
    )
    args = parser.parse_args()
    if (args.fixture or args.live) and not args.within_family_gains:
        parser.error(
            "--within-family-gains is required with --fixture or --live; "
            "the primary Gate may not silently omit its family component"
        )

    case_path = Path(args.cases)
    rows = load_raw_rows(case_path)
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    dataset_sha256 = hashlib.sha256(case_path.read_bytes()).hexdigest()
    memtrace_path = Path(args.memtrace_cases)
    memtrace_dataset = load_memtrace_dataset(memtrace_path)
    within_family_gains = (
        load_memtrace_family_net_gains(
            args.within_family_gains,
            dataset=memtrace_dataset,
        )
        if args.within_family_gains
        else None
    )
    scorer_version = args.scorer_version or OFFLINE_SCORER_VERSION
    scorer_identity: dict[str, str] = {
        "scorer_version": scorer_version,
        "identity_source": "fixture_or_dry_run_label",
    }
    backend = None
    discovery_config: dict[str, Any] | None = None
    if args.live:
        from experiments.live_evolution_backend import LiveEvolutionBackend

        backend = LiveEvolutionBackend(
            scorer_version=args.scorer_version,
            judge_seed=args.seed,
        )
        scorer_version = backend.scorer_version
        scorer_identity = dict(backend.scorer_identity)
        discovery_config = {
            "source": "live",
            "classes": "all",
            "max_candidates": 128,
        }
    elif args.fixture:
        backend = FixtureBackend(
            json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        )
        discovery_config = {"source": "fixture", "path": str(args.fixture)}
    manifest = {
        "experiment": "24A",
        "dataset": str(case_path),
        "dataset_sha256": dataset_sha256,
        "memtrace_dataset": str(memtrace_path),
        "memtrace_dataset_sha256": hashlib.sha256(
            memtrace_path.read_bytes()
        ).hexdigest(),
        "within_family_gains": (
            str(Path(args.within_family_gains))
            if args.within_family_gains
            else None
        ),
        "within_family_gains_sha256": (
            hashlib.sha256(Path(args.within_family_gains).read_bytes()).hexdigest()
            if args.within_family_gains
            else None
        ),
        "seed": args.seed,
        "threshold": 0.1,
        "skill_top_k": 5,
        "exploitation_slots": 3,
        "exploration_slots": 2,
        "scorer_version": scorer_version,
        "scorer_identity": scorer_identity,
        "pattern_mutation": False,
        "initial_skill_library": "empty",
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
            f"Prepared {len(split)} families and {len(catalog.patterns)} frozen "
            f"Patterns in {output}. No model calls were made."
        )
        return

    if backend is None or discovery_config is None:
        raise AssertionError("execution backend was not configured")
    if within_family_gains is None:
        raise AssertionError("within-family Gate input was not configured")
    runner = OfflineEvolutionRunner(
        rows,
        case_evaluator=backend.evaluate,
        shadow_discoverer=backend.shadow,
        probe_evaluator=backend.probe,
        direct_revision_evaluator=backend.direct_revision_gain,
        scorer_version=scorer_version,
        discovery_config=discovery_config,
        seed=args.seed,
    )
    if args.live:
        backend.bind_store(runner.store)
    result = runner.run(
        within_family_gains=within_family_gains,
        bootstrap_samples=args.bootstrap_samples,
    )
    write_run_artifacts(result, output, run_manifest=manifest)
    if args.live:
        backend.cache.write_jsonl(output / "rollout_cache.jsonl")
    print(
        f"Experiment 24A fixture complete: primary={result.gate_results.primary.passed}, "
        f"safety={result.gate_results.safety.passed}; wrote {output}"
    )


if __name__ == "__main__":
    main()
