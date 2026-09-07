from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from cmd_audit.repair.ecc import EccRepairReceipt
from cmd_audit.repair.ghost_ecology import (
    FailureDeposit, ObservableResidualGHOSTRouter, PatternResponsibility,
    PatternRevision, RegistrySnapshot, SkillRevision,
)
from cmd_audit.spec_v03.ecology_runtime import EcologyRuntime, SkillStatus


SHA = "a" * 64


def _failure(index: int) -> FailureDeposit:
    return FailureDeposit(f"failure-{index}", f"case-{index}", f"family-{index % 3}", SHA, (("surface", 1.0),), SHA, SHA)


def _skill(failure_id: str, *, name: str = "skill", parents: tuple[str, ...] = ()) -> SkillRevision:
    return SkillRevision.create(
        skill_id=name, program={"kind": name}, parameter_schema={}, preconditions=(), postconditions=(),
        success_probe={"probe_id": f"probe-{name}"}, mutation_budget={}, rollback_program={"undo": name},
        producing_failure_id=failure_id, parent_revision_ids=parents,
        derivation_kind="structural_revision" if parents else "discovery", state="stable",
    )


def test_ecology_default_model_id_matches_unconfigured_runtime() -> None:
    runtime = EcologyRuntime(ObservableResidualGHOSTRouter(allow_development_proxy=True))

    assert runtime.model_id == "unconfigured"


def _active_runtime() -> tuple[EcologyRuntime, SkillRevision, PatternRevision, RegistrySnapshot]:
    runtime = EcologyRuntime(ObservableResidualGHOSTRouter(allow_development_proxy=True))
    skill = _skill("frozen-library")
    runtime.seed_frozen_skill(skill, event_index=0)
    pattern = PatternRevision.create(pattern_id="p", predicate={"kind": "fixture"}, feature_signature=("surface",), derivation_kind="seed", state="stable")
    registry = RegistrySnapshot.create(epoch=1, stable_pattern_revision_ids=(pattern.pattern_revision_id,), stable_skill_revision_ids=(skill.skill_revision_id,), config_sha256=SHA)
    return runtime, skill, pattern, registry


def _select(runtime: EcologyRuntime, skill: SkillRevision, pattern: PatternRevision, registry: RegistrySnapshot, index: int):
    failure = _failure(index)
    runtime.deposit_failure(failure, event_index=index)
    selection = runtime.router.select(
        failure, pattern_responsibilities=(PatternResponsibility(pattern.pattern_revision_id, 1.0),),
        skills=(skill,), registry=registry, event_index=index,
        base_scores={skill.skill_revision_id: 0.25}, base_selected_skill_revision_id=skill.skill_revision_id,
    )
    runtime.register_selection(selection, failure=failure, skills=(skill,), pre_action_prior=0.25)
    return selection


def _receipt(selection, skill: SkillRevision, observed: int, *, receipt_id: str, rollback: bool = False, safety: bool = False) -> EccRepairReceipt:
    return EccRepairReceipt(
        receipt_id=receipt_id, syndrome_id="s", incident_id="i", selection_id=selection.selection_id,
        selected_skill_revision_id=skill.skill_revision_id, probe_id=str(skill.success_probe["probe_id"]),
        observed_after_event_index=observed, before_root="before", shadow_root="shadow",
        after_root="before" if rollback else "shadow", resolved_syndrome=not rollback,
        invariants_passed=not rollback, committed=not rollback, rolled_back=rollback,
        safety_violation=safety, locality_cost=0.1, recurrence_after_commit=False, provenance={"source": "test"},
    )


def test_delayed_settlement_has_no_future_leakage_and_is_deterministically_ordered() -> None:
    runtime, skill, pattern, registry = _active_runtime()
    first = _select(runtime, skill, pattern, registry, 3)
    second = _select(runtime, skill, pattern, registry, 4)
    runtime.submit_receipt(_receipt(second, skill, 6, receipt_id="z"))
    runtime.submit_receipt(_receipt(first, skill, 6, receipt_id="a"))

    before = runtime.router.snapshot["snapshot_sha256"]
    assert runtime.settle_before(5) == ()
    assert runtime.router.snapshot["snapshot_sha256"] == before
    settled = runtime.settle_before(6)
    assert [row.selection_id for row in settled] == [first.selection_id, second.selection_id]
    evidence = runtime.skills[skill.skill_revision_id].evidence
    assert evidence.valid_after_event == 7
    assert runtime.eligible_skills(6) == (skill,)  # old active population still serves at t.
    assert not runtime.router_feedback_eligible(_receipt(first, skill, 6, receipt_id="a"), event_index=7)


def test_failure_memory_deposit_is_content_addressed_before_skill_birth() -> None:
    runtime = EcologyRuntime(ObservableResidualGHOSTRouter(allow_development_proxy=True))
    failure_id = runtime.deposit_failure_memory(
        {"error_type": "retrieval_error", "repair": "replay"}, failure_id="fm-1", case_id="case",
        family_id_audit_only="family", features={"surface": 1.0}, context_sha256=SHA,
        provenance_sha256=SHA, event_index=0,
    )
    skill = _skill(failure_id)
    born = runtime.birth(skill, event_index=0)
    assert born.effective_after_event == 1
    assert born.evidence.evidence_state_sha256 != skill.program_sha256


def test_right_censor_is_not_a_failure_or_success() -> None:
    runtime, skill, pattern, registry = _active_runtime()
    selection = _select(runtime, skill, pattern, registry, 8)
    assert runtime.right_censor(8) == (selection.selection_id,)
    late = _receipt(selection, skill, 9, receipt_id="late")
    runtime.submit_receipt(late)
    assert not runtime.router_feedback_eligible(late, event_index=9)
    evidence = runtime.skills[skill.skill_revision_id].evidence
    assert evidence.settled_receipt_ids == evidence.successful_receipt_ids == ()


def test_rollback_can_quarantine_and_excludes_a_skill_from_future_candidates() -> None:
    runtime, skill, pattern, registry = _active_runtime()
    selection = _select(runtime, skill, pattern, registry, 2)
    runtime.submit_receipt(_receipt(selection, skill, 3, receipt_id="rollback", rollback=True))
    runtime.settle_before(3)
    runtime.quarantine(skill.skill_revision_id, reason="rollback", event_index=3)
    assert runtime.skills[skill.skill_revision_id].status is SkillStatus.QUARANTINED
    assert runtime.eligible_skills(3) == (skill,)
    assert runtime.eligible_skills(4) == ()


def test_promotion_supersede_and_retire_preserve_content_lineage() -> None:
    runtime, skill, pattern, registry = _active_runtime()
    for index in (1, 2, 3):
        selection = _select(runtime, skill, pattern, registry, index)
        runtime.submit_receipt(_receipt(selection, skill, index + 10, receipt_id=f"r-{index}"))
    runtime.settle_before(13)
    # Serving activation above is just fixture setup.  Promotion itself moves a
    # probationary candidate into the active population after settled evidence.
    runtime._skills[skill.skill_revision_id] = replace(
        runtime._skills[skill.skill_revision_id], status=SkillStatus.PROBATIONARY
    )
    promoted = runtime.promote(skill.skill_revision_id, event_index=14, anchor_non_regression=True)
    assert promoted.status is SkillStatus.ACTIVE
    assert promoted.transition_effective_after_event == 15
    assert runtime.eligible_skills(14) == ()
    assert runtime.eligible_skills(15) == (skill,)

    successor = _skill("failure-1", name="skill-v2", parents=(skill.skill_revision_id,))
    runtime.supersede(skill.skill_revision_id, successor, event_index=15)
    assert runtime.skills[skill.skill_revision_id].status is SkillStatus.SUPERSEDED
    assert runtime.skills[successor.skill_revision_id].effective_after_event == 16
    runtime.retire(successor.skill_revision_id, reason="frozen rule", event_index=16)
    assert runtime.skills[successor.skill_revision_id].status is SkillStatus.RETIRED


def test_snapshot_hash_chain_rejects_tampering_and_restores_router_state() -> None:
    # Frozen legacy content is also legal router input; this fixture has no
    # lifecycle mutation outside the append-only runtime log.
    runtime = EcologyRuntime(ObservableResidualGHOSTRouter(allow_development_proxy=True))
    skill = _skill("legacy-failure")
    runtime.seed_frozen_skill(skill, event_index=0)
    pattern = PatternRevision.create(pattern_id="p", predicate={"kind": "fixture"}, feature_signature=("surface",), derivation_kind="seed", state="stable")
    registry = RegistrySnapshot.create(epoch=1, stable_pattern_revision_ids=(pattern.pattern_revision_id,), stable_skill_revision_ids=(skill.skill_revision_id,), config_sha256=SHA)
    selection = _select(runtime, skill, pattern, registry, 1)
    runtime.submit_receipt(_receipt(selection, skill, 2, receipt_id="r"))
    runtime.settle_before(2)
    snapshot = runtime.snapshot
    restored = EcologyRuntime.from_snapshot(snapshot)
    assert restored.snapshot["snapshot_sha256"] == snapshot["snapshot_sha256"]
    assert restored.router.snapshot == runtime.router.snapshot

    tampered = copy.deepcopy(snapshot)
    tampered["events"][0]["payload"]["failure_id"] = "evil"
    with pytest.raises(ValueError, match="hash/schema mismatch"):
        EcologyRuntime.from_snapshot(tampered)


def test_safety_receipt_automatically_quarantines_from_the_next_event() -> None:
    runtime, skill, pattern, registry = _active_runtime()
    selection = _select(runtime, skill, pattern, registry, 4)
    receipt = _receipt(selection, skill, 5, receipt_id="unsafe", rollback=True, safety=True)
    runtime.submit_receipt(receipt)
    runtime.settle_before(5)
    record = runtime.skills[skill.skill_revision_id]
    assert record.status is SkillStatus.QUARANTINED
    assert record.quarantine_reason == "frozen_rule:safety_violation"
    assert runtime.eligible_skills(5) == (skill,)
    assert runtime.eligible_skills(6) == ()


def test_unknown_skill_cannot_be_registered_without_a_frozen_seed() -> None:
    runtime = EcologyRuntime(ObservableResidualGHOSTRouter(allow_development_proxy=True))
    skill = _skill("unseeded")
    failure = _failure(1)
    pattern = PatternRevision.create(pattern_id="p", predicate={"kind": "fixture"}, feature_signature=("surface",), derivation_kind="seed", state="stable")
    registry = RegistrySnapshot.create(epoch=1, stable_pattern_revision_ids=(pattern.pattern_revision_id,), stable_skill_revision_ids=(skill.skill_revision_id,), config_sha256=SHA)
    selection = runtime.router.select(
        failure, pattern_responsibilities=(PatternResponsibility(pattern.pattern_revision_id, 1.0),), skills=(skill,), registry=registry,
        event_index=1, base_scores={skill.skill_revision_id: 0.25}, base_selected_skill_revision_id=skill.skill_revision_id,
    )
    with pytest.raises(PermissionError, match="unregistered"):
        runtime.register_selection(selection, failure=failure, skills=(skill,), pre_action_prior=0.25)
