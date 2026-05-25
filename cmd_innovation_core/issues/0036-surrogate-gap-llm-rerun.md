---
id: 0036
title: Surrogate-gap LLM-stack rerun (~9/11 deployment claim backing)
status: needs-triage
labels: [paper, decision-34, supplementary, surrogate, deployment-claim]
blocks: []
blocked_by: [0023]
created: 2026-05-24
---、、、、

# 0036 — Surrogate-gap LLM-stack rerun

## Why

Decision 34 R10 / Q18 retains the ~9/11 online deployment claim with retention% backing. Issue 0020-E (now complete) implemented the surrogate-gap measurement on 4 gold-dependent labels (write/compression/premature_extraction/injection) using BM25-retrieved success-trace items as surrogate evidence vs gold-evidence-driven recovery gain. But that implementation ran under the phrase-match shortcut.

This issue re-runs surrogate-gap on a 50-case hold-out under the LLM stack (qwen2.5-7b agent + independent evaluator scorer), producing the retention% number that supports the paper's online deployment story. ~400 LLM calls + analysis.

## Acceptance criteria

| AC | Required behavior | Verified by |
|----|-------------------|-------------|
| AC1 | Sample 50 hold-out cases stratified across 4 gold-dependent labels (write/compression/premature_extraction/injection) — ~12-13 per label. Drawn from cases NOT in the 130-case adjudicated set (no contamination). | sampling script + persisted seed |
| AC2 | For each case, run two paths: (a) gold-evidence path — `oracle_write` / `oracle_compression` / `verbatim_event_oracle` / `injection_oracle` with gold evidence injected, score `recovery_gain` under LLM stack; (b) surrogate path — BM25-retrieved success-trace items injected as surrogate evidence, score under same stack. | per-case row in artifact |
| AC3 | Per-label retention% computed: `mean(surrogate_recovery_gain / gold_recovery_gain)` across cases for that label. Bootstrap CI (1000-iter case-level resample). | `artifacts/surrogate_gap_llm.csv` |
| AC4 | Top-2 retention labels designated as "online-recoverable" per Decision 34 R10/P10 framing. The remaining 2 of 4 labels designated "online-degraded to ECS reporting only." Designation is empirical, post-hoc, defensible. | summary text artifact |
| AC5 | Paper text drafted: "Online CMD-Skill Adapter recovers approximately 9 of 11 pipeline labels: 7 are intervention-mode replays not requiring gold evidence; 2 of 4 remaining (LABEL_X / LABEL_Y) recover via self-supervision surrogate path with retention rate Y% relative to offline gold-evidence baseline (50-case hold-out, 95% CI [a,b]). Remaining 2 (LABEL_W / LABEL_Z) stay gold-evidence-dependent and degrade to ECS reporting only." | paper text fragment |
| AC6 | Honest framing: paper says "retention rate Y%" not "online accuracy Y%." Retention is the measurement; online accuracy depends on integration which is V2. | reviewer-defensible language |
| AC7 | V1.1 trigger: re-run on proportionally-sized hold-out drawn from full corpus (~5% of full corpus, e.g., ~200 cases if full corpus is 4000) to strengthen the retention% CI. | V1.1 AC checklist |

## Sampling protocol

```python
# pseudocode
import random
random.seed(43)  # different seed than 0024's 130-case (0024 uses 42) to avoid overlap
gold_dependent_cases = [c for c in all_596 if c.perturbation_label in {
    'write_error', 'compression_error', 'premature_extraction_error', 'injection_error'
}]
adjudicated_ids = set of 130 case_ids in researcher_labeled_subset.json
hold_out_pool = [c for c in gold_dependent_cases if c.case_id not in adjudicated_ids]
by_label = group_by(hold_out_pool, 'perturbation_label')
sampled = []
for label in ['write_error', 'compression_error', 'premature_extraction_error', 'injection_error']:
    sampled.extend(random.sample(by_label[label], min(13, len(by_label[label]))))
# Persist sampled case_ids in artifacts/surrogate_gap_holdout.json
```

## Files affected

| File | Type |
|------|------|
| `artifacts/surrogate_gap_holdout.json` | new; sampled case_ids |
| `artifacts/surrogate_gap_llm.csv` | new; per-case (case_id, label, gold_recovery_gain, surrogate_recovery_gain, retention_ratio) |
| `artifacts/surrogate_gap_summary.txt` | new; per-label retention% + bootstrap CI + 2-of-4 designation |
| `cmd_audit/surrogate_gap.py` | minor edits to thread through `agent_generate / scorer / answer_verifier` (likely already wired by 0022; verify) |
| `scripts/sample_surrogate_gap_holdout.py` | new; samples hold-out IDs only; no LLM calls |
| `scripts/build_surrogate_gap_summary.py` | new; post-processes completed CSV into retention summary + paper fragment; no LLM calls |

## Out of scope

- Online deployment integration — V2.
- Real success-trace corpus selection — uses existing same-agent-session BM25 retrieval per Decision 32 / issue 0020-E.
- Multi-evaluator robustness on surrogate-gap — single evaluator sufficient at supplementary level.

## Estimate

- LLM calls: 50 × 4 (path) × 2 (gold vs surrogate) = 400 calls + post-repair scoring ≈ 600 calls. ~$1 evaluator + ollama free.
- Wall-clock: 1-2 hours for run + half-day for analysis/writeup.

## Dependency

- Blocked by 0023 (LLM stack must exist; 0023's at-scale run can also produce some of this data as byproduct, but standalone hold-out sampling is cleaner).
- No blockees — supplementary deliverable.

## Detail map

`REPAIR.md` §1 R10 (Q18 acceptance), §16 P10 (paper-craft framing for ~9/11 claim).
