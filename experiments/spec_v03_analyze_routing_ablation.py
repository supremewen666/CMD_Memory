#!/usr/bin/env python3
"""Analyze the pre-registered Mix GHOST routing mechanism ablation."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import mean
from typing import Iterable, Mapping, Sequence


ARMS = (
    "routing_frozen_backbone",
    "routing_global",
    "routing_global_pattern",
    "routing_global_pattern_local",
    "routing_full_no_support_gate",
    "mix_ghost",
)
COMPARISONS = (
    ("routing_global", "routing_frozen_backbone", "global_residual"),
    ("routing_global_pattern", "routing_global", "pattern_residual"),
    ("routing_global_pattern_local", "routing_global_pattern", "local_residual"),
    ("mix_ghost", "routing_full_no_support_gate", "support_gate"),
    ("mix_ghost", "routing_frozen_backbone", "full_router"),
)


def _arm_events(arm: Mapping[str, object]) -> dict[tuple[str, int], dict[str, object]]:
    selections = {
        str(row["selection_id"]): row
        for row in arm["selection_records"]  # type: ignore[index]
        if row.get("selection_id") is not None  # type: ignore[union-attr]
    }
    events: dict[tuple[str, int], dict[str, object]] = {}
    for receipt in arm["receipt_records"]:  # type: ignore[index]
        selection = selections[str(receipt["selection_id"])]
        key = (str(selection["case_id"]), int(selection["selected_at_event_index"]))
        if key in events:
            raise ValueError("routing ablation repeats a settled case/event")
        selected = selection.get("selected_skill_revision_id")
        safety = receipt.get("safety_passed")
        invariant = receipt.get("invariant_passed")
        safe_success = (
            bool(receipt.get("valid", False))
            and not bool(receipt.get("rolled_back", False))
            and not bool(receipt.get("delayed_regression", False))
            and safety is not False
            and invariant is not False
        )
        events[key] = {
            "utility": float(receipt["utility"]),
            "selected_skill_revision_id": selected,
            "safe_repair_success_proxy": safe_success,
            "locality_cost": receipt.get("locality_cost"),
            "collateral_cost": receipt.get("collateral_cost"),
        }
    return events


def _optional_mean(values: Iterable[object]) -> float | None:
    parsed = [float(value) for value in values if value is not None]
    return None if not parsed else mean(parsed)


def analyze_report(raw: Mapping[str, object], *, source: str) -> dict[str, object] | None:
    stage5 = raw.get("results", {}).get("stage5")  # type: ignore[union-attr]
    if not isinstance(stage5, Mapping) or not isinstance(stage5.get("arms"), Sequence):
        return None
    arm_index = {
        str(arm["arm"]): arm
        for arm in stage5["arms"]  # type: ignore[index]
        if isinstance(arm, Mapping) and arm.get("status") == "COMPLETE"
    }
    if not set(ARMS) <= set(arm_index):
        return None
    events = {arm: _arm_events(arm_index[arm]) for arm in ARMS}
    paired_keys = set.intersection(*(set(rows) for rows in events.values()))
    if not paired_keys:
        raise ValueError(f"routing ablation has no paired settled events: {source}")
    baseline = events["routing_frozen_backbone"]
    best_by_event = {
        key: max(events[arm][key]["utility"] for arm in ARMS)
        for key in paired_keys
    }
    metrics: dict[str, dict[str, object]] = {}
    for arm in ARMS:
        keyed_rows = [(key, events[arm][key]) for key in paired_keys]
        rows = [row for _key, row in keyed_rows]
        overrides = [
            row
            for key, row in keyed_rows
            if row["selected_skill_revision_id"]
            != baseline[key]["selected_skill_revision_id"]
        ]
        harmful = [
            row
            for key, row in keyed_rows
            if (
                row["selected_skill_revision_id"]
                != baseline[key]["selected_skill_revision_id"]
                and row["utility"] < baseline[key]["utility"]
            )
        ]
        metrics[arm] = {
            "paired_events": len(rows),
            "mean_utility": mean(float(row["utility"]) for row in rows),
            "mean_empirical_regret": mean(
                float(best_by_event[key]) - float(events[arm][key]["utility"])
                for key in paired_keys
            ),
            "safe_repair_success_proxy": mean(
                float(bool(row["safe_repair_success_proxy"])) for row in rows
            ),
            "override_rate": len(overrides) / len(rows),
            "negative_override_rate": (
                None if not overrides else len(harmful) / len(overrides)
            ),
            "mean_locality_cost": _optional_mean(row["locality_cost"] for row in rows),
            "mean_collateral_cost": _optional_mean(row["collateral_cost"] for row in rows),
        }
    comparisons = []
    for left, right, label in COMPARISONS:
        deltas = [
            float(events[left][key]["utility"]) - float(events[right][key]["utility"])
            for key in paired_keys
        ]
        comparisons.append({
            "comparison": f"{left}>{right}",
            "mechanism": label,
            "paired_events": len(deltas),
            "mean_delta": mean(deltas),
            "positive_events": sum(value > 0 for value in deltas),
            "ties": sum(value == 0 for value in deltas),
            "negative_events": sum(value < 0 for value in deltas),
        })
    config = raw.get("config", {})
    return {
        "source": source,
        "run_id": config.get("run_id") if isinstance(config, Mapping) else None,
        "model_id": config.get("model_id") if isinstance(config, Mapping) else None,
        "paired_events": len(paired_keys),
        "arm_metrics": metrics,
        "comparisons": comparisons,
    }


def analyze_paths(paths: Iterable[Path]) -> dict[str, object]:
    reports = []
    for path in sorted(set(paths)):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            continue
        report = analyze_report(raw, source=str(path))
        if report is not None:
            reports.append(report)
    if not reports:
        raise ValueError("no complete six-arm routing ablation reports found")
    by_model: dict[str, list[dict[str, object]]] = defaultdict(list)
    for report in reports:
        by_model[str(report["model_id"])].append(report)
    model_summaries = []
    for model_id, rows in sorted(by_model.items()):
        total_events = sum(int(row["paired_events"]) for row in rows)
        arm_metrics = {}
        for arm in ARMS:
            arm_metrics[arm] = {
                metric: sum(
                    float(row["arm_metrics"][arm][metric]) * int(row["paired_events"])  # type: ignore[index]
                    for row in rows
                    if row["arm_metrics"][arm][metric] is not None  # type: ignore[index]
                ) / sum(
                    int(row["paired_events"])
                    for row in rows
                    if row["arm_metrics"][arm][metric] is not None  # type: ignore[index]
                )
                for metric in (
                    "mean_utility",
                    "mean_empirical_regret",
                    "safe_repair_success_proxy",
                    "override_rate",
                    "negative_override_rate",
                    "mean_locality_cost",
                    "mean_collateral_cost",
                )
                if any(row["arm_metrics"][arm][metric] is not None for row in rows)  # type: ignore[index]
            }
        comparisons = []
        labels = [row["comparison"] for row in rows[0]["comparisons"]]  # type: ignore[index]
        for label in labels:
            matches = [
                item
                for row in rows
                for item in row["comparisons"]  # type: ignore[index]
                if item["comparison"] == label
            ]
            comparisons.append({
                "comparison": label,
                "mechanism": matches[0]["mechanism"],
                "reports": len(matches),
                "paired_events": sum(int(item["paired_events"]) for item in matches),
                "stream_macro_mean_delta": mean(float(item["mean_delta"]) for item in matches),
                "event_weighted_mean_delta": sum(
                    float(item["mean_delta"]) * int(item["paired_events"])
                    for item in matches
                ) / sum(int(item["paired_events"]) for item in matches),
            })
        model_summaries.append({
            "model_id": model_id,
            "reports": len(rows),
            "paired_events": total_events,
            "arm_metrics": arm_metrics,
            "comparisons": comparisons,
        })
    return {
        "schema_version": "cmd-spec-v03-routing-ablation-analysis-v1",
        "reports_observed": len(reports),
        "arms": list(ARMS),
        "model_summaries": model_summaries,
        "reports": reports,
        "scope_note": (
            "Empirical regret is relative to the best realized arm on each paired event; "
            "safety is a structural proxy unless reports use a sealed feedback provider."
        ),
    }


def _markdown(result: Mapping[str, object]) -> str:
    lines = ["# Routing Mechanism Ablation", ""]
    lines.append(str(result["scope_note"]))
    for model in result["model_summaries"]:  # type: ignore[index]
        lines.extend(["", f"## {model['model_id']}", "", "| Arm | Utility | Regret | Override | Negative override |", "|---|---:|---:|---:|---:|"])
        for arm in ARMS:
            metrics = model["arm_metrics"][arm]
            negative = metrics.get("negative_override_rate")
            lines.append(
                f"| {arm} | {metrics['mean_utility']:.6f} | "
                f"{metrics['mean_empirical_regret']:.6f} | {metrics['override_rate']:.4f} | "
                f"{'NA' if negative is None else f'{negative:.4f}'} |"
            )
        lines.extend(["", "| Mechanism | Event-weighted utility delta |", "|---|---:|"])
        for comparison in model["comparisons"]:
            lines.append(
                f"| {comparison['mechanism']} | {comparison['event_weighted_mean_delta']:.6f} |"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    result = analyze_paths(args.input_root.rglob("report.json"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.markdown_output is not None:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(_markdown(result), encoding="utf-8")
    print(f"[RESULT] reports={result['reports_observed']}")
    print(f"[RESULT] output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
