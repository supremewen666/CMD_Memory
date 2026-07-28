#!/usr/bin/env python3
"""Paired analysis of Exp22 operator_transfer_detail.csv (pure post-processing, NO LLM).

The transfer recovery RATES are confounded by inference non-determinism (~37%% churn
moves the oracle 59->69 across runs), so absolute vs_oracle fractions drift. The
trustworthy statistic is WITHIN-RUN paired discordance: for each case, did arm X
recover where the comparison arm did not, and vice versa? McNemar exact over the
discordant pairs is immune to the per-run baseline drift.

Headline questions:
  - comp_fp vs single_xfer : do composite operators carry transferable info beyond single-point?
  - comp_fp vs comp_bm25   : is the C7 content-fingerprint key the cause of the gain (vs query key)?
  - comp_fp vs comp_oracle : how much of the per-case ceiling does one-shot fp-transfer leave on the table?
  - comp_fp_topN (if present) vs comp_fp / comp_oracle : does retrieve-top-N + accept-if-improves close the gap?

Usage:
    python -m experiments.analyze_operator_transfer \
        --csv artifacts/sandbox/operator_transfer_detail_run1.csv \
              artifacts/sandbox/operator_transfer_detail_run2.csv \
              artifacts/sandbox/operator_transfer_detail_run3.csv
"""
from __future__ import annotations

import argparse
import csv
from math import comb


def _exact_two_sided(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2.0 * sum(comb(n, i) for i in range(k + 1)) / (2 ** n))


def _rec(row, arm):
    """Recovered flag: prefer the explicit *_rec column, else net>0 threshold fallback."""
    rk = f"{arm}_rec"
    if rk in row and row[rk] != "":
        return row[rk].strip().lower() == "true"
    nk = f"{arm}_net"
    # "nan" is the explicit timed-out-rollout token: unmeasured, not recovered.
    return (
        nk in row
        and row[nk] not in ("", None, "nan", "NA")
        and float(row[nk]) > 0.0
    )


_COMPARISONS = [
    ("comp_fp", "single_xfer", "composites carry transferable info beyond single-point"),
    ("comp_fp", "comp_bm25", "C7 fingerprint key vs query-keyword key (is the key the cause)"),
    ("comp_fp", "comp_global", "fingerprint retrieval vs global modal"),
    ("comp_oracle", "comp_fp", "per-case ceiling left on the table by one-shot fp-transfer"),
    ("comp_fp_topN", "comp_fp", "does retrieve-topN + accept-if-improves beat one-shot guess"),
    ("comp_fp_topN", "random_topN", "fingerprint topN vs same-budget random operator topN"),
    ("comp_fp_topN", "single_xfer", "topN transfer vs single-point baseline"),
    ("comp_oracle", "comp_fp_topN", "ceiling left by topN library mechanism"),
]


def _paired(rows, a, b):
    a_only = sum(1 for row in rows if _rec(row, a) and not _rec(row, b))
    b_only = sum(1 for row in rows if _rec(row, b) and not _rec(row, a))
    return a_only, b_only, _exact_two_sided(a_only, b_only)


def _sig(p_value: float) -> str:
    if p_value < 0.001:
        return "***"
    if p_value < 0.01:
        return "**"
    if p_value < 0.05:
        return "*"
    return "ns"


def _analyze_one(path: str) -> dict:
    all_rows = list(csv.DictReader(open(path, encoding="utf-8")))
    # Drop rows the runner excluded (identity-backbone rollout timed out); they
    # have no measured net gain and would otherwise count as "not recovered".
    rows = [r for r in all_rows if r.get("excluded", "false") != "true"]
    excluded = len(all_rows) - len(rows)
    n = len(rows)
    arms = [k[:-4] for k in rows[0].keys() if k.endswith("_rec")] if rows else []
    if not arms and rows:
        arms = [k[:-4] for k in rows[0].keys() if k.endswith("_net")]
    print(f"loaded {n} cases from {path}"
          + (f" ({excluded} excluded: base_gain timeout)" if excluded else "")
          + f"\narms present: {arms}\n")

    print("=== Recovery rate per arm (this run) ===")
    rates = {}
    for arm in arms:
        recovered = sum(_rec(row, arm) for row in rows)
        rates[arm] = recovered / n if n else 0.0
        print(f"  {arm:16s} {recovered:>4d}/{n}  {rates[arm]:.4f}")

    print("\n=== Within-run paired McNemar (immune to per-run oracle drift) ===")
    print(f"{'A':16s} {'B':16s} {'A_only':>6s} {'B_only':>6s} {'p':>10s}  sig   note")
    paired_rows = {}
    for a, b, note in _COMPARISONS:
        if a not in arms or b not in arms:
            continue
        a_only, b_only, p_value = _paired(rows, a, b)
        paired_rows[(a, b)] = (a_only, b_only, p_value)
        print(
            f"{a:16s} {b:16s} {a_only:>6d} {b_only:>6d} "
            f"{p_value:>10.2e}  {_sig(p_value):4s}  {note}"
        )

    if "comp_oracle" in arms and rates.get("comp_oracle", 0) > 0:
        print("\n=== Fraction of per-case ceiling (comp_oracle=1.00 this run) ===")
        for arm in arms:
            if arm in ("no_repair", "comp_oracle"):
                continue
            print(f"  {arm:16s} {rates[arm] / rates['comp_oracle']:.2f}")
        print("  GO threshold = 0.80 of oracle. Cite the paired McNemar, not this drifting ratio.")

    return {
        "path": path,
        "n": n,
        "arms": arms,
        "rates": rates,
        "paired": paired_rows,
    }


def _print_multi_run_summary(summaries: list[dict]) -> None:
    def has_rate(summary, arm):
        return arm in summary["rates"]

    def beats(summary, left, right):
        if not has_rate(summary, left) or not has_rate(summary, right):
            return None
        return summary["rates"][left] > summary["rates"][right]

    def reaches_oracle_fraction(summary, arm, fraction):
        if not has_rate(summary, arm) or not has_rate(summary, "comp_oracle"):
            return None
        oracle = summary["rates"]["comp_oracle"]
        if oracle <= 0.0:
            return None
        return (summary["rates"][arm] / oracle) >= fraction

    print("\n=== Multi-run stability summary ===")
    print(f"{'run':>3s} {'cases':>5s} {'topN':>7s} {'random':>7s} {'single':>7s} {'oracle_frac':>11s}")
    random_wins = single_wins = oracle_hits = 0
    random_total = single_total = oracle_total = 0
    for idx, summary in enumerate(summaries, start=1):
        rates = summary["rates"]
        topn = rates.get("comp_fp_topN")
        random_rate = rates.get("random_topN")
        single = rates.get("single_xfer")
        oracle = rates.get("comp_oracle")
        oracle_frac = (topn / oracle) if topn is not None and oracle else None
        print(
            f"{idx:>3d} {summary['n']:>5d} "
            f"{_fmt_rate(topn):>7s} {_fmt_rate(random_rate):>7s} "
            f"{_fmt_rate(single):>7s} {_fmt_rate(oracle_frac):>11s}"
        )
        random_cmp = beats(summary, "comp_fp_topN", "random_topN")
        if random_cmp is not None:
            random_total += 1
            random_wins += int(random_cmp)
        single_cmp = beats(summary, "comp_fp_topN", "single_xfer")
        if single_cmp is not None:
            single_total += 1
            single_wins += int(single_cmp)
        oracle_cmp = reaches_oracle_fraction(summary, "comp_fp_topN", 0.8)
        if oracle_cmp is not None:
            oracle_total += 1
            oracle_hits += int(oracle_cmp)

    print(
        f"comp_fp_topN beats random_topN in {random_wins}/{random_total} run(s); "
        f"beats single_xfer in {single_wins}/{single_total} run(s); "
        f"reaches >=0.80 oracle in {oracle_hits}/{oracle_total} run(s)."
    )


def _fmt_rate(value) -> str:
    return "-" if value is None else f"{value:.3f}"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--csv",
        nargs="+",
        default=["artifacts/sandbox/operator_transfer_detail.csv"],
        help="one or more Exp22 detail CSVs; pass three run-specific files for confirmation",
    )
    args = p.parse_args()

    summaries = []
    for idx, path in enumerate(args.csv, start=1):
        if len(args.csv) > 1:
            print(f"\n### Exp22 run {idx}: {path}\n")
        summaries.append(_analyze_one(path))
    if len(summaries) > 1:
        _print_multi_run_summary(summaries)


if __name__ == "__main__":
    main()
