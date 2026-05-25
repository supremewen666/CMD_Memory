# CMD Open Decisions

Date: 2026-05-20

Status: resolved for V0.

## Decisions 1-9: V0 Foundation (2026-05-09, resolved)

1. **V0 label scope**: 6 pipeline labels (`write_error`, `compression_error`, `premature_extraction_error`, `retrieval_error`, `injection_error`, `reasoning_error`); deferred labels (`granularity_error`, `route_error`, `graph_error`, `safety_error`) excluded for statistical clarity.
2. **`premature_extraction_error`**: Promoted to first-class pipeline label — captures evidence loss between raw events and extracted memory, diagnosed by Verbatim Event Oracle.
3. **Subagent Judge role**: Baseline over failed trace (post-hoc explanation) + cheap high-recall monitor for triggering replay. Not final attribution source.
4. **First implementation target**: Standalone CMD-Audit research harness (synthetic perturbations, baseline memory, replay engine, baselines, attribution metrics, ECS schema).
5. **Post-Repair Context Replay**: Required V0 gate. Flow: AttributionAssigned → ECSDrafted → RepairedContextBuilt → PostRepairRetested → RepairValidated/RepairFailed → FutureCaseGuided. Three-value output (`recovered`/`partial`/`failed`). Constraint: no gold answer injection, no full failed trace to future context.
6. **Naming boundary**: `CMD-Audit` (research harness) vs `CMD-Skill Adapter` (deployment layer).
7. **Subagent Judge Monitor leak-safety**: May trigger replay but must not emit final labels, ECS, gold answers, full failed traces, or memory writes. `anomaly_reason` enum-locked.
8. **Bad memory item labels excluded from V0**: `item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted` deferred to V2.
9. **Retrieval baseline strengthening**: V0.5 issue adding BM25 retriever, ranked traces, retrieval metrics, hard negatives. HybridRerank removed post-implementation — sparse+sparse hybrid cannot deliver semantic recovery; strong retrieval deferred to V1. Boundary: `evidence_recall_from_text` hard divider — no retriever improvement changes `premature_extraction_error`.

**Architecture**: V0 = standalone CMD-Audit harness → V1 = pluggable audit module → V2 = CMD-Skill Adapter for runtime repair.

## Decision 10: Competitive Landscape Validation (2026-05-10)

27 papers + 10 repos survey confirms no existing work does automated counterfactual memory replay for operation-level attribution. Closest: Trajectory-Informed Memory (observational), Peaky Peek (HITL checkpoint replay), D-MEM (binary detection). Failure Memory pattern independently validated across A-MemGuard, SQLFixAgent, Reflection-Driven Control.

## Decision 11: RPE Pre-Filter (2026-05-10)

RPE pre-filter is a V0.5/V1 optimization path, not a V0 requirement. V0 monitor triggers replay unconditionally. After V0 artifacts exist, evaluate whether RPE (scoring evidence-surprise gap, D-MEM's 80% token reduction target) can reduce cost without lowering attribution recall.

## Decision 12: `evidence_recall_from_text` Phrase-Matching (2026-05-10)

Plan A: accept known limitation, do not upgrade scorer in V0.5. Mitigated by multi-phrase granularity (e.g., `["Messi", "GOAT"]`). Semantic entailment upgrade belongs to V1 with real LLM integration. Also decided: BM25 only; HybridRerank removed — both are sparse bag-of-words models and cannot deliver true semantic recovery; strong (dense/hybrid) retrieval deferred to V1 with mem0/Letta adapters.

## Decision 13: V1 Label Expansion Order (2026-05-11)

Priority: `ingestion_error` → `route_error` → `granularity_error` → `graph_error` → `safety_error`. Ordered by implementation cost (low→high) and external validation strength. Bad item labels deferred to V2.

## Decision 14: First Adapter Target — mem0 (2026-05-11)

mem0ai/mem0 (55k stars): simplest memory API (`add()` → `search()`), SOTA benchmarks. Six V0 replays map cleanly to mem0 operations. Second target: Letta (22.6k stars) for V1→V2 gate (exercises `route_error` via core/archival/recall tiering). memory-probe (2603.02473) cited as closest diagnostic work (observational 3×3 grid).

## Decision 15: Single Paper Scope (2026-05-11)

V0 + V1 + V2 = one paper. Arc: controlled attribution (V0) → cross-system generalization (V1) → runtime repair loop (V2). V0→V1 and V1→V2 gates are internal checkpoints.

## Decision 16: RPE Deferred to Late V1 (2026-05-11)

RPE optimization follows after V1's core claims (label expansion + adapter integration) are evidenced. Requires trained surprise model from V1 replay traces.

## Decision 17: Context Construction Mode (2026-05-11)

V0/V1 Failure Memory injects only `corrected_memory + repair_guidance` (corrected-only mode). Contrastive mode (`wrong_memory + cause + corrected_memory + repair_guidance`) is a V2 deliverable.

Three modes evaluated: corrected-only (low risk), contrastive (medium risk — `wrong_memory` contradicts `corrected_memory` for `retrieval_error`/`injection_error`), full trace (anti-pattern — pollution_risk=1.0). V0 smoke data: corrected-only achieves evidence=1.0, answer=1.0; full trace scores 0.0.

V1 evaluates attribution accuracy; V2 evaluates context construction effectiveness. Synthetic string matching cannot measure contrastive learning signals. 4-Mode Context Experiment can run at any time on existing artifacts + real LLM (no CMD code changes) to pre-validate contrastive mode. No existing literature (40+ papers) provides this controlled comparison.

## Decision 18: Dataset Build Order (2026-05-12)

Experiment 2 (Probe Cases) first → CMD pipeline → ECS records → Experiment 1 (4-Mode Context Cases). Context Cases depend on ECS output fields (`wrong_memory`, `cause`, `corrected_memory`, `repair_guidance`). Build path: 10 Probe Cases → CMD pipeline → review ECS → 10 Context Cases → LLM eval → scale Probe Cases to 50-100 → scale Context Cases to 15-40.

Dataset references: MEMAUDIT (package-oracle protocol), Memory-Probe (3×3 grid), MemEvoBench (risk-type taxonomy), ErrorProbe (step-level injection), MedEinst (counterfactual diagnosis hierarchy). Constraints: `perturbation_type` is injected ground truth; Context Case `none` mode must fail.

## Decision 19: Quality Gate Positioning (2026-05-13)

Four independent sources converge on "never make the same mistake twice" loop: CMD, skill-everything, ErrorProbe, SQLFixAgent. Key differentiator: the quality gate mechanism.

CMD's Post-Repair Context Replay is the only fully automated semantic quality gate: re-runs original failing query with repaired context, outputs `recovered`/`partial`/`failed`. Comparators: skill-everything (human PR review — reliable, not scalable), ErrorProbe (executable evidence — automated but checks pattern confirmation, not fix-specific correctness), SQLFixAgent (SQL execution success — domain-specific).

Paper positioning: CMD claims (1) automated counterfactual attribution, (2) Post-Repair Context Replay as automated semantic quality gate. The 4-source convergence validates direction; quality gate differentiation establishes specific contribution.

## Decision 20: Episodic Trace Anchoring (2026-05-14)

2605.12978 (consolidation degrades evidence, episodic traces as first-class), 2605.10870 (memory must preserve decision-relevant distinctions), and MEMOREPAIR (retained support from pre-event valid state) independently converge: raw episodic traces are the ground truth against which memory operations should be validated. CMD's Verbatim Event Oracle already implements this for `premature_extraction_error` attribution. Episodic anchoring should extend to all 6 replay types: each counterfactual replay's recovery gain is measured against the episodic trace baseline, not just the extracted-memory baseline.

## Decision 21: Consolidation Fragility as Core Evidence (2026-05-14)

2605.12978 provides controlled causal evidence that LLM consolidation systematically degrades memory: GPT-5.4 fails on 54% of problems it previously solved without memory after consolidating from ground-truth solutions. Three failure modes (misclassification, interference, overfit) map to CMD labels (premature_extraction, compression_error, reasoning_error). This paper should be cited as primary external validation of CMD's counterfactual approach — the exact failure CMD diagnoses.

## Decision 22: Cascade Repair Integration Path (2026-05-14)

MEMOREPAIR (2605.07242) formalizes cascade repair with barrier-first contract + s-t min-cut optimization. CMD attribution + MEMOREPAIR cascade handling is a natural V2 integration: CMD identifies invalidated root set (F) via counterfactual replay → MEMOREPAIR computes affected cascade and selects optimal repairs → CMD's Post-Repair Context Replay validates republished successors.

**Provenance tracking requirement (V1)**: CMD currently lacks influence provenance (which memory items influenced which decisions/other items). 5 provenance papers in Day 5 window (MemLineage 2605.14421, TRACER 2605.09934, PACT 2605.11039, Execution Lineage 2605.06365, MemQ 2605.08374) confirm provenance is becoming fundamental infrastructure. V1 must implement basic provenance tracking: for each memory item, record which retrieved items influenced its creation (derivation DAG). This is prerequisite for `graph_error` label (needs to track graph edge influence) and cascade repair integration. Full cryptographic provenance (MemLineage pattern) remains V2.

## Decision 23: Detection→Diagnosis→Repair Positioning (2026-05-14)

5 detection-only systems (Nautilus Compass, CAMEL, Cognifold, PrefixGuard, scale-conditioned eval) + 1 repair-only system (MEMOREPAIR) found in Day 4 window. None span detection+attribution+repair. CMD claims the attribution layer (detection→attribution via counterfactual replay) with integration paths to detection (PrefixGuard/Nautilus Compass as monitor frontend) and repair (MEMOREPAIR as cascade handler). This gap is now sufficiently evidenced to feature in the paper's related work section.

## Decision 24: Recovery Gain vs Shapley-Value Differentiation (2026-05-15)

2605.13077 (Counterfactual Reasoning for Causal Responsibility Attribution in Probabilistic Multi-Agent Systems) is the closest formal work to CMD's counterfactual approach. It uses Shapley value to allocate responsibility among agents in concurrent stochastic multi-player games, with formal properties (fairness, consistency) and verification support via nested fixed-point logic.

Key architectural differences:

| Dimension | CMD Recovery Gain | Shapley Value (2605.13077) |
|-----------|------------------|---------------------------|
| Scope | Within-agent pipeline operations | Across-agent responsibility |
| Cost | Linear in pipeline length (|ops|) | Exponential in agent count (2^\|agents\|) |
| Intervention | Single-operation replay | All-subsets coalitional |
| Output | Operation label (argmax Δk) | Responsibility vector (Shapley values) |
| Granularity | Memory pipeline operation | Agent identity |
| Guarantees | Empirical (Recovery Gain) | Axiomatic (fairness, consistency) |

**Positioning**: Not competitors — complementary layers. CMD answers "which pipeline operation failed?" for a single agent. Shapley answers "which agent is responsible?" for a multi-agent system. V2 integration path: Shapley for inter-agent responsibility allocation → CMD Recovery Gain for intra-agent pipeline attribution. Combined: Shapley identifies responsible agent → CMD diagnoses which memory operation within that agent failed.

**Paper impact**: 2605.13077 must be cited in related work as the closest formal counterfactual attribution work. The differentiation argument is: Shapley operates at agent granularity with coalitional cost; CMD operates at operation granularity with linear cost. Both use counterfactual reasoning; they differ in target and tractability.

## Decision 25: Attribution Subfield Crowding and CMD Positioning (2026-05-15)

The week of 2026-05-10 to 2026-05-15 saw 5+ distinct failure attribution methods published, establishing attribution as a recognized subfield of agent memory/agent systems research:

| Method | Evidence | Granularity | Formal Guarantees |
|--------|----------|-------------|-------------------|
| CMD (ours) | Counterfactual replay (intervention) | Operation-level (6-8 labels) | Empirical (Recovery Gain) |
| 2605.13077 Shapley | Counterfactual (coalitional) | Agent-level | Axiomatic (Shapley) |
| 2605.14865 HolisticEval | Observational (span assessment) | Span-level | None (benchmark SOTA) |
| 2605.06788 ConformalAttr | Observational (trajectory) | Step-level (contiguous) | Finite-sample coverage |
| 2605.07509 MASPrism | Prefill signals (NLL+attention) | Token/step-level | None (heuristic) |

**CMD's remaining differentiators after this crowding:**

1. **Operation-level granularity**: Only CMD attributes to specific memory pipeline operations (write/compress/extract/retrieve/inject/route/reason). All others target agent-level, span-level, or step-level.
2. **Counterfactual intervention evidence**: CMD uses intervention-based Recovery Gain (causal). HolisticEval, ConformalAttr, MASPrism use observational evidence (correlational). Only Shapley (2605.13077) shares counterfactual evidence but at agent granularity.
3. **Automated quality gate**: Post-Repair Context Replay validates fixes before deployment. None of the new entrants include repair validation.
4. **Full loop**: Detection → Attribution → ECS → Repair → Failure Memory. All new entrants stop at attribution.

**Risk assessment**: The attribution space is crowding fastest of any agent memory subtopic. CMD's window for claiming counterfactual operation-level attribution as novel is narrowing — likely 2-3 months before a direct operation-level competitor emerges. The paper must ship with explicit differentiation from all five methods in the related work section.

**Action**: Add all five methods to paper related work. Emphasize (1) operation-level granularity as the key differentiator, (2) intervention-based vs observational evidence, (3) repair validation as unique to CMD.

## Decision 26: Counterfactual Convergence and Multi-Resolution Positioning (2026-05-19)

Day 7 metabolism found two additional counterfactual attribution systems (TraceAudit chunk-level, VerifyMAS agent-level), bringing the total to 3 independent counterfactual systems within a 2-week window (with Shapley-value 2605.13077 as a fourth, formal variant). This convergence validates counterfactual as the right methodological foundation but increases competitive pressure.

**Granularity spectrum**: The attribution subfield is self-organizing into a natural hierarchy:

| Level | System | Intervention | Output |
|-------|--------|-------------|--------|
| Chunk | TraceAudit | Remove/paraphrase/distract RAG chunks | URR (Usefulness via Removal Ratio) |
| Agent | VerifyMAS | Hypothesis verification against full trajectories | Agent-level error attribution |
| Agent | Shapley (2605.13077) | All-subsets coalitional marginalization | Shapley responsibility vector |
| Operation | **CMD** | Single-operation counterfactual replay | Recovery Gain → operation label |

No existing system spans multiple levels. CMD occupies the operation-level niche exclusively.

**Key differentiators for paper**:
1. **Granularity**: CMD is the only operation-level attribution system. All others target agent/span/chunk.
2. **Evidence type**: CMD uses causal intervention (replay with oracle operation) — stronger than observational (VerifyMAS, HolisticEval, MASPrism). Only Shapley shares causal evidence but at agent granularity.
3. **Purpose**: CMD diagnoses-to-repair (ECS + targeted fix + Failure Memory). TraceAudit audits-to-report. VerifyMAS attributes-to-identify.
4. **Full loop**: Detection → Attribution → ECS → Repair → Post-Repair Validation → Failure Memory. All competitors stop at attribution.

**Risk**: The counterfactual convergence is a double-edged sword. It validates CMD's methodological choice but signals that the space is crowding rapidly. The paper must:
- Cite TraceAudit, VerifyMAS, and Shapley as related counterfactual work
- Explicitly differentiate along all four dimensions above
- Ship before a direct operation-level counterfactual competitor emerges (estimated 2-3 month window)

**TraceAudit-Specific Positioning**:
TraceAudit is the closest counterfactual system in spirit (both use intervention-based evidence), but differs fundamentally:
- TraceAudit removes RAG chunks and re-generates the final answer (black-box audit)
- CMD replays the memory pipeline with oracle operations and measures Recovery Gain (white-box diagnosis)
- TraceAudit answers "which chunk was useful?" CMD answers "which memory operation failed?"
- TraceAudit targets external RAG systems for auditing; CMD targets its own memory pipeline for self-repair

The two are complementary: TraceAudit could audit a RAG-based agent's retrieval quality; CMD could then diagnose which memory operation caused the poor retrieval.

## Decision 27: Prefilter Architecture — RPE + PrefixGuard Two-Tier (2026-05-19)

CMD's main cost is replay: each failure triggers 6-10 oracle replays. A prefilter before replay reduces cost without losing attribution accuracy. After reviewing 5 candidate approaches against CMD's specific needs (retrospective failure diagnosis, operation-level scoring), two complementary schemes were selected for a two-tier architecture:

**Tier 1 — PrefixGuard (2605.06455): Anomaly Detection**

Replaces/upgrades Subagent Judge Monitor as the CMD trigger.
- Rule-based trace→monitor, zero LLM cost
- Validated: outperforms LLM judge on failure detection
- Prevents CMD pipeline from being triggered on non-failure cases (reduces false-positive CMD runs)
- Maps to hyp-013 (PrefixGuard-CMD two-tier architecture)

**Tier 2 — RPE (2603.14597, D-MEM): Replay Selection**

Operates inside CMD pipeline, after anomaly is detected but before full replay.
- Evidence-surprise gap scoring per memory operation
- Only replays with surprise > threshold are executed
- D-MEM reports 80% token reduction target
- With 596 cases available, a lightweight scorer (BM25/embedding similarity between retrieved evidence and gold evidence) can be trained without LLM cost
- CMD's position: operation-level replay → RPE can score each of the 6-10 replays independently, running only top-k
- Decision 11 already reserved this path: "RPE pre-filter is a V0.5/V1 optimization path"

**Why not the alternatives:**

| Candidate | Rejection Reason |
|-----------|-----------------|
| trace-mem counterfactual gate | Operates at write time (preventive), CMD needs retrospective filtering at failure time |
| Nautilus Compass (2605.09863) | Persona drift detection, not memory-operation-level |
| Full LLM judge | Too expensive for a prefilter that should reduce cost |

**Design**:
```
PrefixGuard (rule-based, zero LLM)
  → anomaly detected? → trigger CMD pipeline
    → RPE scores each of N replays (evidence-surprise gap)
      → run only top-k replays (k default: ceil(N/3), min 2)
        → Recovery Gain attribution on k replays
```

**RPE scorer implementation** (minimum viable):
- Gold evidence from ProbeCase (known ground truth at training time)
- Retrieved evidence from each replay's memory state
- Surprise = 1 - cosine_sim(embed(gold_evidence), embed(retrieved_evidence))
- Train threshold τ on 80% of cases, validate on 20%
- Goal: skip replays with surprise < τ, keeping attribution recall ≥ 95%

**Decision 16 update**: RPE was deferred to "late V1" — with 596 cases available, it moves to **early V1**, before adapter integration. Trained scorer generalizes across adapters (scorer is adapter-agnostic, scores evidence quality not system specifics).

## Decision 28: Provenance Architecture — Execution Lineage DAG + trace-mem Citation (2026-05-19)

After reviewing 8 provenance/provenance-adjacent works (MemLineage, TRACER, PACT, Execution Lineage, MemQ, trace-mem, MemORAI, Portable Agent Memory), the selected approach combines Execution Lineage's DAG structure with trace-mem's citation format.

**Core data model** (minimum viable provenance):

```python
@dataclass
class ProvenanceEdge:
    source_id: str       # 上游 item id
    target_id: str       # 本 item id (MemoryItem.provenance 的 owner)
    operation: str       # write | compress | extract | inject | retrieve
    citation: Citation   # trace-mem 格式
    timestamp: float

@dataclass  
class Citation:
    trajectory_turn: int  # 来源 turn 编号
    char_span: tuple      # (start, end) in source text
    content_hash: str     # HMAC(content, session_key)
```

`MemoryItem` gets one new optional field: `provenance: List[ProvenanceEdge]` (in-edges).

**Why Execution Lineage DAG as the structure:**
- Records `derived_from` relations — which items influenced this item's creation
- DAG supports both directions: in-edges (who influenced me) and out-edges (who did I influence)
- Emphasizes replay reproducibility — aligns with CMD's deterministic counterfactual replay requirement
- Lighter than MemLineage's full cryptographic scheme

**Why trace-mem citation as the edge annotation:**
- HMAC-signed citation to originating trajectory turn + character span
- Enables tamper detection (content hash mismatch → drop)
- Compatible with trace-mem's verifiable memory standard (论文可 claim compatibility)
- Citation is per-edge, not per-item — same item can have multiple source citations

**Why not the alternatives:**

| Candidate | Rejection Reason |
|-----------|-----------------|
| MemLineage (2605.14421) | Full cryptographic provenance is V2; too heavy for V1 minimum viable |
| MemQ (2605.08374) alone | MemQ is the *algorithm* for credit propagation on top of a DAG — needs DAG structure first. MemQ's TD(λ) will be used in V2 on top of our Execution Lineage DAG |
| trace-mem alone | Only in-edges (origin tracking), no out-edges. Cascade repair requires out-edges to trace downstream impact |
| PACT (2605.11039) | Argument-level provenance is finer granularity than CMD needs; overkill for memory operation tracking |

**Phase 1 (V1, now): DAG structure + in-edge tracking**
- Add `ProvenanceEdge` to `MemoryItem`
- Each replay records which items influenced the new memory state
- Basic tamper detection via content hash
- 596 cases used to validate provenance completeness (what fraction of items have full provenance chains)

**Phase 2 (V2): Cascade repair via MemQ TD(λ)**
- MemQ's credit propagation on the DAG: from root failure F, compute which downstream items were affected
- Integration point with MEMOREPAIR: CMD identifies F → MemQ computes cascade → MEMOREPAIR selects repairs → CMD Post-Repair validates

**Relationship to trace-mem integration** (Question 5, Decision pending):
- If trace-mem gate is integrated as optional prefilter, the Citation format is shared infrastructure
- trace-mem gate needs Citation for counterfactual validation; CMD provenance needs Citation for derivation tracking
- Shared format enables interoperation without tight coupling

## Decision 29: Gold Evidence Dependency — Formal Limitations Positioning (2026-05-19)

CMD requires `gold_evidence` with `required_phrases` for 4/11 pipeline labels. These 4 labels diagnose memory *content absence* errors—errors where information never entered the system or was lost. By definition, detecting missing content requires knowing what content should exist. This is an information-theoretic bound, not a methodological weakness.

**Labels affected (4/11)**:
| Label | Replay | Gold Evidence Needed For |
|-------|--------|------------------------|
| `write_error` | Oracle Write | Inject evidence that was never stored |
| `compression_error` | Oracle Compression | Inject evidence lost during compression |
| `premature_extraction_error` | Verbatim Event Oracle | Inject raw events containing evidence lost at extraction |
| `injection_error` | Injection Oracle | Inject evidence into specific retrieval slots |

**Labels unaffected (7/11)**: `retrieval_error`, `reasoning_error`, `ingestion_error`, `route_error`, `granularity_error`, `graph_error`, `safety_error`. These operate on existing memory items—they rearrange, rerank, expand, or re-route without fabricating new content.

**Two-tier deployment defense**:
- **CMD-Audit** (offline, with gold evidence): 11/11 labels. Proves the counterfactual replay methodology works. Research contribution.
- **CMD-Skill Adapter** (online, no gold evidence): 7/11 labels guaranteed + self-supervision augmentation to ~9/11. Deployment contribution. Still better than status quo (zero automated operation-level attribution).

**Self-supervision mitigation**: Success-trace memory items provide surrogate evidence candidates. Counterfactual replay validates causal recovery. Candidates that don't produce recovery gain are discarded. This transforms "where is the gold evidence?" (unsolvable) into "which success-trace items causally improve the answer?" (tractable with replay).

**Paper positioning** (Decision 19 update): CMD's claims must NOT imply that gold evidence is available online. Paper claims: (1) counterfactual replay produces valid operation-level attribution when gold evidence exists, (2) the subset of labels that don't require gold evidence remains functional online, (3) self-supervision reduces but does not eliminate the gap. The comparison is CMD (online, reduced label set) vs. status quo (zero automated attribution), not CMD vs. CMD-with-gold-evidence.

**Reviewer preemption**: The gold evidence gap is documented as a limitation, not hidden. The paper acknowledges it as an information-theoretic bound on content-absence diagnosis. No existing system diagnoses content-absence errors without some form of gold content specification either.

**Full limitations document**: `cmd_innovation_core/plans/limitations.md` (8 limitations across methodological and implementation categories).

## Decision 30: Accelerated Timeline + Deep Memory Diagnosis Beyond Rewind (2026-05-20, RESOLVED)

Day 9 metabolism found counterfactual replay transitioning from differentiator to table stakes. Decision: **accelerate timeline, maintain memory-diagnostic-layer positioning, go deeper than Rewind**.

**Resolution**:

1. **Timeline**: Accelerate. Paper deadline moves earlier than 2026-06-15. Counterfactual replay is commoditizing from both tooling and academic directions.

2. **Positioning**: CMD remains the memory-module diagnostic layer. Not competing with generic replay tools — they're complementary infrastructure. CMD sits on top of any replay backend and adds memory-pipeline-specific intelligence.

3. **Depth differentiation vs Rewind**: Rewind diagnoses at agent-step level (model swap, prompt change, retry). CMD must demonstrate qualitatively deeper diagnosis and repair:

   | Dimension | Rewind (agent-step) | CMD (memory-operation) |
   |-----------|---------------------|------------------------|
   | Granularity | "Step 5 used stale data" | "`compression_error`: evidence lost during session 3→4 summarization" |
   | Diagnosis | LLM guesses failure step | Counterfactual replay produces Recovery Gain ranking over 11 ops |
   | Repair | Retry step with different model/prompt | Rewrite specific memory item to restore lost evidence |
   | Validation | LLM-as-judge score comparison | Post-Repair Context Replay: rerun original query, measure evidence recall |
   | Learning | One-shot fix, no retention | ECS → Failure Memory → recurrence prevention |

4. **Paper must include head-to-head**: CMD operation-level repair vs Rewind-style step-level retry on the same failure cases. Show that memory-specific repair produces higher recovery rates and lower recurrence than generic step-level debugging.

5. **Integration stance**: Standalone harness. CMD-Audit does not depend on Culpa/Rewind. But the paper should acknowledge them as complementary infrastructure and cite the convergence as validation.

**Action items**:
- Add "CMD vs generic agent debugger" comparison to paper (Rewind as representative)
- V2 runtime repair must be memory-item-level, not step-level retry
- Consider adding a "repair depth" metric: does the fix address the root memory operation, or just patch the symptom?

**Related**: Decision 26 (academic convergence), hyp-017 (multi-resolution spectrum), hyp-018 (counterfactual replay as standard primitive).

**2026-05-23 addendum (per Decision 34 R6)**: Head-to-head benchmark dropped. Rewind and CMD operate on different layers (runtime vs memory pipeline) and don't share an input modality; running both on the same cases produces meaningless metrics. Replaced with layered-stack positioning + qualitative boundary examples in related work (Runtime: Rewind/Culpa/TraceForge ↔ Memory pipeline: CMD ↔ Item content: MemRepair/MemLineage). Decision 30's depth differentiation claim is preserved as a related-work section, not as quantitative evidence. The 5-dimension table is reframed from "Rewind vs CMD" to "Runtime debugger vs Memory debugger." Paper requirement "Head-to-head: CMD operation-level repair vs Rewind-style step-level retry on the same failure cases" is removed. See Decision 34 for the full grilling resolution.

## Decision 31: Pre-CMD Hook — Single post_retrieve_hook Architecture (2026-05-21, RESOLVED)

Single `post_retrieve_hook` function gating CMD counterfactual replay. Online: zero gold dependency, purely deterministic/statistical rules. Offline: 596 cases calibrate weights and thresholds, extract constants for online deployment.

**Resolution**:

1. **Single hook point**: `post_retrieve`, not multi-event ECC. Other hook points add cost without proportionate gain for online.

2. **PrefixGuard (4 signals)**: `empty_ctx` (binary), `truncation` ([0,1] fraction), `near_duplicate` (max pairwise BM25), `low_count` (binary). Weights learned offline via logistic regression. `anomaly_score = w1*empty_ctx + w2*truncation + w3*near_duplicate + w4*low_count`.

3. **RPE (BM25)**: `surprise = 1.0 - max(BM25(query, item.text))`, `utility = 1.0 - agent_confidence` (default 0.5). `rpe = surprise * utility`. Four gating branches (priority): utility override → rpe threshold → anomaly threshold → skip.

4. **Trigger → all replays**: No online ranking. Cost saving from skipping clean cases, not from reducing replays per case. `selected_replays` from adapter capability declaration (7 or 10).

5. **Offline calibration**: 6 steps: label gen → signal extraction → logistic regression → grid search (F2, recall-priority) → joint optimization → hold-out validation. Output: constants only.

6. **Module**: `cmd_audit/post_retrieve_hook.py` — `PreCmdDecision` dataclass + `post_retrieve_hook` function.

7. **Integration**: `run_case_v1_with_hook` entry point. Failure Memory retrieved at hook time; `fm_context` injected downstream.

**Design doc**: `cmd_innovation_core/issues/0018-pre-cmd-hook-design.md`.

## Decision 32: Post-Gate Pipeline — Repair Layering, Iterative Repair, Self-Supervision, Failure Memory Lifecycle (2026-05-22, RESOLVED)

Detailed design session ("discussion 1") resolving 15 questions about the CMD pipeline after the filter gate (Pre-CMD Hook → CMD Diagnosis Layer).

**Resolution**:

1. **Repair layering** (4-tier):
   - **Adapter**: pure read/write, 声明 `supported_actions: tuple[str, ...]`（mem0=`(append, replace)`，Letta=`(append, replace, relocate, update_routing)`），`apply_repair(RepairAction)` 执行
   - **RepairExecutor**: stateless 单次修复。`ECS → LLM 选 action（从 adapter.supported_actions）→ apply_repair → 构建 repaired_context（baseline + label + evidence_block + fm_context）→ Post-Repair Context Replay → recovered/partial/failed`
   - **RepairOrchestrator**: `partial` 时取 `close_deltas` 下一 label → 新 ECS → RepairExecutor → 循环至 `recovered` 或 exhaust
   - **Harness/Online Pipeline**: 触发 orchestrator；CMD 归因失败（7/11 在线未命中或 self-supervision 失败）→ `AttributionFailed`，不进入修复

2. **归因 Subagent Loop Context**: 每个 replay 给 `agent.generate()` 的 context = **baseline + label + evidence_block**（不含 fm_context）。`fm_context` 在 ECS 阶段注入（Decision 32 point 13，保证因果纯度）。

   - **baseline**: `case.primary_baseline.retrieved_items` 原文，agent 原始上下文
   - **label**: 当前 replay 的诊断标签（如 `retrieval_error`），agent 理解修复类型
   - **evidence_block**: replay 生成的反事实证据块。所有 10 个 replay（含 retrieval/route）统一机制：用 gold_evidence 做指针从 `case.extracted_memory` 捞已有文本，非模拟检索/路由行为。subagent 合约统一：`verify(evidence, agent_answer) → PRESENT/ABSENT`，不需工具。

   **`_score_recovered_evidence` 改动（Decision B）**:
   - 新增参数 `agent_generate: (query, context) -> str`，构建 `context = baseline + label + evidence_block`，替代 line 339 shortcut（`answer = case.gold_answer if evidence_score == 1.0 else ""`）。
   - `recovery_gain` 切换为 `evidence_score - baseline.evidence_score`，`evidence_score = scorer(gold_evidence, agent_answer)`（subagent 连续 [0,1]）。`answer_score` 保留仅用于离线辅助评估，不驱动 recovery_gain——线上无 gold_answer。

3. **RepairAction** (5 types): `append`, `replace`, `relocate`, `update_routing`, `update_template`。作为 LLM tool 定义传入——LLM 看到 label + evidence_block + fm_context + adapter.supported_actions，自主选择 action 类型并填参。不硬编码 label→action 映射。`target_item_id` (optional), `target_store`, `content`, `label`。

4. **Iterative repair**: `partial` Post-Repair → iterate next `close_deltas` label → new ECS → repair again. Stops at `recovered` or exhausts candidates. `close_deltas` = 所有 `recovery_gain > threshold` 的 label（非固定 top-k），阈值离线在 596 case 上校准 gain 分布下界。

5. **Online ECS corrected_memory**: For 7/11 labels, `replay.evidence_block` directly usable (replay changes pipeline behavior, not injects synthetic content). For 4 gold-dependent labels, self-supervision surrogate path.

6. **Self-supervision surrogate**（Issue E → 0021 Step 2）：4 gold-dependent labels 在线走 BM25 success-trace → replay → gain。Offline 0021 Step 2 用 SubagentScorer 对比 surrogate vs gold recovery gain，产出 paper gap 数据。**不训练，只测量。** 在线部署直接复用 BM25 + replay 因果验证路径，不依赖 gold_evidence。

7. **Failure Memory injection timing**: At Pre-CMD Hook time (方案 A). FM context injected before CMD diagnosis — repair prioritized over diagnosis. `fm_context` (meta-diagnostic) ≠ `wrong_memory` (banned).

   **fm_context 组成**: `wrong_memory + original_evidence`（错误块 + 证据），不是 `corrected_memory` 的重复。`fm_context` 提供诊断参考（"这种错误模式以前见过，为什么错了"），`corrected_memory` 提供修复参考（"上次是这样修的"）。两者互补：
   - `fm_context` = 诊断信号：过去的错误记忆内容 + 证据（为什么是错的）
   - `corrected_memory` = 修复信号：修正后的内容（应该是什么样）

   完整 repair context = **baseline + label + evidence_block（corrected） + fm_context（wrong_memory + original_evidence）**。

8. **Failure Memory retrieval key**: `label + query_keywords + memory_top_terms` composite。当前为 `label|query_keywords` 纯 keyword overlap，升级后三维联合 BM25 检索：相同错误类型 + 相似 query 主题 + 相似记忆内容。`memory_top_terms` = BM25 从 current retrieved_items 提取 top N 词。检索到的 `fm_context` 更精准。存储时 `trigger_signature` 同步升级为三维 key。

9. **Failure Memory storage**: Only `recovered` ECS records stored. Per-agent persistence: `FAILURE_MEMORY.md` alongside agent's MEMORY.md, or merged with `failure` label.

10. **Issue C: `run_case_v1_with_hook` 集成**: CMD-Skill Adapter 外层持有 adapter + agent，调用时注入 `run_case_v1_with_hook(query, retrieved_items, adapter, agent_generate, scorer) → AuditResult`。CMD-Audit 不持有 adapter/agent 引用，与现有 `run_case_with_mem0`/`run_case_with_letta` 模式一致。

11. **Issue F: PreCmdDecision → AuditResult**: Hook 全量信号写入 AuditResult：`hook_stage`（empty_ctx | rpe_top_k | rpe_below_threshold）、`per_replay_scores`（10 个 p-score，可追溯）、`selected_replays`。`fallback_triggered` 在 0021 PR2 移除，改由 `hook_stage == "rpe_below_threshold"` 推导。用于 paper hook 有效性分析和线上调试。

12. **Attribution = None handling** (3-tier): (a) RPE judge top-k replays all zero → self-supervision path, (b) surrogate also zero → `AttributionFailed`, records hook feedback, no FM storage, (c) surrogate has gain → normal ECS flow.

13. **Online Post-Repair validation**: Simplified — offline high correctness justifies trusting attribution. Online executes repairs without blocking on validation. (Open question: specific validation signal deferred.)

14. **Hook false negative**: Trust F2 offline calibration. No post-answer fallback needed.

15. **Hook → pipeline handoff**: `selected_replays` from RPE judge top-k（非 adapter capability）；FM context merged downstream at ECS stage (not before replays — preserves causal purity)；all PreCmdDecision signals written to `AuditResult`.

16. **V2 cascade pre-burial**（Issue H）: `ECSDraft.cascade_candidates` field — downstream item IDs from provenance DAG. V1 always empty. V2: LLM retrieves candidates and self-modifies (not algorithmic MemQ TD(lambda)). 纯预埋，无逻辑。

17. **Online pipeline data flow**: Defined end-to-end from query arrival → hook（0021 两阶段）→ RPE judge top-k replays → 归因 subagent loop → ECS + fm_context → RepairOrchestrator → executor → FM storage → result.

**Related**: Decision 17 (context construction mode), Decision 20 (episodic trace anchoring), Decision 28 (provenance DAG), Decision 29 (gold evidence limitation + self-supervision).

## Decision 33: Hook Redesign — Two-Stage Sequential Gating + RPE Judge Per-Replay top-k (2026-05-22, RESOLVED; updated 2026-05-23 grilling session)

Redesign Pre-CMD Hook from Decision 31's five-branch parallel OR to two-stage sequential architecture with RPE Judge as per-replay scoring model. PrefixGuard independent stage removed (2026-05-23): truncation 11 pattern has zero online recall; near_duplicate/low_count merged into RPE Judge features. Grilling session 2026-05-23 finalized 9 build-detail decisions.

**Resolution**:

1. **Two-stage architecture**: empty_ctx hard short-circuit (len(items)==0 → trigger all replays) → RPE Judge (per-replay p-score → fixed **top-k** selection) → fallback (max(p) < FALLBACK_THRESHOLD → skip CMD). `stage` field three values: `"empty_ctx" | "rpe_top_k" | "rpe_below_threshold"`. No `fallback_triggered` field — derived from `stage == "rpe_below_threshold"`.

2. **RPE Judge design**: Shared logistic regression model + replay_type one-hot (**16 features**: 6 global BM25/structure/duplicate stats + 10 replay_type indicators). `safety_filter_blocked`/`is_graph_expanded`/`store_count` removed (avoids train/serve skew; signal already in replay_type one-hot). `item_count` cap+normalized to `min(x,10)/10`. Trained on 596 cases × 10 replays = 5960 (features, label) pairs. **Label = recovery_gain > 0 via SubagentScorer** (qwen2.5-7b ollama, offline LLM use; deployment hook zero LLM). Inference: sigmoid(dot(weights, features) + intercept) → p per replay, sort descending with deterministic `V1_REPLAY_NAME_ORDER` index tiebreak, select top-k.

3. **Lightweight calibration (3 steps)**: Step 1: RPE judge weight training (546 train + 50 hold-out split, **subagent labels** ~5-15 min ollama qwen2.5-7b, cache + phrase-match fallback on subagent failure). Step 2: Surrogate path quality measurement (50 hold-out, requires LLM for gap data). Step 3: **Global threshold tuning** across all adapters (50 hold-out, joint grid search `TOP_K ∈ {2,3,4,5} × FALLBACK_THRESHOLD ∈ [0,1] step 0.05` = 84 points, F2 recall-priority). Per-agent calibration **deferred to V2**.

4. **Hook packaging**: `cmd_audit/hook/` sub-package: `rpe_judge.py` (new), `post_retrieve_hook.py` (two-stage orchestration, rewritten), `constants.py` (offline-calibrated, + RPE_JUDGE_WEIGHTS, RPE_JUDGE_INTERCEPT, TOP_K, FALLBACK_THRESHOLD), `__init__.py`. `prefix_guard.py` NOT migrated — empty_ctx handled as hard short-circuit, remaining 3 signals become RPE features.

5. **Incremental migration via two PRs**: completed 2026-05-23. PR1 added `hook/` subpackage + rewrote `run_case_v1_with_hook` + deleted `tests/test_cmd_audit_issue18_pre_cmd_hook.py` + partially trimmed `test_cmd_audit_issue16_rpe_prefilter.py`. PR2 physically deleted `cmd_audit/prefix_guard.py`, `cmd_audit/rpe_prefilter.py`, `cmd_audit/replay_ordering.py`, root `cmd_audit/post_retrieve_hook.py`, and `cmd_audit/hook_constants.py`; deleted `run_case_v1_with_prefilter` + batch wrapper; renamed CLI semantics to `--use-hook` / `--no-hook` (kept `--no-prefilter` alias); rewrote provenance integration test to use hook entry; removed `AuditResult.fallback_triggered`.

6. **PreCmdDecision restructured**: `trigger_cmd: bool`, `stage: str` (3 values), `per_replay_scores: tuple[ReplayScore, ...]` (always 10 elements; sentinel `is_sentinel=True, p_score=-1.0` only in `mode="online"` empty_ctx path), `selected_replays: tuple[str, ...]` (empty_ctx: full 10; rpe_top_k: top-3 subset; rpe_below_threshold: empty). `anomaly_score`, `prefix_guard_signals`, `reason`, `reason_codes`, `surprise_score`, `utility_score`, `rpe`, `fallback_triggered` removed.

7. **Online vs offline mode flag**: `post_retrieve_hook(..., mode="online" | "offline")`. `mode="online"` (default, used by `run_case_v1_with_hook`): empty_ctx path skips RPE Judge inference, fills sentinel ReplayScores. `mode="offline"` (used by `scripts/calibrate_hook.py`): empty_ctx path still runs RPE Judge for paper hook effectiveness analysis. Stage 2 logic identical between modes.

8. **No oracle topline baseline**: `replay_ordering.py` (gold-aware ordering) deleted entirely in PR2, not preserved as paper baseline. Hook evaluation baselines limited to always-trigger / never-trigger / random ordering.

**Why**: Decision 31's parallel OR + all-or-nothing replay doesn't support per-replay selection. D-MEM's Critic Router pattern validates per-stimulus scoring. User preference for fixed top-k over dynamic threshold for cost predictability. PrefixGuard's truncation patterns have structural zero online recall (retrieved text is plain, no metadata tags). Remaining PrefixGuard signals (near_duplicate, low_count) are informative but not deterministic — better as continuous features in RPE Judge than as independent gate. Subagent labels chosen over phrase-match because hook accuracy ceiling matters more than calibration time; subagent use stays offline-only, deployment hook remains zero-LLM. Global thresholds chosen over per-adapter because 50 hold-out × 2 adapters → too few samples per grid cell for reliable per-adapter calibration.

**Design doc**: `cmd_innovation_core/issues/0021-hook-redesign-three-stage-rpe-judge.md`.

**Related**: Decision 27 (original RPE+PrefixGuard two-tier), Decision 31 (superseded hook internals), Decision 32 (downstream CMD layer, unchanged), Issue 0019 Phase B (provides `SubagentScorer` reused as Step 1 label scorer).

18. **New issues** (dependency-ordered):
   - 0021: Hook 重设计 — 两阶段 + RPE Judge per-replay 排序（新，design doc 已有）
   - B: RepairAction + `adapter.apply_repair`
   - A: RepairExecutor + RepairOrchestrator
   - G: ECS iterative repair (`draft_ecs_for_label`, `close_deltas` drive)
   - D: Failure Memory upgrade (composite key + fm_context injection)
   - E: Self-supervision surrogate → 0021 Step 2（离线测量，不训练）
   - C: `run_case_v1_with_hook`（在线入口注入模式）
   - F: `PreCmdDecision` signals → `AuditResult`
   - H: V2 cascade pre-burial (`cascade_candidates` field)

**Related**: Decision 17 (context construction mode), Decision 20 (episodic trace anchoring), Decision 28 (provenance DAG), Decision 29 (gold evidence limitation + self-supervision).

## Decision 34: Paper Claim Integrity — At-scale Re-test, Headline Eval Set, Hook Demotion, Rewind Reframe, Branch P/D/S Resolutions (2026-05-23/24, RESOLVED)

Bundled resolution from 2026-05-23/24 grilling sessions ("discussion 3"). Eleven binding decisions (R1-R11) covering paper claim integrity ahead of 2026-06-10 V1.0 arxiv preprint and post-corpus V1.1 venue submission. Supersedes prescribed details in Decision 33 step 1 (training-label scorer source) and Decision 30 (Rewind head-to-head).

**Resolution**:

1. **R1 — At-scale Macro F1 = 1.000 is a phrase-match artifact, not a paper claim**. The 596-case at-scale result (`V0V1_gate_status.md` 2026-05-22) was produced under `replays.py:477` shortcut (`answer = case.gold_answer if evidence_score == 1.0 else ""`, `agent_generate=None`), so `recovery_gain ∈ {0.0, 1.0}` and the perfect identity matrix is mechanical. Re-test required on the same 596 cases under: (a) `agent_generate` = qwen2.5-7b ollama producing replay answers from `baseline + evidence_block` (label string dropped per R1 point 5); (b) evidence scorer = an LLM independent of the agent model; (c) on-the-fly LLM rescoring of `vector_memory` baseline so `recovery_gain = evidence_score_replay_llm - evidence_score_baseline_llm` is parity-scored. Pre-baked `baseline.evidence_score` preserved for backward compat.

   **R1 point 5**: `_build_replay_agent_context` drops `CMD ATTRIBUTION LABEL` line. Label is the *output* of attribution, not an input.

2. **R2 — Post-Repair Context Replay must run the agent**. `run_post_repair_context_replay` currently does substring matching (`post_repair.py:227-230`). Wire `agent_generate(query, repaired_context)` so the agent answers, then score the answer (not the context). Wire `AnswerVerifier` (issue 0019 Phase B, "Decision B 待接入") into the `recovered` decision: `recovered ⇔ AnswerVerifier == EQUIVALENT`. Default partial threshold τ=0.5 for headline; calibrate post-hoc on 30-50 manually-inspected cases. `AnswerVerifier` runs on the independent evaluator (≠ agent model).

3. **R3 — Headline argmax tie_margin = 0.0**. Zero free parameters in the decision rule. Per-case `recovery_gain` distributions logged. Coupled-failure becomes a separate post-hoc subset report on 30-50 near-tie cases manually inspected, calibrated for coupled recall ≥ 80%.

4. **R4 — Researcher-adjudicated 130-case headline + LLM-A assist**. 596 deepseek labels are LLM-annotated, so LLM-vs-LLM agreement is circular. Researcher hand-labels 130 stratified cases (~16 × 8 active labels) with LLM-A (`llama-3.3-70b-instruct`) candidate suggestion + accept/reject. 20-case blind spot-check measures automation bias via κ(researcher_blind, researcher_assisted). High+medium confidence subset = headline; low → appendix. Headline claim binds to adjudicated set: "CMD Macro F1 = X on N high+medium adjudicated cases vs LLM-as-judge = Y vs evidence_recall = Z." Supplementary: "CMD reproduces deepseek labels at Macro F1 = W across 596." Cohen's κ between researcher and deepseek reported as methods artifact. **R4-prov**: deepseek labeling prompt + script must be checked into `scripts/annotate_perturbation_labels.py`.

5. **R5 — Hook → supplementary**. Decision 33's two-stage `empty_ctx + RPE Judge top-3` design is implemented (issue 0021) and stays. But: (a) Experiment 2 bypasses hook (`run_case_full_v1`, no top-k); headline independent of hook quality. (b) `scripts/calibrate_hook.py` refactored to consume at-scale re-test outputs as training labels — labels free byproduct under exactly the scorer paper headline uses. (c) Hook efficacy = one supplementary table. Decision 33's "SubagentScorer (qwen) for hook training labels" overridden by R5 (use whatever scorer headline eval uses).

6. **R6 — CMD vs Rewind head-to-head dropped**. Different layers (runtime vs memory pipeline), different input modalities. Replaced with layered-stack positioning + reframed 5-dim table ("Runtime debugger vs Memory debugger") + 3-4 boundary examples in related work. ~2 hr writing. Decision 30 receives addendum.

7. **R7 — Experiment 1 hardened**. 80 cases (20 × 4 labels). Add `corrected_only_padded` 5th mode (token-controlled). 3-trial `none`-mode pre-check (lenient: exclude if ≥1 of 3 trials produces correct answer). Researcher manually inspects/edits 80 ECS records before mode rendering. Document inspection in methods. ~400 LLM calls + 240 pre-check + 400 secondary judge ≈ 1040. <$3 at evaluator price. 5-mode ablation (cause-only / wrong_memory-only) deferred to V2 if positive.

8. **R8 — Paper claim binding sub-resolutions**:
   - **Q11**: standalone Failure Memory recurrence comparison (3-case smoke `recurrence_comparison.csv`) **dropped**. Decision 19 paper claim #1's "store→reuse" arrow satisfied by Experiment 1's `none` vs `corrected_only` McNemar. FM-retrieval-recall as one supplementary paragraph.
   - **Q12**: Repair depth = design claim, not aggregate statistic. "Level 2 capability is a design property of CMD's RepairAction emission" (`repairs.py:563-672` shows LLM tool emits `target_item_id`); paper shows representative `(case → action)` traces. V2 cascade pre-burial via `cascade_candidates` (currently always `()` in V1).
   - **Q13**: AttributionFailed cases reported as principled abstention (conformal-prediction terminology). Two-tier headline: coverage% + Macro F1 on attributed cases. 8-label headline + 11-label supplementary architecture-completeness note (synthetic granularity/graph/safety probe cases acknowledged but not quantitatively claimed).

9. **R9 — Artifact regeneration matrix**. All 11 artifact types regenerated under LLM stack on 596 cases (V1.0); per-source split preserved (LongMemEval/MemoryArena/ToolBench); pre-D34 artifacts archived to `artifacts/legacy_phrase_match_2026_05_22/` with MANIFEST.txt; new artifacts get `artifacts/MANIFEST.txt`. Three semantic shifts annotated. `recurrence_comparison.csv` dropped (Q11). Re-runs again under V1.1 when corpus migrates (issue 0035).

10. **R10 — V1.0/V1.1 dual-release pattern + paper-craft framing**:
    - V1.0 arxiv preprint at 06-10 binds to 596-derived 130-case headline.
    - V1.1 venue submission binds to full-corpus 130-case headline post-issue-0035.
    - Cross-dataset generalization claim: V1.0 = coverage only; V1.1 = explicit generalization (post-corpus N supports it).
    - Online ~9/11 deployment claim retained with retention% backing from surrogate-gap rerun under LLM stack on 50-case hold-out (issue 0036).
    - Two-evaluator robustness on 130-case headline only.
    - Bootstrap CIs (1000-iter case-level resample) on Macro F1 + top-2 + per-label F1 + per-baseline numbers + κ.
    - Cost/latency reported as headline column (tokens / wallclock_sec / usd per case, agent + scorer + verifier subtotals).

11. **R11 — LLM-A for 130-case adjudication = `llama-3.3-70b-instruct`**. Different family from deepseek-v4-pro-max (annotator), qwen2.5-7b (agent), evaluator scorer. Open weights → fully reproducible. ~$0.06 for 130 calls at Groq/Together pricing. Researcher reads (case, candidate label, rationale), accepts/rejects/replaces, assigns confidence. **Automation-bias countermeasure**: 20-case blind spot-check labeled before LLM-A pass, then re-labeled with LLM-A; κ(researcher_blind, researcher_assisted) reported. If κ < 0.7, researcher is anchoring; redo without LLM-A.

**Why bundled, not 11 separate decisions**: All eleven came from the same grilling thread, and they reference each other. Bundling matches Decision 32/33 style.

**Sequencing impact**: TASK.md updated; see TASK edit in REPAIR.md §4.

**Source**: 2026-05-23/24 grilling-with-docs sessions "discussion 3". Full integration in REPAIR.md at repo root.
