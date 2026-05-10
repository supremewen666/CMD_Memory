---
title: Establish baseline memory systems and judge monitor
labels:
  - needs-triage
type: AFK
blocked_by:
  - 0001-define-probe-dataset-and-gold-evidence
user_stories:
  - 3
  - 4
  - 5
  - 6
  - 7
  - 21
---

# Establish baseline memory systems and judge monitor

## Parent

`prd/cmd_minimal_probe_prd.md`

## What to Build

Define the first baseline and monitor set for the CMD probe: fixed-summary memory, vector-memory retrieval, evidence recall heuristic, LLM-as-judge or subagent judge explanation, subagent judge monitor trigger, and random labels. The slice is complete when every probe case can produce a failed baseline answer, a non-CMD comparator label or explanation, and a cheap monitor decision about whether expensive replay should run.

## Implementation Detail

See `0002-baselines-and-judge-monitor-implementation-details.md` for the code map, public entry points, monitor boundary, and metric surface.

## Acceptance Criteria

- [x] Fixed-summary and vector-memory baseline behavior is specified for each probe case.
- [x] Evidence recall heuristic output is specified as a comparator, not as CMD attribution.
- [x] Subagent judge output is specified as post-hoc explanation over the trace.
- [x] Subagent judge monitor behavior is specified as high-recall replay triggering, not final attribution.
- [x] Subagent judge monitor is leak-safe and cannot emit final labels, ECS, memory writes, gold answers, or full failed traces.
- [x] Random label baseline is specified for attribution sanity checks.
- [x] The comparison metrics include attribution accuracy, macro F1, top-2 accuracy, and cost per diagnosis.

## Blocked By

- `0001-define-probe-dataset-and-gold-evidence`
