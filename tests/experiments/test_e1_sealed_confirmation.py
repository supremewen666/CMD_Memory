from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.e1_sealed_confirmation import (
    audit_held_out,
    seal_protocol,
    verify_registration,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _anchors(path: Path) -> None:
    rows = [
        {
            "anchor_id": f"reference-{index}",
            "payload": f"reference payload {index}",
            "reference": float(index % 2),
            "split": "reference",
        }
        for index in range(10)
    ]
    rows.extend(
        (
            {
                "anchor_id": "held-0",
                "payload": "held payload zero",
                "reference": 0.25,
                "split": "held_out",
            },
            {
                "anchor_id": "held-1",
                "payload": "held payload one",
                "reference": 0.75,
                "split": "held_out",
            },
        )
    )
    _write_jsonl(path, rows)


def test_seal_verify_and_audit_are_hash_bound(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text('{"case_id":"case-0"}\n', encoding="utf-8")
    anchors = tmp_path / "anchors.jsonl"
    _anchors(anchors)
    registration_path = tmp_path / "registration.json"
    preflight_path = tmp_path / "preflight.json"
    scores = tmp_path / "scores.jsonl"
    audit_path = tmp_path / "audit.json"

    registration = seal_protocol(
        dataset=dataset,
        anchors=anchors,
        output=registration_path,
        protocol_id="e1-test",
        anchor_set_id="anchors-test",
        arms=("cmd", "control"),
        primary_metric="anchor_mean_absolute_deviation",
        thresholds={"max_anchor_mean_absolute_deviation": 0.1},
        seeds=(24, 25),
    )
    assert registration["held_out_content_exposed"] is False
    assert registration["reference_anchor_count"] == 10
    assert registration["held_out_anchor_count"] == 2

    preflight = verify_registration(
        registration_path=registration_path,
        dataset=dataset,
        anchors=anchors,
        output=preflight_path,
    )
    assert preflight["verified"] is True
    assert preflight["held_out_read"] is False
    assert preflight["model_calls"] == 0

    _write_jsonl(
        scores,
        [
            {"anchor_id": "held-0", "observed": 0.3},
            {"anchor_id": "held-1", "observed": 0.7},
        ],
    )
    audit = audit_held_out(
        registration_path=registration_path,
        dataset=dataset,
        anchors=anchors,
        scores=scores,
        output=audit_path,
    )
    assert audit["passed"] is True
    assert audit["audit"]["held_out_count"] == 2
    assert audit["model_calls_new"] == 0


def test_audit_fails_closed_on_incomplete_scores_and_overwrite(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text("dataset\n", encoding="utf-8")
    anchors = tmp_path / "anchors.jsonl"
    _anchors(anchors)
    registration = tmp_path / "registration.json"
    seal_protocol(
        dataset=dataset,
        anchors=anchors,
        output=registration,
        protocol_id="e1-test",
        anchor_set_id="anchors-test",
        arms=("cmd", "control"),
        primary_metric="anchor_mean_absolute_deviation",
        thresholds={"max_anchor_mean_absolute_deviation": 0.1},
        seeds=(24,),
    )
    with pytest.raises(ValueError, match="overwrite"):
        seal_protocol(
            dataset=dataset,
            anchors=anchors,
            output=registration,
            protocol_id="e1-test",
            anchor_set_id="anchors-test",
            arms=("cmd", "control"),
            primary_metric="anchor_mean_absolute_deviation",
            thresholds={"max_anchor_mean_absolute_deviation": 0.1},
            seeds=(24,),
        )
    scores = tmp_path / "scores.jsonl"
    _write_jsonl(scores, [{"anchor_id": "held-0", "observed": 0.25}])
    with pytest.raises(ValueError, match="coverage mismatch"):
        audit_held_out(
            registration_path=registration,
            dataset=dataset,
            anchors=anchors,
            scores=scores,
            output=tmp_path / "audit.json",
        )
