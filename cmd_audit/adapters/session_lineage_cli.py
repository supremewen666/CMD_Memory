"""Export structured session lineage and optional selection-bound evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Mapping, Sequence

from experiments.v4_prequential_runner import V4CandidateOutcome, V4PrequentialCase

from .session_log import (
    SESSION_LINEAGE_EVIDENCE_SCHEMA_VERSION,
    SESSION_LINEAGE_MANIFEST_SCHEMA_VERSION,
    SESSION_SELECTION_SCHEMA_VERSION,
    LineageSelection,
    load_lineage_selections,
    load_normalized_session_exports,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stage_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    staged = Path(staged_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
    except Exception:
        staged.unlink(missing_ok=True)
        raise
    return staged


def _publish_new(staged: Path, target: Path) -> None:
    """Publish without replacing an existing artifact, even on a race."""
    try:
        os.link(staged, target)
    except FileExistsError as error:
        raise ValueError(f"refusing to overwrite lineage output or manifest: {target}") from error
    finally:
        staged.unlink(missing_ok=True)


def _selection_mapping(selection: LineageSelection) -> dict[str, object]:
    return {
        "schema_version": SESSION_SELECTION_SCHEMA_VERSION,
        "session_id": selection.session_id,
        "family_id": selection.family_id,
        "branch_id": selection.branch_id,
        "repair_intent_id": selection.repair_intent_id,
        "selected_event_index": selection.selected_event_index,
        "effective_after_event_index": selection.effective_after_event_index,
        "annotation_ids": list(selection.annotation_ids),
        "changed_item_ids": list(selection.changed_item_ids),
        "exposure_start_event_index": selection.exposure_start_event_index,
        "exposure_end_event_index": selection.exposure_end_event_index,
    }


def _selection_evidence(trace: object, selection: LineageSelection) -> dict[str, object]:
    if selection.exposure_start_event_index is None or selection.exposure_end_event_index is None:
        raise ValueError("selection must provide an explicit exposure window")
    evidence = trace.project_followup_evidence(
        selection=selection,
        exposure_window=(selection.exposure_start_event_index, selection.exposure_end_event_index),
    )
    return {
        "selection": _selection_mapping(selection),
        "evidence_schema_version": SESSION_LINEAGE_EVIDENCE_SCHEMA_VERSION,
        "followup_evidence": evidence,
    }


def _counts(rows: Sequence[dict[str, object]]) -> tuple[dict[str, int], dict[str, int]]:
    names = ("annotation_consumed", "delayed_confirmation", "no_regression_observed")
    unknown = {name: 0 for name in names}
    confirmed = {name: 0 for name in names}
    for row in rows:
        for name, evidence in row["followup_evidence"].items():
            value = evidence["confirmed"]
            if value is None:
                unknown[name] += 1
            elif value is True:
                confirmed[name] += 1
    return unknown, confirmed


def export_lineage(
    source: Path,
    output: Path,
    manifest: Path,
    *,
    selections: Path | None = None,
) -> dict[str, object]:
    if output.exists() or manifest.exists():
        raise ValueError("refusing to overwrite lineage output or manifest")
    traces = load_normalized_session_exports(source)
    trace_by_session = {trace.session_id: trace for trace in traces}
    if len(trace_by_session) != len(traces):
        raise ValueError("normalized export session IDs must be unique")
    selection_rows = load_lineage_selections(selections) if selections is not None else ()
    projected_by_session: dict[str, list[dict[str, object]]] = {trace.session_id: [] for trace in traces}
    for selection in selection_rows:
        trace = trace_by_session.get(selection.session_id)
        if trace is None:
            raise ValueError("selection references a missing session")
        projected_by_session[selection.session_id].append(_selection_evidence(trace, selection))
    rows = []
    for trace in traces:
        projected = projected_by_session[trace.session_id]
        rows.append({
            "schema_version": SESSION_LINEAGE_EVIDENCE_SCHEMA_VERSION,
            "session_id": trace.session_id,
            "stream_id": trace.stream_id,
            "family_id": trace.family_id,
            "source_export_schema": trace.source_export_schema,
            "source_export_sha256": trace.source_export_sha256,
            "content_sha256": trace.content_sha256,
            "events": [row.__dict__ for row in trace.events],
            "followup_evidence": projected,
        })
    output_text = "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":"), default=list) for row in rows) + "\n"
    output_hash = hashlib.sha256(output_text.encode("utf-8")).hexdigest()
    evidence_rows = [item for row in rows for item in row["followup_evidence"]]
    candidate_keys = {
        (
            row["session_id"],
            row["family_id"],
            item["selection"]["branch_id"],
            item["selection"]["repair_intent_id"],
        )
        for row in rows
        for item in row["followup_evidence"]
    }
    family_keys = {
        row["family_id"] for row in rows for _ in row["followup_evidence"]
    }
    observed_candidates: set[tuple[str, str, str, str]] = set()
    observed_families: set[str] = set()
    observed_by_session: dict[tuple[str, str], list[bool]] = {}
    for row in rows:
        session_key = (str(row["session_id"]), str(row["family_id"]))
        for item in row["followup_evidence"]:
            selection = item["selection"]
            key = (
                str(row["session_id"]),
                str(row["family_id"]),
                str(selection["branch_id"]),
                str(selection["repair_intent_id"]),
            )
            is_observed = any(
                evidence["confirmed"] is not None
                for evidence in item["followup_evidence"].values()
            )
            observed_by_session.setdefault(session_key, []).append(is_observed)
            if is_observed:
                observed_candidates.add(key)
                observed_families.add(str(row["family_id"]))
    total_pairs = comparable_pairs = 0
    for flags in observed_by_session.values():
        for index, left in enumerate(flags):
            for right in flags[:index]:
                total_pairs += 1
                comparable_pairs += int(left and right)
    unknown_counts, confirmed_counts = _counts(evidence_rows) if evidence_rows else (
        {name: 0 for name in ("annotation_consumed", "delayed_confirmation", "no_regression_observed")},
        {name: 0 for name in ("annotation_consumed", "delayed_confirmation", "no_regression_observed")},
    )
    payload = {
        "schema_version": SESSION_LINEAGE_MANIFEST_SCHEMA_VERSION,
        "source_schema_versions": sorted({trace.source_export_schema for trace in traces}),
        "evidence_schema_version": SESSION_LINEAGE_EVIDENCE_SCHEMA_VERSION,
        "selection_schema_version": SESSION_SELECTION_SCHEMA_VERSION if selections is not None else None,
        "source_sha256": _sha256(source),
        "selection_source_sha256": None if selections is None else _sha256(selections),
        "output_sha256": output_hash,
        "session_count": len(rows),
        "event_count": sum(len(row["events"]) for row in rows),
        "selection_count": len(evidence_rows),
        "evidence_count": len(evidence_rows) * 3,
        "unknown_counts": unknown_counts,
        "confirmed_counts": confirmed_counts,
        "coverage": {
            "candidate": {
                "observed": len(observed_candidates),
                "registered_total": len(candidate_keys),
                "value": (
                    None
                    if not candidate_keys
                    else len(observed_candidates) / len(candidate_keys)
                ),
            },
            "family": {
                "observed": len(observed_families),
                "registered_total": len(family_keys),
                "value": (
                    None
                    if not family_keys
                    else len(observed_families) / len(family_keys)
                ),
            },
            "pairwise_comparable": {
                "comparable": comparable_pairs,
                "total": total_pairs,
                "value": None if total_pairs == 0 else comparable_pairs / total_pairs,
            },
        },
        "coverage_audit_empty": not bool(evidence_rows),
        "real_claude_tap_coverage": (
            "VERIFIED"
            if {trace.source_export_schema for trace in traces}
            == {"claude-tap-normalized-v1"}
            else "UNVERIFIED"
        ),
        "model_calls": 0,
        "network_calls": 0,
    }
    manifest_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    staged_output = staged_manifest = None
    published_output = False
    try:
        staged_output = _stage_text(output, output_text)
        staged_manifest = _stage_text(manifest, manifest_text)
        _publish_new(staged_output, output)
        published_output = True
        _publish_new(staged_manifest, manifest)
    except Exception:
        if staged_output is not None:
            staged_output.unlink(missing_ok=True)
        if staged_manifest is not None:
            staged_manifest.unlink(missing_ok=True)
        if published_output:
            output.unlink(missing_ok=True)
        raise
    return payload


def merge_followup_evidence_into_v4_case(
    case_mapping: Mapping[str, object],
    selection_record: Mapping[str, object],
) -> dict[str, object]:
    """Merge one branch-bound follow-up record into existing V4 typed fields."""
    case = V4PrequentialCase.from_mapping(case_mapping)
    selection = selection_record.get("selection")
    evidence = selection_record.get("followup_evidence")
    if set(selection_record) != {"selection", "evidence_schema_version", "followup_evidence"}:
        raise ValueError("lineage selection record is not closed")
    if not isinstance(selection, Mapping) or not isinstance(evidence, Mapping):
        raise ValueError("lineage selection record is not closed")
    selection_obj = LineageSelection.from_mapping(selection)
    if selection_record.get("evidence_schema_version") != SESSION_LINEAGE_EVIDENCE_SCHEMA_VERSION:
        raise ValueError("lineage evidence schema mismatch")
    if selection_obj.session_id != case.case_id or selection_obj.family_id != case.family_id:
        raise ValueError("lineage selection crosses V4 case/family")
    if selection_obj.selected_event_index != case.context.event_index:
        raise ValueError("selection is not bound to the V4 pre-action event")
    if selection_obj.effective_after_event_index <= case.context.event_index:
        raise ValueError("follow-up effective-after is not post-selection")
    if selection_obj.exposure_start_event_index is None or selection_obj.exposure_start_event_index <= selection_obj.effective_after_event_index:
        raise ValueError("follow-up exposure window is not post-effective-after")
    expected_evidence_names = {
        "annotation_consumed", "delayed_confirmation", "no_regression_observed",
    }
    if set(evidence) != expected_evidence_names:
        raise ValueError("follow-up evidence mapping is not closed")
    intent_id = selection_obj.repair_intent_id
    outcomes = {row.intent_id: row for row in case.candidate_outcomes}
    outcome = outcomes.get(intent_id)
    if outcome is None:
        raise ValueError("lineage selection candidate is absent from V4 case")
    values = outcome.to_mapping()
    provenance = dict(outcome.typed_evidence_provenance or {})
    provenance["session_lineage"] = {
        "evidence_schema_version": SESSION_LINEAGE_EVIDENCE_SCHEMA_VERSION,
        "session_id": selection_obj.session_id,
        "family_id": selection_obj.family_id,
        "branch_id": selection_obj.branch_id,
        "selected_event_index": selection_obj.selected_event_index,
        "effective_after_event_index": selection_obj.effective_after_event_index,
    }
    for evidence_name in ("annotation_consumed", "delayed_confirmation", "no_regression_observed"):
        item = evidence.get(evidence_name)
        if not isinstance(item, Mapping) or set(item) != {
            "kind", "confirmed", "reason", "source_event_id",
            "observed_at_event_index", "state_sha256", "schema_version",
        }:
            raise ValueError("follow-up evidence item is not closed")
        if item.get("schema_version") != SESSION_LINEAGE_EVIDENCE_SCHEMA_VERSION:
            raise ValueError("follow-up evidence item schema mismatch")
        value = item["confirmed"]
        if value is not None and not isinstance(value, bool):
            raise ValueError("follow-up evidence confirmed must be bool or unknown")
        values[evidence_name] = value
        provenance["session_lineage"][f"{evidence_name}_source_event_id"] = item.get("source_event_id")
    values["typed_evidence_provenance"] = provenance
    updated = V4CandidateOutcome.from_mapping(values)
    result = dict(case_mapping)
    result["candidate_outcomes"] = [updated.to_mapping() if row.intent_id == intent_id else row.to_mapping() for row in case.candidate_outcomes]
    V4PrequentialCase.from_mapping(result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--selections", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(export_lineage(args.source, args.output, args.manifest, selections=args.selections), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
