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
from experiments.build_relation_graph import build_graphs

PROTOCOL_HASH = "f" * 64
VALIDATION_HASH = "e" * 64
INSTRUMENT_HASH = "a" * 64


def measurement() -> RelationMeasurementBinding:
    return RelationMeasurementBinding.build(
        left_text="Dana works at Acme",
        right_text="Dana works at Globex",
        relation="same_slot_different_value",
        slot="employer",
        abstained=False,
        prompt_sha256="b" * 64,
        parser_version="parser-v1",
        model_id="model-v1",
        model_config_hash="c" * 64,
        normalization_version="normalization-v1",
        instrument_version="instrument-v1",
        instrument_manifest_sha256=INSTRUMENT_HASH,
    )


def evidence(item_id: str, day: int) -> OrderingEvidence:
    return OrderingEvidence(
        item_id=item_id,
        observed_at=datetime(2024, 1, day, tzinfo=timezone.utc),
        observed_at_domain="utc-v1",
        provenance="sidecar",
        audit_version="audit-v1",
        deployment_visible=True,
        reliability=EvidenceReliability.TRUSTED,
    )


POLICY = OrderingPolicy(
    policy_version="policy-v1",
    accepted_sources=("observed_at",),
    source_semantics=(("observed_at", "chronology_lower_target"),),
)


def edge() -> FrozenRelationEdge:
    left, right = evidence("old", 1), evidence("new", 2)
    edge_id = FrozenRelationEdge.relation_edge_id(
        pair_id="pair-1", case_id="case-1", left_item_id="old", right_item_id="new"
    )
    verdict = resolve_actionability(
        "old", "new", "same_slot_different_value", left, right,
        relation_edge_id=edge_id, ordering_policy=POLICY,
    )
    return FrozenRelationEdge.build(
        pair_id="pair-1", case_id="case-1", left_item_id="old", right_item_id="new",
        relation="same_slot_different_value", measurement=measurement(),
        left_evidence=left, right_evidence=right, ordering_policy=POLICY,
        actionability=verdict,
    )


def runtime_mapping() -> dict:
    return {
        "case_id": "case-1",
        "family_id": "family-1",
        "query": "where does dana work",
        "token_budget": 100,
        "runtime_surface": "route-a-runtime-v1",
        "items": [
            {"item_id": "old", "text": "Dana works at Acme", "source_event_ids": [], "store": "default", "rank": 0, "retrieved": True},
            {"item_id": "new", "text": "Dana works at Globex", "source_event_ids": [], "store": "default", "rank": 1, "retrieved": True},
        ],
        "raw_events": [],
    }


def cache_record() -> dict:
    value = measurement().as_mapping()
    return {
        key: value[key]
        for key in (
            "cache_key", "canonical_left", "canonical_right", "prompt_sha256",
            "parser_version", "model_id", "model_config_hash",
            "normalization_version", "instrument_version",
        )
    } | {
        "verdict": {
            key: value[key]
            for key in ("relation", "slot", "abstained", "prompt_sha256", "parser_version", "model_id")
        }
    }


def payload() -> dict:
    return {
        "schema_version": "graph-build-input-v1",
        "protocol_id": "route-a-successor-semantic-actionability-v3",
        "protocol_manifest_sha256": PROTOCOL_HASH,
        "protocol_validation_file_sha256": VALIDATION_HASH,
        "instrument_manifest_sha256": INSTRUMENT_HASH,
        "cache_manifest_sha256": "d" * 64,
        "llm_calls": 0,
        "cache_miss_policy": "refuse",
        "cache_records": [cache_record()],
        "cases": [{"runtime_case": runtime_mapping(), "item_ids": ["old", "new"], "edges": [edge().as_mapping()]}],
    }


def build(value: dict) -> list[dict[str, object]]:
    return build_graphs(
        value,
        validated_protocol_manifest_sha256=PROTOCOL_HASH,
        protocol_validation_file_sha256=VALIDATION_HASH,
    )


def test_build_graph_requires_replayable_cache_row_and_round_trips() -> None:
    graphs = build(payload())
    assert len(graphs) == 1
    assert FrozenRelationGraph.from_mapping(graphs[0]).protocol_manifest_sha256 == PROTOCOL_HASH


def test_build_graph_refuses_missing_cache_record_and_duplicate_case() -> None:
    missing = payload()
    missing["cache_records"] = []
    with pytest.raises(ValueError, match="absent cache"):
        build(missing)

    duplicate = payload()
    duplicate["cases"].append(duplicate["cases"][0])
    with pytest.raises(ValueError, match="duplicate graph case"):
        build(duplicate)


def test_build_graph_refuses_live_calls_or_open_schema_extension() -> None:
    live = payload()
    live["llm_calls"] = 1
    with pytest.raises(ValueError, match="zero-live-call"):
        build(live)

    extended = payload()
    extended["runtime_fallback"] = True
    with pytest.raises(ValueError, match="exactly"):
        build(extended)
