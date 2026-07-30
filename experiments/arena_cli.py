"""CLI plumbing for the observational arena entry points."""
from __future__ import annotations

import argparse
from collections import Counter
import importlib
from pathlib import Path
from typing import Callable, Sequence

from .arena_runner_common import (
    ArenaCase,
    ObservationalArenaRunner,
    write_arena_artifacts,
)


ArenaLoader = Callable[..., tuple[ArenaCase, ...]]


def run_arena_cli(
    *,
    arena_id: str,
    loader: ArenaLoader,
    default_cases: str,
    default_output: str,
    chains_default: bool,
) -> int:
    parser = argparse.ArgumentParser(
        description=f"Run the {arena_id} observational skill arena."
    )
    parser.add_argument("--cases", default=default_cases)
    parser.add_argument("--output", default=default_output)
    parser.add_argument("--seed", type=int, default=24)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--recovery-threshold", type=float, default=0.1)
    parser.add_argument(
        "--backend-factory",
        default="experiments.arena_backends:create_vllm_backend",
        help=(
            "Import path module:factory. The factory is called with "
            "(cases=<tuple>, args=<Namespace>) and must return a "
            "DualScoreArenaBackend. Defaults to the concrete vLLM/OpenAI "
            "dual-score backend."
        ),
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate stream structure without executing candidates.",
    )
    parser.add_argument(
        "--chains",
        action=argparse.BooleanOptionalAction,
        default=chains_default,
    )
    parser.add_argument(
        "--deposit-after",
        type=float,
        default=None,
        help=(
            "Optional stream fraction for one observed chain deposition. "
            "The backend must implement deposit_composite."
        ),
    )
    parser.add_argument("--deposit-min-benefit", type=float, default=0.05)
    parser.add_argument("--deposit-min-support", type=int, default=3)
    parser.add_argument(
        "--perturb-after",
        type=float,
        default=None,
        help=(
            "Optional stream fraction after which one current keystone or "
            "specialist skill is removed for the rest of this run."
        ),
    )
    parser.add_argument(
        "--perturb-strategy",
        choices=("keystone", "specialist"),
        default="keystone",
    )
    parser.add_argument("--perturb-window-size", type=int, default=25)
    parser.add_argument("--perturb-stability-threshold", type=float, default=0.05)
    parser.add_argument("--perturb-stable-windows", type=int, default=2)
    args = parser.parse_args()

    cases = loader(args.cases, seed=args.seed, limit=args.limit)
    _print_stream_validation(arena_id, cases)
    if args.validate_only:
        print("[RESULT] validation_only=1")
        return 0
    backend = _load_backend(args.backend_factory, cases=cases, args=args)
    runner = ObservationalArenaRunner(
        cases,
        backend=backend,
        top_k=args.top_k,
        recovery_threshold=args.recovery_threshold,
        seed=args.seed,
        enable_chains=args.chains,
        deposition_after_fraction=args.deposit_after,
        deposition_min_benefit=args.deposit_min_benefit,
        deposition_min_support=args.deposit_min_support,
        perturb_after_fraction=args.perturb_after,
        perturb_strategy=args.perturb_strategy,
        perturb_window_size=args.perturb_window_size,
        perturb_stability_threshold=args.perturb_stability_threshold,
        perturb_stable_windows=args.perturb_stable_windows,
    )
    result = runner.run()
    output = write_arena_artifacts(result, args.output)
    print(f"[RESULT] arena_id={arena_id}")
    print(f"[RESULT] cases={len(cases)}")
    print(
        "[RESULT] candidate_executions="
        f"{sum(len(row.attempted_skill_ids) for row in result.competition_events)}"
    )
    print(f"[RESULT] chain_attempts={len(result.chain_attempts)}")
    print(f"[RESULT] deposition_events={len(result.deposition_events)}")
    print(f"[RESULT] perturbation_events={len(result.perturbation_events)}")
    print(f"[RESULT] output={output}")
    return 0


def _load_backend(
    import_path: str,
    *,
    cases: Sequence[ArenaCase],
    args: argparse.Namespace,
):
    if ":" not in import_path:
        raise SystemExit("--backend-factory must use module:factory syntax")
    module_name, attribute = import_path.split(":", 1)
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise SystemExit(f"backend factory is not callable: {import_path}")
    return factory(cases=tuple(cases), args=args)


def _print_stream_validation(
    arena_id: str,
    cases: Sequence[ArenaCase],
) -> None:
    failures = Counter(case.failure_type for case in cases)
    families = {case.family_id for case in cases}
    subsets = Counter(case.subset for case in cases)
    print(f"[RESULT] arena_id={arena_id}")
    print(f"[RESULT] validated_cases={len(cases)}")
    print(f"[RESULT] families={len(families)}")
    print(
        "[RESULT] failure_distribution="
        + ",".join(f"{key}:{failures[key]}" for key in sorted(failures))
    )
    print(
        "[RESULT] subset_distribution="
        + ",".join(f"{key}:{subsets[key]}" for key in sorted(subsets))
    )
