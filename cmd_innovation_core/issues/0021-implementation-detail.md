---
name: "0021: Pre-CMD Hook Redesign Implementation Detail"
description: Implementation detail for Decision 33 two-stage Pre-CMD Hook, RPE Judge per-replay ranking, harness integration, and supplementary calibration path.
status: complete-runtime-calibration-deferred-to-0028
---

# Issue 0021: Pre-CMD Hook Redesign — Implementation Detail

**Date**: 2026-05-24  
**Decision**: Decision 33, updated by Decision 34 R5  
**Status**: Runtime hook implemented. Calibrated constants remain supplementary and are handled by issue 0028.

## Zoom-Out Map

Issue 0021 owns the **Pre-CMD Hook**. It is the gate before the **CMD Diagnosis Layer**, not the diagnosis layer itself.

```text
Memory-Augmented Agent retrieval
  -> RetrievedItem tuple
  -> Pre-CMD Hook
       Stage 1: empty_ctx hard trigger
       Stage 2: RPE Judge per-replay p-score ranking
  -> PreCmdDecision
       trigger_cmd=False -> skip CMD, AuditResult(attribution=None)
       trigger_cmd=True  -> selected_replays subset
  -> CMD Diagnosis Layer
       counterfactual replay -> Recovery Gain -> Operation-Level Attribution
  -> ECS / RepairOrchestrator / Failure Memory
```

The hook answers only one question: **should CMD spend replay budget, and which replays should run first?** It must not emit attribution labels, ECS records, RepairActions, gold answers, or memory writes.

After Decision 34 R5, the hook is a **supplementary cost-reduction artifact**. Headline attribution experiments can bypass it with `use_hook=False` and run all 10 replays.

## Decision Mapping

| Decision point | Requirement | Implementation |
| --- | --- | --- |
| Two-stage architecture | Replace Decision 31 parallel OR gate with sequential `empty_ctx -> RPE Judge` | `cmd_audit/hook/post_retrieve_hook.py` |
| Stage vocabulary | Use only `empty_ctx`, `rpe_top_k`, `rpe_below_threshold` | `PreCmdDecision.stage` validation |
| No `fallback_triggered` | Derive fallback from `stage == "rpe_below_threshold"` | `AuditResult` has `hook_stage`, no fallback field |
| RPE Judge | 16-feature LR: 6 global + 10 replay one-hot | `cmd_audit/hook/rpe_judge.py` |
| top-k selection | Fixed integer `TOP_K`, not top-p nucleus | `constants.TOP_K`, `_effective_top_k()` |
| Empty context split | Online uses sentinel scores; offline computes real RPE scores | `post_retrieve_hook(..., mode="online"|"offline")` |
| PrefixGuard cleanup | Keep only `empty_ctx`; fold `near_duplicate` and `low_count` into RPE features | No root PrefixGuard gate remains |
| Per-agent thresholds | Deferred to V2 | `adapter_name` accepted but informational |
| Calibration | Runtime hook reads constants only; fitting constants is supplementary | `scripts/calibrate_hook.py`, issue 0028 |

## Runtime Modules

### `cmd_audit/hook/constants.py`

Owns deployment constants read by online inference:

- `V1_REPLAY_NAME_ORDER`: canonical 10-replay order.
- `TOP_K`: number of replays selected by Stage 2.
- `FALLBACK_THRESHOLD`: minimum max p-score needed to trigger CMD.
- `RPE_JUDGE_WEIGHTS`: 16 LR coefficients.
- `RPE_JUDGE_INTERCEPT`: LR intercept.

The checked-in defaults are placeholders before calibration. Online inference uses only these constants and does not call an LLM.

### `cmd_audit/hook/rpe_judge.py`

Owns feature extraction and scoring:

- `compute_global_features(query, retrieved_items) -> tuple[float, ...]`
- `compute_replay_type_one_hot(replay_name) -> tuple[float, ...]`
- `extract_features(query, retrieved_items, replay_name) -> tuple[float, ...]`
- `score_replays(query, retrieved_items) -> tuple[ReplayScore, ...]`
- `rank_scores(scores) -> tuple[ReplayScore, ...]`

The 6 global features are:

| Feature | Meaning |
| --- | --- |
| `bm25_max` | strongest lexical query-memory match |
| `bm25_mean` | average lexical match across retrieved items |
| `bm25_std` | spread of lexical match scores |
| `item_count` | `min(len(items), 10) / 10.0` |
| `near_duplicate` | max pairwise Jaccard similarity |
| `low_count` | `1.0 if len(items) < 2 else 0.0` |

The remaining 10 features are replay-type one-hot features in `V1_REPLAY_NAME_ORDER`.

### `cmd_audit/hook/post_retrieve_hook.py`

Owns the Pre-CMD Hook decision:

- `ReplayScore`
- `PreCmdDecision`
- `post_retrieve_hook(query, retrieved_items, *, adapter_name="", mode="online")`

Stage behavior:

```text
if len(retrieved_items) == 0:
  stage = "empty_ctx"
  trigger_cmd = True
  selected_replays = all 10
  per_replay_scores =
    online: sentinel p_score=-1.0, selected=True
    offline: real RPE scores, selected=True

else:
  scores = score_replays(query, retrieved_items)
  if max(scores.p_score) >= FALLBACK_THRESHOLD:
    stage = "rpe_top_k"
    trigger_cmd = True
    selected_replays = top TOP_K by (-p_score, replay order)
  else:
    stage = "rpe_below_threshold"
    trigger_cmd = False
    selected_replays = ()
```

### `cmd_audit/hook/__init__.py`

Public package API. Re-exports `PreCmdDecision`, `ReplayScore`, constants, and RPE Judge helpers.

## Data Contracts

### `RetrievedItem`

Defined in `cmd_audit/models.py`. It intentionally stays minimal:

```python
@dataclass(frozen=True)
class RetrievedItem:
    memory_id: str
    text: str
```

No `store`, `graph`, or `safety` metadata is required by the hook. This avoids train/serve skew and keeps the hook adapter-neutral.

### `ReplayScore`

```python
@dataclass(frozen=True)
class ReplayScore:
    replay_name: str
    p_score: float
    selected: bool
    is_sentinel: bool = False
```

Invariants:

- `is_sentinel=True` implies `p_score == -1.0`.
- `p_score == -1.0` is reserved for sentinel scores.
- Non-sentinel scores must be in `[0.0, 1.0]`.
- Sentinel scores occur only on the online `empty_ctx` path.

### `PreCmdDecision`

```python
@dataclass(frozen=True)
class PreCmdDecision:
    trigger_cmd: bool
    stage: str
    per_replay_scores: tuple[ReplayScore, ...]
    selected_replays: tuple[str, ...]
```

Invariants:

- `stage` is one of `empty_ctx`, `rpe_top_k`, `rpe_below_threshold`.
- `per_replay_scores` always contains all 10 V1 replay names.
- `selected_replays` is all 10 for `empty_ctx`, top-k for `rpe_top_k`, and empty for `rpe_below_threshold`.
- There is no `fallback_triggered` field.

### `AuditResult` Hook Fields

`cmd_audit/harness.py` stores hook output on `AuditResult`:

- `hook_stage`
- `selected_replays`
- `per_replay_scores`

When the hook skips, `AuditResult.attribution` is `None` and `AuditResult.replays` is empty.

## Caller Map

### `run_case_v1_with_hook`

Primary runtime entry point:

```text
run_case_v1_with_hook(case)
  -> build RetrievedItem tuple from case.primary_baseline.retrieved_memory_ids
  -> post_retrieve_hook(case.query, retrieved_items, mode=mode)
  -> if trigger_cmd=False: return skipped AuditResult
  -> run_v1_replay_portfolio_subset(case, decision.selected_replays)
  -> assign_attribution_v1(...)
  -> return AuditResult with hook fields
```

It accepts optional `scorer` and `agent_generate` so the downstream attribution loop can run with real subagent scoring. That belongs to the **CMD Diagnosis Layer**, not to the hook itself.

### `run_case_v1_with_hook_and_repair`

Post-gate repair path:

```text
run_case_v1_with_hook_and_repair
  -> run_case_v1_with_hook
  -> if attribution is None: no repair
  -> RepairOrchestrator
```

The hook decides whether repair is even reachable; it does not build ECS or RepairAction output.

### `run_full_real_suite`

Batch path for real-data artifacts:

- `use_hook=True`: run `run_cases_v1_with_hook`.
- `use_hook=False`: bypass the hook and run all 10 replays. This is the Decision 34 headline-experiment path.

### CLI

`cmd_audit/cli.py` exposes:

- `--use-hook`: default.
- `--no-hook`: bypass hook.
- `--no-prefilter`: deprecated alias for `--no-hook`.

## Calibration Path

Runtime 0021 does not require calibration-time LLM calls. It reads `cmd_audit/hook/constants.py`.

`scripts/calibrate_hook.py` owns calibration:

1. Build training rows: 596 cases × 10 replays.
2. Label each row as `recovery_gain > 0`.
3. Fit 16-feature logistic regression.
4. Grid-search `TOP_K ∈ {2,3,4,5}` and `FALLBACK_THRESHOLD ∈ [0.0, 1.0]` in `0.05` increments.
5. Write constants and calibration artifacts.

Two label sources exist:

| Mode | Source | Artifact |
| --- | --- | --- |
| Legacy/offline | `labeler=phrase` or `labeler=subagent` inside calibration script | `training_set_subagent.npz` or dry-run equivalent |
| Decision 34 / issue 0028 | `--retest-csv artifacts/at_scale_llm_retest.csv` | `training_set_llm.npz` |

Issue 0028 is the preferred paper path. It consumes at-scale LLM retest outputs, avoids a second LLM labeling pass, and reports hook efficacy as supplementary.

Calibration outputs:

- `artifacts/hook_calibration/training_set_llm.npz`
- `artifacts/hook_calibration/threshold_grid.csv`
- `artifacts/hook_calibration/surrogate_gap.csv`
- `artifacts/hook_calibration/calibration_report.md`
- updated `cmd_audit/hook/constants.py`

## Removed Legacy Surface

Issue 0021 replaces the Decision 31 hook stack:

- root `cmd_audit/post_retrieve_hook.py`
- root `cmd_audit/prefix_guard.py`
- root `cmd_audit/rpe_prefilter.py`
- root `cmd_audit/replay_ordering.py`
- root `cmd_audit/hook_constants.py`
- `run_case_v1_with_prefilter`
- `run_cases_v1_with_prefilter`

Current hook code lives under `cmd_audit/hook/`.

## Tests

Primary tests:

- `tests/test_cmd_audit_issue21_hook_redesign.py`
- `tests/test_cmd_audit_decision34_calibrate_hook_retest.py`
- `tests/test_cmd_audit_issue20_f_precmd_signals.py`
- `tests/test_cmd_audit_issue20_c_hook_repair_integration.py`
- `tests/test_cmd_audit_issue17_provenance.py`

Coverage map:

| Area | Tests |
| --- | --- |
| Empty context hard trigger | online sentinel, offline real RPE, all replays selected |
| Feature extraction | 6 global, 10 one-hot, 16 total, item cap, duplicate score, low count |
| RPE scoring | sigmoid bounds, top-k, deterministic tie break, top-k > portfolio |
| Stage decisions | `empty_ctx`, `rpe_top_k`, `rpe_below_threshold` |
| Data shape | 10 `ReplayScore` entries, sentinel invariants, no `fallback_triggered` |
| Harness integration | hook fields on `AuditResult`, skip path, subset replay path |
| Calibration from retest | build training set from CSV, threshold grid over retest labels |
| Provenance compatibility | hook subset does not break replay provenance edges |

Suggested verification:

```bash
pytest -q tests/test_cmd_audit_issue21_hook_redesign.py \
  tests/test_cmd_audit_decision34_calibrate_hook_retest.py \
  tests/test_cmd_audit_issue20_f_precmd_signals.py \
  tests/test_cmd_audit_issue20_c_hook_repair_integration.py
```

## Reader Pitfalls

1. **Hook stage is not attribution.** `rpe_top_k` means CMD should run selected replays; it does not mean the failure is an RPE failure.
2. **RPE Judge is not an LLM judge.** Online RPE inference is a sigmoid over constants. LLM/subagent work only appears in offline calibration labels or issue 0028 retest outputs.
3. **`empty_ctx` is a hard trigger.** It intentionally bypasses RPE inference in online mode and uses sentinel scores.
4. **`fallback_threshold` is represented by stage, not a boolean field.** Use `hook_stage == "rpe_below_threshold"`.
5. **`adapter_name` is currently informational.** Per-agent thresholds are a V2 topic.
6. **Hook bypass is expected.** Decision 34 allows all-10 replay runs for headline attribution.

## Known Limits

- Checked-in LR weights are placeholders until supplementary calibration writes constants.
- Current hook features are lexical/statistical only. No embedding model or online LLM is used.
- Per-agent calibration is deferred to V2.
- Hook efficacy is supplementary. The core CMD paper claim still comes from counterfactual replay attribution, not from the hook.
- `surrogate_gap.csv` remains a calibration/reporting artifact, not an online training dependency.

## Related Documents

- `cmd_innovation_core/issues/0021-hook-redesign-three-stage-rpe-judge.md`
- `cmd_innovation_core/issues/0028-hook-calibration-supplementary.md`
- `cmd_innovation_core/issues/0020-C-run-case-v1-with-hook.md`
- `cmd_innovation_core/issues/0020-F-pre-cmd-decision-to-audit-result.md`
- `cmd_innovation_core/plans/cmd_open_decisions.md` Decision 33 and Decision 34 R5
