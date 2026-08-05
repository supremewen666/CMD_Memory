from __future__ import annotations

import hashlib
import json

import pytest

from cmd_audit.eval.scope_audit import (
    ScopeAuditObservation,
    audit_scope_signals,
    write_scope_audit_events,
)
from cmd_audit.repair.scope_ledger import ScopeLedger


def test_scope_audit_promotes_known_valid_signal_with_full_provenance(
    tmp_path,
) -> None:
    dataset = tmp_path / "gold.json"
    dataset.write_text('{"version":"fixture"}\n', encoding="utf-8")
    ledger = ScopeLedger(
        n_min=30,
        bootstrap_samples=500,
        seed=24,
    )
    observations = tuple(
        ScopeAuditObservation(
            case_id=f"c{index}",
            signal_type="recall_set_collision",
            domain_fingerprint="memfail",
            indication_action="item_conflict",
            indication_gain=0.8,
            oracle_gain=0.82,
            frozen_gain=0.2,
            family_id=f"f{index}",
            evidence_ids=(f"m{index}",),
        )
        for index in range(30)
    )

    events = audit_scope_signals(
        observations,
        ledger=ledger,
        generation=3,
        dataset_path=dataset,
        provenance={
            "runtime_uses_gold": False,
            "uses_injection_control": False,
            "input_allowlist_sha256": "a" * 64,
            "extractor_version": "fixture-extractor",
            "evaluator_identity": "offline-shadow-evaluator",
        },
    )

    event = events[0]
    assert event.decision == "promote"
    assert event.new_status == "active"
    assert event.dataset_sha256 == hashlib.sha256(dataset.read_bytes()).hexdigest()
    assert len(event.selected_case_ids_sha256) == 64
    assert len(event.provenance_sha256) == 64
    assert event.provenance["evaluator_identity"] == (
        "offline-shadow-evaluator"
    )
    assert event.fires == event.valid == 30
    assert event.mean_incremental_gain == pytest.approx(0.6)

    path = write_scope_audit_events(
        events,
        tmp_path / "events.jsonl",
        append=False,
    )
    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["record_type"] == "scope_audit_event"
    assert row["dataset_path"] == str(dataset.resolve())


def test_scope_audit_keeps_low_validity_signal_in_shadow(tmp_path) -> None:
    dataset = tmp_path / "gold.json"
    dataset.write_text("{}\n", encoding="utf-8")
    ledger = ScopeLedger(n_min=30, bootstrap_samples=500)
    observations = tuple(
        ScopeAuditObservation(
            case_id=f"c{index}",
            signal_type="recency",
            domain_fingerprint="stale",
            indication_action="item_stale",
            indication_gain=0.8 if index < 6 else 0.0,
            oracle_gain=0.8,
            frozen_gain=0.1,
            family_id=f"f{index}",
        )
        for index in range(30)
    )

    event = audit_scope_signals(
        observations,
        ledger=ledger,
        generation=1,
        dataset_path=dataset,
        provenance={
            "runtime_uses_gold": False,
            "uses_injection_control": False,
            "input_allowlist_sha256": "b" * 64,
            "extractor_version": "fixture-extractor",
            "evaluator_identity": "fixture-evaluator",
        },
    )[0]

    assert event.decision == "ci_below_promotion_threshold"
    assert event.new_status == "shadow"
    assert event.validity == 0.2


def test_scope_audit_rejects_label_side_channel_provenance(tmp_path) -> None:
    dataset = tmp_path / "gold.json"
    dataset.write_text("{}\n", encoding="utf-8")
    observation = ScopeAuditObservation(
        case_id="c",
        signal_type="coverage",
        domain_fingerprint="memfail",
        indication_action="retrieval_error",
        indication_gain=0.5,
        oracle_gain=0.5,
        frozen_gain=0.0,
        family_id="f",
    )

    try:
        audit_scope_signals(
            (observation,),
            ledger=ScopeLedger(n_min=1, bootstrap_samples=100),
            generation=1,
            dataset_path=dataset,
            provenance={
                "runtime_uses_gold": False,
                "uses_injection_control": True,
                "input_allowlist_sha256": "a" * 64,
                "extractor_version": "fixture",
                "evaluator_identity": "fixture",
            },
        )
    except ValueError as exc:
        assert "uses_injection_control" in str(exc)
    else:
        raise AssertionError("injection-control provenance must fail closed")
