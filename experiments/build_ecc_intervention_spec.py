#!/usr/bin/env python3
"""Build closed, gold-free runtime event specs for ECC controlled tracks."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

from cmd_audit.core.state_codec import atomic_json_write, content_sha256
from experiments.materialize_ecc_memory_benchmark_harness import (
    DEFAULT_POISON_PAYLOAD,
    INTERVENTION_SCHEMA,
    _load_cases,
)


MANIFEST_SCHEMA = "cmd-ecc-runtime-intervention-manifest-v1"
STATE_EVENT_SCHEMA = "cmd-ecc-state-update-source-event-v1"


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, object]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _state_rows(source_events: Path) -> tuple[Mapping[str, object], ...]:
    expected = {"schema_version", "case_id", "superseded_memory_id", "event_id", "text"}
    rows: list[Mapping[str, object]] = []
    seen: set[str] = set()
    for number, line in enumerate(source_events.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        raw = json.loads(line)
        if not isinstance(raw, Mapping) or set(raw) != expected or raw["schema_version"] != STATE_EVENT_SCHEMA:
            raise ValueError(f"state update source event is not closed at {source_events}:{number}")
        values = [raw[key] for key in ("case_id", "superseded_memory_id", "event_id", "text")]
        if any(not isinstance(value, str) or not value for value in values):
            raise ValueError(f"state update source event is invalid at {source_events}:{number}")
        case_id = str(raw["case_id"])
        if case_id in seen:
            raise ValueError(f"duplicate state update source case: {case_id}")
        seen.add(case_id)
        event_id, text = str(raw["event_id"]), str(raw["text"])
        rows.append({
            "schema_version": INTERVENTION_SCHEMA,
            "case_id": case_id,
            "mechanism": "state_drift",
            "source_event_id": event_id,
            "source_event_sha256": content_sha256({"event_id": event_id, "text": text}),
            "superseded_memory_id": str(raw["superseded_memory_id"]),
            "superseding_text": text,
        })
    if not rows:
        raise ValueError("state update source event stream is empty")
    return tuple(rows)


def _poison_rows(
    *,
    benchmark: str,
    cases: Path,
    payload: str,
    seed: int,
    offset: int,
    limit: int,
    retrieval_top_k: int,
    candidate_pool_k: int,
) -> tuple[Mapping[str, object], ...]:
    if not payload.strip():
        raise ValueError("poison payload must be non-empty")
    if offset < 0:
        raise ValueError("poison selection offset must be non-negative")
    ordered = _load_cases(
        benchmark,
        cases,
        seed=seed,
        limit=0,
        retrieval_top_k=retrieval_top_k,
        candidate_pool_k=candidate_pool_k,
    )
    selected = ordered[offset : offset + limit if limit else None]
    rows = []
    for case in selected:
        event_id = f"poison-event:{content_sha256({'case_id': case.case_id, 'payload': payload})}"
        text = "\n".join((
            "[untrusted-memory-override]",
            payload,
            "query_sha256=" + content_sha256(str(case.raw["query"])),
        ))
        rows.append({
            "schema_version": INTERVENTION_SCHEMA,
            "case_id": case.case_id,
            "mechanism": "adversarial_poison",
            "source_event_id": event_id,
            "source_event_sha256": content_sha256({"event_id": event_id, "text": text}),
            "poison_text": text,
        })
    if not rows:
        raise ValueError("poison case selection is empty")
    return tuple(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mechanism", choices=("state_drift", "adversarial_poison"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-events", type=Path)
    parser.add_argument("--benchmark", choices=("locomo", "longmemeval"), default="locomo")
    parser.add_argument("--cases", type=Path, default=Path("data/ghost_live_v2/raw_sources/locomo10.json"))
    parser.add_argument("--payload", default=DEFAULT_POISON_PAYLOAD)
    parser.add_argument("--seed", type=int, default=24)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--retrieval-top-k", type=int, default=5)
    parser.add_argument("--candidate-pool-k", type=int, default=10)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError("fresh intervention spec builder refuses an existing output")
    if args.mechanism == "state_drift":
        if args.source_events is None or not args.source_events.is_file():
            parser.error("state_drift requires --source-events")
        rows = _state_rows(args.source_events)
        source = args.source_events
    else:
        rows = _poison_rows(
            benchmark=args.benchmark,
            cases=args.cases,
            payload=args.payload,
            seed=args.seed,
            offset=args.offset,
            limit=args.limit,
            retrieval_top_k=args.retrieval_top_k,
            candidate_pool_k=args.candidate_pool_k,
        )
        source = args.cases
    root = _write_jsonl(args.output, rows)
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "mechanism": args.mechanism,
        "case_count": len(rows),
        "intervention_spec_sha256": root,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "runtime_uses_reference_targets": False,
        "runtime_uses_scorer_labels": False,
        "selection": (
            {
                "benchmark": args.benchmark,
                "seed": args.seed,
                "offset": args.offset,
                "limit": args.limit,
                "payload_sha256": content_sha256(args.payload),
            }
            if args.mechanism == "adversarial_poison"
            else {"source_event_stream": "externally_frozen"}
        ),
    }
    manifest["binding_root"] = content_sha256(manifest)
    atomic_json_write(
        args.output.with_suffix(args.output.suffix + ".manifest.json"), manifest,
        ensure_ascii=False, allow_nan=False, indent=2, trailing_newline=True,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
