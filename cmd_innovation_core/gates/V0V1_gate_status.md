# CMD V0→V1 Gate Status

**Last checked:** 2026-05-10
**Gate ID:** V0→V1
**Status:** EVIDENCE_PASSING (HITL review pending)

## Gate Criteria

V0→V1 requires four V0 evidence artifacts to pass paper-claim thresholds (per PRD AC10 and issue 0010).

### Criterion 1: Macro F1 exceeds baselines

- **Artifact:** `artifacts/comparison_metrics.csv`
- **Threshold:** CMD-Audit macro_f1 > evidence_recall AND subagent_judge AND random_label
- **Result:** PASS
- **Evidence:** CMD-Audit macro_f1=1.000; evidence_recall=0.778; subagent_judge=0.778; random_label=0.167
- **Note:** Perfect macro F1 on 6 smoke cases (1 per label). Expected to regress toward realistic values as probe case count increases toward 50-100.

### Criterion 2: Confusion matrix diagonal dominance

- **Artifact:** `artifacts/attribution_confusion_matrix.csv`
- **Threshold:** For each V0 label row: diagonal > sum of off-diagonal entries
- **Result:** PASS
- **Evidence:** All 6 V0 labels have diagonal=1, off-diagonal_sum=0 (perfect identity matrix on smoke cases).
- **Note:** With 1 case per label, off-diagonal is structurally empty. Diagonal dominance must hold as the probe suite scales.

### Criterion 3: Attribution accuracy and top-2 exceed baselines

- **Artifact:** `artifacts/comparison_metrics.csv`
- **Threshold:** CMD-Audit attribution_accuracy > all baselines AND top2_accuracy > all baselines
- **Result:** PASS
- **Evidence:** CMD-Audit attribution_accuracy=1.000 (best baseline=0.833); CMD-Audit top2_accuracy=1.000 (best baseline=0.833).

### Criterion 4: Repair assessment distribution

- **Artifact:** `artifacts/sandbox/post_repair_table.csv`
- **Threshold:** recovered_rate >= 0.5 AND recovered + partial > failed
- **Result:** PASS
- **Evidence:** 6 cases: recovered=6, partial=0, failed=0 (recovered_rate=1.000).
- **Note:** 4/6 cases show `had_repair_regression=true`, indicating the targeted repair introduced regression on the hard-case baseline comparison axis. This does not fail the gate criterion but should be noted in the HITL review.

## Missing Evidence (for scaling beyond smoke)

The four criteria pass against the current 6-case smoke suite, but the PRD targets 50-100 probe cases. The following evidence is thin:

1. **Macro F1** at 1.0 on 6 cases is not a credible paper claim. A scalable suite of 50+ cases with realistic failure distributions is needed.
2. **Confusion matrix** with 1 case per label has no opportunity for off-diagonal entries. Multi-case labels will expose whether certain label pairs (e.g., `compression_error` vs `premature_extraction_error`) are confused.
3. **Repair assessment** at 100% recovered will not hold at scale. The `partial` and `failed` outcomes are diagnostic signals that a smoke suite cannot produce.
4. **Cost per diagnosis** (6.2 token units) is measured on small synthetic cases. Realistic cases will shift this metric.

## HITL Review Log

| Date | Reviewer | Decision | Notes |
|------|----------|----------|-------|
| 2026-05-10 | (pending) | (pending) | Initial evidence check complete. All four criteria pass on 6-case smoke suite. |

## V1→V2 Gate (deferred)

The V1→V2 gate requires at least two distinct memory agents integrated through the Adapter Interface without macro F1 regression. This gate is not yet evaluable — 0 adapter integrations exist in V0.
