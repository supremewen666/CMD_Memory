# CMD Innovation Core

This context defines the domain language for Counterfactual Memory Debugger research. It exists to keep PRDs, issues, prototypes, and tests aligned on the same meaning of memory failure, replay, attribution, and repair.

## Language

**CMD**:
Counterfactual Memory Debugger, a framework that diagnoses memory-augmented agent failures by replaying controlled memory-operation interventions and measuring recovery gain.
_Avoid_: generic memory architecture, generic debugger

**Memory-Augmented Agent**:
An agent whose answer may depend on persistent memory from prior interactions, traces, or user facts.
_Avoid_: plain LLM, chatbot

**Memory Failure**:
A failed task where memory content, the memory pipeline, or reasoning over memory plausibly causes a hallucination, omission, conflict, or misuse.
_Avoid_: generic model error

**Memory Item**:
A stored unit of memory that can be independently assessed as wrong, stale, conflicting, poisoned, or compression-distorted.
_Avoid_: document chunk, vector row

**Memory Pipeline**:
The process that writes, compresses, routes, retrieves, injects, and reasons over memory.
_Avoid_: retrieval stack

**V0 Core Label Set**:
The first probe's six pipeline attribution labels: `write_error`, `compression_error`, `premature_extraction_error`, `retrieval_error`, `injection_error`, and `reasoning_error`.
_Avoid_: full taxonomy

**CMD-Audit**:
The standalone research harness that runs counterfactual replays, produces replay deltas, assigns operation-level attribution, and measures repair validity. Write permissions are limited to replay-local sandbox: Audit may modify in-memory probe state and construct repaired context for Post-Repair Context Replay, but must not write to a real agent's persistent memory.
_Avoid_: runtime skill, production memory agent

**CMD-Skill Adapter**:
The future deployment layer that takes validated repair records from CMD-Audit and applies them to a real agent's memory pipeline, skill registry, or User Memory. Only the Adapter writes to production agent state; Audit never does.
_Avoid_: CMD-Audit, attribution engine

**Counterfactual Replay**:
A rerun of a failed case where one memory operation is replaced by an oracle, variant, or controlled intervention.
_Avoid_: rerun, retry

**V0 Replay Portfolio**:
The six controlled counterfactual replays used by CMD-Audit V0: Oracle Write, Oracle Compression, Verbatim Event Oracle, Oracle Retrieval, Injection-Oracle, and Evidence-Given Reasoning.
_Avoid_: full replay taxonomy, all possible memory interventions

**Recovery Gain**:
The answer-score improvement produced by a counterfactual replay relative to the original failed output.
_Avoid_: improvement, score bump

**Operation-Level Attribution**:
The failure label assigned to the memory operation whose intervention produces the strongest recovery gain.
_Avoid_: blame, explanation

**Attribution Table**:
The V0 evidence artifact that records each probe case, replay scores, recovery gains, predicted label, top-2 labels, ground-truth perturbation label, comparator outputs, and diagnosis cost.
_Avoid_: qualitative explanation log, judge report

**Verbatim Event Oracle**:
A counterfactual replay that uses raw pre-extraction events to test whether required evidence survived memory extraction or ingestion.
_Avoid_: raw retrieval

**Premature Extraction Error**:
A first-class pipeline failure where raw events contain required evidence but ingestion or extraction leaves no recoverable memory representation.
_Avoid_: compression_error, retrieval_error

**Error-Cause-Solution**:
A compact repair record containing the error, cause, corrected memory, repair action, and repair guidance.
_Avoid_: failure log, traceback

**Failure Memory**:
A store of Error-Cause-Solution records used to guide future similar tasks.
_Avoid_: full failure archive

**Post-Repair Context Replay**:
A required V0 gate that rebuilds a repaired context from CMD outputs and reruns the original failed query to test whether the repair actually restores the task. Outputs layered scores (`post_repair_answer_score`, `post_repair_evidence_score`) and a three-value `repair_assessment` (`recovered` / `partial` / `failed`), not a binary gate. A `partial` result (evidence recovered, answer still wrong) exposes coupled failures that the pipeline error previously masked.
_Avoid_: future-task transfer test, generic retry, binary pass/fail gate

**Ingestion Error** (deferred to V1):
A pipeline failure where gold evidence never reached the agent at all (truncated input, upstream loss, missing source). V0 subsumes this under `write_error` because Oracle Write is the correct counterfactual intervention for both cases. If V1 shows ingestion cases have a different repair path, they may be split into a first-class label.
_Avoid_: write_error subtype in V0

**User Memory**:
The long-term user-facing memory that CMD repairs after a diagnosed memory failure.
_Avoid_: profile cache

**Targeted Memory Fix**:
A repair action chosen from the operation-level attribution label rather than from an undifferentiated hard-case update.
_Avoid_: generic update

**Subagent Judge Baseline**:
A trace-reading baseline that explains a memory failure without itself performing counterfactual interventions.
_Avoid_: CMD judge, replacement for CMD

**Subagent Judge Monitor**:
A leak-safe, high-recall monitor that can trigger expensive CMD replay but cannot emit final labels, Error-Cause-Solution, memory writes, gold answers, or full failed traces. `anomaly_reason` is forced to a predefined enum (`answer_vs_evidence_mismatch`, `retrieved_context_incomplete`, `evidence_recall_low`, `confidence_anomaly`); free-form natural language is prohibited. Evidence pointers are opaque IDs only, never content text.
_Avoid_: final judge

**Standalone Research Harness**:
The V0 implementation boundary for reproducible perturbation labels, replay deltas, baselines, and metrics outside any production memory agent.
_Avoid_: production memory agent integration

**Adapter Interface**:
A reserved integration boundary that can later connect CMD to existing memory agents without shaping the V0 harness around them.
_Avoid_: direct production coupling

**RPE Gating** (external, from D-MEM 2603.14597):
A Reward Prediction Error signal used to gate expensive memory restructuring. High-RPE stimuli (surprise, contradiction) trigger deep processing; low-RPE stimuli are handled cheaply. CMD may adopt a similar gating pattern for Subagent Judge Monitor pre-filtering.
_Avoid_: CMD mechanism, mandatory V0 component

**Risk-Sensitive Retrieval** (external, from RSCB-MC 2604.27283):
A retrieval framing where the key question is not similarity but safety: abstention and non-injection are first-class actions with asymmetric penalties for false-positive memory injection.
_Avoid_: standard top-k retrieval, similarity-only retrieval

**Abstention Action** (external, from RSCB-MC 2604.27283):
A retrieval decision to deliberately not inject memory when the risk of injection exceeds the benefit, even when similar memories exist. CMD's retrieval_error attribution should distinguish "retrieved wrong memory" from "retrieved right memory but injected unsafely."
_Avoid_: always-inject retrieval

**Decision Attribution** (external, from Trajectory-Informed Memory 2603.10600):
Observational analysis that pinpoints which agent decisions caused failures. Closest existing work to CMD's operation-level attribution but uses trajectory analysis rather than counterfactual intervention.
_Avoid_: CMD attribution, counterfactual replay

**Agent-Native Memory** (external, from ByteRover 2604.01599):
A memory architecture where the same LLM curates, structures, and retrieves knowledge. The tight write-retrieval coupling is the failure mode CMD's counterfactual replay is designed to disentangle.
_Avoid_: multi-component memory pipeline

**Memory Governance** (external, from MemArchitect 2603.18330):
Policy-driven memory lifecycle management (decay, conflict resolution, privacy controls) decoupled from model weights. CMD's ECS repair guidance is the evidence layer that should inform governance policy selection.
_Avoid_: static memory policy, unmanaged memory

**Competitive Baseline** (new V0 concern):
Existing tools and papers that address parts of the memory debugging problem. Peaky Peek (agent_debugger) does interactive checkpoint replay with human-in-the-loop. Trajectory-Informed Memory does observational decision attribution. D-MEM does binary surprise detection. None do automated counterfactual operation-level attribution.
_Avoid_: CMD competitor, replacement for CMD

## Relationships

- **CMD** diagnoses **Memory Failures** by running one or more **Counterfactual Replays**.
- **CMD-Audit** owns V0 attribution, replay deltas, and repair validation; **CMD-Skill Adapter** is deferred as a deployment layer.
- **V0 Core Label Set** scopes the first probe to six pipeline labels; bad memory item labels plus `granularity_error`, `route_error`, `graph_error`, `safety_error`, and `ingestion_error` are deferred to V1/V2.
- A **Memory Failure** may involve a bad **Memory Item**, a failed **Memory Pipeline**, or both.
- A **Counterfactual Replay** produces a **Recovery Gain**.
- The **V0 Replay Portfolio** is the bounded replay set for the first **Attribution Table**; broader interventions stay deferred.
- **Operation-Level Attribution** maps the largest meaningful **Recovery Gain** to a failure label.
- An **Attribution Table** is claim evidence only when it records replay scores, recovery gains, predicted labels, ground-truth perturbation labels, top-2 labels, and comparator context.
- **Verbatim Event Oracle** diagnoses **Premature Extraction Error** by comparing raw events with extracted memory before assigning retrieval blame.
- **Error-Cause-Solution** updates **User Memory** and writes compact **Failure Memory**.
- **Post-Repair Context Replay** follows **Error-Cause-Solution** and precedes future-task **Failure Memory** evaluation.
- **Failure Memory** should retrieve `corrected_memory + repair_guidance`, not the full failed trace. The **Context Construction Mode** decision restricts V0/V1 to corrected-only; contrastive mode is a V2 experiment.
- **Subagent Judge Baseline** compares against CMD, while **Subagent Judge Monitor** may trigger replay.
- **Standalone Research Harness** owns V0 reproducibility and exposes an **Adapter Interface** for later integration.
- **PrefixGuard** detects anomalies from execution traces; **CMD** detects anomalies from memory state. Both are online, but only CMD performs attribution and repair. The two are complementary layers, not competitors.
- **MAGE** shadow memory and **CMD Failure Memory** are structurally analogous: both maintain purpose-built parallel memory stores separate from the agent's operational memory.
- **MemORAI** provenance tracking records WHERE facts came from; **CMD** diagnoses WHICH pipeline operation lost them. Together they form a complete evidence-to-attribution chain.
- A **Probe Case** (experiment 2) carries a known `perturbation_type` and `expected_behavior`; running it through CMD produces an **ECS record**. That ECS record becomes the input data for a **4-Mode Context Case** (experiment 1). Build order is constrained: Probe Cases → CMD → ECS → Context Cases.
- **Probe Suite Scaling** is the V0→V1 gate prerequisite. The 10-case minimum viable template is the first scaling increment — enough to serve both experiments simultaneously.

## Example Dialogue

> **Dev:** "This looks like a retrieval error because the answer missed the evidence."
> **Domain expert:** "Run **Verbatim Event Oracle** first. If the raw event contains the evidence but extracted memory does not, this is **Premature Extraction Error**, not **retrieval_error**."

> **Dev:** "Should we store the whole failed trace in **Failure Memory**?"
> **Domain expert:** "No. Store an **Error-Cause-Solution** record and retrieve only `corrected_memory + repair_guidance` for similar future tasks."

> **Dev:** "Can the subagent judge decide the final label?"
> **Domain expert:** "No. It can act as **Subagent Judge Baseline** and leak-safe **Subagent Judge Monitor**, but final attribution comes from CMD replay deltas."

> **Dev:** "Is **CMD-Skill Adapter** the same thing as **CMD-Audit**?"
> **Domain expert:** "No. **CMD-Audit** proves attribution and repair validity; **CMD-Skill Adapter** is a later runtime layer that uses validated repair guidance."

> **Dev:** "Should we include `wrong_memory + cause` alongside `corrected_memory` when building context for future tasks?"
> **Domain expert:** "Not in V0/V1. Inject only `corrected_memory + repair_guidance`. The value of contrastive context (showing what went wrong) can only be measured with a real LLM agent, not with synthetic string matching. Test it as a 4th experimental mode in V2."

> **Dev:** "Can **PrefixGuard** replace CMD's monitor?"
> **Domain expert:** "PrefixGuard can replace the **detection** layer — it converts traces into risk scores. But it cannot replace CMD's attribution, ECS, Post-Repair Context Replay, or Failure Memory. The two are complementary: PrefixGuard fires the alarm, CMD investigates the cause."

## Flagged Ambiguities

- `CMD-Audit` and `CMD-Skill Adapter` are separate concepts: V0 builds the audit harness, while the skill adapter remains a later deployment boundary.
- `retrieval_error` must mean the correct memory exists in recoverable form but was not retrieved. If raw events contain the evidence and extracted memory cannot recover it, use `premature_extraction_error`.
- `premature_extraction_error` is a first-class pipeline label in V0, not a subtype of `compression_error` or `retrieval_error`.
- **V0 Replay Portfolio** means the six replay paths listed above, not `granularity_error`, `route_error`, `graph_error`, or `safety_error`.
- **Attribution Table** is not a subagent judge narrative. It must stay replay-delta grounded.
- `Subagent Judge Monitor` is leak-safe: it can trigger replay but cannot emit final labels, Error-Cause-Solution, memory writes, gold answers, or full failed traces.
- V0 attribution excludes bad memory item labels (`item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`) and evaluates only the six pipeline labels in **V0 Core Label Set**.
- ECS `cause` may describe item state in natural language (e.g., "stored preference was outdated relative to ground truth") but must not use forbidden item label names or re-declare them through natural language equivalents (e.g., "the memory item is stale").
- `Memory Item` and `Memory Pipeline` are separate failure families. A case may need top-2 or multi-label attribution when both contribute.
- `Failure Memory` is not a raw log archive. Re-injecting complete failed traces risks reintroducing memory pollution.
- `CMD-Audit` write permissions are limited to replay-local sandbox. It may modify in-memory probe state and construct repaired context, but must not write to a real agent's persistent memory. Only **CMD-Skill Adapter** applies validated repairs to production agent state.
- `Oracle Write` injects gold evidence into memory, while **Verbatim Event Oracle** tests whether evidence was lost before or during extraction.
- `Standalone Research Harness` is the V0 implementation target. Existing memory-agent integration is intentionally deferred behind an **Adapter Interface**.
- Version gates are evidence-driven, not feature-stacking: V0→V1 requires the four V0 evidence artifacts passing paper-claim thresholds; V1→V2 requires at least two distinct memory agents integrated through the Adapter Interface without macro F1 regression.
- When both Verbatim Event Oracle and Oracle Retrieval recover evidence, gain ranking naturally picks the stronger replay — no special tie-breaker needed. The case is not an extraction loss, it's a retrieval miss that raw events happened to partially patch.
- When Verbatim Event Oracle and Oracle Retrieval both fail but Oracle Compression succeeds, the case is a legitimate `compression_error` — the diagnosis cost of running two non-recovering replays before finding the root cause is design-expected, not a bug. This additional replay cost is expected for taxonomy boundary cases and is considered design-internal, as long as it remains bounded within the smoke suite.
- V0/V1 Failure Memory context uses **corrected-only mode** (`corrected_memory + repair_guidance`). The **contrastive mode** (`wrong_memory + cause + corrected_memory + repair_guidance`) is deferred to V2 because: (1) V1 evaluates attribution accuracy, not context construction effectiveness; (2) V0 synthetic string matching cannot measure the contrastive learning signal; (3) no existing literature provides evidence that contrastive context outperforms corrected-only context for agent memory repair.
- The **4-Mode Context Experiment** (none / full_trace / corrected_only / contrastive) is a pre-V2 validation that can run at any time on existing V0 smoke artifacts + a real LLM. It does not require CMD code changes. It answers whether contrastive mode is worth adding to V2 before investing in full V2 infrastructure.
- **PrefixGuard** detects anomalies from **execution traces** (tool calls, action sequences); **CMD Subagent Judge Monitor** detects anomalies from **memory state** (evidence-answer mismatch, retrieval completeness). The two signal sources are complementary — each can detect failures the other misses.
- **MAGE** shadow memory and **CMD Failure Memory** are two independent instances (May 2026) of the same architectural pattern: purpose-built parallel memory stores separate from the agent's operational memory. This cross-team convergence suggests an emerging principle.
- **Probe Case** `perturbation_type` is injected, not guessed. The perturbation is deliberately manufactured. Do not construct cases where `perturbation_type` is an LLM's best guess or post-hoc label.
- **4-Mode Context Case** `none` mode must fail (baseline answer ≠ gold_answer). If `none` already succeeds, the case does not need Failure Memory and should be excluded from the context experiment.
- **Dataset build order** is constrained: building Context Cases before CMD has produced ECS records is not possible — the `wrong_memory`, `cause`, `corrected_memory`, and `repair_guidance` fields come from CMD's ECS output. Build Probe Cases first, run CMD, then build Context Cases from ECS.
- No existing literature (40+ papers surveyed as of 2026-05-12) provides a controlled comparison of `wrong_memory + cause + corrected_memory` vs `corrected_memory only` for agent memory repair context. **MEMAUDIT** (2605.02199) package-oracle protocol, **Memory-Probe** (2603.02473) 3×3 grid design, **MemEvoBench** (2604.15774) risk-type classification, **ErrorProbe** (2604.17658) step-level error injection, and **MedEinst** (2601.06636) counterfactual diagnosis benchmark are the closest methodological references for dataset design, but none address context construction comparison.

## V1 Domain Language (2026-05-11)

**V1 Pipeline Label Expansion**:
The five deferred pipeline labels introduced in V1, in priority order: `ingestion_error` → `route_error` → `granularity_error` → `graph_error` → `safety_error`. Bad memory item labels remain deferred to V2.
_Avoid_: adding all labels at once, adding item labels before pipeline labels

**Ingestion Error** (V1, priority 1):
A pipeline failure where gold evidence never reached the agent at all (truncated input, upstream loss, missing source). Split from `write_error` because repair paths differ: ingestion errors require upstream data pipeline fixes, while write errors require memory admission policy changes. Diagnosed by Oracle Write (same replay, different root cause).
_Avoid_: write_error subtype

**mem0** (external, V1 Adapter target):
The most popular open-source agent memory layer (55k GitHub stars, YC S24). v3 algorithm uses single-pass ADD-only extraction with multi-signal retrieval (semantic + BM25 + entity matching). SOTA on LoCoMo (91.6) and LongMemEval (93.4). Selected as the first CMD-Skill Adapter integration target for V1.
_Avoid_: CMD component, CMD dependency

**Letta** (external, V1 Adapter target):
Formerly MemGPT. Platform for building stateful agents with explicit memory tiering (core/archival/recall). Selected as the second CMD-Skill Adapter integration target for V1→V2 gate (≥2 agents required).
_Avoid_: CMD component

**Memory-Probe** (external, from 2603.02473):
A diagnostic framework that separates retrieval quality from write quality using 3×3 grid comparison (Mem0-style / MemGPT-style / raw chunks × cosine / BM25 / hybrid). Closest existing work to CMD's diagnostic framing, but uses observational grid-comparison rather than counterfactual replay. Key finding: retrieval method dominates write strategy (20-point vs 3-8 point accuracy span), and raw chunks often outperform lossy extraction — directly validating `premature_extraction_error`.
_Avoid_: CMD alternative, counterfactual method

**Single-Paper Scope**:
The decision that V0 + V1 + V2 together constitute one paper, with V2 as the final module/skill. V0→V1 and V1→V2 gates are internal checkpoints, not paper boundaries. The paper tells the full arc from diagnosis (V0) through cross-system generalization (V1) to runtime repair (V2).
_Avoid_: three separate papers, V0-only paper

**Adapter Integration Target**:
A real-world memory agent system that CMD-Skill Adapter connects to in V1/V2. Must expose observable memory operations (write, retrieve) that map to CMD's counterfactual replays. First target: mem0. Second target: Letta.
_Avoid_: custom agent, synthetic agent

**RPE Pre-Filter** (late V1 optimization):
A Reward Prediction Error gating mechanism for Subagent Judge Monitor, based on D-MEM (2603.14597). Deferred to late V1 — after label expansion and adapter integration produce full replay traces for training the surprise model.
_Avoid_: V1 gate prerequisite, V0 component

**PrefixGuard** (external, from 2605.06455):
A trace-to-monitor framework that converts raw LLM agent traces into lightweight online failure-warning monitors. Offline StepView induction derives deterministic typed-step adapters; supervised monitor training learns a prefix-risk scorer from terminal outcomes. Key finding: LLM judges are substantially weaker than trained monitors for online warning — independently validates CMD's decision to use rule-based (not LLM) monitoring. Key concept: observability ceiling — a theoretical AUPRC bound separating monitor error from failures lacking evidence in the prefix. PrefixGuard and CMD are complementary: PrefixGuard detects anomalies from execution traces, CMD detects anomalies from memory state, and only CMD does attribution and repair.
_Avoid_: CMD replacement, CMD monitor equivalent

**MAGE** (external, from 2605.03228):
Memory As Guardrail Enforcement. A defensive framework inspired by "shadow stack" in systems security: maintains a dedicated safety-focused agentic memory (shadow memory) that distills safety-critical context across the full execution trajectory, proactively assessing risk of pending actions before execution. Structurally mirrors CMD's Failure Memory — both are purpose-built parallel memory stores. Directly validates `safety_error` as a V1 label.
_Avoid_: CMD component

**MemORAI** (external, from 2605.01386):
Memory Organization and Retrieval via Adaptive Graph Intelligence. SOTA graph-based memory on LOCOMO and LongMemEval. Three innovations: dual-layer compression, provenance-enriched multi-relational graph with turn-level factual origins, and query-adaptive Dynamic Weighted PageRank. Should be cited as both related work and SOTA baseline for V1 evaluation. Its turn-level provenance tracking independently validates CMD's need for evidence chains in pipeline attribution.
_Avoid_: CMD component

**Trojan Hippo** (external, from 2605.01970):
First systematic characterization of dormant payload attacks on agent memory — single untrusted tool call plants payload, activates on sensitive topics. Validates `item_poisoned` as a necessary V2 label. Published 1 day apart from MAGE, together defining agent memory security as a May 2026 research frontier.
_Avoid_: CMD component

**Context Construction Mode** (CMD design decision):
The strategy for building Failure Memory context injected into future similar tasks. V0/V1 uses **corrected-only mode**: injects `corrected_memory + repair_guidance` only. A fourth mode (**contrastive mode**: `wrong_memory + cause + corrected_memory + repair_guidance`) is deferred to V2 as a complete deliverable — code and evaluation together. Reasoning: V1 evaluates attribution accuracy, V2 evaluates context construction effectiveness; V0 synthetic string matching cannot measure the contrastive learning signal that only a real LLM agent exhibits.
_Avoid_: full trace injection, mixing audiences (cause is diagnostic for researchers, guidance is prescriptive for agents)

**Observability Ceiling** (from PrefixGuard, 2605.06455):
A theoretical AUPRC bound that separates monitor error from failures lacking detectable evidence in the observed prefix. 6-49% of failures fall below this ceiling — even a perfect monitor cannot detect them from the prefix alone. Maps to CMD's monitor boundary: when no `anomaly_reason` fires, the failure may be undetectable from the memory trace prefix. Can be used to gate replay: skip replay when observability ceiling is low, saving cost for cases where the trace genuinely lacks diagnostic signal.
_Avoid_: monitor performance metric, CMD internal concept

**4-Mode Context Experiment** (pre-V2 validation):
A minimum-viable LLM evaluation to test whether contrastive context (`wrong_memory + cause + corrected_memory`) outperforms corrected-only context. Uses **4-Mode Context Case Schema** (see below): each case contains one query, gold answer, and four pre-built context strings (none/full_trace/corrected_only/contrastive). Requires 15-40 cases covering 3-4 error types (minimum 5 per label, ideal 10). Runs with a real LLM without CMD code changes. Provides the evidence basis for whether contrastive mode is worth adding to V2. No existing literature provides a comparable controlled experiment — if results are significant, the experiment itself constitutes a novelty claim.
_Avoid_: V0/V1 blocker, CMD code dependency

**Probe Case Schema** (CMD experiment 2 dataset format):
The JSON schema for CMD attribution probe cases. Each case carries `query`, `history/raw_events`, `extracted_memory` (list of `{memory_id, text}`), `gold_answer`, `gold_evidence_units` (list of `{evidence_id, text, source}`), `perturbation_type` (injected ground-truth label), and `expected_behavior` (which replays should/should not recover). `perturbation_type` is a known injected label, not a guessed one — the perturbation is deliberately manufactured to test whether CMD correctly identifies it. Each label requires 8-10 cases with different failure variants (e.g., `retrieval_error` variants: BM25 miss, semantic-distractor interference, multi-hop evidence dispersion). Total target: 50-100 cases for V0→V1 gate, 100-150 for 11-label.
_Avoid_: guessed label, observational diagnosis, single-variant per label

**4-Mode Context Case Schema** (CMD experiment 1 dataset format):
The JSON schema for context construction comparison. Each case is a within-subject design (same query × 4 context modes). Carries `case_id`, `query`, `gold_answer`, `gold_evidence_phrases`, `failure_type`, `wrong_memory`, `cause`, `corrected_memory`, `repair_guidance`, and a pre-built `contexts` dict with four keys (`none`, `full_trace`, `corrected_only`, `contrastive`). The `contexts` are fully pre-rendered prompt strings — the experiment only compares LLM outputs. `none` mode must fail (otherwise the case doesn't need FM). Built from ECS records produced by running CMD on Probe Cases (experiment 2 outputs → experiment 1 inputs).
_Avoid_: runtime context assembly, post-hoc context generation

**Probe Suite Scaling** (V0→V1 gate prerequisite):
The expansion of probe cases from 6 smoke cases (1 per V0 label) to 50-100 labeled cases (8-16 per label). Each label needs multiple failure variants: same error type, different root causes. Scaling path: 6 → 10 (2-3 labels, multiple variants) → 50 (all 6 labels, 8+ each) → 100 (full V0 gate threshold). The 10-case minimum viable template serves both experiments: run through CMD pipeline → produce ECS → feed into 4-Mode Context Experiment.
_Avoid_: single-variant-per-label smoke suite, scaling without variant diversity

**Dataset Build Order** (CMD design constraint):
Experiment 2 (CMD attribution effectiveness) datasets must be built first, because Experiment 1 (context construction) depends on ECS records produced by running CMD on Experiment 2's probe cases. Path: Probe Cases (50-100, with perturbation labels) → CMD pipeline → ECS records → review ECS quality → construct 4-Mode Context Cases → LLM evaluation. Build the 10-case minimum viable template first — it serves both experiments simultaneously.
_Avoid_: parallel build, context experiment before CMD produces ECS
