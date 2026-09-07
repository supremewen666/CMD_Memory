from __future__ import annotations

import pytest

from experiments.build_ghost_public_dataset import PublicCase, _validate_partitions


def _case(case_id: str, family_id: str) -> PublicCase:
    return PublicCase(case_id, family_id, "domain", case_id, {})


def test_four_partition_contract_accepts_represented_recurrence() -> None:
    _validate_partitions(
        {
            "ghost_dev": [_case("dev-a", "family-a"), _case("dev-b", "family-b")],
            "ghost_cal": [_case("cal", "family-cal")],
            "ghost_test_rep": [_case("rep", "family-a")],
            "ghost_test_new": [_case("new", "family-new")],
        }
    )


@pytest.mark.parametrize(
    "replacement",
    [
        {"ghost_test_rep": [_case("rep", "family-not-in-dev")]},
        {"ghost_test_new": [_case("new", "family-a")]},
        {"ghost_cal": [_case("cal", "family-a")]},
    ],
)
def test_four_partition_contract_rejects_family_leakage(
    replacement: dict[str, list[PublicCase]],
) -> None:
    partitions = {
        "ghost_dev": [_case("dev", "family-a")],
        "ghost_cal": [_case("cal", "family-cal")],
        "ghost_test_rep": [_case("rep", "family-a")],
        "ghost_test_new": [_case("new", "family-new")],
    }
    partitions.update(replacement)
    with pytest.raises(ValueError):
        _validate_partitions(partitions)
