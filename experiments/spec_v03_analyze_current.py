"""Summarize the current CMD v0.3 development experiment bundle.

This is a read-only convenience analysis. It intentionally reports structural
development proxies separately from confirmatory safety evidence.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence


ROUTER_ARMS = (
    "random_legal",
    "best_global",
    "global_thompson",
    "niche_thompson",
    "contextual_bandit",
    "ghost_hierarchy",
    "mix_ghost",
)
TRANSFER_COMPARISONS = (
    "matched>reset",
    "global>reset",
    "global_prefix>global",
    "global_prefix>matched",
)
ABA_PHASES = (
    "recurring_a_stationary",
    "recurring_b_abrupt",
    "recurring_a_return_stationary",
)


def mean(values: Iterable[float]) -> float | None:
    rows = list(values)
    return fmean(rows) if rows else None


def rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def load_json(path: Path) -> Mapping[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"JSON root must be an object: {path}")
    return raw


def stage5(report: Mapping[str, Any]) -> Mapping[str, Any]:
    if report.get("schema_version") == "cmd-spec-v03-stage59-runner-v1":
        return report["results"]["stage5"]
    return report


def identity(report: Mapping[str, Any], path: Path) -> dict[str, Any]:
    config = report.get("config", {})
    return {
        "model_id": config.get("model_id", "unknown"),
        "seed": config.get("seed"),
        "run_id": config.get("run_id", path.parent.name),
    }


def receipt_rows(arm: Mapping[str, Any]) -> list[dict[str, Any]]:
    selections = {
        row["selection_id"]: row
        for row in arm.get("selection_records", [])
        if row.get("selection_id") is not None
    }
    rows = []
    for receipt in arm.get("receipt_records", []):
        selection = selections.get(receipt.get("selection_id"), {})
        rows.append(
            {
                **receipt,
                "case_id": selection.get("case_id"),
                "selection_mode": selection.get("selection_mode"),
            }
        )
    return rows


def arm_rows(report: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result = {}
    for arm in stage5(report).get("arms", []):
        if arm.get("status") == "COMPLETE":
            result[str(arm["arm"])] = receipt_rows(arm)
    return result


def event_index(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[Any, Any], Mapping[str, Any]]:
    return {
        (row.get("case_id"), row.get("selected_at_event_index")): row
        for row in rows
        if row.get("case_id") is not None
    }


def safe_success(row: Mapping[str, Any]) -> bool:
    return bool(
        row.get("valid") is True
        and row.get("rolled_back") is False
        and row.get("delayed_regression") is False
        and row.get("safety_passed") is True
        and row.get("invariant_passed") is True
    )


def completion(root: Path) -> dict[str, Any]:
    def count(path: Path) -> int:
        return sum(1 for _ in path.rglob("report.json")) if path.exists() else 0

    family_path = root / "analysis" / "family-bootstrap.json"
    family_complete = False
    if family_path.is_file():
        try:
            family_complete = bool(load_json(family_path).get("effects"))
        except (ValueError, json.JSONDecodeError):
            pass
    return {
        "mix_only_reports": {"observed": count(root / "v2_multiseed_mix_only"), "expected": 144},
        "matched_allarms_qwen3": {"observed": count(root / "matched_allarms" / "qwen3"), "expected": 12},
        "matched_allarms_llama31": {"observed": count(root / "matched_allarms" / "llama31"), "expected": 12},
        "family_bootstrap": "COMPLETE" if family_complete else "PENDING",
        "aba_reports": {"observed": count(root / "aba" / "qwen3"), "expected": 4},
        "cross_reports": {"observed": count(root / "v2_cross_allarms"), "expected": 8},
    }


def transfer_summary(root: Path) -> dict[str, Any]:
    aggregate_path = root / "analysis" / "mix-only-aggregate.json"
    bootstrap_path = root / "analysis" / "family-bootstrap.json"
    result: dict[str, Any] = {"event_weighted": [], "family_blocked": [], "model_contrasts": []}

    if aggregate_path.is_file():
        raw = load_json(aggregate_path)
        grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
        for row in raw.get("machine", {}).get("transfer_comparisons", []):
            if row.get("arm") == "mix_ghost":
                grouped[(str(row["identity"]["model_id"]), str(row["comparison"]))].append(row)
        for (model, comparison), rows in sorted(grouped.items()):
            deltas = [float(row["mean_delta"]) for row in rows]
            weights = [int(row["matched_event_count"]) for row in rows]
            denominator = sum(weights)
            result["event_weighted"].append(
                {
                    "model_id": model,
                    "comparison": comparison,
                    "streams": len(rows),
                    "positive": sum(value > 0 for value in deltas),
                    "ties": sum(value == 0 for value in deltas),
                    "negative": sum(value < 0 for value in deltas),
                    "stream_macro_mean": rounded(mean(deltas)),
                    "event_weighted_mean": rounded(
                        sum(value * weight for value, weight in zip(deltas, weights)) / denominator
                        if denominator
                        else None
                    ),
                }
            )

    if bootstrap_path.is_file():
        raw = load_json(bootstrap_path)
        for field, output in (("effects", "family_blocked"), ("model_contrasts", "model_contrasts")):
            for row in raw.get(field, []):
                low, high = (float(value) for value in row["ci95"])
                result[output].append(
                    {
                        **row,
                        "family_macro_mean": rounded(float(row["family_macro_mean"])),
                        "ci95": [rounded(low), rounded(high)],
                        "inference": "POSITIVE" if low > 0 else "NEGATIVE" if high < 0 else "MIXED_OR_NULL",
                    }
                )
    return result


def allarms_summary(root: Path) -> dict[str, Any]:
    base = root / "matched_allarms"
    reports_by_model: dict[str, list[tuple[Path, Mapping[str, Any]]]] = defaultdict(list)
    for path in sorted(base.rglob("report.json")) if base.exists() else ():
        report = load_json(path)
        reports_by_model[str(identity(report, path)["model_id"])].append((path, report))

    output = []
    for model, reports in sorted(reports_by_model.items()):
        per_arm: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "report_means": [],
                "utilities": [],
                "safe": [],
                "locality": [],
                "collateral": [],
                "regrets": [],
                "wins": 0,
            }
        )
        complete_reports = 0
        for _, report in reports:
            arms = arm_rows(report)
            if set(ROUTER_ARMS) <= set(arms):
                complete_reports += 1
            means = {arm: mean(float(row["utility"]) for row in rows) for arm, rows in arms.items() if rows}
            if means:
                best = max(value for value in means.values() if value is not None)
                for arm, value in means.items():
                    if value == best:
                        per_arm[arm]["wins"] += 1

            indexes = {arm: event_index(rows) for arm, rows in arms.items()}
            event_keys = set().union(*(set(rows) for rows in indexes.values())) if indexes else set()
            event_best = {
                key: max(float(rows[key]["utility"]) for rows in indexes.values() if key in rows)
                for key in event_keys
            }
            for arm, rows in arms.items():
                values = [float(row["utility"]) for row in rows]
                if values:
                    per_arm[arm]["report_means"].append(fmean(values))
                per_arm[arm]["utilities"].extend(values)
                per_arm[arm]["safe"].extend(safe_success(row) for row in rows)
                per_arm[arm]["locality"].extend(
                    float(row["locality_cost"]) for row in rows if row.get("locality_cost") is not None
                )
                per_arm[arm]["collateral"].extend(
                    float(row["collateral_cost"]) for row in rows if row.get("collateral_cost") is not None
                )
                for key, row in indexes[arm].items():
                    per_arm[arm]["regrets"].append(event_best[key] - float(row["utility"]))

        ranking = []
        for arm, values in per_arm.items():
            ranking.append(
                {
                    "arm": arm,
                    "stream_macro_utility": rounded(mean(values["report_means"])),
                    "event_weighted_utility": rounded(mean(values["utilities"])),
                    "safe_repair_success_proxy": rounded(mean(float(value) for value in values["safe"])),
                    "mean_locality_cost": rounded(mean(values["locality"])),
                    "mean_collateral_cost": rounded(mean(values["collateral"])),
                    "mean_pseudo_regret": rounded(mean(values["regrets"])),
                    "stream_wins_including_ties": values["wins"],
                    "receipt_count": len(values["utilities"]),
                }
            )
        ranking.sort(
            key=lambda row: (
                -(row["stream_macro_utility"] if row["stream_macro_utility"] is not None else -2.0),
                row["arm"],
            )
        )

        mix_deltas = []
        for _, report in reports:
            arms = arm_rows(report)
            if "mix_ghost" not in arms or "ghost_hierarchy" not in arms:
                continue
            left, right = event_index(arms["mix_ghost"]), event_index(arms["ghost_hierarchy"])
            shared = set(left) & set(right)
            if shared:
                mix_deltas.append(mean(float(left[key]["utility"]) - float(right[key]["utility"]) for key in shared))
        output.append(
            {
                "model_id": model,
                "reports_observed": len(reports),
                "reports_with_all_7_arms": complete_reports,
                "ranking": ranking,
                "mix_minus_ghost": {
                    "stream_count": len(mix_deltas),
                    "positive": sum(value > 0 for value in mix_deltas if value is not None),
                    "ties": sum(value == 0 for value in mix_deltas if value is not None),
                    "negative": sum(value < 0 for value in mix_deltas if value is not None),
                    "stream_macro_delta": rounded(mean(value for value in mix_deltas if value is not None)),
                },
            }
        )
    return {"models": output}


def aba_summary(root: Path) -> dict[str, Any]:
    base = root / "aba" / "qwen3"
    paths = sorted(base.rglob("report.json")) if base.exists() else []
    phase_rows: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    paired: dict[str, list[float]] = defaultdict(list)
    strategy_counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)

    for path in paths:
        report = load_json(path)
        arms = arm_rows(report)
        for arm in ("ghost_hierarchy", "mix_ghost"):
            for row in arms.get(arm, []):
                phase = str(row.get("regime"))
                phase_rows[(arm, phase)].append(row)
                if row.get("strategy_id"):
                    strategy_counts[(arm, phase)][str(row["strategy_id"])] += 1
        if {"ghost_hierarchy", "mix_ghost"} <= set(arms):
            mix = event_index(arms["mix_ghost"])
            ghost = event_index(arms["ghost_hierarchy"])
            for key in set(mix) & set(ghost):
                phase = str(mix[key].get("regime"))
                paired[phase].append(float(mix[key]["utility"]) - float(ghost[key]["utility"]))

    phases = []
    for phase in ABA_PHASES:
        for arm in ("ghost_hierarchy", "mix_ghost"):
            rows = phase_rows.get((arm, phase), [])
            phases.append(
                {
                    "phase": phase,
                    "arm": arm,
                    "receipt_count": len(rows),
                    "mean_utility": rounded(mean(float(row["utility"]) for row in rows)),
                    "safe_repair_success_proxy": rounded(mean(float(safe_success(row)) for row in rows)),
                    "strategy_counts": dict(strategy_counts[(arm, phase)]),
                }
            )

    return_gaps = []
    for arm in ("ghost_hierarchy", "mix_ghost"):
        a1 = mean(float(row["utility"]) for row in phase_rows.get((arm, ABA_PHASES[0]), []))
        a2 = mean(float(row["utility"]) for row in phase_rows.get((arm, ABA_PHASES[2]), []))
        return_gaps.append(
            {
                "arm": arm,
                "a1_mean": rounded(a1),
                "a2_return_mean": rounded(a2),
                "a2_minus_a1": rounded(None if a1 is None or a2 is None else a2 - a1),
            }
        )
    return {
        "reports_observed": len(paths),
        "reports_expected": 4,
        "phase_metrics": phases,
        "mix_minus_ghost_by_phase": [
            {
                "phase": phase,
                "paired_events": len(paired.get(phase, [])),
                "mean_delta": rounded(mean(paired.get(phase, []))),
            }
            for phase in ABA_PHASES
        ],
        "return_to_a": return_gaps,
    }


def conclusions(summary: Mapping[str, Any]) -> list[str]:
    rows = []
    completion_rows = summary["completion"]
    if completion_rows["mix_only_reports"]["observed"] == 144:
        rows.append("The 144-report Mix-only transfer matrix is complete.")
    for effect in summary["transfer"]["family_blocked"]:
        if effect.get("comparison") == "matched>reset":
            rows.append(
                f"{effect['model_id']}: matched>reset family-blocked effect is "
                f"{effect['family_macro_mean']} with 95% CI {effect['ci95']} ({effect['inference']})."
            )
    for model in summary["allarms"]["models"]:
        if model["ranking"]:
            rows.append(
                f"{model['model_id']}: top matched all-arms router by stream-macro utility is "
                f"{model['ranking'][0]['arm']} ({model['ranking'][0]['stream_macro_utility']})."
            )
    if summary["aba"]["reports_observed"] < summary["aba"]["reports_expected"]:
        rows.append("ABA recurrence evidence is pending and must not yet be claimed.")
    rows.append(
        "All current safety rates use development-structural feedback and are structural proxies, not sealed confirmatory safety truth."
    )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.run_root.resolve()
    summary = {
        "schema_version": "cmd-spec-v03-current-analysis-v1",
        "run_root": str(root),
        "claim_boundary": "DEVELOPMENT_STRUCTURAL_ONLY",
        "completion": completion(root),
        "transfer": transfer_summary(root),
        "allarms": allarms_summary(root),
        "aba": aba_summary(root),
    }
    summary["conclusions"] = conclusions(summary)
    rendered = json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"[RESULT] output={args.output}")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
