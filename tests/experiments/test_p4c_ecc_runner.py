from __future__ import annotations

import json
from pathlib import Path

import pytest

from cmd_audit.repair.ghost_ecology import (
    EcologyLedger,
    FailureDeposit,
    GhostEcology,
    PatternResponsibility,
    PatternRevision,
    RegistrySnapshot,
    SkillRevision,
)
from cmd_audit.repair.ecc import EccRepairReceipt
from experiments.p4c_ecc_runner import (
    P4cEccCase,
    P4cEccRunner,
    P4cGhostBinding,
    P4cGhostRouter,
    audit_p4c_run,
    load_p4c_cases,
)


def _observation() -> dict[str, object]:
    return {
        "observation_id": "observation-1",
        "incident_id": "incident-1",
        "observed_at_event_index": 1,
        "state_root": "state-before",
        "source_manifest_root": "manifest-root",
        "process_fault_subtype": "retrieval",
        "observed_order": [],
        "superseding_memory_id": None,
        "superseded_memory_id": None,
        "cas_anomaly": False,
        "influence_anomaly": False,
        "suspect_ids": [],
        "signal_ids": ["retrieval-miss"],
        "provenance": {"detector": "memaudit-v1"},
    }


class _Decision:
    selection_id = "selection-1"
    selected_skill_revision_id = "skill-1"


class _ReceiptOnlyRouter:
    def __init__(self) -> None:
        self.receipts = 0
        self.legacy_observations = 0
        self.selections = 0

    def select(self, case: P4cEccCase, syndrome: object) -> _Decision:
        assert case.case_id == "case-1"
        assert getattr(syndrome, "incident_id") == "incident-1"
        self.selections += 1
        return _Decision()

    def observe_receipt(
        self, decision: _Decision, receipt: object, *, event_index: int
    ) -> dict[str, object]:
        assert decision.selection_id == getattr(receipt, "selection_id")
        assert event_index == 2
        self.receipts += 1
        return {"snapshot_sha256": "router-after"}

    def observe(self, *_args: object, **_kwargs: object) -> None:
        self.legacy_observations += 1
        raise AssertionError("legacy feedback is forbidden in P4C live mode")


class _Store:
    def __init__(self, expected_skill: str = "skill-1") -> None:
        self.root = "state-before"
        self.commits = 0
        self.rollbacks = 0
        self.expected_skill = expected_skill

    def snapshot_root(self) -> str:
        return self.root

    def apply_shadow(self, syndrome: object, selected_skill_revision_id: str) -> None:
        assert selected_skill_revision_id == self.expected_skill
        self.root = "state-shadow"

    def commit_shadow(self) -> None:
        self.commits += 1

    def rollback_shadow(self, before_root: str) -> None:
        self.rollbacks += 1
        self.root = before_root


class _Evaluator:
    def __init__(self) -> None:
        self.answer_replay_calls = 0

    def evaluate_ecc(
        self, syndrome: object, *, before_root: str, shadow_root: str
    ) -> dict[str, object]:
        assert before_root == "state-before" and shadow_root == "state-shadow"
        return {
            "resolved_syndrome": True,
            "invariants_passed": True,
            "safety_violation": False,
            "locality_cost": 0.05,
            "recurrence_after_commit": False,
            "provenance": {"checker": "ecc-v1"},
        }

    def replay_answer(self, *_args: object, **_kwargs: object) -> None:
        self.answer_replay_calls += 1
        raise AssertionError("same-trace answer replay is forbidden")


def test_p4c_live_case_uses_ecc_receipt_only_and_never_answer_replay(
    tmp_path: Path,
) -> None:
    case = P4cEccCase.from_mapping(
        {
            "schema_version": "cmd-p4c-ecc-case-v1",
            "case_id": "case-1",
            "event_index": 2,
            "observation": _observation(),
            "candidates": [
                {
                    "skill_revision_id": "skill-1",
                    "probe_id": "probe:skill-1",
                    "operator_sha256": "a" * 64,
                }
            ],
        }
    )
    router = _ReceiptOnlyRouter()
    store = _Store()
    evaluator = _Evaluator()

    result = P4cEccRunner(
        (case,),
        output_dir=tmp_path,
        router=router,
        store_factory=lambda _case: store,
        evaluator_factory=lambda _case: evaluator,
    ).run()

    assert result["status"] == "success"
    assert result["runtime_uses_gold"] is False
    assert result["same_trace_answer_replay"] is False
    assert router.receipts == 1 and router.legacy_observations == 0
    assert evaluator.answer_replay_calls == 0
    assert store.commits == 1 and store.rollbacks == 0
    receipt = json.loads((tmp_path / "repair_receipts.jsonl").read_text())
    assert receipt["committed"] is True
    runtime_artifacts = (
        (tmp_path / "repair_receipts.jsonl").read_text()
        + (tmp_path / "incidents.jsonl").read_text()
    )
    assert "gold" not in runtime_artifacts.lower()


def _ghost(tmp_path: Path) -> tuple[GhostEcology, FailureDeposit, PatternRevision, SkillRevision, RegistrySnapshot]:
    ecology = GhostEcology(EcologyLedger(tmp_path / "ecology.jsonl"))
    failure = FailureDeposit(
        "failure-1",
        "case-1",
        "audit-family",
        "failure-memory-root",
        (("retrieval-miss", 1.0),),
        "context-root",
        "provenance-root",
    )
    pattern = PatternRevision.create(
        pattern_id="process-fault",
        predicate={"kind": "typed_predicate", "requires": ["retrieval-miss"]},
        feature_signature=("retrieval-miss",),
        derivation_kind="seed",
        state="stable",
    )
    skill = SkillRevision.create(
        skill_id="pipeline-patch",
        program={"kind": "typed_repair_program", "steps": [{"op": "patch_retrieval"}]},
        parameter_schema={},
        preconditions=({"predicate": "retrieval_miss"},),
        postconditions=({"predicate": "syndrome_resolved"},),
        success_probe={"probe_id": "probe:pipeline-patch", "kind": "ecc_parity"},
        mutation_budget={"max_items": 1},
        rollback_program={"kind": "restore_snapshot"},
        producing_failure_id=failure.failure_id,
        state="stable",
    )
    ecology.deposit_failure(failure, event_index=1)
    ecology.propose_pattern(pattern, event_index=2)
    ecology.propose_skill(skill, event_index=3)
    registry = RegistrySnapshot.create(
        epoch=1,
        stable_pattern_revision_ids=(pattern.pattern_revision_id,),
        stable_skill_revision_ids=(skill.skill_revision_id,),
        config_sha256="p4c-config",
    )
    ecology.freeze_registry(registry, event_index=4)
    return ecology, failure, pattern, skill, registry


def test_p4c_real_ghost_posterior_is_updated_only_by_ecc_receipt(
    tmp_path: Path,
) -> None:
    ecology, failure, pattern, skill, registry = _ghost(tmp_path)
    observation = _observation()
    observation["observed_at_event_index"] = 6
    case = P4cEccCase.from_mapping(
        {
            "schema_version": "cmd-p4c-ecc-case-v1",
            "case_id": "case-1",
            "event_index": 7,
            "observation": observation,
            "candidates": [
                {
                    "skill_revision_id": skill.skill_revision_id,
                    "probe_id": skill.success_probe["probe_id"],
                    "operator_sha256": skill.program_sha256,
                }
            ],
        }
    )
    router = P4cGhostRouter(
        ecology,
        {
            case.case_id: P4cGhostBinding(
                failure.failure_id,
                (PatternResponsibility(pattern.pattern_revision_id, 1.0),),
                registry.registry_id,
                ((skill.skill_revision_id, 0.25),),
            )
        },
    )

    result = P4cEccRunner(
        (case,),
        output_dir=tmp_path / "run",
        router=router,
        store_factory=lambda _case: _Store(skill.skill_revision_id),
        evaluator_factory=lambda _case: _Evaluator(),
    ).run()

    assert result["committed"] == 1
    feedback = next(
        row for row in ecology.ledger.events if row["event_type"] == "skill_feedback"
    )
    selection = next(
        row for row in ecology.ledger.events if row["event_type"] == "selection"
    )
    assert selection["payload"]["skill_priors"] == [
        [skill.skill_revision_id, 0.25]
    ]
    assert feedback["payload"]["feedback_kind"] == "ecc_repair_receipt"
    assert "typed_reward" not in feedback["payload"]
    replayed = GhostEcology(EcologyLedger(tmp_path / "ecology.jsonl"))
    assert replayed.router.snapshot == ecology.router.snapshot


def test_p4c_resume_verifies_completed_receipt_prefix_before_skipping(
    tmp_path: Path,
) -> None:
    case = P4cEccCase.from_mapping(
        {
            "schema_version": "cmd-p4c-ecc-case-v1",
            "case_id": "case-1",
            "event_index": 2,
            "observation": _observation(),
            "candidates": [
                {
                    "skill_revision_id": "skill-1",
                    "probe_id": "probe:skill-1",
                    "operator_sha256": "a" * 64,
                }
            ],
        }
    )
    router = _ReceiptOnlyRouter()
    output = tmp_path / "run"
    first = P4cEccRunner(
        (case,),
        output_dir=output,
        router=router,
        store_factory=lambda _case: _Store(),
        evaluator_factory=lambda _case: _Evaluator(),
    ).run()

    resumed = P4cEccRunner(
        (case,),
        output_dir=output,
        router=router,
        store_factory=lambda _case: (_ for _ in ()).throw(
            AssertionError("completed case was executed again")
        ),
        evaluator_factory=lambda _case: (_ for _ in ()).throw(
            AssertionError("completed case was evaluated again")
        ),
        run_mode="resume",
    ).run()

    assert resumed == first
    assert router.selections == 1 and router.receipts == 1

    receipt_path = output / "repair_receipts.jsonl"
    receipt_path.write_text(
        receipt_path.read_text().replace('"committed":true', '"committed":false'),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="receipt"):
        P4cEccRunner(
            (case,),
            output_dir=output,
            router=router,
            store_factory=lambda _case: _Store(),
            evaluator_factory=lambda _case: _Evaluator(),
            run_mode="resume",
        ).run()


def test_p4c_sealed_audit_is_post_runtime_root_bound_and_read_only(
    tmp_path: Path,
) -> None:
    case = P4cEccCase.from_mapping(
        {
            "schema_version": "cmd-p4c-ecc-case-v1",
            "case_id": "case-1",
            "event_index": 2,
            "observation": _observation(),
            "candidates": [
                {
                    "skill_revision_id": "skill-1",
                    "probe_id": "probe:skill-1",
                    "operator_sha256": "a" * 64,
                }
            ],
        }
    )
    run_dir = tmp_path / "run"
    manifest = P4cEccRunner(
        (case,),
        output_dir=run_dir,
        router=_ReceiptOnlyRouter(),
        store_factory=lambda _case: _Store(),
        evaluator_factory=lambda _case: _Evaluator(),
    ).run()
    receipt = json.loads((run_dir / "repair_receipts.jsonl").read_text())
    sidecar = tmp_path / "sealed-audit.json"
    sidecar.write_text(
        json.dumps(
            {
                "schema_version": "cmd-p4c-sealed-audit-v1",
                "run_manifest_sha256": manifest["run_manifest_sha256"],
                "rows": [
                    {
                        "case_id": "case-1",
                        "receipt_sha256": EccRepairReceipt.from_mapping(
                            receipt
                        ).content_hash,
                        "expected_incident": True,
                        "expected_mechanism": "process_fault",
                        "repair_expected": False,
                        "task_correct_after": False,
                        "sealed_label": "control-no-repair",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    before = {
        path.name: path.read_bytes()
        for path in run_dir.iterdir()
        if path.is_file()
    }

    report = audit_p4c_run(
        run_dir=run_dir,
        sealed_sidecar=sidecar,
        output_path=tmp_path / "audit-report.json",
    )

    assert report["accuracy"] == 0.0
    assert report["false_repair_rate"] == 1.0
    assert report["incident_recall"] == 1.0
    assert report["incident_type_accuracy"] == 1.0
    assert report["runtime_feedback_written"] is False
    assert before == {
        path.name: path.read_bytes()
        for path in run_dir.iterdir()
        if path.is_file()
    }

    bad = json.loads(sidecar.read_text())
    bad["run_manifest_sha256"] = "wrong"
    sidecar.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest"):
        audit_p4c_run(
            run_dir=run_dir,
            sealed_sidecar=sidecar,
            output_path=tmp_path / "bad-audit.json",
        )


def test_p4c_overlay_loader_is_closed_and_rejects_sealed_fields(
    tmp_path: Path,
) -> None:
    row = {
        "schema_version": "cmd-p4c-ecc-case-v1",
        "case_id": "case-1",
        "event_index": 2,
        "observation": _observation(),
        "candidates": [
            {
                "skill_revision_id": "skill-1",
                "probe_id": "probe:skill-1",
                "operator_sha256": "a" * 64,
            }
        ],
    }
    overlay = tmp_path / "overlay.jsonl"
    overlay.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert load_p4c_cases(overlay)[0].case_id == "case-1"

    row["sealed_label"] = "process_fault"
    overlay.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="closed"):
        load_p4c_cases(overlay)

    row.pop("sealed_label")
    row["observation"]["provenance"] = {
        "nested": {"gold_answer": "sealed"}
    }
    overlay.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="gold-free"):
        load_p4c_cases(overlay)
