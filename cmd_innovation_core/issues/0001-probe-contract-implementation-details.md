# Issue 0001 Implementation Details: Probe Dataset and Gold Evidence Contract

## Purpose

This document is the zoomed-out implementation map for issue 0001, `Define the probe dataset and gold evidence contract`.

Issue 0001 is the first executable slice of **CMD-Audit**, not the whole CMD system. Its job is to make one labeled **Memory Failure** loadable and diagnosable through the first tracer bullet:

```text
ProbeCase JSON
  -> ProbeCase contract validation
  -> baseline failed output
  -> Oracle Retrieval counterfactual replay
  -> replay delta / Recovery Gain
  -> Operation-Level Attribution
  -> attribution_table.csv row
```

The implemented slice intentionally stops before a full replay engine, **Error-Cause-Solution**, **Post-Repair Context Replay**, **Subagent Judge Monitor**, or **CMD-Skill Adapter**. Those are later issue slices.

## Source Requirements

The implementation follows these local planning files:

- `TASK.md`
  - Start with issue 0001 and the first TDD tracer bullet.
  - Define the probe case contract.
  - Include raw events, extracted memory, gold evidence, gold answer, baseline output, perturbation label, and scoring fields.
  - Add one minimal `retrieval_error` case where extracted memory contains gold evidence, baseline retrieval misses it, and Oracle Retrieval recovers the answer.
  - Do not implement the full replay engine before the first red-green path exists.
- `CLAUDE.md`
  - Treat `cmd_innovation_core/` as source of truth.
  - Keep **CMD-Audit** separate from **CMD-Skill Adapter**.
  - Keep V0 scoped to six pipeline labels.
  - Do not emit bad memory item labels in V0 attribution.
- `cmd_innovation_core/CONTEXT.md`
  - Use **Memory Failure**, **Memory Item**, **Memory Pipeline**, **Counterfactual Replay**, **Recovery Gain**, and **Operation-Level Attribution** precisely.
  - `retrieval_error` means correct memory exists in recoverable form but was not retrieved.
  - If raw events contain evidence but extracted memory cannot recover it, future code must prefer `premature_extraction_error`, not `retrieval_error`.
- `cmd_innovation_core/prd/cmd_minimal_probe_prd.md`
  - Make the probe dataset the first deep module.
  - Make the replay engine the second deep module.
  - Make the attribution layer the third deep module.
  - Use rule-based replay deltas first.
- `cmd_innovation_core/tdd/cmd_tracer_bullets.md`
  - Cycle 1: recoverable extracted memory + failed baseline retrieval + successful Oracle Retrieval = `retrieval_error`.

## Current Code Artifacts

| Artifact | Role in issue 0001 |
| --- | --- |
| `cmd_audit/models.py` | Probe case contract and JSON loader. |
| `cmd_audit/labels.py` | V0 label boundary and replay-to-label mapping. |
| `cmd_audit/scoring.py` | Deterministic answer/evidence scoring helpers. |
| `cmd_audit/replays.py` | First Counterfactual Replay: Oracle Retrieval. |
| `cmd_audit/attribution.py` | Recovery Gain ranking and Operation-Level Attribution. |
| `cmd_audit/harness.py` | Public harness entry points and CSV table writer. |
| `cmd_audit/cli.py` | CLI entry point for the standalone research harness. |
| `cmd_audit/__main__.py` | Enables `python3 -m cmd_audit ...`. |
| `cmd_audit/__init__.py` | Small public import surface. |
| `data/probe_cases/v0_retrieval_error_case.json` | First executable synthetic probe case. |
| `tests/test_cmd_audit_tracer_bullet.py` | Behavior-level tests for the first tracer bullet and label boundaries. |
| `artifacts/attribution_table.csv` | First generated attribution evidence artifact. |

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
              -> replays.run_oracle_retrieval
                  -> ProbeCase.primary_baseline
                  -> replays._recover_extracted_gold_evidence
                  -> scoring.evidence_recall_from_text
                  -> scoring.answer_score
              -> attribution.assign_attribution
                  -> attribution._label_for_replay
                  -> labels.validate_v0_label
      -> harness.write_attribution_table
```

Domain reading:

- `models.py` owns the **Memory Failure** probe contract.
- `labels.py` enforces the **V0 Core Label Set**.
- `replays.py` runs a controlled **Counterfactual Replay**.
- `scoring.py` computes answer and evidence scores.
- `attribution.py` converts **Recovery Gain** into **Operation-Level Attribution**.
- `harness.py` is the **CMD-Audit** public surface for this first slice.
- `cli.py` is a standalone runner, not a **CMD-Skill Adapter**.

## Probe Case Contract

The JSON fixture in `data/probe_cases/v0_retrieval_error_case.json` is the concrete contract example.

Required top-level fields:

| JSON field | Python representation | Domain meaning |
| --- | --- | --- |
| `case_id` | `ProbeCase.case_id` | Stable synthetic case identifier. |
| `query` | `ProbeCase.query` | Original failed query for the Memory-Augmented Agent. |
| `raw_events` | `tuple[RawEvent, ...]` | Pre-extraction history or event trace. |
| `extracted_memory` | `tuple[MemoryItem, ...]` | Stored recoverable Memory Items. |
| `gold_evidence` | `tuple[GoldEvidence, ...]` | Evidence needed to score replay success. |
| `gold_answer` | `ProbeCase.gold_answer` | Expected answer used only for scoring, not injected into Post-Repair Context Replay. |
| `baseline_outputs` | `tuple[BaselineOutput, ...]` | Failed outputs from fixed-summary/vector-memory baselines. |
| `perturbation_label` | validated V0 label | Known synthetic failure cause for evaluation. |
| `scoring` | `ScoringSpec` | Declares answer/evidence metric names. |

For the current case:

- Raw event `evt-001` contains the true decision: Mira chose Lisbon.
- Extracted memory `mem-001` preserves that gold evidence.
- Baseline `vector_memory` retrieves `mem-002`, a distractor about Porto.
- Baseline answer is wrong: `Porto`.
- Oracle Retrieval recovers `mem-001`, answers `Lisbon`, and yields Recovery Gain `1.000`.
- CMD-Audit predicts `retrieval_error`.

## Function-Level Contract

### `cmd_audit/labels.py`

#### `LabelValidationError`

Purpose:

- Signals that a label violates the **V0 Core Label Set** boundary.

Used by:

- `validate_v0_label`.
- Tests that assert bad memory item labels and deferred pipeline labels are rejected.

#### `validate_v0_label(label: str) -> str`

Purpose:

- Accepts only the six V0 pipeline labels:
  - `write_error`
  - `compression_error`
  - `premature_extraction_error`
  - `retrieval_error`
  - `injection_error`
  - `reasoning_error`

Behavior:

- Returns the label unchanged when it is in `V0_PIPELINE_LABELS`.
- Raises `LabelValidationError` for bad **Memory Item** labels:
  - `item_wrong`
  - `item_stale`
  - `item_conflict`
  - `item_poisoned`
  - `item_compression_distorted`
- Raises `LabelValidationError` for deferred pipeline labels:
  - `granularity_error`
  - `route_error`
  - `graph_error`
  - `safety_error`
- Raises `LabelValidationError` for unknown labels.

Why issue 0001 needs it:

- The probe dataset contract must not accidentally admit V1/V2 labels or item-level labels into V0 attribution scoring.

Callers:

- `ProbeCase.from_mapping` validates each case's `perturbation_label`.
- `assign_attribution` validates the final predicted label.
- `V0LabelBoundaryTest.test_v0_accepts_only_pipeline_labels` verifies boundary behavior.

Constants:

- `V0_PIPELINE_LABELS` is the allowed V0 scoring set.
- `OUT_OF_SCOPE_ITEM_LABELS` documents explicitly excluded item labels.
- `DEFERRED_PIPELINE_LABELS` documents deferred V1/V2 labels.
- `REPLAY_TO_LABEL` maps replay names to the pipeline labels that they diagnose.

### `cmd_audit/models.py`

#### `ProbeCaseError`

Purpose:

- Signals malformed probe JSON or contract violations.

Used by:

- `_required_str`
- `load_probe_cases`
- `ProbeCase.validate`

#### `RawEvent.from_mapping(cls, value: dict[str, Any]) -> RawEvent`

Purpose:

- Converts one raw event JSON object into an immutable `RawEvent`.

Required JSON fields:

- `event_id`
- `text`

Domain meaning:

- Represents pre-extraction evidence available to future **Verbatim Event Oracle** logic.

Current issue 0001 usage:

- Loaded as part of `ProbeCase.from_mapping`.
- Ensures the case distinguishes raw events from extracted memory.

#### `MemoryItem.from_mapping(cls, value: dict[str, Any]) -> MemoryItem`

Purpose:

- Converts one extracted memory JSON object into an immutable `MemoryItem`.

Required JSON fields:

- `memory_id`
- `text`

Optional JSON field:

- `source_event_ids`

Domain meaning:

- Represents a recoverable **Memory Item** after ingestion/extraction.

Current issue 0001 usage:

- Oracle Retrieval can recover gold evidence only if `GoldEvidence.source_memory_id` points to one of these Memory Items.

#### `GoldEvidence.from_mapping(cls, value: dict[str, Any]) -> GoldEvidence`

Purpose:

- Converts one gold evidence JSON object into an immutable `GoldEvidence`.

Required JSON fields:

- `evidence_id`
- `text`

Optional JSON fields:

- `source_memory_id`
- `source_event_id`
- `required_phrases`

Domain meaning:

- Defines the evidence that a Counterfactual Replay must recover.

Current issue 0001 usage:

- For `retrieval_error`, `source_memory_id` must identify a real Memory Item in `extracted_memory`.
- `required_phrases` drives `evidence_recall_from_text`.

#### `BaselineOutput.from_mapping(cls, value: dict[str, Any]) -> BaselineOutput`

Purpose:

- Converts one baseline output JSON object into an immutable `BaselineOutput`.

Required JSON fields:

- `baseline_name`
- `answer`

Optional JSON fields:

- `retrieved_memory_ids`
- `answer_score`
- `evidence_score`
- `injected_context`

Domain meaning:

- Records the failed starting point before CMD-Audit replays.

Current issue 0001 usage:

- The first baseline is `vector_memory`.
- It retrieves distractor `mem-002`, answers `Porto`, and starts with answer/evidence scores of `0.0`.

#### `ScoringSpec.from_mapping(cls, value: dict[str, Any] | None) -> ScoringSpec`

Purpose:

- Loads the scoring metric names declared by the probe case.

Defaults:

- `answer_metric = "casefold_exact_match"`
- `evidence_metric = "gold_evidence_recall"`

Domain meaning:

- Keeps the probe contract explicit about how answer score and evidence score are measured.

Current issue 0001 usage:

- The actual deterministic implementations live in `scoring.py`.
- Future cases can keep the same declared metric names while expanding fixtures.

#### `ProbeCase.from_mapping(cls, value: dict[str, Any]) -> ProbeCase`

Purpose:

- Constructs a complete immutable `ProbeCase` from one JSON object.

Work performed:

- Reads required scalar fields with `_required_str`.
- Converts `raw_events` through `RawEvent.from_mapping`.
- Converts `extracted_memory` through `MemoryItem.from_mapping`.
- Converts `gold_evidence` through `GoldEvidence.from_mapping`.
- Converts `baseline_outputs` through `BaselineOutput.from_mapping`.
- Validates `perturbation_label` through `validate_v0_label`.
- Loads scoring through `ScoringSpec.from_mapping`.
- Calls `ProbeCase.validate`.

Domain meaning:

- This is the core issue 0001 contract boundary. Everything downstream assumes that a loaded `ProbeCase` has separated raw events, extracted memory, gold evidence, baseline outputs, gold answer, perturbation label, and scoring fields.

Callers:

- `load_probe_cases`.
- Tests constructing a deliberately broken case.

#### `ProbeCase.primary_baseline(self) -> BaselineOutput`

Purpose:

- Returns the first baseline output for the initial tracer bullet.

Domain meaning:

- The first slice needs one failed baseline starting point before measuring replay Recovery Gain.

Current limitation:

- V0 issue 0001 uses only the first baseline for `run_case`.
- Issue 0002 should generalize baseline comparison across fixed-summary and vector-memory behavior.

#### `ProbeCase.validate(self) -> None`

Purpose:

- Enforces structural invariants after object construction.

Checks:

- `raw_events` is not empty.
- `extracted_memory` is not empty.
- `gold_evidence` is not empty.
- `baseline_outputs` is not empty.
- Every `GoldEvidence.source_memory_id`, when present, points to an existing `MemoryItem.memory_id`.

Why issue 0001 needs it:

- The first `retrieval_error` tracer bullet depends on gold evidence being recoverable from extracted memory.
- If `GoldEvidence.source_memory_id` points nowhere, Oracle Retrieval would be testing a broken fixture rather than a retrieval failure.

Callers:

- `ProbeCase.from_mapping`.

#### `load_probe_cases(path: str | Path) -> list[ProbeCase]`

Purpose:

- Public file loader for probe datasets.

Behavior:

- Reads UTF-8 JSON.
- Accepts either:
  - one JSON object, or
  - a list of JSON objects.
- Converts each object through `ProbeCase.from_mapping`.
- Raises `ProbeCaseError` if the JSON top level is neither object nor list.

Domain meaning:

- This is the first public dataset-loading API for the standalone **CMD-Audit** harness.

Callers:

- `cli.main`.
- `tests/test_cmd_audit_tracer_bullet.py`.
- External users can import it from `cmd_audit`.

#### `_required_str(value: dict[str, Any], key: str) -> str`

Purpose:

- Internal helper for required non-empty string fields.

Behavior:

- Raises `ProbeCaseError` when the key is absent.
- Raises `ProbeCaseError` when the value is not a non-empty string.
- Returns the raw string otherwise.

Domain meaning:

- Keeps the case contract strict enough for 50-100 synthetic cases to fail fast on malformed identifiers and text.

Callers:

- `RawEvent.from_mapping`
- `MemoryItem.from_mapping`
- `GoldEvidence.from_mapping`
- `BaselineOutput.from_mapping`
- `ProbeCase.from_mapping`

### `cmd_audit/scoring.py`

#### `answer_score(answer: str, gold_answer: str) -> float`

Purpose:

- Scores answer correctness for synthetic cases.

Behavior:

- Normalizes both strings with `_normalize`.
- Returns `1.0` for exact match after normalization.
- Returns `0.0` otherwise.

Domain meaning:

- Produces the answer-score part of **Recovery Gain**:

```text
Recovery Gain = replay answer score - baseline answer score
```

Current issue 0001 usage:

- `Porto` vs `Lisbon` scores `0.0`.
- `Lisbon` vs `Lisbon` scores `1.0`.

#### `evidence_recall_from_memory_ids(case: ProbeCase, memory_ids: tuple[str, ...]) -> float`

Purpose:

- Scores whether a set of retrieved memory IDs includes the required gold evidence memory IDs.

Behavior:

- Builds the set of required `source_memory_id` values from `case.gold_evidence`.
- Returns `0.0` if no required memory IDs exist.
- Returns recall as `matched_required_ids / required_ids`.

Current status:

- Implemented as a small baseline/evidence helper.
- Not currently called by `run_case`.
- Useful for issue 0002 evidence recall heuristics and baseline comparison.

#### `evidence_recall_from_text(gold_evidence: tuple[GoldEvidence, ...], text: str) -> float`

Purpose:

- Scores whether replay evidence text contains each gold evidence item's required phrases.

Behavior:

- Returns `0.0` if there is no gold evidence.
- Casefolds the evidence block.
- For each `GoldEvidence`, uses `required_phrases` if present, otherwise uses full `GoldEvidence.text`.
- Counts an evidence item as matched only if all its phrases appear in the evidence block.
- Returns `matched / total_gold_evidence`.

Current issue 0001 usage:

- Oracle Retrieval builds an evidence block from `mem-001`.
- Required phrases `Mira`, `Lisbon`, and `Q3 offsite` all appear.
- Evidence score becomes `1.0`.

#### `_normalize(value: str) -> str`

Purpose:

- Internal helper for answer matching.

Behavior:

- Casefolds.
- Strips surrounding whitespace.
- Removes leading/trailing non-word punctuation.
- Collapses repeated whitespace.

Caller:

- `answer_score`.

### `cmd_audit/replays.py`

#### `ReplayResult`

Purpose:

- Immutable output record for one **Counterfactual Replay**.

Fields:

- `replay_name`
- `answer`
- `answer_score`
- `evidence_score`
- `evidence_block`
- `recovery_gain`

Domain meaning:

- This is the first replay result shape that later issue slices can reuse for Oracle Write, Oracle Compression, Verbatim Event Oracle, Injection-Oracle, and Evidence-Given Reasoning.

#### `run_oracle_retrieval(case: ProbeCase) -> ReplayResult`

Purpose:

- Runs the first implemented Counterfactual Replay.

Behavior:

1. Reads the failed baseline through `case.primary_baseline`.
2. Recovers the gold evidence block from extracted memory through `_recover_extracted_gold_evidence`.
3. Scores evidence recovery with `evidence_recall_from_text`.
4. Returns `case.gold_answer` only if evidence score is `1.0`.
5. Scores the replay answer with `answer_score`.
6. Computes `recovery_gain = replay answer score - baseline answer score`.
7. Returns `ReplayResult(replay_name="oracle_retrieval", ...)`.

Domain meaning:

- Diagnoses `retrieval_error` only when the correct memory survived extraction/storage and can be recovered from `extracted_memory`.
- It does not inspect raw events. That boundary matters because raw-event-only recovery belongs to future `verbatim_event_oracle` and `premature_extraction_error`.

Callers:

- `harness.run_case`.

#### `_recover_extracted_gold_evidence(case: ProbeCase) -> str`

Purpose:

- Internal helper that builds the Oracle Retrieval evidence block.

Behavior:

- Indexes `case.extracted_memory` by `memory_id`.
- For each `GoldEvidence`, looks up `source_memory_id`.
- Appends the corresponding `MemoryItem.text` when found.
- Joins recovered memory texts with newline separators.

Domain meaning:

- Encodes the condition "gold evidence exists in recoverable extracted memory".

Caller:

- `run_oracle_retrieval`.

### `cmd_audit/attribution.py`

#### `AttributionResult`

Purpose:

- Immutable result of Operation-Level Attribution.

Fields:

- `predicted_label`
- `top_replay`
- `recovery_gain`
- `top2_labels`
- `is_ambiguous`

Domain meaning:

- Stores the failure label assigned from replay deltas rather than from a post-hoc explanation.

#### `assign_attribution(replay_results: tuple[ReplayResult, ...], *, positive_gain_threshold: float = 0.0, tie_margin: float = 0.05) -> AttributionResult`

Purpose:

- Converts replay deltas into an attribution label.

Behavior:

1. Requires at least one replay result.
2. Sorts results by descending `recovery_gain`.
3. Rejects the set if the top gain is not greater than `positive_gain_threshold`.
4. Maps `top.replay_name` to a label through `_label_for_replay`.
5. Validates the mapped label through `validate_v0_label`.
6. Builds up to two close labels within `tie_margin`.
7. Marks `is_ambiguous = True` if two close labels exist.
8. Returns `AttributionResult`.

Domain meaning:

- Implements the rule-based V0 attribution principle:

```text
operation label = label of replay with strongest positive Recovery Gain
```

Current issue 0001 usage:

- Receives one replay: `oracle_retrieval`.
- Maps it to `retrieval_error`.
- Produces `top2_labels = ("retrieval_error",)` and `is_ambiguous = False`.

Future issue usage:

- Cycle 4 can use `tie_margin` for top-2 or multi-label behavior once more replay types exist.

#### `_label_for_replay(replay_name: str) -> str`

Purpose:

- Internal replay-name to label lookup.

Behavior:

- Looks up `replay_name` in `REPLAY_TO_LABEL`.
- Raises `ValueError` for unknown replay names.

Domain meaning:

- Keeps replay implementation names separate from attribution labels while preserving an explicit mapping.

Caller:

- `assign_attribution`.

### `cmd_audit/harness.py`

#### `AuditResult`

Purpose:

- Immutable row-level result for one audited probe case.

Fields:

- `case_id`
- `perturbation_label`
- `baseline_name`
- `baseline_answer_score`
- `baseline_evidence_score`
- `replay`
- `attribution`

Domain meaning:

- Bundles the known synthetic label, baseline state, replay result, and CMD attribution for evidence table generation.

#### `AuditResult.attribution_correct(self) -> bool`

Purpose:

- Reports whether CMD recovered the synthetic perturbation label.

Behavior:

- Returns `self.attribution.predicted_label == self.perturbation_label`.

Domain meaning:

- First building block for later attribution accuracy and macro F1 metrics.

Current issue 0001 usage:

- `retrieval_error == retrieval_error`, so the first row is correct.

#### `run_case(case: ProbeCase) -> AuditResult`

Purpose:

- Public single-case CMD-Audit path for the first tracer bullet.

Behavior:

1. Runs `run_oracle_retrieval(case)`.
2. Passes the replay result into `assign_attribution`.
3. Reads `case.primary_baseline`.
4. Returns `AuditResult`.

Domain meaning:

- This is the minimal standalone **CMD-Audit** harness path:

```text
ProbeCase -> Counterfactual Replay -> Recovery Gain -> Operation-Level Attribution
```

Current limitation:

- It only runs Oracle Retrieval.
- Future issues should add replay selection or a replay set without changing the `ProbeCase` contract unnecessarily.

Callers:

- `run_cases`.
- Tests.
- External users can import it from `cmd_audit`.

#### `run_cases(cases: list[ProbeCase]) -> list[AuditResult]`

Purpose:

- Applies `run_case` to a list of loaded cases.

Behavior:

- Returns one `AuditResult` per input case.

Domain meaning:

- Keeps the API compatible with the target 50-100 synthetic probe cases from `TASK.md`.

Callers:

- `cli.main`.

#### `write_attribution_table(results: list[AuditResult], output_path: str | Path) -> None`

Purpose:

- Writes a CSV evidence artifact for attribution.

CSV columns:

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
- `top2_labels`
- `is_ambiguous`
- `attribution_correct`

Behavior:

- Creates the parent output directory if needed.
- Writes one header row.
- Writes one row per `AuditResult`.
- Formats numeric scores to three decimals.
- Joins `top2_labels` with `|`.
- Writes booleans as lowercase strings.

Domain meaning:

- Produces the first evidence artifact required before any attribution claim can be made.

Callers:

- `cli.main`.
- Tests.

### `cmd_audit/cli.py`

#### `main(argv: list[str] | None = None) -> int`

Purpose:

- Command-line entry point for the standalone CMD-Audit harness.

Command:

```bash
python3 -m cmd_audit run \
  --cases data/probe_cases/v0_retrieval_error_case.json \
  --out artifacts/attribution_table.csv
```

Behavior:

- Parses subcommand `run`.
- Defaults `--cases` to the first retrieval-error fixture.
- Defaults `--out` to `artifacts/attribution_table.csv`.
- Loads cases through `load_probe_cases`.
- Runs cases through `run_cases`.
- Writes CSV through `write_attribution_table`.
- Prints how many attribution rows were written.
- Returns `0` on success.

Domain meaning:

- Provides a reproducible local execution surface for CMD-Audit.
- It is not a production memory-agent integration and not a CMD-Skill Adapter.

### `cmd_audit/__main__.py`

#### Module-level `raise SystemExit(main())`

Purpose:

- Allows Python module execution with `python3 -m cmd_audit ...`.

Behavior:

- Imports `main` from `cmd_audit.cli`.
- Converts the returned integer into the process exit status.

### `cmd_audit/__init__.py`

Purpose:

- Defines the package's public import surface.

Exported objects:

- `AttributionResult`
- `AuditResult`
- `ProbeCase`
- `V0_PIPELINE_LABELS`
- `assign_attribution`
- `load_probe_cases`
- `run_case`
- `run_cases`
- `validate_v0_label`
- `write_attribution_table`

Domain meaning:

- Keeps caller-facing CMD-Audit API narrow while implementation modules stay replaceable.

## Test-Level Contract

Tests live in `tests/test_cmd_audit_tracer_bullet.py`.

### `RetrievalFailureTracerBulletTest.test_probe_case_contract_loads_retrieval_failure_case`

Verifies:

- `load_probe_cases` loads the JSON fixture.
- The perturbation label is `retrieval_error`.
- Raw events, extracted memory, and gold evidence are present.
- The gold answer is `Lisbon`.
- The primary baseline is `vector_memory`.
- The primary baseline retrieved distractor `mem-002`.

Requirement coverage:

- Issue 0001 case contract distinction.
- TDD Cycle 1 setup.

### `RetrievalFailureTracerBulletTest.test_oracle_retrieval_recovers_answer_and_attributes_retrieval_error`

Verifies:

- `run_case` runs `oracle_retrieval`.
- Replay answer is `Lisbon`.
- Replay answer score is `1.0`.
- Replay evidence score is `1.0`.
- Predicted label is `retrieval_error`.
- `attribution_correct` is true.

Requirement coverage:

- First red-green tracer bullet.
- Operation-Level Attribution from Recovery Gain.

### `RetrievalFailureTracerBulletTest.test_attribution_table_contains_first_retrieval_row`

Verifies:

- `write_attribution_table` writes the expected CSV header.
- The first row contains `v0-retrieval-001,retrieval_error,retrieval_error`.

Requirement coverage:

- First evidence artifact shape for `attribution_table.csv`.

### `V0LabelBoundaryTest.test_v0_accepts_only_pipeline_labels`

Verifies:

- `retrieval_error` is accepted.
- `item_wrong` is rejected.
- `route_error` is rejected.

Requirement coverage:

- Bad memory item labels excluded from V0.
- Deferred pipeline labels excluded from V0.

### `V0LabelBoundaryTest.test_probe_case_rejects_gold_evidence_missing_from_extracted_memory`

Verifies:

- A `retrieval_error` fixture is invalid if gold evidence points to a missing extracted memory item.

Requirement coverage:

- Prevents false `retrieval_error` cases where the evidence was never recoverable from extracted memory.
- Protects the boundary between `retrieval_error` and future `premature_extraction_error`.

## Label Scenario Examples for Issue 0001

These examples satisfy issue 0001's contract-level requirement that each minimum V0 label has at least one scenario. Only `retrieval_error` is executable in the current code slice; the other five are contract examples for future tracer bullets.

### `write_error`

Scenario:

- Raw events contain the needed evidence.
- Extracted memory lacks any Memory Item representing that evidence.
- Oracle Write injects a correct Memory Item.
- The answer recovers.

Expected attribution:

- `write_error`

Implementation status:

- Label is allowed by `V0_PIPELINE_LABELS`.
- Replay mapping exists as `REPLAY_TO_LABEL["oracle_write"] = "write_error"`.
- Oracle Write replay is not implemented in issue 0001.

### `compression_error`

Scenario:

- Raw events contain a complete fact: entity, relation, time, and constraint.
- Extracted memory contains a compressed Memory Item that drops a critical field.
- Oracle Compression replaces it with a complete compressed memory.
- The answer recovers.

Expected attribution:

- `compression_error`

Implementation status:

- Label is allowed by `V0_PIPELINE_LABELS`.
- Replay mapping exists as `REPLAY_TO_LABEL["oracle_compression"] = "compression_error"`.
- Oracle Compression replay is not implemented in issue 0001.

### `premature_extraction_error`

Scenario:

- Raw events contain the needed evidence.
- Extracted memory contains no recoverable representation of it because ingestion abstracted too early.
- Oracle Retrieval over extracted memory fails.
- Verbatim Event Oracle recovers from raw events.

Expected attribution:

- `premature_extraction_error`

Implementation status:

- Label is allowed by `V0_PIPELINE_LABELS`.
- Replay mapping exists as `REPLAY_TO_LABEL["verbatim_event_oracle"] = "premature_extraction_error"`.
- Verbatim Event Oracle replay is planned for TDD Cycle 2.

### `retrieval_error`

Scenario:

- Extracted memory contains the gold evidence.
- Baseline vector memory retrieves the wrong Memory Item.
- Oracle Retrieval directly recovers the gold Memory Item.
- The answer recovers.

Expected attribution:

- `retrieval_error`

Implementation status:

- Fully executable in `data/probe_cases/v0_retrieval_error_case.json`.
- Covered by `run_oracle_retrieval`.
- Covered by behavior tests.

### `injection_error`

Scenario:

- Correct Memory Item is retrieved.
- Baseline injects it into a confusing or malformed context block.
- Injection-Oracle provides a canonical evidence block.
- The answer recovers.

Expected attribution:

- `injection_error`

Implementation status:

- Label is allowed by `V0_PIPELINE_LABELS`.
- Replay mapping exists as `REPLAY_TO_LABEL["injection_oracle"] = "injection_error"`.
- Injection-Oracle replay is not implemented in issue 0001.

### `reasoning_error`

Scenario:

- Correct evidence is retrieved and injected.
- Baseline answer is still wrong because final reasoning misuses the evidence.
- Evidence-Given Reasoning recovers the answer.

Expected attribution:

- `reasoning_error`

Implementation status:

- Label is allowed by `V0_PIPELINE_LABELS`.
- Replay mapping exists as `REPLAY_TO_LABEL["evidence_given_reasoning"] = "reasoning_error"`.
- Evidence-Given Reasoning replay is planned for TDD Cycle 3.

## Acceptance Criteria Mapping

| Issue 0001 AC | Current implementation |
| --- | --- |
| Distinguish raw events, extracted memory, gold evidence, base output, injected failure label. | `ProbeCase` fields and JSON fixture separate these fields explicitly. |
| Include labels for six V0 pipeline labels. | `V0_PIPELINE_LABELS` includes all six labels. |
| Exclude bad memory item labels. | `OUT_OF_SCOPE_ITEM_LABELS` and `validate_v0_label` reject them. |
| Exclude deferred labels. | `DEFERRED_PIPELINE_LABELS` and `validate_v0_label` reject them. |
| At least one example scenario per minimum label. | This document defines one scenario per label; only `retrieval_error` is currently executable. |
| State answer score and evidence score measurement. | `ScoringSpec`, `answer_score`, and `evidence_recall_from_text` define current scoring. |
| Small enough for 50-100 synthetic cases. | `load_probe_cases` accepts one case or a list; `run_cases` maps one case path over a list. |

## Current Execution

Run tests:

```bash
python3 -m unittest discover -s tests -v
```

Run the first CMD-Audit case:

```bash
python3 -m cmd_audit run \
  --cases data/probe_cases/v0_retrieval_error_case.json \
  --out artifacts/attribution_table.csv
```

Expected first CSV row:

```text
v0-retrieval-001,retrieval_error,retrieval_error,oracle_retrieval,vector_memory,0.000,0.000,1.000,1.000,1.000,retrieval_error,false,true
```

## Non-Goals Preserved

- No production memory agent is implemented.
- No CMD-Skill Adapter is implemented.
- No UI or dashboard is added.
- No learned attribution classifier is added.
- No full replay engine is added before the first red-green path exists.
- No bad Memory Item label is emitted as V0 attribution.
- No Post-Repair Context Replay injects a gold answer; that gate is not implemented yet.

## Next Technical Step

The next tracer bullet should add a `premature_extraction_error` fixture and **Verbatim Event Oracle** replay:

```text
raw events contain evidence
extracted memory lacks recoverable evidence
Oracle Retrieval fails
Verbatim Event Oracle recovers
predicted label = premature_extraction_error
```

That next step should reuse `ProbeCase`, `GoldEvidence.source_event_id`, `ReplayResult`, and `assign_attribution` rather than broadening the contract prematurely.
