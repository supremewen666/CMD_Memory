---
id: 0027
title: Experiment 1 — 5-mode context construction on 80 cases
status: needs-triage
labels: [paper, decision-34, experiment, headline, experiment-1]
blocks: []
blocked_by: [0022, 0025]
created: 2026-05-24
---

# 0027 — Experiment 1 hardened run

## Why

Decision 19 paper claim #1 ("full detect→diagnose→repair→**store→reuse** loop") binds to this issue's output. Per Decision 34 R7 + R8/Q11, Experiment 1 absorbs the standalone Failure Memory recurrence comparison (3-case smoke `recurrence_comparison.csv`) into a properly-powered context-construction comparison. The standalone recurrence comparison is retired.

The hardened design tests whether `wrong_memory + cause + corrected_memory + repair_guidance` (contrastive mode) provides signal beyond `corrected_memory + repair_guidance` (V0/V1 corrected-only mode), with a token-control mode that rules out "more tokens helps" as the explanation.

## Acceptance criteria

| AC | Required behavior | Verified by |
|----|-------------------|-------------|
| AC1 | 80 cases — 20 per label across 4 labels (`retrieval_error`, `compression_error`, `premature_extraction_error`, `reasoning_error`). Drawn from issue 0025's inspected ECS records. | dataset cross-check |
| AC2 | 5 modes per case: `none` / `full_trace` / `corrected_only` / `corrected_only_padded` / `contrastive`. `corrected_only_padded` adds neutral filler tokens to match `contrastive`'s character count (±5 tokens tolerance). | mode renderer test |
| AC3 | 3-trial `none`-mode pre-check: each candidate case run 3 times with `temperature=0`, `seed=42`, identical prompt; if ≥1 of 3 trials produces correct answer, case is replaced with another from same-label pool. | pre-check log |
| AC4 | Core run: 80 cases × 5 modes = 400 LLM calls (one answer per (case, mode)). | call log |
| AC5 | Optional secondary: LLM-judge semantic-match on each answer (80 × 5 = 400 calls). Same evaluator as Experiment 2 for consistency. | call log |
| AC6 | Two McNemar's tests reported: (a) `Δ_1 = EM(contrastive) - EM(corrected_only)` (vs literature comparison); (b) `Δ_2 = EM(contrastive) - EM(corrected_only_padded)` (token-controlled, causal). Auxiliary `Δ_pad = EM(corrected_only_padded) - EM(corrected_only)` reported separately. | results CSV with p-values |
| AC7 | Per-label sub-analyses: each Δ computed within each of the 4 labels. | per-label result rows |
| AC8 | Conclusion stated using the 2x2 outcome table (REPAIR §9D): {Δ_1 sig, Δ_2 sig} → "contrastive helps, V2 should adopt"; {Δ_1 sig, Δ_2 ≈ 0} → "improvement is token effect, do not adopt"; {Δ_1 ≈ 0, Δ_2 ≈ 0} → "corrected_only sufficient"; {Δ_1 < 0} → "contrastive harmful". | summary text |
| AC9 | Token cost recorded per mode; report mean total tokens per (case, mode). | columns in results CSV |
| AC10 | Compliance checklist (REPAIR §9E) signed off before publication of results. | checklist file |

## Reporting style (Decision 34 R8 / paper-craft)

- Lead with the conclusion drawn from the 2x2 outcome table, not raw EM percentages.
- Show the McNemar p-values prominently for both Δ_1 and Δ_2.
- If Δ_1 > 0 and Δ_2 ≈ 0: report this clearly as the finding ("contrastive's apparent advantage is a token-count artifact"); this is a legitimate paper contribution and serves as a warning to the community using contrastive context construction without token controls.
- Per-label breakdown shows whether the effect localizes (e.g., reasoning_error benefits from contrastive but retrieval_error doesn't).

## Files affected

| File | Type |
|------|------|
| `artifacts/experiment_01_results.csv` | new; per-(case, mode) row |
| `artifacts/experiment_01_summary.txt` | new; reviewer-facing prose with conclusion table |
| `artifacts/experiment_01_per_label.csv` | new; per-label McNemar |
| `data/probe_cases/experiment_01_inspected_ecs.json` | finalized after pre-check exclusions |
| `cmd_innovation_core/plans/experiment_01_context_construction.md` | update with realized numbers |

## Out of scope

- 5-mode ablation (cause-only, wrong_memory-only) — V2 if Experiment 1 returns positive.
- Multi-LLM reproduction (Claude vs GPT etc) — V2 if results are direction-significant.
- Headline attribution on these 80 cases — that's Experiment 2 (issue 0026), different evaluation.

## Estimate

- Wall-clock: ~3-4 hours of LLM calls (~1040 calls including pre-check + secondary judge).
- $ cost: <$3 at evaluator price.
- Researcher: ~2 hours (mode rendering inspection, results review).

## Dependency

- Blocked by 0022 (LLM stack wiring).
- Blocked by 0025 (inspected ECS records).
- No blockees.

## Detail map

`REPAIR.md` §9 (entire experiment_01 plan rewrite — 5 modes, sample 80, 3-trial pre-check, R7 compliance checklist), §1 R7.
