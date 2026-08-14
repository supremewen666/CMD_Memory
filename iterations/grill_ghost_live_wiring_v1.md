# GHOST live wiring — grill record

Date: 2026-08-13
Decision: **STOP / no model calls authorized**

## Questions that changed the design

1. Can the already inspected 3,100 cases be repartitioned into a sealed test?
   No. They are restricted to `ghost_dev` and `ghost_cal`; both test partitions require an
   independently attested source.
2. Does filtering four logical partitions from one JSONL protect test access?
   No. A claim-bearing freeze therefore requires four physically separate, hash-bound JSONL
   sources. The access ledger is appended before the selected source is opened.
3. Can shadow `recovery_gain` update GHOST?
   No. Updates use only deployment-observable typed-executor/guard telemetry: success,
   locality, rollback, cost, and validity. Shadow utility is audit-only.
4. Can a weak feedback audit be made to pass by changing thresholds after seeing results?
   No. The registered zero-call audit failed and remains a hard gate.
5. Can the Full V4 baseline continue learning or reuse its old event clock directly?
   No. Its snapshot is immutable, and prospective GHOST event indexes start strictly after
   the snapshot's `effective_after_event_index`.
6. Can test evaluation write any posterior or export state?
   No. Test observations are evaluation-only and state export is rejected. Both test streams
   start independently from the frozen calibration state.

## Current hard evidence

- Identifiability decision: `BLOCKED_FEEDBACK_NOT_IDENTIFIABLE`
- Family-macro Pearson: `-0.061538217863358696`
- One-sided 95% bootstrap lower bound: `-0.12130777005096512`
- Within-case pairwise concordance: `0.3113906987193889`
- Model/API calls used by the audit: `0`
- Existing corpus freeze: `ghost_dev=2518`, `ghost_cal=582`, both tests `0`

## Unlock conditions

All must be true in one immutable protocol: independently sourced physical dev/cal/test files,
identifiability `PASS`, exact judge/answerer/crossjudge model tree hashes, Full V4 snapshot hash,
candidate budget, config hash, code tree hash, and a pre-read access-ledger entry for each
partition. Until then `ghost_prepare`, `ghost_gpu*`, and `ghost_merge` fail closed.
