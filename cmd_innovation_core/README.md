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

## Competitive Positioning (2026-05-10 Metabolism)

A broad survey of 27 papers and 10 GitHub repos across arxiv + openalex + GitHub confirms CMD's unique position:

| Approach | Evidence Type | Attribution Granularity | Automated |
|----------|--------------|------------------------|-----------|
| Subagent Judge | observational (same trace) | free-form explanation | yes |
| Trajectory-Informed (2603.10600) | observational (execution trace) | decision-level | yes |
| Peaky Peek (agent_debugger) | interactive (checkpoint + human) | visual debugging | no (HITL) |
| D-MEM (2603.14597) | RPE signal | binary surprise flag | yes (no attribution) |
| **CMD (proposed)** | **counterfactual (replay intervention)** | **operation-level (6 labels)** | **yes** |

No existing paper or open-source project does automated counterfactual memory replay for operation-level attribution. The closest tools (Peaky Peek, AgentLens) validate engineering demand but use interactive or observational approaches.

## V0 Scope

Use a standalone CMD-Audit harness.

Core labels:

- `write_error`
- `compression_error`
- `premature_extraction_error`
- `retrieval_error`
- `injection_error`
- `reasoning_error`

Deferred labels:

- `granularity_error`
- `route_error`
- `graph_error`
- `safety_error`

Required V0 gates:

1. synthetic perturbation labels are known;
2. baseline memory systems run;
3. replay deltas are produced;
4. attribution beats heuristic and subagent judge baselines;
5. ECS is generated;
6. Post-Repair Context Replay verifies whether the repaired context recovers the original failed query.

## Current Active Slice

Issue 0006 (validate targeted memory fixes) is the active slice. Issues 0001-0005 and 0009 are complete. The implementation detail map for the active slice is:

- `issues/0006-validate-targeted-memory-fixes.md` — done. Six per-label repair actions complete.

The V0 CMD-Audit evidence chain is structurally complete through issue 0010. The next work is probe suite scaling (V0→V1 gate prerequisite).

## Next Experiment

Build the first attribution table:

```text
50-100 CMD probe cases
  -> fixed-summary / vector-memory baselines
  -> oracle write / compression / retrieval / raw-event / injection / reasoning replays
  -> attribution_table.csv
  -> confusion matrix against known perturbation labels
  -> repair_success table after Post-Repair Context Replay
```
