"""Fill/Fix branch router for V2 confidence gate hook."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

from cmd_audit.core.models import MemoryItem

from .post_retrieve_hook import HookDecision


@dataclass
class FillBranchResult:
    """Result from Fill branch processing."""
    generated_response: str  # this turn's answer (best effort)
    async_task: Optional[str]  # ID for async re-extraction task


@dataclass
class FixBranchResult:
    """Result from Fix branch processing."""
    fixed_items: List[MemoryItem]  # after de-conflict / re-rank
    proceed_to_diagnosis: bool


def route_fill_branch(
    query: str,
    items: List[MemoryItem],
    generate_fn: Callable[[str, List[MemoryItem]], str],
) -> FillBranchResult:
    """
    Fill branch: evidence missing.
    1. Generate response with available context (best effort)
    2. Trigger async re-extraction (not blocking this turn)
    3. No diagnosis, no label
    """
    # Generate best-effort response with available items
    generated_response = generate_fn(query, items)

    # TODO: Implement async re-extraction task scheduling
    # For now, return placeholder async task ID
    async_task = f"re_extract_{hash(query)}" if items else None

    return FillBranchResult(
        generated_response=generated_response,
        async_task=async_task,
    )


def route_fix_branch(
    query: str,
    items: List[MemoryItem],
    hook_decision: HookDecision,
) -> FixBranchResult:
    """
    Fix branch: evidence present but may need lightweight fix.
    1. If conflict_signal high: remove conflicting items or re-rank
    2. Return fixed items for generation
    3. After generation, proceed to diagnostic cascade (Tier2 → Tier3)
    """
    fixed_items = list(items)  # Copy to avoid mutating input

    # Check if conflicts need resolution.
    conflict_signal = hook_decision.factors.conflict_signal

    if conflict_signal > 0.5:  # High conflict threshold
        # Simple conflict resolution: remove items with contradictory content
        fixed_items = _resolve_conflicts(fixed_items)

    # Re-rank items by relevance/recency if needed
    if hook_decision.factors.retrieval_score_max < 0.7:
        fixed_items = _rerank_items(query, fixed_items)

    return FixBranchResult(
        fixed_items=fixed_items,
        proceed_to_diagnosis=True,  # Always proceed to diagnosis in fix branch
    )


def _resolve_conflicts(items: List[MemoryItem]) -> List[MemoryItem]:
    """Remove conflicting items using simple heuristics."""
    if len(items) <= 1:
        return items

    # Simple conflict resolution: prefer more recent items
    # In practice, this would use proper item_gate collision detection

    # Group items by content similarity and keep most recent from each group
    resolved_items = []

    for item in items:
        # Simple heuristic: if item doesn't contradict existing resolved items, keep it
        has_conflict = False
        item_content = item.text.lower()

        for resolved_item in resolved_items:
            resolved_content = resolved_item.text.lower()

            # Check for contradictory keywords
            contradictory_pairs = [
                ('yes', 'no'), ('true', 'false'), ('correct', 'incorrect'),
                ('valid', 'invalid'), ('success', 'failure'), ('pass', 'fail')
            ]

            for word1, word2 in contradictory_pairs:
                if ((word1 in item_content and word2 in resolved_content) or
                    (word2 in item_content and word1 in resolved_content)):
                    has_conflict = True
                    break

            if has_conflict:
                break

        if not has_conflict:
            resolved_items.append(item)

    return resolved_items


def _rerank_items(query: str, items: List[MemoryItem]) -> List[MemoryItem]:
    """Re-rank items by relevance to query."""
    if len(items) <= 1:
        return items

    # Simple relevance scoring based on keyword overlap
    query_words = set(word.lower().strip('.,!?;:') for word in query.split())

    def relevance_score(item: MemoryItem) -> float:
        item_content = item.text.lower()
        item_words = set(word.lower().strip('.,!?;:') for word in item_content.split())

        # Jaccard similarity
        intersection = len(query_words & item_words)
        union = len(query_words | item_words)

        if union == 0:
            return 0.0

        return intersection / union

    # Sort by relevance score (descending)
    return sorted(items, key=relevance_score, reverse=True)
