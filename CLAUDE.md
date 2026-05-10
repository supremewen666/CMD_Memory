# CLAUDE.md

This repository is a research-planning workspace for **CMD: Counterfactual Memory Debugger for LLM Agent Memory**. Treat `cmd_innovation_core/` as the source of truth.

## Project Summary

CMD frames agent-memory failure diagnosis as counterfactual attribution:

```text
failed task
  -> leak-safe monitor
  -> counterfactual replay over memory operations
  -> operation-level attribution by recovery gain
  -> Error-Cause-Solution
  -> Post-Repair Context Replay
  -> User Memory repair
  -> Failure Memory for future similar tasks
```

The first milestone is not a universal memory architecture. It is a standalone **CMD-Audit** harness that produces attribution and repair-validation evidence.

CMD occupies an unverified gap confirmed by a broad 2026-05-10 metabolism survey (27 papers, 10 repos): no existing paper or open-source project does automated counterfactual memory replay for operation-level attribution. The closest approaches are observational (Trajectory-Informed Memory), interactive (Peaky Peek), or binary-detection (D-MEM).

## Required Reading

Before changing plans or code, read:

1. `cmd_innovation_core/README.md`
2. `cmd_innovation_core/CONTEXT.md`
3. `knowledge/current-memory.md`
4. `cmd_innovation_core/prd/cmd_minimal_probe_prd.md`
5. `cmd_innovation_core/issues/README.md`
6. `cmd_innovation_core/gates/V0V1_gate_status.md`
7. `TASK.md`

## Current Execution State

As of 2026-05-10, the V0 CMD-Audit evidence chain is structurally complete through issue 0010:

- Issues 0001-0010 are complete. 218 tests pass.
- Issue 0001: retrieval-error tracer bullet, Oracle Retrieval replay, Recovery Gain, attribution table row.
- Issue 0002: fixed-summary/vector baselines, evidence-recall comparator, subagent judge comparator, random baseline, leak-safe Subagent Judge Monitor.
- Issue 0003: six-replay V0 counterfactual attribution table. Implementation detail map at `cmd_innovation_core/issues/0003-counterfactual-attribution-table-implementation-details.md`.
- Issue 0004 (taxonomy boundary review): done. V0 six-label taxonomy confirmed with no changes needed. All 11 acceptance criteria met.
- Issue 0005 (Post-Repair Context Replay): done. Three-value `repair_assessment` (`recovered`/`partial`/`failed`), ECS draft pipeline, sandbox write boundary, hard-case update baseline. Detail map at `cmd_innovation_core/issues/0005-post-repair-context-replay-implementation-details.md`.
- Issue 0006 (Targeted Memory Fixes): done. Six per-label repair actions (`cmd_audit/repairs.py`), repair comparison table, claim ledger, 26 new behavior-level tests. Detail map at `cmd_innovation_core/issues/0006-validate-targeted-memory-fixes-implementation-details.md`.
- Issue 0007 (ECS Failure Memory recurrence): done. FailureMemoryStore, three-mode context comparison (none/full_trace/corrected_guidance), keyword-based retrieval, pollution risk metric, recurrence comparison table, 44 new behavior-level tests. Detail map at `cmd_innovation_core/issues/0007-ecs-failure-memory-recurrence-implementation-details.md`.
- Issue 0008 (retrieval baseline strengthening): done. BM25 + HybridRerank deterministic retrievers over `case.extracted_memory`, 6 hard negative probe cases (`v0_issue8_hard_negatives.json`), ranked retrieval traces, retrieval metrics (Recall@k, MRR, nDCG@10, Precision@k, context noise ratio, answer accuracy/F1), evidence boundary enforcement (`enforce_retrieval_error_boundary`, `compute_evidence_boundary_audit`), 43 new behavior-level tests. Detail map at `cmd_innovation_core/issues/0008-retrieval-baseline-implementation-details.md`.
- Issue 0009 (Monitor contract hardening): done. `anomaly_reason` locked to enum, evidence pointers restricted to opaque IDs, 15 new behavior-level tests.
- Issue 0010 (Evidence-driven version gates): done. V0→V1 four-criteria gate check (`cmd_audit/version_gates.py`), V1→V2 stub, HITL review pipeline, gate tracking document (`cmd_innovation_core/gates/V0V1_gate_status.md`), 48 new behavior-level tests.
- Existing smoke artifacts: `artifacts/attribution_table.csv`, `artifacts/comparison_metrics.csv`, `artifacts/attribution_confusion_matrix.csv`, `artifacts/sandbox/post_repair_table.csv`, `artifacts/sandbox/repair_success_table.csv`, `artifacts/sandbox/repair_label_summary.csv`, `artifacts/sandbox/repair_claim_ledger.txt`, `artifacts/sandbox/recurrence_comparison.csv`, `artifacts/sandbox/recurrence_summary.txt`, `artifacts/sandbox/V0V1_gate_status.txt`, `artifacts/sandbox/V0V1_gate_review.txt`, `artifacts/sandbox/retrieval_metrics.csv`, `artifacts/sandbox/retrieval_trace.csv`, `data/probe_cases/v0_issue8_hard_negatives.json`.
- All four V0 gate criteria pass on the 6-case smoke suite. HITL V0→V1 gate review is deferred pending probe suite scaling (PRD targets 50-100 cases).
- The next work is **probe suite scaling** (expand from 6 smoke cases to 50-100 labeled cases), a V0→V1 gate prerequisite.

### 2026-05-10 Architecture Polish (V0)

After issues 0001-0010 completed, the codebase received a consolidation pass:

- `cmd_audit/writers.py` extracts shared CSV and text-artifact writers (`write_csv_table`, `write_text_artifact`) that enforce the sandbox write boundary in one place. Previously, 8+ call sites across 4 modules duplicated the pattern `path.parent.mkdir + csv.DictWriter + writeheader + row loop`.
- `harness.py` is now pure orchestration (pipeline + `AuditResult`/`FullAuditResult`). The CSV artifact writers moved to `writers.py`. `write_comparison_metrics_table` and `write_repair_success_table_from_full` remain in `harness.py` because they import `diagnosis_predictions` and `make_repair_comparison` respectively, avoiding a circular dependency between `writers.py` and `harness.py`.
- `TargetedRepairAction` in `repairs.py` now carries `cause` and `repair_guidance` fields, making it the single source of truth for label-to-text mappings. `_ecs_for_label` in `post_repair.py` delegates to `get_targeted_repair_action` via lazy import, eliminating the parallel if/elif chain.
- `retrieval_baselines.py` extracts `_compute_bm25_scores` as a shared function used by both `run_bm25_retrieval` and `run_hybrid_rerank_retrieval`, removing the ~30-line inline BM25 duplication in the hybrid retriever.

### 2026-05-10 Metabolism Day 0 Update

A broad survey across arxiv + openalex + GitHub (27 papers, 10 repos) produced:

- 18 new reference notes in `reference_notes/` (see `knowledge/_index.md` for full list).
- hyp-011: RPE as cheap anomaly pre-filter for Subagent Judge Monitor (D-MEM analogue).
- Prototype brief: `cmd_innovation_core/prototypes/rpe_monitor_prefilter_prototype.md`.
- Competitive positioning confirmed across PRD, README, CONTEXT, and open_decisions.
- New domain terms in CONTEXT.md: RPE Gating, Risk-Sensitive Retrieval, Abstention Action, Decision Attribution, Agent-Native Memory, Memory Governance, Competitive Baseline.

Do not restart issue 0001-0005 or 0009 unless the contract changes. Extend them only when a later tracer bullet exposes a missing boundary.

## Domain Rules

- Use the domain language in `cmd_innovation_core/CONTEXT.md`.
- Keep **CMD-Audit** separate from **CMD-Skill Adapter**.
- V0 builds a standalone CMD-Audit harness. A skill adapter is a later deployment boundary.
- **Subagent Judge Baseline** is a comparator over the failed trace.
- **Subagent Judge Monitor** is leak-safe: it may trigger replay but must not emit final labels, ECS, memory writes, gold answers, or full failed traces. `anomaly_reason` is locked to a predefined enum (`answer_vs_evidence_mismatch`, `retrieved_context_incomplete`, `evidence_recall_low`, `confidence_anomaly`); free-form natural language is prohibited. Evidence pointers are opaque IDs only, never content text.
- **Post-Repair Context Replay** is a required V0 gate, not a separate product feature. It outputs three-value `repair_assessment` (`recovered` / `partial` / `failed`), not a binary gate. `partial` (evidence recovered, answer still wrong) exposes coupled failures as diagnostic signal.
- **CMD-Audit** write permissions are limited to replay-local sandbox. Only **CMD-Skill Adapter** applies validated repairs to production agent state.
- V0.5 stronger retrieval may flip `retrieval_error` only when `evidence_recall_from_text(gold_evidence, memory_item.text)` confirms the Memory Item text contains the evidence. When the Memory Item text lacks the evidence phrases, the label stays `premature_extraction_error`.
- V0.5 uses two retrieval baselines (BM25 as weak, HybridRerank as strong) as comparator systems, not as CMD counterfactual interventions. Both are deterministic, pure Python, blind to gold evidence during ranking. Agentic search is deferred to V1.
- `evidence_recall_from_text` phrase matching is a necessary but not sufficient condition for semantic correctness (Decision 12). This is a known V0 limitation mitigated by careful `required_phrases` construction. Semantic scoring upgrade belongs to V1 alongside real LLM-agent integration.
- ECS `cause` may describe item state in natural language but must not use V0-forbidden item label names or re-declare them through natural language equivalents.
- Version gates V0→V1→V2 are evidence-driven: V0→V1 requires the four V0 evidence artifacts passing paper-claim thresholds; V1→V2 requires at least two distinct memory agents integrated through the Adapter Interface without macro F1 regression.

## V0 Attribution Scope

V0 outputs only these six pipeline labels:

- `write_error`
- `compression_error`
- `premature_extraction_error`
- `retrieval_error`
- `injection_error`
- `reasoning_error`

Do not output bad memory item labels in V0 attribution:

- `item_wrong`
- `item_stale`
- `item_conflict`
- `item_poisoned`
- `item_compression_distorted`

Do not add these deferred labels to V0 unless the plan is explicitly revised:

- `granularity_error`
- `route_error`
- `graph_error`
- `safety_error`
- `ingestion_error` (V1 candidate: evidence never reached the agent; V0 subsumes under `write_error`)

## Local Issue Tracker

Issues live as markdown files in `cmd_innovation_core/issues/`.

Work in dependency order, with current status:

1. probe dataset and gold evidence contract - green smoke foundation exists;
2. baselines and judge monitor - green smoke foundation exists;
3. counterfactual attribution table - green six-replay V0 table exists;
4. taxonomy boundary review - done;
5. Post-Repair Context Replay - done;
6. targeted memory fixes - done;
7. ECS Failure Memory recurrence - done;
8. V0.5 retrieval baseline strengthening - ✅ done;
9. Subagent Judge Monitor contract hardening - done;
10. evidence-driven version gates - done (HITL review deferred pending probe suite scaling).

## TDD Guidance

Use tracer bullets from `cmd_innovation_core/tdd/cmd_tracer_bullets.md`.

Testing should verify behavior through public interfaces, not implementation details. Continue one failing behavior at a time. Key tracer bullets already proven:

- **Issue 0001**: A recoverable extracted memory missed by baseline retrieval is attributed as `retrieval_error`.
- **Issue 0003**: The Verbatim Event Oracle boundary — when raw events contain required evidence but extracted memory cannot recover it, CMD-Audit assigns `premature_extraction_error`, not `retrieval_error`. Gold evidence for such cases should point to raw events and omit `source_memory_id`.
- **Issue 0005**: Post-Repair Context Replay outputs three-value `repair_assessment`; `partial` exposes coupled failures.
- **Issue 0009**: Subagent Judge Monitor rejects free-form `anomaly_reason` and content-bearing evidence pointers.

The V0 TDD cycle is structurally complete through issue 0010. The next TDD cycle will be probe suite scaling (V0→V1 gate prerequisite).

## Editing Rules

- Preserve existing research notes unless the user explicitly asks to rewrite them.
- When adding knowledge, update the relevant `knowledge/` page and add a short `logs/YYYY-MM-DD-*.md` note.
- Keep `topic-cmd-memory-failure.md` compact and information-dense.
- Keep V0 scope narrow; avoid expanding labels, datasets, UI, or production integrations during implementation.
- Do not inject gold answers into Post-Repair Context Replay.
- Do not store or reuse full failed traces as future Failure Memory context; use `corrected_memory + repair_guidance`.
- New reference notes follow the format: arXiv ID, core contribution, key concepts, CMD relevance, open gap (one line each). GitHub notes use the same format with `GitHub:` prefix instead of arXiv ID.
- When metabolism produces new signals, update `topic-cmd-memory-failure.md` with a dated signal table and `current-memory.md` with an incremental conclusions section before adding new hypotheses or prototypes.

## Output Artifacts To Aim For

The V0 run should eventually produce:

- `attribution_table.csv`
- `comparison_metrics.csv`
- attribution confusion matrix
- CMD vs heuristic vs subagent judge comparison
- Post-Repair Context Replay table ✅
- targeted repair-success table ✅
- ECS Failure Memory recurrence comparison ✅
- evidence-driven version gates ✅

Evidence comes before claims. If the tables do not support a claim, narrow the paper framing instead of overstating CMD.
