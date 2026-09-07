from __future__ import annotations

import json
from pathlib import Path
import sys
import time

import pytest

from experiments.detached_run import (
    inspect_run,
    launch_detached,
    monitor_snapshot,
    read_status_events,
    stop_run,
)


def _wait_for_terminal(run_dir: Path, timeout: float = 5.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = inspect_run(run_dir)
        if state["last_event"] in {"completed", "failed"}:
            return state
        time.sleep(0.02)
    raise AssertionError("detached worker did not reach a terminal state")


def test_detached_run_writes_machine_readable_lifecycle(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-001"
    started = time.monotonic()
    launch = launch_detached(
        run_dir=run_dir,
        run_id="run-001",
        role="v4_gpu0",
        gpu_id="0",
        command=(
            sys.executable,
            "-c",
            "import os; print('gpu=' + os.environ['CUDA_VISIBLE_DEVICES'])",
        ),
    )

    assert time.monotonic() - started < 1.0
    assert launch["role"] == "v4_gpu0"
    assert launch["gpu_id"] == "0"
    assert (run_dir / "run.pid").read_text(encoding="utf-8").strip().isdigit()
    assert json.loads((run_dir / "launch.json").read_text(encoding="utf-8")) == launch

    terminal = _wait_for_terminal(run_dir)
    assert terminal["last_event"] == "completed"
    assert terminal["exit_code"] == 0
    assert "gpu=0" in (run_dir / "run.log").read_text(encoding="utf-8")
    events = read_status_events(run_dir / "status.jsonl")
    assert [row["event"] for row in events] == ["launched", "running", "completed"]
    assert all(row["run_id"] == "run-001" for row in events)


def test_failed_worker_and_monitor_snapshot_are_fail_closed(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-failed"
    launch_detached(
        run_dir=run_dir,
        run_id="run-failed",
        role="v4_gpu1",
        gpu_id="1",
        command=(sys.executable, "-c", "raise SystemExit(7)"),
    )

    terminal = _wait_for_terminal(run_dir)
    assert terminal["last_event"] == "failed"
    assert terminal["exit_code"] == 7
    snapshot = monitor_snapshot((run_dir / "status.jsonl",))
    assert snapshot[0]["role"] == "v4_gpu1"
    assert snapshot[0]["event"] == "failed"

    status = run_dir / "status.jsonl"
    status.write_text(status.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="closed status event"):
        read_status_events(status)


def test_stop_terminates_the_detached_process_group(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-stop"
    launch_detached(
        run_dir=run_dir,
        run_id="run-stop",
        role="v4_gpu0",
        gpu_id="0",
        command=(sys.executable, "-c", "import time; time.sleep(30)"),
    )
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if inspect_run(run_dir)["last_event"] == "running":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("detached worker did not start")

    state = stop_run(run_dir)

    assert state["last_event"] == "stopping"
    assert state["last_status"]["message"] == "SIGTERM requested"
