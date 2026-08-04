"""Governance for reusable repair operators.

The governance layer is deliberately independent from experiment runners.  It
implements the five A4 controls in one place: cluster replay admission,
evidence accounting, retirement, an active-shape cap, content-hash
deduplication, and a bootstrap confidence gate.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import random
from statistics import fmean, median
from typing import Iterable

from ..counterfactual.operators import OperatorSpec


@dataclass(frozen=True)
class GovernanceDecision:
    admitted: bool
    reason: str
    operator_hash: str
    observations: int
    mean_gain: float
    ci_lower: float | None
    low_evidence: bool
    deduplicated: bool = False
    retired_hashes: tuple[str, ...] = ()


@dataclass
class OperatorLedgerEntry:
    fingerprint: str
    operator: OperatorSpec
    operator_hash: str
    replay_observations: int = 0
    replay_gain_sum: float = 0.0
    applied: int = 0
    succeeded: int = 0
    consecutive_failures: int = 0
    last_success_generation: int | None = None
    admitted_generation: int = 0
    low_evidence: bool = False
    retired: bool = False

    @property
    def average_gain(self) -> float:
        if not self.replay_observations:
            return 0.0
        return self.replay_gain_sum / self.replay_observations

    @property
    def success_rate(self) -> float:
        if not self.applied:
            return 0.0
        return self.succeeded / self.applied

    @property
    def eta(self) -> float:
        """Laplace-smoothed live reliability used by the lifecycle."""
        return (self.succeeded + 1) / (self.applied + 2)

    @property
    def probation(self) -> bool:
        """New operators remain searchable but discounted until proven live."""
        return not self.retired and not (
            self.applied >= 4 and self.eta >= 0.5
        )

    @property
    def lifecycle_status(self) -> str:
        if self.retired:
            return "retired"
        return "probation" if self.probation else "active"

    @property
    def ranking_score(self) -> tuple[float, float, float, int, int]:
        return (
            self.eta,
            0.0 if self.probation else 1.0,
            self.average_gain,
            self.last_success_generation or -1,
            -self.admitted_generation,
        )


class OperatorGovernance:
    """Mutable evidence ledger and admission gate for operator shapes."""

    def __init__(
        self,
        *,
        active_cap: int = 5,
        retirement_patience: int = 5,
        confidence: float = 0.95,
        bootstrap_samples: int = 2000,
        seed: int = 0,
    ) -> None:
        if active_cap < 1:
            raise ValueError("active_cap must be >= 1")
        if retirement_patience < 1:
            raise ValueError("retirement_patience must be >= 1")
        if not 0.0 < confidence < 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if bootstrap_samples < 100:
            raise ValueError("bootstrap_samples must be >= 100")
        self.active_cap = active_cap
        self.retirement_patience = retirement_patience
        self.confidence = confidence
        self.bootstrap_samples = bootstrap_samples
        self.seed = seed
        self._entries: dict[tuple[str, str], OperatorLedgerEntry] = {}
        self.dedup_hits = 0

    def admit_with_cluster_replay(
        self,
        fingerprint: str,
        operator: OperatorSpec,
        replay_gains: Iterable[float],
        *,
        generation: int = 0,
    ) -> GovernanceDecision:
        """Admit an operator only when replay evidence has positive net value.

        Three or more observations use a bootstrap lower confidence bound.
        Smaller clusters use their point estimate and are explicitly marked
        ``low_evidence`` so experiments cannot silently present them as
        confidence-gated admissions.
        """
        gains = tuple(
            float(value)
            for value in replay_gains
            if math.isfinite(float(value))
        )
        operator_hash = operator.content_hash()
        key = (fingerprint, operator_hash)
        existing = self._entries.get(key)
        mean_gain = fmean(gains) if gains else 0.0
        low_evidence = len(gains) < 3
        ci_lower = (
            None
            if low_evidence
            else _bootstrap_lower_bound(
                gains,
                confidence=self.confidence,
                samples=self.bootstrap_samples,
                seed=self.seed,
            )
        )

        if existing is not None:
            self.dedup_hits += 1
            existing.replay_observations += len(gains)
            existing.replay_gain_sum += sum(gains)
            existing.low_evidence = existing.low_evidence and low_evidence
            return GovernanceDecision(
                admitted=False,
                reason="duplicate_operator",
                operator_hash=operator_hash,
                observations=len(gains),
                mean_gain=mean_gain,
                ci_lower=ci_lower,
                low_evidence=low_evidence,
                deduplicated=True,
            )

        admitted = bool(gains) and (
            mean_gain > 0.0 if low_evidence else bool(ci_lower and ci_lower > 0.0)
        )
        if not admitted:
            return GovernanceDecision(
                admitted=False,
                reason="non_positive_point_estimate" if low_evidence else "ci_crosses_zero",
                operator_hash=operator_hash,
                observations=len(gains),
                mean_gain=mean_gain,
                ci_lower=ci_lower,
                low_evidence=low_evidence,
            )

        self._entries[key] = OperatorLedgerEntry(
            fingerprint=fingerprint,
            operator=operator,
            operator_hash=operator_hash,
            replay_observations=len(gains),
            replay_gain_sum=sum(gains),
            admitted_generation=generation,
            low_evidence=low_evidence,
        )
        retired = self._enforce_active_cap(fingerprint)
        admitted_entry = self._entries[key]
        return GovernanceDecision(
            admitted=not admitted_entry.retired,
            reason="admitted" if not admitted_entry.retired else "retired_by_active_cap",
            operator_hash=operator_hash,
            observations=len(gains),
            mean_gain=mean_gain,
            ci_lower=ci_lower,
            low_evidence=low_evidence,
            retired_hashes=retired,
        )

    def record_application(
        self,
        fingerprint: str,
        operator_hash: str,
        *,
        succeeded: bool,
        generation: int,
    ) -> OperatorLedgerEntry:
        entry = self._entries[(fingerprint, operator_hash)]
        entry.applied += 1
        if succeeded:
            entry.succeeded += 1
            entry.consecutive_failures = 0
            entry.last_success_generation = generation
        else:
            entry.consecutive_failures += 1
            if entry.consecutive_failures >= 2 * self.retirement_patience:
                entry.retired = True
        return entry

    def active_operators(self, fingerprint: str) -> tuple[OperatorSpec, ...]:
        entries = [
            entry
            for entry in self._entries.values()
            if entry.fingerprint == fingerprint and not entry.retired
        ]
        entries.sort(key=lambda entry: entry.ranking_score, reverse=True)
        return tuple(entry.operator for entry in entries)

    def entries(self, fingerprint: str | None = None) -> tuple[OperatorLedgerEntry, ...]:
        values = tuple(self._entries.values())
        if fingerprint is None:
            return values
        return tuple(entry for entry in values if entry.fingerprint == fingerprint)

    def _enforce_active_cap(self, fingerprint: str) -> tuple[str, ...]:
        active = [
            entry
            for entry in self._entries.values()
            if entry.fingerprint == fingerprint and not entry.retired
        ]
        if len(active) <= self.active_cap:
            return ()
        active.sort(key=lambda entry: entry.ranking_score)
        retired = []
        for entry in active[: len(active) - self.active_cap]:
            entry.retired = True
            retired.append(entry.operator_hash)
        return tuple(retired)


def _bootstrap_lower_bound(
    gains: tuple[float, ...],
    *,
    confidence: float,
    samples: int,
    seed: int,
    aggregate: str = "mean",
) -> float:
    if aggregate not in {"mean", "median"}:
        raise ValueError("aggregate must be 'mean' or 'median'")
    rng = random.Random(seed)
    n = len(gains)
    statistic = fmean if aggregate == "mean" else median
    estimates = sorted(
        statistic(gains[rng.randrange(n)] for _ in range(n))
        for _ in range(samples)
    )
    alpha = 1.0 - confidence
    index = max(
        0,
        min(len(estimates) - 1, int(alpha / 2.0 * len(estimates))),
    )
    return estimates[index]
