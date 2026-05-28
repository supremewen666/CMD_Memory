"""Operation-level attribution from replay deltas."""

from __future__ import annotations

from dataclasses import dataclass

from .core.labels import (
    REPLAY_TO_LABEL,
    validate_label,
    validate_label_base,
)
from .replays import ReplayResult


# Failure reason enum for principled abstention (Decision 35 R1).
FAILURE_REASON_ZERO_GAIN = "zero_gain"
FAILURE_REASON_NEGATIVE_GAIN = "negative_gain"


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
    shadow_replay_resolution: str | None = None
    attribution_failed: bool = False
    failure_reason: str | None = None


def _build_abstain_result(
    top_recovery_gain: float,
    *,
    distractor_edges: tuple = (),
) -> AttributionResult:
    """Construct a principled-abstention AttributionResult.

    Decision 35 R1: zero/negative-gain returns ``attribution_failed=True``
    instead of raising. Downstream callers check the flag and decide whether
    to skip post-repair / log abstention coverage.
    """
    failure_reason = (
        FAILURE_REASON_NEGATIVE_GAIN
        if top_recovery_gain < 0.0
        else FAILURE_REASON_ZERO_GAIN
    )
    return AttributionResult(
        predicted_label="",
        top_replay="",
        recovery_gain=top_recovery_gain,
        top2_labels=(),
        is_ambiguous=False,
        top_k_labels=(),
        close_deltas=(),
        distractor_provenance_ids=tuple(e.source_id for e in distractor_edges),
        distractor_provenance_edges=tuple(distractor_edges),
        shadow_replay_resolution=None,
        attribution_failed=True,
        failure_reason=failure_reason,
    )


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
        return _build_abstain_result(top.recovery_gain)

    predicted_label = validate_label_base(_label_for_replay(top.replay_name))
    close = [
        validate_label_base(_label_for_replay(result.replay_name))
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
    gold_stores: frozenset[str] | tuple[str, ...] | None = None,
    queried_stores: frozenset[str] | tuple[str, ...] | None = None,
    default_store: str | None = None,
    shadow_noise_band: float = 0.05,
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

    Route/retrieval shadow disambiguation
    -------------------------------------
    When ``oracle_route`` and ``oracle_retrieval`` are tied within
    ``shadow_noise_band`` they recover the same gold evidence by two
    different paths.  Without store information the rubric softmax tail
    decides arbitrarily.  When ``gold_stores`` and ``queried_stores`` are
    provided, the tie is broken structurally:

      * gold evidence sits in the default store → ``retrieval_error``
        (route is a shadow of retrieval — same store, different framing)
      * gold evidence sits in a store the baseline never queried →
        ``route_error`` (route is a real intervention)
      * mixed / inconclusive → keep the rubric-ranked top1 and flag
        ``shadow_replay_resolution="ambiguous"`` for downstream review.
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
            return _build_abstain_result(
                top.recovery_gain,
                distractor_edges=distractor_edges,
            )

    shadow_resolution: str | None = None
    if not reasoning_fallback:
        top, shadow_resolution = _disambiguate_route_retrieval_shadow(
            ranked,
            gold_stores=gold_stores,
            queried_stores=queried_stores,
            default_store=default_store,
            shadow_noise_band=shadow_noise_band,
        )

    predicted_label = validate_label(
        _v1_label_for_replay(top.replay_name, has_ingestion_trace=has_ingestion_trace)
    )

    if reasoning_fallback:
        all_close = [(predicted_label, 0.0)]
    else:
        all_close: list[tuple[str, float]] = []
        for result in ranked:
            delta = top.recovery_gain - result.recovery_gain
            if delta <= tie_margin:
                label = validate_label(
                    _v1_label_for_replay(
                        result.replay_name, has_ingestion_trace=has_ingestion_trace
                    )
                )
                all_close.append((label, delta))
        # When shadow disambiguation chose a runner-up, surface it as the
        # head of close_deltas so top_k / top2 reflect the resolved order.
        if shadow_resolution in {"prefer_retrieval", "prefer_route"}:
            head = (predicted_label, 0.0)
            all_close = [head] + [
                (label, delta) for label, delta in all_close if label != predicted_label
            ]

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
        shadow_replay_resolution=shadow_resolution,
    )


def _disambiguate_route_retrieval_shadow(
    ranked: list[ReplayResult],
    *,
    gold_stores: frozenset[str] | tuple[str, ...] | None,
    queried_stores: frozenset[str] | tuple[str, ...] | None,
    default_store: str | None,
    shadow_noise_band: float,
) -> tuple[ReplayResult, str | None]:
    """Resolve oracle_route vs oracle_retrieval ties by store membership.

    Returns ``(chosen_replay, resolution_tag)``.  ``resolution_tag`` is
    ``None`` when the rule does not fire (no tie, missing store info, or
    only one of the two replays present in the top pair).
    """
    if len(ranked) < 2:
        return ranked[0], None
    first, second = ranked[0], ranked[1]
    pair = {first.replay_name, second.replay_name}
    if pair != {"oracle_retrieval", "oracle_route"}:
        return first, None
    if abs(first.recovery_gain - second.recovery_gain) > shadow_noise_band:
        return first, None
    if not gold_stores:
        return first, None

    gold_set = frozenset(gold_stores)
    queried_set = frozenset(queried_stores or ())
    default_aliases = {default_store, "default"} - {None, ""}

    retrieval_replay = first if first.replay_name == "oracle_retrieval" else second
    route_replay = first if first.replay_name == "oracle_route" else second

    in_default = bool(gold_set & default_aliases)
    outside_queried_and_default = gold_set.isdisjoint(queried_set | default_aliases)

    if in_default:
        return retrieval_replay, "prefer_retrieval"
    if outside_queried_and_default:
        return route_replay, "prefer_route"
    return first, "ambiguous"


def _label_for_replay(replay_name: str) -> str:
    try:
        return REPLAY_TO_LABEL[replay_name]
    except KeyError as exc:
        raise ValueError(f"unknown replay {replay_name!r}") from exc


def _v1_label_for_replay(replay_name: str, *, has_ingestion_trace: bool) -> str:
    if replay_name == "oracle_write" and not has_ingestion_trace:
        return "ingestion_error"
    try:
        return REPLAY_TO_LABEL[replay_name]
    except KeyError as exc:
        raise ValueError(f"unknown replay {replay_name!r}") from exc
