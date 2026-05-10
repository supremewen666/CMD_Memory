# 2026-05-09 Issue 0003 Attribution Table Skeleton

## Trigger

User requested another skill-ordered pass over the cleaned core non-code content using grill-with-docs, to-prd, to-issues, prototype, and tdd, while preserving core paths and updating `CLAUDE.md` and `TASK.md`.

## Result

Added `cmd_innovation_core/issues/0003-counterfactual-attribution-table-implementation-details.md`.

The new skeleton defines issue 0003 as the transition from a one-replay Oracle Retrieval smoke path to the bounded V0 Replay Portfolio:

```text
ProbeCase
-> baseline suite and leak-safe monitor
-> V0 Replay Portfolio
-> replay scores and Recovery Gains
-> Operation-Level Attribution
-> Attribution Table
```

## Boundary

- First red-green behavior remains Verbatim Event Oracle and `premature_extraction_error`.
- Gold evidence for raw-event-only recovery should use `source_event_id` and omit `source_memory_id`.
- ECS, Post-Repair Context Replay, targeted repair, Failure Memory, and CMD-Skill Adapter remain later issues.

## Implementation Update

Issue 0003 is now implemented for the bounded V0 Replay Portfolio.

Added:

- `cmd_audit.replays.run_oracle_write`
- `cmd_audit.replays.run_oracle_compression`
- `cmd_audit.replays.run_verbatim_event_oracle`
- `cmd_audit.replays.run_oracle_retrieval` diagnostic gating for missed recoverable memory
- `cmd_audit.replays.run_injection_oracle`
- `cmd_audit.replays.run_evidence_given_reasoning`
- `cmd_audit.replays.run_v0_replay_portfolio`
- `AuditResult.replays`
- per-replay attribution table columns for all six V0 replay paths
- `write_confusion_matrix_table`
- `source_event_id` validation in `ProbeCase.validate`
- `data/probe_cases/v0_premature_extraction_error_case.json`
- `data/probe_cases/v0_issue3_cases.json`
- `tests/test_cmd_audit_issue3_attribution_table.py`

Verified with `python3 -m pytest`: 16 tests pass.

The generated attribution table now contains one row for each V0 label:

- `v0-write-001` -> `write_error`
- `v0-compression-001` -> `compression_error`
- `v0-retrieval-001` -> `retrieval_error`
- `v0-premature-extraction-001` -> `premature_extraction_error`
- `v0-injection-001` -> `injection_error`
- `v0-reasoning-001` -> `reasoning_error`

The CLI also writes `artifacts/attribution_confusion_matrix.csv`.
