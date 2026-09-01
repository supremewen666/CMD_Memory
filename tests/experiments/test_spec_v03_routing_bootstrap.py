from __future__ import annotations

import json

from cmd_audit.spec_v03.order_only import CaseOrderMetadata
from experiments.spec_v03_analyze_routing_ablation import ARMS
from experiments.spec_v03_routing_bootstrap import _load_case_indexes, _paired_deltas


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


def test_routing_bootstrap_rows_preserve_family_blocks() -> None:
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
    metadata = CaseOrderMetadata("case-1", "family-1", "episode-1", "fixture", "process_fault")

    rows = _paired_deltas(raw, {"case-1": metadata}, source="fixture")

    assert len(rows) == 5
    assert {row["family_id"] for row in rows} == {"family-1"}
    gate = next(row for row in rows if row["mechanism"] == "support_gate")
    assert gate["delta"] == 0.5


def test_routing_bootstrap_loads_repeated_consistent_case_indexes(tmp_path) -> None:
    row = {
        "case_id": "case-1",
        "family_id": "family-1",
        "source_episode_id": "episode-1",
        "source_dataset_id": "fixture",
        "incident_type": "process_fault",
    }
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps([row]))
    second.write_text(json.dumps([row]))

    result = _load_case_indexes([first, second])

    assert result["case-1"].family_id == "family-1"
