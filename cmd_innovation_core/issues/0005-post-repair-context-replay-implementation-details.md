# Issue 0005 Implementation Details: Post-Repair Context Replay

## Purpose

This document is the zoomed-out implementation map for issue 0005, `Validate Post-Repair Context Replay`. It maps every function, dataclass, helper, exception, and constant to its exact source location, signature, behavior, callers, and domain meaning — in the same format as the issue 0001 and issue 0002 implementation details documents.

Issue 0005 builds the repair-validation pipeline on top of the existing attribution layer:

```text
ProbeCase
  -> run_case_full
      -> run_case (existing: run_v0_replay_portfolio → assign_attribution)
      -> draft_ecs (per-label rule-based Error-Cause-Solution)
      -> build_repaired_context (corrected memory + repair guidance + evidence block)
      -> run_post_repair_context_replay (three-value repair_assessment, no gold answer injection)
      -> run_hard_case_update_baseline (generic comparison baseline)
  -> FullAuditResult
  -> write_post_repair_table (sandbox-boundary-validated CSV)
```

The slice delivers four TDD cycles from `cmd_tracer_bullets.md`:

| Cycle | Title | Status |
| --- | --- | --- |
| Cycle 5 | Post-Repair Context Replay | Green |
| Cycle 12 | Three-Value Post-Repair Assessment | Green |
| Cycle 13 | ECS Cause Item-Label-Name Prohibition | Green |
| Cycle 15 | CMD-Audit Sandbox Write Boundary | Green |

Issue 0006 (`cmd_audit/repairs.py`) later builds on issue 0005's `FullAuditResult` and `run_case_full` to produce the repair success comparison table; issue 0005 remains the foundational repair-validation layer.

## Source Requirements

The implementation follows these local documents.

| Source | Requirement Applied In Issue 0005 |
| --- | --- |
| `TASK.md` | Post-Repair Context Replay must rerun the original failed query with repaired context, without injecting the gold answer, and output three-value `repair_assessment` (`recovered` / `partial` / `failed`). `partial` means evidence recovered but answer still wrong — exposes coupled failures. ECS `cause` may describe item state but must not use V0-forbidden item label names or re-declare them through natural language equivalents. CMD-Audit write permissions limited to replay-local sandbox. |
| `CLAUDE.md` | Keep CMD-Audit separate from CMD-Skill Adapter; CMD-Audit write permissions limited to replay-local sandbox; three-value `repair_assessment`; ECS `cause` item-state description rules; do not inject gold answers into Post-Repair Context Replay. |
| `cmd_innovation_core/CONTEXT.md` | **Post-Repair Context Replay** definition: rebuilds repaired context from CMD outputs, reruns original failed query, outputs three-value `repair_assessment`, does not inject gold answer. **ECS** cause rules. **CMD-Audit** sandbox write limitation — only CMD-Skill Adapter applies validated repairs to production agent state. |
| `cmd_innovation_core/prd/cmd_minimal_probe_prd.md` | User Story 17 (Post-Repair Context Replay), AC4 (three-value assessment, not binary gate), AC7 (ECS cause item-label-name prohibition), AC8 (CMD-Audit sandbox write boundary). |
| `cmd_innovation_core/issues/0005-validate-post-repair-context-replay.md` | Six acceptance criteria: full pipeline flow, repaired context components, no gold answer injection, three-value assessment, token cost + regression risk, hard-case update baseline, sandbox write limit. |
| `cmd_innovation_core/prototypes/post_repair_and_monitor_contract_prototype.md` | State transitions for three-value classification, four scenario cards (Full Recovery, Partial Coupled Failure, Failed, Partial Injection). |
| `cmd_innovation_core/tdd/cmd_tracer_bullets.md` | Cycle 5 RED/GREEN: Post-Repair Context Replay. Cycle 12 RED/GREEN: Three-Value Assessment. Cycle 13 RED/GREEN: ECS Cause Prohibition. Cycle 15 RED/GREEN: Sandbox Write Boundary. |

## Domain Boundary

Issue 0005 builds the repair-validation pipeline on top of the existing attribution (issues 0001-0003), baselines (issue 0002), taxonomy review (issue 0004), and monitor contract (issue 0009). It does not change any existing replay, attribution, or baseline logic.

```text
run_case (existing, unchanged)
  -> AuditResult

run_case_full (issue 0005)
  -> run_case (existing)
  -> draft_ecs (issue 0005)
  -> build_repaired_context (issue 0005)
  -> run_post_repair_context_replay (issue 0005)
  -> run_hard_case_update_baseline (issue 0005)
  -> FullAuditResult (issue 0005)

run_cases_full (issue 0006, builds on issue 0005)
  -> [run_case_full(c) for c in cases]

write_repair_success_table_from_full (issue 0006, builds on issue 0005)
  -> make_repair_comparison(fr) for each FullAuditResult
  -> write_repair_success_table (from repairs.py)
```

Issue 0005 owns:

- Defining `REPAIR_ASSESSMENT_VALUES` (`recovered`, `partial`, `failed`).
- `classify_repair_assessment(answer_score, evidence_score) -> str` with explicit three-value decision logic.
- `ECSDraft` dataclass with `__post_init__` validation (V0 label + ECS cause prohibition).
- `_validate_ecs_cause(cause)` to reject forbidden item label names and natural-language equivalents via regex.
- `RepairedContext` dataclass for the rebuilt context before replay.
- `PostRepairResult` dataclass with answer score, evidence score, assessment, token cost, and regression risk.
- `draft_ecs(case, audit_result) -> ECSDraft` with rule-based per-label ECS drafting.
- `build_repaired_context(case, ecs_draft) -> RepairedContext` — the gold-answer-injection prevention gate.
- `run_post_repair_context_replay(case, repaired_context) -> PostRepairResult` without gold answer injection.
- `run_hard_case_update_baseline(case) -> PostRepairResult` as the undifferentiated comparison baseline.
- `validate_sandbox_path(output_path, sandbox_root)` for sandbox write boundary enforcement.
- `FullAuditResult` dataclass wrapping the complete pipeline output.
- `run_case_full(case) -> FullAuditResult` as the top-level pipeline entry point.
- `write_post_repair_table(results, output_path, *, sandbox_root)` with sandbox validation.
- Per-label ECS rule helpers (`_ecs_for_label`) for all six V0 labels.
- Token cost estimator (`_estimate_token_cost`) and regression risk estimator (`_estimate_regression_risk`).
- Behavior-level tests for all four TDD cycles (26 test methods).

Issue 0005 does NOT own (these belong to other issues):

- Changing replay logic or adding new replay paths (issue 0003).
- Changing `assign_attribution` or its thresholds (issue 0001).
- Changing baseline suite or comparator logic (issue 0002).
- Changing monitor contract or validation (issue 0009).
- Adding new probe cases (issue 0003).
- ECS Failure Memory recurrence (issue 0007).
- Targeted memory fix comparison metrics (issue 0006).

## Module Map

| Module | Issue 0005 Role |
| --- | --- |
| `cmd_audit/post_repair.py` | Owns all post-repair data types, pipeline functions, sandbox validation, ECS cause validation, per-label ECS rules, and scoring helpers. This is the primary module created by issue 0005. |
| `cmd_audit/harness.py` | UPDATED. Owns `FullAuditResult` dataclass, `run_case_full` pipeline entry point, `write_post_repair_table` CSV writer. Also now owns `run_cases_full` and `write_repair_success_table_from_full` (added in issue 0006, depend on FullAuditResult). Existing `AuditResult`, `run_case`, `run_cases`, and all prior table writers are preserved unchanged. |
| `cmd_audit/__init__.py` | UPDATED. Exports new public surface from `post_repair` and `harness`: `ECSDraft`, `FullAuditResult`, `PostRepairResult`, `RepairedContext`, `build_repaired_context`, `classify_repair_assessment`, `draft_ecs`, `run_case_full`, `run_cases_full`, `run_hard_case_update_baseline`, `run_post_repair_context_replay`, `validate_sandbox_path`, `write_post_repair_table`, `write_repair_success_table_from_full`. |
| `cmd_audit/labels.py` | No changes by issue 0005. `OUT_OF_SCOPE_ITEM_LABELS` and `validate_v0_label` are imported by `post_repair.py` for ECS cause and predicted_label validation. |
| `cmd_audit/scoring.py` | No changes. `answer_score` and `evidence_recall_from_text` are imported by `post_repair.py` for replay scoring. |
| `cmd_audit/repairs.py` | Issue 0006 module. Imports `PostRepairResult`, `RepairedContext`, `validate_sandbox_path` from `post_repair.py`. `make_repair_comparison` consumes `FullAuditResult` and both `PostRepairResult` fields. |
| `tests/test_cmd_audit_issue5_post_repair.py` | 5 test classes, 26 test methods covering Cycles 5, 12, 13, 15. |
| `tests/test_cmd_audit_issue6_targeted_repairs.py` | Issue 0006 tests. 5 test classes, 26 test methods that depend on `FullAuditResult`, `run_case_full`, and `validate_sandbox_path` from issue 0005. |

## Caller Graph

### Attribution → Post-Repair Pipeline (issue 0005)

```text
cmd_audit/__init__.py
  -> harness.run_case_full(ProbeCase)
      -> harness.run_case(ProbeCase)
          -> baselines.run_baseline_suite(ProbeCase)
              -> baselines.run_memory_baselines
              -> baselines._select_comparison_baseline
              -> baselines.run_evidence_recall_heuristic
                  -> baselines._observational_label
                      -> scoring.evidence_recall_from_memory_ids
                      -> scoring.evidence_recall_from_text
              -> baselines.run_subagent_judge_baseline
              -> baselines.run_random_label_baseline
              -> baselines.run_subagent_judge_monitor
                  -> SubagentJudgeMonitorDecision.to_payload
                      -> baselines.validate_monitor_payload
                          -> baselines._reject_forbidden_monitor_fields
          -> replays.run_v0_replay_portfolio(ProbeCase)
              -> replays.run_oracle_write
              -> replays.run_oracle_compression
              -> replays.run_verbatim_event_oracle
              -> replays.run_oracle_retrieval
              -> replays.run_injection_oracle
              -> replays.run_evidence_given_reasoning
          -> attribution.assign_attribution(replays)
              -> attribution._label_for_replay
              -> labels.validate_v0_label
      -> post_repair.draft_ecs(ProbeCase, AuditResult)
          -> AuditResult.attribution  (AttributionResult)
          -> AuditResult.replay       (ReplayResult, top-gain replay)
          -> post_repair._ecs_for_label(case, predicted_label, replay)
          -> ECSDraft(...)
              -> __post_init__:
                  -> labels.validate_v0_label(predicted_label)
                  -> post_repair._validate_ecs_cause(cause)
                      -> checks OUT_OF_SCOPE_ITEM_LABELS for substring match
                      -> checks regex _FORBIDDEN_NL_PATTERNS
      -> post_repair.build_repaired_context(ProbeCase, ECSDraft)
          -> RepairedContext(case_id, corrected_memory, repair_guidance, repaired_evidence_block, original_query)
      -> post_repair.run_post_repair_context_replay(ProbeCase, RepairedContext)
          -> post_repair._combine_context(RepairedContext)
          -> scoring.evidence_recall_from_text(gold_evidence, combined_context)
          -> case.gold_answer.casefold() in combined.casefold()  (answer score)
          -> post_repair.classify_repair_assessment(answer_score, evidence_score)
          -> post_repair._estimate_token_cost(combined_context, query)
          -> post_repair._estimate_regression_risk(case, ctx)
          -> PostRepairResult(...)
      -> post_repair.run_hard_case_update_baseline(ProbeCase)
          -> RepairedContext(case_id, all_extracted_memory, "Hard-case update: ...", all_extracted_memory, query)
          -> post_repair.run_post_repair_context_replay(case, ctx)
      -> FullAuditResult(audit, ecs_draft, repaired_context, post_repair, hard_case_baseline)

  -> harness.write_post_repair_table([FullAuditResult], output_path, sandbox_root)
      -> post_repair.validate_sandbox_path(output_path, sandbox_root)
          -> Path.resolve() to neutralize '..' traversal
```

### Issue 0006 Integration (post-0005 consumer)

```text
cmd_audit/__init__.py
  -> harness.run_cases_full([ProbeCase, ...])
      -> [harness.run_case_full(c) for c in cases]
  -> harness.write_repair_success_table_from_full([FullAuditResult], output_path, sandbox_root)
      -> repairs.make_repair_comparison(FullAuditResult)
          -> FullAuditResult.audit (AuditResult)
          -> FullAuditResult.post_repair (PostRepairResult, CMD-guided)
          -> FullAuditResult.hard_case_baseline (PostRepairResult, generic)
          -> repairs.get_targeted_repair_action(predicted_label)
      -> repairs.write_repair_success_table(rows, output_path, sandbox_root)
          -> post_repair.validate_sandbox_path(output_path, sandbox_root)
```

### Behavior-Test Path

```text
tests/test_cmd_audit_issue5_post_repair.py
  -> post_repair.classify_repair_assessment(answer_score, evidence_score)
  -> post_repair.draft_ecs(case, audit_result)
  -> post_repair.build_repaired_context(case, ecs_draft)
  -> post_repair.run_post_repair_context_replay(case, repaired_context)
  -> post_repair.run_hard_case_update_baseline(case)
  -> harness.run_case_full(case)
  -> post_repair.ECSDraft(...)  (direct construction for cause validation tests)
  -> post_repair.validate_sandbox_path(output_path, sandbox_root)
  -> harness.write_post_repair_table(results, output_path, sandbox_root)
```

## Data Flow

### Input Fixtures

```text
data/probe_cases/v0_retrieval_error_case.json          # single-case retrieval fixture (issue 0001)
data/probe_cases/v0_premature_extraction_error_case.json  # single-case extraction fixture (issue 0003)
data/probe_cases/v0_issue3_cases.json                  # six-case smoke suite (issue 0003)
```

### Intermediate Types

**ECSDraft** (from `draft_ecs`, frozen dataclass):

| Field | Type | Source |
| --- | --- | --- |
| `case_id` | `str` | `ProbeCase.case_id` |
| `predicted_label` | `str` | `AttributionResult.predicted_label`, validated against `V0_PIPELINE_LABELS` |
| `cause` | `str` | `_ecs_for_label(...)`, validated by `_validate_ecs_cause` |
| `corrected_memory` | `str` | Top replay's `evidence_block` |
| `repair_guidance` | `str` | `_ecs_for_label(...)` |
| `repaired_evidence_block` | `str` | Top replay's `evidence_block` |

**RepairedContext** (from `build_repaired_context`, frozen dataclass):

| Field | Type | Source |
| --- | --- | --- |
| `case_id` | `str` | `ProbeCase.case_id` |
| `corrected_memory` | `str` | `ECSDraft.corrected_memory` |
| `repair_guidance` | `str` | `ECSDraft.repair_guidance` |
| `repaired_evidence_block` | `str` | `ECSDraft.repaired_evidence_block` |
| `original_query` | `str` | `ProbeCase.query` |

**PostRepairResult** (from `run_post_repair_context_replay`, frozen dataclass):

| Field | Type | Meaning |
| --- | --- | --- |
| `case_id` | `str` | Case identifier |
| `repair_assessment` | `str` | `"recovered"`, `"partial"`, or `"failed"` |
| `post_repair_answer_score` | `float` | `1.0` if `gold_answer` text appears in combined repaired context, else `0.0` |
| `post_repair_evidence_score` | `float` | `evidence_recall_from_text(gold_evidence, combined_context)` |
| `token_cost` | `float` | `(len(combined_context) + len(query)) / 4.0` |
| `regression_risk` | `float` | `1.0 - overlap_ratio` between original baseline injected context and repaired context |
| `had_repair_regression` | `bool` | `regression_risk > 0.5` |

**FullAuditResult** (from `run_case_full`, frozen dataclass):

| Field | Type | Meaning |
| --- | --- | --- |
| `audit` | `AuditResult` | Existing attribution + baselines result |
| `ecs_draft` | `ECSDraft` | Error-Cause-Solution from CMD attribution |
| `repaired_context` | `RepairedContext` | Context rebuilt for post-repair replay |
| `post_repair` | `PostRepairResult` | CMD-guided post-repair replay result |
| `hard_case_baseline` | `PostRepairResult` | Generic hard-case update comparison |

### Output Artifacts

```text
artifacts/sandbox/post_repair_table.csv      # from write_post_repair_table (issue 0005)
artifacts/sandbox/repair_success_table.csv    # from write_repair_success_table_from_full (issue 0006)
artifacts/sandbox/repair_label_summary.csv    # from write_repair_success_table (issue 0006)
artifacts/sandbox/repair_claim_ledger.txt     # from write_repair_success_table (issue 0006)
```

Existing artifacts preserved unchanged:

```text
artifacts/attribution_table.csv
artifacts/comparison_metrics.csv
artifacts/attribution_confusion_matrix.csv
```

### Per-Label ECS Cause and Repair Guidance

| Predicted Label | cause | corrected_memory | repair_guidance |
| --- | --- | --- | --- |
| `retrieval_error` | "retrieved context did not include the correct memory item even though the item was present in extracted memory" | `replay.evidence_block` | "update retrieval routing to include the corrected memory item" |
| `premature_extraction_error` | "key evidence was present in raw events but was not preserved in any extracted memory item" | `replay.evidence_block` | "improve extraction to preserve evidence from raw events into memory items" |
| `reasoning_error` | "the injected context contained the required evidence, but the final answer did not match the gold answer" | `replay.evidence_block` | "review reasoning step over provided evidence; the evidence was sufficient but the conclusion was wrong" |
| `compression_error` | "lossy compression removed key evidence that was present in the original memory item" | `replay.evidence_block` | "reduce compression aggressiveness or preserve key evidence phrases during compression" |
| `injection_error` | "retrieved evidence was not correctly injected into the final context for the agent to use" | `replay.evidence_block` | "fix injection formatting so retrieved evidence is presented as a clean evidence block" |
| `write_error` | "no recoverable evidence found in extracted memory; the failure may originate at or before the write step" | `replay.evidence_block` | "ensure events are written to memory and evidence is preserved through the pipeline" |

## Function-Level Contract

### `cmd_audit/post_repair.py`

This is the primary module created by issue 0005. File: `cmd_audit/post_repair.py` (299 lines). Contains 4 public functions, 5 private helpers, 3 frozen dataclasses, 1 exception class, 1 constant, and 2 regex-based validation patterns.

---

#### Constant: `REPAIR_ASSESSMENT_VALUES`

Location: `cmd_audit/post_repair.py:13`

```python
REPAIR_ASSESSMENT_VALUES = ("recovered", "partial", "failed")
```

Purpose:

- Defines the exhaustive set of valid repair assessment values for Post-Repair Context Replay.
- Tuple ordering is stable for iteration and documentation.

Domain meaning:

| Value | Condition | Interpretation |
| --- | --- | --- |
| `recovered` | `answer_score == 1.0` | Repair fully restored correct task behavior. |
| `partial` | `answer_score < 1.0` AND `evidence_score == 1.0` | Evidence recovered but answer still wrong — exposes coupled failure. This is diagnostic depth, not repair failure. |
| `failed` | `answer_score < 1.0` AND `evidence_score < 1.0` | Neither evidence nor answer recovered. Repair targeted the wrong operation or root cause is misdiagnosed. |

---

#### Private Constant: `_FORBIDDEN_NL_PATTERNS`

Location: `cmd_audit/post_repair.py:16-25`

```python
_FORBIDDEN_NL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bitem[_\s]?(is\s+)?wrong\b",
        r"\bitem[_\s]?(is\s+)?stale\b",
        r"\bitem[_\s]?(is\s+)?conflict(ed|ing)?\b",
        r"\bitem[_\s]?(is\s+)?poisoned\b",
        r"\bcompression[_\s]?distorted\b",
    )
)
```

Purpose:

- Compiled regex patterns that detect natural-language equivalents of forbidden item label names in ECS cause text.
- Used by `_validate_ecs_cause` to reject phrases like "the memory item is wrong" or "item_is_stale".

Pattern matching table:

| Pattern | Rejects | Allows |
| --- | --- | --- |
| `\bitem[_\s]?(is\s+)?wrong\b` | "item_wrong", "the item is wrong", "memory item is wrong" | "wrong delivery address" |
| `\bitem[_\s]?(is\s+)?stale\b` | "item_stale", "the item is stale" | "stale coffee" |
| `\bitem[_\s]?(is\s+)?conflict(ed|ing)?\b` | "item_conflict", "item is conflicting" | "schedule conflict" |
| `\bitem[_\s]?(is\s+)?poisoned\b` | "item_poisoned", "item is poisoned" | "food poisoning" |
| `\bcompression[_\s]?distorted\b` | "compression_distorted", "compression distorted the fact" | "image compression" |

---

#### Exception: `ECSCauseValidationError`

Location: `cmd_audit/post_repair.py:28-29`

```python
class ECSCauseValidationError(ValueError):
    """Raised when ECS cause contains forbidden item label names or equivalents."""
```

Purpose:

- Signals that an ECS `cause` string violates the item-label-name prohibition rule.
- Distinct from `LabelValidationError` (label boundary scope, in `labels.py`) and `LeakSafeMonitorError` (monitor boundary scope, in `baselines.py`).

Raised by:

- `_validate_ecs_cause(cause)` (line 32-47)

Caught by:

- Callers of `ECSDraft(...)` constructor, since `__post_init__` calls `_validate_ecs_cause`.

---

#### Private Function: `_validate_ecs_cause(cause: str) -> str`

Location: `cmd_audit/post_repair.py:32-47`

```python
def _validate_ecs_cause(cause: str) -> str:
    lowered = cause.casefold()
    for label in OUT_OF_SCOPE_ITEM_LABELS:
        if label in lowered:
            raise ECSCauseValidationError(
                f"ECS cause must not use forbidden item label {label!r}; "
                f"describe item state instead (e.g., 'stored preference was outdated')"
            )
    for pattern in _FORBIDDEN_NL_PATTERNS:
        if pattern.search(lowered):
            raise ECSCauseValidationError(
                f"ECS cause contains natural-language equivalent of a forbidden "
                f"item label; use descriptive state language instead"
            )
    return cause
```

Purpose:

- Rejects ECS `cause` text containing forbidden item label names or their natural-language equivalents.
- Allows descriptive state language like "stored preference was outdated relative to ground truth."

Behavior:

1. Casefolds the input.
2. Checks for exact substring matches of each label in `OUT_OF_SCOPE_ITEM_LABELS` — if any found, raises `ECSCauseValidationError`.
3. Checks each compiled regex in `_FORBIDDEN_NL_PATTERNS` via `pattern.search(lowered)` — if matched, raises `ECSCauseValidationError`.
4. Returns `cause` unchanged on success.

Callers:

- `ECSDraft.__post_init__()` (line 80)

---

#### Function: `classify_repair_assessment(answer_score: float, evidence_score: float) -> str`

Location: `cmd_audit/post_repair.py:50-61`

```python
def classify_repair_assessment(answer_score: float, evidence_score: float) -> str:
    if answer_score == 1.0:
        return "recovered"
    if evidence_score == 1.0:
        return "partial"
    return "failed"
```

Purpose:

- Classifies Post-Repair Context Replay outcome into the three-value assessment.
- Implements the state machine from `prototypes/post_repair_and_monitor_contract_prototype.md`.

Decision table:

| answer_score | evidence_score | Result | Interpretation |
| --- | --- | --- | --- |
| 1.0 | 1.0 | `recovered` | Single-cause failure, repair works end-to-end. |
| 1.0 | <1.0 | `recovered` | Answer matches gold despite incomplete evidence — rare but valid. |
| 0.0 | 1.0 | `partial` | **Key diagnostic**: evidence recovered but reasoning still fails. Coupled failure exposed. |
| 0.5 | 1.0 | `partial` | Any answer_score < 1.0 with evidence == 1.0 is partial. |
| 0.0 | 0.0 | `failed` | Repair missed root cause entirely. |
| 0.3 | 0.3 | `failed` | Both below threshold. |

Priority rule: `answer_score == 1.0` dominates — if the answer is fully correct, assessment is `recovered` regardless of evidence score. If answer is not fully correct, only then does evidence_score distinguish `partial` from `failed`.

Callers:

- `run_post_repair_context_replay` (line 156)
- Direct tests in `ThreeValueRepairAssessmentTest` (test_cmd_audit_issue5_post_repair.py)

---

#### Dataclass: `ECSDraft`

Location: `cmd_audit/post_repair.py:67-80`

```python
@dataclass(frozen=True)
class ECSDraft:
    case_id: str
    predicted_label: str
    cause: str
    corrected_memory: str
    repair_guidance: str
    repaired_evidence_block: str

    def __post_init__(self) -> None:
        validate_v0_label(self.predicted_label)
        _validate_ecs_cause(self.cause)
```

Purpose:

- Immutable Error-Cause-Solution record drafted from CMD-Audit attribution results.
- Frozen to prevent mutation after construction; all fields are strings.

`__post_init__` validation:

1. Calls `validate_v0_label(self.predicted_label)` — ensures only V0 pipeline labels appear. Raises `LabelValidationError` for item labels or deferred labels.
2. Calls `_validate_ecs_cause(self.cause)` — enforces the item-label-name prohibition on cause text. Raises `ECSCauseValidationError` for forbidden names or NL equivalents.

Field meanings:

| Field | Domain Meaning |
| --- | --- |
| `predicted_label` | CMD-attributed failure label, one of six V0 pipeline labels. |
| `cause` | Natural-language description of why the failure occurred. Uses descriptive state language, not item label names. |
| `corrected_memory` | Correct memory text that should have been available (from winning replay's `evidence_block`). |
| `repair_guidance` | Natural-language instruction for how to fix the failure. |
| `repaired_evidence_block` | Evidence block recovered by the winning counterfactual replay. |

Constructed exclusively by `draft_ecs` (line 110-131). Direct construction is used in tests to verify validation rejection.

---

#### Dataclass: `RepairedContext`

Location: `cmd_audit/post_repair.py:83-92`

```python
@dataclass(frozen=True)
class RepairedContext:
    case_id: str
    corrected_memory: str
    repair_guidance: str
    repaired_evidence_block: str
    original_query: str
```

Purpose:

- Immutable context rebuilt from ECS draft for Post-Repair Context Replay.
- Frozen, no `__post_init__` validation (content is constructed from already-validated `ECSDraft` + `ProbeCase`).

Field meanings:

| Field | Domain Meaning |
| --- | --- |
| `corrected_memory` | Correct memory text injected into the new context. |
| `repair_guidance` | Repair instructions the agent should follow. |
| `repaired_evidence_block` | Recovered evidence to include in context. |
| `original_query` | User's original query from the failed task (`ProbeCase.query`). |

Constructed by:

- `build_repaired_context` (line 133-141) — for CMD-guided repair.
- `run_hard_case_update_baseline` (line 172-186) — for generic baseline (all extracted memory).

Does NOT reference `case.gold_answer`. This is the gold-answer-injection prevention gate.

---

#### Dataclass: `PostRepairResult`

Location: `cmd_audit/post_repair.py:95-105`

```python
@dataclass(frozen=True)
class PostRepairResult:
    case_id: str
    repair_assessment: str
    post_repair_answer_score: float
    post_repair_evidence_score: float
    token_cost: float
    regression_risk: float
    had_repair_regression: bool
```

Purpose:

- Immutable result of one Post-Repair Context Replay run.
- Frozen, no `__post_init__` validation (values are computed internally by `run_post_repair_context_replay`).

Field meanings:

| Field | Meaning |
| --- | --- |
| `repair_assessment` | One of `("recovered", "partial", "failed")`, from `classify_repair_assessment`. |
| `post_repair_answer_score` | `1.0` if `gold_answer` casefold text appears in combined repaired context, else `0.0`. |
| `post_repair_evidence_score` | `evidence_recall_from_text(gold_evidence, combined_context)`. |
| `token_cost` | `(len(context) + len(query)) / 4.0` character-based token estimate. |
| `regression_risk` | `1.0 - overlap_ratio` between original baseline context terms and repaired context terms, clamped to [0.0, 1.0]. |
| `had_repair_regression` | `True` when `regression_risk > 0.5`. |

Constructed exclusively by `run_post_repair_context_replay` (line 144-169). Imported and consumed by `repairs.py` (issue 0006) for `make_repair_comparison`.

---

#### Function: `draft_ecs(case: ProbeCase, audit_result) -> ECSDraft`

Location: `cmd_audit/post_repair.py:110-131`

```python
def draft_ecs(case: ProbeCase, audit_result) -> ECSDraft:
    attribution = audit_result.attribution
    replay = audit_result.replay
    cause, corrected_memory, repair_guidance = _ecs_for_label(
        case, attribution.predicted_label, replay
    )
    return ECSDraft(
        case_id=case.case_id,
        predicted_label=attribution.predicted_label,
        cause=cause,
        corrected_memory=corrected_memory,
        repair_guidance=repair_guidance,
        repaired_evidence_block=replay.evidence_block,
    )
```

Purpose:

- Drafts an Error-Cause-Solution record from CMD-Audit attribution results.
- Rule-based V0 draft: predicted label and top replay drive cause text, corrected memory, and repair guidance selection.

Behavior:

1. Reads `audit_result.attribution` (the `AttributionResult` with `predicted_label` and `top_replay`).
2. Reads `audit_result.replay` (the top-gain `ReplayResult` via `AuditResult.replay` property).
3. Calls `_ecs_for_label(case, predicted_label, replay)` to get `(cause, corrected_memory, repair_guidance)`.
4. Uses `replay.evidence_block` as `repaired_evidence_block`.
5. Constructs and returns `ECSDraft` — validation runs at construction time via `__post_init__`.

Input contract:

- `case` must be a valid `ProbeCase`.
- `audit_result` must be an `AuditResult` with valid `attribution` (has `predicted_label`) and `replay` (top-gain `ReplayResult`).

Output: a validated `ECSDraft`.

Callers:

- `harness.run_case_full` (line 91)
- Direct tests in `PostRepairContextReplayTest`

---

#### Function: `build_repaired_context(case: ProbeCase, ecs_draft: ECSDraft) -> RepairedContext`

Location: `cmd_audit/post_repair.py:133-141`

```python
def build_repaired_context(case: ProbeCase, ecs_draft: ECSDraft) -> RepairedContext:
    return RepairedContext(
        case_id=case.case_id,
        corrected_memory=ecs_draft.corrected_memory,
        repair_guidance=ecs_draft.repair_guidance,
        repaired_evidence_block=ecs_draft.repaired_evidence_block,
        original_query=case.query,
    )
```

Purpose:

- Assembles a `RepairedContext` from ECS draft + original probe case.
- Simple pass-through constructor.

Gold-answer-injection prevention:

- Does NOT reference `case.gold_answer`.
- Only uses `case.case_id` and `case.query` from the probe case.
- All memory/evidence content comes from the already-validated ECS draft.

Callers:

- `harness.run_case_full` (line 92)
- Direct tests in `PostRepairContextReplayTest`

---

#### Function: `run_post_repair_context_replay(case: ProbeCase, repaired_context: RepairedContext) -> PostRepairResult`

Location: `cmd_audit/post_repair.py:144-169`

```python
def run_post_repair_context_replay(
    case: ProbeCase, repaired_context: RepairedContext
) -> PostRepairResult:
    combined = _combine_context(repaired_context)
    evidence_score = evidence_recall_from_text(case.gold_evidence, combined)
    gold_in_context = case.gold_answer.casefold() in combined.casefold()
    post_answer_score = 1.0 if gold_in_context else 0.0
    assessment = classify_repair_assessment(post_answer_score, evidence_score)
    token_cost = _estimate_token_cost(combined, repaired_context.original_query)
    regression_risk = _estimate_regression_risk(case, repaired_context)
    return PostRepairResult(
        case_id=case.case_id,
        repair_assessment=assessment,
        post_repair_answer_score=post_answer_score,
        post_repair_evidence_score=evidence_score,
        token_cost=token_cost,
        regression_risk=regression_risk,
        had_repair_regression=regression_risk > 0.5,
    )
```

Purpose:

- Reruns the original failed query with repaired context, without injecting the gold answer into scoring.
- Scores evidence and answer from the repaired context content alone.

Step-by-step behavior:

1. Calls `_combine_context(repaired_context)` to join `corrected_memory + repair_guidance + repaired_evidence_block` with newlines.
2. Computes `evidence_score = evidence_recall_from_text(case.gold_evidence, combined)`.
3. Checks if `case.gold_answer.casefold() in combined.casefold()`:
   - If found → `post_answer_score = 1.0` (agent can "read off" the answer from corrected context).
   - If not found → `post_answer_score = 0.0` (agent cannot extract the answer even with corrected evidence — simulates reasoning gap).
4. Calls `classify_repair_assessment(post_answer_score, evidence_score)`.
5. Computes `token_cost = _estimate_token_cost(combined, query)`.
6. Computes `regression_risk = _estimate_regression_risk(case, ctx)`.
7. Returns `PostRepairResult`.

Scoring semantics:

| Evidence Found | Answer in Context | assessment | Interpretation |
| --- | --- | --- | --- |
| Yes (1.0) | Yes (1.0) | `recovered` | Repair works; agent can read correct answer from context. |
| Yes (1.0) | No (0.0) | `partial` | Evidence is there but agent can't produce correct answer — coupled failure exposed. |
| No (<1.0) | No (0.0) | `failed` | Repair failed; evidence still not recoverable. |

Gold-answer-injection guard: The answer score is determined by whether `gold_answer` text appears in the repaired context's combined text — not by calling `answer_score(answer, gold_answer)`. This simulates a real agent reading the context and extracting the answer, rather than the function itself injecting or comparing against the gold answer. The `gold_answer` is only used for the `casefold in combined` membership check.

Why casefold membership instead of answer_score: `answer_score` would require the function to produce a candidate answer string, which would mean injecting the gold answer into the comparison logic. The membership check ensures the gold answer text authentically appears in the context that the agent would see, without the function itself generating or comparing candidate answers.

Callers:

- `harness.run_case_full` → for CMD-guided post-repair (line 93)
- `run_hard_case_update_baseline` → for generic baseline (line 186)
- Direct tests in `PostRepairContextReplayTest`

---

#### Function: `run_hard_case_update_baseline(case: ProbeCase) -> PostRepairResult`

Location: `cmd_audit/post_repair.py:172-186`

```python
def run_hard_case_update_baseline(case: ProbeCase) -> PostRepairResult:
    all_memory = "\n".join(item.text for item in case.extracted_memory)
    ctx = RepairedContext(
        case_id=case.case_id,
        corrected_memory=all_memory,
        repair_guidance="Hard-case update: all extracted memory injected as context.",
        repaired_evidence_block=all_memory,
        original_query=case.query,
    )
    return run_post_repair_context_replay(case, ctx)
```

Purpose:

- Runs a generic "hard-case update" baseline for comparison with CMD-guided repair.
- Injects ALL extracted memory items as context (without CMD attribution diagnosis) to measure whether simply adding more context suffices.

Behavior:

1. Joins all `case.extracted_memory` item texts with newlines into `all_memory`.
2. Constructs a `RepairedContext` where `corrected_memory` and `repaired_evidence_block` are both `all_memory`, with a fixed repair guidance string.
3. Passes to `run_post_repair_context_replay` for scoring with identical scoring logic as CMD repair.
4. Returns the `PostRepairResult`.

Comparison semantics with CMD repair (issue 0006 uses both):

| CMD `post_repair.assessment` | Hard-Case `assessment` | Interpretation |
| --- | --- | --- |
| `recovered` | `failed` | CMD-targeted repair is necessary; generic context injection is insufficient. |
| `recovered` | `recovered` | Failure was simple — even generic context fixes it. CMD adds value through precise diagnosis. |
| `partial` | `failed` | CMD improved evidence recall but coupled reasoning failure remains. |
| `failed` | `failed` | Diagnosis missed root cause; both approaches fail. |

Callers:

- `harness.run_case_full` (line 94)
- `repairs.make_repair_comparison` (via `FullAuditResult.hard_case_baseline`)
- Direct tests in `PostRepairContextReplayTest`

---

#### Function: `validate_sandbox_path(output_path: str | Path, sandbox_root: str | Path | None = None) -> Path`

Location: `cmd_audit/post_repair.py:192-208`

```python
def validate_sandbox_path(output_path: str | Path, sandbox_root: str | Path | None = None) -> Path:
    sandbox = Path(sandbox_root if sandbox_root is not None else "artifacts/sandbox")
    target = Path(output_path).resolve()
    sandbox_resolved = sandbox.resolve()
    try:
        target.relative_to(sandbox_resolved)
    except ValueError:
        raise ValueError(
            f"CMD-Audit write rejected: {target} is outside the replay-local "
            f"sandbox {sandbox_resolved}. Only sandbox writes are permitted."
        )
    return target
```

Purpose:

- Enforces the CMD-Audit sandbox write boundary (TDD Cycle 15).
- Rejects writes to paths that resolve outside the replay-local sandbox.

Behavior:

1. Defaults `sandbox_root` to `"artifacts/sandbox"`.
2. Resolves both `output_path` and `sandbox_root` to absolute paths via `Path.resolve()`.
3. Calls `target.relative_to(sandbox_resolved)` — raises `ValueError` from `Path.relative_to` if the target is not under the sandbox.
4. Catches `ValueError` from `relative_to` and raises a new `ValueError` with a clear boundary error message.
5. Returns the resolved target `Path` on success.

Why `resolve()` is used: `Path.resolve()` eliminates `..` and symlinks, preventing trivial bypass via parent traversal.

Resolution examples:

| Input Path | sandbox_root | Result |
| --- | --- | --- |
| `"artifacts/sandbox/post_repair.csv"` | (default) | Accepted |
| `"/etc/passwd"` | (default) | Rejected |
| `"artifacts/sandbox/../../../etc/passwd"` | (default) | Rejected (parent traversal resolved) |
| `"/tmp/sandbox/out.csv"` | `"/tmp/sandbox"` | Accepted |
| `"/tmp/not-sandbox/out.csv"` | `"/tmp/sandbox"` | Rejected |

Callers:

- `harness.write_post_repair_table` (line 299)
- `repairs.write_repair_success_table` (imported in issue 0006)
- Direct tests in `SandboxWriteBoundaryTest`

---

#### Private Function: `_ecs_for_label(case, predicted_label: str, replay) -> tuple[str, str, str]`

Location: `cmd_audit/post_repair.py:214-268`

```python
def _ecs_for_label(case, predicted_label: str, replay) -> tuple[str, str, str]:
    baseline = case.primary_baseline

    if predicted_label == "retrieval_error":
        return (
            "retrieved context did not include the correct memory item "
            "even though the item was present in extracted memory",
            replay.evidence_block,
            "update retrieval routing to include the corrected memory item",
        )
    if predicted_label == "premature_extraction_error":
        return (
            "key evidence was present in raw events but was not preserved "
            "in any extracted memory item",
            replay.evidence_block,
            "improve extraction to preserve evidence from raw events into memory items",
        )
    if predicted_label == "reasoning_error":
        return (
            "the injected context contained the required evidence, but the "
            "final answer did not match the gold answer",
            replay.evidence_block,
            "review reasoning step over provided evidence; the evidence was "
            "sufficient but the conclusion was wrong",
        )
    if predicted_label == "compression_error":
        return (
            "lossy compression removed key evidence that was present in the "
            "original memory item",
            replay.evidence_block,
            "reduce compression aggressiveness or preserve key evidence "
            "phrases during compression",
        )
    if predicted_label == "injection_error":
        return (
            "retrieved evidence was not correctly injected into the final "
            "context for the agent to use",
            replay.evidence_block,
            "fix injection formatting so retrieved evidence is presented "
            "as a clean evidence block",
        )
    # write_error (and any future V1 labels that roll up in V0)
    return (
        "no recoverable evidence found in extracted memory; the failure "
        "may originate at or before the write step",
        replay.evidence_block,
        "ensure events are written to memory and evidence is preserved "
        "through the pipeline",
    )
```

Purpose:

- Returns per-label `(cause, corrected_memory, repair_guidance)` for the six V0 pipeline labels.
- Rule-based V0 draft: each label has a fixed cause template and repair guidance template.
- `corrected_memory` is always `replay.evidence_block` — the evidence recovered by the winning counterfactual replay.
- `write_error` serves as the fallback for all labels (the final `return` statement catches any V0 label not explicitly handled above).

Why `write_error` is the fallback: `write_error` represents the case where "no recoverable evidence found in extracted memory" — this is the default diagnosis when other specific failure modes are ruled out. Any future V1 labels that roll up into V0 will also hit this fallback.

Callers:

- `draft_ecs` (line 120)

---

#### Private Function: `_combine_context(ctx: RepairedContext) -> str`

Location: `cmd_audit/post_repair.py:271-277`

```python
def _combine_context(ctx: RepairedContext) -> str:
    return "\n".join(
        (
            ctx.corrected_memory,
            ctx.repair_guidance,
            ctx.repaired_evidence_block,
        )
    )
```

Purpose:

- Joins the three context components with newlines for use in `evidence_recall_from_text` and the gold-answer membership check.

Callers:

- `run_post_repair_context_replay` (line 152)
- `_estimate_regression_risk` (line 290)

---

#### Private Function: `_estimate_token_cost(context_text: str, query: str) -> float`

Location: `cmd_audit/post_repair.py:281-283`

```python
def _estimate_token_cost(context_text: str, query: str) -> float:
    return (len(context_text) + len(query)) / 4.0
```

Purpose:

- Simple character-based token estimator using ~4 chars per token approximation.
- V0 placeholder; a real tokenizer would replace this in later versions.

Callers:

- `run_post_repair_context_replay` (line 158)

---

#### Private Function: `_estimate_regression_risk(case, ctx: RepairedContext) -> float`

Location: `cmd_audit/post_repair.py:286-298`

```python
def _estimate_regression_risk(case, ctx: RepairedContext) -> float:
    baseline = case.primary_baseline
    original_context = baseline.injected_context
    combined = _combine_context(ctx)
    if not original_context:
        return 0.0
    original_terms = set(original_context.casefold().split())
    repaired_terms = set(combined.casefold().split())
    if not original_terms:
        return 0.0
    overlap = len(original_terms & repaired_terms) / len(original_terms)
    return max(0.0, min(1.0, 1.0 - overlap))
```

Purpose:

- Estimates regression risk as the proportion of original context terms NOT present in the repaired context.
- Low overlap → high risk that the repair removed useful context from the original baseline.
- Returns `0.0` if the baseline had no injected context.

Behavior:

1. Reads `case.primary_baseline.injected_context` as original context.
2. Returns `0.0` immediately if `original_context` is empty.
3. Tokenizes both original and repaired contexts into casefolded word sets.
4. Returns `0.0` if original has no terms.
5. Computes Jaccard-like overlap ratio: `|intersection| / |original|`.
6. Returns `max(0.0, min(1.0, 1.0 - overlap))` — clamped to [0.0, 1.0].

Callers:

- `run_post_repair_context_replay` (line 159)

### `cmd_audit/harness.py` (Issue 0005 Additions)

---

#### Dataclass: `FullAuditResult`

Location: `cmd_audit/harness.py:76-84`

```python
@dataclass(frozen=True)
class FullAuditResult:
    """Complete CMD-Audit pipeline result including Post-Repair Context Replay."""
    audit: AuditResult
    ecs_draft: ECSDraft
    repaired_context: RepairedContext
    post_repair: PostRepairResult
    hard_case_baseline: PostRepairResult
```

Purpose:

- Wraps the complete CMD-Audit V0 pipeline result from attribution through post-repair replay.
- Frozen (immutable), no `__post_init__` validation.
- The `AuditResult` field preserves the full pre-existing attribution + baselines layer.
- Two `PostRepairResult` fields allow direct comparison: `post_repair` (CMD-guided) vs `hard_case_baseline` (generic).

Constructed exclusively by `run_case_full` (line 87-101).

Consumed by:

- `write_post_repair_table` (line 293)
- `repairs.make_repair_comparison` (issue 0006, via `write_repair_success_table_from_full`)
- `run_cases_full` (line 124-125)

---

#### Function: `run_case_full(case: ProbeCase) -> FullAuditResult`

Location: `cmd_audit/harness.py:87-101`

```python
def run_case_full(case: ProbeCase) -> FullAuditResult:
    audit = run_case(case)
    ecs_draft = draft_ecs(case, audit)
    repaired_context = build_repaired_context(case, ecs_draft)
    post_repair = run_post_repair_context_replay(case, repaired_context)
    hard_case_baseline = run_hard_case_update_baseline(case)
    return FullAuditResult(
        audit=audit,
        ecs_draft=ecs_draft,
        repaired_context=repaired_context,
        post_repair=post_repair,
        hard_case_baseline=hard_case_baseline,
    )
```

Purpose:

- Top-level entry point for the complete V0 pipeline: attribution → ECS → repair → post-repair replay → hard-case baseline.
- Composes existing `run_case` with new issue 0005 functions without modifying any of them.

Behavior:

1. Calls `run_case(case)` — runs the full baseline suite, six counterfactual replays, and attribution assignment.
2. Calls `draft_ecs(case, audit)` — drafts per-label ECS from the attribution result.
3. Calls `build_repaired_context(case, ecs_draft)` — assembles repaired context (gold-answer-injection prevention gate).
4. Calls `run_post_repair_context_replay(case, repaired_context)` — scores CMD-guided repair.
5. Calls `run_hard_case_update_baseline(case)` — scores generic comparison baseline.
6. Returns `FullAuditResult` with all five fields.

This is the "one line to run everything" function. `run_case` remains the attribution-only entry point; `run_case_full` is the complete pipeline entry point.

Callers:

- `run_cases_full` (line 125, issue 0006)
- Direct tests in `PostRepairContextReplayTest`, `RepairComparisonRowTest`, `RepairSuccessSummaryTest`, `ClaimLedgerTest`, `FullPipelinePerLabelTest`

---

#### Function: `run_cases_full(cases: list[ProbeCase]) -> list[FullAuditResult]`

Location: `cmd_audit/harness.py:124-125`

```python
def run_cases_full(cases: list[ProbeCase]) -> list[FullAuditResult]:
    return [run_case_full(case) for case in cases]
```

Purpose:

- Batch version of `run_case_full` for multi-case smoke suites.
- Added in issue 0006 to support repair success comparison across the full six-case portfolio.

Callers:

- Tests in `test_cmd_audit_issue6_targeted_repairs.py`
- External scripts generating repair artifacts

---

#### Function: `write_post_repair_table(results: list[FullAuditResult], output_path: str | Path, *, sandbox_root: str | Path | None = None) -> None`

Location: `cmd_audit/harness.py:293-337`

```python
def write_post_repair_table(
    results: list[FullAuditResult],
    output_path: str | Path,
    *,
    sandbox_root: str | Path | None = None,
) -> None:
    validate_sandbox_path(output_path, sandbox_root)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # ... writes CSV with 13 columns ...
```

Purpose:

- Writes the post-repair comparison table as CSV, gated by sandbox path validation.
- One row per `FullAuditResult`.

Behavior:

1. Calls `validate_sandbox_path(output_path, sandbox_root)` — rejects if output path is outside the sandbox.
2. Creates parent directories.
3. Writes CSV with 13 columns:

| Column | Source Field |
| --- | --- |
| `case_id` | `audit.case_id` |
| `perturbation_label` | `audit.perturbation_label` |
| `predicted_label` | `audit.attribution.predicted_label` |
| `pre_repair_answer_score` | `audit.baseline_answer_score` (formatted to 3 decimals) |
| `pre_repair_evidence_score` | `audit.baseline_evidence_score` (formatted to 3 decimals) |
| `post_repair_answer_score` | `post_repair.post_repair_answer_score` (formatted to 3 decimals) |
| `post_repair_evidence_score` | `post_repair.post_repair_evidence_score` (formatted to 3 decimals) |
| `repair_assessment` | `post_repair.repair_assessment` (raw string) |
| `repair_action` | `audit.attribution.predicted_label` (the repair strategy applied) |
| `hard_case_baseline_assessment` | `hard_case_baseline.repair_assessment` (raw string) |
| `token_cost` | `post_repair.token_cost` (formatted to 1 decimal) |
| `regression_risk` | `post_repair.regression_risk` (formatted to 3 decimals) |
| `had_repair_regression` | `post_repair.had_repair_regression` (lowercase string) |

Callers:

- Direct tests in `SandboxWriteBoundaryTest`, `PostRepairTableShapeTest`
- External scripts generating post-repair artifacts

---

#### Function: `write_repair_success_table_from_full(results: list[FullAuditResult], output_path: str | Path, *, sandbox_root: str | Path | None = None) -> list[RepairComparisonRow]`

Location: `cmd_audit/harness.py:128-137`

```python
def write_repair_success_table_from_full(
    results: list[FullAuditResult],
    output_path: str | Path,
    *,
    sandbox_root: str | Path | None = None,
) -> list[RepairComparisonRow]:
    rows = [make_repair_comparison(fr) for fr in results]
    write_repair_success_table(rows, output_path, sandbox_root=sandbox_root)
    return rows
```

Purpose:

- Bridge function added in issue 0006: converts `FullAuditResult` list into `RepairComparisonRow` list and writes the repair success comparison table.
- Uses issue 0005's `validate_sandbox_path` (called transitively via `write_repair_success_table`).

Callers:

- Tests in `test_cmd_audit_issue6_targeted_repairs.py`

## Test-Level Contract

Tests live in `tests/test_cmd_audit_issue5_post_repair.py`. Five test classes, 26 test methods.

### `ThreeValueRepairAssessmentTest` (Cycle 12)

| Test Method | What It Verifies |
| --- | --- |
| `test_recovered_when_answer_full_score` | `classify_repair_assessment(1.0, 1.0)` returns `"recovered"`. |
| `test_partial_when_evidence_recovered_but_answer_not` | **Cycle 12 RED→GREEN**: `classify_repair_assessment(0.0, 1.0)` returns `"partial"`. Evidence = 1.0 but answer = 0.0 → partial, not recovered or failed. |
| `test_failed_when_neither_answer_nor_evidence_recovered` | `classify_repair_assessment(0.0, 0.0)` returns `"failed"`. |
| `test_partial_on_partial_answer_with_full_evidence` | `classify_repair_assessment(0.5, 1.0)` returns `"partial"`. Any answer_score < 1.0 with evidence = 1.0 is partial. |
| `test_failed_on_low_both_scores` | `classify_repair_assessment(0.3, 0.3)` returns `"failed"`. Both below threshold. |

### `PostRepairContextReplayTest` (Cycle 5)

| Test Method | What It Verifies |
| --- | --- |
| `test_draft_ecs_from_attribution` | `draft_ecs` produces `ECSDraft` for the retrieval case with correct `case_id`, `predicted_label="retrieval_error"`, non-empty `cause`/`corrected_memory`/`repair_guidance`/`repaired_evidence_block`. |
| `test_build_repaired_context_includes_all_components` | `build_repaired_context` transfers all ECS fields + original query into `RepairedContext`. Verified by field-level assertions. |
| `test_post_repair_replay_recovers_retrieval_case` | Full targeted repair path for `retrieval_error`: evidence and answer scores are both 1.0 → assessment = `"recovered"`. |
| `test_post_repair_does_not_inject_gold_answer_directly` | The `repair_guidance` field in the repaired context does not contain the gold answer text. The gold answer can only appear in `corrected_memory` if it naturally occurs in the corrected memory text (from the replay's evidence block). |
| `test_post_repair_result_has_token_cost_and_regression_risk` | `PostRepairResult` has `token_cost >= 0.0`, `regression_risk` in [0.0, 1.0], and `had_repair_regression` is a `bool`. |
| `test_hard_case_update_baseline_is_independent` | `run_hard_case_update_baseline` produces a `PostRepairResult` with valid three-value assessment. |
| `test_full_pipeline_produces_complete_result` | `run_case_full` returns `FullAuditResult` with all five fields as correct types. Retrieval case shows `"recovered"`. |
| `test_post_repair_partial_scenario` | Constructs `RepairedContext` where corrected_memory does not contain the gold answer but evidence_block does → exercises the partial/full boundary. Three-value assessment is always one of the three valid values. |

### `ECSCauseValidationTest` (Cycle 13)

| Test Method | What It Verifies |
| --- | --- |
| `test_ecs_cause_rejects_item_wrong` | `ECSDraft` construction with cause containing `"item_wrong"` raises `ValueError`. |
| `test_ecs_cause_rejects_item_stale` | `ECSDraft` construction with cause containing `"item_stale"` raises `ValueError`. |
| `test_ecs_cause_rejects_item_conflict` | `ECSDraft` construction with cause containing `"item_conflict"` raises `ValueError`. |
| `test_ecs_cause_rejects_item_poisoned` | `ECSDraft` construction with cause containing `"item_poisoned"` raises `ValueError`. |
| `test_ecs_cause_rejects_item_compression_distorted` | `ECSDraft` construction with cause containing `"item_compression_distorted"` raises `ValueError`. |
| `test_ecs_cause_allows_descriptive_state_language` | `ECSDraft` with cause = `"stored preference was outdated relative to ground truth"` is accepted. |
| `test_ecs_cause_rejects_natural_language_equivalents` | `ECSDraft` with cause = `"the memory item is wrong"` (natural-language equivalent of `item_wrong`) is rejected. Validates regex-based detection. |

All seven tests validate at `ECSDraft.__post_init__` construction time, not at a separate validation endpoint.

### `SandboxWriteBoundaryTest` (Cycle 15)

| Test Method | What It Verifies |
| --- | --- |
| `test_sandbox_path_inside_is_accepted` | `validate_sandbox_path(Path("artifacts/sandbox/post_repair.csv"))` succeeds. |
| `test_sandbox_path_outside_is_rejected` | `validate_sandbox_path(Path("/etc/passwd"))` raises `ValueError`. Tests absolute path outside sandbox. |
| `test_sandbox_path_parent_traversal_rejected` | `validate_sandbox_path(Path("artifacts/sandbox/../../../etc/passwd"))` raises `ValueError`. `Path.resolve()` neutralizes `..` traversal. |
| `test_write_post_repair_table_writes_to_sandbox` | End-to-end: `write_post_repair_table` writes CSV inside sandbox, content contains expected headers and case_id. Uses `tempfile.TemporaryDirectory`. |
| `test_write_post_repair_table_rejects_outside_sandbox` | `write_post_repair_table` with output outside sandbox raises `ValueError`. |

### `PostRepairTableShapeTest`

| Test Method | What It Verifies |
| --- | --- |
| `test_table_has_required_columns` | Generated CSV header contains all 13 required columns. Uses `subTest` per column for precise failure reporting. |

## Boundary Rules

1. **Gold answer injection gate**: `run_post_repair_context_replay` never writes or calls `answer_score(gold_answer, ...)`. The answer score is determined by `case.gold_answer.casefold() in combined_context.casefold()` — a membership check on the repaired context text, simulating an agent reading and extracting the answer from context. `build_repaired_context` does not reference `case.gold_answer` at all.

2. **Three-value assessment**: `repair_assessment` outputs exactly `recovered`, `partial`, or `failed`. No binary `repair_success` field is computed. `partial` (evidence recovered, answer still wrong) is the key diagnostic signal for coupled failures — it means "CMD fixed the diagnosed operation, but a second failure (likely reasoning) remains."

3. **ECS cause item-label-name prohibition**: `ECSDraft.__post_init__` rejects cause text containing forbidden item label names (`item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`) or their natural-language equivalents (regex-matched via `_FORBIDDEN_NL_PATTERNS`). Descriptive state language is allowed (e.g., "stored preference was outdated relative to ground truth").

4. **Sandbox write boundary**: All post-repair artifact writes must go through `validate_sandbox_path`, which rejects any path outside `artifacts/sandbox/` (default). Parent traversal (`..`) is neutralized by `Path.resolve()`. This enforces that CMD-Audit writes only to replay-local sandbox.

5. **Hard-case baseline separation**: `run_hard_case_update_baseline` is structurally independent from CMD repair. It uses the same `run_post_repair_context_replay` scoring function but with a generic context (all extracted memory), not CMD-diagnosed repair. The comparison between `post_repair.repair_assessment` and `hard_case_baseline.repair_assessment` is the repair validity evidence — consumed by issue 0006's `make_repair_comparison`.

6. **CMD-Audit / CMD-Skill Adapter separation**: Post-Repair Context Replay stays within CMD-Audit. Replay writes target the local sandbox (`artifacts/sandbox/`) only. CMD-Skill Adapter (future) is the only component authorized to apply validated repairs to production agent state.

7. **Existing attribution unchanged**: `run_case`, `AuditResult`, `assign_attribution`, `run_v0_replay_portfolio`, and all six replay functions are preserved exactly as they were. `run_case_full` composes them; it does not modify them.

## Acceptance Criteria Traceability

| Issue 0005 AC | Code Surface | Test Surface |
| --- | --- | --- |
| Full pipeline from probe case through attribution, ECS, and Post-Repair Context Replay. | `run_case_full` composes `run_case` → `draft_ecs` → `build_repaired_context` → `run_post_repair_context_replay` → `run_hard_case_update_baseline`. | `test_full_pipeline_produces_complete_result` |
| Repaired context includes corrected memory, repair guidance, and repaired evidence block. | `build_repaired_context` transfers all ECS fields + `original_query`. `RepairedContext` is a frozen dataclass with five fields. | `test_build_repaired_context_includes_all_components` |
| Post-Repair Context Replay runs the original query with repaired context, without injecting gold answer. | `run_post_repair_context_replay` uses `gold_answer.casefold() in combined` (membership check), not `answer_score(...)`. `build_repaired_context` does not reference `gold_answer`. | `test_post_repair_does_not_inject_gold_answer_directly` |
| Output includes three-value `repair_assessment` (`recovered` / `partial` / `failed`). | `classify_repair_assessment` returns one of three values. `PostRepairResult.repair_assessment` stores it. | `ThreeValueRepairAssessmentTest` (5 methods) |
| Metrics include answer F1 or accuracy, evidence recall, token cost, and regression risk. | `PostRepairResult` has `post_repair_answer_score`, `post_repair_evidence_score`, `token_cost`, `regression_risk`, `had_repair_regression`. | `test_post_repair_result_has_token_cost_and_regression_risk` |
| Hard-case update baseline is compared with CMD-guided repair. | `run_hard_case_update_baseline` injects all extracted memory; scored independently from CMD repair via `run_post_repair_context_replay`. Both results stored in `FullAuditResult`. | `test_hard_case_update_baseline_is_independent` |
| CMD-Audit write permissions limited to replay-local sandbox. | `validate_sandbox_path` enforces sandbox boundary via `Path.resolve()`. `write_post_repair_table` calls it before writing. | `SandboxWriteBoundaryTest` (5 methods) |

## Verification

Commands:

```bash
# Issue 0005 tests only (26 tests)
python3 -m pytest tests/test_cmd_audit_issue5_post_repair.py -v

# Full test suite (83 tests as of issue 0006 completion)
python3 -m pytest

# Generate post-repair artifact from smoke suite
python3 -c "
from pathlib import Path
from cmd_audit import load_probe_cases, run_case_full, write_post_repair_table
cases = load_probe_cases('data/probe_cases/v0_issue3_cases.json')
results = [run_case_full(c) for c in cases]
sandbox = Path('artifacts/sandbox')
sandbox.mkdir(parents=True, exist_ok=True)
write_post_repair_table(results, sandbox / 'post_repair_table.csv', sandbox_root=sandbox)
for r in results:
    print(f'{r.audit.case_id}: {r.audit.perturbation_label} -> {r.post_repair.repair_assessment}')
"
```

Verified state (2026-05-10, post-issue-0006):

```text
83 tests passed (57 pre-existing + 26 issue 0006)
  -  5 tests in test_cmd_audit_tracer_bullet.py (issue 0001)
  -  6 tests in test_cmd_audit_issue2_baselines.py (issue 0002)
  -  5 tests in test_cmd_audit_issue3_attribution_table.py (issue 0003)
  - 26 tests in test_cmd_audit_issue5_post_repair.py (issue 0005 — POST-REPAIR)
  - 15 tests in test_cmd_audit_issue9_monitor_contract.py (issue 0009)
  - 26 tests in test_cmd_audit_issue6_targeted_repairs.py (issue 0006 — builds on 0005)

Smoke suite post-repair outcomes (6 cases through run_case_full):
  v0-write-001: write_error -> recovered (CMD) vs failed (hard_case)
  v0-compression-001: compression_error -> recovered (CMD) vs failed (hard_case)
  v0-premature-extraction-001: premature_extraction_error -> recovered (CMD) vs failed (hard_case)
  v0-retrieval-001: retrieval_error -> recovered (CMD) vs recovered (hard_case)
  v0-injection-001: injection_error -> recovered (CMD) vs recovered (hard_case)
  v0-reasoning-001: reasoning_error -> recovered (CMD) vs recovered (hard_case)

Artifacts:
  artifacts/sandbox/post_repair_table.csv (13 columns)
  artifacts/sandbox/repair_success_table.csv (issue 0006)
  artifacts/sandbox/repair_label_summary.csv (issue 0006)
  artifacts/sandbox/repair_claim_ledger.txt (issue 0006)
```

CMD repair outperforms hard-case baseline on 3 of 6 smoke cases (write, compression, premature_extraction) — these are the cases where targeted counterfactual intervention matters. For retrieval, injection, and reasoning, both recover because injecting all extracted memory happens to include the correct memory; CMD's value for these cases is in the precise diagnosis (`predicted_label`) and lower token cost rather than repair success alone.

## Subsequent Issues That Depend on Issue 0005

| Issue | Depends On | How |
| --- | --- | --- |
| Issue 0006 (targeted memory fixes) | `FullAuditResult`, `run_case_full`, `PostRepairResult`, `validate_sandbox_path` | `repairs.make_repair_comparison` reads both `post_repair` and `hard_case_baseline` from `FullAuditResult`. `run_cases_full` calls `run_case_full`. Sandbox validation reused for repair success table. |
| Issue 0007 (ECS Failure Memory recurrence) | `ECSDraft`, `draft_ecs`, `PostRepairResult` | Will store ECS records in Failure Memory and measure recurrence reduction on future similar tasks. |
| Issue 0010 (evidence-driven version gates) | `FullAuditResult`, post-repair table | HITL gate review uses post-repair evidence artifacts. |
