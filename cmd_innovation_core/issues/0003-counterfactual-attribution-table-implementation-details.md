# Issue 0003 实现细节：反事实归因表

## 目的

本文档是 issue 0003《生成第一张反事实归因表》的全局实现地图。

Issue 0003 是 **CMD-Audit** 从单回放检索示踪子弹转变为一个有边界的 **V0 Replay Portfolio** 的切片：

```text
ProbeCase JSON
  -> ProbeCase 合约校验
  -> 基线套件与防泄漏 Subagent Judge Monitor
  -> V0 Replay Portfolio
      -> Oracle Write
      -> Oracle Compression
      -> Verbatim Event Oracle
      -> Oracle Retrieval
      -> Injection-Oracle
      -> Evidence-Given Reasoning
  -> 回放分数与 Recovery Gains
  -> Operation-Level Attribution
  -> 归因表
  -> 归因混淆矩阵
  -> CMD-vs-对比器 指标
```

该实现切片仍然在 **Error-Cause-Solution**、**Post-Repair Context Replay**、**Targeted Memory Fix**、**Failure Memory** 和 **CMD-Skill Adapter** 行为之前停止。这些仍是后续 issue。

## 源需求

本实现遵循以下本地文档。

| 来源 | 在 Issue 0003 中应用的需求 |
| --- | --- |
| `TASK.md` | 在 issue 0001/0002 之后构建 issue 0003；运行六个 V0 回放；生成归因表、对比指标和混淆矩阵；将 issue 0004 作为下一个切片。 |
| `CLAUDE.md` | 以 `cmd_innovation_core/` 为真理之源；将 **CMD-Audit** 与 **CMD-Skill Adapter** 分开；仅输出六个 V0 流水线标签。 |
| `cmd_innovation_core/CONTEXT.md` | 一致使用 **V0 Core Label Set**、**Counterfactual Replay**、**Recovery Gain**、**Operation-Level Attribution**、**Premature Extraction Error**、**Subagent Judge Baseline** 和 **Subagent Judge Monitor**。 |
| `cmd_innovation_core/prd/cmd_minimal_probe_prd.md` | 将回放引擎作为一个具有通用结果形态的深层模块；将回放增量转换为 top-1/top-2 归因；将 CMD 与启发式和 subagent judge 基线进行比较。 |
| `cmd_innovation_core/issues/0003-generate-counterfactual-attribution-table.md` | 包含 Oracle Write、Oracle Compression、Oracle Retrieval、Verbatim Event Oracle、Injection-Oracle 和 Evidence-Given Reasoning；为每个回放写入一个 recovery-gain 列；拒绝延迟的标签。 |
| `cmd_innovation_core/tdd/cmd_tracer_bullets.md` | 通过 harness 验证公共行为；保留 Verbatim Event Oracle 边界；将 Subagent Judge 输出与最终 CMD 归因分开。 |

## 领域边界

Issue 0003 拥有第一个 **Counterfactual Replay** 组合和归因证据产出物。

它拥有的内容：

- 运行六个 V0 回放干预；
- 用答案分数、证据分数和 Recovery Gain 评分每个回放；
- 将最强的回放映射到 **Operation-Level Attribution** 标签；
- 输出 top-1、top-2、歧义性、每个回放的增益列以及诊断成本；
- 写入 V0 归因混淆矩阵；
- 将对比器输出放在 CMD 输出旁边，而不让它们设置 CMD 标签。

它不拥有的内容：

- **Error-Cause-Solution** 构建；
- **Post-Repair Context Replay**；
- 针对性记忆修复操作；
- 未来的 **Failure Memory** 检索；
- **CMD-Skill Adapter** 集成；
- V0.5 的真实 BM25/向量/混合检索基线；
- 延迟标签：`granularity_error`、`route_error`、`graph_error`、`safety_error`；
- 坏的 Memory Item 标签：`item_wrong`、`item_stale`、`item_conflict`、`item_poisoned`、`item_compression_distorted`。

## 当前代码产出物

| 产出物 | 在 issue 0003 中的角色 |
| --- | --- |
| `cmd_audit/replays.py` | 拥有六个 V0 Counterfactual Replay 和通用的 `ReplayResult` 形态。 |
| `cmd_audit/attribution.py` | 将回放 Recovery Gains 转换为 Operation-Level Attribution。 |
| `cmd_audit/harness.py` | 公共案例运行器、归因表写入器、对比指标写入器和混淆矩阵写入器。 |
| `cmd_audit/labels.py` | 定义 V0 标签顺序、允许的标签集合和回放到标签的映射。 |
| `cmd_audit/models.py` | 加载探针合约并校验 `source_memory_id` 和 `source_event_id` 引用。 |
| `cmd_audit/scoring.py` | 提供回放和基线使用的确定性答案和证据评分器。 |
| `cmd_audit/baselines.py` | 从 issue 0002 提供固定摘要/向量基线状态、对比器标签和防泄漏 monitor 输出。 |
| `cmd_audit/metrics.py` | 计算 CMD-vs-对比器的归因准确率、宏 F1、top-2 准确率和成本。 |
| `cmd_audit/cli.py` | 通过 `python3 -m cmd_audit run` 暴露 issue 0003。 |
| `cmd_audit/__main__.py` | 启用模块执行。 |
| `cmd_audit/__init__.py` | 导出 issue 0003 的公共 API 接口。 |
| `data/probe_cases/v0_issue3_cases.json` | 六案例烟雾套件：每个 V0 流水线标签一个案例。 |
| `data/probe_cases/v0_premature_extraction_error_case.json` | 专注的 Verbatim Event Oracle 边界夹具。 |
| `tests/test_cmd_audit_issue3_attribution_table.py` | Issue 0003 的行为级测试。 |
| `artifacts/attribution_table.csv` | 生成的逐案例归因表，包含每个回放的分数和增益列。 |
| `artifacts/comparison_metrics.csv` | 生成的 CMD-vs-对比器指标表。 |
| `artifacts/attribution_confusion_matrix.csv` | 生成的 V0 归因混淆矩阵。 |

## 全局模块地图

```text
cmd_audit.__main__
  -> cli.main
      -> models.load_probe_cases
          -> ProbeCase.from_mapping
              -> RawEvent.from_mapping
              -> MemoryItem.from_mapping
              -> GoldEvidence.from_mapping
              -> BaselineOutput.from_mapping
              -> ScoringSpec.from_mapping
              -> labels.validate_v0_label
              -> ProbeCase.validate
      -> harness.run_cases
          -> harness.run_case
              -> baselines.run_baseline_suite
                  -> run_memory_baselines
                  -> run_evidence_recall_heuristic
                  -> run_subagent_judge_baseline
                  -> run_random_label_baseline
                  -> run_subagent_judge_monitor
              -> replays.run_v0_replay_portfolio
                  -> run_oracle_write
                  -> run_oracle_compression
                  -> run_verbatim_event_oracle
                  -> run_oracle_retrieval
                  -> run_injection_oracle
                  -> run_evidence_given_reasoning
                  -> _score_recovered_evidence
                      -> scoring.evidence_recall_from_text
                      -> scoring.answer_score
              -> attribution.assign_attribution
                  -> attribution._label_for_replay
                  -> labels.validate_v0_label
      -> harness.write_attribution_table
      -> harness.write_comparison_metrics_table
          -> harness.diagnosis_predictions
          -> metrics.compute_diagnosis_metrics
      -> harness.write_confusion_matrix_table
```

领域解读：

- `models.py` 拥有 **Memory Failure** 探针合约。
- `baselines.py` 拥有来自 issue 0002 的非 CMD 基线/对比器/monitor 状态。
- `replays.py` 拥有 issue 0003 的 **Counterfactual Replay** 干预。
- `scoring.py` 拥有确定性证据和答案评分。
- `attribution.py` 拥有 **Recovery Gain** 排序和 **Operation-Level Attribution**。
- `harness.py` 是 **CMD-Audit** 的公共接口。
- `metrics.py` 将 CMD 和对比器预测转换为声明门控指标。
- `cli.py` 是独立的研究 harness 运行器，而非 **CMD-Skill Adapter**。

## 数据流

输入：

```text
data/probe_cases/v0_issue3_cases.json
```

输出：

```text
artifacts/attribution_table.csv
artifacts/comparison_metrics.csv
artifacts/attribution_confusion_matrix.csv
```

对于每个案例：

```text
ProbeCase
  -> BaselineSuiteResult
  -> tuple[ReplayResult, ...]
  -> AttributionResult
  -> AuditResult
```

对于所有案例：

```text
list[AuditResult]
  -> attribution_table.csv
  -> comparison_metrics.csv
  -> attribution_confusion_matrix.csv
```

## 探针夹具

### `data/probe_cases/v0_issue3_cases.json`

此夹具是 issue 0003 的烟雾套件。每个 V0 流水线标签包含一个案例。

| 案例 ID | 金标准标签 | 预期 top 回放 | 正在测试的边界 |
| --- | --- | --- | --- |
| `v0-write-001` | `write_error` | `oracle_write` | 金标准证据评估者已知，但在原始事件或提取后的记忆中均不存在。 |
| `v0-compression-001` | `compression_error` | `oracle_compression` | 金标准证据指向一个 Memory Item，但存储的文本丢失了必需的短语。 |
| `v0-premature-extraction-001` | `premature_extraction_error` | `verbatim_event_oracle` | 原始事件保留了证据，但没有可恢复的提取后 Memory Item。 |
| `v0-retrieval-001` | `retrieval_error` | `oracle_retrieval` | 正确的 Memory Item 存在，但基线检索到了一个干扰项。 |
| `v0-injection-001` | `injection_error` | `injection_oracle` | 正确的 Memory Item 已被检索，但注入的上下文遗漏了可用的证据。 |
| `v0-reasoning-001` | `reasoning_error` | `evidence_given_reasoning` | 基线上下文已包含证据，但基线答案错误。 |

### `data/probe_cases/v0_premature_extraction_error_case.json`

此专注夹具保护最重要的标签边界：

```text
原始事件证据可恢复
提取后的记忆证据不可恢复
Oracle Retrieval gain = 0
Verbatim Event Oracle gain = 1
预测标签 = premature_extraction_error
```

它有意使用 `source_event_id` 并省略 `source_memory_id`。这表示提取损失，而非格式错误的记忆引用。

## 公共结果形态

### `ReplayResult`

定义在 `cmd_audit/replays.py`。

字段：

- `replay_name`：`REPLAY_TO_LABEL` 使用的规范回放键。
- `answer`：回放答案字符串。
- `answer_score`：针对 `gold_answer` 的确定性分数。
- `evidence_score`：针对金标准证据短语的确定性召回率。
- `evidence_block`：回放使用的证据文本。
- `recovery_gain`：回放答案分数减去基线答案分数。
- `cost_units`：简单的诊断成本计量值。默认为 `1.0`。

### `AttributionResult`

定义在 `cmd_audit/attribution.py`。

字段：

- `predicted_label`：从 top 回放映射的 V0 流水线标签。
- `top_replay`：具有最大正 Recovery Gain 的回放。
- `recovery_gain`：top 回放的增益。
- `top2_labels`：在 `tie_margin` 范围内接近 top 回放的标签，最多两个标签。
- `is_ambiguous`：当 `top2_labels` 有多个标签时为 true。

### `AuditResult`

定义在 `cmd_audit/harness.py`。

字段：

- `case_id`
- `perturbation_label`
- `baseline_name`
- `baseline_answer_score`
- `baseline_evidence_score`
- `replays`
- `attribution`
- `baseline_suite`

属性：

- `attribution_correct`
- `replay`
- `diagnosis_cost`

方法：

- `replay_by_name(...)`

旧的单回放接口由 `AuditResult.replay` 保留，它返回 top 回放。新的 issue 0003 行为在需要完整组合时应使用 `AuditResult.replays`。

## 函数级合约

### `cmd_audit/replays.py`

此模块拥有 issue 0003 的 **V0 Replay Portfolio**。

#### `ReplayResult`

目的：

- 每个 Counterfactual Replay 共享的不可变结果形态。

被以下使用：

- 所有 `run_*` 回放函数；
- `assign_attribution(...)`；
- `AuditResult.replays`；
- `write_attribution_table(...)`。

领域含义：

- 一个回放结果是对 **Memory Pipeline** 的一次受控干预。
- `recovery_gain` 是用于 **Operation-Level Attribution** 的证据。

#### `run_v0_replay_portfolio(case: ProbeCase) -> tuple[ReplayResult, ...]`

目的：

- 按稳定的表格顺序运行全部六个 V0 回放干预。

当前顺序：

1. `oracle_write`
2. `oracle_compression`
3. `verbatim_event_oracle`
4. `oracle_retrieval`
5. `injection_oracle`
6. `evidence_given_reasoning`

行为：

- 每个回放函数调用一次。
- 返回一个元组，直接由 `assign_attribution(...)` 消费。

调用者：

- `harness.run_case(...)`
- 从 `cmd_audit.__init__` 导出
- 被 `test_issue3_suite_attributes_all_v0_pipeline_labels` 测试

为什么 issue 0003 需要它：

- 集中管理有边界的 V0 回放集合，使表格生成、归因和测试不会各自发明不同的回放列表。

边界：

- 它不得包含延迟的 V1/V2 回放，如 route、graph、granularity 或 safety。

#### `run_oracle_write(case: ProbeCase) -> ReplayResult`

目的：

- 诊断 `write_error`。

行为：

- 从同时缺少 `source_memory_id` 和 `source_event_id` 的金标准证据构建 `evidence_block`。
- 通过 `_score_recovered_evidence(...)` 对该块评分。
- 正增益意味着评估者已知的证据在原始事件或提取后的记忆中均不存在，因此 Oracle Write 干预恢复了它。

夹具示例：

- `v0-write-001`

调用者：

- `run_v0_replay_portfolio(...)`

边界：

- 此回放仅将金标准证据用作评估的 oracle 干预。它不是生产环境的记忆写入。

#### `run_oracle_compression(case: ProbeCase) -> ReplayResult`

目的：

- 诊断 `compression_error`。

行为：

- 查找每个金标准证据项的 `source_memory_id`。
- 如果 Memory Item 存在但其文本不满足 `evidence_recall_from_text((evidence,), memory.text)`，则回放恢复 `evidence.text`。
- 通过 `_score_recovered_evidence(...)` 对恢复的证据评分。

夹具示例：

- `v0-compression-001`

为什么这不是检索：

- 金标准证据指向一个提取后的 Memory Item，但该项目的表示过于有损，无法恢复必需的短语。

调用者：

- `run_v0_replay_portfolio(...)`

#### `run_oracle_retrieval(case: ProbeCase) -> ReplayResult`

目的：

- 诊断 `retrieval_error`。

行为：

- 调用 `_recover_extracted_gold_evidence(...)`。
- 通过 `_score_recovered_evidence(...)` 对恢复的提取后记忆证据评分。

夹具示例：

- `v0-retrieval-001`

关键边界：

- 检索只能恢复满足以下条件的证据：
  - 具有 `source_memory_id`；
  - 指向一个存在的 Memory Item；
  - 未被基线检索到；
  - 确实存在于该 Memory Item 的文本中。

为什么跳过已被检索的证据：

- 如果基线检索到了正确的 Memory Item 但注入或推理失败，则该失败不是检索遗漏。

调用者：

- `run_v0_replay_portfolio(...)`
- 当此回放为 top 时，遗留测试仍检查 `result.replay`。

#### `run_verbatim_event_oracle(case: ProbeCase) -> ReplayResult`

目的：

- 诊断 `premature_extraction_error`。

行为：

- 调用 `_recover_raw_event_only_gold_evidence(...)`。
- 通过 `_score_recovered_evidence(...)` 对原始事件证据评分。

夹具示例：

- `v0_premature_extraction_error_case.json`
- `v0_issue3_cases.json` 中的 `v0-premature-extraction-001`

关键边界：

- 金标准证据必须具有 `source_event_id`。
- 当没有提取后的 Memory Item 保留它时，金标准证据必须省略 `source_memory_id`。
- 这避免了将提取损失编码为损坏的记忆指针。

调用者：

- `run_v0_replay_portfolio(...)`

#### `run_injection_oracle(case: ProbeCase) -> ReplayResult`

目的：

- 诊断 `injection_error`。

行为：

- 读取 `case.primary_baseline`。
- 如果基线 `injected_context` 已经召回了所有金标准证据，返回空块和零增益。这防止推理案例被错误归因到注入。
- 否则，检查基线是否检索到了文本包含金标准证据的 Memory Item。
- 如果是，将该 Memory Item 文本恢复为干净的证据块。
- 通过 `_score_recovered_evidence(...)` 评分。

夹具示例：

- `v0-injection-001`

为什么这不是检索：

- 正确的 Memory Item 已被检索。失败发生在证据被格式化或注入到模型上下文的环节。

调用者：

- `run_v0_replay_portfolio(...)`

#### `run_evidence_given_reasoning(case: ProbeCase) -> ReplayResult`

目的：

- 诊断 `reasoning_error`。

行为：

- 读取 `case.primary_baseline`。
- 如果基线 `injected_context` 已经召回了所有金标准证据且 `baseline.answer_score < 1.0`，使用基线上下文作为证据块。
- 否则，返回空的证据块。
- 通过 `_score_recovered_evidence(...)` 评分。

夹具示例：

- `v0-reasoning-001`

为什么这不是注入：

- 基线上下文已有证据。失败在于对有效证据的最终答案推理。

调用者：

- `run_v0_replay_portfolio(...)`

#### `_score_recovered_evidence(case, replay_name, evidence_block) -> ReplayResult`

目的：

- 所有回放函数共享的评分辅助函数。

行为：

- 计算 `evidence_score = evidence_recall_from_text(case.gold_evidence, evidence_block)`。
- 仅当 `evidence_score == 1.0` 时设置回放 `answer = case.gold_answer`。
- 计算 `answer_score(answer, case.gold_answer)`。
- 计算 `recovery_gain = 恢复后的答案分数 - case.primary_baseline.answer_score`。
- 返回一个 `ReplayResult`。

为什么 issue 0003 需要它：

- 保持回放函数简洁，并使所有回放增量可比较。

边界：

- 这是合成 oracle 评分。不应被误认为是生产环境的答案生成。

#### `_recover_extracted_gold_evidence(case: ProbeCase) -> str`

目的：

- Oracle Retrieval 的辅助函数。

行为：

- 从 `case.extracted_memory` 构建 `memory_by_id`。
- 从 `case.primary_baseline.retrieved_memory_ids` 构建 `baseline_retrieved_ids`。
- 对于每个金标准证据：
  - 如果没有 `source_memory_id` 则跳过；
  - 如果基线已经检索了该记忆 ID 则跳过；
  - 仅当 Memory Item 文本满足该证据的短语召回时才恢复其文本。
- 用换行符连接恢复的记忆文本。

Issue 0003 边界：

- 防止 `injection_error` 和 `reasoning_error` 案例被 Oracle Retrieval 错误恢复。

#### `_recover_raw_event_only_gold_evidence(case: ProbeCase) -> str`

目的：

- Verbatim Event Oracle 的辅助函数。

行为：

- 从 `case.raw_events` 构建 `event_by_id`。
- 对于每个金标准证据：
  - 如果有 `source_memory_id` 则跳过；
  - 当 `source_event_id` 存在且指向真实事件时恢复原始事件文本。
- 用换行符连接恢复的原始事件。

Issue 0003 边界：

- 只恢复仅存在于原始事件中的证据。在提取后存活的证据属于 retrieval/compression/injection/reasoning 路径。

### `cmd_audit/attribution.py`

此模块拥有从回放增量进行的 **Operation-Level Attribution**。

#### `AttributionResult`

目的：

- 单个案例的不可变归因输出。

字段：

- `predicted_label`
- `top_replay`
- `recovery_gain`
- `top2_labels`
- `is_ambiguous`

被以下使用：

- `AuditResult.attribution`
- `write_attribution_table(...)`
- `diagnosis_predictions(...)`
- issue 0003 测试

#### `assign_attribution(replay_results, positive_gain_threshold=0.0, tie_margin=0.05) -> AttributionResult`

目的：

- 将 `ReplayResult` 对象的元组转换为一个 CMD 归因。

行为：

- 要求至少一个回放结果。
- 按 `recovery_gain` 降序排序回放结果。
- 如果 top 增益不为正，则拒绝该案例。
- 通过 `_label_for_replay(...)` 将 `top.replay_name` 映射到 V0 标签。
- 从在 `tie_margin` 范围内接近 top 回放的回放标签构建 `top2_labels`，最多两个标签。
- 当存在多个相近标签时标记 `is_ambiguous`。

调用者：

- `harness.run_case(...)`
- 早期 issue 覆盖中的直接测试。

Issue 0003 含义：

- 最终 CMD 归因来自基于干预的 Recovery Gains，而非来自启发式或 subagent judge 输出。

#### `_label_for_replay(replay_name: str) -> str`

目的：

- 将回放名称映射到其所诊断的 V0 流水线标签。

行为：

- 读取 `REPLAY_TO_LABEL`。
- 对未知回放名称抛出 `ValueError`。

为什么 issue 0003 需要它：

- 强制组合中的每个回放都有一个显式的标签映射。

### `cmd_audit/harness.py`

此模块是 issue 0003 的 **CMD-Audit** 公共接口。

#### 常量：`REPLAY_TABLE_ORDER`

定义：

```python
REPLAY_TABLE_ORDER = (
    "oracle_write",
    "oracle_compression",
    "verbatim_event_oracle",
    "oracle_retrieval",
    "injection_oracle",
    "evidence_given_reasoning",
)
```

目的：

- 保持归因表列顺序稳定。
- 与 V0 回放顺序和 V0 标签顺序一致。

被以下使用：

- `write_attribution_table(...)`

#### `AuditResult`

目的：

- 单个探针案例的公共结果对象。

字段：

- `case_id`
- `perturbation_label`
- `baseline_name`
- `baseline_answer_score`
- `baseline_evidence_score`
- `replays`
- `attribution`
- `baseline_suite`

Issue 0003 变更：

- `replays` 替换了旧的单回放存储。
- `replay` 属性保留了旧的 top 回放便捷接口。

#### `AuditResult.attribution_correct`

目的：

- 烟雾夹具和表格的便捷检查。

行为：

- 返回 `attribution.predicted_label == perturbation_label`。

被以下使用：

- `write_attribution_table(...)`
- 测试

#### `AuditResult.replay`

目的：

- 向后兼容的 top 回放访问器。

行为：

- 调用 `replay_by_name(self.attribution.top_replay)`。

被以下使用：

- 现有的检索示踪子弹测试；
- `write_attribution_table(...)` 通用 top 回放列。

#### `AuditResult.diagnosis_cost`

目的：

- 计算 issue 0003 的诊断成本。

行为：

- 将 `baseline_suite.monitor.cost_per_decision` 加上 `replays` 中每个回放的 `replay.cost_units` 之和。
- 以六个默认回放成本和 monitor 成本 `0.2` 计算，当前烟雾成本为 `6.2`。

被以下使用：

- `write_attribution_table(...)`
- `diagnosis_predictions(...)`

#### `AuditResult.replay_by_name(replay_name: str) -> ReplayResult`

目的：

- 在回放组合中查找一个回放结果。

行为：

- 返回按 `replay_name` 匹配的回放。
- 如果回放未运行则抛出 `KeyError`。

被以下使用：

- `AuditResult.replay`
- 检查命名回放的测试。

#### `run_case(case: ProbeCase) -> AuditResult`

目的：

- 为单个案例运行完整的 issue 0003 公共路径。

行为：

1. 调用 `run_baseline_suite(case)`。
2. 调用 `run_v0_replay_portfolio(case)`。
3. 调用 `assign_attribution(replays)`。
4. 从基线、回放、归因和对比器上下文构建 `AuditResult`。

调用者：

- `run_cases(...)`
- 测试

边界：

- Subagent judge 和 evidence-recall 输出包含在 `baseline_suite` 中，但它们不设置 `attribution`。

#### `run_cases(cases: list[ProbeCase]) -> list[AuditResult]`

目的：

- CLI 和测试的批量运行器。

行为：

- 为每个加载的探针案例调用 `run_case(...)`。

调用者：

- `cli.main(...)`
- issue 0003 测试

#### `write_attribution_table(results, output_path) -> None`

目的：

- 写入 issue 0003 的 **归因表**。

行为：

- 创建父目录。
- 构建字段名，包含：
  - 案例标识；
  - 金标准和预测标签；
  - top 回放；
  - 基线分数；
  - 通用 top 回放分数；
  - `REPLAY_TABLE_ORDER` 中每个回放的逐回放答案/证据/增益列；
  - top-2 标签；
  - 歧义标志；
  - 诊断成本；
  - 正确性标志。
- 每个 `AuditResult` 写入一行。

生成的文件：

- `artifacts/attribution_table.csv`

Issue 0003 测试：

- `test_issue3_table_contains_per_replay_gain_columns`

#### `diagnosis_predictions(result: AuditResult) -> tuple[DiagnosisPrediction, ...]`

目的：

- 将一个 `AuditResult` 转换为可比较的系统预测。

行为：

- 使用回放增量归因为 `CMD-Audit` 创建一个 `DiagnosisPrediction`。
- 为 `baseline_suite.comparator_results` 中的每个对比器创建一个预测：
  - evidence recall 启发式；
  - subagent judge 基线；
  - random label 基线。

被以下使用：

- `write_comparison_metrics_table(...)`
- issue 0002 测试

边界：

- 对比器预测仅用于比较报告。它们不修改 CMD 归因。

#### `write_comparison_metrics_table(results, output_path) -> None`

目的：

- 写入 CMD-vs-对比器诊断指标。

行为：

- 展平所有结果的 `diagnosis_predictions(...)`。
- 调用 `compute_diagnosis_metrics(...)`。
- 写入 `system_name`、`attribution_accuracy`、`macro_f1`、`top2_accuracy` 和 `cost_per_diagnosis`。

生成的文件：

- `artifacts/comparison_metrics.csv`

#### `write_confusion_matrix_table(results, output_path) -> None`

目的：

- 写入证据关卡所需的 V0 归因混淆矩阵。

行为：

- 使用 `V0_PIPELINE_LABEL_ORDER` 初始化一个方形计数表。
- 为每个结果递增 `counts[gold_label][predicted_label]`。
- 每个金标准标签写入一行。

生成的文件：

- `artifacts/attribution_confusion_matrix.csv`

Issue 0003 测试：

- `test_confusion_matrix_contains_one_diagonal_count_per_v0_label`

### `cmd_audit/models.py`

Issue 0001 拥有完整的探针合约。Issue 0003 为仅原始事件的证据扩展了合约边界。

#### `GoldEvidence.from_mapping(...)`

Issue 0003 相关性：

- 允许 `source_event_id` 而不带 `source_memory_id`。
- 这是 `premature_extraction_error` 所必需的。

#### `ProbeCase.validate(self) -> None`

Issue 0003 行为：

- 仍然要求非空的 `raw_events`、`extracted_memory`、`gold_evidence` 和 `baseline_outputs`。
- 校验 `source_memory_id`（当存在时）指向一个存在的提取后 Memory Item。
- 校验 `source_event_id`（当存在时）指向一个存在的原始事件。

为什么 issue 0003 需要它：

- 当证据指向真实原始事件并省略 `source_memory_id` 时，仅原始事件的案例是有效的。
- 缺失 `source_memory_id` 的案例仍然无效。这保护 retrieval-error 夹具不隐藏格式错误的引用。

#### `load_probe_cases(path) -> list[ProbeCase]`

Issue 0003 行为：

- 加载单个 JSON 对象或 JSON 对象列表。
- `v0_issue3_cases.json` 使用列表形式。
- `v0_premature_extraction_error_case.json` 使用单对象形式。

### `cmd_audit/scoring.py`

Issue 0003 使用来自 issue 0001 的确定性评分器。

#### `answer_score(answer, gold_answer) -> float`

目的：

- Casefold 精确匹配，带标点修剪。

Issue 0003 用途：

- `_score_recovered_evidence(...)` 计算回放答案分数。

#### `evidence_recall_from_text(gold_evidence, text) -> float`

目的：

- 针对证据块检查每个金标准证据项的必需短语。

Issue 0003 用途：

- 每个回放使用它来决定其证据块是否恢复了案例。
- 回放辅助函数使用它来避免将 retrieved/injected/reasoning 案例归因到错误的干预。

#### `evidence_recall_from_memory_ids(case, memory_ids) -> float`

Issue 0003 用途：

- 被包含在 issue 0003 输出中的 issue 0002 对比器逻辑使用。

#### `_normalize(value: str) -> str`

Issue 0003 用途：

- 支持答案评分。

### `cmd_audit/labels.py`

#### `V0_PIPELINE_LABEL_ORDER`

目的：

- 定义 V0 标签的稳定顺序。

Issue 0003 用途：

- 驱动混淆矩阵的行/列顺序。
- 被测试用于断言所有标签均被覆盖。

#### `REPLAY_TO_LABEL`

目的：

- 将每个回放名称映射到其操作级标签。

当前 issue 0003 映射：

| 回放 | 标签 |
| --- | --- |
| `oracle_write` | `write_error` |
| `oracle_compression` | `compression_error` |
| `verbatim_event_oracle` | `premature_extraction_error` |
| `oracle_retrieval` | `retrieval_error` |
| `injection_oracle` | `injection_error` |
| `evidence_given_reasoning` | `reasoning_error` |

被以下使用：

- `attribution._label_for_replay(...)`

#### `validate_v0_label(label: str) -> str`

Issue 0003 用途：

- 校验夹具 `perturbation_label`。
- 校验归因和对比器指标中的预测标签。
- 防止 issue 0003 意外接受延迟的或坏的 Memory Item 标签。

### `cmd_audit/metrics.py`

Issue 0002 拥有大部分指标细节。Issue 0003 将这些指标作为归因证据包的一部分进行消费。

#### `DiagnosisPrediction`

Issue 0003 用途：

- 以一种规范化的形态表示 CMD-Audit 和对比器输出。
- 通过 `validate_v0_label(...)` 校验金标准、预测和 top-2 标签。

#### `DiagnosisMetrics`

Issue 0003 用途：

- `comparison_metrics.csv` 的行形态。

#### `compute_diagnosis_metrics(predictions, labels=None) -> dict[str, DiagnosisMetrics]`

Issue 0003 用途：

- 在六案例烟雾套件上聚合 CMD-Audit、evidence recall、subagent judge 和 random-label 预测。

当前烟雾输出：

- CMD-Audit 宏 F1 为 `1.000`。
- Evidence recall 和 subagent judge 在烟雾套件上较低，因为它们是基于观察的对比器。

#### `_observed_labels(...)`、`_top2_correct(...)`、`_macro_f1(...)`、`_label_f1(...)`

Issue 0003 用途：

- 对比指标计算的私有辅助函数。

### `cmd_audit/cli.py`

#### `main(argv: list[str] | None = None) -> int`

目的：

- 独立的命令行入口点。

Issue 0003 行为：

- 默认 `--cases` 为 `data/probe_cases/v0_issue3_cases.json`。
- 默认 `--out` 为 `artifacts/attribution_table.csv`。
- 默认 `--metrics-out` 为 `artifacts/comparison_metrics.csv`。
- 默认 `--confusion-out` 为 `artifacts/attribution_confusion_matrix.csv`。

执行路径：

```text
load_probe_cases
-> run_cases
-> write_attribution_table
-> write_comparison_metrics_table
-> write_confusion_matrix_table
```

示例：

```bash
python3 -m cmd_audit run
```

边界：

- 这是本地研究 harness CLI，而非生产适配器。

### `cmd_audit/__main__.py`

目的：

- 允许 `python3 -m cmd_audit run`。

行为：

- 从 `cmd_audit.cli` 导入 `main`。
- 抛出 `SystemExit(main())`。

### `cmd_audit/__init__.py`

目的：

- 暴露测试和未来本地调用者使用的小型公共 API。

Issue 0003 导出：

- `ReplayResult`
- `run_v0_replay_portfolio`
- `write_confusion_matrix_table`

保留的现有导出：

- `run_case`
- `run_cases`
- `write_attribution_table`
- `write_comparison_metrics_table`
- `assign_attribution`
- `load_probe_cases`
- `validate_v0_label`
- 指标和结果数据类

## 测试级合约

### `tests/test_cmd_audit_issue3_attribution_table.py`

此文件是 issue 0003 的行为级规范。

#### `test_raw_event_only_evidence_is_valid_probe_case`

验证：

- 仅原始事件的金标准证据加载成功；
- `perturbation_label` 为 `premature_extraction_error`；
- `source_event_id` 存在；
- `source_memory_id` 缺失。

为什么重要：

- 它保护 Verbatim Event Oracle 夹具合约。

#### `test_verbatim_event_oracle_beats_oracle_retrieval_for_extraction_loss`

验证：

- Oracle Retrieval 的答案和证据分数为 `0.0`；
- Verbatim Event Oracle 回答 `Berlin`；
- Verbatim Event Oracle 的答案和证据分数为 `1.0`；
- 归因预测 `premature_extraction_error`；
- top 回放为 `verbatim_event_oracle`。

为什么重要：

- 它保护 issue 0003 最重要的边界：提取损失不是检索遗漏。

#### `test_issue3_table_contains_per_replay_gain_columns`

验证：

- 归因表包含所有六个回放路径的 recovery-gain 列；
- premature extraction 行包含预期的预测标签和 top 回放。

为什么重要：

- 它保护 issue 0003 所需的证据产出物形态。

#### `test_issue3_suite_attributes_all_v0_pipeline_labels`

验证：

- `v0_issue3_cases.json` 精确覆盖六个 V0 标签；
- 每个标签映射到预期的 top 回放；
- 每个 `AuditResult` 有六个回放结果；
- 每个烟雾归因正确。

为什么重要：

- 它证明了 V0 Replay Portfolio 对于有界烟雾套件是完整的。

#### `test_confusion_matrix_contains_one_diagonal_count_per_v0_label`

验证：

- 混淆矩阵表头遵循 `V0_PIPELINE_LABEL_ORDER`；
- 每个 V0 标签有一个对角线计数。

为什么重要：

- 它保护归因证据关卡不只有表格形式。

## 产出物合约

### `artifacts/attribution_table.csv`

必需列包括：

- `case_id`
- `perturbation_label`
- `predicted_label`
- `top_replay`
- `baseline_name`
- `baseline_answer_score`
- `baseline_evidence_score`
- `replay_answer_score`
- `replay_evidence_score`
- `recovery_gain`
- 全部六个回放的逐回放答案/证据/增益列
- `top2_labels`
- `is_ambiguous`
- `diagnosis_cost`
- `attribution_correct`

当前烟雾行：

- `v0-write-001`
- `v0-compression-001`
- `v0-premature-extraction-001`
- `v0-retrieval-001`
- `v0-injection-001`
- `v0-reasoning-001`

### `artifacts/comparison_metrics.csv`

必需系统：

- `CMD-Audit`
- `evidence_recall`
- `subagent_judge`
- `random_label`

必需列：

- `system_name`
- `attribution_accuracy`
- `macro_f1`
- `top2_accuracy`
- `cost_per_diagnosis`

### `artifacts/attribution_confusion_matrix.csv`

必需的行和列：

- `write_error`
- `compression_error`
- `premature_extraction_error`
- `retrieval_error`
- `injection_error`
- `reasoning_error`

当前烟雾矩阵：

- 每个 V0 标签一个对角线计数。

## 边界规则

- 最终归因来自回放增量，而非来自 evidence-recall 启发式或 Subagent Judge Baseline。
- Subagent Judge Monitor 可触发回放，但不能输出标签、ECS、记忆写入、金标准答案或完整失败追踪。
- V0 归因仅输出 `write_error`、`compression_error`、`premature_extraction_error`、`retrieval_error`、`injection_error` 和 `reasoning_error`。
- 不要在 issue 0003 中添加 `granularity_error`、`route_error`、`graph_error` 或 `safety_error` 列。
- 不要在 issue 0003 归因输出中添加坏的 Memory Item 标签。
- 不要在 issue 0003 中添加 ECS、Post-Repair Context Replay、针对性修复、Failure Memory 或 CMD-Skill Adapter 行为。

## 验证

命令：

```bash
python3 -m pytest
python3 -m cmd_audit run
```

当前已验证状态：

```text
16 tests passed
wrote 6 attribution row(s) to artifacts/attribution_table.csv
with comparison metrics to artifacts/comparison_metrics.csv
and confusion matrix to artifacts/attribution_confusion_matrix.csv
```

## Issue 0003 之后的剩余工作

Issue 0003 对有界 V0 烟雾套件是绿色的。下一个切片是 issue 0004：

- 从第一张表格审查分类边界；
- 确认 `premature_extraction_error` 保持与 `retrieval_error` 的区别；
- 明确耦合失败的 top-2 或多标签行为；
- 将坏的 Memory Item 标签排除在 V0 评分之外；
- 然后在 issue 0005 中进入 Post-Repair Context Replay。
