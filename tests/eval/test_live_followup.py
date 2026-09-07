from __future__ import annotations

import pytest

from cmd_audit.eval.live_followup import FollowupBranchTracker, FollowupEvidence


def test_followup_requires_real_downstream_binding_and_effective_after() -> None:
    tracker = FollowupBranchTracker(branch_id="b1", family_id="f1", selected_event_index=2)
    with pytest.raises(ValueError):
        tracker.record_annotation_consumed(annotation_id="a", retrieved_or_used_ids=(), event_index=3, source_event_id="e3", state_hash="s")
    row = tracker.record_annotation_consumed(annotation_id="a", retrieved_or_used_ids=("a",), event_index=3, source_event_id="e3", state_hash="s")
    assert row.confirmed and row.observed_at_event_index == 3
    with pytest.raises(ValueError):
        tracker.record_no_regression(event_index=2, source_event_id="e2", state_hash="s", guard_passed=True)


def test_followup_branch_and_family_isolation() -> None:
    tracker = FollowupBranchTracker(branch_id="b1", family_id="f1", selected_event_index=0)
    with pytest.raises(ValueError):
        tracker._append(FollowupEvidence("b2", "delayed_confirmation", 1, "e", "s"))
    assert tracker.snapshot()["branch_id"] == "b1"
