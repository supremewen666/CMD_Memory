from __future__ import annotations

import pytest

from cmd_audit.eval.gold_free_observer import (
    GoldFreeObserver,
    ProbeCoordinates,
    spearman_score_correlation,
)


def test_observer_records_rank_fidelity_null_behavior_and_probe_slices():
    observer = GoldFreeObserver(arena_id="memtrace")
    first = observer.record(
        case_id="c1",
        family_id="f1",
        failure_type="retrieval_error",
        gold_free_scores={"a": 0.4, "b": 0.2, "c": 0.1},
        shadow_gold_scores={"a": 0.8, "b": 0.3, "c": 0.0},
        runtime_abstained=False,
        coordinates=ProbeCoordinates(4, "current", "present"),
    )
    null = observer.record(
        case_id="c2",
        family_id="f2",
        failure_type=None,
        gold_free_scores={"a": 0.3, "b": 0.1, "c": 0.0},
        shadow_gold_scores={"a": 0.0, "b": 0.0, "c": 0.0},
        runtime_abstained=False,
        coordinates=ProbeCoordinates(8, "historical", "present"),
    )
    assert first.spearman_rho == pytest.approx(1.0)
    assert first.oracle_rank_of_selected == 1.0
    assert first.shadow_regret == pytest.approx(0.0)
    assert null.failure_type == "null"
    assert null.null_false_positive
    by_failure = {row.slice_key: row for row in observer.summarize()}
    assert by_failure["retrieval_error"].top1_agreement_rate == 1.0
    assert by_failure["null"].null_false_positive_rate == 1.0
    by_age = {row.slice_key: row for row in observer.summarize(slice_by="age_sessions")}
    assert set(by_age) == {"4", "8"}


def test_spearman_uses_average_tie_ranks_and_rejects_mismatched_candidates():
    assert spearman_score_correlation(
        {"a": 1.0, "b": 1.0, "c": 0.0},
        {"a": 2.0, "b": 2.0, "c": 0.0},
    ) == pytest.approx(1.0)
    with pytest.raises(ValueError, match="candidate sets differ"):
        spearman_score_correlation({"a": 1.0}, {"b": 1.0})


def test_runtime_abstention_is_not_rewritten_by_shadow_oracle():
    observer = GoldFreeObserver(arena_id="memtrace")
    row = observer.record(
        case_id="c",
        family_id="f",
        failure_type="null",
        gold_free_scores={"a": 0.01, "b": 0.0},
        shadow_gold_scores={"a": 0.5, "b": 0.0},
        runtime_abstained=True,
    )
    assert row.selected_skill_id is None
    assert row.oracle_skill_id == "a"
    assert row.top1_agreement is None
    assert not row.null_false_positive

