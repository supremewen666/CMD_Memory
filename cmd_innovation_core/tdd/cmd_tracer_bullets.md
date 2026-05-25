# TDD Plan: CMD Tracer Bullets

This is a non-code TDD skeleton. It names behavior to test through future public interfaces before implementation starts.

## Current Green State

Verified on 2026-05-19:

- Cycles 1-21 are green through issues 0001-0015. 453 tests pass, 622 subtests pass.
- V0 evidence chain is structurally complete: attribution table, confusion matrix, comparison metrics, Post-Repair Context Replay, targeted repairs, ECS Failure Memory, retrieval baselines, monitor contract hardening, version gates.
- V1 Cycle 16 (`ingestion_error` + `route_error`) is green: 8-label pipeline, 7-replay V1 portfolio, `has_ingestion_trace` boundary, `oracle_route` store enumeration.
- V1 Cycle 17 (`granularity_error` + `graph_error` + `safety_error`) is green: 11-label pipeline, 10-replay V1 portfolio.
- V1 Cycle 18 (coupled-failure recalibration) is green: configurable `top_k` (default 2, supports 3+), `close_deltas` transparency.
- V1 Cycle 19 (memory-probe baseline) is green: 3×2 grid comparator (3 write × 2 retrieve: cosine + BM25; dense retrieval deferred to V1 adapter layer per issue 0008), `memory_probe_best_accuracy` column in comparison metrics.
- V1 Cycle 20 (mem0 adapter) is green: `Mem0Adapter` with `intercept_add`/`intercept_search` two-cut-point interception, recorded-trace mode, sandbox checksum, adapter-label parity, 30 tests.
- V1 Cycle 21 (Letta adapter + V1→V2 gate) is green: `LettaAdapter` with `intercept_core_write`/`intercept_archival_store`/`intercept_recall` three-cut-point interception, tripartite memory model, sandbox checksum, adapter-label parity, cross-agent non-regression, V1→V2 gate passing with both adapters, 44 tests.
- All four V0→V1 gate criteria pass on the 6-case smoke suite. V1→V2 gate passes with both `mem0_integrated=True` and `letta_integrated=True`.
- HITL V0→V1 gate review approved (supremewen, 2026-05-10). V0 LOCKED. Probe suite scaling to 596 cases is the next gate validation step.

V1 Cycle 22 (RPE prefilter) is planned but not yet active. V1 Cycle 23 (provenance tracking) is planned. See `## V1 Red-Green Sequence` below.

## Public Interface Questions To Confirm

- How should the public harness expose a replay portfolio while preserving the simple one-case runner?
- What should a replay engine return for one failed case once there is more than one replay result?
- What should the attribution layer accept: raw replay outputs, scored deltas, or both? Current answer: scored replay results are enough for rule-based V0 attribution.
- What should the ECS layer expose to future tasks?

## Resolved Interface Decisions

- V0 is a standalone research harness.
- V0 keeps an adapter interface for future memory-agent integration.
- V0 labels are `write_error`, `compression_error`, `premature_extraction_error`, `retrieval_error`, `injection_error`, and `reasoning_error`.
- Subagent judge is invoked both as a comparator baseline and as a cheap high-recall monitor.
- Subagent judge monitor is leak-safe: it cannot emit final labels, ECS, memory writes, gold answers, or full failed traces.
- `CMD-Audit` and `CMD-Skill Adapter` are separate boundaries.
- V0 attribution excludes bad memory item labels.
- Issue 0003 should use a V0 Replay Portfolio rather than inventing separate table-specific replay paths.
- For `premature_extraction_error`, a valid case can have gold evidence with `source_event_id` and no `source_memory_id`; that is not a malformed retrieval case.
- Subagent Judge Monitor `anomaly_reason` is locked to four enum values; free-form natural language and content-bearing evidence pointers are rejected.
- Post-Repair Context Replay outputs three-value `repair_assessment` (`recovered` / `partial` / `failed`), not a binary gate.
- ECS `cause` must not use V0-forbidden item label names or re-declare them through natural language equivalents.
- `ingestion_error` is a registered V1 label; it is rejected by V0 label validation but accepted by V1 label validation (issue 0011 complete).
- CMD-Audit write operations are restricted to replay-local sandbox; writes to paths outside the sandbox are rejected.

## V1 Resolved Interface Decisions (2026-05-11, updated 2026-05-15)

- V1 labels are the 5 deferred pipeline labels introduced in priority order: `ingestion_error` (✅ done) → `route_error` (✅ done) → `granularity_error` (✅ done) → `graph_error` (✅ done) → `safety_error` (✅ done). All 11 pipeline labels active.
- V1 attribution outputs all 11 pipeline labels through 10-replay V1 portfolio.
- The first CMD-Skill Adapter target is mem0 (mem0ai/mem0). The second is Letta (letta-ai/letta) for V1→V2 gate. Both done (issues 0014-0015).
- mem0 Adapter intercepts `add()` and `search()` at two cut points. Letta Adapter intercepts core_write, archival_store, and recall at three cut points.
- All adapter replays run sandboxed; original mem0/Letta store state is never mutated by CMD-Audit.
- Adapter-label parity: both mem0 and Letta adapter paths produce identical labels to standalone harness on V0 smoke suite.
- V0 + V1 + V2 constitute one paper. V2 is the final module/skill.
- V1 baseline comparison adds memory-probe (2603.02473) grid-comparison as a new comparator.
- RPE Pre-Filter is a late-V1 optimization (Cycle 22), not a V1 gate prerequisite.
- LoCoMo/LongMemEval real data probe construction is researcher-led; CMD-Audit consumes the resulting probe cases.
- `ingestion_error` is distinct from `write_error`: ingestion means evidence never reached the agent at all; write means evidence reached the agent but was not stored.
- `route_error` is distinct from `retrieval_error`: route means correct memory is stored but in the wrong store/tier; retrieval means correct memory is in the right store but not retrieved.
- LettaAdapter uses recorded-trace mode (V1), same as Mem0Adapter. Live integration is V2 scope.
- V1→V2 gate requires `adapter_count >= 2` (both `mem0_integrated=True` and `letta_integrated=True`). Gate passes with both adapters integrated.
- Provenance tracking (Decision 28, Issue 0017): Execution Lineage DAG with trace-mem HMAC citation. `MemoryItem.provenance: Optional[List[ProvenanceEdge]]` records in-edge derivation. Phase 1 (V1): DAG structure + in-edge tracking. Phase 2 (V2): cascade repair via MemQ TD(λ). Provenance edges are append-only. Only replay-created items carry provenance; baseline items have provenance=None.

## Priority Behaviors

1. A failed case with recoverable extracted memory and successful Oracle Retrieval is attributed as `retrieval_error`.
2. A failed case where only raw-event replay recovers evidence is attributed as first-class `premature_extraction_error`, not `retrieval_error`.
3. A failed case with correct evidence but bad final answer is attributed as `reasoning_error`.
4. Close replay deltas produce top-2 or multi-label attribution.
5. An ECS record exposes corrected memory and repair guidance without exposing the full failed trace to future tasks.
6. Post-Repair Context Replay reruns the original failed query with repaired context and without injecting the gold answer.
7. CMD-guided repair is measured against an undifferentiated hard-case update.
8. Subagent judge explanation is recorded as a comparator and cannot directly set CMD attribution.
9. Subagent judge monitor can trigger replay, but the final attribution remains CMD replay-delta grounded.
10. The standalone harness exposes an adapter-boundary payload without integrating into an existing memory agent.
11. V0 attribution outputs no bad memory item labels.
12. Subagent Judge Monitor output with free-form `anomaly_reason` or content-bearing evidence pointers is rejected.
13. Post-Repair Context Replay with evidence recovered but answer still wrong yields `repair_assessment = "partial"`.
14. ECS `cause` containing V0-forbidden item label names or their natural-language equivalents is rejected.
15. `ingestion_error` is rejected by V0 label validation but registered as a known deferred label.
16. CMD-Audit writes to paths outside the replay-local sandbox are rejected.

## Red-Green Sequence

### Cycle 1: Retrieval Failure Tracer Bullet

Status: green smoke foundation exists.

RED: A probe case has gold evidence in extracted memory, baseline misses it, and Oracle Retrieval recovers the answer. The expected attribution is `retrieval_error`.

GREEN: Implement the minimum public path that loads the case, records the replay delta, and assigns `retrieval_error`.

### Cycle 2: Verbatim Event Oracle Boundary

Status: green in issue 0003.

RED: A probe case has gold evidence in raw events but not in extracted memory. Verbatim Event Oracle recovers the answer and Oracle Retrieval does not. The expected attribution is `premature_extraction_error`.

GREEN: Add only enough behavior to make ingestion-time information loss a first-class V0 attribution.

Public behavior check:

- case loads through the existing probe loader;
- `run_case` or the public case runner returns replay results that include Verbatim Event Oracle and Oracle Retrieval;
- attribution chooses `premature_extraction_error`;
- attribution output still rejects bad-memory-item labels and deferred pipeline labels.

### Cycle 3: Reasoning Failure Boundary

Status: green in issue 0003.

RED: A probe case has correct retrieved evidence but the baseline answer is wrong. Evidence-Given Reasoning recovers the answer. The expected attribution is `reasoning_error`.

GREEN: Add the reasoning replay path and attribution rule.

### Cycle 3b: Six-Replay Attribution Table

Status: green in issue 0003.

RED: One smoke case per V0 label produces replay scores, recovery gains, top-1 attribution, and a confusion matrix row.

GREEN: Add Oracle Write, Oracle Compression, Injection-Oracle, Evidence-Given Reasoning, and per-replay table columns without adding deferred labels.

### Cycle 4: Coupled Failure

RED: A probe case has two close recovery gains. The expected output includes top-2 attribution and an ambiguity note.

GREEN: Add thresholded top-2 behavior without changing prior labels.

### Cycle 5: Post-Repair Context Replay

Status: green in issue 0005.

RED: A diagnosed case produces ECS guidance. The repaired context is rebuilt from corrected memory, repair guidance, and repaired evidence block, then the original failed query is rerun without injecting the gold answer. The expected result includes repair success or repair failure.

GREEN: Add the minimum post-repair result shape that records answer score, evidence score, token cost, and regression risk.

### Cycle 6: ECS Future Retrieval

RED: A diagnosed case produces ECS guidance. A future similar task should receive corrected memory and repair guidance, not the full failed trace.

GREEN: Add the minimum ECS surface needed for future retrieval.

### Cycle 7: Baseline Comparison

Status: green comparator metrics exist for the issue 0003 six-label smoke suite; revisit when real retrieval baselines are added.

RED: The same case has CMD attribution and subagent judge explanation. The evaluation report treats subagent judge as comparator, not ground truth.

GREEN: Add the minimal result shape that keeps CMD attribution and judge explanation separate.

### Cycle 8: Monitor Trigger Boundary

Status: early green coverage exists for forbidden monitor payload rejection; revisit when monitor decisions span multiple labels and failure modes.

RED: A subagent judge monitor flags a suspicious failed trace and triggers expensive replay, but it tries to emit a final label, ECS, memory write, gold answer, or full failed trace. The expected monitor output is rejected except for the replay trigger.

GREEN: Add the minimum leak-safe monitor result shape that can trigger replay without emitting forbidden payloads.

### Cycle 9: Adapter Boundary

RED: The standalone harness produces a replay request and result payload that a future memory-agent adapter could consume, without importing or depending on a real agent.

GREEN: Add the minimum adapter-boundary contract and keep the harness standalone.

### Cycle 10: Bad Memory Item Exclusion

RED: A V0 attribution result attempts to emit `item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, or `item_compression_distorted`. The expected result is rejected as outside V0 scoring scope.

GREEN: Add the minimum V0 label validation that permits only the six pipeline labels.

### Cycle 11: Monitor Enum-Locked Contract

RED: A Subagent Judge Monitor output contains free-form natural language in `anomaly_reason` (e.g., "the answer looks wrong compared to stored facts"). The expected result is rejected with `MonitorRejection(reason="invalid_anomaly_reason")`. Evidence pointers containing content text rather than opaque IDs are also rejected.

GREEN: Add enum validation for `anomaly_reason` (only `answer_vs_evidence_mismatch`, `retrieved_context_incomplete`, `evidence_recall_low`, `confidence_anomaly` accepted) and opaque-ID validation for evidence pointers (no whitespace, no content text). Add field-name blocklist for forbidden payloads (final label, ECS, gold answer, full failed trace, memory write).

### Cycle 12: Three-Value Post-Repair Assessment

Status: green in issue 0005.

RED: A diagnosed retrieval_error case is repaired (correct memory injected into context). Post-Repair Context Replay runs: `post_repair_answer_score=0.0`, `post_repair_evidence_score=1.0`. The expected `repair_assessment` is `"partial"`, not `"recovered"` or `"failed"`.

GREEN: Add `classify_repair_assessment(answer_score, evidence_score)` returning `recovered` (answer=1.0), `partial` (answer<1.0, evidence=1.0), or `failed` (answer<1.0, evidence<1.0). Test all three branches.

### Cycle 13: ECS Cause Item-Label-Name Prohibition

Status: green in issue 0005.

RED: An ECS record's `cause` field contains a V0-forbidden item label name: `"memory item is stale"` or `"item_conflict detected"`. The expected result is rejected at ECS validation time.

GREEN: Add ECS `cause` validation that rejects strings containing forbidden item label names (`item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`) and common natural-language equivalents. Allow descriptive state language (e.g., "stored preference was outdated relative to ground truth").

### Cycle 14: Ingestion Error Deferred Label Registration

Status: green in issue 0011. `ingestion_error` is now a V1 active label—`validate_v1_label("ingestion_error")` succeeds. `validate_v0_label("ingestion_error")` still raises `LabelValidationError`.

RED: A probe case or attribution result attempts to use `ingestion_error` as a V0 pipeline label. The expected result is rejected because `ingestion_error` is a V1 label, not a V0 label.

GREEN: Add `ingestion_error` to the known-deferred label registry (now moved to V1 active via issue 0011). `validate_v0_label("ingestion_error")` raises `LabelValidationError`. `validate_v1_label("ingestion_error")` succeeds. The full V1 label expansion implements the actual attribution logic.

### Cycle 15: CMD-Audit Sandbox Write Boundary

Status: green in issue 0005.

RED: A CMD-Audit operation attempts to write attribution output or repaired context to a path outside the replay-local sandbox (e.g., a production agent memory store path). The expected result is rejected.

GREEN: Add sandbox path validation to CMD-Audit write operations. All artifact writes (attribution table, comparison metrics, confusion matrix, post-repair results) must land under the designated sandbox output directory. Writes to paths outside the sandbox are rejected with a clear boundary error.

## Refactor Gates

- Do not extract abstractions until at least two cycles expose the same pressure.
- Prefer deep modules with small interfaces for probe cases, replay results, attribution, and ECS records.
- After each green cycle, check whether terminology still matches `../CONTEXT.md`.

## V1 Priority Behaviors

1. A failed case where gold evidence never reached the agent (`add()` never called with it) is attributed as `ingestion_error`, not `write_error`. — ✅ green (issue 0011)
2. A failed case where correct memory was stored but in the wrong store/tier is attributed as `route_error` after Oracle Route recovers it. — ✅ green (issue 0011)
3. A failed case where correct information was stored at wrong granularity is attributed as `granularity_error`.
4. A failed case where graph expansion introduced distractors is attributed as `graph_error` after Graph-Off recovers it.
5. A failed case where safety filtering incorrectly removed useful evidence is attributed as `safety_error` after Safety-Off recovers it.
6. With 11 labels, close replay deltas produce top-2 or multi-label attribution; coupled failure rate is expected higher than V0.
7. Baseline comparison includes memory-probe grid-comparison as a new comparator alongside evidence-recall, subagent judge, and random.
8. mem0 Adapter produces the same attribution label as standalone harness for the same probe case.
9. mem0 Adapter replays do not mutate the original mem0 store (sandboxed interception).
10. Letta Adapter produces correct attribution on Letta's tiered memory (core/archival/recall) without macro F1 regression vs mem0.
11. RPE Pre-Filter skips low-surprise cases with <5% false skip rate and ≥30% replay cost reduction.

## V1 Red-Green Sequence

### Cycle 16: Ingestion + Route Error Labels

Status: green (issue 0011). 44 behavior-level tests, 262 total tests, zero regressions.

RED: A probe case has gold evidence that never reached the agent at all (ingestion truncated). Oracle Write recovers the answer. V0 attributes it as `write_error`. V1 must attribute it as `ingestion_error`.

A second probe case has correct memory stored but in the wrong store/tier. Oracle Route recovers the answer by testing all stores. V1 must attribute it as `route_error`.

GREEN: Split `ingestion_error` from `write_error` in Oracle Write replay attribution via `has_ingestion_trace` boolean boundary. Add Oracle Route replay (enumerate stores/tiers, pick best recovery via `_collect_stores` + `_recover_from_store`). Extend label validator (`validate_v1_label`) to accept both new labels. Add V1 pipeline functions (`run_case_v1`, `run_cases_v1`, `run_case_full_v1`, `run_cases_full_v1`, `load_probe_cases_v1`, `assign_attribution_v1`, `run_v1_replay_portfolio`). Update ECS, metrics, and repairs modules for V1 compatibility.

Public behavior check:
- `ingestion_error` case: gold evidence has no corresponding `add()` call in trace (`has_ingestion_trace=false`); Oracle Write recovery gain > 0; label = `ingestion_error`.
- `route_error` case: gold evidence exists in wrong store; Oracle Route recovery gain > 0; label = `route_error`.
- Existing 6-label smoke suite: no label flips; macro F1 unchanged (all 6 labels match through V1 pipeline).
- Deferred label registry: `ingestion_error` moved from deferred to active; `route_error` moved from deferred to active.
- `validate_v1_label` accepts all 8 labels; `validate_v0_label` rejects `ingestion_error` and `route_error`.
- V1 portfolio: 7 replays (V0 6 + `oracle_route`); `V1_REPLAY_TO_LABEL` maps all 7 replays to valid V1 labels.

Detail map: `cmd_innovation_core/issues/0011-implement-ingestion-and-route-error-labels-implementation-details.md`

### Cycle 17: Granularity + Graph + Safety Error Labels

Status: green (issue 0012). 81 behavior-level tests, 345 total tests, zero regressions.

RED: Three probe cases cover the remaining V1 pipeline labels:
- `granularity_error`: correct info stored at wrong granularity (session summary too coarse). Oracle Granularity recovers by testing raw/event/session/persona levels.
- `graph_error`: graph expansion added distractors. Graph-Off replay recovers by disabling graph expansion.
- `safety_error`: safety filter incorrectly removed useful evidence. Safety-Off replay recovers by bypassing safety gate.

GREEN: Add Oracle Granularity replay (enumerate granularity levels, pick best). Add Graph-Off replay (run without graph expansion). Add Safety-Off replay (run without safety filter). Extend label validator to accept all five V1 labels. 11-label validation passes.

Public behavior check:
- Each new label has one smoke case with correct attribution.
- 11-label confusion matrix has diagonal dominance on smoke suite.
- No label flips on existing V0 6-label smoke cases.
- `granularity_error`, `graph_error`, `safety_error` move from deferred to active.

### Cycle 18: 11-Label Coupled Failure Recalibration

Status: green (issue 0013). 42 behavior-level tests (shared with Cycle 19), 387 total tests, zero regressions.

RED: With 11 labels, close replay deltas are more frequent. A probe case has Oracle Compression and Oracle Granularity both recovering with close deltas (Δ difference < threshold). V0's top-2 threshold was calibrated for 6 labels. V1 must recalibrate.

GREEN: Add `top_k: int = 2` parameter to `assign_attribution_v1`. Add `top_k_labels` and `close_deltas` fields to `AttributionResult` with backward-compatible defaults. Compute `all_close` unbounded by `top_k`; cap `top_k_labels` at `top_k`; expose full delta distribution in `close_deltas`. Plumb `top_k` through `run_case_v1`, `run_cases_v1`, `run_case_full_v1`, `run_cases_full_v1`. Add `top_k_labels` and `close_deltas` columns to `write_attribution_table`. V0 `assign_attribution` sets `top_k_labels = top2_labels`, `close_deltas = ()`. Default `top_k=2, tie_margin=0.05` preserves exact existing behavior.

Public behavior check:
- Close-delta case (Δ difference < 0.05): top-2 attribution output with both labels. ✅
- Triple-close case (3 replays within 0.05): `top_k=3` produces 3 labels in `top_k_labels`, 2 in `top2_labels`, 3+ in `close_deltas`. ✅
- Single-dominant case (Δ difference > 0.10): top-1 only. ✅
- 4+ close deltas: `top_k_labels` capped at 3, `close_deltas` exposes all 4+. ✅
- Existing V0 coupled-failure cases still produce correct top-2 on 6-label subset. ✅

### Cycle 19: V1 Baseline Comparison with Memory-Probe

Status: green (issue 0013). 42 behavior-level tests (shared with Cycle 18), 387 total tests, zero regressions.

RED: The baseline comparison currently covers evidence-recall, subagent judge, and random. V1 adds memory-probe grid-comparison (write-strategy × retrieval-method) as a new comparator. CMD-Audit 11-label macro F1 must still exceed all baselines.

GREEN: Create `cmd_audit/memory_probe.py` (235 lines). Implement 3 write strategies (`_write_fact_extraction` Mem0-style, `_write_summarization` MemGPT-style, `_write_raw_chunks`) producing `tuple[MemoryItem, ...]`. Implement 2 retrieval helpers (`_retrieve_top1_cosine`, `_retrieve_top1_bm25`) reusing public helpers from `retrieval_baselines.py`; `_retrieve_top1_hybrid` removed per issue 0008 (sparse+sparse cannot deliver semantic recovery; dense retrieval deferred to V1 adapter layer). Build grid runner `run_memory_probe_case` (6 cells: 3 write × 2 retrieve) and aggregate `run_memory_probe_baselines` (best-cell accuracy by write×retrieve pair). Make 4 internal helpers public (`tokenize`, `compute_bm25_scores`, `build_tfidf_vectors`, `cosine_similarity`). Add optional `memory_probe_best_accuracy` column to `write_comparison_metrics_table`. Export 9 new symbols from `__init__.py`.

Public behavior check:
- 6 cells per case (3 write × 2 retrieve: cosine + BM25), each with valid strategy/method and scores in [0,1]. ✅
- Aggregate `best_cell_accuracy` in [0,1]; best strategy/method are valid names. ✅
- Comparison metrics CSV includes `memory_probe_best_accuracy` column when provided, absent when None. ✅
- CMD-Audit macro F1 > all comparators including memory-probe on V0 smoke suite. ✅

### Cycle 20: mem0 Adapter Integration

Status: green (issue 0014). 30 behavior-level tests, 417 total tests, zero regressions.

RED: A probe case runs through the mem0 adapter path (intercept `add()` and `search()`) instead of fixture-controlled memory. The adapter path produces the same attribution label as the standalone harness for the same case. Adapter replays never mutate the original mem0 store.

GREEN: Implement `Mem0Adapter` with two cut points (`intercept_add`, `intercept_search`). Wire adapter into existing `ReplayEngine` without changing Attribution or ECS layers. Add sandbox checksum verification that mem0 store is unchanged after replay. `run_mem0_replay_portfolio` executes 10 replays (6 intercepted + 4 V1 passthrough). `check_v1_to_v2_gate` now accepts `mem0_integrated` parameter.

Public behavior check:
- Same 6 cases from V0 smoke suite: adapter path labels match standalone path labels. ✅
- Case with `ingestion_error`: adapter correctly identifies `add()` never called. ✅
- Case with `retrieval_error`: adapter correctly identifies `search()` miss. ✅
- Store checksum before/after replay is identical. ✅
- Adapter macro F1 on 6-label suite == standalone macro F1 (1.000 on smoke). ✅

Detail map: `cmd_innovation_core/issues/0014-integrate-mem0-adapter-implementation-details.md`

### Cycle 21: Letta Adapter + V1→V2 Gate Check

Status: green (issue 0015). 44 behavior-level tests, 453 total tests, 622 subtests, zero regressions.

RED: A probe case runs through the Letta adapter path (intercept core/archival/recall tier operations). The Letta adapter produces correct attribution on Letta's tiered memory architecture. V1→V2 gate: two agents (mem0 + Letta) both integrated without macro F1 regression.

GREEN: Implement `LettaAdapter` with tier-aware interception (`intercept_core_write`, `intercept_archival_store`, `intercept_recall`). Shared `_intercept_write_side` function for both write-side cut points. `run_letta_replay_portfolio` executes 10 replays (6 intercepted + 4 V1 passthrough). `check_v1_to_v2_gate` now accepts `letta_integrated` parameter alongside `mem0_integrated`. Sandbox checksum over `sorted(core_blocks + archival_blocks)`. Cross-agent non-regression verified: mem0 and Letta independently produce identical labels.

Public behavior check:
- Letta adapter correctly routes all 6 V0 replays across three cut points (core/archival/recall). ✅
- `route_error` attribution is structurally supported on Letta (tier miss) where it was N/A on mem0 (flat store). ✅
- 6-label macro F1 on Letta = standalone macro F1 = 1.000 (no regression). ✅
- Cross-agent non-regression: mem0 labels unchanged when Letta adapter exists. ✅
- V1→V2 gate passes with both `mem0_integrated=True` and `letta_integrated=True`. ✅
- Sandbox checksum unchanged after all three cut point interceptions. ✅

Detail map: `cmd_innovation_core/issues/0015-letta-adapter-implementation-details.md`

### Cycle 22: RPE Pre-Filter Optimization

Status: planned (issue 0016).

RED: Subagent Judge Monitor triggers full 11-replay portfolio for every anomaly. An RPE pre-filter should skip low-surprise cases where evidence is already in retrieved context, reducing replay cost without missing pipeline errors.

GREEN: Implement RPE scoring (surprise * utility). Add gating decision: high RPE → trigger; low confidence → trigger (safety net); low RPE + high confidence → skip. Run on 50+ probe cases. Measure skip rate, false skip rate, attribution recall.

Public behavior check:
- False skip rate < 5% (skipped cases that actually had pipeline errors).
- Replay cost reduction ≥ 30% (vs triggering full portfolio on every anomaly).
- Attribution recall after pre-filter ≥ 0.95 (vs no pre-filter).

### Cycle 23: Provenance Tracking — Execution Lineage DAG

Status: planned (issue 0017).

RED: A `MemoryItem` created during counterfactual replay has no record of which upstream items or operations influenced it. After `graph_error` attribution, there are no provenance edges referencing which graph-distractor items caused the failure. Tampering with source evidence after recording is undetectable.

GREEN: Add `Citation` and `ProvenanceEdge` dataclasses to `cmd_audit/models.py`. Add `provenance: Optional[List[ProvenanceEdge]]` field to `MemoryItem`. Add `cmd_audit/provenance.py` with `record_provenance_edge()`, `compute_provenance_completeness()`, `detect_tamper()`, `get_graph_distractor_edges()`. Wire provenance recording into all 10 replay paths (6 V0 + 4 V1). Wire into mem0 and Letta adapter replay paths. Add `distractor_provenance_ids` to `AuditResult` for `graph_error` cases.

Public behavior check:
- `Citation` and `ProvenanceEdge` importable from `cmd_audit.models`.
- One smoke case per replay type: `len(item.provenance) >= 1` for items created/modified during replay.
- `compute_provenance_completeness()` returns fraction in [0, 1]; target ≥ 0.80 on 596-case suite.
- `detect_tamper(edge, modified_source, session_key)` returns `True`; `detect_tamper(edge, original_source, session_key)` returns `False`.
- `graph_error` smoke case: `AuditResult.distractor_provenance_ids` non-empty, referencing graph-distractor items.
- Existing `MemoryItem` without provenance (or `provenance=None`) works in all existing pipelines — all 453 existing tests pass.
- Both mem0 and Letta adapter paths record provenance edges on at least one smoke case.
- `attribution_table.csv` gains optional `distractor_provenance_ids` column; `comparison_metrics.csv` gains `provenance_completeness` row.

### Cycle 24: Subagent-Based LLM Scoring (Issue 0019)

Status: Phase A GREEN, Phase B planned. TDD cycle: 24.

**Phase A — COMPLETE (2026-05-21):**
1. `llm_client.py`: Provider-agnostic LLM API client (`generate(prompt) -> str`). OpenAI-compatible, stdlib `urllib.request`. Handles: model unreachable, empty response, Unicode errors.
2. `llm_judge.py`: `build_judge_prompt` (observable artifacts only, no gold leak) + `parse_label_from_response` (structured LABEL:/EXPLANATION: parser).
3. `baselines.py`: `run_llm_judge_baseline` with 3-mode fallback (None-client / parse error / success). `llm_judge` as 4th comparator auto-appears in `comparison_metrics.csv` via existing `comparator_results` iteration (zero harness/metrics/writers changes).
4. 32 tests, 8 classes. 645 total tests pass. Detail map: `issues/0019-phase-a-llm-judge-baseline-implementation-details.md`.

**Phase B — `subagent_runner.py` + `llm_scoring.py` + `hooks.py`:**
3. `subagent_runner.py`: Minimal `run(system_prompt: str, user_message: str) -> str`. Isolated context window, dedicated system prompt, no tool access, returns result only.
4. `hooks.py`: Two pure validation functions:
   - `validate_context_isolation(context: dict) -> None`: Raises `ContextLeakError` if context contains case_id, gold_label, ptype, or other cross-case data.
   - `validate_output_format(output: str, expected: tuple[str, ...]) -> str`: Returns parsed output if valid binary; raises `OutputFormatError` on non-binary output.
5. `llm_scoring.py`: `EvidenceVerifier`, `AnswerVerifier`, `SubagentScorer`.
   - `EvidenceVerifier`: Receives atomic `{FACT, TEXT}` via subagent_runner, outputs `PRESENT | ABSENT`. Active in Phase B.
   - `AnswerVerifier`: Receives atomic `{ANSWER, GOLD_ANSWER}` via subagent_runner, outputs `EQUIVALENT | NOT_EQUIVALENT`. Implemented but not wired — deferred to Decision B.
   - `SubagentScorer.score_evidence(gold_evidence, text) -> float`: Matches `evidence_recall_from_text` contract. Internally splits gold_evidence into N atomic calls, aggregates `count(PRESENT)/N`. Enforces hooks at subagent call boundary. Fallback to `evidence_recall_from_text` on LLM unavailable or parse failure (retry once with stricter prompt first).

**Integration:**
6. `_score_recovered_evidence` in `replays.py`: Accepts optional `scorer: Callable[[tuple[GoldEvidence, ...], str], float] | None` parameter. When `None`, uses `evidence_recall_from_text` (default/fallback). When provided, delegates to `scorer(gold_evidence, evidence_block)`.

Public behavior check:
- `SubagentScorer.score_evidence(gold_evidence, text)` returns float in [0, 1] matching `evidence_recall_from_text` contract.
- EvidenceVerifier subagent context contains exactly `{FACT, TEXT, STANDARD}` — no case_id, no gold_label.
- `validate_context_isolation` raises `ContextLeakError` when case_id injected into context.
- `validate_output_format` raises `OutputFormatError` on non-binary output; retry once with stricter prompt before fallback.
- `LLMJudgeBaseline` produces valid `DiagnosisPrediction` for all 596 cases; appears in `comparison_metrics.csv`.
- CMD attribution accuracy >= 0.95 (LLM variance tolerance) on 596 cases with SubagentScorer.
- Deterministic phrase-matching path preserved: `_score_recovered_evidence(case, name, block, tracker)` without scorer uses `evidence_recall_from_text`.
- All 613 existing tests pass without modification.
