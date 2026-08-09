from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

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
from cmd_audit.eval.state_intent import RuntimeMemoryItem, RuntimeRepairCase
from cmd_audit.repair.parametric_policy import PolicyContext, RepairIntent
from experiments.v4_live_materialization import V4LiveMaterializer
from experiments.v4_prequential_runner import (
    V4_ARMS,
    V4CandidateOutcome,
    V4PrequentialCase,
    V4PrequentialRunner,
    main,
)


def _graph(case_id: str) -> FrozenRelationGraph:
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
    )
    evidence = tuple(
        OrderingEvidence(
            item_id,
            observed_at=value,
            observed_at_domain="utc",
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


def _case(index: int, *, probe_set: str, family: str) -> V4PrequentialCase:
    case_id = f"case-{index}"
    graph = _graph(case_id)
    intent = RepairIntent.build(
        strategy_id="prefer-trusted-later@v1",
        relation_edge_id=graph.edges[0].edge_id,
        target_item_id="old",
        effect="demote",
        proposer_id="recorded-proposer-v1",
        proposer_model_hash="e" * 64,
        evidence_ids=(graph.edges[0].edge_sha256,),
    )
    context = PolicyContext(
        case_id,
        index * 2 + 1,
        graph.graph_sha256,
        "tier2-item-repair",
        "mem0",
        "supersession",
        "trusted-later",
        {"trusted_order": 1.0},
    )
    return V4PrequentialCase(
        case_id=case_id,
        family_id=family,
        probe_set=probe_set,
        context=context,
        graph=graph,
        intents=(intent,),
        legacy_intent_id=intent.intent_id,
        candidate_outcomes=(
            V4CandidateOutcome(
                intent.intent_id,
                recovery_gain=1.0,
                locality_cost=0.01,
                changed_item_count=1,
                valid=True,
                rolled_back=False,
            ),
        ),
        chain_attempts=(),
    )


def _cases() -> tuple[V4PrequentialCase, ...]:
    return (
        _case(0, probe_set="represented", family="f0"),
        _case(1, probe_set="represented", family="f1"),
        _case(2, probe_set="represented", family="f2"),
        _case(3, probe_set="unseen", family="u0"),
        _case(4, probe_set="unseen", family="u1"),
    )


def test_runner_is_arm_paired_test_then_update_and_sediments(tmp_path: Path) -> None:
    streamed: list[dict[str, object]] = []
    result = V4PrequentialRunner(
        _cases(),
        output_dir=tmp_path,
        candidate_budget=1,
        bootstrap_samples=100,
        on_arm_outcome=streamed.append,
    ).run()

    assert len(result.outcomes) == len(_cases()) * len(V4_ARMS)
    assert tuple(row for row in streamed) == tuple(
        outcome.to_mapping() for outcome in result.outcomes
    )
    for case in _cases():
        rows = [row for row in result.outcomes if row.case_id == case.case_id]
        assert tuple(row.arm_id for row in rows) == V4_ARMS
        assert all(row.candidate_count == 1 for row in rows if row.arm_id != "identity")
        assert rows[0].selected_intent_id is None
    stable = [
        transition
        for row in result.outcomes
        if row.arm_id == "full_v4"
        for transition in row.species_transitions
        if transition["to_state"] == "stable"
    ]
    assert stable
    assert result.report["selected_action_feedback_only"] is True
    assert result.report["gate"]["primary_baseline"] == "global_policy"
    assert result.report["gate"]["passed"] is False


def test_unseen_families_are_scored_without_policy_or_species_updates(
    tmp_path: Path,
) -> None:
    result = V4PrequentialRunner(
        _cases(),
        output_dir=tmp_path,
        candidate_budget=1,
        bootstrap_samples=100,
    ).run()

    unseen = [row for row in result.outcomes if row.probe_set == "unseen"]
    assert unseen
    for row in unseen:
        if row.arm_id in {"global_policy", "hierarchical_no_chain", "full_v4"}:
            assert row.policy_snapshot_after == row.policy_snapshot_before
            assert row.update_effective_after_event_index is None
            assert row.species_transitions == ()
            assert row.chain_decisions == ()


def test_closed_case_schema_rejects_budget_and_outcome_mismatch(tmp_path: Path) -> None:
    case = _cases()[0]
    with pytest.raises(ValueError, match="candidate budget"):
        V4PrequentialRunner(
            (case,),
            output_dir=tmp_path,
            candidate_budget=2,
            bootstrap_samples=100,
        )
    broken = case.to_mapping()
    broken["candidate_outcomes"] = []
    with pytest.raises(ValueError, match="exactly cover"):
        V4PrequentialCase.from_mapping(broken)


def test_cli_streams_case_results_and_progress_jsonl(tmp_path: Path) -> None:
    source = tmp_path / "cases.jsonl"
    source.write_text(
        "".join(json.dumps(row.to_mapping(), sort_keys=True) + "\n" for row in _cases()),
        encoding="utf-8",
    )
    output = tmp_path / "run"

    assert main(
        (
            "--cases",
            str(source),
            "--output-dir",
            str(output),
            "--candidate-budget",
            "1",
            "--bootstrap-samples",
            "100",
        )
    ) == 0

    rows = [
        json.loads(line)
        for line in (output / "arm_outcomes.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    progress = [
        json.loads(line)
        for line in (output / "progress.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == len(_cases()) * len(V4_ARMS)
    assert [row["event"] for row in progress] == [
        "started",
        *("case_completed" for _ in _cases()),
        "completed",
    ]
    assert json.loads((output / "report.json").read_text(encoding="utf-8"))[
        "case_count"
    ] == len(_cases())


def test_live_materializer_executes_typed_intents_before_shadow_scoring() -> None:
    case = _case(0, probe_set="represented", family="f0")
    second_intent = RepairIntent.build(
        strategy_id="suppress-trusted-earlier@v1",
        relation_edge_id=case.graph.edges[0].edge_id,
        target_item_id="old",
        effect="suppress",
        proposer_id="recorded-proposer-v1",
        proposer_model_hash="e" * 64,
        evidence_ids=(case.graph.edges[0].edge_sha256,),
    )
    live_intents = (*case.intents, second_intent)
    runtime_case = RuntimeRepairCase(
        case_id=case.case_id,
        family_id="runtime-only",
        query="where does Dana work",
        raw_events=(),
        token_budget=64,
        items=(
            RuntimeMemoryItem("old", "Dana works at Acme", (), "runtime", 1, True),
            RuntimeMemoryItem("new", "Dana works at Globex", (), "runtime", 0, True),
        ),
    )
    runtime_mapping = {
        "case_id": runtime_case.case_id,
        "family_id": runtime_case.family_id,
        "query": runtime_case.query,
        "token_budget": runtime_case.token_budget,
        "runtime_surface": runtime_case.runtime_surface,
        "items": [
            {
                "item_id": row.item_id,
                "text": row.text,
                "source_event_ids": list(row.source_event_ids),
                "store": row.store,
                "rank": row.rank,
                "retrieved": row.retrieved,
            }
            for row in runtime_case.items
        ],
        "raw_events": [],
    }
    probe_case = {
        "case_id": case.case_id,
        "query": runtime_case.query,
        "raw_events": [{"event_id": "e1", "text": "Dana now works at Globex"}],
        "extracted_memory": [
            {
                "memory_id": "old",
                "text": "Dana works at Acme",
                "store": "runtime",
            },
            {
                "memory_id": "new",
                "text": "Dana works at Globex",
                "store": "runtime",
            },
        ],
        "gold_evidence": [
            {
                "evidence_id": "gold-1",
                "text": "Dana works at Globex",
                "source_memory_id": "new",
            }
        ],
        "gold_answer": "Globex",
        "baseline_outputs": [
            {
                "baseline_name": "vector_memory",
                "answer": "Acme",
                "retrieved_memory_ids": ["new", "old"],
                "answer_score": 0.0,
                "evidence_score": 0.0,
                "injected_context": "Dana works at Acme\nDana works at Globex",
            }
        ],
        "perturbation_label": "retrieval_error",
    }
    source = {
        "schema_version": "cmd-v4-live-materialization-input-v1",
        "case_id": case.case_id,
        "family_id": case.family_id,
        "probe_set": case.probe_set,
        "context": case.context.to_mapping(),
        "graph": case.graph.as_mapping(),
        "runtime_case": runtime_mapping,
        "intents": [row.to_mapping() for row in live_intents],
        "legacy_intent_id": case.legacy_intent_id,
        "chain_pairs": [[case.intents[0].intent_id, second_intent.intent_id]],
        "probe_case": probe_case,
    }

    class Answerer:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def generate(self, prompt, *, system=None):
            self.prompts.append(prompt if system is None else system + prompt)
            return "Globex"

    answerer = Answerer()
    result = V4LiveMaterializer(
        answer_client=answerer,
        answer_verifier=lambda _answer, _gold: 1.0,
    ).materialize(source, "gpu0")
    parsed = V4PrequentialCase.from_mapping(result)

    assert len(parsed.candidate_outcomes) == len(live_intents)
    assert parsed.candidate_outcomes[0].recovery_gain == 1.0
    assert parsed.candidate_outcomes[0].changed_item_count == 1
    assert len(parsed.chain_attempts) == 1
    assert parsed.chain_attempts[0].materialized_intermediate is True
    assert parsed.chain_attempts[0].first_intent_id == case.intents[0].intent_id
    assert len(answerer.prompts) == len(live_intents) + 1
    assert "Globex" not in parsed.context.features
