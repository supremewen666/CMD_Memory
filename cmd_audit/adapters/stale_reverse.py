"""Post-retrieval STALE repair adapter for external memory pipelines."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.models import MemoryItem
from ..item_gate.freshness import FreshnessDecision, arbitrate_freshness


@dataclass(frozen=True)
class StaleReverseResult:
    original_items: tuple[MemoryItem, ...]
    repaired_items: tuple[MemoryItem, ...]
    decision: FreshnessDecision

    @property
    def changed(self) -> bool:
        return self.original_items != self.repaired_items


def repair_post_retrieval(
    items: tuple[MemoryItem, ...],
    *,
    tolerance_days: int = 7,
) -> StaleReverseResult:
    """Apply deterministic freshness after retrieval and before answer injection."""
    decision = arbitrate_freshness(items, tolerance_days=tolerance_days)
    if not decision.applicable:
        return StaleReverseResult(items, items, decision)
    by_id = {item.memory_id: item for item in items}
    repaired = tuple(
        by_id[memory_id]
        for memory_id in decision.kept_ids
        if memory_id in by_id
    )
    return StaleReverseResult(items, repaired, decision)


def three_dimension_accuracy(
    predicted: tuple[str, str, str],
    expected: tuple[str, str, str],
) -> tuple[float, float, float]:
    """Return STALE's three per-dimension exact-match scores."""
    if len(predicted) != 3 or len(expected) != 3:
        raise ValueError("STALE scoring requires exactly three dimensions")
    return tuple(
        float(left.strip().casefold() == right.strip().casefold())
        for left, right in zip(predicted, expected)
    )
