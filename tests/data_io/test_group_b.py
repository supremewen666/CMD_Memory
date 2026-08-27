from __future__ import annotations

import json
from pathlib import Path

import pytest

from cmd_audit.data_io import (
    DatasetBlockedError,
    GroupBManifestError,
    load_group_b_catalog,
    load_group_b_payloads,
    validate_group_b_catalog,
)
from cmd_audit.cli import main


ROOT = Path("data/external/group_b")


def test_group_b_catalog_has_every_specified_dataset_and_validates() -> None:
    report = validate_group_b_catalog(ROOT)

    assert report.valid, report.errors
    assert {item.dataset_id for item in report.datasets} == {
        "memsecbench", "memevobench", "longmemeval", "evo_memory", "evo_bench"
    }
    assert {item.dataset_id for item in report.datasets if item.status == "blocked"} == {
        "memsecbench", "memevobench", "longmemeval"
    }


def test_blocked_dataset_fails_closed_before_any_payload_load() -> None:
    with pytest.raises(DatasetBlockedError, match="blocked"):
        load_group_b_payloads("memsecbench", ROOT)


def test_acquired_dataset_loader_returns_only_verified_json_payloads() -> None:
    payloads = load_group_b_payloads("evo_memory", ROOT)

    assert len(payloads) == 6
    assert all(isinstance(payload.content, list) for payload in payloads)
    assert {payload.path.name for payload in payloads} == {
        "aime_2024.json", "alfworld.json", "babyai.json", "gpqa_diamond.json", "mmlu_pro.json", "toolbench.json"
    }


def test_checksum_mismatch_is_reported_and_blocks_loading(tmp_path: Path) -> None:
    inventory = json.loads((ROOT / "DATASET_INVENTORY.json").read_text(encoding="utf-8"))
    (tmp_path / "DATASET_INVENTORY.json").write_text(json.dumps(inventory), encoding="utf-8")
    for entry in inventory["datasets"]:
        source = ROOT / entry["manifest"]
        destination = tmp_path / entry["manifest"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    evo_dir = tmp_path / "evo-memory"
    for source in (ROOT / "evo-memory").glob("*.json"):
        if source.name != "dataset_manifest.json":
            (evo_dir / source.name).write_bytes(source.read_bytes())
    for source in (ROOT / "evo-bench").glob("*.json"):
        if source.name != "dataset_manifest.json":
            destination = tmp_path / "evo-bench" / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
    target = evo_dir / "aime_2024.json"
    target.write_text("[]\n", encoding="utf-8")

    report = validate_group_b_catalog(tmp_path)

    assert not report.valid
    assert any("checksum mismatch" in error for error in report.errors)
    with pytest.raises(GroupBManifestError, match="validation failed"):
        load_group_b_payloads("evo_memory", tmp_path)


def test_catalog_rejects_missing_dataset_from_inventory(tmp_path: Path) -> None:
    inventory = json.loads((ROOT / "DATASET_INVENTORY.json").read_text(encoding="utf-8"))
    inventory["datasets"] = inventory["datasets"][:-1]
    (tmp_path / "DATASET_INVENTORY.json").write_text(json.dumps(inventory), encoding="utf-8")

    with pytest.raises(GroupBManifestError, match="must contain exactly"):
        load_group_b_catalog(tmp_path)


def test_cli_exposes_manifest_validation_entrypoint(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate-group-b-data", "--root", str(ROOT)]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["valid"] is True
    assert output["dataset_count"] == 5
