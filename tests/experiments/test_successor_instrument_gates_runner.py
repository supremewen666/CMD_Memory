"""CLI contract for successor-v3's pre-headroom gate bundle."""

import json
from pathlib import Path

from experiments.run_successor_instrument_gates import main


def _thresholds() -> dict[str, object]:
    return {
        "schema_version": "route-a-successor-v3-freeze-schema-v2",
        "protocol_id": "route-a-successor-semantic-actionability-v3",
        "freeze_stage": "F1",
        "gates": {
            "g0": {
                "relation_precision_min": 0.8,
                "relation_recall_min": 0.8,
                "permutation_fpr_max": 0.1,
                "canary_recall_min": 0.8,
                "abstention_rate_max": 0.1,
                "confidence_level": 0.95,
                "bootstrap_iterations": 100,
                "bootstrap_seed": 1,
                "min_pairs": 2,
                "min_positive_pairs": 1,
                "min_negative_pairs": 1,
                "min_families": 2,
            },
            "g1": {
                "target_precision_min": 0.9,
                "target_recall_min": 0.9,
                "ordering_coverage_min": 0.9,
                "destructive_coverage_min": 0.25,
                "unknown_rate_max": 0.1,
                "conflict_rate_max": 0.1,
                "confidence_level": 0.95,
                "bootstrap_iterations": 100,
                "bootstrap_seed": 2,
                "min_pairs": 1,
                "min_directional_pairs": 1,
                "min_families": 1,
            },
            "g2": {
                "min_firing_cases": 1,
                "min_firing_families": 1,
                "null_false_fire_max": 0.0,
                "field_alignment_max": 0.8,
                "nmi_alarm_max": 0.8,
                "permutation_target_precision_max": 0.5,
                "reusable_value_unique_ratio_max": 0.5,
            },
        },
    }


def _observations() -> dict[str, object]:
    return {
        "protocol_version": "route-a-successor-semantic-actionability-v3",
        "relation_instrument_frozen": True,
        "runtime_uses_gold": False,
        "llm_calls_in_policy_search": 0,
        "relation_observations": [
            {"family_id": "f1", "expected_positive": True,
             "predicted_positive": True, "lane": "calibration"},
            {"family_id": "f2", "expected_positive": False,
             "predicted_positive": False, "lane": "calibration"},
            {"family_id": "p1", "expected_positive": False,
             "predicted_positive": False, "lane": "permutation"},
            {"family_id": "c1", "expected_positive": True,
             "predicted_positive": True, "lane": "canary"},
        ],
        "actionability_observations": [
            {"family_id": "f1", "expected_target_id": "old",
             "predicted_target_id": "old", "destructive_authorized": True,
             "ordering_state": "resolved",
             "evidence_deployment_visible": True,
             "evidence_trusted": True},
        ],
        "predicate_activity": [
            {"predicate": "divergent_pair_member", "fires": 1, "families": 1},
            {"predicate": "superseded_item", "fires": 1, "families": 1},
        ],
        "shortcut_items": [
            {"case_id": "c1", "item_id": "a", "is_target": True,
             "fields": {"store": "same"}, "permutation_predicted_target": True},
            {"case_id": "c1", "item_id": "b", "is_target": False,
             "fields": {"store": "same"}, "permutation_predicted_target": True},
            {"case_id": "c2", "item_id": "c", "is_target": True,
             "fields": {"store": "same"}, "permutation_predicted_target": True},
            {"case_id": "c2", "item_id": "d", "is_target": False,
             "fields": {"store": "same"}, "permutation_predicted_target": True},
        ],
    }


def test_runner_writes_go_only_when_every_pre_headroom_gate_passes(tmp_path: Path) -> None:
    threshold_path = tmp_path / "thresholds.json"
    observation_path = tmp_path / "observations.json"
    output_path = tmp_path / "gate.json"
    threshold_path.write_text(json.dumps(_thresholds()))
    observation_path.write_text(json.dumps(_observations()))

    code = main([
        "--threshold-manifest", str(threshold_path),
        "--observations", str(observation_path),
        "--output", str(output_path),
    ])
    assert code == 0
    payload = json.loads(output_path.read_text())
    assert payload["decision"] == "GO"
    assert payload["headroom_authorized"] is True


def test_runner_refuses_a_manifest_not_frozen_before_reading(tmp_path: Path) -> None:
    bad = _thresholds()
    bad["freeze_stage"] = "F0"
    threshold_path = tmp_path / "thresholds.json"
    observation_path = tmp_path / "observations.json"
    output_path = tmp_path / "gate.json"
    threshold_path.write_text(json.dumps(bad))
    observation_path.write_text(json.dumps(_observations()))

    code = main([
        "--threshold-manifest", str(threshold_path),
        "--observations", str(observation_path),
        "--output", str(output_path),
    ])
    assert code == 2
    assert json.loads(output_path.read_text())["decision"] == "REFUSE"


def test_runner_refuses_model_calls_inside_policy_search(tmp_path: Path) -> None:
    observations = _observations()
    observations["llm_calls_in_policy_search"] = 1
    threshold_path = tmp_path / "thresholds.json"
    observation_path = tmp_path / "observations.json"
    output_path = tmp_path / "gate.json"
    threshold_path.write_text(json.dumps(_thresholds()))
    observation_path.write_text(json.dumps(observations))

    code = main([
        "--threshold-manifest", str(threshold_path),
        "--observations", str(observation_path),
        "--output", str(output_path),
    ])
    assert code == 2
    payload = json.loads(output_path.read_text())
    assert "llm_calls_in_policy_search" in payload["contract_failures"]
