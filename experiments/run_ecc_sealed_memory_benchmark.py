#!/usr/bin/env python3
"""Answer LoCoMo/LongMemEval from root-bound ECC committed memory state."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Mapping

from cmd_audit.core.state_codec import atomic_json_write, content_sha256
from cmd_audit.repair.ecc import EccRepairReceipt
from experiments.arena_backends import VLLMDualScoreArenaBackend
from experiments.experiment_runner_common import AGENT_SYSTEM_PROMPT
from experiments.locomo_arena import load_locomo_arena_cases
from experiments.longmemeval_arena import load_longmemeval_arena_cases
from experiments.model_context_budget import ModelContextBudget
from experiments.run_sealed_memory_benchmark import DEFAULTS, preflight_openai_endpoint


SEAL_SCHEMA = "cmd-ecc-memory-benchmark-prediction-seal-v1"


def _load_runtime(runtime_dir: Path) -> tuple[dict[str, EccRepairReceipt], dict[str, Mapping[str, object]], Mapping[str, object]]:
    runtime_dir = Path(runtime_dir)
    report = json.loads((runtime_dir / "report.json").read_text(encoding="utf-8"))
    states_path = runtime_dir / "committed_states.jsonl"
    if hashlib.sha256(states_path.read_bytes()).hexdigest() != report.get("committed_states_sha256"):
        raise ValueError("ECC committed-state export does not match its runtime report")
    states: dict[str, Mapping[str, object]] = {}
    for line in states_path.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        if not isinstance(raw, Mapping) or set(raw) != {
            "schema_version", "case_id", "state_root", "state"
        } or raw["schema_version"] != "cmd-ecc-committed-state-v1":
            raise ValueError("ECC committed-state row is not closed or versioned")
        states[str(raw["case_id"])] = raw
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
        if state is None or state["state_root"] != receipt.after_root:
            raise ValueError("ECC committed state does not bind receipt after_root")
        receipts[case_id] = receipt
    if set(receipts) != set(states):
        raise ValueError("ECC receipts and committed states cover different cases")
    return receipts, states, report


def _memory_items(case: object, ids: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    raw = getattr(case, "raw")
    available = {
        str(item["memory_id"]): str(item["text"])
        for item in raw.get("extracted_memory", ())
        if isinstance(item, Mapping) and item.get("memory_id") and item.get("text")
    }
    unknown = set(ids) - set(available)
    if unknown:
        raise ValueError(
            f"ECC state references memory IDs absent from benchmark runtime view: {sorted(unknown)!r}"
        )
    return tuple((memory_id, available[memory_id]) for memory_id in ids)


def _baseline_ids(case: object) -> tuple[str, ...]:
    raw = getattr(case, "raw")
    baselines = raw.get("baseline_outputs") or ()
    baseline = baselines[0] if baselines else {}
    return tuple(str(row) for row in baseline.get("retrieved_memory_ids", ()))


def _active_ids(case: object, state_row: Mapping[str, object]) -> tuple[str, ...]:
    state = state_row["state"]
    if not isinstance(state, Mapping):
        raise ValueError("ECC committed state payload is invalid")
    pipeline = state.get("pipeline")
    memories = state.get("memories")
    if not isinstance(pipeline, Mapping) or not isinstance(memories, Mapping):
        raise ValueError("ECC committed memory/pipeline state is invalid")
    if pipeline.get("retrieval") is not True or pipeline.get("injection") is not True:
        return ()
    available_order = [memory_id for memory_id, _text in _memory_items(case, tuple(memories))]
    return tuple(
        memory_id
        for memory_id in available_order
        if isinstance(memories[memory_id], Mapping)
        and memories[memory_id].get("active") is True
        and memory_id not in set(state.get("quarantine") or ())
    )


def _write_jsonl(path: Path, rows: list[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


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
    backend = VLLMDualScoreArenaBackend(enable_shadow_scoring=False)
    preflight_openai_endpoint(backend)
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
    if set(states) - case_ids:
        raise ValueError("ECC runtime contains cases absent from this benchmark stream")
    budget = ModelContextBudget()
    predictions: dict[str, list[Mapping[str, object]]] = {"bm25": [], "cmd_ecc": []}
    ledger: list[Mapping[str, object]] = []
    for position, case in enumerate(cases, 1):
        query = str(case.raw["query"])
        baseline = budget.fit_memory_items(
            query=query,
            items=_memory_items(case, _baseline_ids(case)),
            system=AGENT_SYSTEM_PROMPT,
            heading="BM25 retrieved memory",
        )
        state_row = states.get(case.case_id)
        if state_row is None:
            ecc = baseline
        else:
            ecc = budget.fit_memory_items(
                query=query,
                items=_memory_items(case, _active_ids(case, state_row)),
                system=AGENT_SYSTEM_PROMPT,
                heading="ECC committed active memory",
            )
        baseline_answer = backend.answer_context(case, baseline.context, purpose="bm25_control")
        ecc_answer = (
            baseline_answer
            if state_row is None
            else backend.answer_context(case, ecc.context, purpose="ecc_committed_state")
        )
        predictions["bm25"].append({"question_id": case.case_id, "hypothesis": baseline_answer})
        predictions["cmd_ecc"].append({"question_id": case.case_id, "hypothesis": ecc_answer})
        receipt = receipts.get(case.case_id)
        ledger.append({
            "schema_version": "cmd-ecc-memory-prediction-row-v1",
            "position": position,
            "case_id": case.case_id,
            "runtime_path": "ecc_receipt" if receipt is not None else "clean_bypass",
            "receipt_sha256": None if receipt is None else receipt.content_hash,
            "committed": None if receipt is None else receipt.committed,
            "baseline_memory_ids": list(baseline.included_ids),
            "ecc_memory_ids": list(ecc.included_ids),
            "baseline_input_tokens": baseline.input_tokens,
            "ecc_input_tokens": ecc.input_tokens,
            "baseline_truncated": baseline.truncated,
            "ecc_truncated": ecc.truncated,
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
        "clean_bypass_case_count": len(cases) - len(receipts),
        "arms": list(predictions),
        "prediction_file_roots": roots,
        "runtime_report_binding_root": runtime_report["binding_root"],
        "runtime_receipt_root": runtime_report["receipt_root"],
        "runtime_ledger_sha256": hashlib.sha256((args.output / "runtime_ledger.jsonl").read_bytes()).hexdigest(),
        "dataset_sha256": hashlib.sha256(Path(dataset_path).read_bytes()).hexdigest(),
        "runtime_uses_gold": False,
        "same_trace_answer_replay_for_router": False,
        "prompt_budget": {
            "max_model_len": budget.max_model_len,
            "max_output_tokens": budget.max_output_tokens,
            "reserve_tokens": budget.reserve_tokens,
            "max_input_tokens": budget.max_input_tokens,
            "counting_mode": budget.counting_mode,
        },
        "sealed": True,
    }
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
