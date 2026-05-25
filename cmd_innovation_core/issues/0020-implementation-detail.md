---
name: "0020: Post-Gate Pipeline Implementation Detail"
description: Complete implementation detail for Decision 32 post-gate pipeline (issues 0020-A through 0020-H).
status: complete
---

# Issue 0020: Post-Gate Pipeline — Implementation Detail

**Date**: 2026-05-23
**Status**: Complete — 805 tests pass, 0 regressions (91 new tests for 0020)
**Decision**: Decision 32 (Post-Gate Pipeline — Repair Layering, Iterative Repair, Self-Supervision, Failure Memory Lifecycle)

## Decision 32 Point-by-Point Mapping

| Point | Requirement | Subtask | Implementation | Tests |
|-------|-------------|---------|----------------|-------|
| 1 | Repair layering (4-tier) | B + A | AdapterRepairMixin, RepairExecutor, RepairOrchestrator, run_case_v1_with_hook_and_repair | 19 + 15 + 6 |
| 2 | 归因 Subagent Loop Context | — | Replay portfolios accept `agent_generate + scorer`; context = baseline + label + evidence_block; recovery_gain = subagent evidence gain | via Decision B tests |
| 3 | RepairAction (5 types) | B | `RepairAction` dataclass, JSON-only prompt/parser, LLM path in `RepairExecutor`, `_select_action_type` fallback | 19 + JSON contract tests |
| 4 | Iterative repair | A + G | `RepairOrchestrator` walks `close_deltas`, stops at `recovered`; `draft_ecs_for_label` for any label | 15 + 9 |
| 5 | Online ECS corrected_memory | — | 7/11 labels: `replay.evidence_block`; 4 gold-dependent: surrogate path | via 0020-E |
| 6 | Self-supervision surrogate | E | `measure_surrogate_gap`, `measure_surrogate_gaps`, `compute_surrogate_gap_summary` | 11 |
| 7 | FM injection timing | D | `build_failure_memory_context_v1` (wrong_memory + original_evidence) | 18 |
| 8 | FM composite key | D | `FailureMemoryStoreV1.retrieve` (label + query + memory_top_terms) | 18 |
| 9 | FM recovered-only storage | D | `FailureMemoryStoreV1.add_if_recovered(assessment)` | 18 |
| 10 | run_case_v1_with_hook integration | C | `run_case_v1_with_hook_and_repair` | 6 |
| 11 | PreCmdDecision → AuditResult | F | `AuditResult.hook_stage`, `selected_replays`, `per_replay_scores` (`fallback_triggered` removed in 0021 PR2) | 6 |
| 12 | Attribution = None (3-tier) | A | `AttributionFailed` sentinel; (a) RPE zero → self-supervision, (b) surrogate zero → AttributionFailed, (c) surrogate gain → normal | 15 |
| 13 | Online Post-Repair validation | — | Simplified — offline high correctness justifies trust | — |
| 14 | Hook false negative | — | Trust F2 offline calibration (design doc) | — |
| 15 | Hook → pipeline handoff | F | `selected_replays` from RPE judge; FM at ECS stage | 6 |
| 16 | V2 cascade pre-burial | H | `ECSDraft.cascade_candidates` (V1 always empty) | 7 |
| 17 | Online pipeline data flow | A-H | End-to-end: hook → RPE → attribution → ECS + fm → RepairOrchestrator → executor → FM | all |

## New Modules

### `cmd_audit/repair_executor.py`

- `RepairExecutorResult`: assessment, scores, applied_action, repair_context, label
- `RepairExecutor.run()`: single repair execution
  - Builds `repair_context` via `build_repair_context`
  - Creates `RepairAction` via JSON-only LLM output when `llm_client` is supplied
  - Falls back to `_select_action_type` only in no-LLM/offline mode or non-strict LLM failure mode
  - Calls `adapter.apply_repair(action)` (catches `ValueError` only)
  - Runs `run_post_repair_context_replay` with `RepairedContext` (now includes `fm_context`)
- `_select_action_type`: maps label → preferred action, falls back to `supported_actions[0]`
- `action_selection_failed`: strict LLM mode result when RepairAction JSON is invalid

### `cmd_audit/repair_orchestrator.py`

- `AttributionFailed`: sentinel exception for zero-gain cases (Decision 32, Point 12)
- `RepairOrchestratorResult`: case_id, final_assessment, scores, attempts, recovered, exhausted, labels_tried
- `RepairOrchestrator.run()`: iteration controller
  - Builds `labels_to_try` from `attribution.predicted_label` + `close_deltas`
  - For each label: `draft_ecs_for_label(case, audit_result, label)` → `RepairExecutor.run()`
  - Stops at first `recovered` or exhausts
  - `close_deltas_threshold` filters labels by recovery gain

### `cmd_audit/surrogate_gap.py`

- `GOLD_DEPENDENT_LABELS`: 4 labels needing gold evidence
- `measure_surrogate_gap(case)`: compares gold vs surrogate recovery gain
  - Gold path: `gold_evidence` → `_compute_recovery_gain`
  - Surrogate path: `_find_surrogate_evidence` (uses `retrieval_baselines.compute_bm25_scores`)
- `measure_surrogate_gaps(cases)`: batch processing
- `compute_surrogate_gap_summary(rows)`: aggregated statistics (avg, median, max, min, pct_found)

## Modified Modules

### `cmd_audit/post_repair.py`

- `ECSDraft.cascade_candidates: tuple[str, ...] = ()` (0020-H)
- `draft_ecs_for_label(case, audit_result, label)`: drafts ECS for any label
  - Finds replay matching label from `audit_result.replays`
  - Fallback to `gold_evidence` when `audit_result` is None
- `RepairedContext.fm_context: str = ""` (candidate 1 fix)
- `_combine_context` now includes `fm_context` if present

### `cmd_audit/failure_memory.py`

- `compute_memory_top_terms()`: BM25 top-N terms from retrieved items
- `_score_composite_key()`: label_match(2) + query_overlap + memory_term_overlap
- `FailureMemoryStoreV1`: composite-key retrieval
  - `retrieve(query, label, memory_top_terms, top_k)`
  - `add_if_recovered(record, assessment)`: stores only recovered (Point 9)
- `build_failure_memory_context_v1()`: fm_context = wrong_memory + original_evidence
- `build_repair_context()`: baseline + label + evidence_block + fm_context

### `cmd_audit/repairs.py`

- `RepairAction` dataclass: action_type, target_item_id, target_store, content, label, reasoning
- `REPAIR_ACTION_TYPES`: ("append", "replace", "relocate", "update_routing", "update_template")
- `REPAIR_ACTION_TOOL_DEFINITION`: JSON-schema-like dict for LLM tool calling
- `REPAIR_ACTION_SYSTEM_PROMPT`: JSON-only RepairAction subagent instruction
- `build_repair_action_prompt()`: label + evidence_block + fm_context + supported_actions + schema context
- `parse_repair_action_response()`: strict JSON-only parser; rejects markdown/prose/unsupported action/label mismatch
- `validate_repair_action_type()`: rejects invalid action types
- `RepairActionResult`: success, action, checksums

### `cmd_audit/adapters/base.py`

- `AdapterRepairMixin`: `supported_actions`, `apply_repair`, `_validate_action_type`

### `cmd_audit/adapters/mem0_adapter.py`

- `supported_actions = ("append", "replace")`
- `apply_repair()`: append (add to core_blocks), replace (update recall_results)

### `cmd_audit/adapters/letta_adapter.py`

- `supported_actions = ("append", "replace", "relocate", "update_routing")`
- `apply_repair()`: append, replace, relocate, update_routing

### `cmd_audit/harness.py`

- `AuditResult`: added `hook_stage`, `selected_replays`, `per_replay_scores`; `fallback_triggered` removed in 0021 PR2 and is derived from `hook_stage == "rpe_below_threshold"`
- `run_case_v1()` / `run_case_v1_with_hook()` / prefilter variants: accept optional `agent_generate + scorer` for real attribution subagent loop
- `run_case_v1_with_hook_and_repair()`: full pipeline integration (hook + attribution + RepairOrchestrator), accepts optional RepairAction LLM client

### `cmd_audit/replays.py`

- Replay portfolios accept optional `agent_generate` and `scorer`.
- Default path remains deterministic evidence-block scoring for offline compatibility.
- Real path builds `BASELINE CONTEXT + CMD ATTRIBUTION LABEL + COUNTERFACTUAL EVIDENCE BLOCK`, calls `agent_generate(query, context)`, then scores the real agent answer with `scorer(gold_evidence, answer)`.
- Real-path recovery gain uses `evidence_score - baseline.evidence_score`; answer score is retained as offline auxiliary signal.

## Post-Optimization Fixes (7 candidates)

| # | Issue | Fix |
|---|-------|-----|
| 1 | `build_repair_context` unused | `RepairedContext.fm_context` field + `_combine_context` injection |
| 2 | `except Exception` swallows `SandboxViolationError` | Changed to `except ValueError` only |
| 3 | `draft_ecs_for_label(case, None, label)` always fallback | Pass `audit_result` through `RepairOrchestrator.run()` |
| 4 | No composite retrieval precision test | Added `CompositeRetrievalPrecisionTest` (2 tests) |
| 5 | `_find_surrogate_evidence` uses simple keyword overlap | Reuses `retrieval_baselines.compute_bm25_scores` |
| 6 | Attribution subagent loop not wired | Added `agent_generate + scorer` to replay portfolios and harness entry points |
| 7 | RepairAction had schema but no parser | Added JSON-only prompt/parser and strict LLM mode |

## Test Files

- `tests/test_cmd_audit_issue20_h_cascade_preburial.py` — 7 tests
- `tests/test_cmd_audit_issue20_b_repair_action.py` — 19 tests
- `tests/test_cmd_audit_issue20_d_failure_memory_upgrade.py` — 20 tests (18 + 2 optimization)
- `tests/test_cmd_audit_issue20_a_repair_executor.py` — 15 tests
- `tests/test_cmd_audit_issue20_g_iterative_repair.py` — 9 tests
- `tests/test_cmd_audit_issue20_f_precmd_signals.py` — 6 tests
- `tests/test_cmd_audit_issue20_c_hook_repair_integration.py` — 6 tests
- `tests/test_cmd_audit_issue20_e_self_supervision_surrogate.py` — 11 tests

**Total**: 93 issue-0020-specific tests (91 original + 2 optimization)

## Known Limitations / Deferred to 0021

- `close_deltas_threshold` is default 0.0, not offline-calibrated
- `AttributionFailed` is defined but not yet wired into the full 3-tier flow in harness
- `per_replay_scores` field exists but not populated from hook (hook redesign pending in 0021)
- FM per-agent persistence (`FAILURE_MEMORY.md`) not implemented
- Post-Repair Context Replay still uses context scoring; real `agent_generate + AnswerVerifier` post-repair validation remains separate from the attribution replay loop.
