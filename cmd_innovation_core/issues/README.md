# Local Issue Skeleton

This folder acts as a local markdown issue tracker for the initial CMD non-code skeleton. The repo currently has no configured remote, so these files stand in for issue tracker entries and carry `needs-triage` in frontmatter.

## Proposed Vertical Slices

V0 is scoped to six pipeline labels: `write_error`, `compression_error`, `premature_extraction_error`, `retrieval_error`, `injection_error`, and `reasoning_error`. Bad memory item labels plus `granularity_error`, `route_error`, `graph_error`, and `safety_error` are deferred to V1/V2.

1. Define the probe dataset and gold evidence contract - ✅ done - green smoke foundation exists - covers PRD stories 1, 2, 22, 24.
2. Establish baseline memory systems and judge monitor - ✅ done - green smoke foundation exists - covers PRD stories 3-7, 21.
3. Generate the first counterfactual attribution table - ✅ done - green six-replay table exists - covers PRD stories 8-15. Detail map: `0003-counterfactual-attribution-table-implementation-details.md`.
4. Review attribution taxonomy boundaries - ✅ done - covers PRD stories 11, 14, 21.
5. Validate Post-Repair Context Replay - ✅ done - covers PRD story 17.
6. Validate targeted memory fixes - 🔴 active slice - blocked by issue 0005 (now resolved) - covers PRD stories 16, 20.
7. Validate ECS Failure Memory recurrence reduction - AFK - blocked by issue 0006 - covers PRD stories 18-20.
8. Strengthen retrieval baselines and evidence scoring - ✅ done - V0.5 complete - covers PRD stories 3, 4, 7, 10, 15.
9. Harden Subagent Judge Monitor contract - ✅ done - covers PRD stories 6, 27, 28.
10. Enforce evidence-driven version gates - HITL - blocked by issues 0004, 0005, 0007 - covers PRD stories 20, 32.

## Review Questions

- Is V0 small enough now that the first attribution table is realistic?
- Are the dependency relationships correct?
- Should V1 add `granularity_error` and `route_error` before graph/safety labels?
- Is the standalone harness plus adapter boundary enough for future integration?
- Do the three boundary ACs remain constraints rather than new feature branches?
- Does V0.5 retrieval strengthening belong immediately after issue 0003, or after Post-Repair Context Replay proves repair validity?

## V1 Vertical Slices (2026-05-11)

V1 extends V0 with 5 pipeline labels (11 total) and integrates CMD-Skill Adapter with real memory agents. Issues 0011-0017 are planned but not yet active—all are blocked directly or transitively by issue 0010 (V0 evidence chain complete) and probe suite scaling.

### Label Expansion

11. Implement `ingestion_error` and `route_error` labels — AFK, blocked by 0010 — covers V1 PRD stories 37-38.
12. Implement `granularity_error`, `graph_error`, and `safety_error` labels — AFK, blocked by 0011 — covers V1 PRD stories 39-41.
13. Recalibrate coupled-failure for 11 labels and add memory-probe baseline — AFK, blocked by 0012 — covers V1 PRD story 42.

### Adapter Integration

14. Integrate mem0 as first CMD-Skill Adapter target — AFK, blocked by 0013 — covers V1 PRD stories 34-36.
15. Integrate Letta as second adapter target and enforce V1→V2 gate — AFK, blocked by 0014 — covers V1 PRD story 36.

### Data & Optimization

16. Build LoCoMo/LongMemEval real-data probe cases and integrate memory-probe baseline — AFK, blocked by 0012 — covers V1 PRD stories 42-43.
17. Implement RPE pre-filter for Subagent Judge Monitor replay gating — AFK, blocked by 0015 — late-V1 optimization.

### V1 Dependency Graph

```text
0010 (V0 complete)
  └→ 0011 (ingestion + route labels)
       └→ 0012 (granularity + graph + safety labels)
            ├→ 0013 (coupled-failure recalibration + memory-probe baseline)
            │    └→ 0014 (mem0 adapter)
            │         └→ 0015 (letta adapter + V1→V2 gate)
            │              └→ 0017 (RPE pre-filter)
            └→ 0016 (real-data probe cases)
```
