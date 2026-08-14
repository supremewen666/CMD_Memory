# Experiment Analysis Round 2 — Gate Reconstruction

## Evidence boundary

This supplementary analysis uses the same completed local artifacts from Round 1.
It performs no model, API, network, training, tuning, or router update. It remains
a **shadow-gold development screen**, not sealed/live and not an end-to-end
gold-free claim.

## Supplementary method

For each seed, utilities were grouped by represented or unseen family. Case-level
utilities were averaged within family. For the multiseed reconstruction, each
family's effect was first averaged across seeds, then the 640 represented or 156
unseen families were sampled with replacement 10,000 times using local
`random.Random(24)`. The represented test uses a one-sided 95% lower bound; the
unseen non-inferiority test uses a two-sided 95% lower bound. This analysis changes
no core algorithm and does not overwrite the registered reports.

## Represented-family primary gate

| Comparator | GHOST − comparator family-macro utility | One-sided 95% lower bound | Strictly positive | Gate |
|---|---:|---:|---:|---|
| Full V4 | +0.0459260825 | +0.0376301546 | yes | PASS |
| Diagonal Thompson | +0.0123915806 | +0.0080225958 | yes | PASS |
| Online linear SGD | +0.0118651344 | +0.0068532245 | yes | PASS |

Online linear SGD is the strongest of the three named comparators by represented
family-macro utility in this replay. GHOST remains strictly higher, and its
aggregate family-bootstrap lower bound is positive.

Per-seed estimates are positive against all three comparators. One exploratory
seed-26 lower bound against online linear SGD is `-0.0015193494`; the frozen build
spec applies the gate on confirmatory aggregation rather than requiring every seed
lower bound to be positive. This is recorded to avoid overstating seed-wise
uniformity.

## Unseen-family non-inferiority

- Full V4 unseen family-macro utility: `0.2032501781`.
- GHOST unseen family-macro utility: `0.2136713140`.
- GHOST − Full V4: `+0.0104211360`.
- Two-sided 95% lower bound: `+0.0029024137`.
- Registered non-inferiority threshold: `-0.005`.
- Result: `+0.0029024137 >= -0.005`, **PASS**.

The worst seed-specific supplementary two-sided lower bound was
`-0.0002369681` (seed 26), which also remains above `-0.005`.

## Safety gate

| Metric | GHOST | Full V4 | Registered condition | Gate |
|---|---:|---:|---|---|
| Harm rate | 0.1619354839 | 0.2135483871 | GHOST ≤ Full | PASS |
| CVaR 0.05 | -0.7276707396 | -0.7396455848 | GHOST ≥ Full − 0.01 (`-0.7496455848`) | PASS |

GHOST is better than Full V4 on both observed safety statistics in this accessed
stream. This is a development-screen result; it is not a prospective safety
guarantee.

## Ablation interpretation after supplementary checks

- `ghost_no_typed_motif` falls from `0.2350165790` to `0.2227517775` mean
  utility, consistent with useful typed-motif information.
- `ghost_shuffled_feedback` falls to `0.1215968143`, its represented family-macro
  delta versus Full becomes `-0.0174701005`, and harm rises to `0.2481935484`.
  Correct feedback chronology is therefore strongly associated with performance.
- The flat global and no-signal arms are slightly higher than the recursive arm on
  raw overall mean (`0.2357088692` and `0.2356452636` respectively). The present
  artifacts do not justify claiming that hierarchy itself beats every ablation.
- The primary frozen recursive GHOST arm nevertheless passes every build-screen
  comparator, unseen non-inferiority, harm, and CVaR gate.

## Consistency results

- Five reports: canonical embedded report hashes all matched recomputation.
- One multiseed summary: canonical embedded summary hash matched recomputation.
- Each seed: `model_calls=0`, `case_count=3100`, `bootstrap_samples=10000`.
- Each seed: 49,600 rows, 16 arms, 3,100 unique cases per arm.
- Frozen stream binding: canonical case stream
  `51d18796abfb8f1eeaecbbe2d621854a6755ee018132f16364d1b9b9a31ce8aa`.
- Frozen reference file binding:
  `bb2a0acec091fa3acce39f549da009d5b82d3e06fc770ada905a1f7035d0dd29`.
- Multiseed canonical embedded summary:
  `aa119b3c812dea18e19571b9869afb2ee5ad3a1ed8e88f5c29cf34980429c0f8`.
- Actual multiseed summary file SHA-256:
  `20d457fb1b75f236edbe52b5035836eae5d25f8d440bfe9edba2d0dde7a87185`.

## Exact execution and verification commands

The following replay command was run once for each `SEED` in `24 25 26 27 28`,
with the corresponding literal seed substituted in both places:

```bash
python -m experiments.baselines.v4_zero_call_replay \
  --cases artifacts/neuro_symbolic_evolution_v4/neuro_symbolic_evolution_v4/runs/v7-001/cases.merged.jsonl \
  --reference-outcomes artifacts/neuro_symbolic_evolution_v4/neuro_symbolic_evolution_v4/runs/v7-001/prequential/arm_outcomes.jsonl \
  --output-dir artifacts/neuro_symbolic_evolution_v4/replays/ghost-router-v1-reviewed-seedSEED \
  --seed SEED \
  --bootstrap-samples 10000
```

```bash
python -m experiments.baselines.summarize_v4_zero_call \
  --reports \
    artifacts/neuro_symbolic_evolution_v4/replays/ghost-router-v1-reviewed-seed24/report.json \
    artifacts/neuro_symbolic_evolution_v4/replays/ghost-router-v1-reviewed-seed25/report.json \
    artifacts/neuro_symbolic_evolution_v4/replays/ghost-router-v1-reviewed-seed26/report.json \
    artifacts/neuro_symbolic_evolution_v4/replays/ghost-router-v1-reviewed-seed27/report.json \
    artifacts/neuro_symbolic_evolution_v4/replays/ghost-router-v1-reviewed-seed28/report.json \
  --output artifacts/neuro_symbolic_evolution_v4/replays/ghost-router-v1-reviewed-multiseed-summary.json

python -m pytest tests/repair/test_ghost_router.py \
  tests/experiments/test_ghost_router.py \
  tests/experiments/test_v4_zero_call_replay.py -q

python -m pytest tests/repair tests/counterfactual tests/experiments -q

ruff check cmd_audit/repair/ghost_router.py \
  cmd_audit/repair/evolution_repository.py \
  experiments/baselines/v4_zero_call_replay.py \
  experiments/baselines/summarize_v4_zero_call.py \
  tests/repair/test_ghost_router.py \
  tests/experiments/test_ghost_router.py \
  tests/experiments/test_v4_zero_call_replay.py

git diff --check
```

Verification results: focused tests `22 passed`; broad tests `859 passed, 1
skipped, 1 warning, 58,232 subtests passed`; ruff `All checks passed!`; diff
check passed. The warning is the pre-existing `deposit_best` deprecation warning.

## Conclusion and next boundary

The final reviewed GHOST Router V1 implementation passes the requested zero-call
shadow-gold development screen on the same 3,100-case stream. The next experiment
must not treat this as sealed evidence. A claim-bearing run still requires a new
immutable, leakage-audited evaluation split and deployment-observable feedback;
real model calls are neither used nor authorized by this report.
