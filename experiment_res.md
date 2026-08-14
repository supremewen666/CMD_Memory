# Experiment Results — Counterfactual Memory Evolution Governance

## Prospective live readiness gate (2026-08-14)

Decision: **IMPLEMENTATION PASS; DATA/OUTCOME COLLECTION PENDING.**

The model-calling path now has a four-way fresh-data freeze, independent-source
attestation, code/config/model/evaluator/budget hashing, single-`RUN_ID` access
authorization, `nohup`-compatible detached supervision, JSONL progress and selection
ledgers, and upstream model-call accounting. In prospective mode, GHOST receives no
development proxy or shadow-gold update. Delayed feedback is accepted only after its
registered window matures and must bind the emitted selection, intent, skill, probe,
effect, and pre-action prior.

No model-calling result is reported here: new `ghost_test_rep` / `ghost_test_new`
cases, independent-source attestation, and actual four-role model snapshot hashes have
not been supplied. The command sequence is frozen in `GHOST_LIVE_EXPERIMENT.md` and
will fail closed rather than reuse the old 3,100-case dev/cal stream.

## Latest gate — GHOST Ecology V3 industry-aligned optimization (2026-08-14)

Decision: **development-proxy zero-call PASS; sealed/live experiment still gated.**

V3 removes post-action telemetry from selection, replaces the zero residual with
`mature delayed utility proxy - pre-action evaluator prior`, adds the same-feedback
`full_v4_observable` baseline, and changes the ecology from 1,058 fragmented dev
species (662 singletons) to 12 preregistered parameterized repair types with
support-gated global→pattern→local residual shrinkage.

The pre-action evaluator passes identifiability: family correlation `0.6393220035`,
bootstrap LB `0.5290066352`, pairwise concordance `0.7056096430`.

Five zero-call seeds (24--28), each 3,100 cases × 8 arms and 10,000 bootstrap
samples, all pass:

- GHOST vs same-feedback Full V4: mean family delta `+0.0872389171`, worst-seed
  one-sided 95% LB `+0.0542759323`;
- GHOST vs shadow-gold Full V4 oracle: mean family delta `+0.0357592521`,
  worst-seed LB `+0.0089756501` (context only, not the decision gate);
- 2,518/2,518 dev residuals were nonzero in every seed;
- both learning arms made zero updates on all 582 `ghost_cal` cases;
- model/API/network calls: 0.

Primary evidence:
`artifacts/neuro_symbolic_evolution_v4/ghost_ecology_v3/industry-optimized-multiseed-summary.json`.
Embedded summary hash:
`050f4696f9c86e3410cc70e4b21f90bc3f4340d014e257997e9b3e0d73efc057`;
file SHA-256:
`cfa0e1521bbd2dde6c1878fd4de0b70a5b7c33df0e8db3236a6a6017818b950e`.

This is a development-proxy replay over previously materialized future outcomes.
It is not real delayed deployment feedback and does not replace the still-empty
`ghost_test_rep` / `ghost_test_new` sealed splits.

## Latest gate — GHOST Ecology V2 deployment feedback (2026-08-13)

Final decision: **feedback identifiability PASS; end-to-end GHOST routing BLOCKED.**

The original immediate-probe result below remains a valid negative ablation: action
typing alone was insufficient. A frozen parameterized evaluator was then fitted on
`ghost_dev` only and evaluated on family-disjoint `ghost_cal` without calibration
updates. Identifiability passed with family correlation `0.6374268463`, bootstrap
lower bound `0.5272669761`, and pairwise concordance `0.7032916087`.

The final fair seven-arm replay restricts every online arm to `ghost_dev` updates.
On evaluation-only `ghost_cal`, GHOST beat global policy (`+0.0628574068`, lower
bound `+0.0327165307`) but did not beat Full V4 (`-0.0072636236`, lower bound
`-0.0309097982`). All 582 calibration cases left the GHOST posterior unchanged.
The runner now registers
`ghost_hierarchy_v1`, binds the frozen evaluator hash, requires the GHOST protocol,
and updates only `ghost_dev`.

This does **not** authorize the model-calling experiment. The evaluator repair is
accepted, but the integrated router remains blocked by the Full V4 confirmatory
gate. In addition, `ghost_test_rep` and `ghost_test_new` remain empty.

Repaired artifacts:
`artifacts/neuro_symbolic_evolution_v4/ghost_ecology_v2/evaluator-identifiability-zero-call-20260813-seed24.json`
and
`artifacts/neuro_symbolic_evolution_v4/ghost_ecology_v2/prequential-zero-call-residual-final-seed24/report.json`.

### Superseded immediate-probe gate

Decision at this stage was **BLOCKED_FEEDBACK_NOT_IDENTIFIABLE**.

The skill-conditioned zero-call audit used 3,100 cases, 796 families, 12,400
candidate observations and 10,000 family-bootstrap draws. It made zero model/API
calls. Pairwise concordance passed (`0.6249671485 >= 0.55`), showing that typed
per-skill probes improve within-case action ranking. Family correlation failed
(`0.0036162454 < 0.20`) and its one-sided 95% bootstrap lower bound failed
(`-0.0662486454 < 0.10`).

The source telemetry is degenerate for repair-success identification: all candidates
are valid, none roll back, verify/abstain always make zero changes, and target-mutation
probes always report immediate success. No delayed recurrence, target resolution,
anchor non-regression, or annotation-consumption signal is present. The earlier
shadow-gold router PASS therefore remains only a development screen.

Primary artifact:
`artifacts/neuro_symbolic_evolution_v4/ghost_ecology_v2/identifiability-zero-call-20260813.json`.
Full analysis: `experiment_analysis/analysis_3.md`.

## GHOST Router V1 final reviewed zero-call replay (2026-08-13)

### Outcome

The final review-fixed GHOST Router V1 implementation completed a fresh five-seed
zero-model-call replay under new, non-overwriting
`ghost-router-v1-reviewed-*` artifact names. The primary recursive GHOST arm passes
the requested **development-screen** comparisons. This is previously materialized
shadow-gold selected-action feedback, so it is explicitly **not** a sealed/live or
end-to-end gold-free claim.

```text
[RESULT] claim_scope=selector_screen_not_end_to_end_gold_free
[RESULT] cases=3100
[RESULT] represented_families=640
[RESULT] unseen_families=156
[RESULT] seeds=24,25,26,27,28
[RESULT] bootstrap_samples_per_seed=10000
[RESULT] registered_arms=16
[RESULT] rows_per_seed=49600
[RESULT] model_calls=0
[RESULT] ghost_mean_utility=0.235016578990796
[RESULT] full_v4_mean_utility=0.18455403238812873
[RESULT] ghost_represented_family_macro=0.25735190307647327
[RESULT] full_v4_represented_family_macro=0.21142582055322517
[RESULT] ghost_unseen_family_macro=0.21367131403410727
[RESULT] full_v4_unseen_family_macro=0.20325017805920298
[RESULT] ghost_harm_rate=0.16193548387096773
[RESULT] full_v4_harm_rate=0.2135483870967742
[RESULT] ghost_cvar_05=-0.7276707395590808
[RESULT] full_v4_cvar_05=-0.7396455848317953
[RESULT] summary_sha256=aa119b3c812dea18e19571b9869afb2ee5ad3a1ed8e88f5c29cf34980429c0f8
[RESULT] summary_file_sha256=20d457fb1b75f236edbe52b5035836eae5d25f8d440bfe9edba2d0dde7a87185
```

### Primary and safety gates

Family-macro utilities were reconstructed from the raw rows with families as the
experimental unit and 10,000 local bootstrap draws over the five-seed aggregate.

| Gate | Estimate | Lower bound / threshold | Result |
|---|---:|---:|---|
| GHOST − Full V4, represented | +0.0459260825 | one-sided 95% LB +0.0376301546 | PASS |
| GHOST − diagonal Thompson, represented | +0.0123915806 | one-sided 95% LB +0.0080225958 | PASS |
| GHOST − online linear SGD, represented | +0.0118651344 | one-sided 95% LB +0.0068532245 | PASS |
| GHOST − Full V4, unseen non-inferiority | +0.0104211360 | two-sided 95% LB +0.0029024137 ≥ -0.005 | PASS |
| Harm | 0.1619354839 vs 0.2135483871 | GHOST ≤ Full | PASS |
| CVaR 0.05 | -0.7276707396 vs -0.7396455848 | GHOST ≥ Full - 0.01 | PASS |

The strongest named learned comparator on represented family-macro utility is
online linear SGD; GHOST exceeds it on the five-seed aggregation. The raw overall
GHOST mean is 27.3449% above Full V4.

### Ablations

| Arm | Mean utility | Represented family-macro Δ vs Full | Unseen mean | Harm |
|---|---:|---:|---:|---:|
| GHOST recursive / `ghost_hierarchy_v1` | 0.2350165790 | +0.0459260825 | 0.1944057665 | 0.1619354839 |
| `ghost_global_v1` | 0.2357088692 | +0.0445981047 | 0.1946168937 | 0.1578709677 |
| `ghost_no_semantic_level` | 0.2345246272 | +0.0452438828 | 0.1943783896 | 0.1621290323 |
| `ghost_no_signal_level` | 0.2356452636 | +0.0447203226 | 0.1944187793 | 0.1578064516 |
| `ghost_no_typed_motif` | 0.2227517775 | +0.0393408476 | 0.1806205043 | 0.1704516129 |
| `ghost_shuffled_feedback` | 0.1215968143 | -0.0174701005 | 0.0728207676 | 0.2481935484 |

All other registered controls were retained. Typed-motif removal and shuffled
feedback are clearly adverse, while global and no-signal variants are slightly
higher on raw mean. Therefore this replay supports the frozen recursive arm's gate
pass, but does not claim that every hierarchy component beats every ablation.

### Provenance and integrity

- Closed input case canonical hash:
  `51d18796abfb8f1eeaecbbe2d621854a6755ee018132f16364d1b9b9a31ce8aa`.
- Reference outcome file SHA-256:
  `bb2a0acec091fa3acce39f549da009d5b82d3e06fc770ada905a1f7035d0dd29`.
- Embedded multiseed summary hash:
  `aa119b3c812dea18e19571b9869afb2ee5ad3a1ed8e88f5c29cf34980429c0f8`;
  independent canonical recomputation matched.
- Actual summary file SHA-256:
  `20d457fb1b75f236edbe52b5035836eae5d25f8d440bfe9edba2d0dde7a87185`.
- All five embedded report hashes matched independent canonical recomputation.
- Every seed has exactly 3,100 rows for every one of the 16 arms and records
  `model_calls=0`.

Full commands, per-file hashes, and the two Novix-style analysis rounds are in
`experiment_analysis/analysis_1.md` and
`experiment_analysis/analysis_2.md`. The next claim-bearing step still requires
an untouched frozen split plus deployment-observable feedback.

Verification after the replay: focused GHOST/replay tests `22 passed`; broader
repair/counterfactual/experiment tests `859 passed, 1 skipped, 1 warning, 58,232
subtests passed`; ruff and `git diff --check` both passed. No core algorithm file
was changed during this experiment run and no commit was created.

The older governance report retained below is a separate historical experiment.
Its statement that no `judge_v*.md` existed described that earlier run; the GHOST
replay above independently verified the now-present `iterations/judge_v1.md` PASS.

## Executive verdict

The completed confirmatory experiment is a **negative result**. Phase 0 found enough
offline selector evidence to authorize Phase 1, but the live, budget-aligned Phase 1
comparison did not show a statistically significant endpoint benefit from evolution
in either arena. The frozen decision in
`artifacts/evolution_governance/phase1/combined/phase1_combined_summary.json` is
`negative_result_chapter`.

This verdict supersedes the older C7/C9 narrative in `EXPERIMENT.md` whenever the
claim is about **online evolution**. In particular, the static LOO operator-transfer
result in Exp22 is not evidence that enabling online evolution improves the
confirmatory endpoint.

```text
[RESULT] final_decision=negative_result_chapter
[RESULT] phase0_selector_significant_runs=2/4
[RESULT] phase0_reproducible_d1_pairs=0
[RESULT] phase1_memtrace_endpoint_contrast=4.135379531967027
[RESULT] phase1_memtrace_family_blocked_permutation_p=0.3607
[RESULT] phase1_stale_endpoint_contrast=-34.373306560848896
[RESULT] phase1_stale_family_blocked_permutation_p=1.0
[RESULT] phase1_g_e2_passed=1
[RESULT] phase1_g_e3_passed=0
```

## Evidence boundary

- Primary confirmatory evidence: Phase 1 `evolution_on` versus `all_frozen`,
  same case stream, seed 24, candidate budget 2, with family-blocked inference.
- Authorization evidence: the stored Phase 0 selector evaluation over four
  MemTrace runs.
- Context-only evidence: the six-arena unified analysis is explicitly
  `descriptive_observational`, and its manifest records
  `hypothesis_test_role=descriptive_not_confirmatory`. Paired tests are now
  computed, so the evidence tier rests on that role field and on the
  observational design — arms were not randomized over a pre-registered case
  stream — rather than on the absence of p-values. It is not used to confirm a
  treatment effect.
- CMD has no gradient-training loop. “Evolution” is online state/selector
  updating, so no loss curve, epoch count, or train/validation gap is invented.
- The repository contains no `iterations/judge_v*.md`; therefore the
  `/research-experiment` prerequisite “latest review verdict=PASS” could not be
  independently verified.

## Data and run integrity

### Unified observational arena

The manifest at `artifacts/arena/analysis_full/analysis_manifest.json` records:

| Check | Observed |
|---|---:|
| Arenas | 6 |
| Case observations | 10,080 |
| Arm-comparison events | 9,735 |
| Chain attempts | 176,496 |
| Ecology snapshots | 24 |
| Deposition events | 4 |
| Perturbation events | 0 |
| Hypothesis tests | not run |
| Analysis kind | `descriptive_observational` |

The four chain-enabled MemTrace JSONL files contain exactly 176,496
`chain_attempt` records in aggregate. MemFail and STALE contain no chain attempts,
as reflected by their manifests. Every source manifest records
`runtime_uses_gold=false`; runtime selection uses the reference-free signal and
shadow gold is outcome-only.

### Phase 0

`artifacts/evolution_governance/phase0/phase0_summary.json` records four inputs,
zero model calls, the stored selector evaluation, and the frozen gate:

```text
selector p<0.05 in >=2/4 runs OR D1 pair reproduced in >=3/4
```

The stored outcome is 2/4 selector runs significant, no reproducible D1 pair,
and Phase 1 authorized. This report analyzes those completed outputs as given;
it does not substitute a new run for the recorded experiment.

### Phase 1

For both arenas, direct JSONL checks confirmed that the two arms have identical
ordered `top_p_saturation_event` case streams. Each arm has the expected case
count, the candidate budget is 2, and the dataset fingerprint, seed, answer judge,
selection judge, and `runtime_uses_gold=false` settings agree within arena.

The stored combined summary links the two source summaries and records the final
frozen decision. All endpoint gains and logical costs below are taken from these
completed artifacts.

## Main results

| Arena | Cases / families | Evolution endpoint | Frozen endpoint | Contrast | Family 95% lower bound | Permutation p | G_E3 |
|---|---:|---:|---:|---:|---:|---:|---|
| MemTrace | 2,047 / 182 | 53.7867 | 49.6513 | +4.1354 | -0.1022 | 0.3607 | fail |
| STALE | 1,200 / 400 | 366.7691 | 401.1424 | -34.3733 | -0.1225 | 1.0000 | fail |

Source:
`artifacts/evolution_governance/phase1/combined/phase1_combined_arena_summary.csv`
and
`artifacts/evolution_governance/phase1/combined/phase1_combined_summary.json`.

Interpretation:

- MemTrace has a positive raw endpoint contrast, but it is not distinguishable
  from zero under the family-blocked randomization test.
- STALE moves in the harmful direction on the endpoint.
- Both lower bounds cross zero and both arenas fail `G_E3`, whose frozen rule is
  `endpoint_contrast > 0 and p < 0.05`.
- `G_E2` passes in both arenas because deposition confirmation calls are 0,
  below the budget cap of 50. Passing a budget guardrail is not evidence of
  efficacy.

## Ablations and controls

### Phase 0 selector and D1 deposition control

| Run | Endpoint contrast | AULC contrast | Family-blocked p | Selector gate |
|---|---:|---:|---:|---|
| memtrace_seed24 | +0.7499 | +14.0194 | 0.4897 | fail |
| memtrace_seed124 | +39.4864 | +24.1024 | 0.0499 | pass |
| memtrace_seed224 | +57.3702 | +27.3950 | 0.0004 | pass |
| memtrace_llama | +16.8211 | +15.2303 | 0.2560 | fail |

Stored source: `artifacts/evolution_governance/phase0/selector_replay.csv`.

No D1 ordered skill pair passed in any run, no pair reproduced in at least 3/4
runs, and every one of the 144 calibration cells
(`n_min` 4 values × confirmation `K` 3 × dominance threshold 3 × runs 4)
had `passed_pair_count=0`.

Sources:
`artifacts/evolution_governance/phase0/d1_candidates.csv`,
`artifacts/evolution_governance/phase0/threshold_calibration.csv`, and
`artifacts/evolution_governance/phase0/phase0_summary.json`.

### Phase 1 equal-budget arm comparison

| Arena | Metric | Evolution on | All frozen | Descriptive delta |
|---|---|---:|---:|---:|
| MemTrace | endpoint shadow gain | 53.7867 | 49.6513 | +4.1354 |
| MemTrace | first-try oracle top-1 rate | 0.6610 | 0.6810 | -0.0200 |
| MemTrace | logical cost / case | 3.8915 | 3.9375 | -0.0459 |
| STALE | endpoint shadow gain | 366.7691 | 401.1424 | -34.3733 |
| STALE | first-try oracle top-1 rate | 0.5458 | 0.4858 | +0.0600 |
| STALE | logical cost / case | 3.1125 | 3.6567 | -0.5442 |

Budget alignment is 1.0 in both arenas. First-try rate and cost were not assigned
their own inferential gates in the frozen protocol, so their deltas are
descriptive, not confirmatory wins.

## Statistical specification

- Experimental unit for inference: family, not individual case.
- Endpoint: cumulative `shadow_selected_cumulative_gain` difference between
  `evolution_on` and `all_frozen`.
- Null construction: family-level sign-flip permutation with 9,999 draws and a
  local seeded RNG.
- Reported p-value: upper-tail permutation probability produced by
  `cmd_audit.eval.evolution_gates.permutation_p_value`.
- Interval: bootstrap lower bound over family effects with 2,000 samples and
  95% confidence as implemented by `_bootstrap_lower_bound`.
- Missing/nonfinite selected shadow gains are converted to zero by the Phase 1
  analyzer.
- Multiplicity: no cross-arena multiplicity correction is applied. This does
  not alter the verdict because neither arena is individually significant.
- Phase 0 is gate calibration/authorization evidence; Phase 1 is the
  confirmatory treatment comparison.

Implementation:
`experiments/run_evolution_governance_phase0.py`,
`experiments/run_evolution_governance_phase1.py`,
`cmd_audit/eval/evolution_gates.py`, and
`cmd_audit/repair/governance.py`.

## Claim-by-claim verdict

| Claim | Verdict | Evidence-bounded interpretation |
|---|---|---|
| Runtime selection is gold-free and shadow gold is outcome-only | **SUPPORTED** | Manifests record `runtime_uses_gold=false`; Phase 0 serializes runtime and outcome signals separately. This is a protocol/property claim. |
| Offline family-conditioned selector shows a repeatable signal sufficient to proceed | **SUPPORTED** | 2/4 recorded runs pass the frozen Phase 0 alternative. This supports authorization only. |
| A D1 chain pair is reproducibly depositable | **NOT SUPPORTED** | No pair passes any run or reaches 3/4 replication; all threshold calibration cells yield zero passing pairs. |
| Online evolution improves endpoint gain over a frozen selector on MemTrace | **NOT SUPPORTED** | Contrast +4.1354, but `p=0.3607`; G_E3 fails. |
| Online evolution improves endpoint gain over a frozen selector on STALE | **NOT SUPPORTED** | Contrast -34.3733, `p=1.0`; G_E3 fails. |
| Online evolution is broadly beneficial across arenas | **NOT SUPPORTED** | Both arenas would need to pass G_E3; neither does, and STALE is negative. |
| Evolution reduces logical cost per case | **EXPLORATORY** | Cost is lower in both arenas, but no frozen inferential test or cost gate supports a confirmatory claim. |
| Evolution improves first-try oracle selection | **NOT SUPPORTED** | Direction differs: -2.00 points on MemTrace, +6.00 points on STALE; no inferential test was frozen. |
| Unified-arena ecology, niche, chain, and CMD-vs-best-of-N patterns are treatment effects | **EXPLORATORY** | The analysis manifest explicitly labels them descriptive and says no hypothesis tests were run. |
| Older C7 “越用越准” self-improvement narrative | **NOT SUPPORTED** | Earlier trajectory evidence already found warm-up reuse without sustained recovery improvement; Phase 1 does not rescue the online-evolution claim. |
| Older Exp22/C9 static operator-transfer “GO” proves online evolution | **EXPLORATORY** | It supports a static LOO transfer mechanism within its older protocol, not the Phase 1 online causal claim. |

## Negative-result explanation

The negative result is not a pipeline failure:

1. Phase 1 arms share the case stream and budgets, and all endpoint/cost totals
   reproduce from raw JSONLs.
2. The selector can alter behavior: first-try and chain-attempt patterns differ
   between arms.
3. Those changes do not translate into a stable endpoint improvement.
   MemTrace's small positive contrast is noisy across families; STALE's large
   negative contrast shows domain dependence and possible harmful adaptation.
4. D1 deposition does not explain the outcome: there were zero confirmation
   calls, and Phase 0 found no reproducible depositable pair.
5. Lower cost is compatible with attempting fewer chains, but cheaper execution
   is not equivalent to better repair quality.

The defensible paper result is therefore: **gold-free online adaptation changed
selection/cost behavior but failed to improve the family-blocked endpoint under
the frozen two-arena protocol, with a harmful endpoint direction on STALE.**

## Threats and limitations

- The missing `iterations/judge_v*.md` prevents verification of the required
  pre-experiment review PASS.
- Phase 1 uses one seed (24) per arena. Family blocking addresses within-run
  dependence, not run-to-run model-stack variability.
- The two arenas differ in dataset and failure composition; no claim of universal
  transportability is justified.
- The evaluation and selection judges are fixed local endpoint/model identities;
  model/version replication is not present in Phase 1.
- The endpoint uses shadow-gold scoring for outcome evaluation. Runtime remains
  gold-free, but judge validity and calibration remain measurement assumptions.
- First-try and cost metrics lack frozen inferential tests.
- No perturbation treatment was run in the six-arena analysis
  (`perturbation_events=0`).
- Older sandbox experiments use different scales, runners, and sometimes older
  datasets. They must not be pooled numerically with Phase 1.

## Artifact index

| Evidence | Artifact |
|---|---|
| Six-arena scope and evidence label | `artifacts/arena/analysis_full/analysis_manifest.json` |
| Descriptive signal slices | `artifacts/arena/analysis_full/signal_by_failure.csv` |
| Descriptive CMD vs best-of-N | `artifacts/arena/analysis_full/cmd_vs_best_of_n_by_budget.csv` |
| Chain-attempt totals | `artifacts/arena/analysis_full/chain_benefit_spectrum.csv` |
| Depositions | `artifacts/arena/analysis_full/depositions.csv` |
| Phase 0 decision and protocol metadata | `artifacts/evolution_governance/phase0/phase0_summary.json` |
| Phase 0 per-run selector statistics | `artifacts/evolution_governance/phase0/selector_replay.csv` |
| Phase 0 D1 candidates | `artifacts/evolution_governance/phase0/d1_candidates.csv` |
| Phase 0 threshold sensitivity | `artifacts/evolution_governance/phase0/threshold_calibration.csv` |
| MemTrace Phase 1 raw arms | `artifacts/evolution_governance/phase1/gpu0/memtrace_evolution_on.jsonl`, `artifacts/evolution_governance/phase1/gpu0/memtrace_all_frozen.jsonl` |
| STALE Phase 1 raw arms | `artifacts/evolution_governance/phase1/gpu1/stale_evolution_on.jsonl`, `artifacts/evolution_governance/phase1/gpu1/stale_all_frozen.jsonl` |
| Combined Phase 1 main table | `artifacts/evolution_governance/phase1/combined/phase1_combined_arena_summary.csv` |
| Final frozen decision | `artifacts/evolution_governance/phase1/combined/phase1_combined_summary.json` |
| Implementation regression record | `ml_res.md` |

```text
[RESULT] observational_arenas=6
[RESULT] observational_case_observations=10080
[RESULT] observational_chain_attempts=176496
[RESULT] observational_hypothesis_tests_run=0
[RESULT] phase1_memtrace_budget_aligned_case_rate=1.0
[RESULT] phase1_stale_budget_aligned_case_rate=1.0
[RESULT] repository_tests_recorded=467
[RESULT] repository_subtests_recorded=9
[RESULT] repository_failures_recorded=0
```

The block above is the recorded transcript of the run that produced
`artifacts/arena/analysis_full/`, and its numbers are left as recorded. Both
`observational_hypothesis_tests_run=0` and that directory's
`hypothesis_tests_run=false` were true of the analyzer at that time: it computed
no paired tests. The analyzer now computes them, so a rerun over the same six
arenas reports 60 strata and writes four tables that directory does not contain
(`cmd_vs_best_of_n_significance.csv`,
`cmd_vs_context_stuffing_significance.csv`,
`self_assessment_calibration.csv`, `abstention_curve_by_failure.csv`). The
evidence tier is unchanged — `analysis_kind` is still
`descriptive_observational`, and the new manifests record
`hypothesis_test_role=descriptive_not_confirmatory` — so these tests bound
sampling noise and still do not confirm a treatment effect.
