"""Compatibility wrapper around the canonical two-branch confidence gate."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..core.models import MemoryItem, RetrievedItem
from .post_retrieve_hook import (
    ConfidenceFactors,
    compute_confidence_factors as _compute_canonical_confidence_factors,
    post_retrieve_hook,
)

_logger = logging.getLogger(__name__)


@dataclass
class ConfidenceGateResult:
    """Result of confidence gate evaluation."""

    trigger_subagent_loop: bool
    branch: str
    confidence_score: float
    factors: ConfidenceFactors
    corrected_items: tuple[MemoryItem, ...] | None = None
    correction_applied: bool = False

    @property
    def should_fill(self) -> bool:
        """True if evidence is missing (Fill branch)."""
        return self.branch == "fill"

    @property
    def should_fix(self) -> bool:
        """True if evidence exists and enters the Fix branch."""
        return self.branch == "fix"


def confidence_gate_hook(
    query: str,
    recall_set: tuple[MemoryItem, ...],
    *,
    confidence_threshold: float = 0.6,
    llm_client: Any = None,
    apply_light_corrections: bool = True,
    failure_memory_store: Any = None,
) -> ConfidenceGateResult:
    """Run the canonical DISCUSSION #5 confidence gate for MemoryItem recalls."""
    del confidence_threshold, llm_client
    decision = post_retrieve_hook(
        query,
        _as_retrieved_items(recall_set),
        failure_memory_store=failure_memory_store,
    )

    corrected_items = None
    correction_applied = False
    if decision.branch == "fix" and apply_light_corrections:
        corrected_items, correction_applied = _apply_light_corrections(
            recall_set, decision.factors
        )

    _logger.debug(
        "Confidence gate: branch=%s, confidence=%.3f, trigger=%s",
        decision.branch,
        decision.confidence,
        decision.trigger_diagnosis,
    )

    return ConfidenceGateResult(
        trigger_subagent_loop=decision.trigger_diagnosis,
        branch=decision.branch,
        confidence_score=decision.confidence,
        factors=decision.factors,
        corrected_items=corrected_items,
        correction_applied=correction_applied,
    )


def _compute_confidence_factors(
    query: str,
    recall_set: tuple[MemoryItem, ...],
    llm_client: Any,
) -> ConfidenceFactors:
    """Compatibility wrapper for older imports in tests/prototypes."""
    del llm_client
    return _compute_canonical_confidence_factors(query, _as_retrieved_items(recall_set))


def _as_retrieved_items(recall_set: tuple[MemoryItem, ...]) -> tuple[RetrievedItem, ...]:
    return tuple(
        RetrievedItem(memory_id=item.memory_id, text=item.text)
        for item in recall_set
    )


def _apply_light_corrections(
    recall_set: tuple[MemoryItem, ...],
    factors: ConfidenceFactors,
) -> tuple[tuple[MemoryItem, ...], bool]:
    corrected = list(recall_set)
    correction_applied = False

    if factors.conflict_signal > 0.7 and len(corrected) > 1:
        corrected = _resolve_conflicts(corrected)
        correction_applied = True

    if factors.retrieval_score_max < 0.7 and len(corrected) > 1:
        corrected.sort(key=lambda item: len(item.text), reverse=True)
        correction_applied = True

    return tuple(corrected), correction_applied


def _resolve_conflicts(items: list[MemoryItem]) -> list[MemoryItem]:
    resolved: list[MemoryItem] = []
    negation_words = {"not", "no", "never", "none", "neither", "nor", "without"}
    for item in items:
        tokens = set(item.text.casefold().split())
        contradicts_existing = any(
            bool(tokens & negation_words)
            != bool(set(existing.text.casefold().split()) & negation_words)
            for existing in resolved
        )
        if not contradicts_existing:
            resolved.append(item)
    return resolved
