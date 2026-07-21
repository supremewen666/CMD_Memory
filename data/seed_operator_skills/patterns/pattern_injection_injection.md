# Pattern: injection_error

**Trigger Fingerprint**: review_required

**Trigger Conditions**:
- Recall-content fingerprint matches the source case cluster.
- Retrieved memory shape is compatible with the operator spec.
- Multihop residual: single-point structural repair at any one generation
  point failed to recover; both hop-1 and hop-2 injection buffers hold the
  needed items but in unusable order.

**Diagnosis**: injection_error

## Operator Spec
```operator-spec
 - hop=1 action=injection_error select=injection_buffer transform=re_emit_ordered
 - hop=2 action=injection_error select=injection_buffer transform=re_emit_ordered
```

## Recovery Track Record
- Recovered source cases: 9/115
- Average recovery gain: 0.441
- Acceptance gate: execute the operator and keep it only if recovery improves.

## Source Cases
- Exp21 composite scan, `artifacts/sandbox/operator_headroom_detail.csv`
  rows with shape `gp0:injection_error+gp1:injection_error` (seed prior,
  shape-level; per-case ids rewritten by the store on first cluster hit).
