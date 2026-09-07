from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "run_remaining_experiments.sh"


def test_shell_help_exposes_v4_detach_and_explicit_gpu_lanes() -> None:
    completed = subprocess.run(
        ("bash", str(SCRIPT), "--help"),
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    output = completed.stdout
    assert "v4_prepare" in output
    assert "v4_prepare_inputs" in output
    assert "v4_single_gpu" in output
    assert "v4_gpu0" in output
    assert "v4_gpu1" in output
    assert "v4_merge" in output
    assert "monitor" in output
    assert "status" in output
    assert "stop" in output
    assert "GPU 0: physical id 0, ports 8000/8001" in output
    assert "single A100: physical id 0, ports 8000/8001" in output
    assert "GPU 1: physical id 1, ports 8000/8001" in output
    assert "launch.json" in output
    assert "status.jsonl" in output
    assert "canonical eight-arm replay" in output


def test_shell_is_syntax_valid_and_defaults_to_its_checkout() -> None:
    subprocess.run(("bash", "-n", str(SCRIPT)), cwd=ROOT, check=True)
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'CMD_ROOT="${CMD_ROOT:-$SCRIPT_DIR}"' in source
    assert "$HOME/wsy/CMD_Memory" not in source
    assert 'CUDA_VISIBLE_DEVICES="${LANE_GPU_ID' in source
    assert "python -m experiments.prepare_v4_live_cases" in source
    assert "python -m experiments.validate_v4_prepared_cases" in source
    assert "prepared_cases.smoke.jsonl" in source
    assert "CMD_V4_MAX_RELATION_ATTEMPTS:-3" in source
    assert "--collect-proposer-failures" in source
    assert "preparation_attempt_manifest.json" in source
    assert "repair_required" in source
    assert "python -m experiments.ghost_live_protocol validate-run" in source
    assert '--ghost-evaluator "$V4_GHOST_EVALUATOR"' in source
    assert '--ghost-protocol "$V4_GHOST_PROTOCOL"' in source
    assert '--materialization-manifest "${merged}.manifest.json"' in source
    assert "CMD_V4_GHOST_AUTHORIZATION" in source
    assert "CMD_V4_GHOST_ACCESS_LEDGER" in source
    assert "CMD_V4_MODEL_MANIFEST" in source
    assert '--run-id "$RUN_ID"' in source
    assert "main_v4_single_gpu" in source
    assert 'main_v4_materialize single_gpu' in source
    assert 'single_shard="${materialized}/single_gpu.jsonl"' in source


def test_v4_gpu_roles_validate_the_exact_prepared_bundle_before_model_start() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    materialize = source.split("main_v4_materialize() {", 1)[1].split(
        "main_v4_gpu0() {", 1
    )[0]

    validation_offset = materialize.index(
        "python -m experiments.validate_v4_prepared_cases"
    )
    model_start_offset = materialize.index("start_llama_dual_vllm")
    assert validation_offset < model_start_offset
    live_gate_offset = materialize.index(
        "python -m experiments.ghost_live_protocol validate-run"
    )
    assert live_gate_offset < model_start_offset
    assert '--manifest "$preparation_manifest"' in materialize
    assert '--prepared "$V4_SOURCE_CASES"' in materialize
    assert "CMD_V4_PREPARATION_MANIFEST" in materialize
