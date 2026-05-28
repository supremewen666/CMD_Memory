# CMD-Audit V1.0 Implementation Plan — D35 Route E

**Date:** 2026-05-28
**Scope:** 6-week implementation plan from refactor through arxiv preprint
**Target:** arxiv 2026-07-08; AAAI 2027 deadline ~2026-08-end (6 weeks revision window)
**Inputs:**
- `selection_res.md` (root) — Route E selection rationale
- `cmd_innovation_core/plans/cmd_open_decisions.md` — Decision 35 R1-R9 binding
- `knowledge/current-memory.md` — V0/V1 status, refactor plan
- Prior conversation — refactor skeleton (10 subpackages, hard cut, no shim)
- `artifacts/at_scale_llm_retest.csv` — 596-case LLM rescore basis

**Skill adaptation note:** Standard `/research-implement` workflow assumes greenfield ML project (`project/`, `run.py`, `[RESULT]` lines). This is a 39-file research codebase with 803 existing tests, zero PyPI dependencies, pytest-driven. Adapting: this document is the executable plan; verification commands use `pytest`/artifact regeneration; per-week gate criteria replace per-epoch loss curves. No fabrication of execution results — every gate has a falsifiable check.

---

## Pre-flight Inventory

Before W1 starts, run this dry-run and record the numbers. If any number changes during refactor, that's a regression:

```bash
cd /Users/supremewen/CMD_Counterfactual_Memory_Debugger
python -m pytest tests/ -q --co | tail -5            # collected tests
python -m pytest tests/ -q 2>&1 | tail -3            # current pass/fail
rg "from baselines" -l --type py | wc -l             # baselines import sites
rg "V0_PIPELINE_LABELS|V1_PIPELINE_LABELS" -l --type py | wc -l
rg "run_case_v1|run_case_full_v1|run_case_v1_with_hook" -l --type py | wc -l
rg "validate_v0_label|validate_v1_label" -l --type py | wc -l
rg "assign_attribution_v1" -l --type py | wc -l
rg "run_v0_replay_portfolio|run_v1_replay_portfolio" -l --type py | wc -l
```

Record in `refactor_baseline.txt`:
- Test count: ___
- Pass/fail status: ___ pass, ___ fail
- 6 grep counts as integers

Each W1 daily gate compares against this baseline.

---

## W1 — Refactor (5/29-6/4, 7 daily commits)

Goal: cmd_audit/ uses 10 subpackages, harness has 3 entry points, baselines/ lives inside cmd_audit/, no V0/V1 in code identifiers, 803 tests still pass.

### Day 1 (5/29) — core/ scaffold + label rename

**Tasks:**
1. Create `cmd_audit/core/` directory
2. Move:
   - `cmd_audit/models.py` → `cmd_audit/core/models.py`
   - `cmd_audit/labels.py` → `cmd_audit/core/labels.py`
   - `cmd_audit/constants.py` → `cmd_audit/core/constants.py`
   - `cmd_audit/llm_client.py` → `cmd_audit/core/llm_client.py`
3. Rename in `core/labels.py`:
   - `V0_PIPELINE_LABELS` → `PIPELINE_LABELS_BASE` (intermediate; will become `PIPELINE_LABELS = (10)` at end of D35 work in W2)
   - `V1_PIPELINE_LABELS` → `EXTENDED_LABELS` (intermediate)
   - `V1_REPLAY_TO_LABEL` → `REPLAY_TO_LABEL`
   - `validate_v0_label` → `validate_label_base`
   - `validate_v1_label` → `validate_label`
   - `DEFERRED_PIPELINE_LABELS` keep as-is (semantic, not version)
4. Create `cmd_audit/core/__init__.py` with explicit re-exports
5. **Hard cut**: update all imports across codebase (use `rg` to find sites)
6. Delete `cmd_audit/warnings.py`, fold its 2 symbols into `cmd_audit/__init__.py`

**Verification gate (must all pass):**
```bash
python -m pytest tests/ -q 2>&1 | tail -3        # 803 pass, 0 fail
rg "from cmd_audit\.models" --type py -q          # 0 hits (all moved to core)
rg "V0_PIPELINE_LABELS|V1_PIPELINE_LABELS" --type py -q  # 0 hits
rg "validate_v[01]_label" --type py -q            # 0 hits
```

**Commit:** `[refactor] Day 1: core/ subpackage + label rename`

**Rollback:** if any verification fails, `git reset --hard HEAD~1` and restart Day 1 with smaller batches.

### Day 2 (5/30) — replays/ + scoring/ split

**Tasks:**
1. Create `cmd_audit/replays/` and `cmd_audit/scoring/`
2. Split `cmd_audit/replays.py` (42 symbols) into:
   - `replays/interventions.py` — 10 replay implementations (oracle_write..safety_off)
   - `replays/portfolio.py` — `run_replay_portfolio` (replaces `run_v0_replay_portfolio`/`run_v1_replay_portfolio`)
   - `replays/_scoring_bridge.py` — `_score_recovered_evidence` and helpers (private)
3. Split `cmd_audit/scoring.py` + `cmd_audit/llm_scoring.py` + `cmd_audit/retrieval_baselines.py` into:
   - `scoring/phrase.py` — `answer_score`, `evidence_recall_from_text`
   - `scoring/llm.py` — `SubagentScorer`, `EvidenceVerifier`, `AnswerVerifier`
   - `scoring/retrieval.py` — `compute_bm25_scores`, `tokenize`, `RetrievalMetrics`
   - `scoring/factories.py` — `build_evidence_scorer`, `build_answer_verifier`
4. Update all imports across codebase

**Verification gate:**
```bash
python -m pytest tests/ -q 2>&1 | tail -3
rg "from cmd_audit\.replays " --type py -q       # 0 hits (now subpackage)
rg "run_v[01]_replay_portfolio" --type py -q     # 0 hits
rg "from cmd_audit\.(scoring|llm_scoring|retrieval_baselines)" --type py -q  # 0 hits
```

### Day 3 (5/31) — attribution/ + zero-gain unification

**Tasks:**
1. Create `cmd_audit/attribution/`
2. Split `cmd_audit/attribution.py` into:
   - `attribution/ranking.py` — recovery-gain ranking + close_deltas
   - `attribution/shadow.py` — `_disambiguate_route_retrieval_shadow`
   - `attribution/failure.py` — zero/negative-gain handler **(unified)**
3. **Critical fix** (D35 unblocks post_repair crash):
   - Old: `attribution.py:140` raises `ValueError("no replay produced a positive recovery gain")`
   - New: returns `AttributionResult(attribution_failed=True, failure_reason="zero_gain"|"negative_gain", predicted_label=None, ...)`
4. Update `harness.py` (still old layout) to consume new result shape
5. Merge `assign_attribution_v1` and `assign_attribution` into single `assign_attribution`. Distinguish V1 features via kwargs (`has_ingestion_trace=`, `top_k=`).

**Verification gate:**
```bash
python -m pytest tests/ -q 2>&1 | tail -3        # 803 pass
rg "assign_attribution_v1|assign_attribution_v0" --type py -q  # 0 hits
python -m pytest tests/test_cmd_audit_decision34_post_repair_subset_runner.py -v  # post_repair runs without crash
```

### Day 4 (6/1) — repair/ 5-file consolidation

**Tasks:**
1. Create `cmd_audit/repair/`
2. Move + reorganize:
   - `cmd_audit/post_repair.py` → `repair/post_repair.py` (ECSDraft, RepairedContext, run_post_repair_context_replay)
   - `cmd_audit/repairs.py` → `repair/actions.py` (RepairAction, action_type taxonomy, tool schema)
   - `cmd_audit/repair_executor.py` → `repair/executor.py`
   - `cmd_audit/repair_orchestrator.py` → `repair/orchestrator.py`
   - `cmd_audit/failure_memory.py` → `repair/failure_memory.py`
3. Extract `ECSDraft` → `repair/ecs.py` (was inside post_repair.py)
4. Update all imports

**Verification gate:**
```bash
python -m pytest tests/ -q 2>&1 | tail -3
rg "from cmd_audit\.(post_repair|repairs|repair_executor|repair_orchestrator|failure_memory)" --type py -q  # 0 hits
```

### Day 5 (6/2) — adapters/ flatten + baselines/ migration

**Tasks:**
1. Adapter consolidation:
   - `adapters/mem0_adapter.py` + `adapters/mem0_replays.py` → `adapters/mem0.py`
   - `adapters/letta_adapter.py` + `adapters/letta_replays.py` → `adapters/letta.py`
   - Keep `adapters/base.py`, `adapters/harness.py`, `adapters/_shared.py`
   - Keep `adapters/_replay_skeleton.py` as private template
2. **Baselines migration (the one external-facing change):**
   - `git mv baselines/ cmd_audit/baselines/`
   - Update all `from baselines import X` → `from cmd_audit.baselines import X`
   - Update `CLAUDE.md` (root): change "external comparator package" → "cmd_audit.baselines subpackage"
   - Verify no scripts/CI scripts reference old path

**Verification gate:**
```bash
python -m pytest tests/ -q 2>&1 | tail -3
rg "from baselines " --type py -q                # 0 hits
test -d baselines && echo "FAIL: old baselines/ still exists" || echo "OK"
test -d cmd_audit/baselines && echo "OK" || echo "FAIL"
```

### Day 6 (6/3) — eval/ + data_io/ + harness 3-entry

**Tasks:**
1. Create `cmd_audit/eval/`
2. Move:
   - `cmd_audit/metrics.py` → `eval/metrics.py`
   - `cmd_audit/agreement.py` → `eval/agreement.py`
   - `cmd_audit/bootstrap.py` → `eval/bootstrap.py`
   - `cmd_audit/writers.py` → `eval/writers.py`
   - `cmd_audit/version_gates.py` → `eval/release_gates.py` (rename: drop "version" connotation per D35 framing)
   - `cmd_audit/provenance.py` → `eval/provenance.py`
   - `cmd_audit/surrogate_gap.py` → `eval/surrogate_gap.py`
3. Create `cmd_audit/data_io/`
4. Extract case-loading from `core/models.py` into:
   - `data_io/probe_cases.py` — `load_probe_cases` (drop `_v1` suffix)
   - `data_io/real_data.py` — `load_all_real_cases`, `load_real_cases_by_source`
5. **Harness consolidation (the centerpiece):**
   - Old 5 entries: `run_case`, `run_case_v1`, `run_case_full_v1`, `run_case_v1_with_hook`, `run_case_v1_with_hook_and_repair`
   - New 3 entries: `run_case`, `run_cases`, `run_real_suite`
   - Behavior controlled by kwargs: `agent_generate=None`, `hook=None`, `repair=False`, `scorer=...`
   - All call sites in tests + scripts updated

**Verification gate:**
```bash
python -m pytest tests/ -q 2>&1 | tail -3
rg "run_case_v1|run_case_full_v1|run_case_v1_with_hook" --type py -q  # 0 hits
rg "from cmd_audit\.(metrics|writers|version_gates|provenance|surrogate_gap|agreement|bootstrap)" --type py -q  # 0 hits
```

### Day 7 (6/4) — final cleanup + tests rename + public API trim

**Tasks:**
1. Delete any leftover deprecated files
2. Rename test files to mirror subpackage structure:
   - `tests/test_cmd_audit_issueNN_*.py` → `tests/{subpkg}/test_*.py`
   - Drop issue-number naming (post-issue-resolution they're noise)
3. Trim `cmd_audit/__init__.py` from ~200 symbols to ~80 by selective re-exports
4. Update CLAUDE.md (root) sections:
   - Project structure
   - Commands (paths)
   - Code Architecture table
5. Update `knowledge/current-memory.md` — sync new module map
6. Final run

**Verification gate (W1 exit criteria):**
```bash
# All must pass
python -m pytest tests/ -q 2>&1 | tail -3              # 803 pass
python -c "from cmd_audit import run_case, run_cases, run_real_suite"
python -c "from cmd_audit import PIPELINE_LABELS_BASE, ALL_LABELS"  # exists
python -c "from cmd_audit.baselines import run_baseline_suite"
python -c "import cmd_audit; print(len(dir(cmd_audit)))"  # ≤ 100 public
test ! -d baselines && test -d cmd_audit/baselines && echo "OK"
test ! -f cmd_audit/warnings.py && echo "OK"
```

**W1 → W2 gate:** if any verification fails, **do not proceed to W2**. Diagnose root cause, fix, rerun. Refactor must be airtight before feature work begins.

---

## W2 — Hook re-calibration + 10-label headline (6/5-6/11)

Goal: PIPELINE_LABELS finalized at 10 (drop reasoning_error), `ALL_LABELS = PIPELINE_LABELS + ("reasoning_error",)`, hook recalibrated using D34 R5 protocol (consume at-scale outputs), 596-case headline regenerated under new label set.

### Day 8-9 (6/5-6/6) — Label registry finalization + calibrate_hook refactor

**Tasks:**
1. In `core/labels.py`:
   - `PIPELINE_LABELS = (write_error, compression_error, premature_extraction_error, retrieval_error, injection_error, ingestion_error, route_error, granularity_error, graph_error, safety_error)` — 10 labels
   - `ALL_LABELS = PIPELINE_LABELS + ("reasoning_error",)`
   - Update `validate_label` to default to `PIPELINE_LABELS` membership; `validate_label(x, allow_reasoning=True)` opens to `ALL_LABELS`
2. Update `eval/metrics.py::compute_diagnosis_metrics`:
   - default `labels=PIPELINE_LABELS`
   - new `coverage_aware: bool = False` mode (failed cases counted as abstain, not FN)
3. Refactor `scripts/calibrate_hook.py` per D34 R5:
   - Input: `artifacts/at_scale_llm_retest.csv` (already LLM-rescored)
   - Output: new `RPE_JUDGE_WEIGHTS`, `RPE_JUDGE_INTERCEPT`, `TOP_K`, `FALLBACK_THRESHOLD`
   - Stratified train/holdout split (546/50)
4. Tests:
   - Update `tests/eval/test_metrics.py` for `coverage_aware=True` semantics
   - Update `tests/core/test_labels.py` for the new registry

**Verification gate:**
```bash
python -m pytest tests/core/test_labels.py tests/eval/test_metrics.py -v
python -c "from cmd_audit import PIPELINE_LABELS, ALL_LABELS; print(len(PIPELINE_LABELS), len(ALL_LABELS))"  # 10 11
```

### Day 10 (6/7) — Hook recalibration

**Tasks:**
1. Run `python scripts/calibrate_hook.py --input artifacts/at_scale_llm_retest.csv --output cmd_audit/hook/constants.py`
2. Inspect output:
   - Three deferred replay weights (granularity/graph/safety) — should differ if calibration set covers them; if still bit-identical, document as expected (no positive-gain signal in 596 set for these labels)
   - oracle_route weight should be ≤ +0.5 (down from previous +0.64 if data quality fix applied)
   - FALLBACK_THRESHOLD should be > 0.0 (raise from 0.0 due to D35 risk concern)
3. Run hold-out validation
4. Commit new constants.py with note explaining each parameter

**Verification gate:**
```bash
python -m pytest tests/hook/ -v
python -c "from cmd_audit.hook.constants import RPE_JUDGE_WEIGHTS, FALLBACK_THRESHOLD; assert FALLBACK_THRESHOLD >= 0.15, 'fallback too aggressive'"
```

If FALLBACK_THRESHOLD calibrates to <0.15, manually override to 0.20 with comment in constants.py and document in §Limitations as "calibration produced unsafe value, manually clamped per D35 R8".

### Day 11-12 (6/8-6/9) — 10-label headline rerun

**Tasks:**
1. Update `scripts/build_experiment_02_tables.py`:
   - `HEADLINE_LABELS = PIPELINE_LABELS` (10 labels, no reasoning)
   - Reasoning case bookkeeping: pass-through but exclude from headline F1 calculation; report separately
2. Run `python scripts/build_experiment_02_tables.py --out artifacts/headline_v2/`
3. Compare against `artifacts/headline_130_original_labels/`:
   - mF1 should rise from 0.629 to ~0.72 (per offline analysis)
   - coverage from 0.681 to ~0.873
   - 10 labels, not 8 or 11
4. Regenerate `comparison_metrics.csv` for evidence_recall, subagent_judge, llm_judge baselines on the 10-label scope

**Verification gate:**
```bash
test -f artifacts/headline_v2/experiment_02_headline.csv
python -c "import csv; r=list(csv.DictReader(open('artifacts/headline_v2/experiment_02_headline.csv'))); assert float(r[0]['macro_f1']) > 0.65, 'mF1 regression'"
```

### Day 13 (6/10) — W2 closeout

**Tasks:**
1. Generate W2 summary report under `artifacts/W2_summary.md`
2. Update `current-memory.md` with W2 results
3. Decide W3 readiness gate:
   - 803 tests pass: ✓
   - New constants.py committed: ✓
   - 10-label headline mF1 ≥ 0.65: ✓ or fail
   - All baselines rerun: ✓

**W2 → W3 gate:** if 10-label mF1 < 0.65, investigate (likely a refactor regression). Do not proceed to W3 until ≥ 0.65.

---

## W3 — R1 reasoning subtype decomposition (6/12-6/18)

Goal: `assign_attribution` emits `reasoning_subtype` tag on reasoning_error gold cases. Subtype-based ablation tables published. Narrow-class reasoning F1 reported.

### Day 14-15 (6/12-6/13) — Subtype tagger

**Tasks:**
1. Add to `attribution/failure.py`:
```python
@dataclass(frozen=True)
class ReasoningSubtype:
    UPSTREAM_RECOVERABLE: str = "context_recoverable"
    EVIDENCE_DEPENDENT: str = "evidence_dependent_reasoning"
    HARD: str = "hard_reasoning"
    AMBIGUOUS: str = "ambiguous"

def classify_reasoning_subtype(replay_results, *, gain_threshold=0.0) -> str:
    upstream_replays = ("oracle_write","oracle_compression","verbatim_event_oracle",
                        "oracle_retrieval","injection_oracle","oracle_route",
                        "oracle_granularity","graph_off","safety_off")
    egr_gain = next((r.recovery_gain for r in replay_results
                     if r.replay_name == "evidence_given_reasoning"), 0.0)
    upstream_gains = [r.recovery_gain for r in replay_results
                      if r.replay_name in upstream_replays]
    upstream_max = max(upstream_gains, default=0.0)

    upstream_pos = upstream_max > gain_threshold
    egr_pos = egr_gain > gain_threshold

    if upstream_pos and egr_pos:
        return ReasoningSubtype.AMBIGUOUS
    if upstream_pos:
        return ReasoningSubtype.UPSTREAM_RECOVERABLE
    if egr_pos:
        return ReasoningSubtype.EVIDENCE_DEPENDENT
    return ReasoningSubtype.HARD
```
2. Extend `AttributionResult` (in `attribution/__init__.py` or `core/models.py`):
```python
@dataclass(frozen=True)
class AttributionResult:
    ...  # existing fields
    reasoning_subtype: str | None = None  # set when gold_label == "reasoning_error"
```
3. Wire `classify_reasoning_subtype` into `assign_attribution` — call only when `case.perturbation_label == "reasoning_error"`
4. For `UPSTREAM_RECOVERABLE` subtype: remap `predicted_label` to the upstream replay's label per `REPLAY_TO_LABEL`
5. For `HARD` subtype: set `attribution_failed=True, failure_reason="hard_reasoning_capability_ceiling"`
6. For `EVIDENCE_DEPENDENT` subtype: keep `predicted_label="reasoning_error"`
7. For `AMBIGUOUS`: keep `predicted_label="reasoning_error"` but add to a separate ablation report column

**Tests** in `tests/attribution/test_reasoning_subtype.py`:
- 4 test classes, one per subtype
- Use synthetic recovery_gain fixtures matching each pattern
- Test edge cases: zero gain on all (HARD), all positive (AMBIGUOUS)

**Verification gate:**
```bash
python -m pytest tests/attribution/test_reasoning_subtype.py -v
python -m pytest tests/ -q 2>&1 | tail -3       # full suite still 803 + new tests
```

### Day 16 (6/14) — Ablation script

**Tasks:**
1. Create `scripts/build_reasoning_ablation.py`:
   - Input: `artifacts/at_scale_llm_retest.csv`
   - Apply `classify_reasoning_subtype` to each reasoning_error gold case
   - Compute:
     - Subtype distribution (expected: ~27 / 24 / 32 per offline analysis)
     - Narrow-class reasoning F1 (TP/FP/FN on EVIDENCE_DEPENDENT cases only)
     - Subtype × source heatmap
     - Cohen's κ vs deepseek labels (if researcher_labeled_subset.json available)
2. Outputs:
   - `artifacts/reasoning_ablation/subtype_distribution.csv`
   - `artifacts/reasoning_ablation/narrow_class_f1.csv` (with bootstrap CI)
   - `artifacts/reasoning_ablation/subtype_by_source.csv`

**Verification gate:**
```bash
python scripts/build_reasoning_ablation.py --input artifacts/at_scale_llm_retest.csv --out artifacts/reasoning_ablation/
test -f artifacts/reasoning_ablation/subtype_distribution.csv
python -c "
import csv
d = {r['subtype']:int(r['count']) for r in csv.DictReader(open('artifacts/reasoning_ablation/subtype_distribution.csv'))}
total = sum(d.values())
assert total == 83, f'expected 83 reasoning cases, got {total}'
print(d)
"
# Expected: context_recoverable ~27, evidence_dependent_reasoning ~24, hard_reasoning ~32, ambiguous ~0
```

### Day 17-18 (6/15-6/16) — Headline integration + paper §Method draft

**Tasks:**
1. Update `scripts/build_experiment_02_tables.py` to:
   - Apply subtype remapping (UPSTREAM_RECOVERABLE → upstream label)
   - Compute headline F1 with subtype-adjusted predictions
   - Emit `reasoning_narrow_f1` column alongside `macro_f1`
2. Rerun headline:
```bash
python scripts/build_experiment_02_tables.py --out artifacts/headline_v3/
```
3. Expected numbers (per offline projection):
   - Macro F1 (10 labels) ≈ 0.74-0.76 (lift from 0.72 due to UPSTREAM_RECOVERABLE remap freeing FP from route/write)
   - Narrow reasoning F1 ≈ 0.70-0.95 (24 cases, depends on calibration noise)
   - Coverage ≈ 0.88
4. Draft `paper/sections/method_reasoning_subtype.md` (~1.5 pages):
   - Subtype definition + algorithm
   - Cite 2605.15000 Premature Closure for HARD ceiling
   - Cite 2604.27283 RSCB-MC for abstention
   - Cite 2605.06788 Conformal for coverage framing

**Verification gate:**
```bash
test -f artifacts/headline_v3/experiment_02_headline.csv
python -c "
import csv
r = list(csv.DictReader(open('artifacts/headline_v3/experiment_02_headline.csv')))
mf1 = float(r[0]['macro_f1'])
narrow = float(r[0].get('reasoning_narrow_f1', '0'))
assert mf1 >= 0.70, f'mF1 too low: {mf1}'
assert narrow >= 0.50, f'narrow reasoning F1 too low: {narrow}'
print(f'mF1={mf1:.3f}, narrow_reasoning_F1={narrow:.3f}')
"
```

### Day 19 (6/17) — W3 closeout

**W3 → W4 gate:**
- ≥ 4 ablation tables published
- Headline mF1 ≥ 0.70
- Narrow reasoning F1 ≥ 0.50
- 803 + new tests pass
- §Method draft committed

If narrow reasoning F1 < 0.50, the EVIDENCE_DEPENDENT subtype is noisier than expected; investigate why `evidence_given_reasoning` replay misfires on these cases. Do not proceed to W4 until understood.

---

## W4 — R2 self-consistency probe (6/19-6/25)

Goal: `replays/self_consistency_probe.py` implemented and calibrated. If pilot succeeds (p < 0.05 separation), integrate as runtime online surrogate. If fails, ship R1 alone, document R2 in Future Work.

### Day 20-21 (6/19-6/20) — Probe implementation

**Tasks:**
1. Create `cmd_audit/replays/self_consistency_probe.py`:
```python
@dataclass(frozen=True)
class SelfConsistencyResult:
    samples: tuple[str, ...]              # N=5 agent outputs
    agreement_ratio: float                # max-cluster / N
    correctness_estimate: float           # avg evidence_recall over samples
    classification: str                   # "high_agree_low_correct" | "high_agree_high_correct" | "low_agree" | "noise"

def run_self_consistency_probe(
    case: ProbeCase,
    *,
    agent_generate: Callable[[str, str], str],
    n_samples: int = 5,
    temperature: float = 0.5,
    scorer: SubagentScorer,
) -> SelfConsistencyResult: ...
```
2. Integration: add to portfolio behind a flag, not in default 10-replay set
3. Tests in `tests/replays/test_self_consistency_probe.py`:
   - N=5 sampling deterministic with seeded LLM stub
   - Agreement ratio computation
   - Classification logic for 4 buckets

**Verification gate:**
```bash
python -m pytest tests/replays/test_self_consistency_probe.py -v
```

### Day 22 (6/21) — Calibration set construction

**Tasks:**
1. Create `scripts/build_self_consistency_calibration_set.py`:
   - 50 cases: 25 reasoning_error + 25 non-reasoning negative control
   - Reasoning subset: stratified by R1 subtype (8 EVIDENCE_DEPENDENT + 8 HARD + 9 UPSTREAM_RECOVERABLE)
   - Negative control: random 25 from {retrieval, injection, route, write}
   - Output: `data/probe_cases/self_consistency_calibration.json`

### Day 23 (6/22) — Pilot run + threshold calibration

**Tasks:**
1. Run probe on 50 cases:
```bash
python scripts/run_self_consistency_pilot.py \
    --cases data/probe_cases/self_consistency_calibration.json \
    --out artifacts/self_consistency_pilot/
```
2. Compute:
   - For each case: (agreement_ratio, correctness_estimate, true_subtype)
   - ROC for "agreement_ratio threshold separating EVIDENCE_DEPENDENT from HARD"
   - Mann-Whitney U test for separation significance
3. Decision:
   - **GO** if p < 0.05 and AUC ≥ 0.7
   - **NO-GO (fallback)** otherwise

### Day 24-25 (6/23-6/24) — GO path: integrate. NO-GO path: cleanup.

**GO path:**
1. Wire probe into `harness.run_case` behind `online=True` flag
2. Update `attribution/failure.py` to use probe result for online subtype assignment when `gold_evidence is None`
3. Tests for online path in `tests/integration/test_online_attribution.py`
4. Document in `cmd_innovation_core/plans/limitations.md` §Online Surrogate

**NO-GO path:**
1. Keep probe code but mark as experimental
2. Update §Future Work in paper draft
3. Document calibration failure data — useful for V1.1 redo
4. **W4 reduces from full week to 2 days; reallocate Day 24-25 to §Methodological Comparison (Shapley + conformal head-to-head baselines)**

**Verification gate:**
```bash
test -f artifacts/self_consistency_pilot/calibration_decision.txt
cat artifacts/self_consistency_pilot/calibration_decision.txt | grep -E "^DECISION: (GO|NO-GO)"
```

### Day 26 (6/25) — W4 closeout

**W4 → W5 gate:**
- Either GO (probe integrated, online tests pass) or NO-GO (probe shelved, §Future Work documented)
- 803 + W3 + W4 tests all green
- Final headline numbers locked: mF1, narrow reasoning F1, coverage, 4 baselines

---

## W5 — Paper writing (6/26-7/2)

Goal: full §Method + §Experiment drafted; figures generated; ready for §Introduction + §Discussion in W6.

### Daily breakdown

- Day 27 (6/26): §Method §1-3 (Architecture, Replay Portfolio, Attribution)
- Day 28 (6/27): §Method §4 (Reasoning Subtype Decomposition + optional Online Surrogate)
- Day 29 (6/28): §Method §5 (Post-Repair Context Replay) — emphasize as co-equal contribution per prior framing decision
- Day 30 (6/29): §Method §6 (Methodological Comparison: Recovery Gain vs Shapley vs Conformal) — head-to-head ablation results
- Day 31 (6/30): §Experiment §1 (Headline 130-case + 596 sanity, with bootstrap CI)
- Day 32 (7/1): §Experiment §2-4 (Reasoning ablation, retrieval/route merge, hook efficacy)
- Day 33 (7/2): Figures via `figure-standardize` skill; tables via `artifact-review` skill

**Verification gate:** every section ≤ 2 pages, every claim has artifact-bound citation `[file:row]`, no orphan claims without numbers.

---

## W6 — Introduction, Discussion, arxiv (7/3-7/9)

- Day 34 (7/3): §Introduction (4 bullets: operation-level granularity, repair-validated diagnosis, full closed loop, Recovery Gain interventional)
- Day 35 (7/4): §Related Work (TraceAudit, VerifyMAS, Shapley, Conformal positioned per layered framing)
- Day 36 (7/5): §Discussion (Premature Closure ceiling, Online surrogate scope, repair as quality gate)
- Day 37 (7/6): §Limitations + §Future Work (PRM, rate-distortion as parked routes)
- Day 38 (7/7): full pass via `artifact-review` skill; reference completeness check
- **Day 39 (7/8): arxiv submission** — final tex + figures + supplementary
- Day 40 (7/9): buffer / unforeseen / first feedback

---

## Risk Checkpoints

| Risk | Trigger | Mitigation |
|---|---|---|
| W1 refactor breaks tests | any day W1 verification gate fails | revert that day's commit, smaller batches |
| W2 hook calibration produces unsafe FALLBACK=0 | constants.py output | manually clamp to 0.20, document in §Limitations |
| W3 narrow reasoning F1 < 0.5 | Day 18 verification gate | investigate evidence_given_reasoning replay quality on EVIDENCE_DEPENDENT cases; may need to refine `classify_reasoning_subtype` thresholds |
| W4 self-consistency calibration fails | Day 23 NO-GO | fallback to R1 alone; reallocate Days 24-25 to Methodological Comparison |
| W5 paper section X over 2 pages | section draft review | trim or move to appendix |
| TraceAudit publishes first | arxiv listings 7/1+ | accelerate to 7/5; emphasize differentiation in §Introduction |
| AAAI deadline shifts earlier | watch AAAI 2027 CFP | compress W6 to 5 days |

---

## Verification Commands Reference

Quick-check commands used across gates:

```bash
# Test suite
python -m pytest tests/ -q 2>&1 | tail -3

# Per-subpackage tests
python -m pytest tests/core tests/replays tests/attribution tests/scoring tests/repair tests/eval tests/hook tests/adapters tests/baselines tests/data_io -q

# Public API surface
python -c "import cmd_audit; api = [x for x in dir(cmd_audit) if not x.startswith('_')]; print(len(api), 'public symbols')"

# Import sanity
python -c "from cmd_audit import run_case, run_cases, run_real_suite, PIPELINE_LABELS, ALL_LABELS, validate_label, AttributionResult, ProbeCase, MemoryItem"

# Headline regen
python scripts/build_experiment_02_tables.py --out artifacts/headline_latest/

# Reasoning ablation
python scripts/build_reasoning_ablation.py --out artifacts/reasoning_ablation/

# Hook calibration
python scripts/calibrate_hook.py --input artifacts/at_scale_llm_retest.csv

# Self-consistency pilot (W4)
python scripts/run_self_consistency_pilot.py --cases data/probe_cases/self_consistency_calibration.json
```

---

## Deviations from Standard `/research-implement` Skill

The skill assumes greenfield ML training project. CMD-Audit is a 39-file research codebase with 803 tests, zero PyPI dependencies, no model training in the traditional sense. Adaptations:

- **No `project/` scaffold:** existing `cmd_audit/` package is refactored in place
- **No `run.py` entry:** `pytest` + `scripts/build_*.py` artifact regeneration replaces it
- **No `[RESULT]` lines:** verification gates use `pytest` exit codes + CSV header parsing instead
- **No `uv venv`:** project has zero PyPI deps per CLAUDE.md; standard CPython works
- **No 2-epoch validation:** "validation" means 803-test pass + headline F1 ≥ threshold
- **No mock data:** `artifacts/at_scale_llm_retest.csv` is real LLM-rescored 596 cases

Spirit of the skill preserved: every task has a falsifiable verification gate, no fabrication of execution numbers, rollback plans on failure, time-boxed phases.

---

## Known Issues & Open Decisions

1. **`run_post_repair_subset.py` zero-gain crash** (current bug discussed earlier): resolved as side effect of W1 Day 3 (attribution unifies error handling).
2. **Hook three-deferred-replay bit-identical weights**: may persist after W2 recalibration if 596 set lacks positive samples for granularity/graph/safety. Document as expected, not a bug.
3. **Adapter parity under LLM stack**: not in V1.0 scope per Decision 34 R9; deferred to V1.1 issue 0035.
4. **Cost/latency token instrumentation**: `experiment_02_cost_latency.csv` USD/token columns are blank (currently only wallclock measured). W5 paper writing must either fill these in or note as supplementary.
5. **`evaluator_model` selection**: D34 says "≠ qwen, ≠ llama-A, ≠ deepseek" but specific candidate (gpt-4o-mini class) deferred. Lock by Day 8.

---

## Implementation Plan Status

This plan is **executable starting Day 1 (5/29)**. Each verification gate is a Boolean: pass or stop.

The first action is the pre-flight inventory; every subsequent action assumes the prior verification gate passed.

This document is the equivalent of `ml_res.md` adapted for a 6-week refactor + extension plan over an existing research codebase.
