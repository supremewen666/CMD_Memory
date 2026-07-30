#!/usr/bin/env python3
"""MemFail arena: cross-environment signal and ecology replication."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from experiments.arena_cli import run_arena_cli
from experiments.arena_runner_common import load_memfail_arena_cases


def main() -> int:
    return run_arena_cli(
        arena_id="memfail",
        loader=load_memfail_arena_cases,
        default_cases="data/probe_cases/memfail_cases.json",
        default_output="artifacts/arena/memfail_observations.jsonl",
        chains_default=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
