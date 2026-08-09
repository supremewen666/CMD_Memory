#!/usr/bin/env python3
"""Freeze relation measurements, graphs, intents, and GPU-ready V4 cases.

The relation instrument and proposer receive deployment-visible surfaces only.
Shadow labels are joined after every graph and intent has been content-bound.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import Mapping, Protocol, Sequence

from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.counterfactual.actionability import ActionMode, resolve_actionability
from cmd_audit.counterfactual.item_ordering import (
    EvidenceReliability,
    OrderingEvidence,
    OrderingPolicy,
)
from cmd_audit.counterfactual.relation_cache import (
    NORMALIZATION_VERSION,
    RelationCache,
    RelationCacheKey,
)
from cmd_audit.counterfactual.relation_graph import (
    FrozenRelationEdge,
    FrozenRelationGraph,
    RelationMeasurementBinding,
    canonical_sha256,
)
from cmd_audit.counterfactual.slot_relation import (
    PARSER_VERSION,
    PROMPT_TEMPLATE_SHA256,
    SLOT_RELATION_VERSION,
    RelationType,
    judge_relation,
)
from cmd_audit.repair.parametric_policy import (
    PolicyContext,
    RepairIntent,
    compile_intent,
)
from experiments.build_v4_evolution_dataset import (
    RELATION_REQUEST_SCHEMA_VERSION,
    RUNTIME_ROW_SCHEMA_VERSION,
    SHADOW_ROW_SCHEMA_VERSION,
)
from experiments.v4_live_materialization import (
    LIVE_INPUT_SCHEMA_VERSION,
    runtime_case_from_mapping,
    validate_live_input,
)
from experiments.validate_v4_evolution_dataset import validate_bundle


PREPARATION_SCHEMA_VERSION = "cmd-v4-live-input-preparation-manifest-v1"
INSTRUMENT_MANIFEST_SCHEMA_VERSION = "cmd-v4-relation-instrument-manifest-v1"
CACHE_RECORDS_SCHEMA_VERSION = "cmd-v4-relation-cache-records-v1"
INTENT_PROPOSER_VERSION = "cmd-v4-llm-intent-proposer-v1"
INTENT_PROPOSAL_ROW_VERSION = "cmd-v4-intent-proposal-row-v1"
GRAPH_ROW_VERSION = "cmd-v4-frozen-graph-row-v1"
DEFAULT_MAX_UNCERTAIN_RATE = 0.05
DEFAULT_PROPOSER_RETRIES = 2
_HEX = re.compile(r"^[0-9a-f]{64}$")
_PROPOSAL_KEYS = frozenset(
    {
        "strategy_id",
        "relation_edge_id",
        "effect",
        "target_item_id",
        "replacement_item_id",
    }
)
INTENT_PROMPT_TEMPLATE = (
    "Return exactly one JSON object with key proposals. The value must contain "
    "exactly proposals_needed complete repair proposals. Use only listed edge "
    "and item identifiers. Destructive effects demote, suppress, or replace "
    "require the listed actionability target; replace also requires its survivor. "
    "Unknown or conflicting direction permits only annotate_conflict, verify, or "
    "abstain. strategy_id must name a reusable versioned semantic motif and must "
    "not contain any concrete item identifier. Do not add fields or prose."
)
INTENT_PROMPT_TEMPLATE_SHA256 = hashlib.sha256(
    INTENT_PROMPT_TEMPLATE.encode("utf-8")
).hexdigest()
ORDERING_POLICY = OrderingPolicy(
    policy_version="cmd-v4-event-sequence-ordering-v1",
    accepted_sources=("event_sequence",),
    source_semantics=(("event_sequence", "chronology_lower_target"),),
)


class TextGenerator(Protocol):
    def generate(self, prompt: str, *, system: str | None = None) -> str: ...


class IntentProposalCache:
    """Persistent content-addressed cache for validated proposer JSON."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS v4_intent_proposals (
                cache_key TEXT PRIMARY KEY,
                proposer_input_sha256 TEXT NOT NULL,
                prompt_template_sha256 TEXT NOT NULL,
                proposer_version TEXT NOT NULL,
                proposer_model_sha256 TEXT NOT NULL,
                proposals_needed INTEGER NOT NULL,
                response_json TEXT NOT NULL,
                response_sha256 TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def get(self, cache_key: str) -> Mapping[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT response_json, response_sha256 "
                "FROM v4_intent_proposals WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if row is None:
            return None
        value = json.loads(row[0])
        if not isinstance(value, Mapping) or canonical_sha256(value) != row[1]:
            raise ValueError("intent proposal cache record failed content replay")
        return value

    def put(
        self,
        *,
        cache_key: str,
        proposer_input_sha256: str,
        proposer_model_sha256: str,
        proposals_needed: int,
        response: Mapping[str, object],
    ) -> None:
        response_json = _canonical_bytes(response).decode("utf-8")
        response_sha256 = canonical_sha256(response)
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO v4_intent_proposals
                (cache_key, proposer_input_sha256, prompt_template_sha256,
                 proposer_version, proposer_model_sha256, proposals_needed,
                 response_json, response_sha256)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cache_key,
                    proposer_input_sha256,
                    INTENT_PROMPT_TEMPLATE_SHA256,
                    INTENT_PROPOSER_VERSION,
                    proposer_model_sha256,
                    proposals_needed,
                    response_json,
                    response_sha256,
                ),
            )
            stored = self._connection.execute(
                "SELECT response_sha256 FROM v4_intent_proposals WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if stored is None or stored[0] != response_sha256:
            raise ValueError("conflicting intent proposal cache replay")

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "IntentProposalCache":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_hash(value: str, name: str) -> str:
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _read_payload(path: Path) -> bytes:
    payload = path.read_bytes()
    return gzip.decompress(payload) if path.suffix == ".gz" else payload


def _load_json(path: Path) -> Mapping[str, object]:
    value = json.loads(_read_payload(path).decode("utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{path.name} must contain one JSON object")
    return value


def _load_jsonl(path: Path) -> list[Mapping[str, object]]:
    rows: list[Mapping[str, object]] = []
    for line_number, line in enumerate(
        _read_payload(path).decode("utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, Mapping):
            raise ValueError(f"{path.name}:{line_number} must be a JSON object")
        rows.append(value)
    return rows


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _atomic_jsonl(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(b"".join(_canonical_bytes(row) + b"\n" for row in rows))
    temporary.replace(path)


def _append_progress(path: Path | None, value: Mapping[str, object]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(_canonical_bytes(value).decode("utf-8") + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _ordering_evidence(value: object) -> OrderingEvidence:
    if not isinstance(value, Mapping):
        raise ValueError("ordering evidence must be a mapping")
    expected = {
        "item_id",
        "observed_at",
        "observed_at_domain",
        "event_sequence",
        "event_stream_id",
        "source_priority",
        "source_priority_domain",
        "provenance",
        "audit_version",
        "deployment_visible",
        "reliability",
    }
    if set(value) != expected:
        raise ValueError("ordering evidence mapping is not closed")
    observed_at = value["observed_at"]
    if observed_at is not None:
        if not isinstance(observed_at, str):
            raise ValueError("observed_at must be an RFC3339 string")
        observed_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    return OrderingEvidence(
        item_id=value["item_id"],
        observed_at=observed_at,
        observed_at_domain=value["observed_at_domain"],
        event_sequence=value["event_sequence"],
        event_stream_id=value["event_stream_id"],
        source_priority=value["source_priority"],
        source_priority_domain=value["source_priority_domain"],
        provenance=value["provenance"],
        audit_version=value["audit_version"],
        deployment_visible=value["deployment_visible"],
        reliability=EvidenceReliability(value["reliability"]),
    )


def _cache_record(measurement: RelationMeasurementBinding) -> dict[str, object]:
    value = measurement.as_mapping()
    return {
        key: value[key]
        for key in (
            "cache_key",
            "canonical_left",
            "canonical_right",
            "prompt_sha256",
            "parser_version",
            "model_id",
            "model_config_hash",
            "normalization_version",
            "instrument_version",
        )
    } | {
        "verdict": {
            key: value[key]
            for key in (
                "relation",
                "slot",
                "abstained",
                "prompt_sha256",
                "parser_version",
                "model_id",
            )
        }
    }


def _selected_assignments(
    assignments: Sequence[Mapping[str, object]],
    eligible_case_ids: set[str],
    limit: int | None,
) -> list[Mapping[str, object]]:
    eligible = sorted(
        (row for row in assignments if row.get("case_id") in eligible_case_ids),
        key=lambda row: int(row["stream_position"]),
    )
    if limit is None or limit >= len(eligible):
        return eligible
    if limit < 1:
        raise ValueError("limit must be positive")
    represented = [row for row in eligible if row.get("probe_set") == "represented"]
    unseen = [row for row in eligible if row.get("probe_set") == "unseen"]
    represented_target = (limit + 1) // 2
    unseen_target = limit // 2
    chosen = represented[:represented_target] + unseen[:unseen_target]
    chosen_ids = {str(row["case_id"]) for row in chosen}
    if len(chosen) < limit:
        chosen.extend(row for row in eligible if row["case_id"] not in chosen_ids)
        chosen = chosen[:limit]
    return sorted(chosen, key=lambda row: int(row["stream_position"]))


def _instrument_manifest(
    *,
    dataset_sha256: str,
    model_id: str,
    model_hash: str,
    max_uncertain_rate: float,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": INSTRUMENT_MANIFEST_SCHEMA_VERSION,
        "dataset_sha256": dataset_sha256,
        "instrument_version": SLOT_RELATION_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "parser_version": PARSER_VERSION,
        "prompt_template_sha256": PROMPT_TEMPLATE_SHA256,
        "model_id": model_id,
        "model_config_sha256": model_hash,
        "max_uncertain_rate": max_uncertain_rate,
        "text_only": True,
        "direction_free": True,
        "gold_inputs": False,
    }
    return {**payload, "instrument_manifest_sha256": canonical_sha256(payload)}


def _measure_relations(
    requests: Sequence[Mapping[str, object]],
    *,
    judge: TextGenerator,
    cache: RelationCache,
    instrument_manifest_sha256: str,
    model_id: str,
    model_hash: str,
    max_uncertain_rate: float,
    progress_path: Path | None,
) -> tuple[
    dict[str, RelationMeasurementBinding],
    list[dict[str, object]],
    int,
    int,
]:
    by_request: dict[str, RelationMeasurementBinding] = {}
    selected_cache_records: dict[str, dict[str, object]] = {}
    model_calls = 0
    uncertain = 0
    total = len(requests)
    for index, request in enumerate(requests, 1):
        if (
            set(request)
            != {
                "schema_version",
                "request_id",
                "pair_id",
                "case_id",
                "left_item_id",
                "right_item_id",
                "left_text",
                "right_text",
                "left_evidence",
                "right_evidence",
            }
            or request.get("schema_version") != RELATION_REQUEST_SCHEMA_VERSION
        ):
            raise ValueError("relation request mapping is not closed or versioned")
        key = RelationCacheKey.build(
            request["left_text"],
            request["right_text"],
            prompt_sha256=PROMPT_TEMPLATE_SHA256,
            parser_version=PARSER_VERSION,
            model_id=model_id,
            model_config_hash=model_hash,
            normalization_version=NORMALIZATION_VERSION,
            instrument_version=SLOT_RELATION_VERSION,
        )
        if cache.get(key) is None:
            model_calls += 1
        verdict = judge_relation(
            request["left_text"],
            request["right_text"],
            judge=judge,
            cache=cache,
            model_id=model_id,
            model_config_hash=model_hash,
        )
        uncertain += verdict.relation is RelationType.UNCERTAIN
        measurement = RelationMeasurementBinding.build(
            left_text=request["left_text"],
            right_text=request["right_text"],
            relation=verdict.relation,
            slot=verdict.slot,
            abstained=verdict.abstained,
            prompt_sha256=verdict.prompt_sha256,
            parser_version=verdict.parser_version,
            model_id=verdict.model_id,
            model_config_hash=model_hash,
            normalization_version=NORMALIZATION_VERSION,
            instrument_version=SLOT_RELATION_VERSION,
            instrument_manifest_sha256=instrument_manifest_sha256,
        )
        request_id = request["request_id"]
        if not isinstance(request_id, str) or request_id in by_request:
            raise ValueError("relation request IDs must be unique strings")
        by_request[request_id] = measurement
        selected_cache_records[measurement.cache_key] = _cache_record(measurement)
        _append_progress(
            progress_path,
            {
                "event": "relation_measured",
                "completed": index,
                "total": total,
                "request_id": request_id,
                "cache_key": measurement.cache_key,
                "relation": measurement.relation,
            },
        )
    uncertain_rate = uncertain / total if total else 0.0
    if uncertain_rate > max_uncertain_rate:
        raise ValueError(
            "relation uncertainty rate exceeds frozen cutoff: "
            f"{uncertain_rate:.6f} > {max_uncertain_rate:.6f}"
        )
    return (
        by_request,
        [selected_cache_records[key] for key in sorted(selected_cache_records)],
        model_calls,
        uncertain,
    )


def _build_graph(
    runtime_mapping: Mapping[str, object],
    requests: Sequence[Mapping[str, object]],
    measurements: Mapping[str, RelationMeasurementBinding],
    *,
    protocol_manifest_sha256: str,
    instrument_manifest_sha256: str,
    cache_manifest_sha256: str,
) -> FrozenRelationGraph:
    runtime = runtime_case_from_mapping(runtime_mapping)
    edges: list[FrozenRelationEdge] = []
    for request in requests:
        left = _ordering_evidence(request["left_evidence"])
        right = _ordering_evidence(request["right_evidence"])
        edge_id = FrozenRelationEdge.relation_edge_id(
            pair_id=request["pair_id"],
            case_id=runtime.case_id,
            left_item_id=request["left_item_id"],
            right_item_id=request["right_item_id"],
        )
        measurement = measurements[request["request_id"]]
        actionability = resolve_actionability(
            request["left_item_id"],
            request["right_item_id"],
            measurement.relation,
            left,
            right,
            relation_edge_id=edge_id,
            ordering_policy=ORDERING_POLICY,
        )
        edges.append(
            FrozenRelationEdge.build(
                pair_id=request["pair_id"],
                case_id=runtime.case_id,
                left_item_id=request["left_item_id"],
                right_item_id=request["right_item_id"],
                relation=measurement.relation,
                measurement=measurement,
                left_evidence=left,
                right_evidence=right,
                ordering_policy=ORDERING_POLICY,
                actionability=actionability,
            )
        )
    return FrozenRelationGraph.build(
        case=runtime,
        item_ids=tuple(item.item_id for item in runtime.items if item.retrieved),
        protocol_manifest_sha256=protocol_manifest_sha256,
        instrument_manifest_sha256=instrument_manifest_sha256,
        cache_manifest_sha256=cache_manifest_sha256,
        edges=tuple(edges),
    )


def _evidence_ids(edge: FrozenRelationEdge) -> tuple[str, ...]:
    return tuple(sorted((edge.edge_sha256, edge.measurement.relation_verdict_sha256)))


def _safe_baseline(edge: FrozenRelationEdge) -> RepairIntent:
    if edge.actionability.mode is ActionMode.DESTRUCTIVE:
        effect = "demote"
        target = edge.actionability.target_item_id
    elif edge.actionability.mode is ActionMode.ANNOTATE_ONLY:
        effect = "annotate_conflict"
        target = None
    else:
        effect = "verify"
        target = None
    return RepairIntent.build(
        strategy_id="frozen_safe_actionability_v1",
        relation_edge_id=edge.edge_id,
        target_item_id=target,
        effect=effect,
        proposer_id="cmd-v4-frozen-symbolic-baseline-v1",
        proposer_model_hash=INTENT_PROMPT_TEMPLATE_SHA256,
        evidence_ids=_evidence_ids(edge),
    )


def proposer_surface(
    runtime_mapping: Mapping[str, object],
    graph: FrozenRelationGraph,
    *,
    proposals_needed: int,
) -> dict[str, object]:
    items = runtime_mapping["items"]
    if not isinstance(items, list):
        raise ValueError("runtime items must be a list")
    return {
        "schema_version": "cmd-v4-intent-proposer-input-v1",
        "proposals_needed": proposals_needed,
        "runtime_surface": runtime_mapping["runtime_surface"],
        "query": runtime_mapping["query"],
        "retrieved_items": [
            {
                "item_id": item["item_id"],
                "text": item["text"],
                "store": item["store"],
                "rank": item["rank"],
            }
            for item in items
            if item["retrieved"] is True
        ],
        "edges": [
            {
                "edge_id": edge.edge_id,
                "relation": edge.relation,
                "slot": edge.measurement.slot,
                "actionability": {
                    "mode": edge.actionability.mode.value,
                    "target_item_id": edge.actionability.target_item_id,
                    "survivor_item_id": edge.actionability.survivor_item_id,
                    "reason_code": edge.actionability.reason_code,
                },
            }
            for edge in graph.edges
        ],
    }


def intent_proposal_cache_key(
    surface: Mapping[str, object],
    *,
    proposer_model_hash: str,
    proposals_needed: int,
) -> str:
    return canonical_sha256(
        {
            "proposer_input_sha256": canonical_sha256(surface),
            "prompt_template_sha256": INTENT_PROMPT_TEMPLATE_SHA256,
            "proposer_version": INTENT_PROPOSER_VERSION,
            "proposer_model_sha256": proposer_model_hash,
            "proposals_needed": proposals_needed,
        }
    )


def parse_intent_proposals(
    response: str,
    *,
    graph: FrozenRelationGraph,
    proposals_needed: int,
    proposer_model_hash: str,
) -> tuple[RepairIntent, ...]:
    value = json.loads(response)
    if not isinstance(value, Mapping) or set(value) != {"proposals"}:
        raise ValueError("proposer response must be a closed proposals object")
    proposals = value["proposals"]
    if not isinstance(proposals, list) or len(proposals) != proposals_needed:
        raise ValueError("proposer response does not match frozen proposal budget")
    edges = {edge.edge_id: edge for edge in graph.edges}
    intents: list[RepairIntent] = []
    for proposal in proposals:
        if not isinstance(proposal, Mapping) or set(proposal) != _PROPOSAL_KEYS:
            raise ValueError("intent proposal mapping is not closed")
        edge = edges.get(proposal["relation_edge_id"])
        if edge is None:
            raise ValueError("intent proposal references an absent graph edge")
        intent = RepairIntent.build(
            strategy_id=proposal["strategy_id"],
            relation_edge_id=edge.edge_id,
            target_item_id=proposal["target_item_id"],
            effect=proposal["effect"],
            replacement_item_id=proposal["replacement_item_id"],
            proposer_id=INTENT_PROPOSER_VERSION,
            proposer_model_hash=proposer_model_hash,
            evidence_ids=_evidence_ids(edge),
        )
        compile_intent(intent, graph=graph)
        intents.append(intent)
    if len({intent.intent_id for intent in intents}) != len(intents):
        raise ValueError("intent proposer emitted duplicate concrete intents")
    return tuple(intents)


def _propose_intents(
    runtime_mapping: Mapping[str, object],
    graph: FrozenRelationGraph,
    *,
    judge: TextGenerator,
    candidate_budget: int,
    proposer_model_hash: str,
    max_retries: int,
    cache_path: Path,
) -> tuple[tuple[RepairIntent, ...], dict[str, object], int]:
    if candidate_budget < 1:
        raise ValueError("candidate_budget must be positive")
    baseline = _safe_baseline(graph.edges[0])
    proposals_needed = candidate_budget - 1
    if proposals_needed == 0:
        return (
            (baseline,),
            {
                "proposer_input_sha256": None,
                "proposer_response_sha256": None,
                "proposer_response": None,
                "attempts": 0,
                "proposer_cache_key": None,
                "proposer_cache_hit": False,
            },
            0,
        )
    surface = proposer_surface(
        runtime_mapping,
        graph,
        proposals_needed=proposals_needed,
    )
    input_sha256 = canonical_sha256(surface)
    cache_key = intent_proposal_cache_key(
        surface,
        proposer_model_hash=proposer_model_hash,
        proposals_needed=proposals_needed,
    )
    with IntentProposalCache(cache_path) as cache:
        cached_response = cache.get(cache_key)
    if cached_response is not None:
        proposed = parse_intent_proposals(
            _canonical_bytes(cached_response).decode("utf-8"),
            graph=graph,
            proposals_needed=proposals_needed,
            proposer_model_hash=proposer_model_hash,
        )
        intents = (baseline, *proposed)
        if len({intent.intent_id for intent in intents}) != candidate_budget:
            raise ValueError("cached baseline and proposer intents are not unique")
        return (
            intents,
            {
                "proposer_input_sha256": input_sha256,
                "proposer_response_sha256": canonical_sha256(cached_response),
                "proposer_response": dict(cached_response),
                "attempts": 0,
                "proposer_cache_key": cache_key,
                "proposer_cache_hit": True,
            },
            0,
        )
    prompt = f"{INTENT_PROMPT_TEMPLATE}\nPROPOSER_INPUT:\n{_canonical_bytes(surface).decode('utf-8')}"
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 2):
        response = judge.generate(prompt)
        try:
            proposed = parse_intent_proposals(
                response,
                graph=graph,
                proposals_needed=proposals_needed,
                proposer_model_hash=proposer_model_hash,
            )
            intents = (baseline, *proposed)
            if len({intent.intent_id for intent in intents}) != candidate_budget:
                raise ValueError("baseline and proposer intents are not unique")
            parsed_response = json.loads(response)
            with IntentProposalCache(cache_path) as cache:
                cache.put(
                    cache_key=cache_key,
                    proposer_input_sha256=input_sha256,
                    proposer_model_sha256=proposer_model_hash,
                    proposals_needed=proposals_needed,
                    response=parsed_response,
                )
            return (
                intents,
                {
                    "proposer_input_sha256": input_sha256,
                    "proposer_response_sha256": canonical_sha256(parsed_response),
                    "proposer_response": parsed_response,
                    "attempts": attempt,
                    "proposer_cache_key": cache_key,
                    "proposer_cache_hit": False,
                },
                attempt,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            last_error = error
    raise ValueError(
        f"intent proposer exhausted closed-schema retries: {type(last_error).__name__}"
    ) from last_error


def _policy_context(
    *,
    case_id: str,
    event_index: int,
    runtime_mapping: Mapping[str, object],
    graph: FrozenRelationGraph,
) -> PolicyContext:
    positive = sum(edge.relation == "same_slot_different_value" for edge in graph.edges)
    destructive = sum(
        edge.actionability.mode is ActionMode.DESTRUCTIVE for edge in graph.edges
    )
    annotate = sum(
        edge.actionability.mode is ActionMode.ANNOTATE_ONLY for edge in graph.edges
    )
    uncertain = sum(edge.relation == "uncertain" for edge in graph.edges)
    if destructive:
        cluster = "directed_semantic_update"
    elif positive:
        cluster = "undirected_semantic_conflict"
    else:
        cluster = "semantic_uncertainty"
    signature = f"positive-{positive}_directional-{destructive}_uncertain-{uncertain}"
    items = runtime_mapping["items"]
    return PolicyContext(
        case_id=case_id,
        event_index=event_index,
        graph_sha256=graph.graph_sha256,
        runtime_surface=runtime_mapping["runtime_surface"],
        domain="memory",
        semantic_cluster=cluster,
        signal_signature=signature,
        features={
            "annotate_relations": float(annotate),
            "destructive_relations": float(destructive),
            "positive_relations": float(positive),
            "relation_edges": float(len(graph.edges)),
            "retrieved_items": float(sum(item["retrieved"] is True for item in items)),
            "uncertain_relations": float(uncertain),
        },
    )


def _model_config_hash(config: LLMClientConfig, adapter_version: str) -> str:
    return canonical_sha256(
        {
            "adapter_version": adapter_version,
            "base_url": config.base_url,
            "max_retries": config.max_retries,
            "model": config.model,
            "temperature": config.temperature,
            "timeout_seconds": config.timeout_seconds,
        }
    )


def prepare_live_cases(
    *,
    dataset_dir: Path,
    output_path: Path,
    artifacts_dir: Path,
    cache_path: Path,
    relation_judge: TextGenerator,
    intent_judge: TextGenerator,
    instrument_model_id: str,
    instrument_model_hash: str,
    proposer_model_id: str,
    proposer_model_hash: str,
    candidate_budget: int = 4,
    max_uncertain_rate: float = DEFAULT_MAX_UNCERTAIN_RATE,
    max_proposer_retries: int = DEFAULT_PROPOSER_RETRIES,
    limit: int | None = None,
    progress_path: Path | None = None,
) -> dict[str, object]:
    """Build one immutable prepared stream; persistent cache permits safe resume."""
    dataset_dir = Path(dataset_dir)
    output_path = Path(output_path)
    artifacts_dir = Path(artifacts_dir)
    cache_path = Path(cache_path)
    _require_hash(instrument_model_hash, "instrument_model_hash")
    _require_hash(proposer_model_hash, "proposer_model_hash")
    if not instrument_model_id or not proposer_model_id:
        raise ValueError("instrument and proposer model IDs are required")
    if candidate_budget < 1:
        raise ValueError("candidate_budget must be positive")
    if (
        isinstance(max_uncertain_rate, bool)
        or not isinstance(max_uncertain_rate, (int, float))
        or not math.isfinite(max_uncertain_rate)
        or not 0 <= max_uncertain_rate <= 1
    ):
        raise ValueError("max_uncertain_rate must be finite in [0, 1]")
    if max_proposer_retries < 0:
        raise ValueError("max_proposer_retries must be non-negative")
    manifest_path = artifacts_dir / "preparation_manifest.json"
    reserved = (
        output_path,
        manifest_path,
        artifacts_dir / "instrument_manifest.json",
        artifacts_dir / "relation_cache_records.jsonl",
        artifacts_dir / "graphs.jsonl",
        artifacts_dir / "intent_proposals.jsonl",
    )
    if any(path.exists() for path in reserved):
        raise ValueError("refusing to overwrite immutable V4 preparation artifacts")

    dataset_validation = validate_bundle(dataset_dir)
    if dataset_validation.get("decision") != "PASS":
        raise ValueError("V4 CPU dataset validation did not pass")
    dataset_manifest = _load_json(dataset_dir / "dataset_manifest.json")
    dataset_sha256 = dataset_manifest.get("dataset_sha256")
    _require_hash(dataset_sha256, "dataset_sha256")
    runtime_rows = _load_jsonl(dataset_dir / "runtime_cases.jsonl.gz")
    shadow_rows = _load_jsonl(dataset_dir / "shadow_cases.jsonl.gz")
    relation_requests = _load_jsonl(dataset_dir / "relation_requests.jsonl.gz")
    split_manifest = _load_json(dataset_dir / "split_manifest.json.gz")
    assignments = split_manifest.get("assignments")
    if not isinstance(assignments, list):
        raise ValueError("split assignments must be a list")
    runtime_by_id = {str(row["case_id"]): row for row in runtime_rows}
    shadow_by_id = {str(row["case_id"]): row for row in shadow_rows}
    requests_by_case: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for request in relation_requests:
        requests_by_case[str(request["case_id"])].append(request)
    eligible_case_ids = set(requests_by_case)
    selected_assignments = _selected_assignments(
        assignments,
        eligible_case_ids,
        limit,
    )
    selected_case_ids = [str(row["case_id"]) for row in selected_assignments]
    selected_requests = [
        request
        for case_id in selected_case_ids
        for request in sorted(
            requests_by_case[case_id], key=lambda row: str(row["request_id"])
        )
    ]
    if not selected_case_ids or not selected_requests:
        raise ValueError("no graph-eligible cases were selected")
    _append_progress(
        progress_path,
        {
            "event": "preparation_started",
            "selected_cases": len(selected_case_ids),
            "relation_requests": len(selected_requests),
        },
    )

    instrument_manifest = _instrument_manifest(
        dataset_sha256=dataset_sha256,
        model_id=instrument_model_id,
        model_hash=instrument_model_hash,
        max_uncertain_rate=float(max_uncertain_rate),
    )
    instrument_hash = instrument_manifest["instrument_manifest_sha256"]
    with RelationCache(cache_path) as cache:
        measurements, cache_records, relation_calls, uncertain_count = (
            _measure_relations(
                selected_requests,
                judge=relation_judge,
                cache=cache,
                instrument_manifest_sha256=instrument_hash,
                model_id=instrument_model_id,
                model_hash=instrument_model_hash,
                max_uncertain_rate=float(max_uncertain_rate),
                progress_path=progress_path,
            )
        )
    cache_payload = {
        "schema_version": CACHE_RECORDS_SCHEMA_VERSION,
        "instrument_manifest_sha256": instrument_hash,
        "records": cache_records,
    }
    relation_cache_sha256 = canonical_sha256(cache_payload)

    graphs: dict[str, FrozenRelationGraph] = {}
    graph_rows: list[dict[str, object]] = []
    for assignment in selected_assignments:
        case_id = str(assignment["case_id"])
        runtime_row = runtime_by_id[case_id]
        if (
            set(runtime_row)
            != {"schema_version", "case_id", "source_case_sha256", "runtime_case"}
            or runtime_row.get("schema_version") != RUNTIME_ROW_SCHEMA_VERSION
        ):
            raise ValueError("runtime row mapping is not closed or versioned")
        graph = _build_graph(
            runtime_row["runtime_case"],
            requests_by_case[case_id],
            measurements,
            protocol_manifest_sha256=dataset_sha256,
            instrument_manifest_sha256=instrument_hash,
            cache_manifest_sha256=relation_cache_sha256,
        )
        graphs[case_id] = graph
        graph_rows.append(
            {
                "schema_version": GRAPH_ROW_VERSION,
                "case_id": case_id,
                "graph": graph.as_mapping(),
            }
        )

    prepared_rows: list[dict[str, object]] = []
    intent_rows: list[dict[str, object]] = []
    proposer_calls = 0
    proposer_cache_hits = 0
    for index, assignment in enumerate(selected_assignments, 1):
        case_id = str(assignment["case_id"])
        runtime_row = runtime_by_id[case_id]
        shadow_row = shadow_by_id[case_id]
        if (
            set(shadow_row)
            != {
                "schema_version",
                "case_id",
                "family_id",
                "dependency_group",
                "probe_set",
                "stream_role",
                "source_case_sha256",
                "probe_case",
                "hidden_intent",
            }
            or shadow_row.get("schema_version") != SHADOW_ROW_SCHEMA_VERSION
        ):
            raise ValueError("shadow row mapping is not closed or versioned")
        graph = graphs[case_id]
        intents, proposal_binding, calls = _propose_intents(
            runtime_row["runtime_case"],
            graph,
            judge=intent_judge,
            candidate_budget=candidate_budget,
            proposer_model_hash=proposer_model_hash,
            max_retries=max_proposer_retries,
            cache_path=cache_path,
        )
        proposer_calls += calls
        proposer_cache_hits += proposal_binding["proposer_cache_hit"] is True
        chain_pairs = (
            [
                [intents[0].intent_id, intents[1].intent_id],
                [intents[1].intent_id, intents[0].intent_id],
            ]
            if len(intents) > 1
            else []
        )
        context = _policy_context(
            case_id=case_id,
            event_index=int(assignment["selection_event_index"]),
            runtime_mapping=runtime_row["runtime_case"],
            graph=graph,
        )
        prepared = {
            "schema_version": LIVE_INPUT_SCHEMA_VERSION,
            "case_id": case_id,
            "family_id": shadow_row["family_id"],
            "probe_set": shadow_row["probe_set"],
            "context": context.to_mapping(),
            "graph": graph.as_mapping(),
            "runtime_case": runtime_row["runtime_case"],
            "intents": [intent.to_mapping() for intent in intents],
            "legacy_intent_id": intents[0].intent_id,
            "chain_pairs": chain_pairs,
            "probe_case": shadow_row["probe_case"],
        }
        validate_live_input(prepared)
        prepared_rows.append(prepared)
        intent_rows.append(
            {
                "schema_version": INTENT_PROPOSAL_ROW_VERSION,
                "case_id": case_id,
                "graph_sha256": graph.graph_sha256,
                "legacy_intent_id": intents[0].intent_id,
                "intents": [intent.to_mapping() for intent in intents],
                "chain_pairs": chain_pairs,
                **proposal_binding,
            }
        )
        _append_progress(
            progress_path,
            {
                "event": "case_prepared",
                "case_id": case_id,
                "completed": index,
                "total": len(selected_assignments),
                "graph_sha256": graph.graph_sha256,
            },
        )

    _atomic_json(artifacts_dir / "instrument_manifest.json", instrument_manifest)
    _atomic_jsonl(artifacts_dir / "relation_cache_records.jsonl", cache_records)
    _atomic_jsonl(artifacts_dir / "graphs.jsonl", graph_rows)
    _atomic_jsonl(artifacts_dir / "intent_proposals.jsonl", intent_rows)
    _atomic_jsonl(output_path, prepared_rows)
    file_hashes = {
        "instrument_manifest.json": _file_sha256(
            artifacts_dir / "instrument_manifest.json"
        ),
        "relation_cache_records.jsonl": _file_sha256(
            artifacts_dir / "relation_cache_records.jsonl"
        ),
        "graphs.jsonl": _file_sha256(artifacts_dir / "graphs.jsonl"),
        "intent_proposals.jsonl": _file_sha256(
            artifacts_dir / "intent_proposals.jsonl"
        ),
        "prepared_cases.jsonl": _file_sha256(output_path),
    }
    relation_counts = Counter(
        edge.relation for graph in graphs.values() for edge in graph.edges
    )
    actionability_counts = Counter(
        edge.actionability.mode.value
        for graph in graphs.values()
        for edge in graph.edges
    )
    manifest: dict[str, object] = {
        "schema_version": PREPARATION_SCHEMA_VERSION,
        "build_status": "gpu_input_ready",
        "dataset_sha256": dataset_sha256,
        "dataset_manifest_file_sha256": _file_sha256(
            dataset_dir / "dataset_manifest.json"
        ),
        "instrument_manifest_sha256": instrument_hash,
        "relation_cache_sha256": relation_cache_sha256,
        "graph_stream_sha256": file_hashes["graphs.jsonl"],
        "intent_stream_sha256": file_hashes["intent_proposals.jsonl"],
        "prepared_stream_sha256": file_hashes["prepared_cases.jsonl"],
        "file_sha256": file_hashes,
        "candidate_budget": candidate_budget,
        "chain_pair_budget": 2 if candidate_budget > 1 else 0,
        "selection_mode": "full" if limit is None else "balanced_probe_set_subset",
        "selection_limit": limit,
        "source_case_count": len(runtime_rows),
        "eligible_case_count": len(eligible_case_ids),
        "excluded_no_relation_pair_count": len(runtime_rows) - len(eligible_case_ids),
        "selected_case_count": len(selected_case_ids),
        "selected_case_ids": selected_case_ids,
        "selected_case_ids_sha256": canonical_sha256(selected_case_ids),
        "selected_probe_set_counts": dict(
            sorted(
                Counter(str(row["probe_set"]) for row in selected_assignments).items()
            )
        ),
        "relation_request_count": len(selected_requests),
        "unique_relation_cache_record_count": len(cache_records),
        "relation_model_call_count": relation_calls,
        "relation_uncertain_count": uncertain_count,
        "relation_uncertain_rate": uncertain_count / len(selected_requests),
        "max_uncertain_rate": float(max_uncertain_rate),
        "relation_counts": dict(sorted(relation_counts.items())),
        "actionability_counts": dict(sorted(actionability_counts.items())),
        "proposer_model_id": proposer_model_id,
        "proposer_model_sha256": proposer_model_hash,
        "proposer_version": INTENT_PROPOSER_VERSION,
        "proposer_prompt_template_sha256": INTENT_PROMPT_TEMPLATE_SHA256,
        "proposer_model_call_count": proposer_calls,
        "proposer_cache_hit_count": proposer_cache_hits,
        "max_proposer_retries": max_proposer_retries,
        "runtime_uses_gold": False,
        "relation_instrument_uses_gold": False,
        "intent_proposer_uses_gold": False,
        "shadow_join_after_graph_and_intent_freeze": True,
    }
    manifest["preparation_manifest_sha256"] = canonical_sha256(manifest)
    _atomic_json(manifest_path, manifest)
    _append_progress(
        progress_path,
        {
            "event": "preparation_completed",
            "selected_cases": len(selected_case_ids),
            "prepared_stream_sha256": file_hashes["prepared_cases.jsonl"],
            "preparation_manifest_sha256": manifest["preparation_manifest_sha256"],
        },
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--progress", type=Path)
    parser.add_argument("--candidate-budget", type=int, default=4)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--max-uncertain-rate", type=float, default=DEFAULT_MAX_UNCERTAIN_RATE
    )
    parser.add_argument(
        "--max-proposer-retries", type=int, default=DEFAULT_PROPOSER_RETRIES
    )
    parser.add_argument("--instrument-model-hash")
    parser.add_argument("--proposer-model-hash")
    args = parser.parse_args(argv)
    try:
        instrument_config = LLMClientConfig.for_role("judge")
        proposer_config = LLMClientConfig.for_role("answer")
        instrument_hash = args.instrument_model_hash or _model_config_hash(
            instrument_config, SLOT_RELATION_VERSION
        )
        proposer_hash = args.proposer_model_hash or _model_config_hash(
            proposer_config, INTENT_PROPOSER_VERSION
        )
        manifest = prepare_live_cases(
            dataset_dir=args.dataset_dir,
            output_path=args.output,
            artifacts_dir=args.artifacts_dir,
            cache_path=args.cache,
            relation_judge=LLMClient(instrument_config),
            intent_judge=LLMClient(proposer_config),
            instrument_model_id=instrument_config.model,
            instrument_model_hash=instrument_hash,
            proposer_model_id=proposer_config.model,
            proposer_model_hash=proposer_hash,
            candidate_budget=args.candidate_budget,
            max_uncertain_rate=args.max_uncertain_rate,
            max_proposer_retries=args.max_proposer_retries,
            limit=args.limit,
            progress_path=args.progress,
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"REFUSE: {type(error).__name__}: {error}")
        return 2
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_MAX_UNCERTAIN_RATE",
    "INTENT_PROPOSER_VERSION",
    "IntentProposalCache",
    "PREPARATION_SCHEMA_VERSION",
    "parse_intent_proposals",
    "intent_proposal_cache_key",
    "prepare_live_cases",
    "proposer_surface",
]
