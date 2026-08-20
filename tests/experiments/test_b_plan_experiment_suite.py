from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments import b_plan_experiment_suite as suite


def _fake_cases(count: int = 2):
    return tuple(SimpleNamespace(intents=(object(), object())) for _ in range(count))


def _write(path: Path, text: str = "fixture\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _config(tmp_path: Path, *, stages=suite.STAGES, budget: int = 2) -> suite.BPlanConfig:
    prepared = tmp_path / "prepared.jsonl"
    cases = tmp_path / "cases.jsonl"
    _write(prepared)
    _write(cases)
    materialization = tmp_path / "materialization.json"
    _write(
        materialization,
        json.dumps(
            {"output_sha256": hashlib.sha256(cases.read_bytes()).hexdigest()}
        )
        + "\n",
    )
    evaluator = tmp_path / "evaluator.json"
    ghost_protocol = tmp_path / "ghost-protocol.json"
    _write(evaluator, "{}\n")
    _write(ghost_protocol, "{}\n")
    return suite.BPlanConfig(
        prepared_path=prepared,
        cases_path=cases,
        output_dir=tmp_path / "out",
        backend_locator="tests.fixture:capture",
        candidate_budget=budget,
        seeds=(7, 11),
        protocol={"protocol": "fixture-v1", "candidate_budget": budget},
        ghost_evaluator_path=evaluator,
        ghost_protocol_path=ghost_protocol,
        source_materialization_manifest=materialization,
        stages=tuple(stages),
    )


def test_suite_freezes_hashes_argv_and_rejects_non_callable_backend(tmp_path, monkeypatch):
    config = _config(tmp_path, stages=())
    monkeypatch.setattr(suite, "load_cases", lambda _path: _fake_cases())
    with pytest.raises(ValueError, match="callable"):
        suite.run_b_plan(config, backend=None)  # type: ignore[arg-type]

    manifest = suite.run_b_plan(config, backend=lambda _plan: {})
    assert manifest["closed"] is True
    assert manifest["inputs"]["candidate_budget"] == 2
    assert manifest["inputs"]["cases_sha256"]
    assert suite.exact_argv(config, "lineage_plan")[:4] == (
        "python", "-m", "experiments.b_plan_experiment_suite", "--stage"
    )
    assert suite.exact_argv(config, "lineage_plan").count("--seed") == 2
    assert config.paths["suite_manifest"].is_file()


def test_suite_validates_candidate_budget_and_no_overwrite(tmp_path, monkeypatch):
    config = _config(tmp_path, stages=())
    monkeypatch.setattr(suite, "load_cases", lambda _path: _fake_cases())
    bad = suite.BPlanConfig(
        prepared_path=config.prepared_path,
        cases_path=config.cases_path,
        output_dir=config.output_dir,
        backend_locator=config.backend_locator,
        candidate_budget=3,
        seeds=config.seeds,
        protocol=config.protocol,
        ghost_evaluator_path=config.ghost_evaluator_path,
        ghost_protocol_path=config.ghost_protocol_path,
        source_materialization_manifest=config.source_materialization_manifest,
        stages=(),
    )
    with pytest.raises(ValueError, match="candidate budget"):
        suite.run_b_plan(bad, backend=lambda _plan: {})

    suite.run_b_plan(config, backend=lambda _plan: {})
    with pytest.raises(ValueError, match="overwrite"):
        suite.run_b_plan(config, backend=lambda _plan: {})


def test_full_stage_order_uses_pseudo_backend_and_closes_manifest(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(suite, "load_cases", lambda _path: _fake_cases())
    calls: list[str] = []

    def fake_plan(**kwargs):
        calls.append("lineage_plan")
        for key in ("capture_output", "selections_output", "manifest_output"):
            _write(kwargs[key])
        return {"model_calls": 0, "network_calls": 0}

    def fake_capture(**kwargs):
        calls.append("followup_capture")
        _write(kwargs["output_path"])
        _write(kwargs["manifest_path"], json.dumps({"model_calls": 1, "network_calls": 1}))
        return {"model_calls": 1, "network_calls": 1}

    def fake_project(source, output, manifest, **kwargs):
        calls.append("lineage_project")
        _write(output)
        _write(manifest)
        return {"model_calls": 0, "network_calls": 0}

    def fake_merge(**kwargs):
        calls.append("lineage_merge")
        _write(kwargs["output_path"])
        _write(kwargs["manifest_path"])
        return {"model_calls_new": 0, "network_calls_new": 0}

    def fake_e2(**kwargs):
        calls.append("E2")
        _write(kwargs["output_dir"] / "summary.json")
        return {"model_calls": 0, "network_calls": 0}

    def fake_e3(**kwargs):
        calls.append("E3")
        return {"model_calls": 0, "network_calls": 0}

    def fake_e4(argv):
        calls.append("E4")
        assert argv.count("--bootstrap-samples") == 1
        assert argv[argv.index("--bootstrap-samples") + 1] == str(
            config.bootstrap_samples
        )
        output_dir = Path(argv[argv.index("--output-dir") + 1])
        _write(output_dir / "report.json", json.dumps({"model_calls": 0, "network_calls": 0}))
        return 0

    def fake_e4b(**kwargs):
        calls.append("E4b")
        _write(kwargs["output_dir"] / "summary.json")
        return {"model_calls": 0, "network_calls": 0}

    monkeypatch.setattr(suite, "build_capture_plan", fake_plan)
    monkeypatch.setattr(suite, "capture_followups", fake_capture)
    monkeypatch.setattr(suite, "export_lineage", fake_project)
    monkeypatch.setattr(suite, "merge_lineage_cases", fake_merge)
    monkeypatch.setattr(suite, "run_e2_suite", fake_e2)
    monkeypatch.setattr(suite, "run_sweep", fake_e3)
    monkeypatch.setattr(suite, "run_v4", fake_e4)
    monkeypatch.setattr(suite, "run_e4b", fake_e4b)

    manifest = suite.run_b_plan(config, backend=lambda _plan: {})
    assert calls == list(suite.STAGES)
    assert manifest["closed"] is True
    assert manifest["model_calls"] == 1
    assert manifest["network_calls"] == 1
    assert [row["stage"] for row in manifest["stages"]] == list(suite.STAGES)
    assert all(row["argv"][3] == "--stage" for row in manifest["stages"])


def test_separate_stage_invocations_share_one_run_root(tmp_path, monkeypatch):
    first = _config(tmp_path, stages=("lineage_plan",))
    second = suite.BPlanConfig(
        prepared_path=first.prepared_path,
        cases_path=first.cases_path,
        output_dir=first.output_dir,
        backend_locator=first.backend_locator,
        candidate_budget=first.candidate_budget,
        seeds=first.seeds,
        protocol=first.protocol,
        ghost_evaluator_path=first.ghost_evaluator_path,
        ghost_protocol_path=first.ghost_protocol_path,
        source_materialization_manifest=first.source_materialization_manifest,
        stages=("followup_capture",),
    )
    monkeypatch.setattr(suite, "load_cases", lambda _path: _fake_cases())

    def fake_plan(**kwargs):
        for key in ("capture_output", "selections_output", "manifest_output"):
            _write(kwargs[key])
        return {"model_calls": 0, "network_calls": 0}

    def fake_capture(**kwargs):
        _write(kwargs["output_path"])
        _write(kwargs["manifest_path"])
        return {"model_calls": 1, "network_calls": 1}

    monkeypatch.setattr(suite, "build_capture_plan", fake_plan)
    monkeypatch.setattr(suite, "capture_followups", fake_capture)
    suite.run_b_plan(first, backend=lambda _plan: {})
    suite.run_b_plan(second, backend=lambda _plan: {})
    assert first.paths["suite_manifest"].is_file()
    assert second.paths["suite_manifest"].is_file()
    assert first.paths["suite_manifest"] != second.paths["suite_manifest"]
