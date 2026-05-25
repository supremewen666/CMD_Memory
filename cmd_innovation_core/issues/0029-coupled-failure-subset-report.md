---
id: 0029
title: Coupled-failure subset post-hoc report (tie_margin calibration)
status: needs-triage
labels: [paper, decision-34, supplementary, coupled-failure]
blocks: []
blocked_by: [0023]
created: 2026-05-24
---

# 0029 — Coupled-failure subset post-hoc report

## Why

Decision 34 R3 dropped `tie_margin` to 0.0 for the headline argmax (zero free parameters). Coupled-failure analysis becomes a separate post-hoc report. Under continuous LLM scoring, two replays scoring within 0.05 of each other can mean (a) genuine coupled failure where multiple operations contributed, or (b) LLM scorer noise. The 596 cases are single-fault by construction, so coupled cases must be identified empirically.

This issue produces one supplementary table + one calibrated `tie_margin` value backed by manual inspection. Not a headline contribution.

## Acceptance criteria

| AC | Required behavior | Verified by |
|----|-------------------|-------------|
| AC1 | Sample 30-50 cases from `artifacts/at_scale_llm_retest.csv` (issue 0023) where the top-2 `recovery_gain` gap is < 0.10. Stratified across labels where possible. | sampling script + persisted seed |
| AC2 | Researcher manually inspects each sampled case, labels each as `genuine_coupled` (multiple operations plausibly contributed) or `scorer_noise` (gap is below LLM evaluator's noise floor). | per-case json or CSV with `coupled_label` column |
| AC3 | `tie_margin` calibrated to maximize coupled-recall ≥ 80% on the inspected set, subject to scorer-noise false-positive ≤ 20%. Report (margin, recall, FP) tuple. | `artifacts/tie_margin_calibration.csv` |
| AC4 | Calibrated `tie_margin` value committed to `cmd_audit/__init__.py` or a new `cmd_audit/constants.py` module as `COUPLED_FAILURE_TIE_MARGIN`. Headline run still uses 0.0; only coupled-failure analysis uses the calibrated value. | constants file |
| AC5 | Supplementary table generated showing how many of the 596 cases would be flagged as coupled under the calibrated `tie_margin`, by label. | `artifacts/coupled_failure_distribution.csv` |
| AC6 | Reporting framing: "We empirically calibrated `tie_margin` on a 30-50 case manual inspection set; on the broader 596-case suite, X% of cases exhibit coupled-failure signature under this margin." Avoid claiming this is "ground truth" coupled detection. | summary text |

## Why this is post-hoc, not pre-registered

The 596 cases are designed as single-fault. There is no a-priori coupled-failure ground truth. Manual inspection on a sampled subset is the only available calibration source, and `tie_margin = 0.0` for headline avoids the appearance of post-hoc tuning of the primary number.

## Files affected

| File | Type |
|------|------|
| `data/probe_cases/coupled_failure_inspected_subset.json` | new; researcher inspection output |
| `cmd_audit/constants.py` (or `__init__.py`) | new constant `COUPLED_FAILURE_TIE_MARGIN` |
| `scripts/calibrate_tie_margin.py` | new; reads 0023 CSV + case metadata + inspection JSON, samples near-ties, fits margin, writes distribution + summary |
| `artifacts/tie_margin_calibration.csv` | new |
| `artifacts/coupled_failure_distribution.csv` | new |
| `artifacts/coupled_failure_summary.txt` | new |

## Out of scope

- True multi-fault probe case construction — V2 dataset work.
- `close_deltas` UX changes — already exposed by `assign_attribution_v1`.

## Estimate

- Researcher: 2-3 hours (30-50 cases × 3-5 min each).
- Code: half day for sampling + calibration scripts.

## Dependency

- Blocked by 0023 (sample from per-case top-2 gap distribution in re-test outputs).
- No blockees.

## Detail map

`REPAIR.md` §1 R3, §3 (V1V2 footer note), TASK §4 Next Step #9.
