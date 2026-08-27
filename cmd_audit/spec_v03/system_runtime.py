"""End-to-end, gold-free prequential CMD runtime coordination.

This module is intentionally a serving boundary: it accepts a public
``DecisionView``, a bound backbone prediction, and a materialized memory state.
It never imports repair-case constructors, evaluator sidecars, or outcome
matrices.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Callable, Mapping

from cmd_audit.repair.ecc import EccRepairReceipt

from .contracts import DecisionView, canonical_sha256
from .ecology_runtime import EcologyRuntime, Settlement
from .repair_stream import MemoryState, OperatorSpec, execute_operator, operator_catalog
from .router_stage5 import BackbonePrediction
from .runtime_pipeline import PipelineDecision, RuntimePipeline
from .syndrome_runtime import audit_structural_telemetry


SYSTEM_SNAPSHOT_SCHEMA = "cmd-spec-v03-system-runtime-v1"


@dataclass(frozen=True)
class CommitResult:
    committed: bool
    conflicted: bool
    expected_before_root: str
    current_root: str
    after_root: str


class VersionedMemoryStore:
    """Single-root memory store with explicit compare-and-swap commits."""

    def __init__(self, initial_state: MemoryState) -> None:
        self._state = initial_state

    @property
    def state(self) -> MemoryState:
        return self._state

    @property
    def root(self) -> str:
        return self._state.root

    def replace_current(self, state: MemoryState) -> None:
        """Apply an external memory advance; useful for real concurrent writers."""
        self._state = state

    def commit(self, *, before_root: str, shadow_state: MemoryState) -> CommitResult:
        current = self._state.root
        if current != before_root:
            return CommitResult(False, True, before_root, current, current)
        self._state = shadow_state
        return CommitResult(True, False, before_root, shadow_state.root, shadow_state.root)


@dataclass(frozen=True)
class RuntimeGates:
    root_passed: bool
    invariants_passed: bool
    safety_passed: bool
    locality_cost: int
    locality_passed: bool
    resolved_syndrome: bool

    @property
    def accepted(self) -> bool:
        return (
            self.root_passed
            and self.invariants_passed
            and self.safety_passed
            and self.locality_passed
            and self.resolved_syndrome
        )


@dataclass(frozen=True)
class AbstainRecord:
    event_index: int
    case_id: str
    before_root: str
    reason: str
    record_sha256: str


@dataclass(frozen=True)
class RuntimeEventResult:
    event_index: int
    settlements: tuple[Settlement, ...]
    decision: PipelineDecision
    commit: CommitResult | None
    gates: RuntimeGates | None
    receipt: EccRepairReceipt | None
    pending: "PendingRepairOutcome | None"
    abstain: AbstainRecord | None


@dataclass(frozen=True)
class PendingRepairOutcome:
    """Immediate branch telemetry; it is not an observed repair receipt."""

    pending_id: str
    selected_at_event_index: int
    matures_at_event_index: int
    syndrome_id: str
    incident_id: str
    selection_id: str
    selected_skill_revision_id: str
    probe_id: str
    before_root: str
    shadow_root: str
    branch_after_root: str
    gates: RuntimeGates
    commit: CommitResult
    observed_store_root: str


@dataclass(frozen=True)
class MaturedObservation:
    pending_id: str
    observed_event_index: int
    recurrence_after_commit: bool = False
    safety_violation: bool = False
    integrity_violation: bool = False
    provenance: Mapping[str, object] | None = None


def _operator(operator_id: str) -> OperatorSpec:
    for spec in operator_catalog():
        if spec.operator_id == operator_id:
            return spec
    raise ValueError("selected runtime operator is not in the frozen catalog")


def _locality(before: MemoryState, shadow: MemoryState, spec: OperatorSpec) -> int:
    if before == shadow:
        return 0
    changed = {
        name for name, different in (
            ("projection_order", before.projection_order != shadow.projection_order),
            ("projection_index", before.projection_index != shadow.projection_index),
            ("scope_projection", before.scope_projection != shadow.scope_projection),
            ("cache_event_ids", before.cache_event_ids != shadow.cache_event_ids),
            ("supersession_edges", before.supersession_edges != shadow.supersession_edges),
            ("quarantine_set", before.quarantine_set != shadow.quarantine_set),
        ) if different
    }
    # A contract names a logical mutation surface; redundant materialized
    # indexes do not multiply its locality cost.  Extra changed surfaces fail.
    contracts = {
        "process_restore": ({"projection_order", "projection_index"}, 2),
        "process_replay_order": ({"projection_order", "projection_index"}, 2),
        "process_rebuild_index": ({"projection_index"}, 1),
        "process_scope_repair": ({"scope_projection"}, 1),
        "process_cache_invalidate": ({"cache_event_ids"}, 1),
        "state_supersede_lineage": ({"projection_order", "projection_index", "supersession_edges"}, 2),
        "poison_quarantine_audit": ({"projection_order", "projection_index", "quarantine_set"}, 1),
    }
    allowed, cost = contracts.get(spec.operator_id, (set(), spec.locality_bound + 1))
    return cost if changed <= allowed else spec.locality_bound + 1


def _rebased_decision(decision: DecisionView, state: MemoryState) -> DecisionView:
    """Bind the public structural observation to a shadow root for its gate."""
    observation = dict(decision.observation)
    current = dict(observation["current_state"])  # validated by pipeline first
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


class PrequentialCMDRuntime:
    """Coordinates route-only selection, COW execution, gates, CAS, and maturity."""

    def __init__(
        self,
        initial_state: MemoryState,
        *,
        pipeline: RuntimePipeline | None = None,
        ecology: EcologyRuntime | None = None,
        shadow_executor: Callable[[MemoryState, OperatorSpec], MemoryState] = execute_operator,
    ) -> None:
        self.pipeline = pipeline or RuntimePipeline()
        if ecology is None:
            ecology = EcologyRuntime(self.pipeline.router, model_id=self.pipeline.model_id)
        if ecology.router is not self.pipeline.router:
            raise ValueError("pipeline and ecology must share the exact route-only router")
        self.ecology = ecology
        self.store = VersionedMemoryStore(initial_state)
        self._shadow_executor = shadow_executor
        self._decision_log: list[dict[str, object]] = []
        self._commit_log: list[dict[str, object]] = []
        self._abstains: list[AbstainRecord] = []
        self._pending_outcomes: dict[str, PendingRepairOutcome] = {}
        self._register_frozen_library()

    def _register_frozen_library(self) -> None:
        existing = self.ecology.skills
        if not existing:
            self.ecology.import_frozen_registry(
                self.pipeline.frozen_registry, self.pipeline.frozen_skill_library, event_index=0,
            )
            return
        for skill in self.pipeline.frozen_skill_library:
            record = existing.get(skill.skill_revision_id)
            if record is None:
                self.ecology.seed_frozen_skill(skill, event_index=0)
            elif record.content != skill:
                raise ValueError("ecology frozen skill conflicts with pipeline library")

    @property
    def decision_log(self) -> tuple[Mapping[str, object], ...]:
        return tuple(dict(row) for row in self._decision_log)

    @property
    def commit_log(self) -> tuple[Mapping[str, object], ...]:
        return tuple(dict(row) for row in self._commit_log)

    @property
    def pending_outcomes(self) -> tuple[PendingRepairOutcome, ...]:
        return tuple(self._pending_outcomes[key] for key in sorted(self._pending_outcomes))

    def process(
        self,
        decision: DecisionView,
        prediction: BackbonePrediction | None = None,
        *,
        observed_after_event_index: int | None = None,
        matured_observations: tuple[MaturedObservation, ...] = (),
        before_commit: Callable[[VersionedMemoryStore], None] | None = None,
        development_zero_backbone: bool = False,
    ) -> RuntimeEventResult:
        """Process event ``t``; only receipts mature before the new route."""
        if decision.event_index < 0:
            raise ValueError("event index must be non-negative")
        # The runtime learns only from observations that have arrived by this
        # event.  Validate the whole batch before finalizing any receipt so a
        # malformed future observation cannot cause a partial update.
        if any(observation.observed_event_index > decision.event_index for observation in matured_observations):
            raise ValueError("matured observation cannot be observed after the current event")
        for observation in matured_observations:
            self.finalize_matured(
                observation.pending_id, observation.observed_event_index,
                recurrence_after_commit=observation.recurrence_after_commit,
                safety_violation=observation.safety_violation,
                integrity_violation=observation.integrity_violation,
                provenance=observation.provenance,
            )
        settlements = self.ecology.settle_before(decision.event_index)
        before = self.store.state
        eligible_skill_ids = tuple(
            skill.skill_revision_id for skill in self.ecology.eligible_skills(decision.event_index)
        )
        pipeline_decision = self.pipeline.decide(
            decision,
            before,
            prediction,
            development_zero_backbone=development_zero_backbone,
            eligible_skill_revision_ids=eligible_skill_ids,
        )
        if pipeline_decision.abstained:
            body = {
                "event_index": decision.event_index, "case_id": decision.case_id,
                "before_root": before.root, "reason": pipeline_decision.abstain_reason,
            }
            abstain = AbstainRecord(**body, record_sha256=canonical_sha256(body))
            self._abstains.append(abstain)
            self._decision_log.append({"kind": "abstain", **asdict(abstain)})
            return RuntimeEventResult(decision.event_index, settlements, pipeline_decision, None, None, None, None, abstain)

        selection = pipeline_decision.selection_handle
        failure = pipeline_decision.failure_deposit
        if selection is None or failure is None or pipeline_decision.backbone_prediction is None:
            raise RuntimeError("non-abstaining pipeline decision is missing binding data")
        candidates = tuple(self.pipeline.frozen_skill(skill_id) for skill_id in pipeline_decision.candidates.skill_revision_ids)
        self.ecology.deposit_failure(failure, event_index=decision.event_index)
        self.ecology.register_selection(
            selection, failure=failure, skills=candidates,
            pre_action_prior=pipeline_decision.backbone_prediction.scores[selection.selected_skill_revision_id],
        )
        dispatch = pipeline_decision.executor_dispatch()
        if dispatch is None or dispatch.operator_id is None:
            raise RuntimeError("non-abstaining decision has no selected dispatch")
        spec = _operator(dispatch.operator_id)
        shadow = self._shadow_executor(before.clone(), spec)
        gates = self._gate(decision, before, shadow, spec)
        if gates.accepted and before_commit is not None:
            before_commit(self.store)
        commit = self.store.commit(before_root=before.root, shadow_state=shadow) if gates.accepted else CommitResult(
            False, False, before.root, self.store.root, before.root,
        )
        maturity = decision.event_index + 1 if observed_after_event_index is None else observed_after_event_index
        if maturity <= decision.event_index:
            raise ValueError("a pending outcome must mature strictly after its selection event")
        pending_body = {
            "selected_at_event_index": decision.event_index,
            "matures_at_event_index": maturity,
            "syndrome_id": pipeline_decision.syndrome.ecc_syndrome.syndrome_id,
            "incident_id": pipeline_decision.syndrome.ecc_syndrome.incident_id,
            "selection_id": selection.selection_id,
            "selected_skill_revision_id": selection.selected_skill_revision_id,
            "probe_id": str(self.pipeline.frozen_skill(selection.selected_skill_revision_id).success_probe["probe_id"]),
            "before_root": before.root,
            "shadow_root": shadow.root,
            "branch_after_root": shadow.root if commit.committed else before.root,
            "gates": gates,
            "commit": commit,
            "observed_store_root": self.store.root,
        }
        pending_hash_body = {
            **pending_body, "gates": asdict(gates), "commit": asdict(commit),
        }
        pending = PendingRepairOutcome(
            pending_id="runtime-pending-" + canonical_sha256(pending_hash_body), **pending_body,
        )
        self._pending_outcomes[pending.pending_id] = pending
        self._decision_log.append({
            "kind": "selection", "event_index": decision.event_index, "case_id": decision.case_id,
            "selection_id": selection.selection_id, "selected_skill_revision_id": selection.selected_skill_revision_id,
            "before_root": before.root,
        })
        self._commit_log.append({"event_index": decision.event_index, "gates": asdict(gates), "commit": asdict(commit), "observed_store_root": self.store.root, "pending_id": pending.pending_id})
        return RuntimeEventResult(decision.event_index, settlements, pipeline_decision, commit, gates, None, pending, None)

    def finalize_matured(
        self, pending_id: str, observed_event_index: int, *, recurrence_after_commit: bool = False,
        safety_violation: bool = False, integrity_violation: bool = False,
        provenance: Mapping[str, object] | None = None,
    ) -> EccRepairReceipt:
        """Turn a matured external observation into the first actual receipt."""
        pending = self._pending_outcomes.get(pending_id)
        if pending is None:
            raise ValueError("unknown or already finalized pending outcome")
        if observed_event_index < pending.matures_at_event_index:
            raise ValueError("pending outcome is not mature")
        branch_committed = pending.commit.committed and not safety_violation and not integrity_violation
        receipt_body = {
            "syndrome_id": pending.syndrome_id,
            "incident_id": pending.incident_id,
            "selection_id": pending.selection_id,
            "selected_skill_revision_id": pending.selected_skill_revision_id,
            "probe_id": pending.probe_id,
            "observed_after_event_index": observed_event_index,
            "before_root": pending.before_root,
            "shadow_root": pending.shadow_root,
            "after_root": pending.branch_after_root if branch_committed else pending.before_root,
            "resolved_syndrome": pending.gates.resolved_syndrome and branch_committed,
            "invariants_passed": pending.gates.invariants_passed and not integrity_violation,
            "committed": branch_committed,
            "rolled_back": not branch_committed,
            "safety_violation": (not pending.gates.safety_passed) or safety_violation,
            "locality_cost": float(pending.gates.locality_cost),
            "recurrence_after_commit": recurrence_after_commit if branch_committed else False,
            "provenance": {
                "runtime": "cmd-spec-v03-prequential-v1",
                "pending_id": pending.pending_id,
                "observed_store_root": pending.observed_store_root,
                "cas_conflict": pending.commit.conflicted,
                "integrity_violation": integrity_violation,
                **({} if provenance is None else dict(provenance)),
            },
        }
        receipt = EccRepairReceipt(
            receipt_id="runtime-receipt-" + canonical_sha256(receipt_body), **receipt_body,
        )
        self.ecology.submit_receipt(receipt)
        del self._pending_outcomes[pending_id]
        return receipt

    def right_censor(self, horizon_event_index: int) -> tuple[str, ...]:
        """Censor unobserved selected actions without manufacturing an outcome."""
        selection_ids = set(self.ecology.right_censor(horizon_event_index))
        censored = tuple(
            pending_id for pending_id, pending in self._pending_outcomes.items()
            if pending.selection_id in selection_ids
        )
        for pending_id in censored:
            del self._pending_outcomes[pending_id]
        return tuple(sorted(censored))

    def _gate(self, decision: DecisionView, before: MemoryState, shadow: MemoryState, spec: OperatorSpec) -> RuntimeGates:
        invariant = shadow.immutable_source_log == before.immutable_source_log and shadow.audit_log == before.audit_log
        active_untrusted = {
            event.event_id for event in shadow.audit_log
            if event.actor_scope == "untrusted" and event.event_id in shadow.projection_order
        }
        audit_ids = {event.event_id for event in shadow.audit_log}
        safety = invariant and not active_untrusted and set(shadow.quarantine_set) <= audit_ids
        locality_cost = _locality(before, shadow, spec)
        locality_passed = locality_cost <= spec.locality_bound
        # A malformed shadow never gets a second chance through diagnosis: the
        # immutable-log gate is authoritative and fail-closed.
        resolved = False
        if invariant:
            descriptor = audit_structural_telemetry(_rebased_decision(decision, shadow), shadow)
            resolved = descriptor.classification == "clean"
        return RuntimeGates(shadow.root != before.root, invariant, safety, locality_cost, locality_passed, resolved)

    @property
    def snapshot(self) -> dict[str, object]:
        payload = {
            "schema_version": SYSTEM_SNAPSHOT_SCHEMA,
            "memory_root": self.store.root,
            "ecology": self.ecology.snapshot,
            "pending_outcomes": [asdict(row) for row in self.pending_outcomes],
            "decision_log": self._decision_log,
            "commit_log": self._commit_log,
            "abstains": [asdict(row) for row in self._abstains],
        }
        return {**payload, "snapshot_sha256": canonical_sha256(payload)}

    @staticmethod
    def verify_snapshot(snapshot: Mapping[str, object]) -> None:
        required = {"schema_version", "memory_root", "ecology", "pending_outcomes", "decision_log", "commit_log", "abstains", "snapshot_sha256"}
        if set(snapshot) != required or snapshot.get("schema_version") != SYSTEM_SNAPSHOT_SCHEMA:
            raise ValueError("system runtime snapshot uses an unsupported schema")
        payload = dict(snapshot)
        claimed = payload.pop("snapshot_sha256")
        if claimed != canonical_sha256(payload):
            raise ValueError("system runtime snapshot hash mismatch")
        ecology = snapshot["ecology"]
        if not isinstance(ecology, Mapping):
            raise ValueError("system runtime snapshot ecology is invalid")
        EcologyRuntime.from_snapshot(ecology)


CMDSystemRuntime = PrequentialCMDRuntime
