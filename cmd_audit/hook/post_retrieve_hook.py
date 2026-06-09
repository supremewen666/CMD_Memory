"""Two-branch confidence gate for CMD hook (DISCUSSION.md decision #5).

Hook = pure confidence gate, no diagnosis, no classification.

    retrieval recall -> hook(6 factors -> evidence in recall?)
                          |
                          +- NO (missing)  -> FILL branch
                          |     generate first, async re-extract
                          |     no diagnosis, no label
                          |
                          +- YES (present) -> FIX branch
                                lightweight correction (de-conflict / re-rank)
                                -> generate -> subagent loop (Tier2 -> Tier3)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from cmd_audit.core.models import RetrievedItem

from . import constants


@dataclass(frozen=True)
class ConfidenceFactors:
    """6 confidence factors for the two-branch gate decision."""

    retrieval_score_max: float
    retrieval_score_entropy: float
    evidence_coverage: float
    memory_recency_min: float
    memory_recency_spread: float
    conflict_signal: float

    def as_tuple(self) -> tuple[float, ...]:
        return (
            self.retrieval_score_max,
            self.retrieval_score_entropy,
            self.evidence_coverage,
            self.memory_recency_min,
            self.memory_recency_spread,
            self.conflict_signal,
        )


@dataclass(frozen=True)
class HookDecision:
    """Result of the two-branch confidence gate."""

    branch: str  # "fill" | "fix"
    confidence: float
    factors: ConfidenceFactors
    corrections: tuple[str, ...] = ()
    experience_bonus: float = 0.0

    def __post_init__(self) -> None:
        if self.branch not in {"fill", "fix"}:
            raise ValueError(f"branch must be 'fill' or 'fix', got {self.branch!r}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")

    @property
    def trigger_diagnosis(self) -> bool:
        """True if this triggers the diagnostic cascade (Tier 2/3)."""
        return self.branch == "fix"


def compute_confidence_factors(
    query: str,
    retrieved_items: tuple[RetrievedItem, ...],
) -> ConfidenceFactors:
    """Compute the 6 confidence factors from query and retrieved items."""
    if not retrieved_items:
        return ConfidenceFactors(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    scores = _compute_retrieval_scores(query, retrieved_items)

    return ConfidenceFactors(
        retrieval_score_max=max(scores) if scores else 0.0,
        retrieval_score_entropy=_compute_entropy(scores),
        evidence_coverage=_compute_evidence_coverage(query, retrieved_items),
        memory_recency_min=0.0,  # RetrievedItem doesn't have timestamp yet
        memory_recency_spread=0.0,
        conflict_signal=_compute_conflict_signal(retrieved_items),
    )


def post_retrieve_hook(
    query: str,
    retrieved_items: tuple[RetrievedItem, ...],
    *,
    failure_memory_store: Any = None,
) -> HookDecision:
    """Run the two-branch confidence gate."""
    factors = compute_confidence_factors(query, retrieved_items)
    confidence = _compute_confidence(factors)
    experience_bonus = _experience_confidence_bonus(failure_memory_store, query)
    confidence = min(1.0, confidence + experience_bonus)

    if confidence < constants.FILL_FIX_THRESHOLD:
        return HookDecision(
            branch="fill",
            confidence=confidence,
            factors=factors,
            experience_bonus=experience_bonus,
        )

    corrections = _identify_corrections(retrieved_items, factors)
    return HookDecision(
        branch="fix",
        confidence=confidence,
        factors=factors,
        corrections=corrections,
        experience_bonus=experience_bonus,
    )


# =============================================================================
# Internal helpers
# =============================================================================


def _compute_retrieval_scores(
    query: str, items: tuple[RetrievedItem, ...]
) -> list[float]:
    query_tokens = set(_tokenize(query))
    if not query_tokens:
        return [0.0] * len(items)

    scores = []
    for item in items:
        item_tokens = set(_tokenize(item.text))
        if not item_tokens:
            scores.append(0.0)
            continue
        overlap = len(query_tokens & item_tokens)
        scores.append(min(1.0, overlap / len(query_tokens)))
    return scores


def _tokenize(text: str) -> list[str]:
    return [w.lower().strip(".,!?;:'\"") for w in text.split() if len(w) > 1]


def _compute_entropy(scores: list[float]) -> float:
    if not scores or len(scores) < 2:
        return 0.0
    total = sum(scores)
    if total <= 0:
        return 0.0
    probs = [s / total for s in scores if s > 0]
    if not probs:
        return 0.0
    entropy = -sum(p * math.log(p) for p in probs)
    max_entropy = math.log(len(probs)) if len(probs) > 1 else 1.0
    return entropy / max_entropy if max_entropy > 0 else 0.0


def _compute_evidence_coverage(
    query: str, items: tuple[RetrievedItem, ...]
) -> float:
    query_tokens = set(_tokenize(query))
    if not query_tokens:
        return 0.0
    covered = set()
    for item in items:
        covered.update(query_tokens & set(_tokenize(item.text)))
    return len(covered) / len(query_tokens)


def _compute_conflict_signal(items: tuple[RetrievedItem, ...]) -> float:
    if len(items) < 2:
        return 0.0
    negation_words = {"not", "no", "never", "none", "neither", "nor", "without"}
    token_sets = [set(_tokenize(item.text)) for item in items]

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            left, right = token_sets[i], token_sets[j]
            if bool(left & negation_words) != bool(right & negation_words):
                union = left | right
                if union and len(left & right) / len(union) > 0.5:
                    return 1.0
    return 0.0


def _compute_confidence(factors: ConfidenceFactors) -> float:
    weights = constants.CONFIDENCE_WEIGHTS
    intercept = constants.CONFIDENCE_INTERCEPT
    logit = sum(w * f for w, f in zip(weights, factors.as_tuple())) + intercept
    if logit >= 0:
        return 1.0 / (1.0 + math.exp(-logit))
    z = math.exp(logit)
    return z / (1.0 + z)


def _experience_confidence_bonus(failure_memory_store: Any, query: str) -> float:
    if failure_memory_store is None:
        return 0.0
    getter = getattr(failure_memory_store, "get_hook_confidence_bonus", None)
    if getter is None:
        return 0.0
    try:
        return float(getter(query))
    except Exception:
        return 0.0


def _identify_corrections(
    items: tuple[RetrievedItem, ...], factors: ConfidenceFactors
) -> tuple[str, ...]:
    if factors.conflict_signal > 0.5:
        return tuple(item.memory_id for item in items)
    return ()
