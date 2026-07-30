from __future__ import annotations

from cmd_audit.counterfactual.actions import PipelineAction
from cmd_audit.counterfactual.operators import OperatorSpec
from cmd_audit.repair.skill_ecology import SkillCandidate, SkillExecution
from experiments.ecology_runner_common import (
    EcologyCase,
    SkillEcologyExperimentRunner,
)


def test_runner_updates_only_evolution_arms_after_all_case_outcomes():
    candidates = (
        SkillCandidate(
            "a",
            OperatorSpec.single(0, PipelineAction.RETRIEVAL_ERROR),
        ),
        SkillCandidate(
            "b",
            OperatorSpec.single(0, PipelineAction.INJECTION_ERROR),
        ),
    )
    state = {
        "no_update": 0,
        "fixed_library": 0,
        "competitive_topk": 0,
        "lamarckian": 0,
        "darwinian_global": 0,
    }
    provider_observations = []

    def provider(arm_id, case):
        provider_observations.append((case.case_id, arm_id, dict(state)))
        return candidates

    def evaluator(candidate, context):
        gain = 0.4 if candidate.skill_id == "b" else 0.2
        return SkillExecution(
            skill_id=candidate.skill_id,
            operator=candidate.operator,
            repaired_context=f"{context}->{candidate.skill_id}",
            recovery_gain=gain,
            execution_cost=1.0,
            success=True,
        )

    def update(arm_id, _case, _outcome):
        state[arm_id] += 1

    runner = SkillEcologyExperimentRunner(
        (EcologyCase("case-1", "retrieval_error", "base"),),
        candidate_provider=provider,
        evaluator=evaluator,
        post_case_updater=update,
        state_fingerprint=lambda arm_id: str(state[arm_id]),
        arms=tuple(state),
        top_k=2,
        seed=9,
    )
    result = runner.run()
    assert dict(result.leakage_assertions) == {
        "all_arm_outcomes_before_updates": True,
        "frozen_arms_unchanged": True,
    }
    assert state == {
        "no_update": 0,
        "fixed_library": 0,
        "competitive_topk": 0,
        "lamarckian": 1,
        "darwinian_global": 1,
    }
    # Every provider call for the case happened before either mutable update.
    assert all(
        snapshot["lamarckian"] == snapshot["darwinian_global"] == 0
        for _case_id, _arm_id, snapshot in provider_observations
    )
    by_arm = {row.arm_id: row for row in result.outcomes}
    assert by_arm["competitive_topk"].selected_skill_id == "b"
    assert by_arm["no_update"].selected_skill_id == "a"
    assert not by_arm["no_update"].updated_after_case
    assert by_arm["lamarckian"].updated_after_case


def test_random_skill_arm_is_seeded_by_case():
    candidates = tuple(
        SkillCandidate(
            action.value,
            OperatorSpec.single(0, action),
        )
        for action in (
            PipelineAction.RETRIEVAL_ERROR,
            PipelineAction.INJECTION_ERROR,
            PipelineAction.GRANULARITY_ERROR,
        )
    )

    def evaluator(candidate, context):
        return SkillExecution(
            skill_id=candidate.skill_id,
            operator=candidate.operator,
            repaired_context=context,
            recovery_gain=0.2,
            execution_cost=1.0,
            success=True,
        )

    def run():
        return SkillEcologyExperimentRunner(
            (EcologyCase("case-7", "x", "base"),),
            candidate_provider=lambda _arm, _case: candidates,
            evaluator=evaluator,
            arms=("random_skill",),
            seed=123,
        ).run()

    assert run().outcomes == run().outcomes

