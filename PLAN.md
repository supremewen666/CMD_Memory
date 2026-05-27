# CMD Case Pilot — Baseline Context Fix Plan

## Background

> **Revised after `data_validation.md` audit + pseudo-rescore probe (2026-05-27).**
> Earlier conversation cited "38% of records leak gold". That number used a
> raw-text-substring criterion and overcounts. The operative criterion is
> `required_phrases` membership (matches `evidence_recall_from_text`); under
> that, **non-reasoning unintended-leak rate is 4.5% by case (9/200)**, not
> 38%. The 50 `reasoning_error` cases are intentionally evidence-bearing;
> they do not "leak" — they need axis-aware scoring (Phase 3) to be usable.
>
> A pseudo-LLM rescore probe over 9 leak + 9 clean cases (haiku, planning-only)
> shows only **1/9 leak cases would actually score baseline=1.0** under
> SubagentScorer; 7/9 land at 0.5 because compression's 6-word fallback only
> preserves a numeric prefix like "21 days". So the rescore-collapse risk is
> ~0.5% of total cases, not 38%.

The original framing "all rows are 1.0" was specific to the cases the user
inspected (compression_error short-numeric and the relabel-orphaned 0029).
The real distribution is: ~50% reasoning-axis (intentional), ~4.5% partial
leak, ~95.5% clean. The fix priority below reflects this.

### Root cause (three independent failure modes)

| Mode | Cases affected | Root cause |
|---|---|---|
| **A. annotator/generator desync** | 29 longmemeval cases | `scripts/annotate_perturbation_labels.py:189` only rewrites `perturbation_label`; it does not regenerate `baseline_outputs` / `extracted_memory`. 29 cases were re-labeled to `injection_error` (a label the generator never produces) while keeping the compression-stage baseline body. |
| **B. compression short-answer leak** | 21 longmemeval + some memoryarena | `experiments/build_probecases.py:_compress_snippet` only compresses when (i) the gold matches a hardcoded city/tool replacement, or (ii) `len(words) > 6`. Short answers like `"Johnson"` fall through and return verbatim, so `injected_context = "Johnson"` ⊇ gold. |
| **C. reasoning_error axis mismatch** | 50 reasoning_error cases | `_build_baselines` intentionally sets `evidence_score=1.0` for reasoning_error (evidence IS present, the agent failed to *use* it). But `recovery_gain = replay.evidence_score − baseline_evidence_score_llm` is identically 0 in this regime, so attribution loses signal. The `evidence_given_reasoning` replay's contract is correct; the gain formula is wrong. |

### Pilot contract (chosen: option 2)

> baseline must reflect the failure context that the agent actually saw under
> the assigned `perturbation_label`. We do **not** mask gold from the baseline
> at scoring time (that would be unilateral baseline degradation). reasoning_error
> baselines legitimately contain evidence; recovery for that label is measured
> on the **answer axis**, not the evidence axis.

### attribution behavior under reasoning_error (chosen: fallback ②)

`evidence_given_reasoning` does NOT participate in cross-axis ranking. It is
evaluated as a fallback: when all other replays have `recovery_gain ≤ threshold`
but `evidence_given_reasoning.recovery_gain > threshold` on the answer axis,
attribution returns `reasoning_error`. This keeps every recovery_gain
comparison same-axis and gives a clean paper narrative — "for each failure
type, CMD measures recovery on that type's corresponding channel."

---

## Plan summary

| Phase | Scope | Status |
|---|---|---|
| 0 | static audit + pseudo-rescore probe | ✅ done 2026-05-27 |
| 3 (was C) | dual-axis recovery_gain + attribution fallback | ✅ done 2026-05-27 |
| 4 | new tests for dual-axis | ✅ done 2026-05-27 |
| 1 (was B) | compress / garble short-answer fallback | ✅ done 2026-05-27 |
| 2 (was A) | annotator regenerates case body on relabel | ✅ done 2026-05-27 |
| 5 | pilot rerun + GPU-server LLM verification | ⏳ deferred (user request) |

Final completion order on local: `0 → 3 → 4 → 1 → 2`. Phase 1 ran before
Phase 2 because Phase 2's regeneration uses the now-fixed
`_compress_snippet` / `_garble`.

---

## Phase 0 — Static audit + pseudo-rescore probe (DONE 2026-05-27)

Outputs:
- `data_validation.md` — full structural audit
- `artifacts/pseudo_rescore_probe/probe_input.json` — 18 sampled cases
- `artifacts/pseudo_rescore_probe/probe_output.json` — pseudo SubagentScorer scores
- `artifacts/pseudo_rescore_probe/probe_summary.md` — distribution + verdict

Key findings:

1. **Phrase-match leak audit (whole file)**:
   - 9 unintended-leak cases / 200 (4.5%); 8 compression + 1 injection
   - 50 reasoning_error cases leak by design (evidence-axis baseline=1.0)

2. **Pseudo-rescore probe (18 cases, haiku, planning-only)**:
   - leak group: 1/9 → baseline=1.0 (case 0005, "Arati Prabhakar"); 7/9 → 0.5
     (compression numeric prefix only); 1/9 → 0.2
   - clean group: 0/9 → baseline=1.0; 7/9 → 0.0
   - extrapolated upper bound on baseline=1.0: ~1–2 cases out of 200
     non-reasoning, ~0.5–1% of corpus

3. **Structural integrity issue (independent of leak)**:
   29 cases were relabeled compression→injection by deepseek annotator, but
   `extracted_memory[gold].text`, `gold_evidence` pointers, and
   `baseline_outputs[*].evidence_score` still match the compression
   contract. Phrase-leak is incidental; the real liability is that
   attribution's replay routing (`oracle_compression` vs `oracle_retrieval`
   vs the would-be injection oracle) sees compression-shaped fields under
   an injection_error label.

**Phase 0 verdict**: A+B fix small numbers; C is the only fix that
unlocks the 50 reasoning_error cases. Reorder confirmed.

The pseudo-probe is NOT the real evaluation. Real LLM rescore goes to the
GPU server in Phase 5; Phase 0 only sized the budget.

---

## Phase 1 — B: fix `_compress_snippet` short-answer fallback (30–45 min)

**File:** `experiments/build_probecases.py:606-630`

**Changes:**
1. Add `query: str` parameter to `_compress_snippet`.
2. Before the existing 6-word-fallback, add a short-answer branch:
   - Extract a head noun from the query using a small heuristic
     (`"last name"`, `"city"`, `"tool"`, `"flight number"`, `"country"`,
     `"name"`, `"date"`, `"price"`, fallback `"specific value"`).
   - Return `f"The {head_noun} was discussed but the specific value was abstracted."`
3. Update the call site in `_build_memory_items` to pass `query`.

**Verification:**
- One-liner: `python -c "from experiments.build_probecases import _compress_snippet; print(_compress_snippet('Johnson', 'What was my last name'))"`
- `pytest tests/ -v` — should stay green (no test currently asserts compress output).

**No LLM cost. No data regen yet — A will regen everything in Phase 2.**

---

## Phase 2 — A: annotator regenerates case body on relabel (1.5–2 h, includes LLM pass)

**Files:**
- `scripts/annotate_perturbation_labels.py` (primary)
- `experiments/build_probecases.py` (no change; `_build_one` already module-level)

**Changes:**
1. CLI: add `--cleaned-path` (default `data/cleaned_cases/cleaned_cases.json`).
2. Build `case_id → (idx, cleaned_case)` index. The current naming convention
   is `{source}-{idx:04d}` so `idx` is recoverable from `case_id`. Cross-check
   against `cleaned_cases.json` ordering used by `build_probecases.build_all`.
3. After `parse_label_response` returns a new label:
   - if `new_label == old_label`: keep row as-is.
   - if `new_label != old_label`: call `_build_one(idx, cleaned_case, new_label)`
     and replace the entire case dict in the output.
4. Reproducibility report: add `regenerated_count` line and a sample of 5
   regenerated case_ids.

**LLM pass:**
- Run on all 596 cases (or current researcher-adjudicated subset, depending
  on which corpus the pilot targets). Cost is small for deepseek.
- Re-check `compare_annotations` agreement against the existing labels — should
  approach ~100% because relabels now propagate consistently into the body.

**Verification:**
- `pytest tests/test_cmd_audit_decision34_deepseek_annotation.py -v` if present.
- Ad-hoc round-robin assertion script: every case's `injected_context` shape
  must match the generator's branch for its `perturbation_label`
  (e.g. `injection_error` → garbled string, `compression_error` → compressed
  text, `write_error` → "No relevant records found in memory.").
- Re-run the 38% leak audit; should drop near 0 (modulo intentional
  reasoning_error leakage handled by C).

**Outputs:**
- New `data/probe_cases/real_longmemeval_cases.json` (and `memoryarena`,
  `toolbench` if those were also run through annotator).
- New reproducibility report.

---

## Phase 3 — C: dual-axis recovery_gain + attribution fallback (2–3 h)

### 3.1 Schema (30 min)

**File:** `cmd_audit/harness.py`

- Add `baseline_answer_score_llm: float | None = None` to `AuditResult`.
- Rename `_score_baseline_evidence_with_agent` → `_score_baseline_with_agent`,
  return `tuple[float | None, float | None]` (evidence_llm, answer_llm).
- Use `AnswerVerifier` from `cmd_audit/llm_scoring.py` for the answer axis;
  evidence axis stays on `SubagentScorer`.
- Update `run_case` and `run_case_v1` to unpack both values and store both
  in `AuditResult`.

### 3.2 Dual-axis recovery_gain (30 min)

**File:** `cmd_audit/harness.py`

Rename `_with_llm_baseline_recovery_gain` → `_apply_dual_axis_recovery_gain`:

```python
def _apply_dual_axis_recovery_gain(
    replays: tuple[ReplayResult, ...],
    *,
    baseline_evidence_llm: float | None,
    baseline_answer_llm: float | None,
) -> tuple[ReplayResult, ...]:
    out = []
    for r in replays:
        if r.replay_name == "evidence_given_reasoning":
            ref, score = baseline_answer_llm, r.answer_score
        else:
            ref, score = baseline_evidence_llm, r.evidence_score
        if ref is None:
            out.append(r)
        else:
            out.append(replace(r, recovery_gain=score - ref))
    return tuple(out)
```

Update both callers (`run_case`, `run_case_v1`).

### 3.3 Attribution fallback (45 min)

**File:** `cmd_audit/attribution.py:assign_attribution_v1`

Change the "no positive recovery gain" branch to consult
`evidence_given_reasoning` first:

```python
ranked = sorted(replay_results, key=lambda r: r.recovery_gain, reverse=True)
top = ranked[0]

if top.recovery_gain <= positive_gain_threshold:
    egr = next(
        (r for r in replay_results if r.replay_name == "evidence_given_reasoning"),
        None,
    )
    if egr is not None and egr.recovery_gain > positive_gain_threshold:
        top = egr
        # fall through with top := egr; predicted_label resolves to reasoning_error
    else:
        raise ValueError("no replay produced a positive recovery gain")
```

**close_deltas semantics under fallback:** when the fallback path triggers,
`close_deltas` lists only `("reasoning_error", 0.0)`. This avoids comparing
answer-axis deltas against evidence-axis deltas in the close-margin tuple.
Document this in the docstring.

### 3.4 Test triage (1–1.5 h)

20 test files reference `recovery_gain` / `baseline_evidence_score_llm` /
`on_the_fly_baseline_rescore`. Triage:

**High risk (assertions must change):**
- `tests/test_cmd_audit_decision34_baseline_rescore.py` — directly tests the
  recovery_gain denominator. Extend to cover both axes.
- `tests/test_cmd_audit_issue11_v1_labels.py` — reasoning_error attribution
  path.
- `tests/test_cmd_audit_issue12_v1_labels.py` — V1 attribution ordering.

**Medium risk (schema/structure):**
- `tests/test_cmd_audit_issue3_attribution_table.py` — CSV writer; may need
  `baseline_answer_score_llm` column.
- `tests/test_cmd_audit_issue13_coupled_failure_and_memory_probe.py` —
  close_deltas behavior.
- `tests/test_cmd_audit_decision34_adapter_parity_llm.py` — adapter call path
  must propagate the new return tuple.

**Low risk (pass-through):**
- Remaining 14 files. Run, fix mechanically.

Strategy: implement 3.1–3.3, run `pytest tests/ -v`, fix in declared order.

---

## Phase 4 — New tests (45 min)

**New file:** `tests/test_cmd_audit_dual_axis_recovery_gain.py`

Minimum 4 tests:
1. `test_evidence_axis_replay_uses_evidence_baseline` — non-reasoning replay
   gain = `replay.evidence_score - baseline_evidence_llm`.
2. `test_reasoning_replay_uses_answer_baseline` — `evidence_given_reasoning`
   gain = `replay.answer_score - baseline_answer_llm`.
3. `test_attribution_fallback_triggers_when_only_reasoning_recovers` — all
   other replays at `recovery_gain ≤ 0`, EGR > threshold → predicted
   `reasoning_error`.
4. `test_attribution_fallback_skipped_when_top_replay_is_positive` — top
   replay positive → fallback never consulted, EGR ignored.

---

## Phase 5 — Pilot run + verification (1–2 h)

Command:

```bash
python -m cmd_audit run \
  --cases data/probe_cases/real_longmemeval_cases.json \
  --on-the-fly-baseline-rescore \
  --output artifacts/pilot_dual_axis/
```

**Acceptance criteria:**
- "all 1.0" rows in `attribution_table.csv` drop to <5%.
- reasoning_error cases now have positive recovery_gain on
  `evidence_given_reasoning` (sample-spot 5 cases manually).
- macro F1 between `predicted_label` and `perturbation_label` improves
  measurably vs. pre-fix baseline (record both numbers; this is the core
  pilot evidence).

If acceptance fails, return to Phase 0 hypothesis and re-audit.

---

## Files touched (summary)

| File | Phase | Reason |
|---|---|---|
| `experiments/build_probecases.py` | 1 | `_compress_snippet` short-answer branch |
| `scripts/annotate_perturbation_labels.py` | 2 | regen on relabel |
| `cmd_audit/harness.py` | 3.1, 3.2 | dual-axis baseline scoring + gain |
| `cmd_audit/attribution.py` | 3.3 | reasoning fallback |
| `data/probe_cases/real_*_cases.json` | 2 | regenerated by annotator |
| `tests/test_cmd_audit_dual_axis_recovery_gain.py` | 4 | new tests |
| `tests/test_cmd_audit_decision34_baseline_rescore.py` | 3.4 | extend |
| `tests/test_cmd_audit_issue11_v1_labels.py` | 3.4 | extend |
| `tests/test_cmd_audit_issue12_v1_labels.py` | 3.4 | extend |
| ~14 other test files | 3.4 | pass-through fixes after pytest run |

---

## Risk register

| Risk | Mitigation |
|---|---|
| Phase 2 LLM regen disagrees with researcher-adjudicated subset | Run annotator on the 130-case adjudicated subset first; only proceed to full 596 if agreement ≥ 95%. |
| Cross-axis recovery_gain ranking creates ambiguous attribution | Avoided by design: fallback ② keeps each ranking same-axis. |
| Existing 596-case attribution artifacts become invalid | Expected — they were mechanics-validation snapshots per CLAUDE.md note. Regen and re-cite. |
| close_deltas semantics under fallback confuses downstream consumers | Document in attribution docstring; add an assertion in the fallback branch that close_deltas tuple length ≤ 1. |
