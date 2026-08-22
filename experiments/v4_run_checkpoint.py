"""Fail-closed committed checkpoints for the V4 runner.

The JSONL journal is the authority; ``latest.json`` is only an atomically
replaced convenience pointer and is always revalidated from the journal.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Mapping

from cmd_audit.core.state_codec import append_jsonl_fsync, atomic_json_write, content_sha256


SCHEMA_VERSION = "cmd-v4-run-checkpoint-v1"


def _hash(value: object) -> str:
    return content_sha256(value)


@dataclass(frozen=True)
class RunCheckpoint:
    run_id: str
    manifest_sha256: str
    case_stream_sha256: str
    next_position: int
    last_completed_event_index: int
    global_policy_snapshot: Mapping[str, object]
    arm_policy_snapshots: Mapping[str, Mapping[str, object]]
    repository_identities: Mapping[str, str]
    settlement_head: str
    pending_root: str
    router_snapshots: Mapping[str, object]
    checkpoint_sha256: str = ""
    outcome_head: str = "0" * 64
    outcome_count: int = 0

    def __post_init__(self) -> None:
        if not self.run_id or not self.manifest_sha256 or not self.case_stream_sha256:
            raise ValueError("checkpoint identity is required")
        if self.next_position < 0 or self.last_completed_event_index < -1:
            raise ValueError("checkpoint watermark is invalid")
        expected = _hash({k: v for k, v in asdict(self).items() if k != "checkpoint_sha256"})
        if self.checkpoint_sha256 and self.checkpoint_sha256 != expected:
            raise ValueError("checkpoint hash mismatch")
        if not self.checkpoint_sha256:
            object.__setattr__(self, "checkpoint_sha256", expected)

    def mapping(self) -> dict[str, object]:
        return {"schema_version": SCHEMA_VERSION, **asdict(self)}

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "RunCheckpoint":
        if set(value) != {"schema_version", *cls.__dataclass_fields__} or value["schema_version"] != SCHEMA_VERSION:
            raise ValueError("checkpoint schema mismatch")
        return cls(**{k: value[k] for k in cls.__dataclass_fields__})


class RunCheckpointStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.journal = self.directory / "checkpoints.jsonl"
        self.latest = self.directory / "latest.json"

    def commit(self, checkpoint: RunCheckpoint) -> None:
        self.prepare(checkpoint)
        self.commit_prepared(checkpoint)

    def prepare(self, checkpoint: RunCheckpoint) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._append("checkpoint_prepared", checkpoint)

    def commit_prepared(self, checkpoint: RunCheckpoint) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._append("checkpoint_committed", checkpoint)
        # Preserve v1 pointer bytes (default separators, ASCII), while making
        # replacement reusable and explicit.
        atomic_json_write(self.latest, checkpoint.mapping())

    def _append(self, phase: str, checkpoint: RunCheckpoint) -> None:
        previous = self._journal_head()
        event = {"schema_version": SCHEMA_VERSION, "event_index": previous[0] + 1, "previous_event_hash": previous[1], "phase": phase, "checkpoint": checkpoint.mapping()}
        event["event_hash"] = _hash(event)
        append_jsonl_fsync(self.journal, event)

    def load_latest(self, *, manifest_sha256: str, case_stream_sha256: str) -> RunCheckpoint:
        if not self.journal.exists():
            raise ValueError("no committed checkpoint")
        committed: dict[str, RunCheckpoint] = {}; prepared: set[str] = set(); index = 0; head = "0" * 64
        for line in self.journal.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if set(event) != {"schema_version", "event_index", "previous_event_hash", "phase", "checkpoint", "event_hash"} or event.get("schema_version") != SCHEMA_VERSION or event.get("event_index") != index + 1 or event.get("previous_event_hash") != head:
                raise ValueError("checkpoint journal discontinuity")
            raw = {k: v for k, v in event.items() if k != "event_hash"}
            if event.get("event_hash") != _hash(raw):
                raise ValueError("checkpoint journal hash mismatch")
            checkpoint = RunCheckpoint.from_mapping(event["checkpoint"])
            if event.get("phase") == "checkpoint_prepared": prepared.add(checkpoint.checkpoint_sha256)
            elif event.get("phase") == "checkpoint_committed":
                if checkpoint.checkpoint_sha256 not in prepared: raise ValueError("checkpoint commit lacks prepared record")
                committed[checkpoint.checkpoint_sha256] = checkpoint
            else: raise ValueError("checkpoint phase is invalid")
            index += 1; head = event["event_hash"]
        if not committed: raise ValueError("no committed checkpoint")
        checkpoint = max(committed.values(), key=lambda row: row.next_position)
        if checkpoint.manifest_sha256 != manifest_sha256 or checkpoint.case_stream_sha256 != case_stream_sha256:
            raise ValueError("checkpoint manifest/case-stream mismatch")
        return checkpoint

    def _journal_head(self) -> tuple[int, str]:
        if not self.journal.exists(): return 0, "0" * 64
        lines = [line for line in self.journal.read_text(encoding="utf-8").splitlines() if line]
        if not lines: return 0, "0" * 64
        # Validate through public loader's syntax without requiring bindings.
        index = 0; head = "0" * 64
        for line in lines:
            event = json.loads(line); raw = {k: v for k, v in event.items() if k != "event_hash"}
            if event.get("event_index") != index + 1 or event.get("previous_event_hash") != head or event.get("event_hash") != _hash(raw): raise ValueError("checkpoint journal discontinuity")
            index += 1; head = event["event_hash"]
        return index, head


class OutcomeJournal:
    """Append-only per-case outcome truth source, independent of reports."""
    SCHEMA = "cmd-v4-outcome-journal-v1"
    def __init__(self, path: Path) -> None:
        self.path = Path(path); self.events: list[dict[str, object]] = []; self.head = "0" * 64
        if self.path.exists(): self._load()
    @staticmethod
    def _digest(value: object) -> str: return _hash(value)
    def append(self, position: int, case_id: str, rows: list[Mapping[str, object]]) -> None:
        key = (position, case_id); payload = {"position": position, "case_id": case_id, "rows": rows}
        for existing in self.events:
            if (existing["position"], existing["case_id"]) == key:
                if existing["rows"] != rows: raise ValueError("outcome journal logical collision")
                return
        event = {"schema_version": self.SCHEMA, "event_index": len(self.events)+1, "previous_hash": self.head, **payload}
        event["event_hash"] = _hash(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        append_jsonl_fsync(self.path, event)
        self.events.append(event); self.head = event["event_hash"]
    def prefix(self, head: str, count: int) -> tuple[list[dict[str, object]], ...]:
        if count < 0 or count > len(self.events): raise ValueError("outcome journal prefix count mismatch")
        chosen = self.events[:count]
        actual = "0" * 64 if not chosen else str(chosen[-1]["event_hash"])
        if actual != head: raise ValueError("outcome journal prefix head mismatch")
        return tuple(chosen)
    def _load(self) -> None:
        for line in self.path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            required = {"schema_version", "event_index", "previous_hash", "event_hash", "position", "case_id", "rows"}
            if set(event) != required or event["schema_version"] != self.SCHEMA or event["event_index"] != len(self.events)+1 or event["previous_hash"] != self.head or event["event_hash"] != _hash({k:v for k,v in event.items() if k != "event_hash"}): raise ValueError("outcome journal chain mismatch")
            if not isinstance(event["position"], int) or event["position"] < 1 or not isinstance(event["case_id"], str) or not isinstance(event["rows"], list): raise ValueError("outcome journal event invalid")
            self.events.append(event); self.head = event["event_hash"]
