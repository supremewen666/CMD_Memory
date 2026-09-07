# BUILD SPEC V2 — Descriptor–Fitness–Policy Separation for CMD

- **Status:** V0 executed — `NO_GO`; V1/V2/V3 are not authorized on the
  inspected benchmarks
- **Working name:** SIGIL-QD V2
- **Supersedes:** `BUILD_SPEC_AUDITED_NICHE_EVOLUTION.md`
- **Evidence state:** Stage 1 live item-gate audit completed with `NO-GO` for
  direct signal-to-action routing
- **Execution record:** [`V0_DESCRIPTOR_POLICY_RESULT.md`](V0_DESCRIPTOR_POLICY_RESULT.md)
- **Post-V0 Route A:** [`BUILD_SPEC_ROUTE_A_STATE_FITNESS_AND_SYNTHESIS.md`](BUILD_SPEC_ROUTE_A_STATE_FITNESS_AND_SYNTHESIS.md)
- **Scope:** One bounded viability screen, shadow niche learning, target-tested
  transfer/composition, and one untouched confirmatory evaluation

## 0. Executive decision

The completed Stage 1 experiment invalidates the V1 activation rule, not every
possible use of the observed structural signals.

V1 required a domain × signal cell to show that a preassigned operator was
near-oracle before the signal could become a niche descriptor. This conflated
three different objects:

```text
descriptor: what runtime situation does this case resemble?
fitness:    which executable repair actually worked here?
policy:     which repair, if any, should be activated now?
```

The final attempt will keep these objects separate:

```text
runtime descriptor z(x)
    -> assigns a case to a niche only

legal candidate executions a ∈ A(x)
    -> produce post-outcome repair utility y(x, a)

held-out niche-local comparison
    -> identifies an elite, or no elite

frozen activation policy π(z)
    -> selects a validated elite or abstains
```

A structural signal must not directly suggest, vote for, validate, or activate
a repair action. Different input modalities do not make two channels
independent, and agreement between two biased proxies is not ground truth.

Before any new model calls or untouched-data expenditure, existing artifacts
must pass the zero-call viability screen in Section 7. Failure ends the positive
evolution program on these benchmarks.

## 1. Binding empirical findings

### 1.1 Leakage result

The earlier safety channel is invalid:

```text
MemFail:  injected safety metadata ⇔ safety_error label, 157/157
MemTrace: injected safety metadata ⇔ safety_error label, 496/496
off-diagonal cases: 0
```

`safety_filter_blocked`, `passed_safety_filter`, perturbation labels, injector
metadata, and their derived forms remain forbidden from runtime construction,
descriptors, policy selection, deposition, promotion, and evaluation inputs.

### 1.2 Single-proxy non-identifiability result

The observed contrast:

```text
reference-free selection, original run: 220.9
same scorer family, independent rerun:   219.7
family pooling:                          approximately 251
```

supports this bounded claim:

> When only outputs and resamples of the same miscalibrated reference-free
> measurement mechanism are available, stable scorer bias cannot be
> distinguished from stable task-relevant preference by reproducibility,
> margin, or self-consistency alone.

It does not establish that every possible single signal is insufficient. A
single task-grounded deterministic evaluator can be sufficient. The paper must
state the observation domain, accessible statistic class, and absence of
external task feedback.

### 1.3 Live Stage 1 result

The authoritative artifact is:

```text
artifacts/sigil_qd/stage1/audit/stage1_summary.json
```

The completed audit contains 3,939 cases and 2,878 pre-outcome live indication
events. No active direct-routing scope passed.

Selected findings:

| Scope | Diagnostic observation | Preassigned-action validity | Mean incremental gain |
|---|---|---:|---:|
| STALE temporal | `item_stale` precision 100%, recall 91.7% | 35.5% | +0.005 |
| STALE collision | `item_conflict` precision 98.0%, recall 89.3% | 32.7% | -0.005 |
| MemTrace temporal | `item_stale` precision 88.3%, recall 92.5% | 16.6% | -0.004 |
| MemTrace collision | broad mixed firing | 8.7% | -0.063 |
| MemFail collision | structurally confounded | 3.8% | -0.162 |

Signal strength was not calibrated as action confidence. For example, almost
all STALE temporal events had near-maximal signal strength, while their
preassigned-action validity remained about 35%.

The result supports:

> A runtime sensor may identify a condition without identifying the repair that
> maximizes utility under that condition.

It does not support activating direct signal-to-action routing, and it does not
by itself show that the same signals are useless as behavior descriptors.

### 1.4 Measurement limitation

The current item-gate stops at the first non-PASS result. Consequently:

- no case records a complete simultaneous signal signature;
- collision can preempt temporal or leave-one-out divergence;
- the ten observed divergence events are insufficient to assess divergence as
  a descriptor;
- absence of a downstream signal is often missing-by-control-flow, not a
  negative measurement.

Any future shadow descriptor run must evaluate frozen extractors
non-short-circuit and record all signal outputs before policy execution.

## 2. Claim boundary

### 2.1 Primary research question

Do deployment-visible structural descriptors identify subpopulations with
different held-out optimal repair policies, such that a niche-keyed policy
outperforms equal-budget frozen, unkeyed, and random-key controls without
scope-external, null, Fill, or safety harm?

### 2.2 Positive claim, only after confirmation

> Runtime-derived behavior descriptors support held-out niche-local selection
> of executable repair skills, improving recovery over equal-budget frozen and
> unkeyed policies under explicit abstention and safety constraints.

### 2.3 Separate optional claims

Transfer:

> A source-niche elite improves target-niche held-out recovery after direct
> target-niche execution and validation.

Composition:

> Sequential execution of two skills improves held-out recovery beyond both
> single skills under the same total budget.

Open-ended evolution is out of scope unless the system also generates new
challenges and new executable operators. Archive growth, routing, reuse, or
elite replacement alone will be described as audited online skill selection
and deposition.

### 2.4 Claims that remain forbidden

- signal strength is repair confidence;
- signal-label agreement is repair validity;
- two-channel agreement establishes truth or channel independence;
- migration is composition;
- a prompt or reasoning template is an executable skill;
- archive size, deposits, generations, or edge count establish capability
  growth;
- a new judge run over inspected cases is fresh confirmation;
- a runtime niche may contain a perturbation label or recurrence-family ID.

## 3. Normative separation contract

### 3.1 Descriptor contract

The descriptor is computed before outcome observation:

```text
z(x) = f(
    query,
    recalled item contents,
    deployment-visible timestamps,
    retrieval ranks/scores,
    frozen non-short-circuit structural extractor outputs,
    calibration-frozen content fingerprint
)
```

It may:

- assign a case to a niche;
- express uncertainty or `unknown`;
- determine which already-validated niche policy is eligible for lookup.

It may not:

- contain `suggested_operator_family`;
- read gold, labels, injector flags, evaluator outcomes, or family IDs;
- promote an operator;
- determine that an operator is correct;
- activate a newly learned operator on its producing case.

### 3.2 Fitness contract

Fitness comes only from post-outcome evaluation of a concrete executable
candidate:

```text
u(x, a) =
    normalized_recovery_gain(x, execute(a, base_context))
    - frozen_cost_penalty(x, a)
```

All compared candidates must share:

- legal candidate set;
- answerer and judge identities;
- prompt/rubric versions;
- answer, token, rollout, and logical-operation budgets;
- case order and effective-after boundary;
- nonfinite and abstention treatment.

Descriptor quality, label accuracy, LLM confidence, rationale style, and
archive frequency are not fitness.

### 3.3 Policy contract

The activation policy is learned only from calibration outcomes and frozen
before its evaluation cases:

```text
π(z) -> validated OperatorSpec revision | frozen selector | abstain
```

The policy must:

- use shrinkage or fall back when a niche lacks support;
- be effective only after the evidence that created it;
- preserve byte-identical frozen behavior outside active niches;
- abstain when the conservative expected advantage is non-positive;
- never use the current case outcome.

### 3.4 State-machine separation

The following ledgers are distinct and append-only:

```text
descriptor_version_ledger
candidate_execution_ledger
niche_archive_ledger
activation_policy_ledger
transfer_composition_ledger
```

No state transition in one ledger implicitly promotes a state in another.

## 4. Runtime integrity

### 4.1 Denylist

The denylist includes aliases, hashes, embeddings, derived forms, and nested
copies of:

```text
perturbation_label
perturbation_type
gold_*
oracle_*
shadow_gold_*
recurrence_family_id
evaluation family_id
safety_filter_blocked
passed_safety_filter
injector provenance and injector-written flags
current-case evaluator outcomes
split membership
```

Evaluation `family_id` is permitted only in blocked statistical analysis.

### 4.2 Runtime surface

`runtime_surface` must name a deployment architecture location such as:

```text
fill
tier2_item_context
tier3_pipeline_context
```

It must not be a renamed gold fault class.

### 4.3 Provenance

Every descriptor field records:

```text
field_name
origin_component
available_before_outcome
available_in_deployment
injector_can_write
extractor_version
value_hash
```

Unknown provenance fails closed.

## 5. Initial descriptor family

The initial descriptor must remain small enough to support held-out inference.
Do not begin with an unrestricted Cartesian product.

Candidate components:

```text
content_fingerprint_cluster
temporal_content_contradiction bucket
recall_set_collision bucket
reference_contrast_divergence bucket
coverage_insufficiency bucket
runtime_surface
```

Rules:

1. Start with one signal family × runtime surface; add a second axis only after
   the first has adequate occupancy.
2. Bucket boundaries and cluster assignments use calibration data only.
3. Collapse unsupported cells to their declared parent or `unknown`; do not
   silently pool after results are observed.
4. Report cell count, occupancy distribution, effective sample size, entropy,
   and `unknown` rate.
5. Measure assignment stability across seeds, extractor reruns, and allowed
   judge/model variants.
6. A descriptor whose adjusted Rand agreement or preregistered stability metric
   fails remains descriptive only.
7. STALE is the initial feasibility domain because temporal and collision
   signals showed diagnostic content there. MemTrace and MemFail are negative
   transport tests, not required positive domains.

The failure label is never an axis.

## 6. Candidate repair set

### 6.1 Frozen initial candidates

The viability screen uses only executable, already materialized legal
operators. At minimum include:

```text
frozen selector action
abstain / no repair
stale replacement
conflict arbitration
evidence-preserving demotion
coverage fill
context stuffing named baseline
random legal operator
```

Every candidate must have a versioned `OperatorSpec`, explicit preconditions,
concrete state transition, verification rule, and cost.

### 6.2 Reasoning templates

An elite may carry a structured reasoning scaffold containing:

```text
preconditions
evidence slots
ordered executable steps
expected state transition
verification checks
abstention conditions
```

It is metadata attached to an executable revision. It is not raw hidden
chain-of-thought, generic answer guidance, an independent candidate, or
evidence of correctness.

### 6.3 Novelty requirement

No evolutionary novelty claim is allowed unless the system defines and tests a
candidate-generation mechanism, for example:

- typed parameter mutation within `OperatorSpec`;
- guarded insertion, deletion, or reordering of executable steps;
- composition proposal with a real intermediate context;
- model-generated candidate compiled into the same constrained grammar.

Generated candidates remain provisional until independent post-creation cases
validate them. Without this mechanism, use “niche-local selection/deposition,”
not “skill evolution.”

## 7. Stage V0 — Zero-call conditional-policy viability screen

### 7.1 Purpose

This stage asks whether the existing data contain any recoverable
signal-conditioned policy value. It makes no new model calls and does not open
untouched confirmation data.

### 7.2 Split

- Split at the highest dependency unit: user, source episode, or recurrence
  family.
- Sibling variants never cross folds.
- Use deterministic nested cross-fitting with serialized seeds.
- Descriptor construction, cell merging, elite choice, and fallback thresholds
  are fitted inside each training fold.
- The outer test fold is used exactly once for that fold.

### 7.3 Required analyses

#### A. Oracle headroom

For every case with comparable candidate executions:

```text
headroom(x) = max_a u(x, a) - u(x, frozen)
```

Report overall and descriptor-conditional headroom. This is a necessary but
not sufficient condition.

#### B. Skill heterogeneity

For each training niche, estimate the best supported operator and compare it
with the globally best operator. Report:

- number and share of supported niches;
- elite identity distribution and entropy;
- between-niche utility interaction;
- whether one operator wins almost everywhere;
- stability of elite identity across inner folds.

If one operator dominates all supported niches, QD adds no demonstrated value.

#### C. Cross-fitted policy value

Fit on training folds and evaluate on outer held-out families:

```text
frozen_policy
unkeyed_global_policy
runtime_descriptor_policy
random_descriptor_policy
label_oracle_policy       # descriptive upper bound only, never deployable
```

The runtime descriptor policy must use conservative shrinkage:

```text
if niche support or lower bound is insufficient:
    fall back to unkeyed or frozen according to the preregistered hierarchy
```

#### D. Actuator audit

For every candidate operator, report:

- execution eligibility and failure rate;
- effect on the intended runtime state;
- recovery gain conditional on successful state change;
- cost and nonfinite rate;
- null, Fill, and safety regressions;
- reason for no-op.

This distinguishes “the sensor found a niche but the available actuator cannot
repair it” from “the niche has no policy value.”

### 7.4 Equal-budget contrasts

Primary:

```text
runtime_descriptor_policy - unkeyed_global_policy
runtime_descriptor_policy - frozen_policy
```

Negative control:

```text
runtime_descriptor_policy - random_descriptor_policy
```

All contrasts are paired at the family level.

### 7.5 Frozen V0 GO gate

Proceed to a new shadow archive only when all are true:

```text
1. oracle headroom mean > 0 and one-sided family-blocked LB95 > 0
2. descriptor policy - frozen mean > 0 and LB95 > 0
3. descriptor policy - unkeyed mean > 0 and LB95 > 0
4. descriptor policy - random descriptor mean > 0 and LB95 > 0
5. at least two supported niches select different stable elites
6. selected-elite agreement across outer training folds >= 0.80
7. scope-external and unseen-family LB95 >= -0.05
8. null and Fill exact-selection invariants = 100%
9. anchor regressions = 0
10. budget alignment = 100%
```

Minimum supported niche:

```text
n_training_cases >= 30
n_training_families >= 10
n_test_families >= 5
```

If sample sizes cannot support these gates, the verdict is
`INSUFFICIENT_SUPPORT`, not `GO`.

### 7.6 V0 NO-GO

If any primary efficacy gate fails:

- do not spend untouched confirmatory data;
- do not add another selector, signal-to-action mapping, or QD variant on the
  same evidence;
- freeze the evolution chapter as leakage audit + two preregistered negative
  results + bounded non-identifiability result + sensor/controller separation;
- move experimental budget to the repair and null-protection claims.

This is the binding final stop for mechanism iteration on the inspected
benchmarks.

## 8. Stage V1 — Non-short-circuit shadow archive

Authorized only after V0 GO.

### 8.1 Shadow measurement

Run all frozen structural extractors independently on each case before outcome
observation. Record a complete signal vector and missingness reason. The shadow
path must not change retrieval, context, answer generation, or repair choice.

### 8.2 Cell-local competition

A challenger replaces an incumbent only on independent target-cell cases under
the same budget when:

```text
no anchor/null/Fill regression
paired mean utility difference > 0
one-sided family-blocked LB95 > 0
at least 3 challenger-only recoveries
cost non-inferiority passes
```

Cross-niche performance cannot evict an incumbent.

### 8.3 Controls

Run isolated state for:

```text
all_frozen
unkeyed_pool
random_niche
map_elites_shadow
```

`map_elites_shadow` remains observational until its policy version passes
held-out calibration gates.

### 8.4 V1 gate

Proceed only if the shadow archive reproduces the V0 descriptor-policy
advantage on later, non-producing calibration families and passes every safety
and budget gate.

## 9. Stage V2 — Transfer and true composition

### 9.1 Transfer

`A → B` means only:

1. A’s elite is executed as an ordinary candidate on independent B cases;
2. it beats B’s incumbent under the frozen target-niche evaluator;
3. it passes anchors, null, Fill, safety, and cost gates.

Transfer does not imply a chain.

### 9.2 Composition

`A ∘ B` requires real sequential execution:

```text
context_1 = execute(A, base_context)
context_2 = execute(B, context_1)
```

Primary statistic:

```text
chain_gain = u(x, A ∘ B) - max(u(x, A), u(x, B))
```

An edge requires independent cases across at least two families, positive
family-blocked LB95, budget compliance, and no protected regression.
Concatenated text, shared rationale, co-occurrence, or transfer success cannot
create a composition edge.

## 10. Stage V3 — One-shot confirmation

### 10.1 Holdout integrity

Confirmation requires a source/family set untouched by:

- signal and descriptor design;
- threshold, bucket, and clustering decisions;
- operator construction;
- V0/V1 analysis;
- paper claim selection.

Changing only the judge on inspected cases is robustness, not confirmation.
Freeze dataset hashes, ordered-case hash, code revision, prompts, model
identities, seeds, budgets, policy version, and decision rules before opening
the holdout.

### 10.2 Confirmatory arms

```text
all_frozen
context_stuffing
unkeyed_pool
random_niche
sigil_qd_v2_frozen
```

Add `sigil_qd_v2_edges` only if V2 created at least one valid edge.

### 10.3 Primary success

The positive niche claim requires:

```text
sigil_qd_v2_frozen - all_frozen: mean > 0 and LB95 > 0
sigil_qd_v2_frozen - unkeyed_pool: mean > 0 and LB95 > 0
sigil_qd_v2_frozen - random_niche: mean > 0 and LB95 > 0
```

All mandatory safety, null, Fill, anchor, and budget gates must pass. Edge
claims additionally require a positive registered edge-vs-no-edge contrast.

The holdout is read once. A failure cannot authorize another mechanism
iteration on the same benchmark family.

## 11. Statistical protocol

- paired unit: highest dependency unit, normally family or user;
- estimator: paired family-mean difference;
- interval: one-sided family-blocked paired bootstrap, 10,000 resamples;
- randomization check: family-level sign-flip permutation, 9,999 draws;
- report discordant recoveries, effect, interval, p-value, coverage,
  abstention, nonfinite rate, and cost;
- do not treat cases inside a family as independent;
- freeze whether the primary family uses Holm correction or a single ordered
  primary contrast;
- missing and nonfinite outcomes remain explicit;
- exploratory cells and subgroup effects are labeled exploratory.

## 12. Implementation changes

### 12.1 Retire from activation

The following V1 concepts remain available only for historical analysis:

```text
suggested_operator_family in StructuralIndication
P(valid preassigned action | signal fires) as an activation gate
domain × signal direct-routing scope promotion
two-channel agreement as evidence of correctness
Stage 1 NO-GO as proof that descriptors have no conditional policy value
```

Do not delete historical artifacts.

### 12.2 Required components

```text
experiments/analyze_descriptor_policy_value.py
    V0 cross-fitted headroom, heterogeneity, policy contrasts, and GO/NO-GO

cmd_audit/repair/behavior_descriptor.py
    gold-free versioned descriptors with parent/unknown fallback

cmd_audit/repair/niche_archive.py
    cell-local candidates, elites, immutable transitions, and rollback

cmd_audit/repair/activation_policy.py
    frozen conservative policy and exact fallback/abstention behavior

experiments/run_niche_shadow_v2.py
    non-short-circuit shadow measurement and isolated control arms

experiments/run_niche_confirmatory_v2.py
    one-shot frozen-policy confirmation
```

Reuse the existing `OperatorSpec`, append-only governance, anchor, scorer
identity, family-blocked inference, and artifact-manifest contracts.

### 12.3 V0 artifact contract

```text
v0_manifest.json
descriptor_occupancy.csv
descriptor_stability.json
operator_actuator_audit.csv
oracle_headroom_by_scope.csv
elite_heterogeneity.csv
crossfit_policy_predictions.jsonl
paired_policy_contrasts.csv
protected_scope_gates.csv
v0_claim_decision.json
```

`v0_claim_decision.json` is computed mechanically from Section 7.5.

## 13. Required tests

### Descriptor

- denylisted fields and derived aliases are rejected;
- the same allowed input/version produces the same descriptor;
- extractor order does not change a complete signal vector;
- downstream signals are measured after an earlier signal fires;
- unsupported/OOD inputs map to the registered parent or `unknown`;
- split, family, and label metadata cannot affect assignment.

### Cross-fitting

- sibling cases cannot cross outer folds;
- descriptor fitting and cell merging use training folds only;
- producing cases cannot validate candidates;
- outer test outcomes cannot change the fitted policy;
- random-niche control preserves occupancy and budget;
- unkeyed and keyed arms share the same candidate set.

### Archive and policy

- cross-niche challengers cannot evict incumbents;
- an unsupported niche falls back exactly;
- inactive/scope-external cases reproduce frozen selection byte-for-byte;
- anchor/null/Fill regression vetoes promotion;
- policy activation is effective only after its evidence;
- arm state cannot leak between controls.

### Transfer/composition

- target-niche execution is required for transfer;
- transfer creates no composition edge;
- composition consumes a real intermediate context;
- chain gain is compared with both singles;
- failed attempts remain in the ledger but create no active edge.

### Evidence

- V0 decision exactly matches all registered gates;
- insufficient support cannot become GO;
- family-blocked inference preserves pairing;
- holdout hashes cannot match a development manifest;
- robustness reruns are not labeled confirmation.

## 14. Execution order

```text
V0: zero-call conditional-policy viability
    ├── GO -> implement non-short-circuit shadow archive
    └── NO-GO / insufficient support
            -> stop positive evolution work on these benchmarks
            -> finalize bounded negative/mechanistic chapter
            -> move budget to repair + null protection

V1: later-family shadow archive
    ├── GO -> optional target-tested transfer/composition
    └── NO-GO -> retain V0 diagnostic only; stop

V2: transfer/composition
    ├── passing edges -> include frozen edge arm
    └── no passing edges -> no chain claim

V3: one-shot untouched confirmation
    ├── primary efficacy + all protections pass -> bounded positive claim
    └── otherwise -> strongest passed partial/negative claim; final stop
```

## 15. Paper reporting

### If V0 fails

The evolution chapter contains:

1. two preregistered negative results;
2. the single-proxy non-identifiability observation with explicit assumptions;
3. the safety-metadata leakage cross-tab and benchmark audit method;
4. the live counterexample separating diagnostic accuracy from repair utility;
5. the harmless frozen/abstaining shell;
6. the resulting design law: descriptor, fitness, and activation policy must
   be independently evidenced.

### If V0 passes but confirmation fails

Report conditional policy value as a development/calibration result. Do not
promote it to a confirmed evolution claim.

### If confirmation passes

Claim only bounded audited niche-local selection/deposition. Use “evolution”
only if validated generation/mutation created novel executable operators.

## 16. Definition of done

The evolution beam is repaired only if:

- V0 demonstrates cross-fitted descriptor-policy value over frozen, unkeyed,
  and random-descriptor controls;
- at least two supported niches require different stable elites;
- runtime construction is gold-, label-, injector-, split-, and family-free;
- V1 reproduces the effect on later non-producing families;
- one untouched confirmatory read passes all efficacy and protection gates;
- artifacts reproduce mechanically from a frozen commit and manifest;
- paper language stays within the highest passed evidence stage.

Anything less is not “almost repaired.” It is the strongest supported partial
or negative result.

## 17. Design provenance and limits

The architecture adapts ideas from:

- [MAP-Elites](https://arxiv.org/abs/1504.04909): experimenter-defined
  behavior descriptors and cell-local competition;
- [Rainbow Teaming](https://arxiv.org/abs/2402.16822): QD organization of a
  defined feature space, while retaining its warning that proxy judges can be
  exploited;
- [POET](https://arxiv.org/abs/1901.01753) and
  [Enhanced POET](https://arxiv.org/abs/2003.08536): target-tested transfer
  between environment–agent pairs, not proof of a persistent skill chain;
- [Buffer of Thoughts](https://arxiv.org/abs/2406.04271): reusable reasoning
  scaffolds, not executable-skill validation;
- [Voyager](https://arxiv.org/abs/2305.16291): executable skills with
  environment feedback and verification;
- [FunSearch](https://www.nature.com/articles/s41586-023-06924-6) and
  [ADAS](https://arxiv.org/abs/2408.08435): candidate generation coupled to an
  evaluator, with different degrees of evaluator objectivity.

These works motivate design choices only. They do not supply evidence that CMD
descriptors, operators, policies, or evolutionary claims are valid.
