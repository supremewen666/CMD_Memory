"""Target-audited transfer and true sequential-composition graph."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import fmean
import math
import random
from typing import Literal, Sequence


AttemptKind = Literal["transfer", "composition"]


@dataclass(frozen=True)
class TransferEvidence:
    case_id: str
    family_id: str
    source_gain: float
    target_incumbent_gain: float
    execution_cost: float
    anchor_regression: bool = False

    def __post_init__(self) -> None:
        _validate_identity_and_numbers(
            self.case_id,
            self.family_id,
            self.source_gain,
            self.target_incumbent_gain,
            self.execution_cost,
        )


@dataclass(frozen=True)
class CompositionEvidence:
    case_id: str
    family_id: str
    first_gain: float
    second_gain: float
    composed_gain: float
    execution_cost: float
    cost_budget: float
    executed_intermediate: bool
    anchor_regression: bool = False

    def __post_init__(self) -> None:
        _validate_identity_and_numbers(
            self.case_id,
            self.family_id,
            self.first_gain,
            self.second_gain,
            self.composed_gain,
            self.execution_cost,
            self.cost_budget,
        )
        if self.execution_cost < 0.0 or self.cost_budget < 0.0:
            raise ValueError("execution cost and budget must be non-negative")

    @property
    def chain_gain(self) -> float:
        return self.composed_gain - max(self.first_gain, self.second_gain)


@dataclass(frozen=True)
class SkillGraphEdge:
    edge_id: str
    kind: AttemptKind
    source_niche_id: str
    target_niche_id: str
    source_revision_id: str
    target_revision_id: str
    observations: int
    families: int
    estimate: float
    lower_bound: float


@dataclass(frozen=True)
class SkillGraphAttempt:
    kind: AttemptKind
    source_niche_id: str
    target_niche_id: str
    source_revision_id: str
    target_revision_id: str
    decision: str
    observations: int
    families: int
    estimate: float | None
    lower_bound: float | None
    edge_id: str | None


class AuditedSkillGraph:
    """Append-only attempts; only passing target audits create active edges."""

    def __init__(
        self,
        *,
        success_threshold: float = 0.1,
        confidence: float = 0.95,
        bootstrap_samples: int = 2000,
        seed: int = 0,
    ) -> None:
        if success_threshold < 0.0:
            raise ValueError("success_threshold must be non-negative")
        if not 0.0 < confidence < 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if bootstrap_samples < 100:
            raise ValueError("bootstrap_samples must be >= 100")
        self.success_threshold = float(success_threshold)
        self.confidence = float(confidence)
        self.bootstrap_samples = int(bootstrap_samples)
        self.seed = int(seed)
        self._edges: dict[str, SkillGraphEdge] = {}
        self._attempts: list[SkillGraphAttempt] = []

    @property
    def edges(self) -> tuple[SkillGraphEdge, ...]:
        return tuple(self._edges[key] for key in sorted(self._edges))

    @property
    def attempts(self) -> tuple[SkillGraphAttempt, ...]:
        return tuple(self._attempts)

    def audit_transfer(
        self,
        *,
        source_niche_id: str,
        target_niche_id: str,
        source_revision_id: str,
        target_revision_id: str,
        evidence: Sequence[TransferEvidence],
    ) -> SkillGraphAttempt:
        self._validate_endpoints(source_niche_id, target_niche_id)
        rows = tuple(evidence)
        decision = self._support_decision(rows)
        estimate: float | None = None
        lower: float | None = None
        if decision is None and any(row.anchor_regression for row in rows):
            decision = "anchor_regression"
        if decision is None and fmean(row.source_gain for row in rows) < (
            self.success_threshold
        ):
            decision = "source_gain_below_threshold"
        if decision is None:
            values = tuple(
                (
                    row.family_id,
                    row.source_gain - row.target_incumbent_gain,
                )
                for row in rows
            )
            estimate = fmean(value for _family, value in values)
            lower = _family_blocked_lower(
                values,
                confidence=self.confidence,
                samples=self.bootstrap_samples,
                seed=self.seed,
            )
            decision = (
                "activate_transfer"
                if estimate > 0.0 and lower > 0.0
                else "target_noninferiority_failed"
            )
        return self._finish_attempt(
            kind="transfer",
            source_niche_id=source_niche_id,
            target_niche_id=target_niche_id,
            source_revision_id=source_revision_id,
            target_revision_id=target_revision_id,
            rows=rows,
            decision=decision,
            estimate=estimate,
            lower=lower,
        )

    def audit_composition(
        self,
        *,
        source_niche_id: str,
        target_niche_id: str,
        source_revision_id: str,
        target_revision_id: str,
        evidence: Sequence[CompositionEvidence],
    ) -> SkillGraphAttempt:
        self._validate_endpoints(source_niche_id, target_niche_id)
        rows = tuple(evidence)
        decision = self._support_decision(rows)
        estimate: float | None = None
        lower: float | None = None
        if decision is None and any(not row.executed_intermediate for row in rows):
            decision = "intermediate_not_executed"
        if decision is None and any(row.anchor_regression for row in rows):
            decision = "anchor_regression"
        if decision is None and any(
            row.execution_cost > row.cost_budget for row in rows
        ):
            decision = "cost_budget_exceeded"
        if decision is None:
            values = tuple(
                (row.family_id, row.chain_gain) for row in rows
            )
            estimate = fmean(value for _family, value in values)
            lower = _family_blocked_lower(
                values,
                confidence=self.confidence,
                samples=self.bootstrap_samples,
                seed=self.seed + 1,
            )
            decision = (
                "activate_composition"
                if estimate > 0.0 and lower > 0.0
                else "incremental_benefit_failed"
            )
        return self._finish_attempt(
            kind="composition",
            source_niche_id=source_niche_id,
            target_niche_id=target_niche_id,
            source_revision_id=source_revision_id,
            target_revision_id=target_revision_id,
            rows=rows,
            decision=decision,
            estimate=estimate,
            lower=lower,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "version": "sigil-skill-graph-v1",
            "config": {
                "success_threshold": self.success_threshold,
                "confidence": self.confidence,
                "bootstrap_samples": self.bootstrap_samples,
                "seed": self.seed,
            },
            "edges": [asdict(edge) for edge in self.edges],
            "attempts": [asdict(attempt) for attempt in self.attempts],
        }

    def _support_decision(self, rows: Sequence[object]) -> str | None:
        if len(rows) < 3:
            return "insufficient_observations"
        if len({str(getattr(row, "family_id")) for row in rows}) < 2:
            return "insufficient_families"
        return None

    def _finish_attempt(
        self,
        *,
        kind: AttemptKind,
        source_niche_id: str,
        target_niche_id: str,
        source_revision_id: str,
        target_revision_id: str,
        rows: Sequence[object],
        decision: str,
        estimate: float | None,
        lower: float | None,
    ) -> SkillGraphAttempt:
        active = decision in {"activate_transfer", "activate_composition"}
        edge_id = (
            f"{kind}:{source_revision_id}->{target_revision_id}"
            if active
            else None
        )
        if active and self._would_create_cycle(
            source_niche_id,
            target_niche_id,
        ):
            decision = "cycle_rejected"
            edge_id = None
            active = False
        if active and edge_id is not None:
            self._edges[edge_id] = SkillGraphEdge(
                edge_id=edge_id,
                kind=kind,
                source_niche_id=source_niche_id,
                target_niche_id=target_niche_id,
                source_revision_id=source_revision_id,
                target_revision_id=target_revision_id,
                observations=len(rows),
                families=len(
                    {str(getattr(row, "family_id")) for row in rows}
                ),
                estimate=float(estimate),
                lower_bound=float(lower),
            )
        attempt = SkillGraphAttempt(
            kind=kind,
            source_niche_id=source_niche_id,
            target_niche_id=target_niche_id,
            source_revision_id=source_revision_id,
            target_revision_id=target_revision_id,
            decision=decision,
            observations=len(rows),
            families=len(
                {str(getattr(row, "family_id")) for row in rows}
            ),
            estimate=estimate,
            lower_bound=lower,
            edge_id=edge_id,
        )
        self._attempts.append(attempt)
        return attempt

    def _validate_endpoints(self, source: str, target: str) -> None:
        if not source or not target:
            raise ValueError("source and target niches are required")
        if source == target:
            raise ValueError("graph edges require distinct niches")

    def _would_create_cycle(self, source: str, target: str) -> bool:
        adjacency: dict[str, set[str]] = {}
        for edge in self._edges.values():
            adjacency.setdefault(edge.source_niche_id, set()).add(
                edge.target_niche_id
            )
        stack = [target]
        visited: set[str] = set()
        while stack:
            current = stack.pop()
            if current == source:
                return True
            if current in visited:
                continue
            visited.add(current)
            stack.extend(adjacency.get(current, ()))
        return False


def _family_blocked_lower(
    values: Sequence[tuple[str, float]],
    *,
    confidence: float,
    samples: int,
    seed: int,
) -> float:
    by_family: dict[str, list[float]] = {}
    for family_id, value in values:
        by_family.setdefault(family_id, []).append(float(value))
    family_means = tuple(
        fmean(by_family[key]) for key in sorted(by_family)
    )
    rng = random.Random(seed)
    draws = sorted(
        fmean(
            family_means[rng.randrange(len(family_means))]
            for _ in family_means
        )
        for _ in range(samples)
    )
    index = max(
        0,
        min(
            len(draws) - 1,
            int((1.0 - confidence) * len(draws)),
        ),
    )
    return draws[index]


def _validate_identity_and_numbers(
    case_id: str,
    family_id: str,
    *values: float,
) -> None:
    if not case_id or not family_id:
        raise ValueError("case_id and family_id are required")
    if any(not math.isfinite(value) for value in values):
        raise ValueError("graph evidence values must be finite")
    if values and values[-1] < 0.0:
        raise ValueError("execution cost must be non-negative")
