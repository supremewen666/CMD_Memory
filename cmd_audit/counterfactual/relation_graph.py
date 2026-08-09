"""Replayable, content-addressed successor-v3 relation graph.

Every edge carries the semantic cache measurement plus the independent ordering
evidence and policy needed to reproduce its actionability verdict.  Loading an
artifact replays that derivation; serialized targets are never trusted alone.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from cmd_audit.counterfactual.actionability import (
    ActionMode,
    ActionabilityVerdict,
    resolve_actionability,
)
from cmd_audit.counterfactual.item_ordering import (
    EvidenceReliability,
    OrderingEvidence,
    OrderingPolicy,
    OrderingRelation,
    OrderingVerdict,
    SourceComparison,
    compare_ordering_sources,
)
from cmd_audit.counterfactual.relation_cache import RelationCacheKey, canonical_text
from cmd_audit.eval.state_intent import RuntimeRepairCase, runtime_case_to_mapping

RELATION_GRAPH_SCHEMA_VERSION = "route-a-relation-graph-v1"
RELATION_MEASUREMENT_SCHEMA_VERSION = "route-a-relation-measurement-binding-v1"
SUCCESSOR_PROTOCOL_ID = "route-a-successor-semantic-actionability-v3"
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RELATIONS = frozenset({"same_slot_different_value", "unrelated", "uncertain"})

__all__ = [
    "RELATION_GRAPH_SCHEMA_VERSION",
    "RELATION_MEASUREMENT_SCHEMA_VERSION",
    "SUCCESSOR_PROTOCOL_ID",
    "RelationMeasurementBinding",
    "FrozenRelationEdge",
    "FrozenRelationGraph",
    "runtime_case_sha256",
    "item_set_sha256",
]


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _closed(value: object, keys: frozenset[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{name} must have exactly {sorted(keys)}")
    return value


def _hash(value: object, name: str) -> str:
    if not isinstance(value, str) or not _HEX_SHA256.fullmatch(value):
        raise ValueError(f"{name} must be lowercase SHA-256")
    return value


def runtime_case_sha256(case: RuntimeRepairCase) -> str:
    surface = runtime_case_to_mapping(case)
    surface["items"] = [
        {**item, "retrieved": source.retrieved}
        for item, source in zip(surface["items"], case.items, strict=True)
    ]
    return canonical_sha256(surface)


def item_set_sha256(item_ids: tuple[str, ...]) -> str:
    if (
        not item_ids
        or len(set(item_ids)) != len(item_ids)
        or any(not isinstance(item_id, str) or not item_id for item_id in item_ids)
    ):
        raise ValueError("item set must contain unique non-empty IDs")
    return canonical_sha256({"item_ids": sorted(item_ids)})


@dataclass(frozen=True)
class RelationMeasurementBinding:
    cache_key: str
    cache_record_sha256: str
    relation_verdict_sha256: str
    canonical_left: str
    canonical_right: str
    relation: str
    slot: str | None
    abstained: bool
    prompt_sha256: str
    parser_version: str
    model_id: str
    model_config_hash: str
    normalization_version: str
    instrument_version: str
    instrument_manifest_sha256: str
    schema_version: str = RELATION_MEASUREMENT_SCHEMA_VERSION

    @classmethod
    def build(
        cls,
        *,
        left_text: str,
        right_text: str,
        relation: object,
        slot: str | None,
        abstained: bool,
        prompt_sha256: str,
        parser_version: str,
        model_id: str,
        model_config_hash: str,
        normalization_version: str,
        instrument_version: str,
        instrument_manifest_sha256: str,
    ) -> "RelationMeasurementBinding":
        key = RelationCacheKey.build(
            left_text,
            right_text,
            prompt_sha256=prompt_sha256,
            parser_version=parser_version,
            model_id=model_id,
            model_config_hash=model_config_hash,
            normalization_version=normalization_version,
            instrument_version=instrument_version,
        )
        relation_value = str(getattr(relation, "value", relation))
        fields = {
            "cache_key": key.cache_key,
            "canonical_left": key.canonical_left,
            "canonical_right": key.canonical_right,
            "relation": relation_value,
            "slot": slot,
            "abstained": abstained,
            "prompt_sha256": prompt_sha256,
            "parser_version": parser_version,
            "model_id": model_id,
            "model_config_hash": model_config_hash,
            "normalization_version": normalization_version,
            "instrument_version": instrument_version,
            "instrument_manifest_sha256": instrument_manifest_sha256,
        }
        verdict_hash = canonical_sha256(cls._verdict_payload(**fields))
        return cls(
            cache_record_sha256=canonical_sha256(
                cls._cache_record_payload(**fields)
            ),
            relation_verdict_sha256=verdict_hash,
            **fields,
        )

    @staticmethod
    def _verdict_payload(**fields: object) -> dict[str, object]:
        return {
            "relation": fields["relation"],
            "slot": fields["slot"],
            "abstained": fields["abstained"],
            "prompt_sha256": fields["prompt_sha256"],
            "parser_version": fields["parser_version"],
            "model_id": fields["model_id"],
        }

    @staticmethod
    def _cache_record_payload(**fields: object) -> dict[str, object]:
        return {
            "cache_key": fields["cache_key"],
            "canonical_left": fields["canonical_left"],
            "canonical_right": fields["canonical_right"],
            "prompt_sha256": fields["prompt_sha256"],
            "parser_version": fields["parser_version"],
            "model_id": fields["model_id"],
            "model_config_hash": fields["model_config_hash"],
            "normalization_version": fields["normalization_version"],
            "instrument_version": fields["instrument_version"],
            "verdict": RelationMeasurementBinding._verdict_payload(**fields),
        }

    def __post_init__(self) -> None:
        if self.schema_version != RELATION_MEASUREMENT_SCHEMA_VERSION:
            raise ValueError("unsupported relation measurement schema")
        for name in (
            "cache_key",
            "cache_record_sha256",
            "relation_verdict_sha256",
            "prompt_sha256",
            "model_config_hash",
            "instrument_manifest_sha256",
        ):
            _hash(getattr(self, name), name)
        if self.relation not in _RELATIONS:
            raise ValueError("unregistered semantic relation")
        if not all(
            isinstance(value, str) and value
            for value in (
                self.canonical_left,
                self.canonical_right,
                self.parser_version,
                self.model_id,
                self.normalization_version,
                self.instrument_version,
            )
        ):
            raise ValueError("measurement versions/texts must be non-empty")
        if (self.relation == "uncertain") != self.abstained:
            raise ValueError("uncertain is the only abstaining measurement")
        if self.relation != "same_slot_different_value" and self.slot is not None:
            raise ValueError("only a positive relation may carry a slot")
        expected_key = RelationCacheKey.build(
            self.canonical_left,
            self.canonical_right,
            prompt_sha256=self.prompt_sha256,
            parser_version=self.parser_version,
            model_id=self.model_id,
            model_config_hash=self.model_config_hash,
            normalization_version=self.normalization_version,
            instrument_version=self.instrument_version,
        )
        if (
            self.cache_key != expected_key.cache_key
            or self.canonical_left != expected_key.canonical_left
            or self.canonical_right != expected_key.canonical_right
        ):
            raise ValueError("semantic cache key does not match measurement")
        fields = {
            key: getattr(self, key)
            for key in (
                "cache_key",
                "canonical_left",
                "canonical_right",
                "relation",
                "slot",
                "abstained",
                "prompt_sha256",
                "parser_version",
                "model_id",
                "model_config_hash",
                "normalization_version",
                "instrument_version",
                "instrument_manifest_sha256",
            )
        }
        if self.cache_record_sha256 != canonical_sha256(
            self._cache_record_payload(**fields)
        ):
            raise ValueError("semantic cache record hash mismatch")
        if self.relation_verdict_sha256 != canonical_sha256(
            self._verdict_payload(**fields)
        ):
            raise ValueError("semantic relation verdict hash mismatch")

    def matches_texts(self, left_text: str, right_text: str) -> bool:
        return sorted((canonical_text(left_text), canonical_text(right_text))) == [
            self.canonical_left,
            self.canonical_right,
        ]

    def as_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "cache_key": self.cache_key,
            "cache_record_sha256": self.cache_record_sha256,
            "relation_verdict_sha256": self.relation_verdict_sha256,
            "canonical_left": self.canonical_left,
            "canonical_right": self.canonical_right,
            "relation": self.relation,
            "slot": self.slot,
            "abstained": self.abstained,
            "prompt_sha256": self.prompt_sha256,
            "parser_version": self.parser_version,
            "model_id": self.model_id,
            "model_config_hash": self.model_config_hash,
            "normalization_version": self.normalization_version,
            "instrument_version": self.instrument_version,
            "instrument_manifest_sha256": self.instrument_manifest_sha256,
        }

    @classmethod
    def from_mapping(cls, value: object) -> "RelationMeasurementBinding":
        mapping = _closed(value, frozenset(cls.__dataclass_fields__), "measurement")
        return cls(**mapping)


def _evidence_mapping(value: OrderingEvidence) -> dict[str, object]:
    return {
        "item_id": value.item_id,
        "observed_at": value.observed_at.isoformat() if value.observed_at else None,
        "observed_at_domain": value.observed_at_domain,
        "event_sequence": value.event_sequence,
        "event_stream_id": value.event_stream_id,
        "source_priority": value.source_priority,
        "source_priority_domain": value.source_priority_domain,
        "provenance": value.provenance,
        "audit_version": value.audit_version,
        "deployment_visible": value.deployment_visible,
        "reliability": value.reliability.value,
    }


def _evidence_from_mapping(value: object) -> OrderingEvidence:
    keys = frozenset(_evidence_mapping(OrderingEvidence(item_id="schema")))
    mapping = _closed(value, keys, "ordering evidence")
    observed_at = mapping["observed_at"]
    if observed_at is not None:
        if not isinstance(observed_at, str):
            raise ValueError("observed_at must be RFC3339 string or null")
        observed_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    return OrderingEvidence(
        **{key: mapping[key] for key in keys - {"observed_at", "reliability"}},
        observed_at=observed_at,
        reliability=EvidenceReliability(mapping["reliability"]),
    )


def _policy_mapping(value: OrderingPolicy) -> dict[str, object]:
    return {
        "policy_version": value.policy_version,
        "accepted_sources": list(value.accepted_sources),
        "source_semantics": [list(item) for item in value.source_semantics],
        "require_agreement": value.require_agreement,
    }


def _policy_from_mapping(value: object) -> OrderingPolicy:
    mapping = _closed(
        value,
        frozenset(
            {"policy_version", "accepted_sources", "source_semantics", "require_agreement"}
        ),
        "ordering policy",
    )
    return OrderingPolicy(
        policy_version=mapping["policy_version"],
        accepted_sources=tuple(mapping["accepted_sources"]),
        source_semantics=tuple(tuple(item) for item in mapping["source_semantics"]),
        require_agreement=mapping["require_agreement"],
    )


def _comparison_value(value: object | None) -> object | None:
    return value.isoformat() if isinstance(value, datetime) else value


def _comparison_mapping(value: SourceComparison) -> dict[str, object]:
    return {
        "source": value.source,
        "semantic": value.semantic,
        "comparable_domain": value.comparable_domain,
        "left_value": _comparison_value(value.left_value),
        "right_value": _comparison_value(value.right_value),
        "comparable": value.comparable,
        "outcome": value.outcome,
        "reason_code": value.reason_code,
    }


def _comparison_from_mapping(value: object) -> SourceComparison:
    mapping = _closed(
        value,
        frozenset(
            {
                "source",
                "semantic",
                "comparable_domain",
                "left_value",
                "right_value",
                "comparable",
                "outcome",
                "reason_code",
            }
        ),
        "source comparison",
    )
    converted = dict(mapping)
    if mapping["source"] == "observed_at":
        for key in ("left_value", "right_value"):
            if converted[key] is not None:
                if not isinstance(converted[key], str):
                    raise ValueError("observed_at comparison value must be RFC3339")
                converted[key] = datetime.fromisoformat(
                    converted[key].replace("Z", "+00:00")
                )
    return SourceComparison(**converted)


def _ordering_mapping(value: OrderingVerdict) -> dict[str, object]:
    return {
        "relation": value.relation.value,
        "agreeing_sources": list(value.agreeing_sources),
        "conflicting_sources": list(value.conflicting_sources),
        "reason_code": value.reason_code,
        "policy_version": value.policy_version,
    }


def _actionability_mapping(value: ActionabilityVerdict) -> dict[str, object]:
    return {
        "relation_edge_id": value.relation_edge_id,
        "target_item_id": value.target_item_id,
        "survivor_item_id": value.survivor_item_id,
        "mode": value.mode.value,
        "reason_code": value.reason_code,
        "ordering": _ordering_mapping(value.ordering),
    }


def _actionability_from_mapping(value: object) -> ActionabilityVerdict:
    mapping = _closed(
        value,
        frozenset(
            {
                "relation_edge_id",
                "target_item_id",
                "survivor_item_id",
                "mode",
                "reason_code",
                "ordering",
            }
        ),
        "actionability",
    )
    ordering = _closed(
        mapping["ordering"],
        frozenset(
            {
                "relation",
                "agreeing_sources",
                "conflicting_sources",
                "reason_code",
                "policy_version",
            }
        ),
        "ordering verdict",
    )
    return ActionabilityVerdict(
        relation_edge_id=mapping["relation_edge_id"],
        target_item_id=mapping["target_item_id"],
        survivor_item_id=mapping["survivor_item_id"],
        mode=ActionMode(mapping["mode"]),
        reason_code=mapping["reason_code"],
        ordering=OrderingVerdict(
            relation=OrderingRelation(ordering["relation"]),
            agreeing_sources=tuple(ordering["agreeing_sources"]),
            reason_code=ordering["reason_code"],
            policy_version=ordering["policy_version"],
            conflicting_sources=tuple(ordering["conflicting_sources"]),
        ),
    )


@dataclass(frozen=True)
class FrozenRelationEdge:
    edge_id: str
    pair_id: str
    case_id: str
    left_item_id: str
    right_item_id: str
    relation: str
    measurement: RelationMeasurementBinding
    left_evidence: OrderingEvidence
    right_evidence: OrderingEvidence
    ordering_policy: OrderingPolicy
    source_comparisons: tuple[SourceComparison, ...]
    ordering: OrderingVerdict
    actionability: ActionabilityVerdict
    edge_sha256: str

    @staticmethod
    def relation_edge_id(
        *,
        pair_id: str,
        case_id: str,
        left_item_id: str,
        right_item_id: str,
    ) -> str:
        return canonical_sha256(
            {
                "protocol_id": SUCCESSOR_PROTOCOL_ID,
                "case_id": case_id,
                "pair_id": pair_id,
                "left_item_id": left_item_id,
                "right_item_id": right_item_id,
            }
        )

    @classmethod
    def build(
        cls,
        *,
        pair_id: str,
        case_id: str,
        left_item_id: str,
        right_item_id: str,
        relation: object,
        measurement: RelationMeasurementBinding,
        left_evidence: OrderingEvidence,
        right_evidence: OrderingEvidence,
        ordering_policy: OrderingPolicy,
        actionability: ActionabilityVerdict,
    ) -> "FrozenRelationEdge":
        relation_value = str(getattr(relation, "value", relation))
        edge_id = cls.relation_edge_id(
            pair_id=pair_id,
            case_id=case_id,
            left_item_id=left_item_id,
            right_item_id=right_item_id,
        )
        comparisons = compare_ordering_sources(
            left_evidence, right_evidence, policy=ordering_policy
        )
        ordering = actionability.ordering
        values = {
            "edge_id": edge_id,
            "pair_id": pair_id,
            "case_id": case_id,
            "left_item_id": left_item_id,
            "right_item_id": right_item_id,
            "relation": relation_value,
            "measurement": measurement,
            "left_evidence": left_evidence,
            "right_evidence": right_evidence,
            "ordering_policy": ordering_policy,
            "source_comparisons": comparisons,
            "ordering": ordering,
            "actionability": actionability,
        }
        return cls(edge_sha256=canonical_sha256(cls._payload(**values)), **values)

    @staticmethod
    def _payload(**values: object) -> dict[str, object]:
        return {
            "edge_id": values["edge_id"],
            "pair_id": values["pair_id"],
            "case_id": values["case_id"],
            "left_item_id": values["left_item_id"],
            "right_item_id": values["right_item_id"],
            "relation": values["relation"],
            "measurement": values["measurement"].as_mapping(),
            "left_evidence": _evidence_mapping(values["left_evidence"]),
            "right_evidence": _evidence_mapping(values["right_evidence"]),
            "ordering_policy": _policy_mapping(values["ordering_policy"]),
            "source_comparisons": [
                _comparison_mapping(row) for row in values["source_comparisons"]
            ],
            "ordering": _ordering_mapping(values["ordering"]),
            "actionability": _actionability_mapping(values["actionability"]),
        }

    def __post_init__(self) -> None:
        endpoints = {self.left_item_id, self.right_item_id}
        if (
            not self.pair_id
            or not self.case_id
            or not self.left_item_id
            or not self.right_item_id
            or len(endpoints) != 2
        ):
            raise ValueError("relation edge endpoints must be distinct")
        if self.relation != self.measurement.relation:
            raise ValueError("edge relation does not match semantic measurement")
        if (
            self.left_evidence.item_id != self.left_item_id
            or self.right_evidence.item_id != self.right_item_id
        ):
            raise ValueError("ordering evidence item mismatch")
        expected_id = self.relation_edge_id(
            pair_id=self.pair_id,
            case_id=self.case_id,
            left_item_id=self.left_item_id,
            right_item_id=self.right_item_id,
        )
        if self.edge_id != expected_id:
            raise ValueError("relation edge hash mismatch")
        replayed_comparisons = compare_ordering_sources(
            self.left_evidence,
            self.right_evidence,
            policy=self.ordering_policy,
        )
        if replayed_comparisons != self.source_comparisons:
            raise ValueError("source comparison drift from frozen sidecars/policy")
        replayed = resolve_actionability(
            self.left_item_id,
            self.right_item_id,
            self.relation,
            self.left_evidence,
            self.right_evidence,
            relation_edge_id=self.edge_id,
            ordering_policy=self.ordering_policy,
        )
        if replayed.ordering != self.ordering or replayed != self.actionability:
            raise ValueError("actionability verdict drift from frozen evidence/policy")
        values = {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
            if key != "edge_sha256"
        }
        if self.edge_sha256 != canonical_sha256(self._payload(**values)):
            raise ValueError("relation edge content hash mismatch")

    def as_mapping(self) -> dict[str, object]:
        values = {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
            if key != "edge_sha256"
        }
        return {**self._payload(**values), "edge_sha256": self.edge_sha256}

    @classmethod
    def from_mapping(cls, value: object) -> "FrozenRelationEdge":
        mapping = _closed(value, frozenset(cls.__dataclass_fields__), "relation edge")
        return cls(
            edge_id=mapping["edge_id"],
            pair_id=mapping["pair_id"],
            case_id=mapping["case_id"],
            left_item_id=mapping["left_item_id"],
            right_item_id=mapping["right_item_id"],
            relation=mapping["relation"],
            measurement=RelationMeasurementBinding.from_mapping(mapping["measurement"]),
            left_evidence=_evidence_from_mapping(mapping["left_evidence"]),
            right_evidence=_evidence_from_mapping(mapping["right_evidence"]),
            ordering_policy=_policy_from_mapping(mapping["ordering_policy"]),
            source_comparisons=tuple(
                _comparison_from_mapping(row) for row in mapping["source_comparisons"]
            ),
            ordering=_actionability_from_mapping(
                {
                    "relation_edge_id": mapping["actionability"]["relation_edge_id"],
                    "target_item_id": mapping["actionability"]["target_item_id"],
                    "survivor_item_id": mapping["actionability"]["survivor_item_id"],
                    "mode": mapping["actionability"]["mode"],
                    "reason_code": mapping["actionability"]["reason_code"],
                    "ordering": mapping["ordering"],
                }
            ).ordering,
            actionability=_actionability_from_mapping(mapping["actionability"]),
            edge_sha256=mapping["edge_sha256"],
        )


@dataclass(frozen=True)
class FrozenRelationGraph:
    protocol_id: str
    protocol_manifest_sha256: str
    case_id: str
    runtime_case_sha256: str
    item_set_sha256: str
    instrument_manifest_sha256: str
    cache_manifest_sha256: str
    edges: tuple[FrozenRelationEdge, ...]
    graph_sha256: str
    schema_version: str = RELATION_GRAPH_SCHEMA_VERSION

    @classmethod
    def build(
        cls,
        *,
        case: RuntimeRepairCase,
        item_ids: tuple[str, ...],
        protocol_manifest_sha256: str,
        instrument_manifest_sha256: str,
        cache_manifest_sha256: str,
        edges: tuple[FrozenRelationEdge, ...],
        protocol_id: str = SUCCESSOR_PROTOCOL_ID,
    ) -> "FrozenRelationGraph":
        ordered = tuple(sorted(edges, key=lambda edge: edge.edge_id))
        values = {
            "protocol_id": protocol_id,
            "protocol_manifest_sha256": protocol_manifest_sha256,
            "case_id": case.case_id,
            "runtime_case_sha256": runtime_case_sha256(case),
            "item_set_sha256": item_set_sha256(item_ids),
            "instrument_manifest_sha256": instrument_manifest_sha256,
            "cache_manifest_sha256": cache_manifest_sha256,
            "edges": ordered,
        }
        graph = cls(
            graph_sha256=canonical_sha256(cls._payload(**values)), **values
        )
        graph.assert_matches(
            case=case,
            item_ids=item_ids,
            expected_graph_sha256=graph.graph_sha256,
            expected_protocol_manifest_sha256=protocol_manifest_sha256,
        )
        return graph

    @staticmethod
    def _payload(**values: object) -> dict[str, object]:
        return {
            "schema_version": RELATION_GRAPH_SCHEMA_VERSION,
            "protocol_id": values["protocol_id"],
            "protocol_manifest_sha256": values["protocol_manifest_sha256"],
            "case_id": values["case_id"],
            "runtime_case_sha256": values["runtime_case_sha256"],
            "item_set_sha256": values["item_set_sha256"],
            "instrument_manifest_sha256": values["instrument_manifest_sha256"],
            "cache_manifest_sha256": values["cache_manifest_sha256"],
            "edges": [edge.as_mapping() for edge in values["edges"]],
        }

    def __post_init__(self) -> None:
        if (
            self.schema_version != RELATION_GRAPH_SCHEMA_VERSION
            or self.protocol_id != SUCCESSOR_PROTOCOL_ID
        ):
            raise ValueError("unregistered relation graph protocol/schema")
        if not self.case_id:
            raise ValueError("graph case_id is required")
        for name in (
            "protocol_manifest_sha256",
            "runtime_case_sha256",
            "item_set_sha256",
            "instrument_manifest_sha256",
            "cache_manifest_sha256",
            "graph_sha256",
        ):
            _hash(getattr(self, name), name)
        if self.edges != tuple(sorted(self.edges, key=lambda edge: edge.edge_id)):
            raise ValueError("graph edges are not canonical")
        pairs: set[tuple[str, str]] = set()
        pair_ids: set[str] = set()
        ids: set[str] = set()
        for edge in self.edges:
            pair = tuple(sorted((edge.left_item_id, edge.right_item_id)))
            if edge.edge_id in ids or edge.pair_id in pair_ids or pair in pairs:
                raise ValueError("relation graph duplicate edge")
            if edge.measurement.instrument_manifest_sha256 != self.instrument_manifest_sha256:
                raise ValueError("edge instrument manifest drift")
            if edge.case_id != self.case_id:
                raise ValueError("edge belongs to another case")
            ids.add(edge.edge_id)
            pair_ids.add(edge.pair_id)
            pairs.add(pair)
        values = {
            key: getattr(self, key)
            for key in (
                "protocol_id",
                "protocol_manifest_sha256",
                "case_id",
                "runtime_case_sha256",
                "item_set_sha256",
                "instrument_manifest_sha256",
                "cache_manifest_sha256",
                "edges",
            )
        }
        if self.graph_sha256 != canonical_sha256(self._payload(**values)):
            raise ValueError("relation graph content hash mismatch")

    def assert_matches(
        self,
        *,
        case: RuntimeRepairCase,
        item_ids: tuple[str, ...],
        expected_graph_sha256: str,
        expected_protocol_manifest_sha256: str,
    ) -> None:
        if expected_graph_sha256 != self.graph_sha256:
            raise ValueError("registered graph hash mismatch")
        if expected_protocol_manifest_sha256 != self.protocol_manifest_sha256:
            raise ValueError("registered protocol manifest hash mismatch")
        if case.case_id != self.case_id:
            raise ValueError("graph case_id mismatch")
        if runtime_case_sha256(case) != self.runtime_case_sha256:
            raise ValueError("graph runtime case hash mismatch")
        expected_runtime_ids = {item.item_id for item in case.items if item.retrieved}
        if set(item_ids) != expected_runtime_ids:
            raise ValueError("input item-set does not equal runtime retrieved set")
        if item_set_sha256(item_ids) != self.item_set_sha256:
            raise ValueError("graph item-set hash mismatch")
        items = {item.item_id: item for item in case.items}
        known = set(item_ids)
        for edge in self.edges:
            if {edge.left_item_id, edge.right_item_id} - known:
                raise ValueError("graph contains dangling/cross-case endpoint")
            if not edge.measurement.matches_texts(
                items[edge.left_item_id].text, items[edge.right_item_id].text
            ):
                raise ValueError("edge cache measurement does not bind runtime texts")

    def as_mapping(self) -> dict[str, object]:
        values = {
            key: getattr(self, key)
            for key in (
                "protocol_id",
                "protocol_manifest_sha256",
                "case_id",
                "runtime_case_sha256",
                "item_set_sha256",
                "instrument_manifest_sha256",
                "cache_manifest_sha256",
                "edges",
            )
        }
        return {**self._payload(**values), "graph_sha256": self.graph_sha256}

    @classmethod
    def from_mapping(cls, value: object) -> "FrozenRelationGraph":
        mapping = _closed(value, frozenset(cls.__dataclass_fields__), "relation graph")
        return cls(
            **{
                key: mapping[key]
                for key in cls.__dataclass_fields__
                if key != "edges"
            },
            edges=tuple(FrozenRelationEdge.from_mapping(edge) for edge in mapping["edges"]),
        )
