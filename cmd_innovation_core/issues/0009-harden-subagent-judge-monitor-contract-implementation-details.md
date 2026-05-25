# Issue 0009 实现细节：加固 Subagent Judge Monitor 契约

## 目的

本文档是 issue 0009（`加固 Subagent Judge Monitor 契约`）的全局实现地图。

Issue 0009 从 issue 0002 获取防泄漏的 Subagent Judge Monitor，并在实现层面加固其契约：

```text
SubagentJudgeMonitorDecision 构造
  -> anomaly_reason 枚举验证
  -> evidence_pointers 不透明ID验证
  -> 禁止字段黑名单检查
  -> 载荷序列化闸门
  -> MonitorAnomalyReasonError / LeakSafeMonitorError 拒绝
```

已实现的切片将 Monitor 的 `anomaly_reason` 锁定为四值枚举，将 evidence pointers 限制为仅不透明 ID，并拒绝任何包含自由形式自然语言、最终标签、ECS、gold answer、内存写入或完整失败轨迹的输出。所有验证在构造时（`__post_init__`）和序列化时（`to_payload`）触发。

## 源头需求

实现遵循以下本地文档。


| 来源                                                                                      | 在 Issue 0009 中应用的需求                                                                                                                                                                                             |
| --------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `TASK.md`                                                                               | Subagent Judge Monitor 的 `anomaly_reason` 锁定为预定义枚举；禁止自由形式自然语言；evidence pointers 仅为不透明 ID。                                                                                                                       |
| `CLAUDE.md`                                                                             | Subagent Judge Monitor 防泄漏：可触发重放，但不得输出最终标签、ECS、内存写入、gold answer 或完整失败轨迹；`anomaly_reason` 锁定为枚举；evidence pointers 仅为不透明 ID。                                                                                      |
| `cmd_innovation_core/CONTEXT.md`                                                        | **Subagent Judge Monitor** 的 `anomaly_reason` 强制为预定义枚举（`answer_vs_evidence_mismatch`、`retrieved_context_incomplete`、`evidence_recall_low`、`confidence_anomaly`）；禁止自由形式自然语言；evidence pointers 仅为不透明 ID，绝不包含内容文本。 |
| `cmd_innovation_core/prd/cmd_minimal_probe_prd.md`                                      | AC6：Monitor 的 `anomaly_reason` 锁定为预定义枚举；禁止自由形式自然语言；evidence pointers 仅为不透明 ID。用户故事 6/27/28。                                                                                                                     |
| `cmd_innovation_core/issues/0009-harden-subagent-judge-monitor-contract.md`             | 将 `anomaly_reason` 锁定为枚举；将 evidence pointers 限制为不透明 ID；拒绝自由形式文本；在契约边界进行测试。                                                                                                                                      |
| `cmd_innovation_core/tdd/cmd_tracer_bullets.md`                                         | Cycle 11：拒绝自由形式 `anomaly_reason`；Cycle 8：拒绝禁止的载荷字段。                                                                                                                                                             |
| `cmd_innovation_core/issues/0002-baselines-and-judge-monitor-implementation-details.md` | 提供加固前的 Monitor 基础（含自由形式 `reasons` 的 `SubagentJudgeMonitorDecision`、`validate_monitor_payload`、`FORBIDDEN_MONITOR_FIELDS`）。                                                                                      |


## 领域边界

Issue 0009 加固 issue 0002 的 Monitor 契约，不改变 Monitor 的角色或引入新的 Monitor 能力。

```text
run_subagent_judge_monitor(case, baseline)
  -> risk_score 计算（与 issue 0002 一致，不变）
  -> anomaly_reason 从枚举中选择（新增）
  -> evidence_pointers 从 baseline.retrieved_memory_ids 获取（新增）
  -> SubagentJudgeMonitorDecision 构造
      -> __post_init__:
          -> validate_monitor_anomaly_reason(anomaly_reason)  （新增）
          -> validate_evidence_pointers(evidence_pointers)     （新增）
      -> to_payload:
          -> validate_monitor_payload(payload)
              -> _reject_forbidden_monitor_fields              （与 0002 一致，不变）
```

本 Issue 负责：

- 定义四值 `MONITOR_ANOMALY_REASON_VALUES` 枚举；
- 添加 `MonitorAnomalyReasonError` 用于枚举验证失败；
- 添加 `validate_monitor_anomaly_reason()` 作为枚举闸门；
- 添加 `_is_opaque_id()` 和 `validate_evidence_pointers()` 用于不透明 ID 强制校验；
- 更新 `SubagentJudgeMonitorDecision` 字段：`reasons` → `anomaly_reason` + `evidence_pointers`；
- 更新 `run_subagent_judge_monitor()` 以生成枚举值和不透明指针；
- 更新 `SubagentJudgeMonitorDecision.to_payload()` 以序列化新字段；
- 针对枚举拒绝、不透明 ID 拒绝和端到端契约的行为级测试。

本 Issue 不负责（属于其他 issue）：

- 改变 Monitor 的角色（仍是重放触发器，非诊断源）；
- 改变 `FORBIDDEN_MONITOR_FIELDS` 或 `_reject_forbidden_monitor_fields`（由 issue 0002 负责）；
- 添加新的 Monitor 决策逻辑（risk_score 计算不变）；
- 改变 `validate_monitor_payload` 行为；
- CMD-Audit 归因或重放逻辑；
- ECS、Post-Repair Context Replay 或 Failure Memory。

## 模块地图


| 模块                                                | Issue 0009 角色                                                                                                                                                                                  |
| ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cmd_audit/labels.py`                             | 负责 `MONITOR_ANOMALY_REASON_VALUES`、`VALID_MONITOR_ANOMALY_REASONS`、`MonitorAnomalyReasonError` 和 `validate_monitor_anomaly_reason()`。                                                          |
| `cmd_audit/baselines.py`                          | 负责 `_is_opaque_id()`、`validate_evidence_pointers()`、更新后的 `SubagentJudgeMonitorDecision` 和更新后的 `run_subagent_judge_monitor()`。                                                                  |
| `cmd_audit/__init__.py`                           | 导出新的公共接口：`MONITOR_ANOMALY_REASON_VALUES`、`MonitorAnomalyReasonError`、`SubagentJudgeMonitorDecision`、`validate_evidence_pointers`、`validate_monitor_anomaly_reason`、`validate_monitor_payload`。 |
| `tests/test_cmd_audit_issue9_monitor_contract.py` | 加固后契约的行为级测试：枚举验证、不透明 ID 验证、禁止字段黑名单和端到端载荷形态。                                                                                                                                                    |


## 调用关系图

主 CLI 路径（从 issue 0002 更新）：

```text
cmd_audit.__main__
  -> cli.main
      -> models.load_probe_cases
      -> harness.run_cases
          -> harness.run_case
              -> baselines.run_baseline_suite
                  -> baselines.run_subagent_judge_monitor
                      -> risk_score 计算（不变）
                      -> anomaly_reason 选择（新增：基于优先级的枚举选择）
                      -> evidence_pointers = tuple(baseline.retrieved_memory_ids)（新增）
                      -> SubagentJudgeMonitorDecision(...)  （新增：anomaly_reason + evidence_pointers）
                          -> __post_init__:
                              -> labels.validate_monitor_anomaly_reason（新增）
                              -> baselines.validate_evidence_pointers（新增）
                                  -> baselines._is_opaque_id（新增）
                      -> SubagentJudgeMonitorDecision.to_payload（更新：anomaly_reason + evidence_pointers）
                          -> baselines.validate_monitor_payload
                              -> baselines._reject_forbidden_monitor_fields（不变）
              -> replays.run_v0_replay_portfolio
              -> attribution.assign_attribution
      -> harness.write_attribution_table
      -> harness.write_comparison_metrics_table
      -> harness.write_confusion_matrix_table
```

行为测试路径：

```text
tests/test_cmd_audit_issue9_monitor_contract.py
  -> validate_monitor_anomaly_reason
  -> validate_evidence_pointers
  -> SubagentJudgeMonitorDecision(...)
  -> validate_monitor_payload
  -> load_probe_cases
  -> run_baseline_suite
```

## 数据流

输入（与 issue 0002 共享）：

```text
data/probe_cases/v0_issue3_cases.json
```

每个案例的 Monitor 决策输出形态（已更新）：

```text
SubagentJudgeMonitorDecision
  should_trigger_replay: bool
  risk_score: float
  anomaly_reason: str          # 枚举值（原为：reasons: tuple[str, ...]）
  evidence_pointers: tuple[str, ...]  # 不透明 ID（新增）
  trace_summary: str
  cost_per_decision: float
```

Harness 层输出（结构不变，Monitor 字段已更新）：

```text
BaselineSuiteResult
  monitor: SubagentJudgeMonitorDecision
```

载荷形态（序列化后）：

```json
{
  "should_trigger_replay": true,
  "risk_score": 0.9,
  "anomaly_reason": "evidence_recall_low",
  "evidence_pointers": ["mem_001"],
  "trace_summary": "vector_memory: answer_score=0.000; ...",
  "cost_per_decision": 0.2
}
```

## 函数级契约

### `cmd_audit/labels.py`

Issue 0009 向本模块添加三个新常量、一个新异常和一个新验证函数。所有来自 issue 0001/0002 的现有常量和函数保持不变。

#### 常量：`MONITOR_ANOMALY_REASON_VALUES`

定义：

```python
MONITOR_ANOMALY_REASON_VALUES = (
    "answer_vs_evidence_mismatch",
    "retrieved_context_incomplete",
    "evidence_recall_low",
    "confidence_anomaly",
)
```

目的：

- 定义 Subagent Judge Monitor 的 `anomaly_reason` 有效值全集。
- 元组顺序为文档和迭代提供稳定顺序，但不暗示优先级（优先级在 `run_subagent_judge_monitor` 中定义）。

各值的领域含义：


| 枚举值                            | 含义                                         |
| ------------------------------ | ------------------------------------------ |
| `answer_vs_evidence_mismatch`  | 基线上下文具有充分证据召回但答案错误——暗示推理失败。                |
| `retrieved_context_incomplete` | 基线未检索到任何 memory item——上下文为空。               |
| `evidence_recall_low`          | 基线检索到了 memory item 但证据召回低于阈值——检索可能遗漏了相关条目。 |
| `confidence_anomaly`           | Risk score 异常但无单一信号主导——边界情况的兜底分类。          |


被以下调用方使用：

- `validate_monitor_anomaly_reason(...)`
- `SubagentJudgeMonitorDecision.__post_init__()`
- 遍历所有合法值的测试。

#### 常量：`VALID_MONITOR_ANOMALY_REASONS`

定义：

```python
VALID_MONITOR_ANOMALY_REASONS = frozenset(MONITOR_ANOMALY_REASON_VALUES)
```

目的：

- 为 `validate_monitor_anomaly_reason(...)` 提供 O(1) 成员检查。
- 以 frozenset 形式存在，防止意外修改。

#### 异常：`MonitorAnomalyReasonError`

定义：

```python
class MonitorAnomalyReasonError(ValueError):
    """当 Monitor 的 anomaly_reason 不是有效枚举值时抛出。"""
```

目的：

- 表示 Monitor 的 `anomaly_reason` 值违反了枚举契约。
- 与 `LabelValidationError`（标签范围）和 `LeakSafeMonitorError`（禁止字段）区分开来。

由以下抛出：

- `validate_monitor_anomaly_reason(...)`

由以下捕获：

- `SubagentJudgeMonitorDecision.__post_init__()` —— 在构造无效时向调用方传播。

#### 函数：`validate_monitor_anomaly_reason(reason: str) -> str`

签名：

```python
def validate_monitor_anomaly_reason(reason: str) -> str
```

目的：

- 验证 Monitor 的 `anomaly_reason` 字符串是否为四个允许的枚举值之一。
- 验证通过时原样返回 reason，使其可作为直通式验证器使用。

行为：

1. 检查 `reason in VALID_MONITOR_ANOMALY_REASONS`（frozenset 查找）。
2. 有效则返回 `reason`。
3. 无效则抛出 `MonitorAnomalyReasonError`，消息包含无效值和有效值列表。

验证规则：


| 输入                               | 结果                                  |
| -------------------------------- | ----------------------------------- |
| `"evidence_recall_low"`          | 返回 `"evidence_recall_low"`          |
| `"answer_vs_evidence_mismatch"`  | 返回 `"answer_vs_evidence_mismatch"`  |
| `"retrieved_context_incomplete"` | 返回 `"retrieved_context_incomplete"` |
| `"confidence_anomaly"`           | 返回 `"confidence_anomaly"`           |
| `"the answer looks wrong"`       | 抛出 `MonitorAnomalyReasonError`      |
| `"evidence_recall_low "`（尾部空格）   | 抛出 `MonitorAnomalyReasonError`      |
| `"Confidence_Anomaly"`（大小写错误）    | 抛出 `MonitorAnomalyReasonError`      |
| `""`（空字符串）                       | 抛出 `MonitorAnomalyReasonError`      |


调用方：

- `SubagentJudgeMonitorDecision.__post_init__()`
- `MonitorAnomalyReasonEnumTest` 中的直接测试

边界：

- 此函数强制执行 grill-session 规则：Monitor 不得输出自由形式自然语言。任何不与四个枚举值完全匹配的字符串均被拒绝。

### `cmd_audit/baselines.py`

Issue 0009 通过添加两个新辅助函数、更新一个 dataclass 和修改一个运行器函数来修改本模块。所有其他函数（`run_baseline_suite`、`run_memory_baselines`、`run_evidence_recall_heuristic`、`run_subagent_judge_baseline`、`run_random_label_baseline`、`validate_monitor_payload`、`_reject_forbidden_monitor_fields`、`_select_comparison_baseline`、`_observational_label`）自 issue 0002 以来保持不变。

#### 函数：`_is_opaque_id(value: str) -> bool`

签名：

```python
def _is_opaque_id(value: str) -> bool
```

目的：

- 私有辅助函数，判断一个字符串是否可作为不透明 ID。
- 不透明 ID 是短小、无空白字符的令牌，不包含携带内容的分隔符。

行为：

```python
return bool(value) and " " not in value and ":" not in value and "\n" not in value and len(value) <= 128
```

规则：


| 检查项                 | 理由                                 |
| ------------------- | ---------------------------------- |
| `bool(value)`       | 拒绝空字符串。                            |
| `" " not in value`  | 拒绝包含空格分隔的内容文本。                     |
| `":" not in value`  | 拒绝 `mem_003:Berlin` 这类在冒号后嵌入内容的模式。 |
| `"\n" not in value` | 拒绝多行内容转储。                          |
| `len(value) <= 128` | 上界：真实的 memory/event ID 是短令牌。       |


调用方：

- `validate_evidence_pointers(...)`

为何是私有的：

- 公共契约是 `validate_evidence_pointers(...)`。`_is_opaque_id` 检查是"不透明"构成条件的实现细节。

#### 函数：`validate_evidence_pointers(pointers: tuple[str, ...]) -> tuple[str, ...]`

签名：

```python
def validate_evidence_pointers(pointers: tuple[str, ...]) -> tuple[str, ...]
```

目的：

- 验证 evidence pointers 元组中的每个指针都是不透明 ID。
- 验证通过时原样返回元组，可作为直通式验证器使用。

行为：

1. 遍历 `pointers` 中的每个指针。
2. 对每个指针调用 `_is_opaque_id(ptr)`。
3. 如果任何指针不是不透明 ID：抛出 `LeakSafeMonitorError`，附带违规值。
4. 全部通过则原样返回 `pointers`。

验证规则：


| 输入                                        | 结果                        |
| ----------------------------------------- | ------------------------- |
| `("mem_001", "mem_002", "evt_301")`       | 返回输入                      |
| `()`                                      | 返回 `()`                   |
| `("mem_003: user lives in Berlin",)`      | 抛出 `LeakSafeMonitorError` |
| `("memory item #4 contains stale data",)` | 抛出 `LeakSafeMonitorError` |
| `("mem_001\nevidence leaked",)`           | 抛出 `LeakSafeMonitorError` |
| `("mem_003:Berlin",)`                     | 抛出 `LeakSafeMonitorError` |


调用方：

- `SubagentJudgeMonitorDecision.__post_init__()`
- `MonitorEvidencePointerTest` 中的直接测试

领域含义：

- Monitor 可以通过 ID 指出哪些 memory item 触发了异常，但不能在指针中嵌入内容文本。这强制执行了 grill-session 规则：evidence pointers 仅为不透明 ID，绝不包含内容文本。

#### Dataclass：`SubagentJudgeMonitorDecision`（已更新）

Issue 0009 之前的字段：

```python
should_trigger_replay: bool
risk_score: float
reasons: tuple[str, ...]
trace_summary: str
cost_per_decision: float = 0.2
```

Issue 0009 之后的字段：

```python
should_trigger_replay: bool
risk_score: float
anomaly_reason: str
evidence_pointers: tuple[str, ...]
trace_summary: str
cost_per_decision: float = 0.2
```

字段级变更：


| 旧字段                        | 新字段                                  | 变更                         |
| -------------------------- | ------------------------------------ | -------------------------- |
| `reasons: tuple[str, ...]` | `anomaly_reason: str`                | 自由形式元组 → 单枚举锁定字符串          |
| *(无)*                      | `evidence_pointers: tuple[str, ...]` | 新增字段，用于不透明 memory/event ID |


领域含义：

- `anomaly_reason`：来自 `MONITOR_ANOMALY_REASON_VALUES` 的单一枚举值，描述主要异常信号。取代自由形式的 `reasons` 元组。
- `evidence_pointers`：与异常相关的 memory item 的不透明 ID（通常为 `baseline.retrieved_memory_ids`）。不得包含内容文本。

#### `SubagentJudgeMonitorDecision.__post_init__()`（已更新）

签名：

```python
def __post_init__(self) -> None
```

行为：

1. 调用 `validate_monitor_anomaly_reason(self.anomaly_reason)`。
2. 调用 `validate_evidence_pointers(self.evidence_pointers)`。

Issue 0009 之前：不存在 `__post_init__`，验证仅在 `to_payload()` 中进行。

为何将验证移至构造时：

- 在构造时（而非序列化时）捕获无效的枚举值和携带内容的指针，使错误更靠近源头。一个 `anomaly_reason` 无效的 `SubagentJudgeMonitorDecision` 被立即拒绝，调用方无法检查它。

#### `SubagentJudgeMonitorDecision.to_payload()`（已更新）

签名：

```python
def to_payload(self) -> dict[str, Any]
```

行为（流程不变，载荷形态已更新）：

1. 构建包含以下键的 dict：
  - `should_trigger_replay`
  - `risk_score`
  - `anomaly_reason`（原为：`reasons`）
  - `evidence_pointers`（新增）
  - `trace_summary`
  - `cost_per_decision`
2. 调用 `validate_monitor_payload(payload)`。
3. 返回验证后的载荷。

Issue 0009 之前的载荷：

```python
{
    "should_trigger_replay": True,
    "risk_score": 0.9,
    "reasons": ["baseline answer score is below success threshold"],
    "trace_summary": "...",
    "cost_per_decision": 0.2,
}
```

Issue 0009 之后的载荷：

```python
{
    "should_trigger_replay": True,
    "risk_score": 0.9,
    "anomaly_reason": "evidence_recall_low",
    "evidence_pointers": ["mem_001"],
    "trace_summary": "...",
    "cost_per_decision": 0.2,
}
```

调用方：

- `run_subagent_judge_monitor(...)` 在返回前调用 `decision.to_payload()`。
- 测试直接调用以验证载荷形态。

#### 函数：`run_subagent_judge_monitor(case, baseline, *, trigger_threshold=0.5)`（已更新）

签名（不变）：

```python
def run_subagent_judge_monitor(
    case: ProbeCase,
    baseline: BaselineOutput | None = None,
    *,
    trigger_threshold: float = 0.5,
) -> SubagentJudgeMonitorDecision
```

输入：

- `case`：一个已验证的 `ProbeCase`。
- `baseline`：可选基线输出（默认为 `vector_memory`）。
- `trigger_threshold`：`should_trigger_replay` 的 risk_score 阈值。

Risk score 计算（与 issue 0002 一致，不变）：

```python
risk_score = 0.0
if baseline.answer_score < 1.0:
    risk_score += 0.5
if baseline.evidence_score < 1.0:
    risk_score += 0.4
if not baseline.retrieved_memory_ids:
    risk_score += 0.1
risk_score = min(risk_score, 1.0)
```

Anomaly reason 选择（新增——取代自由形式 `reasons` 列表）：

```python
if not baseline.retrieved_memory_ids:
    anomaly_reason = "retrieved_context_incomplete"
elif baseline.evidence_score < 1.0:
    anomaly_reason = "evidence_recall_low"
elif baseline.answer_score < 1.0:
    anomaly_reason = "answer_vs_evidence_mismatch"
else:
    anomaly_reason = "confidence_anomaly"
```

优先级逻辑：


| 优先级   | 条件                  | 枚举值                            |
| ----- | ------------------- | ------------------------------ |
| 1（最高） | 未检索到 memory item    | `retrieved_context_incomplete` |
| 2     | 证据召回低于阈值            | `evidence_recall_low`          |
| 3     | 答案评分低于阈值（但证据充分）     | `answer_vs_evidence_mismatch`  |
| 4（兜底） | Risk score 异常但无主导信号 | `confidence_anomaly`           |


Evidence pointers（新增）：

```python
evidence_pointers = tuple(baseline.retrieved_memory_ids)
```

Monitor 返回基线的已检索 memory ID 作为不透明指针。这些是来自探针案例契约的 memory_id 字符串（例如 `"mem_001"`、`"mem_302"`），本身即是短令牌，不含嵌入内容。`__post_init_`_ 中的 `validate_evidence_pointers` 调用提供了纵深防御检查。

决策构造：

```python
decision = SubagentJudgeMonitorDecision(
    should_trigger_replay=risk_score >= trigger_threshold,
    risk_score=risk_score,
    anomaly_reason=anomaly_reason,
    evidence_pointers=evidence_pointers,
    trace_summary=(
        f"{baseline.baseline_name}: answer_score={baseline.answer_score:.3f}; "
        f"evidence_score={baseline.evidence_score:.3f}; "
        f"retrieved_count={len(baseline.retrieved_memory_ids)}"
    ),
)
decision.to_payload()
return decision
```

调用方：

- `run_baseline_suite(...)`
- 从 `cmd_audit.__init__` 导出

## 测试级契约

### `tests/test_cmd_audit_issue9_monitor_contract.py`

此文件是 issue 0009 的行为级规约，包含 4 个测试类和 15 个测试方法。

#### `MonitorAnomalyReasonEnumTest`


| 测试                                                | 验证内容                                                                                                                                                                                                                      |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_all_four_enum_values_accepted`              | 四个 `MONITOR_ANOMALY_REASON_VALUES` 中的每一个都被 `validate_monitor_anomaly_reason()` 接受。使用 `subTest` 测试全部四个值。                                                                                                                   |
| `test_free_form_natural_language_rejected`        | 四个自然语言原因的示例（"the answer looks wrong compared to stored facts"、"baseline evidence score is below success threshold"、"possible retrieval failure detected"、"suspicious context injection"）被 `MonitorAnomalyReasonError` 拒绝。 |
| `test_misspelled_enum_value_rejected`             | 五个近似值（拼写错误、尾部空格、大小写错误、空字符串）被拒绝。                                                                                                                                                                                           |
| `test_none_or_empty_rejected`                     | 空字符串被 `MonitorAnomalyReasonError` 拒绝。                                                                                                                                                                                     |
| `test_decision_construction_rejects_bad_reason`   | 使用无效的 `anomaly_reason` 构造 `SubagentJudgeMonitorDecision` 时，在 `__post_init__` 时刻抛出 `MonitorAnomalyReasonError`。                                                                                                            |
| `test_decision_construction_accepts_valid_reason` | 使用有效的 `anomaly_reason` 构造 `SubagentJudgeMonitorDecision` 成功，且值被保留。                                                                                                                                                        |


#### `MonitorEvidencePointerTest`


| 测试                                               | 验证内容                                                                                                        |
| ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| `test_opaque_ids_accepted`                       | 有效的不透明 ID（`"mem_001"`、`"mem_002"`、`"evt_301"`）被接受。                                                          |
| `test_empty_pointers_accepted`                   | 空元组被接受（无指针需验证）。                                                                                             |
| `test_content_bearing_pointers_rejected`         | 三个携带内容的指针示例（冒号分隔内容、描述性文本、换行注入）被 `LeakSafeMonitorError` 拒绝。使用 `subTest`。                                     |
| `test_pointer_with_colon_rejected`               | `"mem_003:Berlin"` 被拒绝（冒号是携带内容的分隔符）。                                                                        |
| `test_decision_construction_rejects_bad_pointer` | 使用携带内容的 evidence pointer 构造 `SubagentJudgeMonitorDecision` 时，在 `__post_init__` 时刻抛出 `LeakSafeMonitorError`。 |


#### `MonitorForbiddenFieldsTest`


| 测试                                                  | 验证内容                                                                                                                     |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `test_forbidden_field_names_rejected_in_payload`    | `FORBIDDEN_MONITOR_FIELDS` 中的全部 22 个键在出现在 Monitor 载荷中时均被拒绝。使用 `subTest` 测试全部 22 个键。在 issue 0009 上下文中重新验证 issue 0002 的契约。 |
| `test_clean_payload_with_anomaly_reason_accepted`   | 包含新的 `anomaly_reason` 和 `evidence_pointers` 字段（但无禁止字段）的载荷被 `validate_monitor_payload` 接受。                                |
| `test_payload_with_forbidden_field_nested_rejected` | 嵌套 dict 中的禁止字段被拒绝（测试递归的 `_reject_forbidden_monitor_fields`）。                                                             |


#### `MonitorEndToEndContractTest`


| 测试                                                         | 验证内容                                                                                                                                             |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `test_monitor_payload_exposes_anomaly_reason_and_pointers` | 端到端：从 `v0_issue3_cases.json` 加载真实探针案例，运行 `run_baseline_suite`，检查 Monitor 载荷包含有效的 `anomaly_reason`（在枚举中）和 `evidence_pointers`（全部为不透明 ID，无冒号、无空格）。 |


## 产物契约

Issue 0009 不产生新的输出产物。它加固了现有 `BaselineSuiteResult.monitor` 路径的内部 Monitor 契约。

`BaselineSuiteResult.monitor.to_payload()` 中的 Monitor 载荷形态是契约变更点。载荷的现有消费者（测试、harness）已更新为引用 `anomaly_reason` 而非 `reasons`。

## `cmd_audit/__init__.py` 导出

Issue 0009 的新公共导出：


| 导出                                | 来源模块                  |
| --------------------------------- | --------------------- |
| `MONITOR_ANOMALY_REASON_VALUES`   | `cmd_audit.labels`    |
| `MonitorAnomalyReasonError`       | `cmd_audit.labels`    |
| `SubagentJudgeMonitorDecision`    | `cmd_audit.baselines` |
| `validate_evidence_pointers`      | `cmd_audit.baselines` |
| `validate_monitor_anomaly_reason` | `cmd_audit.labels`    |
| `validate_monitor_payload`        | `cmd_audit.baselines` |


从 issue 0002 保留的已有导出：`BaselineSuiteResult`、`run_baseline_suite`、`validate_v0_label`、`V0_PIPELINE_LABEL_ORDER`、`V0_PIPELINE_LABELS`。

## 边界规则

- Monitor 的 `anomaly_reason` 仅接受四个枚举值。任何其他字符串在构造时被拒绝。
- Monitor 的 `evidence_pointers` 仅接受不透明 ID（无空格、无冒号、无换行、≤128 字符、非空）。携带内容的字符串在构造时被拒绝。
- 禁止字段黑名单（`FORBIDDEN_MONITOR_FIELDS`）在 `to_payload()` 时刻继续强制执行（与 issue 0002 一致，不变）。
- Monitor 仍然只触发重放，不输出最终标签、ECS、内存写入、gold answer 或完整失败轨迹。
- Monitor 的 `trace_summary` 仍然是用于调试的单一聚合字符串，其内容不被验证（内部追踪，非面向用户输出）。
- `ingestion_error` 在 `DEFERRED_PIPELINE_LABELS`（labels.py）中注册。被 `validate_v0_label` 拒绝，但不是 Monitor 的关注点——Monitor 仅通过比较器层间接使用 pipeline labels。

## 验证

命令：

```bash
python3 -m pytest tests/test_cmd_audit_issue9_monitor_contract.py -v
python3 -m pytest                                  # 完整测试套件
python3 -m cmd_audit run                           # 产物生成
```

已验证状态：

```text
31 个测试通过（16 个已有 + 15 个 issue 0009）
向 artifacts/attribution_table.csv 写入了 6 个归因行
比较指标写入 artifacts/comparison_metrics.csv
混淆矩阵写入 artifacts/attribution_confusion_matrix.csv
```

## Issue 0009 之后的剩余工作

Issue 0009 已通过。后续按依赖顺序的切片：

- Issue 0005：Post-Repair Context Replay，输出三值 `repair_assessment`。
- Issue 0006：定向内存修复。
- Issue 0007：ECS Failure Memory 复发（强制执行 ECS cause 的 item-label 名称禁止规则）。
- Issue 0010：基于证据的版本关口（HITL，被 0004/0005/0007 阻塞）。

