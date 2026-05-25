#!/usr/bin/env python3
"""Sample the issue 0036 surrogate-gap LLM hold-out set.

This script only selects case IDs. It does not run gold/surrogate replay paths
or call any LLM.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


GOLD_DEPENDENT_LABELS = (
    "write_error",
    "compression_error",
    "premature_extraction_error",
    "injection_error",
)


@dataclass(frozen=True)
class CaseRef:
    case_id: str
    perturbation_label: str
    source_path: str


def load_case_refs(paths: Iterable[str | Path]) -> list[CaseRef]:
    refs: list[CaseRef] = []
    for path in paths:
        source_path = str(path)
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        rows = payload.get("cases", []) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ValueError(f"{source_path} must contain a JSON array or cases array")
        for row in rows:
            if not isinstance(row, dict):
                continue
            case_id = row.get("case_id")
            label = row.get("perturbation_label")
            if case_id and label:
                refs.append(
                    CaseRef(
                        case_id=str(case_id),
                        perturbation_label=str(label),
                        source_path=source_path,
                    )
                )
    return refs


def load_excluded_case_ids(path: str | Path) -> set[str]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = payload.get("cases", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("researcher subset must contain a JSON array or cases array")
    return {
        str(row["case_id"])
        for row in rows
        if isinstance(row, dict) and row.get("case_id")
    }


def sample_holdout(
    refs: Iterable[CaseRef],
    *,
    excluded_case_ids: set[str],
    per_label: int = 13,
    random_state: int = 43,
) -> list[CaseRef]:
    rng = random.Random(random_state)
    by_label: dict[str, list[CaseRef]] = defaultdict(list)
    for ref in refs:
        if (
            ref.perturbation_label in GOLD_DEPENDENT_LABELS
            and ref.case_id not in excluded_case_ids
        ):
            by_label[ref.perturbation_label].append(ref)

    sampled: list[CaseRef] = []
    for label in GOLD_DEPENDENT_LABELS:
        pool = sorted(by_label[label], key=lambda ref: ref.case_id)
        if not pool:
            raise ValueError(f"no eligible cases for label {label}")
        sample_size = min(per_label, len(pool))
        sampled.extend(rng.sample(pool, sample_size))
    return sorted(sampled, key=lambda ref: (ref.perturbation_label, ref.case_id))


def write_holdout(
    path: str | Path,
    sampled: list[CaseRef],
    *,
    source_cases: list[str],
    excluded_subset: str,
    random_state: int,
    per_label: int,
) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for ref in sampled:
        counts[ref.perturbation_label] = counts.get(ref.perturbation_label, 0) + 1
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "decision": "Decision 34 R10 / issue 0036",
        "random_state": random_state,
        "per_label_target": per_label,
        "source_cases": source_cases,
        "excluded_researcher_subset": excluded_subset,
        "gold_dependent_labels": list(GOLD_DEPENDENT_LABELS),
        "counts_by_label": counts,
        "cases": [
            {
                "case_id": ref.case_id,
                "perturbation_label": ref.perturbation_label,
                "source_path": ref.source_path,
            }
            for ref in sampled
        ],
    }
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sample issue 0036 surrogate-gap hold-out case IDs"
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        default=[
            "data/probe_cases/real_longmemeval_cases.json",
            "data/probe_cases/real_memoryarena_cases.json",
            "data/probe_cases/real_toolbench_cases.json",
        ],
    )
    parser.add_argument(
        "--researcher-subset",
        default="data/probe_cases/researcher_labeled_subset.json",
    )
    parser.add_argument("--out", default="artifacts/surrogate_gap_holdout.json")
    parser.add_argument("--per-label", type=int, default=13)
    parser.add_argument("--random-state", type=int, default=43)
    args = parser.parse_args()

    refs = load_case_refs(args.cases)
    excluded = load_excluded_case_ids(args.researcher_subset)
    sampled = sample_holdout(
        refs,
        excluded_case_ids=excluded,
        per_label=args.per_label,
        random_state=args.random_state,
    )
    write_holdout(
        args.out,
        sampled,
        source_cases=list(args.cases),
        excluded_subset=args.researcher_subset,
        random_state=args.random_state,
        per_label=args.per_label,
    )
    print(f"sampled {len(sampled)} cases -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
