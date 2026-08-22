from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments import run_no_mem0_suite as suite


def test_registry_is_a_small_offline_allowlist(tmp_path: Path) -> None:
    plan = suite.run(profile="plumbing-smoke", output_root=tmp_path / "unused", run_mode="fresh", limit=1, plan_only=True, fail_fast=True)
    names = [row["name"] for row in plan["steps"]]
    assert names == ["p3a-longmemeval-m0-r1", "p3b-memfail-m0-r1", "p3c-fake-e2e", "p3d-fake-lifecycle"]
    assert "--backend" in plan["steps"][0]["command"] and "in-memory" in plan["steps"][0]["command"]
    assert "openai-compatible" not in json.dumps(plan)


def test_plan_only_does_not_write(tmp_path: Path) -> None:
    target = tmp_path / "plan"
    suite.run(profile="offline-memory", output_root=target, run_mode="fresh", limit=1, plan_only=True, fail_fast=False)
    assert not target.exists()

def test_baseline_confirmation_profile_is_vanilla_only(tmp_path: Path) -> None:
    plan=suite.run(profile="baseline-confirmation", output_root=tmp_path/"unused", run_mode="fresh", limit=1, plan_only=True, fail_fast=True)
    text=json.dumps(plan)
    assert "p4a-longmemeval-s-bm25" in text and "mem0" in text
    assert "--arms" not in text and "oracle-ceiling" not in text


def test_missing_input_is_skip_not_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(suite, "_steps", lambda *_: (suite.Step("missing", "x", (tmp_path / "none",), ()),))
    result = suite.run(profile="plumbing-smoke", output_root=tmp_path / "out", run_mode="fresh", limit=1, plan_only=False, fail_fast=True)
    assert result["skipped"] == 1 and result["passed"] == 0


def test_resume_rejects_tampered_input_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    input_file = tmp_path / "input"; input_file.write_text("a")
    monkeypatch.setattr(suite, "_steps", lambda *_: (suite.Step("ok", "experiments.run_evobench_harness", (input_file,), ("--help",)),))
    out = tmp_path / "out"; suite.run(profile="plumbing-smoke", output_root=out, run_mode="fresh", limit=1, plan_only=False, fail_fast=True)
    input_file.write_text("changed")
    with pytest.raises(ValueError, match="root mismatch"):
        suite.run(profile="plumbing-smoke", output_root=out, run_mode="resume", limit=1, plan_only=False, fail_fast=True)
