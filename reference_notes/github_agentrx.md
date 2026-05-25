# AgentRx (Microsoft): Diagnosing AI Agent Failures from Execution Trajectories

GitHub: microsoft/AgentRx (2026-03), arXiv: 2602.02475

## Core Contribution

Automated, domain-agnostic diagnostic framework that pinpoints the critical failure step in agent trajectories. Synthesizes constraints (invariants), evaluates them step-by-step, produces auditable validation logs. LLM judge classifies into 10-category taxonomy.

## Pipeline
1. IR — Normalize raw logs into canonical Trajectory IR
2. Static — Generate policy/tool/structure invariants
3. Dynamic — Generate per-step context-aware invariants
4. Check — Evaluate invariants, record violations
5. Judge — LLM classifies root-cause into 10-category taxonomy

## 10-Category Failure Taxonomy
Instruction/Plan Adherence, Invention of New Information, Invalid Invocation, Misinterpretation of Tool Output, Intent-Plan Misalignment, Underspecified User Intent, Intent Not Supported, Guardrails Triggered, System Failure, Inconclusive

## CMD Relevance

**Microsoft's entry into automated agent failure diagnosis validates the problem space.** AgentRx's 10-category taxonomy is agent-execution-level (tool calls, plans, guardrails) vs CMD's memory-pipeline-level (write, compress, retrieve, etc.).

**Methodology difference:**
- AgentRx: constraint-based (invariants) + LLM judge → observational
- CMD: counterfactual replay + Recovery Gain → causal

Complementary granularity. AgentRx-style invariants could serve as CMD pre-filter; CMD-style replay could validate AgentRx attributions with causal evidence.
