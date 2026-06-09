"""Pipeline repair actions for MCTS tree expansion.

Implements the 5 pipeline step actions from DISCUSSION.md decision F:
- retrieval_error: Wrong retrieval decisions
- injection_error: Injection format/order or context management issues
- granularity_error: Granularity masking evidence
- graph_error: Graph expansion introducing distraction (gated)
- safety_error: Safety layer blocking evidence (gated)
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from ..core.models import MemoryItem

_logger = logging.getLogger(__name__)


class PipelineAction(Enum):
    """Pipeline step actions for generation points."""
    RETRIEVAL_ERROR = "retrieval_error"
    INJECTION_ERROR = "injection_error"
    GRANULARITY_ERROR = "granularity_error"
    GRAPH_ERROR = "graph_error"
    SAFETY_ERROR = "safety_error"
    IDENTITY = "identity"  # No intervention (baseline)

    @property
    def is_always_legal(self) -> bool:
        """True if action is always legal at any generation point."""
        return self in {
            PipelineAction.RETRIEVAL_ERROR,
            PipelineAction.INJECTION_ERROR,
            PipelineAction.GRANULARITY_ERROR,
            PipelineAction.IDENTITY,
        }

    @property
    def requires_gating(self) -> bool:
        """True if action requires metadata gating flags."""
        return self in {
            PipelineAction.GRAPH_ERROR,
            PipelineAction.SAFETY_ERROR,
        }


def get_legal_actions(
    recall_set: tuple[MemoryItem, ...],
    generation_point: int,
    *,
    include_gated_actions: bool = True,
) -> list[PipelineAction]:
    """Get legal actions for a generation point.

    Args:
        recall_set: Memory items from retrieval
        generation_point: Which generation point in the trajectory
        include_gated_actions: Whether to include gated actions (graph/safety)

    Returns:
        List of legal actions for this generation point
    """
    actions = [
        PipelineAction.RETRIEVAL_ERROR,
        PipelineAction.INJECTION_ERROR,
        PipelineAction.GRANULARITY_ERROR,
        PipelineAction.IDENTITY,
    ]

    if include_gated_actions:
        # Check if any items have graph expansion metadata
        has_graph_expansion = any(
            item.is_graph_expanded for item in recall_set
        )
        if has_graph_expansion:
            actions.append(PipelineAction.GRAPH_ERROR)

        # Check if any items passed the structural safety metadata gate.
        has_safety_metadata = any(
            item.passed_safety_filter
            for item in recall_set
        )
        if has_safety_metadata:
            actions.append(PipelineAction.SAFETY_ERROR)

    return actions


def apply_pipeline_action(
    action: PipelineAction,
    context: str,
    recall_set: tuple[MemoryItem, ...],
    generation_point: int,
    *,
    intervention_config: dict[str, Any] | None = None,
) -> str:
    """Apply a counterfactual repair action at a generation point.

    The action label names the suspected pipeline failure. The transform asks:
    "if this failure were repaired at this point, would the terminal answer
    recover?" MCTS then assigns credit from the recovery delta.

    Args:
        action: Pipeline action to apply
        context: Current context at this generation point
        recall_set: Available memory items
        generation_point: Which generation point we're at
        intervention_config: Optional configuration for interventions

    Returns:
        Modified context after applying the action
    """
    if action == PipelineAction.IDENTITY:
        return context

    if action == PipelineAction.RETRIEVAL_ERROR:
        return _repair_retrieval_context(context, recall_set, intervention_config)

    elif action == PipelineAction.INJECTION_ERROR:
        return _repair_injection_context(context, recall_set)

    elif action == PipelineAction.GRANULARITY_ERROR:
        return _repair_granularity_context(context, recall_set, intervention_config)

    elif action == PipelineAction.GRAPH_ERROR:
        return _repair_graph_context(context, recall_set)

    elif action == PipelineAction.SAFETY_ERROR:
        return _repair_safety_context(context, recall_set, intervention_config)

    else:
        _logger.warning("Unknown pipeline action: %s", action)
        return context


def _repair_retrieval_context(
    context: str,
    recall_set: tuple[MemoryItem, ...],
    intervention_config: dict[str, Any] | None,
) -> str:
    """Replace the retrieval boundary with best available candidate memory."""
    candidates = _candidate_items(recall_set, intervention_config)
    if not candidates:
        return context

    return _append_block(
        context,
        "Corrected retrieval candidates",
        _format_memory_items(candidates),
    )


def _repair_injection_context(
    context: str,
    recall_set: tuple[MemoryItem, ...],
) -> str:
    """Normalize injection into an explicit, ordered memory block."""
    if not recall_set:
        return context

    return _append_block(
        context,
        "Normalized injected memory",
        _format_memory_items(recall_set),
    )


def _repair_granularity_context(
    context: str,
    recall_set: tuple[MemoryItem, ...],
    intervention_config: dict[str, Any] | None,
) -> str:
    """Expose lower-granularity memory text when summaries mask evidence."""
    candidates = _candidate_items(recall_set, intervention_config)
    if not candidates:
        return context

    lines = []
    for item in candidates:
        source = (
            f" source_events={','.join(item.source_event_ids)}"
            if item.source_event_ids
            else ""
        )
        lines.append(f"- [{item.memory_id}{source}] {item.text}")
    return _append_block(context, "Expanded memory granularity", "\n".join(lines))


def _repair_graph_context(
    context: str,
    recall_set: tuple[MemoryItem, ...],
) -> str:
    """Suppress graph-expanded distractors and keep direct memory evidence."""
    direct_items = tuple(item for item in recall_set if not item.is_graph_expanded)
    if not direct_items:
        return context
    return _append_block(
        context,
        "Graph expansion disabled; direct memory only",
        _format_memory_items(direct_items),
    )


def _repair_safety_context(
    context: str,
    recall_set: tuple[MemoryItem, ...],
    intervention_config: dict[str, Any] | None,
) -> str:
    """Restore safe evidence candidates after an over-broad safety filter."""
    candidates = _candidate_items(recall_set, intervention_config)
    if not candidates:
        return context
    return _append_block(
        context,
        "Safety-reviewed evidence candidates",
        _format_memory_items(candidates),
    )


def _candidate_items(
    recall_set: tuple[MemoryItem, ...],
    intervention_config: dict[str, Any] | None,
) -> tuple[MemoryItem, ...]:
    if intervention_config:
        configured = intervention_config.get("candidate_items")
        if configured:
            return tuple(configured)
    return recall_set


def _format_memory_items(items: tuple[MemoryItem, ...]) -> str:
    return "\n".join(f"- [{item.memory_id}] {item.text}" for item in items)


def _append_block(context: str, heading: str, body: str) -> str:
    if not body.strip():
        return context
    return f"{context.rstrip()}\n\n{heading}:\n{body}"
