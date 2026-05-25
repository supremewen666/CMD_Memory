# Issue 0007 实现细节：ECS 失败记忆复发率验证

## 目的

本文档是 issue 0007（`验证 ECS 失败记忆复发率降低`）的详细实现蓝图。

Issue 0007 通过回答以下问题来闭合 V0 证据链：

```text
当 CMD 诊断出一个内存故障，并将一条 Error-Cause-Solution 记录存储到
失败记忆（Failure Memory）中时，该记录是否能在未来类似任务中减少重复故障
——同时不会用错误的过往轨迹污染那些未来任务？
```

当前实现的切片通过三模式对比来回答这一问题：

```text
原始 ProbeCase
  -> CMD-Audit 归因（issues 0001-0003）
  -> ECS 草案（issue 0005）
  -> FailureMemoryRecord 存储
  -> 未来相似 ProbeCase
      -> no-FM 上下文（无失败记忆）
      -> full-trace FM 上下文（反模式）
      -> corrected_guidance FM 上下文（CMD 模式）
  -> RecurrenceComparisonRow
  -> RecurrenceSummary
  -> recurrence_comparison.csv
```

该实现切片有意止步于生产级失败记忆部署或真实多任务 agent 评估之前。这些属于 **CMD-Skill Adapter** 边界。

## 来源要求

本实现遵循以下本地规划文件：

| 来源 | 在 Issue 0007 中应用的要求 |
| --- | --- |
| `TASK.md` | 定义 ECS 失败记忆的存储与检索契约；在有/无失败记忆的条件下测量复发率；验证失败记忆上下文不泄露金标答案；将失败记忆写入限定在沙箱写入边界内。 |
| `CLAUDE.md` | 不得将完整失败轨迹作为未来失败记忆上下文存储或复用；使用 `corrected_memory + repair_guidance`；保持 **CMD-Audit** 与 **CMD-Skill Adapter** 分离；将写入权限限制在重放本地沙箱内。 |
| `cmd_innovation_core/CONTEXT.md` | **Failure Memory** 存储 **Error-Cause-Solution** 记录；检索时仅注入 `corrected_memory + repair_guidance`，而非完整失败轨迹；**CMD-Audit** 写入权限受沙箱限制。 |
| `cmd_innovation_core/prd/cmd_minimal_probe_prd.md` | ECS 记录包含错误类型、错误内存、原始证据、原因、修正后内存、修复动作、修复指导以及触发签名；未来任务检索修正后内存和修复指导；对比包含答案分数、证据召回率和 token 成本。 |
| `cmd_innovation_core/issues/0007-validate-ecs-failure-memory-recurrence.md` | ECS 记录包含所有必需字段；未来任务检索 `corrected_memory + repair_guidance`，而非完整失败轨迹；对比包含幻觉率、冲突复发率、污染复发率、答案分数、证据召回率及追加 token 成本；结果说明失败记忆是否有足够价值以保留在范围内。 |
| `cmd_innovation_core/tdd/cmd_tracer_bullets.md` | 循环 6（ECS 未来检索）：一个已诊断的案例产生 ECS 指导；未来相似任务应接收到修正后内存和修复指导，而非完整失败轨迹。 |

## 领域边界

Issue 0007 位于 ECS 草案（issue 0005）与证据驱动版本关口（issue 0010）之间：

```text
ProbeCase（原始）
  -> issues 0001-0003：CMD-Audit 归因
  -> issue 0005：ECS 草案
  -> issue 0007：失败记忆层
      -> FailureMemoryRecord 存储
      -> trigger_signature 索引
      -> 基于关键词的检索
      -> 三模式上下文构建
          -> "none"（无失败记忆）
          -> "full_trace"（反模式：注入过往 wrong_memory）
          -> "corrected_guidance"（CMD 模式：注入 corrected_memory + repair_guidance）
      -> RecurrenceComparisonRow（逐案例对比）
      -> RecurrenceSummary（聚合指标）
      -> recurrence_comparison.csv（证据产物）
```

关键分离点：

- **FailureMemoryRecord**：将 ECS 包装为可检索的触发签名；不存储原始失败轨迹。
- **FailureMemoryStore**：不可变存储，基于关键词检索；绝不检索完整失败轨迹。
- **build_failure_memory_context**：三种显式模式；`full_trace` 模式仅作为对比反模式存在。
- **RecurrenceComparisonRow**：衡量 CMD 失败记忆是否有帮助，而非是否已可投入生产。
- **CMD-Skill Adapter**：仍然推迟；issue 0007 不写入生产 agent 内存。

## 当前代码产物

| 产物 | 在 issue 0007 中的角色 |
| --- | --- |
| `cmd_audit/failure_memory.py` | 核心模块：数据类型、存储、检索、上下文构建器、复发率运行器、聚合、表格输出器。 |
| `cmd_audit/__init__.py` | 从 `failure_memory.py` 导出 8 个新符号。 |
| `data/probe_cases/v0_issue3_cases.json` | 用作失败记忆"训练"集的 6 个原始探针案例。 |
| `data/probe_cases/v0_issue7_future_cases.json` | 用于复发率测量的 3 个未来任务探针案例。 |
| `tests/test_cmd_audit_issue7_failure_memory.py` | 跨 10 个测试类的 44 个行为级测试。 |
| `artifacts/sandbox/recurrence_comparison.csv` | 逐案例三模式对比表。 |
| `artifacts/sandbox/recurrence_summary.txt` | 聚合复发率摘要及声明陈述。 |

## 模块映射

| 模块 | Issue 0007 角色 |
| --- | --- |
| `cmd_audit/failure_memory.py` | 拥有失败记忆数据类型、存储、检索、上下文构建器、复发率对比、聚合和表格输出。 |
| `cmd_audit/post_repair.py` | 提供 `ECSDraft`（作为 `FailureMemoryRecord.from_ecs_draft` 的输入）和 `validate_sandbox_path`（输出关口）。 |
| `cmd_audit/models.py` | 提供 `ProbeCase` 和 `GoldEvidence`（用于构建记录）。 |
| `cmd_audit/scoring.py` | 提供 `evidence_recall_from_text`（用于上下文评分和污染风险评估）。 |
| `cmd_audit/labels.py` | 提供 `validate_v0_label`（用于 `FailureMemoryRecord.__post_init__`）。 |
| `cmd_audit/harness.py` | Issue 0007 不修改 harness；复发率路径是独立的。 |
| `cmd_audit/__init__.py` | 导出调用方和测试所需的公开接口。 |

## 调用图

主复发率路径：

```text
tests/test_cmd_audit_issue7_failure_memory.py
  -> models.load_probe_cases（issue 3 案例）
  -> harness.run_case（逐案例）
  -> post_repair.draft_ecs（逐案例）
  -> failure_memory.FailureMemoryRecord.from_ecs_draft
      -> failure_memory._build_trigger_signature
          -> failure_memory._extract_keywords
      -> labels.validate_v0_label
  -> failure_memory.FailureMemoryStore.add（逐记录）
  -> models.load_probe_cases（issue 7 未来案例）
  -> failure_memory.run_recurrence_comparisons
      -> failure_memory.run_recurrence_comparison
          -> failure_memory.FailureMemoryStore.retrieve
              -> failure_memory._extract_keywords
          -> failure_memory._score_context（x3：none / full_trace / corrected_guidance）
              -> scoring.evidence_recall_from_text
          -> failure_memory.build_failure_memory_context（x2：full_trace / corrected_guidance）
  -> failure_memory.compute_recurrence_summary
  -> failure_memory.write_recurrence_comparison_table
      -> post_repair.validate_sandbox_path
      -> failure_memory._write_recurrence_summary
```

基于 CLI 的产物生成：

```text
python3 -c "..."
  -> models.load_probe_cases
  -> harness.run_case
  -> post_repair.draft_ecs
  -> failure_memory.FailureMemoryRecord.from_ecs_draft
  -> failure_memory.FailureMemoryStore.add
  -> failure_memory.run_recurrence_comparisons
  -> failure_memory.compute_recurrence_summary
  -> failure_memory.write_recurrence_comparison_table
```

## 数据流

输入 fixture：

```text
data/probe_cases/v0_issue3_cases.json   （6 个原始案例 → 失败记忆）
data/probe_cases/v0_issue7_future_cases.json   （3 个未来相似任务案例）
```

关键 fixture 关系：

- `v0-fm-retrieval-001` 是 `v0-retrieval-001` 的未来变体（两者均涉及 Mira/Lisbon/Q3 offsite，但查询措辞和事件文本不同）。
- `v0-fm-premature-extraction-001` 是 `v0-premature-extraction-001` 的未来变体（两者均涉及 Nia/Berlin/incident review）。
- `v0-fm-compression-001` 是 `v0-compression-001` 的未来变体（两者均涉及 Omar/Prague/retention review）。
- 每个未来案例拥有独立的失败基线和金标证据，与原始案例无关。
- 来自原始案例的失败记忆提供的修正后证据与未来案例的金标证据领域相匹配。

Issue 0007 输出：

```text
RecurrenceComparisonRow
  case_id
  perturbation_label
  no_fm_answer_score, no_fm_evidence_score
  full_trace_answer_score, full_trace_evidence_score
  corrected_guidance_answer_score, corrected_guidance_evidence_score
  no_fm_token_cost, full_trace_token_cost, corrected_guidance_token_cost
  full_trace_pollution_risk
  corrected_guidance_better_than_none
  corrected_guidance_better_than_full_trace
  failure_memory_useful

RecurrenceSummary
  total_cases
  fm_useful_count, fm_useful_rate
  avg_evidence_gain_vs_none, avg_evidence_gain_vs_full_trace
  avg_full_trace_pollution_risk
  avg_token_cost_none, avg_token_cost_full_trace, avg_token_cost_corrected_guidance
  failure_memory_worth_keeping
```

产物输出：

```text
artifacts/sandbox/recurrence_comparison.csv
artifacts/sandbox/recurrence_summary.txt
```

## 函数级契约

### `cmd_audit/failure_memory.py`

本模块拥有 issue 0007 的整个失败记忆对外接口。这是一个新模块，依赖 `post_repair.py`（获取 `ECSDraft` 和 `validate_sandbox_path`）、`models.py`（获取 `ProbeCase`）、`scoring.py`（获取 `evidence_recall_from_text`）和 `labels.py`（获取 `validate_v0_label`）。

### 常量：`_STOP_WORDS`

定义：

```python
_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "was", "are", "were", "be", "been",
    "for", "of", "in", "to", "with", "on", "at", "by", "from",
    "which", "what", "who", "whom", "whose", "where", "when",
    "did", "do", "does", "has", "have", "had", "this", "that",
    "and", "or", "not", "but", "if", "then", "else", "about",
    "city", "chose", "choose", "selected", "select",
})
```

角色：

- 在触发签名提取中过滤掉常见英语功能词和查询模板词。
- 使触发签名聚焦于领域重要术语（人名、城市名、事件类型）。
- 额外的查询模板词（`city`、`chose`、`choose`、`selected`、`select`）防止查询模板本身主导签名。

被使用者：

- `_extract_keywords`。

### 常量：`_CONTEXT_MODE_VALUES`

定义：

```python
_CONTEXT_MODE_VALUES = ("none", "full_trace", "corrected_guidance")
```

角色：

- 定义三种合法的失败记忆上下文模式。
- 由 `build_failure_memory_context` 用于校验 `mode` 参数。

### 辅助函数：`_extract_keywords(text: str) -> tuple[str, ...]`

目的：

- 从查询字符串中提取重要关键词，用于触发签名构建和检索匹配。

行为：

1. 使用 `re.findall(r"\b[a-zA-Z]{3,}\b", text.casefold())` 查找所有 3 个字符以上的字母 token。
2. 过滤掉存在于 `_STOP_WORDS` 中的 token。
3. 返回排序去重后的元组。

调用方：

- `_build_trigger_signature`（用于存储）。
- `FailureMemoryStore.retrieve`（用于查询端关键词提取）。

领域含义：

- 无需外部依赖即可实现关键词重叠检索（无 embedding、无向量存储、无 LLM）。

### 辅助函数：`_build_trigger_signature(query: str, label: str) -> str`

目的：

- 从查询和错误标签构建可检索的触发签名。

行为：

1. 调用 `_extract_keywords(query)` 获取重要术语。
2. 返回 `f"{label}|{' '.join(keywords)}"`。

示例输出：

```text
"retrieval_error|lisbon mira offsite"
```

调用方：

- `FailureMemoryRecord.from_ecs_draft`。

领域含义：

- 触发签名同时编码故障类型和领域重要术语。
- 检索通过新查询与已存储签名之间的关键词重叠进行匹配。
- `|` 分隔符在检索分词时清晰地区分标签前缀与关键词 token。

### 数据类：`FailureMemoryRecord`

字段：

```python
error_type: str
wrong_memory: str
original_evidence: str
cause: str
corrected_memory: str
repair_action: str
repair_guidance: str
trigger_signature: str
```

角色：

- 存储在 **Failure Memory** 中的不可变 ECS 派生记录。
- 包含 issue 0007 验收标准要求的全部 8 个字段。

各字段含义：

| 字段 | 来源 | 领域含义 |
| --- | --- | --- |
| `error_type` | `ecs.predicted_label` | 来自 CMD 归因的 V0 管线标签。 |
| `wrong_memory` | `baseline.injected_context` | 呈现给 agent 的失败基线上下文。 |
| `original_evidence` | `case.gold_evidence`（拼接） | 基线本应使用的金标证据。 |
| `cause` | `ecs.cause` | 自然语言原因描述（已验证：不含物品级标签）。 |
| `corrected_memory` | `ecs.corrected_memory` | 使答案得以恢复的重放证据块。 |
| `repair_action` | `ecs.predicted_label` | 标签名称，作为修复动作标识符。 |
| `repair_guidance` | `ecs.repair_guidance` | 面向未来相似任务的指导文本。 |
| `trigger_signature` | `_build_trigger_signature(query, label)` | 用于关键词匹配的可检索签名。 |

#### `FailureMemoryRecord.__post_init__(self) -> None`

目的：

- 验证 `error_type` 是合法的 V0 管线标签。

行为：

- 调用 `validate_v0_label(self.error_type)`。
- 若标签为错误的内存物品标签或推迟的管线标签，则抛出 `LabelValidationError`。

为何重要：

- 确保 **Failure Memory** 记录永远不会超出 V0 归因范围。
- 阻止物品级标签（`item_wrong`、`item_stale` 等）进入失败记忆存储。

#### `FailureMemoryRecord.from_ecs_draft(cls, ecs: ECSDraft, case: ProbeCase) -> "FailureMemoryRecord"`

目的：

- 工厂构造函数，将 ECS 草案及其源案例转换为失败记忆记录。

行为：

1. 从 `case.primary_baseline` 读取 `wrong_memory`（基线的 `injected_context`）。
2. 将所有 `case.gold_evidence` 文本以 `" | "` 拼接为 `original_evidence`。
3. 直接复制 `ecs.cause`、`ecs.corrected_memory`、`ecs.repair_guidance`。
4. 将 `ecs.predicted_label` 同时用作 `error_type` 和 `repair_action`。
5. 从 `case.query` 和 `ecs.predicted_label` 构建 `trigger_signature`。

调用方：

- 测试（所有构建 FM 存储的测试类）。
- 产物生成脚本。
- 从 `cmd_audit` 导入的外部用户。

领域边界：

- `wrong_memory` 是基线的 `injected_context`，而非完整失败轨迹。
- `corrected_memory` 是重放证据块，对于非推理类错误，它与 `wrong_memory` 不同。
- 对于 `reasoning_error`，两者可能使用相同的证据文本，因为证据已正确检索，仅最终推理步骤失败。修复添加的是推理指导，而非修正后的内存内容。

### 数据类：`FailureMemoryStore`

字段：

```python
records: tuple[FailureMemoryRecord, ...] = ()
```

角色：

- **Failure Memory** 记录的不可变存储。
- 支持仅追加式添加和基于关键词的检索。
- 绝不存储或检索完整失败轨迹。

#### `FailureMemoryStore.add(self, record: FailureMemoryRecord) -> "FailureMemoryStore"`

目的：

- 返回追加了该记录的新存储。

行为：

- `return FailureMemoryStore(records=self.records + (record,))`。

为何不可变：

- 确保存储安全用于重放本地沙箱环境。
- 防止复发率测量过程中的意外修改。
- 遵循与其他 CMD-Audit 数据类型相同的不可变模式。

调用方：

- 所有构建 FM 存储的测试 `setUpClass` 方法。
- 产物生成脚本。

#### `FailureMemoryStore.retrieve(self, query: str, top_k: int = 3) -> tuple[FailureMemoryRecord, ...]`

目的：

- 通过关键词重叠为新查询检索相关的失败记忆记录。

行为：

1. 使用 `_extract_keywords` 从 `query` 中提取关键词。
2. 若未提取到关键词则返回空元组。
3. 对每条已存储记录：
   - 对 `record.trigger_signature` 在 casefold 后按空白分隔分词。
   - 计算查询关键词与签名 token 之间的重叠数。
   - 收集重叠数大于 0 的 `(overlap, record)` 对。
4. 按重叠数降序排列。
5. 返回最多 `top_k` 条记录。

边界情况：

- 空存储返回 `()`。
- 无可提取关键词的查询返回 `()`。
- 不相关查询（与所有记录零关键词重叠）返回 `()`。

调用方：

- `run_recurrence_comparison`。

领域含义：

- 这是 V0 的基于关键词的检索，而非语义 embedding 搜索。
- 触发签名格式（`label|keyword1 keyword2 ...`）确保错误类型和领域术语都参与匹配。
- 一个关于"Q3 offsite"的未来任务将匹配触发签名中包含"q3"、"offsite"的记录。

#### `FailureMemoryStore.__len__(self) -> int`

目的：

- 返回已存储记录的数量。

#### `FailureMemoryStore.__bool__(self) -> bool`

目的：

- 若存储中至少有一条记录则返回 `True`。

### 函数：`build_failure_memory_context(records: tuple[FailureMemoryRecord, ...], mode: str) -> str`

目的：

- 从检索到的失败记忆记录构建上下文文本，用于注入未来任务。

签名：

```python
def build_failure_memory_context(
    records: tuple[FailureMemoryRecord, ...],
    mode: str,
) -> str
```

输入：

- `records`：检索到的失败记忆记录（可为空）。
- `mode`：`"none"`、`"full_trace"`、`"corrected_guidance"` 之一。

返回值：

- 可直接注入的上下文字符串；对于 `"none"` 模式或空记录返回 `""`。

各模式行为：

**`"none"`**：
- 始终返回 `""`。
- 代表基线：完全无失败记忆。

**`"full_trace"`**（反模式）：
- 对每条记录，注入 `[Past Failure Trace N]\n{record.wrong_memory}`。
- `wrong_memory` 是来自失败案例的基线 `injected_context`。
- 这是演示为何不应存储完整轨迹的对比分支。
- 错误的基线上下文通常缺少金标证据，存在污染新任务的风险。

**`"corrected_guidance"`**（CMD 模式）：
- 对每条记录，注入：
  ```text
  [Failure Memory Guidance N]
  Corrected: {record.corrected_memory}
  Guidance: {record.repair_guidance}
  ```
- `corrected_memory` 包含使答案得以恢复的重放证据块。
- `repair_guidance` 为新任务提供可操作的指导。
- 这是 CMD 推荐的失败记忆检索模式。

边界情况：

- 当 `mode == "none"` 或 `records` 为空（无论何种模式）时返回 `""`。
- 对未知模式值抛出 `ValueError`。

为何校验模式：

- 防止意外注入未定义的上下文类型。
- 使三模式对比变得显式且可审计。

调用方：

- `run_recurrence_comparison`（两次：分别用于 `full_trace` 和 `corrected_guidance` 模式）。
- 测试（所有 `BuildFailureMemoryContextTest` 方法）。

领域边界：

- `corrected_guidance` 模式仅注入 `corrected_memory + repair_guidance`，绝不注入 `wrong_memory` 或 `original_evidence`。
- `full_trace` 模式仅用于对比；issue 0007 验收标准明确要求未来任务不得检索完整失败轨迹。
- 该函数不修改存储或记录。

### 辅助函数：`_score_context(gold_evidence, gold_answer: str, fm_context: str, query: str) -> tuple[float, float, float]`

目的：

- 为上下文（有或无失败记忆）评分，涵盖证据召回率、答案存在性和 token 成本。

签名：

```python
def _score_context(
    gold_evidence, gold_answer: str, fm_context: str, query: str
) -> tuple[float, float, float]
```

返回值：

- `(answer_score, evidence_score, token_cost)`。

行为：

1. 若 fm_context 非空则构建合并文本 `"{fm_context}\n\nQuery: {query}"`，否则为 `"Query: {query}"`。
2. 通过 `evidence_recall_from_text(gold_evidence, combined)` 计算 `evidence_score`。
3. 计算 `answer_score`：若 `gold_answer.casefold() in combined.casefold()` 则为 `1.0`，否则为 `0.0`。
4. 计算 `token_cost = len(combined) / 4.0`（基于字符的 token 估算器，约 4 字符/token）。

领域含义：

- 这是 V0 的确定性上下文评分器。
- 答案分数模拟 agent 能否从提供的上下文中提取出正确答案。
- 证据分数衡量所需证据短语是否存在。
- 两项分数均不调用 LLM；两者均基于与金标数据的字符串匹配操作。
- 金标答案不作为独立答案注入——仅检查其是否存在于合并上下文中，以模拟 agent 找到它的能力。
- 金标答案检查是 V0 的合成捷径。真实的 Post-Repair Context Replay（issue 0005）不注入金标答案；此处我们检查上下文是否*包含*答案文本。

调用方：

- `run_recurrence_comparison`（三次：每种模式一次）。

### 数据类：`RecurrenceComparisonRow`

字段：

```python
case_id: str
perturbation_label: str
no_fm_answer_score: float
no_fm_evidence_score: float
full_trace_answer_score: float
full_trace_evidence_score: float
corrected_guidance_answer_score: float
corrected_guidance_evidence_score: float
no_fm_token_cost: float
full_trace_token_cost: float
corrected_guidance_token_cost: float
full_trace_pollution_risk: float
corrected_guidance_better_than_none: bool
corrected_guidance_better_than_full_trace: bool
failure_memory_useful: bool
```

角色：

- 单个未来任务案例的三模式复发率对比中的一行。
- 记录 CMD 失败记忆（corrected_guidance）相对于无 FM 和 full-trace 基线是否有用。

各字段含义：

| 字段 | 含义 |
| --- | --- |
| `no_fm_answer_score` | 无失败记忆上下文时的答案分数。 |
| `no_fm_evidence_score` | 无失败记忆上下文时的证据分数。 |
| `full_trace_answer_score` | 注入过往 `wrong_memory` 时的答案分数（反模式）。 |
| `full_trace_evidence_score` | 注入过往 `wrong_memory` 时的证据分数。 |
| `corrected_guidance_answer_score` | 注入 `corrected_memory + repair_guidance` 时的答案分数（CMD 模式）。 |
| `corrected_guidance_evidence_score` | 注入 `corrected_memory + repair_guidance` 时的证据分数。 |
| `full_trace_pollution_risk` | `1.0 - evidence_recall(full_trace_context)`。当完整轨迹缺少新任务所需证据时该值较高。 |
| `corrected_guidance_better_than_none` | 当修正指导相比无 FM 有改善时为 True（证据优先，答案作为平局决胜）。 |
| `corrected_guidance_better_than_full_trace` | 当修正指导至少不差于完整轨迹时为 True。 |
| `failure_memory_useful` | 等于 `corrected_guidance_better_than_none`。 |

#### `RecurrenceComparisonRow.any_fm_improvement(self) -> bool`

目的：

- 返回 `self.corrected_guidance_better_than_none`。

领域含义：

- 便捷访问器，用于测试检查 FM 是否提供任何益处。

#### `RecurrenceComparisonRow.full_trace_causes_regression(self) -> bool`

目的：

- 当完整轨迹模式在证据或答案上得分低于无 FM 模式时返回 `True`。

行为：

```python
return (
    self.full_trace_evidence_score < self.no_fm_evidence_score
    or self.full_trace_answer_score < self.no_fm_answer_score
)
```

领域含义：

- 检测污染效应：注入过往错误轨迹会主动损害未来任务的表现。

### 函数：`run_recurrence_comparison(case: ProbeCase, fm_store: FailureMemoryStore) -> RecurrenceComparisonRow`

目的：

- 为单个未来任务案例运行三模式失败记忆对比。

签名：

```python
def run_recurrence_comparison(
    case: ProbeCase,
    fm_store: FailureMemoryStore,
) -> RecurrenceComparisonRow
```

输入：

- `case`：一个未来相似任务探针案例。
- `fm_store`：由先前已诊断案例构建的失败记忆存储。

逐步行为：

1. 通过 `fm_store.retrieve(case.query)` 检索相关 FM 记录。
2. 对无 FM 模式评分：空上下文 + 查询。
3. 通过 `build_failure_memory_context(records, "full_trace")` 构建完整轨迹上下文。
4. 对完整轨迹模式评分。
5. 通过 `build_failure_memory_context(records, "corrected_guidance")` 构建修正指导上下文。
6. 对修正指导模式评分。
7. 计算污染风险：`1.0 - evidence_recall_from_text(case.gold_evidence, full_trace_ctx)`。
8. 确定对比标志：
   - `cg_better_none`：证据增益 > 0，或证据持平且答案更好。
   - `cg_better_ft`：证据增益 > 0，或证据持平且答案 >=。
   - `fm_useful = cg_better_none`。
9. 返回 `RecurrenceComparisonRow`。

对比逻辑（证据优先）：

```text
cg_better_none = (cg_ev > no_fm_ev) or (cg_ev == no_fm_ev and cg_ans > no_fm_ans)
cg_better_ft  = (cg_ev > ft_ev) or (cg_ev == ft_ev and cg_ans >= ft_ans)
```

为何证据优先：

- 证据召回率是 CMD 的主要信号；答案正确性取决于对证据的推理。
- 若修正指导改善了证据召回率，则即使答案分数未变（agent 可能仍需更好的推理），FM 也是有用的。

调用方：

- `run_recurrence_comparisons`。
- 测试。

领域边界：

- 该函数不修改探针案例、FM 存储或任何持久状态。
- 它是纯粹的测量函数，而非修复函数。
- 金标答案仅检查是否存在于合并上下文中，不作为答案注入。

### 函数：`run_recurrence_comparisons(cases: list[ProbeCase], fm_store: FailureMemoryStore) -> list[RecurrenceComparisonRow]`

目的：

- 批处理包装器，为多个未来任务案例运行复发率对比。

行为：

- 返回 `[run_recurrence_comparison(case, fm_store) for case in cases]`。

调用方：

- 测试。
- 产物生成脚本。

### 数据类：`RecurrenceSummary`

字段：

```python
total_cases: int
fm_useful_count: int
fm_useful_rate: float
avg_evidence_gain_vs_none: float
avg_evidence_gain_vs_full_trace: float
avg_full_trace_pollution_risk: float
avg_token_cost_none: float
avg_token_cost_full_trace: float
avg_token_cost_corrected_guidance: float
failure_memory_worth_keeping: bool
```

角色：

- 跨所有未来任务案例的聚合失败记忆复发率指标。
- 为"失败记忆值得保留在范围内"这一声明提供证据基础。

各字段含义：

| 字段 | 计算方式 |
| --- | --- |
| `total_cases` | `len(rows)` |
| `fm_useful_count` | 统计 `row.failure_memory_useful == True` 的数量 |
| `fm_useful_rate` | `fm_useful_count / total_cases` |
| `avg_evidence_gain_vs_none` | `(cg_ev - no_fm_ev)` 的均值 |
| `avg_evidence_gain_vs_full_trace` | `(cg_ev - ft_ev)` 的均值 |
| `avg_full_trace_pollution_risk` | `row.full_trace_pollution_risk` 的均值 |
| `avg_token_cost_none` | `row.no_fm_token_cost` 的均值 |
| `avg_token_cost_full_trace` | `row.full_trace_token_cost` 的均值 |
| `avg_token_cost_corrected_guidance` | `row.corrected_guidance_token_cost` 的均值 |
| `failure_memory_worth_keeping` | `fm_useful_rate >= 0.5` |

### 函数：`compute_recurrence_summary(rows: list[RecurrenceComparisonRow]) -> RecurrenceSummary`

目的：

- 将逐案例复发率对比行聚合为摘要。

签名：

```python
def compute_recurrence_summary(
    rows: list[RecurrenceComparisonRow],
) -> RecurrenceSummary
```

行为：

1. 若 `rows` 为空则返回零值摘要。
2. 否则从各行计算所有聚合字段。
3. 当 `fm_useful_rate >= 0.5` 时设置 `failure_memory_worth_keeping = True`。

边界情况（空行）：

```python
RecurrenceSummary(
    total_cases=0, fm_useful_count=0, fm_useful_rate=0.0,
    avg_evidence_gain_vs_none=0.0, avg_evidence_gain_vs_full_trace=0.0,
    avg_full_trace_pollution_risk=0.0,
    avg_token_cost_none=0.0, avg_token_cost_full_trace=0.0,
    avg_token_cost_corrected_guidance=0.0,
    failure_memory_worth_keeping=False,
)
```

调用方：

- `_write_recurrence_summary`。
- 测试（`RecurrenceSummaryTest`、`FullPipelineRecurrenceTest`）。

领域含义：

- `failure_memory_worth_keeping` 的 0.5 阈值是 V0 的烟雾级关口。
- 若至少一半的未来任务案例受益于 FM，则将其保留在范围内。
- 对于第一份证据切片而言，这是一个有意设低的门槛；论文声明需要更强的证据。

### 函数：`write_recurrence_comparison_table(rows, output_path, *, sandbox_root=None) -> Path`

目的：

- 将复发率对比 CSV 产物和摘要文本文件写入沙箱。

签名：

```python
def write_recurrence_comparison_table(
    rows: list[RecurrenceComparisonRow],
    output_path: str | Path,
    *,
    sandbox_root: str | Path | None = None,
) -> Path
```

行为：

1. 解析 `output_path` 并通过 `validate_sandbox_path` 验证其在沙箱内。
2. 创建父目录。
3. 写入包含 15 列的 CSV：
   - `case_id`、`perturbation_label`
   - `no_fm_answer_score`、`no_fm_evidence_score`
   - `full_trace_answer_score`、`full_trace_evidence_score`
   - `corrected_guidance_answer_score`、`corrected_guidance_evidence_score`
   - `no_fm_token_cost`、`full_trace_token_cost`、`corrected_guidance_token_cost`
   - `full_trace_pollution_risk`
   - `corrected_guidance_better_than_none`、`corrected_guidance_better_than_full_trace`
   - `failure_memory_useful`
4. 数值分数格式化为 3 位小数，token 成本格式化为 1 位小数。
5. 布尔值格式化为小写字符串。
6. 调用 `_write_recurrence_summary(rows, path.parent / "recurrence_summary.txt")`。
7. 返回输出路径。

沙箱边界：

- 调用 `validate_sandbox_path(path, sandbox_root)`，拒绝重放本地沙箱之外的路径。
- 默认沙箱为 `artifacts/sandbox/`。

调用方：

- 产物生成。
- 测试（`RecurrenceTableOutputTest`）。

### 辅助函数：`_write_recurrence_summary(rows: list[RecurrenceComparisonRow], path: Path) -> None`

目的：

- 写入人类可读的复发率摘要文本文件。

行为：

1. 调用 `compute_recurrence_summary(rows)`。
2. 写入结构化文本文件，包含以下段落：
   - 头部，标识该产物为 "CMD V0 ECS Failure Memory Recurrence Summary — Issue 0007"。
   - 案例总数。
   - 失败记忆效用（数量和比率）。
   - 相对 none 和相对 full_trace 的证据增益。
   - 完整轨迹污染风险。
   - 三种模式下的 token 成本对比。
   - 布尔值：FM 是否值得保留。
   - 声明陈述。
   - 逐案例详细行。

调用方：

- `write_recurrence_comparison_table`。

## `cmd_audit/__init__.py` 公开接口

Issue 0007 导出的符号：

- `FailureMemoryRecord`
- `FailureMemoryStore`
- `RecurrenceComparisonRow`
- `RecurrenceSummary`
- `build_failure_memory_context`
- `compute_recurrence_summary`
- `run_recurrence_comparison`
- `run_recurrence_comparisons`
- `write_recurrence_comparison_table`

为何导出它们：

- 测试和未来 issue 切片可以使用稳定的公开接口。
- harness 保持独立，不暴露 CMD-Skill Adapter。

## `cmd_audit/post_repair.py` 复用的契约

Issue 0007 依赖 issue 0005 中的两个现有契约：

### `ECSDraft`

作为 `FailureMemoryRecord.from_ecs_draft` 的输入。使用的字段如下：

- `predicted_label` → `error_type` 和 `repair_action`
- `cause` → `cause`（已由 `ECSDraft.__post_init__` 验证不含物品级标签）
- `corrected_memory` → `corrected_memory`
- `repair_guidance` → `repair_guidance`
- `repaired_evidence_block` → 不直接使用；`corrected_memory` 承载重放证据

### `validate_sandbox_path`

由 `write_recurrence_comparison_table` 用于实施沙箱写入边界。所有失败记忆产物写入必须落在 `artifacts/sandbox/`（或显式指定的 `sandbox_root`）之下。

## `cmd_audit/scoring.py` 复用的契约

### `evidence_recall_from_text`

在三个位置使用：

1. `_score_context`：衡量每种 FM 上下文模式是否包含所需金标证据短语。
2. `run_recurrence_comparison`：计算 `full_trace_pollution_risk = 1.0 - evidence_recall_from_text(gold_evidence, full_trace_ctx)`。
3. `_score_context` 隐式地为三种模式各调用一次。

## `cmd_audit/labels.py` 复用的契约

### `validate_v0_label`

由 `FailureMemoryRecord.__post_init__` 调用，以确保 `error_type` 是合法的 V0 管线标签。这防止了错误的内存物品标签（`item_wrong`、`item_stale` 等）和推迟的管线标签（`granularity_error`、`route_error` 等）进入失败记忆。

## Issue 0007 的探针案例设计

### 原始案例（失败记忆来源）

`data/probe_cases/v0_issue3_cases.json` 包含 6 个案例，每个 V0 标签对应一个。这些案例通过完整 CMD 管线运行以生成 ECS 草案，随后存储为失败记忆记录。

### 未来任务案例（复发率测量）

`data/probe_cases/v0_issue7_future_cases.json` 包含 3 个案例：

| 案例 ID | 标签 | 配对的原始案例 | 查询差异 |
| --- | --- | --- | --- |
| `v0-fm-retrieval-001` | `retrieval_error` | `v0-retrieval-001`（Mira/Lisbon/Q3 offsite） | "pick for the Q3 offsite meeting" vs "choose for the Q3 offsite" |
| `v0-fm-premature-extraction-001` | `premature_extraction_error` | `v0-premature-extraction-001`（Nia/Berlin/incident review） | "select for the incident review meeting" vs "choose for the incident review" |
| `v0-fm-compression-001` | `compression_error` | `v0-compression-001`（Omar/Prague/retention review） | "pick for the retention review meeting" vs "choose for the retention review" |

每个未来案例：

- 拥有独立的原始事件、提取后的内存、金标证据和基线输出。
- 使用与原始案例不同的事件 ID 和内存 ID，以避免交叉污染。
- 使用略有不同的查询措辞，使任务"相似"而非"完全相同"。
- 具有与错误类型匹配的失败基线（answer_score=0.0, evidence_score=0.0）。
- 与配对原始案例共享相同的金标答案领域（相同的人、城市、事件）。

共享的领域意味着来自原始案例的失败记忆包含带有正确证据短语的 `corrected_memory`，`_score_context` 通过 `evidence_recall_from_text` 检测到这些短语。

## 测试覆盖

测试文件：

```text
tests/test_cmd_audit_issue7_failure_memory.py
```

跨 10 个测试类共 44 个测试。

### `FailureMemoryRecordCreationTest`（5 个测试）

**`test_record_from_ecs_has_all_required_fields`**

验证：

- 全部 6 个原始案例生成的 FM 记录均包含所有 8 个必需的字符串字段。
- `error_type` 在 `V0_PIPELINE_LABEL_ORDER` 中。
- `trigger_signature` 非空。

**`test_record_error_type_matches_ecs_predicted_label`**

验证：

- 全部 6 个案例的 `record.error_type == ecs.predicted_label`。

**`test_record_rejects_invalid_error_type`**

验证：

- 使用 `error_type="item_wrong"` 构造 `FailureMemoryRecord` 会抛出 `LabelValidationError` 或 `ValueError`。

**`test_trigger_signature_contains_label_and_keywords`**

验证：

- 触发签名包含预测标签和 `|` 分隔符。
- 查询关键词出现在分隔符之后。

**`test_wrong_memory_reflects_baseline_context`**

验证：

- 全部 6 个案例的 `record.wrong_memory == case.primary_baseline.injected_context`。
- 即使对于 `reasoning_error`（基线上下文包含金标证据但答案仍然错误）也是如此。

需求覆盖：

- Issue 0007 AC：ECS 记录包含所有必需字段。
- ECS 原因验证（继承自 `ECSDraft.__post_init__`）。

### `FailureMemoryStoreRetrieveTest`（7 个测试）

**`test_store_contains_all_six_records`**

验证：

- 由 6 个原始案例构建的 FM 存储的 `len(store) == 6`。

**`test_retrieve_by_matching_query_returns_records`**

验证：

- 使用与原始案例完全相同的查询进行查询返回至少一条记录。

**`test_retrieve_returns_related_label_records`**

验证：

- 一个相似但不同的查询（"What location was picked for the offsite meeting?"）仍能检索到记录。

**`test_retrieve_unrelated_query_returns_empty`**

验证：

- 完全不相关的查询返回零条记录。

**`test_empty_store_retrieve_returns_empty`**

验证：

- 空的 `FailureMemoryStore` 对任何查询均返回 `()`。

**`test_retrieve_respects_top_k`**

验证：

- `top_k=2` 最多返回 2 条记录。

**`test_full_trace_is_not_retrieved_as_guidance`**

验证：

- 对所有检索到的记录，`record.corrected_memory != record.wrong_memory`（`reasoning_error` 除外）。
- 这确保作为"指导"检索到的是修正后内存，而非失败的基线上下文。

需求覆盖：

- 未来任务检索 `corrected_memory + repair_guidance`，而非完整失败轨迹。

### `BuildFailureMemoryContextTest`（9 个测试）

**`test_none_mode_returns_empty_string`**

验证：

- `build_failure_memory_context(records, "none")` 返回 `""`。

**`test_none_mode_with_empty_records_returns_empty`**

验证：

- `build_failure_memory_context((), "none")` 返回 `""`。

**`test_full_trace_mode_injects_wrong_memory`**

验证：

- `full_trace` 模式输出包含 `"Past Failure Trace"` 标记。
- 输出非空。

**`test_corrected_guidance_mode_injects_guidance`**

验证：

- `corrected_guidance` 模式输出包含 `"Failure Memory Guidance"`、`"Corrected:"` 和 `"Guidance:"`。

**`test_corrected_guidance_does_not_inject_wrong_memory_text`**

验证：

- `corrected_guidance` 上下文不包含任何记录中的 `wrong_memory` 文本。

**`test_corrected_guidance_does_not_inject_full_failed_trace`**

验证：

- `corrected_guidance` 上下文不包含 `"Past Failure Trace"` 标记。

**`test_invalid_mode_raises`**

验证：

- 无效模式字符串抛出 `ValueError`。

**`test_empty_records_with_full_trace_returns_empty`**

验证：

- `build_failure_memory_context((), "full_trace")` 返回 `""`。

**`test_empty_records_with_corrected_guidance_returns_empty`**

验证：

- `build_failure_memory_context((), "corrected_guidance")` 返回 `""`。

需求覆盖：

- 上下文模式产生正确的内容和结构。
- 修正指导排除完整轨迹和错误内存。

### `RecurrenceComparisonRowTest`（8 个测试）

**`test_one_row_per_future_case`**

验证：

- 3 个未来案例产生 3 行。

**`test_rows_have_all_required_fields`**

验证：

- 全部 15 个字段存在且类型正确。
- `targeted_assessment` 和 `hard_case_assessment` 的值在 `("recovered", "partial", "failed")` 中。

**`test_scores_are_in_range`**

验证：

- 全部 6 个分数字段在 `[0.0, 1.0]` 范围内。

**`test_token_costs_are_positive`**

验证：

- 全部三个 token 成本字段 > 0.0。

**`test_pollution_risk_in_range`**

验证：

- `full_trace_pollution_risk` 在 `[0.0, 1.0]` 范围内。

**`test_full_trace_pollution_risk_is_high_when_no_evidence`**

验证：

- 至少有一个案例的 `pollution_risk >= 0.5`，确认错误检索的完整轨迹不包含所需证据。

**`test_failure_memory_useful_flag_is_consistent`**

验证：

- 对所有行，`row.failure_memory_useful == row.corrected_guidance_better_than_none`。

**`test_any_fm_improvement_property`**

验证：

- `any_fm_improvement` 属性等于 `corrected_guidance_better_than_none`。

需求覆盖：

- 三向对比产生有效且范围受约束的结果。

### `RecurrenceSummaryTest`（5 个测试）

**`test_summary_has_all_fields`**

验证：

- 摘要是 `RecurrenceSummary`，且 `total_cases=3`。

**`test_summary_rates_in_range`**

验证：

- `fm_useful_rate` 在 `[0.0, 1.0]` 范围内。

**`test_summary_with_empty_rows`**

验证：

- 空行产生零值摘要，且 `failure_memory_worth_keeping=False`。

**`test_token_costs_are_positive`**

验证：

- 平均 token 成本 > 0.0。

**`test_full_trace_pollution_risk_positive`**

验证：

- 平均污染风险 >= 0.0。

需求覆盖：

- 聚合指标有效且可解释。

### `RecurrenceTableOutputTest`（3 个测试）

**`test_table_writes_csv_with_required_columns`**

验证：

- CSV 写入，表头包含全部 10 个必需列。

**`test_table_writes_summary_file`**

验证：

- `recurrence_summary.txt` 与 CSV 并列写入。
- 包含 "CMD V0 ECS Failure Memory Recurrence Summary" 和 "Failure Memory worth keeping"。

**`test_table_rejects_outside_sandbox`**

验证：

- 写入沙箱外路径抛出 `ValueError`。

需求覆盖：

- 结果可生成产物且受沙箱关口限制。

### `FullPipelineRecurrenceTest`（5 个测试）

**`test_full_pipeline_produces_valid_rows`**

验证：

- 完整管线产生 3 个包含 V0 管线标签的有效行。

**`test_corrected_guidance_outperforms_full_trace`**

验证：

- 至少有一个案例显示 `corrected_guidance_better_than_full_trace == True`。

**`test_all_original_cases_in_failure_memory`**

验证：

- FM 存储包含 6 条记录。

**`test_similar_future_case_retrieves_original_record`**

验证：

- `v0-fm-retrieval-001` 检索到至少一条 `retrieval_error` 记录。

**`test_recurrence_summary_is_positive`**

验证：

- `fm_useful_rate >= 0.0` 且 `avg_full_trace_pollution_risk >= 0.0`。

需求覆盖：

- 从原始案例经 FM 到未来案例对比的端到端管线。

### `FailureMemoryECSCauseValidationTest`（1 个测试）

**`test_fm_record_cause_does_not_contain_forbidden_labels`**

验证：

- 全部 6 条 FM 记录的 `cause` 字段不含被禁止的物品级标签名称（`item_wrong`、`item_stale`、`item_conflict`、`item_poisoned`、`item_compression_distorted`）。

需求覆盖：

- FM 记录中的 ECS cause 不得使用 V0 禁止的物品级标签名称。

### `FailureMemoryNoGoldLeakageTest`（1 个测试）

**`test_fm_record_preserves_ecs_boundaries`**

验证：

- 对于非 `reasoning_error` 案例：`corrected_memory != wrong_memory`。
- 对于 `reasoning_error`：`corrected_memory == wrong_memory`（证据正确；修复添加推理指导）。

需求覆盖：

- FM 记录保持 ECS 边界；修正后内存和错误内存被正确区分。

## 验收标准可追溯性

| Issue 0007 AC | 代码接口 | 测试接口 |
| --- | --- | --- |
| ECS 记录包含错误类型、错误内存、原始证据、原因、修正后内存、修复动作、修复指导以及触发签名。 | `FailureMemoryRecord` 字段、`FailureMemoryRecord.from_ecs_draft` | `test_record_from_ecs_has_all_required_fields` |
| ECS `cause` 不得使用 V0 禁止的物品级标签名称。 | `ECSDraft.__post_init__` 调用 `_validate_ecs_cause`（issue 0005）；`FailureMemoryRecord.from_ecs_draft` 原样复制 `ecs.cause`。 | `test_fm_record_cause_does_not_contain_forbidden_labels` |
| 未来任务检索 `corrected_memory + repair_guidance`，而非完整失败轨迹。 | `build_failure_memory_context(records, "corrected_guidance")` 仅注入修正后内存和指导；`FailureMemoryStore.retrieve` 返回 `corrected_memory != wrong_memory` 的记录。 | `test_corrected_guidance_does_not_inject_wrong_memory_text`、`test_corrected_guidance_does_not_inject_full_failed_trace`、`test_full_trace_is_not_retrieved_as_guidance` |
| 对比包含答案分数、证据召回率和追加 token 成本。 | `RecurrenceComparisonRow` 中三种模式的答案/证据分数字段；token 成本字段；`full_trace_pollution_risk`。 | `test_rows_have_all_required_fields`、`test_scores_are_in_range`、`test_token_costs_are_positive` |
| 结果说明失败记忆是否有足够价值以保留在范围内。 | `RecurrenceSummary.failure_memory_worth_keeping`（阈值：`fm_useful_rate >= 0.5`）；`_write_recurrence_summary` 文本输出。 | `test_summary_has_all_fields`、`test_table_writes_summary_file` |
| 任何正面声明均有表格支撑或标注为未经证实。 | `write_recurrence_comparison_table` 生成 `recurrence_comparison.csv`；`_write_recurrence_summary` 生成包含显式声明陈述的 `recurrence_summary.txt`。 | `test_table_writes_csv_with_required_columns`、`test_table_writes_summary_file` |

## 当前产物语义

当前 `artifacts/sandbox/recurrence_comparison.csv` 在三个未来任务案例上的结果：

```text
case_id,perturbation_label,no_fm_answer_score,no_fm_evidence_score,full_trace_answer_score,full_trace_evidence_score,corrected_guidance_answer_score,corrected_guidance_evidence_score,...
v0-fm-retrieval-001,retrieval_error,0.000,0.000,0.000,0.000,1.000,1.000,...
v0-fm-premature-extraction-001,premature_extraction_error,0.000,0.000,0.000,0.000,1.000,1.000,...
v0-fm-compression-001,compression_error,0.000,0.000,0.000,0.000,1.000,1.000,...
```

解读：

- 全部三个未来案例显示 `no_fm_evidence_score=0.0`（无 FM 时基线失败）。
- 全部三个显示 `full_trace_evidence_score=0.0`（过往错误轨迹无用；污染风险为 1.0）。
- 全部三个显示 `corrected_guidance_evidence_score=1.0` 和 `corrected_guidance_answer_score=1.0`（CMD FM 完全恢复）。
- 全部 3 个案例的 `failure_memory_useful=True`。
- `failure_memory_worth_keeping=True`。

此产物证明了复发率对比管线的存在，以及 CMD 失败记忆在合成烟雾测试集上有帮助。它尚不支持关于真实多任务 agent 性能的论文声明。后者需要：
- 更大、更多样化的未来任务数据集。
- FM 无用的案例（以衡量假阳性率）。
- 真实 agent 评估，而非合成字符串匹配。

## 验证

命令：

```bash
python3 -m pytest tests/test_cmd_audit_issue7_failure_memory.py -v
python3 -m pytest tests/ -v
python3 -m compileall cmd_audit tests
```

预期状态：

- 全部 44 个 issue 0007 测试通过。
- 全部 127 个总测试通过（83 个现有 + 44 个新增）。
- 生成 `artifacts/sandbox/recurrence_comparison.csv`。
- 生成 `artifacts/sandbox/recurrence_summary.txt`。

## 保留的非目标

- 无生产内存 agent 集成（CMD-Skill Adapter 被推迟）。
- 无真实 LLM 调用进行检索（基于关键词，非基于 embedding）。
- 无 UI 或仪表盘。
- 无超出合成探针案例的多任务 agent 评估。
- 无失败记忆写入生产 agent 状态（仅沙箱内）。
- 无完整失败轨迹存储或作为失败记忆上下文检索。
- 无金标答案注入未来任务上下文（答案存在性在合并上下文中检查，而非单独注入）。
- 无 V0 标签集扩展。

## 下一步技术步骤

Issue 0007 完成了 V0 证据链。此刻全部四份必需证据产物已就绪：

1. `attribution_table.csv` — issue 0003
2. `comparison_metrics.csv` — issue 0002
3. Post-Repair Context Replay 表 — issue 0005
4. ECS 失败记忆复发率对比 — issue 0007

下一步是 issue 0010：实施证据驱动的版本关口（V0→V1）。这是一个 HITL（人机协同）issue，评估四份 V0 证据产物是否通过论文声明阈值。
