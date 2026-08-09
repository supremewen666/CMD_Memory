from datetime import datetime, timezone

from cmd_audit.counterfactual.actionability import ActionMode, resolve_actionability
from cmd_audit.counterfactual.item_ordering import (
    EvidenceReliability,
    OrderingEvidence,
    OrderingPolicy,
)


POLICY = OrderingPolicy(
    policy_version="ordering-policy-test-v1",
    accepted_sources=("observed_at",),
    source_semantics=(("observed_at", "chronology_lower_target"),),
)


def evidence(item_id: str, when: int | None = None) -> OrderingEvidence:
    return OrderingEvidence(
        item_id=item_id,
        observed_at=(datetime(2024, 1, when, tzinfo=timezone.utc) if when else None),
        observed_at_domain=("deployment-clock-v1" if when else None),
        provenance="runtime-sidecar",
        audit_version="ordering-audit-v1",
        deployment_visible=True,
        reliability=EvidenceReliability.TRUSTED,
    )


def test_positive_relation_with_trusted_direction_selects_only_superseded_target() -> None:
    verdict = resolve_actionability(
        "old", "current", "same_slot_different_value", evidence("old", 1), evidence("current", 2),
        relation_edge_id="edge-1",
        ordering_policy=POLICY,
    )
    assert verdict.mode is ActionMode.DESTRUCTIVE
    assert verdict.target_item_id == "old"
    assert verdict.survivor_item_id == "current"


def test_positive_relation_without_direction_is_annotation_only() -> None:
    verdict = resolve_actionability(
        "a", "b", "same_slot_different_value", evidence("a"), evidence("b"),
        relation_edge_id="edge-1",
        ordering_policy=POLICY,
    )
    assert verdict.mode is ActionMode.ANNOTATE_ONLY
    assert verdict.target_item_id is None


def test_uncertain_relation_never_authorizes_action() -> None:
    verdict = resolve_actionability(
        "a", "b", "uncertain", evidence("a", 1), evidence("b", 2),
        relation_edge_id="edge-1",
        ordering_policy=POLICY,
    )
    assert verdict.mode is ActionMode.ABSTAIN
    assert verdict.target_item_id is None


def test_evidence_for_different_items_fails_closed() -> None:
    verdict = resolve_actionability(
        "a", "b", "same_slot_different_value", evidence("x", 1), evidence("y", 2),
        relation_edge_id="edge-1",
        ordering_policy=POLICY,
    )
    assert verdict.mode is ActionMode.ABSTAIN
    assert verdict.target_item_id is None
