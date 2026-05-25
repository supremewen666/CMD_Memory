# Local Issue Skeleton

This folder acts as a local markdown issue tracker for the initial CMD non-code skeleton. The repo currently has no configured remote, so these files stand in for issue tracker entries and carry `needs-triage` in frontmatter.

## Proposed Vertical Slices

V0 is scoped to six pipeline labels: `write_error`, `compression_error`, `premature_extraction_error`, `retrieval_error`, `injection_error`, and `reasoning_error`. Bad memory item labels plus `granularity_error`, `route_error`, `graph_error`, and `safety_error` are deferred to V1/V2.

1. Define the probe dataset and gold evidence contract - ✅ done - green smoke foundation exists - covers PRD stories 1, 2, 22, 24.
2. Establish baseline memory systems and judge monitor - ✅ done - green smoke foundation exists - covers PRD stories 3-7, 21.
3. Generate the first counterfactual attribution table - ✅ done - green six-replay table exists - covers PRD stories 8-15. Detail map: `0003-counterfactual-attribution-table-implementation-details.md`.
4. Review attribution taxonomy boundaries - ✅ done - covers PRD stories 11, 14, 21.
5. Validate Post-Repair Context Replay - ✅ done - covers PRD story 17.
6. Validate targeted memory fixes - ✅ done - covers PRD stories 16, 20.
7. Validate ECS Failure Memory recurrence reduction - ✅ done - covers PRD stories 18-20.
8. Strengthen retrieval baselines and evidence scoring - ✅ done - V0.5 complete - covers PRD stories 3, 4, 7, 10, 15.
9. Harden Subagent Judge Monitor contract - ✅ done - covers PRD stories 6, 27, 28.
10. Enforce evidence-driven version gates - ✅ done - V0→V1 gate HITL approved (supremewen, 2026-05-10) - covers PRD stories 20, 32.

## Review Questions

- Is V0 small enough now that the first attribution table is realistic?
- Are the dependency relationships correct?
- Should V1 add `granularity_error` and `route_error` before graph/safety labels?
- Is the standalone harness plus adapter boundary enough for future integration?
- Do the three boundary ACs remain constraints rather than new feature branches?
- Does V0.5 retrieval strengthening belong immediately after issue 0003, or after Post-Repair Context Replay proves repair validity?

## V1 Vertical Slices (2026-05-19 update)

V1 extends V0 with 5 pipeline labels (11 total) and integrates CMD-Skill Adapter with real memory agents. Issues 0011-0012 are complete. Issues 0013-0018 are planned. Probe suite scaling bottleneck is resolved: 596 cleaned cases + 596 real probe cases available.

### Label Expansion

11. Implement `ingestion_error` and `route_error` labels — ✅ done.
12. Implement `granularity_error`, `graph_error`, and `safety_error` labels — ✅ done (345 tests pass, 81 issue-specific tests).
13. Recalibrate coupled-failure for 11 labels and add memory-probe baseline — ✅ done (387 tests pass, 42 issue-specific tests). Detail map: `0013-recalibrate-coupled-failure-and-memory-probe-baseline-implementation-details.md`.

### Adapter Integration

14. Integrate mem0 as first CMD-Skill Adapter target — ✅ done (417 tests pass, 30 issue-specific tests). First CMD-Skill Adapter target complete; adapter-label parity confirmed. Detail map: `0014-integrate-mem0-adapter-implementation-details.md`.
15. Integrate Letta as second adapter target and enforce V1→V2 gate — ✅ done (453 tests pass, 44 issue-specific tests). Second CMD-Skill Adapter target complete; adapter-label parity confirmed; cross-agent non-regression verified; V1→V2 gate passes. Detail map: `0015-letta-adapter-implementation-details.md`.

### Infrastructure & Optimization

16. RPE prefilter + PrefixGuard two-tier architecture (Decision 27) — superseded by 0021 RPE Judge per-replay scoring. Original RPE binary gate + PrefixGuard parallel OR replaced by empty_ctx hard short-circuit + 16-feature logistic regression model.
17. Provenance tracking — ✅ done. Execution Lineage DAG + trace-mem citation (Decision 28). 78 tests, detail map: `0017-provenance-tracking-execution-lineage-dag-implementation-details.md`.
18. Real data integration — ✅ complete 05-22. 601 cases, unified loader, CLI `run-v1 --real-data`.
19. LLM-as-Judge + Subagent scoring — ✅ done. Adds provider-agnostic `LLMClient`, observational `llm_judge` comparator, EvidenceVerifier/AnswerVerifier/SubagentScorer, and Decision 34 real-agent replay wiring. Detail map: `0019-implementation-detail.md`.

### Hook Redesign (Decision 33)

21. (0021) Hook redesign: two-stage sequential gating (empty_ctx hard short-circuit + RPE Judge per-replay top-k=3 ranking, 16-feature LR, subagent-labeled training, global threshold calibration) — ✅ done (PR1 + PR2). Replaced five-branch parallel OR with two-stage sequential architecture; packaged as `hook/` sub-package. PR2 deleted legacy modules (`prefix_guard.py`, `rpe_prefilter.py`, `replay_ordering.py`, root `post_retrieve_hook.py`, `hook_constants.py`), removed `run_case_v1_with_prefilter`, renamed CLI semantics to hook, and removed `AuditResult.fallback_triggered`. Grilling session 2026-05-23 finalized 9 build-detail decisions (see issue file).

### Paper Claim Integrity (Decision 34, 2026-05-23/24)

Decision 34 is the paper-claim integrity repair pass before the 2026-06-10 V1.0 arxiv preprint and post-corpus V1.1 venue submission:

- 596-case Macro F1 = 1.000 is preserved as a phrase-match mechanics snapshot, not a paper headline.
- Issue 0022 LLM eval wiring is ✅ done: replay warning/scorer-only path, label-stripped agent context, Post-Repair `agent_generate` + `AnswerVerifier`, on-the-fly baseline rescore, bootstrap helper, and V1 `tie_margin=0.0` defaults.
- Issue 0032 test migration is ✅ done: project warning filter, explicit warning assertion fixture, leak-free replay-context invariant, and Mem0/Letta parity under the stubbed LLM stack.
- Issue 0031 archive step is ✅ archive-complete: pre-D34 artifacts moved under `artifacts/legacy_phrase_match_2026_05_22/` with manifest; post-0023 LLM-stack artifact regeneration remains pending.
- Issue 0033 deepseek provenance is recovered but API-run-pending: reconstructed script/prompt, metadata references, and honest reproducibility report are checked in; full 596-case DeepSeek rerun needs credentials.
- Headline Experiment 2 moves to 130 researcher-adjudicated cases with LLM-A (`llama-3.3-70b-instruct`) suggestions + 20-case blind spot-check; 596 becomes scale sanity check against deepseek-v4-pro-max labels.
- Hook remains implemented but becomes supplementary; headline attribution bypasses hook and runs all 10 replays.
- CMD vs Rewind head-to-head is dropped; related work uses layered runtime / memory-pipeline / item-content positioning.
- Experiment 1 hardens to 80 cases × 5 modes with `corrected_only_padded` token-control.
- Q11 standalone Failure Memory recurrence collapses into Experiment 1; repair depth is a design claim via RepairAction emission; AttributionFailed is reported as principled abstention.
- Artifacts regenerate under the LLM stack with pre-D34 artifacts archived to `legacy_phrase_match_2026_05_22/`.
- V1.0/V1.1 dual-release is tracked explicitly: V1.0 coverage claim on 596-derived data, V1.1 full-corpus rerun with stronger generalization claims.
- Bootstrap CIs, cost/latency columns, two-evaluator robustness, and surrogate-gap retention% are required paper-craft outputs.

Entry points: `REPAIR.md`, `PLAN.md`, `cmd_innovation_core/plans/cmd_open_decisions.md` Decision 34, `TASK.md` Next Steps, and `0034-decision-34-issue-index.md`.

Next code-bearing issue: `0023-at-scale-llm-retest.md`, which consumes the completed 0022 wiring and archived pre-D34 artifacts.

### V1 Dependency Graph (2026-05-22)

```text
0010 (V0 complete) ✅
  └→ 0011 (ingestion + route labels) ✅
       └→ 0012 (granularity + graph + safety labels) ✅
            ├→ 0013 (coupled-failure recalibration) ✅
            │    └→ 0014 (mem0 adapter) ✅
            │         └→ 0015 (letta adapter + V1→V2 gate) ✅
            │              ├→ 0016 (Real data integration) ✅
            │              ├→ 0017 (Provenance DAG + trace-mem citation) ✅
            │              ├→ 0018 (Pre-CMD Hook) ✅
            │              │    └→ 0021 (Hook redesign: 2-stage + RPE judge) ✅
            │              │         └→ 0020-C (run_case_v1_with_hook) ✅
            │              │              └→ 0020-F (PreCmdDecision → AuditResult) ✅
            │              └→ 0020-A/B/D/E/G/H (Decision 32 post-gate) ✅, parallel to 0021
``````

## Issue 0020: Post-Gate Pipeline — Decision 32 (2026-05-23, design phase)

Design finalized 2026-05-23. 7 design documents (B, A, G, D, C, F, H) + 0021 (Hook redesign). Issue E merged into 0021 Step 2. Implementation pending — 0 tests written.

### Subtask Design Docs

| Subtask | Content | Blocked by | Detail Map |
|---------|---------|------------|------------|
| 0020-B | RepairAction + adapter.apply_repair (5 action_types) | — | `0020-B-repair-action-adapter-apply-repair.md` |
| 0020-A | RepairExecutor + RepairOrchestrator | B | `0020-A-repair-executor-orchestrator.md` |
| 0020-G | ECS iterative repair (close_deltas + draft_ecs_for_label) | A | `0020-G-ecs-iterative-repair.md` |
| 0020-D | Failure Memory upgrade (composite key + fm_context) | — | `0020-D-failure-memory-upgrade.md` |
| 0020-C | run_case_v1_with_hook (online entry) | A, D | `0020-C-run-case-v1-with-hook.md` |
| 0020-F | PreCmdDecision signals → AuditResult | C | `0020-F-pre-cmd-decision-to-audit-result.md` |
| 0020-H | V2 cascade pre-burial (ECSDraft.cascade_candidates) | — | `0020-H-v2-cascade-pre-burial.md` |
| 0021 | Hook redesign (two-stage + RPE Judge) | — | `0021-hook-redesign-three-stage-rpe-judge.md` |

### Planned New Modules

- `cmd_audit/repair_executor.py` — RepairExecutor + RepairOrchestrator + compute_close_deltas
- `cmd_audit/hook/` sub-package — rpe_judge.py + post_retrieve_hook.py + constants.py

### Planned Modified Modules

- `cmd_audit/post_repair.py` — ECSDraft.cascade_candidates, draft_ecs_for_label, build_repaired_context, RepairedContext 扩展
- `cmd_audit/models.py` — RepairAction, AuditResult 扩展
- `cmd_audit/failure_memory.py` — FailureMemoryRecord 扩展, query 升级, _extract_memory_top_terms
- `cmd_audit/adapters/base.py` — supported_actions, apply_repair
- `cmd_audit/adapters/mem0_adapter.py` — apply_repair (append, replace)
- `cmd_audit/adapters/letta_adapter.py` — apply_repair (append, replace, relocate, update_routing)
- `cmd_audit/harness.py` — run_case_v1_with_hook
- `cmd_audit/writers.py` — write_hook_analysis_table
- `cmd_audit/repairs.py` — REPAIR_ACTION_BY_LABEL deprecated
