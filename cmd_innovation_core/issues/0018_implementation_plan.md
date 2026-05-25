# Issue 0018 Implementation Plan — Pre-CMD Hook (Zero-Gold Online Memory Gate)

**Date**: 2026-05-21
**Decision**: 31 (design), 32-61 (implementation details, this session)
**Status**: superseded-by-0021

> **0021 PR2 cleanup (2026-05-23).** This plan is historical. The legacy
> PrefixGuard/RPE prefilter modules and `run_case_v1_with_prefilter` path were
> removed. Current implementation is the two-stage `cmd_audit/hook/` package.

## Architecture Summary

Single `post_retrieve_hook(query, retrieved_items, ...) -> PreCmdDecision` function that gates CMD counterfactual replay. Online: zero gold, deterministic/statistical rules only. Offline: 596 cases calibrate weights/thresholds.

```
Online (deployment)                    Offline (calibration, once)
─────────────────────                  ──────────────────────────
query, retrieved_items                 596 cases x full CMD replay
  ↓                                     ↓
post_retrieve_hook                     gold labels (Recovery Gain > 0)
  ↓                                     ↓
PrefixGuard (4 zero-gold signals)       logistic regression -> weights
  + RPE (BM25 surprise x utility)      grid search -> thresholds
  ↓                                     ↓
trigger_cmd?                           constants only
  YES -> CMD 10-replay
  NO  -> skip
```

## Key Design Decisions (Grilled 2026-05-21)

### Module Architecture

1. **prefix_guard.py → refactored to zero-gold only**. Remove gold-dependent signals (answer_score, evidence_score, gold coverage). Add `PrefixGuardSignals` dataclass + `compute_prefix_guard_signals()`. Keep `PrefixGuardResult` with optional `offline_metrics: PrefixGuardOfflineMetrics | None` sub-object.
2. **rpe_prefilter.py → refactored to zero-gold gating**. Remove gold-dependent ordering (`compute_per_replay_ordering`, `_replay_heuristic_surprise`). Add `RPESignals` dataclass + `compute_rpe_signals()`. `run_rpe_prefilter` becomes pure zero-gold gate. `selected_replays` = full `V1_REPLAY_NAME_ORDER` when triggered.
3. **replay_ordering.py → NEW**. Houses gold-dependent `compute_per_replay_ordering` + `_replay_heuristic_surprise` (moved out of rpe_prefilter.py, for offline research only).
4. **post_retrieve_hook.py → NEW**. Pure composition/decision layer. Calls PrefixGuard + RPE, applies 5-branch gating, returns `PreCmdDecision`. Orchestration only (input normalization, empty guard, error fallback). Does NOT redefine signal semantics.
5. **hook_constants.py → NEW**. All offline-calibrated constants in one module. Imported by prefix_guard, rpe_prefilter, post_retrieve_hook.
6. **calibrate_hook.py → NEW in scripts/**. Six-step offline calibration (requires sklearn, external to cmd_audit core).
7. **models.py → modified**. Add `RetrievedItem(memory_id, text)` lightweight dataclass.

### Signal Design

8. **PrefixGuard — 4 zero-gold signals**:
   - `empty_ctx`: `len(items) == 0` (binary)
   - `truncation`: fraction of items matching truncation/filter/redaction marker patterns (11 patterns, case-insensitive). Option A: continuous [0,1]. No severity weighting.
   - `near_duplicate`: max pairwise Jaccard similarity across all item pairs. Threshold Jaccard > 0.5. Reuses `retrieval_baselines.tokenize()`.
   - `low_count`: `len(items) < 2` (binary)
   - `anomaly_score = w1*empty_ctx + w2*truncation + w3*near_duplicate + w4*low_count`

9. **Truncation patterns** (11, case-insensitive):
   `"[...]"`, `"[blocked]"`, `"[filtered]"`, `"...truncated"`, `"[redacted]"`, `"[omitted]"`, `"[censored]"`, `"[removed]"`, `"[restricted]"`, `"[suppressed]"`, `"[sanitized]"`

10. **RPE — BM25-based, zero-gold**:
    - `surprise = 1.0 - max(BM25(query, item.text))`
    - `agent_confidence = max_bm25 * coverage_bonus`
    - `coverage_bonus = 0.7 + 0.3 * min(n_good_items, 3) / 3`
    - `good = BM25 score in top-25% AND score >= MIN_GOOD_BM25`
    - `utility = 1.0 - agent_confidence`
    - `rpe = surprise * utility`

11. **Five gating branches** (priority order):
    1. `len(items) == 0` → trigger_cmd=True, reason="empty_ctx" (hard sentinel)
    2. `utility > UTILITY_OVERRIDE_THRESHOLD` → trigger, reason="utility_override"
    3. `rpe > RPE_THRESHOLD` → trigger, reason="rpe_above_threshold"
    4. `anomaly_score > ANOMALY_THRESHOLD` → trigger, reason="anomaly_above_threshold"
    5. Otherwise → skip, reason="clean"

12. **reason_codes**: tuple of fine-grained signal names (e.g., `("empty_ctx", "low_count")`) accompanying primary reason.

### Data Types

13. **RetrievedItem** = `(memory_id: str, text: str)`. In `cmd_audit/models.py`. Minimal online contract — adapters convert from MemoryItem.

14. **PreCmdDecision fields**: `trigger_cmd`, `reason`, `reason_codes`, `anomaly_score`, `surprise_score`, `utility_score`, `rpe`, `selected_replays`. `selected_replays` is audit/execution-plan field (full V1_REPLAY_NAME_ORDER when triggered, empty when skip).

15. **PrefixGuardResult**: top-level only zero-gold fields (`anomaly_score`, `should_trigger_cmd`, `empty_ctx`, `truncation`, `near_duplicate`, `low_count`, `item_count`). Gold fields in optional `offline_metrics: PrefixGuardOfflineMetrics | None` sub-object.

16. **RPEPrefilterResult**: top-level only zero-gold gating fields (`gate_decision`, `gate_reason`, `rpe`, `surprise`, `utility`). `selected_replays` = full list or empty. `per_replay_scores` kept (zero scores when skipped).

17. **No top_k in hook semantics**. Removed from `post_retrieve_hook`. If needed later, it's an outer budget policy, not hook semantics.

### Integration

18. **run_case_v1_with_prefilter**: preserved (offline research mode, gold-dependent ordering for ablation).
19. **run_case_v1_with_hook**: NEW (online deployment mode, zero-gold hook). Paper's deployment readiness section.
20. **Post-Repair Context Replay**: unchanged, downstream of hook. Hook only gates; pipeline unchanged after trigger.

### Default Constants (pre-calibration)

| Constant | Default | Note |
|----------|---------|------|
| PREFIXGUARD_WEIGHTS | (0.40, 0.25, 0.20, 0.15) | empty_ctx, truncation, near_duplicate, low_count |
| ANOMALY_THRESHOLD | 0.5 | design doc original |
| RPE_THRESHOLD | 0.3 | design doc original |
| UTILITY_OVERRIDE_THRESHOLD | 0.8 | design doc original |
| GOOD_BM25_PERCENTILE | 0.25 | top-25% |
| MIN_GOOD_BM25 | 0.5 | absolute floor |
| DUPLICATE_THRESHOLD | 0.5 | Jaccard |

## Implementation Batches

### Batch 1: Foundation (no dependencies)
- `cmd_audit/hook_constants.py` — all constants
- `cmd_audit/models.py` — add `RetrievedItem`

### Batch 2: Signal Layer
- `cmd_audit/prefix_guard.py` — refactor: remove gold signals, add `PrefixGuardSignals` + `compute_prefix_guard_signals`, rewrite `run_prefix_guard`
- `cmd_audit/rpe_prefilter.py` — refactor: remove gold ordering, add `RPESignals` + `compute_rpe_signals`, rewrite `run_rpe_prefilter`
- `cmd_audit/replay_ordering.py` — NEW: move gold-dependent ordering functions

### Batch 3: Composition + Integration
- `cmd_audit/post_retrieve_hook.py` — NEW: `PreCmdDecision` + `post_retrieve_hook`
- `cmd_audit/harness.py` — add `run_case_v1_with_hook`
- `cmd_audit/__init__.py` — update exports

### Batch 4: Tests + Calibration
- `tests/test_cmd_audit_issue18_pre_cmd_hook.py` — NEW
- `scripts/calibrate_hook.py` — NEW

## Test Plan (~40-50 tests)

| Test Class | Coverage |
|------------|----------|
| RetrievedItemTest | dataclass construction, field immutability |
| PrefixGuardSignalsTest | 4 signal computation correctness |
| PrefixGuardOnlineTest | run_prefix_guard with new signature, anomaly_score weighting, should_trigger_cmd |
| PrefixGuardOfflineMetricsTest | optional sub-object populated correctly / None |
| RPESignalsTest | BM25 surprise + utility + rpe computation |
| RPEGatingTest | run_rpe_prefilter gate branches |
| PostRetrieveHookTest | post_retrieve_hook composition, 5 reason branches, empty_ctx sentinel, reason_codes |
| HookConstantsTest | constant existence, types, range validation |
| PreCmdDecisionTest | dataclass fields, frozen |
| ReplayOrderingTest | gold-dependent ordering preserved (moved module) |
| IntegrationTest | hook -> run_case_v1_with_hook end-to-end, V0 smoke 6 cases non-regression |
