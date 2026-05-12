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

## Decision 13: V1 Label Expansion Order — Pipeline Labels First (2026-05-11)

V1 expands labels in this priority order:

| Priority | Label | Rationale |
|----------|-------|-----------|
| 1 | `ingestion_error` | Oracle Write already covers it; lowest implementation cost to split from `write_error`. Gold evidence never reached the agent (truncated input, upstream loss). |
| 2 | `route_error` | MemFlow (2605.03312) independently validates this failure mode. Oracle Route replay logic is clear. |
| 3 | `granularity_error` | Oracle Granularity concept exists but requires granularity enumeration; higher implementation cost. |
| 4 | `graph_error` | Graph-Off/Graph-Only replay needs graph memory infrastructure. |
| 5 | `safety_error` | Safety-Off/Safety-Oracle needs controllable safety filter switches. |

Bad memory item labels (`item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`) remain deferred to V2. V1 stays focused on pipeline attribution.

Reason: order by implementation cost (low→high) and external validation strength (strong→weak). Each new label requires a corresponding counterfactual replay, so introducing them in dependency order avoids rework.

## Decision 14: First CMD-Skill Adapter Target Agent — mem0 (2026-05-11)

**Chosen: mem0ai/mem0 (55,320 stars) as the first adapter target.**

mem0 is the most popular open-source agent memory layer. Its v3 algorithm (April 2026) uses the simplest viable memory pipeline:

```text
ADD (single-pass extraction) → Entity Linking → Multi-Signal Retrieval (semantic + BM25 + entity)
```

CMD replay mapping:

| mem0 Operation | CMD Counterfactual Replay | Diagnosis |
|---------------|--------------------------|-----------|
| `add()` failure/omission | Oracle Write | `write_error` |
| ADD extraction lost details | Oracle Compression | `compression_error` |
| Raw events have evidence, ADD missed it | Verbatim Event Oracle | `premature_extraction_error` |
| `search()` missed relevant facts | Oracle Retrieval | `retrieval_error` |
| Facts injected in wrong format/order | Injection-Oracle | `injection_error` |
| Facts correct, agent reasoning wrong | Evidence-Given Reasoning | `reasoning_error` |

**Second target: Letta (letta-ai/letta, 22,609 stars)** for V1→V2 gate (requires ≥2 agents). Letta's core/archival/recall tiering exercises `route_error` in a way mem0's flat store does not.

**Also noted: memory-probe (arXiv:2603.02473)** independently validates the write-vs-retrieval diagnostic framing using a 3×3 grid-comparison approach (Mem0-style, MemGPT-style, raw chunks × cosine, BM25, hybrid). It is observational, not counterfactual, and should be cited as the closest existing diagnostic work.

Reason: mem0 has the simplest, cleanest memory API, SOTA benchmark performance, and the largest community. Its ADD-only extraction model is the most straightforward intervention point for CMD's counterfactual replays. "CMD on mem0" is the strongest possible paper claim for V1.

## Decision 15: V0 + V1 + V2 Constitute a Single Paper (2026-05-11)

**Chosen: V0, V1, and V2 are three phases of one paper, not three separate papers.**

Paper story arc:

```text
V0: CMD-Audit standalone harness
    → proves counterfactual attribution works on controlled perturbations

V1: CMD-Skill Adapter + real agent integration (mem0 → Letta)
    → proves cross-system generalization and real-agent applicability

V2: CMD final module/skill
    → proves runtime repair loop and skill evolution value
```

Consequences:
- V0→V1 gate and V1→V2 gate become internal paper checkpoints, not paper boundaries.
- The experimental section needs three layers: controlled attribution (V0) → cross-system generalization (V1) → runtime repair闭环 (V2).
- The paper's abstract and claims must encompass the full arc from diagnosis to deployment.
- Claim ledger (Section 12 of research plan) expands to include V1 and V2 claims.

Reason: the full story from "diagnosis works in vitro" through "diagnosis works in vivo" to "diagnosis drives repair" is stronger than three incremental papers. The counterfactual attribution core is established in V0; V1 and V2 demonstrate its practical value.

## Decision 16: RPE Pre-Filter Deferred to Late V1 (2026-05-11)

**Chosen: RPE pre-filter optimization is a late-V1 efficiency task, not a V1 gate prerequisite.**

Reasoning:
1. V1's core contribution is label expansion + real-agent adapter integration. RPE pre-filtering would dilute this story.
2. Full replay is acceptable cost during V1 development and evaluation; the cost argument matters more for production deployment (V2).
3. D-MEM's 80% token reduction provides a concrete efficiency target, but the optimization should follow after V1's core claims are evidenced.
4. RPE pre-filter requires a trained surprise model, which needs the full replay traces V1 will produce — it is naturally sequenced after V1's main experiments.

This extends Decision 11 (which noted RPE as a "V0.5/V1 optimization path"). V1's priority order: label expansion → adapter integration → real-data probe scaling → RPE pre-filter.

## Decision 17: Failure Memory Context Construction Mode (2026-05-11)

Context: When CMD retrieves Failure Memory records for a future similar task, what text should be injected into the agent's context?

**Chosen: V0/V1 injects only `corrected_memory + repair_guidance`.** The alternative of injecting `wrong_memory + cause + corrected_memory + repair_guidance` (contrastive mode) is deferred to V2 as an additional experimental condition.

Three options evaluated:

| Mode | Injected content | Risk |
|------|-----------------|------|
| A. corrected-only (current) | `corrected_memory + repair_guidance` | Low |
| B. contrastive | `wrong_memory + cause + corrected_memory + repair_guidance` | Medium |
| C. full trace (anti-pattern) | all fields including `wrong_memory` | High |

Reasoning:
1. V0 smoke data shows mode A achieves evidence=1.0, answer=1.0 on all 3 future cases; mode C scores 0.0 on both with pollution_risk=1.0.
2. Mode B's risk profile is label-dependent: harmless for `write_error` (wrong_memory is empty/missing context), but actively risky for `retrieval_error` and `injection_error` (wrong_memory directly contradicts corrected_memory, creating context conflict for the LLM).
3. `repair_guidance` already encodes the lesson from `cause` in actionable form. Adding `cause` text is redundant for the agent and adds token cost.
4. `cause` was designed as a diagnostic signal for researchers/debuggers, not as agent-facing context. Mixing these audiences creates ambiguity.

V2 plan: The 4th experimental mode (`corrected_guidance_with_cause`) is a V2 complete deliverable — code and evaluation together. V1's evaluation target is attribution accuracy (macro F1, confusion matrix); V2's evaluation target is context construction effectiveness (recurrence reduction, hallucination rate). Adding the 4th mode earlier would produce no evidence, since synthetic string matching cannot measure the contrastive learning signal that only a real LLM agent can exhibit. No V1 code changes to Failure Memory's 3-mode structure.

**Pre-V2 validation available at any time**: The 4-Mode Context Experiment can run on existing V0 smoke artifacts (6 ECS records, 3 future task cases) plus a real LLM, without any CMD code changes. It compares 4 modes (none / full_trace / corrected_only / contrastive) on answer accuracy and token cost. This provides early evidence for whether contrastive mode is worth adding to V2, before investing in full V2 infrastructure.

No existing literature (40+ papers surveyed) provides a controlled comparison of `wrong_memory + cause + corrected_memory` vs `corrected_memory only` for agent memory repair context construction. This makes the 4-Mode Context Experiment a novel contribution in itself, regardless of outcome.

## Decision 18: Dataset Build Order (2026-05-12)

Context: CMD needs two datasets — Probe Cases (experiment 2, attribution effectiveness) and 4-Mode Context Cases (experiment 1, context construction). Which should be built first?

**Chosen: Experiment 2 (Probe Cases) first, then Experiment 1 (Context Cases) derived from CMD's ECS output.**

Reasoning:
1. Context Cases require `wrong_memory`, `cause`, `corrected_memory`, and `repair_guidance` — all of which are CMD ECS output fields. Building Context Cases before CMD has produced ECS records is not possible.
2. Probe Cases are the V0→V1 gate prerequisite (probe suite scaling: 6→50-100).
3. A 10-case minimum viable template serves both experiments: run through CMD → produce 10 ECS records → review quality → construct 10 Context Cases → LLM evaluation.

Build path:

```
Step 1: Build 10 Probe Cases (2-3 labels, multiple variants each)
Step 2: Run CMD pipeline → produce ECS records
Step 3: Review ECS quality (cause text, corrected_memory accuracy)
Step 4: Build 10 4-Mode Context Cases from reviewed ECS
Step 5: Run LLM evaluation on Context Cases
Step 6: Scale Probe Cases to 50-100 (V0→V1 gate)
Step 7: Scale Context Cases to 15-40
```

Dataset design references: MEMAUDIT (2605.02199) package-oracle protocol for perturbation injection; Memory-Probe (2603.02473) 3×3 grid for baseline comparison design; MemEvoBench (2604.15774) risk-type taxonomy for perturbation variants; ErrorProbe (2604.17658) step-level error injection methodology; MedEinst (2601.06636) counterfactual diagnosis hierarchy.

Two key constraints:
- Probe Case `perturbation_type` is an injected ground-truth label, never a guessed or post-hoc label.
- Context Case `none` mode must fail; cases where baseline already succeeds should be excluded.
