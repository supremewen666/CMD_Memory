"""Bounded content-fingerprint buckets for memory-directory item gating."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.models import MemoryItem
from ..repair.failure_memory import _memory_fingerprint


@dataclass(frozen=True)
class MemoryBucket:
    bucket_id: str
    fingerprint: str
    items: tuple[MemoryItem, ...]


def bucket_memory_items(
    items: tuple[MemoryItem, ...],
    *,
    max_bucket_size: int = 5,
    similarity_threshold: float = 0.35,
) -> tuple[MemoryBucket, ...]:
    """Greedily group similar facts while enforcing the C(5,2) cost bound."""
    if max_bucket_size < 1:
        raise ValueError("max_bucket_size must be >= 1")
    if not 0.0 <= similarity_threshold <= 1.0:
        raise ValueError("similarity_threshold must be in [0, 1]")

    groups: list[list[MemoryItem]] = []
    fingerprints: list[str] = []
    for item in sorted(items, key=lambda value: value.memory_id):
        item_fp = _memory_fingerprint((item.text,))
        best_index = None
        best_score = -1.0
        for index, (group, fingerprint) in enumerate(zip(groups, fingerprints)):
            if len(group) >= max_bucket_size:
                continue
            score = _similarity(item_fp, fingerprint)
            if score >= similarity_threshold and score > best_score:
                best_index = index
                best_score = score
        if best_index is None:
            groups.append([item])
            fingerprints.append(item_fp)
        else:
            groups[best_index].append(item)
            fingerprints[best_index] = _memory_fingerprint(
                tuple(value.text for value in groups[best_index])
            )

    return tuple(
        MemoryBucket(
            bucket_id=f"bucket-{index:04d}",
            fingerprint=fingerprints[index],
            items=tuple(group),
        )
        for index, group in enumerate(groups)
    )


def _similarity(left: str, right: str) -> float:
    left_terms = set(left.split())
    right_terms = set(right.split())
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / len(left_terms | right_terms)
