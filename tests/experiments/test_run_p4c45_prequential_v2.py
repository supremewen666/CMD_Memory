from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.run_p4c45_prequential_v2 import run_p4c45_prequential_v2


ROOT = Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "experiments" / "fixtures" / "p4c_zero_call_v1.jsonl"
CONFIG = ROOT / "experiments" / "fixtures" / "p4c45_prequential_v2.json"


def test_v2_separates_final_correction_from_shadow_resolution(tmp_path: Path) -> None:
    report = run_p4c45_prequential_v2(
        overlay_path=OVERLAY,
        config_path=CONFIG,
        output_dir=tmp_path / "run",
    )

    assert report["case_count"] == 600
    assert report["outcome_count"] == 4800
    assert report["phase_case_counts"] == {
        "calibration": 120,
        "adaptation": 240,
        "holdout": 240,
    }
    assert report["model_call_count"] == 0
    assert report["runtime_uses_gold"] is False
    assert report["router_feedback_channel"].endswith("only")
    assert report["evidence_units"]["base_structural_templates"] == 3
    assert report["evidence_units"]["independent_real_source_cases"] == 0

    static = report["arms"]["static_typed"]["overall"]
    assert static["shadow_resolution_rate"]["value"] == 1.0
    assert static["safe_correction_rate"]["value"] < 1.0
    assert static["safe_correction_rate"]["denominator"] == 600
    assert static["ecc_acceptance_rate"]["denominator"] == 600

    no_repair = report["arms"]["no_repair"]["overall"]
    assert no_repair["mean_typed_receipt_reward"] is None
    assert no_repair["safe_correction_rate"]["value"] == 0.0
    assert no_repair["unresolved_after_transition_rate"]["value"] == 1.0

    unsafe = report["arms"]["without_ecc_gate"]["overall"]
    assert unsafe["ecc_acceptance_rate"] == {
        "numerator": 0,
        "denominator": 0,
        "value": None,
    }
    assert unsafe["unsafe_commit_per_incident_rate"]["value"] == 1.0

    for arm in (
        "ghost_zero_frozen",
        "ghost_zero_evolution",
        "ghost_typed_prior_frozen",
        "ghost_typed_prior_evolution",
    ):
        assert report["arms"][arm]["holdout_router_updates"] == 0
        assert report["arms"][arm]["phases"]["holdout"]["router_update_count"] == 0


def test_v2_resume_replays_receipt_updates_deterministically(tmp_path: Path) -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    config["phase_replicates"] = {
        "calibration": 1,
        "adaptation": 1,
        "holdout": 1,
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    interrupted = tmp_path / "interrupted"
    with pytest.raises(RuntimeError, match="injected stop"):
        run_p4c45_prequential_v2(
            overlay_path=OVERLAY,
            config_path=config_path,
            output_dir=interrupted,
            stop_after=9,
        )
    resumed = run_p4c45_prequential_v2(
        overlay_path=OVERLAY,
        config_path=config_path,
        output_dir=interrupted,
        run_mode="resume",
    )
    clean = run_p4c45_prequential_v2(
        overlay_path=OVERLAY,
        config_path=config_path,
        output_dir=tmp_path / "clean",
    )
    assert resumed["outcome_root"] == clean["outcome_root"]
    assert resumed["case_stream_sha256"] == clean["case_stream_sha256"]
