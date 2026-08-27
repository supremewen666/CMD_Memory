from __future__ import annotations

import json
from pathlib import Path

import pytest

from cmd_audit.spec_v03.backbone_provider import BackboneProviderConfig, DeterministicDevelopmentProvider, ProviderBudget
from cmd_audit.spec_v03.event_order import compile_event_order
from cmd_audit.spec_v03.experiment_matrix import STAGE5_VARIANTS
from cmd_audit.spec_v03.prequential_executor import RuntimeOrderManifest
from cmd_audit.spec_v03.repair_stream import build_intervention, compile_repair_case, iter_public_episodes
from cmd_audit.spec_v03.runtime_bundle import deserialize
from cmd_audit.spec_v03.stage5_executor import Stage5ExecutionConfig, Stage5Executor, Stage5Receipt


def _inputs():
    episode = next(iter_public_episodes("halumem", Path("data/external/group_a")))
    cases = tuple(compile_repair_case(episode, build_intervention(episode, template, seed=91)) for template in ("clean", "drop", "explicit_supersede", "untrusted_injection"))
    bundles = tuple(deserialize(case.public_mapping()) for case in cases)
    order = RuntimeOrderManifest.from_mapping(json.loads(json.dumps(compile_event_order(cases, seed=93, schedule="stationary", maturity_delay=1).to_mapping())))
    return bundles, order


def _provider():
    return DeterministicDevelopmentProvider(BackboneProviderConfig(model_id="development-hash-provider", snapshot="development-non-model-v1", environment="DEVELOPMENT", max_output_tokens=64, endpoint=None), ProviderBudget(max_requests=20, max_total_tokens=1_000_000))


class _Feedback:
    def observe(self, **kwargs):
        kwargs.pop("case")
        return Stage5Receipt(**kwargs, utility=0.75, provenance={"channel": "fixture"})


class _Oracle:
    sealed = True

    def select_legal(self, *, candidate_skill_revision_ids, **_kwargs):
        return sorted(candidate_skill_revision_ids)[0]


class _UnsealedOracle(_Oracle):
    sealed = False


class _LateFeedback(_Feedback):
    def observe(self, **kwargs):
        kwargs.pop("case")
        return Stage5Receipt(**{**kwargs, "observed_after_event_index": kwargs["observed_after_event_index"] + 1}, utility=0.75)


def test_stage5_runs_all_arms_with_shared_backbone_predictions_and_selected_only_delayed_feedback() -> None:
    bundles, order = _inputs()
    executor = Stage5Executor(Stage5ExecutionConfig("stage5-fixture", "development-hash-provider", 97), _provider(), _Feedback(), sealed_oracle_provider=_Oracle())
    report = executor.run(bundles, order)

    assert tuple(arm.arm for arm in report.arms) == STAGE5_VARIANTS
    assert all(arm.status == "COMPLETE" for arm in report.arms)
    assert all(len(arm.selection_records) == len(order.rows) for arm in report.arms)
    assert all(receipt.selected_at_event_index < receipt.observed_after_event_index == receipt.settled_before_event_index for arm in report.arms for receipt in arm.receipt_records)
    active = [record for record in report.arms[0].selection_records if record.selected_skill_revision_id]
    assert {record.backbone_prediction_sha256 for record in active} == set(report.backbone_prediction_sha256s)
    score_vectors = [
        tuple(record.backbone_scores for record in arm.selection_records)
        for arm in report.arms
    ]
    assert all(vector == score_vectors[0] for vector in score_vectors[1:])
    expected_modes = {
        "random_legal": "random_legal", "best_global": "best_global", "global_thompson": "beta_thompson",
        "niche_thompson": "beta_thompson", "contextual_bandit": "linucb", "oracle_legal": "sealed_oracle",
    }
    for arm in report.arms:
        active_modes = {record.selection_mode for record in arm.selection_records if record.selected_skill_revision_id}
        if arm.arm in expected_modes:
            assert active_modes == {expected_modes[arm.arm]}
        else:
            assert active_modes <= {"observable_fallback", "residual", "exploration", "hierarchy", "ghost_hierarchy"}


def test_stage5_oracle_fails_closed_without_explicit_provider() -> None:
    bundles, order = _inputs()
    report = Stage5Executor(Stage5ExecutionConfig("stage5-no-oracle", "development-hash-provider", 101), _provider(), _Feedback()).run(bundles, order)
    oracle = next(arm for arm in report.arms if arm.arm == "oracle_legal")
    assert oracle.status == "UNSUPPORTED"
    assert not oracle.selection_records

    unsealed = Stage5Executor(Stage5ExecutionConfig("stage5-unsealed-oracle", "development-hash-provider", 101), _provider(), _Feedback(), sealed_oracle_provider=_UnsealedOracle()).run(bundles, order)
    assert next(arm for arm in unsealed.arms if arm.arm == "oracle_legal").status == "UNSUPPORTED"


def test_stage5_same_event_cannot_update_and_seed_replays() -> None:
    bundles, order = _inputs()
    config = Stage5ExecutionConfig("stage5-replay", "development-hash-provider", 103)
    first = Stage5Executor(config, _provider(), _Feedback(), sealed_oracle_provider=_Oracle()).run(bundles, order)
    second = Stage5Executor(config, _provider(), _Feedback(), sealed_oracle_provider=_Oracle()).run(bundles, order)

    assert first == second
    for arm in first.arms:
        assert all(receipt.selected_at_event_index < receipt.settled_before_event_index == receipt.observed_after_event_index for receipt in arm.receipt_records)


def test_stage5_best_global_is_a_frozen_calibration_prior_and_maturity_must_match_order() -> None:
    bundles, order = _inputs()
    report = Stage5Executor(Stage5ExecutionConfig("stage5-frozen-prior", "development-hash-provider", 107), _provider(), _Feedback(), sealed_oracle_provider=_Oracle()).run(bundles, order)
    best = next(arm for arm in report.arms if arm.arm == "best_global")
    assert best.algorithm_snapshot["beta"] == []
    assert all(record.selection_mode == "best_global" for record in best.selection_records if record.selected_skill_revision_id)

    with pytest.raises(ValueError, match="bound to the selected Stage 5 action"):
        Stage5Executor(Stage5ExecutionConfig("stage5-late", "development-hash-provider", 109), _provider(), _LateFeedback(), sealed_oracle_provider=_Oracle()).run(bundles, order)
