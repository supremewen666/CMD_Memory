---
title: Implement granularity_error, graph_error, and safety_error labels
labels:
  - AFK
type: AFK
blocked_by: "0011"
user_stories:
  - 39
  - 40
  - 41
tdd_cycle: 17
---

# Implement granularity_error, graph_error, and safety_error labels

## Parent

`prd/cmd_minimal_probe_prd.md` V1 Scope

## What to Build

Add the remaining three V1 pipeline labels with their corresponding counterfactual replays. These labels complete the 11-label V1 attribution space.

## Implementation Detail

### granularity_error

- Add Oracle Granularity replay: enumerate granularity levels (raw, event, session, persona, procedure, graph). For each level, re-express the stored memory at that granularity, re-run retrieval and answer, score recovery gain.
- Attribution: if best-granularity recovery gain > baseline and original granularity was not the best, label = `granularity_error`.
- Granularity enumeration is configurable per probe case (not all cases exercise all levels).

### graph_error

- Add Graph-Off replay: run retrieval and answering with graph expansion disabled.
- Add Graph-Only replay (optional, for isolation): run with ONLY graph-retrieved evidence.
- Attribution: if Graph-Off recovery gain > baseline, label = `graph_error`.
- If Graph-Off fails but Graph-Only succeeds, label may be `graph_error` with ambiguity note (graph contains evidence but expansion introduced distractors that masked it).

### safety_error

- Add Safety-Off replay: run with safety filter bypassed.
- Add Safety-Oracle replay (optional): run with oracle safety decision (correctly allow/block).
- Attribution: if Safety-Off recovery gain > baseline, label = `safety_error`.
- Safety filter behavior is mocked in standalone harness via a `safety_filter_blocked` flag on probe cases.

### Non-regression

- Run V0 6-label + V1 2-label (0011) smoke suite. All existing labels must match.
- New labels must not flip existing cases unless the case deliberately exercises the new failure mode.

## Acceptance Criteria

- [ ] Oracle Granularity replay enumerates levels and attributes `granularity_error` when best granularity ≠ original.
- [ ] Graph-Off replay disables graph expansion and attributes `graph_error` when recovery gain > 0.
- [ ] Safety-Off replay bypasses safety filter and attributes `safety_error` when recovery gain > 0.
- [ ] All 11 labels validate through `validate_v1_label()`.
- [ ] Deferred label registry: `granularity_error`, `graph_error`, `safety_error` moved to active.
- [ ] No regression on V0 6-label + 0011 2-label smoke cases.
- [ ] Behavior-level tests: one smoke case per new label with correct attribution.
