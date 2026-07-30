from __future__ import annotations

import math

import pytest

from cmd_audit.counterfactual.actions import PipelineAction
from cmd_audit.counterfactual.operators import OperatorSpec
from cmd_audit.repair.evolution import (
    AnchorRegressionTracker,
    ArmCaseOutcome,
    EvolutionCoordinator,
    build_revision_anchor_set,
    derive_weight_snapshot,
    make_runtime_evidence,
    operator_weight,
    paired_skill_dominance,
    promotion_decision,
    retrieve_revisions,
)
from cmd_audit.repair.operator_library import (
    AppendOnlyEvolutionStore,
    EVOLUTION_ARMS,
    OperatorSpecRecord,
    PatternRecord,
    RevisionAnchorSet,
    TapeCandidate,
    build_experience_tape,
)


def _outcomes(store: AppendOnlyEvolutionStore, case_id: str):
    return {
        arm: ArmCaseOutcome(
            arm_id=arm,
            case_id=case_id,
            library_version_id=store.head(arm).library_version_id,
            attempted_revision_ids=(),
        )
        for arm in EVOLUTION_ARMS
    }


def test_canonical_spec_hash_ignores_key_order_and_explicit_defaults():
    left = OperatorSpec.from_dict(
        {
            "steps": [
                {"generation_point": 0, "action": "retrieval_error"}
            ]
        }
    )
    right = OperatorSpec.from_dict(
        {
            "params": {"item_signal_hints": {}},
            "steps": [
                {
                    "action": "retrieval_error",
                    "generation_point": 0,
                    "transform": "add_from_store",
                    "select": "missed_candidates",
                }
            ],
        }
    )
    assert OperatorSpecRecord.from_operator(left).spec_hash == (
        OperatorSpecRecord.from_operator(right).spec_hash
    )


def test_top3_tape_is_deduplicated_and_deterministic():
    specs = [
        OperatorSpec.single(0, PipelineAction.RETRIEVAL_ERROR),
        OperatorSpec.single(0, PipelineAction.INJECTION_ERROR),
        OperatorSpec.single(0, PipelineAction.GRANULARITY_ERROR),
        OperatorSpec.single(0, PipelineAction.SAFETY_ERROR),
    ]
    tape, records = build_experience_tape(
        case_id="case-1",
        pre_repair_snapshot_hash="snapshot",
        discovery_config_hash="config",
        candidates=(
            TapeCandidate(specs[0], 0.2, 2),
            TapeCandidate(specs[0], 0.3, 3),
            TapeCandidate(specs[1], 0.4, 1),
            TapeCandidate(specs[2], 0.1, 1),
            TapeCandidate(specs[3], 0.09, 1),
        ),
        scorer_version="judge-v1",
        created_after_all_arm_outcomes=True,
    )
    assert len(tape.selected_specs) == 3
    assert len(set(tape.selected_specs)) == 3
    assert tuple(record.spec_hash for record in records) == tape.selected_specs
    assert tape.candidate_manifest[0].recovery_gain == 0.4
    duplicate_rows = [
        item for item in tape.candidate_manifest
        if item.spec_hash == OperatorSpecRecord.from_operator(specs[0]).spec_hash
    ]
    assert sum(item.selected_rank is not None for item in duplicate_rows) == 1


def test_activation_starts_after_producing_case_and_no_update_stays_empty():
    store = AppendOnlyEvolutionStore()
    pattern = PatternRecord("p1", "proto", "alpha beta", "features", "v1")
    store.append_pattern(pattern)
    coordinator = EvolutionCoordinator(store, pattern_catalog_hash="catalog")
    spec = OperatorSpec.single(0, PipelineAction.RETRIEVAL_ERROR)
    tape, records = build_experience_tape(
        case_id="case-1",
        pre_repair_snapshot_hash="snapshot",
        discovery_config_hash="config",
        candidates=(TapeCandidate(spec, 0.4, 1),),
        scorer_version="judge-v1",
        created_after_all_arm_outcomes=True,
    )
    coordinator.commit_case(
        case_id="case-1",
        case_index=1,
        outcomes=_outcomes(store, "case-1"),
        tape=tape,
        selected_spec_records=records,
        matched_pattern_ids=("p1",),
        recurrent_family_id_audit_only="family-a",
    )
    patterned = store.head("patterned")
    assert retrieve_revisions(
        store, patterned, case_index=1, matched_pattern_ids=("p1",)
    ) == ()
    assert len(
        retrieve_revisions(
            store, patterned, case_index=2, matched_pattern_ids=("p1",)
        )
    ) == 1
    assert store.head("no_update").active_revision_ids == ()
    assert len(store.head("unkeyed_global").active_revision_ids) == 1


def test_only_later_runtime_execution_changes_operator_weight():
    store = AppendOnlyEvolutionStore()
    coordinator = EvolutionCoordinator(store, pattern_catalog_hash="catalog")
    spec = OperatorSpec.single(0, PipelineAction.RETRIEVAL_ERROR)
    tape, records = build_experience_tape(
        case_id="case-1",
        pre_repair_snapshot_hash="snapshot",
        discovery_config_hash="config",
        candidates=(TapeCandidate(spec, 0.4, 1),),
        scorer_version="judge-v1",
        created_after_all_arm_outcomes=True,
    )
    # Patterned needs a Pattern match; this test uses the unkeyed revision.
    coordinator.commit_case(
        case_id="case-1",
        case_index=1,
        outcomes=_outcomes(store, "case-1"),
        tape=tape,
        selected_spec_records=records,
        matched_pattern_ids=("p1",),
        recurrent_family_id_audit_only="family-a",
    )
    revision_id = store.head("unkeyed_global").active_revision_ids[0]
    neutral = derive_weight_snapshot(
        store,
        arm_id="unkeyed_global",
        revision_id=revision_id,
        library_version=store.head("unkeyed_global").library_version_id,
    )
    assert neutral.success_count == neutral.failure_count == 0
    assert neutral.weight == pytest.approx(0.05)
    revision = store.revisions[revision_id]
    store.append_evidence(
        make_runtime_evidence(
            revision=revision,
            case_id="case-2",
            library_version_before_case=store.head(
                "unkeyed_global"
            ).library_version_id,
            recovery_gain=0.2,
            rollout_cost=1,
            recurrent_family_id_audit_only="family-b",
        )
    )
    updated = derive_weight_snapshot(
        store,
        arm_id="unkeyed_global",
        revision_id=revision_id,
        library_version=store.head("unkeyed_global").library_version_id,
    )
    assert updated.success_count == 1
    assert updated.failure_count == 0
    assert updated.weight == pytest.approx(math.sqrt(0.05))
    with pytest.raises(ValueError, match="producing case"):
        make_runtime_evidence(
            revision=revision,
            case_id="case-1",
            library_version_before_case="L1",
            recovery_gain=1.0,
            rollout_cost=1,
            recurrent_family_id_audit_only="family-a",
        )


def test_unchanged_operator_weight_can_be_snapshotted_across_versions():
    store = AppendOnlyEvolutionStore()
    coordinator = EvolutionCoordinator(store, pattern_catalog_hash="catalog")
    spec = OperatorSpec.single(0, PipelineAction.RETRIEVAL_ERROR)
    for case_index in (1, 2):
        case_id = f"case-{case_index}"
        tape, records = build_experience_tape(
            case_id=case_id,
            pre_repair_snapshot_hash=f"snapshot-{case_index}",
            discovery_config_hash="config",
            candidates=(TapeCandidate(spec, 0.4, 1),),
            scorer_version="judge-v1",
            created_after_all_arm_outcomes=True,
        )
        coordinator.commit_case(
            case_id=case_id,
            case_index=case_index,
            outcomes=_outcomes(store, case_id),
            tape=tape,
            selected_spec_records=records,
            matched_pattern_ids=("p1",),
            recurrent_family_id_audit_only=f"family-{case_index}",
        )
    version_ids = store._version_order["unkeyed_global"]
    first_weight_ids = store.library_versions[version_ids[-2]].weight_snapshot_ids
    second_weight_ids = store.library_versions[version_ids[-1]].weight_snapshot_ids
    assert first_weight_ids
    assert second_weight_ids
    assert first_weight_ids != second_weight_ids


def test_operator_weight_known_beta_quantiles():
    assert operator_weight(0, 0) == pytest.approx(0.05)
    assert operator_weight(1, 0) == pytest.approx(math.sqrt(0.05))
    assert operator_weight(0, 1) == pytest.approx(1 - math.sqrt(0.95))


def test_paired_skill_dominance_is_conservative():
    assert paired_skill_dominance(
        [
            (True, False, 1, 1),
            (True, False, 1, 1),
            (True, False, 1, 1),
            (True, True, 1, 2),
        ],
        preserves_incumbent_anchors=True,
    )
    assert not paired_skill_dominance(
        [
            (True, False, 1, 1),
            (True, False, 1, 1),
            (True, False, 1, 1),
            (False, True, 1, 1),
        ],
        preserves_incumbent_anchors=True,
    )


def test_stable_retirement_requires_two_consecutive_anchor_regressions():
    tracker = AnchorRegressionTracker()
    creation = (True, True, True, True)
    regression = (True, True, False, True)
    assert not tracker.record_checkpoint(
        "revision-1",
        creation_vector=creation,
        replay_vector=regression,
    )
    assert tracker.record_checkpoint(
        "revision-1",
        creation_vector=creation,
        replay_vector=regression,
    )


def test_clean_anchor_checkpoint_resets_regression_streak():
    tracker = AnchorRegressionTracker()
    creation = (True, True, True, True)
    assert not tracker.record_checkpoint(
        "revision-1",
        creation_vector=creation,
        replay_vector=(True, False, True, True),
    )
    assert not tracker.record_checkpoint(
        "revision-1",
        creation_vector=creation,
        replay_vector=creation,
    )
    assert not tracker.record_checkpoint(
        "revision-1",
        creation_vector=creation,
        replay_vector=(True, False, True, True),
    )


def _create_unkeyed_revision(
    store: AppendOnlyEvolutionStore,
    coordinator: EvolutionCoordinator,
    *,
    producing_case_id: str,
) -> str:
    spec = OperatorSpec.single(0, PipelineAction.RETRIEVAL_ERROR)
    tape, records = build_experience_tape(
        case_id=producing_case_id,
        pre_repair_snapshot_hash=f"snapshot-{producing_case_id}",
        discovery_config_hash="config",
        candidates=(TapeCandidate(spec, 0.4, 1),),
        scorer_version="judge-v1",
        created_after_all_arm_outcomes=True,
    )
    coordinator.commit_case(
        case_id=producing_case_id,
        case_index=1,
        outcomes=_outcomes(store, producing_case_id),
        tape=tape,
        selected_spec_records=records,
        matched_pattern_ids=("p1",),
        recurrent_family_id_audit_only="family-producing",
    )
    return store.head("unkeyed_global").active_revision_ids[0]


def test_promotion_requires_three_successes_across_two_families_and_excludes_producer():
    store = AppendOnlyEvolutionStore()
    coordinator = EvolutionCoordinator(store, pattern_catalog_hash="catalog")
    revision_id = _create_unkeyed_revision(
        store, coordinator, producing_case_id="case-0"
    )
    revision = store.revisions[revision_id]
    library_version_id = store.head("unkeyed_global").library_version_id

    # The producing case can never validate its own revision, so it is
    # structurally excluded from the promotion count.
    with pytest.raises(ValueError, match="producing case"):
        make_runtime_evidence(
            revision=revision,
            case_id="case-0",
            library_version_before_case=library_version_id,
            recovery_gain=1.0,
            rollout_cost=1,
            recurrent_family_id_audit_only="family-producing",
        )

    def _add_success(case_id: str, family_id: str) -> None:
        store.append_evidence(
            make_runtime_evidence(
                revision=revision,
                case_id=case_id,
                library_version_before_case=library_version_id,
                recovery_gain=0.5,
                rollout_cost=1,
                recurrent_family_id_audit_only=family_id,
            )
        )

    # Two distinct post-creation successful validations: not promoted.
    _add_success("case-1", "family-a")
    _add_success("case-2", "family-a")
    two_case_decision = promotion_decision(
        store,
        revision_id,
        paired_noninferior=True,
        preserves_incumbent_anchors=True,
    )
    assert not two_case_decision.eligible
    assert two_case_decision.reason == "needs_three_successful_cases"

    # Three successes, but all from the same recurrence family: not promoted.
    _add_success("case-3", "family-a")
    three_case_one_family_decision = promotion_decision(
        store,
        revision_id,
        paired_noninferior=True,
        preserves_incumbent_anchors=True,
    )
    assert not three_case_one_family_decision.eligible
    assert three_case_one_family_decision.reason == "needs_two_recurrence_families"

    # A fourth success from a second recurrence family: now promotable.
    _add_success("case-4", "family-b")
    cross_family_decision = promotion_decision(
        store,
        revision_id,
        paired_noninferior=True,
        preserves_incumbent_anchors=True,
    )
    assert cross_family_decision.eligible
    assert cross_family_decision.reason == "eligible"
    assert len(cross_family_decision.validation_case_ids) == 3
    assert "case-0" not in cross_family_decision.validation_case_ids
    assert "case-4" in cross_family_decision.validation_case_ids
    assert len(set(cross_family_decision.family_ids)) == 2


def test_anchor_set_has_exactly_four_legal_members():
    store = AppendOnlyEvolutionStore()
    common = dict(
        stable_revision_id="revision-x",
        family_ids_audit_only=("family-a", "family-b"),
        creation_outcome_vector=(True, True, True, True),
        created_at_library_version="L1",
    )

    # Duplicate case collapses producing + validation to three distinct
    # members: rejected.
    with pytest.raises(ValueError, match="four distinct cases"):
        store.append_anchor_set(
            RevisionAnchorSet(
                anchor_set_id="anchor-three",
                producing_case_id="case-0",
                validation_case_ids=("case-0", "case-2", "case-3"),
                pre_repair_snapshot_hashes=("h0", "h0", "h2", "h3"),
                **common,
            )
        )

    # Four validation cases (plus the producing case) is five members:
    # rejected.
    with pytest.raises(ValueError, match="exactly three validation cases"):
        store.append_anchor_set(
            RevisionAnchorSet(
                anchor_set_id="anchor-five",
                producing_case_id="case-0",
                validation_case_ids=("case-1", "case-2", "case-3", "case-4"),
                pre_repair_snapshot_hashes=("h0", "h1", "h2", "h3", "h4"),
                **common,
            )
        )

    # Exactly four distinct members (producing + three validation): legal.
    store.append_anchor_set(
        RevisionAnchorSet(
            anchor_set_id="anchor-four",
            producing_case_id="case-0",
            validation_case_ids=("case-1", "case-2", "case-3"),
            pre_repair_snapshot_hashes=("h0", "h1", "h2", "h3"),
            **common,
        )
    )
    assert "anchor-four" in store.anchor_sets

    # Anchor preservation blocks a promotion that regresses any one of the
    # four anchor cases: promotion_decision treats this as a hard gate,
    # independent of the successful-cases/cross-family criteria.
    store2 = AppendOnlyEvolutionStore()
    coordinator = EvolutionCoordinator(store2, pattern_catalog_hash="catalog")
    revision_id = _create_unkeyed_revision(
        store2, coordinator, producing_case_id="case-0"
    )
    revision = store2.revisions[revision_id]
    library_version_id = store2.head("unkeyed_global").library_version_id
    for case_id, family_id in (
        ("case-1", "family-a"),
        ("case-2", "family-b"),
        ("case-3", "family-a"),
    ):
        store2.append_evidence(
            make_runtime_evidence(
                revision=revision,
                case_id=case_id,
                library_version_before_case=library_version_id,
                recovery_gain=0.5,
                rollout_cost=1,
                recurrent_family_id_audit_only=family_id,
            )
        )
    regressed_decision = promotion_decision(
        store2,
        revision_id,
        paired_noninferior=True,
        preserves_incumbent_anchors=False,
    )
    assert not regressed_decision.eligible
    assert regressed_decision.reason == "incumbent_anchor_regression"

    clean_decision = promotion_decision(
        store2,
        revision_id,
        paired_noninferior=True,
        preserves_incumbent_anchors=True,
    )
    assert clean_decision.eligible


def test_two_consecutive_anchor_regressions_retire_a_stable_revision():
    store = AppendOnlyEvolutionStore()
    coordinator = EvolutionCoordinator(store, pattern_catalog_hash="catalog")
    revision_id = _create_unkeyed_revision(
        store, coordinator, producing_case_id="case-0"
    )
    revision = store.revisions[revision_id]
    library_version_id = store.head("unkeyed_global").library_version_id
    for case_id, family_id in (
        ("case-1", "family-a"),
        ("case-2", "family-b"),
        ("case-3", "family-a"),
    ):
        store.append_evidence(
            make_runtime_evidence(
                revision=revision,
                case_id=case_id,
                library_version_before_case=library_version_id,
                recovery_gain=0.5,
                rollout_cost=1,
                recurrent_family_id_audit_only=family_id,
            )
        )
    _, anchor = coordinator.promote_revision(
        revision_id,
        paired_noninferior=True,
        preserves_incumbent_anchors=True,
        pre_repair_snapshot_hashes={
            "case-0": "h0",
            "case-1": "h1",
            "case-2": "h2",
            "case-3": "h3",
        },
        effective_after_case_id="case-3",
    )
    assert revision_id in store.head("unkeyed_global").stable_revision_ids

    tracker = AnchorRegressionTracker()
    creation_vector = anchor.creation_outcome_vector
    regression_vector = (True, True, False, True)

    # One regression: does not retire, revision stays stable.
    assert not tracker.record_checkpoint(
        revision_id,
        creation_vector=creation_vector,
        replay_vector=regression_vector,
    )
    assert revision_id in store.head("unkeyed_global").stable_revision_ids

    # A clean checkpoint in between resets the streak.
    assert not tracker.record_checkpoint(
        revision_id,
        creation_vector=creation_vector,
        replay_vector=creation_vector,
    )

    # First regression after the reset: still does not retire.
    assert not tracker.record_checkpoint(
        revision_id,
        creation_vector=creation_vector,
        replay_vector=regression_vector,
    )
    assert revision_id in store.head("unkeyed_global").stable_revision_ids

    # Second consecutive regression: tracker signals retirement, and wiring
    # that signal into the store actually retires the stable revision.
    assert tracker.record_checkpoint(
        revision_id,
        creation_vector=creation_vector,
        replay_vector=regression_vector,
    )
    coordinator.retire_revision(
        revision_id,
        reason_code="two_consecutive_anchor_regressions",
        effective_after_case_id="case-4",
    )
    final = store.head("unkeyed_global")
    assert revision_id in final.retired_revision_ids
    assert revision_id not in final.active_revision_ids
    assert revision_id not in final.stable_revision_ids
