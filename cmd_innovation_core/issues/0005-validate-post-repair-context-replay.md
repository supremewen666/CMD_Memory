---
title: Validate Post-Repair Context Replay
labels:
  - done
type: AFK
blocked_by:
  - 0003-generate-counterfactual-attribution-table
  - 0004-review-attribution-taxonomy-boundaries
user_stories:
  - 17
---

# Validate Post-Repair Context Replay

## Parent

`prd/cmd_minimal_probe_prd.md`

## What to Build

Verify that CMD can rebuild a repaired context after attribution and ECS drafting, then rerun the original failed query to check whether the repair actually restores the task. This is a V0 gate, not a new feature surface.

## Acceptance Criteria

- [ ] The flow includes `AttributionAssigned -> ECSDrafted -> RepairedContextBuilt -> PostRepairRetested -> RepairValidated / RepairFailed`.
- [ ] The repaired context may include corrected memory, repair guidance, and repaired evidence block.
- [ ] The retest reruns the original failed query and does not inject the gold answer.
- [ ] The result records `post_repair_answer_score`, `post_repair_evidence_score`, and a three-value `repair_assessment` (`recovered` / `partial` / `failed`), not a binary gate. `partial` means evidence recovered but answer still wrong—exposes coupled failures.
- [ ] Token cost and regression risk are recorded per retest.
- [ ] The comparison includes a generic hard-case update baseline.
- [ ] Write operations are limited to replay-local sandbox; no writes to persistent agent memory.

## Blocked By

- `0003-generate-counterfactual-attribution-table`
- `0004-review-attribution-taxonomy-boundaries`
