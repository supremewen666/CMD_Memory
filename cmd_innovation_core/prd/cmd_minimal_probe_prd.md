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

As of 2026-05-19, issues 0001-0015 are complete. 453 tests pass, 622 subtests pass. V1 label expansion complete (issues 0011-0012: all 11 pipeline labels active). V1 coupled-failure recalibration and memory-probe baseline complete (issue 0013). mem0 adapter (first CMD-Skill Adapter target) complete (issue 0014). Letta adapter (second CMD-Skill Adapter target) complete (issue 0015). V1→V2 gate passes with both `mem0_integrated=True` and `letta_integrated=True`.

- one `retrieval_error` probe case loads through the public CMD-Audit harness;
- Oracle Retrieval recovers the answer and produces a correct attribution row;
- fixed-summary and vector-memory baselines are represented;
- evidence-recall, subagent judge, and random-label comparators are represented separately from CMD attribution;
- Subagent Judge Monitor is leak-safe with enum-locked `anomaly_reason` and opaque evidence pointers;
- the six V0 replay paths produce one smoke attribution row per V0 label;
- Post-Repair Context Replay outputs three-value `repair_assessment` (`recovered`/`partial`/`failed`);
- V1 `ingestion_error` + `route_error` labels implemented: 8-label pipeline, 7-replay V1 portfolio, `has_ingestion_trace` boundary, `oracle_route` store enumeration;
- V1 `granularity_error` + `graph_error` + `safety_error` labels implemented: 11-label pipeline, 10-replay V1 portfolio;
- V1 coupled-failure recalibration: configurable `top_k` parameter (default 2, supports 3+), `close_deltas` transparent delta distribution;
- V1 memory-probe 3x2 grid baseline: 3 write strategies × 2 retrieval methods (cosine + BM25; dense retrieval deferred to V1 adapter layer per issue 0008), aggregate best-cell accuracy in `comparison_metrics.csv`;
- 4 retrieval helpers made public (`tokenize`, `compute_bm25_scores`, `build_tfidf_vectors`, `cosine_similarity`) for cross-module reuse;
- mem0 adapter (`Mem0Adapter` with `intercept_add`/`intercept_search` sandbox-interception, recorded-trace mode, adapter-label parity);
- `artifacts/attribution_table.csv` with `top_k_labels` and `close_deltas` columns; `artifacts/comparison_metrics.csv` with optional `memory_probe_best_accuracy` column;
- Letta adapter (`LettaAdapter` with `intercept_core_write`/`intercept_archival_store`/`intercept_recall` three-cut-point sandbox-interception, tripartite memory model, recorded-trace mode, adapter-label parity, cross-agent non-regression);
- V1→V2 gate now passes with both `mem0_integrated=True` and `letta_integrated=True`.
- 453 tests pass, 622 subtests pass.

The active slice is issue 0016: RPE prefilter (evidence-surprise scoring, top-k replay selection).

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

`granularity_error`, `graph_error`, and `safety_error` are deferred to V1 (issue 0012). `route_error` and `ingestion_error` are V1-active via issue 0011.

V0 excludes bad memory item labels from attribution evaluation: `item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, and `item_compression_distorted`.

## Boundary Rules and Acceptance Conditions

- AC1: `CONTEXT.md` defines **CMD-Audit** and **CMD-Skill Adapter** separately.
- AC2: **Subagent Judge Monitor** is leak-safe: it can trigger replay but cannot emit final labels, Error-Cause-Solution, memory writes, gold answers, or full failed traces.
- AC3: V0 attribution excludes bad memory item labels and evaluates only six pipeline labels: `write_error`, `compression_error`, `premature_extraction_error`, `retrieval_error`, `injection_error`, and `reasoning_error`.
- AC4: Post-Repair Context Replay is a required V0 gate: it rebuilds repaired context, reruns the original failed query, does not inject the gold answer, and outputs layered scores (`post_repair_answer_score`, `post_repair_evidence_score`) with a three-value `repair_assessment` (`recovered` / `partial` / `failed`), not a binary gate.
- AC5: `write_error` subsumes "evidence never reached the agent" cases in V0. `ingestion_error` is now a V1-active label (issue 0011) that splits these cases from `write_error` when `has_ingestion_trace=false`.
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

---

## V1 Scope (2026-05-11)

### V1 Problem Statement

V0 proved that counterfactual attribution works on a standalone harness with 6 pipeline labels and synthetic perturbations. This establishes the method's internal validity. But three questions remain before CMD can claim practical value:

1. **Label coverage gap**: V0 deliberately excluded `granularity_error`, `route_error`, `graph_error`, `safety_error`, and `ingestion_error`. Real agent memory failures involve these pipeline stages, and without them CMD cannot diagnose the full failure surface. Memory-Probe (2603.02473) independently demonstrated that retrieval-vs-write is only the first diagnostic axis—route and granularity matter too.

2. **Standalone-to-real gap**: V0 runs on fixture-controlled memory operations. A real memory agent (mem0, Letta) has actual `add()` and `search()` calls, entity linking, multi-signal fusion, and tiering. CMD must show that its counterfactual replays can intercept real operations without knowing agent internals, and that attribution accuracy does not degrade when moving from fixtures to real systems.

3. **Comparator gap**: V0 compared against evidence-recall heuristics, subagent judge, and random baselines. Memory-Probe's 3×2 grid-comparison (3 write × 2 retrieval: cosine + BM25; dense retrieval deferred to V1 adapter layer) is a stronger diagnostic baseline—it explicitly separates write quality from retrieval quality at the aggregate level. V1 must add this comparator and show CMD's case-level counterfactual attribution outperforms aggregate grid-comparison.

4. **Provenance gap**: CMD currently lacks influence provenance—it cannot track which memory items influenced which decisions or downstream items. Five provenance papers (MemLineage, TRACER, PACT, Execution Lineage, MemQ) in a single week confirm provenance is becoming fundamental infrastructure for memory trust and reproducibility. Without provenance tracking, CMD cannot: (a) validate `graph_error` attribution (graph edge influence requires lineage), (b) compute cascade repair (which downstream items were affected by a root failure), (c) enable MemQ TD(λ) credit propagation in V2. V1 must add basic provenance tracking: a derivation DAG recording which items influenced each new memory creation.

V1 addresses all four gaps by expanding to 11 pipeline labels, integrating with mem0 (first target) and Letta (second target), adding memory-probe as a new baseline comparator, and implementing Execution Lineage DAG provenance tracking.

### V1 Solution

Extend the CMD-Audit harness and introduce CMD-Skill Adapter:

**Label expansion (11 labels):** Add 5 deferred pipeline labels in priority order—`ingestion_error` (split from `write_error`, ✅ done issue 0011), `route_error` (✅ done issue 0011), `granularity_error` (✅ done issue 0012), `graph_error` (✅ done issue 0012), `safety_error` (✅ done issue 0012). Each new label has a corresponding counterfactual replay. All 11 pipeline labels active.

**CMD-Skill Adapter (mem0 first):** Build `Mem0Adapter` with two interception cut points: `intercept_add()` for write-side replays and `intercept_search()` for retrieval-side replays. The Adapter runs sandboxed—it never mutates the original mem0 store. The ReplayEngine, Attribution, and ECS layers are unchanged from V0; only the input source changes from fixture to intercepted operations. After mem0 is proven, integrate Letta as the second agent (V1→V2 gate: ≥2 agents, no macro F1 regression).

**Baseline strengthening:** Add memory-probe grid-comparison as a new comparator (✅ done issue 0013). Recalibrate top-2/multi-label thresholds for 11-label space with configurable `top_k` and transparent `close_deltas` (✅ done issue 0013). Re-run all V0 baselines against 11-label attribution.

**Real data (researcher-led):** Mix LoCoMo/LongMemEval real data into probe cases alongside synthetic perturbations. Data construction is researcher-led; CMD-Audit consumes the resulting probe files.

**RPE Pre-Filter (late V1):** Add D-MEM-style RPE gating to reduce replay cost. Deferred to late V1—not a gate prerequisite.

**Provenance tracking (V1, Issue 0017):** Add Execution Lineage DAG per MemoryItem with trace-mem HMAC citation format. Phase 1 records in-edge derivation per replay (which items influenced this item's creation). Phase 2 (V2) enables cascade repair via MemQ TD(λ) on the provenance DAG. Architecture per Decision 28: `ProvenanceEdge` (source_id, target_id, operation, Citation) + `Citation` (trajectory_turn, char_span, content_hash). Required for `graph_error` validation and cascade repair integration.

### V1 Boundary Rules and Acceptance Conditions

- **AC11 (Label non-regression):** Adding 5 V1 pipeline labels must not change attribution on the existing V0 6-label smoke suite. Macro F1 on the 6-label subset must remain 1.000.
- **AC12 (mem0 Adapter sandbox):** `Mem0Adapter` replays run in sandbox. After any replay, the original mem0 store checksum must match the pre-replay checksum. No CMD-Audit operation writes to production mem0 state.
- **AC13 (Adapter-label parity):** For the same probe case, the mem0 adapter path and standalone harness path must produce the same attribution label. Any discrepancy is a bug.
- **AC14 (V1→V2 gate):** At least two distinct memory agents (mem0 + Letta) must be integrated through the Adapter Interface. 11-label macro F1 on the second agent must not regress below the first agent's baseline.
- **AC15 (ingestion vs write boundary):** `ingestion_error` is attributed only when gold evidence never reached the agent at all (no corresponding `add()` call in trace). If `add()` was called but stored wrong/unformatted content, the label remains `write_error`.
- **AC16 (Provenance DAG completeness):** Every `MemoryItem` produced or modified by a counterfactual replay must carry `provenance: List[ProvenanceEdge]` recording in-edge derivation. Provenance completeness (fraction of items with non-empty provenance) must be measured on the 596-case suite. Content hash tamper detection must flag mismatches. `graph_error` attribution must reference provenance edges showing which graph-distractor items influenced the failed answer.

### V1 User Stories

#### A. Mainline — Adapter Integration (core V1 value)

- US34: As a researcher, I want CMD-Skill Adapter to intercept mem0's `add()` operation so that write-side counterfactual replays (Oracle Write, Oracle Compression, Verbatim Event Oracle, Injection-Oracle) can replace or bypass the stored facts with oracle variants.
- US35: As a researcher, I want CMD-Skill Adapter to intercept mem0's `search()` operation so that retrieval-side counterfactual replays (Oracle Retrieval, Evidence-Given Reasoning) can replace or augment the retrieved facts.
- US36: As a researcher, I want CMD-Audit running through the mem0 adapter to produce the same attribution labels as the standalone harness for identical probe cases, so that the adapter does not introduce attribution errors.

#### B. Support — Label Expansion (complexity that makes V1 more than "V0 on real system")

- US37: As a researcher, I want `ingestion_error` to be split from `write_error` so that "evidence never reached the agent" is distinguished from "evidence reached the agent but was not stored." — ✅ implemented (issue 0011)
- US38: As a researcher, I want `route_error` diagnosed by Oracle Route replay so that wrong store/tier routing failures are attributed correctly. — ✅ implemented (issue 0011)
- US39: As a researcher, I want `granularity_error` diagnosed by Oracle Granularity replay so that wrong memory granularity failures are attributed correctly. — ✅ implemented (issue 0012)
- US40: As a researcher, I want `graph_error` diagnosed by Graph-Off replay so that graph expansion distractor failures are attributed correctly. — ✅ implemented (issue 0012)
- US41: As a researcher, I want `safety_error` diagnosed by Safety-Off replay so that safety filter false-positive failures are attributed correctly. — ✅ implemented (issue 0012)

#### C. Validation — Baselines and Data

- US42: As a researcher, I want memory-probe 3×2 grid-comparison added as a V1 baseline comparator so that CMD is measured against the strongest existing diagnostic approach. — ✅ implemented (issue 0013)
- US43: As a researcher, I want LoCoMo and LongMemEval real-data probe cases mixed with synthetic perturbation cases so that V1 evaluation covers both controlled and natural failure distributions.

#### D. Infrastructure — Provenance Tracking (Issue 0017)

- US44: As a researcher, I want each `MemoryItem` to carry a `provenance` field recording which items and operations influenced its creation (in-edge derivation DAG), so that I can trace evidence lineage through the memory pipeline.
- US45: As a researcher, I want provenance edges to carry trace-mem HMAC citations (trajectory turn, character span, content hash) so that tampering with source evidence is detectable.
- US46: As a researcher, I want `graph_error` attribution to reference specific provenance edges (which graph-distractor items influenced the answer), so that graph-expansion failures are validated by lineage evidence, not just recovery gain.
- US47: As a researcher, I want provenance completeness measured across the 596-case suite (what fraction of items have full provenance chains), so that the coverage of lineage tracking is quantified for the paper.

### V1 Implementation Decisions

- V1 label expansion follows priority order: `ingestion_error` (✅ done) → `route_error` (✅ done) → `granularity_error` (✅ done) → `graph_error` (✅ done) → `safety_error` (✅ done). Implementation completed in three issues (0011: first two; 0012: remaining three; 0013: recalibration + baseline).
- Bad memory item labels remain excluded from V1 attribution. They are deferred to V2.
- mem0 Adapter uses exactly two interception cut points (`add()` + `search()`). No other mem0 internals are intercepted.
- Entity linking and multi-signal fusion in mem0 are not intercepted—CMD evaluates retrieval outcomes, not retrieval internals.
- Letta Adapter uses tier-aware interception (core write, archival store, recall retrieval) for Oracle Route replay.
- All adapter replays run sandboxed. Store mutation by CMD-Audit during replay is a hard error.
- Coupled-failure threshold with configurable `top_k` (default 2, supports 3+) and full `close_deltas` transparency for 11-label space (issue 0013 ✅ done).
- Memory-probe 3×2 grid comparator (issue 0013 ✅ done): 3 write strategies × 2 retrieval methods (cosine + BM25; dense deferred to V1 per issue 0008) per case, aggregate best-cell accuracy in `comparison_metrics.csv`.
- LoCoMo/LongMemEval real data probe construction is researcher-led. CMD-Audit consumes the resulting probe case files without coupling to the construction process.
- RPE Pre-Filter is implemented in late V1 (Cycle 22). It does not block V1→V2 gate.
- Provenance tracking (Issue 0017) uses Decision 28 architecture: Execution Lineage DAG structure + trace-mem HMAC citation format. Phase 1: in-edge tracking per MemoryItem. Phase 2 (V2): cascade repair via MemQ TD(λ). `graph_error` attribution must reference provenance edges. Content hash tamper detection via `Citation.content_hash`. No cryptographic provenance (MemLineage) in V1 — deferred to V2.
- V0+V1+V2 constitute one paper. V2 is the final module/skill. V1 claims (C7-C10) join the existing V0 claim ledger (C1-C6).

### V1 Testing Decisions

- Each new label (Cycles 16-17) requires one smoke case with known perturbation, following the V0 tracer-bullet pattern.
- 11-label non-regression: run V0 6-label smoke suite through V1 pipeline; all labels must match.
- Adapter-label parity: run V0 6-label smoke suite through both standalone and mem0 adapter paths; all labels must match.
- mem0 store immutability: checksum before and after every replay; any mutation is a test failure.
- V1→V2 gate: automated check that ≥2 agents integrated AND macro F1(agent2) ≥ macro F1(agent1).
- RPE pre-filter: batch evaluation on 50+ probe cases; false skip rate < 5%, cost reduction ≥ 30%.
- Memory-probe comparator: produces valid accuracy scores (not NaN, not trivially zero).
- Provenance tracking (Issue 0017): one smoke case per replay type with provenance edges recorded; provenance completeness ≥ 80% on 596-case suite; content hash mismatch → tamper detection flag; `graph_error` smoke case with provenance edges referencing distractor items; backward compatibility — existing MemoryItem without provenance works as before (provenance=None).

### V1 Out of Scope

- Bad memory item labels (`item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`) — deferred to V2.
- Learned CMD classifier — still deferred; rule-based deltas remain the attribution method.
- Agentic search retrieval — still deferred; introduces LLM-call non-determinism requiring independent taxonomy review.
- Full production deployment of CMD-Skill Adapter as a runtime service.
- UI or dashboard for attribution results.
- Internal circuit/activation analysis.
- CMD-Audit writing to production agent persistent memory—this remains prohibited; only CMD-Skill Adapter applies validated repairs.
