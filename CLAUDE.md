# CLAUDE.md

## Project Summary

> **Runtime contract override (2026-08-23).** The live system is now a
> gold-free ECC memory-state correction loop:
> `MemAudit telemetry -> Contract -> GHOST repair selection -> shadow state
> repair -> ECC checks -> commit/rollback -> EccRepairReceipt -> incident sinks
> + router evolution`. Runtime detection, selection, mutation, acceptance,
> receipt creation, and router updates must never read dataset gold/labels or
> same-trace answer replay. GHOST/Thompson updates consume
> `EccRepairReceipt`, not task accuracy. The replay/recovery-gain design below
> is retained as legacy/offline baseline context and does not define the live
> loop. `CONTEXT.md` and
> `docs/RUNTIME_EVIDENCE_BOUNDARY_CONTRACT.md` are authoritative when older
> text conflicts with this override.

CMD frames agent-memory failure diagnosis as counterfactual attribution. The runtime is a two-branch confidence gate followed by a diagnostic cascade:

```text
retrieval recall
  -> hook (4 live confidence factors, 0 LLM calls -> evidence present in recall?)
       ├─ NO  (missing) -> FILL: generate this turn, async re-extract. No diagnosis, no label.
       └─ YES (present) -> FIX: lightweight correction (de-conflict / re-rank) -> generate
                              -> Tier 2 item gate (item signals folded into counterfactual, not predicted labels)
                              -> Tier 3 single-point counterfactual scan (exhaustive over legal step actions at one generation point; SINGLE_POINT_DEPTH = 1)
                              -> ECS draft -> RepairExecutor / RepairOrchestrator
                              -> Post-Repair Context Replay (quality gate)
                              -> Failure Memory for future similar failures
                              -> two-tier skill library: recovered repairs distilled into composite-operator skills
                                 (skill body = executable repair operator, fingerprint-retrieved, recovery-gated accept-if-improves)
```

The deliverable is a standalone **CMD-Audit** harness whose headline is **counterfactual repair efficacy** — a self-repair + self-evolution loop, not a fault classifier reporting label macro-F1. The **4 live pipeline step actions** (`retrieval_error`, `injection_error`, `granularity_error`, `safety_error`) and **5 item labels** (`item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`) are an internal action space for repair search, not prediction targets scored against gold; recovery gain Δk is the fitness signal. Construction of repaired context is gold-free by structure — a pure function of `(recall_set, pipeline_action)` that never reads `case.gold_*`. **Selection is not gold-free**: `_rollout_score` → `rollout_to_terminal(..., gold_answer, answer_verifier)` ranks actions by `score_answer_with_verifier(answer, gold_answer)`. Any experiment that needs a gold-free *choice* (e.g. the observational arena's CMD arm) must inject a reference-free `answer_verifier` and a non-empty sentinel `gold_answer` — `_compute_recovery_gain` short-circuits to `0.0` on a falsy gold. Online attribution is offline-oracle / online-student: an offline exhaustive single-point scan distills transferable `(gen_point, action)` priors; online, priors re-order the action list. The scan is bounded by both `max_iterations` (intervention-rollout budget) and `time_limit_seconds`; `attribute_single_point` forwards both to `SinglePointConfig`. On either cutoff, `SearchResult` records `truncated`, `unscored_actions`, and `timed_out_actions`, so claims of exhaustiveness are conditional on no recorded truncation (tree search retired from the mainline per C6: TRUE_COUPLED 1/30, recorded in `DISCUSSION.md`; there is no `cmd_audit/mcts/` package). `SinglePointConfig.include_item_actions` defaults to `False` and `harness.py` does not override it, so the live Tier 3 scan proposes only the 4 step actions; item findings enter through `intervention_config["item_signal_hints"]`, not as searchable actions. Formation failures (write / compression / premature_extraction / ingestion) are absorbed by the Fill branch, not labeled; reasoning faults emerge through back-prop, not labeled. `CONTEXT.md` is the authority on the target design; `SKILL_EVOLUTION_DESIGN.md` is the authority on the operator DSL / skill-library layer.

## Required Reading

Before changing plans or code, read:

1. `CONTEXT.md` — domain language, boundaries, label taxonomy (authority on target design)
2. `DISCUSSION.md` — converged design decisions (G-Eval dual-axis, item gate, hook reshape; step-level MCTS retired per C6)
3. `SKILL_EVOLUTION_DESIGN.md` — operator DSL + composite-operator skill library design and retirement list
4. `knowledge/current-memory.md` — compressed active memory
5. `cmd_innovation_core/plans/cmd_open_decisions.md` — decision log

## Commands

Python >= 3.11, zero external PyPI dependencies. Tests use `unittest` via `pytest`.

```bash
# Run all tests
python -m pytest tests/ -v

# Run a subpackage's tests
python -m pytest tests/repair/ -v
python -m pytest tests/eval/ -v

# Run a single test file
python -m pytest tests/adapters/test_stale_adapter.py -v

# Run a single test method
python -m pytest tests/scoring/test_rubric_scoring.py::RubricScorerTest -v

# Run the CMD-Audit CLI harness
python -m cmd_audit run --cases data/probe_cases/real_multihop_cases.json
```

## Code Architecture

`cmd_audit/` is a standalone research harness with no external PyPI dependencies; `cmd_audit/baselines/` is its comparator subpackage. Modules and data flow:

```
data/probe_cases/*.json
  -> data_io/ loaders
  -> harness.py run_case / run_cases / run_real_suite
    -> hook/ two-branch confidence gate (Fill vs Fix)
    -> baselines/ comparators and memory-probe baseline
    -> item_gate/ Tier 2 item labels (reference-contrast divergence)
    -> counterfactual/ Tier 3 single-point scan (depth 1, exhaustive over legal step actions)
    -> scoring/ LLM SubagentScorer + AnswerRubricScorer (G-Eval logprob)
    -> attribution/ recovery-gain / credit-based label assignment
    -> repair/ ECS + RepairExecutor / RepairOrchestrator + failure_memory
    -> eval/ provenance, writers, metrics, gates

cmd_audit/adapters/
  -> mem0.py / letta.py recorded-trace adapters, stale.py STALE item-layer adapter
```

`replays/` is a legacy/internal offline-baseline shim for *attribution*: the live attribution path is `item_gate/` (Tier 2) + `counterfactual/` operator specs (Tier 3), and formation oracle, reasoning, route, and portfolio replays are not paper-facing live modules. It is **not fully detached** — `repair/post_repair.py` imports `ReplayResult` and `core.labels.REPLAY_TO_LABEL`, and `draft_ecs` (on the live `_run_full` path) uses them, falling back to `_gold_free_runtime_evidence_block` when no replay matches. Removing `replays/` requires cutting that dependency first.

Probe datasets (`data/probe_cases/`, built by `experiments/build_probe_cases.py`): `real_multihop_cases.json` (4-action single-fault chains, C4/C5), `real_recurrent_cases.json` (same-chain query families with fixed label across paraphrased variants — the recurrence structure C7 self-evolution needs), `real_three_source_cases.json` (cross-source, item labels retained), `real_coupled_failure_boundary_cases.json` (C6), `real_item_layer_cases.json` + `real_item_poisoned_hitl_cases.json` (STALE item-layer), `real_longmemeval_cases.json` / `real_memoryarena_cases.json` / `real_toolbench_cases.json` (external benchmark sources), `coupled_failure_inspected_subset.json` (inspected C6 subset).

| Subpackage / Module | Role |
|---------------------|------|
| `core/models.py` | `ProbeCase`, `MemoryItem`, `GoldEvidence`, `BaselineOutput` dataclasses |
| `core/labels.py` | `PIPELINE_LABEL_ORDER` (4 live step actions), `ITEM_LABELS` (5), validators; `REPLAY_TO_LABEL` is legacy/offline replay mapping only. |
| `core/llm_client.py` | Provider-agnostic LLM API client (`generate(prompt, *, system=None) -> str`) |
| `data_io/` | `load_probe_cases`, `load_all_real_cases`, `load_real_cases_by_source` |
| `replays/` | Legacy/internal offline-baseline shim; not exported from top-level `cmd_audit`, but still imported by `repair/post_repair.py` (`ReplayResult`, `REPLAY_TO_LABEL`) on the live `draft_ecs` path. |
| `attribution/` | Legacy/offline replay ranking helpers; live attribution uses `counterfactual/` operator search. |
| `scoring/phrase.py` | `answer_score`, `evidence_recall_from_text` (phrase-matching fallback) |
| `scoring/llm.py` | `SubagentScorer`, `EvidenceVerifier`, `AnswerVerifier`; `AnswerRubricScorer` (continuous answer-axis G-Eval) |
| `scoring/retrieval.py` | BM25 deterministic retrieval, `RetrievalMetrics`, evidence boundary enforcement |
| `harness.py` | 3 public entry points: `run_case`, `run_cases`, `run_real_suite`; kwargs control hook/repair/post_repair |
| `adapters/` | CMD-Skill Adapter package: `base.py`, `harness.py`, `mem0.py` (2 cut points), `letta.py` (3 cut points), `stale.py` (STALE item-layer scenarios: `M_old` stale item vs `M_new` current item + gold) |
| `hook/` | Two-branch confidence gate (live at `harness.py:690`, **0 LLM calls** — token overlap, Shannon entropy, negation-based conflict, logistic): `post_retrieve_hook.py`, `constants.py`. Schema declares 6 factors; `memory_recency_min` / `memory_recency_spread` are hardcoded `0.0` (`RetrievedItem` carries no timestamp), so 4 are live. |
| `item_gate/` | Tier 2 item gate (live in `harness.py`): `divergence.py`, `collision.py`, `loo.py`, `gate.py` |
| `counterfactual/` | Tier 3 single-point scan: `actions.py` (`SINGLE_POINT_DEPTH = 1`), `rollout.py`, `context.py`, `search.py` (`SinglePointAttributor`), `operators.py` (composable operator specs); tree.py/value.py/distill.py deleted per C6. Cost model: `_step_context` = 1 call, `rollout_to_terminal` = 2 (1 generate + 1 judge), so a scan is `3 + 3A` calls for `A` legal non-identity actions (A = 3, or 4 when any item has safety metadata). |
| `repair/post_repair.py` | `ECSDraft`, `RepairedContext`, `PostRepairResult`; `draft_ecs`, `run_post_repair_context_replay` |
| `repair/executor.py` | `RepairExecutor`, `RepairExecutorResult`; single-repair execution |
| `repair/orchestrator.py` | Iterative repair loop over `close_deltas` |
| `repair/actions.py` | `RepairAction`, `TargetedRepairAction`, action_type taxonomy, tool schema |
| `repair/ecs.py` | ECS draft dataclass and validation |
| `repair/efficacy.py` | Gold-free repair execution core (diagnosis -> repaired context -> rollout -> recovery gain) |
| `repair/failure_memory.py` | `FailureMemoryStore`, content-fingerprint cluster retrieval key (paraphrase-invariant; replaced query-word key per C7), recurrence comparison |
| `baselines/` | Comparator subpackage: evidence-recall, subagent judge, random label, llm_judge, memory-probe grid |
| `eval/metrics.py` | Internal diagnostic label metrics only; macro-F1 is not a live/release headline. |
| `eval/writers.py` | Shared CSV/text writers (`write_attribution_table`, recovery metrics, step-level metrics, provenance; confusion writer retained for internal diagnostics). |
| `eval/provenance.py` | `ProvenanceTracker`, HMAC tamper detection, `get_graph_distractor_edges()` |
| `eval/surrogate_gap.py` | Surrogate-vs-gold recovery-gain measurement |
| `eval/release_gates.py` | `GateResult`, `GateReview`, phase gate checks |
| `cli.py` | `argparse` CLI (`cmd-audit run`) |
| `__init__.py` | Public exports (paper-facing surface) |

### Test Files

Tests are organized by subpackage under `tests/`:

| Directory | Contents |
|-----------|----------|
| `tests/integration/` | Harness-level smoke, comparators, attribution table, leak-safe monitor contract |
| `tests/repair/` | Post-repair, targeted repairs, failure memory, executor, orchestrator, surrogate |
| `tests/eval/` | Phase gates, provenance, agreement, bootstrap, at-scale + experiment eval |
| `tests/scoring/` | Retrieval baselines, subagent scoring, rubric scoring, dual-axis recovery gain |
| `tests/attribution/` | Empty (legacy label/coupled-failure/shadow-replay tests removed) |
| `tests/hook/` | Two-branch confidence gate |
| `tests/item_gate/` | Tier 2 item gate: collision, LOO, divergence, cost ladder |
| `tests/counterfactual/` | Tier 3 step-level single-point scan: actions, rollout, attribution (tree/value/distill tests deleted per C6) |
| `tests/adapters/` | mem0 adapter, Letta adapter |
| `tests/data_io/` | Real data integration |
| `tests/experiments/` | Experiment config tests (Exp22 random control, Exp23 item headroom) |

## Domain Rules (coding boundaries)

Full domain language and taxonomy live in `CONTEXT.md`.

## Editing Rules

- Preserve existing research notes unless explicitly asked to rewrite.
- When adding knowledge, update relevant `knowledge/` page and add `logs/YYYY-MM-DD.md` note.
- Keep `knowledge/topic-cmd-memory-failure.md` compact and information-dense.
- New reference notes format: arXiv ID (or Zenodo/GitHub identifier), core contribution, key concepts, CMD relevance, open gap. One line each.
- When metabolism produces new signals, update `knowledge/topic-cmd-memory-failure.md` with dated signal table and `knowledge/current-memory.md` with incremental conclusions.

## Output Artifacts

Primary artifacts:

- `artifacts/attribution_table*.csv` — per-case predicted label, per-hop credit, recovery gains, comparator outputs.
- `artifacts/comparison_metrics*.csv` — operator recovery metrics (`positive_recovery_rate`, `mean_recovery_gain`), cost, and provenance completeness.
- `artifacts/attribution_confusion_matrix*.csv` — legacy/internal diagnostic confusion matrix; not produced by live/release runs by default.
- `artifacts/sandbox/post_repair_table*.csv` — Post-Repair Context Replay assessment distribution.
- `artifacts/sandbox/repair_success_table*.csv` — targeted repair outcomes.
- `artifacts/sandbox/recurrence_*.csv|txt` — Failure Memory recurrence summaries.

Artifacts written under phrase-match scoring are mechanics-validation snapshots; paper-grade numbers come from the LLM scoring stack.

## Project Agent Skills

### Domain docs

Single-context: `CONTEXT.md` at root (authority on target design), `DISCUSSION.md` for converged design decisions, `cmd_innovation_core/plans/cmd_open_decisions.md` for the decision log, `knowledge/current-memory.md` for compressed active memory, and `knowledge/_index.md` for retrieval entry points.
