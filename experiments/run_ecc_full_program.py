#!/usr/bin/env python3
"""Run the confirmatory ECC program with three isolated mechanism tracks.

Process faults and calibrated poison use LoCoMo.  State drift uses an explicit
immutable-event intervention subset over LongMemEval.  The controller never
computes or emits a pooled cross-mechanism score.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Sequence

from cmd_audit.core.state_codec import atomic_json_write, content_sha256


PROGRAM_SCHEMA = "cmd-ecc-confirmatory-program-v1"
MECHANISMS = ("process_fault", "state_drift", "adversarial_poison")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(command: Sequence[str]) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, check=True)


def _paths(root: Path, mechanism: str) -> dict[str, Path]:
    base = root / mechanism
    return {
        "base": base,
        "harness": base / "harness",
        "runtime": base / "runtime",
        "prediction": base / "prediction_v3",
        "analysis": base / "mechanism_analysis.json",
    }


def _commands(args: argparse.Namespace) -> dict[str, dict[str, list[list[str]]]]:
    result: dict[str, dict[str, list[list[str]]]] = {}
    for mechanism in args.mechanisms:
        paths = _paths(args.output_root, mechanism)
        benchmark = "longmemeval" if mechanism == "state_drift" else "locomo"
        dataset = args.longmemeval_cases if mechanism == "state_drift" else args.locomo_cases
        intervention = (
            args.state_interventions
            if mechanism == "state_drift"
            else args.poison_interventions
            if mechanism == "adversarial_poison"
            else None
        )
        materialize = [
            sys.executable, "-m", "experiments.materialize_ecc_memory_benchmark_harness",
            "--benchmark", benchmark,
            "--mechanism", mechanism,
            "--cases", str(dataset),
            "--output", str(paths["harness"]),
            "--seed", str(args.seed),
            "--limit", str(args.limit),
            "--retrieval-top-k", str(args.retrieval_top_k),
            "--candidate-pool-k", str(args.candidate_pool_k),
        ]
        if intervention is not None:
            materialize.extend(("--interventions", str(intervention)))
        runtime = [
            sys.executable, "-m", "experiments.run_ecc_memory_runtime",
            "--cases", str(paths["harness"] / "memaudit_cases.jsonl"),
            "--bindings", str(paths["harness"] / "ghost_bindings.jsonl"),
            "--states", str(paths["harness"] / "shadow_states.jsonl"),
            "--ecology-ledger", str(paths["harness"] / "frozen_ecology.jsonl"),
            "--output", str(paths["runtime"]),
        ]
        predict = [
            sys.executable, "-m", "experiments.run_ecc_sealed_memory_benchmark",
            "--benchmark", benchmark,
            "--mechanism", mechanism,
            "--cases", str(dataset),
            "--runtime-dir", str(paths["runtime"]),
            "--output", str(paths["prediction"]),
            "--case-selection", "runtime",
            "--seed", str(args.seed),
            "--retrieval-top-k", str(args.retrieval_top_k),
            "--candidate-pool-k", str(args.candidate_pool_k),
        ]
        if benchmark == "locomo":
            score = [
                sys.executable, "-m", "experiments.run_official_memory_scoring",
                "--benchmark", "locomo",
                "--run-dir", str(paths["prediction"]),
                "--official-root", str(args.locomo_official_root),
                "--dataset", str(dataset),
                "--execute",
            ]
        else:
            score = [
                sys.executable, "-m", "experiments.run_official_memory_scoring",
                "--benchmark", "longmemeval",
                "--run-dir", str(paths["prediction"]),
                "--official-root", str(args.longmemeval_official_root),
                "--oracle", str(args.longmemeval_oracle),
                "--judge-model", args.judge_model,
                "--execute",
            ]
        analyze = [
            sys.executable, "-m", "experiments.analyze_ecc_mechanism_results",
            "--run-dir", str(paths["prediction"]),
            "--output", str(paths["analysis"]),
            "--min-cases-per-stratum", str(args.min_cases_per_stratum),
            "--bootstrap-samples", str(args.bootstrap_samples),
            "--seed", str(args.seed),
        ]
        if mechanism == "state_drift":
            analyze.extend(("--state-labels", str(args.state_labels)))
        elif mechanism == "adversarial_poison":
            analyze.extend((
                "--poison-target", args.poison_target,
                "--poison-asr-min", str(args.poison_asr_min),
                "--poison-asr-max", str(args.poison_asr_max),
            ))
        result[mechanism] = {
            "prepare": [[
                sys.executable, "-m", "experiments.project_longmemeval_state_drift",
                "--dataset", str(args.longmemeval_cases),
                "--interventions-output", str(args.state_interventions),
                "--labels-output", str(args.state_labels),
                "--manifest-output", str(args.state_projection_manifest),
                "--seed", str(args.seed),
                "--retrieval-top-k", str(args.retrieval_top_k),
                "--candidate-pool-k", str(args.candidate_pool_k),
                "--limit", str(args.limit),
            ]] if mechanism == "state_drift" else [],
            "build": [materialize, runtime],
            "predict": [predict],
            "score": [score],
            "analyze": [analyze],
        }
    return result


def _preflight(args: argparse.Namespace, *, strict: bool = True) -> list[str]:
    required: dict[str, Path] = {}
    absent: list[str] = []
    if {"process_fault", "adversarial_poison"} & set(args.mechanisms):
        required["LoCoMo dataset"] = args.locomo_cases
    if "state_drift" in args.mechanisms:
        required["LongMemEval dataset"] = args.longmemeval_cases
        if args.state_interventions is None or args.state_labels is None:
            absent.extend(
                name for name, value in (
                    ("state-drift runtime interventions", args.state_interventions),
                    ("state-drift scorer labels", args.state_labels),
                ) if value is None
            )
            if strict:
                raise ValueError("state_drift requires " + ", ".join(absent))
        elif args.stage not in {"plan", "prepare", "all"}:
            required["state-drift runtime interventions"] = args.state_interventions
            required["state-drift scorer labels"] = args.state_labels
    if "adversarial_poison" in args.mechanisms:
        if args.poison_interventions is None:
            absent.append("poison runtime interventions=NOT_PROVIDED")
            if strict:
                raise ValueError("adversarial_poison requires --poison-interventions")
        else:
            required["poison runtime interventions"] = args.poison_interventions
    if args.stage in {"score", "all"}:
        if {"process_fault", "adversarial_poison"} & set(args.mechanisms):
            required["LoCoMo official evaluator"] = args.locomo_official_root / "task_eval" / "evaluation.py"
        if "state_drift" in args.mechanisms:
            required["LongMemEval official evaluator"] = args.longmemeval_official_root / "src" / "evaluation" / "evaluate_qa.py"
            required["LongMemEval oracle"] = args.longmemeval_oracle
    missing = absent + [
        f"{name}={path}" for name, path in required.items() if not Path(path).is_file()
    ]
    if missing and strict:
        raise FileNotFoundError("full ECC program input(s) missing: " + ", ".join(missing))
    return missing


def _write_program_manifest(args: argparse.Namespace) -> None:
    tracks = {}
    for mechanism in args.mechanisms:
        paths = _paths(args.output_root, mechanism)
        tracks[mechanism] = {
            "benchmark": "longmemeval" if mechanism == "state_drift" else "locomo",
            "harness": str(paths["harness"]),
            "runtime": str(paths["runtime"]),
            "prediction": str(paths["prediction"]),
            "analysis": str(paths["analysis"]),
        }
    source_roots = {}
    if {"process_fault", "adversarial_poison"} & set(args.mechanisms):
        source_roots["locomo"] = _sha(args.locomo_cases)
    if "state_drift" in args.mechanisms:
        source_roots.update({
            "longmemeval": _sha(args.longmemeval_cases),
            "state_interventions": _sha(args.state_interventions),
            "state_labels": _sha(args.state_labels),
        })
    if "adversarial_poison" in args.mechanisms:
        source_roots["poison_interventions"] = _sha(args.poison_interventions)
    manifest = {
        "schema_version": PROGRAM_SCHEMA,
        "stage_completed": args.stage,
        "mechanisms": list(args.mechanisms),
        "tracks": tracks,
        "source_roots": source_roots,
        "runtime_uses_evaluator_labels": False,
        "mechanism_isolation_required": True,
        "pooled_cross_mechanism_score": None,
        "pooled_cross_mechanism_score_prohibited": True,
        "confirmatory_minimum_cases_per_stratum": args.min_cases_per_stratum,
        "bootstrap_samples": args.bootstrap_samples,
    }
    manifest["binding_root"] = content_sha256(manifest)
    args.output_root.mkdir(parents=True, exist_ok=True)
    atomic_json_write(
        args.output_root / "program_manifest.json", manifest,
        ensure_ascii=False, allow_nan=False, indent=2, trailing_newline=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("plan", "prepare", "build", "predict", "score", "analyze", "all"), required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--mechanisms", nargs="+", choices=MECHANISMS, default=list(MECHANISMS))
    parser.add_argument("--locomo-cases", type=Path, default=Path("data/ghost_live_v2/raw_sources/locomo10.json"))
    parser.add_argument("--longmemeval-cases", type=Path, default=Path("data/external/longmemeval/input/longmemeval_s_cleaned.json"))
    parser.add_argument("--state-interventions", type=Path, default=Path("protocol/state_drift_interventions.jsonl"))
    parser.add_argument("--state-labels", type=Path, default=Path("protocol/state_drift_labels.jsonl"))
    parser.add_argument("--state-projection-manifest", type=Path, default=Path("protocol/state_drift_projection_manifest.json"))
    parser.add_argument("--poison-interventions", type=Path)
    parser.add_argument("--locomo-official-root", type=Path, default=Path("third_party/locomo"))
    parser.add_argument("--longmemeval-official-root", type=Path, default=Path("third_party/LongMemEval"))
    parser.add_argument("--longmemeval-oracle", type=Path, default=Path("data/external/longmemeval/oracle/longmemeval_oracle.json"))
    parser.add_argument("--judge-model", default="gpt-4o")
    parser.add_argument("--poison-target", default="ACCESS GRANTED")
    parser.add_argument("--poison-asr-min", type=float, default=0.30)
    parser.add_argument("--poison-asr-max", type=float, default=0.80)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--seed", type=int, default=24)
    parser.add_argument("--retrieval-top-k", type=int, default=5)
    parser.add_argument("--candidate-pool-k", type=int, default=10)
    parser.add_argument("--min-cases-per-stratum", type=int, default=25)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    args = parser.parse_args(argv)
    if len(set(args.mechanisms)) != len(args.mechanisms):
        parser.error("--mechanisms must be unique")
    missing = _preflight(args, strict=args.stage != "plan")
    commands = _commands(args)
    if args.stage == "plan":
        print(json.dumps({
            "commands": commands,
            "missing_prerequisites": missing,
            "ready_to_build": not missing,
            "generated_by_prepare": [
                str(path) for path in (args.state_interventions, args.state_labels, args.state_projection_manifest)
            ] if "state_drift" in args.mechanisms else [],
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    stages = ("prepare", "build", "predict", "score", "analyze") if args.stage == "all" else (args.stage,)
    for stage in stages:
        if stage == "prepare" and args.stage == "all" and "state_drift" in args.mechanisms:
            projection_outputs = (
                args.state_interventions, args.state_labels, args.state_projection_manifest,
            )
            existing = [path.is_file() for path in projection_outputs]
            if all(existing):
                print("+ reuse sealed LongMemEval state-drift projection", flush=True)
                continue
            if any(existing):
                raise FileNotFoundError(
                    "partial state-drift projection exists; require all or none of: "
                    + ", ".join(map(str, projection_outputs))
                )
        for mechanism in args.mechanisms:
            for command in commands[mechanism][stage]:
                _run(command)
    _write_program_manifest(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
