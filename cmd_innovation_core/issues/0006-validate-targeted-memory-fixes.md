---
title: Validate targeted memory fixes
labels:
  - needs-triage
type: AFK
blocked_by:
  - 0005-validate-post-repair-context-replay
user_stories:
  - 16
  - 20
---

# Validate targeted memory fixes

## Parent

`prd/cmd_minimal_probe_prd.md`

## What to Build

Use CMD attribution labels to choose targeted memory fixes, then compare those repairs against undifferentiated hard-case updates using Post-Repair Context Replay results. The slice is complete when repair success is measured per label and summarized in a claim-ready result table.

## Acceptance Criteria

- [ ] Each major attribution label maps to a repair action.
- [ ] CMD-guided repairs are compared with undifferentiated hard-case updates.
- [ ] Repair success is grounded in Post-Repair Context Replay, not only in ECS text quality.
- [ ] Metrics include answer F1 or accuracy, evidence recall, fixed failures, and token cost.
- [ ] Results are broken down by attribution label.
- [ ] The claim ledger records whether targeted fixes are actually better.

## Blocked By

- `0005-validate-post-repair-context-replay`
