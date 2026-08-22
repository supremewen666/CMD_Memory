from __future__ import annotations

import json
from pathlib import Path

from experiments.run_p4c_zero_call_sweep import (
    load_p4c_zero_call_scenarios,
    run_p4c_zero_call_prior_calibration,
    run_p4c_zero_call_sweep,
)


ROOT = Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "experiments" / "fixtures" / "p4c_zero_call_v1.jsonl"


def test_frozen_p4c_zero_call_overlay_covers_three_root_bound_mechanisms() -> None:
    plan = load_p4c_zero_call_scenarios(OVERLAY)

    assert len(plan.scenarios) == 3
    assert {
        scenario.case.observation["process_fault_subtype"] is not None
        and "process_fault"
        or scenario.case.observation["superseding_memory_id"] is not None
        and "state_drift"
        or "adversarial_poison"
        for scenario in plan.scenarios
    } == {"process_fault", "state_drift", "adversarial_poison"}
    assert all(
        scenario.case.candidates[0].skill_revision_id
        == skill.skill_revision_id
        for scenario, skill in zip(plan.scenarios, plan.skills, strict=True)
    )
    assert len(plan.overlay_sha256) == 64


def test_formal_p4c_zero_call_sweep_uses_real_ghost_receipt_feedback(
    tmp_path: Path,
) -> None:
    result = run_p4c_zero_call_sweep(
        overlay_path=OVERLAY,
        output_dir=tmp_path / "sweep",
    )

    assert result["status"] == "success"
    assert result["router"] == "P4cGhostRouter"
    assert result["case_count"] == 3
    assert result["mechanism_counts"] == {
        "process_fault": 1,
        "state_drift": 1,
        "adversarial_poison": 1,
    }
    assert result["model_call_count"] == 0
    assert result["commit_rate"] == 1.0
    ecology_rows = [
        json.loads(line)
        for line in (tmp_path / "sweep" / "ecology.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    feedback = [row for row in ecology_rows if row["event_type"] == "skill_feedback"]
    assert len(feedback) == 3
    assert {
        row["payload"]["feedback_kind"] for row in feedback
    } == {"ecc_repair_receipt"}
    assert all("typed_reward" not in row["payload"] for row in feedback)


def test_mix_ghost_prior_calibration_reaches_every_support_gate(
    tmp_path: Path,
) -> None:
    result = run_p4c_zero_call_prior_calibration(
        overlay_path=OVERLAY,
        output_dir=tmp_path / "calibration",
    )

    assert result["status"] == "success"
    assert result["case_count"] == 18
    assert result["candidate_count_per_mechanism"] == 2
    assert result["receipts_per_candidate"] == 3
    assert result["commit_rate"] == 0.5
    assert result["rollback_rate"] == 0.5
    assert result["prior_coverage_complete"] is True
    assert result["global_support_ready"] is True
    assert result["pattern_support_ready"] is True
    assert result["local_support_ready"] is True
    assert result["mix_ghost_ready"] is True
    assert result["model_call_count"] == 0
