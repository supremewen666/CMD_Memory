---
title: Recalibrate coupled-failure attribution and add memory-probe baseline for 11-label space
labels:
  - AFK
type: AFK
blocked_by: "0012"
user_stories:
  - 42
tdd_cycle: "18, 19"
---

# Recalibrate coupled-failure attribution and add memory-probe baseline for 11-label space

## Parent

`prd/cmd_minimal_probe_prd.md` V1 Scope

## What to Build

Two V1 extensions to existing V0 infrastructure:

1. **Coupled-failure recalibration (Cycle 18):** With 11 labels, replay deltas are expected to be closer together. The V0 top-2 threshold (calibrated for 6 labels) must be recalibrated. Add multi-label (≥3) attribution for cases where three replays produce close deltas.

2. **Memory-probe baseline (Cycle 19):** Add memory-probe 3×3 grid-comparison as a new comparator baseline. Re-run all baselines (evidence-recall, subagent judge, random, memory-probe) against 11-label CMD-Audit attribution.

## Implementation Detail

### Coupled-Failure Recalibration

- Measure delta distribution across 11-label smoke suite to establish new close-delta threshold.
- Expected: higher label density → more close deltas. Threshold may need to be wider than V0.
- Add `top_k` attribution parameter: V0 used top-2; V1 supports top-3 for cases with three close deltas.
- Multi-label (≥3) attribution output format: list of labels with delta values, plus ambiguity note referencing the 3+ close deltas.
- Update `AttributionResult` schema to support N-label output (N ≥ 1, bounded by label count).
- Edge case: if 4+ deltas are close, output top-3 with a note that additional labels are within threshold.

### Memory-Probe Baseline Comparator

- Implement memory-probe 3×3 grid comparator:
  - Write strategies: Mem0-style fact extraction, MemGPT-style summarization, raw chunks.
  - Retrieval methods: cosine similarity, BM25, hybrid reranking.
- For each probe case, run the 3×3 grid, record best cell accuracy.
- Compare CMD-Audit 11-label macro F1 against memory-probe's best grid-cell accuracy.
- Memory-probe is an aggregate diagnostic (identifies whether write or retrieval dominates at the dataset level), not case-level. The comparison metric is: CMD case-level accuracy vs memory-probe aggregate accuracy ceiling.
- Add `memory_probe_best_accuracy` column to `comparison_metrics.csv`.

### Non-regression

- V0 coupled-failure smoke cases (Cycle 4) must still produce correct top-2 on the 6-label subset.
- Existing baselines (evidence-recall, subagent judge, random) must not regress.

## Acceptance Criteria

- [ ] 11-label top-2 threshold is calibrated from measured delta distribution (not hardcoded from V0).
- [ ] Multi-label (≥3) attribution output works for cases with three close deltas.
- [ ] Memory-probe 3×3 grid comparator produces valid accuracy scores.
- [ ] Memory-probe baseline is included in `comparison_metrics.csv`.
- [ ] CMD-Audit 11-label macro F1 > all baselines including memory-probe.
- [ ] V0 Cycle 4 coupled-failure cases still attribute correctly on 6-label subset.
- [ ] Behavior-level tests: close-delta top-2, triple-close multi-label, memory-probe accuracy in valid range.
