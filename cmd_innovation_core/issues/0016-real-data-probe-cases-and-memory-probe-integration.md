---
title: Build LoCoMo/LongMemEval real-data probe cases and integrate memory-probe baseline
labels:
  - ready-for-human
type: ready-for-human
blocked_by: ~
user_stories:
  - 42
  - 43
---

# Build LoCoMo/LongMemEval real-data probe cases and integrate memory-probe baseline

## Parent

`prd/cmd_minimal_probe_prd.md` V1 Scope

## What to Build

Two data-layer additions that strengthen V1 evaluation:

1. **Real-data probe cases:** Mix LongMemEval, MemoryArena, and ToolBench real-data cases into the probe suite alongside synthetic perturbations. Data construction is researcher-led; CMD-Audit consumes the resulting probe case files. (LoCoMo was originally planned but MemoryArena was selected as the primary conversational-memory source; LoCoMo remains a backup option.)
2. **Memory-probe baseline integration:** Memory-probe's 3×2 grid-comparison logic (3 write × 2 retrieve: cosine + BM25; dense retrieval deferred to V1 per issue 0008) is implemented in issue 0013 (Cycle 19). This issue completes the integration by running the comparator against the expanded probe suite and producing the final comparison metrics.

## Implementation Detail

### Real-Data Probe Cases

- Researcher constructs probe cases from LongMemEval (long-context memory evaluation), MemoryArena (conversational memory in constrained environments), and ToolBench (tool-use API interactions). LoCoMo is a backup option for conversational memory data.
- Each real-data case follows the existing probe case schema: `query`, `raw_events`, `extracted_memory`, `gold_answer`, `gold_evidence`, `perturbation_label`, `baseline_outputs`, `scoring`, `has_ingestion_trace`, `default_store`.
- For naturally occurring failures (not synthetic perturbations), `perturbation_label` may be `null` (unknown ground truth). These cases are evaluation-only; they do not contribute to macro F1 against known labels but are used for qualitative analysis and human evaluation. Null-label cases are loaded through both V0 and V1 loaders; `attribution_correct` returns `None` for null-label cases; metrics computation skips null-gold-label rows for accuracy/F1.
- Case files: `data/probe_cases/real_longmemeval_cases.json` (200 cases), `data/probe_cases/real_memoryarena_cases.json` (198 cases), `data/probe_cases/real_toolbench_cases.json` (198 cases). Null-label test fixture: `data/probe_cases/v1_null_label_cases.json` (5 cases).
- Total: 596 cleaned real-data probe cases, surpassing the minimum target of 20. Source distribution: 200 LongMemEval (33.6%) + 198 MemoryArena (33.2%) + 198 ToolBench (33.2%).

### Memory-Probe Baseline Integration

- Memory-probe comparator (implemented in issue 0013) is run against the full probe suite (synthetic + real-data).
- For real-data cases without ground-truth perturbation labels, memory-probe is evaluated on aggregate accuracy; CMD is evaluated on attribution plausibility (human review, not automated).
- Final comparison metrics CSV includes both synthetic-only and full-suite columns.

### CMD-Audit Responsibility

- CMD-Audit loads real-data cases through the same `ProbeLoader` interface as synthetic cases (`load_probe_cases` for V0, `load_probe_cases_v1` for V1).
- V0 loader accepts cases with V0 labels; rejects cases with V1-only labels (`route_error`, `ingestion_error`, `granularity_error`, `graph_error`, `safety_error`).
- No special code path for real-data cases. The only difference is `perturbation_label` may be `null`.
- `attribution_correct` returns `None` for null-label cases (no ground truth to compare).
- `DiagnosisPrediction` accepts `gold_label=None`; `compute_diagnosis_metrics` excludes null-gold-label rows from accuracy/F1 computation.
- ECS and Post-Repair Context Replay run identically on real-data cases regardless of perturbation label.

## Acceptance Criteria

- [x] LongMemEval real-data probe cases (200) load through existing `ProbeLoader` without schema changes.
- [x] MemoryArena real-data probe cases (198) load through existing `ProbeLoader` without schema changes.
- [x] ToolBench real-data probe cases (198) load through V1 `ProbeLoader`; correctly rejected by V0 loader (V1 labels).
- [x] Real-data cases with `perturbation_label: null` are handled gracefully: `attribution_correct` returns `None`, excluded from macro F1, included in qualitative output.
- [x] Memory-probe comparator runs against full probe suite (synthetic + real-data + null-label).
- [x] `comparison_metrics.csv` includes `memory_probe_best_accuracy` column for both synthetic-only and full-suite.
- [x] Behavior-level tests: 38 tests (1993 subtests) covering null perturbation label handling, real-data case loading (V0+V1), memory-probe on mixed suite, null label exclusion from metrics, data source integrity.

## Implementation Notes

This is a data-layer issue with minimal code changes (~30 lines of business logic). No new modules. Key touchpoints:

### Code changes

| File | Line(s) | Change |
|------|---------|--------|
| `cmd_audit/models.py:121-122` | 121-122 | `perturbation_label` → `str \| None = None`; `scoring` → `ScoringSpec()` default (required for dataclass field ordering) |
| `cmd_audit/models.py:157` | 157 | `from_mapping`: use `_optional_label_v0()` instead of `validate_v0_label(_required_str(...))` |
| `cmd_audit/models.py:196` | 196 | `from_mapping_v1`: use `_optional_label_v1()` for null-label passthrough |
| `cmd_audit/models.py:293-310` | 293-310 | New helpers `_optional_label_v0()` / `_optional_label_v1()`: return `None` for null input, validate non-null labels |
| `cmd_audit/metrics.py:16,22` | 16, 22 | `DiagnosisPrediction.gold_label` → `str \| None`; skip validation when `None` |
| `cmd_audit/metrics.py:51-58` | 51-58 | `compute_diagnosis_metrics`: filter `labeled_rows` excluding null-gold-label rows from accuracy/F1; still count all rows for cost |
| `cmd_audit/metrics.py:70-78` | 70-78 | `_observed_labels`: filter `None` from label set before sorting |
| `cmd_audit/harness.py:44-46` | 44-46 | `attribution_correct` → `bool \| None`: returns `None` when `perturbation_label is None` |
| `cmd_audit/memory_probe.py:1,23,42,154,196` | multiple | Docstring/comment corrections: "3x3"→"3x2", "9 cell"→"6 cell", note dense retrieval deferred to V1 per issue 0008 |

### Data files

| File | Count | Description |
|------|-------|-------------|
| `data/probe_cases/real_longmemeval_cases.json` | 200 | LongMemEval real probe cases (V0 labels) |
| `data/probe_cases/real_memoryarena_cases.json` | 198 | MemoryArena real probe cases (V0 labels) |
| `data/probe_cases/real_toolbench_cases.json` | 198 | ToolBench real probe cases (V1 labels; V0 loader rejects) |
| `data/probe_cases/v1_null_label_cases.json` | 5 | Null perturbation_label test fixture |
| `data/cleaned_cases/cleaned_cases.json` | 596 | Raw cleaned cases (haystack format) |
| `data/cleaned_cases/context_cases.json` | 596 | Context cases (wrong_memory, corrected_memory, ECS fields) |

### Test file: `tests/test_cmd_audit_issue16_real_data.py` (390 lines, 38 tests)

| Class | Tests | Coverage |
|-------|-------|----------|
| `RealDataLoadingTest` | 12 | V0/V1 loader for all 3 sources, 596 total, label validity, schema completeness |
| `NullPerturbationLabelTest` | 10 | Null-label load/run/full-pipeline, `attribution_correct=None`, `DiagnosisPrediction` construction, null exclusion from metrics, mixed suite |
| `MemoryProbeMixedSuiteTest` | 9 | 3x2 grid on synthetic/real/null-label, 6 cells per case, case_id matching, best_cell validation |
| `ComparisonMetricsTest` | 4 | CSV column inclusion/omission, null-label + memory-probe metrics, real data sample metrics |
| `RealDataSourceIntegrityTest` | 3 | Source distribution (200+198+198), unique IDs, null-label ID isolation |

### Related documents updated

`CONTEXT.md`, `TASK.md` (4 places), `knowledge/current-memory.md`, `knowledge/topic-cmd-memory-failure.md`, `reference_notes/paper_2603_02473_memory_probe.md`, `prd/cmd_minimal_probe_prd.md` (5 places), `tdd/cmd_tracer_bullets.md`, `prototypes/cmd_probe_logic_prototype.md`, `plans/cmd_progress_report.md`, `issues/0013-*.md` (both spec and detail map), `issues/modify.md` — all updated from "3x3" to "3x2" grid with dense retrieval deferral note.
