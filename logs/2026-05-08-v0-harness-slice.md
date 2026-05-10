# 2026-05-08 V0 Harness Slice

Implemented the first CMD-Audit tracer bullet as code:

- Added a standalone `cmd_audit` package.
- Defined the V0 probe case contract with raw events, extracted memory, gold evidence, gold answer, baseline outputs, perturbation label, and scoring fields.
- Added one synthetic `retrieval_error` probe where extracted memory contains the gold evidence, baseline retrieval misses it, and Oracle Retrieval recovers the answer.
- Added V0 label validation that rejects bad memory item labels and deferred pipeline labels.
- Added a minimal attribution table writer for the first row.

Scope deliberately stops before the full replay engine. The only active replay path is Oracle Retrieval, matching Cycle 1 in `tdd/cmd_tracer_bullets.md`.
