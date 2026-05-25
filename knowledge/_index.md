# CMD Core Knowledge Index

检索日期: 2026-05-10

This index intentionally points only to the current CMD-Audit line. Direction-discovery side branches and broad survey dumps were removed to improve retrieval precision.

## Core Reading Path

- `current-memory.md` - Compressed active memory for CMD-Audit.
- `topic-cmd-memory-failure.md` - CMD-specific memory failure diagnosis topic.
- `../cmd_innovation_core/CONTEXT.md` - Domain vocabulary and boundary rules.
- `../cmd_innovation_core/prd/cmd_minimal_probe_prd.md` - Current PRD.
- `../cmd_innovation_core/issues/README.md` - Local issue tracker overview.
- `../cmd_innovation_core/issues/0003-counterfactual-attribution-table-implementation-details.md` - Active issue 0003 implementation skeleton.
- `../cmd_innovation_core/tdd/cmd_tracer_bullets.md` - Behavior-first implementation sequence.
- `../cmd_innovation_core/prototypes/cmd_probe_logic_prototype.md` - Throwaway logic prototype brief.
- `../PLAN.md` - Current full REPAIR + code + cleanup execution plan.
- `../REPAIR.md` - Decision 34 paste-ready repair specification and cross-reference index.

## Core Plans

- `../cmd_innovation_core/plans/direction_01_counterfactual_memory_debugger.md` - Selected CMD direction summary.
- `../cmd_innovation_core/plans/direction_01_research_plan.md` - Full CMD research plan.
- `../cmd_innovation_core/plans/cmd_research_plan_and_roadmap.md` - Complete CMD research plan and path map.
- `../cmd_innovation_core/plans/cmd_research_plan_and_roadmap.zh.md` - Chinese CMD research plan and path map.
- `../cmd_innovation_core/plans/cmd_open_decisions.md` - Resolved V0 CMD design decisions.
- `../cmd_innovation_core/issues/0008-strengthen-retrieval-baselines-and-evidence-scoring.md` - V0.5 issue for real retrievers, ranked evidence traces, richer retrieval metrics, and hard negatives.

## Core Hypotheses

- `../ideas/hyp-001.md` - Original CMD hypothesis.
- `../ideas/hyp-005.md` - Verbatim Event Oracle boundary.
- `../ideas/hyp-006.md` - CMD vs subagent judge.
- `../ideas/hyp-007.md` - CMD-Audit first, CMD-Skill Adapter later.
- `../ideas/hyp-008.md` - Post-Repair Context Replay as a V0 gate.
- `../ideas/hyp-009.md` - MEMAUDIT-style package-oracle probes.
- `../ideas/hyp-010.md` - Strong retrieval baselines and evidence metrics.
- `../ideas/hyp-011.md` - RPE as cheap anomaly pre-filter for Subagent Judge Monitor.
- `../ideas/hyp-012.md` - Verified Episodic Memory as cross-domain convergence pattern.
- `../ideas/hyp-013.md` - PrefixGuard as CMD Subagent Judge Monitor replacement frontend (two-tier architecture).

## Retained Reference Notes

- `../reference_notes/paper_2601_01885.md` - AgeMem: operation-level memory policy.
- `../reference_notes/paper_2601_02553.md` - SimpleMem: memory compression.
- `../reference_notes/paper_2602_02474.md` - MemSkill: hard-case memory skill evolution.
- `../reference_notes/paper_2602_06025.md` - BudgetMem: query-aware memory routing.
- `../reference_notes/paper_2604_01007.md` - Omni-SimpleMem: failure-driven memory search.
- `../reference_notes/paper_2605_02199.md` - MEMAUDIT: package-oracle audit protocol.
- `../reference_notes/paper_2605_02812.md` - Persistent memory re-entry risks.
- `../reference_notes/paper_2605_03354.md` - Agent memory operation diagnosis signals.
- `../reference_notes/paper_2605_03675.md` - MEMTIER: tiered memory bottlenecks.
- `../reference_notes/paper_2605_04264.md` - Governed correction and provenance.
- `../reference_notes/paper_2605_04811.md` - TreeMem: future credit-assignment direction.
- `../reference_notes/paper_2605_04897.md` - Storage vs memory boundary.
- `../reference_notes/paper_2605_05583.md` - BeliefMem: future item-state adjudication.
- `../reference_notes/paper_2605_05704.md` - SafeHarbor: monitor safety boundary.
- `../reference_notes/paper_2605_05716.md` - Cross-component interference.
- `../reference_notes/paper_2605_06132.md` - MemReranker: retrieval baseline strengthening.
- `../reference_notes/paper_2605_06527.md` - STALE: future bad-memory-item labels.
- `../reference_notes/paper_2603_07670.md` - Agent memory survey: write-manage-read loop.
- `../reference_notes/paper_2603_14597.md` - D-MEM: RPE-gated Fast/Slow memory routing.
- `../reference_notes/paper_2510_02373.md` - A-MemGuard: proactive memory defense with lesson memory.
- `../reference_notes/paper_2603_10600.md` - Trajectory-Informed Memory: Decision Attribution Analyzer.
- `../reference_notes/paper_2604_27283.md` - RSCB-MC: risk-sensitive retrieval, abstention as safety action.
- `../reference_notes/paper_2406_13408.md` - SQLFixAgent: failure memory reflection for repair.
- `../reference_notes/paper_2512_21354.md` - Reflection-Driven Control: evolving reflective memory.
- `../reference_notes/paper_2601_06636.md` - MedEinst: counterfactual differential diagnosis benchmark.
- `../reference_notes/paper_2604_12231.md` - Thought-Retriever: self-evolving long-term memory.
- `../reference_notes/paper_2310_08560.md` - MemGPT: foundational virtual context management.
- `../reference_notes/paper_2603_18330.md` - MemArchitect: policy-driven memory governance.
- `../reference_notes/paper_2603_21272.md` - Library Theorem: formal O(log N) indexed memory bounds.
- `../reference_notes/paper_2604_01599.md` - ByteRover: agent-native hierarchical memory.
- `../reference_notes/paper_2604_04853.md` - MemMachine: ground-truth-preserving memory.
- `../reference_notes/paper_2605_01688.md` - GRAVITY: structured anchoring for conversational memory.
- `../reference_notes/paper_2604_09747.md` - ADAM: privacy extraction attack on agent memory.
- `../reference_notes/github_agent_debugger.md` - Peaky Peek: local-first agent debugger with replay.
- `../reference_notes/github_agentlens.md` - AgentLens: memory attribution and root-cause debugging.

## 2026-05-11 Metabolism Day 1 Additions

- `../reference_notes/paper_2604_25161.md` - Capability-oriented failure attribution for VLN agents (ACL 2026).
- `../reference_notes/paper_2604_15774.md` - MemEvoBench: first benchmark for memory mis-evolution in LLM agents.
- `../reference_notes/paper_2604_16548.md` - Survey: security of long-term memory, mnemonic sovereignty.
- `../reference_notes/paper_2604_17658.md` - ErrorProbe: self-improving error diagnosis in multi-agent systems.
- `../reference_notes/paper_2605_03312.md` - MemFlow: intent-driven memory orchestration for SLM agents.
- `../reference_notes/paper_2604_20006.md` - Memora: long-term memory benchmark with forgetting-aware metric (ACL 2026 Findings).
- `../reference_notes/paper_2604_27045.md` - Dual-stream memory and reconciliation for health coaching agents.
- `../reference_notes/paper_2604_20117.md` - SCG-MEM: schema-constrained generation for agent memory.
- `../reference_notes/github_memoscope.md` - MemoScope: framework-agnostic AI agent memory debugger.

## 2026-05-11 V1 Planning Additions

- `../reference_notes/github_mem0.md` - mem0: universal memory layer (55k stars), first CMD-Skill Adapter target.
- `../reference_notes/paper_2603_02473_memory_probe.md` - Memory-Probe: write vs retrieval diagnostic framework, closest adjacent work to CMD.
- `../cmd_innovation_core/plans/cmd_open_decisions.md` - New Decisions 13-16: V1 label order, mem0 adapter target, single-paper scope, RPE late V1. Decision 17: Failure Memory context construction mode. Decision 28: Provenance architecture (Execution Lineage DAG + trace-mem citation).
- `../cmd_innovation_core/issues/0017-provenance-tracking-execution-lineage-dag.md` - Issue 0017 provenance tracking specification (9 ACs, TDD Cycle 23).
- `../cmd_innovation_core/plans/cmd_research_plan_and_roadmap.zh.md` - Section 19: single-paper scope, V1/V2 roadmap, updated claims and timeline.

## 2026-05-12 Metabolism Day 2 Additions

- `../reference_notes/paper_2605_06455.md` - PrefixGuard: trace-to-monitor framework, online failure-warning from agent traces, validates rule-based monitor over LLM judge.
- `../reference_notes/paper_2605_03228.md` - MAGE: shadow memory abstraction for safety guardrails, proactive risk assessment, validates `safety_error` V1 label.
- `../reference_notes/paper_2605_01970.md` - Trojan Hippo: dormant payload attacks on agent memory, validates `item_poisoned` V2 label, adaptive red-teaming benchmark.
- `../reference_notes/paper_2605_01386.md` - MemORAI: SOTA graph memory on LOCOMO/LongMemEval, provenance tracking + dual-layer compression + Dynamic Weighted PageRank.

## 2026-05-13 Metabolism Day 3 Additions

- `../reference_notes/paper_2605_intent_gap.md` - Intent Gap: real-user failure taxonomy from WildChat-1M + LMSYS-Chat-1M, validates need for real-data probe cases, orthogonal dimension to CMD's memory operation taxonomy.
- `../reference_notes/paper_2605_skill_as_memory.md` - Skill as Memory: database-native skill storage for agents, three failure modes of document-first catalogs, validates CMD's compact ECS storage and Post-Repair quality gate.
- `../reference_notes/paper_2605_agent_skills_survey.md` - Agent Skills Survey: lifecycle-oriented skill taxonomy (4 axes × 6 dimensions), framework for CMD's V2 skill evolution.
- `../reference_notes/github_skill_everything.md` - skill-everything: "agents that never make the same mistake twice", closest engineering analogue to CMD's Failure Memory loop, human-in-the-loop PR review quality gate.
- `../reference_notes/github_memoryos.md` - MemoryOS: temporal knowledge graph + hybrid retrieval + Ebbinghaus decay, architecture reference for item_stale and provenance tracking.
- `../reference_notes/github_portable_agent_memory.md` - portable-agent-memory: cryptographically-verified memory transfer, portability protocol for cross-system adapter.
- `../reference_notes/github_memory_poisoning_demo.md` - memory-poisoning-demo: practical vector store poisoning PoC, validates item_poisoned and safety_error attack surface.

- `../ideas/hyp-014.md` - "Never make the same mistake twice" convergence: four-source independent validation of CMD's Failure Memory loop; Post-Repair Context Replay as only fully automated semantic quality gate.

## 2026-05-14 Metabolism Day 4 Additions

- `../reference_notes/2605.12978.md` - Memory Becomes Faulty: consolidation systematically degrades useful memories; 3 failure modes; episodic traces as first-class evidence.
- `../reference_notes/2605.07242.md` - MEMOREPAIR: barrier-first cascade repair; s-t min-cut publication selection; 0% invalidated-memory exposure.
- `../reference_notes/2605.10870.md` - Decision-Centric Memory: rate-distortion framework; memory quality = decision quality loss.
- `../reference_notes/2605.09330.md` - Spurious Correlations: memory amplifies spurious patterns; CAMEL calibration.
- `../reference_notes/2605.07313.md` - Scale-Conditioned Eval: evidence usability degrades as irrelevant sessions accumulate.
- `../reference_notes/2605.13438.md` - Cognifold: 3-layer CLS always-on proactive memory; graph-topology self-organization.
- `../reference_notes/2605.12493.md` - LongMemEval-V2: 451 questions, 5 memory abilities for web agents.
- `../reference_notes/2605.09863.md` - Nautilus Compass: black-box persona drift detection; no LLM at index time.
- `../reference_notes/2605.09033.md` - ShadowMerge: graph memory poisoning via relation-channel conflicts; 93.8% ASR.
- `../reference_notes/2605.06716.md` - Storage→Experience Survey: 3-stage evolutionary framework for agent memory.
- `../reference_notes/2605.12061.md` - SAGE: self-evolving graph memory with reader→writer feedback loop.

- `../ideas/hyp-015.md` - Episodic-Trace Anchored Cascade Repair: CMD attribution + MEMOREPAIR cascade handling + episodic trace ground truth.

## 2026-05-15 Metabolism Day 5 Additions

- `../reference_notes/2605.14865.md` - Holistic Eval & Failure Diagnosis: span-level diagnosis, closest adjacent work to CMD operation-level attribution.
- `../reference_notes/2605.14892.md` - LIFE Survey: first unified survey of failure attribution + self-evolution in multi-agent systems.
- `../reference_notes/2605.13077.md` - Counterfactual Responsibility Attribution: Shapley-value counterfactual reasoning, closest formal work to CMD.
- `../reference_notes/2605.06788.md` - Conformal Agent Error Attribution: finite-sample coverage guarantees for error localization.
- `../reference_notes/2605.07509.md` - MASPrism: lightweight failure attribution via prefill-stage signals.
- `../reference_notes/2605.14421.md` - MemLineage: cryptographic provenance + derivation lineage for agent memory.
- `../reference_notes/2605.13941.md` - EvolveMem: self-evolving memory with LLM diagnosis module.
- `../reference_notes/2605.15109.md` - Traversal Context and Provenance in Agentic GraphRAG.
- `../reference_notes/2605.11039.md` - PACT: argument-level provenance for agent security.
- `../reference_notes/2605.03482.md` - MEMSAD: gradient-coupled anomaly detection for memory poisoning.
- `../reference_notes/2605.08717.md` - PROBE: failure-anchored structured recovery for SE agents.
- `../reference_notes/2605.08715.md` - AgentForesight: online auditing for early failure prediction.
- `../reference_notes/2605.08374.md` - MemQ: TD(λ) credit propagation through provenance DAGs.
- `../reference_notes/2605.08060.md` - The Memory Curse: expanded recall erodes cooperative intent.
- `../reference_notes/2605.09934.md` - TRACER: verifiable generative provenance for tool-using agents.
- `../reference_notes/2605.06365.md` - Execution Lineage: DAG-based replay for reproducible AI-native work.
- `../reference_notes/2605.08468.md` - PYTHALAB-MERA: validation-grounded episodic memory for coding agents.
- `../reference_notes/2605.11882.md` - FATE: on-policy self-evolution via failure trajectories.

- `../ideas/hyp-016.md` - Operation-Level Counterfactual Attribution as Differentiator in Crowding Subfield.

## 2026-05-18 Metabolism Day 6 Additions

- `../reference_notes/zenodo_lobster_bench.md` - LOBSTER-Bench: long-lived agent observability benchmark, validates CMD's premise with real operational data.
- `../reference_notes/2605.15000.md` - Premature Closure in LLMs: inappropriate commitment under uncertainty, maps to reasoning_error.

## 2026-05-19 Metabolism Day 7 Additions

### Papers
- `../reference_notes/2605.17467_verifymas.md` - VerifyMAS: hypothesis verification for failure attribution in LLM multi-agent systems. Agent-level attribution via error-first verification against full trajectories.
- `../reference_notes/2605.17444_memrepair.md` - MemRepair: hierarchical memory (3-layer) for agentic vulnerability repair. Failure-to-success trajectory reuse validates ECS paradigm.
- `../reference_notes/2605.16883_sega.md` - SE-GA: memory-augmented self-evolution for GUI agents (ICML 2026). Hierarchical memory + iterative self-improvement.
- `../reference_notes/2605.17439_diageval.md` - DiagEval: trajectory-conditioned diagnosis for GUI-agent evaluation. Reuses failure trajectories for targeted diagnostic probes.

### GitHub Repos
- `../reference_notes/github_traceaudit.md` - TraceAudit: counterfactual auditing of agentic RAG. Chunk-level counterfactual interventions, pre-registered hypotheses, AAAI 2027 target.
- `../reference_notes/github_tracemem.md` - trace-mem: verifiable memory with HMAC-signed citations + counterfactual admission gate. Preventive counterfactual gating.
- `../reference_notes/github_recmem.md` - RecMem: recurrence-based memory consolidation (ACL 2026 Findings). Validates `compression_error` and `premature_extraction_error`.
- `../reference_notes/github_debugmind.md` - DebugMind: experiential memory for bug diagnosis. Engineering analogue of CMD Failure Memory.

### Hypotheses
- `../ideas/hyp-017.md` - Multi-Resolution Counterfactual Attribution: subfield self-organizing into granularity spectrum (chunk→agent→operation).
- `../ideas/hyp-018.md` - Counterfactual Replay as Standard Agent Debugging Primitive: record→replay→fork→measure becoming the `git bisect` of the agent era.

## 2026-05-20 Metabolism Day 9 Additions

### Papers
- `../reference_notes/2605.15581_star.md` - STAR: Stage-attributed Triage and Repair for RCA agents. Counterfactual candidate evaluation + stage-localization + patch-and-replay. Architectural convergence with CMD.
- `../reference_notes/2603.18718_memma.md` - MemMA: Multi-agent memory cycle with in-situ self-evolving memory. Probe→verify→repair before memory commit.
- `../reference_notes/2604.12007_memory_worth.md` - Memory Worth: Two-counter per-memory governance signal. Lightweight complement to CMD's heavyweight causal attribution.
- `../reference_notes/2604.07877_memreader.md` - MemReader: Active extraction (passive→active). Validates `write_error` and `compression_error`.

### GitHub Repos
- `../reference_notes/github_culpa.md` - Culpa: Deterministic replay & counterfactual forking for AI agents.
- `../reference_notes/github_rewind.md` - Rewind: Time-travel debugger. `rewind fix` — one command from broken to proven fix.
- `../reference_notes/github_traceforge.md` - TraceForge: Record, replay, fuzz, counterfactual attribution with sensitivity scoring.
- `../reference_notes/github_causalos.md` - CausalOS: Causal memory layer preventing recurring failures via action-outcome chains.
- `../reference_notes/github_agentrx.md` - AgentRx (Microsoft): Constraint-based agent failure diagnosis. 10-category taxonomy.

## 2026-05-20 Day 10 — Decision 30 Resolution

- Decision 30 resolved: accelerate paper deadline from 2026-06-15 → **2026-06-10**; maintain memory-diagnostic-layer positioning; 5-dimension depth differentiation vs Rewind
- New metric: **Repair Depth** (Level 0 symptom → Level 3 cascade). CMD V1 at Level 1-2, Rewind at Level 0
- Paper requirement at the time: head-to-head CMD operation-level repair vs Rewind-style step-level retry on same failure cases; superseded by Decision 34 on 2026-05-23.
- See: `cmd_innovation_core/plans/cmd_open_decisions.md` Decision 30 + Decision 34, `CONTEXT.md` Key Relationships

## 2026-05-23 Day 13 — Decision 34 Paper Claim Integrity

- `../cmd_innovation_core/plans/cmd_open_decisions.md` - Decision 34: 596-case Macro F1 reframed as phrase-match mechanics snapshot; headline moves to 130 researcher-adjudicated cases.
- `../cmd_innovation_core/gates/V0V1_gate_status.md` - 2026-05-23 caveat + LLM re-test plan.
- `../cmd_innovation_core/gates/V1V2_gate_status.md` - adapter parity caveat and re-test row.
- `../cmd_innovation_core/plans/experiment_02_cmd_attribution.md` - Experiment 2 two-tier eval: 130 headline / 596 scale sanity.
- `../cmd_innovation_core/plans/experiment_01_context_construction.md` - Experiment 1 R7 hardening: 80 cases x 5 modes with token-control.
- `../cmd_innovation_core/plans/limitations.md` - evaluator-annotator circularity added; deterministic/phrase-match scoring reframed as resolved for LLM eval path.
- `../data/probe_cases/researcher_labeled_subset.json` - empty researcher adjudication stub.
- `../data/probe_cases/experiment_01_inspected_ecs.json` - empty ECS inspection stub.
