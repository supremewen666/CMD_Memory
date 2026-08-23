from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from experiments.run_longmemeval_e2e import (
    AnswerResult,
    OpenAICompatibleAnswerer,
    _safe_instance_name,
)
from experiments.run_remaining_live_experiment import _parser, execute


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "run_remaining_experiment.sh"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("bash", str(SCRIPT), *args),
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _prerequisites(tmp_path: Path) -> tuple[str, ...]:
    data = tmp_path / "data.json"
    data.write_text(
        json.dumps([{"question_id": "q1", "question": "What?", "answer": "sealed"}]),
        encoding="utf-8",
    )
    data_root = hashlib.sha256(data.read_bytes()).hexdigest()
    p4c1 = tmp_path / "p4c1"
    p4c1.mkdir()
    (p4c1 / "p4c1_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "cmd-p4c1-real-source-zero-call-v1",
                "status": "success",
                "runtime_uses_gold": False,
                "runtime_uses_labels": False,
                "router_feedback": "EccRepairReceipt",
                "model_call_count": 0,
                "source_roots": {"longmemeval": data_root},
            }
        ),
        encoding="utf-8",
    )
    prior = tmp_path / "prior"
    prior.mkdir()
    (prior / "prior_calibration_manifest.json").write_text(
        json.dumps({"mix_ghost_ready": True}), encoding="utf-8"
    )
    retrieval = tmp_path / "retrieval"
    retrieval.mkdir()
    (retrieval / "manifest.json").write_text(
        json.dumps({"schema_version": "cmd-longmemeval-m0-r1-v3"}),
        encoding="utf-8",
    )
    for arm in ("vanilla", "static", "cmd", "ghost"):
        arm_dir = retrieval / "retrieval" / arm
        arm_dir.mkdir(parents=True)
        (arm_dir / f"{_safe_instance_name('q1')}.json").write_text(
            json.dumps(
                {
                    "schema_version": "cmd-longmemeval-retrieval-v1",
                    "question_id": "q1",
                    "arm": arm,
                    "records": [],
                }
            ),
            encoding="utf-8",
        )
    return (
        "--p4c1-run", str(p4c1),
        "--prior-run", str(prior),
        "--retrieval-run", str(retrieval),
        "--data", str(data),
    )


def test_default_is_zero_call_plan_and_does_not_write(tmp_path: Path) -> None:
    output = tmp_path / "must-not-exist"
    completed = _run()
    assert completed.returncode == 0, completed.stderr
    plan = json.loads(completed.stdout)
    assert plan["mode"] == "plan"
    assert plan["external_calls_authorized"] is False
    assert plan["runtime_gold_free"] is True
    assert plan["router_feedback"] == "EccRepairReceipt-only"
    assert plan["primary_claim"] == "gold-free memory fault correction and evolution"
    assert [row["stage"] for row in plan["mainline_stages"]] == [
        "p4c1", "p4c3", "p4c45"
    ]
    assert plan["supplementary_stages"] == ["p4c2", "p4c6"]
    assert plan["legacy_stages"] == ["legacy-answer"]
    assert not output.exists()


def test_help_names_primary_claim_and_zero_call_boundary() -> None:
    completed = _run("--help")
    assert completed.returncode == 0
    assert "--plan" in completed.stdout
    assert "--verify" in completed.stdout
    assert "gold-free memory fault correction and evolution" in completed.stdout
    assert "no commit authority" in completed.stdout


def test_execute_requires_explicit_config() -> None:
    completed = _run("legacy-answer", "--execute")
    assert completed.returncode != 0
    assert "--llm-config is required" in completed.stderr


def test_preflight_binds_ready_artifacts_and_redacts_secret(tmp_path: Path) -> None:
    config = tmp_path / "llm.json"
    secret = "must-never-appear"
    config.write_text(
        json.dumps(
            {
                "base_url": "http://127.0.0.1:9/v1",
                "api_key": secret,
                "model": "frozen-answer-model",
            }
        ),
        encoding="utf-8",
    )
    completed = _run(
        "legacy-answer", "--preflight", "--llm-config", str(config), "--limit", "1",
        *_prerequisites(tmp_path),
    )
    assert completed.returncode == 0, completed.stderr
    assert secret not in completed.stdout
    report = json.loads(completed.stdout)
    assert report["preflight_passed"] is True
    assert report["external_calls_authorized"] is False
    assert report["llm_config"] == {
        "base_url": "http://127.0.0.1:9/v1",
        "credential_present": True,
        "model": "frozen-answer-model",
    }
    assert set(report["roots"]) == {
        "p4c1_manifest_sha256",
        "prior_manifest_sha256",
        "retrieval_manifest_sha256",
        "data_sha256",
    }


def test_execute_reaches_provider_boundary_and_seals_predictions(
    tmp_path: Path, monkeypatch,
) -> None:
    calls: list[str] = []

    def answer(self, request):
        calls.append(request.question)
        return AnswerResult("bounded provider response", 1)

    monkeypatch.setattr(OpenAICompatibleAnswerer, "answer", answer)
    secret = "provider-boundary-secret"
    config = tmp_path / "llm.json"
    config.write_text(
        json.dumps(
            {
                "base_url": "https://provider.invalid/v1",
                "api_key": secret,
                "model": "provider-contract-model",
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "live"
    args = _parser().parse_args(
        (
            "--execute",
            "--llm-config",
            str(config),
            "--limit",
            "1",
            "--output",
            str(output),
            *_prerequisites(tmp_path),
        )
    )
    execute(args)
    manifest = json.loads((output / "remaining_live_manifest.json").read_text())
    assert manifest["status"] == "prediction_sealed"
    assert manifest["prediction_case_count"] == 1
    assert manifest["prediction_count"] == 4
    assert manifest["sealed_score_opened"] is False
    assert manifest["router_updated_from_predictions"] is False
    assert len(calls) == 4
    assert secret not in (output / "remaining_live_manifest.json").read_text()
    assert (output / "prediction_seal.json").is_file()
    assert not (output / "score_report.json").exists()


def test_shell_is_syntax_valid_and_never_sources_credentials() -> None:
    subprocess.run(("bash", "-n", str(SCRIPT)), cwd=ROOT, check=True)
    source = SCRIPT.read_text(encoding="utf-8")
    assert "source " not in source
    assert ". $" not in source
    assert "run_remaining_experiments.sh" not in source
    assert "experiments.run_remaining_live_experiment" in source
    assert "experiments.run_p4c_mainline" in source
    assert "experiments.run_p4c2_live_efficacy" in source
    assert "experiments.run_p4c3_native_detection" in source
    assert "experiments.run_p4c45_zero_call" in source
    assert "experiments.run_p4c6_sealed_evaluation" in source


def test_shell_dispatches_p4c2_without_breaking_default_plan() -> None:
    stages = _run("--stages")
    assert stages.returncode == 0
    assert stages.stdout.splitlines() == [
        "mainline", "p4c1", "p4c3", "p4c45", "p4c2", "p4c6", "legacy-answer"
    ]
    p4c2 = _run("p4c2", "--plan", "--limit", "2")
    assert p4c2.returncode == 0, p4c2.stderr
    plan = json.loads(p4c2.stdout)
    assert plan["stage"] == "P4C-2 repair-vs-control paired live efficacy"
    assert plan["planned_calls"] == 4
    assert plan["external_calls_authorized"] is False

    legacy = _run("legacy-answer", "--plan", "--limit", "2")
    assert legacy.returncode == 0, legacy.stderr
    legacy_plan = json.loads(legacy.stdout)
    assert legacy_plan["paper_role"] == "legacy"
    assert legacy_plan["mainline"] is False
    assert legacy_plan["runtime_gold_free"] is False
    assert legacy_plan["strict_gold_free_status"] == "failed_nested_has_answer_projection"
