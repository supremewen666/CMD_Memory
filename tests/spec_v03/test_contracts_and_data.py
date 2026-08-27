from __future__ import annotations

import json
from pathlib import Path

import pytest

from cmd_audit.spec_v03.adapters import iter_group_a_decision_views
from cmd_audit.spec_v03.contracts import deserialize_decision_view
from cmd_audit.spec_v03.source_audit import audit_downloads
from cmd_audit.spec_v03.splits import build_lockbox_manifest, build_split_manifest
from experiments.spec_v03_dry_run import main


GROUP_A = Path("data/external/group_a")
GROUP_B = Path("data/external/group_b")


def test_download_audit_verifies_obtained_group_a_and_blocks_missing_group_b_manifest() -> None:
    report = audit_downloads(GROUP_A, GROUP_B)
    rows = {row.dataset_id: row for row in report.datasets}

    assert rows["memfail"].executable
    assert rows["halumem"].executable
    assert rows["memtracebench"].executable
    assert not rows["stale"].executable
    assert not rows["memsecbench"].executable
    assert not rows["memevobench"].executable
    assert not rows["longmemeval"].executable
    assert rows["evo_memory"].status == "acquired"
    assert not rows["evo_memory"].executable
    assert "auxiliary/protocol" in rows["evo_memory"].errors[-1]
    assert report.group_b_inventory.endswith("data/external/group_b/DATASET_INVENTORY.json")


def test_adapter_never_serializes_memfail_answer_or_repair_labels() -> None:
    audit = audit_downloads(GROUP_A, GROUP_B)
    case = next(iter_group_a_decision_views("memfail", root=GROUP_A, audit=audit))
    text = json.dumps(case.to_mapping()).casefold()

    assert "ground_truth_answer" not in text
    assert "ground_truth_answer" not in case.observation
    assert "incident_type" in case.unsupported_fields
    assert "legal_operator_ids" in case.unsupported_fields


def test_runtime_deserializer_fails_closed_on_evaluator_only_key() -> None:
    audit = audit_downloads(GROUP_A, GROUP_B)
    value = next(iter_group_a_decision_views("halumem", root=GROUP_A, audit=audit)).to_mapping()
    value["observation"] = {"gold_label": "poison"}

    with pytest.raises(ValueError, match="evaluator field"):
        deserialize_decision_view(value)


def test_family_and_source_episode_components_cannot_cross_splits() -> None:
    audit = audit_downloads(GROUP_A, GROUP_B)
    cases = list(iter_group_a_decision_views("memfail", root=GROUP_A, audit=audit))[:12]
    split = build_split_manifest(cases, seed=7)
    lockbox = build_lockbox_manifest(split)
    assigned = split.assignments

    for left in cases:
        for right in cases:
            if left.family_id == right.family_id or left.source_episode_id == right.source_episode_id:
                assert assigned[left.case_id] == assigned[right.case_id]
    assert set(lockbox.lockbox_splits) == {"T_anchor", "T_final"}
    assert lockbox.split_manifest_sha256 == split.content_sha256


def test_dry_run_emits_hashed_manifest_without_model_calls(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "spec-v03-dry-run.json"
    assert main([
        "--dry-run", "--dataset", "memfail", "--limit", "8", "--output", str(output),
    ]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["run_manifest"]["budget"]["llm_calls"] == 0
    assert len(payload["run_manifest"]["content_sha256"]) == 64
    assert "status=DRY_RUN_COMPLETE" in capsys.readouterr().out
