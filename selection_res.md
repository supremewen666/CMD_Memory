# Algorithm Selection — Non-Gradient Sparse Repair Experts

## Project Goal

- Task: replace V4's unbounded sparse-counter update with a replayable,
  selected-feedback router over graph-bound typed repair experts.
- Main metrics: prequential utility, harm rate, represented/unseen family utility,
  calibrated coverage, and regret to the frozen candidate oracle.
- Constraints: no base-model weight updates; zero model calls during policy replay;
  selected-action feedback only in the live protocol; deployment-visible features;
  typed legality and transactional mutation guards remain authoritative; stdlib-only
  core; content-addressed snapshots and exact replay.
- Evidence caveat: `SOUL.md`, required by the generic algorithm-selection skill, is
  absent. This selection therefore uses `survey_res.md`, the V4 code audit, and the
  completed zero-call replay as the project contract.

## Decision Criteria

1. Learns from partial online feedback without backpropagation.
2. Shares evidence globally while allowing niche specialization without hard
   last-writer routing.
3. Supports experts that are unavailable on a case because the frozen graph makes
   their action illegal.
4. Represents uncertainty, safe fallback, abstention, birth and retirement.
5. Can be serialized as small sufficient statistics and replayed exactly.
6. Directly addresses observed V4 failures: dead per-intent parameters, exploding
   scores, hard niche switching, dirty free-text strategy IDs, and no exploration.

## Candidate Options

| Route | Core idea | Strengths | Risks | Cost | Basis |
|---|---|---|---|---|---|
| A. Graph-gated hierarchical specialist Thompson router | Legal typed species are sleeping experts; use a global Bayesian linear posterior plus shrinkage-controlled niche residuals and top-1 posterior sampling | Partial-feedback native; uncertainty-aware; exact sufficient-statistic replay; smooth global-to-local transfer | Requires a trustworthy motif identity and calibrated safety layer | Medium | Linear Thompson sampling; Hierarchical Bayesian Bandits; sleeping experts; online clustering of bandits |
| B. Specialist EXP4/CORRAL master | Multiplicative-weights master combines Full, global, Thompson, SGD and abstention policies | Robust to misspecification/nonstationarity; can hedge between qualitatively different routers | Importance-weight variance, policy starvation and more complex proofs; current EXP3 motif result is weak | Medium-high | Sleeping experts and feedback graphs; CORRAL |
| C. DP-means + MAP-Elites router | Create niches online by graph-motif distance and keep elites in each niche; route by nearest centroid/fitness | Open-world expert birth; interpretable ecology; no gradients | Clustering distance is not utility; weak exploration/credit assignment; threshold sensitivity | Medium | DP-means; online clustering; MAP-Elites |
| D. Evolution strategies/CMA-ES over router weights | Population search optimizes router parameters offline | Derivative-free and handles non-smooth objectives | Very sample hungry, poorly matched to selected online feedback, difficult causal attribution | High | Evolution strategies / quality-diversity |

## Chosen Route

### GHOST: Graph-gated Hierarchical Online Specialist Thompson router

This is a sparse external MoE, not a Transformer-layer MoE:

- **expert**: a reusable typed repair species, not a neural FFN;
- **router input**: deployment-visible frozen-graph motif and intent attributes;
- **sparse activation**: only graph-legal candidate species are awake, and one
  repair is executed unless chain governance separately authorizes composition;
- **expert output**: a complete graph-bound state-transition program;
- **learning signal**: only the selected action's post-action observable feedback;
- **parameters**: Bayesian sufficient statistics, not gradients or base-model
  weights.

### Stable expert identity

Delete case-unique `intent_prior`. Define a reusable motif key from canonical,
typed attributes only:

```text
expert_key = H(
  effect,
  relation_class,
  ordering_state,
  actionability_mode,
  target_role,
  replacement_role,
  evidence_provenance_class,
  compiler_version
)
```

Free-text proposer labels remain metadata. They cannot directly create a parameter
key. A new semantic motif becomes a candidate species and inherits the global
prior; it does not start as an unregularized independent expert.

### Action-dependent features

The feature map must include interactions that differ among candidates:

```text
phi(x, a) = [
  effect(a), expert_motif(a),
  graph_features(x) x effect(a),
  graph_features(x) x expert_motif(a),
  actionability(a), provenance(a), cost_features(a)
]
```

Context-only features cannot rank candidates within the same case and are omitted
unless crossed with action attributes.

### Non-gradient posterior update

For a diagonal Bayesian ridge posterior, maintain precision `Lambda` and natural
mean `eta` for each active feature. With clipped selected reward `r_t` and optional
forgetting `rho`:

```text
Lambda_j <- rho * Lambda_j + phi_j^2 / sigma^2
eta_j    <- rho * eta_j    + phi_j * r_t / sigma^2
mu_j      = eta_j / Lambda_j
theta_j  ~ Normal(mu_j, tau^2 / Lambda_j)
```

This is a recursive posterior update, not gradient descent. It naturally stops
increasing confidence when evidence is scarce and provides exploration through
posterior sampling.

### Recursive hierarchical shrinkage instead of hard niche switching

The selected route has three explicit ecological levels:

```text
global population -> semantic niche -> semantic x signal sub-niche
```

At each child level and feature, direct selected-reward evidence is shrunk to the
current parent draw:

```text
alpha_lj = q_lj / (q_lj + kappa_l)
theta_lj = (1 - alpha_lj) * theta_parent,j
           + alpha_lj * sample(eta_lj / q_lj)
```

Cold children therefore equal their live parent exactly; warm children specialize
smoothly. Direct rewards are deposited at all levels. Residual targets are rejected
because a moving parent makes historical residuals stale, and additive parent/child
scores are rejected because they double count evidence. Development replay selected
`semantic_kappa=256` and `signal_kappa=16`; these are frozen before prospective use.

### Sleeping and safe experts

An expert is awake only when:

1. its concrete intent compiles against the frozen graph;
2. its target/actionability contract is legal;
3. its lifecycle is not blocked/retired;
4. its harm upper bound passes the frozen risk limit.

The router samples only among awake experts plus abstention and the frozen legacy
fallback. Thompson exploration does not bypass typed or transactional safety.

### Dynamic niche birth

Use a DP-means-style threshold over canonical graph-motif vectors, not LLM
embeddings, to propose a new niche when no existing centroid is sufficiently close.
New niches begin as probationary residuals with `alpha_z` near zero. Promotion,
merge and retirement require later cross-family evidence. The number of niches is
therefore data-adaptive but governed.

### Two-timescale evolution

- Fast time scale: posterior sufficient-statistic updates after selected outcomes.
- Slow time scale: species/niche birth, promotion, merge and retirement after
  cross-family evidence and safety checks.

This separates statistical learning from ecological governance. Lifecycle state is
not itself a router parameter.

## Fusion Map

| Imported mechanism | Role in GHOST | What CMD adds |
|---|---|---|
| Linear Thompson sampling | uncertainty-aware selected-feedback router | typed repair actions and immutable replay |
| Hierarchical Bayesian bandits | global prior plus niche residual sharing | niches derived from semantic graph motifs and governed lifecycle |
| Sleeping experts | per-case legal expert set and abstention | availability is proved by typed compilation/actionability |
| Online clustering / DP-means | open-world niche birth | canonical graph descriptors, cross-family promotion and rollback-safe retirement |
| MAP-Elites | preserve useful specialist diversity | elites are executable repair species with provenance and transaction guards |
| CORRAL | optional master over GHOST, frozen Full and conservative baseline | fail-safe fallback under distribution shift |
| Expert-choice/load balancing | audit starvation and enforce exploration floors | no forced unsafe execution; safety precedes load balance |

## Innovation Assessment

The ingredients are not individually novel. Thompson sampling, hierarchical
bandits, sleeping experts, online clustering, DP-means, CORRAL and MAP-Elites all
have prior art. The defensible innovation domain is their constrained composition:

1. **Typed sleeping-expert routing.** Expert availability is not a learned top-k
   gate; it is a proof-carrying legality mask from a frozen semantic graph and typed
   state-transition compiler.
2. **Posterior-to-lifecycle separation.** Fast Bayesian routing statistics and slow
   cross-family species governance are deposited separately, avoiding the current
   last-writer niche bug.
3. **Open-world but fail-closed expertise.** New LLM-proposed repairs may create a
   probationary motif/niche, but cannot mutate state until graph compilation and
   governance pass.
4. **Content-addressed non-gradient MoE evolution.** Every posterior update,
   sufficient statistic, expert birth, niche assignment and retirement is an
   immutable replayable event; no opaque checkpoint is required.
5. **Risk-constrained sparse activation.** Posterior utility exploration is
   subordinate to a separately estimated harm bound, abstention and transactional
   rollback.
6. **Counterfactual shadow/live protocol split.** Frozen materializations may screen
   router algorithms with zero model calls, while live claims use only the selected
   deployment-observable feedback channel and preserve the same typed action ABI.

These points are research hypotheses until ablations show that the fusion beats
plain Thompson, plain hierarchical Thompson, static niches, DP-means-only routing,
Full V4, SGD and a CORRAL fallback on sealed represented and unseen streams.

## Rejected Routes

- **Pure learned neural MoE gate**: rejected because it reintroduces backpropagation,
  opaque checkpoints and more data demand without addressing typed legality.
- **Pure MAP-Elites/evolution strategy router**: rejected as the main online update
  because it wastes selected-feedback samples and does not estimate uncertainty.
- **Pure nearest-centroid router**: rejected because semantic proximity does not
  imply repair utility or safety.
- **Plain EXP3 over motifs**: rejected as the main route because the completed
  zero-call replay underperforms Full overall and transfers poorly to unseen
  families.
- **Current hard niche policy**: rejected because 70.5% of cases fall back to
  global, parameters explode, per-intent keys are dead, and niche state is subject
  to last-writer oscillation.

## Fallback Route

- Route: conservative CORRAL/master policy over frozen Full V4, global diagonal
  Thompson, and GHOST, with abstention as an explicit arm.
- Switch condition: GHOST fails the sealed unseen-family non-inferiority gate or its
  harm upper bound exceeds Full. The master preserves a path back to the strongest
  safe base policy instead of committing to one misspecified router.

## Required Ablations

1. Global diagonal Thompson vs recursive hierarchical Thompson.
2. Fixed graph-signature niches vs DP-means dynamic niches.
3. Hard niche switching vs shrinkage `alpha_z`.
4. Free-text strategy keys vs canonical typed motif keys.
5. No lifecycle vs lifecycle governance, holding router predictions fixed.
6. No harm gate vs posterior harm gate, in counterfactual execution only.
7. GHOST vs CORRAL fallback under represented-to-unseen distribution shift.
8. Shadow-gold feedback vs strictly deployment-observable feedback; only the latter
   supports the end-to-end gold-free claim.

## Literature Basis

- Agrawal and Goyal, *Thompson Sampling for Contextual Bandits with Linear
  Payoffs*, ICML 2013: https://proceedings.mlr.press/v28/agrawal13.html
- Hong et al., *Hierarchical Bayesian Bandits*, AISTATS 2022:
  https://proceedings.mlr.press/v151/hong22c.html
- Cortes et al., *Online Learning with Sleeping Experts and Feedback Graphs*, ICML
  2019: https://proceedings.mlr.press/v97/cortes19a.html
- Gentile et al., *Online Clustering of Bandits*, ICML 2014:
  https://proceedings.mlr.press/v32/gentile14.html
- Kulis and Jordan, *Revisiting K-Means: New Algorithms via Bayesian
  Nonparametrics*, ICML 2012: https://icml.cc/2012/papers/291.pdf
- Agarwal et al., *Corralling a Band of Bandit Algorithms*, COLT 2017:
  https://proceedings.mlr.press/v65/agarwal17b.html
- Zhou et al., *Mixture-of-Experts with Expert Choice Routing*, NeurIPS 2022:
  https://proceedings.neurips.cc/paper_files/paper/2022/hash/2f00ecd787b432c1d36f3de9800728eb-Abstract-Conference.html

## Next Step

- Implement `GHOSTRouter` as a new policy version without mutating V4 evidence.
- First output: exact snapshot schema, motif canonicalizer, diagonal posterior,
  hierarchical residual/shrinkage, sleeping legality mask, harm state, and
  zero-call replay arms.
- Acceptance: beat global Thompson on represented utility while meeting Full V4
  non-inferiority on unseen utility and harm; otherwise activate the CORRAL
  fallback route.
