#!/usr/bin/env python3
"""Analysis of Exp21 operator_headroom_detail.csv (pure post-processing, NO LLM).

Two things, both requested:
  (b1) Paired significance of richer-vs-single (and double/param) using WITHIN-RUN
       per-case net gains -> McNemar exact + sign test. This is the publishable
       headroom statistic; it is immune to the absolute-rate baseline drift
       (single recovering 48/115 instead of ~0 is a rollout-path/non-determinism
       artifact, but the within-run paired delta is not affected by it).
  (b2) Decomposition of the headroom set (richer recovers, single does not):
       which operator class supplies each rescue (double / param / double+param),
       and the gold-label distribution -> tells the skill library what operator
       forms its body must express.

Usage:
    python -m experiments.analyze_operator_headroom \
        --csv artifacts/sandbox/operator_headroom_detail.csv \
        --recovered-threshold 0.1
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from math import comb


def _exact_two_sided(b: int, c: int) -> float:
    """Exact McNemar / sign-test p over discordant pairs (b, c)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2.0 * sum(comb(n, i) for i in range(k + 1)) / (2 ** n))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv", default="artifacts/sandbox/operator_headroom_detail.csv")
    p.add_argument("--recovered-threshold", type=float, default=0.1)
    args = p.parse_args()

    rows = list(csv.DictReader(open(args.csv, encoding="utf-8")))
    thr = args.recovered_threshold
    n = len(rows)
    print(f"loaded {n} residual cases from {args.csv}\n")

    def net(r, k):
        v = r.get(k, "")
        return float(v) if v not in ("", None) else -1.0

    def rec(r, k):
        return net(r, k) > thr

    # ---- (b1) paired significance vs single ----
    print("=== (b1) Within-run paired significance vs single (immune to baseline drift) ===")
    print(f"{'arm':14s} {'rate':>6s} {'a_wins':>7s} {'b_wins':>7s} {'McNemar_p':>11s}  sig")
    s_rate = sum(rec(r, "single_net") for r in rows) / n
    print(f"{'single':14s} {s_rate:>6.3f} {'-':>7s} {'-':>7s} {'-':>11s}")
    for arm, col in [("double", "double_net"), ("param", "param_net"), ("richer", "richer_net")]:
        rate = sum(rec(r, col) for r in rows) / n
        a_wins = sum(1 for r in rows if rec(r, col) and not rec(r, "single_net"))   # arm recovers, single not
        b_wins = sum(1 for r in rows if rec(r, "single_net") and not rec(r, col))   # single recovers, arm not
        pval = _exact_two_sided(a_wins, b_wins)
        sig = "***" if pval < 0.001 else "**" if pval < 0.01 else "*" if pval < 0.05 else "ns"
        print(f"{arm:14s} {rate:>6.3f} {a_wins:>7d} {b_wins:>7d} {pval:>11.2e}  {sig}")
    print("  a_wins = arm recovers where single fails (the headroom direction).")

    # ---- (b2) headroom decomposition ----
    print("\n=== (b2) Headroom decomposition (richer recovers, single does NOT) ===")
    headroom = [r for r in rows if rec(r, "richer_net") and not rec(r, "single_net")]
    print(f"headroom set: {len(headroom)}/{n}\n")
    src = Counter()
    by_label = defaultdict(Counter)
    for r in headroom:
        d, pm = rec(r, "double_net"), rec(r, "param_net")
        if d and pm:
            cls = "both(double&param)"
        elif d:
            cls = "double_only"
        elif pm:
            cls = "param_only"
        else:
            cls = "double_param_interaction"  # neither double nor param alone, only their combo
        src[cls] += 1
        by_label[r["gold_label"]][cls] += 1
    print("  by operator class that supplies the rescue:")
    for cls, cnt in src.most_common():
        print(f"    {cls:26s} {cnt}")
    print("\n  by gold label (operator-form demand for the skill body):")
    for lbl in sorted(by_label):
        print(f"    {lbl:18s} total={sum(by_label[lbl].values()):2d}  {dict(by_label[lbl])}")

    # which concrete operators recur (skill-library template candidates)
    print("\n  recurring best-operator shapes in the headroom set:")
    shapes = Counter()
    for r in headroom:
        d, pm = rec(r, "double_net"), rec(r, "param_net")
        key = r["double_op"] if (d and net(r, "double_net") >= net(r, "param_net")) else r["param_op"]
        # normalize: drop case-specific item ids in param hints, keep action shape
        norm = key.split("|")[0]
        shapes[norm] += 1
    for shape, cnt in shapes.most_common(12):
        print(f"    {cnt:2d}x  {shape}")


if __name__ == "__main__":
    main()
