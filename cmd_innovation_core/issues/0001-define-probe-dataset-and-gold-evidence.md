---
title: Define the probe dataset and gold evidence contract
labels:
  - needs-triage
type: AFK
blocked_by: none
user_stories:
  - 1
  - 2
  - 22
  - 24
---

# Define the probe dataset and gold evidence contract

## Parent

`prd/cmd_minimal_probe_prd.md`

## What to Build

Define the first CMD probe as a set of labeled memory failure cases with query, history or raw events, extracted memory, gold answer, gold evidence, injected perturbation label, and expected scoring fields. The result should make it possible to run one failed case end-to-end through baseline output, replay, attribution, and later repair.

## Implementation Detail

See `0001-probe-contract-implementation-details.md` for the zoomed-out module map, function-by-function contract, label scenario examples, code linkage, and current test/CLI execution path.

## Acceptance Criteria

- [ ] The case contract distinguishes raw events, extracted memory, gold evidence, base output, and injected failure label.
- [ ] The contract includes labels for `write_error`, `compression_error`, `retrieval_error`, `premature_extraction_error`, `injection_error`, and `reasoning_error` at minimum.
- [ ] V0 attribution excludes bad memory item labels: `item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, and `item_compression_distorted`.
- [ ] The contract explicitly excludes `granularity_error`, `route_error`, `graph_error`, and `safety_error` from V0 cases.
- [ ] At least one example scenario is written for each minimum label.
- [ ] The contract states how answer score and evidence score will be measured.
- [ ] The contract is small enough to support 50-100 synthetic cases before any large dataset integration.

## Blocked By

None - can start immediately.
