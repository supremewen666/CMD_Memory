#!/usr/bin/env python3
"""Experiment 14: repair-efficacy four-arm comparison (Phase 1, E2/E3).

Runs four arms over the pipeline-step cases. Every arm shares the gold-free
repair execution core (``cmd_audit.repair.efficacy.run_single_repair``); they
differ ONLY in how the repaired action is selected:

  no_repair   floor — no action (identity rollout of base context).
  random      noise floor — case_id-seeded pick over legal (gen_point, action).
  llm_judge   E3 competitor — LLM names the fault -> its action (gp=0 fallback).
  cmd         this method — recovery-gain pick, honoring the located hop.

E2 = cmd vs no_repair (does the loop recover at all).
E3 = cmd vs random / llm_judge (does CMD select the recovering action).

recovered is judged on NET gain = arm_gain - no_repair_gain. The no_repair arm
is itself the baseline (net 0), so a case whose gold is already answerable
cannot let an arm that changed nothing claim a recovery. Because construction is
gold-free, a recovered answer cannot come from copying gold into the context.

The arm scheduling, random/no_repair comparison arms, and net-gain bookkeeping
are experiment scaffolding and live here, not in cmd_audit/.

Run after vLLM is up:
    python -m experiments.run_experiment_14_repair_efficacy --limit 0
"""

from __future__ import annotations

import argparse
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.baselines.comparators import run_llm_judge_baseline
from cmd_audit.core.labels import PIPELINE_STEP_ACTIONS
from cmd_audit.data_io import load_probe_cases
from cmd_audit.eval.writers import (
    format_recovery_value,
    is_timeout_value,
    write_csv_table,
)
from cmd_audit.counterfactual.actions import PipelineAction, get_legal_actions
from cmd_audit.repair.efficacy import run_single_repair, select_label_cmd
from experiments.experiment_runner_common import (
    DATA,
    OUT,
    assert_g_eval_available,
    build_answer_verifier,
    run_mcts_for_case,
)
from experiments.experiment_runner_common import build_clients
from experiments.probe_exhaustive import _evaluate_case

ROOT = Path(__file__).resolve().parent.parent

REPAIR_ARMS = ("no_repair", "random", "llm_judge", "cmd")


def _legal_actions_all_points(recall_set, max_depth):
    """Enumerate non-identity (gen_point, action) pairs legal along the trajectory."""
    pairs = []
    for gp in range(max_depth):
        for action in get_legal_actions(recall_set, gp):
            if action == PipelineAction.IDENTITY:
                continue
            pairs.append((gp, action))
    return pairs


def _select_random(case, recall_set, max_depth):
    """case_id-seeded pick over legal (gen_point, action) pairs. Deterministic."""
    pairs = _legal_actions_all_points(recall_set, max_depth)
    if not pairs:
        return None
    return random.Random(case.case_id).choice(pairs)


def _cmd_attribution(case, client, verifier, max_iterations, max_depth):
    """Return (label, gen_point) from step-level attribution.

    The gen_point is load-bearing: a correct action at the wrong generation
    point does not recover, so the cmd arm must honor the localized hop, not
    re-pick gp=0.
    """
    result = run_mcts_for_case(
        case, client, verifier,
        max_iterations=max_iterations, max_depth=max_depth,
    )
    if result.main_culprit is None:
        return None, None
    gen_point, action, _credit = result.main_culprit
    return action.value, gen_point


def _cmd_attribution_exhaustive(case, client, verifier):
    """A2 diagnostic: full single-point scan instead of MCTS/UCB.

    Reuses probe_exhaustive._evaluate_case, which rolls out EVERY (gen_point,
    action) along the identity backbone — the upper bound of the counterfactual
    signal, with no search-coverage loss. Returns (label, gen_point) so the cmd
    arm can be tested under guaranteed full coverage. If this turns the cmd arm
    positive where MCTS did not, the MCTS gap is coverage, not signal."""
    _credits, culprit = _evaluate_case(case, client, verifier)
    if culprit is None:
        return None, None
    gen_point, action, _credit = culprit
    return action.value, gen_point


def _choice_for_arm(
    arm, case, recall_set, max_depth, *,
    cmd_label, cmd_gen_point, llm_selector,
):
    """Produce the (gen_point, action) choice for an arm, or None for no action."""
    if arm == "no_repair":
        return None
    if arm == "random":
        return _select_random(case, recall_set, max_depth)
    if arm == "cmd":
        return select_label_cmd(
            cmd_label or "", recall_set, max_depth, gen_point=cmd_gen_point
        )
    # llm_judge: LLM names the fault AND localizes the hop, so it is judged on
    # equal footing with cmd (both know where to look) — not a gp=0 strawman.
    if llm_selector is None:
        return None
    label, llm_gen_point = llm_selector(case, recall_set, max_depth)
    return select_label_cmd(
        label or "", recall_set, max_depth, gen_point=llm_gen_point
    )


def _llm_label_selector(client):
    """LLM names BOTH the fault action and the hop it occurs at.

    Isolates "LLM self-diagnoses fault + location" from "counterfactual search
    + verification" (the cmd arm). Without the hop, llm_judge fell back to gp=0
    and recovered nothing on every case — that confounds wrong-label with
    wrong-hop and makes E3 a strawman. Here the LLM picks the hop too, so E3
    contrasts naming vs search on equal footing (both know where to look).
    Returns (label, gen_point); gen_point is None on parse failure -> first legal.
    """
    def select(case, recall_set, max_depth):
        comparator = run_llm_judge_baseline(case, llm_client=client)
        gen_point = _llm_localize_hop(client, case, recall_set, max_depth)
        return comparator.predicted_label, gen_point
    return select


def _llm_localize_hop(client, case, recall_set, max_depth):
    """Ask the LLM which generation point (hop) carries the failure.

    Returns a 0-based gen_point in [0, max_depth), or None on parse failure
    (caller then falls back to the first legal point). Hops are grouped by the
    ``m_hop{N}_`` id prefix; the LLM answers a 1-based hop index.
    """
    if client is None or not hasattr(client, "generate"):
        return None
    hop_blocks = []
    for gp in range(max_depth):
        items = [m for m in recall_set if f"m_hop{gp + 1}_" in m.memory_id]
        listed = "\n".join(f"    - {m.text}" for m in items) or "    (no items)"
        hop_blocks.append(f"  Hop {gp + 1}:\n{listed}")
    prompt = (
        "A memory-augmented agent failed to answer the query below. The recalled "
        "memory is grouped by hop (generation point). Identify the SINGLE hop where "
        "the failure is introduced.\n\n"
        f"QUERY:\n{case.query}\n\n"
        f"RECALLED MEMORY BY HOP:\n" + "\n".join(hop_blocks) + "\n\n"
        f"Answer with only the hop number (1 to {max_depth}). HOP:"
    )
    try:
        response = client.generate(prompt)
    except Exception:
        return None
    match = re.search(r"\d+", response or "")
    if not match:
        return None
    hop_1based = int(match.group())
    if 1 <= hop_1based <= max_depth:
        return hop_1based - 1
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "real_multihop_cases.json"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-iterations", type=int, default=12)
    parser.add_argument("--recovered-threshold", type=float, default=0.1)
    parser.add_argument(
        "--cmd-attribution",
        choices=("mcts", "exhaustive"),
        default="mcts",
        help=(
            "How the cmd arm picks (label, gen_point). 'mcts' is retained as "
            "a backward-compatible alias for the live single-point attribution "
            "path. 'exhaustive' runs the same full single-point scan along the "
            "identity backbone."
        ),
    )
    parser.add_argument(
        "--case-ids",
        default="",
        help=(
            "Comma-separated case_id substrings to keep (e.g. '-0001-,-0002-'). "
            "Empty = all. Scopes the A2 diagnostic to a few cases."
        ),
    )
    args = parser.parse_args()

    client, judge_client = build_clients()
    assert_g_eval_available(judge_client, role="repair-efficacy")
    verifier = build_answer_verifier(judge_client, answer_mode="answer-rubric")

    cases = [
        c
        for c in load_probe_cases(args.cases)
        if c.perturbation_label in PIPELINE_STEP_ACTIONS
    ]
    if args.case_ids:
        wanted = [s for s in args.case_ids.split(",") if s]
        cases = [c for c in cases if any(w in c.case_id for w in wanted)]
    if args.limit:
        cases = cases[: args.limit]

    from cmd_audit.harness import _initial_mcts_context, _retrieved_memory_items

    llm_selector = _llm_label_selector(client)

    # arm -> gold_label -> [recovered_count, total]
    tally = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    detail_rows = []
    excluded_cases = []

    print(f"Repair efficacy: 4 arms over {len(cases)} cases\n")
    for i, case in enumerate(cases):
        recall_set = _retrieved_memory_items(case)
        max_depth = max(1, min(3, len(recall_set) or 1))
        base_ctx = _initial_mcts_context(case, recall_set)
        cfg = {"candidate_items": case.extracted_memory, "raw_events": case.raw_events}
        gold = case.perturbation_label

        if args.cmd_attribution == "exhaustive":
            cmd_label, cmd_gen_point = _cmd_attribution_exhaustive(
                case, client, verifier
            )
        else:
            cmd_label, cmd_gen_point = _cmd_attribution(
                case, client, verifier, args.max_iterations, max_depth
            )

        def _run(arm):
            choice = _choice_for_arm(
                arm, case, recall_set, max_depth,
                cmd_label=cmd_label, cmd_gen_point=cmd_gen_point,
                llm_selector=llm_selector,
            )
            return run_single_repair(
                case, choice,
                client=client, answer_verifier=verifier,
                base_context=base_ctx, recall_set=recall_set, max_depth=max_depth,
                intervention_config=cfg,
            )

        # no_repair runs first: its absolute gain is the baseline every other arm
        # subtracts. recovered is judged on net_gain (E2 contract).
        baseline_res = _run("no_repair")
        baseline_gain = baseline_res.recovery_gain
        if is_timeout_value(baseline_gain):
            excluded_cases.append(case.case_id)
            detail_rows.extend(_excluded_rows(case, gold))
            print(
                f"  [{i+1}/{len(cases)}] {gold:20s} "
                "EXCLUDED (identity-backbone rollout timed out)"
            )
            continue

        for arm in REPAIR_ARMS:
            res = baseline_res if arm == "no_repair" else _run(arm)
            net = 0.0 if arm == "no_repair" else res.recovery_gain - baseline_gain
            timed_out = is_timeout_value(net)
            recovered = not timed_out and net > args.recovered_threshold
            tally[arm][gold][0] += int(recovered)
            tally[arm][gold][1] += 1
            detail_rows.append({
                "case_id": case.case_id,
                "gold_label": gold,
                "status": "ok",
                "excluded": "false",
                "timeout_count": "1" if timed_out else "0",
                "arm": arm,
                "selected_action": res.selected_action or "",
                "generation_point": "" if res.generation_point is None else str(res.generation_point),
                "recovery_gain": format_recovery_value(
                    res.recovery_gain, digits=4
                ),
                "net_gain": format_recovery_value(net, digits=4),
                "recovered": str(recovered).lower(),
            })
        print(
            f"  [{i+1}/{len(cases)}] {gold:20s} "
            f"cmd_label={cmd_label} gp={cmd_gen_point} baseline={baseline_gain:.3f}"
        )

    _print_summary(tally)
    if excluded_cases:
        print(
            "\nEXCLUDED (identity-backbone rollout timed out): "
            f"{len(excluded_cases)} -- absent from every arm denominator."
        )

    detail_path = write_csv_table(
        OUT / "repair_efficacy_detail.csv",
        ["case_id", "gold_label", "status", "excluded", "timeout_count",
         "arm", "selected_action",
         "generation_point", "recovery_gain", "net_gain", "recovered"],
        detail_rows,
        sandbox_root=OUT,
        judge_client=judge_client,
    )
    print(f"\nWrote {detail_path}")

    summary_rows = []
    for arm in REPAIR_ARMS:
        rec = sum(v[0] for v in tally[arm].values())
        tot = sum(v[1] for v in tally[arm].values())
        summary_rows.append({
            "arm": arm,
            "recovered": str(rec),
            "total": str(tot),
            "recovered_rate": f"{rec / tot:.4f}" if tot else "0.0000",
        })
    summary_path = write_csv_table(
        OUT / "repair_efficacy_summary.csv",
        ["arm", "recovered", "total", "recovered_rate"],
        summary_rows,
        sandbox_root=OUT,
        judge_client=judge_client,
    )
    print(f"Wrote {summary_path}")


def _excluded_rows(case, gold):
    return [
        {
            "case_id": case.case_id,
            "gold_label": gold,
            "status": "base_gain_timeout",
            "excluded": "true",
            "timeout_count": "1",
            "arm": arm,
            "selected_action": "",
            "generation_point": "",
            "recovery_gain": "nan" if arm == "no_repair" else "",
            "net_gain": "",
            "recovered": "false",
        }
        for arm in REPAIR_ARMS
    ]


def _print_summary(tally) -> None:
    print("\n=== Recovered rate per arm (overall, net of no_repair) ===")
    print(f"{'arm':12s} {'recovered':>9s} {'total':>6s} {'rate':>8s}")
    for arm in REPAIR_ARMS:
        rec = sum(v[0] for v in tally[arm].values())
        tot = sum(v[1] for v in tally[arm].values())
        rate = rec / tot if tot else 0.0
        print(f"{arm:12s} {rec:>9d} {tot:>6d} {rate:>8.4f}")

    print("\n=== Recovered rate per arm x gold label ===")
    labels = sorted(PIPELINE_STEP_ACTIONS)
    header = f"{'arm':12s} " + " ".join(f"{l[:10]:>11s}" for l in labels)
    print(header)
    for arm in REPAIR_ARMS:
        cells = []
        for label in labels:
            rec, tot = tally[arm][label]
            cells.append(f"{(rec/tot if tot else 0.0):>11.3f}")
        print(f"{arm:12s} " + " ".join(cells))


if __name__ == "__main__":
    main()
