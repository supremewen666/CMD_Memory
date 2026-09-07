from __future__ import annotations

import json

import pytest

from experiments.run_mix_ghost_ecology_repair import main
from experiments.mix_ghost_ecology_repair import (
    ArmOutcome,
    BlockedData,
    FreezeManifest,
    GroupBConfig,
    GroupBExperimentRunner,
    SeedOrderManifest,
    STAGE_ARMS,
    StageExecutionConfig,
    plan_stage_execution,
)


def _row(case: str, family: str, arm: str, *, committed: bool, resolved: bool = True, incident: str = "process_fault") -> ArmOutcome:
    return ArmOutcome(case, family, incident, arm, committed, resolved, True, True, True)


def test_router_reports_family_macro_regret_and_never_uses_blocked_rows() -> None:
    config = GroupBConfig("mix_ghost_routing", ("random_legal", "mix_ghost", "oracle_legal_operator"))
    report = GroupBExperimentRunner(config).evaluate((
        _row("c1", "f1", "oracle_legal_operator", committed=True),
        _row("c1", "f1", "mix_ghost", committed=True),
        _row("c1", "f1", "random_legal", committed=False),
    ), blocked=(BlockedData("c2", "f2", "source unavailable"),))
    assert report.status == "COMPLETE_WITH_SKIPPED_DATA"
    nsr = report.metrics["router"]["family_macro_normalized_safe_regret"]
    assert nsr["mix_ghost"] == 0.0
    assert nsr["random_legal"] == 0.5
    assert report.skipped[0]["case_id"] == "c2"


def test_repair_reports_false_commits_by_incident() -> None:
    config = GroupBConfig("safe_memory_repair", ("full_governance",))
    report = GroupBExperimentRunner(config).evaluate((
        _row("p", "f", "full_governance", committed=True, resolved=True, incident="poison"),
        _row("c", "f", "full_governance", committed=True, resolved=False, incident="clean"),
    ))
    repair = report.metrics["repair"]
    assert repair["poison"]["safe_repair_success"] == 1.0
    assert repair["clean"]["false_commit"] == 1.0


_SHA = "a" * 64


def _freeze() -> FreezeManifest:
    return FreezeManifest(
        {key: _SHA for key in (
            "F-DATA", "F-MG-ALG", "F-SKILL", "F-SYNDROME", "F-REWARD", "F-EVAL",
            "F-MODEL", "F-BASELINE",
        )}, _SHA, _SHA, "group-a-decision-view-adapter", _SHA,
    )


def test_stage5_refuses_source_state_and_has_explicit_adapter_blocker() -> None:
    with pytest.raises(ValueError, match="must not import"):
        StageExecutionConfig("stage5_router", STAGE_ARMS["stage5_router"], "T_online", True, _SHA)
    plan = plan_stage_execution(
        StageExecutionConfig("stage5_router", STAGE_ARMS["stage5_router"], "T_online", True),
        _freeze(), SeedOrderManifest(_SHA, (1, 2, 3)), adapter_available=False,
    )
    assert plan.status == "BLOCKED_ADAPTER"
    assert "no cases were executed" in plan.blockers[0]


def test_stage6_and_stage8_enforce_their_isolation_boundaries() -> None:
    with pytest.raises(ValueError, match="frozen residual"):
        StageExecutionConfig("stage6_ecology", STAGE_ARMS["stage6_ecology"], "T_online", True, _SHA)
    with pytest.raises(ValueError, match="target prefix"):
        StageExecutionConfig("stage8a_transfer_state", STAGE_ARMS["stage8a_transfer_state"], "T_online", False, _SHA)
    ready = plan_stage_execution(
        StageExecutionConfig("stage8a_transfer_state", STAGE_ARMS["stage8a_transfer_state"], "T_final", False, _SHA, "T_prefix"),
        _freeze(), SeedOrderManifest(_SHA, (11,)), adapter_available=True,
    )
    assert ready.status == "READY"


def test_dry_run_cli_writes_a_hashed_stage_plan(tmp_path: Path) -> None:
    freeze = _freeze()
    freeze_path = tmp_path / "freeze.json"
    freeze_path.write_text(json.dumps({**freeze.__dict__}), encoding="utf-8")
    seeds_path = tmp_path / "seeds.json"
    seeds_path.write_text(json.dumps({"schema_version": "cmd-mix-ghost-seed-order-v1", "event_order_manifest_sha256": _SHA, "seeds": [7]}), encoding="utf-8")
    output = tmp_path / "plan.json"
    assert main([
        "--stage", "stage5_router", "--dry-run", "--adapter-available",
        "--freeze-manifest", str(freeze_path), "--seed-order-manifest", str(seeds_path),
        "--output", str(output),
    ]) == 0
    plan = json.loads(output.read_text(encoding="utf-8"))
    assert plan["status"] == "READY"
    assert len(plan["plan_sha256"]) == 64
