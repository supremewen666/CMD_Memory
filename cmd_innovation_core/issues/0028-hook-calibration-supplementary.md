---
id: 0028
title: Hook calibration from at-scale re-test outputs (supplementary)
status: needs-triage
labels: [paper, decision-34, hook, supplementary]
blocks: []
blocked_by: [0023]
created: 2026-05-24
---

# 0028 — Hook calibration as free byproduct

## Why

Decision 34 R5 demoted the Pre-CMD Hook (Decision 33 / issue 0021) to supplementary. Original Decision 33 step 1 prescribed running SubagentScorer (qwen) inline on 5960 (case × replay) pairs to produce training labels. With issue 0023 producing those `recovery_gain` values under the LLM stack as a byproduct of the at-scale re-test, the hook's training labels become free — same scorer the paper headline uses, no separate LLM pass.

This issue refactors `scripts/calibrate_hook.py` to consume issue 0023's outputs, fits the 16-feature LR, runs the global threshold grid search, and emits constants. No paper-headline impact; the hook efficacy table is one supplementary section.

## Acceptance criteria

| AC | Required behavior | Verified by |
|----|-------------------|-------------|
| AC1 | `scripts/calibrate_hook.py` reads `artifacts/at_scale_llm_retest.csv` produced by issue 0023. No internal LLM calls. | run trace shows 0 LLM API calls |
| AC2 | Training set construction: per `(case, replay)` row, label = `recovery_gain > 0`. Train/hold-out split = 546/50 with `random_state=42`. | persisted `artifacts/hook_calibration/training_set_llm.npz` |
| AC3 | LR fit: 16 features (6 global + 10 replay_type one-hot), `class_weight='balanced'`, `random_state=42`. Persist weights + intercept to `cmd_audit/hook/constants.py` (RPE_JUDGE_WEIGHTS, RPE_JUDGE_INTERCEPT). | constants.py round-trip; coefficients match training |
| AC4 | Optional Step 2: surrogate-vs-gold gap measurement on 50 hold-out cases for the 4 gold-dependent labels. Reuses 0023 outputs (no new LLM calls). | `artifacts/hook_calibration/surrogate_gap.csv` |
| AC5 | Step 3: global threshold grid search `TOP_K ∈ {2,3,4,5} × FALLBACK_THRESHOLD ∈ [0,1] step 0.05` = 84 grid points. F2 recall-priority objective. Cross-adapter (single set of constants). Selected constants written to `cmd_audit/hook/constants.py`. | grid CSV + best-config text artifact |
| AC6 | Hook efficacy supplementary table generated: replays the at-scale re-test outputs through the calibrated hook in inference mode (no retraining); reports (a) recall — fraction of cases where the calibrated hook's selected replays include the actual top-recovery replay, (b) cost reduction — fraction of replays skipped vs all-10. One-table paper artifact. | `artifacts/hook_efficacy_supplementary.csv` + summary line |
| AC7 | Per-agent calibration deferred to V2 — explicitly noted as out of scope per Decision 33 grilling decision. | text artifact mentions deferral |

## Reporting style (paper-craft)

- This is a supplementary section, not a paper claim. One table + one paragraph in the methods/supplementary section.
- Headline: "A trained selector achieves recall ≥ R% with C% replay cost reduction." Numbers report what they are; no advocacy.
- The hook demotion itself is documented in `cmd_open_decisions.md` Decision 34 R5 — paper does not need to explain; the supplementary section just reports efficacy.

## Files affected

| File | Edit type |
|------|-----------|
| `scripts/calibrate_hook.py` | refactor to read from 0023 CSV; remove inline LLM calls |
| `scripts/build_hook_efficacy_supplementary.py` | new; inference-only hook recall/cost-reduction table from 0023 CSV |
| `cmd_audit/hook/constants.py` | regenerated weights + intercept + TOP_K + FALLBACK_THRESHOLD |
| `artifacts/hook_calibration/training_set_llm.npz` | new |
| `artifacts/hook_calibration/grid_search.csv` | new |
| `artifacts/hook_calibration/surrogate_gap.csv` | new |
| `artifacts/hook_efficacy_supplementary.csv` | new |
| `artifacts/hook_efficacy_summary.txt` | new |

## Out of scope

- New tests for the hook itself (existing 34 tests for issue 0021 remain green).
- Hook code changes (scripts only refactor).
- Per-agent calibration (V2 scope).

## Estimate

Half a day. No LLM calls; CPU-only LR fit.

## Dependency

- Blocked by 0023 (consumes its `recovery_gain` outputs).
- No blockees — supplementary deliverable.

## Detail map

`REPAIR.md` §1 R5 (full hook demotion), §11E (calibrate_hook.py refactor spec).
