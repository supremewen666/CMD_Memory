"""Issue 0021 Pre-CMD Hook public API."""

from .constants import (
    FALLBACK_THRESHOLD,
    RPE_JUDGE_INTERCEPT,
    RPE_JUDGE_WEIGHTS,
    TOP_K,
    V1_REPLAY_NAME_ORDER,
)
from .post_retrieve_hook import PreCmdDecision, ReplayScore, post_retrieve_hook
from .rpe_judge import (
    compute_global_features,
    compute_replay_type_one_hot,
    extract_features,
    rank_scores,
    score_replays,
)

__all__ = [
    "FALLBACK_THRESHOLD",
    "PreCmdDecision",
    "RPE_JUDGE_INTERCEPT",
    "RPE_JUDGE_WEIGHTS",
    "ReplayScore",
    "TOP_K",
    "V1_REPLAY_NAME_ORDER",
    "compute_global_features",
    "compute_replay_type_one_hot",
    "extract_features",
    "post_retrieve_hook",
    "rank_scores",
    "score_replays",
]

