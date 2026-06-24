#!/usr/bin/env python3
"""Experiment 20: can a generative SKILL recover the structural-repair residual?

Motivation (see session analysis):
  - Single-point STRUCTURAL repair (apply_pipeline_action at one gen point)
    leaves a 115/240 residual on real_multihop_cases. Offline classification
    shows the residual is NOT a data floor: every residual case still has its
    gold-evidence-bearing items in the store. ~52 of them (injection_error +
    safety_error) have those items already in the agent's recall -- the content
    is in front of the model, just unusable. Those 52 are the only cases where a
    GOLD-FREE generative skill (abstracted from OTHER cases) has a clean shot:
    it does not need to fetch missing content, only to teach the model to use
    content that is present.
  - Exp17 C8 showed that injecting the *executor-facing* repair_guidance text
    ("update retrieval routing ...") HURTS (corrected_only 0.312 -> solution
    0.264), and that explanation-only (`cause_only`) recovers 0. This experiment
    replaces that executor-imperative text with an LLM-generated, ANSWER-FACING
    skill and asks whether it recovers cases structural repair cannot.

Prediction being tested (registered up front):
  Small effect, likely n.s., plausibly ~0. The `cause_only=0.000` prior is
  strong (explanation without effective corrected content recovered nothing on
  EASIER cases). Any positive signal should concentrate in injection_error
  (a cross-hop reasoning lever structural formatting repair does not touch),
  not safety_error. The scientifically likely outcome is a tight upper BOUND on
  what generative guidance adds beyond the structural ceiling.

Arms (corrected_context held FIXED across all four; only the wrapper differs):
  corrected_only   structural best-effort repair only        (residual baseline ~0)
  ecs_no_guidance  error+cause frame + corrected, NO guidance (the missing C8 control)
  ecs_template     ecs_no_guidance + boilerplate guidance     (executor-imperative text)
  ecs_skill        ecs_no_guidance + generated answer skill   (treatment)
The clean estimate of the skill's value is the paired ecs_skill - ecs_no_guidance
difference: frame and corrected content are fixed, so it isolates whether the
generated guidance TEXT adds anything over adding no text at all. ecs_template -
ecs_no_guidance isolates the (known-harmful) boilerplate text for contrast.
Exp17 never ran ecs_no_guidance, so full_ecs's gain could not be attributed to the
guidance vs the frame+content; this arm closes that gap.

Gold-free guarantees:
  - corrected_context is built only by apply_pipeline_action(recall_set, gp, action);
    never copies case.gold_*.
  - the (gp, action) repair point is the case's own recovery-gain culprit
    (_evaluate_case, gold-free) with a global-prior fallback; the held-out case's
    perturbation_label is never read to construct context.
  - per-action skills are abstracted from OTHER cases' queries only.
  - case.gold_answer is touched ONLY by the answer verifier (scoring), never
    during construction.

Run (needs a logprob-capable vLLM endpoint, like the rest of the suite):
    python -m experiments.run_experiment_20_generative_guidance_residual \
        --ecs-detail artifacts/sandbox/ecs_structure_ablation_detail.csv \
        --prior-bank artifacts/sandbox/exhaustive_detail_mincredit05.csv
Add --limit 6 for a smoke test.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.labels import PIPELINE_STEP_ACTIONS
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.data_io import load_probe_cases
from cmd_audit.eval.writers import write_csv_table
from cmd_audit.counterfactual.actions import (
    PipelineAction,
    apply_pipeline_action,
    get_legal_actions,
)
from cmd_audit.repair.actions import get_targeted_repair_action_v1
from cmd_audit.repair.efficacy import LABEL_TO_ACTION
from cmd_audit.scoring import score_answer_with_verifier
from experiments.experiment_runner_common import (
    DATA,
    OUT,
    AGENT_SYSTEM_PROMPT,
    assert_g_eval_available,
    build_answer_verifier,
)
from experiments.probe_exhaustive import _evaluate_case

ARMS = ("corrected_only", "ecs_no_guidance", "ecs_template", "ecs_skill")
ABSTAIN = "<abstain>"


# --------------------------------------------------------------------------- #
# residual identification (read the committed ECS split; no re-attribution)   #
# --------------------------------------------------------------------------- #
def _load_residual_ids(ecs_detail_path: Path, labels: set[str]) -> set[str]:
    """case_ids that were EXCLUDED by the ECS run (single-point repair failed),
    restricted to the requested gold labels."""
    residual: set[str] = set()
    with ecs_detail_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["included"] != "true" and row["gold_label"] in labels:
                residual.add(row["case_id"])
    return residual


# --------------------------------------------------------------------------- #
# gold-free global prior fallback (mirrors Exp15)                             #
# --------------------------------------------------------------------------- #
def _load_prior_bank(path: Path) -> dict[str, tuple[int | None, str | None]]:
    bank: dict[str, tuple[int | None, str | None]] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            action = row["culprit_action"]
            gp_raw = row["culprit_gen_point"]
            if action == ABSTAIN or gp_raw == "":
                bank[row["case_id"]] = (None, None)
            else:
                bank[row["case_id"]] = (int(gp_raw), action)
    return bank


def _global_top_pairs(bank, exclude_case_id, k=3):
    counter = Counter(
        (gp, a)
        for cid, (gp, a) in bank.items()
        if cid != exclude_case_id and gp is not None and a is not None
    )
    return [pair for pair, _n in counter.most_common(k)]


def _first_legal_choice(pairs, recall_set, max_depth):
    for gp, action_str in pairs:
        action = LABEL_TO_ACTION.get(action_str)
        if action is None or action == PipelineAction.IDENTITY:
            continue
        if 0 <= gp < max_depth and action in get_legal_actions(recall_set, gp):
            return gp, action
    return None


# --------------------------------------------------------------------------- #
# answer-facing skill abstraction (the LLM "guidance" path, fixed prompt)      #
# --------------------------------------------------------------------------- #
def _generate_action_skill(client, action_value: str, example_queries: tuple[str, ...]) -> str:
    """Abstract one reusable, ANSWER-FACING skill for a fault type from OTHER
    cases' queries. This is the answer-facing variant of
    FailureMemorySkillLoop.format_pattern(llm_client=...): format_pattern emits a
    pattern *description*; here we ask for an instruction that directs the
    answering model to use evidence already present in its context.

    Gold-free: uses the fault-type cause template and other cases' query text
    only -- never a gold answer or the held-out case.
    """
    cause = get_targeted_repair_action_v1(action_value).cause
    examples = "\n".join(f"- {q[:200]}" for q in example_queries[:3]) or "- (none)"
    prompt = (
        "You are abstracting a reusable SKILL for a memory-augmented QA agent.\n"
        f"Failures of type '{action_value}' arise because: {cause}\n"
        "In these failures the needed evidence is usually ALREADY present in the "
        "agent's context but is mis-formatted, buried, flagged, or split across "
        "hops, so the agent does not use it.\n\n"
        "Example user queries that failed this way:\n"
        f"{examples}\n\n"
        "Write a concise, answer-facing instruction (at most 3 sentences) telling "
        "the agent HOW to locate and use the evidence already in its context to "
        "answer such queries (e.g. follow a bridge key across hops, prefer the "
        "item that resolves the key, treat present-but-flagged content as usable). "
        "Do NOT mention internal labels, pipelines, retrieval routing, or this "
        "instruction itself. Output only the instruction."
    )
    out = client.generate(prompt, system=None)
    return (out or "").strip()


# --------------------------------------------------------------------------- #
# context construction (full_ecs scaffold; pointer, no base-context leak)      #
# --------------------------------------------------------------------------- #
def _wrong_context_pointer(base_context: str) -> str:
    stripped = base_context.strip()
    if not stripped:
        return "The failed context was empty."
    return (
        "The failed retrieved-memory buffer is withheld to avoid replay "
        f"pollution; length_chars={len(stripped)}, line_count={len(stripped.splitlines())}."
    )


def _build_contexts(*, base_context, corrected_context, label, cause, guidance, skill):
    wrong_cause = (
        f"[Error]\nThe previous memory context failed under {label}.\n\n"
        f"[Cause]\n{cause}\n\n"
        f"[Wrong Context Pointer]\n{_wrong_context_pointer(base_context)}"
    )
    framed_corrected = "\n\n".join((wrong_cause, "[Corrected Memory]", corrected_context))
    return {
        "corrected_only": corrected_context,
        "ecs_no_guidance": framed_corrected,
        "ecs_template": "\n\n".join((framed_corrected, "[Repair Guidance]", guidance)),
        "ecs_skill": "\n\n".join((framed_corrected, "[Repair Guidance]", skill or guidance)),
    }


def _agent_answer(client, query: str, context: str) -> str:
    prompt = "\n\n".join(("CONTEXT:", context or "(empty)", "QUERY:", query, "ANSWER:"))
    out = client.generate(prompt, system=AGENT_SYSTEM_PROMPT)
    return out.strip() if out else ""


# --------------------------------------------------------------------------- #
# main                                                                        #
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "real_multihop_cases.json"))
    parser.add_argument("--ecs-detail", default=str(OUT / "ecs_structure_ablation_detail.csv"))
    parser.add_argument("--prior-bank", default=str(OUT / "exhaustive_detail_mincredit05.csv"))
    parser.add_argument(
        "--labels", default="injection_error,safety_error",
        help="Comma-separated residual labels to target (the clean generative subset).",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-credit", type=float, default=0.05)
    parser.add_argument("--answer-recovered-threshold", type=float, default=0.8)
    parser.add_argument("--skill-examples", type=int, default=3)
    args = parser.parse_args()

    labels = {l.strip() for l in args.labels.split(",") if l.strip()}
    ecs_path = Path(args.ecs_detail)
    if not ecs_path.exists():
        raise SystemExit(
            f"ECS detail not found: {ecs_path}\n"
            "Run Exp17 first (it defines the structural-repair residual)."
        )
    residual_ids = _load_residual_ids(ecs_path, labels)
    if not residual_ids:
        raise SystemExit(f"no residual cases for labels={labels} in {ecs_path}")

    bank = _load_prior_bank(Path(args.prior_bank)) if Path(args.prior_bank).exists() else {}

    client = LLMClient(LLMClientConfig())
    assert_g_eval_available(client, role="generative-guidance-residual")
    verifier = build_answer_verifier(client, answer_mode="answer-rubric")

    all_cases = [c for c in load_probe_cases(args.cases) if c.perturbation_label in PIPELINE_STEP_ACTIONS]
    residual = [c for c in all_cases if c.case_id in residual_ids]
    if args.limit:
        residual = residual[: args.limit]

    # Pre-build one answer-facing skill per target action, from OTHER (non-residual)
    # cases' queries only. Gold-free.
    skills: dict[str, str] = {}
    for lbl in sorted(labels):
        examples = tuple(
            c.query for c in all_cases
            if c.perturbation_label == lbl and c.case_id not in residual_ids
        )[: args.skill_examples]
        skills[lbl] = _generate_action_skill(client, lbl, examples)
        print(f"[skill:{lbl}] {skills[lbl][:160]}")

    from cmd_audit.harness import _initial_mcts_context, _retrieved_memory_items

    tally = defaultdict(lambda: [0, 0])
    tally_by_label = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    detail_rows = []
    print(f"\nGenerative-guidance residual test over {len(residual)} cases, labels={sorted(labels)}\n")

    for i, case in enumerate(residual, start=1):
        recall_set = _retrieved_memory_items(case)
        max_depth = max(1, min(3, len(recall_set) or 1))
        base_ctx = _initial_mcts_context(case, recall_set)
        cfg = {"candidate_items": case.extracted_memory, "raw_events": case.raw_events}
        gold = case.perturbation_label

        # structural best-effort repair point (gold-free): own culprit, else global prior.
        _credits, culprit = _evaluate_case(case, client, verifier, min_credit=args.min_credit)
        if culprit is not None:
            gp, action, _credit = culprit
            point_src = "own_culprit"
        else:
            choice = _first_legal_choice(_global_top_pairs(bank, case.case_id), recall_set, max_depth)
            if choice is None:
                print(f"  [{i}/{len(residual)}] {gold:16s} SKIP (no legal repair point)")
                continue
            gp, action = choice
            point_src = "global_prior"

        corrected_context = apply_pipeline_action(action, base_ctx, recall_set, gp, intervention_config=cfg)
        repair_action = get_targeted_repair_action_v1(action.value)
        contexts = _build_contexts(
            base_context=base_ctx,
            corrected_context=corrected_context,
            label=action.value,
            cause=repair_action.cause,
            guidance=repair_action.repair_guidance,
            skill=skills.get(gold, ""),
        )

        per_arm = {}
        for arm in ARMS:
            answer = _agent_answer(client, case.query, contexts[arm])
            score = score_answer_with_verifier(verifier, answer, case.gold_answer)
            recovered = score >= args.answer_recovered_threshold
            per_arm[arm] = (score, recovered, answer)
            tally[arm][0] += int(recovered); tally[arm][1] += 1
            tally_by_label[gold][arm][0] += int(recovered); tally_by_label[gold][arm][1] += 1
            detail_rows.append({
                "case_id": case.case_id,
                "gold_label": gold,
                "repair_point": f"gp{gp}:{action.value}",
                "point_source": point_src,
                "arm": arm,
                "answer_score": f"{score:.4f}",
                "recovered": str(recovered).lower(),
                "answer": answer.replace("\n", " ")[:500],
            })
        print(
            f"  [{i}/{len(residual)}] {gold:16s} gp{gp}:{action.value:18s} "
            f"corrected={'Y' if per_arm['corrected_only'][1] else '.'} "
            f"template={'Y' if per_arm['ecs_template'][1] else '.'} "
            f"skill={'Y' if per_arm['ecs_skill'][1] else '.'}"
        )

    _print_summary(tally, tally_by_label, detail_rows)

    detail_path = write_csv_table(
        OUT / "generative_guidance_residual_detail.csv",
        ["case_id", "gold_label", "repair_point", "point_source", "arm",
         "answer_score", "recovered", "answer"],
        detail_rows,
        sandbox_root=OUT,
    )
    print(f"\nWrote {detail_path}")


def _paired(detail_rows, a, b):
    """McNemar discordant counts (a-only, b-only) over cases scored on both arms."""
    by_case = defaultdict(dict)
    for r in detail_rows:
        by_case[r["case_id"]][r["arm"]] = r["recovered"] == "true"
    a_only = b_only = 0
    for m in by_case.values():
        if a in m and b in m:
            if m[a] and not m[b]: a_only += 1
            elif m[b] and not m[a]: b_only += 1
    return a_only, b_only


def _print_summary(tally, tally_by_label, detail_rows) -> None:
    print("\n=== Recovery rate per arm (residual cases) ===")
    print(f"{'arm':16s} {'recovered':>9s} {'total':>6s} {'rate':>8s}")
    for arm in ARMS:
        rec, tot = tally[arm]
        print(f"{arm:16s} {rec:>9d} {tot:>6d} {(rec/tot if tot else 0):>8.4f}")
    print("\n=== Per label ===")
    for lbl in sorted(tally_by_label):
        cells = " ".join(
            f"{arm}={tally_by_label[lbl][arm][0]}/{tally_by_label[lbl][arm][1]}" for arm in ARMS
        )
        print(f"  {lbl:16s} {cells}")
    sn, ns = _paired(detail_rows, "ecs_skill", "ecs_no_guidance")
    tn, nt = _paired(detail_rows, "ecs_template", "ecs_no_guidance")
    sa, ta = _paired(detail_rows, "ecs_skill", "ecs_template")
    print("\n=== Paired marginals (frame + corrected content fixed) ===")
    print(f"  ecs_skill    vs ecs_no_guidance: skill-wins={sn}   no_guidance-wins={ns}   <- DOES THE SKILL TEXT ADD VALUE")
    print(f"  ecs_template vs ecs_no_guidance: template-wins={tn} no_guidance-wins={nt}   <- boilerplate harm/help")
    print(f"  ecs_skill    vs ecs_template   : skill-wins={sa}   template-wins={ta}        <- skill vs boilerplate")
    print("  Verdict on the skill: positive only if (ecs_skill - ecs_no_guidance) is clearly > 0.")
    print("  If ecs_no_guidance >= ecs_skill, the guidance TEXT layer (yours included) has no value;")
    print("  the recovery is carried by the error+cause frame + corrected content.")


if __name__ == "__main__":
    main()
