---
id: 0026
title: Experiment 2 — CMD attribution headline on 130 adjudicated cases
status: needs-triage
labels: [paper, decision-34, experiment, headline]
blocks: []
blocked_by: [0022, 0023, 0024]
created: 2026-05-24
---

# 0026 — Experiment 2 headline run

## Why

The paper's primary claim under Decision 19 ("automated counterfactual attribution at operation-level granularity") binds to this issue's output. Per Decision 34 R3+R4+R8, the headline number is computed on:

- The 130 researcher-adjudicated cases (issue 0024) — high+medium confidence subset is the headline; low-confidence in appendix.
- 4 baseline comparators: random_label / evidence_recall / subagent_judge / llm_judge (Decision 19 falsification test).
- All 10 V1 replays per case, no hook (Decision 34 R5).
- `tie_margin = 0.0` for argmax (Decision 34 R3).
- AttributionFailed reported as a separate column, framed as principled abstention.
- 8-label headline + 11-label supplementary architecture-completeness note (Decision 34 R8 = Q13).

The 596-case scale sanity check is a parallel deliverable (already produced by issue 0023; this issue cross-references it for the supplementary table).

## Acceptance criteria

| AC | Required behavior | Verified by |
|----|-------------------|-------------|
| AC1 | All 130 adjudicated cases scored under same agent + scorer + verifier as issue 0023 (no re-running of agent_generate; reuse `artifacts/at_scale_llm_retest.csv` rows for the 130 case_ids). Cohen's κ filter applied to identify high+medium confidence subset. | join check on case_id |
| AC2 | Headline metrics computed: Macro F1 (8 active labels), top-1 attribution accuracy, top-2 accuracy, per-label F1, AttributionFailed count. Reported on (a) high+medium confidence subset = primary headline, (b) all 130 = sensitivity check. | `artifacts/experiment_02_headline.csv` |
| AC3 | 4 baseline comparators evaluated on the same 130 cases. CMD's number reported alongside, not in isolation. | `artifacts/experiment_02_comparison.csv` |
| AC4 | Coverage rate reported: `coverage = 1 - (AttributionFailed / 130)`. Two-tier framing in the headline text: "CMD attributes X% at operation level (N/130); among attributed, Macro F1 = Y vs LLM-as-judge = Z." | summary text artifact |
| AC5 | 11-label supplementary table runs on the synthetic V1 cases (`v1_granularity_error_case.json`, `v1_graph_error_case.json`, `v1_safety_error_case.json`) and reports per-label F1 framed as label-coverage validation, not a headline contribution. | `artifacts/experiment_02_supplementary_11label.csv` |
| AC6 | Per-label × per-source heatmap generated for the headline 130 cases (8 labels × 3 sources). | `artifacts/experiment_02_heatmap.csv` |
| AC7 | RepairAction descriptive measurement: per-case dump of `(case_id, predicted_label, action_type, target_item_id, target_store)` so the methods section can show 5 representative `(case → action)` traces. Verifies Level 2 capability per Decision 34 R8/Q12. | `artifacts/experiment_02_repair_actions.csv` |
| AC8 | Source-imbalance disclosure: per-label distribution across LongMemEval/MemoryArena/ToolBench documented. `route_error` and `ingestion_error` are toolbench-only by data reality, not by selection bias. | text block in headline summary |
| AC9 | Bootstrap CIs (1000-iter case-level resample) reported for Macro F1, top-2 accuracy, per-label F1, per-baseline metrics, and Cohen's κ values consumed from issue 0024. | CI columns in headline/comparison/heatmap artifacts |
| AC10 | Cost/latency headline columns reported from real metadata only: `tokens_per_case`, `wallclock_sec_per_case`, `usd_per_case`, with agent + scorer + verifier subtotals where available. If metadata is absent, numeric cost cells stay blank and `cost_metadata_status=missing_cost_metadata`; hardcoded pseudo-costs are forbidden. | `artifacts/experiment_02_cost_latency.csv` + summary table |
| AC11 | Two-evaluator robustness on the 130-case headline set: evaluator-A and evaluator-B Macro F1 + agreement reported. The evaluator family must differ from deepseek, qwen, and LLM-A. | `artifacts/experiment_02_two_evaluator_robustness.csv` |
| AC12 | All artifact files include MANIFEST.txt entries describing the LLM stack (issue 0031). | manifest cross-check |

## Reporting style (Decision 34 R8 / paper-craft)

- Primary headline number: high+medium confidence subset, coverage% + Macro F1 on attributed cases.
- Sensitivity: same numbers on all 130 (low-confidence included).
- Comparison table: lead with widest gap (CMD vs random_label), include all 4 baselines, llm_judge present and visible.
- AttributionFailed cases reported as "principled abstention" with conformal-prediction citation (Romano et al. or Vovk & Shafer).
- Repair depth: claim "Level 2 capability demonstrated by all CMD repairs (target_item_id non-null in M of N cases; null target represents new-item creation, still item-content repair)."
- Cost/latency appears in the headline comparison table, not hidden in supplementary.
- Two-evaluator robustness is reported only on the 130-case headline set.

## Files affected

| File | Type |
|------|------|
| `artifacts/experiment_02_headline.csv` | new |
| `artifacts/experiment_02_comparison.csv` | new |
| `artifacts/experiment_02_supplementary_11label.csv` | new |
| `artifacts/experiment_02_heatmap.csv` | new |
| `artifacts/experiment_02_repair_actions.csv` | new |
| `artifacts/experiment_02_cost_latency.csv` | new |
| `artifacts/experiment_02_two_evaluator_robustness.csv` | new |
| `artifacts/experiment_02_summary.txt` | new; reviewer-facing prose summary |
| `scripts/build_experiment_02_tables.py` | post-processes 0023 + 0024 artifacts into headline/comparison/heatmap tables; no LLM calls |
| `cmd_innovation_core/plans/experiment_02_cmd_attribution.md` | update with realized numbers + reporting language |

## Out of scope

- Coupled-failure tie_margin calibration — issue 0029.
- Hook efficacy supplementary — issue 0028.
- Experiment 1 — issue 0027.
- Layered positioning section — issue 0030.

## Estimate

~1 day. Most heavy lifting is in 0023; this issue is filtering + aggregation + presentation.

## Dependency

- Blocked by 0022 (LLM stack code).
- Blocked by 0023 (per-case `recovery_gain` under LLM stack).
- Blocked by 0024 (130-case adjudicated labels).
- No blockees — this is a paper-headline producer.

## Detail map

`REPAIR.md` §1 R3+R4 (Decision 34 main), §8 (experiment_02 plan rewrite), R8 sub-points (Q11 collapsed FM, Q12 repair depth, Q13 AttributionFailed + 8-label), §16 P4/P11/P12 (cost/latency, two-evaluator robustness, bootstrap CIs).
