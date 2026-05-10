# 2026-05-10 Metabolism Day 0 — Broad Baseline Survey

## Trigger

User invoked /metabolism. No prior config.json existed. Run Day 0 initialization.

## Configuration

- Keywords: LLM agent memory, counterfactual attribution, memory failure diagnosis, memory repair, retrieval error, compression error, premature extraction, injection error, reasoning error, ECS, subagent judge monitor, evidence-driven evaluation
- Sources: arxiv, openalex, github
- arXiv categories: cs.AI, cs.CL, cs.LG, cs.SE

## Collection

Searched arxiv across 8 query variants. Also searched GitHub for relevant repos.

### Papers collected (27 total, 18 new reference notes created)

Tier-1 (directly CMD-relevant, 10 notes):
- 2603.07670: Agent Memory Survey (write-manage-read loop)
- 2603.14597: D-MEM (RPE-gated Fast/Slow routing)
- 2510.02373: A-MemGuard (proactive defense, dual memory)
- 2603.10600: Trajectory-Informed Memory (Decision Attribution Analyzer)
- 2604.27283: RSCB-MC (risk-sensitive retrieval)
- 2406.13408: SQLFixAgent (failure memory reflection)
- 2512.21354: Reflection-Driven Control (evolving reflective memory)
- 2601.06636: MedEinst (counterfactual diagnosis benchmark)
- 2604.12231: Thought-Retriever (self-evolving memory)
- 2310.08560: MemGPT (foundational virtual context)

Tier-2 (architecture/safety, 8 notes):
- 2603.18330: MemArchitect (policy governance)
- 2603.21272: Library Theorem (formal retrieval bounds)
- 2604.01599: ByteRover (agent-native memory)
- 2604.04853: MemMachine (ground-truth preservation)
- 2605.01688: GRAVITY (structured anchoring)
- 2604.09747: ADAM (privacy attack)
- github_agent_debugger: Peaky Peek
- github_agentlens: AgentLens

## Key Findings

1. **No existing counterfactual attribution framework**: The survey (2603.07670) confirms the write-manage-read loop but notes causally grounded retrieval as an open challenge. No paper or repo does operation-level counterfactual memory replay.

2. **Closest competitors**: Trajectory-Informed Memory (2603.10600) does decision-level attribution observationally. Peaky Peek (agent_debugger) does interactive checkpoint replay with HITL. Neither is automated counterfactual attribution.

3. **Validated demand**: A-MemGuard (dual memory), SQLFixAgent (failure memory), Reflection-Driven Control (reflective memory) all independently converge on the Failure Memory pattern CMD proposes. Peaky Peek and AgentLens validate engineering demand for memory debugging tools.

4. **CMD's unique position**: Automated operation-level attribution via counterfactual replay with Recovery Gain scoring is unoccupied in both literature and open-source.

## Files Modified

- Created 18 reference notes in `reference_notes/`
- Updated `knowledge/_index.md` with new references
- Updated `knowledge/topic-cmd-memory-failure.md` with 2026-05-10 signals, engineering ecosystem signal, and competitive landscape table

## New Hypotheses

- hyp-011: RPE as cheap anomaly pre-filter (D-MEM Critic Router analogue for Subagent Judge Monitor)
