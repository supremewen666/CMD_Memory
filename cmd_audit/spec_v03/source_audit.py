"""Download-manifest integrity audit with explicit unavailable-source status."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .contracts import canonical_sha256


@dataclass(frozen=True)
class DatasetAudit:
    dataset_id: str
    status: str
    executable: bool
    files_checked: int
    errors: tuple[str, ...]
    manifest_sha256: str | None


@dataclass(frozen=True)
class DownloadAuditReport:
    schema_version: str
    group_a_manifest: str
    group_b_inventory: str
    datasets: tuple[DatasetAudit, ...]
    report_sha256: str

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


@lru_cache(maxsize=None)
def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_mapping(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _audit_group_a(root: Path) -> list[DatasetAudit]:
    manifest_path = root / "download_manifest.json"
    if not manifest_path.is_file():
        return [DatasetAudit("group_a", "blocked", False, 0, ("download_manifest.json missing",), None)]
    raw = _read_mapping(manifest_path)
    datasets = raw.get("datasets")
    if not isinstance(datasets, Mapping):
        return [DatasetAudit("group_a", "blocked", False, 0, ("datasets mapping missing",), _sha256(manifest_path))]
    result: list[DatasetAudit] = []
    for dataset_id, metadata in sorted(datasets.items()):
        if not isinstance(metadata, Mapping):
            result.append(DatasetAudit(str(dataset_id), "blocked", False, 0, ("invalid dataset metadata",), _sha256(manifest_path)))
            continue
        status = str(metadata.get("status", "blocked"))
        dataset_root = root / str(metadata.get("path", "")).removeprefix("data/external/group_a/")
        errors: list[str] = []
        checked = 0
        files = metadata.get("files", {})
        if not dataset_root.is_dir():
            errors.append("declared dataset path missing")
        elif dataset_id == "MemFail" and isinstance(files, Mapping):
            for name, expected in files.items():
                path = dataset_root / str(name)
                checked += 1
                if not path.is_file() or _sha256(path) != expected:
                    errors.append(f"checksum mismatch: {name}")
        elif dataset_id == "HaluMem" and isinstance(files, Mapping):
            for name, expected in files.items():
                path = dataset_root / str(name)
                checked += 1
                if not path.is_file() or _sha256(path) != expected:
                    errors.append(f"checksum mismatch: {name}")
        elif dataset_id == "MemTraceBench":
            checksum_file = dataset_root / "SHA256SUMS.txt"
            expected = files.get("sha256_manifest_sha256") if isinstance(files, Mapping) else None
            checked = 1
            if not checksum_file.is_file() or not isinstance(expected, str) or _sha256(checksum_file) != expected:
                errors.append("SHA256SUMS checksum mismatch")
            else:
                for line in checksum_file.read_text(encoding="utf-8").splitlines():
                    digest, relative = line.split(maxsplit=1)
                    path = dataset_root / relative.removeprefix("./")
                    checked += 1
                    if not path.is_file():
                        errors.append(f"missing payload: {relative}")
                    elif _sha256(path) != digest:
                        errors.append(f"checksum mismatch: {relative}")
        else:
            errors.append("no spec-v0.3 verifier for manifest entry")
        executable = status == "downloaded" and not errors
        result.append(DatasetAudit(str(dataset_id).casefold(), status, executable, checked, tuple(errors), _sha256(manifest_path)))
    # STALE is required to be explicit even though it has no payload entry.
    locomo = root / "LoCoMo/locomo10.json"
    locomo_expected = "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4"
    locomo_errors = () if locomo.is_file() and _sha256(locomo) == locomo_expected else ("LoCoMo payload missing or checksum mismatch",)
    result.append(DatasetAudit("locomo", "downloaded" if not locomo_errors else "blocked", not locomo_errors, 1, locomo_errors, _sha256(locomo) if locomo.is_file() else None))
    stale = root / "STALE"
    result.append(DatasetAudit("stale", "blocked", False, 0, ("official instances unavailable; no substitute permitted",), None if not stale.exists() else None))
    return result


def _audit_group_b(root: Path) -> list[DatasetAudit]:
    manifest_path = root / "download_manifest.json"
    inventory_path = root / "DATASET_INVENTORY.json"
    if not inventory_path.is_file():
        return [DatasetAudit("group_b", "blocked", False, 0, ("DATASET_INVENTORY.json missing",), None)]
    inventory = _read_mapping(inventory_path)
    entries = inventory.get("datasets")
    if not isinstance(entries, list):
        return [DatasetAudit("group_b", "blocked", False, 0, ("inventory datasets missing",), _sha256(inventory_path))]
    result: list[DatasetAudit] = []
    for entry in entries:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("dataset_id"), str) or not isinstance(entry.get("manifest"), str):
            result.append(DatasetAudit("group_b_invalid", "blocked", False, 0, ("invalid inventory entry",), _sha256(inventory_path)))
            continue
        dataset_id = str(entry["dataset_id"])
        path = root / str(entry["manifest"])
        try:
            metadata = _read_mapping(path)
        except (OSError, ValueError) as exc:
            result.append(DatasetAudit(dataset_id, "blocked", False, 0, (str(exc),), _sha256(inventory_path)))
            continue
        status = str(metadata.get("status", "blocked"))
        errors: list[str] = []
        checked = 0
        for payload in metadata.get("payloads", ()):
            if not isinstance(payload, Mapping):
                errors.append("invalid payload entry")
                continue
            file_path = path.parent / str(payload.get("path", ""))
            expected = payload.get("sha256")
            checked += 1
            if not file_path.is_file() or not isinstance(expected, str) or _sha256(file_path) != expected:
                errors.append(f"checksum mismatch: {payload.get('path', '')}")
        # These two downloaded collections are protocol/auxiliary material, not
        # repair truth.  Their SHA validation is still reported.
        executable = status == "acquired" and not errors and dataset_id not in {"evo_memory", "evo_bench"}
        if dataset_id in {"evo_memory", "evo_bench"}:
            errors.append("auxiliary/protocol only; not executable repair truth")
        if status == "blocked":
            reason = metadata.get("blocked_reason")
            if not isinstance(reason, str) or not reason:
                errors.append("blocked manifest lacks reason")
            else:
                errors.append(f"blocked: {reason}")
        result.append(DatasetAudit(dataset_id, status, executable, checked, tuple(errors), _sha256(path)))
    return result


def audit_downloads(group_a_root: str | Path, group_b_root: str | Path) -> DownloadAuditReport:
    rows = tuple(_audit_group_a(Path(group_a_root)) + _audit_group_b(Path(group_b_root)))
    body = {
        "schema_version": "cmd-spec-v03-download-audit-v1",
        "group_a_manifest": str(Path(group_a_root) / "download_manifest.json"),
        "group_b_inventory": str(Path(group_b_root) / "DATASET_INVENTORY.json"),
        "datasets": [asdict(row) for row in rows],
    }
    return DownloadAuditReport(
        schema_version=str(body["schema_version"]),
        group_a_manifest=str(body["group_a_manifest"]),
        group_b_inventory=str(body["group_b_inventory"]),
        datasets=rows,
        report_sha256=canonical_sha256(body),
    )
