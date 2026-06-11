#!/usr/bin/env python3
"""Experiment 13: cross-dataset attribution stability."""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.data_io import load_real_cases_by_source
from cmd_audit.eval.metrics import compute_diagnosis_metrics
from cmd_audit.eval.writers import write_csv_table
from cmd_audit.harness import diagnosis_predictions, run_cases
from experiments.experiment_runner_common import (
    AGENT_SYSTEM_PROMPT,
    OUT,
    AgentGenerateWithLogprobs,
    assert_g_eval_available,
    build_answer_verifier,
    build_evidence_scorer,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default="data/probe_cases")
    parser.add_argument("--limit-per-source", type=int, default=0)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--max-retries", type=int, default=1)
    args = parser.parse_args()

    client = LLMClient(LLMClientConfig())
    assert_g_eval_available(client, role="cross-dataset")
    agent_generate = AgentGenerateWithLogprobs(client, system_prompt=AGENT_SYSTEM_PROMPT)
    scorer = build_evidence_scorer(
        client,
        scorer_mode="g-eval-hybrid",
        max_workers=args.max_workers,
        max_retries=args.max_retries,
    )
    answer_verifier = build_answer_verifier(
        client,
        answer_mode="answer-rubric",
        max_workers=1,
        max_retries=args.max_retries,
    )

    metric_rows = []
    for source, cases in load_real_cases_by_source(args.input_dir).items():
        if args.limit_per_source:
            cases = cases[: args.limit_per_source]
        results = run_cases(
            cases,
            scorer=scorer,
            agent_generate=agent_generate,
            answer_verifier=answer_verifier,
        )
        predictions = [p for result in results for p in diagnosis_predictions(result)]
        metrics = compute_diagnosis_metrics(predictions)["CMD-Audit"]
        row = {
            "source": source,
            "macro_f1": f"{metrics.macro_f1:.4f}",
            "accuracy": f"{metrics.attribution_accuracy:.4f}",
            "n_cases": str(len(cases)),
        }
        metric_rows.append(row)
        print(row)

    f1_values = [float(row["macro_f1"]) for row in metric_rows]
    acc_values = [float(row["accuracy"]) for row in metric_rows]
    variance_row = {
        "source": "variance",
        "macro_f1": f"{(statistics.pvariance(f1_values) if len(f1_values) > 1 else 0.0):.4f}",
        "accuracy": f"{(statistics.pvariance(acc_values) if len(acc_values) > 1 else 0.0):.4f}",
        "n_cases": "N/A",
    }
    metric_rows.append(variance_row)
    print(variance_row)

    out_path = OUT / "experiment_cross_dataset.csv"
    write_csv_table(out_path, ["source", "macro_f1", "accuracy", "n_cases"], metric_rows)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
