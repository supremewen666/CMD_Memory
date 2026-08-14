#!/usr/bin/env python3
"""Deterministic single/dual-lane materialization and strict V4 shard merging."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
from typing import Callable, Mapping, Sequence

from experiments.v4_prequential_runner import V4PrequentialCase


DUAL_GPU_LANES = ("gpu0", "gpu1")
SINGLE_GPU_LANE = "single_gpu"
LANES = (*DUAL_GPU_LANES, SINGLE_GPU_LANE)
MATERIALIZATION_SCHEMA_VERSION = "cmd-v4-materialized-shard-v1"
MERGE_SCHEMA_VERSION = "cmd-v4-materialized-merge-v1"
Backend = Callable[[Mapping[str, object], str], object]
Validator = Callable[[Mapping[str, object]], object]


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _case_id(value: Mapping[str, object]) -> str:
    case_id = value.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError("every materialization row requires case_id")
    return case_id


def _as_mapping(value: object, name: str) -> dict[str, object]:
    if hasattr(value, "to_mapping"):
        value = value.to_mapping()
    return dict(_mapping(value, name))


def _append_jsonl(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as stream:
        stream.write(encoded + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lane_for_case(case_id: str) -> str:
    if not isinstance(case_id, str) or not case_id:
        raise ValueError("case_id must be a non-empty string")
    bucket = int(hashlib.sha256(case_id.encode("utf-8")).hexdigest(), 16) % 2
    return DUAL_GPU_LANES[bucket]


def _rows_for_lane(
    rows: Sequence[Mapping[str, object]], lane: str
) -> tuple[Mapping[str, object], ...]:
    if lane == SINGLE_GPU_LANE:
        return tuple(rows)
    return tuple(row for row in rows if lane_for_case(_case_id(row)) == lane)


def passthrough_backend(row: Mapping[str, object], lane: str) -> Mapping[str, object]:
    """Validate/copy already materialized rows; useful for replay and smoke runs."""
    _ = lane
    return row


def load_backend(locator: str) -> Backend:
    module_name, separator, attribute = locator.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("backend must use module:function syntax")
    module = importlib.import_module(module_name)
    backend = getattr(module, attribute, None)
    if not callable(backend):
        raise ValueError(f"materialization backend is not callable: {locator}")
    return backend


def load_jsonl(
    path: Path, *, allow_empty: bool = False
) -> tuple[Mapping[str, object], ...]:
    rows: list[Mapping[str, object]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSONL at {path}:{line_number}") from error
        rows.append(_mapping(value, f"row {line_number}"))
    if not rows and not allow_empty:
        raise ValueError(f"JSONL input is empty: {path}")
    return tuple(rows)


def materialize_shard(
    rows: Sequence[Mapping[str, object]],
    *,
    lane: str,
    output: Path,
    progress: Path,
    backend: Backend,
    validator: Validator,
    model_call_accounting: Mapping[str, int] | None = None,
) -> dict[str, object]:
    if lane not in LANES:
        raise ValueError("lane must be gpu0, gpu1, or single_gpu")
    if output.exists() or progress.exists():
        raise ValueError("refusing to overwrite materialization artifacts")
    selected = _rows_for_lane(rows, lane)
    _append_jsonl(
        progress,
        {"event": "started", "lane": lane, "completed": 0, "total": len(selected)},
    )
    case_ids: list[str] = []
    try:
        for index, raw in enumerate(selected, 1):
            raw_case_id = _case_id(raw)
            materialized = _as_mapping(
                validator(_mapping(backend(raw, lane), "backend result")),
                "validated backend result",
            )
            if _case_id(materialized) != raw_case_id:
                raise ValueError("materialization backend changed case_id")
            if lane != SINGLE_GPU_LANE and lane_for_case(raw_case_id) != lane:
                raise ValueError("materialized case escaped its deterministic lane")
            _append_jsonl(output, materialized)
            case_ids.append(raw_case_id)
            _append_jsonl(
                progress,
                {
                    "event": "case_materialized",
                    "lane": lane,
                    "case_id": raw_case_id,
                    "completed": index,
                    "total": len(selected),
                },
            )
    except Exception as error:
        _append_jsonl(
            progress,
            {
                "event": "failed",
                "lane": lane,
                "completed": len(case_ids),
                "total": len(selected),
                "error": repr(error),
            },
        )
        raise
    if not output.exists():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("", encoding="utf-8")
    manifest: dict[str, object] = {
        "schema_version": MATERIALIZATION_SCHEMA_VERSION,
        "lane": lane,
        "partition_rule": (
            "all_cases" if lane == SINGLE_GPU_LANE else "sha256(case_id)_mod_2"
        ),
        "input_case_count": len(rows),
        "case_count": len(case_ids),
        "case_ids": sorted(case_ids),
        "output": str(output.resolve()),
        "output_sha256": _file_sha256(output),
        "model_call_accounting": dict(model_call_accounting or {}),
    }
    _atomic_json(output.with_suffix(output.suffix + ".manifest.json"), manifest)
    _append_jsonl(
        progress,
        {
            "event": "completed",
            "lane": lane,
            "completed": len(case_ids),
            "total": len(selected),
            "output_sha256": manifest["output_sha256"],
        },
    )
    return manifest


def merge_materialized_shards(
    shards: Sequence[Path],
    *,
    output: Path,
    expected_case_ids: set[str] | None,
    validator: Validator,
    event_index: Callable[[Mapping[str, object]], object],
) -> dict[str, object]:
    if not shards:
        raise ValueError("merge requires at least one materialization shard")
    if output.exists():
        raise ValueError("refusing to overwrite merged materialization artifact")
    by_case: dict[str, dict[str, object]] = {}
    for shard in shards:
        if not shard.exists():
            raise ValueError(f"materialization shard is missing: {shard}")
        for raw in load_jsonl(shard, allow_empty=True):
            checked = _as_mapping(validator(raw), "validated materialized case")
            case_id = _case_id(checked)
            if case_id in by_case:
                raise ValueError(f"duplicate case_id across shards: {case_id}")
            by_case[case_id] = checked
    actual = set(by_case)
    if expected_case_ids is not None and actual != expected_case_ids:
        missing = sorted(expected_case_ids - actual)
        extra = sorted(actual - expected_case_ids)
        raise ValueError(f"materialized case coverage mismatch: missing={missing}, extra={extra}")
    indexed: list[tuple[int, str, dict[str, object]]] = []
    for case_id, row in by_case.items():
        value = event_index(row)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("materialized case event index must be non-negative")
        indexed.append((value, case_id, row))
    if len({value for value, _case_id_value, _row in indexed}) != len(indexed):
        raise ValueError("materialized case event indexes must be unique")
    for _index, _case_id_value, row in sorted(indexed):
        _append_jsonl(output, row)
    shard_hashes = {
        str(path.resolve()): _file_sha256(path) for path in sorted(shards)
    }
    call_totals: dict[str, int] = {}
    manifest_lanes: list[str] = []
    partition_rules: list[str] = []
    for shard in shards:
        manifest_path = shard.with_suffix(shard.suffix + ".manifest.json")
        if not manifest_path.is_file():
            raise ValueError(f"materialization shard manifest is missing: {manifest_path}")
        manifest_value = _mapping(
            json.loads(manifest_path.read_text(encoding="utf-8")),
            "materialization shard manifest",
        )
        if manifest_value.get("output_sha256") != shard_hashes[str(shard.resolve())]:
            raise ValueError(f"materialization shard manifest hash mismatch: {shard}")
        lane = manifest_value.get("lane")
        partition_rule = manifest_value.get("partition_rule")
        if lane not in LANES or not isinstance(partition_rule, str):
            raise ValueError("materialization shard lane/partition manifest is invalid")
        manifest_lanes.append(lane)
        partition_rules.append(partition_rule)
        accounting = _mapping(
            manifest_value.get("model_call_accounting", {}),
            "model call accounting",
        )
        for role, count in accounting.items():
            if (
                not isinstance(role, str)
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
            ):
                raise ValueError("model call accounting is invalid")
            call_totals[role] = call_totals.get(role, 0) + count
    if len(shards) == 1:
        if manifest_lanes != [SINGLE_GPU_LANE] or partition_rules != ["all_cases"]:
            raise ValueError(
                "single-shard merge requires the single_gpu all_cases manifest"
            )
    elif (
        len(shards) != 2
        or set(manifest_lanes) != set(DUAL_GPU_LANES)
        or set(partition_rules) != {"sha256(case_id)_mod_2"}
    ):
        raise ValueError("multi-shard merge requires exactly gpu0 and gpu1 manifests")
    manifest: dict[str, object] = {
        "schema_version": MERGE_SCHEMA_VERSION,
        "materialization_mode": "single_gpu" if len(shards) == 1 else "multi_shard",
        "shard_count": len(shards),
        "case_count": len(indexed),
        "case_ids_sha256": hashlib.sha256(
            "\n".join(sorted(actual)).encode("utf-8")
        ).hexdigest(),
        "shard_sha256": shard_hashes,
        "output": str(output.resolve()),
        "output_sha256": _file_sha256(output),
        "ordering": "context.event_index_then_case_id",
        "materialization_model_call_accounting": dict(sorted(call_totals.items())),
        "materialization_model_calls": sum(call_totals.values()),
    }
    _atomic_json(output.with_suffix(output.suffix + ".manifest.json"), manifest)
    return manifest


def _validate_case(row: Mapping[str, object]) -> V4PrequentialCase:
    return V4PrequentialCase.from_mapping(row)


def _context_event_index(row: Mapping[str, object]) -> object:
    context = _mapping(row.get("context"), "case context")
    return context.get("event_index")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    materialize_parser = subparsers.add_parser("materialize")
    materialize_parser.add_argument("--source", type=Path, required=True)
    materialize_parser.add_argument("--lane", choices=LANES, required=True)
    materialize_parser.add_argument("--output", type=Path, required=True)
    materialize_parser.add_argument("--progress", type=Path, required=True)
    materialize_parser.add_argument("--limit", type=int)
    materialize_parser.add_argument(
        "--backend",
        default="experiments.v4_materialization:passthrough_backend",
    )
    merge_parser = subparsers.add_parser("merge")
    merge_parser.add_argument("--shard", type=Path, action="append", required=True)
    merge_parser.add_argument("--output", type=Path, required=True)
    merge_parser.add_argument("--expected-source", type=Path)
    args = parser.parse_args(argv)
    if args.action == "materialize":
        rows = load_jsonl(args.source)
        if args.limit is not None:
            if args.limit < 1:
                parser.error("--limit must be positive")
            rows = rows[: args.limit]
        selected = _rows_for_lane(rows, args.lane)
        if args.backend == "experiments.v4_live_materialization:live_backend":
            scored_states = 0
            for row in selected:
                intents = row.get("intents")
                chains = row.get("chain_pairs")
                if not isinstance(intents, list) or not isinstance(chains, list):
                    raise ValueError("live model-call accounting requires intents/chains")
                scored_states += len(intents) + len(chains)
            call_accounting = {
                "answer_generation": scored_states,
                "shadow_judge": scored_states,
            }
        else:
            call_accounting = {}
        manifest = materialize_shard(
            rows,
            lane=args.lane,
            output=args.output,
            progress=args.progress,
            backend=load_backend(args.backend),
            validator=_validate_case,
            model_call_accounting=call_accounting,
        )
    else:
        expected = None
        if args.expected_source is not None:
            expected = {_case_id(row) for row in load_jsonl(args.expected_source)}
        manifest = merge_materialized_shards(
            args.shard,
            output=args.output,
            expected_case_ids=expected,
            validator=_validate_case,
            event_index=_context_event_index,
        )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DUAL_GPU_LANES",
    "LANES",
    "SINGLE_GPU_LANE",
    "lane_for_case",
    "load_backend",
    "materialize_shard",
    "merge_materialized_shards",
    "passthrough_backend",
]
