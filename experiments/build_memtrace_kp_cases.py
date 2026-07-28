"""Build MemTrace-B protocol knowledge-point probe cases from HaluMem source.

This writes an **additive external-validity arm** only. It writes exclusively to
``data/probe_cases/memtrace_kp_*.json`` and never touches any existing probe
dataset: ``real_multihop_cases.json``, ``real_recurrent_cases.json``,
``real_item_layer_cases.json`` and the rest hold live evidence chains behind
published claims. The output-path guard below enforces that.

Provenance: MemTrace-B (arXiv 2606.17328) released no data. This is a protocol
reimplementation over public HaluMem source data, not a replication of their
artifact — see ``cmd_audit/adapters/memtrace_kp.py`` for the full disclaimer.

Obtaining the HaluMem source
============================
``huggingface.co`` and ``raw.githubusercontent.com`` may be blocked; the GitHub
blob API works. Fetch and decode with stdlib only:

    python -c "
    import base64, json, urllib.request
    url = 'https://api.github.com/repos/MemTensor/HaluMem/git/blobs/62566339d4b90678a63b0e53ac71a8aca1f936b0'
    blob = json.loads(urllib.request.urlopen(url).read())
    open('data/raw_cases/halumem_stage4_1_events2memories.jsonl','wb').write(
        base64.b64decode(blob['content']))
    "

Then:

    python -m experiments.build_memtrace_kp_cases \\
        --input data/raw_cases/halumem_stage4_1_events2memories.jsonl

Usage
=====
    --users N         only the first N HaluMem users (0 = all 20)
    --checkpoints N   chronological memory-age checkpoints per user (paper: 8)
    --kps-per-user N  knowledge points sampled per user
    --limit N         cap total cases (smoke tests; 0 = no cap)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.adapters.memtrace_kp import (  # noqa: E402
    DEFAULT_CHECKPOINTS,
    DEFAULT_KPS_PER_USER,
    iter_halumem_records,
    memtrace_kp_dimensions,
    write_memtrace_kp_probe_cases,
)
from cmd_audit.data_io import load_probe_cases_v1

PROBE_DIR = Path("data/probe_cases")
DEFAULT_INPUT = Path("data/raw_cases/halumem_stage4_1_events2memories.jsonl")
DEFAULT_OUTPUT = PROBE_DIR / "memtrace_kp_cases.json"

#: Only this filename prefix may be written. Any other target is refused, so a
#: mistyped path can never overwrite a live main-line dataset.
OUTPUT_PREFIX = "memtrace_kp_"


class UnsafeOutputPathError(ValueError):
    """Raised when the requested output path is not a memtrace_kp_* file."""


def assert_safe_output_path(path: Path) -> Path:
    """Refuse any output filename outside the ``memtrace_kp_*`` namespace."""
    if not path.name.startswith(OUTPUT_PREFIX):
        raise UnsafeOutputPathError(
            f"refusing to write {path.name!r}: this builder may only write "
            f"{OUTPUT_PREFIX}*.json files, never an existing probe dataset"
        )
    return path


def build(
    *,
    input_path: Path = DEFAULT_INPUT,
    output_path: Path = DEFAULT_OUTPUT,
    users: int = 0,
    checkpoints: int = DEFAULT_CHECKPOINTS,
    kps_per_user: int = DEFAULT_KPS_PER_USER,
    limit: int = 0,
) -> tuple[Path, Path, int]:
    """Write probe cases plus a dimension sidecar; return both paths and count."""
    assert_safe_output_path(output_path)

    out = write_memtrace_kp_probe_cases(
        input_path,
        output_path,
        users=users,
        checkpoints=checkpoints,
        kps_per_user=kps_per_user,
        limit=limit,
    )

    # The dimension sidecar carries the grouping variables (memory age,
    # question type, evidence condition) that ProbeCase has no field for.
    kept = {case.case_id for case in load_probe_cases_v1(out)}
    rows = [
        row
        for record in iter_halumem_records(input_path, users=users)
        for row in memtrace_kp_dimensions(
            record, checkpoints=checkpoints, kps_per_user=kps_per_user
        )
        if row["case_id"] in kept
    ]
    sidecar = assert_safe_output_path(out.with_name(out.stem + "_dimensions.csv"))
    with sidecar.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)

    return out, sidecar, len(kept)


def _report(cases_path: Path, rows: list[dict[str, str]]) -> None:
    from collections import Counter

    print(f"Wrote {len(rows)} cases to {cases_path}")
    for dimension in (
        "perturbation_label",
        "question_type",
        "evidence_condition",
        "kp_type",
        "probe_class",
    ):
        counts = Counter(row[dimension] or "(none)" for row in rows)
        rendered = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        print(f"  {dimension:20} {rendered}")
    ages = sorted({int(row["age_sessions"]) for row in rows})
    if ages:
        print(f"  {'age_sessions':20} min={ages[0]} max={ages[-1]} distinct={len(ages)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build MemTrace-B protocol probe cases from HaluMem source data. "
            "Writes only data/probe_cases/memtrace_kp_*.json."
        )
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--users", type=int, default=0, help="First N HaluMem users; 0 = all."
    )
    parser.add_argument(
        "--checkpoints",
        type=int,
        default=DEFAULT_CHECKPOINTS,
        help=f"Memory-age checkpoints per user (paper: {DEFAULT_CHECKPOINTS}).",
    )
    parser.add_argument(
        "--kps-per-user", type=int, default=DEFAULT_KPS_PER_USER
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Cap total cases; 0 = no cap."
    )
    args = parser.parse_args()

    if not args.input.exists():
        parser.error(
            f"HaluMem source not found at {args.input}. See this module's "
            f"docstring for the GitHub blob API fetch command."
        )

    cases_path, sidecar, count = build(
        input_path=args.input,
        output_path=args.output,
        users=args.users,
        checkpoints=args.checkpoints,
        kps_per_user=args.kps_per_user,
        limit=args.limit,
    )

    with sidecar.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    _report(cases_path, rows)
    print(f"  dimensions sidecar  {sidecar}")

    # A case the loader rejects is worthless; verify before declaring success.
    reloaded = load_probe_cases_v1(cases_path)
    assert len(reloaded) == count, "loader round-trip lost cases"
    print(f"  load_probe_cases_v1 OK ({len(reloaded)} cases)")


if __name__ == "__main__":
    main()
