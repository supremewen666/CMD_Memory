# CMD V0→V1 Gate Status

**Last checked:** 2026-05-22 (at scale: 596 labeled real-data cases)
**Gate ID:** V0→V1
**Status:** V0 LOCKED — HITL approved (supremewen, 2026-05-10); at-scale validation PASS (2026-05-22)

## Gate Criteria

V0→V1 requires four V0 evidence artifacts to pass paper-claim thresholds (per PRD AC10 and issue 0010).

### Criterion 1: Macro F1 exceeds baselines

- **Artifact:** `artifacts/comparison_metrics.csv`
- **Threshold:** CMD-Audit macro_f1 > evidence_recall AND subagent_judge AND random_label
- **Result:** PASS
- **Evidence:** CMD-Audit macro_f1=1.000; evidence_recall=0.778; subagent_judge=0.778; random_label=0.167
- **Note:** Perfect macro F1 on 6 smoke cases (1 per label). Expected to regress toward realistic values as probe case count increases toward 50-100.

### Criterion 2: Confusion matrix diagonal dominance

- **Artifact:** `artifacts/attribution_confusion_matrix.csv`
- **Threshold:** For each V0 label row: diagonal > sum of off-diagonal entries
- **Result:** PASS
- **Evidence:** All 6 V0 labels have diagonal=1, off-diagonal_sum=0 (perfect identity matrix on smoke cases).
- **Note:** With 1 case per label, off-diagonal is structurally empty. Diagonal dominance must hold as the probe suite scales.

### Criterion 3: Attribution accuracy and top-2 exceed baselines

- **Artifact:** `artifacts/comparison_metrics.csv`
- **Threshold:** CMD-Audit attribution_accuracy > all baselines AND top2_accuracy > all baselines
- **Result:** PASS
- **Evidence:** CMD-Audit attribution_accuracy=1.000 (best baseline=0.833); CMD-Audit top2_accuracy=1.000 (best baseline=0.833).

### Criterion 4: Repair assessment distribution

- **Artifact:** `artifacts/sandbox/post_repair_table.csv`
- **Threshold:** recovered_rate >= 0.5 AND recovered + partial > failed
- **Result:** PASS
- **Evidence:** 6 cases: recovered=6, partial=0, failed=0 (recovered_rate=1.000).
- **Note:** 4/6 cases show `had_repair_regression=true`, indicating the targeted repair introduced regression on the hard-case baseline comparison axis. This does not fail the gate criterion but should be noted in the HITL review.

## At-Scale Validation (2026-05-22)

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

All four criteria re-validated against the full 596-case labeled real-data suite (200 LongMemEval + 198 MemoryArena + 198 ToolBench). Pipeline: V1 full (10 replays + Post-Repair Context Replay) via `run_case_full_v1`.

### Results

| Criterion | Result | Evidence |
|-----------|--------|----------|
| C1: Macro F1 > baselines | **PASS** | Macro F1=1.000, Attribution Accuracy=1.000, Top-2=1.000. CMD correctly attributes all 596 cases. |
| C2: Diagonal dominance | **PASS** | All 8 active labels (33-132 cases each) have perfect diagonal, zero off-diagonal. |
| C3: Top-2 > baselines | **PASS** | Top-2 accuracy=1.000 on all 596 cases. |
| C4: Repair assessment | **PASS** | recovered=506 (84.9%), partial=90 (15.1%), failed=0 (0.0%). recovered+partial (596) >> failed (0). |

### Active Label Distribution (596 cases)

| Label | Count | Accuracy |
|-------|-------|----------|
| `retrieval_error` | 132 | 1.000 |
| `injection_error` | 111 | 1.000 |
| `premature_extraction_error` | 83 | 1.000 |
| `reasoning_error` | 83 | 1.000 |
| `compression_error` | 54 | 1.000 |
| `ingestion_error` | 50 | 1.000 |
| `route_error` | 50 | 1.000 |
| `write_error` | 33 | 1.000 |

Three V1 passthrough labels (`granularity_error`, `graph_error`, `safety_error`) have 0 cases in the current real-data suite. They are exercisable via synthetic probe cases (issue 0012).

### Comparison to Smoke (6 cases → 596 cases)

| Metric | Smoke (6) | At-Scale (596) | Δ |
|--------|-----------|-----------------|---|
| Macro F1 | 1.000 | 1.000 | — |
| Attribution Accuracy | 1.000 | 1.000 | — |
| Repair recovered rate | 1.000 | 0.849 | −0.151 |
| Repair partial rate | 0.000 | 0.151 | +0.151 |
| Repair failed rate | 0.000 | 0.000 | — |

The key difference: `partial` repairs emerge at scale (15.1%) — cases where a targeted repair improves the answer but not to equivalence with the gold baseline. This is the expected partial-fix signal that the smoke suite could not produce.

## 2026-05-23/24 Re-test Plan (post-grilling, Decision 34)

V1.0 (596-case dataset state, target 06-10 arxiv preprint) and V1.1 (full-corpus dataset state, post-issue-0035, target venue submission) are tracked separately.

| Step | What | Owner | V1.0 Target | V1.1 Trigger |
|------|------|-------|-------------|--------------|
| Wire LLM eval infra (issue 0022) | `agent_generate` flowing through `run_case_full_v1`; `AnswerVerifier` wired into `run_post_repair_context_replay`; label string dropped; on-the-fly baseline rescore of `vector_memory` baseline | engineering | 05-25~28 | (no rerun needed) |
| At-scale re-test (issue 0023) | 596 × 10 replays under qwen2.5-7b agent + independent scorer + on-the-fly baseline rescore | engineering | 05-28~30 | issue 0035 lands |
| Headline adjudication set (issue 0024) | 130 researcher-labeled cases + LLM-A (llama-3.3-70b-instruct) suggest + 20-case blind spot-check | researcher | 05-30~01 (~5 hr) | re-sample post-0035 |
| Cohen's κ vs deepseek annotator | report agreement on 130-case overlap; bootstrap CI | researcher | 06-02 | re-compute post-0035 |
| deepseek labeling provenance recovery (issue 0033) | check into `scripts/annotate_perturbation_labels.py`, reference in `cleaning_report.txt` | researcher | 05-25 | (one-time) |
| Final V0→V1 gate decision | re-evaluate four criteria under new stack on adjudication set + 596 sanity check; bootstrap CIs | HITL | 06-03 V1.0 | 06-10+ V1.1 |

The 2026-05-22 evidence rows above remain on file as the pre-grilling snapshot.

## HITL Review Log

| Date | Reviewer | Decision | Notes |
|------|----------|----------|-------|
| 2026-05-10 | supremewen | approved | Initial evidence check complete. All four criteria pass on 6-case smoke suite. |
| 2026-05-22 | automated | at-scale validation | All four criteria validated on 596 labeled real-data cases. Macro F1=1.000, repair recovered=84.9%, partial=15.1%, failed=0%. |

## V1 Integration Plan (2026-05-11)

### First Adapter Target: mem0

- **System:** mem0ai/mem0 (55,320 GitHub stars, YC S24)
- **API:** `add()` → entity linking → `search()` (semantic + BM25 + entity matching)
- **SOTA:** LoCoMo 91.6, LongMemEval 93.4 (v3 algorithm, April 2026)
- **Integration points:** `add()` interception for write-side replays; `search()` interception for retrieval-side replays
- **CMD replay mapping:** Oracle Write, Oracle Compression, Verbatim Event Oracle, Oracle Retrieval, Injection-Oracle, Evidence-Given Reasoning — all six V0 replays map to mem0 operations

### Second Adapter Target: Letta

- **System:** letta-ai/letta (22,609 GitHub stars)
- **Architecture:** core/archival/recall memory tiering
- **Integration value:** exercises `route_error` (memory tier selection) that mem0's flat store cannot test
- **Timing:** after mem0 integration proven, for V1→V2 gate (requires ≥2 agents)

### V1 Label Expansion Plan

Priority order: `ingestion_error` → `route_error` → `granularity_error` → `graph_error` → `safety_error`

Gate check: 11-label macro F1 must not regress from 6-label V0 baseline.

### Adjacent Work to Cite

**memory-probe (arXiv:2603.02473):** Boqin Yuan et al. independently validate the write-vs-retrieval diagnostic framing using 3×3 grid comparison on LoCoMo. Observational, not counterfactual. Closest existing work to CMD.

## V1→V2 Gate (PASS — 2026-05-19)

The V1→V2 gate requires at least two distinct memory agents integrated through the Adapter Interface without macro F1 regression. Both mem0 (issue 0014) and Letta (issue 0015) adapters are integrated and pass adapter-label parity on V0 smoke suite (Macro F1 = 1.000 each). Cross-agent non-regression verified. See `../gates/V1V2_gate_status.md` for full details.
