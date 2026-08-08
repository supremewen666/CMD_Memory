"""Shared single-path runner for observational memory-repair arenas."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import random
from statistics import fmean
from typing import Mapping, Protocol, Sequence

from cmd_audit.core.models import MemoryItem, ProbeCase, RetrievedItem
from cmd_audit.data_io.probe_cases import load_probe_cases
from cmd_audit.eval.gold_free_observer import (
    GoldFreeObservation,
    GoldFreeObserver,
    ProbeCoordinates,
)
from cmd_audit.eval.gold_free_identifiability import (
    RuntimeSelectionProvenance,
)
from cmd_audit.hook import post_retrieve_hook
from cmd_audit.repair.chain_dynamics import (
    ChainAttempt,
    ChainDepositionEvent,
    ChainObserver,
    CoactivationSnapshot,
    DepositionCandidate,
)
from cmd_audit.repair.failure_memory import AntiPatternRecord
from cmd_audit.repair.governance import OperatorGovernance
from cmd_audit.repair.memtrace_families import build_families, family_stream
from cmd_audit.repair.skill_ecology import (
    AdditiveSaturationExecutor,
    AdditiveSaturationResult,
    ChainExecution,
    EcologySnapshot,
    PerturbationEvent,
    PerturbationProbe,
    SaturationEcologyObserver,
    SkillCandidate,
    SkillExecution,
    TopPSaturationEvent,
)
from cmd_audit.repair.structural_router import (
    DETERMINISTIC_EXTRACTOR_VERSION,
    ItemGateExtractor,
    RUNTIME_INPUT_ALLOWLIST_SHA256,
    ScopePolicy,
    StructuralIndicationEvent,
    extract_structural_indications,
    indication_events,
    route as route_structural_indications,
)


@dataclass(frozen=True)
class ArenaCase:
    arena_id: str
    case_id: str
    family_id: str
    failure_type: str
    base_context: str
    coordinates: ProbeCoordinates
    subset: str
    raw: Mapping[str, object]
    runtime_branch: str = "fix"
    hook_confidence: float = 1.0


ARENA_DATASET_FINGERPRINT_VERSION = "arena-dataset-v1"


@dataclass(frozen=True)
class ArenaDatasetFingerprint:
    version: str
    source_kind: str
    source_path: str
    source_sha256: str | None
    source_size_bytes: int | None
    selected_case_ids_sha256: str
    selected_cases_sha256: str


@dataclass(frozen=True)
class DualScoreExecution:
    skill_id: str
    repaired_context: str
    gold_free_gain: float | None
    shadow_gold_gain: float | None
    execution_cost: float
    status: str = "ok"


@dataclass(frozen=True)
class BestOfNControlExecution:
    candidate_count: int
    finite_candidate_count: int
    selected_index: int | None
    selection_gain: float | None
    shadow_gold_gain: float | None
    answer_calls: int
    selection_judge_calls: int
    status: str = "ok"
    abstained: bool = False


@dataclass(frozen=True)
class ContextStuffingExecution:
    """Outcome of the named no-search baseline.

    Carries its token policy so the arm is reproducible: a pool silently cut to
    fit would otherwise be a different baseline on every case.
    """

    shadow_gold_gain: float | None
    answer_calls: int
    selection_judge_calls: int
    token_policy: str
    token_budget: int
    items_offered: int
    items_included: int
    truncated: bool
    status: str = "ok"


class DualScoreArenaBackend(Protocol):
    """Backend contract that keeps runtime and shadow scoring explicit."""

    gold_free_signal_name: str
    shadow_gold_signal_name: str
    runtime_uses_gold: bool

    def candidates(self, case: ArenaCase) -> Sequence[SkillCandidate]:
        ...

    def evaluate(
        self,
        case: ArenaCase,
        candidate: SkillCandidate,
        *,
        input_context: str,
        origin_context: str,
    ) -> DualScoreExecution:
        ...

    def deposit_composite(self, event: ChainDepositionEvent) -> None:
        """Add an observed composite to subsequent candidate retrieval."""
        ...

    def evaluate_best_of_n(
        self,
        case: ArenaCase,
        *,
        candidate_count: int,
        origin_context: str,
    ) -> BestOfNControlExecution:
        """Evaluate an unstructured, same-selection-budget control arm."""
        ...


@dataclass(frozen=True)
class ArenaManifest:
    arena_id: str
    case_count: int
    selection_mode: str
    saturation_threshold: float
    candidate_limit: int | None
    gold_free_signal_name: str
    shadow_gold_signal_name: str
    runtime_uses_gold: bool
    chains_enabled: bool
    deposition_enabled: bool
    selector_evolution_enabled: bool
    perturbation_enabled: bool
    perturbation_strategy: str
    seed: int
    fill_case_count: int
    fix_case_count: int
    fill_policy: str
    case_workers: int
    best_of_n_control_enabled: bool
    context_stuffing_control_enabled: bool
    context_stuffing_token_policy: str
    context_stuffing_token_budget: int
    selection_judge_identity: str
    evaluation_judge_identity: str
    cmd_budget_accounting: str
    deposition_confirmation_calls: int
    deposition_confirmation_budget: int
    dataset_fingerprint_version: str
    dataset_source_kind: str
    dataset_source_path: str
    dataset_source_sha256: str | None
    dataset_source_size_bytes: int | None
    selected_case_ids_sha256: str
    selected_cases_sha256: str
    scope_version: str
    active_structural_signal_types: tuple[str, ...]
    structural_extractor_version: str
    structural_input_allowlist_sha256: str


@dataclass(frozen=True)
class ArenaRunResult:
    manifest: ArenaManifest
    gold_free_observations: tuple[GoldFreeObservation, ...]
    saturation_events: tuple[TopPSaturationEvent, ...]
    ecology_snapshots: tuple[EcologySnapshot, ...]
    chain_attempts: tuple[ChainAttempt, ...]
    coactivation_snapshots: tuple[CoactivationSnapshot, ...]
    deposition_events: tuple[ChainDepositionEvent, ...]
    deposition_candidate_events: tuple[DepositionCandidate, ...]
    deposition_confirmation_events: tuple["DepositionConfirmationEvent", ...]
    anti_pattern_events: tuple[AntiPatternRecord, ...]
    perturbation_events: tuple[PerturbationEvent, ...]
    arm_comparison_events: tuple["ArenaArmComparisonEvent", ...]
    structural_indication_events: tuple[StructuralIndicationEvent, ...]


def _context_stuffing_fields(
    execution: "ContextStuffingExecution | None",
) -> dict[str, object]:
    """Comparison-event fields for the stuffing arm, or the not-run defaults."""
    if execution is None:
        return {}
    return {
        "context_stuffing_shadow_gold_gain": execution.shadow_gold_gain,
        "context_stuffing_answer_calls": execution.answer_calls,
        "context_stuffing_selection_judge_calls": (
            execution.selection_judge_calls
        ),
        "context_stuffing_items_offered": execution.items_offered,
        "context_stuffing_items_included": execution.items_included,
        "context_stuffing_truncated": execution.truncated,
        "context_stuffing_status": execution.status,
    }


@dataclass(frozen=True)
class ArenaArmComparisonEvent:
    arena_id: str
    case_id: str
    failure_type: str
    runtime_branch: str
    candidate_budget: int
    cmd_selected_skill_id: str | None
    cmd_abstained: bool
    cmd_selection_gain: float | None
    cmd_shadow_gold_gain: float | None
    best_of_n_selected_index: int | None
    best_of_n_abstained: bool
    best_of_n_selection_gain: float | None
    best_of_n_shadow_gold_gain: float | None
    cmd_answer_calls: int
    cmd_selection_judge_calls: int
    best_of_n_answer_calls: int
    best_of_n_selection_judge_calls: int
    budget_aligned: bool
    cmd_budget_source: str
    status: str
    # Context-stuffing arm. Defaulted so runs with the arm disabled -- and
    # artifacts written before it existed -- stay readable.
    context_stuffing_shadow_gold_gain: float | None = None
    context_stuffing_answer_calls: int = 0
    context_stuffing_selection_judge_calls: int = 0
    context_stuffing_items_offered: int = 0
    context_stuffing_items_included: int = 0
    context_stuffing_truncated: bool = False
    context_stuffing_status: str = "not_run"

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["record_type"] = "arena_arm_comparison_event"
        return value


@dataclass(frozen=True)
class DepositionConfirmationEvent:
    """Aggregated D2 replay and D3 marginal-contribution decision."""

    arena_id: str
    checkpoint: str
    first_skill_id: str
    second_skill_id: str
    case_ids: tuple[str, ...]
    cluster_ids: tuple[str, ...]
    replay_gains: tuple[float | None, ...]
    stage_max_gains: tuple[float | None, ...]
    confirmation_ci_lower: float | None
    marginal_dominance_rate: float
    d2_passed: bool
    d3_passed: bool
    deposited: bool
    reason: str
    confirmation_calls: int
    cumulative_confirmation_calls: int
    thresholds: Mapping[str, float | int]
    seed: int
    source_sha256: str
    provenance_sha256: str

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["record_type"] = "deposition_confirmation_event"
        return value


@dataclass(frozen=True)
class _EvaluatedArenaCase:
    case: ArenaCase
    candidates: tuple[SkillCandidate, ...]
    result: AdditiveSaturationResult
    dual_by_skill: Mapping[str, DualScoreExecution]
    chain_executions: tuple[ChainExecution, ...]
    best_of_n: BestOfNControlExecution | None
    context_stuffing: ContextStuffingExecution | None
    cmd_answer_calls: int
    cmd_selection_judge_calls: int
    cmd_budget_source: str
    structural_indication_events: tuple[StructuralIndicationEvent, ...]


class ObservationalArenaRunner:
    """Execute one immutable stream while append-only observers watch."""

    def __init__(
        self,
        cases: Sequence[ArenaCase],
        *,
        backend: DualScoreArenaBackend,
        saturation_threshold: float = 0.8,
        candidate_limit: int | None = None,
        seed: int = 24,
        enable_chains: bool = True,
        evolve_selection_priors: bool = False,
        deposition_after_fraction: float | None = None,
        deposition_min_benefit: float = 0.05,
        deposition_min_support: int = 10,
        deposition_min_clusters: int = 3,
        deposition_sign_alpha: float = 0.05,
        deposition_direction_alpha: float = 0.10,
        deposition_confirmation_cases: int = 8,
        deposition_max_candidates: int = 2,
        deposition_marginal_dominance: float = 0.60,
        deposition_confirmation_budget: int = 50,
        perturb_after_fraction: float | None = None,
        perturb_strategy: str = "keystone",
        perturb_window_size: int = 25,
        perturb_stability_threshold: float = 0.05,
        perturb_stable_windows: int = 2,
        case_workers: int = 1,
        enable_best_of_n_control: bool = False,
        enable_context_stuffing_control: bool = False,
        dataset_source_path: str | Path | None = None,
        scope_policy: ScopePolicy | None = None,
        item_gate_extractor: ItemGateExtractor | None = None,
    ) -> None:
        if not cases:
            raise ValueError("arena requires at least one case")
        case_ids = [case.case_id for case in cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("arena case_id values must be unique")
        if backend.runtime_uses_gold:
            raise ValueError(
                "arena backend declares gold-dependent runtime selection"
            )
        if not backend.gold_free_signal_name:
            raise ValueError("backend must name the gold-free signal")
        if not backend.shadow_gold_signal_name:
            raise ValueError("backend must name the shadow-gold signal")
        if saturation_threshold <= 0:
            raise ValueError("saturation_threshold must be > 0")
        if candidate_limit is not None and candidate_limit <= 0:
            raise ValueError("candidate_limit must be > 0 when provided")
        if deposition_after_fraction is not None and not (
            0 < deposition_after_fraction < 1
        ):
            raise ValueError("deposition fraction must be in (0, 1)")
        if (
            deposition_after_fraction is not None
            and not callable(getattr(backend, "deposit_composite", None))
        ):
            raise ValueError(
                "deposition requires a backend deposit_composite hook"
            )
        if deposition_min_support < 1 or deposition_min_clusters < 1:
            raise ValueError("deposition support and cluster minima must be positive")
        if deposition_confirmation_cases < 3:
            raise ValueError("deposition confirmation requires at least 3 cases")
        if deposition_max_candidates < 1:
            raise ValueError("deposition_max_candidates must be positive")
        if deposition_confirmation_budget < 1:
            raise ValueError("deposition confirmation budget must be positive")
        if not 0.0 <= deposition_marginal_dominance <= 1.0:
            raise ValueError("marginal dominance threshold must be in [0, 1]")
        if perturb_after_fraction is not None and not (
            0 < perturb_after_fraction < 1
        ):
            raise ValueError("perturbation fraction must be in (0, 1)")
        if perturb_strategy not in {"keystone", "specialist"}:
            raise ValueError("perturbation strategy must be keystone or specialist")
        if case_workers <= 0:
            raise ValueError("case_workers must be > 0")
        if case_workers > 1 and (
            deposition_after_fraction is not None
            or perturb_after_fraction is not None
            or evolve_selection_priors
        ):
            raise ValueError(
                "cross-case concurrency is incompatible with deposition or "
                "perturbation because those change later candidate sets"
            )
        if enable_best_of_n_control and not callable(
            getattr(backend, "evaluate_best_of_n", None)
        ):
            raise ValueError(
                "best-of-N control requires backend.evaluate_best_of_n"
            )
        if enable_context_stuffing_control and not callable(
            getattr(backend, "evaluate_context_stuffing", None)
        ):
            raise ValueError(
                "context-stuffing control requires "
                "backend.evaluate_context_stuffing"
            )
        self.cases = tuple(cases)
        self.backend = backend
        self.saturation_threshold = float(saturation_threshold)
        self.candidate_limit = (
            int(candidate_limit) if candidate_limit is not None else None
        )
        self.seed = int(seed)
        self.enable_chains = bool(enable_chains)
        self.evolve_selection_priors = bool(evolve_selection_priors)
        self._selection_prior_history: dict[
            tuple[str, str],
            list[float],
        ] = {}
        self._chain_pair_weights: dict[tuple[str, str], float] = {}
        self.deposition_after_fraction = deposition_after_fraction
        self.deposition_min_benefit = float(deposition_min_benefit)
        self.deposition_min_support = int(deposition_min_support)
        self.deposition_min_clusters = int(deposition_min_clusters)
        self.deposition_sign_alpha = float(deposition_sign_alpha)
        self.deposition_direction_alpha = float(deposition_direction_alpha)
        self.deposition_confirmation_cases = int(deposition_confirmation_cases)
        self.deposition_max_candidates = int(deposition_max_candidates)
        self.deposition_marginal_dominance = float(
            deposition_marginal_dominance
        )
        self.deposition_confirmation_budget = int(
            deposition_confirmation_budget
        )
        self.perturb_after_fraction = perturb_after_fraction
        self.perturb_strategy = str(perturb_strategy)
        self.perturb_window_size = int(perturb_window_size)
        self.perturb_stability_threshold = float(
            perturb_stability_threshold
        )
        self.perturb_stable_windows = int(perturb_stable_windows)
        self.case_workers = int(case_workers)
        self.enable_best_of_n_control = bool(enable_best_of_n_control)
        self.enable_context_stuffing_control = bool(
            enable_context_stuffing_control
        )
        self.scope_policy = scope_policy or ScopePolicy()
        self.item_gate_extractor = item_gate_extractor
        self.dataset_fingerprint = build_arena_dataset_fingerprint(
            self.cases,
            source_path=dataset_source_path,
        )

    def run(self) -> ArenaRunResult:
        arena_id = self.cases[0].arena_id
        if any(case.arena_id != arena_id for case in self.cases):
            raise ValueError("one runner cannot mix arena ids")
        gold_observer = GoldFreeObserver(arena_id=arena_id)
        ecology_observer = SaturationEcologyObserver(
            arena_id=arena_id,
            total_cases=len(self.cases),
        )
        chain_observer = ChainObserver(arena_id=arena_id)
        candidate_catalog: dict[str, SkillCandidate] = {}
        deposition_position = (
            math.ceil(len(self.cases) * self.deposition_after_fraction)
            if self.deposition_after_fraction is not None
            else None
        )
        # Deliberately one-shot: C5 is a natural experiment with one treatment
        # time, not an online composite-learning policy. Periodic deposition is
        # a separate future intervention and would change the observed stream.
        deposition_done = False
        perturbation_position = (
            math.ceil(len(self.cases) * self.perturb_after_fraction)
            if self.perturb_after_fraction is not None
            else None
        )
        removed_skill_id: str | None = None
        perturbation_probe: PerturbationProbe | None = None
        arm_comparison_events: list[ArenaArmComparisonEvent] = []
        structural_events: list[StructuralIndicationEvent] = []
        confirmation_events: list[DepositionConfirmationEvent] = []
        anti_pattern_events: list[AntiPatternRecord] = []
        deposition_confirmation_calls = 0
        evidence_by_case: dict[
            str,
            tuple[ArenaCase, Mapping[str, DualScoreExecution]],
        ] = {}
        deposition_governance = OperatorGovernance(seed=self.seed)
        governance_by_skill: dict[str, tuple[str, str]] = {}
        source_sha256 = (
            self.dataset_fingerprint.source_sha256
            or self.dataset_fingerprint.selected_cases_sha256
        )

        parallel_evaluations: tuple[_EvaluatedArenaCase, ...] | None = None
        if self.case_workers > 1:
            # ``map`` preserves input order, so observer reduction and JSONL
            # serialization remain deterministic for deterministic backends.
            with ThreadPoolExecutor(max_workers=self.case_workers) as pool:
                parallel_evaluations = tuple(
                    pool.map(self._evaluate_case, self.cases)
                )

        for position, case in enumerate(self.cases, start=1):
            evaluated = (
                parallel_evaluations[position - 1]
                if parallel_evaluations is not None
                else self._evaluate_case(
                    case,
                    removed_skill_id=removed_skill_id,
                )
            )
            candidates = evaluated.candidates
            result = evaluated.result
            dual_by_skill = dict(evaluated.dual_by_skill)
            chain_executions = evaluated.chain_executions
            structural_events.extend(evaluated.structural_indication_events)
            evidence_by_case[case.case_id] = (case, dual_by_skill)
            for deposition in chain_observer.depositions:
                live_row = dual_by_skill.get(deposition.composite_skill_id)
                governance_key = governance_by_skill.get(
                    deposition.composite_skill_id
                )
                if live_row is None or governance_key is None:
                    continue
                fingerprint, operator_hash = governance_key
                entry = deposition_governance.record_application(
                    fingerprint,
                    operator_hash,
                    succeeded=(
                        _finite(live_row.gold_free_gain)
                        and float(live_row.gold_free_gain) > 0.0
                    ),
                    generation=position,
                )
                lifecycle_updater = getattr(
                    self.backend,
                    "update_composite_lifecycle",
                    None,
                )
                if callable(lifecycle_updater):
                    lifecycle_updater(
                        deposition.composite_skill_id,
                        status=entry.lifecycle_status,
                        eta=entry.eta,
                    )
            if self.evolve_selection_priors:
                for skill_id, live_row in dual_by_skill.items():
                    if _finite(live_row.gold_free_gain):
                        self._selection_prior_history.setdefault(
                            (case.family_id, skill_id),
                            [],
                        ).append(float(live_row.gold_free_gain))
            if evaluated.best_of_n is not None:
                cmd_winner = result.selected[0] if result.selected else None
                cmd_dual = (
                    dual_by_skill.get(cmd_winner.skill_id)
                    if cmd_winner is not None
                    else None
                )
                control = evaluated.best_of_n
                cmd_budget = evaluated.cmd_answer_calls
                arm_comparison_events.append(
                    ArenaArmComparisonEvent(
                        arena_id=arena_id,
                        case_id=case.case_id,
                        failure_type=case.failure_type,
                        runtime_branch=case.runtime_branch,
                        candidate_budget=cmd_budget,
                        cmd_selected_skill_id=(
                            cmd_winner.skill_id if cmd_winner is not None else None
                        ),
                        cmd_abstained=cmd_winner is None,
                        cmd_selection_gain=(
                            float(cmd_winner.recovery_gain)
                            if cmd_winner is not None
                            and cmd_winner.recovery_gain is not None
                            else None
                        ),
                        cmd_shadow_gold_gain=(
                            cmd_dual.shadow_gold_gain if cmd_dual is not None else None
                        ),
                        best_of_n_selected_index=control.selected_index,
                        best_of_n_abstained=control.abstained,
                        best_of_n_selection_gain=control.selection_gain,
                        best_of_n_shadow_gold_gain=control.shadow_gold_gain,
                        cmd_answer_calls=evaluated.cmd_answer_calls,
                        cmd_selection_judge_calls=(
                            evaluated.cmd_selection_judge_calls
                        ),
                        best_of_n_answer_calls=control.answer_calls,
                        best_of_n_selection_judge_calls=(
                            control.selection_judge_calls
                        ),
                        budget_aligned=(
                            evaluated.cmd_answer_calls == control.answer_calls
                            and evaluated.cmd_selection_judge_calls
                            == control.selection_judge_calls
                        ),
                        cmd_budget_source=evaluated.cmd_budget_source,
                        status=control.status,
                        **_context_stuffing_fields(evaluated.context_stuffing),
                    )
                )
            candidate_catalog.update(
                (candidate.skill_id, candidate) for candidate in candidates
            )
            gold_observer.record(
                case_id=case.case_id,
                family_id=case.family_id,
                failure_type=case.failure_type,
                gold_free_scores={
                    skill_id: row.gold_free_gain
                    for skill_id, row in dual_by_skill.items()
                },
                shadow_gold_scores={
                    skill_id: row.shadow_gold_gain
                    for skill_id, row in dual_by_skill.items()
                },
                runtime_abstained=not result.repair_effective,
                runtime_selected_skill_id=(
                    result.selected[0].skill_id
                    if result.selected
                    and any(
                        event.route_selected
                        for event in evaluated.structural_indication_events
                    )
                    else None
                ),
                coordinates=case.coordinates,
                runtime_provenance=RuntimeSelectionProvenance(
                    context_constructed_without_gold=True,
                    selection_used_gold=False,
                    shadow_scores_isolated=True,
                ),
            )
            event = _top_p_saturation_event(
                result,
                dual_by_skill=dual_by_skill,
                checkpoint=f"{arena_id}:{position}/{len(self.cases)}",
                subset=case.subset,
                runtime_branch=case.runtime_branch,
            )
            snapshot = ecology_observer.record(
                event,
                stream_position=position,
            )
            if perturbation_probe is not None:
                perturbation_probe.observe(
                    stream_position=position,
                    winner_skill_id=(
                        result.selected[0].skill_id
                        if result.selected
                        else None
                    ),
                )
            chain_observer.record_case(
                case_id=case.case_id,
                family_id=case.family_id,
                failure_type=case.failure_type,
                stream_position=position,
                activated_skill_ids=tuple(
                    candidate.skill_id for candidate in candidates
                ),
                chain_executions=chain_executions,
            )
            if snapshot is not None:
                chain_observer.snapshot(snapshot.checkpoint)

            if (
                deposition_position is not None
                and position >= deposition_position
                and not deposition_done
            ):
                d1_events = chain_observer.promote_candidates(
                    candidates=candidate_catalog,
                    min_support=self.deposition_min_support,
                    min_clusters=self.deposition_min_clusters,
                    sign_alpha=self.deposition_sign_alpha,
                    direction_alpha=self.deposition_direction_alpha,
                    checkpoint=f"{arena_id}:{position}/{len(self.cases)}",
                    seed=self.seed,
                    source_sha256=source_sha256,
                )
                for candidate in d1_events:
                    if not candidate.anti_pattern:
                        continue
                    anti_pattern_events.append(
                        AntiPatternRecord(
                            first_skill_id=candidate.first_skill_id,
                            second_skill_id=candidate.second_skill_id,
                            cluster_id="*",
                            n_support=candidate.n_support,
                            ci_upper=candidate.ci_upper,
                            thresholds=candidate.thresholds,
                            seed=candidate.seed,
                            source_sha256=candidate.source_sha256,
                            provenance_sha256=candidate.provenance_sha256,
                        )
                    )
                    self._chain_pair_weights[
                        (
                            candidate.first_skill_id,
                            candidate.second_skill_id,
                        )
                    ] = 0.25
                survivors = sorted(
                    (row for row in d1_events if row.passed),
                    key=lambda row: (
                        -row.ci_lower,
                        -row.median_chain_benefit,
                        row.first_skill_id,
                        row.second_skill_id,
                    ),
                )[: self.deposition_max_candidates]
                for candidate in survivors:
                    confirmation, deposition, calls = (
                        self._confirm_deposition_candidate(
                            candidate,
                            checkpoint=(
                                f"{arena_id}:{position}/{len(self.cases)}"
                            ),
                            chain_attempts=chain_observer.attempts,
                            evidence_by_case=evidence_by_case,
                            governance=deposition_governance,
                            calls_already_used=deposition_confirmation_calls,
                            source_sha256=source_sha256,
                        )
                    )
                    deposition_confirmation_calls += calls
                    confirmation_events.append(confirmation)
                    if deposition is not None:
                        materialized = chain_observer.materialize_deposition(
                            candidate,
                            deposited_after_case=position,
                            confirmation_ci_lower=float(
                                confirmation.confirmation_ci_lower
                            ),
                            marginal_dominance_rate=(
                                confirmation.marginal_dominance_rate
                            ),
                            lifecycle_status="probation",
                            provenance_sha256=(
                                confirmation.provenance_sha256
                            ),
                        )
                        governance_by_skill[
                            materialized.composite_skill_id
                        ] = (arena_id, materialized.composite_spec_hash)
                        self.backend.deposit_composite(materialized)
                deposition_done = True
            if (
                perturbation_position is not None
                and position >= perturbation_position
                and perturbation_probe is None
            ):
                removed_skill_id = _select_removed_skill(
                    ecology_observer.events,
                    strategy=self.perturb_strategy,
                )
                if removed_skill_id is not None:
                    perturbation_probe = PerturbationProbe(
                        arena_id=arena_id,
                        removed_skill_id=removed_skill_id,
                        removal_strategy=self.perturb_strategy,
                        started_after_case=position,
                        window_size=self.perturb_window_size,
                        stability_threshold=self.perturb_stability_threshold,
                        stable_windows_required=self.perturb_stable_windows,
                    )

        ecology_observer.finalize()
        if (
            not chain_observer.snapshots
            or chain_observer.snapshots[-1].observed_cases != len(self.cases)
        ):
            chain_observer.snapshot(
                f"{arena_id}:{len(self.cases)}/{len(self.cases)}"
            )
        return ArenaRunResult(
            manifest=ArenaManifest(
                arena_id=arena_id,
                case_count=len(self.cases),
                selection_mode="additive_top_p",
                saturation_threshold=self.saturation_threshold,
                candidate_limit=self.candidate_limit,
                gold_free_signal_name=self.backend.gold_free_signal_name,
                shadow_gold_signal_name=self.backend.shadow_gold_signal_name,
                runtime_uses_gold=self.backend.runtime_uses_gold,
                chains_enabled=self.enable_chains,
                deposition_enabled=self.deposition_after_fraction is not None,
                selector_evolution_enabled=self.evolve_selection_priors,
                perturbation_enabled=self.perturb_after_fraction is not None,
                perturbation_strategy=(
                    self.perturb_strategy
                    if self.perturb_after_fraction is not None
                    else ""
                ),
                seed=self.seed,
                fill_case_count=sum(
                    case.runtime_branch == "fill" for case in self.cases
                ),
                fix_case_count=sum(
                    case.runtime_branch == "fix" for case in self.cases
                ),
                fill_policy="explicit_routed_no_diagnosis_excluded_from_cmd_selection",
                case_workers=self.case_workers,
                best_of_n_control_enabled=self.enable_best_of_n_control,
                context_stuffing_control_enabled=(
                    self.enable_context_stuffing_control
                ),
                context_stuffing_token_policy=str(
                    getattr(self.backend, "context_stuffing_token_policy", "")
                ),
                context_stuffing_token_budget=int(
                    getattr(self.backend, "context_stuffing_token_budget", 0)
                ),
                selection_judge_identity=str(
                    getattr(self.backend, "selection_judge_identity", "")
                ),
                evaluation_judge_identity=str(
                    getattr(self.backend, "evaluation_judge_identity", "")
                ),
                cmd_budget_accounting=(
                    "backend_call_counters"
                    if callable(getattr(self.backend, "cmd_call_counts", None))
                    else "logical_fallback"
                ),
                deposition_confirmation_calls=(
                    deposition_confirmation_calls
                ),
                deposition_confirmation_budget=(
                    self.deposition_confirmation_budget
                ),
                dataset_fingerprint_version=(
                    self.dataset_fingerprint.version
                ),
                dataset_source_kind=self.dataset_fingerprint.source_kind,
                dataset_source_path=self.dataset_fingerprint.source_path,
                dataset_source_sha256=(
                    self.dataset_fingerprint.source_sha256
                ),
                dataset_source_size_bytes=(
                    self.dataset_fingerprint.source_size_bytes
                ),
                selected_case_ids_sha256=(
                    self.dataset_fingerprint.selected_case_ids_sha256
                ),
                selected_cases_sha256=(
                    self.dataset_fingerprint.selected_cases_sha256
                ),
                scope_version=self.scope_policy.version,
                active_structural_signal_types=tuple(
                    sorted(self.scope_policy.active_signal_types)
                ),
                structural_extractor_version="+".join(
                    filter(
                        None,
                        (
                            DETERMINISTIC_EXTRACTOR_VERSION,
                            str(
                                getattr(
                                    self.item_gate_extractor,
                                    "extractor_version",
                                    "",
                                )
                            ),
                        ),
                    )
                ),
                structural_input_allowlist_sha256=(
                    RUNTIME_INPUT_ALLOWLIST_SHA256
                ),
            ),
            gold_free_observations=gold_observer.observations,
            saturation_events=ecology_observer.events,
            ecology_snapshots=ecology_observer.snapshots,
            chain_attempts=chain_observer.attempts,
            coactivation_snapshots=chain_observer.snapshots,
            deposition_events=chain_observer.depositions,
            deposition_candidate_events=chain_observer.candidate_events,
            deposition_confirmation_events=tuple(confirmation_events),
            anti_pattern_events=tuple(anti_pattern_events),
            perturbation_events=(
                (perturbation_probe.result(),)
                if perturbation_probe is not None
                else ()
            ),
            arm_comparison_events=tuple(arm_comparison_events),
            structural_indication_events=tuple(structural_events),
        )

    def _confirm_deposition_candidate(
        self,
        candidate: DepositionCandidate,
        *,
        checkpoint: str,
        chain_attempts: Sequence[ChainAttempt],
        evidence_by_case: Mapping[
            str,
            tuple[ArenaCase, Mapping[str, DualScoreExecution]],
        ],
        governance: OperatorGovernance,
        calls_already_used: int,
        source_sha256: str,
    ) -> tuple[DepositionConfirmationEvent, bool, int]:
        """Run bounded D2 replay and D3 stage-dominance checks."""
        if candidate.composite_spec is None:
            raise ValueError("D2 requires a materialized composite spec")
        selected = _select_confirmation_evidence(
            candidate,
            chain_attempts=chain_attempts,
            evidence_by_case=evidence_by_case,
            limit=self.deposition_confirmation_cases,
            seed=self.seed,
        )
        composite = SkillCandidate(
            skill_id=f"confirmation:{candidate.composite_spec_hash}",
            operator=candidate.composite_spec,
        )
        replay_gains: list[float | None] = []
        stage_max_gains: list[float | None] = []
        case_ids: list[str] = []
        cluster_ids: list[str] = []
        calls_used = 0
        confirm_hook = getattr(self.backend, "confirm_composite", None)
        for case, stage_rows in selected:
            # The v2 protocol budgets three model calls per confirmation case.
            if (
                calls_already_used + calls_used + 3
                > self.deposition_confirmation_budget
            ):
                break
            if callable(confirm_hook):
                response = confirm_hook(case, composite)
                if (
                    isinstance(response, tuple)
                    and len(response) == 2
                    and isinstance(response[0], DualScoreExecution)
                ):
                    row, call_count = response
                else:
                    row, call_count = response, 3
            else:
                row = self.backend.evaluate(
                    case,
                    composite,
                    input_context=case.base_context,
                    origin_context=case.base_context,
                )
                call_count = 3
            call_count = int(call_count)
            if call_count < 0:
                raise ValueError("confirmation call count cannot be negative")
            calls_used += call_count
            stage_values = [
                float(stage_rows[skill_id].gold_free_gain)
                for skill_id in (
                    candidate.first_skill_id,
                    candidate.second_skill_id,
                )
                if skill_id in stage_rows
                and _finite(stage_rows[skill_id].gold_free_gain)
            ]
            case_ids.append(case.case_id)
            cluster_ids.append(case.family_id)
            replay_gains.append(
                float(row.gold_free_gain)
                if _finite(row.gold_free_gain)
                else None
            )
            stage_max_gains.append(
                max(stage_values) if len(stage_values) == 2 else None
            )

        finite_gains = tuple(
            float(value) for value in replay_gains if _finite(value)
        )
        complete = (
            len(case_ids) == self.deposition_confirmation_cases
            and len(finite_gains) == len(case_ids)
        )
        decision = governance.admit_with_cluster_replay(
            candidate.arena_id,
            candidate.composite_spec,
            finite_gains,
            generation=len(evidence_by_case),
        )
        d2_passed = bool(
            complete
            and decision.admitted
            and not decision.low_evidence
            and decision.ci_lower is not None
            and decision.ci_lower > 0.0
        )
        dominance_count = sum(
            gain is not None
            and stage is not None
            and float(gain) > float(stage)
            for gain, stage in zip(replay_gains, stage_max_gains)
        )
        dominance_rate = (
            dominance_count / len(case_ids) if case_ids else 0.0
        )
        d3_passed = bool(
            complete
            and dominance_rate >= self.deposition_marginal_dominance
        )
        deposited = d2_passed and d3_passed
        if deposited:
            reason = "d2_d3_passed"
        elif not complete:
            reason = "confirmation_incomplete_or_budget_exhausted"
        elif not d2_passed:
            reason = decision.reason
        else:
            reason = "marginal_dominance_failed"
        thresholds = {
            **dict(candidate.thresholds),
            "confirmation_cases": self.deposition_confirmation_cases,
            "max_candidates": self.deposition_max_candidates,
            "marginal_dominance": self.deposition_marginal_dominance,
            "confirmation_budget": self.deposition_confirmation_budget,
        }
        provenance = _event_provenance_sha256(
            {
                "candidate_provenance_sha256": (
                    candidate.provenance_sha256
                ),
                "checkpoint": checkpoint,
                "case_ids": case_ids,
                "thresholds": thresholds,
                "seed": self.seed,
                "source_sha256": source_sha256,
            }
        )
        return (
            DepositionConfirmationEvent(
                arena_id=candidate.arena_id,
                checkpoint=str(checkpoint),
                first_skill_id=candidate.first_skill_id,
                second_skill_id=candidate.second_skill_id,
                case_ids=tuple(case_ids),
                cluster_ids=tuple(cluster_ids),
                replay_gains=tuple(replay_gains),
                stage_max_gains=tuple(stage_max_gains),
                confirmation_ci_lower=decision.ci_lower,
                marginal_dominance_rate=dominance_rate,
                d2_passed=d2_passed,
                d3_passed=d3_passed,
                deposited=deposited,
                reason=reason,
                confirmation_calls=calls_used,
                cumulative_confirmation_calls=(
                    calls_already_used + calls_used
                ),
                thresholds=thresholds,
                seed=self.seed,
                source_sha256=source_sha256,
                provenance_sha256=provenance,
            ),
            deposited,
            calls_used,
        )

    def _evaluate_case(
        self,
        case: ArenaCase,
        *,
        removed_skill_id: str | None = None,
    ) -> _EvaluatedArenaCase:
        # Fill consumes no candidate calls and remains an explicit routed
        # abstention rather than a failed/no-repair CMD observation.
        candidates = (
            tuple(self.backend.candidates(case))
            if case.runtime_branch == "fix"
            else ()
        )
        if removed_skill_id is not None:
            candidates = tuple(
                candidate
                for candidate in candidates
                if candidate.skill_id != removed_skill_id
            )
        if self.evolve_selection_priors:
            candidates = tuple(
                sorted(
                    candidates,
                    key=lambda candidate: (
                        -self._selection_prior_mean(
                            case.family_id,
                            candidate.skill_id,
                        ),
                        candidate.skill_id,
                    ),
                )
            )
        if self.candidate_limit is not None:
            candidates = candidates[: self.candidate_limit]
        # Structural indications are constructed before any candidate is
        # evaluated.  They cannot observe current-case recovery outcomes.
        indications = _arena_structural_indications(
            case,
            item_gate_extractor=(
                self.item_gate_extractor
                if case.runtime_branch == "fix"
                else None
            ),
        )
        dual_by_skill: dict[str, DualScoreExecution] = {}
        count_reader = getattr(self.backend, "cmd_call_counts", None)
        before_counts = (
            tuple(count_reader(case))
            if callable(count_reader)
            else None
        )

        def evaluate(candidate: SkillCandidate, context: str) -> SkillExecution:
            dual = self.backend.evaluate(
                case,
                candidate,
                input_context=context,
                origin_context=case.base_context,
            )
            if dual.skill_id != candidate.skill_id:
                raise ValueError("backend returned a mismatched skill id")
            dual_by_skill[candidate.skill_id] = dual
            return _skill_execution(candidate, dual)

        result = AdditiveSaturationExecutor(
            saturation_threshold=self.saturation_threshold,
        ).execute(
            case_id=case.case_id,
            failure_type=case.failure_type,
            base_context=case.base_context,
            candidates=candidates,
            evaluator=evaluate,
        )
        structural_decision = route_structural_indications(
            candidates,
            {
                skill_id: row.gold_free_gain
                for skill_id, row in dual_by_skill.items()
            },
            indications,
            self.scope_policy,
            domain_fingerprint=case.arena_id,
            frozen_selected_ids=tuple(
                execution.skill_id for execution in result.selected
            ),
        )
        if structural_decision.signal_type is not None:
            selected_by_id = {
                execution.skill_id: execution for execution in result.executions
            }
            selected = tuple(
                selected_by_id[skill_id]
                for skill_id in structural_decision.selected_ids
                if skill_id in selected_by_id
            )
            selected_ids = {execution.skill_id for execution in selected}
            cumulative_gain = sum(
                float(execution.recovery_gain)
                for execution in selected
                if execution.has_finite_gain
            )
            result = AdditiveSaturationResult(
                case_id=result.case_id,
                failure_type=result.failure_type,
                executions=result.executions,
                selected=selected,
                rejected=tuple(
                    execution
                    for execution in result.executions
                    if execution.skill_id not in selected_ids
                ),
                cumulative_gain=cumulative_gain,
                saturation_threshold=result.saturation_threshold,
                covered=cumulative_gain >= result.saturation_threshold,
                repair_effective=bool(selected),
            )
        structural_events = indication_events(
            arena_id=case.arena_id,
            case_id=case.case_id,
            domain_fingerprint=case.arena_id,
            scope=self.scope_policy,
            indications=indications,
            decision=structural_decision,
        )
        if callable(count_reader) and before_counts is not None:
            after_counts = tuple(count_reader(case))
            cmd_answer_calls = int(after_counts[0]) - int(before_counts[0])
            cmd_selection_judge_calls = (
                int(after_counts[1]) - int(before_counts[1])
            )
            cmd_budget_source = "backend_call_counters"
        else:
            # Fixture/custom backends without counters retain a deterministic
            # logical fallback based on cache-distinct non-baseline contexts.
            logical_budget = len(
                {
                    row.repaired_context
                    for row in dual_by_skill.values()
                    if row.repaired_context != case.base_context
                }
            )
            cmd_answer_calls = logical_budget
            cmd_selection_judge_calls = logical_budget
            cmd_budget_source = "logical_fallback"
        chain_executions = (
            self._evaluate_chains(case, candidates, dual_by_skill)
            if self.enable_chains
            else ()
        )
        best_of_n = (
            self.backend.evaluate_best_of_n(
                case,
                candidate_count=cmd_answer_calls,
                origin_context=case.base_context,
            )
            if self.enable_best_of_n_control and cmd_answer_calls > 0
            else None
        )
        # Deliberately not gated on cmd_answer_calls: stuffing spends a fixed
        # single call by definition, so it is not budget-matched to CMD the way
        # best-of-N is, and a CMD arm that made no calls still needs the
        # baseline measured on that case.
        context_stuffing = (
            self.backend.evaluate_context_stuffing(
                case,
                origin_context=case.base_context,
            )
            if self.enable_context_stuffing_control
            else None
        )
        return _EvaluatedArenaCase(
            case=case,
            candidates=candidates,
            result=result,
            dual_by_skill=dual_by_skill,
            chain_executions=chain_executions,
            best_of_n=best_of_n,
            context_stuffing=context_stuffing,
            cmd_answer_calls=cmd_answer_calls,
            cmd_selection_judge_calls=cmd_selection_judge_calls,
            cmd_budget_source=cmd_budget_source,
            structural_indication_events=structural_events,
        )

    def _selection_prior_mean(
        self,
        family_id: str,
        skill_id: str,
    ) -> float:
        values = self._selection_prior_history.get(
            (str(family_id), str(skill_id)),
            (),
        )
        return fmean(values) if values else 0.0

    def _evaluate_chains(
        self,
        case: ArenaCase,
        candidates: Sequence[SkillCandidate],
        standalone: Mapping[str, DualScoreExecution],
    ) -> tuple[ChainExecution, ...]:
        output: list[ChainExecution] = []
        for first in candidates:
            for second in candidates:
                if first.skill_id == second.skill_id:
                    continue
                if _operator_family(first) == _operator_family(second):
                    continue
                if not self._chain_pair_scheduled(
                    case.case_id,
                    first.skill_id,
                    second.skill_id,
                ):
                    continue
                first_row = standalone[first.skill_id]
                second_row = standalone[second.skill_id]
                chained = self.backend.evaluate(
                    case,
                    second,
                    input_context=first_row.repaired_context,
                    origin_context=case.base_context,
                )
                standalone_values = [
                    float(value)
                    for value in (
                        first_row.gold_free_gain,
                        second_row.gold_free_gain,
                    )
                    if _finite(value)
                ]
                standalone_max = (
                    max(standalone_values) if standalone_values else None
                )
                chained_gain = (
                    float(chained.gold_free_gain)
                    if _finite(chained.gold_free_gain)
                    else None
                )
                benefit = (
                    chained_gain - standalone_max
                    if chained_gain is not None
                    and standalone_max is not None
                    else None
                )
                output.append(
                    ChainExecution(
                        first_skill_id=first.skill_id,
                        second_skill_id=second.skill_id,
                        chained_context=chained.repaired_context,
                        chained_gain=chained_gain,
                        standalone_max=standalone_max,
                        chain_benefit=benefit,
                        beneficial=(
                            benefit is not None
                            and benefit > self.deposition_min_benefit
                        ),
                        execution_cost=(
                            first_row.execution_cost
                            + chained.execution_cost
                        ),
                        status=chained.status,
                    )
                )
        return tuple(output)

    def _chain_pair_scheduled(
        self,
        case_id: str,
        first_skill_id: str,
        second_skill_id: str,
    ) -> bool:
        weight = self._chain_pair_weights.get(
            (first_skill_id, second_skill_id),
            1.0,
        )
        if weight >= 1.0:
            return True
        digest = hashlib.sha256(
            (
                f"{self.seed}\0{case_id}\0{first_skill_id}\0"
                f"{second_skill_id}"
            ).encode("utf-8")
        ).digest()
        draw = int.from_bytes(digest[:8], "big") / (2**64)
        return draw < weight


def arena_case_ids_sha256(case_ids: Sequence[str]) -> str:
    """Hash the ordered case-id sequence used by one arena run."""
    return _canonical_json_sha256([str(case_id) for case_id in case_ids])


def arena_file_sha256(path: str | Path) -> str:
    """Hash exact source bytes without loading the whole dataset into memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_arena_dataset_fingerprint(
    cases: Sequence[ArenaCase],
    *,
    source_path: str | Path | None,
) -> ArenaDatasetFingerprint:
    """Bind an artifact to both source bytes and the ordered selected stream."""
    selected_case_ids = [case.case_id for case in cases]
    selected_cases = [asdict(case) for case in cases]
    if source_path is None:
        source_kind = "in_memory"
        resolved_source = ""
        source_sha256 = None
        source_size_bytes = None
    else:
        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        source_kind = "file"
        resolved_source = str(source)
        source_sha256 = arena_file_sha256(source)
        source_size_bytes = source.stat().st_size
    return ArenaDatasetFingerprint(
        version=ARENA_DATASET_FINGERPRINT_VERSION,
        source_kind=source_kind,
        source_path=resolved_source,
        source_sha256=source_sha256,
        source_size_bytes=source_size_bytes,
        selected_case_ids_sha256=arena_case_ids_sha256(selected_case_ids),
        selected_cases_sha256=_canonical_json_sha256(selected_cases),
    )


def _canonical_json_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_arena_artifacts(result: ArenaRunResult, output: str | Path) -> Path:
    """Write one append-only JSONL stream with a manifest first."""
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = [
        {
            "record_type": "arena_manifest",
            **asdict(result.manifest),
        }
    ]
    rows.extend(row.to_dict() for row in result.gold_free_observations)
    rows.extend(row.to_dict() for row in result.saturation_events)
    rows.extend(row.to_dict() for row in result.ecology_snapshots)
    rows.extend(row.to_dict() for row in result.chain_attempts)
    rows.extend(row.to_dict() for row in result.coactivation_snapshots)
    rows.extend(row.to_dict() for row in result.deposition_candidate_events)
    rows.extend(
        row.to_dict() for row in result.deposition_confirmation_events
    )
    rows.extend(row.to_dict() for row in result.deposition_events)
    rows.extend(row.to_dict() for row in result.anti_pattern_events)
    rows.extend(row.to_dict() for row in result.perturbation_events)
    rows.extend(row.to_dict() for row in result.arm_comparison_events)
    rows.extend(row.to_dict() for row in result.structural_indication_events)
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                )
            )
            handle.write("\n")
    return target


def load_memtrace_arena_cases(
    path: str | Path,
    *,
    seed: int,
    limit: int = 0,
) -> tuple[ArenaCase, ...]:
    cases = tuple(load_probe_cases(path))
    families = build_families(cases)
    family_by_case = {
        member.case_id: family
        for family in families
        for member in family.members
    }
    members = family_stream(families, seed=seed)
    output = []
    for member in members[: limit or None]:
        family = family_by_case[member.case_id]
        question_type, evidence_condition = _split_condition(member.condition)
        output.append(
            _arena_case(
                "memtrace",
                member.case,
                family_id=family.family_id,
                subset="memtrace_kp",
                coordinates=ProbeCoordinates(
                    age_sessions=member.a_index,
                    question_type=question_type,
                    evidence_condition=evidence_condition,
                ),
            )
        )
    return tuple(output)


def load_memfail_arena_cases(
    path: str | Path,
    *,
    seed: int,
    limit: int = 0,
) -> tuple[ArenaCase, ...]:
    cases = list(load_probe_cases(path))
    random.Random(seed).shuffle(cases)
    output = []
    for case in cases[: limit or None]:
        parts = case.case_id.split("-")
        subset = parts[1] if len(parts) > 2 else "unknown"
        if subset.startswith("conditional_"):
            subset = "conditional"
        family_id = case.case_id.rsplit("-q", 1)[0]
        output.append(
            _arena_case(
                "memfail",
                case,
                family_id=family_id,
                subset=subset,
            )
        )
    return tuple(output)


def load_stale_arena_cases(
    path: str | Path,
    *,
    seed: int,
    limit: int = 0,
) -> tuple[ArenaCase, ...]:
    cases = list(load_probe_cases(path))
    random.Random(seed).shuffle(cases)
    return tuple(
        _arena_case(
            "stale",
            case,
            family_id=case.case_id.rsplit("-dim", 1)[0],
            subset="stale",
        )
        for case in cases[: limit or None]
    )


def _arena_case(
    arena_id: str,
    case: ProbeCase,
    *,
    family_id: str,
    subset: str,
    coordinates: ProbeCoordinates = ProbeCoordinates(),
) -> ArenaCase:
    memory_by_id = {item.memory_id: item for item in case.extracted_memory}
    recalled = [
        memory_by_id[memory_id].text
        for memory_id in case.primary_baseline.retrieved_memory_ids
        if memory_id in memory_by_id
    ]
    injected = case.primary_baseline.injected_context or "\n".join(recalled)
    retrieved_items = tuple(
        RetrievedItem(
            memory_id=memory_id,
            text=memory_by_id[memory_id].text,
        )
        for memory_id in case.primary_baseline.retrieved_memory_ids
        if memory_id in memory_by_id
    )
    hook_decision = post_retrieve_hook(case.query, retrieved_items)
    return ArenaCase(
        arena_id=arena_id,
        case_id=case.case_id,
        family_id=family_id,
        failure_type=str(case.perturbation_label or "null"),
        base_context=f"Query: {case.query}\n\nRetrieved Memory:\n{injected}",
        coordinates=coordinates,
        subset=subset,
        raw=_probe_case_mapping(case),
        runtime_branch=hook_decision.branch,
        hook_confidence=hook_decision.confidence,
    )


def _probe_case_mapping(case: ProbeCase) -> dict[str, object]:
    """Round-trip through public field serializers without carrying extensions."""
    return {
        "case_id": case.case_id,
        "query": case.query,
        "raw_events": [asdict(item) for item in case.raw_events],
        "extracted_memory": [asdict(item) for item in case.extracted_memory],
        "gold_evidence": [asdict(item) for item in case.gold_evidence],
        "gold_answer": case.gold_answer,
        "baseline_outputs": [asdict(item) for item in case.baseline_outputs],
        "perturbation_label": case.perturbation_label,
        "scoring": asdict(case.scoring),
        "has_ingestion_trace": case.has_ingestion_trace,
        "default_store": case.default_store,
        "granularity_levels": list(case.granularity_levels),
        "current_granularity": case.current_granularity,
        "safety_filter_blocked": case.safety_filter_blocked,
    }


def _arena_structural_indications(
    case: ArenaCase,
    *,
    item_gate_extractor: ItemGateExtractor | None = None,
) -> tuple[StructuralIndication, ...]:
    raw = case.raw
    items = tuple(
        MemoryItem.from_mapping(dict(item))
        for item in raw.get("extracted_memory", ())
    )
    by_id = {item.memory_id: item for item in items}
    baselines = tuple(raw.get("baseline_outputs") or ())
    baseline = next(
        (
            value
            for value in baselines
            if value.get("baseline_name") == "vector_memory"
        ),
        baselines[0] if baselines else {},
    )
    recalled = tuple(
        by_id[str(memory_id)]
        for memory_id in baseline.get("retrieved_memory_ids", ())
        if str(memory_id) in by_id
    )
    return extract_structural_indications(
        str(raw.get("query", "")),
        recalled,
        item_gate_extractor=item_gate_extractor,
    )


def _top_p_saturation_event(
    result: AdditiveSaturationResult,
    *,
    dual_by_skill: Mapping[str, DualScoreExecution],
    checkpoint: str,
    subset: str,
    runtime_branch: str,
) -> TopPSaturationEvent:
    selected_ids = tuple(item.skill_id for item in result.selected)
    positive = sorted(
        (
            item
            for item in result.executions
            if item.has_finite_gain and float(item.recovery_gain) > 0.0
        ),
        key=lambda item: (
            -float(item.recovery_gain),
            float(item.execution_cost),
            item.skill_id,
        ),
    )
    mean_selected = (
        sum(float(item.recovery_gain) for item in result.selected)
        / len(result.selected)
        if result.selected
        else None
    )
    selected_shadow_values = [
        dual_by_skill[skill_id].shadow_gold_gain for skill_id in selected_ids
    ]
    selected_shadow = (
        sum(float(value) for value in selected_shadow_values)
        if all(_finite(value) for value in selected_shadow_values)
        else None
    )
    all_shadow = {
        skill_id: row.shadow_gold_gain
        for skill_id, row in dual_by_skill.items()
    }
    shadow_complete = bool(all_shadow) and all(
        _finite(value) for value in all_shadow.values()
    )
    oracle_shadow = (
        _saturating_positive_sum(
            {
                skill_id: float(value)
                for skill_id, value in all_shadow.items()
            },
            threshold=result.saturation_threshold,
        )
        if shadow_complete
        else None
    )
    return TopPSaturationEvent(
        checkpoint=str(checkpoint),
        case_id=result.case_id,
        failure_type=result.failure_type,
        subset=str(subset),
        runtime_branch=str(runtime_branch),
        attempted_skill_ids=tuple(
            item.skill_id for item in result.executions
        ),
        finite_skill_ids=tuple(
            item.skill_id
            for item in result.executions
            if item.has_finite_gain
        ),
        positive_skill_ids=tuple(item.skill_id for item in positive),
        selected_skill_ids=selected_ids,
        rejected_skill_ids=tuple(item.skill_id for item in result.rejected),
        gold_free_gains=tuple(
            (item.skill_id, item.recovery_gain)
            for item in result.executions
        ),
        cumulative_gain=result.cumulative_gain,
        saturation_threshold=result.saturation_threshold,
        covered=result.covered,
        repair_effective=result.repair_effective,
        mean_selected_gain=mean_selected,
        shadow_selected_cumulative_gain=selected_shadow,
        shadow_oracle_cumulative_gain=oracle_shadow,
        shadow_selected_covered=(
            selected_shadow >= result.saturation_threshold
            if selected_shadow is not None
            else None
        ),
        shadow_oracle_covered=(
            oracle_shadow >= result.saturation_threshold
            if oracle_shadow is not None
            else None
        ),
        shadow_oracle_repair_effective=(
            any(float(value) > 0.0 for value in all_shadow.values())
            if shadow_complete
            else None
        ),
        shadow_regret=(
            max(0.0, oracle_shadow - selected_shadow)
            if oracle_shadow is not None and selected_shadow is not None
            else None
        ),
    )


def _saturating_positive_sum(
    values: Mapping[str, float],
    *,
    threshold: float,
) -> float:
    cumulative = 0.0
    for _skill_id, value in sorted(
        (
            (skill_id, float(value))
            for skill_id, value in values.items()
            if math.isfinite(float(value)) and float(value) > 0.0
        ),
        key=lambda item: (-item[1], item[0]),
    ):
        cumulative += value
        if cumulative >= threshold:
            break
    return cumulative


def _skill_execution(
    candidate: SkillCandidate,
    row: DualScoreExecution,
) -> SkillExecution:
    return SkillExecution(
        skill_id=candidate.skill_id,
        operator=candidate.operator,
        repaired_context=row.repaired_context,
        recovery_gain=row.gold_free_gain,
        execution_cost=float(row.execution_cost),
        success=_finite(row.gold_free_gain),
        status=row.status,
    )


def _operator_family(candidate: SkillCandidate) -> str:
    action = candidate.operator.last_action
    return action.value if action is not None else ""


def _select_confirmation_evidence(
    candidate: DepositionCandidate,
    *,
    chain_attempts: Sequence[ChainAttempt],
    evidence_by_case: Mapping[
        str,
        tuple[ArenaCase, Mapping[str, DualScoreExecution]],
    ],
    limit: int,
    seed: int,
) -> tuple[
    tuple[ArenaCase, Mapping[str, DualScoreExecution]],
    ...,
]:
    """Deterministic cluster-round-robin sample from D1 support cases."""
    support_ids = {
        row.case_id
        for row in chain_attempts
        if row.first_skill_id == candidate.first_skill_id
        and row.second_skill_id == candidate.second_skill_id
        and row.chain_benefit is not None
    }
    by_cluster: dict[
        str,
        list[tuple[ArenaCase, Mapping[str, DualScoreExecution]]],
    ] = {}
    for case_id in sorted(support_ids):
        evidence = evidence_by_case.get(case_id)
        if evidence is None:
            continue
        case, _rows = evidence
        by_cluster.setdefault(case.family_id, []).append(evidence)
    cluster_ids = sorted(by_cluster)
    pair_seed = int(
        hashlib.sha256(
            (
                f"{seed}\0{candidate.first_skill_id}\0"
                f"{candidate.second_skill_id}"
            ).encode("utf-8")
        ).hexdigest()[:16],
        16,
    )
    random.Random(pair_seed).shuffle(cluster_ids)
    selected = []
    offset = 0
    while len(selected) < limit:
        added = False
        for cluster_id in cluster_ids:
            rows = by_cluster[cluster_id]
            if offset < len(rows):
                selected.append(rows[offset])
                added = True
                if len(selected) >= limit:
                    break
        if not added:
            break
        offset += 1
    return tuple(selected)


def _event_provenance_sha256(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite(value: object) -> bool:
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _split_condition(value: str) -> tuple[str, str]:
    for evidence in ("present", "contradicted", "missing"):
        suffix = f"-{evidence}"
        if value.endswith(suffix):
            return value[: -len(suffix)], evidence
    return value, ""


def _select_removed_skill(
    events: Sequence[TopPSaturationEvent],
    *,
    strategy: str,
) -> str | None:
    skills = sorted(
        {
            skill_id
            for event in events
            for skill_id in event.attempted_skill_ids
        }
    )
    if not skills:
        return None
    attempts = {
        skill_id: sum(
            skill_id in event.attempted_skill_ids for event in events
        )
        for skill_id in skills
    }
    selections_by_failure: dict[str, dict[str, int]] = {
        skill_id: {} for skill_id in skills
    }
    for event in events:
        for skill_id in event.selected_skill_ids:
            counts = selections_by_failure[skill_id]
            counts[event.failure_type] = counts.get(event.failure_type, 0) + 1
    if not any(selections_by_failure[skill_id] for skill_id in skills):
        return None
    if strategy == "keystone":
        return min(
            skills,
            key=lambda skill_id: (
                -sum(selections_by_failure[skill_id].values())
                / max(1, attempts[skill_id]),
                skill_id,
            ),
        )
    if strategy != "specialist":
        raise ValueError("unknown perturbation strategy")
    return min(
        skills,
        key=lambda skill_id: (
            -_specialization_index(selections_by_failure[skill_id]),
            skill_id,
        ),
    )


def _specialization_index(wins: Mapping[str, int]) -> float:
    values = [value for value in wins.values() if value > 0]
    total = sum(values)
    if total == 0:
        return 0.0
    if len(values) <= 1:
        return 1.0
    probabilities = [value / total for value in values]
    entropy = -sum(value * math.log(value) for value in probabilities)
    return 1.0 - entropy / math.log(len(values))
