from datetime import datetime, timezone

from cmd_audit.counterfactual.actionability import resolve_actionability
from cmd_audit.counterfactual.item_ordering import (
    EvidenceReliability,
    OrderingEvidence,
    OrderingPolicy,
)
from cmd_audit.counterfactual.program_ir import ActionKind
from cmd_audit.counterfactual.repair_state import initial_state_from_runtime_case
from cmd_audit.counterfactual.successor_program_ir import Action, If, Predicate, PredicateKind
from cmd_audit.counterfactual.successor_state_executor import (
    FrozenRelationEdge,
    FrozenRelationGraph,
    execute_program,
)
from cmd_audit.counterfactual.relation_graph import RelationMeasurementBinding
from cmd_audit.eval.state_intent import RuntimeMemoryItem, RuntimeRepairCase


def runtime() -> RuntimeRepairCase:
    return RuntimeRepairCase(case_id="c", family_id="f", query="where does dana work", raw_events=(), token_budget=100, items=(
        RuntimeMemoryItem("old", "Dana works at Acme", (), "untrusted", 99, True),
        RuntimeMemoryItem("current", "Dana works at Globex", (), "verified", 0, True),
    ))


PROTOCOL_HASH = "f" * 64
INSTRUMENT_HASH = "a" * 64


def measurement() -> RelationMeasurementBinding:
    return RelationMeasurementBinding.build(
        left_text="Dana works at Acme",
        right_text="Dana works at Globex",
        relation="same_slot_different_value",
        slot="employer",
        abstained=False,
        prompt_sha256="c" * 64,
        parser_version="parser-v1",
        model_id="model-v1",
        model_config_hash="d" * 64,
        normalization_version="normalization-v1",
        instrument_version="instrument-v1",
        instrument_manifest_sha256=INSTRUMENT_HASH,
    )


def edge(*, old_day: int | None = 1, current_day: int | None = 2) -> FrozenRelationEdge:
    old = ordering("old", old_day)
    current = ordering("current", current_day)
    relation_edge_id = FrozenRelationEdge.relation_edge_id(
        pair_id="pair-1", case_id="c", left_item_id="old", right_item_id="current"
    )
    verdict = resolve_actionability(
        "old", "current", "same_slot_different_value", old, current,
        relation_edge_id=relation_edge_id,
        ordering_policy=POLICY,
    )
    return FrozenRelationEdge.build(
        pair_id="pair-1", case_id="c",
        left_item_id="old", right_item_id="current",
        relation="same_slot_different_value", measurement=measurement(),
        left_evidence=old, right_evidence=current, ordering_policy=POLICY,
        actionability=verdict,
    )


def graph() -> FrozenRelationGraph:
    return FrozenRelationGraph.build(
        case=runtime(),
        item_ids=("old", "current"),
        protocol_manifest_sha256=PROTOCOL_HASH,
        instrument_manifest_sha256=INSTRUMENT_HASH,
        cache_manifest_sha256="b" * 64,
        edges=(edge(),),
    )


POLICY = OrderingPolicy(
    policy_version="ordering-policy-test-v1",
    accepted_sources=("observed_at",),
    source_semantics=(("observed_at", "chronology_lower_target"),),
)


def ordering(item_id: str, day: int | None = None) -> OrderingEvidence:
    return OrderingEvidence(
        item_id=item_id,
        observed_at=(datetime(2024, 1, day, tzinfo=timezone.utc) if day else None),
        observed_at_domain=("utc" if day else None),
        provenance="runtime-sidecar",
        audit_version="ordering-audit-v1",
        deployment_visible=True,
        reliability=EvidenceReliability.TRUSTED,
    )


def test_superseded_demotion_hits_only_actionability_target_not_current() -> None:
    state = initial_state_from_runtime_case(runtime())
    frozen = graph()
    result = execute_program(If(Predicate(PredicateKind.SUPERSEDED_ITEM), Action(ActionKind.DEMOTE)), runtime(), state, graph=frozen, expected_graph_sha256=frozen.graph_sha256, expected_protocol_manifest_sha256=PROTOCOL_HASH)
    dispositions = {item.item_id: item.disposition for item in result.state.items}
    assert dispositions == {"old": "demoted", "current": "active"}


def test_exact_target_binding_rejects_another_edge_or_item() -> None:
    state = initial_state_from_runtime_case(runtime())
    frozen = graph()
    wrong_edge = If(
        Predicate(
            PredicateKind.SUPERSEDED_ITEM,
            relation_edge_id="0" * 64,
            target_item_id="old",
        ),
        Action(ActionKind.DEMOTE),
    )
    wrong_item = If(
        Predicate(
            PredicateKind.SUPERSEDED_ITEM,
            relation_edge_id=frozen.edges[0].edge_id,
            target_item_id="current",
        ),
        Action(ActionKind.DEMOTE),
    )

    for program in (wrong_edge, wrong_item):
        result = execute_program(
            program,
            runtime(),
            state,
            graph=frozen,
            expected_graph_sha256=frozen.graph_sha256,
            expected_protocol_manifest_sha256=PROTOCOL_HASH,
        )
        assert result.state.state_hash == state.state_hash


def test_divergent_annotation_marks_both_members_without_destructive_repair() -> None:
    state = initial_state_from_runtime_case(runtime())
    frozen = graph()
    result = execute_program(If(Predicate(PredicateKind.DIVERGENT_PAIR_MEMBER), Action(ActionKind.ANNOTATE_CONFLICT)), runtime(), state, graph=frozen, expected_graph_sha256=frozen.graph_sha256, expected_protocol_manifest_sha256=PROTOCOL_HASH)
    assert {item.item_id for item in result.state.items if item.disposition == "conflict"} == {"old", "current"}


def test_verify_records_refusal_without_changing_state() -> None:
    state = initial_state_from_runtime_case(runtime())
    frozen = graph()
    result = execute_program(
        If(Predicate(PredicateKind.DIVERGENT_PAIR_MEMBER), Action(ActionKind.VERIFY)),
        runtime(),
        state,
        graph=frozen,
        expected_graph_sha256=frozen.graph_sha256,
        expected_protocol_manifest_sha256=PROTOCOL_HASH,
    )
    assert result.abstained is True
    assert result.state.state_hash == state.state_hash


def test_unknown_direction_never_matches_superseded_leaf() -> None:
    frozen = FrozenRelationGraph.build(
        case=runtime(), item_ids=("old", "current"),
        protocol_manifest_sha256=PROTOCOL_HASH,
        instrument_manifest_sha256=INSTRUMENT_HASH,
        cache_manifest_sha256="b" * 64,
        edges=(edge(old_day=None, current_day=None),),
    )
    state = initial_state_from_runtime_case(runtime())
    result = execute_program(If(Predicate(PredicateKind.SUPERSEDED_ITEM), Action(ActionKind.DEMOTE)), runtime(), state, graph=frozen, expected_graph_sha256=frozen.graph_sha256, expected_protocol_manifest_sha256=PROTOCOL_HASH)
    assert result.state.state_hash == state.state_hash


def test_graph_is_bound_to_case_and_cannot_cross_case_ids() -> None:
    case = runtime()
    wrong_case = RuntimeRepairCase(
        case_id="other",
        family_id=case.family_id,
        query=case.query,
        raw_events=case.raw_events,
        token_budget=case.token_budget,
        items=case.items,
    )
    state = initial_state_from_runtime_case(wrong_case)
    import pytest

    frozen = graph()
    with pytest.raises(ValueError, match="case"):
        execute_program(
            If(Predicate(PredicateKind.SUPERSEDED_ITEM), Action(ActionKind.DEMOTE)),
            wrong_case,
            state,
            graph=frozen, expected_graph_sha256=frozen.graph_sha256,
            expected_protocol_manifest_sha256=PROTOCOL_HASH,
        )


def test_graph_hash_mismatch_fails_closed() -> None:
    import pytest

    state = initial_state_from_runtime_case(runtime())
    with pytest.raises(ValueError, match="registered graph hash"):
        execute_program(If(Predicate(PredicateKind.SUPERSEDED_ITEM), Action(ActionKind.DEMOTE)), runtime(), state, graph=graph(), expected_graph_sha256="0" * 64, expected_protocol_manifest_sha256=PROTOCOL_HASH)


def test_graph_rejects_item_set_mismatch_before_execution() -> None:
    import pytest

    state = initial_state_from_runtime_case(runtime())
    frozen = graph()
    altered = state.__class__(
        case_id=state.case_id, items=state.items[:-1], trace=state.trace,
        rendered_context=state.rendered_context, token_count=state.token_count,
        state_hash=state.state_hash,
    )
    with pytest.raises(ValueError, match="item-set"):
        execute_program(If(Predicate(PredicateKind.SUPERSEDED_ITEM), Action(ActionKind.DEMOTE)), runtime(), altered, graph=frozen, expected_graph_sha256=frozen.graph_sha256, expected_protocol_manifest_sha256=PROTOCOL_HASH)


def test_graph_rejects_edge_actionability_bound_to_another_relation() -> None:
    import pytest

    verdict = resolve_actionability(
        "old", "current", "same_slot_different_value", ordering("old", 1), ordering("current", 2),
        relation_edge_id="f" * 64, ordering_policy=POLICY,
    )
    with pytest.raises(ValueError, match="drift"):
        FrozenRelationEdge.build(
            pair_id="pair-1", case_id="c", left_item_id="old", right_item_id="current",
            relation="same_slot_different_value", measurement=measurement(),
            left_evidence=ordering("old", 1), right_evidence=ordering("current", 2),
            ordering_policy=POLICY, actionability=verdict,
        )


def test_graph_round_trip_replays_closed_edge_evidence() -> None:
    frozen = graph()
    assert FrozenRelationGraph.from_mapping(frozen.as_mapping()) == frozen


def test_protocol_manifest_hash_mismatch_fails_closed() -> None:
    import pytest

    frozen = graph()
    with pytest.raises(ValueError, match="protocol manifest"):
        execute_program(
            If(Predicate(PredicateKind.SUPERSEDED_ITEM), Action(ActionKind.DEMOTE)),
            runtime(), initial_state_from_runtime_case(runtime()), graph=frozen,
            expected_graph_sha256=frozen.graph_sha256,
            expected_protocol_manifest_sha256="0" * 64,
        )
