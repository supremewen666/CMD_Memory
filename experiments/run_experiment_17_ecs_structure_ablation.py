#!/usr/bin/env python3
"""Experiment 17: ECS structure ablation.

Isolates context presentation while holding the repair point and corrected
content fixed. The corrected content is built by the same gold-free
``apply_pipeline_action`` path used by the repair-efficacy runner; no arm
constructs context by copying ``case.gold_*``.

Run after vLLM is up:
    python -m experiments.run_experiment_17_ecs_structure_ablation
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.labels import PIPELINE_STEP_ACTIONS
from cmd_audit.data_io import load_probe_cases
from cmd_audit.eval.writers import is_timeout_value, write_csv_table
from cmd_audit.counterfactual.actions import apply_pipeline_action
from cmd_audit.repair.actions import get_targeted_repair_action_v1
from cmd_audit.repair.efficacy import run_single_repair
from cmd_audit.scoring import score_answer_with_verifier
from experiments.experiment_runner_common import (
    DATA,
    OUT,
    AGENT_SYSTEM_PROMPT,
    assert_g_eval_available,
    build_answer_verifier,
    build_evidence_scorer,
)
from experiments.experiment_runner_common import build_clients
from experiments.probe_exhaustive import _evaluate_case


ARMS = ("raw_corrected", "corrected_only", "solution", "full_ecs", "cause_only")


@dataclass(frozen=True)
class ArmResult:
    answer_score: float
    evidence_score: float
    recovered: bool
    answer_band: str
    evidence_band: str
    token_cost: float
    regression_risk: float
    answer: str


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "real_multihop_cases.json"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-credit", type=float, default=0.05)
    parser.add_argument("--recovered-threshold", type=float, default=0.1)
    parser.add_argument("--answer-recovered-threshold", type=float, default=0.8)
    parser.add_argument("--partial-threshold", type=float, default=0.5)
    args = parser.parse_args()

    client, judge_client = build_clients()
    assert_g_eval_available(judge_client, role="ecs-structure-ablation")
    answer_verifier = build_answer_verifier(
        judge_client, answer_mode="answer-rubric"
    )
    evidence_scorer = build_evidence_scorer(
        judge_client, scorer_mode="g-eval-hybrid"
    )

    cases = [
        c
        for c in load_probe_cases(args.cases)
        if c.perturbation_label in PIPELINE_STEP_ACTIONS
    ]
    if args.limit:
        cases = cases[: args.limit]

    from cmd_audit.harness import _initial_mcts_context, _retrieved_memory_items

    detail_rows: list[dict[str, str]] = []
    print(f"ECS structure ablation over {len(cases)} candidate cases\n")
    included = 0
    excluded = 0
    for i, case in enumerate(cases, start=1):
        recall_set = _retrieved_memory_items(case)
        max_depth = max(1, min(3, len(recall_set) or 1))
        base_ctx = _initial_mcts_context(case, recall_set)
        cfg = {"candidate_items": case.extracted_memory, "raw_events": case.raw_events}

        baseline_gain = run_single_repair(
            case,
            None,
            client=client,
            answer_verifier=answer_verifier,
            base_context=base_ctx,
            recall_set=recall_set,
            max_depth=max_depth,
            intervention_config=cfg,
        ).recovery_gain
        if is_timeout_value(baseline_gain):
            excluded += 1
            detail_rows.extend(_excluded_rows(case, "base_gain_timeout"))
            print(
                f"  [{i}/{len(cases)}] {case.perturbation_label:20s} "
                "EXCLUDED (identity-backbone rollout timed out)"
            )
            continue
        _credits, culprit = _evaluate_case(
            case,
            client,
            answer_verifier,
            min_credit=args.min_credit,
        )
        if culprit is None:
            excluded += 1
            detail_rows.extend(_excluded_rows(case, "no_recoverable_single_point"))
            print(f"  [{i}/{len(cases)}] {case.perturbation_label:20s} excluded")
            continue

        gp, action, credit = culprit
        repair_check = run_single_repair(
            case,
            (gp, action),
            client=client,
            answer_verifier=answer_verifier,
            base_context=base_ctx,
            recall_set=recall_set,
            max_depth=max_depth,
            intervention_config=cfg,
        )
        if is_timeout_value(repair_check.recovery_gain):
            excluded += 1
            detail_rows.extend(_excluded_rows(case, "repair_check_timeout"))
            print(
                f"  [{i}/{len(cases)}] {case.perturbation_label:20s} "
                "excluded (repair-check rollout timed out)"
            )
            continue
        if repair_check.recovery_gain - baseline_gain <= args.recovered_threshold:
            excluded += 1
            detail_rows.extend(_excluded_rows(case, "single_point_below_recovery_threshold"))
            print(f"  [{i}/{len(cases)}] {case.perturbation_label:20s} excluded")
            continue

        included += 1
        corrected_context = apply_pipeline_action(
            action,
            base_ctx,
            recall_set,
            gp,
            intervention_config=cfg,
        )
        repair_action = get_targeted_repair_action_v1(action.value)
        contexts = _arm_contexts(
            base_context=base_ctx,
            corrected_context=corrected_context,
            label=action.value,
            cause=repair_action.cause,
            repair_guidance=repair_action.repair_guidance,
        )
        for arm in ARMS:
            result = _run_arm(
                client,
                case,
                contexts[arm],
                answer_verifier=answer_verifier,
                evidence_scorer=evidence_scorer,
                answer_recovered_threshold=args.answer_recovered_threshold,
                partial_threshold=args.partial_threshold,
            )
            detail_rows.append({
                "case_id": case.case_id,
                "gold_label": case.perturbation_label,
                "included": "true",
                "exclude_reason": "",
                "culprit_gen_point": str(gp),
                "culprit_action": action.value,
                "culprit_credit": f"{credit:.4f}",
                "arm": arm,
                "answer_score": f"{result.answer_score:.4f}",
                "evidence_score": f"{result.evidence_score:.4f}",
                "recovered": str(result.recovered).lower(),
                "answer_band": result.answer_band,
                "evidence_band": result.evidence_band,
                "token_cost": f"{result.token_cost:.2f}",
                "regression_risk": f"{result.regression_risk:.4f}",
                "answer": result.answer.replace("\n", " ")[:500],
            })
        print(
            f"  [{i}/{len(cases)}] {case.perturbation_label:20s} "
            f"culprit=gp{gp}:{action.value} included"
        )

    detail_path = write_csv_table(
        OUT / "ecs_structure_ablation_detail.csv",
        _detail_fieldnames(),
        detail_rows,
        sandbox_root=OUT,
        judge_client=judge_client,
    )
    summary_rows = _summary_rows(detail_rows)
    summary_path = write_csv_table(
        OUT / "ecs_structure_ablation_summary.csv",
        _summary_fieldnames(),
        summary_rows,
        sandbox_root=OUT,
        judge_client=judge_client,
    )
    _print_summary(summary_rows, included=included, excluded=excluded)
    print(f"\nWrote {detail_path}")
    print(f"Wrote {summary_path}")


def _arm_contexts(
    *,
    base_context: str,
    corrected_context: str,
    label: str,
    cause: str,
    repair_guidance: str,
) -> dict[str, str]:
    wrong_cause = (
        f"[Error]\nThe previous memory context failed under {label}.\n\n"
        f"[Cause]\n{cause}\n\n"
        f"[Wrong Context Pointer]\n{_wrong_context_pointer(base_context)}"
    )
    return {
        "raw_corrected": "\n\n".join((
            base_context,
            "[Corrected Evidence]",
            corrected_context,
        )),
        "corrected_only": corrected_context,
        "solution": "\n\n".join((
            "[Corrected Memory]",
            corrected_context,
            "[Repair Guidance]",
            repair_guidance,
        )),
        "full_ecs": "\n\n".join((
            wrong_cause,
            "[Corrected Memory]",
            corrected_context,
            "[Repair Guidance]",
            repair_guidance,
        )),
        "cause_only": wrong_cause,
    }


def _run_arm(
    client,
    case,
    context: str,
    *,
    answer_verifier,
    evidence_scorer,
    answer_recovered_threshold: float,
    partial_threshold: float,
) -> ArmResult:
    answer = _agent_answer(client, case.query, context)
    answer_score = score_answer_with_verifier(answer_verifier, answer, case.gold_answer)
    evidence_score = evidence_scorer(case.gold_evidence, answer)
    recovered = answer_score >= answer_recovered_threshold
    answer_band = _score_band(
        answer_score,
        full_threshold=answer_recovered_threshold,
        partial_threshold=partial_threshold,
    )
    evidence_band = _score_band(
        evidence_score,
        full_threshold=1.0,
        partial_threshold=partial_threshold,
    )
    return ArmResult(
        answer_score=answer_score,
        evidence_score=evidence_score,
        recovered=recovered,
        answer_band=answer_band,
        evidence_band=evidence_band,
        token_cost=(len(context) + len(case.query)) / 4.0,
        regression_risk=_regression_risk(case, context),
        answer=answer,
    )


def _agent_answer(client, query: str, context: str) -> str:
    prompt = "\n\n".join((
        "CONTEXT:",
        context or "(empty)",
        "QUERY:",
        query,
        "ANSWER:",
    ))
    response = client.generate(prompt, system=AGENT_SYSTEM_PROMPT)
    return response.strip() if response else ""


def _regression_risk(case, context: str) -> float:
    baseline = case.primary_baseline.injected_context.strip()
    if not baseline:
        return 0.0
    return 0.0 if baseline in context else 1.0


def _wrong_context_pointer(base_context: str) -> str:
    """Describe the failed context without reinjecting its content."""
    stripped = base_context.strip()
    if not stripped:
        return "The failed context was empty."
    line_count = len(stripped.splitlines())
    return (
        "The failed retrieved-memory buffer is withheld to avoid replay "
        f"pollution; length_chars={len(stripped)}, line_count={line_count}."
    )


def _score_band(
    score: float,
    *,
    full_threshold: float,
    partial_threshold: float,
) -> str:
    if score >= full_threshold:
        return "full"
    if score >= partial_threshold:
        return "partial"
    return "failed"


def _excluded_rows(case, reason: str) -> list[dict[str, str]]:
    rows = []
    for arm in ARMS:
        rows.append({
            "case_id": case.case_id,
            "gold_label": case.perturbation_label,
            "included": "false",
            "exclude_reason": reason,
            "culprit_gen_point": "",
            "culprit_action": "",
            "culprit_credit": "",
            "arm": arm,
            "answer_score": "",
            "evidence_score": "",
            "recovered": "false",
            "answer_band": "failed",
            "evidence_band": "failed",
            "token_cost": "",
            "regression_risk": "",
            "answer": "",
        })
    return rows


def _summary_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    included = [row for row in rows if row["included"] == "true"]
    out = []
    for arm in ARMS:
        arm_rows = [row for row in included if row["arm"] == arm]
        total = len(arm_rows)
        recovered = sum(1 for row in arm_rows if row["recovered"] == "true")
        answer_partial = sum(1 for row in arm_rows if row["answer_band"] == "partial")
        evidence_partial = sum(1 for row in arm_rows if row["evidence_band"] == "partial")
        evidence_full = sum(1 for row in arm_rows if row["evidence_band"] == "full")
        out.append({
            "arm": arm,
            "included_cases": str(total),
            "recovered": str(recovered),
            "recovery_rate": _rate(recovered, total),
            "answer_partial": str(answer_partial),
            "evidence_partial": str(evidence_partial),
            "evidence_full": str(evidence_full),
            "avg_answer_score": _avg(arm_rows, "answer_score"),
            "avg_evidence_score": _avg(arm_rows, "evidence_score"),
            "avg_token_cost": _avg(arm_rows, "token_cost"),
            "avg_regression_risk": _avg(arm_rows, "regression_risk"),
        })
    return out


def _detail_fieldnames() -> list[str]:
    return [
        "case_id",
        "gold_label",
        "included",
        "exclude_reason",
        "culprit_gen_point",
        "culprit_action",
        "culprit_credit",
        "arm",
        "answer_score",
        "evidence_score",
        "recovered",
        "answer_band",
        "evidence_band",
        "token_cost",
        "regression_risk",
        "answer",
    ]


def _summary_fieldnames() -> list[str]:
    return [
        "arm",
        "included_cases",
        "recovered",
        "recovery_rate",
        "answer_partial",
        "evidence_partial",
        "evidence_full",
        "avg_answer_score",
        "avg_evidence_score",
        "avg_token_cost",
        "avg_regression_risk",
    ]


def _rate(num: int, den: int) -> str:
    return f"{(num / den if den else 0.0):.4f}"


def _avg(rows: list[dict[str, str]], key: str) -> str:
    if not rows:
        return "0.0000"
    return f"{(sum(float(row[key]) for row in rows) / len(rows)):.4f}"


def _print_summary(rows: list[dict[str, str]], *, included: int, excluded: int) -> None:
    print("\n=== ECS structure ablation summary ===")
    print(f"included={included} excluded={excluded}")
    print(
        f"{'arm':18s} {'recovered':>9s} {'partial':>8s} {'ev_part':>8s} "
        f"{'ev_full':>7s} {'total':>6s} {'rate':>8s} {'ans':>8s} {'ev':>8s}"
    )
    for row in rows:
        print(
            f"{row['arm']:18s} {row['recovered']:>9s} "
            f"{row['answer_partial']:>8s} {row['evidence_partial']:>8s} "
            f"{row['evidence_full']:>7s} "
            f"{row['included_cases']:>6s} {row['recovery_rate']:>8s} "
            f"{row['avg_answer_score']:>8s} {row['avg_evidence_score']:>8s}"
        )


if __name__ == "__main__":
    main()
