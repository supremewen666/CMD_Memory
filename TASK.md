# TASK: CMD — Counterfactual Memory Debugger

## Read First

(CLAUDE.md covers remaining required reading.)

## Current State (2026-05-24)

**803 tests pass.** V0 complete and locked. V1 issues 0011-0021 complete. V0→V1 gate HITL approved and V1→V2 gate passes (mem0 + Letta) as mechanics validation. Decision 34 (2026-05-23/24, R1-R11) reframes the 596-case Macro F1 = 1.000 as a phrase-match shortcut artifact, not paper-grade evidence. Paper headline now binds to 130 researcher-adjudicated cases with LLM-A + blind spot-check; hook is supplementary; CMD vs Rewind head-to-head is dropped in favor of layered positioning. **Issue 0020 complete (Decision 32 post-gate pipeline): all 8 subtasks done, 91 new tests, 0 regressions.**

| Issue | Content | Tests | Status |
|-------|---------|-------|--------|
| 0001 | Probe dataset + Oracle Retrieval | — | ✅ |
| 0002 | Baselines + comparators + leak-safe monitor | — | ✅ |
| 0003 | 6-replay V0 attribution table | — | ✅ |
| 0004 | Taxonomy boundary review | — | ✅ |
| 0005 | Post-Repair Context Replay | — | ✅ |
| 0006 | Targeted memory fixes (6 per-label actions) | 26 | ✅ |
| 0007 | ECS Failure Memory recurrence | 44 | ✅ |
| 0008 | BM25 retrieval baseline strengthening | 35 | ✅ |
| 0009 | Subagent Judge Monitor contract hardening | 15 | ✅ |
| 0010 | Evidence-driven version gates | 48 | ✅ |
| 0011 | `ingestion_error` + `route_error` labels | 44 | ✅ |
| 0012 | `granularity_error` + `graph_error` + `safety_error` | 81 | ✅ |
| 0013 | Coupled-failure recalibration + memory-probe baseline | 42 | ✅ |
| 0014 | mem0 adapter (two-cut-point, recorded-trace) | 30 | ✅ |
| 0015 | Letta adapter (three-cut-point, tripartite) + V1→V2 gate | 44 | ✅ |
| 0016 | Real data integration (601 cases, unified loader, CLI) | 38 | ✅ |
| 0017 | Provenance tracking (Execution Lineage DAG) | 78 | ✅ |
| 0017-1 | RPE prefilter + PrefixGuard (refactored zero-gold) | — | refactored in 0018 |
| 0019 Phase A | LLM-as-Judge baseline (4th comparator) | 32 | ✅ |
| 0019 Phase B | SubagentScorer replacing phrase-matching | 39 | ✅ |
| 0018 | Pre-CMD Hook — zero-gold online gate | 88 | ✅ |
| 0021 | Hook redesign: 2-stage + RPE judge + hook/ package | 34 | ✅ |
| 0020-H | V2 cascade pre-burial (ECSDraft.cascade_candidates) | 7 | ✅ |
| 0020-B | RepairAction + adapter.apply_repair (5 action_types) | 19 | ✅ |
| 0020-D | Failure Memory upgrade (composite key + fm_context) | 18 | ✅ |
| 0020-A | RepairExecutor + RepairOrchestrator (iterative repair) | 15 | ✅ |
| 0020-G | ECS iterative repair (draft_ecs_for_label) | 9 | ✅ |
| 0020-F | PreCmdDecision signals → AuditResult | 6 | ✅ |
| 0020-C | run_case_v1_with_hook + RepairOrchestrator integration | 6 | ✅ |
| 0020-E | Self-supervision surrogate (surrogate vs gold gap) | 11 | ✅ |

Detail maps: `cmd_innovation_core/issues/0003-*.md`, `0005-*.md`, `0006-*.md`, `0007-*.md`, `0008-*.md`, `0011-*.md`, `0012-*.md`, `0013-*.md`, `0014-*.md`, `0015-*.md`, `0017-*.md`, `0017-1-*.md`, `0019-phase-a-*.md`, `0019-phase-b-*.md`, `0018-pre-cmd-hook-design.md`, `0021-hook-redesign-three-stage-rpe-judge.md`, `0020-h-*.md`, `0020-b-*.md`, `0020-d-*.md`, `0020-a-*.md`, `0020-g-*.md`, `0020-f-*.md`, `0020-c-*.md`, `0020-e-*.md`.

## Label Taxonomy

**V0 (locked, 6 labels):** `write_error`, `compression_error`, `premature_extraction_error`, `retrieval_error`, `injection_error`, `reasoning_error`.

**V1 (active, +5 labels):** `ingestion_error`, `route_error`, `granularity_error`, `graph_error`, `safety_error`. All 11 pipeline labels active.

**Still excluded:** Bad memory item labels (`item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`) — deferred to V2.

V1 pipeline (10 replays) = V0 6 replays + `oracle_route` + 3 V1 passthrough (`graph_off`, `safety_off`, `oracle_granularity`). V1 functions accept V0+V1 labels; V0 functions reject V1 labels.

## Boundary Acceptance Conditions

- **CMD-Audit** (research harness) and **CMD-Skill Adapter** (deployment layer) are separate. Audit writes to `artifacts/sandbox/` only. Adapter applies validated repairs to production state.
- **Subagent Judge Monitor**: enum-locked `anomaly_reason`, opaque evidence IDs. No labels, ECS, writes, gold answers, or full traces.
- **Post-Repair Context Replay**: rerun original query with repaired context, no gold injection, three-value `repair_assessment` (`recovered`/`partial`/`failed`).
- **ECS `cause`**: must not use forbidden item label names (`item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`) or their natural-language equivalents.
- **Verbatim Event Oracle boundary**: `evidence_recall_from_text(gold_evidence, memory_item.text)` is the hard gate. Evidence absent from text → `premature_extraction_error`, never `retrieval_error`.
- **V0/V1 stream separation**: V1 functions accept V0+V1 labels; V0 functions reject V1 labels. Never cross the streams.
- **Adapter sandbox**: SHA-256 checksum verification over store state before/after replay. Any mutation is `SandboxViolationError`.

## ⚠️ Accelerated Timeline — Target: 2026-06-10 V1.0 Arxiv Preprint, V1.1 Venue Submission Post-Corpus

Decision 30 (2026-05-20): counterfactual replay commoditizing. Timeline accelerated to **2026-06-10**. Decision 34 (2026-05-23/24): CMD vs Rewind head-to-head dropped. Paper headline binds to 130 researcher-adjudicated cases (LLM-A + spot-check assisted). V1.0/V1.1 dual-release: V1.0 ships as arxiv preprint on 06-10 with 596-derived numbers; V1.1 venue submission re-runs on full corpus post-issue-0035.

| Date | Milestone | Deliverable |
|------|-----------|-------------|
| ✅ 05-15~18 | V1 label expansion | Issues 0011-0012 |
| ✅ 05-19 | Coupled-failure + mem0 + Letta | Issues 0013-0015 |
| ✅ 05-20 | Decision 30 | Accelerate, Rewind 5-dim diff, repair depth metric |
| ✅ 05-21 | Issue 0019 Phase A + Issue 0018 design | llm_judge comparator, Pre-CMD Hook design |
| ✅ 05-22 | Real data + Gate at scale | Issues 0016-0018 (under phrase-match shortcut, see Decision 34 R1) |
| ✅ 05-23 | Decision 33 hook redesign + Decision 34 grilling start | issue 0021 implemented; REPAIR.md captures Q1-Q10 → R1-R7 |
| ✅ 05-24 | Decision 34 grilling close | Q11-Q23 → R8-R11; issues 0022-0034 written; REPAIR.md §15-§19 |
| 05-25~28 | LLM eval infra wiring (issue 0022, R1+R2+R5 + Gap 3/4) | `agent_generate` + independent scorer + Post-Repair AnswerVerifier + label-strip + on-the-fly baseline rescore + bootstrap CI helper + V1 `tie_margin=0.0` defaults |
| ✅ 05-25 | deepseek labeling provenance recovery (issue 0033) | Reconstructed `scripts/annotate_perturbation_labels.py` + cleaning_report annotated; full DeepSeek API rerun pending credentials |
| 05-25 | Test suite migration (issue 0032) | conftest, label-leak invariant rewrite, adapter-parity-at-LLM-stack tests |
| 05-25 | Artifact archive (issue 0031) | move pre-D34 artifacts to `legacy_phrase_match_2026_05_22/` + MANIFEST |
| 05-28~30 | At-scale LLM re-test V1.0 (issue 0023) | 596 cases × 10 replays + post-repair under LLM stack — feeds 0026/0028/0029/0036 |
| 05-30 | Free hook calibration V1.0 (issue 0028) | LR fit on re-test outputs (~half-day) |
| 05-30~01 | Researcher 130-case adjudication V1.0 (issue 0024) | LLM-A (llama-3.3-70b-instruct) + 20-case blind spot-check + κ vs deepseek (~5 hr) |
| 06-01~03 | Researcher 80-ECS inspection V1.0 (issue 0025) | Manually corrected ECS for Experiment 1 (~5 hr) |
| 06-03 | Experiment 2 V1.0 headline (issue 0026) | CMD attribution Macro F1 + bootstrap CI on 130 adjudicated cases vs LLM-as-judge + evidence-recall + random; cost/latency column; per-source heatmap with CIs |
| 06-04 | Hook efficacy supplementary table (issue 0028) | recall + cost reduction |
| 06-04 | Surrogate-gap LLM rerun supplementary (issue 0036) | retention% on 4 gold-dependent labels, 50-case hold-out |
| 06-06 | Experiment 1 V1.0 + coupled-failure subset report | 5-mode (none/full_trace/corrected_only/corrected_only_padded/contrastive) on 80 cases; coupled-failure post-hoc on 30-50 near-tie cases (issue 0029) |
| 06-07 | Layered positioning + Decision 30 addendum (issue 0030) | ~2 hr writing, no code |
| 06-08~10 | V1.0 arxiv preprint draft | Headline 130-case + Experiment 1 + layered positioning + supplementary scale check + supplementary hook + supplementary coupled-failure + supplementary surrogate-gap. Cross-dataset claim = coverage only (V1.0 N too small for generalization) |
| post-corpus | Issue 0035 corpus migration cutover | V1.1 trigger: re-run 0023/0024/0026/0027/0028/0029/0031/0036 on full corpus |
| post-V1.1 | V1.1 venue submission | Same headline structure with full-corpus N; cross-dataset generalization claim now defensible |

**Critical path V1.0**: LLM eval infra → re-test → adjudication → Experiment 2 → arxiv preprint. Hook calibration off critical path. Rewind benchmark off critical path.

**Critical path V1.1**: issue 0035 corpus availability → all V1.0 issues re-run → venue submission.

## Next Steps (ordered by dependency)

Per Decision 34 (2026-05-23/24 grilling). All historical V0/V1 issue items remain ✅. This list is forward-only and tracks both V1.0 (06-10 arxiv) and V1.1 (post-corpus venue) milestones. Each issue 0022-0036 has individual detail map under `cmd_innovation_core/issues/`.

1. **issue 0033 — deepseek labeling provenance recovery** (R4-prov) — provenance recovered 05-25; full 596-case DeepSeek API rerun pending credentials before citing scale sanity agreement.
2. **issue 0022 — LLM eval infrastructure wiring** (R1+R2+R5 + Gap 3/4, target 05-25~28). Wiring edits (replays.py shortcut gate + label-strip; post_repair.py AnswerVerifier wiring; harness.py pass-through + on-the-fly baseline rescore; PhraseMatchShortcutWarning category; conftest filter; bootstrap CI helper; V1 entry-point `tie_margin=0.0` defaults; new tests).
3. **issue 0032 — Test suite migration** (target 05-25~28). conftest filter, label-leak invariant rewrite, adapter-parity-at-LLM-stack tests.
4. **issue 0031 — Artifact archive + manifests** (R9, target 05-25). Pre-D34 artifacts → `legacy_phrase_match_2026_05_22/` with MANIFEST.txt.
5. **issue 0023 — At-scale LLM re-test V1.0** (R1+R3, target 05-28~30). 596 × 10 replays under LLM stack → `at_scale_llm_retest.csv`. Feeds 0026 / 0028 / 0029 / 0036.
6. **issue 0028 — Hook calibration V1.0** (R5 supplementary, target 05-30). Refactor `calibrate_hook.py` to consume 0023 outputs. Half-day. Off critical path.
7. **issue 0024 — Researcher 130-case adjudication V1.0** (R4+R11, target 05-30~01). LLM-A + 20-case blind spot-check. ~5 hours.
8. **issue 0025 — Researcher 80-ECS inspection V1.0** (R7, target 06-01~03). ~5 hours.
9. **issue 0026 — Experiment 2 V1.0 headline run** (R8, target 06-03). 130 adjudicated cases + 4 baselines + cost/latency + bootstrap CIs + per-source heatmap.
10. **issue 0036 — Surrogate-gap LLM-stack rerun** (R10/Q18, target 06-04). 50-case hold-out, 4 gold-dependent labels, retention%.
11. **issue 0029 — Coupled-failure subset post-hoc** (R3, target 06-04). 30-50 near-tie cases, manual inspection, calibrated tie_margin.
12. **issue 0027 — Experiment 1 V1.0 hardened** (R7, target 06-06). 80 cases × 5 modes, McNemar's tests Δ_1 + Δ_2.
13. **issue 0030 — Layered positioning + Decision 30 addendum** (R6, target 06-07). ~2 hr writing.
14. **V1.0 arxiv preprint** (target 06-08~10). Cross-dataset claim = coverage only.
15. **issue 0035 — Corpus migration cutover** (R10, post-V1.0). Full-corpus rebuild + re-annotation; triggers V1.1 reruns of 0023/0024/0026/0027/0028/0029/0031/0036.
16. **V1.1 venue submission** (post-0035). Cross-dataset claim = explicit generalization (post-corpus N supports it).

Post-paper V2: cascade repair via LLM self-modification on provenance DAG, multi-agent CMD, runtime repair loop, real-time live mem0/Letta integration.

## V1 Key Decisions (reference)

Recorded in `cmd_innovation_core/plans/cmd_open_decisions.md` (Decisions 13-34):

- V0+V1+V2 = single paper (D15)
- First adapter: mem0. Second: Letta. (D14)
- RPE prefilter (D27): 2-tier architecture (PrefixGuard Tier-1 + RPE Tier-2) — superseded by D33 for hook internals
- Provenance (D28): Execution Lineage DAG + trace-mem Citation
- Gold evidence limitation (D29): 4/11 gold-dependent, self-supervision mitigation
- Rewind 5-dim differentiation (D30): granularity, diagnosis, repair, validation, learning
- Pre-CMD Hook (D31): single `post_retrieve_hook`, zero-gold online, offline calibration — superseded by D33
- Post-Gate Pipeline (D32): repair layering (4-tier), iterative repair, self-supervision surrogate (→0021 Step 2), FM lifecycle
- Hook Redesign (D33): two-stage sequential (empty_ctx + RPE Judge 16-feature per-replay ranking), 0021
- Paper Claim Integrity (D34): 130-case adjudicated headline, 596-case scale sanity, hook supplementary, Rewind benchmark dropped

## Non-Code Skeleton Sync

When updating planning files, order is: PRD → issues → prototypes → TDD → gates → knowledge → reference_notes → plans → experiments → hypotheses → CLAUDE.md + TASK.md → config → logs.

## Evidence Gates

Do not make paper claims until the corresponding artifact exists:

- Attribution: `attribution_table.csv` + confusion matrix ✅
- Comparator: `comparison_metrics.csv` ✅
- Repair: Post-Repair Context Replay table ✅
- Recurrence: Failure Memory recurrence comparison ✅
- Version gates: V0→V1 ✅ / V1→V2 ✅ as mechanics validation; paper-grade attribution awaits Decision 34 LLM re-test + 130-case adjudication

Paper claims focus: (1) automated counterfactual attribution at operation-level granularity, (2) Post-Repair Context Replay as automated semantic quality gate, (3) full detection→diagnosis→repair→validate→store loop.

## Non-Goals

- Do not build a production memory agent — CMD-Audit is a research harness, CMD-Skill Adapter is a deployment layer.
- Do not add UI or dashboard work.
- Do not train a learned attribution classifier — rule-based replay deltas are the evidence foundation.
- Do not expand labels beyond the 11 active pipeline labels without updating the issue plan first.
- Do not claim gold evidence is available online — 4/11 labels need it offline (information-theoretic bound).
