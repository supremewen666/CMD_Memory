#!/usr/bin/env python3
"""Arm-paired prequential evaluation for governed V4 memory evolution.

The input is a frozen, fully materialized case stream.  Candidate execution
outcomes are shadow evidence: an arm may learn only from the intent it selected
before those outcomes were exposed.  This module performs zero model calls.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
from statistics import fmean
from typing import Callable, Mapping, Sequence

from cmd_audit.counterfactual.relation_graph import FrozenRelationGraph
from cmd_audit.repair.evolution_repository import (
    EvolutionRepository,
    content_sha256,
)
from cmd_audit.repair.deployment_feedback_evaluator import (
    FrozenDeploymentEvaluator,
    pre_action_features,
)
from cmd_audit.repair.ghost_ecology import (
    DelayedOutcomeFeedback,
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
from cmd_audit.repair.neuro_symbolic_evolution import NeuroSymbolicEvolutionEngine
from cmd_audit.repair.parametric_policy import (
    OnlineRepairPolicy,
    OutcomeObservation,
    PolicyContext,
    RepairIntent,
    SelectionDecision,
    compile_intent,
)
from cmd_audit.repair.repair_chain_governance import (
    ChainAttemptInput,
    ChainGovernanceDecision,
)


LEGACY_CASE_SCHEMA_VERSION = "cmd-v4-prequential-case-v1"
CASE_SCHEMA_VERSION = "cmd-v4-prequential-case-v2-typed-evidence"
OUTCOME_SCHEMA_VERSION = "cmd-v4-candidate-outcome-v2"
REPORT_SCHEMA_VERSION = "cmd-v4-prequential-report-v1"
V4_ARMS = (
    "identity",
    "legacy_symbolic",
    "random_k",
    "global_policy",
    "hierarchical_no_chain",
    "full_v4",
    "full_v4_observable",
    "ghost_hierarchy_v1",
    "v4_observable_ghost_residual_v1",
)
_UPDATING_ARMS = frozenset(
    {"global_policy", "hierarchical_no_chain", "full_v4"}
)
_PROBE_SETS = frozenset({"represented", "unseen"})
_GHOST_FEEDBACK_MODES = frozenset({"development_proxy", "prospective_deployment"})


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{name} must be finite")
    return converted


@dataclass(frozen=True)
class V4CandidateOutcome:
    intent_id: str
    recovery_gain: float
    locality_cost: float
    changed_item_count: int
    valid: bool
    rolled_back: bool
    # v2 typed execution evidence.  ``None`` is an explicit unknown for old
    # materializations; it is never reconstructed from changed_item_count.
    actionability_mode: str | None = None
    target_binding_observed: bool | None = None
    target_match_observed: bool | None = None
    annotation_consumed: bool | None = None
    downstream_confirmation: bool | None = None
    delayed_confirmation: bool | None = None
    no_regression_observed: bool | None = None
    changed_item_ids: tuple[str, ...] | None = None
    actionability_mode_observed: str | None = None
    typed_evidence_provenance: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not self.intent_id:
            raise ValueError("candidate outcome requires intent_id")
        object.__setattr__(
            self, "recovery_gain", _finite(self.recovery_gain, "recovery_gain")
        )
        object.__setattr__(
            self, "locality_cost", _finite(self.locality_cost, "locality_cost")
        )
        if (
            isinstance(self.changed_item_count, bool)
            or not isinstance(self.changed_item_count, int)
            or self.changed_item_count < 0
        ):
            raise ValueError("changed_item_count must be a non-negative integer")
        if not isinstance(self.valid, bool) or not isinstance(self.rolled_back, bool):
            raise ValueError("valid and rolled_back must be booleans")
        for name in (
            "target_binding_observed", "target_match_observed", "annotation_consumed",
            "downstream_confirmation", "delayed_confirmation", "no_regression_observed",
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{name} must be bool or unknown")
        if self.changed_item_ids is not None:
            if tuple(sorted(set(self.changed_item_ids))) != tuple(self.changed_item_ids):
                raise ValueError("changed_item_ids must be sorted and unique")

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "V4CandidateOutcome":
        legacy = {"intent_id", "recovery_gain", "locality_cost", "changed_item_count", "valid", "rolled_back"}
        current = set(cls.__dataclass_fields__)
        if set(value) == legacy:
            return cls(**value)  # explicit v1 -> v2 unknown adaptation
        if set(value) != current:
            raise ValueError("V4CandidateOutcome mapping is not closed")
        return cls(**value)


@dataclass(frozen=True)
class V4PrequentialCase:
    case_id: str
    family_id: str
    probe_set: str
    context: PolicyContext
    graph: FrozenRelationGraph
    intents: tuple[RepairIntent, ...]
    legacy_intent_id: str
    candidate_outcomes: tuple[V4CandidateOutcome, ...]
    chain_attempts: tuple[ChainAttemptInput, ...]

    def __post_init__(self) -> None:
        if not self.case_id or not self.family_id:
            raise ValueError("case_id and family_id are required")
        if self.probe_set not in _PROBE_SETS:
            raise ValueError("probe_set must be represented or unseen")
        if self.context.case_id != self.case_id or self.graph.case_id != self.case_id:
            raise ValueError("case, context, and graph identities disagree")
        if self.context.graph_sha256 != self.graph.graph_sha256:
            raise ValueError("policy context does not bind the frozen graph")
        if not self.intents or len({row.intent_id for row in self.intents}) != len(
            self.intents
        ):
            raise ValueError("case intents must be non-empty and unique")
        intent_ids = {row.intent_id for row in self.intents}
        if self.legacy_intent_id not in intent_ids:
            raise ValueError("legacy_intent_id must identify a frozen candidate")
        outcome_ids = tuple(row.intent_id for row in self.candidate_outcomes)
        if len(set(outcome_ids)) != len(outcome_ids) or set(outcome_ids) != intent_ids:
            raise ValueError("candidate outcomes must exactly cover the frozen intents")
        for intent in self.intents:
            compile_intent(intent, graph=self.graph)
        for attempt in self.chain_attempts:
            if attempt.case_id != self.case_id or attempt.family_id != self.family_id:
                raise ValueError("chain attempt must belong to its materialized case")
            if attempt.event_index <= self.context.event_index:
                raise ValueError("chain attempt must follow pre-outcome selection")

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": CASE_SCHEMA_VERSION,
            "case_id": self.case_id,
            "family_id": self.family_id,
            "probe_set": self.probe_set,
            "context": self.context.to_mapping(),
            "graph": self.graph.as_mapping(),
            "intents": [row.to_mapping() for row in self.intents],
            "legacy_intent_id": self.legacy_intent_id,
            "candidate_outcomes": [
                row.to_mapping() for row in self.candidate_outcomes
            ],
            "chain_attempts": [asdict(row) for row in self.chain_attempts],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "V4PrequentialCase":
        expected = {
            "schema_version",
            "case_id",
            "family_id",
            "probe_set",
            "context",
            "graph",
            "intents",
            "legacy_intent_id",
            "candidate_outcomes",
            "chain_attempts",
        }
        if set(value) != expected or value.get("schema_version") not in {LEGACY_CASE_SCHEMA_VERSION, CASE_SCHEMA_VERSION}:
            raise ValueError("V4PrequentialCase mapping is not closed or versioned")
        raw_intents = value["intents"]
        raw_outcomes = value["candidate_outcomes"]
        raw_attempts = value["chain_attempts"]
        if not isinstance(raw_intents, list):
            raise ValueError("intents must be a list")
        if not isinstance(raw_outcomes, list):
            raise ValueError("candidate_outcomes must be a list")
        if not isinstance(raw_attempts, list):
            raise ValueError("chain_attempts must be a list")
        attempts: list[ChainAttemptInput] = []
        chain_fields = set(ChainAttemptInput.__dataclass_fields__)
        for item in raw_attempts:
            mapping = _mapping(item, "chain attempt")
            if set(mapping) != chain_fields:
                raise ValueError("chain attempt mapping is not closed")
            attempts.append(ChainAttemptInput(**mapping))
        return cls(
            case_id=value["case_id"],
            family_id=value["family_id"],
            probe_set=value["probe_set"],
            context=PolicyContext.from_mapping(_mapping(value["context"], "context")),
            graph=FrozenRelationGraph.from_mapping(value["graph"]),
            intents=tuple(
                RepairIntent.from_mapping(_mapping(row, "repair intent"))
                for row in raw_intents
            ),
            legacy_intent_id=value["legacy_intent_id"],
            candidate_outcomes=tuple(
                V4CandidateOutcome.from_mapping(_mapping(row, "candidate outcome"))
                for row in raw_outcomes
            ),
            chain_attempts=tuple(attempts),
        )


def _typed_runtime_feedback(
    case: V4PrequentialCase,
    intent: RepairIntent,
    outcome: V4CandidateOutcome,
) -> float | None:
    """Derive update feedback only from typed execution/follow-up evidence.

    ``recovery_gain`` is a shadow scorer output and is intentionally not part
    of this path.  Unknown delayed/target evidence abstains from updating.
    """
    if not outcome.valid or outcome.rolled_back:
        return -1.0
    edge = next(
        (row for row in case.graph.edges if row.edge_id == intent.relation_edge_id),
        None,
    )
    if edge is None:
        return None
    if intent.effect in {"replace", "demote", "suppress"}:
        if outcome.target_binding_observed is not True or outcome.target_match_observed is not True:
            return None
        success = True
    elif intent.effect == "annotate_conflict":
        if outcome.annotation_consumed is not True and outcome.downstream_confirmation is not True:
            return None
        success = True
    else:
        if outcome.delayed_confirmation is not True and outcome.no_regression_observed is not True:
            return None
        success = True
    changed = outcome.changed_item_count
    return max(-1.0, min(1.0, float(success) - outcome.locality_cost - min(1.0, 0.05 * changed)))


@dataclass(frozen=True)
class V4ArmOutcome:
    case_id: str
    family_id: str
    probe_set: str
    event_index: int
    arm_id: str
    candidate_count: int
    candidate_intent_ids: tuple[str, ...]
    selected_intent_id: str | None
    selection_reason: str
    niche_path: tuple[str, ...]
    recovery_gain: float
    locality_cost: float
    changed_item_count: int
    valid: bool
    rolled_back: bool
    utility: float
    policy_snapshot_before: str | None
    policy_snapshot_after: str | None
    update_effective_after_event_index: int | None
    species_transitions: tuple[dict[str, object], ...]
    chain_decisions: tuple[dict[str, object], ...]

    def to_mapping(self) -> dict[str, object]:
        value = asdict(self)
        value["candidate_intent_ids"] = list(self.candidate_intent_ids)
        value["niche_path"] = list(self.niche_path)
        value["species_transitions"] = list(self.species_transitions)
        value["chain_decisions"] = list(self.chain_decisions)
        return value


@dataclass(frozen=True)
class V4PrequentialRun:
    outcomes: tuple[V4ArmOutcome, ...]
    report: dict[str, object]


@dataclass(frozen=True)
class _ArmSelection:
    intent_id: str | None
    reason: str
    niche_path: tuple[str, ...]
    decision: SelectionDecision | EcologySelection | ObservableResidualSelection | None
    policy_snapshot_before: str | None


class V4PrequentialRunner:
    """Run registered isolated arms with select-all-then-update-all chronology."""

    def __init__(
        self,
        cases: Sequence[V4PrequentialCase],
        *,
        output_dir: Path,
        candidate_budget: int,
        bootstrap_samples: int = 10_000,
        bootstrap_seed: int = 24,
        safety_margin: float = -0.05,
        primary_baseline: str = "global_policy",
        locality_penalty: float = 1.0,
        change_penalty: float = 0.05,
        ghost_evaluator: FrozenDeploymentEvaluator,
        ghost_partitions: Mapping[str, str],
        ghost_feedback_mode: str = "development_proxy",
        upstream_model_calls: int = 0,
        on_arm_outcome: Callable[[dict[str, object]], None] | None = None,
        on_case_completed: Callable[[int, int, str], None] | None = None,
        on_ghost_selection: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        if not cases:
            raise ValueError("V4 prequential run requires cases")
        if candidate_budget < 1:
            raise ValueError("candidate_budget must be positive")
        if bootstrap_samples < 100:
            raise ValueError("bootstrap_samples must be at least 100")
        if primary_baseline not in set(V4_ARMS) - {"full_v4"}:
            raise ValueError("primary_baseline must be a non-full registered arm")
        self.cases = tuple(cases)
        indexes = tuple(row.context.event_index for row in self.cases)
        if indexes != tuple(sorted(indexes)) or len(set(indexes)) != len(indexes):
            raise ValueError("case selection event indexes must be strictly increasing")
        prior_boundaries = tuple(
            max(
                (
                    row.context.event_index + 1,
                    *(attempt.event_index for attempt in row.chain_attempts),
                )
            )
            for row in self.cases[:-1]
        )
        if any(
            next_index <= boundary
            for boundary, next_index in zip(prior_boundaries, indexes[1:])
        ):
            raise ValueError(
                "each next selection must follow prior outcome/chain boundaries"
            )
        if len({row.case_id for row in self.cases}) != len(self.cases):
            raise ValueError("case IDs must be unique")
        if any(len(row.intents) != candidate_budget for row in self.cases):
            raise ValueError("every case must match the frozen candidate budget")
        self.output_dir = Path(output_dir)
        self.candidate_budget = int(candidate_budget)
        self.bootstrap_samples = int(bootstrap_samples)
        self.bootstrap_seed = int(bootstrap_seed)
        self.safety_margin = float(safety_margin)
        self.primary_baseline = primary_baseline
        self.locality_penalty = _finite(locality_penalty, "locality_penalty")
        self.change_penalty = _finite(change_penalty, "change_penalty")
        if not isinstance(ghost_evaluator, FrozenDeploymentEvaluator):
            raise TypeError("ghost_hierarchy_v1 requires a frozen deployment evaluator")
        self.ghost_evaluator = ghost_evaluator
        allowed_partitions = {
            "ghost_dev", "ghost_cal", "ghost_test_rep", "ghost_test_new"
        }
        if set(ghost_partitions) != {row.case_id for row in self.cases}:
            raise ValueError("GHOST partition assignments must exactly cover cases")
        if set(ghost_partitions.values()) - allowed_partitions:
            raise ValueError("GHOST partition assignment is unregistered")
        self.ghost_partitions = dict(ghost_partitions)
        if ghost_feedback_mode not in _GHOST_FEEDBACK_MODES:
            raise ValueError("GHOST feedback mode is unregistered")
        self.ghost_feedback_mode = ghost_feedback_mode
        if (
            isinstance(upstream_model_calls, bool)
            or not isinstance(upstream_model_calls, int)
            or upstream_model_calls < 0
        ):
            raise ValueError("upstream_model_calls must be a non-negative integer")
        self.upstream_model_calls = upstream_model_calls
        self.ghost_router = GHOSTEcologyRouter(
            seed=bootstrap_seed,
            min_pattern_support=2.0,
            min_local_support=3.0,
            allow_development_proxy=ghost_feedback_mode == "development_proxy",
        )
        self._ghost_selected_priors: dict[str, float] = {}
        self._ghost_residuals: list[float] = []
        self.observable_ghost_router = ObservableResidualGHOSTRouter(
            seed=bootstrap_seed,
            min_global_support=2.0,
            min_pattern_support=4.0,
            min_local_support=8.0,
            min_exploration_support=4.0,
            allow_development_proxy=ghost_feedback_mode == "development_proxy",
        )
        self._observable_ghost_selected_priors: dict[str, float] = {}
        self._observable_ghost_residuals: list[float] = []
        self.ghost_patterns, self.ghost_skills, self.ghost_registry = (
            self._freeze_ghost_registry()
        )
        self.observable_ghost_registry = RegistrySnapshot.create(
            epoch=self.ghost_registry.epoch,
            stable_pattern_revision_ids=(
                self.ghost_registry.stable_pattern_revision_ids
            ),
            stable_skill_revision_ids=self.ghost_registry.stable_skill_revision_ids,
            config_sha256=content_sha256(
                {
                    "arm": "v4_observable_ghost_residual_v1",
                    "evaluator_snapshot_sha256": (
                        self.ghost_evaluator.snapshot_sha256
                    ),
                    "candidate_budget": self.candidate_budget,
                    "min_global_support": (
                        self.observable_ghost_router.min_global_support
                    ),
                    "min_pattern_support": (
                        self.observable_ghost_router.min_pattern_support
                    ),
                    "min_local_support": (
                        self.observable_ghost_router.min_local_support
                    ),
                    "min_exploration_support": (
                        self.observable_ghost_router.min_exploration_support
                    ),
                }
            ),
        )
        self.on_arm_outcome = on_arm_outcome
        self.on_case_completed = on_case_completed
        self.on_ghost_selection = on_ghost_selection

    def run(self) -> V4PrequentialRun:
        repository_dir = self.output_dir / "repositories"
        repository_dir.mkdir(parents=True, exist_ok=True)
        repository_paths = {
            arm: repository_dir / f"{arm}.sqlite"
            for arm in (
                "hierarchical_no_chain", "full_v4", "full_v4_observable"
            )
        }
        for path in repository_paths.values():
            if path.exists():
                raise ValueError(f"refusing to reuse mutable experiment repository: {path}")
        repositories = {
            arm: EvolutionRepository(path) for arm, path in repository_paths.items()
        }
        try:
            engines = {
                arm: NeuroSymbolicEvolutionEngine(repository)
                for arm, repository in repositories.items()
            }
            global_policy = OnlineRepairPolicy(
                locality_penalty=self.locality_penalty,
                change_penalty=self.change_penalty,
            )
            outcomes: list[V4ArmOutcome] = []
            total = len(self.cases)
            for position, case in enumerate(self.cases, 1):
                selections = self._select_all(
                    case,
                    global_policy=global_policy,
                    engines=engines,
                )
                case_rows = self._update_all(
                    case,
                    selections=selections,
                    global_policy=global_policy,
                    engines=engines,
                )
                outcomes.extend(case_rows)
                if self.on_arm_outcome is not None:
                    for row in case_rows:
                        self.on_arm_outcome(row.to_mapping())
                if self.on_case_completed is not None:
                    self.on_case_completed(position, total, case.case_id)
            report = self._report(tuple(outcomes), repositories)
            return V4PrequentialRun(tuple(outcomes), report)
        finally:
            for repository in repositories.values():
                repository.close()

    def _select_all(
        self,
        case: V4PrequentialCase,
        *,
        global_policy: OnlineRepairPolicy,
        engines: Mapping[str, NeuroSymbolicEvolutionEngine],
    ) -> dict[str, _ArmSelection]:
        intent_ids = tuple(row.intent_id for row in case.intents)
        selections: dict[str, _ArmSelection] = {
            "identity": _ArmSelection(None, "identity", (), None, None),
            "legacy_symbolic": _ArmSelection(
                case.legacy_intent_id, "frozen_legacy", (), None, None
            ),
            "random_k": _ArmSelection(
                intent_ids[self._random_index(case, len(intent_ids))],
                "seeded_random",
                (),
                None,
                None,
            ),
        }
        before = global_policy.snapshot.snapshot_sha256
        global_decision = global_policy.select(case.context, case.intents)
        selections["global_policy"] = _ArmSelection(
            global_decision.selected_intent_id,
            global_decision.reason,
            global_decision.niche_path,
            global_decision,
            before,
        )
        intent_priors = {
            intent.intent_id: self.ghost_evaluator.score(
                pre_action_features(
                    context=case.context,
                    graph=case.graph,
                    intent=intent,
                )
            )
            for intent in case.intents
        }
        for arm in ("hierarchical_no_chain", "full_v4"):
            engine = engines[arm]
            before = engine.policy.snapshot.snapshot_sha256
            decision = engine.select(
                case.context,
                graph=case.graph,
                intents=case.intents,
            ).decision
            selections[arm] = _ArmSelection(
                decision.selected_intent_id,
                decision.reason,
                decision.niche_path,
                decision,
                before,
            )
        observable_engine = engines["full_v4_observable"]
        before = observable_engine.policy.snapshot.snapshot_sha256
        observable_decision = observable_engine.select(
            case.context,
            graph=case.graph,
            intents=case.intents,
            score_offsets=intent_priors,
        ).decision
        selections["full_v4_observable"] = _ArmSelection(
            observable_decision.selected_intent_id,
            observable_decision.reason,
            observable_decision.niche_path,
            observable_decision,
            before,
        )
        failure = self._ghost_failure(case)
        pattern = self.ghost_patterns[case.context.semantic_cluster]
        species_to_intents: dict[str, list[RepairIntent]] = {}
        for intent in case.intents:
            skill = self.ghost_skills[self._ghost_species_key(case, intent)]
            species_to_intents.setdefault(skill.skill_revision_id, []).append(intent)
        candidates = tuple(
            self.ghost_skills[key]
            for key in sorted(
                {self._ghost_species_key(case, intent) for intent in case.intents}
            )
        )
        before = str(self.ghost_router.snapshot["snapshot_sha256"])
        skill_priors = {
            skill_id: max(intent_priors[row.intent_id] for row in intents)
            for skill_id, intents in species_to_intents.items()
        }
        ghost_decision = self.ghost_router.select(
            failure,
            pattern_responsibilities=(
                PatternResponsibility(pattern.pattern_revision_id, 1.0),
            ),
            skills=candidates,
            registry=self.ghost_registry,
            event_index=case.context.event_index,
            skill_priors=skill_priors,
        )
        selected_intent = min(
            species_to_intents[ghost_decision.selected_skill_revision_id],
            key=lambda row: (-intent_priors[row.intent_id], row.intent_id),
        )
        selections["ghost_hierarchy_v1"] = _ArmSelection(
            selected_intent.intent_id,
            "frozen_evaluator_prior_then_ghost_posterior",
            (pattern.pattern_revision_id, ghost_decision.selected_skill_revision_id),
            ghost_decision,
            before,
        )
        self._ghost_selected_priors[ghost_decision.selection_id] = intent_priors[
            selected_intent.intent_id
        ]
        observable_selected_skill_id = (
            None
            if observable_decision.selected_intent_id is None
            else self.ghost_skills[
                self._ghost_species_key(
                    case,
                    next(
                        row
                        for row in case.intents
                        if row.intent_id == observable_decision.selected_intent_id
                    ),
                )
            ].skill_revision_id
        )
        observable_ghost_before = str(
            self.observable_ghost_router.snapshot["snapshot_sha256"]
        )
        observable_ghost_decision = self.observable_ghost_router.select(
            failure,
            pattern_responsibilities=(
                PatternResponsibility(pattern.pattern_revision_id, 1.0),
            ),
            skills=candidates,
            registry=self.observable_ghost_registry,
            event_index=case.context.event_index,
            base_scores=skill_priors,
            base_selected_skill_revision_id=observable_selected_skill_id,
        )
        observable_ghost_intent: RepairIntent | None = None
        if observable_ghost_decision.selected_skill_revision_id is not None:
            if (
                observable_ghost_decision.selection_mode
                == "observable_fallback"
                and observable_decision.selected_intent_id is not None
            ):
                observable_ghost_intent = next(
                    row
                    for row in case.intents
                    if row.intent_id == observable_decision.selected_intent_id
                )
            else:
                observable_ghost_intent = min(
                    species_to_intents[
                        observable_ghost_decision.selected_skill_revision_id
                    ],
                    key=lambda row: (-intent_priors[row.intent_id], row.intent_id),
                )
            self._observable_ghost_selected_priors[
                observable_ghost_decision.selection_id
            ] = intent_priors[observable_ghost_intent.intent_id]
        selections["v4_observable_ghost_residual_v1"] = _ArmSelection(
            None
            if observable_ghost_intent is None
            else observable_ghost_intent.intent_id,
            observable_ghost_decision.selection_mode,
            (
                pattern.pattern_revision_id,
                *(() if observable_ghost_decision.selected_skill_revision_id is None
                  else (observable_ghost_decision.selected_skill_revision_id,)),
            ),
            observable_ghost_decision,
            observable_ghost_before,
        )
        if self.on_ghost_selection is not None:
            selected_skill = next(
                row for row in candidates
                if row.skill_revision_id == ghost_decision.selected_skill_revision_id
            )
            self.on_ghost_selection(
                {
                    "schema_version": "cmd-ghost-live-selection-v1",
                    "selected_at_utc": datetime.now(timezone.utc).isoformat().replace(
                        "+00:00", "Z"
                    ),
                    "case_id": case.case_id,
                    "family_id_audit_only": case.family_id,
                    "partition": self.ghost_partitions[case.case_id],
                    "selection_id": ghost_decision.selection_id,
                    "event_index": case.context.event_index,
                    "selected_intent_id": selected_intent.intent_id,
                    "selected_skill_revision_id": (
                        ghost_decision.selected_skill_revision_id
                    ),
                    "probe_id": str(selected_skill.success_probe["probe_id"]),
                    "repair_effect": selected_intent.effect,
                    "pre_action_prior": intent_priors[selected_intent.intent_id],
                    "registry_id": self.ghost_registry.registry_id,
                    "evaluator_snapshot_sha256": self.ghost_evaluator.snapshot_sha256,
                    "evaluation_only": self.ghost_partitions[case.case_id]
                    != "ghost_dev",
                    "development_proxy": self.ghost_feedback_mode
                    == "development_proxy",
                }
            )
        return selections

    def _update_all(
        self,
        case: V4PrequentialCase,
        *,
        selections: Mapping[str, _ArmSelection],
        global_policy: OnlineRepairPolicy,
        engines: Mapping[str, NeuroSymbolicEvolutionEngine],
    ) -> tuple[V4ArmOutcome, ...]:
        shadow = {row.intent_id: row for row in case.candidate_outcomes}
        rows: list[V4ArmOutcome] = []
        observed_after = case.context.event_index + 1
        for arm in V4_ARMS:
            selection = selections[arm]
            selected = (
                shadow[selection.intent_id] if selection.intent_id is not None else None
            )
            observation: OutcomeObservation | None = None
            runtime_gain: float | None = None
            if (
                selected is not None
                and arm in _UPDATING_ARMS
                and self.ghost_partitions[case.case_id] == "ghost_dev"
            ):
                if selection.decision is None:
                    raise AssertionError("updating arm is missing its frozen decision")
                selected_intent = next(
                    row for row in case.intents if row.intent_id == selected.intent_id
                )
                runtime_gain = _typed_runtime_feedback(case, selected_intent, selected)
                if runtime_gain is not None:
                    observation = OutcomeObservation(
                        selection.decision.selection_id,
                        case.case_id,
                        observed_after,
                        case.family_id,
                        selected.intent_id,
                        runtime_gain,
                        selected.locality_cost,
                        selected.changed_item_count,
                        selected.valid,
                        selected.rolled_back,
                    )
            transitions: tuple[dict[str, object], ...] = ()
            chain_decisions: tuple[dict[str, object], ...] = ()
            after = selection.policy_snapshot_before
            if arm == "global_policy" and observation is not None:
                if selection.decision is None:
                    raise AssertionError("global arm is missing decision")
                after = global_policy.observe(
                    selection.decision,
                    (observation,),
                    observed_after_event_index=observed_after,
                ).snapshot_sha256
            elif arm in {"hierarchical_no_chain", "full_v4"}:
                engine = engines[arm]
                if observation is not None:
                    if selection.decision is None:
                        raise AssertionError("engine arm is missing decision")
                    update = engine.record_outcomes(selection.decision, (observation,))
                    after = update.policy_snapshot.snapshot_sha256
                    transitions = tuple(
                        asdict(transition) for transition in update.species_transitions
                    )
                if (
                    arm == "full_v4"
                    and self.ghost_partitions[case.case_id] == "ghost_dev"
                    and case.chain_attempts
                ):
                    decisions: list[ChainGovernanceDecision] = [
                        engine.record_chain_attempt(attempt)
                        for attempt in case.chain_attempts
                    ]
                    chain_decisions = tuple(row.to_dict() for row in decisions)
            elif arm == "full_v4_observable":
                engine = engines[arm]
                if (
                    selected is not None
                    and self.ghost_partitions[case.case_id] == "ghost_dev"
                    and self.ghost_feedback_mode == "development_proxy"
                ):
                    if not isinstance(selection.decision, SelectionDecision):
                        raise AssertionError(
                            "observable Full V4 arm is missing its decision"
                        )
                    intent = next(
                        row for row in case.intents
                        if row.intent_id == selected.intent_id
                    )
                    prior = self.ghost_evaluator.score(
                        pre_action_features(
                            context=case.context,
                            graph=case.graph,
                            intent=intent,
                        )
                    )
                    residual = self._delayed_utility_proxy(selected) - prior
                    observation = OutcomeObservation(
                        selection.decision.selection_id,
                        case.case_id,
                        observed_after,
                        case.family_id,
                        selected.intent_id,
                        max(-1.0, min(1.0, residual)),
                        0.0,
                        0,
                        True,
                        False,
                    )
                    update = engine.record_outcomes(
                        selection.decision, (observation,)
                    )
                    after = update.policy_snapshot.snapshot_sha256
                    transitions = tuple(
                        asdict(transition)
                        for transition in update.species_transitions
                    )
            elif arm == "ghost_hierarchy_v1":
                if selected is None or not isinstance(selection.decision, EcologySelection):
                    raise AssertionError("GHOST arm is missing its selected outcome/decision")
                intent = next(
                    row for row in case.intents if row.intent_id == selected.intent_id
                )
                selected_prior = self._ghost_selected_priors.pop(
                    selection.decision.selection_id
                )
                if self.ghost_feedback_mode == "development_proxy":
                    delayed_utility = self._delayed_utility_proxy(selected)
                    residual = max(
                        -1.0, min(1.0, delayed_utility - selected_prior)
                    )
                    if self.ghost_partitions[case.case_id] == "ghost_dev":
                        self._ghost_residuals.append(residual)
                    skill = next(
                        row for row in self.ghost_skills.values()
                        if row.skill_revision_id
                        == selection.decision.selected_skill_revision_id
                    )
                    after = str(
                        self.ghost_router.observe(
                            selection.decision,
                            DelayedOutcomeFeedback(
                                selection_id=selection.decision.selection_id,
                                selected_skill_revision_id=(
                                    selection.decision.selected_skill_revision_id
                                ),
                                probe_id=str(skill.success_probe["probe_id"]),
                                selected_at_event_index=case.context.event_index,
                                observed_after_event_index=observed_after,
                                pre_action_prior=selected_prior,
                                delayed_utility=delayed_utility,
                                valid=selected.valid,
                                rolled_back=selected.rolled_back,
                                delayed_regression=False,
                                provenance="dev-delayed-outcome-proxy-v1",
                                evaluation_only=self.ghost_partitions[case.case_id]
                                != "ghost_dev",
                                development_proxy=True,
                            ),
                        )["snapshot_sha256"]
                    )
            elif arm == "v4_observable_ghost_residual_v1":
                if not isinstance(selection.decision, ObservableResidualSelection):
                    raise AssertionError(
                        "observable residual GHOST arm is missing its decision"
                    )
                if selected is not None:
                    selected_prior = self._observable_ghost_selected_priors.pop(
                        selection.decision.selection_id
                    )
                    if self.ghost_feedback_mode == "development_proxy":
                        delayed_utility = self._delayed_utility_proxy(selected)
                        residual = max(
                            -1.0, min(1.0, delayed_utility - selected_prior)
                        )
                        if self.ghost_partitions[case.case_id] == "ghost_dev":
                            self._observable_ghost_residuals.append(residual)
                        skill = next(
                            row
                            for row in self.ghost_skills.values()
                            if row.skill_revision_id
                            == selection.decision.selected_skill_revision_id
                        )
                        after = str(
                            self.observable_ghost_router.observe(
                                selection.decision,
                                DelayedOutcomeFeedback(
                                    selection_id=selection.decision.selection_id,
                                    selected_skill_revision_id=(
                                        skill.skill_revision_id
                                    ),
                                    probe_id=str(skill.success_probe["probe_id"]),
                                    selected_at_event_index=case.context.event_index,
                                    observed_after_event_index=observed_after,
                                    pre_action_prior=selected_prior,
                                    delayed_utility=delayed_utility,
                                    valid=selected.valid,
                                    rolled_back=selected.rolled_back,
                                    delayed_regression=False,
                                    provenance="dev-delayed-outcome-proxy-v1",
                                    evaluation_only=(
                                        self.ghost_partitions[case.case_id]
                                        != "ghost_dev"
                                    ),
                                    development_proxy=True,
                                ),
                            )["snapshot_sha256"]
                        )
            utility = self._utility(selected)
            rows.append(
                V4ArmOutcome(
                    case_id=case.case_id,
                    family_id=case.family_id,
                    probe_set=case.probe_set,
                    event_index=case.context.event_index,
                    arm_id=arm,
                    candidate_count=0 if arm == "identity" else len(case.intents),
                    candidate_intent_ids=()
                    if arm == "identity"
                    else tuple(row.intent_id for row in case.intents),
                    selected_intent_id=selection.intent_id,
                    selection_reason=selection.reason,
                    niche_path=selection.niche_path,
                    recovery_gain=0.0 if selected is None else selected.recovery_gain,
                    locality_cost=0.0 if selected is None else selected.locality_cost,
                    changed_item_count=0
                    if selected is None
                    else selected.changed_item_count,
                    valid=True if selected is None else selected.valid,
                    rolled_back=False if selected is None else selected.rolled_back,
                    utility=utility,
                    policy_snapshot_before=selection.policy_snapshot_before,
                    policy_snapshot_after=after,
                    update_effective_after_event_index=observed_after
                    if observation is not None
                    or (
                        arm in {
                            "ghost_hierarchy_v1",
                            "full_v4_observable",
                            "v4_observable_ghost_residual_v1",
                        }
                        and self.ghost_partitions[case.case_id] == "ghost_dev"
                        and self.ghost_feedback_mode == "development_proxy"
                    )
                    else None,
                    species_transitions=transitions,
                    chain_decisions=chain_decisions,
                )
            )
        return tuple(rows)

    @staticmethod
    def _ghost_species_key(case: V4PrequentialCase, intent: RepairIntent) -> str:
        edge = next(
            row for row in case.graph.edges if row.edge_id == intent.relation_edge_id
        )
        return content_sha256(
            {
                "schema_version": "cmd-preregistered-repair-type-v1",
                "effect": intent.effect,
                "typed_motif": {
                    "relation": edge.relation,
                    "actionability_mode": edge.actionability.mode.value,
                    "reason_code": edge.actionability.reason_code,
                },
            }
        )

    @staticmethod
    def _ghost_parameter_rank(
        case: V4PrequentialCase, intent: RepairIntent
    ) -> tuple[int, str]:
        edge = next(
            row for row in case.graph.edges if row.edge_id == intent.relation_edge_id
        )
        mode = edge.actionability.mode.value
        destructive = intent.effect in {"demote", "suppress", "replace"}
        compatible = (
            (
                destructive
                and mode == "destructive"
                and intent.target_item_id == edge.actionability.target_item_id
            )
            or (intent.effect == "annotate_conflict" and mode == "annotate_only")
            or (intent.effect in {"verify", "abstain"} and mode != "destructive")
        )
        return (0 if compatible else 1, intent.intent_id)

    def _freeze_ghost_registry(
        self,
    ) -> tuple[
        dict[str, PatternRevision], dict[str, SkillRevision], RegistrySnapshot
    ]:
        patterns = {
            semantic: PatternRevision.create(
                pattern_id=f"semantic:{semantic}",
                predicate={"kind": "deployment_semantic_cluster", "value": semantic},
                feature_signature=tuple(sorted({
                    key
                    for case in self.cases
                    if case.context.semantic_cluster == semantic
                    for key in case.context.features
                })),
                derivation_kind="seed",
                state="stable",
            )
            for semantic in sorted({case.context.semantic_cluster for case in self.cases})
        }
        representatives: dict[str, tuple[V4PrequentialCase, RepairIntent]] = {}
        for case in self.cases:
            for intent in case.intents:
                representatives.setdefault(
                    self._ghost_species_key(case, intent), (case, intent)
                )
        skills = {
            key: SkillRevision.create(
                skill_id=f"repair-species:{key}",
                program={
                    "kind": "registered_parameterized_repair_species",
                    "effect": intent.effect,
                    "typed_motif": {
                        "relation": next(
                            row.relation
                            for row in case.graph.edges
                            if row.edge_id == intent.relation_edge_id
                        ),
                        "actionability_mode": next(
                            row.actionability.mode.value
                            for row in case.graph.edges
                            if row.edge_id == intent.relation_edge_id
                        ),
                        "reason_code": next(
                            row.actionability.reason_code
                            for row in case.graph.edges
                            if row.edge_id == intent.relation_edge_id
                        ),
                    },
                },
                parameter_schema={
                    "relation_edge_id": {"type": "frozen_relation_edge_id"},
                    "target_item_id": {"type": ["memory_item_id", "null"]},
                },
                preconditions=({"predicate": "intent_compiles_against_frozen_graph"},),
                postconditions=({"predicate": "frozen_deployment_evaluator_score"},),
                success_probe={
                    "probe_id": f"probe:frozen-evaluator:{key}",
                    "kind": "frozen_deployment_evaluator_v1",
                    "evaluator_snapshot_sha256": self.ghost_evaluator.snapshot_sha256,
                },
                mutation_budget={"max_items": 4, "max_logical_cost": 16},
                rollback_program={"kind": "restore_pre_selection_snapshot"},
                producing_failure_id="preregistered-seed-library",
                derivation_kind="seed",
                state="stable",
            )
            for key, (case, intent) in sorted(representatives.items())
        }
        registry = RegistrySnapshot.create(
            epoch=1,
            stable_pattern_revision_ids=tuple(
                row.pattern_revision_id for row in patterns.values()
            ),
            stable_skill_revision_ids=tuple(
                row.skill_revision_id for row in skills.values()
            ),
            config_sha256=content_sha256(
                {
                    "arm": "ghost_hierarchy_v1",
                    "evaluator_snapshot_sha256": self.ghost_evaluator.snapshot_sha256,
                    "candidate_budget": self.candidate_budget,
                }
            ),
        )
        return patterns, skills, registry

    @staticmethod
    def _ghost_failure(case: V4PrequentialCase) -> FailureDeposit:
        return FailureDeposit(
            failure_id=f"failure:{content_sha256({'context': case.context.content_hash()})}",
            case_id=case.case_id,
            family_id_audit_only=case.family_id,
            failure_memory_sha256=content_sha256(
                {"graph_sha256": case.graph.graph_sha256, "context": case.context.content_hash()}
            ),
            features=tuple(sorted((key, float(value)) for key, value in case.context.features.items())),
            context_sha256=case.context.content_hash(),
            provenance_sha256=content_sha256(
                {"runtime_surface": case.context.runtime_surface, "graph": case.graph.graph_sha256}
            ),
        )

    def _utility(self, outcome: V4CandidateOutcome | None) -> float:
        if outcome is None or not outcome.valid or outcome.rolled_back:
            return 0.0
        return (
            outcome.recovery_gain
            - self.locality_penalty * outcome.locality_cost
            - self.change_penalty * outcome.changed_item_count
        )

    def _delayed_utility_proxy(self, outcome: V4CandidateOutcome) -> float:
        if not outcome.valid or outcome.rolled_back:
            return -1.0
        return max(-1.0, min(1.0, self._utility(outcome)))

    def _random_index(self, case: V4PrequentialCase, size: int) -> int:
        payload = f"{self.bootstrap_seed}\x00{case.case_id}\x00{case.context.event_index}"
        return int(hashlib.sha256(payload.encode()).hexdigest(), 16) % size

    def _report(
        self,
        outcomes: tuple[V4ArmOutcome, ...],
        repositories: Mapping[str, EvolutionRepository],
    ) -> dict[str, object]:
        summaries: dict[str, dict[str, object]] = {}
        for arm in V4_ARMS:
            arm_rows = tuple(row for row in outcomes if row.arm_id == arm)
            summaries[arm] = {
                "n": len(arm_rows),
                "selected_rate": fmean(
                    row.selected_intent_id is not None for row in arm_rows
                ),
                "mean_recovery_gain": fmean(row.recovery_gain for row in arm_rows),
                "mean_locality_cost": fmean(row.locality_cost for row in arm_rows),
                "mean_utility": fmean(row.utility for row in arm_rows),
                "aulc_utility": _aulc(tuple(row.utility for row in arm_rows)),
                "represented_mean_utility": _optional_mean(
                    row.utility for row in arm_rows if row.probe_set == "represented"
                ),
                "unseen_mean_utility": _optional_mean(
                    row.utility for row in arm_rows if row.probe_set == "unseen"
                ),
            }
        gate = _paired_gate(
            outcomes,
            baseline=self.primary_baseline,
            samples=self.bootstrap_samples,
            seed=self.bootstrap_seed,
            safety_margin=self.safety_margin,
        )
        ghost_cal_gate = _ghost_cal_gate(
            outcomes,
            partitions=self.ghost_partitions,
            samples=self.bootstrap_samples,
            seed=self.bootstrap_seed,
        )
        observable_residual_ghost_cal_gate = _ghost_cal_gate(
            outcomes,
            partitions=self.ghost_partitions,
            samples=self.bootstrap_samples,
            seed=self.bootstrap_seed,
            treatment_arm="v4_observable_ghost_residual_v1",
        )
        payload: dict[str, object] = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "protocol": "cmd-neuro-symbolic-memory-evolution-v4-prequential",
            "model_calls": self.upstream_model_calls,
            "runner_model_calls": 0,
            "upstream_materialization_model_calls": self.upstream_model_calls,
            "case_count": len(self.cases),
            "arm_count": len(V4_ARMS),
            "candidate_budget": self.candidate_budget,
            "selected_action_feedback_only": True,
            "ghost_feedback_gold_derived": False,
            "ghost_evaluator_snapshot_sha256": self.ghost_evaluator.snapshot_sha256,
            "ghost_partition_counts": {
                partition: sum(value == partition for value in self.ghost_partitions.values())
                for partition in sorted(set(self.ghost_partitions.values()))
            },
            "ghost_partition_mean_utility": {
                partition: fmean(
                    row.utility
                    for row in outcomes
                    if row.arm_id == "ghost_hierarchy_v1"
                    and self.ghost_partitions[row.case_id] == partition
                )
                for partition in sorted(set(self.ghost_partitions.values()))
            },
            "observable_residual_ghost_partition_mean_utility": {
                partition: fmean(
                    row.utility
                    for row in outcomes
                    if row.arm_id == "v4_observable_ghost_residual_v1"
                    and self.ghost_partitions[row.case_id] == partition
                )
                for partition in sorted(set(self.ghost_partitions.values()))
            },
            "ghost_updates_only_partition": "ghost_dev",
            "ghost_feedback_mode": self.ghost_feedback_mode,
            "ghost_pending_feedback_count": (
                len(self.cases)
                if self.ghost_feedback_mode == "prospective_deployment"
                else 0
            ),
            "observable_residual_ghost_pending_feedback_count": (
                sum(
                    row.arm_id == "v4_observable_ghost_residual_v1"
                    and row.selected_intent_id is not None
                    for row in outcomes
                )
                if self.ghost_feedback_mode == "prospective_deployment"
                else 0
            ),
            "arm_roles": {
                "full_v4": "shadow_gold_oracle_ceiling",
                "full_v4_observable": "same_feedback_online_residual_baseline",
                "ghost_hierarchy_v1": "hierarchical_residual_treatment",
                "v4_observable_ghost_residual_v1": (
                    "observable_backbone_support_gated_hierarchical_residual"
                ),
            },
            "feature_timing": {
                "selection": "pre_action_only",
                "post_action_telemetry_in_selection": False,
                "delayed_outcome_proxy_available_after_selection": True,
            },
            "ghost_residual_diagnostics": {
                "count": len(self._ghost_residuals),
                "nonzero_count": sum(abs(row) > 1e-12 for row in self._ghost_residuals),
                "mean": _optional_mean(self._ghost_residuals),
                "mean_absolute": _optional_mean(abs(row) for row in self._ghost_residuals),
            },
            "observable_residual_ghost_diagnostics": {
                **self.observable_ghost_router.diagnostics,
                "residual_count": len(self._observable_ghost_residuals),
                "residual_nonzero_count": sum(
                    abs(row) > 1e-12
                    for row in self._observable_ghost_residuals
                ),
                "residual_mean": _optional_mean(
                    self._observable_ghost_residuals
                ),
                "residual_mean_absolute": _optional_mean(
                    abs(row) for row in self._observable_ghost_residuals
                ),
                "min_global_support": (
                    self.observable_ghost_router.min_global_support
                ),
                "min_pattern_support": (
                    self.observable_ghost_router.min_pattern_support
                ),
                "min_local_support": (
                    self.observable_ghost_router.min_local_support
                ),
                "min_exploration_support": (
                    self.observable_ghost_router.min_exploration_support
                ),
            },
            "ghost_hierarchy": {
                "species_identity": "effect+typed_motif",
                "min_pattern_support": self.ghost_router.min_pattern_support,
                "min_local_support": self.ghost_router.min_local_support,
                "update_mode": "global_then_pattern_residual_then_local_residual",
            },
            "test_all_arms_before_update": True,
            "chain_mode": "governed_shadow_only",
            "case_stream_sha256": content_sha256(
                [row.to_mapping() for row in self.cases]
            ),
            "arm_summaries": summaries,
            "gate": gate,
            "ghost_cal_gate": ghost_cal_gate,
            "observable_residual_ghost_cal_gate": (
                observable_residual_ghost_cal_gate
            ),
            "repository_sha256": {
                arm: repository.repository_hash()
                for arm, repository in sorted(repositories.items())
            },
            "stable_species_transitions": sum(
                transition.get("to_state") == "stable"
                for row in outcomes
                for transition in row.species_transitions
            ),
            "stable_chain_decisions": sum(
                decision.get("lifecycle") == "stable"
                for row in outcomes
                for decision in row.chain_decisions
            ),
        }
        return {**payload, "report_sha256": content_sha256(payload)}


def _optional_mean(values: object) -> float | None:
    rows = tuple(values)  # type: ignore[call-overload]
    return None if not rows else fmean(rows)


def _aulc(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    cumulative = [fmean(values[: index + 1]) for index in range(len(values))]
    return sum(
        (left + right) / 2.0
        for left, right in zip(cumulative, cumulative[1:])
    ) / (len(cumulative) - 1)


def _lower_one_sided(draws: Sequence[float]) -> float:
    ordered = sorted(draws)
    index = max(0, math.ceil(0.05 * len(ordered)) - 1)
    return float(ordered[index])


def _family_differences(
    outcomes: Sequence[V4ArmOutcome],
    *,
    probe_set: str,
    left: str,
    right: str,
) -> dict[str, float]:
    by_key = {
        (row.case_id, row.arm_id): row
        for row in outcomes
        if row.probe_set == probe_set
    }
    by_family: dict[str, list[float]] = {}
    cases = {
        (row.case_id, row.family_id)
        for row in outcomes
        if row.probe_set == probe_set
    }
    for case_id, family_id in sorted(cases):
        try:
            difference = by_key[(case_id, left)].utility - by_key[(case_id, right)].utility
        except KeyError as error:
            raise ValueError("gate requires every arm to be case-paired") from error
        by_family.setdefault(family_id, []).append(difference)
    return {family: fmean(values) for family, values in by_family.items()}


def _bootstrap_mean(
    family_values: Mapping[str, float], *, samples: int, seed: int
) -> tuple[float, float]:
    if not family_values:
        raise ValueError("family-block gate has no observations")
    keys = tuple(sorted(family_values))
    point = fmean(family_values.values())
    rng = random.Random(seed)
    draws = [
        fmean(family_values[keys[rng.randrange(len(keys))]] for _ in keys)
        for _ in range(samples)
    ]
    return point, _lower_one_sided(draws)


def _paired_gate(
    outcomes: Sequence[V4ArmOutcome],
    *,
    baseline: str,
    samples: int,
    seed: int,
    safety_margin: float,
) -> dict[str, object]:
    represented = _family_differences(
        outcomes,
        probe_set="represented",
        left="full_v4",
        right=baseline,
    )
    unseen = _family_differences(
        outcomes,
        probe_set="unseen",
        left="full_v4",
        right="identity",
    )
    estimate, lower = _bootstrap_mean(represented, samples=samples, seed=seed)
    safety_estimate, safety_lower = _bootstrap_mean(
        unseen, samples=samples, seed=seed + 1
    )
    primary_passed = estimate > 0.0 and lower > 0.0
    safety_passed = safety_estimate >= 0.0 and safety_lower >= safety_margin
    return {
        "primary_baseline": baseline,
        "bootstrap_samples": samples,
        "bootstrap_seed": seed,
        "represented_family_count": len(represented),
        "represented_estimate": estimate,
        "represented_lower_95_one_sided": lower,
        "primary_passed": primary_passed,
        "unseen_family_count": len(unseen),
        "unseen_safety_estimate": safety_estimate,
        "unseen_safety_lower_95_one_sided": safety_lower,
        "safety_margin": safety_margin,
        "safety_passed": safety_passed,
        "passed": primary_passed and safety_passed,
    }


def _ghost_cal_gate(
    outcomes: Sequence[V4ArmOutcome],
    *,
    partitions: Mapping[str, str],
    samples: int,
    seed: int,
    treatment_arm: str = "ghost_hierarchy_v1",
) -> dict[str, object]:
    by_key = {(row.case_id, row.arm_id): row for row in outcomes}
    families = {
        row.family_id
        for row in outcomes
        if row.arm_id == treatment_arm
        and partitions[row.case_id] == "ghost_cal"
    }
    comparisons: dict[str, object] = {}
    for baseline in ("full_v4_observable", "full_v4", "global_policy"):
        by_family: dict[str, list[float]] = {family: [] for family in families}
        for row in outcomes:
            if (
                row.arm_id == treatment_arm
                and partitions[row.case_id] == "ghost_cal"
            ):
                by_family[row.family_id].append(
                    row.utility - by_key[(row.case_id, baseline)].utility
                )
        values = {family: fmean(rows) for family, rows in by_family.items()}
        estimate, lower = _bootstrap_mean(values, samples=samples, seed=seed)
        comparison_passed = estimate > 0.0 and lower > 0.0
        comparisons[baseline] = {
            "family_count": len(values),
            "estimate": estimate,
            "lower_95_one_sided": lower,
            "passed": comparison_passed,
            "decision_role": (
                "same_feedback_primary"
                if baseline == "full_v4_observable"
                else "oracle_context_only"
            ),
        }
    primary = comparisons["full_v4_observable"]
    return {
        "partition": "ghost_cal",
        "treatment_arm": treatment_arm,
        "updates_allowed": False,
        "primary_baseline": "full_v4_observable",
        "oracle_comparisons_affect_decision": False,
        "comparisons": comparisons,
        "passed": bool(primary["passed"]),
    }


def load_cases(path: Path) -> tuple[V4PrequentialCase, ...]:
    cases: list[V4PrequentialCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid case JSONL at line {line_number}") from error
        cases.append(
            V4PrequentialCase.from_mapping(_mapping(value, f"case line {line_number}"))
        )
    if not cases:
        raise ValueError("case JSONL is empty")
    return tuple(cases)


def _append_jsonl(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as stream:
        stream.write(encoded + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidate-budget", type=int, required=True)
    parser.add_argument("--ghost-evaluator", type=Path, required=True)
    parser.add_argument("--ghost-protocol", type=Path, required=True)
    parser.add_argument("--materialization-manifest", type=Path)
    parser.add_argument(
        "--ghost-feedback-mode",
        choices=sorted(_GHOST_FEEDBACK_MODES),
        default="development_proxy",
    )
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--bootstrap-seed", type=int, default=24)
    parser.add_argument("--safety-margin", type=float, default=-0.05)
    parser.add_argument(
        "--primary-baseline", default="global_policy", choices=V4_ARMS[:-1]
    )
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    outcome_path = args.output_dir / "arm_outcomes.jsonl"
    progress_path = args.output_dir / "progress.jsonl"
    report_path = args.output_dir / "report.json"
    ghost_selection_path = args.output_dir / "ghost_selections.jsonl"
    for path in (outcome_path, progress_path, report_path, ghost_selection_path):
        if path.exists():
            raise ValueError(f"refusing to overwrite experiment artifact: {path}")
    cases = load_cases(args.cases)
    upstream_model_calls = 0
    materialization_manifest_sha256 = None
    if args.materialization_manifest is not None:
        materialization_manifest = _mapping(
            json.loads(args.materialization_manifest.read_text(encoding="utf-8")),
            "materialization manifest",
        )
        if materialization_manifest.get("output_sha256") != hashlib.sha256(
            args.cases.read_bytes()
        ).hexdigest():
            raise ValueError("materialization manifest does not bind --cases")
        raw_calls = materialization_manifest.get("materialization_model_calls")
        if (
            isinstance(raw_calls, bool)
            or not isinstance(raw_calls, int)
            or raw_calls < 0
        ):
            raise ValueError("materialization manifest model call count is invalid")
        upstream_model_calls = raw_calls
        materialization_manifest_sha256 = hashlib.sha256(
            args.materialization_manifest.read_bytes()
        ).hexdigest()
    manifest = {
        "schema_version": "cmd-v4-prequential-run-manifest-v1",
        "cases": str(args.cases.resolve()),
        "cases_file_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        "candidate_budget": args.candidate_budget,
        "bootstrap_samples": args.bootstrap_samples,
        "bootstrap_seed": args.bootstrap_seed,
        "safety_margin": args.safety_margin,
        "primary_baseline": args.primary_baseline,
        "arms": list(V4_ARMS),
        "ghost_evaluator": str(args.ghost_evaluator.resolve()),
        "ghost_evaluator_file_sha256": hashlib.sha256(
            args.ghost_evaluator.read_bytes()
        ).hexdigest(),
        "ghost_protocol": str(args.ghost_protocol.resolve()),
        "ghost_protocol_file_sha256": hashlib.sha256(
            args.ghost_protocol.read_bytes()
        ).hexdigest(),
        "ghost_feedback_mode": args.ghost_feedback_mode,
        "materialization_manifest": (
            None
            if args.materialization_manifest is None
            else str(args.materialization_manifest.resolve())
        ),
        "materialization_manifest_file_sha256": materialization_manifest_sha256,
        "upstream_materialization_model_calls": upstream_model_calls,
    }
    _atomic_json(args.output_dir / "run_manifest.json", manifest)
    _append_jsonl(
        progress_path,
        {"event": "started", "completed": 0, "total": len(cases)},
    )

    def completed(position: int, total: int, case_id: str) -> None:
        _append_jsonl(
            progress_path,
            {
                "event": "case_completed",
                "completed": position,
                "total": total,
                "case_id": case_id,
            },
        )

    try:
        ghost_evaluator = FrozenDeploymentEvaluator.from_mapping(
            _mapping(
                json.loads(args.ghost_evaluator.read_text(encoding="utf-8")),
                "ghost evaluator",
            )
        )
        ghost_protocol = _mapping(
            json.loads(args.ghost_protocol.read_text(encoding="utf-8")),
            "ghost protocol",
        )
        raw_assignments = ghost_protocol.get("assignments")
        if not isinstance(raw_assignments, list):
            raise ValueError("ghost protocol assignments are missing")
        ghost_partitions: dict[str, str] = {}
        for raw in raw_assignments:
            assignment = _mapping(raw, "ghost protocol assignment")
            case_id = assignment.get("case_id")
            partition = assignment.get("partition")
            if not isinstance(case_id, str) or not isinstance(partition, str):
                raise ValueError("ghost protocol assignment is invalid")
            if case_id in ghost_partitions:
                raise ValueError("ghost protocol repeats a case assignment")
            ghost_partitions[case_id] = partition
        result = V4PrequentialRunner(
            cases,
            output_dir=args.output_dir,
            candidate_budget=args.candidate_budget,
            bootstrap_samples=args.bootstrap_samples,
            bootstrap_seed=args.bootstrap_seed,
            safety_margin=args.safety_margin,
            primary_baseline=args.primary_baseline,
            ghost_evaluator=ghost_evaluator,
            ghost_partitions=ghost_partitions,
            ghost_feedback_mode=args.ghost_feedback_mode,
            upstream_model_calls=upstream_model_calls,
            on_arm_outcome=lambda row: _append_jsonl(outcome_path, row),
            on_case_completed=completed,
            on_ghost_selection=lambda row: _append_jsonl(
                ghost_selection_path, row
            ),
        ).run()
    except Exception as error:
        _append_jsonl(
            progress_path,
            {
                "event": "failed",
                "completed": 0,
                "total": len(cases),
                "error": repr(error),
            },
        )
        raise
    _atomic_json(report_path, result.report)
    _append_jsonl(
        progress_path,
        {"event": "completed", "completed": len(cases), "total": len(cases)},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CASE_SCHEMA_VERSION",
    "REPORT_SCHEMA_VERSION",
    "V4_ARMS",
    "V4ArmOutcome",
    "V4CandidateOutcome",
    "V4PrequentialCase",
    "V4PrequentialRun",
    "V4PrequentialRunner",
    "load_cases",
    "main",
]
