from __future__ import annotations

import json
from pathlib import Path

from experiments.run_p4c1_real_sources import (
    P4C1_MANIFEST_SCHEMA,
    SESSION_PROJECTION_SCHEMA,
)
from experiments.run_p4c_mainline import build_plan, verify


def _write(path: Path, value: object) -> None:
    path.mkdir(parents=True, exist_ok=True)
    name = {
        "p4c1": "p4c1_manifest.json",
        "p4c3-runtime": "runtime_manifest.json",
        "p4c3-audit": "detector_audit.json",
        "p4c45": "p4c45_manifest.json",
    }[path.name]
    (path / name).write_text(json.dumps(value), encoding="utf-8")


def test_plan_makes_structural_gold_free_evidence_the_only_mainline() -> None:
    plan = build_plan()
    assert plan["primary_claim"] == "gold-free memory fault correction and evolution"
    assert [row["stage"] for row in plan["mainline_stages"]] == [
        "p4c1", "p4c3", "p4c45"
    ]
    assert plan["supplementary_stages"] == ["p4c2", "p4c6"]
    assert plan["legacy_stages"] == ["legacy-answer"]
    assert plan["external_calls_authorized"] is False


def test_verify_accepts_only_mainline_manifests(tmp_path: Path) -> None:
    p4c1 = tmp_path / "p4c1"
    p4c3 = tmp_path / "p4c3"
    p4c45 = tmp_path / "p4c45"
    _write(
        p4c1,
        {
            "schema_version": P4C1_MANIFEST_SCHEMA,
            "status": "success",
            "paper_role": "mainline",
            "primary_claim": "gold-free memory fault correction and evolution",
            "runtime_uses_gold": False,
            "runtime_uses_labels": False,
            "router_feedback": "EccRepairReceipt",
            "session_projection_schema": SESSION_PROJECTION_SCHEMA,
        },
    )
    p4c3.mkdir()
    (p4c3 / "runtime_manifest.json").write_text(
        json.dumps(
            {
                "status": "prediction_sealed",
                "paper_role": "mainline",
                "runtime_gold_free": True,
                "external_call_count": 0,
            }
        ),
        encoding="utf-8",
    )
    (p4c3 / "detector_audit.json").write_text(
        json.dumps({"paper_role": "mainline", "runtime_feedback_written": False}),
        encoding="utf-8",
    )
    _write(
        p4c45,
        {
            "status": "success",
            "paper_role": "mainline",
            "primary_claim": "gold-free memory fault correction and evolution",
            "runtime_uses_gold": False,
            "runtime_uses_labels": False,
            "router_implementation": "GHOSTEcologyRouter",
        },
    )
    report = verify(p4c1_run=p4c1, p4c3_run=p4c3, p4c45_run=p4c45)
    assert report["status"] == "mainline_evidence_ready"
    assert set(report["roots"]) == {
        "p4c1_manifest_sha256",
        "p4c3_runtime_manifest_sha256",
        "p4c3_audit_sha256",
        "p4c45_manifest_sha256",
    }
