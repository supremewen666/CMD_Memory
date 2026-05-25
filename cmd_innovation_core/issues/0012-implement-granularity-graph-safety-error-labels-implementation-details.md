# Issue 0012 实现细节：granularity_error、graph_error 与 safety_error 标签

## 目的

本文档是 issue 0012《实现 granularity_error、graph_error 与 safety_error 标签》的全局实现地图。它映射每个函数、数据类、辅助函数、异常和常量到其确切的源码位置、签名、行为、调用者和领域含义。

Issue 0012 将 V1 管道从 8 标签扩展为 11 标签，新增三个标签并保持完全的非回归：

```text
V1 8-Label Pipeline (issue 0011)
  ProbeCase -> run_case_v1 -> run_v1_replay_portfolio (7 replays)
           -> assign_attribution_v1 (has_ingestion_trace 参数)
           -> AuditResult

V1 11-Label Pipeline (issue 0012)
  ProbeCase -> run_case_v1 -> run_v1_replay_portfolio (10 replays)
           -> assign_attribution_v1 (has_ingestion_trace 参数)
           -> AuditResult

granularity_error:
  oracle_granularity recovery_gain > 0 AND 最佳粒度 ≠ 当前粒度
    -> "granularity_error" (记忆在次优粒度级别上表达导致证据丢失)

graph_error:
  graph_off recovery_gain > 0 AND 存在 graph-expanded 记忆项
    -> "graph_error" (图扩展引入了覆盖直接证据的干扰项)

safety_error:
  safety_off recovery_gain > 0 AND safety_filter_blocked == True
    -> "safety_error" (安全过滤器阻挡了产生正确答案所必需的有效证据)
```

该切片交付 `cmd_tracer_bullets.md` 中的 TDD Cycle 17：

| 周期 | 标题 | 状态 |
| --- | --- | --- |
| Cycle 17 | Granularity + Graph + Safety Error Labels | 绿色 |

Issue 0012 在 issue 0011 的 V1 标签注册表（8 标签、7 回放）之上构建，将 V1 提升至 11 标签、10 回放。`DEFERRED_PIPELINE_LABELS` 现在为空——所有规划的 V1 管道标签均已激活。

## 源需求

本实现遵循以下本地文档。

| 来源 | 在 Issue 0012 中应用的需求 |
| --- | --- |
| `TASK.md` | V1 标签扩展完成：`granularity_error`、`graph_error`、`safety_error` 从延迟移至活动。Oracle Granularity 回放枚举粒度级别并选择最佳恢复。Graph-Off 回放禁用图扩展。Safety-Off 回放绕过安全过滤器。非回归：8 标签烟雾套件通过 11 标签 V1 管道产生相同标签。 |
| `CLAUDE.md` | V1 扩展顺序：issue 0011（`ingestion_error` + `route_error`）→ issue 0012（`granularity_error` + `graph_error` + `safety_error`）。V1 活动标签：11 个管道标签，10 回放 V1 组合。V0 函数拒绝 V1 标签。`DEFERRED_PIPELINE_LABELS` 为空。 |
| `cmd_innovation_core/CONTEXT.md` | V1 Essentials：三种新标签覆盖粒度、图扩展和安全过滤器故障模式。`granularity_error`：在错误的粒度级别表达的证据。`graph_error`：图扩展干扰掩盖正确证据。`safety_error`：安全过滤器阻挡有效证据。 |
| `cmd_innovation_core/prd/cmd_minimal_probe_prd.md` | User Story 39（`granularity_error` 标签）、User Story 40（`graph_error` 标签）、User Story 41（`safety_error` 标签）。V1 Scope：11 标签归因表。 |
| `cmd_innovation_core/issues/0012-implement-granularity-graph-safety-error-labels.md` | 七个验收标准：Oracle Granularity 回放枚举级别并在最佳粒度 ≠ 原始粒度时归因；Graph-Off 回放禁用图扩展并在 recovery_gain > 0 时归因；Safety-Off 回放绕过安全过滤器并在 recovery_gain > 0 时归因；所有 11 个标签通过 `validate_v1_label()`；延迟注册表清空；V0 6 标签 + 0011 2 标签烟雾无回归；每个新标签至少一个行为级烟雾案例。 |

## 领域边界

Issue 0012 在现有 V0 归因（issues 0001-0003）、V1 标签层（issue 0011）、基线（issue 0002）、修复验证（issue 0005）、针对性修复（issue 0006）和版本关卡（issue 0010）之上构建最终的 V1 标签层。它不更改任何现有的 V0 回放逻辑、V1 回放逻辑（`oracle_route`）或归因逻辑（`assign_attribution_v1` 内部）。它扩展标签注册表、回放组合和修复操作字典。

```text
V0 Pipeline（现有，未更改）
  run_case(ProbeCase) -> run_v0_replay_portfolio -> assign_attribution -> AuditResult

V1 8-Label Pipeline（issue 0011，未更改核心逻辑）
  run_case_v1(ProbeCase) -> run_v1_replay_portfolio (7 replays)
                         -> assign_attribution_v1 -> AuditResult

V1 11-Label Pipeline（issue 0012）
  run_case_v1(ProbeCase) -> run_v1_replay_portfolio (10 replays)
                         -> assign_attribution_v1 -> AuditResult
```

Issue 0012 拥有的内容：

- 更新 `V1_PIPELINE_LABEL_ORDER`（11 个标签）和 `V1_PIPELINE_LABELS`（frozenset）。
- 更新 `V1_REPLAY_TO_LABEL`（10 个映射，新增 `oracle_granularity`、`graph_off`、`safety_off`）。
- 清空 `DEFERRED_PIPELINE_LABELS`（从 frozenset of 3 → 空 frozenset）。
- `GoldEvidence` 上的 `granularity_level: str | None = None` 字段。
- `MemoryItem` 上的 `is_graph_expanded: bool = False` 字段。
- `ProbeCase` 上的 `granularity_levels: tuple[str, ...]` 字段（默认 6 个级别）。
- `ProbeCase` 上的 `current_granularity: str = "session"` 字段。
- `ProbeCase` 上的 `safety_filter_blocked: bool = False` 字段。
- `run_oracle_granularity(case) -> ReplayResult`：枚举粒度级别的新回放干预。
- `run_graph_off(case) -> ReplayResult`：禁用图扩展的新回放干预。
- `run_safety_off(case) -> ReplayResult`：绕过安全过滤器的新回放干预。
- `_recover_at_granularity(case, level) -> str`：从给定粒度级别收集证据的私有辅助函数。
- `_recover_without_graph_expansion(case) -> str`：从非图扩展记忆项收集证据的私有辅助函数。
- `run_v1_replay_portfolio` 已更新：10 回放组合（曾为 7）。
- `REPAIR_ACTION_BY_LABEL` 中针对 `granularity_error`、`graph_error`、`safety_error` 的三个新修复操作。
- 三个 V1 探针案例夹具文件和 81 个测试方法。

Issue 0012 不拥有的内容（属于其他 issue）：

- 更改 `assign_attribution_v1` 核心排名逻辑（issue 0011）。
- 更改 `_v1_label_for_replay` 摄入/写入拆分（issue 0011）。
- 更改基线套件或对比器逻辑（issue 0002）。
- 更改 Post-Repair Context Replay 评分（issue 0005）。
- 更改 ECS Failure Memory 逻辑（issue 0007）。
- 11 标签耦合失败重新校准（issue 0013）。
- mem0/Letta 适配器集成（issues 0014、0015）。
- 真实数据探针案例（issue 0016）。

## 当前代码产出物

| 产出物 | 在 Issue 0012 中的角色 |
| --- | --- |
| `cmd_audit/labels.py` | V1 标签注册表 11 个标签、`V1_REPLAY_TO_LABEL` 10 个映射、已清空的延迟集。主标签边界模块。 |
| `cmd_audit/models.py` | `GoldEvidence` 上的 `granularity_level` 字段、`MemoryItem` 上的 `is_graph_expanded` 字段、`ProbeCase` 上的 `granularity_levels`/`current_granularity`/`safety_filter_blocked` 字段、更新后的 `from_mapping` 和 `from_mapping_v1` 工厂方法。 |
| `cmd_audit/replays.py` | `run_oracle_granularity`、`run_graph_off`、`run_safety_off` 回放、`_recover_at_granularity`、`_recover_without_graph_expansion` 辅助函数、更新后的 `run_v1_replay_portfolio`（10 回放）。 |
| `cmd_audit/repairs.py` | 针对 `granularity_error`、`graph_error`、`safety_error` 的新 `TargetedRepairAction` 条目。 |
| `cmd_audit/__init__.py` | 导出 `run_oracle_granularity`、`run_graph_off`、`run_safety_off`。 |
| `data/probe_cases/v1_granularity_error_case.json` | 单一 `granularity_error` 探针夹具（事件级证据，会话级基线）。 |
| `data/probe_cases/v1_graph_error_case.json` | 单一 `graph_error` 探针夹具（直接证据 + 图扩展干扰项）。 |
| `data/probe_cases/v1_safety_error_case.json` | 单一 `safety_error` 探针夹具（`safety_filter_blocked: true`）。 |
| `tests/test_cmd_audit_issue12_v1_labels.py` | 10 个测试类，81 个测试方法，覆盖 Cycle 17。 |
| `tests/test_cmd_audit_issue11_v1_labels.py` | 3 个测试已更新（8→11 标签、7→10 回放、延迟→活动状态）。 |

## 模块地图

| 模块 | Issue 0012 角色 |
| --- | --- |
| `cmd_audit/labels.py` | **已更新。** `V1_PIPELINE_LABEL_ORDER` 扩展至 11 个标签（第 16-28 行）。`V1_REPLAY_TO_LABEL` 扩展至 10 个映射（第 62-73 行），新增 `oracle_granularity -> granularity_error`、`graph_off -> graph_error`、`safety_off -> safety_error`。`DEFERRED_PIPELINE_LABELS` 清空为 `frozenset()`（第 42 行）。现有 V0 常量、`validate_v0_label`、`validate_v1_label` 保持不变。 |
| `cmd_audit/models.py` | **已更新。** `GoldEvidence` 获得 `granularity_level: str \| None = None`（第 53 行）和 `from_mapping` 中的解析（第 63 行）。`MemoryItem` 获得 `is_graph_expanded: bool = False`（第 33 行）和 `from_mapping` 中的解析（第 42 行）。`ProbeCase` 获得三个新字段（第 123-125 行）：`granularity_levels`（默认 6 元组）、`current_granularity`（默认 `"session"`）、`safety_filter_blocked`（默认 `False`）。`from_mapping` 和 `from_mapping_v1` 均解析所有三个新字段。 |
| `cmd_audit/replays.py` | **已更新。** 拥有三个新公共回放函数和两个私有辅助函数（第 227-330 行）：`run_oracle_granularity`、`run_graph_off`、`run_safety_off`、`_recover_at_granularity`、`_recover_without_graph_expansion`。`run_v1_replay_portfolio` 已更新为 10 回放（第 35-49 行），新增 oracle_granularity、graph_off、safety_off 在位置 8-10。现有的 6 个 V0 回放、`run_oracle_route` 和 `_score_recovered_evidence` 保持不变。 |
| `cmd_audit/repairs.py` | **已更新。** `REPAIR_ACTION_BY_LABEL` 已扩展，包含 `granularity_error`（第 109-118 行）、`graph_error`（第 119-128 行）、`safety_error`（第 129-138 行）条目。现有的 `get_targeted_repair_action`、`get_targeted_repair_action_v1` 和所有修复对比逻辑保持不变。 |
| `cmd_audit/__init__.py` | **已更新。** 导出 `run_oracle_granularity`、`run_graph_off`、`run_safety_off`（第 84-90 行），并在 `__all__` 中声明（第 185-187 行）。 |
| `tests/test_cmd_audit_issue12_v1_labels.py` | 10 个测试类，81 个测试方法，覆盖 Cycle 17。 |
| `tests/test_cmd_audit_issue11_v1_labels.py` | 3 个测试方法已更新，以反映新的 11 标签/10 回放 V1 状态。 |

## 调用图

### V1 归因流水线（issue 0012）

```text
cmd_audit/__init__.py
  -> harness.run_case_v1(ProbeCase)
      -> baselines.run_baseline_suite(ProbeCase)
      -> replays.run_v1_replay_portfolio(ProbeCase)
          -> replays.run_oracle_write                    (V0)
          -> replays.run_oracle_compression              (V0)
          -> replays.run_verbatim_event_oracle           (V0)
          -> replays.run_oracle_retrieval                (V0)
          -> replays.run_injection_oracle                (V0)
          -> replays.run_evidence_given_reasoning        (V0)
          -> replays.run_oracle_route                    (V1, issue 0011)
          -> replays.run_oracle_granularity(case)        (V1, issue 0012)
              -> 计算 current_granularity 下的当前证据分数
              -> 遍历 case.granularity_levels:
                  -> 跳过 case.current_granularity
                  -> replays._recover_at_granularity(case, level)
                      -> 对于每个 gold_evidence:
                          -> 检查 evidence.granularity_level is None 或 == level
                          -> 通过 source_memory_id 查找 MemoryItem
                          -> scoring.evidence_recall_from_text((evidence,), memory.text)
              -> 如果 best_score <= current_score:
                  -> 返回空 _score_recovered_evidence（零增益，防止误报）
              -> 否则返回带有 best_block 的 _score_recovered_evidence
          -> replays.run_graph_off(case)
              -> 检查是否存在任何 item.is_graph_expanded == True
              -> 如果不存在: 返回空 _score_recovered_evidence（无图扩展则无图错误）
              -> replays._recover_without_graph_expansion(case)
                  -> 对于每个 gold_evidence:
                      -> 跳过 source_memory_id 为 None 的
                      -> 跳过 is_graph_expanded == True 的记忆项
                      -> evidence_recall_from_text((evidence,), memory.text)
              -> 返回带有恢复证据块的 _score_recovered_evidence
          -> replays.run_safety_off(case)
              -> 检查 case.safety_filter_blocked 是否为 True
              -> 如果为 False: 返回空 _score_recovered_evidence（无安全阻塞则无安全错误）
              -> 直接收集所有 gold_evidence.text（绕过安全过滤器）
              -> 返回带有收集到的证据块的 _score_recovered_evidence
      -> attribution.assign_attribution_v1(replays, has_ingestion_trace=case.has_ingestion_trace)
          -> 按 recovery_gain 降序排列 10 个 replays
          -> top = ranked[0]
          -> attribution._v1_label_for_replay(top.replay_name, has_ingestion_trace=...)
          -> labels.validate_v1_label(predicted_label)
      -> AuditResult(...)

  -> harness.run_case_full_v1(ProbeCase)
      -> harness.run_case_v1(ProbeCase)          （V1 11 标签归因）
      -> post_repair.draft_ecs(case, audit)
          -> post_repair._ecs_for_label(case, predicted_label, replay)
              -> repairs.get_targeted_repair_action_v1(predicted_label)
                  -> 返回 REPAIR_ACTION_BY_LABEL[label]（现在包含 3 个新标签）
          -> ECSDraft(...)
              -> __post_init__:
                  -> labels.validate_v1_label(predicted_label)（接受所有 11 个标签）
      -> post_repair.build_repaired_context(case, ecs_draft)
      -> post_repair.run_post_repair_context_replay(case, repaired_context)
      -> FullAuditResult(...)
```

### 行为测试路径

```text
tests/test_cmd_audit_issue12_v1_labels.py
  -> labels.validate_v1_label(label)                           （V1LabelValidationTest）
  -> labels.validate_v0_label(label)                           （V1LabelValidationTest，回归）
  -> labels.DEFERRED_PIPELINE_LABELS                           （V1LabelValidationTest）
  -> labels.V1_REPLAY_TO_LABEL                                 （V1LabelValidationTest）
  -> models.load_probe_cases_v1(path)                          （V1ProbeCaseLoadingTest）
  -> models.load_probe_cases(path)                             （V1ProbeCaseLoadingTest，回归）
  -> replays.run_oracle_granularity(case)                      （OracleGranularityReplayTest）
  -> replays.run_graph_off(case)                               （GraphOffReplayTest）
  -> replays.run_safety_off(case)                              （SafetyOffReplayTest）
  -> replays.run_v1_replay_portfolio(case)                     （V1ReplayPortfolioTest）
  -> attribution.assign_attribution_v1(replays, ...)           （GranularityErrorAttributionTest）
  -> attribution.assign_attribution_v1(replays, ...)           （GraphErrorAttributionTest）
  -> replays.run_safety_off(case)                              （SafetyErrorAttributionTest）
  -> harness.run_case_v1(case)                                 （V1NonRegressionTest）
  -> harness.run_cases_v1(cases)                               （V1NonRegressionTest）
  -> harness.run_case_full_v1(case)                            （V1ECSCompatibilityTest）
  -> models.ProbeCase 默认字段                                   （NewProbeCaseFieldsTest）
```

## 数据流

### 输入夹具

```text
data/probe_cases/v0_issue3_cases.json                       # 六案例 V0 烟雾套件（非回归）
data/probe_cases/v1_ingestion_error_case.json                # Issue 0011 ingestion 夹具（非回归）
data/probe_cases/v1_route_error_case.json                    # Issue 0011 route 夹具（非回归）
data/probe_cases/v1_granularity_error_case.json              # 单一 granularity_error 探针夹具
data/probe_cases/v1_graph_error_case.json                    # 单一 graph_error 探针夹具
data/probe_cases/v1_safety_error_case.json                   # 单一 safety_error 探针夹具
```

### v1_granularity_error_case.json 的结构

此夹具演示了证据在"event"级别存在，但基线使用"session"级别重新表达导致证据丢失的情况。Priya 为年度合作伙伴峰会选择了 Barcelona。详细的事件级记忆（mem-801）包含城市名称，但基线上下文使用会话级重新表达（mem-802），该表达将位置概括为"a city was selected"。

| 字段 | 值 | 目的 |
| --- | --- | --- |
| `case_id` | `"v1-granularity-001"` | 唯一案例标识符 |
| `query` | `"Which city did Priya select for the annual partner summit?"` | 原始失败查询 |
| `extracted_memory[0]` | mem-801: "Priya selected Barcelona for the annual partner summit..." | 包含证据的详细事件级记忆项 |
| `extracted_memory[1]` | mem-802: "The partner summit location was discussed and a city was selected." | 不含城市名称的会话级重新表达 |
| `gold_evidence[0]` | source_memory_id="mem-801"，granularity_level="event" | 证据在事件粒度上可恢复 |
| `gold_answer` | `"Barcelona"` | 金标准答案 |
| `perturbation_label` | `"granularity_error"` | 通过 `validate_v1_label`，被 `validate_v0_label` 拒绝 |
| `current_granularity` | `"session"` | 基线在会话级别表达记忆 |
| `granularity_levels` | `["event", "session", "persona"]` | 配置的粒度级别（案例特定的子集） |
| `baseline_outputs[0].retrieved_memory_ids` | `["mem-801", "mem-802"]` | 基线检索到两个项 |
| `baseline_outputs[0].injected_context` | `"The partner summit location was discussed and a city was selected."` | 基线使用会话级重新表达（无城市名称） |
| `baseline_outputs[0].answer` | `"Unknown"` | answer_score = 0.0 |

数据流步骤：
1. `run_oracle_granularity` 首先计算当前粒度（`"session"`）下的证据分数。在会话级别，`_recover_at_granularity(case, "session")` 寻找 `granularity_level is None` 或 `granularity_level == "session"` 的证据。gold-801 的 `granularity_level="event"`，因此被排除。当前分数 = 0.0。
2. 对于 `level="event"`：`_recover_at_granularity(case, "event")` 包括 gold-801（`granularity_level="event"` 匹配）。检查 mem-801 的文本是否包含证据短语（"Priya"、"Barcelona"、"partner summit"）→ 全部找到 → `evidence_block = mem-801.text` → `evidence_recall = 1.0`。
3. 对于 `level="persona"`：`_recover_at_granularity(case, "persona")` 排除 gold-801（`granularity_level="event"` ≠ `"persona"`）。分数 = 0.0。
4. `best_score = 1.0 > current_score = 0.0` → `_score_recovered_evidence` 设置 `answer = "Barcelona"` → `answer_score = 1.0`。
5. `recovery_gain = 1.0 - 0.0 = 1.0` → oracle_granularity 具有正 recovery_gain。
6. `assign_attribution_v1` 调用 `_v1_label_for_replay("oracle_granularity", ...)` → 返回 `"granularity_error"`。
7. `validate_v1_label("granularity_error")` 成功 → `predicted_label = "granularity_error"` ✓。

**关于粒度级别守卫的说明：** `run_oracle_granularity` 包含一个显式守卫：`if best_score <= current_score: return _score_recovered_evidence(case, "oracle_granularity", "")`。这可以防止当所有粒度级别产生相同证据时（例如安全性案例和大多数 V0 案例，其中 `granularity_level is None` 是所有证据的默认值）出现误报。只有当不同的粒度级别产生严格更好的证据召回时，回放才会产生正增益。

### v1_graph_error_case.json 的结构

此夹具演示了图扩展引入干扰项的情况。Chen 推荐 Oakwood Construction 进行办公室翻新。直接记忆项（mem-901，`is_graph_expanded=false`）包含正确的供应商。图扩展检索了一个干扰项（mem-902，`is_graph_expanded=true`，关于 Pinewood Builders）。基线检索到两个项，答案混淆，选择了 Pinewood Builders。

| 字段 | 值 | 目的 |
| --- | --- | --- |
| `case_id` | `"v1-graph-001"` | 唯一案例标识符 |
| `query` | `"Which vendor did Chen recommend for the office renovation project?"` | 原始失败查询 |
| `extracted_memory[0]` | mem-901: "Chen recommended Oakwood Construction...", is_graph_expanded=false | 包含证据的直接项 |
| `extracted_memory[1]` | mem-902: "Pinewood Builders completed a similar construction project...", is_graph_expanded=true | 图扩展干扰项 |
| `gold_evidence[0]` | source_memory_id="mem-901" | 证据位于直接项中 |
| `gold_answer` | `"Oakwood Construction"` | 金标准答案 |
| `perturbation_label` | `"graph_error"` | 通过 `validate_v1_label` |
| `baseline_outputs[0].retrieved_memory_ids` | `["mem-901", "mem-902"]` | 基线检索到直接项和干扰项 |
| `baseline_outputs[0].answer` | `"Pinewood Builders"` | 被干扰项混淆的错误答案 |
| `baseline_outputs[0].answer_score` | `0.0` | |

数据流步骤：
1. `run_graph_off` 检查 `has_expanded = any(item.is_graph_expanded for item in case.extracted_memory)` → mem-902 的 `is_graph_expanded=True` → `has_expanded = True`。
2. `_recover_without_graph_expansion(case)` 迭代 gold_evidence。gold-901 的 `source_memory_id="mem-901"`。查找 mem-901：`is_graph_expanded=False` → 不跳过。检查 `evidence_recall_from_text` → 找到 "Chen"、"Oakwood Construction"、"office renovation" → 恢复 mem-901.text。
3. `evidence_block = "Chen recommended Oakwood Construction for the office renovation project."` → `evidence_score = 1.0`。
4. `_score_recovered_evidence` 设置 `answer = "Oakwood Construction"` → `answer_score = 1.0`。
5. `recovery_gain = 1.0 - 0.0 = 1.0` → graph_off 具有正 recovery_gain。
6. `assign_attribution_v1` 调用 `_v1_label_for_replay("graph_off", ...)` → 返回 `"graph_error"`。
7. `validate_v1_label("graph_error")` 成功 → `predicted_label = "graph_error"` ✓。

**关于 Graph-Off 守卫的说明：** `run_graph_off` 包含一个守卫：`if not has_expanded: return _score_recovered_evidence(case, "graph_off", "")`。这可以防止在不存在图扩展项时出现误报（大多数 V0 案例、粒度案例、安全性案例）。只有当图扩展项存在且至少一个非扩展项包含证据时，回放才会产生正增益。

### v1_safety_error_case.json 的结构

此夹具演示了安全过滤器阻止有效证据的情况。安全审计在支付模块中发现了一个 SQL 注入漏洞。`safety_filter_blocked=true` 标志指示安全过滤器阻止了响应。基线回答 "Blocked"。Safety-Off 回放绕过过滤器，揭示被阻止的证据。

| 字段 | 值 | 目的 |
| --- | --- | --- |
| `case_id` | `"v1-safety-001"` | 唯一案例标识符 |
| `query` | `"What security vulnerability did the audit report identify in the payment module?"` | 原始失败查询 |
| `extracted_memory[0]` | mem-a01: "The audit identified a SQL injection vulnerability in the payment module..." | 包含被阻止证据的记忆项 |
| `extracted_memory[1]` | mem-a02: "The safety filter blocked the detailed audit findings..." | 确认过滤器阻止 |
| `gold_evidence[0]` | source_memory_id="mem-a01" | 被安全过滤器阻止的证据 |
| `gold_answer` | `"SQL injection vulnerability in the payment module transaction lookup"` | 金标准答案 |
| `perturbation_label` | `"safety_error"` | 通过 `validate_v1_label` |
| `safety_filter_blocked` | `true` | 触发 safety_off 恢复 |
| `baseline_outputs[0].answer` | `"Blocked"` | answer_score = 0.0（安全过滤器阻止了正确答案） |
| `baseline_outputs[0].evidence_score` | `0.0` | |

数据流步骤：
1. `run_safety_off` 检查 `case.safety_filter_blocked` → `True`。
2. 收集所有 gold_evidence.text：`"SQL injection vulnerability in the payment module transaction lookup."`。
3. `evidence_score = evidence_recall_from_text(case.gold_evidence, evidence_block)` → `1.0`（所有必需短语都匹配）。
4. `gold_answer` 设置，因此 `answer = "SQL injection vulnerability in the payment module transaction lookup"` → `answer_score = 1.0`。
5. `recovery_gain = 1.0 - 0.0 = 1.0` → safety_off 具有正 recovery_gain。
6. `assign_attribution_v1` 调用 `_v1_label_for_replay("safety_off", ...)` → 返回 `"safety_error"`。
7. `validate_v1_label("safety_error")` 成功 → `predicted_label = "safety_error"` ✓。

**关于 Safety-Off 守卫的说明：** `run_safety_off` 包含一个守卫：`if not case.safety_filter_blocked: return _score_recovered_evidence(case, "safety_off", "")`。这可以防止在安全过滤器未阻止任何内容时出现误报（大多数 V0 案例、粒度案例、图案例）。只有当 `safety_filter_blocked` 标志显式为 `True` 时，回放才会产生正增益。

### 中间类型

**GoldEvidence** 新增字段（来自 `cmd_audit/models.py:46-64`）：

| 字段 | 类型 | 默认值 | 含义 |
| --- | --- | --- | --- |
| `granularity_level` | `str \| None` | `None` | 此证据片段相关的粒度级别。`None` 意味着在所有级别都可用。由 `_recover_at_granularity` 用于按级别过滤。 |

**MemoryItem** 新增字段（来自 `cmd_audit/models.py:28-43`）：

| 字段 | 类型 | 默认值 | 含义 |
| --- | --- | --- | --- |
| `is_graph_expanded` | `bool` | `False` | 如果此记忆项是通过图扩展（而非直接匹配）检索到的，则为 `True`。由 `run_graph_off` 用于识别干扰项，由 `_recover_without_graph_expansion` 用于过滤。 |

**ProbeCase** 新增字段（来自 `cmd_audit/models.py:104-125`）：

| 字段 | 类型 | 默认值 | 含义 |
| --- | --- | --- | --- |
| `granularity_levels` | `tuple[str, ...]` | `("raw", "event", "session", "persona", "procedure", "graph")` | 要枚举的粒度级别。可按探针案例配置（并非所有案例都测试所有级别）。 |
| `current_granularity` | `str` | `"session"` | 基线使用的当前粒度级别。`run_oracle_granularity` 将其用作基准线——只有当不同的级别产生严格更好的恢复时，它才会生成正增益。 |
| `safety_filter_blocked` | `bool` | `False` | 当安全过滤器阻止有效证据时为 `True`。驱动 `run_safety_off` 守卫——只有此标志为真时才绕过。 |

**V1_REPLAY_TO_LABEL** 映射新增条目（来自 `cmd_audit/labels.py:62-73`）：

| 回放名称 | V1 标签 | 新增/现有 |
| --- | --- | --- |
| `oracle_granularity` | `granularity_error` | **Issue 0012 新增** |
| `graph_off` | `graph_error` | **Issue 0012 新增** |
| `safety_off` | `safety_error` | **Issue 0012 新增** |

完整 10 条目 `V1_REPLAY_TO_LABEL` 映射表：

| 回放名称 | V1 标签 | 备注 |
| --- | --- | --- |
| `oracle_write` | `write_error`（默认；可被 `ingestion_error` 覆盖） | Issue 0011: `has_ingestion_trace` 覆盖 |
| `oracle_compression` | `compression_error` | 与 V0 相同 |
| `verbatim_event_oracle` | `premature_extraction_error` | 与 V0 相同 |
| `oracle_retrieval` | `retrieval_error` | 与 V0 相同 |
| `injection_oracle` | `injection_error` | 与 V0 相同 |
| `evidence_given_reasoning` | `reasoning_error` | 与 V0 相同 |
| `oracle_route` | `route_error` | Issue 0011 新增 |
| `oracle_granularity` | `granularity_error` | Issue 0012 新增 |
| `graph_off` | `graph_error` | Issue 0012 新增 |
| `safety_off` | `safety_error` | Issue 0012 新增 |

**TargetedRepairAction** V1 新增条目（来自 `cmd_audit/repairs.py:109-138`）：

| 标签 | action_name | cause | repair_guidance |
| --- | --- | --- | --- |
| `granularity_error` | Oracle Granularity Repair | "memory was expressed at a granularity level that lost key evidence; a different granularity preserves the evidence" | "adjust memory expression granularity to preserve evidence; consider the level that best balances detail and conciseness" |
| `graph_error` | Graph-Off Repair | "graph expansion introduced distractor items that masked correct evidence present in directly-matched memory items" | "constrain or re-rank graph expansion results to prevent distractors from overriding directly-matched evidence" |
| `safety_error` | Safety-Off Repair | "safety filter blocked valid evidence that was necessary for a correct answer" | "review safety filter rules to reduce false positives; consider evidence-level allow-listing for known-safe content" |

### 输出产出物

V1 11 标签管道运行生成与 V0 相同类型的产出物，但使用 11 标签归因：

```text
artifacts/sandbox/
  attribution_table.csv                     # 11 标签归因
  comparison_metrics.csv                    # CMD-V1 vs 基线
  attribution_confusion_matrix.csv          # 11x11 混淆矩阵
  post_repair_table.csv                     # 修复后回放结果
  repair_success_table.csv                  # 修复成功对比
  repair_label_summary.csv                  # 逐标签修复汇总
  repair_claim_ledger.txt                   # 修复声明账本
```

## 函数级合约

### `cmd_audit/labels.py`

这是 issue 0012 的主标签边界模块。文件：`cmd_audit/labels.py`（128 行）。Issue 0012 变更：更新 `V1_PIPELINE_LABEL_ORDER`（扩展）、`V1_REPLAY_TO_LABEL`（扩展）、`DEFERRED_PIPELINE_LABELS`（清空）。

---

#### 常量：`V1_PIPELINE_LABEL_ORDER`（已更新）

位置：`cmd_audit/labels.py:16-28`

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
    "granularity_error",
    "graph_error",
    "safety_error",
)
```

目的：

- 为 V1 归因定义完整的 11 标签有序元组。
- 顺序决定了混淆矩阵的列顺序和报告的一致性。
- V0 的 6 个标签在前（保留 V0 顺序），随后是 issue 0011 的 2 个标签，最后是 issue 0012 的 3 个标签。

变更自 Issue 0011：

| 方面 | Issue 0011 | Issue 0012 |
| --- | --- | --- |
| 标签数量 | 8 | 11 |
| 新增标签 | `ingestion_error`、`route_error` | `granularity_error`、`graph_error`、`safety_error` |
| 位置 7-8 | `ingestion_error`、`route_error` | 保持不变 |
| 位置 9-11 | 不适用 | `granularity_error`、`graph_error`、`safety_error` |

调用者：

- `validate_v1_label`（`labels.py:117`）
- `V1_PIPELINE_LABELS` 构造（`labels.py:30`）
- `V1LabelValidationTest.test_v1_label_order_has_eleven_labels`
- `V1LabelValidationTest.test_validate_v1_label_accepts_all_eleven_labels`

---

#### 常量：`V1_REPLAY_TO_LABEL`（已更新）

位置：`cmd_audit/labels.py:62-73`

```python
V1_REPLAY_TO_LABEL = {
    "oracle_write": "write_error",
    "oracle_compression": "compression_error",
    "verbatim_event_oracle": "premature_extraction_error",
    "oracle_retrieval": "retrieval_error",
    "injection_oracle": "injection_error",
    "evidence_given_reasoning": "reasoning_error",
    "oracle_route": "route_error",
    "oracle_granularity": "granularity_error",
    "graph_off": "graph_error",
    "safety_off": "safety_error",
}
```

目的：

- 将每个 V1 回放名称映射到其 V1 管道标签。
- 新增三个映射：`oracle_granularity` → `granularity_error`、`graph_off` → `graph_error`、`safety_off` → `safety_error`。
- 所有映射都是无条件的（与需要 `has_ingestion_trace` 覆盖的 `oracle_write` 不同）。

变更自 Issue 0011：

| 方面 | Issue 0011 | Issue 0012 |
| --- | --- | --- |
| 条目数量 | 7 | 10 |
| 新增键 | `oracle_route` | `oracle_granularity`、`graph_off`、`safety_off` |

与 `REPLAY_TO_LABEL` 的关系：

- `V1_REPLAY_TO_LABEL` 是 `REPLAY_TO_LABEL` 的字典超集（相同的 6 个 V0 条目 + 4 个新条目）。
- V0 使用 `REPLAY_TO_LABEL`（仅 6 个键）。V1 使用 `V1_REPLAY_TO_LABEL`（10 个键）。

调用者：

- `_v1_label_for_replay`（`attribution.py:98-99`）
- `V1ReplayToLabelMappingTest` 中的所有测试

---

#### 常量：`DEFERRED_PIPELINE_LABELS`（已清空）

位置：`cmd_audit/labels.py:42`

```python
DEFERRED_PIPELINE_LABELS: frozenset[str] = frozenset()
```

目的：

- 列出为 future issues 保留的管道标签。Issue 0012 激活最后三个延迟标签后，此集合现在为空。
- `validate_v0_label` 和 `validate_v1_label` 均检查此集合，以拒绝带有适当版本范围消息的延迟标签。使用空集合时，延迟检查永远不会触发，但代码路径保留以供 future 版本（V2）使用。

变更自 Issue 0011：

| 标签 | Issue 0011 状态 | Issue 0012 状态 |
| --- | --- | --- |
| `granularity_error` | 延迟（被 `validate_v1_label` 拒绝） | 活动（被 `validate_v1_label` 接受） |
| `graph_error` | 延迟（被 `validate_v1_label` 拒绝） | 活动（被 `validate_v1_label` 接受） |
| `safety_error` | 延迟（被 `validate_v1_label` 拒绝） | 活动（被 `validate_v1_label` 接受） |

**所有规划的 V1 管道标签现在都已激活。** V2 范围（`item_wrong`、`item_stale` 等）仍留在 `OUT_OF_SCOPE_ITEM_LABELS` 中。

---

#### `validate_v0_label` 与 `validate_v1_label`：行为

这两个函数本身在 Issue 0012 中没有改变。但行为边界发生了变化：

- `validate_v0_label("granularity_error")` → `LabelValidationError`（"is a V1 pipeline label and is outside V0 attribution scope"）——新标签在第 107-110 行的 V1 检查中被捕获。
- `validate_v0_label("graph_error")` → 同上。
- `validate_v0_label("safety_error")` → 同上。
- `validate_v1_label("granularity_error")` → 返回 `"granularity_error"`（在 `V1_PIPELINE_LABELS` 中，第 117 行）。
- `validate_v1_label("graph_error")` → 同上。
- `validate_v1_label("safety_error")` → 同上。

延迟检查（`validate_v1_label` 中的第 123-126 行）现在永远不会触发，因为 `DEFERRED_PIPELINE_LABELS` 为空。代码路径被保留，不作为死代码移除，以便在 V2 标签扩展（issue 0013+）中保持结构完整性。

### `cmd_audit/replays.py`

文件：`cmd_audit/replays.py`（331 行）。Issue 0012 新增：3 个公共回放函数、2 个私有辅助函数、更新后的组合函数。

---

#### 函数：`run_v1_replay_portfolio(case: ProbeCase) -> tuple[ReplayResult, ...]`（已更新）

位置：`cmd_audit/replays.py:35-49`

```python
def run_v1_replay_portfolio(case: ProbeCase) -> tuple[ReplayResult, ...]:
    """Run the V1 replay portfolio (10 replays) for one case."""

    return (
        run_oracle_write(case),
        run_oracle_compression(case),
        run_verbatim_event_oracle(case),
        run_oracle_retrieval(case),
        run_injection_oracle(case),
        run_evidence_given_reasoning(case),
        run_oracle_route(case),
        run_oracle_granularity(case),
        run_graph_off(case),
        run_safety_off(case),
    )
```

目的：

- 运行完整的 10 回放 V1 组合（V0 的 6 个 + `oracle_route` + `oracle_granularity` + `graph_off` + `safety_off`）。
- 元组顺序很重要：`assign_attribution_v1` 使用 Python 的稳定排序，当 recovery_gain 值相等时，首先列出的获胜。新回放放置在位置 8-10（在 V0 6 + oracle_route 之后），因此当平局时，现有的回放优先。

回放顺序与归因影响：

| 位置 | 回放 | 恢复的内容 | 当具有最高且唯一的 recovery_gain 时分配的标签 |
| --- | --- | --- | --- |
| 1 | `oracle_write` | 未写入或从未摄入的证据 | `write_error` 或 `ingestion_error` |
| 2 | `oracle_compression` | 通过有损压缩丢失的证据 | `compression_error` |
| 3 | `verbatim_event_oracle` | 在提取过程中丢失的原始事件证据 | `premature_extraction_error` |
| 4 | `oracle_retrieval` | 存在但未检索的证据 | `retrieval_error` |
| 5 | `injection_oracle` | 检索到但注入错误的证据 | `injection_error` |
| 6 | `evidence_given_reasoning` | 已注入但推理错误的证据 | `reasoning_error` |
| 7 | `oracle_route` | 错误存储/层级中的证据 | `route_error` |
| 8 | `oracle_granularity` | 在错误粒度级别表达的证据 | `granularity_error` |
| 9 | `graph_off` | 被图扩展干扰掩盖的证据 | `graph_error` |
| 10 | `safety_off` | 被安全过滤器阻挡的证据 | `safety_error` |

调用者：

- `run_case_v1`（`harness.py:197`）
- `V1ReplayPortfolioTest` 中的所有测试
- `GranularityErrorAttributionTest`、`GraphErrorAttributionTest`、`SafetyErrorAttributionTest`

---

#### 函数：`run_oracle_granularity(case: ProbeCase) -> ReplayResult`

位置：`cmd_audit/replays.py:230-260`

```python
def run_oracle_granularity(case: ProbeCase) -> ReplayResult:
    """Replay by re-expressing memory at each granularity level.

    This intervention diagnoses ``granularity_error``: the baseline used a
    sub-optimal granularity level for memory expression. Enumerates configured
    granularity levels, picks the one with the best evidence recovery.

    Only produces a positive recovery gain when a granularity level *different
    from the current one* yields higher evidence recall. When all levels
    produce the same evidence (no granularity effect), the replay returns
    zero gain so it does not interfere with other attribution labels.
    """
    current_block = _recover_at_granularity(case, case.current_granularity)
    current_score = evidence_recall_from_text(case.gold_evidence, current_block)

    best_score = current_score
    best_block = current_block

    for level in case.granularity_levels:
        if level == case.current_granularity:
            continue
        evidence_block = _recover_at_granularity(case, level)
        score = evidence_recall_from_text(case.gold_evidence, evidence_block)
        if score > best_score:
            best_score = score
            best_block = evidence_block

    if best_score <= current_score:
        return _score_recovered_evidence(case, "oracle_granularity", "")

    return _score_recovered_evidence(case, "oracle_granularity", best_block)
```

目的：

- 通过枚举所有配置的粒度级别并选择具有最佳证据恢复的级别，诊断 `granularity_error`。
- 与 `run_oracle_route`（按存储枚举）的结构同构，但使用粒度级别作为枚举维度。
- **关键守卫**：只有当严格更好的级别（`best_score > current_score`）存在时才产生正 recovery_gain。这可以防止在所有粒度级别产生相同证据时（例如，所有证据的 `granularity_level is None`）出现误报。

行为：

1. 通过 `_recover_at_granularity` 计算当前粒度级别下的证据召回作为基线。
2. 遍历 `case.granularity_levels`，跳过 `case.current_granularity`。
3. 对于每个其他级别，调用 `_recover_at_granularity(case, level)` 以获取证据块，并通过 `evidence_recall_from_text` 评分。
4. 跟踪 `best_score` 和 `best_block`（从当前分数/块初始化）。
5. 如果 `best_score <= current_score`：返回空的 `ReplayResult`（recovery_gain = 0）。**这是关键的误报预防机制。**
6. 否则：通过 `_score_recovered_evidence` 返回带有最佳级别证据块的 `ReplayResult`。

领域边界：

- 粒度级别是按案例配置的（并非所有案例都测试所有 6 个级别）。`granularity_levels` 字段允许案例特定的子集（例如 `["event", "session", "persona"]`）。
- `granularity_level is None` 的证据在所有级别都可用——它不会被 `_recover_at_granularity` 过滤掉。这意味着仅具有 `None` 级别证据的案例（大多数 V0 案例）永远不会触发 `granularity_error`。
- 在独立工具中，`injection_oracle` 或 `oracle_retrieval` 也可能恢复相同的证据（因为它们搜索所有记忆项，无论粒度如何）。在平局的情况下，`injection_oracle`（位置 5）或 `oracle_retrieval`（位置 4）由于元组顺序先于 `oracle_granularity`（位置 8）获胜。在实际系统中，粒度重新表达是一个真正的干预，与其他回放有不同的效果。

调用者：

- `run_v1_replay_portfolio`（`replays.py:46`）
- `OracleGranularityReplayTest`（直接测试）

---

#### 函数：`run_graph_off(case: ProbeCase) -> ReplayResult`

位置：`cmd_audit/replays.py:263-275`

```python
def run_graph_off(case: ProbeCase) -> ReplayResult:
    """Replay with graph expansion disabled.

    This intervention diagnoses ``graph_error``: graph expansion introduced
    distractors that masked correct evidence. Filters to non-graph-expanded
    memory items and checks whether direct evidence alone recovers.
    """
    has_expanded = any(item.is_graph_expanded for item in case.extracted_memory)
    if not has_expanded:
        return _score_recovered_evidence(case, "graph_off", "")

    evidence_block = _recover_without_graph_expansion(case)
    return _score_recovered_evidence(case, "graph_off", evidence_block)
```

目的：

- 通过过滤掉图扩展记忆项并仅评估直接匹配项，诊断 `graph_error`。
- **关键守卫**：`if not has_expanded` → 返回空的 `ReplayResult`。如果案例中没有图扩展项，则不会出现 `graph_error`（错误不适用）。

行为：

1. 检查任何记忆项是否设置了 `is_graph_expanded == True`。
2. 如果没有扩展项：返回带有空证据块的 `ReplayResult`（recovery_gain = 0）。
3. 如果有扩展项：调用 `_recover_without_graph_expansion(case)` 以从仅直接项收集证据。
4. 通过 `_score_recovered_evidence` 返回带有恢复证据的 `ReplayResult`。

领域边界：

- `_recover_without_graph_expansion` 从 `is_graph_expanded == False` 的记忆项中收集。它不检查基线是否实际使用了这些项——如果任何直接项包含证据，回放就会恢复它。
- `has_expanded` 守卫确保此回放在大多数不具有图扩展的 V0 案例上产生零增益。
- 图案例（如 v1-graph-001）同时具有直接项和扩展项。直接项（mem-901）包含证据，扩展项（mem-902）是干扰项。

调用者：

- `run_v1_replay_portfolio`（`replays.py:47`）
- `GraphOffReplayTest`（直接测试）

---

#### 函数：`run_safety_off(case: ProbeCase) -> ReplayResult`

位置：`cmd_audit/replays.py:278-291`

```python
def run_safety_off(case: ProbeCase) -> ReplayResult:
    """Replay with safety filter bypassed.

    This intervention diagnoses ``safety_error``: the safety filter blocked
    evidence that was otherwise valid. When ``safety_filter_blocked`` is True,
    the blocked evidence is provided directly.
    """
    if not case.safety_filter_blocked:
        return _score_recovered_evidence(case, "safety_off", "")

    evidence_block = "\n".join(
        evidence.text for evidence in case.gold_evidence
    )
    return _score_recovered_evidence(case, "safety_off", evidence_block)
```

目的：

- 通过绕过安全过滤器并直接暴露被阻止的证据，诊断 `safety_error`。
- **关键守卫**：`if not case.safety_filter_blocked` → 返回空的 `ReplayResult`。如果安全过滤器未阻止任何内容，则不会出现 `safety_error`。

行为：

1. 检查 `case.safety_filter_blocked`。
2. 如果为 `False`：返回带有空证据块的 `ReplayResult`（recovery_gain = 0）。
3. 如果为 `True`：直接收集所有 `gold_evidence.text` 字符串，绕过任何过滤逻辑。
4. 通过 `_score_recovered_evidence` 返回带有收集到的证据的 `ReplayResult`。

领域边界：

- 在独立工具中，安全过滤器行为是被模拟的（通过 `safety_filter_blocked` 标志）。没有实际的安全过滤器逻辑——回放只是揭示在没有过滤的情况下本应可用的证据。
- 此回放是最简单的干预：当 `safety_filter_blocked=True` 时，它无条件地收集所有金标证据文本。真正的安全过滤器系统将需要更细致的绕过。
- `safety_filter_blocked` 守卫确保此回放在大多数不具有安全过滤器阻止的 V0 案例上产生零增益。

调用者：

- `run_v1_replay_portfolio`（`replays.py:48`）
- `SafetyOffReplayTest`（直接测试）

---

#### 私有函数：`_recover_at_granularity(case: ProbeCase, level: str) -> str`

位置：`cmd_audit/replays.py:294-313`

```python
def _recover_at_granularity(case: ProbeCase, level: str) -> str:
    """Collect gold evidence recoverable at a given granularity level.

    Evidence with ``granularity_level`` set to *level* or ``None``
    (available at all levels) is included when its source memory item
    contains the required phrases.
    """
    memory_by_id = {item.memory_id: item for item in case.extracted_memory}
    recovered = []
    for evidence in case.gold_evidence:
        if evidence.granularity_level is not None and evidence.granularity_level != level:
            continue
        if not evidence.source_memory_id:
            continue
        memory = memory_by_id.get(evidence.source_memory_id)
        if memory is None:
            continue
        if evidence_recall_from_text((evidence,), memory.text) >= 1.0:
            recovered.append(memory.text)
    return "\n".join(recovered)
```

目的：

- 从与给定粒度级别匹配的记忆项中恢复证据文本。
- 证据包含条件：`granularity_level is None`（在所有级别都可用）或 `granularity_level == level`（特定于此级别）。

行为：

1. 通过 ID 构建记忆项查找映射。
2. 对于每个金标证据片段：
   - 如果 `evidence.granularity_level is not None` 且 `!= level`：跳过——此证据在此粒度级别不可用。
   - 如果没有 `source_memory_id` 则跳过。
   - 通过 ID 查找记忆项；如果未找到则跳过。
   - 检查记忆项文本是否包含证据短语（`evidence_recall_from_text >= 1.0`）。
   - 如果所有检查通过，将 `memory.text` 追加到恢复列表。
3. 返回通过换行符连接的恢复文本。

核心过滤逻辑：

```text
if evidence.granularity_level is None:
    → include (available at all levels)
elif evidence.granularity_level == level:
    → include (specific to this level)
else:
    → skip (belongs to a different level)
```

调用者：

- `run_oracle_granularity`（`replays.py:242, 251`）

---

#### 私有函数：`_recover_without_graph_expansion(case: ProbeCase) -> str`

位置：`cmd_audit/replays.py:316-330`

```python
def _recover_without_graph_expansion(case: ProbeCase) -> str:
    """Collect evidence from memory items not retrieved via graph expansion."""
    memory_by_id = {item.memory_id: item for item in case.extracted_memory}
    recovered = []
    for evidence in case.gold_evidence:
        if not evidence.source_memory_id:
            continue
        memory = memory_by_id.get(evidence.source_memory_id)
        if memory is None:
            continue
        if memory.is_graph_expanded:
            continue
        if evidence_recall_from_text((evidence,), memory.text) >= 1.0:
            recovered.append(memory.text)
    return "\n".join(recovered)
```

目的：

- 从非图扩展项（`is_graph_expanded == False`）收集证据文本。
- 结构与 `_recover_from_store` 同构（按存储过滤），但按 `is_graph_expanded` 过滤。

行为：

1. 通过 ID 构建记忆项查找映射。
2. 对于每个金标证据片段：
   - 如果没有 `source_memory_id` 则跳过。
   - 通过 ID 查找记忆项；如果未找到则跳过。
   - **如果 `memory.is_graph_expanded` 为 True 则跳过**——这是核心过滤器。
   - 检查记忆项文本是否包含证据短语。
   - 如果所有检查通过，将 `memory.text` 追加到恢复列表。
3. 返回通过换行符连接的恢复文本。

调用者：

- `run_graph_off`（`replays.py:274`）

### `cmd_audit/models.py`

文件：`cmd_audit/models.py`（250 行）。Issue 0012 新增：`GoldEvidence` 上的 `granularity_level` 字段、`MemoryItem` 上的 `is_graph_expanded` 字段、`ProbeCase` 上的 3 个新字段。

---

#### 数据类：`GoldEvidence`（已更新）

位置：`cmd_audit/models.py:46-64`

```python
@dataclass(frozen=True)
class GoldEvidence:
    evidence_id: str
    text: str
    source_memory_id: str | None = None
    source_event_id: str | None = None
    required_phrases: tuple[str, ...] = ()
    granularity_level: str | None = None            # ← 新增

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "GoldEvidence":
        return cls(
            ...
            granularity_level=value.get("granularity_level"),  # ← 新增
        )
```

新增字段 `granularity_level`：

- 默认值 `None` 确保向后兼容：所有现有 V0 和 issue 0011 夹具无需更改。`None` 意味着证据在所有粒度级别都可用。
- 由 `_recover_at_granularity`（`replays.py:304`）用于按级别过滤证据。
- 在粒度案例中：`gold-801` 设置了 `granularity_level="event"`，这意味着它仅在事件级别恢复中可用。

---

#### 数据类：`MemoryItem`（已更新）

位置：`cmd_audit/models.py:28-43`

```python
@dataclass(frozen=True)
class MemoryItem:
    memory_id: str
    text: str
    source_event_ids: tuple[str, ...] = ()
    store: str = "default"
    is_graph_expanded: bool = False                  # ← 新增

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "MemoryItem":
        return cls(
            ...
            is_graph_expanded=bool(value.get("is_graph_expanded", False)),  # ← 新增
        )
```

新增字段 `is_graph_expanded`：

- 默认值 `False` 确保向后兼容：所有现有 V0 和 issue 0011 夹具无需更改。
- 由 `run_graph_off`（`replays.py:270`）用于检测是否存在图扩展项，由 `_recover_without_graph_expansion`（`replays.py:326`）用于过滤扩展项。
- 在图案例中：mem-902 设置了 `is_graph_expanded=true`，将其标记为图扩展干扰项。

---

#### 数据类：`ProbeCase`（已更新）

位置：`cmd_audit/models.py:104-185`

新增字段：

```python
granularity_levels: tuple[str, ...] = (              # 第 123 行
    "raw", "event", "session", "persona", "procedure", "graph"
)
current_granularity: str = "session"                  # 第 124 行
safety_filter_blocked: bool = False                   # 第 125 行
```

| 字段 | 默认值 | 目的 |
| --- | --- | --- |
| `granularity_levels` | `("raw", "event", "session", "persona", "procedure", "graph")` | 要枚举的粒度级别。可按探针案例配置（例如粒度夹具仅使用 `["event", "session", "persona"]`）。 |
| `current_granularity` | `"session"` | 基线使用的当前粒度级别。`run_oracle_granularity` 将其用作基准线。 |
| `safety_filter_blocked` | `False` | 当安全过滤器阻止有效证据时为 `True`。驱动 `run_safety_off` 守卫。 |

`from_mapping` 和 `from_mapping_v1` 中的解析（均相同）：

```python
granularity_levels=tuple(
    value.get("granularity_levels",
              ("raw", "event", "session", "persona", "procedure", "graph"))
),
current_granularity=str(value.get("current_granularity", "session")),
safety_filter_blocked=bool(value.get("safety_filter_blocked", False)),
```

向后兼容：

- 所有三个字段都有合理的默认值。现有的 V0 夹具（6 个烟雾案例）和 issue 0011 夹具（ingestion、route）无需更改。
- `granularity_levels` 默认值覆盖了完整的 6 级分类法。
- `current_granularity="session"` 与大多数记忆系统使用的默认会话级表达相匹配。
- `safety_filter_blocked=False` 意味着在未明确配置的案例上没有安全错误。

### `cmd_audit/repairs.py`

文件：`cmd_audit/repairs.py`（506 行）。Issue 0012 变更：`REPAIR_ACTION_BY_LABEL` 中新增 3 个条目。

---

#### 字典：`REPAIR_ACTION_BY_LABEL`（已扩展）

位置：`cmd_audit/repairs.py:88-138`

新增条目（第 109-138 行）：

```python
REPAIR_ACTION_BY_LABEL.update(
    {
        "granularity_error": TargetedRepairAction(
            label="granularity_error",
            action_name="Oracle Granularity Repair",
            description="Re-express memory at a finer or coarser granularity to preserve evidence.",
            intervention_summary="Oracle Granularity recovers evidence lost at the original granularity level.",
            cause="memory was expressed at a granularity level that lost key evidence; "
                  "a different granularity preserves the evidence",
            repair_guidance="adjust memory expression granularity to preserve evidence; "
                            "consider the level that best balances detail and conciseness",
        ),
        "graph_error": TargetedRepairAction(
            label="graph_error",
            action_name="Graph-Off Repair",
            description="Disable graph expansion to avoid distractor items masking correct evidence.",
            intervention_summary="Graph-Off recovers evidence when graph expansion introduced distractors.",
            cause="graph expansion introduced distractor items that masked correct evidence "
                  "present in directly-matched memory items",
            repair_guidance="constrain or re-rank graph expansion results to prevent "
                            "distractors from overriding directly-matched evidence",
        ),
        "safety_error": TargetedRepairAction(
            label="safety_error",
            action_name="Safety-Off Repair",
            description="Bypass safety filter to allow valid blocked evidence through.",
            intervention_summary="Safety-Off recovers evidence blocked by an over-aggressive safety filter.",
            cause="safety filter blocked valid evidence that was necessary for a correct answer",
            repair_guidance="review safety filter rules to reduce false positives; "
                            "consider evidence-level allow-listing for known-safe content",
        ),
    }
)
```

领域含义：

- `granularity_error` 修复与粒度表达相关。原因定位到"错误粒度级别"问题，指导调整为更合适的粒度。
- `graph_error` 修复与图扩展干扰相关。原因指出干扰项掩盖了直接匹配的证据。指导建议约束或重新排序图扩展结果。
- `safety_error` 修复与安全过滤器过度阻塞相关。原因指出过滤器阻止了有效证据。指导建议审查规则并考虑允许列表。

所有三个条目都通过现有的 `TargetedRepairAction.__post_init__` 进行验证，该函数调用 `validate_v1_label(self.label)` ——现在接受所有 11 个标签。

### `cmd_audit/__init__.py`

文件：`cmd_audit/__init__.py`（210 行）。Issue 0012 变更：新增 3 个导出。

---

#### 新增导出

位置：`cmd_audit/__init__.py:84-90`（导入）、`185-187`（`__all__`）

```python
from .replays import (
    ...
    run_graph_off,
    run_oracle_granularity,
    run_safety_off,
    ...
)

__all__ = [
    ...
    "run_graph_off",
    "run_oracle_granularity",
    "run_safety_off",
    ...
]
```

三个新回放函数被导出为公共 API，可通过 `from cmd_audit import run_oracle_granularity` 等方式访问。

## 测试结构

文件：`tests/test_cmd_audit_issue12_v1_labels.py`。10 个测试类，81 个测试方法。文件：`tests/test_cmd_audit_issue11_v1_labels.py`，3 个测试方法已更新。

| 测试类 | 测试方法数 | 覆盖的 TDD 行为 |
| --- | --- | --- |
| `V1LabelValidationTest` | 14 | `validate_v1_label` 接受全部 11 个 V1 标签、V0 子集，拒绝项目标签；`DEFERRED_PIPELINE_LABELS` 为空（0 个元素）；`validate_v0_label` 拒绝 `granularity_error`、`graph_error`、`safety_error`；新标签不在延迟中，且在 V1 中；`V1_REPLAY_TO_LABEL` 包含 3 个新映射；`REPLAY_TO_LABEL` 未更改 |
| `V1ProbeCaseLoadingTest` | 8 | `load_probe_cases_v1` 加载粒度、图、安全性夹具；`load_probe_cases` 拒绝全部三个；`granularity_level` 已解析；`is_graph_expanded` 已解析 |
| `OracleGranularityReplayTest` | 7 | `oracle_granularity` 回放名称；证据恢复；正 recovery_gain；证据块包含 "Barcelona"；在 V0 案例上零增益；在 route 案例上零增益；在所有级别相等时零增益 |
| `GraphOffReplayTest` | 7 | `graph_off` 回放名称；证据恢复；正 recovery_gain；来自直接项的证据块；在 V0 案例上零增益；在无扩展项时零增益；在安全性案例上零增益 |
| `SafetyOffReplayTest` | 7 | `safety_off` 回放名称；证据恢复；正 recovery_gain；证据块包含被阻止内容；在 V0 案例上零增益；在未阻止时零增益；在粒度案例上零增益 |
| `V1ReplayPortfolioTest` | 6 | 10 个回放计数；包含全部 3 个新回放；包含 `oracle_route`；包含所有 V0 回放；每个回放都有标签映射；新回放有有效标签 |
| `GranularityErrorAttributionTest` | 5 | `oracle_granularity` 具有正 recovery_gain；产生有效归因；`current_granularity` 为 "session"；`granularity_level` 为 "event"；没有 V0 案例获得 `granularity_error` |
| `GraphErrorAttributionTest` | 5 | `graph_off` 具有正 recovery_gain；产生有效归因；案例具有扩展项；案例具有包含证据的直接项；没有 V0 案例获得 `graph_error` |
| `SafetyErrorAttributionTest` | 4 | 安全性案例设置了被阻止标志；`safety_off` 产生正增益；`safety_error` 在 V1 标签中；没有 V0 案例获得 `safety_error` |
| `V1NonRegressionTest` | 6 | 所有 6 个 V0 标签通过 V1 管道匹配；没有 V0 案例获得 `granularity_error`、`graph_error`、`safety_error`；ingestion 案例仍然是 `ingestion_error`；完整的 6 标签集通过 V1 产生 |
| `V1ReplayToLabelMappingTest` | 4 | 所有映射都是有效的 V1 标签；V1 映射是 V0 的超集；新回放仅在 V1 中；10 个条目 |
| `V1ECSCompatibilityTest` | 4 | 粒度/图/安全性案例的完整 V1 管道 ECS 输出；所有新标签都存在修复操作 |
| `NewProbeCaseFieldsTest` | 6 | `granularity_levels` 默认值；`current_granularity` 默认值；`safety_filter_blocked` 默认值；`is_graph_expanded` 默认值；`granularity_level` 默认值；粒度案例级别已配置 |

### Issue 0011 测试更新

在 `tests/test_cmd_audit_issue11_v1_labels.py` 中更新了 3 个测试方法：

| 测试方法 | 变更 | 原因 |
| --- | --- | --- |
| `test_v1_label_order_has_eight_labels` | 重命名为 `test_v1_label_order_has_eleven_labels`，断言从 `8` 改为 `11` | V1 现在有 11 个标签（曾为 8 个） |
| `test_granularity_graph_safety_still_deferred` | 重命名为 `test_granularity_graph_safety_now_active`，将 `assertIn` 改为 `assertNotIn`（针对 DEFERRED），新增 `assertIn`（针对 V1_PIPELINE_LABELS） | 三个标签已从延迟移至活动 |
| `test_portfolio_has_seven_replays` | 重命名为 `test_portfolio_has_ten_replays`，断言从 `7` 改为 `10` | V1 组合现在有 10 个回放（曾为 7 个） |

## 非回归分析

对 V0 烟雾套件（`data/probe_cases/v0_issue3_cases.json`，6 个案例）和 issue 0011 夹具（ingestion、route）运行 11 标签 V1 管道产生：

| 案例 ID | 原始标签 | V1 11 标签预测标签 | 匹配？ | 备注 |
| --- | --- | --- | --- | --- |
| v0-write-001 | `write_error` | `write_error` | ✓ | 新回放在此案例上产生零增益（粒度守卫：所有级别相同；图守卫：无扩展项；安全性守卫：`safety_filter_blocked=False`） |
| v0-compression-001 | `compression_error` | `compression_error` | ✓ | 新回放产生零增益；`oracle_compression` 是唯一的正增益回放 |
| v0-premature-extraction-001 | `premature_extraction_error` | `premature_extraction_error` | ✓ | 新回放产生零增益 |
| v0-retrieval-001 | `retrieval_error` | `retrieval_error` | ✓ | 新回放可能在直接证据项上产生正增益，但 `oracle_retrieval` 在元组中先出现（位置 4 vs 位置 8-10） |
| v0-injection-001 | `injection_error` | `injection_error` | ✓ | 同上——平局时 `injection_oracle`（位置 5）获胜 |
| v0-reasoning-001 | `reasoning_error` | `reasoning_error` | ✓ | 同上——平局时 `evidence_given_reasoning`（位置 6）获胜 |
| v1-ingestion-001 | `ingestion_error` | `ingestion_error` | ✓ | `has_ingestion_trace=False` + `oracle_write` 仍然是最高增益 |
| v1-route-001 | `route_error` | `route_error` | ✓ | `oracle_route` 在具有多存储记忆项的案例上保持最高增益 |

所有 8 个现有标签保持不变（在烟雾套件 + issue 0011 夹具上 Macro F1 = 1.000）。没有案例将其预测标签翻转为新的 issue 0012 标签。三个新标签（`granularity_error`、`graph_error`、`safety_error`）都不作为任何现有案例的预测标签出现。

**新回放守卫在非回归中至关重要：**

1. `run_oracle_granularity` 的 `best_score <= current_score` 守卫：在大多数 V0 案例中，所有证据的 `granularity_level is None`，因此所有级别产生相同的证据分数。守卫返回零增益。
2. `run_graph_off` 的 `has_expanded` 守卫：大多数 V0 案例的 `is_graph_expanded=False` 适用于所有项。守卫返回零增益。
3. `run_safety_off` 的 `safety_filter_blocked` 守卫：大多数 V0 案例的 `safety_filter_blocked=False`。守卫返回零增益。

**关于独立工具中回放重叠的说明：** 在独立工具中，多个回放可能通过不同路径恢复相同的证据块。例如，如果具有直接证据的记忆项同时被基线检索到并被图扩展标记，`injection_oracle`、`oracle_retrieval` 和 `graph_off` 都可能恢复它。当它们具有相等的 recovery_gain 时，元组顺序（位置 4-6 在位置 8-10 之前）确保现有的回放获胜。在实际系统中（例如带有 Letta 适配器的 Graph RAG），图扩展是一个真正的系统操作，具有不同的 API——禁用它是其他回放无法复制的真正干预。
