# CMD Open Decisions

Date: 2026-05-09

Status: resolved for V0.

## Decision 1: First Probe Label Scope

Use a smaller V0 label set rather than all pipeline labels.

V0 labels:

- `write_error`
- `compression_error`
- `premature_extraction_error`
- `retrieval_error`
- `injection_error`
- `reasoning_error`

Deferred labels:

- `granularity_error`
- `route_error`
- `graph_error`
- `safety_error`

Reason: V0 needs statistical clarity. With only 50-100 probe examples, full-label coverage creates sparse classes and weak evidence.

## Decision 2: Status of `premature_extraction_error`

Promote `premature_extraction_error` to a first-class pipeline label.

Reason: it captures a failure that is neither standard compression nor retrieval. The raw event contains the needed evidence, but the extracted memory no longer preserves it. Verbatim Event Oracle diagnoses this label.

## Decision 3: Subagent Judge Role

Use Subagent Judge as:

1. a baseline over the same failed trace;
2. a cheap high-recall monitor for triggering expensive replay.

Do not use it as the final attribution source unless it reads CMD replay deltas.

Reason: subagent judge produces post-hoc explanation, while CMD produces intervention-grounded attribution.

## Decision 4: First Implementation Target

Build V0 as a standalone research harness.

Harness components:

- synthetic perturbation dataset;
- baseline memory systems;
- counterfactual replay engine;
- heuristic / subagent baselines;
- attribution metrics;
- ECS output schema.

Reason: the first paper needs clean perturbation labels and reproducible metrics. Full integration into an existing memory agent should come after the attribution claim is proven.

## Decision 5: Post-Repair Context Replay

Add **Post-Repair Context Replay** as a required V0 gate.

Current prototype/PRD/TDD language already mentions targeted repair, ECS, and future Failure Memory retrieval, but that is not enough. V0 must explicitly test whether the corrected context fixes the original failed case.

Required flow:

```text
AttributionAssigned
-> ECSDrafted
-> RepairedContextBuilt
-> PostRepairRetested
-> RepairValidated / RepairFailed
-> FutureCaseGuided
```

The repaired context may include:

- corrected memory;
- repair guidance;
- fixed evidence injection format;
- deleted or suppressed wrong memory;
- rerouted or raw-event evidence, depending on the attribution label.

Constraints:

- do not inject the gold answer;
- do not expose the full failed trace to the future-task context;
- compare against generic hard-case update;
- record answer score, evidence score, repair success, and regression risk.

Reason: CMD should not only explain the failure. It must show that the proposed repair context makes the same failed query recover.

## Decision 6: Naming Boundary

Use `CMD-Audit` and `CMD-Skill Adapter` as first-class names.

- `CMD-Audit`: the V0 standalone research harness and audit module.
- `CMD-Skill Adapter`: the later deployment layer that invokes audit, injects repair guidance, and connects to skill evolution.

Reason: without this language, the project can blur the research object with the deployment interface.

## Decision 7: Subagent Judge Monitor Leak-Safety

Subagent Judge Monitor is allowed to trigger replay, but its output is restricted to:

- `trigger_replay`;
- `anomaly_reason`;
- `confidence`;
- optional redacted evidence pointers.

It must not output:

- final attribution label;
- ECS;
- gold answer;
- full failed trace;
- wrong memory payload intended for future context;
- User Memory / Failure Memory writes.

Reason: monitor context can contain private memory, poisoned memory, and failed traces. It should be a trigger, not a memory writer or repair source.

## Decision 8: Bad Memory Item Labels Excluded From V0 Attribution

V0 excludes bad-memory-item labels from the attribution task:

- `item_wrong`
- `item_stale`
- `item_conflict`
- `item_poisoned`
- `item_compression_distorted`

These labels may appear as ECS notes or future V1 labels, but V0 metrics should only evaluate the six pipeline labels.

Reason: STALE and BeliefMem show this is important, but adding item labels now would dilute the first probe and make V0 less decisive.

## Decision 9: Retrieval Baseline Strengthening Is A V0.5 Issue

The first minimal V0 harness may keep retrieval simple and fixture-controlled, but the roadmap should explicitly add a follow-up issue named **Strengthen retrieval baselines and evidence scoring**.

That issue should add:

- lexical/BM25 retrieval;
- vector retrieval;
- hybrid/rerank retrieval;
- ranked retrieval traces with `memory_id`, `rank`, `score`, `token_cost`, `retrieved_text`, and `matched_gold_evidence_units`;
- retrieval metrics: Recall@k, MRR, nDCG, Precision@k, context noise ratio, and answer accuracy/F1;
- hard negatives covering same-entity confusion, temporal conflict, paraphrase, multi-hop evidence, stale memory, and compression-loss.

Boundary: stronger retrievers may change `retrieval_error` attribution but must not erase `premature_extraction_error`. The hard divider is `evidence_recall_from_text(gold_evidence, memory_item.text)`: if the Memory Item text contains the evidence and a weak retriever missed it, the label may flip to `retrieval_error`. If the Memory Item text does not contain the evidence phrases (extraction already lost them), no retriever improvement can change the label—it stays `premature_extraction_error`.

Reason: V0 should stay small enough to finish, but the paper claim will be stronger if retrieval baselines are not only hand-filled `retrieved_memory_ids`.

## Architecture Consequence

```text
V0: standalone CMD-Audit harness
V1: pluggable audit module for existing memory agents
V2: CMD-Skill Adapter for runtime repair and skill evolution
```

## Decision 10: Competitive Landscape Validation (2026-05-10)

CMD's counterfactual attribution approach occupies an unverified gap. A broad metabolism survey across 27 papers and 10 repos confirms:

- No existing paper or open-source project does automated counterfactual memory replay for operation-level attribution.
- Closest approaches: Trajectory-Informed Memory (observational decision attribution), Peaky Peek (interactive checkpoint replay with HITL), D-MEM (binary surprise detection).
- Failure Memory pattern (A-MemGuard, SQLFixAgent, Reflection-Driven Control) is independently validated across security, code generation, and SQL repair domains.

Status: confirmed. CMD's paper claim that counterfactual replay provides stronger attribution evidence than observational or interactive approaches is defensible against the current literature.

Reason: the first paper's contribution claim must be grounded against real alternatives, not strawmen.

## Decision 11: RPE Pre-Filter for Monitor Trigger (2026-05-10)

D-MEM (2603.14597) uses Reward Prediction Error gating to decide whether to trigger expensive knowledge graph restructuring. CMD faces a similar efficiency question: should Subagent Judge Monitor trigger full V0 Replay Portfolio for every anomaly, or can a lightweight RPE-style pre-filter skip low-surprise cases?

Decision: note this as a V0.5/V1 optimization path, not a V0 requirement. The V0 Subagent Judge Monitor may trigger replay unconditionally on anomaly detection. After V0 evidence artifacts exist, evaluate whether an RPE pre-filter (scoring evidence-surprise gap) can reduce replay cost without lowering attribution recall.

Reason: V0 should establish the full replay-trigger path before optimizing the trigger. D-MEM's 80% token reduction via RPE gating provides a concrete efficiency target for later optimization.

## Decision 12: `evidence_recall_from_text` Phrase-Matching Limitation (Plan A) (2026-05-10)

`evidence_recall_from_text` uses phrase matching as evidence scoring — a necessary but not sufficient condition for semantic correctness. A memory item containing "Messi" can match `required_phrases: ["Messi"]` regardless of whether the semantic content is "Messi is GOAT" (correct evidence) or "Messi is a father" (irrelevant). This is a known V0 limitation.

**Chosen approach: Plan A — accept known limitation, do not upgrade scorer in V0.5.**

Reasoning:
1. **Mitigated by phrase granularity**: `required_phrases` are constructed to include distinguishing terms (e.g., `["Messi", "GOAT"]`). False-positive matches require the probe designer to deliberately weaken the phrase set.
2. **Semantic evaluation belongs to V1**: When V1 integrates real LLM agents, answer scoring will be replaced by LLM-judge evaluation, and evidence scoring can be upgraded to entailment-based checks.
3. **Hard negatives as evidence boundary signal**: Issue 0008's hard negative cases are purposely designed to expose scenarios where phrase matching alone is insufficient (entity confusion, paraphrase). These cases will serve as baseline evidence for the V0→V1 scorer upgrade.

**Rejected alternative: Plan B — upgrade scorer in V0.5.** Rejected because deterministic semantic checking (without LLM calls) would still be heuristic and would not meaningfully close the gap. The real upgrade path requires LLM-based entailment, which belongs to V1.

**Also decided: two retrievers, not four.** Issue 0008 needs only BM25 (weak) and HybridRerank (strong) to demonstrate the core claim that stronger retrievers recover evidence weaker retrievers miss. Vector-alone and hybrid-without-rerank add no independent analytical value.

**Also decided: agentic search deferred to V1.** Agentic search introduces LLM-call non-determinism and new failure modes (query rewrite errors, tool selection errors) that need their own taxonomy review.
