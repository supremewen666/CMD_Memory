"""V0 and V1 attribution label boundaries."""

from __future__ import annotations

V0_PIPELINE_LABEL_ORDER = (
    "write_error",
    "compression_error",
    "premature_extraction_error",
    "retrieval_error",
    "injection_error",
    "reasoning_error",
)

V0_PIPELINE_LABELS = frozenset(V0_PIPELINE_LABEL_ORDER)

V1_PIPELINE_LABEL_ORDER = (
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

V1_PIPELINE_LABELS = frozenset(V1_PIPELINE_LABEL_ORDER)

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
}

V1_REPLAY_TO_LABEL = {
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


class LabelValidationError(ValueError):
    """Raised when a label violates CMD-Audit V0 scope."""


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


def validate_v0_label(label: str) -> str:
    """Return a valid V0 pipeline label or raise with the boundary reason."""

    if label in V0_PIPELINE_LABELS:
        return label
    if label in OUT_OF_SCOPE_ITEM_LABELS:
        raise LabelValidationError(
            f"{label!r} is a bad memory item label and is outside V0 attribution scope"
        )
    if label in DEFERRED_PIPELINE_LABELS:
        raise LabelValidationError(
            f"{label!r} is deferred to V1/V2 and is outside V0 attribution scope"
        )
    if label in V1_PIPELINE_LABELS:
        raise LabelValidationError(
            f"{label!r} is a V1 pipeline label and is outside V0 attribution scope"
        )
    raise LabelValidationError(f"{label!r} is not a CMD-Audit V0 attribution label")


def validate_v1_label(label: str) -> str:
    """Return a valid V1 pipeline label or raise with the boundary reason."""

    if label in V1_PIPELINE_LABELS:
        return label
    if label in OUT_OF_SCOPE_ITEM_LABELS:
        raise LabelValidationError(
            f"{label!r} is a bad memory item label and is outside V1 attribution scope"
        )
    if label in DEFERRED_PIPELINE_LABELS:
        raise LabelValidationError(
            f"{label!r} is deferred to V2 and is outside V1 attribution scope"
        )
    raise LabelValidationError(f"{label!r} is not a CMD-Audit V1 attribution label")
