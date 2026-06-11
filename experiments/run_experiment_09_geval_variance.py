#!/usr/bin/env python3
"""Experiment 9: G-Eval continuous score variance vs sampled rubric scoring."""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.eval.writers import write_csv_table
from cmd_audit.scoring.llm import (
    _RUBRIC_SYSTEM_PROMPT,
    _continuous_verify,
    _parse_rubric_output,
)
from experiments.experiment_runner_common import OUT, RUBRIC_PAIRS, assert_g_eval_available


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair-id", default="6s-strong")
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--sampling-temperature", type=float, default=0.7)
    args = parser.parse_args()

    pair = next((p for p in RUBRIC_PAIRS if p.pair_id == args.pair_id), None)
    if pair is None:
        raise ValueError(f"unknown pair-id {args.pair_id!r}")

    continuous_client = LLMClient(LLMClientConfig(temperature=0.0))
    assert_g_eval_available(continuous_client, role="geval-variance")
    sampling_client = LLMClient(LLMClientConfig(temperature=args.sampling_temperature))

    continuous_scores = [
        float(_continuous_verify(continuous_client, pair.fact, pair.text) or 0.0)
        for _ in range(args.repeats)
    ]
    sampling_scores = [_sample_discrete_score(sampling_client, pair.fact, pair.text) for _ in range(args.repeats)]

    rows = [
        _row("continuous_verify", continuous_scores),
        _row("temperature_sampling", sampling_scores),
    ]
    for row in rows:
        print(row)

    out_path = OUT / "experiment_geval_variance.csv"
    write_csv_table(out_path, ["method", "mean_score", "std", "n_repeats"], rows)
    print(f"Wrote {out_path}")


def _sample_discrete_score(client: LLMClient, fact: str, text: str) -> float:
    user_message = f"FACT:\n  {fact}\n\nTEXT:\n  {text}"
    try:
        return float(_parse_rubric_output(client.generate(user_message, system=_RUBRIC_SYSTEM_PROMPT)))
    except Exception:
        return 0.0


def _row(method: str, scores: list[float]) -> dict[str, str]:
    return {
        "method": method,
        "mean_score": f"{(sum(scores) / len(scores) if scores else 0.0):.4f}",
        "std": f"{(statistics.pstdev(scores) if len(scores) > 1 else 0.0):.4f}",
        "n_repeats": str(len(scores)),
    }


if __name__ == "__main__":
    main()
