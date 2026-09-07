# SIGIL-QD V0 — Descriptor Policy Value Result

- **Protocol:** `sigil-qd-v0-descriptor-policy-v2`
- **Decision:** `NO_GO`
- **Domains:** MemFail `NO_GO`; MemTrace `NO_GO`; STALE `NO_GO`
- **Model calls:** 0
- **Bootstrap:** one-sided family-blocked, 10,000 resamples, seed 24
- **Outer evaluation:** five-fold family-grouped cross-fitting
- **Authoritative decision:**
  `artifacts/sigil_qd/v0_descriptor_policy/v0_claim_decision.json`

## Decision

The inspected runtime descriptors do not support a positive niche-local
selection or evolution claim.

This is not a headroom failure. All three domains contain significant oracle
repair headroom:

| Domain | Oracle headroom | One-sided LB95 | Result |
|---|---:|---:|---|
| MemFail | +0.1322 | +0.1110 | present |
| MemTrace | +0.0795 | +0.0626 | present |
| STALE | +0.1965 | +0.1834 | present |

The failure is conditional policy value: the runtime descriptors do not
identify different, stable held-out repair policies that beat an equal-budget
unkeyed policy.

## Primary contrasts

| Domain | Descriptor − frozen | LB95 | Descriptor − unkeyed | LB95 | Descriptor − random niche | LB95 |
|---|---:|---:|---:|---:|---:|---:|
| MemFail | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| MemTrace | -0.0305 | -0.0433 | -0.0144 | -0.0237 | -0.0144 | -0.0238 |
| STALE | +0.0802 | +0.0650 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

STALE does improve over the recorded frozen selector. However, the unkeyed and
random-niche controls obtain the same improvement. Both supported STALE niches
select the same stable elite:

```text
recall_set_collision:very_high
    -> seed:injection_error, fold agreement 1.0

temporal_content_contradiction:very_high
    -> seed:injection_error, fold agreement 1.0
```

Therefore the STALE effect is evidence for a possible global repair-policy
improvement, not evidence that the descriptor or MAP-Elites ecology adds value.

MemTrace is worse than every registered control and also violates the exact
null/Fill protection gate. MemFail has supported descriptor cells but no stable
descriptor-specific elite after the invalid safety candidate is excluded.

## Integrity correction made before the final run

`seed:safety_error` was removed from the V0 candidate set. Its availability was
conditioned by the same synthetic safety pathway already shown to be
label-equivalent. Allowing it would let the policy infer the target through
candidate availability even when safety metadata was absent from the explicit
descriptor.

The first diagnostic run was also corrected so that:

- inadequate sample occupancy yields `INSUFFICIENT_SUPPORT`;
- adequate samples with no differentiated stable elites yield `NO_GO`.

The final decision above was produced only after both corrections and the full
test suite passed.

## Authorized next work

Under the frozen V2 stop condition:

- do not build or run the V1 non-short-circuit MAP-Elites archive on these
  inspected benchmarks;
- do not spend untouched confirmatory data on the evolution claim;
- retain the leakage audit, bounded single-proxy non-identifiability result,
  and sensor/controller separation as the evolution chapter;
- move the experimental budget to repair and null protection;
- evaluate the cross-fitted global STALE policy as a repair hypothesis against
  the named context-stuffing, current CMD, random-legal-operator, and
  cross-judge controls.

The global STALE policy is a new repair hypothesis. It is not a rescued
evolution result and must receive its own frozen repair protocol.
