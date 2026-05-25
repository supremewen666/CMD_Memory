# Issue 0002 实现细节：基线系统与 Judge Monitor

## 目的

Issue 0002 在现有 CMD-Audit 检索示踪子弹之上，添加了首个非 CMD 的对比层。

所实现的切片回答以下问题：

```text
对于每个已标注的 Memory Failure 探针案例，CMD-Audit 能否将 replay-delta 归因与更廉价的观察性对比方法以及防泄漏的 replay 触发器保持分离？
```

本文档从代码中的单个语句拉远视角，将 issue 映射到项目词汇、模块边界、调用方路径、公开结果结构，以及每个 issue-0002 函数。

## 需求来源

实现遵循以下本地文档。

| 来源 | Issue 0002 中应用的需求 |
| --- | --- |
| `TASK.md` | 运行 fixed-summary 和 vector-memory 基线；将 CMD 与启发式基线及 subagent judge 基线进行对比；在论文声明之前产出证据。 |
| `CLAUDE.md` | 以 `cmd_innovation_core/` 为真源；保持 **CMD-Audit** 与 **CMD-Skill Adapter** 分离；保持 **Subagent Judge Monitor** 防泄漏。 |
| `cmd_innovation_core/CONTEXT.md` | 仅将 **Subagent Judge Baseline** 用作对比器；仅将 **Subagent Judge Monitor** 用于触发 replay；最终归因属于 CMD replay delta。 |
| `cmd_innovation_core/prd/cmd_minimal_probe_prd.md` | 包含基线输出、证据召回启发式、subagent judge 基线、高召回 monitor、随机基线及对比指标。 |
| `cmd_innovation_core/issues/0002-establish-baselines-and-judge-monitor.md` | 实现 fixed-summary/vector 基线、证据召回对比器、subagent judge 解释、monitor 触发器、随机标签及指标。 |
| `cmd_innovation_core/tdd/cmd_tracer_bullets.md` | Cycle 7 将 judge 解释与 CMD 归因分离；Cycle 8 拒绝输出最终标签、ECS、记忆写入、gold answer 或完整失败 trace 的 monitor 载荷。 |

## 领域边界

Issue 0002 位于失败的基线输出与昂贵的 **Counterfactual Replay** 路径之间：

```text
ProbeCase
  -> 来自 issue 0001 的基线记忆输出
  -> issue 0002 对比层
      -> fixed-summary 基线记录
      -> vector-memory 基线记录
      -> evidence-recall 启发式对比器
      -> Subagent Judge Baseline 对比器
      -> random label 健全性基线
      -> Subagent Judge Monitor replay 触发器
  -> issue 0001 replay 路径保持不变
      -> Oracle Retrieval Counterfactual Replay
      -> Recovery Gain
      -> Operation-Level Attribution
```

关键分离：

- **CMD-Audit** 最终标签：来自对 replay 结果调用 `assign_attribution(...)`。
- **证据召回启发式**：廉价对比器，非 CMD 归因。
- **Subagent Judge Baseline**：事后解释性对比器，非 CMD 归因。
- **Subagent Judge Monitor**：高召回触发器，用于触发昂贵的 replay，非归因系统。
- **CMD-Skill Adapter**：仍延迟实现；issue 0002 不与运行时 memory agent 集成。

## 模块地图

| 模块 | Issue 0002 角色 |
| --- | --- |
| `cmd_audit/baselines.py` | 拥有记忆基线规范化、非 CMD 对比器输出、随机健全性基线及 monitor 防泄漏检查。 |
| `cmd_audit/metrics.py` | 拥有系统级诊断预测和对比指标。 |
| `cmd_audit/harness.py` | 将 issue 0002 接入公开的 CMD-Audit 结果，同时保留 replay-delta 归因。 |
| `cmd_audit/cli.py` | 通过 `python3 -m cmd_audit run` 暴露 issue 0002 制品。 |
| `cmd_audit/__init__.py` | 导出调用方和测试所需的精简公开接口。 |
| `cmd_audit/labels.py` | 提供对比器和指标使用的 V0 标签顺序及校验。 |
| `tests/test_cmd_audit_issue2_baselines.py` | issue 验收标准的行为级测试。 |

## 调用图

主 CLI 路径：

```text
cmd_audit.__main__
  -> cli.main
      -> models.load_probe_cases
      -> harness.run_cases
          -> harness.run_case
              -> baselines.run_baseline_suite
                  -> baselines.run_memory_baselines
                  -> baselines._select_comparison_baseline
                  -> baselines.run_evidence_recall_heuristic
                      -> baselines._observational_label
                          -> scoring.evidence_recall_from_memory_ids
                          -> scoring.evidence_recall_from_text
                  -> baselines.run_subagent_judge_baseline
                      -> baselines.run_evidence_recall_heuristic
                  -> baselines.run_random_label_baseline
                  -> baselines.run_subagent_judge_monitor
                      -> SubagentJudgeMonitorDecision.to_payload
                          -> baselines.validate_monitor_payload
                              -> baselines._reject_forbidden_monitor_fields
              -> replays.run_oracle_retrieval
              -> attribution.assign_attribution
      -> harness.write_attribution_table
      -> harness.write_comparison_metrics_table
          -> harness.diagnosis_predictions
          -> metrics.compute_diagnosis_metrics
              -> metrics._observed_labels
              -> metrics._top2_correct
              -> metrics._macro_f1
                  -> metrics._label_f1
```

行为测试路径：

```text
tests/test_cmd_audit_issue2_baselines.py
  -> load_probe_cases
  -> run_baseline_suite
  -> run_case
  -> diagnosis_predictions
  -> compute_diagnosis_metrics
  -> validate_monitor_payload
  -> write_comparison_metrics_table
```

## 数据流

输入 fixture：

```text
data/probe_cases/v0_retrieval_error_case.json
```

重要 fixture 事实：

- `vector_memory` 检索到 `mem-002`，一个干扰项。
- `fixed_summary` 给出通用摘要，不检索任何记忆条目。
- Gold evidence 存在于提取记忆 `mem-001` 中。
- Gold answer 为 `Lisbon`。
- 扰动标签为 `retrieval_error`。

Issue 0002 输出：

```text
BaselineSuiteResult
  case_id
  memory_baselines
  evidence_recall_heuristic
  subagent_judge
  random_label
  monitor
```

Harness 级输出：

```text
AuditResult
  baseline_suite
  replay
  attribution
```

制品输出：

```text
artifacts/attribution_table.csv
artifacts/comparison_metrics.csv
```

## `cmd_audit/baselines.py`

此模块拥有 issue 0002 的非 CMD 层。其 docstring 直接说明：CMD-Audit issue 0002 的基线、对比器和 monitor 接口。

### 常量：`REQUIRED_MEMORY_BASELINES`

定义：

```python
REQUIRED_MEMORY_BASELINES = ("fixed_summary", "vector_memory")
```

角色：

- 编码 issue 0002 所需的基线系统。
- 作为 `run_memory_baselines(...)` 中 `required_baselines` 参数的默认值。
- 确保每个探针案例都证明已指定 fixed-summary 基线和 vector-memory 基线。

失败行为：

- 如果 `case.baseline_outputs` 中缺少任一名称，`run_memory_baselines(...)` 抛出 `BaselineConfigurationError`。

领域含义：

- 这些是 **Memory-Augmented Agent** 的基线行为，而非 CMD 干预。
- 这些值来自为每个探针案例指定 fixed-summary 和 vector-memory 行为的 issue 需求。

### 常量：`FORBIDDEN_MONITOR_FIELDS`

定义：

```python
FORBIDDEN_MONITOR_FIELDS = frozenset({...})
```

当前拒绝的键：

- 类似标签的字段：`label`、`labels`、`final_label`、`predicted_label`、`diagnosis_label`、`cmd_label`、`attribution`、`attribution_label`、`replay_label`、`top2_labels`
- repair/ECS 字段：`ecs`、`error_cause_solution`、`repair_guidance`、`corrected_memory`
- 记忆写入字段：`memory_write`、`memory_writes`
- gold 数据字段：`gold_answer`、`gold_evidence`
- trace 或数据集字段：`raw_events`、`extracted_memory`、`baseline_outputs`、`full_trace`、`full_failed_trace`、`failed_trace`

角色：

- 定义 **Subagent Judge Monitor** 载荷的防泄漏边界。
- 由 `_reject_forbidden_monitor_fields(...)` 使用。
- 通过 `test_monitor_payload_can_trigger_replay_without_forbidden_outputs` 和 `test_monitor_rejects_final_labels_ecs_memory_writes_gold_answers_and_full_traces` 进行测试。

领域含义：

- Monitor 可以说"应运行 replay"。
- 但不能输出最终的 **Operation-Level Attribution**、**Error-Cause-Solution**、**User Memory** 写入、gold answer/evidence 或完整的失败 trace。

### 异常：`BaselineConfigurationError`

定义：

```python
class BaselineConfigurationError(ValueError):
    """Raised when a probe case lacks required baseline outputs."""
```

抛出位置：

- `run_memory_baselines(...)`

失败场景：

- `case.baseline_outputs` 中存在重复的基线名称。
- 缺少 `REQUIRED_MEMORY_BASELINES` 中的名称。

为何存在：

- 如果探针案例无法产生两条所需的基线记录，则 issue 0002 无效。
- 这是案例契约失败，而非 replay 失败。

### 异常：`LeakSafeMonitorError`

定义：

```python
class LeakSafeMonitorError(ValueError):
    """Raised when the monitor attempts to expose forbidden diagnosis payloads."""
```

抛出位置：

- `_reject_forbidden_monitor_fields(...)`

使用者：

- `validate_monitor_payload(...)`
- `SubagentJudgeMonitorDecision.to_payload(...)`

为何存在：

- 强制执行 `CLAUDE.md`、`TASK.md` 和 `CONTEXT.md` 中规定的边界：monitor 不能输出最终标签、ECS、记忆写入、gold answer 或完整失败 trace。

### 数据类：`MemoryBaselineRun`

字段：

```python
baseline_name: str
answer: str
retrieved_memory_ids: tuple[str, ...]
answer_score: float
evidence_score: float
injected_context: str
```

角色：

- `BaselineOutput` 的规范化运行时视图。
- 在不改变 issue 0001 `ProbeCase` 契约的前提下，使基线行为在 issue 0002 中可用。

领域映射：

- `baseline_name`：fixed-summary 或 vector-memory 基线系统。
- `answer`：基线 **Memory-Augmented Agent** 的失败输出。
- `retrieved_memory_ids`：基线记忆系统检索到的记忆条目。
- `answer_score`：与 `gold_answer` 的匹配得分，用于对比指标。
- `evidence_score`：与 `gold_evidence` 的匹配得分，由 monitor 和诊断使用。
- `injected_context`：在生成答案前提供给基线的上下文。

#### `MemoryBaselineRun.from_output(output)`

签名：

```python
@classmethod
def from_output(cls, output: BaselineOutput) -> "MemoryBaselineRun"
```

输入：

- `output`：存储在探针案例中的 issue 0001 `BaselineOutput`。

返回：

- 具有相同基线名称、answer、检索 ID、得分和注入上下文的 `MemoryBaselineRun`。

调用方：

- `run_memory_baselines(...)`

方法存在的原因：

- 使 issue 0002 的输出独立于 issue 0001 模型内部结构，同时保留相同的值。

#### `MemoryBaselineRun.failed`

签名：

```python
@property
def failed(self) -> bool
```

返回：

- 当 `answer_score < 1.0` 时返回 `True`。
- 当基线答案在当前确定性评分器下完全正确时返回 `False`。

调用方：

- 测试用它断言当前两个 fixture 基线均失败。

领域含义：

- 基线是 CMD replay 之前的失败起点。

### 数据类：`ComparatorResult`

字段：

```python
comparator_name: str
predicted_label: str
top2_labels: tuple[str, ...]
explanation: str
cost_per_diagnosis: float
uses_counterfactual_replay: bool = False
```

角色：

- 非 CMD 标签输出对比器的通用结果结构。
- 由证据召回启发式、subagent judge 基线和随机标签基线使用。

领域边界：

- `predicted_label` 是对比器预测，而非最终 CMD 归因。
- `uses_counterfactual_replay` 对所有 issue 0002 对比器显式设为 `False`。

#### `ComparatorResult.__post_init__()`

签名：

```python
def __post_init__(self) -> None
```

行为：

- 对 `predicted_label` 调用 `validate_v0_label(...)`。
- 对 `top2_labels` 中的每个标签调用 `validate_v0_label(...)`。

为何重要：

- Issue 0002 对比器必须保持在相同的 V0 六标签边界内：
  - `write_error`
  - `compression_error`
  - `premature_extraction_error`
  - `retrieval_error`
  - `injection_error`
  - `reasoning_error`
- 对比器不能将不合规的记忆条目标签或延后的流水线标签引入对比表。

### 数据类：`SubagentJudgeMonitorDecision`

字段：

```python
should_trigger_replay: bool
risk_score: float
reasons: tuple[str, ...]
trace_summary: str
cost_per_decision: float = 0.2
```

角色：

- 防泄漏的 monitor 决策。
- 仅表示是否应运行昂贵的 CMD replay，附带少量风险摘要。

允许的载荷内容：

- trigger 布尔值
- risk score
- 简短 reasons
- 聚合 trace summary
- monitor cost

禁止的内容：

- final label
- ECS
- memory writes
- gold answer/evidence
- 完整失败 trace

#### `SubagentJudgeMonitorDecision.to_payload()`

签名：

```python
def to_payload(self) -> dict[str, Any]
```

行为：

1. 构建包含以下键的字典：
   - `should_trigger_replay`
   - `risk_score`
   - `reasons`
   - `trace_summary`
   - `cost_per_decision`
2. 调用 `validate_monitor_payload(payload)`。
3. 返回校验通过的载荷。

调用方：

- `run_subagent_judge_monitor(...)` 在返回决策之前立即调用它。
- 测试直接调用它来验证允许的载荷结构。

为何在此方法内调用校验：

- monitor 决策的每条序列化路径都必须通过防泄漏门禁。

### 数据类：`BaselineSuiteResult`

字段：

```python
case_id: str
memory_baselines: tuple[MemoryBaselineRun, ...]
evidence_recall_heuristic: ComparatorResult
subagent_judge: ComparatorResult
random_label: ComparatorResult
monitor: SubagentJudgeMonitorDecision
```

角色：

- 聚合单个探针案例的所有 issue 0002 输出。
- 存储在 `AuditResult.baseline_suite` 上。

领域映射：

- `memory_baselines`：fixed-summary 和 vector-memory 记忆系统的观测行为。
- `evidence_recall_heuristic`：廉价的以检索为中心的对比器。
- `subagent_judge`：事后观察性解释对比器。
- `random_label`：用于归因指标的健全性基线。
- `monitor`：高召回 replay 触发器。

#### `BaselineSuiteResult.comparator_results`

签名：

```python
@property
def comparator_results(self) -> tuple[ComparatorResult, ...]
```

返回：

```python
(
    self.evidence_recall_heuristic,
    self.subagent_judge,
    self.random_label,
)
```

调用方：

- `harness.diagnosis_predictions(...)`

为何存在：

- 使指标生成对对比器系统保持通用，同时不将 monitor 输出混入最终归因指标。

### 函数：`run_baseline_suite(case)`

签名：

```python
def run_baseline_suite(case: ProbeCase) -> BaselineSuiteResult
```

输入：

- `case`：一个已验证的 `ProbeCase`。

返回：

- `BaselineSuiteResult`。

逐步行为：

1. 调用 `run_memory_baselines(case)` 以校验并规范化基线记忆系统输出。
2. 调用 `_select_comparison_baseline(case)` 选择对比器使用的基线 trace。
3. 调用 `run_evidence_recall_heuristic(case, comparison_baseline)`。
4. 调用 `run_subagent_judge_baseline(case, comparison_baseline)`。
5. 调用 `run_random_label_baseline(case)`。
6. 调用 `run_subagent_judge_monitor(case, comparison_baseline)`。
7. 将所有输出打包为 `BaselineSuiteResult`。

调用方：

- `harness.run_case(...)`
- `tests/test_cmd_audit_issue2_baselines.py`
- 通过 `cmd_audit.__init__` 公开导出

边界保证：

- 该函数不调用任何 replay 函数。
- 不分配 CMD 归因。
- 生成位于 `AuditResult.attribution` 旁边而非内部的对比数据。

### 函数：`run_memory_baselines(case, required_baselines=REQUIRED_MEMORY_BASELINES)`

签名：

```python
def run_memory_baselines(
    case: ProbeCase,
    required_baselines: tuple[str, ...] = REQUIRED_MEMORY_BASELINES,
) -> tuple[MemoryBaselineRun, ...]
```

输入：

- `case`：一个探针案例。
- `required_baselines`：必须存在于 `case.baseline_outputs` 中的名称。

返回：

- `MemoryBaselineRun` 的有序元组。

排序：

1. 按 `required_baselines` 中的顺序排列的必需基线名称：
   - `fixed_summary`
   - `vector_memory`
2. 来自案例的任何额外基线输出，保持其 fixture 顺序。

校验：

- 拒绝重复的 `baseline_name`。
- 拒绝缺少必需基线名称的情况。

调用方：

- `run_baseline_suite(...)`

领域含义：

- 探针案例必须指定两种基线记忆系统行为，CMD 对比才有意义。

### 函数：`run_evidence_recall_heuristic(case, baseline=None)`

签名：

```python
def run_evidence_recall_heuristic(
    case: ProbeCase,
    baseline: BaselineOutput | None = None,
) -> ComparatorResult
```

输入：

- `case`：一个探针案例。
- `baseline`：可选的基线 trace。如果省略，则使用 `_select_comparison_baseline(case)`。

返回：

- `ComparatorResult`，包含：
  - `comparator_name="evidence_recall"`
  - `predicted_label` 来自 `_observational_label(...)`
  - 单一元素的 `top2_labels`
  - 自然语言理由
  - `cost_per_diagnosis=0.05`
  - `uses_counterfactual_replay=False`

调用方：

- `run_baseline_suite(...)`
- `run_subagent_judge_baseline(...)`

边界保证：

- 不运行 counterfactual replay。
- 是观察性对比器，而非 CMD 归因。

当前 fixture 行为：

- 提取记忆中包含 gold 记忆条目。
- Vector-memory 基线检索到的是干扰项。
- 启发式方法预测对比器标签为 `retrieval_error`。

### 函数：`run_subagent_judge_baseline(case, baseline=None)`

签名：

```python
def run_subagent_judge_baseline(
    case: ProbeCase,
    baseline: BaselineOutput | None = None,
) -> ComparatorResult
```

输入：

- `case`：一个探针案例。
- `baseline`：可选的基线 trace。如果省略，则使用 `_select_comparison_baseline(case)`。

返回：

- `ComparatorResult`，包含：
  - `comparator_name="subagent_judge"`
  - `predicted_label` 从证据召回启发式复制
  - `top2_labels` 从证据召回启发式复制
  - 针对失败 trace 的事后解释字符串
  - `cost_per_diagnosis=1.0`
  - `uses_counterfactual_replay=False`

内部调用：

- 调用 `run_evidence_recall_heuristic(case, baseline)` 以复用廉价的观察性标签。

为何采用此结构：

- V0 不会从测试中调用真实的 LLM/subagent。
- 确定性占位符保留了研究契约：类似 judge 的解释可以被记录和比较，但不能成为 CMD 归因。

领域边界：

- 这实现的是 **Subagent Judge Baseline**，而非 **Subagent Judge Monitor**，也非 **CMD-Audit** 归因。

### 函数：`run_random_label_baseline(case)`

签名：

```python
def run_random_label_baseline(case: ProbeCase) -> ComparatorResult
```

输入：

- `case`：一个探针案例。

返回：

- `ComparatorResult`，包含：
  - `comparator_name="random_label"`
  - 确定性伪随机的 `predicted_label`
  - 确定性伪随机的双标签元组
  - `cost_per_diagnosis=0.01`
  - `uses_counterfactual_replay=False`

算法：

1. 使用 SHA-256 对 `case.case_id` 进行哈希。
2. 使用第一个字节从 `V0_PIPELINE_LABEL_ORDER` 中选择 top-1 标签。
3. 使用第二个字节选择一个不同的 top-2 标签。

为何是确定性的：

- 测试结果和制品保持可复现。
- 随机基线仍可作为健全性检查，而不会在 CI 中产生不确定行为。

领域含义：

- 它不是一个有意义的诊断系统。
- 它的存在是为了揭示 CMD 和更强的对比器是否优于随机水平的归因。

### 函数：`run_subagent_judge_monitor(case, baseline=None, *, trigger_threshold=0.5)`

签名：

```python
def run_subagent_judge_monitor(
    case: ProbeCase,
    baseline: BaselineOutput | None = None,
    *,
    trigger_threshold: float = 0.5,
) -> SubagentJudgeMonitorDecision
```

输入：

- `case`：一个探针案例。
- `baseline`：可选的基线 trace。如果省略，则使用 `_select_comparison_baseline(case)`。
- `trigger_threshold`：触发 replay 所需的最低 risk score。

风险评分：

- 如果 `baseline.answer_score < 1.0`，则加 `0.5`。
- 如果 `baseline.evidence_score < 1.0`，则加 `0.4`。
- 如果 `baseline.retrieved_memory_ids` 为空，则加 `0.1`。
- 将总风险封顶于 `1.0`。

返回：

- `SubagentJudgeMonitorDecision`。

内部校验：

- 构建决策。
- 调用 `decision.to_payload()`。
- `to_payload()` 调用 `validate_monitor_payload(...)`。

当前 fixture 行为：

- Vector-memory 基线 answer score 为 `0.0`。
- Vector-memory 基线 evidence score 为 `0.0`。
- 它确实检索了一个记忆条目，因此不适用无检索加分。
- Risk score 为 `0.9`。
- 在阈值 `0.5` 下，`should_trigger_replay=True`。

领域边界：

- 这是 **Subagent Judge Monitor** 的行为。
- 它可以触发昂贵的 replay。
- 不能提供最终标签或修复内容。

### 函数：`validate_monitor_payload(payload)`

签名：

```python
def validate_monitor_payload(payload: dict[str, Any]) -> dict[str, Any]
```

输入：

- 任何意图代表 monitor 载荷的字典。

返回：

- 如果有效，返回相同的载荷。

抛出：

- 当载荷中任意位置出现禁止键时抛出 `LeakSafeMonitorError`。

调用方：

- `SubagentJudgeMonitorDecision.to_payload(...)`
- 故意传入禁止键的测试。

为何返回载荷：

- 可在未来适配器代码中内联使用，不改变数据。

### 辅助函数：`_reject_forbidden_monitor_fields(value)`

签名：

```python
def _reject_forbidden_monitor_fields(value: Any) -> None
```

输入：

- 任意嵌套值。

行为：

- 如果 `value` 是 dict：
  - 检查每个键是否在 `FORBIDDEN_MONITOR_FIELDS` 中；
  - 递归检查每个嵌套值。
- 如果 `value` 是 list 或 tuple：
  - 递归检查每个元素。
- 其他值被忽略。

抛出：

- 当在任意嵌套层级发现任何禁止字段时抛出 `LeakSafeMonitorError`。

为何递归：

- Monitor 不得将 `gold_answer`、`final_label`、`ecs` 或完整 trace 内容隐藏在嵌套元数据中。

### 辅助函数：`_select_comparison_baseline(case)`

签名：

```python
def _select_comparison_baseline(case: ProbeCase) -> BaselineOutput
```

行为：

- 如果存在名称为 `vector_memory` 的基线输出，返回第一个匹配项。
- 否则返回 `case.primary_baseline`。

调用方：

- `run_baseline_suite(...)`
- `run_evidence_recall_heuristic(...)`
- `run_subagent_judge_baseline(...)`
- `run_subagent_judge_monitor(...)`

为何优先选择 vector-memory：

- Issue 0002 明确评估 vector-memory 检索。
- 证据召回启发式在检索基线上最有意义。

边界说明：

- 该选择仅用于对比器和 monitor 的观察。
- 不决定最终的 CMD 归因。

### 辅助函数：`_observational_label(case, baseline)`

签名：

```python
def _observational_label(case: ProbeCase, baseline: BaselineOutput) -> tuple[str, str]
```

输入：

- `case`：一个探针案例。
- `baseline`：选定的失败基线 trace。

返回：

- `(predicted_label, rationale)`

计算的得分：

- `retrieved_recall`：`baseline.retrieved_memory_ids` 是否覆盖 gold 记忆 ID。
- `extracted_recall`：所有提取记忆 ID 是否包含 gold 记忆 ID。
- `context_recall`：基线注入的上下文文本是否包含所需的 gold 短语。
- `raw_event_recall`：原始事件文本是否包含所需的 gold 短语。
- `has_gold_memory_pointer`：是否有任何 gold evidence 指向某个源记忆 ID。

规则顺序：

1. 如果检索到的 ID 包含 gold evidence 但注入上下文缺少证据，返回 `injection_error`。
2. 如果注入上下文召回了证据但 answer score 失败，返回 `reasoning_error`。
3. 如果提取记忆包含 gold evidence 但检索 ID 遗漏了它，返回 `retrieval_error`。
4. 如果原始事件包含证据但提取记忆不包含：
   - 当 gold evidence 有记忆指针时返回 `compression_error`；
   - 当没有可恢复的提取记忆指向它时返回 `premature_extraction_error`。
5. 否则返回 `write_error`。

为何顺序重要：

- 它使 `retrieval_error` 仅限于存在可恢复的提取记忆但基线检索遗漏了它的情形。
- 通过检查原始事件召回 vs 提取记忆召回来保护 **Premature Extraction Error** 边界。
- 它仍然是启发式对比器，而非基于干预的归因结果。

## `cmd_audit/metrics.py`

此模块拥有 issue 0002 的对比指标接口。

### 数据类：`DiagnosisPrediction`

字段：

```python
system_name: str
case_id: str
gold_label: str
predicted_label: str
top2_labels: tuple[str, ...]
cost_per_diagnosis: float
```

角色：

- 一个系统在一个案例上的一行诊断输出。
- 同时用于 CMD-Audit 和对比器系统。

当前产生的系统名称：

- `CMD-Audit`
- `evidence_recall`
- `subagent_judge`
- `random_label`

#### `DiagnosisPrediction.__post_init__()`

签名：

```python
def __post_init__(self) -> None
```

行为：

- 校验 `gold_label`。
- 校验 `predicted_label`。
- 校验 `top2_labels` 中的每个标签。

为何重要：

- 使对比指标与 V0 归因边界保持一致。
- 防止条目标签或延后标签进入对比结果。

### 数据类：`DiagnosisMetrics`

字段：

```python
system_name: str
attribution_accuracy: float
macro_f1: float
top2_accuracy: float
cost_per_diagnosis: float
```

角色：

- 一个系统的聚合指标行。
- 写入 `artifacts/comparison_metrics.csv`。

指标含义：

- `attribution_accuracy`：top-1 标签相对于已知扰动标签的正确率。
- `macro_f1`：在观测到的标签上的平均 F1，除非提供了固定的标签集。
- `top2_accuracy`：gold label 是否出现在系统的 top-2 元组中。
- `cost_per_diagnosis`：系统的平均成本单位。

### 函数：`compute_diagnosis_metrics(predictions, *, labels=None)`

签名：

```python
def compute_diagnosis_metrics(
    predictions: Iterable[DiagnosisPrediction],
    *,
    labels: tuple[str, ...] | None = None,
) -> dict[str, DiagnosisMetrics]
```

输入：

- `predictions`：来自 `harness.diagnosis_predictions(...)` 的行。
- `labels`：可选，用于 macro F1 的显式标签集。

返回：

- 以 `system_name` 为键的字典。

逐步行为：

1. 按 `system_name` 对预测进行分组。
2. 对每个系统：
   - 如果提供了 `labels` 则使用之，否则使用 `_observed_labels(rows)`；
   - 统计 top-1 正确的行数；
   - 使用 `_top2_correct(...)` 统计 top-2 正确的行数；
   - 累加成本；
   - 计算平均成本；
   - 使用 `_macro_f1(...)` 计算 macro F1。
3. 每个系统返回一个 `DiagnosisMetrics`。

调用方：

- `harness.write_comparison_metrics_table(...)`
- 测试

领域含义：

- 这是 CMD-vs-启发式-vs-subagent judge 对比的首个证据层。

### 辅助函数：`_observed_labels(rows)`

签名：

```python
def _observed_labels(rows: list[DiagnosisPrediction]) -> tuple[str, ...]
```

行为：

- 从系统行中观测到的 gold labels 和 predicted labels 构建排序集合。

为何使用观测标签：

- 当前 V0 fixture 集只有一个案例。
- 在数据集扩展之前，观测标签的 macro F1 避免在所有六个标签上报告零值。
- 未来更大的运行可以通过 `labels` 参数传入完整的 V0 标签集。

### 辅助函数：`_top2_correct(row)`

签名：

```python
def _top2_correct(row: DiagnosisPrediction) -> bool
```

行为：

- 当 `row.top2_labels` 存在时使用之。
- 当 top-2 为空时退回到 `(row.predicted_label,)`。
- 返回 `row.gold_label` 是否出现在该元组中。

为何存在回退：

- 某些系统在未来 issue 切片中可能只产出 top-1 标签。

### 辅助函数：`_macro_f1(rows, labels)`

签名：

```python
def _macro_f1(rows: list[DiagnosisPrediction], labels: tuple[str, ...]) -> float
```

行为：

- 如果没有标签存在，返回 `0.0`。
- 否则对每个标签计算 `_label_f1(rows, label)` 并取平均。

领域含义：

- Macro F1 是 issue 0002 所要求的，后续也支持 CMD 归因质量的声明门禁。

### 辅助函数：`_label_f1(rows, label)`

签名：

```python
def _label_f1(rows: list[DiagnosisPrediction], label: str) -> float
```

行为：

1. 统计 true positives：
   - `gold_label == label and predicted_label == label`
2. 统计 false positives：
   - `gold_label != label and predicted_label == label`
3. 统计 false negatives：
   - `gold_label == label and predicted_label != label`
4. 计算 precision 和 recall，含零分母保护。
5. 返回调和平均值；如果 precision 加 recall 为零则返回 `0.0`。

为何本地实现：

- 保持 V0 harness 无外部依赖。
- 与当前无任何依赖的 `pyproject.toml` 一致。

## `cmd_audit/harness.py` 集成

Issue 0002 扩展了公开的 harness，但不替换 issue 0001 的行为。

### 常量：`CMD_REPLAY_COST_UNITS`

定义：

```python
CMD_REPLAY_COST_UNITS = 5.0
```

角色：

- CMD replay 诊断的占位符单位成本。
- 仅在 `diagnosis_predictions(...)` 中用于对比指标。

解读：

- CMD-Audit 成本 = monitor 成本 + replay 成本。
- 当前值为确定性 V0 代理，而非实测的 token 或挂钟成本。

### 数据类字段：`AuditResult.baseline_suite`

新增字段：

```python
baseline_suite: BaselineSuiteResult
```

角色：

- 将 issue 0002 输出附加到每个 harness 结果上。
- 保持对比器和 monitor 输出可用，而不混入 `attribution`。

边界：

- `AuditResult.attribution` 仍然是 CMD replay 归因。
- `AuditResult.baseline_suite.subagent_judge.predicted_label` 仍然是对比器输出。

### 函数：`run_case(case)`

Issue 0002 行为：

1. 在 replay 之前调用 `run_baseline_suite(case)`。
2. 运行现有的 `run_oracle_retrieval(case)`。
3. 运行现有的 `assign_attribution((replay,))`。
4. 返回 `AuditResult(..., baseline_suite=baseline_suite)`。

为何采用此顺序：

- Monitor/对比器层在昂贵的 replay 之前观察失败的基线 trace。
- 最终标签仍然来自 replay-delta 归因。

### 函数：`diagnosis_predictions(result)`

签名：

```python
def diagnosis_predictions(result: AuditResult) -> tuple[DiagnosisPrediction, ...]
```

输入：

- 一个 `AuditResult`。

返回：

- 为 `CMD-Audit` 生成一个 `DiagnosisPrediction`。
- 为 `result.baseline_suite.comparator_results` 中的每个对比器生成一个 `DiagnosisPrediction`。

CMD-Audit 预测行：

- `system_name="CMD-Audit"`
- `gold_label=result.perturbation_label`
- `predicted_label=result.attribution.predicted_label`
- `top2_labels=result.attribution.top2_labels`
- `cost_per_diagnosis=result.baseline_suite.monitor.cost_per_decision + CMD_REPLAY_COST_UNITS`

对比器预测行：

- `system_name=comparator.comparator_name`
- `gold_label=result.perturbation_label`
- `predicted_label=comparator.predicted_label`
- `top2_labels=comparator.top2_labels`
- `cost_per_diagnosis=comparator.cost_per_diagnosis`

调用方：

- `write_comparison_metrics_table(...)`
- 测试

边界保证：

- 生成对比行但不修改归因。

### 函数：`write_comparison_metrics_table(results, output_path)`

签名：

```python
def write_comparison_metrics_table(
    results: list[AuditResult],
    output_path: str | Path,
) -> None
```

输入：

- `results`：来自 `run_cases(...)` 的输出。
- `output_path`：CSV 目标路径。

行为：

1. 跨所有结果展平 `diagnosis_predictions(result)`。
2. 调用 `compute_diagnosis_metrics(predictions)`。
3. 创建父目录。
4. 写入 CSV 列：
   - `system_name`
   - `attribution_accuracy`
   - `macro_f1`
   - `top2_accuracy`
   - `cost_per_diagnosis`
5. 按系统名称排序以确保确定性输出。

输出制品：

```text
artifacts/comparison_metrics.csv
```

## `cmd_audit/cli.py` 集成

### 函数：`main(argv=None)`

Issue 0002 新增内容：

- 添加 `--metrics-out`，默认值为 `artifacts/comparison_metrics.csv`。
- 在 `run_cases(cases)` 之后：
  - 调用 `write_attribution_table(results, args.out)`；
  - 调用 `write_comparison_metrics_table(results, args.metrics_out)`。
- 打印两个制品路径。

CLI 命令：

```bash
python3 -m cmd_audit run
```

当前默认输出：

```text
artifacts/attribution_table.csv
artifacts/comparison_metrics.csv
```

## `cmd_audit/__init__.py` 公开接口

Issue 0002 导出：

- `BaselineSuiteResult`
- `DiagnosisMetrics`
- `DiagnosisPrediction`
- `V0_PIPELINE_LABEL_ORDER`
- `compute_diagnosis_metrics`
- `diagnosis_predictions`
- `run_baseline_suite`
- `write_comparison_metrics_table`

为何导出它们：

- 测试和未来的 issue 切片可以使用稳定的公开接口。
- Harness 保持独立，不暴露 CMD-Skill Adapter。

## `cmd_audit/labels.py` 支持

Issue 0002 新增并使用：

```python
V0_PIPELINE_LABEL_ORDER = (
    "write_error",
    "compression_error",
    "premature_extraction_error",
    "retrieval_error",
    "injection_error",
    "reasoning_error",
)
```

角色：

- 为 `run_random_label_baseline(...)` 提供确定性排序。
- 将随机基线限制在 V0 核心标签集内。

现有的 `validate_v0_label(...)` 被以下复用：

- `ComparatorResult.__post_init__()`
- `DiagnosisPrediction.__post_init__()`

这确保对比器预测和指标行不能脱离 V0 归因范围。

## 测试覆盖

测试文件：

```text
tests/test_cmd_audit_issue2_baselines.py
```

### `BaselineAndComparatorTest.test_issue2_baseline_suite_keeps_comparators_separate_from_cmd`

验证：

- `run_baseline_suite(...)` 同时返回 `fixed_summary` 和 `vector_memory`。
- 当前两个 fixture 基线均失败。
- 证据召回对比器预测 `retrieval_error`。
- 证据召回不使用 counterfactual replay。
- Subagent judge 对比器名称为 `subagent_judge`。
- Subagent judge 解释包含 `post-hoc`。
- Subagent judge 不使用 counterfactual replay。
- 随机基线以 `random_label` 形式存在。

覆盖的验收标准：

- fixed-summary/vector 基线行为；
- 证据召回对比器；
- subagent judge 对比器；
- 随机标签基线。

### `BaselineAndComparatorTest.test_run_case_exposes_baseline_suite_but_cmd_label_still_comes_from_replay`

验证：

- `run_case(...)` 仍然通过 CMD 归因预测 `retrieval_error`。
- Top replay 仍为 `oracle_retrieval`。
- Subagent judge 对比器标签可用，但系统名称不是 `CMD-Audit`。

覆盖的验收标准：

- subagent judge 不能直接设置 CMD 归因。

### `SubagentJudgeMonitorBoundaryTest.test_monitor_payload_can_trigger_replay_without_forbidden_outputs`

验证：

- Monitor 载荷中 `should_trigger_replay=True`。
- 载荷键与 `FORBIDDEN_MONITOR_FIELDS` 无交集。
- 载荷字符串不包含 fixture 的 gold answer。
- 载荷字符串不包含最终标签 `retrieval_error`。

覆盖的验收标准：

- monitor 可以触发 replay；
- monitor 是防泄漏的。

### `SubagentJudgeMonitorBoundaryTest.test_monitor_rejects_final_labels_ecs_memory_writes_gold_answers_and_full_traces`

验证：

- 对于以下字段，`validate_monitor_payload(...)` 抛出 `LeakSafeMonitorError`：
  - `final_label`
  - `ecs`
  - `memory_writes`
  - `gold_answer`
  - `full_failed_trace`

覆盖的验收标准：

- monitor 不能输出最终标签、ECS、记忆写入、gold answer 或完整失败 trace。

### `ComparisonMetricsTest.test_comparison_metrics_include_accuracy_macro_f1_top2_and_cost`

验证：

- 指标包含：
  - `CMD-Audit`
  - `evidence_recall`
  - `subagent_judge`
  - `random_label`
- CMD-Audit 归因准确率对当前 fixture 为 `1.0`。
- CMD-Audit top-2 准确率为 `1.0`。
- CMD-Audit macro F1 存在。
- CMD-Audit 成本大于零。

覆盖的验收标准：

- 对比指标包含归因准确率、macro F1、top-2 准确率及每次诊断成本。

### `ComparisonMetricsTest.test_comparison_metrics_table_can_be_written`

验证：

- `write_comparison_metrics_table(...)` 写入一个 CSV。
- CSV 表头包含所需的指标列。
- CSV 包含 `CMD-Audit`。

覆盖的验收标准：

- 指标已就绪，可作为制品输出。

## 当前制品语义

在当前单案例 fixture 上的 `artifacts/comparison_metrics.csv`：

```text
system_name,attribution_accuracy,macro_f1,top2_accuracy,cost_per_diagnosis
CMD-Audit,1.000,1.000,1.000,5.200
evidence_recall,1.000,1.000,1.000,0.050
random_label,0.000,0.000,1.000,0.010
subagent_judge,1.000,1.000,1.000,1.000
```

解读：

- 该制品证明了对比流水线的存在。
- 尚不能支持"CMD 优于启发式或 subagent judge"的论文声明，因为数据集仅有一个 retrieval-error 案例且启发式/subagent 对比器与之匹配。
- 确实满足了 issue 0002 的证据门禁要求：产出 CMD-vs-对比器的指标行。

## 验收标准可追溯性

| Issue 0002 验收标准 | 代码层 | 测试层 |
| --- | --- | --- |
| 为每个探针案例指定 fixed-summary 和 vector-memory 基线行为。 | `REQUIRED_MEMORY_BASELINES`、`run_memory_baselines(...)`、`MemoryBaselineRun` | `test_issue2_baseline_suite_keeps_comparators_separate_from_cmd` |
| 证据召回启发式输出被指定为对比器，而非 CMD 归因。 | `run_evidence_recall_heuristic(...)`、`ComparatorResult.uses_counterfactual_replay=False` | `test_issue2_baseline_suite_keeps_comparators_separate_from_cmd` |
| Subagent judge 输出被指定为针对 trace 的事后解释。 | `run_subagent_judge_baseline(...)` | `test_issue2_baseline_suite_keeps_comparators_separate_from_cmd` |
| Subagent judge monitor 行为是高召回 replay 触发，而非最终归因。 | `run_subagent_judge_monitor(...)`、`SubagentJudgeMonitorDecision` | `test_monitor_payload_can_trigger_replay_without_forbidden_outputs` |
| Monitor 是防泄漏的，不能输出最终标签、ECS、记忆写入、gold answer 或完整失败 trace。 | `FORBIDDEN_MONITOR_FIELDS`、`validate_monitor_payload(...)`、`_reject_forbidden_monitor_fields(...)` | `test_monitor_rejects_final_labels_ecs_memory_writes_gold_answers_and_full_traces` |
| 随机标签基线被指定用于归因健全性检查。 | `run_random_label_baseline(...)` | `test_issue2_baseline_suite_keeps_comparators_separate_from_cmd` |
| 对比指标包含归因准确率、macro F1、top-2 准确率和每次诊断成本。 | `DiagnosisMetrics`、`compute_diagnosis_metrics(...)`、`write_comparison_metrics_table(...)` | `test_comparison_metrics_include_accuracy_macro_f1_top2_and_cost`、`test_comparison_metrics_table_can_be_written` |

## 验证

命令：

```bash
python3 -m unittest discover -s tests -v
.venv/bin/python -m pytest -q
python3 -m compileall cmd_audit tests
python3 -m cmd_audit run
```

预期状态：

- 所有 issue 0001 和 issue 0002 的测试通过。
- `artifacts/attribution_table.csv` 被重新生成。
- `artifacts/comparison_metrics.csv` 被重新生成。

## Issue 0003 前的已知限制

- 仅 Oracle Retrieval replay 可执行。
- Subagent judge 是确定性且本地的；它是对比器结构，而非真实的 LLM 调用。
- 证据召回和 subagent judge 预期与当前单个 retrieval-error fixture 匹配。
- Macro F1 当前使用观测标签，除非传入完整的标签集。
- 尚无混淆矩阵。
- 尚无 Post-Repair Context Replay。
- 尚无 Error-Cause-Solution 记录。

这些限制是有意为之。Issue 0002 在 issue 0003 扩展 counterfactual 归因表之前完成了对比器和 monitor 边界。
