"""Shared cost-sensitive utility for delayed CMD repair receipts."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class RepairUtilityWeights:
    locality: float = 0.20
    collateral: float = 0.20
    expected_cost: float = 0.15
    latency: float = 0.10
    recurrence: float = 0.75

    def __post_init__(self) -> None:
        values = (self.locality, self.collateral, self.expected_cost, self.latency, self.recurrence)
        if any(not math.isfinite(value) or value < 0.0 for value in values):
            raise ValueError("repair utility weights must be finite and non-negative")


def _unit(value: float, name: str) -> float:
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be finite in [0, 1]")
    return value


def score_repair_utility(
    *,
    committed: bool,
    safety_passed: bool,
    invariant_passed: bool,
    locality_cost: int,
    locality_bound: int,
    collateral_cost: float = 0.0,
    expected_cost: float = 0.0,
    latency_cost: float = 0.0,
    recurrence_after_commit: bool = False,
    rolled_back: bool = False,
    weights: RepairUtilityWeights = RepairUtilityWeights(),
) -> float:
    """Return a bounded reward after hard safety and correctness gates.

    Safety, invariants, commit, rollback, and locality bounds remain hard gates.
    Cost terms only rank repairs that are already admissible.
    """
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (locality_cost, locality_bound)):
        raise ValueError("locality cost and bound must be non-negative integers")
    collateral = _unit(float(collateral_cost), "collateral_cost")
    expected = _unit(float(expected_cost), "expected_cost")
    latency = _unit(float(latency_cost), "latency_cost")
    if rolled_back or not committed or not safety_passed or not invariant_passed or locality_cost > locality_bound:
        return -1.0
    locality = 0.0 if locality_bound == 0 else min(1.0, locality_cost / locality_bound)
    penalty = (
        weights.locality * locality
        + weights.collateral * collateral
        + weights.expected_cost * expected
        + weights.latency * latency
        + weights.recurrence * float(recurrence_after_commit)
    )
    return max(-1.0, min(1.0, 1.0 - penalty))
