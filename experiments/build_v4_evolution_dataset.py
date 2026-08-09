#!/usr/bin/env python3
"""Build the leak-separated, family-blocked CPU package for V4 evolution.

This command deliberately stops before semantic relation measurement and intent
proposal.  It emits the exact runtime text pairs those frozen instruments must
consume; it never fabricates a relation edge from gold labels.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
import gzip
import hashlib
from itertools import combinations
import json
from pathlib import Path
import re
from typing import Mapping, Sequence

from cmd_audit.core.models import ProbeCase
from cmd_audit.eval.dev_state_intents import build_dev_intent, family_id_for_case
from cmd_audit.eval.state_intent import (
    RuntimeRepairCase,
    runtime_case_from_probe_case,
    runtime_case_to_mapping,
)
from cmd_audit.repair.memtrace_families import (
    HELDOUT_C_INDICES,
    UPDATE_C_INDICES,
    build_families,
    family_bucket,
)


BUILDER_VERSION = "cmd-v4-evolution-dataset-builder-v1"
DATASET_MANIFEST_SCHEMA_VERSION = "cmd-v4-evolution-dataset-manifest-v1"
SOURCE_MANIFEST_SCHEMA_VERSION = "cmd-v4-source-manifest-v1"
SPLIT_MANIFEST_SCHEMA_VERSION = "cmd-v4-family-split-manifest-v1"
RUNTIME_ROW_SCHEMA_VERSION = "cmd-v4-runtime-row-v1"
SHADOW_ROW_SCHEMA_VERSION = "cmd-v4-shadow-row-v1"
RELATION_REQUEST_SCHEMA_VERSION = "cmd-v4-relation-request-v1"
NORMALIZATION_VERSION = "cmd-v4-runtime-text-normalization-v1"
SPLIT_POLICY_VERSION = "cmd-v4-dependency-split-v1"
DEFAULT_SEED = 20260809
DEFAULT_TOKEN_BUDGET = 100_000
OUTPUT_FILES = (
    "runtime_cases.jsonl.gz",
    "shadow_cases.jsonl.gz",
    "relation_requests.jsonl.gz",
    "split_manifest.json.gz",
    "source_manifest.json",
)

_MARKER = re.compile(r"\bM_(?:old|new)\s*:\s*", re.IGNORECASE)
_SPACE = re.compile(r"\s+")
_STALE_MEMBER = re.compile(r"-dim(\d+)$")
_MEMFAIL_MEMBER = re.compile(r"-q(\d+)$")


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_runtime_text(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("runtime text normalization requires a string")
    return _SPACE.sub(" ", _MARKER.sub("", value).strip())


def _normalize_strings(value: object) -> object:
    if isinstance(value, str):
        return normalize_runtime_text(value)
    if isinstance(value, list):
        return [_normalize_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_strings(item) for key, item in value.items()}
    return value


def normalize_probe_mapping(value: Mapping[str, object]) -> dict[str, object]:
    normalized = _normalize_strings(deepcopy(dict(value)))
    if not isinstance(normalized, dict):
        raise AssertionError("normalization changed a probe mapping into another type")
    return normalized


def runtime_case_mapping(case: RuntimeRepairCase) -> dict[str, object]:
    value = runtime_case_to_mapping(case)
    value["items"] = [
        {**mapping, "retrieved": source.retrieved}
        for mapping, source in zip(value["items"], case.items, strict=True)
    ]
    return value


def hidden_intent_mapping(value: object) -> dict[str, object]:
    mapping = {
        "case_id": value.case_id,
        "family_id": value.family_id,
        "required_items": [asdict(row) for row in value.required_items],
        "perturbations": [asdict(row) for row in value.perturbations],
        "protected_item_ids": list(value.protected_item_ids),
        "allowed_added_item_ids": list(value.allowed_added_item_ids),
        "required_provenance_hashes": [
            list(row) for row in value.required_provenance_hashes
        ],
        "token_budget": value.token_budget,
        "null_case": value.null_case,
        "schema_version": value.schema_version,
    }
    # Round through the canonical JSON representation so tuple-valued nested
    # dataclass fields have the same list shape before and after disk replay.
    return json.loads(canonical_bytes(mapping))


def _opaque(prefix: str, *parts: str) -> str:
    payload = "\0".join((prefix, *parts)).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(payload).hexdigest()}"


def _split_bucket(dependency_group: str) -> int:
    payload = f"{SPLIT_POLICY_VERSION}\0{dependency_group}".encode("utf-8")
    return int(hashlib.sha256(payload).hexdigest(), 16) % 5


def _member_index(case_id: str, pattern: re.Pattern[str]) -> int:
    match = pattern.search(case_id)
    return int(match.group(1)) if match else 0


def _case_metadata(
    domain: str,
    cases: Sequence[ProbeCase],
) -> dict[str, dict[str, object]]:
    if domain == "memtrace_kp":
        output: dict[str, dict[str, object]] = {}
        for family in build_families(cases):
            dependency_group = _opaque("dependency", domain, family.user_uuid)
            probe_set = "unseen" if family_bucket(family.user_uuid) == 0 else "represented"
            for member in family.members:
                if probe_set == "unseen":
                    role = "unseen"
                elif member.c_index in UPDATE_C_INDICES:
                    role = "represented_update"
                elif member.c_index in HELDOUT_C_INDICES:
                    role = "represented_later"
                else:
                    role = "represented_other"
                output[member.case_id] = {
                    "family_id": f"memtrace_kp:{family.family_id}",
                    "dependency_group": dependency_group,
                    "probe_set": probe_set,
                    "stream_role": role,
                    "member_index": member.c_index,
                    "member_subindex": member.a_index,
                }
        return output

    pattern = _STALE_MEMBER if domain == "stale_item" else _MEMFAIL_MEMBER
    output = {}
    for case in cases:
        raw_family = family_id_for_case(case.case_id)
        family_id = _opaque("family", domain, raw_family)
        dependency_group = _opaque("dependency", domain, raw_family)
        probe_set = "unseen" if _split_bucket(dependency_group) == 0 else "represented"
        output[case.case_id] = {
            "family_id": family_id,
            "dependency_group": dependency_group,
            "probe_set": probe_set,
            "stream_role": "unseen" if probe_set == "unseen" else "represented_stream",
            "member_index": _member_index(case.case_id, pattern),
            "member_subindex": 0,
        }
    return output


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def _load_domain(
    domain: str,
    path: Path,
    *,
    limit: int | None,
) -> tuple[list[dict[str, object]], list[ProbeCase], dict[str, object]]:
    raw_bytes = path.read_bytes()
    payload = json.loads(raw_bytes)
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"{domain} source must be a non-empty JSON list")
    if any(not isinstance(row, dict) for row in payload):
        raise ValueError(f"{domain} source rows must be mappings")
    selected = payload[:limit] if limit is not None else payload
    cases = [ProbeCase.from_mapping(row) for row in selected]
    case_ids = [case.case_id for case in cases]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError(f"{domain} contains duplicate selected case IDs")
    source = {
        "domain": domain,
        "source_file": _portable_path(path),
        "source_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "source_byte_size": len(raw_bytes),
        "source_case_count": len(payload),
        "selected_case_count": len(selected),
        "selected_case_ids_sha256": canonical_sha256(case_ids),
    }
    return list(selected), cases, source


def _parse_observed_at(store: str) -> str | None:
    if not isinstance(store, str):
        return None
    try:
        parsed = datetime.fromisoformat(store.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.isoformat().replace("+00:00", "Z")


def _ordering_evidence(
    *,
    case: RuntimeRepairCase,
    item: object,
) -> dict[str, object]:
    event_positions = {
        event.event_id: position for position, event in enumerate(case.raw_events)
    }
    positions = [
        event_positions[event_id]
        for event_id in item.source_event_ids
        if event_id in event_positions
    ]
    observed_at = _parse_observed_at(item.store)
    event_sequence = min(positions) if positions else None
    usable = observed_at is not None or event_sequence is not None
    event_stream_id = (
        canonical_sha256([event.event_id for event in case.raw_events])
        if event_sequence is not None
        else None
    )
    return {
        "item_id": item.item_id,
        "observed_at": observed_at,
        "observed_at_domain": "utc-rfc3339-v1" if observed_at is not None else None,
        "event_sequence": event_sequence,
        "event_stream_id": event_stream_id,
        "source_priority": None,
        "source_priority_domain": None,
        "provenance": "probe-runtime-sidecar" if usable else "none",
        "audit_version": BUILDER_VERSION if usable else "unavailable",
        "deployment_visible": usable,
        "reliability": "trusted" if usable else "unknown",
    }


def _relation_requests(case: RuntimeRepairCase) -> list[dict[str, object]]:
    retrieved = sorted(
        (item for item in case.items if item.retrieved), key=lambda item: item.item_id
    )
    rows: list[dict[str, object]] = []
    for left, right in combinations(retrieved, 2):
        pair_payload = {
            "case_id": case.case_id,
            "left_item_id": left.item_id,
            "right_item_id": right.item_id,
            "left_text": left.text,
            "right_text": right.text,
        }
        pair_id = canonical_sha256(pair_payload)
        rows.append(
            {
                "schema_version": RELATION_REQUEST_SCHEMA_VERSION,
                "request_id": pair_id,
                "pair_id": pair_id,
                **pair_payload,
                "left_evidence": _ordering_evidence(case=case, item=left),
                "right_evidence": _ordering_evidence(case=case, item=right),
            }
        )
    return rows


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def _write_gzip(path: Path, payload: bytes) -> None:
    path.write_bytes(gzip.compress(payload, compresslevel=9, mtime=0))


def _write_json_gzip(path: Path, value: object) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode("utf-8")
    _write_gzip(path, payload)


def _write_jsonl_gzip(
    path: Path,
    rows: Sequence[Mapping[str, object]],
) -> None:
    payload = b"".join(canonical_bytes(row) + b"\n" for row in rows)
    _write_gzip(path, payload)


def build_dataset(
    *,
    source_paths: Mapping[str, Path],
    output_dir: Path,
    seed: int = DEFAULT_SEED,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    limit_per_domain: int | None = None,
) -> dict[str, object]:
    """Build one deterministic CPU package and return its closed manifest."""
    expected_domains = {"memtrace_kp", "stale_item", "memfail"}
    if set(source_paths) != expected_domains:
        raise ValueError("source_paths must contain exactly memtrace_kp/stale_item/memfail")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if token_budget < 1:
        raise ValueError("token_budget must be positive")
    if limit_per_domain is not None and limit_per_domain < 1:
        raise ValueError("limit_per_domain must be positive")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("refusing to overwrite a non-empty V4 dataset directory")
    output_dir.mkdir(parents=True, exist_ok=True)

    sources: list[dict[str, object]] = []
    records: list[dict[str, object]] = []
    seen_case_ids: set[str] = set()
    for domain in sorted(expected_domains):
        raw_rows, cases, source = _load_domain(
            domain,
            Path(source_paths[domain]),
            limit=limit_per_domain,
        )
        sources.append(source)
        metadata = _case_metadata(domain, cases)
        for raw, case in zip(raw_rows, cases, strict=True):
            if case.case_id in seen_case_ids:
                raise ValueError(f"cross-domain duplicate case_id: {case.case_id}")
            seen_case_ids.add(case.case_id)
            records.append(
                {
                    "domain": domain,
                    "raw": raw,
                    "case": case,
                    "metadata": metadata[case.case_id],
                    "source_case_sha256": canonical_sha256(raw),
                }
            )

    grouped: dict[str, list[dict[str, object]]] = {}
    for record in records:
        dependency = str(record["metadata"]["dependency_group"])
        grouped.setdefault(dependency, []).append(record)
    group_order = sorted(
        grouped,
        key=lambda dependency: (
            grouped[dependency][0]["metadata"]["probe_set"] == "unseen",
            canonical_sha256({"seed": seed, "dependency_group": dependency}),
        ),
    )
    ordered: list[dict[str, object]] = []
    for dependency in group_order:
        ordered.extend(
            sorted(
                grouped[dependency],
                key=lambda record: (
                    record["metadata"]["member_index"],
                    record["metadata"]["member_subindex"],
                    record["case"].case_id,
                ),
            )
        )

    runtime_rows: list[dict[str, object]] = []
    shadow_rows: list[dict[str, object]] = []
    relation_rows: list[dict[str, object]] = []
    assignments: list[dict[str, object]] = []
    for position, record in enumerate(ordered, 1):
        metadata = record["metadata"]
        normalized_mapping = normalize_probe_mapping(record["raw"])
        probe = ProbeCase.from_mapping(normalized_mapping)
        runtime_family = _opaque("runtime", probe.case_id)
        runtime = runtime_case_from_probe_case(
            probe,
            token_budget=token_budget,
            family_id=runtime_family,
            reject_template_hints=True,
        )
        hidden = build_dev_intent(
            probe,
            token_budget=token_budget,
            family_id=str(metadata["family_id"]),
        )
        runtime_rows.append(
            {
                "schema_version": RUNTIME_ROW_SCHEMA_VERSION,
                "case_id": probe.case_id,
                "source_case_sha256": record["source_case_sha256"],
                "runtime_case": runtime_case_mapping(runtime),
            }
        )
        shadow_rows.append(
            {
                "schema_version": SHADOW_ROW_SCHEMA_VERSION,
                "case_id": probe.case_id,
                "family_id": metadata["family_id"],
                "dependency_group": metadata["dependency_group"],
                "probe_set": metadata["probe_set"],
                "stream_role": metadata["stream_role"],
                "source_case_sha256": record["source_case_sha256"],
                "probe_case": normalized_mapping,
                "hidden_intent": hidden_intent_mapping(hidden),
            }
        )
        relation_rows.extend(_relation_requests(runtime))
        assignments.append(
            {
                "case_id": probe.case_id,
                "domain": record["domain"],
                "family_id": metadata["family_id"],
                "dependency_group": metadata["dependency_group"],
                "probe_set": metadata["probe_set"],
                "stream_role": metadata["stream_role"],
                "stream_position": position,
                "selection_event_index": position * 100,
                "member_index": metadata["member_index"],
            }
        )

    source_manifest = {
        "schema_version": SOURCE_MANIFEST_SCHEMA_VERSION,
        "builder_version": BUILDER_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "sources": sources,
    }
    split_counts = Counter(str(row["probe_set"]) for row in assignments)
    role_counts = Counter(str(row["stream_role"]) for row in assignments)
    split_manifest = {
        "schema_version": SPLIT_MANIFEST_SCHEMA_VERSION,
        "split_policy_version": SPLIT_POLICY_VERSION,
        "seed": seed,
        "family_is_evaluation_only": True,
        "unseen_updates_authorized": False,
        "case_count": len(assignments),
        "family_count": len({str(row["family_id"]) for row in assignments}),
        "dependency_group_count": len(
            {str(row["dependency_group"]) for row in assignments}
        ),
        "probe_set_counts": dict(sorted(split_counts.items())),
        "stream_role_counts": dict(sorted(role_counts.items())),
        "assignments": assignments,
    }
    _write_jsonl_gzip(output_dir / "runtime_cases.jsonl.gz", runtime_rows)
    _write_jsonl_gzip(output_dir / "shadow_cases.jsonl.gz", shadow_rows)
    _write_jsonl_gzip(output_dir / "relation_requests.jsonl.gz", relation_rows)
    _write_json_gzip(output_dir / "split_manifest.json.gz", split_manifest)
    _write_json(output_dir / "source_manifest.json", source_manifest)

    file_hashes = {
        name: file_sha256(output_dir / name) for name in OUTPUT_FILES
    }
    domain_counts = Counter(str(row["domain"]) for row in assignments)
    domain_family_counts = {
        domain: len(
            {
                str(row["family_id"])
                for row in assignments
                if row["domain"] == domain
            }
        )
        for domain in sorted(expected_domains)
    }
    domain_dependency_counts = {
        domain: len(
            {
                str(row["dependency_group"])
                for row in assignments
                if row["domain"] == domain
            }
        )
        for domain in sorted(expected_domains)
    }
    domain_probe_set_counts = {
        domain: dict(
            sorted(
                Counter(
                    str(row["probe_set"])
                    for row in assignments
                    if row["domain"] == domain
                ).items()
            )
        )
        for domain in sorted(expected_domains)
    }
    manifest: dict[str, object] = {
        "schema_version": DATASET_MANIFEST_SCHEMA_VERSION,
        "builder_version": BUILDER_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "split_policy_version": SPLIT_POLICY_VERSION,
        "build_status": "relation_instrument_pending",
        "runtime_uses_gold": False,
        "relation_requests_use_gold": False,
        "semantic_edges_from_labels": False,
        "seed": seed,
        "token_budget": token_budget,
        "case_count": len(assignments),
        "family_count": split_manifest["family_count"],
        "dependency_group_count": split_manifest["dependency_group_count"],
        "relation_request_count": len(relation_rows),
        "domain_case_counts": dict(sorted(domain_counts.items())),
        "domain_family_counts": domain_family_counts,
        "domain_dependency_group_counts": domain_dependency_counts,
        "domain_probe_set_counts": domain_probe_set_counts,
        "probe_set_counts": dict(sorted(split_counts.items())),
        "source_manifest_sha256": file_hashes["source_manifest.json"],
        "split_manifest_sha256": file_hashes["split_manifest.json.gz"],
        "file_sha256": file_hashes,
        "next_required_artifact": "frozen_relation_verdicts_and_complete_intent_proposals",
    }
    manifest["dataset_sha256"] = canonical_sha256(manifest)
    _write_json(output_dir / "dataset_manifest.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--memtrace",
        type=Path,
        default=Path("data/probe_cases/memtrace_kp_cases.json"),
    )
    parser.add_argument(
        "--stale",
        type=Path,
        default=Path("data/probe_cases/stale_item_cases.json"),
    )
    parser.add_argument(
        "--memfail",
        type=Path,
        default=Path("data/probe_cases/memfail_cases.json"),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)
    parser.add_argument("--limit-per-domain", type=int)
    args = parser.parse_args(argv)
    try:
        manifest = build_dataset(
            source_paths={
                "memtrace_kp": args.memtrace,
                "stale_item": args.stale,
                "memfail": args.memfail,
            },
            output_dir=args.output_dir,
            seed=args.seed,
            token_budget=args.token_budget,
            limit_per_domain=args.limit_per_domain,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(f"REFUSE: {type(error).__name__}: {error}")
        return 2
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BUILDER_VERSION",
    "DATASET_MANIFEST_SCHEMA_VERSION",
    "NORMALIZATION_VERSION",
    "OUTPUT_FILES",
    "build_dataset",
    "canonical_sha256",
    "file_sha256",
    "hidden_intent_mapping",
    "main",
    "normalize_probe_mapping",
    "normalize_runtime_text",
    "runtime_case_mapping",
]
