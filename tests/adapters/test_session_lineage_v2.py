from __future__ import annotations

import json
import hashlib
import pytest
from pathlib import Path

from cmd_audit.adapters.session_log import (
    LineageSelection, SessionLogError, normalize_session_export,
    load_lineage_selections,
)
from cmd_audit.adapters.session_lineage_cli import export_lineage


def _export():
    return {"schema_version": "claude-tap-normalized-v1", "session_id": "s", "stream_id": "st", "family_id": "f", "events": [
        {"event_id": "e0", "session_id": "s", "stream_id": "st", "branch_id": "b", "event_index": 0, "turn": 0, "kind": "message", "state_sha256": "s0", "root": True, "repair_intent_id": "i"},
        {"event_id": "e1", "session_id": "s", "stream_id": "st", "branch_id": "b", "event_index": 1, "turn": 1, "kind": "context", "parent_event_id": "e0", "parent_state_sha256": "s0", "state_sha256": "s1", "retrieved_item_ids": ["a"], "created_annotation_ids": ["ann"], "annotation_item_bindings": {"ann": ["a"]}},
        {"event_id": "e2", "session_id": "s", "stream_id": "st", "branch_id": "b", "event_index": 2, "turn": 2, "kind": "message", "parent_event_id": "e1", "parent_state_sha256": "s1", "state_sha256": "s2", "context_item_ids": ["a"]},
    ]}


def test_normalized_trace_preserves_parent_hash_and_structured_binding():
    trace = normalize_session_export(_export())
    assert trace.events[2].context_item_ids == ("a",)
    assert trace.events[1].created_annotation_ids == ("ann",)
    evidence = trace.project_followup_evidence(selection=__import__("cmd_audit.adapters.session_log", fromlist=["LineageSelection"]).LineageSelection("s", "f", "b", "i", 0, 1, annotation_ids=("ann",)), exposure_window=(2, 2))
    assert evidence["annotation_consumed"]["confirmed"] is True
    assert evidence["delayed_confirmation"]["confirmed"] is None


def test_unknown_schema_and_broken_parent_fail_closed():
    with pytest.raises(SessionLogError):
        normalize_session_export(_export() | {"schema_version": "legacy-jsonl-v1"})
    bad = _export(); bad["events"][2]["parent_state_sha256"] = "wrong"
    with pytest.raises(SessionLogError, match="parent"):
        normalize_session_export(bad)


def test_event_types_ids_and_registered_signals_fail_closed():
    bad = _export(); bad["events"][1]["event_index"] = True
    with pytest.raises(SessionLogError, match="index/turn"):
        normalize_session_export(bad)
    bad = _export(); bad["events"][1]["root"] = 1
    with pytest.raises(SessionLogError, match="must be boolean"):
        normalize_session_export(bad)
    bad = _export(); bad["events"][1]["changed_item_ids"] = [1]
    with pytest.raises(SessionLogError, match="strings"):
        normalize_session_export(bad)
    bad = _export(); bad["events"][2]["confirmation_signal"] = "unregistered"
    with pytest.raises(SessionLogError, match="confirmation signal"):
        normalize_session_export(bad)
    bad = _export(); bad["events"][1]["state_sha256"] = None
    with pytest.raises(SessionLogError, match="state hash"):
        normalize_session_export(bad)


def test_reordered_and_duplicate_ids_fail_closed():
    bad = _export(); bad["events"] = [bad["events"][1], bad["events"][0], bad["events"][2]]
    with pytest.raises(SessionLogError, match="out of order"):
        normalize_session_export(bad)


def _branched_export():
    return {"schema_version": "claude-tap-normalized-v1", "session_id": "s", "stream_id": "st", "family_id": "f", "events": [
        {"event_id": "e0", "session_id": "s", "stream_id": "st", "branch_id": "b1", "event_index": 0, "turn": 0, "kind": "message", "state_sha256": "s0", "root": True, "repair_intent_id": "i1"},
        {"event_id": "e1", "session_id": "s", "stream_id": "st", "branch_id": "b1", "event_index": 1, "turn": 1, "kind": "context", "parent_event_id": "e0", "parent_state_sha256": "s0", "state_sha256": "s1", "created_annotation_ids": ["ann1"], "annotation_item_bindings": {"ann1": ["item-a"]}},
        {"event_id": "e2", "session_id": "s", "stream_id": "st", "branch_id": "b2", "event_index": 2, "turn": 2, "kind": "message", "parent_event_id": "e0", "parent_state_sha256": "s0", "state_sha256": "s2", "root": True, "fork_parent_event_id": "e0", "repair_intent_id": "i2", "created_annotation_ids": ["ann2"], "annotation_item_bindings": {"ann2": ["item-b"]}},
        {"event_id": "e3", "session_id": "s", "stream_id": "st", "branch_id": "b1", "event_index": 3, "turn": 3, "kind": "context", "parent_event_id": "e1", "parent_state_sha256": "s1", "state_sha256": "s3", "context_item_ids": ["item-a"], "usage_opportunity": False},
        {"event_id": "e4", "session_id": "s", "stream_id": "st", "branch_id": "b1", "event_index": 4, "turn": 4, "kind": "typed_confirmation", "parent_event_id": "e3", "parent_state_sha256": "s3", "state_sha256": "s4", "context_item_ids": ["item-a"], "repair_intent_id": "i1", "confirmation_signal": "delayed_confirmation", "usage_opportunity": True, "guard_passed": True},
        {"event_id": "e5", "session_id": "s", "stream_id": "st", "branch_id": "b2", "event_index": 5, "turn": 5, "kind": "typed_confirmation", "parent_event_id": "e2", "parent_state_sha256": "s2", "state_sha256": "s5", "context_item_ids": ["item-b"], "repair_intent_id": "i2", "confirmation_signal": "typed_outcome", "usage_opportunity": True, "guard_passed": False, "rollback": True, "target_loss": True},
    ]}


def _selection(session: str, family: str, branch: str, intent: str, selected: int, effective: int, start: int, end: int, annotations=()):
    return {"schema_version": "cmd-session-lineage-selection-v1", "session_id": session, "family_id": family, "branch_id": branch, "repair_intent_id": intent, "selected_event_index": selected, "effective_after_event_index": effective, "annotation_ids": list(annotations), "changed_item_ids": [], "exposure_start_event_index": start, "exposure_end_event_index": end}


def test_branch_local_typed_evidence_and_three_valued_outcomes():
    trace = normalize_session_export(_branched_export())
    first = trace.project_followup_evidence(selection=LineageSelection.from_mapping(_selection("s", "f", "b1", "i1", 0, 1, 2, 4, ("ann1",))), exposure_window=(2, 4))
    assert first["annotation_consumed"]["confirmed"] is True
    assert first["delayed_confirmation"]["confirmed"] is True
    assert first["no_regression_observed"]["confirmed"] is True
    second = trace.project_followup_evidence(selection=LineageSelection.from_mapping(_selection("s", "f", "b2", "i2", 2, 3, 4, 5)), exposure_window=(4, 5))
    assert second["delayed_confirmation"]["confirmed"] is False
    assert second["no_regression_observed"]["confirmed"] is False


def test_annotation_ids_are_not_item_ids_and_no_event_is_unknown():
    trace = normalize_session_export(_branched_export())
    selection = LineageSelection.from_mapping(_selection("s", "f", "b1", "i1", 0, 1, 2, 4, ("item-a",)))
    evidence = trace.project_followup_evidence(selection=selection, exposure_window=(2, 4))
    assert evidence["annotation_consumed"]["confirmed"] is None
    empty_selection = LineageSelection("s", "f", "b1", "i1", 0, 1, annotation_ids=("item-a",))
    empty = trace.project_followup_evidence(selection=empty_selection, exposure_window=(2, 2))
    assert empty["no_regression_observed"]["confirmed"] is None


def test_future_or_other_intent_events_do_not_supply_followup_evidence():
    raw = _branched_export()
    raw["events"][3]["repair_intent_id"] = "other-intent"
    raw["events"][4]["repair_intent_id"] = "other-intent"
    trace = normalize_session_export(raw)
    selection = LineageSelection.from_mapping(_selection("s", "f", "b1", "i1", 0, 1, 2, 4, ("ann1",)))
    evidence = trace.project_followup_evidence(selection=selection, exposure_window=(2, 4))
    assert evidence["annotation_consumed"]["confirmed"] is None

    raw = _branched_export()
    raw["events"][4]["created_annotation_ids"] = ["future-ann"]
    raw["events"][4]["annotation_item_bindings"] = {"future-ann": ["item-a"]}
    trace = normalize_session_export(raw)
    selection = LineageSelection.from_mapping(_selection("s", "f", "b1", "i1", 0, 1, 2, 4, ("future-ann",)))
    evidence = trace.project_followup_evidence(selection=selection, exposure_window=(2, 4))
    assert evidence["annotation_consumed"]["confirmed"] is None
    truncated_selection = LineageSelection("s", "f", "b1", "i1", 0, 1, annotation_ids=("future-ann",))
    truncated = trace.project_followup_evidence(selection=truncated_selection, exposure_window=(2, 99))
    assert truncated["no_regression_observed"]["confirmed"] is None


def test_duplicate_tool_use_and_response_chain_fail_closed():
    bad = _export()
    bad["events"][1]["kind"] = "tool_call"
    bad["events"][1]["tool_use_id"] = "u"
    bad["events"][2]["kind"] = "tool_call"
    bad["events"][2]["tool_use_id"] = "u"
    with pytest.raises(SessionLogError, match="tool_use_id"):
        normalize_session_export(bad)
    bad = _export()
    bad["events"][1]["response_id"] = "r1"
    bad["events"][2]["previous_response_id"] = "missing"
    with pytest.raises(SessionLogError, match="previous_response_id"):
        normalize_session_export(bad)
    bad = _export()
    bad["events"][1]["kind"] = "tool_result"
    bad["events"][1]["tool_use_id"] = "u"
    with pytest.raises(SessionLogError, match="earlier tool_call"):
        normalize_session_export(bad)


def test_cross_branch_parent_requires_explicit_fork_root():
    bad = _branched_export()
    bad["events"][2]["fork_parent_event_id"] = None
    with pytest.raises(SessionLogError, match="crosses branch"):
        normalize_session_export(bad)
    bad = _branched_export()
    bad["events"][2]["root"] = False
    with pytest.raises(SessionLogError):
        normalize_session_export(bad)


def test_gold_and_recovery_fields_are_not_accepted_by_lineage_schema():
    bad = _export()
    bad["gold_answer"] = "should never enter lineage"
    with pytest.raises(SessionLogError, match="not closed"):
        normalize_session_export(bad)
    bad = _export()
    bad["events"][1]["recovery_gain"] = 1.0
    with pytest.raises(SessionLogError, match="unknown fields"):
        normalize_session_export(bad)


def test_selection_driven_cli_manifest_and_empty_audit(tmp_path: Path):
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps(_branched_export()) + "\n")
    selection_path = tmp_path / "selections.jsonl"
    selection_path.write_text("\n".join(json.dumps(row) for row in (
        _selection("s", "f", "b1", "i1", 0, 1, 2, 4, ("ann1",)),
        _selection("s", "f", "b2", "i2", 2, 3, 4, 5),
    )) + "\n")
    output = tmp_path / "lineage.jsonl"
    manifest = tmp_path / "lineage.manifest.json"
    stats = export_lineage(source, output, manifest, selections=selection_path)
    assert stats["selection_count"] == 2
    assert stats["evidence_count"] == 6
    assert stats["unknown_counts"]["annotation_consumed"] == 1
    assert stats["coverage"]["candidate"] == {
        "observed": 2,
        "registered_total": 2,
        "value": 1.0,
    }
    assert stats["coverage"]["family"]["value"] == 1.0
    assert stats["coverage"]["pairwise_comparable"] == {
        "comparable": 1,
        "total": 1,
        "value": 1.0,
    }
    assert stats["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert stats["selection_source_sha256"] == hashlib.sha256(selection_path.read_bytes()).hexdigest()
    assert stats["model_calls"] == stats["network_calls"] == 0
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows[0]["followup_evidence"]) == 2
    assert rows[0]["schema_version"] == "cmd-session-lineage-evidence-v2"
    with pytest.raises(ValueError, match="overwrite"):
        export_lineage(source, output, manifest, selections=selection_path)
    empty_output = tmp_path / "empty.jsonl"; empty_manifest = tmp_path / "empty.manifest.json"
    empty = export_lineage(source, empty_output, empty_manifest)
    assert empty["coverage_audit_empty"] is True
    assert empty["selection_count"] == empty["evidence_count"] == 0
    blocked_parent = tmp_path / "blocked-parent"
    blocked_parent.write_text("not a directory")
    with pytest.raises((FileExistsError, NotADirectoryError)):
        export_lineage(source, blocked_parent / "out.jsonl", blocked_parent / "out.manifest.json")
    assert not (tmp_path / "out.jsonl").exists()


def test_selection_cross_family_and_effective_after_fail_closed():
    trace = normalize_session_export(_branched_export())
    with pytest.raises(SessionLogError, match="session/family"):
        trace.project_followup_evidence(selection=LineageSelection.from_mapping(_selection("s", "other", "b1", "i1", 0, 1, 2, 4)), exposure_window=(2, 4))
    with pytest.raises(SessionLogError, match="effective-after"):
        LineageSelection.from_mapping(_selection("s", "f", "b1", "i1", 0, 0, 1, 2))
    with pytest.raises(SessionLogError, match="does not match"):
        trace.project_followup_evidence(
            selection=LineageSelection.from_mapping(_selection("s", "f", "b1", "i1", 0, 1, 2, 4)),
            exposure_window=(2, 3),
        )
    with pytest.raises(SessionLogError):
        load_lineage_selections(Path("/does/not/exist"))
    bad = _selection("s", "f", "b1", "i1", 0, 1, 2, 2)
    bad["annotation_ids"] = [1]
    with pytest.raises(SessionLogError, match="non-empty strings"):
        LineageSelection.from_mapping(bad)
