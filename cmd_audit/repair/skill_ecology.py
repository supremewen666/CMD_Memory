"""Deterministic competitive execution and observational skill ecology."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Callable, Iterable, Sequence

from ..counterfactual.operators import OperatorSpec


@dataclass(frozen=True)
class SkillCandidate:
    skill_id: str
    operator: OperatorSpec


@dataclass(frozen=True)
class SkillExecution:
    skill_id: str
    operator: OperatorSpec
    repaired_context: str
    recovery_gain: float | None
    execution_cost: float
    success: bool
    status: str = "ok"

    @property
    def has_finite_gain(self) -> bool:
        if self.recovery_gain is None:
            return False
        try:
            return math.isfinite(float(self.recovery_gain))
        except (TypeError, ValueError):
            return False


@dataclass(frozen=True)
class CompetitiveResult:
    case_id: str
    failure_type: str
    executions: tuple[SkillExecution, ...]
    winner: SkillExecution | None
    runner_up: SkillExecution | None
    losers: tuple[SkillExecution, ...]
    winner_margin: float | None
    tied_skill_ids: tuple[str, ...]
    all_failed: bool
    abstained: bool
    abstention_reason: str


@dataclass(frozen=True)
class CompetitionEvent:
    checkpoint: str
    case_id: str
    failure_type: str
    attempted_skill_ids: tuple[str, ...]
    finite_skill_ids: tuple[str, ...]
    winner_skill_id: str | None
    loser_skill_ids: tuple[str, ...]
    tied_skill_ids: tuple[str, ...]
    winner_margin: float | None
    abstention_reason: str


@dataclass(frozen=True)
class NicheProfile:
    skill_id: str
    win_rates: tuple[tuple[str, float], ...]
    total_wins: int
    total_attempts: int
    dominant_niche: str | None
    specialization_index: float


@dataclass(frozen=True)
class NicheOverlap:
    skill_a: str
    skill_b: str
    cosine_similarity: float
    competitive: bool


@dataclass(frozen=True)
class EcologySnapshot:
    checkpoint: str
    event_count: int
    niches: tuple[NicheProfile, ...]
    overlaps: tuple[NicheOverlap, ...]
    winner_distribution: tuple[tuple[str, float], ...]
    diversity_index: float
    jsd_from_previous: float | None
    abstention_count: int

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["record_type"] = "ecology_snapshot"
        return value


@dataclass(frozen=True)
class ChainExecution:
    first_skill_id: str
    second_skill_id: str
    chained_context: str
    chained_gain: float | None
    standalone_max: float | None
    chain_benefit: float | None
    beneficial: bool
    execution_cost: float
    status: str


@dataclass(frozen=True)
class OperatorConflict:
    skill_a: str
    skill_b: str
    target: str
    action_a: str
    action_b: str


@dataclass(frozen=True)
class PerturbationEvent:
    arena_id: str
    removed_skill_id: str
    removal_strategy: str
    started_after_case: int
    window_size: int
    stability_threshold: float
    stable_windows_required: int
    recovered_after_cases: int | None
    winnerless_windows: int
    window_jsd: tuple[tuple[int, float], ...]

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["record_type"] = "perturbation_event"
        return value


SkillEvaluator = Callable[[SkillCandidate, str], SkillExecution]


class CompetitiveExecutor:
    """Execute top-K candidates independently from the same base context."""

    def __init__(
        self,
        *,
        top_k: int = 3,
        recovery_threshold: float = 0.1,
        tie_tolerance: float = 1e-12,
    ) -> None:
        if top_k <= 0:
            raise ValueError("top_k must be > 0")
        if tie_tolerance < 0:
            raise ValueError("tie_tolerance must be >= 0")
        self.top_k = int(top_k)
        self.recovery_threshold = float(recovery_threshold)
        self.tie_tolerance = float(tie_tolerance)

    def execute(
        self,
        *,
        case_id: str,
        failure_type: str,
        base_context: str,
        candidates: Sequence[SkillCandidate],
        evaluator: SkillEvaluator,
    ) -> CompetitiveResult:
        selected = tuple(candidates[: self.top_k])
        ids = [item.skill_id for item in selected]
        if len(ids) != len(set(ids)):
            raise ValueError("competitive candidates contain duplicate skill_id")
        executions: list[SkillExecution] = []
        for candidate in selected:
            execution = evaluator(candidate, base_context)
            if execution.skill_id != candidate.skill_id:
                raise ValueError("evaluator returned a mismatched skill_id")
            if execution.operator.content_hash() != candidate.operator.content_hash():
                raise ValueError("evaluator returned a mismatched operator")
            executions.append(execution)
        return select_competitive_winner(
            case_id=case_id,
            failure_type=failure_type,
            executions=executions,
            recovery_threshold=self.recovery_threshold,
            tie_tolerance=self.tie_tolerance,
        )


def select_competitive_winner(
    *,
    case_id: str,
    failure_type: str,
    executions: Sequence[SkillExecution],
    recovery_threshold: float = 0.1,
    tie_tolerance: float = 1e-12,
) -> CompetitiveResult:
    """Select a winner with explicit empty, missing, tie and all-failed states."""
    if tie_tolerance < 0:
        raise ValueError("tie_tolerance must be >= 0")
    all_executions = tuple(executions)
    if len({item.skill_id for item in all_executions}) != len(all_executions):
        raise ValueError("executions contain duplicate skill_id")
    finite = sorted(
        (item for item in all_executions if item.has_finite_gain),
        key=lambda item: (
            -float(item.recovery_gain),
            float(item.execution_cost),
            item.skill_id,
        ),
    )
    if not finite:
        return CompetitiveResult(
            case_id=case_id,
            failure_type=failure_type,
            executions=all_executions,
            winner=None,
            runner_up=None,
            losers=all_executions,
            winner_margin=None,
            tied_skill_ids=(),
            all_failed=True,
            abstained=True,
            abstention_reason="no_finite_gain",
        )
    best_gain = float(finite[0].recovery_gain)
    tied = tuple(
        item.skill_id
        for item in finite
        if abs(best_gain - float(item.recovery_gain)) <= tie_tolerance
    )
    runner_up = finite[1] if len(finite) > 1 else None
    margin = (
        best_gain - float(runner_up.recovery_gain)
        if runner_up is not None
        else None
    )
    if len(tied) > 1:
        return CompetitiveResult(
            case_id=case_id,
            failure_type=failure_type,
            executions=all_executions,
            winner=None,
            runner_up=runner_up,
            losers=all_executions,
            winner_margin=0.0,
            tied_skill_ids=tied,
            all_failed=best_gain < recovery_threshold,
            abstained=True,
            abstention_reason="tie",
        )
    if best_gain < recovery_threshold:
        return CompetitiveResult(
            case_id=case_id,
            failure_type=failure_type,
            executions=all_executions,
            winner=None,
            runner_up=runner_up,
            losers=all_executions,
            winner_margin=margin,
            tied_skill_ids=tied,
            all_failed=True,
            abstained=True,
            abstention_reason="all_failed",
        )
    winner = finite[0]
    return CompetitiveResult(
        case_id=case_id,
        failure_type=failure_type,
        executions=all_executions,
        winner=winner,
        runner_up=runner_up,
        losers=tuple(
            item for item in all_executions if item.skill_id != winner.skill_id
        ),
        winner_margin=margin,
        tied_skill_ids=tied,
        all_failed=False,
        abstained=False,
        abstention_reason="",
    )


def evaluate_skill_chain(
    *,
    first: SkillCandidate,
    second: SkillCandidate,
    base_context: str,
    evaluator: SkillEvaluator,
    min_chain_benefit: float = 0.0,
    allow_same_family: bool = False,
) -> ChainExecution:
    """Execute ``A(base)`` and then ``B(A.output)`` with standalone controls."""
    first_family = (
        first.operator.last_action.value
        if first.operator.last_action is not None
        else ""
    )
    second_family = (
        second.operator.last_action.value
        if second.operator.last_action is not None
        else ""
    )
    if not allow_same_family and first_family == second_family:
        raise ValueError("chain requires different operator families")
    first_standalone = evaluator(first, base_context)
    second_standalone = evaluator(second, base_context)
    chained_second = evaluator(second, first_standalone.repaired_context)
    standalone_values = [
        float(item.recovery_gain)
        for item in (first_standalone, second_standalone)
        if item.has_finite_gain
    ]
    standalone_max = max(standalone_values) if standalone_values else None
    chained_gain = (
        float(chained_second.recovery_gain)
        if chained_second.has_finite_gain
        else None
    )
    benefit = (
        chained_gain - standalone_max
        if chained_gain is not None and standalone_max is not None
        else None
    )
    statuses = (
        first_standalone.status,
        second_standalone.status,
        chained_second.status,
    )
    return ChainExecution(
        first_skill_id=first.skill_id,
        second_skill_id=second.skill_id,
        chained_context=chained_second.repaired_context,
        chained_gain=chained_gain,
        standalone_max=standalone_max,
        chain_benefit=benefit,
        beneficial=benefit is not None and benefit > min_chain_benefit,
        execution_cost=(
            float(first_standalone.execution_cost)
            + float(chained_second.execution_cost)
        ),
        status="ok" if all(item == "ok" for item in statuses) else ",".join(statuses),
    )


def detect_operator_conflicts(
    left: SkillCandidate,
    right: SkillCandidate,
) -> tuple[OperatorConflict, ...]:
    """Detect contradictory actions on the same typed structural target."""
    conflicts: list[OperatorConflict] = []
    left_actions = left.operator.action_by_generation_point()
    right_actions = right.operator.action_by_generation_point()
    for generation_point in sorted(set(left_actions) & set(right_actions)):
        action_a = left_actions[generation_point]
        action_b = right_actions[generation_point]
        if action_a != action_b:
            conflicts.append(
                OperatorConflict(
                    skill_a=left.skill_id,
                    skill_b=right.skill_id,
                    target=f"generation_point:{generation_point}",
                    action_a=action_a.value,
                    action_b=action_b.value,
                )
            )
    left_hints = left.operator.item_signal_hints_dict()
    right_hints = right.operator.item_signal_hints_dict()
    for memory_id in sorted(set(left_hints) & set(right_hints)):
        if left_hints[memory_id] * right_hints[memory_id] < 0:
            conflicts.append(
                OperatorConflict(
                    skill_a=left.skill_id,
                    skill_b=right.skill_id,
                    target=f"memory_item:{memory_id}",
                    action_a="promote" if left_hints[memory_id] > 0 else "demote",
                    action_b="promote" if right_hints[memory_id] > 0 else "demote",
                )
            )
    return tuple(conflicts)


class EcologyTracker:
    """Append-only tracker for winner/loser events and niche snapshots."""

    def __init__(self, *, overlap_threshold: float = 0.7) -> None:
        if not 0 <= overlap_threshold <= 1:
            raise ValueError("overlap_threshold must be in [0, 1]")
        self.overlap_threshold = float(overlap_threshold)
        self._events: list[CompetitionEvent] = []
        self._last_winner_distribution: dict[str, float] | None = None

    @property
    def events(self) -> tuple[CompetitionEvent, ...]:
        return tuple(self._events)

    def record(self, result: CompetitiveResult, *, checkpoint: str) -> None:
        self._events.append(
            CompetitionEvent(
                checkpoint=str(checkpoint),
                case_id=result.case_id,
                failure_type=result.failure_type,
                attempted_skill_ids=tuple(
                    item.skill_id for item in result.executions
                ),
                finite_skill_ids=tuple(
                    item.skill_id
                    for item in result.executions
                    if item.has_finite_gain
                ),
                winner_skill_id=(
                    result.winner.skill_id if result.winner is not None else None
                ),
                loser_skill_ids=tuple(item.skill_id for item in result.losers),
                tied_skill_ids=result.tied_skill_ids,
                winner_margin=result.winner_margin,
                abstention_reason=result.abstention_reason,
            )
        )

    def snapshot(self, checkpoint: str) -> EcologySnapshot:
        failures = sorted({event.failure_type for event in self._events})
        skills = sorted(
            {
                skill_id
                for event in self._events
                for skill_id in event.attempted_skill_ids
            }
        )
        profiles: list[NicheProfile] = []
        vectors: dict[str, list[float]] = {}
        for skill_id in skills:
            rates: list[tuple[str, float]] = []
            total_attempts = 0
            total_wins = 0
            for failure_type in failures:
                attempts = sum(
                    skill_id in event.attempted_skill_ids
                    and event.failure_type == failure_type
                    for event in self._events
                )
                wins = sum(
                    event.winner_skill_id == skill_id
                    and event.failure_type == failure_type
                    for event in self._events
                )
                total_attempts += attempts
                total_wins += wins
                rates.append(
                    (failure_type, wins / attempts if attempts else 0.0)
                )
            vector = [rate for _failure, rate in rates]
            vectors[skill_id] = vector
            normalized = _normalize(vector)
            support = sum(value > 0 for value in normalized)
            if not normalized or sum(normalized) == 0:
                specialization = 0.0
            elif support <= 1:
                specialization = 1.0
            else:
                specialization = 1.0 - _entropy(normalized) / math.log(support)
            dominant = None
            if rates and max(rate for _failure, rate in rates) > 0:
                dominant = min(
                    failure
                    for failure, rate in rates
                    if rate == max(value for _name, value in rates)
                )
            profiles.append(
                NicheProfile(
                    skill_id=skill_id,
                    win_rates=tuple(rates),
                    total_wins=total_wins,
                    total_attempts=total_attempts,
                    dominant_niche=dominant,
                    specialization_index=specialization,
                )
            )
        overlaps = tuple(
            NicheOverlap(
                skill_a=left,
                skill_b=right,
                cosine_similarity=_cosine(vectors[left], vectors[right]),
                competitive=(
                    _cosine(vectors[left], vectors[right])
                    > self.overlap_threshold
                ),
            )
            for left_index, left in enumerate(skills)
            for right in skills[left_index + 1 :]
        )
        winner_counts = {
            skill_id: sum(
                event.winner_skill_id == skill_id for event in self._events
            )
            for skill_id in skills
        }
        winner_distribution = {
            skill_id: value
            for skill_id, value in zip(
                skills,
                _normalize([winner_counts[skill_id] for skill_id in skills]),
            )
        }
        jsd = None
        if self._last_winner_distribution is not None:
            jsd = jensen_shannon_divergence(
                self._last_winner_distribution,
                winner_distribution,
            )
        self._last_winner_distribution = dict(winner_distribution)
        return EcologySnapshot(
            checkpoint=str(checkpoint),
            event_count=len(self._events),
            niches=tuple(profiles),
            overlaps=overlaps,
            winner_distribution=tuple(sorted(winner_distribution.items())),
            diversity_index=_entropy(list(winner_distribution.values())),
            jsd_from_previous=jsd,
            abstention_count=sum(
                bool(event.abstention_reason) for event in self._events
            ),
        )


class EcologyObserver:
    """Checkpointed, append-only facade over :class:`EcologyTracker`.

    The observer receives completed competitive results.  It has no candidate
    provider, evaluator, or update callback and therefore cannot change the
    runtime execution path.
    """

    def __init__(
        self,
        *,
        arena_id: str,
        total_cases: int,
        checkpoint_fractions: Sequence[float] = (0.25, 0.5, 0.75, 1.0),
        overlap_threshold: float = 0.7,
    ) -> None:
        if total_cases <= 0:
            raise ValueError("total_cases must be > 0")
        fractions = tuple(float(value) for value in checkpoint_fractions)
        if not fractions or any(value <= 0 or value > 1 for value in fractions):
            raise ValueError("checkpoint fractions must be in (0, 1]")
        if tuple(sorted(set(fractions))) != fractions:
            raise ValueError("checkpoint fractions must be unique and sorted")
        self.arena_id = str(arena_id)
        self.total_cases = int(total_cases)
        self.tracker = EcologyTracker(overlap_threshold=overlap_threshold)
        self._boundaries = tuple(
            max(1, math.ceil(self.total_cases * fraction))
            for fraction in fractions
        )
        self._snapshots: list[EcologySnapshot] = []
        self._emitted_boundaries: set[int] = set()

    @property
    def snapshots(self) -> tuple[EcologySnapshot, ...]:
        return tuple(self._snapshots)

    @property
    def events(self) -> tuple[CompetitionEvent, ...]:
        return self.tracker.events

    def record(
        self,
        result: CompetitiveResult,
        *,
        stream_position: int,
    ) -> EcologySnapshot | None:
        if stream_position <= 0 or stream_position > self.total_cases:
            raise ValueError("stream_position outside declared arena")
        checkpoint = self._checkpoint_name(stream_position)
        self.tracker.record(result, checkpoint=checkpoint)
        if (
            stream_position in self._boundaries
            and stream_position not in self._emitted_boundaries
        ):
            snapshot = self.tracker.snapshot(checkpoint)
            self._snapshots.append(snapshot)
            self._emitted_boundaries.add(stream_position)
            return snapshot
        return None

    def finalize(self) -> EcologySnapshot:
        if not self.tracker.events:
            raise ValueError("cannot finalize an empty ecology")
        if self.total_cases in self._emitted_boundaries:
            return self._snapshots[-1]
        snapshot = self.tracker.snapshot(self._checkpoint_name(self.total_cases))
        self._snapshots.append(snapshot)
        self._emitted_boundaries.add(self.total_cases)
        return snapshot

    def _checkpoint_name(self, stream_position: int) -> str:
        return f"{self.arena_id}:{stream_position}/{self.total_cases}"


class PerturbationProbe:
    """Observe post-removal stabilization without performing the removal.

    The arena runner owns actuation.  This class only receives the winner
    stream after a declared removal and measures JSD between adjacent windows.
    """

    def __init__(
        self,
        *,
        arena_id: str,
        removed_skill_id: str,
        removal_strategy: str,
        started_after_case: int,
        window_size: int = 25,
        stability_threshold: float = 0.05,
        stable_windows_required: int = 2,
    ) -> None:
        if started_after_case < 0:
            raise ValueError("started_after_case must be >= 0")
        if window_size <= 0:
            raise ValueError("window_size must be > 0")
        if stability_threshold < 0:
            raise ValueError("stability_threshold must be >= 0")
        if stable_windows_required <= 0:
            raise ValueError("stable_windows_required must be > 0")
        self.arena_id = str(arena_id)
        self.removed_skill_id = str(removed_skill_id)
        self.removal_strategy = str(removal_strategy)
        self.started_after_case = int(started_after_case)
        self.window_size = int(window_size)
        self.stability_threshold = float(stability_threshold)
        self.stable_windows_required = int(stable_windows_required)
        self._winners: list[str | None] = []
        self._previous_distribution: dict[str, float] | None = None
        self._window_jsd: list[tuple[int, float]] = []
        self._winnerless_windows = 0
        self._stable_run = 0
        self._recovered_after_cases: int | None = None

    def observe(self, *, stream_position: int, winner_skill_id: str | None) -> None:
        if stream_position <= self.started_after_case:
            raise ValueError("perturbation observation precedes removal")
        if winner_skill_id == self.removed_skill_id:
            raise ValueError("removed skill appeared as a post-removal winner")
        self._winners.append(winner_skill_id)
        if len(self._winners) % self.window_size:
            return
        distribution = _winner_distribution(
            self._winners[-self.window_size :]
        )
        if not distribution:
            self._winnerless_windows += 1
            self._stable_run = 0
            self._previous_distribution = distribution
            return
        if self._previous_distribution is not None:
            jsd = jensen_shannon_divergence(
                self._previous_distribution,
                distribution,
            )
            self._window_jsd.append((stream_position, jsd))
            self._stable_run = (
                self._stable_run + 1
                if jsd <= self.stability_threshold
                else 0
            )
            if (
                self._recovered_after_cases is None
                and self._stable_run >= self.stable_windows_required
            ):
                self._recovered_after_cases = (
                    stream_position - self.started_after_case
                )
        self._previous_distribution = distribution

    def result(self) -> PerturbationEvent:
        return PerturbationEvent(
            arena_id=self.arena_id,
            removed_skill_id=self.removed_skill_id,
            removal_strategy=self.removal_strategy,
            started_after_case=self.started_after_case,
            window_size=self.window_size,
            stability_threshold=self.stability_threshold,
            stable_windows_required=self.stable_windows_required,
            recovered_after_cases=self._recovered_after_cases,
            winnerless_windows=self._winnerless_windows,
            window_jsd=tuple(self._window_jsd),
        )


def jensen_shannon_divergence(
    left: dict[str, float],
    right: dict[str, float],
) -> float:
    keys = sorted(set(left) | set(right))
    p = _normalize([max(0.0, float(left.get(key, 0.0))) for key in keys])
    q = _normalize([max(0.0, float(right.get(key, 0.0))) for key in keys])
    if not keys or (sum(p) == 0 and sum(q) == 0):
        return 0.0
    midpoint = [(a + b) / 2.0 for a, b in zip(p, q)]
    return 0.5 * _kl(p, midpoint) + 0.5 * _kl(q, midpoint)


def _normalize(values: Sequence[float]) -> list[float]:
    total = sum(values)
    if total <= 0:
        return [0.0 for _value in values]
    return [float(value) / total for value in values]


def _entropy(probabilities: Sequence[float]) -> float:
    return -sum(
        value * math.log(value)
        for value in probabilities
        if value > 0
    )


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _kl(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(
        p * math.log(p / q)
        for p, q in zip(left, right)
        if p > 0 and q > 0
    )


def _winner_distribution(
    winners: Iterable[str | None],
) -> dict[str, float]:
    counts: dict[str, int] = {}
    total = 0
    for winner in winners:
        if winner is None:
            continue
        counts[winner] = counts.get(winner, 0) + 1
        total += 1
    if total == 0:
        return {}
    return {key: value / total for key, value in counts.items()}
