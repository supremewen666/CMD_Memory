#!/usr/bin/env python3
"""Run an instrumented memory harness through MemAudit/ECC/GHOST.

Inputs are deployment-visible runtime artifacts only.  Dataset answers and
labels are deliberately absent.  A separate prediction/scoring stage may read
the committed-state export after this command completes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from collections import Counter
from typing import Mapping

from cmd_audit.core.state_codec import atomic_json_write, content_sha256
from cmd_audit.repair.ecc import EccRepairReceipt, MemAuditEccAdapter
from cmd_audit.repair.ghost_ecology import (
    EcologyLedger,
    GhostEcology,
    PatternResponsibility,
)
from experiments.ecc_memory_runtime import (
    EccGhostBinding,
    EccGhostRouter,
    EccRuntimeRunner,
    StructuralEccEvaluator,
    StructuralMemoryStore,
    load_ecc_runtime_cases,
)
from experiments.ecc_answer_causal_contrast import OPERATOR_SEMANTICS, REPAIR_SEMANTICS


BINDING_SCHEMA = "cmd-ecc-ghost-binding-v1"
STATE_SCHEMA = "cmd-ecc-structural-scenario-v2"
CAUSAL_STATE_SCHEMA = "cmd-ecc-causal-state-pair-v3"
RUNTIME_REPORT_SCHEMA = "cmd-ecc-memory-runtime-report-v3"


def _jsonl(path: Path) -> tuple[Mapping[str, object], ...]:
    rows: list[Mapping[str, object]] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{number}") from exc
        if not isinstance(value, Mapping):
            raise ValueError(f"JSONL row is not an object at {path}:{number}")
        rows.append(value)
    if not rows:
        raise ValueError(f"runtime input is empty: {path}")
    return tuple(rows)


def load_bindings(path: Path) -> dict[str, EccGhostBinding]:
    expected = {
        "schema_version", "case_id", "failure_id", "responsibilities",
        "registry_id", "skill_priors",
    }
    result: dict[str, EccGhostBinding] = {}
    for raw in _jsonl(path):
        if set(raw) != expected or raw["schema_version"] != BINDING_SCHEMA:
            raise ValueError("ECC GHOST binding row is not closed or versioned")
        case_id = str(raw["case_id"])
        responsibilities = raw["responsibilities"]
        priors = raw["skill_priors"]
        if not isinstance(responsibilities, list) or not isinstance(priors, list):
            raise ValueError("ECC GHOST responsibilities/priors must be lists")
        if case_id in result:
            raise ValueError(f"duplicate ECC GHOST binding: {case_id}")
        result[case_id] = EccGhostBinding(
            failure_id=str(raw["failure_id"]),
            responsibilities=tuple(
                PatternResponsibility(str(row[0]), float(row[1]))
                for row in responsibilities
                if isinstance(row, list) and len(row) == 2
            ),
            registry_id=str(raw["registry_id"]),
            skill_priors=tuple(
                (str(row[0]), float(row[1]))
                for row in priors
                if isinstance(row, list) and len(row) == 2
            ),
        )
    return result


def load_stores(path: Path) -> dict[str, StructuralMemoryStore]:
    expected = {"schema_version", "case_id", "state", "operators"}
    result: dict[str, StructuralMemoryStore] = {}
    for raw in _jsonl(path):
        if set(raw) != expected or raw["schema_version"] != STATE_SCHEMA:
            raise ValueError("ECC structural scenario row is not closed or versioned")
        case_id = str(raw["case_id"])
        state, operators = raw["state"], raw["operators"]
        if not isinstance(state, Mapping) or not isinstance(operators, Mapping):
            raise ValueError("ECC structural state/operators must be mappings")
        if case_id in result:
            raise ValueError(f"duplicate ECC structural scenario: {case_id}")
        result[case_id] = StructuralMemoryStore(
            state=state,
            operators={
                str(key): value
                for key, value in operators.items()
                if isinstance(value, Mapping)
            },
        )
    return result


def _load_receipts_by_case(runtime_dir: Path) -> dict[str, EccRepairReceipt]:
    completions = [
        json.loads(line)
        for line in (runtime_dir / "case_completions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    receipts = [
        EccRepairReceipt.from_mapping(json.loads(line))
        for line in (runtime_dir / "repair_receipts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    if len(completions) != len(receipts):
        raise ValueError("ECC runtime receipt/completion coverage differs")
    result: dict[str, EccRepairReceipt] = {}
    for completion, receipt in zip(completions, receipts, strict=True):
        case_id = str(completion.get("case_id"))
        if completion.get("receipt_sha256") != receipt.content_hash:
            raise ValueError("ECC completion does not bind its repair receipt")
        if case_id in result:
            raise ValueError("ECC runtime contains duplicate case completions")
        result[case_id] = receipt
    return result


def _write_causal_states(
    path: Path,
    cases: tuple[object, ...],
    stores: Mapping[str, StructuralMemoryStore],
    before_states: Mapping[str, Mapping[str, object]],
    receipts: Mapping[str, EccRepairReceipt],
) -> str:
    rows = []
    for case in cases:
        case_id = str(getattr(case, "case_id"))
        store = stores[case_id]
        before_state = dict(before_states[case_id])
        after_state = dict(store.committed_state())
        receipt = receipts[case_id]
        before_root = content_sha256(before_state, ensure_ascii=False, allow_nan=False)
        after_root = content_sha256(after_state, ensure_ascii=False, allow_nan=False)
        if receipt.before_root != before_root or receipt.after_root != after_root:
            raise ValueError(f"ECC causal state pair does not bind receipt roots: {case_id}")
        observation = getattr(case, "observation")
        syndrome = MemAuditEccAdapter().decode(observation)
        mechanism = syndrome.mechanism.value
        subtype = (
            None
            if syndrome.process_fault_subtype is None
            else syndrome.process_fault_subtype.value
        )
        semantics = (
            OPERATOR_SEMANTICS[subtype]
            if subtype is not None
            else REPAIR_SEMANTICS[mechanism]
        )
        overrides = getattr(case, "runtime_memory_texts", {})
        if not isinstance(overrides, Mapping) or any(
            not isinstance(memory_id, str) or not isinstance(text, str)
            for memory_id, text in overrides.items()
        ):
            raise ValueError(f"ECC controlled memory text view is invalid: {case_id}")
        rows.append({
            "schema_version": CAUSAL_STATE_SCHEMA,
            "case_id": case_id,
            "mechanism": mechanism,
            "process_fault_subtype": subtype,
            "repair_semantics": semantics,
            "superseding_memory_id": syndrome.superseding_memory_id,
            "superseded_memory_id": syndrome.superseded_memory_id,
            "suspect_ids": list(syndrome.suspect_ids),
            "memory_text_overrides": dict(overrides),
            "before_root": before_root,
            "before_state": before_state,
            "after_root": after_root,
            "after_state": after_state,
            "receipt_sha256": receipt.content_hash,
        })
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--states", type=Path, required=True)
    parser.add_argument("--ecology-ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    inputs = {
        "cases": args.cases,
        "bindings": args.bindings,
        "states": args.states,
        "ecology ledger": args.ecology_ledger,
    }
    missing = [f"{name}={path}" for name, path in inputs.items() if not path.is_file()]
    if missing:
        placeholder_hint = (
            " The /path/to/... values in documentation are placeholders; first run "
            "`python -m experiments.materialize_ecc_memory_benchmark_harness`."
            if any(str(path).startswith("/path/to/") for path in inputs.values())
            else ""
        )
        raise FileNotFoundError(
            "ECC runtime input file(s) do not exist: " + ", ".join(missing) + "." + placeholder_hint
        )

    input_roots = {
        "cases": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        "bindings": hashlib.sha256(args.bindings.read_bytes()).hexdigest(),
        "states": hashlib.sha256(args.states.read_bytes()).hexdigest(),
        "ecology": hashlib.sha256(args.ecology_ledger.read_bytes()).hexdigest(),
    }
    bundle_metadata: dict[str, object] = {}
    bundle_manifest_path = args.cases.parent / "manifest.json"
    if (
        args.bindings.parent == args.cases.parent
        and args.states.parent == args.cases.parent
        and args.ecology_ledger.parent == args.cases.parent
        and bundle_manifest_path.is_file()
    ):
        bundle_manifest = json.loads(bundle_manifest_path.read_text(encoding="utf-8"))
        expected_roots = bundle_manifest.get("file_roots")
        actual_roots = {
            "memaudit_cases.jsonl": input_roots["cases"],
            "ghost_bindings.jsonl": input_roots["bindings"],
            "shadow_states.jsonl": input_roots["states"],
            "frozen_ecology.jsonl": input_roots["ecology"],
        }
        if (
            bundle_manifest.get("schema_version") not in {
                "cmd-ecc-harness-bundle-v2", "cmd-ecc-harness-bundle-v3"
            }
            or not isinstance(expected_roots, Mapping)
            or dict(expected_roots) != actual_roots
        ):
            raise ValueError("ECC harness bundle files do not match manifest roots")
        bundle_metadata = {
            "harness_profile": bundle_manifest.get("profile"),
            "benchmark_track": bundle_manifest.get("benchmark_track"),
            "harness_binding_root": bundle_manifest.get("binding_root"),
            "harness_mechanism": bundle_manifest.get("mechanism", "process_fault"),
        }

    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("fresh ECC runtime refuses a non-empty output directory")
    args.output.mkdir(parents=True, exist_ok=True)
    runtime_ecology_path = args.output / "ecology.jsonl"
    shutil.copyfile(args.ecology_ledger, runtime_ecology_path)
    ecology = GhostEcology(
        EcologyLedger(runtime_ecology_path),
        discovery_authorized=False,
        evaluation_only=False,
    )
    cases = load_ecc_runtime_cases(args.cases)
    bindings = load_bindings(args.bindings)
    stores = load_stores(args.states)
    case_ids = {case.case_id for case in cases}
    if set(bindings) != case_ids or set(stores) != case_ids:
        raise ValueError("ECC cases, bindings, and structural states must cover identical IDs")
    for case in cases:
        if stores[case.case_id].snapshot_root() != case.observation["state_root"]:
            raise ValueError(f"ECC state root mismatch for {case.case_id}")
    before_states = {
        case.case_id: dict(stores[case.case_id].committed_state()) for case in cases
    }

    runtime = EccRuntimeRunner(
        cases,
        output_dir=args.output / "runtime",
        router=EccGhostRouter(ecology, bindings),
        store_factory=lambda case: stores[case.case_id],
        evaluator_factory=lambda case: StructuralEccEvaluator(stores[case.case_id]),
    ).run()
    receipts = _load_receipts_by_case(args.output / "runtime")
    if set(receipts) != case_ids:
        raise ValueError("ECC runtime receipt coverage differs from causal state stream")
    causal_states_path = args.output / "causal_states.jsonl"
    causal_states_root = _write_causal_states(
        causal_states_path,
        cases,
        stores,
        before_states,
        receipts,
    )
    mechanism_counts = Counter(
        MemAuditEccAdapter().decode(case.observation).mechanism.value for case in cases
    )
    if len(mechanism_counts) != 1:
        raise ValueError(
            "one ECC runtime experiment must contain exactly one incident mechanism"
        )
    report: dict[str, object] = {
        "schema_version": RUNTIME_REPORT_SCHEMA,
        "status": "success",
        "runtime_manifest_sha256": runtime["run_manifest_sha256"],
        "receipt_root": runtime["receipt_root"],
        "case_count": runtime["case_count"],
        "committed": runtime["committed"],
        "rolled_back": runtime["rolled_back"],
        "causal_states_sha256": causal_states_root,
        "causal_state_schema": CAUSAL_STATE_SCHEMA,
        "answer_contrast_ready": bool(
            runtime["committed"] == runtime["case_count"]
            and runtime["rolled_back"] == 0
        ),
        "mechanism": next(iter(mechanism_counts)),
        "mechanism_counts": dict(sorted(mechanism_counts.items())),
        "causal_experiment_kind": "single-mechanism",
        "input_roots": input_roots,
        "runtime_uses_gold": False,
        "runtime_uses_labels": False,
        "same_trace_answer_replay": False,
    }
    report.update(bundle_metadata)
    report["binding_root"] = content_sha256(report)
    atomic_json_write(
        args.output / "report.json",
        report,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        trailing_newline=True,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
