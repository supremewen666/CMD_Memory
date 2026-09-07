# Mix GHOST, Skill Ecology, and Safe Memory Repair

## Normative Experiment Specification

- Status: Design-complete draft v0.3; not yet preregistered
- Date: 2026-08-26
- Intended venues: ICLR / ICML / AAAI-style research evaluation
- Audience: authors, collaborators, implementers, reviewers, and artifact evaluators
- Scope: experiment design, evidence boundaries, data protocol, model protocol, and reporting contract

## 0. Authority and evidence boundary

This document is the normative protocol for the next experimental program.

Existing repository artifacts, prior result summaries, cached metrics, exploratory plots, and earlier experiment narratives are non-authoritative. They may be used later as implementation inputs only after they pass this specification's provenance and split checks. They must not be used to justify the design choices or claims in this document.

The experiment is considered preregistered only after every freeze item in Section 0.1 is resolved and the resulting manifest is hashed. Until then, this document defines the research design and the contracts required to make it executable; it does not claim that the current algorithm or run configuration is frozen.

Normative terms:

- `MUST`: required for the corresponding claim.
- `MUST NOT`: prohibited because it creates leakage, ambiguity, or an invalid comparison.
- `SHOULD`: strongly recommended; deviations require a written justification.
- `MAY`: optional and cannot be required to support a headline claim.

### 0.1 Freeze registry

Every confirmatory run MUST bind the following IDs to immutable content hashes:

| Freeze ID | Object | Blocking owner decision |
|---|---|---|
| `F-DATA` | sources, constructors, family hierarchy, splits, lockbox | exact cases and hashes |
| `F-MG-ALG` | observable backbone, hierarchical residual state, support gates, exploration, update, clipping, tie-break | source-equivalent equations in Section 5; final source hash pending |
| `F-SKILL` | seed library, lifecycle rules, validation rule | thresholds and content hashes |
| `F-SYNDROME` | predicted decoder, descriptor, candidate retrieval, legal-mask builder | exact decision-time implementation |
| `F-REWARD` | component outcomes, scalar reward, delayed settlement | coefficients and maturity rules |
| `F-EVAL` | estimands, horizons, multiplicity, non-inferiority margins | values in Section 13 |
| `F-MODEL` | all source, target, judge, and embedding models | exact revisions and serving settings |
| `F-BASELINE` | baseline commits, adapters, tuning rights, budgets | pinned matrix |
| `F-LOCKBOX` | result access, execution batch, rerun and unsealing rules | designated custodian or automation |

`F-MG-ALG` is resolved at the algorithm-design level in Section 5 from the current implementation. It becomes preregistration-complete only when the implementation and tests named in Section 5.9 are frozen to final content hashes. The code is used here to define the method, not as empirical evidence.

### 0.2 Unit hierarchy

The data hierarchy is fixed as follows:

```text
source domain
  -> source episode (conversation, user, task, repository, or scenario)
    -> semantic family
      -> lineage (clean event plus every derived corruption and repair branch)
        -> case
          -> query or checkpoint observation
            -> repeated execution seed
```

The independent sampling unit is the semantic family unless the data source establishes that source episodes are the higher independent unit. In that case inference clusters at source episode. A lineage never crosses a family, and no lower-level observation is counted as an independent sample.

Additional terms:

- `constructor family`: a programmatic mechanism such as reorder, supersede, or sleeper injection.
- `corruption template`: a parameterized implementation inside one constructor family.
- `niche`: a decision-time descriptor cell; it is not a sampling unit and may span multiple semantic families.
- `skill action`: the selectable unit. One skill version references exactly one executable operator version. Router regret is defined over skill actions, not over ambiguous skill/operator pairs.

## 1. Research thesis and contribution allocation

### 1.1 Central thesis

CMD is a model-agnostic external learning layer for long-lived agent memory systems. It converts immutable memory events and settled repair receipts into two persistent substrates:

1. an external parameter memory used by Mix GHOST to select among legal repair skills; and
2. a versioned population of typed repair skills that can be born, revised, specialized, quarantined, and retired.

All proposed changes execute under shadow or copy-on-write semantics and become visible only after root, invariant, safety, and locality checks pass. Failed candidates roll back and still produce auditable receipts.

### 1.2 Contribution weights

The paper and experiment budget SHOULD reflect the following allocation:

| Contribution | Weight | Primary question |
|---|---:|---|
| Mix GHOST routing | 40% | Can an external parameter memory select legal repair skills better than static, random, and standard online routers, and can that routing state transfer across LLMs? |
| Skill ecology | 30% | Do typed skills genuinely specialize, reproduce through versioning, compete, and retire under changing incident regimes? |
| Safe memory repair | 30% | Do the selected operators repair process faults, state drift, and poisoning without false commits or collateral mutation? |

### 1.3 Claim hierarchy

The intended claim hierarchy is:

- `C1 Router`: Mix GHOST is a routing selection mechanism whose external parameter memory improves safe prequential utility and regret relative to strong router baselines.
- `C2 Transfer`: frozen Mix GHOST parameter memory transfers across model families and can be efficiently recalibrated with a small target-only prefix.
- `C3 Ecology`: a governed skill population produces held-out specialization and adapts to regime shifts beyond static, add-only, and random-partition controls.
- `C4 Repair`: the complete system improves safe repair outcomes on three mutually exclusive incident classes while preserving clean memories.
- `C5 Systems`: under controlled budgets, CMD compares favorably with MemSkill, ERSkill, Mem0 OSS, and retrieval baselines through one shared repair-action interface.

The paper MUST NOT describe router posterior updates alone as skill evolution. It MUST NOT describe a growing number of skills alone as ecological evolution.

## 2. Non-goals

This specification does not claim:

- that LLM weights are trained or self-modified;
- that ordinary memory retrieval and repair-skill routing are the same problem;
- that one scalar QA score is sufficient to establish safe repair;
- that an internally generated dataset alone establishes external validity;
- that a hosted commercial memory service is equivalent to its open-source SDK;
- that a frozen cross-model transfer experiment proves online self-evolution;
- that a visualization, cluster plot, or reduced selection entropy proves a niche;
- that model families can be averaged into one undifferentiated score.

## 3. System architecture

### 3.1 Online repair path

```text
immutable event stream / current memory state
  -> MemAudit and structural telemetry
  -> EccSyndrome: incident type, root, descriptor, confidence
  -> legal-action mask
  -> retrieve compatible skills from SkillRegistry
  -> Mix GHOST selects one legal skill/operator
  -> selected skill is exposed to the repair executor only
  -> shadow or copy-on-write execution
  -> root / invariant / safety / locality ECC checks
  -> commit or rollback
  -> settled EccRepairReceipt
  -> FailureMemory, quarantine, lineage, and router-register update
```

### 3.2 Slow skill-learning path

```text
settled receipts grouped by family and lineage
  -> pattern discovery
  -> typed OperatorSpec proposal
  -> compile and static validation
  -> replay on discovery data
  -> shadow validation on held-out families
  -> deduplicate or create a new version
  -> probationary skill
  -> active / quarantined / retired lifecycle transition
```

### 3.3 Separation of responsibilities

- `EccSyndrome` diagnoses; it does not choose the repair.
- `SkillRegistry` stores typed procedural knowledge; it does not contain ordinary user facts.
- `Mix GHOST` selects among currently legal candidates; it does not generate skills and does not mutate memory directly.
- `RepairExecutor` applies the selected operator in shadow or copy-on-write state.
- `ECC/CAS` decides whether a result is committable.
- `EcologyManager` proposes and governs skill lifecycle changes from settled evidence.
- The LLM MUST NOT receive the full router parameter state. It receives at most the selected skill and the evidence needed to execute it.

## 4. Core data contracts

### 4.1 Immutable event

```text
EventRecord {
  event_id
  family_id
  lineage_id
  timestamp
  source_domain
  source_uri_or_dataset_id
  actor_scope
  payload
  payload_sha256
  previous_event_sha256
  provenance
}
```

### 4.2 Syndrome

```text
EccSyndrome {
  syndrome_id
  event_id
  incident_type: process_fault | state_drift | poison | clean | unknown
  root_candidates
  behavior_descriptor
  failure_surface
  structural_signature
  safety_regime
  locality_regime
  confidence
  evidence_refs
}
```

The three repairable incident types MUST be mutually exclusive at the decision boundary. Ambiguous cases MUST map to `unknown` or a predefined abstention path; test labels MUST NOT be used to force a branch.

### 4.3 Typed operator

```text
OperatorSpec {
  operator_id
  version
  incident_type
  preconditions
  read_set
  write_set
  repair_action
  forbidden_effects
  required_evidence
  invariant_checks
  safety_checks
  locality_bound
  rollback_action
  deterministic_components
  llm_required_components
}
```

Qwen2.5 MAY propose an `OperatorSpec`, but prose alone is not a skill. A candidate enters the library only after schema validation, compilation, replay, provenance verification, safety checks, and held-out shadow validation.

### 4.4 Skill

```text
SkillSpec {
  skill_id
  version
  operator_id
  incident_type
  behavior_descriptor_support
  preconditions
  applicability_scope
  incompatibilities
  supporting_receipt_ids
  parent_skill_ids
  status: proposed | probationary | active | quarantined | retired
  created_at_event
  effective_after_event
  content_sha256
}

SkillEvidenceState {
  skill_id
  valid_after_event
  support_count
  success_summary
  rollback_summary
  safety_summary
  locality_summary
  evidence_state_sha256
}
```

Skill versions are immutable. Revision creates a new version with explicit parents and an `effective_after_event`. Evidence state is versioned separately and is never serialized into transferred skill content. No evaluation event may be routed by a skill version or evidence state created from that event's outcome. Cross-model `skill content only` arms transfer `SkillSpec` objects but reset both Mix GHOST state and `SkillEvidenceState`.

### 4.5 Router decision

```text
RouterDecision {
  decision_id
  event_id
  model_id
  syndrome_id
  candidate_skill_ids
  legal_mask
  router_name
  router_state_hash
  selected_skill_id
  selected_operator_id
  selection_probability_or_null
  uncertainty
  base_scores
  routed_scores
  active_levels
  exploration_activated
  random_addresses
  decision_timestamp
}
```

Every router MUST log enough state to replay the decision exactly. A stochastic baseline MUST additionally log its action propensity. The primary Mix GHOST implementation is deterministic conditional on its frozen seed, event index, posterior snapshot, and candidate set; it logs its score vector and content-addressed Gaussian draws instead of claiming an unavailable closed-form argmax propensity. Consequently, propensity-based off-policy evaluation is not a primary Mix GHOST analysis.

### 4.6 Repair receipt

```text
EccRepairReceipt {
  receipt_id
  decision_id
  event_id
  family_id
  lineage_id
  before_state_hash
  shadow_state_hash
  committed_state_hash
  root_check
  invariant_checks
  safety_checks
  locality_checks
  downstream_task_score
  action_cost
  token_cost
  latency_ms
  outcome: commit | rollback | abstain
  failure_reason
  settled_at_event
  receipt_sha256
}
```

A receipt is eligible for learning only after all delayed outcomes included in the protocol have settled. Right-censored receipts MUST NOT be treated as failures or successes.

## 5. Mix GHOST routing contract

### 5.1 Definition and implementation identity

Mix GHOST is exclusively a routing selection mechanism. The primary implementation in this specification is the observable-backbone, support-gated hierarchical residual router implemented as `ObservableResidualGHOSTRouter`. It combines:

1. a frozen, decision-time observable backbone decision and score for every legal skill;
2. an external global/pattern/local residual parameter memory; and
3. support-gated, content-addressed Gaussian exploration.

It does not train or modify LLM weights. It does not index ordinary user facts. Its parameter memory persists outside the LLM and is keyed by stable skill revisions, pattern revisions, and observable failure features.

`GHOSTEcologyRouter` is the non-residual hierarchical comparator. It starts from optional skill priors and directly adds hierarchical posterior draws. It MUST be reported as `GHOST hierarchy`, not silently merged with the primary `Mix GHOST` arm.

### 5.2 Runtime interface and legal candidates

```text
route(
  failure_features,
  pattern_responsibilities,
  legal_stable_skills,
  sealed_registry,
  event_index,
  backbone_scores,
  backbone_selected_skill,
  residual_parameter_memory
) -> RouterDecision

update(
  previous_residual_parameter_memory,
  RouterDecision,
  matured DelayedOutcomeFeedback
) -> next_residual_parameter_memory
```

The legal-action mask is enforced before this interface. A candidate is present only if it passes syndrome compatibility, typed preconditions, registry state, provenance, safety policy, and quarantine checks. The router MUST receive a non-empty, unique set of `stable` skill revisions contained in a sealed registry. Gold `legal_operator_ids` are evaluator-only and MUST never enter this candidate builder.

The backbone MUST provide one finite score `b_t(a)` for every legal candidate and a selected action `a_t^0` from the same candidate set, or `None` to abstain. The backbone decision and score vector are frozen inputs to the router. Mix GHOST MUST NOT recompute either from evaluator labels.

### 5.3 Hierarchical residual parameter memory

For event `t`, legal skill `a`, pattern responsibilities `rho_t,p`, and sorted observable failure features `x_t,j`, define the weighted keys:

```text
global:   k = (global, a)       with w_t,k = 1
pattern:  k = (pattern, p, a)   with w_t,k = rho_t,p
local:    k = (local, p, j, a)  with w_t,k = rho_t,p * x_t,j / ||x_t||_1
```

Pattern responsibilities MUST be unique, lie in `[0, 1]`, and sum to one within `1e-9`. Zero local features are omitted. If `||x_t||_1 = 0`, all local keys are omitted.

Each key stores a positive precision and a natural parameter:

```text
Lambda_k,0 = 1
eta_k,0    = 0
mu_k,t     = eta_k,t / Lambda_k,t
support_k,t = Lambda_k,t - 1
```

This is a weighted Gaussian residual register with unit prior precision. It is not a Beta-Bernoulli Thompson posterior. Effective support can be fractional because an update adds `w^2` rather than one count.

### 5.4 Support-gated residual score

The default activation thresholds are:

```text
tau_global  = 2
tau_pattern = 4
tau_local   = 8
tau_explore = 4
epsilon     = 0.08
```

These defaults are implementation details, not untunable universal constants. Confirmatory values MUST be selected using development/calibration data and frozen in `F-MG-ALG`.

For level `ell(k)`, define `I_k,t = 1[support_k,t >= tau_ell(k)]`. The support-gated posterior residual is:

```text
R_t(a) = sum_{k in K_t(a)} I_k,t * w_t,k * mu_k,t
```

Let `L_t` be the ordered set of levels having at least one active key for any candidate. If `L_t` is empty and exploration is inactive, Mix GHOST MUST return the observable backbone action exactly, even if independently sorting the supplied base scores would produce a different action. This preserves the backbone's abstention and tie policy at cold start.

### 5.5 Mature-support exploration

Exploration is globally activated only when at least two legal skills satisfy:

```text
support_(global,a),t >= tau_explore
```

For an exploration-supported skill, the router adds zero-mean noise to the global key and to each active pattern/local key:

```text
z_t,k ~ Normal(0, epsilon^2 / Lambda_k,t)

Z_t(a) = sum_{k in K_t(a)} J_t,k * w_t,k * z_t,k
```

where `J_t,k=1` for the global key and `J_t,k=I_t,k` for pattern/local keys. Unsupported skills receive no exploration noise.

Every draw is generated from a deterministic random address hashing at least:

```text
(seed, event_index, key, router_version)
```

Thus independent reruns with the same frozen inputs replay exactly. Changing the event order changes the draws and MUST be treated as a distinct execution seed/order replicate.

### 5.6 Mixed score and selection rule

The routed score is:

```text
q_t(a) = b_t(a) + R_t(a) + Z_t(a)
```

Selection is:

```text
if a_t^0 is None:
    abstain
else if L_t is empty and exploration is inactive:
    choose a_t^0
else:
    choose argmax_a q_t(a)
```

Ties are broken by ascending stable `skill_revision_id`. The decision MUST record one of:

- `observable_fallback`: neither residual nor exploration is active;
- `residual_supported`: residual/exploration is active but the backbone action remains selected;
- `residual_override`: a posterior residual changes the action;
- `exploration_override`: the source-compatible mode string meaning that exploration is active and the final action differs from the backbone.

The mode name does not establish that noise caused the override; the residual alone may already have changed the action. A causal exploration diagnostic MUST additionally recompute the same decision with `Z_t(a)=0` and report whether noise changes the argmax. The implementation term “mix” means additive mixing of a model-dependent observable backbone and model-independent external residual memory. It does not mean a learned softmax gate over unnamed experts.

### 5.7 Selected-only residual update

Only the selected skill may update. Let `u_t^delay` be the matured delayed utility and `u_t^pre` the frozen pre-action utility prediction bound to that decision. Define:

```text
u_t^effective = -1,
  if feedback is invalid, rolled back, or has delayed regression;
u_t^effective = u_t^delay,
  otherwise.

r_t = clip(u_t^effective - u_t^pre, -1, 1)
```

Before any coordinate changes, read the selected skill's parent means from the pre-update snapshot:

```text
y_global       = r_t
y_pattern(p)   = r_t - mu_(global,a),t
y_local(p,j)   = r_t - mu_(global,a),t - mu_(pattern,p,a),t
```

For every selected-skill key:

```text
Lambda_k,t+1 = Lambda_k,t + w_t,k^2
eta_k,t+1    = eta_k,t    + w_t,k * y_k
```

All unselected-skill coordinates remain unchanged. Gold-derived feedback is forbidden. Evaluation-only feedback is logged but MUST NOT update state. A feedback record is consumed at most once and MUST bind the original selection ID, selected skill revision, event index, and registered success probe.

The settlement adapter MUST additionally verify `u_t^pre` against the frozen decision-time backbone prediction; the current router type validates the field range and bindings but does not itself recompute this equality. Confirmatory execution is blocked until this adapter check has a regression test.

### 5.8 Router state, transfer, and replay

The decision state consists of the frozen configuration and the map:

```text
key -> (Lambda_k, eta_k)
```

Snapshots MUST use a closed schema, canonical ordering, schema version, and content hash. Import MUST reject repeated keys, non-positive precision, schema mismatch, or hash mismatch. Operational counters such as fallback rate are diagnostics and are not part of the action policy.

The current residual state has no `model_id` coordinate. Cross-model transfer therefore means reusing the exact source residual snapshot with the same skill/pattern identities while recomputing the observable backbone scores on the target model. A model-conditioned variant is a separate algorithm and MUST NOT replace this arm without a new freeze ID.

### 5.9 Source-equivalence anchors

This algorithm block was extracted from source and behavior tests, not from result artifacts:

| Role | Source anchor | Extraction SHA-256 |
|---|---|---|
| primary selection and update | `cmd_audit/repair/ghost_ecology.py::ObservableResidualGHOSTRouter` | `00134d5206fbab973c228f6553f5cefd6f9c2241fdf14e7627e745f79c9bcb28` |
| receipt acceptance and telemetry reward | `cmd_audit/repair/ecc.py::EccRepairReceipt` | `b35077d452cd19eb4c8d13e06d33aec9881946ed767f737294c005b0c1460c41` |
| cold start, gates, exploration, replay, selected-only feedback | `tests/repair/test_ghost_ecology.py` | `e088b007728b7758a732a06bae7236a862d4640c6a7d7bc7d73be7ecbb9c33be` |

These hashes document what was inspected for this draft. `F-MG-ALG` MUST be rebound if any source or behavioral test changes before preregistration.

### 5.10 Router invariants

Mix GHOST MUST:

- receive only legal candidates from the frozen mask builder;
- reject unsealed registries, duplicate candidates, non-stable skills, and incomplete base-score maps;
- log the complete ranked score vector, selected action, active levels, exploration state, and pre-update posterior hash;
- update only from matured typed feedback derived from receipts/windows available before the next decision;
- preserve an append-only register history;
- support reset, freeze, export, import, and exact replay;
- remain independent from hidden test labels and oracle rewards;
- not generate, revise, merge, quarantine, or retire skills;
- not bypass ECC/CAS commit decisions.

### 5.11 ECC receipt utility versus Mix GHOST residual reward

The immediate ECC receipt utility is:

```text
failed acceptance, rollback, unresolved syndrome, invariant failure,
safety violation, or post-commit recurrence: -1

accepted safe commit: clip(1 - locality_cost, -1, 1)
```

The primary Mix GHOST update target is not this absolute utility by itself. It is the matured utility residual `r_t` in Section 5.7 relative to the frozen pre-action prediction. Maturity horizon, delayed-regression definition, utility components, and any compute penalty MUST be frozen in `F-REWARD`. The paper MUST report success, rollback, false commit, safety, locality, recurrence, latency, tokens, and scalar residual separately; no scalar reward may hide a safety regression.

## 6. Incident-specific repair semantics

### 6.1 Process fault

Examples include dropped, duplicated, reordered, truncated, misindexed, wrongly scoped, or stale-cache operations. The repair path may patch or replay a pipeline operation. Success requires restoring the intended state without altering unrelated memory.

### 6.2 State drift

An earlier state is no longer current because later evidence supersedes it directly or implicitly. The repair path MUST preserve the historical record, create a supersession edge, update current-state projection, and retain lineage.

### 6.3 Poison

An untrusted or malicious memory attempts to persist, influence later behavior, or cross an authority boundary. The repair path SHOULD quarantine rather than destructively erase the event, preserve audit evidence, block unsafe retrieval or execution, and verify benign-memory preservation.

### 6.4 Clean and unknown controls

Clean cases are required to measure false repair. Unknown or ambiguous cases are required to evaluate abstention and decoder calibration. Neither may be silently excluded from headline denominators.

## 7. Skill ecology contract

### 7.1 Niche definition

Niches MUST be defined before observing evaluation outcomes:

```text
niche = incident_type
      x failure_surface
      x structural_signature
      x safety_regime
      x locality_regime
```

The descriptor MUST be computable at decision time and MUST NOT contain gold labels, oracle operator IDs, downstream answers, or post-repair outcomes.

### 7.2 Lifecycle

- `birth`: a candidate is proposed from multiple settled receipts and passes compilation and replay.
- `probation`: the candidate runs only in shadow and cannot commit.
- `activation`: a candidate passes frozen support, safety, and held-out utility thresholds.
- `revision`: a new immutable version is created; the parent remains auditable.
- `quarantine`: a skill is temporarily ineligible after a safety, provenance, or integrity trigger.
- `retirement`: a skill is no longer selectable after a frozen evidence rule.

Thresholds MUST be selected on development or calibration data. Evaluation results MUST NOT trigger threshold changes.

### 7.3 Evidence required for ecological evolution

Claim `C3 Ecology` requires all of the following:

1. `specialization`: a positive held-out `skill x niche` interaction relative to global and random-key controls;
2. `causal descriptor value`: descriptor permutation or niche-label swapping materially reduces the advantage;
3. `population change`: birth, revision, or retirement changes the active skill population, not only router weights;
4. `regime adaptation`: after a preregistered regime shift, the evolving population recovers faster than a frozen library;
5. `transfer boundary`: same-niche transfer succeeds more often than cross-niche use, or the system correctly abstains;
6. `anchor improvement`: gains appear on a locked anchor set unavailable to the evolution loop.

Skill count, t-SNE clusters, occupancy, entropy, or turnover MAY be reported as descriptive evidence but are insufficient by themselves.

## 8. Dataset program

### 8.1 Public dataset roles

Public datasets are assigned roles rather than pooled into one aggregate benchmark.

| Dataset | Primary role | Incident or claim | Use in discovery? |
|---|---|---|---|
| MemTraceBench | execution-graph attribution and root localization | process fault | No; sealed external |
| MemFail | summarization, storage, and retrieval diagnostics | process fault | No; sealed external |
| HaluMem | extraction, update, QA hallucination propagation | process fault / accumulated memory error | No; sealed external |
| STALE | implicit conflict and outdated-state behavior | state drift | No; sealed external |
| MemSecBench | Write-Execute-Forget and selective repair | poison | No; sealed external |
| MemEvoBench | biased-feedback memory misevolution | harmful evolution / poison | No; sealed external |
| LoCoMo | long-conversation memory QA | end-to-end external validity | No; sealed external |
| LongMemEval | long-term interactive memory and knowledge update | end-to-end / state update | No; sealed external |
| Evo-Memory | streaming test-time memory adaptation | self-evolution protocol transfer | Optional sealed auxiliary |
| Evo-Bench | frozen harness evolution protocol | protocol reference, not primary data | No |

If an official validation split is used for baseline configuration, that split MUST be named and removed from final scoring. Otherwise the entire public benchmark remains sealed.

### 8.2 Need for CMD-RepairStream

A custom controlled stream is required because no listed public benchmark jointly provides:

- immutable paired clean and corrupted event histories;
- mutually exclusive incident labels;
- root-cause and legal-action annotations;
- complete shadow outcomes for candidate operators;
- invariant, safety, locality, and rollback ground truth;
- skill lifecycle events and delayed receipts;
- controlled regime shifts and model transitions;
- sufficient sequential router decisions.

The custom contribution SHOULD be framed as an intervention and repair overlay on realistic source material, not as a replacement for public semantic QA benchmarks.

### 8.3 CMD-RepairStream case schema

```text
RepairCase {
  case_id
  source_dataset_or_domain
  family_id
  lineage_id
  clean_event_ids
  corruption_event_ids
  decision_view {
    immutable_events
    current_memory_state
    observable_telemetry
    predicted_syndrome
  }
  evaluator_only {
    gold_incident_type
    corruption_family
    corruption_template_id
    root_ground_truth
    gold_legal_operator_ids
    expected_state_constraints
    invariant_oracle
    safety_oracle
    locality_oracle
  }
  downstream_queries
  regime_id
  release_split
}
```

Runtime components may deserialize only `decision_view`. The evaluator opens `evaluator_only` after the decision or inside an isolated scoring process. The predicted syndrome drives the candidate builder; the gold incident type and gold legal operators are used only for classification and oracle scoring.

### 8.4 Fault constructors

The initial constructor families SHOULD include:

- process: drop, duplicate, reorder, truncation, wrong index, stale cache, wrong scope, failed partial write;
- state: explicit supersession, implicit invalidation, dependent-state invalidation, conflicting evidence, delayed update;
- poison: memory injection, malicious instruction persistence, noisy tool result, biased feedback, sleeper trigger, authority or scope crossing;
- clean: matched uncorrupted controls and benign updates that resemble faults.

At least one constructor family per incident type MUST be held out entirely from discovery and router accumulation.

### 8.5 Validation and release

- Every generated case MUST pass deterministic structural validation.
- A stratified sample from every constructor and domain MUST receive human review.
- Implicit state-drift and safety cases SHOULD receive expert review because deterministic provenance does not establish semantic supersession or harmlessness.
- Generator code, template versions, model prompts, source licenses, hashes, and rejected-case counts MUST be released.
- Generated paraphrases from the same semantic template remain one family.

## 9. Split protocol

### 9.1 Family-blocked partition

The internal CMD-RepairStream MUST be partitioned by family and lineage:

| Partition | Target proportion | Permitted updates |
|---|---:|---|
| `D_skill` | 35% | skill discovery, compilation, lifecycle-rule development, router implementation development |
| `D_router` | 20% | router parameter accumulation only; skill library frozen |
| `D_cal` | 10% | commit, rollback, abstention, reward, score calibration, retrieval top-k, context budget, and maturity thresholds only |
| `D_lifecycle` | 5% | time-respecting held-out admission/retirement validation; no proposal generation |
| `T_online` | 15% | prequential score first; then protocol-permitted online update |
| `T_anchor` | 5% | no updates; results hidden from all learning loops |
| `T_final` | 10% | fully sealed, single confirmatory evaluation |

The percentages are starting targets. A preregistered power analysis MAY adjust them before any confirmatory result is observed.

### 9.2 Blocking keys

No split may share any of the following:

- original conversation, user, entity, task, repository, or scenario;
- clean, corrupted, repaired, or paraphrased variants from one lineage;
- semantic generation template;
- attack trigger template or payload family;
- source episode from which multiple queries were derived;
- near-duplicate identified by frozen lexical and embedding checks.

### 9.3 IID and OOD evaluation

`T_final` MUST contain both:

- held-out families drawn from known constructor classes; and
- compositional or constructor-family OOD cases not present in `D_skill`, `D_router`, or `D_cal`.

Results MUST be separated into IID-family, unseen-family, and unseen-constructor blocks.

### 9.4 Target-prefix adaptation

For each target model, target-prefix adaptation uses a dedicated set of families disjoint from scored evaluation families.

- Prefix size: preregistered between 10% and 20% of the target allocation.
- During the prefix, only router parameter memory may update.
- New skill generation, decoder changes, prompt edits, threshold changes, and invariant edits are prohibited.
- After the prefix, the adapted router is frozen for the primary suffix score.
- A separate prequential-online arm MAY continue updating during the suffix but must be labeled accordingly.
- Every arm starts from a cloned identical memory snapshot.

### 9.5 Delayed-feedback schedule

At event index `t`, the runner MUST first settle every selected-action outcome whose frozen `observed_after_event_index <= t` and whose selection occurred before `t`. It then snapshots the posterior and makes decision `t`. Outcomes selected at `t` cannot affect that decision. Multiple receipts maturing at the same index are applied in ascending `(selected_at_event_index, selection_id)` order.

The maturity horizon and delayed-regression window are frozen in `F-REWARD`. Pending outcomes at the scored horizon are right-censored for residual updates and reported with their count and age. A post-horizon flush MAY measure durability, but its updates cannot alter any scored decision.

## 10. Model protocol

### 10.1 Discovery model

The default source model is a fixed Qwen2.5-14B-Instruct checkpoint. Qwen2.5-7B-Instruct MAY be included as a source-capacity ablation.

The following MUST be frozen:

- exact model repository and revision or file hashes;
- serving engine and quantization;
- decoding parameters;
- system and task prompts;
- tool schema;
- context and output token limits;
- retry and timeout policy.

### 10.2 Target models

The confirmatory target set SHOULD include:

- a Qwen3 checkpoint;
- a similarly sized Llama 3.1 Instruct checkpoint;
- one fixed closed-model API snapshot, initially GPT-4o if the declared snapshot remains available.

Open-model comparisons SHOULD use similar parameter scale and the same reasoning-mode and token budgets. Closed and open models MUST be reported separately and MUST NOT be averaged into one headline mean.

### 10.3 What transfers

Cross-model experiments transfer:

- the typed skill library;
- skill IDs and versions;
- the syndrome decoder;
- the legal-action mask;
- the Mix GHOST parameter memory for the corresponding arm;
- frozen thresholds, embeddings, reranker, and prompts.

They do not transfer target outcomes, target prefixes, target-specific skill revisions, or manually selected target configurations.

## 11. Experimental stages

### Stage 0: Preregistration and data validation

Before model runs:

1. freeze dataset manifests, family blocks, and checksums;
2. freeze incident definitions and constructor families;
3. freeze primary endpoints and claim gates;
4. freeze the exact Mix GHOST algorithm block;
5. freeze baseline eligibility and budgets;
6. run leakage, duplicate, and schema validation;
7. create the empty result schema before scores exist.

### Stage 1: Qwen2.5 discovery

On `D_skill`, Qwen2.5 may perform:

- incident diagnosis development;
- candidate `OperatorSpec` generation;
- compilation and static rejection;
- replay and shadow validation;
- skill deduplication, revision, and retirement-rule development;
- Mix GHOST implementation development;
- FailureMemory and quarantine-rule development.

Recommended provisional budget:

- 80,000 to 100,000 router decisions across discovery and router accumulation;
- 10,000 to 15,000 family-blocked development or validation decisions;
- at least three, preferably five, preregistered event orders and random seeds.

These numbers are interaction budgets, not independent sample counts. Reports MUST include unique family count, domain count, constructor count, and decisions per family.

### Stage 2: Router accumulation

On `D_router`:

- freeze the skill library;
- freeze the syndrome decoder and legal mask;
- allow only Mix GHOST parameter-memory updates;
- accumulate the source residual-register state;
- record all decisions, base/routed scores, random addresses, feedback bindings, and posterior hashes;
- prohibit skill birth, merge, revision, quarantine-rule edits, or threshold edits.

This stage isolates external parameter learning from skill discovery.

### Stage 3: Calibration and freeze

On `D_cal`, select and freeze:

- commit/rollback thresholds;
- abstention threshold;
- reward coefficients;
- calibration maps;
- retrieval top-k;
- context budget;
- delayed-receipt maturity rules.

Produce `frozen_artifact_manifest` containing hashes for every transferred object and configuration. No object in the manifest may be modified in response to `T_online`, `T_anchor`, `T_final`, or external benchmark results.

### Stage 4: Counterfactual shadow matrix

For router-identification experiments, each case SHOULD execute every legal operator in isolated shadow state to produce a full counterfactual reward matrix.

During online simulation:

- the router sees only the receipt for its selected action;
- unselected outcomes remain hidden;
- the oracle and regret scorer may use the full matrix after the run;
- all operator executions use matched model, seed, prompt, and budget settings;
- an operator that exceeds budget is scored under the frozen budget policy.

For operators with stochastic LLM components, each legal action is executed under the same preregistered common-random-number seed list. The number of repeats is selected on `D_cal` and frozen. The candidate-set oracle maximizes mean evaluator utility over exactly the candidates visible after the decision-time legal mask. A library oracle may range over every type-compatible stable skill before retrieval truncation, and a mechanism oracle may range over hidden gold-legal operators; both are diagnostic upper bounds and MUST be named separately.

If full shadow execution is infeasible, action selection MUST be randomized with logged propensity and evaluated using a preregistered IPS or doubly robust estimator. Observational chosen-action logs alone cannot support oracle-regret claims.

### Stage 5: Router isolation experiment

Freeze the skill library, decoder, legal mask, executor, reward, and ECC gates. Compare:

1. random legal;
2. best-global static policy selected on development data;
3. global Thompson sampling;
4. descriptor or niche-keyed Thompson sampling;
5. a preregistered contextual-bandit baseline;
6. GHOST hierarchy without the observable residual backbone;
7. Mix GHOST;
8. oracle legal operator.

For C1, every adaptive router starts from its declared empty/default prior on the same target-model stream and receives the same selected-action feedback opportunities, maturity schedule, update count, and compute budget. Static policies receive no online updates. No Stage 5 arm may import the Qwen2.5 source residual snapshot; source-state transfer is evaluated only in Stage 8. Each event order starts from a fresh cloned state for every arm.

Primary endpoint: family-macro normalized cumulative safe regret.

Secondary endpoints: safe utility, adaptation AUC, calibration, false commit, action coverage, tail risk, and compute cost.

### Stage 6: Ecology isolation experiment

The primary ecology estimand uses a fixed Mix GHOST algorithm, fixed safety gate, and one common residual snapshot frozen for the entire primary Stage 6 stream. Residual updates are disabled. New skills begin with zero residual coordinates and are evaluated through the same frozen observable backbone. Compare:

1. no skill;
2. seed library frozen;
3. add-only skills;
4. add plus deduplication;
5. add plus revision;
6. add plus revision plus retirement;
7. full ecology with predefined niche descriptors;
8. random-key capacity-matched ecology;
9. oracle library or oracle operator upper bound.

`T_online` is scored prequentially: each event is scored before its outcome may update the population. Candidate admission or retirement must first pass the frozen rule on a once-consumed, past-only `D_lifecycle` family batch; a consumed family can never validate a later candidate. Batch assignment is hashed before proposals exist. The event that proposed a skill and all descendants of its lineage are ineligible for validation. `T_anchor` is evaluated at preregistered checkpoints through the same lockbox access policy as `T_final`; checkpoint scores are not returned to the evolution loop or configuration owner. The final population is frozen before `T_final`.

Specialization is estimated on future held-out families using either the full shadow matrix or a preregistered randomized legal assignment. The niche descriptor is cross-fitted from past data and frozen before those future outcomes. The primary interaction compares true descriptors with random-key, descriptor-permuted, and capacity-matched controls; chosen-action logs alone do not establish specialization.

A secondary `coupled adaptation` arm MAY allow both lifecycle transitions and Mix GHOST residual updates between scored events. It estimates end-to-end co-adaptation and MUST NOT replace the primary ecology-isolation result.

### Stage 7: Repair-governance isolation experiment

Freeze the Mix GHOST algorithm and residual snapshot, skill population, syndrome decoder, candidate builder, and executor inputs. Vary only repair governance. Compare:

1. detection only;
2. direct in-place repair;
3. copy-on-write without ECC;
4. ECC without CAS or atomic commit protection;
5. copy-on-write plus ECC plus CAS;
6. full system with provenance and rollback;
7. oracle repair.

Report process fault, state drift, poison, clean, and unknown separately. A micro-average MAY be secondary only.

All governance arms receive the same preregistered concurrency and attack interleaving schedule. CAS comparisons MUST include a matched benign concurrent-write condition and a conflicting-write condition. Poison arrival time, authority level, trigger time, and evaluator observation time are frozen; an arm cannot receive earlier attacker knowledge than another.

The primary direct-governance estimand runs every case from an independently cloned identical pre-incident snapshot, so prior commits cannot change a later arm's input. A secondary rolling-stream experiment lets each governance policy's commits and rollbacks alter its own future state; this estimates cumulative policy effect and MUST be labeled separately from the direct effect.

### Stage 8: Cross-model parameter-memory transfer

#### Stage 8A: Residual-state transfer given source skills

For every target model, keep the same source skill library and gates and compare:

1. `No repair`;
2. `Random legal`;
3. `Skill content only`: source skills, empty residual and evidence state, no updates;
4. `Reset online`: source skills, empty residual state, prequential residual updates;
5. `Frozen source`: source skills plus exact Qwen2.5 residual snapshot, no target updates;
6. `Niche shuffled`: frozen source coordinates reassigned by a seeded bijection within hierarchy level and support bin;
7. `Mean only`: source `(mu_k, support_k)` with exploration disabled;
8. `Reset-prefix`: empty state adapted only on the target prefix, then frozen;
9. `Source-prefix`: frozen source state adapted only on the same target prefix, then frozen;
10. `Oracle legal operator`.

Interpretation contract:

- `Skill content only > Random legal` supports value in the frozen observable backbone over transferred procedural content, not parameter-memory transfer.
- `Frozen source > Skill content only` supports residual parameter-memory transfer.
- `Reset online > Skill content only` supports target-side online residual learning.
- `Reset-prefix > Skill content only` measures target-prefix learning from scratch.
- `Source-prefix > Frozen source` measures incremental target calibration after source transfer.
- `Frozen source > Niche shuffled` supports alignment between parameter memory and descriptor/niche rather than generic optimism.

All non-oracle routing arms use identical target-model backbone scores. Prefix families are disjoint from suffix families and are never included in scored suffix estimands.

This stage supports transfer of residual parameter memory conditional on the source skill stack. It does not by itself prove that discovered skill content transfers.

#### Stage 8B: Procedural skill-content transfer

With residual state and `SkillEvidenceState` reset in every arm, compare:

1. `Seed only`: preregistered generic operator seed library available before Qwen2.5 discovery;
2. `Source skills`: the frozen Qwen2.5-discovered skill library;
3. `Target-native skills`: skills discovered on a separate target-development family block with the same family, model-call, token, and validation budget;
4. `Oracle library`: evaluator-only upper bound.

The target-development block is distinct from the router-adaptation prefix and all scored families. `Source skills > Seed only` supports procedural-content transfer; comparison with `Target-native skills` measures the transfer gap. Without this factorial, the paper MUST scope “model-agnostic” to the typed interface and residual router, not claim empirical cross-model skill transfer.

### Stage 9: Industry-system comparison

#### Track A: Controlled-stack

This is the primary systems comparison. All systems receive matched raw event streams, reader or agent models, scoring, and budgets.

Minimum systems:

- full context, where feasible;
- BM25 or simple RAG;
- MemSkill;
- ERSkill;
- Mem0 OSS;
- CMD full;
- CMD without Mix GHOST;
- CMD without ecology;
- CMD without ECC/CAS;
- oracle repair.

Controlled fields:

- identical reader/agent model and exact revision;
- identical input events and ordering;
- matched context-token ceiling;
- matched maximum LLM calls;
- matched latency, token, or monetary budget policy;
- identical downstream scorer and safety/locality oracle;
- identical event-level and family-level denominators;
- configurations selected only on development data.

Offline indexing, consolidation, and policy-fitting work is part of the resource ledger. Report both end-to-end cost and amortized cost at preregistered stream lengths. No system may perform an evaluation-specific offline pass over future events. If a defining baseline requires periodic offline consolidation, it receives the same wall-clock or monetary envelope and the schedule is disclosed rather than silently omitted.

Each memory system retains its defining storage and retrieval mechanism. Where a system permits configurable embeddings or rerankers without changing its intended algorithm, the common components SHOULD be used. Where it does not, the native component MUST be retained and disclosed rather than silently replaced.

The controlled comparison has two subtracks:

- `A1 Evidence-to-repair`: each system receives the same gold-free event stream. MemSkill and ERSkill produce frozen, split-audited skill evidence; Mem0 produces retrieved memory evidence. One frozen shared head maps that evidence into the legal repair-action space, after which every proposal passes the same COW/ECC/CAS governance. Results MUST be labelled `method + shared repair head`.
- `A2 Primitive repair capability`: a common, non-learning harness presents the same already-triggered incident snapshot and requests a declared repair goal through each system's public update/delete/forget interface. The harness may translate schemas and enforce budgets, but may not select a target using CMD predictions, add rollback, or synthesize a repair the baseline does not expose. This subtrack measures public repair capability, not autonomous incident detection.

Comparative repair claims come only from `A1`. Primitive capability claims come only from `A2`. An unsupported operation is a capability result, not a zero-quality implementation supplied by CMD.

For primary controlled `A1`, every system has the same data entitlement:

| Phase | Common entitlement | Prohibited privilege |
|---|---|---|
| configuration | the same `D_skill` families and scorer calls within a frozen tuning budget | public-test or `T_*` outcomes |
| historical fitting | the same ordered `D_skill + D_router` raw events and native observable feedback | hidden incident labels, gold operators, or unselected shadow outcomes unless supplied to every arm |
| calibration | the same `D_cal` families and calibration budget | changing model/system code after calibration |
| evaluation | score-first `T_*` stream under the same update schedule | future events or evaluator-only fields |

Systems may consume the common entitlement only through their declared native learning/consolidation interfaces. Inability to exploit a data phase is reported as a system property. CMD may not import a residual snapshot or skill learned from events that were withheld from competitors in the primary C5 comparison. A supplemental native-supervision track may preserve unequal official recipes, but it cannot pass C5.

#### Track B: Native-task context

Official native-task results may be cited in a separate context table. They are not Stage 9 repair results and cannot establish isolated algorithmic superiority on CMD-RepairStream.

Report for every system:

- repository and exact commit or package hash;
- model provider and exact model ID;
- embedding, reranker, vector store, and persistence layer;
- top-k and context limits;
- number of LLM and embedding calls;
- token use, latency, storage, and cost;
- whether offline consolidation is used;
- whether memory mutation, rollback, or provenance is natively supported.

Mem0 MUST use a pinned open-source implementation for controlled comparisons. Hosted-platform results containing undisclosed proprietary optimizations MUST NOT be merged with local OSS results.

#### Baseline fairness rules

- Competitor evidence generators MUST NOT use CMD diagnosis, routing, evaluator fields, or receipts. The shared head and governance are frozen common evaluation components and MUST be identical for every controlled arm.
- The strongest baseline configuration is selected on `D_skill`/development data and frozen.
- Failed or unsupported baseline operations remain in the denominator.
- Full-context results are a non-budget-matched reference when the context exceeds the common ceiling and must be labeled accordingly.
- Published marketing or paper numbers are contextual references, not experimental baselines.

## 12. Primary and secondary metrics

### 12.1 Router metrics

Define the evaluator utility, distinct from the residual update target, as:

```text
U_t(a) = -1,  for false commit or safety violation
       =  0,  for safe rollback, justified abstention, or no repair
       = clip(1 - lambda_L * locality_cost
                - lambda_C * normalized_compute_cost, 0, 1),
          for a safe successful commit
```

`lambda_L` and `lambda_C` are frozen in `F-REWARD`. Abstention is included as a legal action. With a full shadow matrix, let `a_t* = argmax_{a in A_t} U_t(a)` and define:

```text
NormalizedSafeRegret_t = (U_t(a_t*) - U_t(a_t)) / 2
FamilyMacroNSR = mean_f [ mean_{t in f} NormalizedSafeRegret_t ]
```

The primary C1 estimand is:

```text
Delta_C1 = FamilyMacroNSR_strongest_baseline - FamilyMacroNSR_MixGHOST
```

over the preregistered horizon. Positive is better for Mix GHOST. Ties in oracle utility are all optimal.

Also report:

- family-macro evaluator utility;
- recovery AUC after regime shift;
- adaptation half-life;
- Brier score and expected calibration error only for a score-to-success calibration map frozen on `D_cal`;
- CVaR or lower-tail utility;
- illegal-candidate rejection count by mask reason and any post-mask legality violation;
- tokens, calls, latency, and storage for parameter memory.

Recovery AUC is the mean utility over the first `H_shift` scored events after a frozen change point, relative to the pre-shift frozen-library baseline. Adaptation half-life is the first event where a preregistered smoothed curve recovers half of the gap between immediate post-shift utility and its stationary target; unresolved recovery is right-censored, not set to the horizon.

### 12.2 Ecology metrics

- locked-anchor utility by checkpoint;
- `skill x niche` interaction effect;
- same-niche versus cross-niche transfer gap;
- skill birth, activation, revision, quarantine, and retirement counts;
- active population turnover and survival duration;
- descriptor-permutation performance drop;
- niche occupancy and coverage;
- catastrophic forgetting on previously served niches.

### 12.3 Repair metrics

```text
SafeRepairSuccess = root corrected
                 AND all required invariants pass
                 AND safety passes
                 AND locality passes
                 AND outcome == commit

FalseCommit = outcome == commit
          AND any required root/invariant/safety/locality condition fails

CleanFalseRepair = clean case
                AND committed memory mutation occurs

CleanPreservation = clean case
                 AND no committed mutation
                 AND downstream utility is within the frozen non-inferiority margin

UnknownSelectiveRisk = erroneous commit on an unknown case
                     / non-abstained unknown cases
```

When every unknown case is abstained, `UnknownSelectiveRisk` is reported as undefined with coverage zero, not as zero risk. Report the risk-coverage curve and the preregistered operating point.

Also report:

- incident classification macro-F1 and per-class F1;
- open-set or unknown detection;
- root-localization accuracy;
- invariant pass rate;
- safety violation rate;
- locality or collateral-mutation rate;
- rollback success and rollback durability;
- receipt validity and hash-chain integrity;
- downstream QA or task success;
- poison attack success and selective benign preservation;
- abstention coverage and selective risk.

### 12.4 Reporting granularity

Every primary metric MUST be reported:

- separately for process fault, state drift, poison, clean, and unknown;
- by source dataset and domain;
- by model;
- by seen family, unseen family, and unseen constructor;
- as family-macro and event-micro values;
- with cost and safety metrics adjacent to utility.

### 12.5 Primary estimand registry

The following are the only headline estimands unless `F-EVAL` is versioned before unsealing:

| Claim | Primary estimand | Comparator and scope |
|---|---|---|
| `C1 Router` | `NSR_strongest_baseline - NSR_MixGHOST` | Stage 5 streams; positive favors Mix GHOST |
| `C2 Transfer` | `NSR_SkillContentOnly - NSR_FrozenSource` | separately per target model; positive favors residual transfer |
| `C3 Ecology` | locked-anchor utility AUC difference plus held-out `skill x niche` interaction | full ecology versus frozen and add-only populations |
| `C4 Repair` | SafeRepairSuccess risk difference | full governance versus strongest eligible repair baseline, separately for each incident |
| `C5 Systems` | family-macro evaluator utility at matched budget | CMD versus strongest eligible locally executed system in controlled `A1` |

False-commit, collateral-mutation, clean false-repair, and unknown selective-risk margins are gate constraints, not secondary observations that can be traded for utility. Each numeric horizon, margin, and strongest-baseline selection rule is frozen in `F-EVAL`.

## 13. Statistical protocol

### 13.1 Unit of inference

The primary independent unit is the source episode or, when source episodes are unavailable and family independence is justified, the original semantic family. A lineage, query, paraphrase, memory entry, shadow operator execution, repeated seed, or checkpoint is never an independent unit.

### 13.2 Estimation

- For frozen-state static evaluations, use paired source-episode/family-cluster bootstrap confidence intervals.
- For prequential router or ecology evaluations, resample whole independent source-episode/family blocks and replay every compared arm from the same initial snapshot in the resampled event order. Bootstrapping already-produced event rows without replay is prohibited because later decisions depend on earlier feedback.
- Preserve regime boundaries within a replay replicate. Report a robustness analysis that resamples contiguous temporal blocks within each regime.
- Use hierarchical mixed-effects models as a robustness analysis when domains, models, and families are crossed.
- Stream-order seeds are repeated measurements, not independent families.
- Report absolute differences, relative differences where meaningful, confidence intervals, and exact family counts.

### 13.3 Multiple comparisons

The confirmatory hypothesis family MUST be declared before execution. Holm correction MUST be used within each contribution family, with either adjusted p-values or multiplicity-compatible confidence intervals reported consistently. Exploratory comparisons MUST be labeled and cannot replace failed primary hypotheses.

### 13.4 Power

Before confirmatory runs, perform power analysis at the family level using pilot variance from development data. The protocol MUST declare:

- minimum detectable effect for safe repair success;
- minimum detectable reduction in normalized regret;
- minimum detectable locked-anchor ecology gain;
- planned family count per incident and domain;
- expected attrition from invalid or unsettled receipts.

Increasing decisions per family cannot compensate for too few independent families.

## 14. Regime-shift protocol

At least three preregistered stream schedules SHOULD be used:

1. stationary balanced incidents;
2. abrupt shift, such as process-heavy to state-heavy to poison-heavy;
3. recurring shift, such as A to B to A, to distinguish adaptation from irreversible forgetting.

The exact change points and distributions are frozen before execution. A hidden random schedule MAY be included as an OOD robustness test.

Router adaptation and skill-population adaptation MUST be measured separately:

- router state may change with a frozen library in Stage 5;
- skill population may change with a fixed router algorithm and residual snapshot in primary Stage 6;
- the full coupled system is evaluated only after both isolated effects are established.

## 15. Reproducibility and freeze manifest

The `frozen_artifact_manifest` MUST contain:

- data source versions, splits, and checksums;
- constructor and template versions;
- all model IDs, revisions, quantization, and serving details;
- prompt and tool-schema hashes;
- skill-library content hashes;
- operator IDs and versions;
- Mix GHOST algorithm configuration and parameter-state hash;
- decoder and legal-mask hashes;
- embedding and reranker versions;
- reward and threshold values;
- random seeds and event-order manifests;
- baseline commits and adapters;
- scorer and judge prompts;
- environment, dependency, and container hashes;
- result-schema version.

Any post-freeze change creates a new experiment version. It cannot silently overwrite a confirmatory run.

### 15.1 Lockbox operation

`T_final` evaluator labels, outcome matrices, and aggregate scores MUST be encrypted or access-controlled separately from runtime inputs. A designated custodian or one-shot automation may expose only pass/fail schema validation before unsealing. Every access is append-logged. The preregistration MUST state the single execution batch, allowable infrastructure-only retries, retry equivalence test, and unsealing condition. Any model, prompt, threshold, skill, router, adapter, or scorer change after unsealing creates a new explicitly exploratory study version.

## 16. LLM judge and oracle policy

- Deterministic checks SHOULD decide provenance, hashes, CAS, write sets, invariant predicates, and exact-answer conditions where possible.
- LLM judges MAY score semantic conditions not expressible deterministically.
- Judge prompts, model IDs, ordering, and tie policies MUST be frozen.
- A stratified human agreement study MUST cover all incident types and all headline semantic metrics.
- The same unconstrained model output MUST NOT serve as both repair generator and sole safety oracle.
- Oracle legal operators MUST be defined from hidden post-repair constraints or the full shadow outcome matrix, not from the router's own score.

## 17. Paper-facing experiment matrix

The primary paper SHOULD avoid a full Cartesian product. Use orthogonal slices:

| Table/Figure | Fixed components | Varied component | Contribution |
|---|---|---|---|
| Table 1 | Qwen3, frozen skills, full gates | router algorithms | Mix GHOST |
| Figure 2 | same as Table 1 | stream time and regimes | regret/adaptation |
| Table 2 | frozen skills and gates | source/reset/prefix router state across models | parameter-memory transfer |
| Table 3 | Mix GHOST and full gates | skill lifecycle variants | ecology |
| Figure 3 | same as Table 3 | population and anchor checkpoints | niche evolution |
| Table 4 | Mix GHOST and full ecology | governance ablations by incident | safe repair |
| Table 5 | matched models and budgets | MemSkill, ERSkill, Mem0 OSS, CMD | skill-system comparison |
| Appendix | native configurations and complete model matrix | all | deployment context |

Closed-model results appear as separate columns or panels. They are not averaged with open-model results.

## 18. Claim gates

### Gate C1: Mix GHOST routing

Pass only if the multiplicity-corrected lower confidence bound for `Delta_C1` is greater than zero, without increasing false commit beyond the frozen safety margin.

### Gate C2: Parameter-memory transfer

Pass only if the multiplicity-corrected lower confidence bound for `NSR_SkillContentOnly - NSR_FrozenSource` is greater than zero on at least two target model families, `Frozen source` beats the niche-shuffled control, and all target arms meet frozen safety margins. Otherwise describe transfer as model-conditional or negative.

### Gate C3: Skill ecology

Pass only if full ecology beats frozen and add-only libraries on locked-anchor utility, exhibits a positive held-out specialization interaction, fails under descriptor permutation, and improves regime-shift recovery through population change.

### Gate C4: Safe repair

Pass only if the complete governance path improves SafeRepairSuccess over no-repair and the strongest eligible repair baseline without violating the false-commit or collateral-mutation margins, separately for process fault, state drift, and poison. It must also satisfy the clean-memory false-repair non-inferiority margin and the preregistered unknown-case selective-risk bound. Failure in one incident class narrows the claim; it cannot be hidden by a pooled average.

### Gate C5: Industry comparison

Define `Delta_C5 = Utility_CMD - Utility_strongest_eligible_system` in controlled `A1`. Pass only if the multiplicity-corrected lower confidence bound for `Delta_C5` is positive, CMD meets the common token/call/latency or monetary budget, and false commit, safety violation, and clean false repair satisfy their frozen non-inferiority margins. Only locally executed, pinned systems with the common data entitlement are eligible. Native-task results may provide context but cannot establish isolated algorithmic superiority.

## 19. Negative-result policy

- Failed gates remain in the paper if scientifically informative.
- A failed transfer gate cannot be repaired by averaging models.
- A failed ecology gate cannot be replaced by descriptive population plots.
- A failed safety gate cannot be hidden behind average QA improvement.
- Hyperparameters cannot be changed after a failed sealed result without declaring a new study version.
- External benchmark failures remain in denominators and must be explained by evidence, not removed as outliers post hoc.

## 20. Execution order and economical matrix

### Round 1: Development screening

- Qwen2.5-14B for discovery;
- Qwen3 target for all router baselines and ablations;
- development and calibration partitions only;
- eliminate configurations that fail schema, budget, or safety gates;
- do not access sealed benchmarks.

### Round 2: Confirmatory internal study

- run frozen router, ecology, and repair slices on `T_online`, `T_anchor`, and `T_final`;
- use three to five open-model stream orders;
- materialize all raw decisions and receipts;
- decide claim gates without external benchmark tuning.

### Round 3: Cross-model and external study

- Qwen3, similarly sized Llama 3.1, and fixed closed-model snapshot;
- frozen skill and parameter-memory transfer arms;
- MemTraceBench, MemFail, HaluMem, STALE, MemSecBench, MemEvoBench, LoCoMo, and LongMemEval according to their assigned roles;
- no post-result prompt, threshold, or adapter changes.

### Round 4: Systems comparison

- controlled-stack comparison on the sealed common cases;
- native-task results as non-ranked external context;
- complete cost and capability disclosure.

## 21. Required outputs

Every run MUST produce machine-readable outputs sufficient for independent reanalysis:

- dataset and split manifests;
- frozen artifact manifest;
- candidate and legal-mask logs;
- router decisions, base/routed scores, posterior hashes, and random addresses;
- action propensities for stochastic baselines and any off-policy arm;
- shadow outcome matrix or randomized logging propensities;
- receipts and lifecycle transitions;
- cost and latency logs;
- per-family result table;
- bootstrap samples or reproducible bootstrap seeds;
- claim-gate decision file;
- environment and baseline-version report.

## 22. Open decisions that block preregistration

### 22.1 Executable wiring status (2026-08-27)

Stages 5-9 now have a common executable surface over the closed runtime bundle and frozen event-order manifest:

- Stage 5 executes all eight router arms with one shared backbone-prediction cache, independent arm state, exact delayed maturity, frozen `Best global`, and sealed-oracle gating.
- Stage 6 executes all nine ecology arms with typed operator replay gates and `t+1` birth, supersede, quarantine, and retirement semantics.
- Stage 7 executes all seven governance arms through explicit COW, ECC, CAS, rollback, and receipt-provenance component switches.
- Stage 8A/8B names and semantics are bound directly to the frozen experiment matrix, with skill content, evidence state, and residual state transported independently.
- Stage 9 gives CMD ablations distinct capability profiles and replays controlled competitor proposals through common governance; native-task outputs are outside its repair-action contract.

`experiments/spec_v03_stage5_9.py` compiles these outputs into one content-addressed report. A development smoke over public HaluMem data and the deterministic non-model proposal policy completed successfully. This is wiring evidence only: unconfigured model providers, delayed outcome channels, sealed oracles, discovery providers, and industry wrappers remain `UNSUPPORTED`, and no empirical score is inferred from the smoke.

The Mix GHOST equations, priors, hierarchy, support gates, exploration form, update, clipping, and tie-break are defined in Section 5. The following decisions still block confirmatory execution:

1. final source/test hashes and frozen observable-backbone implementation;
2. regression-tested binding from `u_t^pre` to the decision-time backbone prediction;
3. the contextual-bandit baseline and its tuning budget;
4. delayed utility components, maturity horizon, and frozen safety margin;
5. skill birth, activation, quarantine, and retirement thresholds;
6. target model checkpoints and the closed API snapshot;
7. minimum detectable effects and resulting family counts;
8. exact regime schedules and OOD constructor families;
9. the maximum common token, call, latency, and monetary budgets;
10. baseline repository commits and supported adapter interfaces;
11. the human-audit sample size and agreement threshold;
12. lockbox custodian, access log, rerun policy, and unsealing condition.

## 23. Final checklist

Before any result is called confirmatory, verify:

- [ ] Mix GHOST is implemented only as a router.
- [ ] Mix GHOST source and behavior-test hashes match `F-MG-ALG`.
- [ ] Cold start exactly reproduces the observable backbone.
- [ ] Delayed feedback is selected-only and bound to the decision-time prior.
- [ ] Skill discovery and router accumulation use different family blocks.
- [ ] Ordinary memory and the skill registry use separate indexes and schemas.
- [ ] Public benchmarks remain sealed or their official validation use is disclosed.
- [ ] All target-prefix families are disjoint from scored families.
- [ ] Every decision is exactly replayable; every stochastic baseline logs its propensity.
- [ ] Oracle regret uses a full shadow matrix or valid off-policy estimator.
- [ ] Router, ecology, and repair are isolated before full-system comparison.
- [ ] Clean and unknown cases remain in the denominator.
- [ ] Process fault, state drift, and poison are reported separately.
- [ ] Cross-model results are not averaged into one score.
- [ ] Controlled repair results and native-task context are separated.
- [ ] Baselines are pinned and configured using development data only.
- [ ] Safety, locality, cost, and utility appear together.
- [ ] Claim gates are evaluated exactly as preregistered.
- [ ] Negative results are retained and scoped honestly.

## 24. Reference entry points

- MemTraceBench: https://github.com/zjunlp/MemTrace
- MemFail: https://arxiv.org/abs/2605.26667
- HaluMem: https://arxiv.org/abs/2511.03506
- STALE: https://arxiv.org/abs/2605.06527
- MemSecBench: https://arxiv.org/abs/2607.27080
- MemEvoBench: https://arxiv.org/abs/2604.15774
- LoCoMo: https://aclanthology.org/2024.acl-long.747/
- LongMemEval: https://github.com/xiaowu0162/LongMemEval
- Evo-Memory: https://arxiv.org/abs/2511.20857
- Evo-Bench: https://github.com/RUCAIBox/Evo-Bench
- ReasoningBank: https://openreview.net/forum?id=jL7fwchScm
- MetaSkill-Evolve: https://arxiv.org/abs/2607.05297
