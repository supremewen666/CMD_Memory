---
id: 0022
title: Decision 34 wiring sprint — LLM eval infrastructure
status: done
labels: [paper, decision-34, code, wiring]
blocks: [0023, 0026, 0028, 0031, 0032]
blocked_by: []
created: 2026-05-24
completed: 2026-05-25
---

# 0022 — Decision 34 wiring sprint

## Completion note (2026-05-25)

LLM eval wiring is implemented and covered by Decision 34 tests: replay shortcut
warning/scorer-only behavior, label-stripped replay context, Post-Repair
`agent_generate` + `AnswerVerifier`, on-the-fly baseline rescore, bootstrap
helper, tie-margin V1 defaults, and D34 cost-schema checks.

## Why

`replays.py:477` short-circuits the agent loop with `answer = case.gold_answer if evidence_score == 1.0 else ""`, and `post_repair.py:227-230` substring-matches the gold answer against the combined repaired context. Under those two paths every paper number reduces to "did the gold phrase end up where it was supposed to?" — mechanical, not diagnostic. Decision 34 R1+R2 require the agent to actually answer, scored by an evaluator independent of the agent.

This issue is the umbrella for the wiring edits that make Experiment 2 + the at-scale re-test (issue 0023) + per-source artifact regeneration (issue 0031) producible. No paper number can be cited until 0022 lands.

## Acceptance criteria

| AC | Required behavior | Verified by |
|----|-------------------|-------------|
| AC1 | `replays.py:_score_recovered_evidence` phrase-match shortcut runs only when `agent_generate is None and scorer is None`; emits `PhraseMatchShortcutWarning` once per process. When `scorer` is provided but `agent_generate` is None, score `evidence_block` directly (no fake answer). | new test `test_cmd_audit_decision34_phrase_match_warning.py` |
| AC2 | `replays.py:_build_replay_agent_context` no longer emits `CMD ATTRIBUTION LABEL` line. Output structure is `BASELINE CONTEXT + COUNTERFACTUAL EVIDENCE BLOCK` only. `replay_name` arg retained for signature stability. `V1_REPLAY_TO_LABEL` import removed if no other use. | rewritten `test_cmd_audit_repair_action_json_and_agent_loop.py:155-174` (asserts label string is **absent**, not present); new test `test_cmd_audit_decision34_replay_context_no_label.py` |
| AC3 | `post_repair.py:run_post_repair_context_replay` accepts kw-only `agent_generate`, `evidence_scorer`, `answer_verifier`, `partial_threshold=0.5`. With `agent_generate` provided, it runs `agent_generate(case.query, combined)`, scores the **answer** (not the context), uses `answer_verifier(answer, gold_answer) == "EQUIVALENT"` for `recovered`, applies threshold for `partial`. Without it, falls back to legacy substring path emitting a deprecation warning. | new test `test_cmd_audit_decision34_post_repair_agent.py` |
| AC4 | `harness.py:run_case_full_v1` (and `run_cases_v1`, `run_full_real_suite`) thread `agent_generate / scorer / answer_verifier` to both `run_v1_replay_portfolio` and `run_post_repair_context_replay`. New flag `on_the_fly_baseline_rescore: bool = False` — when `True` and `agent_generate + evidence_scorer` are both provided, rescore `vector_memory` baseline per case via `agent_generate(case.query, baseline.injected_context)` + `evidence_scorer(case.gold_evidence, baseline_answer)` before replays; emit `baseline_evidence_score_llm` to artifact CSV; use that value for `recovery_gain` denominator in place of pre-baked `baseline.evidence_score`. | new test `test_cmd_audit_decision34_baseline_rescore.py` |
| AC5 | `cmd_audit/__init__.py` exports `PhraseMatchShortcutWarning(DeprecationWarning)` (single category for both shortcut paths in replays.py and post_repair.py). | import test |
| AC6 | New `tests/conftest.py` filters `PhraseMatchShortcutWarning` so legacy V0/V1 tests run silently. | `pytest tests/ -v` produces no warning lines for legacy fixtures |
| AC7 | All 803 existing tests still pass under default pytest. | full suite green |
| AC8 | Gap 3 bootstrap helper lands in `cmd_audit/bootstrap.py`: `bootstrap_metric(case_ids, score_fn, n_iters=1000) -> (mean, ci_low, ci_high)`. Case-level resampling uses a fixed seed for reproducible paper tables. | `test_cmd_audit_decision34_bootstrap.py` |
| AC9 | Gap 4 tie-margin default moves to the V1 entry points: `run_case_v1`, `run_case_full_v1`, and `run_case_v1_with_hook` default `tie_margin=0.0`. Lower-level `assign_attribution_v1` stays backward compatible. Legacy V0 fixture tests explicitly pass `tie_margin=0.05`. | `test_cmd_audit_decision34_tie_margin_defaults.py` + V0 smoke tests |
| AC10 | D34 cost schema is enforced in builders: no hardcoded pseudo-costs (`10 replays = 10.0`) may appear in paper-facing artifacts. Cost metadata must use token / wallclock / USD fields or explicit `missing_cost_metadata`. | `test_cmd_audit_decision34_experiment2_tables.py` |

## Out of scope

- Calibration of `partial_threshold` τ — uses default 0.5, calibrated post-hoc in 0026/0029.
- Calibration of `tie_margin` for coupled-failure — issue 0029.
- Adapter parity at LLM stack — issue 0032.
- Hook bypass mode — already supported via `use_hook=False` in `run_full_real_suite`. No new code.

## Files affected

| File | Edit type | Spec section in REPAIR.md |
|------|-----------|---------------------------|
| `cmd_audit/replays.py` | Edit `_score_recovered_evidence`, edit `_build_replay_agent_context`, define warning category usage | §11A, §11B |
| `cmd_audit/post_repair.py` | Edit `run_post_repair_context_replay` signature + behavior; legacy substring path retained behind warning | §11C |
| `cmd_audit/harness.py` | Pass-through new kwargs in 3 entry points; on-the-fly baseline rescore; V1 entry point `tie_margin=0.0` defaults | §11D |
| `cmd_audit/bootstrap.py` | New case-level bootstrap CI helper for paper metrics | Gap 3 |
| `cmd_audit/__init__.py` | Export `PhraseMatchShortcutWarning` | §11A AC5 |
| `tests/conftest.py` | New file; filter custom warning category | §11A 14.3, §17 |
| `tests/test_cmd_audit_repair_action_json_and_agent_loop.py` | Rewrite assertions at lines 155-174 to test leak-free invariant | §11B 14.1 |
| `tests/test_cmd_audit_decision34_*.py` | 4 new test files: phrase_match_warning, replay_context_no_label, post_repair_agent, baseline_rescore | §11F |

## Dependency

- Blocks 0023 (at-scale re-test cannot run until wiring lands).
- Blocks 0026 (Experiment 2 headline depends on agent_generate path).
- Blocks 0028 (hook calibration consumes re-test outputs from 0023, which depends on 0022).
- Blocks 0031 (artifact regeneration uses the new code paths).
- Blocks 0032 (adapter parity test exercises the new pass-through).

## Estimate

3 working days. Wiring is mechanical; the bulk is the 12-15 new tests.

## Detail map

`REPAIR.md` §11 (entire), §13, §14 in the repo root.
