from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cmd_audit.adapters.session_lineage_cli import export_lineage
from experiments.v4_followup_capture import (
    CAPTURE_RESULT_SCHEMA_VERSION,
    capture_followups,
)
from experiments.v4_lineage_dataset import (
    build_capture_plan,
    merge_lineage_cases,
)


ROOT = Path(__file__).resolve().parents[2]
PREPARED = ROOT / "artifacts/ghost_public_call_v1/prepared_cases.jsonl"
ENRICHED = (
    ROOT
    / "artifacts/ghost_public_call_v1/runs/zero-call-typed-enrichment-20260820-v6/enriched.jsonl"
)


def _first_row(source: Path, target: Path) -> None:
    if not source.is_file():
        pytest.skip(f"frozen integration source unavailable: {source}")
    row = next(line for line in source.read_text(encoding="utf-8").splitlines() if line.strip())
    target.write_text(row + "\n", encoding="utf-8")


def _backend(plan):
    root = plan["root_event"]
    start = plan["exposure_start_event_index"]
    end = plan["exposure_end_event_index"]
    first_state = "followup-state-" + plan["plan_id"]
    used = list(plan["context_item_ids"])
    first = {
        "event_id": "context-" + plan["plan_id"],
        "session_id": plan["session_id"],
        "stream_id": plan["stream_id"],
        "branch_id": plan["branch_id"],
        "event_index": start,
        "turn": 1,
        "kind": "context",
        "parent_event_id": root["event_id"],
        "parent_state_sha256": root["state_sha256"],
        "state_sha256": first_state,
        "repair_intent_id": plan["repair_intent_id"],
        "context_item_ids": used,
        "usage_opportunity": True,
        "guard_passed": True,
        "rollback": False,
        "target_persistence": True,
    }
    events = [first]
    parent = first
    if end > start:
        events.append(
            {
                "event_id": "confirmation-" + plan["plan_id"],
                "session_id": plan["session_id"],
                "stream_id": plan["stream_id"],
                "branch_id": plan["branch_id"],
                "event_index": end,
                "turn": 2,
                "kind": "typed_confirmation",
                "parent_event_id": parent["event_id"],
                "parent_state_sha256": parent["state_sha256"],
                "state_sha256": "confirmed-state-" + plan["plan_id"],
                "repair_intent_id": plan["repair_intent_id"],
                "context_item_ids": used,
                "usage_opportunity": True,
                "guard_passed": True,
                "rollback": False,
                "target_persistence": True,
                "confirmation_signal": "delayed_confirmation",
            }
        )
    return {
        "schema_version": CAPTURE_RESULT_SCHEMA_VERSION,
        "plan_id": plan["plan_id"],
        "followup_events": events,
        "model_call_accounting": {"answer_generation": 1},
        "network_calls": 1,
        "capture_provenance": "fixture-observed-events-v1",
        "source_export_schema": "cmd-session-normalized-v1",
        "source_export_sha256": "f" * 64,
    }


def test_capture_plan_to_lineage_to_v4_merge_is_complete(tmp_path: Path) -> None:
    prepared = tmp_path / "prepared.jsonl"
    cases = tmp_path / "cases.jsonl"
    _first_row(PREPARED, prepared)
    _first_row(ENRICHED, cases)
    capture_plan = tmp_path / "capture-plan.jsonl"
    selections = tmp_path / "selections.jsonl"
    plan_manifest = tmp_path / "capture-plan.manifest.json"
    plan = build_capture_plan(
        prepared_path=prepared,
        cases_path=cases,
        capture_output=capture_plan,
        selections_output=selections,
        manifest_output=plan_manifest,
        exposure_events=2,
    )
    assert plan["case_count"] == 1
    assert plan["candidate_count"] >= 2
    assert plan["model_calls"] == 0
    plan_rows = [json.loads(line) for line in capture_plan.read_text().splitlines()]
    assert all(row["root_event"]["kind"] == "repair_selected" for row in plan_rows)
    assert all(row["required_followup_contract"]["absence_means_unknown"] for row in plan_rows)

    normalized = tmp_path / "normalized.jsonl"
    capture_manifest = tmp_path / "normalized.manifest.json"
    capture = capture_followups(
        plan_path=capture_plan,
        backend=_backend,
        backend_locator="tests.fixture:capture",
        output_path=normalized,
        manifest_path=capture_manifest,
    )
    assert capture["candidate_branch_count"] == plan["candidate_count"]
    assert capture["model_calls"] == plan["candidate_count"]
    assert capture["network_calls"] == plan["candidate_count"]
    assert capture["real_claude_tap_coverage"] == "UNVERIFIED"

    lineage = tmp_path / "lineage.jsonl"
    lineage_manifest = tmp_path / "lineage.manifest.json"
    projected = export_lineage(
        normalized,
        lineage,
        lineage_manifest,
        selections=selections,
    )
    assert projected["selection_count"] == plan["candidate_count"]
    assert projected["unknown_counts"]["delayed_confirmation"] == 0
    assert projected["real_claude_tap_coverage"] == "UNVERIFIED"

    merged = tmp_path / "merged.jsonl"
    merged_manifest = tmp_path / "merged.manifest.json"
    source_manifest = tmp_path / "source.manifest.json"
    source_manifest.write_text(
        json.dumps(
                {
                    "schema_version": "cmd-v4-materialized-merge-v1",
                    "output_sha256": hashlib.sha256(cases.read_bytes()).hexdigest(),
                "materialization_model_calls": 7,
                "materialization_network_calls": 7,
                "materialization_backend_locators": [
                    "experiments.v4_live_materialization:live_backend"
                ],
                "reference_is_fresh_replay": True,
            }
        ),
        encoding="utf-8",
    )
    result = merge_lineage_cases(
        cases_path=cases,
        lineage_path=lineage,
        output_path=merged,
        manifest_path=merged_manifest,
        source_materialization_manifest=source_manifest,
        capture_manifest=capture_manifest,
        lineage_manifest=lineage_manifest,
    )
    assert result["candidate_count"] == plan["candidate_count"]
    assert result["evidence_counts"]["delayed_confirmation"]["true"] == plan["candidate_count"]
    assert result["materialization_model_calls"] == 7 + plan["candidate_count"]
    assert result["materialization_network_calls"] == 7 + plan["candidate_count"]
    assert result["reference_is_fresh_replay"] is True
    row = json.loads(merged.read_text().splitlines()[0])
    assert all(item["delayed_confirmation"] is True for item in row["candidate_outcomes"])
    assert all(item["no_regression_observed"] is True for item in row["candidate_outcomes"])


def test_capture_backend_cannot_cross_branch(tmp_path: Path) -> None:
    prepared = tmp_path / "prepared.jsonl"
    cases = tmp_path / "cases.jsonl"
    _first_row(PREPARED, prepared)
    _first_row(ENRICHED, cases)
    capture_plan = tmp_path / "capture-plan.jsonl"
    build_capture_plan(
        prepared_path=prepared,
        cases_path=cases,
        capture_output=capture_plan,
        selections_output=tmp_path / "selections.jsonl",
        manifest_output=tmp_path / "plan.manifest.json",
    )

    def bad_backend(plan):
        result = _backend(plan)
        result["followup_events"][0]["branch_id"] = "other"
        return result

    with pytest.raises(ValueError, match="branch_id"):
        capture_followups(
            plan_path=capture_plan,
            backend=bad_backend,
            backend_locator="tests.fixture:bad",
            output_path=tmp_path / "normalized.jsonl",
            manifest_path=tmp_path / "normalized.manifest.json",
        )
    assert not (tmp_path / "normalized.jsonl").exists()


def test_capture_plan_refuses_overwrite(tmp_path: Path) -> None:
    prepared = tmp_path / "prepared.jsonl"
    cases = tmp_path / "cases.jsonl"
    _first_row(PREPARED, prepared)
    _first_row(ENRICHED, cases)
    output = tmp_path / "capture.jsonl"
    output.write_text("owned\n", encoding="utf-8")
    with pytest.raises(ValueError, match="overwrite"):
        build_capture_plan(
            prepared_path=prepared,
            cases_path=cases,
            capture_output=output,
            selections_output=tmp_path / "selections.jsonl",
            manifest_output=tmp_path / "manifest.json",
        )
    assert output.read_text(encoding="utf-8") == "owned\n"
