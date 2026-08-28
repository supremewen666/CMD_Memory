from __future__ import annotations

import pytest

from cmd_audit.spec_v03.repair_utility import RepairUtilityWeights, score_repair_utility


def test_safe_repairs_are_ranked_by_cost_without_weakening_hard_gates() -> None:
    targeted = score_repair_utility(
        committed=True, safety_passed=True, invariant_passed=True,
        locality_cost=1, locality_bound=2, expected_cost=0.1,
    )
    rebuild = score_repair_utility(
        committed=True, safety_passed=True, invariant_passed=True,
        locality_cost=2, locality_bound=2, collateral_cost=0.25, expected_cost=0.8,
    )

    assert 0.0 < rebuild < targeted < 1.0
    assert score_repair_utility(
        committed=False, safety_passed=True, invariant_passed=True,
        locality_cost=0, locality_bound=2,
    ) == -1.0
    assert score_repair_utility(
        committed=True, safety_passed=False, invariant_passed=True,
        locality_cost=0, locality_bound=2,
    ) == -1.0


def test_recurrence_can_reverse_a_low_cost_strategy_ranking() -> None:
    targeted_with_recurrence = score_repair_utility(
        committed=True, safety_passed=True, invariant_passed=True,
        locality_cost=1, locality_bound=2, expected_cost=0.1,
        recurrence_after_commit=True,
    )
    robust_rebuild = score_repair_utility(
        committed=True, safety_passed=True, invariant_passed=True,
        locality_cost=2, locality_bound=2, expected_cost=0.8,
    )

    assert targeted_with_recurrence < robust_rebuild


def test_utility_inputs_and_weights_are_closed_and_bounded() -> None:
    with pytest.raises(ValueError, match="collateral_cost"):
        score_repair_utility(
            committed=True, safety_passed=True, invariant_passed=True,
            locality_cost=0, locality_bound=1, collateral_cost=1.1,
        )
    with pytest.raises(ValueError, match="weights"):
        RepairUtilityWeights(locality=-0.1)
