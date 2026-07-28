"""Pipeline repair actions for counterfactual attribution.

Implements the live pipeline step actions:
- retrieval_error: Wrong retrieval decisions
- injection_error: Injection format/order or context management issues
- granularity_error: Granularity masking evidence
- safety_error: Safety layer blocking evidence (gated)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any

from ..core.models import MemoryItem

_logger = logging.getLogger(__name__)


class PipelineAction(Enum):
    """Pipeline step actions for generation points."""
    RETRIEVAL_ERROR = "retrieval_error"
    INJECTION_ERROR = "injection_error"
    GRANULARITY_ERROR = "granularity_error"
    SAFETY_ERROR = "safety_error"
    ITEM_STALE = "item_stale"
    ITEM_CONFLICT = "item_conflict"
    ITEM_POISONED = "item_poisoned"
    ITEM_WRONG = "item_wrong"
    ITEM_COMPRESSION_DISTORTED = "item_compression_distorted"
    IDENTITY = "identity"  # No intervention (baseline)

    @property
    def is_always_legal(self) -> bool:
        """True if action is always legal at any generation point."""
        return self in {
            PipelineAction.RETRIEVAL_ERROR,
            PipelineAction.INJECTION_ERROR,
            PipelineAction.GRANULARITY_ERROR,
            PipelineAction.ITEM_STALE,
            PipelineAction.ITEM_CONFLICT,
            PipelineAction.ITEM_POISONED,
            PipelineAction.ITEM_WRONG,
            PipelineAction.ITEM_COMPRESSION_DISTORTED,
            PipelineAction.IDENTITY,
        }

    @property
    def requires_gating(self) -> bool:
        """True if action requires metadata gating flags."""
        return self in {
            PipelineAction.SAFETY_ERROR,
        }

    @property
    def is_item_level(self) -> bool:
        """True if the action repairs a memory item rather than a pipeline step."""
        return self in ITEM_LEVEL_ACTIONS


class SelectPredicate(Enum):
    """Memory-item selector predicates used by pipeline repair operators."""

    MISSED_CANDIDATES = "missed_candidates"
    INJECTION_BUFFER = "injection_buffer"
    COARSE_RECALL = "coarse_recall"
    SAFETY_REVIEWED = "safety_reviewed"
    SAME_KEY_OLDER_VERSION = "same_key_older_version"
    MUTUALLY_EXCLUSIVE_ASSERTION = "mutually_exclusive_assertion"
    PROVENANCE_ANOMALY = "provenance_anomaly"
    RAW_EVENT_CONTRADICTION = "raw_event_contradiction"
    DISTORTED_SUMMARY = "distorted_summary"


class TransformPrimitive(Enum):
    """Structural transforms applied to selected memory items."""

    ADD_FROM_STORE = "add_from_store"
    RE_EMIT_ORDERED = "re_emit_ordered"
    EXPAND_GRANULARITY = "expand_granularity"
    RESTORE_REDACTED = "restore_redacted"
    REPLACE_WITH_NEWER = "replace_with_newer"
    DECONFLICT_HIGHER_PROVENANCE = "deconflict_higher_provenance"
    SUPPRESS_ITEM = "suppress_item"
    REPLACE_FROM_RAW = "replace_from_raw"
    EXPAND_TO_RAW = "expand_to_raw"


@dataclass(frozen=True)
class PipelineOperatorDSL:
    """SELECT(predicate) x TRANSFORM(primitive) spec for one step action."""

    action: PipelineAction
    selector: SelectPredicate
    transform: TransformPrimitive


PIPELINE_ACTION_OPERATOR_DSL: dict[PipelineAction, PipelineOperatorDSL] = {
    PipelineAction.RETRIEVAL_ERROR: PipelineOperatorDSL(
        action=PipelineAction.RETRIEVAL_ERROR,
        selector=SelectPredicate.MISSED_CANDIDATES,
        transform=TransformPrimitive.ADD_FROM_STORE,
    ),
    PipelineAction.INJECTION_ERROR: PipelineOperatorDSL(
        action=PipelineAction.INJECTION_ERROR,
        selector=SelectPredicate.INJECTION_BUFFER,
        transform=TransformPrimitive.RE_EMIT_ORDERED,
    ),
    PipelineAction.GRANULARITY_ERROR: PipelineOperatorDSL(
        action=PipelineAction.GRANULARITY_ERROR,
        selector=SelectPredicate.COARSE_RECALL,
        transform=TransformPrimitive.EXPAND_GRANULARITY,
    ),
    PipelineAction.SAFETY_ERROR: PipelineOperatorDSL(
        action=PipelineAction.SAFETY_ERROR,
        selector=SelectPredicate.SAFETY_REVIEWED,
        transform=TransformPrimitive.RESTORE_REDACTED,
    ),
    PipelineAction.ITEM_STALE: PipelineOperatorDSL(
        action=PipelineAction.ITEM_STALE,
        selector=SelectPredicate.SAME_KEY_OLDER_VERSION,
        transform=TransformPrimitive.REPLACE_WITH_NEWER,
    ),
    PipelineAction.ITEM_CONFLICT: PipelineOperatorDSL(
        action=PipelineAction.ITEM_CONFLICT,
        selector=SelectPredicate.MUTUALLY_EXCLUSIVE_ASSERTION,
        transform=TransformPrimitive.DECONFLICT_HIGHER_PROVENANCE,
    ),
    PipelineAction.ITEM_POISONED: PipelineOperatorDSL(
        action=PipelineAction.ITEM_POISONED,
        selector=SelectPredicate.PROVENANCE_ANOMALY,
        transform=TransformPrimitive.SUPPRESS_ITEM,
    ),
    PipelineAction.ITEM_WRONG: PipelineOperatorDSL(
        action=PipelineAction.ITEM_WRONG,
        selector=SelectPredicate.RAW_EVENT_CONTRADICTION,
        transform=TransformPrimitive.REPLACE_FROM_RAW,
    ),
    PipelineAction.ITEM_COMPRESSION_DISTORTED: PipelineOperatorDSL(
        action=PipelineAction.ITEM_COMPRESSION_DISTORTED,
        selector=SelectPredicate.DISTORTED_SUMMARY,
        transform=TransformPrimitive.EXPAND_TO_RAW,
    ),
}

ITEM_LEVEL_ACTIONS = frozenset(
    {
        PipelineAction.ITEM_STALE,
        PipelineAction.ITEM_CONFLICT,
        PipelineAction.ITEM_POISONED,
        PipelineAction.ITEM_WRONG,
        PipelineAction.ITEM_COMPRESSION_DISTORTED,
    }
)


def operator_dsl_for_action(action: PipelineAction) -> PipelineOperatorDSL | None:
    """Return the structural SELECT x TRANSFORM spec for a step action."""
    return PIPELINE_ACTION_OPERATOR_DSL.get(action)


def get_legal_actions(
    recall_set: tuple[MemoryItem, ...],
    generation_point: int,
    *,
    include_gated_actions: bool = True,
    include_item_actions: bool = False,
    intervention_config: dict[str, Any] | None = None,
    restrict_to_hop: int | None = None,
) -> list[PipelineAction]:
    """Get legal actions for a generation point.

    Args:
        recall_set: Memory items from retrieval
        generation_point: Which generation point in the trajectory
        include_gated_actions: Whether to include gated actions (safety)

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
        # Check if any items passed the structural safety metadata gate.
        has_safety_metadata = any(
            item.passed_safety_filter
            for item in recall_set
        )
        if has_safety_metadata:
            actions.append(PipelineAction.SAFETY_ERROR)

    if include_item_actions:
        actions.extend(_legal_item_actions(recall_set, intervention_config))

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
    recover?" Step-level attribution assigns credit from the recovery delta.

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
        return _repair_injection_context(context, recall_set, intervention_config)

    elif action == PipelineAction.GRANULARITY_ERROR:
        return _repair_granularity_context(context, recall_set, intervention_config)

    elif action == PipelineAction.SAFETY_ERROR:
        return _repair_safety_context(context, recall_set, intervention_config)

    elif action == PipelineAction.ITEM_STALE:
        return _repair_item_stale_context(context, recall_set, intervention_config)

    elif action == PipelineAction.ITEM_CONFLICT:
        return _repair_item_conflict_context(context, recall_set, intervention_config)

    elif action == PipelineAction.ITEM_POISONED:
        return _repair_item_poisoned_context(context, recall_set, intervention_config)

    elif action == PipelineAction.ITEM_WRONG:
        return _repair_item_wrong_context(context, recall_set, intervention_config)

    elif action == PipelineAction.ITEM_COMPRESSION_DISTORTED:
        return _repair_item_compression_context(context, recall_set, intervention_config)

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
        _format_memory_items(missed, intervention_config),
    )


def _repair_injection_context(
    context: str,
    recall_set: tuple[MemoryItem, ...],
    intervention_config: dict[str, Any] | None,
) -> str:
    """Re-render the injection buffer as an explicit, ordered memory block.

    Injection sits after retrieval and the safety filter in
    the pipeline, so its input buffer is every recalled item EXCEPT those the
    safety layer already redacted upstream — those never reached injection and
    re-rendering cannot resurrect them (that is ``safety_error``'s job).
    """
    buffer = tuple(item for item in recall_set if not item.passed_safety_filter)
    if not buffer:
        return context

    return _append_block(
        context,
        "Normalized injected memory",
        _format_memory_items(buffer, intervention_config),
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
    for item in _order_items_by_signal(coarse, intervention_config):
        for event_id in item.source_event_ids:
            text = event_texts.get(event_id)
            if text:
                lines.append(f"- [{item.memory_id}:{event_id}] {text}")
    if not lines:
        return context
    return _append_block(context, "Expanded memory granularity", "\n".join(lines))

def _repair_safety_context(
    context: str,
    recall_set: tuple[MemoryItem, ...],
    intervention_config: dict[str, Any] | None,
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
        _format_memory_items(safe_items, intervention_config),
    )


def _repair_item_stale_context(
    context: str,
    recall_set: tuple[MemoryItem, ...],
    intervention_config: dict[str, Any] | None,
) -> str:
    """Prefer the newest recalled version and explicitly demote older versions."""
    tolerance_days = int(
        (intervention_config or {}).get("item_timestamp_tolerance_days", 7)
    )
    from ..item_gate.freshness import arbitrate_freshness

    decision = arbitrate_freshness(
        recall_set,
        tolerance_days=tolerance_days,
    )
    if not decision.applicable:
        return context
    by_id = {item.memory_id: item for item in recall_set}
    newest_id = next(
        memory_id
        for memory_id, weight in decision.item_signal_hints
        if weight > 0.0
    )
    newest_item = by_id[newest_id]
    body = "\n".join(
        [
            f"- [{newest_item.memory_id} priority] {newest_item.text}",
            *(
                f"- [{memory_id} downweighted] older version retained only for audit"
                for memory_id in decision.demoted_ids
            ),
        ]
    )
    return _append_block(context, "Item-level stale repair", body)


def _repair_item_conflict_context(
    context: str,
    recall_set: tuple[MemoryItem, ...],
    intervention_config: dict[str, Any] | None,
) -> str:
    """Re-emit conflicting recall by provenance strength without hiding items."""
    if len(recall_set) < 2:
        return context
    non_poisoned = tuple(item for item in recall_set if not _is_poisoned_item(item))
    if len(non_poisoned) < 2:
        return context
    ranked = sorted(
        non_poisoned,
        key=lambda item: (-_item_provenance_score(item), item.memory_id),
    )
    if _item_provenance_score(ranked[0]) == _item_provenance_score(ranked[-1]):
        return context
    lines = []
    for i, item in enumerate(ranked):
        marker = " priority" if i == 0 else " downweighted"
        lines.append(f"- [{item.memory_id}{marker}] {item.text}")
    return _append_block(
        context,
        "Item-level conflict deconfliction",
        "\n".join(lines),
    )


def _repair_item_poisoned_context(
    context: str,
    recall_set: tuple[MemoryItem, ...],
    intervention_config: dict[str, Any] | None,
) -> str:
    """Suppress injection-like recalled items and re-emit trusted neighbors."""
    poisoned = tuple(item for item in recall_set if _is_poisoned_item(item))
    if not poisoned:
        return context
    trusted = tuple(item for item in recall_set if item not in poisoned)
    if not trusted:
        trusted = tuple(
            item for item in _candidate_items(recall_set, intervention_config, 0)
            if not _is_poisoned_item(item)
        )
    if not trusted:
        return context
    poisoned_ids = ", ".join(item.memory_id for item in poisoned)
    body = "\n".join(
        [
            f"- suppressed item ids: {poisoned_ids}",
            *(
                f"- [{item.memory_id} priority] {item.text}"
                for item in _order_items_by_signal(trusted, intervention_config)
            ),
        ]
    )
    return _append_block(context, "Item-level poisoned-item suppression", body)


def _repair_item_wrong_context(
    context: str,
    recall_set: tuple[MemoryItem, ...],
    intervention_config: dict[str, Any] | None,
) -> str:
    """Replace low-provenance contradictory items with reachable raw events."""
    if not recall_set:
        return context
    lines = _raw_repair_lines(
        recall_set,
        intervention_config,
        exclude_recalled_sources=False,
    )
    if not lines:
        return context
    return _append_block(
        context,
        "Item-level raw-event replacement candidates",
        "\n".join(lines),
    )


def _repair_item_compression_context(
    context: str,
    recall_set: tuple[MemoryItem, ...],
    intervention_config: dict[str, Any] | None,
) -> str:
    """Expand compressed summaries to raw detail available in the store trace."""
    if not recall_set:
        return context
    lines = _raw_repair_lines(
        recall_set,
        intervention_config,
        exclude_recalled_sources=True,
    )
    if not lines:
        # Some generated item suites mark the compressed item as the target but
        # still retain its source event. In that case, all raw non-query events
        # are the most faithful expansion surface.
        lines = _raw_repair_lines(
            recall_set,
            intervention_config,
            exclude_recalled_sources=False,
        )
    if not lines:
        return context
    return _append_block(
        context,
        "Item-level raw-detail expansion",
        "\n".join(lines),
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
            return _order_items_by_signal(
                _hop_local_items(tuple(configured), generation_point),
                intervention_config,
            )
    return _order_items_by_signal(recall_set, intervention_config)


def _format_memory_items(
    items: tuple[MemoryItem, ...],
    intervention_config: dict[str, Any] | None = None,
) -> str:
    lines = []
    hints = _item_signal_hints(intervention_config)
    for item in _order_items_by_signal(items, intervention_config):
        signal = hints.get(item.memory_id, 0.0)
        marker = ""
        if signal > 0.0:
            marker = " priority"
        elif signal < 0.0:
            marker = " downweighted"
        lines.append(f"- [{item.memory_id}{marker}] {item.text}")
    return "\n".join(lines)


def _order_items_by_signal(
    items: tuple[MemoryItem, ...],
    intervention_config: dict[str, Any] | None,
) -> tuple[MemoryItem, ...]:
    hints = _item_signal_hints(intervention_config)
    if not hints or len(items) <= 1:
        return items
    indexed = [
        (float(hints.get(item.memory_id, 0.0)), index, item)
        for index, item in enumerate(items)
    ]
    if not any(score != 0.0 for score, _index, _item in indexed):
        return items
    indexed.sort(key=lambda entry: (-entry[0], entry[1]))
    return tuple(item for _score, _index, item in indexed)


def _item_signal_hints(intervention_config: dict[str, Any] | None) -> dict[str, float]:
    if not intervention_config:
        return {}
    raw = (
        intervention_config.get("item_signal_hints")
        or intervention_config.get("item_priority_hints")
        or {}
    )
    if not isinstance(raw, dict):
        return {}
    hints: dict[str, float] = {}
    for key, value in raw.items():
        try:
            hints[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return hints


def _append_block(context: str, heading: str, body: str) -> str:
    if not body.strip():
        return context
    return f"{context.rstrip()}\n\n{heading}:\n{body}"


_POISON_SIGNATURE = re.compile(
    r"\b(ignore|disregard|override|system prompt|developer instruction|"
    r"answer\s+[^.;:]+instead|not trustworthy|malicious|injected instruction)\b",
    re.IGNORECASE,
)


def _legal_item_actions(
    recall_set: tuple[MemoryItem, ...],
    intervention_config: dict[str, Any] | None,
) -> list[PipelineAction]:
    """Return gold-free item actions whose structural preconditions are visible."""
    actions: list[PipelineAction] = []
    if len(_timestamped_items(recall_set)) >= 2:
        actions.append(PipelineAction.ITEM_STALE)
    if len(recall_set) >= 2:
        actions.append(PipelineAction.ITEM_CONFLICT)
    if any(_is_poisoned_item(item) for item in recall_set):
        actions.append(PipelineAction.ITEM_POISONED)
    if _raw_event_texts(intervention_config):
        actions.append(PipelineAction.ITEM_WRONG)
        actions.append(PipelineAction.ITEM_COMPRESSION_DISTORTED)
    return actions


def _timestamped_items(
    items: tuple[MemoryItem, ...],
) -> tuple[tuple[datetime, MemoryItem], ...]:
    out: list[tuple[datetime, MemoryItem]] = []
    for item in items:
        timestamp = _item_timestamp(item)
        if timestamp is not None:
            out.append((timestamp, item))
    return tuple(out)


def _item_timestamp(item: MemoryItem) -> datetime | None:
    raw = item.store
    if not isinstance(raw, str) or not raw.endswith("Z"):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _item_provenance_score(item: MemoryItem) -> float:
    score = 0.0
    if item.provenance:
        score += 2.0 * len(item.provenance)
        score -= sum(3.0 for edge in item.provenance if edge.tamper_detected)
        score += max((edge.timestamp for edge in item.provenance), default=0.0) / 1_000_000_000
    if item.source_event_ids:
        score += len(item.source_event_ids)
    if any(event_id not in {"e_query", "query"} for event_id in item.source_event_ids):
        score += 3.0
    timestamp = _item_timestamp(item)
    if timestamp is not None:
        score += timestamp.timestamp() / 1_000_000_000
    if _is_poisoned_item(item):
        score -= 10.0
    return score


def _is_poisoned_item(item: MemoryItem) -> bool:
    if any(getattr(edge, "tamper_detected", False) for edge in item.provenance):
        return True
    return bool(_POISON_SIGNATURE.search(item.text))


def _raw_repair_lines(
    recall_set: tuple[MemoryItem, ...],
    intervention_config: dict[str, Any] | None,
    *,
    exclude_recalled_sources: bool,
) -> list[str]:
    event_texts = _raw_event_texts(intervention_config)
    if not event_texts:
        return []
    recalled_sources = _recall_source_events(recall_set)
    lines: list[str] = []
    for event_id, text in event_texts.items():
        if event_id in {"e_query", "query"}:
            continue
        if exclude_recalled_sources and event_id in recalled_sources:
            continue
        lines.append(f"- [{event_id}] {text}")
    return lines
