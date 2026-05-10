---
title: Validate ECS Failure Memory recurrence reduction
labels:
  - needs-triage
type: AFK
blocked_by:
  - 0006-validate-targeted-memory-fixes
user_stories:
  - 18
  - 19
  - 20
---

# Validate ECS Failure Memory recurrence reduction

## Parent

`prd/cmd_minimal_probe_prd.md`

## What to Build

Evaluate whether Error-Cause-Solution Failure Memory reduces recurrence on future similar tasks. Compare no Failure Memory, retrieval of complete failed traces, and retrieval of only corrected memory plus repair guidance.

## Acceptance Criteria

- [ ] ECS records contain error type, wrong memory, original evidence, cause, corrected memory, repair action, repair guidance, and trigger signature.
- [ ] ECS `cause` may describe item state in natural language but must not use V0-forbidden item label names (`item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`) or re-declare them through natural language equivalents.
- [ ] Future tasks retrieve `corrected_memory + repair_guidance`, not complete failed traces.
- [ ] The comparison includes hallucination rate, conflict recurrence, pollution recurrence, answer score, evidence recall, and added token cost.
- [ ] Results state whether Failure Memory is useful enough to remain in scope.
- [ ] Any positive claim is backed by a table or marked as unproven.

## Blocked By

- `0006-validate-targeted-memory-fixes`
