#!/usr/bin/env python3
"""Experiment 21: does a RICHER repair operator recover the single-point residual?

THE GATE for the evolvable-skill direction (see SKILL_EVOLUTION_DESIGN.md). On the
multihop residual -- cases where the best single-point structural repair fails
(0% are a data floor; the gold-evidence item is always in store) -- this scans
operators richer than a single (gp, action):

  single        best single-point (gp, action)            -- the residual baseline (~0 by construction)
  double        best two-point composite (a0@gp0, a1@gp1)  -- compose existing operators
  param         best single + per-item promote/demote      -- parameterized via item_signal_hints
  richer        best of {double, param, double+param}      -- the operator-evolution ceiling

Recovery uses the SAME rollout path that DEFINED the residual (probe_exhaustive
_step_context + _own_recovery), so "recovered" is faithful: net gain over the
identity backbone > --recovered-threshold (matches Exp17's residual rule).

HEADROOM = cases recovered by `richer` but NOT by `single`. If headroom is real,
operator evolution can extend what is fixable and the skill library is worth
building. If ~0 (C6 coupled 1/30 cautions), evolution falls back to reuse/efficiency.

Secondary (folded in per request): EC-on/off. For the best operator, compare a
static answer over [Error][Cause] + corrected vs corrected alone (Exp17/20 path),
to test whether the EC frame still adds value once the corrected content is operator
-produced (clean isolation of EC; Exp17's full_ecs>solution +0.104 was the EC block).

Gold-free construction (operators read recall/store metadata + text, never gold);
recovery-gain fitness; NO label is a prediction target.

Run (needs a logprob-capable endpoint, like the rest of the suite):
    export LLM_TIMEOUT=120
    python -m experiments.run_experiment_21_operator_headroom \
        --ecs-detail artifacts/ecs_structure_ablation_detail.csv
Smoke: --limit 6.  Non-determinism (~37%% churn) is real -> run >=2x and compare.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.labels import PIPELINE_STEP_ACTIONS
from cmd_audit.data_io import load_probe_cases
from cmd_audit.eval.writers import (
    best_scored_pair,
    format_recovery_value,
    is_timeout_value,
    nan_safe_max,
    recovery_case_outcomes,
    write_csv_table,
)
from cmd_audit.counterfactual.actions import (
    PipelineAction,
    get_legal_actions,
)
from cmd_audit.counterfactual.operators import OperatorSpec, apply_operator_static
from cmd_audit.repair.actions import get_targeted_repair_action_v1
from cmd_audit.scoring import score_answer_with_verifier
from experiments.experiment_runner_common import (
    DATA,
    OUT,
    AGENT_SYSTEM_PROMPT,
    assert_g_eval_available,
    build_answer_verifier,
)
from experiments.experiment_runner_common import build_clients
from experiments.probe_exhaustive import _step_context, _own_recovery


def _load_residual_ids(ecs_detail_path: Path, labels: set[str]) -> set[str]:
    residual: set[str] = set()
    with ecs_detail_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["included"] != "true" and row["gold_label"] in labels:
                residual.add(row["case_id"])
    return residual


def _chain_recovery(client, base_ctx, recall, max_depth, cfg, operator,
                    gold, verifier, baseline):
    """Apply the operator (one action per gen point, identity elsewhere) along the
    backbone via _step_context, then score the terminal rollout. Faithful to the
    rollout path that defines the residual."""
    ctx = base_ctx
    action_by_gp = operator.action_by_generation_point()
    op_cfg = operator.intervention_config(cfg)
    for gp in range(max_depth):
        action = action_by_gp.get(gp, PipelineAction.IDENTITY)
        ctx = _step_context(client, ctx, action, recall, gp, op_cfg)
    return _own_recovery(client, ctx, max_depth, max_depth, recall, gold, verifier, baseline)


def _agent_answer(client, query: str, context: str) -> str:
    prompt = "\n\n".join(("CONTEXT:", context or "(empty)", "QUERY:", query, "ANSWER:"))
    out = client.generate(prompt, system=AGENT_SYSTEM_PROMPT)
    return out.strip() if out else ""


def _static_corrected(base_ctx, recall, cfg, operator):
    """Static corrected context (Exp17/20 path): apply the op chain WITHOUT prefix
    regeneration, for the EC-framing comparison."""
    return apply_operator_static(
        base_ctx,
        recall,
        operator,
        intervention_config=cfg,
    )


def _parameter_item_ids(
    recall: tuple,
    cfg: dict,
) -> tuple[str, ...]:
    """Items eligible for promote/demote parameters.

    This uses recalled items plus the configured candidate pool. The candidate
    pool is the memory store snapshot used by structural operators; it is not
    gold evidence or the gold answer.
    """
    seen: set[str] = set()
    memory_ids: list[str] = []
    for item in recall + tuple(cfg.get("candidate_items") or ()):
        if item.memory_id in seen:
            continue
        seen.add(item.memory_id)
        memory_ids.append(item.memory_id)
    return tuple(memory_ids)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cases", default=str(DATA / "real_multihop_cases.json"))
    p.add_argument("--ecs-detail", default=str(OUT.parent / "ecs_structure_ablation_detail.csv"))
    p.add_argument("--labels", default="injection_error,safety_error,retrieval_error,granularity_error")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--out", default=str(OUT / "operator_headroom_detail.csv"))
    p.add_argument("--recovered-threshold", type=float, default=0.1,
                   help="net gain over identity backbone to count as recovered (Exp17 rule).")
    p.add_argument("--answer-recovered-threshold", type=float, default=0.8,
                   help="absolute answer score for the static EC-on/off comparison.")
    p.add_argument("--no-ec-test", action="store_true", help="skip the EC-framing measurement.")
    args = p.parse_args()

    labels = {l.strip() for l in args.labels.split(",") if l.strip()}
    ecs_path = Path(args.ecs_detail)
    if not ecs_path.exists():
        raise SystemExit(f"ECS detail not found: {ecs_path} (run Exp17 first).")
    residual_ids = _load_residual_ids(ecs_path, labels)
    if not residual_ids:
        raise SystemExit(f"no residual cases for labels={labels} in {ecs_path}")

    client, judge_client = build_clients()
    assert_g_eval_available(judge_client, role="operator-headroom")
    verifier = build_answer_verifier(judge_client, answer_mode="answer-rubric")

    all_cases = [c for c in load_probe_cases(args.cases) if c.perturbation_label in PIPELINE_STEP_ACTIONS]
    residual = [c for c in all_cases if c.case_id in residual_ids]
    if args.limit:
        residual = residual[: args.limit]

    from cmd_audit.harness import _initial_mcts_context, _retrieved_memory_items

    arms = ("single", "double", "param", "double_param", "richer")
    tally = defaultdict(lambda: [0, 0])
    headroom_cases = []
    excluded_cases: list[str] = []
    ec_on = ec_off = ec_both_rec = 0
    detail_rows = []
    print(f"Operator headroom over {len(residual)} residual cases, labels={sorted(labels)}\n")

    for i, case in enumerate(residual, start=1):
        recall = _retrieved_memory_items(case)
        max_depth = max(1, min(3, len(recall) or 1))
        base_ctx = _initial_mcts_context(case, recall)
        cfg = {"candidate_items": case.extracted_memory, "raw_events": case.raw_events}
        parameter_item_ids = _parameter_item_ids(recall, cfg)
        gold = case.gold_answer
        baseline = case.primary_baseline.answer_score

        def rec(operator=None):
            op = operator or OperatorSpec()
            return _chain_recovery(client, base_ctx, recall, max_depth, cfg,
                                   op, gold, verifier, baseline)

        base_gain = rec()  # identity backbone
        if is_timeout_value(base_gain):
            # The identity-backbone rollout timed out, so every net gain for this
            # case would be `score - NaN == NaN`: every `net > thr` test is False
            # and every seeded maximisation keeps its -1.0 sentinel, so the case
            # would be tallied as "not recovered". Exclude it instead -- counting
            # a timeout as a failure is exactly the downward bias the NaN
            # sentinel exists to remove.
            excluded_cases.append(case.case_id)
            detail_rows.append(_excluded_detail_row(case))
            print(f"  [{i}/{len(residual)}] {case.perturbation_label:16s} "
                  "EXCLUDED (identity-backbone rollout timed out)")
            continue

        arm_timeouts = 0

        def net(g):
            # NaN-safe: a timed-out operator rollout stays NaN (never wins a
            # maximisation, never counted as recovered) and is tallied.
            nonlocal arm_timeouts
            if is_timeout_value(g):
                arm_timeouts += 1
                return float("nan")
            return g - base_gain

        # ---- single ----
        single_best, single_op = -1.0, None
        for gp in range(max_depth):
            for a in get_legal_actions(recall, gp):
                if a == PipelineAction.IDENTITY:
                    continue
                op = OperatorSpec.single(gp, a)
                g = net(rec(op))
                if g > single_best:
                    single_best, single_op = g, op

        # ---- double (two-point composite) ----
        double_best, double_op = -1.0, None
        if max_depth >= 2:
            for gp0 in range(max_depth):
                for gp1 in range(gp0 + 1, max_depth):
                    for a0 in get_legal_actions(recall, gp0):
                        if a0 == PipelineAction.IDENTITY:
                            continue
                        for a1 in get_legal_actions(recall, gp1):
                            if a1 == PipelineAction.IDENTITY:
                                continue
                            op = OperatorSpec.from_actions(((gp0, a0), (gp1, a1)))
                            g = net(rec(op))
                            if g > double_best:
                                double_best, double_op = g, op

        # ---- param (best single + per-item promote/demote) ----
        param_best, param_op = single_best, single_op
        if single_op is not None:
            for memory_id in parameter_item_ids:
                for w in (-1.0, 1.0):
                    op = single_op.with_item_signal_hint(memory_id, w)
                    g = net(rec(op))
                    if g > param_best:
                        param_best, param_op = g, op

        # ---- double+param (best double + per-item promote/demote) ----
        double_param_best, double_param_op = double_best, double_op
        if double_op is not None:
            for memory_id in parameter_item_ids:
                for w in (-1.0, 1.0):
                    op = double_op.with_item_signal_hint(memory_id, w)
                    g = net(rec(op))
                    if g > double_param_best:
                        double_param_best, double_param_op = g, op

        richer_best = _nan_safe_max(double_best, param_best, double_param_best)
        richer_candidates = [
            (single_best, single_op),
            (double_best, double_op),
            (param_best, param_op),
            (double_param_best, double_param_op),
        ]
        _richer_score, richer_op = _best_pair(richer_candidates)
        thr = args.recovered_threshold
        recd = recovery_case_outcomes(
            base_gain,
            {"single": single_best, "double": double_best,
             "param": param_best, "double_param": double_param_best,
             "richer": richer_best},
            threshold=thr,
        )
        assert recd is not None  # NaN base_gain already excluded above
        for a in arms:
            tally[a][0] += int(recd[a]); tally[a][1] += 1

        is_headroom = recd["richer"] and not recd["single"]
        if is_headroom:
            headroom_cases.append(case.case_id)

        # ---- EC-on/off on the best recovering operator (static path) ----
        ec_decision = ""
        if not args.no_ec_test and recd["richer"]:
            corrected = _static_corrected(base_ctx, recall, cfg, richer_op)
            last_action = richer_op.last_action
            if last_action is None:
                continue
            cause = get_targeted_repair_action_v1(last_action.value).cause
            ec_block = f"[Error]\nThe previous memory context failed.\n\n[Cause]\n{cause}"
            s_off = score_answer_with_verifier(verifier, _agent_answer(client, case.query, corrected), gold)
            s_on = score_answer_with_verifier(verifier, _agent_answer(client, case.query, ec_block + "\n\n" + corrected), gold)
            r_off, r_on = s_off >= args.answer_recovered_threshold, s_on >= args.answer_recovered_threshold
            ec_off += int(r_off); ec_on += int(r_on); ec_both_rec += 1
            ec_decision = f"off={s_off:.2f}/{r_off} on={s_on:.2f}/{r_on}"

        detail_rows.append({
            "case_id": case.case_id, "gold_label": case.perturbation_label,
            "status": "ok", "excluded": "false",
            "timeout_count": str(arm_timeouts),
            "base_gain": format_recovery_value(base_gain, digits=4),
            "single_net": format_recovery_value(single_best, digits=4),
            "double_net": format_recovery_value(double_best, digits=4),
            "param_net": format_recovery_value(param_best, digits=4),
            "double_param_net": format_recovery_value(double_param_best, digits=4),
            "richer_net": format_recovery_value(richer_best, digits=4),
            "single_op": _fmt(single_op), "double_op": _fmt(double_op),
            "param_op": _fmt(param_op), "double_param_op": _fmt(double_param_op),
            "richer_op": _fmt(richer_op),
            "headroom": str(is_headroom).lower(), "ec_test": ec_decision,
        })
        flag = "  <== HEADROOM" if is_headroom else ""
        print(f"  [{i}/{len(residual)}] {case.perturbation_label:16s} "
              f"single={single_best:+.2f} double={double_best:+.2f} "
              f"param={param_best:+.2f} double+param={double_param_best:+.2f}{flag}")

    _summary(tally, arms, headroom_cases, ec_off, ec_on, ec_both_rec, excluded_cases)
    path = write_csv_table(
        args.out,
        ["case_id", "gold_label", "status", "excluded", "timeout_count",
         "base_gain", "single_net", "double_net", "param_net",
         "double_param_net", "richer_net", "single_op", "double_op", "param_op",
         "double_param_op", "richer_op", "headroom", "ec_test"],
        detail_rows, sandbox_root=OUT.parent, judge_client=judge_client,
    )
    print(f"\nWrote {path}")


def _fmt(op):
    if not op:
        return ""
    return op.format()


def _excluded_detail_row(case) -> dict[str, str]:
    """Detail row for a case excluded because its identity backbone timed out.

    Recorded distinctly (status/excluded + timeout_count) so the exclusion is
    visible downstream and never read as a 0-recovery case.
    """
    row = {
        "case_id": case.case_id,
        "gold_label": case.perturbation_label,
        "status": "base_gain_timeout",
        "excluded": "true",
        "timeout_count": "1",
        "base_gain": format_recovery_value(float("nan")),
        "headroom": "false",
        "ec_test": "",
    }
    for col in ("single_net", "double_net", "param_net", "double_param_net",
                "richer_net"):
        row[col] = ""
    for col in ("single_op", "double_op", "param_op", "double_param_op",
                "richer_op"):
        row[col] = ""
    return row


def _best_pair(candidates):
    """NaN-safe argmax over (score, op) pairs, skipping op=None entries.

    ``max(..., key=...)`` is order-dependent once NaN scores are present
    (Python's total-order comparisons make NaN "win" or "lose" depending on
    which element it is compared against first) so it cannot be trusted to
    keep a timed-out (NaN) operator from being selected as best. This mirrors
    the NaN-safe semantics of a plain ``if score > best`` loop.
    """
    return best_scored_pair(candidates)


def _nan_safe_max(*values: float) -> float:
    """NaN-safe max: excludes NaN unless every value is NaN."""
    return nan_safe_max(*values)


def _summary(tally, arms, headroom_cases, ec_off, ec_on, ec_both, excluded_cases=()):
    print("\n=== Recovery rate per operator class (residual) ===")
    print(f"{'arm':10s} {'recovered':>9s} {'total':>6s} {'rate':>8s}")
    for a in arms:
        r, t = tally[a]
        print(f"{a:10s} {r:>9d} {t:>6d} {(r/t if t else 0):>8.4f}")
    if excluded_cases:
        print(f"\nEXCLUDED (identity-backbone rollout timed out): {len(excluded_cases)} "
              "-- not counted as recovered or not-recovered in any arm.")
    print(f"\nHEADROOM (richer recovers, single does NOT): {len(headroom_cases)}/{tally['single'][1]}")
    print("  -> >0 and meaningful: operator evolution extends what is fixable; build the skill library.")
    print("  -> ~0: C6 wall holds; evolution = reuse/efficiency only (Exp18/19).")
    if ec_both:
        print(f"\n=== EC-framing on the best operator's corrected content (n={ec_both} recovered) ===")
        print(f"  ec_off (corrected only)        recovered {ec_off}/{ec_both}")
        print(f"  ec_on  ([Error][Cause]+corrected) recovered {ec_on}/{ec_both}")
        print("  EC adds value iff ec_on > ec_off; if equal, keep EC only as the skill trigger/index, not answer text.")
    print("\nNOTE: single run; inference non-determinism ~37%% churn -> run >=2x and compare headroom set.")


if __name__ == "__main__":
    main()
