from __future__ import annotations

import json

from experiments.analyze_item_gate_scope import main


def test_live_item_gate_scope_analysis_writes_active_ledger(
    tmp_path,
    monkeypatch,
) -> None:
    dataset = tmp_path / "cases.json"
    dataset.write_text("{}\n", encoding="utf-8")
    artifact = tmp_path / "arena.jsonl"
    rows = (
        {
            "record_type": "arena_manifest",
            "arena_id": "stale",
            "runtime_uses_gold": False,
            "evaluation_judge_identity": "frozen-judge",
            "dataset_source_path": str(dataset),
        },
        {
            "record_type": "gold_free_observation",
            "case_id": "c1",
            "family_id": "f1",
            "selected_shadow_gain": 0.1,
            "shadow_gold_scores": [
                ["seed:item_stale", 0.5],
                ["seed:item_conflict", 0.2],
            ],
        },
        {
            "record_type": "structural_indication_event",
            "arena_id": "stale",
            "case_id": "c1",
            "signal_type": "temporal_content_contradiction",
            "action": "item_stale",
            "runtime_surface": "tier2_item_gate",
            "extractor_version": "live-item-gate-v1",
            "input_allowlist_sha256": "a" * 64,
            "created_before_outcome": True,
            "evidence_ids": ["new", "old"],
        },
    )
    artifact.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    output = tmp_path / "audit"
    monkeypatch.setattr(
        "sys.argv",
        [
            "analyze_item_gate_scope",
            "--inputs",
            str(artifact),
            "--output-dir",
            str(output),
            "--n-min",
            "1",
            "--bootstrap-samples",
            "100",
        ],
    )

    assert main() == 0

    summary = json.loads(
        (output / "stage1_summary.json").read_text(encoding="utf-8")
    )
    assert summary["stage1_gate_passed"] is True
    assert summary["stop_stage2_and_stage3"] is False
    assert summary["active_scopes"] == [
        {
            "signal_type": "temporal_content_contradiction",
            "domain_fingerprint": "stale",
            "validity": 1.0,
            "ci_lower": 1.0,
            "mean_incremental_gain": 0.4,
        }
    ]


def test_zero_firing_live_channel_is_a_stage1_no_go(
    tmp_path,
    monkeypatch,
) -> None:
    dataset = tmp_path / "cases.json"
    dataset.write_text("{}\n", encoding="utf-8")
    artifact = tmp_path / "arena.jsonl"
    rows = (
        {
            "record_type": "arena_manifest",
            "arena_id": "memfail",
            "runtime_uses_gold": False,
            "evaluation_judge_identity": "frozen-judge",
            "dataset_source_path": str(dataset),
            "structural_extractor_version": (
                "sigil-structural-v1+live-item-gate-v1"
            ),
        },
        {
            "record_type": "gold_free_observation",
            "case_id": "c1",
            "family_id": "f1",
            "selected_shadow_gain": 0.0,
            "shadow_gold_scores": [],
        },
    )
    artifact.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    output = tmp_path / "audit"
    monkeypatch.setattr(
        "sys.argv",
        [
            "analyze_item_gate_scope",
            "--inputs",
            str(artifact),
            "--output-dir",
            str(output),
            "--n-min",
            "1",
            "--bootstrap-samples",
            "100",
        ],
    )

    assert main() == 0

    summary = json.loads(
        (output / "stage1_summary.json").read_text(encoding="utf-8")
    )
    assert summary["stage1_gate_passed"] is False
    assert summary["stop_stage2_and_stage3"] is True
    assert summary["inputs"][0]["audit_decisions"] == [
        "no_live_signal_fired"
    ]
