# Prototype Brief: mem0 Adapter Interface

## Branch

LOGIC prototype. The question is whether CMD-Audit can intercept mem0's `add()` and `search()` operations with minimal surface area while preserving standalone-harness attribution accuracy.

## Source

- mem0ai/mem0 (55,320 GitHub stars, YC S24): universal memory layer, v3 algorithm (April 2026) uses single-pass ADD-only extraction + multi-signal retrieval (semantic + BM25 + entity matching).
- CMD open_decisions Decision 14: mem0 selected as first CMD-Skill Adapter target.
- V0 Cycle 9 (Adapter Boundary): standalone harness already reserves an adapter interface contract. This prototype extends it from abstract boundary to concrete mem0 interception.

## Throwaway Contract

- This prototype is throwaway from day one.
- All state in memory.
- Surface full state after every action.
- Delete or absorb after the interception contract is validated.
- No production data, persistence, polished UI, or broad error handling.
- Does not require a running mem0 instance; intercepts are simulated against recorded mem0 operation traces.

## Question

When CMD-Audit runs counterfactual replays against a mem0-backed agent, can the adapter intercept `add()` and `search()` at well-defined cut points without requiring mem0 internals knowledge, and does the resulting attribution accuracy match standalone-harness accuracy?

## Adapter Cut Points

mem0's v3 pipeline has two natural interception surfaces:

```text
mem0 Pipeline:
  Raw Dialogue/Events
    -> [Cut Point A: add()]     ← intercept here for write-side replays
    -> Memory Store (facts)
    -> [Cut Point B: search()]  ← intercept here for retrieval-side replays
    -> Retrieved Facts
    -> Agent Context
    -> Agent Answer
```

### Cut Point A: `add()` Interception

| Replay | Interception Behavior |
|--------|----------------------|
| Oracle Write | Replace `add()` input with gold evidence facts that should have been written |
| Oracle Compression | Replace `add()` input with uncompressed/complete version of facts |
| Verbatim Event Oracle | Bypass `add()` entirely; feed raw events directly as context |
| Injection-Oracle | Replace `add()` input with correctly formatted evidence block |

### Cut Point B: `search()` Interception

| Replay | Interception Behavior |
|--------|----------------------|
| Oracle Retrieval | Replace `search()` results with gold evidence facts directly |
| Evidence-Given Reasoning | Keep `search()` results as-is; append gold evidence to agent context after retrieval |

### Non-Intercepted (Passthrough)

- Entity linking runs normally (not intercepted — CMD does not modify mem0's entity graph).
- Multi-signal fusion runs normally (not intercepted — CMD evaluates retrieval outcome, not retrieval internals).

## Scenario Cards

### Card A: Write Error on mem0

- A probe case has gold evidence that was never passed to `add()`.
- Baseline: `search()` returns wrong/distractor facts. Answer wrong.
- Interception: Oracle Write replaces `add()` input with gold evidence facts. `search()` runs normally.
- Expected: `search()` now returns correct facts. Answer recovers.
- Attribution: `write_error` → in V1, `ingestion_error` if evidence never reached `add()` at all.
- State to surface: original `add()` input, oracle `add()` input, `search()` results before/after, answer scores.

### Card B: Retrieval Error on mem0

- A probe case has gold evidence correctly stored via `add()`.
- Baseline: `search()` fails to return it (BM25 miss, semantic mismatch).
- Interception: Oracle Retrieval replaces `search()` results with gold evidence facts.
- Expected: correct facts in context. Answer recovers.
- Attribution: `retrieval_error`.
- State to surface: stored facts, `search()` query, original `search()` results, oracle `search()` results, answer scores.

### Card C: Premature Extraction on mem0

- Raw dialogue contains gold evidence (e.g., "user is allergic to penicillin").
- `add()` extracted a lossy fact ("user has medical conditions").
- Baseline: `search()` returns lossy fact. Answer wrong.
- Interception: Verbatim Event Oracle bypasses `add()`/`search()` entirely; feeds raw dialogue directly.
- Expected: raw dialogue provides the specific evidence. Answer recovers.
- Attribution: `premature_extraction_error`.
- State to surface: raw dialogue text, `add()` extracted facts, bypass flag.

### Card D: Injection Error on mem0

- Gold evidence is stored correctly and `search()` returns it.
- But mem0 injects it into agent context in a confusing format (wrong order, missing boundary markers).
- Interception: Injection-Oracle replaces the context injection block with cleanly formatted evidence.
- Expected: same facts, cleaner presentation. Answer recovers.
- Attribution: `injection_error`.
- State to surface: original context injection block, oracle injection block, answer scores.

### Card E: Attributed Label Matches Standalone

- Same probe case runs through (a) standalone CMD-Audit harness and (b) mem0 adapter path.
- Expected: attribution label identical. Macro F1 on 6-label smoke suite unchanged.
- State to surface: standalone label, adapter label, match flag, any delta in recovery gains.

### Card F: Adapter Does Not Mutate mem0 State

- All replays run in sandbox: intercepted `add()` and `search()` produce temporary in-memory variants.
- Original mem0 store is never written to.
- Expected: after replay, mem0 store state is identical to before replay.
- State to surface: store checksum before/after.

## State to Surface

After mem0 adapter replay, display:

- `case_id`
- `perturbation_label`
- `interception_points_used` (which cut points were intercepted)
- `add_input_original` / `add_input_oracle`
- `search_results_original` / `search_results_oracle`
- `replay_scores` (per replay: answer_score, evidence_score, recovery_gain)
- `predicted_label` (adapter path)
- `predicted_label` (standalone path, for comparison)
- `label_match` (bool)
- `store_mutated` (must be false)

## Adapter Interface Contract

```text
Mem0Adapter:
  # Interception
  intercept_add(case_id: str, original_facts: list[str], replay: ReplayName) -> list[str]
  intercept_search(case_id: str, original_query: str, original_results: list[MemoryItem], replay: ReplayName) -> list[MemoryItem]

  # Read-only access
  get_store_snapshot() -> StoreChecksum

  # No write methods — all mutations are in-memory sandboxed variants

ReplayName:
  "oracle_write"
  | "oracle_compression"
  | "verbatim_event_oracle"
  | "oracle_retrieval"
  | "injection_oracle"
  | "evidence_given_reasoning"
```

## Relationship to Standalone Harness

```text
Standalone Harness (V0):
  ProbeCase -> ReplayEngine -> Attribution -> ECS

mem0 Adapter (V1):
  ProbeCase -> Mem0Adapter(intercept_add, intercept_search) -> ReplayEngine -> Attribution -> ECS
                   |
            mem0 Store (read-only, sandboxed)
```

The ReplayEngine, Attribution, and ECS layers are unchanged. Only the input source changes: from fixture-controlled memory operations to intercepted mem0 operations.

## Verdict Placeholder

Does the two-cut-point interception (add + search) cover all six V0 replays without requiring mem0 internal knowledge? Are there mem0-specific failure modes (entity linking errors, multi-signal fusion bugs) that the adapter cannot intercept and therefore cannot diagnose?
