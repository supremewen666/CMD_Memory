from __future__ import annotations

import json
import sys

import pytest

from experiments import analyze_arena_results


def _write(path, rows):
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_unified_analysis_writes_descriptive_tables(tmp_path, monkeypatch):
    source = tmp_path / "arena.jsonl"
    _write(
        source,
        (
            {
                "record_type": "arena_manifest",
                "arena_id": "fixture",
                "runtime_uses_gold": False,
            },
            {
                "record_type": "gold_free_observation",
                "arena_id": "fixture",
                "failure_type": "retrieval_error",
                "spearman_rho": 1.0,
                "top1_agreement": True,
                "runtime_abstained": False,
                "oracle_rank_of_selected": 1.0,
                "shadow_regret": 0.0,
                "null_false_positive": False,
                "coordinates": {
                    "age_sessions": 2,
                    "question_type": "current",
                    "evidence_condition": "present",
                },
            },
            {
                "record_type": "top_p_saturation_event",
                "checkpoint": "fixture:1/1",
                "case_id": "case-1",
                "failure_type": "retrieval_error",
                "subset": "fixture",
                "attempted_skill_ids": ["a", "b"],
                "selected_skill_ids": ["a", "b"],
                "gold_free_gains": [["a", 0.5], ["b", 0.3]],
                "cumulative_gain": 0.8,
                "covered": True,
                "repair_effective": True,
                "mean_selected_gain": 0.4,
                "shadow_regret": 0.1,
            },
            {
                "record_type": "ecology_snapshot",
                "checkpoint": "fixture:1/1",
                "event_count": 1,
                "niches": [
                    {
                        "skill_id": "a",
                        "dominant_niche": "retrieval_error",
                        "specialization_index": 1.0,
                        "total_wins": 1,
                        "total_attempts": 1,
                        "win_rates": [["retrieval_error", 1.0]],
                    }
                ],
                "overlaps": [],
                "winner_distribution": [["a", 1.0]],
                "diversity_index": 0.0,
                "jsd_from_previous": None,
            },
            {
                "record_type": "chain_attempt",
                "arena_id": "fixture",
                "first_skill_id": "a",
                "second_skill_id": "b",
                "chain_benefit": 0.1,
            },
            {
                "record_type": "perturbation_event",
                "arena_id": "fixture",
                "removed_skill_id": "a",
                "removal_strategy": "keystone",
                "started_after_case": 1,
                "window_size": 2,
                "stability_threshold": 0.05,
                "stable_windows_required": 1,
                "recovered_after_cases": 4,
                "winnerless_windows": 0,
                "window_jsd": [[3, 0.2], [5, 0.0]],
            },
        ),
    )
    output = tmp_path / "analysis"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze_arena_results.py",
            "--inputs",
            str(source),
            "--output-dir",
            str(output),
        ],
    )
    assert analyze_arena_results.main() == 0
    manifest = json.loads(
        (output / "analysis_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["hypothesis_tests_run"] is False
    assert manifest["case_observations"] == 1
    assert manifest["saturation_events"] == 1
    assert (output / "signal_by_failure.csv").exists()
    assert (output / "saturation_summary.csv").exists()
    assert (output / "skill_contribution.csv").exists()
    assert (output / "chain_benefit_spectrum.csv").exists()
    assert (output / "perturbation_response.csv").exists()


def test_analysis_rejects_gold_dependent_runtime_manifest(tmp_path):
    source = tmp_path / "bad.jsonl"
    _write(
        source,
        (
            {
                "record_type": "arena_manifest",
                "arena_id": "bad",
                "runtime_uses_gold": True,
            },
        ),
    )
    with pytest.raises(ValueError, match="runtime_uses_gold"):
        analyze_arena_results._load_artifacts((source,))
