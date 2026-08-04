"""Observational records for skill co-activation and sequential chain gains."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from statistics import fmean
from typing import Mapping, Sequence
import warnings

from ..core.math_utils import finite_float, mean_finite
from .governance import _bootstrap_lower_bound
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
    family_id: str = ""

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
    n_support: int | None = None
    n_clusters: int | None = None
    sign_p: float | None = None
    ci_lower: float | None = None
    direction_p: float | None = None
    confirmation_ci_lower: float | None = None
    marginal_dominance_rate: float | None = None
    lifecycle_status: str = "probation"
    thresholds: Mapping[str, float | int] | None = None
    seed: int = 0
    source_sha256: str = ""
    provenance_sha256: str = ""

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["record_type"] = "chain_deposition_event"
        value["composite_spec"] = self.composite_spec.to_dict()
        return value


@dataclass(frozen=True)
class DepositionCandidate:
    """Auditable D1 decision for one directed pair at one checkpoint."""

    arena_id: str
    checkpoint: str
    first_skill_id: str
    second_skill_id: str
    n_support: int
    n_clusters: int
    positive_count: int
    negative_count: int
    sign_p: float
    median_chain_benefit: float
    ci_lower: float
    ci_upper: float
    reverse_mean_chain_benefit: float | None
    direction_pair_count: int
    direction_p: float | None
    passed: bool
    rejection_reasons: tuple[str, ...]
    anti_pattern: bool
    composite_spec_hash: str | None
    composite_spec: CompositeOperatorSpec | None
    thresholds: Mapping[str, float | int]
    seed: int
    source_sha256: str
    provenance_sha256: str

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["record_type"] = "deposition_candidate_event"
        value["composite_spec"] = (
            self.composite_spec.to_dict()
            if self.composite_spec is not None
            else None
        )
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
        self._candidate_events: list[DepositionCandidate] = []

    @property
    def attempts(self) -> tuple[ChainAttempt, ...]:
        return tuple(self._attempts)

    @property
    def snapshots(self) -> tuple[CoactivationSnapshot, ...]:
        return tuple(self._snapshots)

    @property
    def depositions(self) -> tuple[ChainDepositionEvent, ...]:
        return tuple(self._depositions)

    @property
    def candidate_events(self) -> tuple[DepositionCandidate, ...]:
        return tuple(self._candidate_events)

    def record_case(
        self,
        *,
        case_id: str,
        family_id: str | None = None,
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
                    family_id=str(family_id or failure_type or case_id),
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
        """Deprecated v1 greedy deposition retained for artifact compatibility."""
        warnings.warn(
            "deposit_best is deprecated; use promote_candidates plus "
            "materialize_deposition",
            DeprecationWarning,
            stacklevel=2,
        )
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

    def promote_candidates(
        self,
        *,
        candidates: Mapping[str, SkillCandidate],
        checkpoint: str,
        min_support: int = 10,
        min_clusters: int = 3,
        sign_alpha: float = 0.05,
        confidence: float = 0.95,
        direction_alpha: float = 0.10,
        bootstrap_samples: int = 2000,
        seed: int = 0,
        source_sha256: str = "",
    ) -> tuple[DepositionCandidate, ...]:
        """Evaluate every observed directed pair with the zero-call D1 gate."""
        if min_support < 1 or min_clusters < 1:
            raise ValueError("D1 support and cluster minima must be positive")
        if bootstrap_samples < 100:
            raise ValueError("bootstrap_samples must be >= 100")
        if not 0.0 < sign_alpha < 1.0:
            raise ValueError("sign_alpha must be between zero and one")
        if not 0.0 < direction_alpha < 1.0:
            raise ValueError("direction_alpha must be between zero and one")

        thresholds: dict[str, float | int] = {
            "min_support": int(min_support),
            "min_clusters": int(min_clusters),
            "sign_alpha": float(sign_alpha),
            "confidence": float(confidence),
            "direction_alpha": float(direction_alpha),
            "bootstrap_samples": int(bootstrap_samples),
        }
        grouped: dict[tuple[str, str], list[ChainAttempt]] = {}
        by_case_pair: dict[tuple[str, str, str], float] = {}
        for row in self._attempts:
            if row.chain_benefit is None:
                continue
            pair = (row.first_skill_id, row.second_skill_id)
            grouped.setdefault(pair, []).append(row)
            by_case_pair[(row.case_id, *pair)] = row.chain_benefit

        output: list[DepositionCandidate] = []
        for pair, rows in sorted(grouped.items()):
            values = tuple(float(row.chain_benefit) for row in rows)
            positive = sum(value > 0.0 for value in values)
            negative = sum(value < 0.0 for value in values)
            sign_p = _one_sided_sign_p(positive, negative)
            ci_lower = _bootstrap_lower_bound(
                values,
                confidence=confidence,
                samples=bootstrap_samples,
                seed=_pair_seed(seed, pair),
                aggregate="median",
            )
            ci_upper = -_bootstrap_lower_bound(
                tuple(-value for value in values),
                confidence=confidence,
                samples=bootstrap_samples,
                seed=_pair_seed(seed, pair),
                aggregate="median",
            )
            reverse_values: list[float] = []
            paired_differences: list[float] = []
            for row in rows:
                reverse = by_case_pair.get(
                    (row.case_id, pair[1], pair[0])
                )
                if reverse is None:
                    continue
                reverse_values.append(reverse)
                paired_differences.append(float(row.chain_benefit) - reverse)
            direction_p = (
                _one_sided_sign_p(
                    sum(value > 0.0 for value in paired_differences),
                    sum(value < 0.0 for value in paired_differences),
                )
                if paired_differences
                else None
            )
            reverse_mean = (
                fmean(reverse_values) if reverse_values else None
            )
            clusters = {row.family_id for row in rows if row.family_id}
            first = candidates.get(pair[0])
            second = candidates.get(pair[1])
            composite = (
                merge_operators((first.operator, second.operator))
                if first is not None and second is not None
                else None
            )
            reasons = []
            if len(values) < min_support:
                reasons.append("insufficient_support")
            if len(clusters) < min_clusters:
                reasons.append("insufficient_cluster_diversity")
            if sign_p >= sign_alpha:
                reasons.append("sign_test_failed")
            if ci_lower <= 0.0:
                reasons.append("ci_crosses_zero")
            if reverse_mean is None or fmean(values) <= reverse_mean:
                reasons.append("direction_mean_failed")
            if direction_p is None or direction_p >= direction_alpha:
                reasons.append("direction_test_failed")
            if composite is None:
                reasons.append("candidate_operator_unavailable")
            anti_pattern = len(values) >= min_support and ci_upper < 0.0
            provenance = _provenance_sha256(
                {
                    "arena_id": self.arena_id,
                    "checkpoint": checkpoint,
                    "pair": pair,
                    "thresholds": thresholds,
                    "seed": seed,
                    "source_sha256": source_sha256,
                }
            )
            output.append(
                DepositionCandidate(
                    arena_id=self.arena_id,
                    checkpoint=str(checkpoint),
                    first_skill_id=pair[0],
                    second_skill_id=pair[1],
                    n_support=len(values),
                    n_clusters=len(clusters),
                    positive_count=positive,
                    negative_count=negative,
                    sign_p=sign_p,
                    median_chain_benefit=float(
                        sorted(values)[len(values) // 2]
                        if len(values) % 2
                        else (
                            sorted(values)[len(values) // 2 - 1]
                            + sorted(values)[len(values) // 2]
                        )
                        / 2.0
                    ),
                    ci_lower=ci_lower,
                    ci_upper=ci_upper,
                    reverse_mean_chain_benefit=reverse_mean,
                    direction_pair_count=len(paired_differences),
                    direction_p=direction_p,
                    passed=not reasons,
                    rejection_reasons=tuple(reasons),
                    anti_pattern=anti_pattern,
                    composite_spec_hash=(
                        composite.content_hash() if composite is not None else None
                    ),
                    composite_spec=composite,
                    thresholds=thresholds,
                    seed=int(seed),
                    source_sha256=str(source_sha256),
                    provenance_sha256=provenance,
                )
            )
        self._candidate_events.extend(output)
        return tuple(output)

    def materialize_deposition(
        self,
        candidate: DepositionCandidate,
        *,
        deposited_after_case: int,
        confirmation_ci_lower: float,
        marginal_dominance_rate: float,
        lifecycle_status: str = "probation",
        provenance_sha256: str | None = None,
    ) -> ChainDepositionEvent:
        """Materialize a D1 survivor only after the D2 and D3 gates pass."""
        if not candidate.passed or candidate.composite_spec is None:
            raise ValueError("only a passed D1 candidate can be deposited")
        event = ChainDepositionEvent(
            arena_id=self.arena_id,
            deposited_after_case=int(deposited_after_case),
            composite_skill_id=(
                f"composite:{candidate.composite_spec.content_hash()[:16]}"
            ),
            first_skill_id=candidate.first_skill_id,
            second_skill_id=candidate.second_skill_id,
            supporting_attempts=candidate.n_support,
            mean_chain_benefit=float(
                fmean(
                    row.chain_benefit
                    for row in self._attempts
                    if row.chain_benefit is not None
                    and row.first_skill_id == candidate.first_skill_id
                    and row.second_skill_id == candidate.second_skill_id
                )
            ),
            composite_spec_hash=candidate.composite_spec.content_hash(),
            composite_spec=candidate.composite_spec,
            n_support=candidate.n_support,
            n_clusters=candidate.n_clusters,
            sign_p=candidate.sign_p,
            ci_lower=candidate.ci_lower,
            direction_p=candidate.direction_p,
            confirmation_ci_lower=float(confirmation_ci_lower),
            marginal_dominance_rate=float(marginal_dominance_rate),
            lifecycle_status=str(lifecycle_status),
            thresholds=candidate.thresholds,
            seed=candidate.seed,
            source_sha256=candidate.source_sha256,
            provenance_sha256=(
                str(provenance_sha256)
                if provenance_sha256 is not None
                else candidate.provenance_sha256
            ),
        )
        self._depositions.append(event)
        return event


def _one_sided_sign_p(positive: int, negative: int) -> float:
    """Exact P[X >= positive] under Binomial(n, 0.5), discarding ties."""
    n = int(positive) + int(negative)
    if n <= 0:
        return 1.0
    return sum(math.comb(n, k) for k in range(int(positive), n + 1)) / (2**n)


def _pair_seed(seed: int, pair: tuple[str, str]) -> int:
    digest = hashlib.sha256(
        f"{int(seed)}\0{pair[0]}\0{pair[1]}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big")


def _provenance_sha256(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
