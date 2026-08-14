from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from experiments.ghost_live_protocol import (
    ATTESTATION_SCHEMA,
    FEEDBACK_SCHEMA,
    MODEL_MANIFEST_SCHEMA,
    PARTITIONS,
    audit_feedback,
    authorize,
    freeze,
    validate_run,
)


ROOT = Path(__file__).resolve().parents[2]


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _bundle(tmp_path: Path) -> dict[str, Path]:
    cases = tmp_path / "fresh-cases.jsonl"
    rows = [
        ("dev-1", "family-a"),
        ("dev-2", "family-b"),
        ("cal-1", "family-c"),
        ("rep-1", "family-a"),
        ("new-1", "family-new"),
    ]
    cases.write_text(
        "".join(
            json.dumps(
                {"case_id": case_id, "family_id": family_id, "intents": [{}]},
                sort_keys=True,
            )
            + "\n"
            for case_id, family_id in rows
        ),
        encoding="utf-8",
    )
    assignments = {
        "ghost_dev": ("dev-1", "dev-2"),
        "ghost_cal": ("cal-1",),
        "ghost_test_rep": ("rep-1",),
        "ghost_test_new": ("new-1",),
    }
    partition_files = {}
    for partition, case_ids in assignments.items():
        path = tmp_path / f"{partition}.txt"
        path.write_text("\n".join(case_ids) + "\n", encoding="utf-8")
        partition_files[partition] = path
    attestation = tmp_path / "attestation.json"
    _write_json(
        attestation,
        {
            "schema_version": ATTESTATION_SCHEMA,
            "independent_source": True,
            "source_id": "prospective-collector-1",
            "collector": "independent-curator",
            "collected_at_utc": "2026-08-14T00:00:00Z",
            "cases_file_sha256": _hash(cases),
            "partition_file_sha256": {
                name: _hash(path) for name, path in partition_files.items()
            },
            "notes": "fixture attestation",
        },
    )
    models = tmp_path / "models.json"
    _write_json(
        models,
        {
            "schema_version": MODEL_MANIFEST_SCHEMA,
            "models": [
                {"role": "relation_instrument", "model_id": "instrument-v1", "model_sha256": "c" * 64},
                {"role": "intent_proposer", "model_id": "proposer-v1", "model_sha256": "d" * 64},
                {"role": "answer", "model_id": "answer-v1", "model_sha256": "a" * 64},
                {"role": "judge", "model_id": "judge-v1", "model_sha256": "b" * 64},
            ],
        },
    )
    evaluator = tmp_path / "evaluator.json"
    _write_json(evaluator, {"frozen": True})
    preparation = tmp_path / "preparation_manifest.json"
    _write_json(preparation, {"candidate_budget": 1, "frozen": True})
    return {
        "cases": cases,
        "attestation": attestation,
        "models": models,
        "evaluator": evaluator,
        "preparation": preparation,
        **partition_files,
    }


def _frozen(tmp_path: Path) -> tuple[dict[str, Path], Path, Path, Path]:
    bundle = _bundle(tmp_path)
    protocol = tmp_path / "protocol.json"
    access = tmp_path / "access.jsonl"
    freeze(
        root=ROOT,
        cases=bundle["cases"],
        partition_files={name: bundle[name] for name in PARTITIONS},
        attestation=bundle["attestation"],
        model_manifest=bundle["models"],
        evaluator=bundle["evaluator"],
        preparation_manifest=bundle["preparation"],
        output=protocol,
        access_ledger=access,
        candidate_budget=1,
    )
    authorization = tmp_path / "authorization.json"
    authorize(
        protocol_path=protocol,
        access_ledger=access,
        output=authorization,
        authorizer="test-governor",
        run_id="confirm-001",
    )
    return bundle, protocol, access, authorization


def test_fresh_four_way_freeze_authorization_and_preflight(tmp_path: Path) -> None:
    bundle, protocol, access, authorization = _frozen(tmp_path)
    value = json.loads(protocol.read_text(encoding="utf-8"))
    assert value["partition_counts"] == {
        "ghost_dev": 2,
        "ghost_cal": 1,
        "ghost_test_rep": 1,
        "ghost_test_new": 1,
    }
    assert value["first_test_access_authorized"] is False
    assert len(access.read_text(encoding="utf-8").splitlines()) == 2
    report = validate_run(
        root=ROOT,
        cases=bundle["cases"],
        protocol_path=protocol,
        authorization_path=authorization,
        access_ledger_path=access,
        model_manifest_path=bundle["models"],
        evaluator_path=bundle["evaluator"],
        preparation_manifest_path=bundle["preparation"],
        candidate_budget=1,
        run_id="confirm-001",
    )
    assert report["decision"] == "PASS"
    assert report["model_calls"] == 0


def test_freeze_refuses_empty_test_or_unseen_family_leakage(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    bundle["ghost_test_new"].write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="non-empty"):
        freeze(
            root=ROOT,
            cases=bundle["cases"],
            partition_files={name: bundle[name] for name in PARTITIONS},
            attestation=bundle["attestation"],
            model_manifest=bundle["models"],
            evaluator=bundle["evaluator"],
            preparation_manifest=bundle["preparation"],
            output=tmp_path / "protocol.json",
            access_ledger=tmp_path / "access.jsonl",
            candidate_budget=1,
        )


def _selection() -> dict[str, object]:
    return {
        "schema_version": "cmd-ghost-live-selection-v1",
        "selected_at_utc": "2026-08-14T00:00:00Z",
        "case_id": "rep-1",
        "family_id_audit_only": "family-a",
        "partition": "ghost_test_rep",
        "selection_id": "selection-1",
        "event_index": 1,
        "selected_intent_id": "intent-1",
        "selected_skill_revision_id": "skill-1",
        "probe_id": "probe:replace-v1",
        "repair_effect": "replace",
        "pre_action_prior": 0.25,
        "registry_id": "registry-1",
        "evaluator_snapshot_sha256": "e" * 64,
        "evaluation_only": True,
        "development_proxy": False,
    }


def _feedback(*, matured: bool, proxy: bool = False) -> dict[str, object]:
    return {
        "schema_version": FEEDBACK_SCHEMA,
        "feedback_id": "feedback-1",
        "case_id": "rep-1",
        "selection_id": "selection-1",
        "selected_intent_id": "intent-1",
        "selected_skill_revision_id": "skill-1",
        "probe_id": "probe:replace-v1",
        "repair_effect": "replace",
        "applicable_signals": [
            "target_resolved", "anchor_non_regression", "recurrence"
        ],
        "pre_action_prior": 0.25,
        "selected_at_utc": "2026-08-14T00:00:00Z",
        "window_ends_at_utc": "2026-08-21T00:00:00Z",
        "observed_at_utc": (
            "2026-08-21T01:00:00Z" if matured else "2026-08-18T00:00:00Z"
        ),
        "target_resolved": True if matured else None,
        "anchor_non_regression": True if matured else None,
        "recurrence": False if matured else None,
        "annotation_consumed": None,
        "valid": True,
        "rolled_back": False,
        "locality_cost": 0.05,
        "execution_cost": 0.05,
        "provenance": "prospective-deployment-window-v1",
        "gold_derived": False,
        "matured": matured,
        "development_proxy": proxy,
    }


def test_delayed_feedback_keeps_censoring_separate_and_rejects_proxy(tmp_path: Path) -> None:
    _bundle_value, protocol, _access, _authorization = _frozen(tmp_path)
    selections = tmp_path / "selections.jsonl"
    selections.write_text(json.dumps(_selection()) + "\n", encoding="utf-8")
    feedback = tmp_path / "feedback.jsonl"
    feedback.write_text(json.dumps(_feedback(matured=False)) + "\n", encoding="utf-8")
    blocked = audit_feedback(
        feedback, tmp_path / "blocked.json", protocol, selections
    )
    assert blocked["decision"] == "BLOCKED_NO_MATURED_FEEDBACK"
    assert blocked["right_censored_count"] == 1

    matured_path = tmp_path / "matured.jsonl"
    matured_path.write_text(json.dumps(_feedback(matured=True)) + "\n", encoding="utf-8")
    passed = audit_feedback(
        matured_path, tmp_path / "passed.json", protocol, selections
    )
    assert passed["decision"] == "BLOCKED_FEEDBACK_NOT_IDENTIFIABLE"
    assert passed["matured_count"] == 1

    proxy_path = tmp_path / "proxy.jsonl"
    proxy_path.write_text(
        json.dumps(_feedback(matured=True, proxy=True)) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="proxy"):
        audit_feedback(
            proxy_path, tmp_path / "proxy-report.json", protocol, selections
        )
