#!/usr/bin/env python3
"""Paired significance post-processing over existing artifact detail CSVs.

Pure post-processing — reads the per-case detail CSVs already written by
Exp14/15/17 and computes paired effect sizes with confidence intervals and
exact McNemar tests. No LLM calls. This is the "paired deltas / bootstrap
intervals" pre-table step listed in EXPERIMENT.md.

Each comparison is PAIRED on case_id: the same case is repaired under two arms,
so recovery is a matched binary outcome. We report:
  - per-arm recovery rate with a Wilson 95% interval,
  - the paired rate difference with a bootstrap 95% percentile interval,
  - the exact (binomial) two-sided McNemar p-value over discordant pairs,
    plus the discordant counts (b, c) that drive it — these matter most when
    the sample is small (Exp17, n=35).

Run (no vLLM needed):
    python -m experiments.analyze_significance
"""

from __future__ import annotations

import csv
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.eval.writers import write_csv_table  # noqa: E402
from experiments.experiment_runner_common import OUT  # noqa: E402

BOOT = 10000
RNG_SEED = 0


def _load_paired(path: Path, arm_field="arm", key_field="case_id",
                 outcome_field="recovered", include_filter=None):
    """case_id -> {arm: bool recovered}. Skips rows failing include_filter."""
    paired: dict[str, dict[str, bool]] = defaultdict(dict)
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if include_filter is not None and not include_filter(row):
                continue
            paired[row[key_field]][row[arm_field]] = (
                row[outcome_field].strip().lower() == "true"
            )
    return paired


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _mcnemar_exact(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value. b, c are the discordant counts.

    Under H0, b ~ Binomial(b+c, 0.5). Two-sided exact p sums the tail at
    min(b, c) and doubles it (capped at 1.0). Robust for small n.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) * (0.5 ** n)
    return min(1.0, 2.0 * tail)


def _paired_arrays(paired, arm_a, arm_b):
    """Aligned 0/1 arrays for cases present in BOTH arms."""
    a, b = [], []
    for outcomes in paired.values():
        if arm_a in outcomes and arm_b in outcomes:
            a.append(int(outcomes[arm_a]))
            b.append(int(outcomes[arm_b]))
    return np.array(a), np.array(b)


def _bootstrap_diff_ci(a: np.ndarray, b: np.ndarray, rng) -> tuple[float, float, float]:
    """Paired bootstrap of mean(a)-mean(b). Returns (diff, lo95, hi95)."""
    n = len(a)
    diff = float(a.mean() - b.mean())
    idx = rng.integers(0, n, size=(BOOT, n))
    diffs = a[idx].mean(axis=1) - b[idx].mean(axis=1)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return diff, float(lo), float(hi)


def _compare(paired, arm_a, arm_b, rng):
    a, b = _paired_arrays(paired, arm_a, arm_b)
    n = len(a)
    ka, kb = int(a.sum()), int(b.sum())
    # discordant counts: B = a wins (1,0), C = b wins (0,1)
    disc_b = int(((a == 1) & (b == 0)).sum())
    disc_c = int(((a == 0) & (b == 1)).sum())
    diff, lo, hi = _bootstrap_diff_ci(a, b, rng)
    p = _mcnemar_exact(disc_b, disc_c)
    wa = _wilson(ka, n)
    wb = _wilson(kb, n)
    return {
        "comparison": f"{arm_a} vs {arm_b}",
        "n_paired": str(n),
        "rate_a": f"{ka/n:.4f}" if n else "0",
        "rate_a_ci": f"[{wa[0]:.3f},{wa[1]:.3f}]",
        "rate_b": f"{kb/n:.4f}" if n else "0",
        "rate_b_ci": f"[{wb[0]:.3f},{wb[1]:.3f}]",
        "diff": f"{diff:+.4f}",
        "diff_ci95": f"[{lo:+.3f},{hi:+.3f}]",
        "discordant_b_c": f"{disc_b}/{disc_c}",
        "mcnemar_p": f"{p:.4g}",
        "sig_05": "yes" if p < 0.05 else "no",
        "ci_excludes_0": "yes" if (lo > 0 or hi < 0) else "no",
    }


def main() -> None:
    rng = np.random.default_rng(RNG_SEED)
    rows = []

    # --- C4 (Exp14): cmd vs competitors, paired on case_id ---
    eff = _load_paired(OUT / "repair_efficacy_detail.csv")
    for other in ("llm_judge", "random", "no_repair"):
        rows.append({"claim": "C4", **_compare(eff, "cmd", other, rng)})

    # --- C5 (Exp15): transfer arms vs floor, paired on case_id ---
    pt = _load_paired(OUT / "prior_transfer_detail.csv")
    for a, b in (("bm25", "no_repair"), ("global", "no_repair"),
                 ("oracle", "no_repair"), ("bm25", "oracle"), ("global", "oracle")):
        rows.append({"claim": "C5", **_compare(pt, a, b, rng)})

    # --- C8 (Exp17): ECS structure arms, paired on included cases only ---
    ecs = _load_paired(
        OUT / "ecs_structure_ablation_detail.csv",
        include_filter=lambda r: r["included"].strip().lower() == "true",
    )
    for a, b in (("full_ecs", "solution"), ("full_ecs", "raw_corrected"),
                 ("solution", "cause_only"), ("full_ecs", "cause_only")):
        rows.append({"claim": "C8", **_compare(ecs, a, b, rng)})

    # --- C5/generalization (Exp13): cross-source transfer, paired on case_id ---
    # Each arm has one row per case across all 3 sources, so pairing on case_id
    # gives the ALL-level paired test (same structure as C5/Exp15).
    xds = _load_paired(OUT / "experiment_cross_dataset_detail.csv")
    for a, b in (("bm25_all", "no_repair"), ("global_all", "no_repair"),
                 ("oracle", "no_repair"), ("bm25_all", "oracle"),
                 ("global_all", "oracle"), ("global_xsource", "oracle")):
        rows.append({"claim": "C13", **_compare(xds, a, b, rng)})

    fields = ["claim", "comparison", "n_paired", "rate_a", "rate_a_ci",
              "rate_b", "rate_b_ci", "diff", "diff_ci95", "discordant_b_c",
              "mcnemar_p", "sig_05", "ci_excludes_0"]
    _print(rows, fields)
    path = write_csv_table(
        OUT / "significance_summary.csv", fields, rows, sandbox_root=OUT
    )
    print(f"\nWrote {path}")
    print(f"(paired bootstrap B={BOOT}, seed={RNG_SEED}; McNemar exact two-sided)")

    # --- C7 (Exp18): prequential stream is NOT arm-paired. Test the TREND:
    # does recovery / seed-hit rise as priors accumulate? Cochran-Armitage on
    # ordered stream bins + a halves contrast. McNemar would be a category error
    # here (consecutive rows are different cases, not matched pairs). ---
    _analyze_trend()


def _cochran_armitage(counts: list[tuple[int, int]]) -> tuple[float, float]:
    """Trend test over ordered bins. counts = [(recovered, n), ...] with bin
    score = index. Returns (z, two-sided p). Positive z = rising trend."""
    from math import erfc, sqrt
    N = sum(n for _r, n in counts)
    R = sum(r for r, _n in counts)
    if N == 0 or R == 0 or R == N:
        return 0.0, 1.0
    scores = list(range(len(counts)))
    p_bar = R / N
    t_bar = sum(s * n for s, (_r, n) in zip(scores, counts)) / N
    num = sum(s * r for s, (r, _n) in zip(scores, counts)) - R * t_bar
    var = p_bar * (1 - p_bar) * sum(
        n * (s - t_bar) ** 2 for s, (_r, n) in zip(scores, counts)
    )
    if var <= 0:
        return 0.0, 1.0
    z = num / sqrt(var)
    return z, erfc(abs(z) / sqrt(2.0))


def _two_prop_z(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float]:
    """Unpaired two-proportion z-test (halves contrast). Returns (diff, p)."""
    from math import erfc, sqrt
    if n1 == 0 or n2 == 0:
        return 0.0, 1.0
    p1, p2 = k1 / n1, k2 / n2
    p = (k1 + k2) / (n1 + n2)
    se = sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return p1 - p2, 1.0
    z = (p1 - p2) / se
    return p1 - p2, erfc(abs(z) / sqrt(2.0))


def _analyze_trend() -> None:
    """Exp18 self-evolution: trend of recovery/seed-hit over the stream."""
    path = OUT / "failure_memory_trajectory_detail.csv"
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    rows.sort(key=lambda r: int(r["case_index"]))
    n = len(rows)

    def rec(r):
        return r["recovered"].strip().lower() == "true"

    def seed(r):
        return int(r["seed_rank_recovered"]) > 0

    # 5 ordered bins of 15 (matches the summary's bins)
    bins = [rows[i:i + 15] for i in range(0, n, 15)]
    rec_counts = [(sum(rec(r) for r in b), len(b)) for b in bins]
    seed_counts = [(sum(seed(r) for r in b), len(b)) for b in bins]
    z_rec, p_rec = _cochran_armitage(rec_counts)
    z_seed, p_seed = _cochran_armitage(seed_counts)

    # first-30 vs last-45 halves contrast (the wording the summary uses)
    h1, h2 = rows[:30], rows[30:]
    d_rec, p_rec_h = _two_prop_z(
        sum(rec(r) for r in h1), len(h1), sum(rec(r) for r in h2), len(h2))
    d_seed, p_seed_h = _two_prop_z(
        sum(seed(r) for r in h1), len(h1), sum(seed(r) for r in h2), len(h2))

    print("\n=== C7 (Exp18) self-evolution trend (NOT arm-paired) ===")
    print(f"recovery   bins={[f'{r}/{nn}' for r, nn in rec_counts]}")
    print(f"           Cochran-Armitage z={z_rec:+.2f} p={p_rec:.3f}  |  "
          f"first30={sum(rec(r) for r in h1)}/30 last45={sum(rec(r) for r in h2)}/45 "
          f"diff={d_rec:+.3f} p={p_rec_h:.3f}")
    print(f"seed-hit   bins={[f'{r}/{nn}' for r, nn in seed_counts]}")
    print(f"           Cochran-Armitage z={z_seed:+.2f} p={p_seed:.3f}  |  "
          f"first30={sum(seed(r) for r in h1)}/30 last45={sum(seed(r) for r in h2)}/45 "
          f"diff={d_seed:+.3f} p={p_seed_h:.3f}")
    verdict = ("rising trend significant" if p_rec < 0.05 or p_seed < 0.05
               else "NO significant trend — accumulation does not improve recovery/seed-hit")
    print(f"verdict    {verdict}")


def _print(rows, fields) -> None:
    print("\n=== Paired significance (bootstrap 95% CI + exact McNemar) ===\n")
    hdr = ("claim", "comparison", "n", "rate_a", "rate_b", "diff",
           "diff_ci95", "b/c", "mcnemar_p", "sig")
    print(f"{hdr[0]:5s} {hdr[1]:24s} {hdr[2]:>3s} {hdr[3]:>7s} {hdr[4]:>7s} "
          f"{hdr[5]:>8s} {hdr[6]:>16s} {hdr[7]:>7s} {hdr[8]:>9s} {hdr[9]:>4s}")
    for r in rows:
        sig = "***" if r["ci_excludes_0"] == "yes" and r["sig_05"] == "yes" else (
            "ns" if r["ci_excludes_0"] == "no" else "*")
        print(f"{r['claim']:5s} {r['comparison']:24s} {r['n_paired']:>3s} "
              f"{r['rate_a']:>7s} {r['rate_b']:>7s} {r['diff']:>8s} "
              f"{r['diff_ci95']:>16s} {r['discordant_b_c']:>7s} "
              f"{r['mcnemar_p']:>9s} {sig:>4s}")
    print("\n*** = bootstrap CI excludes 0 AND McNemar p<0.05; "
          "ns = CI includes 0; b/c = discordant pairs (a-wins/b-wins)")


if __name__ == "__main__":
    main()
