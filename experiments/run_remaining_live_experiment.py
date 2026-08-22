"""Fail-closed launcher for the remaining call-required confirmation run.

The ECC repair loop stays zero-call and gold-free.  This module only invokes an
answer model over already-frozen LongMemEval retrieval snapshots, seals every
prediction, and stops before any reference answer is opened.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cmd_audit.core.state_codec import atomic_json_write, content_sha256
from experiments.run_longmemeval_e2e import _input_cases, _safe_instance_name, predict


SCHEMA_VERSION = "cmd-remaining-live-experiment-v1"
ARMS = ("vanilla", "static", "cmd", "ghost")
DEFAULT_P4C1 = Path("artifacts/experiments/p4c1_real_sources_v1")
DEFAULT_PRIOR = Path("artifacts/experiments/p4c_zero_call_prior_calibration_v1")
DEFAULT_RETRIEVAL = Path("artifacts/experiments/longmemeval_m0_r1_s5_live_ready_v1")
DEFAULT_DATA = Path("data/external/longmemeval/input/longmemeval_s_cleaned.json")
DEFAULT_OUTPUT = Path("artifacts/experiments/remaining_live_confirmation_v1")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unavailable or invalid: {path}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return value


def _redacted_config(path: Path) -> tuple[Mapping[str, object], Mapping[str, object]]:
    config = _object(path, "LLM config")
    allowed = {"base_url", "api_key", "model"}
    if set(config) - allowed:
        raise ValueError("LLM config contains unsupported fields")
    if not all(isinstance(config.get(key), str) and config[key] for key in ("base_url", "api_key", "model")):
        raise ValueError("LLM config requires non-empty base_url, api_key, and model")
    redacted: dict[str, object] = {
        "base_url": config["base_url"],
        "model": config["model"],
        "credential_present": True,
    }
    return config, redacted


def build_plan(*, limit: int, output: Path, run_mode: str) -> dict[str, object]:
    if limit < 1:
        raise ValueError("--limit must be positive; use an explicit finite call budget")
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "plan",
        "stage": "post-ECC LongMemEval live answer confirmation",
        "external_calls_authorized": False,
        "runtime_gold_free": True,
        "router_feedback": "EccRepairReceipt-only",
        "same_trace_answer_replay": False,
        "prediction_then_seal_then_offline_audit": True,
        "arms": list(ARMS),
        "case_limit": limit,
        "planned_answer_calls": limit * len(ARMS),
        "local_live_judge_calls": 0,
        "output": str(output),
        "run_mode": run_mode,
        "claim_scope": (
            "live answer confirmation over frozen retrieval; not P4C repair efficacy, "
            "not runtime reward, and not a sealed score"
        ),
    }


def preflight(
    *,
    p4c1_run: Path,
    prior_run: Path,
    retrieval_run: Path,
    data: Path,
    llm_config: Path,
    limit: int,
    output: Path,
    run_mode: str,
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    plan = build_plan(limit=limit, output=output, run_mode=run_mode)
    p4c1 = _object(p4c1_run / "p4c1_manifest.json", "P4C-1 manifest")
    if not (
        p4c1.get("schema_version") == "cmd-p4c1-real-source-zero-call-v1"
        and p4c1.get("status") == "success"
        and p4c1.get("runtime_uses_gold") is False
        and p4c1.get("runtime_uses_labels") is False
        and p4c1.get("router_feedback") == "EccRepairReceipt"
        and p4c1.get("model_call_count") == 0
    ):
        raise ValueError("P4C-1 live-ABI prerequisite is not ready")
    prior = _object(prior_run / "prior_calibration_manifest.json", "GHOST prior manifest")
    if prior.get("mix_ghost_ready") is not True:
        raise ValueError("mixed-GHOST prior support is not ready")
    retrieval = _object(retrieval_run / "manifest.json", "retrieval manifest")
    if retrieval.get("schema_version") != "cmd-longmemeval-m0-r1-v3":
        raise ValueError("frozen retrieval manifest schema mismatch")
    if not data.is_file():
        raise ValueError(f"LongMemEval data is unavailable: {data}")
    expected_data_root = (p4c1.get("source_roots") or {}).get("longmemeval")
    actual_data_root = _sha256(data)
    if expected_data_root != actual_data_root:
        raise ValueError("LongMemEval data root differs from the P4C-1 frozen source")
    for case in _input_cases(data, limit):
        question_id = case["question_id"]
        artifact_name = f"{_safe_instance_name(question_id)}.json"
        for arm in ARMS:
            artifact = _object(
                retrieval_run / "retrieval" / arm / artifact_name,
                f"{arm} retrieval snapshot for {question_id}",
            )
            if (
                artifact.get("schema_version") != "cmd-longmemeval-retrieval-v1"
                or artifact.get("question_id") != question_id
                or artifact.get("arm") != arm
                or not isinstance(artifact.get("records"), list)
            ):
                raise ValueError(
                    f"{arm} retrieval snapshot identity/schema mismatch for {question_id}"
                )
    config, redacted = _redacted_config(llm_config)
    report = {
        **plan,
        "mode": "preflight",
        "preflight_passed": True,
        "external_calls_authorized": False,
        "roots": {
            "p4c1_manifest_sha256": _sha256(p4c1_run / "p4c1_manifest.json"),
            "prior_manifest_sha256": _sha256(prior_run / "prior_calibration_manifest.json"),
            "retrieval_manifest_sha256": _sha256(retrieval_run / "manifest.json"),
            "data_sha256": actual_data_root,
        },
        "llm_config": redacted,
    }
    return report, config


def execute(args: argparse.Namespace) -> Mapping[str, object]:
    report, config = preflight(
        p4c1_run=args.p4c1_run,
        prior_run=args.prior_run,
        retrieval_run=args.retrieval_run,
        data=args.data,
        llm_config=args.llm_config,
        limit=args.limit,
        output=args.output,
        run_mode=args.run_mode,
    )
    model = str(config["model"])
    seal = predict(
        data=args.data,
        retrieval_run=args.retrieval_run,
        output=args.output,
        answerer_backend="openai-compatible",
        answerer_model=model,
        prompt=args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else (
            "Answer the question using only the retrieved memories. If unsupported, say Unknown."
        ),
        temperature=args.temperature,
        context_budget=args.context_budget,
        resume=args.run_mode == "resume",
        limit=args.limit,
        llm_config=config,
    )
    manifest = {
        **report,
        "mode": "execute",
        "external_calls_authorized": True,
        "status": "prediction_sealed",
        "answerer_model": model,
        "prediction_case_count": seal["prediction_count"],
        "prediction_count": int(seal["prediction_count"]) * len(ARMS),
        "prediction_seal_sha256": _sha256(args.output / "prediction_seal.json"),
        "sealed_score_opened": False,
        "router_updated_from_predictions": False,
    }
    manifest["manifest_sha256"] = content_sha256(manifest, ensure_ascii=False, allow_nan=False)
    atomic_json_write(
        args.output / "remaining_live_manifest.json",
        manifest,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        trailing_newline=True,
    )
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Plan, preflight, or explicitly execute the remaining live answer run. "
            "The prediction seal is written before any offline evaluator can read gold; "
            "that score never feeds GHOST."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="print a zero-call plan (default)")
    mode.add_argument("--preflight", action="store_true", help="validate all roots/config without calls")
    mode.add_argument("--execute", action="store_true", help="authorize answer-model calls")
    parser.add_argument("--llm-config", type=Path, help="JSON with base_url/api_key/model; never sourced")
    parser.add_argument("--p4c1-run", type=Path, default=DEFAULT_P4C1)
    parser.add_argument("--prior-run", type=Path, default=DEFAULT_PRIOR)
    parser.add_argument("--retrieval-run", type=Path, default=DEFAULT_RETRIEVAL)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--run-mode", choices=("fresh", "resume"), default="fresh")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--context-budget", type=int, default=12000)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if (args.preflight or args.execute) and args.llm_config is None:
        parser.error("--llm-config is required for --preflight/--execute")
    try:
        if args.execute:
            result = execute(args)
        elif args.preflight:
            result, _ = preflight(
                p4c1_run=args.p4c1_run,
                prior_run=args.prior_run,
                retrieval_run=args.retrieval_run,
                data=args.data,
                llm_config=args.llm_config,
                limit=args.limit,
                output=args.output,
                run_mode=args.run_mode,
            )
        else:
            result = build_plan(limit=args.limit, output=args.output, run_mode=args.run_mode)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
