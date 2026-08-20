#!/usr/bin/env python3
"""E5 source-bound competitor coverage matrix (zero model calls).

The script deliberately does not infer literature claims from prose.  It
accepts a closed, curator-provided JSONL whose rows cite source IDs, verifies
the four frozen dimensions, and emits CMD-vs-competitor coverage deltas.  This
keeps the comparison reproducible without treating project memory as ground
truth.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Mapping, Sequence


COMPETITOR_RECORD_SCHEMA_VERSION = "cmd-e5-competitor-record-v1"
E5_REPORT_SCHEMA_VERSION = "cmd-e5-competitor-matrix-v1"
DIMENSIONS = (
    "gold_free",
    "quality_fault_scope",
    "counterfactual_attribution_and_repair",
    "auditable_ledger",
)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _load(path: Path) -> tuple[Mapping[str, object], ...]:
    expected = {
        "schema_version",
        "system_id",
        *DIMENSIONS,
        "source_ids",
        "notes",
    }
    rows = []
    seen = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = _mapping(json.loads(line), f"competitor row {number}")
        if set(row) != expected or row.get("schema_version") != COMPETITOR_RECORD_SCHEMA_VERSION:
            raise ValueError("competitor record is not closed or versioned")
        system_id = row["system_id"]
        if not isinstance(system_id, str) or not system_id or system_id in seen:
            raise ValueError("competitor system IDs must be non-empty and unique")
        seen.add(system_id)
        for dimension in DIMENSIONS:
            if row[dimension] not in {"yes", "partial", "no"}:
                raise ValueError("competitor dimensions must be yes/partial/no")
        sources = row["source_ids"]
        if (
            not isinstance(sources, list)
            or not sources
            or any(not isinstance(value, str) or not value for value in sources)
        ):
            raise ValueError("every competitor record requires source IDs")
        if not isinstance(row["notes"], str):
            raise ValueError("competitor notes must be a string")
        rows.append(row)
    if len(rows) < 2 or "CMD" not in seen:
        raise ValueError("E5 requires CMD and at least one competitor")
    return tuple(rows)


def run_e5(*, input_path: Path, output_json: Path, output_csv: Path) -> dict[str, object]:
    if output_json.exists() or output_csv.exists():
        raise ValueError("refusing to overwrite E5 artifacts")
    rows = _load(input_path)
    cmd = next(row for row in rows if row["system_id"] == "CMD")
    score = {"no": 0.0, "partial": 0.5, "yes": 1.0}
    comparisons = []
    for row in sorted(rows, key=lambda item: str(item["system_id"])):
        if row["system_id"] == "CMD":
            continue
        deltas = {
            dimension: score[cmd[dimension]] - score[row[dimension]]
            for dimension in DIMENSIONS
        }
        comparisons.append(
            {
                "competitor": row["system_id"],
                **{f"delta_{key}": value for key, value in deltas.items()},
                "cmd_strictly_adds_dimensions": sum(value > 0 for value in deltas.values()),
                "source_ids": "|".join(row["source_ids"]),
            }
        )
    report = {
        "schema_version": E5_REPORT_SCHEMA_VERSION,
        "input_path": str(input_path.resolve()),
        "input_sha256": _file_sha256(input_path),
        "dimension_order": list(DIMENSIONS),
        "system_count": len(rows),
        "competitor_count": len(comparisons),
        "records": [dict(row) for row in rows],
        "comparisons": comparisons,
        "project_memory_used_as_ground_truth": False,
        "source_ids_required": True,
        "model_calls": 0,
        "network_calls": 0,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    staged = []
    published = []
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{output_json.name}.", suffix=".tmp", dir=output_json.parent)
        json_tmp = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
        staged.append((json_tmp, output_json))
        descriptor, name = tempfile.mkstemp(prefix=f".{output_csv.name}.", suffix=".tmp", dir=output_csv.parent)
        csv_tmp = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            fields = [
                "competitor",
                *(f"delta_{key}" for key in DIMENSIONS),
                "cmd_strictly_adds_dimensions",
                "source_ids",
            ]
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(comparisons)
        staged.append((csv_tmp, output_csv))
        for temporary, target in staged:
            os.link(temporary, target)
            published.append(target)
            temporary.unlink(missing_ok=True)
    except Exception:
        for temporary, _target in staged:
            temporary.unlink(missing_ok=True)
        for target in published:
            target.unlink(missing_ok=True)
        raise
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args(argv)
    result = run_e5(
        input_path=args.input,
        output_json=args.output_json,
        output_csv=args.output_csv,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
