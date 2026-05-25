---
title: Implement Provenance Tracking — Execution Lineage DAG + trace-mem Citation
labels:
  - ready-for-human
type: ready-for-human
blocked_by: ~
user_stories:
  - 44
  - 45
  - 46
  - 47
tdd_cycle: 23
---

# Implement Provenance Tracking — Execution Lineage DAG + trace-mem Citation

## Parent

`prd/cmd_minimal_probe_prd.md` V1 Scope, §D Infrastructure — Provenance Tracking (US44-US47, AC16)

## What to Build

Add influence provenance to every `MemoryItem` produced or modified during counterfactual replay. Phase 1 records in-edge derivation via an Execution Lineage DAG: for each item, which upstream items and operations influenced its creation. Each edge carries a trace-mem HMAC citation (trajectory turn, character span, content hash) for tamper detection. `graph_error` attribution must reference specific provenance edges showing which graph-distractor items influenced the failed answer.

Architecture per Decision 28: `ProvenanceEdge` (source_id, target_id, operation, Citation) + `Citation` (trajectory_turn, char_span, content_hash).

## Implementation Detail

### Core Data Model

```python
@dataclass
class Citation:
    """trace-mem HMAC citation to originating trajectory evidence."""
    trajectory_turn: int       # source turn number
    char_span: tuple[int, int] # (start, end) in source text
    content_hash: str          # HMAC(content, session_key)

@dataclass
class ProvenanceEdge:
    """In-edge derivation record: which item+operation influenced this item."""
    source_id: str       # upstream MemoryItem id
    target_id: str       # this MemoryItem's id
    operation: str       # write | compress | extract | inject | retrieve
    citation: Citation
    timestamp: float
```

`MemoryItem` gains one new optional field: `provenance: Optional[List[ProvenanceEdge]]` (in-edges, default `None` for backward compatibility).

### Recording Points (6 per replay type)

Each of the 6 V0 replay paths records provenance at the point where it creates or modifies memory items:

| Replay | Recording Point | `operation` value | Source Items |
|--------|----------------|-------------------|-------------|
| Oracle Write | After injecting new evidence item | `write` | Raw events that contained the evidence |
| Oracle Compression | After decompressing/restoring evidence | `compress` | Compressed item that lost evidence |
| Verbatim Event Oracle | After extracting from raw events | `extract` | Raw event item(s) containing verbatim evidence |
| Oracle Retrieval | After retrieving correct item | `retrieve` | Correctly-retrieved memory item |
| Injection Oracle | After injecting evidence into slot | `inject` | Retrieved items + injected evidence item |
| Evidence-Given Reasoning | After reasoning step (reference only) | `reason` | All injected evidence items |

V1 replay paths (Oracle Route, Oracle Granularity, Graph-Off, Safety-Off) also record provenance:

| Replay | Recording Point | `operation` value |
|--------|----------------|-------------------|
| Oracle Route | After cross-store enumeration | `route` |
| Oracle Granularity | After re-expression | `extract` (granularity adjustment is re-extraction) |
| Graph-Off | After disabling graph expansion | `retrieve` (direct retrieval without graph) |
| Safety-Off | After bypassing safety filter | `inject` (unblocked evidence injection) |

### `graph_error` Provenance Integration

`graph_error` attribution must surface which graph-distractor items influenced the failed answer. After `Graph-Off` replay produces recovery, CMD must:
1. Compare the memory items present in baseline (graph-expanded) vs `graph_off` replay
2. Identify distractor items: items present in baseline retrieval but absent from `graph_off` retrieval
3. Record provenance edges from each distractor item to the final answer item (operation=`retrieve`)
4. Include these edges in the `AuditResult` for `graph_error` cases

### Provenance Completeness Metric

Compute across the 596-case suite:
- `provenance_completeness` = fraction of `MemoryItem` instances with non-empty `provenance`
- Target: ≥ 80% (some items are created outside replay paths)
- Items created outside replay (e.g., baseline memory) may have empty provenance

### HMAC Content Hash

Session key derived from `hashlib.sha256(probe_case_id.encode()).hexdigest()`.
Content hash = `hmac.new(session_key, source_text.encode(), hashlib.sha256).hexdigest()`.
Tamper detection: when replay reads a source item, recompute hash and compare — mismatch → flag `tamper_detected=True` in the edge.

### New/Updated Files

| File | Change |
|------|--------|
| `cmd_audit/models.py` | Add `Citation`, `ProvenanceEdge` dataclasses; add `provenance` field to `MemoryItem` |
| `cmd_audit/provenance.py` | **New** — `record_provenance_edge()`, `compute_provenance_completeness()`, `detect_tamper()`, `get_graph_distractor_edges()` |
| `cmd_audit/replays.py` | Add provenance recording to all 10 replay functions |
| `cmd_audit/attribution.py` | Add provenance edges to `AuditResult` for `graph_error` cases |
| `cmd_audit/harness.py` | Pass provenance state through `run_case_v1` |
| `cmd_audit/adapters/mem0_replays.py` | Add provenance recording to mem0 intercepted replays |
| `cmd_audit/adapters/letta_replays.py` | Add provenance recording to Letta intercepted replays |
| `cmd_audit/__init__.py` | Export new symbols |
| `tests/test_cmd_audit_issue17_provenance.py` | **New** — provenance completeness, tamper detection, graph_error edges, backward compatibility |

## Acceptance Criteria

1. **AC16.1 — Data model**: `Citation` and `ProvenanceEdge` dataclasses importable from `cmd_audit.models`. `MemoryItem.provenance` field accepts `Optional[List[ProvenanceEdge]]`.

2. **AC16.2 — Recording per replay type**: Each of the 10 V1 replay paths records provenance edges on at least one smoke case. One smoke case per replay type with `len(item.provenance) >= 1` for items created/modified by that replay.

3. **AC16.3 — Provenance completeness**: `compute_provenance_completeness()` returns a float in [0.0, 1.0]. Measured on the full case suite; target ≥ 80%.

4. **AC16.4 — HMAC tamper detection**: `detect_tamper(edge, source_text, session_key)` returns `True` when content hash mismatches. Smoke test: valid edge passes, modified source text fails.

5. **AC16.5 — `graph_error` provenance edges**: Smoke case with `perturbation_label=graph_error` produces `AuditResult` with non-empty distractor provenance edges referencing specific graph-distractor items.

6. **AC16.6 — Backward compatibility**: Existing `MemoryItem` without `provenance` field (or `provenance=None`) works in all existing pipelines without modification. All 453 existing tests continue to pass.

7. **AC16.7 — Adapter compatibility**: Provenance recording works through both mem0 and Letta adapter paths. At least one smoke case per adapter produces items with non-empty provenance.

8. **AC16.8 — CSV output**: Attribution table (`attribution_table.csv`) gains optional `distractor_provenance_ids` column for `graph_error` rows. Comparison metrics CSV gains `provenance_completeness` row.

9. **AC16.9 — Paper-facing metric**: `provenance_completeness` reported in comparison metrics with value, denominator (total items), and numerator (items with provenance). Stretch: breakdown by replay type.

## Implementation Notes

- **Why not full cryptographic provenance (MemLineage) in V1**: MemLineage requires cryptographic commitment chains and verification — too heavy for minimum viable provenance. V1 needs basic lineage DAG; full crypto deferred to V2.
- **Why not MemQ TD(λ) now**: MemQ operates on top of a DAG for credit propagation. The DAG must exist first. Phase 1 builds the DAG; Phase 2 (V2) applies MemQ for cascade repair.
- **Relationship to trace-mem**: Citation format is shared infrastructure. If trace-mem counterfactual gate is integrated later (Decision 27 mentions it as optional prefilter), the Citation format enables interoperation.
- **Session key**: Per-probe-case session key — each case gets its own key for content hashing. No cross-case key sharing.
- **Memory items created outside replay**: Baseline memory items, probe case extracted_memory items — these have `provenance=None`. Provenance is only recorded for items created/modified during counterfactual replay.
- **Provenance edges are append-only**: Once recorded, edges are never modified. This matches the Execution Lineage DAG semantics (immutable derivation history).
