#!/usr/bin/env python3
"""Execute a closed follow-up capture plan with a pluggable live backend.

The backend seam is deliberately small and versioned.  It receives one
``cmd-v4-followup-capture-plan-v1`` mapping and returns only structured later
events plus explicit call accounting.  This runner adds the frozen root event,
groups candidate branches into sessions, validates the result through the
session-lineage schema, and publishes a normalized export atomically.

Backend callable contract::

    def capture(plan: Mapping[str, object]) -> Mapping[str, object]:
        return {
            "schema_version": "cmd-v4-followup-capture-result-v2",
            "plan_id": plan["plan_id"],
            "followup_events": [...],
            "model_call_accounting": {"answer_generation": 1, ...},
            "network_calls": 1,
            "capture_provenance": "claude-tap-export:<content-hash>",
            "source_export_schema": "claude-tap-normalized-v1",
            "source_export_sha256": "<sha256 of the immutable raw export>",
        }

Free text is never parsed for item IDs.  Every event must carry the structured
IDs required by ``cmd-session-normalized-v1``.  Missing follow-up observations
remain absent and later project to unknown; this runner never fabricates them.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import tempfile
from typing import Callable, Mapping, Sequence

from cmd_audit.adapters.session_log import (
    ALLOWED_SOURCE_SCHEMAS,
    normalize_session_export,
)
from experiments.v4_lineage_dataset import CAPTURE_PLAN_SCHEMA_VERSION


CAPTURE_RESULT_SCHEMA_VERSION = "cmd-v4-followup-capture-result-v2"
CAPTURE_RUN_MANIFEST_SCHEMA_VERSION = "cmd-v4-followup-capture-run-manifest-v2"
NORMALIZED_EXPORT_SCHEMA_VERSION = "cmd-session-normalized-v1"
CaptureBackend = Callable[[Mapping[str, object]], Mapping[str, object]]


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _load_jsonl(path: Path) -> tuple[Mapping[str, object], ...]:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows: list[Mapping[str, object]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSONL at {path}:{number}") from error
        rows.append(_mapping(row, f"capture plan row {number}"))
    if not rows:
        raise ValueError("capture plan is empty")
    return tuple(rows)


def load_backend(locator: str) -> CaptureBackend:
    module_name, separator, attribute = locator.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("capture backend must use module:function syntax")
    backend = getattr(importlib.import_module(module_name), attribute, None)
    if not callable(backend):
        raise ValueError(f"capture backend is not callable: {locator}")
    return backend


def _validate_accounting(value: object) -> dict[str, int]:
    mapping = _mapping(value, "model_call_accounting")
    result: dict[str, int] = {}
    for role, count in mapping.items():
        if (
            not isinstance(role, str)
            or not role
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
        ):
            raise ValueError("model call accounting must map roles to non-negative integers")
        result[role] = count
    return result


def _validate_result(
    plan: Mapping[str, object], result: Mapping[str, object]
) -> tuple[
    tuple[Mapping[str, object], ...],
    dict[str, int],
    int,
    str,
    str,
    str,
]:
    expected = {
        "schema_version",
        "plan_id",
        "followup_events",
        "model_call_accounting",
        "network_calls",
        "capture_provenance",
        "source_export_schema",
        "source_export_sha256",
    }
    if set(result) != expected or result.get("schema_version") != CAPTURE_RESULT_SCHEMA_VERSION:
        raise ValueError("capture backend result is not closed or versioned")
    if result.get("plan_id") != plan.get("plan_id"):
        raise ValueError("capture backend result does not bind the plan")
    raw_events = result.get("followup_events")
    if not isinstance(raw_events, list) or not raw_events:
        raise ValueError("capture backend must return at least one real follow-up event")
    events: list[Mapping[str, object]] = []
    root = _mapping(plan.get("root_event"), "capture root event")
    root_id = root.get("event_id")
    branch_id = plan.get("branch_id")
    session_id = plan.get("session_id")
    stream_id = plan.get("stream_id")
    effective_after = plan.get("effective_after_event_index")
    for offset, raw in enumerate(raw_events):
        event = _mapping(raw, f"follow-up event {offset}")
        for name, expected_value in (
            ("session_id", session_id),
            ("stream_id", stream_id),
            ("branch_id", branch_id),
        ):
            if event.get(name) != expected_value:
                raise ValueError(f"follow-up event {name} crosses capture plan")
        index = event.get("event_index")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not isinstance(effective_after, int)
            or index <= effective_after
        ):
            raise ValueError("follow-up event is not after the effective boundary")
        if offset == 0 and event.get("parent_event_id") != root_id:
            raise ValueError("first follow-up event must bind the frozen root event")
        events.append(event)
    accounting = _validate_accounting(result["model_call_accounting"])
    network_calls = result["network_calls"]
    if isinstance(network_calls, bool) or not isinstance(network_calls, int) or network_calls < 0:
        raise ValueError("network_calls must be a non-negative integer")
    provenance = result["capture_provenance"]
    if not isinstance(provenance, str) or not provenance:
        raise ValueError("capture_provenance must be a non-empty string")
    source_schema = result["source_export_schema"]
    if source_schema not in ALLOWED_SOURCE_SCHEMAS:
        raise ValueError("capture source export lacks a registered normalized schema")
    source_sha256 = result["source_export_sha256"]
    if (
        not isinstance(source_sha256, str)
        or len(source_sha256) != 64
        or any(character not in "0123456789abcdef" for character in source_sha256)
    ):
        raise ValueError("capture source export SHA-256 must be lowercase hex")
    return (
        tuple(events),
        accounting,
        network_calls,
        provenance,
        str(source_schema),
        source_sha256,
    )


def _atomic_publish(output: Path, manifest: Path, output_text: str, manifest_text: str) -> None:
    if output.exists() or manifest.exists():
        raise ValueError("refusing to overwrite capture artifacts")
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[Path, Path]] = []
    published: list[Path] = []
    try:
        for target, content in ((output, output_text), (manifest, manifest_text)):
            descriptor, name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
            )
            temporary = Path(name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            staged.append((temporary, target))
        for temporary, target in staged:
            os.link(temporary, target)
            published.append(target)
            temporary.unlink(missing_ok=True)
    except Exception:
        for temporary, _target in staged:
            temporary.unlink(missing_ok=True)
        for target in published:
            target.unlink(missing_ok=True)
        raise


def capture_followups(
    *,
    plan_path: Path,
    backend: CaptureBackend,
    backend_locator: str,
    output_path: Path,
    manifest_path: Path,
) -> dict[str, object]:
    plans = _load_jsonl(plan_path)
    by_session: dict[str, dict[str, object]] = {}
    call_totals: dict[str, int] = {}
    network_total = 0
    provenance_hashes: list[str] = []
    source_export_hashes: set[str] = set()
    source_export_schemas: set[str] = set()
    seen_plan_ids: set[str] = set()
    for plan in plans:
        if plan.get("schema_version") != CAPTURE_PLAN_SCHEMA_VERSION:
            raise ValueError("capture plan schema mismatch")
        plan_id = plan.get("plan_id")
        if not isinstance(plan_id, str) or not plan_id or plan_id in seen_plan_ids:
            raise ValueError("capture plan IDs must be non-empty and unique")
        seen_plan_ids.add(plan_id)
        raw_result = _mapping(backend(plan), "capture backend result")
        (
            events,
            accounting,
            network_calls,
            provenance,
            source_export_schema,
            source_export_sha256,
        ) = _validate_result(plan, raw_result)
        for role, count in accounting.items():
            call_totals[role] = call_totals.get(role, 0) + count
        network_total += network_calls
        provenance_hashes.append(hashlib.sha256(provenance.encode("utf-8")).hexdigest())
        source_export_hashes.add(source_export_sha256)
        source_export_schemas.add(source_export_schema)
        session_id = str(plan["session_id"])
        row = by_session.setdefault(
            session_id,
            {
                "schema_version": source_export_schema,
                "session_id": session_id,
                "stream_id": plan["stream_id"],
                "family_id": plan["family_id"],
                "events": [],
            },
        )
        if row["stream_id"] != plan["stream_id"] or row["family_id"] != plan["family_id"]:
            raise ValueError("capture plans disagree on session stream/family")
        if row["schema_version"] != source_export_schema:
            raise ValueError("capture plans mix source schemas within one session")
        row["events"].append(dict(_mapping(plan["root_event"], "root event")))
        row["events"].extend(dict(event) for event in events)

    normalized: list[dict[str, object]] = []
    for session_id in sorted(by_session):
        row = by_session[session_id]
        row["events"] = sorted(
            row["events"], key=lambda value: (value["event_index"], value["branch_id"], value["event_id"])
        )
        normalize_session_export(row)
        normalized.append(row)
    output_text = "".join(_canonical(row).decode("utf-8") + "\n" for row in normalized)
    manifest = {
        "schema_version": CAPTURE_RUN_MANIFEST_SCHEMA_VERSION,
        "plan_path": str(plan_path.resolve()),
        "plan_sha256": _file_sha256(plan_path),
        "backend_locator": backend_locator,
        "backend_contract_schema_version": CAPTURE_RESULT_SCHEMA_VERSION,
        "normalized_export_schema_versions": sorted(source_export_schemas),
        "source_export_sha256": sorted(source_export_hashes),
        "session_count": len(normalized),
        "candidate_branch_count": len(plans),
        "event_count": sum(len(row["events"]) for row in normalized),
        "model_call_accounting": dict(sorted(call_totals.items())),
        "model_calls": sum(call_totals.values()),
        "network_calls": network_total,
        "capture_provenance_sha256": hashlib.sha256(
            "\n".join(sorted(provenance_hashes)).encode("utf-8")
        ).hexdigest(),
        "output_sha256": hashlib.sha256(output_text.encode("utf-8")).hexdigest(),
        "followup_events_are_backend_observed": True,
        "real_claude_tap_coverage": (
            "VERIFIED"
            if source_export_schemas == {"claude-tap-normalized-v1"}
            else "UNVERIFIED"
        ),
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    _atomic_publish(output_path, manifest_path, output_text, manifest_text)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    result = capture_followups(
        plan_path=args.plan,
        backend=load_backend(args.backend),
        backend_locator=args.backend,
        output_path=args.output,
        manifest_path=args.manifest,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CAPTURE_RESULT_SCHEMA_VERSION",
    "CAPTURE_RUN_MANIFEST_SCHEMA_VERSION",
    "capture_followups",
    "load_backend",
]
