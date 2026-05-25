# Issue 0004 实现细节：归因分类边界审查

## 目的

本文档是 issue 0004（`审查归因分类边界`）的全局实现地图。

Issue 0004 是首个六重放 V0 归因表生成后的 HITL 关口。其职责是检查烟雾测试套件的输出，挑战每条标签边界，并在进入修复后上下文重放之前确认或修订 V0 分类体系：

```text
artifacts/attribution_table.csv
artifacts/comparison_metrics.csv
artifacts/attribution_confusion_matrix.csv
  -> 逐案例重放增益检查
  -> 混淆/近似混淆模式检测
  -> premature_extraction_error vs retrieval_error 边界复查
  -> Top-2 / 多标签规则审查
  -> Subagent Judge Baseline 与 Monitor 分离审计
  -> 坏内存项排除审计
  -> 延迟标签注册审计
  -> HITL 裁决
```

审查后的切片确认 V0 分类体系在当前烟雾套件中是正确的，并为未来更丰富的探针套件记录边界情况。

## 源头需求

审查遵循以下本地文档。

| 来源 | 在 Issue 0004 中应用的需求 |
| --- | --- |
| `TASK.md` | 检查六例烟雾用例中的混淆或近似混淆；判定 `premature_extraction_error` 是否与 `retrieval_error` 保持区分；明确耦合故障的 Top-2 或多标签规则；将 Subagent Judge Baseline 和 Subagent Judge Monitor 与最终 CMD 归因分离；将 V0 坏内存项排除作为边界规则。 |
| `CLAUDE.md` | 以 `cmd_innovation_core/` 为真源；将 **CMD-Audit** 与 **CMD-Skill Adapter** 分离；仅输出六个 V0 管线标签；不静默扩展分类体系。 |
| `cmd_innovation_core/CONTEXT.md` | 一致使用 **V0 Core Label Set**、**Premature Extraction Error**、**Verbatim Event Oracle**、**Subagent Judge Baseline**、**Subagent Judge Monitor**；遵守所有已标记的歧义。 |
| `cmd_innovation_core/prd/cmd_minimal_probe_prd.md` | 在首张表生成后审查歧义归因案例；确认 V0 分类边界对所有六个标签有效；保持 `CMD-Audit` 与 `CMD-Skill Adapter` 分离；将 `ingestion_error` 注册为延迟标签。 |
| `cmd_innovation_core/issues/0003-counterfactual-attribution-table-implementation-details.md` | 提供 issue 0004 审查所需的 V0 Replay Portfolio、重放-标签映射、归因表结构和混淆矩阵。 |
| `cmd_innovation_core/tdd/cmd_tracer_bullets.md` | Cycle 4（耦合故障）边界为 Top-2 审查提供依据；Cycle 10（坏内存项排除）边界为项目标签排除审计提供依据。 |

## 领域边界

Issue 0004 审查首个归因证据。它不改变标签、重放或归因逻辑。

它的职责范围：
- 检查 `attribution_table.csv` 中逐案例的重放增益列，定位混淆或近似混淆；
- 用 Verbatim Event Oracle 夹具证据复查 `premature_extraction_error` / `retrieval_error` 边界；
- 审查 Top-2 和 `is_ambiguous` 行为对耦合故障的适用性；
- 审计 `comparison_metrics.csv` 中 Subagent Judge Baseline 和 Monitor 与 CMD 归因的分离；
- 审计所有 V0 工件中坏内存项的缺失；
- 将 `ingestion_error` 注册为延迟 V1 标签；
- 审查 grill-session 交叉边界案例（A：Verbatim Event Oracle 和 Oracle Retrieval 均恢复；B：两者均失败但 Oracle Compression 成功）；
- 确认或修订 ECS `cause` 项目状态描述规则；
- 发布 HITL 裁决。

不在其职责范围：
- 修改重放逻辑或新增重放路径；
- 修改归因 `tie_margin` 或 `positive_gain_threshold`；
- 新增探针案例；
- 实现修复后上下文重放；
- 实现 ECS 或 Failure Memory。

## 审查工件

| 工件 | 在 Issue 0004 中的角色 |
| --- | --- |
| `artifacts/attribution_table.csv` | 逐案例重放分数、恢复增益、预测标签、Top-2 标签、歧义标志，以及全部六种 V0 重放的逐重放增益列。 |
| `artifacts/comparison_metrics.csv` | CMD-Audit vs evidence_recall vs subagent_judge vs random_label 的准确率、宏 F1、Top-2 准确率和每次诊断成本。 |
| `artifacts/attribution_confusion_matrix.csv` | 6×6 混淆矩阵，行为黄金标签，列为预测标签。 |
| `data/probe_cases/v0_issue3_cases.json` | 六例烟雾套件：每种 V0 管线标签一例。 |
| `data/probe_cases/v0_premature_extraction_error_case.json` | 聚焦的 Verbatim Event Oracle 边界夹具。 |
| `tests/test_cmd_audit_issue3_attribution_table.py` | V0 Replay Portfolio 和归因表的行为级测试。 |
| `cmd_audit/labels.py` | V0 标签集、重放-标签映射、延迟标签注册、`validate_v0_label`。 |
| `cmd_audit/attribution.py` | `assign_attribution`，`tie_margin=0.05`，`positive_gain_threshold=0.0`。 |
| `cmd_audit/replays.py` | 六个 V0 重放函数及其逐重放恢复逻辑。 |
| `cmd_audit/baselines.py` | 比较器输出和 Subagent Judge Monitor 决策。 |
| `cmd_audit/harness.py` | 归因表写入器、比较指标写入器、混淆矩阵写入器。 |

## 审查方法

对六例烟雾案例逐一检查：

1. **预测标签 vs 扰动标签**：CMD-Audit 是否正确分配标签？
2. **Top 重放增益 vs 次优增益**：是否存在近似混淆（delta < `tie_margin`）？
3. **跨重放增益模式**：在另一种夹具设计下，其他重放是否可能恢复同一案例？
4. **边界完整性**：案例是否遵守 V0 标签边界（无延迟标签，无项目标签）？

对比较器层进行检查：

1. **CMD vs 比较器分离**：CMD-Audit 预测是否独立于 evidence_recall、subagent_judge 和 random_label 计算？
2. **Subagent Judge Monitor 载荷**：Monitor 输出是否保持在防泄漏边界内？

对整体分类体系进行检查：

1. **标签区分度**：是否存在可合并而不损失诊断信号的 V0 标签对？
2. **覆盖缺口**：是否存在六种 V0 标签无法表示的常见内存故障模式？
3. **延迟标签就绪度**：`ingestion_error` 和其他延迟标签是否已正确注册？

## 逐案例边界分析

### 案例 v0-write-001（`write_error`）

**夹具设计**：黄金证据（"Kai chose Madrid for the partner workshop"）没有 `source_memory_id` 也没有 `source_event_id`。原始事件文本是泛化的（"the final city was not written into the memory event stream"）。提取内存是有损的（"Kai discussed a partner workshop location"）。基线检索到有损内存并回答 "Unknown"。

**重放增益模式**：

| 重放 | answer_score | evidence_score | recovery_gain |
| --- | --- | --- | --- |
| `oracle_write` | 1.000 | 1.000 | 1.000 |
| `oracle_compression` | 0.000 | 0.000 | 0.000 |
| `verbatim_event_oracle` | 0.000 | 0.000 | 0.000 |
| `oracle_retrieval` | 0.000 | 0.000 | 0.000 |
| `injection_oracle` | 0.000 | 0.000 | 0.000 |
| `evidence_given_reasoning` | 0.000 | 0.000 | 0.000 |

**边界评估**：干净的单重放恢复。只有 Oracle Write 能够恢复，因为黄金证据没有 `source_memory_id` 或 `source_event_id`——证据从未被写入事件流或提取内存。这是正确的行为：`write_error` 是唯一适用于"证据不存在于任何可恢复形式"的标签。

**边界情况说明**：如果原始事件在上游被截断（证据从未到达 agent），增益模式将完全相同。在 V0 中，这种情况被归入 `write_error`。如果这些情况有不同修复路径，V1 可能拆分出 `ingestion_error`。

### 案例 v0-compression-001（`compression_error`）

**夹具设计**：黄金证据指向 `source_memory_id: "mem-101"`。原始事件包含 "Omar chose Prague for the retention review"。提取内存 `mem-101` 文本为 "Omar chose a Central European city for the retention review"——城市名 "Prague" 在压缩过程中丢失。

**重放增益模式**：

| 重放 | answer_score | evidence_score | recovery_gain |
| --- | --- | --- | --- |
| `oracle_write` | 0.000 | 0.000 | 0.000 |
| `oracle_compression` | 1.000 | 1.000 | 1.000 |
| `verbatim_event_oracle` | 0.000 | 0.000 | 0.000 |
| `oracle_retrieval` | 0.000 | 0.000 | 0.000 |
| `injection_oracle` | 0.000 | 0.000 | 0.000 |
| `evidence_given_reasoning` | 0.000 | 0.000 | 0.000 |

**边界评估**：干净的单重放恢复。Oracle Compression 成功恢复，因为 `source_memory_id` 指向 `mem-101`，且黄金证据文本（"Omar chose Prague..."）与存储文本（"Omar chose a Central European city..."）不同。Oracle Retrieval 的增益为零，因为 Memory Item `mem-101` 不满足 `evidence_recall_from_text` 对所需短语的要求——压缩已经在 Memory Item 内部摧毁了证据。

**为什么不是 `retrieval_error`**：基线检索到了 `mem-101`，但 `mem-101` 的文本不包含 "Prague"。Oracle Retrieval 检查 `evidence_recall_from_text((gold_evidence,), memory_item.text)` 并正确发现证据短语缺失。故障在于 Memory Item 的表示，而非检索。

### 案例 v0-premature-extraction-001（`premature_extraction_error`）

**夹具设计**：黄金证据具有 `source_event_id: "evt-201"` 且无 `source_memory_id`。原始事件 `evt-201` 包含 "Nia chose Berlin for the incident review"。提取内存 `mem-201` 文本为 "Nia selected a European city for the incident review"——具体城市 "Berlin" 在提取过程中丢失。

**重放增益模式**：

| 重放 | answer_score | evidence_score | recovery_gain |
| --- | --- | --- | --- |
| `oracle_write` | 0.000 | 0.000 | 0.000 |
| `oracle_compression` | 0.000 | 0.000 | 0.000 |
| `verbatim_event_oracle` | 1.000 | 1.000 | 1.000 |
| `oracle_retrieval` | 0.000 | 0.000 | 0.000 |
| `injection_oracle` | 0.000 | 0.000 | 0.000 |
| `evidence_given_reasoning` | 0.000 | 0.000 | 0.000 |

**边界评估**：这是最重要的 V0 边界，并且它正确地成立。Verbatim Event Oracle 恢复成功，因为 `source_event_id: "evt-201"` 存在且原始事件文本包含 "Berlin"。Oracle Retrieval 的增益为零，因为 `_recover_extracted_gold_evidence` 跳过了没有 `source_memory_id` 的黄金证据项。该案例无歧义地属于 `premature_extraction_error`。

**为什么不是 `retrieval_error`**：没有任何提取的 Memory Item 保存了 "Berlin"。对已丢失在提取中的证据，基于提取内存的 Oracle Retrieval 无法恢复。`evidence_recall_from_text(gold_evidence, mem-201.text)` 返回 0.0，因为 "Berlin" 不在 "Nia selected a European city for the incident review" 中。

**交叉引用**：`tests/test_cmd_audit_issue3_attribution_table.py` 中的 `test_verbatim_event_oracle_beats_oracle_retrieval_for_extraction_loss` 精确断言了此边界。

### 案例 v0-retrieval-001（`retrieval_error`）

**夹具设计**：黄金证据指向 `source_memory_id: "mem-301"`。提取内存 `mem-301` 正确保存了 "Mira chose Lisbon for the Q3 offsite"。基线 vector_memory 检索到了 `mem-302`（"Porto was considered...but rejected"），一个干扰项。

**重放增益模式**：

| 重放 | answer_score | evidence_score | recovery_gain |
| --- | --- | --- | --- |
| `oracle_write` | 0.000 | 0.000 | 0.000 |
| `oracle_compression` | 0.000 | 0.000 | 0.000 |
| `verbatim_event_oracle` | 0.000 | 0.000 | 0.000 |
| `oracle_retrieval` | 1.000 | 1.000 | 1.000 |
| `injection_oracle` | 0.000 | 0.000 | 0.000 |
| `evidence_given_reasoning` | 0.000 | 0.000 | 0.000 |

**边界评估**：干净的单重放恢复。Oracle Retrieval 恢复 `mem-301` 因为：(a) `source_memory_id` 存在，(b) `mem-301` 不在基线 `retrieved_memory_ids` 中（基线检索到了 `mem-302`），(c) `mem-301.text` 对所需短语满足 `evidence_recall_from_text`。Injection-Oracle 增益为零因为基线未检索到正确的 Memory Item。

### 案例 v0-injection-001（`injection_error`）

**夹具设计**：黄金证据指向 `source_memory_id: "mem-401"`。提取内存 `mem-401` 正确保存了 "Lina chose Oslo for the launch rehearsal"。基线 vector_memory 检索到了 `mem-401`，但 `injected_context` 说 "A launch rehearsal memory was retrieved, but the evidence block omitted the city"——正确的内存被检索到，但上下文注入丢失了证据。

**重放增益模式**：

| 重放 | answer_score | evidence_score | recovery_gain |
| --- | --- | --- | --- |
| `oracle_write` | 0.000 | 0.000 | 0.000 |
| `oracle_compression` | 0.000 | 0.000 | 0.000 |
| `verbatim_event_oracle` | 0.000 | 0.000 | 0.000 |
| `oracle_retrieval` | 0.000 | 0.000 | 0.000 |
| `injection_oracle` | 1.000 | 1.000 | 1.000 |
| `evidence_given_reasoning` | 0.000 | 0.000 | 0.000 |

**边界评估**：干净的单重放恢复。Injection-Oracle 恢复成功因为：(a) `source_memory_id` 存在并指向 `mem-401`，(b) 基线检索到了 `mem-401`（在 `retrieved_memory_ids` 中），(c) 基线 `injected_context` 未召回全部黄金证据（evidence_score 为 0.0）。Oracle Retrieval 增益为零因为 `_recover_extracted_gold_evidence` 跳过了 `source_memory_id` 已在基线 `retrieved_memory_ids` 中的黄金证据项——正确的内存已被检索，因此故障不在检索。

**为什么不是 `retrieval_error`**：基线确实检索到了正确的 Memory Item。故障发生在证据被格式化注入模型上下文时。Injection-Oracle 通过检查检索到的 Memory Item 文本是否包含注入上下文丢失的黄金证据来判断这一点。

### 案例 v0-reasoning-001（`reasoning_error`）

**夹具设计**：黄金证据指向 `source_memory_id: "mem-501"`。提取内存 `mem-501` 正确保存了 "Pavel chose Dublin for the finance sync"。基线 vector_memory 检索到了 `mem-501`，`injected_context` 正确包含 "Pavel chose Dublin for the finance sync"，`evidence_score: 1.0`。但基线答案是 "Cork"——对正确证据的错误推理。

**重放增益模式**：

| 重放 | answer_score | evidence_score | recovery_gain |
| --- | --- | --- | --- |
| `oracle_write` | 0.000 | 0.000 | 0.000 |
| `oracle_compression` | 0.000 | 0.000 | 0.000 |
| `verbatim_event_oracle` | 0.000 | 0.000 | 0.000 |
| `oracle_retrieval` | 0.000 | 0.000 | 0.000 |
| `injection_oracle` | 0.000 | 0.000 | 0.000 |
| `evidence_given_reasoning` | 1.000 | 1.000 | 1.000 |

**边界评估**：干净的单重放恢复。Evidence-Given Reasoning 恢复成功因为：(a) 基线 `injected_context` 已召回全部黄金证据（`evidence_score: 1.0`——"Pavel chose Dublin for the finance sync"包含所有所需短语），(b) `baseline.answer_score < 1.0`（答案是 "Cork" 而非 "Dublin"）。

**为什么不是 `injection_error`**：基线已在上下文中具有正确证据。故障在于对有效证据的最终推理步骤。Evidence-Given Reasoning 使用基线自身的注入上下文作为证据块，确认上下文已充分但答案错误。

## 跨案例模式分析

### 混淆矩阵审计

`artifacts/attribution_confusion_matrix.csv` 显示完美的对角线：

| gold_label | write | compression | premature_extraction | retrieval | injection | reasoning |
| --- | --- | --- | --- | --- | --- | --- |
| write_error | 1 | 0 | 0 | 0 | 0 | 0 |
| compression_error | 0 | 1 | 0 | 0 | 0 | 0 |
| premature_extraction_error | 0 | 0 | 1 | 0 | 0 | 0 |
| retrieval_error | 0 | 0 | 0 | 1 | 0 | 0 |
| injection_error | 0 | 0 | 0 | 0 | 1 | 0 |
| reasoning_error | 0 | 0 | 0 | 0 | 0 | 1 |

**发现**：零非对角线混淆。对于六例烟雾套件（每例设计为恰好触发一条重放路径），这是预期结果。混淆矩阵尚未经受耦合故障或歧义案例的压力测试。

**对 Top-2 行为的启示**：`tie_margin=0.05` 和 `is_ambiguous` 逻辑实现正确但未被当前烟雾套件测试。每例只有一条重放的 gain=1.0，其余均为 0.0——1.0 的差值远超 0.05 的 tie margin。当加入真正耦合故障的更丰富探针案例时（计划于 issue 0005），Top-2 逻辑将被验证。

### 比较器分离审计

`artifacts/comparison_metrics.csv` 确认分离：

| system_name | attribution_accuracy | macro_f1 | top2_accuracy | cost_per_diagnosis |
| --- | --- | --- | --- | --- |
| CMD-Audit | 1.000 | 1.000 | 1.000 | 6.200 |
| evidence_recall | 0.833 | 0.778 | 0.833 | 0.050 |
| subagent_judge | 0.833 | 0.778 | 0.833 | 1.000 |
| random_label | 0.167 | 0.167 | 0.667 | 0.010 |

**发现**：CMD-Audit 在烟雾套件上优于所有比较器（1.0 vs 0.833 宏 F1）。CMD 与 evidence_recall/subagent_judge 之间 0.167 的差距在烟雾套件上是有意义的，因为比较器是观测性的（读取失败轨迹但不进行反事实干预），而 CMD 运行实际的重放干预。这一差距预计会随着具有真正歧义故障签名的更丰富探针案例而扩大。

**Subagent Judge Monitor 分离**：Monitor 的 `to_payload()` 输出包含在 `BaselineSuiteResult.monitor` 中，但绝不会流入 `attribution.predicted_label`。Monitor 决策（`should_trigger_replay`、`risk_score`、`anomaly_reason`、`evidence_pointers`）在结构上与归因结果（`predicted_label`、`top_replay`、`recovery_gain`）分离。`harness.diagnosis_predictions()` 函数从 `result.attribution` 而非 `result.baseline_suite.monitor` 构建 CMD-Audit 预测。

### 坏内存项排除审计

跨所有三个工件验证：

- `attribution_table.csv`：`predicted_label` 列仅包含六种 V0 管线标签。
- `comparison_metrics.csv`：没有项目标签作为系统名称或指标维度出现。
- `attribution_confusion_matrix.csv`：行列标题仅为六种 V0 管线标签。

**发现**：所有 V0 输出工件中均无坏内存项标签（`item_wrong`、`item_stale`、`item_conflict`、`item_poisoned`、`item_compression_distorted`）。`cmd_audit/labels.py` 中的 `validate_v0_label()` 函数在代码边界强制此规则：任何使用项目标签的尝试都会引发 `LabelValidationError`。

## Grill-Session 交叉边界案例

### 边界案例 A：Verbatim Event Oracle 和 Oracle Retrieval 均恢复

**场景**：一个探针案例，其中原始事件包含证据，且提取的 Memory Item 也保存了证据。Verbatim Event Oracle 和 Oracle Retrieval 均产生正向恢复增益。

**裁决规则**：`assign_attribution()` 按 `recovery_gain` 降序排列重放结果。增益最高的重放获胜。如果两者增益均为 1.0，排序顺序中的第一个获胜（稳定排序）。`tie_margin=0.05` 意味着两个标签都会出现在 `top2_labels` 中且 `is_ambiguous=True`。

**评估**：烟雾套件中未观察到（所有案例恰好一条重放 gain=1.0）。当添加更丰富的探针案例时，此边界情况将自然触发 Top-2 逻辑。无需分类更改——增益排序是正确的仲裁方式。

**记录于**：`CONTEXT.md` Flagged Ambiguities。

### 边界案例 B：Verbatim Event Oracle 和 Oracle Retrieval 均失败，Oracle Compression 成功

**场景**：原始事件不直接包含证据（分散在多个事件中），提取的 Memory Item 也未保存。但存在一个 Memory Item，其压缩前文本本应包含证据。Oracle Compression 恢复成功而前两条重放不成功。

**评估**：烟雾套件中未观察到。运行两条无恢复的重放（Verbatim Event Oracle + Oracle Retrieval）之后 Oracle Compression 才找到根因的额外诊断成本为 $2.0（2 × 默认 cost_units 1.0）。这在六重放烟雾套件（总计 $6.2）范围内是可接受的内部设计。无需分类更改。

**记录于**：`CONTEXT.md` Flagged Ambiguities 和 `cmd_innovation_core/knowledge/current-memory.md`。

## 延迟标签注册审计

### `ingestion_error`

**状态**：在三个位置注册为延迟 V1 标签：
- `cmd_innovation_core/CONTEXT.md`：语言章节定义 + 延迟标签列表
- `cmd_innovation_core/prd/cmd_minimal_probe_prd.md`：V0 范围延迟标签 + AC5
- `cmd_audit/labels.py`：`DEFERRED_PIPELINE_LABELS` frozenset

**V0 归入规则**：在 V0 中，证据从未到达 agent 的案例归入 `write_error`。"证据未写入"和"证据从未到达"的反事实干预（Oracle Write）是相同的。如果这些案例被证明有不同的修复路径，V1 可能拆分出 `ingestion_error`。

**验证**：`validate_v0_label("ingestion_error")` 引发 `LabelValidationError`，提示其被延迟到 V1/V2。该标签在 V0 归因或比较器预测中不被接受。

### 其他延迟标签

| 标签 | 状态 |
| --- | --- |
| `granularity_error` | 在 `DEFERRED_PIPELINE_LABELS` 中注册。被 `validate_v0_label` 拒绝。 |
| `route_error` | 在 `DEFERRED_PIPELINE_LABELS` 中注册。被 `validate_v0_label` 拒绝。 |
| `graph_error` | 在 `DEFERRED_PIPELINE_LABELS` 中注册。被 `validate_v0_label` 拒绝。 |
| `safety_error` | 在 `DEFERRED_PIPELINE_LABELS` 中注册。被 `validate_v0_label` 拒绝。 |
| `ingestion_error` | 在 `DEFERRED_PIPELINE_LABELS` 中注册。被 `validate_v0_label` 拒绝。 |

## ECS Cause 项目状态描述规则

**规则**：ECS `cause` 可以用自然语言描述项目状态（例如 "stored preference was outdated relative to ground truth"），但不得使用 V0 禁止的项目标签名称（`item_wrong`、`item_stale`、`item_conflict`、`item_poisoned`、`item_compression_distorted`）或通过自然语言等价语重新声明它们（例如 "the memory item is stale"）。

**状态**：规则已记录在 `CONTEXT.md` Flagged Ambiguities、`PRD` AC7、`current-memory.md` 决策 #10 和 `TASK.md` 边界验收条件中。强制执行将在 issue 0007（ECS Failure Memory）中首次构建 ECS 记录时实施。

## 测试覆盖

| 测试 | 对 Issue 0004 的验证 |
| --- | --- |
| `test_verbatim_event_oracle_beats_oracle_retrieval_for_extraction_loss` | Verbatim Event Oracle 恢复 + Oracle Retrieval 不恢复 = `premature_extraction_error`。最重要的 V0 边界。 |
| `test_raw_event_only_evidence_is_valid_probe_case` | 仅有原始事件的黄金证据（无 `source_memory_id`）作为有效探针案例加载。 |
| `test_issue3_suite_attributes_all_v0_pipeline_labels` | 烟雾套件覆盖全部六种 V0 标签；每种映射到预期的 top 重放。 |
| `test_confusion_matrix_contains_one_diagonal_count_per_v0_label` | 烟雾套件混淆矩阵每种 V0 标签恰有一个对角线计数。 |
| `test_v0_accepts_only_pipeline_labels` | `validate_v0_label` 接受全部六种 V0 管线标签，拒绝项目标签 + 延迟标签。 |
| `test_issue2_baseline_suite_keeps_comparators_separate_from_cmd` | BaselineSuiteResult 比较器在结构上与 CMD 重放归因分离。 |
| `test_monitor_payload_can_trigger_replay_without_forbidden_outputs` | Monitor 载荷触发重放且不包含禁止字段。 |
| `test_monitor_rejects_final_labels_ecs_memory_writes_gold_answers_and_full_traces` | Monitor 拒绝包含禁止字段名的载荷。 |

## HITL 裁决

**日期**：2026-05-09

**决定**：确认 V0 六标签分类体系。无需边界更改。

**证据**：
1. 全部六种 V0 标签在烟雾套件中显示干净的单重放恢复（无混淆、无近似混淆）。
2. `premature_extraction_error` 保持与 `retrieval_error` 的区分——Verbatim Event Oracle 边界已通过夹具和测试验证。
3. Top-2 和 `is_ambiguous` 逻辑实现正确，将在具有耦合故障的更丰富探针案例添加时激活。
4. Subagent Judge Baseline 和 Monitor 在所有工件中与 CMD-Audit 归因保持结构分离。
5. 所有 V0 输出工件中无坏内存项标签。
6. `ingestion_error` 已正确注册为延迟；V0 归入 `write_error` 已记录。
7. Grill-session 交叉边界案例 A 和 B 已记录，判定为无问题。
8. ECS cause 项目状态描述规则已记录，供 issue 0007 强制执行。

**下一步**：进入 issue 0005——验证具有三值 `repair_assessment` 的 Post-Repair Context Replay。

## Issue 0004 之后的剩余工作

Issue 0004 是 Post-Repair Context Replay 之前的 HITL 关口。后续切片：

- Issue 0005：具有三值 `repair_assessment` 的 Post-Repair Context Replay。
- Issue 0006：从归因标签映射的定向内存修复。
- Issue 0007：ECS Failure Memory 复发减少（强制执行 ECS cause 项目标签名称规则）。
- Issue 0010：证据驱动的版本关口（HITL，被 0004/0005/0007 阻塞）。
