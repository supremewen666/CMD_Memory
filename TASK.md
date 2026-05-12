# TASK: CMD V0 Minimal Probe

This repository currently contains the planning package for **Counterfactual Memory Debugger (CMD)**. The next executable goal is to turn the non-code skeleton into a V0 **CMD-Audit** harness that produces the first attribution table and repair-validation table.

## Read First

1. `cmd_innovation_core/README.md`
2. `knowledge/current-memory.md`
3. `cmd_innovation_core/CONTEXT.md`
4. `cmd_innovation_core/prd/cmd_minimal_probe_prd.md`
5. `cmd_innovation_core/issues/README.md`
6. `cmd_innovation_core/issues/0007-validate-ecs-failure-memory-recurrence.md`
7. `cmd_innovation_core/tdd/cmd_tracer_bullets.md`

## Current Objective

Advance the standalone CMD-Audit V0 harness from the green issue 0001-0005+0009 smoke foundation through issue 0006 (targeted memory fixes), then continue toward a full V0 evidence chain that can:

1. load 50-100 labeled synthetic memory-failure probe cases;
2. run fixed-summary and vector-memory baselines;
3. run V0 counterfactual replays (six-path portfolio);
4. compute replay deltas and operation-level attribution;
5. compare CMD against heuristic and subagent judge baselines;
6. generate Error-Cause-Solution records;
7. run Post-Repair Context Replay on the original failed query;
8. produce attribution and repair-success tables.

## Verified Current State

Verified on 2026-05-10 with `python3 -m pytest`: 218 tests pass.

- Issues 0001-0010 are complete.
- Issue 0001: retrieval-error tracer bullet, Oracle Retrieval replay, Recovery Gain, attribution table row.
- Issue 0002: fixed-summary/vector baselines, evidence-recall/subagent-judge/random comparators, leak-safe Subagent Judge Monitor.
- Issue 0003: six-replay V0 attribution table with one smoke case per V0 label. Detail map: `issues/0003-counterfactual-attribution-table-implementation-details.md`.
- Issue 0004: taxonomy boundary review complete; V0 six-label taxonomy confirmed.
- Issue 0005: Post-Repair Context Replay complete; three-value `repair_assessment`, ECS draft pipeline, sandbox write boundary, hard-case update baseline. Detail map: `issues/0005-post-repair-context-replay-implementation-details.md`.
- Issue 0006: targeted memory fixes complete; six per-label repair actions, repair comparison table, claim ledger, 26 behavior-level tests. Detail map: `issues/0006-validate-targeted-memory-fixes-implementation-details.md`.
- Issue 0007: ECS Failure Memory recurrence complete; FailureMemoryStore, three-mode context comparison, keyword-based retrieval, pollution risk metric, 44 behavior-level tests. Detail map: `issues/0007-ecs-failure-memory-recurrence-implementation-details.md`.
- Issue 0009: Subagent Judge Monitor contract hardening complete; enum-locked `anomaly_reason`, 15 behavior-level tests.
- Issue 0010: evidence-driven version gates complete; V0→V1 gate definition with four-criteria check, V1→V2 gate stub, HITL review pipeline, gate tracking document, 48 behavior-level tests.
- Generated artifacts: `artifacts/attribution_table.csv`, `artifacts/comparison_metrics.csv`, `artifacts/attribution_confusion_matrix.csv`, `artifacts/sandbox/post_repair_table.csv`, `artifacts/sandbox/repair_success_table.csv`, `artifacts/sandbox/repair_label_summary.csv`, `artifacts/sandbox/repair_claim_ledger.txt`, `artifacts/sandbox/recurrence_comparison.csv`, `artifacts/sandbox/recurrence_summary.txt`, `artifacts/sandbox/V0V1_gate_status.txt`, `artifacts/sandbox/V0V1_gate_review.txt`, `artifacts/sandbox/retrieval_metrics.csv`, `artifacts/sandbox/retrieval_trace.csv`, `data/probe_cases/v0_issue8_hard_negatives.json`.
- Gate tracking document: `cmd_innovation_core/gates/V0V1_gate_status.md`.
- Competitive positioning confirmed via 2026-05-10 metabolism: no existing work does automated counterfactual memory replay for operation-level attribution.
- The V0 CMD-Audit evidence chain is structurally complete. The next slice is issue 0008 (V0.5 retrieval baseline strengthening), a follow-up not on the V0 critical path.

## V0 Scope

V0 evaluates only six pipeline labels:

- `write_error`
- `compression_error`
- `premature_extraction_error`
- `retrieval_error`
- `injection_error`
- `reasoning_error`

Out of V0 attribution scope:

- bad memory item labels: `item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`
- deferred pipeline labels: `granularity_error`, `route_error`, `graph_error`, `safety_error`, `ingestion_error`

## Boundary Acceptance Conditions

- `cmd_innovation_core/CONTEXT.md` must keep **CMD-Audit** and **CMD-Skill Adapter** separate.
- **Subagent Judge Monitor** can trigger replay but cannot emit final labels, ECS, memory writes, gold answers, or full failed traces. `anomaly_reason` is locked to a predefined enum; free-form natural language is prohibited. Evidence pointers are opaque IDs only.
- **CMD-Audit** write permissions are limited to replay-local sandbox. Only **CMD-Skill Adapter** applies validated repairs to production agent state.
- V0 attribution must output only the six pipeline labels listed above.
- Post-Repair Context Replay must rerun the original failed query with repaired context, without injecting the gold answer, and output three-value `repair_assessment` (`recovered` / `partial` / `failed`).
- ECS `cause` may describe item state but must not use V0-forbidden item label names or re-declare them through natural language.
- V0.5 stronger retrieval may flip `retrieval_error` only when `evidence_recall_from_text` confirms the Memory Item text contains the evidence. When the Memory Item text lacks the evidence phrases, the label stays `premature_extraction_error`.
- Version gates V0→V1→V2 are evidence-driven: V0→V1 requires the four V0 evidence artifacts passing paper-claim thresholds; V1→V2 requires at least two distinct memory agents integrated without macro F1 regression.

## Issue Order

Use the local markdown issue tracker under `cmd_innovation_core/issues/`:

1. `0001-define-probe-dataset-and-gold-evidence.md` - ✅ done.
2. `0002-establish-baselines-and-judge-monitor.md` - ✅ done.
3. `0003-generate-counterfactual-attribution-table.md` - ✅ done. Detail map: `0003-counterfactual-attribution-table-implementation-details.md`.
4. `0004-review-attribution-taxonomy-boundaries.md` - ✅ done.
5. `0005-validate-post-repair-context-replay.md` - ✅ done.
6. `0006-validate-targeted-memory-fixes.md` - ✅ done. Detail map: `0006-validate-targeted-memory-fixes-implementation-details.md`.
7. `0007-validate-ecs-failure-memory-recurrence.md` - ✅ done. Detail map: `0007-ecs-failure-memory-recurrence-implementation-details.md`.
8. `0008-strengthen-retrieval-baselines-and-evidence-scoring.md` - ✅ done. Detail map: `0008-retrieval-baseline-implementation-details.md`.
9. `0009-harden-subagent-judge-monitor-contract.md` - ✅ done.
10. `0010-enforce-evidence-driven-version-gates.md` - ✅ done. Gate tracking: `../gates/V0V1_gate_status.md`.

## Completed Issues 0001-0010

The V0 CMD-Audit evidence chain is structurally complete. Summary:

- **0001**: Probe contract + Oracle Retrieval tracer bullet.
- **0002**: Baselines (fixed-summary, vector), comparators (evidence-recall, subagent judge, random), leak-safe monitor.
- **0003**: Six-replay V0 attribution table (Oracle Write, Oracle Compression, Verbatim Event Oracle, Oracle Retrieval, Injection-Oracle, Evidence-Given Reasoning), confusion matrix, comparison metrics. Detail map: `issues/0003-counterfactual-attribution-table-implementation-details.md`.
- **0004**: Taxonomy boundary review — V0 six-label taxonomy confirmed, no changes needed.
- **0005**: Post-Repair Context Replay — three-value `repair_assessment`, ECS draft pipeline, sandbox write boundary, hard-case update baseline. Detail map: `issues/0005-post-repair-context-replay-implementation-details.md`.
- **0006**: Targeted memory fixes — six per-label repair actions, repair comparison table, claim ledger, 26 behavior-level tests. Detail map: `issues/0006-validate-targeted-memory-fixes-implementation-details.md`.
- **0007**: ECS Failure Memory recurrence — FailureMemoryStore, three-mode comparison (none/full_trace/corrected_guidance), keyword-based retrieval, pollution risk metric, 44 behavior-level tests. Detail map: `issues/0007-ecs-failure-memory-recurrence-implementation-details.md`.
- **0008**: V0.5 retrieval baseline strengthening — BM25 + HybridRerank deterministic retrievers, 6 hard negative probe cases, ranked retrieval traces, retrieval metrics (Recall@k, MRR, nDCG, Precision@k), evidence boundary enforcement, 43 behavior-level tests. Detail map: `issues/0008-retrieval-baseline-implementation-details.md`.
- **0009**: Subagent Judge Monitor contract hardening — enum-locked `anomaly_reason`, opaque evidence pointers, 15 behavior-level tests.
- **0010**: Evidence-driven version gates — V0→V1 four-criteria gate check, V1→V2 stub, HITL review pipeline, gate tracking document, 48 behavior-level tests.

No `granularity_error`, `route_error`, `graph_error`, `safety_error`, or bad-memory-item labels in V0 outputs.

## Next Steps

The V0 CMD-Audit evidence chain is structurally complete. All four V0 gate criteria pass on the 6-case smoke suite. The HITL V0→V1 gate review is deferred pending probe suite scaling (PRD targets 50-100 cases; current smoke suite has 1 case per label).

Architecture polish completed 2026-05-10:
- Shared CSV/text writers in `cmd_audit/writers.py` consolidate 8+ duplicated write patterns across 4 modules.
- `harness.py` split into pure orchestration; artifact writers moved to `writers.py`.
- `TargetedRepairAction` now carries `cause` and `repair_guidance`; `_ecs_for_label` delegates to it.
- BM25 scoring de-duplicated in `retrieval_baselines.py` via shared `_compute_bm25_scores`.

Remaining work:
- Probe suite scaling: expand from 6 smoke cases to 50-100 labeled cases (PRD target, V0→V1 gate prerequisite).
- HITL gate review: human sign-off on V0→V1 gate once probe suite is scaled.

## V1 Roadmap (2026-05-11)

V1 planning is complete. Issues 0011-0017 are planned but not yet active—all blocked by probe suite scaling and V0→V1 gate. See `cmd_innovation_core/issues/README.md` for V1 dependency graph.

Key V1 decisions (recorded in `cmd_innovation_core/plans/cmd_open_decisions.md` Decisions 13-16):
- V0+V1+V2 = single paper. V2 is the final module/skill.
- V1 label expansion: `ingestion_error` → `route_error` → `granularity_error` → `graph_error` → `safety_error`.
- First adapter target: mem0 (55k stars). Second: Letta (22.6k stars).
- RPE pre-filter deferred to late V1 (issue 0017).

Updated V1 planning files: `cmd_tracer_bullets.md` (V1 cycles 16-22), `cmd_minimal_probe_prd.md` (V1 Scope section), `issues/0011-*.md` through `issues/0017-*.md`, `prototypes/mem0_adapter_interface_prototype.md` (+ `.zh.md`), existing prototypes now have `.zh.md` Chinese versions.

## Non-Code Skeleton Sync

When planning files need updates, preserve the existing core paths and update in this order:

1. PRD source: `cmd_innovation_core/prd/cmd_minimal_probe_prd.md`.
2. Issue tracker: `cmd_innovation_core/issues/`.
3. Prototype briefs: `cmd_innovation_core/prototypes/cmd_probe_logic_prototype.md`, `cmd_innovation_core/prototypes/post_repair_and_monitor_contract_prototype.md`, and `cmd_innovation_core/prototypes/rpe_monitor_prefilter_prototype.md`.
4. TDD sequence: `cmd_innovation_core/tdd/cmd_tracer_bullets.md`.
5. Knowledge files: `knowledge/current-memory.md`, `knowledge/topic-cmd-memory-failure.md`, `knowledge/_index.md`.
6. Plans: `plans/cmd_open_decisions.md`, `plans/direction_01_counterfactual_memory_debugger.md`.
7. Root execution guidance: `CLAUDE.md` and `TASK.md`.

## Competitive Positioning (2026-05-10 Metabolism)

CMD occupies a verified gap. A survey of 27 papers and 10 GitHub repos confirms:

| Approach | Evidence | Granularity | Automated |
|----------|----------|-------------|-----------|
| Subagent Judge | observational | free-form | yes |
| Trajectory-Informed (2603.10600) | observational | decision-level | yes |
| Peaky Peek (agent_debugger) | interactive | visual | no (HITL) |
| D-MEM (2603.14597) | RPE signal | binary flag | yes (no attr) |
| **CMD** | **counterfactual** | **operation-level** | **yes** |

Key signals from metabolism are recorded in `knowledge/topic-cmd-memory-failure.md` (2026-05-10 signal table), `plans/cmd_open_decisions.md` (Decisions 10-11), and `hypotheses/hyp-011.md` (RPE pre-filter).

## Evidence Gates

Do not make paper claims until the corresponding artifact exists:

- attribution claim: `attribution_table.csv` and confusion matrix;
- comparator claim: `comparison_metrics.csv` plus CMD vs heuristic vs subagent judge metrics;
- repair claim: Post-Repair Context Replay table;
- recurrence claim: Failure Memory recurrence comparison ✅;
- version gate: V0→V1 gate check operational, HITL review deferred pending probe suite scaling ✅;

## Non-Goals

- Do not build a production memory agent in V0.
- Do not add UI or dashboard work.
- Do not train a learned attribution classifier before rule-based replay deltas are validated.
- Do not broaden V0 to full taxonomy unless the local issue plan is updated first.
