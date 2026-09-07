"""Manifest-gated access to sealed Group A external datasets.

Group A is intentionally *not* a discovery or router-accumulation input.  The
loader verifies the recorded source revisions and every acquired payload before
returning a payload handle.  It does not create ``RepairCase`` objects: doing
so would require deciding how benchmark answer and attribution fields map to
the evaluator-only side of the Section 8 contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator


GROUP_A_ROOT = Path("data/external/group_a")
_DOWNLOAD_MANIFEST = "download_manifest.json"
_SCHEMA_VERSION = "cmd-group-a-dataset-registry-v1"
_EXPECTED_DATASETS = frozenset({"memtracebench", "memfail", "halumem", "stale", "locomo"})
# This seed is a provenance value for a future, explicitly approved sealed
# evaluation order.  It does not authorize using these datasets for discovery,
# router accumulation, calibration, or current experiments.
F_DATA_SPLIT_SEED = 20260826


class GroupAManifestError(ValueError):
    """Raised when a sealed dataset no longer matches its registered source."""


class DatasetBlockedError(GroupAManifestError):
    """Raised when a non-acquired dataset is requested as executable input."""


@dataclass(frozen=True)
class GroupAPayload:
    """A verified payload handle.  Content is deliberately loaded only on demand."""

    dataset_id: str
    path: Path
    sha256: str
    format: str
    record_count: int

    def records(self) -> Iterator[dict[str, Any]]:
        """Yield raw records for an evaluator adapter after integrity validation.

        This is an evaluation-data API.  Callers must construct their
        decision-view/evaluator-only boundary before supplying any records to
        an executor; no runtime repair component imports this module.
        """
        if self.format == "json":
            value = _read_json(self.path)
            if isinstance(value, list):
                for row in value:
                    if not isinstance(row, dict):
                        raise GroupAManifestError(f"{self.path}: JSON list row must be an object")
                    yield row
            elif isinstance(value, dict):
                yield value
            else:  # validation guarantees this, but retain a closed boundary.
                raise GroupAManifestError(f"{self.path}: unsupported JSON root")
        elif self.format == "jsonl":
            with self.path.open(encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise GroupAManifestError(f"{self.path}:{line_number}: invalid JSONL") from exc
                    if not isinstance(row, dict):
                        raise GroupAManifestError(f"{self.path}:{line_number}: JSONL row must be an object")
                    yield row
        elif self.format == "csv":
            with self.path.open(newline="", encoding="utf-8") as handle:
                yield from csv.DictReader(handle)
        else:
            raise GroupAManifestError(f"{self.path}: unsupported payload format {self.format!r}")


@dataclass(frozen=True)
class GroupADatasetManifest:
    dataset_id: str
    display_name: str
    status: str
    spec_role: str
    incident_or_claim: str
    discovery_access: str
    official_source: str
    pinned_revision: str
    split_policy: str
    seed_policy: str
    payloads: tuple[GroupAPayload, ...]
    blocked_reason: str | None


@dataclass(frozen=True)
class GroupAValidationReport:
    valid: bool
    registry_sha256: str
    datasets: tuple[GroupADatasetManifest, ...]
    errors: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "valid": self.valid,
            "registry_sha256": self.registry_sha256,
            "split_manifest": {
                "schema_version": "cmd-group-a-sealed-split-v1",
                "seed": F_DATA_SPLIT_SEED,
                "assignment": "sealed_external",
                "permitted_updates": "none",
                "blocking_key": "source_episode_or_semantic_family",
            },
            "dataset_count": len(self.datasets),
            "acquired": sorted(item.dataset_id for item in self.datasets if item.status == "acquired"),
            "blocked": sorted(item.dataset_id for item in self.datasets if item.status == "blocked"),
            "errors": list(self.errors),
        }


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GroupAManifestError(f"unreadable JSON: {path}: {exc}") from exc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(value: str) -> Path:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise GroupAManifestError("payload path must be a non-empty path below Group A root")
    return path


def _count_records(path: Path, format_name: str) -> int:
    if format_name == "json":
        content = _read_json(path)
        if isinstance(content, list):
            if not all(isinstance(row, dict) for row in content):
                raise GroupAManifestError(f"{path}: JSON list rows must be objects")
            return len(content)
        if isinstance(content, dict):
            return 1
        raise GroupAManifestError(f"{path}: JSON root must be an object or list")
    if format_name == "jsonl":
        count = 0
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise GroupAManifestError(f"{path}:{line_number}: invalid JSONL") from exc
                if not isinstance(row, dict):
                    raise GroupAManifestError(f"{path}:{line_number}: JSONL row must be an object")
                count += 1
        return count
    if format_name == "csv":
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise GroupAManifestError(f"{path}: CSV must contain a header")
            return sum(1 for _ in reader)
    raise GroupAManifestError(f"{path}: unsupported payload format {format_name!r}")


def _payload(dataset_id: str, root: Path, relative_path: str, expected_sha: str, format_name: str, count: int) -> GroupAPayload:
    path = root / _relative(relative_path)
    if not path.is_file():
        raise GroupAManifestError(f"{dataset_id}: missing payload {relative_path}")
    if _sha256(path) != expected_sha:
        raise GroupAManifestError(f"{dataset_id}: checksum mismatch for {relative_path}")
    actual_count = _count_records(path, format_name)
    if actual_count != count:
        raise GroupAManifestError(f"{dataset_id}: record count mismatch for {relative_path}: {actual_count} != {count}")
    return GroupAPayload(dataset_id, path, expected_sha, format_name, count)


def _memtrace_payloads(root: Path, source: dict[str, Any]) -> tuple[GroupAPayload, ...]:
    checksums_path = root / "MemTraceBench/SHA256SUMS.txt"
    expected_manifest_hash = source["files"]["sha256_manifest_sha256"]
    if _sha256(checksums_path) != expected_manifest_hash:
        raise GroupAManifestError("memtracebench: SHA256SUMS manifest checksum mismatch")
    entries: list[GroupAPayload] = []
    for line in checksums_path.read_text(encoding="utf-8").splitlines():
        digest, relative = line.split(maxsplit=1)
        relative = relative.removeprefix("./")
        # SHA256SUMS also covers the upstream README and image assets.  They
        # remain bound by the manifest hash above, but are not data payloads.
        if not relative.endswith(".json"):
            continue
        entries.append(_payload("memtracebench", root / "MemTraceBench", relative, digest, "json", 1))
    expected_count = source["files"]["total_json"]
    if len(entries) != expected_count:
        raise GroupAManifestError(f"memtracebench: expected {expected_count} JSON payloads")
    return tuple(entries)


def load_group_a_catalog(root: str | Path = GROUP_A_ROOT) -> tuple[GroupADatasetManifest, ...]:
    """Build the fixed Section 8 registry without opening result artifacts."""
    base = Path(root)
    raw_manifest = _read_json(base / _DOWNLOAD_MANIFEST)
    if not isinstance(raw_manifest, dict) or not isinstance(raw_manifest.get("datasets"), dict):
        raise GroupAManifestError("download manifest must contain a datasets object")
    sources: dict[str, Any] = raw_manifest["datasets"]
    expected_downloaded = {"MemTraceBench", "MemFail", "HaluMem"}
    if set(sources) != expected_downloaded:
        raise GroupAManifestError("download manifest must list exactly MemTraceBench, MemFail, and HaluMem")

    memtrace = sources["MemTraceBench"]
    memfail = sources["MemFail"]
    halumem = sources["HaluMem"]
    for name, value in sources.items():
        if not isinstance(value, dict) or value.get("status") != "downloaded":
            raise GroupAManifestError(f"{name}: expected downloaded source metadata")

    memfail_payloads = tuple(
        _payload("memfail", base, f"MemFail/{name}", digest, "csv", count)
        for name, digest, count in (
            ("coexisting_facts_dataset.csv", memfail["files"]["coexisting_facts_dataset.csv"], 100),
            ("conditional_facts_dataset_easy.csv", memfail["files"]["conditional_facts_dataset_easy.csv"], 100),
            ("conditional_facts_dataset_hard.csv", memfail["files"]["conditional_facts_dataset_hard.csv"], 100),
            # CSV records, rather than physical lines: several questions have
            # quoted newlines in their multiple-choice prompt.
            ("long_hop_chains.csv", memfail["files"]["long_hop_chains.csv"], 92),
            ("persona_dataset.csv", memfail["files"]["persona_dataset.csv"], 100),
        )
    )
    halumem_payloads = tuple(
        _payload("halumem", base, f"HaluMem/{name}", digest, "jsonl", 20)
        for name, digest in halumem["files"].items()
    )
    locomo_payload = _payload("locomo", base, "LoCoMo/locomo10.json", "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4", "json", 10)

    manifests = (
        GroupADatasetManifest("memtracebench", "MemTraceBench", "acquired", "execution-graph attribution and root localization", "process_fault", "sealed_external_only", memtrace["source_url"], memtrace["revision"], "all official payloads remain sealed; split only by source episode", "no router/discovery seed is permitted", _memtrace_payloads(base, memtrace), None),
        GroupADatasetManifest("memfail", "MemFail", "acquired", "summarization, storage, and retrieval diagnostics", "process_fault", "sealed_external_only", memfail["source_url"], memfail["revision"], "all official payloads remain sealed; split only by source episode", "no router/discovery seed is permitted", memfail_payloads, None),
        GroupADatasetManifest("halumem", "HaluMem", "acquired", "extraction, update, and QA hallucination propagation", "process_fault_or_accumulated_memory_error", "sealed_external_only", halumem["source_url"], halumem["revision"], "all official payloads remain sealed; user UUID is the source episode", "no router/discovery seed is permitted", halumem_payloads, None),
        GroupADatasetManifest("stale", "STALE", "blocked", "implicit conflict and outdated-state behavior", "state_drift", "sealed_external_only", "https://arxiv.org/abs/2605.06527", "ea7d391103a151927cd29d2f01d87597a782bdcb", "not applicable: official instances unavailable", "not applicable", (), "official final benchmark instances were not released; no substitute is permitted"),
        GroupADatasetManifest("locomo", "LoCoMo", "acquired", "long-conversation memory QA", "end_to_end_external_validity", "sealed_external_only", "https://github.com/snap-research/locomo", "3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376", "all official payloads remain sealed; sample_id is the source episode", "no router/discovery seed is permitted", (locomo_payload,), None),
    )
    if {item.dataset_id for item in manifests} != _EXPECTED_DATASETS:
        raise AssertionError("Group A registry definition drifted")
    return manifests


def validate_group_a_catalog(root: str | Path = GROUP_A_ROOT) -> GroupAValidationReport:
    """Verify sources, hashes, parseability, and sealed/blocked data boundaries."""
    base = Path(root)
    try:
        datasets = load_group_a_catalog(base)
    except GroupAManifestError as exc:
        return GroupAValidationReport(False, "", (), (str(exc),))
    registry_view = {
        "schema_version": _SCHEMA_VERSION,
        "download_manifest_sha256": _sha256(base / _DOWNLOAD_MANIFEST),
        "split_seed": F_DATA_SPLIT_SEED,
        "split_assignment": "sealed_external",
        "datasets": [
            {"dataset_id": item.dataset_id, "status": item.status, "revision": item.pinned_revision,
             "payloads": [{"path": payload.path.relative_to(base).as_posix(), "sha256": payload.sha256, "records": payload.record_count} for payload in item.payloads]}
            for item in datasets
        ],
    }
    return GroupAValidationReport(True, _canonical_sha256(registry_view), datasets, ())


def load_group_a_payloads(dataset_id: str, root: str | Path = GROUP_A_ROOT) -> tuple[GroupAPayload, ...]:
    """Return verified payload handles for an evaluator adapter, never a live runtime."""
    report = validate_group_a_catalog(root)
    if not report.valid:
        raise GroupAManifestError("Group A validation failed: " + "; ".join(report.errors))
    dataset = next((item for item in report.datasets if item.dataset_id == dataset_id), None)
    if dataset is None:
        raise GroupAManifestError(f"unknown Group A dataset: {dataset_id}")
    if dataset.status == "blocked":
        raise DatasetBlockedError(f"{dataset_id} is blocked: {dataset.blocked_reason}")
    return dataset.payloads


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
