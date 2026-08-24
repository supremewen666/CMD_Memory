#!/usr/bin/env python3
"""Thin adapter to Evo-Bench's canonical formal CLI; no local surrogate score."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def build_command(
    *,
    stage: str,
    root: Path,
    policy_config: Path,
    judge_config: Path,
    evolver_config: Path | None = None,
    frozen_harness: Path | None = None,
    output_dir: Path | None = None,
) -> list[str]:
    root = Path(root)
    validation = root / "benchmark" / "suites" / "evobench_validation.json"
    evaluation = root / "benchmark" / "suites" / "evobench_evaluation.json"
    common = [
        "--policy-model-config", str(policy_config),
        "--judge-model-config", str(judge_config),
    ]
    if stage == "seed-validation":
        return [sys.executable, "-m", "evobench", "run-validation-eval",
                "--suite", str(validation), "--policy-harness", str(root / "policy_harness_seed"),
                *common, "--output-dir", str(output_dir or root / "validation_evals" / "seed"),
                "--trials-by-domain", "general=3", "--rollout-concurrency", "20"]
    if stage == "evolve":
        if evolver_config is None:
            raise ValueError("evolve requires evolver_config")
        return [sys.executable, "-m", "evobench", "run-evolve",
                "--suite", str(validation), "--evaluation-suite", str(evaluation),
                "--seed-policy-harness", str(root / "policy_harness_seed"), *common,
                "--evolver-model-config", str(evolver_config),
                "--max-iterations", "20", "--max-steps", "1000",
                "--sandbox-ttl-minutes", "2880", "--trials-by-domain", "general=3",
                "--rollout-concurrency", "20", "--evaluation-concurrency", "20",
                "--evaluation-output-dir", str(output_dir or root / "evaluations" / "formal")]
    if stage == "evaluation":
        if frozen_harness is None:
            raise ValueError("evaluation requires frozen_harness")
        return [sys.executable, "-m", "evobench", "run-evaluation",
                "--suite", str(evaluation), "--frozen-harness", str(frozen_harness),
                *common, "--output-dir", str(output_dir or root / "evaluations" / "frozen"),
                "--trials-by-domain", "general=3", "--rollout-concurrency", "20"]
    if stage == "release-test":
        return [sys.executable, "-m", "unittest", "discover", "-v"]
    raise ValueError("unknown Evo-Bench stage")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("seed-validation", "evolve", "evaluation", "release-test"), required=True)
    parser.add_argument("--official-root", type=Path, required=True)
    parser.add_argument("--policy-config", type=Path, required=True)
    parser.add_argument("--judge-config", type=Path, required=True)
    parser.add_argument("--evolver-config", type=Path)
    parser.add_argument("--frozen-harness", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    command = build_command(
        stage=args.stage,
        root=args.official_root,
        policy_config=args.policy_config,
        judge_config=args.judge_config,
        evolver_config=args.evolver_config,
        frozen_harness=args.frozen_harness,
        output_dir=args.output_dir,
    )
    plan = {
        "official": True,
        "stage": args.stage,
        "command": command,
        "cwd": str(args.official_root),
        "local_governance_runner_is_score": False,
    }
    if not args.execute:
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
        return 0
    if args.stage != "release-test" and os.environ.get("EVOBENCH_EXECUTION_MODE") != "e2b":
        raise RuntimeError("formal Evo-Bench execution requires EVOBENCH_EXECUTION_MODE=e2b")
    completed = subprocess.run(command, cwd=args.official_root, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
