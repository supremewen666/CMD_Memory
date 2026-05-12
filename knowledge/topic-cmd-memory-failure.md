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
| Memory-Probe (2603.02473) | 3×3 grid: 3 write strategies × 3 retrieval methods on LoCoMo | Experiment 2 baseline comparison design: grid layout of different memory systems × different retrieval methods |
| MemEvoBench (2604.15774) | 7 domains, 36 risk types of memory mis-evolution detection | Experiment 2 perturbation variant design: borrow risk type taxonomy for richer perturbation variants |
| ErrorProbe (2604.17658) | Step-level error injection into multi-agent traces, tests diagnosis accuracy | Experiment 2 error injection methodology: inject errors at specific steps → check if CMD localizes to correct step |
| MedEinst (2601.06636) | 3-level causal hierarchy (association/intervention/counterfactual) for diagnosis | Experiment 2 counterfactual case construction: association → intervention → counterfactual hierarchy |

Adaptable raw data sources: LoCoMo (long conversation history → memory units → perturbation injection), LongMemEval (QA pairs → trace evidence to original text → extraction loss scenarios), HotpotQA memory variant (multi-hop questions → delete one hop's evidence → retrieval vs reasoning boundary).

Key finding: None of the 40+ papers surveyed provides a controlled 4-mode comparison of `wrong_memory + cause + corrected_memory` vs `corrected_memory only`. This makes the 4-Mode Context Experiment a potential novelty contribution regardless of outcome.
