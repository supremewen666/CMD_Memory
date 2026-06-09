"""CMD Hook public API - two-branch confidence gate."""

from .constants import (
    CONFIDENCE_FACTOR_NAMES,
    CONFIDENCE_INTERCEPT,
    CONFIDENCE_WEIGHTS,
    FILL_FIX_THRESHOLD,
)
from .post_retrieve_hook import (
    ConfidenceFactors,
    HookDecision,
    compute_confidence_factors,
    post_retrieve_hook,
)

__all__ = [
    "CONFIDENCE_FACTOR_NAMES",
    "CONFIDENCE_INTERCEPT",
    "CONFIDENCE_WEIGHTS",
    "FILL_FIX_THRESHOLD",
    "ConfidenceFactors",
    "HookDecision",
    "compute_confidence_factors",
    "post_retrieve_hook",
]
