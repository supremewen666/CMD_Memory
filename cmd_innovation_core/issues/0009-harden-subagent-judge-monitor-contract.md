---
title: Harden Subagent Judge Monitor contract
labels:
  - done
type: AFK
blocked_by:
  - 0002-establish-baselines-and-judge-monitor
user_stories:
  - 6
  - 27
  - 28
---

# Harden Subagent Judge Monitor contract

## Parent

`prd/cmd_minimal_probe_prd.md`

## What to Build

Enforce the Subagent Judge Monitor leak-safety contract at the implementation level. Lock `anomaly_reason` to a predefined enum, restrict evidence pointers to opaque IDs only, and reject any output that includes free-form natural language, final labels, ECS, gold answers, memory writes, or full failed traces.

## Acceptance Criteria

- [ ] `anomaly_reason` is locked to a predefined enum: `answer_vs_evidence_mismatch`, `retrieved_context_incomplete`, `evidence_recall_low`, `confidence_anomaly`. Any other value is rejected.
- [ ] Free-form natural language in `anomaly_reason` is rejected at validation time.
- [ ] Evidence pointers are restricted to opaque IDs only (`memory_id` or `event_id`), with no content text, no excerpts, no phrase snippets.
- [ ] Monitor output that includes a final attribution label, ECS record, gold answer, full failed trace, wrong memory payload, or User Memory / Failure Memory write is rejected.
- [ ] Rejection behavior is tested at the contract boundary with behavior-level tests.
- [ ] The existing monitor trigger path (replay triggering without forbidden payloads) continues to pass.

## Blocked By

- `0002-establish-baselines-and-judge-monitor`
