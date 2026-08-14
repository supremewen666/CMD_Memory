# V4 Zero-Model-Call Replay Results

## Verdict

The frozen materialized candidate outcomes support a valid zero-model-call replay,
but not an end-to-end gold-free evolution claim. Selection is gold-free; the
post-selection `recovery_gain` used for subsequent policy updates was materialized
earlier by scoring generated answers against `probe.gold_answer`. The run is
therefore a shadow-outcome-supervised algorithm screen. Within that boundary, a
simple diagonal Thompson selector and an
online linear SGD selector both outperform the current `full_v4` selector on the
same 3,100-case stream. The gain is concentrated in represented families; both
selectors underperform `full_v4` on unseen families. This is evidence for online
outcome deposition and within-niche adaptation, not yet evidence for open-domain
generalization.

## Evaluation Contract

- Workload: 3,100 frozen cases, 796 families, candidate budget 4. There are 2,484
  represented cases from 640 families and 616 unseen cases from 156 disjoint
  families.
- Utility: `recovery_gain - locality_cost - 0.05 * changed_item_count`; invalid,
  rolled-back, or abstained candidates receive zero utility.
- Protocol: prequential selected-action feedback. The selector chooses before the
  candidate outcome firewall opens; only the selected outcome can update state;
  unseen cases never update state.
- Runtime selection inputs: deployment-visible graph/context features and frozen
  typed intents. Case ID, family ID, gold, utility, and shadow candidate outcomes
  are not selector features. After selection, represented cases expose the selected
  candidate's previously materialized shadow utility as learning feedback. This
  feedback is gold-derived in the current artifact.
- Random seeds: 24, 25, 26, 27, 28. Each run uses 10,000 represented-family
  bootstrap samples. Total model calls: 0.
- Frozen bindings: case stream
  `51d18796abfb8f1eeaecbbe2d621854a6755ee018132f16364d1b9b9a31ce8aa`;
  reference outcomes
  `bb2a0acec091fa3acce39f549da009d5b82d3e06fc770ada905a1f7035d0dd29`.

## Baseline Matrix

| Baseline | Basis | Metric | Status | Notes |
|---|---|---|---|---|
| `full_v4` | Current hierarchical evolution policy | Mean utility / harm | ran | Main matched reference |
| Hierarchical, no chain | Full hierarchy with chain path disabled | Mean utility / harm | ran | Identical to Full on all 3,100 selections and utilities |
| No-update lexical | Deterministic candidate-ID tie break | Mean utility / harm | ran | Identical to the explicit deposition-off control |
| Online global replay | Existing `OnlineRepairPolicy` | Mean utility / harm | ran | Reproduced frozen Global selection and utility exactly per case |
| Periodic reset (100) | Online policy with state erased every 100 cases | Mean utility / harm | ran | Retention ablation |
| Lagged shuffled feedback | Outcome assigned from a seeded prior case | Mean utility / harm | ran | Alignment negative control |
| LinUCB | Linear contextual bandit | Mean utility / harm | ran | Deployment-visible features only |
| Diagonal Thompson | Bayesian linear contextual selector | Mean utility / harm | ran | Five seeded policy trajectories |
| Online linear SGD | Selected-feedback linear regressor | Mean utility / harm | ran | Deterministic |
| EXP3 motif | Adversarial bandit over strategy/effect motifs | Mean utility / harm | ran | Five seeded policy trajectories |

## Main Results

Values for stochastic methods are the mean over five seeds; `±` is the sample
standard deviation across policy seeds. Reference and deterministic arms repeat
exactly across seeds.

| Arm | Mean utility | Relative to Full | Harm rate | Represented | Unseen |
|---|---:|---:|---:|---:|---:|
| Hierarchical GHOST | **0.22123 ± 0.00085** | **+19.87%** | **17.18%** | **0.23174** | **0.17884** |
| `full_v4` | 0.18455 | — | 21.35% | 0.18612 | **0.17825** |
| Diagonal Thompson | **0.20993 ± 0.00118** | **+13.75%** | 18.25% | **0.22058** | 0.16702 |
| Online linear SGD | **0.20794** | **+12.67%** | **17.71%** | 0.21815 | 0.16680 |
| Legacy symbolic | 0.17843 | -3.32% | 20.65% | 0.18765 | 0.14123 |
| EXP3 motif | 0.17428 ± 0.00162 | -5.57% | 20.85% | 0.18559 | 0.12868 |
| Periodic reset (100) | 0.16864 | -8.62% | 21.23% | 0.18807 | 0.09031 |
| Random-K | 0.16805 | -8.94% | 23.00% | 0.18001 | 0.11983 |
| No update / deposition off | 0.14063 | -23.80% | 23.29% | 0.15300 | 0.09077 |
| Online global replay | 0.13102 | -29.01% | 25.42% | 0.14214 | 0.08616 |
| LinUCB | 0.12276 | -33.48% | 21.29% | 0.13599 | 0.06941 |
| Lagged shuffled feedback | 0.10661 ± 0.00306 | -42.23% | 27.19% | 0.11919 | 0.05588 |
| Identity / abstain | 0.00000 | -100.00% | 0.00% | 0.00000 | 0.00000 |

Relative to `full_v4`, Thompson reduces harm by 3.11 percentage points (14.56%
relative) and SGD reduces it by 3.65 points (17.07% relative). On represented
families, the family-macro difference against Full is +0.03353 for Thompson and
+0.03406 for SGD. The worst seed's one-sided 95% bootstrap lower bound remains
positive (+0.02291 and +0.02577 respectively). These are exploratory replay
intervals, not a preregistered confirmatory test.

Hierarchical GHOST is a post-baseline development result under the same firewall.
It uses recursive `global -> semantic -> signal` direct-reward posteriors with
per-feature shrinkage (`semantic_kappa=256`, `signal_kappa=16`). Across seeds
24--28 it exceeds diagonal Thompson by 5.38% and SGD by 6.39% in mean utility,
reduces harm by 4.17 percentage points relative to Full, and does not show the
unseen-family loss of the flat learned baselines. Its represented family-macro
difference against Full is +0.04138; the worst seeded one-sided 95% bootstrap
lower bound is +0.03018. These remain development-screening results because the
same stream was used to select the hierarchy and shrinkage constants.

## What the Controls Establish

1. **The replay is protocol-compatible.** `online_global_replay` exactly matches
   the frozen Global arm case by case, for both selected intent and utility.
2. **Correctly aligned feedback matters.** Lagged/shuffled feedback falls to
   0.10661, below both Full and the no-update selector.
3. **Persistent shadow-outcome-supervised learning matters, but not every learner
   helps.** Turning
   deposition off yields 0.14063; resetting every 100 cases yields 0.16864; the
   persistent SGD/Thompson selectors exceed 0.207. Conversely, the existing Global
   update and LinUCB are worse than no update, so “evolution” is not automatically
   beneficial.
4. **The chain mechanism has no measured effect in this run.** `full_v4` and
   `hierarchical_no_chain` make identical selections and receive identical utility
   on all 3,100 cases. This run cannot support a chain-benefit claim.
5. **The current weakness is transfer.** Thompson improves represented utility by
   18.51% over Full but loses 6.30% on unseen families. SGD improves represented
   utility by 17.21% but loses 6.42% on unseen families. Full remains the strongest
   tested method on the unseen split.
6. **There is substantial remaining selector headroom.** The post-selection shadow
   oracle has utility 0.34434. Full captures 53.60% of oracle utility; the best
   zero-call selector captures about 61.0%. The oracle never trains or selects the
   evaluated policies.

## Exploratory Risk–Coverage Result

Using a margin threshold chosen post hoc on the same replay, online SGD at 75%
coverage has system utility 0.19689, still 6.68% above Full, while all-case harm is
8.55% instead of 21.35%. Thompson gives system utility 0.19501 and 10.35% harm.
This is promising but not claim-ready: the threshold must be frozen on a separate
calibration split and then evaluated once on a sealed test stream.

## Claim Boundary

- Supported: materialized outcomes enable outcome-firewalled, selected-feedback,
  zero-model-call replay; outcome-aligned, shadow-supervised persistent deposition
  improves represented-family selection under this frozen candidate set.
- Not supported: that arbitrary replay equals a new live experiment; that the
  current Full policy is the strongest selector; that the gains transfer to unseen
  memory families; that the learning loop is end-to-end gold-free; or that the
  post-hoc risk threshold is calibrated.
- The deposition-off arm is a no-update lexical policy, not `full_v4` with only one
  internal component surgically removed. It supports the value of outcome-driven
  updating from the same initial selector, not a clean causal estimate for every
  hierarchy/species mechanism inside Full.

## Reproducibility

Runnable implementations:

- `experiments/baselines/v4_zero_call_replay.py`
- `experiments/baselines/summarize_v4_zero_call.py`

Aggregated evidence:

- `artifacts/neuro_symbolic_evolution_v4/replays/ghost-hierarchy-final-multiseed-summary.json`
- hierarchy summary SHA-256:
  `e76d2ad9607616eb50356aedbd35630e48fc0f898f28045c4bb2326ac0f80a6e`
- `artifacts/neuro_symbolic_evolution_v4/replays/zero-call-shadow-v2-multiseed-summary.json`
- summary SHA-256:
  `75a75dfc79a950cbda6095fa20c05e02727b10a1a5affc9e99a010fb82ad168d`
- per-seed report directories: `zero-call-shadow-v2-seed24` through
  `zero-call-shadow-v2-seed28`.

Focused verification: `4 passed`; Ruff: clean.

## Remaining Live-Model Experiments

Before any model call, a strict gold-free feedback replay should replace
`recovery_gain` updates with a frozen deployment-observable feedback channel (or
abstain if none exists). The next necessary model-calling experiments are:
independent risk-threshold calibration plus sealed evaluation; live
rematerialization to test candidate-outcome stability; counterfactual repair
execution on independently generated answers; cross-model transfer (Qwen
proposer/Llama executor and reverse); and a prospective prequential run comparing
revised Full+SGD/Thompson against frozen Full. Those are separate experiments and
are not silently inferred from this replay.
