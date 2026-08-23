"""P4C-4/5 v2: corrected ECC metrics and receipt-only prequential routing.

The stream is structural and zero-call.  Calibration/adaptation receipts may
update adaptive routers; the sealed holdout is evaluated without updates.
No task label, answer, oracle, or answer replay is admitted at runtime.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass, replace
import json
from pathlib import Path
import random
from statistics import fmean
from typing import Mapping

from cmd_audit.core.state_codec import atomic_json_write, content_sha256
from cmd_audit.repair.ecc import EccRepairReceipt, MemAuditEccAdapter
from cmd_audit.repair.ghost_ecology import (
    FailureDeposit,
    GHOSTEcologyRouter,
    PatternResponsibility,
)
from experiments.p4c_zero_call import StructuralMemoryStore
from experiments.run_p4c45_zero_call import _BudgetedEvaluator, _build_ghost_context
from experiments.run_p4c_zero_call_sweep import load_p4c_zero_call_scenarios
from experiments.v4_run_checkpoint import OutcomeJournal


CONFIG_SCHEMA = "cmd-p4c45-prequential-config-v2"
OUTCOME_SCHEMA = "cmd-p4c45-prequential-outcome-v2"
REPORT_SCHEMA = "cmd-p4c45-prequential-report-v2"
ARMS = (
    "no_repair",
    "random_legal",
    "static_typed",
    "ghost_zero_frozen",
    "ghost_zero_evolution",
    "ghost_typed_prior_frozen",
    "ghost_typed_prior_evolution",
    "without_ecc_gate",
)
PHASES = ("calibration", "adaptation", "holdout")
_FORBIDDEN = ("gold", "label", "answer", "oracle", "replay")


@dataclass(frozen=True)
class _Case:
    case_id: str
    observation: Mapping[str, object]
    state: Mapping[str, object]
    operator_kind: str
    mechanism: str
    poison_density: int
    repeat_challenge_round: int
    locality_budget: float
    replicate: int
    phase: str


def _reject_forbidden(value: object, location: str = "config") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if any(token in str(key).casefold() for token in _FORBIDDEN):
                raise ValueError(f"P4C-4/5 v2 rejects {location}.{key}")
            _reject_forbidden(nested, f"{location}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_forbidden(nested, f"{location}[{index}]")
    elif isinstance(value, str) and any(
        token in value.casefold() for token in _FORBIDDEN
    ):
        raise ValueError(f"P4C-4/5 v2 rejects {location}")


def _load_config(path: Path) -> dict[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    _reject_forbidden(value)
    fields = {
        "schema_version",
        "arms",
        "poison_density",
        "repeat_challenge_rounds",
        "locality_budgets",
        "phase_replicates",
        "seed",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("P4C-4/5 v2 config must be closed")
    if value["schema_version"] != CONFIG_SCHEMA or tuple(value["arms"]) != ARMS:
        raise ValueError("P4C-4/5 v2 schema/arms mismatch")
    densities = value["poison_density"]
    budgets = value["locality_budgets"]
    phases = value["phase_replicates"]
    if (
        not isinstance(densities, list)
        or not densities
        or any(isinstance(x, bool) or not isinstance(x, int) or x < 1 for x in densities)
        or not isinstance(budgets, list)
        or not budgets
        or any(isinstance(x, bool) or not isinstance(x, (int, float)) or x <= 0 for x in budgets)
        or not isinstance(phases, Mapping)
        or set(phases) != set(PHASES)
        or any(isinstance(x, bool) or not isinstance(x, int) or x < 1 for x in phases.values())
        or isinstance(value["repeat_challenge_rounds"], bool)
        or not isinstance(value["repeat_challenge_rounds"], int)
        or value["repeat_challenge_rounds"] < 1
        or isinstance(value["seed"], bool)
        or not isinstance(value["seed"], int)
    ):
        raise ValueError("P4C-4/5 v2 robustness dimensions are invalid")
    return value


def _expand_cases(overlay_path: Path, config: Mapping[str, object]) -> tuple[_Case, ...]:
    frozen = load_p4c_zero_call_scenarios(overlay_path)
    phase_replicates = {name: int(config["phase_replicates"][name]) for name in PHASES}
    replicate_phase: dict[int, str] = {}
    cursor = 0
    for phase in PHASES:
        for _ in range(phase_replicates[phase]):
            cursor += 1
            replicate_phase[cursor] = phase
    rows: list[_Case] = []
    for base in frozen.scenarios:
        syndrome = MemAuditEccAdapter().decode(base.case.observation)
        densities = (
            tuple(int(x) for x in config["poison_density"])
            if syndrome.mechanism.value == "adversarial_poison"
            else (1,)
        )
        operator_kind = str(next(iter(base.operators.values()))["kind"])
        for density in densities:
            for repeat_round in range(1, int(config["repeat_challenge_rounds"]) + 1):
                for budget in config["locality_budgets"]:
                    for replicate, phase in replicate_phase.items():
                        state = deepcopy(dict(base.state))
                        observation = deepcopy(dict(base.case.observation))
                        memories = state["memories"]
                        protected = state["protected_ids"]
                        assert isinstance(memories, dict) and isinstance(protected, list)
                        suffix = f"d{density}-q{repeat_round}-l{float(budget):g}-x{replicate}"
                        case_id = f"{base.case.case_id}-{suffix}"
                        anchor = f"protected-{case_id}"
                        memories[anchor] = {"active": True}
                        protected.append(anchor)
                        if syndrome.mechanism.value == "adversarial_poison":
                            original = str(observation["suspect_ids"][0])
                            suspects = [original]
                            for index in range(2, density + 1):
                                suspect = f"{original}-{index}-{replicate}"
                                memories[suspect] = deepcopy(memories[original])
                                suspects.append(suspect)
                            observation["suspect_ids"] = suspects
                        observation.update(
                            {
                                "observation_id": f"observation-{case_id}",
                                "incident_id": f"incident-{case_id}",
                                "observed_at_event_index": 0,
                                "state_root": content_sha256(state, ensure_ascii=False, allow_nan=False),
                                "source_manifest_root": frozen.overlay_sha256,
                                "provenance": {
                                    "detector": "structural-zero-call-v2",
                                    "overlay": "p4c45-prequential-v2",
                                    "phase": phase,
                                },
                            }
                        )
                        rows.append(
                            _Case(
                                case_id,
                                observation,
                                state,
                                operator_kind,
                                syndrome.mechanism.value,
                                density,
                                repeat_round,
                                float(budget),
                                replicate,
                                phase,
                            )
                        )
    phase_index = {name: index for index, name in enumerate(PHASES)}
    rows.sort(
        key=lambda row: (
            phase_index[row.phase],
            row.replicate,
            row.mechanism,
            row.case_id,
        )
    )
    bound: list[_Case] = []
    for index, row in enumerate(rows):
        observation = dict(row.observation)
        observation["observed_at_event_index"] = 1000 + index * 2
        bound.append(replace(row, observation=observation))
    return tuple(bound)


def _ghost_selection(
    arm: str,
    case: _Case,
    *,
    router: GHOSTEcologyRouter,
    context: object,
) -> tuple[object, str, str]:
    pattern = context.patterns[case.mechanism]
    pair = context.skills[case.mechanism]
    failure = FailureDeposit(
        failure_id=f"failure-{case.case_id}",
        case_id=case.case_id,
        family_id_audit_only="p4c45-prequential-v2",
        failure_memory_sha256=content_sha256(
            {"case_id": case.case_id, "mechanism": case.mechanism}
        ),
        features=((f"context:{case.mechanism}:d{case.poison_density}:l{case.locality_budget:g}", 1.0),),
        context_sha256=str(case.observation["state_root"]),
        provenance_sha256=str(case.observation["source_manifest_root"]),
    )
    typed_prior = "typed_prior" in arm
    priors = (
        {pair[0].skill_revision_id: 0.15, pair[1].skill_revision_id: 0.0}
        if typed_prior
        else None
    )
    decision = router.select(
        failure,
        pattern_responsibilities=(PatternResponsibility(pattern.pattern_revision_id, 1.0),),
        skills=pair,
        registry=context.registry,
        event_index=int(case.observation["observed_at_event_index"]) + 1,
        skill_priors=priors,
    )
    role_by_id = {
        pair[0].skill_revision_id: "repair",
        pair[1].skill_revision_id: "unsafe",
    }
    selected = decision.selected_skill_revision_id
    return decision, selected, role_by_id[selected]


def _rollback_reason(
    *,
    resolved: bool,
    invariants: bool,
    safety: bool,
    locality_exceeded: bool,
    repeat_challenge: bool,
) -> str | None:
    if safety:
        return "safety"
    if not resolved:
        return "unresolved"
    if locality_exceeded:
        return "locality"
    if repeat_challenge and not invariants:
        return "repeat_challenge"
    if not invariants:
        return "invariant"
    return None


def _run_one(
    arm: str,
    case: _Case,
    *,
    seed: int,
    router: GHOSTEcologyRouter | None,
    context: object,
) -> dict[str, object]:
    syndrome = MemAuditEccAdapter().decode(case.observation)
    decision = None
    if arm.startswith("ghost_"):
        if router is None:
            raise ValueError("GHOST v2 arm requires a router")
        decision, selected, selected_role = _ghost_selection(
            arm, case, router=router, context=context
        )
    elif arm == "no_repair":
        selected, selected_role = None, None
    elif arm == "static_typed":
        selected, selected_role = f"{case.mechanism}:repair", "repair"
    elif arm == "without_ecc_gate":
        selected, selected_role = f"{case.mechanism}:unsafe", "unsafe"
    elif arm == "random_legal":
        selected_role = random.Random(f"{seed}:{case.case_id}").choice(("repair", "unsafe"))
        selected = f"{case.mechanism}:{selected_role}"
    else:
        raise ValueError(f"unknown P4C-4/5 v2 arm: {arm}")
    repeat_challenge = case.repeat_challenge_round > 1
    base = {
        "schema_version": OUTCOME_SCHEMA,
        "arm": arm,
        "case_id": case.case_id,
        "phase": case.phase,
        "mechanism": case.mechanism,
        "poison_density": case.poison_density,
        "repeat_challenge_round": case.repeat_challenge_round,
        "repeat_incident_challenge": repeat_challenge,
        "locality_budget": case.locality_budget,
        "replicate": case.replicate,
        "selected_candidate": selected,
        "selected_role": selected_role,
        "repair_attempted": selected is not None,
        "selection_id": None if decision is None else decision.selection_id,
        "posterior_before_sha256": None if decision is None else decision.posterior_before_sha256,
    }
    if selected is None:
        return {
            **base,
            "shadow_syndrome_resolved": False,
            "invariants_passed": False,
            "ecc_accepted": False,
            "committed": False,
            "rolled_back": False,
            "safe_committed_resolution": False,
            "unresolved_after_transition": True,
            "safety_violation": False,
            "unsafe_commit": False,
            "locality_cost": 0.0,
            "locality_budget_exceeded": False,
            "rollback_reason": None,
            "typed_receipt_sha256": None,
            "typed_receipt_reward": None,
            "router_feedback_written": False,
            "posterior_after_sha256": None,
            "unsafe_control_outcome_sha256": None,
        }
    if decision is None:
        repair = f"{case.mechanism}:repair"
        unsafe = f"{case.mechanism}:unsafe"
    else:
        pair = context.skills[case.mechanism]
        repair, unsafe = pair[0].skill_revision_id, pair[1].skill_revision_id
    store = StructuralMemoryStore(
        state=case.state,
        operators={
            repair: {"kind": case.operator_kind, "variant": "repair"},
            unsafe: {"kind": case.operator_kind, "variant": "unsafe_protected_mutation"},
        },
    )
    evaluator = _BudgetedEvaluator(
        store,
        locality_budget=case.locality_budget,
        recurrence=repeat_challenge,
    )
    selection_id = (
        decision.selection_id
        if decision is not None
        else "selection-" + content_sha256(
            {"arm": arm, "case_id": case.case_id, "selected": selected}
        )
    )
    probe_id = (
        "probe:structural-p4c45-v2"
        if decision is None
        else str(
            next(
                skill
                for skill in context.skills[case.mechanism]
                if skill.skill_revision_id == selected
            ).success_probe["probe_id"]
        )
    )
    before = store.snapshot_root()
    if arm == "without_ecc_gate":
        store.apply_shadow(syndrome, selected)
        shadow = store.snapshot_root()
        report = evaluator.evaluate_ecc(syndrome, before_root=before, shadow_root=shadow)
        store.commit_shadow()
        locality = float(report["locality_cost"])
        exceeded = locality > case.locality_budget
        safety = bool(report["safety_violation"])
        return {
            **base,
            "selection_id": selection_id,
            "shadow_syndrome_resolved": bool(report["resolved_syndrome"]),
            "invariants_passed": bool(report["invariants_passed"]),
            "ecc_accepted": False,
            "committed": True,
            "rolled_back": False,
            "safe_committed_resolution": False,
            "unresolved_after_transition": True,
            "safety_violation": safety,
            "unsafe_commit": safety,
            "locality_cost": locality,
            "locality_budget_exceeded": exceeded,
            "rollback_reason": None,
            "typed_receipt_sha256": None,
            "typed_receipt_reward": None,
            "router_feedback_written": False,
            "posterior_after_sha256": None,
            "unsafe_control_outcome_sha256": content_sha256(
                {
                    "selection_id": selection_id,
                    "before_root": before,
                    "after_root": shadow,
                    "safety_violation": safety,
                }
            ),
        }
    receipt = MemAuditEccAdapter().execute_shadow_repair(
        syndrome,
        selection_id=selection_id,
        selected_skill_revision_id=selected,
        probe_id=probe_id,
        observed_after_event_index=syndrome.observed_at_event_index + 2,
        store=store,
        evaluator=evaluator,
    )
    assert isinstance(receipt, EccRepairReceipt)
    exceeded = receipt.locality_cost > case.locality_budget
    feedback_written = bool(
        decision is not None
        and "evolution" in arm
        and case.phase != "holdout"
    )
    posterior_after = None
    if feedback_written:
        assert router is not None and decision is not None
        posterior_after = str(router.observe_receipt(decision, receipt)["snapshot_sha256"])
    elif router is not None:
        posterior_after = str(router.snapshot["snapshot_sha256"])
    safe_resolution = bool(
        receipt.committed
        and receipt.resolved_syndrome
        and receipt.invariants_passed
        and not receipt.safety_violation
    )
    return {
        **base,
        "selection_id": selection_id,
        "shadow_syndrome_resolved": receipt.resolved_syndrome,
        "invariants_passed": receipt.invariants_passed,
        "ecc_accepted": receipt.committed,
        "committed": receipt.committed,
        "rolled_back": receipt.rolled_back,
        "safe_committed_resolution": safe_resolution,
        "unresolved_after_transition": not safe_resolution,
        "safety_violation": receipt.safety_violation,
        "unsafe_commit": receipt.committed and receipt.safety_violation,
        "locality_cost": receipt.locality_cost,
        "locality_budget_exceeded": exceeded,
        "rollback_reason": (
            _rollback_reason(
                resolved=receipt.resolved_syndrome,
                invariants=receipt.invariants_passed,
                safety=receipt.safety_violation,
                locality_exceeded=exceeded,
                repeat_challenge=repeat_challenge,
            )
            if receipt.rolled_back
            else None
        ),
        "typed_receipt_sha256": receipt.content_hash,
        "typed_receipt_reward": receipt.reward,
        "router_feedback_written": feedback_written,
        "posterior_after_sha256": posterior_after,
        "unsafe_control_outcome_sha256": None,
    }


def _rate(numerator: int, denominator: int) -> dict[str, object]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": None if denominator == 0 else numerator / denominator,
    }


def _summarize(rows: list[Mapping[str, object]]) -> dict[str, object]:
    incidents = len(rows)
    attempted = [row for row in rows if row["repair_attempted"] is True]
    typed = [row for row in rows if row["typed_receipt_sha256"] is not None]
    committed = [row for row in rows if row["committed"] is True]
    repeats = [row for row in rows if row["repeat_incident_challenge"] is True]
    feedback = [row for row in rows if row["router_feedback_written"] is True]
    safe = sum(row["safe_committed_resolution"] is True for row in rows)
    mechanism_rates = []
    for mechanism in ("process_fault", "state_drift", "adversarial_poison"):
        selected = [row for row in rows if row["mechanism"] == mechanism]
        if selected:
            mechanism_rates.append(
                sum(row["safe_committed_resolution"] is True for row in selected)
                / len(selected)
            )
    return {
        "incident_case_count": incidents,
        "repair_attempt_count": len(attempted),
        "typed_receipt_count": len(typed),
        "committed_count": len(committed),
        "router_update_count": len(feedback),
        "safe_correction_rate": _rate(safe, incidents),
        "shadow_resolution_rate": _rate(
            sum(row["shadow_syndrome_resolved"] is True for row in attempted),
            len(attempted),
        ),
        "ecc_acceptance_rate": _rate(
            sum(row["committed"] is True for row in typed), len(typed)
        ),
        "rollback_rate": _rate(
            sum(row["rolled_back"] is True for row in typed), len(typed)
        ),
        "unresolved_after_transition_rate": _rate(
            sum(row["unresolved_after_transition"] is True for row in rows), incidents
        ),
        "candidate_safety_violation_rate": _rate(
            sum(row["safety_violation"] is True for row in attempted), len(attempted)
        ),
        "unsafe_commit_per_incident_rate": _rate(
            sum(row["unsafe_commit"] is True for row in rows), incidents
        ),
        "unsafe_commit_per_commit_rate": _rate(
            sum(row["unsafe_commit"] is True for row in committed), len(committed)
        ),
        "repeat_challenge_containment_rate": _rate(
            sum(row["rolled_back"] is True for row in repeats), len(repeats)
        ),
        "mean_locality_cost_attempted": (
            None if not attempted else fmean(float(row["locality_cost"]) for row in attempted)
        ),
        "mean_locality_cost_committed": (
            None if not committed else fmean(float(row["locality_cost"]) for row in committed)
        ),
        "mean_typed_receipt_reward": (
            None if not typed else fmean(float(row["typed_receipt_reward"]) for row in typed)
        ),
        "mean_router_feedback_reward": (
            None if not feedback else fmean(float(row["typed_receipt_reward"]) for row in feedback)
        ),
        "mechanism_macro_safe_correction_rate": (
            None if not mechanism_rates else fmean(mechanism_rates)
        ),
    }


def run_p4c45_prequential_v2(
    *,
    overlay_path: Path,
    config_path: Path,
    output_dir: Path,
    run_mode: str = "fresh",
    stop_after: int | None = None,
) -> dict[str, object]:
    if run_mode not in {"fresh", "resume"}:
        raise ValueError("run_mode must be fresh or resume")
    config = _load_config(config_path)
    cases = _expand_cases(overlay_path, config)
    stream = [(arm, case) for arm in ARMS for case in cases]
    stream_root = content_sha256(
        [
            {
                "arm": arm,
                "case_id": case.case_id,
                "phase": case.phase,
                "observation": dict(case.observation),
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
            raise ValueError("fresh P4C-4/5 v2 refuses non-empty output")
        output_dir.mkdir(parents=True, exist_ok=True)
        atomic_json_write(identity_path, identity, indent=2, trailing_newline=True)
    elif not identity_path.exists() or json.loads(identity_path.read_text()) != identity:
        raise ValueError("P4C-4/5 v2 resume identity mismatch")
    journal = OutcomeJournal(output_dir / "outcomes.jsonl")
    if len(journal.events) > len(stream):
        raise ValueError("P4C-4/5 v2 outcome prefix exceeds stream")
    context = _build_ghost_context(int(config["seed"]))
    ghost_arms = {arm for arm in ARMS if arm.startswith("ghost_")}
    routers = {
        arm: GHOSTEcologyRouter(seed=int(config["seed"]), exploration=0.08)
        for arm in ghost_arms
    }
    for position, event in enumerate(journal.events):
        arm, case = stream[position]
        replayed = _run_one(
            arm,
            case,
            seed=int(config["seed"]),
            router=routers.get(arm),
            context=context,
        )
        if event["case_id"] != f"{arm}:{case.case_id}" or event["rows"][0] != replayed:
            raise ValueError("P4C-4/5 v2 deterministic resume mismatch")
    completed = 0
    for position in range(len(journal.events), len(stream)):
        arm, case = stream[position]
        row = _run_one(
            arm,
            case,
            seed=int(config["seed"]),
            router=routers.get(arm),
            context=context,
        )
        journal.append(position + 1, f"{arm}:{case.case_id}", [row])
        completed += 1
        if stop_after is not None and completed >= stop_after:
            raise RuntimeError("injected stop after durable outcome")
    rows = [event["rows"][0] for event in journal.events]
    arms: dict[str, object] = {}
    for arm in ARMS:
        selected = [row for row in rows if row["arm"] == arm]
        arms[arm] = {
            "overall": _summarize(selected),
            "phases": {
                phase: _summarize([row for row in selected if row["phase"] == phase])
                for phase in PHASES
            },
            "holdout_router_updates": sum(
                row["router_feedback_written"] is True
                for row in selected
                if row["phase"] == "holdout"
            ),
        }
    phase_counts = {
        phase: sum(case.phase == phase for case in cases) for phase in PHASES
    }
    report: dict[str, object] = {
        "schema_version": REPORT_SCHEMA,
        "supersedes_schema_version": "cmd-p4c45-zero-call-report-v1",
        "status": "success",
        "case_count": len(cases),
        "outcome_count": len(rows),
        "phase_case_counts": phase_counts,
        "model_call_count": 0,
        "external_call_count": 0,
        "runtime_uses_gold": False,
        "runtime_uses_labels": False,
        "same_trace_answer_replay": False,
        "paper_role": "mainline",
        "primary_claim": "gold-free memory fault correction and evolution",
        "router_feedback_channel": "GHOSTEcologyRouter.observe_receipt(EccRepairReceipt) only",
        "router_implementation": "GHOSTEcologyRouter",
        "holdout_update_policy": "frozen_no_observe",
        "case_stream_sha256": stream_root,
        "outcome_root": journal.head,
        "arms": arms,
        "metric_semantics": {
            "primary": "safe_committed_resolution_per_incident",
            "shadow_resolution": "proposal_only_not_final_correction",
            "repeat_challenge": "precommit_invariant_stress_not_postcommit_recurrence",
            "utility": "no_cross_arm_scalar_ranking; typed receipt reward reported on typed denominator only",
        },
        "evidence_units": {
            "base_structural_templates": 3,
            "scenario_variant_count": len(cases),
            "independent_real_source_cases": 0,
            "warning": "replicated robustness cells are not independent real-source cases",
        },
        "claim_scope": "prequential structural router/ECC evidence; not task accuracy",
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
    report = run_p4c45_prequential_v2(
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
