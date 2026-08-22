from __future__ import annotations

import pytest

from experiments.v4_feedback_settlement import (
    FeedbackSettlementLedger,
    PendingSelection,
    TypedFollowup,
    ingest_followups,
)
from experiments.v4_run_checkpoint import RunCheckpoint, RunCheckpointStore
from experiments.v4_prequential_runner import V4PrequentialRunner
from tests.experiments.test_v4_prequential_runner import _cases, _evaluator, _ghost_partitions


def _pending() -> PendingSelection:
    return PendingSelection(
        arm_id="full_v4",
        selection_id="selection-1",
        case_id="case-1",
        family_id="family-a",
        intent_id="intent-1",
        graph_sha256="g" * 64,
        pre_policy_snapshot_sha256="p" * 64,
        probe_id="probe-1",
        selected_at_event_index=10,
        effect="verify",
        decision_mapping={"selection_id": "selection-1"},
        evidence_contract_sha256="c" * 64,
    )


def _followup(**overrides: object) -> TypedFollowup:
    values: dict[str, object] = {
        "feedback_id": "feedback-1",
        "arm_id": "full_v4",
        "selection_id": "selection-1",
        "case_id": "case-1",
        "family_id": "family-a",
        "intent_id": "intent-1",
        "graph_sha256": "g" * 64,
        "pre_policy_snapshot_sha256": "p" * 64,
        "probe_id": "probe-1",
        "selected_at_event_index": 10,
        "effect": "verify",
        "observed_after_event_index": 12,
        "provenance": "live-typed-followup-v1",
        "exposure_state_sha256": "e" * 64,
        "post_state_sha256": "s" * 64,
        "evidence_contract_sha256": "c" * 64,
        "typed_reward": 0.8,
        "locality_cost": 0.0,
        "changed_item_count": 0,
        "valid": True,
        "rolled_back": False,
    }
    values.update(overrides)
    return TypedFollowup(**values)  # type: ignore[arg-type]


def test_typed_feedback_settlement_survives_restart_and_is_idempotent(tmp_path) -> None:
    path = tmp_path / "settlement.json"
    pending = _pending()
    FeedbackSettlementLedger(path).register(pending)

    reopened = FeedbackSettlementLedger(path)
    accepted = reopened.settle(_followup())
    assert accepted.status == "accepted"
    assert reopened.pending_count == 0

    replayed = FeedbackSettlementLedger(path).settle(_followup())
    assert replayed.status == "duplicate"


@pytest.mark.parametrize(
    "field",
    (
        "selection_id",
        "intent_id",
        "graph_sha256",
        "pre_policy_snapshot_sha256",
        "probe_id",
        "evidence_contract_sha256",
    ),
)
def test_feedback_settlement_rejects_unbound_lineage(tmp_path, field: str) -> None:
    ledger = FeedbackSettlementLedger(tmp_path / "settlement.json")
    ledger.register(_pending())
    value: object = "different" if field != "selection_id" else "selection-other"
    receipt = ledger.settle(_followup(**{field: value}))
    assert receipt.status == "rejected"
    assert receipt.reason == f"mismatched_{field}" or receipt.reason == "unknown_or_consumed_selection"
    assert ledger.pending_count == 1


def test_feedback_settlement_rejects_stale_and_shadow_derived_followup() -> None:
    with pytest.raises(ValueError, match="after its selection"):
        _followup(observed_after_event_index=10)
    with pytest.raises(ValueError, match="shadow-derived"):
        _followup(provenance="shadow-judge-v1")


def test_feedback_id_collision_and_consumed_selection_are_rejected(tmp_path) -> None:
    ledger = FeedbackSettlementLedger(tmp_path / "settlement.json")
    pending = _pending()
    ledger.register(pending)
    assert ledger.settle(_followup()).status == "accepted"
    assert ledger.settle(_followup(typed_reward=0.1)).reason == "feedback_id_payload_collision"
    with pytest.raises(ValueError, match="consumed selection"):
        ledger.register(pending)


def test_restart_context_rejection_keeps_pending_feedback_unconsumed(tmp_path) -> None:
    path = tmp_path / "settlement.json"
    FeedbackSettlementLedger(path).register(_pending())
    reopened = FeedbackSettlementLedger(path)
    assert reopened.reject(_followup(), "restart_context_unavailable").status == "rejected"
    assert reopened.pending_count == 1
    assert FeedbackSettlementLedger(path).settle(_followup()).status == "duplicate"


@pytest.mark.parametrize("effect", ("demote", "annotate_conflict", "verify"))
def test_feedback_effect_is_identity_bound(tmp_path, effect: str) -> None:
    ledger = FeedbackSettlementLedger(tmp_path / f"{effect}.json")
    pending = PendingSelection(
        **{**_pending().__dict__, "effect": effect, "selection_id": f"selection-{effect}"}
    )
    ledger.register(pending)
    feedback = _followup(selection_id=pending.selection_id, effect=effect)
    assert ledger.settle(feedback).status == "accepted"
    ledger = FeedbackSettlementLedger(tmp_path / f"wrong-{effect}.json")
    ledger.register(pending)
    assert ledger.settle(_followup(selection_id=pending.selection_id, effect="verify" if effect != "verify" else "demote")).status == "rejected"


def test_prospective_runner_defers_policy_update_until_selected_feedback(tmp_path) -> None:
    emitted: set[str] = set()

    def followups(pending: tuple[PendingSelection, ...], event_index: int) -> tuple[TypedFollowup, ...]:
        ready = [row for row in pending if row.arm_id == "global_policy" and row.selected_at_event_index < event_index]
        if not ready:
            return ()
        row = ready[0]
        if row.selection_id in emitted:
            return ()
        emitted.add(row.selection_id)
        return (
            TypedFollowup(
                feedback_id=f"feedback:{row.selection_id}",
                arm_id=row.arm_id,
                selection_id=row.selection_id,
                case_id=row.case_id,
                family_id=row.family_id,
                intent_id=row.intent_id,
                graph_sha256=row.graph_sha256,
                pre_policy_snapshot_sha256=row.pre_policy_snapshot_sha256,
                probe_id=row.probe_id,
                selected_at_event_index=row.selected_at_event_index,
                effect=row.effect,
                observed_after_event_index=event_index,
                provenance="live-typed-followup-v1",
                exposure_state_sha256="e" * 64,
                post_state_sha256="s" * 64,
                evidence_contract_sha256=row.evidence_contract_sha256,
                typed_reward=0.8,
                locality_cost=0.0,
                changed_item_count=0,
                valid=True,
                rolled_back=False,
            ),
        )

    result = V4PrequentialRunner(
        _cases(), output_dir=tmp_path, candidate_budget=1, bootstrap_samples=100,
        ghost_evaluator=_evaluator(), ghost_partitions=_ghost_partitions(),
        ghost_feedback_mode="prospective_deployment", typed_followup_provider=followups,
    ).run()
    global_rows = [row for row in result.outcomes if row.arm_id == "global_policy"]
    assert global_rows[0].policy_snapshot_before == global_rows[0].policy_snapshot_after
    assert global_rows[1].policy_snapshot_before != global_rows[0].policy_snapshot_before
    settlement = result.report["typed_feedback_settlement"]
    assert settlement["counts"]["accepted"] == len(_cases()) - 1
    assert settlement["per_effect_update_counts"] == {"demote": 3}


def test_audit_tamper_and_manifest_mismatch_fail_closed(tmp_path) -> None:
    path = tmp_path / "settlement.jsonl"
    FeedbackSettlementLedger(path, manifest_root="manifest-a").register(_pending())
    with pytest.raises(ValueError, match="manifest mismatch"):
        FeedbackSettlementLedger(path, manifest_root="manifest-b")
    path.write_text(path.read_text(encoding="utf-8").replace("selection-1", "selection-x", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        FeedbackSettlementLedger(path, manifest_root="manifest-a")


def test_cli_ingestion_is_idempotent_and_does_not_settle(tmp_path) -> None:
    source = tmp_path / "followups.json"
    import json
    source.write_text(json.dumps([_followup().__dict__]), encoding="utf-8")
    path = tmp_path / "audit.jsonl"
    assert ingest_followups(path, source, manifest_root="m") == 1
    assert ingest_followups(path, source, manifest_root="m") == 1
    assert FeedbackSettlementLedger(path, manifest_root="m").pending_count == 0


def test_checkpoint_ignores_prepared_only_and_binds_stream(tmp_path) -> None:
    checkpoint = RunCheckpoint("run", "manifest", "stream", 1, 10, {}, {}, {}, "head", "pending", {})
    store = RunCheckpointStore(tmp_path / "cp")
    store.commit(checkpoint)
    assert store.load_latest(manifest_sha256="manifest", case_stream_sha256="stream").next_position == 1
    with pytest.raises(ValueError, match="mismatch"):
        store.load_latest(manifest_sha256="other", case_stream_sha256="stream")


def test_prepared_update_keeps_pending_until_committed(tmp_path) -> None:
    ledger = FeedbackSettlementLedger(tmp_path / "audit.jsonl", manifest_root="m")
    ledger.register(_pending())
    assert ledger.prepare_settlement(_followup(), before_root="before", arm_id="full_v4").status == "prepared"
    assert ledger.pending_count == 1
    with pytest.raises(ValueError, match="committed"):
        ledger.accept_prepared(_followup())
    ledger.policy_update_committed("feedback-1", "after")
    assert ledger.accept_prepared(_followup()).status == "accepted"
    assert ledger.pending_count == 0


def test_interrupted_resume_replays_verified_outcome_prefix(tmp_path) -> None:
    interrupted = tmp_path / "interrupted"
    def stop(position: int, _total: int, _case: str) -> None:
        if position == 1: raise RuntimeError("simulated crash")
    with pytest.raises(RuntimeError, match="simulated crash"):
        V4PrequentialRunner(_cases(), output_dir=interrupted, candidate_budget=1, bootstrap_samples=100, ghost_evaluator=_evaluator(), ghost_partitions=_ghost_partitions(), on_case_completed=stop).run()
    resumed = V4PrequentialRunner(_cases(), output_dir=interrupted, candidate_budget=1, bootstrap_samples=100, ghost_evaluator=_evaluator(), ghost_partitions=_ghost_partitions(), run_mode="resume").run()
    clean = V4PrequentialRunner(_cases(), output_dir=tmp_path / "clean", candidate_budget=1, bootstrap_samples=100, ghost_evaluator=_evaluator(), ghost_partitions=_ghost_partitions()).run()
    assert [row.to_mapping() for row in resumed.outcomes] == [row.to_mapping() for row in clean.outcomes]
    for key in ("arm_summaries", "gate", "case_stream_sha256"):
        assert resumed.report[key] == clean.report[key]
