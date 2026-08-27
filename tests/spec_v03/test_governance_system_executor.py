from __future__ import annotations

from pathlib import Path

import pytest

from cmd_audit.spec_v03.governance_system_executor import (
    Bm25LegalProposalPolicy,
    ExecutionOrder,
    FirstLegalProposalPolicy,
    GovernanceSystemExecutor,
    STAGE7_VARIANTS,
    STAGE9_SYSTEMS,
)
from cmd_audit.spec_v03.industry_adapters import AdapterResponse, ResourceUsage
from cmd_audit.spec_v03.contracts import DecisionView, canonical_sha256
from cmd_audit.spec_v03.repair_stream import MemoryState, PublicEvent
from cmd_audit.spec_v03.runtime_bundle import RuntimeBundle


def _bundle() -> RuntimeBundle:
    payload = {"content": "public runtime memory"}
    payload_sha = canonical_sha256(payload)
    event = PublicEvent("event-source", "source-1", 0, None, "trusted", payload, payload_sha, payload_sha)
    # A missing source event in the projection is the smallest valid process-fault state.
    state = MemoryState((event,), (), (), (), ((event.event_id, "trusted"),), (), (), ())
    decision = DecisionView(
        case_id="case-process", source_dataset_id="fixture", source_episode_id="episode-1",
        family_id="fixture-family", lineage_id="lineage-1", event_index=0,
        observation={
            "event_log": [{"event_id": event.event_id, "timestamp": None, "actor_scope": "trusted", "content": payload, "authority": "trusted", "provenance": {"source_payload_sha256": payload_sha}}],
            "current_state": {"projection_order": [], "projection_index": [], "scope_projection": [[event.event_id, "trusted"]], "cache_event_ids": [], "supersession_edges": [], "quarantine_set": [], "state_root": state.root},
            "observable_telemetry": {"event_count": 1, "projection_size": 0},
        },
        provenance={"source_sha256": payload_sha}, unsupported_fields=("sealed_fields_omitted",),
    )
    return RuntimeBundle("case-process", "fixture", "episode-1", "fixture-family", "lineage-1", (event.event_id,), decision, state)


@pytest.fixture(scope="module")
def drop_bundle():
    return _bundle()


class _SealedOracle:
    sealed = True

    def invoke(self, request):
        return AdapterResponse("OK", request.legal_operator_ids[0], None, ResourceUsage.zero(), "sealed:test").verify_for(request)


class _MissingEcologyCMD:
    capabilities = ("mix_ghost_router",)

    def invoke(self, request):
        return AdapterResponse("OK", request.legal_operator_ids[0], None, ResourceUsage.zero(), "cmd:incomplete").verify_for(request)

    def invoke_profile(self, request, _profile):
        return self.invoke(request)


class _TrackingCMD(FirstLegalProposalPolicy):
    def __init__(self) -> None:
        self.profile_ids: list[str] = []

    def invoke_profile(self, request, profile):
        self.profile_ids.append(profile.profile_id)
        return super().invoke_profile(request, profile)


class _LegalIndustryAdapter:
    capability_id = "test:industry"
    supported_tracks = ("controlled_a1", "controlled_a2", "native")

    def invoke(self, request):
        return AdapterResponse("OK", request.legal_operator_ids[0], None, ResourceUsage(1, 4, 2, 0.1, 0), self.capability_id).verify_for(request)


def test_stage7_all_variants_are_executable_and_oracle_fails_closed_without_sealed_provider(drop_bundle) -> None:
    bundle = drop_bundle
    executor = GovernanceSystemExecutor()
    for variant in STAGE7_VARIANTS - {"oracle_repair"}:
        record = executor.execute_stage7(bundle, ExecutionOrder(3), FirstLegalProposalPolicy(), variant=variant, run_id="run-7")
        assert record.variant == variant
        assert record.record_sha256
        assert record.before_root == bundle.memory_state.root
    unsupported = executor.execute_stage7(bundle, ExecutionOrder(3), FirstLegalProposalPolicy(), variant="oracle_repair", run_id="run-7")
    assert unsupported.status == "UNSUPPORTED"
    assert unsupported.abstain_reason == "sealed_oracle_provider_required"
    oracle = executor.execute_stage7(bundle, ExecutionOrder(3), None, variant="oracle_repair", run_id="run-7", oracle_provider=_SealedOracle())
    assert oracle.status == "OK" and oracle.committed


def test_governance_component_switches_cover_direct_cow_ecc_and_cas(drop_bundle) -> None:
    bundle = drop_bundle
    executor = GovernanceSystemExecutor()
    direct = executor.execute_stage7(bundle, ExecutionOrder(1), FirstLegalProposalPolicy(), variant="in_place", run_id="r")
    cow = executor.execute_stage7(bundle, ExecutionOrder(1), FirstLegalProposalPolicy(), variant="copy_on_write", run_id="r")
    ecc = executor.execute_stage7(bundle, ExecutionOrder(1), FirstLegalProposalPolicy(), variant="ecc_no_cas", run_id="r")
    raced = executor.execute_stage7(bundle, ExecutionOrder(1, cas_conflict=True), FirstLegalProposalPolicy(), variant="ecc_cas", run_id="r")
    full = executor.execute_stage7(bundle, ExecutionOrder(1), FirstLegalProposalPolicy(), variant="full_governance", run_id="r")
    assert direct.committed and cow.committed and ecc.committed
    assert not direct.receipt_provenance and not cow.receipt_provenance
    assert not ecc.cas_conflicted and raced.cas_conflicted and not raced.committed
    assert full.receipt_provenance and full.invariant_passed and full.safety_passed and full.locality_passed


def test_stage9_namespaces_and_unconfigured_adapters_keep_the_denominator(drop_bundle) -> None:
    bundle = drop_bundle
    executor = GovernanceSystemExecutor()
    budget = ResourceUsage(2, 100, 100, 3.0, 0)
    controlled = executor.execute_stage9(bundle, ExecutionOrder(2), system_id="cmd_full", run_id="r9", track="controlled_a1", budget=budget, cmd_provider=FirstLegalProposalPolicy())
    native = executor.execute_stage9(bundle, ExecutionOrder(2), system_id="lightmem", run_id="r9", track="native", budget=budget)
    assert controlled.score_namespace == "controlled"
    assert native.score_namespace == "native"
    assert native.adapter_status == "UNSUPPORTED"
    assert native.denominator_included and native.resource_ledger.denominator_included
    assert native.resource_ledger.observed_usage == ResourceUsage.zero()


@pytest.mark.parametrize("system_id", ("full_context", "bm25_rag", "cmd_no_mix_ghost", "cmd_no_ecology", "cmd_no_ecc_cas", "no_repair", "oracle"))
def test_stage9_rejects_non_native_arms_without_dropping_denominator(drop_bundle, system_id: str) -> None:
    record = GovernanceSystemExecutor().execute_stage9(
        drop_bundle, ExecutionOrder(2), system_id=system_id, run_id="native-gate", track="native",
        budget=ResourceUsage(1, 10, 10, 1.0, 0), cmd_provider=FirstLegalProposalPolicy(), oracle_provider=_SealedOracle(),
    )
    assert record.adapter_status == "UNSUPPORTED"
    assert record.adapter_reason == "native_track_unsupported"
    assert record.denominator_included and record.governance is None


def test_cmd_profiles_are_distinct_and_missing_capability_fails_closed(drop_bundle) -> None:
    executor = GovernanceSystemExecutor()
    budget = ResourceUsage(1, 10, 10, 1.0, 0)
    provider = _TrackingCMD()
    records = {
        system_id: executor.execute_stage9(
            drop_bundle, ExecutionOrder(2), system_id=system_id, run_id="profiles", track="controlled_a1",
            budget=budget, cmd_provider=provider,
        )
        for system_id in ("cmd_full", "cmd_no_mix_ghost", "cmd_no_ecology")
    }
    profile_ids = {record.component_profile["profile_id"] for record in records.values()}
    assert len(profile_ids) == 3
    assert set(provider.profile_ids) == profile_ids
    assert records["cmd_full"].component_profile["mix_ghost_router"] is True
    assert records["cmd_no_mix_ghost"].component_profile["mix_ghost_router"] is False
    assert records["cmd_no_ecology"].component_profile["ecology"] is False

    incomplete = executor.execute_stage9(
        drop_bundle, ExecutionOrder(2), system_id="cmd_full", run_id="profiles", track="controlled_a1",
        budget=budget, cmd_provider=_MissingEcologyCMD(),
    )
    assert incomplete.adapter_status == "UNSUPPORTED"
    assert incomplete.adapter_reason == "missing_cmd_capabilities:ecology"
    assert incomplete.governance is None


def test_controlled_industry_legal_action_uses_common_governance_but_native_stays_native(drop_bundle) -> None:
    executor = GovernanceSystemExecutor()
    budget = ResourceUsage(2, 10, 10, 1.0, 0)
    adapter = _LegalIndustryAdapter()
    controlled = executor.execute_stage9(
        drop_bundle, ExecutionOrder(5), system_id="lightmem", run_id="industry", track="controlled_a1",
        budget=budget, industry_adapters={"lightmem": adapter},
    )
    native = executor.execute_stage9(
        drop_bundle, ExecutionOrder(5), system_id="lightmem", run_id="industry", track="native",
        budget=budget, industry_adapters={"lightmem": adapter},
    )
    assert controlled.governance is not None
    assert controlled.governance.committed
    assert controlled.governance.invariant_passed and controlled.governance.safety_passed
    assert controlled.resource_ledger.observed_usage == ResourceUsage(1, 4, 2, 0.1, 0)
    assert native.governance is None
    assert native.adapter_status == "OK"


def test_stage9_all_systems_share_budget_and_records_are_closed(drop_bundle) -> None:
    bundle = drop_bundle
    executor = GovernanceSystemExecutor()
    budget = ResourceUsage(1, 10, 10, 1.0, 0)
    for system_id in STAGE9_SYSTEMS:
        record = executor.execute_stage9(
            bundle, ExecutionOrder(4), system_id=system_id, run_id="matrix", track="controlled_a2",
            budget=budget, cmd_provider=FirstLegalProposalPolicy(),
        )
        assert record.denominator_included
        assert record.resource_ledger.requested_budget == budget
        assert record.resource_ledger.observed_usage.within(budget)
        assert record.record_sha256 and record.resource_ledger.entry_sha256


def test_builtin_context_and_bm25_are_legal_proposal_policies_not_answer_readers(drop_bundle) -> None:
    bundle = drop_bundle
    executor = GovernanceSystemExecutor()
    for policy in (FirstLegalProposalPolicy(), Bm25LegalProposalPolicy()):
        record = executor.execute_stage7(bundle, ExecutionOrder(0), policy, variant="full_governance", run_id="safe")
        assert record.status == "OK"
        assert record.selected_operator_id is not None
    source = Path("cmd_audit/spec_v03/governance_system_executor.py").read_text()
    assert "EvaluatorOnly" not in source
    assert "ShadowOutcomeMatrix" not in source
