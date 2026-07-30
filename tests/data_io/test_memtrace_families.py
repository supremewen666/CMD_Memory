from __future__ import annotations

from dataclasses import replace
import json
from statistics import median

import pytest

from cmd_audit.data_io import (
    build_memtrace_family_net_gains,
    load_memtrace_dataset,
    load_memtrace_family_net_gains,
)
from cmd_audit.repair.memtrace_families import (
    MemtraceFamilyError,
    build_families,
    family_bucket,
)


@pytest.fixture(scope="module")
def memtrace():
    return load_memtrace_dataset()


def test_real_memtrace_dataset_builds_all_verified_families(memtrace):
    assert len(memtrace.cases) == 2047
    assert len(memtrace.families) == 182
    assert sum(family.keying == "kp" for family in memtrace.families) == 120
    assert sum(family.keying == "slug" for family in memtrace.families) == 62
    assert all(
        len(family.members) == 8
        for family in memtrace.families
        if family.keying == "slug"
    )


def test_real_memtrace_families_have_zero_memory_scale_drift(memtrace):
    for family in memtrace.families:
        min_c = min(member.c_index for member in family.members)
        max_c = max(member.c_index for member in family.members)
        at_min = [
            len(member.case.extracted_memory)
            for member in family.members
            if member.c_index == min_c
        ]
        at_max = [
            len(member.case.extracted_memory)
            for member in family.members
            if member.c_index == max_c
        ]
        assert median(at_min) == median(at_max), family.family_id


def test_user_bucket_keeps_both_keyings_on_the_same_side(memtrace):
    by_user: dict[str, set[str]] = {}
    for family in memtrace.families:
        by_user.setdefault(family.user_uuid, set()).add(family.keying)
    cross_keyed = {
        user_uuid for user_uuid, keyings in by_user.items() if len(keyings) == 2
    }
    assert len(cross_keyed) == 20

    unseen_users = {family.user_uuid for family in memtrace.split.unseen}
    represented_users = {
        family.user_uuid for family in memtrace.split.represented
    }
    assert unseen_users.isdisjoint(represented_users)
    assert all(
        (user_uuid in unseen_users) == (family_bucket(user_uuid) == 0)
        for user_uuid in by_user
    )


def test_family_stream_is_seeded_and_never_reorders_members(memtrace):
    first = memtrace.stream(seed=24)
    repeated = memtrace.stream(seed=24)
    other = memtrace.stream(seed=124)
    assert tuple(member.case_id for member in first) == tuple(
        member.case_id for member in repeated
    )
    assert tuple(member.case_id for member in first) != tuple(
        member.case_id for member in other
    )

    expected_by_family = {
        family.family_id: tuple(member.case_id for member in family.members)
        for family in memtrace.families
    }
    position = 0
    while position < len(first):
        member = first[position]
        family = next(
            family
            for family in memtrace.families
            if member.case_id in expected_by_family[family.family_id]
        )
        expected = expected_by_family[family.family_id]
        observed = tuple(
            item.case_id for item in first[position : position + len(expected)]
        )
        assert observed == expected
        position += len(expected)


def test_unmatched_memtrace_case_id_fails_closed(memtrace):
    invalid = replace(memtrace.cases[0], case_id="not-a-memtrace-case")
    with pytest.raises(MemtraceFamilyError, match="matches neither"):
        build_families((invalid,))


def test_family_net_gain_artifact_covers_every_case_and_binds_by_c_index(
    memtrace,
    tmp_path,
):
    values = {
        member.case_id: 0.0 if member.c_index == 0 else 0.25
        for family in memtrace.families
        for member in family.members
    }
    artifact = tmp_path / "memtrace_net_gains.json"
    artifact.write_text(json.dumps(values), encoding="utf-8")

    families = load_memtrace_family_net_gains(
        artifact,
        dataset=memtrace,
    )

    assert len(families) == 182
    eligible = [
        family
        for family in families
        if family.baseline_gains and family.later_gains
    ]
    assert eligible
    assert all(set(family.baseline_gains) == {0.0} for family in eligible)
    assert all(set(family.later_gains) == {0.25} for family in eligible)


def test_family_net_gain_join_fails_on_missing_case(memtrace):
    values = {case.case_id: 0.1 for case in memtrace.cases[1:]}
    with pytest.raises(ValueError, match="do not match"):
        build_memtrace_family_net_gains(memtrace, values)
