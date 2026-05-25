---
id: 0034
title: Decision 34 issue tracker index (V1.0/V1.1 dual-release)
status: needs-triage
labels: [paper, decision-34, meta]
blocks: []
blocked_by: []
created: 2026-05-24
updated: 2026-05-24 (R8-R11 expansion + 0035 + 0036)
---

# 0034 — Decision 34 issue index

## Why

Issues 0022-0036 each implement a slice of Decision 34 (paper claim integrity grilling, 2026-05-23/24). This file is the index that maps Decision 34 resolutions (R1-R11) to the 15 issues, plus the dependency graph + V1.0/V1.1 dual-release sequencing.

Source of truth for Decision 34: `cmd_innovation_core/plans/cmd_open_decisions.md` Decision 34 + `REPAIR.md` at repo root.

## Decision 34 → Issue map

| Resolution | Issues | Status |
|------------|--------|--------|
| R1 (at-scale LLM re-test, parity baseline rescore, drop label leak) | 0022 (wiring) → 0023 (run V1.0) → 0035 (V1.1 trigger) → 0031 (artifacts) | 0022 done; 0031 archive-complete; 0023 pending |
| R2 (Post-Repair agent_generate + AnswerVerifier + τ) | 0022 (wiring) → 0023 (run) | 0022 done; 0023 pending |
| R3 (tie_margin = 0.0 headline; coupled-failure post-hoc) | 0022 (config) + 0029 (subset) | 0022 done; 0029 pending |
| R4 (130-case researcher headline + 596 sanity check + κ) + R11 (LLM-A llama-3.3-70b + 20-case blind spot-check) | 0024 (adjudication) + 0033 (provenance) → 0026 (run) | 0033 provenance recovered; API rerun pending credentials; 0024 pending |
| R5 (hook → supplementary; bypass for headline; free calibration) | 0022 (bypass) + 0028 (calibration) | 0022 done; 0028 pending |
| R6 (drop Rewind head-to-head; layered positioning) | 0030 (writing only) | needs-triage |
| R7 (Experiment 1: 80 × 5 modes + token-control + 3-trial pre-check + ECS inspection) | 0025 (inspection) → 0027 (run) | needs-triage |
| R8 (Q11 FM-collapse, Q12 repair-depth as design claim, Q13 AttributionFailed + 8-label) | 0026 implements; 0027 absorbs Q11 | needs-triage |
| R9 (artifact regeneration matrix + archive + manifest) | 0031 | archive-complete; LLM-stack regen pending after 0023 |
| R10 (V1.0/V1.1 dual-release; cross-dataset claim version-gated; ~9/11 deployment with retention% backing; bootstrap CIs; cost/latency in headline; two-evaluator robustness) | 0035 (V1.1 trigger) + 0036 (surrogate-gap) + paper-craft updates in 0026/0027/0028/0029 | needs-triage |
| R11 (LLM-A llama-3.3-70b for adjudication + 20-case blind spot-check) | 0024 updated 2026-05-24 | needs-triage |
| Test migration | 0032 (conftest, label-leak invariant, adapter parity at LLM stack) | done |

## Dependency graph

```text
V1.0 critical path (target 06-10 arxiv preprint):

0033 (deepseek labeling provenance)
  → 0022 (wiring sprint: LLM stack + Gap 3/4 bootstrap/tie-margin defaults)
  → 0032 (test migration: conftest, label-leak rewrite, adapter parity at LLM)
  → 0031 (artifact archive + manifest scheme)
  → 0023 (at-scale LLM re-test V1.0 on 596 cases)
  → 0028 (hook calibration V1.0, supplementary)
  → 0024 (researcher 130-case adjudication V1.0, LLM-A + spot-check)
  → 0025 (researcher 80-ECS inspection V1.0)
  → 0026 (Experiment 2 V1.0 headline on 130 adjudicated cases)
  → 0036 (surrogate-gap LLM rerun on 50-case hold-out, supplementary)
  → 0029 (coupled-failure subset post-hoc)
  → 0027 (Experiment 1 V1.0 hardened, 80 cases × 5 modes)
  → 0030 (layered positioning + Decision 30 addendum)
  → V1.0 arxiv preprint

V1.1 trigger (post-arxiv, target venue submission):

0035 (full-corpus migration cutover)
  ↑
  └── needs 0033 (deepseek labeling re-runnable)
  └── triggers re-run of 0023/0024/0025/0026/0027/0028/0029/0031/0036 with V1.1 ACs
```

## V1.0 timeline (target 06-10 arxiv preprint)

- 05-25 → 0033 starts; 0022 starts after provenance script lands
- 05-26~28 → 0022 + 0032 finish; 0031 archive complete
- 05-28~30 → 0023 V1.0 runs
- 05-30 → 0028 calibration (free byproduct of 0023)
- 05-30~01 → 0024 researcher adjudication V1.0 (LLM-A + 20-case spot-check, ~5 hr)
- 06-01~03 → 0025 ECS inspection V1.0 (~5 hr)
- 06-03 → 0026 Experiment 2 V1.0 headline
- 06-04 → 0036 surrogate-gap V1.0; 0029 coupled-failure
- 06-06 → 0027 Experiment 1 V1.0
- 06-07 → 0030 layered positioning written
- 06-08~10 → V1.0 arxiv preprint draft consolidation

## V1.1 timeline (post-corpus, target venue submission)

- 0035 lands (full corpus rebuilt + re-annotated)
- 0023 V1.1 re-runs on full corpus
- 0024 V1.1 re-samples 130 from full corpus pool, researcher re-adjudicates
- 0025 V1.1 re-inspects 80 ECS records
- 0026/0027/0028/0029/0036 V1.1 re-runs
- 0031 V1.1 manifest update
- V1.1 venue submission

## Closure criteria for Decision 34

When all 15 issues (0022-0036) reach completed state with V1.0 + V1.1 ACs both satisfied, Decision 34 is closed. Update `cmd_innovation_core/plans/cmd_open_decisions.md` Decision 34 status from "RESOLVED (planning)" to "RESOLVED (V1.0 arxiv + V1.1 venue submission complete, paper integrity restored)".

## Detail map

- `REPAIR.md` (repo root) — full paste-ready edit plan, source of truth.
- `cmd_innovation_core/plans/cmd_open_decisions.md` Decision 34 — R1-R11 resolutions.
- Individual issue files 0022-0036 — implementation details.
