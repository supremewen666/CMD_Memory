"""CLI plumbing for the observational arena entry points."""
from __future__ import annotations

import argparse
from collections import Counter
import importlib
import os
from pathlib import Path
from typing import Callable, Sequence

from .arena_runner_common import (
    ArenaCase,
    ObservationalArenaRunner,
    build_arena_dataset_fingerprint,
    write_arena_artifacts,
)
from cmd_audit.repair.structural_router import (
    ScopePolicy,
    build_live_item_gate_extractor,
)
from cmd_audit.repair.scope_ledger import ScopeLedger


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
    parser.add_argument("--saturation-threshold", type=float, default=0.8)
    parser.add_argument(
        "--candidate-limit",
        type=int,
        default=0,
        help="Optional hard cap for diagnostics; 0 evaluates every legal skill.",
    )
    parser.add_argument(
        "--case-workers",
        type=int,
        default=int(os.environ.get("CMD_CASE_WORKERS", "1")),
        help=(
            "Concurrent cases for stateless runs. Values >1 are rejected when "
            "deposition or perturbation changes later candidate sets."
        ),
    )
    parser.add_argument(
        "--best-of-n-control",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Run an unstructured best-of-N arm with N equal to CMD's distinct "
            "non-baseline contexts after cache reuse."
        ),
    )
    parser.add_argument(
        "--scope-ledger",
        default="",
        help=(
            "Load active domain×signal scopes from an audited SIGIL ledger. "
            "This is the production activation path."
        ),
    )
    parser.add_argument(
        "--live-item-gate",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Run the Tier-2 item gate in shadow mode before outcomes. "
            "It can route only when an audited scope ledger activates its signal."
        ),
    )
    parser.add_argument(
        "--item-gate-divergence-threshold",
        type=float,
        default=0.5,
    )
    parser.add_argument(
        "--item-gate-timestamp-tolerance-days",
        type=int,
        default=7,
    )
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
        "--evolve-selection-priors",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Prequentially reorder candidates from prior same-family evidence.",
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
    parser.add_argument("--deposit-min-support", type=int, default=10)
    parser.add_argument("--deposit-min-clusters", type=int, default=3)
    parser.add_argument("--deposit-sign-alpha", type=float, default=0.05)
    parser.add_argument("--deposit-direction-alpha", type=float, default=0.10)
    parser.add_argument("--deposit-confirmation-cases", type=int, default=8)
    parser.add_argument("--deposit-max-candidates", type=int, default=2)
    parser.add_argument("--deposit-marginal-dominance", type=float, default=0.60)
    parser.add_argument("--deposit-confirmation-budget", type=int, default=50)
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
    _print_stream_validation(arena_id, cases, dataset_source_path=args.cases)
    if args.validate_only:
        print("[RESULT] validation_only=1")
        return 0
    backend = _load_backend(args.backend_factory, cases=cases, args=args)
    scope_policy = (
        ScopeLedger.read(args.scope_ledger).to_scope_policy()
        if args.scope_ledger
        else ScopePolicy()
    )
    item_gate_extractor = None
    if args.live_item_gate:
        item_gate_client = getattr(backend, "selection_judge_client", None)
        item_gate_model_identity = str(
            getattr(backend, "selection_judge_identity", "")
        )
        if item_gate_client is None or not item_gate_model_identity:
            raise ValueError(
                "--live-item-gate requires a backend exposing a "
                "gold-free selection_judge_client and identity"
            )
        item_gate_extractor = build_live_item_gate_extractor(
            item_gate_client,
            model_identity=item_gate_model_identity,
            divergence_threshold=args.item_gate_divergence_threshold,
            timestamp_tolerance_days=(
                args.item_gate_timestamp_tolerance_days
            ),
        )
    runner = ObservationalArenaRunner(
        cases,
        backend=backend,
        saturation_threshold=args.saturation_threshold,
        candidate_limit=args.candidate_limit or None,
        seed=args.seed,
        enable_chains=args.chains,
        evolve_selection_priors=args.evolve_selection_priors,
        deposition_after_fraction=args.deposit_after,
        deposition_min_benefit=args.deposit_min_benefit,
        deposition_min_support=args.deposit_min_support,
        deposition_min_clusters=args.deposit_min_clusters,
        deposition_sign_alpha=args.deposit_sign_alpha,
        deposition_direction_alpha=args.deposit_direction_alpha,
        deposition_confirmation_cases=args.deposit_confirmation_cases,
        deposition_max_candidates=args.deposit_max_candidates,
        deposition_marginal_dominance=args.deposit_marginal_dominance,
        deposition_confirmation_budget=args.deposit_confirmation_budget,
        perturb_after_fraction=args.perturb_after,
        perturb_strategy=args.perturb_strategy,
        perturb_window_size=args.perturb_window_size,
        perturb_stability_threshold=args.perturb_stability_threshold,
        perturb_stable_windows=args.perturb_stable_windows,
        case_workers=args.case_workers,
        enable_best_of_n_control=args.best_of_n_control,
        dataset_source_path=args.cases,
        scope_policy=scope_policy,
        item_gate_extractor=item_gate_extractor,
    )
    result = runner.run()
    output = write_arena_artifacts(result, args.output)
    print(f"[RESULT] arena_id={arena_id}")
    print(f"[RESULT] cases={len(cases)}")
    print(f"[RESULT] case_workers={result.manifest.case_workers}")
    print(f"[RESULT] live_item_gate={int(args.live_item_gate)}")
    print(
        "[RESULT] branch_distribution="
        f"fill:{result.manifest.fill_case_count},fix:{result.manifest.fix_case_count}"
    )
    print(
        "[RESULT] candidate_executions="
        f"{sum(len(row.attempted_skill_ids) for row in result.saturation_events)}"
    )
    fix_events = tuple(
        row for row in result.saturation_events if row.runtime_branch == "fix"
    )
    print(
        "[RESULT] cumulative_coverage_rate="
        + (
            f"{sum(row.covered for row in fix_events) / len(fix_events):.6f}"
            if fix_events
            else "nan"
        )
    )
    print(
        "[RESULT] repair_effective_rate="
        + (
            f"{sum(row.repair_effective for row in fix_events) / len(fix_events):.6f}"
            if fix_events
            else "nan"
        )
    )
    print(f"[RESULT] chain_attempts={len(result.chain_attempts)}")
    print(f"[RESULT] deposition_events={len(result.deposition_events)}")
    print(
        "[RESULT] deposition_confirmation_calls="
        f"{result.manifest.deposition_confirmation_calls}"
    )
    print(f"[RESULT] perturbation_events={len(result.perturbation_events)}")
    print(f"[RESULT] arm_comparison_events={len(result.arm_comparison_events)}")
    print(
        "[RESULT] arm_comparison_coverage_rate="
        + (
            f"{len(result.arm_comparison_events) / result.manifest.fix_case_count:.6f}"
            if result.manifest.fix_case_count
            else "nan"
        )
    )
    print(
        "[RESULT] fix_cases_without_arm_comparison="
        f"{result.manifest.fix_case_count - len(result.arm_comparison_events)}"
    )
    aligned_comparisons = sum(
        event.budget_aligned for event in result.arm_comparison_events
    )
    print(f"[RESULT] budget_aligned_pairs={aligned_comparisons}")
    print(
        "[RESULT] budget_aligned_rate="
        + (
            f"{aligned_comparisons / len(result.arm_comparison_events):.6f}"
            if result.arm_comparison_events
            else "nan"
        )
    )
    budget_sources = Counter(
        event.cmd_budget_source for event in result.arm_comparison_events
    )
    print(
        "[RESULT] cmd_budget_source_distribution="
        + (
            ",".join(
                f"{key}:{budget_sources[key]}" for key in sorted(budget_sources)
            )
            if budget_sources
            else "none"
        )
    )
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
    *,
    dataset_source_path: str | Path,
) -> None:
    fingerprint = build_arena_dataset_fingerprint(
        cases,
        source_path=dataset_source_path,
    )
    failures = Counter(case.failure_type for case in cases)
    families = {case.family_id for case in cases}
    subsets = Counter(case.subset for case in cases)
    branches = Counter(case.runtime_branch for case in cases)
    print(f"[RESULT] arena_id={arena_id}")
    print(f"[RESULT] validated_cases={len(cases)}")
    print(f"[RESULT] dataset_source_path={fingerprint.source_path}")
    print(f"[RESULT] dataset_source_sha256={fingerprint.source_sha256}")
    print(
        "[RESULT] selected_case_ids_sha256="
        f"{fingerprint.selected_case_ids_sha256}"
    )
    print(
        "[RESULT] selected_cases_sha256="
        f"{fingerprint.selected_cases_sha256}"
    )
    print(f"[RESULT] families={len(families)}")
    print(
        "[RESULT] failure_distribution="
        + ",".join(f"{key}:{failures[key]}" for key in sorted(failures))
    )
    print(
        "[RESULT] subset_distribution="
        + ",".join(f"{key}:{subsets[key]}" for key in sorted(subsets))
    )
    print(
        "[RESULT] branch_distribution="
        + ",".join(f"{key}:{branches[key]}" for key in sorted(branches))
    )
