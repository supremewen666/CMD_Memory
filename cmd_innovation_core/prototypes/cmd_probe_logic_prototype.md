# Prototype Brief: CMD Probe Logic

## Branch

LOGIC prototype. The question is whether the CMD state model and attribution loop feel right before implementation.

## Current Prototype Status

The first executable smoke path already validates the simplest `retrieval_error` scenario and the issue 0002 comparator/monitor boundary. Keep this prototype brief as the non-code state model, not as production documentation.

The next prototype question is whether the same state model makes the Verbatim Event Oracle boundary obvious: raw events recover the evidence, extracted memory cannot, and the label should be `premature_extraction_error`.

Issue 0003 should not create a UI prototype. The relevant prototype remains LOGIC: a tiny terminal/state simulation that shows the full case state, replay portfolio, recovery gains, and attribution after each action.

## Prototype Question

Can a tiny interactive probe make it obvious when a failed memory case should be labeled as write, compression, retrieval, premature extraction, injection, or reasoning failure?

V0 only simulates six labels: `write_error`, `compression_error`, `premature_extraction_error`, `retrieval_error`, `injection_error`, and `reasoning_error`.

V1 (issues 0011-0013 complete) extends to eleven labels with `ingestion_error`, `route_error`, `granularity_error`, `graph_error`, and `safety_error`. The V1 replay portfolio has 10 replays (V0 6 + Oracle Route + Oracle Granularity + Oracle Graph + Oracle Safety).

V1 coupled-failure recalibration (issue 0013): configurable `top_k` parameter (default 2, supports 3+), `close_deltas` exposes all (label, delta) pairs within tie_margin. Memory-probe 3x2 grid baseline: 3 write strategies (fact_extraction, summarization, raw_chunks) × 2 retrieval methods (cosine, bm25; hybrid_rerank removed per issue 0008, dense retrieval deferred to V1 adapter layer) as aggregate diagnostic comparator.

Boundary rules: the prototype distinguishes CMD-Audit from a future CMD-Skill Adapter, keeps the subagent judge monitor leak-safe, and excludes bad memory item labels from V0 attribution.

## Throwaway Contract

- This prototype should be throwaway from day one.
- It should keep all state in memory.
- It should surface full case state after every action.
- It should be deleted or absorbed after the attribution loop is validated.
- No production data, persistence, polished UI, or broad error handling belongs here.

## Future One-Command Shape

When implementation starts, the intended command should be a single local command that launches an interactive terminal run. The exact command should follow the eventual project runner.

The prototype should assume a standalone research harness. It may show an adapter boundary, but it should not integrate with an existing memory agent.

For the current harness shape, the eventual one-command smoke run is expected to stay close to:

```text
python3 -m cmd_audit run --cases data/probe_cases/<v0_cases>.json --out artifacts/attribution_table.csv --metrics-out artifacts/comparison_metrics.csv
```

## State Model

1. `CaseLoaded`: query, raw events, extracted memory, gold evidence, gold answer, perturbation label.
2. `BaselineFailed`: baseline output and initial score are recorded.
3. `MonitorFlagged`: leak-safe subagent judge monitor attaches a high-recall replay trigger reason without emitting final labels, ECS, memory writes, gold answers, or full failed traces.
4. `ReplaysRun`: each selected replay has output, answer score, evidence score, recovery gain, and cost.
5. `AttributionAssigned`: top-1, top-2, and ambiguity notes are visible.
6. `ECSDrafted`: wrong memory, cause, corrected memory, repair action, and guidance are visible.
7. `RepairedContextBuilt`: corrected memory, repair guidance, and repaired evidence block are assembled without the gold answer.
8. `PostRepairRetested`: the original failed query is rerun against the repaired context.
9. `RepairValidated` / `RepairFailed`: repair success, evidence score, token cost, and regression risk are visible.
10. `RepairSimulated`: targeted repair is compared with a generic hard-case update.
11. `FutureCaseGuided`: future similar task receives corrected memory and repair guidance only.
12. `ProvenanceRecorded`: each MemoryItem created/modified during replay carries `provenance: List[ProvenanceEdge]` recording in-edge derivation (source_id, target_id, operation, Citation). Items from outside replay (baseline, probe case) have `provenance=None`.
13. `ProvenanceValidated`: completeness metric computed (fraction of items with non-empty provenance), tamper detection run on all edges, `graph_error` distractor edges surfaced. Target: ≥ 80% completeness on the 596-case suite.

## Actions To Simulate

- Load a labeled failure case.
- Run baseline memory answer.
- Trigger subagent judge monitor.
- Build the V0 Replay Portfolio in the fixed V0 label order.
- Run Oracle Write replay.
- Run Oracle Compression replay.
- Run Oracle Retrieval replay.
- Run Verbatim Event Oracle replay.
- Run Injection-Oracle replay.
- Run Evidence-Given Reasoning replay.
- Run Oracle Route replay (V1: enumerate stores/tiers, pick best evidence recovery).
- Run Oracle Granularity replay (V1: enumerate granularity levels—raw/event/session/persona, pick best).
- Run Graph-Off replay (V1: run without graph expansion to isolate distractor failures).
- Run Safety-Off replay (V1: run without safety filter to isolate false-positive filtering failures).
- Assign attribution from recovery gains.
- Draft Error-Cause-Solution.
- Build repaired context without injecting the gold answer.
- Rerun the original failed query against repaired context.
- Simulate targeted fix.
- Simulate future Failure Memory retrieval.
- Show adapter-boundary payload for a future memory-agent integration.
- Record provenance edges for each replay-created MemoryItem (6 V0 + 4 V1 replays).
- Compute provenance completeness across the case suite.
- Run HMAC tamper detection on provenance edges.
- Surface graph-distractor provenance edges for `graph_error` cases.

## Scenario Cards

### Retrieval-Looking But Premature Extraction

Raw events contain the needed evidence, but extracted memory does not. Verbatim Event Oracle recovers the answer while Oracle Retrieval over extracted memory does not. Expected result: do not label as `retrieval_error`.
Expected label: `premature_extraction_error`.

State details to surface:

- gold evidence points to a raw event;
- gold evidence does not point to an extracted Memory Item;
- Oracle Retrieval evidence score stays low;
- Verbatim Event Oracle evidence score recovers;
- top replay maps to `premature_extraction_error`.

### Correct Memory, Bad Retrieval

Extracted memory contains gold evidence, but baseline retrieval misses it. Oracle Retrieval recovers the answer. Expected result: label `retrieval_error`.

### Correct Evidence, Bad Reasoning

Baseline retrieves the right evidence but answers incorrectly. Evidence-Given Reasoning recovers the answer. Expected result: label `reasoning_error`.

### Tied Recovery

Oracle Compression and Oracle Retrieval both recover the answer with close deltas. Expected result: output top-2 attribution and mark coupled failure.

### Leak-Safe Monitor

The subagent judge monitor flags a suspicious trace. Expected result: it triggers replay but emits no final label, ECS, memory write, gold answer, or full failed trace.

### Post-Repair Retest

ECS provides corrected memory and repair guidance. Expected result: rebuilt context recovers the original failed query without injecting the gold answer.

### Graph-Expansion Distractor with Provenance

Graph expansion adds distractor items that cause retrieval to miss the correct evidence. Graph-Off replay recovers the answer, producing `graph_error` attribution. Provenance edges on the failed answer item point to specific distractor items. Expected result: `AuditResult` carries `distractor_provenance_ids` referencing the items that graph expansion introduced. Evidence recall recovers when those items are removed.

State details to surface:
- baseline retrieval includes graph-expanded items;
- `graph_off` retrieval excludes graph-expanded items and recovers evidence;
- provenance edges on the failed answer reference distractor item ids;
- distractor items carry provenance edges back to the graph expansion operation.

### Provenance Completeness Measurement

After all 10 replay paths record provenance edges, compute completeness across the case suite. Expected result: ≥ 80% of MemoryItem instances created during replay have non-empty provenance. Items from baseline (probe case data, initial memory state) are excluded from the denominator.

State details to surface:
- total replay-created items across suite;
- items with non-empty provenance;
- items with empty provenance (and which replay paths they came from);
- completeness ratio per replay type.

### HMAC Tamper Detection

A provenance edge's content hash is computed from the source text at record time. If an attacker (or bug) modifies the source text after recording, recomputing the hash produces a mismatch. Expected result: `detect_tamper()` returns `True` for modified source text, `False` for unmodified source text.

State details to surface:
- original content hash recorded with edge;
- recomputed hash from current source text;
- match/mismatch boolean;
- edge metadata (source_id, trajectory_turn, char_span) for audit trail.

## Verdict Placeholder

Keep only the answer from this prototype: whether the state model exposes enough information for humans to trust CMD attribution before implementation.

**Verdict (V0 verified, 2026-05-10):** Yes. The state model's 11 states (CaseLoaded → FutureCaseGuided) proved sufficient for all V0 tracer bullets (issues 0001-0010). Each state transition exposes exactly the information needed for the next step without leaking implementation details.

**Verdict (premature_extraction boundary, 2026-05-10):** Confirmed. `evidence_recall_from_text(gold_evidence, memory_item.text)` is the hard gate. When MemoryItem text lacks evidence phrases, Verbatim Event Oracle correctly recovers and the label is `premature_extraction_error`, never `retrieval_error`.

**Verdict (V1 label expansion, 2026-05-19):** The state model extends cleanly. Adding 5 pipeline labels (ingestion, route, granularity, graph, safety) required no state model changes — only replay additions and label registry expansion. The 10-replay V1 portfolio maps isomorphically to the ReplaysRun state.

**Verdict (coupled-failure recalibration, 2026-05-19):** `top_k` parameter + `close_deltas` transparency is the right approach. Configurable `top_k` (default 2, supports 3+) handles 11-label delta density without over-engineering. `close_deltas` provides full transparency unbounded by `top_k` — downstream consumers can inspect 4+ close deltas without truncation.

**Verdict (memory-probe baseline, 2026-05-19):** The 3×2 grid (write × retrieve; dense deferred to V1 per issue 0008) is fundamentally different from CMD's case-level counterfactual attribution — it's an aggregate diagnostic, not a per-case predictor. The distinction is correctly captured by the `memory_probe_best_accuracy` column in comparison metrics, which serves as a dataset-level reference ceiling rather than a head-to-head comparator.

**Verdict (provenance tracking, 2026-05-20):** Provenance fits cleanly as two new terminal states (12-13) after FutureCaseGuided. `ProvenanceRecorded` captures the derivation DAG during replay execution — each replay that creates/modifies a MemoryItem records in-edges. `ProvenanceValidated` runs after all replays complete, computing completeness and running tamper detection. The state model does not need restructuring: provenance is additive state recorded alongside existing replay execution, not a new sequential phase. The key design decision is that provenance edges are append-only (immutable derivation history) and only replay-created items carry provenance (baseline items have provenance=None). `graph_error` attribution is the only label that requires provenance edges in the attribution output (distractor item references).
