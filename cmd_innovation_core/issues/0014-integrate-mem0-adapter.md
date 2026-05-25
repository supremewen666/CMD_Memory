---
title: Integrate mem0 as first CMD-Skill Adapter target
labels:
  - done
type: done
user_stories:
  - 34
  - 35
  - 36
tdd_cycle: 20
---

# Integrate mem0 as first CMD-Skill Adapter target

## Parent

`prd/cmd_minimal_probe_prd.md` V1 Scope
`prototypes/mem0_adapter_interface_prototype.md`

## What to Build

Build `Mem0Adapter` that intercepts mem0's `add()` and `search()` operations at two cut points, enabling CMD-Audit to run counterfactual replays against a real memory agent without knowing its internals. The adapter must produce identical attribution labels to the standalone harness for the same probe cases, and must never mutate the original mem0 store.

## Implementation Detail

### Cut Point A: `intercept_add`

- Accepts: `case_id`, `original_facts` (what mem0 would store), `replay: ReplayName`.
- Returns: `list[str]` of facts to store instead (oracle variant).
- Oracle Write: return gold evidence facts.
- Oracle Compression: return uncompressed/complete version of facts.
- Verbatim Event Oracle: return empty list + set bypass flag (raw events fed directly to context).
- Injection-Oracle: return correctly formatted evidence block.
- Passthrough (no interception): return `original_facts` unchanged.

### Cut Point B: `intercept_search`

- Accepts: `case_id`, `original_query`, `original_results` (what mem0 retrieved), `replay: ReplayName`.
- Returns: `list[MemoryItem]` to use instead (oracle variant).
- Oracle Retrieval: return gold evidence facts as MemoryItems.
- Evidence-Given Reasoning: return `original_results` + append gold evidence (passthrough + augmentation).
- Passthrough (no interception): return `original_results` unchanged.

### Sandbox Guarantee

- `Mem0Adapter` holds a read-only reference to the mem0 store.
- `get_store_snapshot()` returns a checksum before replay.
- After replay completes, verify `get_store_snapshot()` matches pre-replay checksum.
- Mismatch is a hard error: `SandboxViolationError`.
- Adapter replays create temporary in-memory variants of intercepted data; no variant is written back.

### Adapter-Label Parity

- Run V0 6-label smoke suite through both standalone harness and mem0 adapter path.
- Assert: `predicted_label(standalone) == predicted_label(adapter)` for all 6 cases.
- Assert: `macro_f1(standalone) == macro_f1(adapter)`.
- Any discrepancy is a bug in the adapter interception logic.

### mem0 Integration Mode

- V1 uses a *recorded trace* integration mode: mem0 operations are pre-recorded for probe cases, and the adapter replays against the recorded trace. This avoids requiring a live mem0 instance during CMD-Audit runs.
- Live mem0 integration (real-time interception during agent execution) is V2 scope.

## Acceptance Criteria

- [x] `Mem0Adapter.intercept_add()` correctly routes each write-side replay to the appropriate oracle variant.
- [x] `Mem0Adapter.intercept_search()` correctly routes each retrieval-side replay to the appropriate oracle variant.
- [x] All 6 V0 smoke cases produce identical attribution labels through adapter path vs standalone path.
- [x] mem0 store checksum is identical before and after every replay (no mutation).
- [x] `SandboxViolationError` is raised if any replay attempts to write to mem0 store.
- [x] Adapter macro F1 on 6-label suite == standalone macro F1.
- [x] Behavior-level tests: adapter-label parity, store immutability, sandbox violation rejection.

## Implementation Summary

- **New package**: `cmd_audit/adapters/` — `base.py` (Mem0Trace, StoreChecksum, SandboxViolationError, ReplayName), `mem0_adapter.py` (Mem0Adapter with intercept_add/intercept_search), `mem0_replays.py` (10-replay portfolio: 6 adapter-intercepted + 4 V1 passthrough), `harness.py` (run_case_with_mem0, run_cases_with_mem0).
- **Trace fixtures**: `data/probe_cases/mem0_v0_smoke_traces.json` — 6 pre-recorded mem0 operation traces with SHA-256 store checksums.
- **Tests**: `tests/test_cmd_audit_issue14_mem0_adapter.py` — 30 tests, 90 subtests across 5 test classes (Mem0TraceValidation, Mem0AdapterInterception, Mem0AdapterSandbox, AdapterLabelParity, Mem0AdapterEndToEnd).
- **No regressions**: Full suite 417 tests pass (387 baseline + 30 new).
- **V1→V2 gate**: `check_v1_to_v2_gate(mem0_integrated=True)` now recognizes 1 adapter integration; second integration (Letta) still required.
- Detail map: `cmd_innovation_core/issues/0014-integrate-mem0-adapter-implementation-details.md`
