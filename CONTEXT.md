# CMD Domain Context

Domain language for Counterfactual Memory Debugger research. Defines terminology, boundaries, and rules.

## Runtime ECC Contract (authoritative, 2026-08-23)

The live CMD runtime is a gold-free memory-state correction and evolution loop.
Its authoritative dataflow is:

```text
immutable event stream + real memory state
  -> MemAudit signals + structural telemetry
     (CAS / provenance / safety / locality)
  -> MemAuditEccAdapter decodes exactly one EccSyndrome
     -> process_fault      -> pipeline patch proposal
     -> state_drift        -> supersede proposal with lineage
     -> adversarial_poison -> quarantine proposal with audit evidence
  -> execute the selected typed repair on shadow/copy-on-write state
  -> ECC acceptance gate
     (syndrome resolution + parity/invariants + root binding + safety + locality)
     -> pass: commit shadow root
     -> fail: rollback to the exact before root
  -> emit a root-bound EccRepairReceipt
  -> update FailureMemory / lineage / quarantine+audit as typed by the branch
  -> evolve receipt-only router state
```

Immutable events and the real memory state are the source of truth. MemAudit
and structural telemetry are observations of that state, not substitute truth
and not evaluator annotations. `MemAuditEccAdapter` must decode the observation
into exactly one mutually exclusive syndrome: `process_fault`, `state_drift`,
or `adversarial_poison`. Each syndrome admits exactly one repair family and one
durable incident sink:

| Syndrome | Legal repair family | Durable sink | Forbidden cross-type effect |
|---|---|---|---|
| `process_fault` | pipeline patch | `FailureMemory` | no supersession or poison quarantine |
| `state_drift` | supersede while retaining history | lineage log | never record the old, once-correct state as a failure |
| `adversarial_poison` | quarantine suspects | quarantine + audit | never distill quarantined content into `FailureMemory` |

GHOST may select only a stable, registered operator compatible with the decoded
syndrome. The selected operator runs on shadow state; it never mutates the live
state before acceptance. Commit is conjunctive: the syndrome must resolve,
parity and invariants must pass, roots and provenance must bind, safety must
hold, and locality cost must remain within budget. Failure of any check rolls
back to the exact pre-repair root. Every attempt, including rollback, emits an
`EccRepairReceipt`.

The receipt is the only online learning signal. A valid non-evaluation receipt
may update GHOST's posterior/register statistics and the matching branch sink.
It must not directly promote, rewrite, or add members to the frozen serving
registry; pattern/skill creation and registry promotion remain governed,
versioned transitions. Thus the loop evolves selection state online while
preserving an auditable operator lineage.

Dataset gold answers, labels, reference answers, split metadata, and answer
replay results are sealed evaluator inputs. They may score completed artifacts
after the runtime has finished, but may not enter incident detection, operator
selection, memory mutation, commit acceptance, receipt construction, or router
updates. Runtime provenance is a closed gold-free structure and fails closed
when sealed concepts appear at any nesting depth.

For the live loop, *counterfactual* means applying a candidate repair to shadow
state and checking whether the syndrome disappears without violating ECC
invariants. Same-trace answer replay is forbidden as causal evidence. The
counterfactual-replay, recovery-gain, Post-Repair Context Replay, gold verifier,
and label-action material below is retained only as legacy/offline baseline and
external-evaluation vocabulary; it does not define the live loop.

The detailed boundary is frozen in
`docs/RUNTIME_EVIDENCE_BOUNDARY_CONTRACT.md`.

### Prior and cold-start terminology

An operator library or frozen registry is **capability initialization**: it
makes legal repairs executable. A router prior is **selection initialization**:
it ranks two or more legal candidates before any receipt from the evaluated
stream. They are different from **online learning**, which is the chronological
change in selection state caused only by earlier valid `EccRepairReceipt`s.

A runtime prior is optional, not part of the ECC correctness contract. When an
experiment uses one, it must be frozen before the evaluated stream, cover the
candidate set without reading the current case's sealed answer/label, carry its
own provenance, and be reported as a warm-start condition. A zero-valued prior
is the canonical cold-start condition. An experiment cannot claim a learning
effect merely because it starts with a seeded operator library, a non-zero
prior, or a single candidate whose selection is forced; it must expose genuine
candidate competition and measure chronological change against a frozen-router
control on a later, non-updating evaluation segment.

## Core Concepts

**CMD** — Counterfactual Memory Debugger: diagnoses memory-augmented agent failures by replaying controlled memory-operation interventions and measuring recovery gain.

**Memory-Augmented Agent** — An agent whose answer depends on persistent memory from prior interactions.

**Memory Failure** — A failed task where memory content, the pipeline, or reasoning over memory causes hallucination, omission, conflict, or misuse.

**Memory Item** — A stored memory unit independently assessable as wrong, stale, conflicting, poisoned, or compression-distorted.

**Memory Pipeline** — The process that writes, compresses, extracts, retrieves, and injects memory.

**Counterfactual Replay** — Re-running the agent with a controlled memory intervention and measuring recovery gain: Δk = Metric(ŷ_k, y) - Metric(ŷ, y).

**Recovery Gain** — Δk: the score improvement from a replay intervention over the baseline. CMD's universal primitive: "perturb, then measure directed response against the strongest available reference." Δk is the **fitness signal** for repair search and skill evolution — which intervention actually recovers — not a label scored against gold. (CMD has pivoted from a fault classifier reporting label macro-F1 to a self-repair + self-evolution loop headlined by repair efficacy; the step actions below are an internal action space, not prediction targets.)

**Operation-Level Attribution** — Assigning a failure to a specific pipeline operation rather than a free-form explanation.

**Error-Cause-Solution (ECS)** — A structured record: what failed, which operation caused it, what corrected memory should replace it, and what repair guidance to apply.

**Failure Memory** — A store of ECS/operator records, retrieved by fingerprint match on current task. Its executable repair output (`corrected_memory` / operator-transformed context) may enter answer-time context; `repair_guidance`, `cause`, and `fm_context` remain metadata for repair selection, audit records, and skill distillation.

**Post-Repair Context Replay** — After ECS, rebuild context with the repair and re-run the original query. Outputs `recovered` / `partial` / `failed`. This is CMD's automated quality gate.

**Subagent EvidenceVerifier** — A subagent that receives atomic context {FACT, TEXT, STANDARD} and outputs PRESENT | ABSENT. One subagent per gold_evidence item. Continuous evidence_score emerges from aggregation: count(PRESENT) / total.

**Subagent AnswerVerifier** — A subagent that receives {ANSWER, GOLD_ANSWER, STANDARD} and outputs EQUIVALENT | NOT_EQUIVALENT. Used at Post-Repair validation and MCTS leaf Δ.

**G-Eval** — Judge-as-distribution: single forward pass reads top_logprobs of score token, computes E[score] = Σp(k)·k. Zero variance given same endpoint. Used for value function (#2) and directed entailment divergence (#4).

**Directed Entailment Divergence** — The divergence measure for item-level diagnosis. Judge-as-distribution reads "m̂_i entails/contradicts m_i" score-token distribution, takes expectation as continuous directed contrast scalar. NOT information-theoretic KL divergence (KL requires two same-support probability distributions; m_i/m̂_i are texts, not distributions). Satisfies d(x,x)=0, asymmetric, no triangle inequality. Directedness enables typing: forward vs reverse direction → wrong vs compression.

**Reference Hierarchy** — CMD's three-tier reference availability, governing which divergence is computable:
- **Gold** (top): task provides correct answer y. Recovery gain = Metric(ŷ_k, y) − Metric(ŷ, y). MCTS leaf Δ uses this when y is available.
- **Surrogate-of-gold** (middle): calibrated cheap proxy for gold_answer, anchored via offline gap measurement. Online step-action attribution uses this when the task result y is unavailable (MCTS leaf Δ, value answer-axis).
- **Reconstruction** (bottom): LOO-reconstructed m̂_i from store\{m_i}. Item labels use this (no task result, source θ deleted).

Law: never descend when a stronger reference is available; reconstruction is last resort.

## Two-Branch Runtime Logic

**Core principle: (missing → fill, present → fix).**

```
evidence in recalled content?
  ├─ NO  → Fill: first send to API generate (answer this turn)
  │        → async trigger re-extract / ask / HITL
  │        No diagnosis, no label — just fill the gap (next turn benefits)
  └─ YES → Fix: hook lightweight correction (de-conflict / re-rank)
           → then send to API generate
           → then enter diagnostic cascade (Tier 2 item gate → Tier 3 pipeline MCTS)
```

**Evidence check** is a soft runtime signal (hook's `evidence_coverage` / `retrieval_score` low → "missing"), NOT a hard phrase-match gate. Runtime tolerates noise because fill action is low-risk (filling what's actually present but reworded causes no harm).

**Formation failure types** (write/compression/premature_extraction/ingestion) are NOT produced as labels — they surface as "evidence missing" and are absorbed by the Fill branch (re-extract this turn, no diagnosis). CMD never disentangles which formation operation dropped the evidence. This is an honest information-theoretic floor: detecting "what should have been written" requires knowing "what should exist," which is unavailable source-free.

## Hook (Confidence Gate)

**Hook** — A confidence gate at retrieval-after, generation-before. Outputs single scalar confidence score; does NOT diagnose or classify.

**Two branches**:
- Evidence missing (coverage/score low) → **Fill branch**: first send to API generate (answer this turn), then async re-extract / ask / HITL. No diagnosis, no label.
- Evidence present → **Fix branch**: hook lightweight correction (de-conflict / re-rank) → send to API generate → enter subagent loop (Tier 2 → Tier 3), diagnose and repair.

**empty_ctx deprecated**: Subsumed into hook's low-confidence fill branch.

**Cold-start factors (6)** — Hand-crafted factors for confidence scoring, targeting "can this recall support correct answer":

| Factor | Signal | Source |
|--------|--------|--------|
| `retrieval_score_max` | Top retrieval score | Retriever |
| `retrieval_score_entropy` | Score distribution entropy (lower = more focused = more confident) | Retriever |
| `evidence_coverage` | Recall content covers query key entities ratio | NER / keyword match |
| `memory_recency_min` | Newest item timestamp (newer = more confident) | Memory metadata |
| `memory_recency_spread` | Timestamp spread (large = may contain stale) | Memory metadata |
| `conflict_signal` | Recall items have explicit contradiction (bool or degree) | Tier2 ② byproduct |

**Distillation endpoint**: Use Tier2-3 post-diagnosis "actually needed diagnosis vs wasted run" as labels, train confidence model to replace hand-crafted factors.

**Online mode**: Offline-oracle / online-student. The offline exhaustive single-point scan is the oracle that distills transferable `(gen_point, action)` priors into Failure Memory (keyed `(query, hop, label)`); online search is a budget-capped top-2 directed seed plus UCT rollout remainder (the second seed is typically `injection` — re-injecting recall is a near-universal fallback repair). Single-point-unrecoverable cases (below the noise-floor abstention threshold — gold already answerable, or coupled) abstain and fall to MCTS's coupled `b^d` search. Distilled no-tree strategy is future latency-sensitive degradation path.

## Diagnostic Cascade (Fix Branch Only)

When hook routes to Fix branch (evidence present), enter two-tier diagnostic cascade:

### Tier 2: Item Gate (Reference-Contrast Divergence)

**Position**: Same position as hook (retrieval-after, generation-before), but inside subagent loop (hook is loop entry, item gate is loop interior). Runs before Tier3; item-wrong → item treatment, skip Tier3; item-correct → enter Tier3.

**Unified principle**: Reference-contrast divergence = item-side recovery gain. Same counterfactual primitive as pipeline, different reference hierarchy level (reconstruction vs gold).

**Item internal cost ladder** (reference acquisition cost ascending, reconstruction is fallback not main path):

```
① Timestamp     free, deterministic, 0 LLM   → only produces "old" flag for ②, no standalone verdict
② Recall-set collision   existing reference (sibling items), ≤C(5,2) contrasts, 0 generation → stale / conflict
③ LOO reconstruction   must create reference, 1 generation + contrast → wrong / compression
Terminal HITL   ③ divergence at threshold edge + item_poisoned (no source-free detection, info-theoretic floor)
```

**② Collision scope = this retrieval's recall set** (per-task scoping, ~5 items). Cross-item collision only within recall set; "library has contradicting item but not recalled this time" not caught — that item has no causal relation to this task. Cross-task library hygiene is separate offline job (same scoping as pipeline "only counterfactual on recalled context").

**① merged into ②**: "Does m_i have newer version" = "Is there a same-topic but newer item in recall set that contradicts m_i" = ② collision + timestamp direction. ① degenerates to pure direction signal; stale vs conflict branch in same ② collision pass:

| ② Collision result | Timestamp direction (from ①) | Verdict |
|-------------------|------------------------------|---------|
| Large directed divergence (contradiction) | One has reliable timestamp and is newer | `item_stale` (old overwritten) → can auto-update to new |
| Large directed divergence (contradiction) | Same period / no reliable timestamp | `item_conflict` (coexisting contradiction, needs human/rule arbitration) |
| Small divergence (consistent) | Any | Pass (including "old but not overwritten") |

**Content-wrong labels** (5): `item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`.

**Engine reuse**: ② contrast and ③ divergence both use `_continuous_verify` (G-Eval logprob path). Two-direction divergence → wrong/compression typing for free (forward = wrong, reverse = compression).

**Co-morbidity cost**: stale ∧ wrong → ② judges stale first, won't enter ③, "also wrong" not discovered. This is consistent tradeoff of CMD single-operation attribution, falls outside Shapley coalitional out-of-scope boundary; high label orthogonality reduces co-morbidity probability.

### Tier 3: Pipeline Single-Point Counterfactual Scan (Step-Level Attribution)

**Precondition**: Memory content is both present AND correct (passed Tier 2 item gate).

**Mechanism**: Single-point counterfactual scan over pipeline labels as step actions, applied at each generation point (API send where LLM actually reasons). Tool calls / pure-reasoning hops / context accumulation = pass-through, no branching. (MCTS tree search retired from the mainline per C6: TRUE_COUPLED 1/30; offline exhaustive single-point oracle + online top-2 directed seed replaces it. The `counterfactual/` package, formerly `mcts/`, keeps the historical import path.)

**Retrieval-period labels** (4 live, serve as step actions): `retrieval_error`, `injection_error`, `granularity_error`, `safety_error`.

**Action table per generation point**:

| Action | Legality | Step-level semantics |
|--------|----------|---------------------|
| `retrieval_error` | Always legal | Wrong retrieval (absorbs route: cross-tier misfetch, no sub-actions) |
| `injection_error` | Always legal | Injection format/order error OR context management squeezed out injected evidence (dual semantics, b-sense explained separately in paper) |
| `granularity_error` | Always legal | Granularity obscures evidence (rewrites item itself, relies on value pruning, not flag gating) |
| `safety_error` | Gated `passed_safety_filter` | Safety layer blocked evidence |

3 always-legal + 2 gated. Gating flags = RPE metadata removed (`is_graph_expanded` / `passed_safety_filter`), reliable offline, online degrades to base actions only (same gold-dependent pattern, online relies on HITL).

**Tree structure**: Root = (memory + system prompt + question); depth = generation point count (not tool call count, not hop count); width = legal labels per generation point; siblings = counterfactual contrast (same prefix, different label this hop).

**Value function (#2, nested, no free weight)**:

```
k       = #{ atom_i : rubric_B(ctx_h, atom_i) ≥ τ }   τ≈0.5    # evidence hard count
ceiling = k / N                                                 # integer part: grounded ceiling
V_scalar = ceiling · ( E[score_answer] / 4 )                    # fractional part: answer within [0,ceiling] continuous
V_vector = ( E[score_answer], [rubric_B raw continuous scores × N] )  # stored per node, fed to credit/repair
```

Ceiling semantics: evidence all present but answer low → value can drop to bottom → injection/granularity "evidence present but unusable" true memory failures not masked; reasoning-type non-memory errors handled by back-prop (any intervention can't recover → Δ≈0 → UCT abandons).

**Credit assignment (#3, max-backup)**:

```
Selection:  child* = argmax_c [ Qmax(c) + C·√( ln N(parent)/n(c) ) ]
Expansion:  unfold one untried legal action; new node Qmax initialized = V_scalar (soft pruning here)
Rollout:    remaining hops set identity → full re-run to terminal → leaf real Δ
Back-prop:  Qmax(a) ← max( Qmax(a), Δ ) ; n(a) += 1    # max-backup, along path all ancestors
```

Leaf Δ = terminal AnswerVerifier(leaf_answer, gold_answer). Max-backup not mean: attribution asks "does there EXIST a completion that recovers" (existence); CMD rollout near-deterministic (fixed oracle + logprob kills variance), max ≈ mean, bare max is clean.

**Shallowest-recovery-depth stopping**: depth-1 single-point intervention rollout already recovers → that hop is main culprit, no deeper expansion (same rule as RepairOrchestrator "first recovered or exhaust", moved earlier to search stage).

**Credit from back-propped Qmax**: `credit(hop h) = Qmax(prefix<h + h:best_label) − Qmax(prefix<h + h:identity)`. Largest hop = main culprit; diff ≈ 0 = collateral/innocent.

## Pipeline Step Actions

CMD's Tier 3 single-point counterfactual scan diagnoses retrieval-period failures at 4 live generation-point step actions (was 5; `graph_error` retired 2026-06-19 — recovery floor 0.067, dropped from the action space):

| Label | Definition | Intervention |
|-------|-----------|--------------|
| `retrieval_error` | Correct memory exists but not retrieved (absorbs cross-tier misfetch) | oracle_retrieval |
| `injection_error` | Memory retrieved but injected with format/order errors; OR context management squeezed out injected evidence | injection_oracle |
| `granularity_error` | Memory expressed at sub-optimal granularity, obscuring evidence | oracle_granularity |
| `safety_error` | Safety filter blocked valid evidence (gated `passed_safety_filter`) | safety_off |

3 always-legal (retrieval / injection / granularity) + 1 gated (safety).

**Not labels**:
- **Formation failures** (evidence never written / compressed away / lost in extraction / never ingested) surface as evidence-missing → Fill branch, no sub-typing (information-theoretic floor: can't name which formation op dropped evidence without gold).
- **`reasoning_error`** is not a label — non-memory reasoning faults emerge through back-prop (no intervention recovers → Δ≈0 → UCT abandons), never assigned as a step action.
- **Cross-tier misfetch (route)** is absorbed into `retrieval_error`, no separate label.

## Key Boundaries

1. **Leak-safe monitor**: enum-locked `anomaly_reason` only; opaque evidence IDs; no content text, no labels, no gold answers.

2. **Sandbox write boundary**: CMD-Audit writes only to replay-local sandbox; never to production agent memory.

3. **Evidence-missing routes to Fill**: when recalled memory text lacks evidence phrases (`evidence_recall_from_text(gold_evidence, memory_item.text)` low), the failure is upstream formation (write/compression/extraction/ingestion). CMD does not sub-type which formation op failed — it routes to Fill (re-extract this turn). The `retrieval_error` step action requires correct memory present in recoverable form; absent that, no retrieval blame is assigned.

4. **ECS cause constraint**: a step-action ECS cause must name a step action (`retrieval_error` etc.), not borrow item-label vocabulary; a Tier 2 item ECS cause uses the item label directly. The two streams stay separate — pipeline ECS never re-declares item-fault names in free text.

5. **Context construction mode**: Answer-time repair context receives only executable repaired content: `corrected_memory` or an operator-transformed context. `repair_guidance`, `cause`, `wrong_memory`, and `fm_context` do not enter the answer prompt; they are metadata for action selection, audit, and skill distillation. **Gold-free construction guard** (structural, not text-based): repaired context is a pure function of `(recall_set, pipeline_action)` via `apply_pipeline_action` and never reads `case.gold_*` — scoring legitimately uses gold, construction does not. This isolates the selection-policy claim (CMD-repair vs no-repair vs random / llm_judge, all sharing one gold-free executor) from any gold leak.

6. **Perturbation type**: Probe Case `perturbation_type` is an injected ground-truth label, never guessed.

7. **Quality gate**: Post-Repair Context Replay is CMD's automated quality gate — re-runs the original query with repaired context.

8. **Recovery Gain vs Shapley value**: CMD's Recovery Gain is operation-sequential (linear in pipeline length, single-operation Δk). Shapley-value counterfactual responsibility is coalitional (exponential in agent count, average marginal contribution across all subsets). They are complementary: CMD targets within-agent pipeline attribution; Shapley targets across-agent responsibility allocation. Coupled failures (two steps each independently bad, single-point replay neither recovers) fall outside CMD single-operation linear Δk boundary.

9. **Provenance tracking**: Execution Lineage DAG recording in-edge derivation per MemoryItem across interventions, HMAC tamper detection, `graph_error` distractor identification.

10. **Item-level vs pipeline-level diagnosis**: Item gate (Tier 2) checks memory content correctness before pipeline attribution. Content-wrong labels diagnose content incorrectness. Pipeline labels only apply when memory content is confirmed present and correct (passed Tier 2). Fill branch (evidence missing) skips diagnosis entirely — just fills this turn.

11. **Information-theoretic floors**: Three source-free detection limits, all routing to Fill or HITL rather than producing a label. (a) `item_wrong` "confidently consistently wrong" — self-consistency can't catch (same bias both paths); (b) `item_poisoned` — no source-free detection, HITL-only; (c) formation failures (write/compression/premature_extraction/ingestion) — can't name which op dropped evidence without gold, so evidence-missing routes to Fill. These are honest paper contributions, not gaps.

## Key Relationships

- **CMD** diagnoses **Memory Failures** via **Counterfactual Replays** → **Recovery Gain** → **Operation-Level Attribution**.
- **CMD-Audit** owns attribution, replay deltas, repair validation; **CMD-Skill Adapter** is deployment layer connecting to real memory agent APIs.
- **4 live pipeline step actions**: retrieval, injection, granularity, safety (Tier 3 generation-point actions; `graph_error` retired 2026-06-19).
- **5 item labels**: item_wrong, item_stale, item_conflict, item_poisoned, item_compression_distorted (Tier 2 item gate).
- **Formation failures** (write / compression / premature_extraction / ingestion) are not labels — absorbed by Fill branch as evidence-missing.
- **Interventions** for step actions: oracle_retrieval, injection_oracle, oracle_granularity, graph_off, safety_off.
- **Post-Repair Context Replay** follows **ECS** and serves as automated quality gate.
- **Failure Memory** stores `wrong_memory + original_evidence + corrected_memory + repair_guidance` per ECS/operator record; `repair_guidance`/`fm_context` are metadata signals, while `corrected_memory` or the operator-transformed context is the answer-time repair signal.
- **Hook** gates two branches: Fill (evidence missing → re-extract this turn, no diagnosis) vs Fix (evidence present → Tier 2 item gate → Tier 3 pipeline MCTS). Tier 2 and Tier 3 run inside Fix branch, in series not parallel.
- **RepairOrchestrator** iterates `close_deltas` via **RepairExecutor** → LLM selects **RepairAction** → **Adapter.apply_repair** → Post-Repair Context Replay.

## Limitations

**Source-free detection floor**: Formation failures (`write` / `compression` / `premature_extraction` / `ingestion`) cannot be sub-typed at runtime — detecting "what should have been written" requires knowing "what should exist," which is unavailable without gold. CMD does not fabricate formation labels online; it routes evidence-missing cases to the Fill branch (re-extract this turn). This is an information-theoretic bound, stated as an honest contribution rather than papered over. The live attribution surface is therefore 4 pipeline step actions (Tier 3 single-point scan; `graph_error` retired) + 5 item labels (Tier 2 item gate); formation and reasoning faults are handled by Fill and back-prop respectively, not by label assignment.

## Flagged Ambiguities

- `retrieval_error` requires correct memory present in recoverable form. If recalled text lacks evidence phrases, the failure is upstream formation → Fill branch, not a retrieval label.
- Subagent Judge Monitor is leak-safe: triggers replay, emits no final labels/ECS/writes/gold answers/full traces.
- ECS `cause`: step-action ECS names a step action; item ECS names an item label. The pipeline stream does not borrow item-fault vocabulary in free text.
- Failure Memory is not a raw log archive. Don't re-inject complete failed traces.
- CMD-Audit writes limited to replay-local sandbox. Only CMD-Skill Adapter writes production state.
- Perturbation type is injected ground truth. Never use LLM-guessed or post-hoc labels.

## Evolution Protocol

**Operator Skill** — A gold-free trigger plus an executable `OperatorSpec` and
its recovery track record. Its body changes the memory context; it is not
answer-time guidance text.

**Operator Library** — The versioned collection of Operator Skills available
before a case is evaluated. A case may update the library only after its own
outcome has been recorded.

**Repair Pattern** — A gold-free, reusable description of the observable
memory-failure situation that triggers skill retrieval. A pattern aggregates
case evidence and owns no executable mutation itself. One Repair Pattern may
index multiple competing Skill Families. In the Skill Self-Evolution
experiments, its definition, fingerprint function, similarity function, and
matching parameters are frozen; only its observational statistics accumulate.

**Skill Family** — The append-only lineage of executable Operator Skills
associated with one Repair Pattern. Distinct operator shapes remain separate
competing families even when they recover the same observable situation; no
single family is assumed canonical.

**Skill Revision** — An immutable executable revision in a Skill Family. A new
revision may specialize, generalize, parameterize, or compose earlier
OperatorSpecs, but never overwrites them. Its parent revision and producing
case evidence remain recorded.

**Active Skill Set** — The Skill Revisions eligible for runtime retrieval at a
particular library version. It includes provisional-active and stable
revisions. Evolution changes this set by activation, promotion, or retirement
pointers; it does not mutate historical revisions.

**Skill Self-Evolution** — The append-only process by which accumulated,
post-outcome repair evidence creates new Skill Revisions and promotes revisions
that improve held-out recovery under a fixed retrieval and execution policy.
Repair Pattern mutation, prompt rewriting, and retriever training are outside
this claim.

**Provisional-Active Revision** — A Skill Revision that recovered its producing
case with Recovery Gain above threshold and becomes runtime-eligible starting
with the next case. Its producing case establishes local executability, not
transfer.

**Stable Revision** — A Provisional-Active Revision promoted after at least
three successful independent post-creation case validations spanning at least
two Recurrence Families, paired non-inferiority to the previous active revision
under the same budget, and no regression on the incumbent's anchor set.

**Revision Anchor Set** — The immutable four-case capability contract created
when a revision becomes stable: its producing case plus the earliest three
successful independent validation cases that satisfy the cross-family promotion
rule. Anchors come only from represented update-producing cases. They are
checked by direct revision execution with no retrieval or discovery.

**Soft Retirement** — Removal of a Skill Revision from the Active Skill Set
without deleting its executable body, evidence, or lineage. A retired revision
remains reproducible and rollback-eligible.

**Paired Skill Dominance** — On the same independent validation cases and
budget, a challenger never loses recovery to the incumbent, records at least
three challenger-only recoveries, costs no more on shared recoveries, and
introduces no anchor-set regression.

**Capability Evolution** — Improvement in recovery on cases that were not used
to produce the evaluated library version. A growing library, a changing
recovery provenance, or a falling retry cost is not sufficient by itself.

**Warm-up Reuse** — Stable recovery with lower discovery or rollout cost after
previous repairs become reusable. This is an efficiency result, not Capability
Evolution.

**Recurrence Family** — A group of distinct case variants that instantiate the
same reusable memory-failure structure. Family membership is evaluation
metadata and must not enter the runtime retrieval key.

**Prior Same-Family Count** — The number of earlier variants from the current
case's Recurrence Family that were eligible to update the Operator Library.
This is the exposure axis for recurrence experiments; stream position alone is
not a substitute.

**Offline Closed Loop** — A round-based development protocol: diagnose failures
on training trajectories, update a library or policy, then evaluate the new
version on a fixed held-out probe set.

**True Online Evolution** — A prequential protocol: evaluate case `t`, record
its outcome, then and only then allow it to update the Operator Library. Case
`t` never re-enters future evaluation.

**Verified-Feedback Prequential Evolution** — The bounded Experiment B claim.
After case `t` is evaluated and irreversibly recorded, delayed verifiable
feedback may generate an experience-tape event for `L_(t+1)`. The producing
case never re-enters the online recovery numerator or validates its own
revision. Offline gold supports only a prequential simulation claim unless an
equivalent feedback channel exists in deployment.

**Pipeline-Symmetric Control** — A control arm with the same discovery fallback,
acceptance rule, scorer, and token/rollout budget as the treatment arm. The
intended intervention is the only difference.

**Retrieval Displacement** — Loss of an earlier library recovery because later
skills push its working operator outside the fixed top-N retrieval budget.
It is measured separately from Revision Anchor Set regression, which tests the
executable directly.

**Evolution AULC** — Area under the held-out recovery learning curve, compared
against a stream-order permutation null or a pipeline-symmetric no-update
control.

**Represented-Family Gate** — The primary Skill Self-Evolution gate. It measures
recovery improvement on held-out variants whose Recurrence Families contributed
different training variants but not the evaluated cases. It passes only when
the patterned arm improves from `L0` to `L3`, its difference-in-differences
against `no_update` is positive, and its normalized Evolution AULC exceeds
`no_update`, with every one-sided family-blocked paired-bootstrap 95% lower
confidence bound above zero.

**Family-Blocked Paired Bootstrap** — The evolution Gate estimator. It first
averages held-out variants within each Recurrence Family, then resamples
families with replacement while preserving the same cases, checkpoints, arms,
and run seeds as a paired block. Gate confidence bounds use 10,000
deterministically seeded resamples.

**Unseen-Family Safety Gate** — A mandatory non-regression gate on held-out
Recurrence Families that contributed no update-producing case. It measures
whether specialization harms out-of-family behavior; improvement is not
required for the Skill Self-Evolution claim. At `L3`, the patterned arm's
paired end-to-end recovery difference against concurrent `no_update` must have
a non-negative point estimate and a one-sided family-blocked-bootstrap 95%
lower confidence bound of at least `-0.05`. The five-point margin is the
maximum tolerable material loss, not evidence of improvement.

**Evolution Family Split** — The immutable SHA-256 family-level 80/20 split:
families whose `SHA-256(recurrent_family_id) mod 5 == 0` are unseen safety
families; all others are represented families. Represented variants 0–2 produce
experience and variants 3–4 form the primary held-out probe. Every unseen
variant is read-only evaluation data.

**Offline Evolution Checkpoint** — One of four fixed library evaluations:
`L0` before updates, `L1` after every represented-family variant 0, `L2` after
variant 1, and `L3` after variant 2. Seeds shuffle family order only within a
round.

**Pattern Catalog** — The immutable set of Repair Pattern prototypes generated
before `L0` from represented-family variant 0 gold-free recall content and
metadata using `_memory_fingerprint`. Runtime matching uses the frozen
`_query_signature_similarity` and top-5 Pattern prototypes. No later or unseen
case may create a Pattern.

**Cold-Start Skill State** — At `L0`, every arm has the same empty Active Skill
Set. The Pattern Catalog and discovery fallback exist, but no OperatorSpec is
counted as accumulated experience until a post-outcome successful discovery
creates a provisional-active revision.

**Unkeyed Experience Control** — A pipeline-symmetric evolution arm that
receives the same successful OperatorSpecs, update times, lifecycle rules, and
budget as the patterned CMD arm, but stores revisions in one global Skill pool
and never uses Repair Pattern membership for retrieval.

**No-Update Control** — A pipeline-symmetric arm whose Active Skill Set remains
frozen. It retains the same discovery fallback and execution budget but cannot
accumulate executable repair experience.

**Top-3 Experience Tape Event** — The case-level, arm-shared record of at most
three distinct successful `OperatorSpec`s eligible for post-outcome Skill
updates. Every retained operator must achieve `Recovery Gain >= 0.1`.
Candidates are deduplicated by canonical spec hash and ordered by higher
Recovery Gain, lower rollout cost, then lexical spec hash. An already-known
hash reinforces its existing revision rather than creating a duplicate. New
revisions become provisional-active only from the next case.

**Post-Outcome Shadow Discovery** — The arm-independent procedure that creates
the Experiment A experience tape after every arm has recorded the current
case. It runs the frozen discovery fallback once from the same pre-repair case
snapshot, without reading an arm's Skill Library, Pattern membership, repaired
context, or outcome. Operator construction is gold-free; offline gold/scoring
may only verify Recovery Gain and select the Top-3 Experience Tape Event.

**Skill Retrieval Quota** — The fixed top-5 runtime allocation used during
Skill Self-Evolution: three exploitation slots for validated active revisions
and two exploration slots for provisional-active revisions needing independent
evidence. Unused slots are backfilled without increasing the total budget.

**Operator Weight** — The automatically learned conservative recovery weight of
a Skill Revision. After `s` independent validation successes and `f` failures,
`weight = Q_0.05(Beta(1+s, 1+f))`. A success requires
`Recovery Gain >= 0.1`. Producing-case and shadow-discovery evidence are
excluded. The shared `Beta(1,1)` prior is neutral; no operator-specific value is
written by hand.

**Exploitation Order** — Deterministic lexicographic ranking by higher Operator
Weight, higher median Recovery Gain, lower median rollouts, then revision ID.
Producing-case and shadow-discovery evidence are excluded.

**Exploration Order** — Deterministic lexicographic ranking of
provisional-active revisions by fewer independent validations, earlier
activation, then revision ID.
