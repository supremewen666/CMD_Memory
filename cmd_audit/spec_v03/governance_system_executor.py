"""Executable, gold-free Stage 7 governance and Stage 9 system tracks.

The module is a thin execution layer over serving-visible ``RuntimeBundle``
objects.  It deliberately has no imports from repair-case construction or
evaluator sidecars.  The single exception is the explicit ``sealed`` oracle
provider boundary used for the oracle upper-bound arm.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from typing import Mapping, Protocol, Sequence

from .contracts import DecisionView, canonical_sha256
from .industry_adapters import (
    AdapterRequest,
    AdapterResponse,
    IndustryAdapter,
    ResourceUsage,
    erskill_adapter,
    memskill_adapter,
    mem0_adapter,
)
from .repair_stream import MemoryState, OperatorSpec, execute_operator, operator_catalog
from .runtime_bundle import RuntimeBundle
from .syndrome_runtime import RuntimeSyndrome, audit_structural_telemetry, decode_ecc_syndrome
from .system_runtime import VersionedMemoryStore, _locality, _rebased_decision


GOVERNANCE_EXECUTOR_SCHEMA = "cmd-spec-v03-governance-system-executor-v1"
STAGE7_VARIANTS = frozenset({
    "detection_only", "in_place", "copy_on_write", "ecc_no_cas", "ecc_cas",
    "full_governance", "oracle_repair",
})
STAGE9_SYSTEMS = frozenset({
    "full_context", "bm25_rag", "cmd_full", "cmd_no_mix_ghost", "cmd_no_ecology",
    "cmd_no_ecc_cas", "memskill", "erskill", "mem0", "no_repair", "oracle",
})
_TRACK_NAMESPACE = {"controlled_a1": "controlled", "controlled_a2": "controlled", "native": "native"}
_NATIVE_STAGE9_SYSTEMS = frozenset({"cmd_full"})


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _operator(operator_id: str) -> OperatorSpec:
    for spec in operator_catalog():
        if spec.operator_id == operator_id:
            return spec
    raise ValueError("selected operator is absent from the frozen operator catalog")


@dataclass(frozen=True)
class ExecutionOrder:
    """One order position, including an explicit deterministic CAS race switch."""

    event_index: int
    cas_conflict: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.event_index, bool) or not isinstance(self.event_index, int) or self.event_index < 0:
            raise ValueError("event_index must be a non-negative integer")
        if not isinstance(self.cas_conflict, bool):
            raise ValueError("cas_conflict must be boolean")


class ProposalProvider(Protocol):
    """A model or policy sees only the closed adapter request."""

    def invoke(self, request: AdapterRequest) -> AdapterResponse: ...


class ProfiledCMDProvider(ProposalProvider, Protocol):
    """CMD ablations must disclose their router/ecology implementation surface."""

    capabilities: tuple[str, ...]

    def invoke_profile(self, request: AdapterRequest, profile: "SystemComponentProfile") -> AdapterResponse: ...


class SealedOracleProvider(ProposalProvider, Protocol):
    """Oracle arms require an intentional evaluator-owned sealed boundary."""

    sealed: bool


class FirstLegalProposalPolicy:
    """A deterministic, serving-visible policy useful for offline wiring tests."""

    capability_id = "builtin:first-legal"
    supported_tracks = ("controlled_a1", "controlled_a2", "native")
    capabilities = ("mix_ghost_router", "global_router", "ecology")

    def invoke(self, request: AdapterRequest) -> AdapterResponse:
        return AdapterResponse("OK", sorted(request.legal_operator_ids)[0], None, ResourceUsage.zero(), self.capability_id).verify_for(request)

    def invoke_profile(self, request: AdapterRequest, profile: "SystemComponentProfile") -> AdapterResponse:
        # The profile is part of the executable contract, not annotation added
        # after routing.  A real provider can use it to select the declared
        # router/ecology path; this deterministic test policy records it too.
        response = self.invoke(request)
        return AdapterResponse(response.status, response.selected_operator_id, response.abstain_reason,
                               response.usage, f"{self.capability_id}:{profile.profile_id}").verify_for(request)


class FullContextProposalPolicy:
    """Select a legal operator from public context only, never an answer key."""

    capability_id = "builtin:full-context-legal-proposal"
    supported_tracks = ("controlled_a1", "controlled_a2", "native")

    def invoke(self, request: AdapterRequest) -> AdapterResponse:
        visible = json.dumps(request.decision, sort_keys=True, separators=(",", ":"))
        index = int(canonical_sha256({"visible_context": visible})[:16], 16) % len(request.legal_operator_ids)
        return AdapterResponse("OK", sorted(request.legal_operator_ids)[index], None, ResourceUsage.zero(), self.capability_id).verify_for(request)


class Bm25LegalProposalPolicy:
    """A lexical baseline restricted to the runtime legal action mask."""

    capability_id = "builtin:bm25-legal-proposal"
    supported_tracks = ("controlled_a1", "controlled_a2", "native")

    def invoke(self, request: AdapterRequest) -> AdapterResponse:
        text = json.dumps(request.decision, sort_keys=True).casefold().replace("_", " ")
        scored = []
        for operator_id in request.legal_operator_ids:
            terms = operator_id.replace("_", " ").split()
            scored.append((sum(text.count(term) for term in terms), operator_id))
        best = max(scored)[1]
        return AdapterResponse("OK", best, None, ResourceUsage.zero(), self.capability_id).verify_for(request)


@dataclass(frozen=True)
class GovernanceComponents:
    detection: bool
    copy_on_write: bool
    ecc_gates: bool
    cas: bool
    receipt_provenance: bool


@dataclass(frozen=True)
class SystemComponentProfile:
    """Auditable Stage 9 composition; required capabilities are fail-closed."""

    profile_id: str
    mix_ghost_router: bool
    ecology: bool
    ecc_cas: bool
    governance_variant: str
    required_capabilities: tuple[str, ...]

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


_COMPONENTS = {
    "detection_only": GovernanceComponents(True, False, False, False, False),
    "in_place": GovernanceComponents(True, False, False, False, False),
    "copy_on_write": GovernanceComponents(True, True, False, False, False),
    "ecc_no_cas": GovernanceComponents(True, True, True, False, False),
    "ecc_cas": GovernanceComponents(True, True, True, True, False),
    "full_governance": GovernanceComponents(True, True, True, True, True),
    "oracle_repair": GovernanceComponents(True, True, True, True, True),
}

_CMD_PROFILES = {
    "cmd_full": SystemComponentProfile(
        "cmd_full:mix-ghost+ecology+ecc-cas", True, True, True, "full_governance",
        ("mix_ghost_router", "ecology"),
    ),
    "cmd_no_mix_ghost": SystemComponentProfile(
        "cmd_no_mix_ghost:global-router+ecology+ecc-cas", False, True, True, "full_governance",
        ("global_router", "ecology"),
    ),
    "cmd_no_ecology": SystemComponentProfile(
        "cmd_no_ecology:mix-ghost+no-ecology+ecc-cas", True, False, True, "full_governance",
        ("mix_ghost_router",),
    ),
    "cmd_no_ecc_cas": SystemComponentProfile(
        "cmd_no_ecc_cas:mix-ghost+ecology+cow", True, True, False, "copy_on_write",
        ("mix_ghost_router", "ecology"),
    ),
}


@dataclass(frozen=True)
class GovernanceRecord:
    schema_version: str
    run_id: str
    variant: str
    case_id: str
    event_index: int
    status: str
    score_namespace: str
    diagnosed_classification: str
    selected_operator_id: str | None
    before_root: str
    shadow_root: str | None
    after_root: str
    invariant_passed: bool | None
    safety_passed: bool | None
    locality_cost: int | None
    locality_passed: bool | None
    resolved_syndrome: bool | None
    committed: bool
    cas_conflicted: bool
    false_commit_ready: bool
    receipt_provenance: Mapping[str, object] | None
    abstain_reason: str | None
    usage: ResourceUsage
    record_sha256: str

    def to_mapping(self) -> dict[str, object]:
        return {**asdict(self), "usage": self.usage.to_mapping()}


@dataclass(frozen=True)
class ResourceLedgerEntry:
    schema_version: str
    run_id: str
    system_id: str
    case_id: str
    track: str
    score_namespace: str
    denominator_included: bool
    status: str
    requested_budget: ResourceUsage
    observed_usage: ResourceUsage
    adapter_revision: str
    entry_sha256: str

    def to_mapping(self) -> dict[str, object]:
        return {
            **asdict(self), "requested_budget": self.requested_budget.to_mapping(),
            "observed_usage": self.observed_usage.to_mapping(),
        }


@dataclass(frozen=True)
class SystemExecutionRecord:
    schema_version: str
    run_id: str
    system_id: str
    track: str
    score_namespace: str
    denominator_included: bool
    component_profile: Mapping[str, object] | None
    governance: GovernanceRecord | None
    adapter_status: str
    adapter_reason: str | None
    resource_ledger: ResourceLedgerEntry
    record_sha256: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "system_id": self.system_id,
            "track": self.track,
            "score_namespace": self.score_namespace,
            "denominator_included": self.denominator_included,
            "component_profile": self.component_profile,
            "governance": None if self.governance is None else self.governance.to_mapping(),
            "adapter_status": self.adapter_status,
            "adapter_reason": self.adapter_reason,
            "resource_ledger": self.resource_ledger.to_mapping(),
            "record_sha256": self.record_sha256,
        }


def _request(*, run_id: str, system_id: str, track: str, decision: DecisionView,
             legal_operator_ids: Sequence[str], budget: ResourceUsage) -> AdapterRequest:
    if track not in _TRACK_NAMESPACE:
        raise ValueError("unsupported Stage 9 track")
    return AdapterRequest.from_decision(
        run_id=run_id, system_id=system_id, track=track, decision=decision,
        legal_operator_ids=tuple(legal_operator_ids), budget=budget,
    )


def _call(provider: ProposalProvider, request: AdapterRequest) -> AdapterResponse:
    try:
        return provider.invoke(request).verify_for(request)
    except (AttributeError, TypeError, ValueError):
        return AdapterResponse("FAILED", None, "provider_invalid_response", ResourceUsage.zero(), "executor:provider").verify_for(request)


def _gates(decision: DecisionView, before: MemoryState, shadow: MemoryState, spec: OperatorSpec) -> tuple[bool, bool, int, bool, bool]:
    invariant = shadow.immutable_source_log == before.immutable_source_log and shadow.audit_log == before.audit_log
    active_untrusted = {
        event.event_id for event in shadow.audit_log
        if event.actor_scope == "untrusted" and event.event_id in shadow.projection_order
    }
    audit_ids = {event.event_id for event in shadow.audit_log}
    safety = invariant and not active_untrusted and set(shadow.quarantine_set) <= audit_ids
    locality_cost = _locality(before, shadow, spec)
    locality = locality_cost <= spec.locality_bound
    resolved = invariant and audit_structural_telemetry(_rebased_decision(decision, shadow), shadow).classification == "clean"
    return invariant, safety, locality_cost, locality, resolved


def _record(**values: object) -> GovernanceRecord:
    body = dict(values)
    body.pop("record_sha256", None)
    return GovernanceRecord(**body, record_sha256=canonical_sha256({
        **body, "usage": body["usage"].to_mapping(),
    }))  # type: ignore[arg-type]


class _FixedResponseProvider:
    """Replays a validated external response into the common governance path."""

    def __init__(self, response: AdapterResponse) -> None:
        self.response = response

    def invoke(self, request: AdapterRequest) -> AdapterResponse:
        return self.response.verify_for(request)


class GovernanceSystemExecutor:
    """Runs exactly the declared Stage 7/9 arms with closed resource records."""

    def execute_stage7(
        self,
        bundle: RuntimeBundle,
        order: ExecutionOrder,
        provider: ProposalProvider | None,
        *,
        variant: str,
        run_id: str,
        track: str = "controlled_a1",
        budget: ResourceUsage | None = None,
        oracle_provider: SealedOracleProvider | None = None,
        request_system_id: str | None = None,
    ) -> GovernanceRecord:
        if variant not in STAGE7_VARIANTS:
            raise ValueError("unsupported Stage 7 variant")
        _text(run_id, "run_id")
        if track not in _TRACK_NAMESPACE:
            raise ValueError("unsupported Stage 7 track")
        budget = budget or ResourceUsage.zero()
        decision = replace(bundle.decision_view, event_index=order.event_index)
        before = bundle.memory_state
        syndrome: RuntimeSyndrome = decode_ecc_syndrome(decision, before)
        components = _COMPONENTS[variant]
        base = {
            "schema_version": GOVERNANCE_EXECUTOR_SCHEMA, "run_id": run_id, "variant": variant,
            "case_id": bundle.case_id, "event_index": order.event_index, "score_namespace": _TRACK_NAMESPACE[track],
            "diagnosed_classification": syndrome.descriptor.classification, "before_root": before.root,
        }
        # Oracle is an evaluator-owned upper bound.  Even a clean runtime
        # observation must not make the arm look executable without the sealed
        # provider that authorizes this otherwise forbidden boundary.
        if variant == "oracle_repair" and (oracle_provider is None or getattr(oracle_provider, "sealed", False) is not True):
            return _record(**base, status="UNSUPPORTED", selected_operator_id=None, shadow_root=None, after_root=before.root,
                           invariant_passed=None, safety_passed=None, locality_cost=None, locality_passed=None,
                           resolved_syndrome=None, committed=False, cas_conflicted=False, false_commit_ready=False,
                           receipt_provenance=None, abstain_reason="sealed_oracle_provider_required", usage=ResourceUsage.zero())
        if variant == "detection_only" or syndrome.abstains:
            return _record(**base, status="OK", selected_operator_id=None, shadow_root=None, after_root=before.root,
                           invariant_passed=None, safety_passed=None, locality_cost=None, locality_passed=None,
                           resolved_syndrome=None, committed=False, cas_conflicted=False, false_commit_ready=False,
                           receipt_provenance=None, abstain_reason="detection_only" if variant == "detection_only" else syndrome.descriptor.classification,
                           usage=ResourceUsage.zero())
        candidates = tuple(
            spec.operator_id for spec in operator_catalog()
            if spec.operator_id != "noop_abstain" and execute_operator(before, spec) != before
        )
        if not candidates:
            return _record(**base, status="OK", selected_operator_id=None, shadow_root=None, after_root=before.root,
                           invariant_passed=None, safety_passed=None, locality_cost=None, locality_passed=None,
                           resolved_syndrome=None, committed=False, cas_conflicted=False, false_commit_ready=False,
                           receipt_provenance=None, abstain_reason="no_legal_operator", usage=ResourceUsage.zero())
        request = _request(run_id=run_id, system_id=request_system_id or variant, track=track, decision=decision,
                           legal_operator_ids=candidates, budget=budget)
        selected_provider: ProposalProvider | None = provider
        if variant == "oracle_repair":
            selected_provider = oracle_provider
        if selected_provider is None:
            return _record(**base, status="UNSUPPORTED", selected_operator_id=None, shadow_root=None, after_root=before.root,
                           invariant_passed=None, safety_passed=None, locality_cost=None, locality_passed=None,
                           resolved_syndrome=None, committed=False, cas_conflicted=False, false_commit_ready=False,
                           receipt_provenance=None, abstain_reason="proposal_provider_unconfigured", usage=ResourceUsage.zero())
        response = _call(selected_provider, request)
        if response.status != "OK" or response.selected_operator_id is None:
            return _record(**base, status=response.status, selected_operator_id=None, shadow_root=None, after_root=before.root,
                           invariant_passed=None, safety_passed=None, locality_cost=None, locality_passed=None,
                           resolved_syndrome=None, committed=False, cas_conflicted=False, false_commit_ready=False,
                           receipt_provenance=None, abstain_reason=response.abstain_reason, usage=response.usage)
        spec = _operator(response.selected_operator_id)
        shadow = execute_operator(before.clone(), spec) if components.copy_on_write else execute_operator(before, spec)
        invariant, safety, locality_cost, locality, resolved = _gates(decision, before, shadow, spec)
        eligible = shadow.root != before.root and (not components.ecc_gates or (invariant and safety and locality and resolved))
        store = VersionedMemoryStore(before)
        conflicted = False
        if eligible and components.cas:
            if order.cas_conflict:
                store.replace_current(shadow)
            commit = store.commit(before_root=before.root, shadow_state=shadow)
            committed, conflicted = commit.committed, commit.conflicted
        elif eligible:
            store.replace_current(shadow)
            committed = True
        else:
            committed = False
        receipt = None
        if components.receipt_provenance:
            receipt = {
                "runtime": GOVERNANCE_EXECUTOR_SCHEMA,
                "operator_id": spec.operator_id,
                "provider_revision": response.adapter_revision,
                "cas_conflicted": conflicted,
                "event_index": order.event_index,
            }
        return _record(**base, status="OK", selected_operator_id=spec.operator_id, shadow_root=shadow.root,
                       after_root=store.root, invariant_passed=invariant, safety_passed=safety,
                       locality_cost=locality_cost, locality_passed=locality, resolved_syndrome=resolved,
                       committed=committed, cas_conflicted=conflicted,
                       false_commit_ready=committed and not (invariant and safety and locality and resolved),
                       receipt_provenance=receipt, abstain_reason=None if committed else ("cas_conflict" if conflicted else "governance_gate_rejected"),
                       usage=response.usage)

    def execute_stage9(
        self,
        bundle: RuntimeBundle,
        order: ExecutionOrder,
        *,
        system_id: str,
        run_id: str,
        track: str,
        budget: ResourceUsage,
        cmd_provider: ProposalProvider | None = None,
        oracle_provider: SealedOracleProvider | None = None,
        industry_adapters: Mapping[str, IndustryAdapter] | None = None,
    ) -> SystemExecutionRecord:
        if system_id not in STAGE9_SYSTEMS:
            raise ValueError("unsupported Stage 9 system")
        if track not in _TRACK_NAMESPACE:
            raise ValueError("unsupported Stage 9 track")
        decision = replace(bundle.decision_view, event_index=order.event_index)
        syndrome = decode_ecc_syndrome(decision, bundle.memory_state)
        legal = tuple(
            spec.operator_id for spec in operator_catalog()
            if spec.operator_id != "noop_abstain" and execute_operator(bundle.memory_state, spec) != bundle.memory_state
        ) if not syndrome.abstains else ()
        request = _request(run_id=run_id, system_id=system_id, track=track, decision=decision,
                           legal_operator_ids=legal or ("noop_abstain",), budget=budget)
        adapters = dict(industry_adapters or {})
        profile = _CMD_PROFILES.get(system_id)
        if track == "native" and system_id not in _NATIVE_STAGE9_SYSTEMS:
            return self._system_record(
                bundle=bundle, run_id=run_id, system_id=system_id, track=track, budget=budget,
                governance=None, adapter_status="UNSUPPORTED", adapter_reason="native_track_unsupported",
                adapter_revision="stage9:track-gate", component_profile=profile,
            )
        if system_id in {"full_context", "bm25_rag", "no_repair", "oracle"}:
            if system_id == "full_context":
                governance = self.execute_stage7(bundle, order, FullContextProposalPolicy(), variant="full_governance", run_id=run_id, track=track, budget=budget)
            elif system_id == "bm25_rag":
                governance = self.execute_stage7(bundle, order, Bm25LegalProposalPolicy(), variant="full_governance", run_id=run_id, track=track, budget=budget)
            elif system_id == "no_repair":
                governance = self.execute_stage7(bundle, order, None, variant="detection_only", run_id=run_id, track=track, budget=budget)
            elif system_id == "oracle":
                governance = self.execute_stage7(bundle, order, None, variant="oracle_repair", run_id=run_id, track=track, budget=budget, oracle_provider=oracle_provider)
            response_status = governance.status
            reason = governance.abstain_reason
            usage = governance.usage
            revision = "governance:" + system_id
        elif profile is not None:
            response, missing = self._cmd_profile_response(cmd_provider, request, profile)
            if missing is not None:
                governance = None
                response_status, reason, usage, revision = "UNSUPPORTED", missing, ResourceUsage.zero(), "cmd:profile-gate"
            else:
                governance = self.execute_stage7(
                    bundle, order, _FixedResponseProvider(response), variant=profile.governance_variant,
                    run_id=run_id, track=track, budget=budget, request_system_id=system_id,
                )
                response_status, reason, usage, revision = governance.status, governance.abstain_reason, governance.usage, response.adapter_revision
        else:
            factory = {"memskill": memskill_adapter, "erskill": erskill_adapter, "mem0": mem0_adapter}[system_id]
            adapter = adapters.get(system_id, factory())
            response = _call(adapter, request)
            if track != "native" and response.status == "OK" and response.selected_operator_id is not None:
                governance = self.execute_stage7(
                    bundle, order, _FixedResponseProvider(response), variant="full_governance",
                    run_id=run_id, track=track, budget=budget, request_system_id=system_id,
                )
                response_status, reason, usage, revision = governance.status, governance.abstain_reason, governance.usage, response.adapter_revision
            else:
                response_status, reason, usage, revision = response.status, response.abstain_reason, response.usage, response.adapter_revision
                governance = None
        return self._system_record(
            bundle=bundle, run_id=run_id, system_id=system_id, track=track, budget=budget,
            governance=governance, adapter_status=response_status, adapter_reason=reason,
            adapter_revision=revision, observed_usage=usage, component_profile=profile,
        )

    @staticmethod
    def _cmd_profile_response(
        provider: ProposalProvider | None,
        request: AdapterRequest,
        profile: SystemComponentProfile,
    ) -> tuple[AdapterResponse, str | None]:
        if provider is None:
            return AdapterResponse("UNSUPPORTED", None, "cmd_provider_unconfigured", ResourceUsage.zero(), "cmd:profile-gate"), "cmd_provider_unconfigured"
        capabilities = getattr(provider, "capabilities", None)
        invoke_profile = getattr(provider, "invoke_profile", None)
        if not isinstance(capabilities, tuple) or not all(isinstance(item, str) for item in capabilities) or not callable(invoke_profile):
            return AdapterResponse("UNSUPPORTED", None, "cmd_profile_api_unconfigured", ResourceUsage.zero(), "cmd:profile-gate"), "cmd_profile_api_unconfigured"
        missing = tuple(capability for capability in profile.required_capabilities if capability not in capabilities)
        if missing:
            return AdapterResponse("UNSUPPORTED", None, "missing_cmd_capabilities:" + ",".join(missing), ResourceUsage.zero(), "cmd:profile-gate"), "missing_cmd_capabilities:" + ",".join(missing)
        try:
            return invoke_profile(request, profile).verify_for(request), None
        except (AttributeError, TypeError, ValueError):
            return AdapterResponse("FAILED", None, "cmd_profile_invalid_response", ResourceUsage.zero(), "cmd:profile-gate"), None

    @staticmethod
    def _system_record(
        *,
        bundle: RuntimeBundle,
        run_id: str,
        system_id: str,
        track: str,
        budget: ResourceUsage,
        governance: GovernanceRecord | None,
        adapter_status: str,
        adapter_reason: str | None,
        adapter_revision: str,
        component_profile: SystemComponentProfile | None,
        observed_usage: ResourceUsage | None = None,
    ) -> SystemExecutionRecord:
        usage = observed_usage or ResourceUsage.zero()
        ledger_body = {
            "schema_version": GOVERNANCE_EXECUTOR_SCHEMA, "run_id": run_id, "system_id": system_id,
            "case_id": bundle.case_id, "track": track, "score_namespace": _TRACK_NAMESPACE[track],
            "denominator_included": True, "status": adapter_status, "requested_budget": budget,
            "observed_usage": usage, "adapter_revision": adapter_revision,
        }
        ledger = ResourceLedgerEntry(**ledger_body, entry_sha256=canonical_sha256({
            **ledger_body, "requested_budget": budget.to_mapping(), "observed_usage": usage.to_mapping(),
        }))
        record_body = {
            "schema_version": GOVERNANCE_EXECUTOR_SCHEMA, "run_id": run_id, "system_id": system_id,
            "track": track, "score_namespace": _TRACK_NAMESPACE[track], "denominator_included": True,
            "component_profile": None if component_profile is None else component_profile.to_mapping(),
            "governance": governance, "adapter_status": adapter_status, "adapter_reason": adapter_reason,
            "resource_ledger": ledger,
        }
        return SystemExecutionRecord(**record_body, record_sha256=canonical_sha256({
            **record_body,
            "component_profile": None if component_profile is None else component_profile.to_mapping(),
            "governance": None if governance is None else governance.to_mapping(),
            "resource_ledger": ledger.to_mapping(),
        }))
