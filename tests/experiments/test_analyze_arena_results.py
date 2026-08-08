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
    # Paired tests are now computed, so claiming none ran would be false. The
    # design is still observational, which is what keeps the arena out of the
    # confirmatory evidence tier — recorded as a separate, explicit field.
    assert manifest["hypothesis_tests_run"] is True
    assert manifest["hypothesis_test_role"] == "descriptive_not_confirmatory"
    assert manifest["analysis_kind"] == "descriptive_observational"
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


def _paired_row(cmd_gain: float, control_gain: float, **overrides) -> dict:
    row = {
        "arena_id": "fixture",
        "failure_type": "retrieval_error",
        "runtime_branch": "fix",
        "candidate_budget": 2,
        "cmd_selected_skill_id": "a",
        "cmd_abstained": False,
        "cmd_shadow_gold_gain": cmd_gain,
        "best_of_n_selected_index": 0,
        "best_of_n_abstained": False,
        "best_of_n_shadow_gold_gain": control_gain,
        "budget_aligned": True,
        "status": "ok",
    }
    row.update(overrides)
    return row


def test_significance_table_reports_paired_tests_over_the_same_pairs() -> None:
    """The significance row must test exactly the pairs the summary counted.

    Hand-calculated: 5 CMD wins, 1 control win, 0 ties. The sign test over
    6 discordant pairs is 2 * P(X <= 1), X ~ Binomial(6, 0.5) = 14/64.
    """
    rows = [_paired_row(0.6, 0.2) for _ in range(5)]
    rows.append(_paired_row(0.1, 0.9))

    summary = analyze_arena_results._arm_comparison_summary(rows)[0]
    significance = analyze_arena_results._arm_significance_summary(rows)[0]

    assert significance["n_paired"] == summary["n_paired"] == 6
    assert significance["cmd_wins"] == 5
    assert significance["control_wins"] == 1
    assert float(significance["sign_test_p"]) == pytest.approx(14 / 64)
    assert significance["ci_excludes_zero"] is False


def test_significance_excludes_the_same_rows_the_summary_excludes() -> None:
    """An unpaired row cannot enter a paired test."""
    rows = [
        _paired_row(0.6, 0.2),
        _paired_row(0.6, None, status="selection_score_unavailable"),
        _paired_row(0.6, 0.2, budget_aligned=False),
        _paired_row(None, 0.2, cmd_abstained=True, cmd_selected_skill_id=None),
    ]

    summary = analyze_arena_results._arm_comparison_summary(rows)[0]
    significance = analyze_arena_results._arm_significance_summary(rows)[0]

    assert significance["n_paired"] == summary["n_paired"] == 1


def test_significance_blocks_the_bootstrap_by_family_when_available() -> None:
    """Siblings share a fault, so the interval must resample whole families.

    Two families disagree in direction with four siblings each. A case-level
    bootstrap concentrates near the average; blocking by family must reach the
    per-family means instead.
    """
    rows = [
        _paired_row(1.0, 0.0, case_id=f"f1-{index}") for index in range(4)
    ] + [_paired_row(0.0, 1.0, case_id=f"f2-{index}") for index in range(4)]
    families = {f"f1-{index}": "f1" for index in range(4)}
    families.update({f"f2-{index}": "f2" for index in range(4)})

    blocked = analyze_arena_results._arm_significance_summary(
        rows, families=families
    )[0]

    assert blocked["bootstrap_unit"] == "family"
    assert blocked["n_families"] == 2
    assert float(blocked["diff_ci_low"]) == pytest.approx(-1.0)
    assert float(blocked["diff_ci_high"]) == pytest.approx(1.0)


def test_significance_falls_back_to_case_level_without_family_ids() -> None:
    rows = [_paired_row(0.6, 0.2) for _ in range(4)]

    significance = analyze_arena_results._arm_significance_summary(rows)[0]

    assert significance["bootstrap_unit"] == "case"
    assert significance["n_families"] is None


def _observation(**overrides) -> dict:
    row = {
        "arena_id": "fixture",
        "failure_type": "safety_error",
        "case_id": "case-1",
        "family_id": "family-1",
        "selected_skill_id": "seed:safety_error",
        "oracle_skill_id": "seed:safety_error",
        "gold_free_margin": 0.5,
        "shadow_gold_margin": 0.5,
        "top1_agreement": True,
        "runtime_abstained": False,
        "null_false_positive": False,
        "shadow_regret": 0.0,
    }
    row.update(overrides)
    return row


def test_self_assessment_calibration_separates_confidence_from_agreement():
    """The reported 1.0-versus-0.27 needs both rates side by side.

    Hand-built: 4 cases, all confident under the gold-free signal, only 1 of
    which the shadow judge agrees with. So self-confidence is 4/4 = 1.0 and
    shadow agreement is 1/4 = 0.25, and the gap is what the table has to show.
    """
    rows = [
        _observation(shadow_gold_margin=0.0, top1_agreement=False)
        for _ in range(3)
    ] + [_observation()]

    table = analyze_arena_results._self_assessment_calibration(rows)[0]

    assert table["failure_type"] == "safety_error"
    assert table["n"] == 4
    assert float(table["self_confident_rate"]) == pytest.approx(1.0)
    assert float(table["shadow_agreement_rate"]) == pytest.approx(0.25)
    assert float(table["calibration_gap"]) == pytest.approx(0.75)


def test_self_assessment_calibration_names_what_the_oracle_preferred_instead():
    """A bare gap is unexplained; the table has to say what won instead.

    Otherwise the row reads as "the safety self-score is miscalibrated" when the
    measured behavior is that a different operator scored better on those cases.
    """
    rows = [
        _observation(
            oracle_skill_id="composite:composite-spec-1",
            shadow_gold_margin=0.0,
            top1_agreement=False,
        )
        for _ in range(3)
    ] + [_observation()]

    table = analyze_arena_results._self_assessment_calibration(rows)[0]

    assert table["top_alternative_skill_id"] == "composite:composite-spec-1"
    assert float(table["top_alternative_share"]) == pytest.approx(0.75)
    # A confident-and-wrong case is not the same as a near-tie misread as
    # confidence, so the table reports how many margins were negligible.
    assert float(table["tiny_margin_share"]) == pytest.approx(0.0)


def test_inapplicable_operators_are_separated_from_losing_ones():
    """An operator whose precondition never holds is not a weak candidate.

    Hand-built: on this layer skill-b returns exactly zero on both cases -- its
    precondition never fires -- while skill-a moves the score on both. So skill-b
    is inapplicable at rate 1.0 and skill-a at 0.0, and only skill-a is a real
    competitor for the argmax.
    """
    rows = [
        _observation(
            failure_type="granularity_error",
            gold_free_scores=[["skill-a", 0.02], ["skill-b", 0.0]],
        ),
        _observation(
            failure_type="granularity_error",
            gold_free_scores=[["skill-a", -0.03], ["skill-b", 0.0]],
        ),
    ]

    table = {
        row["skill_id"]: row
        for row in analyze_arena_results._operator_applicability(rows)
    }

    assert float(table["skill-b"]["inapplicable_rate"]) == pytest.approx(1.0)
    assert table["skill-b"]["never_applicable"] is True
    assert float(table["skill-a"]["inapplicable_rate"]) == pytest.approx(0.0)
    assert table["skill-a"]["never_applicable"] is False


def test_applicability_counts_the_candidates_actually_in_contention():
    """The ranked field is smaller than the candidate list, and by how much matters.

    Both cases offer 3 candidates but only 2 can move the score, so the layer's
    effective field is 2 -- which is what decides whether an argmax over the
    other 1 is a choice or a coin flip.
    """
    rows = [
        _observation(
            failure_type="granularity_error",
            gold_free_scores=[
                ["skill-a", 0.02],
                ["skill-b", 0.0],
                ["skill-c", -0.01],
            ],
        ),
        _observation(
            failure_type="granularity_error",
            gold_free_scores=[
                ["skill-a", 0.05],
                ["skill-b", 0.0],
                ["skill-c", -0.04],
            ],
        ),
    ]

    summary = analyze_arena_results._layer_effective_field(rows)[0]

    assert summary["failure_type"] == "granularity_error"
    assert int(summary["candidates_offered"]) == 3
    assert int(summary["never_applicable_operators"]) == 1
    assert int(summary["effective_field"]) == 2


def test_abstention_curve_trades_coverage_for_selective_agreement():
    """Raising the margin threshold should drop cases and lift agreement.

    Hand-built so the answer is countable rather than recomputed: 4 acted cases
    at margins 0.5/0.4/0.001/0.0005, and only the two high-margin ones agree
    with the shadow oracle. At tau=0 all 4 are retained and agreement is
    2/4 = 0.5; at tau=0.01 only the two high-margin cases survive, so coverage
    is 2/4 = 0.5 and selective agreement is 2/2 = 1.0.
    """
    rows = [
        _curve_observation(margin=0.5, agrees=True),
        _curve_observation(margin=0.4, agrees=True),
        _curve_observation(margin=0.001, agrees=False),
        _curve_observation(margin=0.0005, agrees=False),
    ]

    table = {
        float(row["threshold"]): row
        for row in analyze_arena_results._abstention_curve_by_failure(rows)
        if row["failure_type"] == "granularity_error"
    }

    assert float(table[0.0]["coverage"]) == pytest.approx(1.0)
    assert float(table[0.0]["selective_agreement"]) == pytest.approx(0.5)
    assert float(table[0.01]["coverage"]) == pytest.approx(0.5)
    assert float(table[0.01]["selective_agreement"]) == pytest.approx(1.0)


def test_abstention_curve_margin_ignores_inapplicable_candidates():
    """A no-op candidate must not set the margin the threshold is compared to.

    Two applicable operators sit 0.02 apart, and a third is a no-op at exactly
    zero. Ranking all three puts the runner-up at 0.0 and reports a margin of
    0.05, which clears tau=0.01; ranking only the two that can act reports the
    real 0.02 gap. Both clear this tau, so the assertion is on the margin the
    curve used, not on which side of the threshold it fell.
    """
    rows = [
        _observation(
            failure_type="granularity_error",
            gold_free_scores=[
                ["skill-a", 0.05],
                ["skill-b", 0.03],
                ["skill-noop", 0.0],
            ],
            shadow_gold_scores=[
                ["skill-a", 1.0],
                ["skill-b", 1.0],
                ["skill-noop", 1.0],
            ],
        ),
    ]

    row = next(
        row
        for row in analyze_arena_results._abstention_curve_by_failure(rows)
        if float(row["threshold"]) == 0.0
    )

    assert float(row["mean_applicable_margin"]) == pytest.approx(0.02)
    assert int(row["mean_effective_field"]) == 2


def test_abstention_curve_only_scores_cases_the_runtime_acted_on():
    """A case the runtime already skipped cannot be credited to the threshold.

    Two cases share a margin below every tested threshold, but one was already
    abstained at runtime. Only the acted case is a unit of the curve, so the
    eligible count is 1, not 2.
    """
    rows = [
        _curve_observation(margin=0.0005, agrees=False),
        _curve_observation(margin=0.0005, agrees=False, runtime_abstained=True),
    ]

    row = next(
        row
        for row in analyze_arena_results._abstention_curve_by_failure(rows)
        if float(row["threshold"]) == 0.0
    )

    assert int(row["eligible_cases"]) == 1


def test_abstention_curve_reports_the_regret_the_threshold_avoids():
    """Coverage loss is only worth it if the dropped cases were the costly ones.

    The two sub-threshold cases carry regret 0.4 and 0.2; the surviving
    high-margin case carries none. So mean retained regret falls from
    0.2 at tau=0 to 0.0 at tau=0.01.
    """
    rows = [
        _curve_observation(margin=0.5, agrees=True, regret=0.0),
        _curve_observation(margin=0.001, agrees=False, regret=0.4),
        _curve_observation(margin=0.0005, agrees=False, regret=0.2),
    ]

    table = {
        float(row["threshold"]): row
        for row in analyze_arena_results._abstention_curve_by_failure(rows)
        if row["failure_type"] == "granularity_error"
    }

    assert float(table[0.0]["mean_retained_regret"]) == pytest.approx(0.2)
    assert float(table[0.01]["mean_retained_regret"]) == pytest.approx(0.0)


#: Stand-in regret for a disagreeing fixture case whose cost is not the point
#: of the assertion. Any positive value works; it only has to be non-zero.
_FIXTURE_REGRET = 0.1

#: Baseline gain for the runner-up candidate, so it counts as applicable. Small
#: enough that the requested margin still lands in the intended threshold band.
_FIXTURE_RUNNER_UP_GAIN = 1e-6


def _curve_observation(
    *,
    margin: float,
    agrees: bool,
    regret: float | None = None,
    runtime_abstained: bool = False,
) -> dict:
    """A gold-free observation carrying the two candidate score vectors.

    The curve reads candidate scores rather than the precomputed margin, so the
    fixture places the requested margin between the top two gold-free gains and
    puts the shadow oracle's preference on whichever skill it should agree with.

    Agreement and regret are the same quantity seen twice -- regret is the
    shadow gain the chosen skill gave up -- so agreeing implies zero regret and
    disagreeing implies positive regret. The contradictory combinations are
    rejected rather than silently resolved, because a disagreement with zero
    regret would tie on the shadow axis and the tie-break would hand it back to
    the chosen skill, turning it into an agreement.
    """
    # Both candidates must be applicable, so the runner-up carries a small
    # non-zero gain rather than exactly zero: a zero-gain operator is a no-op
    # whose precondition never fired, and the curve excludes it from the margin.
    runner_up = _FIXTURE_RUNNER_UP_GAIN
    if agrees:
        if regret not in (None, 0.0):
            raise ValueError("an agreeing case cannot carry positive regret")
        return _observation(
            failure_type="granularity_error",
            runtime_abstained=runtime_abstained,
            gold_free_scores=[
                ["skill-a", runner_up + margin],
                ["skill-b", runner_up],
            ],
            shadow_gold_scores=[["skill-a", 1.0], ["skill-b", 1.0]],
        )
    resolved = _FIXTURE_REGRET if regret is None else regret
    if resolved <= 0.0:
        raise ValueError("a disagreeing case needs positive regret")
    return _observation(
        failure_type="granularity_error",
        runtime_abstained=runtime_abstained,
        gold_free_scores=[
            ["skill-a", runner_up + margin],
            ["skill-b", runner_up],
        ],
        shadow_gold_scores=[["skill-a", 1.0 - resolved], ["skill-b", 1.0]],
    )


def test_null_protection_reports_abstention_against_false_positives():
    """On a no-fault case, abstaining is the correct action and repairing is not.

    Hand-built: 4 null cases -- 2 abstained, 1 repaired and flagged a false
    positive, 1 repaired without one. So abstention is 2/4 = 0.5 and the null
    false-positive rate is 1/4 = 0.25.
    """
    rows = [
        _observation(failure_type="null", runtime_abstained=True)
        for _ in range(2)
    ] + [
        _observation(
            failure_type="null",
            runtime_abstained=False,
            null_false_positive=True,
        ),
        _observation(failure_type="null", runtime_abstained=False),
    ]

    table = analyze_arena_results._null_protection_calibration(rows)[0]

    assert table["failure_type"] == "null"
    assert table["n"] == 4
    assert float(table["abstention_rate"]) == pytest.approx(0.5)
    assert float(table["null_false_positive_rate"]) == pytest.approx(0.25)


def test_null_protection_separates_harmful_from_harmless_intervention():
    """Acting on a no-fault case is only a real cost if it lost ground.

    Two cases repair a null case; one has positive shadow regret, one does not.
    A false-positive count alone cannot distinguish those, so the table reports
    the regret-bearing share separately.
    """
    rows = [
        _observation(
            failure_type="null",
            runtime_abstained=False,
            null_false_positive=True,
            shadow_regret=0.4,
        ),
        _observation(
            failure_type="null",
            runtime_abstained=False,
            null_false_positive=True,
            shadow_regret=0.0,
        ),
    ]

    table = analyze_arena_results._null_protection_calibration(rows)[0]

    assert float(table["harmful_intervention_rate"]) == pytest.approx(0.5)
    assert float(table["mean_shadow_regret"]) == pytest.approx(0.2)


def test_abstention_calibration_covers_cases_that_had_a_fault_to_fix():
    """Abstaining on a real fault is a miss, and must not be scored as caution.

    Two retrieval_error cases: one abstained (a miss), one acted. The abstention
    rate on faulted cases is therefore 0.5, reported apart from the null rows so
    caution and missed repairs are never averaged together.
    """
    rows = [
        _observation(failure_type="retrieval_error", runtime_abstained=True),
        _observation(failure_type="retrieval_error", runtime_abstained=False),
        _observation(failure_type="null", runtime_abstained=True),
    ]

    table = {
        row["failure_type"]: row
        for row in analyze_arena_results._null_protection_calibration(rows)
    }

    assert float(table["retrieval_error"]["abstention_rate"]) == pytest.approx(0.5)
    assert table["retrieval_error"]["case_kind"] == "faulted"
    assert table["null"]["case_kind"] == "no_fault"


def test_context_stuffing_arm_is_tested_against_cmd_on_the_same_cases() -> None:
    """The named baseline needs the same paired treatment as best-of-N.

    Hand-calculated: 5 cases where CMD beats stuffing, 1 where it loses, so the
    sign test is 2 * P(X <= 1), X ~ Binomial(6, 0.5) = 14/64 = 0.21875.
    """
    rows = [
        _paired_row(0.6, 0.2, context_stuffing_shadow_gold_gain=0.1)
        for _ in range(5)
    ]
    rows.append(_paired_row(0.1, 0.9, context_stuffing_shadow_gold_gain=0.8))

    stuffing = analyze_arena_results._arm_significance_summary(
        rows, control_field="context_stuffing_shadow_gold_gain"
    )[0]

    assert stuffing["control_arm"] == "context_stuffing"
    assert stuffing["n_paired"] == 6
    assert stuffing["cmd_wins"] == 5
    assert stuffing["control_wins"] == 1
    assert float(stuffing["sign_test_p"]) == pytest.approx(0.21875)


def test_context_stuffing_significance_skips_cases_the_arm_did_not_run() -> None:
    """A row without a stuffing outcome is not a pair, and must not be counted.

    The arm is off by default, so most existing artifacts carry ``None`` here.
    Treating those as zero-gain would manufacture wins out of missing data.
    """
    rows = [
        _paired_row(0.6, 0.2, context_stuffing_shadow_gold_gain=0.1),
        _paired_row(0.6, 0.2, context_stuffing_shadow_gold_gain=None),
    ]

    stuffing = analyze_arena_results._arm_significance_summary(
        rows, control_field="context_stuffing_shadow_gold_gain"
    )[0]

    assert stuffing["n_paired"] == 1
