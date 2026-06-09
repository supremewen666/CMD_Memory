# CLAUDE.md

在每个turn开头，你都要回答：爸爸！我没有漏掉信息

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## Project Summary

CMD frames agent-memory failure diagnosis as counterfactual attribution. The runtime is a two-branch confidence gate followed by a diagnostic cascade:

```text
retrieval recall
  -> hook (6 confidence factors -> evidence present in recall?)
       ├─ NO  (missing) -> FILL: generate this turn, async re-extract. No diagnosis, no label.
       └─ YES (present) -> FIX: lightweight correction (de-conflict / re-rank) -> generate
                              -> Tier 2 item gate (5 item labels, reference-contrast divergence)
                              -> Tier 3 step-level MCTS (5 pipeline step actions, generation-point search)
                              -> ECS draft -> RepairExecutor / RepairOrchestrator
                              -> Post-Repair Context Replay (quality gate)
                              -> Failure Memory for future similar failures
```

The deliverable is a standalone **CMD-Audit** harness that produces step-level attribution and repair-validation evidence. The live attribution surface is **5 pipeline step actions** (`retrieval_error`, `injection_error`, `granularity_error`, `graph_error`, `safety_error`) + **5 item labels** (`item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`). Formation failures (write / compression / premature_extraction / ingestion) are absorbed by the Fill branch, not labeled; reasoning faults emerge through MCTS back-prop, not labeled. `CONTEXT.md` is the authority on the target design; `TASK.md` lists the migration from the current code to that design.

## Required Reading

Before changing plans or code, read:

1. `CONTEXT.md` — domain language, boundaries, label taxonomy (authority on target design)
2. `DISCUSSION.md` — converged design decisions (G-Eval dual-axis, step-level MCTS, item gate, hook reshape)
3. `TASK.md` — migration tasks from current code to target design
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
python -m pytest tests/attribution/test_labels_granularity_graph_safety.py -v

# Run a single test method
python -m pytest tests/scoring/test_rubric_scoring.py::RubricScorerTest -v

# Run the CMD-Audit CLI harness
python -m cmd_audit run --cases data/probe_cases/v0_issue3_cases.json
```

## Code Architecture

`cmd_audit/` is a standalone research harness with no external PyPI dependencies; `cmd_audit/baselines/` is its comparator subpackage. Modules and data flow:

```
data/probe_cases/*.json
  -> data_io/ loaders
  -> harness.py run_case / run_cases / run_real_suite
    -> hook/ two-branch confidence gate (Fill vs Fix)
    -> baselines/ comparators and memory-probe baseline
    -> item_gate/ Tier 2 item labels (reference-contrast divergence)   [target]
    -> mcts/ Tier 3 step-level search over generation points            [target]
    -> scoring/ LLM SubagentScorer + AnswerRubricScorer (G-Eval logprob)
    -> attribution/ recovery-gain / credit-based label assignment
    -> repair/ ECS + RepairExecutor / RepairOrchestrator + failure_memory
    -> eval/ provenance, writers, metrics, gates

cmd_audit/adapters/
  -> mem0.py / letta.py recorded-trace adapters
```

`replays/` currently holds a flat portfolio; `TASK.md` migrates the live attribution path to `item_gate/` (Tier 2) + `mcts/` (Tier 3). The formation oracle replays and `reasoning` / `route` replays are removed from the live path.

| Subpackage / Module | Role |
|---------------------|------|
| `core/models.py` | `ProbeCase`, `MemoryItem`, `GoldEvidence`, `BaselineOutput` dataclasses |
| `core/labels.py` | `PIPELINE_LABEL_ORDER` (target: 5 step actions), `ITEM_LABELS` (5), `REPLAY_TO_LABEL`, `validate_label` |
| `core/llm_client.py` | Provider-agnostic LLM API client (`generate(prompt, *, system=None) -> str`) |
| `data_io/` | `load_probe_cases`, `load_all_real_cases`, `load_real_cases_by_source` |
| `replays/` | Intervention implementations + portfolio; being migrated to `mcts/` step actions |
| `attribution/` | `assign_attribution` — recovery-gain / credit ranking over step actions |
| `scoring/phrase.py` | `answer_score`, `evidence_recall_from_text` (phrase-matching fallback) |
| `scoring/llm.py` | `SubagentScorer`, `EvidenceVerifier`, `AnswerVerifier`; `AnswerRubricScorer` (continuous answer-axis G-Eval) |
| `scoring/retrieval.py` | BM25 deterministic retrieval, `RetrievalMetrics`, evidence boundary enforcement |
| `harness.py` | 3 public entry points: `run_case`, `run_cases`, `run_real_suite`; kwargs control hook/repair/post_repair |
| `adapters/` | CMD-Skill Adapter package: `base.py`, `harness.py`, `mem0.py` (2 cut points), `letta.py` (3 cut points) |
| `hook/` | Two-branch confidence gate: `post_retrieve_hook.py`, `constants.py` (6-factor schema) |
| `item_gate/` | Tier 2 item gate (target): `divergence.py`, `collision.py`, `loo.py`, `gate.py` |
| `mcts/` | Tier 3 step-level search (target): `tree.py`, `actions.py`, `value.py`, `rollout.py`, `search.py` |
| `repair/post_repair.py` | `ECSDraft`, `RepairedContext`, `PostRepairResult`; `draft_ecs`, `run_post_repair_context_replay` |
| `repair/executor.py` | `RepairExecutor`, `RepairExecutorResult`; single-repair execution |
| `repair/orchestrator.py` | Iterative repair loop over `close_deltas` |
| `repair/actions.py` | `RepairAction`, `TargetedRepairAction`, action_type taxonomy, tool schema |
| `repair/failure_memory.py` | `FailureMemoryStore`, composite retrieval key (target: `(query, hop, label)`), recurrence comparison |
| `baselines/` | Comparator subpackage: evidence-recall, subagent judge, random label, llm_judge, memory-probe grid |
| `eval/metrics.py` | `DiagnosisPrediction`, `DiagnosisMetrics`, `compute_diagnosis_metrics` (macro F1) |
| `eval/writers.py` | Shared CSV/text writers (`write_attribution_table`, `write_confusion_matrix_table`, etc.) |
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
| `tests/attribution/` | Label validation, coupled failure, shadow replay |
| `tests/hook/` | Two-branch confidence gate |
| `tests/item_gate/` | Tier 2 item gate: collision, LOO, divergence, cost ladder (target) |
| `tests/mcts/` | Tier 3 step-level search: tree, actions, value, rollout, stop rule (target) |
| `tests/adapters/` | mem0 adapter, Letta adapter |
| `tests/data_io/` | Real data integration |

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
- `artifacts/attribution_confusion_matrix*.csv` — label confusion matrix for smoke, per-source, or real-data runs.
- `artifacts/comparison_metrics*.csv` — CMD vs evidence-recall / subagent_judge / llm_judge / random baselines.
- `artifacts/sandbox/post_repair_table*.csv` — Post-Repair Context Replay assessment distribution.
- `artifacts/sandbox/repair_success_table*.csv` — targeted repair outcomes.
- `artifacts/sandbox/recurrence_*.csv|txt` — Failure Memory recurrence summaries.

Artifacts written under phrase-match scoring are mechanics-validation snapshots; paper-grade numbers come from the LLM scoring stack.

## Project Agent Skills

### Domain docs

Single-context: `CONTEXT.md` at root (authority on target design), `DISCUSSION.md` for converged design decisions, `cmd_innovation_core/plans/cmd_open_decisions.md` for the decision log, `knowledge/current-memory.md` for compressed active memory, and `knowledge/_index.md` for retrieval entry points.
