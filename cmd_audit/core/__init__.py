"""Public API for cmd_audit.core."""


class PhraseMatchShortcutWarning(DeprecationWarning):
    """Legacy phrase-match scoring path is active."""


from .constants import COUPLED_FAILURE_TIE_MARGIN
from .labels import (
    ITEM_LABELS,
    LabelValidationError,
    MONITOR_ANOMALY_REASON_VALUES,
    MonitorAnomalyReasonError,
    PIPELINE_LABEL_ORDER,
    PIPELINE_LABELS,
    PIPELINE_STEP_ACTIONS,
    REPLAY_TO_LABEL,
    VALID_MONITOR_ANOMALY_REASONS,
    validate_diagnosis_label,
    validate_item_label,
    validate_label,
    validate_monitor_anomaly_reason,
)
from .llm_client import (
    LLMClient,
    LLMClientConfig,
    LLMClientError,
    LLMEmptyResponseError,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
    TokenLogprob,
)
from .models import (
    Citation,
    ProbeCase,
    ProvenanceEdge,
    RetrievedItem,
)

__all__ = [
    "Citation",
    "COUPLED_FAILURE_TIE_MARGIN",
    "ITEM_LABELS",
    "LabelValidationError",
    "LLMClient",
    "LLMClientConfig",
    "LLMClientError",
    "LLMEmptyResponseError",
    "LLMResponse",
    "LLMResponseError",
    "LLMTimeoutError",
    "LLMUnavailableError",
    "MONITOR_ANOMALY_REASON_VALUES",
    "MonitorAnomalyReasonError",
    "PIPELINE_LABEL_ORDER",
    "PIPELINE_LABELS",
    "PIPELINE_STEP_ACTIONS",
    "PhraseMatchShortcutWarning",
    "ProbeCase",
    "ProvenanceEdge",
    "REPLAY_TO_LABEL",
    "RetrievedItem",
    "TokenLogprob",
    "VALID_MONITOR_ANOMALY_REASONS",
    "validate_diagnosis_label",
    "validate_item_label",
    "validate_label",
    "validate_monitor_anomaly_reason",
]
