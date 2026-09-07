from __future__ import annotations

from dataclasses import replace
import inspect
from pathlib import Path

from cmd_audit.spec_v03.repair_stream import (
    build_intervention, compile_repair_case, execute_operator, iter_public_episodes, operator_catalog,
)
from cmd_audit.spec_v03.runtime_pipeline import RuntimePipeline, build_legal_candidates
from cmd_audit.spec_v03.router_stage5 import BackbonePrediction
from cmd_audit.spec_v03.syndrome_runtime import decode_ecc_syndrome
from cmd_audit.spec_v03.system_runtime import (
    MaturedObservation,
    PrequentialCMDRuntime,
    VersionedMemoryStore,
    _rebased_decision,
)


def _case(template: str):
    episode = next(iter_public_episodes("halumem", Path("data/external/group_a")))
    return compile_repair_case(episode, build_intervention(episode, template, seed=17))


def _prediction(decision, state) -> BackbonePrediction:
    candidates = build_legal_candidates(state, decode_ecc_syndrome(decision, state))
    scores = {skill_id: 0.0 for skill_id in candidates.skill_revision_ids}
    return BackbonePrediction.create(
        case_id=decision.case_id,
        event_index=decision.event_index,
        model_id="unconfigured",
        candidate_skill_revision_ids=candidates.skill_revision_ids,
        scores=scores,
        selected_skill_revision_id=min(scores),
        backbone_state_sha256=state.root,
    )


def test_process_state_and_poison_commit_with_immutable_source_and_audit() -> None:
    for template in ("drop", "explicit_supersede", "untrusted_injection"):
        case = _case(template)
        runtime = PrequentialCMDRuntime(case.corrupt_state)
        outcome = runtime.process(case.decision_view, _prediction(case.decision_view, case.corrupt_state))

        assert outcome.commit is not None and outcome.commit.committed
        assert outcome.receipt is None and outcome.pending is not None
        assert outcome.gates is not None and outcome.gates.resolved_syndrome
        receipt = runtime.finalize_matured(outcome.pending.pending_id, outcome.pending.matures_at_event_index)
        assert receipt.committed
        assert runtime.store.state.immutable_source_log == case.corrupt_state.immutable_source_log
        assert runtime.store.state.audit_log == case.corrupt_state.audit_log


def test_cas_conflict_rolls_back_receipt_without_overwriting_external_root() -> None:
    case = _case("drop")
    runtime = PrequentialCMDRuntime(case.corrupt_state)

    def conflict(store) -> None:
        spec = next(spec for spec in operator_catalog() if spec.operator_id == "process_restore")
        store.replace_current(execute_operator(store.state, spec))

    outcome = runtime.process(case.decision_view, _prediction(case.decision_view, case.corrupt_state), before_commit=conflict)

    assert outcome.commit is not None and outcome.commit.conflicted
    assert outcome.pending is not None
    receipt = runtime.finalize_matured(outcome.pending.pending_id, outcome.pending.matures_at_event_index)
    assert receipt.rolled_back
    assert runtime.store.root == outcome.commit.current_root
    assert receipt.after_root == receipt.before_root
    assert receipt.provenance["observed_store_root"] == outcome.commit.current_root


def test_versioned_memory_store_has_benign_cas_commit() -> None:
    case = _case("drop")
    store = VersionedMemoryStore(case.corrupt_state)
    spec = next(spec for spec in operator_catalog() if spec.operator_id == "process_restore")
    shadow = execute_operator(case.corrupt_state, spec)

    committed = store.commit(before_root=case.corrupt_state.root, shadow_state=shadow)

    assert committed.committed and not committed.conflicted
    assert store.root == shadow.root


def test_safety_and_locality_gates_restore_before_root() -> None:
    case = _case("drop")

    def unsafe(state, _spec):
        return replace(state, audit_log=())

    poison = _case("untrusted_injection")
    unsafe_runtime = PrequentialCMDRuntime(poison.corrupt_state, shadow_executor=unsafe)
    unsafe = unsafe_runtime.process(poison.decision_view, _prediction(poison.decision_view, poison.corrupt_state))
    assert unsafe.commit is not None and not unsafe.commit.committed
    assert unsafe.pending is not None
    assert unsafe_runtime.finalize_matured(unsafe.pending.pending_id, unsafe.pending.matures_at_event_index).safety_violation
    assert unsafe_runtime.store.root == poison.corrupt_state.root

    def tagged_but_active(state, _spec):
        return replace(state, quarantine_set=(state.audit_log[0].event_id,))

    def must_not_run(_store):
        raise AssertionError("failed gates must not invoke CAS hook")

    tagged_runtime = PrequentialCMDRuntime(poison.corrupt_state, shadow_executor=tagged_but_active)
    tagged = tagged_runtime.process(
        poison.decision_view, _prediction(poison.decision_view, poison.corrupt_state),
        before_commit=must_not_run,
    )
    assert tagged.gates is not None and not tagged.gates.safety_passed
    assert tagged.commit is not None and not tagged.commit.committed

    def excessive_locality(state, _spec):
        source_id = state.immutable_source_log[0].event_id
        return replace(state, cache_event_ids=(source_id,), supersession_edges=((source_id, source_id),), quarantine_set=(source_id,))

    nonlocal_runtime = PrequentialCMDRuntime(case.corrupt_state, shadow_executor=excessive_locality)
    locality_outcome = nonlocal_runtime.process(case.decision_view, _prediction(case.decision_view, case.corrupt_state))
    assert locality_outcome.gates is not None and not locality_outcome.gates.locality_passed
    assert locality_outcome.pending is not None
    assert nonlocal_runtime.finalize_matured(locality_outcome.pending.pending_id, locality_outcome.pending.matures_at_event_index).rolled_back
    assert nonlocal_runtime.store.root == case.corrupt_state.root


def test_state_repair_requires_a_valid_supersession_edge_to_be_clean() -> None:
    case = _case("explicit_supersede")

    def delete_old_without_edge(state, _spec):
        source_ref = state.audit_log[0].payload["supersedes_source_ref"]
        old = next(event.event_id for event in state.immutable_source_log if event.source_ref == source_ref)
        order = tuple(event_id for event_id in state.projection_order if event_id != old)
        return replace(
            state, projection_order=order,
            projection_index=tuple((event_id, index) for index, event_id in enumerate(order)),
        )

    runtime = PrequentialCMDRuntime(case.corrupt_state, shadow_executor=delete_old_without_edge)
    outcome = runtime.process(case.decision_view, _prediction(case.decision_view, case.corrupt_state))
    assert outcome.gates is not None and not outcome.gates.resolved_syndrome
    assert outcome.commit is not None and not outcome.commit.committed
    assert runtime.store.root == case.corrupt_state.root


def test_committed_state_repair_reaudits_as_clean() -> None:
    case = _case("explicit_supersede")
    runtime = PrequentialCMDRuntime(case.corrupt_state)

    outcome = runtime.process(case.decision_view, _prediction(case.decision_view, case.corrupt_state))

    assert outcome.commit is not None and outcome.commit.committed
    reaudited = decode_ecc_syndrome(_rebased_decision(case.decision_view, runtime.store.state), runtime.store.state)
    assert reaudited.descriptor.classification == "clean"
    assert reaudited.ecc_syndrome is None


def test_receipt_is_pending_until_a_later_clean_event_settles_router_feedback() -> None:
    broken = _case("drop")
    clean = _case("clean")
    runtime = PrequentialCMDRuntime(broken.corrupt_state)
    first = runtime.process(broken.decision_view, _prediction(broken.decision_view, broken.corrupt_state))
    assert first.receipt is None and first.pending is not None
    assert runtime.pending_outcomes == (first.pending,)
    # A same-event receipt cannot train the action selected at that event.
    assert runtime.ecology.settle_before(broken.decision_view.event_index) == ()

    later_decision = replace(clean.decision_view, event_index=first.pending.matures_at_event_index)
    later = runtime.process(
        later_decision,
        matured_observations=(MaturedObservation(first.pending.pending_id, later_decision.event_index),),
    )
    assert later.abstain is not None and later.abstain.reason == "clean"
    assert len(later.settlements) == 1
    assert runtime.pending_outcomes == ()


def test_process_rejects_future_observation_without_partial_receipt_submission() -> None:
    broken = _case("drop")
    clean = _case("clean")
    runtime = PrequentialCMDRuntime(broken.corrupt_state)
    first = runtime.process(broken.decision_view, _prediction(broken.decision_view, broken.corrupt_state))
    assert first.pending is not None
    router_before = runtime.pipeline.router.snapshot["snapshot_sha256"]
    future_index = first.pending.matures_at_event_index + 1
    decision = replace(clean.decision_view, event_index=first.pending.matures_at_event_index)

    try:
        runtime.process(
            decision,
            matured_observations=(MaturedObservation(first.pending.pending_id, future_index),),
        )
    except ValueError as error:
        assert "after the current event" in str(error)
    else:
        raise AssertionError("future observation was accepted")

    assert runtime.pending_outcomes == (first.pending,)
    assert runtime.ecology.settle_before(decision.event_index) == ()
    assert runtime.pipeline.router.snapshot["snapshot_sha256"] == router_before


def test_unfinalized_pending_does_not_update_and_recurrence_is_negative_feedback() -> None:
    broken = _case("drop")
    clean = _case("clean")
    runtime = PrequentialCMDRuntime(broken.corrupt_state)
    first = runtime.process(broken.decision_view, _prediction(broken.decision_view, broken.corrupt_state))
    assert first.pending is not None
    assert runtime.ecology.settle_before(first.pending.matures_at_event_index) == ()

    decision = replace(clean.decision_view, event_index=first.pending.matures_at_event_index)
    settled = runtime.process(
        decision,
        matured_observations=(MaturedObservation(first.pending.pending_id, decision.event_index, recurrence_after_commit=True),),
    )
    assert len(settled.settlements) == 1
    skill = runtime.ecology.skills[first.pending.selected_skill_revision_id]
    assert skill.evidence.successful_receipt_ids == ()


def test_quarantined_skill_is_removed_from_the_next_event_candidate_mask() -> None:
    first_case = _case("drop")
    first_runtime = PrequentialCMDRuntime(first_case.corrupt_state)
    first = first_runtime.process(
        first_case.decision_view,
        _prediction(first_case.decision_view, first_case.corrupt_state),
    )
    assert first.pending is not None
    first_runtime.finalize_matured(
        first.pending.pending_id,
        first.pending.matures_at_event_index,
        safety_violation=True,
    )
    first_runtime.ecology.settle_before(first.pending.matures_at_event_index)

    second_case = _case("drop")
    second_runtime = PrequentialCMDRuntime(
        second_case.corrupt_state,
        pipeline=first_runtime.pipeline,
        ecology=first_runtime.ecology,
    )
    second_decision = replace(
        second_case.decision_view,
        event_index=first.pending.matures_at_event_index + 1,
    )
    second = second_runtime.process(second_decision, development_zero_backbone=True)

    assert second.abstain is None
    assert second.decision.selected_operator_id == "process_projection_rebuild"
    assert ("process_restore", "lifecycle-ineligible") in second.decision.candidates.rejected_operator_reasons


def test_clean_and_unknown_are_audited_abstentions_without_receipts() -> None:
    clean = _case("clean")
    runtime = PrequentialCMDRuntime(clean.corrupt_state)
    result = runtime.process(clean.decision_view)
    assert result.abstain is not None and result.receipt is None
    assert runtime.pending_outcomes == ()

    unknown_case = _case("untrusted_injection")
    mixed = replace(unknown_case.corrupt_state, cache_event_ids=(unknown_case.corrupt_state.projection_order[0],))
    observation = dict(unknown_case.decision_view.observation)
    current = dict(observation["current_state"])
    current["state_root"] = mixed.root
    observation["current_state"] = current
    unknown = replace(unknown_case.decision_view, observation=observation)
    unknown_runtime = PrequentialCMDRuntime(mixed)
    outcome = unknown_runtime.process(unknown)
    assert outcome.abstain is not None and outcome.abstain.reason == "unknown"
    assert outcome.receipt is None


def test_snapshot_binds_memory_ecology_pending_receipts_and_logs_without_gold_proxy() -> None:
    case = _case("drop")
    runtime = PrequentialCMDRuntime(case.corrupt_state)
    runtime.process(case.decision_view, _prediction(case.decision_view, case.corrupt_state))
    snapshot = runtime.snapshot
    assert snapshot["memory_root"] == runtime.store.root
    assert snapshot["pending_outcomes"]
    PrequentialCMDRuntime.verify_snapshot(snapshot)

    tampered = dict(snapshot)
    tampered["memory_root"] = "0" * 64
    try:
        PrequentialCMDRuntime.verify_snapshot(tampered)
    except ValueError as error:
        assert "hash mismatch" in str(error)
    else:
        raise AssertionError("tampered snapshot was accepted")

    runtime_source = inspect.getsource(__import__("cmd_audit.spec_v03.system_runtime", fromlist=["PrequentialCMDRuntime"]))
    assert "EvaluatorOnly" not in runtime_source
    assert "ShadowOutcomeMatrix" not in runtime_source
    assert "RepairCase" not in runtime_source
