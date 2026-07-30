"""Family-keyed stream builder for the memtrace_kp_cases dataset.

The dataset ``data/probe_cases/memtrace_kp_cases.json`` encodes two disjoint
``case_id`` shapes (see ``cmd_audit/adapters/memtrace_kp.py::_case_id``):

    numeric:  memtraceb-<uuid8>-kp<NNNN>-a<N>c<I>-<condition>
    slug:     memtraceb-<uuid8>-<slug>-a<N>c<I>-<condition>

``<uuid8>`` is the first eight hex characters of the source HaluMem user's
uuid (``_user_uuid_short`` in the adapter). ``ProbeCase`` carries no explicit
user-identity field, so this eight-character segment is the only per-case
user identifier recoverable from a bare ``ProbeCase``/``case_id`` pair; it is
used here as ``user_uuid`` even though it is a truncation of the real uuid,
not the full value.

A "family" is every case sharing the same ``(keying, user_uuid, key_value)``
triple, where ``key_value`` is the knowledge-point position (numeric) or the
boundary-category slug (slug). Families never mix the two keyings.
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass
from typing import Iterable, Mapping

from ..core.models import ProbeCase

_NUMERIC_RE = re.compile(
    r"^memtraceb-([0-9a-f]{8})-kp(\d{4})-a(\d+)c(\d+)-(.+)$"
)
_SLUG_RE = re.compile(
    r"^memtraceb-([0-9a-f]{8})-([a-z0-9]+)-a(\d+)c(\d+)-(.+)$"
)

#: c_index values (within a represented family) used to update the library.
UPDATE_C_INDICES = (0, 1, 2)
#: c_index values (within a represented family) held out as probe members.
HELDOUT_C_INDICES = (3, 4, 5, 6, 7)


class MemtraceFamilyError(ValueError):
    """Raised when a case_id matches neither the numeric nor the slug shape."""


@dataclass(frozen=True)
class MemtraceMember:
    case_id: str
    c_index: int
    a_index: int
    condition: str
    case: ProbeCase


@dataclass(frozen=True)
class MemtraceFamily:
    family_id: str
    keying: str
    user_uuid: str
    key_value: str
    members: tuple[MemtraceMember, ...]


@dataclass(frozen=True)
class FamilySplit:
    """Result of ``split_families``.

    ``unseen`` families (bucket 0) are safety-probe families: never used for
    library updates. ``represented`` families (buckets 1-4) contribute
    ``update_members`` (c_index in {0,1,2}) and are probed on
    ``heldout_members`` (c_index in {3..7}). Both member maps are keyed by
    ``family_id`` and are absent (not present as a key) for unseen families.
    """

    unseen: tuple[MemtraceFamily, ...]
    represented: tuple[MemtraceFamily, ...]
    update_members: Mapping[str, tuple[MemtraceMember, ...]]
    heldout_members: Mapping[str, tuple[MemtraceMember, ...]]


def _parse_case_id(case_id: str) -> tuple[str, str, str, int, int, str]:
    """Return ``(keying, user_uuid, key_value, c_index, a_index, condition)``."""
    match = _NUMERIC_RE.match(case_id)
    if match:
        uuid8, kp_position, a_index, c_index, condition = match.groups()
        return "kp", uuid8, str(int(kp_position)), int(c_index), int(a_index), condition
    match = _SLUG_RE.match(case_id)
    if match:
        uuid8, slug, a_index, c_index, condition = match.groups()
        return "slug", uuid8, slug, int(c_index), int(a_index), condition
    raise MemtraceFamilyError(
        f"case_id matches neither the numeric nor the slug memtrace shape: {case_id!r}"
    )


def build_families(cases: Iterable[ProbeCase]) -> tuple[MemtraceFamily, ...]:
    """Group cases into families, in deterministic order by ``family_id``.

    Every case must match exactly one of the two anchored id shapes; a case
    matching neither raises :class:`MemtraceFamilyError` rather than being
    silently skipped.
    """
    groups: dict[tuple[str, str, str], list[MemtraceMember]] = {}
    for case in cases:
        keying, user_uuid, key_value, c_index, a_index, condition = _parse_case_id(
            case.case_id
        )
        key = (keying, user_uuid, key_value)
        groups.setdefault(key, []).append(
            MemtraceMember(
                case_id=case.case_id,
                c_index=c_index,
                a_index=a_index,
                condition=condition,
                case=case,
            )
        )

    families: list[MemtraceFamily] = []
    for (keying, user_uuid, key_value), members in groups.items():
        family_id = hashlib.sha256(
            "\0".join(("memtrace", keying, user_uuid, key_value)).encode("utf-8")
        ).hexdigest()
        ordered_members = tuple(
            sorted(members, key=lambda item: (item.c_index, item.a_index, item.case_id))
        )
        families.append(
            MemtraceFamily(
                family_id=family_id,
                keying=keying,
                user_uuid=user_uuid,
                key_value=key_value,
                members=ordered_members,
            )
        )
    families.sort(key=lambda item: item.family_id)
    return tuple(families)


def family_bucket(user_uuid: str) -> int:
    """Hash-stable 0-4 bucket for one user's memories.

    Bucketed on ``user_uuid``, NOT on ``family_id``: 20 user_uuid values span
    both the numeric (kp) and slug keyings in this dataset, and bucketing on
    ``family_id`` would put the same user's kp-keyed and slug-keyed families
    into different buckets — leaking that user's memories across the
    unseen/represented split, which the safety-probe design requires to stay
    disjoint per user.
    """
    digest = hashlib.sha256(user_uuid.encode("utf-8")).hexdigest()
    return int(digest, 16) % 5


def split_families(families: Iterable[MemtraceFamily]) -> FamilySplit:
    """Partition families into unseen (bucket 0) / represented (buckets 1-4).

    Represented families are further split into update members
    (``c_index`` in {0,1,2}) and heldout probe members (``c_index`` in
    {3..7}). A member whose ``c_index`` falls outside a family's observed
    range simply is not present in either list; there is never any padding
    and never a raise for a short family.
    """
    unseen: list[MemtraceFamily] = []
    represented: list[MemtraceFamily] = []
    update_members: dict[str, tuple[MemtraceMember, ...]] = {}
    heldout_members: dict[str, tuple[MemtraceMember, ...]] = {}
    for family in families:
        if family_bucket(family.user_uuid) == 0:
            unseen.append(family)
            continue
        represented.append(family)
        update_members[family.family_id] = tuple(
            member for member in family.members if member.c_index in UPDATE_C_INDICES
        )
        heldout_members[family.family_id] = tuple(
            member for member in family.members if member.c_index in HELDOUT_C_INDICES
        )
    return FamilySplit(
        unseen=tuple(unseen),
        represented=tuple(represented),
        update_members=update_members,
        heldout_members=heldout_members,
    )


def family_stream(
    families: Iterable[MemtraceFamily], *, seed: int
) -> tuple[MemtraceMember, ...]:
    """Flatten families into one member stream, shuffling family ORDER only.

    Member order within a family is always the family's stored sorted
    ``(c_index, a_index, case_id)`` order; families are never interleaved.
    Deterministic: the same ``seed`` always produces the same ``case_id``
    sequence, byte for byte.
    """
    ordered = list(families)
    random.Random(seed).shuffle(ordered)
    stream: list[MemtraceMember] = []
    for family in ordered:
        stream.extend(family.members)
    return tuple(stream)
