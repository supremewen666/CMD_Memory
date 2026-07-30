from __future__ import annotations

from cmd_audit.eval.evolution_gates import (
    CHECKPOINTS,
    EvolutionProbeOutcome,
    FamilyNetGains,
    build_family_split,
    evaluate_evolution_gates,
    family_bucket,
    permutation_p_value,
    prior_same_family_counts,
)


def _positive_within_family_gains():
    return (
        FamilyNetGains("kp-1", "kp", (0.0,), (0.5,)),
        FamilyNetGains("kp-2", "kp", (0.0,), (0.5,)),
        FamilyNetGains("slug-1", "slug", (0.0,), (0.5,)),
        FamilyNetGains("slug-2", "slug", (0.0,), (0.5,)),
    )


def _family_for_bucket(bucket: int) -> str:
    index = 0
    while True:
        value = f"family-{bucket}-{index}"
        if family_bucket(value) == bucket:
            return value
        index += 1


def test_family_split_is_hash_stable_and_variant_strict():
    represented = _family_for_bucket(1)
    unseen = _family_for_bucket(0)
    rows = [
        {
            "case_id": f"{family}-{variant}",
            "recurrent_family_id": family,
            "recurrent_variant_index": variant,
        }
        for family in (represented, unseen)
        for variant in range(5)
    ]
    first = build_family_split(rows)
    second = build_family_split(reversed(rows))
    assert first == second
    by_id = {item.recurrent_family_id: item for item in first}
    assert by_id[represented].update_variant_indices == (0, 1, 2)
    assert by_id[represented].probe_variant_indices == (3, 4)
    assert by_id[unseen].update_variant_indices == ()
    assert by_id[unseen].probe_variant_indices == (0, 1, 2, 3, 4)


def test_family_blocked_primary_and_safety_gates():
    outcomes = []
    for family in ("represented-a", "represented-b"):
        for variant in (3, 4):
            for checkpoint_index, checkpoint in enumerate(CHECKPOINTS):
                for arm in ("patterned", "unkeyed_global", "no_update"):
                    outcomes.append(
                        EvolutionProbeOutcome(
                            recurrent_family_id=family,
                            recurrent_variant_index=variant,
                            probe_set="represented",
                            checkpoint=checkpoint,
                            arm_id=arm,
                            recovered=(
                                arm == "patterned" and checkpoint_index > 0
                            ),
                        )
                    )
    for family in ("unseen-a", "unseen-b"):
        for variant in range(5):
            for checkpoint in CHECKPOINTS:
                for arm in ("patterned", "unkeyed_global", "no_update"):
                    outcomes.append(
                        EvolutionProbeOutcome(
                            recurrent_family_id=family,
                            recurrent_variant_index=variant,
                            probe_set="unseen",
                            checkpoint=checkpoint,
                            arm_id=arm,
                            recovered=True,
                        )
                    )
    result = evaluate_evolution_gates(
        outcomes,
        within_family_gains=_positive_within_family_gains(),
        bootstrap_samples=200,
        bootstrap_seed=7,
    )
    assert result.primary.passed
    assert result.primary.endpoint.estimate == 1.0
    assert result.primary.difference_in_differences.estimate == 1.0
    assert result.primary.aulc.estimate > 0.0
    assert result.safety.passed
    assert result.safety.estimate == 0.0
    assert result.within_family.combined.passed


def test_permutation_p_value_and_prior_family_count():
    assert permutation_p_value(0.8, [0.1, 0.2, 0.9]) == 0.5
    assert prior_same_family_counts(("a", "b", "a", "a", "b")) == (
        0,
        0,
        1,
        2,
        1,
    )
