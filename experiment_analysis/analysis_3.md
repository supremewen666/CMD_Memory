# Experiment Analysis Round 3 — GHOST Ecology V2 Deployment Feedback

Original V2 date: 2026-08-13
Current decision: **DEVELOPMENT-PROXY ZERO-CALL PASS; SEALED/LIVE EVIDENCE STILL REQUIRED**

## 2026-08-14 Industry-aligned V3 repair

Decision: **DEVELOPMENT-PROXY ZERO-CALL PASS; SEALED/LIVE EVIDENCE STILL REQUIRED.**

Three failures in the V2 comparison were repaired without reading `ghost_cal` for
configuration selection:

1. selection now uses a pre-action-only evaluator; post-action changed/locality/
   rollback telemetry cannot enter candidate scores;
2. GHOST learns the nonzero matured-outcome proxy residual
   `y_delayed_proxy - q_pre_action`, and a new `full_v4_observable` arm receives
   the same prior and selected-action residual; the old `full_v4` remains a
   shadow-gold oracle ceiling;
3. Expert identity is the 12 preregistered `(effect, typed motif)` repair types,
   with case targets/proposers as parameters. The previous dev identity produced
   1,058 species, 662 singletons; the registered repair-type grammar produces 12
   species and zero singletons. Pattern/local effects are residual random effects
   and require support 2/3 before affecting selection.

The refitted pre-action evaluator remained identifiable on family-disjoint cal:
family Pearson `0.6393220035`, one-sided 95% bootstrap LB `0.5290066352`,
pairwise concordance `0.7056096430` — all PASS.

Five complete zero-call replays used seeds 24--28, 3,100 cases, eight arms,
24,800 rows per seed and 10,000 family-bootstrap samples per seed. The same-feedback
primary comparison was:

| Comparison | Mean family-macro delta | Worst seed one-sided 95% LB | Decision |
|---|---:|---:|---|
| GHOST - Full V4 observable | +0.0872389171 | +0.0542759323 | PASS |
| GHOST - Full V4 gold oracle (context only) | +0.0357592521 | +0.0089756501 | PASS, non-decision |

Every seed had 2,518 nonzero dev residuals. Across all five runs both learning
arms had zero posterior/policy changes on all 582 `ghost_cal` cases. Embedded
report hashes, raw-row family estimates and 124,000 row counts were independently
recomputed. Model/API/network calls were zero.

This result repairs the earlier V2 experimental mismatch. It is still based on
an already-materialized future-outcome proxy, not a real matured deployment
ledger, and `ghost_test_rep` / `ghost_test_new` remain empty. It therefore
authorizes the next data-freeze/real-feedback stage, not an efficacy claim from
sealed or live deployment.

## Repair outcome

The original immediate-probe audit below correctly failed because its inputs were
nearly constant. It has not been deleted or relabelled. The repaired design adds a
frozen, parametric deployment evaluator between typed execution telemetry and GHOST:

```text
ghost_dev shadow labels -> fit and freeze evaluator
ghost_cal observable features -> evaluator score -> audit-only shadow comparison
deployment selected telemetry -> evaluator score -> selected-skill GHOST update
```

The evaluator has a closed, identity-free feature grammar, fixed 512 feature-hash
buckets, ridge `1.0`, content-addressed weights, and no runtime API/model dependency.
It cannot accept case/family/gold/answer/recovery/utility fields. Calibration never
updates it.

### Repaired identifiability gate

| Gate | Result | Threshold | Decision |
|---|---:|---:|---|
| Family-macro Pearson | 0.6374268463 | >= 0.20 | PASS |
| One-sided 95% family-bootstrap lower bound | 0.5272669761 | >= 0.10 | PASS |
| Within-case pairwise concordance | 0.7032916087 | >= 0.55 | PASS |

- Fit: `ghost_dev`, 2,518 cases / 640 families / 10,072 candidates.
- Audit: family-disjoint `ghost_cal`, 582 cases / 156 families / 2,328 candidates.
- Evaluator snapshot:
  `9f27df1526f3d0f81638d893194209a6e655a4b2c286e98bd9bc360773ec784d`.
- Model/API/network calls: 0.
- Claim scope: dev-fitted calibration identifiability, not sealed test.

### Partition-correct GHOST prequential result

`ghost_hierarchy_v1` is now the seventh registered arm in
`experiments/v4_prequential_runner.py`. Expert identity is reusable
`(strategy_id, effect, proposer_id, proposer_model_hash)`; case-bound relation/target
values are parameters, not new species. Only `ghost_dev` updates GHOST. `ghost_cal`
is evaluation-only.

After enforcing the same `ghost_dev` update boundary on every online arm and
placing the frozen evaluator in its intended MoE role, the final residual-posterior
run produced:

| Comparison on ghost_cal | Family-macro delta | One-sided 95% LB | Decision |
|---|---:|---:|---|
| GHOST vs Full V4 | -0.0072636236 | -0.0309097982 | FAIL |
| GHOST vs global policy | +0.0628574068 | +0.0327165307 | PASS |

- GHOST cal mean utility: `0.2568861680`.
- GHOST cal posterior unchanged: 582/582 cases; update indexes present: 0.
- Rows: 21,700 = 3,100 cases × 7 registered arms.
- Model calls: 0.

The feedback evaluator is identifiable, but the current GHOST residual posterior
does not establish stable superiority to the stronger frozen Full V4 comparator.
The overall gate therefore remains blocked. No further structure or exploration
parameter was tuned on the same calibration set.

An earlier seven-arm development replay used legacy `probe_set` rather than the
prospective GHOST partition assignment. The audit found 523 `ghost_cal` cases marked
legacy `represented`; that artifact is invalid for the final claim and was replaced
by later non-overwriting, partition-correct runs. The final evidence is
`prequential-zero-call-residual-final-seed24`.

## Original immediate-probe ablation

Decision at this stage: **BLOCKED_FEEDBACK_NOT_IDENTIFIABLE**

## Evidence boundary

This round is a zero-model/API/network-call audit of the deployment-observable
feedback channel. GHOST does not learn from `recovery_gain`, gold answers, family
IDs, or unselected outcomes. Previously materialized shadow utility is read only
on the audit side to test whether the registered feedback signal is identifiable.

This result does not invalidate the earlier shadow-gold router screen. It does
invalidate using that screen as authorization for a live/model-calling experiment.

## Frozen execution

- Cases: 3,100.
- Families: 796.
- Candidate observations: 12,400.
- Candidate budget: 4.
- Bootstrap: 10,000 family resamples; preregistered seed 24.
- Feedback: skill-conditioned typed-executor/deployment-guard telemetry V2.
- Model/API/network calls: 0.
- Input file SHA-256:
  `52569b23e71fe1750a0b3e037670587b0f485df2fa250bd04a24537d61f3d522`.

## Gate results

| Gate | Result | Threshold | Decision |
|---|---:|---:|---|
| Family-macro Pearson | 0.0036162454 | >= 0.20 | FAIL |
| One-sided 95% family-bootstrap lower bound | -0.0662486454 | >= 0.10 | FAIL |
| Within-case pairwise concordance | 0.6249671485 | >= 0.55 | PASS |

The action-conditioned probes improve pairwise concordance from the old V1 audit's
`0.3113906987` to `0.6249671485`. Therefore action typing was a real issue, but it
was not the only issue. Family-level identifiability remains absent.

## Why the feedback channel is still insufficient

The available materialization records execution shape, not delayed task outcome:

- valid rate: 1.0;
- rollback rate: 0.0;
- zero-change rate: 0.5630645161;
- exactly-one-change rate: 0.1744354839;
- delayed regression observed: false;
- target resolution observed: false;
- annotation consumption observed: false.

Consequently, the immediate probes saturate. Verify and abstain probes both have
success rate 1.0; all target-mutation probes have success rate 1.0. Only annotation
commit varies, and commit is not evidence that later routing consumed the annotation
or that recurrence fell. These signals identify whether a typed action executed,
but generally cannot identify whether it repaired the failure.

## Seed robustness

Exploratory bootstrap seeds 25–28 changed no data, feedback rule, threshold, or
router state. All remained blocked, with lower bounds:

| Seed | Lower bound |
|---:|---:|
| 24 | -0.0662486454 |
| 25 | -0.0666305369 |
| 26 | -0.0689938799 |
| 27 | -0.0674628837 |
| 28 | -0.0682272139 |

The primary decision remains the preregistered seed-24 decision.

## Required next evidence

Do not relax the thresholds or substitute shadow recovery for deployment feedback.
The next zero-call corpus/materializer must expose independently observable,
skill-conditioned outcomes:

1. verify: registered postcondition probes pass with no mutation;
2. replace/demote/suppress: target failure resolution plus anchor non-regression;
3. annotate: a later router consumes the annotation and recurrence decreases;
4. abstain: no delayed recurrence or unsafe escalation in a frozen observation window;
5. every skill: execution cost, rollback, locality, and delayed regression.

Only after those fields are physically present, hash-bound, and the unchanged gate
passes may `ghost_hierarchy_v1` be authorized in the live prequential runner.

## Integrity and verification

- Primary embedded report SHA-256:
  `71096b0ca7086a9b02bfa85ceab5d165497b175531c060def70488c3f063f91f`;
  canonical recomputation matched.
- Primary report file SHA-256:
  `ccc9f403756e591e7e48afb33be906b88ffd43a48bbd73e1d4b26f7a7109f2b0`.
- Focused GHOST/evaluator/runner tests: 27 passed.
- Broad tests: 859 passed, 1 skipped, 1 warning, 58,232 subtests passed.
- Ruff: PASS.
- `git diff --check`: PASS.
