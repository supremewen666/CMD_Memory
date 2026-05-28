# CLAUDE.md

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

CMD frames agent-memory failure diagnosis as counterfactual attribution:

```text
failed memory task
  -> baseline memory answer + evidence score
  -> 10 counterfactual memory-operation replays
  -> recovery gains scored by phrase fallback or LLM SubagentScorer
  -> operation-level attribution + close_deltas
  -> ECS draft
  -> RepairExecutor / RepairOrchestrator
  -> Post-Repair Context Replay
  -> Failure Memory for future similar failures
```

The first milestone is a standalone **CMD-Audit** harness that produces attribution and repair-validation evidence. V0, V1, and V2 together constitute a single paper, with V2 as the final module/skill. After Decision 34 (2026-05-23), paper headline claims bind to a 130-case researcher-adjudicated set; the 596-case suite is a scale sanity check, the hook is supplementary, and CMD vs Rewind head-to-head is dropped in favor of layered positioning.

## Required Reading

Before changing plans or code, read:

1. `cmd_innovation_core/README.md`
2. `CONTEXT.md` — domain language, boundaries, label taxonomy
3. `knowledge/current-memory.md` — compressed active memory
4. `cmd_innovation_core/issues/README.md`
5. `cmd_innovation_core/gates/V1V2_gate_status.md`
6. `TASK.md`

## Commands

Python >= 3.11, zero external PyPI dependencies. Tests use `unittest` via `pytest`.

```bash
# Run all tests
python -m pytest tests/ -v

# Run a single test file
python -m pytest tests/test_cmd_audit_issue11_v1_labels.py -v

# Run a single test method
python -m pytest tests/test_cmd_audit_issue11_v1_labels.py::V1LabelValidationTest::test_v1_labels_are_superset_of_v0 -v

# Run the V0 CMD-Audit CLI harness
python -m cmd_audit run --cases data/probe_cases/v0_issue3_cases.json
```

## Code Architecture

`cmd_audit/` is a standalone research harness with no external PyPI dependencies; `cmd_audit/baselines/` is its comparator subpackage. Modules and data flow:

```
data/probe_cases/*.json
  -> models.py loaders
  -> harness.py run_case* / run_case_full_v1
    -> cmd_audit.baselines comparators and memory-probe baseline
    -> replays.py V0/V1 replay portfolio
    -> scoring.py phrase fallback or llm_scoring.py semantic scorer
    -> attribution.py recovery-gain label assignment
    -> post_repair.py ECS + validation
    -> repair_executor.py / repair_orchestrator.py targeted repair loop
    -> failure_memory.py recurrence store
    -> provenance.py lineage tracking
    -> writers.py CSV/text artifacts

cmd_audit/adapters/
  -> mem0.py / letta.py recorded-trace adapters

cmd_audit/hook/
  -> post_retrieve_hook.py + rpe_judge.py + constants.py
  -> supplementary replay selection and cost-reduction analysis
```

| Module | Role |
|--------|------|
| `models.py` | `ProbeCase`, `MemoryItem` dataclasses; `load_probe_cases` / `load_probe_cases_v1` loaders |
| `labels.py` | Label registries (`V0_PIPELINE_LABELS`, `V1_PIPELINE_LABELS`, `V1_REPLAY_TO_LABEL`), `validate_v0_label` / `validate_v1_label` boundary, `DEFERRED_PIPELINE_LABELS` |
| `replays.py` | 6 V0 replays + `oracle_route` (V1); `run_v0_replay_portfolio` / `run_v1_replay_portfolio` |
| `attribution.py` | `assign_attribution` (V0) / `assign_attribution_v1` (V1) — ranks replays by recovery gain, handles `has_ingestion_trace` split |
| `harness.py` | Public entry points: `run_case` / `run_case_v1`, `run_cases` / `run_cases_v1`, `run_case_full` / `run_case_full_v1`; also `run_case_with_mem0`/`run_case_with_letta` via adapters harness |
| `adapters/` | CMD-Skill Adapter package: `base.py` (trace types, loaders), `harness.py` (adapter run helpers), `mem0.py` (Mem0Adapter + `run_mem0_replay_portfolio`, two cut points), `letta.py` (LettaAdapter + `run_letta_replay_portfolio`, three cut points) |
| `hook/` | Current hook package: `post_retrieve_hook.py`, `rpe_judge.py`, `constants.py`; two-stage empty-context + RPE Judge top-k selector for supplementary hook analysis |
| `post_repair.py` | `ECSDraft`, `RepairedContext`, `PostRepairResult`; `draft_ecs`, `run_post_repair_context_replay`, sandbox path validation |
| `repair_executor.py` | `RepairExecutor`, `RepairExecutorResult`, and single-repair execution through adapter-supported actions |
| `repair_orchestrator.py` | Iterative repair loop over `close_deltas`, stopping on recovered or exhausted candidates |
| `repairs.py` | `TargetedRepairAction`, legacy repair mapping, repair comparison helpers |
| `failure_memory.py` | `FailureMemoryStore`, upgraded composite retrieval key, recurrence comparison |
| `cmd_audit/baselines/` | Comparator subpackage: evidence-recall heuristic, subagent judge, random label, llm_judge, memory-probe grid |
| `retrieval_baselines.py` | BM25 deterministic retrieval, `RetrievalMetrics`, evidence boundary enforcement |
| `scoring.py` | `answer_score`, `evidence_recall_from_text` (phrase-matching); preserved as default/fallback; superseded by `llm_scoring.py` (issue 0019, Decision A) |
| `llm_client.py` | Provider-agnostic LLM API client (`generate(prompt, *, system=None) -> str`) |
| `llm_scoring.py` | `SubagentScorer`, `EvidenceVerifier`, `AnswerVerifier`; binary atomic subagent scoring replacing phrase-matching in the LLM eval path |
| `metrics.py` | `DiagnosisPrediction`, `DiagnosisMetrics`, `compute_diagnosis_metrics` (macro F1) |
| `writers.py` | Shared CSV/text writers (`write_attribution_table`, `write_confusion_matrix_table`, etc.) |
| `provenance.py` | `ProvenanceTracker`, `ProvenanceEdge`/`Citation` dataclasses, HMAC tamper detection, `get_graph_distractor_edges()` |
| `surrogate_gap.py` | Surrogate-vs-gold recovery-gain measurement for gold-dependent labels |
| `version_gates.py` | `GateResult`, `GateReview`, `check_v0_to_v1_gate`, `check_v1_to_v2_gate` (now with `letta_integrated` param) |
| `cli.py` | `argparse` CLI (`cmd-audit run`) |
| `__init__.py` | All public exports (~200 symbols) |

### Test Files

Tests follow the pattern `tests/test_cmd_audit_issueNN_*.py`, one file per issue:
- `test_cmd_audit_tracer_bullet.py` — issues 0001-0004 (V0 smoke)
- `test_cmd_audit_issue5_post_repair.py` — issue 0005
- `test_cmd_audit_issue6_targeted_fixes.py` — issue 0006
- `test_cmd_audit_issue7_failure_memory.py` — issue 0007
- `test_cmd_audit_issue8_retrieval_baselines.py` — issue 0008
- `test_cmd_audit_issue9_monitor_contract.py` — issue 0009
- `test_cmd_audit_issue10_version_gates.py` — issue 0010
- `test_cmd_audit_issue11_v1_labels.py` — issue 0011 (44 tests, 9 classes)
- `test_cmd_audit_issue12_v1_labels.py` — issue 0012 (81 tests)
- `test_cmd_audit_issue13_coupled_failure.py` — issue 0013 (42 tests)
- `test_cmd_audit_issue14_mem0_adapter.py` — issue 0014 (30 tests)
- `test_cmd_audit_issue15_letta_adapter.py` — issue 0015 (44 tests, 7 classes)
- `test_cmd_audit_issue17_provenance.py` — issue 0017 (78 tests, 12 classes)
- `test_cmd_audit_issue19_subagent_scoring.py` — issue 0019 (planned, ~48 tests, ~8 classes)

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

- `artifacts/attribution_table*.csv` — per-case predicted label, top-2, recovery gains, comparator outputs.
- `artifacts/attribution_confusion_matrix*.csv` — label confusion matrix for smoke, per-source, or real-data runs.
- `artifacts/comparison_metrics*.csv` — CMD vs evidence-recall / subagent_judge / llm_judge / random baselines.
- `artifacts/sandbox/post_repair_table*.csv` — Post-Repair Context Replay assessment distribution.
- `artifacts/sandbox/repair_success_table*.csv` — targeted repair outcomes.
- `artifacts/sandbox/recurrence_*.csv|txt` — Failure Memory recurrence summaries.
- `data/probe_cases/researcher_labeled_subset.json` — Decision 34 headline adjudication stub.
- `data/probe_cases/experiment_01_inspected_ecs.json` — Experiment 1 ECS inspection stub.

Decision 34 caveat: existing 596-case Macro F1 artifacts are mechanics-validation snapshots until the LLM re-test and researcher adjudication land.

## Project Agent Skills

### Issue tracker

Local markdown files in `cmd_innovation_core/issues/`. The overview index is `cmd_innovation_core/issues/README.md`.

### Triage labels

Default five-role vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`), stored as `**Status**:` in issue frontmatter when present.

### Domain docs

Single-context: `CONTEXT.md` at root, `cmd_innovation_core/plans/cmd_open_decisions.md` for decisions, `knowledge/current-memory.md` for compressed active memory, and `knowledge/_index.md` for retrieval entry points.
