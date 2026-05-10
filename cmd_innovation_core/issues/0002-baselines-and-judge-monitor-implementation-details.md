# Issue 0002 Implementation Details: Baselines and Judge Monitor

## Purpose

Issue 0002 adds the first non-CMD comparison layer around the existing CMD-Audit retrieval tracer bullet.

The implemented slice answers this question:

```text
For each labeled Memory Failure probe case, can CMD-Audit keep replay-delta attribution separate from cheaper observational comparators and from a leak-safe replay trigger?
```

This document zooms out from individual statements in code and maps the issue to project vocabulary, module boundaries, caller paths, public result shapes, and every issue-0002 function.

## Source Requirements

The implementation follows these local documents.

| Source | Requirement Applied In Issue 0002 |
| --- | --- |
| `TASK.md` | Run fixed-summary and vector-memory baselines; compare CMD against heuristic and subagent judge baselines; produce evidence before paper claims. |
| `CLAUDE.md` | Treat `cmd_innovation_core/` as source of truth; keep **CMD-Audit** separate from **CMD-Skill Adapter**; keep **Subagent Judge Monitor** leak-safe. |
| `cmd_innovation_core/CONTEXT.md` | Use **Subagent Judge Baseline** only as comparator; use **Subagent Judge Monitor** only to trigger replay; final attribution belongs to CMD replay deltas. |
| `cmd_innovation_core/prd/cmd_minimal_probe_prd.md` | Include baseline outputs, evidence recall heuristics, subagent judge baseline, high-recall monitor, random baseline, and comparison metrics. |
| `cmd_innovation_core/issues/0002-establish-baselines-and-judge-monitor.md` | Implement fixed-summary/vector baselines, evidence recall comparator, subagent judge explanation, monitor trigger, random labels, and metrics. |
| `cmd_innovation_core/tdd/cmd_tracer_bullets.md` | Cycle 7 keeps judge explanation separate from CMD attribution; Cycle 8 rejects monitor payloads that emit final labels, ECS, memory writes, gold answers, or full failed traces. |

## Domain Boundary

Issue 0002 sits between the failed baseline output and the expensive **Counterfactual Replay** path:

```text
ProbeCase
  -> baseline memory outputs from issue 0001
  -> issue 0002 comparison layer
      -> fixed-summary baseline record
      -> vector-memory baseline record
      -> evidence-recall heuristic comparator
      -> Subagent Judge Baseline comparator
      -> random label sanity baseline
      -> Subagent Judge Monitor replay trigger
  -> issue 0001 replay path remains unchanged
      -> Oracle Retrieval Counterfactual Replay
      -> Recovery Gain
      -> Operation-Level Attribution
```

The important separation:

- **CMD-Audit** final label: comes from `assign_attribution(...)` over replay results.
- **Evidence recall heuristic**: a cheap comparator, not CMD attribution.
- **Subagent Judge Baseline**: a post-hoc explanation comparator, not CMD attribution.
- **Subagent Judge Monitor**: a high-recall trigger for expensive replay, not an attribution system.
- **CMD-Skill Adapter**: still deferred; issue 0002 does not integrate with a runtime memory agent.

## Module Map

| Module | Issue 0002 Role |
| --- | --- |
| `cmd_audit/baselines.py` | Owns memory baseline normalization, non-CMD comparator outputs, random sanity baseline, and monitor leak-safety. |
| `cmd_audit/metrics.py` | Owns system-level diagnosis predictions and comparison metrics. |
| `cmd_audit/harness.py` | Wires issue 0002 into the public CMD-Audit result while preserving replay-delta attribution. |
| `cmd_audit/cli.py` | Exposes issue 0002 artifacts through `python3 -m cmd_audit run`. |
| `cmd_audit/__init__.py` | Exports the small public surface for callers and tests. |
| `cmd_audit/labels.py` | Provides the V0 label order and validation used by comparators and metrics. |
| `tests/test_cmd_audit_issue2_baselines.py` | Behavior-level tests for the issue acceptance criteria. |

## Caller Graph

Main CLI path:

```text
cmd_audit.__main__
  -> cli.main
      -> models.load_probe_cases
      -> harness.run_cases
          -> harness.run_case
              -> baselines.run_baseline_suite
                  -> baselines.run_memory_baselines
                  -> baselines._select_comparison_baseline
                  -> baselines.run_evidence_recall_heuristic
                      -> baselines._observational_label
                          -> scoring.evidence_recall_from_memory_ids
                          -> scoring.evidence_recall_from_text
                  -> baselines.run_subagent_judge_baseline
                      -> baselines.run_evidence_recall_heuristic
                  -> baselines.run_random_label_baseline
                  -> baselines.run_subagent_judge_monitor
                      -> SubagentJudgeMonitorDecision.to_payload
                          -> baselines.validate_monitor_payload
                              -> baselines._reject_forbidden_monitor_fields
              -> replays.run_oracle_retrieval
              -> attribution.assign_attribution
      -> harness.write_attribution_table
      -> harness.write_comparison_metrics_table
          -> harness.diagnosis_predictions
          -> metrics.compute_diagnosis_metrics
              -> metrics._observed_labels
              -> metrics._top2_correct
              -> metrics._macro_f1
                  -> metrics._label_f1
```

Behavior-test path:

```text
tests/test_cmd_audit_issue2_baselines.py
  -> load_probe_cases
  -> run_baseline_suite
  -> run_case
  -> diagnosis_predictions
  -> compute_diagnosis_metrics
  -> validate_monitor_payload
  -> write_comparison_metrics_table
```

## Data Flow

Input fixture:

```text
data/probe_cases/v0_retrieval_error_case.json
```

Important fixture facts:

- `vector_memory` retrieves `mem-002`, a distractor.
- `fixed_summary` gives a generic summary and does not retrieve a memory item.
- Gold evidence lives in extracted memory `mem-001`.
- Gold answer is `Lisbon`.
- Perturbation label is `retrieval_error`.

Issue 0002 outputs:

```text
BaselineSuiteResult
  case_id
  memory_baselines
  evidence_recall_heuristic
  subagent_judge
  random_label
  monitor
```

Harness-level output:

```text
AuditResult
  baseline_suite
  replay
  attribution
```

Artifact output:

```text
artifacts/attribution_table.csv
artifacts/comparison_metrics.csv
```

## `cmd_audit/baselines.py`

This module owns issue 0002's non-CMD layer. Its docstring says it directly: baseline, comparator, and monitor surfaces for CMD-Audit issue 0002.

### Constant: `REQUIRED_MEMORY_BASELINES`

Definition:

```python
REQUIRED_MEMORY_BASELINES = ("fixed_summary", "vector_memory")
```

Role:

- Encodes issue 0002's required baseline systems.
- Used as the default `required_baselines` argument in `run_memory_baselines(...)`.
- Makes every probe case prove that both a fixed-summary baseline and vector-memory baseline have been specified.

Failure behavior:

- If either name is missing from `case.baseline_outputs`, `run_memory_baselines(...)` raises `BaselineConfigurationError`.

Domain meaning:

- These are **Memory-Augmented Agent** baseline behaviors, not CMD interventions.
- The values come from the issue requirement to specify fixed-summary and vector-memory behavior for every probe case.

### Constant: `FORBIDDEN_MONITOR_FIELDS`

Definition:

```python
FORBIDDEN_MONITOR_FIELDS = frozenset({...})
```

Current rejected keys:

- label-like fields: `label`, `labels`, `final_label`, `predicted_label`, `diagnosis_label`, `cmd_label`, `attribution`, `attribution_label`, `replay_label`, `top2_labels`
- repair/ECS fields: `ecs`, `error_cause_solution`, `repair_guidance`, `corrected_memory`
- memory-write fields: `memory_write`, `memory_writes`
- gold-data fields: `gold_answer`, `gold_evidence`
- trace or dataset fields: `raw_events`, `extracted_memory`, `baseline_outputs`, `full_trace`, `full_failed_trace`, `failed_trace`

Role:

- Defines the leak-safety boundary for **Subagent Judge Monitor** payloads.
- Used by `_reject_forbidden_monitor_fields(...)`.
- Tested through `test_monitor_payload_can_trigger_replay_without_forbidden_outputs` and `test_monitor_rejects_final_labels_ecs_memory_writes_gold_answers_and_full_traces`.

Domain meaning:

- The monitor can say "replay should run".
- It cannot output final **Operation-Level Attribution**, **Error-Cause-Solution**, **User Memory** writes, gold answers/evidence, or full failed traces.

### Exception: `BaselineConfigurationError`

Definition:

```python
class BaselineConfigurationError(ValueError):
    """Raised when a probe case lacks required baseline outputs."""
```

Raised by:

- `run_memory_baselines(...)`

Failure cases:

- Duplicate baseline names in `case.baseline_outputs`.
- Missing names from `REQUIRED_MEMORY_BASELINES`.

Why it exists:

- Issue 0002 is invalid if a probe case cannot produce both required baseline records.
- This is a case-contract failure, not a replay failure.

### Exception: `LeakSafeMonitorError`

Definition:

```python
class LeakSafeMonitorError(ValueError):
    """Raised when the monitor attempts to expose forbidden diagnosis payloads."""
```

Raised by:

- `_reject_forbidden_monitor_fields(...)`

Used by:

- `validate_monitor_payload(...)`
- `SubagentJudgeMonitorDecision.to_payload(...)`

Why it exists:

- It enforces the `CLAUDE.md`, `TASK.md`, and `CONTEXT.md` boundary that the monitor cannot emit final labels, ECS, memory writes, gold answers, or full failed traces.

### Dataclass: `MemoryBaselineRun`

Fields:

```python
baseline_name: str
answer: str
retrieved_memory_ids: tuple[str, ...]
answer_score: float
evidence_score: float
injected_context: str
```

Role:

- Normalized runtime view of a `BaselineOutput`.
- Keeps baseline behavior available in issue 0002 without changing the issue 0001 `ProbeCase` contract.

Domain mapping:

- `baseline_name`: fixed-summary or vector-memory baseline system.
- `answer`: failed output from the baseline **Memory-Augmented Agent**.
- `retrieved_memory_ids`: memory items retrieved by the baseline memory system.
- `answer_score`: score against `gold_answer`, used for comparison metrics.
- `evidence_score`: score against `gold_evidence`, used by monitor and diagnostics.
- `injected_context`: context supplied to the baseline before answer generation.

#### `MemoryBaselineRun.from_output(output)`

Signature:

```python
@classmethod
def from_output(cls, output: BaselineOutput) -> "MemoryBaselineRun"
```

Inputs:

- `output`: the issue 0001 `BaselineOutput` stored in a probe case.

Returns:

- A `MemoryBaselineRun` with the same baseline name, answer, retrieved IDs, scores, and injected context.

Callers:

- `run_memory_baselines(...)`

Reason for the method:

- Keeps issue 0002 output independent from issue 0001 model internals while preserving the same values.

#### `MemoryBaselineRun.failed`

Signature:

```python
@property
def failed(self) -> bool
```

Returns:

- `True` when `answer_score < 1.0`.
- `False` when the baseline answer is fully correct under the current deterministic scorer.

Callers:

- Tests use it to assert both current fixture baselines fail.

Domain meaning:

- The baseline is a failed starting point before CMD replay.

### Dataclass: `ComparatorResult`

Fields:

```python
comparator_name: str
predicted_label: str
top2_labels: tuple[str, ...]
explanation: str
cost_per_diagnosis: float
uses_counterfactual_replay: bool = False
```

Role:

- Common result shape for non-CMD label-producing comparators.
- Used by evidence recall heuristic, subagent judge baseline, and random label baseline.

Domain boundary:

- `predicted_label` is a comparator prediction, not final CMD attribution.
- `uses_counterfactual_replay` is explicitly `False` for all issue 0002 comparators.

#### `ComparatorResult.__post_init__()`

Signature:

```python
def __post_init__(self) -> None
```

Behavior:

- Calls `validate_v0_label(...)` for `predicted_label`.
- Calls `validate_v0_label(...)` for every label in `top2_labels`.

Why it matters:

- Issue 0002 comparators must stay inside the same V0 six-label boundary:
  - `write_error`
  - `compression_error`
  - `premature_extraction_error`
  - `retrieval_error`
  - `injection_error`
  - `reasoning_error`
- Comparators cannot introduce bad memory item labels or deferred pipeline labels into the comparison table.

### Dataclass: `SubagentJudgeMonitorDecision`

Fields:

```python
should_trigger_replay: bool
risk_score: float
reasons: tuple[str, ...]
trace_summary: str
cost_per_decision: float = 0.2
```

Role:

- Leak-safe monitor decision.
- Represents only whether expensive CMD replay should run, plus a small risk summary.

Allowed payload content:

- trigger boolean
- risk score
- short reasons
- aggregate trace summary
- monitor cost

Forbidden content:

- final label
- ECS
- memory writes
- gold answers/evidence
- full failed trace

#### `SubagentJudgeMonitorDecision.to_payload()`

Signature:

```python
def to_payload(self) -> dict[str, Any]
```

Behavior:

1. Builds a dict with these keys:
   - `should_trigger_replay`
   - `risk_score`
   - `reasons`
   - `trace_summary`
   - `cost_per_decision`
2. Calls `validate_monitor_payload(payload)`.
3. Returns the validated payload.

Callers:

- `run_subagent_judge_monitor(...)` calls it immediately before returning the decision.
- Tests call it directly to verify allowed payload shape.

Why the validation call is inside this method:

- Every serialization path for monitor decisions must pass the leak-safety gate.

### Dataclass: `BaselineSuiteResult`

Fields:

```python
case_id: str
memory_baselines: tuple[MemoryBaselineRun, ...]
evidence_recall_heuristic: ComparatorResult
subagent_judge: ComparatorResult
random_label: ComparatorResult
monitor: SubagentJudgeMonitorDecision
```

Role:

- Aggregates all issue 0002 outputs for one probe case.
- Stored on `AuditResult.baseline_suite`.

Domain mapping:

- `memory_baselines`: observed behavior of fixed-summary and vector-memory memory systems.
- `evidence_recall_heuristic`: cheap retrieval-centered comparator.
- `subagent_judge`: post-hoc observational explanation comparator.
- `random_label`: sanity baseline for attribution metrics.
- `monitor`: high-recall replay trigger.

#### `BaselineSuiteResult.comparator_results`

Signature:

```python
@property
def comparator_results(self) -> tuple[ComparatorResult, ...]
```

Returns:

```python
(
    self.evidence_recall_heuristic,
    self.subagent_judge,
    self.random_label,
)
```

Callers:

- `harness.diagnosis_predictions(...)`

Why it exists:

- Keeps metric generation generic over comparator systems without mixing monitor outputs into final attribution metrics.

### Function: `run_baseline_suite(case)`

Signature:

```python
def run_baseline_suite(case: ProbeCase) -> BaselineSuiteResult
```

Inputs:

- `case`: one validated `ProbeCase`.

Returns:

- `BaselineSuiteResult`.

Step-by-step behavior:

1. Calls `run_memory_baselines(case)` to validate and normalize baseline memory system outputs.
2. Calls `_select_comparison_baseline(case)` to pick the baseline trace used by comparators.
3. Calls `run_evidence_recall_heuristic(case, comparison_baseline)`.
4. Calls `run_subagent_judge_baseline(case, comparison_baseline)`.
5. Calls `run_random_label_baseline(case)`.
6. Calls `run_subagent_judge_monitor(case, comparison_baseline)`.
7. Packs all outputs into `BaselineSuiteResult`.

Callers:

- `harness.run_case(...)`
- `tests/test_cmd_audit_issue2_baselines.py`
- Public export through `cmd_audit.__init__`

Boundary guarantee:

- The function does not call any replay function.
- It does not assign CMD attribution.
- It creates comparison data that sits beside, not inside, `AuditResult.attribution`.

### Function: `run_memory_baselines(case, required_baselines=REQUIRED_MEMORY_BASELINES)`

Signature:

```python
def run_memory_baselines(
    case: ProbeCase,
    required_baselines: tuple[str, ...] = REQUIRED_MEMORY_BASELINES,
) -> tuple[MemoryBaselineRun, ...]
```

Inputs:

- `case`: one probe case.
- `required_baselines`: names that must exist in `case.baseline_outputs`.

Returns:

- Ordered tuple of `MemoryBaselineRun`.

Ordering:

1. Required baseline names in `required_baselines` order:
   - `fixed_summary`
   - `vector_memory`
2. Any extra baseline outputs from the case, preserving their fixture order.

Validation:

- Rejects duplicate `baseline_name`.
- Rejects missing required baseline names.

Callers:

- `run_baseline_suite(...)`

Domain meaning:

- The probe case must specify both baseline memory system behaviors before CMD comparisons are meaningful.

### Function: `run_evidence_recall_heuristic(case, baseline=None)`

Signature:

```python
def run_evidence_recall_heuristic(
    case: ProbeCase,
    baseline: BaselineOutput | None = None,
) -> ComparatorResult
```

Inputs:

- `case`: one probe case.
- `baseline`: optional baseline trace. If omitted, `_select_comparison_baseline(case)` is used.

Returns:

- `ComparatorResult` with:
  - `comparator_name="evidence_recall"`
  - `predicted_label` from `_observational_label(...)`
  - single-entry `top2_labels`
  - natural-language rationale
  - `cost_per_diagnosis=0.05`
  - `uses_counterfactual_replay=False`

Callers:

- `run_baseline_suite(...)`
- `run_subagent_judge_baseline(...)`

Boundary guarantee:

- It does not run counterfactual replay.
- It is an observational comparator, not CMD attribution.

Current fixture behavior:

- Extracted memory contains the gold memory item.
- Vector-memory baseline retrieved a distractor instead.
- Heuristic predicts comparator label `retrieval_error`.

### Function: `run_subagent_judge_baseline(case, baseline=None)`

Signature:

```python
def run_subagent_judge_baseline(
    case: ProbeCase,
    baseline: BaselineOutput | None = None,
) -> ComparatorResult
```

Inputs:

- `case`: one probe case.
- `baseline`: optional baseline trace. If omitted, `_select_comparison_baseline(case)` is used.

Returns:

- `ComparatorResult` with:
  - `comparator_name="subagent_judge"`
  - `predicted_label` copied from the evidence recall heuristic
  - `top2_labels` copied from the evidence recall heuristic
  - a post-hoc explanation string over the failed trace
  - `cost_per_diagnosis=1.0`
  - `uses_counterfactual_replay=False`

Internal calls:

- Calls `run_evidence_recall_heuristic(case, baseline)` to reuse the cheap observational label.

Why this shape:

- V0 does not invoke a real LLM/subagent from tests.
- The deterministic placeholder preserves the research contract: a judge-like explanation can be recorded and compared, but it cannot become CMD attribution.

Domain boundary:

- This implements **Subagent Judge Baseline**, not **Subagent Judge Monitor** and not **CMD-Audit** attribution.

### Function: `run_random_label_baseline(case)`

Signature:

```python
def run_random_label_baseline(case: ProbeCase) -> ComparatorResult
```

Inputs:

- `case`: one probe case.

Returns:

- `ComparatorResult` with:
  - `comparator_name="random_label"`
  - deterministic pseudo-random `predicted_label`
  - deterministic pseudo-random two-label tuple
  - `cost_per_diagnosis=0.01`
  - `uses_counterfactual_replay=False`

Algorithm:

1. Hashes `case.case_id` with SHA-256.
2. Uses the first byte to choose the top-1 label from `V0_PIPELINE_LABEL_ORDER`.
3. Uses the second byte to choose a different top-2 label.

Why deterministic:

- Test results and artifacts remain reproducible.
- Random baseline can still serve as a sanity check without nondeterministic CI behavior.

Domain meaning:

- It is not a meaningful diagnosis system.
- It exists to reveal whether CMD and stronger comparators beat chance-level attribution.

### Function: `run_subagent_judge_monitor(case, baseline=None, *, trigger_threshold=0.5)`

Signature:

```python
def run_subagent_judge_monitor(
    case: ProbeCase,
    baseline: BaselineOutput | None = None,
    *,
    trigger_threshold: float = 0.5,
) -> SubagentJudgeMonitorDecision
```

Inputs:

- `case`: one probe case.
- `baseline`: optional baseline trace. If omitted, `_select_comparison_baseline(case)` is used.
- `trigger_threshold`: minimum risk score required to trigger replay.

Risk scoring:

- Add `0.5` if `baseline.answer_score < 1.0`.
- Add `0.4` if `baseline.evidence_score < 1.0`.
- Add `0.1` if `baseline.retrieved_memory_ids` is empty.
- Cap total risk at `1.0`.

Returns:

- `SubagentJudgeMonitorDecision`.

Internal validation:

- Builds a decision.
- Calls `decision.to_payload()`.
- `to_payload()` calls `validate_monitor_payload(...)`.

Current fixture behavior:

- Vector-memory baseline answer score is `0.0`.
- Vector-memory baseline evidence score is `0.0`.
- It did retrieve one memory item, so no no-retrieval bonus.
- Risk score is `0.9`.
- With threshold `0.5`, `should_trigger_replay=True`.

Domain boundary:

- This is **Subagent Judge Monitor** behavior.
- It can trigger expensive replay.
- It cannot provide the final label or repair content.

### Function: `validate_monitor_payload(payload)`

Signature:

```python
def validate_monitor_payload(payload: dict[str, Any]) -> dict[str, Any]
```

Inputs:

- Any dict intended to represent a monitor payload.

Returns:

- The same payload if valid.

Raises:

- `LeakSafeMonitorError` when a forbidden key appears anywhere inside the payload.

Callers:

- `SubagentJudgeMonitorDecision.to_payload(...)`
- Tests that intentionally pass forbidden keys.

Why it returns the payload:

- It can be used inline in future adapter code without changing the data.

### Helper: `_reject_forbidden_monitor_fields(value)`

Signature:

```python
def _reject_forbidden_monitor_fields(value: Any) -> None
```

Inputs:

- Any nested value.

Behavior:

- If `value` is a dict:
  - checks each key against `FORBIDDEN_MONITOR_FIELDS`;
  - recurses into each nested value.
- If `value` is a list or tuple:
  - recurses into each element.
- Other values are ignored.

Raises:

- `LeakSafeMonitorError` when any forbidden field is found at any nesting level.

Why recursive:

- A monitor must not hide `gold_answer`, `final_label`, `ecs`, or full trace content inside nested metadata.

### Helper: `_select_comparison_baseline(case)`

Signature:

```python
def _select_comparison_baseline(case: ProbeCase) -> BaselineOutput
```

Behavior:

- Returns the first baseline output named `vector_memory` if present.
- Otherwise returns `case.primary_baseline`.

Callers:

- `run_baseline_suite(...)`
- `run_evidence_recall_heuristic(...)`
- `run_subagent_judge_baseline(...)`
- `run_subagent_judge_monitor(...)`

Why vector-memory is preferred:

- Issue 0002 explicitly evaluates vector-memory retrieval.
- Evidence recall heuristic is most meaningful over a retrieval baseline.

Boundary note:

- The selection is only for comparator and monitor observation.
- It does not decide final CMD attribution.

### Helper: `_observational_label(case, baseline)`

Signature:

```python
def _observational_label(case: ProbeCase, baseline: BaselineOutput) -> tuple[str, str]
```

Inputs:

- `case`: one probe case.
- `baseline`: selected failed baseline trace.

Returns:

- `(predicted_label, rationale)`

Scores computed:

- `retrieved_recall`: whether `baseline.retrieved_memory_ids` cover gold memory IDs.
- `extracted_recall`: whether all extracted memory IDs include gold memory IDs.
- `context_recall`: whether baseline injected context text includes required gold phrases.
- `raw_event_recall`: whether raw event text includes required gold phrases.
- `has_gold_memory_pointer`: whether any gold evidence points to a source memory ID.

Rule order:

1. If retrieved IDs contain gold evidence but injected context lacks the evidence, return `injection_error`.
2. If injected context recalls evidence but answer score fails, return `reasoning_error`.
3. If extracted memory contains gold evidence but retrieved IDs miss it, return `retrieval_error`.
4. If raw events contain evidence but extracted memory does not:
   - return `compression_error` when gold evidence had a memory pointer;
   - return `premature_extraction_error` when no recoverable extracted memory points to it.
5. Otherwise return `write_error`.

Why the order matters:

- It keeps `retrieval_error` restricted to the case where recoverable extracted memory exists but baseline retrieval missed it.
- It protects the **Premature Extraction Error** boundary by checking raw event recall versus extracted memory recall.
- It remains a heuristic comparator, not an intervention-grounded attribution result.

## `cmd_audit/metrics.py`

This module owns issue 0002's comparison metric surface.

### Dataclass: `DiagnosisPrediction`

Fields:

```python
system_name: str
case_id: str
gold_label: str
predicted_label: str
top2_labels: tuple[str, ...]
cost_per_diagnosis: float
```

Role:

- One row of diagnosis output for one system on one case.
- Used for both CMD-Audit and comparator systems.

System names currently produced:

- `CMD-Audit`
- `evidence_recall`
- `subagent_judge`
- `random_label`

#### `DiagnosisPrediction.__post_init__()`

Signature:

```python
def __post_init__(self) -> None
```

Behavior:

- Validates `gold_label`.
- Validates `predicted_label`.
- Validates every label in `top2_labels`.

Why it matters:

- Keeps comparison metrics aligned with the V0 attribution boundary.
- Prevents item labels or deferred labels from entering comparison results.

### Dataclass: `DiagnosisMetrics`

Fields:

```python
system_name: str
attribution_accuracy: float
macro_f1: float
top2_accuracy: float
cost_per_diagnosis: float
```

Role:

- Aggregated metric row for one system.
- Written to `artifacts/comparison_metrics.csv`.

Metric meaning:

- `attribution_accuracy`: top-1 label correctness against the known perturbation label.
- `macro_f1`: mean F1 over observed labels unless a fixed label set is provided.
- `top2_accuracy`: whether the gold label appears in the system's top-2 tuple.
- `cost_per_diagnosis`: average cost unit for the system.

### Function: `compute_diagnosis_metrics(predictions, *, labels=None)`

Signature:

```python
def compute_diagnosis_metrics(
    predictions: Iterable[DiagnosisPrediction],
    *,
    labels: tuple[str, ...] | None = None,
) -> dict[str, DiagnosisMetrics]
```

Inputs:

- `predictions`: rows from `harness.diagnosis_predictions(...)`.
- `labels`: optional explicit label set for macro F1.

Returns:

- Dict keyed by `system_name`.

Step-by-step behavior:

1. Groups predictions by `system_name`.
2. For each system:
   - chooses `labels` if provided, otherwise `_observed_labels(rows)`;
   - counts top-1 correct rows;
   - counts top-2 correct rows with `_top2_correct(...)`;
   - sums cost;
   - computes average cost;
   - computes macro F1 with `_macro_f1(...)`.
3. Returns one `DiagnosisMetrics` per system.

Callers:

- `harness.write_comparison_metrics_table(...)`
- Tests

Domain meaning:

- This is the first evidence surface for CMD-vs-heuristic-vs-subagent judge comparison.

### Helper: `_observed_labels(rows)`

Signature:

```python
def _observed_labels(rows: list[DiagnosisPrediction]) -> tuple[str, ...]
```

Behavior:

- Builds a sorted set from gold labels and predicted labels observed in the system's rows.

Why observed labels:

- Current V0 fixture set has one case.
- Observed-label macro F1 avoids reporting zero across all six labels before the dataset is expanded.
- Future larger runs can pass the full V0 label set through the `labels` argument.

### Helper: `_top2_correct(row)`

Signature:

```python
def _top2_correct(row: DiagnosisPrediction) -> bool
```

Behavior:

- Uses `row.top2_labels` when present.
- Falls back to `(row.predicted_label,)` when top-2 is empty.
- Returns whether `row.gold_label` appears in that tuple.

Why fallback exists:

- Some systems may only produce top-1 labels in future issue slices.

### Helper: `_macro_f1(rows, labels)`

Signature:

```python
def _macro_f1(rows: list[DiagnosisPrediction], labels: tuple[str, ...]) -> float
```

Behavior:

- Returns `0.0` if no labels exist.
- Otherwise computes `_label_f1(rows, label)` for each label and averages.

Domain meaning:

- Macro F1 is required by issue 0002 and later supports claim gating for CMD attribution quality.

### Helper: `_label_f1(rows, label)`

Signature:

```python
def _label_f1(rows: list[DiagnosisPrediction], label: str) -> float
```

Behavior:

1. Counts true positives:
   - `gold_label == label and predicted_label == label`
2. Counts false positives:
   - `gold_label != label and predicted_label == label`
3. Counts false negatives:
   - `gold_label == label and predicted_label != label`
4. Computes precision and recall with zero-denominator protection.
5. Returns harmonic mean, or `0.0` if precision plus recall is zero.

Why implemented locally:

- Keeps the V0 harness dependency-free.
- Matches `pyproject.toml`, which currently has no dependencies.

## `cmd_audit/harness.py` Integration

Issue 0002 extends the public harness but does not replace issue 0001 behavior.

### Constant: `CMD_REPLAY_COST_UNITS`

Definition:

```python
CMD_REPLAY_COST_UNITS = 5.0
```

Role:

- Placeholder unit cost for CMD replay diagnosis.
- Used only in `diagnosis_predictions(...)` for comparison metrics.

Interpretation:

- CMD-Audit cost is monitor cost plus replay cost.
- Current value is a deterministic V0 proxy, not a measured token or wall-clock cost.

### Dataclass field: `AuditResult.baseline_suite`

Added field:

```python
baseline_suite: BaselineSuiteResult
```

Role:

- Attaches issue 0002 outputs to every harness result.
- Keeps comparator and monitor outputs available without mixing them into `attribution`.

Boundary:

- `AuditResult.attribution` remains CMD replay attribution.
- `AuditResult.baseline_suite.subagent_judge.predicted_label` remains comparator output.

### Function: `run_case(case)`

Issue 0002 behavior:

1. Calls `run_baseline_suite(case)` before replay.
2. Runs existing `run_oracle_retrieval(case)`.
3. Runs existing `assign_attribution((replay,))`.
4. Returns `AuditResult(..., baseline_suite=baseline_suite)`.

Why this order:

- The monitor/comparator layer observes the failed baseline trace before expensive replay.
- Final label still comes from replay-delta attribution.

### Function: `diagnosis_predictions(result)`

Signature:

```python
def diagnosis_predictions(result: AuditResult) -> tuple[DiagnosisPrediction, ...]
```

Inputs:

- One `AuditResult`.

Returns:

- One `DiagnosisPrediction` for `CMD-Audit`.
- One `DiagnosisPrediction` for each comparator in `result.baseline_suite.comparator_results`.

CMD-Audit prediction row:

- `system_name="CMD-Audit"`
- `gold_label=result.perturbation_label`
- `predicted_label=result.attribution.predicted_label`
- `top2_labels=result.attribution.top2_labels`
- `cost_per_diagnosis=result.baseline_suite.monitor.cost_per_decision + CMD_REPLAY_COST_UNITS`

Comparator prediction rows:

- `system_name=comparator.comparator_name`
- `gold_label=result.perturbation_label`
- `predicted_label=comparator.predicted_label`
- `top2_labels=comparator.top2_labels`
- `cost_per_diagnosis=comparator.cost_per_diagnosis`

Callers:

- `write_comparison_metrics_table(...)`
- Tests

Boundary guarantee:

- It creates comparison rows but does not mutate attribution.

### Function: `write_comparison_metrics_table(results, output_path)`

Signature:

```python
def write_comparison_metrics_table(
    results: list[AuditResult],
    output_path: str | Path,
) -> None
```

Inputs:

- `results`: output from `run_cases(...)`.
- `output_path`: CSV target.

Behavior:

1. Flattens `diagnosis_predictions(result)` across all results.
2. Calls `compute_diagnosis_metrics(predictions)`.
3. Creates parent directories.
4. Writes CSV columns:
   - `system_name`
   - `attribution_accuracy`
   - `macro_f1`
   - `top2_accuracy`
   - `cost_per_diagnosis`
5. Sorts systems by name for deterministic output.

Output artifact:

```text
artifacts/comparison_metrics.csv
```

## `cmd_audit/cli.py` Integration

### Function: `main(argv=None)`

Issue 0002 additions:

- Adds `--metrics-out`, defaulting to `artifacts/comparison_metrics.csv`.
- After `run_cases(cases)`:
  - calls `write_attribution_table(results, args.out)`;
  - calls `write_comparison_metrics_table(results, args.metrics_out)`.
- Prints both artifact paths.

CLI command:

```bash
python3 -m cmd_audit run
```

Current default outputs:

```text
artifacts/attribution_table.csv
artifacts/comparison_metrics.csv
```

## `cmd_audit/__init__.py` Public Surface

Issue 0002 exports:

- `BaselineSuiteResult`
- `DiagnosisMetrics`
- `DiagnosisPrediction`
- `V0_PIPELINE_LABEL_ORDER`
- `compute_diagnosis_metrics`
- `diagnosis_predictions`
- `run_baseline_suite`
- `write_comparison_metrics_table`

Why export them:

- Tests and future issue slices can use the stable public surface.
- The harness remains standalone and does not expose a CMD-Skill Adapter.

## `cmd_audit/labels.py` Support

Issue 0002 adds and uses:

```python
V0_PIPELINE_LABEL_ORDER = (
    "write_error",
    "compression_error",
    "premature_extraction_error",
    "retrieval_error",
    "injection_error",
    "reasoning_error",
)
```

Role:

- Provides deterministic ordering for `run_random_label_baseline(...)`.
- Keeps random baseline constrained to the V0 core label set.

Existing `validate_v0_label(...)` is reused by:

- `ComparatorResult.__post_init__()`
- `DiagnosisPrediction.__post_init__()`

This ensures comparator predictions and metric rows cannot leave V0 attribution scope.

## Test Coverage

Test file:

```text
tests/test_cmd_audit_issue2_baselines.py
```

### `BaselineAndComparatorTest.test_issue2_baseline_suite_keeps_comparators_separate_from_cmd`

Verifies:

- `run_baseline_suite(...)` returns both `fixed_summary` and `vector_memory`.
- Both current fixture baselines fail.
- Evidence recall comparator predicts `retrieval_error`.
- Evidence recall does not use counterfactual replay.
- Subagent judge comparator is named `subagent_judge`.
- Subagent judge explanation includes `post-hoc`.
- Subagent judge does not use counterfactual replay.
- Random baseline is present as `random_label`.

Acceptance criteria covered:

- fixed-summary/vector baseline behavior;
- evidence recall comparator;
- subagent judge comparator;
- random label baseline.

### `BaselineAndComparatorTest.test_run_case_exposes_baseline_suite_but_cmd_label_still_comes_from_replay`

Verifies:

- `run_case(...)` still predicts `retrieval_error` through CMD attribution.
- Top replay remains `oracle_retrieval`.
- Subagent judge comparator label is available but is not named `CMD-Audit`.

Acceptance criteria covered:

- subagent judge cannot directly set CMD attribution.

### `SubagentJudgeMonitorBoundaryTest.test_monitor_payload_can_trigger_replay_without_forbidden_outputs`

Verifies:

- Monitor payload says `should_trigger_replay=True`.
- Payload keys do not intersect `FORBIDDEN_MONITOR_FIELDS`.
- Payload string does not contain the fixture gold answer.
- Payload string does not contain the final label `retrieval_error`.

Acceptance criteria covered:

- monitor can trigger replay;
- monitor is leak-safe.

### `SubagentJudgeMonitorBoundaryTest.test_monitor_rejects_final_labels_ecs_memory_writes_gold_answers_and_full_traces`

Verifies:

- `validate_monitor_payload(...)` raises `LeakSafeMonitorError` for:
  - `final_label`
  - `ecs`
  - `memory_writes`
  - `gold_answer`
  - `full_failed_trace`

Acceptance criteria covered:

- monitor cannot emit final label, ECS, memory writes, gold answer, or full failed trace.

### `ComparisonMetricsTest.test_comparison_metrics_include_accuracy_macro_f1_top2_and_cost`

Verifies:

- Metrics include:
  - `CMD-Audit`
  - `evidence_recall`
  - `subagent_judge`
  - `random_label`
- CMD-Audit attribution accuracy is `1.0` for the current fixture.
- CMD-Audit top-2 accuracy is `1.0`.
- CMD-Audit macro F1 is present.
- CMD-Audit cost is greater than zero.

Acceptance criteria covered:

- comparison metrics include attribution accuracy, macro F1, top-2 accuracy, and cost per diagnosis.

### `ComparisonMetricsTest.test_comparison_metrics_table_can_be_written`

Verifies:

- `write_comparison_metrics_table(...)` writes a CSV.
- CSV header contains the required metric columns.
- CSV contains `CMD-Audit`.

Acceptance criteria covered:

- metrics are artifact-ready.

## Current Artifact Semantics

Current `artifacts/comparison_metrics.csv` on the one-case fixture:

```text
system_name,attribution_accuracy,macro_f1,top2_accuracy,cost_per_diagnosis
CMD-Audit,1.000,1.000,1.000,5.200
evidence_recall,1.000,1.000,1.000,0.050
random_label,0.000,0.000,1.000,0.010
subagent_judge,1.000,1.000,1.000,1.000
```

Interpretation:

- This artifact proves the comparison pipeline exists.
- It does not support a paper claim that CMD beats heuristic or subagent judge yet, because the dataset has only one retrieval-error case and the heuristic/subagent comparator match it.
- It does satisfy the issue 0002 evidence gate for producing CMD-vs-comparator metric rows.

## Acceptance Criteria Traceability

| Issue 0002 Acceptance Criterion | Code Surface | Test Surface |
| --- | --- | --- |
| Fixed-summary and vector-memory baseline behavior is specified for each probe case. | `REQUIRED_MEMORY_BASELINES`, `run_memory_baselines(...)`, `MemoryBaselineRun` | `test_issue2_baseline_suite_keeps_comparators_separate_from_cmd` |
| Evidence recall heuristic output is specified as a comparator, not as CMD attribution. | `run_evidence_recall_heuristic(...)`, `ComparatorResult.uses_counterfactual_replay=False` | `test_issue2_baseline_suite_keeps_comparators_separate_from_cmd` |
| Subagent judge output is specified as post-hoc explanation over the trace. | `run_subagent_judge_baseline(...)` | `test_issue2_baseline_suite_keeps_comparators_separate_from_cmd` |
| Subagent judge monitor behavior is high-recall replay triggering, not final attribution. | `run_subagent_judge_monitor(...)`, `SubagentJudgeMonitorDecision` | `test_monitor_payload_can_trigger_replay_without_forbidden_outputs` |
| Monitor is leak-safe and cannot emit final labels, ECS, memory writes, gold answers, or full failed traces. | `FORBIDDEN_MONITOR_FIELDS`, `validate_monitor_payload(...)`, `_reject_forbidden_monitor_fields(...)` | `test_monitor_rejects_final_labels_ecs_memory_writes_gold_answers_and_full_traces` |
| Random label baseline is specified for attribution sanity checks. | `run_random_label_baseline(...)` | `test_issue2_baseline_suite_keeps_comparators_separate_from_cmd` |
| Comparison metrics include attribution accuracy, macro F1, top-2 accuracy, and cost per diagnosis. | `DiagnosisMetrics`, `compute_diagnosis_metrics(...)`, `write_comparison_metrics_table(...)` | `test_comparison_metrics_include_accuracy_macro_f1_top2_and_cost`, `test_comparison_metrics_table_can_be_written` |

## Verification

Commands:

```bash
python3 -m unittest discover -s tests -v
.venv/bin/python -m pytest -q
python3 -m compileall cmd_audit tests
python3 -m cmd_audit run
```

Expected state:

- All issue 0001 and issue 0002 tests pass.
- `artifacts/attribution_table.csv` is regenerated.
- `artifacts/comparison_metrics.csv` is regenerated.

## Known Limits Before Issue 0003

- Only Oracle Retrieval replay is executable.
- The subagent judge is deterministic and local; it is a comparator shape, not a real LLM call.
- Evidence recall and subagent judge are expected to match the single current retrieval-error fixture.
- Macro F1 currently uses observed labels unless a full label set is passed.
- No confusion matrix exists yet.
- No Post-Repair Context Replay exists yet.
- No Error-Cause-Solution record exists yet.

These limits are intentional. Issue 0002 completes the comparator and monitor boundary before issue 0003 expands the counterfactual attribution table.
