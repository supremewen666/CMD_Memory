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
