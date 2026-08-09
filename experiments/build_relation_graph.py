"""Build replayable graph shards from validated F1 and frozen cache records."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from cmd_audit.counterfactual.relation_graph import (
    FrozenRelationEdge,
    FrozenRelationGraph,
    SUCCESSOR_PROTOCOL_ID,
    canonical_sha256,
)
from cmd_audit.eval.state_intent import RuntimeEvent, RuntimeMemoryItem, RuntimeRepairCase
from cmd_audit.eval.successor_protocol_freeze import require_validated_f1


def _closed(value: object, keys: frozenset[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{name} must have exactly {sorted(keys)}")
    return value


def _runtime_case(value: object) -> RuntimeRepairCase:
    mapping = _closed(
        value,
        frozenset(
            {
                "case_id",
                "family_id",
                "query",
                "token_budget",
                "runtime_surface",
                "items",
                "raw_events",
            }
        ),
        "runtime case",
    )
    items: list[RuntimeMemoryItem] = []
    for raw in mapping["items"]:
        item = _closed(
            raw,
            frozenset(
                {
                    "item_id",
                    "text",
                    "source_event_ids",
                    "store",
                    "rank",
                    "retrieved",
                }
            ),
            "runtime item",
        )
        items.append(
            RuntimeMemoryItem(
                item_id=item["item_id"],
                text=item["text"],
                source_event_ids=tuple(item["source_event_ids"]),
                store=item["store"],
                rank=item["rank"],
                retrieved=item["retrieved"],
            )
        )
    if len({item.item_id for item in items}) != len(items):
        raise ValueError("runtime case contains duplicate item IDs")
    events = []
    for raw in mapping["raw_events"]:
        event = _closed(raw, frozenset({"event_id", "text"}), "runtime event")
        events.append(RuntimeEvent(event_id=event["event_id"], text=event["text"]))
    return RuntimeRepairCase(
        case_id=mapping["case_id"],
        family_id=mapping["family_id"],
        query=mapping["query"],
        token_budget=mapping["token_budget"],
        runtime_surface=mapping["runtime_surface"],
        items=tuple(items),
        raw_events=tuple(events),
    )


def build_graphs(
    payload: Mapping[str, Any],
    *,
    validated_protocol_manifest_sha256: str,
    protocol_validation_file_sha256: str,
) -> list[dict[str, object]]:
    _closed(
        payload,
        frozenset(
            {
                "schema_version",
                "protocol_id",
                "protocol_manifest_sha256",
                "protocol_validation_file_sha256",
                "instrument_manifest_sha256",
                "cache_manifest_sha256",
                "llm_calls",
                "cache_miss_policy",
                "cache_records",
                "cases",
            }
        ),
        "graph build input",
    )
    if (
        payload["protocol_id"] != SUCCESSOR_PROTOCOL_ID
        or payload["protocol_manifest_sha256"]
        != validated_protocol_manifest_sha256
        or payload["protocol_validation_file_sha256"]
        != protocol_validation_file_sha256
        or payload["llm_calls"] != 0
        or payload["cache_miss_policy"] != "refuse"
    ):
        raise ValueError("graph build is detached from validated zero-live-call F1")
    cache_records = payload["cache_records"]
    if not isinstance(cache_records, list):
        raise ValueError("cache_records must be a list")
    cache_hashes: set[str] = set()
    for record in cache_records:
        _closed(
            record,
            frozenset(
                {
                    "cache_key",
                    "canonical_left",
                    "canonical_right",
                    "prompt_sha256",
                    "parser_version",
                    "model_id",
                    "model_config_hash",
                    "normalization_version",
                    "instrument_version",
                    "verdict",
                }
            ),
            "cache record",
        )
        digest = canonical_sha256(record)
        if digest in cache_hashes:
            raise ValueError("duplicate cache record")
        cache_hashes.add(digest)
    cases = payload["cases"]
    if not isinstance(cases, list):
        raise ValueError("cases must be a list")
    output: list[dict[str, object]] = []
    seen_cases: set[str] = set()
    for raw in cases:
        row = _closed(
            raw,
            frozenset({"runtime_case", "item_ids", "edges"}),
            "graph case row",
        )
        case = _runtime_case(row["runtime_case"])
        if case.case_id in seen_cases:
            raise ValueError("duplicate graph case")
        seen_cases.add(case.case_id)
        if not isinstance(row["item_ids"], list):
            raise ValueError("item_ids must be a list")
        edges = tuple(FrozenRelationEdge.from_mapping(edge) for edge in row["edges"])
        if any(edge.measurement.cache_record_sha256 not in cache_hashes for edge in edges):
            raise ValueError("edge refers to absent cache record")
        graph = FrozenRelationGraph.build(
            case=case,
            item_ids=tuple(row["item_ids"]),
            protocol_manifest_sha256=validated_protocol_manifest_sha256,
            instrument_manifest_sha256=payload["instrument_manifest_sha256"],
            cache_manifest_sha256=payload["cache_manifest_sha256"],
            edges=edges,
        )
        output.append(graph.as_mapping())
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol-freeze", type=Path, required=True)
    parser.add_argument("--protocol-validation", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.protocol_freeze.read_text(encoding="utf-8"))
        validation = json.loads(args.protocol_validation.read_text(encoding="utf-8"))
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        if not all(isinstance(value, dict) for value in (manifest, validation, payload)):
            raise ValueError("inputs must be JSON objects")
        graphs = build_graphs(
            payload,
            validated_protocol_manifest_sha256=require_validated_f1(
                manifest, validation
            ),
            protocol_validation_file_sha256=hashlib.sha256(
                args.protocol_validation.read_bytes()
            ).hexdigest(),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            "".join(
                json.dumps(graph, ensure_ascii=False, sort_keys=True) + "\n"
                for graph in graphs
            ),
            encoding="utf-8",
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"REFUSE: {type(error).__name__}: {error}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
