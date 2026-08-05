from __future__ import annotations

import pytest

from cmd_audit.counterfactual.actions import PipelineAction
from cmd_audit.counterfactual.operators import OperatorSpec
from cmd_audit.repair.niche_archive import BehaviorDescriptor, NicheArchive
from cmd_audit.repair.skill_ecology import SkillCandidate
from experiments.niche_evolution_runner import (
    AuditedNicheEvolutionRunner,
    NicheDualExecution,
    NicheEvolutionCase,
)


def _cases() -> tuple[NicheEvolutionCase, ...]:
    descriptor = BehaviorDescriptor(
        "cluster-a",
        ("recall_set_collision:high",),
        "tier2_item",
    )
    return tuple(
        NicheEvolutionCase(
            case_id=f"c{index}",
            family_id=f"f{index % 2}",
            case_index=index,
            base_context=f"base-{index}",
            descriptor=descriptor,
        )
        for index in range(5)
    )


def test_runner_is_test_then_update_and_budget_aligned() -> None:
    seeds = (
        SkillCandidate(
            "seed:conflict",
            OperatorSpec.single(0, PipelineAction.ITEM_CONFLICT),
        ),
        SkillCandidate(
            "seed:stale",
            OperatorSpec.single(0, PipelineAction.ITEM_STALE),
        ),
    )

    def evaluate(case, candidate, context):
        del case
        gold_free = 0.8 if candidate.skill_id.endswith("stale") else 0.2
        shadow = 0.8 if candidate.skill_id.endswith("conflict") else 0.1
        return NicheDualExecution(
            candidate.skill_id,
            gold_free,
            shadow,
            1.0,
            context + "->" + candidate.skill_id,
        )

    result = AuditedNicheEvolutionRunner(
        _cases(),
        seed_candidate_provider=lambda _case: seeds,
        evaluator=evaluate,
        candidate_budget=2,
        archive_factory=lambda: NicheArchive(bootstrap_samples=100),
    ).run()

    assert all(row.budget_aligned for row in result.outcomes)
    assert all(value for _name, value in result.leakage_assertions)
    assert all(
        row.selected_skill_id == "seed:stale"
        for row in result.outcomes
        if row.case_index == 0
    )
    assert "all_frozen" not in result.archive_snapshots


def test_fill_is_exact_abstention_across_arms() -> None:
    case = NicheEvolutionCase(
        case_id="fill",
        family_id="f",
        case_index=0,
        base_context="base",
        descriptor=BehaviorDescriptor(
            "cluster",
            (),
            "fill",
        ),
        runtime_branch="fill",
    )
    result = AuditedNicheEvolutionRunner(
        (case,),
        seed_candidate_provider=lambda _case: (),
        evaluator=lambda *_args: (_ for _ in ()).throw(
            AssertionError("fill must not execute")
        ),
        candidate_budget=1,
        archive_factory=lambda: NicheArchive(bootstrap_samples=100),
    ).run()

    assert all(row.abstained for row in result.outcomes)
    assert all(row.candidate_count == 0 for row in result.outcomes)


def test_updating_arms_cannot_share_archive_state() -> None:
    shared = NicheArchive(bootstrap_samples=100)

    with pytest.raises(ValueError, match="independent archive"):
        AuditedNicheEvolutionRunner(
            _cases(),
            seed_candidate_provider=lambda _case: (),
            evaluator=lambda *_args: NicheDualExecution(
                "unused",
                0.0,
                0.0,
                0.0,
                "unused",
            ),
            candidate_budget=1,
            archive_factory=lambda: shared,
        )
