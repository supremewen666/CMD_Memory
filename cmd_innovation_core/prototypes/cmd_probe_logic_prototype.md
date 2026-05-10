# Prototype Brief: CMD Probe Logic

## Branch

LOGIC prototype. The question is whether the CMD state model and attribution loop feel right before implementation.

## Current Prototype Status

The first executable smoke path already validates the simplest `retrieval_error` scenario and the issue 0002 comparator/monitor boundary. Keep this prototype brief as the non-code state model, not as production documentation.

The next prototype question is whether the same state model makes the Verbatim Event Oracle boundary obvious: raw events recover the evidence, extracted memory cannot, and the label should be `premature_extraction_error`.

Issue 0003 should not create a UI prototype. The relevant prototype remains LOGIC: a tiny terminal/state simulation that shows the full case state, replay portfolio, recovery gains, and attribution after each action.

## Prototype Question

Can a tiny interactive probe make it obvious when a failed memory case should be labeled as write, compression, retrieval, premature extraction, injection, or reasoning failure?

V0 only simulates six labels: `write_error`, `compression_error`, `premature_extraction_error`, `retrieval_error`, `injection_error`, and `reasoning_error`.

Boundary rules: the prototype distinguishes CMD-Audit from a future CMD-Skill Adapter, keeps the subagent judge monitor leak-safe, and excludes bad memory item labels from V0 attribution.

## Throwaway Contract

- This prototype should be throwaway from day one.
- It should keep all state in memory.
- It should surface full case state after every action.
- It should be deleted or absorbed after the attribution loop is validated.
- No production data, persistence, polished UI, or broad error handling belongs here.

## Future One-Command Shape

When implementation starts, the intended command should be a single local command that launches an interactive terminal run. The exact command should follow the eventual project runner.

The prototype should assume a standalone research harness. It may show an adapter boundary, but it should not integrate with an existing memory agent.

For the current harness shape, the eventual one-command smoke run is expected to stay close to:

```text
python3 -m cmd_audit run --cases data/probe_cases/<v0_cases>.json --out artifacts/attribution_table.csv --metrics-out artifacts/comparison_metrics.csv
```

## State Model

1. `CaseLoaded`: query, raw events, extracted memory, gold evidence, gold answer, perturbation label.
2. `BaselineFailed`: baseline output and initial score are recorded.
3. `MonitorFlagged`: leak-safe subagent judge monitor attaches a high-recall replay trigger reason without emitting final labels, ECS, memory writes, gold answers, or full failed traces.
4. `ReplaysRun`: each selected replay has output, answer score, evidence score, recovery gain, and cost.
5. `AttributionAssigned`: top-1, top-2, and ambiguity notes are visible.
6. `ECSDrafted`: wrong memory, cause, corrected memory, repair action, and guidance are visible.
7. `RepairedContextBuilt`: corrected memory, repair guidance, and repaired evidence block are assembled without the gold answer.
8. `PostRepairRetested`: the original failed query is rerun against the repaired context.
9. `RepairValidated` / `RepairFailed`: repair success, evidence score, token cost, and regression risk are visible.
10. `RepairSimulated`: targeted repair is compared with a generic hard-case update.
11. `FutureCaseGuided`: future similar task receives corrected memory and repair guidance only.

## Actions To Simulate

- Load a labeled failure case.
- Run baseline memory answer.
- Trigger subagent judge monitor.
- Build the V0 Replay Portfolio in the fixed V0 label order.
- Run Oracle Write replay.
- Run Oracle Compression replay.
- Run Oracle Retrieval replay.
- Run Verbatim Event Oracle replay.
- Run Injection-Oracle replay.
- Run Evidence-Given Reasoning replay.
- Assign attribution from recovery gains.
- Draft Error-Cause-Solution.
- Build repaired context without injecting the gold answer.
- Rerun the original failed query against repaired context.
- Simulate targeted fix.
- Simulate future Failure Memory retrieval.
- Show adapter-boundary payload for a future memory-agent integration.

## Scenario Cards

### Retrieval-Looking But Premature Extraction

Raw events contain the needed evidence, but extracted memory does not. Verbatim Event Oracle recovers the answer while Oracle Retrieval over extracted memory does not. Expected result: do not label as `retrieval_error`.
Expected label: `premature_extraction_error`.

State details to surface:

- gold evidence points to a raw event;
- gold evidence does not point to an extracted Memory Item;
- Oracle Retrieval evidence score stays low;
- Verbatim Event Oracle evidence score recovers;
- top replay maps to `premature_extraction_error`.

### Correct Memory, Bad Retrieval

Extracted memory contains gold evidence, but baseline retrieval misses it. Oracle Retrieval recovers the answer. Expected result: label `retrieval_error`.

### Correct Evidence, Bad Reasoning

Baseline retrieves the right evidence but answers incorrectly. Evidence-Given Reasoning recovers the answer. Expected result: label `reasoning_error`.

### Tied Recovery

Oracle Compression and Oracle Retrieval both recover the answer with close deltas. Expected result: output top-2 attribution and mark coupled failure.

### Leak-Safe Monitor

The subagent judge monitor flags a suspicious trace. Expected result: it triggers replay but emits no final label, ECS, memory write, gold answer, or full failed trace.

### Post-Repair Retest

ECS provides corrected memory and repair guidance. Expected result: rebuilt context recovers the original failed query without injecting the gold answer.

## Verdict Placeholder

Keep only the answer from this prototype: whether the state model exposes enough information for humans to trust CMD attribution before implementation.

Next verdict to capture: whether `premature_extraction_error` is visibly different from `retrieval_error` when only raw-event replay can recover required evidence.
