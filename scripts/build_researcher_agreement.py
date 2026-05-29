#!/usr/bin/env python3
"""Build researcher agreement artifacts for Decision 34 issue 0024.

This script is offline: it reads the populated
``data/probe_cases/researcher_labeled_subset.json`` file and writes the two
agreement reports required by issue 0024. It does not call LLMs.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cmd_audit.eval.agreement import cohen_kappa


@dataclass(frozen=True)
class KappaReport:
    name: str
    n: int
    kappa: float
    ci_low: float
    ci_high: float


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    cases = payload.get("cases", []) if isinstance(payload, dict) else payload
    if not isinstance(cases, list):
        raise ValueError("researcher subset must contain a cases array")
    return [case for case in cases if isinstance(case, dict)]


def paired_labels(
    cases: Iterable[dict[str, Any]],
    *,
    left_keys: tuple[str, ...],
    right_keys: tuple[str, ...],
) -> tuple[list[str], list[str]]:
    left: list[str] = []
    right: list[str] = []
    missing: list[str] = []
    for case in cases:
        case_id = str(case.get("case_id", "<missing-case-id>"))
        left_value = next(
            (case.get(key) for key in left_keys if case.get(key)),
            None,
        )
        right_value = next(
            (case.get(key) for key in right_keys if case.get(key)),
            None,
        )
        if not left_value or not right_value:
            missing.append(case_id)
            continue
        left.append(str(left_value))
        right.append(str(right_value))
    if missing:
        preview = ", ".join(missing[:5])
        suffix = "..." if len(missing) > 5 else ""
        raise ValueError(
            f"missing labels for {left_keys} vs {right_keys}: {preview}{suffix}"
        )
    if not left:
        raise ValueError(f"no paired labels found for {left_keys} vs {right_keys}")
    return left, right


def bootstrap_kappa_ci(
    labels_a: list[str],
    labels_b: list[str],
    *,
    iterations: int = 1000,
    random_state: int = 42,
) -> tuple[float, float]:
    if len(labels_a) != len(labels_b):
        raise ValueError("bootstrap_kappa_ci requires equally sized inputs")
    if not labels_a:
        raise ValueError("bootstrap_kappa_ci requires at least one pair")
    rng = random.Random(random_state)
    n = len(labels_a)
    values: list[float] = []
    for _ in range(iterations):
        sample_a: list[str] = []
        sample_b: list[str] = []
        for _ in range(n):
            index = rng.randrange(n)
            sample_a.append(labels_a[index])
            sample_b.append(labels_b[index])
        values.append(cohen_kappa(sample_a, sample_b))
    values.sort()
    low_index = int(0.025 * (len(values) - 1))
    high_index = int(0.975 * (len(values) - 1))
    return values[low_index], values[high_index]


def build_report(
    name: str,
    labels_a: list[str],
    labels_b: list[str],
    *,
    bootstrap_iterations: int,
    random_state: int,
) -> KappaReport:
    ci_low, ci_high = bootstrap_kappa_ci(
        labels_a,
        labels_b,
        iterations=bootstrap_iterations,
        random_state=random_state,
    )
    return KappaReport(
        name=name,
        n=len(labels_a),
        kappa=cohen_kappa(labels_a, labels_b),
        ci_low=ci_low,
        ci_high=ci_high,
    )


def write_report(path: str | Path, report: KappaReport) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    lines = [
        report.name,
        f"n: {report.n}",
        f"cohen_kappa: {report.kappa:.6f}",
        f"bootstrap_ci_95: [{report.ci_low:.6f}, {report.ci_high:.6f}]",
        "",
    ]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def build_agreement_artifacts(
    subset_path: str | Path,
    *,
    out_dir: str | Path,
    bootstrap_iterations: int = 1000,
    random_state: int = 42,
) -> tuple[KappaReport, KappaReport]:
    cases = load_cases(subset_path)
    researcher, deepseek = paired_labels(
        cases,
        left_keys=("researcher_label", "final_label"),
        right_keys=("deepseek_label", "perturbation_label"),
    )
    blind_cases = [
        case for case in cases
        if case.get("researcher_blind_label") or case.get("blind_label")
    ]
    blind, assisted = paired_labels(
        blind_cases,
        left_keys=("researcher_blind_label", "blind_label"),
        right_keys=("researcher_assisted_label", "researcher_label", "final_label"),
    )
    researcher_report = build_report(
        "Researcher vs deepseek agreement",
        researcher,
        deepseek,
        bootstrap_iterations=bootstrap_iterations,
        random_state=random_state,
    )
    automation_report = build_report(
        "Automation-bias spot-check agreement",
        blind,
        assisted,
        bootstrap_iterations=bootstrap_iterations,
        random_state=random_state + 1,
    )

    root = Path(out_dir)
    write_report(root / "researcher_vs_deepseek_kappa.txt", researcher_report)
    write_report(root / "automation_bias_kappa.txt", automation_report)
    return researcher_report, automation_report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build issue 0024 researcher agreement reports"
    )
    parser.add_argument(
        "--subset",
        default="data/probe_cases/researcher_labeled_subset.json",
    )
    parser.add_argument("--out-dir", default="artifacts")
    parser.add_argument("--bootstrap-iterations", type=int, default=1000)
    parser.add_argument("--random-state", type=int, default=42)
    args = parser.parse_args()

    reports = build_agreement_artifacts(
        args.subset,
        out_dir=args.out_dir,
        bootstrap_iterations=args.bootstrap_iterations,
        random_state=args.random_state,
    )
    for report in reports:
        print(
            f"{report.name}: n={report.n} "
            f"kappa={report.kappa:.4f} "
            f"ci=[{report.ci_low:.4f}, {report.ci_high:.4f}]"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
