# Issue 0020-B: RepairAction + Adapter.apply_repair

**Status**: design
**Date**: 2026-05-23
**Decision**: 32 point 3 (RepairAction), point 1 (Adapter.apply_repair)
**Parent**: Decision 32 Post-Gate Pipeline
**Blocked by**: None — independent from other 0020 issues

## 目的

将 CMD 修复操作标准化为 LLM tool 定义（5 种 action_type），由 Adapter 声明支持能力，LLM 自主选择并填参执行。消除硬编码 `label → repair_action` 映射。

## 源需求

| 来源 | 应用的需求 |
| --- | --- |
| `cmd_open_decisions.md` Decision 32 point 3 | RepairAction 5 种类型：`append`, `replace`, `relocate`, `update_routing`, `update_template`。作为 LLM tool 定义传入，LLM 看到 `label + evidence_block + fm_context + adapter.supported_actions`，自主选择并填参。不硬编码 label→action 映射。 |
| `cmd_open_decisions.md` Decision 32 point 1 | Adapter 层：声明 `supported_actions: tuple[str, ...]`（mem0=`(append, replace)`，Letta=`(append, replace, relocate, update_routing)`），`apply_repair(RepairAction)` 执行。 |
| `TASK.md` | Issue 0020-B — RepairAction 作为 LLM tool 定义，adapter 声明 `supported_actions`，LLM 自主选择。 |

## 领域边界

Issue 0020-B 定义修复操作的数据模型和 Adapter 执行接口。它不定义修复流程（RepairExecutor/RepairOrchestrator，属于 issue 0020-A），不定义迭代逻辑（issue 0020-G），不定义 Failure Memory 存储（issue 0020-D）。

```text
RepairAction（数据模型，cmd_audit/models.py）
  → Adapter.supported_actions（声明能力）
  → Adapter.apply_repair(action)（执行修复）
  → LLM tool definition（context 传入，LLM 选 action 类型 + 填参）
```

Issue 0020-B 拥有的内容：
- `RepairAction` 数据类（`models.py` 新增）
- `Adapter.apply_repair` 抽象方法（`adapters/base.py`）
- `Adapter.supported_actions` 类属性（`adapters/base.py`）
- mem0 和 Letta adapter 的 `supported_actions` 声明和 `apply_repair` 实现
- `repairs.py` 中 `REPAIR_ACTION_BY_LABEL` 标记 deprecated

## 计划代码产出物

| 产出物 | 角色 |
| --- | --- |
| `cmd_audit/models.py` | 新增 `RepairAction` 冻结数据类 |
| `cmd_audit/adapters/base.py` | 新增 `Adapter.apply_repair` 抽象方法 + `supported_actions` 抽象属性 |
| `cmd_audit/adapters/mem0_adapter.py` | `supported_actions = ("append", "replace")` + `apply_repair` 实现 |
| `cmd_audit/adapters/letta_adapter.py` | `supported_actions = ("append", "replace", "relocate", "update_routing")` + `apply_repair` 实现 |
| `cmd_audit/repairs.py` | `REPAIR_ACTION_BY_LABEL` 标记 deprecated（保留向后兼容） |
| `cmd_audit/__init__.py` | 导出 `RepairAction` |
| `tests/test_cmd_audit_issue20_B_repair_action.py` | 测试文件 |

## 模块地图

| 模块 | Issue 0020-B 角色 |
| --- | --- |
| `cmd_audit/models.py` | 新增 `RepairAction` 数据类（5 种 action_type + target_item_id + target_store + content + label） |
| `cmd_audit/adapters/base.py` | 新增 `supported_actions: tuple[str, ...]` 抽象属性 + `apply_repair(self, action: RepairAction) -> None` 抽象方法 |
| `cmd_audit/adapters/mem0_adapter.py` | `supported_actions = ("append", "replace")`，`apply_repair` 调用 mem0 API add/update |
| `cmd_audit/adapters/letta_adapter.py` | `supported_actions = ("append", "replace", "relocate", "update_routing")`，`apply_repair` 调用 Letta API |
| `cmd_audit/repairs.py` | `REPAIR_ACTION_BY_LABEL` + `get_targeted_repair_action_v1` 标记 deprecated |

## 数据流

### RepairAction 数据类

```python
@dataclass(frozen=True)
class RepairAction:
    """LLM-selected repair operation, filled from tool definition."""
    action_type: str          # append | replace | relocate | update_routing | update_template
    label: str                # 诊断标签（如 retrieval_error）
    content: str              # 修复后的记忆内容
    target_store: str         # 目标 store（默认 "default"）
    target_item_id: str | None = None  # replace/relocate/update 时必填
```

### 5 种 action_type 语义

| Action Type | 含义 | target_item_id | 适配器行为 |
| --- | --- | --- | --- |
| `append` | 向 store 追加新记忆 | None | `adapter.add(content, store)` |
| `replace` | 替换已有记忆内容 | required | `adapter.update(item_id, content)` |
| `relocate` | 将记忆迁移到不同 store | required | `adapter.move(item_id, target_store)` + 可选 content 更新 |
| `update_routing` | 修改路由规则 | required | Letta 特定：更新路由表 |
| `update_template` | 修改记忆模板 | None | 更新写入/压缩模板参数 |

### JSON-only LLM Tool 输出格式

```python
REPAIR_ACTION_TOOL = {
    "type": "function",
    "function": {
        "name": "repair_memory",
        "description": "Execute a memory repair operation to fix a diagnosed error.",
        "parameters": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "enum": ["append", "replace", "relocate", "update_routing", "update_template"],
                },
                "label": {"type": "string"},
                "content": {"type": "string"},
                "target_store": {"type": "string"},
                "target_item_id": {"type": "string"},
            },
            "required": ["action_type", "label", "content", "target_store"]
        }
    }
}
```

LLM context 中展示 `adapter.supported_actions` 限制可选 action_type 枚举范围。

Subagent 输出必须是单个 JSON object，不允许 Markdown code fence、解释性 prose、多个对象或缺失字段。解析路径：

```text
raw LLM response
  → parse_repair_action_response(response, supported_actions, expected_label)
  → validate JSON-only
  → validate action_type ∈ adapter.supported_actions
  → validate label == current attribution label
  → RepairAction(...)
```

严格 real/subagent mode 下解析失败返回 `action_selection_failed`；无 LLM/offline mode 才允许 `_select_action_type` 启发式 fallback。

### Adapter 接口变更

```python
class Adapter(ABC):
    @property
    @abstractmethod
    def supported_actions(self) -> tuple[str, ...]:
        """Action types this agent's memory API supports."""
        ...

    @abstractmethod
    def apply_repair(self, action: RepairAction) -> None:
        """Execute a repair action against the agent's memory store.
        Raises SandboxViolationError if store state is mutated outside sandbox rules.
        """
        ...
```

## 调用图（计划）

```text
RepairExecutor（issue 0020-A）
  → LLM.generate(context_with_tool_definition)
    → LLM 选择 action_type（受 adapter.supported_actions 约束）
    → RepairAction(action_type=..., label=..., content=..., ...)
      → adapter.apply_repair(RepairAction)
        → mem0: mem0_client.add(content) or mem0_client.update(item_id, content)
        → Letta: letta_client.add(content) or letta_client.update(item_id, content)
                  or letta_client.move(item_id, target_store)
```

## 函数级合约（计划）

### `RepairAction` 数据类

```python
@dataclass(frozen=True)
class RepairAction:
    action_type: str          # 必填，必须是 adapter.supported_actions 中的值
    label: str                # 必填，11 个 pipeline label 之一
    content: str              # 必填，修复后的正确记忆内容（非空）
    target_store: str         # 必填，目标 store 名称（非空）
    target_item_id: str | None = None  # replace/relocate/update_routing 时必填
```

### `Adapter.supported_actions`

```python
@property
def supported_actions(self) -> tuple[str, ...]:
    """mem0: ("append", "replace")
       Letta: ("append", "replace", "relocate", "update_routing")
    """
```

### `Adapter.apply_repair`

```python
def apply_repair(self, action: RepairAction) -> None:
    """Execute repair. Validate action_type ∈ supported_actions first.
    Raise ValueError if action_type not supported.
    Raise SandboxViolationError if store state corrupted.
    """
```

## 测试结构（计划）

| 测试类 | 验收标准 | 测试数量 | 覆盖内容 |
| --- | --- | --- |
| `RepairActionDataModelTest` | 数据模型 | ~8 | 5 种 action_type 构造、target_item_id None/必填、frozen 不可变、content 非空校验 |
| `AdapterSupportedActionsTest` | Adapter 声明 | ~4 | mem0 声明、Letta 声明、supported_actions 为 tuple、不支持 action 抛错 |
| `RepairActionToolDefTest` | Tool 定义 | ~4 | tool 定义格式正确、enum 包含所有类型、required 字段列表正确 |
| `LLMSelectionTest` | LLM 选 action | ~6 | append 场景、replace 场景、relocate 场景、LLM context 含 supported_actions、action_type 受限、JSON-only parser |
| `AdapterApplyRepairTest` | 执行修复 | ~6 | mem0 apply_repair(append)、mem0 apply_repair(replace)、Letta apply_repair(relocate)、不支持的 action_type、SandboxViolationError、SHA-256 checksum |

## 非回归分析

### 现有模块不变
- `repairs.py` 中 `REPAIR_ACTION_BY_LABEL` 和 `get_targeted_repair_action_v1` 标记 deprecated（Decision 32 不再硬编码 label→action）。不删除，保留向后兼容。
- `models.py` 现有数据类（`ProbeCase`、`MemoryItem`、`BaselineOutput` 等）不变。
- `adapters/base.py` 现有 `intercept_write`/`intercept_search` 抽象方法不变。

### 向后兼容性
- `RepairAction` 是新增数据类，不改变现有数据类。
- Adapter 新方法是抽象新增，mem0 和 Letta adapter 需要实现，独立 harness 路径不受影响。
