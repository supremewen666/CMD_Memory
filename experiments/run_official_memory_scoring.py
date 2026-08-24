#!/usr/bin/env python3
"""Score a sealed memory run with the benchmark authors' evaluator code."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.official_memory_eval import (
    longmemeval_commands,
    run_longmemeval_official,
    score_locomo_official,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", choices=("locomo", "longmemeval"), required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--official-root", type=Path, required=True)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--oracle", type=Path)
    parser.add_argument("--judge-model", default="gpt-4o")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.benchmark == "locomo":
        if args.dataset is None:
            parser.error("--dataset is required for LoCoMo")
        if not args.execute:
            print(json.dumps({
                "benchmark": "locomo",
                "official_entrypoint": str(args.official_root / "task_eval" / "evaluation.py"),
                "action": "import eval_question_answering after validating seal",
            }, sort_keys=True))
            return 0
        result = score_locomo_official(run_dir=args.run_dir, dataset=args.dataset, official_root=args.official_root)
    else:
        if args.oracle is None:
            parser.error("--oracle is required for LongMemEval")
        if args.execute:
            result = run_longmemeval_official(
                run_dir=args.run_dir,
                official_root=args.official_root,
                oracle=args.oracle,
                judge_model=args.judge_model,
            )
        else:
            result = {"commands": longmemeval_commands(
                run_dir=args.run_dir,
                official_root=args.official_root,
                oracle=args.oracle,
                judge_model=args.judge_model,
            )}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
