# TDD Plan: CMD Tracer Bullets

This is a non-code TDD skeleton. It names behavior to test through future public interfaces before implementation starts.

## Current Green State

Verified on 2026-05-09:

- Cycle 1 has a green smoke path: a recoverable extracted memory missed by baseline retrieval is attributed as `retrieval_error`.
- Issue 0003 has green coverage for all six V0 replay labels and writes the attribution table plus confusion matrix.
- Baseline comparison has early green coverage: evidence-recall, subagent judge, and random-label comparators are kept separate from CMD-Audit attribution.
- Monitor boundary has early green coverage: forbidden monitor payload fields are rejected.

The next implementation step should review taxonomy boundaries in issue 0004 before moving to Post-Repair Context Replay.

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
- `ingestion_error` is a registered deferred V1 label; it is rejected by V0 label validation but queryable for V1 planning.
- CMD-Audit write operations are restricted to replay-local sandbox; writes to paths outside the sandbox are rejected.

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

Status: green in issue 0004/0009.

RED: A probe case or attribution result attempts to use `ingestion_error` as a V0 pipeline label. The expected result is rejected because `ingestion_error` is a deferred V1 label, not a V0 label.

GREEN: Add `ingestion_error` to the known-deferred label registry. `validate_v0_label("ingestion_error")` raises `ValueError`. The deferred label list is queryable so V1 planning can enumerate it.

### Cycle 15: CMD-Audit Sandbox Write Boundary

Status: green in issue 0005.

RED: A CMD-Audit operation attempts to write attribution output or repaired context to a path outside the replay-local sandbox (e.g., a production agent memory store path). The expected result is rejected.

GREEN: Add sandbox path validation to CMD-Audit write operations. All artifact writes (attribution table, comparison metrics, confusion matrix, post-repair results) must land under the designated sandbox output directory. Writes to paths outside the sandbox are rejected with a clear boundary error.

## Refactor Gates

- Do not extract abstractions until at least two cycles expose the same pressure.
- Prefer deep modules with small interfaces for probe cases, replay results, attribution, and ECS records.
- After each green cycle, check whether terminology still matches `../CONTEXT.md`.
