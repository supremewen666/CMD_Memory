"""task.md 3.1 — the telemetry-decoupling controls must collapse.

These tests exercise the audit's control machinery directly on synthetic
observation rows rather than through :func:`audit_identifiability`, because the
frozen JSONL fixture that function loads carries its own materialized outcomes
and would test the dataset as much as the control.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from experiments.ghost_ecology_zero_call import (
    DECOUPLING_ARMS,
    _Observation,
    _decouple,
    _derange,
    _statistics,
)


@dataclass(frozen=True)
class _Outcome:
    """A ``V4CandidateOutcome``-shaped record for the audit path."""

    recovery_gain: float
    locality_cost: float = 0.0
    changed_item_count: int = 1
    valid: bool = True
    rolled_back: bool = False


def _aligned_rows(
    *, families: int = 8, cases_per_family: int = 3
) -> tuple[_Observation, ...]:
    """Rows where telemetry genuinely tracks the audit reference.

    Each case pairs one clean execution that recovered with one rollback that
    recovered nothing — the within-case signal.  Locality cost rises with the
    family index so family means differ too; without that spread the macro
    correlation would be computed over identical points and read 0.0 for every
    arm, which would make the control comparison vacuous.
    """
    rows: list[_Observation] = []
    for family in range(families):
        locality = 0.04 * family
        for case in range(cases_per_family):
            case_id = f"family_{family}_case_{case}"
            for useful in (True, False):
                outcome = _Outcome(
                    recovery_gain=0.6 if useful else 0.0,
                    locality_cost=locality,
                    changed_item_count=1,
                    valid=True,
                    rolled_back=not useful,
                )
                rows.append(
                    _Observation(
                        case_id=case_id,
                        family_id=f"family_{family}",
                        effect="replace",
                        telemetry=outcome,
                        reference=outcome,
                    )
                )
    return tuple(rows)


def _stats(rows):
    return _statistics(rows, bootstrap_samples=200, bootstrap_seed=7)


def test_true_arm_is_identifiable_on_aligned_telemetry() -> None:
    stats = _stats(_aligned_rows())

    assert stats["family_macro_pearson"] > 0.9
    assert stats["within_case_pairwise_concordance"] == 1.0
    assert stats["candidate_observation_count"] == 48
    assert stats["family_count"] == 8


@pytest.mark.parametrize("arm", DECOUPLING_ARMS)
def test_decoupled_arms_collapse_and_separate_from_the_true_arm(arm: str) -> None:
    rows = _aligned_rows()
    true_stats = _stats(rows)
    control_stats = _stats(_decouple(rows, arm=arm, seed=91))

    # The kill condition: breaking the candidate/telemetry pairing must destroy
    # identifiability.  If this passes, the true arm's signal was not circular.
    assert control_stats["within_case_pairwise_concordance"] < 0.55
    assert control_stats["family_macro_pearson"] < 0.2

    # And the two arms must be separable, not merely both reported.
    assert (
        true_stats["within_case_pairwise_concordance"]
        > control_stats["within_case_pairwise_concordance"]
    )
    assert true_stats["family_macro_pearson"] > control_stats["family_macro_pearson"]


def test_placebo_arm_is_fully_degenerate() -> None:
    control = _decouple(_aligned_rows(), arm="telemetry_placebo", seed=91)

    # Identical telemetry everywhere means the reward is constant, so no pair is
    # comparable and no correlation can be computed.
    stats = _stats(control)
    assert stats["comparable_pair_count"] == 0
    assert stats["within_case_pairwise_concordance"] == 0.0
    assert stats["candidate_level_pearson"] == pytest.approx(0.0, abs=1e-12)


def test_permutation_preserves_the_audit_reference_it_must_not_move() -> None:
    rows = _aligned_rows()
    permuted = _decouple(rows, arm="telemetry_permutation", seed=91)

    # References stay bound to their own candidate; only telemetry moves.  If
    # both moved together the correlation would survive and the control would
    # be vacuous.
    assert [row.reference for row in permuted] == [row.reference for row in rows]
    assert [row.case_id for row in permuted] == [row.case_id for row in rows]
    assert any(
        permuted[index].telemetry is not rows[index].telemetry
        for index in range(len(rows))
    )


def test_derangement_leaves_no_candidate_correctly_paired() -> None:
    for seed in (0, 1, 91, 1234):
        order = _derange(24, seed=seed)
        assert sorted(order) == list(range(24))
        assert all(index != position for position, index in enumerate(order))

    with pytest.raises(ValueError, match="at least two candidates"):
        _derange(1, seed=91)


def test_unknown_decoupling_arm_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown decoupling arm"):
        _decouple(_aligned_rows(), arm="telemetry_handwave", seed=91)
