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
