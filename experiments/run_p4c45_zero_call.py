"""Run the gold-free P4C-4 ablation and P4C-5 robustness matrix.

This is a structural, zero-model-call experiment.  ``without_ecc_gate`` is an
intentionally unsafe control and is never a deployable runtime configuration.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import random
from typing import Mapping

from cmd_audit.core.state_codec import atomic_json_write, content_sha256
from cmd_audit.repair.ecc import EccRepairReceipt, MemAuditEccAdapter
from cmd_audit.repair.ghost_ecology import (
    FailureDeposit,
    GHOSTEcologyRouter,
    PatternResponsibility,
    PatternRevision,
    RegistrySnapshot,
    SkillRevision,
)
from experiments.p4c_zero_call import StructuralEccEvaluator, StructuralMemoryStore
from experiments.run_p4c_zero_call_sweep import load_p4c_zero_call_scenarios
from experiments.v4_run_checkpoint import OutcomeJournal


CONFIG_SCHEMA = "cmd-p4c45-zero-call-config-v1"
REPORT_SCHEMA = "cmd-p4c45-zero-call-report-v1"
_ARMS = (
    "no_repair",
    "random_legal",
    "static_typed",
    "thompson_no_prior",
    "ghost_frozen_posterior",
    "ghost_receipt_evolution",
    "without_ecc_gate",
    "full_ghost_ecc",
)
_FORBIDDEN = ("gold", "label", "answer_replay", "same_trace")


@dataclass(frozen=True)
class _RobustCase:
    case_id: str
    observation: Mapping[str, object]
    state: Mapping[str, object]
    operator_kind: str
    mechanism: str
    poison_density: int
    recurrence_round: int
    locality_budget: float


@dataclass(frozen=True)
class _GhostContext:
    patterns: Mapping[str, PatternRevision]
    skills: Mapping[str, tuple[SkillRevision, SkillRevision]]
    registry: RegistrySnapshot


class _BudgetedEvaluator:
    def __init__(
        self,
        store: StructuralMemoryStore,
        *,
        locality_budget: float,
        recurrence: bool,
    ) -> None:
        self.base = StructuralEccEvaluator(store)
        self.locality_budget = locality_budget
        self.recurrence = recurrence

    def evaluate_ecc(self, syndrome: object, **roots: object) -> dict[str, object]:
        report = self.base.evaluate_ecc(syndrome, **roots)  # type: ignore[arg-type]
        exceeded = float(report["locality_cost"]) > self.locality_budget
        # A recurrence detected before commit is a parity/invariant failure.
        # The typed receipt reserves recurrence_after_commit for a later,
        # already-committed observation and cannot encode a speculative event.
        report["invariants_passed"] = bool(
            report["invariants_passed"] and not exceeded and not self.recurrence
        )
        report["recurrence_after_commit"] = False
        report["provenance"] = {
            **dict(report["provenance"]),
            "locality_budget": self.locality_budget,
            "locality_budget_exceeded": exceeded,
            "recurrence_probe": self.recurrence,
        }
        return report


def _load_config(path: Path) -> dict[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    def require_gold_free(item: object, location: str = "config") -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if any(marker in str(key).casefold() for marker in _FORBIDDEN):
                    raise ValueError(f"gold-free P4C-4/5 rejects {location}.{key}")
                require_gold_free(nested, f"{location}.{key}")
        elif isinstance(item, list):
            for index, nested in enumerate(item):
                require_gold_free(nested, f"{location}[{index}]")
        elif isinstance(item, str) and any(
            marker in item.casefold() for marker in _FORBIDDEN
        ):
            raise ValueError(f"gold-free P4C-4/5 rejects {location}")

    require_gold_free(value)
    fields = {
        "schema_version", "arms", "poison_density", "recurrence_rounds",
        "locality_budgets", "seed",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("P4C-4/5 config is not closed")
    if value["schema_version"] != CONFIG_SCHEMA or tuple(value["arms"]) != _ARMS:
        raise ValueError("P4C-4/5 config schema/arms mismatch")
    densities = value["poison_density"]
    budgets = value["locality_budgets"]
    if (
        not isinstance(densities, list)
        or not densities
        or any(isinstance(x, bool) or not isinstance(x, int) or x < 1 for x in densities)
        or not isinstance(budgets, list)
        or not budgets
        or any(isinstance(x, bool) or not isinstance(x, (int, float)) or x <= 0 for x in budgets)
        or isinstance(value["recurrence_rounds"], bool)
        or not isinstance(value["recurrence_rounds"], int)
        or value["recurrence_rounds"] < 1
    ):
        raise ValueError("P4C-4/5 robustness dimensions are invalid")
    return value


def _expand_cases(
    overlay_path: Path, config: Mapping[str, object]
) -> tuple[_RobustCase, ...]:
    frozen = load_p4c_zero_call_scenarios(overlay_path)
    rows: list[_RobustCase] = []
    for base in frozen.scenarios:
        syndrome = MemAuditEccAdapter().decode(base.case.observation)
        densities = (
            tuple(int(x) for x in config["poison_density"])
            if syndrome.mechanism.value == "adversarial_poison"
            else (1,)
        )
        operator_kind = str(next(iter(base.operators.values()))["kind"])
        for density in densities:
            for recurrence_round in range(1, int(config["recurrence_rounds"]) + 1):
                for budget in config["locality_budgets"]:
                    state = deepcopy(dict(base.state))
                    observation = deepcopy(dict(base.case.observation))
                    memories = state["memories"]
                    protected = state["protected_ids"]
                    assert isinstance(memories, dict) and isinstance(protected, list)
                    anchor = f"protected-{base.case.case_id}-{density}-{recurrence_round}-{budget}"
                    memories[anchor] = {"active": True}
                    protected.append(anchor)
                    if syndrome.mechanism.value == "adversarial_poison":
                        original = str(observation["suspect_ids"][0])
                        suspects = [original]
                        for index in range(2, density + 1):
                            suspect = f"{original}-{index}"
                            memories[suspect] = deepcopy(memories[original])
                            suspects.append(suspect)
                        observation["suspect_ids"] = suspects
                    suffix = f"d{density}-r{recurrence_round}-l{budget:g}"
                    case_id = f"{base.case.case_id}-{suffix}"
                    observation.update(
                        {
                            "observation_id": f"observation-{case_id}",
                            "incident_id": f"incident-{case_id}",
                            "observed_at_event_index": len(rows) * 2 + 100,
                            "state_root": content_sha256(
                                state, ensure_ascii=False, allow_nan=False
                            ),
                            "source_manifest_root": frozen.overlay_sha256,
                            "provenance": {
                                "detector": "structural-zero-call-v1",
                                "overlay": "p4c45-robustness-v1",
                            },
                        }
                    )
                    rows.append(
                        _RobustCase(
                            case_id=case_id,
                            observation=observation,
                            state=state,
                            operator_kind=operator_kind,
                            mechanism=syndrome.mechanism.value,
                            poison_density=density,
                            recurrence_round=recurrence_round,
                            locality_budget=float(budget),
                        )
                    )
    return tuple(rows)


def _choose(
    arm: str,
    case: _RobustCase,
    *,
    seed: int,
) -> str | None:
    repair = f"{case.mechanism}:repair"
    unsafe = f"{case.mechanism}:unsafe"
    if arm == "no_repair":
        return None
    if arm == "static_typed":
        return repair
    if arm == "without_ecc_gate":
        return unsafe
    if arm == "random_legal":
        rng = random.Random(f"{seed}:{arm}:{case.case_id}")
        return rng.choice((repair, unsafe))
    raise ValueError(f"unknown P4C-4 arm: {arm}")


def _build_ghost_context(seed: int) -> _GhostContext:
    mechanisms = ("process_fault", "state_drift", "adversarial_poison")
    patterns: dict[str, PatternRevision] = {}
    skills: dict[str, tuple[SkillRevision, SkillRevision]] = {}
    for mechanism in mechanisms:
        pattern = PatternRevision.create(
            pattern_id=f"p4c45:{mechanism}",
            predicate={"kind": "structural_syndrome", "mechanism": mechanism},
            feature_signature=(mechanism,),
            derivation_kind="seed",
            state="stable",
        )
        pair = tuple(
            SkillRevision.create(
                skill_id=f"p4c45:{mechanism}:{role}",
                program={"kind": "typed_repair_program", "role": role},
                parameter_schema={"type": "object", "additionalProperties": False},
                preconditions=({"predicate": pattern.pattern_id},),
                postconditions=({"predicate": "syndrome_resolved"},),
                success_probe={"probe_id": f"probe:p4c45:{role}", "kind": "ecc_parity"},
                mutation_budget={"max_locality_cost": 1.0},
                rollback_program={"kind": "restore_before_root"},
                producing_failure_id=f"failure-seed:{mechanism}",
                derivation_kind="seed",
                state="stable",
            )
            for role in ("repair", "unsafe")
        )
        patterns[mechanism] = pattern
        skills[mechanism] = pair  # type: ignore[assignment]
    registry = RegistrySnapshot.create(
        epoch=1,
        stable_pattern_revision_ids=tuple(
            pattern.pattern_revision_id for pattern in patterns.values()
        ),
        stable_skill_revision_ids=tuple(
            skill.skill_revision_id for pair in skills.values() for skill in pair
        ),
        config_sha256=content_sha256(
            {"experiment": "p4c45-zero-call-v1", "seed": seed}
        ),
    )
    return _GhostContext(patterns=patterns, skills=skills, registry=registry)


def _ghost_select(
    arm: str,
    case: _RobustCase,
    *,
    router: GHOSTEcologyRouter,
    context: _GhostContext,
) -> tuple[object, str, str]:
    pattern = context.patterns[case.mechanism]
    pair = context.skills[case.mechanism]
    failure = FailureDeposit(
        failure_id=f"failure-{case.case_id}",
        case_id=case.case_id,
        family_id_audit_only="p4c45-zero-call",
        failure_memory_sha256=content_sha256(
            {"case_id": case.case_id, "mechanism": case.mechanism}
        ),
        features=((case.mechanism, 1.0),),
        context_sha256=str(case.observation["state_root"]),
        provenance_sha256=str(case.observation["source_manifest_root"]),
    )
    priors: Mapping[str, float] | None
    if arm == "thompson_no_prior":
        priors = None
    elif arm == "ghost_receipt_evolution":
        priors = {
            pair[0].skill_revision_id: 0.0,
            pair[1].skill_revision_id: 0.15,
        }
    else:
        priors = {
            pair[0].skill_revision_id: 0.15,
            pair[1].skill_revision_id: 0.0,
        }
    decision = router.select(
        failure,
        pattern_responsibilities=(
            PatternResponsibility(pattern.pattern_revision_id, 1.0),
        ),
        skills=pair,
        registry=context.registry,
        event_index=int(case.observation["observed_at_event_index"]),
        skill_priors=priors,
    )
    roles = {
        pair[0].skill_revision_id: "repair",
        pair[1].skill_revision_id: "unsafe",
    }
    selected = decision.selected_skill_revision_id
    return decision, selected, roles[selected]


def _receipt_utility(receipt: EccRepairReceipt) -> float:
    if receipt.rolled_back or receipt.safety_violation or receipt.recurrence_after_commit:
        return -1.0
    return max(-1.0, min(1.0, float(receipt.resolved_syndrome) - receipt.locality_cost))


def _run_one(
    arm: str,
    case: _RobustCase,
    *,
    seed: int,
    ghost_router: GHOSTEcologyRouter | None,
    ghost_context: _GhostContext,
) -> dict[str, object]:
    syndrome = MemAuditEccAdapter().decode(case.observation)
    decision = None
    if arm in {
        "thompson_no_prior", "ghost_frozen_posterior",
        "ghost_receipt_evolution", "full_ghost_ecc",
    }:
        if ghost_router is None:
            raise ValueError("real GHOST arm requires GHOSTEcologyRouter")
        decision, selected, selected_role = _ghost_select(
            arm, case, router=ghost_router, context=ghost_context
        )
    else:
        selected = _choose(arm, case, seed=seed)
        selected_role = (
            None if selected is None else str(selected).rsplit(":", 1)[-1]
        )
    base = {
        "arm": arm,
        "case_id": case.case_id,
        "mechanism": case.mechanism,
        "poison_density": case.poison_density,
        "recurrence_round": case.recurrence_round,
        "locality_budget": case.locality_budget,
        "selected_candidate": selected,
        "selected_role": selected_role,
        "unsafe_control": arm == "without_ecc_gate",
    }
    if selected is None:
        return {
            **base,
            "resolved_syndrome": False,
            "committed": False,
            "rolled_back": False,
            "safety_violation": False,
            "recurrence_after_commit": case.recurrence_round > 1,
            "locality_cost": 0.0,
            "utility": 0.0,
            "receipt_sha256": None,
        }
    if decision is None:
        repair = f"{case.mechanism}:repair"
        unsafe = f"{case.mechanism}:unsafe"
    else:
        pair = ghost_context.skills[case.mechanism]
        repair = pair[0].skill_revision_id
        unsafe = pair[1].skill_revision_id
    store = StructuralMemoryStore(
        state=case.state,
        operators={
            repair: {"kind": case.operator_kind, "variant": "repair"},
            unsafe: {
                "kind": case.operator_kind,
                "variant": "unsafe_protected_mutation",
            },
        },
    )
    evaluator = _BudgetedEvaluator(
        store,
        locality_budget=case.locality_budget,
        recurrence=case.recurrence_round > 1,
    )
    selection_id = (
        str(decision.selection_id)
        if decision is not None
        else "selection-" + content_sha256(
            {"arm": arm, "case_id": case.case_id, "selected": selected}
        )
    )
    probe_id = (
        "probe:structural-p4c45"
        if decision is None
        else str(
            next(
                skill for skill in ghost_context.skills[case.mechanism]
                if skill.skill_revision_id == selected
            ).success_probe["probe_id"]
        )
    )
    if arm != "without_ecc_gate":
        receipt = MemAuditEccAdapter().execute_shadow_repair(
            syndrome,
            selection_id=selection_id,
            selected_skill_revision_id=selected,
            probe_id=probe_id,
            observed_after_event_index=syndrome.observed_at_event_index + 1,
            store=store,
            evaluator=evaluator,
        )
    else:
        before = store.snapshot_root()
        store.apply_shadow(syndrome, selected)
        shadow = store.snapshot_root()
        report = evaluator.evaluate_ecc(
            syndrome, before_root=before, shadow_root=shadow
        )
        store.commit_shadow()
        # Deliberately not an EccRepairReceipt: that ABI correctly refuses to
        # represent a safety-violating commit.  This hash binds the explicit
        # unsafe control outcome without laundering it into router feedback.
        control = {
            **base,
            "resolved_syndrome": bool(report["resolved_syndrome"]),
            "committed": True,
            "rolled_back": False,
            "safety_violation": bool(report["safety_violation"]),
            "recurrence_after_commit": case.recurrence_round > 1,
            "locality_cost": float(report["locality_cost"]),
            "utility": -1.0,
            "receipt_sha256": None,
            "unsafe_control_outcome_sha256": content_sha256(
                {
                    "selection_id": selection_id,
                    "syndrome_sha256": syndrome.content_hash,
                    "before_root": before,
                    "after_root": shadow,
                    "safety_violation": report["safety_violation"],
                }
            ),
        }
        return control
    utility = _receipt_utility(receipt)
    if decision is not None and arm != "ghost_frozen_posterior":
        assert ghost_router is not None
        ghost_router.observe(decision, receipt)
    return {
        **base,
        "resolved_syndrome": receipt.resolved_syndrome,
        "committed": receipt.committed,
        "rolled_back": receipt.rolled_back,
        "safety_violation": receipt.safety_violation,
        "recurrence_after_commit": receipt.recurrence_after_commit,
        "locality_cost": receipt.locality_cost,
        "utility": utility,
        "receipt_sha256": receipt.content_hash,
        "unsafe_control_outcome_sha256": None,
        "router_snapshot_sha256": (
            None
            if ghost_router is None
            else str(ghost_router.snapshot["snapshot_sha256"])
        ),
    }


def run_p4c45_zero_call(
    *,
    overlay_path: Path,
    config_path: Path,
    output_dir: Path,
    run_mode: str = "fresh",
    stop_after: int | None = None,
) -> dict[str, object]:
    """Execute or resume the frozen P4C-4/5 zero-call experiment."""
    if run_mode not in {"fresh", "resume"}:
        raise ValueError("run_mode must be fresh or resume")
    config = _load_config(config_path)
    cases = _expand_cases(overlay_path, config)
    stream = [(arm, case) for arm in _ARMS for case in cases]
    stream_root = content_sha256(
        [
            {
                "arm": arm,
                "case_id": case.case_id,
                "observation": dict(case.observation),
                "locality_budget": case.locality_budget,
            }
            for arm, case in stream
        ],
        ensure_ascii=False,
        allow_nan=False,
    )
    identity = {
        "schema_version": CONFIG_SCHEMA,
        "overlay_sha256": content_sha256(Path(overlay_path).read_bytes().hex()),
        "config_sha256": content_sha256(config),
        "case_stream_sha256": stream_root,
    }
    output_dir = Path(output_dir)
    identity_path = output_dir / "run_identity.json"
    if run_mode == "fresh":
        if output_dir.exists() and any(output_dir.iterdir()):
            raise ValueError("fresh P4C-4/5 run refuses non-empty output")
        output_dir.mkdir(parents=True, exist_ok=True)
        atomic_json_write(identity_path, identity, indent=2, trailing_newline=True)
    else:
        if not identity_path.exists() or json.loads(identity_path.read_text()) != identity:
            raise ValueError("P4C-4/5 resume identity mismatch")
    journal = OutcomeJournal(output_dir / "outcomes.jsonl")
    if len(journal.events) > len(stream):
        raise ValueError("P4C-4/5 outcome prefix exceeds case stream")
    ghost_context = _build_ghost_context(int(config["seed"]))
    ghost_arms = {
        "thompson_no_prior", "ghost_frozen_posterior",
        "ghost_receipt_evolution", "full_ghost_ecc",
    }
    routers = {
        arm: GHOSTEcologyRouter(seed=int(config["seed"]), exploration=0.08)
        for arm in ghost_arms
    }
    for position, event in enumerate(journal.events):
        arm, case = stream[position]
        row = event["rows"][0]
        if event["case_id"] != f"{arm}:{case.case_id}" or row["arm"] != arm:
            raise ValueError("P4C-4/5 outcome prefix binding mismatch")
        replayed = _run_one(
            arm,
            case,
            seed=int(config["seed"]),
            ghost_router=routers.get(arm),
            ghost_context=ghost_context,
        )
        if replayed != row:
            raise ValueError("P4C-4/5 deterministic resume replay mismatch")
    completed_this_run = 0
    for position in range(len(journal.events), len(stream)):
        arm, case = stream[position]
        row = _run_one(
            arm,
            case,
            seed=int(config["seed"]),
            ghost_router=routers.get(arm),
            ghost_context=ghost_context,
        )
        journal.append(position + 1, f"{arm}:{case.case_id}", [row])
        completed_this_run += 1
        if stop_after is not None and completed_this_run >= stop_after:
            raise RuntimeError("injected stop after durable outcome")
    rows = [event["rows"][0] for event in journal.events]
    arm_reports: dict[str, object] = {}
    for arm in _ARMS:
        chosen = [row for row in rows if row["arm"] == arm]
        count = len(chosen)
        arm_reports[arm] = {
            "case_count": count,
            "unsafe_control": arm == "without_ecc_gate",
            "syndrome_resolution_rate": sum(bool(x["resolved_syndrome"]) for x in chosen) / count,
            "commit_rate": sum(bool(x["committed"]) for x in chosen) / count,
            "rollback_rate": sum(bool(x["rolled_back"]) for x in chosen) / count,
            "safety_violation_rate": sum(bool(x["safety_violation"]) for x in chosen) / count,
            "recurrence_rate": sum(bool(x["recurrence_after_commit"]) for x in chosen) / count,
            "recurrence_probe_rate": sum(int(x["recurrence_round"]) > 1 for x in chosen) / count,
            "mean_locality_cost": sum(float(x["locality_cost"]) for x in chosen) / count,
            "locality_rejection_count": sum(
                bool(x["rolled_back"])
                and float(x["locality_cost"]) > float(x["locality_budget"])
                for x in chosen
            ),
            "unsafe_commit_count": sum(bool(x["committed"]) and bool(x["safety_violation"]) for x in chosen),
            "unsafe_selection_rate": sum(
                x["selected_role"] == "unsafe"
                for x in chosen
            ) / count,
            "typed_receipt_count": sum(x["receipt_sha256"] is not None for x in chosen),
            "unsafe_control_outcome_count": sum(
                x.get("unsafe_control_outcome_sha256") is not None for x in chosen
            ),
            "mean_receipt_utility": sum(float(x["utility"]) for x in chosen) / count,
        }
    report: dict[str, object] = {
        "schema_version": REPORT_SCHEMA,
        "status": "success",
        "case_count": len(cases),
        "outcome_count": len(rows),
        "model_call_count": 0,
        "external_call_count": 0,
        "runtime_uses_gold": False,
        "runtime_uses_labels": False,
        "same_trace_answer_replay": False,
        "paper_role": "mainline",
        "primary_claim": "gold-free memory fault correction and evolution",
        "router_feedback_channel": (
            "EccRepairReceipt-only for adaptive ECC arms; unsafe no-gate control excluded"
        ),
        "router_implementation": "GHOSTEcologyRouter",
        "router_modes": {
            "thompson_no_prior": "skill_priors=None; observe typed receipt",
            "ghost_frozen_posterior": "explicit priors; select only; no observe",
            "ghost_receipt_evolution": "explicit priors; observe typed receipt",
            "full_ghost_ecc": "explicit priors; observe typed receipt; ECC gate",
        },
        "case_stream_sha256": stream_root,
        "outcome_root": journal.head,
        "arms": arm_reports,
        "robustness_coverage": {
            "poison_density": list(config["poison_density"]),
            "recurrence_rounds": config["recurrence_rounds"],
            "protected_mutation": True,
            "crash_resume": True,
            "locality_budgets": list(config["locality_budgets"]),
        },
        "claim_scope": "post-detection structural router/ECC ablation and robustness only",
    }
    atomic_json_write(
        output_dir / "p4c45_manifest.json",
        report,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        trailing_newline=True,
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overlay", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-mode", choices=("fresh", "resume"), default="fresh")
    parser.add_argument("--stop-after", type=int)
    args = parser.parse_args(argv)
    report = run_p4c45_zero_call(
        overlay_path=args.overlay,
        config_path=args.config,
        output_dir=args.output_dir,
        run_mode=args.run_mode,
        stop_after=args.stop_after,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
