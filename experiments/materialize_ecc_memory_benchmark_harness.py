#!/usr/bin/env python3
"""Materialize a gold-free ECC harness from LoCoMo or LongMemEval.

The source benchmarks contain conversations and QA rows, but no native
MemAudit incident telemetry.  This command therefore creates an explicitly
labelled controlled process-fault stress track.  It never reads reference
answers, evidence IDs, answer-session IDs, or scorer output when constructing
the runtime bundle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

from cmd_audit.core.state_codec import atomic_json_write, content_sha256
from cmd_audit.repair.ghost_ecology import (
    EcologyLedger,
    FailureDeposit,
    GhostEcology,
    PatternRevision,
    RegistrySnapshot,
    SkillRevision,
)
from experiments.arena_runner_common import ArenaCase
from experiments.locomo_arena import load_locomo_arena_cases
from experiments.longmemeval_arena import load_longmemeval_arena_cases
from experiments.p4c_zero_call import StructuralMemoryStore
from experiments.run_ecc_memory_runtime import BINDING_SCHEMA, STATE_SCHEMA
from experiments.run_sealed_memory_benchmark import DEFAULTS


PROFILE = "controlled-process-fault-v2"
TRACK = "controlled-structural-stress-not-native-official"
CASE_SCHEMA = "cmd-p4c-ecc-case-v1"
MANIFEST_SCHEMA = "cmd-ecc-harness-bundle-v2"
SUBTYPES = ("retrieval", "injection", "granularity", "safety")


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, object]]) -> str:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _baseline_ids(case: ArenaCase) -> tuple[str, ...]:
    baselines = case.raw.get("baseline_outputs")
    if not isinstance(baselines, list) or not baselines:
        raise ValueError(f"benchmark case has no frozen retrieval control: {case.case_id}")
    baseline = baselines[0]
    if not isinstance(baseline, Mapping):
        raise ValueError(f"benchmark baseline is malformed: {case.case_id}")
    ids = baseline.get("retrieved_memory_ids")
    if not isinstance(ids, list) or any(not isinstance(row, str) for row in ids):
        raise ValueError(f"benchmark retrieval IDs are malformed: {case.case_id}")
    if not ids or len(set(ids)) != len(ids):
        raise ValueError(
            f"controlled process-fault contrast requires non-empty unique retrieval IDs: {case.case_id}"
        )
    return tuple(ids)


def _memory_state(case: ArenaCase, source_root: str) -> dict[str, object]:
    extracted = case.raw.get("extracted_memory")
    if not isinstance(extracted, list):
        raise ValueError(f"benchmark memory view is malformed: {case.case_id}")
    by_id = {
        str(row["memory_id"]): row
        for row in extracted
        if isinstance(row, Mapping) and row.get("memory_id") and isinstance(row.get("text"), str)
    }
    ids = _baseline_ids(case)
    missing = set(ids) - set(by_id)
    if missing:
        raise ValueError(f"retrieval IDs are absent from runtime memory view: {sorted(missing)!r}")
    return {
        memory_id: {
            "active": True,
            "content_sha256": content_sha256(str(by_id[memory_id]["text"])),
            "source_root": source_root,
        }
        for memory_id in ids
    }


def _load_cases(
    benchmark: str,
    dataset_path: Path,
    *,
    seed: int,
    limit: int,
    retrieval_top_k: int,
    candidate_pool_k: int,
) -> tuple[ArenaCase, ...]:
    kwargs = {
        "seed": seed,
        "limit": limit,
        "retrieval_top_k": retrieval_top_k,
        "candidate_pool_k": candidate_pool_k,
    }
    if benchmark == "locomo":
        return load_locomo_arena_cases(
            dataset_path, include_adversarial=True, **kwargs
        )
    return load_longmemeval_arena_cases(dataset_path, **kwargs)


def materialize_bundle(
    *,
    benchmark: str,
    dataset_path: Path,
    output: Path,
    seed: int = 24,
    limit: int = 0,
    retrieval_top_k: int = 5,
    candidate_pool_k: int = 10,
) -> Mapping[str, object]:
    dataset_path = Path(dataset_path)
    output = Path(output)
    if not dataset_path.is_file():
        raise FileNotFoundError(f"benchmark dataset does not exist: {dataset_path}")
    if output.exists() and any(output.iterdir()):
        raise ValueError("fresh ECC harness materialization refuses a non-empty output directory")
    output.mkdir(parents=True, exist_ok=True)

    cases = _load_cases(
        benchmark,
        dataset_path,
        seed=seed,
        limit=limit,
        retrieval_top_k=retrieval_top_k,
        candidate_pool_k=candidate_pool_k,
    )
    if not cases:
        raise ValueError("benchmark selection produced no cases")
    dataset_sha256 = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    source_manifest_root = content_sha256({
        "benchmark": benchmark,
        "dataset_sha256": dataset_sha256,
        "profile": PROFILE,
        "seed": seed,
        "retrieval_top_k": retrieval_top_k,
        "candidate_pool_k": candidate_pool_k,
        "case_ids": [case.case_id for case in cases],
    })

    assigned = [(case, SUBTYPES[position % len(SUBTYPES)]) for position, case in enumerate(cases)]
    ecology_path = output / "frozen_ecology.jsonl"
    ecology = GhostEcology(EcologyLedger(ecology_path))
    failures: dict[str, FailureDeposit] = {}
    failure_by_subtype: dict[str, str] = {}
    next_event = 1
    for case, subtype in assigned:
        failure_id = "failure-" + content_sha256({
            "case_id": case.case_id,
            "profile": PROFILE,
            "subtype": subtype,
        })
        failure = FailureDeposit(
            failure_id=failure_id,
            case_id=case.case_id,
            family_id_audit_only=f"{benchmark}:controlled-process-fault",
            failure_memory_sha256=content_sha256({
                "incident": "controlled-process-fault",
                "case_id": case.case_id,
                "subtype": subtype,
                "source_manifest_root": source_manifest_root,
            }),
            features=tuple(sorted(((f"pipeline-subtype:{subtype}", 1.0), (f"source:{benchmark}", 1.0)))),
            context_sha256=content_sha256({
                "case_id": case.case_id,
                "retrieved_memory_ids": list(_baseline_ids(case)),
            }),
            provenance_sha256=content_sha256({
                "detector": "controlled-memaudit-fixture-v1",
                "profile": PROFILE,
                "source_manifest_root": source_manifest_root,
            }),
        )
        ecology.deposit_failure(failure, event_index=next_event)
        next_event += 1
        failures[case.case_id] = failure
        failure_by_subtype.setdefault(subtype, failure_id)

    patterns: dict[str, PatternRevision] = {}
    skills: dict[str, SkillRevision] = {}
    for subtype in sorted(failure_by_subtype):
        pattern = PatternRevision.create(
            pattern_id=f"controlled-{subtype}-process-fault",
            predicate={"kind": "memaudit_process_fault", "subtype": subtype},
            feature_signature=(f"pipeline-subtype:{subtype}",),
            derivation_kind="seed",
            state="stable",
        )
        ecology.propose_pattern(pattern, event_index=next_event)
        next_event += 1
        skill = SkillRevision.create(
            skill_id=f"repair-{subtype}-pipeline",
            program={
                "kind": "typed_repair_program",
                "operator_kind": "pipeline_patch",
                "process_fault_subtype": subtype,
            },
            parameter_schema={"type": "object", "additionalProperties": False},
            preconditions=({"process_fault_subtype": subtype},),
            postconditions=({"pipeline_flag": subtype, "value": True},),
            success_probe={"probe_id": f"probe:{subtype}", "kind": "ecc_parity"},
            mutation_budget={"max_locality_cost": 1.0},
            rollback_program={"kind": "restore_before_root"},
            producing_failure_id=failure_by_subtype[subtype],
            derivation_kind="seed",
            state="stable",
        )
        ecology.propose_skill(skill, event_index=next_event)
        next_event += 1
        ecology.bind_pattern_skill(
            pattern.pattern_revision_id,
            skill.skill_revision_id,
            applicability=1.0,
            event_index=next_event,
        )
        next_event += 1
        patterns[subtype] = pattern
        skills[subtype] = skill

    registry = RegistrySnapshot.create(
        epoch=1,
        stable_pattern_revision_ids=tuple(row.pattern_revision_id for row in patterns.values()),
        stable_skill_revision_ids=tuple(row.skill_revision_id for row in skills.values()),
        config_sha256=content_sha256({
            "profile": PROFILE,
            "source_manifest_root": source_manifest_root,
        }),
    )
    ecology.freeze_registry(registry, event_index=next_event)
    next_event += 1

    case_rows: list[Mapping[str, object]] = []
    binding_rows: list[Mapping[str, object]] = []
    state_rows: list[Mapping[str, object]] = []
    first_observed_at = next_event
    for position, (case, subtype) in enumerate(assigned):
        skill = skills[subtype]
        pattern = patterns[subtype]
        state = {
            "pipeline": {name: name != subtype for name in SUBTYPES},
            "memories": _memory_state(case, dataset_sha256),
            # This list, not JSON object iteration order, is the authoritative
            # retrieval order and is covered by both before_root and after_root.
            "memory_order": list(_baseline_ids(case)),
            "lineage": [],
            "quarantine": [],
            "protected_ids": [],
        }
        operators = {skill.skill_revision_id: {"kind": "pipeline_patch"}}
        state_root = StructuralMemoryStore(state=state, operators=operators).snapshot_root()
        observed_at = first_observed_at + position * 3
        case_rows.append({
            "schema_version": CASE_SCHEMA,
            "case_id": case.case_id,
            "event_index": observed_at + 1,
            "observation": {
                "observation_id": "observation-" + content_sha256({"case_id": case.case_id, "profile": PROFILE}),
                "incident_id": "incident-" + content_sha256({"case_id": case.case_id, "subtype": subtype}),
                "observed_at_event_index": observed_at,
                "state_root": state_root,
                "source_manifest_root": source_manifest_root,
                "process_fault_subtype": subtype,
                "observed_order": [],
                "superseding_memory_id": None,
                "superseded_memory_id": None,
                "cas_anomaly": False,
                "influence_anomaly": False,
                "suspect_ids": [],
                "signal_ids": [f"pipeline-check:{subtype}:false"],
                "provenance": {
                    "detector": "controlled-memaudit-fixture-v1",
                    "profile": PROFILE,
                    "source_manifest_root": source_manifest_root,
                },
            },
            "candidates": [{
                "skill_revision_id": skill.skill_revision_id,
                "probe_id": f"probe:{subtype}",
                "operator_sha256": skill.program_sha256,
            }],
        })
        binding_rows.append({
            "schema_version": BINDING_SCHEMA,
            "case_id": case.case_id,
            "failure_id": failures[case.case_id].failure_id,
            "responsibilities": [[pattern.pattern_revision_id, 1.0]],
            "registry_id": registry.registry_id,
            "skill_priors": [[skill.skill_revision_id, 0.0]],
        })
        state_rows.append({
            "schema_version": STATE_SCHEMA,
            "case_id": case.case_id,
            "state": state,
            "operators": operators,
        })

    roots = {
        "memaudit_cases.jsonl": _write_jsonl(output / "memaudit_cases.jsonl", case_rows),
        "ghost_bindings.jsonl": _write_jsonl(output / "ghost_bindings.jsonl", binding_rows),
        "shadow_states.jsonl": _write_jsonl(output / "shadow_states.jsonl", state_rows),
        "frozen_ecology.jsonl": hashlib.sha256(ecology_path.read_bytes()).hexdigest(),
    }
    manifest: dict[str, object] = {
        "schema_version": MANIFEST_SCHEMA,
        "status": "ready",
        "benchmark": benchmark,
        "profile": PROFILE,
        "benchmark_track": TRACK,
        "case_count": len(cases),
        "source_dataset_sha256": dataset_sha256,
        "source_manifest_root": source_manifest_root,
        "file_roots": roots,
        "runtime_uses_reference_targets": False,
        "runtime_uses_scorer_output": False,
        "native_memaudit_telemetry": False,
        "controlled_fault_assignment": "case-position-modulo-four",
        "warning": (
            "This is a controlled structural stress track. Its answer score must not "
            "be reported as the native official benchmark score."
        ),
    }
    manifest["binding_root"] = content_sha256(manifest)
    atomic_json_write(
        output / "manifest.json",
        manifest,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        trailing_newline=True,
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", choices=tuple(DEFAULTS), required=True)
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=24)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--retrieval-top-k", type=int, default=5)
    parser.add_argument("--candidate-pool-k", type=int, default=10)
    args = parser.parse_args(argv)
    manifest = materialize_bundle(
        benchmark=args.benchmark,
        dataset_path=args.cases or DEFAULTS[args.benchmark],
        output=args.output,
        seed=args.seed,
        limit=args.limit,
        retrieval_top_k=args.retrieval_top_k,
        candidate_pool_k=args.candidate_pool_k,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
