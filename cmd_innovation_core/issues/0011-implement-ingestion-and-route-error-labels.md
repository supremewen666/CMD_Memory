---
title: Implement ingestion_error and route_error labels
labels:
  - needs-triage
type: AFK
blocked_by: "0010"
user_stories:
  - 37
  - 38
tdd_cycle: 16
---

# Implement ingestion_error and route_error labels

## Parent

`prd/cmd_minimal_probe_prd.md` V1 Scope

## What to Build

Split `ingestion_error` from `write_error` and add `route_error` as a new first-class pipeline label. These are the two lowest-cost V1 labels:

- `ingestion_error`: Oracle Write already recovers this case. The change is attribution logic only—when gold evidence never reached the agent (no `add()` call in trace), label as `ingestion_error` instead of `write_error`.
- `route_error`: Add Oracle Route replay that enumerates stores/tiers and picks the best recovery. This is a new replay, but the enumeration pattern is isomorphic to existing Oracle Retrieval.

## Implementation Detail

### ingestion_error

- Add `ingestion_error` to active label registry. Move from deferred to active.
- Add `has_ingestion_trace` field to probe case schema: a boolean or nullable field indicating whether gold evidence appears in any `add()` call.
- In Oracle Write replay attribution: if recovery gain > 0 AND `has_ingestion_trace == False`, label = `ingestion_error`. Otherwise, label = `write_error`.
- `validate_v1_label("ingestion_error")` succeeds. `validate_v0_label("ingestion_error")` still raises `ValueError`.

### route_error

- Add Oracle Route replay to V1 Replay Portfolio.
- Oracle Route replay enumerates available stores/tiers for the target agent. For standalone harness: enumerate mock stores ("episodic", "semantic", "graph"). For mem0 adapter (future): enumerate mem0's flat store (single store—route_error is N/A for mem0; this label only activates on tiered agents like Letta).
- Route replay: for each store, re-run retrieval from that store, score answer, record recovery gain. Attribution picks the store with max gain.
- If best-store recovery gain > baseline and the original store was not the best, label = `route_error`.
- Add `route_error` to active label registry. Move from deferred to active.

### Non-regression

- Run V0 6-label smoke suite through V1 pipeline. All 6 labels must match V0 output.
- `ingestion_error` must NOT flip any existing `write_error` smoke case (smoke cases assume evidence reached the agent).
- `route_error` must NOT flip any existing V0 label (smoke cases don't exercise route failures).

## Acceptance Criteria

- [ ] `ingestion_error` is attributed when Oracle Write recovers but gold evidence has no `add()` trace.
- [ ] `write_error` is still attributed when Oracle Write recovers and gold evidence HAS an `add()` trace.
- [ ] Oracle Route replay enumerates stores, produces recovery gains, and attributes `route_error` when best store ≠ original store.
- [ ] V0 6-label smoke suite produces identical labels through V1 pipeline (no regression).
- [ ] `validate_v1_label("ingestion_error")` and `validate_v1_label("route_error")` succeed.
- [ ] Deferred label registry reflects that `ingestion_error` and `route_error` are now active.
- [ ] Behavior-level tests: ingestion vs write boundary, route recovery gain > 0, non-regression on 6-label suite.
