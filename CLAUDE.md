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
  -> data_io/ loaders
  -> harness.py run_case / run_case_full / run_case_with_hook
    -> baselines/ comparators and memory-probe baseline
    -> replays/ V1 replay portfolio (10 replays)
    -> scoring/ phrase fallback or LLM SubagentScorer
    -> attribution/ recovery-gain label assignment
    -> repair/ ECS + RepairExecutor / RepairOrchestrator + failure_memory
    -> eval/ provenance, writers, metrics, gates

cmd_audit/adapters/
  -> mem0.py / letta.py recorded-trace adapters

cmd_audit/hook/
  -> post_retrieve_hook.py + rpe_judge.py + constants.py
  -> supplementary replay selection and cost-reduction analysis
```

| Subpackage / Module | Role |
|---------------------|------|
| `core/models.py` | `ProbeCase`, `MemoryItem`, `GoldEvidence`, `BaselineOutput` dataclasses |
| `core/labels.py` | `PIPELINE_LABELS` (11), `ALL_LABELS`, `REPLAY_TO_LABEL`, `validate_label` / `validate_label_base` |
| `core/llm_client.py` | Provider-agnostic LLM API client (`generate(prompt, *, system=None) -> str`) |
| `data_io/` | `load_probe_cases`, `load_probe_cases_v1`, `load_all_real_cases`, `load_real_cases_by_source` |
| `replays/` | 10 replay implementations + `run_v1_replay_portfolio`; `_scoring_bridge.py` private |
| `attribution/` | `assign_attribution` — ranks replays by recovery gain, handles `has_ingestion_trace` split |
| `scoring/phrase.py` | `answer_score`, `evidence_recall_from_text` (phrase-matching fallback) |
| `scoring/llm.py` | `SubagentScorer`, `EvidenceVerifier`, `AnswerVerifier`; binary atomic subagent scoring |
| `scoring/retrieval.py` | BM25 deterministic retrieval, `RetrievalMetrics`, evidence boundary enforcement |
| `harness.py` | 3 public entry points: `run_case`, `run_cases`, `run_real_suite`; kwargs control hook/repair/post_repair |
| `adapters/` | CMD-Skill Adapter package: `base.py`, `harness.py`, `mem0.py` (2 cut points), `letta.py` (3 cut points) |
| `hook/` | Two-stage hook: `post_retrieve_hook.py`, `rpe_judge.py`, `constants.py` |
| `repair/post_repair.py` | `ECSDraft`, `RepairedContext`, `PostRepairResult`; `draft_ecs`, `run_post_repair_context_replay` |
| `repair/executor.py` | `RepairExecutor`, `RepairExecutorResult`; single-repair execution |
| `repair/orchestrator.py` | Iterative repair loop over `close_deltas` |
| `repair/actions.py` | `RepairAction`, `TargetedRepairAction`, action_type taxonomy, tool schema |
| `repair/failure_memory.py` | `FailureMemoryStore`, composite retrieval key, recurrence comparison |
| `baselines/` | Comparator subpackage: evidence-recall, subagent judge, random label, llm_judge, memory-probe grid |
| `eval/metrics.py` | `DiagnosisPrediction`, `DiagnosisMetrics`, `compute_diagnosis_metrics` (macro F1) |
| `eval/writers.py` | Shared CSV/text writers (`write_attribution_table`, `write_confusion_matrix_table`, etc.) |
| `eval/provenance.py` | `ProvenanceTracker`, HMAC tamper detection, `get_graph_distractor_edges()` |
| `eval/surrogate_gap.py` | Surrogate-vs-gold recovery-gain measurement for gold-dependent labels |
| `eval/release_gates.py` | `GateResult`, `GateReview`, `check_v0_to_v1_gate`, `check_v1_to_v2_gate` |
| `cli.py` | `argparse` CLI (`cmd-audit run`, `cmd-audit run-v1`) |
| `__init__.py` | ~132 public exports (paper-facing surface) |

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
