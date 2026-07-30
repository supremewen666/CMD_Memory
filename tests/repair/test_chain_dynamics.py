from __future__ import annotations

import pytest

from cmd_audit.counterfactual.actions import PipelineAction
from cmd_audit.counterfactual.operators import OperatorSpec
from cmd_audit.repair.chain_dynamics import ChainObserver
from cmd_audit.repair.operator_library import merge_operators
from cmd_audit.repair.skill_ecology import ChainExecution, SkillCandidate


def _candidate(skill_id, action):
    return SkillCandidate(skill_id, OperatorSpec.single(0, action))


def _chain(first, second, benefit):
    return ChainExecution(
        first_skill_id=first,
        second_skill_id=second,
        chained_context="ctx",
        chained_gain=0.4 + benefit,
        standalone_max=0.4,
        chain_benefit=benefit,
        beneficial=benefit > 0.05,
        execution_cost=2.0,
        status="ok",
    )


def test_chain_observer_tracks_network_spectrum_direction_and_deposition():
    observer = ChainObserver(arena_id="memtrace")
    for position in range(1, 4):
        observer.record_case(
            case_id=f"c{position}",
            failure_type="retrieval_error",
            stream_position=position,
            activated_skill_ids=("a", "b"),
            chain_executions=(
                _chain("a", "b", 0.1),
                _chain("b", "a", 0.02),
            ),
        )
    snapshot = observer.snapshot("25%")
    assert snapshot.edges[0].coactivation_count == 3
    assert snapshot.edges[0].coactivation_rate == 1.0
    spectrum = observer.benefit_spectrum()
    assert spectrum.meaningful_positive == 3
    assert spectrum.weak_positive == 3
    direction = observer.directionality()[0]
    assert direction.direction_delta == pytest.approx(0.08)

    candidates = {
        "a": _candidate("a", PipelineAction.RETRIEVAL_ERROR),
        "b": _candidate("b", PipelineAction.INJECTION_ERROR),
    }
    deposition = observer.deposit_best(
        candidates=candidates,
        deposited_after_case=3,
    )
    assert deposition is not None
    assert deposition.first_skill_id == "a"
    assert deposition.composite_spec.format().count(" -> ") == 1


def test_merge_operators_preserves_same_generation_point_as_separate_stages():
    first = OperatorSpec.single(0, PipelineAction.RETRIEVAL_ERROR)
    second = OperatorSpec.single(0, PipelineAction.INJECTION_ERROR)
    composite = merge_operators((first, second))
    assert composite.stages == (first, second)
    assert composite.content_hash() == merge_operators((first, second)).content_hash()
    assert first.action_by_generation_point()[0] == PipelineAction.RETRIEVAL_ERROR

