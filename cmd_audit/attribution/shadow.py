"""Route/retrieval shadow disambiguation (Decision 34 R4)."""

from __future__ import annotations

from ..replays import ReplayResult


def disambiguate_route_retrieval_shadow(
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
