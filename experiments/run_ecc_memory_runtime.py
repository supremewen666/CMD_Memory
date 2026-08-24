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
from typing import Mapping

from cmd_audit.core.state_codec import atomic_json_write, content_sha256
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


BINDING_SCHEMA = "cmd-ecc-ghost-binding-v1"
STATE_SCHEMA = "cmd-ecc-structural-scenario-v1"
COMMITTED_STATE_SCHEMA = "cmd-ecc-committed-state-v1"


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


def _write_committed_states(
    path: Path,
    stores: Mapping[str, StructuralMemoryStore],
) -> str:
    rows = []
    for case_id in sorted(stores):
        store = stores[case_id]
        rows.append({
            "schema_version": COMMITTED_STATE_SCHEMA,
            "case_id": case_id,
            "state_root": store.snapshot_root(),
            "state": dict(store.committed_state()),
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

    runtime = EccRuntimeRunner(
        cases,
        output_dir=args.output / "runtime",
        router=EccGhostRouter(ecology, bindings),
        store_factory=lambda case: stores[case.case_id],
        evaluator_factory=lambda case: StructuralEccEvaluator(stores[case.case_id]),
    ).run()
    committed_path = args.output / "committed_states.jsonl"
    committed_root = _write_committed_states(committed_path, stores)
    report = {
        "schema_version": "cmd-ecc-memory-runtime-report-v1",
        "status": "success",
        "runtime_manifest_sha256": runtime["run_manifest_sha256"],
        "receipt_root": runtime["receipt_root"],
        "case_count": runtime["case_count"],
        "committed": runtime["committed"],
        "rolled_back": runtime["rolled_back"],
        "committed_states_sha256": committed_root,
        "input_roots": {
            "cases": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
            "bindings": hashlib.sha256(args.bindings.read_bytes()).hexdigest(),
            "states": hashlib.sha256(args.states.read_bytes()).hexdigest(),
            "ecology": hashlib.sha256(args.ecology_ledger.read_bytes()).hexdigest(),
        },
        "runtime_uses_gold": False,
        "runtime_uses_labels": False,
        "same_trace_answer_replay": False,
        "binding_root": content_sha256({
            "runtime_manifest_sha256": runtime["run_manifest_sha256"],
            "receipt_root": runtime["receipt_root"],
            "committed_states_sha256": committed_root,
        }),
    }
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
