#!/usr/bin/env python3
"""Summarize issue 0023 at-scale LLM retest CSV outputs.

This is a post-processing utility only. It reads an already-produced
``artifacts/at_scale_llm_retest.csv`` and real-case labels, then writes the
scale-sanity summary required by issue 0023. It does not call agents, scorers,
hooks, RPE training, or LLMs.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cmd_audit.labels import V1_PIPELINE_LABEL_ORDER, V1_REPLAY_TO_LABEL
from cmd_audit.metrics import DiagnosisPrediction, compute_diagnosis_metrics
from cmd_audit.models import ProbeCase, load_real_cases_by_source


# DiagnosisPrediction still requires a numeric cost. This sentinel is only for
# metric computation in a summary that does not emit cost fields.
INTERNAL_METRIC_COST_NOT_REPORTED = 0.0


@dataclass(frozen=True)
class CaseMeta:
    case_id: str
    source: str
    gold_label: str | None
    has_ingestion_trace: bool


@dataclass(frozen=True)
class ScalePrediction:
    case_id: str
    source: str
    gold_label: str | None
    predicted_label: str | None
    top2_labels: tuple[str, ...]
    attribution_failed: bool
    failure_reason: str
    top_replay: str
    top_gain: float


def load_case_meta(input_dir: str | Path) -> dict[str, CaseMeta]:
    meta: dict[str, CaseMeta] = {}
    for source, cases in load_real_cases_by_source(input_dir).items():
        for case in cases:
            meta[case.case_id] = CaseMeta(
                case_id=case.case_id,
                source=source,
                gold_label=case.perturbation_label,
                has_ingestion_trace=case.has_ingestion_trace,
            )
    return meta


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


def build_scale_predictions(
    retest_by_case: dict[str, list[dict[str, str]]],
    case_meta: dict[str, CaseMeta],
) -> list[ScalePrediction]:
    predictions: list[ScalePrediction] = []
    missing: list[str] = []
    for case_id, rows in sorted(retest_by_case.items()):
        meta = case_meta.get(case_id)
        if meta is None:
            missing.append(case_id)
            continue
        ranked = sorted(rows, key=lambda row: float(row["recovery_gain"]), reverse=True)
        if not ranked:
            continue
        top = ranked[0]
        top_gain = float(top["recovery_gain"])
        attribution_failed = top_gain <= 0.0
        failure_reason = ""
        predicted_label = None
        if attribution_failed:
            failure_reason = "zero_gain" if top_gain == 0.0 else "negative_gain"
        else:
            predicted_label = _label_for_replay(
                top["replay_name"],
                has_ingestion_trace=meta.has_ingestion_trace,
            )
        top2_labels = tuple(
            _label_for_replay(row["replay_name"], has_ingestion_trace=meta.has_ingestion_trace)
            for row in ranked[:2]
        )
        predictions.append(
            ScalePrediction(
                case_id=case_id,
                source=meta.source,
                gold_label=meta.gold_label,
                predicted_label=predicted_label,
                top2_labels=top2_labels,
                attribution_failed=attribution_failed,
                failure_reason=failure_reason,
                top_replay=top["replay_name"],
                top_gain=top_gain,
            )
        )
    if missing:
        preview = ", ".join(missing[:10])
        suffix = "..." if len(missing) > 10 else ""
        raise ValueError(f"retest CSV contains unknown case_ids: {preview}{suffix}")
    return predictions


def summarize_predictions(predictions: list[ScalePrediction]) -> list[dict[str, str]]:
    groups: dict[str, list[ScalePrediction]] = {"aggregate": predictions}
    for row in predictions:
        groups.setdefault(row.source, []).append(row)

    summary_rows: list[dict[str, str]] = []
    for group_name, group_rows in groups.items():
        labeled_rows = [row for row in group_rows if row.gold_label is not None]
        metric_rows = [
            DiagnosisPrediction(
                system_name="CMD-Audit",
                case_id=row.case_id,
                gold_label=row.gold_label,
                predicted_label=row.predicted_label,
                top2_labels=row.top2_labels,
                # Scale-sanity summary does not report cost. Real cost/latency
                # is emitted by issue 0026 from measured metadata only.
                cost_per_diagnosis=INTERNAL_METRIC_COST_NOT_REPORTED,
            )
            for row in labeled_rows
        ]
        metrics = compute_diagnosis_metrics(
            metric_rows,
            labels=V1_PIPELINE_LABEL_ORDER,
        )["CMD-Audit"]
        failed = sum(row.attribution_failed for row in labeled_rows)
        summary_rows.append(
            {
                "group": group_name,
                "n": str(len(labeled_rows)),
                "macro_f1": f"{metrics.macro_f1:.6f}",
                "attribution_accuracy": f"{metrics.attribution_accuracy:.6f}",
                "top2_accuracy": f"{metrics.top2_accuracy:.6f}",
                "attribution_failed": str(failed),
            }
        )
    return summary_rows


def write_summary(path: str | Path, rows: list[dict[str, str]]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "At-scale LLM retest scale-sanity summary",
        "",
        "Role: supplementary agreement against deepseek-v4-pro-max labels; not "
        "the paper headline attribution claim.",
        "",
    ]
    for row in rows:
        lines.extend(
            [
                f"[{row['group']}]",
                f"n: {row['n']}",
                f"macro_f1: {row['macro_f1']}",
                f"attribution_accuracy: {row['attribution_accuracy']}",
                f"top2_accuracy: {row['top2_accuracy']}",
                f"attribution_failed: {row['attribution_failed']}",
                "",
            ]
        )
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def write_summary_csv(path: str | Path, rows: list[dict[str, str]]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _label_for_replay(replay_name: str, *, has_ingestion_trace: bool) -> str:
    if replay_name == "oracle_write" and not has_ingestion_trace:
        return "ingestion_error"
    return V1_REPLAY_TO_LABEL[replay_name]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build issue 0023 at-scale retest summary artifacts"
    )
    parser.add_argument("--retest-csv", default="artifacts/at_scale_llm_retest.csv")
    parser.add_argument("--input-dir", default="data/probe_cases")
    parser.add_argument("--out", default="artifacts/at_scale_llm_retest_summary.txt")
    parser.add_argument("--csv-out", default="artifacts/at_scale_llm_retest_summary.csv")
    args = parser.parse_args(argv)

    predictions = build_scale_predictions(
        load_retest_by_case(args.retest_csv),
        load_case_meta(args.input_dir),
    )
    rows = summarize_predictions(predictions)
    write_summary(args.out, rows)
    write_summary_csv(args.csv_out, rows)
    print(f"wrote at-scale retest summary for {len(predictions)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
