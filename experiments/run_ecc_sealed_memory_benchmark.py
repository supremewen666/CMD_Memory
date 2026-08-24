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
    REPAIR_SEMANTICS,
    render_causal_state,
)
from experiments.experiment_runner_common import AGENT_SYSTEM_PROMPT
from experiments.locomo_arena import load_locomo_arena_cases
from experiments.longmemeval_arena import load_longmemeval_arena_cases
from experiments.model_context_budget import ModelContextBudget
from experiments.run_sealed_memory_benchmark import DEFAULTS, preflight_openai_endpoint


SEAL_SCHEMA = "cmd-ecc-memory-benchmark-prediction-seal-v3"
RUNTIME_REPORT_SCHEMA = "cmd-ecc-memory-runtime-report-v3"
CAUSAL_STATE_SCHEMA = "cmd-ecc-causal-state-pair-v3"
LEGACY_SEAL_SCHEMA = "cmd-ecc-memory-benchmark-prediction-seal-v2"
LEGACY_RUNTIME_REPORT_SCHEMA = "cmd-ecc-memory-runtime-report-v2"
LEGACY_CAUSAL_STATE_SCHEMA = "cmd-ecc-causal-state-pair-v2"


def _validate_causal_transition(raw: Mapping[str, object]) -> None:
    mechanism = str(raw["mechanism"])
    subtype = raw["process_fault_subtype"]
    before = raw["before_state"]
    after = raw["after_state"]
    overrides = raw["memory_text_overrides"]
    if not isinstance(overrides, Mapping) or any(
        not isinstance(memory_id, str) or not isinstance(text, str)
        for memory_id, text in overrides.items()
    ):
        raise ValueError("ECC causal-state memory text overrides are invalid")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        raise ValueError("ECC causal-state pair requires mapping states")
    expected_after = deepcopy(dict(before))
    memories = expected_after.get("memories")
    if not isinstance(memories, dict):
        raise ValueError("ECC causal-state memory mapping is invalid")
    if any(
        memory_id not in memories
        or not isinstance(memories[memory_id], Mapping)
        or memories[memory_id].get("content_sha256") != content_sha256(text)
        for memory_id, text in overrides.items()
    ):
        raise ValueError("ECC causal-state override does not bind its memory record")
    if mechanism == "process_fault":
        if not isinstance(subtype, str) or subtype not in OPERATOR_SEMANTICS:
            raise ValueError("process-fault causal state requires one known subtype")
        if raw["repair_semantics"] != OPERATOR_SEMANTICS[subtype]:
            raise ValueError("process-fault repair semantics mismatch")
        if any(raw[name] not in (None, []) for name in (
            "superseding_memory_id", "superseded_memory_id", "suspect_ids"
        )):
            raise ValueError("process fault cannot carry drift or poison evidence")
        pipeline = expected_after.get("pipeline")
        before_pipeline = before.get("pipeline")
        after_pipeline = after.get("pipeline")
        if (
            not isinstance(pipeline, dict)
            or not isinstance(before_pipeline, Mapping)
            or not isinstance(after_pipeline, Mapping)
            or before_pipeline.get(subtype) is not False
            or any(
                before_pipeline.get(name) is not True
                for name in OPERATOR_SEMANTICS
                if name != subtype
            )
            or any(after_pipeline.get(name) is not True for name in OPERATOR_SEMANTICS)
        ):
            raise ValueError("causal-state pair does not encode one typed process fault")
        pipeline[subtype] = True
    elif mechanism == "state_drift":
        new_id = raw["superseding_memory_id"]
        old_id = raw["superseded_memory_id"]
        if (
            subtype is not None
            or raw["repair_semantics"] != REPAIR_SEMANTICS[mechanism]
            or not isinstance(new_id, str)
            or not isinstance(old_id, str)
            or new_id == old_id
            or raw["suspect_ids"] != []
            or old_id not in memories
            or new_id not in memories
            or memories[old_id].get("active") is not True
            or memories[new_id].get("active") is not False
        ):
            raise ValueError("causal-state pair does not encode one typed state drift")
        memories[old_id]["active"] = False
        memories[new_id]["active"] = True
        lineage = expected_after.get("lineage")
        if not isinstance(lineage, list):
            raise ValueError("state-drift causal state requires lineage")
        edge = [old_id, new_id]
        if edge not in lineage:
            lineage.append(edge)
    elif mechanism == "adversarial_poison":
        suspects = raw["suspect_ids"]
        if (
            subtype is not None
            or raw["repair_semantics"] != REPAIR_SEMANTICS[mechanism]
            or raw["superseding_memory_id"] is not None
            or raw["superseded_memory_id"] is not None
            or not isinstance(suspects, list)
            or not suspects
            or any(not isinstance(item, str) or item not in memories for item in suspects)
            or len(set(suspects)) != len(suspects)
            or any(memories[item].get("active") is not True for item in suspects)
        ):
            raise ValueError("causal-state pair does not encode one typed poison incident")
        quarantine = expected_after.get("quarantine")
        if not isinstance(quarantine, list):
            raise ValueError("poison causal state requires quarantine")
        for suspect_id in suspects:
            memories[suspect_id]["active"] = False
            if suspect_id not in quarantine:
                quarantine.append(suspect_id)
    else:
        raise ValueError("unknown ECC causal-state mechanism")
    if expected_after != dict(after):
        raise ValueError("ECC causal-state pair differs outside its typed repair operator")


def _load_runtime(runtime_dir: Path) -> tuple[dict[str, EccRepairReceipt], dict[str, Mapping[str, object]], Mapping[str, object]]:
    runtime_dir = Path(runtime_dir)
    report = json.loads((runtime_dir / "report.json").read_text(encoding="utf-8"))
    if (
        not isinstance(report, Mapping)
        or report.get("schema_version") not in {
            RUNTIME_REPORT_SCHEMA, LEGACY_RUNTIME_REPORT_SCHEMA
        }
        or report.get("answer_contrast_ready") is not True
    ):
        raise ValueError(
            "ECC answer runner requires a causal-state runtime"
        )
    report_without_binding = dict(report)
    report_binding = report_without_binding.pop("binding_root", None)
    if report_binding != content_sha256(report_without_binding):
        raise ValueError("ECC runtime report binding root mismatch")
    states_path = runtime_dir / "causal_states.jsonl"
    if hashlib.sha256(states_path.read_bytes()).hexdigest() != report.get("causal_states_sha256"):
        raise ValueError("ECC causal-state export does not match its runtime report")
    states: dict[str, Mapping[str, object]] = {}
    state_schema_versions: set[str] = set()
    for line in states_path.read_text(encoding="utf-8").splitlines():
        loaded = json.loads(line)
        if not isinstance(loaded, Mapping):
            raise ValueError("ECC causal-state row is not an object")
        state_schema_versions.add(str(loaded.get("schema_version")))
        if loaded.get("schema_version") == LEGACY_CAUSAL_STATE_SCHEMA:
            if set(loaded) != {
                "schema_version", "case_id", "process_fault_subtype",
                "operator_semantics", "before_root", "before_state", "after_root",
                "after_state", "receipt_sha256",
            }:
                raise ValueError("legacy ECC causal-state row is not closed")
            raw = {
                **dict(loaded),
                "schema_version": CAUSAL_STATE_SCHEMA,
                "mechanism": "process_fault",
                "repair_semantics": loaded["operator_semantics"],
                "superseding_memory_id": None,
                "superseded_memory_id": None,
                "suspect_ids": [],
                "memory_text_overrides": {},
            }
            raw.pop("operator_semantics")
        else:
            raw = dict(loaded)
        if set(raw) != {
            "schema_version", "case_id", "mechanism", "process_fault_subtype",
            "repair_semantics", "superseding_memory_id", "superseded_memory_id",
            "suspect_ids", "memory_text_overrides", "before_root", "before_state", "after_root",
            "after_state", "receipt_sha256",
        } or raw["schema_version"] != CAUSAL_STATE_SCHEMA:
            raise ValueError("ECC causal-state row is not closed or versioned")
        case_id = str(raw["case_id"])
        before, after = raw["before_state"], raw["after_state"]
        if (
            case_id in states
            or not isinstance(before, Mapping)
            or not isinstance(after, Mapping)
            or raw["before_root"] != content_sha256(dict(before), ensure_ascii=False, allow_nan=False)
            or raw["after_root"] != content_sha256(dict(after), ensure_ascii=False, allow_nan=False)
        ):
            raise ValueError("ECC causal-state row integrity check failed")
        _validate_causal_transition(raw)
        states[case_id] = raw
    if not states:
        raise ValueError("ECC causal-state runtime is empty")
    expected_state_schema = (
        LEGACY_CAUSAL_STATE_SCHEMA
        if report.get("schema_version") == LEGACY_RUNTIME_REPORT_SCHEMA
        else CAUSAL_STATE_SCHEMA
    )
    if state_schema_versions != {expected_state_schema}:
        raise ValueError("ECC runtime report and causal-state schema versions differ")
    mechanisms = {str(row["mechanism"]) for row in states.values()}
    if len(mechanisms) != 1:
        raise ValueError("one sealed experiment cannot mix ECC mechanisms")
    if report.get("schema_version") == RUNTIME_REPORT_SCHEMA:
        mechanism = next(iter(mechanisms))
        if (
            report.get("mechanism") != mechanism
            or report.get("mechanism_counts") != {mechanism: len(states)}
            or report.get("causal_experiment_kind") != "single-mechanism"
        ):
            raise ValueError("ECC runtime report does not bind one mechanism")
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
        or seal.get("schema_version") not in {SEAL_SCHEMA, LEGACY_SEAL_SCHEMA}
        or seal.get("sealed") is not True
        or seal.get("official_scoring_status") != "pending-independent-stage"
    ):
        raise ValueError("invalid ECC causal prediction seal")
    if seal.get("schema_version") == SEAL_SCHEMA:
        mechanism = seal.get("mechanism")
        if (
            mechanism not in {"process_fault", "state_drift", "adversarial_poison"}
            or seal.get("mechanism_counts") != {mechanism: seal.get("case_count")}
            or seal.get("causal_experiment_kind") != "single-mechanism"
            or seal.get("arms") != ["incident_before", "repaired_after"]
        ):
            raise ValueError("ECC v3 prediction seal is not mechanism-isolated")
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


def _validate_token_ledger_pair(
    *,
    before: object,
    after: object,
    mechanism: str,
    process_fault_subtype: str | None,
    max_input_tokens: int,
) -> None:
    before_budget = getattr(before, "budgeted")
    after_budget = getattr(after, "budgeted")
    for arm, budgeted in (("before", before_budget), ("after", after_budget)):
        count = budgeted.input_tokens
        if not isinstance(count, int) or isinstance(count, bool) or count <= 2:
            raise ValueError(
                f"token ledger smoke gate rejects implausible {arm} token count: {count!r}"
            )
        if count > max_input_tokens:
            raise ValueError(f"token ledger smoke gate rejects over-budget {arm} context")
    if (
        mechanism == "process_fault"
        and process_fault_subtype == "retrieval"
        and not before_budget.included_ids
        and after_budget.included_ids
        and after_budget.input_tokens <= before_budget.input_tokens
    ):
        raise ValueError(
            "retrieval repair token ledger must grow when memory results are restored"
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
    parser.add_argument(
        "--mechanism",
        choices=("process_fault", "state_drift", "adversarial_poison"),
    )
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
    mechanism = str(next(iter(states.values()))["mechanism"])
    if args.mechanism is not None and args.mechanism != mechanism:
        raise ValueError("requested mechanism does not match the isolated ECC runtime")
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
        "incident_before": [],
        "repaired_after": [],
    }
    ledger: list[Mapping[str, object]] = []
    for position, case in enumerate(cases, 1):
        query = str(case.raw["query"])
        state_row = states[case.case_id]
        subtype_value = state_row["process_fault_subtype"]
        subtype = None if subtype_value is None else str(subtype_value)
        before = render_causal_state(
            case=case,
            state=state_row["before_state"],
            state_root=str(state_row["before_root"]),
            process_fault_subtype=subtype,
            mechanism=mechanism,
            memory_text_overrides=state_row["memory_text_overrides"],
            query=query,
            budget=budget,
        )
        after = render_causal_state(
            case=case,
            state=state_row["after_state"],
            state_root=str(state_row["after_root"]),
            process_fault_subtype=subtype,
            mechanism=mechanism,
            memory_text_overrides=state_row["memory_text_overrides"],
            query=query,
            budget=budget,
        )
        _validate_token_ledger_pair(
            before=before,
            after=after,
            mechanism=mechanism,
            process_fault_subtype=subtype,
            max_input_tokens=budget.max_input_tokens,
        )
        before_answer = backend.answer_context(
            case, before.budgeted.context, purpose="ecc_causal_contrast"
        )
        after_answer = backend.answer_context(
            case, after.budgeted.context, purpose="ecc_causal_contrast"
        )
        predictions["incident_before"].append(
            {"question_id": case.case_id, "hypothesis": before_answer}
        )
        predictions["repaired_after"].append(
            {"question_id": case.case_id, "hypothesis": after_answer}
        )
        receipt = receipts[case.case_id]
        ledger.append({
            "schema_version": "cmd-ecc-memory-causal-prediction-row-v3",
            "position": position,
            "case_id": case.case_id,
            "runtime_path": "ecc_receipt_causal_contrast",
            "receipt_sha256": receipt.content_hash,
            "committed": receipt.committed,
            "mechanism": mechanism,
            "process_fault_subtype": subtype,
            "repair_semantics": before.operator_semantics,
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
        "mechanism": mechanism,
        "mechanism_counts": {mechanism: len(cases)},
        "causal_experiment_kind": "single-mechanism",
        "case_count": len(cases),
        "ecc_incident_case_count": len(receipts),
        "clean_bypass_case_count": 0,
        "arms": list(predictions),
        "arm_roles": {
            "incident_before": "receipt-bound pre-repair incident state",
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
            "process_fault_semantics": dict(OPERATOR_SEMANTICS),
            "mechanism_semantics": dict(REPAIR_SEMANTICS),
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
