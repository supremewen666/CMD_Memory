"""Attribution label registry.

Canonical 11-label pipeline taxonomy. PIPELINE_LABELS will be narrowed in W2
(Decision 35) to 10 labels excluding reasoning_error; ALL_LABELS will be the
11-label superset.
"""

from __future__ import annotations

PIPELINE_LABEL_ORDER = (
    "write_error",
    "compression_error",
    "premature_extraction_error",
    "retrieval_error",
    "injection_error",
    "reasoning_error",
    "ingestion_error",
    "route_error",
    "granularity_error",
    "graph_error",
    "safety_error",
)

PIPELINE_LABELS = frozenset(PIPELINE_LABEL_ORDER)

# Historical V0 boundary subset, retained for boundary-validation tests until
# W2 finalization (Decision 35) collapses to PIPELINE_LABELS (10) + ALL_LABELS (11).
PIPELINE_LABELS_BASE_ORDER = PIPELINE_LABEL_ORDER[:6]
PIPELINE_LABELS_BASE = frozenset(PIPELINE_LABELS_BASE_ORDER)

OUT_OF_SCOPE_ITEM_LABELS = frozenset(
    {
        "item_wrong",
        "item_stale",
        "item_conflict",
        "item_poisoned",
        "item_compression_distorted",
    }
)

DEFERRED_PIPELINE_LABELS: frozenset[str] = frozenset()

MONITOR_ANOMALY_REASON_VALUES = (
    "answer_vs_evidence_mismatch",
    "retrieved_context_incomplete",
    "evidence_recall_low",
    "confidence_anomaly",
    "rpe_below_threshold",
)

VALID_MONITOR_ANOMALY_REASONS = frozenset(MONITOR_ANOMALY_REASON_VALUES)

REPLAY_TO_LABEL = {
    "oracle_write": "write_error",
    "oracle_compression": "compression_error",
    "verbatim_event_oracle": "premature_extraction_error",
    "oracle_retrieval": "retrieval_error",
    "injection_oracle": "injection_error",
    "evidence_given_reasoning": "reasoning_error",
    "oracle_route": "route_error",
    "oracle_granularity": "granularity_error",
    "graph_off": "graph_error",
    "safety_off": "safety_error",
}

REPLAY_TO_LABEL_BASE = {
    name: label
    for name, label in REPLAY_TO_LABEL.items()
    if label in PIPELINE_LABELS_BASE
}


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
    """Return a valid pipeline label or raise with the boundary reason."""
    if label in PIPELINE_LABELS:
        return label
    if label in OUT_OF_SCOPE_ITEM_LABELS:
        raise LabelValidationError(
            f"{label!r} is a bad memory item label and is outside CMD-Audit attribution scope"
        )
    if label in DEFERRED_PIPELINE_LABELS:
        raise LabelValidationError(
            f"{label!r} is deferred and is outside current attribution scope"
        )
    raise LabelValidationError(f"{label!r} is not a CMD-Audit attribution label")


def validate_label_base(label: str) -> str:
    """Return a valid base-subset label (historical V0 boundary).

    Retained for boundary-validation tests until W2 finalization. Use
    ``validate_label`` for canonical attribution scope.
    """
    if label in PIPELINE_LABELS_BASE:
        return label
    if label in OUT_OF_SCOPE_ITEM_LABELS:
        raise LabelValidationError(
            f"{label!r} is a bad memory item label and is outside base attribution scope"
        )
    if label in DEFERRED_PIPELINE_LABELS:
        raise LabelValidationError(
            f"{label!r} is deferred and is outside base attribution scope"
        )
    if label in PIPELINE_LABELS:
        raise LabelValidationError(
            f"{label!r} is an extended pipeline label and is outside base attribution scope"
        )
    raise LabelValidationError(f"{label!r} is not a CMD-Audit base attribution label")

