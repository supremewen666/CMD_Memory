---
id: 0031
title: Artifact regeneration matrix + archive + manifest
status: archive-complete
labels: [paper, decision-34, artifacts, hygiene]
blocks: [0023]
blocked_by: [0022]
created: 2026-05-24
archive_completed: 2026-05-25
---

# 0031 — Artifact regeneration matrix

## Archive completion note (2026-05-25)

Pre-Decision-34 artifacts have been archived under
`artifacts/legacy_phrase_match_2026_05_22/` with `MANIFEST.txt`; the dropped
`sandbox/recurrence_comparison.csv` lives under `dropped/` with a note. New
LLM-stack artifacts and `artifacts/MANIFEST.txt` remain gated on issue 0023's
at-scale run.

## Why

Per Branch P (your three accepts during grilling), all 11 artifact types currently in `artifacts/` and `artifacts/sandbox/` were produced under the pre-Decision-34 stack (phrase-match shortcut, `tie_margin = 0.05`, label-leaked replay context, no `agent_generate`, deepseek-labeled cases). They must be regenerated under the LLM stack on the 596 cases, with per-source split preserved, and the legacy artifacts archived for diff/comparison so any LLM-stack regression below the legacy lower bound is detectable and audit-traceable.

This issue is the choreography for the artifact swap. It defines what gets archived, what gets regenerated, where the new artifacts live, and what each MANIFEST.txt records.

## Acceptance criteria

| AC | Required behavior | Verified by |
|----|-------------------|-------------|
| AC1 | Before issue 0023's at-scale re-test runs, all current artifacts under `artifacts/` and `artifacts/sandbox/` are moved to `artifacts/legacy_phrase_match_2026_05_22/` (preserving subdirectory structure for sandbox files: `legacy_phrase_match_2026_05_22/sandbox/`). | directory listing |
| AC2 | `artifacts/legacy_phrase_match_2026_05_22/MANIFEST.txt` written, recording: scoring stack (`phrase_match + agent_generate=None`), `tie_margin=0.05`, dataset version (596 cases as of 2026-05-22), git commit SHA at archive time, one-line per-file role description. | manifest readable, complete file enumeration |
| AC3 | After issue 0023 runs, the new LLM-stack artifacts populate `artifacts/` and `artifacts/sandbox/` with the per-source split preserved (LongMemEval / MemoryArena / ToolBench). | 11-type × 3-source = 33 files plus aggregates |
| AC4 | `artifacts/MANIFEST.txt` written for the new artifacts, recording: scoring stack (qwen2.5-7b agent + evaluator-TBD scorer + `on_the_fly_baseline_rescore=True` + `tie_margin=0.0` + label-stripped replay context), dataset version, git commit SHA, evaluator model+version, one-line per-file role. | manifest readable |
| AC5 | Three semantic-shift annotations in MANIFEST: (a) `attribution_confusion_matrix.csv` cell values move under `tie_margin=0.0`; (b) `repair_label_summary.csv` recovered/partial split shifts under `AnswerVerifier`; (c) `recurrence_comparison.csv` is **dropped** (not regenerated) — collapsed into Experiment 1 outputs per Decision 34 R8/Q11. The dropped file lives only in `legacy_phrase_match_2026_05_22/dropped/` with a one-line note. | both manifests carry the annotations |
| AC6 | Per-source split preserved for the 11 artifact types: each type has 4 variants — aggregate (`*.csv`) + per-source (`*_longmemeval.csv`, `*_memoryarena.csv`, `*_toolbench.csv`). | listing matches the existing pattern from `artifacts/sandbox/post_repair_table*` |
| AC7 | The 130-case adjudicated subset's artifacts (Experiment 2 headline outputs from issue 0026) live under `artifacts/headline_130/` with their own MANIFEST.txt referencing the researcher subset version. | separate directory + manifest |
| AC8 | Archive operation is a single-commit move (no overwrite of legacy data). git history preserves the swap. | git log on artifacts/ |

## Artifact regeneration matrix

| Type | Aggregate file | Per-source variants | Regen scope | Notes |
|------|----------------|---------------------|-------------|-------|
| Attribution table | `attribution_table.csv` | longmemeval / memoryarena / toolbench | full 596 + 130 headline | columns added: `attribution_failed`, `failure_reason`, `baseline_evidence_score_llm` |
| Confusion matrix | `attribution_confusion_matrix.csv` | longmemeval / memoryarena / toolbench | full 596 + 130 headline | cell values move under tie_margin=0.0 |
| Comparison metrics | `comparison_metrics.csv` | longmemeval / memoryarena / toolbench | full 596 + 130 headline | 4 baselines: random / evidence_recall / subagent_judge / llm_judge |
| Post-repair table | `sandbox/post_repair_table.csv` | longmemeval / memoryarena / toolbench | full 596 + 130 headline | recovered/partial split shifts under AnswerVerifier; columns added for partial threshold τ |
| Repair success | `sandbox/repair_success_table.csv` | longmemeval / memoryarena | full 596 | targeted vs hard-case comparison |
| Repair label summary | `sandbox/repair_label_summary.csv` | — | full 596 | per-label recovery distribution |
| Repair claim ledger | `sandbox/repair_claim_ledger.txt` | — | full 596 | text aggregate |
| mem0 adapter parity | `mem0_adapter_parity.csv` | — | smoke + full 596 | new column `parity_under_llm_stack` per issue 0032 |
| Letta adapter parity | `letta_adapter_parity.csv` (if exists) | — | smoke + full 596 | same as above |
| Hook efficacy (new) | `hook_efficacy_supplementary.csv` | — | full 596 | issue 0028 |
| At-scale raw | `at_scale_llm_retest.csv` | — | full 596 | 5960 rows of (case, replay, recovery_gain, ...) — input to 0026/0028/0029 |
| ~~Recurrence comparison~~ | ~~`sandbox/recurrence_comparison.csv`~~ | — | **DROPPED** (Q11) | Experiment 1 supersedes; archived under `dropped/` with note |
| Hook calibration training | `hook_calibration/training_set_llm.npz` | — | full 596 | issue 0028 |
| V0V1 gate status | `V0V1_gate_review.txt` / `.txt` | — | regenerated under LLM stack | reflects new headline number |
| Manifest writer | `scripts/write_llm_artifact_manifest.py` | — | post-run metadata | writes post-D34 `artifacts/MANIFEST.txt`; no artifact regeneration |

## Out of scope

- Code changes — the regeneration is a re-run of issue 0023 + downstream issues, not new code.
- Live mem0/Letta integration — V2.

## Estimate

- Archive move: 30 minutes.
- MANIFEST writing: 1 hour.
- Regenerated artifacts produced as byproducts of issues 0023, 0026, 0027, 0028, 0029.

## Dependency

- Blocked by 0022 (LLM stack must exist before regeneration).
- Blocks 0023 (archive must complete before re-test writes new artifacts to the same paths).

## Detail map

`REPAIR.md` §10D (cleaning_report annotation), §11 (code edits that produce new artifacts), §15 (Artifact regeneration matrix).
