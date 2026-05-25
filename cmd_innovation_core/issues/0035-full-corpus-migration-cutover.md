---
id: 0035
title: Full-corpus migration cutover (V1.1 trigger)
status: needs-triage
labels: [paper, decision-34, dataset, v1-1-trigger]
blocks: []
blocked_by: [0033]
created: 2026-05-24
---

# 0035 — Full-corpus migration cutover

## Why

Decision 34 R10 binds the V1.1 venue submission to a full-corpus run. The current 596 cases are a sampled subset (~17% of the 3564 dedup'd pool) drawn from raw 4064 LongMemEval/MemoryArena/ToolBench cases via a CMD-relevance score filter. Full corpus eliminates the sampling-bias concern (Branch D Q15.1 — "filtered for solvability"), unlocks N for cross-dataset generalization claim (P9, R10/Q20 → option (a)), and produces stronger evidence for every other paper claim.

Issue 0035 is the V1.1 trigger event: when this lands, issues 0023/0024/0026/0027/0028/0029/0031/0036 re-run on the new corpus. V1.0 arxiv preprint ships first (06-10) with the 596-state numbers; V1.1 venue submission supersedes once 0035 + downstream re-runs are complete.

## Acceptance criteria

| AC | Required behavior | Verified by |
|----|-------------------|-------------|
| AC1 | Full corpus build pipeline reruns: 4064 raw → dedup → quality filter → final corpus. Final corpus is the *full* dedup'd set (no relevance-score sampling), or with the sampling filter explicitly disabled and documented. | new `data/cleaned_cases/cleaned_cases.json` v1.1; size ≈ 3500+ cases |
| AC2 | `data/cleaned_cases/cleaning_report.txt` regenerated with v1.1 stats (raw_total, dedup'd, quality-filtered, final). Header line `release_version: v1.1` added. CMD relevance score distribution reported with full distribution (no implicit threshold). | report regenerated |
| AC3 | `scripts/annotate_perturbation_labels.py` re-runs against the v1.1 corpus. New `data/probe_cases/real_*_cases.json` files generated with deepseek-v4-pro-max labels for the full corpus. The original v1.0 deepseek labels for the 596-case subset preserved at `data/probe_cases/v1_0_archive/real_*_cases.json` for V1.0 arxiv reproducibility. | both versions on disk |
| AC4 | Dataset hash recorded: SHA-256 over the sorted concatenation of `case_id`s in v1.1 final corpus, written to `data/cleaned_cases/cleaning_report.txt` § Corpus Hash. Same for v1.0 archive. | hashes in report |
| AC5 | `data/probe_cases/researcher_labeled_subset.json` `release_version` field flips to `v1.1`. New 130 cases re-sampled from v1.1 corpus pool with `random_state=42` (different cases than v1.0). v1.0 subset archived at `data/probe_cases/v1_0_archive/researcher_labeled_subset.json`. | json round-trip |
| AC6 | `data/probe_cases/experiment_01_inspected_ecs.json` similarly archived to v1_0_archive and re-sampled for v1.1. | both versions on disk |
| AC7 | `artifacts/` regeneration matrix (issue 0031) re-runs: pre-V1.1 artifacts archived to `artifacts/v1_0_596_2026_06_XX/` with MANIFEST; new v1.1 artifacts populate `artifacts/`. | manifest regen |
| AC8 | All downstream issues (0023/0024/0026/0027/0028/0029/0036) re-run with V1.1 acceptance gates. Each issue's V1.1 AC is verified before V1.1 venue submission. | per-issue checklist |

## V1.1 re-run cascade

```
0035 lands
  → 0023 re-runs on full corpus (overnight, GPU-fine per user note)
  → 0024 re-samples 130 cases from v1.1 pool, researcher re-adjudicates
       (LLM-A llama-3.3-70b + 20-case blind spot-check, ~5 hr again)
  → 0026 Experiment 2 V1.1 headline run on new 130 cases
  → 0027 Experiment 1 V1.1 — 80 cases re-sampled, ECS re-inspected (~5 hr researcher)
  → 0028 hook calibration V1.1 re-fits on new re-test outputs
  → 0029 coupled-failure subset re-runs on new tie distribution
  → 0036 surrogate-gap V1.1 on proportionally-sized hold-out
  → 0031 artifact regen re-runs; manifests reference v1.1
```

V1.1 venue submission gated on all eight downstream V1.1 ACs being satisfied.

## Dataset version policy

- `release_version: v1.0` — 596-case dataset state (2026-05-22 cleaning), used for V1.0 arxiv preprint.
- `release_version: v1.1` — full-corpus dataset state (2026-XX-XX cleaning), used for V1.1 venue submission.
- Both versions preserved on disk under `data/probe_cases/v1_0_archive/` and `data/probe_cases/` (current = v1.1 once 0035 lands).
- Every artifact's MANIFEST.txt records which dataset version it was produced against.
- Dataset hash (SHA-256 over sorted `case_id`s) is the canonical version identifier; soft string `v1.0`/`v1.1` is for human readability.

## Out of scope

- Re-defining the CMD relevance score formula — out of scope for this issue. If the score formula itself is buggy, that's a separate issue.
- Multi-rater κ on full corpus — V2 community-resource scope.
- Re-evaluating earlier issues (0022 wiring, 0030 layered positioning) — they don't depend on corpus state.

## Estimate

- Dataset rebuild: depends on cleaning pipeline complexity; estimate 1-2 days.
- deepseek re-annotation cost: ~$15-30 if fully reannotated.
- Researcher re-adjudication: ~5 hr (issue 0024 V1.1 path).
- Researcher ECS re-inspection: ~5 hr (issue 0025 V1.1 path).
- Cascade re-runs: ~3-5 days wall-clock for all downstream LLM runs.

Total V1.1 cycle: ~1-2 weeks from 0035 landing to V1.1 venue submission ready.

## Dependency

- Blocked by 0033 (deepseek labeling provenance must be checked in before re-annotation can be reproducibly run).
- No blockees — V1.0 arxiv preprint ships independently.

## Detail map

`REPAIR.md` §15 (artifact regeneration matrix), §18 (V1.0/V1.1 dual-run pattern), §10A `release_version` field.
