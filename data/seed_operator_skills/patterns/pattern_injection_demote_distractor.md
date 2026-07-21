# Pattern: injection_error

**Trigger Fingerprint**: review_required

**Trigger Conditions**:
- Recall-content fingerprint matches the source case cluster.
- Retrieved memory shape is compatible with the operator spec.
- Multihop residual: hop-2 recall contains a high-signal distractor that
  outranks the gold item; structural re-emit alone is insufficient without
  demoting the distractor.

**Diagnosis**: injection_error

## Operator Spec
```operator-spec
 - hop=2 action=injection_error select=injection_buffer transform=re_emit_ordered
```

Parameterization: this operator is executed with `item_signal_hints`
(weight -1 on the distractor memory item). The hint ids are case-specific
and are instantiated at execution time from the item-gate output
(`item_gate/` divergence + collision signals, wired through
`harness.py` item_signal_hints); the seed pattern intentionally leaves the
`params.item_signal_hints` line out of the spec block so no fabricated
memory_id is ever executed.

## Recovery Track Record
- Recovered source cases: 9/115
- Average recovery gain: 0.384
- Acceptance gate: execute the operator and keep it only if recovery improves.

## Source Cases
- Exp21 parameterized scan, `artifacts/sandbox/operator_headroom_detail.csv`
  rows with shape `gp1:*+item_signal_hints[demote]` (seed prior,
  shape-level; per-case ids rewritten by the store on first cluster hit).
