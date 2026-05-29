"""Public API for cmd_audit.core."""


class PhraseMatchShortcutWarning(DeprecationWarning):
    """Legacy phrase-match scoring path is active."""


from .constants import COUPLED_FAILURE_TIE_MARGIN
from .labels import (
    DEFERRED_PIPELINE_LABELS,
    LabelValidationError,
    MONITOR_ANOMALY_REASON_VALUES,
    MonitorAnomalyReasonError,
    OUT_OF_SCOPE_ITEM_LABELS,
    PIPELINE_LABEL_ORDER,
    PIPELINE_LABELS,
    PIPELINE_LABELS_BASE,
    PIPELINE_LABELS_BASE_ORDER,
    REPLAY_TO_LABEL,
    REPLAY_TO_LABEL_BASE,
    VALID_MONITOR_ANOMALY_REASONS,
    validate_label,
    validate_label_base,
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
    "DEFERRED_PIPELINE_LABELS",
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
    "OUT_OF_SCOPE_ITEM_LABELS",
    "PIPELINE_LABEL_ORDER",
    "PIPELINE_LABELS",
    "PIPELINE_LABELS_BASE",
    "PIPELINE_LABELS_BASE_ORDER",
    "PhraseMatchShortcutWarning",
    "ProbeCase",
    "ProvenanceEdge",
    "REPLAY_TO_LABEL",
    "REPLAY_TO_LABEL_BASE",
    "RetrievedItem",
    "TokenLogprob",
    "VALID_MONITOR_ANOMALY_REASONS",
    "validate_label",
    "validate_label_base",
    "validate_monitor_anomaly_reason",
]

