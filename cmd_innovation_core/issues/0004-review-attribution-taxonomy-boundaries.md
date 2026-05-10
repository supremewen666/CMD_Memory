---
title: Review attribution taxonomy boundaries
labels:
  - done
type: HITL
blocked_by:
  - 0003-generate-counterfactual-attribution-table
user_stories:
  - 11
  - 14
  - 21
---

# Review attribution taxonomy boundaries

## Parent

`prd/cmd_minimal_probe_prd.md`

## What to Build

Review ambiguous attribution cases after the first table exists, then confirm whether the V0 taxonomy boundaries are working for `write_error`, `compression_error`, `premature_extraction_error`, `retrieval_error`, `injection_error`, and `reasoning_error`.

## Acceptance Criteria

- [ ] Ambiguous cases are grouped by confusion pattern.
- [ ] `premature_extraction_error` remains first-class unless evidence shows the Verbatim Event Oracle boundary is not useful.
- [ ] Top-2 or multi-label attribution rules are clarified for coupled failures.
- [ ] Subagent judge baseline and monitor roles are evaluated separately from final CMD attribution.
- [ ] `CMD-Audit` and `CMD-Skill Adapter` remain separate terms in `../CONTEXT.md`.
- [ ] V0 bad-memory-item exclusion remains a boundary rule, not a new feature branch.
- [ ] Any proposed V1/V2 labels are listed separately from V0 fixes.
- [ ] Any terminology changes are reflected in `../CONTEXT.md`.
- [ ] `ingestion_error` is registered as a deferred V1 label; `write_error` subsumes ingestion-absence cases in V0.
- [ ] Verbatim Event Oracle vs Oracle Retrieval crossover edge cases reviewed: (A) both recover evidence → gain ranking naturally picks the stronger replay, not a taxonomy problem; (B) both fail but Oracle Compression succeeds → additional replay cost is design-internal, bounded within smoke suite.
- [ ] ECS `cause` item-state description rules confirmed: natural language allowed, forbidden item label names and natural-language equivalents prohibited.

## Blocked By

- `0003-generate-counterfactual-attribution-table`
