# Issue 0020-F: PreCmdDecision Signals → AuditResult

**Status**: design
**Date**: 2026-05-23
**Decision**: 32 point 11 (PreCmdDecision → AuditResult)
**Parent**: Decision 32 Post-Gate Pipeline
**Blocked by**: 0020-C (run_case_v1_with_hook)

## 目的

将 PreCmdDecision 的 hook 全量信号写入 AuditResult，确保 paper hook 有效性分析和线上调试可追溯。包括 `hook_stage`、`per_replay_scores`（10 个 p-score）、`selected_replays`。

## 源需求

| 来源 | 应用的需求 |
| --- | --- |
| `cmd_open_decisions.md` Decision 32 point 11 | Hook 全量信号写入 AuditResult：`hook_stage`（empty_ctx / rpe_top_k / rpe_below_threshold）、`per_replay_scores`（10 个 p-score，可追溯）、`selected_replays`。用于 paper hook 有效性分析和线上调试。 |
| `cmd_open_decisions.md` Decision 32 point 15 | 所有 PreCmdDecision signals 写入 AuditResult。 |
| `TASK.md` | Issue 0020-F — hook_stage + per_replay_scores + selected_replays 写入 AuditResult。依赖 C。 |

## 领域边界

Issue 0020-F 定义 AuditResult 的信号扩展字段和写入逻辑。它不定义 PreCmdDecision 数据模型（0021），不定义 hook pipeline 流程（0020-C）。

```text
PreCmdDecision（0021）
  → stage: "empty_ctx" | "rpe_top_k" | "rpe_below_threshold"
  → per_replay_scores: tuple[ReplayScore, ...]（10 个）
  → selected_replays: tuple[str, ...]
    ↓ 写入
AuditResult（扩展）
  → hook_stage: str
  → per_replay_scores: tuple
  → selected_replays: tuple[str, ...]
```

## 计划代码产出物

| 产出物 | 角色 |
| --- | --- |
| `cmd_audit/models.py` | `AuditResult` 新增 4 个 hook 信号字段 + 2 个在线标记字段 |
| `cmd_audit/hook/post_retrieve_hook.py` | `ReplayScore` 数据类（0021 拥有） |
| `cmd_audit/harness.py` | `run_case_v1_with_hook` 中决策→结果字段映射 |
| `cmd_audit/writers.py` | 新增 `write_hook_analysis_table` |
| `tests/test_cmd_audit_issue20_F_audit_result.py` | 测试文件 |

## 模块地图

| 模块 | Issue 0020-F 角色 |
| --- | --- |
| `cmd_audit/models.py` | `AuditResult` 新增 6 个字段（均有默认值） |
| `cmd_audit/hook/post_retrieve_hook.py` | `ReplayScore` 数据类（0021 拥有） |
| `cmd_audit/harness.py` | `run_case_v1_with_hook` 中 `decision → AuditResult` 映射 |
| `cmd_audit/writers.py` | `write_hook_analysis_table`——hook 分析 CSV 输出 |

## 数据流

### AuditResult 扩展字段

```python
@dataclass(frozen=True)
class AuditResult:
    # 现有字段（不变）
    case_id: str
    attribution: AttributionResult | None
    replays: tuple[ReplayResult, ...] = ()
    baseline_suite: object | None = None

    # 新字段（0020-F）
    hook_stage: str = ""                    # "empty_ctx" | "rpe_top_k" | "rpe_below_threshold"
    per_replay_scores: tuple = ()           # 10 个 ReplayScore 记录
    selected_replays: tuple[str, ...] = ()  # hook 选中的 replay 名称
```

### ReplayScore 数据类

```python
@dataclass(frozen=True)
class ReplayScore:
    """Per-replay score from RPE Judge stage."""
    replay_name: str           # oracle_write, oracle_compression, ...
    p_score: float             # sigmoid(w·x + b) ∈ [0, 1]
    selected: bool             # 是否被选入 top-k
    is_sentinel: bool = False
```

### 写入映射（harness.py）

```python
decision = post_retrieve_hook(query, retrieved_items)
...
return AuditResult(
    case_id=case.case_id,
    attribution=attribution,
    replays=replays,
    hook_stage=decision.stage,
    per_replay_scores=decision.per_replay_scores,
    selected_replays=decision.selected_replays,
)
```

### CSV 输出

```text
artifacts/sandbox/hook_analysis.csv
  columns: case_id, hook_stage, n_selected_replays, selected_replays,
           max_p_score, min_p_score, attribution_label,
           recovery_gain, recovered
  → 用于 paper hook 有效性分析（F2, recall, precision, 跳过率, cost saving）
```

## 函数级合约（计划）

### `AuditResult` 扩展

```python
@dataclass(frozen=True)
class AuditResult:
    # ... 现有字段 ...
    hook_stage: str = ""
    per_replay_scores: tuple = ()
    selected_replays: tuple[str, ...] = ()
```

所有新字段有默认值——向后兼容。

### `write_hook_analysis_table`

```python
def write_hook_analysis_table(
    results: tuple[AuditResult, ...],
    path: str,
) -> None:
    """写入 hook 分析 CSV——每 case 一行 hook 信号 + 下游结果。
    Columns: case_id, hook_stage, n_selected, selected_replays,
             max_p, min_p, attribution_label, recovery_gain, recovered.
    """
```

## 测试结构（计划）

| 测试类 | 验收标准 | 数量 | 覆盖内容 |
| --- | --- | --- | --- |
| `AuditResultHookFieldsTest` | 扩展字段 | ~6 | hook_stage 传递、per_replay_scores 传递、selected_replays 传递、默认值兼容、frozen 不变 |
| `ReplayScoreDataModelTest` | ReplayScore | ~4 | replay_name、p_score ∈ [0,1] 或 sentinel -1、selected 正确、is_sentinel 正确 |
| `SignalMappingTest` | 信号映射 | ~5 | empty_ctx/rpe_top_k/rpe_below_threshold stage 映射、skipped 时 hook_stage 仍记录 |
| `CSVOutputTest` | CSV 输出 | ~4 | hook_analysis.csv 存在、columns 正确、per-case 行数正确、pandas 可读 |
| `PaperAnalysisTest` | paper 分析 | ~5 | F2、recall、跳过率、cost saving、per-stage 统计 |

## 非回归分析

### 现有模块变更
- `models.py`: `AuditResult` 新增 6 字段（均有默认值）。
- `harness.py`: 现有入口不使用新字段（保持默认值）。
- `writers.py`: 新增 `write_hook_analysis_table`，不改变现有 writer。

### 向后兼容性
- 所有现有 `AuditResult(...)` 构造兼容。
- 现有 CSV（`attribution_table.csv`、`comparison_metrics.csv`）不变。
- `hook_analysis.csv` 是新增输出。
