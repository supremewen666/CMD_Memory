---
id: 0023
title: At-scale LLM re-test on 596 cases
status: needs-triage
labels: [paper, decision-34, experiment, evaluation]
blocks: [0026, 0028, 0029, 0031]
blocked_by: [0022, 0033]
created: 2026-05-24
---

# 0023 — At-scale LLM re-test on 596 cases

## Why

After 0022's wiring lands, the 596-case real-data suite must be re-run under the LLM stack to produce paper-grade evidence. Per Decision 34 R1+R3+R4, the re-test:

- Replaces phrase-match scoring with `agent_generate` (qwen2.5-7b ollama) + independent evaluator scorer (specific model TBD; gpt-4o-mini class candidate).
- Drops `CMD ATTRIBUTION LABEL` from replay context (label is the output, not an input).
- Rescores `vector_memory` baseline on-the-fly so `recovery_gain` is parity-scored.
- Sets `tie_margin=0.0` for headline argmax (zero free parameters in the decision rule).
- Bypasses the hook (Decision 34 R5) — runs all 10 replays per case.

This issue produces three downstream artifacts at once:

1. **Per-case per-replay `recovery_gain`** under the LLM stack — input to issue 0026 (Experiment 2 headline) and issue 0028 (hook calibration as free byproduct).
2. **Scale sanity check number** — supplementary table in paper: "CMD reproduces deepseek-v4-pro-max labels at Macro F1 = Y across 596."
3. **Coupled-failure raw distribution** — input to issue 0029 (post-hoc near-tie subset analysis).

## Acceptance criteria

| AC | Required behavior | Verified by |
|----|-------------------|-------------|
| AC1 | All 596 cases × 10 replays executed via `run_full_real_suite(use_hook=False, agent_generate=qwen, evidence_scorer=evaluator, answer_verifier=evaluator, on_the_fly_baseline_rescore=True, tie_margin=0.0)`. | run log + artifact row count |
| AC2 | Per-source artifacts regenerated under LLM stack (LongMemEval 200 / MemoryArena 198 / ToolBench 198). 11 artifact types, see issue 0031. | artifact set listed in 0031 |
| AC3 | Macro F1 vs deepseek labels reported (single number for the 596-case scale sanity check). Per-source split also reported. | `artifacts/at_scale_llm_retest_summary.txt` |
| AC4 | AttributionFailed cases recorded with failure reason (negative_gain / zero_gain). | column `attribution_failed` + `failure_reason` in `artifacts/attribution_table.csv` |
| AC5 | Per-case `recovery_gain` distribution (10 replays per case, all values) persisted to `artifacts/at_scale_llm_retest.csv` for downstream consumption by 0026, 0028, 0029. | column count check |
| AC6 | Run is reproducible: `random_state=42` for any sampling, ollama model+version pinned, evaluator model+version pinned, `temperature=0`. Pinned values written to `artifacts/at_scale_llm_retest.run_meta.txt`. | meta file present and readable |
| AC7 | Cost recorded: total LLM calls (agent + scorer + verifier), wall-clock, $ estimate. | run log |

## Pre-flight checklist

Before invoking the run:

- [ ] 0022 wiring sprint complete and 803+ tests green
- [ ] 0033 deepseek labeling provenance recovered (re-test references deepseek labels)
- [ ] Evaluator model selected and instantiated; integration tested on 5 cases
- [ ] qwen2.5-7b ollama warmed up; baseline test of `agent_generate` on 5 cases produces non-empty answers
- [ ] Sandbox checksum verification path still active (no production-state mutations during replay)
- [ ] Old artifacts archived to `artifacts/legacy_phrase_match_2026_05_22/` per issue 0031 BEFORE this run starts

## Out of scope

- 130-case researcher headline — issue 0026 (consumes this issue's outputs).
- Hook calibration grid search — issue 0028 (consumes this issue's outputs).
- Coupled-failure tie_margin calibration — issue 0029.
- Experiment 1 — issue 0027 (different dataset, ~80 cases).

## Files affected

| File | Role |
|------|------|
| `artifacts/at_scale_llm_retest.csv` | new; 596 × 10 = 5960 rows of `(case_id, source, replay_name, recovery_gain, evidence_score, answer, baseline_evidence_score_llm, attribution_failed, failure_reason)` |
| `artifacts/at_scale_llm_retest_summary.txt` | new; aggregate stats for sanity check |
| `artifacts/at_scale_llm_retest.run_meta.txt` | new; run reproducibility metadata |
| `scripts/build_at_scale_retest_summary.py` | post-process existing retest CSV into scale-sanity summary; no LLM calls |
| `scripts/write_at_scale_retest_run_meta.py` | writes intended run metadata; does not execute retest |
| `artifacts/attribution_table*.csv` | regenerated per source |
| `artifacts/attribution_confusion_matrix*.csv` | regenerated per source |
| `artifacts/comparison_metrics*.csv` | regenerated per source (4 baselines: random/evidence_recall/subagent_judge/llm_judge) |
| `artifacts/sandbox/post_repair_table*.csv` | regenerated per source |
| `artifacts/sandbox/repair_*.csv` + `.txt` | regenerated per source |
| `artifacts/mem0_adapter_parity.csv` | regenerated under LLM stack (issue 0032 verifies parity property) |
| `artifacts/MANIFEST.txt` | new; describes LLM-stack provenance of new artifacts |

## Estimate

- Wall-clock: 4-8 hours of LLM call time (596 cases × ~12 LLM calls per case = ~7000 calls; agent at ~3-5 sec/call = 6-10 hours, evaluator at ~1 sec/call = 2 hours). Run overnight.
- $ cost: ~$2-5 evaluator calls (cheap class) + ollama free.

## Dependency

- Blocked by 0022 (LLM stack wiring).
- Blocked by 0033 (deepseek labeling provenance — needed to interpret per-source agreement).
- Blocks 0026 (Experiment 2 needs per-case recovery_gain under LLM stack to score the 130-case subset).
- Blocks 0028 (hook calibration consumes 5960-row CSV).
- Blocks 0029 (coupled-failure subset draws from per-case top-2 gap distribution).
- Blocks 0031 (artifact regeneration uses this run's outputs).

## Detail map

`REPAIR.md` §1 R1+R3, §2 V0V1 caveat block, §11D `run_case_full_v1` pass-through.
