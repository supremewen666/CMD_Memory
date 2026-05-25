# Issue 0011 实现细节：ingestion_error 与 route_error 标签

## 目的

本文档是 issue 0011《实现 ingestion_error 与 route_error 标签》的全局实现地图。它映射每个函数、数据类、辅助函数、异常和常量到其确切的源码位置、签名、行为、调用者和领域含义。

Issue 0011 将 V0 六标签管道扩展为 V1 八标签管道，新增两个标签并保持完全的非回归：

```text
V0 Pipeline (issue 0001-0003)
  ProbeCase -> run_case -> run_v0_replay_portfolio -> assign_attribution -> AuditResult

V1 Pipeline (issue 0011)
  ProbeCase -> run_case_v1 -> run_v1_replay_portfolio (7 replays)
           -> assign_attribution_v1 (has_ingestion_trace 参数)
           -> AuditResult

ingestion_error 拆分:
  oracle_write recovery_gain > 0 AND has_ingestion_trace == False
    -> "ingestion_error" (证据从未到达代理——摄入流水线失败)
  oracle_write recovery_gain > 0 AND has_ingestion_trace == True
    -> "write_error" (证据到达但未被写入记忆)

route_error 新增:
  oracle_route recovery_gain > 0 AND 最佳 store ≠ 默认 store
    -> "route_error" (证据存在但位于错误的存储/层级)
```

该切片交付 `cmd_tracer_bullets.md` 中的 TDD Cycle 16：

| 周期 | 标题 | 状态 |
| --- | --- | --- |
| Cycle 16 | Ingestion + Route Error Labels | 绿色 |

Issue 0012（`granularity_error` + `graph_error` + `safety_error`）后续在 issue 0011 的 V1 标签注册表之上构建；issue 0011 是 V1 标签扩展的基础层。

## 源需求

本实现遵循以下本地文档。

| 来源 | 在 Issue 0011 中应用的需求 |
| --- | --- |
| `TASK.md` | V1 标签扩展：`ingestion_error` 从 `write_error` 拆分，`route_error` 作为新的第一类管道标签。Oracle Write 已经是恢复入口——仅需归因逻辑变更。Oracle Route 是新的回放，枚举存储/层级并选择最佳恢复。非回归：六标签烟雾套件通过 V1 流水线产生相同标签。 |
| `CLAUDE.md` | V1 标签扩展顺序：`ingestion_error` → `route_error`。V0 归因仅输出六个管道标签。`validate_v1_label("ingestion_error")` 成功；`validate_v0_label("ingestion_error")` 仍然抛出 `ValueError`。将 CMD-Audit 与 CMD-Skill Adapter 分开。 |
| `cmd_innovation_core/CONTEXT.md` | V1 Essentials：标签扩展顺序 `ingestion_error` → `route_error`。`ingestion_error` 拆分自 `write_error`：摄入失败意味着证据从未到达代理。`route_error`：证据存在于错误的存储/层级。延迟标签仅剩 `granularity_error`、`graph_error`、`safety_error`。 |
| `cmd_innovation_core/prd/cmd_minimal_probe_prd.md` | User Story 37（`ingestion_error` 标签）、User Story 38（`route_error` 标签）。V1 Scope：八标签归因表。 |
| `cmd_innovation_core/issues/0011-implement-ingestion-and-route-error-labels.md` | 七个验收标准：ingestion 在 Oracle Write 恢复且无 add() 跟踪时归因；write 在 Oracle Write 恢复且有 add() 跟踪时归因；Oracle Route 回放枚举存储；V0 非回归；validate_v1_label 接受新标签；延迟注册表更新；行为级测试。 |
| `cmd_innovation_core/tdd/cmd_tracer_bullets.md` | Cycle 16 RED/GREEN：Ingestion + Route Error Labels。`ingestion_error` 案例：Oracle Write 恢复但 `has_ingestion_trace=False`。`route_error` 案例：Oracle Route 通过枚举所有存储进行恢复。 |

## 领域边界

Issue 0011 在现有 V0 归因（issues 0001-0003）、基线（issue 0002）、修复验证（issue 0005）、针对性修复（issue 0006）和版本关卡（issue 0010）之上构建 V1 标签层。它不更改任何现有的 V0 回放逻辑、基线逻辑或修复流水线。它扩展标签注册表并在这些现有层之上添加新的 V1 入口点。

```text
V0 Pipeline（现有，未更改）
  run_case(ProbeCase) -> run_v0_replay_portfolio -> assign_attribution -> AuditResult
  run_case_full(ProbeCase) -> run_case + draft_ecs + build_repaired_context + ... -> FullAuditResult

V1 Pipeline（issue 0011，新增）
  run_case_v1(ProbeCase) -> run_v1_replay_portfolio -> assign_attribution_v1 -> AuditResult
  run_case_full_v1(ProbeCase) -> run_case_v1 + draft_ecs + build_repaired_context + ... -> FullAuditResult
  run_cases_v1([ProbeCase]) -> [run_case_v1(c) for c in cases]
  run_cases_full_v1([ProbeCase]) -> [run_case_full_v1(c) for c in cases]
```

Issue 0011 拥有的内容：

- 定义 `V1_PIPELINE_LABEL_ORDER`（8 个标签）和 `V1_PIPELINE_LABELS`（frozenset）。
- 定义 `V1_REPLAY_TO_LABEL`（7 个映射，包含 `oracle_route` → `route_error`）。
- `validate_v1_label(label) -> str`：接受全部 8 个 V1 标签，拒绝项目标签和延迟标签。
- `validate_v0_label` 已更新：对 V1 标签给出区分性错误消息。
- 更新 `DEFERRED_PIPELINE_LABELS`：移除 `ingestion_error` 和 `route_error`。
- `ProbeCase` 上的 `has_ingestion_trace: bool` 字段（默认 `True`，向后兼容）。
- `ProbeCase` 上的 `default_store: str` 字段（默认 `"episodic"`）。
- `MemoryItem` 上的 `store: str` 字段（默认 `"default"`）。
- `ProbeCase.from_mapping_v1(value) -> ProbeCase`：使用 `validate_v1_label` 的类方法。
- `load_probe_cases_v1(path) -> list[ProbeCase]`：使用 V1 标签校验的加载器。
- `run_oracle_route(case) -> ReplayResult`：枚举存储/层级的新回放干预。
- `run_v1_replay_portfolio(case) -> tuple[ReplayResult, ...]`：7 回放组合。
- `_collect_stores(case) -> list[str]`：从记忆项收集唯一存储名称。
- `_recover_from_store(case, store) -> str`：从特定存储恢复证据文本。
- `assign_attribution_v1(replays, *, has_ingestion_trace, ...) -> AttributionResult`：带有摄入/写入拆分的 V1 归因。
- `_v1_label_for_replay(replay_name, *, has_ingestion_trace) -> str`：动态标签映射。
- `run_case_v1(case) -> AuditResult`：V1 流水线入口点。
- `run_cases_v1(cases) -> list[AuditResult]`：批量 V1 运行器。
- `run_case_full_v1(case) -> FullAuditResult`：完整 V1 流水线（归因 → ECS → 修复）。
- `run_cases_full_v1(cases) -> list[FullAuditResult]`：批量完整 V1 运行器。
- `get_targeted_repair_action_v1(label) -> TargetedRepairAction`：接受 V1 标签的修复操作查找。
- `REPAIR_ACTION_BY_LABEL` 中针对 `ingestion_error` 和 `route_error` 的新修复操作。
- `TargetedRepairAction.__post_init__` 使用 `validate_v1_label` 更新。
- `ECSDraft.__post_init__` 使用 `validate_v1_label` 更新（原来是 `validate_v0_label`）。
- `_ecs_for_label` 使用 `get_targeted_repair_action_v1` 更新。
- `DiagnosisPrediction.__post_init__` 使用 `validate_v1_label` 更新。
- 两个 V1 探针案例夹具文件和 9 个测试类包含 44 个测试方法。

Issue 0011 不拥有的内容（属于其他 issue）：

- 更改 V0 回放组合或归因阈值（issue 0001、0003）。
- 更改基线套件或对比器逻辑（issue 0002）。
- 更改 Post-Repair Context Replay 评分（issue 0005）。
- 更改 ECS Failure Memory 逻辑（issue 0007）。
- 添加 `granularity_error`、`graph_error` 或 `safety_error`（issue 0012）。
- 11 标签耦合失败重新校准（issue 0013）。
- mem0/Letta 适配器集成（issues 0014、0015）。
- 真实数据探针案例（issue 0016）。

## 当前代码产出物

| 产出物 | 在 Issue 0011 中的角色 |
| --- | --- |
| `cmd_audit/labels.py` | V1 标签注册表、`V1_REPLAY_TO_LABEL`、`validate_v1_label`、更新后的延迟集。主标签边界模块。 |
| `cmd_audit/models.py` | `ProbeCase` 上的 `has_ingestion_trace` 和 `default_store` 字段、`MemoryItem` 上的 `store` 字段、`from_mapping_v1`、`load_probe_cases_v1`。 |
| `cmd_audit/replays.py` | `run_oracle_route` 回放、`run_v1_replay_portfolio`、`_collect_stores`、`_recover_from_store`。 |
| `cmd_audit/attribution.py` | `assign_attribution_v1`、`_v1_label_for_replay`——带有摄入/写入拆分的 V1 归因。 |
| `cmd_audit/harness.py` | `run_case_v1`、`run_cases_v1`、`run_case_full_v1`、`run_cases_full_v1`——V1 流水线入口点。 |
| `cmd_audit/repairs.py` | 针对 `ingestion_error` 和 `route_error` 的新 `TargetedRepairAction` 条目、`get_targeted_repair_action_v1`、更新后的 `__post_init__` 校验。 |
| `cmd_audit/post_repair.py` | `ECSDraft.__post_init__` 使用 `validate_v1_label`、`_ecs_for_label` 使用 `get_targeted_repair_action_v1`。 |
| `cmd_audit/metrics.py` | `DiagnosisPrediction.__post_init__` 使用 `validate_v1_label`（接受 V0+V1 标签）。 |
| `cmd_audit/__init__.py` | 导出全部新的 V1 公共符号。 |
| `data/probe_cases/v1_ingestion_error_case.json` | 单一 `ingestion_error` 探针夹具（`has_ingestion_trace: false`）。 |
| `data/probe_cases/v1_route_error_case.json` | 单一 `route_error` 探针夹具（多存储记忆项）。 |
| `tests/test_cmd_audit_issue11_v1_labels.py` | 9 个测试类，44 个测试方法，覆盖 Cycle 16。 |
| `artifacts/sandbox/`（未来） | V1 流水线运行将在此处写入归因和修复产出物。 |

## 模块地图

| 模块 | Issue 0011 角色 |
| --- | --- |
| `cmd_audit/labels.py` | **已更新。** 拥有 V1 标签常量和 `validate_v1_label`。现有 V0 常量、`validate_v0_label`、monitor 枚举和 `LabelValidationError` 保持不变。`DEFERRED_PIPELINE_LABELS` 已缩减：仅剩 `granularity_error`、`graph_error`、`safety_error`。 |
| `cmd_audit/models.py` | **已更新。** `MemoryItem` 获得 `store: str = "default"`（第 32 行）。`ProbeCase` 获得 `has_ingestion_trace: bool = True` 和 `default_store: str = "episodic"`（第 117-118 行）。新增 `from_mapping_v1` 类方法（第 144-168 行）和 `load_probe_cases_v1` 函数（第 212-222 行）。现有的 `from_mapping`、`load_probe_cases` 和 `validate` 保持不变。 |
| `cmd_audit/replays.py` | **已更新。** 拥有 `run_oracle_route`（第 132-150 行）、`run_v1_replay_portfolio`（第 35-46 行）、以及私有辅助函数 `_collect_stores`（第 153-160 行）和 `_recover_from_store`（第 163-176 行）。现有的 6 个 V0 回放和 `_score_recovered_evidence` 保持不变。 |
| `cmd_audit/attribution.py` | **已更新。** 拥有 `assign_attribution_v1`（第 49-85 行）和 `_v1_label_for_replay`（第 95-101 行）。现有的 `assign_attribution`、`_label_for_replay` 和 `AttributionResult` 保持不变。 |
| `cmd_audit/harness.py` | **已更新。** 拥有 4 个新的 V1 流水线函数（第 194-235 行）：`run_case_v1`、`run_cases_v1`、`run_case_full_v1`、`run_cases_full_v1`。现有的 `run_case`、`run_case_full` 和所有写入器函数保持不变。 |
| `cmd_audit/repairs.py` | **已更新。** `TargetedRepairAction.__post_init__` 现在使用 `validate_v1_label`（第 23 行）。`REPAIR_ACTION_BY_LABEL` 已扩展，包含 `ingestion_error`（第 90-98 行）和 `route_error`（第 100-108 行）条目。新增 `get_targeted_repair_action_v1`（第 119-122 行）。现有的 `get_targeted_repair_action` 保持不变。 |
| `cmd_audit/post_repair.py` | **已更新。** 从 `labels` 导入 `validate_v1_label`（第 9 行）。`ECSDraft.__post_init__` 现在调用 `validate_v1_label`（第 79 行）。`_ecs_for_label` 现在调用 `get_targeted_repair_action_v1`（第 218 行）。 |
| `cmd_audit/metrics.py` | **已更新。** 从 `labels` 导入 `validate_v1_label`（第 9 行）。`DiagnosisPrediction.__post_init__` 现在对所有标签字段调用 `validate_v1_label`（第 22-25 行）。 |
| `cmd_audit/__init__.py` | **已更新。** 导出 12 个新的 V1 符号：`V1_PIPELINE_LABEL_ORDER`、`V1_PIPELINE_LABELS`、`V1_REPLAY_TO_LABEL`、`LabelValidationError`、`validate_v1_label`、`load_probe_cases_v1`、`run_v1_replay_portfolio`、`assign_attribution_v1`、`run_case_v1`、`run_cases_v1`、`run_case_full_v1`、`run_cases_full_v1`。 |
| `tests/test_cmd_audit_issue11_v1_labels.py` | 9 个测试类，44 个测试方法，覆盖 Cycle 16。 |

## 调用图

### V1 归因流水线（issue 0011）

```text
cmd_audit/__init__.py
  -> harness.run_case_v1(ProbeCase)
      -> baselines.run_baseline_suite(ProbeCase)
          -> baselines.run_memory_baselines
          -> baselines._select_comparison_baseline
          -> baselines.run_evidence_recall_heuristic
          -> baselines.run_subagent_judge_baseline
          -> baselines.run_random_label_baseline
          -> baselines.run_subagent_judge_monitor
      -> replays.run_v1_replay_portfolio(ProbeCase)
          -> replays.run_oracle_write
          -> replays.run_oracle_compression
          -> replays.run_verbatim_event_oracle
          -> replays.run_oracle_retrieval
          -> replays.run_injection_oracle
          -> replays.run_evidence_given_reasoning
          -> replays.run_oracle_route(case)
              -> replays._collect_stores(case)
                  -> 遍历 case.extracted_memory 中的每个 MemoryItem
                  -> 收集唯一的 item.store 值
              -> 对于每个 store:
                  -> replays._recover_from_store(case, store)
                      -> 对于每个 gold_evidence 项:
                          -> 检查 evidence.source_memory_id
                          -> 通过 memory_id 查找 MemoryItem
                          -> 检查 memory.store == store
                          -> scoring.evidence_recall_from_text((evidence,), memory.text)
              -> replays._score_recovered_evidence(case, "oracle_route", best_block)
      -> attribution.assign_attribution_v1(replays, has_ingestion_trace=case.has_ingestion_trace)
          -> 按 recovery_gain 降序排列 replays
          -> top = ranked[0]
          -> attribution._v1_label_for_replay(top.replay_name, has_ingestion_trace=...)
              -> 如果 replay_name == "oracle_write" 且 has_ingestion_trace 为 False:
                  -> 返回 "ingestion_error"
              -> 否则: 返回 V1_REPLAY_TO_LABEL[replay_name]
          -> labels.validate_v1_label(predicted_label)
          -> 对于 top2_labels: validate_v1_label(_v1_label_for_replay(...))
      -> AuditResult(...)

  -> harness.run_case_full_v1(ProbeCase)
      -> harness.run_case_v1(ProbeCase)          （V1 归因）
      -> post_repair.draft_ecs(case, audit)
          -> post_repair._ecs_for_label(case, predicted_label, replay)
              -> repairs.get_targeted_repair_action_v1(predicted_label)
                  -> labels.validate_v1_label(label)
                  -> 返回 REPAIR_ACTION_BY_LABEL[label]
          -> ECSDraft(...)
              -> __post_init__:
                  -> labels.validate_v1_label(predicted_label)
                  -> post_repair._validate_ecs_cause(cause)
      -> post_repair.build_repaired_context(case, ecs_draft)
      -> post_repair.run_post_repair_context_replay(case, repaired_context)
      -> post_repair.run_hard_case_update_baseline(case)
      -> FullAuditResult(audit, ecs_draft, repaired_context, post_repair, hard_case_baseline)
```

### V0 现有流水线（未更改，供参考）

```text
cmd_audit/__init__.py
  -> harness.run_case(ProbeCase)
      -> baselines.run_baseline_suite(ProbeCase)
      -> replays.run_v0_replay_portfolio(ProbeCase)    （6 个回放）
      -> attribution.assign_attribution(replays)        （V0 标签，6 个标签）
          -> attribution._label_for_replay
          -> labels.validate_v0_label
      -> AuditResult(...)
```

### 行为测试路径

```text
tests/test_cmd_audit_issue11_v1_labels.py
  -> labels.validate_v1_label(label)                           （V1LabelValidationTest）
  -> labels.validate_v0_label(label)                           （V1LabelValidationTest，回归）
  -> models.load_probe_cases_v1(path)                          （V1ProbeCaseLoadingTest）
  -> models.load_probe_cases(path)                             （V1ProbeCaseLoadingTest，回归）
  -> replays.run_v1_replay_portfolio(case)                     （IngestionErrorAttributionTest）
  -> attribution.assign_attribution_v1(replays, has_ingestion_trace=...)  （IngestionErrorAttributionTest）
  -> replays.run_oracle_route(case)                            （OracleRouteReplayTest）
  -> replays.run_v1_replay_portfolio(case)                     （V1ReplayPortfolioTest）
  -> harness.run_case_v1(case)                                 （V1NonRegressionTest）
  -> harness.run_cases_v1(cases)                               （V1NonRegressionTest）
  -> labels.V1_REPLAY_TO_LABEL                                 （V1ReplayToLabelMappingTest）
  -> harness.run_case_full_v1(case)                            （V1ECSCompatibilityTest）
```

## 数据流

### 输入夹具

```text
data/probe_cases/v0_issue3_cases.json                       # 六案例 V0 烟雾套件（非回归）
data/probe_cases/v1_ingestion_error_case.json                # 单一 ingestion_error 探针夹具
data/probe_cases/v1_route_error_case.json                    # 单一 route_error 探针夹具
```

### v1_ingestion_error_case.json 的结构

此夹具演示了证据从未到达代理的情况。

| 字段 | 值 | 目的 |
| --- | --- | --- |
| `case_id` | `"v1-ingestion-001"` | 唯一案例标识符 |
| `query` | `"Which city did Ravi choose for the compliance audit?"` | 原始失败查询 |
| `raw_events[0].text` | `"...the relevant conversation transcript never reached the agent..."` | 确认摄入失败的占位事件 |
| `extracted_memory[0].text` | `"A compliance audit was discussed."` | 不含城市名称的模糊记忆 |
| `gold_evidence[0]` | 无 `source_memory_id`，无 `source_event_id` | Oracle Write 收集此证据（两个源 ID 均为 `None`） |
| `gold_answer` | `"Toronto"` | 金标准答案 |
| `perturbation_label` | `"ingestion_error"` | 通过 `validate_v1_label`，被 `validate_v0_label` 拒绝 |
| `has_ingestion_trace` | `false` | 触发 `_v1_label_for_replay` 中的摄入/写入拆分 |
| `baseline_outputs[0].answer` | `"Unknown"` | 基线缺少证据 → answer_score=0.0 |

数据流步骤：
1. `load_probe_cases_v1` 加载此案例，通过 `from_mapping_v1` 校验 `perturbation_label`。
2. `run_oracle_write` 发现金标证据的 `source_memory_id is None and source_event_id is None` → 收集 `evidence_block = "Ravi chose Toronto for the compliance audit."`
3. `evidence_recall_from_text` 找到所有必需短语（"Ravi", "Toronto", "compliance audit"）→ `evidence_score = 1.0`
4. `_score_recovered_evidence` 设置 `answer = "Toronto"`（与金标准匹配）→ `answer_score = 1.0`
5. `recovery_gain = 1.0 - 0.0 = 1.0` → oracle_write 是 V1 组合中唯一具有正 recovery_gain 的回放
6. `assign_attribution_v1` 调用 `_v1_label_for_replay("oracle_write", has_ingestion_trace=False)` → 返回 `"ingestion_error"`
7. `validate_v1_label("ingestion_error")` 成功 → `predicted_label = "ingestion_error"` ✓

### v1_route_error_case.json 的结构

此夹具演示了证据存在于非默认存储（语义记忆而非情景记忆）的情况。

| 字段 | 值 | 目的 |
| --- | --- | --- |
| `case_id` | `"v1-route-001"` | 唯一案例标识符 |
| `query` | `"Which city did Sasha choose for the design workshop?"` | 原始失败查询 |
| `extracted_memory[0]` | store="episodic"，text="A design workshop city was discussed." | 基线检索到的模糊情景记忆项 |
| `extracted_memory[1]` | store="semantic"，text="Sasha chose Stockholm for the design workshop." | 包含证据（"Stockholm"）的语义记忆项 |
| `gold_evidence[0].source_memory_id` | `"mem-702"` | 指向语义存储中的记忆项 |
| `gold_answer` | `"Stockholm"` | 金标准答案 |
| `perturbation_label` | `"route_error"` | 通过 `validate_v1_label`，被 `validate_v0_label` 拒绝 |
| `default_store` | `"episodic"` | 基线仅查询情景记忆 |
| `baseline_outputs[0].retrieved_memory_ids` | `["mem-701"]` | 基线仅检索到情景项（模糊） |
| `baseline_outputs[0].answer` | `"Unknown"` | answer_score = 0.0 |

数据流步骤：
1. `run_oracle_route` 调用 `_collect_stores` → 返回 `["episodic", "semantic"]`
2. 对于 `store="episodic"`：`_recover_from_store` 检查 `mem-701`（store 匹配）→ 文本 = "A design workshop city was discussed." → 不包含 "Stockholm" → `evidence_recall = 0.0`
3. 对于 `store="semantic"`：`_recover_from_store` 检查 `mem-702`（store 匹配）→ 文本 = "Sasha chose Stockholm for the design workshop." → 包含所有必需短语 → `evidence_recall = 1.0`
4. `best_score = 1.0`，`best_block = mem-702.text` → `_score_recovered_evidence` 设置 `answer = "Stockholm"` → `answer_score = 1.0`
5. `recovery_gain = 1.0 - 0.0 = 1.0` → oracle_route 具有正 recovery_gain
6. `assign_attribution_v1` 调用 `_v1_label_for_replay("oracle_route", has_ingestion_trace=True)` → 返回 `"route_error"`
7. `validate_v1_label("route_error")` 成功 → `predicted_label = "route_error"` ✓

**关于独立工具中路由/检索重叠的说明：** 在具有平面记忆的独立工具中，`oracle_retrieval`（搜索所有记忆项）和 `oracle_route`（按存储枚举）可能恢复相同的证据块。当两者具有相等的 recovery_gain 时，`oracle_retrieval` 首先出现在组合元组中，因此 `retrieval_error` 是预测标签——这是独立工具的可接受行为，其中存储边界是概念性的。在真实的层级系统中（例如 Letta 适配器），不同的存储 API 确保了清晰的区分：`oracle_route` 查询基线未触及的存储。

### 中间类型

**ProbeCase** 新增字段（来自 `cmd_audit/models.py:99-168`）：

| 字段 | 类型 | 默认值 | 含义 |
| --- | --- | --- | --- |
| `has_ingestion_trace` | `bool` | `True` | 如果金标证据曾经出现在代理的摄入输入中（任何 `add()` 调用的等价物）则为 `True`。`False` 意味着证据从未到达代理。 |
| `default_store` | `str` | `"episodic"` | 基线检索的默认存储/层级。Oracle Route 使用此信息来识别路由失误。 |

**MemoryItem** 新增字段（来自 `cmd_audit/models.py:28-41`）：

| 字段 | 类型 | 默认值 | 含义 |
| --- | --- | --- | --- |
| `store` | `str` | `"default"` | 此记忆项所在的存储/层级。由 `_collect_stores` 用于枚举可用存储，由 `_recover_from_store` 用于按存储过滤。 |

**V1_REPLAY_TO_LABEL** 映射（来自 `cmd_audit/labels.py:65-73`）：

| 回放名称 | V1 标签 | 与 V0 的区别 |
| --- | --- | --- |
| `oracle_write` | `write_error`（默认；可被 `ingestion_error` 覆盖） | 相同（覆盖是 V1 新增） |
| `oracle_compression` | `compression_error` | 与 V0 相同 |
| `verbatim_event_oracle` | `premature_extraction_error` | 与 V0 相同 |
| `oracle_retrieval` | `retrieval_error` | 与 V0 相同 |
| `injection_oracle` | `injection_error` | 与 V0 相同 |
| `evidence_given_reasoning` | `reasoning_error` | 与 V0 相同 |
| `oracle_route` | `route_error` | **V1 新增** |

**TargetedRepairAction** V1 新增条目（来自 `cmd_audit/repairs.py:88-110`）：

| 标签 | action_name | cause | repair_guidance |
| --- | --- | --- | --- |
| `ingestion_error` | Oracle Write Repair | "gold evidence never reached the agent through the ingestion pipeline; the write step could not store what it never received" | "verify ingestion pipeline is receiving and forwarding all relevant evidence to the write step" |
| `route_error` | Oracle Route Repair | "evidence was stored in a store that the baseline retrieval did not query" | "update routing logic to store evidence in the correct store or expand retrieval to query all relevant stores" |

### 输出产出物

V1 流水线运行生成与 V0 相同类型的产出物，但使用 V1 归因和 8 标签混淆矩阵：

```text
artifacts/sandbox/                          # V1 流水线运行将写入：
  attribution_table.csv                     # 8 标签归因
  comparison_metrics.csv                    # CMD-V1 vs 基线
  attribution_confusion_matrix.csv          # 8x8 混淆矩阵
  post_repair_table.csv                     # 修复后回放结果
  repair_success_table.csv                  # 修复成功对比
  repair_label_summary.csv                  # 逐标签修复汇总
  repair_claim_ledger.txt                   # 修复声明账本
```

## 函数级合约

### `cmd_audit/labels.py`

这是 issue 0011 的主标签边界模块。文件：`cmd_audit/labels.py`（128 行）。包含 6 个常量、2 个 frozenset、2 个异常类、3 个公共函数。

---

#### 常量：`V1_PIPELINE_LABEL_ORDER`

位置：`cmd_audit/labels.py:16-25`

```python
V1_PIPELINE_LABEL_ORDER = (
    "write_error",
    "compression_error",
    "premature_extraction_error",
    "retrieval_error",
    "injection_error",
    "reasoning_error",
    "ingestion_error",
    "route_error",
)
```

目的：

- 为 V1 归因定义完整的 8 标签有序元组。
- 顺序决定了混淆矩阵的列顺序和报告的一致性。
- V0 的 6 个标签在前（保留 V0 顺序），随后是 2 个 V1 标签。

与 V0 的关系：

- `V1_PIPELINE_LABELS` 是 `V0_PIPELINE_LABELS` 的严格超集。
- V0 的 `ingestion_error` 和 `route_error` 在 V0 中属于 `DEFERRED_PIPELINE_LABELS`，现已移至活动状态。

调用者：

- `validate_v1_label`（第 117 行）
- `V1LabelValidationTest.test_v1_label_order_has_eight_labels`
- `V1LabelValidationTest.test_v1_labels_are_superset_of_v0`

---

#### 常量：`V1_REPLAY_TO_LABEL`

位置：`cmd_audit/labels.py:65-73`

```python
V1_REPLAY_TO_LABEL = {
    "oracle_write": "write_error",
    "oracle_compression": "compression_error",
    "verbatim_event_oracle": "premature_extraction_error",
    "oracle_retrieval": "retrieval_error",
    "injection_oracle": "injection_error",
    "evidence_given_reasoning": "reasoning_error",
    "oracle_route": "route_error",
}
```

目的：

- 将每个 V1 回放名称映射到其默认 V1 管道标签。
- `oracle_write` → `write_error` 是**默认**映射；当 `has_ingestion_trace` 为 `False` 时，`_v1_label_for_replay` 会覆盖为 `ingestion_error`。
- `oracle_route` → `route_error` 是 V1 新增的映射。

与 `REPLAY_TO_LABEL` 的关系：

- `V1_REPLAY_TO_LABEL` 是 `REPLAY_TO_LABEL` 的字典超集（相同的 6 个 V0 条目 + 1 个新条目）。
- V0 使用 `REPLAY_TO_LABEL`（仅 6 个键）。V1 使用 `V1_REPLAY_TO_LABEL`（7 个键）。

调用者：

- `_v1_label_for_replay`（`attribution.py:98-99`）
- `V1ReplayToLabelMappingTest` 中的所有测试

---

#### 常量：`DEFERRED_PIPELINE_LABELS`（已更新）

位置：`cmd_audit/labels.py:39-44`

```python
DEFERRED_PIPELINE_LABELS = frozenset(
    {
        "granularity_error",
        "graph_error",
        "safety_error",
    }
)
```

目的：

- 列出为 future issues 保留的管道标签（V2：issue 0012）。
- `ingestion_error` 和 `route_error` 已从此集合中移除——它们现在是活动 V1 标签。
- `validate_v0_label` 和 `validate_v1_label` 均使用此集合来拒绝带有适当版本范围消息的延迟标签。

变更自 V0：

| 标签 | V0 状态 | V1 状态 |
| --- | --- | --- |
| `ingestion_error` | 延迟（被 `validate_v0_label` 拒绝） | 活动（被 `validate_v1_label` 接受） |
| `route_error` | 延迟（被 `validate_v0_label` 拒绝） | 活动（被 `validate_v1_label` 接受） |
| `granularity_error` | 延迟 | 延迟（V2） |
| `graph_error` | 延迟 | 延迟（V2） |
| `safety_error` | 延迟 | 延迟（V2） |

---

#### 函数：`validate_v1_label(label: str) -> str`

位置：`cmd_audit/labels.py:114-127`

```python
def validate_v1_label(label: str) -> str:
    """Return a valid V1 pipeline label or raise with the boundary reason."""

    if label in V1_PIPELINE_LABELS:
        return label
    if label in OUT_OF_SCOPE_ITEM_LABELS:
        raise LabelValidationError(
            f"{label!r} is a bad memory item label and is outside V1 attribution scope"
        )
    if label in DEFERRED_PIPELINE_LABELS:
        raise LabelValidationError(
            f"{label!r} is deferred to V2 and is outside V1 attribution scope"
        )
    raise LabelValidationError(f"{label!r} is not a CMD-Audit V1 attribution label")
```

目的：

- V1 归因范围的标签边界守卫。接受 8 个 V1 管道标签，拒绝项目标签和延迟标签。
- 与 `validate_v0_label` 的对称对应，后者仅接受 6 个 V0 标签。

行为：

1. 检查 `V1_PIPELINE_LABELS`（8 个标签）——如果匹配则返回。
2. 检查 `OUT_OF_SCOPE_ITEM_LABELS`——如果匹配则抛出 `LabelValidationError`（项目标签）。
3. 检查 `DEFERRED_PIPELINE_LABELS`（3 个标签：`granularity_error`、`graph_error`、`safety_error`）——如果匹配则抛出并以 "deferred to V2" 作为消息。
4. 否则抛出，并以通用未知标签错误作为消息。

调用者：

- `ProbeCase.from_mapping_v1`（`models.py:162`）
- `assign_attribution_v1`（`attribution.py:69, 73`）
- `get_targeted_repair_action_v1`（`repairs.py:121`）
- `TargetedRepairAction.__post_init__`（`repairs.py:23`）
- `ECSDraft.__post_init__`（`post_repair.py:79`）
- `DiagnosisPrediction.__post_init__`（`metrics.py:22-25`）

---

#### 函数：`validate_v0_label(label: str) -> str`（已更新）

位置：`cmd_audit/labels.py:94-111`

```python
def validate_v0_label(label: str) -> str:
    """Return a valid V0 pipeline label or raise with the boundary reason."""

    if label in V0_PIPELINE_LABELS:
        return label
    if label in OUT_OF_SCOPE_ITEM_LABELS:
        raise LabelValidationError(...)
    if label in DEFERRED_PIPELINE_LABELS:
        raise LabelValidationError(...)
    if label in V1_PIPELINE_LABELS:                              # ← 新增
        raise LabelValidationError(                               # ← 新增
            f"{label!r} is a V1 pipeline label and is outside V0 attribution scope"
        )
    raise LabelValidationError(f"{label!r} is not a CMD-Audit V0 attribution label")
```

行为变更：

- 新增第 4 个检查（第 107-110 行）：如果 `label` 在 `V1_PIPELINE_LABELS` 中但不在 `V0_PIPELINE_LABELS` 或 `DEFERRED_PIPELINE_LABELS` 中（即 `ingestion_error` 或 `route_error`），抛出并给出区分性消息，指出它是一个 V1 标签，超出 V0 范围。
- 此检查在延迟检查**之后**进行，因为 `ingestion_error` 和 `route_error` 已从 `DEFERRED_PIPELINE_LABELS` 中移除。
- 现有的三个检查保持不变。

### `cmd_audit/replays.py`

文件：`cmd_audit/replays.py`（222 行）。Issue 0011 新增：1 个公共回放函数、1 个公共组合函数、2 个私有辅助函数。

---

#### 函数：`run_v1_replay_portfolio(case: ProbeCase) -> tuple[ReplayResult, ...]`

位置：`cmd_audit/replays.py:35-46`

```python
def run_v1_replay_portfolio(case: ProbeCase) -> tuple[ReplayResult, ...]:
    """Run the V1 replay portfolio (V0 6 + oracle_route) for one case."""

    return (
        run_oracle_write(case),
        run_oracle_compression(case),
        run_verbatim_event_oracle(case),
        run_oracle_retrieval(case),
        run_injection_oracle(case),
        run_evidence_given_reasoning(case),
        run_oracle_route(case),
    )
```

目的：

- 运行完整的 7 回放 V1 组合（6 个 V0 回放 + `oracle_route`）。
- 元组顺序很重要：`assign_attribution_v1` 使用 Python 的稳定排序，当 recovery_gain 值相等时，首先列出的获胜。

回放顺序与归因影响：

| 位置 | 回放 | 恢复的内容 | 当具有最高且唯一的 recovery_gain 时分配的标签 |
| --- | --- | --- | --- |
| 1 | `oracle_write` | 未写入或从未摄入的证据 | `write_error` 或 `ingestion_error`（取决于 `has_ingestion_trace`） |
| 2 | `oracle_compression` | 通过有损压缩丢失的证据 | `compression_error` |
| 3 | `verbatim_event_oracle` | 在提取过程中丢失的原始事件证据 | `premature_extraction_error` |
| 4 | `oracle_retrieval` | 存在但未检索的证据 | `retrieval_error` |
| 5 | `injection_oracle` | 检索到但注入错误的证据 | `injection_error` |
| 6 | `evidence_given_reasoning` | 已注入但推理错误的证据 | `reasoning_error` |
| 7 | `oracle_route` | 错误存储/层级中的证据 | `route_error` |

调用者：

- `run_case_v1`（`harness.py:197`）
- `V1ReplayPortfolioTest` 中的所有测试
- `IngestionErrorAttributionTest` 中的所有测试

---

#### 函数：`run_oracle_route(case: ProbeCase) -> ReplayResult`

位置：`cmd_audit/replays.py:132-150`

```python
def run_oracle_route(case: ProbeCase) -> ReplayResult:
    """Replay by testing retrieval from each available store/tier.

    This intervention diagnoses `route_error`: correct memory exists but was stored
    in a store/tier the baseline retrieval did not access. Enumerates all stores,
    picks the one with the best evidence recovery.
    """
    stores = _collect_stores(case)
    best_score = -1.0
    best_block = ""

    for store in stores:
        evidence_block = _recover_from_store(case, store)
        score = evidence_recall_from_text(case.gold_evidence, evidence_block)
        if score > best_score:
            best_score = score
            best_block = evidence_block

    return _score_recovered_evidence(case, "oracle_route", best_block)
```

目的：

- 通过枚举所有可用存储/层级并选择具有最佳证据恢复的存储/层级，诊断 `route_error`。
- 设计上与其他回放同构：它产生一个带有 `replay_name="oracle_route"` 的标准 `ReplayResult`，供归因层使用。

行为：

1. 调用 `_collect_stores(case)` 以获取所有唯一存储名称。
2. 对于每个存储，调用 `_recover_from_store(case, store)` 以获取证据块。
3. 通过 `evidence_recall_from_text` 对每个存储的证据块进行评分。
4. 跟踪 `best_score` 和 `best_block`。
5. 通过 `_score_recovered_evidence` 返回带有最佳存储证据块的 `ReplayResult`。

领域边界：

- 如果最佳存储的证据分数 ≤ 0，则 `_score_recovered_evidence` 产生一个 `recovery_gain = 0` 的 `ReplayResult`（证据块为空，答案分数为 0.0，与基线答案分数匹配）。
- 如果最佳存储的证据分数 == 1.0，则答案设置为 `case.gold_answer`，并且 `recovery_gain = 1.0 - baseline.answer_score`。
- 在独立工具中，`oracle_retrieval` 也可能恢复相同的证据（因为它搜索所有记忆项，无论存储如何）。这可能会导致平局的 recovery_gain，`oracle_retrieval` 由于元组顺序而获胜。在实际的层级系统中，存储具有不同的 API，因此区分很清楚。

调用者：

- `run_v1_replay_portfolio`（`replays.py:45`）
- `OracleRouteReplayTest`（直接测试）

---

#### 私有函数：`_collect_stores(case: ProbeCase) -> list[str]`

位置：`cmd_audit/replays.py:153-160`

```python
def _collect_stores(case: ProbeCase) -> list[str]:
    stores: list[str] = []
    seen: set[str] = set()
    for item in case.extracted_memory:
        if item.store not in seen:
            seen.add(item.store)
            stores.append(item.store)
    return stores
```

目的：

- 从案例的 `extracted_memory` 中收集唯一存储名称，保留首次出现顺序。
- 使用 `seen` 集合进行 O(1) 去重，使用 `stores` 列表进行稳定迭代顺序。

返回的存储示例：

| 案例 | extracted_memory 中的存储 | 返回 |
| --- | --- | --- |
| V0 烟雾案例 | 所有记忆项 store="default" | `["default"]` |
| v1-route-001 | mem-701 store="episodic"，mem-702 store="semantic" | `["episodic", "semantic"]` |
| 多存储案例 | 混合存储 | 首次出现顺序中的唯一存储 |

调用者：

- `run_oracle_route`（`replays.py:139`）

---

#### 私有函数：`_recover_from_store(case: ProbeCase, store: str) -> str`

位置：`cmd_audit/replays.py:163-176`

```python
def _recover_from_store(case: ProbeCase, store: str) -> str:
    memory_by_id = {item.memory_id: item for item in case.extracted_memory}
    recovered = []
    for evidence in case.gold_evidence:
        if not evidence.source_memory_id:
            continue
        memory = memory_by_id.get(evidence.source_memory_id)
        if memory is None:
            continue
        if memory.store != store:
            continue
        if evidence_recall_from_text((evidence,), memory.text) >= 1.0:
            recovered.append(memory.text)
    return "\n".join(recovered)
```

目的：

- 从属于指定存储的记忆项中恢复证据文本。
- 对于每个金标证据片段：仅当 (a) 具有 `source_memory_id`，(b) 记忆项存在，(c) 记忆项的 `store` 与目标存储完全匹配，且 (d) 记忆项的文本包含金标证据短语时，才包括记忆项的文本。

行为：

1. 通过 ID 构建记忆项查找映射。
2. 对于每个金标证据片段：
   - 如果没有 `source_memory_id` 则跳过（证据未存储在记忆项中）。
   - 通过 ID 查找记忆项；如果未找到则跳过。
   - 如果 `memory.store != store` 则跳过——这将恢复限制在目标存储。
   - 检查记忆项文本是否包含证据短语（`evidence_recall_from_text >= 1.0`）。
   - 如果所有检查通过，将 `memory.text` 追加到恢复列表。
3. 返回通过换行符连接的恢复文本。

调用者：

- `run_oracle_route`（`replays.py:144`）

### `cmd_audit/attribution.py`

文件：`cmd_audit/attribution.py`（102 行）。Issue 0011 新增：1 个公共函数、1 个私有函数。

---

#### 函数：`assign_attribution_v1(replay_results, *, has_ingestion_trace, positive_gain_threshold, tie_margin) -> AttributionResult`

位置：`cmd_audit/attribution.py:49-85`

```python
def assign_attribution_v1(
    replay_results: tuple[ReplayResult, ...],
    *,
    has_ingestion_trace: bool = True,
    positive_gain_threshold: float = 0.0,
    tie_margin: float = 0.05,
) -> AttributionResult:
    """V1 attribution with ingestion/write split and route_error support.

    When oracle_write is the top replay and `has_ingestion_trace` is False,
    the label is ``ingestion_error`` instead of ``write_error``.
    """
    if not replay_results:
        raise ValueError("at least one replay result is required")

    ranked = sorted(replay_results, key=lambda result: result.recovery_gain, reverse=True)
    top = ranked[0]
    if top.recovery_gain <= positive_gain_threshold:
        raise ValueError("no replay produced a positive recovery gain")

    predicted_label = validate_v1_label(
        _v1_label_for_replay(top.replay_name, has_ingestion_trace=has_ingestion_trace)
    )
    close = [
        validate_v1_label(
            _v1_label_for_replay(result.replay_name, has_ingestion_trace=has_ingestion_trace)
        )
        for result in ranked
        if top.recovery_gain - result.recovery_gain <= tie_margin
    ][:2]
    return AttributionResult(
        predicted_label=predicted_label,
        top_replay=top.replay_name,
        recovery_gain=top.recovery_gain,
        top2_labels=tuple(close),
        is_ambiguous=len(close) > 1,
    )
```

目的：

- 执行 V1 操作级归因，支持 8 个标签。
- 唯一的语义差异来自 `has_ingestion_trace` 参数：当为 `False` 且 oracle_write 是最高回放时，标签为 `ingestion_error` 而非 `write_error`。
- 否则，结构上与 `assign_attribution` 相同（相同的排名逻辑、相同的平局边际、相同的 `AttributionResult` 输出形状）。

与 `assign_attribution` 的差异：

| 方面 | `assign_attribution` | `assign_attribution_v1` |
| --- | --- | --- |
| 标签映射 | `_label_for_replay` → `REPLAY_TO_LABEL`（静态，6 个标签） | `_v1_label_for_replay` → `V1_REPLAY_TO_LABEL` + 摄入覆盖（7 个回放，8 个标签） |
| 标签校验 | `validate_v0_label` | `validate_v1_label` |
| `has_ingestion_trace` | 不适用 | 用于 oracle_write → ingestion_error 覆盖 |
| 回放数量 | 6 | 7（V0 6 + oracle_route） |

**摄入/写入拆分决策表：**

| top_replay | has_ingestion_trace | 分配的标签 | 基本原理 |
| --- | --- | --- | --- |
| `oracle_write` | `False` | `ingestion_error` | 证据存在但从未来到代理——摄入失败 |
| `oracle_write` | `True` | `write_error` | 证据到达代理但未被写入记忆——写入失败 |
| `oracle_route` | 不适用 | `route_error` | 证据存在但在错误的存储/层级中 |
| 任何其他 | 不适用 | 来自 `V1_REPLAY_TO_LABEL` 的静态映射 | 标准 V0 标签 |

调用者：

- `run_case_v1`（`harness.py:198-199`）
- `IngestionErrorAttributionTest`（直接测试）

---

#### 私有函数：`_v1_label_for_replay(replay_name: str, *, has_ingestion_trace: bool) -> str`

位置：`cmd_audit/attribution.py:95-101`

```python
def _v1_label_for_replay(replay_name: str, *, has_ingestion_trace: bool) -> str:
    if replay_name == "oracle_write" and not has_ingestion_trace:
        return "ingestion_error"
    try:
        return V1_REPLAY_TO_LABEL[replay_name]
    except KeyError as exc:
        raise ValueError(f"unknown replay {replay_name!r}") from exc
```

目的：

- 将回放名称解析为 V1 标签，动态处理摄入/写入拆分。
- `oracle_write` 是唯一具有条件行为的回放：当 `has_ingestion_trace=False` 时，它解析为 `ingestion_error`；否则解析为 `write_error`（默认）。
- 所有其他回放无条件映射：`V1_REPLAY_TO_LABEL[replay_name]`。

决策逻辑：

```text
if replay_name == "oracle_write" AND not has_ingestion_trace:
    return "ingestion_error"
else:
    return V1_REPLAY_TO_LABEL[replay_name]
```

调用者：

- `assign_attribution_v1`（`attribution.py:70, 73`）

### `cmd_audit/harness.py`

文件：`cmd_audit/harness.py`（236 行）。Issue 0011 新增：4 个公共 V1 流水线函数（第 194-235 行）。

---

#### 函数：`run_case_v1(case: ProbeCase) -> AuditResult`

位置：`cmd_audit/harness.py:194-211`

```python
def run_case_v1(case: ProbeCase) -> AuditResult:
    """Run the V1 pipeline: 8-replay portfolio + V1 attribution."""
    baseline_suite = run_baseline_suite(case)
    replays = run_v1_replay_portfolio(case)
    attribution = assign_attribution_v1(
        replays, has_ingestion_trace=case.has_ingestion_trace
    )
    baseline = case.primary_baseline
    return AuditResult(
        case_id=case.case_id,
        perturbation_label=case.perturbation_label,
        baseline_name=baseline.baseline_name,
        baseline_answer_score=baseline.answer_score,
        baseline_evidence_score=baseline.evidence_score,
        replays=replays,
        attribution=attribution,
        baseline_suite=baseline_suite,
    )
```

目的：

- 单个探针案例的 V1 流水线入口点。
- 与 `run_case` 的结构相同，但使用 `run_v1_replay_portfolio`（7 个回放）和 `assign_attribution_v1`（V1 归因，摄入/写入拆分）。
- 返回与 `run_case` 相同的 `AuditResult` 类型——`AuditResult` 对 V0 和 V1 标签没有区别。

差异自 `run_case`：

| 步骤 | `run_case` | `run_case_v1` |
| --- | --- | --- |
| 回放 | `run_v0_replay_portfolio`（6 个回放） | `run_v1_replay_portfolio`（7 个回放） |
| 归因 | `assign_attribution(replays)` | `assign_attribution_v1(replays, has_ingestion_trace=...)` |
| 标签校验 | `validate_v0_label`（在归因内部） | `validate_v1_label`（在归因内部） |

调用者：

- `run_cases_v1`（`harness.py:215`）
- `run_case_full_v1`（`harness.py:220`）
- `V1NonRegressionTest`（直接测试）

---

#### 函数：`run_cases_v1(cases: list[ProbeCase]) -> list[AuditResult]`

位置：`cmd_audit/harness.py:214-215`

```python
def run_cases_v1(cases: list[ProbeCase]) -> list[AuditResult]:
    return [run_case_v1(case) for case in cases]
```

目的：`run_case_v1` 的批量版本。与 `run_cases` 的结构相同。

---

#### 函数：`run_case_full_v1(case: ProbeCase) -> FullAuditResult`

位置：`cmd_audit/harness.py:218-231`

```python
def run_case_full_v1(case: ProbeCase) -> FullAuditResult:
    """Run the complete V1 pipeline: attribution -> ECS -> repair -> post-repair replay."""
    audit = run_case_v1(case)
    ecs_draft = draft_ecs(case, audit)
    repaired_context = build_repaired_context(case, ecs_draft)
    post_repair = run_post_repair_context_replay(case, repaired_context)
    hard_case_baseline = run_hard_case_update_baseline(case)
    return FullAuditResult(
        audit=audit,
        ecs_draft=ecs_draft,
        repaired_context=repaired_context,
        post_repair=post_repair,
        hard_case_baseline=hard_case_baseline,
    )
```

目的：

- 完整的 V1 流水线：归因 → ECS → 修复 → 修复后回放。
- 与 `run_case_full` 的结构相同，但使用 `run_case_v1` 作为归因步骤。
- ECS 草拟路径现在通过 `get_targeted_repair_action_v1` 处理 V1 标签（`ingestion_error`、`route_error`），该函数在 `_ecs_for_label`（`post_repair.py:218`）内部调用。

关键流水线事实：

- `draft_ecs` 和 `build_repaired_context` 对 V0 和 V1 标签统一工作，因为 `ECSDraft.__post_init__` 已更新为使用 `validate_v1_label`，且 `_ecs_for_label` 已更新为使用 `get_targeted_repair_action_v1`。
- `run_post_repair_context_replay` 和 `run_hard_case_update_baseline` 不变——它们对修复后评分使用相同的 `answer_score` 和 `evidence_recall_from_text`。

调用者：

- `run_cases_full_v1`（`harness.py:235`）
- `V1ECSCompatibilityTest`（直接测试）

---

#### 函数：`run_cases_full_v1(cases: list[ProbeCase]) -> list[FullAuditResult]`

位置：`cmd_audit/harness.py:234-235`

```python
def run_cases_full_v1(cases: list[ProbeCase]) -> list[FullAuditResult]:
    return [run_case_full_v1(case) for case in cases]
```

目的：`run_case_full_v1` 的批量版本。与 `run_cases_full` 的结构相同。

### `cmd_audit/models.py`

文件：`cmd_audit/models.py`（233 行）。Issue 0011 新增：`MemoryItem` 上的 `store` 字段、`ProbeCase` 上的 `has_ingestion_trace` 和 `default_store` 字段、`from_mapping_v1` 类方法、`load_probe_cases_v1` 函数。

---

#### 数据类：`MemoryItem`（已更新）

位置：`cmd_audit/models.py:28-41`

```python
@dataclass(frozen=True)
class MemoryItem:
    memory_id: str
    text: str
    source_event_ids: tuple[str, ...] = ()
    store: str = "default"                                    # ← 新增

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "MemoryItem":
        return cls(
            memory_id=_required_str(value, "memory_id"),
            text=_required_str(value, "text"),
            source_event_ids=tuple(value.get("source_event_ids", ())),
            store=str(value.get("store", "default")),          # ← 新增
        )
```

新增字段 `store`：

- 默认值 `"default"` 确保向后兼容：所有现有 V0 夹具无需更改。
- 由 `_collect_stores`（`replays.py:157`）和 `_recover_from_store`（`replays.py:172`）使用。
- 在 V1 路由案例中：记忆项设置了显式的存储值（`"episodic"`、`"semantic"`）。

---

#### 数据类：`ProbeCase`（已更新）

位置：`cmd_audit/models.py:99-168`

新增字段：

```python
has_ingestion_trace: bool = True                               # 第 117 行
default_store: str = "episodic"                               # 第 118 行
```

| 字段 | 默认值 | 目的 |
| --- | --- | --- |
| `has_ingestion_trace` | `True` | 当金标证据从未进入代理的摄入管道时为 `False`。驱动 `assign_attribution_v1` 中的摄入/写入拆分。 |
| `default_store` | `"episodic"` | 基线检索的默认存储。由路由感知适配器用于确定默认路由。 |

新增类方法：

```python
@classmethod
def from_mapping_v1(cls, value: dict[str, Any]) -> "ProbeCase":  # 第 144-168 行
    from .labels import validate_v1_label

    case = cls(
        ...                                                       # 与 from_mapping 相同的字段
        perturbation_label=validate_v1_label(_required_str(value, "perturbation_label")),
        ...
        has_ingestion_trace=bool(value.get("has_ingestion_trace", True)),
        default_store=str(value.get("default_store", "episodic")),
    )
    case.validate()
    return case
```

与 `from_mapping` 的差异：

- 使用 `validate_v1_label` 而非 `validate_v0_label` 用于 `perturbation_label` 校验。
- 延迟导入 `validate_v1_label` 以避免循环依赖。
- 所有其他字段的解析相同。

新增函数：

```python
def load_probe_cases_v1(path: str | Path) -> list[ProbeCase]:    # 第 212-222 行
```

- `load_probe_cases` 的镜像，但调用 `ProbeCase.from_mapping_v1` 而非 `from_mapping`。
- 接受 `perturbation_label` 为 V1 标签（8 个选项）的夹具。
- `load_probe_cases` 保持不变——对 `ingestion_error` 或 `route_error` 的夹具仍然抛出 `LabelValidationError`。

### `cmd_audit/repairs.py`

文件：`cmd_audit/repairs.py`（477 行）。Issue 0011 变更：更新 `TargetedRepairAction.__post_init__`，扩展 `REPAIR_ACTION_BY_LABEL`，新增 `get_targeted_repair_action_v1`。

---

#### 数据类：`TargetedRepairAction`（已更新）

位置：`cmd_audit/repairs.py:13-23`

```python
@dataclass(frozen=True)
class TargetedRepairAction:
    ...
    def __post_init__(self) -> None:
        validate_v1_label(self.label)                           # 原来是 validate_v0_label
```

行为变更：

- 现在接受 V0 和 V1 标签（通过 `validate_v1_label`）。
- 现有 V0 修复操作（`write_error` 等）不受影响——`validate_v1_label` 接受所有 V0 标签作为 V1 标签的子集。

---

#### 字典：`REPAIR_ACTION_BY_LABEL`（已扩展）

位置：`cmd_audit/repairs.py:26-110`

新增条目（第 88-110 行）：

```python
REPAIR_ACTION_BY_LABEL.update(
    {
        "ingestion_error": TargetedRepairAction(
            label="ingestion_error",
            action_name="Oracle Write Repair",
            description="Inject gold evidence directly into memory; ingestion pipeline missed it.",
            intervention_summary="Oracle Write recovers evidence the agent never received.",
            cause="gold evidence never reached the agent through the ingestion pipeline; "
                  "the write step could not store what it never received",
            repair_guidance="verify ingestion pipeline is receiving and forwarding all "
                            "relevant evidence to the write step",
        ),
        "route_error": TargetedRepairAction(
            label="route_error",
            action_name="Oracle Route Repair",
            description="Route evidence through the correct store/tier for retrieval.",
            intervention_summary="Oracle Route recovers evidence stored in the wrong store/tier.",
            cause="evidence was stored in a store that the baseline retrieval did not query",
            repair_guidance="update routing logic to store evidence in the correct store "
                            "or expand retrieval to query all relevant stores",
        ),
    }
)
```

领域含义：

- `ingestion_error` 和 `write_error` 共享相同的 `action_name`（"Oracle Write Repair"）和 `intervention_summary`（"Oracle Write recovers..."），因为两者都使用相同的回放干预。差异在于 `cause`（摄入失败 vs 写入失败）和 `repair_guidance`（修复摄入管道 vs 修复写入步骤）。
- `route_error` 是一个独特的修复操作，具有 Oracle Route 特定的干预总结和路由修复指导。

---

#### 函数：`get_targeted_repair_action_v1(label: str) -> TargetedRepairAction`

位置：`cmd_audit/repairs.py:119-122`

```python
def get_targeted_repair_action_v1(label: str) -> TargetedRepairAction:
    """Return the targeted repair action for a V1 attribution label."""
    validate_v1_label(label)
    return REPAIR_ACTION_BY_LABEL[label]
```

目的：

- `get_targeted_repair_action` 的 V1 对应版本。使用 `validate_v1_label` 接受截至 V1 的所有 8 个活动管道标签。
- `get_targeted_repair_action` 保持不变（仅 V0，6 个标签）——用于 V0 特定的修复流水线路径。

调用者：

- `_ecs_for_label`（`post_repair.py:218`）

### `cmd_audit/post_repair.py`

文件：`cmd_audit/post_repair.py`。Issue 0011 变更：更新 ECS 草拟以支持 V1 标签（2 处变更）。

---

#### 数据类：`ECSDraft`（已更新）

位置：`cmd_audit/post_repair.py:67-80`

```python
def __post_init__(self) -> None:
    validate_v1_label(self.predicted_label)                    # 原来是 validate_v0_label
    _validate_ecs_cause(self.cause)
```

行为变更：

- `predicted_label` 校验从 `validate_v0_label` 扩展为 `validate_v1_label`。
- 现在接受截至 V1 的所有 8 个管道标签。
- `_validate_ecs_cause` 保持不变——ECS cause 禁止规则是版本无关的。

---

#### 私有函数：`_ecs_for_label`（已更新）

位置：`cmd_audit/post_repair.py:214-219`

```python
def _ecs_for_label(case, predicted_label: str, replay) -> tuple[str, str, str]:
    """Return (cause, corrected_memory, repair_guidance) for a predicted label."""
    from .repairs import get_targeted_repair_action_v1           # 原来是 get_targeted_repair_action

    action = get_targeted_repair_action_v1(predicted_label)      # 原来是 get_targeted_repair_action
    return (action.cause, replay.evidence_block, action.repair_guidance)
```

行为变更：

- 现在使用 `get_targeted_repair_action_v1`，它接受 V1 标签（`ingestion_error`、`route_error`）。
- `get_targeted_repair_action` 将拒绝 V1 标签（它使用 `validate_v0_label`）。
- 返回元组形状不变：`(cause, corrected_memory, repair_guidance)`。

### `cmd_audit/metrics.py`

文件：`cmd_audit/metrics.py`。Issue 0011 变更：更新诊断预测以接受 V1 标签。

---

#### 数据类：`DiagnosisPrediction`（已更新）

位置：`cmd_audit/metrics.py:12-25`

```python
def __post_init__(self) -> None:
    validate_v1_label(self.gold_label)                         # 原来是 validate_v0_label
    validate_v1_label(self.predicted_label)                    # 原来是 validate_v0_label
    for label in self.top2_labels:
        validate_v1_label(label)                               # 原来是 validate_v0_label
```

行为变更：

- 所有三个标签校验调用从 `validate_v0_label` 切换为 `validate_v1_label`。
- 这允许诊断指标计算接受带有 V1 标签的 `DiagnosisPrediction` 实例（例如包含 `ingestion_error` 或 `route_error` 的 `top2_labels`）。
- 向后兼容：`validate_v1_label` 接受所有 6 个 V0 标签。

## 测试结构

文件：`tests/test_cmd_audit_issue11_v1_labels.py`。9 个测试类，44 个测试方法。

| 测试类 | 测试方法数 | 覆盖的 TDD 行为 |
| --- | --- | --- |
| `V1LabelValidationTest` | 15 | `validate_v1_label` 接受全部 8 个 V1 标签、V0 子集，拒绝项目标签和延迟标签；`validate_v0_label` 拒绝 `ingestion_error` 和 `route_error`；延迟标签注册表正确性；`V1_REPLAY_TO_LABEL` 与 `REPLAY_TO_LABEL` 的关系 |
| `V1ProbeCaseLoadingTest` | 5 | `load_probe_cases_v1` 加载摄入和路由夹具；V0 加载器拒绝 V1 夹具；记忆项存储字段已正确反序列化 |
| `IngestionErrorAttributionTest` | 6 | `has_ingestion_trace=false` → `ingestion_error`；`has_ingestion_trace=true` → `write_error`；所有 V0 案例的 `has_ingestion_trace` 默认为 `True`；通过 V1 流水线，没有 V0 案例获得 `ingestion_error` |
| `OracleRouteReplayTest` | 5 | `oracle_route` 回放名称、证据分数、recovery_gain、来自语义存储的证据块内容、对 V0 写入案例的零恢复 |
| `V1ReplayPortfolioTest` | 5 | 7 个回放计数；包含 `oracle_route`；包含所有 V0 回放；每个回放都有标签映射；`oracle_route` 映射到 `route_error` |
| `V1NonRegressionTest` | 4 | 所有 6 个 V0 案例的预测标签通过 V1 流水线匹配；没有 V0 案例获得 `route_error`；没有 V0 案例获得 `ingestion_error`；完整的 6 标签集通过 V1 产生 |
| `V1ReplayToLabelMappingTest` | 3 | 所有 `V1_REPLAY_TO_LABEL` 值都是有效的 V1 标签；V1 映射是 V0 的超集；`oracle_route` 仅在 V1 中 |
| `V1ECSCompatibilityTest` | 2 | 完整 V1 流水线为摄入案例产生 ECS；完整 V1 流水线为路由案例产生 ECS |

## 非回归分析

对 V0 烟雾套件（`data/probe_cases/v0_issue3_cases.json`，6 个案例）运行 V1 流水线产生：

| 案例 ID | V0 标签 | V1 预测标签 | 匹配？ | 备注 |
| --- | --- | --- | --- | --- |
| v0-write-001 | `write_error` | `write_error` | ✓ | `has_ingestion_trace=True`（默认）——没有拆分 |
| v0-compression-001 | `compression_error` | `compression_error` | ✓ | oracle_route 恢复增益 = 0（被压缩的记忆项文本缺少证据短语） |
| v0-premature-extraction-001 | `premature_extraction_error` | `premature_extraction_error` | ✓ | oracle_route 恢复增益 = 0（金标证据没有 `source_memory_id`） |
| v0-retrieval-001 | `retrieval_error` | `retrieval_error` | ✓ | oracle_route 平局但 `oracle_retrieval` 在元组中先出现 |
| v0-injection-001 | `injection_error` | `injection_error` | ✓ | oracle_route 平局但 `injection_oracle` 在元组中先出现 |
| v0-reasoning-001 | `reasoning_error` | `reasoning_error` | ✓ | oracle_route 平局但 `evidence_given_reasoning` 在元组中先出现 |

所有 6 个 V0 标签保持不变（Macro F1 = 1.000 在烟雾套件上）。没有案例翻转其预测标签。`ingestion_error` 和 `route_error` 都不作为任何 V0 案例的预测标签出现。

对于具有平面记忆的独立工具，oracle_route 偶尔会与另一个回放（`oracle_retrieval`、`injection_oracle`、`evidence_given_reasoning`）具有相同的 recovery_gain——这会使 `is_ambiguous` 对一些 V0 案例变为 `True`（当在 V1 下运行时），但预测标签（元组中先出现的那个）从不改变。
