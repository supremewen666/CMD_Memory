"""RPE Judge feature extraction and per-replay logistic scoring."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from cmd_audit.models import RetrievedItem
from cmd_audit.retrieval_baselines import compute_bm25_scores, tokenize

from . import constants

if TYPE_CHECKING:
    from .post_retrieve_hook import ReplayScore


def compute_global_features(
    query: str,
    retrieved_items: tuple[RetrievedItem, ...],
) -> tuple[float, ...]:
    """Return the 6 global RPE Judge features for one retrieval result."""
    item_count = len(retrieved_items)
    item_count_feature = min(item_count, 10) / 10.0
    low_count = 1.0 if item_count < 2 else 0.0

    if not retrieved_items:
        return (0.0, 0.0, 0.0, item_count_feature, 0.0, low_count)

    query_tokens = tokenize(query)
    doc_tokens_list = [tokenize(item.text) for item in retrieved_items]
    if query_tokens:
        bm25_scores = [
            _unit_interval(score)
            for score in compute_bm25_scores(query_tokens, doc_tokens_list)
        ]
    else:
        bm25_scores = [0.0 for _ in retrieved_items]

    bm25_max = max(bm25_scores) if bm25_scores else 0.0
    bm25_mean = sum(bm25_scores) / len(bm25_scores) if bm25_scores else 0.0
    bm25_std = _stddev(bm25_scores) if len(bm25_scores) >= 2 else 0.0
    near_duplicate = _compute_near_duplicate(retrieved_items)

    return (
        bm25_max,
        bm25_mean,
        bm25_std,
        item_count_feature,
        near_duplicate,
        low_count,
    )


def compute_replay_type_one_hot(replay_name: str) -> tuple[float, ...]:
    """Return the 10-dimensional replay-name one-hot vector."""
    try:
        idx = constants.V1_REPLAY_NAME_ORDER.index(replay_name)
    except ValueError as exc:
        raise ValueError(f"unknown replay_name {replay_name!r}") from exc
    return tuple(1.0 if i == idx else 0.0 for i in range(10))


def extract_features(
    query: str,
    retrieved_items: tuple[RetrievedItem, ...],
    replay_name: str,
) -> tuple[float, ...]:
    """Return the 16-dimensional RPE Judge feature vector."""
    return compute_global_features(query, retrieved_items) + compute_replay_type_one_hot(
        replay_name
    )


def score_replays(
    query: str,
    retrieved_items: tuple[RetrievedItem, ...],
) -> tuple["ReplayScore", ...]:
    """Score all V1 replays and mark the current top-k selection."""
    from .post_retrieve_hook import ReplayScore

    raw_scores: list[tuple[str, float]] = []
    for replay_name in constants.V1_REPLAY_NAME_ORDER:
        features = extract_features(query, retrieved_items, replay_name)
        logit = _linear_logit(features)
        raw_scores.append((replay_name, _sigmoid(logit)))

    selected = set(_ranked_names(raw_scores)[: _effective_top_k(constants.TOP_K)])
    return tuple(
        ReplayScore(
            replay_name=name,
            p_score=p_score,
            selected=name in selected,
            is_sentinel=False,
        )
        for name, p_score in raw_scores
    )


def rank_scores(scores: tuple["ReplayScore", ...]) -> tuple["ReplayScore", ...]:
    """Return scores sorted by p descending with replay-order tie break."""
    order = {name: i for i, name in enumerate(constants.V1_REPLAY_NAME_ORDER)}
    return tuple(sorted(scores, key=lambda s: (-s.p_score, order[s.replay_name])))


def _ranked_names(raw_scores: list[tuple[str, float]]) -> list[str]:
    order = {name: i for i, name in enumerate(constants.V1_REPLAY_NAME_ORDER)}
    return [
        name
        for name, _ in sorted(raw_scores, key=lambda row: (-row[1], order[row[0]]))
    ]


def _linear_logit(features: tuple[float, ...]) -> float:
    weights = constants.RPE_JUDGE_WEIGHTS
    if len(weights) != 16:
        raise ValueError(
            f"RPE_JUDGE_WEIGHTS must contain 16 values, got {len(weights)}"
        )
    if len(features) != 16:
        raise ValueError(f"RPE Judge feature vector must be 16-D, got {len(features)}")
    return sum(w * f for w, f in zip(weights, features)) + constants.RPE_JUDGE_INTERCEPT


def _sigmoid(logit: float) -> float:
    if logit >= 0:
        z = math.exp(-logit)
        return 1.0 / (1.0 + z)
    z = math.exp(logit)
    return z / (1.0 + z)


def _unit_interval(value: float) -> float:
    if value <= 0.0:
        return 0.0
    if value >= 1.0:
        return 1.0
    return value


def _stddev(values: list[float]) -> float:
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance)


def _compute_near_duplicate(items: tuple[RetrievedItem, ...]) -> float:
    if len(items) < 2:
        return 0.0

    token_sets = [set(tokenize(item.text)) for item in items]
    max_jaccard = 0.0
    for i in range(len(token_sets)):
        for j in range(i + 1, len(token_sets)):
            left = token_sets[i]
            right = token_sets[j]
            union = left | right
            if not union:
                continue
            max_jaccard = max(max_jaccard, len(left & right) / len(union))
    return max_jaccard


def _effective_top_k(top_k: int) -> int:
    if top_k < 0:
        return 0
    return min(top_k, len(constants.V1_REPLAY_NAME_ORDER))

