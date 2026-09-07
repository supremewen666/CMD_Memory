from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from experiments.e2_typed_identifiability import run_e2_suite
from tests.experiments.test_v4_prequential_runner import _case


def _write_cases(path: Path) -> None:
    rows = [
        _case(index, probe_set="represented", family=f"family-{index}").to_mapping()
        for index in range(6)
    ]
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_e2_suite_publishes_all_seed_reports_atomically(tmp_path: Path) -> None:
    cases = tmp_path / "cases.jsonl"
    _write_cases(cases)
    manifest = tmp_path / "materialization.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "cmd-v4-materialized-merge-v1",
                "output_sha256": hashlib.sha256(cases.read_bytes()).hexdigest(),
                "reference_is_fresh_replay": True,
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "e2"

    report = run_e2_suite(
        cases_path=cases,
        output_dir=output,
        seeds=(24, 25),
        bootstrap_samples=100,
        materialization_manifest=manifest,
    )

    assert report["seeds"] == [24, 25]
    assert report["coverage_blocked"] is True
    assert report["all_pass"] is False
    assert report["model_calls"] == 0
    assert report["reference_is_fresh_replay"] is True
    assert (output / "summary.json").is_file()
    assert (output / "seed-24.json").is_file()
    assert (output / "seed-25.json").is_file()
    assert json.loads((output / "seed-24.json").read_text())["reference_is_fresh_replay"] is True
    assert all(row["claim_statistics_available"] is False for row in report["reports"])

    with pytest.raises(ValueError, match="overwrite"):
        run_e2_suite(
            cases_path=cases,
            output_dir=output,
            seeds=(24,),
            bootstrap_samples=100,
        )


def test_e2_suite_rejects_unbound_manifest_and_duplicate_seeds(tmp_path: Path) -> None:
    cases = tmp_path / "cases.jsonl"
    _write_cases(cases)
    manifest = tmp_path / "materialization.json"
    manifest.write_text(json.dumps({"output_sha256": "0" * 64}), encoding="utf-8")
    with pytest.raises(ValueError, match="does not bind"):
        run_e2_suite(
            cases_path=cases,
            output_dir=tmp_path / "e2-a",
            seeds=(24,),
            bootstrap_samples=100,
            materialization_manifest=manifest,
        )

    mismatched_cases = tmp_path / "mismatched-cases.jsonl"
    _write_cases(mismatched_cases)
    mismatched_cases.write_text(
        mismatched_cases.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not bind"):
        run_e2_suite(
            cases_path=mismatched_cases,
            output_dir=tmp_path / "e2-mismatched-source",
            seeds=(24,),
            materialization_manifest=manifest,
        )
    with pytest.raises(ValueError, match="distinct"):
        run_e2_suite(
            cases_path=cases,
            output_dir=tmp_path / "e2-b",
            seeds=(24, 24),
            bootstrap_samples=100,
        )


def test_e2_suite_rejects_report_that_bypasses_coverage_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cases = tmp_path / "cases.jsonl"
    _write_cases(cases)

    def forged_audit(**kwargs):
        return {
            "model_calls": 0,
            "decision": "PASS",
            "typed_coverage": {
                "pairwise_comparable_coverage": {"value": 0.0},
                "family_macro_pearson": 0.9,
                "family_bootstrap_lower_95_one_sided": 0.8,
                "within_case_pairwise_concordance": 0.9,
                "candidate_level_pearson": 0.9,
                "comparable_pair_count": 1,
            },
            "decoupling_controls": {
                "telemetry_permutation": {"status": "PASS"},
                "telemetry_placebo": {"status": "PASS"},
            },
        }

    monkeypatch.setattr("experiments.e2_typed_identifiability.audit_identifiability_v2", forged_audit)
    with pytest.raises(ValueError, match="coverage gate"):
        run_e2_suite(cases_path=cases, output_dir=tmp_path / "e2", seeds=(24,))
