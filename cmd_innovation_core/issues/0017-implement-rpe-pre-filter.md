---
title: Implement RPE pre-filter for Subagent Judge Monitor replay gating
labels:
  - AFK
type: AFK
blocked_by: "0015"
user_stories: []
tdd_cycle: 22
---

# Implement RPE pre-filter for Subagent Judge Monitor replay gating

## Parent

`prd/cmd_minimal_probe_prd.md` V1 Scope
`prototypes/rpe_monitor_prefilter_prototype.md`
`reference_notes/paper_2603_14597.md` (D-MEM)

## What to Build

Add a D-MEM-style Reward Prediction Error (RPE) pre-filter between Subagent Judge Monitor and the V1 Replay Portfolio. The pre-filter scores the evidence-surprise gap and skips low-surprise cases, reducing replay cost without lowering attribution recall.

This is a late-V1 optimization. It does not block the V1→V2 gate.

## Implementation Detail

### RPE Scoring

```
surprise_score = 1.0 - max(evidence_recall_from_text(gold_evidence_unit, item.text) for item in retrieved_memory_items)
utility_score = 1.0 - answer_score(baseline_answer)
rpe = surprise_score * utility_score
```

### Gating Decision

```
if rpe > REPLAY_THRESHOLD:
    trigger_full_replay_portfolio()
elif monitor_confidence < CONFIDENCE_THRESHOLD:
    trigger_full_replay_portfolio()  // safety net
else:
    skip_replay()
    return MonitorResult(trigger=False, reason="rpe_below_threshold")
```

### Configuration

- `REPLAY_THRESHOLD`: default 0.3 (tunable; calibrated from batch evaluation).
- `CONFIDENCE_THRESHOLD`: default 0.5 (cases where monitor is uncertain always trigger).
- Thresholds are configurable per run, not hardcoded.

### Batch Evaluation

- Run pre-filter on 50+ probe cases with known perturbation labels.
- Measure:
  - `skip_rate`: fraction of cases where replay was skipped.
  - `false_skip_rate`: fraction of skipped cases that actually had pipeline errors (should be < 5%).
  - `attribution_recall`: CMD attribution recall with pre-filter vs without pre-filter (should be ≥ 0.95).
  - `cost_reduction`: (replays_skipped / total_cases) * (avg_replay_cost) (should be ≥ 30%).

### Integration Point

```
Failed Task
  -> Subagent Judge Monitor (leak-safe anomaly detection)
    -> RPE Pre-Filter (this issue)
      -> [trigger] V1 Replay Portfolio (11 replays)
      -> [skip] return monitor result only
```

The pre-filter does not change attribution, ECS, or repair logic. It only gates whether replay runs.

### Edge Cases

- **Low surprise, high utility (utility dominates):** Even with RPE=0, if answer completely failed, trigger anyway. Implementation: if `utility_score > UTILITY_OVERRIDE_THRESHOLD` (default 0.8), trigger regardless of RPE.
- **Gold evidence unavailable (real-data cases):** Use `evidence_recall_from_text` with expected evidence phrases as proxy for `surprise_score`. If no expected phrases available, skip pre-filter and always trigger (conservative fallback).

## Acceptance Criteria

- [ ] RPE pre-filter correctly computes surprise_score, utility_score, and rpe.
- [ ] High RPE cases trigger full replay.
- [ ] Low RPE + high confidence cases skip replay.
- [ ] Low confidence cases always trigger (safety net).
- [ ] Utility override: high utility cases trigger even with low RPE.
- [ ] Batch evaluation on 50+ cases: false skip rate < 5%, cost reduction ≥ 30%.
- [ ] Attribution recall with pre-filter ≥ 0.95 vs without pre-filter.
- [ ] Pre-filter does not change attribution labels or ECS output (only gates replay execution).
- [ ] Real-data cases without expected evidence phrases always trigger (conservative fallback).
- [ ] Behavior-level tests: RPE scoring, gating branches, utility override, batch metrics in expected ranges.
