from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.run_p4c45_zero_call import run_p4c45_zero_call


ROOT = Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "experiments" / "fixtures" / "p4c_zero_call_v1.jsonl"
CONFIG = ROOT / "experiments" / "fixtures" / "p4c45_zero_call_v1.json"


def test_p4c45_covers_closed_ablation_and_robustness_matrix(tmp_path: Path) -> None:
    report = run_p4c45_zero_call(
        overlay_path=OVERLAY,
        config_path=CONFIG,
        output_dir=tmp_path / "run",
        run_mode="fresh",
    )

    assert report["status"] == "success"
    assert report["model_call_count"] == 0
    assert report["runtime_uses_gold"] is False
    assert report["router_feedback_channel"].startswith("EccRepairReceipt-only")
    assert report["router_implementation"] == "GHOSTEcologyRouter"
    assert "no observe" in report["router_modes"]["ghost_frozen_posterior"]
    assert "skill_priors=None" in report["router_modes"]["thompson_no_prior"]
    assert set(report["arms"]) == {
        "no_repair",
        "random_legal",
        "static_typed",
        "thompson_no_prior",
        "ghost_frozen_posterior",
        "ghost_receipt_evolution",
        "without_ecc_gate",
        "full_ghost_ecc",
    }
    coverage = report["robustness_coverage"]
    assert coverage["poison_density"] == [1, 3, 8]
    assert coverage["recurrence_rounds"] == 2
    assert coverage["protected_mutation"] is True
    assert coverage["locality_budgets"] == [0.05, 0.25, 1.0]
    assert report["arms"]["without_ecc_gate"]["unsafe_control"] is True
    assert report["arms"]["without_ecc_gate"]["unsafe_commit_count"] > 0
    assert report["arms"]["without_ecc_gate"]["typed_receipt_count"] == 0
    assert report["arms"]["full_ghost_ecc"]["unsafe_commit_count"] == 0
    assert report["arms"]["full_ghost_ecc"]["typed_receipt_count"] == report["case_count"]
    assert report["arms"]["static_typed"]["syndrome_resolution_rate"] > 0


def test_p4c45_crash_resume_is_byte_stable(tmp_path: Path) -> None:
    interrupted = tmp_path / "interrupted"
    with pytest.raises(RuntimeError, match="injected stop"):
        run_p4c45_zero_call(
            overlay_path=OVERLAY,
            config_path=CONFIG,
            output_dir=interrupted,
            run_mode="fresh",
            stop_after=7,
        )
    partial = [
        json.loads(line)
        for line in (interrupted / "outcomes.jsonl").read_text().splitlines()
    ]
    assert len(partial) == 7

    resumed = run_p4c45_zero_call(
        overlay_path=OVERLAY,
        config_path=CONFIG,
        output_dir=interrupted,
        run_mode="resume",
    )
    clean = run_p4c45_zero_call(
        overlay_path=OVERLAY,
        config_path=CONFIG,
        output_dir=tmp_path / "clean",
        run_mode="fresh",
    )
    assert resumed["outcome_root"] == clean["outcome_root"]
    assert resumed["case_count"] == clean["case_count"]
