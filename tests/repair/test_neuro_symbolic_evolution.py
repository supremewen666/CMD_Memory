from datetime import datetime, timezone

import pytest

from cmd_audit.counterfactual.actionability import resolve_actionability
from cmd_audit.counterfactual.item_ordering import (
    EvidenceReliability,
    OrderingEvidence,
    OrderingPolicy,
)
from cmd_audit.counterfactual.relation_graph import (
    FrozenRelationEdge,
    FrozenRelationGraph,
    RelationMeasurementBinding,
)
from cmd_audit.counterfactual.successor_program_ir import program_to_mapping
from cmd_audit.eval.state_intent import RuntimeMemoryItem, RuntimeRepairCase
from cmd_audit.repair.evolution_repository import EvolutionRepository
from cmd_audit.repair.neuro_symbolic_evolution import NeuroSymbolicEvolutionEngine
from cmd_audit.repair.parametric_policy import (
    OutcomeObservation,
    PolicyContext,
    RepairIntent,
)
from cmd_audit.repair.repair_chain_governance import (
    ChainAttemptInput,
    RepairChainGovernor,
)


def _graph(case_id: str, *, directional: bool = True) -> FrozenRelationGraph:
    case = RuntimeRepairCase(
        case_id=case_id,
        family_id="runtime-only",
        query="where does Dana work",
        raw_events=(),
        token_budget=64,
        items=(
            RuntimeMemoryItem("old", "Dana works at Acme", (), "runtime", 1, True),
            RuntimeMemoryItem("new", "Dana works at Globex", (), "runtime", 0, True),
        ),
    )
    policy = OrderingPolicy(
        "ordering-v1",
        ("observed_at",),
        (("observed_at", "chronology_lower_target"),),
    )
    observed = (
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 2, tzinfo=timezone.utc),
    ) if directional else (None, None)
    evidence = tuple(
        OrderingEvidence(
            item_id,
            observed_at=value,
            observed_at_domain="utc" if value else None,
            provenance="runtime-sidecar",
            audit_version="audit-v1",
            deployment_visible=True,
            reliability=EvidenceReliability.TRUSTED,
        )
        for item_id, value in zip(("old", "new"), observed, strict=True)
    )
    measurement = RelationMeasurementBinding.build(
        left_text="Dana works at Acme",
        right_text="Dana works at Globex",
        relation="same_slot_different_value",
        slot="employer",
        abstained=False,
        prompt_sha256="c" * 64,
        parser_version="parser-v1",
        model_id="model-v1",
        model_config_hash="d" * 64,
        normalization_version="norm-v1",
        instrument_version="instrument-v1",
        instrument_manifest_sha256="a" * 64,
    )
    edge_id = FrozenRelationEdge.relation_edge_id(
        pair_id=f"pair-{case_id}",
        case_id=case_id,
        left_item_id="old",
        right_item_id="new",
    )
    actionability = resolve_actionability(
        "old",
        "new",
        "same_slot_different_value",
        evidence[0],
        evidence[1],
        relation_edge_id=edge_id,
        ordering_policy=policy,
    )
    edge = FrozenRelationEdge.build(
        pair_id=f"pair-{case_id}",
        case_id=case_id,
        left_item_id="old",
        right_item_id="new",
        relation="same_slot_different_value",
        measurement=measurement,
        left_evidence=evidence[0],
        right_evidence=evidence[1],
        ordering_policy=policy,
        actionability=actionability,
    )
    return FrozenRelationGraph.build(
        case=case,
        item_ids=("old", "new"),
        protocol_manifest_sha256="f" * 64,
        instrument_manifest_sha256="a" * 64,
        cache_manifest_sha256="b" * 64,
        edges=(edge,),
    )


def _context(graph: FrozenRelationGraph, event_index: int) -> PolicyContext:
    return PolicyContext(
        graph.case_id,
        event_index,
        graph.graph_sha256,
        "tier2-item-repair",
        "mem0",
        "supersession",
        "trusted-later",
        {"trusted_order": 1.0, "relation_confidence": 1.0},
    )


def _intent(graph: FrozenRelationGraph) -> RepairIntent:
    return RepairIntent.build(
        strategy_id="prefer-trusted-later-then-verify@v1",
        relation_edge_id=graph.edges[0].edge_id,
        target_item_id="old",
        effect="demote",
        proposer_id="recorded-proposer-v1",
        proposer_model_hash="e" * 64,
        evidence_ids=(graph.edges[0].edge_sha256,),
    )


def _positive_outcome(selection, *, event: int, family: str) -> OutcomeObservation:
    return OutcomeObservation(
        selection.decision.selection_id,
        selection.decision.case_id,
        event,
        family,
        selection.decision.selected_intent_id,
        1.0,
        0.01,
        1,
        True,
        False,
    )


def test_engine_sediments_species_promotes_niche_and_restarts(tmp_path) -> None:
    path = tmp_path / "evolution.sqlite"
    repository = EvolutionRepository(path)
    engine = NeuroSymbolicEvolutionEngine(
        repository,
        min_species_later_support=2,
        min_species_families=2,
    )

    transitions = []
    for offset, (case_id, family_id) in enumerate(
        (("producer", "f0"), ("later-a", "f1"), ("later-b", "f2"))
    ):
        graph = _graph(case_id)
        selection = engine.select(
            _context(graph, 1 + offset * 2),
            graph=graph,
            intents=(_intent(graph),),
        )
        predicate = program_to_mapping(selection.compiled_programs[0][1])["predicate"]
        assert predicate["relation_edge_id"] == graph.edges[0].edge_id
        update = engine.record_outcomes(
            selection.decision,
            (_positive_outcome(selection, event=2 + offset * 2, family=family_id),),
        )
        transitions.append(update.species_transitions[-1].to_state)

    assert transitions == ["candidate", "probation", "stable"]
    assert len(repository.rows("outcome")) == 3
    assert len(repository.active_species()) == 1
    stable_snapshot = engine.policy.snapshot.snapshot_sha256
    repository.close()

    reopened = EvolutionRepository(path)
    restored = NeuroSymbolicEvolutionEngine(reopened)
    assert restored.policy.snapshot.snapshot_sha256 == stable_snapshot
    graph = _graph("future")
    decision = restored.select(
        _context(graph, 7), graph=graph, intents=(_intent(graph),)
    ).decision
    assert len(decision.niche_path) == 4
    assert decision.reason == "selected"


def test_engine_rejects_unsafe_intent_before_selection_is_persisted() -> None:
    repository = EvolutionRepository()
    engine = NeuroSymbolicEvolutionEngine(repository)
    graph = _graph("unknown-direction", directional=False)

    with pytest.raises(ValueError, match="cannot compile destructively"):
        engine.select(_context(graph, 1), graph=graph, intents=(_intent(graph),))

    assert repository.rows("selection") == ()


def test_engine_persists_chain_attempt_and_governance_decision() -> None:
    repository = EvolutionRepository()
    engine = NeuroSymbolicEvolutionEngine(repository)
    decision = engine.record_chain_attempt(
        ChainAttemptInput(
            case_id="chain-case",
            family_id="f1",
            event_index=1,
            first_strategy_id="strategy-a@v1",
            second_strategy_id="strategy-b@v1",
            first_utility=0.2,
            second_utility=0.1,
            chain_utility=0.5,
            materialized_intermediate=True,
            changed_item_count=1,
            locality_cost=0.01,
            valid=True,
            rolled_back=False,
            typed_conflict=False,
            anchor_regression=False,
        )
    )

    assert decision.lifecycle == "blocked"
    assert len(repository.rows("chain_attempt")) == 1
    assert len(repository.rows("chain_decision")) == 1


def test_chain_governance_rehydrates_attempts_and_continues_after_restart(
    tmp_path,
) -> None:
    def governor() -> RepairChainGovernor:
        value = RepairChainGovernor(min_support=2, min_families=2)
        value.admit_strategy("strategy-a@v1")
        value.admit_strategy("strategy-b@v1")
        return value

    def attempt(case: str, family: str, event: int) -> ChainAttemptInput:
        return ChainAttemptInput(
            case_id=case,
            family_id=family,
            event_index=event,
            first_strategy_id="strategy-a@v1",
            second_strategy_id="strategy-b@v1",
            first_utility=0.2,
            second_utility=0.1,
            chain_utility=0.5,
            materialized_intermediate=True,
            changed_item_count=1,
            locality_cost=0.01,
            valid=True,
            rolled_back=False,
            typed_conflict=False,
            anchor_regression=False,
        )

    path = tmp_path / "chain.sqlite"
    first_repository = EvolutionRepository(path)
    first = NeuroSymbolicEvolutionEngine(
        first_repository, chain_governor=governor()
    )
    first.record_chain_attempt(attempt("producer", "f0", 1))
    first.record_chain_attempt(attempt("later-a", "f1", 2))
    first_repository.close()

    reopened_repository = EvolutionRepository(path)
    reopened = NeuroSymbolicEvolutionEngine(
        reopened_repository, chain_governor=governor()
    )
    decision = reopened.record_chain_attempt(attempt("later-b", "f2", 3))

    assert decision.lifecycle == "stable"
    assert decision.support_count == 2
    assert len(reopened_repository.rows("chain_attempt")) == 3
