"""Recovery-gain ranking and attribution assignment."""

from __future__ import annotations

from ..core.labels import REPLAY_TO_LABEL, validate_label, validate_label_base
from ..replays import ReplayResult
from .failure import AttributionResult, build_abstain_result
from .shadow import disambiguate_route_retrieval_shadow


def assign_attribution(
    replay_results: tuple[ReplayResult, ...],
    *,
    positive_gain_threshold: float = 0.0,
    tie_margin: float = 0.05,
    # V1 features below — all optional with safe defaults
    has_ingestion_trace: bool = True,
    top_k: int = 2,
    distractor_edges: tuple = (),
    gold_stores: frozenset[str] | tuple[str, ...] | None = None,
    queried_stores: frozenset[str] | tuple[str, ...] | None = None,
    default_store: str | None = None,
    shadow_noise_band: float = 0.05,
    use_extended_labels: bool = True,
    separate_reasoning_axis: bool = True,
) -> AttributionResult:
    """Unified attribution from replay recovery gains.

    Merges V0 (6-label base) and V1 (11-label extended) attribution into a
    single function. V1 features are enabled via kwargs:

    - ``has_ingestion_trace``: when False and top replay is oracle_write,
      label becomes ``ingestion_error`` instead of ``write_error``.
    - ``top_k``: controls how many close-delta labels appear in
      ``top_k_labels``. ``top2_labels`` always caps at 2 for backward compat.
    - ``distractor_edges``: provenance edges for distractor items.
    - ``gold_stores``, ``queried_stores``, ``default_store``,
      ``shadow_noise_band``: route/retrieval shadow disambiguation parameters.
    - ``use_extended_labels``: when True (default), validates against 11-label
      ``PIPELINE_LABELS``; when False, validates against 6-label
      ``PIPELINE_LABELS_BASE`` (V0 boundary).
    - ``separate_reasoning_axis``: when True (default),
      ``evidence_given_reasoning`` is treated as answer-axis fallback and does
      not participate in evidence-axis ranking. When False, it ranks inline
      (V0 behavior).

    ``evidence_given_reasoning`` is an answer-axis replay. When
    ``separate_reasoning_axis=True``, it does not participate in evidence-axis
    ranking; it is used only as a fallback when no evidence-axis replay clears
    ``positive_gain_threshold``. In that fallback path, ``close_deltas``
    contains only ``("reasoning_error", 0.0)`` so answer-axis and evidence-axis
    deltas are never mixed.

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

    # Separate reasoning replay if requested (V1 behavior)
    reasoning_replay = None
    rankable_results = replay_results
    if separate_reasoning_axis:
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

    # Check for zero/negative gain and reasoning fallback
    reasoning_fallback = False
    if top.recovery_gain <= positive_gain_threshold:
        if (
            separate_reasoning_axis
            and reasoning_replay is not None
            and reasoning_replay.recovery_gain > positive_gain_threshold
        ):
            top = reasoning_replay
            reasoning_fallback = True
        else:
            return build_abstain_result(
                top.recovery_gain,
                distractor_edges=distractor_edges,
            )

    # Shadow disambiguation (V1 feature)
    shadow_resolution: str | None = None
    if not reasoning_fallback and gold_stores is not None:
        top, shadow_resolution = disambiguate_route_retrieval_shadow(
            ranked,
            gold_stores=gold_stores,
            queried_stores=queried_stores,
            default_store=default_store,
            shadow_noise_band=shadow_noise_band,
        )

    # Label validation
    label_fn = validate_label if use_extended_labels else validate_label_base
    predicted_label = label_fn(
        _label_for_replay(top.replay_name, has_ingestion_trace=has_ingestion_trace)
    )

    # Build close deltas
    if reasoning_fallback:
        all_close = [(predicted_label, 0.0)]
    else:
        all_close: list[tuple[str, float]] = []
        for result in ranked:
            delta = top.recovery_gain - result.recovery_gain
            if delta <= tie_margin:
                label = label_fn(
                    _label_for_replay(
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

    # V0 compatibility: when use_extended_labels=False and
    # separate_reasoning_axis=False, return empty close_deltas
    close_deltas_out = tuple(all_close) if use_extended_labels else ()

    return AttributionResult(
        predicted_label=predicted_label,
        top_replay=top.replay_name,
        recovery_gain=top.recovery_gain,
        top2_labels=top2_labels,
        is_ambiguous=len(all_close) > 1,
        top_k_labels=top_k_labels,
        close_deltas=close_deltas_out,
        distractor_provenance_ids=tuple(e.source_id for e in distractor_edges),
        distractor_provenance_edges=tuple(distractor_edges),
        shadow_replay_resolution=shadow_resolution,
    )


def _label_for_replay(replay_name: str, *, has_ingestion_trace: bool = True) -> str:
    """Map replay name to label, with write/ingestion split."""
    if replay_name == "oracle_write" and not has_ingestion_trace:
        return "ingestion_error"
    try:
        return REPLAY_TO_LABEL[replay_name]
    except KeyError as exc:
        raise ValueError(f"unknown replay {replay_name!r}") from exc
