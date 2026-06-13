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
    restrict_to_hop: int | None = None,
) -> list[PipelineAction]:
    """Get legal actions for a generation point.

    Args:
        recall_set: Memory items from retrieval
        generation_point: Which generation point in the trajectory
        include_gated_actions: Whether to include gated actions (graph/safety)

    Returns:
        List of legal actions for this generation point
    """
    if restrict_to_hop is not None and generation_point + 1 != restrict_to_hop:
        return [PipelineAction.IDENTITY]

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


def _hop_for_generation_point(generation_point: int) -> int:
    """Map a 0-based generation point to its 1-based hop number.

    generation_point 0 acts at hop 1, generation_point 1 at hop 2, etc.
    Mirrors the ``hop == generation_point + 1`` convention in
    ``get_legal_actions``.
    """
    return generation_point + 1


def _hop_local_items(
    items: tuple[MemoryItem, ...],
    generation_point: int,
) -> tuple[MemoryItem, ...]:
    """Restrict items to the hop owned by ``generation_point``.

    Multihop probe cases tag memory by hop via the ``m_hop{N}_`` memory_id
    prefix. A counterfactual repair at a generation point must only touch
    that hop's evidence, otherwise injecting a later hop's gold item lets an
    early-hop intervention recover the terminal answer and collapses credit
    onto generation_point 0.

    Items without an ``m_hop{N}_`` prefix carry no hop tag (non-multihop
    data) and are always kept, preserving the original full-recall behaviour
    for those cases.
    """
    hop = _hop_for_generation_point(generation_point)
    prefix = f"m_hop{hop}_"
    tagged = tuple(item for item in items if item.memory_id.startswith("m_hop"))
    if not tagged:
        return items
    local = tuple(item for item in items if item.memory_id.startswith(prefix))
    untagged = tuple(
        item for item in items if not item.memory_id.startswith("m_hop")
    )
    return local + untagged


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

    # A repair at this generation point may only touch its own hop's
    # evidence; otherwise an early-hop intervention can pull in a later hop's
    # gold item and spuriously recover the terminal answer.
    recall_set = _hop_local_items(recall_set, generation_point)

    if action == PipelineAction.RETRIEVAL_ERROR:
        return _repair_retrieval_context(context, recall_set, intervention_config, generation_point)

    elif action == PipelineAction.INJECTION_ERROR:
        return _repair_injection_context(context, recall_set)

    elif action == PipelineAction.GRANULARITY_ERROR:
        return _repair_granularity_context(context, recall_set, intervention_config)

    elif action == PipelineAction.GRAPH_ERROR:
        return _repair_graph_context(context, recall_set)

    elif action == PipelineAction.SAFETY_ERROR:
        return _repair_safety_context(context, recall_set)

    else:
        _logger.warning("Unknown pipeline action: %s", action)
        return context


def _source_events(item: MemoryItem) -> set[str]:
    return set(item.source_event_ids)


def _recall_source_events(recall_set: tuple[MemoryItem, ...]) -> set[str]:
    events: set[str] = set()
    for item in recall_set:
        events |= _source_events(item)
    return events


def _retrieval_missed_items(
    recall_set: tuple[MemoryItem, ...],
    candidates: tuple[MemoryItem, ...],
) -> tuple[MemoryItem, ...]:
    """Pool items absent from recall and source-event-disjoint from it.

    The signature of a pure miss: the retriever never surfaced this evidence
    and nothing recalled covers its source events. A finer item masked by a
    coarser recalled summary shares source events and is excluded here (it is
    a granularity signature instead).
    """
    recalled_ids = {item.memory_id for item in recall_set}
    recall_events = _recall_source_events(recall_set)
    return tuple(
        item
        for item in candidates
        if item.memory_id not in recalled_ids
        and not (_source_events(item) & recall_events)
    )


def _coarse_recall_items(
    recall_set: tuple[MemoryItem, ...],
) -> tuple[MemoryItem, ...]:
    """Recalled items that compressed more than one raw event into a summary.

    A granularity failure is a coarse summary that masked detail; the operation
    that repairs it de-summarizes such an item back to its constituent raw
    events. An item carrying a single source event is already atomic — there is
    nothing to de-summarize — so it is excluded here (injection, not
    granularity, owns re-ordering already-atomic recalled items).
    """
    return tuple(item for item in recall_set if len(item.source_event_ids) > 1)


def _raw_event_texts(
    intervention_config: dict[str, Any] | None,
) -> dict[str, str]:
    """Build an ``event_id -> text`` map from the configured raw events."""
    if not intervention_config:
        return {}
    raw_events = intervention_config.get("raw_events")
    if not raw_events:
        return {}
    return {ev.event_id: ev.text for ev in raw_events}


def _repair_retrieval_context(
    context: str,
    recall_set: tuple[MemoryItem, ...],
    intervention_config: dict[str, Any] | None,
    generation_point: int,
) -> str:
    """Add candidate items the retriever missed entirely.

    Precondition: fires only for pool items that are (a) absent from the recall
    set and (b) source-event-disjoint from everything recalled — the signature
    of a pure miss. A finer item masked by a coarser summary already in recall
    shares source events and belongs to ``granularity_error`` instead, so this
    action no-ops there (≈0 credit).
    """
    candidates = _candidate_items(recall_set, intervention_config, generation_point)
    missed = _retrieval_missed_items(recall_set, candidates)
    if not missed:
        return context

    return _append_block(
        context,
        "Corrected retrieval candidates",
        _format_memory_items(missed),
    )


def _repair_injection_context(
    context: str,
    recall_set: tuple[MemoryItem, ...],
) -> str:
    """Re-render the injection buffer as an explicit, ordered memory block.

    Injection sits after retrieval, graph-expansion, and the safety filter in
    the pipeline, so its input buffer is every recalled item EXCEPT those the
    safety layer already redacted upstream — those never reached injection and
    re-rendering cannot resurrect them (that is ``safety_error``'s job). Graph-
    expanded items DID enter the buffer and are re-rendered as-is, distractor
    and all.

    This reads only injection's own input boundary (data-flow scoping); it
    never computes whether another action would recover. When it co-fires with
    another action (e.g. graph, which additionally drops the distractor),
    recovery credit — not a hard rule — decides between them.
    """
    buffer = tuple(item for item in recall_set if not item.passed_safety_filter)
    if not buffer:
        return context

    return _append_block(
        context,
        "Normalized injected memory",
        _format_memory_items(buffer),
    )


def _repair_granularity_context(
    context: str,
    recall_set: tuple[MemoryItem, ...],
    intervention_config: dict[str, Any] | None,
) -> str:
    """De-summarize coarse recalled summaries back to their raw events.

    Operates in-place on recall, not the pool: a granularity failure is a
    recalled item that compressed several raw events into one summary, masking
    the detail. The repair expands each such item to the text of its
    constituent raw events (``source_event_ids`` -> ``raw_events`` text). Items
    already atomic (a single source event) carry no masked detail and are
    skipped — re-ordering those is injection's job. Because this touches no pool
    item, a pure retrieval miss cannot recover here.
    """
    coarse = _coarse_recall_items(recall_set)
    if not coarse:
        return context
    event_texts = _raw_event_texts(intervention_config)
    if not event_texts:
        return context

    lines = []
    for item in coarse:
        for event_id in item.source_event_ids:
            text = event_texts.get(event_id)
            if text:
                lines.append(f"- [{item.memory_id}:{event_id}] {text}")
    if not lines:
        return context
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
) -> str:
    """Restore evidence the safety layer redacted despite being safe.

    Precondition: fires only for recalled items flagged
    ``passed_safety_filter=True`` — items the structural safety gate marked
    safe but an over-broad filter redacted from context. Re-emits their text
    so the terminal answer can recover. No-ops (≈0 credit) when no such item
    is present, keeping this action exclusive to the safety signature.
    """
    safe_items = tuple(item for item in recall_set if item.passed_safety_filter)
    if not safe_items:
        return context
    return _append_block(
        context,
        "Safety-reviewed evidence candidates",
        _format_memory_items(safe_items),
    )


def _candidate_items(
    recall_set: tuple[MemoryItem, ...],
    intervention_config: dict[str, Any] | None,
    generation_point: int,
) -> tuple[MemoryItem, ...]:
    if intervention_config:
        configured = intervention_config.get("candidate_items")
        if configured:
            # The configured pool is the full extracted memory across all
            # hops; restrict it to the hop this generation point repairs so a
            # later hop's gold item can't leak into an early-hop intervention.
            return _hop_local_items(tuple(configured), generation_point)
    return recall_set


def _format_memory_items(items: tuple[MemoryItem, ...]) -> str:
    return "\n".join(f"- [{item.memory_id}] {item.text}" for item in items)


def _append_block(context: str, heading: str, body: str) -> str:
    if not body.strip():
        return context
    return f"{context.rstrip()}\n\n{heading}:\n{body}"
