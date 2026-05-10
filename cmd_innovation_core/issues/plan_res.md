# CMD-Audit V0 Main Plan

Date: 2026-05-09

Current selected direction: **D1 Counterfactual Memory Debugger**.

Primary method name: **CMD-Audit / Counterfactual Memory Auditing Module**.

Deployment interface reserved for later: **CMD-Skill Adapter**.

Full roadmap: `plans/cmd_research_plan_and_roadmap.md`.

Resolved decisions: `plans/cmd_open_decisions.md`.

## 1. Goal

Build a standalone research harness that diagnoses memory-augmented agent failures through counterfactual replay, then validates whether the repaired context fixes the original failed query.

The V0 claim is narrow:

> For controlled memory pipeline failures, CMD-Audit can attribute the failure more accurately and more actionably than evidence-recall heuristics or subagent-judge explanations, and its repaired context can recover the original failed query more often than a generic hard-case update.

V0 is not a production memory agent and does not yet solve bad-memory-item diagnosis.

## 2. V0 Scope

V0 evaluates six pipeline labels:

- `write_error`
- `compression_error`
- `premature_extraction_error`
- `retrieval_error`
- `injection_error`
- `reasoning_error`

Explicitly excluded from V0 attribution:

- `item_wrong`
- `item_stale`
- `item_conflict`
- `item_poisoned`
- `item_compression_distorted`

Those item labels remain V1/V2 scope, motivated by STALE and BeliefMem.

Deferred pipeline labels:

- `granularity_error`
- `route_error`
- `graph_error`
- `safety_error`

## 3. Dataset Plan

Use a MEMAUDIT-style package-oracle schema for 50-100 synthetic probe cases.

Each case should include:

```json
{
  "case_id": "...",
  "query": "...",
  "raw_events": ["..."],
  "candidate_memory_representations": ["..."],
  "extracted_memory": ["..."],
  "retrieved_evidence": ["..."],
  "gold_evidence_units": ["..."],
  "gold_answer": "...",
  "future_query_requirements": ["..."],
  "perturbation_label": "write_error | compression_error | premature_extraction_error | retrieval_error | injection_error | reasoning_error",
  "expected_repair_action": "...",
  "expected_repaired_context": "..."
}
```

Initial sources:

- LoCoMo-style long-term dialogue cases.
- LongMemEval-style long-context recall cases.
- HotpotQA-memory multi-hop cases.
- Synthetic package-oracle cases inspired by MEMAUDIT.

## 4. Method Plan

CMD-Audit has five deep modules:

| Module | Responsibility | Output |
|--------|----------------|--------|
| Probe Case Loader | load package-style failure case | normalized case |
| Replay Engine | run operation-level interventions | replay deltas |
| Attribution Layer | choose top-1/top-2 label from recovery gain | attribution result |
| ECS Builder | produce Error-Cause-Solution | compact repair record |
| Post-Repair Context Replay | rebuild context and rerun original query | repair validation |

Required replays:

- Oracle Write
- Oracle Compression
- Verbatim Event Oracle
- Oracle Retrieval
- Injection-Oracle
- Evidence-Given Reasoning

Recovery gain:

\[
\Delta_k = Metric(\hat{y}_k,y)-Metric(\hat{y},y)
\]

Attribution:

\[
c^*=\arg\max_k\Delta_k
\]

If gains are close, emit top-2 or multi-label attribution.

## 5. Monitor And Safety

Subagent Judge has two V0 roles:

1. baseline over the same failed trace;
2. cheap high-recall monitor for triggering expensive replay.

Leak-safe monitor rule:

Allowed outputs:

- `trigger_replay`
- `anomaly_reason`
- `confidence`
- optional redacted evidence pointers

Forbidden outputs:

- final attribution label
- ECS
- gold answer
- full failed trace
- wrong memory payload for future context
- User Memory / Failure Memory writes

SafeHarbor and Agent Worms make this a V0 boundary, not a later hardening task.

## 6. Testing Plan

Primary metrics:

- attribution macro F1
- top-2 attribution accuracy
- post-repair answer score
- post-repair evidence score
- repair success rate
- actionability rating
- monitor trigger recall
- leakage violations
- token/cost per diagnosis

Baselines:

- random label
- evidence-recall heuristic
- subagent judge explanation
- oracle retrieval only
- generic hard-case update

Post-repair validation:

```text
AttributionAssigned
-> ECSDrafted
-> RepairedContextBuilt
-> PostRepairRetested
-> RepairValidated / RepairFailed
-> FutureCaseGuided
```

The repaired context may include corrected memory, repair guidance, repaired evidence format, and raw-event recovered evidence. It must not inject the gold answer or full failed trace.

## 7. MVP Outputs

V0 should produce:

- `data/cmd_probe/*.jsonl`
- `results/attribution_table.csv`
- `results/post_repair_retest.csv`
- `results/subagent_judge_baseline.csv`
- `results/monitor_safety_report.csv`
- `results/claim_ledger.md`

## 8. Promotion Gates

Proceed beyond V0 only if:

- CMD-Audit beats evidence-recall and subagent-judge baselines on attribution macro F1.
- CMD repaired context beats generic hard-case update on original failed-query recovery.
- Verbatim Event Oracle reduces false `retrieval_error` labels.
- Leak-safe monitor triggers replay with acceptable recall and zero leakage violations in the test set.
- The package-oracle probe produces reproducible tables, not only qualitative examples.

## 9. Near-Term Implementation Order

1. Define package-oracle case schema.
2. Build 20 smoke cases, then expand to 50-100.
3. Implement fixed-summary and vector-memory baselines.
4. Implement the six V0 replays.
5. Implement rule-based attribution.
6. Implement ECS Builder.
7. Implement Post-Repair Context Replay.
8. Implement subagent judge baseline and leak-safe monitor.
9. Export claim-bound result tables.

## 10. Follow-up Issue: Strengthen Retrieval Baselines And Evidence Scoring

Status: V0.5 / post-smoke issue. This should be explicit in the project backlog, but it should not block the first minimal V0 harness.

Purpose: keep V0 simple while preventing the retrieval part of the benchmark from becoming too fixture-like. The first probe may use manually specified `retrieved_memory_ids` for oracle control, but the next issue must test whether CMD still separates retrieval failure from earlier extraction/compression loss under real retrieval systems.

Required additions:

- Implement real baseline retrievers: lexical/BM25, vector, hybrid, and hybrid+rerank.
- Record ranked retrieval traces with `memory_id`, `rank`, `score`, `token_cost`, `retrieved_text`, and `matched_gold_evidence_units`.
- Expand evidence scoring from a single `gold_evidence_recall` number to Recall@k, MRR, nDCG, Precision@k, context noise ratio, and answer accuracy/F1.
- Add hard negative cases: same-entity confusion, temporal conflict, paraphrase, multi-hop evidence, stale memory, and compression-loss.

Boundary invariant:

- If Verbatim Event Oracle can recover the evidence but extracted-memory oracle cannot recover it, prefer `premature_extraction_error` over `retrieval_error`. A stronger retriever must not hide the fact that future-needed evidence was never preserved in the extracted memory representation.

Expected outputs:

- `results/retrieval_trace.csv`
- `results/retrieval_metrics.csv`
- `data/cmd_probe/hard_negative_suite.jsonl`

## 11. Current Non-Goals

- Production memory-agent integration.
- Learned attribution classifier.
- Internal circuit signal diagnosis.
- Full bad-memory-item taxonomy.
- Full LoCoMo / LongMemEval scale.
- UI dashboard.
