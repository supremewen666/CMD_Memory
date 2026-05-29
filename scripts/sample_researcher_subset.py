#!/usr/bin/env python3
"""Sample the Decision 34 130-case researcher adjudication subset.

This script performs only deterministic sampling and JSON writing. It does not
call LLM-A and does not run CMD experiments.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cmd_audit.core.models import ProbeCase
from cmd_audit.data_io import load_real_cases_by_source


ACTIVE_LABELS = (
    "write_error",
    "compression_error",
    "premature_extraction_error",
    "retrieval_error",
    "injection_error",
    "reasoning_error",
    "route_error",
    "ingestion_error",
)


def sample_cases(
    cases_by_source: dict[str, list[ProbeCase]],
    *,
    target_total: int,
    seed: int,
) -> list[tuple[str, ProbeCase]]:
    rng = random.Random(seed)
    grouped: dict[str, list[tuple[str, ProbeCase]]] = defaultdict(list)
    for source, cases in cases_by_source.items():
        if source == "null_label":
            continue
        for case in cases:
            if case.perturbation_label in ACTIVE_LABELS:
                grouped[case.perturbation_label].append((source, case))

    base = target_total // len(ACTIVE_LABELS)
    selected: list[tuple[str, ProbeCase]] = []
    remaining_pool: list[tuple[str, ProbeCase]] = []

    for label in ACTIVE_LABELS:
        pool = list(grouped.get(label, ()))
        if len(pool) < base:
            raise ValueError(
                f"not enough cases for {label}: need {base}, got {len(pool)}"
            )
        rng.shuffle(pool)
        selected.extend(pool[:base])
        remaining_pool.extend(pool[base:])

    spare = target_total - len(selected)
    if spare > 0:
        if len(remaining_pool) < spare:
            raise ValueError(
                f"not enough spare cases: need {spare}, got {len(remaining_pool)}"
            )
        rng.shuffle(remaining_pool)
        selected.extend(remaining_pool[:spare])

    selected.sort(key=lambda item: item[1].case_id)
    return selected


def select_blind_cases(case_ids: list[str], *, seed: int, count: int) -> list[str]:
    rng = random.Random(seed)
    if len(case_ids) < count:
        raise ValueError(f"need at least {count} cases for blind spot-check")
    return sorted(rng.sample(case_ids, count))


def build_payload(
    sampled: list[tuple[str, ProbeCase]],
    *,
    blind_case_ids: list[str],
    release_version: str,
    total_pool: int,
    seed: int,
    blind_seed: int,
) -> dict:
    blind_set = set(blind_case_ids)
    return {
        "schema_version": "1.0",
        "decision": "Decision 34 R4 + R11 (2026-05-23/24)",
        "release_version": release_version,
        "sampling": {
            "source": (
                "data/probe_cases/real_longmemeval_cases.json + "
                "real_memoryarena_cases.json + real_toolbench_cases.json"
            ),
            "total_pool": total_pool,
            "stratification": (
                "8 active labels x ~16 cases = 128 "
                "(target 130 with 2 spare slots)"
            ),
            "random_state": seed,
            "active_labels": list(ACTIVE_LABELS),
        },
        "annotators": {
            "deepseek": {
                "model": "deepseek-v4-pro-max",
                "role": "scale-sanity annotator",
                "script": "scripts/annotate_perturbation_labels.py",
            },
            "llm_a": {
                "model": "llama-3.3-70b-instruct",
                "role": "candidate suggestion for researcher",
                "constraints": (
                    "family-disjoint from deepseek (annotator), qwen2.5-7b "
                    "(agent_generate), evaluator scorer (TBD)"
                ),
            },
            "researcher": {
                "name": "supremewen",
                "protocol": (
                    "Read case (query, extracted_memory, baseline_outputs, "
                    "gold_answer) + LLM-A suggestion + rationale; assign "
                    "final_label, confidence in {high, medium, low}; record "
                    "disagreements. First 20 cases labeled blind (no LLM-A) "
                    "for automation-bias spot-check."
                ),
            },
        },
        "spot_check": {
            "random_state": blind_seed,
            "blind_case_ids": blind_case_ids,
            "kappa_blind_vs_assisted": None,
            "kappa_threshold_for_validity": 0.7,
        },
        "cases": [
            {
                "case_id": case.case_id,
                "source": source,
                "deepseek_label": case.perturbation_label,
                "llm_a_suggestion": None,
                "llm_a_rationale": "",
                "researcher_label": None,
                "confidence": None,
                "disagreement_with_deepseek": None,
                "disagreement_with_llm_a": None,
                "researcher_notes": "",
                "blind_spot_check": case.case_id in blind_set,
                "researcher_blind_label": None,
                "blind_confidence": None,
                "blind_notes": "",
            }
            for source, case in sampled
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sample Decision 34 researcher adjudication subset"
    )
    parser.add_argument("--input-dir", default="data/probe_cases")
    parser.add_argument(
        "--output", default="data/probe_cases/researcher_labeled_subset.json"
    )
    parser.add_argument("--target-total", type=int, default=130)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--blind-seed", type=int, default=43)
    parser.add_argument("--blind-count", type=int, default=20)
    parser.add_argument("--release-version", default="v1.0")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    cases_by_source = load_real_cases_by_source(args.input_dir)
    total_pool = sum(
        1
        for source, cases in cases_by_source.items()
        if source != "null_label"
        for case in cases
        if case.perturbation_label is not None
    )
    sampled = sample_cases(
        cases_by_source,
        target_total=args.target_total,
        seed=args.seed,
    )
    case_ids = [case.case_id for _source, case in sampled]
    blind_ids = select_blind_cases(
        case_ids,
        seed=args.blind_seed,
        count=args.blind_count,
    )
    payload = build_payload(
        sampled,
        blind_case_ids=blind_ids,
        release_version=args.release_version,
        total_pool=total_pool,
        seed=args.seed,
        blind_seed=args.blind_seed,
    )

    if args.dry_run:
        print(json.dumps(payload["sampling"], indent=2, ensure_ascii=False))
        print(f"sampled_cases={len(payload['cases'])}")
        print(f"blind_cases={len(blind_ids)}")
        return 0

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(payload['cases'])} cases to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
