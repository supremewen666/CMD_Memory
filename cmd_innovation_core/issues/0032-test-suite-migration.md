---
id: 0032
title: Test suite migration — conftest, label-leak invariant, adapter parity at LLM stack
status: done
labels: [paper, decision-34, tests, hygiene]
blocks: [0023]
blocked_by: [0022]
created: 2026-05-24
completed: 2026-05-25
---

# 0032 — Test suite migration

## Completion note (2026-05-25)

Test migration is implemented: project-wide warning filter plus explicit
`legacy_phrase_match_path` fixture, leak-free replay-context invariant, and
Mem0/Letta adapter parity tests under the stubbed LLM stack.

## Why

Decision 34's wiring (issue 0022) introduces three test-side concerns that 0022 itself does not fully cover:

1. The `PhraseMatchShortcutWarning` will fire across ~80% of the existing 803-test suite when the legacy phrase-match path runs. Default pytest swallows warnings, but stdout pollution makes test runs hard to read; a project-wide conftest filter is the clean fix.
2. `tests/test_cmd_audit_repair_action_json_and_agent_loop.py:171-173` directly asserts `"CMD ATTRIBUTION LABEL"` appears in the replay agent context. After §11B drops the label string, those assertions fail. The fix is not to remove the test — it's to **invert** the assertion so it enforces a leak-free invariant ("label string MUST NOT appear"), turning the test into regression-detection for any future code that re-introduces label leakage.
3. The mem0 + Letta adapter parity tests (`test_cmd_audit_issue14_mem0_adapter.py`, `test_cmd_audit_issue15_letta_adapter.py`) currently run under `agent_generate=None` (phrase-match path). After Decision 34, parity must hold under the LLM stack too; otherwise the V1V2 gate's parity claim is only a phrase-match-shortcut artifact.

This issue covers all three. Net result: zero existing tests deleted, 1 test rewritten, ~5 net-new tests added on top of issue 0022's 12-15.

## Acceptance criteria

| AC | Required behavior | Verified by |
|----|-------------------|-------------|
| AC1 | `tests/conftest.py` created. Filters `cmd_audit.PhraseMatchShortcutWarning` to `ignore` for the entire suite. Filter is module-level pytest fixture or `pytest_collection_modifyitems` hook — minimal footprint. | `pytest tests/ -v` produces no `PhraseMatchShortcutWarning` lines |
| AC2 | `tests/test_cmd_audit_repair_action_json_and_agent_loop.py:171-173` assertions rewritten. New assertions: (a) `"BASELINE CONTEXT"` is in `calls[0][1]`, (b) `"COUNTERFACTUAL EVIDENCE BLOCK"` is in `calls[0][1]`, (c) `"CMD ATTRIBUTION LABEL"` is **NOT** in `calls[0][1]`, (d) the V1 label name (`"retrieval_error"` for the test fixture) is **NOT** in `calls[0][1]`. The test name should reflect the new invariant: rename to `test_replay_context_leak_free_invariant` or similar. | grep + test passes |
| AC3 | New test file `tests/test_cmd_audit_decision34_adapter_parity_llm.py` covering: (a) Mem0Adapter run with stub `agent_generate` (deterministic stub returning a known answer per replay) + stub `scorer` produces same labels as standalone harness on a 6-label V0 smoke fixture; (b) LettaAdapter same property; (c) cross-adapter non-regression (mem0 result unchanged when Letta also runs in same process). 3 test methods minimum. | tests run green |
| AC4 | `cmd_audit/PhraseMatchShortcutWarning` exported via `cmd_audit/__init__.py` (already in 0022 AC5; this issue depends on it). | import test |
| AC5 | All 803 existing tests continue to pass. Net new tests from 0022 + 0032 add to ~820+. | full suite green |
| AC6 | The conftest also includes a fixture `legacy_phrase_match_path` that explicitly enables the warning (overriding the project filter) for tests that want to assert the warning fires. Used by 0022's `test_cmd_audit_decision34_phrase_match_warning.py`. | fixture works |

## Test rewrites and additions in detail

**Rewrite (test_cmd_audit_repair_action_json_and_agent_loop.py:155-174)**:

```python
# OLD (lines 171-173)
self.assertIn("CMD ATTRIBUTION LABEL", calls[0][1])
self.assertIn("COUNTERFACTUAL EVIDENCE BLOCK", calls[0][1])
self.assertIn("retrieval_error", retrieval.answer)

# NEW (Decision 34 R1 point 5: leak-free invariant)
self.assertIn("BASELINE CONTEXT", calls[0][1])
self.assertIn("COUNTERFACTUAL EVIDENCE BLOCK", calls[0][1])
self.assertNotIn("CMD ATTRIBUTION LABEL", calls[0][1])  # leak prevention
for label_name in V1_PIPELINE_LABELS:                   # leak prevention
    self.assertNotIn(label_name, calls[0][1])
# Drop "retrieval_error in retrieval.answer" assertion entirely:
# the agent_generate stub returns context verbatim, but with label string
# removed there's no expectation that label name appears in the answer.
```

**New file `test_cmd_audit_decision34_adapter_parity_llm.py`**:

```python
class AdapterParityUnderLLMStack(unittest.TestCase):
    def setUp(self):
        # Fixed stub: returns a known answer per replay name; deterministic.
        self.stub_answers = {
            "oracle_write": "stub_answer_with_lisbon",
            "oracle_compression": "stub_answer_with_lisbon",
            ...
        }
        self.stub_scorer = lambda gold_evidence, text: 1.0 if "lisbon" in text.lower() else 0.0
    
    def test_mem0_adapter_label_parity_under_llm_stack(self):
        # Run standalone + Mem0Adapter on same case with same stubs.
        # Assert predicted_label matches.
    
    def test_letta_adapter_label_parity_under_llm_stack(self):
        # Same for Letta.
    
    def test_cross_adapter_non_regression_under_llm_stack(self):
        # Run mem0 alone, then mem0 + letta in same process; mem0 result identical.
```

## Files affected

| File | Edit type |
|------|-----------|
| `tests/conftest.py` | new |
| `tests/test_cmd_audit_repair_action_json_and_agent_loop.py` | edit lines 171-173 |
| `tests/test_cmd_audit_decision34_adapter_parity_llm.py` | new |

## Out of scope

- Removing legacy phrase-match tests — they remain as the legacy lower bound.
- Refactoring existing tests to use the new agent_generate path — only forced when a test breaks.
- Replacing `unittest` with `pytest`-native style — codebase uses `unittest` per CLAUDE.md.

## Estimate

Half a day.

## Dependency

- Blocked by 0022 (PhraseMatchShortcutWarning class must exist).
- Blocks 0023 (clean test suite is a precondition for the at-scale re-test sign-off).

## Detail map

`REPAIR.md` §11A AC5 (warning category), §11F (existing 4 new test specs from 0022), Q14 grilling notes (this issue's three concerns: conftest, label-leak rewrite, adapter parity at LLM stack).
