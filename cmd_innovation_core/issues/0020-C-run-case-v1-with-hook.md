# Issue 0020-C: run_case_v1_with_hook — Online Entry Point Injection Pattern

**Status**: design
**Date**: 2026-05-23
**Decision**: 32 point 10 (Issue C integration)
**Parent**: Decision 32 Post-Gate Pipeline
**Blocked by**: 0020-A (RepairExecutor + RepairOrchestrator), 0020-D (Failure Memory upgrade)

## 目的

实现 CMD-Skill Adapter 在线入口 `run_case_v1_with_hook`——CMD-Audit 不持有 adapter/agent 引用，而是由外层（CMD-Skill Adapter）调用时注入 `adapter`、`agent_generate`、`scorer`。Hook 触发后走完整下游 pipeline：归因 → ECS → RepairOrchestrator → FM 存储。

## 源需求

| 来源 | 应用的需求 |
| --- | --- |
| `cmd_open_decisions.md` Decision 32 point 10 | `run_case_v1_with_hook(query, retrieved_items, adapter, agent_generate, scorer) → AuditResult`。CMD-Audit 不持有 adapter/agent 引用，与现有 `run_case_with_mem0`/`run_case_with_letta` 模式一致。 |
| `cmd_open_decisions.md` Decision 32 point 17 | Online pipeline data flow: query → hook（0021 两阶段）→ RPE judge top-k replays → 归因 subagent loop → ECS + fm_context → RepairOrchestrator → FM storage → result。 |
| `cmd_open_decisions.md` Decision 32 point 15 | Hook → pipeline handoff: selected_replays 来自 RPE judge top-k；FM context 在 ECS 阶段合并（非 replay 前）。 |
| `TASK.md` | Issue 0020-C — CMD-Skill Adapter 注入 adapter/agent_generate/scorer。依赖 A, D。 |

## 领域边界

Issue 0020-C 定义在线入口的集成模式。它不定义 hook 内部逻辑（0021），不定义 RepairExecutor（0020-A），不定义 FM 检索（0020-D），不定义 PreCmdDecision→AuditResult（0020-F）。

```text
CMD-Skill Adapter（外层）
  → 持有: adapter + agent_generate + scorer
  → 调用: cmd_audit.run_case_v1_with_hook(
        query=query,
        retrieved_items=retrieved_items,
        adapter=adapter,
        agent_generate=agent_generate,
        scorer=scorer,
    ) → AuditResult

CMD-Audit（内层，cmd_audit/harness.py）
  → 不持有 adapter/agent 引用
  → 接收注入参数
  → 内部执行:
      1. post_retrieve_hook(query, retrieved_items) → PreCmdDecision
      2. 如果 trigger: run hook pipeline → AuditResult
      3. 如果 skip: AuditResult(skipped_by_hook=True)
```

## 计划代码产出物

| 产出物 | 角色 |
| --- | --- |
| `cmd_audit/harness.py` | 新增 `run_case_v1_with_hook(query, retrieved_items, *, adapter, agent_generate, scorer) -> AuditResult` |
| `cmd_audit/__init__.py` | 导出 `run_case_v1_with_hook` |
| `tests/test_cmd_audit_issue20_C_hook_integration.py` | 测试文件 |

## 模块地图

| 模块 | Issue 0020-C 角色 |
| --- | --- |
| `cmd_audit/harness.py` | 新增 `run_case_v1_with_hook`——在线入口，注入模式 |
| `cmd_audit/hook/post_retrieve_hook.py` | 复用（0021）——两阶段 gating |
| `cmd_audit/replays.py` | 复用 `run_v1_replay_portfolio_subset`——仅运行 selected_replays |
| `cmd_audit/attribution.py` | 复用 `assign_attribution_v1` |
| `cmd_audit/repair_executor.py` | 复用 `RepairOrchestrator`（0020-A） |
| `cmd_audit/post_repair.py` | 复用 `draft_ecs_for_label`（0020-G） |
| `cmd_audit/failure_memory.py` | 复用 FM `store`（0020-D，仅 recovered） |

## 调用图（计划）

```text
cmd_audit/harness.py
  → run_case_v1_with_hook(
        query: str,
        retrieved_items: tuple[MemoryItem, ...],
        *,
        adapter: Adapter,
        agent_generate: Callable[[str, str], str],
        scorer: Callable,
    ) -> AuditResult:

    1. decision = post_retrieve_hook(query, retrieved_items)
       → PreCmdDecision(stage, per_replay_scores, selected_replays)

    2. 如果 decision.stage == "rpe_below_threshold" 或 decision.selected_replays == ():
       → 返回 AuditResult(attribution=None, hook_stage="rpe_below_threshold", ...)

    3. 构建临时 ProbeCase（从 query + retrieved_items）:
       case = ProbeCase(case_id=hash(query), query=query,
                        extracted_memory=retrieved_items, ...)

    4. 运行归因 subagent loop:
       replays = run_v1_replay_portfolio_subset(case, decision.selected_replays)
       → 每个 replay 跑 agent.generate(context) → answer → scorer 评分
       attribution = assign_attribution_v1(replays, ...)

    5. 如果 attribution 为 None:
       → self-supervision path（0021 Step 2）
       → 如果 surrogate 也为零: AttributionFailed

    6. draft_ecs_for_label(case, audit, attribution.primary_label, fm_store=fm_store)
       → ECSDraft（含 fm_context）

    7. 计算 close_deltas:
       close_deltas = compute_close_deltas(replays, threshold=GAIN_THRESHOLD)

    8. RepairOrchestrator.run(case, audit, adapter=adapter,
                              agent_generate=agent_generate,
                              scorer=scorer, close_deltas=close_deltas)

    9. 如果 recovered: FM store(...)

    10. PreCmdDecision signals → AuditResult（0020-F）

    11. 返回 AuditResult
```

## 数据流

### 输入参数

```python
def run_case_v1_with_hook(
    query: str,
    retrieved_items: tuple[MemoryItem, ...],
    *,
    adapter: Adapter,
    agent_generate: Callable[[str, str], str],
    scorer: Callable,
    fm_store: FailureMemoryStore | None = None,
    gain_threshold: float = 0.05,
) -> AuditResult:
```

### 与现有 run_case_with_mem0/letta 的关系

```text
现有模式（离线，记录式）:
  run_case_with_mem0(case, trace) → AuditResult
  → adapter 从 trace 重放，不直接调用 agent

新模式（在线，注入式）:
  run_case_v1_with_hook(query, retrieved_items, adapter, agent_generate, scorer)
  → adapter 直接在 agent 上执行修复
  → agent_generate 调用真实 agent generation
  → scorer 调用 SubagentScorer
```

## 函数级合约（计划）

### `run_case_v1_with_hook`

```python
def run_case_v1_with_hook(
    query: str,
    retrieved_items: tuple[MemoryItem, ...],
    *,
    adapter: Adapter,
    agent_generate: Callable[[str, str], str],
    scorer: Callable,
    fm_store: FailureMemoryStore | None = None,
    gain_threshold: float = 0.05,
) -> AuditResult:
    """CMD-Skill Adapter online entry point. Injection pattern.

    CMD-Audit 不持有 adapter/agent 引用——由外层注入。

    流程:
    1. post_retrieve_hook(query, retrieved_items) → PreCmdDecision
    2. skip → AuditResult(attribution=None, hook_stage="rpe_below_threshold")
    3. trigger → 构建临时 ProbeCase → 归因 → ECS → RepairOrchestrator → FM
    """
```

### AuditResult 扩展字段（0020-F 同步）

```python
hook_stage: str = ""                    # "empty_ctx" | "rpe_top_k" | "rpe_below_threshold"
per_replay_scores: tuple = ()           # 10 x ReplayScore
selected_replays: tuple[str, ...] = ()
```

## 测试结构（计划）

| 测试类 | 验收标准 | 数量 | 覆盖内容 |
| --- | --- | --- | --- |
| `HookIntegrationSkipTest` | hook 跳过 | ~4 | RPE below-threshold 跳过、selected_replays 为空、attribution=None |
| `HookIntegrationTriggerTest` | hook 触发 | ~6 | empty_ctx 触发全部、RPE top-k 触发子集、selected_replays 正确、agent_generate 被调用、scorer 被调用 |
| `AttributionFailedTest` | 归因失败 | ~4 | RPE+surrogate 全零、AttributionFailed、不存 FM、不进 RepairOrchestrator |
| `RepairOrchestratorIntegrationTest` | 修复集成 | ~5 | recovered 存 FM、partial 迭代、exhaust 停止、adapter.apply_repair 被调用、fm_context 注入 |
| `InjectionPatternTest` | 注入模式 | ~4 | CMD-Audit 不持有 adapter/agent、参数注入正确、与 run_case_with_mem0 隔离 |
| `EndToEndTest` | 端到端 | ~6 | empty_ctx→full pipeline、RPE top-k→full pipeline、skip/failed/recovered path、FM 存储 |

## 非回归分析

### 现有模块不变
- 现有 `run_case_v1`、`run_case_with_mem0`、`run_case_with_letta` 保持不变。`run_case_v1_with_prefilter` 已在 0021 PR2 删除。
- 现有 harness 函数签名不变。
- `run_case_v1_with_hook` 是全新入口，不替换现有任何入口。

### 向后兼容性
- 现有离线 harness 路径（`run_case_v1`）仍使用 `load_probe_cases_v1` 加载完整 `ProbeCase`。
- `run_case_v1_with_hook` 在线路径从 query + retrieved_items 构建临时案例，不需要 `gold_evidence`。
