"""Command-line interface for the standalone CMD-Audit harness."""

from __future__ import annotations

import argparse
from pathlib import Path

from .harness import (
    run_cases,
    write_attribution_table,
    write_comparison_metrics_table,
    write_confusion_matrix_table,
)
from .models import load_probe_cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cmd-audit")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run CMD-Audit V0 on probe cases")
    run_parser.add_argument(
        "--cases",
        default="data/probe_cases/v0_issue3_cases.json",
        help="path to one probe case object or a list of cases",
    )
    run_parser.add_argument(
        "--out",
        default="artifacts/attribution_table.csv",
        help="CSV path for the attribution table",
    )
    run_parser.add_argument(
        "--metrics-out",
        default="artifacts/comparison_metrics.csv",
        help="CSV path for CMD-vs-comparator diagnosis metrics",
    )
    run_parser.add_argument(
        "--confusion-out",
        default="artifacts/attribution_confusion_matrix.csv",
        help="CSV path for the CMD attribution confusion matrix",
    )

    args = parser.parse_args(argv)
    if args.command == "run":
        cases = load_probe_cases(args.cases)
        results = run_cases(cases)
        write_attribution_table(results, args.out)
        write_comparison_metrics_table(results, args.metrics_out)
        write_confusion_matrix_table(results, args.confusion_out)
        print(
            f"wrote {len(results)} attribution row(s) to {Path(args.out)} "
            f"with comparison metrics to {Path(args.metrics_out)} "
            f"and confusion matrix to {Path(args.confusion_out)}"
        )
        return 0

    parser.error(f"unknown command {args.command!r}")
    return 2
