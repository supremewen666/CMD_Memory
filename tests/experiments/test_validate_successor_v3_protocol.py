"""CLI contract for schema-v2 successor freeze validation."""

from __future__ import annotations

import json
from pathlib import Path

from experiments.validate_successor_v3_protocol import main
from tests.eval.test_successor_protocol_freeze import _make_fixture


def test_cli_writes_content_bound_v2_validation_artifact(tmp_path: Path) -> None:
    freeze, dataset_path, prompt_path = _make_fixture(tmp_path)
    freeze_path = tmp_path / "protocol_freeze.json"
    output_path = tmp_path / "protocol_freeze_validation.json"
    freeze_path.write_text(json.dumps(freeze), encoding="utf-8")

    code = main([
        "--protocol-freeze", str(freeze_path),
        "--dataset-manifest", str(dataset_path),
        "--repo-root", str(tmp_path),
        "--prompt-file", str(prompt_path),
        "--output", str(output_path),
    ])

    assert code == 0
    artifact = json.loads(output_path.read_text(encoding="utf-8"))
    assert artifact["valid"] is True
    assert artifact["manifest_sha256"]
    assert artifact["recomputed_hashes"]["instrument.prompt_sha256"]
    assert artifact["recomputed_hashes"]["commands.e0.script_sha256"]


def test_cli_refuses_without_a_readable_prompt(tmp_path: Path) -> None:
    freeze, dataset_path, _ = _make_fixture(tmp_path)
    freeze_path = tmp_path / "protocol_freeze.json"
    output_path = tmp_path / "protocol_freeze_validation.json"
    freeze_path.write_text(json.dumps(freeze), encoding="utf-8")

    code = main([
        "--protocol-freeze", str(freeze_path),
        "--dataset-manifest", str(dataset_path),
        "--repo-root", str(tmp_path),
        "--prompt-file", str(tmp_path / "missing-prompt.txt"),
        "--output", str(output_path),
    ])

    assert code == 2
    assert "prompt_file_unreadable" in json.loads(output_path.read_text())["reasons"]
