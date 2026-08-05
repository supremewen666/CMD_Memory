from __future__ import annotations

from cmd_audit.repair.skill_graph import (
    AuditedSkillGraph,
    CompositionEvidence,
    TransferEvidence,
)


def test_transfer_requires_target_niche_gain_and_creates_edge() -> None:
    graph = AuditedSkillGraph(bootstrap_samples=200)
    result = graph.audit_transfer(
        source_niche_id="a",
        target_niche_id="b",
        source_revision_id="ra",
        target_revision_id="rb",
        evidence=tuple(
            TransferEvidence(
                f"c{index}",
                f"f{index % 2}",
                source_gain=0.8,
                target_incumbent_gain=0.2,
                execution_cost=1.0,
            )
            for index in range(4)
        ),
    )

    assert result.decision == "activate_transfer"
    assert result.edge_id is not None
    assert len(graph.edges) == 1


def test_composition_requires_real_intermediate_execution() -> None:
    graph = AuditedSkillGraph(bootstrap_samples=200)
    result = graph.audit_composition(
        source_niche_id="a",
        target_niche_id="b",
        source_revision_id="ra",
        target_revision_id="rb",
        evidence=tuple(
            CompositionEvidence(
                f"c{index}",
                f"f{index % 2}",
                first_gain=0.2,
                second_gain=0.3,
                composed_gain=0.9,
                execution_cost=2.0,
                cost_budget=2.0,
                executed_intermediate=False,
            )
            for index in range(4)
        ),
    )

    assert result.decision == "intermediate_not_executed"
    assert not graph.edges


def test_composition_uses_increment_over_both_singles() -> None:
    graph = AuditedSkillGraph(bootstrap_samples=200)
    result = graph.audit_composition(
        source_niche_id="a",
        target_niche_id="b",
        source_revision_id="ra",
        target_revision_id="rb",
        evidence=tuple(
            CompositionEvidence(
                f"c{index}",
                f"f{index % 2}",
                first_gain=0.4,
                second_gain=0.5,
                composed_gain=0.9,
                execution_cost=2.0,
                cost_budget=2.0,
                executed_intermediate=True,
            )
            for index in range(4)
        ),
    )

    assert result.decision == "activate_composition"
    assert result.estimate == 0.4
