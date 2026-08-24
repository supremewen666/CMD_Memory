#!/usr/bin/env python3
"""Run all official LoCoMo QA categories in the live CMD model arena."""
from __future__ import annotations

import argparse

from experiments.arena_cli import run_arena_cli
from experiments.locomo_arena import load_locomo_arena_cases


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--retrieval-top-k", type=int, default=5)
    parser.add_argument("--candidate-pool-k", type=int, default=10)
    parser.add_argument(
        "--include-adversarial",
        action=argparse.BooleanOptionalAction,
        default=True,
    )


def _kwargs(args: argparse.Namespace) -> dict[str, object]:
    return {
        "retrieval_top_k": args.retrieval_top_k,
        "candidate_pool_k": args.candidate_pool_k,
        "include_adversarial": args.include_adversarial,
    }


def main() -> int:
    return run_arena_cli(
        arena_id="locomo",
        loader=load_locomo_arena_cases,
        default_cases="data/ghost_live_v2/raw_sources/locomo10.json",
        default_output="artifacts/arena/locomo_live_observations.jsonl",
        chains_default=False,
        best_of_n_default=True,
        context_stuffing_default=True,
        configure_parser=_configure,
        loader_kwargs_factory=_kwargs,
    )


if __name__ == "__main__":
    raise SystemExit(main())
