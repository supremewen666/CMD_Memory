---
id: 0033
title: deepseek labeling provenance recovery (R4-prov)
status: provenance-recovered-api-run-pending
labels: [paper, decision-34, dataset, provenance, researcher-task]
blocks: [0023, 0024]
blocked_by: []
created: 2026-05-24
updated: 2026-05-25
---

# 0033 — deepseek labeling provenance recovery

## Implementation note (2026-05-25)

The reconstructed annotator script is checked in with the exact prompt template,
DeepSeek endpoint/API-key wiring, cleaned-case dry-run support, and comparison
report tooling. `cleaning_report.txt`, `researcher_labeled_subset.json`, and
`artifacts/deepseek_label_reproducibility.txt` now reference the reconstructed
provenance.

Full 596-case API rerun is not recorded yet because this environment had no
`DEEPSEEK_API_KEY` / `LLM_API_KEY`. The report is intentionally marked
`not_executed_missing_api_key`; do not cite the 596-case scale sanity number
until the report is replaced by a completed rerun with measured agreement.

## Why

The 596 `perturbation_label`s in `data/probe_cases/real_*_cases.json` were assigned by deepseek-v4-pro-max during dataset construction. Neither the prompt template nor the run script is currently in the repository. Without it:

- The 596-case scale sanity check (paper supplementary) is irreproducible — a reviewer cannot verify how labels were derived.
- Issue 0023's "CMD reproduces deepseek labels at Macro F1 = Y" claim has no traceable annotator definition.
- Issue 0024's `disagreement_with_deepseek` field can be recorded but cannot be characterized in the methods section ("deepseek was prompted with X; researcher saw Y; the disagreement reflects Z").

This is a researcher recovery task. If the original prompt + script are gone, the recovery is to reconstruct them by feeding cleaned cases through deepseek-v4-pro-max with a re-derived prompt and verifying ≥95% agreement with the existing labels.

## Acceptance criteria

| AC | Required behavior | Verified by |
|----|-------------------|-------------|
| AC1 | `scripts/annotate_perturbation_labels.py` exists in the repo. Script is runnable: takes `data/cleaned_cases/cleaned_cases.json` (or equivalent input), produces a JSON array of `{case_id, perturbation_label}` records by calling deepseek-v4-pro-max via API. | `python scripts/annotate_perturbation_labels.py --help` works |
| AC2 | The script's docstring or top-of-file comment includes the **exact** prompt template (system prompt + user prompt) used. The 11 active labels and their definitions appear in the prompt, paste-able from `CONTEXT.md` § "Label Taxonomy". | inspection |
| AC3 | Reproducibility check: re-running the script against `cleaned_cases.json` reproduces the labels in `data/probe_cases/real_*_cases.json` to ≥95% agreement. Any drift is documented with possible causes (provider non-determinism, prompt edits, dataset changes). | comparison report `artifacts/deepseek_label_reproducibility.txt` |
| AC4 | Run metadata persisted: deepseek API endpoint, model+version string, temperature, top-p, total annotation calls (596 expected), wall-clock, $ cost. Written to `data/cleaned_cases/cleaning_report.txt` § "Annotation Provenance" (the stub for this section is in REPAIR §10D, paste-ready). | text artifact updated |
| AC5 | If the original prompt cannot be recovered, the reconstruction is documented honestly: "Original deepseek labeling script was not preserved. The labels were re-derivable by feeding `(query, extracted_memory_summary, vector_memory baseline output, gold_answer)` through deepseek-v4-pro-max with the prompt below; reproducibility against the existing labels is K%." This is a reviewer-defensible position even with imperfect agreement. | honesty check in report |
| AC6 | The script's prompt is referenced from `data/probe_cases/researcher_labeled_subset.json` under `annotators.deepseek.script` and the researcher protocol remains under `annotators.researcher.protocol`, so issue 0024 can characterize both annotators. | json fields reference the script path and protocol |

## Recovery steps for the researcher

1. **Search for the original.** Likely locations: chat history with whoever ran the labeling, jupyter notebooks, the `scripts/` directory in any branches, the dataset construction PR. If found, paste/check in.

2. **If not found, reconstruct.** Write a deepseek-v4-pro-max prompt that, given a cleaned case, returns one of the 11 labels:

   ```
   System: You are a memory pipeline failure analyst. Given a failed
   memory-augmented agent case, classify the failure into exactly one of
   these 11 operation labels: {paste from CONTEXT.md § Label Taxonomy}.
   
   User:
   Query: {case.query}
   Gold answer: {case.gold_answer}
   Extracted memory: {summary of case.extracted_memory}
   Baseline retrieval: {case.baseline_outputs.vector_memory.retrieved_memory_ids}
   Baseline answer: {case.baseline_outputs.vector_memory.answer}
   Has ingestion trace: {case.has_ingestion_trace}
   
   Output: one label name from the list. No explanation.
   ```

3. **Run on 50-case sample first.** Verify 50/50 reproduction matches existing labels. If <90%, refine prompt; if >95%, full 596 run is justified.

4. **Persist metadata** to `data/cleaned_cases/cleaning_report.txt` § "Annotation Provenance" using the template in REPAIR §10D.

## Files affected

| File | Edit type |
|------|-----------|
| `scripts/annotate_perturbation_labels.py` | new |
| `data/cleaned_cases/cleaning_report.txt` | append "Annotation Provenance" section per REPAIR §10D |
| `artifacts/deepseek_label_reproducibility.txt` | new |
| `data/probe_cases/researcher_labeled_subset.json` | reference the script in `annotators.deepseek.script` |

## Out of scope

- Re-labeling all 596 cases — this issue is provenance recovery, not re-annotation. Existing labels stay unless reproducibility is <80% in which case escalate.
- Multi-LLM annotator ensemble — V2 community-resource scope.

## Estimate

- Best case (script found): 30 minutes (check in, document).
- Reconstruction case: 2-4 hours (write prompt, run on sample, verify, full run if needed). $ cost: ~$10 if full re-annotation needed at deepseek pricing.

## Dependency

- No blockers — independent of code work.
- Blocks 0023 (the at-scale re-test references deepseek labels for the scale sanity check; must be reproducible before paper).
- Blocks 0024 (the researcher 130-case adjudication records `disagreement_with_deepseek` which requires a stable deepseek annotator definition).

## Detail map

`REPAIR.md` §1 R4-prov, §10B (recovery target description), §10D (cleaning_report annotation block paste-ready).
