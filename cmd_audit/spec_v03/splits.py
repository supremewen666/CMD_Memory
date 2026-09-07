"""Family/source-episode blocked partitions and sealed lockbox manifests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from typing import Iterable, Mapping

from .contracts import DecisionView, canonical_sha256


SPLITS = ("D_skill", "D_router", "D_cal", "D_lifecycle", "T_online", "T_anchor", "T_final")
_DEFAULT_WEIGHTS = (35, 20, 10, 5, 15, 5, 10)


@dataclass(frozen=True)
class SplitManifest:
    schema_version: str
    seed: int
    assignments: Mapping[str, str]
    split_case_ids: Mapping[str, tuple[str, ...]]
    split_source_episode_ids: Mapping[str, tuple[str, ...]]
    content_sha256: str

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LockboxManifest:
    split_manifest_sha256: str
    lockbox_splits: tuple[str, ...]
    visible_splits: tuple[str, ...]
    lockbox_case_sha256: str
    content_sha256: str

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


def _component_ids(cases: Iterable[DecisionView], extra_block_keys: Mapping[str, tuple[str, ...]] | None = None) -> list[list[DecisionView]]:
    rows = list(cases)
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left, right = find(left), find(right)
        if left != right:
            parent[right] = left

    by_episode: dict[str, int] = {}
    by_family: dict[str, int] = {}
    by_extra: dict[str, int] = {}
    for index, case in enumerate(rows):
        for table, key in ((by_episode, case.source_episode_id), (by_family, case.family_id)):
            previous = table.setdefault(key, index)
            union(index, previous)
        for key in (extra_block_keys or {}).get(case.case_id, ()):
            previous = by_extra.setdefault(key, index)
            union(index, previous)
    groups: dict[int, list[DecisionView]] = {}
    for index, case in enumerate(rows):
        groups.setdefault(find(index), []).append(case)
    return list(groups.values())


def build_split_manifest(cases: Iterable[DecisionView], *, seed: int, extra_block_keys: Mapping[str, tuple[str, ...]] | None = None, forced_assignments: Mapping[str, str] | None = None) -> SplitManifest:
    """Create source/family blocked partitions plus optional frozen template keys."""
    groups = _component_ids(cases, extra_block_keys)
    if not groups:
        raise ValueError("cannot split an empty decision-view collection")
    buckets = {name: [] for name in SPLITS}
    assignments: dict[str, str] = {}
    for group in groups:
        declared = {forced_assignments[case.case_id] for case in group if forced_assignments and case.case_id in forced_assignments}
        if len(declared) > 1 or any(item not in SPLITS for item in declared):
            raise ValueError("blocked component has conflicting forced split assignments")
        if declared:
            split = next(iter(declared))
        else:
            identity = "|".join(sorted(case.case_id for case in group))
            token = int(hashlib.sha256(f"{seed}:{identity}".encode()).hexdigest(), 16) % sum(_DEFAULT_WEIGHTS)
            running = 0
            split = SPLITS[-1]
            for candidate, weight in zip(SPLITS, _DEFAULT_WEIGHTS):
                running += weight
                if token < running:
                    split = candidate
                    break
        for case in group:
            assignments[case.case_id] = split
            buckets[split].append(case)
    case_ids = {name: tuple(sorted(case.case_id for case in values)) for name, values in buckets.items()}
    episodes = {name: tuple(sorted({case.source_episode_id for case in values})) for name, values in buckets.items()}
    body = {"schema_version": "cmd-spec-v03-split-v1", "seed": seed, "assignments": assignments, "split_case_ids": case_ids, "split_source_episode_ids": episodes}
    return SplitManifest(**body, content_sha256=canonical_sha256(body))


def build_lockbox_manifest(split: SplitManifest) -> LockboxManifest:
    lockbox_splits = ("T_anchor", "T_final")
    lockbox_cases = tuple(sorted(case_id for name in lockbox_splits for case_id in split.split_case_ids[name]))
    body = {
        "split_manifest_sha256": split.content_sha256,
        "lockbox_splits": lockbox_splits,
        "visible_splits": tuple(name for name in SPLITS if name not in lockbox_splits),
        "lockbox_case_sha256": canonical_sha256(lockbox_cases),
    }
    return LockboxManifest(**body, content_sha256=canonical_sha256(body))
