"""Error-Cause-Solution (ECS) draft dataclass and validation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.labels import ITEM_LABELS, validate_diagnosis_label, validate_item_label


# Natural-language phrases that re-declare forbidden item labels.
_FORBIDDEN_NL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bitem[_\s]?(is\s+)?wrong\b",
        r"\bitem[_\s]?(is\s+)?stale\b",
        r"\bitem[_\s]?(is\s+)?conflict(ed|ing)?\b",
        r"\bitem[_\s]?(is\s+)?poisoned\b",
        r"\bcompression[_\s]?distorted\b",
    )
)


class ECSCauseValidationError(ValueError):
    """Raised when ECS cause contains forbidden item label names or equivalents."""


def _validate_ecs_cause(cause: str) -> str:
    """Reject ECS cause text that uses forbidden item label names or NL equivalents."""
    lowered = cause.casefold()
    # Pipeline label terms are allowed here; only out-of-scope item labels leak scope.
    for label in ITEM_LABELS:
        if label in lowered:
            raise ECSCauseValidationError(
                f"ECS cause must not use forbidden item label {label!r}; "
                f"describe item state instead (e.g., 'stored preference was outdated')"
            )
    for pattern in _FORBIDDEN_NL_PATTERNS:
        if pattern.search(lowered):
            raise ECSCauseValidationError(
                "ECS cause contains natural-language equivalent of a forbidden "
                "item label; use descriptive state language instead"
            )
    return cause


def _validate_stream_cause(label: str, cause: str) -> str:
    if label in ITEM_LABELS:
        validate_item_label(label)
        return cause
    return _validate_ecs_cause(cause)


@dataclass(frozen=True)
class ECSDraft:
    """Error-Cause-Solution record drafted from recovered actions.

    ``predicted_label`` is retained for compatibility with older callers, but
    its pivot semantics are ``recovered_action``: the step action that produced
    counterfactual recovery, not a classifier verdict.
    """

    case_id: str
    predicted_label: str
    cause: str
    corrected_memory: str
    repair_guidance: str
    repaired_evidence_block: str
    cascade_candidates: tuple[str, ...] = ()
    recovered_action: str | None = None
    recovery_delta: float = 0.0

    def __post_init__(self) -> None:
        validate_diagnosis_label(self.predicted_label)
        if self.recovered_action is None:
            object.__setattr__(self, "recovered_action", self.predicted_label)
        else:
            validate_diagnosis_label(self.recovered_action)
            if self.recovered_action != self.predicted_label:
                raise ValueError(
                    "ECSDraft.recovered_action must match predicted_label "
                    "while the compatibility field remains in use"
                )
        _validate_stream_cause(self.predicted_label, self.cause)
