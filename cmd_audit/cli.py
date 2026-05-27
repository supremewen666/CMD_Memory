"""Command-line interface for the standalone CMD-Audit harness."""

from __future__ import annotations

import argparse
from pathlib import Path

from .harness import (
    run_cases,
    run_cases_v1,
    run_cases_v1_with_hook,
    run_full_real_suite,
    write_attribution_table,
    write_comparison_metrics_table,
    write_confusion_matrix_table,
)
from .models import load_all_real_cases, load_probe_cases, load_probe_cases_v1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cmd-audit")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── V0 run ──────────────────────────────────────────────────────────

    run_parser = subparsers.add_parser("run", help="run CMD-Audit V0 on probe cases")
    run_parser.add_argument(
        "--cases",
        default="data/probe_cases/v0_issue3_cases.json",
        help="path to probe case JSON file",
    )
    run_parser.add_argument(
        "--out",
        default="artifacts/attribution_table.csv",
        help="CSV path for the attribution table",
    )
    run_parser.add_argument(
        "--output",
        default=None,
        help="output directory for attribution, metrics, and confusion artifacts",
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
    run_parser.add_argument(
        "--on-the-fly-baseline-rescore",
        action="store_true",
        help=(
            "enable runtime baseline rescore when an agent/scorer stack is "
            "configured by the caller"
        ),
    )

    # ── V1 run ──────────────────────────────────────────────────────────

    v1_parser = subparsers.add_parser(
        "run-v1", help="run CMD-Audit V1 on probe cases"
    )
    v1_parser.add_argument(
        "--cases",
        default=None,
        help="path to probe case JSON file (V1 labels accepted)",
    )
    v1_parser.add_argument(
        "--real-data",
        action="store_true",
        help="run on all 601 real-data probe cases",
    )
    v1_parser.add_argument(
        "--out-dir",
        default="artifacts/sandbox",
        help="output directory for artifacts",
    )
    v1_parser.add_argument(
        "--output",
        default=None,
        help="alias for --out-dir",
    )
    v1_parser.add_argument(
        "--use-hook",
        dest="use_hook",
        action="store_true",
        default=True,
        help="enable Pre-CMD Hook gating (default)",
    )
    v1_parser.add_argument(
        "--no-hook",
        dest="use_hook",
        action="store_false",
        help="disable Pre-CMD Hook (run all 10 replays per case)",
    )
    v1_parser.add_argument(
        "--no-prefilter",
        dest="use_hook",
        action="store_false",
        help="deprecated alias for --no-hook",
    )
    v1_parser.add_argument(
        "--tie-margin",
        type=float,
        default=0.0,
        help="attribution tie margin for V1 runs (default: 0.0)",
    )
    v1_parser.add_argument(
        "--on-the-fly-baseline-rescore",
        action="store_true",
        help=(
            "enable runtime baseline rescore when an agent/scorer stack is "
            "configured by the caller"
        ),
    )

    # ── Parse and dispatch ──────────────────────────────────────────────

    args = parser.parse_args(argv)

    if args.command == "run":
        if args.output:
            dest = Path(args.output)
            args.out = dest / "attribution_table.csv"
            args.metrics_out = dest / "comparison_metrics.csv"
            args.confusion_out = dest / "attribution_confusion_matrix.csv"
        cases = load_probe_cases(args.cases)
        results = run_cases(
            cases,
            on_the_fly_baseline_rescore=args.on_the_fly_baseline_rescore,
        )
        write_attribution_table(results, args.out)
        write_comparison_metrics_table(results, args.metrics_out)
        write_confusion_matrix_table(results, args.confusion_out)
        print(
            f"wrote {len(results)} attribution row(s) to {Path(args.out)} "
            f"with comparison metrics to {Path(args.metrics_out)} "
            f"and confusion matrix to {Path(args.confusion_out)}"
        )
        return 0

    if args.command == "run-v1":
        if args.output:
            args.out_dir = args.output
        if args.real_data:
            results = run_full_real_suite(
                out_dir=args.out_dir,
                use_hook=args.use_hook,
                tie_margin=args.tie_margin,
                on_the_fly_baseline_rescore=args.on_the_fly_baseline_rescore,
            )
        elif args.cases:
            cases = load_probe_cases_v1(args.cases)
            if args.use_hook:
                results = run_cases_v1_with_hook(
                    cases,
                    tie_margin=args.tie_margin,
                    on_the_fly_baseline_rescore=args.on_the_fly_baseline_rescore,
                )
            else:
                results = run_cases_v1(
                    cases,
                    tie_margin=args.tie_margin,
                    on_the_fly_baseline_rescore=args.on_the_fly_baseline_rescore,
                )
            dest = Path(args.out_dir)
            dest.mkdir(parents=True, exist_ok=True)
            write_attribution_table(results, dest / "attribution_table.csv")
            write_comparison_metrics_table(results, dest / "comparison_metrics.csv")
            write_confusion_matrix_table(
                results, dest / "attribution_confusion_matrix.csv"
            )
            print(f"wrote {len(results)} V1 attribution rows to {dest}/")
        else:
            parser.error("run-v1 requires --cases or --real-data")
        return 0

    parser.error(f"unknown command {args.command!r}")
    return 2
