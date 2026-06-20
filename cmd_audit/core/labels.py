"""Attribution label registry.

Target taxonomy (CONTEXT.md / DISCUSSION.md):
- **Pipeline step actions (4)**: retrieval_error, injection_error,
  granularity_error, safety_error
- **Item labels (5, Tier 2 Item Gate)**: item_wrong, item_stale, item_conflict,
  item_poisoned, item_compression_distorted
"""

from __future__ import annotations

# =============================================================================
# Pipeline step actions (4 live generation-point actions)
# =============================================================================

PIPELINE_STEP_ACTIONS = (
    "retrieval_error",
    "injection_error",
    "granularity_error",
    "safety_error",
)

PIPELINE_LABELS = frozenset(PIPELINE_STEP_ACTIONS)
PIPELINE_LABEL_ORDER = PIPELINE_STEP_ACTIONS  # Alias

# =============================================================================
# Item labels (5, Tier 2 Item Gate)
# =============================================================================

ITEM_LABELS = frozenset(
    {
        "item_wrong",
        "item_stale",
        "item_conflict",
        "item_poisoned",
        "item_compression_distorted",
    }
)

# =============================================================================
# Replay-to-label mapping (4 live step actions)
# =============================================================================

REPLAY_TO_LABEL = {
    "oracle_retrieval": "retrieval_error",
    "injection_oracle": "injection_error",
    "oracle_granularity": "granularity_error",
    "safety_off": "safety_error",
}
# Offline baseline replay names that still map to live step actions.
# Formation, reasoning, and route replays are intentionally absent. They do not
# produce live labels in the current runtime.

# =============================================================================
# Monitor anomaly reasons
# =============================================================================

MONITOR_ANOMALY_REASON_VALUES = (
    "answer_vs_evidence_mismatch",
    "retrieved_context_incomplete",
    "evidence_recall_low",
    "confidence_anomaly",
    "rpe_below_threshold",
)

VALID_MONITOR_ANOMALY_REASONS = frozenset(MONITOR_ANOMALY_REASON_VALUES)

# =============================================================================
# Validation
# =============================================================================


class LabelValidationError(ValueError):
    """Raised when a label violates CMD-Audit attribution scope."""


class MonitorAnomalyReasonError(ValueError):
    """Raised when monitor anomaly_reason is not a valid enum value."""


def validate_monitor_anomaly_reason(reason: str) -> str:
    """Return reason if it is a valid monitor anomaly_reason enum value."""
    if reason in VALID_MONITOR_ANOMALY_REASONS:
        return reason
    raise MonitorAnomalyReasonError(
        f"{reason!r} is not a valid monitor anomaly_reason; "
        f"must be one of {MONITOR_ANOMALY_REASON_VALUES}"
    )


def validate_label(label: str) -> str:
    """Return a valid pipeline step action label or raise."""
    if label in PIPELINE_LABELS:
        return label
    if label in ITEM_LABELS:
        raise LabelValidationError(
            f"{label!r} is an item label; use validate_item_label() instead"
        )
    raise LabelValidationError(f"{label!r} is not a valid step action label")


def validate_item_label(label: str) -> str:
    """Return a valid item label or raise."""
    if label in ITEM_LABELS:
        return label
    if label in PIPELINE_LABELS:
        raise LabelValidationError(
            f"{label!r} is a step action; use validate_label() instead"
        )
    raise LabelValidationError(f"{label!r} is not a valid item label")


def validate_diagnosis_label(label: str) -> str:
    """Return a valid live diagnosis label from either diagnosis stream."""
    if label in PIPELINE_LABELS:
        return label
    if label in ITEM_LABELS:
        return label
    raise LabelValidationError(f"{label!r} is not a valid diagnosis label")
