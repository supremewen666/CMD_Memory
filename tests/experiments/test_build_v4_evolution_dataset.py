from __future__ import annotations

import hashlib
import json
from pathlib import Path

from experiments.build_v4_evolution_dataset import main as build_main
from experiments.validate_v4_evolution_dataset import (
    main as validate_main,
    validate_bundle,
)


ROOT = Path(__file__).resolve().parents[2]
PROBE_DIR = ROOT / "data" / "probe_cases"


def _build(output: Path, *, limit: int = 12) -> dict[str, object]:
    assert build_main(
        (
            "--memtrace",
            str(PROBE_DIR / "memtrace_kp_cases.json"),
            "--stale",
            str(PROBE_DIR / "stale_item_cases.json"),
            "--memfail",
            str(PROBE_DIR / "memfail_cases.json"),
            "--output-dir",
            str(output),
            "--limit-per-domain",
            str(limit),
            "--seed",
            "20260809",
        )
    ) == 0
    return json.loads((output / "dataset_manifest.json").read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_cli_builds_a_deterministic_three_domain_bundle_and_validator_accepts_it(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    manifest = _build(first)
    repeated = _build(second)

    assert manifest["schema_version"] == "cmd-v4-evolution-dataset-manifest-v1"
    assert manifest["build_status"] == "relation_instrument_pending"
    assert manifest["case_count"] == 36
    assert manifest["domain_case_counts"] == {
        "memfail": 12,
        "memtrace_kp": 12,
        "stale_item": 12,
    }
    assert manifest["file_sha256"] == repeated["file_sha256"]
    assert validate_bundle(first)["decision"] == "PASS"
    report = tmp_path / "validation.json"
    assert validate_main(("--dataset-dir", str(first), "--output", str(report))) == 0
    assert json.loads(report.read_text(encoding="utf-8"))["decision"] == "PASS"


def test_runtime_and_relation_surfaces_are_gold_free_and_template_free(
    tmp_path: Path,
) -> None:
    output = tmp_path / "bundle"
    _build(output)
    runtime_rows = _jsonl(output / "runtime_cases.jsonl")
    relation_rows = _jsonl(output / "relation_requests.jsonl")
    shadow_rows = _jsonl(output / "shadow_cases.jsonl")

    runtime_text = json.dumps(runtime_rows, ensure_ascii=False)
    relation_text = json.dumps(relation_rows, ensure_ascii=False)
    shadow_text = json.dumps(shadow_rows, ensure_ascii=False)
    for leaked in (
        '"gold_answer"',
        '"gold_evidence"',
        '"perturbation_label"',
        '"target_item_id"',
        "M_old:",
        "M_new:",
    ):
        assert leaked not in runtime_text
        assert leaked not in relation_text
    assert '"gold_answer"' in shadow_text
    assert '"perturbation_label"' in shadow_text
    assert "M_old:" not in shadow_text
    assert "M_new:" not in shadow_text
    assert all(
        row["runtime_case"]["family_id"].startswith("runtime:")
        for row in runtime_rows
    )


def test_validator_refuses_hash_bound_runtime_tampering(tmp_path: Path) -> None:
    output = tmp_path / "bundle"
    _build(output)
    runtime_path = output / "runtime_cases.jsonl"
    rows = _jsonl(runtime_path)
    rows[0]["gold_answer"] = "leak"
    runtime_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )

    report = validate_bundle(output)
    assert report["decision"] == "REFUSE"
    assert "file_hash_mismatch:runtime_cases.jsonl" in report["reasons"]


def test_source_hashes_bind_the_checked_in_datasets(tmp_path: Path) -> None:
    output = tmp_path / "bundle"
    manifest = _build(output)
    expected = {
        "memtrace_kp": "df655c77b3626f9a2cb5b6c4783e2db06c1bba6d12e9ee2192206cd1b2b44eda",
        "stale_item": "1068e8185530aabd0e799eb633b81cf3bc197543d6c1e2e01ddf12613f914612",
        "memfail": "f30bcd2c47b6ec2d28502654d7d2936843ed4c052827835ec4a05d8b65161864",
    }
    sources = json.loads((output / "source_manifest.json").read_text(encoding="utf-8"))
    assert {row["domain"]: row["source_sha256"] for row in sources["sources"]} == expected
    assert manifest["source_manifest_sha256"] == hashlib.sha256(
        (output / "source_manifest.json").read_bytes()
    ).hexdigest()


def test_checked_in_full_bundle_is_valid_and_family_blocked() -> None:
    bundle = ROOT / "data" / "evolution_v4"
    report = validate_bundle(bundle)
    manifest = json.loads((bundle / "dataset_manifest.json").read_text(encoding="utf-8"))

    assert report["decision"] == "PASS"
    assert report["reasons"] == []
    assert manifest["case_count"] == 3_939
    assert manifest["relation_request_count"] == 14_164
    assert manifest["domain_family_counts"] == {
        "memfail": 492,
        "memtrace_kp": 182,
        "stale_item": 400,
    }
    assert manifest["domain_dependency_group_counts"] == {
        "memfail": 492,
        "memtrace_kp": 20,
        "stale_item": 400,
    }
    assert report["summary"]["dependency_split_violations"] == 0
    assert report["summary"]["runtime_template_marker_count"] == 0
    assert report["summary"]["intent_constructibility_rate"] == 1.0
