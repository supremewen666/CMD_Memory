from __future__ import annotations

from dataclasses import asdict

import pytest

from cmd_audit.repair.ghost_ecology import (
    FailureDeposit,
    GHOSTEcologyRouter,
    ObservableResidualGHOSTRouter,
    PatternResponsibility,
    PatternRevision,
    RegistrySnapshot,
    SkillRevision,
)
from cmd_audit.spec_v03.contracts import DecisionView, SkillEvidenceState, SkillSpec
from cmd_audit.spec_v03.router_stage5 import BackbonePrediction, Stage5RouterIsolationRunner
from cmd_audit.spec_v03.run_manifest import validate_decision_log
from cmd_audit.spec_v03.transfer import Stage8AResidualTransferPlan


SHA = "a" * 64


def _case() -> DecisionView:
    return DecisionView("case-1", "fixture", "episode-1", "family-1", "lineage-1", 10, {"surface": "fixture"}, {"source": "test"})


def _failure() -> FailureDeposit:
    return FailureDeposit("failure-1", "case-1", "family-1", SHA, (("feature", 1.0),), SHA, SHA)


def _skills() -> tuple[SkillRevision, SkillRevision]:
    return tuple(
        SkillRevision.create(
            skill_id=name, program={"kind": name}, parameter_schema={}, preconditions=(), postconditions=(),
            success_probe={"probe_id": f"probe-{name}"}, mutation_budget={}, rollback_program={"undo": name},
            producing_failure_id="failure-1", derivation_kind="seed", state="stable",
        )
        for name in ("left", "right")
    )  # type: ignore[return-value]


def _inputs() -> tuple[DecisionView, FailureDeposit, tuple[PatternResponsibility, ...], tuple[SkillRevision, SkillRevision], RegistrySnapshot, BackbonePrediction]:
    case, failure, skills = _case(), _failure(), _skills()
    pattern = PatternRevision.create(pattern_id="p", predicate={"kind": "fixture"}, feature_signature=("feature",), derivation_kind="seed", state="stable")
    registry = RegistrySnapshot.create(epoch=1, stable_pattern_revision_ids=(pattern.pattern_revision_id,), stable_skill_revision_ids=tuple(skill.skill_revision_id for skill in skills), config_sha256=SHA)
    prediction = BackbonePrediction.create(
        case_id=case.case_id, event_index=case.event_index, model_id="fixture-model",
        candidate_skill_revision_ids=tuple(skill.skill_revision_id for skill in skills),
        scores={skills[0].skill_revision_id: 0.2, skills[1].skill_revision_id: 0.1},
        selected_skill_revision_id=skills[0].skill_revision_id, backbone_state_sha256=SHA,
    )
    return case, failure, (PatternResponsibility(pattern.pattern_revision_id, 1.0),), skills, registry, prediction


@pytest.mark.parametrize(
    ("name", "router"),
    (("mix_ghost", ObservableResidualGHOSTRouter(allow_development_proxy=True)), ("ghost_hierarchy", GHOSTEcologyRouter(allow_development_proxy=True))),
)
def test_stage5_starts_empty_and_binds_selected_feedback_to_u_pre(name: str, router: object) -> None:
    case, failure, responsibilities, skills, registry, prediction = _inputs()
    runner = Stage5RouterIsolationRunner(name, router)  # type: ignore[arg-type]
    log = runner.route_and_observe(
        case=case, failure=failure, responsibilities=responsibilities, skills=skills, registry=registry,
        prediction=prediction, observed_after_event_index=11, delayed_utility=0.8,
        valid=True, rolled_back=False, delayed_regression=False,
    )

    assert log.backbone_prediction_sha256 == prediction.prediction_sha256
    validate_decision_log(asdict(log))
    with pytest.raises(ValueError, match="unsupported schema"):
        validate_decision_log({**asdict(log), "prediction_source": "external_backbone"})
    assert router.snapshot["stats"] != []  # type: ignore[union-attr]


def test_stage5_rejects_tampered_or_misaligned_backbone_prediction() -> None:
    case, failure, responsibilities, skills, registry, prediction = _inputs()
    tampered = BackbonePrediction(
        **{**prediction.__dict__, "event_index": 9}
    )
    runner = Stage5RouterIsolationRunner("mix_ghost", ObservableResidualGHOSTRouter(allow_development_proxy=True))

    with pytest.raises(ValueError, match="hash mismatch"):
        runner.route_and_observe(
            case=case, failure=failure, responsibilities=responsibilities, skills=skills, registry=registry,
            prediction=tampered, observed_after_event_index=11, delayed_utility=0.8,
            valid=True, rolled_back=False, delayed_regression=False,
        )


def test_stage5_rejects_a_router_with_imported_residual_state() -> None:
    _case_value, failure, responsibilities, skills, registry, prediction = _inputs()
    router = ObservableResidualGHOSTRouter(allow_development_proxy=True)
    selection = router.select(failure, pattern_responsibilities=responsibilities, skills=skills, registry=registry, event_index=10, base_scores=prediction.scores, base_selected_skill_revision_id=prediction.selected_skill_revision_id)
    # A selected update creates non-empty residual state, making it illegal for Stage 5.
    from cmd_audit.repair.ghost_ecology import DelayedOutcomeFeedback
    router.observe(selection, DelayedOutcomeFeedback(selection.selection_id, selection.selected_skill_revision_id, str(skills[0].success_probe["probe_id"]), 10, 11, 0.2, 0.8, True, False, False, "fixture", development_proxy=True))
    with pytest.raises(ValueError, match="empty state"):
        Stage5RouterIsolationRunner("mix_ghost", router)


def test_stage8a_keeps_skill_content_evidence_and_residuals_distinct() -> None:
    router = ObservableResidualGHOSTRouter()
    skill = SkillSpec("s", "1", "o", "process_fault", {}, {}, (), 0, {}, SHA)
    evidence = SkillEvidenceState("s", 1, 2.0, {}, {}, {}, {}, SHA)
    plan = Stage8AResidualTransferPlan.create(
        source_model_id="qwen-source", target_model_id="llama-target", source_residual_snapshot=router.snapshot,
        skill_content=(skill,), source_evidence=(evidence,), prefix_split="T_prefix", scored_split="T_final",
    )

    payload = plan.to_mapping()
    assert payload["skill_content_sha256s"] == (SHA,)
    assert payload["source_evidence_state_sha256s"] == (SHA,)
    assert "reset residual" in payload["transfer_rules"]["skill_content_only"]
