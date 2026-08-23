# P4C mainline analysis round 1 — case and metric audit

## Observed v1 evidence

- P4C-1 used `limit_per_source=5`: 15 repair cases, not the 500-row
  LongMemEval protocol.
- P4C-3 evaluated those 15 faults plus 15 post-repair clean controls.
- P4C-4/5 expanded three base templates into 30 robustness cells and eight
  arms (240 outcomes).

## Problems found

1. One shared P4C-1 limit could not represent LongMemEval 500 and MemFail 92.
2. P4C-4/5 `syndrome_resolution_rate` described shadow resolution even when
   ECC rolled the transition back.
3. `recurrence_rate` mixed no-repair, pre-commit repeat probes, and unsafe
   commits; it was not post-commit recurrence.
4. `mean_receipt_utility` included rows without typed receipts.
5. Adaptive arms had differently directed manual priors and no sealed
   calibration/adaptation/holdout split.

## Frozen v2 work package

- independent P4C-1 source counts;
- monotonic fault/post-repair telemetry order;
- safe committed correction as the primary structural metric;
- numerator/denominator for every rate;
- matched zero-prior and typed-prior frozen/evolution arms;
- receipt-only updates and a no-update holdout;
- explicit distinction between real-source cases and replicated robustness
  variants.
