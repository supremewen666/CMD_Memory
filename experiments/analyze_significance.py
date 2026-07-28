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
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.eval.writers import write_csv_table  # noqa: E402
from experiments.experiment_runner_common import OUT  # noqa: E402

BOOT = 10000
RNG_SEED = 0


def _load_paired(path: Path, arm_field="arm", key_field="case_id",
                 outcome_field="recovered", include_filter=None):
    """case_id -> {arm: bool recovered}, excluding unmeasured outcomes."""
    paired: dict[str, dict[str, bool]] = defaultdict(dict)
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("excluded", "false").strip().lower() == "true":
                continue
            if include_filter is not None and not include_filter(row):
                continue
            outcome = row.get(outcome_field, "").strip().lower()
            if outcome in {"", "nan", "na"}:
                continue
            if outcome not in {"true", "false"}:
                raise ValueError(
                    f"unexpected {outcome_field} value {row.get(outcome_field)!r} "
                    f"for case {row.get(key_field)!r}"
                )
            paired[row[key_field]][row[arm_field]] = outcome == "true"
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
    return a, b


def _percentile(values: list[float], percentile: float) -> float:
    """NumPy-compatible linear percentile for a numeric sample."""
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _bootstrap_diff_ci(
    a: list[int],
    b: list[int],
    rng: random.Random,
) -> tuple[float, float, float]:
    """Paired bootstrap of mean(a)-mean(b). Returns (diff, lo95, hi95)."""
    n = len(a)
    if n == 0:
        return 0.0, 0.0, 0.0
    paired_diffs = [left - right for left, right in zip(a, b)]
    diff = sum(paired_diffs) / n
    diffs = [
        sum(paired_diffs[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(BOOT)
    ]
    return diff, _percentile(diffs, 2.5), _percentile(diffs, 97.5)


def _compare(paired, arm_a, arm_b, rng):
    a, b = _paired_arrays(paired, arm_a, arm_b)
    n = len(a)
    ka, kb = sum(a), sum(b)
    # discordant counts: B = a wins (1,0), C = b wins (0,1)
    disc_b = sum(left == 1 and right == 0 for left, right in zip(a, b))
    disc_c = sum(left == 0 and right == 1 for left, right in zip(a, b))
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


def _load_exp25_paired(path: Path) -> dict[str, dict[str, bool]]:
    """Load Exp25's wide three-arm rows into the common paired shape."""
    if not path.is_file():
        return {}
    paired: dict[str, dict[str, bool]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("excluded", "false").strip().lower() == "true":
                continue
            outcomes: dict[str, bool] = {}
            for arm, column in (
                ("no_repair", "no_repair_recovered"),
                ("read_time", "read_time_recovered"),
                ("write_back", "write_back_recovered"),
            ):
                value = row.get(column, "").strip().lower()
                if value in {"true", "false"}:
                    outcomes[arm] = value == "true"
            if outcomes:
                paired[row["case_id"]] = outcomes
    return paired


def main() -> None:
    rng = random.Random(RNG_SEED)
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

    # --- C11 (Exp25): durable state vs repeated read-time repair / control ---
    durability = _load_exp25_paired(OUT / "repair_durability_detail.csv")
    if durability:
        for a, b in (
            ("write_back", "read_time"),
            ("write_back", "no_repair"),
            ("read_time", "no_repair"),
        ):
            rows.append({"claim": "C11", **_compare(durability, a, b, rng)})

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

    # --- Gate 2 (Exp24): the evolution verdict. Trend across generation bins,
    # first-vs-last contrast, and PAIRED comparisons against both control arms.
    # Climbing alone is not evidence: Exp22's same-budget random arm reached
    # 0.84x the oracle ceiling, so a bare climb is consistent with later cases
    # simply getting more attempts. ---
    _analyze_exp24()


def _exp24_detail_paths() -> list[Path]:
    """Every Exp24 detail CSV we can find, one per shuffle seed."""
    candidates = sorted(OUT.glob("operator_trajectory*detail*.csv"))
    runs = sorted(
        (OUT.parent / "exp_runs").glob("**/operator_trajectory*.csv")
    )
    seen: set[Path] = set()
    paths: list[Path] = []
    for path in list(candidates) + list(runs):
        if "summary" in path.name:
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        paths.append(path)
    return paths


def _exp24_rows(path: Path) -> list[dict[str, str]]:
    """Included rows only: excluded cases had a NaN identity baseline, so every
    arm's net was NaN and the case is unmeasured, not a failure."""
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            row
            for row in csv.DictReader(handle)
            if row.get("excluded", "false").strip().lower() != "true"
        ]


def _exp24_arm_flags(rows: list[dict[str, str]], column: str) -> list[bool] | None:
    """Per-case boolean outcomes for one arm, or None when the arm did not run.

    A blank cell means the arm was not executed (``--controls off``); it must
    never be read as a recorded failure, which would fabricate a beaten
    baseline.
    """
    values = [row.get(column, "").strip().lower() for row in rows]
    if not values or all(value == "" for value in values):
        return None
    if any(value == "" for value in values):
        return None
    return [value == "true" for value in values]


def _exp24_coverage(row: dict[str, str]) -> float:
    """budget/pool for the random arm; 1.0 means it could reach every shape.

    Missing or unparseable means "not recorded" -- treated as 0.0 so older CSVs
    are kept in the comparison rather than silently discarded wholesale.
    """
    raw = (row.get("random_coverage") or "").strip()
    if not raw:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _analyze_exp24() -> None:
    paths = _exp24_detail_paths()
    if not paths:
        print("\n=== Gate 2 (Exp24) ===")
        print("  SKIP: no operator_trajectory detail CSV found.")
        print("  Run experiments/run_experiment_24_operator_trajectory.py first.")
        return

    print("\n=== Gate 2 (Exp24) online operator trajectory ===")
    directions: list[str] = []
    for path in paths:
        rows = _exp24_rows(path)
        if not rows:
            print(f"\n  {path.name}: no included rows (all excluded?), skipped")
            continue
        print(f"\n  {path.name}  (n={len(rows)} included)")

        live = [row.get("recovered", "").strip().lower() == "true" for row in rows]
        bins: dict[int, list[bool]] = {}
        sizes: dict[int, list[float]] = {}
        for row, hit in zip(rows, live):
            key = int(row.get("generation_bin") or 0)
            bins.setdefault(key, []).append(hit)
            try:
                sizes.setdefault(key, []).append(
                    float(row.get("library_size_before") or 0.0)
                )
            except ValueError:
                pass
        ordered = sorted(bins)
        counts = [(sum(bins[b]), len(bins[b])) for b in ordered]
        z, p = _cochran_armitage(counts)
        print(
            "    bins       = "
            + " ".join(f"{r}/{n}" for r, n in counts)
        )
        print(f"    trend        Cochran-Armitage z={z:+.2f} p={p:.4f}")
        directions.append("up" if z > 0 else ("down" if z < 0 else "flat"))

        if len(ordered) >= 2:
            (r1, n1), (r2, n2) = counts[0], counts[-1]
            diff, p_fl = _two_prop_z(r2, n2, r1, n1)
            lo1, hi1 = _wilson(r1, n1)
            lo2, hi2 = _wilson(r2, n2)
            print(
                f"    first bin    {r1}/{n1} = {r1 / n1:.3f} "
                f"[{lo1:.3f},{hi1:.3f}]"
            )
            print(
                f"    final bin    {r2}/{n2} = {r2 / n2:.3f} "
                f"[{lo2:.3f},{hi2:.3f}]"
            )
            print(f"    first-vs-last  diff={diff:+.3f} p={p_fl:.4f}")

        # Library thickness vs recovery, per SPEC_A A2.
        if len(ordered) >= 2:
            xs = [
                sum(sizes.get(b, [0.0])) / max(1, len(sizes.get(b, [1])))
                for b in ordered
            ]
            ys = [r / n if n else 0.0 for r, n in counts]
            corr = _pearson(xs, ys)
            print(
                "    library thickness vs recovery  r="
                + ("n/a" if corr is None else f"{corr:+.3f}")
            )

        # Random-arm coverage: makes the degeneracy visible instead of only
        # correcting for it silently.
        coverages = [_exp24_coverage(row) for row in rows]
        if any(c > 0 for c in coverages):
            exhaustive = sum(1 for c in coverages if c >= 1.0)
            print(
                f"    random coverage  mean={sum(coverages) / len(coverages):.2f} "
                f"exhaustive={exhaustive}/{len(coverages)} "
                "(coverage=1.0 -> random saw the whole library)"
            )

        # Cost axis: Exp18's real finding was on cost, not accuracy.
        cost_first, cost_last = _exp24_cost_ends(rows, ordered, bins)
        if cost_first is not None and cost_last is not None:
            print(
                f"    avg rollouts  first={cost_first:.2f} "
                f"last={cost_last:.2f} delta={cost_last - cost_first:+.2f}"
            )

        # Paired arm comparisons: same cases, so McNemar -- not unpaired rates.
        for column, label in (
            ("fixed_recovered", "vs fixed-library"),
            ("random_recovered", "vs random-variation"),
        ):
            flags = _exp24_arm_flags(rows, column)
            if flags is None:
                print(f"    {label:22s} UNAVAILABLE (control arm not run)")
                continue
            pairs = list(zip(live, flags))
            note = ""
            if column == "random_recovered":
                # Drop cases where the random arm could reach the WHOLE pool.
                # There it is exhaustive-over-library, which strictly dominates
                # any ranked top-N, so it is not a same-strength control. The
                # library grows from empty, so these cluster early in the stream
                # -- leaving them in makes the control's discriminating power
                # rise along the stream and fakes a "live pulls ahead" trend.
                keep = [
                    i
                    for i, row in enumerate(rows)
                    if _exp24_coverage(row) < 1.0
                ]
                dropped = len(rows) - len(keep)
                if not keep:
                    print(
                        f"    {label:22s} DEGENERATE (all {len(rows)} cases had "
                        "coverage=1.0: random exhausted the library)"
                    )
                    continue
                pairs = [pairs[i] for i in keep]
                if dropped:
                    note = f"  [{dropped} degenerate case(s) dropped]"
            b = sum(1 for x, y in pairs if x and not y)
            c = sum(1 for x, y in pairs if y and not x)
            p_arm = _mcnemar_exact(b, c)
            verdict = "live wins" if b > c and p_arm < 0.05 else "not significant"
            print(
                f"    {label:22s} live-only={b} control-only={c} "
                f"p={p_arm:.4f}  {verdict}{note}"
            )

        _exp24_ordering_cost(rows)

    _exp24_verdict(directions, len(paths))


def _exp24_ordering_cost(rows: list[dict[str, str]]) -> None:
    """random-ORDER control: same candidate set, shuffled -> a COST comparison.

    Both arms walk the same retrieved set and stop at the first shape clearing
    the threshold, so whether a case recovers is IDENTICAL by construction. Only
    the rank at which it is found can differ, which is why this arm tests the
    track-record ORDERING rather than the recovery rate. Reporting it as a
    recovery win would be reporting a tautology.

    Unlike random-variation, it is immune to pool-coverage degeneracy: the
    candidate set is the live arm's own.
    """
    paired = [
        (int(row["library_rank"]), int(row["random_order_rank"]))
        for row in rows
        if (row.get("library_rank") or "").strip().isdigit()
        and (row.get("random_order_rank") or "").strip().isdigit()
        and int(row["library_rank"]) > 0
        and int(row["random_order_rank"]) > 0
    ]
    if not paired:
        print("    vs random-order        UNAVAILABLE (control arm not run)")
        return

    # Sanity: the rate tie is structural. A mismatch means the arms diverged.
    mismatched = sum(
        1
        for row in rows
        if (row.get("random_order_recovered") or "").strip()
        and (row.get("random_order_recovered") == "true")
        != (row.get("recovery_source") == "library")
    )

    live_cheaper = sum(1 for live, order in paired if live < order)
    order_cheaper = sum(1 for live, order in paired if order < live)
    p_cost = _mcnemar_exact(live_cheaper, order_cheaper)
    mean_live = sum(live for live, _ in paired) / len(paired)
    mean_order = sum(order for _, order in paired) / len(paired)
    verdict = (
        "ranking helps"
        if live_cheaper > order_cheaper and p_cost < 0.05
        else "no ranking benefit"
    )
    print(
        f"    vs random-order        COST axis (rate ties by construction): "
        f"mean rank {mean_live:.2f} vs {mean_order:.2f}"
    )
    print(
        f"    {'':22s} live-earlier={live_cheaper} "
        f"order-earlier={order_cheaper} sign-test p={p_cost:.4f}  {verdict}"
    )
    if mismatched:
        print(
            f"    {'':22s} WARNING: {mismatched} case(s) where the rate tie "
            "broke -- the arms should walk the same candidate set"
        )


def _exp24_cost_ends(
    rows: list[dict[str, str]],
    ordered: list[int],
    bins: dict[int, list[bool]],
) -> tuple[float | None, float | None]:
    """Mean total_rollouts in the first and last generation bin."""
    if len(ordered) < 2:
        return None, None
    by_bin: dict[int, list[float]] = {}
    for row in rows:
        try:
            by_bin.setdefault(int(row.get("generation_bin") or 0), []).append(
                float(row.get("total_rollouts") or 0.0)
            )
        except ValueError:
            continue
    first, last = by_bin.get(ordered[0]), by_bin.get(ordered[-1])
    if not first or not last:
        return None, None
    return sum(first) / len(first), sum(last) / len(last)


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    centered_x = [value - mean_x for value in xs]
    centered_y = [value - mean_y for value in ys]
    sum_sq_x = sum(value * value for value in centered_x)
    sum_sq_y = sum(value * value for value in centered_y)
    if sum_sq_x == 0 or sum_sq_y == 0:
        return None
    covariance = sum(
        left * right for left, right in zip(centered_x, centered_y)
    )
    return covariance / math.sqrt(sum_sq_x * sum_sq_y)


def _exp24_verdict(directions: list[str], seed_count: int) -> None:
    print("\n  --- Gate 2 verdict rule ---")
    print(
        "  PASS requires: rising trend (p<0.05) AND final bin significantly "
        "above first AND live arm beats BOTH controls (paired McNemar)."
    )
    print(
        "  Flat recovery with FALLING rollouts is not a null result -- that is "
        "the 'warm-up reuse' finding; report it on the cost axis."
    )
    if seed_count < 3:
        print(
            f"  NOT SETTLED: {seed_count} seed(s) present, >=3 required "
            "(37% churn). Run more --seed values before deciding."
        )
    elif len(set(directions)) == 1:
        print(f"  All {seed_count} seeds agree in direction: {directions[0]}.")
    else:
        print(
            f"  Seeds DISAGREE in direction ({', '.join(directions)}); "
            "the trend conclusion does not hold."
        )


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
    all_rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    rows = [
        row for row in all_rows
        if row.get("excluded", "false") != "true"
    ]
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

    # Denominators come from the actual split sizes. They were once hardcoded
    # as /30 and /45 from the 75-case era; at 600 rows that printed impossible
    # ratios like "461/45" while the p-values (which read len(h1)/len(h2))
    # stayed correct.
    print("\n=== C7 (Exp18) self-evolution trend (NOT arm-paired) ===")
    print(f"recovery   bins={[f'{r}/{nn}' for r, nn in rec_counts]}")
    print(f"           Cochran-Armitage z={z_rec:+.2f} p={p_rec:.3f}  |  "
          f"first={sum(rec(r) for r in h1)}/{len(h1)} "
          f"last={sum(rec(r) for r in h2)}/{len(h2)} "
          f"diff={d_rec:+.3f} p={p_rec_h:.3f}")
    print(f"seed-hit   bins={[f'{r}/{nn}' for r, nn in seed_counts]}")
    print(f"           Cochran-Armitage z={z_seed:+.2f} p={p_seed:.3f}  |  "
          f"first={sum(seed(r) for r in h1)}/{len(h1)} "
          f"last={sum(seed(r) for r in h2)}/{len(h2)} "
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
