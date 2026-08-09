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
    assert "v4_gpu0" in output
    assert "v4_gpu1" in output
    assert "v4_merge" in output
    assert "monitor" in output
    assert "status" in output
    assert "stop" in output
    assert "GPU 0: physical id 0, ports 8000/8001" in output
    assert "GPU 1: physical id 1, ports 8100/8101" in output
    assert "launch.json" in output
    assert "status.jsonl" in output


def test_shell_is_syntax_valid_and_defaults_to_its_checkout() -> None:
    subprocess.run(("bash", "-n", str(SCRIPT)), cwd=ROOT, check=True)
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'CMD_ROOT="${CMD_ROOT:-$SCRIPT_DIR}"' in source
    assert '$HOME/wsy/CMD_Memory' not in source
    assert 'CUDA_VISIBLE_DEVICES="${LANE_GPU_ID' in source
