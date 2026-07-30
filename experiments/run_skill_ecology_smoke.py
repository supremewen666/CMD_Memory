#!/usr/bin/env python3
"""Small CPU structural smoke for the skill-ecology implementation.

This deliberately does not estimate repair recovery or any paper headline
metric.  It loads real repository probe records, applies typed operators to
their concrete recalled contexts, and checks deterministic arm isolation.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
import time
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.models import MemoryItem
from cmd_audit.counterfactual.actions import PipelineAction
from cmd_audit.counterfactual.operators import OperatorSpec, apply_operator_static
from cmd_audit.repair.skill_ecology import SkillCandidate, SkillExecution
from experiments.ecology_runner_common import (
    EcologyCase,
    SkillEcologyExperimentRunner,
)


DEFAULT_DATA = Path("data/probe_cases/real_recurrent_cases.json")
SMOKE_ARMS = (
    "no_update",
    "random_skill",
    "fixed_library",
    "single_top1",
    "competitive_topk",
    "lamarckian",
    "darwinian_global",
    "darwinian_niche",
)
SMOKE_ACTIONS = (
    PipelineAction.RETRIEVAL_ERROR,
    PipelineAction.INJECTION_ERROR,
    PipelineAction.GRANULARITY_ERROR,
)


@dataclass(frozen=True)
class SmokeRecord:
    case: EcologyCase
    recall_set: tuple[MemoryItem, ...]
    candidate_items: tuple[MemoryItem, ...]
    raw_events: tuple[Any, ...]


def _load_records(path: Path, limit: int) -> tuple[SmokeRecord, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("smoke dataset must be a JSON list")
    records: list[SmokeRecord] = []
    for raw in payload[:limit]:
        memory = tuple(
            MemoryItem.from_mapping(item)
            for item in raw.get("extracted_memory", ())
        )
        by_id = {item.memory_id: item for item in memory}
        baseline = (raw.get("baseline_outputs") or [{}])[0]
        recalled = tuple(
            by_id[memory_id]
            for memory_id in baseline.get("retrieved_memory_ids", ())
            if memory_id in by_id
        )
        context = str(baseline.get("injected_context", ""))
        records.append(
            SmokeRecord(
                case=EcologyCase(
                    case_id=str(raw["case_id"]),
                    failure_type=str(raw.get("perturbation_label") or "unknown"),
                    base_context=context,
                ),
                recall_set=recalled,
                candidate_items=memory,
                raw_events=(),
            )
        )
    return tuple(records)


def _run_once(
    records: Sequence[SmokeRecord],
    *,
    seed: int,
) -> tuple[object, dict[str, int], int]:
    candidates = tuple(
        SkillCandidate(
            skill_id=f"static:{action.value}",
            operator=OperatorSpec.single(0, action),
        )
        for action in SMOKE_ACTIONS
    )
    by_case = {record.case.case_id: record for record in records}
    state = {arm: 0 for arm in SMOKE_ARMS}
    executions = 0

    def provider(_arm_id: str, _case: EcologyCase):
        return candidates

    def evaluator(candidate: SkillCandidate, context: str) -> SkillExecution:
        nonlocal executions
        executions += 1
        record = by_case[current_case_id[0]]
        repaired = apply_operator_static(
            context,
            record.recall_set,
            candidate.operator,
            intervention_config={
                "candidate_items": record.candidate_items,
            },
        )
        # Structural applicability only: this is intentionally not recovery.
        changed = repaired != context
        return SkillExecution(
            skill_id=candidate.skill_id,
            operator=candidate.operator,
            repaired_context=repaired,
            recovery_gain=1.0 if changed else 0.0,
            execution_cost=1.0,
            success=changed,
            status="structural_smoke",
        )

    def update(arm_id: str, _case: EcologyCase, _outcome: object) -> None:
        state[arm_id] += 1

    current_case_id = [""]

    def tracking_provider(arm_id: str, case: EcologyCase):
        current_case_id[0] = case.case_id
        return provider(arm_id, case)

    result = SkillEcologyExperimentRunner(
        tuple(record.case for record in records),
        candidate_provider=tracking_provider,
        evaluator=evaluator,
        post_case_updater=update,
        state_fingerprint=lambda arm_id: str(state[arm_id]),
        arms=SMOKE_ARMS,
        top_k=3,
        seed=seed,
    ).run()
    return result, state, executions


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--seed", type=int, default=24)
    args = parser.parse_args()
    if args.limit <= 0:
        raise SystemExit("--limit must be > 0")

    started = time.perf_counter()
    records = _load_records(args.data, args.limit)
    first, first_state, first_executions = _run_once(records, seed=args.seed)
    second, second_state, second_executions = _run_once(records, seed=args.seed)
    deterministic = first == second
    mutable_expected = len(records)
    mutable_ok = all(
        first_state[arm] == mutable_expected
        for arm in ("lamarckian", "darwinian_global", "darwinian_niche")
    )
    frozen_ok = all(
        first_state[arm] == 0
        for arm in set(SMOKE_ARMS)
        - {"lamarckian", "darwinian_global", "darwinian_niche"}
    )
    leakage_ok = all(value for _name, value in first.leakage_assertions)
    elapsed = time.perf_counter() - started

    print("[RESULT] smoke_kind=structural_not_recovery")
    print(f"[RESULT] repository_cases={len(records)}")
    print(f"[RESULT] candidate_executions={first_executions}")
    print(f"[RESULT] deterministic_replay={int(deterministic)}")
    print(f"[RESULT] mutable_update_boundary={int(mutable_ok)}")
    print(f"[RESULT] frozen_arm_isolation={int(frozen_ok and leakage_ok)}")
    print(f"[RESULT] repeated_execution_count_match={int(first_executions == second_executions)}")
    print(f"[RESULT] elapsed_seconds={elapsed:.6f}")
    print("[RESULT] device=cpu")
    return 0 if deterministic and mutable_ok and frozen_ok and leakage_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
