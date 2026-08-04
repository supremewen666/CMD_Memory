from cmd_audit.counterfactual.actions import PipelineAction
from cmd_audit.counterfactual.operators import OperatorSpec
from cmd_audit.repair.governance import OperatorGovernance
from cmd_audit.repair.failure_memory import AntiPatternRecord, FailureMemoryStore


def _operator(generation_point: int, action: PipelineAction) -> OperatorSpec:
    return OperatorSpec.single(generation_point, action)


def test_operator_content_hash_is_canonical() -> None:
    left = _operator(0, PipelineAction.RETRIEVAL_ERROR).with_item_signal_hint(
        "m2", -1.0
    ).with_item_signal_hint("m1", 1.0)
    right = _operator(0, PipelineAction.RETRIEVAL_ERROR).with_item_signal_hint(
        "m1", 1.0
    ).with_item_signal_hint("m2", -1.0)

    assert left.content_hash() == right.content_hash()


def test_cluster_replay_uses_ci_after_three_observations() -> None:
    governance = OperatorGovernance(seed=7, bootstrap_samples=500)
    decision = governance.admit_with_cluster_replay(
        "fp",
        _operator(0, PipelineAction.RETRIEVAL_ERROR),
        (0.2, 0.3, 0.4, 0.5),
    )

    assert decision.admitted
    assert decision.ci_lower is not None
    assert decision.ci_lower > 0.0
    assert not decision.low_evidence


def test_small_cluster_is_admitted_but_marked_low_evidence() -> None:
    governance = OperatorGovernance()
    decision = governance.admit_with_cluster_replay(
        "fp",
        _operator(0, PipelineAction.INJECTION_ERROR),
        (0.2,),
    )

    assert decision.admitted
    assert decision.low_evidence
    assert decision.ci_lower is None


def test_non_positive_replay_is_rejected() -> None:
    governance = OperatorGovernance()
    decision = governance.admit_with_cluster_replay(
        "fp",
        _operator(0, PipelineAction.GRANULARITY_ERROR),
        (-0.1, 0.0),
    )

    assert not decision.admitted
    assert governance.active_operators("fp") == ()


def test_duplicate_hash_updates_evidence_without_second_shape() -> None:
    governance = OperatorGovernance()
    operator = _operator(0, PipelineAction.RETRIEVAL_ERROR)
    assert governance.admit_with_cluster_replay("fp", operator, (0.2,)).admitted

    duplicate = governance.admit_with_cluster_replay("fp", operator, (0.3,))

    assert duplicate.deduplicated
    assert len(governance.entries("fp")) == 1
    assert governance.entries("fp")[0].replay_observations == 2


def test_active_cap_retires_weakest_shape() -> None:
    governance = OperatorGovernance(active_cap=2)
    operators = (
        _operator(0, PipelineAction.RETRIEVAL_ERROR),
        _operator(0, PipelineAction.INJECTION_ERROR),
        _operator(0, PipelineAction.GRANULARITY_ERROR),
    )
    for index, operator in enumerate(operators):
        governance.admit_with_cluster_replay(
            "fp", operator, (0.1 + index * 0.1,), generation=index
        )

    assert len(governance.active_operators("fp")) == 2
    assert sum(entry.retired for entry in governance.entries("fp")) == 1


def test_consecutive_failures_retire_operator() -> None:
    governance = OperatorGovernance(retirement_patience=2)
    operator = _operator(0, PipelineAction.SAFETY_ERROR)
    decision = governance.admit_with_cluster_replay("fp", operator, (0.2,))
    for generation in range(4):
        governance.record_application(
            "fp",
            decision.operator_hash,
            succeeded=False,
            generation=generation,
        )

    assert governance.active_operators("fp") == ()


def test_eta_probation_lifecycle_and_ranking() -> None:
    governance = OperatorGovernance()
    operator = _operator(0, PipelineAction.SAFETY_ERROR)
    decision = governance.admit_with_cluster_replay("fp", operator, (0.2,))
    entry = governance.entries("fp")[0]

    assert entry.eta == 0.5
    assert entry.lifecycle_status == "probation"
    for generation in range(4):
        governance.record_application(
            "fp",
            decision.operator_hash,
            succeeded=generation < 2,
            generation=generation,
        )

    assert entry.eta == 0.5
    assert entry.lifecycle_status == "active"


def test_anti_pattern_downweights_matching_pair_and_cluster() -> None:
    store = FailureMemoryStore()
    store.record_anti_pattern(
        AntiPatternRecord(
            first_skill_id="seed:retrieval_error",
            second_skill_id="seed:injection_error",
            cluster_id="cluster-a",
            n_support=12,
            ci_upper=-0.1,
            thresholds={"min_support": 10},
            seed=24,
            source_sha256="a" * 64,
            provenance_sha256="b" * 64,
        )
    )

    assert (
        store.chain_pair_weight(
            "retrieval_error",
            "injection_error",
            "cluster-a",
        )
        == 0.25
    )
    assert (
        store.chain_pair_weight(
            "retrieval_error",
            "injection_error",
            "cluster-b",
        )
        == 1.0
    )


def test_failure_memory_store_retrieves_governed_operator() -> None:
    store = FailureMemoryStore()
    operator = _operator(0, PipelineAction.RETRIEVAL_ERROR)
    decision = store.admit_with_cluster_replay(
        "paraphrased query",
        operator,
        (0.4,),
        memory_texts=("The blue key is in Oslo.",),
    )

    specs, _ = store.retrieve_operator_specs(
        "different wording",
        max_depth=2,
        memory_texts=("The blue key is in Oslo.",),
    )

    assert decision.admitted
    assert specs == [operator]
