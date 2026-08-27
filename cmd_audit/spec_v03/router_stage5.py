"""Stage 5, zero-state router isolation wired to the current GHOST routers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Mapping, Sequence

from cmd_audit.repair.ghost_ecology import (
    DelayedOutcomeFeedback,
    FailureDeposit,
    GHOSTEcologyRouter,
    GHOSTEcologyRouter as _SingleGHOST,
    ObservableResidualGHOSTRouter,
    PatternResponsibility,
    RegistrySnapshot,
    SkillRevision,
)

from .contracts import DecisionView, canonical_sha256


@dataclass(frozen=True)
class BackbonePrediction:
    """Content-addressed decision-time backbone output.

    ``u_t_pre`` is not supplied by a later evaluator: it is derived from the
    score of the action actually selected by the router from this signed record.
    """

    case_id: str
    event_index: int
    model_id: str
    candidate_skill_revision_ids: tuple[str, ...]
    scores: Mapping[str, float]
    selected_skill_revision_id: str
    backbone_state_sha256: str
    prediction_sha256: str

    @classmethod
    def create(
        cls, *, case_id: str, event_index: int, model_id: str,
        candidate_skill_revision_ids: Sequence[str], scores: Mapping[str, float],
        selected_skill_revision_id: str, backbone_state_sha256: str,
    ) -> "BackbonePrediction":
        candidate_ids = tuple(sorted(candidate_skill_revision_ids))
        score_map = {str(key): float(value) for key, value in scores.items()}
        body = {
            "case_id": case_id, "event_index": event_index, "model_id": model_id,
            "candidate_skill_revision_ids": candidate_ids, "scores": score_map,
            "selected_skill_revision_id": selected_skill_revision_id,
            "backbone_state_sha256": backbone_state_sha256,
        }
        return cls(**body, prediction_sha256=canonical_sha256(body))

    def verify(self, candidates: Sequence[str], case: DecisionView) -> None:
        body = asdict(self)
        claimed = body.pop("prediction_sha256")
        if claimed != canonical_sha256(body):
            raise ValueError("backbone prediction hash mismatch")
        if self.case_id != case.case_id or self.event_index != case.event_index:
            raise ValueError("backbone prediction is not bound to this decision-time case")
        expected = tuple(sorted(candidates))
        if self.candidate_skill_revision_ids != expected or set(self.scores) != set(expected):
            raise ValueError("backbone prediction candidate set mismatch")
        if self.selected_skill_revision_id not in expected:
            raise ValueError("backbone selected action is not legal")
        if not all(math.isfinite(value) and -1.0 <= value <= 1.0 for value in self.scores.values()):
            raise ValueError("backbone scores must be finite values in [-1, 1]")
        if len(self.backbone_state_sha256) != 64:
            raise ValueError("backbone state must be content addressed")


@dataclass(frozen=True)
class BoundDelayedFeedback:
    feedback: DelayedOutcomeFeedback
    backbone_prediction_sha256: str
    u_t_pre: float

    @classmethod
    def create(
        cls, *, prediction: BackbonePrediction, selected_skill_revision_id: str,
        selection_id: str, probe_id: str, observed_after_event_index: int,
        delayed_utility: float, valid: bool, rolled_back: bool, delayed_regression: bool,
        development_proxy: bool = True,
    ) -> "BoundDelayedFeedback":
        if selected_skill_revision_id not in prediction.scores:
            raise ValueError("selected action has no decision-time backbone score")
        u_t_pre = prediction.scores[selected_skill_revision_id]
        feedback = DelayedOutcomeFeedback(
            selection_id=selection_id,
            selected_skill_revision_id=selected_skill_revision_id,
            probe_id=probe_id,
            selected_at_event_index=prediction.event_index,
            observed_after_event_index=observed_after_event_index,
            pre_action_prior=u_t_pre,
            delayed_utility=delayed_utility,
            valid=valid,
            rolled_back=rolled_back,
            delayed_regression=delayed_regression,
            provenance="cmd-spec-v03-bound-delayed-feedback-v1",
            development_proxy=development_proxy,
        )
        return cls(feedback, prediction.prediction_sha256, u_t_pre)

    def verify(self, prediction: BackbonePrediction, selected_skill_revision_id: str) -> None:
        if self.backbone_prediction_sha256 != prediction.prediction_sha256:
            raise ValueError("feedback is not bound to the decision-time backbone prediction")
        expected = prediction.scores.get(selected_skill_revision_id)
        if expected is None or self.u_t_pre != expected or self.feedback.pre_action_prior != expected:
            raise ValueError("u_t_pre does not equal the selected action's decision-time backbone prediction")


@dataclass(frozen=True)
class RouterDecisionLog:
    schema_version: str
    router_name: str
    case_id: str
    selected_skill_revision_id: str
    candidate_skill_revision_ids: tuple[str, ...]
    backbone_prediction_sha256: str
    router_state_before_sha256: str
    selection_id: str
    selection_mode: str
    scores: tuple[tuple[str, float], ...]


class Stage5RouterIsolationRunner:
    """Single-decision bridge enforcing the Stage 5 common-feedback contract."""

    def __init__(self, router_name: str, router: ObservableResidualGHOSTRouter | GHOSTEcologyRouter) -> None:
        if router_name not in {"mix_ghost", "ghost_hierarchy"}:
            raise ValueError("Stage 5 bridge supports Mix GHOST and GHOST hierarchy only")
        if router_name == "mix_ghost" and not isinstance(router, ObservableResidualGHOSTRouter):
            raise TypeError("mix_ghost must use ObservableResidualGHOSTRouter")
        if router_name == "ghost_hierarchy" and not isinstance(router, _SingleGHOST):
            raise TypeError("ghost_hierarchy must use GHOSTEcologyRouter")
        snapshot = router.snapshot
        if snapshot.get("stats") != []:
            raise ValueError("Stage 5 adaptive routers must start from an empty state")
        self.router_name = router_name
        self.router = router

    def route_and_observe(
        self, *, case: DecisionView, failure: FailureDeposit,
        responsibilities: Sequence[PatternResponsibility], skills: Sequence[SkillRevision],
        registry: RegistrySnapshot, prediction: BackbonePrediction,
        observed_after_event_index: int, delayed_utility: float, valid: bool,
        rolled_back: bool, delayed_regression: bool,
    ) -> RouterDecisionLog:
        candidate_ids = tuple(skill.skill_revision_id for skill in skills)
        prediction.verify(candidate_ids, case)
        before = str(self.router.snapshot["snapshot_sha256"])
        if self.router_name == "mix_ghost":
            selection = self.router.select(
                failure, pattern_responsibilities=responsibilities, skills=skills, registry=registry,
                event_index=case.event_index, base_scores=prediction.scores,
                base_selected_skill_revision_id=prediction.selected_skill_revision_id,
            )
        else:
            selection = self.router.select(
                failure, pattern_responsibilities=responsibilities, skills=skills, registry=registry,
                event_index=case.event_index, skill_priors=prediction.scores,
            )
        selected = selection.selected_skill_revision_id
        skill = next(row for row in skills if row.skill_revision_id == selected)
        bound = BoundDelayedFeedback.create(
            prediction=prediction, selected_skill_revision_id=selected,
            selection_id=selection.selection_id, probe_id=str(skill.success_probe["probe_id"]),
            observed_after_event_index=observed_after_event_index, delayed_utility=delayed_utility,
            valid=valid, rolled_back=rolled_back, delayed_regression=delayed_regression,
        )
        bound.verify(prediction, selected)
        # Both adaptive routes receive precisely the same *shape* of selected
        # action feedback.  The underlying routers reject unselected feedback.
        self.router.observe(selection, bound.feedback)
        return RouterDecisionLog(
            "cmd-spec-v03-router-decision-v1", self.router_name, case.case_id, selected,
            tuple(sorted(candidate_ids)), prediction.prediction_sha256, before,
            selection.selection_id, getattr(selection, "selection_mode", "hierarchy"), selection.scores,
        )
