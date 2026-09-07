"""Frozen evidence contract shared by skill-based Stage 9 competitors."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Mapping

try:
    from .industry_common import (
        AdapterRequestView,
        ProtocolConfig,
        ProtocolError,
        UsageLedger,
        json_safe,
        public_events,
    )
except ImportError:  # Direct execution inside a pinned competitor virtualenv.
    from industry_common import (
        AdapterRequestView,
        ProtocolConfig,
        ProtocolError,
        UsageLedger,
        json_safe,
        public_events,
    )


ARTIFACT_SCHEMA = "cmd-frozen-skill-evidence-v1"
_ARTIFACT_FIELDS = {
    "schema_version", "system_id", "implementation", "artifact_revision",
    "producer_repository", "producer_commit", "frozen", "training_splits", "records",
}
_RECORD_FIELDS = {
    "evidence", "selected_skill_ids", "retrieval_trace", "source_event_ids", "usage",
}
_USAGE_FIELDS = {
    "llm_calls", "input_tokens", "output_tokens", "wall_clock_seconds", "gpu_seconds",
}
_TRAINING_SPLITS = frozenset({"D_skill", "D_router", "D_cal", "D_lifecycle"})
_FORBIDDEN_KEYS = frozenset({
    "evaluator", "ground_truth", "oracle", "oracle_operator", "sealed",
    "shadow_outcome", "shadow_outcome_matrix",
})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contains_forbidden_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).casefold() in _FORBIDDEN_KEYS or _contains_forbidden_key(nested)
            for key, nested in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def _usage_values(value: object) -> tuple[int, int, int, int, float]:
    if not isinstance(value, Mapping) or set(value) != _USAGE_FIELDS:
        raise ProtocolError("skill evidence usage must use the closed resource schema")
    counters = tuple(value[field] for field in ("llm_calls", "input_tokens", "output_tokens", "gpu_seconds"))
    wall = value["wall_clock_seconds"]
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in counters):
        raise ProtocolError("skill evidence resource counters are invalid")
    if (
        isinstance(wall, bool) or not isinstance(wall, (int, float))
        or not math.isfinite(float(wall)) or float(wall) < 0
    ):
        raise ProtocolError("skill evidence wall-clock usage is invalid")
    return counters[0], counters[1], counters[2], counters[3], float(wall)


def _validate_record(record: object) -> Mapping[str, object]:
    if not isinstance(record, Mapping) or set(record) != _RECORD_FIELDS:
        raise ProtocolError("frozen skill evidence record must use the closed schema")
    skill_ids = record["selected_skill_ids"]
    trace = record["retrieval_trace"]
    source_ids = record["source_event_ids"]
    if (
        not isinstance(skill_ids, list)
        or any(not isinstance(item, str) or not item for item in skill_ids)
        or len(set(skill_ids)) != len(skill_ids)
        or not isinstance(trace, list)
        or not isinstance(source_ids, list)
        or any(not isinstance(item, str) or not item for item in source_ids)
        or len(set(source_ids)) != len(source_ids)
    ):
        raise ProtocolError("frozen skill evidence record is invalid")
    if _contains_forbidden_key(record["evidence"]) or _contains_forbidden_key(trace):
        raise ProtocolError("skill evidence contains evaluator-only fields")
    _usage_values(record["usage"])
    return record


def validate_frozen_skill_artifact(
    artifact: object,
    *,
    expected_system_id: str,
    allowed_implementations: frozenset[str],
) -> tuple[Mapping[str, object], str]:
    """Validate the complete frozen artifact before any evaluation lookup."""

    if not isinstance(artifact, Mapping) or set(artifact) != _ARTIFACT_FIELDS:
        raise ProtocolError("frozen skill evidence artifact must use the closed schema")
    implementation = artifact["implementation"]
    if (
        artifact["schema_version"] != ARTIFACT_SCHEMA
        or artifact["system_id"] != expected_system_id
        or implementation not in allowed_implementations
        or artifact["frozen"] is not True
    ):
        raise ProtocolError("frozen skill evidence identity is invalid")
    splits = artifact["training_splits"]
    if (
        not isinstance(splits, list)
        or not splits
        or any(not isinstance(item, str) for item in splits)
        or len(set(splits)) != len(splits)
        or not set(splits).issubset(_TRAINING_SPLITS)
    ):
        raise ProtocolError("skill competitor artifact used an evaluation split for training")
    revision = artifact["artifact_revision"]
    producer_repository = artifact["producer_repository"]
    producer_commit = artifact["producer_commit"]
    records = artifact["records"]
    if (
        not isinstance(revision, str) or not revision
        or not isinstance(producer_repository, str) or not producer_repository
        or not isinstance(producer_commit, str) or len(producer_commit) != 40
        or any(character not in "0123456789abcdef" for character in producer_commit)
        or not isinstance(records, Mapping) or not records
    ):
        raise ProtocolError("frozen skill evidence metadata is invalid")
    for case_id, record in records.items():
        if not isinstance(case_id, str) or not case_id:
            raise ProtocolError("frozen skill evidence case IDs must be non-empty strings")
        _validate_record(record)
    return records, revision


def _usage(value: object, ledger: UsageLedger) -> None:
    llm_calls, input_tokens, output_tokens, gpu_seconds, wall = _usage_values(value)
    ledger.record_batch(
        llm_calls=llm_calls, input_tokens=input_tokens, output_tokens=output_tokens,
        gpu_seconds=gpu_seconds, wall_clock_seconds=wall,
    )


def load_frozen_skill_evidence(
    request: AdapterRequestView,
    protocol: ProtocolConfig,
    *,
    expected_system_id: str,
    allowed_implementations: frozenset[str],
    ledger: UsageLedger,
) -> tuple[object, str]:
    """Load one case's gold-free evidence from a frozen competitor artifact."""

    config = protocol.system
    fields = {"artifact_path", "artifact_sha256", "implementation"}
    if set(config) != fields:
        raise ProtocolError(f"{expected_system_id} wrapper config must use the closed schema")
    path = Path(str(config["artifact_path"]))
    expected_sha = str(config["artifact_sha256"]).lower()
    if len(expected_sha) != 64 or any(char not in "0123456789abcdef" for char in expected_sha):
        raise ProtocolError("artifact_sha256 must be an exact SHA-256 digest")
    try:
        actual_sha = _sha256(path)
    except OSError as exc:
        raise ProtocolError("frozen skill evidence artifact is unreadable") from exc
    if actual_sha != expected_sha:
        raise ProtocolError("frozen skill evidence digest mismatch")
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError("frozen skill evidence artifact is unreadable") from exc
    implementation = config["implementation"]
    records, revision = validate_frozen_skill_artifact(
        artifact, expected_system_id=expected_system_id,
        allowed_implementations=allowed_implementations,
    )
    if artifact["implementation"] != implementation:
        raise ProtocolError("frozen skill evidence identity is invalid")
    case_id = request.decision.get("case_id")
    record = records.get(case_id)
    if not isinstance(record, Mapping):
        raise ProtocolError("frozen skill evidence omitted the requested case")
    record = _validate_record(record)
    source_ids = record["source_event_ids"]
    visible_event_ids = {event.get("event_id") for event in public_events(request)}
    if not set(source_ids).issubset(visible_event_ids):
        raise ProtocolError("skill evidence cites an event outside the serving view")
    _usage(record["usage"], ledger)
    return json_safe({
        "evidence": record["evidence"],
        "selected_skill_ids": record["selected_skill_ids"],
        "retrieval_trace": record["retrieval_trace"],
        "source_event_ids": source_ids,
    }), revision
