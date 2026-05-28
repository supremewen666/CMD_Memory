#!/usr/bin/env python3
"""Sample the Decision 34 Experiment 1 80-case ECS inspection set.

This script consumes the at-scale retest CSV from issue 0023 and writes a
researcher-inspection JSON skeleton. It does not run replays or LLM calls.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cmd_audit.core.models import ProbeCase, load_real_cases_by_source


EXPERIMENT_1_LABELS = (
    "retrieval_error",
    "compression_error",
    "premature_extraction_error",
    "reasoning_error",
)


def load_positive_retest_case_ids(path: str | Path) -> set[str]:
    positive: set[str] = set()
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"case_id", "recovery_gain"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(
                "retest CSV missing required columns: " + ", ".join(sorted(missing))
            )
        for row in reader:
            try:
                gain = float(row["recovery_gain"])
            except ValueError as exc:
                raise ValueError(
                    f"invalid recovery_gain for {row.get('case_id')}: "
                    f"{row['recovery_gain']!r}"
                ) from exc
            if gain > 0.0:
                positive.add(row["case_id"])
    return positive


def sample_cases(
    cases_by_source: dict[str, list[ProbeCase]],
    positive_case_ids: set[str],
    *,
    per_label: int,
    seed: int,
) -> list[tuple[str, ProbeCase]]:
    rng = random.Random(seed)
    grouped: dict[str, list[tuple[str, ProbeCase]]] = defaultdict(list)
    fallback: dict[str, list[tuple[str, ProbeCase]]] = defaultdict(list)

    for source, cases in cases_by_source.items():
        if source == "null_label":
            continue
        for case in cases:
            label = case.perturbation_label
            if label not in EXPERIMENT_1_LABELS:
                continue
            target = grouped if case.case_id in positive_case_ids else fallback
            target[label].append((source, case))

    selected: list[tuple[str, ProbeCase]] = []
    for label in EXPERIMENT_1_LABELS:
        preferred = list(grouped[label])
        backup = list(fallback[label])
        rng.shuffle(preferred)
        rng.shuffle(backup)
        pool = preferred + backup
        if len(pool) < per_label:
            raise ValueError(
                f"not enough {label} cases: need {per_label}, got {len(pool)}"
            )
        selected.extend(pool[:per_label])

    selected.sort(key=lambda item: item[1].case_id)
    return selected


def build_payload(
    sampled: list[tuple[str, ProbeCase]],
    *,
    retest_csv: str,
    release_version: str,
    seed: int,
) -> dict:
    return {
        "schema_version": "1.0",
        "decision": "Decision 34 R7 (2026-05-23/24)",
        "release_version": release_version,
        "purpose": (
            "Manually inspected/edited ECS records for Experiment 1's 80 cases. "
            "Decouples context-mode effect from ECS-generation quality."
        ),
        "source_retest_csv": retest_csv,
        "sampling": {
            "random_state": seed,
            "labels": list(EXPERIMENT_1_LABELS),
            "per_label": 20,
            "preference": "recovery_gain > 0 cases preferred from issue 0023 retest",
        },
        "inspection_window": (
            "2026-06-01 ~ 2026-06-03 (V1.0); re-run post-issue-0035 (V1.1)"
        ),
        "inspector": "supremewen",
        "replaced_cases": [],
        "cases": [
            {
                "case_id": case.case_id,
                "source": source,
                "label": case.perturbation_label,
                "original_ecs": {
                    "cause": "",
                    "corrected_memory": "",
                    "repair_guidance": "",
                },
                "edited_ecs": {
                    "cause": "",
                    "corrected_memory": "",
                    "repair_guidance": "",
                },
                "edit_reason": "",
                "replacement_reason": "",
                "none_mode_precheck": {
                    "trial_answers": [],
                    "excluded": None,
                },
            }
            for source, case in sampled
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sample Decision 34 Experiment 1 ECS inspection set"
    )
    parser.add_argument("--input-dir", default="data/probe_cases")
    parser.add_argument("--retest-csv", required=True)
    parser.add_argument(
        "--output", default="data/probe_cases/experiment_01_inspected_ecs.json"
    )
    parser.add_argument("--per-label", type=int, default=20)
    parser.add_argument("--seed", type=int, default=44)
    parser.add_argument("--release-version", default="v1.0")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    positive = load_positive_retest_case_ids(args.retest_csv)
    sampled = sample_cases(
        load_real_cases_by_source(args.input_dir),
        positive,
        per_label=args.per_label,
        seed=args.seed,
    )
    payload = build_payload(
        sampled,
        retest_csv=args.retest_csv,
        release_version=args.release_version,
        seed=args.seed,
    )

    if args.dry_run:
        print(json.dumps(payload["sampling"], indent=2))
        print(f"sampled_cases={len(payload['cases'])}")
        return 0

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(payload['cases'])} cases to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
