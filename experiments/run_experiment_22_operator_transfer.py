#!/usr/bin/env python3
"""Experiment 22: do COMPOSITE operators TRANSFER? (the evolvability proof)

Exp21 showed richer operators (double / parameterized) recover +37 residual cases
over single-point -- but it found each case's best operator by EXHAUSTIVE per-case
search. That proves a ceiling exists, NOT that a learned library evolves: bigger
search != transfer. This experiment closes the gap, exactly as Exp15 did for the
single-point operator.

Leave-one-out: for each residual case, build the operator seed from the OTHER
residual cases' best operators only (never the held-out case's own), retrieve by
recall-content fingerprint, apply, and take the best recovery. Compare to the
held-out case's own exhaustive operator (oracle ceiling from Exp21).

Arms:
  no_repair   identity backbone floor
  single_xfer single-point operator transferred (the Exp15-style baseline)
  comp_oracle held-out case's OWN best composite operator (Exp21 ceiling; upper bound)
  comp_global most frequent composite operator shape over OTHER cases (blind to query)
  comp_bm25   most frequent composite among BM25-nearest OTHER cases (query-keyword key)
  comp_fp     most frequent composite among CONTENT-FINGERPRINT-nearest OTHER cases
              -- the key C7 validated (90.8%% single-point seed hit). The first two
              transfer keys (query-BM25, global) FAILED in the v1 run (0.37x / 0.51x
              oracle); C7 showed query-keyword keys cannot lock chain identity and a
              recall-content fingerprint is paraphrase-invariant. comp_fp is the fair
              test of whether composites transfer under the RIGHT key.
  random_topN same top-N execution budget as comp_fp_topN, but candidate composite
              operators are sampled uniformly from OTHER cases. This isolates the
              library key from the mere effect of trying several operators.

VERDICT:
  comp_bm25 / comp_global ~ comp_oracle  -> composite operators transfer; the skill
    library captures the headroom without per-case search -> evolvability is real,
    build the library.
  comp_* collapse to single_xfer         -> composite operators are case-specific,
    not transferable; evolution stays at per-case search, the library adds nothing.

Gold-free: operators read recall/store metadata + text, never case.gold_*; fitness
is recovery gain (gold answer scores only). NO label is a prediction target.

Prereq: Exp21 detail (the operator bank).
Run:
    export LLM_TIMEOUT=120
    python -m experiments.run_experiment_22_operator_transfer \
        --operator-bank artifacts/sandbox/operator_headroom_detail.csv
Smoke: --limit 8.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.labels import PIPELINE_STEP_ACTIONS
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.data_io import load_probe_cases
from cmd_audit.eval.writers import write_csv_table
from cmd_audit.counterfactual.actions import PipelineAction, get_legal_actions
from cmd_audit.repair.efficacy import LABEL_TO_ACTION
from cmd_audit.scoring.retrieval import compute_bm25_scores, tokenize
# C7-validated content-fingerprint key (paraphrase-invariant; 90.8% single-point
# seed hit) -- imported, not re-copied, so comp_fp uses the SAME key C7 validated.
from cmd_audit.repair.failure_memory import (
    _memory_fingerprint,
    _query_signature_similarity,
)
from experiments.experiment_runner_common import (
    DATA, OUT, assert_g_eval_available, build_answer_verifier,
)
from experiments.probe_exhaustive import _step_context, _own_recovery

_ACTION_BY_NAME = {a.value: a for a in PipelineAction}


def _parse_op(op_str: str):
    """'gp0:retrieval_error+gp1:injection_error' -> [(0, PipelineAction), ...]; '' -> []."""
    op_str = (op_str or "").split("|")[0].strip()
    if not op_str:
        return []
    out = []
    for tok in op_str.split("+"):
        tok = tok.strip()
        if not tok.startswith("gp") or ":" not in tok:
            continue
        gp_s, act = tok[2:].split(":", 1)
        action = _ACTION_BY_NAME.get(act) or LABEL_TO_ACTION.get(act)
        if action is not None:
            out.append((int(gp_s), action))
    return out


def _op_shape(op):
    """Canonical hashable shape (gp, action_value) tuple for frequency counting."""
    return tuple((gp, a.value) for gp, a in op)


def _load_bank(path: Path):
    """case_id -> {'single': [...], 'comp': [...], 'label':...} from Exp21 detail."""
    bank = {}
    for r in csv.DictReader(path.open(encoding="utf-8")):
        # choose the case's best recovering operator: richer pick (double vs param)
        d, pm = float(r.get("double_net") or -1), float(r.get("param_net") or -1)
        comp = _parse_op(r["double_op"] if d >= pm else r["param_op"])
        bank[r["case_id"]] = {
            "single": _parse_op(r["single_op"]),
            "comp": comp,
            "label": r["gold_label"],
        }
    return bank


def _modal_shape(shapes):
    if not shapes:
        return None
    return Counter(shapes).most_common(1)[0][0]


def _distinct_shapes(shapes):
    """Deduplicate non-empty operator shapes, preserving first-seen order."""
    out = []
    seen = set()
    for shape in shapes:
        if not shape or shape in seen:
            continue
        seen.add(shape)
        out.append(shape)
    return out


def _random_topn_shapes(shapes, *, case_id: str, seed: int, topn: int):
    """Deterministic held-out random control over OTHER-case operator shapes."""
    candidates = _distinct_shapes(shapes)
    rng = random.Random(f"{seed}:{case_id}")
    rng.shuffle(candidates)
    return candidates[: max(0, topn)]


def _shape_to_ops(shape, recall, max_depth):
    """Map a canonical shape back to legal (gp, PipelineAction) ops for THIS case."""
    if not shape:
        return []
    ops = []
    for gp, av in shape:
        a = _ACTION_BY_NAME.get(av)
        if a is None or a == PipelineAction.IDENTITY:
            continue
        if 0 <= gp < max_depth and a in get_legal_actions(recall, gp):
            ops.append((gp, a))
    return ops


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cases", default=str(DATA / "real_multihop_cases.json"))
    p.add_argument("--operator-bank", default=str(OUT / "operator_headroom_detail.csv"))
    p.add_argument("--out", default=str(OUT / "operator_transfer_detail.csv"),
                   help="output CSV path; use a run-specific name for repeated confirmation runs.")
    p.add_argument("--neighbors", type=int, default=10)
    p.add_argument("--topn", type=int, default=5,
                   help="comp_fp_topN: # distinct fp-nearest candidate operators to try "
                        "(retrieve-execute-keep-first-that-recovers; the deployable library).")
    p.add_argument("--random-seed", type=int, default=22,
                   help="deterministic seed for the same-budget random_topN control.")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--recovered-threshold", type=float, default=0.1)
    args = p.parse_args()

    bank_path = Path(args.operator_bank)
    if not bank_path.exists():
        raise SystemExit(f"operator bank not found: {bank_path} (run Exp21 first).")
    bank = _load_bank(bank_path)

    client = LLMClient(LLMClientConfig())
    assert_g_eval_available(client, role="operator-transfer")
    verifier = build_answer_verifier(client, answer_mode="answer-rubric")

    all_cases = {c.case_id: c for c in load_probe_cases(args.cases)
                 if c.perturbation_label in PIPELINE_STEP_ACTIONS}
    residual = [all_cases[cid] for cid in bank if cid in all_cases]
    if args.limit:
        residual = residual[: args.limit]
    queries = {cid: all_cases[cid].query for cid in bank if cid in all_cases}
    # C7 content fingerprint per case, over extracted_memory (faithful to
    # build_skill_library_from_ledger._memory_texts, which keys on the full
    # retrieved memory, not gold). Precomputed once for LOO nearest-fingerprint.
    fingerprints = {
        cid: _memory_fingerprint(tuple(m.text for m in all_cases[cid].extracted_memory))
        for cid in bank if cid in all_cases
    }

    from cmd_audit.harness import _initial_mcts_context, _retrieved_memory_items

    arms = [
        "no_repair",
        "single_xfer",
        "comp_oracle",
        "comp_global",
        "comp_bm25",
        "comp_fp",
        "comp_fp_topN",
        "random_topN",
    ]
    tally = defaultdict(lambda: [0, 0])
    detail = []
    print(f"Operator transfer (LOO) over {len(residual)} residual cases\n")

    for i, case in enumerate(residual, start=1):
        cid = case.case_id
        recall = _retrieved_memory_items(case)
        max_depth = max(1, min(3, len(recall) or 1))
        base_ctx = _initial_mcts_context(case, recall)
        cfg = {"candidate_items": case.extracted_memory, "raw_events": case.raw_events}
        gold = case.gold_answer
        baseline = case.primary_baseline.answer_score

        def run_ops(ops):
            ctx = base_ctx
            abg = {gp: a for gp, a in ops}
            for gp in range(max_depth):
                ctx = _step_context(client, ctx, abg.get(gp, PipelineAction.IDENTITY),
                                    recall, gp, cfg)
            return _own_recovery(client, ctx, max_depth, max_depth, recall, gold, verifier, baseline)

        base_gain = run_ops([])

        # transfer seeds (LOO: never this case's own bank row)
        others = [(c2, b2) for c2, b2 in bank.items() if c2 != cid]
        comp_shapes_all = [_op_shape(b2["comp"]) for _c, b2 in others if b2["comp"]]
        single_shapes_all = [_op_shape(b2["single"]) for _c, b2 in others if b2["single"]]

        # bm25 nearest others (query-keyword key -- the one that FAILED in v1)
        held_q = queries.get(cid, "")
        cand_ids = [c2 for c2, _b in others]
        comp_bm25_shapes = comp_shapes_all
        if held_q and cand_ids:
            qt = tokenize(held_q)
            dt = [tokenize(queries.get(c2, "")) for c2 in cand_ids]
            scores = compute_bm25_scores(qt, dt)
            near = sorted(range(len(cand_ids)), key=lambda j: scores[j], reverse=True)[: args.neighbors]
            near_ids = {cand_ids[j] for j in near}
            comp_bm25_shapes = [_op_shape(bank[c2]["comp"]) for c2 in near_ids if bank[c2]["comp"]]

        # fingerprint nearest others (C7 content key -- the fair test). Rank OTHER
        # cases by Jaccard similarity of recall-content fingerprint, take top-N,
        # use the modal composite shape among them.
        held_fp = fingerprints.get(cid, "")
        comp_fp_shapes = comp_shapes_all
        fp_topn_shapes = []  # distinct candidate shapes, ranked by fp similarity (for topN arm)
        if held_fp and cand_ids:
            sims = sorted(
                ((c2, _query_signature_similarity(held_fp, fingerprints.get(c2, ""))) for c2 in cand_ids),
                key=lambda t: t[1], reverse=True,
            )
            near_fp = {c2 for c2, s in sims[: args.neighbors] if s > 0.0}
            fp_shapes = [_op_shape(bank[c2]["comp"]) for c2 in near_fp if bank[c2]["comp"]]
            if fp_shapes:
                comp_fp_shapes = fp_shapes
            # distinct candidate shapes for topN, kept in similarity order (most
            # similar source case first), deduplicated -- the library's retrieval list.
            seen = set()
            for c2, s in sims:
                if s <= 0.0:
                    break
                sh = _op_shape(bank[c2]["comp"]) if bank[c2]["comp"] else None
                if sh and sh not in seen:
                    seen.add(sh)
                    fp_topn_shapes.append(sh)
                if len(fp_topn_shapes) >= args.topn:
                    break

        arm_ops = {
            "no_repair": [],
            "single_xfer": _shape_to_ops(_modal_shape(single_shapes_all), recall, max_depth),
            "comp_oracle": bank[cid]["comp"],  # own ceiling
            "comp_global": _shape_to_ops(_modal_shape(comp_shapes_all), recall, max_depth),
            "comp_bm25": _shape_to_ops(_modal_shape(comp_bm25_shapes), recall, max_depth),
            "comp_fp": _shape_to_ops(_modal_shape(comp_fp_shapes), recall, max_depth),
        }

        per = {}
        for arm in arms:
            if arm in {"comp_fp_topN", "random_topN"}:
                continue  # handled separately below (retrieve-execute-keep-best)
            net = (run_ops(arm_ops[arm]) - base_gain) if arm_ops[arm] else 0.0
            recd = net > args.recovered_threshold
            per[arm] = (net, recd)
            tally[arm][0] += int(recd); tally[arm][1] += 1

        # comp_fp_topN: the deployable library mechanism -- try each retrieved
        # candidate operator in fp-similarity order, stop at the first that recovers
        # (accept-if-improves). Records cost = #operators tried before success.
        def run_topn_shapes(candidate_shapes):
            best_net, cost, best_op = 0.0, 0, []
            for rank, shape in enumerate(candidate_shapes, start=1):
                ops = _shape_to_ops(shape, recall, max_depth)
                if not ops:
                    continue
                cost = rank
                cand_net = run_ops(ops) - base_gain
                if cand_net > best_net:
                    best_net, best_op = cand_net, ops
                if cand_net > args.recovered_threshold:
                    break  # accept-if-improves: first recovering operator wins
            return best_net, cost, best_op

        topn_net, topn_cost, topn_op = run_topn_shapes(fp_topn_shapes)
        topn_recd = topn_net > args.recovered_threshold
        per["comp_fp_topN"] = (topn_net, topn_recd)
        tally["comp_fp_topN"][0] += int(topn_recd); tally["comp_fp_topN"][1] += 1

        random_topn_shapes = _random_topn_shapes(
            comp_shapes_all,
            case_id=cid,
            seed=args.random_seed,
            topn=args.topn,
        )
        random_net, random_cost, random_op = run_topn_shapes(random_topn_shapes)
        random_recd = random_net > args.recovered_threshold
        per["random_topN"] = (random_net, random_recd)
        tally["random_topN"][0] += int(random_recd); tally["random_topN"][1] += 1

        detail.append({
            "case_id": cid, "gold_label": case.perturbation_label,
            **{f"{a}_net": f"{per[a][0]:.4f}" for a in arms},
            **{f"{a}_rec": str(per[a][1]).lower() for a in arms},
            "topn_cost": str(topn_cost), "topn_candidates": str(len(fp_topn_shapes)),
            "random_topn_cost": str(random_cost),
            "random_topn_candidates": str(len(random_topn_shapes)),
            "comp_oracle_op": "+".join(f"gp{gp}:{x.value}" for gp, x in arm_ops["comp_oracle"]),
            "comp_fp_op": "+".join(f"gp{gp}:{x.value}" for gp, x in arm_ops["comp_fp"]),
            "comp_fp_topN_op": "+".join(f"gp{gp}:{x.value}" for gp, x in topn_op),
            "random_topN_op": "+".join(f"gp{gp}:{x.value}" for gp, x in random_op),
        })
        print(f"  [{i}/{len(residual)}] {case.perturbation_label:16s} "
              f"oracle={'Y' if per['comp_oracle'][1] else '.'} "
              f"topN={'Y' if topn_recd else '.'}(c{topn_cost}) "
              f"rand={'Y' if random_recd else '.'}(c{random_cost}) "
              f"fp={'Y' if per['comp_fp'][1] else '.'} "
              f"bm25={'Y' if per['comp_bm25'][1] else '.'} "
              f"single={'Y' if per['single_xfer'][1] else '.'}")

    _summary(tally, arms)
    path = write_csv_table(
        Path(args.out),
        ["case_id", "gold_label",
         *[f"{a}_net" for a in arms], *[f"{a}_rec" for a in arms],
         "topn_cost", "topn_candidates",
         "random_topn_cost", "random_topn_candidates",
         "comp_oracle_op", "comp_fp_op", "comp_fp_topN_op", "random_topN_op"],
        detail, sandbox_root=OUT,
    )
    print(f"\nWrote {path}")


def _summary(tally, arms):
    print("\n=== Recovery rate per transfer arm (residual) ===")
    print(f"{'arm':14s} {'recovered':>9s} {'total':>6s} {'rate':>8s} {'vs_oracle':>10s}")
    orate = (tally["comp_oracle"][0] / tally["comp_oracle"][1]) if tally["comp_oracle"][1] else 0.0
    for a in arms:
        r, t = tally[a]
        rate = r / t if t else 0.0
        frac = f"{rate / orate:.2f}" if orate and a != "no_repair" else "-"
        print(f"{a:14s} {r:>9d} {t:>6d} {rate:>8.4f} {frac:>10s}")
    print("\n  DECISION ARM = comp_fp_topN (deployable library: retrieve top-N fp-nearest")
    print("  operators, execute each, keep first that recovers = accept-if-improves).")
    print("  random_topN = same execution budget, random OTHER-case operators.")
    print("  comp_fp = one-shot modal guess (lower bound); comp_oracle = per-case ceiling.")
    print("  GO (build library): comp_fp_topN >= ~0.8 of comp_oracle AND clearly beats")
    print("     single_xfer/comp_bm25/random_topN -> composites transfer + the library key captures them.")
    print("  SOFT-GO: comp_fp_topN > comp_fp > single_xfer but < 0.8 oracle -> transfer real but partial.")
    print("  NO-GO: comp_fp_topN ~ single_xfer -> composites case-specific even with C7 key + topN.")
    if tally.get("comp_fp_topN", [0, 0])[1]:
        print("  (See topn_cost in CSV for #operators tried before recovery -> the efficiency story.)")
    print("  NOTE: inference non-determinism ~37%% churn -> run >=2x; cite within-run paired McNemar")
    print("        (analyze_operator_transfer.py), not the drifting vs_oracle ratio.")


if __name__ == "__main__":
    main()
