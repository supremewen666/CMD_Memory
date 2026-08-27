from __future__ import annotations

import json
from pathlib import Path

import pytest

from cmd_audit.spec_v03.freeze import DEVELOPMENT_STATUS, FROZEN_STATUS, FreezeConfig, FreezeError, compile_freeze_bundle
from experiments.spec_v03_freeze import main


ROOT = Path("data/external/group_a")
GROUP_B = Path("data/external/group_b")


def _config() -> dict[str, object]:
    return {
        "source_quotas": {"halumem": 1, "memfail": 1},
        "incident_quotas": {"clean": 2, "process_fault": 1, "state_drift": 1, "poison": 1},
        "template_quotas": {"clean": 2, "drop": 1, "explicit_supersede": 1, "untrusted_injection": 1},
        "order_seeds": [101, 103, 107],
        "order_schedule": "stationary",
        "case_seed": 41,
        "split_seed": 43,
        "template_partition": {"drop": "D_skill", "explicit_supersede": "D_skill", "untrusted_injection": "D_skill"},
    }


def test_freeze_is_reproducible_and_runtime_is_unsealed_only(tmp_path: Path) -> None:
    config = FreezeConfig.from_mapping(_config())
    first = compile_freeze_bundle(config, output_dir=tmp_path / "one", group_a_root=ROOT, group_b_root=GROUP_B)
    second = compile_freeze_bundle(config, output_dir=tmp_path / "two", group_a_root=ROOT, group_b_root=GROUP_B)

    assert first["status"] == DEVELOPMENT_STATUS
    assert first["config_sha256"] == second["config_sha256"]
    assert first["checksums"] == second["checksums"]
    assert (tmp_path / "one" / "sealed" / "lockbox" / "lockbox_manifest.json").is_file()
    runtime = (tmp_path / "one" / "runtime" / "runtime_cases.json").read_text(encoding="utf-8").casefold()
    assert "evaluator_only" not in runtime
    assert "template_id" not in runtime
    assert len(list((tmp_path / "one" / "sealed" / "lockbox").glob("order_manifest_seed_*.json"))) == 3
    assert len(first["compiler_closure"]) == 7
    assert first["manifest_body_sha256"]
    assert all(len(splits) == 1 for splits in first["exception_template_split_cardinality"].values())


def test_frozen_label_requires_both_explicit_authorizations(tmp_path: Path) -> None:
    config = FreezeConfig.from_mapping(_config())
    partial = compile_freeze_bundle(config, output_dir=tmp_path / "partial", group_a_root=ROOT, group_b_root=GROUP_B, freeze_id="f-001")
    with pytest.raises(FreezeError, match="sealed-output-dir"):
        compile_freeze_bundle(config, output_dir=tmp_path / "missing-sealed", group_a_root=ROOT, group_b_root=GROUP_B, freeze_id="f-002", acknowledge_lockbox=True)
    frozen = compile_freeze_bundle(config, output_dir=tmp_path / "frozen", sealed_output_dir=tmp_path / "frozen-sealed", group_a_root=ROOT, group_b_root=GROUP_B, freeze_id="f-002", acknowledge_lockbox=True)

    assert partial["status"] == DEVELOPMENT_STATUS
    assert frozen["status"] == FROZEN_STATUS
    assert frozen["confirmation"]["non_confirmatory"] is False
    assert (tmp_path / "frozen-sealed" / "lockbox" / "runtime_cases.json").is_file()
    assert not (tmp_path / "frozen" / "sealed").exists()


def test_blocked_source_and_split_conflict_fail_closed(tmp_path: Path) -> None:
    blocked = _config()
    blocked["source_quotas"] = {"halumem": 1, "stale": 1}
    with pytest.raises(FreezeError, match="blocked source requested"):
        compile_freeze_bundle(FreezeConfig.from_mapping(blocked), output_dir=tmp_path / "blocked", group_a_root=ROOT, group_b_root=GROUP_B)

    conflict = _config()
    conflict["forced_split_assignments"] = {"not-a-case": "D_skill"}
    with pytest.raises(FreezeError, match="unknown cases"):
        compile_freeze_bundle(FreezeConfig.from_mapping(conflict), output_dir=tmp_path / "conflict", group_a_root=ROOT, group_b_root=GROUP_B)

    unavailable = _config()
    unavailable["template_quotas"] = {"sleeper_trigger": 1}
    with pytest.raises(FreezeError, match="template quota unavailable"):
        compile_freeze_bundle(FreezeConfig.from_mapping(unavailable), output_dir=tmp_path / "quota", group_a_root=ROOT, group_b_root=GROUP_B)


def test_cli_can_publish_explicit_f_data_freeze(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_path = tmp_path / "freeze.json"
    config_path.write_text(json.dumps(_config()), encoding="utf-8")
    output = tmp_path / "bundle"
    sealed = tmp_path / "sealed"
    assert main(["--config", str(config_path), "--output-dir", str(output), "--sealed-output-dir", str(sealed), "--group-a-root", str(ROOT), "--group-b-root", str(GROUP_B), "--freeze-id", "f-003", "--acknowledge-lockbox"]) == 0
    assert json.loads((output / "f_data_manifest.json").read_text(encoding="utf-8"))["status"] == FROZEN_STATUS
    assert "status=F_DATA_FROZEN" in capsys.readouterr().out
