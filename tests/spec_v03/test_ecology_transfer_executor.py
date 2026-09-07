from __future__ import annotations

import pytest

from cmd_audit.repair.ghost_ecology import ObservableResidualGHOSTRouter, SkillRevision
from cmd_audit.spec_v03.contracts import DecisionView
from cmd_audit.spec_v03.ecology_transfer_executor import (
    EcologyTransferExecutor,
    LifecycleCoverageCandidateProvider,
    STAGE8A_ARMS,
    STAGE8B_ARMS,
    _failure,
)
from cmd_audit.spec_v03.experiment_matrix import STAGE8A_VARIANTS, STAGE8B_VARIANTS
from cmd_audit.spec_v03.prequential_executor import RuntimeOrderManifest, RuntimeOrderRow
from cmd_audit.spec_v03.repair_stream import MemoryState, _make_event, operator_catalog
from cmd_audit.spec_v03.runtime_bundle import RuntimeBundle


def _bundle() -> RuntimeBundle:
    source = _make_event("tiny", "episode", "r1", 0, {"text": "a"}, actor_scope="trusted")
    state = MemoryState((source,), (), (), (), ((source.event_id, "trusted"),), (), (), ())
    observation = {
        "event_log": [{"event_id": source.event_id}],
        "current_state": {"state_root": state.root},
    }
    decision = DecisionView("case", "tiny", "episode", "family", "lineage", 1, observation, {"p": "x"}, ())
    return RuntimeBundle("case", "tiny", "episode", "family", "lineage", ("e1",), decision, state)


def _order() -> RuntimeOrderManifest:
    row = RuntimeOrderRow("case", 0, "stationary", 1, "benign")
    from cmd_audit.spec_v03.contracts import canonical_sha256
    return RuntimeOrderManifest(1, "stationary", (row,), canonical_sha256({"seed": 1, "schedule": "stationary", "rows": [{"case_id": "case", "event_index": 0, "regime": "stationary", "receipt_matures_at": 1, "cas_interleaving": "benign"}]}))


def _skill(*, failure_id: str = "pending", parent: tuple[str, ...] = ()) -> SkillRevision:
    spec = next(item for item in operator_catalog() if item.operator_id == "process_restore")
    return SkillRevision.create(
        skill_id="tiny:restore", program={
            "kind": "cmd-spec-v03-operator", "operator_id": spec.operator_id,
            "operator_family": spec.operator_family, "strategy_id": spec.strategy_id,
            "write_set": spec.write_set, "repair_action": spec.repair_action,
            "write_contract": spec.write_contract,
        },
        parameter_schema={"type": "object"}, preconditions=(), postconditions=(),
        success_probe={"probe_id": "tiny"}, mutation_budget={"locality": 2},
        rollback_program={"action": "restore"}, producing_failure_id=failure_id,
        parent_revision_ids=parent, derivation_kind="structural_revision" if parent else "discovery", state="stable",
    )


class _Candidates:
    def candidates(self, bundle, *, event_index, failure):
        return (_skill(failure_id=failure.failure_id),)


class _Oracle:
    def library(self, bundle, *, event_index):
        return (_skill(),)

    def legal_operator(self, bundle, *, event_index):
        return "process_restore"


class _RevisionCandidates:
    def __init__(self, parent: str) -> None:
        self.parent = parent

    def candidates(self, bundle, *, event_index, failure):
        return (_skill(failure_id=failure.failure_id, parent=(self.parent,)),)


def test_stage6_missing_oracle_is_explicitly_unsupported() -> None:
    result = EcologyTransferExecutor().run_stage6("oracle_library", (_bundle(),), _order())
    assert result.status == "UNSUPPORTED"
    assert result.reason == "sealed_library_oracle_missing"


def test_stage6_candidate_birth_is_gated_and_delayed() -> None:
    # The empty projection is a process fault repaired by process_restore.
    result = EcologyTransferExecutor().run_stage6("add_only", (_bundle(),), _order(), candidate_provider=_Candidates())
    assert result.status == "READY_NO_MODEL_RESULTS"
    assert result.transitions[0][1] == "birth@t+1"
    assert len(result.snapshot_sha256) == 64


@pytest.mark.parametrize("arm", ("no_skill", "seed_frozen", "add_dedup", "full_ecology", "random_key_ecology", "oracle_library"))
def test_stage6_all_nonrevision_arms_compile_without_model_results(arm: str) -> None:
    executor = EcologyTransferExecutor(seed=7)
    kwargs = {"frozen_library": (_skill(),)}
    if arm in {"add_dedup", "full_ecology", "random_key_ecology"}:
        kwargs["candidate_provider"] = _Candidates()
    if arm == "oracle_library":
        kwargs["sealed_library_oracle"] = _Oracle()
    result = executor.run_stage6(arm, (_bundle(),), _order(), **kwargs)
    assert result.status == "READY_NO_MODEL_RESULTS"
    assert result.snapshot_sha256 == EcologyTransferExecutor(seed=7).run_stage6(arm, (_bundle(),), _order(), **kwargs).snapshot_sha256


@pytest.mark.parametrize("arm", ("add_revision", "add_revision_retirement"))
def test_stage6_revision_and_retirement_arms_replay_transitions(arm: str) -> None:
    parent = _skill()
    result = EcologyTransferExecutor().run_stage6(
        arm, (_bundle(),), _order(), frozen_library=(parent,),
        candidate_provider=_RevisionCandidates(parent.skill_revision_id),
    )
    assert any("supersede@t+1" in transition for _skill_id, transition in result.transitions)
    if arm == "add_revision_retirement":
        assert any(transition == "retire@t+1" for _skill_id, transition in result.transitions)


def test_stage6_frozen_coverage_triggers_dedup_and_supersede() -> None:
    bundle = _bundle()
    parent = _skill()
    discovered = _skill(failure_id=_failure(bundle).failure_id)
    dedup = EcologyTransferExecutor().run_stage6(
        "add_dedup", (bundle,), _order(),
        candidate_provider=LifecycleCoverageCandidateProvider((discovered,), mode="dedup"),
    )
    supersede = EcologyTransferExecutor().run_stage6(
        "add_revision", (bundle,), _order(), frozen_library=(parent,),
        candidate_provider=LifecycleCoverageCandidateProvider(
            (discovered,), mode="supersede", parent_library=(parent,),
        ),
    )
    assert sum(transition.startswith("dedup:") for _skill_id, transition in dedup.transitions) == 1
    assert sum(transition.startswith("supersede@t+1:") for _skill_id, transition in supersede.transitions) == 1


def test_stage8_arm_constants_exactly_match_the_frozen_experiment_matrix() -> None:
    assert STAGE8A_ARMS == STAGE8A_VARIANTS == (
        "no_repair", "random_legal", "skill_content_only", "reset_online", "frozen_source",
        "niche_shuffled", "mean_only", "reset_prefix", "source_prefix", "oracle_legal_operator",
    )
    assert STAGE8B_ARMS == STAGE8B_VARIANTS == (
        "seed_only", "source_skills", "target_native_skills", "oracle_library",
    )


@pytest.mark.parametrize("arm", STAGE8A_ARMS)
def test_stage8a_every_frozen_arm_is_executable_with_explicit_isolation(arm: str) -> None:
    source = ObservableResidualGHOSTRouter(allow_development_proxy=True).snapshot
    kwargs = {"source_residual_snapshot": source, "source_skill_library": (_skill(),), "target_prefix_snapshot": source, "sealed_library_oracle": _Oracle()}
    result = EcologyTransferExecutor(seed=9).run_stage8a(arm, (_bundle(),), _order(), **kwargs)
    assert result.status == "READY_NO_MODEL_RESULTS"
    assert result.evidence_state_sha256s == ()
    if arm in {"no_repair", "random_legal", "oracle_legal_operator", "skill_content_only"}:
        assert result.residual_snapshot_sha256 is None
    if arm in {"no_repair", "random_legal", "oracle_legal_operator"}:
        assert result.skill_content_sha256s == ()
    else:
        assert result.skill_content_sha256s


@pytest.mark.parametrize(
    ("arm", "kwargs", "reason"),
    (
        ("skill_content_only", {}, "source_skill_library_missing"),
        ("reset_online", {"source_skill_library": (_skill(),)}, "source_residual_snapshot_missing"),
        ("reset_prefix", {"source_skill_library": (_skill(),), "source_residual_snapshot": ObservableResidualGHOSTRouter().snapshot}, "target_prefix_snapshot_missing"),
        ("oracle_legal_operator", {}, "sealed_library_oracle_missing"),
    ),
)
def test_stage8a_required_capabilities_fail_closed(arm, kwargs, reason) -> None:
    result = EcologyTransferExecutor().run_stage8a(arm, (_bundle(),), _order(), **kwargs)
    assert result.status == "UNSUPPORTED"
    assert result.reason == reason


@pytest.mark.parametrize("arm", STAGE8B_ARMS)
def test_stage8b_every_arm_transfers_content_but_never_evidence_or_residual(arm: str) -> None:
    kwargs = {"seed_library": (_skill(),), "source_library": (_skill(),), "target_candidate_provider": _Candidates(), "sealed_library_oracle": _Oracle()}
    result = EcologyTransferExecutor().run_stage8b(arm, (_bundle(),), _order(), **kwargs)
    assert result.status == "READY_NO_MODEL_RESULTS"
    assert result.skill_content_sha256s
    assert result.evidence_state_sha256s == ()
    assert result.residual_snapshot_sha256 is None


@pytest.mark.parametrize(
    ("arm", "kwargs", "reason"),
    (
        ("seed_only", {}, "library_missing"),
        ("source_skills", {}, "library_missing"),
        ("target_native_skills", {}, "skill_candidate_provider_missing"),
        ("oracle_library", {}, "sealed_library_oracle_missing"),
    ),
)
def test_stage8b_required_capabilities_fail_closed(arm, kwargs, reason) -> None:
    result = EcologyTransferExecutor().run_stage8b(arm, (_bundle(),), _order(), **kwargs)
    assert result.status == "UNSUPPORTED"
    assert result.reason == reason
