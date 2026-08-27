"""Manifest-gated access to Group B external evaluation datasets.

This module is intentionally an evaluation-data boundary: it validates frozen
source metadata before exposing acquired payloads and refuses to load datasets
whose official payload acquisition is blocked.  Nothing in the live repair
path imports this module.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any


GROUP_B_ROOT = Path("data/external/group_b")
_SCHEMA_VERSION = "cmd-group-b-dataset-manifest-v1"
_INVENTORY_SCHEMA_VERSION = "cmd-group-b-dataset-inventory-v1"
_EXPECTED_DATASETS = frozenset(
    {"memsecbench", "memevobench", "longmemeval", "evo_memory", "evo_bench"}
)


class GroupBManifestError(ValueError):
    """Raised when Group B metadata or payload integrity is invalid."""


class DatasetBlockedError(GroupBManifestError):
    """Raised when an unavailable dataset is requested as executable input."""


@dataclass(frozen=True)
class GroupBPayload:
    """A verified Group B JSON fixture and its decoded content."""

    path: Path
    sha256: str
    content: object


@dataclass(frozen=True)
class GroupBDatasetManifest:
    dataset_id: str
    display_name: str
    status: str
    discovery_access: str
    spec_role: str
    incident_or_claim: str
    official_source: str
    pinned_revision: str
    payloads: tuple[dict[str, Any], ...]
    blocked_reason: str | None
    path: Path


@dataclass(frozen=True)
class GroupBValidationReport:
    valid: bool
    datasets: tuple[GroupBDatasetManifest, ...]
    errors: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "dataset_count": len(self.datasets),
            "acquired": sorted(item.dataset_id for item in self.datasets if item.status == "acquired"),
            "blocked": sorted(item.dataset_id for item in self.datasets if item.status == "blocked"),
            "errors": list(self.errors),
        }


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GroupBManifestError(f"unreadable JSON: {path}: {exc}") from exc


def _require_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GroupBManifestError(f"{label} must be a JSON object")
    return value


def _safe_relative_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise GroupBManifestError(f"{label} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise GroupBManifestError(f"{label} must stay below its dataset directory")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_group_b_catalog(
    root: str | Path = GROUP_B_ROOT,
) -> tuple[GroupBDatasetManifest, ...]:
    """Load the complete Group B inventory without opening dataset payloads."""
    base = Path(root)
    inventory = _require_mapping(_read_json(base / "DATASET_INVENTORY.json"), "inventory")
    if inventory.get("schema_version") != _INVENTORY_SCHEMA_VERSION:
        raise GroupBManifestError("unsupported Group B inventory schema")
    entries = inventory.get("datasets")
    if not isinstance(entries, list):
        raise GroupBManifestError("inventory datasets must be a list")
    declared_ids = [entry.get("dataset_id") for entry in entries if isinstance(entry, dict)]
    if len(declared_ids) != len(entries) or set(declared_ids) != _EXPECTED_DATASETS:
        raise GroupBManifestError(
            "Group B inventory must contain exactly: " + ", ".join(sorted(_EXPECTED_DATASETS))
        )

    manifests: list[GroupBDatasetManifest] = []
    ids: set[str] = set()
    for entry in entries:
        item = _require_mapping(entry, "inventory dataset entry")
        dataset_id = item.get("dataset_id")
        if not isinstance(dataset_id, str) or not dataset_id:
            raise GroupBManifestError("inventory dataset_id must be a non-empty string")
        if dataset_id in ids:
            raise GroupBManifestError(f"duplicate inventory dataset_id: {dataset_id}")
        ids.add(dataset_id)
        manifest_path = base / _safe_relative_path(item.get("manifest"), "inventory manifest")
        raw = _require_mapping(_read_json(manifest_path), f"manifest {dataset_id}")
        if raw.get("schema_version") != _SCHEMA_VERSION:
            raise GroupBManifestError(f"{dataset_id}: unsupported manifest schema")
        if raw.get("dataset_id") != dataset_id:
            raise GroupBManifestError(f"{dataset_id}: manifest dataset_id does not match inventory")
        payloads = raw.get("payloads")
        if not isinstance(payloads, list):
            raise GroupBManifestError(f"{dataset_id}: payloads must be a list")
        status = raw.get("status")
        if status not in {"acquired", "blocked"}:
            raise GroupBManifestError(f"{dataset_id}: status must be acquired or blocked")
        required_text = (
            "display_name",
            "discovery_access",
            "spec_role",
            "incident_or_claim",
            "official_source",
            "pinned_revision",
        )
        missing = [key for key in required_text if not isinstance(raw.get(key), str) or not raw[key]]
        if missing:
            raise GroupBManifestError(f"{dataset_id}: missing required field(s): {', '.join(missing)}")
        blocked_reason = raw.get("blocked_reason")
        if status == "blocked" and (not isinstance(blocked_reason, str) or not blocked_reason):
            raise GroupBManifestError(f"{dataset_id}: blocked status requires blocked_reason")
        if status == "acquired" and blocked_reason is not None:
            raise GroupBManifestError(f"{dataset_id}: acquired dataset cannot carry blocked_reason")
        manifests.append(
            GroupBDatasetManifest(
                dataset_id=dataset_id,
                display_name=raw["display_name"],
                status=status,
                discovery_access=raw["discovery_access"],
                spec_role=raw["spec_role"],
                incident_or_claim=raw["incident_or_claim"],
                official_source=raw["official_source"],
                pinned_revision=raw["pinned_revision"],
                payloads=tuple(payloads),
                blocked_reason=blocked_reason,
                path=manifest_path,
            )
        )
    return tuple(manifests)


def validate_group_b_catalog(
    root: str | Path = GROUP_B_ROOT,
) -> GroupBValidationReport:
    """Verify every manifest, status boundary, checksum, and JSON root type."""
    try:
        manifests = load_group_b_catalog(root)
    except GroupBManifestError as exc:
        return GroupBValidationReport(False, (), (str(exc),))

    errors: list[str] = []
    for manifest in manifests:
        if manifest.status == "blocked":
            if manifest.payloads:
                errors.append(f"{manifest.dataset_id}: blocked dataset must not declare payloads")
            continue
        if not manifest.payloads:
            errors.append(f"{manifest.dataset_id}: acquired dataset has no payloads")
            continue
        for payload in manifest.payloads:
            try:
                payload_map = _require_mapping(payload, f"{manifest.dataset_id} payload")
                relative_path = _safe_relative_path(payload_map.get("path"), f"{manifest.dataset_id} payload path")
                expected_sha = payload_map.get("sha256")
                if not isinstance(expected_sha, str) or len(expected_sha) != 64:
                    raise GroupBManifestError(f"{manifest.dataset_id}: invalid payload sha256")
                expected_root = payload_map.get("json_root")
                if expected_root not in {"list", "object"}:
                    raise GroupBManifestError(f"{manifest.dataset_id}: json_root must be list or object")
                path = manifest.path.parent / relative_path
                if not path.is_file():
                    raise GroupBManifestError(f"{manifest.dataset_id}: missing payload {relative_path}")
                if _sha256(path) != expected_sha:
                    raise GroupBManifestError(f"{manifest.dataset_id}: checksum mismatch for {relative_path}")
                content = _read_json(path)
                if (expected_root == "list" and not isinstance(content, list)) or (
                    expected_root == "object" and not isinstance(content, dict)
                ):
                    raise GroupBManifestError(f"{manifest.dataset_id}: unexpected JSON root for {relative_path}")
            except GroupBManifestError as exc:
                errors.append(str(exc))
    return GroupBValidationReport(not errors, manifests, tuple(errors))


def load_group_b_payloads(
    dataset_id: str,
    root: str | Path = GROUP_B_ROOT,
) -> tuple[GroupBPayload, ...]:
    """Return acquired JSON payloads only after the complete catalog validates.

    Blocked datasets have no executable input and raise :class:`DatasetBlockedError`.
    This loader is for offline evaluation preparation, never the gold-free runtime.
    """
    report = validate_group_b_catalog(root)
    if not report.valid:
        raise GroupBManifestError("Group B validation failed: " + "; ".join(report.errors))
    manifest = next((item for item in report.datasets if item.dataset_id == dataset_id), None)
    if manifest is None:
        raise GroupBManifestError(f"unknown Group B dataset: {dataset_id}")
    if manifest.status == "blocked":
        raise DatasetBlockedError(f"{dataset_id} is blocked: {manifest.blocked_reason}")
    return tuple(
        GroupBPayload(
            path=manifest.path.parent / payload["path"],
            sha256=payload["sha256"],
            content=_read_json(manifest.path.parent / payload["path"]),
        )
        for payload in manifest.payloads
    )
