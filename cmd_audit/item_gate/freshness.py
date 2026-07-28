"""Deterministic max-timestamp arbitration for stale memory collisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ..core.models import MemoryItem


@dataclass(frozen=True)
class FreshnessDecision:
    applicable: bool
    kept_ids: tuple[str, ...]
    demoted_ids: tuple[str, ...]
    item_signal_hints: tuple[tuple[str, float], ...]
    reason: str

    def hints(self) -> dict[str, float]:
        return dict(self.item_signal_hints)


def arbitrate_freshness(
    items: tuple[MemoryItem, ...],
    *,
    tolerance_days: int = 7,
) -> FreshnessDecision:
    """Prefer the newest timestamped fact and deterministically demote older ones."""
    timestamped = tuple(
        (timestamp, item)
        for item in items
        for timestamp in [_timestamp(item.store)]
        if timestamp is not None
    )
    if len(timestamped) < 2:
        return FreshnessDecision(
            applicable=False,
            kept_ids=tuple(item.memory_id for item in items),
            demoted_ids=(),
            item_signal_hints=(),
            reason="insufficient_comparable_timestamps",
        )
    newest_time, newest = max(timestamped, key=lambda pair: pair[0])
    demoted = tuple(
        item.memory_id
        for timestamp, item in timestamped
        if item.memory_id != newest.memory_id
        and (newest_time - timestamp).total_seconds()
        > tolerance_days * 24 * 60 * 60
    )
    if not demoted:
        return FreshnessDecision(
            applicable=False,
            kept_ids=tuple(item.memory_id for item in items),
            demoted_ids=(),
            item_signal_hints=(),
            reason="timestamps_within_tolerance",
        )
    hints = ((newest.memory_id, 1.0),) + tuple(
        (memory_id, -1.0) for memory_id in demoted
    )
    return FreshnessDecision(
        applicable=True,
        kept_ids=tuple(
            item.memory_id for item in items if item.memory_id not in demoted
        ),
        demoted_ids=demoted,
        item_signal_hints=tuple(sorted(hints)),
        reason="newest_timestamp_wins",
    )


def _timestamp(value: str) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None
