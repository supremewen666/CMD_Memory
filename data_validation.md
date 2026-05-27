# Data Validation

Audit target: `data/probe_cases/real_longmemeval_cases.json` against the
CMD case-pilot experimental contract documented in `PLAN.md`.

Contract (option 2): every case's `primary_baseline.injected_context` must
reflect the failure mode implied by `perturbation_label`. No gold leakage
for non-reasoning labels. `reasoning_error` may legitimately contain
evidence (recovery is measured on the answer axis there).

## Dataset Identity

- dataset: `data/probe_cases/real_longmemeval_cases.json`
- source: built by `experiments/build_probecases.py:build_all` from
  `data/cleaned_cases/cleaned_cases.json`, then post-hoc relabeled by
  `scripts/annotate_perturbation_labels.py` using deepseek
- file size: 737,759 bytes; sha256 prefix `05efcde033c870f8`
- expected split: pilot uses the full file (200 cases, no train/val/test split);
  it is a probe corpus for diagnosis, not an ML training set
- git status: untracked at audit time (current working file)

## Reality Check

- files present: yes (200 cases, all 11 V1 labels representable but only 5
  appear in this file)
- real or mock: **synthetic/templated**. Generator constructs each case from
  a real LongMemEval source case (haystack_sessions are real text), then
  attaches a synthetic `gold_mem_id` item, synthetic `baseline_outputs`, and
  a label-aware `injected_context` template. This is mock-failure-mode data
  by design — the LongMemEval source provides the conversational scaffolding
  but all CMD-specific fields are templated.
- evidence: see `experiments/build_probecases.py:55-110` (`_build_one`),
  `:317-549` (`_build_baselines`), `:177-254` (`_build_memory_items`).
- mock disclosure: this is **not declared** anywhere in the case JSON itself.
  PLAN.md and CLAUDE.md both refer to these as the pilot corpus; the
  templated nature is implicit in the generator code, not flagged in
  artifacts. Recommend adding a `synthesis_provenance` field at corpus level
  before the paper cites it.

## Split Integrity

- train/val/test: not applicable — this is a probe corpus, all 200 cases are
  one diagnostic set
- leakage risk (train→test): not applicable
- leakage risk (gold→baseline): **the relevant risk for this contract**;
  audited below

## Label / Target Health

Label distribution in current file:

| perturbation_label | count |
|---|---:|
| retrieval_error | 50 |
| reasoning_error | 50 |
| premature_extraction_error | 50 |
| injection_error | 29 |
| compression_error | 21 |
| **total** | **200** |

Anomaly: `build_probecases.LABELS_BY_SOURCE["longmemeval"]` round-robins
over `(retrieval, compression, reasoning, premature_extraction)` — 4 labels.
The presence of 29 `injection_error` cases means
`scripts/annotate_perturbation_labels.py` rewrote 29 cases' labels from
`compression_error` → `injection_error` post-hoc. The annotator's scope is
just the `perturbation_label` field; it does **not** rebuild
`baseline_outputs` to match the new label.

This is the failure mode tracked as "A. annotator/generator desync" in
`PLAN.md`. Of the 29 relabeled cases, only 1 still has phrase-level leakage
(0005); the other 28 happen to look injection-shaped because
`_compress_snippet`'s output is plausibly garbled. This is luck, not
correctness — the underlying fields (`extracted_memory[gold].text`,
`baseline_outputs[*].evidence_score`, `gold_evidence` pointers) still match
the compression contract, not the injection contract.

## Preprocessing Check (gold→baseline leakage audit)

**Criterion**: `any(p.casefold() in injected_context.casefold() for p in
gold_evidence[*].required_phrases)`. This matches what `evidence_recall_from_text`
in `cmd_audit/scoring.py` checks. (Earlier in the conversation I quoted a
38% leak rate using a looser raw-text-substring criterion; that criterion
overcounts. The number below is the operative one.)

Audit over 400 (case × baseline_name) records:

| label | OK | LEAK | leak rate | notes |
|---|---:|---:|---:|---|
| retrieval_error | 100 | 0 | 0% | clean |
| premature_extraction_error | 100 | 0 | 0% | clean |
| injection_error | 56 | 2 | 3.4% | `_garble` weak on short proper nouns |
| compression_error | 26 | 16 | 38.1% | `_compress` 6-word fallback retains head/tail words |
| reasoning_error | 2 | 98 | 98% | **intentional** — evidence-axis baseline=1.0 by design |

Aggregate:

- total leaks: 116/400 = 29.0%
- of which intentional (reasoning_error): 98
- **unintended: 18 records / 9 unique case_ids = 4.5% of cases**

### Specific leak diagnoses

**case 0005 (injection_error)**:
```
gold_answer        = "Dr. Arati Prabhakar"
required_phrases   = ["Arati Prabhakar"]
mem-0005-gold.text = "Key fact: Dr. Arati Prabhakar"
_garble output     = "Arati Prabhakar ... Key fact: Dr"
                       ↑ phrase preserved verbatim
```
Root cause: `_garble` reverses sentence order with `[::-1]`. For mem text
of one sentence, it returns the input unchanged; for the two-fragment
"Key fact: Dr. Arati Prabhakar" it just swaps the two halves, leaving the
proper noun intact.

**case 0041 (compression_error)**:
```
gold_answer        = "7 days. 8 days (including the last day) is also acceptable."
required_phrases   = ["7 days", "8 days (including the last day) is also acceptable"]
_compress 6-word fallback output = "7 days. was discussed regarding also acceptable."
                                    ↑ phrase preserved verbatim
```
Root cause: `_compress_snippet`'s 6-word fallback formula
`words[:2] + " was discussed regarding " + words[-2:]` keeps the first two
words. `"7 days"` happens to be exactly the first two words.

### Random-sample structural check

Sampled `longmemeval-single-0051` (`premature_extraction_error`):

- `extracted_memory[gold].text` = `"Some information was discussed regarding the topic."` ✓ matches `_build_memory_items` line 239 (premature_extraction_error branch)
- `gold_evidence[0]` has `source_event_id` and no `source_memory_id` ✓ matches `_build_gold_evidence` lines 290-299
- `vector_memory.injected_context` = same abstract text ✓ matches `_build_baselines` lines 392-403
- `fixed_summary.injected_context` = `"An event was discussed."` ✓ matches lines 407-411
- raw_events contain a `evt-0051-gold` record carrying the real evidence text ✓ supports `verbatim_event_oracle` recovery

No phrase leakage in this case. **PASS for the random sample.**

## Verdict

**PASS (post-regeneration 2026-05-27)**

After PLAN.md Phase 1 (`_compress_snippet` / `_garble` fixes) and Phase 2
(case body regeneration via `scripts/regenerate_case_bodies.py`), the
post-regen audit shows:

| label | OK | LEAK | leak rate |
|---|---:|---:|---:|
| retrieval_error | 100 | 0 | 0% |
| premature_extraction_error | 100 | 0 | 0% |
| compression_error | 42 | 0 | 0% |
| injection_error | 58 | 0 | 0% |
| reasoning_error | 2 | 98 | 98% (intentional, axis-aware) |

- **non-reasoning unintended leak: 0 cases** (down from 9 / 4.5%)
- structural shape match (vector_memory.injected_context vs label
  branch): 200/200
- 853 unit tests / 2699 subtests pass green
- backup of pre-regen file at
  `data/probe_cases/real_longmemeval_cases.json.bak.pre_regen`

Reasoning_error's 98 records are unchanged: that label is by design
evidence-bearing on the evidence axis, and the Phase 3 dual-axis
recovery_gain (now landed) routes those cases through the answer axis.

### Pseudo-rescore probe (added 2026-05-27)

Ran a planning-only pseudo SubagentScorer (haiku, no GPU) over 9 leak + 9
clean cases. Artifacts in `artifacts/pseudo_rescore_probe/`. Results:

| group | n | evidence_score=1.0 | evidence_score=0.5 | evidence_score≤0.2 |
|---|---:|---:|---:|---:|
| leak | 9 | **1** (case 0005) | 7 | 1 |
| clean | 9 | 0 | 1 | 8 |

The 7 leak cases at 0.5 are all compression_error with numeric prefix
("21 days", "54 days") preserved by `_compress_snippet`'s 6-word fallback
— the 1st `required_phrase` matches, the 2nd does not. So the **upper
bound on cases that would actually rescore to baseline=1.0 is ~0.5–1% of
the corpus**, not 38%, not 4.5%. The 4.5% phrase-match audit overstates
the LLM-level leak.

Real evaluation must still run on the GPU server (Phase 5 in PLAN.md);
this pseudo-probe sized the budget, not validated the result.

### Priority rebalance for PLAN.md

- **Phase 3 (C dual-axis) is the largest blocker by case count** — 50
  reasoning_error cases need axis-aware scoring; no other fix unlocks them.
- **Phase 2 (A annotator regen) is the structural-integrity fix** — 29
  relabeled cases have compression-shaped `extracted_memory[gold]`,
  `gold_evidence` pointers, and `evidence_score=0.0` under an
  `injection_error` label. Attribution routing relies on these, not on
  phrase leakage.
- **Phase 1 (B compress/garble fallback) is the smallest, most precise
  fix** — 9 cases. Should run last among the three.

## Next Step

```
python -c "from experiments.build_probecases import _compress_snippet, _garble; \
           print(_compress_snippet('Johnson', 'What was my last name')); \
           print(_garble('Key fact: Dr. Arati Prabhakar'))"
```

reason: before any fix, capture current `_compress`/`_garble` outputs on
the two failing patterns (short proper noun, leading-2-word answer). Then
proceed with PLAN Phase 1 (fix `_compress_snippet`'s 6-word fallback to
not retain `words[:2]` when those words are an exact `required_phrases`
match) and a parallel `_garble` hardening (drop one fragment when the
input has ≤2 sentences). Re-run this audit after each fix and confirm
unintended leak count drops to 0.

## Open question for the GPU server

The phrase-match audit (this file) is a lower bound; the pseudo-rescore
probe (haiku, planning-only) gave an upper-bound *estimate* of ~0.5–1%
true LLM-level leak. The actual number is unknown until Phase 5 runs the
real SubagentScorer on the GPU server. Acceptance for the pilot:

- non-reasoning baseline_evidence_score_llm=1.0 rate < 2% of cases
- reasoning_error cases not counted (covered by Phase 3 dual-axis)

If the GPU run shows >2%, return to Phase 1 with a stricter
`_compress_snippet` short-numeric-prefix rule and re-audit.
