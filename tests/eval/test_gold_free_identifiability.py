from __future__ import annotations

import math

import pytest

from cmd_audit.eval.gold_free_identifiability import (
    CandidateScore,
    CaseRankingInput,
    RuntimeSelectionProvenance,
    analyze_gold_free_agreement,
    case_ranking_from_mappings,
    rank_gold_free,
)


def test_gold_free_agreement_margin_abstention_and_hard_cases():
    cases = (
        case_ranking_from_mappings(
            case_id="agree",
            failure_type="retrieval_error",
            gold_free_scores={"a": 0.4, "b": 0.1},
            shadow_gold_scores={"a": 0.8, "b": 0.2},
        ),
        case_ranking_from_mappings(
            case_id="disagree",
            failure_type="retrieval_error",
            gold_free_scores={"a": 0.2, "b": 0.19},
            shadow_gold_scores={"a": 0.1, "b": 0.5},
        ),
        case_ranking_from_mappings(
            case_id="tie",
            failure_type="item_stale",
            gold_free_scores={"a": 0.3, "b": 0.3},
            shadow_gold_scores={"a": 0.4, "b": 0.4},
        ),
        case_ranking_from_mappings(
            case_id="missing",
            failure_type="item_stale",
            gold_free_scores={"a": math.nan, "b": None},
            shadow_gold_scores={"a": 0.1, "b": 0.2},
        ),
    )
    rows, report = analyze_gold_free_agreement(
        cases,
        abstention_thresholds=(0.0, 0.05, 0.2),
    )

    by_id = {row.case_id: row for row in rows}
    assert by_id["agree"].agreement is True
    assert by_id["agree"].gold_free_margin == pytest.approx(0.3)
    assert by_id["disagree"].agreement is False
    assert by_id["disagree"].supervised_regret == pytest.approx(0.4)
    assert by_id["tie"].gold_free_tied_skills == ("a", "b")
    assert by_id["missing"].agreement is None
    assert "insufficient_finite_scores" in by_id["missing"].hard_reason

    assert report.total_cases == 4
    assert report.eligible_cases == 3
    assert report.agreements == 2  # deterministic lexical top among the tie
    assert report.overall_agreement == pytest.approx(2 / 3)
    coverages = [point.coverage for point in report.abstention_curve]
    assert coverages == sorted(coverages, reverse=True)
    assert report.abstention_curve[-1].selective_agreement == 1.0
    assert {row.failure_type for row in report.by_failure_type} == {
        "retrieval_error",
        "item_stale",
    }


def test_nonfinite_candidate_is_reported_without_poisoning_finite_ranking():
    case = case_ranking_from_mappings(
        case_id="partial",
        failure_type="granularity_error",
        gold_free_scores={"a": 0.2, "b": math.nan, "c": 0.1},
        shadow_gold_scores={"a": 0.3, "b": 0.9, "c": 0.2},
    )
    rows, report = analyze_gold_free_agreement((case,))
    assert rows[0].gold_free_top_skill == "a"
    assert rows[0].gold_supervised_top_skill == "b"
    assert rows[0].missing_or_nonfinite_skill_ids == ("b",)
    assert report.eligible_cases == 1


@pytest.mark.parametrize(
    "provenance, message",
    (
        (
            RuntimeSelectionProvenance(
                context_constructed_without_gold=False,
            ),
            "context construction used gold",
        ),
        (
            RuntimeSelectionProvenance(selection_used_gold=True),
            "selection used gold",
        ),
        (
            RuntimeSelectionProvenance(shadow_scores_isolated=False),
            "not isolated",
        ),
    ),
)
def test_gold_free_provenance_fails_closed(provenance, message):
    case = case_ranking_from_mappings(
        case_id="leak",
        failure_type="retrieval_error",
        gold_free_scores={"a": 0.2},
        shadow_gold_scores={"a": 0.3},
        runtime_provenance=provenance,
    )
    with pytest.raises(ValueError, match=message):
        analyze_gold_free_agreement((case,))


def test_duplicate_skill_ids_fail_closed():
    with pytest.raises(ValueError, match="duplicate skill_id"):
        rank_gold_free(
            (
                CandidateScore("a", 0.1),
                CandidateScore("a", 0.2),
            )
        )


def test_analysis_requires_identical_runtime_and_shadow_candidate_sets():
    malformed = CaseRankingInput(
        case_id="mismatch",
        failure_type="retrieval_error",
        gold_free_scores=(CandidateScore("a", 0.2),),
        shadow_gold_scores=(CandidateScore("b", 0.3),),
    )
    with pytest.raises(ValueError, match="candidate sets differ"):
        analyze_gold_free_agreement((malformed,))
