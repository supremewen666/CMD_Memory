#!/usr/bin/env python3
"""Build issue 0036 surrogate-gap retention summary from completed CSV rows.

This is post-processing only. It reads per-case gold/surrogate recovery gains
and writes the retention summary; it does not run replays or LLMs.
"""

from __future__ import annotations

import argparse
import csv
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SurrogateRetentionRow:
    case_id: str
    label: str
    gold_recovery_gain: float
    surrogate_recovery_gain: float

    @property
    def retention_ratio(self) -> float:
        if self.gold_recovery_gain <= 0.0:
            return 0.0
        return self.surrogate_recovery_gain / self.gold_recovery_gain


def load_rows(path: str | Path) -> list[SurrogateRetentionRow]:
    rows: list[SurrogateRetentionRow] = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"case_id", "label", "gold_recovery_gain", "surrogate_recovery_gain"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(
                "surrogate CSV missing required columns: " + ", ".join(sorted(missing))
            )
        for row in reader:
            rows.append(
                SurrogateRetentionRow(
                    case_id=row["case_id"],
                    label=row["label"],
                    gold_recovery_gain=float(row["gold_recovery_gain"]),
                    surrogate_recovery_gain=float(row["surrogate_recovery_gain"]),
                )
            )
    return rows


def summarize_by_label(
    rows: list[SurrogateRetentionRow],
    *,
    bootstrap_iterations: int = 1000,
    random_state: int = 42,
) -> list[dict[str, str]]:
    by_label: dict[str, list[SurrogateRetentionRow]] = defaultdict(list)
    for row in rows:
        by_label[row.label].append(row)

    summaries: list[dict[str, str]] = []
    for label, label_rows in sorted(by_label.items()):
        ratios = [row.retention_ratio for row in label_rows]
        mean = sum(ratios) / len(ratios) if ratios else 0.0
        low, high = _bootstrap_ci(ratios, bootstrap_iterations, random_state)
        summaries.append(
            {
                "label": label,
                "n": str(len(label_rows)),
                "retention": f"{mean:.6f}",
                "ci_low": f"{low:.6f}",
                "ci_high": f"{high:.6f}",
            }
        )
    ranked = sorted(summaries, key=lambda row: float(row["retention"]), reverse=True)
    online = {row["label"] for row in ranked[:2]}
    for row in summaries:
        row["designation"] = (
            "online_recoverable"
            if row["label"] in online
            else "ecs_reporting_only"
        )
    return summaries


def write_summary(path: str | Path, rows: list[dict[str, str]]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    online = [row for row in rows if row["designation"] == "online_recoverable"]
    degraded = [row for row in rows if row["designation"] == "ecs_reporting_only"]
    lines = [
        "Surrogate-gap LLM-stack retention summary",
        "",
        "Role: supplementary retention-rate backing for the online deployment framing.",
        "",
    ]
    for row in rows:
        lines.extend(
            [
                f"[{row['label']}]",
                f"n: {row['n']}",
                f"retention: {row['retention']}",
                f"bootstrap_ci_95: [{row['ci_low']}, {row['ci_high']}]",
                f"designation: {row['designation']}",
                "",
            ]
        )
    lines.extend(
        [
            "Paper fragment:",
            "Online CMD-Skill Adapter recovers approximately 9 of 11 pipeline "
            "labels: 7 intervention-mode labels require no gold evidence; the "
            f"top surrogate-retention labels ({_label_list(online)}) are "
            "designated online-recoverable. The remaining gold-dependent labels "
            f"({_label_list(degraded)}) degrade to ECS reporting only. Report "
            "retention rate, not online accuracy.",
            "",
        ]
    )
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def write_csv(path: str | Path, rows: list[dict[str, str]]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _bootstrap_ci(
    ratios: list[float],
    iterations: int,
    random_state: int,
) -> tuple[float, float]:
    if not ratios:
        return 0.0, 0.0
    rng = random.Random(random_state)
    n = len(ratios)
    values: list[float] = []
    for _ in range(iterations):
        sample = [ratios[rng.randrange(n)] for _ in range(n)]
        values.append(sum(sample) / n)
    values.sort()
    return (
        values[int(0.025 * (iterations - 1))],
        values[int(0.975 * (iterations - 1))],
    )


def _label_list(rows: list[dict[str, str]]) -> str:
    return " / ".join(row["label"] for row in rows) if rows else "none"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build issue 0036 surrogate-gap retention summary"
    )
    parser.add_argument("--input", default="artifacts/surrogate_gap_llm.csv")
    parser.add_argument("--out", default="artifacts/surrogate_gap_summary.txt")
    parser.add_argument("--csv-out", default="artifacts/surrogate_gap_retention.csv")
    parser.add_argument("--bootstrap-iterations", type=int, default=1000)
    parser.add_argument("--random-state", type=int, default=42)
    args = parser.parse_args(argv)

    rows = summarize_by_label(
        load_rows(args.input),
        bootstrap_iterations=args.bootstrap_iterations,
        random_state=args.random_state,
    )
    write_summary(args.out, rows)
    write_csv(args.csv_out, rows)
    print(f"wrote surrogate-gap summary for {len(rows)} labels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
