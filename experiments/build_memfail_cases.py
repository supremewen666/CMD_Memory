"""Build MemFail probe cases (external-validity arm).

MemFail (arXiv 2605.26667) is a third-party memory-failure benchmark CMD did not
construct. This builder converts its five CSV datasets into CMD ``ProbeCase``
JSON so the counterfactual repair loop can be evaluated on data CMD did not
design.

This arm is ADDITIVE. The builder writes ONLY ``memfail_*.json`` files and
refuses to write anything else: the mainline probe sets
(``real_multihop_cases.json``, ``real_recurrent_cases.json``,
``real_item_layer_cases.json``, and every other ``real_*`` / ``coupled_*`` file)
hold live evidence chains behind published claims and must never be touched by
this script.

Usage::

    python experiments/build_memfail_cases.py --csv-dir /path/to/MemFail/datasets

The CSVs are expected either flat inside ``--csv-dir`` or in MemFail's own
repository layout (``long_hop/``, ``coexisting_facts/``,
``conditional_facts/easy/``, ``conditional_facts/hard/``,
``custom_persona_retrieval/``). Both layouts are probed automatically.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.adapters.memfail import (
    MEMFAIL_CSV_FILENAMES,
    MEMFAIL_TASKS,
    load_memfail_probe_cases,
    write_memfail_probe_cases,
)

PROBE_DIR = Path("data/probe_cases")

#: Only filenames matching this prefix may be written. Guards the mainline sets.
OUTPUT_PREFIX = "memfail_"

#: MemFail repo-relative subdirectories to probe for each task's CSV.
_TASK_SUBDIRS: dict[str, tuple[str, ...]] = {
    "long_hop": ("long_hop",),
    "coexisting": ("coexisting_facts",),
    "conditional_easy": ("conditional_facts/easy",),
    "conditional_hard": ("conditional_facts/hard",),
    "persona": ("custom_persona_retrieval",),
}


class OutputPathViolation(RuntimeError):
    """Raised when a write target is not a ``memfail_*.json`` file."""


def build_all(
    *,
    csv_dir: Path,
    output_dir: Path = PROBE_DIR,
    tasks: tuple[str, ...] = MEMFAIL_TASKS,
    limit: int = 0,
) -> dict[str, Any]:
    """Convert MemFail CSVs into per-task files and one combined runner input."""
    output_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"tasks": {}, "csv_dir": str(csv_dir)}
    combined_rows: list[dict[str, Any]] = []
    total = 0

    for task in tasks:
        csv_path = _resolve_csv(csv_dir, task)
        if csv_path is None:
            summary["tasks"][task] = {"status": "missing_csv"}
            continue

        out_path = _guard_output(output_dir / f"{OUTPUT_PREFIX}{task}_cases.json")
        write_memfail_probe_cases(csv_path, out_path, task=task, limit=limit)
        cases = load_memfail_probe_cases(csv_path, task=task, limit=limit)
        combined_rows.extend(
            json.loads(out_path.read_text(encoding="utf-8"))
        )
        total += len(cases)
        summary["tasks"][task] = {
            "status": "ok",
            "csv": str(csv_path),
            "output": str(out_path),
            "cases": len(cases),
            "label_counts": dict(
                Counter(case.perturbation_label for case in cases)
            ),
        }

    summary["total_cases"] = total
    combined_path = _guard_output(output_dir / "memfail_cases.json")
    combined_path.write_text(
        json.dumps(combined_rows, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    summary["combined_output"] = str(combined_path)
    return summary


def _resolve_csv(csv_dir: Path, task: str) -> Path | None:
    """Find a task's CSV flat in ``csv_dir`` or in MemFail's repo layout."""
    filename = MEMFAIL_CSV_FILENAMES[task]
    candidates = [csv_dir / filename]
    candidates.extend(
        csv_dir / subdir / filename for subdir in _TASK_SUBDIRS.get(task, ())
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _guard_output(path: Path) -> Path:
    """Refuse any write target outside the ``memfail_*.json`` namespace.

    The mainline probe sets are live evidence for published claims. A typo in
    ``--output-dir`` or a future edit to the filename template must fail loudly
    rather than overwrite them.
    """
    if not path.name.startswith(OUTPUT_PREFIX) or path.suffix != ".json":
        raise OutputPathViolation(
            f"refusing to write {path.name!r}: MemFail builder may only write "
            f"{OUTPUT_PREFIX}*.json files"
        )
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv-dir",
        type=Path,
        required=True,
        help="Directory holding the MemFail CSVs (flat or repo layout).",
    )
    parser.add_argument("--output-dir", type=Path, default=PROBE_DIR)
    parser.add_argument(
        "--tasks",
        nargs="+",
        default=list(MEMFAIL_TASKS),
        choices=sorted(MEMFAIL_TASKS),
        help="Subset of MemFail tasks to build; default builds all five.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Per-task smoke-test limit; default 0 keeps the full dataset.",
    )
    args = parser.parse_args()

    summary = build_all(
        csv_dir=args.csv_dir,
        output_dir=args.output_dir,
        tasks=tuple(args.tasks),
        limit=args.limit,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
