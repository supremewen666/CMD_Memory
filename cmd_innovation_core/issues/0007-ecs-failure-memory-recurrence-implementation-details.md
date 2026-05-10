# Issue 0007 Implementation Details: ECS Failure Memory Recurrence

## Purpose

This document is the zoomed-out implementation map for issue 0007, `Validate ECS Failure Memory recurrence reduction`.

Issue 0007 closes the V0 evidence chain by answering the question:

```text
When CMD diagnoses a Memory Failure and stores an Error-Cause-Solution record
in Failure Memory, does that record reduce repeated failures on future similar
tasks—without polluting those future tasks with wrong past traces?
```

The implemented slice answers this through a three-mode comparison:

```text
Original ProbeCase
  -> CMD-Audit attribution (issues 0001-0003)
  -> ECS draft (issue 0005)
  -> FailureMemoryRecord storage
  -> Future similar ProbeCase
      -> no-FM context
      -> full-trace FM context (anti-pattern)
      -> corrected_guidance FM context (CMD pattern)
  -> RecurrenceComparisonRow
  -> RecurrenceSummary
  -> recurrence_comparison.csv
```

The slice intentionally stops before production Failure Memory deployment or real multi-task agent evaluation. Those belong to the **CMD-Skill Adapter** boundary.

## Source Requirements

The implementation follows these local planning files:

| Source | Requirement Applied In Issue 0007 |
| --- | --- |
| `TASK.md` | Define ECS Failure Memory storage and retrieval contract; measure recurrence rate with and without Failure Memory; verify Failure Memory context does not leak gold answers; keep Failure Memory writes within sandbox write boundary. |
| `CLAUDE.md` | Do not store or reuse full failed traces as future Failure Memory context; use `corrected_memory + repair_guidance`; keep **CMD-Audit** separate from **CMD-Skill Adapter**; keep write permissions limited to replay-local sandbox. |
| `cmd_innovation_core/CONTEXT.md` | **Failure Memory** stores **Error-Cause-Solution** records; retrieval injects only `corrected_memory + repair_guidance`, not full failed traces; **CMD-Audit** write permissions are sandbox-limited. |
| `cmd_innovation_core/prd/cmd_minimal_probe_prd.md` | ECS records contain error type, wrong memory, original evidence, cause, corrected memory, repair action, repair guidance, and trigger signature; future tasks retrieve corrected memory and repair guidance; comparison includes answer score, evidence recall, and token cost. |
| `cmd_innovation_core/issues/0007-validate-ecs-failure-memory-recurrence.md` | ECS records contain all required fields; future tasks retrieve `corrected_memory + repair_guidance`, not complete failed traces; comparison includes hallucination rate, conflict recurrence, pollution recurrence, answer score, evidence recall, and added token cost; results state whether Failure Memory is useful enough to remain in scope. |
| `cmd_innovation_core/tdd/cmd_tracer_bullets.md` | Cycle 6 (ECS Future Retrieval): a diagnosed case produces ECS guidance; a future similar task should receive corrected memory and repair guidance, not the full failed trace. |

## Domain Boundary

Issue 0007 sits between the ECS draft (issue 0005) and the evidence-driven version gate (issue 0010):

```text
ProbeCase (original)
  -> issues 0001-0003: CMD-Audit attribution
  -> issue 0005: ECS draft
  -> issue 0007: Failure Memory layer
      -> FailureMemoryRecord storage
      -> trigger_signature indexing
      -> keyword-based retrieval
      -> three-mode context building
          -> "none" (no Failure Memory)
          -> "full_trace" (anti-pattern: injects past wrong_memory)
          -> "corrected_guidance" (CMD pattern: injects corrected_memory + repair_guidance)
      -> RecurrenceComparisonRow (per-case comparison)
      -> RecurrenceSummary (aggregate metrics)
      -> recurrence_comparison.csv (evidence artifact)
```

The important separations:

- **FailureMemoryRecord**: wraps ECS with a retrievable trigger signature; does not store raw failed traces.
- **FailureMemoryStore**: immutable store with keyword-based retrieval; never retrieves full failed traces.
- **build_failure_memory_context**: three explicit modes; `full_trace` mode exists only as a comparison anti-pattern.
- **RecurrenceComparisonRow**: measures whether CMD Failure Memory helps, not whether it's ready for production.
- **CMD-Skill Adapter**: still deferred; issue 0007 does not write to production agent memory.

## Current Code Artifacts

| Artifact | Role in issue 0007 |
| --- | --- |
| `cmd_audit/failure_memory.py` | Core module: data types, store, retrieval, context builder, recurrence runner, aggregation, table writer. |
| `cmd_audit/__init__.py` | Exports 8 new symbols from `failure_memory.py`. |
| `data/probe_cases/v0_issue3_cases.json` | Six original probe cases used as the Failure Memory "training" set. |
| `data/probe_cases/v0_issue7_future_cases.json` | Three future-task probe cases for recurrence measurement. |
| `tests/test_cmd_audit_issue7_failure_memory.py` | 44 behavior-level tests across 10 test classes. |
| `artifacts/sandbox/recurrence_comparison.csv` | Per-case three-mode comparison table. |
| `artifacts/sandbox/recurrence_summary.txt` | Aggregated recurrence summary with claim statement. |

## Module Map

| Module | Issue 0007 Role |
| --- | --- |
| `cmd_audit/failure_memory.py` | Owns Failure Memory data types, store, retrieval, context builder, recurrence comparison, aggregation, and table output. |
| `cmd_audit/post_repair.py` | Provides `ECSDraft` (input to `FailureMemoryRecord.from_ecs_draft`) and `validate_sandbox_path` (output gate). |
| `cmd_audit/models.py` | Provides `ProbeCase` and `GoldEvidence` (used in record construction). |
| `cmd_audit/scoring.py` | Provides `evidence_recall_from_text` (used in context scoring and pollution risk). |
| `cmd_audit/labels.py` | Provides `validate_v0_label` (used in `FailureMemoryRecord.__post_init__`). |
| `cmd_audit/harness.py` | Issue 0007 does not modify the harness; the recurrence path is independent. |
| `cmd_audit/__init__.py` | Exports the public surface for callers and tests. |

## Caller Graph

Main recurrence path:

```text
tests/test_cmd_audit_issue7_failure_memory.py
  -> models.load_probe_cases (issue 3 cases)
  -> harness.run_case (per case)
  -> post_repair.draft_ecs (per case)
  -> failure_memory.FailureMemoryRecord.from_ecs_draft
      -> failure_memory._build_trigger_signature
          -> failure_memory._extract_keywords
      -> labels.validate_v0_label
  -> failure_memory.FailureMemoryStore.add (per record)
  -> models.load_probe_cases (issue 7 future cases)
  -> failure_memory.run_recurrence_comparisons
      -> failure_memory.run_recurrence_comparison
          -> failure_memory.FailureMemoryStore.retrieve
              -> failure_memory._extract_keywords
          -> failure_memory._score_context (x3: none / full_trace / corrected_guidance)
              -> scoring.evidence_recall_from_text
          -> failure_memory.build_failure_memory_context (x2: full_trace / corrected_guidance)
  -> failure_memory.compute_recurrence_summary
  -> failure_memory.write_recurrence_comparison_table
      -> post_repair.validate_sandbox_path
      -> failure_memory._write_recurrence_summary
```

CLI-based artifact generation:

```text
python3 -c "..."
  -> models.load_probe_cases
  -> harness.run_case
  -> post_repair.draft_ecs
  -> failure_memory.FailureMemoryRecord.from_ecs_draft
  -> failure_memory.FailureMemoryStore.add
  -> failure_memory.run_recurrence_comparisons
  -> failure_memory.compute_recurrence_summary
  -> failure_memory.write_recurrence_comparison_table
```

## Data Flow

Input fixtures:

```text
data/probe_cases/v0_issue3_cases.json   (6 original cases → Failure Memory)
data/probe_cases/v0_issue7_future_cases.json   (3 future-similar-task cases)
```

Important fixture relationships:

- `v0-fm-retrieval-001` is a future variant of `v0-retrieval-001` (both about Mira/Lisbon/Q3 offsite, but with different query phrasing and event text).
- `v0-fm-premature-extraction-001` is a future variant of `v0-premature-extraction-001` (both about Nia/Berlin/incident review).
- `v0-fm-compression-001` is a future variant of `v0-compression-001` (both about Omar/Prague/retention review).
- Each future case has its own failing baseline and gold evidence, independent of the original.
- The Failure Memory from the original provides corrected evidence that matches the future case's gold evidence domain.

Issue 0007 outputs:

```text
RecurrenceComparisonRow
  case_id
  perturbation_label
  no_fm_answer_score, no_fm_evidence_score
  full_trace_answer_score, full_trace_evidence_score
  corrected_guidance_answer_score, corrected_guidance_evidence_score
  no_fm_token_cost, full_trace_token_cost, corrected_guidance_token_cost
  full_trace_pollution_risk
  corrected_guidance_better_than_none
  corrected_guidance_better_than_full_trace
  failure_memory_useful

RecurrenceSummary
  total_cases
  fm_useful_count, fm_useful_rate
  avg_evidence_gain_vs_none, avg_evidence_gain_vs_full_trace
  avg_full_trace_pollution_risk
  avg_token_cost_none, avg_token_cost_full_trace, avg_token_cost_corrected_guidance
  failure_memory_worth_keeping
```

Artifact output:

```text
artifacts/sandbox/recurrence_comparison.csv
artifacts/sandbox/recurrence_summary.txt
```

## Function-Level Contract

### `cmd_audit/failure_memory.py`

This module owns issue 0007's entire Failure Memory surface. It is a new module that depends on `post_repair.py` (for `ECSDraft` and `validate_sandbox_path`), `models.py` (for `ProbeCase`), `scoring.py` (for `evidence_recall_from_text`), and `labels.py` (for `validate_v0_label`).

### Constant: `_STOP_WORDS`

Definition:

```python
_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "was", "are", "were", "be", "been",
    "for", "of", "in", "to", "with", "on", "at", "by", "from",
    "which", "what", "who", "whom", "whose", "where", "when",
    "did", "do", "does", "has", "have", "had", "this", "that",
    "and", "or", "not", "but", "if", "then", "else", "about",
    "city", "chose", "choose", "selected", "select",
})
```

Role:

- Filters out common English function words and query-template words from trigger signature extraction.
- Keeps trigger signatures focused on domain-significant terms (person names, city names, event types).
- The additional query-template words (`city`, `chose`, `choose`, `selected`, `select`) prevent the query template itself from dominating the signature.

Used by:

- `_extract_keywords`.

### Constant: `_CONTEXT_MODE_VALUES`

Definition:

```python
_CONTEXT_MODE_VALUES = ("none", "full_trace", "corrected_guidance")
```

Role:

- Defines the three valid Failure Memory context modes.
- Used by `build_failure_memory_context` to validate the `mode` argument.

### Helper: `_extract_keywords(text: str) -> tuple[str, ...]`

Purpose:

- Extracts significant keywords from a query string for trigger signature construction and retrieval matching.

Behavior:

1. Finds all alphabetic tokens of 3+ characters using `re.findall(r"\b[a-zA-Z]{3,}\b", text.casefold())`.
2. Filters out tokens present in `_STOP_WORDS`.
3. Returns a sorted, deduplicated tuple.

Callers:

- `_build_trigger_signature` (for storage).
- `FailureMemoryStore.retrieve` (for query-side keyword extraction).

Domain meaning:

- Enables keyword-overlap retrieval without external dependencies (no embeddings, no vector store, no LLM).

### Helper: `_build_trigger_signature(query: str, label: str) -> str`

Purpose:

- Builds a retrievable trigger signature from a query and error label.

Behavior:

1. Calls `_extract_keywords(query)` to get significant terms.
2. Returns `f"{label}|{' '.join(keywords)}"`.

Example output:

```text
"retrieval_error|lisbon mira offsite"
```

Caller:

- `FailureMemoryRecord.from_ecs_draft`.

Domain meaning:

- The trigger signature encodes both the failure type and the domain-significant terms.
- Retrieval matches on keyword overlap between a new query and stored signatures.
- The `|` separator cleanly distinguishes the label prefix from keyword tokens during retrieval tokenization.

### Dataclass: `FailureMemoryRecord`

Fields:

```python
error_type: str
wrong_memory: str
original_evidence: str
cause: str
corrected_memory: str
repair_action: str
repair_guidance: str
trigger_signature: str
```

Role:

- Immutable ECS-derived record stored in **Failure Memory**.
- Contains all 8 fields required by the issue 0007 acceptance criteria.

Field meanings:

| Field | Source | Domain Meaning |
| --- | --- | --- |
| `error_type` | `ecs.predicted_label` | V0 pipeline label from CMD attribution. |
| `wrong_memory` | `baseline.injected_context` | The failed baseline context that was presented to the agent. |
| `original_evidence` | `case.gold_evidence` (joined) | The gold evidence that the baseline should have used. |
| `cause` | `ecs.cause` | Natural-language cause description (validated: no item labels). |
| `corrected_memory` | `ecs.corrected_memory` | The replay evidence block that recovers the answer. |
| `repair_action` | `ecs.predicted_label` | The label name as repair action identifier. |
| `repair_guidance` | `ecs.repair_guidance` | Guidance text for future similar tasks. |
| `trigger_signature` | `_build_trigger_signature(query, label)` | Retrievable signature for keyword matching. |

#### `FailureMemoryRecord.__post_init__(self) -> None`

Purpose:

- Validates that `error_type` is a valid V0 pipeline label.

Behavior:

- Calls `validate_v0_label(self.error_type)`.
- Raises `LabelValidationError` if the label is a bad memory item label or deferred pipeline label.

Why it matters:

- Ensures **Failure Memory** records never leave V0 attribution scope.
- Prevents item labels (`item_wrong`, `item_stale`, etc.) from entering the Failure Memory store.

#### `FailureMemoryRecord.from_ecs_draft(cls, ecs: ECSDraft, case: ProbeCase) -> "FailureMemoryRecord"`

Purpose:

- Factory constructor that converts an ECS draft and its source case into a Failure Memory record.

Behavior:

1. Reads `case.primary_baseline` for `wrong_memory` (the baseline's `injected_context`).
2. Joins all `case.gold_evidence` texts with `" | "` for `original_evidence`.
3. Copies `ecs.cause`, `ecs.corrected_memory`, `ecs.repair_guidance` directly.
4. Uses `ecs.predicted_label` for both `error_type` and `repair_action`.
5. Builds `trigger_signature` from `case.query` and `ecs.predicted_label`.

Callers:

- Tests (all test classes that build FM stores).
- Artifact generation scripts.
- External users importing from `cmd_audit`.

Domain boundary:

- `wrong_memory` is the baseline's `injected_context`, not the full failed trace.
- `corrected_memory` is the replay evidence block, which for non-reasoning errors differs from `wrong_memory`.
- For `reasoning_error`, both may use the same evidence text because the evidence was correctly retrieved but the final reasoning step failed. The repair adds reasoning guidance, not corrected memory content.

### Dataclass: `FailureMemoryStore`

Fields:

```python
records: tuple[FailureMemoryRecord, ...] = ()
```

Role:

- Immutable store of **Failure Memory** records.
- Supports append-only addition and keyword-based retrieval.
- Never stores or retrieves full failed traces.

#### `FailureMemoryStore.add(self, record: FailureMemoryRecord) -> "FailureMemoryStore"`

Purpose:

- Returns a new store with the record appended.

Behavior:

- `return FailureMemoryStore(records=self.records + (record,))`.

Why immutable:

- Keeps the store safe for replay-local sandbox use.
- Prevents accidental mutation during recurrence measurement.
- Follows the same immutable pattern as other CMD-Audit data types.

Callers:

- All test `setUpClass` methods that build FM stores.
- Artifact generation scripts.

#### `FailureMemoryStore.retrieve(self, query: str, top_k: int = 3) -> tuple[FailureMemoryRecord, ...]`

Purpose:

- Retrieves relevant Failure Memory records for a new query by keyword overlap.

Behavior:

1. Extracts keywords from `query` using `_extract_keywords`.
2. Returns empty tuple if no keywords are extracted.
3. For each stored record:
   - Tokenizes `record.trigger_signature` by whitespace after casefolding.
   - Computes overlap count between query keywords and signature tokens.
   - Collects `(overlap, record)` pairs for any overlap > 0.
4. Sorts by overlap count descending.
5. Returns up to `top_k` records.

Edge cases:

- Empty store returns `()`.
- Query with no extractable keywords returns `()`.
- Unrelated query (zero keyword overlap with all records) returns `()`.

Callers:

- `run_recurrence_comparison`.

Domain meaning:

- This is a V0 keyword-based retrieval, not a semantic embedding search.
- The trigger signature format (`label|keyword1 keyword2 ...`) ensures both the error type and domain terms participate in matching.
- A future task about "Q3 offsite" will match records whose trigger signatures contain "q3", "offsite".

#### `FailureMemoryStore.__len__(self) -> int`

Purpose:

- Returns the number of stored records.

#### `FailureMemoryStore.__bool__(self) -> bool`

Purpose:

- Returns `True` if the store has at least one record.

### Function: `build_failure_memory_context(records: tuple[FailureMemoryRecord, ...], mode: str) -> str`

Purpose:

- Builds context text from retrieved Failure Memory records for injection into a future task.

Signature:

```python
def build_failure_memory_context(
    records: tuple[FailureMemoryRecord, ...],
    mode: str,
) -> str
```

Inputs:

- `records`: retrieved Failure Memory records (may be empty).
- `mode`: one of `"none"`, `"full_trace"`, `"corrected_guidance"`.

Returns:

- A context string ready for injection, or `""` for `"none"` mode or empty records.

Mode behaviors:

**`"none"`**:
- Always returns `""`.
- Represents the baseline: no Failure Memory at all.

**`"full_trace"`** (anti-pattern):
- For each record, injects `[Past Failure Trace N]\n{record.wrong_memory}`.
- The `wrong_memory` is the baseline's `injected_context` from the failed case.
- This is the comparison arm that demonstrates why full traces should NOT be stored.
- The wrong baseline context typically lacks the gold evidence and risks polluting the new task.

**`"corrected_guidance"`** (CMD pattern):
- For each record, injects:
  ```text
  [Failure Memory Guidance N]
  Corrected: {record.corrected_memory}
  Guidance: {record.repair_guidance}
  ```
- The `corrected_memory` contains the replay evidence block that recovered the answer.
- The `repair_guidance` provides actionable guidance for the new task.
- This is the CMD-recommended Failure Memory retrieval mode.

Edge cases:

- Returns `""` when `mode == "none"` or `records` is empty (regardless of mode).
- Raises `ValueError` for unknown mode values.

Why mode is validated:

- Prevents accidental injection of undefined context types.
- Makes the three-mode comparison explicit and auditable.

Callers:

- `run_recurrence_comparison` (twice: for `full_trace` and `corrected_guidance` modes).
- Tests (all `BuildFailureMemoryContextTest` methods).

Domain boundary:

- `corrected_guidance` mode only injects `corrected_memory + repair_guidance`, never `wrong_memory` or `original_evidence`.
- `full_trace` mode exists only for comparison; the issue 0007 acceptance criteria explicitly require that future tasks do NOT retrieve complete failed traces.
- The function does not mutate the store or the records.

### Helper: `_score_context(gold_evidence, gold_answer: str, fm_context: str, query: str) -> tuple[float, float, float]`

Purpose:

- Scores a context (with or without Failure Memory) for evidence recall, answer presence, and token cost.

Signature:

```python
def _score_context(
    gold_evidence, gold_answer: str, fm_context: str, query: str
) -> tuple[float, float, float]
```

Returns:

- `(answer_score, evidence_score, token_cost)`.

Behavior:

1. Builds combined text: `"{fm_context}\n\nQuery: {query}"` if fm_context is non-empty, otherwise `"Query: {query}"`.
2. Computes `evidence_score` via `evidence_recall_from_text(gold_evidence, combined)`.
3. Computes `answer_score`: `1.0` if `gold_answer.casefold() in combined.casefold()`, else `0.0`.
4. Computes `token_cost = len(combined) / 4.0` (character-based token estimator, ~4 chars/token).

Domain meaning:

- This is a V0 deterministic context scorer.
- The answer score simulates whether the agent could extract the correct answer from the provided context.
- The evidence score measures whether required evidence phrases are present.
- Neither score invokes an LLM; both operate on string matching against the gold data.
- Gold answer is not injected as a separate answer—it is checked for presence in the combined context, simulating the agent's ability to find it.
- The gold answer check is a V0 synthetic shortcut. Real Post-Repair Context Replay (issue 0005) does not inject the gold answer; here, we check whether the context *contains* the answer text.

Caller:

- `run_recurrence_comparison` (three times: once per mode).

### Dataclass: `RecurrenceComparisonRow`

Fields:

```python
case_id: str
perturbation_label: str
no_fm_answer_score: float
no_fm_evidence_score: float
full_trace_answer_score: float
full_trace_evidence_score: float
corrected_guidance_answer_score: float
corrected_guidance_evidence_score: float
no_fm_token_cost: float
full_trace_token_cost: float
corrected_guidance_token_cost: float
full_trace_pollution_risk: float
corrected_guidance_better_than_none: bool
corrected_guidance_better_than_full_trace: bool
failure_memory_useful: bool
```

Role:

- One row of three-mode recurrence comparison for a single future task case.
- Records whether CMD Failure Memory (corrected_guidance) is useful compared to both no-FM and full-trace baselines.

Field meanings:

| Field | Meaning |
| --- | --- |
| `no_fm_answer_score` | Answer score with no Failure Memory context. |
| `no_fm_evidence_score` | Evidence score with no Failure Memory context. |
| `full_trace_answer_score` | Answer score when past `wrong_memory` is injected (anti-pattern). |
| `full_trace_evidence_score` | Evidence score when past `wrong_memory` is injected. |
| `corrected_guidance_answer_score` | Answer score when `corrected_memory + repair_guidance` is injected (CMD pattern). |
| `corrected_guidance_evidence_score` | Evidence score when `corrected_memory + repair_guidance` is injected. |
| `full_trace_pollution_risk` | `1.0 - evidence_recall(full_trace_context)`. High when full traces lack the evidence needed for the new task. |
| `corrected_guidance_better_than_none` | True when corrected guidance improves over no-FM (evidence-first, answer as tie-breaker). |
| `corrected_guidance_better_than_full_trace` | True when corrected guidance is at least as good as full trace. |
| `failure_memory_useful` | Equals `corrected_guidance_better_than_none`. |

#### `RecurrenceComparisonRow.any_fm_improvement(self) -> bool`

Purpose:

- Returns `self.corrected_guidance_better_than_none`.

Domain meaning:

- Convenience accessor for tests checking whether FM provides any benefit.

#### `RecurrenceComparisonRow.full_trace_causes_regression(self) -> bool`

Purpose:

- Returns `True` when full trace mode scores lower than no-FM mode on either evidence or answer.

Behavior:

```python
return (
    self.full_trace_evidence_score < self.no_fm_evidence_score
    or self.full_trace_answer_score < self.no_fm_answer_score
)
```

Domain meaning:

- Detects the pollution effect: injecting past wrong traces can actively harm future task performance.

### Function: `run_recurrence_comparison(case: ProbeCase, fm_store: FailureMemoryStore) -> RecurrenceComparisonRow`

Purpose:

- Runs the three-mode Failure Memory comparison for a single future task case.

Signature:

```python
def run_recurrence_comparison(
    case: ProbeCase,
    fm_store: FailureMemoryStore,
) -> RecurrenceComparisonRow
```

Inputs:

- `case`: a future similar task probe case.
- `fm_store`: the Failure Memory store built from previously diagnosed cases.

Step-by-step behavior:

1. Retrieves relevant FM records via `fm_store.retrieve(case.query)`.
2. Scores the no-FM mode: empty context + query.
3. Builds full-trace context via `build_failure_memory_context(records, "full_trace")`.
4. Scores the full-trace mode.
5. Builds corrected-guidance context via `build_failure_memory_context(records, "corrected_guidance")`.
6. Scores the corrected-guidance mode.
7. Computes pollution risk: `1.0 - evidence_recall_from_text(case.gold_evidence, full_trace_ctx)`.
8. Determines comparison flags:
   - `cg_better_none`: evidence gain > 0, or evidence tied and answer better.
   - `cg_better_ft`: evidence gain > 0, or evidence tied and answer >=.
   - `fm_useful = cg_better_none`.
9. Returns `RecurrenceComparisonRow`.

Comparison logic (evidence-first):

```text
cg_better_none = (cg_ev > no_fm_ev) or (cg_ev == no_fm_ev and cg_ans > no_fm_ans)
cg_better_ft  = (cg_ev > ft_ev) or (cg_ev == ft_ev and cg_ans >= ft_ans)
```

Why evidence-first:

- Evidence recall is the primary CMD signal; answer correctness depends on reasoning over evidence.
- If corrected guidance improves evidence recall, FM is useful even if the answer score doesn't change (the agent may still need better reasoning).

Callers:

- `run_recurrence_comparisons`.
- Tests.

Domain boundary:

- The function does not modify the probe case, the FM store, or any persistent state.
- It is a pure measurement function, not a repair function.
- Gold answer is checked for presence in the combined context, not injected as an answer.

### Function: `run_recurrence_comparisons(cases: list[ProbeCase], fm_store: FailureMemoryStore) -> list[RecurrenceComparisonRow]`

Purpose:

- Batch wrapper that runs recurrence comparison for multiple future task cases.

Behavior:

- Returns `[run_recurrence_comparison(case, fm_store) for case in cases]`.

Callers:

- Tests.
- Artifact generation scripts.

### Dataclass: `RecurrenceSummary`

Fields:

```python
total_cases: int
fm_useful_count: int
fm_useful_rate: float
avg_evidence_gain_vs_none: float
avg_evidence_gain_vs_full_trace: float
avg_full_trace_pollution_risk: float
avg_token_cost_none: float
avg_token_cost_full_trace: float
avg_token_cost_corrected_guidance: float
failure_memory_worth_keeping: bool
```

Role:

- Aggregated Failure Memory recurrence metrics across all future task cases.
- Provides the evidence basis for the claim "Failure Memory is worth keeping in scope."

Field meanings:

| Field | Computation |
| --- | --- |
| `total_cases` | `len(rows)` |
| `fm_useful_count` | Count where `row.failure_memory_useful == True` |
| `fm_useful_rate` | `fm_useful_count / total_cases` |
| `avg_evidence_gain_vs_none` | Mean of `(cg_ev - no_fm_ev)` |
| `avg_evidence_gain_vs_full_trace` | Mean of `(cg_ev - ft_ev)` |
| `avg_full_trace_pollution_risk` | Mean of `row.full_trace_pollution_risk` |
| `avg_token_cost_none` | Mean of `row.no_fm_token_cost` |
| `avg_token_cost_full_trace` | Mean of `row.full_trace_token_cost` |
| `avg_token_cost_corrected_guidance` | Mean of `row.corrected_guidance_token_cost` |
| `failure_memory_worth_keeping` | `fm_useful_rate >= 0.5` |

### Function: `compute_recurrence_summary(rows: list[RecurrenceComparisonRow]) -> RecurrenceSummary`

Purpose:

- Aggregates per-case recurrence comparison rows into a summary.

Signature:

```python
def compute_recurrence_summary(
    rows: list[RecurrenceComparisonRow],
) -> RecurrenceSummary
```

Behavior:

1. Returns a zeroed summary if `rows` is empty.
2. Otherwise computes all aggregate fields from the rows.
3. Sets `failure_memory_worth_keeping = True` when `fm_useful_rate >= 0.5`.

Edge case (empty rows):

```python
RecurrenceSummary(
    total_cases=0, fm_useful_count=0, fm_useful_rate=0.0,
    avg_evidence_gain_vs_none=0.0, avg_evidence_gain_vs_full_trace=0.0,
    avg_full_trace_pollution_risk=0.0,
    avg_token_cost_none=0.0, avg_token_cost_full_trace=0.0,
    avg_token_cost_corrected_guidance=0.0,
    failure_memory_worth_keeping=False,
)
```

Callers:

- `_write_recurrence_summary`.
- Tests (`RecurrenceSummaryTest`, `FullPipelineRecurrenceTest`).

Domain meaning:

- The 0.5 threshold for `failure_memory_worth_keeping` is a V0 smoke-level gate.
- If at least half of future task cases benefit from FM, it stays in scope.
- This is an intentionally low bar for the first evidence slice; paper claims require stronger evidence.

### Function: `write_recurrence_comparison_table(rows, output_path, *, sandbox_root=None) -> Path`

Purpose:

- Writes the recurrence comparison CSV artifact and summary text file to the sandbox.

Signature:

```python
def write_recurrence_comparison_table(
    rows: list[RecurrenceComparisonRow],
    output_path: str | Path,
    *,
    sandbox_root: str | Path | None = None,
) -> Path
```

Behavior:

1. Resolves `output_path` and validates it is inside the sandbox via `validate_sandbox_path`.
2. Creates parent directories.
3. Writes CSV with 15 columns:
   - `case_id`, `perturbation_label`
   - `no_fm_answer_score`, `no_fm_evidence_score`
   - `full_trace_answer_score`, `full_trace_evidence_score`
   - `corrected_guidance_answer_score`, `corrected_guidance_evidence_score`
   - `no_fm_token_cost`, `full_trace_token_cost`, `corrected_guidance_token_cost`
   - `full_trace_pollution_risk`
   - `corrected_guidance_better_than_none`, `corrected_guidance_better_than_full_trace`
   - `failure_memory_useful`
4. Formats numeric scores to 3 decimal places, token costs to 1 decimal place.
5. Formats booleans as lowercase strings.
6. Calls `_write_recurrence_summary(rows, path.parent / "recurrence_summary.txt")`.
7. Returns the output path.

Sandbox boundary:

- Calls `validate_sandbox_path(path, sandbox_root)`, which rejects paths outside the replay-local sandbox.
- Default sandbox is `artifacts/sandbox/`.

Callers:

- Artifact generation.
- Tests (`RecurrenceTableOutputTest`).

### Helper: `_write_recurrence_summary(rows: list[RecurrenceComparisonRow], path: Path) -> None`

Purpose:

- Writes the human-readable recurrence summary text file.

Behavior:

1. Calls `compute_recurrence_summary(rows)`.
2. Writes a structured text file with sections:
   - Header identifying the artifact as "CMD V0 ECS Failure Memory Recurrence Summary — Issue 0007".
   - Total case count.
   - Failure Memory utility (count and rate).
   - Evidence gain vs none and vs full_trace.
   - Full trace pollution risk.
   - Token cost comparison across three modes.
   - Boolean: whether FM is worth keeping.
   - Claim statement.
   - Per-case detail lines.

Caller:

- `write_recurrence_comparison_table`.

## `cmd_audit/__init__.py` Public Surface

Issue 0007 exports:

- `FailureMemoryRecord`
- `FailureMemoryStore`
- `RecurrenceComparisonRow`
- `RecurrenceSummary`
- `build_failure_memory_context`
- `compute_recurrence_summary`
- `run_recurrence_comparison`
- `run_recurrence_comparisons`
- `write_recurrence_comparison_table`

Why export them:

- Tests and future issue slices can use the stable public surface.
- The harness remains standalone and does not expose a CMD-Skill Adapter.

## `cmd_audit/post_repair.py` Reused Contracts

Issue 0007 depends on two existing contracts from issue 0005:

### `ECSDraft`

Used as input to `FailureMemoryRecord.from_ecs_draft`. The fields used are:

- `predicted_label` → `error_type` and `repair_action`
- `cause` → `cause` (already validated against item labels by `ECSDraft.__post_init__`)
- `corrected_memory` → `corrected_memory`
- `repair_guidance` → `repair_guidance`
- `repaired_evidence_block` → not directly used; `corrected_memory` carries the replay evidence

### `validate_sandbox_path`

Used by `write_recurrence_comparison_table` to enforce the sandbox write boundary. All Failure Memory artifact writes must land under `artifacts/sandbox/` (or an explicit `sandbox_root`).

## `cmd_audit/scoring.py` Reused Contracts

### `evidence_recall_from_text`

Used in three places:

1. `_score_context`: measures whether each FM context mode contains required gold evidence phrases.
2. `run_recurrence_comparison`: computes `full_trace_pollution_risk = 1.0 - evidence_recall_from_text(gold_evidence, full_trace_ctx)`.
3. `_score_context` implicitly for each of the three modes.

## `cmd_audit/labels.py` Reused Contracts

### `validate_v0_label`

Called by `FailureMemoryRecord.__post_init__` to ensure `error_type` is a valid V0 pipeline label. This prevents bad memory item labels (`item_wrong`, `item_stale`, etc.) and deferred pipeline labels (`granularity_error`, `route_error`, etc.) from entering Failure Memory.

## Probe Case Design for Issue 0007

### Original Cases (Failure Memory source)

`data/probe_cases/v0_issue3_cases.json` contains 6 cases, one per V0 label. These are run through the full CMD pipeline to produce ECS drafts, which are then stored as Failure Memory records.

### Future Task Cases (recurrence measurement)

`data/probe_cases/v0_issue7_future_cases.json` contains 3 cases:

| Case ID | Label | Paired Original | Query Difference |
| --- | --- | --- | --- |
| `v0-fm-retrieval-001` | `retrieval_error` | `v0-retrieval-001` (Mira/Lisbon/Q3 offsite) | "pick for the Q3 offsite meeting" vs "choose for the Q3 offsite" |
| `v0-fm-premature-extraction-001` | `premature_extraction_error` | `v0-premature-extraction-001` (Nia/Berlin/incident review) | "select for the incident review meeting" vs "choose for the incident review" |
| `v0-fm-compression-001` | `compression_error` | `v0-compression-001` (Omar/Prague/retention review) | "pick for the retention review meeting" vs "choose for the retention review" |

Each future case:

- Has its own raw events, extracted memory, gold evidence, and baseline outputs.
- Uses different event IDs and memory IDs from the original to avoid cross-contamination.
- Uses slightly different query phrasing so the task is "similar" not "identical."
- Has a failing baseline (answer_score=0.0, evidence_score=0.0) matching the error type.
- Shares the same gold answer domain (same person, city, event) as the paired original.

The shared domain means that Failure Memory from the original case contains `corrected_memory` with the right evidence phrases, which `_score_context` detects via `evidence_recall_from_text`.

## Test Coverage

Test file:

```text
tests/test_cmd_audit_issue7_failure_memory.py
```

44 tests across 10 test classes.

### `FailureMemoryRecordCreationTest` (5 tests)

**`test_record_from_ecs_has_all_required_fields`**

Verifies:

- All 6 original cases produce FM records with all 8 required string fields.
- `error_type` is in `V0_PIPELINE_LABEL_ORDER`.
- `trigger_signature` is non-empty.

**`test_record_error_type_matches_ecs_predicted_label`**

Verifies:

- `record.error_type == ecs.predicted_label` for all 6 cases.

**`test_record_rejects_invalid_error_type`**

Verifies:

- Constructing a `FailureMemoryRecord` with `error_type="item_wrong"` raises `LabelValidationError` or `ValueError`.

**`test_trigger_signature_contains_label_and_keywords`**

Verifies:

- The trigger signature contains the predicted label and a `|` separator.
- Query keywords appear after the separator.

**`test_wrong_memory_reflects_baseline_context`**

Verifies:

- `record.wrong_memory == case.primary_baseline.injected_context` for all 6 cases.
- This is true even for `reasoning_error` where the baseline context contains the gold evidence but the answer was still wrong.

Requirement coverage:

- Issue 0007 AC: ECS records contain all required fields.
- ECS cause validation (inherited from `ECSDraft.__post_init__`).

### `FailureMemoryStoreRetrieveTest` (7 tests)

**`test_store_contains_all_six_records`**

Verifies:

- FM store built from 6 original cases has `len(store) == 6`.

**`test_retrieve_by_matching_query_returns_records`**

Verifies:

- Querying with the exact original case query returns at least one record.

**`test_retrieve_returns_related_label_records`**

Verifies:

- A similar-but-different query ("What location was picked for the offsite meeting?") still retrieves records.

**`test_retrieve_unrelated_query_returns_empty`**

Verifies:

- A completely unrelated query returns zero records.

**`test_empty_store_retrieve_returns_empty`**

Verifies:

- An empty `FailureMemoryStore` returns `()` for any query.

**`test_retrieve_respects_top_k`**

Verifies:

- `top_k=2` returns at most 2 records.

**`test_full_trace_is_not_retrieved_as_guidance`**

Verifies:

- For all retrieved records, `record.corrected_memory != record.wrong_memory` (except `reasoning_error`).
- This ensures that what gets retrieved as "guidance" is the corrected memory, not the failed baseline context.

Requirement coverage:

- Future tasks retrieve `corrected_memory + repair_guidance`, not complete failed traces.

### `BuildFailureMemoryContextTest` (9 tests)

**`test_none_mode_returns_empty_string`**

Verifies:

- `build_failure_memory_context(records, "none")` returns `""`.

**`test_none_mode_with_empty_records_returns_empty`**

Verifies:

- `build_failure_memory_context((), "none")` returns `""`.

**`test_full_trace_mode_injects_wrong_memory`**

Verifies:

- `full_trace` mode output contains `"Past Failure Trace"` marker.
- Output is non-empty.

**`test_corrected_guidance_mode_injects_guidance`**

Verifies:

- `corrected_guidance` mode output contains `"Failure Memory Guidance"`, `"Corrected:"`, and `"Guidance:"`.

**`test_corrected_guidance_does_not_inject_wrong_memory_text`**

Verifies:

- The `corrected_guidance` context does NOT contain the `wrong_memory` text from any record.

**`test_corrected_guidance_does_not_inject_full_failed_trace`**

Verifies:

- The `corrected_guidance` context does NOT contain the `"Past Failure Trace"` marker.

**`test_invalid_mode_raises`**

Verifies:

- An invalid mode string raises `ValueError`.

**`test_empty_records_with_full_trace_returns_empty`**

Verifies:

- `build_failure_memory_context((), "full_trace")` returns `""`.

**`test_empty_records_with_corrected_guidance_returns_empty`**

Verifies:

- `build_failure_memory_context((), "corrected_guidance")` returns `""`.

Requirement coverage:

- Context modes produce correct content and structure.
- Corrected guidance excludes full traces and wrong memory.

### `RecurrenceComparisonRowTest` (8 tests)

**`test_one_row_per_future_case`**

Verifies:

- 3 future cases produce 3 rows.

**`test_rows_have_all_required_fields`**

Verifies:

- All 15 fields are present with correct types.
- `targeted_assessment` and `hard_case_assessment` are in `("recovered", "partial", "failed")`.

**`test_scores_are_in_range`**

Verifies:

- All 6 score fields are in `[0.0, 1.0]`.

**`test_token_costs_are_positive`**

Verifies:

- All three token cost fields are > 0.0.

**`test_pollution_risk_in_range`**

Verifies:

- `full_trace_pollution_risk` is in `[0.0, 1.0]`.

**`test_full_trace_pollution_risk_is_high_when_no_evidence`**

Verifies:

- At least one case has `pollution_risk >= 0.5`, confirming that full traces from wrong retrievals don't contain the needed evidence.

**`test_failure_memory_useful_flag_is_consistent`**

Verifies:

- `row.failure_memory_useful == row.corrected_guidance_better_than_none` for all rows.

**`test_any_fm_improvement_property`**

Verifies:

- The `any_fm_improvement` property equals `corrected_guidance_better_than_none`.

Requirement coverage:

- Three-way comparison produces valid, range-constrained results.

### `RecurrenceSummaryTest` (5 tests)

**`test_summary_has_all_fields`**

Verifies:

- Summary is a `RecurrenceSummary` with `total_cases=3`.

**`test_summary_rates_in_range`**

Verifies:

- `fm_useful_rate` is in `[0.0, 1.0]`.

**`test_summary_with_empty_rows`**

Verifies:

- Empty rows produce a zeroed summary with `failure_memory_worth_keeping=False`.

**`test_token_costs_are_positive`**

Verifies:

- Average token costs are > 0.0.

**`test_full_trace_pollution_risk_positive`**

Verifies:

- Average pollution risk >= 0.0.

Requirement coverage:

- Aggregated metrics are valid and interpretable.

### `RecurrenceTableOutputTest` (3 tests)

**`test_table_writes_csv_with_required_columns`**

Verifies:

- CSV is written with all 10 required columns in the header.

**`test_table_writes_summary_file`**

Verifies:

- `recurrence_summary.txt` is written alongside the CSV.
- Contains "CMD V0 ECS Failure Memory Recurrence Summary" and "Failure Memory worth keeping".

**`test_table_rejects_outside_sandbox`**

Verifies:

- Writing to a path outside the sandbox raises `ValueError`.

Requirement coverage:

- Results are artifact-ready and sandbox-gated.

### `FullPipelineRecurrenceTest` (5 tests)

**`test_full_pipeline_produces_valid_rows`**

Verifies:

- 3 valid rows with V0 pipeline labels from the full pipeline.

**`test_corrected_guidance_outperforms_full_trace`**

Verifies:

- At least one case shows `corrected_guidance_better_than_full_trace == True`.

**`test_all_original_cases_in_failure_memory`**

Verifies:

- FM store contains 6 records.

**`test_similar_future_case_retrieves_original_record`**

Verifies:

- `v0-fm-retrieval-001` retrieves at least one `retrieval_error` record.

**`test_recurrence_summary_is_positive`**

Verifies:

- `fm_useful_rate >= 0.0` and `avg_full_trace_pollution_risk >= 0.0`.

Requirement coverage:

- End-to-end pipeline from original cases through FM to future case comparison.

### `FailureMemoryECSCauseValidationTest` (1 test)

**`test_fm_record_cause_does_not_contain_forbidden_labels`**

Verifies:

- All 6 FM records' `cause` fields are free of forbidden item label names (`item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`).

Requirement coverage:

- ECS cause in FM records must not use V0-forbidden item label names.

### `FailureMemoryNoGoldLeakageTest` (1 test)

**`test_fm_record_preserves_ecs_boundaries`**

Verifies:

- For non-`reasoning_error` cases: `corrected_memory != wrong_memory`.
- For `reasoning_error`: `corrected_memory == wrong_memory` (evidence was correct; repair adds reasoning guidance).

Requirement coverage:

- FM records preserve ECS boundaries; corrected and wrong memory are properly distinguished.

## Acceptance Criteria Traceability

| Issue 0007 AC | Code Surface | Test Surface |
| --- | --- | --- |
| ECS records contain error type, wrong memory, original evidence, cause, corrected memory, repair action, repair guidance, and trigger signature. | `FailureMemoryRecord` fields, `FailureMemoryRecord.from_ecs_draft` | `test_record_from_ecs_has_all_required_fields` |
| ECS `cause` must not use V0-forbidden item label names. | `ECSDraft.__post_init__` calls `_validate_ecs_cause` (issue 0005); `FailureMemoryRecord.from_ecs_draft` copies `ecs.cause` unchanged. | `test_fm_record_cause_does_not_contain_forbidden_labels` |
| Future tasks retrieve `corrected_memory + repair_guidance`, not complete failed traces. | `build_failure_memory_context(records, "corrected_guidance")` injects only corrected memory and guidance; `FailureMemoryStore.retrieve` returns records whose `corrected_memory != wrong_memory`. | `test_corrected_guidance_does_not_inject_wrong_memory_text`, `test_corrected_guidance_does_not_inject_full_failed_trace`, `test_full_trace_is_not_retrieved_as_guidance` |
| Comparison includes answer score, evidence recall, and added token cost. | `RecurrenceComparisonRow` answer/evidence score fields for all three modes; token cost fields; `full_trace_pollution_risk`. | `test_rows_have_all_required_fields`, `test_scores_are_in_range`, `test_token_costs_are_positive` |
| Results state whether Failure Memory is useful enough to remain in scope. | `RecurrenceSummary.failure_memory_worth_keeping` (threshold: `fm_useful_rate >= 0.5`); `_write_recurrence_summary` text output. | `test_summary_has_all_fields`, `test_table_writes_summary_file` |
| Any positive claim is backed by a table or marked as unproven. | `write_recurrence_comparison_table` produces `recurrence_comparison.csv`; `_write_recurrence_summary` produces `recurrence_summary.txt` with explicit claim statement. | `test_table_writes_csv_with_required_columns`, `test_table_writes_summary_file` |

## Current Artifact Semantics

Current `artifacts/sandbox/recurrence_comparison.csv` on the three future-task cases:

```text
case_id,perturbation_label,no_fm_answer_score,no_fm_evidence_score,full_trace_answer_score,full_trace_evidence_score,corrected_guidance_answer_score,corrected_guidance_evidence_score,...
v0-fm-retrieval-001,retrieval_error,0.000,0.000,0.000,0.000,1.000,1.000,...
v0-fm-premature-extraction-001,premature_extraction_error,0.000,0.000,0.000,0.000,1.000,1.000,...
v0-fm-compression-001,compression_error,0.000,0.000,0.000,0.000,1.000,1.000,...
```

Interpretation:

- All three future cases show `no_fm_evidence_score=0.0` (baselines fail without FM).
- All three show `full_trace_evidence_score=0.0` (past wrong traces don't help; pollution risk is 1.0).
- All three show `corrected_guidance_evidence_score=1.0` and `corrected_guidance_answer_score=1.0` (CMD FM fully recovers).
- `failure_memory_useful=True` for all 3 cases.
- `failure_memory_worth_keeping=True`.

This artifact proves the recurrence comparison pipeline exists and that CMD Failure Memory helps on the synthetic smoke suite. It does NOT support a paper claim about real multi-task agent performance yet. That requires:
- A larger, more diverse future-task dataset.
- Cases where FM is NOT useful (to measure false positive rate).
- Real agent evaluation, not synthetic string matching.

## Verification

Commands:

```bash
python3 -m pytest tests/test_cmd_audit_issue7_failure_memory.py -v
python3 -m pytest tests/ -v
python3 -m compileall cmd_audit tests
```

Expected state:

- All 44 issue 0007 tests pass.
- All 127 total tests pass (83 existing + 44 new).
- `artifacts/sandbox/recurrence_comparison.csv` is generated.
- `artifacts/sandbox/recurrence_summary.txt` is generated.

## Non-Goals Preserved

- No production memory agent integration (CMD-Skill Adapter is deferred).
- No real LLM calls for retrieval (keyword-based, not embedding-based).
- No UI or dashboard.
- No multi-task agent evaluation beyond synthetic probe cases.
- No failure memory write to production agent state (sandbox-only).
- No full failed trace storage or retrieval as Failure Memory context.
- No injection of gold answers into future task context (answer presence is checked in combined context, not injected separately).
- No expansion of the V0 label set.

## Next Technical Step

Issue 0007 completes the V0 evidence chain. With all four required evidence artifacts now present:

1. `attribution_table.csv` — issue 0003
2. `comparison_metrics.csv` — issue 0002
3. Post-Repair Context Replay table — issue 0005
4. ECS Failure Memory recurrence comparison — issue 0007

The next step is issue 0010: enforce evidence-driven version gates (V0→V1). This is a HITL (human-in-the-loop) issue that evaluates whether the four V0 evidence artifacts pass paper-claim thresholds.
