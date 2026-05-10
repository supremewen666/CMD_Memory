---
title: Generate the first counterfactual attribution table
labels:
  - needs-triage
type: AFK
blocked_by:
  - 0001-define-probe-dataset-and-gold-evidence
  - 0002-establish-baselines-and-judge-monitor
user_stories:
  - 8
  - 9
  - 10
  - 11
  - 12
  - 13
  - 14
  - 15
---

# Generate the first counterfactual attribution table

## Parent

`prd/cmd_minimal_probe_prd.md`

## What to Build

Run each failed probe case through the initial counterfactual replay set and produce an attribution table with replay scores, recovery gains, predicted attribution labels, ground-truth perturbation labels, top-2 labels, and diagnosis cost.

## Implementation Detail

See `0003-counterfactual-attribution-table-implementation-details.md` for the issue 0003 module map, current one-replay harness state, next red-green tracer bullet, replay portfolio shape, table columns, and boundary constraints.

## Tracer Order

1. Verbatim Event Oracle boundary: raw events recover required evidence, extracted memory does not, and attribution is `premature_extraction_error`.
2. Evidence-Given Reasoning boundary: correct evidence is available but the baseline answer remains wrong, and attribution is `reasoning_error`.
3. Remaining replay paths: Oracle Write, Oracle Compression, and Injection-Oracle.
4. Table broadening: one recovery-gain column per V0 replay and top-2 labels for close deltas.

## Acceptance Criteria

- [x] The replay set includes Oracle Write, Oracle Compression, Oracle Retrieval, Verbatim Event Oracle, Injection-Oracle, and Evidence-Given Reasoning.
- [x] The table includes one recovery-gain column per replay.
- [x] The table assigns top-1 and top-2 labels from replay deltas.
- [x] Cases where raw-event replay recovers evidence but extracted-memory replay does not are labeled `premature_extraction_error`, not `retrieval_error`.
- [x] The table can be compared against heuristic and subagent judge baselines.
- [x] V0 attribution outputs only the six pipeline labels and no bad memory item labels.
- [x] No V0 table column is required for granularity, route, graph, or safety interventions.

## Verification

Implemented in the standalone CMD-Audit harness.

- `python3 -m pytest` passes 16 tests.
- `python3 -m cmd_audit run` writes:
  - `artifacts/attribution_table.csv`
  - `artifacts/comparison_metrics.csv`
  - `artifacts/attribution_confusion_matrix.csv`

## Blocked By

- `0001-define-probe-dataset-and-gold-evidence`
- `0002-establish-baselines-and-judge-monitor`
