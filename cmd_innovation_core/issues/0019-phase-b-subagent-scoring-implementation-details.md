# Issue 0019 Phase B: Subagent-based LLM Scoring — Implementation Details

**Status:** Complete (2026-05-21)
**Tests:** 39 tests, 8 classes
**Modules:** 1 new (`llm_scoring.py`), 2 modified (`llm_client.py`, `replays.py`)

## Architecture Decisions

1. **No `subagent_runner.py`** — context isolation via `EvidenceVerifier` prompt construction + `_validate_context_isolation`. No process boundary needed.
2. **No `hooks.py`** — validation functions are private inside `llm_scoring.py`, enforced internally by `EvidenceVerifier`/`SubagentScorer`.
3. **Per-fact atomicity** — each subagent call = one (FACT, TEXT) pair. Replay-internal parallelism via `ThreadPoolExecutor`.
4. **Cascading degrade** — `llm_client=None` → phrase fallback. `OutputFormatError` → retry once → ABSENT. Parallel crash → phrase fallback.
5. **`generate(prompt, *, system=None)`** — optional system message prepended. Backward-compatible.
6. **Scorer contract** — `(gold_evidence, text) -> float`. `SubagentScorer.__call__` implements. `_score_recovered_evidence` changed by one line.

## Module Map

### `cmd_audit/llm_scoring.py` (306 lines)

| Symbol | Type | Description |
|--------|------|-------------|
| `ContextLeakError` | Exception | Forbidden tokens in prompt |
| `OutputFormatError` | Exception | Non-binary subagent output |
| `EvidenceVerifier` | class | `verify(fact, text) -> "PRESENT" \| "ABSENT"` |
| `AnswerVerifier` | class | `verify(answer, gold_answer) -> "EQUIVALENT" \| "NOT_EQUIVALENT"` |
| `SubagentScorer` | class | `__call__(gold_evidence, text) -> float`, `score_evidence(...) -> float` |
| `_validate_context_isolation` | function | Casefolded forbidden-token check before LLM call |
| `_validate_output_format` | function | Normalises binary output; raises `OutputFormatError` |
| `_phrase_fallback` | function | Deterministic phrase-matching (same logic as `evidence_recall_from_text`) |

### `cmd_audit/llm_client.py` — Changes

`LLMClient.generate(prompt, *, system=None)` — added optional `system` kwarg. Prepends `{"role":"system","content":system}` to messages array when provided.

### `cmd_audit/replays.py` — Changes

`_score_recovered_evidence(case, replay_name, evidence_block, tracker=None, *, scorer=None)` — added `scorer` keyword parameter. When provided, `evidence_score = scorer(case.gold_evidence, evidence_block)`. Default `None` = existing phrase-matching.

Added imports: `Callable`, `GoldEvidence`.

### `cmd_audit/__init__.py` — New Exports

`AnswerVerifier`, `ContextLeakError`, `EvidenceVerifier`, `OutputFormatError`, `SubagentScorer`.

## Data Flow

```
SubagentScorer(gold_evidence, evidence_block)
  → llm_client is None? → _phrase_fallback()
  → for each evidence (parallel, ThreadPoolExecutor):
      EvidenceVerifier.verify(fact=ev.text, text=evidence_block)
        → _validate_context_isolation(user_message)
        → llm_client.generate(user_message, system=EVIDENCE_SYSTEM_PROMPT)
        → _validate_output_format(response)
          → OK → PRESENT|ABSENT
          → OutputFormatError → retry with firm prompt
            → OK → PRESENT|ABSENT
            → still fail → ABSENT
  → count(PRESENT) / total → float
  → ThreadPoolExecutor exception → fallback_scorer or _phrase_fallback
```

## Fallback Chain

| Condition | Action |
|-----------|--------|
| `llm_client is None` | `_phrase_fallback()` immediately |
| LLM unreachable | `verify()` catches, returns `"ABSENT"` |
| Output format invalid | Retry once with firm prompt |
| Retry still invalid | Conservative: `"ABSENT"` / `"NOT_EQUIVALENT"` |
| Parallel execution crash | `fallback_scorer` or `_phrase_fallback` |

## Context Isolation

`_FORBIDDEN_CONTEXT_TOKENS`: `case_id`, `gold_label`, `perturbation_type`, `perturbation_label`, `gold_answer`, `gold_evidence`, `ptype`, `cross_case`, `other_case`.

`_validate_context_isolation` runs casefolded substring check before every LLM call — inside `EvidenceVerifier.verify()` and `AnswerVerifier.verify()`. Cannot be bypassed externally.

## Test Coverage (39 tests, 8 classes)

| Class | Tests | Focus |
|-------|-------|-------|
| `ContextIsolationTest` | 11 | 10 forbidden tokens + error message content |
| `OutputFormatValidationTest` | 10 | PRESENT/ABSENT/EQUIVALENT/NOT_EQUIVALENT + edge cases |
| `EvidenceVerifierBoundaryTest` | 2 | gold_label/case_id token rejection in user messages |
| `EvidenceVerifierFallbackTest` | 2 | None client → ABSENT, empty fact |
| `AnswerVerifierFallbackTest` | 1 | None client → NOT_EQUIVALENT |
| `SubagentScorerFallbackTest` | 4 | Phrase fallback, empty evidence/text, `__call__` == `score_evidence` |
| `SubagentScorerContractTest` | 3 | Float return [0,1], exact text match, zero on mismatch |
| `SubagentScorerEdgeCaseTest` | 4 | Single/mixed evidence, max_workers config |
| `ExceptionHierarchyTest` | 2 | Both exceptions are ValueError subclasses |

## Integration

`_score_recovered_evidence` at `replays.py:326`:

```python
def _score_recovered_evidence(case, replay_name, evidence_block, tracker=None,
                               *, scorer=None):
    if scorer is not None:
        evidence_score = scorer(case.gold_evidence, evidence_block)
    else:
        evidence_score = evidence_recall_from_text(case.gold_evidence, evidence_block)
```

Harness-level wiring is now available on replay and V1 entry points:

```python
from cmd_audit import LLMClient, SubagentScorer
client = LLMClient()
scorer = SubagentScorer(client, max_workers=5)
result = run_case_v1(case, scorer=scorer, agent_generate=agent.generate)
```

When `agent_generate` is provided, each replay builds `baseline + label + evidence_block`, calls the real agent, and passes the agent answer to `scorer(gold_evidence, answer)`. When omitted, the deterministic offline path is preserved.

## Deferred

AnswerVerifier implemented but not wired into Post-Repair validation. Decision B attribution replay is wired; post-repair real agent validation remains separate.
