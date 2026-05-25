# REPAIR.md — Post-Grilling Documentation & Code Edit Plan

**Source**: 2026-05-23/24 grilling sessions Q1-Q23, recorded in conversation "discussion 3".
**Purpose**: Single source of truth for the doc + code changes following the grilling. Every edit is paste-ready or has explicit before/after blocks the user runs by hand.
**Scope**: 11 files to edit, 4 new files to create, 6 code edits deferred to wiring sprint, 13 issue files (0022-0034), plus held material (§15-§19) integrated 2026-05-24.

Do NOT execute these edits while reading. Read the whole file first; the resolutions reference each other.

---

## 0. Resolutions consolidated (Q1-Q23 → R1-R11)

The 23 grilling questions collapse into 11 binding resolutions (R1-R8 from session 1; R9-R11 from session 2 covering Branch P/D/S).

| ID | Resolution | Source Q | File touchpoints |
|----|-----------|----------|------------------|
| R1 | At-scale Macro F1 = 1.000 is a phrase-match artifact, not a paper claim. Re-test required with `agent_generate` + independent LLM scorer + on-the-fly LLM baseline rescore. Drop label leakage from replay context. | Q1 Q2 Q3 Q5 | gates/V0V1, gates/V1V2, limitations, current-memory, replays.py |
| R2 | Wire `agent_generate` + `AnswerVerifier` into Post-Repair Context Replay. Calibrate "partial" threshold τ empirically; default τ=0.5 for headline. | Q6 | limitations, current-memory, post_repair.py |
| R3 | `tie_margin = 0.05` dropped to 0.0 for headline argmax; calibrate empirically post-hoc on 30-50 manually-inspected near-tie cases for a separate coupled-failure report. | Q4 | experiment_02, current-memory, attribution.py (no edit, doc only) |
| R4 | Headline evaluation = researcher-adjudicated 130-case subset (~16 × 8 active labels). 596-case LLM-annotated suite reframed as scale sanity check ("CMD reproduces deepseek-v4-pro-max labels at Macro F1 = Y"). LLM-A (`llama-3.3-70b-instruct`) assists adjudication; 20-case blind spot-check protocol enforces automation-bias ceiling. Recover and check in deepseek labeling prompt + script. | Q7 Q8 + (Q24 LLM-A assist) | experiment_02, gates/V0V1, gates/V1V2, current-memory, TASK, scripts/, data/cleaned_cases/ |
| R5 | Hook (Decision 33 / issue 0021) demoted to supplementary. Experiment 2 bypasses hook (`run_case_full_v1`, all 10 replays). Hook calibration runs *after* re-test, consumes re-test outputs as free training labels. Hook efficacy = one supplementary table. | Branch X | TASK, current-memory, cmd_progress_report, scripts/calibrate_hook.py |
| R6 | CMD vs Rewind head-to-head dropped. Replaced with layered-stack positioning section in related work (Runtime ↔ Memory pipeline ↔ Item content). 5-dim differentiation table reframed from "Rewind vs CMD" to "Runtime debugger vs Memory debugger." Decision 30 gets an addendum. | Q9 | cmd_open_decisions (Decision 30 addendum), TASK |
| R7 | Experiment 1 hardened: 80 cases (20 × 4 labels), 5 modes (add `corrected_only_padded` token-control), 3-trial `none`-mode pre-check (lenient exclusion), manual ECS inspection of 80 records before mode rendering. | Q10 | experiment_01, TASK |
| R8 | Paper claim binding sub-resolutions: Q11 standalone Failure Memory recurrence collapsed into Experiment 1 (recurrence_comparison.csv dropped); Q12 repair depth = "Level 2 capability is a design property of CMD's RepairAction emission" (verified by code, not aggregate stats); Q13 AttributionFailed reported as principled abstention (two-tier headline coverage% + Macro F1 on attributed); 8-label headline + 11-label supplementary completeness note. | Q11 Q12 Q13 | experiment_02, limitations §9 |
| R9 | All 11 artifact types regenerated under LLM stack on full 596 cases (V1.0); per-source split preserved (LongMemEval/MemoryArena/ToolBench); pre-D34 artifacts archived to `artifacts/legacy_phrase_match_2026_05_22/` with MANIFEST.txt; new artifacts get `artifacts/MANIFEST.txt`. Three semantic shifts annotated: confusion matrix cell movement, repair_label_summary recovered/partial split, recurrence_comparison drop. | Branch P + Q15 | §15 |
| R10 | V1.0 arxiv preprint at 06-10 binds to 596-derived 130-case headline; V1.1 venue submission binds to full-corpus 130-case headline post-issue-0035. Cross-dataset generalization claim version-gated: V1.0 = coverage claim only; V1.1 = explicit generalization claim. ~9/11 online deployment claim retained with retention% backing from surrogate-gap rerun. Two-evaluator robustness on 130-case headline only. Bootstrap CIs on Macro F1 + top-2 + per-label + κ. Cost/latency in headline table column. | Branch S Q18-Q23 | §16 paper-craft, §18 dual-run |
| R11 | LLM-assisted adjudication for 130-case headline uses `llama-3.3-70b-instruct` as candidate generator (LLM-A) — different family from deepseek annotator, qwen agent, evaluator scorer. 20-case blind spot-check measures automation-bias-free judgment via κ(researcher_blind, researcher_assisted). | Q24 (Branch D follow-up) | experiment_02, 0024 |

The new sequencing every doc must reflect:

```
05-25~28  Wire LLM eval infra (R1, R2, R3-headline, R5-bypass)
05-28~30  At-scale LLM re-test on 596 cases (V1.0; all 10 replays, no hook)
05-30     Free hook calibration from re-test outputs (R5 supplementary, half-day)
05-30~01  Researcher adjudicates 130 cases with LLM-A + spot-check (R4+R11, ~5 hr)
06-01~03  Researcher inspects 80 ECS records for Experiment 1 (R7, ~5 hr)
06-03     Experiment 2 V1.0 on 130-case adjudicated set, no hook (headline #1)
06-04     Hook efficacy supplementary table (R5)
06-04     Surrogate-gap LLM-stack rerun on 50-case hold-out (R10/Q18, supplementary)
06-06     Experiment 1 V1.0 + coupled-failure subset report
06-07     Decision 30 addendum + layered positioning section (R6, ~2 hr)
06-08~10  V1.0 arxiv preprint draft consolidation
post-corpus  V1.1 venue submission re-runs (issue 0035 trigger)
```

---

## 1. `cmd_innovation_core/plans/cmd_open_decisions.md` — APPEND new Decision 34 + addendum to Decision 30

### 1A. Append after Decision 33

```markdown
## Decision 34: Paper Claim Integrity — At-scale Re-test, Headline Eval Set, Hook Demotion, Rewind Reframe, Branch P/D/S Resolutions (2026-05-23/24, RESOLVED)

Bundled resolution from 2026-05-23/24 grilling sessions ("discussion 3"). Eleven binding decisions (R1-R11) covering paper claim integrity ahead of 2026-06-10 V1.0 arxiv preprint and post-corpus V1.1 venue submission. Supersedes prescribed details in Decision 33 step 1 (training-label scorer source) and Decision 30 (Rewind head-to-head).

**Resolution**:

1. **R1 — At-scale Macro F1 = 1.000 is a phrase-match artifact, not a paper claim**. The 596-case at-scale result (`V0V1_gate_status.md` 2026-05-22) was produced under `replays.py:477` shortcut (`answer = case.gold_answer if evidence_score == 1.0 else ""`, `agent_generate=None`), so `recovery_gain ∈ {0.0, 1.0}` and the perfect identity matrix is mechanical. Re-test required on the same 596 cases under: (a) `agent_generate` = qwen2.5-7b ollama producing replay answers from `baseline + evidence_block` (label string dropped per R1 point 5); (b) evidence scorer = an LLM independent of the agent model; (c) on-the-fly LLM rescoring of `vector_memory` baseline so `recovery_gain = evidence_score_replay_llm - evidence_score_baseline_llm` is parity-scored. Pre-baked `baseline.evidence_score` preserved for backward compat.

   **R1 point 5**: `_build_replay_agent_context` drops `CMD ATTRIBUTION LABEL` line. Label is the *output* of attribution, not an input.

2. **R2 — Post-Repair Context Replay must run the agent**. `run_post_repair_context_replay` currently does substring matching (`post_repair.py:227-230`). Wire `agent_generate(query, repaired_context)` so the agent answers, then score the answer (not the context). Wire `AnswerVerifier` (issue 0019 Phase B, "Decision B 待接入") into the `recovered` decision: `recovered ⇔ AnswerVerifier == EQUIVALENT`. Default partial threshold τ=0.5 for headline; calibrate post-hoc on 30-50 manually-inspected cases. `AnswerVerifier` runs on the independent evaluator (≠ agent model).

3. **R3 — Headline argmax tie_margin = 0.0**. Zero free parameters in the decision rule. Per-case `recovery_gain` distributions logged. Coupled-failure becomes a separate post-hoc subset report on 30-50 near-tie cases manually inspected, calibrated for coupled recall ≥ 80%.

4. **R4 — Researcher-adjudicated 130-case headline + LLM-A assist**. 596 deepseek labels are LLM-annotated, so LLM-vs-LLM agreement is circular. Researcher hand-labels 130 stratified cases (~16 × 8 active labels) with LLM-A (`llama-3.3-70b-instruct`) candidate suggestion + accept/reject. 20-case blind spot-check measures automation bias via κ(researcher_blind, researcher_assisted). High+medium confidence subset = headline; low → appendix. Headline claim binds to adjudicated set: "CMD Macro F1 = X on N high+medium adjudicated cases vs LLM-as-judge = Y vs evidence_recall = Z." Supplementary: "CMD reproduces deepseek labels at Macro F1 = W across 596." Cohen's κ between researcher and deepseek reported as methods artifact. **R4-prov**: deepseek labeling prompt + script must be checked into `scripts/annotate_perturbation_labels.py`.

5. **R5 — Hook → supplementary**. Decision 33's two-stage `empty_ctx + RPE Judge top-3` design is implemented (issue 0021) and stays. But: (a) Experiment 2 bypasses hook (`run_case_full_v1`, no top-k); headline independent of hook quality. (b) `scripts/calibrate_hook.py` refactored to consume at-scale re-test outputs as training labels — labels free byproduct under exactly the scorer paper headline uses. (c) Hook efficacy = one supplementary table. Decision 33's "SubagentScorer (qwen) for hook training labels" overridden by R5 (use whatever scorer headline eval uses).

6. **R6 — CMD vs Rewind head-to-head dropped**. Different layers (runtime vs memory pipeline), different input modalities. Replaced with layered-stack positioning + reframed 5-dim table ("Runtime debugger vs Memory debugger") + 3-4 boundary examples in related work. ~2 hr writing. Decision 30 receives addendum.

7. **R7 — Experiment 1 hardened**. 80 cases (20 × 4 labels). Add `corrected_only_padded` 5th mode (token-controlled). 3-trial `none`-mode pre-check (lenient: exclude if ≥1 of 3 trials produces correct answer). Researcher manually inspects/edits 80 ECS records before mode rendering. Document inspection in methods. ~400 LLM calls + 240 pre-check + 400 secondary judge ≈ 1040. <$3 at evaluator price. 5-mode ablation (cause-only / wrong_memory-only) deferred to V2 if positive.

8. **R8 — Paper claim binding sub-resolutions**:
   - **Q11**: standalone Failure Memory recurrence comparison (3-case smoke `recurrence_comparison.csv`) **dropped**. Decision 19 paper claim #1's "store→reuse" arrow satisfied by Experiment 1's `none` vs `corrected_only` McNemar. FM-retrieval-recall as one supplementary paragraph.
   - **Q12**: Repair depth = design claim, not aggregate statistic. "Level 2 capability is a design property of CMD's RepairAction emission" (`repairs.py:563-672` shows LLM tool emits `target_item_id`); paper shows representative `(case → action)` traces. V2 cascade pre-burial via `cascade_candidates` (currently always `()` in V1).
   - **Q13**: AttributionFailed cases reported as principled abstention (conformal-prediction terminology). Two-tier headline: coverage% + Macro F1 on attributed cases. 8-label headline + 11-label supplementary architecture-completeness note (synthetic granularity/graph/safety probe cases acknowledged but not quantitatively claimed).

9. **R9 — Artifact regeneration matrix**. All 11 artifact types regenerated under LLM stack on 596 cases (V1.0); per-source split preserved (LongMemEval/MemoryArena/ToolBench); pre-D34 artifacts archived to `artifacts/legacy_phrase_match_2026_05_22/` with MANIFEST.txt; new artifacts get `artifacts/MANIFEST.txt`. Three semantic shifts annotated. `recurrence_comparison.csv` dropped (Q11). Re-runs again under V1.1 when corpus migrates (issue 0035).

10. **R10 — V1.0/V1.1 dual-release pattern + paper-craft framing**:
    - V1.0 arxiv preprint at 06-10 binds to 596-derived 130-case headline.
    - V1.1 venue submission binds to full-corpus 130-case headline post-issue-0035.
    - Cross-dataset generalization claim: V1.0 = coverage only; V1.1 = explicit generalization (post-corpus N supports it).
    - Online ~9/11 deployment claim retained with retention% backing from surrogate-gap rerun under LLM stack on 50-case hold-out (issue 0036).
    - Two-evaluator robustness on 130-case headline only.
    - Bootstrap CIs (1000-iter case-level resample) on Macro F1 + top-2 + per-label F1 + per-baseline numbers + κ.
    - Cost/latency reported as headline column (tokens / wallclock_sec / usd per case, agent + scorer + verifier subtotals).

11. **R11 — LLM-A for 130-case adjudication = `llama-3.3-70b-instruct`**. Different family from deepseek-v4-pro-max (annotator), qwen2.5-7b (agent), evaluator scorer. Open weights → fully reproducible. ~$0.06 for 130 calls at Groq/Together pricing. Researcher reads (case, candidate label, rationale), accepts/rejects/replaces, assigns confidence. **Automation-bias countermeasure**: 20-case blind spot-check labeled before LLM-A pass, then re-labeled with LLM-A; κ(researcher_blind, researcher_assisted) reported. If κ < 0.7, researcher is anchoring; redo without LLM-A.

**Why bundled, not 11 separate decisions**: All eleven came from the same grilling thread, and they reference each other. Bundling matches Decision 32/33 style.

**Sequencing impact**: TASK.md updated; see TASK edit in REPAIR.md §4.

**Source**: 2026-05-23/24 grilling-with-docs sessions "discussion 3". Full integration in REPAIR.md at repo root.
```

### 1B. Append to Decision 30

After the existing line ending with `repair depth metric`, append:

```markdown

**2026-05-23 addendum (per Decision 34 R6)**: Head-to-head benchmark dropped. Rewind and CMD operate on different layers (runtime vs memory pipeline) and don't share an input modality; running both on the same cases produces meaningless metrics. Replaced with layered-stack positioning + qualitative boundary examples in related work (Runtime: Rewind/Culpa/TraceForge ↔ Memory pipeline: CMD ↔ Item content: MemRepair/MemLineage). Decision 30's depth differentiation claim is preserved as a related-work section, not as quantitative evidence. The 5-dimension table is reframed from "Rewind vs CMD" to "Runtime debugger vs Memory debugger." Paper requirement "Head-to-head: CMD operation-level repair vs Rewind-style step-level retry on the same failure cases" is removed. See Decision 34 for the full grilling resolution.
```

---

## 2. `cmd_innovation_core/gates/V0V1_gate_status.md` — Reframe 596 result as sanity check

### 2A. Insert new caveat block immediately AFTER `## At-Scale Validation (2026-05-22)` and BEFORE existing line `All four criteria re-validated against the full 596-case`:

```markdown
> **⚠️ 2026-05-23 grilling caveat (Decision 34 R1+R4)**: The Macro F1 = 1.000 reported below was produced under the phrase-match shortcut in `replays.py:477` (`agent_generate=None` → `answer = case.gold_answer if evidence_score == 1.0 else ""`), so `recovery_gain ∈ {0.0, 1.0}` is mechanical, not diagnostic. Additionally, the 596 `perturbation_label`s were assigned by deepseek-v4-pro-max (LLM annotator), making this an LLM-vs-LLM agreement check, not ground-truth attribution accuracy.
>
> **This block is preserved as a snapshot of the 2026-05-22 mechanics-validation run, not as a paper-grade attribution result.**
>
> Paper-grade evaluation per Decision 34 (post-2026-05-23 grilling):
> 1. **Headline Experiment 2 (target 06-03 V1.0; post-corpus V1.1)**: 130 researcher-adjudicated cases (~16 × 8 active labels) with LLM-A (`llama-3.3-70b-instruct`) candidate suggestion + accept/reject + 20-case blind spot-check. Agent_generate = qwen2.5-7b. Evidence scorer = independent LLM (≠ qwen, ≠ llama-A). `tie_margin = 0.0` for argmax. Label string dropped from replay context. Bootstrap CI (1000-iter) on Macro F1 + top-2 + per-label F1.
> 2. **Scale sanity check**: this 596-case suite re-run under same scorer/agent stack, framed as "CMD reproduces deepseek-v4-pro-max labels at Macro F1 = Y." Cohen's κ between deepseek and researcher labels reported as methods-section artifact.
> 3. **Repair gate**: Post-Repair Context Replay rerun with `agent_generate` actually answering the query and `AnswerVerifier` driving recovered/partial decision (currently substring matching — `post_repair.py:227-230`).
>
> Pending re-test scheduled 05-28~30 (V1.0). Until then, the numbers below are not citable in the paper.
```

### 2B. After the existing "Comparison to Smoke" subsection, append:

```markdown
## 2026-05-23/24 Re-test Plan (post-grilling, Decision 34)

V1.0 (596-case dataset state, target 06-10 arxiv preprint) and V1.1 (full-corpus dataset state, post-issue-0035, target venue submission) are tracked separately.

| Step | What | Owner | V1.0 Target | V1.1 Trigger |
|------|------|-------|-------------|--------------|
| Wire LLM eval infra (issue 0022) | `agent_generate` flowing through `run_case_full_v1`; `AnswerVerifier` wired into `run_post_repair_context_replay`; label string dropped; on-the-fly LLM rescore of `vector_memory` baseline | engineering | 05-25~28 | (no rerun needed) |
| At-scale re-test (issue 0023) | 596 × 10 replays under qwen2.5-7b agent + independent scorer + on-the-fly baseline rescore | engineering | 05-28~30 | issue 0035 lands |
| Headline adjudication set (issue 0024) | 130 researcher-labeled cases + LLM-A (llama-3.3-70b-instruct) suggest + 20-case blind spot-check | researcher | 05-30~01 (~5 hr) | re-sample post-0035 |
| Cohen's κ vs deepseek annotator | report agreement on 130-case overlap; bootstrap CI | researcher | 06-02 | re-compute post-0035 |
| deepseek labeling provenance recovery (issue 0033) | check into `scripts/annotate_perturbation_labels.py`, reference in `cleaning_report.txt` | researcher | 05-25 | (one-time) |
| Final V0→V1 gate decision | re-evaluate four criteria under new stack on adjudication set + 596 sanity check; bootstrap CIs | HITL | 06-03 V1.0 | 06-10+ V1.1 |

The 2026-05-22 evidence rows above remain on file as the pre-grilling snapshot.
```

---

## 3. `cmd_innovation_core/gates/V1V2_gate_status.md` — Cross-reference V0V1 caveat

### 3A. After `## Per-Agent Metrics` table, insert:

```markdown
> **2026-05-23 grilling caveat**: The per-adapter Macro F1 = 1.000 above was measured under V0 6-case smoke suite with the phrase-match shortcut active. See `V0V1_gate_status.md` § "2026-05-23 grilling caveat" for the at-scale 596-case re-test plan. Adapter-label parity is structurally still expected to hold under the LLM stack (the adapters' 10-replay portfolios are deterministic given identical evidence_block content and the scoring layer is shared with standalone harness), but the parity number must be re-measured on the adjudicated set + 596 sanity check before paper publication. Issue 0032 covers the parity-under-LLM-stack regression test.
```

### 3B. Append a new HITL log row:

```markdown
| 2026-05-23 | grilling-with-docs | pending re-test | Decision 34 R1+R4: parity number above is phrase-match shortcut, must be re-measured under LLM stack on 130 researcher-adjudicated + 596 sanity check before paper. |
| 2026-05-24 | grilling-with-docs | pending re-test V1.0 + V1.1 | Decision 34 R9+R10: parity numbers regenerate twice — V1.0 (596 corpus) and V1.1 (full corpus post-0035). |
```

---

## 4. `TASK.md` — Replace timeline + Next Steps

### 4A. Replace entire `## ⚠️ Accelerated Timeline — Target: 2026-06-10 Paper Draft` block

Replace with:

```markdown
## ⚠️ Accelerated Timeline — Target: 2026-06-10 V1.0 Arxiv Preprint, V1.1 Venue Submission Post-Corpus

Decision 30 (2026-05-20): counterfactual replay commoditizing. Timeline accelerated to **2026-06-10**. Decision 34 (2026-05-23/24): CMD vs Rewind head-to-head dropped. Paper headline binds to 130 researcher-adjudicated cases (LLM-A + spot-check assisted). V1.0/V1.1 dual-release: V1.0 ships as arxiv preprint on 06-10 with 596-derived numbers; V1.1 venue submission re-runs on full corpus post-issue-0035.

| Date | Milestone | Deliverable |
|------|-----------|-------------|
| ✅ 05-15~18 | V1 label expansion | Issues 0011-0012 |
| ✅ 05-19 | Coupled-failure + mem0 + Letta | Issues 0013-0015 |
| ✅ 05-20 | Decision 30 | Accelerate, Rewind 5-dim diff, repair depth metric |
| ✅ 05-21 | Issue 0019 Phase A + Issue 0018 design | llm_judge comparator, Pre-CMD Hook design |
| ✅ 05-22 | Real data + Gate at scale | Issues 0016-0018 (under phrase-match shortcut, see Decision 34 R1) |
| ✅ 05-23 | Decision 33 hook redesign + Decision 34 grilling start | issue 0021 implemented; REPAIR.md captures Q1-Q10 → R1-R7 |
| ✅ 05-24 | Decision 34 grilling close | Q11-Q23 → R8-R11; issues 0022-0034 written; REPAIR.md §15-§19 |
| 05-25~28 | LLM eval infra wiring (issue 0022, R1+R2+R5) | `agent_generate` + independent scorer + Post-Repair AnswerVerifier + label-strip + on-the-fly baseline rescore + hook bypass option |
| 05-25 | deepseek labeling provenance recovery (issue 0033) | `scripts/annotate_perturbation_labels.py` + cleaning_report annotated |
| 05-25 | Test suite migration (issue 0032) | conftest, label-leak invariant rewrite, adapter-parity-at-LLM-stack tests |
| 05-25 | Artifact archive (issue 0031) | move pre-D34 artifacts to `legacy_phrase_match_2026_05_22/` + MANIFEST |
| 05-28~30 | At-scale LLM re-test V1.0 (issue 0023) | 596 cases × 10 replays + post-repair under LLM stack — feeds 0026/0028/0029/0036 |
| 05-30 | Free hook calibration V1.0 (issue 0028) | LR fit on re-test outputs (~half-day) |
| 05-30~01 | Researcher 130-case adjudication V1.0 (issue 0024) | LLM-A (llama-3.3-70b-instruct) + 20-case blind spot-check + κ vs deepseek (~5 hr) |
| 06-01~03 | Researcher 80-ECS inspection V1.0 (issue 0025) | Manually corrected ECS for Experiment 1 (~5 hr) |
| 06-03 | Experiment 2 V1.0 headline (issue 0026) | CMD attribution Macro F1 + bootstrap CI on 130 adjudicated cases vs LLM-as-judge + evidence-recall + random; cost/latency column; per-source heatmap with CIs |
| 06-04 | Hook efficacy supplementary table (issue 0028) | recall + cost reduction |
| 06-04 | Surrogate-gap LLM rerun supplementary (issue 0036) | retention% on 4 gold-dependent labels, 50-case hold-out |
| 06-06 | Experiment 1 V1.0 + coupled-failure subset report | 5-mode (none/full_trace/corrected_only/corrected_only_padded/contrastive) on 80 cases; coupled-failure post-hoc on 30-50 near-tie cases (issue 0029) |
| 06-07 | Layered positioning + Decision 30 addendum (issue 0030) | ~2 hr writing, no code |
| 06-08~10 | V1.0 arxiv preprint draft | Headline 130-case + Experiment 1 + layered positioning + supplementary scale check + supplementary hook + supplementary coupled-failure + supplementary surrogate-gap. Cross-dataset claim = coverage only (V1.0 N too small for generalization) |
| post-corpus | Issue 0035 corpus migration cutover | V1.1 trigger: re-run 0023/0024/0026/0027/0028/0029/0031/0036 on full corpus |
| post-V1.1 | V1.1 venue submission | Same headline structure with full-corpus N; cross-dataset generalization claim now defensible |

**Critical path V1.0**: LLM eval infra → re-test → adjudication → Experiment 2 → arxiv preprint. Hook calibration off critical path. Rewind benchmark off critical path.

**Critical path V1.1**: issue 0035 corpus availability → all V1.0 issues re-run → venue submission.
```

### 4B. Replace entire `## Next Steps (ordered by dependency)` block

```markdown
## Next Steps (ordered by dependency)

Per Decision 34 (2026-05-23/24 grilling). All historical V0/V1 issue items remain ✅. This list is forward-only and tracks both V1.0 (06-10 arxiv) and V1.1 (post-corpus venue) milestones. Each issue 0022-0036 has individual detail map under `cmd_innovation_core/issues/`.

1. **issue 0033 — deepseek labeling provenance recovery** (R4-prov, target 05-25). Without this, the 596-case scale sanity check is irreproducible.
2. **issue 0022 — LLM eval infrastructure wiring** (R1+R2+R5, target 05-25~28). 6 code edits (replays.py shortcut gate + label-strip; post_repair.py AnswerVerifier wiring; harness.py pass-through + on-the-fly baseline rescore; PhraseMatchShortcutWarning category; conftest filter; new tests).
3. **issue 0032 — Test suite migration** (target 05-25~28). conftest filter, label-leak invariant rewrite, adapter-parity-at-LLM-stack tests.
4. **issue 0031 — Artifact archive + manifests** (R9, target 05-25). Pre-D34 artifacts → `legacy_phrase_match_2026_05_22/` with MANIFEST.txt.
5. **issue 0023 — At-scale LLM re-test V1.0** (R1+R3, target 05-28~30). 596 × 10 replays under LLM stack → `at_scale_llm_retest.csv`. Feeds 0026 / 0028 / 0029 / 0036.
6. **issue 0028 — Hook calibration V1.0** (R5 supplementary, target 05-30). Refactor `calibrate_hook.py` to consume 0023 outputs. Half-day. Off critical path.
7. **issue 0024 — Researcher 130-case adjudication V1.0** (R4+R11, target 05-30~01). LLM-A + 20-case blind spot-check. ~5 hours.
8. **issue 0025 — Researcher 80-ECS inspection V1.0** (R7, target 06-01~03). ~5 hours.
9. **issue 0026 — Experiment 2 V1.0 headline run** (R8, target 06-03). 130 adjudicated cases + 4 baselines + cost/latency + bootstrap CIs + per-source heatmap.
10. **issue 0036 — Surrogate-gap LLM-stack rerun** (R10/Q18, target 06-04). 50-case hold-out, 4 gold-dependent labels, retention%.
11. **issue 0029 — Coupled-failure subset post-hoc** (R3, target 06-04). 30-50 near-tie cases, manual inspection, calibrated tie_margin.
12. **issue 0027 — Experiment 1 V1.0 hardened** (R7, target 06-06). 80 cases × 5 modes, McNemar's tests Δ_1 + Δ_2.
13. **issue 0030 — Layered positioning + Decision 30 addendum** (R6, target 06-07). ~2 hr writing.
14. **V1.0 arxiv preprint** (target 06-08~10). Cross-dataset claim = coverage only.
15. **issue 0035 — Corpus migration cutover** (R10, post-V1.0). Full-corpus rebuild + re-annotation; triggers V1.1 reruns of 0023/0024/0026/0027/0028/0029/0031/0036.
16. **V1.1 venue submission** (post-0035). Cross-dataset claim = explicit generalization (post-corpus N supports it).

Post-paper V2: cascade repair via LLM self-modification on provenance DAG, multi-agent CMD, runtime repair loop, real-time live mem0/Letta integration.
```

---

## 5. `knowledge/current-memory.md` — Append new "Decision 34 grilling resolution" section

Append at the very end of the file:

```markdown
### 2026-05-23/24 (Day 13/14 — Decision 34 paper claim integrity grilling)

`grill-with-docs` session "discussion 3" with the user produced 11 binding resolutions (R1-R11) bundled as Decision 34 in `cmd_open_decisions.md`. Full paste-ready edit plan: `REPAIR.md` at repo root. 13 issue files written: `issues/0022-0034`.

**Headline finding**: 596-case Macro F1 = 1.000 reported in `V0V1_gate_status.md` 2026-05-22 is mechanical, not paper-grade. Three reasons:
1. `replays.py:477` shortcut means `recovery_gain ∈ {0.0, 1.0}` — perfect identity matrix tests replay-portfolio wiring, not diagnostic power.
2. `perturbation_label`s for 596 cases assigned by deepseek-v4-pro-max → CMD-vs-LLM-annotator agreement, not ground-truth.
3. `tie_margin = 0.05` is a magic number from binary-scoring era; under continuous LLM scoring it has no calibration.

**Resolutions (R1-R11, all approved by user 2026-05-23/24)**:

- **R1 At-scale LLM re-test**: 596 cases × 10 replays under qwen2.5-7b agent + independent LLM evaluator (≠ qwen, ≠ llama-A, ≠ deepseek). On-the-fly LLM rescore of `vector_memory` baseline. Drop `CMD ATTRIBUTION LABEL` line from `_build_replay_agent_context`.
- **R2 Post-Repair must run agent**: Wire `agent_generate` + `AnswerVerifier == EQUIVALENT` for `recovered`. τ=0.5 default partial threshold.
- **R3 Headline tie_margin = 0.0**: Zero-free-parameter argmax. Coupled-failure analysis = separate post-hoc subset report (issue 0029).
- **R4 Researcher-adjudicated 130-case headline + LLM-A assist**: ~16 × 8 active labels stratified, `random_state=42`. LLM-A (`llama-3.3-70b-instruct`) candidate suggestion + accept/reject + 20-case blind spot-check. The 596-case suite reframes as "CMD reproduces deepseek-v4-pro-max labels at Macro F1 = Y" scale sanity check. Cohen's κ vs deepseek as methods artifact. **R4-prov**: deepseek labeling prompt + script must be checked into `scripts/annotate_perturbation_labels.py`.
- **R5 Hook → supplementary**: Decision 33's two-stage hook stays implemented. Experiment 2 bypasses (`run_case_full_v1`, all 10 replays). Hook calibration runs *after* re-test, consumes re-test outputs as free training labels (no separate qwen pass). Hook efficacy = one supplementary table.
- **R6 Drop CMD vs Rewind head-to-head**: Different layers, different input modalities. Replaced with layered-stack positioning + reframed 5-dim table.
- **R7 Experiment 1 hardened**: 80 cases (20 × 4 labels), 5 modes (add `corrected_only_padded` token-control), 3-trial `none`-mode pre-check (lenient), researcher inspects 80 ECS records.
- **R8 Paper claim binding**: Q11 standalone FM recurrence collapsed into Experiment 1; Q12 repair depth as design claim ("Level 2 capability is design property of RepairAction emission"); Q13 AttributionFailed as principled abstention (two-tier headline coverage% + Macro F1 on attributed) + 8-label headline + 11-label supplementary completeness note.
- **R9 Artifact regeneration**: All 11 types regenerated under LLM stack on 596 (V1.0); per-source split preserved; pre-D34 artifacts archived; recurrence_comparison.csv dropped; semantic-shift annotations in MANIFEST.
- **R10 V1.0/V1.1 dual-release + paper-craft**: V1.0 arxiv 06-10 → V1.1 venue post-issue-0035. Cross-dataset claim version-gated: V1.0 coverage only, V1.1 explicit generalization. ~9/11 deployment claim with retention% backing (issue 0036). Two-evaluator robustness on 130-case headline only. Bootstrap CIs everywhere meaningful. Cost/latency in headline column.
- **R11 LLM-A = `llama-3.3-70b-instruct`**: Different family from deepseek/qwen/evaluator. Open weights, reproducible. Spot-check protocol: 20-case blind labeling first, then assisted; κ(blind, assisted) reports automation bias.

**Sequencing impact**: TASK.md updated (V1.0/V1.1 dual schedule). Critical path V1.0: LLM eval infra → re-test → adjudication → Experiment 2 → arxiv. Critical path V1.1: issue 0035 corpus availability → all V1.0 issues re-run → venue submission. Hook calibration and Rewind benchmark off both critical paths.

**Open dependencies V1.0**: deepseek labeling prompt + script recovery (R4-prov, was off-tree); evaluator model selection (specific gpt-4o-mini class candidate, decision deferred — but family must differ from deepseek/qwen/llama-A).

**Open dependencies V1.1**: issue 0035 (full-corpus build + re-annotation + cleaning_report regeneration).

**Files affected**: `cmd_open_decisions.md` (Decision 34 R1-R11 + Decision 30 addendum), `V0V1_gate_status.md`, `V1V2_gate_status.md`, `TASK.md` (V1.0/V1.1 timeline), `current-memory.md` (this section), `cmd_progress_report.md`, `plans/limitations.md`, `plans/experiment_01_context_construction.md`, `plans/experiment_02_cmd_attribution.md`, `replays.py`, `post_repair.py`, `scripts/calibrate_hook.py`, `scripts/annotate_perturbation_labels.py`, plus 13 new issue files (0022-0034) and supplementary issues (0035 corpus migration, 0036 surrogate-gap rerun) to be drafted.

Update header `## 当前状态` (around file line 7) to extend the existing 596-case sentence with: " 2026-05-23/24 grilling identified phrase-match-shortcut + LLM-annotator-circularity + tie_margin issues; Decision 34 R1-R11 captures the V1.0/V1.1 re-test plan."
```

---

## 6. `cmd_innovation_core/plans/cmd_progress_report.md` — Header status + metabolism row + section 9

### 6A. Replace top-of-file header

Replace existing date and status lines with:

```
**项目**: Counterfactual Memory Debugger (CMD) — 面向 LLM Agent 记忆的反事实记忆调试器
**日期**: 2026-05-24
**状态**: V0 完成且锁定。V1 实现层完成 (issues 0011-0021, 803 tests pass)。V0→V1 / V1→V2 gate 已通过 mechanics validation。**2026-05-23/24 grilling (Decision 34 R1-R11)** 识别 596-case Macro F1 = 1.000 为 phrase-match shortcut 产物, 非 paper-grade 归因证据。**当前阻塞 paper headline**: LLM eval infra 未接通, 130-case researcher adjudication (LLM-A + spot-check) 未启动。**V1.0/V1.1 双发布**: V1.0 arxiv preprint target 2026-06-10 (596-case dataset state); V1.1 venue submission post-issue-0035 (full-corpus dataset state)。Critical path V1.0: LLM eval infra → 596 re-test → 130 adjudication → Experiment 2 → arxiv preprint。Hook (Decision 33 / issue 0021) 已实现并降级为 supplementary。CMD vs Rewind head-to-head dropped, 替换为 related-work layered positioning。详见 REPAIR.md + issues 0022-0036。
```

### 6B. Append new Day rows to metabolism table

```markdown
| Day 13 | 2026-05-23 | Decision 34 grilling start: R1-R7 — phrase-match shortcut identified, headline binds to 130 researcher-adjudicated cases, Post-Repair gets agent_generate + AnswerVerifier, hook → supplementary, Rewind head-to-head dropped, Experiment 1 hardened to 80 × 5 modes. | — |
| Day 14 | 2026-05-24 | Decision 34 grilling close: R8-R11 — Q11 FM recurrence collapsed into Experiment 1, Q12 repair-depth as design claim, Q13 AttributionFailed as principled abstention, V1.0/V1.1 dual-release pattern, LLM-A = llama-3.3-70b-instruct for adjudication, bootstrap CIs everywhere, cost/latency in headline. 13 issues (0022-0034) + 2 supplementary (0035/0036) written. | — |
```

### 6C. Replace section 9 entirely

```markdown
## 9. 下一步行动 (V1.0 06-10 + V1.1 post-corpus, Decision 34 R10)

参见 `TASK.md` § "Next Steps (ordered by dependency)" 16 项 forward-only 列表。Critical path V1.0: LLM eval infra → 596 re-test → 130 adjudication → Experiment 2 → arxiv preprint. V1.1 trigger: issue 0035 corpus migration cutover.

**Paper claim 绑定 V1.0**:
- 主结论 (Experiment 2): 130 researcher-adjudicated cases 经 LLM-A 辅助 + 20-case 盲测试，CMD Macro F1 + bootstrap CI vs llm_judge / evidence_recall / random
- 第二结论 (Experiment 1): 80 cases × 5 modes 上下文构建 + token-control + McNemar's Δ_1 + Δ_2
- Scale sanity: 596 cases vs deepseek-v4-pro-max annotator agreement + Cohen's κ
- Online deployment: ~9/11 label coverage + retention% on 4 gold-dependent labels (50-case hold-out)
- Cost/latency: 头条表内列, agent + scorer + verifier 分项
- Supplementary 1: hook efficacy (recall + cost reduction)
- Supplementary 2: coupled-failure 30-50 near-tie cases + post-hoc tie_margin
- Supplementary 3: surrogate-gap retention % (issue 0036)
- Cross-dataset claim: V1.0 coverage only (per-source heatmap with bootstrap CIs)
- Related work: layered-stack positioning, no head-to-head benchmark

**V1.1 expansion** (post-issue-0035): cross-dataset claim → explicit generalization; same metrics on full-corpus N.
```

---

## 7. `cmd_innovation_core/plans/limitations.md` — Rewrite §2 and §6, add §9 evaluator-circularity

### 7A. Replace §2 (`## 2. Deterministic Scoring (Implementation)`) with:

```markdown
## 2. LLM-Based Semantic Scoring — Resolved (Implementation)

**What it was.** Pre-2026-05-23, CMD's `answer_score` used casefold exact-match scoring (`0.0` or `1.0`) and `evidence_recall_from_text` used casefold phrase matching. Both were lower bounds. The at-scale 596-case Macro F1 = 1.000 was driven by `replays.py:477` short-circuiting the agent loop entirely.

**Resolution (Decision 34 R1+R2, 2026-05-23/24).** The at-scale re-test moves to:
- `agent_generate` = qwen2.5-7b ollama producing replay answers from `(query, baseline + evidence_block)` (label string dropped from replay context).
- Evidence scorer = an LLM independent of agent model and adjudication LLM-A. Continuous `evidence_score ∈ [0,1]` from `count(PRESENT) / total` over per-fact subagent calls.
- `AnswerVerifier == EQUIVALENT` drives the `recovered` decision in Post-Repair Context Replay; partial threshold τ=0.5 default, calibrated post-hoc.
- `vector_memory` baseline rescored on-the-fly under same agent + scorer per case, eliminating asymmetric scoring between replays and baselines.
- Bootstrap CI (1000-iter case-level resample) on Macro F1 + top-2 + per-label F1 + per-baseline + κ.
- Two-evaluator robustness check on 130-case headline only (Decision 34 R10/Q19).

The phrase-match path is preserved as a fallback when `agent_generate is None and scorer is None`, gated by `PhraseMatchShortcutWarning`. Functions as regression-detecting lower bound and is the path used by V0 unit tests.

**Remaining limitation.** LLM scorer introduces variance (~5-10% intra-call at temperature=0). Mitigated by per-case `recovery_gain` distribution logging, 3-trial protocols at admission gates (Experiment 1 `none`-mode pre-check), bootstrap CIs on aggregate metrics, and two-evaluator robustness on 130-case headline.
```

### 7B. Replace §6 (`## 6. Phrase-Matching Evidence Recall (Implementation)`) with:

```markdown
## 6. Phrase-Matching Evidence Recall — Resolved (Implementation)

**What it was.** `evidence_recall_from_text` checked whether all `required_phrases` of a gold evidence unit appear (casefold) in a memory item's text. Lexical substring matching, not semantic entailment. Misclassified paraphrases as missing.

**Resolution (Decision 34 R1, 2026-05-23/24).** Replaced in the at-scale evaluation path by `SubagentScorer` (`llm_scoring.py`, issue 0019 Phase B): one binary subagent call per `(gold_evidence_fact, text)`, output PRESENT | ABSENT, aggregated to continuous `[0,1]`. Phrase-match retained as fallback when `scorer is None`. Verbatim Event Oracle boundary (`evidence_recall_from_text(gold_evidence, memory_item.text)`) keeps phrase-match for the structural boundary check.

**Remaining limitation.** Same as §2: LLM scorer variance + per-fact subagent call cost (~6000 calls per 596-case re-test). Caching by `(fact_hash, text_hash)` mitigates repeated runs.
```

### 7C. Insert new §9 BEFORE existing `## Summary`:

```markdown
## 9. Evaluator-Annotator Circularity (Methodological)

**What it is.** The 596 `perturbation_label`s were assigned by deepseek-v4-pro-max (LLM annotator). Macro F1 measured against this label set is by definition CMD-vs-LLM-annotator agreement, not ground-truth attribution accuracy. A reviewer pointing this out can dismiss any large-N number that depends on these labels.

**Why it cannot be fully eliminated for the 596 set.** Hand-labeling 596 cases at ~5 minutes each is ~50 hours, beyond timeline budget.

**Mitigation (Decision 34 R4+R11).** Two-tier evaluation with researcher-grade headline:
- **Headline (small, researcher-grade)**: 130 cases stratified ~16 per active label across 8 labels, hand-labeled by researcher with LLM-A (`llama-3.3-70b-instruct`) candidate suggestion + accept/reject. LLM-A is family-disjoint from deepseek annotator, qwen agent, and evaluator scorer (three-independent-LLMs rule). Researcher confidence ∈ {high, medium, low}; high+medium → headline, low → appendix.
  - **Automation-bias countermeasure**: 20 cases labeled blind first (no LLM-A); same 20 re-labeled with LLM-A. κ(researcher_blind, researcher_assisted) reported. If κ < 0.7, redo without LLM-A.
- **Scale sanity (large, LLM-annotated)**: 596 cases under same scorer/agent stack as headline, framed as "CMD reproduces deepseek-v4-pro-max labels at Macro F1 = Y." Functions as regression check.

**Why this is acceptable.** No memory-debugging benchmark currently exists with researcher-adjudicated labels at scale. Community baseline is "annotator-LLM produces labels, methods evaluated against them." CMD's claim is one step stronger: LLM-A-assisted human adjudication on a representative subset, with automation-bias measurement. V2 / community-resource scope: scale researcher-labeling to 500+ cases via crowdsourced annotation with multi-rater κ.

**V1.0 → V1.1 expansion**: post-issue-0035, full corpus rebuild + re-annotation. Researcher 130-case set re-sampled from new pool. Bootstrap CIs preserved.

**Implementation artifacts**:
- `data/probe_cases/researcher_labeled_subset.json` — 130 cases with researcher labels + confidence + LLM-A suggestion + disagreement flags.
- `scripts/annotate_perturbation_labels.py` — recovered or reconstructed deepseek prompt + run script (R4-prov).
- `artifacts/researcher_vs_deepseek_kappa.txt` — Cohen's κ + bootstrap CI between annotators.
- `artifacts/automation_bias_kappa.txt` — κ(researcher_blind, researcher_assisted) on 20-case spot-check.
- Methods section reports: headline on 130, scale on 596, two-evaluator robustness on 130, three κ values (researcher↔deepseek, blind↔assisted, evaluator-A↔evaluator-B).
```

### 7D. Replace `## Summary` table with:

```markdown
## Summary

| Limitation | Type | Severity | Mitigation | Status |
|-----------|------|----------|------------|--------|
| Gold evidence dependency | Methodological | High (4/11 labels) | Two-tier deployment; surrogate self-supervision (issue 0036 retention% measurement) | V1 candidate generation; V2 deployment validation |
| LLM-based scoring (was: deterministic) | Implementation | Resolved | LLM `agent_generate` + independent scorer + AnswerVerifier per Decision 34 R1+R2 | Wiring sprint 05-25~28 |
| Synthetic + LLM-annotated perturbations | Evaluation | Medium | Researcher-adjudicated 130-case headline (R4) + LLM-A assist + 20-case blind spot-check (R11) | 05-30~01 (5 hr researcher) |
| Single-agent scope | Methodological | Medium | Shapley + CMD composition | V2 |
| Operation-level granularity | Methodological | Low | Per-item replay + bad-item labels | V2 |
| Phrase-matching evidence recall (was) | Implementation | Resolved | `SubagentScorer` replaces phrase-match in eval path | Wiring sprint |
| Evaluation scope | Implementation | Medium | Real traces, human baseline, cost measurement (cost in headline per R10) | V1.0 → V1.1 → V2 |
| Closed-world taxonomy | Methodological | Low | Open-world detection; taxonomy extension | V2 |
| Evaluator-annotator circularity (new §9) | Methodological | High before R4+R11, Medium after | LLM-A-assisted human adjudication + 20-case blind spot-check + automation-bias κ | R4+R11 in progress |

CMD's primary remaining limitation after Decision 34 is gold evidence dependency for content-absence diagnosis (information-theoretic bound) and evaluator-annotator circularity at scale (mitigated by hand-labeled headline subset; full scaling to N≥500 with multi-rater κ is community-resource work).
```

---

## 8. `cmd_innovation_core/plans/experiment_02_cmd_attribution.md` — Rebind to 130-case + LLM-A + V1.0/V1.1

### 8A. Replace top-of-file header

```
**日期**: 2026-05-24 (Decision 34 R1-R11 修订)
**状态**: 设计完成，data 准备 60% — 596 LLM-annotated cases on disk; 130 researcher-adjudicated subset 待生成 (target 05-30~01, LLM-A 辅助); LLM eval infra 待接通 (target 05-25~28); 无 hook 路径 confirmed.
**论文角色**: 主结论 — V1.0 arxiv preprint 头条 Macro F1 + bootstrap CI 报告基于 130 researcher-adjudicated cases。596-case 作为 scale sanity check。V1.1 venue submission 全量数据集重跑 (issue 0035 trigger)。
```

### 8B. Insert new §1.5 between §1 and §2

```markdown
## 1.5 Evaluation Set Definition (Decision 34 R4+R11)

**Two-tier evaluation, not single-set**:

| Tier | Set | Size | Label Source | Role | Headline Eligible |
|------|-----|------|--------------|------|--------------------|
| Headline | researcher-adjudicated subset | 130 (V1.0) / 130 re-sampled (V1.1) | researcher hand-labeled with LLM-A (`llama-3.3-70b-instruct`) candidate suggestion + accept/reject + 20-case blind spot-check | "CMD Macro F1 = X [95% CI ...]" main claim | YES — high+medium confidence subset |
| Scale | full real-data suite | 596 (V1.0) / full corpus (V1.1, post-0035) | deepseek-v4-pro-max LLM annotator | "CMD reproduces annotator labels at Macro F1 = Y across N" sanity | NO — supplementary only |

**Stratified sampling for headline (130 cases)**:
- 8 active labels: write_error, compression_error, premature_extraction_error, retrieval_error, injection_error, reasoning_error, route_error, ingestion_error.
- ~16 per label = 128 + 2 spare = 130 target.
- `random_state=42`. Persist sampled `case_id`s to `data/probe_cases/researcher_labeled_subset.json`.

**LLM-A-assisted labeling protocol (Decision 34 R11)**:
1. LLM-A (`llama-3.3-70b-instruct`) emits `(suggested_label, rationale)` per case.
2. Researcher reads case + suggestion + rationale; assigns `final_label`, `confidence`.
3. Records: `(case_id, deepseek_label, llm_a_suggestion, llm_a_rationale, researcher_label, confidence, disagreement_with_deepseek, disagreement_with_llm_a, researcher_notes)`.
4. **Spot-check**: First 20 cases labeled blind (no LLM-A). Same 20 re-labeled with LLM-A after assisted pass. κ(researcher_blind, researcher_assisted) reported as automation-bias measurement.
5. If κ < 0.7, redo entire pass without LLM-A.

**Three-independent-LLMs rule**: deepseek-v4-pro-max (annotator) ≠ qwen2.5-7b (agent_generate) ≠ llama-3.3-70b-instruct (LLM-A) ≠ evaluator scorer (TBD, family-disjoint).

**Confidence scale**:
- **high**: clear from trace which label applies; headline.
- **medium**: probable, weak alternative support; headline + sensitivity analysis.
- **low**: ambiguous; appendix only.

**Headline reporting**:
- Primary: high+medium subset (≈ N=110-115 expected) Macro F1 + bootstrap CI [95%].
- Sensitivity: same metrics on all N=130 (low included).
- Per-label F1 with bootstrap CI per cell.
- Per-source split via heatmap (LongMemEval / MemoryArena / ToolBench × 8 labels).
- AttributionFailed coverage% reported alongside Macro F1 on attributed (two-tier framing per R8/Q13).
- Cohen's κ between researcher and deepseek labels reported as methods artifact with bootstrap CI.

**deepseek labeling provenance (R4-prov)**: prompt + script must be checked into `scripts/annotate_perturbation_labels.py` and referenced in `data/cleaned_cases/cleaning_report.txt` before headline runs.
```

### 8C. Update §2 装置总览 ASCII diagram by appending below the closing `└────────...────┘`:

```markdown

**Decision 34 changes to this装置 (2026-05-23/24)**:
- Probe Case row: drawn from 130 researcher-adjudicated subset (LLM-A assisted + 20-case blind spot-check), not 596.
- Baseline row: `agent_generate` runs on `(query, vector_memory.injected_context)` per case at re-test time; same independent scorer. Pre-baked `baseline.evidence_score` bypassed.
- 4 Comparator Baselines: random / evidence_recall / subagent_judge / llm_judge (issue 0019 Phase A).
- CMD-Audit Harness:
  - Subagent Judge Monitor: bypassed for headline; trigger replays for all 130 cases unconditionally.
  - V0 Replay Portfolio extended to V1 10-replay portfolio (`run_case_full_v1`); hook bypassed (R5).
  - Recovery Gain: `Δk = scorer(gold_evidence, agent_generate(query, baseline + evidence_block)) - scorer(gold_evidence, baseline_answer_llm)` — both terms LLM-scored.
  - Attribution: `tie_margin = 0.0` for headline argmax (R3).
  - RepairAction emission (`repairs.py:563-672`) records per-case `(action_type, target_item_id, target_store)` for repair-depth descriptive measurement (R8/Q12).
- Evaluation:
  - Confusion matrix vs researcher labels (high+medium confidence) with bootstrap CI per cell.
  - Macro F1 + Top-2 Accuracy + per-label F1, all with bootstrap [95% CI].
  - CMD vs 4 baselines (cost/latency in headline column per R10).
  - Per-source heatmap with CIs.
  - Two-evaluator robustness on 130 (R10/Q19): per-evaluator Macro F1 + agreement.
  - Cohen's κ (researcher↔deepseek, researcher_blind↔researcher_assisted) as methods artifacts.
  - Two-tier coverage% + Macro F1 on attributed (R8/Q13).
  - 11-label supplementary architecture-completeness note (synthetic granularity/graph/safety probe cases acknowledged but not quantitatively claimed).
```

### 8D. Search-and-replace 596 references to V1.0/V1.1 dual targets

Every "596" inside `experiment_02_cmd_attribution.md` should be checked and routed to:
- "130 (V1.0 headline) / 130 re-sampled (V1.1 headline)" — primary evaluation
- "596 (V1.0 scale sanity check) / full corpus (V1.1 scale sanity check)" — secondary
- Left as-is — historical context (dataset construction)

---

## 9. `cmd_innovation_core/plans/experiment_01_context_construction.md` — Apply R7 hardening

### 9A. Replace §2.2 four-mode table with five-mode

```markdown
### 2.2 五种 Context Mode (Decision 34 R7)

| Mode | 注入内容 | 角色 |
|------|---------|------|
| `none` | 无 Failure Memory，仅 query | 基线 (验证该 case 确实需要 FM) |
| `full_trace` | `wrong_memory` (baseline 的错误上下文) | 反模式对照 (验证注入错误记忆有害) |
| `corrected_only` | `corrected_memory + repair_guidance` | CMD V0/V1 当前策略 |
| `corrected_only_padded` | `corrected_memory + repair_guidance` + neutral filler tokens (padding 至 contrastive 字符长度) | **Token-control** — 排除 "更多 tokens 自然提升 attention" 解释 |
| `contrastive` | `wrong_memory + cause + corrected_memory + repair_guidance` | V2 候选策略 |

Neutral filler 内容: 通用且与 query 无关的 placeholder 段落 ("[The following is unrelated reference material that does not affect the answer.] ..." 重复至目标字符数)。
```

### 9B. Replace §2.3 admission criterion

```markdown
### 2.3 Case 准入条件 (Decision 34 R7)

**`none` 模式必须可靠失败。** 单次 LLM 调用因 ~5-10% temperature=0 variance 不可靠。
- 用所选 LLM + `none` mode context 跑 **3 次独立调用** (fixed `seed`, identical prompt; ollama / API 重启 process 之间隔 10s 防止 KV cache 复用)。
- 若 ≥1 次调用 produces correct answer → 排除该 case (lenient 排除)。
- 若 3 次全部 fail → 该 case 进入实验。

3-trial pre-check 平均增加 240 LLM 调用 (80 cases × 3) 至总成本，仍 < $1 在 gpt-4o-mini 价位。
```

### 9C. Replace §3.3 sample size

```markdown
### 3.3 样本量 (Decision 34 R7 + R10)

| 层级 | V1.0 (596 corpus state) | V1.1 (full corpus, post-0035) | 说明 |
|------|--------|--------|------|
| Error type 覆盖 | 4 种固定 | 同 | `retrieval_error`, `compression_error`, `premature_extraction_error`, `reasoning_error` |
| 每 type case 数 | 20 | 20 (re-sampled from full pool) | McNemar's test detectable effect ≈ 15pp at α=0.05, β=0.2 per label |
| 总计 | **80** | **80 (re-sampled)** | 4-label 合并后 N=80 reduces detectable effect to ~10pp |
| 模式数 | **5** (新增 `corrected_only_padded`) | 同 | — |
| LLM 调用 | 80×5 + 80×3 + 80×5 ≈ 1040 | 同 | 总 ~$3 在 gpt-4o-mini 价位 |

**ECS records**: 80 cases 的 ECS records (`cause`, `corrected_memory`, `repair_guidance`) 在 mode 渲染前由 researcher 手动检视/编辑 (Decision 34 R7)。检视记录: `data/probe_cases/experiment_01_inspected_ecs.json`，per-case `(case_id, original_ecs, edited_ecs, edit_reason)`。研究人员预计 ~5 hours per V1.0 / V1.1 round.
```

### 9D. Replace §6.3 contrast metric

```markdown
### 6.3 对比指标 (Decision 34 R7)

```
主要对比 1 (token-uncontrolled, 与文献对照):
  Δ_1 = EM(contrastive) - EM(corrected_only)

主要对比 2 (token-controlled, 因果分离):
  Δ_2 = EM(contrastive) - EM(corrected_only_padded)
  
辅助对比:
  Δ_pad = EM(corrected_only_padded) - EM(corrected_only)
```

McNemar's test on Δ_1 和 Δ_2 各跑一次。Bootstrap CI (1000-iter case-level) on EM rates per mode.

**结果判读规则**:

| Δ_1 | Δ_2 | 结论 |
|-----|-----|------|
| > 0 显著 | > 0 显著 | 对比模式有效，且效果不来自 token 数 → V2 加入 + paper claim |
| > 0 显著 | ≈ 0 不显著 | "改进"是 token 数自然结果 → V2 不加入；paper 报告该发现 (warning to community) |
| ≈ 0 | ≈ 0 | 对比模式无增益 → V0/V1 corrected_only 已足够 + paper claim |
| < 0 | — | 对比模式有害 → V2 不加入 + negative result paper claim |

Per-label 分析: 在 4 labels 上分别报告 Δ_1 + Δ_2 + bootstrap CI。
```

### 9E. Append new §9 compliance checklist

```markdown
## 9. Decision 34 R7 Compliance Checklist

每次 experiment 跑完前确认:

- [ ] 80 ECS records 已 researcher inspected, edits in `data/probe_cases/experiment_01_inspected_ecs.json`
- [ ] 5 modes rendered, `corrected_only_padded` token 数 == `contrastive` token 数 (±5 token tolerance)
- [ ] 80 cases 全部通过 3-trial `none`-mode pre-check
- [ ] LLM model + version locked, `temperature=0`, `seed=42`
- [ ] Bootstrap CI (1000-iter) 在 EM rates / Δ_1 / Δ_2 上报告
- [ ] McNemar's test on Δ_1 + Δ_2 均报告
- [ ] 4-label per-error-type 分析报告
- [ ] 输出 `artifacts/experiment_01_results.csv` schema includes `mode_token_count`, `corrected_only_padded_em`, `mcnemar_p_delta_1`, `mcnemar_p_delta_2`, bootstrap CI columns
- [ ] V1.1 trigger: re-run after issue 0035 with full-corpus 80-case sample
```

---

## 10. New files to create

### 10A. `data/probe_cases/researcher_labeled_subset.json` — stub

```json
{
  "schema_version": "1.0",
  "decision": "Decision 34 R4 + R11 (2026-05-23/24)",
  "release_version": "v1.0",
  "sampling": {
    "source": "data/probe_cases/real_longmemeval_cases.json + real_memoryarena_cases.json + real_toolbench_cases.json (596 cases V1.0; full corpus V1.1)",
    "total_pool": 596,
    "stratification": "8 active labels × ~16 cases = 128 (target 130 with 2 spare slots)",
    "random_state": 42,
    "active_labels": [
      "write_error",
      "compression_error",
      "premature_extraction_error",
      "retrieval_error",
      "injection_error",
      "reasoning_error",
      "route_error",
      "ingestion_error"
    ]
  },
  "annotators": {
    "deepseek": {
      "model": "deepseek-v4-pro-max",
      "role": "scale-sanity annotator",
      "script": "scripts/annotate_perturbation_labels.py"
    },
    "llm_a": {
      "model": "llama-3.3-70b-instruct",
      "role": "candidate suggestion for researcher",
      "constraints": "family-disjoint from deepseek (annotator), qwen2.5-7b (agent_generate), evaluator scorer (TBD)"
    },
    "researcher": {
      "name": "supremewen",
      "protocol": "Read case (query, extracted_memory, baseline_outputs, gold_answer) + LLM-A suggestion + rationale; assign final_label, confidence ∈ {high, medium, low}; record disagreements. First 20 cases labeled blind (no LLM-A) for automation-bias spot-check."
    }
  },
  "spot_check": {
    "blind_case_ids": [],
    "kappa_blind_vs_assisted": null,
    "kappa_threshold_for_validity": 0.7
  },
  "cases": []
}
```

Each case entry once populated:

```json
{
  "case_id": "longmemeval-single-XXXX",
  "deepseek_label": "retrieval_error",
  "llm_a_suggestion": "retrieval_error",
  "llm_a_rationale": "Gold memory item present in extracted_memory but absent from baseline.retrieved_memory_ids; matches retrieval_error definition.",
  "researcher_label": "retrieval_error",
  "confidence": "high",
  "disagreement_with_deepseek": false,
  "disagreement_with_llm_a": false,
  "researcher_notes": "Clear retrieval failure; no ambiguity.",
  "blind_pass": false
}
```

### 10B. `scripts/annotate_perturbation_labels.py` — recovery target (R4-prov)

User must produce or recover the deepseek-v4-pro-max prompt + run script. If lost, write re-derivation per issue 0033 protocol. Reference in `cleaning_report.txt`.

### 10C. `data/probe_cases/experiment_01_inspected_ecs.json` — stub

```json
{
  "schema_version": "1.0",
  "decision": "Decision 34 R7 (2026-05-23/24)",
  "release_version": "v1.0",
  "purpose": "Manually inspected/edited ECS records for Experiment 1's 80 cases. Decouples context-mode effect from ECS-generation quality.",
  "inspection_window": "2026-06-01 ~ 2026-06-03 (V1.0); re-run post-issue-0035 (V1.1)",
  "inspector": "supremewen",
  "cases": []
}
```

### 10D. `data/cleaned_cases/cleaning_report.txt` — append annotator block

After existing CMD relevance score distribution, append:

```
============================================================
Annotation Provenance (Decision 34 R4-prov, added 2026-05-23/24)
============================================================

perturbation_label assignment for the 596 cases below was performed by:

Annotator model: deepseek-v4-pro-max
Annotation date: <FILL IN — researcher to recover from logs>
Annotation script: scripts/annotate_perturbation_labels.py
Annotation prompt template: see scripts/annotate_perturbation_labels.py docstring
Temperature: <FILL IN>
Top-p: <FILL IN>
Total annotation calls: 596 (one per case)

Reproducibility: re-running scripts/annotate_perturbation_labels.py against
data/cleaned_cases/cleaned_cases.json should reproduce the labels in
data/probe_cases/real_*_cases.json to >=95% agreement (some drift expected).

A 130-case stratified researcher-adjudicated subset
(data/probe_cases/researcher_labeled_subset.json) is the headline evaluation set
per Decision 34 R4. The 596 deepseek labels function as scale sanity check.

Adjudication uses LLM-A (llama-3.3-70b-instruct) for candidate suggestions per
Decision 34 R11. Three-independent-LLMs rule: deepseek-v4-pro-max ≠ qwen2.5-7b ≠
llama-3.3-70b-instruct ≠ evaluator scorer (TBD, family-disjoint).

20-case blind spot-check measures automation-bias-free judgment via Cohen's
κ(researcher_blind, researcher_assisted). If κ < 0.7, redo without LLM-A.

V1.1 trigger (issue 0035): full-corpus rebuild + re-annotation. This block
will be regenerated for V1.1 with new corpus statistics.
```

---

## 11. Code edits — Deferred to wiring sprint 05-25~28

REPAIR.md does not edit Python source per user direction. Wiring sprint spec:

### 11A. `cmd_audit/replays.py:477` — gate phrase-match shortcut

Current (lines 473-478):

```python
    if scorer is not None:
        evidence_score = scorer(case.gold_evidence, evidence_block)
    else:
        evidence_score = evidence_recall_from_text(case.gold_evidence, evidence_block)
    answer = case.gold_answer if evidence_score == 1.0 else ""
    recovered_answer_score = answer_score(answer, case.gold_answer)
```

Required:
- `answer = case.gold_answer if evidence_score == 1.0 else ""` shortcut runs only when `agent_generate is None and scorer is None`. With `scorer` provided but `agent_generate is None`, score `evidence_block` directly.
- Emit `warnings.warn(..., PhraseMatchShortcutWarning)` once per process when shortcut runs. Text: "phrase-match shortcut active — recovery_gain is mechanical, not LLM-evaluated; this path is for V0 unit-test determinism only and must not produce paper claims."

### 11B. `cmd_audit/replays.py:_build_replay_agent_context` — drop label string (R1 point 5)

Current (lines 490-513):

```python
def _build_replay_agent_context(
    case: ProbeCase, replay_name: str, evidence_block: str
) -> str:
    label = V1_REPLAY_TO_LABEL.get(replay_name, replay_name)
    if replay_name == "oracle_write" and not case.has_ingestion_trace:
        label = "ingestion_error"
    ...
    return "\n\n".join(
        (
            "BASELINE CONTEXT:\n" + baseline_context,
            "CMD ATTRIBUTION LABEL:\n" + label,
            "COUNTERFACTUAL EVIDENCE BLOCK:\n" + evidence_block,
        )
    )
```

Required:

```python
def _build_replay_agent_context(
    case: ProbeCase, replay_name: str, evidence_block: str
) -> str:
    baseline_context = case.primary_baseline.injected_context
    if not baseline_context:
        memory_by_id = {item.memory_id: item for item in case.extracted_memory}
        baseline_context = "\n".join(
            memory_by_id[mid].text
            for mid in case.primary_baseline.retrieved_memory_ids
            if mid in memory_by_id
        )

    return "\n\n".join(
        (
            "BASELINE CONTEXT:\n" + baseline_context,
            "COUNTERFACTUAL EVIDENCE BLOCK:\n" + evidence_block,
        )
    )
```

`replay_name` arg retained for signature stability. Remove unused `V1_REPLAY_TO_LABEL` import if no other use.

### 11C. `cmd_audit/post_repair.py:run_post_repair_context_replay` — agent + AnswerVerifier (R2)

Required signature + behavior:

```python
def run_post_repair_context_replay(
    case: ProbeCase,
    repaired_context: RepairedContext,
    *,
    agent_generate: AgentGenerate | None = None,
    evidence_scorer: EvidenceScorer | None = None,
    answer_verifier: AnswerVerifier | None = None,
    partial_threshold: float = 0.5,
) -> PostRepairResult:
    combined = _combine_context(repaired_context)
    if agent_generate is not None:
        answer = agent_generate(case.query, combined)
        if evidence_scorer is not None:
            evidence_score = evidence_scorer(case.gold_evidence, answer)
        else:
            evidence_score = evidence_recall_from_text(case.gold_evidence, answer)
        if answer_verifier is not None:
            verdict = answer_verifier(answer, case.gold_answer)
            recovered = (verdict == "EQUIVALENT")
        else:
            recovered = case.gold_answer.casefold() in answer.casefold()
        post_answer_score = 1.0 if recovered else 0.0
    else:
        warnings.warn(
            "Post-Repair substring fallback active — paper claims must use "
            "agent_generate + answer_verifier (Decision 34 R2)",
            PhraseMatchShortcutWarning,
            stacklevel=2,
        )
        evidence_score = evidence_recall_from_text(case.gold_evidence, combined)
        gold_in_context = case.gold_answer.casefold() in combined.casefold()
        post_answer_score = 1.0 if gold_in_context else 0.0

    if post_answer_score == 1.0:
        assessment = "recovered"
    elif evidence_score >= partial_threshold:
        assessment = "partial"
    else:
        assessment = "failed"
    ...
```

`partial_threshold=0.5` default per R2; calibration of τ post-hoc on 30-50 cases (issue 0029).

### 11D. `cmd_audit/harness.py:run_case_full_v1` — pass-through + on-the-fly baseline rescore

Wire `agent_generate / scorer / answer_verifier` from `run_case_full_v1`, `run_cases_v1`, `run_full_real_suite` down to `run_v1_replay_portfolio` and `run_post_repair_context_replay`. Add `on_the_fly_baseline_rescore: bool = False` flag — when True with `agent_generate + evidence_scorer` provided, run `agent_generate(case.query, baseline.injected_context)` per case before replays, score with `evidence_scorer`, populate `baseline_evidence_score_llm` on artifact CSV, use that for `recovery_gain` denominator.

### 11E. `scripts/calibrate_hook.py` — refactor (R5)

- Read `artifacts/at_scale_llm_retest.csv` produced by issue 0023.
- Construct training set from per-(case, replay) `recovery_gain`; label = `recovery_gain > 0`.
- Fit 16-feature LR (6 global + 10 replay_type one-hot), `class_weight='balanced'`, `random_state=42`.
- Persist `artifacts/hook_calibration/training_set_llm.npz`.
- Step 2: surrogate vs gold gap on 50 hold-out (consumes re-test outputs).
- Step 3: global threshold grid `TOP_K ∈ {2,3,4,5} × FALLBACK_THRESHOLD ∈ [0,1] step 0.05`. Emit constants to `cmd_audit/hook/constants.py`.

No internal LLM calls — all LLM-derived labels come from re-test artifact.

### 11F. New tests

- `tests/test_cmd_audit_decision34_phrase_match_warning.py` — warning fires/doesn't fire; uses `legacy_phrase_match_path` fixture.
- `tests/test_cmd_audit_decision34_replay_context_no_label.py` — `_build_replay_agent_context` output does not contain `CMD ATTRIBUTION LABEL` or any V1 label name.
- `tests/test_cmd_audit_decision34_post_repair_agent.py` — `run_post_repair_context_replay` with `agent_generate` calls agent on `(case.query, combined)`; with `answer_verifier` returns `recovered ⇔ EQUIVALENT`; `partial_threshold` default 0.5.
- `tests/test_cmd_audit_decision34_baseline_rescore.py` — `on_the_fly_baseline_rescore=True` populates `baseline_evidence_score_llm`.
- `tests/test_cmd_audit_decision34_adapter_parity_llm.py` — Mem0Adapter + LettaAdapter parity with standalone harness under stub `agent_generate + scorer`; cross-adapter non-regression.
- Bootstrap CI utility test (new `cmd_audit/bootstrap.py` if added).

Total ≈ 12-15 new tests + 1 rewritten leak-invariant test.

---

## 12. Out-of-scope but flagged for later

1. **CLAUDE.md placeholders** — already filled in 2026-05-24.
2. **`paper/limitations.md` location** — file actually at `cmd_innovation_core/plans/limitations.md`. References pointing to `paper/` are aspirational; future cleanup pass.
3. **`replays.py:_build_replay_agent_context` `replay_name` arg unused after R1.5** — keep for signature stability per 11B; future cleanup may remove arg entirely.
4. **`AttributionFailed` bound under noisy LLM scoring** — current bound is strict `> 0`. Consider `≥ ε` (e.g., 0.01) under LLM noise; out of scope for REPAIR.
5. **Cohen's κ utility** — add `cmd_audit/agreement.py` with `cohen_kappa(labels_a, labels_b) -> float`. Used by R4+R11 + automation-bias spot-check. Bootstrap CI on κ also requires this.
6. **`cmd_audit/bootstrap.py`** — new utility for case-level resampling per R10/Q23. CPU-only, no LLM, single function `bootstrap_metric(case_ids, score_fn, n_iters=1000) -> (mean, ci_low, ci_high)`.

---

## 13. Execution order for the user

1. Read REPAIR.md end-to-end. Reject anything that doesn't make sense.
2. Apply doc edits §1 → §2 → §3 → §4 → §5 → §6 → §7 → §8 → §9. Each section self-contained.
3. Create new files per §10A, §10C, §10D. §10B is researcher-recovery (issue 0033).
4. Schedule wiring sprint per §11. Code edits land 05-25~28.
5. Read issues 0022-0036 for execution plans (start with 0034 index).
6. After all integration, archive REPAIR.md to `cmd_innovation_core/issues/0034-decision-34-issue-index.md` is already in place; REPAIR.md itself stays at root as the live integration doc.

---

## 14. Cross-reference index

- "Decision 34" → `cmd_open_decisions.md` Decision 34, R1-R11.
- "phrase-match shortcut" → §1 R1, §11A.
- "researcher-adjudicated 130 cases" → §1 R4+R11, §8B, §10A.
- "LLM-A llama-3.3-70b-instruct" → §1 R11, §8B, §10A.
- "deepseek labeling provenance" → §1 R4-prov, §10B, §10D, issue 0033.
- "tie_margin 0.0" → §1 R3, §3 V1V2 footer, issue 0029.
- "CMD vs Rewind dropped" → §1 R6, §1B Decision 30 addendum, issue 0030.
- "Experiment 1 5 modes" → §9 R7, §10C, issue 0027.
- "hook supplementary" → §1 R5, §11E, issue 0028.
- "evaluator-annotator circularity" → §7 limitations §9.
- "artifact regeneration" → §15, issue 0031.
- "paper-craft P1-P12" → §16.
- "CI / warning filter" → §17, issue 0032.
- "V1.0/V1.1 dual-run" → §18, issue 0035, §10A `release_version`.
- "Held branches I/O/ST/V/O2" → §19.

---

## 15. Artifact regeneration matrix (Branch P, R9)

All 11 artifact types under `artifacts/` and `artifacts/sandbox/` were produced under pre-Decision-34 stack. Regenerated under LLM stack on 596 cases (V1.0) and again on full corpus (V1.1, post-issue-0035). Per-source split preserved. Old artifacts archived.

| Type | Aggregate file | Per-source variants | Regen scope | Notes |
|------|----------------|---------------------|-------------|-------|
| Attribution table | `attribution_table.csv` | longmemeval / memoryarena / toolbench | full 596 + 130 headline | columns added: `attribution_failed`, `failure_reason`, `baseline_evidence_score_llm`, bootstrap CI columns |
| Confusion matrix | `attribution_confusion_matrix.csv` | per-source | full 596 + 130 headline | cell values move under tie_margin=0.0 |
| Comparison metrics | `comparison_metrics.csv` | per-source | full 596 + 130 headline | 4 baselines, two-evaluator robustness, bootstrap CI |
| Post-repair table | `sandbox/post_repair_table.csv` | per-source | full 596 + 130 headline | recovered/partial split shifts; partial threshold τ column |
| Repair success | `sandbox/repair_success_table.csv` | longmemeval / memoryarena | full 596 | targeted vs hard-case |
| Repair label summary | `sandbox/repair_label_summary.csv` | — | full 596 | per-label distribution |
| Repair claim ledger | `sandbox/repair_claim_ledger.txt` | — | full 596 | aggregate |
| mem0 adapter parity | `mem0_adapter_parity.csv` | — | smoke + full 596 | new column `parity_under_llm_stack` |
| Letta adapter parity | `letta_adapter_parity.csv` | — | smoke + full 596 | same |
| Hook efficacy | `hook_efficacy_supplementary.csv` | — | full 596 | issue 0028 |
| At-scale raw | `at_scale_llm_retest.csv` | — | full 596 | 5960 rows; input to 0026/0028/0029 |
| ~~Recurrence comparison~~ | ~~`sandbox/recurrence_comparison.csv`~~ | — | **DROPPED (Q11)** | Experiment 1 supersedes; archived under `dropped/` |
| Hook calibration training | `hook_calibration/training_set_llm.npz` | — | full 596 | issue 0028 |
| V0V1 gate status | `V0V1_gate_review.txt` | — | regen under LLM | reflects new headline |
| Surrogate-gap | `surrogate_gap_llm.csv` | — | 50-case hold-out | issue 0036 |

### Archive scheme

- Pre-D34 artifacts → `artifacts/legacy_phrase_match_2026_05_22/` preserving subdirectory structure.
- `artifacts/legacy_phrase_match_2026_05_22/MANIFEST.txt`: scoring stack (`phrase_match + agent_generate=None`), `tie_margin=0.05`, dataset version (596 cases as of 2026-05-22), git commit SHA at archive time, one-line per-file role.
- Dropped recurrence_comparison → `legacy_phrase_match_2026_05_22/dropped/` with one-line note (superseded by Experiment 1).
- New artifacts get `artifacts/MANIFEST.txt`: scoring stack (qwen2.5-7b agent + evaluator-TBD scorer + `on_the_fly_baseline_rescore=True` + `tie_margin=0.0` + label-stripped replay context), dataset version, git commit SHA, evaluator model+version, one-line per-file role.
- 130-case headline artifacts → `artifacts/headline_130/` with separate MANIFEST referencing researcher subset version.
- Three semantic-shift annotations in MANIFEST.

### V1.1 regeneration

When issue 0035 lands:
- Pre-V1.1 artifacts → `artifacts/v1_0_596_2026_06_XX/` with MANIFEST.
- New full-corpus artifacts populate `artifacts/`.
- 130-case adjudication re-drawn from new corpus pool.
- All downstream issues re-run.

---

## 16. Paper presentation craft (Branch P + Branch S — R8 + R10)

Framing rules that govern how the same data is presented and ordered. Do not alter the data.

### P1 — Coverage-first headline framing

❌ "CMD failed to attribute 12 of 130 cases (9.2%); on the remaining 118 cases, Macro F1 = 0.82."
✅ "CMD attributes 90.8% of failure cases at operation level (118/130); on attributed cases, Macro F1 = 0.82 [95% CI 0.74–0.88], vs LLM-as-judge 0.74 [0.66–0.81]."

Lead with achievement (operation-level coverage), follow with rate.

### P2 — Baseline spread

Report all four baselines (random / evidence_recall / subagent_judge / llm_judge). Lead the table with the widest comparison; keep llm_judge prominent (falsification test).

### P3 — AttributionFailed as principled abstention

Conformal-prediction terminology: "CMD abstains on cases where no replay produces positive recovery gain — calibrated rejection of low-confidence diagnoses, analogous to selective coverage in conformal prediction." Cite Romano et al. 2019 or Vovk & Shafer 2008.

### P4 — Cost/latency as headline column (Q22)

Headline comparison table includes:
- `tokens_per_case` (agent + scorer + verifier subtotals separable)
- `wallclock_sec_per_case` (cold + warm)
- `usd_per_case` at evaluator pricing

No placeholder cost units are allowed in paper-facing artifacts. Replay count
(`10 replays`) is not cost. If token / wallclock / USD metadata is unavailable,
builders must emit blank numeric cells with `cost_metadata_status:
missing_cost_metadata`, not fabricate `cost_per_diagnosis`.

Hook supplementary table (issue 0028) reports same metrics under top-3 selection — separate deployment-optimized number.

### P5 — Per-label × per-source heatmap with bootstrap CI cells (Q23)

8 labels × 3 sources × accuracy. Each cell carries 95% CI from case-level bootstrap (1000 iterations). Aggregate Macro F1 below the heatmap, not as headline.

### P6 — High+medium confidence subset is headline; sensitivity reported (R4)

Two numbers: primary on N=110 high+medium; sensitivity on N=130 all-confidence.

### P7 — Level 2 capability as architecture milestone (Q12)

"CMD V1 demonstrates Level 2 (item-content) repair, deepest layer in runtime → memory-pipeline → item-content stack" + V2 cascade preview via `cascade_candidates`.

### P8 — Naming and table layout

- Name headline metric clearly (`CMD-Audit Macro F1`, not `top1_accuracy`).
- CMD at bottom of comparison tables (last row reads as conclusion).
- Bold winning cells.
- One decimal place; no spurious precision.

### P9 — Cross-dataset claim version-gated (Q20)

- V1.0 (06-10 arxiv): coverage claim only ("evaluated across three datasets representing distinct memory architectures; per-source results reported"). N too small for generalization.
- V1.1 (post-corpus, venue submission): explicit generalization ("CMD generalizes across long-context QA, multi-task shopping, tool-using agent domains; Macro F1 within X pp across sources").

### P10 — Online ~9/11 claim with retention% backing (Q18)

> "Online CMD-Skill Adapter recovers approximately 9 of 11 pipeline labels: 7 are intervention-mode replays not requiring gold evidence; 2 of the 4 remaining (write / compression / premature_extraction / injection) recover via self-supervision surrogate path with retention rate Y% relative to offline gold-evidence baseline (50-case hold-out). The remaining 2 labels stay gold-evidence-dependent and degrade to ECS reporting only."

The 2-of-4 success designation is post-hoc empirical (highest retention pair from surrogate-gap measurement).

### P11 — Multi-evaluator robustness on headline only (Q19)

Two evaluators on 130-case headline only. Methods section reports per-evaluator Macro F1 + agreement. Online deployment evaluator (Claude Code-style isolated subagent per Decision 19 / issue 0019 Phase B) separately described; offline-online evaluator transfer measurement = V2.

### P12 — Bootstrap CI everywhere meaningful (Q23)

Bootstrap (1000-iter case-level resampling) on:
- Macro F1
- Top-2 accuracy
- Per-label F1 (heatmap cells)
- Per-baseline numbers
- Cohen's κ (researcher↔deepseek)
- Cohen's κ (researcher_blind↔researcher_assisted, automation-bias)

CPU-only, no LLM cost.

### Anti-cherry-pick boundaries (binding)

- Evaluator chosen on independence + reproducibility before seeing CMD's number.
- Cases excluded only with explicit methods reason + sensitivity analysis.
- All 4 baselines in comparison; llm_judge appears (falsification test).
- τ, `tie_margin`, partial threshold tuned on held-out 30-50 manual subset (R3, R4); test set is 130-case adjudicated; post-hoc threshold tuning on test set is reportable as sensitivity only.
- All 8 active labels in 130-case headline; all 3 sources in per-source supplementary.
- Limitations §9 (evaluator-annotator circularity) stays exactly as written.

---

## 17. CI / warning configuration (Branch A, Q14)

`tests/conftest.py` filters `cmd_audit.PhraseMatchShortcutWarning` to `ignore` for the entire suite. Pytest still runs without `-W error`, CI green preserved.

Adapter parity tests (`test_cmd_audit_decision34_adapter_parity_llm.py`) cover Mem0Adapter + LettaAdapter under stub `agent_generate + scorer`, asserting label parity vs standalone harness on V0 smoke fixture, plus cross-adapter non-regression.

Label-leak invariant test rewrites `test_cmd_audit_repair_action_json_and_agent_loop.py:155-174`. New assertions: `BASELINE CONTEXT` and `COUNTERFACTUAL EVIDENCE BLOCK` in context; `CMD ATTRIBUTION LABEL` and every V1 label name **NOT** in context. Renamed to `test_replay_context_leak_free_invariant`.

`PhraseMatchShortcutWarning(DeprecationWarning)` exported via `cmd_audit/__init__.py`. Fired by both replays.py phrase-match shortcut and post_repair.py legacy substring path. Single category. Fixture `legacy_phrase_match_path` in conftest enables warning for tests asserting it fires.

---

## 18. V1.0 / V1.1 dual-run pattern (Branch S, R10)

V1.0 arxiv preprint at 06-10 binds to 596-derived 130-case headline; V1.1 venue submission post-issue-0035 binds to full-corpus 130-case headline. Same method, more data, stronger claims.

Issues with V1.0/V1.1 dual-run AC structure:

| Issue | V1.0 trigger | V1.1 trigger |
|-------|--------------|--------------|
| 0023 (at-scale re-test) | runs on 596 corpus | re-runs on full corpus when 0035 lands |
| 0024 (130-case adjudication) | sample from 596 | re-sample from full corpus pool |
| 0026 (Experiment 2 headline) | binds to V1.0 130-case | binds to V1.1 130-case |
| 0027 (Experiment 1) | binds to V1.0 80-case | binds to V1.1 80-case re-sampled |
| 0028 (hook calibration) | uses V1.0 re-test outputs | re-fits on V1.1 outputs |
| 0029 (coupled-failure) | uses V1.0 re-test outputs | re-runs on V1.1 |
| 0031 (artifact regen) | manifest references 596 | manifest references full corpus |
| 0036 (surrogate-gap) | V1.0 50-case hold-out | V1.1 proportionally-sized hold-out |

Each AC has "V1.0 satisfied | V1.1 satisfied" two-stage tracking. V1.0 unblocks arxiv preprint; V1.1 unblocks venue submission.

P9 + P10 cross-dataset claim is gated on V1.1.

---

## 19. Held but not yet integrated (Branches I / O / ST / V / O2)

Preview-only during grilling; not landed because user said "integrate" before grilling them:

- **Branch I (implementation contracts)**: AnswerVerifier return type, PostRepairResult dataclass field additions, warning class hierarchy, `on_the_fly_baseline_rescore` flag vs auto-on. ~3 questions.
- **Branch O (run orchestration)**: ollama crash mid-run resumability, checkpoint cadence, entry-point command, dry-run mode. ~2 questions.
- **Branch ST (statistical reporting)**: substantially closed by Q23 bootstrap. Remaining: power analysis pre-run, McNemar p-value adjustment for multiple-mode comparison. ~1 question.
- **Branch V (versioning)**: dataset hash in MANIFEST, cleaned_cases.json v1.0/v1.1 tagging. ~1 question.
- **Branch O2 (pre-commit framing)**: paper-craft framing for Macro F1 outcomes (CMD wins big / wins narrowly / ties / loses) BEFORE re-test runs. 1 question.

## 19. Branch I/O/ST/V resolutions (unilateral, 2026-05-24)

User authorized unilateral resolution for execution-quality branches. These are decisions the wiring sprint implements without further discussion.

### Branch I — Implementation contracts (R12)

- **`AnswerVerifier.verify` returns `str`** ∈ `{"EQUIVALENT", "NOT_EQUIVALENT"}`. Aligns with `EvidenceVerifier.verify(fact, text) -> str` ∈ `{"PRESENT", "ABSENT"}` per issue 0019 Phase B / `llm_scoring.py`. Caller converts to bool at use site: `recovered = (answer_verifier(answer, gold) == "EQUIVALENT")`. Symmetric with existing scoring contracts.
- **`PostRepairResult` dataclass field additions** (additive, backward-compatible):
  - `partial_threshold_used: float = 0.5` — records the τ that drove the assessment classification
  - `agent_answer: str = ""` — the agent's response under the LLM path; empty string under legacy substring path
  - `evidence_score_source: str = "phrase_match"` — `"phrase_match"` | `"subagent_scorer"` | `"external_scorer"`, distinguishes which scorer produced `post_repair_evidence_score`
  - `answer_verifier_verdict: str = ""` — `"EQUIVALENT"` | `"NOT_EQUIVALENT"` | `""` (when verifier not used)
  All default to backward-compat empty/baseline values so legacy callers remain functional.
- **Warning class hierarchy**: `class PhraseMatchShortcutWarning(DeprecationWarning)` per §11A. `DeprecationWarning` is correct (not `UserWarning`) because phrase-match is an actively-deprecated path being preserved for V0 unit-test determinism only. Default Python behavior: deprecation warnings shown to developers, hidden in production — exactly the desired user experience.
- **`on_the_fly_baseline_rescore` flag default**: `False`. Auto-on would surprise existing callers. Headline runs (issue 0023) explicitly pass `on_the_fly_baseline_rescore=True`; legacy callers and unit tests get unchanged behavior. Document in `harness.py` docstring: "Decision 34 R1 parity-baseline rescoring; required for paper-grade `recovery_gain` numbers."

### Branch O — Run orchestration (R13)

- **Resumability**: Issue 0023's at-scale run script writes `artifacts/at_scale_llm_retest.csv` incrementally — one row appended per `(case, replay)` completion. On crash, resume by reading existing CSV, deduping by `(case_id, replay_name)` composite key, skipping already-completed rows. Implementation: ~20 lines around the main loop. Implementer call.
- **Checkpoint cadence**: every case (not every replay). After all 10 replays for a case complete, flush + fsync the CSV. 596 cases × ~30 sec/case = manageable I/O.
- **Entry-point command**: `python -m cmd_audit run-v1 --real-data --use-llm-stack --evaluator-model <model> --tie-margin 0.0 --on-the-fly-baseline-rescore --no-hook --out-dir artifacts/`. New CLI flags `--use-llm-stack`, `--evaluator-model`, `--on-the-fly-baseline-rescore` added to `cmd_audit/cli.py` during the wiring sprint.
- **Dry-run mode**: `--dry-run --max-cases 5` runs the full pipeline on 5 cases for sanity validation before the overnight run. Standard pattern; implementer adds during wiring sprint.

### Branch ST — Statistical reporting (R14)

- **Power analysis pre-run**: Skip explicit pre-run. Bootstrap CI width on the V1.0 130-case headline directly reveals whether N is sufficient (CIs <±5pp = sufficient, ±10pp+ = wider). If V1.0 CIs are wide, V1.1 (full corpus) tightens them automatically.
- **McNemar p-value adjustment for Experiment 1 multi-mode comparison**: Bonferroni correction. Three pairwise tests (`Δ_1 = contrastive vs corrected_only`, `Δ_2 = contrastive vs corrected_only_padded`, `Δ_pad = corrected_only_padded vs corrected_only`). Adjusted α = 0.05 / 3 ≈ 0.017. Report both raw and Bonferroni-corrected p-values. Standard ML paper practice. Document in issue 0027 §6.3.

### Branch V — Versioning (R15)

- **Dataset hash in MANIFEST**: SHA-256 over sorted `case_id`s in the dataset. Both `legacy_phrase_match_2026_05_22/MANIFEST.txt` and new `artifacts/MANIFEST.txt` carry the hash. Issue 0035 V1.1 trigger generates a different hash (full corpus); the hash difference is the canonical version identifier.
- **`cleaned_cases.json` v1.0/v1.1 tagging**: top-level field `release_version: "v1.0"` | `"v1.1"` plus `dataset_hash: "<sha256>"`. Soft string for human readers; hash for machine verification. v1.0 archive at `data/cleaned_cases/v1_0_archive/cleaned_cases.json` after V1.1 cutover.

### Branch O2 — Pre-commit framing for V1.0 Macro F1 outcomes (R16)

User authorized 2026-05-24. Bucket W1 prioritized as the target outcome; W2/T/L are progressively weaker fallbacks. Bucket-L-don't-ship rule accepted.

Pre-registered framing for V1.0 headline (CMD high+medium-confidence Macro F1 vs `llm_judge` on 130 adjudicated cases):

| Bucket | CMD vs llm_judge gap | Pre-committed framing |
|---|---|---|
| **W1 — wins big (target)** | +0.15 or more | Lead paper with: "CMD significantly outperforms LLM-as-judge baseline (Δ = X pp [95% CI ...])." Per-source heatmap and per-label F1 support. P1+P2+P5 paper-craft applied at full strength. |
| **W2 — wins narrowly** | +0.05 to +0.15 | Headline: "CMD outperforms LLM-as-judge (Δ = X pp [95% CI ...]); margin is modest but consistent across labels (per-label F1 strictly higher in K of 8 labels)." Honest "reliable, not large" framing. Heatmap is the supporting evidence. |
| **T — ties** (within ±0.05) | -0.05 to +0.05 | Pivot to structural properties: "CMD attribution accuracy matches LLM-as-judge while providing operation-level granularity (LLM-as-judge does not), bounded latency, replay provenance, and X% lower per-case cost." Cost/latency table (P4) carries the argument. Methods finding, not failure. |
| **L1 — loses, coverage OK** | < -0.05, coverage% ≥ 80% | Honest concession + structural-advantage repositioning: "CMD provides operation-level + provenance + audit at per-case cost X; accuracy is below LLM-as-judge by Y pp on attributed cases, but CMD covers Z% with traceable replay evidence." Ship V1.0 with explicit concession in abstract. |
| **L2 — loses, coverage low (don't ship)** | < -0.05 AND coverage% < 80% | **V1.0 arxiv 06-10 does not ship.** Decision 34 reopens. Triggers: (a) re-investigate evaluator/scorer choices for stack issues, (b) re-examine 130-case adjudication quality, (c) accept method not ready, defer paper. No write-up rescue. |

**Bucket-L2 hard rule (binding)**: if CMD high+medium-confidence Macro F1 < llm_judge - 0.05 AND CMD coverage% (attributed / total) < 80% on the 130-case headline, V1.0 arxiv preprint does not ship per 06-10 deadline. Decision 34 reopens for method-level review.

**Anti-framing-rationalization protection**:
- Wider tie than ±0.05 (e.g., ±0.10) does NOT qualify for bucket T pivot. Falls into L1 or L2 by gap magnitude.
- "CMD provides X new capabilities" framing is bucket L1 only when coverage ≥ 80% AND accuracy gap < 0.05 below llm_judge. Cannot be invoked to rescue an L2 outcome.
- Cost/latency/provenance arguments are *supporting* in W1/W2 and *primary* in T/L1. Never substitute for the accuracy headline; always reported.
- Bucket assignment locked by the outcome, not by what makes the paper most publishable.

**Operationalization**:
- Issue 0026 acceptance criteria add: "Compute (CMD_macro_f1, llm_judge_macro_f1, CMD_coverage_pct) on 130 high+medium subset. Determine bucket per R16 table. Persist to `artifacts/headline_130/bucket.txt`."
- Paper draft skeleton (issue 0030 expansion target post-V1.0 run) includes 5 alternative-text-blocks corresponding to W1/W2/T/L1/L2. Drafted before re-test; one block selected by bucket determination.
- L2 trigger fires a re-grilling session via Decision 34 reopening, not an issue 0026 acceptance failure. Issue 0026 marks complete with "L2 outcome — paper deferred."

### Branch Cost — Cost field schema (R17)

Paper-facing D34 builders must use this schema for CMD cost/latency:

- `agent_tokens`
- `scorer_tokens`
- `verifier_tokens`
- `tokens_total`
- `wallclock_sec`
- `usd_cost`
- `cost_metadata_status` ∈ `{"measured", "missing_cost_metadata"}`

Comparison/headline tables aggregate these into:

- `tokens_per_case`
- `agent_tokens_per_case`
- `scorer_tokens_per_case`
- `verifier_tokens_per_case`
- `wallclock_sec_per_case`
- `usd_per_case`
- `cost_metadata_status`

Hardcoded pseudo-costs such as `cost_per_diagnosis=10.0` are forbidden for D34
paper artifacts. If the upstream 0023 CSV lacks real cost columns, downstream
builders preserve metric computation but leave paper cost cells blank and mark
`missing_cost_metadata`.

---

End of REPAIR.md.
