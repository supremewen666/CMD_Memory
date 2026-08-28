"""Isolated, replayable executor for the eight Stage 5 routing arms.

The executor deliberately has no evaluator-side dependency.  It routes a
closed :class:`RuntimeBundle` with the structural syndrome and legal-skill
builder used by serving, while delayed feedback arrives through an explicit
provider.  In particular, ``oracle_legal`` is unavailable unless a sealed
oracle capability is injected by the caller.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math
import random
from typing import Mapping, Protocol, Sequence

from cmd_audit.repair.ghost_ecology import (
    DelayedOutcomeFeedback,
    FailureDeposit,
    GHOSTEcologyRouter,
    ObservableResidualGHOSTRouter,
    PatternResponsibility,
    SkillRevision,
)

from .backbone_provider import BackboneProvider, ResourceUsage
from .contracts import DecisionView, canonical_sha256
from .experiment_matrix import STAGE5_VARIANTS
from .prequential_executor import RuntimeOrderManifest
from .repair_stream import (
    MemoryState,
    OperatorSpec,
    execute_operator,
    operator_catalog,
    operator_collateral_cost,
    operator_locality,
)
from .repair_utility import score_repair_utility
from .router_stage5 import BackbonePrediction
from .runtime_bundle import RuntimeBundle
from .runtime_pipeline import RuntimePipeline, build_legal_candidates
from .syndrome_runtime import audit_structural_telemetry, decode_ecc_syndrome


STAGE5_EXECUTOR_SCHEMA = "cmd-spec-v03-stage5-executor-v1"
_ADAPTIVE_ARMS = frozenset({"best_global", "global_thompson", "niche_thompson", "contextual_bandit"})
_ROUTER_ARMS = frozenset({"mix_ghost", "ghost_hierarchy"})
ROUTER_SNAPSHOT_BUNDLE_SCHEMA = "cmd-spec-v03-stage5-router-snapshots-v1"


@dataclass(frozen=True)
class Stage5Receipt:
    """Selected-action-only delayed observation supplied after its maturity."""

    selection_id: str
    selected_skill_revision_id: str
    selected_at_event_index: int
    observed_after_event_index: int
    utility: float
    valid: bool = True
    rolled_back: bool = False
    delayed_regression: bool = False
    provenance: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not self.selection_id or not self.selected_skill_revision_id:
            raise ValueError("Stage 5 receipt requires selection and skill IDs")
        if self.observed_after_event_index <= self.selected_at_event_index:
            raise ValueError("Stage 5 receipt must mature after its selection")
        if not math.isfinite(self.utility) or not -1.0 <= self.utility <= 1.0:
            raise ValueError("Stage 5 receipt utility must be finite in [-1, 1]")

    @property
    def outcome(self) -> float:
        return -1.0 if (not self.valid or self.rolled_back or self.delayed_regression) else self.utility

    @property
    def receipt_sha256(self) -> str:
        return canonical_sha256(asdict(self))


class DelayedFeedbackProvider(Protocol):
    """Serving-side observation boundary; it cannot select an unserved skill."""

    def observe(
        self,
        *,
        selection_id: str,
        selected_skill_revision_id: str,
        selected_at_event_index: int,
        observed_after_event_index: int,
        case: RuntimeBundle,
        regime: str = "stationary",
    ) -> Stage5Receipt: ...


@dataclass(frozen=True)
class StructuralDevelopmentStage5FeedbackProvider:
    """Development-only structural feedback for the selected frozen skill.

    This is deliberately a closed runtime replay, not a proxy for evaluator
    success.  It receives exactly one selected revision and one RuntimeBundle,
    executes that revision's typed operator against a copy-on-write state, and
    returns only structural gate evidence at the scheduled maturity.  It must
    never be used for confirmatory runs.
    """

    model_id: str
    mode: str = "DEVELOPMENT_STRUCTURAL_ONLY"
    development_only: bool = True
    skill_library: tuple[SkillRevision, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not self.model_id:
            raise ValueError("structural Stage 5 feedback requires a model ID")
        if self.mode != "DEVELOPMENT_STRUCTURAL_ONLY" or self.development_only is not True:
            raise ValueError("structural Stage 5 feedback is development-only")

    def _operator(self, selected_skill_revision_id: str) -> OperatorSpec | None:
        """Resolve one selected frozen typed program without candidate look-up."""
        try:
            skill = RuntimePipeline(skill_library=self.skill_library).frozen_skill(selected_skill_revision_id)
        except KeyError:
            return None
        operator_id = skill.program.get("operator_id")
        if not isinstance(operator_id, str):
            return None
        return next((spec for spec in operator_catalog() if spec.operator_id == operator_id), None)

    @staticmethod
    def _rebased_decision(decision: DecisionView, state: MemoryState) -> DecisionView:
        """Bind the public observation to the shadow state before telemetry."""
        observation = dict(decision.observation)
        current = dict(observation["current_state"])
        current.update({
            "projection_order": list(state.projection_order),
            "projection_index": list(state.projection_index),
            "scope_projection": list(state.scope_projection),
            "cache_event_ids": list(state.cache_event_ids),
            "supersession_edges": list(state.supersession_edges),
            "quarantine_set": list(state.quarantine_set),
            "state_root": state.root,
        })
        observation["current_state"] = current
        return replace(decision, observation=observation)

    def observe(
        self,
        *,
        selection_id: str,
        selected_skill_revision_id: str,
        selected_at_event_index: int,
        observed_after_event_index: int,
        case: RuntimeBundle,
        regime: str = "stationary",
    ) -> Stage5Receipt:
        """Replay only the action that was actually selected at route time."""
        before = case.memory_state
        spec = self._operator(selected_skill_revision_id)
        legal_ids: tuple[str, ...] = ()
        before_classification = "unavailable"
        after_classification = "unavailable"
        shadow = before
        if spec is not None:
            decision = replace(case.decision_view, event_index=selected_at_event_index)
            syndrome = decode_ecc_syndrome(decision, before)
            before_classification = syndrome.descriptor.classification
            legal_ids = build_legal_candidates(before, syndrome, skill_library=self.skill_library).skill_revision_ids
            if selected_skill_revision_id in legal_ids:
                shadow = execute_operator(before.clone(), spec)
                after_classification = audit_structural_telemetry(
                    self._rebased_decision(decision, shadow), shadow,
                ).classification

        immutable_log_preserved = shadow.immutable_source_log == before.immutable_source_log
        audit_log_preserved = shadow.audit_log == before.audit_log
        active_untrusted = any(
            event.actor_scope == "untrusted" and event.event_id in shadow.projection_order
            for event in shadow.audit_log
        )
        audit_ids = {event.event_id for event in shadow.audit_log}
        safety_passed = immutable_log_preserved and audit_log_preserved and not active_untrusted and set(shadow.quarantine_set) <= audit_ids
        locality_cost = None if spec is None else operator_locality(before, shadow, spec)
        collateral_cost = None if spec is None else operator_collateral_cost(before, shadow, spec)
        locality_passed = locality_cost is not None and locality_cost <= spec.locality_bound
        changed = shadow.root != before.root
        resolved = after_classification == "clean"
        robust_strategies = {"rebuild", "cascade", "quarantine_and_rebuild"}
        recurrence_after_commit = "abrupt" in regime.casefold() and bool(
            spec is not None and spec.strategy_id not in robust_strategies
        )
        latency_cost = min(1.0, max(0, observed_after_event_index - selected_at_event_index - 1) / 10.0)
        valid = bool(
            spec is not None
            and selected_skill_revision_id in legal_ids
            and changed
            and immutable_log_preserved
            and audit_log_preserved
            and safety_passed
            and locality_passed
            and resolved
        )
        provenance = {
            "provider": "StructuralDevelopmentStage5FeedbackProvider",
            "mode": self.mode,
            "model_id": self.model_id,
            "selected_skill_revision_id": selected_skill_revision_id,
            "before_root": before.root,
            "shadow_root": shadow.root,
            "immutable_log_preserved": immutable_log_preserved,
            "audit_log_preserved": audit_log_preserved,
            "safety_passed": safety_passed,
            "locality_cost": locality_cost,
            "collateral_cost": collateral_cost,
            "locality_passed": locality_passed,
            "operator_family": None if spec is None else spec.operator_family,
            "strategy_id": None if spec is None else spec.strategy_id,
            "expected_cost": None if spec is None else spec.expected_cost,
            "regime": regime,
            "recurrence_after_commit": recurrence_after_commit,
            "latency_cost": latency_cost,
            "before_structural_syndrome": before_classification,
            "after_structural_syndrome": after_classification,
            "structural_resolution": resolved,
        }
        return Stage5Receipt(
            selection_id=selection_id,
            selected_skill_revision_id=selected_skill_revision_id,
            selected_at_event_index=selected_at_event_index,
            observed_after_event_index=observed_after_event_index,
            utility=score_repair_utility(
                committed=valid,
                safety_passed=safety_passed,
                invariant_passed=immutable_log_preserved and audit_log_preserved and resolved,
                locality_cost=0 if locality_cost is None else locality_cost,
                locality_bound=0 if spec is None else spec.locality_bound,
                collateral_cost=0.0 if collateral_cost is None else collateral_cost,
                expected_cost=0.0 if spec is None else spec.expected_cost,
                latency_cost=latency_cost,
                recurrence_after_commit=recurrence_after_commit,
                rolled_back=not valid,
            ),
            valid=valid,
            rolled_back=not valid,
            delayed_regression=not resolved,
            provenance=provenance,
        )


class SealedOracleProvider(Protocol):
    """Evaluator-owned legal-action capability, intentionally not optional by accident."""

    sealed: bool

    def select_legal(
        self,
        *,
        case_id: str,
        event_index: int,
        candidate_skill_revision_ids: tuple[str, ...],
    ) -> str: ...


@dataclass(frozen=True)
class Stage5ExecutionConfig:
    run_id: str
    model_id: str
    seed: int
    best_global_calibration_prior: Mapping[str, float] | None = None
    algorithm_version: str = "stage5-isolation-v1"
    schema_version: str = STAGE5_EXECUTOR_SCHEMA
    execution_mode: str = "DEVELOPMENT"
    initial_router_snapshots: Mapping[str, Mapping[str, object]] | None = None
    adaptation_prefix_ratio: float = 0.0
    arms: tuple[str, ...] = STAGE5_VARIANTS

    def __post_init__(self) -> None:
        if not self.run_id or not self.model_id:
            raise ValueError("Stage 5 config requires run and model IDs")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("Stage 5 seed must be an integer")
        if self.execution_mode not in {"DEVELOPMENT", "CONFIRMATORY"}:
            raise ValueError("Stage 5 execution mode must be DEVELOPMENT or CONFIRMATORY")
        if self.best_global_calibration_prior is not None:
            for skill_id, value in self.best_global_calibration_prior.items():
                if not isinstance(skill_id, str) or not skill_id or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    raise ValueError("best_global calibration prior must have finite named scores")
        if self.schema_version != STAGE5_EXECUTOR_SCHEMA:
            raise ValueError("unsupported Stage 5 executor schema")
        if not isinstance(self.adaptation_prefix_ratio, (int, float)) or isinstance(self.adaptation_prefix_ratio, bool) or not math.isfinite(float(self.adaptation_prefix_ratio)):
            raise ValueError("adaptation prefix ratio must be finite")
        if not 0.0 <= float(self.adaptation_prefix_ratio) < 1.0:
            raise ValueError("adaptation prefix ratio must be in [0, 1)")
        if self.initial_router_snapshots is not None:
            if not set(self.initial_router_snapshots) <= _ROUTER_ARMS:
                raise ValueError("initial router snapshots are supported only for GHOST arms")
            for arm, snapshot in self.initial_router_snapshots.items():
                if not isinstance(arm, str) or not isinstance(snapshot, Mapping):
                    raise ValueError("initial router snapshots must map a GHOST arm to one snapshot")
        if not self.arms or len(set(self.arms)) != len(self.arms) or not set(self.arms) <= set(STAGE5_VARIANTS):
            raise ValueError("Stage 5 arms must be a unique non-empty supported subset")

    @property
    def config_sha256(self) -> str:
        return canonical_sha256(asdict(self))


@dataclass(frozen=True)
class Stage5SelectionRecord:
    arm: str
    event_index: int
    case_id: str
    candidate_skill_revision_ids: tuple[str, ...]
    selected_skill_revision_id: str | None
    backbone_prediction_sha256: str | None
    backbone_scores: tuple[tuple[str, float], ...]
    selected_at_event_index: int | None
    observed_after_event_index: int | None
    selection_id: str | None
    selection_mode: str
    router_snapshot_before_sha256: str
    router_snapshot_after_sha256: str
    algorithm_snapshot_sha256: str
    abstain_reason: str | None
    record_sha256: str


@dataclass(frozen=True)
class Stage5ReceiptRecord:
    arm: str
    receipt_sha256: str
    selection_id: str
    selected_skill_revision_id: str
    selected_at_event_index: int
    observed_after_event_index: int
    utility: float
    settled_before_event_index: int
    posterior_before_sha256: str
    posterior_after_sha256: str


@dataclass(frozen=True)
class Stage5ArmReport:
    arm: str
    status: str
    selection_records: tuple[Stage5SelectionRecord, ...]
    receipt_records: tuple[Stage5ReceiptRecord, ...]
    censored_selection_ids: tuple[str, ...]
    algorithm_snapshot: Mapping[str, object]
    algorithm_snapshot_sha256: str
    resource_usage: ResourceUsage
    adaptation_prefix_event_count: int = 0
    scored_suffix_event_count: int = 0
    imported_router_snapshot: bool = False


@dataclass(frozen=True)
class Stage5ExecutionReport:
    schema_version: str
    config_sha256: str
    order_manifest_sha256: str
    backbone_prediction_sha256s: tuple[str, ...]
    arms: tuple[Stage5ArmReport, ...]
    resource_usage: ResourceUsage
    report_sha256: str


@dataclass
class _Pending:
    case: RuntimeBundle
    selection_id: str
    skill_id: str
    selected_at: int
    matures_at: int
    prediction: BackbonePrediction
    router_selection: object | None
    router_kind: str
    context: tuple[float, ...]
    regime: str


class _AdaptivePolicy:
    """Closed, tiny statistical policies for the non-GHOST Stage 5 arms."""

    def __init__(self, arm: str, seed: int, calibration_prior: Mapping[str, float] | None = None) -> None:
        self.arm, self.seed = arm, seed
        self.calibration_prior = {str(key): float(value) for key, value in (calibration_prior or {}).items()}
        self._beta: dict[str, tuple[float, float]] = {}
        self._linear: dict[str, tuple[tuple[float, ...], tuple[float, ...]]] = {}

    def _rng(self, event_index: int, skill_id: str) -> random.Random:
        address = canonical_sha256({"seed": self.seed, "arm": self.arm, "event": event_index, "skill": skill_id})
        return random.Random(int(address, 16))

    def _key(self, skill_id: str, niche: str) -> str:
        return f"{niche}:{skill_id}" if self.arm == "niche_thompson" else skill_id

    def choose(self, candidates: tuple[str, ...], prediction: BackbonePrediction, context: tuple[float, ...], niche: str) -> tuple[str, str]:
        if self.arm == "best_global":
            # No calibration prior means a frozen uniform prior and lexical
            # tie-break; test-stream receipts never alter this arm.
            scores = {skill: self.calibration_prior.get(skill, 0.0) for skill in candidates}
            return min(candidates, key=lambda skill: (-scores[skill], skill)), "best_global"
        if self.arm in {"global_thompson", "niche_thompson"}:
            draws = {
                skill: self._rng(prediction.event_index, self._key(skill, niche)).betavariate(*self._beta.get(self._key(skill, niche), (1.0, 1.0)))
                for skill in candidates
            }
            return min(candidates, key=lambda skill: (-draws[skill], skill)), "beta_thompson"
        if self.arm == "contextual_bandit":
            values: dict[str, float] = {}
            for skill in candidates:
                diagonal, vector = self._linear.get(skill, ((1.0,) * len(context), (0.0,) * len(context)))
                mean = sum((vector[i] / diagonal[i]) * context[i] for i in range(len(context)))
                bonus = sum(abs(context[i]) / math.sqrt(diagonal[i]) for i in range(len(context)))
                values[skill] = mean + 0.35 * bonus
            return min(candidates, key=lambda skill: (-values[skill], skill)), "linucb"
        raise ValueError("unsupported adaptive policy")

    def observe(self, skill_id: str, outcome: float, context: tuple[float, ...], niche: str) -> None:
        if self.arm in {"global_thompson", "niche_thompson"}:
            key = self._key(skill_id, niche)
            alpha, beta = self._beta.get(key, (1.0, 1.0))
            self._beta[key] = (alpha + max(0.0, outcome), beta + max(0.0, -outcome))
        elif self.arm == "contextual_bandit":
            diagonal, vector = self._linear.get(skill_id, ((1.0,) * len(context), (0.0,) * len(context)))
            self._linear[skill_id] = (
                tuple(diagonal[i] + context[i] * context[i] for i in range(len(context))),
                tuple(vector[i] + outcome * context[i] for i in range(len(context))),
            )

    @property
    def snapshot(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": "cmd-spec-v03-stage5-policy-v1", "arm": self.arm, "seed": self.seed,
            "best_global_calibration_prior": [[skill, score] for skill, score in sorted(self.calibration_prior.items())],
            "beta": [[skill, *values] for skill, values in sorted(self._beta.items())],
            "linear": [[skill, list(diagonal), list(vector)] for skill, (diagonal, vector) in sorted(self._linear.items())],
        }
        return {**payload, "snapshot_sha256": canonical_sha256(payload)}


def router_snapshot_bundle_from_stage5_result(
    stage5_result: Mapping[str, object],
    *,
    source_model_id: str,
) -> dict[str, object]:
    """Extract the two portable GHOST posteriors from an existing Stage 5 report."""
    if not isinstance(source_model_id, str) or not source_model_id:
        raise ValueError("router snapshot bundle requires a source model ID")
    raw_arms = stage5_result.get("arms")
    if not isinstance(raw_arms, Sequence) or isinstance(raw_arms, (str, bytes)):
        raise ValueError("Stage 5 result does not contain arm reports")
    snapshots: dict[str, Mapping[str, object]] = {}
    for raw_arm in raw_arms:
        if not isinstance(raw_arm, Mapping):
            raise ValueError("Stage 5 arm report must be an object")
        arm = raw_arm.get("arm")
        if arm not in _ROUTER_ARMS:
            continue
        if raw_arm.get("status") != "COMPLETE":
            continue
        snapshot = raw_arm.get("algorithm_snapshot")
        if not isinstance(snapshot, Mapping):
            raise ValueError(f"Stage 5 {arm} report has no router snapshot")
        snapshots[str(arm)] = dict(snapshot)
    if not snapshots:
        raise ValueError("Stage 5 result has no complete GHOST router snapshots")
    return {
        "schema_version": ROUTER_SNAPSHOT_BUNDLE_SCHEMA,
        "source_model_id": source_model_id,
        "router_snapshots": snapshots,
    }


def load_router_snapshot_bundle(value: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    """Validate the small portable envelope before a target router imports it."""
    required = {"schema_version", "source_model_id", "router_snapshots"}
    if set(value) != required or value.get("schema_version") != ROUTER_SNAPSHOT_BUNDLE_SCHEMA:
        raise ValueError("unsupported router snapshot bundle")
    if not isinstance(value["source_model_id"], str) or not value["source_model_id"]:
        raise ValueError("router snapshot bundle requires a source model ID")
    raw_snapshots = value["router_snapshots"]
    if not isinstance(raw_snapshots, Mapping) or not raw_snapshots or not set(raw_snapshots) <= _ROUTER_ARMS:
        raise ValueError("router snapshot bundle must contain supported GHOST arms")
    snapshots: dict[str, Mapping[str, object]] = {}
    for arm, snapshot in raw_snapshots.items():
        if not isinstance(arm, str) or not isinstance(snapshot, Mapping):
            raise ValueError("router snapshot bundle contains an invalid snapshot")
        snapshots[arm] = dict(snapshot)
    return snapshots


class Stage5Executor:
    """Run all Stage 5 arms over a single bundle/order/backbone realization."""

    def __init__(
        self,
        config: Stage5ExecutionConfig,
        backbone_provider: BackboneProvider,
        feedback_provider: DelayedFeedbackProvider,
        *,
        sealed_oracle_provider: SealedOracleProvider | None = None,
        skill_library: Sequence[SkillRevision] | None = None,
    ) -> None:
        if config.execution_mode == "CONFIRMATORY" and isinstance(feedback_provider, StructuralDevelopmentStage5FeedbackProvider):
            raise ValueError("confirmatory Stage 5 execution cannot use structural development feedback")
        self.config = config
        self.backbone_provider = backbone_provider
        self.feedback_provider = feedback_provider
        self.sealed_oracle_provider = sealed_oracle_provider
        self.skill_library = RuntimePipeline(skill_library=skill_library).frozen_skill_library
        if isinstance(self.feedback_provider, StructuralDevelopmentStage5FeedbackProvider):
            if self.feedback_provider.skill_library is None:
                self.feedback_provider = replace(self.feedback_provider, skill_library=self.skill_library)
            elif self.feedback_provider.skill_library != self.skill_library:
                raise ValueError("structural feedback library must match the Stage 5 frozen library")

    @staticmethod
    def _context(decision: DecisionView, state: MemoryState) -> tuple[float, ...]:
        return (1.0, float(len(state.projection_order)), float(len(state.audit_log)), float(decision.event_index))

    @staticmethod
    def _failure(decision: DecisionView, syndrome_id: str, state: MemoryState) -> FailureDeposit:
        return FailureDeposit(
            "stage5-failure-" + syndrome_id, decision.case_id, decision.family_id, syndrome_id,
            (("event_index", float(decision.event_index)),), state.root, decision.content_sha256,
        )

    @staticmethod
    def _usage_delta(before: ResourceUsage, after: ResourceUsage) -> ResourceUsage:
        return ResourceUsage(
            after.input_tokens - before.input_tokens, after.output_tokens - before.output_tokens,
            after.total_tokens - before.total_tokens, after.request_count - before.request_count,
        )

    def _prediction_cache(self, bundles: Mapping[str, RuntimeBundle], order: RuntimeOrderManifest) -> dict[int, tuple[DecisionView, tuple[SkillRevision, ...], BackbonePrediction | None, str | None]]:
        cache: dict[int, tuple[DecisionView, tuple[SkillRevision, ...], BackbonePrediction | None, str | None]] = {}
        pipeline = RuntimePipeline(model_id=self.config.model_id, skill_library=self.skill_library)
        for row in order.rows:
            bundle = bundles[row.case_id]
            decision = replace(bundle.decision_view, event_index=row.event_index)
            syndrome = decode_ecc_syndrome(decision, bundle.memory_state)
            if syndrome.abstains:
                cache[row.event_index] = (decision, (), None, syndrome.descriptor.classification)
                continue
            candidates = build_legal_candidates(bundle.memory_state, syndrome, skill_library=self.skill_library)
            skills = tuple(pipeline.frozen_skill(skill_id) for skill_id in candidates.skill_revision_ids)
            if not skills:
                cache[row.event_index] = (decision, (), None, "no-typed-legal-candidate")
                continue
            prediction = self.backbone_provider.predict(decision, bundle.memory_state, skills)
            prediction.verify(tuple(skill.skill_revision_id for skill in skills), decision)
            if prediction.model_id != self.config.model_id or prediction.backbone_state_sha256 != bundle.memory_state.root:
                raise ValueError("backbone prediction is not bound to the Stage 5 runtime input")
            cache[row.event_index] = (decision, skills, prediction, None)
        return cache

    def run(self, bundles: Sequence[RuntimeBundle], order: RuntimeOrderManifest) -> Stage5ExecutionReport:
        order.verify()
        bundle_map = {bundle.case_id: bundle for bundle in bundles}
        if len(bundle_map) != len(bundles) or set(bundle_map) != {row.case_id for row in order.rows}:
            raise ValueError("Stage 5 bundles and order must be the same strict case permutation")
        usage_before = self.backbone_provider.usage
        cache = self._prediction_cache(bundle_map, order)
        prediction_hashes = tuple(
            prediction.prediction_sha256 for _decision, _skills, prediction, _reason in cache.values() if prediction is not None
        )
        shared_usage = self._usage_delta(usage_before, self.backbone_provider.usage)
        prefix_count = math.floor(len(order.rows) * float(self.config.adaptation_prefix_ratio))
        if self.config.adaptation_prefix_ratio and prefix_count == 0:
            raise ValueError("adaptation prefix ratio selects no runtime events")
        reports = tuple(
            self._run_arm(arm, bundle_map, order, cache, shared_usage, prefix_count)
            for arm in self.config.arms
        )
        total_usage = shared_usage
        body = {
            "schema_version": STAGE5_EXECUTOR_SCHEMA, "config_sha256": self.config.config_sha256,
            "order_manifest_sha256": order.source_content_sha256, "backbone_prediction_sha256s": list(prediction_hashes),
            "arms": [asdict(report) for report in reports], "resource_usage": asdict(total_usage),
        }
        return Stage5ExecutionReport(
            schema_version=STAGE5_EXECUTOR_SCHEMA,
            config_sha256=self.config.config_sha256,
            order_manifest_sha256=order.source_content_sha256,
            backbone_prediction_sha256s=prediction_hashes,
            arms=reports,
            resource_usage=total_usage,
            report_sha256=canonical_sha256(body),
        )

    def _run_arm(
        self, arm: str, bundles: Mapping[str, RuntimeBundle], order: RuntimeOrderManifest,
        cache: Mapping[int, tuple[DecisionView, tuple[SkillRevision, ...], BackbonePrediction | None, str | None]],
        shared_usage: ResourceUsage,
        prefix_count: int,
    ) -> Stage5ArmReport:
        if arm not in STAGE5_VARIANTS:
            raise ValueError("unsupported Stage 5 arm")
        if arm == "oracle_legal" and (self.sealed_oracle_provider is None or getattr(self.sealed_oracle_provider, "sealed", False) is not True):
            return Stage5ArmReport(arm, "UNSUPPORTED", (), (), (), {"reason": "sealed_oracle_provider_required"}, canonical_sha256({"arm": arm, "status": "UNSUPPORTED"}), shared_usage)
        policy = _AdaptivePolicy(arm, self.config.seed, self.config.best_global_calibration_prior) if arm in _ADAPTIVE_ARMS else None
        router, imported_router_snapshot = self._router_for_arm(arm)
        pipeline = RuntimePipeline(model_id=self.config.model_id, skill_library=self.skill_library)
        registry = pipeline.frozen_registry
        prefix_rows = order.rows[:prefix_count]
        scored_rows = order.rows[prefix_count:]
        if router is not None and prefix_rows:
            self._adapt_router_prefix(router, pipeline, registry, bundles, prefix_rows, cache, scored_rows)
        pending: list[_Pending] = []
        selections: list[Stage5SelectionRecord] = []
        receipts: list[Stage5ReceiptRecord] = []
        for row in scored_rows:
            for item in tuple(pending):
                if item.matures_at < row.event_index:
                    raise RuntimeError("Stage 5 receipt maturity was skipped by the runtime order")
                if item.matures_at == row.event_index:
                    self._settle(arm, policy, router, item, row.event_index, receipts)
                    pending.remove(item)
            decision, skills, prediction, abstain = cache[row.event_index]
            before = self._snapshot(router, policy)
            selected: str | None = None
            selection_id: str | None = None
            routed: object | None = None
            mode = arm
            if prediction is not None:
                ids = tuple(skill.skill_revision_id for skill in skills)
                if arm == "random_legal":
                    rng = random.Random(int(canonical_sha256({"seed": self.config.seed, "arm": arm, "event": row.event_index}), 16))
                    selected, mode = rng.choice(tuple(sorted(ids))), "random_legal"
                elif arm == "oracle_legal":
                    selected = self.sealed_oracle_provider.select_legal(case_id=decision.case_id, event_index=row.event_index, candidate_skill_revision_ids=tuple(sorted(ids)))  # type: ignore[union-attr]
                    if selected not in ids:
                        raise ValueError("sealed oracle selected an action outside the structural legal mask")
                    mode = "sealed_oracle"
                elif policy is not None:
                    selected, mode = policy.choose(tuple(sorted(ids)), prediction, self._context(decision, bundles[row.case_id].memory_state), decode_ecc_syndrome(decision, bundles[row.case_id].memory_state).descriptor.classification)
                else:
                    routed = self._route_router(router, pipeline, registry, decision, bundles[row.case_id], row.event_index, skills, prediction)
                    selected, selection_id, mode = routed.selected_skill_revision_id, routed.selection_id, getattr(routed, "selection_mode", arm)
                if selection_id is None:
                    selection_id = "stage5-selection-" + canonical_sha256({"arm": arm, "event": row.event_index, "case": decision.case_id, "skill": selected})
                pending.append(_Pending(bundles[row.case_id], selection_id, selected, row.event_index, row.receipt_matures_at, prediction, routed, "ghost" if router else "policy", self._context(decision, bundles[row.case_id].memory_state), row.regime))
            after = self._snapshot(router, policy)
            record_body = {
                "arm": arm, "event_index": row.event_index, "case_id": decision.case_id,
                "candidate_skill_revision_ids": tuple(sorted(skill.skill_revision_id for skill in skills)), "selected_skill_revision_id": selected,
                "backbone_prediction_sha256": None if prediction is None else prediction.prediction_sha256,
                "backbone_scores": () if prediction is None else tuple(sorted((skill, float(score)) for skill, score in prediction.scores.items())),
                "selected_at_event_index": None if selected is None else row.event_index,
                "observed_after_event_index": None if selected is None else row.receipt_matures_at,
                "selection_id": selection_id, "selection_mode": mode, "router_snapshot_before_sha256": before,
                "router_snapshot_after_sha256": after, "algorithm_snapshot_sha256": self._snapshot(router, policy), "abstain_reason": abstain,
            }
            selections.append(Stage5SelectionRecord(**record_body, record_sha256=canonical_sha256(record_body)))
        censored = tuple(item.selection_id for item in pending)
        snapshot = self._snapshot_mapping(router, policy)
        return Stage5ArmReport(
            arm, "COMPLETE", tuple(selections), tuple(receipts), censored, snapshot,
            str(snapshot["snapshot_sha256"]), shared_usage, len(prefix_rows),
            len(scored_rows), imported_router_snapshot,
        )

    def _router_for_arm(
        self, arm: str,
    ) -> tuple[ObservableResidualGHOSTRouter | GHOSTEcologyRouter | None, bool]:
        if arm not in _ROUTER_ARMS:
            return None, False
        imported = (self.config.initial_router_snapshots or {}).get(arm)
        if arm == "mix_ghost":
            router = (
                ObservableResidualGHOSTRouter.from_snapshot(imported)
                if imported is not None
                else ObservableResidualGHOSTRouter(seed=self.config.seed, allow_development_proxy=True)
            )
        else:
            router = (
                GHOSTEcologyRouter.from_snapshot(imported)
                if imported is not None
                else GHOSTEcologyRouter(seed=self.config.seed, allow_development_proxy=True)
            )
        if imported is not None:
            self._validate_imported_router_snapshot(router)
        return router, imported is not None

    def _validate_imported_router_snapshot(
        self, router: ObservableResidualGHOSTRouter | GHOSTEcologyRouter,
    ) -> None:
        skill_ids = {skill.skill_revision_id for skill in self.skill_library}
        pattern_ids = set(RuntimePipeline(model_id=self.config.model_id, skill_library=self.skill_library).frozen_registry.stable_pattern_revision_ids)
        for raw in router.snapshot["stats"]:  # from_snapshot has already validated the envelope and numeric values.
            key = tuple(str(item) for item in raw[0])  # type: ignore[index]
            if not key or key[0] not in {"global", "pattern", "local"}:
                raise ValueError("imported router snapshot contains an unsupported posterior key")
            expected_size = {"global": 2, "pattern": 3, "local": 4}[key[0]]
            if len(key) != expected_size or key[-1] not in skill_ids:
                raise ValueError("imported router snapshot is incompatible with the frozen skill library")
            if key[0] != "global" and key[1] not in pattern_ids:
                raise ValueError("imported router snapshot is incompatible with the frozen pattern registry")

    def _route_router(
        self,
        router: ObservableResidualGHOSTRouter | GHOSTEcologyRouter | None,
        pipeline: RuntimePipeline,
        registry: object,
        decision: DecisionView,
        bundle: RuntimeBundle,
        event_index: int,
        skills: tuple[SkillRevision, ...],
        prediction: BackbonePrediction,
    ) -> object:
        if router is None:
            raise RuntimeError("router selection requested for a non-router arm")
        syndrome = decode_ecc_syndrome(decision, bundle.memory_state)
        failure = self._failure(decision, syndrome.ecc_syndrome.syndrome_id, bundle.memory_state)  # type: ignore[union-attr]
        pattern_id = pipeline.frozen_registry.stable_pattern_revision_ids[
            {"process_fault": 0, "state_drift": 1, "poison": 2}[syndrome.descriptor.classification]
        ]
        responsibilities = (PatternResponsibility(pattern_id, 1.0),)
        if isinstance(router, ObservableResidualGHOSTRouter):
            return router.select(
                failure, pattern_responsibilities=responsibilities, skills=skills,
                registry=registry, event_index=event_index, base_scores=prediction.scores,
                base_selected_skill_revision_id=prediction.selected_skill_revision_id,
            )
        return router.select(
            failure, pattern_responsibilities=responsibilities, skills=skills,
            registry=registry, event_index=event_index, skill_priors=prediction.scores,
        )

    def _adapt_router_prefix(
        self,
        router: ObservableResidualGHOSTRouter | GHOSTEcologyRouter,
        pipeline: RuntimePipeline,
        registry: object,
        bundles: Mapping[str, RuntimeBundle],
        prefix_rows: Sequence[object],
        cache: Mapping[int, tuple[DecisionView, tuple[SkillRevision, ...], BackbonePrediction | None, str | None]],
        scored_rows: Sequence[object],
    ) -> None:
        if not scored_rows:
            raise ValueError("adaptation prefix must leave a scored suffix")
        suffix_start = scored_rows[0].event_index  # type: ignore[union-attr]
        pending: list[_Pending] = []
        ignored_records: list[Stage5ReceiptRecord] = []
        for row in prefix_rows:
            event_index = row.event_index  # type: ignore[union-attr]
            for item in tuple(pending):
                if item.matures_at < event_index:
                    raise RuntimeError("Stage 5 prefix receipt maturity was skipped by the runtime order")
                if item.matures_at == event_index:
                    self._settle("prefix_adaptation", None, router, item, event_index, ignored_records)
                    pending.remove(item)
            decision, skills, prediction, _abstain = cache[event_index]
            # A prefix decision whose receipt crosses into evaluation is never
            # selected: suffix observations must not train the router.
            if prediction is None or row.receipt_matures_at >= suffix_start:  # type: ignore[union-attr]
                continue
            routed = self._route_router(router, pipeline, registry, decision, bundles[row.case_id], event_index, skills, prediction)  # type: ignore[union-attr]
            pending.append(_Pending(
                bundles[row.case_id], routed.selection_id, routed.selected_skill_revision_id,
                event_index, row.receipt_matures_at, prediction, routed, "ghost",
                self._context(decision, bundles[row.case_id].memory_state),  # type: ignore[union-attr]
                row.regime,  # type: ignore[union-attr]
            ))
        if pending:
            raise RuntimeError("adaptation prefix left an unsettled router selection")

    @staticmethod
    def _snapshot(router: ObservableResidualGHOSTRouter | GHOSTEcologyRouter | None, policy: _AdaptivePolicy | None) -> str:
        return str(Stage5Executor._snapshot_mapping(router, policy)["snapshot_sha256"])

    @staticmethod
    def _snapshot_mapping(router: ObservableResidualGHOSTRouter | GHOSTEcologyRouter | None, policy: _AdaptivePolicy | None) -> Mapping[str, object]:
        if router is not None:
            return router.snapshot
        if policy is not None:
            return policy.snapshot
        payload = {"schema_version": "cmd-spec-v03-stage5-static-v1"}
        return {**payload, "snapshot_sha256": canonical_sha256(payload)}

    def _settle(self, arm: str, policy: _AdaptivePolicy | None, router: ObservableResidualGHOSTRouter | GHOSTEcologyRouter | None, item: _Pending, current_event: int, records: list[Stage5ReceiptRecord]) -> None:
        receipt = self.feedback_provider.observe(
            selection_id=item.selection_id,
            selected_skill_revision_id=item.skill_id,
            selected_at_event_index=item.selected_at,
            observed_after_event_index=item.matures_at,
            case=item.case,
            regime=item.regime,
        )
        if (receipt.selection_id, receipt.selected_skill_revision_id, receipt.selected_at_event_index, receipt.observed_after_event_index) != (item.selection_id, item.skill_id, item.selected_at, item.matures_at):
            raise ValueError("delayed feedback is not bound to the selected Stage 5 action")
        if receipt.observed_after_event_index != current_event:
            raise ValueError("Stage 5 receipt must settle exactly at its order-manifest maturity event")
        before = self._snapshot(router, policy)
        if policy is not None:
            syndrome = decode_ecc_syndrome(replace(item.case.decision_view, event_index=item.selected_at), item.case.memory_state)
            policy.observe(item.skill_id, receipt.outcome, item.context, syndrome.descriptor.classification)
        elif router is not None:
            skill = RuntimePipeline(model_id=self.config.model_id, skill_library=self.skill_library).frozen_skill(item.skill_id)
            feedback = DelayedOutcomeFeedback(item.selection_id, item.skill_id, str(skill.success_probe["probe_id"]), item.selected_at, item.matures_at, item.prediction.scores[item.skill_id], receipt.utility, receipt.valid, receipt.rolled_back, receipt.delayed_regression, "cmd-spec-v03-stage5-executor", development_proxy=True)
            router.observe(item.router_selection, feedback)  # type: ignore[arg-type]
        after = self._snapshot(router, policy)
        records.append(Stage5ReceiptRecord(arm, receipt.receipt_sha256, receipt.selection_id, receipt.selected_skill_revision_id, receipt.selected_at_event_index, receipt.observed_after_event_index, receipt.outcome, current_event, before, after))
