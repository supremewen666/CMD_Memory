from __future__ import annotations

import ast
from dataclasses import asdict
import json
from pathlib import Path

import pytest

from cmd_audit.spec_v03.backbone_provider import BackboneProviderConfig, DeterministicDevelopmentProvider, ProviderBudget
from cmd_audit.spec_v03.contracts import DecisionView, canonical_sha256
from cmd_audit.spec_v03.event_order import compile_event_order
from cmd_audit.spec_v03.experiment_matrix import STAGE5_VARIANTS
from cmd_audit.spec_v03.prequential_executor import RuntimeOrderManifest, RuntimeOrderRow
from cmd_audit.spec_v03.repair_stream import MemoryState, PublicEvent, build_intervention, compile_repair_case, iter_public_episodes
from cmd_audit.spec_v03.runtime_bundle import RuntimeBundle, deserialize
from cmd_audit.spec_v03.runtime_pipeline import RuntimePipeline, build_legal_candidates
from cmd_audit.repair.ghost_ecology import SkillRevision
from cmd_audit.spec_v03.stage5_executor import (
    Stage5ExecutionConfig,
    Stage5Executor,
    Stage5Receipt,
    StructuralDevelopmentStage5FeedbackProvider,
    load_router_snapshot_bundle,
    router_snapshot_bundle_from_stage5_result,
)
from cmd_audit.spec_v03.syndrome_runtime import decode_ecc_syndrome


def _inputs():
    episode = next(iter_public_episodes("halumem", Path("data/external/group_a")))
    cases = tuple(compile_repair_case(episode, build_intervention(episode, template, seed=91)) for template in ("clean", "drop", "explicit_supersede", "untrusted_injection"))
    bundles = tuple(deserialize(case.public_mapping()) for case in cases)
    order = RuntimeOrderManifest.from_mapping(json.loads(json.dumps(compile_event_order(cases, seed=93, schedule="stationary", maturity_delay=1).to_mapping())))
    return bundles, order


def _fast_inputs() -> tuple[tuple, RuntimeOrderManifest]:
    payload = {"content": "runtime memory"}
    payload_sha = canonical_sha256(payload)
    event = PublicEvent("source-event", "public-source", 0, None, "trusted", payload, payload_sha, payload_sha)
    state = MemoryState((event,), (), (), (), ((event.event_id, "trusted"),), (), (), ())
    bundles = []
    rows = []
    for index in range(4):
        case_id = f"fast-case-{index}"
        decision = {
            "event_log": [{
                "event_id": event.event_id, "timestamp": None, "actor_scope": "trusted",
                "content": payload, "authority": "trusted",
                "provenance": {"source_payload_sha256": payload_sha},
            }],
            "current_state": {
                "projection_order": [], "projection_index": [],
                "scope_projection": [[event.event_id, "trusted"]], "cache_event_ids": [],
                "supersession_edges": [], "quarantine_set": [], "state_root": state.root,
            },
            "observable_telemetry": {"event_count": 1, "projection_size": 0},
        }
        bundles.append(RuntimeBundle(
            case_id, "fixture", "episode", f"family-{index}", "lineage",
            (event.event_id,),
            DecisionView(case_id, "fixture", "episode", f"family-{index}", "lineage", index, decision, {"source": payload_sha}, ()),
            state,
        ))
        rows.append(RuntimeOrderRow(case_id, index, "stationary", index + 1, "benign"))
    body = {"seed": 53, "schedule": "stationary", "rows": [
        {"case_id": row.case_id, "event_index": row.event_index, "regime": row.regime,
         "receipt_matures_at": row.receipt_matures_at, "cas_interleaving": row.cas_interleaving}
        for row in rows
    ]}
    return tuple(bundles), RuntimeOrderManifest(53, "stationary", tuple(rows), canonical_sha256(body))


def _provider(model_id: str = "development-hash-provider"):
    return DeterministicDevelopmentProvider(BackboneProviderConfig(model_id=model_id, snapshot="development-non-model-v1", environment="DEVELOPMENT", max_output_tokens=64, endpoint=None), ProviderBudget(max_requests=20, max_total_tokens=1_000_000))


def _sibling_restore_library() -> tuple[SkillRevision, ...]:
    base = RuntimePipeline().frozen_skill_library
    restore = next(skill for skill in base if skill.program["operator_id"] == "process_restore")
    siblings = tuple(
        SkillRevision.create(
            skill_id=f"stage5-external:process_restore:{suffix}",
            program={**restore.program, "external_revision": suffix},
            parameter_schema=restore.parameter_schema,
            preconditions=restore.preconditions,
            postconditions=restore.postconditions,
            success_probe={"probe_id": f"stage5-external-restore-{suffix}"},
            mutation_budget=restore.mutation_budget,
            rollback_program=restore.rollback_program,
            producing_failure_id=f"stage5-external-library-{suffix}",
            derivation_kind="seed",
            state="stable",
        )
        for suffix in ("a", "b")
    )
    return tuple(skill for skill in base if skill is not restore) + siblings


class _Feedback:
    def observe(self, **kwargs):
        kwargs.pop("case")
        kwargs.pop("regime")
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
        kwargs.pop("regime")
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


def test_stage5_can_run_only_the_requested_source_posterior_arms() -> None:
    bundles, order = _fast_inputs()
    report = Stage5Executor(
        Stage5ExecutionConfig(
            "stage5-selected-arms", "development-hash-provider", 113,
            arms=("mix_ghost",),
        ),
        _provider(), _Feedback(), sealed_oracle_provider=_Oracle(),
    ).run(bundles, order)

    assert tuple(arm.arm for arm in report.arms) == ("mix_ghost",)
    with pytest.raises(ValueError, match="unique non-empty supported subset"):
        Stage5ExecutionConfig(
            "stage5-bad-arms", "development-hash-provider", 113, arms=(),
        )


def test_stage5_routes_external_sibling_revisions_without_a_singleton_candidate_shortcut() -> None:
    bundles, order = _inputs()
    library = _sibling_restore_library()
    expected = tuple(
        skill.skill_revision_id for skill in library
        if skill.program["operator_id"] == "process_restore"
    )
    report = Stage5Executor(
        Stage5ExecutionConfig("stage5-external-library", "development-hash-provider", 127),
        _provider(),
        _Feedback(),
        sealed_oracle_provider=_Oracle(),
        skill_library=library,
    ).run(bundles, order)

    for arm in report.arms:
        record = next(
            row for row in arm.selection_records
            if set(expected) <= set(row.candidate_skill_revision_ids)
        )
        assert record.selected_skill_revision_id in record.candidate_skill_revision_ids
    ghost = next(arm for arm in report.arms if arm.arm == "mix_ghost")
    assert next(
        row for row in ghost.selection_records
        if set(expected) <= set(row.candidate_skill_revision_ids)
    ).selected_skill_revision_id is not None


def test_stage5_best_global_is_a_frozen_calibration_prior_and_maturity_must_match_order() -> None:
    bundles, order = _inputs()
    report = Stage5Executor(Stage5ExecutionConfig("stage5-frozen-prior", "development-hash-provider", 107), _provider(), _Feedback(), sealed_oracle_provider=_Oracle()).run(bundles, order)
    best = next(arm for arm in report.arms if arm.arm == "best_global")
    assert best.algorithm_snapshot["beta"] == []
    assert all(record.selection_mode == "best_global" for record in best.selection_records if record.selected_skill_revision_id)

    with pytest.raises(ValueError, match="bound to the selected Stage 5 action"):
        Stage5Executor(Stage5ExecutionConfig("stage5-late", "development-hash-provider", 109), _provider(), _LateFeedback(), sealed_oracle_provider=_Oracle()).run(bundles, order)


def test_stage5_imports_ghost_posteriors_and_keeps_prefix_out_of_scored_suffix() -> None:
    bundles, order = _fast_inputs()
    source = Stage5Executor(
        Stage5ExecutionConfig("stage5-source", "source-model", 131),
        _provider("source-model"), _Feedback(), sealed_oracle_provider=_Oracle(),
    ).run(bundles, order)
    exported = router_snapshot_bundle_from_stage5_result(
        {"arms": [asdict(arm) for arm in source.arms]}, source_model_id="source-model",
    )
    snapshots = load_router_snapshot_bundle(exported)

    target = Stage5Executor(
        Stage5ExecutionConfig(
            "stage5-target", "target-model", 137,
            initial_router_snapshots=snapshots, adaptation_prefix_ratio=0.5,
        ),
        _provider("target-model"), _Feedback(), sealed_oracle_provider=_Oracle(),
    ).run(bundles, order)

    suffix_start = order.rows[2].event_index
    for arm in target.arms:
        assert arm.adaptation_prefix_event_count == 2
        assert arm.scored_suffix_event_count == 2
        assert all(row.event_index >= suffix_start for row in arm.selection_records)
        assert all(row.selected_at_event_index >= suffix_start for row in arm.receipt_records)
    by_arm = {arm.arm: arm for arm in target.arms}
    assert by_arm["mix_ghost"].imported_router_snapshot
    assert by_arm["ghost_hierarchy"].imported_router_snapshot
    assert not by_arm["random_legal"].imported_router_snapshot


def test_stage5_fails_closed_for_incompatible_imported_router_snapshots() -> None:
    bundles, order = _fast_inputs()
    source = Stage5Executor(
        Stage5ExecutionConfig("stage5-source-incompatible", "source-model", 139),
        _provider("source-model"), _Feedback(), sealed_oracle_provider=_Oracle(), skill_library=_sibling_restore_library(),
    ).run(bundles, order)
    source_mix = next(arm.algorithm_snapshot for arm in source.arms if arm.arm == "mix_ghost")
    with pytest.raises(ValueError, match="frozen skill library"):
        Stage5Executor(
            Stage5ExecutionConfig(
                "stage5-target-incompatible", "target-model", 149,
                initial_router_snapshots={"mix_ghost": source_mix},
            ),
            _provider("target-model"), _Feedback(), sealed_oracle_provider=_Oracle(),
        ).run(bundles, order)

    wrong_router = next(arm.algorithm_snapshot for arm in source.arms if arm.arm == "ghost_hierarchy")
    with pytest.raises(ValueError, match="closed mapping"):
        Stage5Executor(
            Stage5ExecutionConfig(
                "stage5-target-wrong-router", "target-model", 151,
                initial_router_snapshots={"mix_ghost": wrong_router},
            ),
            _provider("target-model"), _Feedback(), sealed_oracle_provider=_Oracle(),
        ).run(bundles, order)


def test_structural_development_feedback_replays_only_the_selected_frozen_skill() -> None:
    bundles, _order = _inputs()
    bundle = next(bundle for bundle in bundles if not decode_ecc_syndrome(bundle.decision_view, bundle.memory_state).abstains)
    syndrome = decode_ecc_syndrome(bundle.decision_view, bundle.memory_state)
    selected = build_legal_candidates(bundle.memory_state, syndrome).skill_revision_ids[0]

    receipt = StructuralDevelopmentStage5FeedbackProvider("development-hash-provider").observe(
        selection_id="selected-only", selected_skill_revision_id=selected,
        selected_at_event_index=0, observed_after_event_index=1, case=bundle,
    )

    assert receipt.valid
    assert 0.0 < receipt.utility < 1.0
    assert receipt.provenance is not None
    assert receipt.provenance["selected_skill_revision_id"] == selected
    assert receipt.provenance["immutable_log_preserved"] is True
    assert receipt.provenance["audit_log_preserved"] is True
    assert receipt.provenance["after_structural_syndrome"] == "clean"


def test_structural_receipt_cost_preference_reverses_under_abrupt_recurrence() -> None:
    bundles, _order = _inputs()
    pipeline = RuntimePipeline()
    by_revision = {skill.skill_revision_id: skill for skill in pipeline.frozen_skill_library}
    bundle = next(
        bundle for bundle in bundles
        if decode_ecc_syndrome(bundle.decision_view, bundle.memory_state).descriptor.classification == "process_fault"
    )
    candidates = build_legal_candidates(
        bundle.memory_state, decode_ecc_syndrome(bundle.decision_view, bundle.memory_state),
    ).skill_revision_ids
    by_operator = {by_revision[item].program["operator_id"]: item for item in candidates}
    targeted = next(item for operator, item in by_operator.items() if operator != "process_projection_rebuild")
    rebuild = by_operator["process_projection_rebuild"]
    provider = StructuralDevelopmentStage5FeedbackProvider("development-hash-provider")

    def utility(skill_id: str, regime: str) -> float:
        return provider.observe(
            selection_id=f"{skill_id}:{regime}", selected_skill_revision_id=skill_id,
            selected_at_event_index=0, observed_after_event_index=1,
            case=bundle, regime=regime,
        ).utility

    assert utility(targeted, "stationary") > utility(rebuild, "stationary")
    assert utility(targeted, "abrupt_process_state_poison") < utility(rebuild, "abrupt_process_state_poison")


def test_structural_development_feedback_fails_negative_for_an_unselected_illegal_skill() -> None:
    bundles, _order = _inputs()
    bundle = next(bundle for bundle in bundles if not decode_ecc_syndrome(bundle.decision_view, bundle.memory_state).abstains)
    noop = next(
        skill.skill_revision_id for skill in RuntimePipeline().frozen_skill_library
        if skill.skill_id == "runtime:noop_abstain"
    )

    receipt = StructuralDevelopmentStage5FeedbackProvider("development-hash-provider").observe(
        selection_id="illegal-selected", selected_skill_revision_id=noop,
        selected_at_event_index=0, observed_after_event_index=1, case=bundle,
    )

    assert not receipt.valid
    assert receipt.utility == -1.0
    assert receipt.rolled_back
    assert receipt.provenance is not None
    assert receipt.provenance["shadow_root"] == bundle.memory_state.root


def test_structural_development_feedback_has_no_evaluator_import_and_is_rejected_for_confirmatory() -> None:
    import cmd_audit.spec_v03.stage5_executor as subject

    imported_names = {
        alias.name
        for node in ast.walk(ast.parse(Path(subject.__file__).read_text(encoding="utf-8")))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not {"RepairCase", "InterventionSpec", "ShadowOutcomeMatrix", "EvaluatorOnly"} & imported_names

    with pytest.raises(ValueError, match="confirmatory"):
        Stage5Executor(
            Stage5ExecutionConfig("stage5-confirmatory", "development-hash-provider", 113, execution_mode="CONFIRMATORY"),
            _provider(),
            StructuralDevelopmentStage5FeedbackProvider("development-hash-provider"),
        )
