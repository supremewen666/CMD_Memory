# CMD Innovation Core

This folder is the compact research package for **Counterfactual Memory Debugger (CMD)**. It is organized for continuing the CMD paper/experiment track without rereading the whole exploration workspace.

## Read Order

1. `../knowledge/current-memory.md`  
   The current compressed memory of the CMD direction.

2. `plans/cmd_research_plan_and_roadmap.zh.md`  
   Chinese full research plan and roadmap.

3. `plans/cmd_open_decisions.md`  
   Resolved V0 decisions: label scope, `premature_extraction_error`, subagent judge role, standalone harness, and Post-Repair Context Replay.

4. `../knowledge/topic-cmd-memory-failure.md`  
   Focused knowledge page for CMD failure diagnosis.

5. `../ideas/hyp-005.md` through `../ideas/hyp-010.md`  
   Core CMD hypotheses: Verbatim Event Oracle, CMD vs subagent judge, audit-module-first architecture, Post-Repair Context Replay, package-oracle probes, and V0.5 retrieval baselines.

## Folder Structure

```text
cmd_innovation_core/           ← Implementation planning (source of truth for code)
  README.md
  CONTEXT.md
  plans/
    direction_01_counterfactual_memory_debugger.md
    direction_01_research_plan.md
    cmd_research_plan_and_roadmap.md
    cmd_research_plan_and_roadmap.zh.md
    cmd_open_decisions.md
  prd/
    cmd_minimal_probe_prd.md
  issues/
    README.md
    0001-define-probe-dataset-and-gold-evidence.md
    ...
  prototypes/
    cmd_probe_logic_prototype.md
    post_repair_and_monitor_contract_prototype.md
    rpe_monitor_prefilter_prototype.md
  gates/
    V0V1_gate_status.md
  tdd/
    cmd_tracer_bullets.md

../knowledge/                  ← Research knowledge (metabolism-managed)
  _index.md
  current-memory.md
  topic-cmd-memory-failure.md

../ideas/                      ← Research hypotheses
  hyp-001.md .. hyp-012.md

../reference_notes/            ← Paper & GitHub notes (44 entries)

../logs/                       ← Daily metabolism logs
  2026-05-08-v0-harness-slice.md
  2026-05-10-metabolism-day0.md
  2026-05-11-metabolism-day1.md
```

Old side-branch exploration files have been removed from this compact package to reduce retrieval noise. Research knowledge (hypotheses, reference notes, knowledge pages, logs) now lives at the project root under the metabolism directory structure. `cmd_innovation_core/` retains implementation-planning files only: `plans/`, `prd/`, `issues/`, `prototypes/`, `gates/`, `tdd/`, and `CONTEXT.md`.

## Core Claim

CMD defines **agent-memory failure diagnosis** as an interventional attribution problem:

```text
failed task
  -> lightweight monitor
  -> counterfactual replay over memory operations
  -> operation-level attribution by recovery gain
  -> Error-Cause-Solution
  -> Post-Repair Context Replay
  -> User Memory repair
  -> Failure Memory for future similar tasks
```

The first paper should not claim a universal memory architecture. It should claim that operation-level counterfactual replay gives more accurate and actionable labels than final-answer scoring, evidence-recall heuristics, or subagent judge explanations.

## Competitive Positioning (2026-05-19, Day 7 Metabolism)

A sustained survey of 119 papers and 14+ GitHub repos across arxiv + openalex + GitHub confirms CMD's position, but the attribution subfield is crowding rapidly.

### Established Comparators

| Approach | Evidence | Granularity | Automated | Gap vs CMD |
|----------|----------|-------------|-----------|------------|
| Subagent Judge | observational | free-form | yes | post-hoc, same trace |
| Trajectory-Informed (2603.10600) | observational | decision-level | yes | coarser granularity |
| ErrorProbe (2604.17658) | observational (backward trace) | step-level | yes | agent steps, not memory ops |
| D-MEM (2603.14597) | RPE signal | binary flag | yes (no attr) | detection only |
| MEMOREPAIR (2605.07242) | influence provenance | artifact-level | no (manual) | repair only, no auto-detection |
| Peaky Peek (agent_debugger) | interactive | visual | no (HITL) | human-in-the-loop |

### Day 5-7 New Entrants

| Approach | Evidence | Granularity | Gap vs CMD |
|----------|----------|-------------|------------|
| **ShapleyAttr (2605.13077)** | **counterfactual (coalitional)** | **agent-level** | closest formal work; agent not operation granularity |
| **TraceAudit (github)** | **counterfactual (removal)** | **chunk-level** | chunk not operation; audit not repair |
| **VerifyMAS (2605.17467)** | **counterfactual (verification)** | **agent-level** | observational verification not causal replay |
| HolisticEval (2605.14865) | observational (span) | span-level | coarser; no counterfactual evidence |
| ConformalAttr (2605.06788) | observational (trajectory) | step-level | statistical guarantees but correlational |

### Counterfactual Convergence

3 independent counterfactual systems at different granularities within a 2-week window:
- **Chunk-level**: TraceAudit (removal-based RAG audit)
- **Agent-level**: VerifyMAS (hypothesis verification), Shapley (coalitional marginalization)
- **Operation-level**: CMD (single-operation replay, Recovery Gain)

CMD occupies the operation-level niche exclusively. The convergence validates counterfactual as the right foundation but signals accelerating competition. Paper must differentiate on: (1) operation-level granularity, (2) causal replay evidence, (3) full diagnosis→repair→validate→store loop.

## V0 Scope (LOCKED — HITL approved 2026-05-10)

Use a standalone CMD-Audit harness.

Core labels (6 pipeline labels):

- `write_error`
- `compression_error`
- `premature_extraction_error`
- `retrieval_error`
- `injection_error`
- `reasoning_error`

## V1 Scope (issues 0011-0015 complete)

Five additional pipeline labels now active (11 total):

- `ingestion_error` (split from `write_error` via `has_ingestion_trace`)
- `route_error` (tier/store misrouting)
- `granularity_error` (sub-optimal memory granularity)
- `graph_error` (graph expansion distractors)
- `safety_error` (safety filter false-positives)

Bad memory item labels (`item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`) are deferred to V2.

Required V0 gates:

1. synthetic perturbation labels are known;
2. baseline memory systems run;
3. replay deltas are produced;
4. attribution beats heuristic and subagent judge baselines;
5. ECS is generated;
6. Post-Repair Context Replay verifies whether the repaired context recovers the original failed query.

## Current Active Slice

Issues 0001-0015 are complete. 453 tests pass, 622 subtests pass. V0→V1 gate HITL approved. V0 LOCKED. V1→V2 gate passes with both mem0 and Letta adapters integrated.

- V0 evidence chain: structurally complete (issues 0001-0010).
- V1 label expansion: all 11 pipeline labels active (issues 0011-0012).
- V1 coupled-failure recalibration + memory-probe baseline: complete (issue 0013).
- CMD-Skill Adapter: mem0 adapter complete (issue 0014), Letta adapter complete (issue 0015).
- Next: Issue 0016 (RPE prefilter) and Issue 0017 (provenance tracking).

The active slice is issue 0016: RPE prefilter (evidence-surprise scoring, top-k replay selection).

## Next Experiment

Scale to 596 cases and add RPE prefilter:

```text
596 CMD probe cases
  → RPE prefilter (evidence-surprise scoring; skip low-surprise replays)
  → 10-replay V1 portfolio (standalone or adapter path)
  → attribution_table.csv with top_k_labels + close_deltas
  → confusion matrix against known perturbation labels (11 × 11)
  → repair_success table after Post-Repair Context Replay
  → provenance DAG per MemoryItem (Execution Lineage + trace-mem citation)
```
