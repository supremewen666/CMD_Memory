"""Convert a frozen semantic relation and independent order into safe action."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from cmd_audit.counterfactual.item_ordering import (
    OrderingEvidence, OrderingPolicy, OrderingRelation, OrderingVerdict,
    resolve_ordering,
)

__all__ = ["ActionMode", "ActionabilityVerdict", "resolve_actionability"]


class ActionMode(str, Enum):
    DESTRUCTIVE = "destructive"
    ANNOTATE_ONLY = "annotate_only"
    ABSTAIN = "abstain"


@dataclass(frozen=True)
class ActionabilityVerdict:
    relation_edge_id: str
    target_item_id: str | None
    survivor_item_id: str | None
    mode: ActionMode
    reason_code: str
    ordering: OrderingVerdict

    def __post_init__(self) -> None:
        if not self.relation_edge_id:
            raise ValueError("actionability verdict needs a relation edge ID")
        has_target = self.target_item_id is not None
        has_survivor = self.survivor_item_id is not None
        if has_target != has_survivor:
            raise ValueError("target and survivor must be present together")
        if has_target and self.target_item_id == self.survivor_item_id:
            raise ValueError("target and survivor must be distinct")
        directional = self.ordering.relation in {
            OrderingRelation.LEFT_TARGET_RIGHT_SURVIVES,
            OrderingRelation.RIGHT_TARGET_LEFT_SURVIVES,
        }
        if self.mode is ActionMode.DESTRUCTIVE and (not has_target or not directional):
            raise ValueError(
                "destructive actionability needs a unique target and trusted direction"
            )
        if self.mode is not ActionMode.DESTRUCTIVE and has_target:
            raise ValueError("non-destructive actionability cannot carry a target")


def resolve_actionability(
    left_item_id: str,
    right_item_id: str,
    relation: object,
    left_evidence: OrderingEvidence,
    right_evidence: OrderingEvidence,
    *,
    relation_edge_id: str,
    ordering_policy: OrderingPolicy,
) -> ActionabilityVerdict:
    """Authorize a destructive target only for a positive measured relation."""
    if not relation_edge_id:
        raise ValueError("actionability requires a frozen relation_edge_id")
    relation_value = getattr(relation, "value", relation)
    ordering = resolve_ordering(left_evidence, right_evidence, policy=ordering_policy)
    if left_evidence.item_id != left_item_id or right_evidence.item_id != right_item_id:
        return ActionabilityVerdict(
            relation_edge_id,
            None,
            None,
            ActionMode.ABSTAIN,
            "ordering_evidence_item_mismatch",
            ordering,
        )
    if relation_value != "same_slot_different_value":
        return ActionabilityVerdict(
            relation_edge_id,
            None,
            None,
            ActionMode.ABSTAIN,
            "relation_not_positive",
            ordering,
        )
    if ordering.relation is OrderingRelation.LEFT_TARGET_RIGHT_SURVIVES:
        return ActionabilityVerdict(
            relation_edge_id,
            left_item_id,
            right_item_id,
            ActionMode.DESTRUCTIVE,
            "positive_relation_with_trusted_order",
            ordering,
        )
    if ordering.relation is OrderingRelation.RIGHT_TARGET_LEFT_SURVIVES:
        return ActionabilityVerdict(
            relation_edge_id,
            right_item_id,
            left_item_id,
            ActionMode.DESTRUCTIVE,
            "positive_relation_with_trusted_order",
            ordering,
        )
    reason = (
        "positive_relation_with_conflicting_direction"
        if ordering.relation is OrderingRelation.CONFLICTING
        else "positive_relation_without_safe_direction"
    )
    return ActionabilityVerdict(
        relation_edge_id,
        None,
        None,
        ActionMode.ANNOTATE_ONLY,
        reason,
        ordering,
    )
