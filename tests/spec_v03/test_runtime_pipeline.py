from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path

import pytest

from cmd_audit.repair.ghost_ecology import ObservableResidualGHOSTRouter, SkillRevision
from cmd_audit.spec_v03.repair_stream import build_intervention, compile_repair_case, iter_public_episodes
from cmd_audit.spec_v03.runtime_pipeline import RuntimePipeline, build_legal_candidates
from cmd_audit.spec_v03.router_stage5 import BackbonePrediction
from cmd_audit.spec_v03.run_manifest import validate_decision_log
from cmd_audit.spec_v03.syndrome_runtime import decode_ecc_syndrome


def _runtime_case(template: str):
    episode = next(iter_public_episodes("halumem", Path("data/external/group_a")))
    intervention = build_intervention(episode, template, seed=17)
    case = compile_repair_case(episode, intervention)
    return case.decision_view, case.corrupt_state


def _development_decide(pipeline: RuntimePipeline, decision: object, state: object):
    return pipeline.decide(decision, state, development_zero_backbone=True)  # type: ignore[arg-type]


def _prediction(decision: object, state: object, *, model_id: str = "unconfigured") -> BackbonePrediction:
    syndrome = decode_ecc_syndrome(decision, state)  # type: ignore[arg-type]
    candidates = build_legal_candidates(state, syndrome)  # type: ignore[arg-type]
    scores = {skill_id: 0.0 for skill_id in candidates.skill_revision_ids}
    return BackbonePrediction.create(
        case_id=decision.case_id,  # type: ignore[union-attr]
        event_index=decision.event_index,  # type: ignore[union-attr]
        model_id=model_id,
        candidate_skill_revision_ids=tuple(scores),
        scores=scores,
        selected_skill_revision_id=min(scores),
        backbone_state_sha256=state.root,  # type: ignore[union-attr]
    )


def _sibling_restore_library() -> tuple[SkillRevision, ...]:
    """Keep two distinct stable revisions for the one typed restore operator."""
    base = RuntimePipeline().frozen_skill_library
    restore = next(skill for skill in base if skill.program["operator_id"] == "process_restore")
    siblings = tuple(
        SkillRevision.create(
            skill_id=f"external:process_restore:{suffix}",
            program={**restore.program, "external_revision": suffix},
            parameter_schema=restore.parameter_schema,
            preconditions=restore.preconditions,
            postconditions=restore.postconditions,
            success_probe={"probe_id": f"external-restore-{suffix}"},
            mutation_budget=restore.mutation_budget,
            rollback_program=restore.rollback_program,
            producing_failure_id=f"external-frozen-library-{suffix}",
            derivation_kind="seed",
            state="stable",
        )
        for suffix in ("a", "b")
    )
    return tuple(skill for skill in base if skill is not restore) + siblings


@pytest.mark.parametrize(("template", "mechanism", "operator"), (("drop", "process_fault", "process_restore"), ("explicit_supersede", "state_drift", "state_supersede_lineage"), ("untrusted_injection", "adversarial_poison", "poison_quarantine_audit")))
def test_runtime_structural_path_builds_only_typed_legal_candidates(template: str, mechanism: str, operator: str) -> None:
    decision, state = _runtime_case(template)
    result = _development_decide(RuntimePipeline(), decision, state)
    assert result.abstained is False
    assert result.syndrome.ecc_syndrome is not None
    assert result.syndrome.ecc_syndrome.mechanism.value == mechanism
    assert result.candidates.legal_operator_ids == (operator,)
    assert result.router_log is not None
    assert result.selected_skill_revision_id == result.router_log.selected_skill_revision_id
    assert result.selection_handle is not None
    assert result.selection_handle.selected_skill_revision_id == result.selected_skill_revision_id
    assert result.selected_operator_id == operator
    assert result.candidates.skill_content[0].content_sha256 != result.candidates.evidence[0].evidence_sha256


def test_clean_state_abstains_before_router() -> None:
    clean_decision, clean_state = _runtime_case("clean")
    result = RuntimePipeline().decide(clean_decision, clean_state)
    assert result.abstained is True
    assert result.abstain_reason == "clean"
    assert result.router_log is None


def test_mixed_runtime_structure_fails_closed_as_unknown() -> None:
    decision, state = _runtime_case("untrusted_injection")
    mixed = replace(state, cache_event_ids=(state.projection_order[0],))
    observation = dict(decision.observation)
    current_state = dict(observation["current_state"])
    current_state["state_root"] = mixed.root
    observation["current_state"] = current_state
    mixed_decision = replace(decision, observation=observation)

    result = RuntimePipeline().decide(mixed_decision, mixed)

    assert result.abstained is True
    assert result.abstain_reason == "unknown"
    assert result.candidates.legal_operator_ids == ()


class _ForbiddenEvaluatorProxy:
    def __init__(self, state: object) -> None:
        self._state = state

    def __getattr__(self, name: str) -> object:
        if name in {"evaluator_only", "intervention", "shadow_outcome_matrix"}:
            raise AssertionError(f"runtime touched evaluator truth: {name}")
        return getattr(self._state, name)


def test_runtime_never_accepts_or_accesses_evaluator_truth_proxy() -> None:
    decision, state = _runtime_case("drop")
    guarded_state = _ForbiddenEvaluatorProxy(state)
    assert not hasattr(RuntimePipeline(), "evaluator_only")
    assert not hasattr(RuntimePipeline(), "intervention")
    assert not hasattr(RuntimePipeline(), "shadow_outcome_matrix")
    assert _development_decide(RuntimePipeline(), decision, guarded_state).abstained is False


class _ObserveForbiddenRouter(ObservableResidualGHOSTRouter):
    def observe(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("decide must leave delayed feedback to ecology")


def test_decide_is_route_only_and_preserves_router_snapshot() -> None:
    decision, state = _runtime_case("drop")
    router = _ObserveForbiddenRouter()
    pipeline = RuntimePipeline(router=router)
    before = router.snapshot["snapshot_sha256"]

    result = _development_decide(pipeline, decision, state)

    assert result.abstained is False
    assert result.router_state_after_sha256 == before
    assert router.snapshot["snapshot_sha256"] == before
    assert result.router_log is not None
    assert result.router_log.router_state_before_sha256 == before
    assert result.router_log.prediction_source == "development_zero_backbone"


def test_frozen_operator_skill_identity_is_stable_across_cases() -> None:
    first_decision, first_state = _runtime_case("drop")
    second_episode = next(iter_public_episodes("halumem", Path("data/external/group_a")))
    second_case = compile_repair_case(second_episode, build_intervention(second_episode, "drop", seed=18))
    first = build_legal_candidates(first_state, decode_ecc_syndrome(first_decision, first_state))
    second = build_legal_candidates(
        second_case.corrupt_state,
        decode_ecc_syndrome(second_case.decision_view, second_case.corrupt_state),
    )

    assert first.legal_operator_ids == second.legal_operator_ids == ("process_restore",)
    assert first.skill_revision_ids == second.skill_revision_ids


def test_external_library_keeps_sibling_revisions_as_distinct_legal_candidates() -> None:
    decision, state = _runtime_case("drop")
    library = _sibling_restore_library()
    pipeline = RuntimePipeline(skill_library=library)
    candidates = build_legal_candidates(
        state, decode_ecc_syndrome(decision, state), skill_library=library,
    )
    expected = tuple(
        skill.skill_revision_id for skill in library
        if skill.program["operator_id"] == "process_restore"
    )

    assert candidates.legal_operator_ids == ("process_restore", "process_restore")
    assert candidates.skill_revision_ids == expected
    assert pipeline.frozen_registry.stable_skill_revision_ids == tuple(sorted(skill.skill_revision_id for skill in library))
    assert pipeline.frozen_skill(expected[1]) == next(skill for skill in library if skill.skill_revision_id == expected[1])

    prediction = BackbonePrediction.create(
        case_id=decision.case_id,
        event_index=decision.event_index,
        model_id="unconfigured",
        candidate_skill_revision_ids=expected,
        scores={expected[0]: -1.0, expected[1]: 1.0},
        selected_skill_revision_id=expected[1],
        backbone_state_sha256=state.root,
    )
    result = pipeline.decide(decision, state, prediction)
    assert result.selected_skill_revision_id == expected[1]
    assert result.selected_operator_id == "process_restore"
    assert result.selected_skill_content is not None
    assert result.selected_skill_content.program["external_revision"] == "b"


def test_route_handle_binds_ghost_selected_skill_to_operator() -> None:
    decision, state = _runtime_case("drop")
    result = _development_decide(RuntimePipeline(), decision, state)

    assert result.selection_handle is not None
    assert result.selection_handle.selected_skill_revision_id == result.selected_skill_revision_id
    assert result.selected_operator_id == "process_restore"
    assert result.selected_skill_revision_id in result.candidates.skill_revision_ids
    assert result.executor_dispatch() is not None
    assert result.executor_dispatch().skill_content == result.selected_skill_content  # type: ignore[union-attr]


def test_external_backbone_winner_maps_to_the_selected_operator() -> None:
    decision, state = _runtime_case("drop")
    combined = replace(state, cache_event_ids=(state.immutable_source_log[0].event_id,))
    observation = dict(decision.observation)
    current_state = dict(observation["current_state"])
    current_state["state_root"] = combined.root
    observation["current_state"] = current_state
    combined_decision = replace(decision, observation=observation)
    syndrome = decode_ecc_syndrome(combined_decision, combined)
    candidates = build_legal_candidates(combined, syndrome)
    by_operator = dict(zip(candidates.legal_operator_ids, candidates.skill_revision_ids))
    prediction = BackbonePrediction.create(
        case_id=combined_decision.case_id,
        event_index=combined_decision.event_index,
        model_id="unconfigured",
        candidate_skill_revision_ids=candidates.skill_revision_ids,
        scores={by_operator["process_restore"]: -0.5, by_operator["process_cache_invalidate"]: 0.7},
        selected_skill_revision_id=by_operator["process_cache_invalidate"],
        backbone_state_sha256=combined.root,
    )

    result = RuntimePipeline().decide(combined_decision, combined, prediction)

    assert result.selected_skill_revision_id == by_operator["process_cache_invalidate"]
    assert result.selected_operator_id == "process_cache_invalidate"
    assert result.selected_skill_content is not None
    assert result.selected_skill_content.operator_id == "process_cache_invalidate"


def test_runtime_requires_an_external_backbone_prediction_by_default() -> None:
    decision, state = _runtime_case("drop")
    with pytest.raises(ValueError, match="externally bound"):
        RuntimePipeline().decide(decision, state)


def test_external_prediction_is_bound_to_case_candidates_model_and_state() -> None:
    decision, state = _runtime_case("drop")
    prediction = _prediction(decision, state)
    result = RuntimePipeline().decide(decision, state, prediction)

    assert result.prediction_source == "external_backbone"
    assert result.router_log is not None
    assert result.router_log.schema_version == "cmd-spec-v03-runtime-router-decision-v1"
    assert result.router_log.prediction_source == "external_backbone"
    validate_decision_log(asdict(result.router_log))
    with pytest.raises(ValueError, match="unsupported schema"):
        payload = asdict(result.router_log)
        del payload["prediction_source"]
        validate_decision_log(payload)

    with pytest.raises(ValueError, match="hash mismatch"):
        RuntimePipeline().decide(
            decision,
            state,
            BackbonePrediction(**{**prediction.__dict__, "event_index": decision.event_index + 1}),
        )
    with pytest.raises(ValueError, match="model_id"):
        RuntimePipeline().decide(decision, state, _prediction(decision, state, model_id="other"))
    wrong_candidate = BackbonePrediction.create(
        case_id=decision.case_id,
        event_index=decision.event_index,
        model_id="unconfigured",
        candidate_skill_revision_ids=("not-a-runtime-skill",),
        scores={"not-a-runtime-skill": 0.0},
        selected_skill_revision_id="not-a-runtime-skill",
        backbone_state_sha256=state.root,
    )
    with pytest.raises(ValueError, match="candidate set mismatch"):
        RuntimePipeline().decide(decision, state, wrong_candidate)
