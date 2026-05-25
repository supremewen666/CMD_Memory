"""Two-stage Pre-CMD Hook for issue 0021."""

from __future__ import annotations

from dataclasses import dataclass

from cmd_audit.models import RetrievedItem

from . import constants
from .rpe_judge import rank_scores, score_replays


@dataclass(frozen=True)
class ReplayScore:
    replay_name: str
    p_score: float
    selected: bool
    is_sentinel: bool = False

    def __post_init__(self) -> None:
        if self.is_sentinel and self.p_score != -1.0:
            raise ValueError("sentinel ReplayScore must use p_score=-1.0")
        if self.p_score == -1.0 and not self.is_sentinel:
            raise ValueError("p_score=-1.0 is reserved for sentinel ReplayScore")
        if not self.is_sentinel and not 0.0 <= self.p_score <= 1.0:
            raise ValueError(f"p_score must be in [0, 1], got {self.p_score}")


@dataclass(frozen=True)
class PreCmdDecision:
    trigger_cmd: bool
    stage: str
    per_replay_scores: tuple[ReplayScore, ...]
    selected_replays: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.stage not in {"empty_ctx", "rpe_top_k", "rpe_below_threshold"}:
            raise ValueError(f"unknown PreCmdDecision stage {self.stage!r}")
        if len(self.per_replay_scores) != len(constants.V1_REPLAY_NAME_ORDER):
            raise ValueError("per_replay_scores must contain all 10 V1 replays")


def post_retrieve_hook(
    query: str,
    retrieved_items: tuple[RetrievedItem, ...],
    *,
    adapter_name: str = "",
    mode: str = "online",
) -> PreCmdDecision:
    """Run the two-stage Pre-CMD Hook.

    ``adapter_name`` is currently informational; per-adapter thresholds are
    explicitly deferred to V2.
    """
    del adapter_name
    if mode not in {"online", "offline"}:
        raise ValueError("mode must be 'online' or 'offline'")

    stage1 = _stage1_empty_ctx(query, retrieved_items, mode=mode)
    if stage1 is not None:
        return stage1

    return _stage2_rpe_judge(query, retrieved_items)


def _stage1_empty_ctx(
    query: str,
    retrieved_items: tuple[RetrievedItem, ...],
    *,
    mode: str,
) -> PreCmdDecision | None:
    if retrieved_items:
        return None

    if mode == "online":
        scores = _build_sentinel_scores()
    else:
        scores = _mark_all_selected(score_replays(query, retrieved_items))

    return PreCmdDecision(
        trigger_cmd=True,
        stage="empty_ctx",
        per_replay_scores=scores,
        selected_replays=constants.V1_REPLAY_NAME_ORDER,
    )


def _stage2_rpe_judge(
    query: str,
    retrieved_items: tuple[RetrievedItem, ...],
) -> PreCmdDecision:
    scores = score_replays(query, retrieved_items)
    ranked = rank_scores(scores)
    max_p = ranked[0].p_score if ranked else 0.0

    if max_p >= constants.FALLBACK_THRESHOLD:
        selected = tuple(score.replay_name for score in ranked if score.selected)
        return PreCmdDecision(
            trigger_cmd=True,
            stage="rpe_top_k",
            per_replay_scores=scores,
            selected_replays=selected,
        )

    return PreCmdDecision(
        trigger_cmd=False,
        stage="rpe_below_threshold",
        per_replay_scores=_clear_selected(scores),
        selected_replays=(),
    )


def _build_sentinel_scores() -> tuple[ReplayScore, ...]:
    return tuple(
        ReplayScore(
            replay_name=name,
            p_score=-1.0,
            selected=True,
            is_sentinel=True,
        )
        for name in constants.V1_REPLAY_NAME_ORDER
    )


def _mark_all_selected(scores: tuple[ReplayScore, ...]) -> tuple[ReplayScore, ...]:
    return tuple(
        ReplayScore(
            replay_name=score.replay_name,
            p_score=score.p_score,
            selected=True,
            is_sentinel=score.is_sentinel,
        )
        for score in scores
    )


def _clear_selected(scores: tuple[ReplayScore, ...]) -> tuple[ReplayScore, ...]:
    return tuple(
        ReplayScore(
            replay_name=score.replay_name,
            p_score=score.p_score,
            selected=False,
            is_sentinel=score.is_sentinel,
        )
        for score in scores
    )

