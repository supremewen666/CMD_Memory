---
title: Enforce evidence-driven version gates
labels:
  - needs-triage
type: HITL
blocked_by:
  - 0004-review-attribution-taxonomy-boundaries
  - 0005-validate-post-repair-context-replay
  - 0007-validate-ecs-failure-memory-recurrence
user_stories:
  - 20
  - 32
---

# Enforce evidence-driven version gates

## Parent

`prd/cmd_minimal_probe_prd.md`

## What to Build

Define and enforce evidence-driven version gates for V0→V1→V2 transitions. Version gates are driven by credibility evidence, not feature stacking. Each gate requires both evidence artifacts AND functional completeness before the version is locked.

The final gate decision is HITL: a human reviews the evidence and decides whether the threshold is met.

## Acceptance Criteria

- [ ] V0→V1 gate is defined: four V0 evidence artifacts must pass paper-claim thresholds:
  - `attribution_table.csv`: CMD macro F1 exceeds heuristic and subagent judge baselines
  - `attribution_confusion_matrix.csv`: diagonal dominance over off-diagonal for all six V0 labels
  - `comparison_metrics.csv`: CMD-Audit outperforms evidence_recall, subagent_judge, and random_label on attribution accuracy and top-2 accuracy
  - Post-Repair Context Replay table: repair assessment distribution (`recovered` / `partial` / `failed`) supports repair-validity claim
- [ ] V1→V2 gate is defined: at least two distinct memory agents integrated through the Adapter Interface without macro F1 regression on the same probe suite.
- [ ] Gate status is tracked in `CLAUDE.md` or a dedicated gate-tracking document, not in code.
- [ ] Each gate check is documented with a dated review note recording which artifacts were inspected, whether the threshold was met, and the human decision.
- [ ] If a gate is not met, the note records what specific evidence is missing and what V0 work must complete before re-review.
- [ ] Gates do not block ongoing implementation work; they gate the version lock claim only.

## Blocked By

- `0004-review-attribution-taxonomy-boundaries`
- `0005-validate-post-repair-context-replay`
- `0007-validate-ecs-failure-memory-recurrence`
