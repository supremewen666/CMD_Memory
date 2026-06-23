#!/usr/bin/env python3
"""Offline prequential trajectory: tier-1 seed-hit before vs after the
content-fingerprint key fix. No LLM endpoint required.

Mechanism-layer proxy for the C7 money plot. We replay Exp18's already-scored
recovered ledger as a stream. At each case we FIRST query the store built from
all *earlier* cases (honest prequential -- no leak), record whether the seed
contains the gold (gen_point, action), THEN add the case. Two arms differ only
in the retrieval key:

    query   -- legacy query-keyword signature (memory_texts omitted)
    fp      -- recall content fingerprint (memory_texts supplied)

A rising fp curve that the query arm lacks is the mechanism-level evidence that
the flat C7 trajectory was a retrieval-key failure, not an inability to learn.
This does NOT replace the live Exp18 run (real recovery rate / rollouts); it
isolates the seed-hit signal that drives it.

Usage:
    python -m experiments.offline_c7_seed_curve \
        --ledger artifacts/sandbox/failure_memory_trajectory_detail.csv \
        --cases data/probe_cases/real_recurrent_cases.json \
        --out artifacts/sandbox/c7_seed_curve.csv
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.labels import PIPELINE_STEP_ACTIONS
from cmd_audit.data_io import load_probe_cases
from cmd_audit.repair.failure_memory import FailureMemoryStore, StepLevelRecord
from experiments.run_experiment_18_failure_memory_trajectory import _retrieve_seed_pairs

_GEN = re.compile(r"generation point (\d+)")


def _recovered_rows(path: Path) -> list[dict[str, str]]:
    rows = [
        r
        for r in csv.DictReader(path.open(encoding="utf-8"))
        if r["recovered"].strip().lower() == "true"
        and r["ledger_error_type"] in PIPELINE_STEP_ACTIONS
    ]
    rows.sort(key=lambda r: int(r["case_index"]))
    return rows


def _gen_point(cause: str) -> int:
    m = _GEN.search(cause)
    if not m:
        raise ValueError(f"no generation point in {cause!r}")
    return int(m.group(1))


def _run_arm(rows, cases_by_id, *, use_fp: bool, topk: int, neighbors: int):
    """Prequential pass: seed measured against earlier cases only, then add."""
    store = FailureMemoryStore()
    per_case: list[tuple[bool, bool]] = []  # (action_hit, pair_hit)
    for r in rows:
        case = cases_by_id.get(r["case_id"])
        if case is None:
            continue
        gold_gp = _gen_point(r["ledger_cause"])
        gold_action = r["ledger_error_type"]
        texts = tuple(m.text for m in case.extracted_memory) if use_fp else ()
        max_depth = max(1, min(3, len(case.extracted_memory) or 1))

        pairs, _ = _retrieve_seed_pairs(
            store, case.query, max_depth=max_depth, topk=topk,
            neighbors=neighbors, memory_texts=texts,
        )
        actions = [a for _gp, a in pairs]
        per_case.append((gold_action in actions, (gold_gp, gold_action) in pairs))

        store.add(StepLevelRecord.from_mcts_result(
            query=case.query, hop_index=gold_gp + 1, label=gold_action,
            cause=r["ledger_cause"], corrected_memory="", repair_guidance="g",
            recovery_success=True, recovery_gain=float(r["ledger_recovery_gain"] or 0.0),
            memory_texts=texts,
        ))
    return per_case


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ledger", default="artifacts/sandbox/failure_memory_trajectory_detail.csv")
    p.add_argument("--cases", default="data/probe_cases/real_recurrent_cases.json")
    p.add_argument("--out", default="artifacts/sandbox/c7_seed_curve.csv")
    p.add_argument("--bin-size", type=int, default=50)
    p.add_argument("--topk", type=int, default=2)
    p.add_argument("--neighbors", type=int, default=10)
    args = p.parse_args()

    rows = _recovered_rows(Path(args.ledger))
    if not rows:
        raise SystemExit("no recovered ledger rows")
    cases_by_id = {c.case_id: c for c in load_probe_cases(args.cases)}

    query_arm = _run_arm(rows, cases_by_id, use_fp=False, topk=args.topk, neighbors=args.neighbors)
    fp_arm = _run_arm(rows, cases_by_id, use_fp=True, topk=args.topk, neighbors=args.neighbors)
    n = len(query_arm)

    out_rows = []
    for start in range(0, n, args.bin_size):
        end = min(start + args.bin_size, n)
        q = query_arm[start:end]
        f = fp_arm[start:end]
        out_rows.append({
            "bin_start": str(start + 1),
            "bin_end": str(end),
            "cases": str(end - start),
            "query_action_hit": f"{sum(a for a, _ in q) / len(q):.4f}",
            "query_pair_hit": f"{sum(pp for _, pp in q) / len(q):.4f}",
            "fp_action_hit": f"{sum(a for a, _ in f) / len(f):.4f}",
            "fp_pair_hit": f"{sum(pp for _, pp in f) / len(f):.4f}",
        })

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)

    def overall(arm):
        return sum(a for a, _ in arm) / len(arm), sum(pp for _, pp in arm) / len(arm)

    qa, qp = overall(query_arm)
    fa, fp = overall(fp_arm)
    print(f"prequential stream, n={n}, bins of {args.bin_size}")
    print(f"  query-key  : action {qa:.1%}  (gp,action) {qp:.1%}")
    print(f"  fp-key     : action {fa:.1%}  (gp,action) {fp:.1%}")
    print(f"  delta      : action {fa - qa:+.1%}  (gp,action) {fp - qp:+.1%}")
    print(f"\nper-bin curve -> {out}")
    print(f"{'bin':>9} {'q_act':>7} {'fp_act':>7} {'q_pair':>7} {'fp_pair':>8}")
    for r in out_rows:
        print(f"{r['bin_start']:>4}-{r['bin_end']:<4} "
              f"{float(r['query_action_hit']):>7.2f} {float(r['fp_action_hit']):>7.2f} "
              f"{float(r['query_pair_hit']):>7.2f} {float(r['fp_pair_hit']):>8.2f}")


if __name__ == "__main__":
    main()
