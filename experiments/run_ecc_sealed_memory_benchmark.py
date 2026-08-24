#!/usr/bin/env python3
"""Answer LoCoMo/LongMemEval from root-bound ECC committed memory state."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Mapping

from cmd_audit.core.state_codec import atomic_json_write, content_sha256
from cmd_audit.repair.ecc import EccRepairReceipt
from experiments.arena_backends import VLLMDualScoreArenaBackend
from experiments.ecc_answer_causal_contrast import (
    ANSWER_RENDERER_SCHEMA,
    MEMORY_HEADING,
    OPERATOR_SEMANTICS,
    render_causal_state,
)
from experiments.experiment_runner_common import AGENT_SYSTEM_PROMPT
from experiments.locomo_arena import load_locomo_arena_cases
from experiments.longmemeval_arena import load_longmemeval_arena_cases
from experiments.model_context_budget import ModelContextBudget
from experiments.run_sealed_memory_benchmark import DEFAULTS, preflight_openai_endpoint


SEAL_SCHEMA = "cmd-ecc-memory-benchmark-prediction-seal-v2"
RUNTIME_REPORT_SCHEMA = "cmd-ecc-memory-runtime-report-v2"
CAUSAL_STATE_SCHEMA = "cmd-ecc-causal-state-pair-v2"


def _load_runtime(runtime_dir: Path) -> tuple[dict[str, EccRepairReceipt], dict[str, Mapping[str, object]], Mapping[str, object]]:
    runtime_dir = Path(runtime_dir)
    report = json.loads((runtime_dir / "report.json").read_text(encoding="utf-8"))
    if (
        not isinstance(report, Mapping)
        or report.get("schema_version") != RUNTIME_REPORT_SCHEMA
        or report.get("answer_contrast_ready") is not True
    ):
        raise ValueError(
            "ECC answer runner requires a v2 causal-state runtime; rebuild old runtime artifacts"
        )
    report_without_binding = dict(report)
    report_binding = report_without_binding.pop("binding_root", None)
    if report_binding != content_sha256(report_without_binding):
        raise ValueError("ECC runtime report binding root mismatch")
    states_path = runtime_dir / "causal_states.jsonl"
    if hashlib.sha256(states_path.read_bytes()).hexdigest() != report.get("causal_states_sha256"):
        raise ValueError("ECC causal-state export does not match its runtime report")
    states: dict[str, Mapping[str, object]] = {}
    for line in states_path.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        if not isinstance(raw, Mapping) or set(raw) != {
            "schema_version", "case_id", "process_fault_subtype",
            "operator_semantics", "before_root", "before_state", "after_root",
            "after_state", "receipt_sha256",
        } or raw["schema_version"] != CAUSAL_STATE_SCHEMA:
            raise ValueError("ECC causal-state row is not closed or versioned")
        case_id = str(raw["case_id"])
        subtype = str(raw["process_fault_subtype"])
        before, after = raw["before_state"], raw["after_state"]
        if (
            case_id in states
            or subtype not in OPERATOR_SEMANTICS
            or raw["operator_semantics"] != OPERATOR_SEMANTICS.get(subtype)
            or not isinstance(before, Mapping)
            or not isinstance(after, Mapping)
            or raw["before_root"] != content_sha256(dict(before), ensure_ascii=False, allow_nan=False)
            or raw["after_root"] != content_sha256(dict(after), ensure_ascii=False, allow_nan=False)
        ):
            raise ValueError("ECC causal-state row integrity check failed")
        expected_after = deepcopy(dict(before))
        pipeline = expected_after.get("pipeline")
        before_pipeline = before.get("pipeline")
        after_pipeline = after.get("pipeline")
        if (
            not isinstance(pipeline, dict)
            or not isinstance(before_pipeline, Mapping)
            or not isinstance(after_pipeline, Mapping)
            or before_pipeline.get(subtype) is not False
            or any(before_pipeline.get(name) is not True for name in OPERATOR_SEMANTICS if name != subtype)
            or any(after_pipeline.get(name) is not True for name in OPERATOR_SEMANTICS)
        ):
            raise ValueError("ECC causal-state pair does not encode one typed process fault")
        pipeline[subtype] = True
        if expected_after != dict(after):
            raise ValueError("ECC causal-state pair differs outside its preregistered operator")
        states[case_id] = raw
    receipts: dict[str, EccRepairReceipt] = {}
    runtime_run = runtime_dir / "runtime"
    completion_rows = [
        json.loads(line)
        for line in (runtime_run / "case_completions.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    receipt_rows = [
        EccRepairReceipt.from_mapping(json.loads(line))
        for line in (runtime_run / "repair_receipts.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    if len(completion_rows) != len(receipt_rows):
        raise ValueError("ECC runtime receipt/completion coverage differs")
    for completion, receipt in zip(completion_rows, receipt_rows, strict=True):
        case_id = str(completion["case_id"])
        if completion["receipt_sha256"] != receipt.content_hash:
            raise ValueError("ECC completion does not bind its repair receipt")
        state = states.get(case_id)
        if (
            state is None
            or state["before_root"] != receipt.before_root
            or state["after_root"] != receipt.after_root
            or state["receipt_sha256"] != receipt.content_hash
            or receipt.committed is not True
            or receipt.rolled_back is not False
        ):
            raise ValueError("ECC causal state does not bind a committed repair receipt")
        receipts[case_id] = receipt
    if set(receipts) != set(states):
        raise ValueError("ECC receipts and committed states cover different cases")
    return receipts, states, report


def _write_jsonl(path: Path, rows: list[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _generation_parameters(backend: object) -> Mapping[str, object]:
    client = getattr(backend, "answer_client", None)
    config = getattr(client, "config", None)
    if config is None:
        return {"model": "test-double", "temperature": 0.0, "max_tokens": None}
    return {
        "model": str(config.model),
        "temperature": float(config.temperature),
        "max_tokens": int(config.max_tokens),
    }


def validate_ecc_prediction_seal(output: Path) -> Mapping[str, object]:
    """Validate the causal prediction files before any independent scorer opens."""

    output = Path(output)
    seal = json.loads((output / "prediction_seal.json").read_text(encoding="utf-8"))
    if (
        not isinstance(seal, Mapping)
        or seal.get("schema_version") != SEAL_SCHEMA
        or seal.get("sealed") is not True
        or seal.get("official_scoring_status") != "pending-independent-stage"
    ):
        raise ValueError("invalid ECC causal prediction seal")
    actual_roots = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((output / "predictions").glob("*.jsonl"))
    }
    if seal.get("prediction_file_roots") != actual_roots:
        raise ValueError("ECC causal prediction files changed after seal")
    ledger_path = output / "runtime_ledger.jsonl"
    if seal.get("runtime_ledger_sha256") != hashlib.sha256(ledger_path.read_bytes()).hexdigest():
        raise ValueError("ECC causal runtime ledger changed after seal")
    check = dict(seal)
    binding_root = check.pop("binding_root", None)
    if binding_root != content_sha256(check):
        raise ValueError("ECC causal prediction seal binding root mismatch")
    return seal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", choices=tuple(DEFAULTS), required=True)
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=24)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--retrieval-top-k", type=int, default=5)
    parser.add_argument("--candidate-pool-k", type=int, default=10)
    args = parser.parse_args(argv)
    if args.output.exists() and any(path.is_file() for path in args.output.rglob("*")):
        raise ValueError("fresh ECC prediction refuses a non-empty output directory")

    dataset_path = args.cases or DEFAULTS[args.benchmark]
    kwargs = {
        "seed": args.seed,
        "limit": args.limit,
        "retrieval_top_k": args.retrieval_top_k,
        "candidate_pool_k": args.candidate_pool_k,
    }
    cases = (
        load_locomo_arena_cases(dataset_path, include_adversarial=True, **kwargs)
        if args.benchmark == "locomo"
        else load_longmemeval_arena_cases(dataset_path, **kwargs)
    )
    receipts, states, runtime_report = _load_runtime(args.runtime_dir)
    case_ids = {case.case_id for case in cases}
    if set(states) != case_ids:
        raise ValueError(
            "ECC causal runtime and benchmark selection must cover identical case IDs; "
            "use the same --limit/seed/retrieval settings used by materialization"
        )
    backend = VLLMDualScoreArenaBackend(enable_shadow_scoring=False)
    preflight_openai_endpoint(backend)
    budget = ModelContextBudget()
    generation_parameters = _generation_parameters(backend)
    generation_config_sha256 = content_sha256(generation_parameters)
    predictions: dict[str, list[Mapping[str, object]]] = {
        "faulted_before": [],
        "repaired_after": [],
    }
    ledger: list[Mapping[str, object]] = []
    for position, case in enumerate(cases, 1):
        query = str(case.raw["query"])
        state_row = states[case.case_id]
        subtype = str(state_row["process_fault_subtype"])
        before = render_causal_state(
            case=case,
            state=state_row["before_state"],
            state_root=str(state_row["before_root"]),
            process_fault_subtype=subtype,
            query=query,
            budget=budget,
        )
        after = render_causal_state(
            case=case,
            state=state_row["after_state"],
            state_root=str(state_row["after_root"]),
            process_fault_subtype=subtype,
            query=query,
            budget=budget,
        )
        before_answer = backend.answer_context(
            case, before.budgeted.context, purpose="ecc_causal_contrast"
        )
        after_answer = backend.answer_context(
            case, after.budgeted.context, purpose="ecc_causal_contrast"
        )
        predictions["faulted_before"].append(
            {"question_id": case.case_id, "hypothesis": before_answer}
        )
        predictions["repaired_after"].append(
            {"question_id": case.case_id, "hypothesis": after_answer}
        )
        receipt = receipts[case.case_id]
        ledger.append({
            "schema_version": "cmd-ecc-memory-causal-prediction-row-v2",
            "position": position,
            "case_id": case.case_id,
            "runtime_path": "ecc_receipt_causal_contrast",
            "receipt_sha256": receipt.content_hash,
            "committed": receipt.committed,
            "process_fault_subtype": subtype,
            "operator_semantics": before.operator_semantics,
            "before_root": before.state_root,
            "after_root": after.state_root,
            "heading": MEMORY_HEADING,
            "renderer_schema": ANSWER_RENDERER_SCHEMA,
            "system_prompt_sha256": content_sha256(AGENT_SYSTEM_PROMPT),
            "generation_config_sha256": generation_config_sha256,
            "before_source_memory_order": list(before.source_memory_order),
            "after_source_memory_order": list(after.source_memory_order),
            "before_active_memory_order": list(before.active_memory_order),
            "after_active_memory_order": list(after.active_memory_order),
            "before_included_memory_ids": list(before.budgeted.included_ids),
            "after_included_memory_ids": list(after.budgeted.included_ids),
            "before_rendered_items_sha256": before.rendered_items_sha256,
            "after_rendered_items_sha256": after.rendered_items_sha256,
            "before_context_sha256": before.context_sha256,
            "after_context_sha256": after.context_sha256,
            "before_input_tokens": before.budgeted.input_tokens,
            "after_input_tokens": after.budgeted.input_tokens,
            "before_truncated": before.budgeted.truncated,
            "after_truncated": after.budgeted.truncated,
            "counting_mode": budget.counting_mode,
        })
        if position == 1 or position % 10 == 0 or position == len(cases):
            print(f"benchmark={args.benchmark} completed={position}/{len(cases)}", flush=True)

    predictions_dir = args.output / "predictions"
    for arm, rows in predictions.items():
        _write_jsonl(predictions_dir / f"{arm}.jsonl", rows)
    _write_jsonl(args.output / "runtime_ledger.jsonl", ledger)
    roots = {
        prediction_path.name: hashlib.sha256(prediction_path.read_bytes()).hexdigest()
        for prediction_path in sorted(predictions_dir.glob("*.jsonl"))
    }
    seal = {
        "schema_version": SEAL_SCHEMA,
        "benchmark": args.benchmark,
        "case_count": len(cases),
        "ecc_incident_case_count": len(receipts),
        "clean_bypass_case_count": 0,
        "arms": list(predictions),
        "arm_roles": {
            "faulted_before": "receipt-bound pre-repair state",
            "repaired_after": "receipt-bound committed post-repair state",
        },
        "prediction_file_roots": roots,
        "runtime_report_binding_root": runtime_report["binding_root"],
        "runtime_receipt_root": runtime_report["receipt_root"],
        "harness_profile": runtime_report.get("harness_profile", "external-native-telemetry"),
        "benchmark_track": runtime_report.get("benchmark_track", "native-instrumented-runtime"),
        "runtime_ledger_sha256": hashlib.sha256((args.output / "runtime_ledger.jsonl").read_bytes()).hexdigest(),
        "causal_states_sha256": runtime_report["causal_states_sha256"],
        "dataset_sha256": hashlib.sha256(Path(dataset_path).read_bytes()).hexdigest(),
        "case_ids_root": content_sha256([case.case_id for case in cases]),
        "prediction_shape": ["question_id", "hypothesis"],
        "runtime_uses_gold": False,
        "same_trace_answer_replay_for_router": False,
        "answer_renderer": {
            "schema_version": ANSWER_RENDERER_SCHEMA,
            "heading": MEMORY_HEADING,
            "system_prompt_sha256": content_sha256(AGENT_SYSTEM_PROMPT),
            "operator_semantics": dict(OPERATOR_SEMANTICS),
        },
        "generation_parameters": dict(generation_parameters),
        "generation_config_sha256": generation_config_sha256,
        "prompt_budget": {
            "max_model_len": budget.max_model_len,
            "max_output_tokens": budget.max_output_tokens,
            "reserve_tokens": budget.reserve_tokens,
            "max_input_tokens": budget.max_input_tokens,
            "counting_mode": budget.counting_mode,
        },
        "official_scoring_status": "pending-independent-stage",
        "sealed": True,
    }
    if runtime_report.get("benchmark_track") == "controlled-structural-stress-not-native-official":
        seal["reporting_warning"] = (
            "Controlled structural stress predictions are not the native official benchmark arm."
        )
    seal["binding_root"] = content_sha256(seal)
    atomic_json_write(
        args.output / "prediction_seal.json",
        seal,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        trailing_newline=True,
    )
    print(json.dumps(seal, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
