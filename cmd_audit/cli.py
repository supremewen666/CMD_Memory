"""Command-line interface for the standalone CMD-Audit harness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .harness import (
    run_cases,
    run_real_suite,
    write_attribution_table,
    write_comparison_metrics_table,
)
from .data_io import load_probe_cases, load_probe_cases_v1
from .data_io import validate_group_a_catalog, validate_group_b_catalog


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
        help="output directory for attribution and operator recovery metrics",
    )
    run_parser.add_argument(
        "--metrics-out",
        default="artifacts/comparison_metrics.csv",
        help="CSV path for CMD operator recovery metrics",
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
        help="deprecated no-op; live V1 runtime always enters through the hook",
    )
    v1_parser.add_argument(
        "--no-prefilter",
        dest="use_hook",
        action="store_false",
        help="deprecated no-op alias for --no-hook",
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

    # ── Memory-directory repair ─────────────────────────────────────────

    repair_parser = subparsers.add_parser(
        "repair-store",
        help="inspect or repair a one-fact-per-Markdown memory directory",
    )
    repair_parser.add_argument("memory_dir", help="memory directory to inspect")
    repair_parser.add_argument(
        "--mode",
        choices=("dry-run", "apply"),
        default="dry-run",
        help="dry-run writes a report; apply snapshots, gates, and may demote stale files",
    )
    repair_parser.add_argument("--max-bucket-size", type=int, default=5)
    repair_parser.add_argument("--similarity-threshold", type=float, default=0.35)
    repair_parser.add_argument("--timestamp-tolerance-days", type=int, default=7)

    group_b_parser = subparsers.add_parser(
        "validate-group-b-data",
        help="validate Group B external-data manifests and acquired payload hashes",
    )
    group_b_parser.add_argument(
        "--root",
        default="data/external/group_b",
        help="Group B inventory root (default: data/external/group_b)",
    )

    group_a_parser = subparsers.add_parser(
        "validate-group-a-data",
        help="validate sealed Group A external-data sources, hashes, and schemas",
    )
    group_a_parser.add_argument(
        "--root",
        default="data/external/group_a",
        help="Group A root (default: data/external/group_a)",
    )

    # ── Parse and dispatch ──────────────────────────────────────────────

    args = parser.parse_args(argv)

    if args.command == "run":
        if args.output:
            dest = Path(args.output)
            args.out = dest / "attribution_table.csv"
            args.metrics_out = dest / "comparison_metrics.csv"
        cases = load_probe_cases(args.cases)
        results = run_cases(
            cases,
            on_the_fly_baseline_rescore=args.on_the_fly_baseline_rescore,
        )
        write_attribution_table(results, args.out)
        write_comparison_metrics_table(results, args.metrics_out)
        print(
            f"wrote {len(results)} attribution row(s) to {Path(args.out)} "
            f"with operator recovery metrics to {Path(args.metrics_out)}"
        )
        return 0

    if args.command == "run-v1":
        if args.output:
            args.out_dir = args.output
        if args.real_data:
            results = run_real_suite(
                out_dir=args.out_dir,
                use_hook=args.use_hook,
                tie_margin=args.tie_margin,
                on_the_fly_baseline_rescore=args.on_the_fly_baseline_rescore,
            )
        elif args.cases:
            cases = load_probe_cases_v1(args.cases)
            if args.use_hook:
                results = run_cases(
                    cases,
                    hook=True,
                    tie_margin=args.tie_margin,
                    on_the_fly_baseline_rescore=args.on_the_fly_baseline_rescore,
                )
            else:
                results = run_cases(
                    cases,
                    tie_margin=args.tie_margin,
                    on_the_fly_baseline_rescore=args.on_the_fly_baseline_rescore,
                )
            dest = Path(args.out_dir)
            dest.mkdir(parents=True, exist_ok=True)
            write_attribution_table(results, dest / "attribution_table.csv")
            write_comparison_metrics_table(results, dest / "comparison_metrics.csv")
            print(f"wrote {len(results)} V1 attribution rows to {dest}/")
        else:
            parser.error("run-v1 requires --cases or --real-data")
        return 0

    if args.command == "repair-store":
        from .repair.store_repair import execute_store_repair

        result = execute_store_repair(
            args.memory_dir,
            mode=args.mode,
            max_bucket_size=args.max_bucket_size,
            similarity_threshold=args.similarity_threshold,
            tolerance_days=args.timestamp_tolerance_days,
        )
        print(
            f"repair-store mode={result.mode} applied={str(result.applied).lower()} "
            f"gate={result.gate} report={result.report_path}"
        )
        return 0

    if args.command == "validate-group-b-data":
        report = validate_group_b_catalog(args.root)
        print(json.dumps(report.as_dict(), sort_keys=True))
        return 0 if report.valid else 1

    if args.command == "validate-group-a-data":
        report = validate_group_a_catalog(args.root)
        print(json.dumps(report.as_dict(), sort_keys=True))
        return 0 if report.valid else 1

    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
