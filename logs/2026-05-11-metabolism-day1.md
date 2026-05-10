# 2026-05-11 Metabolism Day 1 — Incremental Survey

## Trigger

Scheduled Day 1 incremental cycle. Day 0 completed 2026-05-10. Sliding window: 2026-05-06 ~ 2026-05-11.

## Configuration

- Keywords: agent memory, counterfactual attribution, memory debugging, LLM agent, memory failure diagnosis, memory repair
- Sources: arxiv, openalex, github
- arXiv categories: cs.AI, cs.CL, cs.LG, cs.SE
- Processed IDs (pre-existing): 35 entries from Day 0 + prior work

## Collection

Searched arxiv (cs.CL, cs.AI, cs.LG new listings) + GitHub. 9 new papers found, 8 reference notes + 1 GitHub note created after dedup.

### Papers collected (9 new)

Tier-1 (directly CMD-relevant, 6 notes):
- 2604.25161: Capability-Oriented Failure Attribution for VLN Agents (ACL 2026)
- 2604.17658: ErrorProbe — Self-Improving Error Diagnosis in Multi-Agent Systems
- 2604.15774: MemEvoBench — Benchmarking Memory MisEvolution in LLM Agents
- 2604.16548: Survey on Security of Long-Term Memory in LLM Agents (Mnemonic Sovereignty)
- 2605.03312: MemFlow — Intent-Driven Memory Orchestration for SLM Agents
- 2604.20006: Memora — Benchmarking Long-Term Memory (ACL 2026 Findings)

Tier-2 (supporting, 2 notes):
- 2604.27045: Dual-Stream Memory and Reconciliation for Health Coaching
- 2604.20117: SCG-MEM — Schema-Constrained Generation for Agent Memory

GitHub (1 note):
- eth-jashan/MemoScope: AI Agent Memory Debugger (observational, no replay)

## Key Findings

1. **Failure Attribution is emerging as an independent research cluster** — 3 independent works across VLN, MAS, and memory safety
2. **"Verified Episodic Memory" pattern convergence** — ErrorProbe + A-MemGuard + CMD converge on store-only-verified-repairs
3. **Memory security survey** validates Subagent Judge Monitor leak-safety boundaries
4. **`premature_extraction_error`** gets independent empirical validation (13.6% error cascade from extraction loss)
5. **`route_error`** validated by MemFlow's Router architecture
6. **MemoScope** validates tooling demand; CMD remains unique in counterfactual attribution

## New Hypotheses

- hyp-012: Verified Episodic Memory as a cross-domain convergence pattern for agent memory repair stores

## Files Modified

- Created 8 new reference notes + 1 GitHub note
- Updated `knowledge/_index.md` with Day 1 additions
- Updated `knowledge/topic-cmd-memory-failure.md` with Day 1 signals and expanded competitive landscape
- Updated `knowledge/current-memory.md` with Day 1 incremental conclusions
- Created `hypotheses/hyp-012.md`
- Created `config.json` (Day 0 initialization was manual; formalized today)

## Next Increment (Day 2)

- Continue 5-day sliding window
- Prioritize openalex source (not yet queried in Day 1)
- Monitor for new papers in the 2605.05xxx range (very recent submissions)
