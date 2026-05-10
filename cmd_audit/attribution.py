"""Operation-level attribution from replay deltas."""

from __future__ import annotations

from dataclasses import dataclass

from .labels import REPLAY_TO_LABEL, validate_v0_label
from .replays import ReplayResult


@dataclass(frozen=True)
class AttributionResult:
    predicted_label: str
    top_replay: str
    recovery_gain: float
    top2_labels: tuple[str, ...]
    is_ambiguous: bool


def assign_attribution(
    replay_results: tuple[ReplayResult, ...],
    *,
    positive_gain_threshold: float = 0.0,
    tie_margin: float = 0.05,
) -> AttributionResult:
    if not replay_results:
        raise ValueError("at least one replay result is required")

    ranked = sorted(replay_results, key=lambda result: result.recovery_gain, reverse=True)
    top = ranked[0]
    if top.recovery_gain <= positive_gain_threshold:
        raise ValueError("no replay produced a positive recovery gain")

    predicted_label = validate_v0_label(_label_for_replay(top.replay_name))
    close = [
        validate_v0_label(_label_for_replay(result.replay_name))
        for result in ranked
        if top.recovery_gain - result.recovery_gain <= tie_margin
    ][:2]
    return AttributionResult(
        predicted_label=predicted_label,
        top_replay=top.replay_name,
        recovery_gain=top.recovery_gain,
        top2_labels=tuple(close),
        is_ambiguous=len(close) > 1,
    )


def _label_for_replay(replay_name: str) -> str:
    try:
        return REPLAY_TO_LABEL[replay_name]
    except KeyError as exc:
        raise ValueError(f"unknown replay {replay_name!r}") from exc
