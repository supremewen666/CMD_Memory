---
status: draft
labels:
  - needs-triage
issue_tracker: local-markdown
source: cmd_innovation_core core reading path
---

# PRD: CMD Minimal Probe

## Problem Statement

Memory-augmented agents can fail because the stored memory is wrong, because a correct memory was mishandled by the memory pipeline, or because the final model reasoned poorly over valid evidence. Today the failure is usually observed only as a wrong final answer, leaving the researcher unable to decide whether to improve writing, compression, premature extraction, retrieval, injection, or reasoning.

CMD needs a minimal probe that converts this debugging problem into intervention-grounded attribution, then tests whether the resulting labels produce useful repairs and reusable Failure Memory.

## Solution

Build a standalone CMD-Audit harness that starts with 50-100 labeled memory failure cases, runs baseline memory systems and V0 counterfactual replays, computes recovery gains, assigns operation-level attribution labels, and emits an attribution table. Then use those labels to drive targeted memory fixes, Error-Cause-Solution records, and Post-Repair Context Replay before evaluating future similar tasks.

The first milestone is an attribution table, not a production memory architecture. The code should reserve an adapter interface for later memory-agent integration but should not depend on an existing memory agent in V0.

## Current Execution Addendum

As of 2026-05-10, issues 0001-0005 and 0009 are complete:

- one `retrieval_error` probe case loads through the public CMD-Audit harness;
- Oracle Retrieval recovers the answer and produces a correct attribution row;
- fixed-summary and vector-memory baselines are represented;
- evidence-recall, subagent judge, and random-label comparators are represented separately from CMD attribution;
- Subagent Judge Monitor is leak-safe with enum-locked `anomaly_reason` and opaque evidence pointers;
- the six V0 replay paths produce one smoke attribution row per V0 label;
- Post-Repair Context Replay outputs three-value `repair_assessment` (`recovered`/`partial`/`failed`);
- `artifacts/attribution_table.csv`, `artifacts/comparison_metrics.csv`, and `artifacts/attribution_confusion_matrix.csv` exist as initial smoke artifacts;
- 57 tests pass.

The active slice is issue 0006: validate targeted memory fixes.

## Competitive Landscape Addendum (2026-05-10 Metabolism)

A broad survey across arxiv + openalex + GitHub (27 papers, 10 repos) confirms CMD's differentiation:

- **No existing work does automated counterfactual memory replay for operation-level attribution.** The closest approaches are observational (Trajectory-Informed Memory 2603.10600), interactive (Peaky Peek/agent_debugger), or binary-detection (D-MEM 2603.14597).
- **Failure Memory pattern is independently validated**: A-MemGuard (2510.02373), SQLFixAgent (2406.13408), and Reflection-Driven Control (2512.21354) all converge on storing repair lessons separately from active memory.
- **Risk-sensitive retrieval** (RSCB-MC 2604.27283) suggests CMD's `retrieval_error` attribution should distinguish "retrieved wrong memory" from "retrieved right memory but injected unsafely."
- **Engineering demand validated**: Peaky Peek (agent_debugger, 5 stars, v0.1.18) and AgentLens confirm that agent memory debugging is an emerging tooling category. Neither does counterfactual attribution.

CMD's paper claim is strengthened: the competitive gap between observational diagnosis and counterfactual attribution is real and unoccupied.

## V0 Scope

V0 covers six core pipeline labels:

- `write_error`
- `compression_error`
- `premature_extraction_error`
- `retrieval_error`
- `injection_error`
- `reasoning_error`

`granularity_error`, `route_error`, `graph_error`, `safety_error`, and `ingestion_error` are deferred to V1/V2.

V0 excludes bad memory item labels from attribution evaluation: `item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, and `item_compression_distorted`.

## Boundary Rules and Acceptance Conditions

- AC1: `CONTEXT.md` defines **CMD-Audit** and **CMD-Skill Adapter** separately.
- AC2: **Subagent Judge Monitor** is leak-safe: it can trigger replay but cannot emit final labels, Error-Cause-Solution, memory writes, gold answers, or full failed traces.
- AC3: V0 attribution excludes bad memory item labels and evaluates only six pipeline labels: `write_error`, `compression_error`, `premature_extraction_error`, `retrieval_error`, `injection_error`, and `reasoning_error`.
- AC4: Post-Repair Context Replay is a required V0 gate: it rebuilds repaired context, reruns the original failed query, does not inject the gold answer, and outputs layered scores (`post_repair_answer_score`, `post_repair_evidence_score`) with a three-value `repair_assessment` (`recovered` / `partial` / `failed`), not a binary gate.
- AC5: `write_error` subsumes "evidence never reached the agent" cases in V0. `ingestion_error` is deferred to V1 as a potential future split if these cases have distinct repair paths.
- AC6: **Subagent Judge Monitor** `anomaly_reason` is locked to a predefined enum (`answer_vs_evidence_mismatch`, `retrieved_context_incomplete`, `evidence_recall_low`, `confidence_anomaly`); free-form natural language is prohibited. Evidence pointers are opaque IDs only, never content text.
- AC7: ECS `cause` may describe item state in natural language but must not use V0-forbidden item label names (`item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`) or re-declare them through natural language equivalents.
- AC8: **CMD-Audit** write permissions are limited to replay-local sandbox (in-memory probe state, repaired context construction). It must not write to a real agent's persistent memory. Only **CMD-Skill Adapter** applies validated repairs to production agent state.
- AC9: Stronger V0.5 retrieval baselines may flip attribution to `retrieval_error` only when `evidence_recall_from_text(gold_evidence, memory_item.text)` confirms the Memory Item text contains the evidence. When the Memory Item text does not contain the evidence phrases (extraction already lost them), the label stays `premature_extraction_error`.
- AC10: Version gates V0→V1→V2 are evidence-driven, not feature-stacking: V0→V1 requires the four V0 evidence artifacts passing paper-claim thresholds; V1→V2 requires at least two distinct memory agents integrated through the Adapter Interface without macro F1 regression.

## User Stories

1. As a memory researcher, I want a labeled probe dataset, so that I can measure whether CMD recovers injected failure causes.
2. As a memory researcher, I want each probe case to include gold evidence, so that replay outcomes can be scored against evidence rather than subjective judgment.
3. As a memory researcher, I want baseline memory outputs, so that CMD can measure recovery gain from a failed starting point.
4. As a memory researcher, I want fixed-summary and vector-memory baselines, so that the first results are not tied to one memory design.
5. As a memory researcher, I want a subagent judge baseline, so that CMD is compared against strong post-hoc trace explanation.
6. As a memory researcher, I want a subagent judge monitor, so that expensive replay is triggered with high recall but final attribution still comes from CMD deltas.
7. As a memory researcher, I want evidence recall heuristics, so that CMD is compared against cheap retrieval-centered labels.
8. As a memory researcher, I want Oracle Write replay, so that missing-write failures can be separated from retrieval failures.
9. As a memory researcher, I want Oracle Compression replay, so that compression loss can be isolated.
10. As a memory researcher, I want Oracle Retrieval replay, so that recoverable but missed memories can be diagnosed.
11. As a memory researcher, I want Verbatim Event Oracle replay, so that ingestion-time abstraction loss is diagnosed as first-class `premature_extraction_error`.
12. As a memory researcher, I want Evidence-Given Reasoning replay, so that reasoning failure can be separated from memory failure.
13. As a memory researcher, I want Injection-Oracle replay, so that correct evidence in a bad context format can be identified.
14. As a memory researcher, I want top-2 or multi-label attribution, so that coupled failures are not forced into a false single-cause story.
15. As a memory researcher, I want an attribution table, so that claims can be tied to macro F1, top-2 accuracy, and cost per diagnosis.
16. As a memory researcher, I want targeted memory fixes mapped from labels, so that each diagnosis produces an actionable repair.
17. As a memory researcher, I want Post-Repair Context Replay, so that I can verify the repaired context recovers the original failed query.
18. As a memory researcher, I want Error-Cause-Solution records, so that repairs are compact and reusable.
19. As a memory researcher, I want Failure Memory retrieval to inject only corrected memory and repair guidance, so that old wrong traces do not pollute future context.
20. As a paper author, I want a claim ledger, so that no abstract or conclusion claim appears before its supporting experiment exists.
21. As a reviewer, I want CMD compared with subagent judge, so that the contribution is not merely another natural-language evaluator.
22. As a future implementer, I want the public behavior named before code exists, so that TDD can proceed one tracer bullet at a time.
23. As a future implementer, I want an adapter interface reserved, so that the standalone harness can later plug into a memory agent without coupling V0 to production integration.
24. As a V1/V2 researcher, I want granularity, route, graph, and safety interventions deferred, so that V0 stays small enough to reproduce quickly.
25. As a memory researcher, I want `write_error` to cover "evidence never reached the agent" cases in V0, so that the label set stays at six while still capturing ingestion-absence failures.
26. As a V1 researcher, I want `ingestion_error` noted as a deferred label, so that if ingestion-absence cases prove to have distinct repair paths, they can be split out later.
27. As a security reviewer, I want the Subagent Judge Monitor's `anomaly_reason` locked to a predefined enum with no free-form natural language, so that leak paths through diagnostic text are eliminated.
28. As a security reviewer, I want Monitor evidence pointers limited to opaque IDs without content text, so that the monitor cannot leak evidence through pointer payloads.
29. As a memory researcher, I want CMD-Audit write permissions limited to replay-local sandbox, so that the research harness cannot accidentally modify production agent memory.
30. As a memory researcher, I want Post-Repair Context Replay to output a three-value `repair_assessment` (`recovered` / `partial` / `failed`) rather than binary, so that partial repairs that expose coupled failures are visible as diagnostic signal.
31. As a memory researcher, I want ECS `cause` to describe item state in natural language without using forbidden item label names, so that repair records are actionable without creating a backdoor item-label evaluation channel.
32. As a paper author, I want version gates V0→V1→V2 driven by evidence thresholds rather than feature completion, so that each version is credible before the next begins.
33. As a V0.5 researcher, I want the boundary that stronger retrievers cannot flip `premature_extraction_error` to `retrieval_error` when the Memory Item text lacks the evidence phrases, so that extraction-stage loss is never masked by retrieval improvements.

## Implementation Decisions

- Treat CMD V0 as a standalone research harness first, not a production memory architecture.
- Limit V0 attribution to six labels: `write_error`, `compression_error`, `premature_extraction_error`, `retrieval_error`, `injection_error`, and `reasoning_error`.
- Exclude bad memory item labels from V0 attribution evaluation; they remain taxonomy context, not V0 scoring targets.
- Promote `premature_extraction_error` to a first-class pipeline label diagnosed by Verbatim Event Oracle.
- Make the probe dataset the first deep module: it should expose a small, stable interface for labeled cases, gold evidence, perturbation labels, and expected outputs.
- Make the replay engine the second deep module: it should accept a failed case and replay configuration, then return replay outputs and recovery gains.
- Make the attribution layer the third deep module: it should convert replay deltas into top-1, top-2, or multi-label operation-level attributions.
- Make the ECS layer the fourth deep module: it should convert a diagnosis into a compact repair record and future retrieval guidance.
- Include Verbatim Event Oracle in the initial scope because the current research memory marks it as central to distinguishing retrieval failure from ingestion-time information loss.
- Include subagent judge as both a baseline and cheap high-recall monitor, not as the source of CMD labels, ECS, memory writes, gold answers, or full failed traces.
- Require Post-Repair Context Replay after ECS drafting and before future Failure Memory evaluation.
- Reserve an adapter interface for future memory-agent integration without implementing that integration in V0.
- Keep learned attribution out of the first milestone. Use rule-based deltas first, then consider a learned classifier after replay traces exist.
- Keep internal circuit signals out of the first milestone. Treat them as a future extension.
- Keep the first output as tables and issue-ready claims, not as a polished demo.
- Treat issue 0001 and issue 0002 as the current smoke foundation. Do not restart the probe contract or baseline layer unless issue 0003 exposes a contract gap.
- Track stronger retrieval baselines and evidence scoring as a V0.5 follow-up issue after the first six-replay attribution table exists.
- For issue 0003, preserve a deep replay interface: each replay should return a common result shape with replay name, answer score, evidence score, evidence block, recovery gain, and cost.
- For `premature_extraction_error` cases, gold evidence should point to raw events and omit `source_memory_id` when extracted memory has no recoverable evidence. Do not encode extraction loss as a missing memory reference.
- Lock **Subagent Judge Monitor** `anomaly_reason` to a predefined enum (`answer_vs_evidence_mismatch`, `retrieved_context_incomplete`, `evidence_recall_low`, `confidence_anomaly`). Prohibit free-form natural language. Evidence pointers are opaque IDs only.
- ECS `cause` may describe item state (e.g., "stored preference was outdated relative to ground truth") but must not use V0-forbidden item label names or re-declare them through natural language (e.g., "the memory item is stale").
- **CMD-Audit** write permissions are limited to replay-local sandbox. It may modify in-memory probe state and construct repaired context for Post-Repair Context Replay, but must not write to a real agent's persistent memory. Only **CMD-Skill Adapter** applies validated repairs to production agent state.
- Post-Repair Context Replay outputs `post_repair_answer_score`, `post_repair_evidence_score`, and a three-value `repair_assessment` (`recovered` / `partial` / `failed`). `partial` means evidence recovered but answer still wrong—this exposes coupled failures, which is positive diagnostic signal.
- Version gates V0→V1→V2 are evidence-driven: V0→V1 requires the four V0 evidence artifacts passing paper-claim thresholds; V1→V2 requires at least two distinct memory agents integrated through the Adapter Interface without macro F1 regression.
- Stronger V0.5 retrieval baselines may flip attribution to `retrieval_error` only when `evidence_recall_from_text(gold_evidence, memory_item.text)` confirms the Memory Item text contains the evidence. When the Memory Item text lacks the evidence phrases, no retriever improvement can change `premature_extraction_error`.

## Testing Decisions

- Good tests should verify observable behavior through public interfaces, not internal helper functions.
- The first tests should be integration-style tracer bullets: one probe case, one replay set, one attribution output.
- The replay engine should be tested with synthetic cases where the correct intervention is known.
- The attribution layer should be tested for top-1, top-2, and tied-delta behavior.
- The Verbatim Event Oracle path should be tested before broad retrieval tests because it protects the central label boundary.
- The ECS layer should be tested by checking that future retrieval exposes corrected memory and repair guidance, not the complete failed trace.
- Post-Repair Context Replay should be tested by rerunning the original failed query with repaired context and without injecting the gold answer.
- The subagent judge monitor should be tested for high-recall replay triggering, while final labels remain replay-delta grounded and no forbidden payloads are emitted.
- The judge baseline should be tested as a comparator, not as an oracle that can make CMD pass.
- The adapter interface should be tested only as a boundary contract, not as integration with a real memory agent.
- The first implementation established behavior-level smoke tests; keep extending through public behavior rather than implementation details.
- Current smoke tests already cover the retrieval-error tracer bullet, comparison metrics, and leak-safe monitor rejection. The next red-green cycle should cover raw-event-only recovery as `premature_extraction_error`.
- Issue 0003 tests should be vertical: one new failing behavior at a time, starting with Verbatim Event Oracle, then only later adding the remaining V0 replay paths and top-2 table behavior.

## Out of Scope

- Full production deployment of an online memory system.
- Existing memory-agent integration beyond an adapter interface.
- Full LoCoMo, LongMemEval, or HotpotQA-scale runs before the small probe succeeds.
- `granularity_error`, `route_error`, `graph_error`, and `safety_error` interventions in V0.
- Learned CMD classifiers before rule-based replay deltas are validated.
- Internal activation or circuit analysis.
- UI dashboards.
- Claims that CMD improves memory agents before attribution and repair evidence exists.

## Further Notes

The first paper-worthy gate is whether CMD beats heuristic evidence recall and subagent judge on attribution accuracy, repair success, and stability. If CMD does not beat those baselines, the contribution should be narrowed to taxonomy and benchmark construction.

Do not let stronger V0.5 retrieval baselines collapse `premature_extraction_error` into `retrieval_error`. The boundary is `evidence_recall_from_text(gold_evidence, memory_item.text)`: stronger retrieval may flip the label to `retrieval_error` only when the Memory Item text contains the evidence and a weak retriever simply missed it. When the Memory Item text does not contain the evidence phrases at all (extraction already lost them), no retriever improvement can change the label—it stays `premature_extraction_error`.
