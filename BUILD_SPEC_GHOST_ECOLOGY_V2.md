# BUILD SPEC — GHOST Ecology V2

Status: implementation contract
Replaces: `BUILD_SPEC_GHOST_ROUTER_V1.md`
Model calls in runtime core: prohibited

## 1. Objective

GHOST V2 is an open-world, governed ecology over three durable layers:

```text
failure_memory -> pattern -> repair_skill
                         ^
                         | GHOST routing and credit assignment
```

The objective is not to accumulate logs or tune a fixed action list. The system
must sediment reusable repair capability and evolve new patterns and typed repair
programs while retaining replayability, causal feedback boundaries, safety and
sealed evaluation.

## 2. What counts as sedimentation

Sedimentation has four independently auditable levels:

1. **Fact sedimentation** — immutable failure, selection, execution and feedback events.
2. **Structural sedimentation** — recurrent failures produce versioned pattern revisions.
3. **Capability sedimentation** — successful repair programs produce versioned skills.
4. **Transfer sedimentation** — a skill becomes stable only after positive eligible
   evidence on later, non-producing cases from at least two failure families.

More events, larger weights, or self-validation on the producing case do not establish
sedimentation.

## 3. What counts as evolution

At least one lineage-changing event must occur:

- pattern birth, split, merge or retirement;
- skill birth, structural revision, migration or retirement;
- a successful multi-step behavior deposited as a new atomic typed program;
- niche birth, occupation, competition, branching, collapse or recovery.

Posterior updates over an unchanged skill registry are adaptation, not open-world
evolution.

## 4. Immutable event grammar

The repository is a hash-chained append-only JSONL ledger. Registered events are:

- `failure_observed`
- `pattern_revision`
- `pattern_binding`
- `skill_revision`
- `pattern_skill_binding`
- `selection`
- `skill_feedback`
- `posterior_snapshot`
- `lifecycle_transition`
- `niche_snapshot`
- `niche_transition`
- `registry_snapshot`
- `discovery_pressure`

Every event has a strictly increasing event index, content ID, predecessor hash and
event hash. Current state is derived by replay; historical payloads are never rewritten.

## 5. Failure memory

A failure deposit binds:

- external failure-memory content hash;
- case and audit-only family IDs;
- deployment-observable feature vector;
- context and provenance hashes;
- occurrence time/index.

Failure memory is evidence, not a label. It may bind softly to multiple pattern
revisions with responsibilities summing to one.

## 6. Patterns

A pattern revision contains a typed predicate/feature signature, parents, derivation
kind and lifecycle state. Lifecycle:

```text
candidate -> recurring -> validated -> stable -> deprecated
                                  \-> split | merged
```

Birth is triggered by persistent unmatched residual mass. Split is proposed when one
pattern contains repeatable subcontexts with different best skills. Merge is proposed
when two patterns have equivalent feature, skill-ranking and feedback behavior.
Split/merge proposals are candidates; governance, not the router, approves them.

## 7. Repair skills

A skill revision is an arbitrary typed repair program, not an enumeration or Cartesian
combination of legacy actions. It binds:

- program AST and hash;
- typed parameter schema;
- preconditions and postconditions;
- skill-conditioned deployment success probe;
- locality/mutation budget;
- rollback program;
- parent revisions and derivation provenance.

Lifecycle:

```text
proposed -> sandboxed -> shadow_validated -> calibrated -> stable
                                                   \-> retired
stable -> revised | retired
```

Only stable revisions may serve. A new program may inherit priors from parents and
related patterns, but receives its own revision ID and posterior.

## 8. GHOST routing

GHOST estimates:

```text
P(skill success | failure features, soft pattern responsibility, deployment context)
```

The recursive hierarchy is:

```text
global skill prior
-> pattern x skill posterior
-> local feature x pattern x skill posterior
```

Selection is content-addressed and reproducible. Feedback updates only the selected
skill and its responsibility-weighted path. Unselected counterfactual outcomes and
gold-derived scores are forbidden in deployment mode.

## 9. Skill-conditioned observable feedback

There is no universal `changed_item_count > 0 == success` rule. Every stable skill
registers an observable success probe. Examples:

- verify: probes pass and no mutation is required;
- abstain: no delayed recurrence or unsafe escalation;
- replace: target failure resolves without rollback or downstream regression;
- conflict annotation: later routing consumes the annotation and recurrence falls.

The normalized update target is derived from success, locality, execution cost,
rollback and delayed regression. Feedback provenance must declare `gold_derived=false`.

## 10. Niche definition and observation

A niche is not a static semantic label. It is the time-local relation between failure
resource, pattern responsibility, execution constraints and skill fitness.

Each window emits a `NicheSnapshot` containing:

- arrival and responsibility-weighted resource mass;
- unresolved mass and recurrence rate;
- skill occupancy and successful occupancy;
- skill richness, selection entropy and effective species count;
- per-skill deployment fitness and uncertainty;
- dominant skill/share and fitness margin;
- split pressure from context-dependent winner disagreement.

Lifecycle:

```text
latent -> emerging -> occupied -> stable
                         \-> contested -> branching
stable/contested -> collapsing -> extinct
```

Perturbation observation temporarily removes a dominant stable skill in a controlled
development window and records winnerless duration, replacement time, distribution
drift and recovery time. Sealed tests never perform discovery or perturbation.

## 11. Governance separation

The router may select skills, update posterior state and submit discovery pressure.
It may not define success, approve its own pattern/skill, change safety budgets or
promote a revision. Governance derives lifecycle transitions from registered evidence.

The producer case cannot validate its own revision. Stable promotion requires at least
three later successful cases, at least two audit families, no rollback, registered
deployment feedback and anchor non-regression.

## 12. Epoch and evaluation boundary

```text
epoch N: frozen stable registry N -> dev/cal adaptation -> sealed test evaluation only
discovery quarantine: proposals + sandbox + governance
epoch N+1: frozen registry N+1
```

Test feedback cannot update posterior state, create patterns, propose/revise skills,
change success probes or export learned state.

## 13. Acceptance tests

The implementation is accepted only if tests demonstrate:

1. ledger hash-chain replay and immutable collision refusal;
2. soft failure-pattern responsibilities sum to one;
3. arbitrary new typed programs with lineage, not action enumeration;
4. producer evidence excluded and cross-case/family promotion enforced;
5. selected-skill-only deployment updates and gold refusal;
6. deterministic global/pattern/local posterior replay;
7. niche metrics, competition and lifecycle transitions;
8. split/merge proposals do not self-promote;
9. sealed registry/test mode refuses discovery and updates;
10. zero model/API calls in the core and tests.
11. a crash between selection and feedback restores pending credit from ledger replay;
12. unmatched mass, abstention and residual errors create governed discovery pressure.
