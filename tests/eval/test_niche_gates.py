from __future__ import annotations

from cmd_audit.eval.niche_gates import (
    NicheConfirmatoryOutcome,
    evaluate_niche_confirmation,
)


def _rows(*, null_mismatch: bool = False):
    rows = []
    for index in range(8):
        case_id = f"c{index}"
        family_id = f"f{index}"
        for arm, gain in (
            ("G2", 0.7),
            ("all_frozen", 0.2),
            ("unkeyed_pool", 0.3),
        ):
            rows.append(
                NicheConfirmatoryOutcome(
                    case_id,
                    family_id,
                    arm,
                    gain,
                    scope_external=index < 2,
                    unseen_family=index in {2, 3},
                )
            )
    for arm in ("G2", "all_frozen", "unkeyed_pool"):
        rows.append(
            NicheConfirmatoryOutcome(
                "null",
                "null-family",
                arm,
                0.0,
                null_or_fill=True,
                selection_matches_frozen=not (
                    null_mismatch and arm == "G2"
                ),
            )
        )
    return tuple(rows)


def test_niche_confirmation_passes_efficacy_and_safety() -> None:
    decision = evaluate_niche_confirmation(
        _rows(),
        gstar="G2",
        bootstrap_samples=200,
        bootstrap_seed=24,
    )

    assert decision.primary_passed
    assert decision.final_decision == "positive_niche_claim"
    assert decision.null_fill_exact


def test_null_mismatch_vetoes_positive_claim() -> None:
    decision = evaluate_niche_confirmation(
        _rows(null_mismatch=True),
        gstar="G2",
        bootstrap_samples=200,
    )

    assert not decision.primary_passed
    assert decision.final_decision == "negative_or_partial_result"


def test_missing_safety_subsets_cannot_pass_vacuously() -> None:
    rows = tuple(
        NicheConfirmatoryOutcome(
            row.case_id,
            row.family_id,
            row.arm_id,
            row.recovery_gain,
            null_or_fill=row.null_or_fill,
        )
        for row in _rows()
    )

    decision = evaluate_niche_confirmation(
        rows,
        gstar="G2",
        bootstrap_samples=200,
    )

    assert decision.scope_external_lower_bound is None
    assert decision.unseen_family_lower_bound is None
    assert not decision.primary_passed
