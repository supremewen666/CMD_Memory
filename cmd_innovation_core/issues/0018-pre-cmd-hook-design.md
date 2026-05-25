# 0018: Pre-CMD Hook — Zero-Gold Online Memory Gate Design

**Status**: superseded-by-0021
**Date**: 2026-05-21
**Superseded by**: Issue 0021 (Hook 重设计 — 两阶段 + RPE Judge per-replay 排序, Decision 33, 2026-05-23)
**Decision**: 31 (superseded for hook internals; FM retrieval timing point 7 still valid)
**0021 PR2 cleanup**: legacy `prefix_guard.py`, `rpe_prefilter.py`, `replay_ordering.py`, `hook_constants.py`, and root `post_retrieve_hook.py` removed; current implementation is `cmd_audit/hook/`.

## Overview

A single `post_retrieve_hook` function that gates CMD counterfactual replay. Online: zero gold dependency, purely deterministic/statistical rules. Offline: 596 cases calibrate weights and thresholds from gold data, then extract constants for online deployment.

```
Online (deployment)                    Offline (calibration, once)
─────────────────────                  ──────────────────────────
query, memory_items                   596 cases × full CMD replay
  ↓                                     ↓
post_retrieve_hook                     gold labels (Recovery Gain > 0)
  ↓                                     ↓
trigger_cmd?                           logistic regression + grid search
  ↓                                     ↓
YES → CMD 10-replay                    constants: weights, thresholds
NO  → skip
```

## Architecture

Single hook point, not a multi-event ECC system. Rationale: `post_retrieve` is the last gate before memory enters the agent — it catches downstream symptoms of all upstream errors. Other hook points add cost without proportionate diagnostic gain for online deployment.

```
PrefixGuard (deterministic)  +  RPE (statistical)
        ↓                            ↓
  anomaly_score                  surprise_score
        ↓                            ↓
        any signal > threshold → trigger_cmd = True
        all clean              → trigger_cmd = False
```

## Online Signal Design

### PrefixGuard: Four Deterministic Signals

All four signals are computable from `(query, memory_items)` alone — zero gold, zero models.

| Signal | Detection | Type | Weight (learned offline) |
|--------|-----------|------|--------------------------|
| `empty_ctx` | `len(memory_items) == 0` | binary | w1 |
| `truncation` | pattern match `"[...]"`, `"[blocked]"`, `"[filtered]"`, `"...truncated"` in any item.text | [0,1] — fraction of items flagged | w2 |
| `near_duplicate` | `max(BM25(a.text, b.text))` across all item pairs > 0.9 | [0,1] — max pairwise score | w3 |
| `low_count` | `len(memory_items) < 2` | binary | w4 |

```
anomaly_score = w1*empty_ctx + w2*truncation + w3*near_duplicate + w4*low_count
```

### RPE: Surprise × Utility

```
surprise = 1.0 - max(BM25(query, item.text) for item in memory_items)
utility  = 1.0 - agent_confidence          # default 0.5 when unavailable
rpe      = surprise * utility
trigger  = rpe > rpe_threshold or utility > utility_override_threshold
```

- BM25: lexical retrieval score (already in `retrieval_baselines.py`). No embedding model.
- `agent_confidence`: optional, from adapter. When unavailable, utility defaults to 0.5.
- Four gating branches (priority order, inherited from current RPE design):
  1. `utility > utility_override_threshold` → trigger (answer confidence critically low)
  2. `rpe > rpe_threshold` → trigger (evidence-surprise signal)
  3. `anomaly_score > anomaly_threshold` → trigger (PrefixGuard structural anomaly)
  4. Otherwise → skip

## Offline Calibration (Six Steps)

### Step 1: Generate Training Labels

Run full 10-replay CMD on all 596 cases:

```
for case in all_596_cases:
    results = run_v1_replay_portfolio(case)
    label[case] = any(r.recovery_gain > 0 for r in results)
```

Output: binary label per case (CMD found an error / did not).

### Step 2: Extract Online Signals

For each case, compute signals using only online-available data:

| Signal | Computation | Type |
|--------|-------------|------|
| `empty_ctx` | `len(baseline.retrieved_memory_ids) == 0` | binary |
| `truncation_score` | fraction of retrieved items with truncation markers in text | [0,1] |
| `duplicate_score` | max pairwise BM25 between retrieved item texts | [0,1] |
| `low_count` | `len(retrieved_memory_ids) < 2` | binary |
| `bm25_max` | `max(BM25(query, item.text))` across all retrieved items | [0,1] |
| `bm25_mean` | `mean(BM25(query, item.text))` across all retrieved items | [0,1] |
| `utility` | `1.0 - answer_score` (gold proxy during calibration) | [0,1] |

Output: N × 7 feature matrix.

### Step 3: PrefixGuard Weight Learning

Logistic regression on 4 structural signals → interpretable weights:

```python
from sklearn.linear_model import LogisticRegression
X = features[['empty_ctx', 'truncation_score', 'duplicate_score', 'low_count']]
y = labels
model = LogisticRegression(class_weight='balanced').fit(X, y)
w1, w2, w3, w4 = model.coef_[0]  # learned weights
anomaly_threshold = 0.5
```

Output: `PREFIXGUARD_WEIGHTS = (w1, w2, w3, w4)`, `ANOMALY_THRESHOLD`.

### Step 4: RPE Threshold Calibration

```python
surprise = 1.0 - features['bm25_max']
utility = features['utility']
rpe = surprise * utility

# Grid search for best threshold
best_f2 = 0
best_t = 0.3
for t in [0.0, 0.01, ..., 1.0]:
    predict = (rpe > t) | (anomaly_score > anomaly_threshold)
    f2 = fbeta_score(y, predict, beta=2)  # recall-weighted
    if f2 > best_f2:
        best_f2, best_t = f2, t
```

Output: `RPE_THRESHOLD`, `UTILITY_OVERRIDE_THRESHOLD`.

### Step 5: Joint Calibration

PrefixGuard and RPE are combined via OR logic. Grid search over both thresholds:

```python
for anomaly_t, rpe_t, utility_t in product(anomaly_range, rpe_range, utility_range):
    trigger = (anomaly_score > anomaly_t) | (rpe > rpe_t) | (utility > utility_t)
    score = fbeta_score(y, trigger, beta=2)
```

Output: joint optimal threshold triplet.

### Step 6: Validation & Hardening

- **Hold-out**: 80/20 split, report F2, recall, precision on hold-out
- **Baselines**: compare against always_trigger, never_trigger, random
- **Hardening**: write learned weights and thresholds as module-level constants
- **Perturbation label analysis** (optional): verify signal→label correspondence (e.g., empty_ctx → retrieval_error)

## Online Interface

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class PreCmdDecision:
    trigger_cmd: bool
    reason: str              # "clean" | "empty_context" | "truncation" |
                             # "near_duplicate" | "low_count" | "bm25_mismatch"
    anomaly_score: float     # PrefixGuard weighted score [0, 1]
    surprise_score: float    # RPE surprise [0, 1]
    rpe: float              # Surprise × Utility [0, 1]
    selected_replays: tuple[str, ...]


def post_retrieve_hook(
    query: str,
    memory_items: tuple[MemoryItem, ...],
    *,
    adapter_name: str = "",
    agent_confidence: float | None = None,
    top_k: int | None = None,
) -> PreCmdDecision:
    """Zero-gold online gate for CMD counterfactual replay.

    Args:
        query: The user query or task that triggered memory retrieval.
        memory_items: Retrieved memory items (post_retrieve state).
        adapter_name: For per-adapter logging, empty string for standalone.
        agent_confidence: Optional agent confidence score [0, 1].
        top_k: Max replays to suggest when triggered. None = all 10.

    Returns:
        PreCmdDecision with trigger_cmd and diagnostic signals.
    """
```

### Module Placement

- `cmd_audit/post_retrieve_hook.py` — `PreCmdDecision` dataclass + `post_retrieve_hook` function
- Uses `cmd_audit/retrieval_baselines.py` for BM25 `compute_bm25_scores`
- Uses `cmd_audit/prefix_guard.py` constants (not `run_prefix_guard` — that takes gold)
- Uses `cmd_audit/rpe_prefilter.py` constants (thresholds, replay order — not the gold-dependent functions)

## Integration

New entry point `run_case_v1_with_hook` in `harness.py`, parallel to `run_case_v1_with_prefilter`:

```
run_case_v1_with_prefilter  →  offline research mode (gold PrefixGuard + RPE)
run_case_v1_with_hook       →  online deployment mode (zero-gold hook)
```

Both paths share downstream: `run_v1_replay_portfolio_subset` → `assign_attribution_v1` → ECS → Post-Repair.

```
run_case_v1_with_hook:
  memory_items = extract_from_case(case)
  decision = post_retrieve_hook(case.query, memory_items, ...)
  if decision.trigger_cmd:
      replays = run_v1_replay_portfolio_subset(case, decision.selected_replays)
      return attribution, ecs, ...
  else:
      return AuditResult(attribution=None, skipped_by_hook=True, ...)
```

## Key Design Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Single `post_retrieve` hook, not 11 events | Other hook points add cost without proportionate gain for online |
| 2 | No ECC register/fire pattern | Single-hook architecture doesn't need composability |
| 3 | Online: zero gold, deterministic/statistical only | Information-theoretic bound — gold unavailable online |
| 4 | Offline: calibrate from 596 cases, extract constants | PrefixGuard paper approach: train with rich signals, deploy with DFA-like rules |
| 5 | Trigger → all 10 replays, no online ranking | Prefilter's job is gating; CMD's job is attribution. Cost saving is from skipping clean cases |
| 6 | top_k=None (all replays) default | False positive cost < false negative cost. Deployer can override for budget |
| 7 | F_beta=2 (recall-priority) | Missing a real error is worse than running unnecessary replays |
| 8 | BM25, no embedding model online | PrefixGuard + D-MEM both use lightweight signals at deployment |
| 9 | Per-adapter via `adapter_name` parameter | Logging/tracing, not separate gate instances |
| 10 | utility offline proxy = `1.0 - answer_score` | Validates calibration pipeline; online uses agent_confidence when available |

## Gold Dependency Boundary

| Signal | Offline (calibration) | Online (deployment) |
|--------|----------------------|---------------------|
| `empty_ctx` | available | available (zero gold) |
| `truncation` | available | available (zero gold) |
| `near_duplicate` | available | available (zero gold) |
| `low_count` | available | available (zero gold) |
| `bm25_max` | available | available (zero gold) |
| `answer_score` | proxy for utility | NOT available |
| `agent_confidence` | not in smoke cases | from adapter |
| `gold_evidence` | for replay + label gen | NOT available |
| `perturbation_label` | for analysis | NOT available |

Offline calibration bridges the gap: learn from gold-rich environment, deploy with constants.

## New Module

| File | Content |
|------|---------|
| `cmd_audit/post_retrieve_hook.py` | `PreCmdDecision` dataclass, `post_retrieve_hook` function |

Uses (does not duplicate): `retrieval_baselines.py` (BM25), `prefix_guard.py` (constants), `rpe_prefilter.py` (constants, replay order).

## References

- PrefixGuard (2605.06455): StepView induction + supervised training → DFA extraction for deployment
- D-MEM (2603.14597): Critic Router self-supervised Surprise/Utility scoring, O(1) fast path
- trace-mem: Counterfactual admission gate, non-redundancy detection
