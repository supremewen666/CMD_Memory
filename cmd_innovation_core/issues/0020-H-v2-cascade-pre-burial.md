# Issue 0020-H: V2 Cascade Pre-Burial — ECSDraft.cascade_candidates Field

**Status**: design
**Date**: 2026-05-23
**Decision**: 32 point 16 (V2 cascade pre-burial)
**Parent**: Decision 32 Post-Gate Pipeline
**Blocked by**: None — independent from other 0020 issues

## 目的

在 `ECSDraft` 中预埋 `cascade_candidates` 字段——V1 始终为空元组，V2 用于 LLM 从 provenance DAG 检索下游受影响记忆并自修改。纯预埋，无逻辑。

## 源需求

| 来源 | 应用的需求 |
| --- | --- |
| `cmd_open_decisions.md` Decision 32 point 16 | `ECSDraft.cascade_candidates` — provenance DAG 的下游 item IDs。V1 always empty。V2: LLM retrieves candidates and self-modifies (not algorithmic MemQ TD(lambda))。纯预埋，无逻辑。 |
| `TASK.md` | Issue 0020-H — V2 cascade pre-burial。独立。 |

## 领域边界

这是 8 个 issue 中最小的——仅在 `ECSDraft` 数据类中新增一个字段，V1 无任何填充逻辑。V2 时期该字段将被 `get_graph_distractor_edges`（`provenance.py`）的下游扩展填充。

```text
ECSDraft（cmd_audit/models.py）
  → cascade_candidates: tuple[str, ...] = ()
  → V1: 始终空元组
  → V2: downstream item IDs from provenance DAG 的图遍历结果
```

## 计划代码产出物

| 产出物 | 角色 |
| --- | --- |
| `cmd_audit/models.py` | `ECSDraft` 新增 `cascade_candidates: tuple[str, ...] = ()` |
| `cmd_audit/post_repair.py` | `draft_ecs` 和 `draft_ecs_for_label` 中显式设为 `()` |

## 模块地图

| 模块 | Issue 0020-H 角色 |
| --- | --- |
| `cmd_audit/models.py` | `ECSDraft` 新增 `cascade_candidates: tuple[str, ...] = ()` |
| `cmd_audit/post_repair.py` | `draft_ecs` 和 `draft_ecs_for_label` 返回时显式 `cascade_candidates=()` |
| `cmd_audit/provenance.py` | V2 时期：扩展 `get_graph_distractor_edges` 的下游遍历填充此字段 |

## 数据流

### ECSDraft 扩展

```python
@dataclass(frozen=True)
class ECSDraft:
    # 现有字段（不变）
    cause: str
    corrected_memory: str

    # V2 预埋（0020-H）
    cascade_candidates: tuple[str, ...] = ()
    # V1: 始终空元组
    # V2: 从 provenance DAG 获取的下游受影响 memory IDs
```

### V2 预期使用方式

```text
V2 cascade repair flow（post-paper）:
  1. ECSDraft.cascade_candidates 非空
  2. RepairExecutor 修复 primary label 后
  3. 遍历 cascade_candidates 中的每个 downstream item
  4. 为每个 downstream item 构建新 ECS
  5. 递归修复 → 级联修复链
  6. provenance DAG 保证可追溯性
```

## 函数级合约

```python
@dataclass(frozen=True)
class ECSDraft:
    cause: str
    corrected_memory: str
    cascade_candidates: tuple[str, ...] = ()
```

**draft_ecs / draft_ecs_for_label**:
```python
return ECSDraft(
    cause=...,
    corrected_memory=...,
    cascade_candidates=(),  # V1 显式空
)
```

## 测试结构

| 测试类 | 验收标准 | 数量 | 覆盖内容 |
| --- | --- | --- | --- |
| `ECSDraftCascadeFieldTest` | 字段存在 | ~3 | cascade_candidates 存在、默认为空、frozen |
| `DraftECSCascadeFieldTest` | draft_ecs 输出 | ~3 | draft_ecs/draft_ecs_for_label 含 cascade_candidates、值为 () |
| `BackwardCompatibilityTest` | 向后兼容 | ~3 | 旧 ECSDraft 构造不变、现有 ECS 测试不受影响 |

## 非回归分析

### 最小变更
- `ECSDraft` 新增一个具有默认值的字段——所有现有构造无需修改。
- `draft_ecs` 和 `draft_ecs_for_label` 中显式传递 `cascade_candidates=()` 明确 V1 语义。
- V2 不需要修改数据类，只需修改填充逻辑。
- 不在 2026-06-10 时间线内（post-paper V2 工作）。
