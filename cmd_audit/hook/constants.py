"""Constants for the confidence gate hook (DISCUSSION.md decision #5)."""

from __future__ import annotations

# =============================================================================
# 6 confidence factors
# =============================================================================

CONFIDENCE_FACTOR_NAMES: tuple[str, ...] = (
    "retrieval_score_max",
    "retrieval_score_entropy",
    "evidence_coverage",
    "memory_recency_min",
    "memory_recency_spread",
    "conflict_signal",
)

# Calibrated weights (cold-start heuristic)
CONFIDENCE_WEIGHTS: tuple[float, ...] = (
    1.5,   # retrieval_score_max: higher -> more confident
    -0.3,  # retrieval_score_entropy: high entropy -> less confident
    2.0,   # evidence_coverage: high coverage -> more confident
    0.0,   # memory_recency_min: placeholder
    0.0,   # memory_recency_spread: placeholder
    -1.0,  # conflict_signal: conflict -> less confident
)

CONFIDENCE_INTERCEPT: float = -0.5

# Threshold for Fill vs Fix branch
FILL_FIX_THRESHOLD: float = 0.5
