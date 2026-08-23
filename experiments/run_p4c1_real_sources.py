"""P4C-1: zero-call live-ABI wiring over three real structural sources."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Mapping

from cmd_audit.core.state_codec import (
    append_jsonl_fsync,
    atomic_json_write,
    content_sha256,
)
from cmd_audit.repair.ecc import EccRepairReceipt
from cmd_audit.repair.ghost_ecology import (
    EcologyLedger,
    FailureDeposit,
    GhostEcology,
    PatternResponsibility,
    PatternRevision,
    RegistrySnapshot,
    SkillRevision,
)
from experiments.p4c_ecc_runner import (
    P4cEccCase,
    P4cGhostBinding,
    P4cGhostRouter,
    P4cRepairCandidate,
)
from experiments.p4c_zero_call import P4cZeroCallScenario, P4cZeroCallSuite
from experiments.poison_density_sweep import build_sweep_case
from experiments.run_longmemeval_m0_r1 import iter_json_array


@dataclass(frozen=True)
class _P4c1Seed:
    source: str
    source_case_id: str
    state: Mapping[str, object]
    mechanism: str
    operator_kind: str
    observation_channels: Mapping[str, object]


@dataclass(frozen=True)
class P4c1Plan:
    source_projection: tuple[Mapping[str, object], ...]
    incident_overlay: tuple[Mapping[str, object], ...]
    source_roots: Mapping[str, str]
    plan_sha256: str
    seeds: tuple[_P4c1Seed, ...]


SESSION_PROJECTION_SCHEMA = "cmd-runtime-session-role-content-v1"
P4C1_MANIFEST_SCHEMA = "cmd-p4c1-real-source-zero-call-v2"
P4C1_PROJECTION_SCHEMA = "cmd-p4c1-source-projection-v2"


def project_gold_free_session(session: object) -> list[dict[str, str]]:
    """Return the only LongMemEval message fields admitted to runtime state."""
    if not isinstance(session, list):
        raise ValueError("LongMemEval session must be a list")
    projected: list[dict[str, str]] = []
    for message in session:
        if (
            not isinstance(message, Mapping)
            or not isinstance(message.get("role"), str)
            or not isinstance(message.get("content"), str)
        ):
            raise ValueError("LongMemEval message requires role/content")
        projected.append(
            {"role": str(message["role"]), "content": str(message["content"])}
        )
    return projected


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _base_state(memories: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
    return {
        "pipeline": {
            "retrieval": True,
            "injection": True,
            "granularity": True,
            "safety": True,
        },
        "memories": {key: dict(value) for key, value in memories.items()},
        "lineage": [],
        "quarantine": [],
        "protected_ids": [],
    }


def _visible_telemetry(
    *,
    seed: _P4c1Seed,
    case_id: str,
    observed_at_event_index: int,
    source_manifest_root: str,
) -> dict[str, object]:
    """Project only signals a deployed detector may observe.

    This is deliberately derived from the live state and structural telemetry,
    not from the incident overlay's mechanism field.
    """
    pipeline = seed.state.get("pipeline")
    if not isinstance(pipeline, Mapping):
        raise ValueError("P4C-1 state pipeline telemetry is unavailable")
    checks = {
        name: pipeline.get(name)
        for name in ("retrieval", "injection", "granularity", "safety")
    }
    if any(not isinstance(value, bool) for value in checks.values()):
        raise ValueError("P4C-1 pipeline telemetry must be boolean")

    observed_order = seed.observation_channels.get("observed_order", [])
    if not isinstance(observed_order, list):
        raise ValueError("P4C-1 observed order telemetry must be a list")
    active_versions = [
        {
            "slot": "active-memory-version",
            "memory_id": str(memory_id),
            "observed_at": position,
        }
        for position, memory_id in enumerate(observed_order, 1)
    ]
    suspect_ids = seed.observation_channels.get("suspect_ids", [])
    if not isinstance(suspect_ids, list):
        raise ValueError("P4C-1 integrity telemetry suspects must be a list")
    integrity_signals = [
        {
            "memory_id": str(memory_id),
            "cas_valid": False,
            "influence_score": 1.0,
            "influence_threshold": 0.5,
        }
        for memory_id in suspect_ids
    ]
    return {
        "schema_version": "cmd-p4c3-visible-telemetry-v1",
        "case_id": case_id,
        "observed_at_event_index": observed_at_event_index,
        "state_root": content_sha256(seed.state, ensure_ascii=False, allow_nan=False),
        "source_manifest_root": source_manifest_root,
        "pipeline_checks": checks,
        "active_versions": active_versions,
        "integrity_signals": integrity_signals,
    }


def _clean_visible_telemetry(
    *,
    seed: _P4c1Seed,
    case_id: str,
    observed_at_event_index: int,
    source_manifest_root: str,
    committed_state_root: str,
) -> dict[str, object]:
    observed_order = seed.observation_channels.get("observed_order", [])
    if not isinstance(observed_order, list):
        raise ValueError("P4C-1 clean-control observed order must be a list")
    active_versions = []
    if observed_order:
        active_versions.append(
            {
                "slot": "active-memory-version",
                "memory_id": str(observed_order[-1]),
                "observed_at": 1,
            }
        )
    return {
        "schema_version": "cmd-p4c3-visible-telemetry-v1",
        "case_id": f"{case_id}-clean-control",
        "observed_at_event_index": observed_at_event_index,
        "state_root": committed_state_root,
        "source_manifest_root": source_manifest_root,
        "pipeline_checks": {
            "retrieval": True,
            "injection": True,
            "granularity": True,
            "safety": True,
        },
        "active_versions": active_versions,
        "integrity_signals": [],
    }


def build_p4c1_plan(
    *,
    longmemeval_path: Path,
    memfail_root: Path,
    limit_per_source: int = 5,
    longmemeval_limit: int | None = None,
    memfail_limit: int | None = None,
    poison_case_count: int | None = None,
    poison_recall_size: int = 10,
    poison_count: int = 3,
    poison_counts: tuple[int, ...] | None = None,
) -> P4c1Plan:
    """Project visible source structure, then freeze separate incident overlays."""
    if limit_per_source < 1:
        raise ValueError("P4C-1 limit_per_source must be positive")
    source_limits = {
        "longmemeval": limit_per_source if longmemeval_limit is None else longmemeval_limit,
        "memfail": limit_per_source if memfail_limit is None else memfail_limit,
        "poison_sweep": limit_per_source if poison_case_count is None else poison_case_count,
    }
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in source_limits.values()
    ):
        raise ValueError("P4C-1 source-specific limits must be positive integers")
    poison_grid = (poison_count,) if poison_counts is None else tuple(poison_counts)
    if not poison_grid or any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 < value <= poison_recall_size
        for value in poison_grid
    ):
        raise ValueError("P4C-1 poison_count must be positive and within recall size")
    longmemeval_path = Path(longmemeval_path)
    memfail_root = Path(memfail_root)
    long_root = _file_sha256(longmemeval_path)
    memfail_files = tuple(sorted(memfail_root.rglob("*.csv")))
    if not memfail_files:
        raise ValueError("P4C-1 MemFail source contains no CSV files")
    memfail_root_hash = content_sha256(
        {str(path.relative_to(memfail_root)): _file_sha256(path) for path in memfail_files}
    )
    poison_code = Path(__file__).with_name("poison_density_sweep.py")
    poison_root = content_sha256(
        {
            "code_sha256": _file_sha256(poison_code),
            "recall_size": poison_recall_size,
            "poison_counts": list(poison_grid),
        }
    )
    source_roots = {
        "longmemeval": long_root,
        "memfail": memfail_root_hash,
        "poison_sweep": poison_root,
    }
    projections: list[Mapping[str, object]] = []
    overlays: list[Mapping[str, object]] = []
    seeds: list[_P4c1Seed] = []

    for row in iter_json_array(longmemeval_path):
        if (
            len([seed for seed in seeds if seed.source == "longmemeval"])
            >= source_limits["longmemeval"]
        ):
            break
        question_id = row.get("question_id")
        session_ids = row.get("haystack_session_ids")
        sessions = row.get("haystack_sessions")
        if (
            not isinstance(question_id, str)
            or not isinstance(session_ids, list)
            or not isinstance(sessions, list)
            or len(session_ids) < 2
            or len(sessions) < 2
        ):
            continue
        memory_rows = [
            {
                "memory_id": f"lm-{question_id}-{index}",
                "content_sha256": content_sha256(
                    project_gold_free_session(sessions[index]),
                    ensure_ascii=False,
                    allow_nan=False,
                ),
                "source_event_id": str(session_ids[index]),
                "content_projection_schema": SESSION_PROJECTION_SCHEMA,
            }
            for index in range(2)
        ]
        memories = {
            item["memory_id"]: {
                "active": True,
                "content_sha256": item["content_sha256"],
                "source_root": long_root,
            }
            for item in memory_rows
        }
        state = _base_state(memories)
        case_id = f"p4c1-longmemeval-{question_id}"
        projection = {
            "schema_version": P4C1_PROJECTION_SCHEMA,
            "source": "longmemeval",
            "source_case_id": question_id,
            "source_root": long_root,
            "visible_fields": [
                "question_id",
                "haystack_session_ids",
                "haystack_sessions[].role",
                "haystack_sessions[].content",
            ],
            "session_projection_schema": SESSION_PROJECTION_SCHEMA,
            "memory_records": memory_rows,
            "state_root": content_sha256(state, ensure_ascii=False, allow_nan=False),
        }
        overlay = {
            "schema_version": "cmd-p4c1-incident-overlay-v1",
            "source": "longmemeval",
            "case_id": case_id,
            "mechanism": "state_drift",
            "injection_kind": "chronology_supersession_overlay",
            "source_root": long_root,
            "state_root": projection["state_root"],
            "signal_ids": ["long-horizon-active-version-conflict"],
        }
        projections.append(projection)
        overlays.append(overlay)
        seeds.append(
            _P4c1Seed(
                "longmemeval",
                question_id,
                state,
                "state_drift",
                "supersede_lineage",
                {
                    "process_fault_subtype": None,
                    "observed_order": [item["memory_id"] for item in memory_rows],
                    "superseding_memory_id": memory_rows[1]["memory_id"],
                    "superseded_memory_id": memory_rows[0]["memory_id"],
                    "cas_anomaly": False,
                    "influence_anomaly": False,
                    "suspect_ids": [],
                    "signal_ids": overlay["signal_ids"],
                },
            )
        )

    long_hop = memfail_root / "long_hop" / "long_hop_chains.csv"
    with long_hop.open(newline="", encoding="utf-8") as stream:
        for index, row in enumerate(csv.DictReader(stream)):
            if index >= source_limits["memfail"]:
                break
            source_case_id = str(row["id"])
            facts = [
                row[name].strip()
                for name in ("fact_1", "fact_2", "fact_3", "fact_4")
                if row.get(name, "").strip()
            ]
            memory_rows = [
                {
                    "memory_id": f"mf-{source_case_id}-{position}",
                    "content_sha256": content_sha256(
                        fact, ensure_ascii=False, allow_nan=False
                    ),
                    "source_event_id": f"fact-{position + 1}",
                }
                for position, fact in enumerate(facts)
            ]
            memories = {
                item["memory_id"]: {
                    "active": True,
                    "content_sha256": item["content_sha256"],
                    "source_root": memfail_root_hash,
                }
                for item in memory_rows
            }
            state = _base_state(memories)
            state["pipeline"]["retrieval"] = False
            case_id = f"p4c1-memfail-{source_case_id}"
            projection = {
                "schema_version": P4C1_PROJECTION_SCHEMA,
                "source": "memfail",
                "source_case_id": source_case_id,
                "source_root": memfail_root_hash,
                "visible_fields": ["id", "fact_1", "fact_2", "fact_3", "fact_4"],
                "memory_records": memory_rows,
                "state_root": content_sha256(state, ensure_ascii=False, allow_nan=False),
            }
            overlay = {
                "schema_version": "cmd-p4c1-incident-overlay-v1",
                "source": "memfail",
                "case_id": case_id,
                "mechanism": "process_fault",
                "injection_kind": "retrieval_pipeline_unavailable_overlay",
                "source_root": memfail_root_hash,
                "state_root": projection["state_root"],
                "signal_ids": ["retrieval-unavailable"],
            }
            projections.append(projection)
            overlays.append(overlay)
            seeds.append(
                _P4c1Seed(
                    "memfail",
                    source_case_id,
                    state,
                    "process_fault",
                    "pipeline_patch",
                    {
                        "process_fault_subtype": "retrieval",
                        "observed_order": [],
                        "superseding_memory_id": None,
                        "superseded_memory_id": None,
                        "cas_anomaly": False,
                        "influence_anomaly": False,
                        "suspect_ids": [],
                        "signal_ids": overlay["signal_ids"],
                    },
                )
            )

    for index in range(source_limits["poison_sweep"]):
        case_poison_count = poison_grid[index % len(poison_grid)]
        sweep = build_sweep_case(
            case_id=f"p4c1-poison-{index + 1}",
            recall_size=poison_recall_size,
            poisoned_count=case_poison_count,
        )
        memory_rows = [
            {
                "memory_id": item.memory_id,
                "content_sha256": content_sha256(
                    item.text, ensure_ascii=False, allow_nan=False
                ),
                "source_event_id": f"recall-{position + 1}",
            }
            for position, item in enumerate(sweep.items)
        ]
        suspects = [item.memory_id for item in sweep.items if item.poisoned]
        memories = {
            item["memory_id"]: {
                "active": True,
                "content_sha256": item["content_sha256"],
                "source_root": poison_root,
            }
            for item in memory_rows
        }
        state = _base_state(memories)
        case_id = sweep.case_id
        projection = {
            "schema_version": P4C1_PROJECTION_SCHEMA,
            "source": "poison_sweep",
            "source_case_id": sweep.case_id,
            "source_root": poison_root,
            "visible_fields": ["memory_id", "text_hash", "recall_order"],
            "memory_records": memory_rows,
            "state_root": content_sha256(state, ensure_ascii=False, allow_nan=False),
        }
        overlay = {
            "schema_version": "cmd-p4c1-incident-overlay-v1",
            "source": "poison_sweep",
            "case_id": case_id,
            "mechanism": "adversarial_poison",
            "injection_kind": "coordinated_cas_influence_overlay",
            "source_root": poison_root,
            "state_root": projection["state_root"],
            "signal_ids": ["cas-mismatch", "influence-spike"],
        }
        projections.append(projection)
        overlays.append(overlay)
        seeds.append(
            _P4c1Seed(
                "poison_sweep",
                sweep.case_id,
                state,
                "adversarial_poison",
                "quarantine_poison",
                {
                    "process_fault_subtype": None,
                    "observed_order": [],
                    "superseding_memory_id": None,
                    "superseded_memory_id": None,
                    "cas_anomaly": True,
                    "influence_anomaly": True,
                    "suspect_ids": suspects,
                    "signal_ids": overlay["signal_ids"],
                },
            )
        )
    if any(
        sum(seed.source == source for seed in seeds) != source_limits[source]
        for source in source_limits
    ):
        raise ValueError("P4C-1 could not project the requested source coverage")
    plan_body = {
        "source_projection": projections,
        "incident_overlay": overlays,
        "source_roots": source_roots,
    }
    return P4c1Plan(
        tuple(projections),
        tuple(overlays),
        source_roots,
        content_sha256(plan_body, ensure_ascii=False, allow_nan=False),
        tuple(seeds),
    )


def run_p4c1_zero_call(
    *,
    longmemeval_path: Path,
    memfail_root: Path,
    output_dir: Path,
    limit_per_source: int = 5,
    longmemeval_limit: int | None = None,
    memfail_limit: int | None = None,
    poison_case_count: int | None = None,
    poison_recall_size: int = 10,
    poison_count: int = 3,
    poison_counts: tuple[int, ...] | None = None,
) -> dict[str, object]:
    """Run the sealed source projection through the receipt-only live ABI."""
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("fresh P4C-1 run refuses a non-empty output directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    plan = build_p4c1_plan(
        longmemeval_path=longmemeval_path,
        memfail_root=memfail_root,
        limit_per_source=limit_per_source,
        longmemeval_limit=longmemeval_limit,
        memfail_limit=memfail_limit,
        poison_case_count=poison_case_count,
        poison_recall_size=poison_recall_size,
        poison_count=poison_count,
        poison_counts=poison_counts,
    )
    projection_path = output_dir / "source_projection.jsonl"
    overlay_path = output_dir / "incident_overlay.jsonl"
    visible_telemetry_path = output_dir / "visible_telemetry.jsonl"
    detection_overlay_path = output_dir / "detection_audit_overlay.jsonl"
    for row in plan.source_projection:
        append_jsonl_fsync(
            projection_path, row, ensure_ascii=False, allow_nan=False
        )
    for row in plan.incident_overlay:
        append_jsonl_fsync(overlay_path, row, ensure_ascii=False, allow_nan=False)
    fault_telemetry: list[Mapping[str, object]] = []
    fault_labels: list[Mapping[str, object]] = []
    for position, (seed, overlay) in enumerate(
        zip(plan.seeds, plan.incident_overlay, strict=True)
    ):
        fault_telemetry.append(
            _visible_telemetry(
                seed=seed,
                case_id=str(overlay["case_id"]),
                observed_at_event_index=1000 + position * 3,
                source_manifest_root=plan.plan_sha256,
            )
        )
        fault_labels.append(
            {
                "schema_version": "cmd-p4c3-audit-overlay-v1",
                "case_id": str(overlay["case_id"]),
                "label": seed.mechanism,
            }
        )

    failures: list[FailureDeposit] = []
    for seed, overlay in zip(plan.seeds, plan.incident_overlay, strict=True):
        failures.append(
            FailureDeposit(
                failure_id=f"failure-{overlay['case_id']}",
                case_id=str(overlay["case_id"]),
                family_id_audit_only="p4c1-real-source",
                failure_memory_sha256=content_sha256(
                    {
                        "source": seed.source,
                        "source_case_id": seed.source_case_id,
                        "signals": seed.observation_channels["signal_ids"],
                    }
                ),
                features=(("source-channel", 1.0),),
                context_sha256=str(overlay["state_root"]),
                provenance_sha256=plan.plan_sha256,
            )
        )
    mechanisms = ("process_fault", "state_drift", "adversarial_poison")
    operator_by_mechanism = {
        "process_fault": "pipeline_patch",
        "state_drift": "supersede_lineage",
        "adversarial_poison": "quarantine_poison",
    }
    patterns = {
        mechanism: PatternRevision.create(
            pattern_id=f"p4c1-{mechanism.replace('_', '-')}",
            predicate={
                "kind": "structural_source_incident",
                "mechanism": mechanism,
            },
            feature_signature=("source-channel",),
            derivation_kind="seed",
            state="stable",
        )
        for mechanism in mechanisms
    }
    skills: dict[str, SkillRevision] = {}
    for mechanism in mechanisms:
        producer = next(
            failure
            for failure, seed in zip(failures, plan.seeds, strict=True)
            if seed.mechanism == mechanism
        )
        operator_kind = operator_by_mechanism[mechanism]
        skills[mechanism] = SkillRevision.create(
            skill_id=f"p4c1-{operator_kind}",
            program={
                "kind": "typed_repair_program",
                "operator_kind": operator_kind,
            },
            parameter_schema={"type": "object", "additionalProperties": False},
            preconditions=({"predicate": patterns[mechanism].pattern_id},),
            postconditions=({"predicate": "syndrome_resolved"},),
            success_probe={
                "probe_id": f"probe:p4c1:{operator_kind}",
                "kind": "ecc_parity",
            },
            mutation_budget={"max_locality_cost": 1.0},
            rollback_program={"kind": "restore_before_root"},
            producing_failure_id=producer.failure_id,
            derivation_kind="seed",
            state="stable",
        )
    ecology = GhostEcology(EcologyLedger(output_dir / "ecology.jsonl"))
    event_index = 0
    for failure in failures:
        event_index += 1
        ecology.deposit_failure(failure, event_index=event_index)
    for mechanism in mechanisms:
        event_index += 1
        ecology.propose_pattern(patterns[mechanism], event_index=event_index)
    for mechanism in mechanisms:
        event_index += 1
        ecology.propose_skill(skills[mechanism], event_index=event_index)
    registry = RegistrySnapshot.create(
        epoch=1,
        stable_pattern_revision_ids=tuple(
            pattern.pattern_revision_id for pattern in patterns.values()
        ),
        stable_skill_revision_ids=tuple(
            skill.skill_revision_id for skill in skills.values()
        ),
        config_sha256=content_sha256(
            {"plan_sha256": plan.plan_sha256, "runtime": "P4C-1-zero-call"}
        ),
    )
    event_index += 1
    ecology.freeze_registry(registry, event_index=event_index)

    scenarios: list[P4cZeroCallScenario] = []
    bindings: dict[str, P4cGhostBinding] = {}
    for position, (seed, overlay, failure) in enumerate(
        zip(plan.seeds, plan.incident_overlay, failures, strict=True)
    ):
        observed = 1000 + position * 3
        mechanism = seed.mechanism
        skill = skills[mechanism]
        observation = {
            "observation_id": f"observation-{overlay['case_id']}",
            "incident_id": f"incident-{overlay['case_id']}",
            "observed_at_event_index": observed,
            "state_root": overlay["state_root"],
            "source_manifest_root": plan.plan_sha256,
            **dict(seed.observation_channels),
            "provenance": {
                "detector": "p4c1-structural-source-adapter-v1",
                "source": seed.source,
                "source_projection_sha256": content_sha256(
                    plan.source_projection[position],
                    ensure_ascii=False,
                    allow_nan=False,
                ),
                "incident_overlay_sha256": content_sha256(
                    overlay, ensure_ascii=False, allow_nan=False
                ),
            },
        }
        case = P4cEccCase(
            case_id=str(overlay["case_id"]),
            event_index=observed + 1,
            observation=observation,
            candidates=(
                P4cRepairCandidate(
                    skill_revision_id=skill.skill_revision_id,
                    probe_id=str(skill.success_probe["probe_id"]),
                    operator_sha256=skill.program_sha256,
                ),
            ),
        )
        scenarios.append(
            P4cZeroCallScenario(
                case,
                state=seed.state,
                operators={
                    skill.skill_revision_id: {"kind": seed.operator_kind}
                },
            )
        )
        bindings[case.case_id] = P4cGhostBinding(
            failure_id=failure.failure_id,
            responsibilities=(
                PatternResponsibility(
                    patterns[mechanism].pattern_revision_id, 1.0
                ),
            ),
            registry_id=registry.registry_id,
        )
    router = P4cGhostRouter(ecology, bindings)
    runtime = P4cZeroCallSuite(
        scenarios,
        output_dir=output_dir / "runtime",
        router=router,
    ).run()
    receipts = [
        EccRepairReceipt.from_mapping(json.loads(line))
        for line in (output_dir / "runtime" / "repair_receipts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    if len(receipts) != len(plan.seeds) or any(
        not receipt.committed for receipt in receipts
    ):
        raise ValueError("P4C-1 clean controls require one committed repair per case")
    clean_telemetry: list[Mapping[str, object]] = []
    clean_labels: list[Mapping[str, object]] = []
    for position, (seed, overlay, receipt) in enumerate(
        zip(plan.seeds, plan.incident_overlay, receipts, strict=True)
    ):
        clean = _clean_visible_telemetry(
            seed=seed,
            case_id=str(overlay["case_id"]),
            observed_at_event_index=1000 + position * 3 + 2,
            source_manifest_root=plan.plan_sha256,
            committed_state_root=receipt.after_root,
        )
        clean_telemetry.append(clean)
        clean_labels.append(
            {
                "schema_version": "cmd-p4c3-audit-overlay-v1",
                "case_id": clean["case_id"],
                "label": "no_fault",
            }
        )
    telemetry_by_case = {
        str(row["case_id"]): row for row in (*fault_telemetry, *clean_telemetry)
    }
    labels_by_case = {
        str(row["case_id"]): row for row in (*fault_labels, *clean_labels)
    }
    ordered_telemetry = sorted(
        telemetry_by_case.values(), key=lambda row: int(row["observed_at_event_index"])
    )
    for row in ordered_telemetry:
        append_jsonl_fsync(
            visible_telemetry_path, row, ensure_ascii=False, allow_nan=False
        )
        append_jsonl_fsync(
            detection_overlay_path,
            labels_by_case[str(row["case_id"])],
            ensure_ascii=False,
            allow_nan=False,
        )
    if plan.source_roots != build_p4c1_plan(
        longmemeval_path=longmemeval_path,
        memfail_root=memfail_root,
        limit_per_source=limit_per_source,
        longmemeval_limit=longmemeval_limit,
        memfail_limit=memfail_limit,
        poison_case_count=poison_case_count,
        poison_recall_size=poison_recall_size,
        poison_count=poison_count,
        poison_counts=poison_counts,
    ).source_roots:
        raise ValueError("P4C-1 source roots changed during execution")
    source_counts = {
        source: sum(row["source"] == source for row in plan.source_projection)
        for source in plan.source_roots
    }
    result: dict[str, object] = {
        **runtime,
        "schema_version": P4C1_MANIFEST_SCHEMA,
        "plan_sha256": plan.plan_sha256,
        "source_roots": dict(plan.source_roots),
        "source_counts": source_counts,
        "requested_source_counts": {
            "longmemeval": limit_per_source if longmemeval_limit is None else longmemeval_limit,
            "memfail": limit_per_source if memfail_limit is None else memfail_limit,
            "poison_sweep": limit_per_source if poison_case_count is None else poison_case_count,
        },
        "telemetry_ordering": "observed_at_event_index_ascending_fault_then_clean_per_case",
        "poison_grid": {
            "recall_size": poison_recall_size,
            "poison_counts": list(
                (poison_count,) if poison_counts is None else poison_counts
            ),
            "case_count": source_counts["poison_sweep"],
            "evidence_unit": "parameterized_structural_variant_not_independent_real_source_case",
        },
        "projection_sha256": content_sha256(
            list(plan.source_projection), ensure_ascii=False, allow_nan=False
        ),
        "incident_overlay_sha256": content_sha256(
            list(plan.incident_overlay), ensure_ascii=False, allow_nan=False
        ),
        "visible_telemetry_sha256": _file_sha256(visible_telemetry_path),
        "visible_telemetry_case_count": len(plan.seeds) * 2,
        "detection_audit_overlay_sha256": _file_sha256(detection_overlay_path),
        "ecology_head_sha256": ecology.ledger.head_sha256,
        "router_snapshot_sha256": router.snapshot_sha256,
        "router": "P4cGhostRouter",
        "router_feedback": "EccRepairReceipt",
        "paper_role": "mainline",
        "primary_claim": "gold-free memory fault correction and evolution",
        "session_projection_schema": SESSION_PROJECTION_SCHEMA,
        "raw_sources_mutated": False,
        "claim_scope": "real_source_structural_wiring_not_task_accuracy",
    }
    atomic_json_write(
        output_dir / "p4c1_manifest.json",
        result,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        trailing_newline=True,
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--longmemeval", type=Path, required=True)
    parser.add_argument("--memfail-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit-per-source", type=int, default=5)
    parser.add_argument("--longmemeval-limit", type=int)
    parser.add_argument("--memfail-limit", type=int)
    parser.add_argument("--poison-case-count", type=int)
    parser.add_argument("--poison-recall-size", type=int, default=10)
    parser.add_argument("--poison-count", type=int, default=3)
    parser.add_argument(
        "--poison-counts",
        help="comma-separated poison counts; cycled over poison structural variants",
    )
    args = parser.parse_args(argv)
    result = run_p4c1_zero_call(
        longmemeval_path=args.longmemeval,
        memfail_root=args.memfail_root,
        output_dir=args.output_dir,
        limit_per_source=args.limit_per_source,
        longmemeval_limit=args.longmemeval_limit,
        memfail_limit=args.memfail_limit,
        poison_case_count=args.poison_case_count,
        poison_recall_size=args.poison_recall_size,
        poison_count=args.poison_count,
        poison_counts=(
            None
            if args.poison_counts is None
            else tuple(int(value) for value in args.poison_counts.split(","))
        ),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


__all__ = [
    "P4c1Plan",
    "P4C1_MANIFEST_SCHEMA",
    "P4C1_PROJECTION_SCHEMA",
    "SESSION_PROJECTION_SCHEMA",
    "build_p4c1_plan",
    "project_gold_free_session",
    "run_p4c1_zero_call",
]


if __name__ == "__main__":
    raise SystemExit(main())
