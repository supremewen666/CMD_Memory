"""Gold-free follow-up evidence tracker for live candidate branches."""

from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
from typing import Mapping


FOLLOWUP_SCHEMA_VERSION = "cmd-live-followup-evidence-v1"


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class FollowupEvidence:
    branch_id: str
    kind: str
    observed_at_event_index: int
    source_event_id: str
    branch_state_sha256: str
    evidence_schema_version: str = FOLLOWUP_SCHEMA_VERSION
    confirmed: bool = True

    def __post_init__(self) -> None:
        if self.kind not in {"annotation_consumed", "delayed_confirmation", "no_regression_observed"}:
            raise ValueError("unregistered follow-up evidence kind")
        if self.observed_at_event_index < 0 or not self.source_event_id or not self.branch_id:
            raise ValueError("follow-up evidence requires branch/event provenance")


class FollowupBranchTracker:
    """Isolated append-only tracker; no cross-branch or cross-family joins."""

    def __init__(self, *, branch_id: str, family_id: str, selected_event_index: int) -> None:
        self.branch_id = branch_id
        self.family_id = family_id
        self.selected_event_index = selected_event_index
        self._events: list[FollowupEvidence] = []

    @property
    def events(self) -> tuple[FollowupEvidence, ...]:
        return tuple(self._events)

    def _append(self, evidence: FollowupEvidence) -> FollowupEvidence:
        if evidence.branch_id != self.branch_id or evidence.observed_at_event_index <= self.selected_event_index:
            raise ValueError("follow-up evidence violates branch/effective-after boundary")
        if any(row.observed_at_event_index == evidence.observed_at_event_index for row in self._events):
            raise ValueError("duplicate follow-up event index")
        self._events.append(evidence)
        return evidence

    def record_annotation_consumed(self, *, annotation_id: str, retrieved_or_used_ids: tuple[str, ...], event_index: int, source_event_id: str, state_hash: str) -> FollowupEvidence:
        if annotation_id not in retrieved_or_used_ids:
            raise ValueError("annotation consumption lacks observed downstream binding")
        return self._append(FollowupEvidence(self.branch_id, "annotation_consumed", event_index, source_event_id, state_hash))

    def record_delayed_confirmation(self, *, event_index: int, source_event_id: str, state_hash: str, confirmed: bool) -> FollowupEvidence:
        return self._append(FollowupEvidence(self.branch_id, "delayed_confirmation", event_index, source_event_id, state_hash, confirmed=confirmed))

    def record_no_regression(self, *, event_index: int, source_event_id: str, state_hash: str, guard_passed: bool) -> FollowupEvidence:
        return self._append(FollowupEvidence(self.branch_id, "no_regression_observed", event_index, source_event_id, state_hash, confirmed=guard_passed))

    def snapshot(self) -> dict[str, object]:
        payload = {"schema_version": FOLLOWUP_SCHEMA_VERSION, "branch_id": self.branch_id,
                   "family_id": self.family_id, "selected_event_index": self.selected_event_index,
                   "events": [asdict(row) for row in self._events]}
        return {**payload, "snapshot_sha256": _hash(payload)}


def reject_cross_family(tracker: FollowupBranchTracker, *, family_id: str) -> None:
    if family_id != tracker.family_id:
        raise ValueError("follow-up evidence cannot cross family boundary")


__all__ = ["FOLLOWUP_SCHEMA_VERSION", "FollowupEvidence", "FollowupBranchTracker", "reject_cross_family"]
