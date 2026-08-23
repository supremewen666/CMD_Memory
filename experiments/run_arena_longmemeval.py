#!/usr/bin/env python3
"""Run LongMemEval with real BM25 retrieval and the live CMD model arena."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from experiments.arena_cli import run_arena_cli
from experiments.longmemeval_arena import load_longmemeval_arena_cases


def _configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--retrieval-top-k",
        type=int,
        default=5,
        help="BM25 control context size after indexing every haystack session.",
    )
    parser.add_argument(
        "--candidate-pool-k",
        type=int,
        default=10,
        help="Bounded BM25 prefix available to CMD repair operators.",
    )


def _loader_kwargs(args: argparse.Namespace) -> dict[str, object]:
    return {
        "retrieval_top_k": args.retrieval_top_k,
        "candidate_pool_k": args.candidate_pool_k,
    }


def main() -> int:
    return run_arena_cli(
        arena_id="longmemeval",
        loader=load_longmemeval_arena_cases,
        default_cases=(
            "data/external/longmemeval/input/longmemeval_s_cleaned.json"
        ),
        default_output="artifacts/arena/longmemeval_live_observations.jsonl",
        chains_default=False,
        best_of_n_default=True,
        context_stuffing_default=True,
        configure_parser=_configure_parser,
        loader_kwargs_factory=_loader_kwargs,
    )


if __name__ == "__main__":
    raise SystemExit(main())
