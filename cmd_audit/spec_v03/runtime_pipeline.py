"""Closed runtime wiring from structural telemetry to the existing Stage 5 router."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Mapping, Sequence

from cmd_audit.repair.ghost_ecology import (
    EcologySelection,
    FailureDeposit,
    GHOSTEcologyRouter,
    ObservableResidualGHOSTRouter,
    ObservableResidualSelection,
    PatternResponsibility,
    PatternRevision,
    RegistrySnapshot,
    SkillRevision,
)

from .contracts import DecisionView, canonical_sha256
from .repair_stream import MemoryState, OperatorSpec, execute_operator, operator_catalog
from .router_stage5 import BackbonePrediction
from .syndrome_runtime import RuntimeSyndrome, decode_ecc_syndrome


@dataclass(frozen=True)
class RuntimeSkillContent:
    """Portable operator program; deliberately free of posterior evidence."""

    operator_id: str
    program: Mapping[str, object]
    preconditions: tuple[Mapping[str, object], ...]
    rollback_program: Mapping[str, object]
    content_sha256: str


@dataclass(frozen=True)
class RuntimeSkillEvidence:
    """Non-portable routing evidence, kept outside skill content."""

    skill_revision_id: str
    state_root: str
    evidence_sha256: str


@dataclass(frozen=True)
class CandidateBuildLog:
    state_root: str
    syndrome_id: str | None
    legal_operator_ids: tuple[str, ...]
    rejected_operator_reasons: tuple[tuple[str, str], ...]
    skill_revision_ids: tuple[str, ...]
    skill_content: tuple[RuntimeSkillContent, ...]
    evidence: tuple[RuntimeSkillEvidence, ...]


@dataclass(frozen=True)
class PipelineDecision:
    case_id: str
    syndrome: RuntimeSyndrome
    candidates: CandidateBuildLog
    selected_skill_revision_id: str | None
    selected_operator_id: str | None
    selected_skill_content: RuntimeSkillContent | None
    selection_handle: EcologySelection | ObservableResidualSelection | None
    backbone_prediction: BackbonePrediction | None
    prediction_source: str | None
    router_log: "RuntimeRouterDecisionLog" | None
    router_state_after_sha256: str | None
    abstained: bool
    abstain_reason: str | None
    failure_deposit: FailureDeposit | None = None

    def executor_dispatch(self) -> "SelectedSkillDispatch | None":
        """Return the sole skill payload available to an LLM or executor."""
        if self.selected_skill_content is None or self.selection_handle is None:
            return None
        return SelectedSkillDispatch(
            self.selection_handle.selection_id,
            self.selected_skill_revision_id,
            self.selected_operator_id,
            self.selected_skill_content,
        )


@dataclass(frozen=True)
class SelectedSkillDispatch:
    """Execution-only view: no posterior, candidate evidence, or scores."""

    selection_id: str
    skill_revision_id: str | None
    operator_id: str | None
    skill_content: RuntimeSkillContent


@dataclass(frozen=True)
class RuntimeRouterDecisionLog:
    """Serving-only decision record with its own closed schema version."""

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
    prediction_source: str


def _content(spec: OperatorSpec, skill: SkillRevision | None = None) -> RuntimeSkillContent:
    """Expose a typed operator program while keeping revisions routable."""
    program = dict(skill.program) if skill is not None else {
        "kind": "cmd-spec-v03-operator", "operator_id": spec.operator_id,
        "write_contract": spec.write_contract,
    }
    preconditions = skill.preconditions if skill is not None else ({"kind": "typed_state", "predicate": spec.precondition},)
    rollback = dict(skill.rollback_program) if skill is not None else {"action": spec.rollback_action}
    return RuntimeSkillContent(spec.operator_id, program, preconditions, rollback, canonical_sha256({"program": program, "preconditions": preconditions, "rollback": rollback}))


def _is_state_legal(state: MemoryState, spec: OperatorSpec) -> bool:
    """The candidate mask has exactly one authority: executable state preconditions."""
    if spec.operator_id == "noop_abstain":
        return True
    after = execute_operator(state, spec)
    return after != state and after.immutable_source_log == state.immutable_source_log and after.audit_log == state.audit_log


@lru_cache(maxsize=1)
def _frozen_skill_library() -> tuple[SkillRevision, ...]:
    """One content-addressed stable revision per catalog operator."""
    return tuple(
        _to_skill(_content(spec))
        for spec in operator_catalog()
    )


@lru_cache(maxsize=1)
def _frozen_patterns() -> tuple[tuple[str, PatternRevision], ...]:
    return tuple(
        (
            mechanism,
            PatternRevision.create(
                pattern_id=f"cmd-spec-v03-runtime:{mechanism}",
                predicate={"kind": "ecc-mechanism", "mechanism": mechanism},
                feature_signature=(f"mechanism:{mechanism}",),
                derivation_kind="seed",
                state="stable",
            ),
        )
        for mechanism in ("process_fault", "state_drift", "adversarial_poison")
    )


@lru_cache(maxsize=1)
def _frozen_registry() -> RegistrySnapshot:
    return _registry_for(_frozen_skill_library())


def _registry_for(library: Sequence[SkillRevision]) -> RegistrySnapshot:
    """Registry membership is revision-level, including sibling revisions."""
    default_library = _frozen_skill_library()
    config = {"runtime": "cmd-spec-v03-frozen-library-v1"}
    if tuple(library) != default_library:
        config["stable_skill_revision_ids"] = tuple(
            sorted(skill.skill_revision_id for skill in library)
        )
    return RegistrySnapshot.create(
        epoch=0,
        stable_pattern_revision_ids=tuple(
            pattern.pattern_revision_id for _mechanism, pattern in _frozen_patterns()
        ),
        stable_skill_revision_ids=tuple(
            skill.skill_revision_id for skill in library
        ),
        config_sha256=canonical_sha256(config),
    )


def _pattern_for(mechanism: str) -> PatternRevision:
    return dict(_frozen_patterns())[mechanism]


def _validated_library(skill_library: Sequence[SkillRevision] | None) -> tuple[SkillRevision, ...]:
    """Bind every imported revision to one catalog operator without collapsing siblings."""
    library = _frozen_skill_library() if skill_library is None else tuple(skill_library)
    if not library:
        raise ValueError("frozen skill library must be non-empty")
    ids = tuple(skill.skill_revision_id for skill in library)
    if len(set(ids)) != len(ids):
        raise ValueError("frozen skill library has duplicate revision IDs")
    catalog_ids = {spec.operator_id for spec in operator_catalog()}
    for skill in library:
        operator_id = skill.program.get("operator_id")
        if not isinstance(operator_id, str) or operator_id not in catalog_ids:
            raise ValueError("frozen skill program must bind a catalog operator_id")
    return library


def build_legal_candidates(
    state: MemoryState,
    syndrome: RuntimeSyndrome,
    eligible_skill_revision_ids: Sequence[str] | None = None,
    *,
    skill_library: Sequence[SkillRevision] | None = None,
) -> CandidateBuildLog:
    """Build model-independent legal candidates without evaluator incident data."""
    if syndrome.abstains:
        return CandidateBuildLog(state.root, None, (), (), (), (), ())
    library = _validated_library(skill_library)
    by_operator: dict[str, list[SkillRevision]] = {}
    for skill in library:
        by_operator.setdefault(str(skill.program["operator_id"]), []).append(skill)
    eligible = None if eligible_skill_revision_ids is None else set(eligible_skill_revision_ids)
    if eligible is not None:
        unknown = eligible - {skill.skill_revision_id for skill in library}
        if unknown:
            raise ValueError("eligible skill mask contains an unknown frozen revision")
    legal: list[tuple[OperatorSpec, SkillRevision]] = []
    rejected: list[tuple[str, str]] = []
    for spec in operator_catalog():
        if spec.operator_id == "noop_abstain":
            continue
        if not _is_state_legal(state, spec):
            rejected.append((spec.operator_id, "typed-state-precondition-failed"))
            continue
        for skill in by_operator.get(spec.operator_id, ()):
            if eligible is not None and skill.skill_revision_id not in eligible:
                rejected.append((spec.operator_id, "lifecycle-ineligible"))
                continue
            legal.append((spec, skill))
    contents = tuple(_content(spec, skill) for spec, skill in legal)
    skills = tuple(skill for _spec, skill in legal)
    evidence = tuple(
        RuntimeSkillEvidence(
            skill.skill_revision_id,
            state.root,
            canonical_sha256({"skill_revision_id": skill.skill_revision_id, "state_root": state.root}),
        )
        for skill in skills
    )
    return CandidateBuildLog(
        state.root,
        syndrome.ecc_syndrome.syndrome_id,
        tuple(spec.operator_id for spec, _skill in legal),
        tuple(rejected),
        tuple(skill.skill_revision_id for skill in skills),
        contents,
        evidence,
    )


def _to_skill(content: RuntimeSkillContent) -> SkillRevision:
    return SkillRevision.create(
        skill_id=f"runtime:{content.operator_id}", program=dict(content.program), parameter_schema={"type": "object", "additionalProperties": False},
        preconditions=content.preconditions, postconditions=(), success_probe={"probe_id": f"runtime-structural:{content.operator_id}"},
        mutation_budget={"locality": "operator-contract"}, rollback_program=dict(content.rollback_program),
        producing_failure_id="cmd-spec-v03-runtime-seed-library-v1",
        derivation_kind="seed",
        state="stable",
    )


class RuntimePipeline:
    """A fail-closed serving adapter; it never accepts evaluator sidecars."""

    def __init__(self, *, router_name: str = "mix_ghost", router: ObservableResidualGHOSTRouter | GHOSTEcologyRouter | None = None, model_id: str = "unconfigured", skill_library: Sequence[SkillRevision] | None = None) -> None:
        if router is None:
            router = ObservableResidualGHOSTRouter(allow_development_proxy=True) if router_name == "mix_ghost" else GHOSTEcologyRouter(allow_development_proxy=True)
        self.router_name = router_name
        self.router = router
        self.model_id = model_id
        self._skill_library = _validated_library(skill_library)

    @property
    def frozen_skill_library(self) -> tuple[SkillRevision, ...]:
        """Read-only coordinator view of the sealed runtime skill revisions."""
        return self._skill_library

    @property
    def frozen_registry(self) -> RegistrySnapshot:
        """Read-only coordinator view of the registry matching the library."""
        return _registry_for(self._skill_library)

    def frozen_skill(self, skill_revision_id: str) -> SkillRevision:
        """Look up one frozen revision without exposing it to executor dispatch."""
        for skill in self._skill_library:
            if skill.skill_revision_id == skill_revision_id:
                return skill
        raise KeyError(skill_revision_id)

    def decide(
        self,
        decision: DecisionView,
        state: MemoryState,
        prediction: BackbonePrediction | None = None,
        *,
        development_zero_backbone: bool = False,
        eligible_skill_revision_ids: Sequence[str] | None = None,
    ) -> PipelineDecision:
        syndrome = decode_ecc_syndrome(decision, state)
        candidates = build_legal_candidates(state, syndrome, eligible_skill_revision_ids, skill_library=self._skill_library)
        if syndrome.abstains:
            return PipelineDecision(
                case_id=decision.case_id,
                syndrome=syndrome,
                candidates=candidates,
                selected_skill_revision_id=None,
                selected_operator_id=None,
                selected_skill_content=None,
                selection_handle=None,
                backbone_prediction=None,
                prediction_source=None,
                router_log=None,
                router_state_after_sha256=None,
                abstained=True,
                abstain_reason=syndrome.descriptor.classification,
            )
        if not candidates.legal_operator_ids:
            return PipelineDecision(
                case_id=decision.case_id,
                syndrome=syndrome,
                candidates=candidates,
                selected_skill_revision_id=None,
                selected_operator_id=None,
                selected_skill_content=None,
                selection_handle=None,
                backbone_prediction=None,
                prediction_source=None,
                router_log=None,
                router_state_after_sha256=None,
                abstained=True,
                abstain_reason="no-typed-legal-candidate",
            )
        library = {skill.skill_revision_id: skill for skill in self._skill_library}
        skills = tuple(library[skill_id] for skill_id in candidates.skill_revision_ids)
        pattern = _pattern_for(syndrome.ecc_syndrome.mechanism.value)
        responsibilities = (PatternResponsibility(pattern.pattern_revision_id, 1.0),)
        registry = self.frozen_registry
        if prediction is not None and development_zero_backbone:
            raise ValueError("development zero backbone cannot replace a supplied prediction")
        if prediction is None:
            if not development_zero_backbone:
                raise ValueError("runtime routing requires an externally bound backbone prediction")
            scores = {skill.skill_revision_id: 0.0 for skill in skills}
            prediction = BackbonePrediction.create(
                case_id=decision.case_id,
                event_index=decision.event_index,
                model_id=self.model_id,
                candidate_skill_revision_ids=tuple(scores),
                scores=scores,
                selected_skill_revision_id=min(scores),
                backbone_state_sha256=state.root,
            )
            prediction_source = "development_zero_backbone"
        else:
            prediction_source = "external_backbone"
        prediction.verify(tuple(skill.skill_revision_id for skill in skills), decision)
        if prediction.model_id != self.model_id:
            raise ValueError("backbone prediction model_id does not match runtime pipeline")
        if prediction.backbone_state_sha256 != state.root:
            raise ValueError("backbone prediction is not bound to the runtime state root")
        failure = FailureDeposit(
            "runtime-failure-" + syndrome.ecc_syndrome.syndrome_id,
            decision.case_id,
            decision.family_id,
            syndrome.ecc_syndrome.content_hash,
            (("confidence", syndrome.descriptor.confidence),),
            state.root,
            decision.content_sha256,
        )
        before = str(self.router.snapshot["snapshot_sha256"])
        if self.router_name == "mix_ghost":
            selection = self.router.select(
                failure,
                pattern_responsibilities=responsibilities,
                skills=skills,
                registry=registry,
                event_index=decision.event_index,
                base_scores=prediction.scores,
                base_selected_skill_revision_id=prediction.selected_skill_revision_id,
            )
        else:
            selection = self.router.select(
                failure,
                pattern_responsibilities=responsibilities,
                skills=skills,
                registry=registry,
                event_index=decision.event_index,
                skill_priors=prediction.scores,
            )
        after = str(self.router.snapshot["snapshot_sha256"])
        if before != after:
            raise RuntimeError("route-only runtime path changed router posterior state")
        selected_id = selection.selected_skill_revision_id
        if selected_id is None:
            raise RuntimeError("runtime router returned no action for non-empty candidates")
        selected_skill = library[selected_id]
        selected_operator_id = str(selected_skill.program["operator_id"])
        selected_content = next(
            content
            for skill_id, content in zip(candidates.skill_revision_ids, candidates.skill_content)
            if skill_id == selected_id
        )
        log = RuntimeRouterDecisionLog(
            "cmd-spec-v03-runtime-router-decision-v1",
            self.router_name,
            decision.case_id,
            selected_id,
            tuple(sorted(candidates.skill_revision_ids)),
            prediction.prediction_sha256,
            before,
            selection.selection_id,
            getattr(selection, "selection_mode", "hierarchy"),
            selection.scores,
            prediction_source,
        )
        return PipelineDecision(
            decision.case_id,
            syndrome,
            candidates,
            selected_id,
            selected_operator_id,
            selected_content,
            selection,
            prediction,
            prediction_source,
            log,
            after,
            False,
            None,
            failure,
        )
