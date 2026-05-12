---
title: Build LoCoMo/LongMemEval real-data probe cases and integrate memory-probe baseline
labels:
  - AFK
type: AFK
blocked_by: "0012"
user_stories:
  - 42
  - 43
---

# Build LoCoMo/LongMemEval real-data probe cases and integrate memory-probe baseline

## Parent

`prd/cmd_minimal_probe_prd.md` V1 Scope

## What to Build

Two data-layer additions that strengthen V1 evaluation:

1. **Real-data probe cases:** Mix LoCoMo and LongMemEval real-data cases into the probe suite alongside synthetic perturbations. Data construction is researcher-led; CMD-Audit consumes the resulting probe case files.
2. **Memory-probe baseline integration:** Memory-probe's 3×3 grid-comparison logic is partially implemented in issue 0013 (Cycle 19). This issue completes the integration by running the comparator against the expanded probe suite and producing the final comparison metrics.

## Implementation Detail

### Real-Data Probe Cases

- Researcher constructs probe cases from LoCoMo (long-term conversational memory) and LongMemEval (long-context memory evaluation).
- Each real-data case follows the existing probe case schema: `query`, `history/raw_events`, `extracted_memory`, `gold_answer`, `gold_evidence`, `perturbation_label`.
- For naturally occurring failures (not synthetic perturbations), `perturbation_label` may be `null` (unknown ground truth). These cases are evaluation-only; they do not contribute to macro F1 against known labels but are used for qualitative analysis and human evaluation.
- Case files: `data/probe_cases/v1_locomo_cases.json`, `data/probe_cases/v1_longmemeval_cases.json`.
- Minimum target: 20 real-data cases (10 LoCoMo + 10 LongMemEval) to supplement 50-100 synthetic cases.

### Memory-Probe Baseline Integration

- Memory-probe comparator (implemented in issue 0013) is run against the full probe suite (synthetic + real-data).
- For real-data cases without ground-truth perturbation labels, memory-probe is evaluated on aggregate accuracy; CMD is evaluated on attribution plausibility (human review, not automated).
- Final comparison metrics CSV includes both synthetic-only and full-suite columns.

### CMD-Audit Responsibility

- CMD-Audit loads real-data cases through the same `ProbeLoader` interface as synthetic cases.
- No special code path for real-data cases. The only difference is `perturbation_label` may be `null`.
- ECS and Post-Repair Context Replay run identically on real-data cases.

## Acceptance Criteria

- [ ] LoCoMo real-data probe cases load through existing `ProbeLoader` without schema changes.
- [ ] LongMemEval real-data probe cases load through existing `ProbeLoader` without schema changes.
- [ ] Real-data cases with `perturbation_label: null` are handled gracefully (excluded from macro F1, included in qualitative output).
- [ ] Memory-probe comparator runs against full probe suite (synthetic + real-data).
- [ ] `comparison_metrics.csv` includes memory-probe column for both synthetic-only and full-suite.
- [ ] Behavior-level tests: null perturbation label handling, real-data case loading, memory-probe on mixed suite.
