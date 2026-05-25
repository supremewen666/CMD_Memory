# Topic: CMD Memory Failure Diagnosis

## Current Claim

CMD should treat agent memory failure as a counterfactual attribution and repair-memory problem:

1. detect anomaly;
2. identify whether the memory item is bad or the pipeline failed;
3. replay counterfactual memory operations;
4. generate Error-Cause-Solution;
5. repair User Memory;
6. store compact Failure Memory for future similar tasks.

## 2026-05-08 Incremental Signals

| Source | Signal | CMD Implication |
|--------|--------|-----------------|
| arXiv:2605.03354 | operation-level memory diagnosis may be visible in internal circuits | future optional internal-signal feature for CMD |
| arXiv:2605.03675 | tiered retrieval and consolidation are bottlenecks in long-running agents | route/retrieval/consolidation labels need stress tests |
| arXiv:2605.04264 | persistent memory needs governance, provenance, and correction pathways | ECS records should track provenance and supersession |
| arXiv:2605.04897 | ingestion-time extraction can discard future-needed content | add Verbatim Event Oracle to separate premature abstraction from retrieval failure |

## Updated Failure Hypothesis

Some cases that look like `retrieval_error` are actually earlier `premature_extraction_error` or `compression_error`: the relevant evidence was never preserved in recoverable form. CMD should compare extracted-memory replay against raw-event replay before assigning retrieval blame.

## Proposed New Replay

**Verbatim Event Oracle**

- Input: original raw event/session trace before memory extraction.
- Intervention: retrieve over preserved raw events or inject the minimal raw evidence span.
- Diagnosis: if raw-event replay recovers the answer but extracted memory does not, mark the failure as ingestion-time abstraction loss.

## Engineering Signal

GitHub projects such as Memori and AgenticMemory emphasize production memory attribution, correction chains, graph traversal, and runtime quality/drift tooling. These are not substitutes for a controlled benchmark, but they validate engineering demand for CMD-style labels and repair histories.

## Judge Baseline vs CMD

The user raised an important framing question: why not use a subagent as the judge?

Current position:

- A subagent judge is a necessary baseline, because it tests whether free-form trace explanation already solves the attribution problem.
- It is not sufficient as the main method, because it observes the same failed trace and produces post-hoc explanations.
- CMD changes the evidence type: instead of asking "what seems wrong?", it asks "which intervention recovers the answer?"

This gives CMD three evaluation advantages:

1. attribution can be scored against injected perturbation labels;
2. actionability can be measured by targeted repair success;
3. stability can be tested under prompt/order perturbations.

Subagent judge remains useful as:

- a baseline in Experiment A;
- a natural-language explanation layer over CMD replay deltas;
- a cheap anomaly monitor before expensive counterfactual replay.

## Audit Module vs Skill

Current decision: frame CMD as an audit module first, then expose it through a skill adapter.

Why audit module first:

- counterfactual replay requires external control over memory operations;
- attribution needs reproducible intervention evidence;
- cross-system comparison is easier if CMD sits outside the memory method being audited;
- reviewers are less likely to dismiss it as a prompt-only skill.

Why still keep a skill adapter:

- agents need a callable repair behavior at deployment time;
- ECS records naturally become repair guidance;
- hard-case labels can feed MemSkill-style skill evolution;
- the adapter can decide when expensive audit is worth invoking.

Working interface:

```text
CMD-Audit(trace, memory_state, raw_events, gold_or_proxy_score)
  -> attribution_label
  -> replay_deltas
  -> Error-Cause-Solution

CMD-Skill-Adapter(ECS, current_task)
  -> corrected_memory
  -> repair_guidance
  -> invocation policy
```

## V0 Open Decisions

Resolved positions:

| Decision | Choice | Reason |
|----------|--------|--------|
| First probe label scope | start with small V0 label set | avoid sparse classes and underpowered evaluation |
| `premature_extraction_error` status | first-class pipeline label | raw evidence can be lost before retrieval begins |
| Subagent Judge role | baseline plus high-recall monitor | useful for cheap trigger, not causal attribution |
| First implementation target | standalone research harness | best for reproducible perturbations and metrics |

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

Implementation note: keep adapter boundaries in the harness so V1 can become a pluggable memory-agent component.

## Post-Repair Context Replay

New decision from inspecting the prototype, PRD, and tracer-bullet docs: V0 must include a repaired-context retest.

Existing docs already mentioned targeted repair, ECS, and future Failure Memory retrieval, but those are not enough. The missing check is whether the original failed query recovers when CMD rebuilds the context from the repair.

Required state transition:

```text
AttributionAssigned
-> ECSDrafted
-> RepairedContextBuilt
-> PostRepairRetested
-> RepairValidated / RepairFailed
-> FutureCaseGuided
```

Why it matters:

- attribution alone only says where the failure came from;
- ECS alone only says what repair should be applied;
- future retrieval alone only tests later transfer;
- repaired-context replay tests whether the proposed correction actually fixes the observed failure.

Metrics:

- post-repair answer score;
- post-repair evidence score;
- repair success rate;
- regression risk;
- token cost;
- improvement over generic hard-case update.

## 2026-05-09 Signals

| Source | Signal | CMD Implication |
|--------|--------|-----------------|
| arXiv:2605.06527 STALE | implicit conflicts make old memories invalid without explicit negation | V1 should add state adjudication; V0 still excludes item labels |
| arXiv:2605.02199 MEMAUDIT | write-time memory packages can be audited with exact package oracles | V0 synthetic cases should resemble auditable packages |
| arXiv:2605.05583 BeliefMem | deterministic memories can self-reinforce errors under uncertainty | future bad-memory-item diagnosis should handle probabilistic beliefs |
| arXiv:2605.04811 TreeMem | memory builder/summarizer/retriever can receive operation-specific credit | replay deltas can become learned credit signals after V0 |
| arXiv:2605.06132 MemReranker | retrieval reranking needs temporal/causal reasoning | CMD must compare retrieval reranker fixes against extraction/injection fixes |
| arXiv:2605.05704 SafeHarbor + arXiv:2605.02812 Agent Worms | memory guardrails and persistent state can become attack surfaces | Subagent Judge Monitor needs strict anti-leak boundaries |
| arXiv:2605.05716 CCI | stacking components can hurt agents | keep V0 as standalone harness with small label set |

## MVP Boundary Invariants

- Use `CMD-Audit` for the research harness and `CMD-Skill Adapter` for later deployment language.
- Subagent Judge Monitor can trigger replay but must not emit final attribution, ECS, gold answer, full failed trace, or memory writes.
- V0 excludes bad-memory-item labels from attribution, even though notes may mention wrong or stale memory in ECS text.
- V0 must still run Post-Repair Context Replay before claiming repair value.

## 2026-05-09 Targeted Metabolism: Retrieval Baseline Issue

User judgment: V0 当前简单是合理的，但需要明确后续 issue：**Strengthen retrieval baselines and evidence scoring**。

Metabolized position:

- first minimal V0 may keep fixture-controlled `retrieved_memory_ids`;
- V0.5 should add lexical/BM25, vector, hybrid, and hybrid+rerank retrievers;
- retrieval runs should emit ranked traces with `memory_id`, `rank`, `score`, `token_cost`, `retrieved_text`, and `matched_gold_evidence_units`;
- evidence scoring should expand to Recall@k, MRR, nDCG, Precision@k, context noise ratio, and answer accuracy/F1;
- hard negatives should cover same-entity confusion, temporal conflict, paraphrase, multi-hop evidence, stale memory, and compression-loss.

Boundary invariant: if raw-event oracle recovers the evidence but extracted-memory oracle cannot, prefer `premature_extraction_error` over `retrieval_error`. Stronger retrievers should not hide evidence lost before retrieval.

## 2026-05-10 Metabolism Day 0 Signals

Broad survey across arxiv + GitHub (27 papers, 10 repos). Key signals:

| Source | Signal | CMD Implication |
|--------|--------|-----------------|
| arXiv:2603.07670 Agent Memory Survey | formalizes agent memory as write-manage-read loop with 5 mechanism families; open challenges: causally grounded retrieval, trustworthy reflection, continual consolidation | validates CMD's pipeline labels map; confirms no existing counterfactual attribution framework |
| arXiv:2603.14597 D-MEM | Fast/Slow memory routing via RPE gating; Critic Router scores Surprise and Utility | RPE-based anomaly detection could serve as cheap pre-filter before expensive CMD replay |
| arXiv:2510.02373 A-MemGuard | dual-memory with separate "lesson" store; consensus-based anomaly detection; 95% attack reduction | production-validates CMD's Failure Memory concept and leak-safe monitor pattern |
| arXiv:2603.10600 Trajectory-Informed | Decision Attribution Analyzer pinpoints failure-causing decisions; 149% gain on complex tasks | closest existing work to CMD attribution, but uses observational not counterfactual evidence |
| arXiv:2604.27283 RSCB-MC | risk-sensitive retrieval with abstention as first-class action; 0.0% false-positive rate | retrieval_error should distinguish "wrong memory" from "right memory injected unsafely" |
| arXiv:2406.13408 SQLFixAgent | failure memory reflection for repair selection; similar repair retrieval | concrete domain instance of CMD's Failure Memory recurrence loop (issue 0007) |
| arXiv:2512.21354 Reflection-Driven Control | evolving reflective memory stores repair examples; AAAI 2026 TrustAgent | validates repair-triggered memory retrieval; lacks CMD's counterfactual validation |
| arXiv:2601.06636 MedEinst | three-level causal hierarchy (association/intervention/counterfactual); CGME memory evolution | validates CMD's counterfactual approach over observational diagnosis |
| arXiv:2604.12231 Thought-Retriever | intermediate reasoning as self-evolving memory; 7.6% F1 gain | supports Verbatim Event Oracle premise: intermediate artifacts carry recoverable evidence |
| arXiv:2310.08560 MemGPT | foundational OS-inspired memory tiering; virtual context management | the architecture CMD audits; pipeline labels map to MemGPT's tier operations |
| arXiv:2603.18330 MemArchitect | policy-driven memory governance with decay/conflict/privacy controls | production target for CMD's ECS repair guidance |
| arXiv:2604.01599 ByteRover | agent-native curation: same LLM writes and retrieves | tight write-retrieval coupling is exactly the failure mode CMD disentangles |
| arXiv:2604.04853 MemMachine | ground-truth-preserving three-tier memory; 93% LongMemEval | validates compression_error label: token reduction can lose critical evidence |
| GitHub: acailic/agent_debugger | local-first agent debugger with replay, failure memory, decision tree visualization | validates engineering demand; CMD differs in automated counterfactual attribution vs human-in-the-loop |
| GitHub: Exploreunive/agentlens | root-cause debugging with memory attribution | emerging tooling validates memory attribution as a need |

## Engineering Ecosystem Signal

The GitHub landscape shows a clear gap: multiple agent observability tools (LangSmith, OpenTelemetry, Sentry) but none do counterfactual memory replay. Peaky Peek (agent_debugger) comes closest with checkpoint replay and failure memory, but its replay is state restoration, not operation-level intervention. AgentLens claims memory attribution but appears observational. This confirms CMD's unique position: automated counterfactual attribution with Recovery Gain scoring has no existing open-source implementation.

## Updated Competitive Landscape

| Approach | Evidence Type | Attribution Granularity | Automated |
|----------|--------------|------------------------|-----------|
| Subagent Judge | observational (same trace) | free-form explanation | yes |
| Trajectory-Informed (2603.10600) | observational (execution trace) | decision-level | yes |
| Peaky Peek | interactive (checkpoint + human) | visual debugging | no (HITL) |
| D-MEM (2603.14597) | RPE signal | binary surprise flag | yes (no attribution) |
| MemoScope (eth-jashan) | observational (event capture + visualization) | retrieval-level (score/rank) | yes (no attribution) |
| ErrorProbe (2604.17658) | observational (backward trace) | step-level | yes (no counterfactual) |
| MemEvoBench (2604.15774) | benchmark detection | item-level (contamination) | yes (no attribution) |
| **CMD (proposed)** | **counterfactual (replay intervention)** | **operation-level (6 labels)** | **yes** |

## 2026-05-11 Metabolism Day 1 Signals

Incremental survey (2026-05-06 ~ 2026-05-11 window), 9 new papers + 1 GitHub repo found, 6 directly CMD-relevant.

| Source | Signal | CMD Implication |
|--------|--------|-----------------|
| arXiv:2604.25161 Capability-Failure-Attribution (ACL 2026) | capability oracles attribute VLN failures to perception/memory/planning/decision | validates failure attribution trend; capability-level is coarser than operation-level; CMD's replay portfolio provides finer granularity |
| arXiv:2604.17658 ErrorProbe | step-level error localization via verified episodic memory; only stores patterns confirmed by executable evidence | independently validates CMD's Failure Memory pattern: store only verified ECS records after Post-Repair Context Replay |
| arXiv:2604.15774 MemEvoBench | first benchmark for memory mis-evolution; 7 domains, 36 risk types; static defenses insufficient | validates V1 `item_poisoned` and `item_stale` labels; CMD's ECS repair loop addresses the exact gap of ongoing memory correction |
| arXiv:2604.16548 Mnemonic Sovereignty Survey | 6-phase memory-lifecycle security framework; governance gap spanning 9 primitives | validates Subagent Judge Monitor leak-safety boundaries; maps to CMD's pipeline labels; governance gap = ECS repair governance opportunity |
| arXiv:2605.03312 MemFlow | intent-driven memory orchestration; Router dispatches to 3 memory tiers; ~2× accuracy on SLMs | validates `route_error` as V1 label; mismatched memory operations confirmed as real failure mode; Router is natural target for Oracle Route replay |
| arXiv:2604.20006 Memora (ACL 2026 Findings) | FAMA metric penalizes obsolete memory use; agents "frequently reuse invalid memories" | validates `item_stale` as critical V1 label; FAMA can serve as external validation for CMD's stale-item repair |
| arXiv:2604.27045 Dual-Stream Reconciliation | dual-stream memory (narrative vs clinical record); 13.6% error cascade from extraction loss | empirical evidence for `premature_extraction_error`: extraction-stage information loss causing downstream errors |
| arXiv:2604.20117 SCG-MEM | schema-constrained generation; formal guarantee against "structural hallucination" | introduces structural hallucination as new failure mode; potential V2 label or `reasoning_error` subtype |
| GitHub: eth-jashan/MemoScope | framework-agnostic memory debugger; capture→visualize→diff; no replay or attribution | validates memory debugging tool demand; CMD's counterfactual approach remains unique; potential V1 integration target as plugin |

## 2026-05-12 Metabolism Day 2 Signals

Incremental survey (2026-05-07 ~ 2026-05-12 window). 4 new papers found, 4 directly CMD-relevant. Notable: arxiv API unavailable during search; all discovery via OpenAlex.

| Source | Signal | CMD Implication |
|--------|--------|-----------------|
| arXiv:2605.06455 PrefixGuard | offline trace→monitor induction; prefix-risk scorer from terminal outcomes; LLM judges substantially weaker than trained monitors for online warning; observability ceiling concept | validates CMD's Subagent Judge Monitor design: rule-based over LLM-as-judge; PrefixGuard fires → CMD replays is a natural 2-tier architecture; observability ceiling maps to `anomaly_reason` enum limits |
| arXiv:2605.03228 MAGE | shadow memory abstraction for safety guardrails; proactive risk assessment before action execution; first framework to detect/mitigate long-horizon agent threats via memory architecture | validates `safety_error` as first-class V1 label; MAGE's shadow memory structurally mirrors CMD's Failure Memory (separate purpose-built store); both share "detect cheap, prevent early" pattern |
| arXiv:2605.01970 Trojan Hippo | systematic dormant payload attacks on agent memory; single untrusted tool call suffices; OpenEvolve adaptive red-teaming benchmark; capability-aware security/utility analysis | validates `item_poisoned` (V2 label) with systematic evidence; agent memory security emerges as distinct subfield alongside CMD's correctness focus |
| arXiv:2605.01386 MemORAI | dual-layer compression + provenance-enriched multi-relational graph + Dynamic Weighted PageRank; SOTA on LOCOMO/LongMemEval | direct competitor on CMD's V1 target benchmarks; provenance tracking at turn level is prerequisite for pipeline attribution — MemORAI tracks WHERE facts came from, CMD diagnoses WHICH operation lost them; should be cited as both related work and SOTA baseline |

## Updated Competitive Landscape (Day 2)

**PrefixGuard (2605.06455)** is the closest new work to CMD's monitoring layer. Key comparison:

| Dimension | PrefixGuard | CMD Subagent Judge Monitor |
|-----------|------------|---------------------------|
| Training | supervised on offline traces | rule-based (no training) |
| Input | heterogeneous agent traces | memory operation trace |
| Output | prefix-risk score | enum-locked anomaly_reason |
| After anomaly | (not addressed) | triggers counterfactual replay |
| Determinism | DFA-extractable | enum-locked |
| Evidence | terminal outcomes | replay deltas |

PrefixGuard stops at anomaly detection; CMD continues into attribution and repair. The two are complementary layers, not competitors: PrefixGuard could serve as CMD's monitor frontend, with CMD's replay engine providing the attribution backend.

**MemORAI (2605.01386)** sets a new SOTA bar on CMD's V1 evaluation benchmarks (LOCOMO, LongMemEval). Its provenance tracking is an independent validation that turn-level evidence chains matter — exactly the infrastructure CMD needs for `premature_extraction_error` diagnosis.

**MAGE (2605.03228) + Trojan Hippo (2605.01970)**: published 1 day apart, together define agent memory security as a May 2026 research frontier. CMD's current taxonomy covers correctness failures; adversarial failures (`item_poisoned`, `safety_error` exploited by attacks) represent a natural V2/V3 extension dimension.

## 2026-05-12 Dataset Design References

CMD 需要两类数据集，分别服务归因有效性实验和上下文拼接实验。5 篇论文提供了数据集设计方法论参考：

| Source | Dataset Feature | CMD Reference Value |
|--------|----------------|-------------------|
| MEMAUDIT (2605.02199) | Package-oracle protocol: pre-constructed memory packages with known correct answers | Experiment 2 perturbation injection: pre-build gold evidence packages, then controllably delete/deform at write/compress/retrieve stages |
| Memory-Probe (2603.02473) | 3×3 grid (paper); CMD implementation: 3×2 grid (3 write × 2 retrieval: cosine + BM25; dense deferred to V1 per issue 0008) | Experiment 2 baseline comparison design: grid layout of different memory systems × different retrieval methods |
| MemEvoBench (2604.15774) | 7 domains, 36 risk types of memory mis-evolution detection | Experiment 2 perturbation variant design: borrow risk type taxonomy for richer perturbation variants |
| ErrorProbe (2604.17658) | Step-level error injection into multi-agent traces, tests diagnosis accuracy | Experiment 2 error injection methodology: inject errors at specific steps → check if CMD localizes to correct step |
| MedEinst (2601.06636) | 3-level causal hierarchy (association/intervention/counterfactual) for diagnosis | Experiment 2 counterfactual case construction: association → intervention → counterfactual hierarchy |

Adaptable raw data sources: LoCoMo (long conversation history → memory units → perturbation injection), LongMemEval (QA pairs → trace evidence to original text → extraction loss scenarios), HotpotQA memory variant (multi-hop questions → delete one hop's evidence → retrieval vs reasoning boundary).

Key finding: None of the 40+ papers surveyed provides a controlled 4-mode comparison of `wrong_memory + cause + corrected_memory` vs `corrected_memory only`. This makes the 4-Mode Context Experiment a potential novelty contribution regardless of outcome.

## 2026-05-13 Metabolism Day 3 Signals

Incremental survey (2026-05-08 ~ 2026-05-13 window). 3 papers + 4 GitHub repos found, all CMD-relevant.

| Source | Signal | CMD Implication |
|--------|--------|-----------------|
| Zenodo: The Intent Gap (Yakhmi) | Real-user failure taxonomy from WildChat-1M + LMSYS-Chat-1M; frustration-signal filter; replays across 4 frontier models; "researcher-imagined benchmarks miss deployment-relevant failures" | Validates need for real-data probe cases; Intent Gap's user-facing taxonomy (sycophancy, dialogue breakdown) is complementary to CMD's memory operation taxonomy; compound taxonomy (intent gap × memory operation) is a future direction |
| Zenodo: Skill as Memory (Sarkar) | Three failure modes of document-first skill catalogs: token burn (919K tokens for 5K skills full catalog vs 369 for DB-native), retrieval slowdown (p50 87ms), invalid-skill admission (0% DB vs 97% YAML-parseable); 5,000-skill reproducible corpus | Validates CMD's Failure Memory as database-native structured records (not documents); three failure modes map to CMD: token burn → compact ECS corrected-only, retrieval slowdown → efficient keyword retrieval, invalid admission → Post-Repair Context Replay as quality gate |
| Zenodo: Agent Skills Survey (yang et al.) | Lifecycle-oriented skill taxonomy: 4 axes (representation, source, granularity, role) × 6 dimensions (construction, access, training, safety, evaluation, ecosystems) | Framework for CMD's V2 skill evolution; ECS records as repair skills in lifecycle view; compositional safety and dynamic utility evaluation are relevant to V2 design |
| GitHub: skill-everything | "Agents that never make the same mistake twice"; self-extending skills from accepted mistakes; git-versioned; multi-runtime; 84% fewer tokens | **Closest engineering analogue to CMD's Failure Memory loop**: errors→commits→skills mirrors anomaly→ECS→Failure Memory→retrieval. Key difference: human-in-the-loop PR review vs CMD's automated Post-Repair Context Replay |
| GitHub: MemoryOS | Temporal knowledge graph + hybrid vector retrieval + Ebbinghaus decay; append-only graph; 78ms queries, 9ms/msg ingest | Memory architecture reference; temporal tracking validates `item_stale`; append-only aligns with provenance tracking |
| GitHub: memory-poisoning-demo | Practical vector store poisoning via ChromaDB injection; one entry flips agent behavior; no prompt injection needed | Validates `item_poisoned` (V2) and `safety_error` (V1) with practical attack; confirms memory integrity verification is a real need |
| GitHub: portable-agent-memory | Cryptographically-verified memory transfer across heterogeneous agents | Memory portability for cross-system adapter; verification protocol could inform V2/V3 verified ECS transfer |

### Key Pattern: "Never Make the Same Mistake Twice" Convergence

Four independent sources now converge on the same loop: **detect error → diagnose → store fix → reuse**:

| Source | Detection | Diagnosis | Quality Gate | Fix Storage |
|--------|-----------|-----------|--------------|-------------|
| CMD | Subagent Judge Monitor | Counterfactual replay (6 ops) | Post-Repair Context Replay | Failure Memory (ECS) |
| skill-everything | Agent/user identifies mistake | Human root-cause analysis | Human PR review | Git-versioned skill files |
| ErrorProbe (2604.17658) | Multi-agent trace analysis | Step-level backward trace | Executable evidence | Verified episodic memory |
| SQLFixAgent (2406.13408) | SQL execution failure | Failure memory retrieval | SQL execution success | Repair example store |

CMD's differentiator in this landscape: **fully automated quality gate** (Post-Repair Context Replay) that tests the repair against the original failure, vs human PR review (skill-everything), execution success (SQLFixAgent), or trace evidence (ErrorProbe). Schema validation (Skill as Memory) is syntactic, not semantic.

### Intent Gap × Memory Operation: Compound Failure Space

The Intent Gap paper's user-facing failure taxonomy (intent gap, sycophancy, dialogue breakdown) and CMD's memory operation taxonomy (6 pipeline labels) are orthogonal dimensions. A compound taxonomy could capture failures like: "The model retrieved the correct memory and answered literally, but missed the user's intent" — which is neither an intent gap alone (memory was correct) nor a memory operation failure alone (pipeline worked). This compound space is a potential V2/V3 extension.

## 2026-05-14 Metabolism Day 4 Signals

Incremental survey (2026-05-09 ~ 2026-05-14 window). 18 new papers found, 12 with direct CMD relevance.

| Source | Signal | CMD Implication |
|--------|--------|-----------------|
| arXiv:2605.12978 Memory Becomes Faulty | **Consolidation systematically degrades useful memories**; 3 failure modes (misclassification, interference, overfit); GPT-5.4 fails on 54% of problems it solved without memory; episodic-only control competitive with consolidators; agents choose episodic-first policy when given autonomy | Validates `compression_error` + `premature_extraction_error` with controlled causal evidence. Episodic traces as ground truth for counterfactual replay. Consolidation gating principle maps to CMD's operation-level attribution: identify which consolidation step degraded which evidence. |
| arXiv:2605.07242 MEMOREPAIR | **Cascade update problem formalized**; barrier-first repair contract; affected descendants withdrawn before repair; publication selection reduced to s-t min-cut; 0% invalidated-memory exposure with complete influence provenance | Directly adjacent to CMD's targeted repair. CMD attributes root cause → MEMOREPAIR handles downstream cascade → Post-Repair Context Replay validates republication. Influence provenance tracking is infrastructure CMD currently lacks. |
| arXiv:2605.10870 Remember the Decision | **Decision-centric rate-distortion for memory**: memory quality = loss in achievable decision quality, not description fidelity. Forgetting boundary + memory-distortion frontier. DeMem refines partitions only on decision conflicts | Challenges `compression_error` framing: compression harmful only when collapsing decision-relevant distinctions. CMD's counterfactual replay could incorporate decision-centric quality measure. |
| arXiv:2605.09330 Spurious Correlations | **Spurious correlations as new memory vulnerability**: memory amplifies reliance on spurious patterns; CAMEL calibration at write+retrieval time; robust under adaptive attacks | New failure mode not in CMD's taxonomy — `spurious_correlation_error` or extends `reasoning_error`. CAMEL prevents; CMD detects when prevention failed. |
| arXiv:2605.07313 Scale-Conditioned Eval | **Evidence usability degrades as irrelevant sessions accumulate**; reliability loss is multi-phenomenon (budget-exceeding, retrieval noise, agent-dependent); usable-scale boundary concept | Validates `retrieval_error` at scale. CMD's replay must account for retrieval context size. Usable-scale boundary maps to CMD's attribution threshold. |
| arXiv:2605.13438 Cognifold | **3-layer CLS (prefrontal intent) for always-on proactive memory**; graph-topology self-organization; intent surfacing via cluster density | Proactive memory reduces failures CMD detects. Intent-layer concept informs Failure Memory context construction. |
| arXiv:2605.12493 LongMemEval-V2 | **451 questions, 5 memory abilities for web agents** (static state, dynamic tracking, workflow, gotchas, premise); AgentRunbook-C 72.5% vs RAG 48.5% | Environment gotchas and workflow knowledge categories are natural V1 probe case sources. Coding-agent oracle retriever for CMD. |
| arXiv:2605.09863 Nautilus Compass | **Black-box persona drift detection** at prompt-text layer; no LLM at index time; ROC AUC 0.83; $3.50 reproduction cost; only public memory layer without extraction | Drift detection is complementary to CMD attribution: Compass detects drift → CMD attributes cause. No-extraction ceiling at 56.6% LongMemEval. |
| arXiv:2605.09033 ShadowMerge | **Graph memory poisoning via relation-channel conflicts**: 93.8% ASR on Mem0; shares anchor+relation channel but conflicts on value; input-side defenses insufficient | Validates `injection_error` + `item_poisoned` for graph memory. Relation-channel conflict is new failure signature CMD could detect. |
| arXiv:2605.06716 Storage→Experience Survey | **Three-stage evolutionary framework**: Storage→Reflection→Experience; three drivers: consistency, dynamics, continual learning; proactive exploration + cross-trajectory abstraction as frontier | Places CMD at Reflection→Experience boundary. Three stages map to CMD error labels. Framework is descriptive, not diagnostic — CMD fills the gap. |
| arXiv:2605.12061 SAGE | **Self-evolving graph memory** with reader→writer feedback loop; 82.5/91.6 Recall@2/5 zero-shot | Reader→writer feedback structurally similar to CMD's detect→diagnose→store→reuse loop. No failure diagnosis when self-evolution itself introduces errors. |
| arXiv:2605.09942 HAGE | **RL-driven weighted graph evolution**; retrieval as sequential query-conditioned traversal | Dynamic edge weighting could inform CMD's retrieval replay scoring. |

### Key Pattern: Detection→Diagnosis→Repair Gap Is Widening

Nautilus Compass (drift detection), CAMEL (spurious pattern detection), Cognifold (proactive organization), PrefixGuard (prefix-risk scoring) — all detect anomalies but none attribute root cause or repair. MEMOREPAIR handles cascade repair but requires manual trigger (no auto-detection). The gap between detection and repair is precisely where CMD's counterfactual attribution sits.

This is now confirmed across **5 detection-only systems** and **1 repair-only system** — none combine automated detection + attribution + repair. CMD remains the only architecture spanning all three.

### Episodic Trace Anchoring: Cross-Paper Consensus

2605.12978 (episodic traces as first-class evidence, consolidation gating), 2605.10870 (memory must preserve decision-relevant distinctions), and MEMOREPAIR (retained support from pre-event valid state) independently converge: **raw episodic traces are the ground truth against which memory operations should be validated**. CMD's counterfactual replay with Verbatim Event Oracle is the only method that uses episodic traces for operation-level attribution.

### Graph Memory: Both Promise and Attack Surface

Two independent findings on graph memory within 3 days: SAGE (self-evolving graph, reader-writer feedback) and ShadowMerge (relation-channel poisoning, 93.8% ASR). Together they confirm that graph memory is the frontier for both capability (SAGE, HAGE, Cognifold) and vulnerability (ShadowMerge). CMD's `graph_error` (V1) and `item_poisoned` (V2) labels are correctly positioned.

## 2026-05-15 Metabolism Day 5 Signals

Incremental survey (2026-05-10 ~ 2026-05-15 window). 24 new papers found, 12 with direct CMD relevance. Major theme: **failure attribution is now a recognized subfield**, with 5+ distinct attribution methods published this week.

| Source | Signal | CMD Implication |
|--------|--------|-----------------|
| arXiv:2605.14865 Holistic Eval & Failure Diagnosis | Top-down agent-level diagnosis + bottom-up span-level evaluation; decomposes analysis into per-span assessments; SOTA on TRAIL (GAIA+SWE-Bench); 38% relative gain on category F1 | Closest adjacent work to CMD's attribution: span-level is coarser than operation-level; CMD's counterfactual replay provides causal evidence vs this paper's observational span assessment |
| arXiv:2605.14892 LIFE Survey | First unified survey of collaboration + failure attribution + self-evolution in multi-agent systems; 4 causally linked stages; error propagation across agents formalized | Failure attribution recognized as research subfield distinct from detection and repair; multi-agent error propagation validates need for CMD's pipeline labels to track cross-agent memory contamination |
| arXiv:2605.13077 Counterfactual Responsibility Attribution | Counterfactual reasoning for causal responsibility in multi-agent systems; Shapley value-based allocation; formal verification + strategic reasoning; retrospective (backward) responsibility | **Closest formal work to CMD's counterfactual approach.** Key difference: Shapley value (coalitional, all-agent) vs CMD's Recovery Gain (single-operation intervention, pipeline-specific). Shapley could complement CMD for multi-agent extension |
| arXiv:2605.06788 Conformal Agent Error Attribution | Conformal prediction for error attribution; finite-sample distribution-free coverage guarantees; contiguous sequence predictions for efficient recovery | Formal statistical framework for attribution that CMD currently lacks; conformal sets could bound CMD's attribution confidence; contiguous predictions align with CMD's top-2 attribution |
| arXiv:2605.07509 MASPrism | Lightweight failure attribution via prefill-stage signals (NLL + attention weights) from SLM; no decoding needed; reconstructs diagnostic prompt from token-level signals | Observational alternative to CMD's counterfactual approach; cheaper (no replay) but less causal; validates attribution as a recognized need; signal-based approach complementary to CMD's intervention-based |
| arXiv:2605.14421 MemLineage | Cryptographic provenance (RFC-6962 Merkle log, Ed25519) + derivation lineage (weighted DAG); max-of-strong-edges propagation; Untrusted-Path Persistence guarantee | Provenance infrastructure CMD currently lacks; lineage DAG could inform CMD's influence tracking (MEMOREPAIR gap). Chain-of-custody model for memory trust is novel framing |
| arXiv:2605.13941 EvolveMem | Self-evolving memory: co-evolution of stored knowledge + retrieval mechanism; LLM diagnosis module reads per-question failure logs; guarded meta-analyzer with auto-revert-on-regression | **Structurally closest to CMD's diagnosis loop.** Exposes retrieval config as action space → diagnosis → adjustment. CMD diagnoses pipeline operations; EvolveMem diagnoses retrieval configuration. Complementary layers |
| arXiv:2605.15109 Traversal Context & Provenance | Citation faithfulness as trajectory-level problem in Agentic GraphRAG; visited-but-uncited entities influence answers; cited evidence necessary but not sufficient | Validates CMD's Verbatim Event Oracle premise: what's retrieved (cited) ≠ what influenced reasoning. Traversal context as first-class evidence for attribution |
| arXiv:2605.11039 PACT | Argument-level provenance for agent security; semantic roles for tool arguments; value provenance across replanning steps; isolates LLM reasoning bottleneck | Granularity mismatch (invocation vs argument) parallels CMD's operation-level vs agent-level attribution distinction; provenance granularity matters for security and correctness |
| arXiv:2605.03482 MEMSAD | Memory poisoning as Stackelberg game; gradient coupling theorem: anomaly score gradient = retrieval objective gradient; 4× ASR increase from faithful evaluation | Formal security framework for memory poisoning; gradient coupling provides theoretical grounding for CMD's anomaly detection; validates `item_poisoned` label with game-theoretic analysis |
| arXiv:2605.08717 PROBE | Failure-anchored structured recovery for SE agents; Telemetry → Diagnosis → Guidance Gate pattern; converts heterogeneous runtime evidence into bounded recovery guidance | Same detect→diagnose→guide pattern as CMD; differs in domain (SE agents vs memory) and evidence type (runtime telemetry vs counterfactual replay) |
| arXiv:2605.08715 AgentForesight | Online auditing (prefix-level) vs post-hoc attribution; curates AFTraj-2K corpus; early decisive error detection before trajectory ends | Reframes attribution timing: online vs post-hoc. CMD currently post-hoc; online attribution is a natural V2 extension (real-time replay triggering) |
| arXiv:2605.08374 MemQ | TD(λ) eligibility traces for memory Q-values; credit propagation through provenance DAG; structural proximity replaces temporal distance; Exogenous-Context MDP formalization | Formal credit assignment over memory DAGs; CMD's Recovery Gain is single-operation; MemQ's eligibility traces could extend CMD to cascade attribution |
| arXiv:2605.08060 The Memory Curse | Expanded recall erodes cooperative intent in 18/28 model-game settings; mechanism: eroding forward-looking intent, not rising paranoia; LoRA on forward traces transfers zero-shot | Memory has negative externalities beyond correctness; CMD's repair should consider not just accuracy but cooperative/social dimensions of memory |
| arXiv:2605.09934 TRACER | Claim-level dependency structure for tool-using agents ("provenance gap"); structured provenance trees with verification predicates; enables targeted repair | Provenance gap = missing link between generated claims and supporting evidence; same problem CMD solves for memory operations; structured provenance trees could enrich CMD's ECS records |
| arXiv:2605.06365 Execution Lineage | DAG-based execution model for AI-native work; identity-based replay; stable intermediate boundaries; maintainable under change | Execution lineage as reproducible replay infrastructure; CMD's counterfactual replay could use lineage DAG for precise intervention targeting |
| arXiv:2605.08468 PYTHALAB-MERA | Validation-grounded episodic memory for coding agents; execution feedback + adaptive retrieval-action selection; delayed credit assignment | Execution feedback as validation signal mirrors Post-Repair Context Replay; delayed credit assignment problem maps to CMD's multi-step attribution challenge |
| arXiv:2605.11882 FATE | On-policy self-evolution via failure trajectories; transforms verifier-scored failures into repair supervision without expert demonstrations | Repair-from-failure pattern matches CMD's ECS generation; on-policy approach (same model proposes + repairs) vs CMD's audit-then-adapter approach |

### Key Pattern 1: Failure Attribution Subfield Emergence

This week alone, 5+ distinct papers propose failure attribution methods for agent systems:

| Method | Evidence Type | Granularity | Formal Guarantees | Automated |
|--------|--------------|-------------|-------------------|-----------|
| CMD (ours) | Counterfactual replay | Operation-level (6-8 labels) | Recovery Gain Δk | Yes |
| 2605.14865 HolisticEval | Observational span assessment | Span-level | None (benchmark SOTA) | Yes |
| 2605.13077 CounterfactualResp | Counterfactual (Shapley) | Agent-level | Shapley axioms (fairness, consistency) | Yes (verification) |
| 2605.06788 ConformalAttr | Observational (trajectory) | Step-level (contiguous) | Finite-sample coverage guarantee | Yes |
| 2605.07509 MASPrism | Prefill signals (NLL+attn) | Token/step-level | None (heuristic) | Yes |
| 2605.14892 LIFE | Survey taxonomy | N/A | N/A | N/A |

**CMD's remaining differentiators:**
1. **Operation-level granularity**: Pipeline operations (write/compress/extract/retrieve/inject/route/reason), finer than agent-level or span-level
2. **Counterfactual intervention evidence**: Recovery Gain from replay, not observational signal analysis
3. **Automated quality gate**: Post-Repair Context Replay validates fixes before deployment
4. **Full loop**: Detection → Attribution → ECS → Repair → Failure Memory

**Risk**: Attribution space is getting crowded. CMD must clearly articulate why operation-level counterfactual replay produces more actionable diagnoses than lighter alternatives (MASPrism's signals, ConformalAttr's sets).

### Key Pattern 2: Provenance Infrastructure Convergence

MemLineage (cryptographic), TRACER (claim-level), PACT (argument-level), Execution Lineage (DAG-based), MemQ (credit propagation DAG) — provenance tracking is emerging as fundamental infrastructure across security, correctness, and reproducibility. CMD currently lacks provenance tracking; MEMOREPAIR's cascade repair gap (influence provenance) could be filled by integrating lineage DAGs.

### Key Pattern 3: Diagnosis-Driven Self-Evolution

EvolveMem (diagnosis → config adjustment → validation) and MemQ (credit propagation → Q-value updates) independently validate the diagnosis→evolution loop. Together with CMD (detect→diagnose→store→reuse) and FATE (failure→repair supervision), four systems now implement diagnosis-driven self-improvement. CMD's differentiation: operation-level diagnosis granularity + automated semantic quality gate.

### Key Pattern 4: Memory Externalities

The Memory Curse (2605.08060) provides the first controlled evidence that memory has negative social externalities beyond correctness: expanded recall degrades cooperation. MEMSAD (2605.03482) formalizes security externalities. Together they suggest CMD's repair should consider multi-dimensional impact (correctness + cooperation + security), not just single-task accuracy.

### Updated Competitive Landscape (Day 5)

**Attribution methods comparison (cumulative 65+ papers, 14+ repos):**

CMD's counterfactual replay with Recovery Gain remains the only method that:
1. Operates at memory pipeline operation granularity (not agent/span/step)
2. Uses intervention-based (not observational) evidence
3. Includes automated repair validation (Post-Repair Context Replay)
4. Spans detection → attribution → repair → storage

**New adjacency**: 2605.13077's Shapley-value counterfactual responsibility is the closest formal work. A potential integration: CMD's Recovery Gain for single-operation attribution + Shapley value for multi-agent responsibility allocation.

**Provenance gap**: 5 provenance papers this week, none connecting provenance to failure attribution. CMD + MemLineage/PACT integration is a natural V2 extension.

## 2026-05-18 Metabolism Day 6 Signals

Incremental survey (2026-05-13 ~ 2026-05-18 window). Low-volume weekend: 1 key paper + 2 peripherally relevant found. arxiv API down throughout; discovery via OpenAlex + arxiv page probing.

| Source | Signal | CMD Implication |
|--------|--------|-----------------|
| Zenodo: LOBSTER-Bench (2026-05-16) | First benchmark for long-lived agent observability; 6 dimensions: temporal persistence, cognitive telemetry coverage, relational observability, collective task assay, cognitive load management, governance & auditability; real data from 21-agent system over 7 days — 27,788 telemetry rows, 6,354 wagers, 2,546 template-violation events; emergent cascade: "two individually correct subsystems composed into serial degradation of ten agents under thirty-second mutual-templating signal" | **Validates CMD's core premise with real operational data.** LOBSTER-Bench's cascade failure is exactly the type of multi-operation failure CMD diagnoses. The 6 dimensions map to CMD's pipeline labels: cognitive telemetry → `reasoning_error`, temporal persistence → `item_stale`/`compression_error`, relational observability → `graph_error`. Governance & auditability dimension = CMD's Post-Repair Context Replay as audit trail |
| arXiv:2605.15000 Premature Closure | LLMs commit to conclusions before sufficient information; 55-81% false-action rate when correct answer removed; safety prompting reduces but doesn't eliminate; residual failure "highlights need to evaluate whether LLMs know when not to answer" | Maps to CMD's `reasoning_error`: agent reasons over incomplete evidence. CMD's Evidence-Given Reasoning replay detects premature closure by giving agent the missing evidence and measuring recovery. Complementary: premature closure = when to abstain; CMD = why answer was wrong |
| arXiv:2605.15400 IBTS | Influence-based team steering for multi-agent coordination; zero-shot coordination with partner diversity; first 30-subject HMT study with 2 humans + 1 machine | Some relevance to multi-agent CMD extension (Shapley inter-agent + Recovery Gain intra-agent). Influence shaping concept aligns with CMD's counterfactual intervention philosophy |

### Key Takeaway: Observability as Infrastructure

LOBSTER-Bench frames observability as a benchmark dimension, not an afterthought. Its core argument — "temporal depth is not elapsed runtime, it is the unfakeable trace of observation, revision, and emergent failure" — directly supports CMD's episodic trace anchoring principle (Decision 20). The emergent cascade failure (2 correct subsystems → 10 degraded agents) is a concrete instance of the cascade repair problem MEMOREPAIR formalizes and CMD diagnoses.

LOBSTER-Bench and CMD are complementary: LOBSTER-Bench measures whether a system CAN be observed; CMD provides the attribution mechanism for WHAT to observe when it fails.

## 2026-05-19 Metabolism Day 7 Signals

High-volume post-weekend. 4 papers + 5 repos. Central theme: **counterfactual convergence at multiple granularities**.

| Source | Signal | CMD Implication |
|--------|--------|-----------------|
| TraceAudit (github, AAAI 2027) | Chunk-level counterfactual auditing of RAG; 3 intervention modes + 3 operators; URR metric; pre-registered hypotheses | ⚠️ **Direct counterfactual competitor.** Chunk-level vs CMD's operation-level. Audit-report vs CMD's diagnose-repair. Validates counterfactual as converging paradigm |
| VerifyMAS (2605.17467) | Agent-level hypothesis verification for MAS failure attribution; error-first approach against full trajectories; Aegis-Bench + Who&When eval | Agent-level vs CMD's operation-level. Verification-based (observational) vs CMD's replay-based (causal). Two independent attribution taxonomies converging |
| MemRepair (2605.17444) | 3-layer hierarchical repair memory (History-Fix/Security-Pattern/Refinement-Trajectory); failure-to-success trajectory reuse; SOTA on 3 benchmarks | Validates CMD's ECS store-and-retrieve paradigm. Hierarchical architecture reference for CMD Failure Memory V2 |
| SE-GA (2605.16883, ICML 2026) | Hierarchical episodic/semantic/experiential memory + self-evolution pipeline; ScreenSpot 89%, AndroidControl-High 75.8% | Memory architecture reference for CMD V2 skill evolution |
| DiagEval (2605.17439) | Trajectory-conditioned diagnosis via targeted probes on failed rollouts; 45.6-62.1% misattribution recovery | Methodological parallel: failure trajectory → probes → attribution. Validates CMD's replay-based diagnostic approach |
| trace-mem (github) | Counterfactual ingestion gate ("admit only if accuracy improves"); HMAC-signed provenance; tamper detection | Preventive counterfactual gating complements CMD's retrospective counterfactual replay. Full prevention→diagnosis spectrum |
| RecMem (github, ACL 2026) | Recurrence-based consolidation; defers LLM extraction until semantic recurrence; multi-facet extraction | Validates `compression_error` (eager waste) and `premature_extraction_error` (single-facet loss). Prevention approach to problems CMD diagnoses |

### Key Takeaway: Counterfactual Convergence + CMD's Operation-Level Niche

Three independently developed counterfactual attribution systems found within a 2-week window (TraceAudit chunk-level, VerifyMAS agent-level, CMD operation-level). Counterfactual intervention is converging as the standard methodological foundation. CMD's unique operation-level granularity is its differentiator — no other system targets memory pipeline operations. Must ship paper with clear positioning along: (1) granularity (operation is unique), (2) evidence type (causal replay vs observational verification vs chunk ablation), (3) full loop scope (diagnose→repair→validate→store — competitors do 1-2 of these).

The gap between "prevention" (RecMem, trace-mem) and "diagnosis" (CMD, VerifyMAS, TraceAudit) suggests a natural integration: preventive counterfactual gates reduce the attribution search space, while retrospective counterfactual replay handles what slips through.
