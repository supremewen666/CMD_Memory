# Issue 0020-A: RepairExecutor + RepairOrchestrator

**Status**: design
**Date**: 2026-05-23
**Decision**: 32 point 1 (Repair layering), point 2 (归因 context), point 4 (Iterative repair)
**Parent**: Decision 32 Post-Gate Pipeline
**Blocked by**: 0020-B (RepairAction + adapter.apply_repair)

## 目的

实现 4-tier repair layering 中的 RepairExecutor 和 RepairOrchestrator：

- **RepairExecutor**: stateless 单次修复——接收 ECS + label，调用 LLM 选择 RepairAction，通过 adapter 执行修复，构建完整 repair context，运行 Post-Repair Context Replay，返回 `recovered` / `partial` / `failed`。
- **RepairOrchestrator**: 当 RepairExecutor 返回 `partial` 时，取 `close_deltas` 中下一 label，构建新 ECS，循环至 `recovered` 或 exhaust。

## 源需求

| 来源 | 应用的需求 |
| --- | --- |
| `cmd_open_decisions.md` Decision 32 point 1 | RepairExecutor: `ECS → LLM 选 action → apply_repair → repaired_context（baseline + label + evidence_block + fm_context）→ Post-Repair → recovered/partial/failed`。RepairOrchestrator: partial → close_deltas next label → new ECS → loop。 |
| `cmd_open_decisions.md` Decision 32 point 2 | 归因 subagent loop context: baseline + label + evidence_block。fm_context 在 ECS 阶段注入，非 replay 前（因果纯度）。 |
| `cmd_open_decisions.md` Decision 32 point 4 | close_deltas = 所有 recovery_gain > threshold 的 label（非固定 top-k），阈值离线校准。 |
| `cmd_open_decisions.md` Decision 32 point 5 | 7/11 labels: replay.evidence_block 直接可用；4 gold-dependent labels: self-supervision surrogate。 |
| `TASK.md` | Issue 0020-A — RepairExecutor + RepairOrchestrator。依赖 B。 |

## 领域边界

Issue 0020-A 定义修复执行和编排逻辑。它不定义 RepairAction 数据模型（0020-B），不定义 close_deltas 计算（0020-G），不定义 Failure Memory 存储（0020-D），不定义 hook 集成（0020-C）。

```text
RepairOrchestrator（cmd_audit/repair_executor.py）
  → 接收: ECSDraft + close_deltas + adapter + agent_generate + scorer
  → 循环:
      RepairExecutor.run(ecs, label, adapter, agent_generate, scorer)
        → LLM.generate(context) → RepairAction
        → adapter.apply_repair(action)
        → build_repaired_context(baseline + label + evidence_block + fm_context)
        → run_post_repair_context_replay(repaired_context)
        → PostRepairResult(recovered/partial/failed)
      if partial → next close_deltas label → new ECS
      if recovered → stop
      if exhaust → stop
```

Issue 0020-A 拥有的内容：
- `RepairExecutor` 类（`cmd_audit/repair_executor.py` 新增）
- `RepairOrchestrator` 类（`cmd_audit/repair_executor.py` 新增）
- `build_repaired_context` 函数（`cmd_audit/post_repair.py` 新增）

## 计划代码产出物

| 产出物 | 角色 |
| --- | --- |
| `cmd_audit/repair_executor.py` | **新**。`RepairExecutor` + `RepairOrchestrator` 类 |
| `cmd_audit/post_repair.py` | 新增 `build_repaired_context(baseline, label, evidence_block, fm_context) -> RepairedContext` |
| `cmd_audit/__init__.py` | 导出 `RepairExecutor`, `RepairOrchestrator`, `RepairExecutorOutput` |
| `tests/test_cmd_audit_issue20_A_repair_executor.py` | 测试文件 |

## 模块地图

| 模块 | Issue 0020-A 角色 |
| --- | --- |
| `cmd_audit/repair_executor.py` | **新**。`RepairExecutor`（stateless 单次修复）+ `RepairOrchestrator`（迭代编排） |
| `cmd_audit/post_repair.py` | 新增 `build_repaired_context`——拼接 baseline + label + evidence_block + fm_context |
| `cmd_audit/models.py` | 复用 `ECSDraft`, `PostRepairResult`, `RepairAction`（0020-B） |
| `cmd_audit/replays.py` | 复用 `_score_recovered_evidence`（agent_generate 参数，Decision B） |
| `cmd_audit/adapters/base.py` | 复用 `Adapter.apply_repair`（0020-B） |

## 调用图（计划）

```text
cmd_audit/repair_executor.py
  → RepairOrchestrator.run(case, ecs, close_deltas, adapter, agent_generate, scorer, fm_context)
    → 对 close_deltas 中的每个 label（按 gain 降序）:
        → 如果 ecs 为空: 调用 draft_ecs(case, audit) 构建初始 ECS
        → RepairExecutor.run(case, ecs, label, adapter, agent_generate, scorer, fm_context)
            → 构建 LLM context:
                - baseline: case.primary_baseline.retrieved_items 原文
                - label: 当前修复标签
                - evidence_block: replay.evidence_block
                - fm_context: wrong_memory + original_evidence（ECS 阶段注入）
                - adapter.supported_actions: 可选 action 列表
                - RepairAction tool definition
            → LLM.generate(context + tool_def) → RepairAction
            → adapter.apply_repair(action)
            → build_repaired_context(
                baseline=case.primary_baseline.retrieved_items,
                label=label,
                evidence_block=replay.evidence_block,
                fm_context=fm_context
              )
            → run_post_repair_context_replay(case, repaired_context, agent_generate, scorer)
            → 返回 RepairExecutorOutput(result, action, repaired_context)
        → 如果 recovered: 返回 result
        → 如果 partial: 继续下一 label 循环
        → 如果 failed: 记录，继续下一 label
    → 所有 labels exhaust 且未 recovered: 返回最后一个 result
```

## 数据流

### RepairExecutor 输入输出

**输入**:
```python
@dataclass
class RepairExecutorInput:
    case: ProbeCase
    ecs: ECSDraft
    label: str                  # 当前修复标签
    replay: ReplayResult        # 对应 replay 结果（含 evidence_block）
    adapter: Adapter
    agent_generate: Callable[[str, str], str]  # (query, context) -> answer
    scorer: Callable             # (gold_evidence, agent_answer) -> float
    fm_context: str = ""        # wrong_memory + original_evidence
```

**输出**:
```python
@dataclass
class RepairExecutorOutput:
    result: PostRepairResult    # recovered / partial / failed
    action: RepairAction        # LLM 选择的修复操作
    repaired_context: RepairedContext  # 完整 repair context
```

### RepairedContext 字段扩展

现有 `RepairedContext`（`post_repair.py`）新增 4 个字段：

```python
@dataclass(frozen=True)
class RepairedContext:
    # 现有字段（不变）
    query: str
    corrected_memory: tuple

    # 新字段（0020-A）
    baseline_context: str = ""   # baseline retrieved_items 原文
    label: str = ""              # 修复标签
    evidence_block: str = ""     # 反事实证据块
    fm_context: str = ""         # wrong_memory + original_evidence（诊断信号）
```

### repair context 构建

```text
build_repaired_context(baseline, label, evidence_block, fm_context) → RepairedContext:
  1. corrected_memory = tuple(adapter.get_items(store))  # 修复后 store 状态
  2. baseline_context = "\n".join(item.text for item in baseline)
  3. return RepairedContext(
       query=case.query,
       corrected_memory=corrected_memory,
       baseline_context=baseline_context,
       label=label,
       evidence_block=evidence_block,
       fm_context=fm_context,
     )
```

## 函数级合约（计划）

### `RepairExecutor`

```python
class RepairExecutor:
    """Stateless single-repair executor. One ECS → one label → one repair."""

    @staticmethod
    def run(
        case: ProbeCase,
        ecs: ECSDraft,
        label: str,
        replay: ReplayResult,
        *,
        adapter: Adapter,
        agent_generate: Callable[[str, str], str],
        scorer: Callable,
        fm_context: str = "",
    ) -> RepairExecutorOutput:
        """1. Build LLM context (label + evidence_block + fm_context + supported_actions)
        2. LLM selects action_type and fills RepairAction params
        3. adapter.apply_repair(action)
        4. Build repaired_context
        5. Run Post-Repair Context Replay with agent_generate + scorer
        6. Return RepairExecutorOutput(result, action, repaired_context)
        """
```

### `RepairOrchestrator`

```python
class RepairOrchestrator:
    """Iterative repair orchestration over close_deltas labels."""

    @staticmethod
    def run(
        case: ProbeCase,
        audit: AuditResult,
        *,
        adapter: Adapter,
        agent_generate: Callable[[str, str], str],
        scorer: Callable,
        close_deltas: tuple[tuple[str, float], ...],
        gain_threshold: float = 0.0,
    ) -> RepairOrchestratorOutput:
        """1. Sort close_deltas by gain desc, filter > gain_threshold
        2. draft_ecs(case, audit) → initial ECS
        3. For each (label, gain) in sorted close_deltas:
             a. RepairExecutor.run(case, ecs, label, replay, ...)
             b. If recovered → success
             c. If partial → update ecs, continue
             d. If failed → log, continue
        4. Return last result
        """
```

## 测试结构（计划）

| 测试类 | 验收标准 | 测试数量 | 覆盖内容 |
| --- | --- | --- |
| `RepairExecutorBuildContextTest` | context 构建 | ~6 | context 含 baseline、含 label、含 evidence_block、含 fm_context、含 supported_actions、不含 gold_answer |
| `RepairExecutorRunTest` | 单次修复 | ~8 | LLM 选择 append/replace/relocate、action 执行成功、recovered/partial/failed 判定、不支持的 action 抛错 |
| `RepairOrchestratorLoopTest` | 迭代编排 | ~8 | close_deltas 排序、单 label recovered 停止、partial 迭代、exhaust 未 recover、gain<threshold 跳过、空 close_deltas、ECS 迭代间更新 |
| `RepairedContextTest` | 扩展数据类 | ~5 | baseline_context/label/evidence_block/fm_context 传递正确、corrected_memory 非空 |
| `BuildRepairedContextTest` | context 构造函数 | ~4 | 所有字段填充、fm_context 为空、baseline 为空、与 PostRepair 兼容 |
| `IntegrationTest` | 端到端 | ~6 | 完整 recover、partial→recover、exhaust、mem0/Letta adapter 集成、agent_generate 调用验证 |

## 非回归分析

### 现有模块变更
- `post_repair.py`: `RepairedContext` 新增 4 个字段（均有默认值，向后兼容）。
- `post_repair.py`: 新增 `build_repaired_context` 函数，不改变 `draft_ecs`、`run_post_repair_context_replay`。
- `replays.py`: `_score_recovered_evidence` 需要 `agent_generate` 参数（Decision B，是 0019 Phase B 后续改动）。

### 向后兼容性
- 现有 `RepairedContext` 构造函数添加默认值后无需修改。
- 现有 `run_post_repair_context_replay` 逻辑不变。
- `RepairExecutor` 和 `RepairOrchestrator` 是全新类，不替换现有功能。
