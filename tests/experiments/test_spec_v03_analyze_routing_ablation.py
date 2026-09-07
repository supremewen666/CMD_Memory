from __future__ import annotations

from experiments.spec_v03_analyze_routing_ablation import ARMS, analyze_report


def _arm(name: str, selected: str, utility: float) -> dict[str, object]:
    selection_id = f"selection-{name}"
    return {
        "arm": name,
        "status": "COMPLETE",
        "selection_records": [{
            "selection_id": selection_id,
            "case_id": "case-1",
            "selected_at_event_index": 0,
            "selected_skill_revision_id": selected,
            "backbone_scores": [["base", 0.8], ["other", 0.2]],
        }],
        "receipt_records": [{
            "selection_id": selection_id,
            "utility": utility,
            "valid": True,
            "rolled_back": False,
            "delayed_regression": False,
            "safety_passed": True,
            "invariant_passed": True,
            "locality_cost": 0.1,
            "collateral_cost": 0.0,
        }],
    }


def test_routing_ablation_analysis_pairs_overrides_and_gate_effect() -> None:
    utilities = {
        "routing_frozen_backbone": ("base", 0.4),
        "routing_global": ("other", 0.5),
        "routing_global_pattern": ("other", 0.6),
        "routing_global_pattern_local": ("other", 0.7),
        "routing_full_no_support_gate": ("other", 0.3),
        "mix_ghost": ("other", 0.8),
    }
    raw = {
        "config": {"run_id": "routing-test", "model_id": "model"},
        "results": {"stage5": {"arms": [
            _arm(arm, *utilities[arm]) for arm in ARMS
        ]}},
    }

    result = analyze_report(raw, source="fixture")

    assert result is not None
    assert result["arm_metrics"]["mix_ghost"]["mean_utility"] == 0.8
    assert result["arm_metrics"]["routing_frozen_backbone"]["override_rate"] == 0.0
    assert result["arm_metrics"]["routing_full_no_support_gate"]["negative_override_rate"] == 1.0
    gate = next(row for row in result["comparisons"] if row["mechanism"] == "support_gate")
    assert gate["mean_delta"] == 0.5
