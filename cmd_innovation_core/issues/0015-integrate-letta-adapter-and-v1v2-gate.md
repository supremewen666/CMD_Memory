---
title: Integrate Letta as second adapter target and enforce V1→V2 gate
labels:
  - AFK
type: AFK
blocked_by: "0014"
user_stories:
  - 36
tdd_cycle: 21
---

# Integrate Letta as second adapter target and enforce V1→V2 gate

## Parent

`prd/cmd_minimal_probe_prd.md` V1 Scope

## What to Build

Integrate Letta (letta-ai/letta) as the second CMD-Skill Adapter target and verify the V1→V2 gate: at least two distinct memory agents integrated without macro F1 regression. Letta's core/archival/recall tiering exercises `route_error` in a way mem0's flat store cannot.

## Implementation Detail

### LettaAdapter

- `LettaAdapter` follows the same two-cut-point pattern as `Mem0Adapter`:
  - `intercept_core_write()`: intercept writes to Letta's core memory.
  - `intercept_archival_store()`: intercept writes to Letta's archival memory.
  - `intercept_recall_retrieval()`: intercept retrieval from Letta's recall memory.
- Oracle Route replay on Letta: enumerate core/archival/recall tiers. For each tier, re-run retrieval from that tier, score recovery gain. If best-tier ≠ original tier, label = `route_error`.
- On mem0 (flat store), Oracle Route replay enumerates a single store and always produces zero gain. `route_error` is effectively N/A for mem0—this is expected and not a bug.
- Letta integration uses the same recorded-trace mode as mem0 (live integration is V2).

### V1→V2 Gate Check

Gate criteria (from AC14):
1. ≥ 2 distinct memory agents integrated through Adapter Interface.
2. 11-label macro F1 on agent 2 (Letta) ≥ 11-label macro F1 on agent 1 (mem0).

Implementation:
- `run_v1v2_gate_check()` loads attribution results from both agents.
- Compares macro F1 per label and overall.
- Produces `artifacts/sandbox/V1V2_gate_status.txt` with per-agent metrics and pass/fail decision.
- If macro F1(Letta) < macro F1(mem0), gate fails with a regression report showing which labels degraded.

### Non-regression

- mem0 adapter results must not change after Letta adapter is added (no cross-contamination).
- V0 6-label smoke suite on both agents: labels must match standalone.

## Acceptance Criteria

- [ ] `LettaAdapter` intercepts core/archival/recall operations correctly.
- [ ] Oracle Route replay on Letta produces `route_error` attribution (tier miss).
- [ ] Oracle Route replay on mem0 produces zero gain (expected—flat store has no tier miss).
- [ ] V1→V2 gate check: 2 agents integrated, macro F1(Letta) ≥ macro F1(mem0).
- [ ] `artifacts/sandbox/V1V2_gate_status.txt` produced with per-agent metrics and gate decision.
- [ ] mem0 adapter results unchanged (no cross-contamination).
- [ ] Behavior-level tests: Letta tier routing, V1→V2 gate pass/fail branches, cross-agent non-regression.
