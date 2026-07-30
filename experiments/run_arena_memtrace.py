#!/usr/bin/env python3
"""MemTrace-B arena: gold-free signal, ecology, and chain observations."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from experiments.arena_cli import run_arena_cli
from experiments.arena_runner_common import load_memtrace_arena_cases


def main() -> int:
    return run_arena_cli(
        arena_id="memtrace",
        loader=load_memtrace_arena_cases,
        default_cases="data/probe_cases/memtrace_kp_cases.json",
        default_output="artifacts/arena/memtrace_observations.jsonl",
        chains_default=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
