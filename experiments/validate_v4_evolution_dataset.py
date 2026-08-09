#!/usr/bin/env python3
"""Validate V4 source provenance, runtime leakage, and family isolation."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
from itertools import combinations
import json
from pathlib import Path
from typing import Mapping, Sequence

from cmd_audit.core.models import ProbeCase
from cmd_audit.eval.dev_state_intents import build_dev_intent
from cmd_audit.eval.state_intent import FORBIDDEN_RUNTIME_FIELDS, TEMPLATE_HINT_MARKERS
from experiments.build_v4_evolution_dataset import (
    BUILDER_VERSION,
    DATASET_MANIFEST_SCHEMA_VERSION,
    NORMALIZATION_VERSION,
    OUTPUT_FILES,
    RELATION_REQUEST_SCHEMA_VERSION,
    RUNTIME_ROW_SCHEMA_VERSION,
    SHADOW_ROW_SCHEMA_VERSION,
    SOURCE_MANIFEST_SCHEMA_VERSION,
    SPLIT_MANIFEST_SCHEMA_VERSION,
    SPLIT_POLICY_VERSION,
    canonical_sha256,
    file_sha256,
    hidden_intent_mapping,
)


VALIDATION_SCHEMA_VERSION = "cmd-v4-evolution-dataset-validation-v1"
_RUNTIME_FORBIDDEN_KEYS = frozenset(FORBIDDEN_RUNTIME_FIELDS) | {
    "hidden_intent",
    "probe_set",
    "stream_role",
    "dependency_group",
}


def _load_json(path: Path) -> Mapping[str, object]:
    payload = path.read_bytes()
    if path.suffix == ".gz":
        payload = gzip.decompress(payload)
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must be a JSON object")
    return value


def _load_jsonl(path: Path) -> list[Mapping[str, object]]:
    payload = path.read_bytes()
    if path.suffix == ".gz":
        payload = gzip.decompress(payload)
    rows: list[Mapping[str, object]] = []
    for number, line in enumerate(payload.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{number} must be a JSON object")
        rows.append(value)
    return rows


def _walk(value: object):
    yield value
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _keys(value: object):
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def _case_id(row: Mapping[str, object], source: str) -> str:
    value = row.get("case_id")
    if not isinstance(value, str) or not value:
        raise ValueError(f"{source} row lacks case_id")
    return value


def _source_path(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("source_file must be a non-empty path")
    return Path(value)


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def _source_cases(source_manifest: Mapping[str, object]) -> dict[str, dict[str, object]]:
    sources = source_manifest.get("sources")
    if not isinstance(sources, list) or len(sources) != 3:
        raise ValueError("source manifest must contain three sources")
    output: dict[str, dict[str, object]] = {}
    for source in sources:
        if not isinstance(source, Mapping):
            raise ValueError("source manifest row must be a mapping")
        source_path = _source_path(source.get("source_file"))
        if not source_path.exists():
            raise ValueError(f"source file is unavailable: {source_path}")
        if file_sha256(source_path) != source.get("source_sha256"):
            raise ValueError(f"source hash drift: {source_path}")
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"source file is not a list: {source_path}")
        for row in payload:
            if not isinstance(row, dict):
                raise ValueError("source case must be a mapping")
            case_id = row.get("case_id")
            if not isinstance(case_id, str) or case_id in output:
                raise ValueError("source case IDs must be globally unique")
            output[case_id] = row
    return output


def _runtime_surface_reasons(rows: Sequence[Mapping[str, object]]) -> list[str]:
    reasons: list[str] = []
    seen: set[str] = set()
    for row in rows:
        case_id = _case_id(row, "runtime")
        if case_id in seen:
            reasons.append(f"duplicate_runtime_case:{case_id}")
        seen.add(case_id)
        if set(row) != {
            "schema_version",
            "case_id",
            "source_case_sha256",
            "runtime_case",
        } or row.get("schema_version") != RUNTIME_ROW_SCHEMA_VERSION:
            reasons.append(f"runtime_row_not_closed:{case_id}")
            continue
        runtime = row.get("runtime_case")
        if not isinstance(runtime, Mapping):
            reasons.append(f"runtime_case_not_mapping:{case_id}")
            continue
        if runtime.get("case_id") != case_id:
            reasons.append(f"runtime_case_id_mismatch:{case_id}")
        family_payload = ("runtime\0" + case_id).encode("utf-8")
        expected_family = f"runtime:{hashlib.sha256(family_payload).hexdigest()}"
        if runtime.get("family_id") != expected_family:
            reasons.append(f"runtime_family_not_opaque:{case_id}")
        forbidden = sorted(set(_keys(row)) & _RUNTIME_FORBIDDEN_KEYS)
        if forbidden:
            reasons.append(f"runtime_forbidden_keys:{case_id}:{','.join(forbidden)}")
        strings = [value for value in _walk(row) if isinstance(value, str)]
        if any(marker in value for marker in TEMPLATE_HINT_MARKERS for value in strings):
            reasons.append(f"runtime_template_marker:{case_id}")
    return reasons


def _shadow_surface_reasons(rows: Sequence[Mapping[str, object]]) -> list[str]:
    reasons: list[str] = []
    expected_keys = {
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
    seen: set[str] = set()
    for row in rows:
        case_id = _case_id(row, "shadow")
        if case_id in seen:
            reasons.append(f"duplicate_shadow_case:{case_id}")
        seen.add(case_id)
        if set(row) != expected_keys or row.get("schema_version") != SHADOW_ROW_SCHEMA_VERSION:
            reasons.append(f"shadow_row_not_closed:{case_id}")
        probe = row.get("probe_case")
        if not isinstance(probe, Mapping):
            reasons.append(f"shadow_probe_not_mapping:{case_id}")
            continue
        if probe.get("case_id") != case_id:
            reasons.append(f"shadow_case_id_mismatch:{case_id}")
        if "gold_answer" not in probe or "gold_evidence" not in probe:
            reasons.append(f"shadow_gold_missing:{case_id}")
        strings = [value for value in _walk(probe) if isinstance(value, str)]
        if any(marker in value for marker in TEMPLATE_HINT_MARKERS for value in strings):
            reasons.append(f"shadow_template_marker:{case_id}")
    return reasons


def _relation_surface_reasons(rows: Sequence[Mapping[str, object]]) -> list[str]:
    reasons: list[str] = []
    seen: set[str] = set()
    expected_keys = {
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
    for row in rows:
        case_id = _case_id(row, "relation")
        request_id = row.get("request_id")
        if not isinstance(request_id, str) or request_id in seen:
            reasons.append(f"duplicate_or_missing_relation_request:{case_id}")
        else:
            seen.add(request_id)
        if set(row) != expected_keys or row.get("schema_version") != RELATION_REQUEST_SCHEMA_VERSION:
            reasons.append(f"relation_row_not_closed:{case_id}")
        if row.get("pair_id") != request_id:
            reasons.append(f"relation_pair_id_mismatch:{case_id}")
        forbidden = sorted(set(_keys(row)) & _RUNTIME_FORBIDDEN_KEYS)
        if forbidden:
            reasons.append(f"relation_forbidden_keys:{case_id}:{','.join(forbidden)}")
        strings = [value for value in _walk(row) if isinstance(value, str)]
        if any(marker in value for marker in TEMPLATE_HINT_MARKERS for value in strings):
            reasons.append(f"relation_template_marker:{case_id}")
    return reasons


def _split_reasons(split_manifest: Mapping[str, object]) -> list[str]:
    reasons: list[str] = []
    if (
        split_manifest.get("schema_version") != SPLIT_MANIFEST_SCHEMA_VERSION
        or split_manifest.get("split_policy_version") != SPLIT_POLICY_VERSION
        or split_manifest.get("family_is_evaluation_only") is not True
        or split_manifest.get("unseen_updates_authorized") is not False
    ):
        reasons.append("split_manifest_contract")
    assignments = split_manifest.get("assignments")
    if not isinstance(assignments, list):
        return [*reasons, "split_assignments_not_list"]
    dependency_sets: dict[str, set[str]] = defaultdict(set)
    family_sets: dict[str, set[str]] = defaultdict(set)
    positions: list[int] = []
    case_ids: list[str] = []
    for row in assignments:
        if not isinstance(row, Mapping):
            reasons.append("split_assignment_not_mapping")
            continue
        case_ids.append(_case_id(row, "split"))
        probe_set = row.get("probe_set")
        if probe_set not in {"represented", "unseen"}:
            reasons.append(f"unknown_probe_set:{row.get('case_id')}")
            continue
        dependency_sets[str(row.get("dependency_group"))].add(str(probe_set))
        family_sets[str(row.get("family_id"))].add(str(probe_set))
        position = row.get("stream_position")
        if not isinstance(position, int):
            reasons.append(f"invalid_stream_position:{row.get('case_id')}")
        else:
            positions.append(position)
    if len(set(case_ids)) != len(case_ids):
        reasons.append("duplicate_split_case")
    if any(len(values) != 1 for values in dependency_sets.values()):
        reasons.append("dependency_group_crosses_probe_sets")
    if any(len(values) != 1 for values in family_sets.values()):
        reasons.append("family_crosses_probe_sets")
    if sorted(positions) != list(range(1, len(assignments) + 1)):
        reasons.append("stream_positions_not_total")
    first_unseen = next(
        (
            int(row["stream_position"])
            for row in assignments
            if isinstance(row, Mapping) and row.get("probe_set") == "unseen"
        ),
        len(assignments) + 1,
    )
    if any(
        row.get("probe_set") == "represented"
        and int(row.get("stream_position", 0)) > first_unseen
        for row in assignments
        if isinstance(row, Mapping)
    ):
        reasons.append("represented_case_after_unseen_boundary")
    return reasons


def validate_bundle(dataset_dir: Path) -> dict[str, object]:
    reasons: list[str] = []
    manifest_path = dataset_dir / "dataset_manifest.json"
    try:
        manifest = _load_json(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        reasons.append(f"manifest_input_error:{type(error).__name__}")
        return _report(dataset_dir, reasons, {})
    if (
        manifest.get("schema_version") != DATASET_MANIFEST_SCHEMA_VERSION
        or manifest.get("builder_version") != BUILDER_VERSION
        or manifest.get("normalization_version") != NORMALIZATION_VERSION
        or manifest.get("split_policy_version") != SPLIT_POLICY_VERSION
        or manifest.get("runtime_uses_gold") is not False
        or manifest.get("relation_requests_use_gold") is not False
        or manifest.get("semantic_edges_from_labels") is not False
    ):
        reasons.append("dataset_manifest_contract")
    expected_dataset_hash = manifest.get("dataset_sha256")
    body = {key: value for key, value in manifest.items() if key != "dataset_sha256"}
    if expected_dataset_hash != canonical_sha256(body):
        reasons.append("dataset_manifest_hash_mismatch")

    hashes = manifest.get("file_sha256")
    if not isinstance(hashes, Mapping) or set(hashes) != set(OUTPUT_FILES):
        reasons.append("file_hash_manifest_not_closed")
        hashes = {}
    for name in OUTPUT_FILES:
        path = dataset_dir / name
        if not path.is_file():
            reasons.append(f"missing_file:{name}")
        elif hashes.get(name) != file_sha256(path):
            reasons.append(f"file_hash_mismatch:{name}")
    if reasons and any(reason.startswith("missing_file:") for reason in reasons):
        return _report(dataset_dir, reasons, manifest)

    try:
        runtime_rows = _load_jsonl(dataset_dir / "runtime_cases.jsonl.gz")
        shadow_rows = _load_jsonl(dataset_dir / "shadow_cases.jsonl.gz")
        relation_rows = _load_jsonl(dataset_dir / "relation_requests.jsonl.gz")
        split_manifest = _load_json(dataset_dir / "split_manifest.json.gz")
        source_manifest = _load_json(dataset_dir / "source_manifest.json")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        reasons.append(f"dataset_input_error:{type(error).__name__}")
        return _report(dataset_dir, reasons, manifest)

    if source_manifest.get("schema_version") != SOURCE_MANIFEST_SCHEMA_VERSION:
        reasons.append("source_manifest_schema")
    if manifest.get("source_manifest_sha256") != file_sha256(
        dataset_dir / "source_manifest.json"
    ):
        reasons.append("source_manifest_binding")
    if manifest.get("split_manifest_sha256") != file_sha256(
        dataset_dir / "split_manifest.json.gz"
    ):
        reasons.append("split_manifest_binding")
    reasons.extend(_runtime_surface_reasons(runtime_rows))
    reasons.extend(_shadow_surface_reasons(shadow_rows))
    reasons.extend(_relation_surface_reasons(relation_rows))
    reasons.extend(_split_reasons(split_manifest))

    runtime_by_id = {_case_id(row, "runtime"): row for row in runtime_rows}
    shadow_by_id = {_case_id(row, "shadow"): row for row in shadow_rows}
    assignments = split_manifest.get("assignments", [])
    split_by_id = {
        _case_id(row, "split"): row
        for row in assignments
        if isinstance(row, Mapping)
    }
    case_sets = {frozenset(runtime_by_id), frozenset(shadow_by_id), frozenset(split_by_id)}
    if len(case_sets) != 1:
        reasons.append("runtime_shadow_split_case_set_mismatch")
    if manifest.get("case_count") != len(runtime_rows):
        reasons.append("manifest_case_count_mismatch")
    if manifest.get("relation_request_count") != len(relation_rows):
        reasons.append("manifest_relation_count_mismatch")

    try:
        source_by_id = _source_cases(source_manifest)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        reasons.append(f"source_replay_error:{type(error).__name__}")
        source_by_id = {}
    for case_id in sorted(set(runtime_by_id) & set(shadow_by_id) & set(split_by_id)):
        runtime_row = runtime_by_id[case_id]
        shadow_row = shadow_by_id[case_id]
        assignment = split_by_id[case_id]
        source = source_by_id.get(case_id)
        if source is None:
            reasons.append(f"source_case_missing:{case_id}")
            continue
        expected_source_hash = canonical_sha256(source)
        if {
            runtime_row.get("source_case_sha256"),
            shadow_row.get("source_case_sha256"),
        } != {expected_source_hash}:
            reasons.append(f"source_case_hash_mismatch:{case_id}")
        for field in ("family_id", "dependency_group", "probe_set", "stream_role"):
            if shadow_row.get(field) != assignment.get(field):
                reasons.append(f"shadow_split_mismatch:{case_id}:{field}")
        probe_mapping = shadow_row.get("probe_case")
        if not isinstance(probe_mapping, Mapping):
            reasons.append(f"shadow_probe_not_mapping:{case_id}")
            continue
        try:
            intent = build_dev_intent(
                ProbeCase.from_mapping(dict(probe_mapping)),
                token_budget=int(manifest["token_budget"]),
                family_id=str(shadow_row["family_id"]),
            )
            if shadow_row.get("hidden_intent") != hidden_intent_mapping(intent):
                reasons.append(f"hidden_intent_replay_mismatch:{case_id}")
        except (KeyError, TypeError, ValueError) as error:
            reasons.append(f"hidden_intent_replay_error:{case_id}:{type(error).__name__}")

    expected_pairs: set[tuple[str, str, str]] = set()
    for case_id, row in runtime_by_id.items():
        runtime = row.get("runtime_case")
        if not isinstance(runtime, Mapping) or not isinstance(runtime.get("items"), list):
            continue
        item_ids = sorted(
            str(item["item_id"])
            for item in runtime["items"]
            if isinstance(item, Mapping) and item.get("retrieved") is True
        )
        expected_pairs.update(
            (case_id, left, right) for left, right in combinations(item_ids, 2)
        )
    actual_pairs = {
        (
            str(row.get("case_id")),
            str(row.get("left_item_id")),
            str(row.get("right_item_id")),
        )
        for row in relation_rows
    }
    if actual_pairs != expected_pairs:
        reasons.append("relation_requests_do_not_exactly_cover_retrieved_pairs")

    summary = {
        "case_count": len(runtime_rows),
        "family_count": len(
            {str(row.get("family_id")) for row in assignments if isinstance(row, Mapping)}
        ),
        "relation_request_count": len(relation_rows),
        "probe_set_counts": dict(
            sorted(
                Counter(
                    str(row.get("probe_set"))
                    for row in assignments
                    if isinstance(row, Mapping)
                ).items()
            )
        ),
        "domain_case_counts": dict(
            sorted(
                Counter(
                    str(row.get("domain"))
                    for row in assignments
                    if isinstance(row, Mapping)
                ).items()
            )
        ),
        "domain_family_counts": {
            domain: len(
                {
                    str(row.get("family_id"))
                    for row in assignments
                    if isinstance(row, Mapping) and row.get("domain") == domain
                }
            )
            for domain in sorted(
                {
                    str(row.get("domain"))
                    for row in assignments
                    if isinstance(row, Mapping)
                }
            )
        },
        "intent_constructibility_rate": (
            len(shadow_rows) / len(runtime_rows) if runtime_rows else 0.0
        ),
        "runtime_template_marker_count": sum(
            reason.startswith("runtime_template_marker:") for reason in reasons
        ),
        "dependency_split_violations": sum(
            reason == "dependency_group_crosses_probe_sets" for reason in reasons
        ),
    }
    return _report(dataset_dir, reasons, manifest, summary=summary)


def _report(
    dataset_dir: Path,
    reasons: Sequence[str],
    manifest: Mapping[str, object],
    *,
    summary: Mapping[str, object] | None = None,
) -> dict[str, object]:
    report: dict[str, object] = {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "decision": "PASS" if not reasons else "REFUSE",
        "dataset_dir": _portable_path(dataset_dir),
        "dataset_manifest_sha256": (
            file_sha256(dataset_dir / "dataset_manifest.json")
            if (dataset_dir / "dataset_manifest.json").is_file()
            else None
        ),
        "dataset_sha256": manifest.get("dataset_sha256"),
        "reasons": sorted(set(reasons)),
        "summary": dict(summary or {}),
    }
    report["report_sha256"] = canonical_sha256(report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = validate_bundle(args.dataset_dir)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        report = _report(
            args.dataset_dir,
            (f"input_error:{type(error).__name__}",),
            {},
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"decision": report["decision"], "output": str(args.output)}))
    return 0 if report["decision"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["VALIDATION_SCHEMA_VERSION", "main", "validate_bundle"]
