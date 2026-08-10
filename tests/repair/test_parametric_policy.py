from datetime import datetime, timezone

import pytest

from cmd_audit.counterfactual.actionability import resolve_actionability
from cmd_audit.counterfactual.item_ordering import EvidenceReliability, OrderingEvidence, OrderingPolicy
from cmd_audit.counterfactual.relation_graph import FrozenRelationEdge, FrozenRelationGraph, RelationMeasurementBinding
from cmd_audit.counterfactual.successor_program_ir import PredicateKind, program_to_mapping
from cmd_audit.eval.state_intent import RuntimeMemoryItem, RuntimeRepairCase
from cmd_audit.repair.parametric_policy import (
    NicheStatus,
    OnlineRepairPolicy,
    OutcomeObservation,
    PolicyContext,
    PolicySnapshot,
    RepairIntent,
    compile_intent,
    niche_path,
)


def _graph() -> FrozenRelationGraph:
    case = RuntimeRepairCase(
        case_id="runtime-1", family_id="heldout-family", query="where",
        raw_events=(), token_budget=32,
        items=(
            RuntimeMemoryItem("old", "Dana works at Acme", (), "untrusted", 1, True),
            RuntimeMemoryItem("new", "Dana works at Globex", (), "verified", 0, True),
        ),
    )
    policy = OrderingPolicy("ordering-v1", ("observed_at",), (("observed_at", "chronology_lower_target"),))
    left = OrderingEvidence(
        "old", observed_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        observed_at_domain="utc", provenance="sidecar", audit_version="audit-v1",
        deployment_visible=True, reliability=EvidenceReliability.TRUSTED,
    )
    right = OrderingEvidence(
        "new", observed_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        observed_at_domain="utc", provenance="sidecar", audit_version="audit-v1",
        deployment_visible=True, reliability=EvidenceReliability.TRUSTED,
    )
    measurement = RelationMeasurementBinding.build(
        left_text="Dana works at Acme", right_text="Dana works at Globex",
        relation="same_slot_different_value", slot="employer", abstained=False,
        prompt_sha256="c" * 64, parser_version="parser-v1", model_id="model-v1",
        model_config_hash="d" * 64, normalization_version="norm-v1",
        instrument_version="instrument-v1", instrument_manifest_sha256="a" * 64,
    )
    edge_id = FrozenRelationEdge.relation_edge_id(pair_id="pair", case_id=case.case_id, left_item_id="old", right_item_id="new")
    verdict = resolve_actionability("old", "new", "same_slot_different_value", left, right, relation_edge_id=edge_id, ordering_policy=policy)
    edge = FrozenRelationEdge.build(
        pair_id="pair", case_id=case.case_id, left_item_id="old", right_item_id="new",
        relation="same_slot_different_value", measurement=measurement, left_evidence=left,
        right_evidence=right, ordering_policy=policy, actionability=verdict,
    )
    return FrozenRelationGraph.build(
        case=case, item_ids=("old", "new"), protocol_manifest_sha256="f" * 64,
        instrument_manifest_sha256="a" * 64, cache_manifest_sha256="b" * 64, edges=(edge,),
    )


def _context(graph: FrozenRelationGraph, event: int = 1) -> PolicyContext:
    return PolicyContext("runtime-1", event, graph.graph_sha256, "mem0", "personal", "supersession", "later-trusted", {"trusted_later": 1.0})


def _intent(graph: FrozenRelationGraph, strategy: str = "prefer_trusted_later_fact_v1") -> RepairIntent:
    return RepairIntent.build(
        strategy_id=strategy, relation_edge_id=graph.edges[0].edge_id, target_item_id="old",
        effect="demote", proposer_id="proposer-v1", proposer_model_hash="e" * 64,
        evidence_ids=("edge-evidence",),
    )


def test_complete_intent_compiles_to_exact_graph_bound_ir() -> None:
    graph, intent = _graph(), None
    intent = _intent(graph)
    program = compile_intent(intent, graph=graph)
    predicate = program_to_mapping(program)["predicate"]
    assert predicate == {"kind": PredicateKind.SUPERSEDED_ITEM.value, "relation_edge_id": graph.edges[0].edge_id, "target_item_id": "old"}


def test_strategy_and_feature_leakage_are_rejected() -> None:
    graph = _graph()
    with pytest.raises(ValueError, match="leakage"):
        _intent(graph, "case_runtime_1_strategy")
    graph_bound = _intent(graph, f"semantic_motif_{graph.case_id}")
    with pytest.raises(ValueError, match="frozen graph identifier leakage"):
        compile_intent(graph_bound, graph=graph)
    with pytest.raises(ValueError, match="forbidden"):
        PolicyContext("runtime-1", 1, graph.graph_sha256, "mem0", "d", "s", "sig", {"gold_label": 1.0})
    with pytest.raises(ValueError, match="positive"):
        _context(graph, event=0)


def test_selection_precedes_post_outcome_learning_and_snapshot_is_tamper_evident() -> None:
    graph = _graph()
    policy = OnlineRepairPolicy(pairwise_margin=0.2)
    decision = policy.select(_context(graph), (_intent(graph),))
    assert decision.context_sha256 == _context(graph).content_hash()
    assert type(decision).from_mapping(decision.to_mapping()) == decision
    before = policy.snapshot
    outcome = OutcomeObservation(decision.selection_id, "runtime-1", 2, "eval-only", decision.ranked_intent_ids[0], 1.0, 0.0, 1, True, False)
    after = policy.observe(decision, (outcome,), observed_after_event_index=2)
    assert after.snapshot_sha256 != before.snapshot_sha256
    assert after.parent_snapshot_sha256 == before.snapshot_sha256
    assert PolicySnapshot.from_mapping(after.to_mapping()) == after
    restored = OnlineRepairPolicy.from_snapshot(after)
    with pytest.raises(ValueError, match="strictly increasing"):
        restored.select(_context(graph, event=2), (_intent(graph),))
    replay = restored.select(_context(graph, event=3), (_intent(graph),))
    assert replay.scores == policy.select(_context(graph, event=3), (_intent(graph),)).scores
    next_outcome = OutcomeObservation(replay.selection_id, "runtime-1", 4, "eval-only", replay.ranked_intent_ids[0], 0.5, 0.0, 0, True, False)
    assert restored.observe(replay, (next_outcome,), observed_after_event_index=4).parent_snapshot_sha256 == after.snapshot_sha256
    tampered = after.to_mapping()
    tampered["effective_after_event_index"] = 99
    with pytest.raises(ValueError, match="hash mismatch"):
        PolicySnapshot.from_mapping(tampered)


def test_stable_deeper_niche_wins_but_cold_layer_backs_off() -> None:
    graph = _graph()
    policy = OnlineRepairPolicy()
    context = _context(graph)
    assert policy.resolved_niche_path(context) == ("global",)
    paths = niche_path(context)
    policy.set_niche_status(paths[1], NicheStatus.STABLE)
    policy.set_niche_status(paths[2], NicheStatus.STABLE)
    assert policy.resolved_niche_path(context) == paths[:3]
    policy.set_niche_status(paths[3], NicheStatus.PROBATION)
    assert policy.resolved_niche_path(context) == paths[:3]
