# CMD Innovation Core

This context defines the domain language for Counterfactual Memory Debugger research. It exists to keep PRDs, issues, prototypes, and tests aligned on the same meaning of memory failure, replay, attribution, and repair.

## Language

**CMD**:
Counterfactual Memory Debugger, a framework that diagnoses memory-augmented agent failures by replaying controlled memory-operation interventions and measuring recovery gain.
_Avoid_: generic memory architecture, generic debugger

**Memory-Augmented Agent**:
An agent whose answer may depend on persistent memory from prior interactions, traces, or user facts.
_Avoid_: plain LLM, chatbot

**Memory Failure**:
A failed task where memory content, the memory pipeline, or reasoning over memory plausibly causes a hallucination, omission, conflict, or misuse.
_Avoid_: generic model error

**Memory Item**:
A stored unit of memory that can be independently assessed as wrong, stale, conflicting, poisoned, or compression-distorted.
_Avoid_: document chunk, vector row

**Memory Pipeline**:
The process that writes, compresses, routes, retrieves, injects, and reasons over memory.
_Avoid_: retrieval stack

**V0 Core Label Set**:
The first probe's six pipeline attribution labels: `write_error`, `compression_error`, `premature_extraction_error`, `retrieval_error`, `injection_error`, and `reasoning_error`.
_Avoid_: full taxonomy

**CMD-Audit**:
The standalone research harness that runs counterfactual replays, produces replay deltas, assigns operation-level attribution, and measures repair validity. Write permissions are limited to replay-local sandbox: Audit may modify in-memory probe state and construct repaired context for Post-Repair Context Replay, but must not write to a real agent's persistent memory.
_Avoid_: runtime skill, production memory agent

**CMD-Skill Adapter**:
The future deployment layer that takes validated repair records from CMD-Audit and applies them to a real agent's memory pipeline, skill registry, or User Memory. Only the Adapter writes to production agent state; Audit never does.
_Avoid_: CMD-Audit, attribution engine

**Counterfactual Replay**:
A rerun of a failed case where one memory operation is replaced by an oracle, variant, or controlled intervention.
_Avoid_: rerun, retry

**V0 Replay Portfolio**:
The six controlled counterfactual replays used by CMD-Audit V0: Oracle Write, Oracle Compression, Verbatim Event Oracle, Oracle Retrieval, Injection-Oracle, and Evidence-Given Reasoning.
_Avoid_: full replay taxonomy, all possible memory interventions

**Recovery Gain**:
The answer-score improvement produced by a counterfactual replay relative to the original failed output.
_Avoid_: improvement, score bump

**Operation-Level Attribution**:
The failure label assigned to the memory operation whose intervention produces the strongest recovery gain.
_Avoid_: blame, explanation

**Attribution Table**:
The V0 evidence artifact that records each probe case, replay scores, recovery gains, predicted label, top-2 labels, ground-truth perturbation label, comparator outputs, and diagnosis cost.
_Avoid_: qualitative explanation log, judge report

**Verbatim Event Oracle**:
A counterfactual replay that uses raw pre-extraction events to test whether required evidence survived memory extraction or ingestion.
_Avoid_: raw retrieval

**Premature Extraction Error**:
A first-class pipeline failure where raw events contain required evidence but ingestion or extraction leaves no recoverable memory representation.
_Avoid_: compression_error, retrieval_error

**Error-Cause-Solution**:
A compact repair record containing the error, cause, corrected memory, repair action, and repair guidance.
_Avoid_: failure log, traceback

**Failure Memory**:
A store of Error-Cause-Solution records used to guide future similar tasks.
_Avoid_: full failure archive

**Post-Repair Context Replay**:
A required V0 gate that rebuilds a repaired context from CMD outputs and reruns the original failed query to test whether the repair actually restores the task. Outputs layered scores (`post_repair_answer_score`, `post_repair_evidence_score`) and a three-value `repair_assessment` (`recovered` / `partial` / `failed`), not a binary gate. A `partial` result (evidence recovered, answer still wrong) exposes coupled failures that the pipeline error previously masked.
_Avoid_: future-task transfer test, generic retry, binary pass/fail gate

**Ingestion Error** (deferred to V1):
A pipeline failure where gold evidence never reached the agent at all (truncated input, upstream loss, missing source). V0 subsumes this under `write_error` because Oracle Write is the correct counterfactual intervention for both cases. If V1 shows ingestion cases have a different repair path, they may be split into a first-class label.
_Avoid_: write_error subtype in V0

**User Memory**:
The long-term user-facing memory that CMD repairs after a diagnosed memory failure.
_Avoid_: profile cache

**Targeted Memory Fix**:
A repair action chosen from the operation-level attribution label rather than from an undifferentiated hard-case update.
_Avoid_: generic update

**Subagent Judge Baseline**:
A trace-reading baseline that explains a memory failure without itself performing counterfactual interventions.
_Avoid_: CMD judge, replacement for CMD

**Subagent Judge Monitor**:
A leak-safe, high-recall monitor that can trigger expensive CMD replay but cannot emit final labels, Error-Cause-Solution, memory writes, gold answers, or full failed traces. `anomaly_reason` is forced to a predefined enum (`answer_vs_evidence_mismatch`, `retrieved_context_incomplete`, `evidence_recall_low`, `confidence_anomaly`); free-form natural language is prohibited. Evidence pointers are opaque IDs only, never content text.
_Avoid_: final judge

**Standalone Research Harness**:
The V0 implementation boundary for reproducible perturbation labels, replay deltas, baselines, and metrics outside any production memory agent.
_Avoid_: production memory agent integration

**Adapter Interface**:
A reserved integration boundary that can later connect CMD to existing memory agents without shaping the V0 harness around them.
_Avoid_: direct production coupling

**RPE Gating** (external, from D-MEM 2603.14597):
A Reward Prediction Error signal used to gate expensive memory restructuring. High-RPE stimuli (surprise, contradiction) trigger deep processing; low-RPE stimuli are handled cheaply. CMD may adopt a similar gating pattern for Subagent Judge Monitor pre-filtering.
_Avoid_: CMD mechanism, mandatory V0 component

**Risk-Sensitive Retrieval** (external, from RSCB-MC 2604.27283):
A retrieval framing where the key question is not similarity but safety: abstention and non-injection are first-class actions with asymmetric penalties for false-positive memory injection.
_Avoid_: standard top-k retrieval, similarity-only retrieval

**Abstention Action** (external, from RSCB-MC 2604.27283):
A retrieval decision to deliberately not inject memory when the risk of injection exceeds the benefit, even when similar memories exist. CMD's retrieval_error attribution should distinguish "retrieved wrong memory" from "retrieved right memory but injected unsafely."
_Avoid_: always-inject retrieval

**Decision Attribution** (external, from Trajectory-Informed Memory 2603.10600):
Observational analysis that pinpoints which agent decisions caused failures. Closest existing work to CMD's operation-level attribution but uses trajectory analysis rather than counterfactual intervention.
_Avoid_: CMD attribution, counterfactual replay

**Agent-Native Memory** (external, from ByteRover 2604.01599):
A memory architecture where the same LLM curates, structures, and retrieves knowledge. The tight write-retrieval coupling is the failure mode CMD's counterfactual replay is designed to disentangle.
_Avoid_: multi-component memory pipeline

**Memory Governance** (external, from MemArchitect 2603.18330):
Policy-driven memory lifecycle management (decay, conflict resolution, privacy controls) decoupled from model weights. CMD's ECS repair guidance is the evidence layer that should inform governance policy selection.
_Avoid_: static memory policy, unmanaged memory

**Competitive Baseline** (new V0 concern):
Existing tools and papers that address parts of the memory debugging problem. Peaky Peek (agent_debugger) does interactive checkpoint replay with human-in-the-loop. Trajectory-Informed Memory does observational decision attribution. D-MEM does binary surprise detection. None do automated counterfactual operation-level attribution.
_Avoid_: CMD competitor, replacement for CMD

## Relationships

- **CMD** diagnoses **Memory Failures** by running one or more **Counterfactual Replays**.
- **CMD-Audit** owns V0 attribution, replay deltas, and repair validation; **CMD-Skill Adapter** is deferred as a deployment layer.
- **V0 Core Label Set** scopes the first probe to six pipeline labels; bad memory item labels plus `granularity_error`, `route_error`, `graph_error`, `safety_error`, and `ingestion_error` are deferred to V1/V2.
- A **Memory Failure** may involve a bad **Memory Item**, a failed **Memory Pipeline**, or both.
- A **Counterfactual Replay** produces a **Recovery Gain**.
- The **V0 Replay Portfolio** is the bounded replay set for the first **Attribution Table**; broader interventions stay deferred.
- **Operation-Level Attribution** maps the largest meaningful **Recovery Gain** to a failure label.
- An **Attribution Table** is claim evidence only when it records replay scores, recovery gains, predicted labels, ground-truth perturbation labels, top-2 labels, and comparator context.
- **Verbatim Event Oracle** diagnoses **Premature Extraction Error** by comparing raw events with extracted memory before assigning retrieval blame.
- **Error-Cause-Solution** updates **User Memory** and writes compact **Failure Memory**.
- **Post-Repair Context Replay** follows **Error-Cause-Solution** and precedes future-task **Failure Memory** evaluation.
- **Failure Memory** should retrieve `corrected_memory + repair_guidance`, not the full failed trace.
- **Subagent Judge Baseline** compares against CMD, while **Subagent Judge Monitor** may trigger replay.
- **Standalone Research Harness** owns V0 reproducibility and exposes an **Adapter Interface** for later integration.

## Example Dialogue

> **Dev:** "This looks like a retrieval error because the answer missed the evidence."
> **Domain expert:** "Run **Verbatim Event Oracle** first. If the raw event contains the evidence but extracted memory does not, this is **Premature Extraction Error**, not **retrieval_error**."

> **Dev:** "Should we store the whole failed trace in **Failure Memory**?"
> **Domain expert:** "No. Store an **Error-Cause-Solution** record and retrieve only `corrected_memory + repair_guidance` for similar future tasks."

> **Dev:** "Can the subagent judge decide the final label?"
> **Domain expert:** "No. It can act as **Subagent Judge Baseline** and leak-safe **Subagent Judge Monitor**, but final attribution comes from CMD replay deltas."

> **Dev:** "Is **CMD-Skill Adapter** the same thing as **CMD-Audit**?"
> **Domain expert:** "No. **CMD-Audit** proves attribution and repair validity; **CMD-Skill Adapter** is a later runtime layer that uses validated repair guidance."

## Flagged Ambiguities

- `CMD-Audit` and `CMD-Skill Adapter` are separate concepts: V0 builds the audit harness, while the skill adapter remains a later deployment boundary.
- `retrieval_error` must mean the correct memory exists in recoverable form but was not retrieved. If raw events contain the evidence and extracted memory cannot recover it, use `premature_extraction_error`.
- `premature_extraction_error` is a first-class pipeline label in V0, not a subtype of `compression_error` or `retrieval_error`.
- **V0 Replay Portfolio** means the six replay paths listed above, not `granularity_error`, `route_error`, `graph_error`, or `safety_error`.
- **Attribution Table** is not a subagent judge narrative. It must stay replay-delta grounded.
- `Subagent Judge Monitor` is leak-safe: it can trigger replay but cannot emit final labels, Error-Cause-Solution, memory writes, gold answers, or full failed traces.
- V0 attribution excludes bad memory item labels (`item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`) and evaluates only the six pipeline labels in **V0 Core Label Set**.
- ECS `cause` may describe item state in natural language (e.g., "stored preference was outdated relative to ground truth") but must not use forbidden item label names or re-declare them through natural language equivalents (e.g., "the memory item is stale").
- `Memory Item` and `Memory Pipeline` are separate failure families. A case may need top-2 or multi-label attribution when both contribute.
- `Failure Memory` is not a raw log archive. Re-injecting complete failed traces risks reintroducing memory pollution.
- `CMD-Audit` write permissions are limited to replay-local sandbox. It may modify in-memory probe state and construct repaired context, but must not write to a real agent's persistent memory. Only **CMD-Skill Adapter** applies validated repairs to production agent state.
- `Oracle Write` injects gold evidence into memory, while **Verbatim Event Oracle** tests whether evidence was lost before or during extraction.
- `Standalone Research Harness` is the V0 implementation target. Existing memory-agent integration is intentionally deferred behind an **Adapter Interface**.
- Version gates are evidence-driven, not feature-stacking: V0→V1 requires the four V0 evidence artifacts passing paper-claim thresholds; V1→V2 requires at least two distinct memory agents integrated through the Adapter Interface without macro F1 regression.
- When both Verbatim Event Oracle and Oracle Retrieval recover evidence, gain ranking naturally picks the stronger replay — no special tie-breaker needed. The case is not an extraction loss, it's a retrieval miss that raw events happened to partially patch.
- When Verbatim Event Oracle and Oracle Retrieval both fail but Oracle Compression succeeds, the case is a legitimate `compression_error` — the diagnosis cost of running two non-recovering replays before finding the root cause is design-expected, not a bug. This additional replay cost is expected for taxonomy boundary cases and is considered design-internal, as long as it remains bounded within the smoke suite.
