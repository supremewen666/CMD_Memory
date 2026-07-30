"""Shared single-path runner for observational memory-repair arenas."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import random
from typing import Mapping, Protocol, Sequence

from cmd_audit.core.models import ProbeCase
from cmd_audit.data_io.probe_cases import load_probe_cases
from cmd_audit.eval.gold_free_observer import (
    GoldFreeObservation,
    GoldFreeObserver,
    ProbeCoordinates,
)
from cmd_audit.eval.gold_free_identifiability import (
    RuntimeSelectionProvenance,
)
from cmd_audit.repair.chain_dynamics import (
    ChainAttempt,
    ChainDepositionEvent,
    ChainObserver,
    CoactivationSnapshot,
)
from cmd_audit.repair.memtrace_families import build_families, family_stream
from cmd_audit.repair.skill_ecology import (
    ChainExecution,
    CompetitionEvent,
    CompetitiveExecutor,
    EcologyObserver,
    EcologySnapshot,
    PerturbationEvent,
    PerturbationProbe,
    SkillCandidate,
    SkillExecution,
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


@dataclass(frozen=True)
class DualScoreExecution:
    skill_id: str
    repaired_context: str
    gold_free_gain: float | None
    shadow_gold_gain: float | None
    execution_cost: float
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


@dataclass(frozen=True)
class ArenaManifest:
    arena_id: str
    case_count: int
    top_k: int
    gold_free_signal_name: str
    shadow_gold_signal_name: str
    runtime_uses_gold: bool
    chains_enabled: bool
    deposition_enabled: bool
    perturbation_enabled: bool
    perturbation_strategy: str
    seed: int


@dataclass(frozen=True)
class ArenaRunResult:
    manifest: ArenaManifest
    gold_free_observations: tuple[GoldFreeObservation, ...]
    competition_events: tuple[CompetitionEvent, ...]
    ecology_snapshots: tuple[EcologySnapshot, ...]
    chain_attempts: tuple[ChainAttempt, ...]
    coactivation_snapshots: tuple[CoactivationSnapshot, ...]
    deposition_events: tuple[ChainDepositionEvent, ...]
    perturbation_events: tuple[PerturbationEvent, ...]


class ObservationalArenaRunner:
    """Execute one immutable stream while append-only observers watch."""

    def __init__(
        self,
        cases: Sequence[ArenaCase],
        *,
        backend: DualScoreArenaBackend,
        top_k: int = 3,
        recovery_threshold: float = 0.1,
        seed: int = 24,
        enable_chains: bool = True,
        deposition_after_fraction: float | None = None,
        deposition_min_benefit: float = 0.05,
        deposition_min_support: int = 3,
        perturb_after_fraction: float | None = None,
        perturb_strategy: str = "keystone",
        perturb_window_size: int = 25,
        perturb_stability_threshold: float = 0.05,
        perturb_stable_windows: int = 2,
    ) -> None:
        if not cases:
            raise ValueError("arena requires at least one case")
        if backend.runtime_uses_gold:
            raise ValueError(
                "arena backend declares gold-dependent runtime selection"
            )
        if not backend.gold_free_signal_name:
            raise ValueError("backend must name the gold-free signal")
        if not backend.shadow_gold_signal_name:
            raise ValueError("backend must name the shadow-gold signal")
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
        if perturb_after_fraction is not None and not (
            0 < perturb_after_fraction < 1
        ):
            raise ValueError("perturbation fraction must be in (0, 1)")
        if perturb_strategy not in {"keystone", "specialist"}:
            raise ValueError("perturbation strategy must be keystone or specialist")
        self.cases = tuple(cases)
        self.backend = backend
        self.top_k = int(top_k)
        self.recovery_threshold = float(recovery_threshold)
        self.seed = int(seed)
        self.enable_chains = bool(enable_chains)
        self.deposition_after_fraction = deposition_after_fraction
        self.deposition_min_benefit = float(deposition_min_benefit)
        self.deposition_min_support = int(deposition_min_support)
        self.perturb_after_fraction = perturb_after_fraction
        self.perturb_strategy = str(perturb_strategy)
        self.perturb_window_size = int(perturb_window_size)
        self.perturb_stability_threshold = float(
            perturb_stability_threshold
        )
        self.perturb_stable_windows = int(perturb_stable_windows)

    def run(self) -> ArenaRunResult:
        arena_id = self.cases[0].arena_id
        if any(case.arena_id != arena_id for case in self.cases):
            raise ValueError("one runner cannot mix arena ids")
        gold_observer = GoldFreeObserver(arena_id=arena_id)
        ecology_observer = EcologyObserver(
            arena_id=arena_id,
            total_cases=len(self.cases),
        )
        chain_observer = ChainObserver(arena_id=arena_id)
        executor = CompetitiveExecutor(
            top_k=self.top_k,
            recovery_threshold=self.recovery_threshold,
        )
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

        for position, case in enumerate(self.cases, start=1):
            candidates = tuple(self.backend.candidates(case))
            if removed_skill_id is not None:
                candidates = tuple(
                    candidate
                    for candidate in candidates
                    if candidate.skill_id != removed_skill_id
                )
            candidates = candidates[: self.top_k]
            candidate_catalog.update(
                (candidate.skill_id, candidate) for candidate in candidates
            )
            dual_by_skill: dict[str, DualScoreExecution] = {}

            def evaluate(
                candidate: SkillCandidate,
                context: str,
            ) -> SkillExecution:
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

            result = executor.execute(
                case_id=case.case_id,
                failure_type=case.failure_type,
                base_context=case.base_context,
                candidates=candidates,
                evaluator=evaluate,
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
                runtime_abstained=result.abstained,
                coordinates=case.coordinates,
                runtime_provenance=RuntimeSelectionProvenance(
                    context_constructed_without_gold=True,
                    selection_used_gold=False,
                    shadow_scores_isolated=True,
                ),
            )
            snapshot = ecology_observer.record(
                result,
                stream_position=position,
            )
            if perturbation_probe is not None:
                perturbation_probe.observe(
                    stream_position=position,
                    winner_skill_id=(
                        result.winner.skill_id
                        if result.winner is not None
                        else None
                    ),
                )
            chain_executions = (
                self._evaluate_chains(case, candidates, dual_by_skill)
                if self.enable_chains
                else ()
            )
            chain_observer.record_case(
                case_id=case.case_id,
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
                deposition = chain_observer.deposit_best(
                    candidates=candidate_catalog,
                    deposited_after_case=position,
                    min_chain_benefit=self.deposition_min_benefit,
                    min_support=self.deposition_min_support,
                )
                if deposition is not None:
                    self.backend.deposit_composite(deposition)
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
                top_k=self.top_k,
                gold_free_signal_name=self.backend.gold_free_signal_name,
                shadow_gold_signal_name=self.backend.shadow_gold_signal_name,
                runtime_uses_gold=self.backend.runtime_uses_gold,
                chains_enabled=self.enable_chains,
                deposition_enabled=self.deposition_after_fraction is not None,
                perturbation_enabled=self.perturb_after_fraction is not None,
                perturbation_strategy=(
                    self.perturb_strategy
                    if self.perturb_after_fraction is not None
                    else ""
                ),
                seed=self.seed,
            ),
            gold_free_observations=gold_observer.observations,
            competition_events=ecology_observer.events,
            ecology_snapshots=ecology_observer.snapshots,
            chain_attempts=chain_observer.attempts,
            coactivation_snapshots=chain_observer.snapshots,
            deposition_events=chain_observer.depositions,
            perturbation_events=(
                (perturbation_probe.result(),)
                if perturbation_probe is not None
                else ()
            ),
        )

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
    rows.extend(
        {"record_type": "competition_event", **asdict(row)}
        for row in result.competition_events
    )
    rows.extend(row.to_dict() for row in result.ecology_snapshots)
    rows.extend(row.to_dict() for row in result.chain_attempts)
    rows.extend(row.to_dict() for row in result.coactivation_snapshots)
    rows.extend(row.to_dict() for row in result.deposition_events)
    rows.extend(row.to_dict() for row in result.perturbation_events)
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
    return ArenaCase(
        arena_id=arena_id,
        case_id=case.case_id,
        family_id=family_id,
        failure_type=str(case.perturbation_label or "null"),
        base_context=f"Query: {case.query}\n\nRetrieved Memory:\n{injected}",
        coordinates=coordinates,
        subset=subset,
        raw=_probe_case_mapping(case),
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
    events: Sequence[CompetitionEvent],
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
    wins_by_failure: dict[str, dict[str, int]] = {
        skill_id: {} for skill_id in skills
    }
    for event in events:
        if event.winner_skill_id is not None:
            counts = wins_by_failure[event.winner_skill_id]
            counts[event.failure_type] = counts.get(event.failure_type, 0) + 1
    if not any(wins_by_failure[skill_id] for skill_id in skills):
        return None
    if strategy == "keystone":
        return min(
            skills,
            key=lambda skill_id: (
                -sum(wins_by_failure[skill_id].values())
                / max(1, attempts[skill_id]),
                skill_id,
            ),
        )
    if strategy != "specialist":
        raise ValueError("unknown perturbation strategy")
    return min(
        skills,
        key=lambda skill_id: (
            -_specialization_index(wins_by_failure[skill_id]),
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
