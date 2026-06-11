#!/usr/bin/env python3
"""Experiment 10: surrogate-vs-gold recovery-gain retention."""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.core.models import ProbeCase
from cmd_audit.eval.surrogate_gap import GOLD_DEPENDENT_LABELS, measure_surrogate_gaps
from cmd_audit.eval.writers import write_csv_table
from experiments.experiment_runner_common import (
    DATA,
    OUT,
    assert_g_eval_available,
    build_evidence_scorer,
    load_raw_rows,
    write_surrogate_gap_rows,
)
from experiments.experiment_runner_common import AgentGenerateWithLogprobs, AGENT_SYSTEM_PROMPT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        nargs="+",
        default=[
            str(DATA / "real_longmemeval_cases.json"),
            str(DATA / "real_memoryarena_cases.json"),
            str(DATA / "real_toolbench_cases.json"),
        ],
    )
    parser.add_argument("--per-label", type=int, default=13)
    parser.add_argument("--random-state", type=int, default=43)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--threshold", type=float, default=0.8)
    parser.add_argument("--raw-out", default=str(OUT / "experiment_surrogate_gap_rows.csv"))
    args = parser.parse_args()

    client = LLMClient(LLMClientConfig())
    assert_g_eval_available(client, role="surrogate-gap")
    cases = _sample_cases(args.cases, per_label=args.per_label, random_state=args.random_state)
    print(f"Loaded {len(cases)} gold-dependent cases")

    rows = measure_surrogate_gaps(
        cases,
        agent_generate=AgentGenerateWithLogprobs(client, system_prompt=AGENT_SYSTEM_PROMPT),
        scorer=build_evidence_scorer(
            client,
            scorer_mode="g-eval-hybrid",
            max_workers=args.max_workers,
            max_retries=args.max_retries,
        ),
    )
    write_surrogate_gap_rows(args.raw_out, rows)

    gold_success = sum(row.gold_recovery_gain >= args.threshold for row in rows)
    surrogate_success = sum(row.surrogate_recovery_gain >= args.threshold for row in rows)
    gold_gain = _mean([row.gold_recovery_gain for row in rows])
    surrogate_gain = _mean([row.surrogate_recovery_gain for row in rows])
    gold_acc = gold_success / len(rows) if rows else 0.0
    surrogate_acc = surrogate_success / len(rows) if rows else 0.0

    summary_rows = [
        {
            "path": "gold_dependent",
            "label_correctness": f"{gold_acc:.4f}",
            "recovery_gain_mean": f"{gold_gain:.4f}",
        },
        {
            "path": "surrogate",
            "label_correctness": f"{surrogate_acc:.4f}",
            "recovery_gain_mean": f"{surrogate_gain:.4f}",
        },
        {
            "path": "retention_rate",
            "label_correctness": f"{(surrogate_acc / gold_acc if gold_acc else 0.0):.4f}",
            "recovery_gain_mean": f"{(surrogate_gain / gold_gain if gold_gain else 0.0):.4f}",
        },
    ]
    for row in summary_rows:
        print(row)

    out_path = OUT / "experiment_surrogate_gap.csv"
    write_csv_table(out_path, ["path", "label_correctness", "recovery_gain_mean"], summary_rows)
    print(f"Wrote {out_path}")


def _sample_cases(paths: list[str], *, per_label: int, random_state: int) -> list[ProbeCase]:
    by_label: dict[str, list[ProbeCase]] = {label: [] for label in GOLD_DEPENDENT_LABELS}
    for path in paths:
        for row in load_raw_rows(path):
            raw_label = row.get("perturbation_label")
            if raw_label not in by_label:
                continue
            case = ProbeCase.from_mapping(row)
            by_label[raw_label].append(replace(case, perturbation_label=raw_label))

    rng = random.Random(random_state)
    sampled: list[ProbeCase] = []
    for label in GOLD_DEPENDENT_LABELS:
        pool = sorted(by_label[label], key=lambda case: case.case_id)
        if not pool:
            continue
        sampled.extend(rng.sample(pool, min(per_label, len(pool))))
    return sorted(sampled, key=lambda case: (case.perturbation_label or "", case.case_id))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


if __name__ == "__main__":
    main()
