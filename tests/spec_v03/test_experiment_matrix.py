from __future__ import annotations

import json

import pytest

from cmd_audit.spec_v03.experiment_matrix import (
    Budget,
    CONFIRMATORY_FROZEN,
    DEVELOPMENT_UNPINNED,
    DataEntitlement,
    ModelArm,
    SystemArm,
    build_experiment_matrix,
    freeze_manifest,
    validate_manifest,
)
from experiments.spec_v03_experiment_matrix import main


def _pinned_inputs() -> dict[str, object]:
    development = build_experiment_matrix()
    models = tuple(ModelArm(**{**row, "pinned_commit": "a" * 40, "model_snapshot": f"snapshot:{row['arm_id']}"}) for row in development["models"])
    systems = tuple(SystemArm(**{**row, "pinned_commit": "b" * 40}) for row in development["systems"])
    return {
        "budget": Budget(64, 65536, 8192, 1800, 0),
        "data_entitlement": DataEntitlement("approved-data", "dataset@revision", "c" * 64, "d" * 64),
        "models": models,
        "systems": systems,
    }


def test_default_is_explicitly_unpinned_and_cannot_freeze() -> None:
    manifest = build_experiment_matrix()

    assert manifest["status"] == DEVELOPMENT_UNPINNED
    assert not manifest["freeze_eligible"]
    assert all(row["pinned_commit"] == "UNRESOLVED" for row in manifest["models"])
    assert {row["model_id"] for row in manifest["models"]} >= {"Qwen2.5-14B-Instruct", "Qwen3-14B", "Llama-3.1-8B-Instruct", "GPT-4o"}
    with pytest.raises(ValueError, match="confirmatory execution"):
        freeze_manifest(manifest)


def test_stage_eligibility_omits_inapplicable_combinations() -> None:
    manifest = build_experiment_matrix(family_ids=("family-a",))
    by_stage = {stage: [node for node in manifest["run_dag"]["nodes"] if node["stage"] == stage] for stage in ("stage5", "stage6", "stage7", "stage8", "stage9")}

    for stage in ("stage5", "stage6", "stage7"):
        assert {node["system_arm_id"] for node in by_stage[stage]} == {"cmd"}
        assert {node["track"] for node in by_stage[stage]} == {"controlled_a1", "controlled_a2"}
    assert {node["system_arm_id"] for node in by_stage["stage8"]} == {"cmd"}
    assert {node["model_arm_id"] for node in by_stage["stage8"]} == {
        "qwen3-14b-target", "llama-8b-target", "gpt-4o-target",
    }
    assert {node["substage"] for node in by_stage["stage8"]} == {"8A", "8B"}
    assert {node["experiment_variant_id"] for node in by_stage["stage8"] if node["substage"] == "8A"} == {
        "no_repair", "random_legal", "skill_content_only", "reset_online", "frozen_source",
        "niche_shuffled", "mean_only", "reset_prefix", "source_prefix", "oracle_legal_operator",
    }
    assert {node["system_arm_id"] for node in by_stage["stage9"]} == {
        "full-context", "bm25-rag", "no-repair", "oracle", "cmd", "lightmem", "lycheemem", "mem0",
    }
    assert not any(node["system_arm_id"] == "lightmem" for stage in ("stage5", "stage6", "stage7", "stage8") for node in by_stage[stage])


def test_only_eligible_unsupported_runs_remain_in_denominator() -> None:
    development = build_experiment_matrix()
    systems = tuple(
        SystemArm(**{**row, "supported_tracks": ("controlled_a1", "controlled_a2")})
        if row["arm_id"] == "lightmem" else SystemArm(**row)
        for row in development["systems"]
    )
    manifest = build_experiment_matrix(systems=systems)
    rows = [node for node in manifest["run_dag"]["nodes"] if node["stage"] == "stage9" and node["system_arm_id"] == "lightmem" and node["track"] == "native"]

    assert rows and {row["execution_status"] for row in rows} == {"unsupported"}
    assert {row["denominator_status"] for row in rows} == {"included"}
    assert not any(node["execution_status"] == "unsupported" for node in manifest["run_dag"]["nodes"] if node["stage"] != "stage9")


def test_confirmatory_requires_complete_external_pins_and_freezes() -> None:
    with pytest.raises(ValueError, match="exact model commits"):
        build_experiment_matrix(confirmatory=True)

    manifest = build_experiment_matrix(confirmatory=True, **_pinned_inputs())
    frozen = freeze_manifest(manifest)
    assert frozen["status"] == CONFIRMATORY_FROZEN
    assert len(frozen["frozen_sha256"]) == 64
    validate_manifest(frozen, confirmatory=True)


def test_fail_closed_on_budget_baseline_wiring_and_native_score_mix() -> None:
    with pytest.raises(ValueError, match="complete budget"):
        validate_manifest({**build_experiment_matrix(), "unified_budget": {"llm_calls": 1}})
    with pytest.raises(ValueError, match="baseline system"):
        SystemArm("bad", "mem0", ("controlled_a1",), ("cmd_router",), "mem0", "b" * 40)

    manifest = build_experiment_matrix()
    manifest["run_dag"]["nodes"][0]["score_namespace"] = "native"
    with pytest.raises(ValueError, match="run DAG violates"):
        validate_manifest(manifest)


def test_cli_default_is_development_and_confirmatory_requires_config(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "development.json"
    assert main(["--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == DEVELOPMENT_UNPINNED
    assert "status=DEVELOPMENT_UNPINNED" in capsys.readouterr().out

    with pytest.raises(SystemExit, match="2"):
        main(["--confirmatory"])

    pins = _pinned_inputs()
    config = {
        "budget": pins["budget"].to_mapping(),
        "data_entitlement": pins["data_entitlement"].to_mapping(),
        "models": [item.to_mapping() for item in pins["models"]],
        "systems": [item.to_mapping() for item in pins["systems"]],
    }
    config_path = tmp_path / "pins.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    frozen_path = tmp_path / "frozen.json"
    assert main(["--freeze", "--pins-config", str(config_path), "--output", str(frozen_path)]) == 0
    assert json.loads(frozen_path.read_text(encoding="utf-8"))["frozen_sha256"]
