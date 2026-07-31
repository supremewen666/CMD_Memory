from __future__ import annotations

import math

import pytest

from cmd_audit.counterfactual.actions import PipelineAction
from cmd_audit.counterfactual.operators import OperatorSpec
from cmd_audit.repair.skill_ecology import (
    AdditiveSaturationExecutor,
    CompetitiveExecutor,
    EcologyObserver,
    EcologyTracker,
    PerturbationProbe,
    SkillCandidate,
    SkillExecution,
    detect_operator_conflicts,
    evaluate_skill_chain,
    jensen_shannon_divergence,
    select_competitive_winner,
)


def _candidate(skill_id: str, action: PipelineAction) -> SkillCandidate:
    return SkillCandidate(skill_id, OperatorSpec.single(0, action))


def _execution(candidate: SkillCandidate, gain: float | None) -> SkillExecution:
    return SkillExecution(
        skill_id=candidate.skill_id,
        operator=candidate.operator,
        repaired_context=f"repaired-{candidate.skill_id}",
        recovery_gain=gain,
        execution_cost=1.0,
        success=gain is not None and math.isfinite(gain) and gain >= 0.1,
    )


def test_competitive_executor_uses_same_snapshot_and_records_winner_losers():
    candidates = (
        _candidate("a", PipelineAction.RETRIEVAL_ERROR),
        _candidate("b", PipelineAction.INJECTION_ERROR),
        _candidate("c", PipelineAction.GRANULARITY_ERROR),
    )
    seen_contexts = []
    gains = {"a": 0.2, "b": 0.4, "c": 0.1}

    def evaluator(candidate, base_context):
        seen_contexts.append(base_context)
        return _execution(candidate, gains[candidate.skill_id])

    result = CompetitiveExecutor(top_k=3).execute(
        case_id="case-1",
        failure_type="retrieval_error",
        base_context="immutable-base",
        candidates=candidates,
        evaluator=evaluator,
    )
    assert seen_contexts == ["immutable-base"] * 3
    assert result.winner is not None
    assert result.winner.skill_id == "b"
    assert result.runner_up is not None
    assert result.runner_up.skill_id == "a"
    assert result.winner_margin == pytest.approx(0.2)
    assert {item.skill_id for item in result.losers} == {"a", "c"}


def test_additive_saturation_keeps_positive_contributors_until_threshold():
    candidates = (
        _candidate("a", PipelineAction.RETRIEVAL_ERROR),
        _candidate("b", PipelineAction.INJECTION_ERROR),
        _candidate("c", PipelineAction.GRANULARITY_ERROR),
        _candidate("d", PipelineAction.SAFETY_ERROR),
    )
    seen_contexts = []
    gains = {"a": 0.45, "b": 0.35, "c": 0.2, "d": -0.4}

    def evaluator(candidate, base_context):
        seen_contexts.append(base_context)
        return _execution(candidate, gains[candidate.skill_id])

    result = AdditiveSaturationExecutor(saturation_threshold=0.8).execute(
        case_id="case-1",
        failure_type="compound",
        base_context="immutable-base",
        candidates=candidates,
        evaluator=evaluator,
    )

    assert seen_contexts == ["immutable-base"] * 4
    assert tuple(row.skill_id for row in result.selected) == ("a", "b")
    assert tuple(row.skill_id for row in result.rejected) == ("c", "d")
    assert result.cumulative_gain == pytest.approx(0.8)
    assert result.covered
    assert result.repair_effective


def test_additive_saturation_never_selects_zero_negative_or_nonfinite_gains():
    candidates = (
        _candidate("a", PipelineAction.RETRIEVAL_ERROR),
        _candidate("b", PipelineAction.INJECTION_ERROR),
        _candidate("c", PipelineAction.GRANULARITY_ERROR),
        _candidate("d", PipelineAction.SAFETY_ERROR),
    )
    gains = {"a": 0.3, "b": 0.0, "c": -0.2, "d": math.nan}

    result = AdditiveSaturationExecutor(saturation_threshold=0.8).execute(
        case_id="case-2",
        failure_type="compound",
        base_context="base",
        candidates=candidates,
        evaluator=lambda candidate, _context: _execution(
            candidate, gains[candidate.skill_id]
        ),
    )

    assert tuple(row.skill_id for row in result.selected) == ("a",)
    assert result.cumulative_gain == pytest.approx(0.3)
    assert not result.covered
    assert result.repair_effective


def test_tie_nonfinite_and_all_failed_semantics_are_explicit():
    a = _candidate("a", PipelineAction.RETRIEVAL_ERROR)
    b = _candidate("b", PipelineAction.INJECTION_ERROR)
    tie = select_competitive_winner(
        case_id="tie",
        failure_type="x",
        executions=(_execution(a, 0.3), _execution(b, 0.3)),
    )
    assert tie.abstained
    assert tie.abstention_reason == "tie"
    assert tie.winner is None
    assert tie.tied_skill_ids == ("a", "b")

    no_finite = select_competitive_winner(
        case_id="nan",
        failure_type="x",
        executions=(_execution(a, math.nan), _execution(b, None)),
    )
    assert no_finite.all_failed
    assert no_finite.abstention_reason == "no_finite_gain"

    failed = select_competitive_winner(
        case_id="failed",
        failure_type="x",
        executions=(_execution(a, 0.09), _execution(b, 0.08)),
    )
    assert failed.all_failed
    assert failed.abstention_reason == "all_failed"


def test_ecology_metrics_overlap_specialization_and_stability():
    a = _candidate("a", PipelineAction.RETRIEVAL_ERROR)
    b = _candidate("b", PipelineAction.INJECTION_ERROR)
    tracker = EcologyTracker(overlap_threshold=0.7)
    for case_id, failure, gains in (
        ("r1", "retrieval", (0.4, 0.1)),
        ("r2", "retrieval", (0.3, 0.1)),
        ("i1", "injection", (0.1, 0.4)),
        ("i2", "injection", (0.1, 0.3)),
    ):
        result = select_competitive_winner(
            case_id=case_id,
            failure_type=failure,
            executions=(
                _execution(a, gains[0]),
                _execution(b, gains[1]),
            ),
        )
        tracker.record(result, checkpoint="L1")

    first = tracker.snapshot("L1")
    profiles = {item.skill_id: item for item in first.niches}
    assert profiles["a"].dominant_niche == "retrieval"
    assert profiles["b"].dominant_niche == "injection"
    assert profiles["a"].specialization_index == pytest.approx(1.0)
    assert first.overlaps[0].cosine_similarity == pytest.approx(0.0)
    assert first.diversity_index == pytest.approx(math.log(2))

    second = tracker.snapshot("L2")
    assert second.jsd_from_previous == pytest.approx(0.0)
    assert jensen_shannon_divergence(
        {"a": 1.0, "b": 0.0},
        {"a": 0.0, "b": 1.0},
    ) == pytest.approx(math.log(2))


def test_chain_executes_second_on_first_output_and_conflicts_are_typed():
    first = _candidate("a", PipelineAction.RETRIEVAL_ERROR)
    second = _candidate("b", PipelineAction.INJECTION_ERROR)
    seen = []

    def evaluator(candidate, context):
        seen.append((candidate.skill_id, context))
        gain = {
            ("a", "base"): 0.2,
            ("b", "base"): 0.1,
            ("b", "base->a"): 0.5,
        }[(candidate.skill_id, context)]
        return SkillExecution(
            skill_id=candidate.skill_id,
            operator=candidate.operator,
            repaired_context=f"{context}->{candidate.skill_id}",
            recovery_gain=gain,
            execution_cost=1.0,
            success=gain >= 0.1,
        )

    chain = evaluate_skill_chain(
        first=first,
        second=second,
        base_context="base",
        evaluator=evaluator,
    )
    assert seen == [
        ("a", "base"),
        ("b", "base"),
        ("b", "base->a"),
    ]
    assert chain.chain_benefit == pytest.approx(0.3)
    assert chain.beneficial

    conflicts = detect_operator_conflicts(first, second)
    assert len(conflicts) == 1
    assert conflicts[0].target == "generation_point:0"


def test_checkpoint_observer_and_perturbation_probe_are_append_only():
    a = _candidate("a", PipelineAction.RETRIEVAL_ERROR)
    b = _candidate("b", PipelineAction.INJECTION_ERROR)
    observer = EcologyObserver(arena_id="x", total_cases=4)
    for position in range(1, 5):
        result = select_competitive_winner(
            case_id=f"c{position}",
            failure_type="retrieval",
            executions=(_execution(a, 0.4), _execution(b, 0.2)),
        )
        observer.record(result, stream_position=position)
    assert [row.event_count for row in observer.snapshots] == [1, 2, 3, 4]
    assert observer.finalize() == observer.snapshots[-1]

    probe = PerturbationProbe(
        arena_id="x",
        removed_skill_id="a",
        removal_strategy="keystone",
        started_after_case=4,
        window_size=2,
        stability_threshold=0.0,
        stable_windows_required=1,
    )
    for position, winner in ((5, "b"), (6, "b"), (7, "b"), (8, "b")):
        probe.observe(stream_position=position, winner_skill_id=winner)
    result = probe.result()
    assert result.recovered_after_cases == 4
    with pytest.raises(ValueError, match="removed skill"):
        probe.observe(stream_position=9, winner_skill_id="a")

    collapsed = PerturbationProbe(
        arena_id="x",
        removed_skill_id="a",
        removal_strategy="keystone",
        started_after_case=4,
        window_size=2,
        stable_windows_required=1,
    )
    for position in range(5, 9):
        collapsed.observe(stream_position=position, winner_skill_id=None)
    assert collapsed.result().winnerless_windows == 2
    assert collapsed.result().recovered_after_cases is None


def test_ecology_observer_finalize_rejects_empty_tracker():
    observer = EcologyObserver(arena_id="empty", total_cases=1)
    with pytest.raises(ValueError, match="empty ecology"):
        observer.finalize()
