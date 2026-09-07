"""Build paper-facing tables from the completed CMD v0.3 development runs."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import fmean
import sys
from typing import Any, Iterable, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cmd_audit.spec_v03.order_only import compile_case_order_metadata
from experiments.spec_v03_analyze_current import (
    ROUTER_ARMS,
    aba_summary,
    allarms_summary,
    arm_rows,
    completion,
    event_index,
    identity,
    load_json,
    mean,
    rounded,
    safe_success,
    stage5,
    transfer_summary,
)
from experiments.spec_v03_family_bootstrap import bootstrap_family_means


SOURCES = ("halumem", "memfail", "memtracebench")
ABA_PHASES = (
    "recurring_a_stationary",
    "recurring_b_abrupt",
    "recurring_a_return_stationary",
)
OPERATOR_TO_INCIDENT = {
    "process_restore": "process_fault",
    "state_supersede": "state_drift",
    "poison_quarantine": "poison",
}


def inference(interval: Sequence[float]) -> str:
    low, high = interval
    return "POSITIVE" if low > 0 else "NEGATIVE" if high < 0 else "MIXED_OR_NULL"


def stream_name(path: Path) -> str:
    return path.parent.name


def source_name(stream: str) -> str:
    for source in SOURCES:
        if stream.startswith(source):
            return source
    raise ValueError(f"cannot infer public source from stream: {stream}")


def case_families(root: Path, group_a_root: Path, limit: int) -> dict[str, str]:
    result = {}
    for source in SOURCES:
        manifest = root / "data" / f"{source}_stationary" / "pilot_manifest.json"
        seed = int(load_json(manifest)["seed"]) if manifest.is_file() else 20260827
        for row in compile_case_order_metadata(
            source,
            group_a_root=group_a_root,
            limit=limit,
            case_seed=seed,
        ):
            if row.case_id in result and result[row.case_id] != row.family_id:
                raise ValueError(f"case identity collides across families: {row.case_id}")
            result[row.case_id] = row.family_id
    return result


def paired_deltas(
    left: Sequence[Mapping[str, Any]], right: Sequence[Mapping[str, Any]]
) -> list[tuple[str, float, Mapping[str, Any], Mapping[str, Any]]]:
    lhs, rhs = event_index(left), event_index(right)
    return [
        (str(key[0]), float(lhs[key]["utility"]) - float(rhs[key]["utility"]), lhs[key], rhs[key])
        for key in sorted(set(lhs) & set(rhs))
    ]


def blocked_result(
    rows: Iterable[tuple[str, float]],
    families: Mapping[str, str],
    *,
    iterations: int,
    seed: int,
) -> dict[str, Any]:
    grouped: dict[str, list[float]] = defaultdict(list)
    missing = Counter()
    for case_id, delta in rows:
        family = families.get(case_id)
        if family is None:
            missing[case_id] += 1
        else:
            grouped[family].append(delta)
    if not grouped:
        return {
            "family_count": 0,
            "paired_event_count": 0,
            "family_macro_mean": None,
            "ci95": None,
            "positive_family_rate": None,
            "tie_family_rate": None,
            "inference": "INSUFFICIENT_DATA",
            "unmapped_case_count": len(missing),
        }
    result = bootstrap_family_means(grouped, iterations=iterations, seed=seed)
    result["family_macro_mean"] = rounded(float(result["family_macro_mean"]))
    result["ci95"] = [rounded(float(value)) for value in result["ci95"]]
    result["inference"] = inference(result["ci95"])
    result["unmapped_case_count"] = len(missing)
    return result


def allarms_pairwise(
    root: Path,
    families: Mapping[str, str],
    *,
    iterations: int,
    seed: int,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    for path in sorted((root / "matched_allarms").rglob("report.json")):
        report = load_json(path)
        model = str(identity(report, path)["model_id"])
        arms = arm_rows(report)
        if "mix_ghost" not in arms:
            continue
        for competitor in ROUTER_ARMS:
            if competitor == "mix_ghost" or competitor not in arms:
                continue
            grouped[(model, competitor)].extend(
                (case_id, delta)
                for case_id, delta, _, _ in paired_deltas(arms["mix_ghost"], arms[competitor])
            )
    output = []
    for offset, ((model, competitor), rows) in enumerate(sorted(grouped.items())):
        output.append(
            {
                "model_id": model,
                "comparison": f"mix_ghost>{competitor}",
                **blocked_result(rows, families, iterations=iterations, seed=seed + offset),
            }
        )
    return output


def aba_pairwise(
    root: Path,
    families: Mapping[str, str],
    *,
    iterations: int,
    seed: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for path in sorted((root / "aba" / "qwen3").rglob("report.json")):
        arms = arm_rows(load_json(path))
        if not {"mix_ghost", "ghost_hierarchy"} <= set(arms):
            continue
        for case_id, delta, left, _ in paired_deltas(arms["mix_ghost"], arms["ghost_hierarchy"]):
            grouped[str(left["regime"])].append((case_id, delta))
    return [
        {
            "phase": phase,
            "comparison": "mix_ghost>ghost_hierarchy",
            **blocked_result(grouped.get(phase, ()), families, iterations=iterations, seed=seed + offset),
        }
        for offset, phase in enumerate(ABA_PHASES)
    ]


def operator_strategy_rows(root: Path) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for path in sorted((root / "aba" / "qwen3").rglob("report.json")):
        for arm, receipts in arm_rows(load_json(path)).items():
            if arm not in {"mix_ghost", "ghost_hierarchy"}:
                continue
            for row in receipts:
                key = (
                    arm,
                    str(row.get("regime")),
                    str(row.get("operator_family")),
                    str(row.get("strategy_id")),
                )
                grouped[key].append(row)
    output = []
    for (arm, phase, operator, strategy), rows in sorted(grouped.items()):
        output.append(
            {
                "arm": arm,
                "phase": phase,
                "operator_family": operator,
                "strategy_id": strategy,
                "count": len(rows),
                "mean_utility": rounded(mean(float(row["utility"]) for row in rows)),
                "safe_repair_success_proxy": rounded(mean(float(safe_success(row)) for row in rows)),
                "mean_locality_cost": rounded(
                    mean(float(row["locality_cost"]) for row in rows if row.get("locality_cost") is not None)
                ),
            }
        )
    return output


def incident_metrics(root: Path) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for path in sorted((root / "matched_allarms").rglob("report.json")):
        report = load_json(path)
        model = str(identity(report, path)["model_id"])
        for row in arm_rows(report).get("mix_ghost", []):
            incident = OPERATOR_TO_INCIDENT.get(str(row.get("operator_family")), "unknown")
            grouped[(model, incident)].append(row)
    output = []
    for (model, incident), rows in sorted(grouped.items()):
        output.append(
            {
                "model_id": model,
                "incident_type": incident,
                "receipt_count": len(rows),
                "mean_utility": rounded(mean(float(row["utility"]) for row in rows)),
                "safe_repair_success_proxy": rounded(mean(float(safe_success(row)) for row in rows)),
                "rollback_rate": rounded(mean(float(bool(row.get("rolled_back"))) for row in rows)),
                "delayed_regression_rate": rounded(
                    mean(float(bool(row.get("delayed_regression"))) for row in rows)
                ),
                "mean_locality_cost": rounded(
                    mean(float(row["locality_cost"]) for row in rows if row.get("locality_cost") is not None)
                ),
                "mean_collateral_cost": rounded(
                    mean(float(row["collateral_cost"]) for row in rows if row.get("collateral_cost") is not None)
                ),
            }
        )
    return output


def report_case_ids(report: Mapping[str, Any]) -> set[str]:
    return {
        str(row["case_id"])
        for arm in stage5(report).get("arms", [])
        for row in arm.get("selection_records", [])
        if row.get("case_id") is not None
    }


def overlap_audit(root: Path) -> list[dict[str, Any]]:
    output = []
    for path in sorted((root / "matched_allarms").rglob("report.json")):
        report = load_json(path)
        stream = stream_name(path)
        source_report = root / "source" / stream / "report.json"
        if not source_report.is_file():
            continue
        target_ids = report_case_ids(report)
        source_ids = report_case_ids(load_json(source_report))
        overlap = source_ids & target_ids
        output.append(
            {
                "model_id": identity(report, path)["model_id"],
                "seed": identity(report, path)["seed"],
                "stream": stream,
                "source_case_count": len(source_ids),
                "target_case_count": len(target_ids),
                "overlap_case_count": len(overlap),
                "target_overlap_rate": rounded(len(overlap) / len(target_ids) if target_ids else None),
                "held_out_status": "HELD_OUT" if not overlap else "OVERLAP_PRESENT",
            }
        )
    return output


def resource_usage(root: Path) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for path in sorted((root / "matched_allarms").rglob("report.json")):
        report = load_json(path)
        grouped[str(identity(report, path)["model_id"])].append(stage5(report).get("resource_usage", {}))
    output = []
    for model, rows in sorted(grouped.items()):
        fields = sorted({field for row in rows for field in row if isinstance(row[field], (int, float))})
        output.append(
            {
                "model_id": model,
                "report_count": len(rows),
                "mean_per_report": {
                    field: rounded(mean(float(row[field]) for row in rows if field in row)) for field in fields
                },
                "total": {field: sum(float(row.get(field, 0)) for row in rows) for field in fields},
            }
        )
    return output


def cross_summary(root: Path) -> dict[str, Any]:
    base = root / "v2_cross_allarms"
    grouped: dict[tuple[str, str], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    paths = sorted(base.rglob("report.json")) if base.exists() else []
    for path in paths:
        report = load_json(path)
        grouped[(str(identity(report, path)["model_id"]), path.parent.parent.name)][path.parent.name] = report
    output = []
    for (model, stream), conditions in sorted(grouped.items()):
        for left_name, right_name in (
            ("matched", "reset"),
            ("global", "reset"),
            ("global_prefix", "global"),
            ("global_prefix", "matched"),
        ):
            if left_name not in conditions or right_name not in conditions:
                continue
            left, right = arm_rows(conditions[left_name]), arm_rows(conditions[right_name])
            for arm in ("ghost_hierarchy", "mix_ghost"):
                if arm not in left or arm not in right:
                    continue
                deltas = [delta for _, delta, _, _ in paired_deltas(left[arm], right[arm])]
                output.append(
                    {
                        "model_id": model,
                        "stream": stream,
                        "comparison": f"{left_name}>{right_name}",
                        "arm": arm,
                        "paired_events": len(deltas),
                        "mean_delta": rounded(mean(deltas)),
                    }
                )
    return {"reports_observed": len(paths), "reports_expected": 8, "comparisons": output}


def claim_ledger(result: Mapping[str, Any]) -> list[dict[str, str]]:
    completion_rows = result["completion"]
    overlap = result["source_target_overlap"]
    overlap_present = any(row["held_out_status"] == "OVERLAP_PRESENT" for row in overlap)
    aba_complete = result["aba"]["reports_observed"] == result["aba"]["reports_expected"]
    matched_effects = [
        row
        for row in result["transfer"]["family_blocked"]
        if row.get("comparison") == "matched>reset"
    ]
    matched_supported = len(matched_effects) >= 2 and all(
        row.get("inference") == "POSITIVE" for row in matched_effects
    )
    allarms_complete = all(
        row["reports_with_all_7_arms"] == 12 for row in result["allarms"]["models"]
    ) and len(result["allarms"]["models"]) == 2
    pairwise_positive = bool(result["allarms_family_bootstrap"]) and all(
        row.get("inference") == "POSITIVE" for row in result["allarms_family_bootstrap"]
    )
    return [
        {
            "claim": "Matched ecological posterior improves over reset across Qwen3 and Llama.",
            "status": "SUPPORTED_DEVELOPMENT" if matched_supported else "NOT_ESTABLISHED",
            "boundary": "Family-blocked confidence intervals are positive; inspect overlap audit before claiming held-out generalization.",
        },
        {
            "claim": "Mix GHOST is the strongest matched router among the seven tested arms.",
            "status": (
                "SUPPORTED_DEVELOPMENT"
                if allarms_complete and pairwise_positive
                else "SUPPORTED_DESCRIPTIVE"
                if allarms_complete
                else "NOT_ESTABLISHED"
            ),
            "boundary": "Use all-arms pairwise family bootstrap for uncertainty and do not claim per-stream dominance.",
        },
        {
            "claim": "Mix GHOST preserves ecological memory across A-B-A recurrence.",
            "status": "SUPPORTED_DESCRIPTIVE" if aba_complete else "PENDING",
            "boundary": "Phase-specific family bootstrap determines whether the return-phase advantage excludes zero.",
        },
        {
            "claim": "Transferred posterior generalizes to unseen families.",
            "status": (
                "NOT_ESTABLISHED"
                if not overlap or overlap_present
                else "AUDIT_PASSED"
            ),
            "boundary": "Family-blocked resampling does not repair source-target case overlap.",
        },
        {
            "claim": "CMD provides confirmatory safe memory repair.",
            "status": "NOT_ESTABLISHED",
            "boundary": "Current feedback is development-structural; sealed receipts and real false-commit evaluation are absent.",
        },
        {
            "claim": "CMD outperforms deployed memory systems.",
            "status": "NOT_ESTABLISHED",
            "boundary": "Requires controlled MemSkill, ERSkill, and Mem0 result tables.",
        },
        {
            "claim": "The conclusions extend to an external frontier model.",
            "status": "NOT_ESTABLISHED",
            "boundary": "GPT-4o key-condition evaluation is absent.",
        },
        {
            "claim": "The compact experiment matrix is complete.",
            "status": "COMPLETE" if completion_rows["cross_reports"]["observed"] == 8 else "PARTIAL",
            "boundary": "Cross is supplemental; ABA, Mix-only, all-arms, and family bootstrap are independently tracked.",
        },
    ]


def table(title: str, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> str:
    def cell(value: Any) -> str:
        if isinstance(value, Mapping):
            return ", ".join(f"{key}={item}" for key, item in value.items())
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return "" if value is None else str(value)

    lines = [f"## {title}", "| " + " | ".join(fields) + " |", "| " + " | ".join("---" for _ in fields) + " |"]
    lines.extend("| " + " | ".join(cell(row.get(field)) for field in fields) + " |" for row in rows)
    return "\n".join(lines)


def markdown(result: Mapping[str, Any]) -> str:
    allarms_rows = [
        {"model_id": model["model_id"], **row}
        for model in result["allarms"]["models"]
        for row in model["ranking"]
    ]
    sections = [
        "# CMD v0.3 Development Experiment Report",
        "DEVELOPMENT_STRUCTURAL_ONLY: safety values are structural proxies, not sealed confirmatory truth.",
        table("All-Arms Ranking", allarms_rows, ("model_id", "arm", "stream_macro_utility", "event_weighted_utility", "mean_pseudo_regret", "mean_locality_cost")),
        table("All-Arms Family-Blocked Pairwise Effects", result["allarms_family_bootstrap"], ("model_id", "comparison", "family_macro_mean", "ci95", "positive_family_rate", "inference")),
        table("ABA Family-Blocked Effects", result["aba_family_bootstrap"], ("phase", "comparison", "family_macro_mean", "ci95", "positive_family_rate", "inference")),
        table("ABA Operator Strategies", result["aba_operator_strategy"], ("arm", "phase", "operator_family", "strategy_id", "count", "mean_utility", "mean_locality_cost")),
        table("Incident Metrics", result["incident_metrics"], ("model_id", "incident_type", "receipt_count", "mean_utility", "safe_repair_success_proxy", "rollback_rate", "mean_locality_cost")),
        table("Source-Target Overlap Audit", result["source_target_overlap"], ("model_id", "seed", "stream", "source_case_count", "target_case_count", "overlap_case_count", "target_overlap_rate", "held_out_status")),
        table("Claim Ledger", result["claim_ledger"], ("claim", "status", "boundary")),
    ]
    return "\n\n".join(sections) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--group-a-root", type=Path, default=Path("data/external/group_a"))
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260901)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()

    root = args.run_root.resolve()
    families = case_families(root, args.group_a_root.resolve(), args.limit)
    result: dict[str, Any] = {
        "schema_version": "cmd-spec-v03-paper-analysis-v1",
        "claim_boundary": "DEVELOPMENT_STRUCTURAL_ONLY",
        "run_root": str(root),
        "completion": completion(root),
        "transfer": transfer_summary(root),
        "allarms": allarms_summary(root),
        "aba": aba_summary(root),
        "allarms_family_bootstrap": allarms_pairwise(
            root, families, iterations=args.iterations, seed=args.bootstrap_seed
        ),
        "aba_family_bootstrap": aba_pairwise(
            root, families, iterations=args.iterations, seed=args.bootstrap_seed + 100
        ),
        "aba_operator_strategy": operator_strategy_rows(root),
        "incident_metrics": incident_metrics(root),
        "source_target_overlap": overlap_audit(root),
        "resource_usage": resource_usage(root),
        "cross": cross_summary(root),
    }
    result["claim_ledger"] = claim_ledger(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.write_text(markdown(result), encoding="utf-8")
    print(f"[RESULT] output={args.output}")
    print(f"[RESULT] markdown={args.markdown_output}")
    print(f"[RESULT] allarms_effects={len(result['allarms_family_bootstrap'])}")
    print(f"[RESULT] aba_effects={len(result['aba_family_bootstrap'])}")
    print(f"[RESULT] overlap_rows={len(result['source_target_overlap'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
