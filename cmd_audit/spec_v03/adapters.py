"""Honest, label-free adapters for acquired Group A payloads."""

from __future__ import annotations

import csv
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Iterator

from .contracts import DecisionView
from .source_audit import DownloadAuditReport


_UNSUPPORTED = (
    "incident_type", "root_ground_truth", "legal_operator_ids",
    "safety_oracle", "repair_receipt", "oracle_operator",
)


@lru_cache(maxsize=None)
def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case_id(dataset_id: str, path: Path, index: int) -> str:
    return f"{dataset_id}:{path.name}:{index}:{_sha256(path)[:12]}"


def _decision(
    *, dataset_id: str, path: Path, index: int, source_episode_id: str, family_id: str,
    observation: dict[str, object],
) -> DecisionView:
    case_id = _case_id(dataset_id, path, index)
    return DecisionView(
        case_id=case_id,
        source_dataset_id=dataset_id,
        source_episode_id=source_episode_id,
        family_id=family_id,
        lineage_id=case_id,
        event_index=index,
        observation=observation,
        provenance={"payload_path": path.as_posix(), "payload_sha256": _sha256(path), "adapter": "cmd-spec-v03-group-a-v1"},
        unsupported_fields=_UNSUPPORTED,
    )


def iter_group_a_decision_views(
    dataset_id: str,
    *,
    root: str | Path,
    audit: DownloadAuditReport,
) -> Iterator[DecisionView]:
    """Yield only information available before repair.

    This function deliberately does not read or carry benchmark answer/evidence
    fields.  Datasets without native repair labels remain *adapter-supported*
    only as decision observations; they are not promoted to repair evaluation.
    """
    normalized = dataset_id.casefold()
    status = next((row for row in audit.datasets if row.dataset_id == normalized), None)
    if status is None or not status.executable:
        reason = "not registered" if status is None else "; ".join(status.errors)
        raise ValueError(f"{normalized} is not executable: {reason}")
    base = Path(root)
    if normalized == "memfail":
        for path in sorted((base / "MemFail").glob("*.csv")):
            with path.open(newline="", encoding="utf-8") as handle:
                for index, row in enumerate(csv.DictReader(handle)):
                    category = row.get("preference_category") or path.stem
                    # The question is decision-visible.  ground_truth_answer is
                    # intentionally ignored and never copied into the view.
                    yield _decision(
                        dataset_id=normalized, path=path, index=index,
                        source_episode_id=f"{path.stem}:{category}", family_id=path.stem,
                        observation={"question": row.get("question", ""), "preference_category": category, "source_format": "csv"},
                    )
        return
    if normalized == "halumem":
        for path in sorted((base / "HaluMem").glob("*.jsonl")):
            with path.open(encoding="utf-8") as handle:
                for index, line in enumerate(handle):
                    row = json.loads(line)
                    uuid = row.get("uuid")
                    if not isinstance(uuid, str) or not uuid:
                        raise ValueError(f"{path}:{index + 1} missing UUID")
                    # Persona/session content is left as a source reference;
                    # no answer/evidence/question label crosses this boundary.
                    yield _decision(
                        dataset_id=normalized, path=path, index=index,
                        source_episode_id=uuid, family_id=path.stem,
                        observation={"uuid": uuid, "session_count": len(row.get("sessions", ())), "source_format": "jsonl"},
                    )
        return
    if normalized == "memtracebench":
        for index, path in enumerate(sorted((base / "MemTraceBench").rglob("*.json"))):
            # The trace is executable substrate but its structure does not
            # natively expose incident/root/legal labels.  Keep only an opaque
            # verified reference; a future executor adapter may decode it.
            yield _decision(
                dataset_id=normalized, path=path, index=index,
                source_episode_id=path.stem, family_id=path.parent.name,
                observation={"trace_payload_sha256": _sha256(path), "source_format": "json", "adapter_status": "trace_reference_only"},
            )
        return
    raise ValueError(f"no honest Group A adapter for {normalized}")
