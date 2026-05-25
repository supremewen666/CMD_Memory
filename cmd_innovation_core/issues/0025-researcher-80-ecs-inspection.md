---
id: 0025
title: Researcher 80-ECS inspection for Experiment 1
status: needs-triage
labels: [paper, decision-34, dataset, researcher-task, experiment-1]
blocks: [0027]
blocked_by: [0023]
created: 2026-05-24
---

# 0025 — Researcher 80-ECS inspection

## Why

Decision 34 R7 hardens Experiment 1 to 80 cases × 5 modes (`none / full_trace / corrected_only / corrected_only_padded / contrastive`). The 80 ECS records (`cause`, `corrected_memory`, `repair_guidance`) used as Experiment 1 inputs are **CMD-generated**. If CMD's ECS quality is uneven, a positive contrastive result could come from CMD generating cleaner contrastive signals than corrected-only signals — confounding "context-mode effect" with "ECS-generation quality."

Manual ECS inspection decouples the two. Without this step, Experiment 1's headline result is reviewer-rejectable.

## Acceptance criteria

| AC | Required behavior | Verified by |
|----|-------------------|-------------|
| AC1 | 80 cases selected: 20 per label across `retrieval_error`, `compression_error`, `premature_extraction_error`, `reasoning_error`. Drawn from 0023's at-scale re-test outputs (`recovery_gain > 0` cases preferred so ECS is non-empty). | sampling script + json |
| AC2 | For each case, ECS triple (`cause`, `corrected_memory`, `repair_guidance`) is read by the researcher; if quality is poor, edited inline. | json contains `original_ecs` and `edited_ecs` per case |
| AC3 | Edit reasons recorded (e.g., "corrected_memory contained extra unrelated phrasing", "cause referenced wrong operation", "repair_guidance was generic boilerplate"). One-line `edit_reason` per case. | json schema |
| AC4 | If a case's ECS is unusable even after edit (gold evidence is itself ambiguous), case is replaced with another from the same label's pool; replacement reason logged. | json `replaced_cases` array |
| AC5 | Final 80 cases pass `none`-mode 3-trial pre-check (Decision 34 R7 protocol; ≥1 of 3 LLM calls on `none` context produces correct answer → exclude). Cases that fail pre-check are replaced with another from the pool. | issue 0027 verifies before running modes |
| AC6 | Edited ECS triples written back as the inputs to mode-rendering in issue 0027 — not the originals. | code path in 0027 reads `edited_ecs` not `original_ecs` |
| AC7 | Document the inspection process in Experiment 1's methods section: "80 ECS records were inspected and edited where necessary to decouple context-mode effect from ECS-generation quality. K of 80 records were edited; M of 80 cases were replaced." | text artifact in Experiment 1 paper draft |

## Inspection protocol (researcher)

For each case, read:
1. The original `query` + `gold_answer` + `gold_evidence`.
2. The CMD-generated ECS: `cause`, `corrected_memory`, `repair_guidance`.

Edit if any of:
- `cause` references a label name (forbidden — see CONTEXT.md ECS cause constraint).
- `corrected_memory` contains extraneous content not in gold_evidence.
- `corrected_memory` is missing key phrases from gold_evidence.
- `repair_guidance` is generic boilerplate ("update memory", "fix the error").

Edit conservatively — only fix what's wrong, don't rewrite to taste.

Replace if:
- gold_evidence itself is ambiguous (multiple valid interpretations).
- the case's original perturbation is unclear from the trace alone.

## Files affected

| File | Edit type |
|------|-----------|
| `data/probe_cases/experiment_01_inspected_ecs.json` | populate `cases` array (stub from REPAIR §10C) |
| `scripts/sample_experiment_01_subset.py` | new; samples 80 cases from 0023 re-test outputs |

## Out of scope

- 5-mode rendering and McNemar's tests — issue 0027.
- Token-control padding strategy — issue 0027.
- 3-trial pre-check execution — issue 0027.

## Estimate

~5 hours over 2-3 calendar days. 80 cases × ~3-4 minutes each.

## Dependency

- Blocked by 0023 (need at-scale re-test outputs to draw ECS triples from).
- Blocks 0027 (Experiment 1 needs inspected ECS as inputs).

## Detail map

`REPAIR.md` §1 R7, §9C+§9D (Experiment 1 §3.3 / compliance checklist), §10C (json stub).
