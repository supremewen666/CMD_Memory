#!/usr/bin/env python3
"""Create a closed Stage 9 adapter manifest from explicit local checkouts."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess


def exact_commit(repository: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    commit = result.stdout.strip().lower()
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ValueError(f"repository did not resolve to an exact commit: {repository}")
    return commit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--cmd-repository", type=Path, required=True)
    parser.add_argument("--cmd-python", type=Path, required=True)
    parser.add_argument("--mem0-repository", type=Path, required=True)
    parser.add_argument("--mem0-python", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=1200.0)
    args = parser.parse_args()

    for repository in (args.cmd_repository, args.mem0_repository):
        if not (repository / ".git").is_dir():
            parser.error(f"not a git checkout: {repository}")
    for executable in (args.cmd_python, args.mem0_python):
        if not executable.is_file():
            parser.error(f"Python executable does not exist: {executable}")
    if not args.protocol.is_file():
        parser.error(f"protocol does not exist: {args.protocol}")

    cmd_root = args.cmd_repository.resolve()
    protocol = args.protocol.resolve()
    common = {
        "repository": str(cmd_root),
        "pinned_commit": exact_commit(cmd_root),
        "supported_tracks": ["controlled_a1", "controlled_a2"],
        "timeout_seconds": args.timeout_seconds,
    }
    value = {
        "memskill": {
            **common,
            "command": [
                str(args.cmd_python.resolve()),
                str(cmd_root / "wrappers/memskill_adapter.py"),
                "--protocol-config", str(protocol),
            ],
        },
        "erskill": {
            **common,
            "command": [
                str(args.cmd_python.resolve()),
                str(cmd_root / "wrappers/erskill_adapter.py"),
                "--protocol-config", str(protocol),
            ],
        },
        "mem0": {
            "command": [
                str(args.mem0_python.resolve()),
                str(cmd_root / "wrappers/mem0_adapter.py"),
                "--protocol-config", str(protocol),
            ],
            "repository": str(args.mem0_repository.resolve()),
            "pinned_commit": exact_commit(args.mem0_repository.resolve()),
            "supported_tracks": ["controlled_a1", "controlled_a2"],
            "timeout_seconds": args.timeout_seconds,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, args.output)
    print(f"[RESULT] output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
