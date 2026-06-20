"""Offline replay-baseline attribution for current step actions.

The live CMD runtime does not use replay-portfolio ranking. ``run_case`` routes
through the hook's Fill/Fix branch, then Tier 2 item gate and Tier 3 attribution.

This module remains only for offline replay baselines and migration experiments.
It assigns labels for the five current pipeline step actions when a supported
counterfactual replay is the best positive-gain intervention. Formation,
reasoning, and route replays are intentionally out of scope: formation failures
route to Fill, reasoning faults emerge as no recovering step intervention, and
route is absorbed by ``retrieval_error`` in the live action set.
"""

from __future__ import annotations

from ..core.labels import REPLAY_TO_LABEL, validate_label
from ..replays import ReplayResult
from .failure import (
    FAILURE_REASON_OUT_OF_SCOPE_REPLAY,
    AttributionResult,
    build_abstain_result,
)


def assign_replay_baseline_attribution(
    replay_results: tuple[ReplayResult, ...],
    *,
    positive_gain_threshold: float = 0.0,
    tie_margin: float = 0.05,
    top_k: int = 2,
    distractor_edges: tuple = (),
) -> AttributionResult:
    """Rank offline replay deltas against the current 5 step-action taxonomy."""
    if not replay_results:
        raise ValueError("at least one replay result is required")

    ranked_all = sorted(
        replay_results,
        key=lambda result: result.recovery_gain,
        reverse=True,
    )
    top_overall = ranked_all[0]
    if top_overall.recovery_gain <= positive_gain_threshold:
        return build_abstain_result(
            top_overall.recovery_gain,
            distractor_edges=distractor_edges,
        )

    if top_overall.replay_name not in REPLAY_TO_LABEL:
        return build_abstain_result(
            top_overall.recovery_gain,
            distractor_edges=distractor_edges,
            failure_reason=FAILURE_REASON_OUT_OF_SCOPE_REPLAY,
        )

    ranked_supported = tuple(
        result for result in ranked_all if result.replay_name in REPLAY_TO_LABEL
    )
    top = ranked_supported[0]
    predicted_label = _label_for_replay(top.replay_name)

    close: list[tuple[str, float]] = []
    for result in ranked_supported:
        delta = top.recovery_gain - result.recovery_gain
        if delta <= tie_margin:
            close.append((_label_for_replay(result.replay_name), delta))

    top_k_labels = tuple(label for label, _ in close[:top_k])
    top2_labels = tuple(label for label, _ in close[:2])

    return AttributionResult(
        predicted_label=predicted_label,
        top_replay=top.replay_name,
        recovery_gain=top.recovery_gain,
        top2_labels=top2_labels,
        is_ambiguous=len(close) > 1,
        top_k_labels=top_k_labels,
        close_deltas=tuple(close),
        distractor_provenance_ids=tuple(e.source_id for e in distractor_edges),
        distractor_provenance_edges=tuple(distractor_edges),
    )


def assign_attribution(
    replay_results: tuple[ReplayResult, ...],
    *,
    positive_gain_threshold: float = 0.0,
    tie_margin: float = 0.05,
    top_k: int = 2,
    distractor_edges: tuple = (),
) -> AttributionResult:
    """Compatibility wrapper for offline replay-baseline attribution."""
    return assign_replay_baseline_attribution(
        replay_results,
        positive_gain_threshold=positive_gain_threshold,
        tie_margin=tie_margin,
        top_k=top_k,
        distractor_edges=distractor_edges,
    )


def _label_for_replay(replay_name: str) -> str:
    """Map a supported offline replay name to a current step-action label."""
    try:
        return validate_label(REPLAY_TO_LABEL[replay_name])
    except KeyError as exc:
        raise ValueError(
            f"replay {replay_name!r} is outside the current 5 step-action taxonomy"
        ) from exc
