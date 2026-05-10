# Issue 0004 Implementation Details: Attribution Taxonomy Boundary Review

## Purpose

This document is the zoomed-out implementation map for issue 0004, `Review attribution taxonomy boundaries`.

Issue 0004 is the HITL gate after the first six-replay V0 attribution table exists. Its job is to inspect the smoke-suite outputs, challenge every label boundary, and either confirm or revise the V0 taxonomy before Post-Repair Context Replay begins:

```text
artifacts/attribution_table.csv
artifacts/comparison_metrics.csv
artifacts/attribution_confusion_matrix.csv
  -> per-case replay gain inspection
  -> confusion / near-confusion pattern detection
  -> premature_extraction_error vs retrieval_error boundary re-check
  -> top-2 / multi-label rule review
  -> Subagent Judge Baseline/Monitor separation audit
  -> bad-memory-item exclusion audit
  -> deferred label registration audit
  -> HITL verdict
```

The reviewed slice confirms V0 taxonomy is correct for the current smoke suite and records edge cases for future richer probe suites.

## Source Requirements

The review follows these local documents.

| Source | Requirement Applied In Issue 0004 |
| --- | --- |
| `TASK.md` | Inspect confusions or near-confusions in the six smoke cases; decide whether `premature_extraction_error` remains distinct from `retrieval_error`; clarify top-2 or multi-label rules for coupled failures; keep Subagent Judge Baseline and Subagent Judge Monitor separate from final CMD attribution; keep V0 bad-memory-item exclusion as a boundary rule. |
| `CLAUDE.md` | Treat `cmd_innovation_core/` as source of truth; keep **CMD-Audit** separate from **CMD-Skill Adapter**; output only the six V0 pipeline labels; do not silently broaden the taxonomy. |
| `cmd_innovation_core/CONTEXT.md` | Use **V0 Core Label Set**, **Premature Extraction Error**, **Verbatim Event Oracle**, **Subagent Judge Baseline**, **Subagent Judge Monitor** consistently; respect all Flagged Ambiguities. |
| `cmd_innovation_core/prd/cmd_minimal_probe_prd.md` | Review ambiguous attribution cases after the first table exists; confirm V0 taxonomy boundaries are working for all six labels; keep `CMD-Audit` and `CMD-Skill Adapter` separate; register `ingestion_error` as deferred. |
| `cmd_innovation_core/issues/0003-counterfactual-attribution-table-implementation-details.md` | Provides the V0 Replay Portfolio, replay-to-label mapping, attribution table structure, and confusion matrix that issue 0004 reviews. |
| `cmd_innovation_core/tdd/cmd_tracer_bullets.md` | Cycle 4 (Coupled Failure) boundary informs top-2 review; Cycle 10 (Bad Memory Item Exclusion) boundary informs the item-label exclusion audit. |

## Domain Boundary

Issue 0004 reviews the first attribution evidence. It does not change labels, replays, or attribution logic.

It does own:

- inspecting per-case replay gain columns in `attribution_table.csv` for confusions or near-confusions;
- re-checking the `premature_extraction_error` / `retrieval_error` boundary with Verbatim Event Oracle fixture evidence;
- reviewing top-2 and `is_ambiguous` behavior for coupled failure readiness;
- auditing Subagent Judge Baseline and Monitor separation from CMD attribution in `comparison_metrics.csv`;
- auditing bad-memory-item absence from all V0 artifacts;
- registering `ingestion_error` as a deferred V1 label;
- reviewing grill-session crossover edge cases (A: both Verbatim Event Oracle and Oracle Retrieval recover; B: both fail but Oracle Compression succeeds);
- confirming or revising ECS `cause` item-state description rules;
- issuing a HITL verdict.

It does not own:

- changing replay logic or adding new replay paths;
- changing attribution `tie_margin` or `positive_gain_threshold`;
- adding new probe cases;
- implementing Post-Repair Context Replay;
- implementing ECS or Failure Memory.

## Review Artifacts

| Artifact | Role in issue 0004 |
| --- | --- |
| `artifacts/attribution_table.csv` | Per-case replay scores, recovery gains, predicted labels, top-2 labels, ambiguity flags, and per-replay gain columns for all six V0 replays. |
| `artifacts/comparison_metrics.csv` | CMD-Audit vs evidence_recall vs subagent_judge vs random_label accuracy, macro F1, top-2 accuracy, and cost per diagnosis. |
| `artifacts/attribution_confusion_matrix.csv` | 6×6 confusion matrix with gold labels as rows and predicted labels as columns. |
| `data/probe_cases/v0_issue3_cases.json` | Six-case smoke suite: one case per V0 pipeline label. |
| `data/probe_cases/v0_premature_extraction_error_case.json` | Focused Verbatim Event Oracle boundary fixture. |
| `tests/test_cmd_audit_issue3_attribution_table.py` | Behavior-level tests for the V0 Replay Portfolio and attribution table. |
| `cmd_audit/labels.py` | V0 label set, replay-to-label mapping, deferred label registry, `validate_v0_label`. |
| `cmd_audit/attribution.py` | `assign_attribution` with `tie_margin=0.05` and `positive_gain_threshold=0.0`. |
| `cmd_audit/replays.py` | Six V0 replay functions with per-replay recovery logic. |
| `cmd_audit/baselines.py` | Comparator outputs and Subagent Judge Monitor decision. |
| `cmd_audit/harness.py` | Attribution table writer, comparison metrics writer, confusion matrix writer. |

## Review Method

For each of the six smoke cases, the review inspects:

1. **Predicted label vs perturbation label**: does CMD-Audit assign the correct label?
2. **Top replay gain vs second-best gain**: is there a near-confusion (delta < `tie_margin`)?
3. **Cross-replay gain pattern**: could another replay plausibly recover the case under a different fixture design?
4. **Boundary integrity**: does the case respect V0 label boundaries (no deferred labels, no item labels)?

For the comparator layer, the review inspects:

1. **CMD vs comparator separation**: are CMD-Audit predictions computed independently from evidence_recall, subagent_judge, and random_label?
2. **Subagent Judge Monitor payload**: does the monitor output stay within leak-safe boundaries?

For the taxonomy as a whole, the review inspects:

1. **Label distinctiveness**: can any two V0 labels be collapsed without losing diagnostic signal?
2. **Coverage gap**: is any common memory failure pattern not representable by the six V0 labels?
3. **Deferred label readiness**: are `ingestion_error` and other deferred labels properly registered?

## Per-Case Boundary Analysis

### Case v0-write-001 (`write_error`)

**Fixture design**: Gold evidence ("Kai chose Madrid for the partner workshop") has no `source_memory_id` and no `source_event_id`. Raw event text is generic ("the final city was not written into the memory event stream"). Extracted memory is lossy ("Kai discussed a partner workshop location"). Baseline retrieves the lossy memory and answers "Unknown".

**Replay gain pattern**:

| Replay | answer_score | evidence_score | recovery_gain |
| --- | --- | --- | --- |
| `oracle_write` | 1.000 | 1.000 | 1.000 |
| `oracle_compression` | 0.000 | 0.000 | 0.000 |
| `verbatim_event_oracle` | 0.000 | 0.000 | 0.000 |
| `oracle_retrieval` | 0.000 | 0.000 | 0.000 |
| `injection_oracle` | 0.000 | 0.000 | 0.000 |
| `evidence_given_reasoning` | 0.000 | 0.000 | 0.000 |

**Boundary assessment**: Clean single-replay recovery. Only Oracle Write can recover because gold evidence has no `source_memory_id` or `source_event_id` — the evidence was never written to the event stream or extracted memory. This is the correct behavior: `write_error` is the only label for "evidence not present in any recoverable form."

**Edge case note**: If the raw event were truncated upstream (evidence never reached the agent), the gain pattern would be identical. In V0, this is subsumed under `write_error`. V1 may split `ingestion_error` if these cases have distinct repair paths.

### Case v0-compression-001 (`compression_error`)

**Fixture design**: Gold evidence points to `source_memory_id: "mem-101"`. Raw event contains "Omar chose Prague for the retention review." Extracted memory `mem-101` text is "Omar chose a Central European city for the retention review" — the city name "Prague" was lost during compression.

**Replay gain pattern**:

| Replay | answer_score | evidence_score | recovery_gain |
| --- | --- | --- | --- |
| `oracle_write` | 0.000 | 0.000 | 0.000 |
| `oracle_compression` | 1.000 | 1.000 | 1.000 |
| `verbatim_event_oracle` | 0.000 | 0.000 | 0.000 |
| `oracle_retrieval` | 0.000 | 0.000 | 0.000 |
| `injection_oracle` | 0.000 | 0.000 | 0.000 |
| `evidence_given_reasoning` | 0.000 | 0.000 | 0.000 |

**Boundary assessment**: Clean single-replay recovery. Oracle Compression recovers because `source_memory_id` points to `mem-101` and the gold evidence text ("Omar chose Prague...") differs from the stored text ("Omar chose a Central European city..."). Oracle Retrieval has zero gain because the Memory Item `mem-101` does not satisfy `evidence_recall_from_text` for the required phrases — the compression already destroyed the evidence within that Memory Item.

**Why this is not `retrieval_error`**: The baseline retrieved `mem-101`, but `mem-101`'s text does not contain "Prague". Oracle Retrieval checks `evidence_recall_from_text((gold_evidence,), memory_item.text)` and correctly finds the evidence phrase is absent. The failure is in the Memory Item representation, not in retrieval.

### Case v0-premature-extraction-001 (`premature_extraction_error`)

**Fixture design**: Gold evidence has `source_event_id: "evt-201"` and no `source_memory_id`. Raw event `evt-201` contains "Nia chose Berlin for the incident review." Extracted memory `mem-201` text is "Nia selected a European city for the incident review" — the specific city "Berlin" was lost during extraction.

**Replay gain pattern**:

| Replay | answer_score | evidence_score | recovery_gain |
| --- | --- | --- | --- |
| `oracle_write` | 0.000 | 0.000 | 0.000 |
| `oracle_compression` | 0.000 | 0.000 | 0.000 |
| `verbatim_event_oracle` | 1.000 | 1.000 | 1.000 |
| `oracle_retrieval` | 0.000 | 0.000 | 0.000 |
| `injection_oracle` | 0.000 | 0.000 | 0.000 |
| `evidence_given_reasoning` | 0.000 | 0.000 | 0.000 |

**Boundary assessment**: This is the most important V0 boundary and it holds correctly. Verbatim Event Oracle recovers because `source_event_id: "evt-201"` exists and the raw event text contains "Berlin". Oracle Retrieval has zero gain because `_recover_extracted_gold_evidence` skips gold evidence items with no `source_memory_id`. The case is unambiguously `premature_extraction_error`.

**Why this is not `retrieval_error`**: No extracted Memory Item preserves "Berlin." Oracle Retrieval over extracted memory cannot recover evidence that extraction already lost. `evidence_recall_from_text(gold_evidence, mem-201.text)` returns 0.0 because "Berlin" is not in "Nia selected a European city for the incident review."

**Cross-reference**: `test_verbatim_event_oracle_beats_oracle_retrieval_for_extraction_loss` in `tests/test_cmd_audit_issue3_attribution_table.py` asserts exactly this boundary.

### Case v0-retrieval-001 (`retrieval_error`)

**Fixture design**: Gold evidence points to `source_memory_id: "mem-301"`. Extracted memory `mem-301` correctly preserves "Mira chose Lisbon for the Q3 offsite." Baseline vector_memory retrieves `mem-302` ("Porto was considered...but rejected"), a distractor.

**Replay gain pattern**:

| Replay | answer_score | evidence_score | recovery_gain |
| --- | --- | --- | --- |
| `oracle_write` | 0.000 | 0.000 | 0.000 |
| `oracle_compression` | 0.000 | 0.000 | 0.000 |
| `verbatim_event_oracle` | 0.000 | 0.000 | 0.000 |
| `oracle_retrieval` | 1.000 | 1.000 | 1.000 |
| `injection_oracle` | 0.000 | 0.000 | 0.000 |
| `evidence_given_reasoning` | 0.000 | 0.000 | 0.000 |

**Boundary assessment**: Clean single-replay recovery. Oracle Retrieval recovers `mem-301` because: (a) `source_memory_id` exists, (b) `mem-301` is not in baseline `retrieved_memory_ids` (baseline retrieved `mem-302`), and (c) `mem-301.text` satisfies `evidence_recall_from_text` for the required phrases. Injection-Oracle has zero gain because the baseline did not retrieve the correct Memory Item.

### Case v0-injection-001 (`injection_error`)

**Fixture design**: Gold evidence points to `source_memory_id: "mem-401"`. Extracted memory `mem-401` correctly preserves "Lina chose Oslo for the launch rehearsal." Baseline vector_memory retrieves `mem-401` but the `injected_context` says "A launch rehearsal memory was retrieved, but the evidence block omitted the city" — the correct memory was retrieved but the context injection lost the evidence.

**Replay gain pattern**:

| Replay | answer_score | evidence_score | recovery_gain |
| --- | --- | --- | --- |
| `oracle_write` | 0.000 | 0.000 | 0.000 |
| `oracle_compression` | 0.000 | 0.000 | 0.000 |
| `verbatim_event_oracle` | 0.000 | 0.000 | 0.000 |
| `oracle_retrieval` | 0.000 | 0.000 | 0.000 |
| `injection_oracle` | 1.000 | 1.000 | 1.000 |
| `evidence_given_reasoning` | 0.000 | 0.000 | 0.000 |

**Boundary assessment**: Clean single-replay recovery. Injection-Oracle recovers because: (a) `source_memory_id` exists and points to `mem-401`, (b) the baseline retrieved `mem-401` (it's in `retrieved_memory_ids`), and (c) the baseline `injected_context` does NOT recall all gold evidence (evidence_score is 0.0). Oracle Retrieval has zero gain because `_recover_extracted_gold_evidence` skips gold evidence items whose `source_memory_id` is already in the baseline's `retrieved_memory_ids` — the correct memory was retrieved, so the failure is not retrieval.

**Why this is not `retrieval_error`**: The baseline did retrieve the correct Memory Item. The failure happened when the evidence was formatted into the model context. Injection-Oracle checks this by looking at whether the retrieved Memory Item text contains gold evidence that the injected context lost.

### Case v0-reasoning-001 (`reasoning_error`)

**Fixture design**: Gold evidence points to `source_memory_id: "mem-501"`. Extracted memory `mem-501` correctly preserves "Pavel chose Dublin for the finance sync." Baseline vector_memory retrieves `mem-501` and the `injected_context` correctly contains "Pavel chose Dublin for the finance sync" with `evidence_score: 1.0`. But the baseline answer is "Cork" — wrong reasoning over correct evidence.

**Replay gain pattern**:

| Replay | answer_score | evidence_score | recovery_gain |
| --- | --- | --- | --- |
| `oracle_write` | 0.000 | 0.000 | 0.000 |
| `oracle_compression` | 0.000 | 0.000 | 0.000 |
| `verbatim_event_oracle` | 0.000 | 0.000 | 0.000 |
| `oracle_retrieval` | 0.000 | 0.000 | 0.000 |
| `injection_oracle` | 0.000 | 0.000 | 0.000 |
| `evidence_given_reasoning` | 1.000 | 1.000 | 1.000 |

**Boundary assessment**: Clean single-replay recovery. Evidence-Given Reasoning recovers because: (a) the baseline `injected_context` already recalls all gold evidence (`evidence_score: 1.0` — "Pavel chose Dublin for the finance sync" contains all required phrases), and (b) `baseline.answer_score < 1.0` (answer is "Cork", not "Dublin").

**Why this is not `injection_error`**: The baseline already has correct evidence in context. The failure is in the final reasoning step over valid evidence. Evidence-Given Reasoning uses the baseline's own injected context as the evidence block, confirming that the context was sufficient but the answer was wrong.

## Cross-Case Pattern Analysis

### Confusion Matrix Audit

The `artifacts/attribution_confusion_matrix.csv` shows a perfect diagonal:

| gold_label | write | compression | premature_extraction | retrieval | injection | reasoning |
| --- | --- | --- | --- | --- | --- | --- |
| write_error | 1 | 0 | 0 | 0 | 0 | 0 |
| compression_error | 0 | 1 | 0 | 0 | 0 | 0 |
| premature_extraction_error | 0 | 0 | 1 | 0 | 0 | 0 |
| retrieval_error | 0 | 0 | 0 | 1 | 0 | 0 |
| injection_error | 0 | 0 | 0 | 0 | 1 | 0 |
| reasoning_error | 0 | 0 | 0 | 0 | 0 | 1 |

**Finding**: Zero off-diagonal confusions. This is expected for a six-case smoke suite where each case is designed to trigger exactly one replay path. The confusion matrix is not yet stress-tested with coupled failures or ambiguous cases.

**Implication for top-2 behavior**: The `tie_margin=0.05` and `is_ambiguous` logic is correctly implemented but untested by the current smoke suite. Every case has a single replay with gain=1.0 and all others at 0.0 — a 1.0 delta that far exceeds the 0.05 tie margin. The top-2 logic will need validation when richer probe cases with genuinely coupled failures are added (planned for issue 0005).

### Comparator Separation Audit

The `artifacts/comparison_metrics.csv` confirms separation:

| system_name | attribution_accuracy | macro_f1 | top2_accuracy | cost_per_diagnosis |
| --- | --- | --- | --- | --- |
| CMD-Audit | 1.000 | 1.000 | 1.000 | 6.200 |
| evidence_recall | 0.833 | 0.778 | 0.833 | 0.050 |
| subagent_judge | 0.833 | 0.778 | 0.833 | 1.000 |
| random_label | 0.167 | 0.167 | 0.667 | 0.010 |

**Finding**: CMD-Audit outperforms all comparators on the smoke suite (1.0 vs 0.833 macro F1). The 0.167 gap between CMD and evidence_recall/subagent_judge is meaningful on the smoke suite because the comparators are observational (they read the failed trace without counterfactual intervention), while CMD runs actual replay interventions. This gap is expected to widen with richer probe cases that have genuinely ambiguous failure signatures.

**Subagent Judge Monitor separation**: The monitor's `to_payload()` output is included in `BaselineSuiteResult.monitor` but never flows into `attribution.predicted_label`. The monitor decision (`should_trigger_replay`, `risk_score`, `anomaly_reason`, `evidence_pointers`) is structurally separate from the attribution result (`predicted_label`, `top_replay`, `recovery_gain`). The `harness.diagnosis_predictions()` function builds CMD-Audit predictions from `result.attribution`, not from `result.baseline_suite.monitor`.

### Bad Memory Item Exclusion Audit

Verified across all three artifacts:

- `attribution_table.csv`: `predicted_label` column contains only the six V0 pipeline labels.
- `comparison_metrics.csv`: No item labels appear as system names or metric dimensions.
- `attribution_confusion_matrix.csv`: Row and column headers are only the six V0 pipeline labels.

**Finding**: Bad memory item labels (`item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`) are absent from all V0 output artifacts. The `validate_v0_label()` function in `cmd_audit/labels.py` enforces this at the code boundary: any attempt to use an item label raises `LabelValidationError`.

## Grill-Session Crossover Edge Cases

### Edge Case A: Both Verbatim Event Oracle and Oracle Retrieval Recover

**Scenario**: A probe case where raw events contain the evidence AND an extracted Memory Item also preserves it. Both Verbatim Event Oracle and Oracle Retrieval produce positive recovery gains.

**Governing rule**: `assign_attribution()` sorts replay results by `recovery_gain` descending. The replay with the highest gain wins. If both have gain=1.0, the first in sort order wins (stable sort). `tie_margin=0.05` means both labels appear in `top2_labels` and `is_ambiguous=True`.

**Assessment**: Not observed in the smoke suite (all cases have exactly one replay at gain=1.0). When richer probe cases are added, this edge case will naturally exercise the top-2 logic. No taxonomy change needed — gain ranking is the correct arbiter.

**Recorded in**: `CONTEXT.md` Flagged Ambiguities.

### Edge Case B: Both Verbatim Event Oracle and Oracle Retrieval Fail, Oracle Compression Succeeds

**Scenario**: Raw events don't directly contain the evidence (scattered across multiple events), and no extracted Memory Item preserves it either. But a Memory Item exists whose pre-compression text would have contained it. Oracle Compression recovers while the first two replays do not.

**Assessment**: Not observed in the smoke suite. The diagnosis cost of running two non-recovering replays (Verbatim Event Oracle + Oracle Retrieval) before Oracle Compression finds the root cause is $2.0 extra (2 × default cost_units of 1.0). This is design-internal and bounded within the six-replay smoke suite ($6.2 total). No taxonomy change needed.

**Recorded in**: `CONTEXT.md` Flagged Ambiguities and `cmd_innovation_core/knowledge/current-memory.md`.

## Deferred Label Registration Audit

### `ingestion_error`

**Status**: Registered as a deferred V1 label in three locations:
- `cmd_innovation_core/CONTEXT.md`: Language section definition + deferred labels list
- `cmd_innovation_core/prd/cmd_minimal_probe_prd.md`: V0 Scope deferred labels + AC5
- `cmd_audit/labels.py`: `DEFERRED_PIPELINE_LABELS` frozenset

**V0 subsumption rule**: Cases where evidence never reached the agent are subsumed under `write_error` in V0. The counterfactual intervention (Oracle Write) is identical for both "evidence not written" and "evidence never arrived." V1 may split `ingestion_error` if these cases prove to have distinct repair paths.

**Validation**: `validate_v0_label("ingestion_error")` raises `LabelValidationError` with the message that it is deferred to V1/V2. The label is not accepted in V0 attribution or comparator predictions.

### Other Deferred Labels

| Label | Status |
| --- | --- |
| `granularity_error` | Registered in `DEFERRED_PIPELINE_LABELS`. Rejected by `validate_v0_label`. |
| `route_error` | Registered in `DEFERRED_PIPELINE_LABELS`. Rejected by `validate_v0_label`. |
| `graph_error` | Registered in `DEFERRED_PIPELINE_LABELS`. Rejected by `validate_v0_label`. |
| `safety_error` | Registered in `DEFERRED_PIPELINE_LABELS`. Rejected by `validate_v0_label`. |
| `ingestion_error` | Registered in `DEFERRED_PIPELINE_LABELS`. Rejected by `validate_v0_label`. |

## ECS Cause Item-State Description Rules

**Rule**: ECS `cause` may describe item state in natural language (e.g., "stored preference was outdated relative to ground truth") but must not use V0-forbidden item label names (`item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`) or re-declare them through natural language equivalents (e.g., "the memory item is stale").

**Status**: Rule is documented in `CONTEXT.md` Flagged Ambiguities, `PRD` AC7, `current-memory.md` decision #10, and `TASK.md` Boundary Acceptance Conditions. Enforcement will be implemented in issue 0007 (ECS Failure Memory) when ECS records are first constructed.

## Test Coverage

| Test | What it verifies for issue 0004 |
| --- | --- |
| `test_verbatim_event_oracle_beats_oracle_retrieval_for_extraction_loss` | Verbatim Event Oracle recovers + Oracle Retrieval does not = `premature_extraction_error`. The most important V0 boundary. |
| `test_raw_event_only_evidence_is_valid_probe_case` | Raw-event-only gold evidence (no `source_memory_id`) loads as a valid probe case. |
| `test_issue3_suite_attributes_all_v0_pipeline_labels` | All six V0 labels are covered by the smoke suite; each maps to the expected top replay. |
| `test_confusion_matrix_contains_one_diagonal_count_per_v0_label` | Confusion matrix has exactly one diagonal count per V0 label in the smoke suite. |
| `test_v0_accepts_only_pipeline_labels` | `validate_v0_label` accepts all six V0 pipeline labels and rejects item labels + deferred labels. |
| `test_issue2_baseline_suite_keeps_comparators_separate_from_cmd` | BaselineSuiteResult comparators are structurally separate from CMD replay attribution. |
| `test_monitor_payload_can_trigger_replay_without_forbidden_outputs` | Monitor payload triggers replay without containing forbidden fields. |
| `test_monitor_rejects_final_labels_ecs_memory_writes_gold_answers_and_full_traces` | Monitor rejects payloads with forbidden field names. |

## HITL Verdict

**Date**: 2026-05-09

**Decision**: V0 six-label taxonomy is confirmed. No boundary changes needed.

**Evidence**:
1. All six V0 labels show clean single-replay recovery in the smoke suite (no confusions, no near-confusions).
2. `premature_extraction_error` remains distinct from `retrieval_error` — the Verbatim Event Oracle boundary is validated by fixture and test.
3. Top-2 and `is_ambiguous` logic is correctly implemented and will activate when richer probe cases with coupled failures are added.
4. Subagent Judge Baseline and Monitor remain structurally separate from CMD-Audit attribution in all artifacts.
5. Bad memory item labels are absent from all V0 output artifacts.
6. `ingestion_error` is properly registered as deferred; V0 subsumption under `write_error` is documented.
7. Grill-session crossover edge cases A and B are documented and judged non-problematic.
8. ECS cause item-state description rules are documented for future enforcement in issue 0007.

**Next step**: Proceed to issue 0005 — Validate Post-Repair Context Replay with three-value `repair_assessment`.

## Remaining Work After Issue 0004

Issue 0004 is the HITL gate before Post-Repair Context Replay. The next slices:

- Issue 0005: Post-Repair Context Replay with three-value `repair_assessment`.
- Issue 0006: Targeted memory fixes mapped from attribution labels.
- Issue 0007: ECS Failure Memory recurrence reduction (enforces ECS cause item-label-name rules).
- Issue 0010: Evidence-driven version gates (HITL, blocked by 0004/0005/0007).
