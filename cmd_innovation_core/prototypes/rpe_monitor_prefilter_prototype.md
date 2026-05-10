# Prototype Brief: RPE Pre-Filter for Subagent Judge Monitor

## Branch

LOGIC prototype. The question is whether a D-MEM-style Reward Prediction Error (RPE) pre-filter can reduce Subagent Judge Monitor replay trigger cost without lowering attribution recall.

## Source

- D-MEM (arXiv:2603.14597): Critic Router scores stimuli on Surprise and Utility; low-RPE inputs bypass expensive O(N) restructuring (80% token reduction).
- CMD hyp-011: RPE as cheap anomaly pre-filter for Subagent Judge Monitor.

## Throwaway Contract

- This prototype is throwaway from day one.
- All state in memory.
- Surface full state after every action.
- Delete or absorb after the pre-filter decision is validated.
- No production data, persistence, polished UI, or broad error handling.

## Question

When Subagent Judge Monitor detects an anomaly, should it always trigger the full V0 Replay Portfolio (6 replays), or can a lightweight RPE pre-filter score the evidence-surprise gap and skip low-surprise cases?

## RPE Pre-Filter Design

### Input

- `baseline_answer`: the failed agent output
- `retrieved_memory_items`: what the agent had in context
- `gold_evidence`: known gold (for evaluation only; in production, use evidence_recall_from_text as proxy)
- `confidence_threshold`: below which replay is always triggered

### Scoring

```
surprise_score = 1.0 - max(evidence_recall_from_text(gold_evidence_unit, item.text) for item in retrieved_memory_items)
utility_score = answer_score(baseline_answer)  // how badly the task failed
rpe = surprise_score * utility_score
```

### Gating Decision

```
if rpe > REPLAY_THRESHOLD:
    trigger_full_replay_portfolio()
elif confidence < CONFIDENCE_THRESHOLD:
    trigger_full_replay_portfolio()  // safety net for uncertain cases
else:
    skip_replay()  // low-surprise, high-confidence: memory pipeline likely not at fault
```

## Scenario Cards

### Card A: High Surprise, High Utility → Trigger
- Memory items contain no gold evidence (surprise=1.0)
- Answer score is 0.0 (utility=1.0)
- RPE = 1.0 → trigger full replay
- Expected: replay portfolio runs, identifies the pipeline error.

### Card B: Low Surprise, Low Utility → Skip
- Memory items contain gold evidence (surprise=0.1)
- Answer score is 0.9 (utility=0.1, near-correct)
- RPE = 0.01 → skip replay
- Expected: replay skipped; near-correct answer with evidence present suggests no memory pipeline fault.

### Card C: Low Surprise, High Utility → Trigger (Utility Dominates)
- Memory items contain gold evidence (surprise=0.0)
- Answer score is 0.0 (utility=1.0, complete failure)
- RPE = 0.0 but utility is high
- Expected: this is `reasoning_error` territory. The pre-filter should still trigger because utility=1.0 with evidence present suggests reasoning failure.

### Card D: High Surprise, Low Confidence → Trigger (Safety Net)
- RPE is moderate (0.4)
- Monitor confidence is low (0.3)
- Expected: trigger regardless of RPE; low confidence is a safety override.

### Card E: Batch Skip Rate
- Run pre-filter across 100 probe cases with known perturbation labels.
- Measure: skip rate, false skip rate (skipped cases that actually had pipeline errors), attribution recall after pre-filter.
- Target: <5% false skip rate, >30% replay cost reduction.

## State to Surface

After pre-filter evaluation, display:
- `case_id`
- `surprise_score`
- `utility_score`
- `rpe`
- `monitor_confidence`
- `gate_decision` (trigger / skip)
- `actual_perturbation_label` (for evaluation)
- `false_skip` (true if skipped but had pipeline error)

## Relationship to CMD-Audit

The RPE pre-filter sits between Subagent Judge Monitor and the V0 Replay Portfolio:

```
Failed Task
  -> Subagent Judge Monitor (leak-safe anomaly detection)
    -> RPE Pre-Filter (this prototype)
      -> [trigger] V0 Replay Portfolio
      -> [skip] return monitor result only
```

The pre-filter does not change attribution or ECS output. It only gates whether replay runs.

## Verdict Placeholder

Does the RPE scoring (surprise * utility) capture the right cases for replay? Are there failure modes where RPE is low but a memory pipeline error exists? Does the confidence safety net catch these edge cases?
