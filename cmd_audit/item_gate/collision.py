"""Recall-set collision detection for Tier 2 item gate.

Implements step ② of the cost ladder: collision detection within the recall set
with ≤C(5,2) pairwise G-Eval contrasts and 0 generation cost.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..core.models import MemoryItem
from .divergence import DirectedDivergence, compute_directed_divergence

_logger = logging.getLogger(__name__)


@dataclass
class CollisionResult:
    """Result of collision detection between two memory items."""
    item_a: MemoryItem
    item_b: MemoryItem
    divergence: DirectedDivergence
    has_collision: bool
    collision_type: str | None  # "stale" | "conflict" | None
    timestamp_direction: str | None  # "a_newer" | "b_newer" | "same_period" | None

    @property
    def is_stale_collision(self) -> bool:
        """True if collision is classified as stale (one item newer)."""
        return self.collision_type == "stale"

    @property
    def is_conflict_collision(self) -> bool:
        """True if collision is classified as conflict (same period)."""
        return self.collision_type == "conflict"


def detect_item_collision(
    client: Any,
    item_a: MemoryItem,
    item_b: MemoryItem,
    *,
    divergence_threshold: float = 0.5,
    timestamp_tolerance_days: int = 7,
) -> CollisionResult:
    """Detect collision between two memory items using directed divergence.

    Implements the decision table from DISCUSSION.md:
    - Divergence large + one newer → item_stale
    - Divergence large + same period → item_conflict
    - Divergence small → PASS (consistent)

    Args:
        client: LLM client for divergence computation
        item_a: First memory item
        item_b: Second memory item
        divergence_threshold: Minimum divergence to trigger collision
        timestamp_tolerance_days: Days within which items are "same period"

    Returns:
        CollisionResult with divergence, collision status, and type
    """
    # Compute directed divergence
    divergence = compute_directed_divergence(client, item_a, item_b)

    # Check if divergence is large enough to indicate collision
    has_collision = divergence.max_divergence > divergence_threshold

    if not has_collision:
        return CollisionResult(
            item_a=item_a,
            item_b=item_b,
            divergence=divergence,
            has_collision=False,
            collision_type=None,
            timestamp_direction=None,
        )

    # Analyze timestamp direction
    timestamp_direction = _analyze_timestamp_direction(
        item_a, item_b, timestamp_tolerance_days
    )

    # Determine collision type based on timestamp direction
    collision_type = _classify_collision_type(timestamp_direction)

    return CollisionResult(
        item_a=item_a,
        item_b=item_b,
        divergence=divergence,
        has_collision=True,
        collision_type=collision_type,
        timestamp_direction=timestamp_direction,
    )


def compute_recall_set_divergence(
    client: Any,
    recall_set: tuple[MemoryItem, ...],
    *,
    divergence_threshold: float = 0.5,
    timestamp_tolerance_days: int = 7,
) -> list[CollisionResult]:
    """Compute pairwise divergence across entire recall set.

    Performs ≤C(5,2)=10 pairwise comparisons within the recall set.
    Only items in the same retrieval recall are compared (per-task scoping).

    Args:
        client: LLM client for divergence computation
        recall_set: Retrieved memory items for this task
        divergence_threshold: Minimum divergence to trigger collision
        timestamp_tolerance_days: Days tolerance for "same period"

    Returns:
        List of CollisionResults for all pairs with detected collisions
    """
    if len(recall_set) < 2:
        return []

    collisions = []
    n_items = len(recall_set)

    _logger.debug("Computing recall set divergence for %d items", n_items)

    # Pairwise comparison: C(n,2) = n*(n-1)/2
    for i in range(n_items):
        for j in range(i + 1, n_items):
            collision = detect_item_collision(
                client,
                recall_set[i],
                recall_set[j],
                divergence_threshold=divergence_threshold,
                timestamp_tolerance_days=timestamp_tolerance_days,
            )

            if collision.has_collision:
                collisions.append(collision)

    _logger.debug("Found %d collisions in recall set", len(collisions))
    return collisions


def _analyze_timestamp_direction(
    item_a: MemoryItem,
    item_b: MemoryItem,
    tolerance_days: int,
) -> str:
    """Analyze relative timestamps between two items.

    Note: Since MemoryItem doesn't have metadata field, we use store field
    as a simple timestamp placeholder for this implementation.

    Returns:
        "a_newer": item_a is significantly newer than item_b
        "b_newer": item_b is significantly newer than item_a
        "same_period": timestamps are within tolerance
        "no_reliable_timestamp": cannot determine reliable ordering
    """
    # Use store field as timestamp placeholder
    timestamp_a = item_a.store
    timestamp_b = item_b.store

    if not timestamp_a or not timestamp_b:
        return "no_reliable_timestamp"

    try:
        # Try parsing ISO format timestamps
        if isinstance(timestamp_a, str) and timestamp_a.endswith('Z'):
            dt_a = datetime.fromisoformat(timestamp_a.replace('Z', '+00:00'))
        else:
            return "no_reliable_timestamp"

        if isinstance(timestamp_b, str) and timestamp_b.endswith('Z'):
            dt_b = datetime.fromisoformat(timestamp_b.replace('Z', '+00:00'))
        else:
            return "no_reliable_timestamp"

        # Calculate difference in days
        diff_days = abs((dt_a - dt_b).days)

        if diff_days <= tolerance_days:
            return "same_period"
        elif dt_a > dt_b:
            return "a_newer"
        else:
            return "b_newer"

    except (ValueError, TypeError, AttributeError) as exc:
        _logger.debug("Timestamp parsing failed: %s", exc)
        return "no_reliable_timestamp"


def _classify_collision_type(timestamp_direction: str) -> str | None:
    """Classify collision type based on timestamp analysis.

    Maps timestamp direction to collision type per DISCUSSION.md decision table:
    - One newer → stale (auto-update possible)
    - Same period / no reliable timestamp → conflict (needs arbiter)
    """
    if timestamp_direction in ("a_newer", "b_newer"):
        return "stale"
    elif timestamp_direction in ("same_period", "no_reliable_timestamp"):
        return "conflict"
    else:
        return None