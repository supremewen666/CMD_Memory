# Issue 0019 Phase A: LLM-as-Judge Baseline — Implementation Details

**Status:** Complete (2026-05-21)
**Tests:** 32 tests, 8 classes
**Modules:** 2 new (`llm_client.py`, `llm_judge.py`), 2 modified (`baselines.py`, `__init__.py`)

## Module Map

### `cmd_audit/llm_client.py` (118 lines)

Provider-agnostic LLM API client. Zero external dependencies — stdlib `urllib.request`.

| Symbol | Type | Description |
|--------|------|-------------|
| `LLMClientError` | Exception | Base exception for all LLM failures |
| `LLMUnavailableError` | Exception | Endpoint unreachable or transport error |
| `LLMTimeoutError` | Exception | Request timed out |
| `LLMResponseError` | Exception | Non-200 HTTP response |
| `LLMEmptyResponseError` | Exception | Response body contains no content |
| `LLMClientConfig` | frozen dataclass | `base_url`, `model`, `timeout_seconds`, `max_retries`, `temperature` |
| `LLMClient` | class | `generate(prompt) -> str` via OpenAI-compatible `/v1/chat/completions` |

Config defaults from env vars: `LLM_BASE_URL` (default `http://localhost:11434/v1`), `LLM_MODEL` (default `qwen2.5:7b`), `LLM_TIMEOUT` (default `60`). Temperature=0 for reproducibility.

### `cmd_audit/llm_judge.py` (120 lines)

| Symbol | Type | Description |
|--------|------|-------------|
| `LLMJudgeOutputError` | Exception | Raised when LLM response cannot be parsed |
| `build_judge_prompt(case)` | function | Constructs prompt from observable artifacts only → `str` |
| `parse_label_from_response(response)` | function | Extracts `(predicted_label, explanation)` → `tuple[str, str]` |

**Input boundary:** Only `case.query`, `case.raw_events`, `case.extracted_memory`, `case.primary_baseline`. Never: gold_label, ptype, gold_evidence, gold_answer.

### `cmd_audit/baselines.py` — Changes

| Location | Change |
|----------|--------|
| `BaselineSuiteResult` | New field: `llm_judge: ComparatorResult` after `random_label` |
| `comparator_results` property | Added `self.llm_judge` as 4th entry |
| `run_baseline_suite()` | New `llm_client=None` keyword parameter |
| New function | `run_llm_judge_baseline(case, baseline=None, *, llm_client=None)` |

**`run_llm_judge_baseline` fallback chain:**
```
llm_client=None        → fallback: evidence_recall_heuristic, name="llm_judge"
LLMJudgeOutputError    → fallback: heuristic + parse error annotation
Other Exception        → fallback: heuristic + error message
Success                → ComparatorResult(label, explanation, cost=0.5)
```

### `cmd_audit/__init__.py` — New Exports

10 symbols: `LLMClient`, `LLMClientConfig`, `LLMClientError`, `LLMEmptyResponseError`, `LLMResponseError`, `LLMTimeoutError`, `LLMUnavailableError`, `LLMJudgeOutputError`, `build_judge_prompt`, `parse_label_from_response`.

## Integration Wiring (Zero-Change)

Adding `llm_judge` to `comparator_results` property automatically flows into `harness.diagnosis_predictions()` → `comparison_metrics.csv` with zero changes to harness.py, metrics.py, or writers.py.

## Test Coverage (32 tests, 8 classes)

| Class | Tests | Coverage |
|-------|-------|----------|
| `LLMClientConfigTest` | 4 | Defaults, temperature=0, endpoint computation |
| `LLMClientErrorTest` | 3 | Exception hierarchy, catch-as-base, messages |
| `LLMPromptConstructionTest` | 6 | Query/events/memory/answer/labels in prompt, sections |
| `LLMPromptBoundaryTest` | 5 | No gold data leak (label/answer/evidence/case_id/ptype) |
| `LLMOutputParsingTest` | 5 | Valid parse, whitespace, invalid label, missing label, default explanation |
| `LLMJudgeFallbackTest` | 3 | None client → result, mentions unavailable, cost=0.5 |
| `LLMJudgeSuiteIntegrationTest` | 4 | Field exists, in comparator_results, 4 entries, no replay |
| `LLMJudgeIsolationTest` | 2 | Independent per-case, no cross-case prompt leak |

## Key Design Properties

1. **Provider-agnostic**: OpenAI-compatible endpoint — works with ollama, vllm, openai, any proxy.
2. **Graceful degradation**: `llm_client=None` → fallback to evidence_recall_heuristic with `comparator_name="llm_judge"`.
3. **No circular imports**: `llm_judge.py` imports only from `.labels` and `.models`. `baselines.py` lazy-imports from `llm_judge.py`.
4. **Backward-compatible**: `run_baseline_suite(case)` without `llm_client` works identically — all 613 existing tests pass.

## Deferred to Phase B

`subagent_runner.py`, `llm_scoring.py` (EvidenceVerifier + AnswerVerifier + SubagentScorer), `hooks.py`, integration into `_score_recovered_evidence` with optional `scorer` parameter.
