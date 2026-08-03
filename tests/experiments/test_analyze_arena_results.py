from __future__ import annotations

import json
import sys

import pytest

from experiments import analyze_arena_results
from experiments.arena_runner_common import (
    arena_case_ids_sha256,
    arena_file_sha256,
)


def _write(path, rows):
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def _manifest(
    arena_id: str,
    *,
    case_ids: tuple[str, ...] = ("case-1",),
    runtime_uses_gold: bool = False,
    seed: int = 24,
) -> dict[str, object]:
    return {
        "record_type": "arena_manifest",
        "arena_id": arena_id,
        "case_count": len(case_ids),
        "runtime_uses_gold": runtime_uses_gold,
        "seed": seed,
        "dataset_fingerprint_version": "arena-dataset-v1",
        "dataset_source_kind": "file",
        "dataset_source_path": "/unmounted/fixture-cases.json",
        "dataset_source_sha256": "a" * 64,
        "dataset_source_size_bytes": 1,
        "selected_case_ids_sha256": arena_case_ids_sha256(case_ids),
        "selected_cases_sha256": "b" * 64,
    }


def test_unified_analysis_writes_descriptive_tables(tmp_path, monkeypatch):
    source = tmp_path / "arena.jsonl"
    _write(
        source,
        (
            _manifest("fixture"),
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
            _manifest("bad", runtime_uses_gold=True),
        ),
    )
    with pytest.raises(ValueError, match="runtime_uses_gold"):
        analyze_arena_results._load_artifacts((source,))


def test_analysis_namespaces_replicated_arena_artifacts(tmp_path) -> None:
    paths = []
    for index, seed in enumerate((24, 124, 24), start=1):
        artifact = tmp_path / f"replicate-{index}.jsonl"
        _write(
            artifact,
            (
                _manifest("memtrace", seed=seed),
                {
                    "record_type": "top_p_saturation_event",
                    "checkpoint": "memtrace:1/1",
                    "case_id": "case-1",
                },
            ),
        )
        paths.append(artifact)

    records = analyze_arena_results._load_artifacts(tuple(paths))

    assert [
        row["arena_id"] for row in records["arena_manifest"]
    ] == [
        "memtrace_seed24",
        "memtrace_seed124",
        "memtrace_seed24_rep2",
    ]
    assert [
        row["arena_family"] for row in records["arena_manifest"]
    ] == ["memtrace", "memtrace", "memtrace"]
    assert [
        row["checkpoint"] for row in records["top_p_saturation_event"]
    ] == [
        "memtrace_seed24:1/1",
        "memtrace_seed124:1/1",
        "memtrace_seed24_rep2:1/1",
    ]


def test_analysis_rejects_duplicate_artifact_path(tmp_path) -> None:
    artifact = tmp_path / "arena.jsonl"
    _write(
        artifact,
        (
            _manifest("fixture"),
            {
                "record_type": "top_p_saturation_event",
                "checkpoint": "fixture:1/1",
                "case_id": "case-1",
            },
        ),
    )

    with pytest.raises(ValueError, match="duplicate artifact path"):
        analyze_arena_results._load_artifacts((artifact, artifact))


def test_analysis_rejects_unfingerprinted_arena_artifact(tmp_path) -> None:
    source = tmp_path / "unfingerprinted.jsonl"
    _write(
        source,
        (
            {
                "record_type": "arena_manifest",
                "arena_id": "legacy",
                "runtime_uses_gold": False,
            },
        ),
    )

    with pytest.raises(ValueError, match="dataset fingerprint"):
        analyze_arena_results._load_artifacts((source,))


def test_analysis_rejects_case_ids_that_do_not_match_manifest(tmp_path) -> None:
    source = tmp_path / "wrong-case.jsonl"
    _write(
        source,
        (
            _manifest("fixture"),
            {
                "record_type": "top_p_saturation_event",
                "case_id": "different-case",
            },
        ),
    )

    with pytest.raises(ValueError, match="case ids do not match"):
        analyze_arena_results._load_artifacts((source,))


def test_analysis_rejects_changed_mounted_dataset_bytes(tmp_path) -> None:
    dataset = tmp_path / "cases.json"
    dataset.write_text('[{"case_id":"case-1"}]\n', encoding="utf-8")
    manifest = {
        **_manifest("fixture"),
        "dataset_source_path": str(dataset),
        "dataset_source_sha256": arena_file_sha256(dataset),
        "dataset_source_size_bytes": dataset.stat().st_size,
    }
    artifact = tmp_path / "changed-source.jsonl"
    _write(
        artifact,
        (
            manifest,
            {
                "record_type": "top_p_saturation_event",
                "case_id": "case-1",
            },
        ),
    )
    dataset.write_text('[{"case_id":"tampered"}]\n', encoding="utf-8")

    with pytest.raises(ValueError, match="dataset (size|bytes) differs"):
        analyze_arena_results._load_artifacts((artifact,))


def test_cmd_vs_best_of_n_summary_reports_structural_delta() -> None:
    rows = [
        {
            "arena_id": "fixture",
            "failure_type": "retrieval_error",
            "runtime_branch": "fix",
            "candidate_budget": 2,
            "cmd_selected_skill_id": "a",
            "cmd_abstained": False,
            "cmd_shadow_gold_gain": 0.6,
            "best_of_n_selected_index": 0,
            "best_of_n_abstained": False,
            "best_of_n_shadow_gold_gain": 0.2,
            "budget_aligned": True,
            "status": "ok",
        },
        {
            "arena_id": "fixture",
            "failure_type": "retrieval_error",
            "runtime_branch": "fix",
            "candidate_budget": 2,
            "cmd_selected_skill_id": "a",
            "cmd_abstained": False,
            "cmd_shadow_gold_gain": 0.1,
            "best_of_n_selected_index": 0,
            "best_of_n_abstained": False,
            "best_of_n_shadow_gold_gain": 0.3,
            "budget_aligned": True,
            "status": "ok",
        },
        {
            "arena_id": "fixture",
            "failure_type": "retrieval_error",
            "runtime_branch": "fill",
            "candidate_budget": 2,
            "cmd_selected_skill_id": None,
            "cmd_abstained": True,
            "cmd_shadow_gold_gain": 0.0,
            "best_of_n_selected_index": 0,
            "best_of_n_abstained": False,
            "best_of_n_shadow_gold_gain": 1.0,
            "budget_aligned": True,
            "status": "ok",
        },
    ]

    summary = analyze_arena_results._arm_comparison_summary(rows)[0]

    assert summary["n_total"] == 2
    assert summary["n_paired"] == 2
    assert summary["budget_aligned_count"] == 2
    assert summary["cmd_wins"] == 1
    assert summary["best_of_n_wins"] == 1
    assert summary["mean_structural_delta"] == pytest.approx(0.1)


def test_comparison_summary_drops_failures_misalignment_and_abstentions() -> None:
    base = {
        "arena_id": "fixture",
        "failure_type": "retrieval_error",
        "runtime_branch": "fix",
        "candidate_budget": 3,
        "cmd_selected_skill_id": "a",
        "cmd_abstained": False,
        "cmd_shadow_gold_gain": 0.5,
        "best_of_n_selected_index": 0,
        "best_of_n_abstained": False,
        "best_of_n_shadow_gold_gain": 0.4,
        "budget_aligned": True,
        "status": "ok",
    }
    rows = [
        base,
        {
            **base,
            "best_of_n_shadow_gold_gain": None,
            "status": "selection_score_unavailable",
        },
        {**base, "budget_aligned": False},
        {
            **base,
            "cmd_selected_skill_id": None,
            "cmd_abstained": True,
            "cmd_shadow_gold_gain": None,
        },
    ]

    summary = analyze_arena_results._arm_comparison_summary(rows)[0]

    assert summary["n_total"] == 4
    assert summary["n_paired"] == 1
    assert summary["n_dropped_control_fail"] == 1
    assert summary["n_dropped_budget_mismatch"] == 1
    assert summary["n_cmd_abstain"] == 1


def test_comparison_is_stratified_by_candidate_budget() -> None:
    rows = [
        {
            "arena_id": "fixture",
            "failure_type": "retrieval_error",
            "runtime_branch": "fix",
            "candidate_budget": budget,
            "cmd_selected_skill_id": "a",
            "cmd_abstained": False,
            "cmd_shadow_gold_gain": 0.5,
            "best_of_n_selected_index": 0,
            "best_of_n_abstained": False,
            "best_of_n_shadow_gold_gain": 0.4,
            "budget_aligned": True,
            "status": "ok",
        }
        for budget in (1, 3, 3)
    ]

    strata = analyze_arena_results._arm_comparison_by_budget(rows)

    assert [(row["candidate_budget"], row["n_total"]) for row in strata] == [
        (1, 1),
        (3, 2),
    ]
    assert strata[0]["selection_is_nontrivial"] is False
    assert strata[1]["selection_is_nontrivial"] is True
