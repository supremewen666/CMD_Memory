# TASK: CMD — Counterfactual Memory Debugger

## Read First

1. `CONTEXT.md` — final design: domain language, two-branch runtime, label taxonomy, boundaries
2. `DISCUSSION.md` — converged decisions (G-Eval dual-axis, step-level MCTS A–F, item gate, hook reshape #2–#5)
3. `CLAUDE.md` — coding boundaries

CONTEXT.md is the authority on the target design. The code below predates it; the tasks bring code in line with CONTEXT.md.

## Code State (starting point)

The harness runs a flat portfolio of counterfactual replays scored by an LLM subagent stack, with attribution by recovery-gain ranking, ECS drafting, iterative repair, and a Post-Repair Context Replay quality gate. mem0 and Letta recorded-trace adapters exist. A confidence hook and provenance DAG are wired.

Three things are now stale relative to CONTEXT.md and are the subject of the tasks below:

1. **Label taxonomy** — code carries 11 pipeline labels including `reasoning_error`, `route_error`, and 4 formation labels (`write_error`, `compression_error`, `premature_extraction_error`, `ingestion_error`). The final design keeps **5 live pipeline step actions** + **5 item labels**; formation/reasoning/route are removed from the live surface.
2. **Flat replay portfolio** — the 10-replay flat portfolio is replaced by a step-level MCTS over generation points (5 step actions) plus a Tier 2 item gate (5 item labels). Formation oracle replays are deleted — evidence-missing is absorbed by the Fill branch, not diagnosed.
3. **Hook** — currently a multi-feature gate that predicts replays; reshaped to a pure two-branch confidence gate (Fill vs Fix) that does not classify.

## Label Taxonomy (target)

**Pipeline step actions (5, Tier 3 MCTS generation-point actions):** `retrieval_error`, `injection_error`, `granularity_error`, `graph_error`, `safety_error`. First three always legal; `graph_error` gated on `is_graph_expanded`, `safety_error` gated on `passed_safety_filter`.

**Item labels (5, Tier 2 item gate):** `item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`.

**Removed from live surface:**
- Formation failures (`write_error`, `compression_error`, `premature_extraction_error`, `ingestion_error`) — not labels; evidence-missing routes to Fill branch with no sub-typing (information-theoretic floor).
- `reasoning_error` — not a label; non-memory reasoning faults emerge through back-prop (no intervention recovers → Δ≈0 → UCT abandons).
- `route_error` — absorbed into `retrieval_error` (cross-tier misfetch, no separate label).

## Two-Branch Runtime (target)

```
retrieval recall → hook (6 factors → evidence present in recall?)
  ├─ NO  (evidence missing) → FILL: first send to API generate (answer this turn)
  │         → async re-extract / ask / HITL.  No diagnosis, no label.
  └─ YES (evidence present) → FIX: hook lightweight correction (de-conflict / re-rank)
            → send to API generate
            → subagent loop: Tier 2 item gate → Tier 3 pipeline MCTS
```

Hook is a pure confidence gate: it answers "do we diagnose?" not "what's wrong?". All diagnosis lives in Tier 2 / Tier 3.

## Tasks (dependency order)

### 1. AnswerRubricScorer

Continuous answer-axis G-Eval, prerequisite for the MCTS value function and leaf Δ.

- `scoring/llm.py`: add `_continuous_verify_answer(client, answer, gold_answer)` (logprob G-Eval over the answer rubric, returns `E[score] ∈ [0,4]`); add `AnswerRubricScorer` class whose `verify` returns `float ∈ [0,1]`. `_ANSWER_RUBRIC_SYSTEM_PROMPT` already exists.
- Fallback chain: logprobs unavailable → discrete rubric parse → return 0 (conservative tie-break, matches evidence axis).
- `scoring/__init__.py`: export `AnswerRubricScorer`.
- `build_answer_verifier`: add `"answer-rubric"` mode returning `AnswerRubricScorer` directly (retire the rubric-answer hack path; keep old modes for back-compat).
- Tests under `tests/scoring/`.

### 2. Label taxonomy cleanup

Bring `core/labels.py` to the target taxonomy.

- `PIPELINE_LABEL_ORDER` → the 5 step actions only (`retrieval_error`, `injection_error`, `granularity_error`, `graph_error`, `safety_error`).
- Drop `reasoning_error` and `route_error` everywhere. Remove `evidence_given_reasoning` and `oracle_route` from `REPLAY_TO_LABEL`.
- Remove the 4 formation labels from the live pipeline set. Keep their names only where boundary/limitation docs reference the information-theoretic floor.
- Promote the 5 item labels from `OUT_OF_SCOPE_ITEM_LABELS` to a live `ITEM_LABELS` set used by Tier 2.
- Update `validate_label` to accept 5 step actions; item labels validated through a separate item-label validator (streams stay separate).
- Update all callers and tests that assume the 11-label set.

### 3. Hook two-branch refactor

Reshape the hook to emit a branch decision, not a replay prediction.

- `hook/post_retrieve_hook.py`: compute the 6 confidence factors (`retrieval_score_max`, `retrieval_score_entropy`, `evidence_coverage`, `memory_recency_min`, `memory_recency_spread`, `conflict_signal`) → single scalar → branch.
  - Fill: return a generate-first signal; no diagnostic cascade.
  - Fix: perform lightweight correction (de-conflict / re-rank), then signal entry into the subagent loop.
- Remove the `empty_ctx` gate (subsumed by Fill).
- Remove RPE per-replay ranking from the hook (diagnosis moves to Tier 2/3).
- `hook/constants.py`: 6-factor schema.
- Tests under `tests/hook/`.

### 4. Tier 2 item gate

New `cmd_audit/item_gate/` subpackage implementing reference-contrast divergence over the recall set.

- `divergence.py`: directed entailment divergence via the `_continuous_verify` logprob path. Two-direction read → wrong (forward) vs compression (reverse) typing.
- `collision.py`: recall-set pairwise contrast (≤ C(5,2)), 0 generation. Large directed divergence + reliable newer timestamp → `item_stale`; large divergence + same-period/no-timestamp → `item_conflict`; small divergence → pass.
- `loo.py`: LOO reconstruction of `m̂_i` from store∖{m_i}, 1 generation + contrast → `item_wrong` / `item_compression_distorted`.
- `gate.py`: cost-ladder orchestration ①timestamp (direction signal only) → ②collision → ③LOO → HITL terminal (threshold-edge divergence + `item_poisoned`, source-free floor). Item-wrong → item treatment, skip Tier 3; item-correct → enter Tier 3.
- Scope: collision/divergence operate only on this retrieval's recall set (~5 items), per-task scoping.
- Tests under `tests/item_gate/`.

### 5. Tier 3 step-level MCTS

New `cmd_audit/mcts/` subpackage. Single-player MCTS + UCT over generation points; replaces the flat portfolio for pipeline attribution. Tool calls / pure-reasoning hops / context accumulation = pass-through (no branching). Depth = generation-point count.

- `actions.py`: the 5-action table; legality (`is_graph_expanded` gates `graph_error`, `passed_safety_filter` gates `safety_error`); identity (no-op) action for clean re-run.
- `value.py`: nested value, no free weight —
  `k = #{atom_i : rubric_B(ctx_h, atom_i) ≥ τ}`, `ceiling = k/N`, `V_scalar = ceiling · (E[score_answer]/4)`; `V_vector = (E[score_answer], [rubric_B raw scores × N])` stored per node.
- `tree.py`: node state (memory + system prompt + question at root; child = parent prefix + one hop under a label intervention, inheriting the parent's intervention consequences). UCT selection `argmax_c [Qmax(c) + C·√(ln N(parent)/n(c))]`; expansion initializes new-node `Qmax = V_scalar` (soft prune); max-backup `Qmax(a) ← max(Qmax(a), Δ)` along the path.
- `rollout.py`: set remaining hops to identity, full re-run to terminal, leaf `Δ = AnswerVerifier(leaf_answer, gold_answer)` (or surrogate-of-gold when y unavailable).
- `search.py`: MCTS loop with shallowest-recovery-depth stop (depth-1 single-point intervention recovers → that hop is main culprit, stop). Credit `credit(h) = Qmax(prefix<h + h:best_label) − Qmax(prefix<h + h:identity)`; primary label = argmax credit; close_deltas = top-k for repair.
- Online mode: budget-capped shallow tree / UCT rollouts.
- Delete the flat formation oracle replays (`oracle_write`, `oracle_compression`, `verbatim_event_oracle`) and `evidence_given_reasoning` / `oracle_route` from the live attribution path.
- Tests under `tests/mcts/`.

### 6. Failure Memory step-level key

- `repair/failure_memory.py`: extend the composite retrieval key to `(query_signature, hop_index, label)` for step-level transfer. Keep the flat key path back-compatible.

### 7. Harness integration

- `harness.py`: wire the two-branch runtime into `run_case`. Hook → Fill (early return, generate-first flag) or Fix (lightweight correction → generate → Tier 2 → Tier 3 → ECS → repair → Post-Repair Context Replay). Replace the flat portfolio call with the MCTS path.
- `eval/release_gates.py`: extend the step-level gate with step-level attribution metrics (per-hop credit, primary-label correctness).
- Update integration tests.

## Boundaries (constrain implementation)

- **CMD-Audit** (research harness) writes only to `artifacts/sandbox/`. **CMD-Skill Adapter** (deployment layer) applies validated repairs to production state. Keep separate.
- **Adapter sandbox**: SHA-256 checksum over store state before/after replay; any mutation is `SandboxViolationError`.
- **Leak-safe monitor**: enum-locked `anomaly_reason`, opaque evidence IDs; no labels, ECS, writes, gold answers, or full traces.
- **Post-Repair Context Replay**: rerun original query with repaired context, no gold injection, three-value `repair_assessment` (`recovered` / `partial` / `failed`).
- **ECS cause streams stay separate**: a step-action ECS names a step action; an item ECS names an item label. The pipeline stream never borrows item-fault vocabulary in free text.
- **Evidence-missing → Fill**: when recalled text lacks evidence, the failure is upstream formation; route to Fill, do not sub-type, do not assign `retrieval_error`.
- **Reference hierarchy law**: never descend a reference tier when a stronger reference is available; reconstruction is last resort.
- **Directed entailment divergence** is NOT KL divergence — it is a G-Eval expectation over an entailment score token (asymmetric, d(x,x)=0, no triangle inequality).

## Evidence Gates

Do not claim a result until its artifact exists:

- Attribution: per-case predicted label + per-hop credit table + confusion matrix.
- Comparator: CMD vs baselines metrics.
- Repair: Post-Repair Context Replay assessment distribution.
- Recurrence: Failure Memory recurrence comparison.

Paper claims focus: (1) automated counterfactual attribution at step-level granularity, (2) Post-Repair Context Replay as automated semantic quality gate, (3) full detect → diagnose → repair → validate → store loop.

## Non-Goals

- Do not build a production memory agent — CMD-Audit is a research harness, CMD-Skill Adapter is a deployment layer.
- Do not add UI or dashboard work.
- Do not train a learned attribution classifier — replay/MCTS deltas are the evidence foundation. (MCTS-trajectory distillation into a no-search policy is out of current scope.)
- Do not reintroduce formation, reasoning, or route as live labels.
- Do not claim gold evidence is available online — formation sub-typing needs it offline (information-theoretic bound).
