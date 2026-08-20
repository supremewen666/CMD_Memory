from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.zero_call_typed_enrichment import enrich_files, enrich_row


def test_prepared_legacy_binding_rejects_intent_mismatch() -> None:
    prepared = {"case_id": "c", "family_id": "f", "probe_set": "represented", "legacy_intent_id": "i", "intents": [{"intent_id": "i"}], "context": {}, "graph": {}}
    legacy = {**prepared, "intents": [{"intent_id": "j"}]}
    with pytest.raises(ValueError, match="intent"):
        enrich_row(prepared, legacy)


def test_legacy_candidate_duplicate_is_rejected() -> None:
    prepared_path = Path("artifacts/ghost_public_call_v1/prepared_cases.jsonl")
    legacy_path = Path("artifacts/ghost_public_call_v1/runs/ghost-live-20260814T075634Z/materialized/single_gpu.jsonl")
    if not prepared_path.exists() or not legacy_path.exists():
        pytest.skip("frozen source artifacts unavailable")
    prepared = json.loads(prepared_path.read_text().splitlines()[0])
    legacy = json.loads(legacy_path.read_text().splitlines()[0])
    legacy["candidate_outcomes"].append(dict(legacy["candidate_outcomes"][0]))
    with pytest.raises(ValueError, match="outcomes"):
        enrich_row(prepared, legacy)


def test_real_source_small_preflight_is_zero_call(tmp_path: Path) -> None:
    prepared_path = Path("artifacts/ghost_public_call_v1/prepared_cases.jsonl")
    legacy_path = Path("artifacts/ghost_public_call_v1/runs/ghost-live-20260814T075634Z/materialized/single_gpu.jsonl")
    if not prepared_path.exists() or not legacy_path.exists():
        pytest.skip("frozen source artifacts unavailable")
    manifest = enrich_files(prepared_path, legacy_path, tmp_path / "rows.jsonl", tmp_path / "rows.manifest.json", limit=1)
    assert manifest["model_calls_new"] == 0
    assert manifest["development_only"] is True
    row = json.loads((tmp_path / "rows.jsonl").read_text().splitlines()[0])
    assert all(item["annotation_consumed"] is None for item in row["candidate_outcomes"])


def test_typed_runtime_flags_are_recomputed_not_copied_from_legacy() -> None:
    prepared_path = Path("artifacts/ghost_public_call_v1/prepared_cases.jsonl")
    legacy_path = Path("artifacts/ghost_public_call_v1/runs/ghost-live-20260814T075634Z/materialized/single_gpu.jsonl")
    if not prepared_path.exists() or not legacy_path.exists():
        pytest.skip("frozen source artifacts unavailable")
    prepared = json.loads(prepared_path.read_text().splitlines()[0])
    legacy = json.loads(legacy_path.read_text().splitlines()[0])
    baseline = enrich_row(prepared, legacy)
    tampered = json.loads(json.dumps(legacy))
    for outcome in tampered["candidate_outcomes"]:
        outcome["valid"] = not outcome["valid"]
        outcome["rolled_back"] = not outcome["rolled_back"]
        outcome["recovery_gain"] = 999.0
    isolated = enrich_row(prepared, tampered)
    for expected, actual in zip(
        baseline["candidate_outcomes"], isolated["candidate_outcomes"], strict=True
    ):
        for key in (
            "intent_id", "locality_cost", "changed_item_count", "valid",
            "rolled_back", "actionability_mode_observed", "target_binding_observed",
            "target_match_observed", "changed_item_ids",
        ):
            assert actual[key] == expected[key]
        assert actual["recovery_gain"] == 999.0
