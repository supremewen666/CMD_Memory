---
title: Implement ingestion_error and route_error labels
labels:
  - ready-for-human
type: done
blocked_by: ~
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

- [x] `ingestion_error` is attributed when Oracle Write recovers but gold evidence has no `add()` trace.
- [x] `write_error` is still attributed when Oracle Write recovers and gold evidence HAS an `add()` trace.
- [x] Oracle Route replay enumerates stores, produces recovery gains, and attributes `route_error` when best store ≠ original store.
- [x] V0 6-label smoke suite produces identical labels through V1 pipeline (no regression).
- [x] `validate_v1_label("ingestion_error")` and `validate_v1_label("route_error")` succeed.
- [x] Deferred label registry reflects that `ingestion_error` and `route_error` are now active.
- [x] Behavior-level tests: ingestion vs write boundary, route recovery gain > 0, non-regression on 6-label suite.

## Completion (2026-05-15)

All acceptance criteria pass. 44 behavior-level tests, 262 total tests, zero regressions. Detail map: `0011-implement-ingestion-and-route-error-labels-implementation-details.md`.

### Changed Files

| File | Change |
|------|--------|
| `cmd_audit/labels.py` | V1 labels registry, `V1_REPLAY_TO_LABEL`, `validate_v1_label()`, updated `DEFERRED_PIPELINE_LABELS` |
| `cmd_audit/models.py` | `store` on MemoryItem, `has_ingestion_trace` + `default_store` on ProbeCase, `from_mapping_v1()`, `load_probe_cases_v1()` |
| `cmd_audit/replays.py` | `run_v1_replay_portfolio()`, `run_oracle_route()`, `_collect_stores()`, `_recover_from_store()` |
| `cmd_audit/attribution.py` | `assign_attribution_v1()`, `_v1_label_for_replay()` |
| `cmd_audit/harness.py` | `run_case_v1()`, `run_cases_v1()`, `run_case_full_v1()`, `run_cases_full_v1()` |
| `cmd_audit/repairs.py` | V1 label validation, `ingestion_error` + `route_error` repair actions, `get_targeted_repair_action_v1()` |
| `cmd_audit/post_repair.py` | V1 label validation in ECSDraft |
| `cmd_audit/metrics.py` | V1 label validation in DiagnosisPrediction |
| `cmd_audit/__init__.py` | 13 new exports |
| `data/probe_cases/` | v1_ingestion_error_case.json, v1_route_error_case.json |
| `tests/test_cmd_audit_issue11_v1_labels.py` | 9 test classes, 44 methods |
