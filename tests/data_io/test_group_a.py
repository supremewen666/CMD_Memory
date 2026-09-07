from __future__ import annotations

from pathlib import Path

import pytest

from cmd_audit.data_io.group_a import (
    DatasetBlockedError,
    GroupAManifestError,
    load_group_a_catalog,
    load_group_a_payloads,
    validate_group_a_catalog,
)


ROOT = Path("data/external/group_a")


def test_group_a_catalog_has_all_protocol_datasets_and_validates() -> None:
    report = validate_group_a_catalog(ROOT)

    assert report.valid, report.errors
    assert {item.dataset_id for item in report.datasets} == {"memtracebench", "memfail", "halumem", "stale", "locomo"}
    assert {item.dataset_id for item in report.datasets if item.status == "blocked"} == {"stale"}
    assert len(report.registry_sha256) == 64
    assert report.as_dict()["split_manifest"]["seed"] == 20260826
    assert report.as_dict()["split_manifest"]["permitted_updates"] == "none"


def test_group_a_payloads_are_verified_and_iterable_but_not_runtime_cases() -> None:
    payload = load_group_a_payloads("memfail", ROOT)[0]

    assert payload.record_count == 100
    assert next(payload.records())["question"]


def test_group_a_blocked_dataset_fails_closed() -> None:
    with pytest.raises(DatasetBlockedError, match="blocked"):
        load_group_a_payloads("stale", ROOT)


def test_group_a_validation_rejects_checksum_tampering(tmp_path: Path) -> None:
    # The original manifest still points at its recorded Group A payloads, so
    # changing a copied payload must be observable as a failed validation.
    import shutil

    shutil.copytree(ROOT, tmp_path / "group_a")
    copied = tmp_path / "group_a"
    target = copied / "MemFail/coexisting_facts_dataset.csv"
    target.write_text("changed\n", encoding="utf-8")

    report = validate_group_a_catalog(copied)

    assert not report.valid
    assert "checksum mismatch" in report.errors[0]
    with pytest.raises(GroupAManifestError, match="validation failed"):
        load_group_a_payloads("memfail", copied)


def test_group_a_catalog_keeps_every_external_dataset_sealed() -> None:
    assert all(item.discovery_access == "sealed_external_only" for item in load_group_a_catalog(ROOT))
