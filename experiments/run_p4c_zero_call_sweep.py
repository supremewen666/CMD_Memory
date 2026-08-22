"""Freeze and execute the formal P4C-0 three-mechanism zero-call sweep."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

from cmd_audit.core.state_codec import atomic_json_write, content_sha256
from cmd_audit.repair.ghost_ecology import (
    EcologyLedger,
    FailureDeposit,
    GHOSTEcologyRouter,
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


OVERLAY_SCHEMA_VERSION = "cmd-p4c-zero-call-scenario-v1"
_ROW_FIELDS = frozenset(
    {
        "schema_version",
        "case_id",
        "observed_at_event_index",
        "repair_event_index",
        "observation",
        "state",
        "operator",
        "ghost",
        "scenario_source_root",
    }
)
_OBSERVATION_FIELDS = frozenset(
    {
        "observation_id",
        "incident_id",
        "process_fault_subtype",
        "observed_order",
        "superseding_memory_id",
        "superseded_memory_id",
        "cas_anomaly",
        "influence_anomaly",
        "suspect_ids",
        "signal_ids",
        "provenance",
    }
)
_OPERATOR_FIELDS = frozenset({"kind", "skill_id", "probe_id"})
_GHOST_FIELDS = frozenset({"pattern_id", "feature_signature", "features"})
_FORBIDDEN = ("gold", "label", "answer_replay", "same_trace")


@dataclass(frozen=True)
class FrozenP4cZeroCallPlan:
    scenarios: tuple[P4cZeroCallScenario, ...]
    failures: tuple[FailureDeposit, ...]
    patterns: tuple[PatternRevision, ...]
    skills: tuple[SkillRevision, ...]
    overlay_sha256: str


def _require_gold_free(value: object, path: str = "overlay") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if any(marker in str(key).casefold() for marker in _FORBIDDEN):
                raise ValueError(f"P4C-0 gold-free overlay rejects {path}.{key}")
            _require_gold_free(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _require_gold_free(nested, f"{path}[{index}]")
    elif isinstance(value, str) and any(
        marker in value.casefold() for marker in _FORBIDDEN
    ):
        raise ValueError(f"P4C-0 gold-free overlay rejects {path}")


def _scenario_source_root(row: Mapping[str, object]) -> str:
    return content_sha256(
        {key: value for key, value in row.items() if key != "scenario_source_root"},
        ensure_ascii=False,
        allow_nan=False,
    )


def load_p4c_zero_call_scenarios(path: Path) -> FrozenP4cZeroCallPlan:
    """Load a closed frozen overlay and derive root-bound P4C/GHOST records."""
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid P4C-0 overlay JSON at line {line_number}"
            ) from exc
        if (
            not isinstance(row, dict)
            or set(row) != _ROW_FIELDS
            or row.get("schema_version") != OVERLAY_SCHEMA_VERSION
        ):
            raise ValueError(f"P4C-0 overlay line {line_number} is not closed")
        _require_gold_free(row, f"overlay[{line_number}]")
        if row["scenario_source_root"] != _scenario_source_root(row):
            raise ValueError(f"P4C-0 source root mismatch at line {line_number}")
        rows.append(row)
    if not rows:
        raise ValueError("P4C-0 overlay is empty")

    scenarios: list[P4cZeroCallScenario] = []
    failures: list[FailureDeposit] = []
    patterns: list[PatternRevision] = []
    skills: list[SkillRevision] = []
    for row in rows:
        observation = row["observation"]
        operator = row["operator"]
        ghost = row["ghost"]
        state = row["state"]
        if not isinstance(observation, dict) or set(observation) != _OBSERVATION_FIELDS:
            raise ValueError("P4C-0 observation seed is not closed")
        if not isinstance(operator, dict) or set(operator) != _OPERATOR_FIELDS:
            raise ValueError("P4C-0 operator seed is not closed")
        if not isinstance(ghost, dict) or set(ghost) != _GHOST_FIELDS:
            raise ValueError("P4C-0 GHOST seed is not closed")
        if not isinstance(state, dict):
            raise ValueError("P4C-0 state seed must be a mapping")
        case_id = str(row["case_id"])
        failure = FailureDeposit(
            failure_id=f"failure-{case_id}",
            case_id=case_id,
            family_id_audit_only="p4c-zero-call",
            failure_memory_sha256=content_sha256(
                {"case_id": case_id, "signals": observation["signal_ids"]}
            ),
            features=tuple(
                sorted(
                    (str(key), float(value))
                    for key, value in ghost["features"].items()
                )
            ),
            context_sha256=content_sha256(
                state, ensure_ascii=False, allow_nan=False
            ),
            provenance_sha256=str(row["scenario_source_root"]),
        )
        pattern = PatternRevision.create(
            pattern_id=str(ghost["pattern_id"]),
            predicate={
                "kind": "structural_syndrome",
                "requires": list(observation["signal_ids"]),
            },
            feature_signature=tuple(ghost["feature_signature"]),
            derivation_kind="seed",
            state="stable",
        )
        skill = SkillRevision.create(
            skill_id=str(operator["skill_id"]),
            program={
                "kind": "typed_repair_program",
                "operator_kind": operator["kind"],
            },
            parameter_schema={"type": "object", "additionalProperties": False},
            preconditions=({"predicate": pattern.pattern_id},),
            postconditions=({"predicate": "syndrome_resolved"},),
            success_probe={
                "probe_id": str(operator["probe_id"]),
                "kind": "ecc_parity",
            },
            mutation_budget={"max_locality_cost": 1.0},
            rollback_program={"kind": "restore_before_root"},
            producing_failure_id=failure.failure_id,
            derivation_kind="seed",
            state="stable",
        )
        runtime_observation = {
            **observation,
            "observed_at_event_index": row["observed_at_event_index"],
            "state_root": content_sha256(
                state, ensure_ascii=False, allow_nan=False
            ),
            "source_manifest_root": row["scenario_source_root"],
        }
        case = P4cEccCase(
            case_id=case_id,
            event_index=row["repair_event_index"],
            observation=runtime_observation,
            candidates=(
                P4cRepairCandidate(
                    skill_revision_id=skill.skill_revision_id,
                    probe_id=str(operator["probe_id"]),
                    operator_sha256=skill.program_sha256,
                ),
            ),
        )
        scenarios.append(
            P4cZeroCallScenario(
                case,
                state=state,
                operators={
                    skill.skill_revision_id: {"kind": str(operator["kind"])}
                },
            )
        )
        failures.append(failure)
        patterns.append(pattern)
        skills.append(skill)
    if len({scenario.case.case_id for scenario in scenarios}) != len(scenarios):
        raise ValueError("P4C-0 overlay case IDs must be unique")
    if tuple(s.case.event_index for s in scenarios) != tuple(
        sorted(s.case.event_index for s in scenarios)
    ):
        raise ValueError("P4C-0 repair events must be ordered")
    return FrozenP4cZeroCallPlan(
        scenarios=tuple(scenarios),
        failures=tuple(failures),
        patterns=tuple(patterns),
        skills=tuple(skills),
        overlay_sha256=content_sha256(
            rows, ensure_ascii=False, allow_nan=False
        ),
    )


def run_p4c_zero_call_sweep(
    *, overlay_path: Path, output_dir: Path
) -> dict[str, object]:
    """Execute a fresh formal sweep with a durable real GHOST ecology."""
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("fresh P4C-0 sweep refuses a non-empty output directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    plan = load_p4c_zero_call_scenarios(overlay_path)
    ecology = GhostEcology(EcologyLedger(output_dir / "ecology.jsonl"))
    for index, (failure, pattern, skill) in enumerate(
        zip(plan.failures, plan.patterns, plan.skills, strict=True)
    ):
        base = index * 5 + 1
        responsibility = PatternResponsibility(pattern.pattern_revision_id, 1.0)
        ecology.deposit_failure(failure, event_index=base)
        ecology.propose_pattern(pattern, event_index=base + 1)
        ecology.bind_failure(
            failure.failure_id, (responsibility,), event_index=base + 2
        )
        ecology.propose_skill(skill, event_index=base + 3)
        ecology.bind_pattern_skill(
            pattern.pattern_revision_id,
            skill.skill_revision_id,
            applicability=1.0,
            event_index=base + 4,
        )
    registry = RegistrySnapshot.create(
        epoch=1,
        stable_pattern_revision_ids=tuple(
            pattern.pattern_revision_id for pattern in plan.patterns
        ),
        stable_skill_revision_ids=tuple(
            skill.skill_revision_id for skill in plan.skills
        ),
        config_sha256=content_sha256(
            {
                "overlay_sha256": plan.overlay_sha256,
                "router": "GHOSTEcologyRouter",
                "feedback": "EccRepairReceipt",
            }
        ),
    )
    ecology.freeze_registry(registry, event_index=16)
    router = P4cGhostRouter(
        ecology,
        {
            scenario.case.case_id: P4cGhostBinding(
                failure_id=failure.failure_id,
                responsibilities=(
                    PatternResponsibility(pattern.pattern_revision_id, 1.0),
                ),
                registry_id=registry.registry_id,
            )
            for scenario, failure, pattern in zip(
                plan.scenarios, plan.failures, plan.patterns, strict=True
            )
        },
    )
    runtime_report = P4cZeroCallSuite(
        plan.scenarios,
        output_dir=output_dir / "runtime",
        router=router,
    ).run()
    result: dict[str, object] = {
        **runtime_report,
        "schema_version": "cmd-p4c-zero-call-sweep-v1",
        "router": "P4cGhostRouter",
        "router_feedback": "EccRepairReceipt",
        "overlay_sha256": plan.overlay_sha256,
        "registry_id": registry.registry_id,
        "ecology_head_sha256": ecology.ledger.head_sha256,
        "ecology_event_count": len(ecology.ledger.events),
        "router_snapshot_sha256": router.snapshot_sha256,
        "runtime_report_sha256": content_sha256(
            runtime_report, ensure_ascii=False, allow_nan=False
        ),
    }
    atomic_json_write(
        output_dir / "sweep_manifest.json",
        result,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        trailing_newline=True,
    )
    return result


def run_p4c_zero_call_prior_calibration(
    *, overlay_path: Path, output_dir: Path
) -> dict[str, object]:
    """Give every mixed-GHOST candidate three receipt-only support events."""
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("fresh prior calibration refuses a non-empty output directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    frozen = load_p4c_zero_call_scenarios(overlay_path)
    ecology = GhostEcology(
        EcologyLedger(output_dir / "ecology.jsonl"),
        router=GHOSTEcologyRouter(
            seed=24,
            exploration=0.0,
            min_pattern_support=3.0,
            min_local_support=3.0,
        ),
    )
    failures: list[FailureDeposit] = []
    patterns: list[PatternRevision] = []
    skill_pairs: list[tuple[SkillRevision, SkillRevision]] = []
    for scenario, frozen_pattern in zip(
        frozen.scenarios, frozen.patterns, strict=True
    ):
        operator_kind = str(next(iter(scenario.operators.values()))["kind"])
        failure = FailureDeposit(
            failure_id=f"mix-failure-{scenario.case.case_id}",
            case_id=scenario.case.case_id,
            family_id_audit_only="p4c-zero-call-prior-calibration",
            failure_memory_sha256=content_sha256(
                {"case_id": scenario.case.case_id, "kind": operator_kind}
            ),
            features=(("structural-channel", 1.0),),
            context_sha256=str(scenario.case.observation["state_root"]),
            provenance_sha256=frozen.overlay_sha256,
        )
        common = {
            "parameter_schema": {"type": "object", "additionalProperties": False},
            "preconditions": ({"predicate": frozen_pattern.pattern_id},),
            "postconditions": ({"predicate": "syndrome_resolved"},),
            "mutation_budget": {"max_locality_cost": 1.0},
            "rollback_program": {"kind": "restore_before_root"},
            "producing_failure_id": failure.failure_id,
            "derivation_kind": "seed",
            "state": "stable",
        }
        repair = SkillRevision.create(
            skill_id=f"{operator_kind}-repair",
            program={
                "kind": "typed_repair_program",
                "operator_kind": operator_kind,
                "variant": "repair",
            },
            success_probe={
                "probe_id": f"probe:{operator_kind}:repair",
                "kind": "ecc_parity",
            },
            **common,
        )
        unsafe = SkillRevision.create(
            skill_id=f"{operator_kind}-guard-control",
            program={
                "kind": "typed_repair_program",
                "operator_kind": operator_kind,
                "variant": "unsafe_protected_mutation",
            },
            success_probe={
                "probe_id": f"probe:{operator_kind}:guard-control",
                "kind": "ecc_parity",
            },
            **common,
        )
        failures.append(failure)
        patterns.append(frozen_pattern)
        skill_pairs.append((repair, unsafe))

    for index, (failure, pattern, pair) in enumerate(
        zip(failures, patterns, skill_pairs, strict=True)
    ):
        base = index * 7 + 1
        responsibility = PatternResponsibility(pattern.pattern_revision_id, 1.0)
        ecology.deposit_failure(failure, event_index=base)
        ecology.propose_pattern(pattern, event_index=base + 1)
        ecology.bind_failure(
            failure.failure_id, (responsibility,), event_index=base + 2
        )
        ecology.propose_skill(pair[0], event_index=base + 3)
        ecology.bind_pattern_skill(
            pattern.pattern_revision_id,
            pair[0].skill_revision_id,
            applicability=1.0,
            event_index=base + 4,
        )
        ecology.propose_skill(pair[1], event_index=base + 5)
        ecology.bind_pattern_skill(
            pattern.pattern_revision_id,
            pair[1].skill_revision_id,
            applicability=1.0,
            event_index=base + 6,
        )
    registry = RegistrySnapshot.create(
        epoch=1,
        stable_pattern_revision_ids=tuple(
            pattern.pattern_revision_id for pattern in patterns
        ),
        stable_skill_revision_ids=tuple(
            skill.skill_revision_id for pair in skill_pairs for skill in pair
        ),
        config_sha256=content_sha256(
            {
                "overlay_sha256": frozen.overlay_sha256,
                "calibration": "two-candidates-three-receipts-per-candidate",
            }
        ),
    )
    ecology.freeze_registry(registry, event_index=22)

    scenarios: list[P4cZeroCallScenario] = []
    bindings: dict[str, P4cGhostBinding] = {}
    sequence = 0
    for base_scenario, failure, pattern, pair in zip(
        frozen.scenarios, failures, patterns, skill_pairs, strict=True
    ):
        operator_kind = str(next(iter(base_scenario.operators.values()))["kind"])
        for round_index in range(6):
            state = deepcopy(dict(base_scenario.state))
            memories = state["memories"]
            protected = state["protected_ids"]
            assert isinstance(memories, dict) and isinstance(protected, list)
            observation = dict(base_scenario.case.observation)
            old_id = observation.get("superseded_memory_id")
            new_id = observation.get("superseding_memory_id")
            if isinstance(old_id, str) and isinstance(new_id, str):
                unique_old = f"{old_id}-{round_index + 1}"
                unique_new = f"{new_id}-{round_index + 1}"
                memories[unique_old] = memories.pop(old_id)
                memories[unique_new] = memories.pop(new_id)
                observation["observed_order"] = [unique_old, unique_new]
                observation["superseded_memory_id"] = unique_old
                observation["superseding_memory_id"] = unique_new
            anchor_id = f"anchor-{base_scenario.case.case_id}-{round_index + 1}"
            memories[anchor_id] = {"active": True}
            protected.append(anchor_id)
            case_id = f"{base_scenario.case.case_id}-prior-{round_index + 1}"
            observed = 100 + sequence * 3
            observation.update(
                {
                    "observation_id": f"observation-{case_id}",
                    "incident_id": f"incident-{case_id}",
                    "observed_at_event_index": observed,
                    "state_root": content_sha256(
                        state, ensure_ascii=False, allow_nan=False
                    ),
                    "provenance": {
                        "detector": "structural-zero-call-v1",
                        "calibration": "predeclared-prior-coverage-v1",
                    },
                }
            )
            case = P4cEccCase(
                case_id=case_id,
                event_index=observed + 1,
                observation=observation,
                candidates=tuple(
                    P4cRepairCandidate(
                        skill_revision_id=skill.skill_revision_id,
                        probe_id=str(skill.success_probe["probe_id"]),
                        operator_sha256=skill.program_sha256,
                    )
                    for skill in pair
                ),
            )
            scenarios.append(
                P4cZeroCallScenario(
                    case,
                    state=state,
                    operators={
                        pair[0].skill_revision_id: {
                            "kind": operator_kind,
                            "variant": "repair",
                        },
                        pair[1].skill_revision_id: {
                            "kind": operator_kind,
                            "variant": "unsafe_protected_mutation",
                        },
                    },
                )
            )
            favored = 0 if round_index < 3 else 1
            bindings[case_id] = P4cGhostBinding(
                failure_id=failure.failure_id,
                responsibilities=(
                    PatternResponsibility(pattern.pattern_revision_id, 1.0),
                ),
                registry_id=registry.registry_id,
                skill_priors=tuple(
                    (skill.skill_revision_id, 1.0 if index == favored else -1.0)
                    for index, skill in enumerate(pair)
                ),
            )
            sequence += 1
    router = P4cGhostRouter(ecology, bindings)
    runtime = P4cZeroCallSuite(
        scenarios,
        output_dir=output_dir / "runtime",
        router=router,
    ).run()
    candidate_ids = {
        skill.skill_revision_id for pair in skill_pairs for skill in pair
    }
    supports: dict[str, dict[str, float]] = {
        skill_id: {
            "global": float("inf"),
            "pattern": float("inf"),
            "local": float("inf"),
        }
        for skill_id in candidate_ids
    }
    for raw_key, precision, _natural in ecology.router.snapshot["stats"]:
        key = tuple(str(item) for item in raw_key)
        skill_id = key[-1]
        if skill_id in supports:
            supports[skill_id][key[0]] = min(
                supports[skill_id].get(key[0], float("inf")),
                float(precision) - 1.0,
            )
    feedback_counts = {skill_id: 0 for skill_id in candidate_ids}
    selections = ecology.ledger.by_type("selection")
    for row in ecology.ledger.by_type("skill_feedback"):
        feedback_counts[str(row["payload"]["selected_skill_revision_id"])] += 1
    prior_coverage = all(
        len(row["payload"].get("skill_priors", [])) == 2 for row in selections
    )
    global_ready = all(row["global"] >= 1.0 for row in supports.values())
    pattern_ready = all(
        row["pattern"] >= ecology.router.min_pattern_support
        for row in supports.values()
    )
    local_ready = all(
        row["local"] >= ecology.router.min_local_support
        for row in supports.values()
    )
    result: dict[str, object] = {
        **runtime,
        "schema_version": "cmd-p4c-zero-call-prior-calibration-v1",
        "candidate_count_per_mechanism": 2,
        "receipts_per_candidate": min(feedback_counts.values()),
        "prior_coverage_complete": prior_coverage,
        "global_support_ready": global_ready,
        "pattern_support_ready": pattern_ready,
        "local_support_ready": local_ready,
        "mix_ghost_ready": bool(
            prior_coverage and global_ready and pattern_ready and local_ready
        ),
        "candidate_support": supports,
        "feedback_counts": feedback_counts,
        "overlay_sha256": frozen.overlay_sha256,
        "ecology_head_sha256": ecology.ledger.head_sha256,
        "router_snapshot_sha256": router.snapshot_sha256,
    }
    atomic_json_write(
        output_dir / "prior_calibration_manifest.json",
        result,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        trailing_newline=True,
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overlay", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=("mechanism", "prior-calibration"),
        default="mechanism",
    )
    args = parser.parse_args(argv)
    runner = (
        run_p4c_zero_call_sweep
        if args.mode == "mechanism"
        else run_p4c_zero_call_prior_calibration
    )
    result = runner(overlay_path=args.overlay, output_dir=args.output_dir)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


__all__ = [
    "FrozenP4cZeroCallPlan",
    "load_p4c_zero_call_scenarios",
    "run_p4c_zero_call_prior_calibration",
    "run_p4c_zero_call_sweep",
]


if __name__ == "__main__":
    raise SystemExit(main())
