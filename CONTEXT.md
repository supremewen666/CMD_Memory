# CMD Domain Context

Domain language for Counterfactual Memory Debugger research. Defines terminology, boundaries, and rules.

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

**Failure Memory** — A store of ECS records, retrieved by keyword match on current task, injected as `corrected_memory + repair_guidance`.

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

5. **Context construction mode**: Failure Memory injects only `corrected_memory + repair_guidance`. Contrastive mode (`wrong_memory + cause + corrected_memory + repair_guidance`) is experimental. **Gold-free construction guard** (structural, not text-based): repaired context is a pure function of `(recall_set, pipeline_action)` via `apply_pipeline_action` and never reads `case.gold_*` — scoring legitimately uses gold, construction does not. This isolates the selection-policy claim (CMD-repair vs no-repair vs random / llm_judge, all sharing one gold-free executor) from any gold leak.

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
- **Failure Memory** stores `wrong_memory + original_evidence + corrected_memory + repair_guidance` per ECS record; `fm_context` = diagnosis signal, `corrected_memory` = repair signal.
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
