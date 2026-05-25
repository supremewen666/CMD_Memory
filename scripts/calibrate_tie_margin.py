#!/usr/bin/env python3
"""Sample and calibrate coupled-failure tie margins from LLM retest outputs.

This script is offline-only: it reads issue 0023's
``artifacts/at_scale_llm_retest.csv`` and a researcher-inspected JSON file. It
does not run CMD replays, train RPE filters, or call LLMs.
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

from cmd_audit.labels import V1_REPLAY_TO_LABEL
from cmd_audit.models import load_real_cases_by_source


@dataclass(frozen=True)
class CaseMeta:
    gold_label: str
    source: str


@dataclass(frozen=True)
class CaseGaps:
    case_id: str
    gold_label: str
    source: str
    top_replay: str
    second_replay: str
    top_gain: float
    second_gain: float

    @property
    def top2_gap(self) -> float:
        return self.top_gain - self.second_gain


def load_retest_rows(path: str | Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"case_id", "replay_name", "recovery_gain"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(
                "retest CSV missing required columns: " + ", ".join(sorted(missing))
            )
        return list(reader)


def load_case_meta(input_dir: str | Path) -> dict[str, CaseMeta]:
    meta: dict[str, CaseMeta] = {}
    for source, cases in load_real_cases_by_source(input_dir).items():
        for case in cases:
            if case.perturbation_label is not None:
                meta[case.case_id] = CaseMeta(
                    gold_label=case.perturbation_label,
                    source=source,
                )
    return meta


def compute_case_gaps(
    rows: list[dict[str, str]],
    case_meta: dict[str, CaseMeta] | None = None,
) -> list[CaseGaps]:
    by_case: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_case[row["case_id"]].append(row)

    gaps: list[CaseGaps] = []
    for case_id, case_rows in by_case.items():
        ranked = sorted(
            case_rows,
            key=lambda r: float(r["recovery_gain"]),
            reverse=True,
        )
        if len(ranked) < 2:
            continue
        top, second = ranked[0], ranked[1]
        meta = case_meta.get(case_id) if case_meta is not None else None
        gaps.append(
            CaseGaps(
                case_id=case_id,
                gold_label=top.get("gold_label")
                or top.get("perturbation_label")
                or (meta.gold_label if meta is not None else "")
                or "",
                source=top.get("source") or (meta.source if meta is not None else ""),
                top_replay=top["replay_name"],
                second_replay=second["replay_name"],
                top_gain=float(top["recovery_gain"]),
                second_gain=float(second["recovery_gain"]),
            )
        )
    return gaps


def sample_near_ties(
    gaps: list[CaseGaps],
    *,
    max_gap: float,
    target_cases: int,
    seed: int,
) -> list[CaseGaps]:
    candidates = [gap for gap in gaps if gap.top2_gap < max_gap]
    by_label: dict[str, list[CaseGaps]] = defaultdict(list)
    for gap in candidates:
        by_label[gap.gold_label].append(gap)

    rng = random.Random(seed)
    selected: list[CaseGaps] = []
    labels = sorted(by_label)
    if labels:
        per_label = max(1, target_cases // len(labels))
        for label in labels:
            pool = list(by_label[label])
            rng.shuffle(pool)
            selected.extend(pool[:per_label])

    if len(selected) < target_cases:
        selected_ids = {gap.case_id for gap in selected}
        remainder = [gap for gap in candidates if gap.case_id not in selected_ids]
        rng.shuffle(remainder)
        selected.extend(remainder[: target_cases - len(selected)])

    selected = selected[:target_cases]
    selected.sort(key=lambda gap: (gap.gold_label, gap.case_id))
    return selected


def build_inspection_payload(
    selected: list[CaseGaps],
    *,
    retest_csv: str,
    seed: int,
    max_gap: float,
    target_cases: int,
) -> dict:
    return {
        "schema_version": "1.0",
        "decision": "Decision 34 R3 (2026-05-23/24)",
        "release_version": "v1.0",
        "source_retest_csv": retest_csv,
        "sampling": {
            "random_state": seed,
            "top2_gap_threshold": max_gap,
            "target_cases": target_cases,
        },
        "labels": {"allowed": ["genuine_coupled", "scorer_noise"]},
        "cases": [
            {
                "case_id": gap.case_id,
                "gold_label": gap.gold_label,
                "source": gap.source,
                "top_replay": gap.top_replay,
                "second_replay": gap.second_replay,
                "top_label": V1_REPLAY_TO_LABEL.get(gap.top_replay, ""),
                "second_label": V1_REPLAY_TO_LABEL.get(gap.second_replay, ""),
                "top_gain": round(gap.top_gain, 6),
                "second_gain": round(gap.second_gain, 6),
                "top2_gap": round(gap.top2_gap, 6),
                "coupled_label": None,
                "researcher_notes": "",
            }
            for gap in selected
        ],
    }


def load_inspection(path: str | Path) -> dict[str, str]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    labels: dict[str, str] = {}
    for row in payload.get("cases", []):
        label = row.get("coupled_label")
        if label in {"genuine_coupled", "scorer_noise"}:
            labels[row["case_id"]] = label
    return labels


def calibrate_margin(
    gaps: list[CaseGaps],
    inspected_labels: dict[str, str],
) -> tuple[float, list[dict[str, str]]]:
    inspected = [gap for gap in gaps if gap.case_id in inspected_labels]
    if not inspected:
        raise ValueError("inspection JSON has no labeled cases")

    rows: list[dict[str, str]] = []
    best_margin = 0.0
    best_score = (-1.0, -1.0)

    for i in range(0, 21):
        margin = round(i * 0.005, 3)
        predicted = {gap.case_id for gap in inspected if gap.top2_gap <= margin}
        genuine = {
            gap.case_id
            for gap in inspected
            if inspected_labels[gap.case_id] == "genuine_coupled"
        }
        noise = {
            gap.case_id
            for gap in inspected
            if inspected_labels[gap.case_id] == "scorer_noise"
        }

        tp = len(predicted & genuine)
        fn = len(genuine - predicted)
        fp = len(predicted & noise)
        recall = tp / (tp + fn) if tp + fn else 0.0
        fp_rate = fp / len(noise) if noise else 0.0
        valid = recall >= 0.80 and fp_rate <= 0.20
        score = (1.0 if valid else 0.0, recall - fp_rate)
        if score > best_score:
            best_score = score
            best_margin = margin
        rows.append(
            {
                "tie_margin": f"{margin:.3f}",
                "coupled_recall": f"{recall:.4f}",
                "scorer_noise_fp_rate": f"{fp_rate:.4f}",
                "valid": str(valid),
                "tp": str(tp),
                "fp": str(fp),
                "fn": str(fn),
            }
        )

    return best_margin, rows


def write_csv(path: str | Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        raise ValueError("no rows to write")
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def build_distribution_rows(
    gaps: list[CaseGaps],
    *,
    tie_margin: float,
) -> list[dict[str, str]]:
    by_label: dict[str, list[CaseGaps]] = defaultdict(list)
    for gap in gaps:
        label = gap.gold_label or "unknown"
        by_label[label].append(gap)
    rows: list[dict[str, str]] = []
    for label, label_gaps in sorted(by_label.items()):
        flagged = [gap for gap in label_gaps if gap.top2_gap <= tie_margin]
        rows.append(
            {
                "label": label,
                "n": str(len(label_gaps)),
                "flagged_coupled_signature": str(len(flagged)),
                "flagged_fraction": (
                    f"{len(flagged) / len(label_gaps):.4f}"
                    if label_gaps
                    else "0.0000"
                ),
                "tie_margin": f"{tie_margin:.3f}",
            }
        )
    return rows


def write_summary(
    path: str | Path,
    *,
    best_margin: float,
    distribution_rows: list[dict[str, str]],
) -> None:
    total = sum(int(row["n"]) for row in distribution_rows)
    flagged = sum(int(row["flagged_coupled_signature"]) for row in distribution_rows)
    fraction = flagged / total if total else 0.0
    lines = [
        "Coupled-failure subset post-hoc summary",
        "",
        "Role: supplementary empirical signature; not headline attribution tuning.",
        f"calibrated_tie_margin: {best_margin:.3f}",
        f"total_cases: {total}",
        f"flagged_coupled_signature: {flagged}",
        f"flagged_fraction: {fraction:.4f}",
        "",
        "Framing: the 596 cases are single-fault by construction; this margin "
        "identifies coupled-failure signatures, not ground-truth coupled labels.",
        "",
    ]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calibrate coupled-failure tie margin")
    sub = parser.add_subparsers(dest="command", required=True)

    sample = sub.add_parser("sample", help="sample near-tie cases for inspection")
    sample.add_argument("--retest-csv", required=True)
    sample.add_argument("--input-dir", default="data/probe_cases")
    sample.add_argument(
        "--output", default="data/probe_cases/coupled_failure_inspected_subset.json"
    )
    sample.add_argument("--max-gap", type=float, default=0.10)
    sample.add_argument("--target-cases", type=int, default=50)
    sample.add_argument("--seed", type=int, default=45)
    sample.add_argument("--dry-run", action="store_true")

    cal = sub.add_parser("calibrate", help="fit margin from inspected labels")
    cal.add_argument("--retest-csv", required=True)
    cal.add_argument("--input-dir", default="data/probe_cases")
    cal.add_argument(
        "--inspection", default="data/probe_cases/coupled_failure_inspected_subset.json"
    )
    cal.add_argument("--output", default="artifacts/tie_margin_calibration.csv")
    cal.add_argument(
        "--distribution-output",
        default="artifacts/coupled_failure_distribution.csv",
    )
    cal.add_argument("--summary-output", default="artifacts/coupled_failure_summary.txt")

    args = parser.parse_args(argv)
    gaps = compute_case_gaps(
        load_retest_rows(args.retest_csv),
        load_case_meta(args.input_dir),
    )

    if args.command == "sample":
        selected = sample_near_ties(
            gaps,
            max_gap=args.max_gap,
            target_cases=args.target_cases,
            seed=args.seed,
        )
        payload = build_inspection_payload(
            selected,
            retest_csv=args.retest_csv,
            seed=args.seed,
            max_gap=args.max_gap,
            target_cases=args.target_cases,
        )
        if args.dry_run:
            print(f"candidate_cases={len(selected)}")
            return 0
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {len(selected)} cases to {out}")
        return 0

    labels = load_inspection(args.inspection)
    best, rows = calibrate_margin(gaps, labels)
    write_csv(args.output, rows)
    distribution_rows = build_distribution_rows(gaps, tie_margin=best)
    write_csv(args.distribution_output, distribution_rows)
    write_summary(
        args.summary_output,
        best_margin=best,
        distribution_rows=distribution_rows,
    )
    print(f"best_tie_margin={best:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
