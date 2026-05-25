# 0021: Hook 重设计 — 两阶段顺序门控 + RPE Judge per-replay top-k

**Status**: done
**Date**: 2026-05-22
**Updated**: 2026-05-23 (PrefixGuard 独立阶段移除)
**Updated**: 2026-05-23 (grilling session, 9 ambiguities resolved, build-detailed)
**Implemented**: 2026-05-23 (PR1 + PR2 complete: hook package, harness integration, tests, calibration script, legacy cleanup)
**Decision**: 33
**PR plan**: PR1 (this issue) → PR2 (issue 0021-cleanup)

## Overview

将 Pre-CMD Hook 从 Decision 31 的五分支并行 OR 结构重设计为 **两阶段顺序结构**:

```
retrieved items
  ↓
Stage 1: empty_ctx 硬短路
  ├─ len(items) == 0  → trigger_cmd=True, stage="empty_ctx",
  │                      selected_replays = 全 10 个
  └─ len(items) > 0   → Stage 2
       ↓
Stage 2: RPE Judge (16 维特征 → per-replay p-score → top-k)
  ├─ max(p) ≥ FALLBACK_THRESHOLD → trigger_cmd=True, stage="rpe_top_k",
  │                                 selected_replays = top-3 (TOP_K=3)
  └─ max(p) <  FALLBACK_THRESHOLD → trigger_cmd=False, stage="rpe_below_threshold",
                                     selected_replays = ()
```

**核心变化**:
- RPE 从二进制全局门控变为 **per-replay 排序模型** (D-MEM Critic Router 模式)
- PrefixGuard 独立阶段移除 (truncation 11 pattern 线上召回率为零)
- top-k 取代 top-p (固定整数 count,非 nucleus 累积概率)

## 决议自 grilling session (2026-05-23)

| # | 区域 | 决议 |
|---|------|------|
| 1 | `stage` 字段 | 三值 `"empty_ctx" \| "rpe_top_k" \| "rpe_below_threshold"`;无 `fallback_triggered` |
| 2 | top-p / top-k | **整数** `TOP_K=3`;术语全局替换 top-p → top-k |
| 3 | empty_ctx + per_replay_scores | online mode sentinel (`is_sentinel=True, p_score=-1.0`);offline mode 跑真实 RPE Judge (paper 审计) |
| 4 | 16 维特征 | 6 全局 + 10 replay_type one-hot;删 safety_filter_blocked / is_graph_expanded / store_count;`item_count` cap+归一为 `min(x,10)/10` |
| 5 | 训练 label | **SubagentScorer** (qwen2.5-7b ollama,offline LLM);cache + 失败 fallback to phrase-match;持久化 `artifacts/hook_calibration/training_set_subagent.npz` |
| 6 | 阈值校准 | **全局**阈值 (per-agent 推迟 V2);596 → train 546 + hold-out 50;grid `TOP_K∈{2,3,4,5} × FALLBACK_THRESHOLD∈[0,1] step 0.05`;F2 |
| 7 | 旧模块清理 | 两 PR:PR1 (本 issue) = 新增 + 测试精简 (删 issue18 全文件 + 部分 issue16);PR2 (issue 0021-cleanup) = 纯删除 5 个旧模块 |
| 8 | `replay_ordering.py` | 直接删,不留 oracle topline baseline;hook baseline 限定 always-trigger / never-trigger / random |
| 9 | `run_case_v1_with_prefilter` | PR2 整体删除函数 + wrapper + 导出;CLI line 604 切到 `run_cases_v1_with_hook`;`--no-prefilter` alias 为 `--no-hook` |

## 数据契约

### `ReplayScore` (新)

```python
@dataclass(frozen=True)
class ReplayScore:
    replay_name: str      # 来自 V1_REPLAY_NAME_ORDER
    p_score: float        # [0,1] 真实 LR 输出 / -1.0 sentinel (online empty_ctx)
    selected: bool        # 是否入选 top-k (empty_ctx 路径全 True)
    is_sentinel: bool = False   # True ⇔ online mode 下 empty_ctx 路径未计算 RPE Judge
```

约定:
- `is_sentinel=True ⇒ p_score == -1.0`,反之亦然 (单元测试 invariant)
- `is_sentinel` 仅在 `mode="online"` 且 `stage="empty_ctx"` 时为 `True`
- `p_score` 真实值范围 `[0.0, 1.0]`,sentinel 用 `-1.0` 与真实值显式分离

### `PreCmdDecision` (重写)

```python
@dataclass(frozen=True)
class PreCmdDecision:
    trigger_cmd: bool
    stage: str                                    # "empty_ctx" | "rpe_top_k" | "rpe_below_threshold"
    per_replay_scores: tuple[ReplayScore, ...]    # 永远 10 元 (V1_REPLAY_NAME_ORDER 顺序)
    selected_replays: tuple[str, ...]             # empty_ctx: 全 10; rpe_top_k: top-3; rpe_below: ()
```

与 Decision 31 `PreCmdDecision` 的字段差异:
- **删除**: `reason`, `reason_codes`, `anomaly_score`, `surprise_score`, `utility_score`, `rpe`
- **新增**: `stage`, `per_replay_scores`
- **修改**: `selected_replays` 语义从"全 10 或空"扩展为"全 10 / top-k / 空" 三态
- **不引入**: `fallback_triggered` (改由 `stage == "rpe_below_threshold"` 推导)

### Hook 接口

```python
def post_retrieve_hook(
    query: str,
    retrieved_items: tuple[RetrievedItem, ...],
    *,
    adapter_name: str = "",
    mode: str = "online",     # "online" | "offline"
) -> PreCmdDecision:
    """两阶段顺序门控。

    mode="online":  empty_ctx 路径不跑 RPE Judge,per_replay_scores 填 sentinel
    mode="offline": empty_ctx 路径仍跑 RPE Judge,per_replay_scores 填真实 LR 输出
                    (供 paper hook 有效性分析)
    """
```

**`mode` flag 切分点**:
- `run_case_v1_with_hook` 默认 `mode="online"` (生产路径)
- `scripts/calibrate_hook.py` 用 `mode="offline"` 收集论文 hook 数据
- 两条路径只在 Stage 1 empty_ctx 分支处理上有差异;Stage 2 完全相同

### `RetrievedItem` (不变)

`cmd_audit/models.py:69-80` 现有 schema 保持:
```python
@dataclass(frozen=True)
class RetrievedItem:
    memory_id: str
    text: str
```

不升级 schema 是 grilling 决议 #4 的直接结果 (砍掉 safety/graph/store metadata 信号)。

## RPE Judge 设计

### 16 维特征向量

**全局特征 (6 维,per-case,所有 replay 共享):**

| 特征 | 类型 | 计算 | 备注 |
|------|------|------|------|
| `bm25_max` | [0,1] | `max(BM25(query, item.text))` | 复用 `retrieval_baselines.compute_bm25_scores` |
| `bm25_mean` | [0,1] | `mean(BM25(query, item.text))` | 同上 |
| `bm25_std` | [0,1] | `std(BM25(query, item.text))` | items < 2 时填 0.0 |
| `item_count` | [0,1] | `min(len(items), 10) / 10.0` | cap+归一,LR 系数对应"item 数 0→10 边际贡献" |
| `near_duplicate` | [0,1] | `max pairwise Jaccard(token_sets)` | 复用 `prefix_guard._compute_near_duplicate` 逻辑 |
| `low_count` | {0,1} | `1.0 if len(items) < 2 else 0.0` | binary,跟 PrefixGuard 同义 |

**replay_type one-hot 特征 (10 维):**

`V1_REPLAY_NAME_ORDER` 序: `oracle_write`, `oracle_compression`, `verbatim_event_oracle`, `oracle_retrieval`, `injection_oracle`, `evidence_given_reasoning`, `oracle_route`, `oracle_granularity`, `graph_off`, `safety_off`

**特征向量构造:**
```python
def extract_features(query, retrieved_items, replay_type) -> tuple[float, ...]:
    global_feats = compute_global_features(query, retrieved_items)  # 6 维
    one_hot = compute_replay_type_one_hot(replay_type)              # 10 维
    return global_feats + one_hot                                    # 16 维
```

empty_ctx 路径下全局特征值 (offline mode, paper deliverable):
- `bm25_max=0.0`, `bm25_mean=0.0`, `bm25_std=0.0`, `item_count=0.0`, `near_duplicate=0.0`, `low_count=1.0`
- 16 维向量本质上由 `low_count=1` + replay_type one-hot + intercept 决定 LR 输出

### 推理 (Stage 2 RPE Judge)

```python
def score_replays(query, retrieved_items) -> tuple[ReplayScore, ...]:
    global_feats = compute_global_features(query, retrieved_items)  # 6 维,per-case 共享
    raw_scores: list[tuple[str, float]] = []
    for replay_name in V1_REPLAY_NAME_ORDER:
        x = global_feats + one_hot(replay_name)                     # 16 维
        logit = sum(w * f for w, f in zip(RPE_JUDGE_WEIGHTS, x)) + RPE_JUDGE_INTERCEPT
        p = 1.0 / (1.0 + math.exp(-logit))                          # sigmoid
        raw_scores.append((replay_name, p))

    # Sort by p desc, ties broken by V1_REPLAY_NAME_ORDER index (deterministic)
    name_to_index = {n: i for i, n in enumerate(V1_REPLAY_NAME_ORDER)}
    raw_scores.sort(key=lambda r: (-r[1], name_to_index[r[0]]))

    selected_set = {name for name, _ in raw_scores[:TOP_K]}
    return tuple(
        ReplayScore(
            replay_name=name,
            p_score=p,
            selected=(name in selected_set),
            is_sentinel=False,
        )
        for name, p in raw_scores
    )
```

**确定性约束**:
- 排序用 `(-p_score, V1_REPLAY_NAME_ORDER 索引)` 复合 key,同 p 时按 portfolio 顺序破平
- TOP_K > 10 退化为全选 (防御逻辑,V1 不会触发)

### 训练 (offline,issue 0021 PR1 实现)

详见 §"离线校准 Step 1"。

## 离线校准 (3 步)

### Step 1 — RPE Judge 权重训练 (subagent labels,offline LLM)

**数据**: 596 cases × 10 replays = 5960 个 `(features, label)` 对

**Train/hold-out split**:
- Train: 前 546 cases × 10 = 5460 对
- Hold-out: 后 50 cases × 10 = 500 对 (Step 2 + Step 3 共用,不参与 Step 1)

**Label 计算**:
```python
from cmd_audit.llm_client import LLMClient
from cmd_audit.llm_scoring import SubagentScorer

llm_client = LLMClient(provider="ollama", model="qwen2.5:7b", temperature=0)
scorer = SubagentScorer(llm_client=llm_client, max_workers=8)  # cache shared

train_cases = cases[:546]
training_rows: list[tuple[tuple[float, ...], int]] = []

for case in train_cases:
    replays = run_v1_replay_portfolio(case, scorer=scorer)  # subagent label
    for r in replays:
        x = extract_features(case.query, retrieved_items_from(case), r.replay_name)
        y = 1 if r.recovery_gain > 0.0 else 0
        training_rows.append((x, y))
```

**鲁棒性约束**:
- 单次 subagent call timeout/error → 该 (case, replay) fall back to phrase-match scorer (`scorer=None`),日志标注 `fallback=True` 计数
- **不可丢弃整个 case** (训练样本量已经小)
- 单一 SubagentScorer 实例跨 case 共享 (cache 命中)

**模型训练**:
```python
from sklearn.linear_model import LogisticRegression

X = np.array([row[0] for row in training_rows])  # (5460, 16)
y = np.array([row[1] for row in training_rows])  # (5460,)

model = LogisticRegression(
    class_weight='balanced',     # positive label 估计 30-40%,需 balance
    random_state=42,             # 可重现
    max_iter=1000,
)
model.fit(X, y)

RPE_JUDGE_WEIGHTS = tuple(model.coef_[0].tolist())  # 16 floats
RPE_JUDGE_INTERCEPT = float(model.intercept_[0])
```

**持久化**:
- `artifacts/hook_calibration/training_set_subagent.npz`: features matrix + labels (后续 Step 3 复用、paper ablation 复用)
- `artifacts/hook_calibration/calibration_report.md`: positive label 比例、subagent fallback 计数、模型 coefficients、训练耗时

**预算**:
- ~5460 subagent calls (cache 命中后 ~3000-4000 unique calls)
- ollama qwen2.5-7b 本地 + 8 workers ≈ 5-15 min
- LR 训练 < 1 sec

**Reproducibility for paper**:
- 固化 ollama version + qwen2.5:7b model hash + temperature=0 + subagent prompt 文本
- Paper appendix 列 calibration 配置

### Step 2 — Surrogate path 质量测量 (paper deliverable)

**目的**: 4 个 gold-dependent labels (write/compression/premature_extraction/injection) 在线走 BM25 success-trace surrogate 路径,offline 用 SubagentScorer 测量 surrogate vs gold recovery gain 的差距。**不训练,只测量。**

**数据**: 50 hold-out cases (与 Step 3 共享)

**输出**: `artifacts/hook_calibration/surrogate_gap.csv`,paper §"Self-Supervision Surrogate" gap 数据

(详细设计见 issue 0020-E → 已合并入此处)

### Step 3 — 全局阈值校准 (zero LLM grid search)

**目的**: 固定 Step 1 权重,选最佳 `(TOP_K, FALLBACK_THRESHOLD)`。

**数据**: 50 hold-out cases (跟 Step 2 共享)

**网格**:
| 参数 | 范围 | 步长 | 网格点 |
|------|------|------|--------|
| `TOP_K` | {2, 3, 4, 5} | discrete | 4 |
| `FALLBACK_THRESHOLD` | [0.0, 1.0] | 0.05 | 21 |
| 总计 | | | **84 点** |

**优化目标**: F2 (recall-priority,β=2),与 0018 Step 4 对齐

```python
weights = load_weights("step1.npz")
best_f2 = 0
best_config = None

for top_k in [2, 3, 4, 5]:
    for fallback_t in np.arange(0.0, 1.0 + 0.05, 0.05):
        predictions = []
        for case in hold_out_50:
            decision = post_retrieve_hook(
                case.query, retrieved_from(case),
                mode="offline",
            )
            # 用本轮 (top_k, fallback_t) 重新选 selected_replays
            ...
            predictions.append(decision.trigger_cmd)
        f2 = fbeta_score(y_hold_out, predictions, beta=2)
        if f2 > best_f2:
            best_f2 = f2
            best_config = (top_k, fallback_t)

TOP_K, FALLBACK_THRESHOLD = best_config
```

**输出**: `cmd_audit/hook/constants.py` 内 `TOP_K`、`FALLBACK_THRESHOLD` 常数被覆写

**Pre-calibration defaults** (placeholder,运行 calibrate 前):
```python
TOP_K: int = 3
FALLBACK_THRESHOLD: float = 0.35
```

**Per-agent 推迟 V2** (grilling 决议 #6):
- V1 用全局阈值跨 mem0 / Letta / future adapter 共用
- V2 引入更多 adapter 与真实使用数据后再做 per-adapter calibration

## 模块布局 (PR1 新建)

```
cmd_audit/hook/
├── __init__.py                    # 公共导出
│   from .post_retrieve_hook import PreCmdDecision, ReplayScore, post_retrieve_hook
│   from .rpe_judge import score_replays, extract_features, compute_global_features
│   from .constants import TOP_K, FALLBACK_THRESHOLD, RPE_JUDGE_WEIGHTS,
│                          RPE_JUDGE_INTERCEPT, V1_REPLAY_NAME_ORDER
│
├── post_retrieve_hook.py          # 两阶段调度 + mode flag
│   - PreCmdDecision dataclass
│   - ReplayScore dataclass
│   - post_retrieve_hook(query, items, *, adapter_name, mode) -> PreCmdDecision
│   - _stage1_empty_ctx(items, mode) -> PreCmdDecision | None
│   - _stage2_rpe_judge(query, items) -> PreCmdDecision
│   - _build_sentinel_scores() -> tuple[ReplayScore, ...]
│
├── rpe_judge.py                   # 16 维特征 + LR 推理
│   - compute_global_features(query, items) -> tuple[float, ...]  # 6 维
│   - compute_replay_type_one_hot(replay_name) -> tuple[float, ...]  # 10 维
│   - extract_features(query, items, replay_name) -> tuple[float, ...]  # 16 维
│   - score_replays(query, items) -> tuple[ReplayScore, ...]
│
└── constants.py                   # 离线校准产物 + portfolio order
    - V1_REPLAY_NAME_ORDER: tuple[str, ...]    # 10 个 replay 顺序
    - TOP_K: int = 3                            # 默认占位
    - FALLBACK_THRESHOLD: float = 0.35          # 默认占位
    - RPE_JUDGE_WEIGHTS: tuple[float, ...] = (...)  # 16 个 0.0 占位,calibrate 后覆写
    - RPE_JUDGE_INTERCEPT: float = 0.0          # 占位
```

## PR 切分

### PR1 — issue 0021 (completed)

**新增**:
- `cmd_audit/hook/__init__.py` + `post_retrieve_hook.py` + `rpe_judge.py` + `constants.py`
- `tests/test_cmd_audit_issue21_hook_redesign.py` (~30-40 tests)

**修改**:
- `cmd_audit/__init__.py`: 导出新 `cmd_audit.hook` 符号 (PreCmdDecision 替换为新版)
- `cmd_audit/harness.py`:
  - `run_case_v1_with_hook` 切到新 hook (line 419+)
  - `AuditResult.fallback_triggered` 字段保留 (PR2 删,防止 0020-F 下游 writer 同时炸)
  - `AuditResult.hook_stage` 接收新三值
- `scripts/calibrate_hook.py`: 重写为 3 步流程 (subagent label + 全局阈值)

**测试精简** (PR1 内):
- **删除** `tests/test_cmd_audit_issue18_pre_cmd_hook.py` (88 tests,整个被新 21 tests 等价覆盖)
- **部分保留** `tests/test_cmd_audit_issue16_rpe_prefilter.py`:
  - 删除测 `run_rpe_prefilter` zero-gold API 的 tests (被 21 替代)
  - 暂保留测 `run_case_v1_with_prefilter` 的 tests (PR2 与该函数同步删除)

**PR1 invariants (historical, removed by PR2)**:
- 5 个旧模块文件 (`prefix_guard.py` / `rpe_prefilter.py` / `replay_ordering.py` / `hook_constants.py` / `post_retrieve_hook.py`) **保留**,加 deprecation header
- 旧 `PreCmdDecision` 不再公开导出,但旧文件内部定义保留(兼容 `run_case_v1_with_prefilter` 仍用旧 hook 路径直到 PR2)
- 现有 `cmd_audit.PreCmdDecision` 公共导出指向新 `cmd_audit.hook.PreCmdDecision`
- `803 - 88 - K + ~35 ≈ 720-740` total tests,主线绿

**新增 0021 tests 覆盖范围**:
```
class TestEmptyCtxHardShortCircuit
    test_zero_items_triggers_empty_ctx
    test_empty_ctx_selected_replays_full_10
    test_online_mode_uses_sentinel_scores
    test_offline_mode_runs_rpe_judge

class TestRPEJudgeFeatureExtraction
    test_global_feature_count_is_6
    test_one_hot_count_is_10
    test_full_feature_count_is_16
    test_item_count_capped_and_normalized
    test_bm25_std_zero_when_single_item
    test_near_duplicate_uses_jaccard
    test_low_count_binary

class TestRPEJudgeInference
    test_sigmoid_output_in_unit_interval
    test_per_replay_scores_length_10
    test_sort_by_p_desc_with_deterministic_tiebreak
    test_top_k_selection
    test_top_k_gt_portfolio_size_returns_all

class TestStageDecisionLogic
    test_stage_empty_ctx_when_zero_items
    test_stage_rpe_top_k_when_max_p_above_threshold
    test_stage_rpe_below_threshold_when_max_p_below
    test_trigger_cmd_consistent_with_stage

class TestPreCmdDecisionShape
    test_per_replay_scores_always_10_elements
    test_selected_replays_size_matches_stage
    test_sentinel_p_score_is_negative_one
    test_sentinel_only_in_online_empty_ctx
    test_no_fallback_triggered_field

class TestRunCaseV1WithHook
    test_hook_stage_propagates_to_audit_result
    test_selected_replays_propagates
    test_clean_case_returns_no_attribution
    test_triggered_case_runs_subset_replays
    test_provenance_tracker_intact_under_hook

class TestOnlineOfflineModeSplit
    test_online_empty_ctx_sentinel_per_replay
    test_offline_empty_ctx_real_rpe_per_replay
    test_default_mode_is_online
```

### PR2 — issue 0021-cleanup (completed)

**删除**:
- `cmd_audit/post_retrieve_hook.py` (旧版,5 分支 OR)
- `cmd_audit/prefix_guard.py`
- `cmd_audit/rpe_prefilter.py`
- `cmd_audit/replay_ordering.py`
- `cmd_audit/hook_constants.py` (常数已迁入 `hook/constants.py`)
- `tests/test_cmd_audit_issue16_rpe_prefilter.py` 剩余部分

**修改**:
- `cmd_audit/harness.py`:
  - 删除 `run_case_v1_with_prefilter` + `run_cases_v1_with_prefilter`
  - line 604: `run_cases_v1_with_prefilter` → `run_cases_v1_with_hook`
  - CLI: `--use-prefilter` 重命名为 `--use-hook`,保留 `--no-prefilter` 作为 deprecated alias
  - 删除 `AuditResult.fallback_triggered` 字段 (改由 `hook_stage == "rpe_below_threshold"` 推导)
- `cmd_audit/__init__.py`: 删除 5 个 `run_*_with_prefilter` 导出 + 5 个旧 hook 模块导出
- `tests/test_cmd_audit_issue17_provenance.py:758-760`: `run_case_v1_with_prefilter` → `run_case_v1_with_hook` (provenance 集成测试)
- 7 个文档引用加 deprecation note (`cmd_innovation_core/issues/0017-*.md` 等)

## 依赖关系

```
0018 (Pre-CMD Hook, 当前实现) ✅
  └→ 0021 PR1 (Hook 重设计: 两阶段 + RPE judge + hook/ 子包)  — 本 issue
       ├→ 0020-C (run_case_v1_with_hook 集成) ✅ 已存在
       │    └→ 0020-F (PreCmdDecision → AuditResult) ✅ 已预埋字段
       └→ 0021 PR2 (旧模块清理 + run_case_v1_with_prefilter 删除)  — 后续 issue
```

0020-A/B/D/E/G/H 与 0021 并行,不互阻 (Decision 32 已实现)。

## 测试策略

详见 §"PR1 — issue 0021"·"新增 0021 tests 覆盖范围"。

## Paper deliverables (此 issue 输出)

1. **Hook effectiveness section data**:
   - 596 cases × 10 replays subagent-labeled training set (`artifacts/hook_calibration/training_set_subagent.npz`)
   - 16 维 LR coefficients + intercept (calibration_report.md)
   - 50 hold-out × 84 grid 点 F2/recall/precision (Step 3 输出)
   - empty_ctx vs RPE Judge 独立信号一致性分析 (offline mode per_replay_scores)

2. **Hook baselines** (限定为 grilling 决议 #8 后):
   - Always-trigger
   - Never-trigger
   - Random ordering (top-3 random selection)

3. **Surrogate gap data** (Step 2 → paper §"Self-Supervision Surrogate"):
   - 50 cases × 4 gold-dependent labels × (surrogate gain, gold gain) → gap distribution

## References

- D-MEM (2603.14597): Critic Router self-supervised Surprise/Utility scoring → RPE judge per-replay p 分模式
- PrefixGuard (2605.06455): offline training → DFA extraction → online constants 校准部署模式 (CMD 场景下仅 empty_ctx 适用硬触发)
- Decision 31 (original Pre-CMD Hook design) — superseded by Decision 33 for hook internals
- Decision 32 (Post-Gate Pipeline) — downstream CMD layer, not affected by this redesign
- Issue 0019 Phase B (Subagent Scoring) — provides `SubagentScorer` reused as Step 1 label scorer
- Issue 0020-C / 0020-F — hook integration into `run_case_v1_with_hook` and `AuditResult` (already shipped)
