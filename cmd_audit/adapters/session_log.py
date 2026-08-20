"""Session-log adapter: append-only agent event streams -> ``ProbeCase``.

task.md 3.4.  Coding-agent harnesses (dsh, OpenHands, SWE-agent, Claude Code)
persist a session as an append-only event log and project messages from it,
rather than storing a mutable transcript.  That shape is what CMD needs: the
events are the raw ingestion trace, the projected context is the recall set, and
the harness's own compaction decisions are visible as events instead of being
inferred.

Two cut points, following ``mem0.py``:

* **Cut point A — projection.** Which events reach the model's context.  This is
  where compaction lives (an event dropped or replaced by a summary), so it is
  the write-side analogue of ``mem0.add()``.
* **Cut point B — recall.** Which projected items are surfaced for the current
  turn.

The adapter is deliberately *not* given a gold answer by the loader.  A recorded
session carries an outcome (resolved / unresolved) but no reference answer, so
:func:`session_events_to_probe_case` requires the caller to supply
``gold_answer`` and ``gold_evidence`` explicitly.  Passing them separately keeps
the substrate honest: a third-party trace on its own supports the zero-call
telemetry channel, not a gold-scored one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

from cmd_audit.core.models import (
    BaselineOutput,
    GoldEvidence,
    MemoryItem,
    ProbeCase,
    RawEvent,
)
from .base import AdapterRepairMixin, RepairAction, StoreChecksum


SESSION_ADAPTER_VERSION = "cmd-session-log-adapter-v1"
SESSION_LINEAGE_SCHEMA_VERSION = "cmd-session-lineage-v2"
SESSION_LINEAGE_EVIDENCE_SCHEMA_VERSION = "cmd-session-lineage-evidence-v2"
SESSION_LINEAGE_MANIFEST_SCHEMA_VERSION = "cmd-session-lineage-manifest-v2"
SESSION_SELECTION_SCHEMA_VERSION = "cmd-session-lineage-selection-v1"
ALLOWED_SOURCE_SCHEMAS = frozenset({"claude-tap-normalized-v1", "cmd-session-normalized-v1"})
NORMALIZED_EVENT_KINDS = frozenset({"session_start", "message", "user_message", "assistant_message", "tool_call", "tool_result", "retrieval", "context", "typed_confirmation", "deployment_guard", "annotation", "repair_selected"})
NORMALIZED_CONFIRMATION_SIGNALS = frozenset({"delayed_confirmation", "typed_outcome"})

# Event kinds a harness log may carry.  ``summary`` and ``compaction`` are the
# ones that matter for memory quality: they mark where content was replaced by a
# lossy stand-in.
EVENT_KINDS = (
    "user_message",
    "assistant_message",
    "tool_call",
    "tool_result",
    "summary",
    "compaction",
    "session_start",
)

# Kinds that carry content into the model's context when projected.
_PROJECTED_KINDS = frozenset(
    {"user_message", "assistant_message", "tool_call", "tool_result", "summary"}
)

_COMPACTION_KINDS = frozenset({"summary", "compaction"})


class SessionLogError(ValueError):
    """Raised when a session log does not satisfy the adapter contract."""


@dataclass(frozen=True)
class NormalizedSessionEvent:
    """Strict normalized event; IDs are accepted only as structured fields."""

    event_id: str
    session_id: str
    stream_id: str
    branch_id: str
    event_index: int
    turn: int
    kind: str
    parent_event_id: str | None = None
    parent_state_sha256: str | None = None
    state_sha256: str | None = None
    timestamp: str | None = None
    tool_use_id: str | None = None
    request_id: str | None = None
    response_id: str | None = None
    previous_response_id: str | None = None
    retrieved_item_ids: tuple[str, ...] = ()
    context_item_ids: tuple[str, ...] = ()
    repair_intent_id: str | None = None
    changed_item_ids: tuple[str, ...] = ()
    created_annotation_ids: tuple[str, ...] = ()
    annotation_item_bindings: tuple[tuple[str, tuple[str, ...]], ...] = ()
    rollback: bool | None = None
    guard_passed: bool | None = None
    locality_cost: float | None = None
    target_persistence: bool | None = None
    effective_after_event_index: int | None = None
    confirmation_signal: str | None = None
    usage_opportunity: bool | None = None
    target_loss: bool | None = None
    root: bool = False
    fork_parent_event_id: str | None = None
    source_schema_version: str = "unknown"
    source_payload_sha256: str = ""

    def __post_init__(self) -> None:
        if not self.event_id or not self.session_id or not self.stream_id or not self.branch_id:
            raise SessionLogError("normalized event requires stable session/stream/branch/event IDs")
        for name in ("event_id", "session_id", "stream_id", "branch_id", "kind"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise SessionLogError(f"normalized event {name} must be a non-empty string")
        if (isinstance(self.event_index, bool) or not isinstance(self.event_index, int)
                or isinstance(self.turn, bool) or not isinstance(self.turn, int)
                or self.event_index < 0 or self.turn < 0):
            raise SessionLogError("normalized event index/turn must be non-negative")
        for name in ("retrieved_item_ids", "context_item_ids", "changed_item_ids", "created_annotation_ids"):
            values = tuple(getattr(self, name))
            if tuple(sorted(set(values))) != values:
                raise SessionLogError(f"{name} must be sorted and unique")
        binding_ids = tuple(row[0] for row in self.annotation_item_bindings)
        if tuple(sorted(set(binding_ids))) != binding_ids:
            raise SessionLogError("annotation_item_bindings must be sorted and unique")
        created = set(self.created_annotation_ids)
        for annotation_id, item_ids in self.annotation_item_bindings:
            if annotation_id not in created:
                raise SessionLogError("annotation binding references an uncreated annotation")
            if tuple(sorted(set(item_ids))) != tuple(item_ids):
                raise SessionLogError("annotation binding item IDs must be sorted and unique")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object], *, source_schema_version: str) -> "NormalizedSessionEvent":
        required = {"event_id", "session_id", "stream_id", "branch_id", "event_index", "turn", "kind"}
        if not required.issubset(raw):
            raise SessionLogError("normalized event is missing required linkage fields")
        if str(raw["kind"]) not in NORMALIZED_EVENT_KINDS:
            raise SessionLogError("unknown normalized event kind")
        allowed = set(cls.__dataclass_fields__) - {"source_schema_version", "source_payload_sha256"}
        if set(raw) - allowed:
            raise SessionLogError("normalized event contains unknown fields")
        for name in ("event_id", "session_id", "stream_id", "branch_id", "kind"):
            if not isinstance(raw[name], str) or not raw[name].strip():
                raise SessionLogError(f"normalized event {name} must be a non-empty string")
        for name in ("parent_event_id", "parent_state_sha256", "state_sha256", "timestamp", "tool_use_id", "request_id", "response_id", "previous_response_id", "repair_intent_id", "fork_parent_event_id"):
            if name in raw and raw[name] is not None and (not isinstance(raw[name], str) or not raw[name].strip()):
                raise SessionLogError(f"normalized event {name} must be a non-empty string or null")
        for name in ("event_index", "turn"):
            if isinstance(raw[name], bool) or not isinstance(raw[name], int) or raw[name] < 0:
                raise SessionLogError("normalized event index/turn must be non-negative integers")
        if "effective_after_event_index" in raw and raw["effective_after_event_index"] is not None:
            value = raw["effective_after_event_index"]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise SessionLogError("effective_after_event_index must be a non-negative integer or null")
        for name in ("rollback", "guard_passed", "target_persistence", "root", "usage_opportunity", "target_loss"):
            if name in raw and not isinstance(raw[name], bool):
                raise SessionLogError(f"normalized event {name} must be boolean")
        if "locality_cost" in raw and raw["locality_cost"] is not None:
            value = raw["locality_cost"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise SessionLogError("locality_cost must be a finite number or null")
        if "confirmation_signal" in raw and raw["confirmation_signal"] is not None:
            value = raw["confirmation_signal"]
            if not isinstance(value, str) or value not in NORMALIZED_CONFIRMATION_SIGNALS:
                raise SessionLogError("unknown normalized confirmation signal")
        source_hash = hashlib.sha256(json.dumps(dict(raw), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        values = {name: raw[name] for name in cls.__dataclass_fields__ if name in raw}
        for name in ("retrieved_item_ids", "context_item_ids", "changed_item_ids", "created_annotation_ids"):
            raw_values = values.get(name, ())
            if not isinstance(raw_values, Sequence) or isinstance(raw_values, (str, bytes)):
                raise SessionLogError(f"{name} must be a sequence of strings")
            if any(not isinstance(item, str) or not item.strip() for item in raw_values):
                raise SessionLogError(f"{name} must contain non-empty strings")
            values[name] = tuple(raw_values)
        raw_bindings = raw.get("annotation_item_bindings", {})
        if not isinstance(raw_bindings, Mapping):
            raise SessionLogError("annotation_item_bindings must be a mapping")
        bindings: list[tuple[str, tuple[str, ...]]] = []
        for annotation_id, item_ids in raw_bindings.items():
            if not isinstance(annotation_id, str) or not annotation_id.strip():
                raise SessionLogError("annotation binding IDs must be non-empty strings")
            if not isinstance(item_ids, Sequence) or isinstance(item_ids, (str, bytes)):
                raise SessionLogError("annotation binding item IDs must be a sequence")
            if any(not isinstance(item_id, str) or not item_id.strip() for item_id in item_ids):
                raise SessionLogError("annotation binding item IDs must be non-empty strings")
            bindings.append((annotation_id, tuple(sorted(set(item_ids)))))
        values["annotation_item_bindings"] = tuple(sorted(bindings))
        values["source_schema_version"] = source_schema_version
        values["source_payload_sha256"] = source_hash
        return cls(**values)


@dataclass(frozen=True)
class NormalizedSessionTrace:
    session_id: str
    stream_id: str
    family_id: str
    events: tuple[NormalizedSessionEvent, ...]
    source_export_schema: str
    source_export_sha256: str

    def __post_init__(self) -> None:
        if not self.events:
            raise SessionLogError("normalized trace requires events")
        ids = [row.event_id for row in self.events]
        if len(set(ids)) != len(ids):
            raise SessionLogError("normalized event IDs must be unique")
        if any(row.session_id != self.session_id or row.stream_id != self.stream_id for row in self.events):
            raise SessionLogError("event session/stream linkage mismatch")
        ordered = sorted(self.events, key=lambda row: row.event_index)
        if tuple(self.events) != tuple(ordered):
            raise SessionLogError("normalized events are out of order")
        by_id = {row.event_id: row for row in self.events}
        branches = {row.branch_id for row in self.events}
        for branch in branches:
            branch_rows = [row for row in self.events if row.branch_id == branch]
            roots = [row for row in branch_rows if row.root]
            if len(roots) != 1:
                raise SessionLogError("each branch requires exactly one explicit root")
        for row in self.events:
            if not isinstance(row.state_sha256, str) or not row.state_sha256:
                raise SessionLogError("every normalized event requires a state hash")
            if row.parent_event_id is not None:
                parent = by_id.get(row.parent_event_id)
                if row.parent_state_sha256 is None or parent is None or parent.event_index >= row.event_index or parent.state_sha256 != row.parent_state_sha256:
                    raise SessionLogError("broken normalized parent event/state chain")
                if parent.branch_id != row.branch_id:
                    if not row.root or row.fork_parent_event_id != parent.event_id:
                        raise SessionLogError("parent crosses branch without explicit fork seam")
                elif row.fork_parent_event_id is not None:
                    raise SessionLogError("fork parent must identify a cross-branch root")
            elif not row.root:
                raise SessionLogError("non-root event requires parent event/state chain")
            elif row.fork_parent_event_id is not None:
                raise SessionLogError("fork parent requires a cross-branch parent event")
        created_annotations = [annotation_id for row in self.events for annotation_id in row.created_annotation_ids]
        if len(set(created_annotations)) != len(created_annotations):
            raise SessionLogError("created annotation IDs must be unique per normalized trace")
        calls = [row for row in self.events if row.kind == "tool_call" and row.tool_use_id]
        call_ids = [row.tool_use_id for row in calls]
        if len(set(call_ids)) != len(call_ids):
            raise SessionLogError("tool_use_id must be unique per normalized trace")
        results = [row for row in self.events if row.kind == "tool_result" and row.tool_use_id]
        result_ids = [row.tool_use_id for row in results]
        if len(set(result_ids)) != len(result_ids):
            raise SessionLogError("tool_result tool_use_id must be unique per normalized trace")
        call_by_id = {row.tool_use_id: row for row in calls}
        for row in self.events:
            if row.kind == "tool_result":
                if not row.tool_use_id or row.tool_use_id not in call_by_id:
                    raise SessionLogError("tool_result lacks earlier tool_call binding")
                call = call_by_id[row.tool_use_id]
                if call.branch_id != row.branch_id or call.event_index >= row.event_index:
                    raise SessionLogError("tool_result tool_use_id crosses branch/order")
        responses = [row for row in self.events if row.response_id]
        response_ids = [row.response_id for row in responses]
        if len(set(response_ids)) != len(response_ids):
            raise SessionLogError("response_id must be unique per normalized trace")
        requests = [row for row in self.events if row.request_id]
        request_ids = [row.request_id for row in requests]
        if len(set(request_ids)) != len(request_ids):
            raise SessionLogError("request_id must be unique per normalized trace")
        response_by_id = {row.response_id: row for row in responses}
        for row in self.events:
            if row.previous_response_id is None:
                continue
            previous = response_by_id.get(row.previous_response_id)
            if previous is None or previous.event_index >= row.event_index or previous.branch_id != row.branch_id:
                raise SessionLogError("previous_response_id must bind an earlier same-branch response")

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(json.dumps([row.__dict__ for row in self.events], sort_keys=True, separators=(",", ":"), default=list).encode()).hexdigest()

    def followup_tracker(self, *, branch_id: str, selected_event_index: int):
        from cmd_audit.eval.live_followup import FollowupBranchTracker
        if branch_id not in {row.branch_id for row in self.events}:
            raise SessionLogError("unknown normalized branch")
        return FollowupBranchTracker(branch_id=branch_id, family_id=self.family_id, selected_event_index=selected_event_index)

    def project_followup_evidence(self, *, selection: "LineageSelection", exposure_window: tuple[int, int]) -> dict[str, object]:
        return project_followup_evidence(self, selection=selection, exposure_window=exposure_window)


@dataclass(frozen=True)
class LineageSelection:
    session_id: str
    family_id: str
    branch_id: str
    repair_intent_id: str
    selected_event_index: int
    effective_after_event_index: int
    annotation_ids: tuple[str, ...] = ()
    changed_item_ids: tuple[str, ...] = ()
    exposure_start_event_index: int | None = None
    exposure_end_event_index: int | None = None

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in (self.session_id, self.family_id, self.branch_id, self.repair_intent_id)):
            raise SessionLogError("lineage selection requires session/family/branch/intent identity")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (self.selected_event_index, self.effective_after_event_index)):
            raise SessionLogError("lineage selection event indexes must be integers")
        if self.selected_event_index < 0 or self.effective_after_event_index <= self.selected_event_index:
            raise SessionLogError("selection effective-after index is invalid")
        for name in ("annotation_ids", "changed_item_ids"):
            values = tuple(getattr(self, name))
            if tuple(sorted(set(values))) != values:
                raise SessionLogError(f"selection {name} must be sorted and unique")
        if (self.exposure_start_event_index is None) != (self.exposure_end_event_index is None):
            raise SessionLogError("selection exposure window must provide both bounds")
        if self.exposure_start_event_index is not None and self.exposure_end_event_index is not None:
            if self.exposure_end_event_index < self.exposure_start_event_index:
                raise SessionLogError("selection exposure window is inverted")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "LineageSelection":
        expected = {
            "schema_version", "session_id", "family_id", "branch_id", "repair_intent_id",
            "selected_event_index", "effective_after_event_index", "annotation_ids",
            "changed_item_ids", "exposure_start_event_index", "exposure_end_event_index",
        }
        if set(raw) != expected or raw.get("schema_version") != SESSION_SELECTION_SCHEMA_VERSION:
            raise SessionLogError("lineage selection mapping is not closed or versioned")
        for name in ("selected_event_index", "effective_after_event_index", "exposure_start_event_index", "exposure_end_event_index"):
            if isinstance(raw[name], bool) or not isinstance(raw[name], int):
                raise SessionLogError("lineage selection event indexes must be integers")
        for name in ("annotation_ids", "changed_item_ids"):
            if not isinstance(raw[name], Sequence) or isinstance(raw[name], (str, bytes)):
                raise SessionLogError("lineage selection ID fields must be sequences")
            if any(not isinstance(value, str) or not value.strip() for value in raw[name]):
                raise SessionLogError("lineage selection ID fields must contain non-empty strings")
        return cls(
            session_id=raw["session_id"], family_id=raw["family_id"], branch_id=raw["branch_id"],
            repair_intent_id=raw["repair_intent_id"], selected_event_index=raw["selected_event_index"],
            effective_after_event_index=raw["effective_after_event_index"],
            annotation_ids=tuple(sorted(raw["annotation_ids"])),
            changed_item_ids=tuple(sorted(raw["changed_item_ids"])),
            exposure_start_event_index=raw["exposure_start_event_index"],
            exposure_end_event_index=raw["exposure_end_event_index"],
        )


def project_followup_evidence(trace: NormalizedSessionTrace, *, selection: LineageSelection, exposure_window: tuple[int, int]) -> dict[str, object]:
    if selection.session_id != trace.session_id or selection.family_id != trace.family_id:
        raise SessionLogError("selection crosses session/family")
    if (not isinstance(exposure_window, tuple) or len(exposure_window) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in exposure_window)):
        raise SessionLogError("exposure window indexes must be integers")
    start, end = exposure_window
    if start < 0 or end < start:
        raise SessionLogError("exposure window is invalid")
    if (selection.exposure_start_event_index, selection.exposure_end_event_index) not in {
        (None, None), (start, end),
    }:
        raise SessionLogError("exposure window does not match the selection")
    branch_rows = [row for row in trace.events if row.branch_id == selection.branch_id]
    selected_rows = [row for row in branch_rows if row.event_index == selection.selected_event_index]
    if not selected_rows or selected_rows[0].repair_intent_id != selection.repair_intent_id:
        raise SessionLogError("selection does not bind a branch-local repair intent")
    if selection.branch_id not in {row.branch_id for row in trace.events} or start <= selection.effective_after_event_index:
        raise SessionLogError("invalid follow-up branch or exposure window")
    later = [row for row in trace.events if row.branch_id == selection.branch_id and start <= row.event_index <= end and row.event_index > selection.effective_after_event_index]
    relevant = [row for row in later if row.repair_intent_id in {None, selection.repair_intent_id}]
    def evidence(kind: str, row: NormalizedSessionEvent | None, confirmed: bool | None, reason: str) -> dict[str, object]:
        return {"kind": kind, "confirmed": confirmed, "reason": reason, "source_event_id": None if row is None else row.event_id, "observed_at_event_index": None if row is None else row.event_index, "state_sha256": None if row is None else row.state_sha256, "schema_version": SESSION_LINEAGE_EVIDENCE_SCHEMA_VERSION}
    annotation_bindings = {
        annotation_id: set(item_ids)
        for branch_row in branch_rows
        if branch_row.event_index <= selection.effective_after_event_index
        for annotation_id, item_ids in branch_row.annotation_item_bindings
    }

    def downstream_annotation_ids(row: NormalizedSessionEvent) -> set[str]:
        used_items = set(row.retrieved_item_ids) | set(row.context_item_ids)
        return {
            annotation_id
            for annotation_id, item_ids in annotation_bindings.items()
            if used_items & item_ids
        }
    consumed = next((row for row in relevant if set(selection.annotation_ids) & downstream_annotation_ids(row)), None)
    delayed_candidates = [row for row in relevant if row.confirmation_signal in {"delayed_confirmation", "typed_outcome"}]
    delayed = delayed_candidates[0] if delayed_candidates else None
    exposure = [row for row in relevant if row.usage_opportunity is True]
    if end > max(row.event_index for row in branch_rows):
        regression = evidence("no_regression_observed", None, None, "exposure window truncated")
    elif not exposure:
        regression = evidence("no_regression_observed", None, None, "no registered exposure opportunity")
    else:
        bad = next((row for row in exposure if row.rollback is True or row.target_loss is True or row.guard_passed is False), None)
        unknown = next((row for row in exposure if row.guard_passed is not True), None)
        regression = evidence("no_regression_observed", bad or unknown, False if bad else None if unknown else True, "rollback/target loss" if bad else "exposure guard unknown" if unknown else "guard passed in exposure window")
    if delayed is not None:
        delayed_bad = delayed.rollback is True or delayed.target_loss is True or delayed.guard_passed is False
        delayed = evidence("delayed_confirmation", delayed, False if delayed_bad else True, "rollback/target loss" if delayed_bad else "registered typed signal")
    else:
        delayed = evidence("delayed_confirmation", None, None, "no registered confirmation signal")
    return {"annotation_consumed": evidence("annotation_consumed", consumed, True if consumed else None, "downstream structured binding" if consumed else "no downstream binding"),
            "delayed_confirmation": delayed,
            "no_regression_observed": regression}


def normalize_session_export(value: Mapping[str, object], *, source_schema_version: str | None = None) -> NormalizedSessionTrace:
    """Normalize an explicit structured export; never parses IDs from text."""
    source_schema_version = source_schema_version or str(value.get("schema_version", "unknown"))
    if source_schema_version not in ALLOWED_SOURCE_SCHEMAS:
        raise SessionLogError("source export schema lacks lineage guarantees")
    if source_schema_version in {"unknown", "", "legacy-jsonl-v1"}:
        raise SessionLogError("source export schema lacks lineage guarantees")
    if set(value) != {"schema_version", "session_id", "stream_id", "family_id", "events"}:
        raise SessionLogError("normalized export mapping is not closed")
    raw_events = value.get("events")
    if not isinstance(raw_events, Sequence) or isinstance(raw_events, (str, bytes)):
        raise SessionLogError("normalized export events must be a sequence")
    session_id, stream_id, family_id = (str(value.get(name) or "") for name in ("session_id", "stream_id", "family_id"))
    if not session_id or not stream_id or not family_id:
        raise SessionLogError("normalized export requires session/stream/family IDs")
    events = tuple(NormalizedSessionEvent.from_mapping(row, source_schema_version=source_schema_version) for row in raw_events if isinstance(row, Mapping))
    if len(events) != len(raw_events):
        raise SessionLogError("normalized event must be a mapping")
    raw_hash = hashlib.sha256(json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return NormalizedSessionTrace(session_id, stream_id, family_id, events, source_schema_version, raw_hash)


@dataclass(frozen=True)
class SessionEvent:
    """One append-only entry in a harness session log."""

    event_id: str
    kind: str
    text: str
    turn: int
    replaces_event_ids: tuple[str, ...] = ()
    masked: bool = False

    def __post_init__(self) -> None:
        if not self.event_id:
            raise SessionLogError("session event requires event_id")
        if self.kind not in EVENT_KINDS:
            raise SessionLogError(f"unknown session event kind: {self.kind}")
        if not isinstance(self.turn, int) or isinstance(self.turn, bool) or self.turn < 0:
            raise SessionLogError("session event turn must be a non-negative integer")
        if self.replaces_event_ids and self.kind not in _COMPACTION_KINDS:
            raise SessionLogError(
                "only summary/compaction events may replace earlier events"
            )

    @property
    def is_compaction(self) -> bool:
        return self.kind in _COMPACTION_KINDS

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "SessionEvent":
        if "event_id" not in value or "kind" not in value:
            raise SessionLogError("session event requires event_id and kind")
        return cls(
            event_id=str(value["event_id"]),
            kind=str(value["kind"]),
            text=str(value.get("text", "")),
            turn=int(value.get("turn", 0)),
            replaces_event_ids=tuple(
                str(row) for row in value.get("replaces_event_ids", ())
            ),
            masked=bool(value.get("masked", False)),
        )


@dataclass(frozen=True)
class SessionTrace:
    """A whole recorded session: its events, its arm, and its outcome.

    ``arm`` names the context-management strategy the session ran under (e.g.
    ``raw`` / ``observation_masking`` / ``llm_summary``), which is what makes a
    multi-arm substrate comparable.  ``resolved`` is the harness's own verdict,
    used as a frozen outcome and never as a gold answer.
    """

    session_id: str
    arm: str
    events: tuple[SessionEvent, ...]
    resolved: bool
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.session_id:
            raise SessionLogError("session trace requires session_id")
        if not self.arm:
            raise SessionLogError("session trace requires an arm label")
        if not self.events:
            raise SessionLogError("session trace requires at least one event")
        ids = [row.event_id for row in self.events]
        if len(set(ids)) != len(ids):
            raise SessionLogError("session event ids must be unique")
        turns = [row.turn for row in self.events]
        if turns != sorted(turns):
            raise SessionLogError("session events must be in non-decreasing turn order")
        known = set(ids)
        for row in self.events:
            unknown = set(row.replaces_event_ids) - known
            if unknown:
                raise SessionLogError(
                    f"event {row.event_id} replaces unknown events: {sorted(unknown)}"
                )

    @property
    def compaction_event_count(self) -> int:
        return sum(1 for row in self.events if row.is_compaction)

    @property
    def replaced_event_ids(self) -> frozenset[str]:
        """Events superseded by a summary or compaction event."""
        return frozenset(
            event_id
            for row in self.events
            for event_id in row.replaces_event_ids
        )

    def project(self) -> tuple[SessionEvent, ...]:
        """Cut point A: the events that actually reach the model's context.

        Replaced events drop out (that is what compaction did) and masked events
        stay in the log but carry no content forward.
        """
        replaced = self.replaced_event_ids
        return tuple(
            row
            for row in self.events
            if row.kind in _PROJECTED_KINDS
            and row.event_id not in replaced
            and not row.masked
        )

    def content_sha256(self) -> str:
        payload = [
            {
                "event_id": row.event_id,
                "kind": row.kind,
                "text": row.text,
                "turn": row.turn,
                "replaces_event_ids": list(row.replaces_event_ids),
                "masked": row.masked,
            }
            for row in self.events
        ]
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "SessionTrace":
        raw_events = value.get("events")
        if not isinstance(raw_events, Sequence) or isinstance(raw_events, (str, bytes)):
            raise SessionLogError("session trace events must be a sequence")
        metadata = value.get("metadata") or {}
        if not isinstance(metadata, Mapping):
            raise SessionLogError("session trace metadata must be a mapping")
        return cls(
            session_id=str(value.get("session_id") or ""),
            arm=str(value.get("arm") or ""),
            events=tuple(
                SessionEvent.from_mapping(row)
                if isinstance(row, Mapping)
                else _reject(row)
                for row in raw_events
            ),
            resolved=bool(value.get("resolved", False)),
            metadata=dict(metadata),
        )


def _reject(row: object) -> SessionEvent:
    raise SessionLogError(f"session event must be a mapping, got {type(row).__name__}")


def load_session_traces(path: str | Path) -> tuple[SessionTrace, ...]:
    """Read a JSONL session-log file, one trace per line."""
    target = Path(path)
    traces: list[SessionTrace] = []
    for line_number, line in enumerate(
        target.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise SessionLogError(
                f"invalid session-log JSONL at line {line_number}"
            ) from error
        if not isinstance(value, Mapping):
            raise SessionLogError(f"session-log line {line_number} is not an object")
        traces.append(SessionTrace.from_mapping(value))
    if not traces:
        raise SessionLogError(f"session-log file is empty: {target}")
    return tuple(traces)


def load_normalized_session_exports(path: str | Path) -> tuple[NormalizedSessionTrace, ...]:
    target = Path(path)
    traces = []
    for number, line in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        raw = json.loads(line)
        if not isinstance(raw, Mapping):
            raise SessionLogError(f"normalized export line {number} is not an object")
        traces.append(normalize_session_export(raw, source_schema_version=str(raw.get("schema_version", "unknown"))))
    if not traces:
        raise SessionLogError("normalized export is empty")
    return tuple(traces)


def load_lineage_selections(path: str | Path) -> tuple[LineageSelection, ...]:
    target = Path(path)
    if not target.exists():
        raise SessionLogError(f"lineage selection file is missing: {target}")
    selections: list[LineageSelection] = []
    for number, line in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as error:
            raise SessionLogError(f"invalid lineage selection JSONL at line {number}") from error
        if not isinstance(raw, Mapping):
            raise SessionLogError(f"lineage selection line {number} is not an object")
        selections.append(LineageSelection.from_mapping(raw))
    if not selections:
        raise SessionLogError(f"lineage selection file is empty: {target}")
    identities = {(row.session_id, row.family_id, row.branch_id, row.repair_intent_id) for row in selections}
    if len(identities) != len(selections):
        raise SessionLogError("lineage selection identities must be unique")
    return tuple(selections)


def iter_arms(traces: Iterable[SessionTrace]) -> Iterator[tuple[str, tuple[SessionTrace, ...]]]:
    """Group traces by arm, so a caller can check the multi-arm shape."""
    grouped: dict[str, list[SessionTrace]] = {}
    for trace in traces:
        grouped.setdefault(trace.arm, []).append(trace)
    for arm in sorted(grouped):
        yield arm, tuple(grouped[arm])


def session_events_to_probe_case(
    trace: SessionTrace,
    *,
    query: str,
    gold_answer: str,
    gold_evidence: Sequence[GoldEvidence],
    baseline_answer: str = "",
    baseline_answer_score: float = 0.0,
    baseline_evidence_score: float = 0.0,
) -> ProbeCase:
    """Convert one recorded session into a ``ProbeCase``.

    ``gold_answer`` and ``gold_evidence`` are required arguments rather than
    being scavenged from the trace: a third-party session log does not contain
    them, and silently synthesizing a stand-in would manufacture a reference
    signal that does not exist.
    """
    if not query:
        raise SessionLogError("probe case requires a query")
    if not gold_answer:
        raise SessionLogError(
            "session logs carry no reference answer; supply gold_answer explicitly"
        )
    if not gold_evidence:
        raise SessionLogError(
            "session logs carry no reference evidence; supply gold_evidence explicitly"
        )

    raw_events = tuple(
        RawEvent(event_id=row.event_id, text=row.text) for row in trace.events
    )
    projected = trace.project()
    # ``-`` not ``::``: memory_ids reach the leak-safe monitor as evidence
    # pointers, and ``validate_evidence_pointers`` rejects ``:`` as a
    # content-bearing separator.
    extracted_memory = tuple(
        MemoryItem(
            memory_id=f"{trace.session_id}-{row.event_id}",
            text=row.text,
            source_event_ids=(row.event_id,),
            # Summaries live in a different store from verbatim turns: that is
            # the granularity distinction the item gate reads.
            store="summary" if row.kind == "summary" else "episodic",
            passed_safety_filter=True,
        )
        for row in projected
    )
    if not extracted_memory:
        raise SessionLogError(
            f"session {trace.session_id} projects no context; nothing to audit"
        )

    # ``run_baseline_suite`` requires both names in REQUIRED_MEMORY_BASELINES.
    # ``vector_memory`` is the real one: the context this arm actually projected.
    # ``fixed_summary`` is a harness-contract stand-in — a session log records one
    # arm's behaviour, not a second summarizer's, so it carries no observed answer
    # and must not be read as measured evidence about the trace.
    baseline = BaselineOutput(
        baseline_name="vector_memory",
        answer=baseline_answer,
        retrieved_memory_ids=tuple(item.memory_id for item in extracted_memory),
        answer_score=baseline_answer_score,
        evidence_score=baseline_evidence_score,
        injected_context="\n".join(item.text for item in extracted_memory),
    )
    fixed_summary = BaselineOutput(
        baseline_name="fixed_summary",
        answer="",
        retrieved_memory_ids=(),
        answer_score=0.0,
        evidence_score=0.0,
        injected_context=(
            "not observed in this session log; present only to satisfy the "
            "required-baseline contract"
        ),
    )
    return ProbeCase(
        case_id=f"{trace.session_id}-{trace.arm}",
        query=query,
        raw_events=raw_events,
        extracted_memory=extracted_memory,
        gold_evidence=tuple(gold_evidence),
        gold_answer=gold_answer,
        baseline_outputs=(baseline, fixed_summary),
        has_ingestion_trace=True,
        default_store="episodic",
        current_granularity="summary" if trace.compaction_event_count else "session",
    )


class SessionLogAdapter(AdapterRepairMixin):
    """Intercepts a session log's projection and recall at two cut points.

    Mutations are sandboxed in memory; ``get_store_snapshot`` hashes the trace so
    a caller can prove the recorded log was never written to.
    """

    supported_actions = ("append", "replace")

    def __init__(
        self,
        trace: SessionTrace,
        gold_evidence: Sequence[GoldEvidence] = (),
        extracted_memory: Sequence[MemoryItem] = (),
        raw_events: Sequence[RawEvent] = (),
    ) -> None:
        self._trace = trace
        self._gold_evidence = tuple(gold_evidence)
        self._extracted_memory = tuple(extracted_memory)
        self._raw_events = tuple(raw_events)
        self._pre_checksum = trace.content_sha256()
        self._sandbox_items = list(extracted_memory)

    # ── Domain-specific accessors ──────────────────────────────────────

    @property
    def arm(self) -> str:
        return self._trace.arm

    @property
    def original_projected_events(self) -> list[SessionEvent]:
        """Cut point A's original output: events that reached the context."""
        return list(self._trace.project())

    @property
    def original_recall_results(self) -> list[MemoryItem]:
        return list(self._sandbox_items)

    # ── Standard adapter interface ─────────────────────────────────────

    @property
    def original_inputs(self) -> list[str]:
        return [row.text for row in self._trace.events]

    @property
    def original_query(self) -> str:
        for row in self._trace.events:
            if row.kind == "user_message":
                return row.text
        return ""

    @property
    def original_results(self) -> list[MemoryItem]:
        return self.original_recall_results

    # ── Cut Point A: projection interception ───────────────────────────

    def intercept_projection(
        self, case_id: str, original_events: list[SessionEvent]
    ) -> list[SessionEvent]:
        """Drop compaction stand-ins so the verbatim turns project instead.

        This is the counterfactual the substrate exists to support: the same
        session, with and without the harness's summarization applied.
        """
        replaced = self._trace.replaced_event_ids
        if not replaced:
            return list(original_events)
        kept = [row for row in original_events if not row.is_compaction]
        restored = [
            row
            for row in self._trace.events
            if row.event_id in replaced and row.kind in _PROJECTED_KINDS
        ]
        merged = kept + restored
        merged.sort(key=lambda row: (row.turn, row.event_id))
        return merged

    intercept_write = intercept_projection

    # ── Cut Point B: recall interception ───────────────────────────────

    def intercept_recall(
        self,
        case_id: str,
        original_query: str,
        original_results: list[MemoryItem],
    ) -> list[MemoryItem]:
        """Return the sandboxed recall set for this turn."""
        return list(original_results)

    intercept_search = intercept_recall

    # ── Sandbox ────────────────────────────────────────────────────────

    def get_store_snapshot(self) -> StoreChecksum:
        return StoreChecksum(
            checksum=hashlib.sha256(
                "|".join(
                    sorted(item.memory_id for item in self._sandbox_items)
                ).encode("utf-8")
            ).hexdigest(),
            item_count=len(self._sandbox_items),
        )

    def verify_sandbox(self) -> None:
        """Confirm the recorded trace itself is byte-identical to intake."""
        if self._trace.content_sha256() != self._pre_checksum:
            raise SessionLogError(
                f"session {self._trace.session_id} was mutated outside the sandbox"
            )

    # ── Repair support ─────────────────────────────────────────────────

    def apply_repair(self, action: RepairAction) -> str:
        """Apply a repair to the sandboxed recall set.

        A missing or unknown ``target_item_id`` raises ``ValueError`` rather than
        ``UnsupportedActionError``: ``RepairExecutor`` treats the latter as "this
        adapter cannot do that action type" and continues without applying, which
        would turn a bad target into a silent no-op.
        """
        self._validate_action_type(action)
        if action.action_type == "append":
            self._sandbox_items.append(
                MemoryItem(
                    memory_id=f"{self._trace.session_id}-repair_{len(self._sandbox_items)}",
                    text=action.content,
                    store=action.target_store or "episodic",
                    passed_safety_filter=True,
                )
            )
            return f"session_log append: new -> {action.target_store}"
        if action.target_item_id is None:
            raise ValueError("replace requires target_item_id")
        for index, item in enumerate(self._sandbox_items):
            if item.memory_id == action.target_item_id:
                self._sandbox_items[index] = MemoryItem(
                    memory_id=item.memory_id,
                    text=action.content,
                    source_event_ids=item.source_event_ids,
                    store=item.store,
                    passed_safety_filter=item.passed_safety_filter,
                )
                return (
                    f"session_log replace: {action.target_item_id}"
                    f" -> {action.target_store}"
                )
        raise ValueError(
            f"target_item_id {action.target_item_id!r} not in sandboxed recall set"
        )


__all__ = [
    "NormalizedSessionEvent",
    "NormalizedSessionTrace",
    "SESSION_LINEAGE_SCHEMA_VERSION",
    "SESSION_LINEAGE_EVIDENCE_SCHEMA_VERSION",
    "SESSION_LINEAGE_MANIFEST_SCHEMA_VERSION",
    "SESSION_SELECTION_SCHEMA_VERSION",
    "EVENT_KINDS",
    "SESSION_ADAPTER_VERSION",
    "SessionEvent",
    "SessionLogAdapter",
    "SessionLogError",
    "SessionTrace",
    "iter_arms",
    "load_session_traces",
    "load_normalized_session_exports",
    "load_lineage_selections",
    "normalize_session_export",
    "session_events_to_probe_case",
]
