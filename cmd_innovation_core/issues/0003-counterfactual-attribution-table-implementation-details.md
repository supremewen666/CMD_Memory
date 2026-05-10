# Issue 0003 Implementation Details: Counterfactual Attribution Table

## Purpose

This document is the zoomed-out implementation map for issue 0003, `Generate the first counterfactual attribution table`.

Issue 0003 is the slice where **CMD-Audit** stops being a one-replay retrieval tracer bullet and becomes a bounded **V0 Replay Portfolio**:

```text
ProbeCase JSON
  -> ProbeCase contract validation
  -> baseline suite and leak-safe Subagent Judge Monitor
  -> V0 Replay Portfolio
      -> Oracle Write
      -> Oracle Compression
      -> Verbatim Event Oracle
      -> Oracle Retrieval
      -> Injection-Oracle
      -> Evidence-Given Reasoning
  -> replay scores and Recovery Gains
  -> Operation-Level Attribution
  -> Attribution Table
  -> Attribution Confusion Matrix
  -> CMD-vs-comparator metrics
```

The implemented slice still stops before **Error-Cause-Solution**, **Post-Repair Context Replay**, **Targeted Memory Fix**, **Failure Memory**, and **CMD-Skill Adapter** behavior. Those remain later issues.

## Source Requirements

The implementation follows these local documents.

| Source | Requirement Applied In Issue 0003 |
| --- | --- |
| `TASK.md` | Build issue 0003 after issue 0001/0002; run six V0 replays; produce attribution table, comparison metrics, and confusion matrix; keep issue 0004 as the next slice. |
| `CLAUDE.md` | Treat `cmd_innovation_core/` as source of truth; keep **CMD-Audit** separate from **CMD-Skill Adapter**; output only the six V0 pipeline labels. |
| `cmd_innovation_core/CONTEXT.md` | Use **V0 Core Label Set**, **Counterfactual Replay**, **Recovery Gain**, **Operation-Level Attribution**, **Premature Extraction Error**, **Subagent Judge Baseline**, and **Subagent Judge Monitor** consistently. |
| `cmd_innovation_core/prd/cmd_minimal_probe_prd.md` | Make the replay engine a deep module with a common result shape; convert replay deltas into top-1/top-2 attribution; compare CMD with heuristic and subagent judge baselines. |
| `cmd_innovation_core/issues/0003-generate-counterfactual-attribution-table.md` | Include Oracle Write, Oracle Compression, Oracle Retrieval, Verbatim Event Oracle, Injection-Oracle, and Evidence-Given Reasoning; write one recovery-gain column per replay; reject deferred labels. |
| `cmd_innovation_core/tdd/cmd_tracer_bullets.md` | Verify public behavior through the harness; preserve the Verbatim Event Oracle boundary; keep Subagent Judge outputs separate from final CMD attribution. |

## Domain Boundary

Issue 0003 owns the first **Counterfactual Replay** portfolio and attribution evidence artifacts.

It does own:

- running six V0 replay interventions;
- scoring each replay with answer score, evidence score, and Recovery Gain;
- mapping the strongest replay to an **Operation-Level Attribution** label;
- emitting top-1, top-2, ambiguity, per-replay gain columns, and diagnosis cost;
- writing a V0 attribution confusion matrix;
- keeping comparator outputs next to CMD output without letting them set CMD labels.

It does not own:

- **Error-Cause-Solution** construction;
- **Post-Repair Context Replay**;
- targeted memory repair actions;
- future **Failure Memory** retrieval;
- **CMD-Skill Adapter** integration;
- real BM25/vector/hybrid retrieval baselines from V0.5;
- deferred labels: `granularity_error`, `route_error`, `graph_error`, `safety_error`;
- bad-memory-item labels: `item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`.

## Current Code Artifacts

| Artifact | Role in issue 0003 |
| --- | --- |
| `cmd_audit/replays.py` | Owns the six V0 Counterfactual Replays and common `ReplayResult` shape. |
| `cmd_audit/attribution.py` | Converts replay Recovery Gains into Operation-Level Attribution. |
| `cmd_audit/harness.py` | Public case runner, attribution table writer, comparator metrics writer, and confusion matrix writer. |
| `cmd_audit/labels.py` | Defines the V0 label order, allowed label set, and replay-to-label map. |
| `cmd_audit/models.py` | Loads the probe contract and validates `source_memory_id` plus `source_event_id` references. |
| `cmd_audit/scoring.py` | Provides deterministic answer and evidence scorers used by replays and baselines. |
| `cmd_audit/baselines.py` | Supplies fixed-summary/vector baseline state, comparator labels, and leak-safe monitor output from issue 0002. |
| `cmd_audit/metrics.py` | Computes CMD-vs-comparator attribution accuracy, macro F1, top-2 accuracy, and cost. |
| `cmd_audit/cli.py` | Exposes issue 0003 through `python3 -m cmd_audit run`. |
| `cmd_audit/__main__.py` | Enables module execution. |
| `cmd_audit/__init__.py` | Exports the issue 0003 public API surface. |
| `data/probe_cases/v0_issue3_cases.json` | Six-case smoke suite: one case per V0 pipeline label. |
| `data/probe_cases/v0_premature_extraction_error_case.json` | Focused Verbatim Event Oracle boundary fixture. |
| `tests/test_cmd_audit_issue3_attribution_table.py` | Behavior-level issue 0003 tests. |
| `artifacts/attribution_table.csv` | Generated per-case attribution table with per-replay score and gain columns. |
| `artifacts/comparison_metrics.csv` | Generated CMD-vs-comparator metrics table. |
| `artifacts/attribution_confusion_matrix.csv` | Generated V0 attribution confusion matrix. |

## Zoom-Out Module Map

```text
cmd_audit.__main__
  -> cli.main
      -> models.load_probe_cases
          -> ProbeCase.from_mapping
              -> RawEvent.from_mapping
              -> MemoryItem.from_mapping
              -> GoldEvidence.from_mapping
              -> BaselineOutput.from_mapping
              -> ScoringSpec.from_mapping
              -> labels.validate_v0_label
              -> ProbeCase.validate
      -> harness.run_cases
          -> harness.run_case
              -> baselines.run_baseline_suite
                  -> run_memory_baselines
                  -> run_evidence_recall_heuristic
                  -> run_subagent_judge_baseline
                  -> run_random_label_baseline
                  -> run_subagent_judge_monitor
              -> replays.run_v0_replay_portfolio
                  -> run_oracle_write
                  -> run_oracle_compression
                  -> run_verbatim_event_oracle
                  -> run_oracle_retrieval
                  -> run_injection_oracle
                  -> run_evidence_given_reasoning
                  -> _score_recovered_evidence
                      -> scoring.evidence_recall_from_text
                      -> scoring.answer_score
              -> attribution.assign_attribution
                  -> attribution._label_for_replay
                  -> labels.validate_v0_label
      -> harness.write_attribution_table
      -> harness.write_comparison_metrics_table
          -> harness.diagnosis_predictions
          -> metrics.compute_diagnosis_metrics
      -> harness.write_confusion_matrix_table
```

Domain reading:

- `models.py` owns the **Memory Failure** probe contract.
- `baselines.py` owns non-CMD baseline/comparator/monitor state from issue 0002.
- `replays.py` owns issue 0003 **Counterfactual Replay** interventions.
- `scoring.py` owns deterministic evidence and answer scoring.
- `attribution.py` owns **Recovery Gain** ranking and **Operation-Level Attribution**.
- `harness.py` is the **CMD-Audit** public surface.
- `metrics.py` turns CMD and comparator predictions into claim-gating metrics.
- `cli.py` is a standalone research harness runner, not a **CMD-Skill Adapter**.

## Data Flow

Input:

```text
data/probe_cases/v0_issue3_cases.json
```

Output:

```text
artifacts/attribution_table.csv
artifacts/comparison_metrics.csv
artifacts/attribution_confusion_matrix.csv
```

For each case:

```text
ProbeCase
  -> BaselineSuiteResult
  -> tuple[ReplayResult, ...]
  -> AttributionResult
  -> AuditResult
```

For all cases:

```text
list[AuditResult]
  -> attribution_table.csv
  -> comparison_metrics.csv
  -> attribution_confusion_matrix.csv
```

## Probe Fixtures

### `data/probe_cases/v0_issue3_cases.json`

This fixture is the issue 0003 smoke suite. It contains one case per V0 pipeline label.

| Case ID | Gold label | Expected top replay | Boundary being tested |
| --- | --- | --- | --- |
| `v0-write-001` | `write_error` | `oracle_write` | Gold evidence is evaluator-known but not present in raw events or extracted memory. |
| `v0-compression-001` | `compression_error` | `oracle_compression` | Gold evidence points to a Memory Item, but the stored text lost required phrases. |
| `v0-premature-extraction-001` | `premature_extraction_error` | `verbatim_event_oracle` | Raw events preserve evidence, but no recoverable extracted Memory Item does. |
| `v0-retrieval-001` | `retrieval_error` | `oracle_retrieval` | Correct Memory Item exists but baseline retrieved a distractor. |
| `v0-injection-001` | `injection_error` | `injection_oracle` | Correct Memory Item was retrieved, but injected context omitted usable evidence. |
| `v0-reasoning-001` | `reasoning_error` | `evidence_given_reasoning` | Baseline context already contains evidence, but baseline answer is wrong. |

### `data/probe_cases/v0_premature_extraction_error_case.json`

This focused fixture protects the most important label boundary:

```text
raw event evidence recoverable
extracted memory evidence not recoverable
Oracle Retrieval gain = 0
Verbatim Event Oracle gain = 1
predicted label = premature_extraction_error
```

It deliberately uses `source_event_id` and omits `source_memory_id`. That represents extraction loss. It is not a malformed memory reference.

## Public Result Shapes

### `ReplayResult`

Defined in `cmd_audit/replays.py`.

Fields:

- `replay_name`: canonical replay key used by `REPLAY_TO_LABEL`.
- `answer`: replay answer string.
- `answer_score`: deterministic score against `gold_answer`.
- `evidence_score`: deterministic recall against gold evidence phrases.
- `evidence_block`: evidence text used by the replay.
- `recovery_gain`: replay answer score minus baseline answer score.
- `cost_units`: simple diagnosis-cost accounting value. Defaults to `1.0`.

### `AttributionResult`

Defined in `cmd_audit/attribution.py`.

Fields:

- `predicted_label`: V0 pipeline label mapped from top replay.
- `top_replay`: replay with the largest positive Recovery Gain.
- `recovery_gain`: gain of the top replay.
- `top2_labels`: labels within `tie_margin` of the top replay, capped to two labels.
- `is_ambiguous`: true when `top2_labels` has more than one label.

### `AuditResult`

Defined in `cmd_audit/harness.py`.

Fields:

- `case_id`
- `perturbation_label`
- `baseline_name`
- `baseline_answer_score`
- `baseline_evidence_score`
- `replays`
- `attribution`
- `baseline_suite`

Properties:

- `attribution_correct`
- `replay`
- `diagnosis_cost`

Methods:

- `replay_by_name(...)`

The old single-replay surface is preserved by `AuditResult.replay`, which returns the top replay. New issue 0003 behavior should use `AuditResult.replays` when it needs the full portfolio.

## Function-Level Contract

### `cmd_audit/replays.py`

This module owns issue 0003's **V0 Replay Portfolio**.

#### `ReplayResult`

Purpose:

- Immutable result shape shared by every Counterfactual Replay.

Used by:

- all `run_*` replay functions;
- `assign_attribution(...)`;
- `AuditResult.replays`;
- `write_attribution_table(...)`.

Domain meaning:

- One replay result is one controlled intervention over the **Memory Pipeline**.
- `recovery_gain` is the evidence used for **Operation-Level Attribution**.

#### `run_v0_replay_portfolio(case: ProbeCase) -> tuple[ReplayResult, ...]`

Purpose:

- Runs all six V0 replay interventions in stable table order.

Current order:

1. `oracle_write`
2. `oracle_compression`
3. `verbatim_event_oracle`
4. `oracle_retrieval`
5. `injection_oracle`
6. `evidence_given_reasoning`

Behavior:

- Calls each replay function once.
- Returns a tuple consumed directly by `assign_attribution(...)`.

Callers:

- `harness.run_case(...)`
- exported from `cmd_audit.__init__`
- tested by `test_issue3_suite_attributes_all_v0_pipeline_labels`

Why issue 0003 needs it:

- Centralizes the bounded V0 replay set so table generation, attribution, and tests do not invent separate replay lists.

Boundary:

- It must not include deferred V1/V2 replays such as route, graph, granularity, or safety.

#### `run_oracle_write(case: ProbeCase) -> ReplayResult`

Purpose:

- Diagnoses `write_error`.

Behavior:

- Builds `evidence_block` from gold evidence where both `source_memory_id` and `source_event_id` are absent.
- Scores that block through `_score_recovered_evidence(...)`.
- A positive gain means evaluator-known evidence was not present in raw events or extracted memory, so an Oracle Write intervention recovers it.

Fixture example:

- `v0-write-001`

Callers:

- `run_v0_replay_portfolio(...)`

Boundary:

- This replay uses gold evidence only as an oracle intervention for evaluation. It is not a production memory write.

#### `run_oracle_compression(case: ProbeCase) -> ReplayResult`

Purpose:

- Diagnoses `compression_error`.

Behavior:

- Looks up each gold evidence item's `source_memory_id`.
- If the Memory Item exists but its text does not satisfy `evidence_recall_from_text((evidence,), memory.text)`, the replay recovers `evidence.text`.
- Scores recovered evidence through `_score_recovered_evidence(...)`.

Fixture example:

- `v0-compression-001`

Why this is not retrieval:

- The gold evidence points at an extracted Memory Item, but that item's representation is too lossy to recover required phrases.

Callers:

- `run_v0_replay_portfolio(...)`

#### `run_oracle_retrieval(case: ProbeCase) -> ReplayResult`

Purpose:

- Diagnoses `retrieval_error`.

Behavior:

- Calls `_recover_extracted_gold_evidence(...)`.
- Scores recovered extracted-memory evidence through `_score_recovered_evidence(...)`.

Fixture example:

- `v0-retrieval-001`

Key boundary:

- Retrieval can recover only evidence that:
  - has `source_memory_id`;
  - points to an existing Memory Item;
  - is not already retrieved by the baseline;
  - is actually present in that Memory Item's text.

Why it skips already retrieved evidence:

- If the baseline retrieved the correct Memory Item but injection or reasoning failed, the failure is not a retrieval miss.

Callers:

- `run_v0_replay_portfolio(...)`
- legacy tests still inspect `result.replay` when this replay is top.

#### `run_verbatim_event_oracle(case: ProbeCase) -> ReplayResult`

Purpose:

- Diagnoses `premature_extraction_error`.

Behavior:

- Calls `_recover_raw_event_only_gold_evidence(...)`.
- Scores raw-event evidence through `_score_recovered_evidence(...)`.

Fixture examples:

- `v0_premature_extraction_error_case.json`
- `v0-premature-extraction-001` in `v0_issue3_cases.json`

Key boundary:

- Gold evidence must have `source_event_id`.
- Gold evidence must omit `source_memory_id` when no extracted Memory Item preserves it.
- This avoids encoding extraction loss as a broken memory pointer.

Callers:

- `run_v0_replay_portfolio(...)`

#### `run_injection_oracle(case: ProbeCase) -> ReplayResult`

Purpose:

- Diagnoses `injection_error`.

Behavior:

- Reads `case.primary_baseline`.
- If baseline `injected_context` already recalls all gold evidence, returns an empty block and zero gain. That prevents reasoning cases from being misattributed to injection.
- Otherwise, checks whether the baseline retrieved a Memory Item whose text contains gold evidence.
- If yes, recovers that Memory Item text as a clean evidence block.
- Scores through `_score_recovered_evidence(...)`.

Fixture example:

- `v0-injection-001`

Why this is not retrieval:

- The correct Memory Item was retrieved. The failure happened when evidence was formatted or injected into the model context.

Callers:

- `run_v0_replay_portfolio(...)`

#### `run_evidence_given_reasoning(case: ProbeCase) -> ReplayResult`

Purpose:

- Diagnoses `reasoning_error`.

Behavior:

- Reads `case.primary_baseline`.
- If baseline `injected_context` already recalls all gold evidence and `baseline.answer_score < 1.0`, uses baseline context as the evidence block.
- Otherwise, returns an empty evidence block.
- Scores through `_score_recovered_evidence(...)`.

Fixture example:

- `v0-reasoning-001`

Why this is not injection:

- The baseline context already has evidence. The failure is final answer reasoning over valid evidence.

Callers:

- `run_v0_replay_portfolio(...)`

#### `_score_recovered_evidence(case, replay_name, evidence_block) -> ReplayResult`

Purpose:

- Shared scoring helper for all replay functions.

Behavior:

- Computes `evidence_score = evidence_recall_from_text(case.gold_evidence, evidence_block)`.
- Sets replay `answer = case.gold_answer` only when `evidence_score == 1.0`.
- Computes `answer_score(answer, case.gold_answer)`.
- Computes `recovery_gain = recovered_answer_score - case.primary_baseline.answer_score`.
- Returns a `ReplayResult`.

Why issue 0003 needs it:

- Keeps replay functions shallow and makes all replay deltas comparable.

Boundary:

- This is synthetic-oracle scoring. It should not be mistaken for production answer generation.

#### `_recover_extracted_gold_evidence(case: ProbeCase) -> str`

Purpose:

- Helper for Oracle Retrieval.

Behavior:

- Builds `memory_by_id` from `case.extracted_memory`.
- Builds `baseline_retrieved_ids` from `case.primary_baseline.retrieved_memory_ids`.
- For each gold evidence:
  - skip if it has no `source_memory_id`;
  - skip if baseline already retrieved that memory id;
  - recover the Memory Item text only if it satisfies phrase recall for that evidence.
- Joins recovered memory texts with newlines.

Issue 0003 boundary:

- Prevents `injection_error` and `reasoning_error` cases from being falsely recovered by Oracle Retrieval.

#### `_recover_raw_event_only_gold_evidence(case: ProbeCase) -> str`

Purpose:

- Helper for Verbatim Event Oracle.

Behavior:

- Builds `event_by_id` from `case.raw_events`.
- For each gold evidence:
  - skip if it has `source_memory_id`;
  - recover raw event text when `source_event_id` exists and points to a real event.
- Joins recovered raw events with newlines.

Issue 0003 boundary:

- Recovers only raw-event-only evidence. Evidence that survived extraction belongs to retrieval/compression/injection/reasoning paths.

### `cmd_audit/attribution.py`

This module owns **Operation-Level Attribution** from replay deltas.

#### `AttributionResult`

Purpose:

- Immutable attribution output for one case.

Fields:

- `predicted_label`
- `top_replay`
- `recovery_gain`
- `top2_labels`
- `is_ambiguous`

Used by:

- `AuditResult.attribution`
- `write_attribution_table(...)`
- `diagnosis_predictions(...)`
- issue 0003 tests

#### `assign_attribution(replay_results, positive_gain_threshold=0.0, tie_margin=0.05) -> AttributionResult`

Purpose:

- Converts a tuple of `ReplayResult` objects into one CMD attribution.

Behavior:

- Requires at least one replay result.
- Sorts replay results by `recovery_gain` descending.
- Rejects the case if the top gain is not positive.
- Maps `top.replay_name` to a V0 label through `_label_for_replay(...)`.
- Builds `top2_labels` from replay labels within `tie_margin` of the top replay, capped to two labels.
- Marks `is_ambiguous` when more than one close label exists.

Callers:

- `harness.run_case(...)`
- direct tests in earlier issue coverage.

Issue 0003 meaning:

- Final CMD attribution comes from intervention-grounded Recovery Gains, not from heuristic or subagent judge outputs.

#### `_label_for_replay(replay_name: str) -> str`

Purpose:

- Maps a replay name to the V0 pipeline label it diagnoses.

Behavior:

- Reads `REPLAY_TO_LABEL`.
- Raises `ValueError` for unknown replay names.

Why issue 0003 needs it:

- Forces every replay in the portfolio to have an explicit label mapping.

### `cmd_audit/harness.py`

This module is the public **CMD-Audit** surface for issue 0003.

#### Constant: `REPLAY_TABLE_ORDER`

Definition:

```python
REPLAY_TABLE_ORDER = (
    "oracle_write",
    "oracle_compression",
    "verbatim_event_oracle",
    "oracle_retrieval",
    "injection_oracle",
    "evidence_given_reasoning",
)
```

Purpose:

- Keeps attribution table columns stable.
- Mirrors V0 replay order and V0 label order.

Used by:

- `write_attribution_table(...)`

#### `AuditResult`

Purpose:

- Public result object for one probe case.

Fields:

- `case_id`
- `perturbation_label`
- `baseline_name`
- `baseline_answer_score`
- `baseline_evidence_score`
- `replays`
- `attribution`
- `baseline_suite`

Issue 0003 change:

- `replays` replaces the old single replay storage.
- The `replay` property preserves the older top-replay convenience interface.

#### `AuditResult.attribution_correct`

Purpose:

- Convenience check for smoke fixtures and tables.

Behavior:

- Returns `attribution.predicted_label == perturbation_label`.

Used by:

- `write_attribution_table(...)`
- tests

#### `AuditResult.replay`

Purpose:

- Backward-compatible top-replay accessor.

Behavior:

- Calls `replay_by_name(self.attribution.top_replay)`.

Used by:

- existing retrieval tracer bullet tests;
- `write_attribution_table(...)` generic top replay columns.

#### `AuditResult.diagnosis_cost`

Purpose:

- Computes issue 0003 diagnosis cost.

Behavior:

- Adds `baseline_suite.monitor.cost_per_decision` to the sum of `replay.cost_units` for every replay in `replays`.
- With six default replay costs and monitor cost `0.2`, current smoke cost is `6.2`.

Used by:

- `write_attribution_table(...)`
- `diagnosis_predictions(...)`

#### `AuditResult.replay_by_name(replay_name: str) -> ReplayResult`

Purpose:

- Finds one replay result in the replay portfolio.

Behavior:

- Returns the matching replay by `replay_name`.
- Raises `KeyError` if the replay did not run.

Used by:

- `AuditResult.replay`
- tests that inspect named replays.

#### `run_case(case: ProbeCase) -> AuditResult`

Purpose:

- Runs the full issue 0003 public path for one case.

Behavior:

1. Calls `run_baseline_suite(case)`.
2. Calls `run_v0_replay_portfolio(case)`.
3. Calls `assign_attribution(replays)`.
4. Builds `AuditResult` from baseline, replays, attribution, and comparator context.

Callers:

- `run_cases(...)`
- tests

Boundary:

- Subagent judge and evidence-recall outputs are included in `baseline_suite`, but they do not set `attribution`.

#### `run_cases(cases: list[ProbeCase]) -> list[AuditResult]`

Purpose:

- Batch runner for CLI and tests.

Behavior:

- Calls `run_case(...)` for every loaded probe case.

Callers:

- `cli.main(...)`
- issue 0003 tests

#### `write_attribution_table(results, output_path) -> None`

Purpose:

- Writes the issue 0003 **Attribution Table**.

Behavior:

- Creates parent directories.
- Builds fieldnames with:
  - case identity;
  - gold and predicted labels;
  - top replay;
  - baseline scores;
  - generic top replay scores;
  - per-replay answer/evidence/gain columns for every replay in `REPLAY_TABLE_ORDER`;
  - top-2 labels;
  - ambiguity flag;
  - diagnosis cost;
  - correctness flag.
- Writes one row per `AuditResult`.

Generated file:

- `artifacts/attribution_table.csv`

Issue 0003 tests:

- `test_issue3_table_contains_per_replay_gain_columns`

#### `diagnosis_predictions(result: AuditResult) -> tuple[DiagnosisPrediction, ...]`

Purpose:

- Converts one `AuditResult` into comparable system predictions.

Behavior:

- Creates one `DiagnosisPrediction` for `CMD-Audit` using replay-delta attribution.
- Creates one prediction per comparator in `baseline_suite.comparator_results`:
  - evidence recall heuristic;
  - subagent judge baseline;
  - random label baseline.

Used by:

- `write_comparison_metrics_table(...)`
- issue 0002 tests

Boundary:

- Comparator predictions are reported for comparison only. They do not modify CMD attribution.

#### `write_comparison_metrics_table(results, output_path) -> None`

Purpose:

- Writes CMD-vs-comparator diagnosis metrics.

Behavior:

- Flattens `diagnosis_predictions(...)` for all results.
- Calls `compute_diagnosis_metrics(...)`.
- Writes `system_name`, `attribution_accuracy`, `macro_f1`, `top2_accuracy`, and `cost_per_diagnosis`.

Generated file:

- `artifacts/comparison_metrics.csv`

#### `write_confusion_matrix_table(results, output_path) -> None`

Purpose:

- Writes the V0 attribution confusion matrix required by the evidence gate.

Behavior:

- Initializes a square count table using `V0_PIPELINE_LABEL_ORDER`.
- Increments `counts[gold_label][predicted_label]` for every result.
- Writes one row per gold label.

Generated file:

- `artifacts/attribution_confusion_matrix.csv`

Issue 0003 tests:

- `test_confusion_matrix_contains_one_diagonal_count_per_v0_label`

### `cmd_audit/models.py`

Issue 0001 owns the full probe contract. Issue 0003 extends the contract boundary for raw-event-only evidence.

#### `GoldEvidence.from_mapping(...)`

Issue 0003 relevance:

- Allows `source_event_id` with no `source_memory_id`.
- This is required for `premature_extraction_error`.

#### `ProbeCase.validate(self) -> None`

Issue 0003 behavior:

- Still requires non-empty `raw_events`, `extracted_memory`, `gold_evidence`, and `baseline_outputs`.
- Validates that `source_memory_id`, when present, points to an existing extracted Memory Item.
- Validates that `source_event_id`, when present, points to an existing raw event.

Why issue 0003 needs it:

- A raw-event-only case is valid when evidence points to a real raw event and omits `source_memory_id`.
- A case with a missing `source_memory_id` remains invalid. That protects retrieval-error fixtures from hiding malformed references.

#### `load_probe_cases(path) -> list[ProbeCase]`

Issue 0003 behavior:

- Loads either one JSON object or a list of JSON objects.
- `v0_issue3_cases.json` uses the list form.
- `v0_premature_extraction_error_case.json` uses the single-object form.

### `cmd_audit/scoring.py`

Issue 0003 uses the deterministic scorers from issue 0001.

#### `answer_score(answer, gold_answer) -> float`

Purpose:

- Casefolded exact match with punctuation trimming.

Issue 0003 use:

- `_score_recovered_evidence(...)` computes replay answer score.

#### `evidence_recall_from_text(gold_evidence, text) -> float`

Purpose:

- Checks required phrases for each gold evidence item against an evidence block.

Issue 0003 use:

- Every replay uses this to decide whether its evidence block recovers the case.
- Replay helpers use it to avoid attributing retrieved/injected/reasoning cases to the wrong intervention.

#### `evidence_recall_from_memory_ids(case, memory_ids) -> float`

Issue 0003 use:

- Used by issue 0002 comparator logic that is included in issue 0003 outputs.

#### `_normalize(value: str) -> str`

Issue 0003 use:

- Supports answer scoring.

### `cmd_audit/labels.py`

#### `V0_PIPELINE_LABEL_ORDER`

Purpose:

- Defines the stable order of V0 labels.

Issue 0003 use:

- Drives confusion matrix row/column order.
- Used by tests to assert all labels are covered.

#### `REPLAY_TO_LABEL`

Purpose:

- Maps each replay name to its operation-level label.

Current issue 0003 mapping:

| Replay | Label |
| --- | --- |
| `oracle_write` | `write_error` |
| `oracle_compression` | `compression_error` |
| `verbatim_event_oracle` | `premature_extraction_error` |
| `oracle_retrieval` | `retrieval_error` |
| `injection_oracle` | `injection_error` |
| `evidence_given_reasoning` | `reasoning_error` |

Used by:

- `attribution._label_for_replay(...)`

#### `validate_v0_label(label: str) -> str`

Issue 0003 use:

- Validates fixture `perturbation_label`.
- Validates predicted labels in attribution and comparator metrics.
- Prevents issue 0003 from accidentally admitting deferred or bad-memory-item labels.

### `cmd_audit/metrics.py`

Issue 0002 owns most metric details. Issue 0003 consumes those metrics as part of the attribution evidence bundle.

#### `DiagnosisPrediction`

Issue 0003 use:

- Represents CMD-Audit and comparator outputs in one normalized shape.
- Validates gold, predicted, and top-2 labels through `validate_v0_label(...)`.

#### `DiagnosisMetrics`

Issue 0003 use:

- Row shape for `comparison_metrics.csv`.

#### `compute_diagnosis_metrics(predictions, labels=None) -> dict[str, DiagnosisMetrics]`

Issue 0003 use:

- Aggregates CMD-Audit, evidence recall, subagent judge, and random-label predictions across the six-case smoke suite.

Current smoke output:

- CMD-Audit macro F1 is `1.000`.
- Evidence recall and subagent judge are lower on the smoke suite because they are observational comparators.

#### `_observed_labels(...)`, `_top2_correct(...)`, `_macro_f1(...)`, `_label_f1(...)`

Issue 0003 use:

- Private helpers for comparison metric computation.

### `cmd_audit/cli.py`

#### `main(argv: list[str] | None = None) -> int`

Purpose:

- Standalone command-line entry point.

Issue 0003 behavior:

- Default `--cases` is `data/probe_cases/v0_issue3_cases.json`.
- Default `--out` is `artifacts/attribution_table.csv`.
- Default `--metrics-out` is `artifacts/comparison_metrics.csv`.
- Default `--confusion-out` is `artifacts/attribution_confusion_matrix.csv`.

Execution path:

```text
load_probe_cases
-> run_cases
-> write_attribution_table
-> write_comparison_metrics_table
-> write_confusion_matrix_table
```

Example:

```bash
python3 -m cmd_audit run
```

Boundary:

- This is a local research harness CLI, not a production adapter.

### `cmd_audit/__main__.py`

Purpose:

- Allows `python3 -m cmd_audit run`.

Behavior:

- Imports `main` from `cmd_audit.cli`.
- Raises `SystemExit(main())`.

### `cmd_audit/__init__.py`

Purpose:

- Exposes the small public API used by tests and future local callers.

Issue 0003 exports:

- `ReplayResult`
- `run_v0_replay_portfolio`
- `write_confusion_matrix_table`

Existing exports kept:

- `run_case`
- `run_cases`
- `write_attribution_table`
- `write_comparison_metrics_table`
- `assign_attribution`
- `load_probe_cases`
- `validate_v0_label`
- metric and result dataclasses

## Test-Level Contract

### `tests/test_cmd_audit_issue3_attribution_table.py`

This file is the behavior-level specification for issue 0003.

#### `test_raw_event_only_evidence_is_valid_probe_case`

Verifies:

- raw-event-only gold evidence loads successfully;
- `perturbation_label` is `premature_extraction_error`;
- `source_event_id` is present;
- `source_memory_id` is absent.

Why it matters:

- It protects the Verbatim Event Oracle fixture contract.

#### `test_verbatim_event_oracle_beats_oracle_retrieval_for_extraction_loss`

Verifies:

- Oracle Retrieval has answer and evidence score `0.0`;
- Verbatim Event Oracle answers `Berlin`;
- Verbatim Event Oracle has answer and evidence score `1.0`;
- attribution predicts `premature_extraction_error`;
- top replay is `verbatim_event_oracle`.

Why it matters:

- It protects the most important issue 0003 boundary: extraction loss is not retrieval miss.

#### `test_issue3_table_contains_per_replay_gain_columns`

Verifies:

- attribution table includes recovery-gain columns for all six replay paths;
- premature extraction row contains the expected predicted label and top replay.

Why it matters:

- It protects the evidence artifact shape required by issue 0003.

#### `test_issue3_suite_attributes_all_v0_pipeline_labels`

Verifies:

- `v0_issue3_cases.json` covers exactly the six V0 labels;
- each label maps to the expected top replay;
- each `AuditResult` has six replay results;
- every smoke attribution is correct.

Why it matters:

- It proves the V0 Replay Portfolio is complete for the bounded smoke suite.

#### `test_confusion_matrix_contains_one_diagonal_count_per_v0_label`

Verifies:

- confusion matrix header follows `V0_PIPELINE_LABEL_ORDER`;
- each V0 label has one diagonal count.

Why it matters:

- It protects the attribution evidence gate from being table-only.

## Artifact Contract

### `artifacts/attribution_table.csv`

Required columns include:

- `case_id`
- `perturbation_label`
- `predicted_label`
- `top_replay`
- `baseline_name`
- `baseline_answer_score`
- `baseline_evidence_score`
- `replay_answer_score`
- `replay_evidence_score`
- `recovery_gain`
- per-replay answer/evidence/gain columns for all six replays
- `top2_labels`
- `is_ambiguous`
- `diagnosis_cost`
- `attribution_correct`

Current smoke rows:

- `v0-write-001`
- `v0-compression-001`
- `v0-premature-extraction-001`
- `v0-retrieval-001`
- `v0-injection-001`
- `v0-reasoning-001`

### `artifacts/comparison_metrics.csv`

Required systems:

- `CMD-Audit`
- `evidence_recall`
- `subagent_judge`
- `random_label`

Required columns:

- `system_name`
- `attribution_accuracy`
- `macro_f1`
- `top2_accuracy`
- `cost_per_diagnosis`

### `artifacts/attribution_confusion_matrix.csv`

Required rows and columns:

- `write_error`
- `compression_error`
- `premature_extraction_error`
- `retrieval_error`
- `injection_error`
- `reasoning_error`

Current smoke matrix:

- one diagonal count for each V0 label.

## Boundary Rules

- Final attribution comes from replay deltas, not from evidence-recall heuristic or Subagent Judge Baseline.
- Subagent Judge Monitor may trigger replay but cannot emit labels, ECS, memory writes, gold answers, or full failed traces.
- V0 attribution outputs only `write_error`, `compression_error`, `premature_extraction_error`, `retrieval_error`, `injection_error`, and `reasoning_error`.
- Do not add `granularity_error`, `route_error`, `graph_error`, or `safety_error` columns in issue 0003.
- Do not add bad-memory-item labels to issue 0003 attribution output.
- Do not add ECS, Post-Repair Context Replay, targeted repair, Failure Memory, or CMD-Skill Adapter behavior in issue 0003.

## Verification

Commands:

```bash
python3 -m pytest
python3 -m cmd_audit run
```

Current verified state:

```text
16 tests passed
wrote 6 attribution row(s) to artifacts/attribution_table.csv
with comparison metrics to artifacts/comparison_metrics.csv
and confusion matrix to artifacts/attribution_confusion_matrix.csv
```

## Remaining Work After Issue 0003

Issue 0003 is green for the bounded V0 smoke suite. The next slice is issue 0004:

- review taxonomy boundaries from the first table;
- confirm `premature_extraction_error` remains distinct from `retrieval_error`;
- clarify top-2 or multi-label behavior for coupled failures;
- keep bad-memory-item labels out of V0 scoring;
- then proceed to Post-Repair Context Replay in issue 0005.
