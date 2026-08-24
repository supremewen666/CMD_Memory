#!/usr/bin/env python3
"""Seal a zero-model-call process-fault renderer/token smoke run."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Mapping

from cmd_audit.core.state_codec import atomic_json_write, content_sha256
from experiments.ecc_answer_causal_contrast import (
    ANSWER_RENDERER_SCHEMA,
    OPERATOR_SEMANTICS,
    render_causal_state,
)
from experiments.locomo_arena import load_locomo_arena_cases
from experiments.longmemeval_arena import load_longmemeval_arena_cases
from experiments.model_context_budget import ModelContextBudget
from experiments.run_ecc_sealed_memory_benchmark import (
    _load_runtime,
    _validate_token_ledger_pair,
)
from experiments.run_sealed_memory_benchmark import DEFAULTS


SEAL_SCHEMA = "cmd-ecc-process-fault-token-smoke-seal-v1"
ROW_SCHEMA = "cmd-ecc-process-fault-token-smoke-row-v1"


def _write_jsonl(path: Path, rows: list[Mapping[str, object]]) -> str:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_process_fault_token_smoke(output: Path) -> Mapping[str, object]:
    output = Path(output)
    seal = json.loads((output / "token_smoke_seal.json").read_text(encoding="utf-8"))
    if (
        not isinstance(seal, Mapping)
        or seal.get("schema_version") != SEAL_SCHEMA
        or seal.get("sealed") is not True
        or seal.get("status") != "passed"
        or seal.get("mechanism") != "process_fault"
    ):
        raise ValueError("invalid process-fault token smoke seal")
    check = dict(seal)
    binding_root = check.pop("binding_root", None)
    if binding_root != content_sha256(check):
        raise ValueError("process-fault token smoke binding root mismatch")
    ledger_path = output / "token_ledger.jsonl"
    if seal.get("token_ledger_sha256") != hashlib.sha256(ledger_path.read_bytes()).hexdigest():
        raise ValueError("process-fault token smoke ledger changed after seal")
    counts = {name: 0 for name in OPERATOR_SEMANTICS}
    expected = {
        "schema_version", "position", "case_id", "process_fault_subtype",
        "operator_semantics", "before_root", "after_root",
        "before_included_memory_ids", "after_included_memory_ids",
        "before_context_sha256", "after_context_sha256", "before_input_tokens",
        "after_input_tokens", "before_truncated", "after_truncated", "counting_mode",
    }
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if not isinstance(row, Mapping) or set(row) != expected or row.get("schema_version") != ROW_SCHEMA:
            raise ValueError("process-fault token smoke row is not closed")
        subtype = str(row["process_fault_subtype"])
        if (
            subtype not in counts
            or row["operator_semantics"] != OPERATOR_SEMANTICS[subtype]
            or not isinstance(row["before_input_tokens"], int)
            or not isinstance(row["after_input_tokens"], int)
            or row["before_input_tokens"] <= 2
            or row["after_input_tokens"] <= 2
            or row["before_input_tokens"] > seal["max_input_tokens"]
            or row["after_input_tokens"] > seal["max_input_tokens"]
            or row["counting_mode"] != seal["counting_mode"]
        ):
            raise ValueError("process-fault token smoke row failed token checks")
        if subtype == "retrieval" and (
            row["before_included_memory_ids"]
            or not row["after_included_memory_ids"]
            or row["after_input_tokens"] <= row["before_input_tokens"]
        ):
            raise ValueError("retrieval token smoke does not restore memory context")
        counts[subtype] += 1
    if counts != seal.get("subtype_counts") or sum(counts.values()) != seal.get("case_count"):
        raise ValueError("process-fault token smoke coverage mismatch")
    return seal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", choices=tuple(DEFAULTS), required=True)
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=24)
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--retrieval-top-k", type=int, default=5)
    parser.add_argument("--candidate-pool-k", type=int, default=10)
    args = parser.parse_args(argv)
    if args.limit < 1:
        raise ValueError("token smoke requires a positive small case limit")
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("fresh token smoke refuses a non-empty output directory")
    args.output.mkdir(parents=True, exist_ok=True)

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
    _receipts, states, report = _load_runtime(args.runtime_dir)
    if report.get("mechanism") != "process_fault":
        raise ValueError("token smoke accepts only an isolated process_fault runtime")
    if set(states) != {case.case_id for case in cases}:
        raise ValueError("token smoke cases and runtime coverage differ")

    budget = ModelContextBudget()
    rows: list[Mapping[str, object]] = []
    subtype_counts = {name: 0 for name in OPERATOR_SEMANTICS}
    for position, case in enumerate(cases, 1):
        state_row = states[case.case_id]
        subtype = str(state_row["process_fault_subtype"])
        before = render_causal_state(
            case=case,
            state=state_row["before_state"],
            state_root=str(state_row["before_root"]),
            process_fault_subtype=subtype,
            mechanism="process_fault",
            memory_text_overrides=state_row["memory_text_overrides"],
            query=str(case.raw["query"]),
            budget=budget,
        )
        after = render_causal_state(
            case=case,
            state=state_row["after_state"],
            state_root=str(state_row["after_root"]),
            process_fault_subtype=subtype,
            mechanism="process_fault",
            memory_text_overrides=state_row["memory_text_overrides"],
            query=str(case.raw["query"]),
            budget=budget,
        )
        _validate_token_ledger_pair(
            before=before,
            after=after,
            mechanism="process_fault",
            process_fault_subtype=subtype,
            max_input_tokens=budget.max_input_tokens,
        )
        subtype_counts[subtype] += 1
        rows.append({
            "schema_version": ROW_SCHEMA,
            "position": position,
            "case_id": case.case_id,
            "process_fault_subtype": subtype,
            "operator_semantics": before.operator_semantics,
            "before_root": before.state_root,
            "after_root": after.state_root,
            "before_included_memory_ids": list(before.budgeted.included_ids),
            "after_included_memory_ids": list(after.budgeted.included_ids),
            "before_context_sha256": before.context_sha256,
            "after_context_sha256": after.context_sha256,
            "before_input_tokens": before.budgeted.input_tokens,
            "after_input_tokens": after.budgeted.input_tokens,
            "before_truncated": before.budgeted.truncated,
            "after_truncated": after.budgeted.truncated,
            "counting_mode": budget.counting_mode,
        })
    if any(count < 1 for count in subtype_counts.values()):
        raise ValueError("process-fault token smoke must cover all four subtypes")

    ledger_path = args.output / "token_ledger.jsonl"
    ledger_root = _write_jsonl(ledger_path, rows)
    seal: dict[str, object] = {
        "schema_version": SEAL_SCHEMA,
        "sealed": True,
        "status": "passed",
        "model_calls": 0,
        "benchmark": args.benchmark,
        "case_count": len(cases),
        "mechanism": "process_fault",
        "subtype_counts": subtype_counts,
        "renderer_schema": ANSWER_RENDERER_SCHEMA,
        "runtime_report_binding_root": report["binding_root"],
        "causal_states_sha256": report["causal_states_sha256"],
        "token_ledger_sha256": ledger_root,
        "dataset_sha256": hashlib.sha256(Path(dataset_path).read_bytes()).hexdigest(),
        "counting_mode": budget.counting_mode,
        "max_input_tokens": budget.max_input_tokens,
        "seal_scope": "renderer-and-token-ledger-smoke-not-answer-predictions",
    }
    seal["binding_root"] = content_sha256(seal)
    atomic_json_write(
        args.output / "token_smoke_seal.json",
        seal,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        trailing_newline=True,
    )
    validate_process_fault_token_smoke(args.output)
    print(json.dumps(seal, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
