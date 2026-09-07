from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cmd_audit.core.state_codec import content_sha256
from experiments.run_evobench_harness import HarnessRun, ROOT_FIELDS, validation_metadata

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "data/external/evobench/public_validation/benchmark/suites/evobench_validation.json"
SEED = ROOT / "data/external/evobench/public_validation/policy_harness_seed"


def _json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8"); return path


def _root(value: str) -> str: return content_sha256(value)


def _roots(*, harness: str, suite: str, previous: str = "0" * 64) -> dict[str, str]:
    return {key: (harness if key == "harness_root" else suite if key == "task_suite_root" else previous if key == "previous_root" else _root(key)) for key in ROOT_FIELDS}


def _setup(tmp_path: Path) -> tuple[HarnessRun, dict[str, str], Path]:
    suite_root, _, _ = validation_metadata(SUITE)
    from experiments.run_evobench_harness import _sha_tree
    roots = _roots(harness=_sha_tree(SEED), suite=suite_root)
    run = HarnessRun(tmp_path / "run")
    run.init({"run_id": "p3d-smoke", "seed_harness_path": str(SEED), "roots": roots, "arms": [
        {"arm_id": "seed", "kind": "seed", "description": "seed"}, {"arm_id": "static", "kind": "static", "description": "static"},
        {"arm_id": "cmd", "kind": "cmd", "description": "typed CMD"}, {"arm_id": "ghost", "kind": "ghost", "description": "GHOST/Thompson"}]}, SUITE)
    artifact = tmp_path / "candidate.json"; artifact.write_text('{"external":"candidate"}\n', encoding="utf-8")
    return run, roots, artifact


def _prepare(run: HarnessRun, roots: dict[str, str], artifact: Path, candidate: str = "candidate-a") -> dict[str, str]:
    from experiments.run_evobench_harness import _sha_tree
    prepared = dict(roots); prepared["previous_root"] = run.ledger.head; prepared["harness_root"] = _sha_tree(artifact)
    run.prepare({"candidate_id": candidate, "arm_id": "cmd", "artifact_path": str(artifact), "roots": prepared, "evolver_id": "external-evolver"})
    return prepared


def _record(run: HarnessRun, roots: dict[str, str], candidate: str = "candidate-a") -> None:
    recorded = dict(roots); recorded["previous_root"] = run.ledger.head
    run.record_validation({"candidate_id": candidate, "executor_id": "fake-external-metadata-only", "task_scores": [
        {"task_id": "apex-0b6e147c84754379a4e8f3a9057336f8", "score": 0.5},
        {"task_id": "apex-2299b89dcaf64a4da4f3d03f8aac7215", "score": 0.6},
        {"task_id": "apex-260818eebc2a4366af65fe8f3f17910f", "score": 0.7}], "cost": 3.0, "failed_runs": 0, "roots": recorded}, SUITE)


def test_public_validation_is_exactly_160_nested_tasks() -> None:
    root, ids, strata = validation_metadata(SUITE)
    assert len(root) == 64 and len(ids) == 160 and sum(strata.values()) == 160


def test_lifecycle_freeze_sealed_boundary_and_resume(tmp_path: Path) -> None:
    run, roots, artifact = _setup(tmp_path); candidate_roots = _prepare(run, roots, artifact); _record(run, candidate_roots)
    run.commit("candidate-a"); run.freeze("candidate-a")
    with pytest.raises(ValueError, match="frozen"):
        _prepare(run, roots, artifact, "late-edit")
    request_spec = {"request_id": _root("request"), "sealed_suite_root": _root("opaque-sealed-suite"), "budget_root": roots["budget_root"], "runtime_root": roots["runtime_root"], "scorer_contract_root": roots["scorer_contract_root"]}
    export = run.export_eval(request_spec, tmp_path / "request.json")
    assert export["sealed_task_content_included"] is False
    with pytest.raises(ValueError, match="root mismatch"):
        run.ingest_eval({**{key: export[key] for key in ("request_root", "frozen_root", "sealed_suite_root", "budget_root", "runtime_root", "scorer_contract_root")}, "sealed_suite_root": _root("wrong"), "native_score": 1.0, "cost": 2.0, "failed_runs": 0})
    restored = HarnessRun(tmp_path / "run")
    assert restored.status()["frozen_root"] == run.status()["frozen_root"]
    audit = restored.audit(SUITE)
    assert audit["audit_passed"] and audit["memory_metric_pooling"] == "prohibited"


def test_prepared_crash_resume_is_exactly_once_and_closed(tmp_path: Path) -> None:
    run, roots, artifact = _setup(tmp_path)
    candidate_roots = _prepare(run, roots, artifact)
    # Simulates a process exit after the fsync prepare/checkpoint boundary.
    resumed = HarnessRun(tmp_path / "run")
    assert resumed.status()["prepared"] == ["candidate-a"] and resumed.status()["committed"] == []
    _record(resumed, candidate_roots)
    with pytest.raises(ValueError, match="duplicate"):
        _record(HarnessRun(tmp_path / "run"), candidate_roots)
    with pytest.raises(ValueError, match="closed"):
        resumed.prepare({"candidate_id": "candidate-b", "arm_id": "cmd", "artifact_path": str(artifact), "roots": candidate_roots, "evolver_id": "x", "unexpected": True})


def test_rollback_collision_tamper_and_cli_smoke(tmp_path: Path) -> None:
    run, roots, artifact = _setup(tmp_path); _prepare(run, roots, artifact); run.rollback("candidate-a", "validation regression")
    with pytest.raises(ValueError, match="candidate cannot"):
        run.commit("candidate-a")
    journal = run.directory / "harness_events.jsonl"; journal.write_text(journal.read_text(encoding="utf-8").replace("candidate_rolled_back", "candidate_xolled_back"), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        HarnessRun(run.directory)
    cli = tmp_path / "cli"; spec = {"run_id": "cli-smoke", "seed_harness_path": str(SEED), "roots": roots, "arms": [{"arm_id": "seed", "kind": "seed", "description": "seed"}]}
    spec_path = _json(tmp_path / "init.json", spec)
    result = subprocess.run([sys.executable, "-m", "experiments.run_evobench_harness", "init", "--run-dir", str(cli), "--spec", str(spec_path), "--validation-suite", str(SUITE)], cwd=ROOT, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["memory_metric_pooling"] == "prohibited"
