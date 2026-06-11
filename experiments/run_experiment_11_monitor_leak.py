#!/usr/bin/env python3
"""Experiment 11: leak-safe monitor contract."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.baselines.comparators import FORBIDDEN_MONITOR_FIELDS, run_baseline_suite
from cmd_audit.core.labels import VALID_MONITOR_ANOMALY_REASONS
from cmd_audit.data_io import load_probe_cases
from cmd_audit.eval.writers import write_csv_table
from experiments.experiment_runner_common import DATA, OUT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "real_three_source_cases.json"))
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    cases = load_probe_cases(args.cases)
    if args.limit:
        cases = cases[: args.limit]

    leak_counts: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    for case in cases:
        payload = run_baseline_suite(case).monitor.to_payload()
        _scan_forbidden(payload, leak_counts)
        reasons[payload["anomaly_reason"]] += 1

    coverage = len(set(reasons) & set(VALID_MONITOR_ANOMALY_REASONS)) / len(
        VALID_MONITOR_ANOMALY_REASONS
    )
    rows = [
        {"leak_field": "label", "count": str(leak_counts["label"])},
        {"leak_field": "gold_answer", "count": str(leak_counts["gold_answer"])},
        {"leak_field": "gold_evidence", "count": str(leak_counts["gold_evidence"])},
        {"leak_field": "anomaly_reason_coverage", "count": f"{coverage:.4f}"},
    ]
    for row in rows:
        print(row)

    out_path = OUT / "experiment_monitor_leak.csv"
    write_csv_table(out_path, ["leak_field", "count"], rows)
    print(f"Wrote {out_path}")


def _scan_forbidden(value: Any, counts: Counter[str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in FORBIDDEN_MONITOR_FIELDS:
                counts[key] += 1
            _scan_forbidden(nested, counts)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _scan_forbidden(nested, counts)


if __name__ == "__main__":
    main()
