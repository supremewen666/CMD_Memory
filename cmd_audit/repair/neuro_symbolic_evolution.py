"""Integrated V4 parameterized-policy and governed-sedimentation engine.

The engine is intentionally not a memory-store authority.  It ranks complete
intents, compiles them against a frozen graph, records post-outcome evidence,
and evolves replayable policy/species/chain state.  Store mutation remains the
job of the separately authorized transactional deployment guard.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Mapping, Sequence

from cmd_audit.counterfactual.relation_graph import FrozenRelationGraph
from cmd_audit.counterfactual.successor_program_ir import (
    Program,
    canonical_ast_hash,
    program_to_mapping,
)

from .evolution_repository import EvolutionRepository, content_sha256
from .parametric_policy import (
    COMPILER_VERSION,
    NicheStatus,
    OnlineRepairPolicy,
    OutcomeObservation,
    PolicyContext,
    PolicySnapshot,
    RepairIntent,
    SelectionDecision,
    compile_intent,
    niche_path,
)
from .repair_chain_governance import (
    ChainAttemptInput,
    ChainGovernanceDecision,
    RepairChainGovernor,
)


@dataclass(frozen=True)
class EvolutionSelection:
    decision: SelectionDecision
    compiled_programs: tuple[tuple[str, Program], ...]

    def to_mapping(self) -> dict[str, object]:
        return {
            "decision": self.decision.to_mapping(),
            "compiled_programs": [
                {
                    "intent_id": intent_id,
                    "program": program_to_mapping(program),
                    "program_sha256": canonical_ast_hash(program),
                }
                for intent_id, program in self.compiled_programs
            ],
        }


@dataclass(frozen=True)
class SpeciesTransition:
    species_id: str
    strategy_id: str
    niche_path: str
    from_state: str | None
    to_state: str
    reason: str
    event_index: int
    support_count: int
    family_count: int
    utility: float
    lifecycle_event_id: str


@dataclass(frozen=True)
class EvolutionUpdate:
    observation_ids: tuple[str, ...]
    policy_snapshot: PolicySnapshot
    species_transitions: tuple[SpeciesTransition, ...]


@dataclass
class _SpeciesState:
    species_id: str
    key: tuple[str, str, str, str]
    strategy_id: str
    niche_path: str
    producing_case_id: str
    producing_event_index: int
    state: str
    evidence: list[dict[str, object]]

    @property
    def later_positive(self) -> tuple[dict[str, object], ...]:
        return tuple(
            row
            for row in self.evidence
            if row["case_id"] != self.producing_case_id
            and bool(row["positive"])
        )

    @property
    def failures(self) -> int:
        return sum(not bool(row["positive"]) for row in self.evidence)


class NeuroSymbolicEvolutionEngine:
    """Chronological selection, learning, deposition, and chain governance."""

    def __init__(
        self,
        repository: EvolutionRepository,
        *,
        policy: OnlineRepairPolicy | None = None,
        chain_governor: RepairChainGovernor | None = None,
        min_species_later_support: int = 2,
        min_species_families: int = 2,
        min_species_utility: float = 0.0,
        retirement_patience: int = 2,
        active_species_cap: int = 5,
    ) -> None:
        if min_species_later_support < 1 or min_species_families < 2:
            raise ValueError("species promotion needs later cross-family support")
        if retirement_patience < 1 or active_species_cap < 1:
            raise ValueError("governance caps/patience must be positive")
        self.repository = repository
        self.min_species_later_support = int(min_species_later_support)
        self.min_species_families = int(min_species_families)
        self.min_species_utility = float(min_species_utility)
        self.retirement_patience = int(retirement_patience)
        self.active_species_cap = int(active_species_cap)
        snapshots = self.repository.rows("policy_snapshot")
        if snapshots:
            latest = max(
                (PolicySnapshot.from_mapping(row["payload"]) for row in snapshots),
                key=lambda item: (
                    item.effective_after_event_index,
                    item.snapshot_sha256,
                ),
            )
            if policy is not None and policy.snapshot.snapshot_sha256 != latest.snapshot_sha256:
                raise ValueError("supplied policy disagrees with repository head")
            self.policy = OnlineRepairPolicy.from_snapshot(latest)
        else:
            self.policy = policy or OnlineRepairPolicy()
            self.repository.append_policy_snapshot(self.policy.snapshot.to_mapping())
        self.chain_governor = chain_governor or RepairChainGovernor()
        self._selection_records: dict[
            str,
            tuple[PolicyContext, tuple[RepairIntent, ...], SelectionDecision],
        ] = {}
        self._species_by_key: dict[tuple[str, str, str, str], _SpeciesState] = {}
        self._known_outcome_ids = {
            str(row["event_id"]) for row in self.repository.rows("outcome")
        }
        self._load_species()
        self._load_chains()

    def select(
        self,
        context: PolicyContext,
        *,
        graph: FrozenRelationGraph,
        intents: Sequence[RepairIntent],
    ) -> EvolutionSelection:
        if context.case_id != graph.case_id:
            raise ValueError("policy context and graph belong to different cases")
        if context.graph_sha256 != graph.graph_sha256:
            raise ValueError("policy context does not bind the frozen graph")
        candidates = tuple(intents)
        compiled = tuple(
            (intent.intent_id, compile_intent(intent, graph=graph))
            for intent in candidates
        )
        decision = self.policy.select(context, candidates)
        self.repository.append_selection(decision.to_mapping())
        self._selection_records[decision.selection_id] = (
            context,
            candidates,
            decision,
        )
        return EvolutionSelection(decision, compiled)

    def record_outcomes(
        self,
        decision: SelectionDecision,
        observations: Sequence[OutcomeObservation],
    ) -> EvolutionUpdate:
        record = self._selection_records.get(decision.selection_id)
        if record is None or record[2] != decision:
            raise ValueError("outcome refers to an unknown in-process selection")
        context, intents, _ = record
        by_intent = {intent.intent_id: intent for intent in intents}
        rows = tuple(observations)
        if not rows:
            return EvolutionUpdate((), self.policy.snapshot, ())
        if len({row.content_hash() for row in rows}) != len(rows):
            raise ValueError("outcome batch contains duplicates")
        for row in rows:
            if row.selection_id != decision.selection_id:
                raise ValueError("outcome belongs to another selection")
            if row.intent_id not in by_intent:
                raise ValueError("outcome intent was not proposed")

        observation_ids: list[str] = []
        transitions: list[SpeciesTransition] = []
        for row in rows:
            observation_id = self.repository.append_outcome(row.to_mapping())
            observation_ids.append(observation_id)
            if observation_id in self._known_outcome_ids:
                continue
            self._known_outcome_ids.add(observation_id)
            transition = self._record_species_observation(
                context=context,
                decision=decision,
                intent=by_intent[row.intent_id],
                observation=row,
                observation_id=observation_id,
            )
            if transition is not None:
                transitions.append(transition)

        snapshot = self.policy.observe(
            decision,
            rows,
            observed_after_event_index=max(
                row.observed_after_event_index for row in rows
            ),
        )
        self.repository.append_policy_snapshot(snapshot.to_mapping())
        return EvolutionUpdate(tuple(observation_ids), snapshot, tuple(transitions))

    def record_chain_attempt(
        self, attempt: ChainAttemptInput
    ) -> ChainGovernanceDecision:
        attempt_id = self.repository.append_chain_attempt(
            {
                **attempt.__dict__,
                "chain_benefit": attempt.chain_benefit,
            }
        )
        decision = self.chain_governor.record_attempt(attempt)
        self.repository.append_chain_decision(
            {
                "chain_id": decision.chain_id,
                "to_state": decision.lifecycle,
                "event_index": decision.event_index,
                "attempt_id": attempt_id,
                "reason": decision.reason,
                "anti_pattern": decision.anti_pattern,
                "governance_payload_sha256": decision.decision_sha256,
            }
        )
        return decision

    def _record_species_observation(
        self,
        *,
        context: PolicyContext,
        decision: SelectionDecision,
        intent: RepairIntent,
        observation: OutcomeObservation,
        observation_id: str,
    ) -> SpeciesTransition | None:
        utility = self._utility(observation)
        positive = bool(
            observation.valid
            and not observation.rolled_back
            and utility > self.min_species_utility
        )
        key = intent.species_key
        state = self._species_by_key.get(key)
        deepest_niche = niche_path(context)[-1]
        if state is None:
            if not positive:
                return None
            species_payload = {
                "strategy_id": intent.strategy_id,
                "effect": intent.effect,
                "compiler_version": COMPILER_VERSION,
                "proposer_model_hash": intent.proposer_model_hash,
                "producing_case_id": observation.case_id,
                "producing_event_index": observation.observed_after_event_index,
                "niche_path": deepest_niche,
            }
            species_id = self.repository.append_species(species_payload)
            state = _SpeciesState(
                species_id=species_id,
                key=key,
                strategy_id=intent.strategy_id,
                niche_path=deepest_niche,
                producing_case_id=observation.case_id,
                producing_event_index=observation.observed_after_event_index,
                state="candidate",
                evidence=[],
            )
            self._species_by_key[key] = state
            from_state: str | None = None
            reason = "positive_producer_deposit"
        else:
            from_state = state.state
            reason = "later_evidence_recorded"
        evidence = {
            "observation_id": observation_id,
            "selection_id": decision.selection_id,
            "case_id": observation.case_id,
            "family_id": observation.family_id,
            "event_index": observation.observed_after_event_index,
            "utility": utility,
            "positive": positive,
        }
        if any(row["observation_id"] == observation_id for row in state.evidence):
            return None
        state.evidence.append(evidence)
        if state.producing_case_id == observation.case_id:
            state.state = "candidate"
            reason = "producing_case_not_self_validating"
        elif not positive:
            if state.failures >= self.retirement_patience:
                state.state = "retired"
                reason = "repeated_failure_retirement"
            else:
                reason = "negative_or_rolled_back_evidence"
        else:
            later = state.later_positive
            families = {str(row["family_id"]) for row in later}
            if (
                len(later) >= self.min_species_later_support
                and len(families) >= self.min_species_families
                and min(float(row["utility"]) for row in later)
                > self.min_species_utility
            ):
                state.state = "stable"
                reason = "cross_family_promotion"
            else:
                state.state = "probation"
                reason = "awaiting_cross_family_support"

        policy_status = {
            "candidate": NicheStatus.PROBATION,
            "probation": NicheStatus.PROBATION,
            "stable": NicheStatus.STABLE,
            "retired": NicheStatus.RETIRED,
        }[state.state]
        self.policy.set_niche_status(state.niche_path, policy_status)
        if state.state == "stable":
            self.chain_governor.admit_strategy(state.strategy_id)
        support = state.later_positive
        lifecycle_payload = {
            "subject_id": state.species_id,
            "subject_kind": "intent_species",
            "from_state": from_state,
            "to_state": state.state,
            "reason": reason,
            "event_index": observation.observed_after_event_index,
            "niche_path": state.niche_path,
            "evidence": evidence,
            "support_count": len(support),
            "family_count": len({str(row["family_id"]) for row in support}),
        }
        lifecycle_event_id = self.repository.append_lifecycle_event(
            lifecycle_payload
        )
        self.repository.append_niche_membership(
            {
                "species_id": state.species_id,
                "niche_path": state.niche_path,
                "state": state.state,
                "event_index": observation.observed_after_event_index,
            }
        )
        self._enforce_active_cap(state.niche_path, observation.observed_after_event_index)
        return SpeciesTransition(
            species_id=state.species_id,
            strategy_id=state.strategy_id,
            niche_path=state.niche_path,
            from_state=from_state,
            to_state=state.state,
            reason=reason,
            event_index=observation.observed_after_event_index,
            support_count=len(support),
            family_count=len({str(row["family_id"]) for row in support}),
            utility=utility,
            lifecycle_event_id=lifecycle_event_id,
        )

    def _utility(self, observation: OutcomeObservation) -> float:
        config = dict(self.policy.snapshot.learning_config)
        return (
            observation.recovery_gain
            - config["locality_penalty"] * observation.locality_cost
            - config["change_penalty"] * observation.changed_item_count
        )

    def _enforce_active_cap(self, niche: str, event_index: int) -> None:
        active = [
            state
            for state in self._species_by_key.values()
            if state.niche_path == niche and state.state == "stable"
        ]
        if len(active) <= self.active_species_cap:
            return
        ranked = sorted(
            active,
            key=lambda state: (
                fmean(float(row["utility"]) for row in state.later_positive),
                state.species_id,
            ),
        )
        for state in ranked[: len(active) - self.active_species_cap]:
            state.state = "retired"
            self.repository.append_lifecycle_event(
                {
                    "subject_id": state.species_id,
                    "subject_kind": "intent_species",
                    "from_state": "stable",
                    "to_state": "retired",
                    "reason": "active_species_cap",
                    "event_index": event_index,
                    "niche_path": niche,
                }
            )

    def _load_species(self) -> None:
        for row in self.repository.rows("species"):
            payload = row["payload"]
            key = (
                str(payload["strategy_id"]),
                str(payload["effect"]),
                str(payload["compiler_version"]),
                str(payload["proposer_model_hash"]),
            )
            self._species_by_key[key] = _SpeciesState(
                species_id=str(payload["species_id"]),
                key=key,
                strategy_id=str(payload["strategy_id"]),
                niche_path=str(payload["niche_path"]),
                producing_case_id=str(payload["producing_case_id"]),
                producing_event_index=int(payload["producing_event_index"]),
                state="candidate",
                evidence=[],
            )
        lifecycle = sorted(
            self.repository.rows("lifecycle"),
            key=lambda row: (
                int(row["payload"].get("event_index", -1)),
                str(row["event_id"]),
            ),
        )
        by_id = {state.species_id: state for state in self._species_by_key.values()}
        for row in lifecycle:
            payload = row["payload"]
            state = by_id.get(str(payload.get("subject_id", "")))
            if state is None:
                continue
            state.state = str(payload["to_state"])
            evidence = payload.get("evidence")
            if isinstance(evidence, Mapping) and not any(
                old.get("observation_id") == evidence.get("observation_id")
                for old in state.evidence
            ):
                state.evidence.append(dict(evidence))
        for state in self._species_by_key.values():
            self.policy.set_niche_status(
                state.niche_path,
                {
                    "candidate": NicheStatus.PROBATION,
                    "probation": NicheStatus.PROBATION,
                    "stable": NicheStatus.STABLE,
                    "retired": NicheStatus.RETIRED,
                }.get(state.state, NicheStatus.COLD),
            )
            if state.state == "stable":
                self.chain_governor.admit_strategy(state.strategy_id)

    def _load_chains(self) -> None:
        rows = sorted(
            self.repository.rows("chain_attempt"),
            key=lambda row: (
                int(row["payload"]["event_index"]),
                str(row["event_id"]),
            ),
        )
        if rows and self.chain_governor.decisions:
            raise ValueError(
                "chain governor already has state while repository needs replay"
            )
        fields = set(ChainAttemptInput.__dataclass_fields__)
        for row in rows:
            payload = row["payload"]
            if not fields <= set(payload):
                raise ValueError("persisted chain attempt is incomplete")
            self.chain_governor.record_attempt(
                ChainAttemptInput(**{name: payload[name] for name in fields})
            )


def evolution_report(
    *,
    repository: EvolutionRepository,
    selections: Sequence[EvolutionSelection],
    updates: Sequence[EvolutionUpdate],
    chain_decisions: Sequence[ChainGovernanceDecision],
) -> dict[str, object]:
    payload = {
        "protocol": "cmd-neuro-symbolic-memory-evolution-v4",
        "model_calls": 0,
        "selection_count": len(selections),
        "outcome_count": sum(len(update.observation_ids) for update in updates),
        "species_transition_count": sum(
            len(update.species_transitions) for update in updates
        ),
        "stable_chain_count": sum(
            decision.lifecycle == "stable" for decision in chain_decisions
        ),
        "active_species": list(repository.active_species()),
        "active_niche_memberships": list(
            repository.active_niche_memberships()
        ),
        "active_chains": list(repository.active_chains()),
        "policy_snapshot_sha256": (
            updates[-1].policy_snapshot.snapshot_sha256
            if updates
            else None
        ),
        "repository_sha256": repository.repository_hash(),
        "selections": [selection.to_mapping() for selection in selections],
        "updates": [
            {
                "observation_ids": list(update.observation_ids),
                "policy_snapshot_sha256": update.policy_snapshot.snapshot_sha256,
                "species_transitions": [
                    asdict(transition)
                    for transition in update.species_transitions
                ],
            }
            for update in updates
        ],
        "chain_decisions": [
            decision.to_dict() for decision in chain_decisions
        ],
    }
    return {**payload, "report_sha256": content_sha256(payload)}


__all__ = [
    "EvolutionSelection",
    "EvolutionUpdate",
    "NeuroSymbolicEvolutionEngine",
    "SpeciesTransition",
    "evolution_report",
]
