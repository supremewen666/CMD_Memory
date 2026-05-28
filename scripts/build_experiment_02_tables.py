#!/usr/bin/env python3
"""Build Decision 34 Experiment 2 tables from completed offline artifacts.

Inputs are issue 0023's at-scale retest CSV and issue 0024's researcher subset
JSON. This script does not run agents, scorers, hooks, RPE training, or LLMs.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cmd_audit.core.labels import REPLAY_TO_LABEL
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.metrics import DiagnosisPrediction, compute_diagnosis_metrics
from cmd_audit.core.models import ProbeCase, load_real_cases_by_source
from baselines import run_baseline_suite


HEADLINE_LABELS = (
    "write_error",
    "compression_error",
    "premature_extraction_error",
    "retrieval_error",
    "injection_error",
    "reasoning_error",
    "route_error",
    "ingestion_error",
)

# DiagnosisMetrics still requires a numeric cost field. Decision 34 cost
# reporting must use CostLatency rows below; this sentinel is never emitted.
INTERNAL_METRIC_COST_NOT_REPORTED = 0.0


@dataclass(frozen=True)
class ResearcherCase:
    case_id: str
    gold_label: str
    confidence: str
    source: str


@dataclass(frozen=True)
class CmdPrediction:
    case_id: str
    source: str
    gold_label: str
    confidence: str
    predicted_label: str | None
    top2_labels: tuple[str, ...]
    attribution_failed: bool
    failure_reason: str
    top_replay: str
    top_gain: float
    cost: "CostLatency"


@dataclass(frozen=True)
class CostLatency:
    agent_tokens: float | None = None
    scorer_tokens: float | None = None
    verifier_tokens: float | None = None
    total_tokens: float | None = None
    wallclock_sec: float | None = None
    usd_cost: float | None = None

    @property
    def status(self) -> str:
        if any(
            value is not None
            for value in (
                self.agent_tokens,
                self.scorer_tokens,
                self.verifier_tokens,
                self.total_tokens,
                self.wallclock_sec,
                self.usd_cost,
            )
        ):
            return "measured"
        return "missing_cost_metadata"


def load_case_index(input_dir: str | Path) -> dict[str, ProbeCase]:
    index: dict[str, ProbeCase] = {}
    for cases in load_real_cases_by_source(input_dir).values():
        for case in cases:
            index[case.case_id] = case
    return index


def load_researcher_cases(path: str | Path) -> dict[str, ResearcherCase]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows: dict[str, ResearcherCase] = {}
    for row in payload.get("cases", []):
        label = row.get("researcher_label")
        confidence = row.get("confidence")
        if label is None:
            continue
        if confidence not in {"high", "medium", "low"}:
            raise ValueError(f"{row['case_id']}: invalid confidence {confidence!r}")
        rows[row["case_id"]] = ResearcherCase(
            case_id=row["case_id"],
            gold_label=label,
            confidence=confidence,
            source=row.get("source", ""),
        )
    if not rows:
        raise ValueError(
            "researcher subset has no labeled cases; either populate "
            "cases[*].researcher_label or rerun with --label-source original "
            "for an unadjudicated baseline sanity table"
        )
    return rows


def load_original_label_cases(
    *,
    retest_by_case: dict[str, list[dict[str, str]]],
    case_index: dict[str, ProbeCase],
    subset_path: str | Path | None = None,
) -> dict[str, ResearcherCase]:
    """Build unadjudicated cases using source perturbation labels.

    This is for scale-sanity/baseline debugging only. It deliberately reuses
    the same ResearcherCase shape so downstream table builders stay unchanged,
    but the labels are not researcher-adjudicated headline labels.
    """
    subset_rows: list[dict] = []
    if subset_path is not None:
        payload = json.loads(Path(subset_path).read_text(encoding="utf-8"))
        raw_rows = payload.get("cases", []) if isinstance(payload, dict) else payload
        if not isinstance(raw_rows, list):
            raise ValueError("researcher subset must contain an array or cases array")
        subset_rows = [row for row in raw_rows if isinstance(row, dict)]

    if subset_rows:
        case_ids = [str(row["case_id"]) for row in subset_rows if row.get("case_id")]
        source_by_case = {
            str(row["case_id"]): str(row.get("source", ""))
            for row in subset_rows
            if row.get("case_id")
        }
    else:
        case_ids = sorted(retest_by_case)
        source_by_case = {}

    rows: dict[str, ResearcherCase] = {}
    missing: list[str] = []
    for case_id in case_ids:
        case = case_index.get(case_id)
        if case is None or case.perturbation_label is None:
            missing.append(case_id)
            continue
        retest_source = (
            retest_by_case.get(case_id, [{}])[0].get("source", "")
            if retest_by_case.get(case_id)
            else ""
        )
        rows[case_id] = ResearcherCase(
            case_id=case_id,
            gold_label=case.perturbation_label,
            confidence="medium",
            source=source_by_case.get(case_id) or retest_source,
        )
    if missing:
        preview = ", ".join(missing[:10])
        raise ValueError(f"missing original labels for case ids: {preview}")
    if not rows:
        raise ValueError("no cases available for --label-source original")
    return rows


def load_retest_by_case(path: str | Path) -> dict[str, list[dict[str, str]]]:
    by_case: dict[str, list[dict[str, str]]] = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"case_id", "replay_name", "recovery_gain"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(
                "retest CSV missing required columns: " + ", ".join(sorted(missing))
            )
        for row in reader:
            by_case[row["case_id"]].append(row)
    return by_case


def build_cmd_predictions(
    researcher_cases: dict[str, ResearcherCase],
    retest_by_case: dict[str, list[dict[str, str]]],
    case_index: dict[str, ProbeCase],
) -> list[CmdPrediction]:
    predictions: list[CmdPrediction] = []
    missing: list[str] = []
    for case_id, researcher in researcher_cases.items():
        rows = retest_by_case.get(case_id)
        case = case_index.get(case_id)
        if not rows or case is None:
            missing.append(case_id)
            continue
        ranked = sorted(rows, key=lambda r: float(r["recovery_gain"]), reverse=True)
        top = ranked[0]
        top_gain = float(top["recovery_gain"])
        attribution_failed = top_gain <= 0.0
        predicted = None
        failure_reason = ""
        if attribution_failed:
            failure_reason = "zero_gain" if top_gain == 0.0 else "negative_gain"
        else:
            predicted = _label_for_replay(
                top["replay_name"],
                has_ingestion_trace=case.has_ingestion_trace,
            )
        top2 = tuple(
            _label_for_replay(row["replay_name"], has_ingestion_trace=case.has_ingestion_trace)
            for row in ranked[:2]
        )
        predictions.append(
            CmdPrediction(
                case_id=case_id,
                source=researcher.source or top.get("source", ""),
                gold_label=researcher.gold_label,
                confidence=researcher.confidence,
                predicted_label=predicted,
                top2_labels=top2,
                attribution_failed=attribution_failed,
                failure_reason=failure_reason,
                top_replay=top["replay_name"],
                top_gain=top_gain,
                cost=_cost_from_retest_rows(rows),
            )
        )
    if missing:
        preview = ", ".join(missing[:10])
        raise ValueError(f"missing retest/case rows for researcher cases: {preview}")
    return predictions


def compute_cmd_metrics(predictions: list[CmdPrediction]) -> dict[str, float]:
    metric_rows = cmd_diagnosis_predictions(predictions)
    metrics = compute_diagnosis_metrics(metric_rows, labels=HEADLINE_LABELS)["CMD-Audit"]
    attributed = sum(not row.attribution_failed for row in predictions)
    total = len(predictions)
    return {
        "n": float(total),
        "coverage": attributed / total if total else 0.0,
        "attribution_accuracy": metrics.attribution_accuracy,
        "macro_f1": metrics.macro_f1,
        "top2_accuracy": metrics.top2_accuracy,
        "attribution_failed": float(total - attributed),
    }


def cmd_diagnosis_predictions(
    predictions: list[CmdPrediction],
) -> list[DiagnosisPrediction]:
    return [
        DiagnosisPrediction(
            system_name="CMD-Audit",
            case_id=row.case_id,
            gold_label=row.gold_label,
            predicted_label=row.predicted_label,
            top2_labels=row.top2_labels,
            cost_per_diagnosis=_metric_cost_for_non_reporting_path(row.cost),
        )
        for row in predictions
    ]


def _metric_cost_for_non_reporting_path(cost: CostLatency) -> float:
    if cost.usd_cost is not None:
        return cost.usd_cost
    return INTERNAL_METRIC_COST_NOT_REPORTED


def bootstrap_ci(
    predictions: list[CmdPrediction],
    metric_name: str,
    *,
    iterations: int = 1000,
    seed: int = 42,
) -> tuple[float, float]:
    if not predictions:
        return (0.0, 0.0)
    rng = random.Random(seed)
    values: list[float] = []
    for _ in range(iterations):
        sample = [rng.choice(predictions) for _ in predictions]
        values.append(compute_cmd_metrics(sample)[metric_name])
    values.sort()
    lower = values[int(0.025 * (len(values) - 1))]
    upper = values[int(0.975 * (len(values) - 1))]
    return lower, upper


def bootstrap_metric_ci(
    predictions: list[DiagnosisPrediction],
    metric_name: str,
    *,
    iterations: int = 1000,
    seed: int = 42,
) -> tuple[float, float]:
    if not predictions:
        return (0.0, 0.0)
    rng = random.Random(seed)
    values: list[float] = []
    system_name = predictions[0].system_name
    for _ in range(iterations):
        sample = [rng.choice(predictions) for _ in predictions]
        metrics = compute_diagnosis_metrics(sample, labels=HEADLINE_LABELS)
        values.append(getattr(metrics[system_name], metric_name))
    values.sort()
    lower = values[int(0.025 * (len(values) - 1))]
    upper = values[int(0.975 * (len(values) - 1))]
    return lower, upper


def build_baseline_predictions(
    researcher_cases: dict[str, ResearcherCase],
    case_index: dict[str, ProbeCase],
    *,
    llm_client=None,
) -> list[DiagnosisPrediction]:
    rows: list[DiagnosisPrediction] = []
    missing: list[str] = []
    for case_id, researcher in researcher_cases.items():
        case = case_index.get(case_id)
        if case is None:
            missing.append(case_id)
            continue
        suite = run_baseline_suite(case, llm_client=llm_client)
        for comparator in suite.comparator_results:
            rows.append(
                DiagnosisPrediction(
                    system_name=comparator.comparator_name,
                    case_id=case_id,
                    gold_label=researcher.gold_label,
                    predicted_label=comparator.predicted_label,
                    top2_labels=comparator.top2_labels,
                    cost_per_diagnosis=comparator.cost_per_diagnosis,
                )
            )
    if missing:
        preview = ", ".join(missing[:10])
        raise ValueError(f"missing case rows for baseline comparison: {preview}")
    return rows


def build_comparison_rows(
    cmd_predictions: list[CmdPrediction],
    baseline_predictions: list[DiagnosisPrediction],
) -> list[dict[str, str]]:
    cmd_rows = cmd_diagnosis_predictions(cmd_predictions)
    confidence_by_case = {row.case_id: row.confidence for row in cmd_predictions}
    cmd_cost_by_group = {
        "high_medium": _mean_cost(
            [row.cost for row in cmd_predictions if row.confidence in {"high", "medium"}]
        ),
        "all_130": _mean_cost([row.cost for row in cmd_predictions]),
    }
    rows: list[dict[str, str]] = []
    for group_name, allowed_conf in (
        ("high_medium", {"high", "medium"}),
        ("all_130", {"high", "medium", "low"}),
    ):
        group_predictions = [
            row
            for row in [*cmd_rows, *baseline_predictions]
            if confidence_by_case.get(row.case_id) in allowed_conf
        ]
        metrics_by_system = compute_diagnosis_metrics(
            group_predictions,
            labels=HEADLINE_LABELS,
        )
        by_system: dict[str, list[DiagnosisPrediction]] = defaultdict(list)
        for row in group_predictions:
            by_system[row.system_name].append(row)
        for system_name, metrics in sorted(metrics_by_system.items()):
            cost = cmd_cost_by_group[group_name] if system_name == "CMD-Audit" else None
            macro_low, macro_high = bootstrap_metric_ci(
                by_system[system_name],
                "macro_f1",
            )
            top2_low, top2_high = bootstrap_metric_ci(
                by_system[system_name],
                "top2_accuracy",
            )
            rows.append(
                {
                    "group": group_name,
                    "system_name": system_name,
                    "n": str(len(by_system[system_name])),
                    "macro_f1": f"{metrics.macro_f1:.4f}",
                    "macro_f1_ci_low": f"{macro_low:.4f}",
                    "macro_f1_ci_high": f"{macro_high:.4f}",
                    "top2_accuracy": f"{metrics.top2_accuracy:.4f}",
                    "top2_ci_low": f"{top2_low:.4f}",
                    "top2_ci_high": f"{top2_high:.4f}",
                    "attribution_accuracy": f"{metrics.attribution_accuracy:.4f}",
                    "tokens_per_case": _fmt_optional(
                        cost.total_tokens if cost is not None else None
                    ),
                    "agent_tokens_per_case": _fmt_optional(
                        cost.agent_tokens if cost is not None else None
                    ),
                    "scorer_tokens_per_case": _fmt_optional(
                        cost.scorer_tokens if cost is not None else None
                    ),
                    "verifier_tokens_per_case": _fmt_optional(
                        cost.verifier_tokens if cost is not None else None
                    ),
                    "wallclock_sec_per_case": _fmt_optional(
                        cost.wallclock_sec if cost is not None else None
                    ),
                    "usd_per_case": _fmt_optional(
                        cost.usd_cost if cost is not None else None
                    ),
                    "cost_metadata_status": (
                        cost.status if cost is not None else "not_measured_for_baseline"
                    ),
                }
            )
    return rows


def build_cost_latency_rows(predictions: list[CmdPrediction]) -> list[dict[str, str]]:
    return [
        {
            "case_id": row.case_id,
            "source": row.source,
            "confidence": row.confidence,
            "agent_tokens": _fmt_optional(row.cost.agent_tokens),
            "scorer_tokens": _fmt_optional(row.cost.scorer_tokens),
            "verifier_tokens": _fmt_optional(row.cost.verifier_tokens),
            "tokens_total": _fmt_optional(row.cost.total_tokens),
            "wallclock_sec": _fmt_optional(row.cost.wallclock_sec),
            "usd_cost": _fmt_optional(row.cost.usd_cost),
            "cost_metadata_status": row.cost.status,
        }
        for row in predictions
    ]


def build_headline_rows(predictions: list[CmdPrediction]) -> list[dict[str, str]]:
    groups = {
        "high_medium": [p for p in predictions if p.confidence in {"high", "medium"}],
        "all_130": predictions,
    }
    rows: list[dict[str, str]] = []
    for group, group_predictions in groups.items():
        metrics = compute_cmd_metrics(group_predictions)
        macro_low, macro_high = bootstrap_ci(group_predictions, "macro_f1")
        top2_low, top2_high = bootstrap_ci(group_predictions, "top2_accuracy")
        rows.append(
            {
                "group": group,
                "n": str(int(metrics["n"])),
                "coverage": f"{metrics['coverage']:.4f}",
                "macro_f1": f"{metrics['macro_f1']:.4f}",
                "macro_f1_ci_low": f"{macro_low:.4f}",
                "macro_f1_ci_high": f"{macro_high:.4f}",
                "top2_accuracy": f"{metrics['top2_accuracy']:.4f}",
                "top2_ci_low": f"{top2_low:.4f}",
                "top2_ci_high": f"{top2_high:.4f}",
                "attribution_accuracy": f"{metrics['attribution_accuracy']:.4f}",
                "attribution_failed": str(int(metrics["attribution_failed"])),
            }
        )
    return rows


def build_heatmap_rows(predictions: list[CmdPrediction]) -> list[dict[str, str]]:
    grouped: dict[tuple[str, str], list[CmdPrediction]] = defaultdict(list)
    for row in predictions:
        grouped[(row.gold_label, row.source)].append(row)
    rows: list[dict[str, str]] = []
    for (label, source), group in sorted(grouped.items()):
        metrics = compute_cmd_metrics(group)
        rows.append(
            {
                "label": label,
                "source": source,
                "n": str(len(group)),
                "accuracy": f"{metrics['attribution_accuracy']:.4f}",
                "macro_f1": f"{metrics['macro_f1']:.4f}",
                "coverage": f"{metrics['coverage']:.4f}",
            }
        )
    return rows


def write_csv(path: str | Path, rows: list[dict[str, str]]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError(f"no rows for {path}")
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _label_for_replay(replay_name: str, *, has_ingestion_trace: bool) -> str:
    if replay_name == "oracle_write" and not has_ingestion_trace:
        return "ingestion_error"
    return REPLAY_TO_LABEL[replay_name]


def _cost_from_retest_rows(rows: list[dict[str, str]]) -> CostLatency:
    agent_tokens = _sum_optional(rows, ("agent_tokens", "agent_token_count"))
    scorer_tokens = _sum_optional(rows, ("scorer_tokens", "evaluator_tokens"))
    verifier_tokens = _sum_optional(rows, ("verifier_tokens", "answer_verifier_tokens"))
    total_tokens = _sum_optional(rows, ("tokens_total", "total_tokens", "token_count"))
    if total_tokens is None:
        token_parts = [agent_tokens, scorer_tokens, verifier_tokens]
        if any(value is not None for value in token_parts):
            total_tokens = sum(value or 0.0 for value in token_parts)
    return CostLatency(
        agent_tokens=agent_tokens,
        scorer_tokens=scorer_tokens,
        verifier_tokens=verifier_tokens,
        total_tokens=total_tokens,
        wallclock_sec=_sum_optional(
            rows,
            ("wallclock_sec", "wallclock_seconds", "elapsed_seconds"),
        ),
        usd_cost=_sum_optional(rows, ("usd_cost", "usd", "cost_usd")),
    )


def _sum_optional(rows: list[dict[str, str]], keys: tuple[str, ...]) -> float | None:
    total = 0.0
    found = False
    for row in rows:
        for key in keys:
            raw = row.get(key)
            if raw not in (None, ""):
                total += float(raw)
                found = True
                break
    return total if found else None


def _mean_cost(costs: list[CostLatency]) -> CostLatency:
    return CostLatency(
        agent_tokens=_mean_optional(cost.agent_tokens for cost in costs),
        scorer_tokens=_mean_optional(cost.scorer_tokens for cost in costs),
        verifier_tokens=_mean_optional(cost.verifier_tokens for cost in costs),
        total_tokens=_mean_optional(cost.total_tokens for cost in costs),
        wallclock_sec=_mean_optional(cost.wallclock_sec for cost in costs),
        usd_cost=_mean_optional(cost.usd_cost for cost in costs),
    )


def _mean_optional(values) -> float | None:
    observed = [float(value) for value in values if value is not None]
    return sum(observed) / len(observed) if observed else None


def _fmt_optional(value: float | None) -> str:
    return "" if value is None else f"{value:.6f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Experiment 2 headline tables")
    parser.add_argument("--retest-csv", required=True)
    parser.add_argument("--researcher-subset", default=None)
    parser.add_argument(
        "--label-source",
        choices=("researcher", "original"),
        default="researcher",
        help=(
            "researcher = require cases[*].researcher_label for headline tables; "
            "original = use source perturbation_label/deepseek labels for "
            "unadjudicated baseline sanity"
        ),
    )
    parser.add_argument("--input-dir", default="data/probe_cases")
    parser.add_argument("--out-dir", default="artifacts/headline_130")
    parser.add_argument(
        "--run-llm-judge",
        action="store_true",
        help=(
            "run the llm_judge comparator through an OpenAI-compatible endpoint; "
            "without this flag llm_judge remains the offline fallback"
        ),
    )
    parser.add_argument("--llm-judge-base-url", default=None)
    parser.add_argument("--llm-judge-model", default=None)
    parser.add_argument("--llm-judge-timeout", type=float, default=120.0)
    args = parser.parse_args(argv)

    llm_client = None
    if args.run_llm_judge:
        env_defaults = LLMClientConfig()
        llm_client = LLMClient(
            LLMClientConfig(
                base_url=args.llm_judge_base_url or env_defaults.base_url,
                model=args.llm_judge_model or env_defaults.model,
                timeout_seconds=args.llm_judge_timeout,
                temperature=0.0,
                max_retries=1,
            )
        )

    case_index = load_case_index(args.input_dir)
    retest_by_case = load_retest_by_case(args.retest_csv)
    if args.label_source == "researcher":
        if args.researcher_subset is None:
            raise ValueError("--researcher-subset is required with --label-source researcher")
        researcher_cases = load_researcher_cases(args.researcher_subset)
    else:
        researcher_cases = load_original_label_cases(
            retest_by_case=retest_by_case,
            case_index=case_index,
            subset_path=args.researcher_subset,
        )
    predictions = build_cmd_predictions(
        researcher_cases,
        retest_by_case,
        case_index,
    )
    baseline_predictions = build_baseline_predictions(
        researcher_cases,
        case_index,
        llm_client=llm_client,
    )
    out_dir = Path(args.out_dir)
    write_csv(out_dir / "experiment_02_headline.csv", build_headline_rows(predictions))
    write_csv(
        out_dir / "experiment_02_comparison.csv",
        build_comparison_rows(predictions, baseline_predictions),
    )
    write_csv(
        out_dir / "experiment_02_cost_latency.csv",
        build_cost_latency_rows(predictions),
    )
    write_csv(out_dir / "experiment_02_heatmap.csv", build_heatmap_rows(predictions))
    print(f"wrote Experiment 2 tables for {len(predictions)} researcher cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
