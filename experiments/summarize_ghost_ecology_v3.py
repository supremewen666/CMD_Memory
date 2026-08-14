#!/usr/bin/env python3
"""Hash-audit and summarize completed GHOST Ecology V3 zero-call reports."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from statistics import fmean
from typing import Sequence

from cmd_audit.repair.evolution_repository import content_sha256


def summarize(report_paths: Sequence[Path]) -> dict[str, object]:
    if not report_paths:
        raise ValueError("at least one report is required")
    reports: list[tuple[Path, dict[str, object]]] = []
    for path in report_paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        claimed = report.get("report_sha256")
        payload = {key: value for key, value in report.items() if key != "report_sha256"}
        if claimed != content_sha256(payload):
            raise ValueError(f"report hash mismatch: {path}")
        if report.get("model_calls") != 0 or report.get("arm_count") != 8:
            raise ValueError(f"report protocol mismatch: {path}")
        gate = report.get("ghost_cal_gate")
        if not isinstance(gate, dict) or gate.get("updates_allowed") is not False:
            raise ValueError(f"calibration firewall mismatch: {path}")
        reports.append((path, report))

    rows = []
    for path, report in reports:
        comparisons = report["ghost_cal_gate"]["comparisons"]
        rows.append(
            {
                "seed": report["gate"]["bootstrap_seed"],
                "report": str(path),
                "report_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "report_sha256": report["report_sha256"],
                "same_feedback_estimate": comparisons["full_v4_observable"]["estimate"],
                "same_feedback_lower_95_one_sided": comparisons["full_v4_observable"]["lower_95_one_sided"],
                "oracle_estimate": comparisons["full_v4"]["estimate"],
                "oracle_lower_95_one_sided": comparisons["full_v4"]["lower_95_one_sided"],
                "ghost_mean_utility": report["arm_summaries"]["ghost_hierarchy_v1"]["mean_utility"],
                "same_feedback_mean_utility": report["arm_summaries"]["full_v4_observable"]["mean_utility"],
                "oracle_mean_utility": report["arm_summaries"]["full_v4"]["mean_utility"],
                "nonzero_residual_count": report["ghost_residual_diagnostics"]["nonzero_count"],
            }
        )
    rows.sort(key=lambda row: int(row["seed"]))
    payload: dict[str, object] = {
        "schema_version": "cmd-ghost-ecology-v3-multiseed-summary-v1",
        "decision": "PASS" if all(row["same_feedback_lower_95_one_sided"] > 0.0 for row in rows) else "BLOCKED",
        "claim_scope": "development_proxy_zero_call_not_sealed_or_live",
        "model_calls": 0,
        "case_count_per_seed": 3100,
        "arm_count": 8,
        "rows_per_seed": 24800,
        "seeds": [row["seed"] for row in rows],
        "same_feedback_primary": "full_v4_observable",
        "oracle_context_only": "full_v4",
        "mean_same_feedback_estimate": fmean(float(row["same_feedback_estimate"]) for row in rows),
        "worst_same_feedback_lower_95_one_sided": min(float(row["same_feedback_lower_95_one_sided"]) for row in rows),
        "mean_oracle_estimate": fmean(float(row["oracle_estimate"]) for row in rows),
        "worst_oracle_lower_95_one_sided": min(float(row["oracle_lower_95_one_sided"]) for row in rows),
        "reports": rows,
    }
    return {**payload, "summary_sha256": content_sha256(payload)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError("refusing to overwrite multiseed summary")
    summary = summarize(args.report)
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["decision"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
