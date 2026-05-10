# Issue 0010 Implementation Details: Evidence-Driven Version Gates

## Purpose

This document is the zoomed-out implementation map for issue 0010, `Enforce evidence-driven version gates`.

Issue 0010 closes the V0 governance loop by defining, checking, and tracking version gates that are driven by credibility evidence rather than feature stacking:

```text
Four V0 Evidence Artifacts
  -> GateCriterion per artifact
  -> check_v0_to_v1_gate()
      -> _check_macro_f1 (comparison_metrics.csv)
      -> _check_confusion_diagonal (attribution_confusion_matrix.csv)
      -> _check_accuracy_top2 (comparison_metrics.csv)
      -> _check_repair_distribution (post_repair_table.csv)
  -> GateResult (pass/fail per criterion)
  -> write_gate_status -> V0V1_gate_status.txt (sandbox artifact)
  -> GateReview (HITL decision: approved/deferred/rejected)
  -> write_gate_review -> V0V1_gate_review.txt (dated review note)
```

The V1→V2 gate is defined as a stub: at least two distinct memory agents integrated through the Adapter Interface without macro F1 regression. This gate always returns not-met in V0 because no adapter integrations exist.

The final gate decision is HITL (human-in-the-loop): the code checks thresholds and reports evidence; the human reviews and signs off.

## Source Requirements

The implementation follows these local planning files:

| Source | Requirement Applied In Issue 0010 |
| --- | --- |
| `TASK.md` | Define V0→V1 and V1→V2 evidence gates; gate status tracked in a document, not code; HITL sign-off required. |
| `CLAUDE.md` | Version gates V0→V1→V2 are evidence-driven: V0→V1 requires four V0 evidence artifacts passing paper-claim thresholds; V1→V2 requires at least two distinct memory agents integrated without macro F1 regression. |
| `cmd_innovation_core/CONTEXT.md` | **Version Gates** are evidence-driven, not feature-stacking. **CMD-Audit** writes to sandbox only. |
| `cmd_innovation_core/prd/cmd_minimal_probe_prd.md` | AC10: V0→V1 requires four V0 evidence artifacts passing paper-claim thresholds; V1→V2 requires adapter integration count and macro F1 non-regression. User story 32: version gates driven by evidence thresholds, not feature completion. |
| `cmd_innovation_core/issues/0010-enforce-evidence-driven-version-gates.md` | Four V0→V1 criteria: macro F1, confusion diagonal, accuracy+top-2, repair distribution. V1→V2 stub. Gate tracking document with dated review notes. Gates do not block implementation — they gate the version lock claim only. |
| `cmd_innovation_core/tdd/cmd_tracer_bullets.md` | Gate check verified through public interfaces; sandbox write boundary enforced; HITL review pipeline testable independently. |

## Domain Boundary

Issue 0010 sits at the governance layer, reading evidence artifacts produced by earlier issues and producing gate status output:

```text
Issue 0002/0003 artifacts
  -> comparison_metrics.csv ─────────────┐
  -> attribution_confusion_matrix.csv ───┼──> check_v0_to_v1_gate()
Issue 0005 artifacts                      │
  -> post_repair_table.csv ──────────────┘
      -> GateCriterion x4
      -> GateResult
      -> artifacts/sandbox/V0V1_gate_status.txt
      -> GateReview (HITL)
      -> artifacts/sandbox/V0V1_gate_review.txt
```

It does own:

- defining V0→V1 gate criteria with specific thresholds;
- reading evidence artifacts and evaluating each criterion;
- producing a GateResult with pass/fail per criterion and aggregate all_passed;
- defining the V1→V2 gate criterion (adapter integration count);
- writing gate status documents to the replay-local sandbox;
- writing dated HITL review notes;
- exposing the gate check API for programmatic use.

It does not own:

- producing the evidence artifacts themselves (issues 0002, 0003, 0005);
- making the final version-lock decision (HITL);
- blocking implementation work (gates gate the version claim, not development);
- integrating with production CI/CD or remote tracking;
- expanding the V0 label set or attribution taxonomy.

## Current Code Artifacts

| Artifact | Role in issue 0010 |
| --- | --- |
| `cmd_audit/version_gates.py` | Core module: data types (GateCriterion, GateResult, GateReview), V0→V1 gate check, V1→V2 stub, gate status and review writers, internal CSV readers and criterion checkers. |
| `cmd_audit/__init__.py` | Exports 7 new symbols: GateCriterion, GateResult, GateReview, check_v0_to_v1_gate, check_v1_to_v2_gate, write_gate_status, write_gate_review. |
| `cmd_innovation_core/gates/V0V1_gate_status.md` | Human-readable gate tracking document with per-criterion evidence and HITL review log. |
| `artifacts/sandbox/V0V1_gate_status.txt` | Generated gate status artifact from `write_gate_status`. |
| `artifacts/sandbox/V0V1_gate_review.txt` | Generated HITL review artifact from `write_gate_review`. |
| `tests/test_cmd_audit_issue10_version_gates.py` | 48 behavior-level tests across 14 test classes. |

## Module Map

| Module | Issue 0010 Role |
| --- | --- |
| `cmd_audit/version_gates.py` | Owns gate data types, criterion checks, gate check functions, and output writers. |
| `cmd_audit/labels.py` | Provides `V0_PIPELINE_LABEL_ORDER` used by `_check_confusion_diagonal` to iterate all six V0 labels. |
| `cmd_audit/post_repair.py` | Provides `validate_sandbox_path` used by `write_gate_status` and `write_gate_review` to enforce sandbox write boundary. |
| `cmd_audit/__init__.py` | Exports the public surface for callers and tests. |

Issue 0010 does not depend on `harness.py`, `baselines.py`, `replays.py`, `attribution.py`, `repairs.py`, `failure_memory.py`, or `models.py`. It reads CSV artifacts directly, not through the harness.

## Caller Graph

Main gate check path:

```text
tests/test_cmd_audit_issue10_version_gates.py
  -> check_v0_to_v1_gate()
      -> _check_macro_f1(comparison_metrics.csv)
          -> _read_comparison_csv
      -> _check_confusion_diagonal(attribution_confusion_matrix.csv)
          -> _read_confusion_csv
          -> labels.V0_PIPELINE_LABEL_ORDER
      -> _check_accuracy_top2(comparison_metrics.csv)
          -> _read_comparison_csv
      -> _check_repair_distribution(post_repair_table.csv)
          -> _read_repair_csv
  -> check_v1_to_v2_gate()  [stub, always not-met]
  -> write_gate_status(result, path, sandbox_root)
      -> post_repair.validate_sandbox_path
  -> write_gate_review(review, path, sandbox_root)
      -> post_repair.validate_sandbox_path
```

Artifact generation path:

```text
python3 -c "..."
  -> check_v0_to_v1_gate()
      -> [_check_macro_f1, _check_confusion_diagonal, _check_accuracy_top2, _check_repair_distribution]
  -> write_gate_status(result, "artifacts/sandbox/V0V1_gate_status.txt")
  -> GateReview(...)
  -> write_gate_review(review, "artifacts/sandbox/V0V1_gate_review.txt")
```

## Data Flow

Input artifacts (read by gate checks):

```text
artifacts/comparison_metrics.csv
  -> macro_f1 column (criterion 1)
  -> attribution_accuracy, top2_accuracy columns (criterion 3)

artifacts/attribution_confusion_matrix.csv
  -> per-label row counts (criterion 2)

artifacts/sandbox/post_repair_table.csv
  -> repair_assessment column (criterion 4)
```

Output:

```text
GateCriterion (7 fields)
  criterion_id, description, artifact_path, threshold, passed, evidence, missing

GateResult (4 fields)
  gate_id: "V0→V1" | "V1→V2"
  criteria: tuple[GateCriterion, ...]
  all_passed: bool
  checked_at: str (ISO timestamp)

GateReview (6 fields)
  gate_id, reviewer, decision ("approved"|"deferred"|"rejected"),
  rationale, missing_evidence, reviewed_at
```

Artifact output:

```text
artifacts/sandbox/V0V1_gate_status.txt
artifacts/sandbox/V0V1_gate_review.txt
```

## Function-Level Contract

### `cmd_audit/version_gates.py`

This module owns issue 0010's entire version gate surface. It is a new module that depends on `labels.py` (for `V0_PIPELINE_LABEL_ORDER`) and `post_repair.py` (for `validate_sandbox_path`). It does not depend on the harness, baselines, replays, or models.

### Constant: `V0V1_CRITERION_IDS`

Definition:

```python
V0V1_CRITERION_IDS = (
    "macro_f1_exceeds_baselines",
    "confusion_diagonal_dominance",
    "accuracy_top2_exceeds_baselines",
    "repair_assessment_distribution",
)
```

Role:

- Documents the four criterion IDs that define the V0→V1 gate.
- Used as a reference constant; not enforced programmatically (the four criteria are hardcoded in `check_v0_to_v1_gate` for explicit ordering).

### Constant: `_GATE_DECISION_VALUES`

Definition:

```python
_GATE_DECISION_VALUES = ("approved", "deferred", "rejected")
```

Role:

- Defines the three valid HITL review decisions.
- Used by `GateReview.__post_init__` for validation.

### Dataclass: `GateCriterion`

Fields:

```python
criterion_id: str
description: str
artifact_path: str
threshold: str
passed: bool
evidence: str
missing: str
```

Role:

- Immutable single-criterion check result.
- Records whether the criterion passed, what evidence was observed, and what is missing if it failed.

Field meanings:

| Field | Meaning |
| --- | --- |
| `criterion_id` | Short machine-readable identifier (e.g., `"macro_f1_exceeds_baselines"`). |
| `description` | Human-readable one-line description of what this criterion checks. |
| `artifact_path` | Path to the evidence artifact that was read (as a string, for documentation). |
| `threshold` | Human-readable threshold description (e.g., `"CMD-Audit macro_f1 > evidence_recall AND subagent_judge AND random_label"`). |
| `passed` | Whether the criterion is satisfied. |
| `evidence` | Concise summary of observed values (e.g., `"CMD-Audit macro_f1=1.000; evidence_recall=0.778"`). |
| `missing` | Empty string if passed; description of what is missing if failed. |

### Dataclass: `GateResult`

Fields:

```python
gate_id: str
criteria: tuple[GateCriterion, ...]
all_passed: bool
checked_at: str
```

Role:

- Immutable aggregate result of checking all criteria for a version gate.
- `all_passed` is `True` only when every criterion in `criteria` has `passed=True`.

### Dataclass: `GateReview`

Fields:

```python
gate_id: str
reviewer: str
decision: str
rationale: str
missing_evidence: str
reviewed_at: str
```

Role:

- Immutable HITL review decision for a version gate.
- The `decision` field is validated to be one of `("approved", "deferred", "rejected")`.

#### `GateReview.__post_init__(self) -> None`

Purpose:

- Validates that `decision` is one of the three allowed values.

Behavior:

- Raises `ValueError` if `decision` is not in `_GATE_DECISION_VALUES`.

Why it matters:

- Prevents ambiguous or undefined review decisions from entering the gate tracking document.
- The three values map to: approved (gate passed, version locked), deferred (evidence insufficient, need more data), rejected (gate failed, version cannot be claimed).

### Function: `check_v0_to_v1_gate(artifacts_dir=None, sandbox_dir=None) -> GateResult`

Purpose:

- Checks all four V0→V1 evidence gate criteria against the current artifacts.

Signature:

```python
def check_v0_to_v1_gate(
    artifacts_dir: Path | None = None,
    sandbox_dir: Path | None = None,
) -> GateResult
```

Inputs:

- `artifacts_dir`: path to the artifacts directory (default `Path("artifacts")`).
- `sandbox_dir`: path to the sandbox directory (default `Path("artifacts/sandbox")`).

Behavior:

1. Resolves artifact and sandbox directory paths (defaulting to project-relative paths).
2. Runs four criterion checks in order:
   - `_check_macro_f1(artifacts_dir / "comparison_metrics.csv")`
   - `_check_confusion_diagonal(artifacts_dir / "attribution_confusion_matrix.csv")`
   - `_check_accuracy_top2(artifacts_dir / "comparison_metrics.csv")`
   - `_check_repair_distribution(sandbox_dir / "post_repair_table.csv")`
3. Sets `all_passed = all(c.passed for c in criteria)`.
4. Records the current UTC timestamp as `checked_at`.
5. Returns `GateResult(gate_id="V0→V1", criteria=..., all_passed=..., checked_at=...)`.

Edge cases:

- If an artifact file is missing, the corresponding criterion returns `passed=False` with `missing` describing the missing file.
- If an artifact is malformed (e.g., missing expected columns), the criterion returns `passed=False` with the exception message in `missing`.

Domain boundary:

- The function does not modify any artifacts or write to disk.
- It reads CSV files directly; it does not use the CMD harness.
- The `all_passed` field is a programmatic summary, not the final gate decision. The final decision is HITL.

Callers:

- Tests (`V0V1GateCheckWithRealArtifactsTest`, `V0V1GateCheckWithTempArtifactsTest`, `GatesDoNotBlockImplementationTest`).
- Artifact generation scripts.
- External users importing from `cmd_audit`.

### Function: `check_v1_to_v2_gate() -> GateResult`

Purpose:

- Returns a stub V1→V2 gate result indicating the gate is not yet evaluable.

Signature:

```python
def check_v1_to_v2_gate() -> GateResult
```

Behavior:

1. Creates a single `GateCriterion` with:
   - `criterion_id="adapter_integration_count"`
   - `description="At least two distinct memory agents integrated through the Adapter Interface without macro F1 regression"`
   - `artifact_path="(none — adapter integrations do not exist in V0)"`
   - `threshold="adapter_count >= 2 AND no macro F1 regression"`
   - `passed=False`
   - `evidence="0 adapter integrations; V0 operates as standalone harness."`
   - `missing="No Adapter Interface integrations exist. V1 must integrate at least two distinct memory agents before V1→V2 gate review."`
2. Records the current UTC timestamp as `checked_at`.
3. Returns `GateResult(gate_id="V1→V2", criteria=(criterion,), all_passed=False, checked_at=...)`.

Domain meaning:

- The V1→V2 gate is defined but always returns not-met in V0. It serves as a forward reference for the V1 roadmap.
- The gate criterion is intentionally single-criterion (adapter count + non-regression). As V1 progresses, additional criteria may be added.

### Function: `write_gate_status(result, output_path, sandbox_root=None) -> Path`

Purpose:

- Writes a human-readable gate status document to the sandbox.

Signature:

```python
def write_gate_status(
    result: GateResult,
    output_path: Path,
    sandbox_root: str | Path | None = None,
) -> Path
```

Inputs:

- `result`: the `GateResult` from `check_v0_to_v1_gate` or `check_v1_to_v2_gate`.
- `output_path`: the file path to write (must be inside the sandbox).
- `sandbox_root`: optional sandbox root for path validation.

Behavior:

1. Validates `output_path` is inside the sandbox via `validate_sandbox_path(output_path, sandbox_root)`.
2. Creates parent directories as needed.
3. Writes a structured text document with:
   - Header: gate ID, date, separator line.
   - `All criteria passed: True/False`.
   - Per-criterion block: criterion number, ID, PASS/FAIL status, description, artifact path, threshold, evidence, missing (if any).
   - Footer: "Final decision: HITL review required.", checked timestamp.
4. Returns the written path.

Output format:

```text
CMD V0→V1 Gate Status — 2026-05-10
============================================================

All criteria passed: True

Criterion 1: macro_f1_exceeds_baselines [PASS]
  Description: CMD macro F1 exceeds all comparator baselines
  Artifact:    artifacts/comparison_metrics.csv
  Threshold:   CMD-Audit macro_f1 > evidence_recall AND subagent_judge AND random_label
  Evidence:    CMD-Audit macro_f1=1.000; evidence_recall=0.778; subagent_judge=0.778; random_label=0.167

...

---
Final decision: HITL review required.
Checked at: 2026-05-10T...
```

Callers:

- Artifact generation scripts.
- Tests (`GateStatusWriteTest`).

### Function: `write_gate_review(review, output_path, sandbox_root=None) -> Path`

Purpose:

- Writes a dated HITL gate review note to the sandbox.

Signature:

```python
def write_gate_review(
    review: GateReview,
    output_path: Path,
    sandbox_root: str | Path | None = None,
) -> Path
```

Inputs:

- `review`: the `GateReview` with the HITL decision.
- `output_path`: the file path to write (must be inside the sandbox).
- `sandbox_root`: optional sandbox root for path validation.

Behavior:

1. Validates `output_path` is inside the sandbox via `validate_sandbox_path(output_path, sandbox_root)`.
2. Creates parent directories as needed.
3. Writes a structured text document with:
   - Header: gate ID, date.
   - Reviewer, decision, reviewed timestamp.
   - Rationale section.
   - Missing evidence section (if non-empty).
4. Returns the written path.

Output format:

```text
CMD V0→V1 Gate Review — 2026-05-10
============================================================

Reviewer:   HITL
Decision:   deferred
Reviewed:   2026-05-10T12:00:00Z

Rationale:
  All four criteria pass on 6-case smoke suite. ...

Missing evidence:
  Smoke suite (6 cases) is insufficient for paper claims. ...
```

Callers:

- Artifact generation scripts.
- Tests (`GateReviewWriteTest`).

### Internal: `_read_comparison_csv(path) -> dict[str, dict[str, float]]`

Purpose:

- Reads `comparison_metrics.csv` and returns a nested dict: `{system_name: {column_name: value}}`.

Behavior:

1. Raises `FileNotFoundError` if the file does not exist.
2. Parses CSV with `csv.DictReader`.
3. Converts all non-`system_name` columns to `float`.
4. Returns `{row["system_name"]: {col: float(val), ...}, ...}`.

Used by:

- `_check_macro_f1`
- `_check_accuracy_top2`

### Internal: `_read_confusion_csv(path) -> dict[str, dict[str, int]]`

Purpose:

- Reads `attribution_confusion_matrix.csv` and returns a nested dict: `{gold_label: {pred_label: count}}`.

Behavior:

1. Raises `FileNotFoundError` if the file does not exist.
2. Parses CSV with `csv.DictReader`.
3. Converts all non-`gold_label` columns to `int`.
4. Returns `{row["gold_label"]: {col: int(val), ...}, ...}`.

Used by:

- `_check_confusion_diagonal`

### Internal: `_read_repair_csv(path) -> list[str]`

Purpose:

- Reads `post_repair_table.csv` and returns a list of `repair_assessment` values.

Behavior:

1. Raises `FileNotFoundError` if the file does not exist.
2. Parses CSV with `csv.DictReader`.
3. Returns `[row["repair_assessment"], ...]` for each data row.

Used by:

- `_check_repair_distribution`

### Internal: `_check_macro_f1(comparison_path) -> GateCriterion`

Purpose:

- Checks whether CMD-Audit macro F1 exceeds all three comparator baselines.

Behavior:

1. Reads `comparison_metrics.csv` via `_read_comparison_csv`.
2. Extracts `macro_f1` for CMD-Audit, evidence_recall, subagent_judge, and random_label.
3. `passed = cmd_macro_f1 > max(evidence_recall, subagent_judge, random_label)`.
4. If any baseline has equal or higher macro F1, `passed=False` and `missing` records the gap.
5. On `FileNotFoundError` or `KeyError`: returns `passed=False` with the exception in `evidence` and `missing`.

Threshold:

```text
CMD-Audit macro_f1 > evidence_recall AND subagent_judge AND random_label
```

Domain meaning:

- Macro F1 is the primary paper-claim metric for attribution quality.
- CMD must strictly exceed (not equal) all baselines. Equality would not demonstrate improvement.

Caller:

- `check_v0_to_v1_gate`

### Internal: `_check_confusion_diagonal(confusion_path) -> GateCriterion`

Purpose:

- Checks that the confusion matrix has diagonal dominance for all six V0 labels.

Behavior:

1. Reads `attribution_confusion_matrix.csv` via `_read_confusion_csv`.
2. Iterates all six labels from `V0_PIPELINE_LABEL_ORDER`.
3. For each label row: computes `diagonal` (self-prediction count) and `off_diagonal_sum` (sum of all other columns).
4. `passed = diagonal > off_diagonal_sum` for every label.
5. Collects violation strings for any label where `diagonal <= off_diagonal_sum`.
6. On success: evidence = "All 6 V0 labels have diagonal > off-diagonal sum".
7. On failure: missing = concatenated violation strings.
8. On `FileNotFoundError` or `KeyError`: returns `passed=False` with the exception in `evidence` and `missing`.

Threshold:

```text
For each V0 label row: diagonal > sum of off-diagonal entries
```

Domain meaning:

- Diagonal dominance means each label is more often correct than confused with any other label.
- With 1 case per label, diagonal=1 and off-diagonal=0 is trivially true. As the probe suite scales, off-diagonal entries will appear and this criterion will become discriminating.

Caller:

- `check_v0_to_v1_gate`

### Internal: `_check_accuracy_top2(comparison_path) -> GateCriterion`

Purpose:

- Checks whether CMD-Audit outperforms all baselines on both attribution accuracy and top-2 accuracy.

Behavior:

1. Reads `comparison_metrics.csv` via `_read_comparison_csv`.
2. Extracts `attribution_accuracy` and `top2_accuracy` for CMD-Audit and all three baselines.
3. `acc_ok = cmd_attribution_accuracy > max(baseline_attribution_accuracy)`.
4. `top2_ok = cmd_top2_accuracy > max(baseline_top2_accuracy)`.
5. `passed = acc_ok AND top2_ok`.
6. On failure: `missing` records which sub-criterion failed and the specific gap.
7. On `FileNotFoundError` or `KeyError`: returns `passed=False` with the exception in `evidence` and `missing`.

Threshold:

```text
CMD-Audit attribution_accuracy > all baselines AND CMD-Audit top2_accuracy > all baselines
```

Domain meaning:

- Attribution accuracy measures exact-match correctness; top-2 accuracy measures whether the correct label is in the top two.
- Both must exceed all baselines. This is stricter than requiring only one of the two.
- `random_label` is included as a sanity check baseline but is not the discriminating comparator.

Caller:

- `check_v0_to_v1_gate`

### Internal: `_check_repair_distribution(repair_path) -> GateCriterion`

Purpose:

- Checks whether the post-repair assessment distribution supports the repair-validity claim.

Behavior:

1. Reads `post_repair_table.csv` via `_read_repair_csv`.
2. Counts `recovered`, `partial`, and `failed` assessments.
3. Computes `recovered_rate = recovered / total`.
4. Computes `majority_improves = (recovered + partial) > failed`.
5. `passed = recovered_rate >= 0.5 AND majority_improves`.
6. On success: evidence includes counts and rate.
7. On failure: `missing` records which sub-criterion failed.
8. On empty table (0 rows): `passed=False` with "Post-repair table is empty".
9. On `FileNotFoundError` or `KeyError`: returns `passed=False` with the exception.

Threshold:

```text
recovered_rate >= 0.5 AND recovered + partial > failed
```

Why two sub-criteria:

- `recovered_rate >= 0.5`: the majority of repair assessments should be full recoveries.
- `recovered + partial > failed`: even if the recovery rate is below 0.5, the total of improved cases (recovered + partial) should exceed failed cases. This sub-criterion alone is not sufficient — both must hold.

Domain meaning:

- `partial` cases (evidence recovered, answer still wrong) are positive diagnostic signal — they expose coupled failures.
- The threshold requires BOTH a majority recovery rate AND more improvements than failures.
- This two-part test prevents gaming: you can't pass with 10% recovered + 41% partial (rec+partial > failed but recovered_rate < 0.5), and you can't pass with 50% recovered + 0% partial + 50% failed (rec+partial == failed).

Caller:

- `check_v0_to_v1_gate`

## `cmd_audit/__init__.py` Public Surface

Issue 0010 exports:

- `GateCriterion`
- `GateResult`
- `GateReview`
- `check_v0_to_v1_gate`
- `check_v1_to_v2_gate`
- `write_gate_status`
- `write_gate_review`

Why export them:

- Tests can import and verify gate behavior through the public surface.
- Future issues (e.g., CI integration, automated gate checks) can use the stable API.
- The HITL review pipeline is callable from scripts without importing internal helpers.

## Test Coverage

Test file:

```text
tests/test_cmd_audit_issue10_version_gates.py
```

48 tests across 14 test classes.

### `GateCriterionCreationTest` (3 tests)

**`test_passed_criterion`**

Verifies:

- A GateCriterion with `passed=True` has correct field values.
- `missing` is empty for passing criteria.

**`test_failed_criterion_with_missing`**

Verifies:

- A GateCriterion with `passed=False` has non-empty `missing`.
- `evidence` records observed values even when failing.

**`test_criterion_immutable`**

Verifies:

- GateCriterion is frozen; mutation raises an exception.

### `GateResultCreationTest` (3 tests)

**`test_result_all_passed_true`**

Verifies:

- When all criteria pass, `all_passed=True`.

**`test_result_all_passed_false`**

Verifies:

- When any criterion fails, `all_passed=False`.

**`test_result_immutable`**

Verifies:

- GateResult is frozen; mutation raises an exception.

### `GateReviewCreationTest` (3 tests)

**`test_valid_review`**

Verifies:

- A GateReview with decision="approved" is created successfully.

**`test_review_rejects_invalid_decision`**

Verifies:

- A GateReview with decision="maybe_later" raises `ValueError`.

**`test_deferred_review_with_missing`**

Verifies:

- A deferred review with missing_evidence correctly stores both fields.

### `ComparisonCSVReaderTest` (2 tests)

**`test_reads_all_systems`**

Verifies:

- `_read_comparison_csv` parses system_name-keyed rows with float values.

**`test_missing_file_raises`**

Verifies:

- Missing file raises `FileNotFoundError`.

### `ConfusionCSVReaderTest` (2 tests)

**`test_reads_matrix`**

Verifies:

- `_read_confusion_csv` parses gold_label-keyed rows with int counts.

**`test_missing_file_raises`**

Verifies:

- Missing file raises `FileNotFoundError`.

### `RepairCSVReaderTest` (1 test)

**`test_reads_assessments`**

Verifies:

- `_read_repair_csv` returns a list of `repair_assessment` strings in order.

### `MacroF1CheckTest` (4 tests)

**`test_passes_when_cmd_beats_all_baselines`**

Verifies:

- CMD macro_f1=0.92 > all baselines (0.78, 0.80, 0.17) → passes.

**`test_fails_when_baseline_beats_cmd`**

Verifies:

- CMD macro_f1=0.70 < evidence_recall=0.85 → fails, missing contains "0.70".

**`test_fails_when_cmd_missing_from_csv`**

Verifies:

- CSV without CMD-Audit row → fails.

**`test_fails_when_artifact_missing`**

Verifies:

- Non-existent file → fails, missing contains "not found".

### `ConfusionDiagonalCheckTest` (2 tests)

**`test_passes_with_perfect_diagonal`**

Verifies:

- Identity matrix (diagonal=1, off-diagonal=0) for all 6 labels → passes.

**`test_fails_with_off_diagonal`**

Verifies:

- write_error row has 2 off-diagonal entries → fails, missing contains "write_error".

### `AccuracyTop2CheckTest` (3 tests)

**`test_passes_when_cmd_beats_all`**

Verifies:

- CMD accuracy=0.95 and top2=1.0 > all baselines → passes.

**`test_fails_when_accuracy_lower`**

Verifies:

- CMD accuracy=0.70 < evidence_recall=0.83 → fails.

**`test_fails_when_top2_lower`**

Verifies:

- CMD top2=0.65 < evidence_recall=0.83 → fails.

### `RepairDistributionCheckTest` (5 tests)

**`test_passes_with_high_recovery`**

Verifies:

- 3 recovered, 1 partial, 1 failed → recovered_rate=0.6, rec+partial(4) > failed(1) → passes.

**`test_fails_when_below_recovery_threshold`**

Verifies:

- 1 recovered, 2 partial, 1 failed → recovered_rate=0.25 < 0.5 → fails. (rec+partial > failed alone is not sufficient.)

**`test_fails_when_recovery_rate_low`**

Verifies:

- 1 recovered, 4 failed → recovered_rate=0.2 < 0.5 AND rec+partial(1) < failed(4) → fails.

**`test_fails_when_failed_dominates`**

Verifies:

- 1 recovered, 2 failed → rec+partial(1) <= failed(2) → fails.

**`test_fails_with_empty_table`**

Verifies:

- CSV with headers only, no data rows → fails with "Post-repair table is empty".

### `V0V1GateCheckWithRealArtifactsTest` (4 tests)

**`test_all_criteria_pass_with_current_artifacts`**

Verifies:

- `check_v0_to_v1_gate()` against the real project artifacts returns `all_passed=True` with 4 criteria.

**`test_criterion_ids_match_spec`**

Verifies:

- The four criterion IDs match the spec exactly in the expected order.

**`test_result_is_immutable`**

Verifies:

- The returned GateResult is frozen.

**`test_each_criterion_has_evidence`**

Verifies:

- All criteria have non-empty `evidence` strings.

### `V0V1GateCheckWithTempArtifactsTest` (5 tests)

**`test_all_pass_with_passing_artifacts`**

Verifies:

- End-to-end: passing temp artifacts → `all_passed=True`.

**`test_fails_when_comparison_missing`**

Verifies:

- Deleting `comparison_metrics.csv` → 2 criteria fail (macro_f1 and accuracy_top2 both depend on it).

**`test_fails_when_confusion_missing`**

Verifies:

- Deleting `attribution_confusion_matrix.csv` → confusion_diagonal_dominance fails.

**`test_fails_when_repair_missing`**

Verifies:

- Deleting `post_repair_table.csv` → repair_assessment_distribution fails.

**`test_fails_when_macro_f1_insufficient`**

Verifies:

- Overwriting comparison_metrics with CMD macro_f1=0.50 < baselines → macro_f1 criterion fails.

### `V1V2GateCheckTest` (2 tests)

**`test_returns_not_met_stub`**

Verifies:

- `check_v1_to_v2_gate()` returns `all_passed=False` with one criterion `adapter_integration_count`.
- Evidence contains "0 adapter integrations".

**`test_result_has_timestamp`**

Verifies:

- Result has an ISO timestamp with "T" separator.

### `GateStatusWriteTest` (4 tests)

**`test_writes_status_file`**

Verifies:

- `write_gate_status` writes a file containing "V0→V1" and "PASS".

**`test_output_contains_all_criteria`**

Verifies:

- Output contains both passing and failing criterion IDs, PASS/FAIL status tags, and missing text.

**`test_sandbox_path_enforced`**

Verifies:

- Writing to a path outside the sandbox raises `ValueError`.

**`test_creates_parent_directories`**

Verifies:

- Writing to a deeply nested path under sandbox creates parent directories and succeeds.

### `GateReviewWriteTest` (3 tests)

**`test_writes_review_file`**

Verifies:

- `write_gate_review` writes a file containing gate ID, decision, and reviewer.

**`test_dated_review_format`**

Verifies:

- Review file contains the date, deferred decision, rationale, and missing evidence text.

**`test_sandbox_path_enforced`**

Verifies:

- Writing to a path outside the sandbox raises `ValueError`.

### `GatesDoNotBlockImplementationTest` (2 tests)

**`test_gate_check_runs_independently`**

Verifies:

- `check_v0_to_v1_gate()` runs without importing from harness, baselines, or other implementation modules.
- The function does not write to disk by itself.

**`test_v1_v2_stub_does_not_crash`**

Verifies:

- `check_v1_to_v2_gate()` returns a valid GateResult without crashing.

## Acceptance Criteria Traceability

| Issue 0010 AC | Code Surface | Test Surface |
| --- | --- | --- |
| V0→V1 gate defined with four criteria. | `check_v0_to_v1_gate` runs four criterion checks; `V0V1_CRITERION_IDS` documents the four IDs. | `test_criterion_ids_match_spec`, `test_all_criteria_pass_with_current_artifacts` |
| V1→V2 gate defined with adapter integration criterion. | `check_v1_to_v2_gate` returns a single-criterion GateResult with `adapter_integration_count`. | `test_returns_not_met_stub` |
| Gate status tracked in a dedicated document, not code. | `cmd_innovation_core/gates/V0V1_gate_status.md` is the tracking document. `write_gate_status` produces the sandbox artifact. | `test_writes_status_file` |
| Each gate check documented with dated review note recording artifacts inspected, threshold result, and human decision. | `GateReview` records gate_id, reviewer, decision, rationale, missing_evidence, and reviewed_at timestamp. `write_gate_review` produces the dated review artifact. | `test_dated_review_format`, `test_writes_review_file` |
| If gate not met, note records specific evidence missing and what must complete before re-review. | `GateCriterion.missing` records what is missing; `GateReview.missing_evidence` records what must be addressed. | `test_failed_criterion_with_missing`, `test_deferred_review_with_missing` |
| Gates do not block ongoing implementation; they gate the version lock claim only. | `check_v0_to_v1_gate` is a read-only function that does not write to disk or affect other modules. `version_gates.py` does not import from harness or baselines. | `test_gate_check_runs_independently` |

## Current Artifact Semantics

Current `artifacts/sandbox/V0V1_gate_status.txt`:

```text
CMD V0→V1 Gate Status — 2026-05-10
============================================================

All criteria passed: True

Criterion 1: macro_f1_exceeds_baselines [PASS]
  ...
  Evidence:    CMD-Audit macro_f1=1.000; evidence_recall=0.778; subagent_judge=0.778; random_label=0.167

Criterion 2: confusion_diagonal_dominance [PASS]
  ...
  Evidence:    All 6 V0 labels have diagonal > off-diagonal sum

Criterion 3: accuracy_top2_exceeds_baselines [PASS]
  ...
  Evidence:    CMD-Audit attribution_accuracy=1.000 (best baseline=0.833); CMD-Audit top2_accuracy=1.000 (best baseline=0.833)

Criterion 4: repair_assessment_distribution [PASS]
  ...
  Evidence:    6 cases: recovered=6, partial=0, failed=0 (recovered_rate=1.000)
```

Current `artifacts/sandbox/V0V1_gate_review.txt`:

```text
CMD V0→V1 Gate Review — 2026-05-10
============================================================

Reviewer:   HITL
Decision:   deferred
...

Rationale:
  All four criteria pass on 6-case smoke suite. However, the PRD targets 50-100 probe cases...
```

Interpretation:

- All four criteria pass against the 6-case smoke suite because the suite is small enough to produce ceiling effects (perfect macro F1, perfect confusion matrix, 100% recovery).
- The HITL review is deferred because 6 cases (1 per label) is insufficient evidence for a paper claim. The PRD targets 50-100 cases.
- The gate check infrastructure is operational. It will produce discriminating results as the probe suite scales:
  - Macro F1 will regress from 1.000 toward realistic values.
  - Off-diagonal confusion entries will appear when multiple cases share a label.
  - `partial` and `failed` repair assessments will emerge with more complex cases.
- The V0→V1 gate is not yet locked. The version remains unlocked until HITL approves the gate review.

## Verification

Commands:

```bash
python3 -m pytest tests/test_cmd_audit_issue10_version_gates.py -v
python3 -m pytest tests/ -q
python3 -m compileall cmd_audit tests
```

Expected state:

- All 48 issue 0010 tests pass.
- All 175 total tests pass (127 existing + 48 new).
- `artifacts/sandbox/V0V1_gate_status.txt` is generated.
- `artifacts/sandbox/V0V1_gate_review.txt` is generated.
- `cmd_innovation_core/gates/V0V1_gate_status.md` is the gate tracking document.

## Non-Goals Preserved

- No production CI/CD integration (gates are checked locally, not in a pipeline).
- No automatic version locking (HITL decision is required).
- No blocking of implementation work (gates gate the version claim, not development).
- No expansion of the V0 label set or attribution taxonomy.
- No modification of evidence artifacts (gate checks are read-only).
- No dependency on the CMD harness, baselines, replays, or models (reads CSV files directly).
- No remote gate status tracking (local documents only).

## Next Technical Step

Issue 0010 completes the V0 CMD-Audit governance layer. The V0 evidence chain is now structurally complete:

1. `attribution_table.csv` + `comparison_metrics.csv` + `attribution_confusion_matrix.csv` — issues 0002/0003
2. Post-Repair Context Replay table — issue 0005
3. Targeted repair-success table + claim ledger — issue 0006
4. ECS Failure Memory recurrence comparison — issue 0007
5. Evidence-driven version gates — issue 0010

All four V0 gate criteria pass on the 6-case smoke suite. The HITL review is deferred pending probe suite scaling.

The next slice is **issue 0008** (V0.5 retrieval baseline strengthening), a follow-up not on the V0 critical path. Parallel work: scale the probe suite from 6 to 50-100 cases to produce discriminating evidence for the V0→V1 gate review.
