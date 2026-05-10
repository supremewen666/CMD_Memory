# Issue 0009 Implementation Details: Harden Subagent Judge Monitor Contract

## Purpose

This document is the zoomed-out implementation map for issue 0009, `Harden Subagent Judge Monitor contract`.

Issue 0009 takes the leak-safe Subagent Judge Monitor from issue 0002 and hardens its contract at the implementation level:

```text
SubagentJudgeMonitorDecision construction
  -> anomaly_reason enum validation
  -> evidence_pointers opaque-ID validation
  -> forbidden field blocklist check
  -> payload serialization gate
  -> MonitorAnomalyReasonError / LeakSafeMonitorError rejection
```

The implemented slice locks the monitor's `anomaly_reason` to a four-value enum, restricts evidence pointers to opaque IDs only, and rejects any output that includes free-form natural language, final labels, ECS, gold answers, memory writes, or full failed traces. All validation fires at construction time (`__post_init__`) and serialization time (`to_payload`).

## Source Requirements

The implementation follows these local documents.

| Source | Requirement Applied In Issue 0009 |
| --- | --- |
| `TASK.md` | Subagent Judge Monitor `anomaly_reason` locked to a predefined enum; free-form natural language prohibited; evidence pointers are opaque IDs only. |
| `CLAUDE.md` | Subagent Judge Monitor is leak-safe: may trigger replay but must not emit final labels, ECS, memory writes, gold answers, or full failed traces; `anomaly_reason` locked to enum; evidence pointers opaque IDs only. |
| `cmd_innovation_core/CONTEXT.md` | **Subagent Judge Monitor** `anomaly_reason` forced to predefined enum (`answer_vs_evidence_mismatch`, `retrieved_context_incomplete`, `evidence_recall_low`, `confidence_anomaly`); free-form natural language prohibited; evidence pointers opaque IDs only, never content text. |
| `cmd_innovation_core/prd/cmd_minimal_probe_prd.md` | AC6: Monitor `anomaly_reason` locked to predefined enum; free-form natural language prohibited; evidence pointers opaque IDs only. User stories 6/27/28. |
| `cmd_innovation_core/issues/0009-harden-subagent-judge-monitor-contract.md` | Lock `anomaly_reason` to enum; restrict evidence pointers to opaque IDs; reject free-form text; test at contract boundary. |
| `cmd_innovation_core/tdd/cmd_tracer_bullets.md` | Cycle 11: free-form `anomaly_reason` rejected; Cycle 8: forbidden payload fields rejected. |
| `cmd_innovation_core/issues/0002-baselines-and-judge-monitor-implementation-details.md` | Provides the pre-hardening monitor foundation (`SubagentJudgeMonitorDecision` with free-form `reasons`, `validate_monitor_payload`, `FORBIDDEN_MONITOR_FIELDS`). |

## Domain Boundary

Issue 0009 hardens the issue 0002 monitor contract. It does not change the monitor's role or introduce new monitor capabilities.

```text
run_subagent_judge_monitor(case, baseline)
  -> risk_score computation (unchanged from issue 0002)
  -> anomaly_reason selection from enum (NEW)
  -> evidence_pointers from baseline retrieved_memory_ids (NEW)
  -> SubagentJudgeMonitorDecision construction
      -> __post_init__:
          -> validate_monitor_anomaly_reason(anomaly_reason)  (NEW)
          -> validate_evidence_pointers(evidence_pointers)     (NEW)
      -> to_payload:
          -> validate_monitor_payload(payload)
              -> _reject_forbidden_monitor_fields              (unchanged from 0002)
```

It does own:

- defining the four-value `MONITOR_ANOMALY_REASON_VALUES` enum;
- adding `MonitorAnomalyReasonError` for enum validation failures;
- adding `validate_monitor_anomaly_reason()` as the enum gate;
- adding `_is_opaque_id()` and `validate_evidence_pointers()` for opaque-ID enforcement;
- updating `SubagentJudgeMonitorDecision` fields: `reasons` → `anomaly_reason` + `evidence_pointers`;
- updating `run_subagent_judge_monitor()` to produce enum values and opaque pointers;
- updating `SubagentJudgeMonitorDecision.to_payload()` to serialize new fields;
- behavior-level tests for enum rejection, opaque-ID rejection, and end-to-end contract.

It does not own:

- changing the monitor's role (still a replay trigger, not a diagnosis source);
- changing `FORBIDDEN_MONITOR_FIELDS` or `_reject_forbidden_monitor_fields` (issue 0002 owns those);
- adding new monitor decision logic (risk_score computation is unchanged);
- changing `validate_monitor_payload` behavior;
- CMD-Audit attribution or replay logic;
- ECS, Post-Repair Context Replay, or Failure Memory.

## Module Map

| Module | Issue 0009 Role |
| --- | --- |
| `cmd_audit/labels.py` | Owns `MONITOR_ANOMALY_REASON_VALUES`, `VALID_MONITOR_ANOMALY_REASONS`, `MonitorAnomalyReasonError`, and `validate_monitor_anomaly_reason()`. |
| `cmd_audit/baselines.py` | Owns `_is_opaque_id()`, `validate_evidence_pointers()`, updated `SubagentJudgeMonitorDecision`, and updated `run_subagent_judge_monitor()`. |
| `cmd_audit/__init__.py` | Exports new public surface: `MONITOR_ANOMALY_REASON_VALUES`, `MonitorAnomalyReasonError`, `SubagentJudgeMonitorDecision`, `validate_evidence_pointers`, `validate_monitor_anomaly_reason`, `validate_monitor_payload`. |
| `tests/test_cmd_audit_issue9_monitor_contract.py` | Behavior-level tests for the hardened contract: enum validation, opaque-ID validation, forbidden field blocklist, and end-to-end payload shape. |

## Caller Graph

Main CLI path (updated from issue 0002):

```text
cmd_audit.__main__
  -> cli.main
      -> models.load_probe_cases
      -> harness.run_cases
          -> harness.run_case
              -> baselines.run_baseline_suite
                  -> baselines.run_subagent_judge_monitor
                      -> risk_score computation (unchanged)
                      -> anomaly_reason selection (NEW: priority-based enum choice)
                      -> evidence_pointers = tuple(baseline.retrieved_memory_ids) (NEW)
                      -> SubagentJudgeMonitorDecision(...)  (NEW: anomaly_reason + evidence_pointers)
                          -> __post_init__:
                              -> labels.validate_monitor_anomaly_reason (NEW)
                              -> baselines.validate_evidence_pointers (NEW)
                                  -> baselines._is_opaque_id (NEW)
                      -> SubagentJudgeMonitorDecision.to_payload (updated: anomaly_reason + evidence_pointers)
                          -> baselines.validate_monitor_payload
                              -> baselines._reject_forbidden_monitor_fields (unchanged)
              -> replays.run_v0_replay_portfolio
              -> attribution.assign_attribution
      -> harness.write_attribution_table
      -> harness.write_comparison_metrics_table
      -> harness.write_confusion_matrix_table
```

Behavior-test path:

```text
tests/test_cmd_audit_issue9_monitor_contract.py
  -> validate_monitor_anomaly_reason
  -> validate_evidence_pointers
  -> SubagentJudgeMonitorDecision(...)
  -> validate_monitor_payload
  -> load_probe_cases
  -> run_baseline_suite
```

## Data Flow

Input (shared with issue 0002):

```text
data/probe_cases/v0_issue3_cases.json
```

Per-case monitor decision output shape (updated):

```text
SubagentJudgeMonitorDecision
  should_trigger_replay: bool
  risk_score: float
  anomaly_reason: str          # enum value (was: reasons: tuple[str, ...])
  evidence_pointers: tuple[str, ...]  # opaque IDs (NEW)
  trace_summary: str
  cost_per_decision: float
```

Harness-level output (unchanged structure, updated monitor fields):

```text
BaselineSuiteResult
  monitor: SubagentJudgeMonitorDecision
```

Payload shape (serialized):

```json
{
  "should_trigger_replay": true,
  "risk_score": 0.9,
  "anomaly_reason": "evidence_recall_low",
  "evidence_pointers": ["mem_001"],
  "trace_summary": "vector_memory: answer_score=0.000; ...",
  "cost_per_decision": 0.2
}
```

## Function-Level Contract

### `cmd_audit/labels.py`

Issue 0009 adds three new constants, one new exception, and one new validation function to this module. All existing issue 0001/0002 constants and functions are preserved unchanged.

#### Constant: `MONITOR_ANOMALY_REASON_VALUES`

Definition:

```python
MONITOR_ANOMALY_REASON_VALUES = (
    "answer_vs_evidence_mismatch",
    "retrieved_context_incomplete",
    "evidence_recall_low",
    "confidence_anomaly",
)
```

Purpose:

- Defines the exhaustive set of valid `anomaly_reason` values for the Subagent Judge Monitor.
- Tuple ordering is stable for documentation and iteration, but does not imply priority (priority is defined in `run_subagent_judge_monitor`).

Domain meaning of each value:

| Enum Value | Meaning |
| --- | --- |
| `answer_vs_evidence_mismatch` | Baseline context has adequate evidence recall but the answer is wrong — suggests reasoning failure. |
| `retrieved_context_incomplete` | Baseline did not retrieve any memory items at all — context is empty. |
| `evidence_recall_low` | Baseline retrieved memory items but evidence recall is below threshold — retrieval may have missed relevant items. |
| `confidence_anomaly` | Risk score is anomalous but no specific signal dominates — catch-all for edge cases. |

Used by:

- `validate_monitor_anomaly_reason(...)`
- `SubagentJudgeMonitorDecision.__post_init__()`
- Tests that iterate over all valid reasons.

#### Constant: `VALID_MONITOR_ANOMALY_REASONS`

Definition:

```python
VALID_MONITOR_ANOMALY_REASONS = frozenset(MONITOR_ANOMALY_REASON_VALUES)
```

Purpose:

- O(1) membership check for `validate_monitor_anomaly_reason(...)`.
- Exists as a frozenset to prevent accidental mutation.

#### Exception: `MonitorAnomalyReasonError`

Definition:

```python
class MonitorAnomalyReasonError(ValueError):
    """Raised when monitor anomaly_reason is not a valid enum value."""
```

Purpose:

- Signals that a monitor `anomaly_reason` value violates the enum contract.
- Distinct from `LabelValidationError` (label scope) and `LeakSafeMonitorError` (forbidden fields).

Raised by:

- `validate_monitor_anomaly_reason(...)`

Caught by:

- `SubagentJudgeMonitorDecision.__post_init__()` — propagates to caller on invalid construction.

#### Function: `validate_monitor_anomaly_reason(reason: str) -> str`

Signature:

```python
def validate_monitor_anomaly_reason(reason: str) -> str
```

Purpose:

- Validates that a monitor `anomaly_reason` string is one of the four allowed enum values.
- Returns the reason unchanged on success, enabling use as a pass-through validator.

Behavior:

1. Checks `reason in VALID_MONITOR_ANOMALY_REASONS` (frozenset lookup).
2. If valid: returns `reason`.
3. If invalid: raises `MonitorAnomalyReasonError` with a message that includes the invalid value and the list of valid values.

Validation rules:

| Input | Result |
| --- | --- |
| `"evidence_recall_low"` | Returns `"evidence_recall_low"` |
| `"answer_vs_evidence_mismatch"` | Returns `"answer_vs_evidence_mismatch"` |
| `"retrieved_context_incomplete"` | Returns `"retrieved_context_incomplete"` |
| `"confidence_anomaly"` | Returns `"confidence_anomaly"` |
| `"the answer looks wrong"` | Raises `MonitorAnomalyReasonError` |
| `"evidence_recall_low "` (trailing space) | Raises `MonitorAnomalyReasonError` |
| `"Confidence_Anomaly"` (wrong case) | Raises `MonitorAnomalyReasonError` |
| `""` (empty string) | Raises `MonitorAnomalyReasonError` |

Callers:

- `SubagentJudgeMonitorDecision.__post_init__()`
- Direct tests in `MonitorAnomalyReasonEnumTest`

Boundary:

- This function enforces the grill-session rule that the monitor cannot emit free-form natural language. Any string that is not an exact match to one of the four enum values is rejected.

### `cmd_audit/baselines.py`

Issue 0009 modifies this module by adding two new helper functions, updating one dataclass, and modifying one runner function. All other functions (`run_baseline_suite`, `run_memory_baselines`, `run_evidence_recall_heuristic`, `run_subagent_judge_baseline`, `run_random_label_baseline`, `validate_monitor_payload`, `_reject_forbidden_monitor_fields`, `_select_comparison_baseline`, `_observational_label`) are unchanged from issue 0002.

#### Function: `_is_opaque_id(value: str) -> bool`

Signature:

```python
def _is_opaque_id(value: str) -> bool
```

Purpose:

- Private helper that determines whether a string qualifies as an opaque ID.
- An opaque ID is a short, whitespace-free token that carries no content-bearing separators.

Behavior:

```python
return bool(value) and " " not in value and ":" not in value and "\n" not in value and len(value) <= 128
```

Rules:

| Check | Rationale |
| --- | --- |
| `bool(value)` | Rejects empty strings. |
| `" " not in value` | Rejects strings with space-separated content text. |
| `":" not in value` | Rejects `mem_003:Berlin` patterns that embed content after a colon. |
| `"\n" not in value` | Rejects multi-line content dumps. |
| `len(value) <= 128` | Upper bound: genuine memory/event IDs are short tokens. |

Callers:

- `validate_evidence_pointers(...)`

Why it is private:

- The public contract is `validate_evidence_pointers(...)`. The `_is_opaque_id` check is an implementation detail of what constitutes "opaque."

#### Function: `validate_evidence_pointers(pointers: tuple[str, ...]) -> tuple[str, ...]`

Signature:

```python
def validate_evidence_pointers(pointers: tuple[str, ...]) -> tuple[str, ...]
```

Purpose:

- Validates that every evidence pointer in the tuple is an opaque ID.
- Returns the tuple unchanged on success, enabling use as a pass-through validator.

Behavior:

1. Iterates over every pointer in `pointers`.
2. Calls `_is_opaque_id(ptr)` for each.
3. If any pointer is not an opaque ID: raises `LeakSafeMonitorError` with the offending value.
4. If all pointers pass: returns `pointers` unchanged.

Validation rules:

| Input | Result |
| --- | --- |
| `("mem_001", "mem_002", "evt_301")` | Returns input |
| `()` | Returns `()` |
| `("mem_003: user lives in Berlin",)` | Raises `LeakSafeMonitorError` |
| `("memory item #4 contains stale data",)` | Raises `LeakSafeMonitorError` |
| `("mem_001\nevidence leaked",)` | Raises `LeakSafeMonitorError` |
| `("mem_003:Berlin",)` | Raises `LeakSafeMonitorError` |

Callers:

- `SubagentJudgeMonitorDecision.__post_init__()`
- Direct tests in `MonitorEvidencePointerTest`

Domain meaning:

- The monitor can point to which memory items triggered the anomaly (by ID), but must not embed content text in those pointers. This enforces the grill-session rule that evidence pointers are opaque IDs only, never content text.

#### Dataclass: `SubagentJudgeMonitorDecision` (updated)

Pre-0009 fields:

```python
should_trigger_replay: bool
risk_score: float
reasons: tuple[str, ...]
trace_summary: str
cost_per_decision: float = 0.2
```

Post-0009 fields:

```python
should_trigger_replay: bool
risk_score: float
anomaly_reason: str
evidence_pointers: tuple[str, ...]
trace_summary: str
cost_per_decision: float = 0.2
```

Field-level changes:

| Old Field | New Field | Change |
| --- | --- | --- |
| `reasons: tuple[str, ...]` | `anomaly_reason: str` | Free-form tuple → single enum-locked string |
| _(none)_ | `evidence_pointers: tuple[str, ...]` | New field for opaque memory/event IDs |

Domain meaning:

- `anomaly_reason`: a single enum value from `MONITOR_ANOMALY_REASON_VALUES` describing the primary anomaly signal. Replaces the free-form `reasons` tuple.
- `evidence_pointers`: opaque IDs of memory items that are relevant to the anomaly (typically `baseline.retrieved_memory_ids`). Must not contain content text.

#### `SubagentJudgeMonitorDecision.__post_init__()` (updated)

Signature:

```python
def __post_init__(self) -> None
```

Behavior:

1. Calls `validate_monitor_anomaly_reason(self.anomaly_reason)`.
2. Calls `validate_evidence_pointers(self.evidence_pointers)`.

Pre-0009 behavior: no `__post_init__` existed. Validation only happened in `to_payload()`.

Why validation moved to construction time:

- Catching invalid enum values and content-bearing pointers at construction time (rather than at serialization time) makes the error proximal to the source. A `SubagentJudgeMonitorDecision` with an invalid `anomaly_reason` is rejected immediately, before any caller can inspect it.

#### `SubagentJudgeMonitorDecision.to_payload()` (updated)

Signature:

```python
def to_payload(self) -> dict[str, Any]
```

Behavior (unchanged flow, updated payload shape):

1. Builds a dict with keys:
   - `should_trigger_replay`
   - `risk_score`
   - `anomaly_reason` (was: `reasons`)
   - `evidence_pointers` (NEW)
   - `trace_summary`
   - `cost_per_decision`
2. Calls `validate_monitor_payload(payload)`.
3. Returns the validated payload.

Pre-0009 payload:

```python
{
    "should_trigger_replay": True,
    "risk_score": 0.9,
    "reasons": ["baseline answer score is below success threshold"],
    "trace_summary": "...",
    "cost_per_decision": 0.2,
}
```

Post-0009 payload:

```python
{
    "should_trigger_replay": True,
    "risk_score": 0.9,
    "anomaly_reason": "evidence_recall_low",
    "evidence_pointers": ["mem_001"],
    "trace_summary": "...",
    "cost_per_decision": 0.2,
}
```

Callers:

- `run_subagent_judge_monitor(...)` calls `decision.to_payload()` before returning.
- Tests call it directly to verify payload shape.

#### Function: `run_subagent_judge_monitor(case, baseline, *, trigger_threshold=0.5)` (updated)

Signature (unchanged):

```python
def run_subagent_judge_monitor(
    case: ProbeCase,
    baseline: BaselineOutput | None = None,
    *,
    trigger_threshold: float = 0.5,
) -> SubagentJudgeMonitorDecision
```

Inputs:

- `case`: one validated `ProbeCase`.
- `baseline`: optional baseline output (defaults to `vector_memory`).
- `trigger_threshold`: risk_score threshold for `should_trigger_replay`.

Risk score computation (unchanged from issue 0002):

```python
risk_score = 0.0
if baseline.answer_score < 1.0:
    risk_score += 0.5
if baseline.evidence_score < 1.0:
    risk_score += 0.4
if not baseline.retrieved_memory_ids:
    risk_score += 0.1
risk_score = min(risk_score, 1.0)
```

Anomaly reason selection (NEW — replaces free-form `reasons` list):

```python
if not baseline.retrieved_memory_ids:
    anomaly_reason = "retrieved_context_incomplete"
elif baseline.evidence_score < 1.0:
    anomaly_reason = "evidence_recall_low"
elif baseline.answer_score < 1.0:
    anomaly_reason = "answer_vs_evidence_mismatch"
else:
    anomaly_reason = "confidence_anomaly"
```

Priority logic:

| Priority | Condition | Enum Value |
| --- | --- | --- |
| 1 (highest) | No memory items retrieved | `retrieved_context_incomplete` |
| 2 | Evidence recall below threshold | `evidence_recall_low` |
| 3 | Answer score below threshold (but evidence OK) | `answer_vs_evidence_mismatch` |
| 4 (fallback) | Risk score anomalous but no dominant signal | `confidence_anomaly` |

Evidence pointers (NEW):

```python
evidence_pointers = tuple(baseline.retrieved_memory_ids)
```

The monitor returns the baseline's retrieved memory IDs as opaque pointers. These are memory_id strings from the probe case contract (e.g., `"mem_001"`, `"mem_302"`), which are already short tokens with no embedded content. The `validate_evidence_pointers` call in `__post_init__` provides a defense-in-depth check.

Decision construction:

```python
decision = SubagentJudgeMonitorDecision(
    should_trigger_replay=risk_score >= trigger_threshold,
    risk_score=risk_score,
    anomaly_reason=anomaly_reason,
    evidence_pointers=evidence_pointers,
    trace_summary=(
        f"{baseline.baseline_name}: answer_score={baseline.answer_score:.3f}; "
        f"evidence_score={baseline.evidence_score:.3f}; "
        f"retrieved_count={len(baseline.retrieved_memory_ids)}"
    ),
)
decision.to_payload()
return decision
```

Callers:

- `run_baseline_suite(...)`
- Exported from `cmd_audit.__init__`

## Test-Level Contract

### `tests/test_cmd_audit_issue9_monitor_contract.py`

This file is the behavior-level specification for issue 0009. It contains 4 test classes and 15 test methods.

#### `MonitorAnomalyReasonEnumTest`

| Test | What It Verifies |
| --- | --- |
| `test_all_four_enum_values_accepted` | Each of the four `MONITOR_ANOMALY_REASON_VALUES` is accepted by `validate_monitor_anomaly_reason()`. Uses `subTest` for all four values. |
| `test_free_form_natural_language_rejected` | Four examples of natural-language reasons ("the answer looks wrong compared to stored facts", "baseline evidence score is below success threshold", "possible retrieval failure detected", "suspicious context injection") are rejected with `MonitorAnomalyReasonError`. |
| `test_misspelled_enum_value_rejected` | Five near-miss values (misspelling, trailing space, wrong case, empty string) are rejected. |
| `test_none_or_empty_rejected` | Empty string is rejected with `MonitorAnomalyReasonError`. |
| `test_decision_construction_rejects_bad_reason` | Constructing a `SubagentJudgeMonitorDecision` with an invalid `anomaly_reason` raises `MonitorAnomalyReasonError` at `__post_init__` time. |
| `test_decision_construction_accepts_valid_reason` | Constructing a `SubagentJudgeMonitorDecision` with a valid `anomaly_reason` succeeds and preserves the value. |

#### `MonitorEvidencePointerTest`

| Test | What It Verifies |
| --- | --- |
| `test_opaque_ids_accepted` | Valid opaque IDs (`"mem_001"`, `"mem_002"`, `"evt_301"`) are accepted. |
| `test_empty_pointers_accepted` | Empty tuple is accepted (no pointers to validate). |
| `test_content_bearing_pointers_rejected` | Three examples of content-bearing pointers (colon-separated content, descriptive text, newline injection) are rejected with `LeakSafeMonitorError`. Uses `subTest`. |
| `test_pointer_with_colon_rejected` | `"mem_003:Berlin"` is rejected (colon is a content-bearing separator). |
| `test_decision_construction_rejects_bad_pointer` | Constructing a `SubagentJudgeMonitorDecision` with a content-bearing evidence pointer raises `LeakSafeMonitorError` at `__post_init__` time. |

#### `MonitorForbiddenFieldsTest`

| Test | What It Verifies |
| --- | --- |
| `test_forbidden_field_names_rejected_in_payload` | All 22 keys in `FORBIDDEN_MONITOR_FIELDS` are rejected when present in a monitor payload. Uses `subTest` for all 22 keys. This re-validates the issue 0002 contract within the issue 0009 context. |
| `test_clean_payload_with_anomaly_reason_accepted` | A payload with the new `anomaly_reason` and `evidence_pointers` fields (but no forbidden fields) is accepted by `validate_monitor_payload`. |
| `test_payload_with_forbidden_field_nested_rejected` | Forbidden fields in nested dicts are rejected (tests recursive `_reject_forbidden_monitor_fields`). |

#### `MonitorEndToEndContractTest`

| Test | What It Verifies |
| --- | --- |
| `test_monitor_payload_exposes_anomaly_reason_and_pointers` | End-to-end: loads a real probe case from `v0_issue3_cases.json`, runs `run_baseline_suite`, and checks that the monitor payload contains a valid `anomaly_reason` (in the enum) and `evidence_pointers` (all opaque IDs with no colons, no spaces). |

## Artifact Contract

Issue 0009 produces no new output artifacts. It hardens the internal monitor contract of the existing `BaselineSuiteResult.monitor` path.

The monitor payload shape in `BaselineSuiteResult.monitor.to_payload()` is the contract change. Existing consumers of the payload (tests, harness) are updated to reference `anomaly_reason` instead of `reasons`.

## `cmd_audit/__init__.py` Exports

New public exports for issue 0009:

| Export | Source Module |
| --- | --- |
| `MONITOR_ANOMALY_REASON_VALUES` | `cmd_audit.labels` |
| `MonitorAnomalyReasonError` | `cmd_audit.labels` |
| `SubagentJudgeMonitorDecision` | `cmd_audit.baselines` |
| `validate_evidence_pointers` | `cmd_audit.baselines` |
| `validate_monitor_anomaly_reason` | `cmd_audit.labels` |
| `validate_monitor_payload` | `cmd_audit.baselines` |

Pre-existing exports preserved from issue 0002: `BaselineSuiteResult`, `run_baseline_suite`, `validate_v0_label`, `V0_PIPELINE_LABEL_ORDER`, `V0_PIPELINE_LABELS`.

## Boundary Rules

- Monitor `anomaly_reason` accepts exactly four enum values. Any other string is rejected at construction time.
- Monitor `evidence_pointers` accept only opaque IDs (no spaces, no colons, no newlines, ≤128 chars, non-empty). Content-bearing strings are rejected at construction time.
- Forbidden field blocklist (`FORBIDDEN_MONITOR_FIELDS`) remains enforced at `to_payload()` time (unchanged from issue 0002).
- The monitor still only triggers replay. It does not emit final labels, ECS, memory writes, gold answers, or full failed traces.
- The monitor's `trace_summary` remains a single aggregate string for debugging. It is not validated for content because it is an internal trace, not a user-facing output.
- `ingestion_error` is registered in `DEFERRED_PIPELINE_LABELS` (labels.py). It is rejected by `validate_v0_label` but is not a monitor concern — the monitor only uses pipeline labels indirectly through the comparator layer.

## Verification

Commands:

```bash
python3 -m pytest tests/test_cmd_audit_issue9_monitor_contract.py -v
python3 -m pytest                                  # full suite
python3 -m cmd_audit run                           # artifact generation
```

Verified state:

```text
31 tests passed (16 pre-existing + 15 issue 0009)
wrote 6 attribution row(s) to artifacts/attribution_table.csv
with comparison metrics to artifacts/comparison_metrics.csv
and confusion matrix to artifacts/attribution_confusion_matrix.csv
```

## Remaining Work After Issue 0009

Issue 0009 is green. The next slices in dependency order:

- Issue 0005: Post-Repair Context Replay with three-value `repair_assessment`.
- Issue 0006: Targeted memory fixes.
- Issue 0007: ECS Failure Memory recurrence (enforces ECS cause item-label-name prohibition).
- Issue 0010: Evidence-driven version gates (HITL, blocked by 0004/0005/0007).
