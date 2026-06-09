"""Main Tier 2 Item Gate orchestration implementing the cost ladder.

Implements the three-step cost ladder from DISCUSSION.md decision #4:
① Timestamp → "old" flag only, no verdict
② Recall-set collision → stale/conflict classification
③ LOO reconstruction → wrong/compression_distorted classification

Item gate runs before pipeline MCTS to validate memory content correctness.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..core.models import MemoryItem
from .collision import compute_recall_set_divergence, CollisionResult
from .loo import (
    compute_loo_divergence,
    order_items_by_experience,
    LOOReconstructionResult,
)

_logger = logging.getLogger(__name__)


class ItemGateStatus(Enum):
    """Status of item gate processing."""
    PASS = "pass"  # Item content verified as correct
    ITEM_STALE = "item_stale"  # Item outdated, newer version available
    ITEM_CONFLICT = "item_conflict"  # Item conflicts with siblings, needs arbiter
    ITEM_WRONG = "item_wrong"  # Item content incorrect vs reconstruction
    ITEM_COMPRESSION_DISTORTED = "item_compression_distorted"  # Item over-compressed
    ITEM_POISONED = "item_poisoned"  # Adversarial content, requires HITL
    HITL_REQUIRED = "hitl_required"  # Edge case at threshold boundary, human review needed
    PROCESSING_FAILED = "processing_failed"  # Technical failure in gate processing


@dataclass
class ItemGateResult:
    """Complete result of Tier 2 item gate processing."""
    target_item: MemoryItem
    recall_set: tuple[MemoryItem, ...]
    query: str
    status: ItemGateStatus

    # Step ② results
    collision_results: list[CollisionResult]
    has_timestamp_conflicts: bool

    # Step ③ results (only if needed)
    loo_result: LOOReconstructionResult | None

    # Metadata
    processing_cost: int  # Number of LLM generations used
    decision_path: str  # Which steps were executed

    @property
    def needs_item_treatment(self) -> bool:
        """True if item requires treatment (not PASS)."""
        return self.status != ItemGateStatus.PASS

    @property
    def should_skip_tier3(self) -> bool:
        """True if item-wrong detected, should skip Tier 3 pipeline MCTS."""
        return self.status in {
            ItemGateStatus.ITEM_STALE,
            ItemGateStatus.ITEM_CONFLICT,
            ItemGateStatus.ITEM_WRONG,
            ItemGateStatus.ITEM_COMPRESSION_DISTORTED,
            ItemGateStatus.ITEM_POISONED,
            ItemGateStatus.HITL_REQUIRED,
        }

    @property
    def can_auto_update(self) -> bool:
        """True if item can be automatically updated (stale with clear newer version)."""
        return self.status == ItemGateStatus.ITEM_STALE

    @property
    def needs_human_arbitration(self) -> bool:
        """True if item needs human arbitration (conflict, poisoned, or edge case)."""
        return self.status in {
            ItemGateStatus.ITEM_CONFLICT,
            ItemGateStatus.ITEM_POISONED,
            ItemGateStatus.HITL_REQUIRED,
        }


def run_item_gate(
    client: Any,
    target_item: MemoryItem,
    recall_set: tuple[MemoryItem, ...],
    query: str,
    *,
    divergence_threshold: float = 0.5,
    timestamp_tolerance_days: int = 7,
    reconstruction_prompt_template: str | None = None,
) -> ItemGateResult:
    """Run complete Tier 2 item gate cost ladder for target item.

    Implements the three-step process from DISCUSSION.md:
    ① Timestamp analysis (0 cost, flag only)
    ② Recall-set collision detection (≤C(5,2) contrasts, 0 generation)
    ③ LOO reconstruction if needed (1 generation + contrast)

    Args:
        client: LLM client for divergence computation and reconstruction
        target_item: Memory item to validate
        recall_set: All recalled items for this query (including target)
        query: Original query that retrieved these items
        divergence_threshold: Minimum divergence for significant differences
        timestamp_tolerance_days: Days tolerance for "same period" classification
        reconstruction_prompt_template: Custom template for LOO reconstruction

    Returns:
        ItemGateResult with status, costs, and detailed results
    """
    processing_cost = 0
    decision_path = []

    try:
        # Step ①: Timestamp analysis (0 cost, informational only)
        _logger.debug("Item gate step ①: timestamp analysis for %s", target_item.memory_id)
        has_timestamp_conflicts = _analyze_timestamps(target_item, recall_set)
        decision_path.append("timestamp_analysis")

        if client is None:
            return ItemGateResult(
                target_item=target_item,
                recall_set=recall_set,
                query=query,
                status=ItemGateStatus.PROCESSING_FAILED,
                collision_results=[],
                has_timestamp_conflicts=has_timestamp_conflicts,
                loo_result=None,
                processing_cost=processing_cost,
                decision_path=" → ".join(decision_path) + " → no_client",
            )

        # Step ②: Recall-set collision detection
        _logger.debug("Item gate step ②: recall-set collision for %s", target_item.memory_id)
        collision_results = compute_recall_set_divergence(
            client,
            recall_set,
            divergence_threshold=divergence_threshold,
            timestamp_tolerance_days=timestamp_tolerance_days,
        )
        decision_path.append("collision_detection")

        # Check if target item involved in any collision
        target_collision = _find_target_collision(target_item, collision_results)

        if target_collision is not None:
            # Target item has collision, classify based on collision type
            if target_collision.is_stale_collision:
                status = ItemGateStatus.ITEM_STALE
            elif target_collision.is_conflict_collision:
                status = ItemGateStatus.ITEM_CONFLICT
            else:
                # Fallback for unexpected collision type
                status = ItemGateStatus.ITEM_CONFLICT

            return ItemGateResult(
                target_item=target_item,
                recall_set=recall_set,
                query=query,
                status=status,
                collision_results=collision_results,
                has_timestamp_conflicts=has_timestamp_conflicts,
                loo_result=None,
                processing_cost=processing_cost,
                decision_path=" → ".join(decision_path),
            )

        # Step ③: LOO reconstruction (only if no collision detected)
        _logger.debug("Item gate step ③: LOO reconstruction for %s", target_item.memory_id)
        loo_result = compute_loo_divergence(
            client,
            target_item,
            recall_set,  # Full store including target
            query,
            divergence_threshold=divergence_threshold,
            reconstruction_prompt_template=reconstruction_prompt_template,
        )
        decision_path.append("loo_reconstruction")

        if loo_result.reconstruction_successful:
            processing_cost += 1  # One generation for reconstruction

        # Determine final status based on LOO result
        if loo_result.item_label == "item_wrong":
            status = ItemGateStatus.ITEM_WRONG
        elif loo_result.item_label == "item_compression_distorted":
            status = ItemGateStatus.ITEM_COMPRESSION_DISTORTED
        else:
            # No significant issues found
            status = ItemGateStatus.PASS

        return ItemGateResult(
            target_item=target_item,
            recall_set=recall_set,
            query=query,
            status=status,
            collision_results=collision_results,
            has_timestamp_conflicts=has_timestamp_conflicts,
            loo_result=loo_result,
            processing_cost=processing_cost,
            decision_path=" → ".join(decision_path),
        )

    except Exception as exc:
        _logger.error("Item gate processing failed for %s: %s", target_item.memory_id, exc)
        return ItemGateResult(
            target_item=target_item,
            recall_set=recall_set,
            query=query,
            status=ItemGateStatus.PROCESSING_FAILED,
            collision_results=[],
            has_timestamp_conflicts=False,
            loo_result=None,
            processing_cost=processing_cost,
            decision_path=" → ".join(decision_path) + " → FAILED",
        )


def run_item_gate_for_recall_set(
    client: Any,
    recall_set: tuple[MemoryItem, ...],
    query: str,
    *,
    failure_memory_store: Any = None,
    divergence_threshold: float = 0.5,
    timestamp_tolerance_days: int = 7,
    reconstruction_prompt_template: str | None = None,
) -> ItemGateResult | None:
    """Run item gate over recall-set items in experience-prioritized order.

    This is the runtime Tier 2 entrypoint: history can move likely item-fault
    targets earlier, and the first non-PASS verdict stops the scan.
    """
    ordered_items = order_items_by_experience(
        query, recall_set, failure_memory_store=failure_memory_store
    )
    last_result: ItemGateResult | None = None
    for target_item in ordered_items:
        result = run_item_gate(
            client,
            target_item,
            recall_set,
            query,
            divergence_threshold=divergence_threshold,
            timestamp_tolerance_days=timestamp_tolerance_days,
            reconstruction_prompt_template=reconstruction_prompt_template,
        )
        last_result = result
        if result.status != ItemGateStatus.PASS:
            return result
    return last_result


def _analyze_timestamps(
    target_item: MemoryItem,
    recall_set: tuple[MemoryItem, ...],
) -> bool:
    """Step ①: Analyze timestamps for "old" flag (informational only).

    This step produces no verdict, only flags potential timestamp issues.
    The actual collision classification happens in step ②.

    Note: Using store field as timestamp placeholder since MemoryItem
    doesn't have a metadata field in the current structure.

    Args:
        target_item: Item being analyzed
        recall_set: All recalled items

    Returns:
        True if timestamp conflicts detected (informational only)
    """
    target_timestamp = target_item.store
    if not target_timestamp or not target_timestamp.endswith('Z'):
        return False

    # Check if any other items have conflicting timestamps
    for other_item in recall_set:
        if other_item.memory_id == target_item.memory_id:
            continue

        other_timestamp = other_item.store
        if not other_timestamp or not other_timestamp.endswith('Z'):
            continue

        # Simple heuristic: if timestamps differ significantly, flag as conflict
        try:
            # This is just flagging, not definitive classification
            if target_timestamp != other_timestamp:
                return True
        except (ValueError, TypeError):
            continue

    return False


def _find_target_collision(
    target_item: MemoryItem,
    collision_results: list[CollisionResult],
) -> CollisionResult | None:
    """Find collision result involving the target item.

    Args:
        target_item: Item to find collisions for
        collision_results: All detected collisions in recall set

    Returns:
        First collision involving target item, or None if not found
    """
    for collision in collision_results:
        if (collision.item_a.memory_id == target_item.memory_id or
            collision.item_b.memory_id == target_item.memory_id):
            return collision
    return None
