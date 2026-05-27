"""Operation-level attribution from replay deltas."""

from __future__ import annotations

from dataclasses import dataclass

from .labels import (
    REPLAY_TO_LABEL,
    V1_REPLAY_TO_LABEL,
    validate_v0_label,
    validate_v1_label,
)
from .replays import ReplayResult


@dataclass(frozen=True)
class AttributionResult:
    predicted_label: str
    top_replay: str
    recovery_gain: float
    top2_labels: tuple[str, ...]
    is_ambiguous: bool
    top_k_labels: tuple[str, ...] = ()
    close_deltas: tuple[tuple[str, float], ...] = ()
    distractor_provenance_ids: tuple[str, ...] = ()
    distractor_provenance_edges: tuple = ()


def assign_attribution(
    replay_results: tuple[ReplayResult, ...],
    *,
    positive_gain_threshold: float = 0.0,
    tie_margin: float = 0.05,
) -> AttributionResult:
    if not replay_results:
        raise ValueError("at least one replay result is required")

    ranked = sorted(
        replay_results, key=lambda result: result.recovery_gain, reverse=True
    )
    top = ranked[0]
    if top.recovery_gain <= positive_gain_threshold:
        raise ValueError("no replay produced a positive recovery gain")

    predicted_label = validate_v0_label(_label_for_replay(top.replay_name))
    close = [
        validate_v0_label(_label_for_replay(result.replay_name))
        for result in ranked
        if top.recovery_gain - result.recovery_gain <= tie_margin
    ]
    close_capped = close[:2]
    return AttributionResult(
        predicted_label=predicted_label,
        top_replay=top.replay_name,
        recovery_gain=top.recovery_gain,
        top2_labels=tuple(close_capped),
        is_ambiguous=len(close) > 1,
        top_k_labels=tuple(close_capped),
        close_deltas=(),
    )


def assign_attribution_v1(
    replay_results: tuple[ReplayResult, ...],
    *,
    has_ingestion_trace: bool = True,
    positive_gain_threshold: float = 0.0,
    tie_margin: float = 0.05,
    top_k: int = 2,
    distractor_edges: tuple = (),
) -> AttributionResult:
    """V1 attribution with ingestion/write split and route_error support.

    When oracle_write is the top replay and `has_ingestion_trace` is False,
    the label is ``ingestion_error`` instead of ``write_error``.

    ``evidence_given_reasoning`` is an answer-axis replay. It does not
    participate in evidence-axis ranking; it is used only as a fallback when
    no evidence-axis replay clears ``positive_gain_threshold``. In that
    fallback path, ``close_deltas`` contains only ``("reasoning_error", 0.0)``
    so answer-axis and evidence-axis deltas are never mixed.

    The *top_k* parameter controls how many close-delta labels appear in
    ``top_k_labels``.  ``top2_labels`` always caps at 2 for backward
    compatibility; ``close_deltas`` exposes every (label, delta) pair
    within *tie_margin* unbounded by *top_k*.
    """
    if not replay_results:
        raise ValueError("at least one replay result is required")

    reasoning_replay = next(
        (
            result
            for result in replay_results
            if result.replay_name == "evidence_given_reasoning"
        ),
        None,
    )
    rankable_results = tuple(
        result
        for result in replay_results
        if result.replay_name != "evidence_given_reasoning"
    )
    if not rankable_results:
        rankable_results = replay_results

    ranked = sorted(
        rankable_results, key=lambda result: result.recovery_gain, reverse=True
    )
    top = ranked[0]
    reasoning_fallback = False
    if top.recovery_gain <= positive_gain_threshold:
        if (
            reasoning_replay is not None
            and reasoning_replay.recovery_gain > positive_gain_threshold
        ):
            top = reasoning_replay
            reasoning_fallback = True
        else:
            raise ValueError("no replay produced a positive recovery gain")

    predicted_label = validate_v1_label(
        _v1_label_for_replay(top.replay_name, has_ingestion_trace=has_ingestion_trace)
    )

    if reasoning_fallback:
        all_close = [(predicted_label, 0.0)]
    else:
        all_close: list[tuple[str, float]] = []
        for result in ranked:
            delta = top.recovery_gain - result.recovery_gain
            if delta <= tie_margin:
                label = validate_v1_label(
                    _v1_label_for_replay(
                        result.replay_name, has_ingestion_trace=has_ingestion_trace
                    )
                )
                all_close.append((label, delta))

    top_k_labels = tuple(label for label, _ in all_close[:top_k])
    top2_labels = tuple(label for label, _ in all_close[:2])

    return AttributionResult(
        predicted_label=predicted_label,
        top_replay=top.replay_name,
        recovery_gain=top.recovery_gain,
        top2_labels=top2_labels,
        is_ambiguous=len(all_close) > 1,
        top_k_labels=top_k_labels,
        close_deltas=tuple(all_close),
        distractor_provenance_ids=tuple(
            e.source_id for e in distractor_edges
        ),
        distractor_provenance_edges=tuple(distractor_edges),
    )


def _label_for_replay(replay_name: str) -> str:
    try:
        return REPLAY_TO_LABEL[replay_name]
    except KeyError as exc:
        raise ValueError(f"unknown replay {replay_name!r}") from exc


def _v1_label_for_replay(replay_name: str, *, has_ingestion_trace: bool) -> str:
    if replay_name == "oracle_write" and not has_ingestion_trace:
        return "ingestion_error"
    try:
        return V1_REPLAY_TO_LABEL[replay_name]
    except KeyError as exc:
        raise ValueError(f"unknown replay {replay_name!r}") from exc
