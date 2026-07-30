"""Observational records for skill co-activation and sequential chain gains."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping, Sequence

from ..core.math_utils import finite_float, mean_finite
from .operator_library import CompositeOperatorSpec, merge_operators
from .skill_ecology import ChainExecution, SkillCandidate


@dataclass(frozen=True)
class ChainAttempt:
    arena_id: str
    case_id: str
    failure_type: str
    stream_position: int
    first_skill_id: str
    second_skill_id: str
    chain_benefit: float | None
    chained_gain: float | None
    standalone_max: float | None
    changed_item_count: int | None
    status: str

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["record_type"] = "chain_attempt"
        return value


@dataclass(frozen=True)
class CoactivationEdge:
    skill_a: str
    skill_b: str
    coactivation_count: int
    coactivation_rate: float


@dataclass(frozen=True)
class CoactivationSnapshot:
    arena_id: str
    checkpoint: str
    observed_cases: int
    nodes: tuple[str, ...]
    edges: tuple[CoactivationEdge, ...]

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["record_type"] = "coactivation_snapshot"
        return value


@dataclass(frozen=True)
class ChainDirectionality:
    skill_a: str
    skill_b: str
    a_to_b_count: int
    b_to_a_count: int
    mean_a_to_b_benefit: float | None
    mean_b_to_a_benefit: float | None
    direction_delta: float | None


@dataclass(frozen=True)
class ChainBenefitSpectrum:
    nonpositive: int
    weak_positive: int
    meaningful_positive: int
    missing_or_nonfinite: int
    weak_upper_bound: float
    meaningful_threshold: float


@dataclass(frozen=True)
class ChainDepositionEvent:
    arena_id: str
    deposited_after_case: int
    composite_skill_id: str
    first_skill_id: str
    second_skill_id: str
    supporting_attempts: int
    mean_chain_benefit: float
    composite_spec_hash: str
    composite_spec: CompositeOperatorSpec

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["record_type"] = "chain_deposition_event"
        value["composite_spec"] = self.composite_spec.to_dict()
        return value


class ChainObserver:
    """Append-only observer for experiments C1--C5."""

    def __init__(self, *, arena_id: str) -> None:
        self.arena_id = str(arena_id)
        self._observed_cases = 0
        self._coactivation_counts: dict[tuple[str, str], int] = {}
        self._nodes: set[str] = set()
        self._attempts: list[ChainAttempt] = []
        self._snapshots: list[CoactivationSnapshot] = []
        self._depositions: list[ChainDepositionEvent] = []

    @property
    def attempts(self) -> tuple[ChainAttempt, ...]:
        return tuple(self._attempts)

    @property
    def snapshots(self) -> tuple[CoactivationSnapshot, ...]:
        return tuple(self._snapshots)

    @property
    def depositions(self) -> tuple[ChainDepositionEvent, ...]:
        return tuple(self._depositions)

    def record_case(
        self,
        *,
        case_id: str,
        failure_type: str,
        stream_position: int,
        activated_skill_ids: Sequence[str],
        chain_executions: Sequence[ChainExecution],
        changed_item_counts: Mapping[tuple[str, str], int] | None = None,
    ) -> None:
        skill_ids = tuple(str(value) for value in activated_skill_ids)
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("activated skills contain duplicates")
        if stream_position <= self._observed_cases:
            raise ValueError("chain stream positions must be strictly increasing")
        self._observed_cases = int(stream_position)
        self._nodes.update(skill_ids)
        for left_index, left in enumerate(sorted(skill_ids)):
            for right in sorted(skill_ids)[left_index + 1 :]:
                key = (left, right)
                self._coactivation_counts[key] = (
                    self._coactivation_counts.get(key, 0) + 1
                )
        allowed = {
            (left, right)
            for left in skill_ids
            for right in skill_ids
            if left != right
        }
        seen_pairs: set[tuple[str, str]] = set()
        for execution in chain_executions:
            pair = (execution.first_skill_id, execution.second_skill_id)
            if pair not in allowed:
                raise ValueError("chain execution was not co-activated")
            if pair in seen_pairs:
                raise ValueError("duplicate directed chain execution")
            seen_pairs.add(pair)
            self._attempts.append(
                ChainAttempt(
                    arena_id=self.arena_id,
                    case_id=str(case_id),
                    failure_type=str(failure_type),
                    stream_position=int(stream_position),
                    first_skill_id=pair[0],
                    second_skill_id=pair[1],
                    chain_benefit=finite_float(execution.chain_benefit),
                    chained_gain=finite_float(execution.chained_gain),
                    standalone_max=finite_float(execution.standalone_max),
                    changed_item_count=(
                        (changed_item_counts or {}).get(pair)
                    ),
                    status=execution.status,
                )
            )

    def snapshot(self, checkpoint: str) -> CoactivationSnapshot:
        denominator = max(1, self._observed_cases)
        snapshot = CoactivationSnapshot(
            arena_id=self.arena_id,
            checkpoint=str(checkpoint),
            observed_cases=self._observed_cases,
            nodes=tuple(sorted(self._nodes)),
            edges=tuple(
                CoactivationEdge(
                    skill_a=left,
                    skill_b=right,
                    coactivation_count=count,
                    coactivation_rate=count / denominator,
                )
                for (left, right), count in sorted(
                    self._coactivation_counts.items()
                )
            ),
        )
        self._snapshots.append(snapshot)
        return snapshot

    def benefit_spectrum(
        self,
        *,
        weak_upper_bound: float = 0.05,
        meaningful_threshold: float = 0.05,
    ) -> ChainBenefitSpectrum:
        if weak_upper_bound < 0 or meaningful_threshold < weak_upper_bound:
            raise ValueError("invalid chain benefit thresholds")
        counts = {
            "nonpositive": 0,
            "weak": 0,
            "meaningful": 0,
            "missing": 0,
        }
        for row in self._attempts:
            value = row.chain_benefit
            if value is None:
                counts["missing"] += 1
            elif value <= 0:
                counts["nonpositive"] += 1
            elif value <= weak_upper_bound:
                counts["weak"] += 1
            else:
                counts["meaningful"] += 1
        return ChainBenefitSpectrum(
            nonpositive=counts["nonpositive"],
            weak_positive=counts["weak"],
            meaningful_positive=counts["meaningful"],
            missing_or_nonfinite=counts["missing"],
            weak_upper_bound=float(weak_upper_bound),
            meaningful_threshold=float(meaningful_threshold),
        )

    def directionality(self) -> tuple[ChainDirectionality, ...]:
        unordered = {
            tuple(sorted((row.first_skill_id, row.second_skill_id)))
            for row in self._attempts
        }
        output: list[ChainDirectionality] = []
        for skill_a, skill_b in sorted(unordered):
            forward = [
                row.chain_benefit
                for row in self._attempts
                if row.first_skill_id == skill_a
                and row.second_skill_id == skill_b
                and row.chain_benefit is not None
            ]
            reverse = [
                row.chain_benefit
                for row in self._attempts
                if row.first_skill_id == skill_b
                and row.second_skill_id == skill_a
                and row.chain_benefit is not None
            ]
            mean_forward = mean_finite(forward)
            mean_reverse = mean_finite(reverse)
            output.append(
                ChainDirectionality(
                    skill_a=skill_a,
                    skill_b=skill_b,
                    a_to_b_count=len(forward),
                    b_to_a_count=len(reverse),
                    mean_a_to_b_benefit=mean_forward,
                    mean_b_to_a_benefit=mean_reverse,
                    direction_delta=(
                        mean_forward - mean_reverse
                        if mean_forward is not None
                        and mean_reverse is not None
                        else None
                    ),
                )
            )
        return tuple(output)

    def deposit_best(
        self,
        *,
        candidates: Mapping[str, SkillCandidate],
        deposited_after_case: int,
        min_chain_benefit: float = 0.05,
        min_support: int = 3,
    ) -> ChainDepositionEvent | None:
        """Materialize the best supported directed chain as a staged composite."""
        if min_support <= 0:
            raise ValueError("min_support must be > 0")
        grouped: dict[tuple[str, str], list[float]] = {}
        for row in self._attempts:
            if (
                row.chain_benefit is not None
                and row.chain_benefit > min_chain_benefit
            ):
                grouped.setdefault(
                    (row.first_skill_id, row.second_skill_id),
                    [],
                ).append(row.chain_benefit)
        eligible = [
            (pair, values)
            for pair, values in grouped.items()
            if len(values) >= min_support
            and pair[0] in candidates
            and pair[1] in candidates
        ]
        if not eligible:
            return None
        # ``min`` over negative quality/support implements:
        # highest mean benefit -> highest support -> lexicographically smallest
        # pair. The final tie-break is intentionally stable across processes.
        pair, values = min(
            eligible,
            key=lambda item: (
                -float(mean_finite(item[1])),
                -len(item[1]),
                item[0],
            ),
        )
        composite = merge_operators(
            (
                candidates[pair[0]].operator,
                candidates[pair[1]].operator,
            )
        )
        event = ChainDepositionEvent(
            arena_id=self.arena_id,
            deposited_after_case=int(deposited_after_case),
            composite_skill_id=f"composite:{composite.content_hash()[:16]}",
            first_skill_id=pair[0],
            second_skill_id=pair[1],
            supporting_attempts=len(values),
            mean_chain_benefit=float(mean_finite(values)),
            composite_spec_hash=composite.content_hash(),
            composite_spec=composite,
        )
        self._depositions.append(event)
        return event
