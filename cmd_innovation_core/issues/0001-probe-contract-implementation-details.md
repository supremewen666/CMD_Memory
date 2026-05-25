# Issue 0001 实现细节：探针数据集与黄金证据合约

## 目的

本文档是 issue 0001（`Define the probe dataset and gold evidence contract`）的宏观实现地图。

Issue 0001 是 **CMD-Audit** 的第一个可执行切片，而非整个 CMD 系统。其职责是让一个带标记的 **Memory Failure** 能够通过第一条示踪弹路径加载和诊断：

```text
ProbeCase JSON
  -> ProbeCase contract validation
  -> baseline failed output
  -> Oracle Retrieval counterfactual replay
  -> replay delta / Recovery Gain
  -> Operation-Level Attribution
  -> attribution_table.csv row
```

当前实现的切片刻意在完整的重放引擎、**Error-Cause-Solution**、**Post-Repair Context Replay**、**Subagent Judge Monitor** 或 **CMD-Skill Adapter** 之前停止。这些属于后续 issue 的切片。

## 源需求

本实现遵循以下本地规划文件：

- `TASK.md`
  - 从 issue 0001 和第一条 TDD 示踪弹开始。
  - 定义探针案例合约。
  - 包含原始事件、提取后的记忆、黄金证据、黄金答案、基线输出、扰动标签和评分字段。
  - 添加一个最小的 `retrieval_error` 案例：提取后的记忆包含黄金证据，基线检索遗漏了它，Oracle Retrieval 恢复了答案。
  - 在第一条红绿路径存在之前，不要实现完整的重放引擎。
- `CLAUDE.md`
  - 将 `cmd_innovation_core/` 视为事实来源。
  - 保持 **CMD-Audit** 与 **CMD-Skill Adapter** 分离。
  - 将 V0 范围限定在六个 pipeline 标签内。
  - 在 V0 归因中不要输出坏的 Memory Item 标签。
- `cmd_innovation_core/CONTEXT.md`
  - 精确使用 **Memory Failure**、**Memory Item**、**Memory Pipeline**、**Counterfactual Replay**、**Recovery Gain** 和 **Operation-Level Attribution**。
  - `retrieval_error` 表示正确记忆以可恢复形式存在但未被检索到。
  - 如果原始事件包含证据但提取后的记忆无法恢复它，后续代码必须优先使用 `premature_extraction_error`，而非 `retrieval_error`。
- `cmd_innovation_core/prd/cmd_minimal_probe_prd.md`
  - 将探针数据集作为第一个深层模块。
  - 将重放引擎作为第二个深层模块。
  - 将归因层作为第三个深层模块。
  - 首先使用基于规则的重放增量。
- `cmd_innovation_core/tdd/cmd_tracer_bullets.md`
  - 周期 1：可恢复的提取后记忆 + 基线检索失败 + Oracle Retrieval 成功 = `retrieval_error`。

## 当前代码工件

| 工件 | 在 issue 0001 中的角色 |
| --- | --- |
| `cmd_audit/models.py` | 探针案例合约和 JSON 加载器。 |
| `cmd_audit/labels.py` | V0 标签边界和重放到标签的映射。 |
| `cmd_audit/scoring.py` | 确定性的答案/证据评分辅助函数。 |
| `cmd_audit/replays.py` | 第一个 Counterfactual Replay：Oracle Retrieval。 |
| `cmd_audit/attribution.py` | Recovery Gain 排序和 Operation-Level Attribution。 |
| `cmd_audit/harness.py` | 公开的 harness 入口点和 CSV 表格写入器。 |
| `cmd_audit/cli.py` | 独立研究 harness 的 CLI 入口点。 |
| `cmd_audit/__main__.py` | 支持 `python3 -m cmd_audit ...`。 |
| `cmd_audit/__init__.py` | 小型公开导入接口。 |
| `data/probe_cases/v0_retrieval_error_case.json` | 第一个可执行的合成探针案例。 |
| `tests/test_cmd_audit_tracer_bullet.py` | 第一条示踪弹和标签边界的行为级测试。 |
| `artifacts/attribution_table.csv` | 第一个人工生成的归因证据工件。 |

## 宏观模块地图

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
              -> replays.run_oracle_retrieval
                  -> ProbeCase.primary_baseline
                  -> replays._recover_extracted_gold_evidence
                  -> scoring.evidence_recall_from_text
                  -> scoring.answer_score
              -> attribution.assign_attribution
                  -> attribution._label_for_replay
                  -> labels.validate_v0_label
      -> harness.write_attribution_table
```

领域含义：

- `models.py` 拥有 **Memory Failure** 探针合约。
- `labels.py` 强制实施 **V0 Core Label Set**。
- `replays.py` 运行受控的 **Counterfactual Replay**。
- `scoring.py` 计算答案和证据得分。
- `attribution.py` 将 **Recovery Gain** 转换为 **Operation-Level Attribution**。
- `harness.py` 是此第一切片的 **CMD-Audit** 公开接口。
- `cli.py` 是独立运行器，而非 **CMD-Skill Adapter**。

## 探针案例合约

`data/probe_cases/v0_retrieval_error_case.json` 中的 JSON fixture 即为具体的合约示例。

必需的顶级字段：

| JSON 字段 | Python 表示 | 领域含义 |
| --- | --- | --- |
| `case_id` | `ProbeCase.case_id` | 稳定的合成案例标识符。 |
| `query` | `ProbeCase.query` | Memory-Augmented Agent 的原始失败查询。 |
| `raw_events` | `tuple[RawEvent, ...]` | 提取前的历史或事件轨迹。 |
| `extracted_memory` | `tuple[MemoryItem, ...]` | 已存储的可恢复 Memory Item 集合。 |
| `gold_evidence` | `tuple[GoldEvidence, ...]` | 评分重放成功所需的证据。 |
| `gold_answer` | `ProbeCase.gold_answer` | 仅用于评分的预期答案，不注入 Post-Repair Context Replay。 |
| `baseline_outputs` | `tuple[BaselineOutput, ...]` | 来自 fixed-summary/vector-memory 基线的失败输出。 |
| `perturbation_label` | 经过验证的 V0 标签 | 已知的合成失败原因，用于评估。 |
| `scoring` | `ScoringSpec` | 声明答案/证据指标名称。 |

对于当前案例：

- 原始事件 `evt-001` 包含真实决策：Mira 选择了 Lisbon。
- 提取后的记忆 `mem-001` 保留了该黄金证据。
- 基线 `vector_memory` 检索到了 `mem-002`，一个关于 Porto 的干扰项。
- 基线答案是错误的：`Porto`。
- Oracle Retrieval 恢复到 `mem-001`，回答 `Lisbon`，产生 Recovery Gain `1.000`。
- CMD-Audit 预测为 `retrieval_error`。

## 函数级合约

### `cmd_audit/labels.py`

#### `LabelValidationError`

目的：

- 表示某个标签违反了 **V0 Core Label Set** 边界。

使用者：

- `validate_v0_label`。
- 断言坏 Memory Item 标签和延迟 pipeline 标签应被拒绝的测试。

#### `validate_v0_label(label: str) -> str`

目的：

- 仅接受六个 V0 pipeline 标签：
  - `write_error`
  - `compression_error`
  - `premature_extraction_error`
  - `retrieval_error`
  - `injection_error`
  - `reasoning_error`

行为：

- 当标签在 `V0_PIPELINE_LABELS` 中时，原样返回标签。
- 对坏的 **Memory Item** 标签抛出 `LabelValidationError`：
  - `item_wrong`
  - `item_stale`
  - `item_conflict`
  - `item_poisoned`
  - `item_compression_distorted`
- 对延迟的 pipeline 标签抛出 `LabelValidationError`：
  - `granularity_error`
  - `route_error`
  - `graph_error`
  - `safety_error`
- 对未知标签抛出 `LabelValidationError`。

为什么 issue 0001 需要它：

- 探针数据集合约不得意外地将 V1/V2 标签或 item 级标签纳入 V0 归因评分。

调用者：

- `ProbeCase.from_mapping` 验证每个案例的 `perturbation_label`。
- `assign_attribution` 验证最终预测标签。
- `V0LabelBoundaryTest.test_v0_accepts_only_pipeline_labels` 验证边界行为。

常量：

- `V0_PIPELINE_LABELS` 是允许的 V0 评分集合。
- `OUT_OF_SCOPE_ITEM_LABELS` 记录显式排除的 item 标签。
- `DEFERRED_PIPELINE_LABELS` 记录延迟的 V1/V2 标签。
- `REPLAY_TO_LABEL` 将重放名称映射到其诊断的 pipeline 标签。

### `cmd_audit/models.py`

#### `ProbeCaseError`

目的：

- 表示格式错误的探针 JSON 或合约违规。

使用者：

- `_required_str`
- `load_probe_cases`
- `ProbeCase.validate`

#### `RawEvent.from_mapping(cls, value: dict[str, Any]) -> RawEvent`

目的：

- 将一个原始事件 JSON 对象转换为不可变的 `RawEvent`。

必需的 JSON 字段：

- `event_id`
- `text`

领域含义：

- 表示未来 **Verbatim Event Oracle** 逻辑可用的提取前证据。

当前 issue 0001 用法：

- 作为 `ProbeCase.from_mapping` 的一部分加载。
- 确保案例区分原始事件与提取后的记忆。

#### `MemoryItem.from_mapping(cls, value: dict[str, Any]) -> MemoryItem`

目的：

- 将一个提取后的记忆 JSON 对象转换为不可变的 `MemoryItem`。

必需的 JSON 字段：

- `memory_id`
- `text`

可选的 JSON 字段：

- `source_event_ids`

领域含义：

- 表示摄入/提取后的一个可恢复 **Memory Item**。

当前 issue 0001 用法：

- 只有当 `GoldEvidence.source_memory_id` 指向这些 Memory Item 之一时，Oracle Retrieval 才能恢复黄金证据。

#### `GoldEvidence.from_mapping(cls, value: dict[str, Any]) -> GoldEvidence`

目的：

- 将一个黄金证据 JSON 对象转换为不可变的 `GoldEvidence`。

必需的 JSON 字段：

- `evidence_id`
- `text`

可选的 JSON 字段：

- `source_memory_id`
- `source_event_id`
- `required_phrases`

领域含义：

- 定义 Counterfactual Replay 必须恢复的证据。

当前 issue 0001 用法：

- 对于 `retrieval_error`，`source_memory_id` 必须指向 `extracted_memory` 中一个真实存在的 Memory Item。
- `required_phrases` 驱动 `evidence_recall_from_text`。

#### `BaselineOutput.from_mapping(cls, value: dict[str, Any]) -> BaselineOutput`

目的：

- 将一个基线输出 JSON 对象转换为不可变的 `BaselineOutput`。

必需的 JSON 字段：

- `baseline_name`
- `answer`

可选的 JSON 字段：

- `retrieved_memory_ids`
- `answer_score`
- `evidence_score`
- `injected_context`

领域含义：

- 记录 CMD-Audit 重放前的失败起点。

当前 issue 0001 用法：

- 第一个基线是 `vector_memory`。
- 它检索到干扰项 `mem-002`，回答 `Porto`，答案/证据得分初始为 `0.0`。

#### `ScoringSpec.from_mapping(cls, value: dict[str, Any] | None) -> ScoringSpec`

目的：

- 加载探针案例声明的评分指标名称。

默认值：

- `answer_metric = "casefold_exact_match"`
- `evidence_metric = "gold_evidence_recall"`

领域含义：

- 使探针合约明确声明答案得分和证据得分如何衡量。

当前 issue 0001 用法：

- 实际的确定性实现位于 `scoring.py`。
- 后续案例可以在扩展 fixture 的同时保持相同的声明指标名称。

#### `ProbeCase.from_mapping(cls, value: dict[str, Any]) -> ProbeCase`

目的：

- 从一个 JSON 对象构建完整的不可变 `ProbeCase`。

执行的工作：

- 使用 `_required_str` 读取必需的标量字段。
- 通过 `RawEvent.from_mapping` 转换 `raw_events`。
- 通过 `MemoryItem.from_mapping` 转换 `extracted_memory`。
- 通过 `GoldEvidence.from_mapping` 转换 `gold_evidence`。
- 通过 `BaselineOutput.from_mapping` 转换 `baseline_outputs`。
- 通过 `validate_v0_label` 验证 `perturbation_label`。
- 通过 `ScoringSpec.from_mapping` 加载 scoring。
- 调用 `ProbeCase.validate`。

领域含义：

- 这是 issue 0001 的核心合约边界。所有下游都假设加载后的 `ProbeCase` 已将原始事件、提取后的记忆、黄金证据、基线输出、黄金答案、扰动标签和评分字段彼此分离。

调用者：

- `load_probe_cases`。
- 构建故意破坏案例的测试。

#### `ProbeCase.primary_baseline(self) -> BaselineOutput`

目的：

- 返回第一个基线输出，用于初始示踪弹。

领域含义：

- 第一切片需要一个失败的基线起跑点，然后才能测量重放 Recovery Gain。

当前限制：

- V0 issue 0001 对 `run_case` 仅使用第一个基线。
- Issue 0002 应跨 fixed-summary 和 vector-memory 行为推广基线比较。

#### `ProbeCase.validate(self) -> None`

目的：

- 在对象构造后强制实施结构不变量。

检查项：

- `raw_events` 不为空。
- `extracted_memory` 不为空。
- `gold_evidence` 不为空。
- `baseline_outputs` 不为空。
- 每一个 `GoldEvidence.source_memory_id`（当存在时）必须指向一个实际存在的 `MemoryItem.memory_id`。

为什么 issue 0001 需要它：

- 第一条 `retrieval_error` 示踪弹依赖于黄金证据能够从提取后的记忆中恢复。
- 如果 `GoldEvidence.source_memory_id` 指向空，Oracle Retrieval 将是在测试一个损坏的 fixture 而非检索失败。

调用者：

- `ProbeCase.from_mapping`。

#### `load_probe_cases(path: str | Path) -> list[ProbeCase]`

目的：

- 探针数据集的公开文件加载器。

行为：

- 读取 UTF-8 JSON。
- 接受以下格式之一：
  - 一个 JSON 对象，或
  - 一个 JSON 对象列表。
- 将每个对象通过 `ProbeCase.from_mapping` 转换。
- 如果 JSON 顶层既不是对象也不是列表，抛出 `ProbeCaseError`。

领域含义：

- 这是独立 **CMD-Audit** harness 的第一个公开数据集加载 API。

调用者：

- `cli.main`。
- `tests/test_cmd_audit_tracer_bullet.py`。
- 外部用户可以从 `cmd_audit` 导入它。

#### `_required_str(value: dict[str, Any], key: str) -> str`

目的：

- 用于必需的非空字符串字段的内部辅助函数。

行为：

- 当键不存在时抛出 `ProbeCaseError`。
- 当值不是非空字符串时抛出 `ProbeCaseError`。
- 否则返回原始字符串。

领域含义：

- 使案例合约足够严格，确保 50-100 个合成案例在格式错误的标识符和文本上快速失败。

调用者：

- `RawEvent.from_mapping`
- `MemoryItem.from_mapping`
- `GoldEvidence.from_mapping`
- `BaselineOutput.from_mapping`
- `ProbeCase.from_mapping`

### `cmd_audit/scoring.py`

#### `answer_score(answer: str, gold_answer: str) -> float`

目的：

- 为合成案例评估答案正确性。

行为：

- 使用 `_normalize` 对两个字符串进行标准化。
- 标准化后完全匹配返回 `1.0`。
- 否则返回 `0.0`。

领域含义：

- 产生 **Recovery Gain** 的答案得分部分：

```text
Recovery Gain = replay answer score - baseline answer score
```

当前 issue 0001 用法：

- `Porto` 对比 `Lisbon` 得分为 `0.0`。
- `Lisbon` 对比 `Lisbon` 得分为 `1.0`。

#### `evidence_recall_from_memory_ids(case: ProbeCase, memory_ids: tuple[str, ...]) -> float`

目的：

- 评估一组检索到的 memory ID 是否包含所需的黄金证据 memory ID。

行为：

- 从 `case.gold_evidence` 构建所需的 `source_memory_id` 值集合。
- 如果没有所需的 memory ID，返回 `0.0`。
- 返回召回率为 `matched_required_ids / required_ids`。

当前状态：

- 作为一个小型基线/证据辅助函数实现。
- 当前未被 `run_case` 调用。
- 对 issue 0002 证据召回启发式和基线比较有用。

#### `evidence_recall_from_text(gold_evidence: tuple[GoldEvidence, ...], text: str) -> float`

目的：

- 评估重放证据文本是否包含每个黄金证据项的必需短语。

行为：

- 如果没有黄金证据，返回 `0.0`。
- 对证据文本块进行 casefold。
- 对每个 `GoldEvidence`，如果存在 `required_phrases` 则使用之，否则使用完整的 `GoldEvidence.text`。
- 只有当证据项的所有短语都在证据文本块中出现时，才将该证据项计为匹配。
- 返回 `matched / total_gold_evidence`。

当前 issue 0001 用法：

- Oracle Retrieval 从 `mem-001` 构建证据文本块。
- 必需短语 `Mira`、`Lisbon` 和 `Q3 offsite` 全部出现。
- 证据得分变为 `1.0`。

#### `_normalize(value: str) -> str`

目的：

- 用于答案匹配的内部辅助函数。

行为：

- Casefold。
- 去除首尾空白。
- 去除首尾非单词标点符号。
- 折叠重复空白。

调用者：

- `answer_score`。

### `cmd_audit/replays.py`

#### `ReplayResult`

目的：

- 一次 **Counterfactual Replay** 的不可变输出记录。

字段：

- `replay_name`
- `answer`
- `answer_score`
- `evidence_score`
- `evidence_block`
- `recovery_gain`

领域含义：

- 这是第一个重放结果形态，后续 issue 切片可以复用于 Oracle Write、Oracle Compression、Verbatim Event Oracle、Injection-Oracle 和 Evidence-Given Reasoning。

#### `run_oracle_retrieval(case: ProbeCase) -> ReplayResult`

目的：

- 运行第一个已实现的 Counterfactual Replay。

行为：

1. 通过 `case.primary_baseline` 读取失败的基线。
2. 通过 `_recover_extracted_gold_evidence` 从提取后的记忆中恢复黄金证据文本块。
3. 使用 `evidence_recall_from_text` 对证据恢复评分。
4. 仅当证据得分为 `1.0` 时返回 `case.gold_answer`。
5. 使用 `answer_score` 对重放答案评分。
6. 计算 `recovery_gain = replay answer score - baseline answer score`。
7. 返回 `ReplayResult(replay_name="oracle_retrieval", ...)`。

领域含义：

- 仅当正确记忆在提取/存储后幸存且能从 `extracted_memory` 中恢复时，才诊断为 `retrieval_error`。
- 它不检查原始事件。这个边界很重要，因为只能从原始事件恢复的情况属于未来的 `verbatim_event_oracle` 和 `premature_extraction_error`。

调用者：

- `harness.run_case`。

#### `_recover_extracted_gold_evidence(case: ProbeCase) -> str`

目的：

- 构建 Oracle Retrieval 证据文本块的内部辅助函数。

行为：

- 按 `memory_id` 索引 `case.extracted_memory`。
- 对每个 `GoldEvidence`，查找 `source_memory_id`。
- 找到时追加对应的 `MemoryItem.text`。
- 用换行符连接恢复的记忆文本。

领域含义：

- 编码"黄金证据存在于可恢复的提取后记忆中"这一条件。

调用者：

- `run_oracle_retrieval`。

### `cmd_audit/attribution.py`

#### `AttributionResult`

目的：

- Operation-Level Attribution 的不可变结果。

字段：

- `predicted_label`
- `top_replay`
- `recovery_gain`
- `top2_labels`
- `is_ambiguous`

领域含义：

- 存储从重放增量（而非事后解释）中分配的失败标签。

#### `assign_attribution(replay_results: tuple[ReplayResult, ...], *, positive_gain_threshold: float = 0.0, tie_margin: float = 0.05) -> AttributionResult`

目的：

- 将重放增量转换为归因标签。

行为：

1. 要求至少一个重放结果。
2. 按 `recovery_gain` 降序排序。
3. 如果最高增益不大于 `positive_gain_threshold`，则拒绝该集合。
4. 通过 `_label_for_replay` 将 `top.replay_name` 映射到标签。
5. 通过 `validate_v0_label` 验证映射后的标签。
6. 构建最多两个在 `tie_margin` 范围内的相近标签。
7. 如果存在两个相近标签，标记 `is_ambiguous = True`。
8. 返回 `AttributionResult`。

领域含义：

- 实现了基于规则的 V0 归因原则：

```text
operation label = label of replay with strongest positive Recovery Gain
```

当前 issue 0001 用法：

- 接收一个重放：`oracle_retrieval`。
- 映射到 `retrieval_error`。
- 产生 `top2_labels = ("retrieval_error",)` 和 `is_ambiguous = False`。

未来 issue 用法：

- 一旦有更多重放类型，周期 4 可以使用 `tie_margin` 实现 top-2 或多标签行为。

#### `_label_for_replay(replay_name: str) -> str`

目的：

- 内部重放名称到标签的查找。

行为：

- 在 `REPLAY_TO_LABEL` 中查找 `replay_name`。
- 对未知重放名称抛出 `ValueError`。

领域含义：

- 保持重放实现名称与归因标签分离，同时保留显式映射表。

调用者：

- `assign_attribution`。

### `cmd_audit/harness.py`

#### `AuditResult`

目的：

- 一次审计探针案例的不可变行级结果。

字段：

- `case_id`
- `perturbation_label`
- `baseline_name`
- `baseline_answer_score`
- `baseline_evidence_score`
- `replay`
- `attribution`

领域含义：

- 将已知的合成标签、基线状态、重放结果和 CMD 归因捆绑在一起，用于证据表格生成。

#### `AuditResult.attribution_correct(self) -> bool`

目的：

- 报告 CMD 是否恢复了合成扰动标签。

行为：

- 返回 `self.attribution.predicted_label == self.perturbation_label`。

领域含义：

- 为后续归因准确率和 Macro F1 指标提供第一个构建块。

当前 issue 0001 用法：

- `retrieval_error == retrieval_error`，因此第一行是正确的。

#### `run_case(case: ProbeCase) -> AuditResult`

目的：

- 第一条示踪弹的公开单案例 CMD-Audit 路径。

行为：

1. 运行 `run_oracle_retrieval(case)`。
2. 将重放结果传入 `assign_attribution`。
3. 读取 `case.primary_baseline`。
4. 返回 `AuditResult`。

领域含义：

- 这是最小化的独立 **CMD-Audit** harness 路径：

```text
ProbeCase -> Counterfactual Replay -> Recovery Gain -> Operation-Level Attribution
```

当前限制：

- 仅运行 Oracle Retrieval。
- 后续 issue 应在不必要地扩大 `ProbeCase` 合约的前提下添加重放选择或重放集合。

调用者：

- `run_cases`。
- 测试。
- 外部用户可以从 `cmd_audit` 导入它。

#### `run_cases(cases: list[ProbeCase]) -> list[AuditResult]`

目的：

- 对已加载案例列表应用 `run_case`。

行为：

- 每个输入案例返回一个 `AuditResult`。

领域含义：

- 保持 API 与 `TASK.md` 中目标 50-100 个合成探针案例的兼容性。

调用者：

- `cli.main`。

#### `write_attribution_table(results: list[AuditResult], output_path: str | Path) -> None`

目的：

- 为归因写入 CSV 证据工件。

CSV 列：

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
- `top2_labels`
- `is_ambiguous`
- `attribution_correct`

行为：

- 如有需要则创建父输出目录。
- 写入一行表头。
- 每个 `AuditResult` 写入一行。
- 数值得分格式化为三位小数。
- `top2_labels` 用 `|` 连接。
- 布尔值写为小写字符串。

领域含义：

- 在提出任何归因声明之前，产生第一个必需的证据工件。

调用者：

- `cli.main`。
- 测试。

### `cmd_audit/cli.py`

#### `main(argv: list[str] | None = None) -> int`

目的：

- 独立 CMD-Audit harness 的命令行入口点。

命令：

```bash
python3 -m cmd_audit run \
  --cases data/probe_cases/v0_retrieval_error_case.json \
  --out artifacts/attribution_table.csv
```

行为：

- 解析子命令 `run`。
- `--cases` 默认为第一个 retrieval-error fixture。
- `--out` 默认为 `artifacts/attribution_table.csv`。
- 通过 `load_probe_cases` 加载案例。
- 通过 `run_cases` 运行案例。
- 通过 `write_attribution_table` 写入 CSV。
- 打印写入了多少归因行。
- 成功时返回 `0`。

领域含义：

- 为 CMD-Audit 提供可复现的本地执行界面。
- 它不是一个生产级 memory-agent 集成，也不是一个 CMD-Skill Adapter。

### `cmd_audit/__main__.py`

#### 模块级 `raise SystemExit(main())`

目的：

- 允许通过 `python3 -m cmd_audit ...` 以 Python 模块方式执行。

行为：

- 从 `cmd_audit.cli` 导入 `main`。
- 将返回的整数转换为进程退出状态。

### `cmd_audit/__init__.py`

目的：

- 定义包的公开导入接口。

导出的对象：

- `AttributionResult`
- `AuditResult`
- `ProbeCase`
- `V0_PIPELINE_LABELS`
- `assign_attribution`
- `load_probe_cases`
- `run_case`
- `run_cases`
- `validate_v0_label`
- `write_attribution_table`

领域含义：

- 保持面向调用者的 CMD-Audit API 精简，同时实现模块保持可替换。

## 测试级合约

测试位于 `tests/test_cmd_audit_tracer_bullet.py`。

### `RetrievalFailureTracerBulletTest.test_probe_case_contract_loads_retrieval_failure_case`

验证：

- `load_probe_cases` 加载 JSON fixture。
- 扰动标签为 `retrieval_error`。
- 原始事件、提取后的记忆和黄金证据均存在。
- 黄金答案为 `Lisbon`。
- 主基线为 `vector_memory`。
- 主基线检索到干扰项 `mem-002`。

需求覆盖：

- Issue 0001 案例合约区分。
- TDD 周期 1 设置。

### `RetrievalFailureTracerBulletTest.test_oracle_retrieval_recovers_answer_and_attributes_retrieval_error`

验证：

- `run_case` 运行 `oracle_retrieval`。
- 重放答案为 `Lisbon`。
- 重放答案得分为 `1.0`。
- 重放证据得分为 `1.0`。
- 预测标签为 `retrieval_error`。
- `attribution_correct` 为 true。

需求覆盖：

- 第一条红绿示踪弹。
- 从 Recovery Gain 到 Operation-Level Attribution。

### `RetrievalFailureTracerBulletTest.test_attribution_table_contains_first_retrieval_row`

验证：

- `write_attribution_table` 写入预期的 CSV 表头。
- 第一行包含 `v0-retrieval-001,retrieval_error,retrieval_error`。

需求覆盖：

- `attribution_table.csv` 的第一个证据工件形态。

### `V0LabelBoundaryTest.test_v0_accepts_only_pipeline_labels`

验证：

- `retrieval_error` 被接受。
- `item_wrong` 被拒绝。
- `route_error` 被拒绝。

需求覆盖：

- 坏的 Memory Item 标签被排除在 V0 之外。
- 延迟的 pipeline 标签被排除在 V0 之外。

### `V0LabelBoundaryTest.test_probe_case_rejects_gold_evidence_missing_from_extracted_memory`

验证：

- 如果黄金证据指向一个缺失的提取后记忆项，则 `retrieval_error` fixture 无效。

需求覆盖：

- 防止在证据从提取后记忆中本来就不可恢复的情况下产生虚假的 `retrieval_error` 案例。
- 保护 `retrieval_error` 与未来 `premature_extraction_error` 之间的边界。

## Issue 0001 标签场景示例

以下示例满足 issue 0001 的合约级要求——每个 V0 最小标签都至少有一个场景。在当前代码切片中，只有 `retrieval_error` 是可执行的；其余五个是面向未来示踪弹的合约示例。

### `write_error`

场景：

- 原始事件包含所需证据。
- 提取后的记忆缺少任何代表该证据的 Memory Item。
- Oracle Write 注入一个正确的 Memory Item。
- 答案恢复。

预期归因：

- `write_error`

实现状态：

- 标签被 `V0_PIPELINE_LABELS` 允许。
- 重放映射存在：`REPLAY_TO_LABEL["oracle_write"] = "write_error"`。
- Oracle Write 重放尚未在 issue 0001 中实现。

### `compression_error`

场景：

- 原始事件包含完整的事实：实体、关系、时间和约束。
- 提取后的记忆包含一个压缩过的 Memory Item，丢失了关键字段。
- Oracle Compression 将其替换为完整的压缩记忆。
- 答案恢复。

预期归因：

- `compression_error`

实现状态：

- 标签被 `V0_PIPELINE_LABELS` 允许。
- 重放映射存在：`REPLAY_TO_LABEL["oracle_compression"] = "compression_error"`。
- Oracle Compression 重放尚未在 issue 0001 中实现。

### `premature_extraction_error`

场景：

- 原始事件包含所需证据。
- 提取后的记忆不包含该证据的可恢复表示，因为摄入时过早进行了抽象。
- 在提取后的记忆上 Oracle Retrieval 失败。
- Verbatim Event Oracle 从原始事件中恢复。

预期归因：

- `premature_extraction_error`

实现状态：

- 标签被 `V0_PIPELINE_LABELS` 允许。
- 重放映射存在：`REPLAY_TO_LABEL["verbatim_event_oracle"] = "premature_extraction_error"`。
- Verbatim Event Oracle 重放计划在 TDD 周期 2 中实现。

### `retrieval_error`

场景：

- 提取后的记忆包含黄金证据。
- 基线 vector memory 检索到错误的 Memory Item。
- Oracle Retrieval 直接恢复黄金 Memory Item。
- 答案恢复。

预期归因：

- `retrieval_error`

实现状态：

- 在 `data/probe_cases/v0_retrieval_error_case.json` 中完全可执行。
- 被 `run_oracle_retrieval` 覆盖。
- 被行为测试覆盖。

### `injection_error`

场景：

- 正确的 Memory Item 被检索到。
- 基线将其注入到混乱或格式错误的上下文块中。
- Injection-Oracle 提供规范的证据文本块。
- 答案恢复。

预期归因：

- `injection_error`

实现状态：

- 标签被 `V0_PIPELINE_LABELS` 允许。
- 重放映射存在：`REPLAY_TO_LABEL["injection_oracle"] = "injection_error"`。
- Injection-Oracle 重放尚未在 issue 0001 中实现。

### `reasoning_error`

场景：

- 正确的证据被检索到并注入。
- 基线答案仍然错误，因为最终推理误用了证据。
- Evidence-Given Reasoning 恢复答案。

预期归因：

- `reasoning_error`

实现状态：

- 标签被 `V0_PIPELINE_LABELS` 允许。
- 重放映射存在：`REPLAY_TO_LABEL["evidence_given_reasoning"] = "reasoning_error"`。
- Evidence-Given Reasoning 重放计划在 TDD 周期 3 中实现。

## 验收标准对照

| Issue 0001 AC | 当前实现 |
| --- | --- |
| 区分原始事件、提取后的记忆、黄金证据、基线输出、注入的失败标签。 | `ProbeCase` 字段和 JSON fixture 明确分离这些字段。 |
| 包含六个 V0 pipeline 标签。 | `V0_PIPELINE_LABELS` 包含全部六个标签。 |
| 排除坏的 Memory Item 标签。 | `OUT_OF_SCOPE_ITEM_LABELS` 和 `validate_v0_label` 拒绝它们。 |
| 排除延迟的标签。 | `DEFERRED_PIPELINE_LABELS` 和 `validate_v0_label` 拒绝它们。 |
| 每个最小标签至少有一个示例场景。 | 本文档为每个标签定义一个场景；当前仅 `retrieval_error` 可执行。 |
| 说明答案得分和证据得分的衡量方式。 | `ScoringSpec`、`answer_score` 和 `evidence_recall_from_text` 定义了当前评分方式。 |
| 体量足够小，适合 50-100 个合成案例。 | `load_probe_cases` 接受一个案例或一个列表；`run_cases` 对列表映射单案例路径。 |

## 当前执行

运行测试：

```bash
python3 -m unittest discover -s tests -v
```

运行第一个 CMD-Audit 案例：

```bash
python3 -m cmd_audit run \
  --cases data/probe_cases/v0_retrieval_error_case.json \
  --out artifacts/attribution_table.csv
```

预期的第一行 CSV：

```text
v0-retrieval-001,retrieval_error,retrieval_error,oracle_retrieval,vector_memory,0.000,0.000,1.000,1.000,1.000,retrieval_error,false,true
```

## 已保留的非目标

- 不实现生产级 memory agent。
- 不实现 CMD-Skill Adapter。
- 不添加 UI 或仪表板。
- 不添加学习型归因分类器。
- 在第一条红绿路径存在之前不添加完整重放引擎。
- 不输出坏的 Memory Item 标签作为 V0 归因结果。
- Post-Repair Context Replay 不注入黄金答案；该关卡尚未实现。

## 下一步技术动作

下一条示踪弹应添加一个 `premature_extraction_error` fixture 和 **Verbatim Event Oracle** 重放：

```text
raw events contain evidence
extracted memory lacks recoverable evidence
Oracle Retrieval fails
Verbatim Event Oracle recovers
predicted label = premature_extraction_error
```

该下一步应复用以 `ProbeCase`、`GoldEvidence.source_event_id`、`ReplayResult` 和 `assign_attribution`，而非过早扩大合约。
