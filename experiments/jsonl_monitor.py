#!/usr/bin/env python3
"""Stream compact snapshots from heterogeneous experiment JSONL files."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time
from typing import Mapping, Sequence


_TERMINAL_EVENTS = frozenset({"completed", "failed", "stopped"})


def read_jsonl(path: Path) -> tuple[dict[str, object], ...]:
    if not path.exists():
        return ()
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSONL at {path}:{line_number}") from error
        if not isinstance(value, Mapping):
            raise ValueError(f"JSONL row must be a JSON object at {path}:{line_number}")
        rows.append(dict(value))
    return tuple(rows)


def snapshot(paths: Sequence[Path]) -> tuple[dict[str, object], ...]:
    result: list[dict[str, object]] = []
    for path in paths:
        rows = read_jsonl(path)
        if not rows:
            result.append(
                {
                    "source": str(path.resolve()),
                    "line_count": 0,
                    "event": "missing",
                }
            )
            continue
        latest = rows[-1]
        value: dict[str, object] = {
            "source": str(path.resolve()),
            "line_count": len(rows),
            **latest,
        }
        completed = latest.get("completed")
        total = latest.get("total")
        if (
            isinstance(completed, (int, float))
            and not isinstance(completed, bool)
            and isinstance(total, (int, float))
            and not isinstance(total, bool)
            and math.isfinite(float(completed))
            and math.isfinite(float(total))
            and float(total) > 0
        ):
            value["progress_fraction"] = float(completed) / float(total)
        result.append(value)
    return tuple(result)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--exit-when-terminal", action="store_true")
    args = parser.parse_args(argv)
    if args.interval <= 0:
        parser.error("--interval must be positive")
    seen = [-1] * len(args.input)
    while True:
        current = snapshot(args.input)
        for index, row in enumerate(current):
            line_count = int(row.get("line_count", 0))
            if seen[index] != line_count:
                print(json.dumps(row, ensure_ascii=False, sort_keys=True), flush=True)
                seen[index] = line_count
        if not args.follow:
            return 0
        if args.exit_when_terminal and all(
            row.get("event") in _TERMINAL_EVENTS for row in current
        ):
            return 0
        time.sleep(min(args.interval, 60.0))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "read_jsonl", "snapshot"]
