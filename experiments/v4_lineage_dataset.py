#!/usr/bin/env python3
"""Build and merge the branch-complete follow-up dataset used by E2/E4.

The old V4 materializer records the immediate executed state and shadow score.
That is enough for actionability, but it is not a session trace.  This module
adds the missing *experiment plumbing* without manufacturing follow-up facts:

``plan``
    Re-executes every frozen candidate locally, verifies the resulting state
    against the materialized outcome, and writes a closed capture request plus
    a :class:`~cmd_audit.adapters.session_log.LineageSelection` for every
    candidate branch.

``merge``
    Applies a selection-bound lineage evidence sidecar to the matching V4
    candidate outcomes and writes a new, hash-bound case stream.  Unknown
    follow-up evidence remains ``None``.

No command in this module calls a model or the network.  A separate capture
backend must turn the capture plan into a normalized session export containing
real later events before ``cmd_audit.adapters.session_lineage_cli`` can project
the evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Iterable, Mapping, Sequence

from cmd_audit.adapters.session_lineage_cli import (
    merge_followup_evidence_into_v4_case,
)
from cmd_audit.adapters.session_log import (
    SESSION_LINEAGE_EVIDENCE_SCHEMA_VERSION,
    SESSION_SELECTION_SCHEMA_VERSION,
)
from cmd_audit.counterfactual.repair_state import (
    VISIBLE_DISPOSITIONS,
    initial_state_from_runtime_case,
)
from cmd_audit.counterfactual.successor_state_executor import execute_program
from cmd_audit.repair.parametric_policy import compile_intent
from experiments.v4_live_materialization import (
    _changed_item_ids,
    validate_live_input,
)
from experiments.v4_prequential_runner import V4PrequentialCase


CAPTURE_PLAN_SCHEMA_VERSION = "cmd-v4-followup-capture-plan-v1"
CAPTURE_PLAN_MANIFEST_SCHEMA_VERSION = "cmd-v4-followup-capture-plan-manifest-v1"
LINEAGE_MERGE_MANIFEST_SCHEMA_VERSION = "cmd-v4-lineage-merge-manifest-v1"
FRESH_SOURCE_MATERIALIZATION_SCHEMAS = frozenset(
    {"cmd-v4-materialized-merge-v1"}
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _non_negative_count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _optional_non_negative_count(value: object, name: str) -> int:
    if value is None:
        return 0
    return _non_negative_count(value, name)


def _load_jsonl(path: Path) -> tuple[Mapping[str, object], ...]:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows: list[Mapping[str, object]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSONL at {path}:{line_number}") from error
        rows.append(_mapping(value, f"row {line_number}"))
    if not rows:
        raise ValueError(f"JSONL input is empty: {path}")
    return tuple(rows)


def _stage(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        staged.unlink(missing_ok=True)
        raise
    return staged


def _publish(staged: Path, target: Path) -> None:
    try:
        os.link(staged, target)
    except FileExistsError as error:
        raise ValueError(f"refusing to overwrite artifact: {target}") from error
    finally:
        staged.unlink(missing_ok=True)


def _publish_group(values: Sequence[tuple[Path, str]]) -> None:
    for path, _content in values:
        if path.exists():
            raise ValueError(f"refusing to overwrite artifact: {path}")
    staged: list[tuple[Path, Path]] = []
    published: list[Path] = []
    try:
        for path, content in values:
            staged.append((_stage(path, content), path))
        for temporary, path in staged:
            _publish(temporary, path)
            published.append(path)
    except Exception:
        for temporary, _path in staged:
            temporary.unlink(missing_ok=True)
        for path in published:
            path.unlink(missing_ok=True)
        raise


def _jsonl(rows: Iterable[Mapping[str, object]]) -> str:
    materialized = tuple(rows)
    return "".join(_canonical(row).decode("utf-8") + "\n" for row in materialized)


def _branch_id(case_id: str, intent_id: str) -> str:
    return "branch-" + hashlib.sha256(
        f"cmd-v4-lineage-v1|{case_id}|{intent_id}".encode("utf-8")
    ).hexdigest()


def _stream_id(case_id: str, family_id: str) -> str:
    return "stream-" + hashlib.sha256(
        f"cmd-v4-lineage-v1|{family_id}|{case_id}".encode("utf-8")
    ).hexdigest()


def _annotation_ids(
    *, case_id: str, intent_id: str, effect: str, changed_item_ids: Sequence[str]
) -> tuple[tuple[str, ...], dict[str, list[str]]]:
    if effect != "annotate_conflict":
        return (), {}
    annotations = []
    bindings: dict[str, list[str]] = {}
    for item_id in sorted(changed_item_ids):
        annotation_id = "annotation-" + hashlib.sha256(
            f"cmd-v4-annotation-v1|{case_id}|{intent_id}|{item_id}".encode("utf-8")
        ).hexdigest()
        annotations.append(annotation_id)
        bindings[annotation_id] = [item_id]
    return tuple(annotations), bindings


def build_capture_plan(
    *,
    prepared_path: Path,
    cases_path: Path,
    capture_output: Path,
    selections_output: Path,
    manifest_output: Path,
    exposure_events: int = 2,
) -> dict[str, object]:
    """Freeze one capture request and one selection for every candidate."""
    if isinstance(exposure_events, bool) or not isinstance(exposure_events, int) or exposure_events < 1:
        raise ValueError("exposure_events must be a positive integer")
    prepared_rows = _load_jsonl(prepared_path)
    case_rows = _load_jsonl(cases_path)
    prepared_by_id = {str(row.get("case_id") or ""): row for row in prepared_rows}
    case_by_id = {str(row.get("case_id") or ""): row for row in case_rows}
    if "" in prepared_by_id or len(prepared_by_id) != len(prepared_rows):
        raise ValueError("prepared case IDs must be non-empty and unique")
    if "" in case_by_id or len(case_by_id) != len(case_rows):
        raise ValueError("materialized case IDs must be non-empty and unique")
    if set(prepared_by_id) != set(case_by_id):
        missing = sorted(set(prepared_by_id) - set(case_by_id))
        extra = sorted(set(case_by_id) - set(prepared_by_id))
        raise ValueError(f"prepared/materialized case coverage mismatch: missing={missing}, extra={extra}")

    captures: list[dict[str, object]] = []
    selections: list[dict[str, object]] = []
    candidate_count_by_case: dict[str, int] = {}
    for raw_case in case_rows:
        case = V4PrequentialCase.from_mapping(raw_case)
        frozen = validate_live_input(prepared_by_id[case.case_id])
        if frozen.family_id != case.family_id or frozen.context.event_index != case.context.event_index:
            raise ValueError("prepared/materialized family or event index mismatch")
        initial = initial_state_from_runtime_case(frozen.runtime_case)
        outcomes = {row.intent_id: row for row in case.candidate_outcomes}
        candidate_count_by_case[case.case_id] = len(case.intents)
        for intent in case.intents:
            result = execute_program(
                compile_intent(intent, graph=frozen.graph),
                frozen.runtime_case,
                initial,
                graph=frozen.graph,
                expected_graph_sha256=frozen.graph.graph_sha256,
                expected_protocol_manifest_sha256=frozen.graph.protocol_manifest_sha256,
            )
            outcome = outcomes[intent.intent_id]
            changed = tuple(sorted(_changed_item_ids(initial, result.state)))
            if outcome.changed_item_ids is None:
                raise ValueError(
                    "materialized candidate lacks changed_item_ids; rerun typed live materialization"
                )
            if tuple(outcome.changed_item_ids) != changed or outcome.changed_item_count != len(changed):
                raise ValueError("materialized candidate changed-item evidence disagrees with local replay")
            provenance = outcome.typed_evidence_provenance or {}
            recorded_state = provenance.get("state_hash") or provenance.get("executed_state_sha256")
            if recorded_state is not None and recorded_state != result.state.state_hash:
                raise ValueError("materialized candidate state hash disagrees with local replay")

            annotations, bindings = _annotation_ids(
                case_id=case.case_id,
                intent_id=intent.intent_id,
                effect=intent.effect,
                changed_item_ids=changed,
            )
            selected = case.context.event_index
            effective_after = selected + 1
            exposure_start = effective_after + 1
            exposure_end = exposure_start + exposure_events - 1
            branch_id = _branch_id(case.case_id, intent.intent_id)
            stream_id = _stream_id(case.case_id, case.family_id)
            root_event_id = f"selection-{intent.intent_id}"
            visible_item_ids = sorted(
                row.item_id
                for row in result.state.items
                if row.disposition in VISIBLE_DISPOSITIONS
            )
            root_event = {
                "event_id": root_event_id,
                "session_id": case.case_id,
                "stream_id": stream_id,
                "branch_id": branch_id,
                "event_index": selected,
                "turn": 0,
                "kind": "repair_selected",
                "state_sha256": result.state.state_hash,
                "repair_intent_id": intent.intent_id,
                "changed_item_ids": list(changed),
                "created_annotation_ids": list(annotations),
                "annotation_item_bindings": bindings,
                "rollback": outcome.rolled_back,
                "guard_passed": outcome.valid and not outcome.rolled_back,
                "locality_cost": outcome.locality_cost,
                "effective_after_event_index": effective_after,
                "root": True,
            }
            plan_id = _sha256(
                {
                    "schema_version": CAPTURE_PLAN_SCHEMA_VERSION,
                    "case_id": case.case_id,
                    "intent_id": intent.intent_id,
                    "branch_id": branch_id,
                    "state_sha256": result.state.state_hash,
                }
            )
            captures.append(
                {
                    "schema_version": CAPTURE_PLAN_SCHEMA_VERSION,
                    "plan_id": plan_id,
                    "session_id": case.case_id,
                    "stream_id": stream_id,
                    "family_id": case.family_id,
                    "case_id": case.case_id,
                    "branch_id": branch_id,
                    "repair_intent_id": intent.intent_id,
                    "repair_effect": intent.effect,
                    "query": frozen.runtime_case.query,
                    "candidate_context": result.state.rendered_context,
                    "context_item_ids": visible_item_ids,
                    "changed_item_ids": list(changed),
                    "annotation_ids": list(annotations),
                    "annotation_item_bindings": bindings,
                    "root_event": root_event,
                    "effective_after_event_index": effective_after,
                    "exposure_start_event_index": exposure_start,
                    "exposure_end_event_index": exposure_end,
                    "required_followup_contract": {
                        "source_schema_version": "cmd-session-normalized-v1",
                        "must_bind_parent_event_id": root_event_id,
                        "must_bind_parent_state_sha256": result.state.state_hash,
                        "must_record_context_or_retrieved_item_ids": True,
                        "must_record_usage_opportunity": True,
                        "confirmation_signal_is_optional_but_typed": True,
                        "absence_means_unknown": True,
                    },
                    "source_case_sha256": _sha256(raw_case),
                }
            )
            selections.append(
                {
                    "schema_version": SESSION_SELECTION_SCHEMA_VERSION,
                    "session_id": case.case_id,
                    "family_id": case.family_id,
                    "branch_id": branch_id,
                    "repair_intent_id": intent.intent_id,
                    "selected_event_index": selected,
                    "effective_after_event_index": effective_after,
                    "annotation_ids": list(annotations),
                    "changed_item_ids": list(changed),
                    "exposure_start_event_index": exposure_start,
                    "exposure_end_event_index": exposure_end,
                }
            )

    capture_text = _jsonl(captures)
    selection_text = _jsonl(selections)
    manifest = {
        "schema_version": CAPTURE_PLAN_MANIFEST_SCHEMA_VERSION,
        "prepared_path": str(prepared_path.resolve()),
        "prepared_sha256": _file_sha256(prepared_path),
        "cases_path": str(cases_path.resolve()),
        "cases_sha256": _file_sha256(cases_path),
        "case_count": len(case_rows),
        "candidate_count": len(captures),
        "candidate_count_by_case_sha256": _sha256(candidate_count_by_case),
        "capture_plan_schema_version": CAPTURE_PLAN_SCHEMA_VERSION,
        "capture_plan_sha256": hashlib.sha256(capture_text.encode("utf-8")).hexdigest(),
        "selection_schema_version": SESSION_SELECTION_SCHEMA_VERSION,
        "selections_sha256": hashlib.sha256(selection_text.encode("utf-8")).hexdigest(),
        "exposure_events": exposure_events,
        "model_calls": 0,
        "network_calls": 0,
        "followup_evidence_status": "PENDING_REAL_CAPTURE",
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    _publish_group(
        (
            (capture_output, capture_text),
            (selections_output, selection_text),
            (manifest_output, manifest_text),
        )
    )
    return manifest


def _lineage_records(path: Path) -> dict[tuple[str, str], Mapping[str, object]]:
    records: dict[tuple[str, str], Mapping[str, object]] = {}
    for trace in _load_jsonl(path):
        if trace.get("schema_version") != SESSION_LINEAGE_EVIDENCE_SCHEMA_VERSION:
            raise ValueError("lineage sidecar schema mismatch")
        evidence = trace.get("followup_evidence")
        if not isinstance(evidence, list):
            raise ValueError("lineage sidecar followup_evidence must be a list")
        for raw in evidence:
            row = _mapping(raw, "lineage selection evidence")
            selection = _mapping(row.get("selection"), "lineage selection")
            key = (str(selection.get("session_id") or ""), str(selection.get("repair_intent_id") or ""))
            if "" in key or key in records:
                raise ValueError("lineage evidence selection keys must be non-empty and unique")
            records[key] = row
    return records


def merge_lineage_cases(
    *,
    cases_path: Path,
    lineage_path: Path,
    output_path: Path,
    manifest_path: Path,
    source_materialization_manifest: Path,
    capture_manifest: Path,
    lineage_manifest: Path,
) -> dict[str, object]:
    """Bulk merge every registered lineage record into its V4 case."""
    source_calls = 0
    source_network_calls = 0
    capture_calls = 0
    capture_network_calls = 0
    source_manifest_hash = None
    source_backend_locators: tuple[str, ...] = ()
    source_reference_is_fresh_replay = False
    capture_manifest_hash = None
    lineage_manifest_hash = None
    if source_materialization_manifest is not None:
        source_raw = _mapping(
            json.loads(source_materialization_manifest.read_text(encoding="utf-8")),
            "source materialization manifest",
        )
        if source_raw.get("output_sha256") != _file_sha256(cases_path):
            raise ValueError("source materialization manifest does not bind cases")
        source_calls = _non_negative_count(
            source_raw.get("materialization_model_calls"),
            "source materialization model calls",
        )
        source_network_calls = _optional_non_negative_count(
            source_raw.get("materialization_network_calls"),
            "source materialization network calls",
        )
        raw_locators = source_raw.get("materialization_backend_locators", ())
        if not isinstance(raw_locators, Sequence) or isinstance(
            raw_locators, (str, bytes)
        ):
            raise ValueError("source materialization backend locators must be a sequence")
        if any(not isinstance(value, str) or not value for value in raw_locators):
            raise ValueError("source materialization backend locators are invalid")
        source_backend_locators = tuple(sorted(set(raw_locators)))
        raw_fresh = source_raw.get("reference_is_fresh_replay", False)
        if not isinstance(raw_fresh, bool):
            raise ValueError("source materialization fresh-replay flag must be boolean")
        if (
            raw_fresh
            and source_raw.get("schema_version")
            not in FRESH_SOURCE_MATERIALIZATION_SCHEMAS
        ):
            raise ValueError(
                "fresh replay requires a recognized source materialization schema"
            )
        source_reference_is_fresh_replay = raw_fresh
        source_manifest_hash = _file_sha256(source_materialization_manifest)
    if lineage_manifest is not None:
        lineage_raw = _mapping(
            json.loads(lineage_manifest.read_text(encoding="utf-8")),
            "lineage manifest",
        )
        if lineage_raw.get("output_sha256") != _file_sha256(lineage_path):
            raise ValueError("lineage manifest does not bind lineage evidence")
        lineage_manifest_hash = _file_sha256(lineage_manifest)
    else:
        lineage_raw = None
    if capture_manifest is not None:
        capture_raw = _mapping(
            json.loads(capture_manifest.read_text(encoding="utf-8")),
            "capture manifest",
        )
        capture_calls = _non_negative_count(
            capture_raw.get("model_calls"), "capture model calls"
        )
        capture_network_calls = _non_negative_count(
            capture_raw.get("network_calls"), "capture network calls"
        )
        if lineage_raw is None:
            raise ValueError("capture manifest requires the lineage manifest binding")
        if capture_raw.get("output_sha256") != lineage_raw.get("source_sha256"):
            raise ValueError("capture and lineage manifests do not form a hash chain")
        capture_manifest_hash = _file_sha256(capture_manifest)
    case_rows = _load_jsonl(cases_path)
    records = _lineage_records(lineage_path)
    merged_rows: list[dict[str, object]] = []
    consumed: set[tuple[str, str]] = set()
    evidence_counts = {
        name: {"true": 0, "false": 0, "unknown": 0}
        for name in (
            "annotation_consumed",
            "delayed_confirmation",
            "no_regression_observed",
        )
    }
    for raw in case_rows:
        case = V4PrequentialCase.from_mapping(raw)
        current = dict(raw)
        for intent in case.intents:
            key = (case.case_id, intent.intent_id)
            record = records.get(key)
            if record is None:
                raise ValueError(f"lineage sidecar is missing candidate selection: {key}")
            current = merge_followup_evidence_into_v4_case(current, record)
            consumed.add(key)
            evidence = _mapping(record["followup_evidence"], "followup evidence")
            for name in evidence_counts:
                value = _mapping(evidence[name], name).get("confirmed")
                bucket = "unknown" if value is None else "true" if value is True else "false"
                evidence_counts[name][bucket] += 1
        merged_rows.append(current)
    extra = sorted(set(records) - consumed)
    if extra:
        raise ValueError(f"lineage sidecar contains candidates outside case stream: {extra}")
    output_text = _jsonl(merged_rows)
    candidate_count = len(consumed)
    manifest = {
        "schema_version": LINEAGE_MERGE_MANIFEST_SCHEMA_VERSION,
        "case_schema_version": V4PrequentialCase.from_mapping(merged_rows[0]).to_mapping()["schema_version"],
        "cases_path": str(cases_path.resolve()),
        "cases_sha256": _file_sha256(cases_path),
        "lineage_path": str(lineage_path.resolve()),
        "lineage_sha256": _file_sha256(lineage_path),
        "source_materialization_manifest": (
            None
            if source_materialization_manifest is None
            else str(source_materialization_manifest.resolve())
        ),
        "source_materialization_manifest_sha256": source_manifest_hash,
        "source_materialization_backend_locators": list(source_backend_locators),
        "capture_manifest": (
            None if capture_manifest is None else str(capture_manifest.resolve())
        ),
        "capture_manifest_sha256": capture_manifest_hash,
        "lineage_manifest": (
            None if lineage_manifest is None else str(lineage_manifest.resolve())
        ),
        "lineage_manifest_sha256": lineage_manifest_hash,
        "output_path": str(output_path.resolve()),
        "output_sha256": hashlib.sha256(output_text.encode("utf-8")).hexdigest(),
        "case_count": len(merged_rows),
        "candidate_count": candidate_count,
        "evidence_counts": evidence_counts,
        "model_calls_new": 0,
        "network_calls_new": 0,
        "source_materialization_model_calls": source_calls,
        "followup_capture_model_calls": capture_calls,
        "materialization_model_calls": source_calls + capture_calls,
        "source_materialization_network_calls": source_network_calls,
        "followup_capture_network_calls": capture_network_calls,
        "materialization_network_calls": source_network_calls + capture_network_calls,
        "reference_is_fresh_replay": (
            source_reference_is_fresh_replay
            and source_calls > 0
            and source_backend_locators
            == ("experiments.v4_live_materialization:live_backend",)
        ),
        "unknown_is_preserved": True,
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    _publish_group(((output_path, output_text), (manifest_path, manifest_text)))
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--prepared", type=Path, required=True)
    plan.add_argument("--cases", type=Path, required=True)
    plan.add_argument("--capture-output", type=Path, required=True)
    plan.add_argument("--selections-output", type=Path, required=True)
    plan.add_argument("--manifest", type=Path, required=True)
    plan.add_argument("--exposure-events", type=int, default=2)
    merge = subparsers.add_parser("merge")
    merge.add_argument("--cases", type=Path, required=True)
    merge.add_argument("--lineage", type=Path, required=True)
    merge.add_argument("--output", type=Path, required=True)
    merge.add_argument("--manifest", type=Path, required=True)
    merge.add_argument("--source-materialization-manifest", type=Path, required=True)
    merge.add_argument("--capture-manifest", type=Path, required=True)
    merge.add_argument("--lineage-manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.action == "plan":
        result = build_capture_plan(
            prepared_path=args.prepared,
            cases_path=args.cases,
            capture_output=args.capture_output,
            selections_output=args.selections_output,
            manifest_output=args.manifest,
            exposure_events=args.exposure_events,
        )
    else:
        result = merge_lineage_cases(
            cases_path=args.cases,
            lineage_path=args.lineage,
            output_path=args.output,
            manifest_path=args.manifest,
            source_materialization_manifest=args.source_materialization_manifest,
            capture_manifest=args.capture_manifest,
            lineage_manifest=args.lineage_manifest,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CAPTURE_PLAN_MANIFEST_SCHEMA_VERSION",
    "CAPTURE_PLAN_SCHEMA_VERSION",
    "LINEAGE_MERGE_MANIFEST_SCHEMA_VERSION",
    "build_capture_plan",
    "merge_lineage_cases",
]
