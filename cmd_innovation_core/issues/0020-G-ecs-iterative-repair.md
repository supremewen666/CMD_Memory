# Issue 0020-G: ECS Iterative Repair — close_deltas Drive + draft_ecs_for_label

**Status**: design
**Date**: 2026-05-23
**Decision**: 32 point 4 (Iterative repair), point 12 (Attribution=None handling)
**Parent**: Decision 32 Post-Gate Pipeline
**Blocked by**: 0020-A (RepairExecutor + RepairOrchestrator)

## 目的

实现 `close_deltas` 计算逻辑——确定哪些 label 的 recovery_gain 超过阈值，驱动迭代修复循环。提供 `draft_ecs_for_label` 为每个 label 构建独立 ECS，支持 RepairOrchestrator 逐 label 循环。

## 源需求

| 来源 | 应用的需求 |
| --- | --- |
| `cmd_open_decisions.md` Decision 32 point 4 | `close_deltas` = 所有 `recovery_gain > threshold` 的 label（非固定 top-k）。阈值离线在 596 case 上校准 gain 分布下界。 |
| `cmd_open_decisions.md` Decision 32 point 12 | Attribution=None 三级处理：(a) RPE judge top-p 全零 → self-supervision path, (b) surrogate 也为零 → `AttributionFailed`, (c) surrogate 有 gain → 正常 ECS 流。 |
| `TASK.md` | Issue 0020-G — ECS iterative repair。close_deltas = gain>threshold 的 labels（离线校准阈值）。依赖 A。 |

## 领域边界

Issue 0020-G 定义 close_deltas 计算和 per-label ECS 构建。它不定义迭代循环逻辑（0020-A RepairOrchestrator），不定义 Failure Memory（0020-D），不定义离线校准本身（0021 Step 3）。

```text
attribution_result.replays（10 个 ReplayResult）
  → compute_close_deltas(replays, threshold)
    → 筛选 recovery_gain > threshold 的 replay
    → 返回 ((label, gain), ...) 按 gain 降序排列
  → draft_ecs_for_label(case, audit, label)
    → 构建 label-specific ECSDraft
    → 注入 fm_context（wrong_memory + original_evidence）
  → RepairOrchestrator.run(ecs, close_deltas, ...)
```

Issue 0020-G 拥有的内容：
- `compute_close_deltas` 函数（`cmd_audit/repair_executor.py`）
- `draft_ecs_for_label` 函数（`cmd_audit/post_repair.py` 新增）
- `GAIN_THRESHOLD` 常量（离线可覆盖）

## 计划代码产出物

| 产出物 | 角色 |
| --- | --- |
| `cmd_audit/repair_executor.py` | 新增 `compute_close_deltas(replays, threshold) -> tuple[tuple[str, float], ...]` |
| `cmd_audit/post_repair.py` | 新增 `draft_ecs_for_label(case, audit, label, fm_store=None) -> ECSDraft` |
| `tests/test_cmd_audit_issue20_G_ecs_iterative.py` | 测试文件 |

## 模块地图

| 模块 | Issue 0020-G 角色 |
| --- | --- |
| `cmd_audit/repair_executor.py` | 新增 `compute_close_deltas`——按 gain 阈值筛选 + 降序排列 |
| `cmd_audit/post_repair.py` | 新增 `draft_ecs_for_label`——per-label ECS 构建 + fm_context 注入 |
| `cmd_audit/failure_memory.py` | 复用 FM 检索以获取 `fm_context`（0020-D 升级后的三维 key） |
| `cmd_audit/models.py` | 复用 `ReplayResult`, `ECSDraft`, `AuditResult` |

## 调用图（计划）

```text
cmd_audit/repair_executor.py
  → compute_close_deltas(replays, threshold=GAIN_THRESHOLD)
    → 遍历 replays:
        → 如果 r.recovery_gain > threshold:
            → label = _v1_label_for_replay(r.replay_name, ...)
            → close_deltas.append((label, r.recovery_gain))
    → 按 gain 降序排列
    → 返回 tuple[tuple[str, float], ...]

cmd_audit/post_repair.py
  → draft_ecs_for_label(case, audit, label, *, fm_store=None)
    → 找到对应 replay（audit.replays 中 replay_name 匹配 label）
    → 检索 Failure Memory: fm_context = fm_store.query(label, query_keywords, memory_top_terms)
    → 构建 ECSDraft:
        - cause = replay.evidence_block 的描述
        - corrected_memory = replay.evidence_block  # 7/11 direct; 4/11 surrogate
        - cascade_candidates = ()  # V1 空（0020-H 预埋）
    → 返回 ECSDraft
```

## 数据流

### close_deltas 计算

```python
def compute_close_deltas(
    replays: tuple[ReplayResult, ...],
    *,
    threshold: float = 0.05,
    has_ingestion_trace: bool = True,
) -> tuple[tuple[str, float], ...]:
    """返回 gain > threshold 的 (label, gain) 对，降序排列。

    边角情况:
    - 无 replay 超过阈值: 返回 ()
    - recovery_gain 为负: 跳过
    - 多个 replay 有相同 label: 取 gain 最大的
    """
```

### draft_ecs_for_label

```python
def draft_ecs_for_label(
    case: ProbeCase,
    audit: AuditResult,
    label: str,
    *,
    fm_store: FailureMemoryStore | None = None,
) -> ECSDraft:
    """为指定 label 构建独立 ECS。

    1. 从 audit.replays 中找到对应 replay
    2. 构建 cause 描述（从 replay.evidence_block 提取）
    3. 确定 corrected_memory（7/11 direct, 4/11 surrogate）
    4. 如有 fm_store: 检索 fm_context（三维 key）
    5. 返回 ECSDraft(cause=..., corrected_memory=..., cascade_candidates=())
    """
```

### Gain 阈值离线校准

```text
在 596 case 上（0021 Step 3 的一部分）:
  1. 运行完整 10-replay CMD 归因
  2. 收集所有 (label, recovery_gain) 对
  3. 对每个 label 计算 gain 分布
  4. 联合 grid search: (gain_threshold × top_p × fallback_threshold)
  5. 优化 F2 recall-priority
  6. 产出: GAIN_THRESHOLD 常量
```

## 测试结构（计划）

| 测试类 | 验收标准 | 测试数量 | 覆盖内容 |
| --- | --- | --- |
| `CloseDeltasComputeTest` | close_deltas 计算 | ~8 | 全部超阈值、部分超阈值、全部低于阈值、负 gain 排除、降序排列、相同 label 取最大、空 replays、单 replay |
| `CloseDeltasThresholdTest` | 阈值行为 | ~5 | 默认阈值、threshold=0 全部通过、threshold=1.0 无通过、边界值、精度 |
| `DraftECSForLabelTest` | per-label ECS | ~7 | write_error/retrieval_error/injection_error ECS、7 direct label、4 surrogate label、fm_context 注入、fm_store=None |
| `FMContextInjectionTest` | fm_context 注入 | ~5 | 三维 key 检索、fm_context 内容正确（wrong+evidence）、无匹配时空 fm_context、fm_context ≠ corrected_memory |
| `IntegrationWithOrchestratorTest` | 集成 | ~4 | close_deltas 驱动迭代、单 label recover、多 label 迭代、exhaust 停止 |

## 非回归分析

### 现有模块变更
- `post_repair.py`: 新增 `draft_ecs_for_label`，不改动现有 `draft_ecs`。
- `repair_executor.py`: 新增 `compute_close_deltas`，全新模块。

### 向后兼容性
- 现有 `draft_ecs(case, audit)` 保持不变。
- `draft_ecs_for_label` 是增量函数。
- `ECSDraft.cascade_candidates` 字段（0020-H 预埋）默认为空元组。
