#!/usr/bin/env python3
"""Exhaustive single-point counterfactual probe (diagnostic C, NOT a runner).

Bypasses MCTS/UCB entirely. For each case, deterministically evaluates every
single-point intervention along the identity backbone:

  credit[0][a] = own_recovery(hop1=a,        hop2=identity)
               - own_recovery(hop1=identity, hop2=identity)
  credit[1][a] = own_recovery(hop1=identity, hop2=a)
               - own_recovery(hop1=identity, hop2=identity)

This is the UPPER BOUND of the counterfactual signal: every (hop, action) path
is rolled out, so any low accuracy here is a signal-quality problem, not a
search-coverage problem. main_culprit = argmax credit over non-identity actions.

Run after vLLM is up:
    python -m experiments.probe_exhaustive --limit 0 --aggregate
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.labels import PIPELINE_STEP_ACTIONS
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.data_io import load_probe_cases
from cmd_audit.counterfactual.actions import (
    PipelineAction,
    apply_pipeline_action,
    get_legal_actions,
)
from cmd_audit.counterfactual.rollout import rollout_to_terminal
from cmd_audit.counterfactual.context import generate_conditioned_context
from cmd_audit.eval.writers import write_csv_table
from experiments.experiment_runner_common import (
    OUT,
    assert_g_eval_available,
    build_answer_verifier,
    load_raw_rows,
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "probe_cases"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "real_multihop_cases.json"))
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--aggregate", action="store_true")
    parser.add_argument(
        "--out",
        default=None,
        help=(
            "Optional CSV path for per-case detail (case_id, gold, culprit gp, "
            "culprit action, credit). Used to inspect prior transferability."
        ),
    )
    parser.add_argument(
        "--min-credit",
        type=float,
        default=0.0,
        help=(
            "Abstention threshold: a culprit must exceed this credit to be "
            "committed. Raise above 0 (e.g. 0.05-0.1) to abstain on "
            "noise-floor credits that would otherwise become confident wrong "
            "labels. Default 0.0 (any positive credit wins)."
        ),
    )
    args = parser.parse_args()

    client = LLMClient(LLMClientConfig())
    assert_g_eval_available(client, role="probe-exhaustive")
    verifier = build_answer_verifier(client, answer_mode="answer-rubric")

    cases = [
        c
        for c in load_probe_cases(args.cases)
        if c.perturbation_label in PIPELINE_STEP_ACTIONS
    ]
    if args.limit:
        cases = cases[: args.limit]

    expected_hops = {}
    for row in load_raw_rows(args.cases):
        expected = row.get("expected_fault") or {}
        if expected.get("hop_index") is not None:
            expected_hops[str(row["case_id"])] = int(expected["hop_index"])

    by_label = defaultdict(lambda: [0, 0, 0, 0])
    pred_by_label = defaultdict(Counter)
    genpoint_by_label = defaultdict(Counter)
    joint_by_label = defaultdict(Counter)  # gold -> Counter[(gp, action)]
    detail_rows = []

    print(f"Exhaustive single-point over {len(cases)} cases\n")
    for i, case in enumerate(cases):
        credits, culprit = _evaluate_case(case, client, verifier, min_credit=args.min_credit)
        gold = case.perturbation_label
        by_label[gold][0] += 1
        culprit_gp = culprit_action = culprit_credit = None
        if culprit:
            gp, action, credit = culprit
            culprit_gp, culprit_action, culprit_credit = gp, action.value, credit
            pred = action.value
            pred_by_label[gold][pred] += 1
            genpoint_by_label[gold][gp] += 1
            joint_by_label[gold][(gp, pred)] += 1
            if pred == gold:
                by_label[gold][1] += 1
            expected_hop = expected_hops.get(case.case_id)
            if expected_hop is not None and gp + 1 == expected_hop:
                by_label[gold][2] += 1
        else:
            # Principled abstention: no positive-credit repair (failure absent,
            # out of scope, or identity already recovered). Not a wrong label.
            by_label[gold][3] += 1
            pred_by_label[gold]["<abstain>"] += 1
            joint_by_label[gold][("<abstain>", "<abstain>")] += 1
        detail_rows.append({
            "case_id": case.case_id,
            "gold_label": gold,
            "expected_hop": "" if expected_hops.get(case.case_id) is None else str(expected_hops[case.case_id]),
            "culprit_gen_point": "" if culprit_gp is None else str(culprit_gp),
            "culprit_action": culprit_action or "<abstain>",
            "culprit_credit": "" if culprit_credit is None else f"{culprit_credit:.4f}",
        })
        if not args.aggregate:
            print(f"case_id    : {case.case_id}")
            print(f"gold       : {gold}  expected_hop={expected_hops.get(case.case_id)}")
            print(f"culprit    : {culprit}")
            for k in sorted(credits):
                inner = {a.value: round(v, 3) for a, v in credits[k].items()}
                print(f"  credit[{k}]: {inner}")
            print("-" * 60)
        else:
            print(f"  [{i+1}/{len(cases)}] {gold:20s} -> {culprit}")

    _print_aggregate(by_label, pred_by_label, genpoint_by_label, len(cases))
    _print_transferability(joint_by_label)

    if args.out:
        _write_detail_csv(args.out, detail_rows)
        print(f"\nWrote {args.out}")


PLACEHOLDER_TAIL = None


def _step_context(client, parent_context, action, recall_set, gen_point, intervention_config):
    """Replicate search.py's context_generator: apply action then re-generate prefix."""
    intervened = apply_pipeline_action(
        action, parent_context, recall_set, gen_point,
        intervention_config=intervention_config,
    )
    return generate_conditioned_context(client, intervened, gen_point + 1)


def _own_recovery(client, context, start_gp, max_depth, recall_set, gold_answer, verifier, baseline):
    """Single rollout from a node: remaining hops identity, score terminal."""
    result = rollout_to_terminal(
        client, context, start_gp, max_depth, recall_set, gold_answer,
        answer_verifier=verifier, baseline_answer_score=baseline,
    )
    return result.recovery_gain if result.rollout_successful else 0.0


def _evaluate_case(case, client, verifier, min_credit=0.0):
    from cmd_audit.harness import _initial_mcts_context, _retrieved_memory_items

    recall_set = _retrieved_memory_items(case)
    max_depth = max(1, min(3, len(recall_set) or 1))
    base_ctx = _initial_mcts_context(case, recall_set)
    cfg = {"candidate_items": case.extracted_memory, "raw_events": case.raw_events}
    gold_answer = case.gold_answer
    baseline = case.primary_baseline.answer_score

    def own(context, start_gp):
        return _own_recovery(
            client, context, start_gp, max_depth, recall_set, gold_answer, verifier, baseline
        )

    # hop1 actions (gen_point 0), then hop2 actions along identity backbone (gen_point 1).
    hop1_actions = get_legal_actions(recall_set, 0)
    # identity-backbone context at hop2: hop1 = identity
    id1_ctx = _step_context(client, base_ctx, PipelineAction.IDENTITY, recall_set, 0, cfg)
    hop2_actions = get_legal_actions(recall_set, 1)

    credits = {0: {}, 1: {}}
    # baseline = identity everywhere
    id_id_recovery = own(
        _step_context(client, id1_ctx, PipelineAction.IDENTITY, recall_set, 1, cfg), 2
    )

    # credit[0]: vary hop1, hop2 = identity (rollout fills identity for remaining)
    id1_recovery = own(id1_ctx, 1)  # hop1=identity, then rollout identity
    for a in hop1_actions:
        if a == PipelineAction.IDENTITY:
            credits[0][a] = 0.0
            continue
        ctx = _step_context(client, base_ctx, a, recall_set, 0, cfg)
        credits[0][a] = own(ctx, 1) - id1_recovery

    # credit[1]: hop1 = identity, vary hop2
    for a in hop2_actions:
        if a == PipelineAction.IDENTITY:
            credits[1][a] = 0.0
            continue
        ctx = _step_context(client, id1_ctx, a, recall_set, 1, cfg)
        credits[1][a] = own(ctx, 2) - id_id_recovery

    # Principled abstention: only commit to an action with strictly positive
    # credit. A non-positive best means no repair recovered, or identity already
    # recovered (the model did not commit the labeled failure) — committing the
    # first-iterated action then fabricates a label. Mirrors find_main_culprit.
    culprit = None
    best = min_credit
    for gp in (0, 1):
        for a, c in credits[gp].items():
            if a != PipelineAction.IDENTITY and c > best:
                best = c
                culprit = (gp, a, c)
    return credits, culprit


def _print_aggregate(by_label, pred_by_label, genpoint_by_label, n_cases):
    print("\n=== Per-label accuracy (exhaustive single-point) ===")
    print(
        f"{'gold label':22s} {'n':>3s} {'abst':>4s} "
        f"{'label_acc':>10s} {'decided_acc':>12s} {'hop_acc':>10s}"
    )
    tot = [0, 0, 0, 0]
    for label in sorted(by_label):
        n, lc, hc, ab = by_label[label]
        tot[0] += n; tot[1] += lc; tot[2] += hc; tot[3] += ab
        decided = n - ab
        dec_acc = lc / decided if decided else 0.0
        print(
            f"{label:22s} {n:>3d} {ab:>4d} {lc/n:>10.4f} "
            f"{dec_acc:>12.4f} {hc/n:>10.4f}"
        )
    if tot[0]:
        dec_tot = tot[0] - tot[3]
        dec_acc_tot = tot[1] / dec_tot if dec_tot else 0.0
        print(
            f"{'TOTAL':22s} {tot[0]:>3d} {tot[3]:>4d} {tot[1]/tot[0]:>10.4f} "
            f"{dec_acc_tot:>12.4f} {tot[2]/tot[0]:>10.4f}"
        )
    print(
        "\n(label_acc counts abstentions as wrong; decided_acc excludes them. "
        "Abstention = no positive-credit repair: failure absent / out of scope / "
        "identity already recovered.)"
    )
    print("\n=== Predicted-label distribution per gold ===")
    for label in sorted(pred_by_label):
        print(f"  {label:22s} -> {dict(pred_by_label[label])}")
    print("\n=== gen_point distribution per gold (want gp=1) ===")
    for label in sorted(genpoint_by_label):
        print(f"  {label:22s} -> {dict(genpoint_by_label[label])}")


def _print_transferability(joint_by_label):
    """Per-gold concentration of the JOINT (gen_point, action) culprit.

    This is the prior-transferability metric the online seed depends on. The
    Failure Memory prior keys on (query, hop, label); for it to steer one
    targeted rollout, the offline exhaustive culprit must land on a STABLE
    (gp, action) within a fault type. concentration = modal_count / decided is
    that stability: 1.0 = every decided case agrees, 0.x = the prior is diluted
    across multiple points and a single targeted seed cannot cover them.
    """
    print("\n=== Joint (gen_point, action) culprit concentration per gold ===")
    print(f"{'gold label':22s} {'decided':>7s} {'modal (gp,action)':>26s} {'concentration':>14s}")
    for label in sorted(joint_by_label):
        counter = joint_by_label[label]
        decided = sum(n for key, n in counter.items() if key != ("<abstain>", "<abstain>"))
        if decided == 0:
            print(f"{label:22s} {0:>7d} {'<all abstain>':>26s} {'n/a':>14s}")
            continue
        decided_items = [
            (key, n) for key, n in counter.items()
            if key != ("<abstain>", "<abstain>")
        ]
        modal_key, modal_n = max(decided_items, key=lambda kv: kv[1])
        conc = modal_n / decided
        modal_str = f"(gp{modal_key[0]}, {modal_key[1]})"
        print(f"{label:22s} {decided:>7d} {modal_str:>26s} {conc:>14.3f}")
    print(
        "\n=== Full joint distribution per gold ===")
    for label in sorted(joint_by_label):
        pretty = {
            (f"gp{k[0]}", k[1]) if k != ("<abstain>", "<abstain>") else "<abstain>": n
            for k, n in joint_by_label[label].items()
        }
        print(f"  {label:22s} -> {pretty}")
    print(
        "\n(concentration = modal_count / decided. High = the correct "
        "(hop, action) is stable within the fault type, so a Failure-Memory "
        "prior transfers and one targeted seed suffices online. Low = diluted, "
        "single-seed coverage insufficient.)"
    )


def _write_detail_csv(path, rows):
    import csv

    fieldnames = [
        "case_id", "gold_label", "expected_hop",
        "culprit_gen_point", "culprit_action", "culprit_credit",
    ]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


if __name__ == "__main__":
    main()
