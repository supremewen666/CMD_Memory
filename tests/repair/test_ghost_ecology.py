from __future__ import annotations

import json
from pathlib import Path

import pytest

from cmd_audit.repair.ecc import Contract, EccRepairReceipt, MemAuditEccAdapter
from cmd_audit.repair.incident_store import IncidentLedger
from cmd_audit.repair.incident_triage import (
    ClassificationStatus,
    IncidentMechanism,
    ProcessFaultSubtype,
    RepairFamily,
)
from cmd_audit.repair.ghost_ecology import (
    DelayedOutcomeFeedback,
    DeploymentSkillFeedback,
    derive_discovery_pressure,
    EcologyLedger,
    FailureDeposit,
    GHOSTEcologyRouter,
    ObservableResidualGHOSTRouter,
    GhostEcology,
    NicheObservation,
    NicheObserver,
    PatternResponsibility,
    PatternRevision,
    PromotionEvidence,
    RegistrySnapshot,
    SkillRevision,
    observe_niche_perturbation,
    propose_pattern_split,
    propose_pattern_merge,
    skill_promotion_decision,
    validate_responsibilities,
)
from cmd_audit.repair.failure_memory import FailureMemoryRecord
from cmd_audit.repair.operator_library import (
    OperatorSpecRecord,
    PatternRecord,
    SkillRevisionRecord,
)
from cmd_audit.counterfactual import OperatorSpec, PipelineAction


def _failure(index: int = 1) -> FailureDeposit:
    return FailureDeposit(
        failure_id=f"failure-{index}",
        case_id=f"case-{index}",
        family_id_audit_only=f"family-{index % 2}",
        failure_memory_sha256=f"memory-{index}",
        features=(("conflict", 1.0), ("temporal", 0.5)),
        context_sha256=f"context-{index}",
        provenance_sha256=f"provenance-{index}",
    )


def _pattern(*, state: str = "stable") -> PatternRevision:
    return PatternRevision.create(
        pattern_id="temporal-scoped-conflict",
        predicate={"kind": "typed_predicate", "requires": ["temporal", "conflict"]},
        feature_signature=("conflict", "temporal"),
        derivation_kind="seed",
        state=state,
    )


def _skill(
    name: str,
    producer: str = "failure-1",
    *,
    state: str = "stable",
    parents: tuple[str, ...] = (),
    kind: str = "seed",
) -> SkillRevision:
    return SkillRevision.create(
        skill_id=name,
        program={
            "kind": "typed_repair_program",
            "steps": [
                {"op": "split_validity_scope", "target": "$target"},
                {"op": "relink_dependencies", "scope": "$scope"},
                {"op": "verify_local_queries", "budget": "$probe_budget"},
            ],
        },
        parameter_schema={
            "target": {"type": "memory_item_id"},
            "scope": {"enum": ["local", "dependency_closure"]},
            "probe_budget": {"type": "integer", "minimum": 1, "maximum": 8},
        },
        preconditions=({"predicate": "has_temporal_conflict"},),
        postconditions=({"predicate": "conflict_resolved_in_scope"},),
        success_probe={"probe_id": f"probe:{name}", "kind": "deployment_consistency"},
        mutation_budget={"max_items": 4, "max_edges": 8},
        rollback_program={"kind": "restore_snapshot", "scope": "touched_items"},
        producing_failure_id=producer,
        parent_revision_ids=parents,
        derivation_kind=kind,
        state=state,
    )


def _ready_ecology(tmp_path: Path) -> tuple[GhostEcology, FailureDeposit, PatternRevision, tuple[SkillRevision, SkillRevision], RegistrySnapshot]:
    ecology = GhostEcology(EcologyLedger(tmp_path / "ecology.jsonl"))
    failure = _failure()
    ecology.deposit_failure(failure, event_index=1)
    pattern = _pattern()
    ecology.propose_pattern(pattern, event_index=2)
    skills = (_skill("scope-split"), _skill("conditional-retention"))
    ecology.propose_skill(skills[0], event_index=3)
    ecology.propose_skill(skills[1], event_index=4)
    registry = RegistrySnapshot.create(
        epoch=1,
        stable_pattern_revision_ids=(pattern.pattern_revision_id,),
        stable_skill_revision_ids=tuple(row.skill_revision_id for row in skills),
        config_sha256="config-v2",
    )
    ecology.freeze_registry(registry, event_index=5)
    return ecology, failure, pattern, skills, registry


def test_ledger_is_hash_chained_replayable_and_tamper_evident(tmp_path: Path) -> None:
    path = tmp_path / "ecology.jsonl"
    ledger = EcologyLedger(path)
    first = ledger.append("failure_observed", event_index=1, payload={"x": 1})
    second = ledger.append("pattern_revision", event_index=2, payload={"x": 2})
    assert second["previous_event_sha256"] == first["event_sha256"]
    assert EcologyLedger(path).head_sha256 == second["event_sha256"]
    rows = path.read_text(encoding="utf-8").splitlines()
    value = json.loads(rows[0])
    value["payload"]["x"] = 9
    rows[0] = json.dumps(value)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        EcologyLedger(path)


def test_soft_pattern_responsibility_must_be_closed_probability_mass() -> None:
    validate_responsibilities(
        (PatternResponsibility("p1", 0.6), PatternResponsibility("p2", 0.4))
    )
    with pytest.raises(ValueError, match="sum to one"):
        validate_responsibilities(
            (PatternResponsibility("p1", 0.6), PatternResponsibility("p2", 0.3))
        )


def test_open_world_skill_is_arbitrary_program_with_lineage_not_action_enum() -> None:
    parent = _skill("scope-split")
    child = _skill(
        "temporal-claim-reconciliation",
        parents=(parent.skill_revision_id,),
        kind="structural_revision",
        state="proposed",
    )
    assert child.program["steps"][0]["op"] == "split_validity_scope"
    assert child.parent_revision_ids == (parent.skill_revision_id,)
    assert child.program_sha256 != ""


def test_existing_three_layer_records_migrate_as_v2_seeds() -> None:
    failure_record = FailureMemoryRecord(
        error_type="retrieval_error", wrong_memory="wrong", original_evidence="evidence",
        cause="miss", corrected_memory="correct", repair_action="retrieval_error",
        repair_guidance="retrieve bridge", trigger_signature="bridge",
    )
    deposit = FailureDeposit.from_failure_memory(
        failure_record, failure_id="failure-legacy", case_id="case-legacy",
        family_id_audit_only="family-legacy", features={"retrieval": 1.0},
        context_sha256="context", provenance_sha256="provenance",
    )
    legacy_pattern = PatternRecord(
        "pattern-legacy", "prototype", "bridge-fingerprint", "feature-hash", "v1"
    )
    pattern = PatternRevision.from_pattern_record(legacy_pattern)
    operator = OperatorSpec.single(0, PipelineAction.RETRIEVAL_ERROR)
    spec = OperatorSpecRecord.from_operator(operator)
    revision = SkillRevisionRecord(
        "revision-legacy", "patterned", "family-legacy",
        (legacy_pattern.pattern_id,), spec.spec_hash, None, "seed",
        "case-legacy", "tape-legacy", "library-legacy", 1,
    )
    skill = SkillRevision.from_operator_revision(
        revision, spec, producing_failure_id=deposit.failure_id,
        success_probe={"probe_id": "probe:retrieval", "kind": "recurrence"},
        mutation_budget={"max_items": 2},
        rollback_program={"kind": "restore_snapshot"},
    )
    assert pattern.state == "stable"
    assert skill.state == "stable"
    assert skill.program["kind"] == "operator_spec_v1"


def test_three_layer_sedimentation_replays_and_sealed_epoch_refuses_discovery(tmp_path: Path) -> None:
    ecology, failure, pattern, skills, registry = _ready_ecology(tmp_path)
    ecology.bind_failure(
        failure.failure_id,
        (PatternResponsibility(pattern.pattern_revision_id, 1.0),),
        event_index=6,
    )
    ecology.bind_pattern_skill(
        pattern.pattern_revision_id, skills[0].skill_revision_id,
        applicability=0.9, event_index=7,
    )
    replayed = GhostEcology(EcologyLedger(tmp_path / "ecology.jsonl"))
    assert failure.failure_id in replayed.failures
    assert pattern.pattern_revision_id in replayed.patterns
    assert skills[0].skill_revision_id in replayed.skills
    assert registry.registry_id in replayed.registries
    sealed = GhostEcology(
        EcologyLedger(tmp_path / "sealed.jsonl"),
        discovery_authorized=False,
        evaluation_only=True,
    )
    with pytest.raises(PermissionError, match="sealed"):
        sealed.deposit_failure(failure, event_index=1)
    with pytest.raises(PermissionError, match="discovery"):
        sealed.propose_pattern(pattern, event_index=1)


def test_router_updates_only_selected_skill_registered_probe_and_replays(tmp_path: Path) -> None:
    ecology, failure, pattern, skills, registry = _ready_ecology(tmp_path)
    responsibilities = (PatternResponsibility(pattern.pattern_revision_id, 1.0),)
    decision = ecology.select(
        failure,
        responsibilities=responsibilities,
        candidate_skill_revision_ids=tuple(row.skill_revision_id for row in skills),
        registry_id=registry.registry_id,
        event_index=10,
    )
    selected = next(row for row in skills if row.skill_revision_id == decision.selected_skill_revision_id)
    before = decision.posterior_before_sha256
    feedback = DeploymentSkillFeedback(
        decision.selection_id, decision.selected_skill_revision_id,
        str(selected.success_probe["probe_id"]), 1.0, 0.05, 0.02,
        False, False, True, "typed-executor+skill-probe-v2",
    )
    after = ecology.observe(decision, feedback, event_index=11)
    assert after["snapshot_sha256"] != before
    replayed = GhostEcology(EcologyLedger(tmp_path / "ecology.jsonl"))
    assert replayed.router.snapshot == after

    decision2 = ecology.select(
        failure, responsibilities=responsibilities, skills=skills,
        registry=registry, event_index=20,
    ) if False else ecology.router.select(
        failure, pattern_responsibilities=responsibilities, skills=skills,
        registry=registry, event_index=20,
    )
    with pytest.raises(ValueError, match="unselected"):
        ecology.router.observe(
            decision2,
            DeploymentSkillFeedback(
                decision2.selection_id,
                next(row.skill_revision_id for row in skills if row.skill_revision_id != decision2.selected_skill_revision_id),
                "wrong", 1.0, 0.0, 0.0, False, False, True, "deployment",
            ),
        )


def test_router_evolves_from_root_bound_ecc_repair_receipt(tmp_path: Path) -> None:
    ecology, failure, pattern, skills, registry = _ready_ecology(tmp_path)
    decision = ecology.select(
        failure,
        responsibilities=(PatternResponsibility(pattern.pattern_revision_id, 1.0),),
        candidate_skill_revision_ids=tuple(row.skill_revision_id for row in skills),
        registry_id=registry.registry_id,
        event_index=10,
    )
    selected = ecology.skills[decision.selected_skill_revision_id]
    receipt = EccRepairReceipt(
        receipt_id="receipt-1",
        syndrome_id="syndrome-1",
        incident_id="incident-1",
        selection_id=decision.selection_id,
        selected_skill_revision_id=decision.selected_skill_revision_id,
        probe_id=str(selected.success_probe["probe_id"]),
        observed_after_event_index=11,
        before_root="root-before",
        shadow_root="root-shadow",
        after_root="root-shadow",
        resolved_syndrome=True,
        invariants_passed=True,
        committed=True,
        rolled_back=False,
        safety_violation=False,
        locality_cost=0.05,
        recurrence_after_commit=False,
        provenance={"evaluator": "ecc-v1"},
    )

    after = ecology.observe_receipt(decision, receipt, event_index=11)

    assert after["snapshot_sha256"] != decision.posterior_before_sha256
    feedback = ecology.ledger.events[-2]
    assert feedback["payload"]["feedback_kind"] == "ecc_repair_receipt"
    assert "gold_derived" not in feedback["payload"]
    replayed = GhostEcology(EcologyLedger(tmp_path / "ecology.jsonl"))
    assert replayed.router.snapshot == after


def test_receipt_settlement_binds_incident_sink_and_router_update(tmp_path: Path) -> None:
    ecology, failure, pattern, skills, registry = _ready_ecology(tmp_path)
    decision = ecology.select(
        failure,
        responsibilities=(PatternResponsibility(pattern.pattern_revision_id, 1.0),),
        candidate_skill_revision_ids=tuple(row.skill_revision_id for row in skills),
        registry_id=registry.registry_id,
        event_index=10,
    )
    selected = ecology.skills[decision.selected_skill_revision_id]
    syndrome = Contract(
        syndrome_id="syndrome-1",
        observation_id="observation-1",
        incident_id="incident-1",
        observed_at_event_index=9,
        state_root="root-before",
        source_manifest_root="manifest-1",
        mechanism=IncidentMechanism.PROCESS_FAULT,
        repair_family=RepairFamily.PIPELINE_PATCH,
        classification_status=ClassificationStatus.CONFIRMED,
        process_fault_subtype=ProcessFaultSubtype.RETRIEVAL,
        signal_ids=("retrieval-miss",),
        provenance={"detector": "memaudit-v1"},
    )
    receipt = EccRepairReceipt(
        receipt_id="receipt-1",
        syndrome_id=syndrome.syndrome_id,
        incident_id=syndrome.incident_id,
        selection_id=decision.selection_id,
        selected_skill_revision_id=decision.selected_skill_revision_id,
        probe_id=str(selected.success_probe["probe_id"]),
        observed_after_event_index=11,
        before_root=syndrome.state_root,
        shadow_root="root-after",
        after_root="root-after",
        resolved_syndrome=True,
        invariants_passed=True,
        committed=True,
        rolled_back=False,
        safety_violation=False,
        locality_cost=0.0,
        recurrence_after_commit=False,
        provenance={"checker": "ecc-v1"},
    )
    incidents = IncidentLedger(tmp_path / "incidents.jsonl")

    event, snapshot = MemAuditEccAdapter().settle_repair(
        syndrome,
        receipt,
        ledger=incidents,
        ecology=ecology,
        decision=decision,
        event_index=11,
    )

    assert event["mechanism"] == "process_fault"
    assert len(incidents.views.process_faults) == 1
    assert snapshot["snapshot_sha256"] != decision.posterior_before_sha256


def test_pending_selection_survives_process_restart_before_feedback(tmp_path: Path) -> None:
    ecology, failure, pattern, skills, registry = _ready_ecology(tmp_path)
    decision = ecology.select(
        failure,
        responsibilities=(PatternResponsibility(pattern.pattern_revision_id, 1.0),),
        candidate_skill_revision_ids=tuple(row.skill_revision_id for row in skills),
        registry_id=registry.registry_id,
        event_index=10,
    )
    restarted = GhostEcology(EcologyLedger(tmp_path / "ecology.jsonl"))
    selected = restarted.skills[decision.selected_skill_revision_id]
    after = restarted.observe(
        decision,
        DeploymentSkillFeedback(
            decision.selection_id, selected.skill_revision_id,
            str(selected.success_probe["probe_id"]), 1.0, 0.0, 0.0,
            False, False, True, "deployment-after-restart-v2",
        ),
        event_index=11,
    )
    assert after["snapshot_sha256"] != decision.posterior_before_sha256


def test_mature_delayed_outcome_sediments_and_replays(tmp_path: Path) -> None:
    ecology, failure, pattern, skills, registry = _ready_ecology(tmp_path)
    decision = ecology.select(
        failure,
        responsibilities=(
            PatternResponsibility(pattern.pattern_revision_id, 1.0),
        ),
        candidate_skill_revision_ids=tuple(
            row.skill_revision_id for row in skills
        ),
        registry_id=registry.registry_id,
        event_index=10,
    )
    selected = ecology.skills[decision.selected_skill_revision_id]
    after = ecology.observe(
        decision,
        DelayedOutcomeFeedback(
            selection_id=decision.selection_id,
            selected_skill_revision_id=selected.skill_revision_id,
            probe_id=str(selected.success_probe["probe_id"]),
            selected_at_event_index=10,
            observed_after_event_index=20,
            pre_action_prior=0.2,
            delayed_utility=0.7,
            valid=True,
            rolled_back=False,
            delayed_regression=False,
            provenance="live-matured-window-v1",
            development_proxy=False,
        ),
        event_index=20,
    )
    feedback_events = [
        row for row in EcologyLedger(tmp_path / "ecology.jsonl").events
        if row["event_type"] == "skill_feedback"
    ]
    assert feedback_events[-1]["payload"]["feedback_kind"] == "delayed_outcome"
    assert GhostEcology(EcologyLedger(tmp_path / "ecology.jsonl")).router.snapshot == after


def test_gold_feedback_refused_and_evaluation_only_does_not_update() -> None:
    failure = _failure()
    pattern = _pattern()
    skill = _skill("scope-split")
    registry = RegistrySnapshot.create(
        epoch=1,
        stable_pattern_revision_ids=(pattern.pattern_revision_id,),
        stable_skill_revision_ids=(skill.skill_revision_id,),
        config_sha256="config",
    )
    responsibilities = (PatternResponsibility(pattern.pattern_revision_id, 1.0),)
    router = GHOSTEcologyRouter()
    decision = router.select(
        failure, pattern_responsibilities=responsibilities,
        skills=(skill,), registry=registry, event_index=1,
    )
    with pytest.raises(ValueError, match="gold"):
        router.observe(
            decision,
            DeploymentSkillFeedback(
                decision.selection_id, skill.skill_revision_id,
                str(skill.success_probe["probe_id"]), 1.0, 0.0, 0.0,
                False, False, True, "shadow", gold_derived=True,
            ),
        )
    decision = router.select(
        failure, pattern_responsibilities=responsibilities,
        skills=(skill,), registry=registry, event_index=2,
    )
    before = router.snapshot
    after = router.observe(
        decision,
        DeploymentSkillFeedback(
            decision.selection_id, skill.skill_revision_id,
            str(skill.success_probe["probe_id"]), 1.0, 0.0, 0.0,
            False, False, True, "deployment", evaluation_only=True,
        ),
    )
    assert after == before


def test_frozen_evaluator_utility_is_not_cost_penalized_twice() -> None:
    feedback = DeploymentSkillFeedback(
        "selection", "skill", "probe", 0.6, 0.2, 0.1,
        False, False, True, "frozen-deployment-evaluator-v1",
        estimated_utility=0.37,
    )
    assert feedback.reward == 0.37


def test_delayed_outcome_feedback_uses_mature_future_residual() -> None:
    feedback = DelayedOutcomeFeedback(
        selection_id="selection",
        selected_skill_revision_id="skill",
        probe_id="probe",
        selected_at_event_index=10,
        observed_after_event_index=15,
        pre_action_prior=0.25,
        delayed_utility=0.70,
        valid=True,
        rolled_back=False,
        delayed_regression=False,
        provenance="dev-delayed-outcome-proxy-v1",
    )
    assert feedback.reward == pytest.approx(0.45)
    with pytest.raises(ValueError, match="after selection"):
        DelayedOutcomeFeedback(
            selection_id="selection",
            selected_skill_revision_id="skill",
            probe_id="probe",
            selected_at_event_index=10,
            observed_after_event_index=10,
            pre_action_prior=0.25,
            delayed_utility=0.70,
            valid=True,
            rolled_back=False,
            delayed_regression=False,
            provenance="dev-delayed-outcome-proxy-v1",
        )


def test_coherent_hierarchy_defers_singleton_local_effect() -> None:
    failure = _failure()
    pattern = _pattern()
    skills = (_skill("left"), _skill("right"))
    registry = RegistrySnapshot.create(
        epoch=1,
        stable_pattern_revision_ids=(pattern.pattern_revision_id,),
        stable_skill_revision_ids=tuple(row.skill_revision_id for row in skills),
        config_sha256="partial-pooling-test",
    )
    router = GHOSTEcologyRouter(
        seed=24,
        exploration=0.0,
        min_pattern_support=2.0,
        min_local_support=3.0,
    )
    decision = router.select(
        failure,
        pattern_responsibilities=(
            PatternResponsibility(pattern.pattern_revision_id, 1.0),
        ),
        skills=skills,
        registry=registry,
        event_index=10,
        skill_priors={
            skills[0].skill_revision_id: 1.0,
            skills[1].skill_revision_id: -1.0,
        },
    )
    selected = next(
        row for row in skills
        if row.skill_revision_id == decision.selected_skill_revision_id
    )
    router.observe(
        decision,
        DeploymentSkillFeedback(
            decision.selection_id,
            selected.skill_revision_id,
            str(selected.success_probe["probe_id"]),
            1.0,
            0.0,
            0.0,
            False,
            False,
            True,
            "deployment",
            estimated_utility=0.8,
        ),
    )
    snapshot = router.snapshot
    levels = {row[0][0] for row in snapshot["stats"]}
    assert levels == {"global", "pattern", "local"}
    assert snapshot["min_local_support"] == 3.0
    # One local observation is retained but cannot yet override its parent.
    next_decision = router.select(
        failure,
        pattern_responsibilities=(
            PatternResponsibility(pattern.pattern_revision_id, 1.0),
        ),
        skills=skills,
        registry=registry,
        event_index=11,
        skill_priors={row.skill_revision_id: 0.0 for row in skills},
    )
    selected_score = dict(next_decision.scores)[selected.skill_revision_id]
    other_score = next(
        score for skill_id, score in next_decision.scores
        if skill_id != selected.skill_revision_id
    )
    assert selected_score == pytest.approx(0.4)  # global 0.8 / precision 2
    assert other_score == pytest.approx(0.0)


def test_deployment_observable_skill_prior_participates_in_routing() -> None:
    failure = _failure()
    pattern = _pattern()
    skills = (_skill("left"), _skill("right"))
    registry = RegistrySnapshot.create(
        epoch=1,
        stable_pattern_revision_ids=(pattern.pattern_revision_id,),
        stable_skill_revision_ids=tuple(row.skill_revision_id for row in skills),
        config_sha256="prior-routing-test",
    )
    router = GHOSTEcologyRouter(seed=24, exploration=0.0)
    decision = router.select(
        failure,
        pattern_responsibilities=(
            PatternResponsibility(pattern.pattern_revision_id, 1.0),
        ),
        skills=skills,
        registry=registry,
        event_index=10,
        skill_priors={
            skills[0].skill_revision_id: -0.2,
            skills[1].skill_revision_id: 0.4,
        },
    )
    assert decision.selected_skill_revision_id == skills[1].skill_revision_id
    with pytest.raises(ValueError, match="exactly cover"):
        router.select(
            failure,
            pattern_responsibilities=(
                PatternResponsibility(pattern.pattern_revision_id, 1.0),
            ),
            skills=skills,
            registry=registry,
            event_index=11,
            skill_priors={skills[0].skill_revision_id: 0.0},
        )


def test_observable_residual_router_cold_start_exactly_falls_back_to_backbone() -> None:
    failure = _failure()
    pattern = _pattern()
    skills = (_skill("left"), _skill("right"))
    registry = RegistrySnapshot.create(
        epoch=1,
        stable_pattern_revision_ids=(pattern.pattern_revision_id,),
        stable_skill_revision_ids=tuple(row.skill_revision_id for row in skills),
        config_sha256="observable-residual-cold-start",
    )
    router = ObservableResidualGHOSTRouter(seed=24, exploration=0.08)
    decision = router.select(
        failure,
        pattern_responsibilities=(
            PatternResponsibility(pattern.pattern_revision_id, 1.0),
        ),
        skills=skills,
        registry=registry,
        event_index=10,
        base_scores={
            skills[0].skill_revision_id: 0.10,
            skills[1].skill_revision_id: 0.11,
        },
        base_selected_skill_revision_id=skills[0].skill_revision_id,
    )

    assert decision.selected_skill_revision_id == skills[0].skill_revision_id
    assert decision.selection_mode == "observable_fallback"
    assert decision.exploration_activated is False
    assert decision.active_levels == ()
    assert router.diagnostics["fallback_count"] == 1


def test_observable_residual_router_activates_only_supported_hierarchy_levels() -> None:
    failure = _failure()
    pattern = _pattern()
    skills = (_skill("left"), _skill("right"))
    registry = RegistrySnapshot.create(
        epoch=1,
        stable_pattern_revision_ids=(pattern.pattern_revision_id,),
        stable_skill_revision_ids=tuple(row.skill_revision_id for row in skills),
        config_sha256="observable-residual-support-gates",
    )
    router = ObservableResidualGHOSTRouter(
        seed=24,
        exploration=0.0,
        min_global_support=1.0,
        min_pattern_support=2.0,
        min_local_support=3.0,
        min_exploration_support=3.0,
        allow_development_proxy=True,
    )
    responsibilities = (
        PatternResponsibility(pattern.pattern_revision_id, 1.0),
    )
    first = router.select(
        failure,
        pattern_responsibilities=responsibilities,
        skills=skills,
        registry=registry,
        event_index=10,
        base_scores={
            skills[0].skill_revision_id: 0.20,
            skills[1].skill_revision_id: 0.10,
        },
        base_selected_skill_revision_id=skills[0].skill_revision_id,
    )
    router.observe(
        first,
        DelayedOutcomeFeedback(
            selection_id=first.selection_id,
            selected_skill_revision_id=skills[0].skill_revision_id,
            probe_id=str(skills[0].success_probe["probe_id"]),
            selected_at_event_index=10,
            observed_after_event_index=11,
            pre_action_prior=0.20,
            delayed_utility=0.80,
            valid=True,
            rolled_back=False,
            delayed_regression=False,
            provenance="dev-delayed-outcome-proxy-v1",
            development_proxy=True,
        ),
    )

    second = router.select(
        failure,
        pattern_responsibilities=responsibilities,
        skills=skills,
        registry=registry,
        event_index=12,
        base_scores={
            skills[0].skill_revision_id: 0.00,
            skills[1].skill_revision_id: 0.10,
        },
        base_selected_skill_revision_id=skills[1].skill_revision_id,
    )

    assert second.selected_skill_revision_id == skills[0].skill_revision_id
    assert second.selection_mode == "residual_override"
    assert second.active_levels == ("global",)
    assert second.exploration_activated is False


def test_observable_residual_router_explores_only_after_mature_support() -> None:
    failure = _failure()
    pattern = _pattern()
    skills = (_skill("left"), _skill("right"))
    registry = RegistrySnapshot.create(
        epoch=1,
        stable_pattern_revision_ids=(pattern.pattern_revision_id,),
        stable_skill_revision_ids=tuple(row.skill_revision_id for row in skills),
        config_sha256="observable-residual-exploration-gate",
    )
    router = ObservableResidualGHOSTRouter(
        seed=24,
        exploration=0.08,
        min_global_support=1.0,
        min_pattern_support=1.0,
        min_local_support=1.0,
        min_exploration_support=1.0,
        allow_development_proxy=True,
    )
    responsibilities = (
        PatternResponsibility(pattern.pattern_revision_id, 1.0),
    )

    for position, event_index in enumerate((10, 12)):
        expected = skills[position]
        decision = router.select(
            failure,
            pattern_responsibilities=responsibilities,
            skills=skills,
            registry=registry,
            event_index=event_index,
            base_scores={
                skills[0].skill_revision_id: 0.90 if position == 0 else -0.90,
                skills[1].skill_revision_id: -0.90 if position == 0 else 0.90,
            },
            base_selected_skill_revision_id=expected.skill_revision_id,
        )
        assert decision.exploration_activated is False
        selected = next(
            row
            for row in skills
            if row.skill_revision_id == decision.selected_skill_revision_id
        )
        router.observe(
            decision,
            DelayedOutcomeFeedback(
                selection_id=decision.selection_id,
                selected_skill_revision_id=selected.skill_revision_id,
                probe_id=str(selected.success_probe["probe_id"]),
                selected_at_event_index=event_index,
                observed_after_event_index=event_index + 1,
                pre_action_prior=0.20,
                delayed_utility=0.60,
                valid=True,
                rolled_back=False,
                delayed_regression=False,
                provenance="dev-delayed-outcome-proxy-v1",
                development_proxy=True,
            ),
        )

    supported = router.select(
        failure,
        pattern_responsibilities=responsibilities,
        skills=skills,
        registry=registry,
        event_index=14,
        base_scores={
            skills[0].skill_revision_id: 0.20,
            skills[1].skill_revision_id: 0.10,
        },
        base_selected_skill_revision_id=skills[0].skill_revision_id,
    )
    assert supported.exploration_activated is True
    assert router.diagnostics["exploration_count"] == 1


def test_observable_residual_router_snapshot_restores_replayable_routing() -> None:
    failure = _failure()
    pattern = _pattern()
    skills = (_skill("left"), _skill("right"))
    registry = RegistrySnapshot.create(
        epoch=1,
        stable_pattern_revision_ids=(pattern.pattern_revision_id,),
        stable_skill_revision_ids=tuple(row.skill_revision_id for row in skills),
        config_sha256="observable-residual-replay",
    )
    responsibilities = (
        PatternResponsibility(pattern.pattern_revision_id, 1.0),
    )
    router = ObservableResidualGHOSTRouter(
        seed=24,
        min_global_support=1.0,
        min_pattern_support=2.0,
        min_local_support=3.0,
        min_exploration_support=1.0,
        allow_development_proxy=True,
    )
    first = router.select(
        failure,
        pattern_responsibilities=responsibilities,
        skills=skills,
        registry=registry,
        event_index=10,
        base_scores={
            skills[0].skill_revision_id: 0.20,
            skills[1].skill_revision_id: 0.10,
        },
        base_selected_skill_revision_id=skills[0].skill_revision_id,
    )
    router.observe(
        first,
        DelayedOutcomeFeedback(
            selection_id=first.selection_id,
            selected_skill_revision_id=skills[0].skill_revision_id,
            probe_id=str(skills[0].success_probe["probe_id"]),
            selected_at_event_index=10,
            observed_after_event_index=11,
            pre_action_prior=0.20,
            delayed_utility=0.70,
            valid=True,
            rolled_back=False,
            delayed_regression=False,
            provenance="dev-delayed-outcome-proxy-v1",
            development_proxy=True,
        ),
    )
    restored = ObservableResidualGHOSTRouter.from_snapshot(router.snapshot)

    select_kwargs = {
        "pattern_responsibilities": responsibilities,
        "skills": skills,
        "registry": registry,
        "event_index": 12,
        "base_scores": {
            skills[0].skill_revision_id: 0.20,
            skills[1].skill_revision_id: 0.10,
        },
        "base_selected_skill_revision_id": skills[0].skill_revision_id,
    }
    assert router.select(failure, **select_kwargs) == restored.select(
        failure, **select_kwargs
    )


def test_observable_residual_routing_profiles_isolate_coordinates_and_round_trip() -> None:
    failure = _failure()
    pattern = _pattern()
    skills = (_skill("left"), _skill("right"))
    registry = RegistrySnapshot.create(
        epoch=1,
        stable_pattern_revision_ids=(pattern.pattern_revision_id,),
        stable_skill_revision_ids=tuple(row.skill_revision_id for row in skills),
        config_sha256="observable-residual-profile",
    )
    responsibilities = (
        PatternResponsibility(pattern.pattern_revision_id, 1.0),
    )
    router = ObservableResidualGHOSTRouter(
        routing_profile="global",
        allow_development_proxy=True,
    )
    decision = router.select(
        failure,
        pattern_responsibilities=responsibilities,
        skills=skills,
        registry=registry,
        event_index=10,
        base_scores={
            skills[0].skill_revision_id: 0.2,
            skills[1].skill_revision_id: 0.1,
        },
        base_selected_skill_revision_id=skills[0].skill_revision_id,
    )
    router.observe(
        decision,
        DelayedOutcomeFeedback(
            selection_id=decision.selection_id,
            selected_skill_revision_id=skills[0].skill_revision_id,
            probe_id=str(skills[0].success_probe["probe_id"]),
            selected_at_event_index=10,
            observed_after_event_index=11,
            pre_action_prior=0.2,
            delayed_utility=0.8,
            valid=True,
            rolled_back=False,
            delayed_regression=False,
            provenance="dev-delayed-outcome-proxy-v1",
            development_proxy=True,
        ),
    )

    assert {row[0][0] for row in router.snapshot["stats"]} == {"global"}
    assert router.snapshot["routing_profile"] == "global"
    restored = ObservableResidualGHOSTRouter.from_snapshot(router.snapshot)
    assert restored.snapshot == router.snapshot
    assert restored.diagnostics["routing_profile"] == "global"


def test_observable_residual_no_support_gate_is_active_at_zero_support() -> None:
    failure = _failure()
    pattern = _pattern()
    skills = (_skill("left"), _skill("right"))
    registry = RegistrySnapshot.create(
        epoch=1,
        stable_pattern_revision_ids=(pattern.pattern_revision_id,),
        stable_skill_revision_ids=tuple(row.skill_revision_id for row in skills),
        config_sha256="observable-residual-no-support-gate",
    )
    router = ObservableResidualGHOSTRouter(routing_profile="full_no_support_gate")
    decision = router.select(
        failure,
        pattern_responsibilities=(
            PatternResponsibility(pattern.pattern_revision_id, 1.0),
        ),
        skills=skills,
        registry=registry,
        event_index=10,
        base_scores={
            skills[0].skill_revision_id: 0.2,
            skills[1].skill_revision_id: 0.1,
        },
        base_selected_skill_revision_id=skills[0].skill_revision_id,
    )

    assert decision.active_levels == ("global", "pattern", "local")
    assert decision.exploration_activated is True


def test_observable_residual_router_rejects_unselected_delayed_feedback() -> None:
    failure = _failure()
    pattern = _pattern()
    skills = (_skill("left"), _skill("right"))
    registry = RegistrySnapshot.create(
        epoch=1,
        stable_pattern_revision_ids=(pattern.pattern_revision_id,),
        stable_skill_revision_ids=tuple(row.skill_revision_id for row in skills),
        config_sha256="observable-residual-selected-only",
    )
    router = ObservableResidualGHOSTRouter(allow_development_proxy=True)
    decision = router.select(
        failure,
        pattern_responsibilities=(
            PatternResponsibility(pattern.pattern_revision_id, 1.0),
        ),
        skills=skills,
        registry=registry,
        event_index=10,
        base_scores={
            skills[0].skill_revision_id: 0.20,
            skills[1].skill_revision_id: 0.10,
        },
        base_selected_skill_revision_id=skills[0].skill_revision_id,
    )

    with pytest.raises(ValueError, match="unselected"):
        router.observe(
            decision,
            DelayedOutcomeFeedback(
                selection_id=decision.selection_id,
                selected_skill_revision_id=skills[1].skill_revision_id,
                probe_id=str(skills[1].success_probe["probe_id"]),
                selected_at_event_index=10,
                observed_after_event_index=11,
                pre_action_prior=0.20,
                delayed_utility=0.60,
                valid=True,
                rolled_back=False,
                delayed_regression=False,
                provenance="dev-delayed-outcome-proxy-v1",
                development_proxy=True,
            ),
        )


def test_sealed_ecology_logs_evaluation_without_posterior_sedimentation(tmp_path: Path) -> None:
    builder, failure, pattern, skills, registry = _ready_ecology(tmp_path)
    _ = builder
    sealed = GhostEcology(
        EcologyLedger(tmp_path / "ecology.jsonl"),
        discovery_authorized=False,
        evaluation_only=True,
    )
    responsibilities = (PatternResponsibility(pattern.pattern_revision_id, 1.0),)
    decision = sealed.select(
        failure,
        responsibilities=responsibilities,
        candidate_skill_revision_ids=tuple(row.skill_revision_id for row in skills),
        registry_id=registry.registry_id,
        event_index=10,
    )
    skill = next(row for row in skills if row.skill_revision_id == decision.selected_skill_revision_id)
    before = sealed.router.snapshot
    sealed.observe(
        decision,
        DeploymentSkillFeedback(
            decision.selection_id, skill.skill_revision_id,
            str(skill.success_probe["probe_id"]), 1.0, 0.0, 0.0,
            False, False, True, "sealed-deployment-evaluation-v2",
            evaluation_only=True,
        ),
        event_index=11,
    )
    assert sealed.router.snapshot == before
    assert len(sealed.ledger.by_type("skill_feedback")) == 1
    assert sealed.ledger.by_type("posterior_snapshot") == ()


def test_promotion_requires_later_cross_family_deployment_evidence() -> None:
    skill = _skill("scope-split")
    evidence = [
        PromotionEvidence(
            f"feedback-{index}",
            "failure-1" if index == 0 else f"later-{index}",
            f"family-{index % 2}", skill.skill_revision_id,
            True, False, False,
        )
        for index in range(4)
    ]
    decision = skill_promotion_decision(skill, evidence, anchor_non_regression=True)
    assert decision.eligible
    assert "feedback-0" not in decision.supporting_feedback_ids
    assert not skill_promotion_decision(
        skill, evidence, anchor_non_regression=False
    ).eligible


def _observations(pattern_id: str, *, winner: str, other: str, unresolved: int = 1) -> tuple[NicheObservation, ...]:
    rows = []
    for index in range(10):
        selected = winner if index < 8 else other
        rows.append(
            NicheObservation(
                f"failure-{index}", pattern_id, selected, 1.0, True,
                1.0 if selected == winner else 0.5,
                index >= unresolved,
            )
        )
    return tuple(rows)


def test_niche_observation_competition_split_pressure_and_perturbation() -> None:
    pattern = _pattern()
    observer = NicheObserver()
    baseline = observer.snapshot(
        pattern_revision_id=pattern.pattern_revision_id,
        observations=_observations(pattern.pattern_revision_id, winner="a", other="b"),
        window_start=1, window_end=10,
    )
    assert baseline.state == "stable"
    assert baseline.dominant_skill_revision_id == "a"
    assert baseline.arrival_count == 10
    assert baseline.skill_fitness[0][2] > 0.0
    contested_rows = tuple(
        NicheObservation(
            f"x-{index}", pattern.pattern_revision_id,
            "a" if index % 2 else "b", 1.0, True, 0.5, False,
        )
        for index in range(10)
    )
    contested = observer.snapshot(
        pattern_revision_id=pattern.pattern_revision_id,
        observations=contested_rows,
        window_start=11, window_end=20,
        previous_state="stable",
    )
    assert contested.state == "branching"
    assert propose_pattern_split(pattern, contested) is not None
    replacement = observer.snapshot(
        pattern_revision_id=pattern.pattern_revision_id,
        observations=_observations(pattern.pattern_revision_id, winner="b", other="c"),
        window_start=21, window_end=30, previous_state="stable",
    )
    recovery = observer.snapshot(
        pattern_revision_id=pattern.pattern_revision_id,
        observations=_observations(pattern.pattern_revision_id, winner="a", other="b"),
        window_start=31, window_end=40, previous_state="stable",
    )
    report = observe_niche_perturbation(
        baseline=baseline,
        removed_skill_revision_id="a",
        windows=(replacement, recovery),
    )
    assert report.replacement_after_windows == 1
    assert report.recovered_after_windows == 2


def test_pattern_merge_is_governed_and_requires_behavioral_equivalence() -> None:
    left = _pattern()
    right = PatternRevision.create(
        pattern_id="temporal-alias",
        predicate={"kind": "typed_predicate", "requires": ["time", "conflict"]},
        feature_signature=("conflict", "temporal"),
        derivation_kind="seed",
        state="stable",
    )
    assert propose_pattern_merge(
        left, right, feature_similarity=0.95,
        skill_ranking_similarity=0.95, feedback_similarity=0.95,
    )["governance_required"] is True
    assert propose_pattern_merge(
        left, right, feature_similarity=0.95,
        skill_ranking_similarity=0.5, feedback_similarity=0.95,
    ) is None


def test_niche_state_change_is_observed_as_append_only_transition(tmp_path: Path) -> None:
    ecology = GhostEcology(EcologyLedger(tmp_path / "niche.jsonl"))
    pattern = _pattern()
    observer = NicheObserver()
    emerging = observer.snapshot(
        pattern_revision_id=pattern.pattern_revision_id,
        observations=(NicheObservation("f", pattern.pattern_revision_id, "a", 1.0, True, 1.0, True),),
        window_start=1, window_end=1,
    )
    stable = observer.snapshot(
        pattern_revision_id=pattern.pattern_revision_id,
        observations=_observations(pattern.pattern_revision_id, winner="a", other="b"),
        window_start=2, window_end=11, previous_state=emerging.state,
    )
    ecology.record_niche_snapshot(emerging, event_index=1)
    ecology.record_niche_snapshot(stable, event_index=2)
    event_id = ecology.record_niche_transition(emerging, stable, event_index=3)
    assert event_id is not None
    assert ecology.ledger.by_type("niche_transition")[0]["payload"]["to_state"] == "stable"


def test_discovery_pressure_routes_unmatched_failure_to_governed_pattern_birth() -> None:
    pressure = derive_discovery_pressure(
        niche_id="niche:unmatched",
        window_start=1,
        window_end=10,
        unmatched_responsibilities=(0.7, 0.8, 0.9),
        abstentions=(False, False, True),
        prediction_residuals=(0.1, 0.2, 0.1),
    )
    assert pressure is not None
    assert pressure.proposal_kind == "new_pattern"
    assert pressure.governance_required is True
    assert derive_discovery_pressure(
        niche_id="niche:quiet",
        window_start=1,
        window_end=10,
        unmatched_responsibilities=(0.1,),
        abstentions=(False,),
        prediction_residuals=(0.1,),
    ) is None


def test_niche_lifecycle_rejects_moves_outside_the_transition_table(
    tmp_path: Path,
) -> None:
    """Niches need a transition table for the same reason patterns and skills do.

    Without one the ``subject_kind == "niche"`` branch fell through to an empty
    allow-list, so any two distinct ``NICHE_STATES`` were accepted and a ledger
    could record a resurrection (``extinct -> latent``) while auditing clean.
    """
    ecology, _failure, pattern, _skills, _registry = _ready_ecology(tmp_path)

    event_id = ecology.lifecycle_transition(
        subject_kind="niche",
        subject_revision_id=pattern.pattern_revision_id,
        from_state="latent",
        to_state="emerging",
        reason="occupation rising",
        supporting_event_ids=(),
        event_index=6,
    )
    assert event_id

    # Extinction is terminal, and occupation cannot skip straight past emergence.
    for from_state, to_state in (
        ("extinct", "latent"),
        ("latent", "stable"),
        ("extinct", "occupied"),
    ):
        with pytest.raises(ValueError, match="invalid lifecycle transition"):
            ecology.lifecycle_transition(
                subject_kind="niche",
                subject_revision_id=pattern.pattern_revision_id,
                from_state=from_state,
                to_state=to_state,
                reason="illegal",
                supporting_event_ids=(),
                event_index=7,
            )
