from datetime import datetime, timezone

from cmd_audit.counterfactual.item_ordering import (
    EvidenceReliability,
    OrderingEvidence,
    OrderingPolicy,
    OrderingRelation,
    compare_ordering_sources,
    resolve_ordering,
)


def evidence(item_id: str, **kwargs: object) -> OrderingEvidence:
    deployment_visible = bool(kwargs.pop("deployment_visible", True))
    reliability = kwargs.pop("reliability", EvidenceReliability.TRUSTED)
    if kwargs.get("observed_at") is not None:
        kwargs.setdefault("observed_at_domain", "deployment-clock-v1")
    if kwargs.get("event_sequence") is not None:
        kwargs.setdefault("event_stream_id", "stream-v1")
    if kwargs.get("source_priority") is not None:
        kwargs.setdefault("source_priority_domain", "authority-scale-v1")
    return OrderingEvidence(
        item_id=item_id,
        provenance="runtime-sidecar",
        audit_version="ordering-audit-v1",
        deployment_visible=deployment_visible,
        reliability=reliability,
        **kwargs,
    )


POLICY = OrderingPolicy(
    policy_version="ordering-policy-test-v1",
    accepted_sources=("observed_at", "event_sequence", "source_priority"),
    source_semantics=(
        ("observed_at", "chronology_lower_target"),
        ("event_sequence", "chronology_lower_target"),
        ("source_priority", "higher_wins"),
    ),
)


def test_visible_trusted_observed_at_orders_items() -> None:
    result = resolve_ordering(
        evidence("old", observed_at=datetime(2024, 1, 1, tzinfo=timezone.utc)),
        evidence("new", observed_at=datetime(2024, 2, 1, tzinfo=timezone.utc)),
        policy=POLICY,
    )
    assert result.relation is OrderingRelation.LEFT_TARGET_RIGHT_SURVIVES
    assert result.agreeing_sources == ("observed_at",)


def test_rank_store_and_text_are_not_ordering_inputs() -> None:
    result = resolve_ordering(
        evidence("a"),
        evidence("b"),
        policy=POLICY,
    )
    assert result.relation is OrderingRelation.UNKNOWN
    assert result.agreeing_sources == ()


def test_hidden_or_untrusted_sidecar_fails_closed() -> None:
    result = resolve_ordering(
        evidence("a", event_sequence=1, deployment_visible=False),
        evidence("b", event_sequence=2),
        policy=POLICY,
    )
    assert result.relation is OrderingRelation.UNKNOWN


def test_conflicting_trusted_signals_are_unknown() -> None:
    result = resolve_ordering(
        evidence("a", observed_at=datetime(2024, 1, 1, tzinfo=timezone.utc), event_sequence=2),
        evidence("b", observed_at=datetime(2024, 2, 1, tzinfo=timezone.utc), event_sequence=1),
        policy=POLICY,
    )
    assert result.relation is OrderingRelation.CONFLICTING
    assert result.conflicting_sources == ("observed_at", "event_sequence")


def test_source_comparisons_preserve_policy_order_domains_and_raw_values() -> None:
    left = evidence("a", event_sequence=1, source_priority=10)
    right = evidence("b", event_sequence=2, source_priority=5)
    comparisons = compare_ordering_sources(left, right, policy=POLICY)
    assert tuple(row.source for row in comparisons) == POLICY.accepted_sources
    event = comparisons[1]
    assert (event.comparable_domain, event.left_value, event.right_value) == (
        "stream-v1", 1, 2
    )
    assert event.outcome == "left_target_right_survives"


def test_sidecars_are_untrusted_and_hidden_by_default() -> None:
    raw_left = OrderingEvidence(item_id="a", event_sequence=1)
    raw_right = OrderingEvidence(item_id="b", event_sequence=2)
    assert not raw_left.usable
    assert resolve_ordering(raw_left, raw_right, policy=POLICY).relation is OrderingRelation.UNKNOWN


def test_unregistered_source_is_ignored_even_when_present() -> None:
    observed_only = OrderingPolicy(
        policy_version="observed-only-v1",
        accepted_sources=("observed_at",),
        source_semantics=(("observed_at", "chronology_lower_target"),),
    )
    result = resolve_ordering(
        evidence("a", event_sequence=1),
        evidence("b", event_sequence=2),
        policy=observed_only,
    )
    assert result.relation is OrderingRelation.UNKNOWN


def test_source_priority_is_explicit_higher_wins_not_a_chronology_claim() -> None:
    priority_only = OrderingPolicy(
        policy_version="priority-higher-wins-v1",
        accepted_sources=("source_priority",),
        source_semantics=(("source_priority", "higher_wins"),),
    )
    result = resolve_ordering(
        evidence("lower-authority", source_priority=1),
        evidence("higher-authority", source_priority=10),
        policy=priority_only,
    )
    assert result.relation is OrderingRelation.LEFT_TARGET_RIGHT_SURVIVES
    assert result.agreeing_sources == ("source_priority",)


def test_incomparable_clock_domains_fail_closed() -> None:
    observed_only = OrderingPolicy(
        policy_version="observed-only-v1",
        accepted_sources=("observed_at",),
        source_semantics=(("observed_at", "chronology_lower_target"),),
    )
    result = resolve_ordering(
        evidence(
            "a",
            observed_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            observed_at_domain="clock-a",
        ),
        evidence(
            "b",
            observed_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
            observed_at_domain="clock-b",
        ),
        policy=observed_only,
    )
    assert result.relation is OrderingRelation.UNKNOWN
    assert result.reason_code == "incomparable_observed_at_domain"
