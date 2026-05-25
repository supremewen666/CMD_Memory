---
id: 0024
title: Researcher 130-case adjudication for Experiment 2 headline (LLM-A assisted)
status: needs-triage
labels: [paper, decision-34, dataset, researcher-task, llm-a]
blocks: [0026]
blocked_by: [0033]
created: 2026-05-24
updated: 2026-05-24 (Decision 34 R11 — LLM-A llama-3.3-70b-instruct + 20-case blind spot-check)
---

# 0024 — Researcher 130-case adjudication (LLM-A assisted)

## Why

Decision 34 R4+R11 binds the paper's headline attribution claim to a 130-case researcher-adjudicated subset, with LLM-A (`llama-3.3-70b-instruct`) providing candidate label suggestions to compress researcher time from ~10 hr to ~5 hr. A 20-case blind spot-check ensures researcher judgment isn't being anchored to LLM-A.

Three-independent-LLMs rule: the LLM-A used for adjudication suggestion must be family-disjoint from:
- deepseek-v4-pro-max (the upstream annotator we're checking against)
- qwen2.5-7b (the agent_generate model in issue 0023)
- the evaluator scorer (TBD specific model from issue 0026)

`llama-3.3-70b-instruct` satisfies all three — open weights for reproducibility, distinct family, ~$0.06 for 130 calls at Groq pricing.

## Acceptance criteria

| AC | Required behavior | Verified by |
|----|-------------------|-------------|
| AC1 | Stratified sample drawn from 596 cases (V1.0) / full corpus (V1.1, post-issue-0035): ~16 cases per active label across 8 labels (write/compression/premature_extraction/retrieval/injection/reasoning/route/ingestion) = 128 base + 2 spare slots = 130 target. | sampling script + persisted seed |
| AC2 | Sample seed `random_state=42`; sampled `case_id`s persisted in `data/probe_cases/researcher_labeled_subset.json` `cases[*].case_id`. `release_version` field tracks v1.0 / v1.1. | json round-trip check |
| AC3 | LLM-A (`llama-3.3-70b-instruct`) emits `(suggested_label, rationale)` per case before researcher review. LLM-A constraints: temperature=0, system prompt enumerates the 11 active labels with definitions paste-able from `CONTEXT.md`. | per-case `llm_a_suggestion` and `llm_a_rationale` populated |
| AC4 | Per case the researcher records: `(deepseek_label, llm_a_suggestion, llm_a_rationale, researcher_label, confidence, disagreement_with_deepseek, disagreement_with_llm_a, researcher_notes)`. `confidence ∈ {high, medium, low}`. | json schema validation |
| AC5 | Researcher notes are short (≤2 sentences) and reference observable artifacts: query, extracted_memory, baseline_outputs, gold_answer. Not derived from CMD's own output. | spot review |
| AC6 | **Blind spot-check (Decision 34 R11)**: First 20 cases (selected uniformly from the 130 with separate seed `random_state=43`) labeled BLIND — researcher sees no LLM-A suggestion. Same 20 cases re-labeled WITH LLM-A after the assisted pass. κ(researcher_blind, researcher_assisted) computed. If κ < 0.7, the entire pass is redone without LLM-A. | `artifacts/automation_bias_kappa.txt` with bootstrap CI |
| AC7 | Cohen's κ between researcher labels and deepseek labels computed on the 130-case overlap with bootstrap CI (1000-iter case-level resample); written to `artifacts/researcher_vs_deepseek_kappa.txt`. | one-line script using sklearn or manual κ formula |
| AC8 | Per-label distribution after labeling reported (counts + per-label confidence breakdown). Documents whether stratification held under researcher labels. | summary block in researcher_labeled_subset.json |
| AC9 | Researcher labeling protocol documented in the json file's top-level `annotators.researcher.protocol` field; reproducible by an independent researcher reading only that field. | self-check |
| AC10 | V1.1 trigger (issue 0035): re-sample 130 cases from full corpus pool, repeat full protocol (LLM-A + 20-case blind + κ). v1.0 subset archived to `data/probe_cases/v1_0_archive/researcher_labeled_subset.json`. | both versions on disk |

## LLM-A-assisted labeling protocol (Decision 34 R11)

```
Phase 1 — Sampling:
  1. Stratified sample 130 cases from 596 (V1.0) or full corpus (V1.1) with random_state=42.
  2. Within those 130, select 20 for blind spot-check with random_state=43.

Phase 2 — Blind labeling (20 cases):
  3. Researcher labels the 20 spot-check cases WITHOUT LLM-A. Records (researcher_blind_label, blind_confidence, blind_notes).

Phase 3 — LLM-A pass (all 130 cases including the 20):
  4. For each of the 130 cases, run llama-3.3-70b-instruct with prompt:
     [System: 11-label enumeration paste from CONTEXT.md]
     [User: Query / Gold answer / Extracted memory summary / Baseline retrieval / Baseline answer / Has ingestion trace]
     [Output: one label name + rationale (1-2 sentences)]
  5. Record (case_id, llm_a_suggestion, llm_a_rationale).

Phase 4 — Researcher review (all 130 cases):
  6. Researcher reads case + LLM-A suggestion + rationale.
  7. Researcher assigns final_label, confidence, records disagreements.

Phase 5 — Spot-check measurement:
  8. For the 20 spot-check cases, compare researcher_blind_label vs final researcher_label.
  9. Compute κ(blind, assisted) with bootstrap CI.
  10. If κ < 0.7, REDO Phase 4 without LLM-A for all 130 cases.

Phase 6 — Aggregation:
  11. Compute κ(researcher, deepseek) on all 130 cases with bootstrap CI.
  12. Write summary to data/probe_cases/researcher_labeled_subset.json.
```

## Labeling protocol (researcher reasoning)

For each case, read in order:
1. `query`
2. `gold_answer`
3. `extracted_memory` (note which item carries gold_evidence)
4. `baseline_outputs.vector_memory.retrieved_memory_ids` and `injected_context`
5. `baseline_outputs.vector_memory.answer` (the failed baseline answer)
6. (If LLM-A pass) — `llm_a_suggestion` + `llm_a_rationale`

Then assign:
- `write_error` if gold-evidence-bearing memory item is **absent** from `extracted_memory`.
- `compression_error` if memory item is present but evidence is lost or distorted in its text.
- `premature_extraction_error` if `raw_events` contain evidence but no `extracted_memory` item preserves it.
- `retrieval_error` if memory item is in `extracted_memory` but not in `retrieved_memory_ids`.
- `injection_error` if memory item is retrieved but `injected_context` is malformed/disordered.
- `reasoning_error` if injected context contains the evidence but baseline answer is still wrong.
- `route_error` (toolbench) if memory is in the wrong tier/store and baseline did not query it.
- `ingestion_error` (toolbench) if `has_ingestion_trace=false` and evidence never reached the agent.

Confidence:
- **high**: one label clearly applies, others clearly don't.
- **medium**: one label most likely, but a second label has weak support.
- **low**: ambiguous between 2+ labels with similar support; or unclear from the trace alone.

If LLM-A's suggestion clearly conflicts with the trace evidence, override and flag the disagreement. If LLM-A's suggestion looks right, the researcher still must verify against the trace itself — accepting LLM-A without verification is automation bias.

## Files affected

| File | Edit type |
|------|-----------|
| `data/probe_cases/researcher_labeled_subset.json` | populate `cases` array (stub from REPAIR §10A); `release_version` v1.0 / v1.1 |
| `scripts/sample_researcher_subset.py` | new; minimal sampling script |
| `scripts/run_llm_a_suggestions.py` | new; runs llama-3.3-70b-instruct on 130 cases |
| `artifacts/researcher_vs_deepseek_kappa.txt` | new; κ + bootstrap CI |
| `artifacts/automation_bias_kappa.txt` | new; κ(blind, assisted) on 20 spot-check cases |

## Out of scope

- ECS inspection — issue 0025 (different 80-case set, may overlap).
- Multi-rater κ — V2 community-resource.
- Headline number computation — issue 0026.

## Estimate

- V1.0: ~5 hours over 3-4 calendar days. (Reduced from 10 hr by LLM-A assist.)
  - Phase 2 blind labeling: ~1.5 hr (20 cases × ~5 min)
  - Phase 3 LLM-A pass: ~5 min (130 calls × ~1 sec at Groq)
  - Phase 4 researcher review: ~3.5 hr (130 cases × ~1.5 min with LLM-A suggestion)
  - Phase 5+6: ~30 min computation
- V1.1: ~5 hours again on re-sampled 130 from full corpus.

## Cost

- LLM-A: 130 calls × ~$0.0005 = ~$0.07 per pass. Negligible.

## Dependency

- Blocked by 0033 (deepseek labeling provenance — to compute disagreement_with_deepseek correctly).
- Blocks 0026 (Experiment 2 headline runs on this subset).

## Detail map

`REPAIR.md` §1 R4+R11, §10A (json stub), §8B (Experiment 2 §1.5 two-tier evaluation), §16 P11 (multi-evaluator robustness on headline).
