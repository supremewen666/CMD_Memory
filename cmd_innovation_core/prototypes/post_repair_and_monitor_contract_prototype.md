# Prototype Brief: Post-Repair Assessment & Monitor Contract

## Branch

LOGIC prototype. The question is whether the three-value repair assessment state machine and the enum-locked monitor rejection path feel right before implementation.

## Throwaway Contract

- This prototype is throwaway from day one.
- All state in memory.
- Surface full state after every action.
- Delete or absorb after the state model is validated.
- No production data, persistence, polished UI, or broad error handling.

## Scenario 1: Three-Value Post-Repair Assessment

### Question

When Post-Repair Context Replay runs and evidence recovers (evidence_score = 1.0) but the answer still fails (answer_score = 0.0), does the `partial` assessment state machine expose enough information for a researcher to diagnose the coupled failure?

### State Transitions

```
RepairedContextBuilt
  -> PostRepairRetested(post_repair_answer_score, post_repair_evidence_score)
      -> repair_assessment = classify(post_repair_answer_score, post_repair_evidence_score)

classify(answer_score, evidence_score):
  answer_score == 1.0                               -> "recovered"
  answer_score < 1.0 AND evidence_score == 1.0      -> "partial"
  answer_score < 1.0 AND evidence_score < 1.0       -> "failed"
```

### Scenario Cards

#### Card A: Full Recovery
- Pre-repair: answer_score=0.0, evidence_score=0.0, label=retrieval_error
- Repair: fix retrieval routing, rebuild context with correct memory
- Post-repair: answer_score=1.0, evidence_score=1.0
- Expected assessment: `recovered`
- Interpretation: single-cause failure, repair works end-to-end.

#### Card B: Partial — Coupled Failure Exposed
- Pre-repair: answer_score=0.0, evidence_score=0.0, label=retrieval_error
- Repair: fix retrieval routing, rebuild context with correct memory
- Post-repair: answer_score=0.0, evidence_score=1.0
- Expected assessment: `partial`
- Interpretation: retrieval was broken AND the model can't reason over the evidence. The retrieval_error masked a reasoning_error. This is diagnostic depth, not repair failure.
- Follow-up action: re-run with Evidence-Given Reasoning replay to confirm reasoning_error.

#### Card C: Failed — Repair Missed the Root Cause
- Pre-repair: answer_score=0.0, evidence_score=0.0, label=retrieval_error
- Repair: fix retrieval routing, rebuild context with correct memory
- Post-repair: answer_score=0.0, evidence_score=0.0
- Expected assessment: `failed`
- Interpretation: retrieval was not the root cause. The repair targeted the wrong operation.
- Follow-up action: check top-2 labels, re-run attribution with different baseline.

#### Card D: Partial — Injection Fix, Reasoning Still Broken
- Pre-repair: answer_score=0.0, evidence_score=0.0, label=injection_error
- Repair: fix injection format, rebuild context with clean evidence block
- Post-repair: answer_score=0.0, evidence_score=1.0
- Expected assessment: `partial`
- Interpretation: injection format was broken AND reasoning over clean evidence also fails. Coupled injection_error + reasoning_error.

### State to Surface

After Post-Repair Context Replay, display:
- `case_id`
- `perturbation_label`
- `predicted_label`
- `pre_repair_answer_score`
- `pre_repair_evidence_score`
- `post_repair_answer_score`
- `post_repair_evidence_score`
- `repair_assessment`
- `repair_action_taken`
- `regression_risk` (did any other metric degrade?)
- `token_cost`

### Verdict Placeholder

Does the three-value classification make the coupled-failure diagnosis path obvious? Or does `partial` need a `suspected_coupled_label` hint?

---

## Scenario 2: Monitor Enum-Locked Contract Rejection

### Question

When the Subagent Judge Monitor attempts to emit free-form natural language `anomaly_reason` or content-bearing evidence pointers, does the rejection path surface enough information for the caller to understand why the output was rejected?

### Allowed Output Schema

```text
MonitorOutput:
  trigger_replay: bool
  anomaly_reason: "answer_vs_evidence_mismatch"
                 | "retrieved_context_incomplete"
                 | "evidence_recall_low"
                 | "confidence_anomaly"
  confidence: float (0.0-1.0)
  evidence_pointers: list[opaque_id: str]  // ID only, no text
```

### Rejection Rules

| Violation | Detection | Rejection Behavior |
|-----------|-----------|-------------------|
| `anomaly_reason` not in enum | enum membership check | Reject entire output, return `MonitorRejection(reason="invalid_anomaly_reason", detail=raw_value)` |
| Free-form text in `anomaly_reason` | string match against enum values | Same as above—enum check catches this |
| Evidence pointer contains content text | pointer value contains whitespace or exceeds ID length limit | Reject, return `MonitorRejection(reason="evidence_pointer_not_opaque", detail=offending_pointer)` |
| Final attribution label present | field name check for `label`, `attribution`, `diagnosis` | Reject, return `MonitorRejection(reason="forbidden_field", detail=field_name)` |
| ECS record present | field name check for `ecs`, `error_cause_solution`, `repair` | Reject, return `MonitorRejection(reason="forbidden_field", detail=field_name)` |
| Gold answer present | field name check for `gold_answer`, `correct_answer` | Reject, return `MonitorRejection(reason="forbidden_field", detail=field_name)` |
| Full failed trace present | field name check for `trace`, `full_context`, `failed_output` | Reject, return `MonitorRejection(reason="forbidden_field", detail=field_name)` |
| Memory write payload present | field name check for `memory_write`, `user_memory`, `failure_memory` | Reject, return `MonitorRejection(reason="forbidden_field", detail=field_name)` |

### Scenario Cards

#### Card A: Legitimate Trigger
- Monitor input: baseline answer contradicts retrieved evidence
- Monitor output: `{trigger_replay: true, anomaly_reason: "answer_vs_evidence_mismatch", confidence: 0.87, evidence_pointers: ["mem_003"]}`
- Expected: accepted, replay triggered.
- State to surface: trigger_replay, anomaly_reason label, confidence, pointer count.

#### Card B: Free-Form Reason Rejected
- Monitor output: `{trigger_replay: true, anomaly_reason: "the answer looks wrong compared to the stored facts about user location", confidence: 0.9}`
- Expected: rejected with `MonitorRejection(reason="invalid_anomaly_reason", detail="the answer looks wrong...")`.
- State to surface: rejection reason, raw value, suggestion to use enum value.

#### Card C: Content-Bearing Pointer Rejected
- Monitor output: `{trigger_replay: true, anomaly_reason: "evidence_recall_low", evidence_pointers: ["mem_003: user lives in Berlin"]}`
- Expected: rejected with `MonitorRejection(reason="evidence_pointer_not_opaque", detail="mem_003: user lives in Berlin")`.
- State to surface: rejection reason, offending pointer, suggestion to use opaque ID only.

#### Card D: Forbidden Field Rejected
- Monitor output includes `{"diagnosis": "retrieval_error"}` or `{"gold_answer": "Berlin"}`
- Expected: rejected with `MonitorRejection(reason="forbidden_field", detail=field_name)`.
- State to surface: which forbidden field was detected.

#### Card E: All-Enum Exhaustion
- Test that all four enum values are accepted: `answer_vs_evidence_mismatch`, `retrieved_context_incomplete`, `evidence_recall_low`, `confidence_anomaly`.
- Test that each is rejected when misspelled or paraphrased.
- State to surface: acceptance count vs rejection count per enum value.

### State Model

```
MonitorCalled(trace, baseline_state)
  -> MonitorOutputSubmitted(raw_output)
      -> validate_monitor_output(raw_output)
          -> | Accepted: MonitorResult(trigger, reason_enum, confidence, opaque_ids)
          -> | Rejected: MonitorRejection(reason, detail)
```

### Verdict Placeholder

Does the enum exhaustion cover all reasonable monitor trigger scenarios? Are there legitimate anomaly signals that don't fit into the four enum values?
