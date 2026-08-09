#!/usr/bin/env python3
"""Freeze relation measurements, graphs, intents, and GPU-ready V4 cases.

The relation instrument and proposer receive deployment-visible surfaces only.
Shadow labels are joined after every graph and intent has been content-bound.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
from enum import Enum
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
    RELATION_RESPONSE_SCHEMA_SHA256,
    SLOT_RELATION_VERSION,
    RelationType,
    RelationVerdict,
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


PREPARATION_SCHEMA_VERSION = "cmd-v4-live-input-preparation-manifest-v2"
INSTRUMENT_MANIFEST_SCHEMA_VERSION = "cmd-v4-relation-instrument-manifest-v2"
CACHE_RECORDS_SCHEMA_VERSION = "cmd-v4-relation-cache-records-v2"
RELATION_RESPONSE_ROW_VERSION = "cmd-v4-relation-response-row-v1"
RELATION_MEASUREMENT_REPORT_VERSION = "cmd-v4-relation-measurement-report-v1"
INTENT_PROPOSER_VERSION = "cmd-v4-llm-intent-proposer-v2-structured-json"
INTENT_PROPOSAL_ROW_VERSION = "cmd-v4-intent-proposal-row-v1"
INTENT_RESPONSE_ROW_VERSION = "cmd-v4-intent-response-row-v1"
INTENT_PROPOSAL_REPORT_VERSION = "cmd-v4-intent-proposal-report-v1"
GRAPH_ROW_VERSION = "cmd-v4-frozen-graph-row-v1"
DEFAULT_MAX_UNCERTAIN_RATE = 0.05
DEFAULT_RELATION_ATTEMPTS = 3
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
_SAFE_INTENT_EFFECTS = ("abstain", "annotate_conflict", "verify")
_TARGET_ONLY_INTENT_EFFECTS = ("demote", "suppress")
_SINGLE_JSON_FENCE = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n(?P<body>.*?)\r?\n```[ \t]*\Z",
    re.IGNORECASE | re.DOTALL,
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


class IntentProposalReason(str, Enum):
    ACCEPTED = "accepted"
    ACCEPTED_FENCED_JSON = "accepted_fenced_json"
    CACHE_REPLAY = "cache_replay"
    MALFORMED_JSON = "malformed_json"
    INVALID_SCHEMA = "invalid_schema"
    COMPILER_REJECTED = "compiler_rejected"
    TRANSPORT_ERROR = "transport_error"


class IntentProposalError(ValueError):
    def __init__(self, reason_code: IntentProposalReason, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


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


def _response_records(
    cache_key: str,
    verdict: RelationVerdict,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for attempt in verdict.attempts:
        payload: dict[str, object] = {
            "schema_version": RELATION_RESPONSE_ROW_VERSION,
            "cache_key": cache_key,
            "attempt_index": attempt.attempt_index,
            "reason_code": attempt.reason_code.value,
            "raw_response": attempt.raw_response,
            "raw_response_sha256": attempt.raw_response_sha256,
            "structured_output_used": attempt.structured_output_used,
            "prompt_sha256": verdict.prompt_sha256,
            "parser_version": verdict.parser_version,
            "instrument_version": SLOT_RELATION_VERSION,
        }
        rows.append(
            {
                **payload,
                "response_record_sha256": canonical_sha256(payload),
            }
        )
    return rows


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
    max_relation_attempts: int,
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
        "max_relation_attempts": max_relation_attempts,
        "response_schema_sha256": RELATION_RESPONSE_SCHEMA_SHA256,
        "structured_output_required": True,
        "raw_response_audit": True,
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
    max_relation_attempts: int,
    progress_path: Path | None,
) -> tuple[
    dict[str, RelationMeasurementBinding],
    list[dict[str, object]],
    list[dict[str, object]],
    int,
    int,
]:
    by_request: dict[str, RelationMeasurementBinding] = {}
    selected_cache_records: dict[str, dict[str, object]] = {}
    selected_response_records: dict[str, dict[str, object]] = {}
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
        cached_before = cache.get(key) is not None
        verdict = judge_relation(
            request["left_text"],
            request["right_text"],
            judge=judge,
            cache=cache,
            model_id=model_id,
            model_config_hash=model_hash,
            max_attempts=max_relation_attempts,
        )
        if not cached_before:
            model_calls += len(verdict.attempts)
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
        for response_record in _response_records(measurement.cache_key, verdict):
            selected_response_records[response_record["response_record_sha256"]] = (
                response_record
            )
        _append_progress(
            progress_path,
            {
                "event": "relation_measured",
                "completed": index,
                "total": total,
                "request_id": request_id,
                "cache_key": measurement.cache_key,
                "relation": measurement.relation,
                "reason_code": verdict.reason_code.value,
                "attempt_count": len(verdict.attempts),
                "cache_hit": cached_before,
            },
        )
    return (
        by_request,
        [selected_cache_records[key] for key in sorted(selected_cache_records)],
        [selected_response_records[key] for key in sorted(selected_response_records)],
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


def intent_response_schema(
    surface: Mapping[str, object],
) -> dict[str, object]:
    """Build a closed schema that admits only intents the typed compiler can use."""
    proposals_needed = surface.get("proposals_needed")
    edges = surface.get("edges")
    items = surface.get("retrieved_items")
    if (
        isinstance(proposals_needed, bool)
        or not isinstance(proposals_needed, int)
        or proposals_needed < 1
        or not isinstance(edges, list)
        or not edges
        or not isinstance(items, list)
        or not items
    ):
        raise ValueError("proposer surface cannot define a response schema")
    item_ids = sorted(
        str(item["item_id"])
        for item in items
        if isinstance(item, Mapping) and isinstance(item.get("item_id"), str)
    )
    if len(item_ids) != len(items) or len(set(item_ids)) != len(item_ids):
        raise ValueError("proposer surface carries invalid identifiers")

    def branch(
        *,
        edge_id: str,
        effects: Sequence[str],
        target_item_id: str | None,
        replacement_item_id: str | None,
    ) -> dict[str, object]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": sorted(_PROPOSAL_KEYS),
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "minLength": 3,
                    "maxLength": 128,
                },
                "relation_edge_id": {"const": edge_id},
                "effect": {"type": "string", "enum": list(effects)},
                "target_item_id": {"const": target_item_id},
                "replacement_item_id": {"const": replacement_item_id},
            },
        }

    proposal_branches: list[dict[str, object]] = []
    edge_ids: set[str] = set()
    for edge in edges:
        if not isinstance(edge, Mapping) or not isinstance(edge.get("edge_id"), str):
            raise ValueError("proposer surface carries invalid edge identifiers")
        edge_id = edge["edge_id"]
        if not edge_id or edge_id in edge_ids:
            raise ValueError("proposer surface edge identifiers must be unique")
        edge_ids.add(edge_id)
        actionability = edge.get("actionability")
        if not isinstance(actionability, Mapping):
            raise ValueError("proposer surface edge lacks actionability")
        mode = actionability.get("mode")
        target = actionability.get("target_item_id")
        survivor = actionability.get("survivor_item_id")
        if mode not in {item.value for item in ActionMode}:
            raise ValueError("proposer surface has unknown actionability mode")
        proposal_branches.append(
            branch(
                edge_id=edge_id,
                effects=_SAFE_INTENT_EFFECTS,
                target_item_id=None,
                replacement_item_id=None,
            )
        )
        if mode == ActionMode.DESTRUCTIVE.value:
            if (
                not isinstance(target, str)
                or not isinstance(survivor, str)
                or target not in item_ids
                or survivor not in item_ids
                or target == survivor
            ):
                raise ValueError("destructive actionability has invalid target binding")
            proposal_branches.extend(
                (
                    branch(
                        edge_id=edge_id,
                        effects=_TARGET_ONLY_INTENT_EFFECTS,
                        target_item_id=target,
                        replacement_item_id=None,
                    ),
                    branch(
                        edge_id=edge_id,
                        effects=("replace",),
                        target_item_id=target,
                        replacement_item_id=survivor,
                    ),
                )
            )
        elif target is not None or survivor is not None:
            raise ValueError("non-destructive actionability cannot bind item targets")

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["proposals"],
        "properties": {
            "proposals": {
                "type": "array",
                "minItems": proposals_needed,
                "maxItems": proposals_needed,
                "uniqueItems": True,
                "items": {"oneOf": proposal_branches},
            }
        },
    }


def _parse_intent_response_object(
    response: str,
) -> tuple[Mapping[str, object], IntentProposalReason]:
    if not isinstance(response, str):
        raise IntentProposalError(
            IntentProposalReason.MALFORMED_JSON,
            "proposer response must be text",
        )
    fenced = _SINGLE_JSON_FENCE.fullmatch(response.strip())
    payload = fenced.group("body") if fenced else response
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        raise IntentProposalError(
            IntentProposalReason.MALFORMED_JSON,
            "proposer response is not one JSON object",
        ) from error
    if not isinstance(value, Mapping):
        raise IntentProposalError(
            IntentProposalReason.INVALID_SCHEMA,
            "proposer response must be a JSON object",
        )
    return value, (
        IntentProposalReason.ACCEPTED_FENCED_JSON
        if fenced
        else IntentProposalReason.ACCEPTED
    )


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
    value, _ = _parse_intent_response_object(response)
    if set(value) != {"proposals"}:
        raise IntentProposalError(
            IntentProposalReason.INVALID_SCHEMA,
            "proposer response must be a closed proposals object",
        )
    proposals = value["proposals"]
    if not isinstance(proposals, list) or len(proposals) != proposals_needed:
        raise IntentProposalError(
            IntentProposalReason.INVALID_SCHEMA,
            "proposer response does not match frozen proposal budget",
        )
    edges = {edge.edge_id: edge for edge in graph.edges}
    intents: list[RepairIntent] = []
    for proposal in proposals:
        if not isinstance(proposal, Mapping) or set(proposal) != _PROPOSAL_KEYS:
            raise IntentProposalError(
                IntentProposalReason.INVALID_SCHEMA,
                "intent proposal mapping is not closed",
            )
        edge = edges.get(proposal["relation_edge_id"])
        if edge is None:
            raise IntentProposalError(
                IntentProposalReason.INVALID_SCHEMA,
                "intent proposal references an absent graph edge",
            )
        try:
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
        except (KeyError, TypeError, ValueError) as error:
            raise IntentProposalError(
                IntentProposalReason.COMPILER_REJECTED,
                "intent proposal failed typed compilation",
            ) from error
        intents.append(intent)
    if len({intent.intent_id for intent in intents}) != len(intents):
        raise IntentProposalError(
            IntentProposalReason.INVALID_SCHEMA,
            "intent proposer emitted duplicate concrete intents",
        )
    return tuple(intents)


def _intent_response_record(
    *,
    case_id: str,
    cache_key: str,
    attempt_index: int,
    reason_code: IntentProposalReason,
    raw_response: str | None,
    structured_output_used: bool,
    response_schema_sha256: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": INTENT_RESPONSE_ROW_VERSION,
        "case_id": case_id,
        "proposer_cache_key": cache_key,
        "attempt_index": attempt_index,
        "reason_code": reason_code.value,
        "raw_response": raw_response,
        "raw_response_sha256": (
            hashlib.sha256(raw_response.encode("utf-8")).hexdigest()
            if raw_response is not None
            else None
        ),
        "structured_output_used": structured_output_used,
        "prompt_template_sha256": INTENT_PROMPT_TEMPLATE_SHA256,
        "proposer_version": INTENT_PROPOSER_VERSION,
        "response_schema_sha256": response_schema_sha256,
    }
    return {
        **payload,
        "response_record_sha256": canonical_sha256(payload),
    }


def _propose_intents(
    runtime_mapping: Mapping[str, object],
    graph: FrozenRelationGraph,
    *,
    judge: TextGenerator,
    candidate_budget: int,
    proposer_model_hash: str,
    max_retries: int,
    cache_path: Path,
    attempt_records: list[dict[str, object]],
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
    response_schema = intent_response_schema(surface)
    response_schema_sha256 = canonical_sha256(response_schema)
    input_sha256 = canonical_sha256(surface)
    cache_key = intent_proposal_cache_key(
        surface,
        proposer_model_hash=proposer_model_hash,
        proposals_needed=proposals_needed,
    )
    with IntentProposalCache(cache_path) as cache:
        cached_response = cache.get(cache_key)
    if cached_response is not None:
        cached_raw = _canonical_bytes(cached_response).decode("utf-8")
        proposed = parse_intent_proposals(
            cached_raw,
            graph=graph,
            proposals_needed=proposals_needed,
            proposer_model_hash=proposer_model_hash,
        )
        intents = (baseline, *proposed)
        if len({intent.intent_id for intent in intents}) != candidate_budget:
            raise ValueError("cached baseline and proposer intents are not unique")
        attempt_records.append(
            _intent_response_record(
                case_id=graph.case_id,
                cache_key=cache_key,
                attempt_index=0,
                reason_code=IntentProposalReason.CACHE_REPLAY,
                raw_response=cached_raw,
                structured_output_used=True,
                response_schema_sha256=response_schema_sha256,
            )
        )
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
    structured_generate = getattr(judge, "generate_json", None)
    if not callable(structured_generate):
        raise ValueError("V4 intent proposer must support strict JSON Schema output")
    prompt = f"{INTENT_PROMPT_TEMPLATE}\nPROPOSER_INPUT:\n{_canonical_bytes(surface).decode('utf-8')}"
    last_reason = IntentProposalReason.TRANSPORT_ERROR
    for attempt in range(1, max_retries + 2):
        try:
            response = structured_generate(
                prompt,
                schema=response_schema,
                schema_name="repair_intents",
            )
        except Exception:
            last_reason = IntentProposalReason.TRANSPORT_ERROR
            attempt_records.append(
                _intent_response_record(
                    case_id=graph.case_id,
                    cache_key=cache_key,
                    attempt_index=attempt,
                    reason_code=last_reason,
                    raw_response=None,
                    structured_output_used=True,
                    response_schema_sha256=response_schema_sha256,
                )
            )
            continue
        try:
            proposed = parse_intent_proposals(
                response,
                graph=graph,
                proposals_needed=proposals_needed,
                proposer_model_hash=proposer_model_hash,
            )
            intents = (baseline, *proposed)
            if len({intent.intent_id for intent in intents}) != candidate_budget:
                raise IntentProposalError(
                    IntentProposalReason.COMPILER_REJECTED,
                    "baseline and proposer intents are not unique",
                )
            parsed_response, accepted_reason = _parse_intent_response_object(response)
            attempt_records.append(
                _intent_response_record(
                    case_id=graph.case_id,
                    cache_key=cache_key,
                    attempt_index=attempt,
                    reason_code=accepted_reason,
                    raw_response=response,
                    structured_output_used=True,
                    response_schema_sha256=response_schema_sha256,
                )
            )
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
        except IntentProposalError as error:
            last_reason = error.reason_code
            attempt_records.append(
                _intent_response_record(
                    case_id=graph.case_id,
                    cache_key=cache_key,
                    attempt_index=attempt,
                    reason_code=last_reason,
                    raw_response=response,
                    structured_output_used=True,
                    response_schema_sha256=response_schema_sha256,
                )
            )
    raise ValueError(
        f"intent proposer exhausted closed-schema retries: {last_reason.value}"
    )


def _write_intent_audit(
    *,
    artifacts_dir: Path,
    records: Sequence[Mapping[str, object]],
    decision: str,
    expected_case_count: int,
    failure_reason: str | None,
) -> dict[str, object]:
    ordered = sorted(
        (dict(row) for row in records),
        key=lambda row: (
            str(row["case_id"]),
            str(row["proposer_cache_key"]),
            int(row["attempt_index"]),
        ),
    )
    positive_attempts_by_cache = Counter(
        str(row["proposer_cache_key"])
        for row in ordered
        if int(row["attempt_index"]) > 0
    )
    retry_count = sum(
        max(0, count - 1) for count in positive_attempts_by_cache.values()
    )
    reason_counts = dict(
        sorted(Counter(str(row["reason_code"]) for row in ordered).items())
    )
    report_body: dict[str, object] = {
        "schema_version": INTENT_PROPOSAL_REPORT_VERSION,
        "decision": decision,
        "proposer_version": INTENT_PROPOSER_VERSION,
        "prompt_template_sha256": INTENT_PROMPT_TEMPLATE_SHA256,
        "expected_case_count": expected_case_count,
        "audited_case_count": len({str(row["case_id"]) for row in ordered}),
        "attempt_record_count": len(ordered),
        "model_call_count": sum(int(row["attempt_index"]) > 0 for row in ordered),
        "retry_count": retry_count,
        "reason_counts": reason_counts,
        "response_stream_sha256": canonical_sha256(ordered),
        "failure_reason": failure_reason,
    }
    report = {
        **report_body,
        "report_sha256": canonical_sha256(report_body),
    }
    _atomic_jsonl(artifacts_dir / "intent_responses.jsonl", ordered)
    _atomic_json(artifacts_dir / "intent_proposal_report.json", report)
    return report


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
    max_relation_attempts: int = DEFAULT_RELATION_ATTEMPTS,
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
    if (
        isinstance(max_relation_attempts, bool)
        or not isinstance(max_relation_attempts, int)
        or max_relation_attempts < 1
    ):
        raise ValueError("max_relation_attempts must be a positive integer")
    if not callable(getattr(relation_judge, "generate_json", None)):
        raise ValueError("V4 relation judge must support strict JSON Schema output")
    if candidate_budget > 1 and not callable(
        getattr(intent_judge, "generate_json", None)
    ):
        raise ValueError("V4 intent proposer must support strict JSON Schema output")
    manifest_path = artifacts_dir / "preparation_manifest.json"
    reserved = (output_path, manifest_path)
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
        max_relation_attempts=max_relation_attempts,
    )
    instrument_hash = instrument_manifest["instrument_manifest_sha256"]
    with RelationCache(cache_path) as cache:
        (
            measurements,
            cache_records,
            response_records,
            relation_calls,
            uncertain_count,
        ) = _measure_relations(
            selected_requests,
            judge=relation_judge,
            cache=cache,
            instrument_manifest_sha256=instrument_hash,
            model_id=instrument_model_id,
            model_hash=instrument_model_hash,
            max_relation_attempts=max_relation_attempts,
            progress_path=progress_path,
        )
    relation_uncertain_rate = uncertain_count / len(selected_requests)
    relation_reason_counts = dict(
        sorted(Counter(str(row["reason_code"]) for row in response_records).items())
    )
    attempts_by_cache = Counter(str(row["cache_key"]) for row in response_records)
    relation_retry_count = sum(
        max(0, attempt_count - 1) for attempt_count in attempts_by_cache.values()
    )
    response_stream_sha256 = canonical_sha256(response_records)
    measurement_report_body: dict[str, object] = {
        "schema_version": RELATION_MEASUREMENT_REPORT_VERSION,
        "decision": (
            "PASS" if relation_uncertain_rate <= float(max_uncertain_rate) else "REFUSE"
        ),
        "instrument_manifest_sha256": instrument_hash,
        "relation_request_count": len(selected_requests),
        "relation_attempt_count": len(response_records),
        "relation_retry_count": relation_retry_count,
        "relation_uncertain_count": uncertain_count,
        "relation_uncertain_rate": relation_uncertain_rate,
        "max_uncertain_rate": float(max_uncertain_rate),
        "reason_counts": relation_reason_counts,
        "response_stream_sha256": response_stream_sha256,
    }
    measurement_report = {
        **measurement_report_body,
        "report_sha256": canonical_sha256(measurement_report_body),
    }
    _atomic_json(artifacts_dir / "instrument_manifest.json", instrument_manifest)
    _atomic_jsonl(artifacts_dir / "relation_cache_records.jsonl", cache_records)
    _atomic_jsonl(artifacts_dir / "relation_responses.jsonl", response_records)
    _atomic_json(
        artifacts_dir / "relation_measurement_report.json",
        measurement_report,
    )
    if relation_uncertain_rate > float(max_uncertain_rate):
        _append_progress(
            progress_path,
            {
                "event": "relation_gate_refused",
                "relation_uncertain_count": uncertain_count,
                "relation_uncertain_rate": relation_uncertain_rate,
                "max_uncertain_rate": float(max_uncertain_rate),
                "reason_counts": relation_reason_counts,
                "report_sha256": measurement_report["report_sha256"],
            },
        )
        raise ValueError(
            "relation uncertainty rate exceeds frozen cutoff: "
            f"{relation_uncertain_rate:.6f} > {float(max_uncertain_rate):.6f}"
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
    intent_response_records: list[dict[str, object]] = []
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
        try:
            intents, proposal_binding, calls = _propose_intents(
                runtime_row["runtime_case"],
                graph,
                judge=intent_judge,
                candidate_budget=candidate_budget,
                proposer_model_hash=proposer_model_hash,
                max_retries=max_proposer_retries,
                cache_path=cache_path,
                attempt_records=intent_response_records,
            )
        except Exception as error:
            failed_report = _write_intent_audit(
                artifacts_dir=artifacts_dir,
                records=intent_response_records,
                decision="REFUSE",
                expected_case_count=len(selected_assignments),
                failure_reason=f"{type(error).__name__}:{error}",
            )
            _append_progress(
                progress_path,
                {
                    "event": "intent_gate_refused",
                    "case_id": case_id,
                    "reason": failed_report["failure_reason"],
                    "report_sha256": failed_report["report_sha256"],
                },
            )
            raise
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

    intent_proposal_report = _write_intent_audit(
        artifacts_dir=artifacts_dir,
        records=intent_response_records,
        decision="PASS",
        expected_case_count=len(selected_assignments),
        failure_reason=None,
    )

    _atomic_json(artifacts_dir / "instrument_manifest.json", instrument_manifest)
    _atomic_jsonl(artifacts_dir / "relation_cache_records.jsonl", cache_records)
    _atomic_jsonl(artifacts_dir / "relation_responses.jsonl", response_records)
    _atomic_json(
        artifacts_dir / "relation_measurement_report.json",
        measurement_report,
    )
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
        "relation_responses.jsonl": _file_sha256(
            artifacts_dir / "relation_responses.jsonl"
        ),
        "relation_measurement_report.json": _file_sha256(
            artifacts_dir / "relation_measurement_report.json"
        ),
        "graphs.jsonl": _file_sha256(artifacts_dir / "graphs.jsonl"),
        "intent_proposals.jsonl": _file_sha256(
            artifacts_dir / "intent_proposals.jsonl"
        ),
        "intent_responses.jsonl": _file_sha256(
            artifacts_dir / "intent_responses.jsonl"
        ),
        "intent_proposal_report.json": _file_sha256(
            artifacts_dir / "intent_proposal_report.json"
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
        "relation_response_stream_sha256": file_hashes["relation_responses.jsonl"],
        "relation_measurement_report_sha256": file_hashes[
            "relation_measurement_report.json"
        ],
        "graph_stream_sha256": file_hashes["graphs.jsonl"],
        "intent_stream_sha256": file_hashes["intent_proposals.jsonl"],
        "intent_response_stream_sha256": file_hashes["intent_responses.jsonl"],
        "intent_proposal_report_sha256": file_hashes["intent_proposal_report.json"],
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
        "relation_attempt_count": len(response_records),
        "relation_retry_count": relation_retry_count,
        "relation_reason_counts": relation_reason_counts,
        "relation_uncertain_count": uncertain_count,
        "relation_uncertain_rate": relation_uncertain_rate,
        "max_uncertain_rate": float(max_uncertain_rate),
        "max_relation_attempts": max_relation_attempts,
        "relation_counts": dict(sorted(relation_counts.items())),
        "actionability_counts": dict(sorted(actionability_counts.items())),
        "proposer_model_id": proposer_model_id,
        "proposer_model_sha256": proposer_model_hash,
        "proposer_version": INTENT_PROPOSER_VERSION,
        "proposer_prompt_template_sha256": INTENT_PROMPT_TEMPLATE_SHA256,
        "proposer_model_call_count": proposer_calls,
        "proposer_attempt_count": intent_proposal_report["attempt_record_count"],
        "proposer_retry_count": intent_proposal_report["retry_count"],
        "proposer_reason_counts": intent_proposal_report["reason_counts"],
        "intent_response_schema_version": INTENT_RESPONSE_ROW_VERSION,
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
        "--max-relation-attempts",
        type=int,
        default=DEFAULT_RELATION_ATTEMPTS,
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
            max_relation_attempts=args.max_relation_attempts,
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
    "DEFAULT_RELATION_ATTEMPTS",
    "INTENT_PROPOSER_VERSION",
    "INTENT_PROPOSAL_REPORT_VERSION",
    "INTENT_RESPONSE_ROW_VERSION",
    "IntentProposalReason",
    "IntentProposalCache",
    "PREPARATION_SCHEMA_VERSION",
    "RELATION_MEASUREMENT_REPORT_VERSION",
    "RELATION_RESPONSE_ROW_VERSION",
    "parse_intent_proposals",
    "intent_proposal_cache_key",
    "intent_response_schema",
    "prepare_live_cases",
    "proposer_surface",
]
