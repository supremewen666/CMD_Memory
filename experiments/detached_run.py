#!/usr/bin/env python3
"""Detached experiment supervisor with a strict JSONL lifecycle protocol."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Mapping, Sequence


STATUS_SCHEMA_VERSION = "cmd-detached-status-v1"
LAUNCH_SCHEMA_VERSION = "cmd-detached-launch-v1"
_STATUS_KEYS = {
    "schema_version",
    "timestamp",
    "run_id",
    "role",
    "gpu_id",
    "event",
    "pid",
    "exit_code",
    "message",
}
_EVENTS = frozenset(
    {"launched", "running", "completed", "failed", "stopping", "stopped"}
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def append_status_event(
    path: Path,
    *,
    run_id: str,
    role: str,
    gpu_id: str | None,
    event: str,
    pid: int,
    exit_code: int | None = None,
    message: str = "",
) -> dict[str, object]:
    if event not in _EVENTS:
        raise ValueError(f"unregistered detached status event: {event}")
    if not run_id or not role or pid <= 0:
        raise ValueError("status event requires run_id, role, and a positive pid")
    row: dict[str, object] = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "timestamp": _now(),
        "run_id": run_id,
        "role": role,
        "gpu_id": gpu_id,
        "event": event,
        "pid": pid,
        "exit_code": exit_code,
        "message": message,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as stream:
        stream.write(encoded + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return row


def _validated_status_row(value: object, line_number: int) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _STATUS_KEYS:
        raise ValueError(f"line {line_number}: closed status event schema mismatch")
    row = dict(value)
    if row["schema_version"] != STATUS_SCHEMA_VERSION:
        raise ValueError(f"line {line_number}: unknown status schema version")
    if row["event"] not in _EVENTS:
        raise ValueError(f"line {line_number}: unknown status event")
    if not isinstance(row["pid"], int) or isinstance(row["pid"], bool) or row["pid"] <= 0:
        raise ValueError(f"line {line_number}: invalid status pid")
    if row["exit_code"] is not None and (
        not isinstance(row["exit_code"], int) or isinstance(row["exit_code"], bool)
    ):
        raise ValueError(f"line {line_number}: invalid exit code")
    for name in ("timestamp", "run_id", "role", "message"):
        if not isinstance(row[name], str):
            raise ValueError(f"line {line_number}: {name} must be a string")
    if row["gpu_id"] is not None and not isinstance(row["gpu_id"], str):
        raise ValueError(f"line {line_number}: gpu_id must be a string or null")
    return row


def read_status_events(path: Path) -> tuple[dict[str, object], ...]:
    if not path.exists():
        return ()
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"line {line_number}: invalid status JSONL") from error
        rows.append(_validated_status_row(value, line_number))
    return tuple(rows)


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def inspect_run(run_dir: Path) -> dict[str, object]:
    pid_path = run_dir / "run.pid"
    if not pid_path.exists():
        raise ValueError(f"run has no PID file: {run_dir}")
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except ValueError as error:
        raise ValueError("run PID file is invalid") from error
    events = read_status_events(run_dir / "status.jsonl")
    latest = events[-1] if events else None
    return {
        "run_dir": str(run_dir.resolve()),
        "pid": pid,
        "alive": _pid_is_alive(pid),
        "last_event": None if latest is None else latest["event"],
        "exit_code": None if latest is None else latest["exit_code"],
        "last_status": latest,
    }


def monitor_snapshot(paths: Sequence[Path]) -> tuple[dict[str, object], ...]:
    result: list[dict[str, object]] = []
    for path in paths:
        events = read_status_events(path)
        if not events:
            result.append({"status_path": str(path.resolve()), "event": "missing"})
            continue
        result.append({"status_path": str(path.resolve()), **events[-1]})
    return tuple(result)


def launch_detached(
    *,
    run_dir: Path,
    run_id: str,
    role: str,
    gpu_id: str | None,
    command: Sequence[str],
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    if not run_id or not role or not command:
        raise ValueError("detached launch requires run_id, role, and command")
    run_dir.mkdir(parents=True, exist_ok=True)
    launch_path = run_dir / "launch.json"
    if launch_path.exists():
        existing = inspect_run(run_dir)
        if existing["alive"]:
            raise ValueError(f"run is already active: {run_dir}")
        raise ValueError(f"run directory is immutable and already used: {run_dir}")

    status_path = run_dir / "status.jsonl"
    log_path = run_dir / "run.log"
    env = dict(os.environ)
    if environment:
        env.update(environment)
    env.update(
        {
            "CMD_RUN_DIR": str(run_dir.resolve()),
            "CMD_RUN_ID": run_id,
            "CMD_RUN_ROLE": role,
            "CMD_STATUS_JSONL": str(status_path.resolve()),
        }
    )
    if gpu_id is not None:
        env["CUDA_VISIBLE_DEVICES"] = gpu_id
        env["CMD_GPU_ID"] = gpu_id
    project_root = str(Path(__file__).resolve().parent.parent)
    current_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        project_root if not current_pythonpath else f"{project_root}{os.pathsep}{current_pythonpath}"
    )
    worker_command = (
        sys.executable,
        "-m",
        "experiments.detached_run",
        "_worker",
        "--run-dir",
        str(run_dir.resolve()),
        "--run-id",
        run_id,
        "--role",
        role,
        "--gpu-id",
        "" if gpu_id is None else gpu_id,
        "--",
        *tuple(command),
    )
    process = subprocess.Popen(  # noqa: S603 - the caller supplies the intended argv.
        worker_command,
        cwd=project_root,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    (run_dir / "run.pid").write_text(f"{process.pid}\n", encoding="utf-8")
    created_at = _now()
    launch: dict[str, object] = {
        "schema_version": LAUNCH_SCHEMA_VERSION,
        "created_at": created_at,
        "run_id": run_id,
        "role": role,
        "gpu_id": gpu_id,
        "command": list(command),
        "pid": process.pid,
        "status_jsonl": str(status_path.resolve()),
        "log_path": str(log_path.resolve()),
    }
    append_status_event(
        status_path,
        run_id=run_id,
        role=role,
        gpu_id=gpu_id,
        event="launched",
        pid=process.pid,
        message="detached supervisor launched",
    )
    _atomic_json(launch_path, launch)
    return launch


def _worker(
    *,
    run_dir: Path,
    run_id: str,
    role: str,
    gpu_id: str | None,
    command: Sequence[str],
) -> int:
    launch_path = run_dir / "launch.json"
    deadline = time.monotonic() + 10.0
    while not launch_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    status_path = run_dir / "status.jsonl"
    pid = os.getpid()
    append_status_event(
        status_path,
        run_id=run_id,
        role=role,
        gpu_id=gpu_id,
        event="running",
        pid=pid,
        message="worker command started",
    )
    with (run_dir / "run.log").open("a", encoding="utf-8") as log:
        log.write(f"[{_now()}] role={role} gpu_id={gpu_id} command={json.dumps(command)}\n")
        log.flush()
        try:
            completed = subprocess.run(  # noqa: S603 - frozen argv, never a shell.
                tuple(command),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            )
            code = int(completed.returncode)
        except Exception as error:  # pragma: no cover - OS launch failures vary.
            log.write(f"supervisor launch error: {error!r}\n")
            code = 126
    event = "completed" if code == 0 else "failed"
    append_status_event(
        status_path,
        run_id=run_id,
        role=role,
        gpu_id=gpu_id,
        event=event,
        pid=pid,
        exit_code=code,
        message=f"worker command exited with code {code}",
    )
    return code


def stop_run(run_dir: Path) -> dict[str, object]:
    state = inspect_run(run_dir)
    latest = state["last_status"]
    if not isinstance(latest, Mapping):
        raise ValueError("cannot stop run without a status identity")
    pid = int(state["pid"])
    if not state["alive"]:
        return state
    append_status_event(
        run_dir / "status.jsonl",
        run_id=str(latest["run_id"]),
        role=str(latest["role"]),
        gpu_id=latest["gpu_id"] if isinstance(latest["gpu_id"], str) else None,
        event="stopping",
        pid=pid,
        message="SIGTERM requested",
    )
    os.killpg(pid, signal.SIGTERM)
    return inspect_run(run_dir)


def _command_after_separator(values: Sequence[str]) -> tuple[str, ...]:
    result = tuple(values)
    if result and result[0] == "--":
        result = result[1:]
    if not result:
        raise ValueError("missing worker command after --")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    launch_parser = subparsers.add_parser("launch")
    launch_parser.add_argument("--run-dir", type=Path, required=True)
    launch_parser.add_argument("--run-id", required=True)
    launch_parser.add_argument("--role", required=True)
    launch_parser.add_argument("--gpu-id")
    launch_parser.add_argument("command", nargs=argparse.REMAINDER)
    worker_parser = subparsers.add_parser("_worker")
    worker_parser.add_argument("--run-dir", type=Path, required=True)
    worker_parser.add_argument("--run-id", required=True)
    worker_parser.add_argument("--role", required=True)
    worker_parser.add_argument("--gpu-id", default="")
    worker_parser.add_argument("command", nargs=argparse.REMAINDER)
    inspect_parser = subparsers.add_parser("status")
    inspect_parser.add_argument("--run-dir", type=Path, required=True)
    monitor_parser = subparsers.add_parser("monitor")
    monitor_parser.add_argument("--status-jsonl", type=Path, action="append", required=True)
    monitor_parser.add_argument("--follow", action="store_true")
    monitor_parser.add_argument("--interval", type=float, default=1.0)
    stop_parser = subparsers.add_parser("stop")
    stop_parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.action == "launch":
        value = launch_detached(
            run_dir=args.run_dir,
            run_id=args.run_id,
            role=args.role,
            gpu_id=args.gpu_id,
            command=_command_after_separator(args.command),
        )
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    if args.action == "_worker":
        return _worker(
            run_dir=args.run_dir,
            run_id=args.run_id,
            role=args.role,
            gpu_id=args.gpu_id or None,
            command=_command_after_separator(args.command),
        )
    if args.action == "status":
        print(json.dumps(inspect_run(args.run_dir), ensure_ascii=False, sort_keys=True))
        return 0
    if args.action == "stop":
        print(json.dumps(stop_run(args.run_dir), ensure_ascii=False, sort_keys=True))
        return 0
    if args.interval <= 0:
        parser.error("--interval must be positive")
    seen = [-1] * len(args.status_jsonl)
    while True:
        snapshots = monitor_snapshot(args.status_jsonl)
        for index, snapshot in enumerate(snapshots):
            events = read_status_events(args.status_jsonl[index])
            if len(events) != seen[index]:
                print(json.dumps(snapshot, ensure_ascii=False, sort_keys=True), flush=True)
                seen[index] = len(events)
        if not args.follow:
            return 0
        time.sleep(min(args.interval, 60.0))


if __name__ == "__main__":
    raise SystemExit(main())
