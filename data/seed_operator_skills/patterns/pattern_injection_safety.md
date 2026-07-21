# Pattern: injection_error

**Trigger Fingerprint**: review_required

**Trigger Conditions**:
- Recall-content fingerprint matches the source case cluster.
- Retrieved memory shape is compatible with the operator spec.
- Multihop residual: hop-1 evidence present but mis-ordered in the
  injection buffer, hop-2 evidence redacted or filtered by the safety
  layer (safety-reviewed provenance mark present on the hop-2 item).

**Diagnosis**: injection_error

## Operator Spec
```operator-spec
 - hop=1 action=injection_error select=injection_buffer transform=re_emit_ordered
 - hop=2 action=safety_error select=safety_reviewed transform=restore_redacted
```

## Recovery Track Record
- Recovered source cases: 7/115
- Average recovery gain: 0.460
- Acceptance gate: execute the operator and keep it only if recovery improves.

## Source Cases
- Exp21 composite scan, `artifacts/sandbox/operator_headroom_detail.csv`
  rows with shape `gp0:injection_error+gp1:safety_error` (seed prior,
  shape-level; per-case ids rewritten by the store on first cluster hit).
