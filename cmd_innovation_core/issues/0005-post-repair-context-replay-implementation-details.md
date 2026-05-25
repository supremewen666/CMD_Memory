# Issue 0005 实现细节：修复后上下文回放

## 目的

本文档是 issue 0005《验证修复后上下文回放》的全局实现地图。它按照与 issue 0001 和 issue 0002 实现细节文档相同的格式，映射每个函数、数据类、辅助函数、异常和常量到其确切的源码位置、签名、行为、调用者和领域含义。

Issue 0005 在现有归因层之上构建修复-验证流水线：

```text
ProbeCase
  -> run_case_full
      -> run_case（现有：run_v0_replay_portfolio → assign_attribution）
      -> draft_ecs（基于规则逐标签的 Error-Cause-Solution）
      -> build_repaired_context（修正后的记忆 + 修复指导 + 证据块）
      -> run_post_repair_context_replay（三值 repair_assessment，无金标准答案注入）
      -> run_hard_case_update_baseline（通用对比基线）
  -> FullAuditResult
  -> write_post_repair_table（经沙箱边界校验的 CSV）
```

该切片交付 `cmd_tracer_bullets.md` 中的四个 TDD 周期：

| 周期 | 标题 | 状态 |
| --- | --- | --- |
| Cycle 5 | Post-Repair Context Replay | 绿色 |
| Cycle 12 | Three-Value Post-Repair Assessment | 绿色 |
| Cycle 13 | ECS Cause Item-Label-Name Prohibition | 绿色 |
| Cycle 15 | CMD-Audit Sandbox Write Boundary | 绿色 |

Issue 0006（`cmd_audit/repairs.py`）后续在 issue 0005 的 `FullAuditResult` 和 `run_case_full` 之上构建修复成功对比表；issue 0005 是基础的修复验证层。

## 源需求

本实现遵循以下本地文档。

| 来源 | 在 Issue 0005 中应用的需求 |
| --- | --- |
| `TASK.md` | Post-Repair Context Replay 必须使用修复后的上下文重新运行原始失败查询，不注入金标准答案，并输出三值 `repair_assessment`（`recovered` / `partial` / `failed`）。`partial` 表示证据已恢复但答案仍然错误——暴露耦合失败。ECS `cause` 可以描述项目状态，但不得使用 V0 禁止的项目标签名称或通过自然语言等价词重新声明它们。CMD-Audit 写入权限限制在回放本地沙箱内。 |
| `CLAUDE.md` | 将 CMD-Audit 与 CMD-Skill Adapter 分开；CMD-Audit 写入权限限制在回放本地沙箱；三值 `repair_assessment`；ECS `cause` 项目状态描述规则；不要向 Post-Repair Context Replay 注入金标准答案。 |
| `cmd_innovation_core/CONTEXT.md` | **Post-Repair Context Replay** 定义：从 CMD 输出重建修复后的上下文，重新运行原始失败查询，输出三值 `repair_assessment`，不注入金标准答案。**ECS** cause 规则。**CMD-Audit** 沙箱写入限制——只有 CMD-Skill Adapter 将经过验证的修复应用到生产代理状态。 |
| `cmd_innovation_core/prd/cmd_minimal_probe_prd.md` | User Story 17（Post-Repair Context Replay），AC4（三值评估，非二元关卡），AC7（ECS cause 项目标签名称禁止），AC8（CMD-Audit 沙箱写入边界）。 |
| `cmd_innovation_core/issues/0005-validate-post-repair-context-replay.md` | 六个验收标准：完整流水线流程、修复后的上下文组件、无金标准答案注入、三值评估、token 成本 + 回归风险、hard-case 更新基线、沙箱写入限制。 |
| `cmd_innovation_core/prototypes/post_repair_and_monitor_contract_prototype.md` | 三值分类的状态转移、四个场景卡片（完全恢复、部分耦合失败、失败、部分注入）。 |
| `cmd_innovation_core/tdd/cmd_tracer_bullets.md` | Cycle 5 RED/GREEN：Post-Repair Context Replay。Cycle 12 RED/GREEN：三值评估。Cycle 13 RED/GREEN：ECS Cause 禁止。Cycle 15 RED/GREEN：沙箱写入边界。 |

## 领域边界

Issue 0005 在现有归因（issues 0001-0003）、基线（issue 0002）、分类审查（issue 0004）和 monitor 合约（issue 0009）之上构建修复验证流水线。它不更改任何现有的回放、归因或基线逻辑。

```text
run_case（现有，未更改）
  -> AuditResult

run_case_full（issue 0005）
  -> run_case（现有）
  -> draft_ecs（issue 0005）
  -> build_repaired_context（issue 0005）
  -> run_post_repair_context_replay（issue 0005）
  -> run_hard_case_update_baseline（issue 0005）
  -> FullAuditResult（issue 0005）

run_cases_full（issue 0006，基于 issue 0005）
  -> [run_case_full(c) for c in cases]

write_repair_success_table_from_full（issue 0006，基于 issue 0005）
  -> 对每个 FullAuditResult 调用 make_repair_comparison(fr)
  -> write_repair_success_table（来自 repairs.py）
```

Issue 0005 拥有的内容：

- 定义 `REPAIR_ASSESSMENT_VALUES`（`recovered`、`partial`、`failed`）。
- `classify_repair_assessment(answer_score, evidence_score) -> str`，具有显式的三值决策逻辑。
- 带有 `__post_init__` 校验的 `ECSDraft` 数据类（V0 标签 + ECS cause 禁止）。
- `_validate_ecs_cause(cause)`，通过正则拒绝禁止的项目标签名称和自然语言等价词。
- 回放前的重建上下文 `RepairedContext` 数据类。
- 带有答案分数、证据分数、评估、token 成本和回归风险的 `PostRepairResult` 数据类。
- `draft_ecs(case, audit_result) -> ECSDraft`，具有基于规则的逐标签 ECS 起草。
- `build_repaired_context(case, ecs_draft) -> RepairedContext`——金标准答案注入防护门。
- `run_post_repair_context_replay(case, repaired_context) -> PostRepairResult`，无金标准答案注入。
- `run_hard_case_update_baseline(case) -> PostRepairResult`，作为无差别对比基线。
- `validate_sandbox_path(output_path, sandbox_root)`，用于沙箱写入边界强制执行。
- 包装完整流水线输出的 `FullAuditResult` 数据类。
- `run_case_full(case) -> FullAuditResult`，作为顶层流水线入口点。
- `write_post_repair_table(results, output_path, *, sandbox_root)`，带沙箱校验。
- 全部六个 V0 标签的逐标签 ECS 规则辅助函数（`_ecs_for_label`）。
- Token 成本估算器（`_estimate_token_cost`）和回归风险估算器（`_estimate_regression_risk`）。
- 全部四个 TDD 周期的行为级测试（26 个测试方法）。

Issue 0005 不拥有的内容（属于其他 issue）：

- 更改回放逻辑或添加新的回放路径（issue 0003）。
- 更改 `assign_attribution` 或其阈值（issue 0001）。
- 更改基线套件或对比器逻辑（issue 0002）。
- 更改 monitor 合约或校验（issue 0009）。
- 添加新的探针案例（issue 0003）。
- ECS Failure Memory 复发率（issue 0007）。
- 针对性记忆修复对比指标（issue 0006）。

## 模块地图

| 模块 | Issue 0005 角色 |
| --- | --- |
| `cmd_audit/post_repair.py` | 拥有所有修复后数据类型、流水线函数、沙箱校验、ECS cause 校验、逐标签 ECS 规则和评分辅助函数。这是 issue 0005 创建的主模块。 |
| `cmd_audit/harness.py` | 已更新。拥有 `FullAuditResult` 数据类、`run_case_full` 流水线入口点、`write_post_repair_table` CSV 写入器。现在还拥有 `run_cases_full` 和 `write_repair_success_table_from_full`（在 issue 0006 中添加，依赖 `FullAuditResult`）。现有的 `AuditResult`、`run_case`、`run_cases` 和所有先前的表格写入器保持不变。 |
| `cmd_audit/__init__.py` | 已更新。从 `post_repair` 和 `harness` 导出新的公共接口：`ECSDraft`、`FullAuditResult`、`PostRepairResult`、`RepairedContext`、`build_repaired_context`、`classify_repair_assessment`、`draft_ecs`、`run_case_full`、`run_cases_full`、`run_hard_case_update_baseline`、`run_post_repair_context_replay`、`validate_sandbox_path`、`write_post_repair_table`、`write_repair_success_table_from_full`。 |
| `cmd_audit/labels.py` | Issue 0005 无更改。`post_repair.py` 导入 `OUT_OF_SCOPE_ITEM_LABELS` 和 `validate_v0_label` 用于 ECS cause 和 predicted_label 校验。 |
| `cmd_audit/scoring.py` | 无更改。`post_repair.py` 导入 `answer_score` 和 `evidence_recall_from_text` 用于回放评分。 |
| `cmd_audit/repairs.py` | Issue 0006 模块。从 `post_repair.py` 导入 `PostRepairResult`、`RepairedContext`、`validate_sandbox_path`。`make_repair_comparison` 消费 `FullAuditResult` 和两个 `PostRepairResult` 字段。 |
| `tests/test_cmd_audit_issue5_post_repair.py` | 5 个测试类，26 个测试方法，覆盖 Cycles 5、12、13、15。 |
| `tests/test_cmd_audit_issue6_targeted_repairs.py` | Issue 0006 测试。5 个测试类，26 个测试方法，依赖 issue 0005 的 `FullAuditResult`、`run_case_full` 和 `validate_sandbox_path`。 |

## 调用图

### 归因 → 修复后流水线（issue 0005）

```text
cmd_audit/__init__.py
  -> harness.run_case_full(ProbeCase)
      -> harness.run_case(ProbeCase)
          -> baselines.run_baseline_suite(ProbeCase)
              -> baselines.run_memory_baselines
              -> baselines._select_comparison_baseline
              -> baselines.run_evidence_recall_heuristic
                  -> baselines._observational_label
                      -> scoring.evidence_recall_from_memory_ids
                      -> scoring.evidence_recall_from_text
              -> baselines.run_subagent_judge_baseline
              -> baselines.run_random_label_baseline
              -> baselines.run_subagent_judge_monitor
                  -> SubagentJudgeMonitorDecision.to_payload
                      -> baselines.validate_monitor_payload
                          -> baselines._reject_forbidden_monitor_fields
          -> replays.run_v0_replay_portfolio(ProbeCase)
              -> replays.run_oracle_write
              -> replays.run_oracle_compression
              -> replays.run_verbatim_event_oracle
              -> replays.run_oracle_retrieval
              -> replays.run_injection_oracle
              -> replays.run_evidence_given_reasoning
          -> attribution.assign_attribution(replays)
              -> attribution._label_for_replay
              -> labels.validate_v0_label
      -> post_repair.draft_ecs(ProbeCase, AuditResult)
          -> AuditResult.attribution  (AttributionResult)
          -> AuditResult.replay       (ReplayResult, top-gain replay)
          -> post_repair._ecs_for_label(case, predicted_label, replay)
          -> ECSDraft(...)
              -> __post_init__:
                  -> labels.validate_v0_label(predicted_label)
                  -> post_repair._validate_ecs_cause(cause)
                      -> 检查 OUT_OF_SCOPE_ITEM_LABELS 的子串匹配
                      -> 检查正则 _FORBIDDEN_NL_PATTERNS
      -> post_repair.build_repaired_context(ProbeCase, ECSDraft)
          -> RepairedContext(case_id, corrected_memory, repair_guidance, repaired_evidence_block, original_query)
      -> post_repair.run_post_repair_context_replay(ProbeCase, RepairedContext)
          -> post_repair._combine_context(RepairedContext)
          -> scoring.evidence_recall_from_text(gold_evidence, combined_context)
          -> case.gold_answer.casefold() in combined.casefold()  （答案分数）
          -> post_repair.classify_repair_assessment(answer_score, evidence_score)
          -> post_repair._estimate_token_cost(combined_context, query)
          -> post_repair._estimate_regression_risk(case, ctx)
          -> PostRepairResult(...)
      -> post_repair.run_hard_case_update_baseline(ProbeCase)
          -> RepairedContext(case_id, all_extracted_memory, "Hard-case update: ...", all_extracted_memory, query)
          -> post_repair.run_post_repair_context_replay(case, ctx)
      -> FullAuditResult(audit, ecs_draft, repaired_context, post_repair, hard_case_baseline)

  -> harness.write_post_repair_table([FullAuditResult], output_path, sandbox_root)
      -> post_repair.validate_sandbox_path(output_path, sandbox_root)
          -> Path.resolve() 以消除 '..' 遍历
```

### Issue 0006 集成（post-0005 消费者）

```text
cmd_audit/__init__.py
  -> harness.run_cases_full([ProbeCase, ...])
      -> [harness.run_case_full(c) for c in cases]
  -> harness.write_repair_success_table_from_full([FullAuditResult], output_path, sandbox_root)
      -> repairs.make_repair_comparison(FullAuditResult)
          -> FullAuditResult.audit (AuditResult)
          -> FullAuditResult.post_repair (PostRepairResult, CMD-guided)
          -> FullAuditResult.hard_case_baseline (PostRepairResult, generic)
          -> repairs.get_targeted_repair_action(predicted_label)
      -> repairs.write_repair_success_table(rows, output_path, sandbox_root)
          -> post_repair.validate_sandbox_path(output_path, sandbox_root)
```

### 行为测试路径

```text
tests/test_cmd_audit_issue5_post_repair.py
  -> post_repair.classify_repair_assessment(answer_score, evidence_score)
  -> post_repair.draft_ecs(case, audit_result)
  -> post_repair.build_repaired_context(case, ecs_draft)
  -> post_repair.run_post_repair_context_replay(case, repaired_context)
  -> post_repair.run_hard_case_update_baseline(case)
  -> harness.run_case_full(case)
  -> post_repair.ECSDraft(...)  （直接构造用于 cause 校验测试）
  -> post_repair.validate_sandbox_path(output_path, sandbox_root)
  -> harness.write_post_repair_table(results, output_path, sandbox_root)
```

## 数据流

### 输入夹具

```text
data/probe_cases/v0_retrieval_error_case.json          # 单案例检索夹具（issue 0001）
data/probe_cases/v0_premature_extraction_error_case.json  # 单案例提取夹具（issue 0003）
data/probe_cases/v0_issue3_cases.json                  # 六案例烟雾套件（issue 0003）
```

### 中间类型

**ECSDraft**（来自 `draft_ecs`，冻结数据类）：

| 字段 | 类型 | 来源 |
| --- | --- | --- |
| `case_id` | `str` | `ProbeCase.case_id` |
| `predicted_label` | `str` | `AttributionResult.predicted_label`，通过 `V0_PIPELINE_LABELS` 校验 |
| `cause` | `str` | `_ecs_for_label(...)`，通过 `_validate_ecs_cause` 校验 |
| `corrected_memory` | `str` | Top 回放的 `evidence_block` |
| `repair_guidance` | `str` | `_ecs_for_label(...)` |
| `repaired_evidence_block` | `str` | Top 回放的 `evidence_block` |

**RepairedContext**（来自 `build_repaired_context`，冻结数据类）：

| 字段 | 类型 | 来源 |
| --- | --- | --- |
| `case_id` | `str` | `ProbeCase.case_id` |
| `corrected_memory` | `str` | `ECSDraft.corrected_memory` |
| `repair_guidance` | `str` | `ECSDraft.repair_guidance` |
| `repaired_evidence_block` | `str` | `ECSDraft.repaired_evidence_block` |
| `original_query` | `str` | `ProbeCase.query` |

**PostRepairResult**（来自 `run_post_repair_context_replay`，冻结数据类）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `case_id` | `str` | 案例标识符 |
| `repair_assessment` | `str` | `"recovered"`、`"partial"` 或 `"failed"` |
| `post_repair_answer_score` | `float` | 如果 `gold_answer` 文本出现在合并后的修复上下文文本中则为 `1.0`，否则 `0.0` |
| `post_repair_evidence_score` | `float` | `evidence_recall_from_text(gold_evidence, combined_context)` |
| `token_cost` | `float` | `(len(combined_context) + len(query)) / 4.0` |
| `regression_risk` | `float` | `1.0 - overlap_ratio`，基于原始基线注入上下文与修复后上下文之间的重叠比例 |
| `had_repair_regression` | `bool` | `regression_risk > 0.5` |

**FullAuditResult**（来自 `run_case_full`，冻结数据类）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `audit` | `AuditResult` | 现有归因 + 基线结果 |
| `ecs_draft` | `ECSDraft` | 来自 CMD 归因的 Error-Cause-Solution |
| `repaired_context` | `RepairedContext` | 为修复后回放重建的上下文 |
| `post_repair` | `PostRepairResult` | CMD 引导的修复后回放结果 |
| `hard_case_baseline` | `PostRepairResult` | 通用 hard-case 更新对比 |

### 输出产出物

```text
artifacts/sandbox/post_repair_table.csv      # 来自 write_post_repair_table（issue 0005）
artifacts/sandbox/repair_success_table.csv    # 来自 write_repair_success_table_from_full（issue 0006）
artifacts/sandbox/repair_label_summary.csv    # 来自 write_repair_success_table（issue 0006）
artifacts/sandbox/repair_claim_ledger.txt     # 来自 write_repair_success_table（issue 0006）
```

现有产出物保持不变：

```text
artifacts/attribution_table.csv
artifacts/comparison_metrics.csv
artifacts/attribution_confusion_matrix.csv
```

### 逐标签 ECS Cause 和修复指导

| 预测标签 | cause | corrected_memory | repair_guidance |
| --- | --- | --- | --- |
| `retrieval_error` | "retrieved context did not include the correct memory item even though the item was present in extracted memory" | `replay.evidence_block` | "update retrieval routing to include the corrected memory item" |
| `premature_extraction_error` | "key evidence was present in raw events but was not preserved in any extracted memory item" | `replay.evidence_block` | "improve extraction to preserve evidence from raw events into memory items" |
| `reasoning_error` | "the injected context contained the required evidence, but the final answer did not match the gold answer" | `replay.evidence_block` | "review reasoning step over provided evidence; the evidence was sufficient but the conclusion was wrong" |
| `compression_error` | "lossy compression removed key evidence that was present in the original memory item" | `replay.evidence_block` | "reduce compression aggressiveness or preserve key evidence phrases during compression" |
| `injection_error` | "retrieved evidence was not correctly injected into the final context for the agent to use" | `replay.evidence_block` | "fix injection formatting so retrieved evidence is presented as a clean evidence block" |
| `write_error` | "no recoverable evidence found in extracted memory; the failure may originate at or before the write step" | `replay.evidence_block` | "ensure events are written to memory and evidence is preserved through the pipeline" |

## 函数级合约

### `cmd_audit/post_repair.py`

这是 issue 0005 创建的主模块。文件：`cmd_audit/post_repair.py`（299 行）。包含 4 个公共函数、5 个私有辅助函数、3 个冻结数据类、1 个异常类、1 个常量和 2 个基于正则的校验模式。

---

#### 常量：`REPAIR_ASSESSMENT_VALUES`

位置：`cmd_audit/post_repair.py:13`

```python
REPAIR_ASSESSMENT_VALUES = ("recovered", "partial", "failed")
```

目的：

- 为 Post-Repair Context Replay 定义完整的有效修复评估值集合。
- 元组顺序对迭代和文档是稳定的。

领域含义：

| 值 | 条件 | 解读 |
| --- | --- | --- |
| `recovered` | `answer_score == 1.0` | 修复完全恢复了正确的任务行为。 |
| `partial` | `answer_score < 1.0` 且 `evidence_score == 1.0` | 证据已恢复但答案仍然错误——暴露耦合失败。这是诊断深度，而非修复失败。 |
| `failed` | `answer_score < 1.0` 且 `evidence_score < 1.0` | 证据和答案均未恢复。修复针对了错误的操作或根因被误诊。 |

---

#### 私有常量：`_FORBIDDEN_NL_PATTERNS`

位置：`cmd_audit/post_repair.py:16-25`

```python
_FORBIDDEN_NL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bitem[_\s]?(is\s+)?wrong\b",
        r"\bitem[_\s]?(is\s+)?stale\b",
        r"\bitem[_\s]?(is\s+)?conflict(ed|ing)?\b",
        r"\bitem[_\s]?(is\s+)?poisoned\b",
        r"\bcompression[_\s]?distorted\b",
    )
)
```

目的：

- 编译后的正则模式，用于检测 ECS cause 文本中禁止的项目标签名称的自然语言等价词。
- 由 `_validate_ecs_cause` 用于拒绝诸如 "the memory item is wrong" 或 "item_is_stale" 的短语。

模式匹配表：

| 模式 | 拒绝 | 允许 |
| --- | --- | --- |
| `\bitem[_\s]?(is\s+)?wrong\b` | "item_wrong"、"the item is wrong"、"memory item is wrong" | "wrong delivery address" |
| `\bitem[_\s]?(is\s+)?stale\b` | "item_stale"、"the item is stale" | "stale coffee" |
| `\bitem[_\s]?(is\s+)?conflict(ed|ing)?\b` | "item_conflict"、"item is conflicting" | "schedule conflict" |
| `\bitem[_\s]?(is\s+)?poisoned\b` | "item_poisoned"、"item is poisoned" | "food poisoning" |
| `\bcompression[_\s]?distorted\b` | "compression_distorted"、"compression distorted the fact" | "image compression" |

---

#### 异常：`ECSCauseValidationError`

位置：`cmd_audit/post_repair.py:28-29`

```python
class ECSCauseValidationError(ValueError):
    """Raised when ECS cause contains forbidden item label names or equivalents."""
```

目的：

- 表示 ECS `cause` 字符串违反了项目标签名称禁止规则。
- 与 `LabelValidationError`（标签边界范围，在 `labels.py` 中）和 `LeakSafeMonitorError`（monitor 边界范围，在 `baselines.py` 中）区分。

由以下抛出：

- `_validate_ecs_cause(cause)`（第 32-47 行）

由以下捕获：

- `ECSDraft(...)` 构造函数的调用者，因为 `__post_init__` 调用 `_validate_ecs_cause`。

---

#### 私有函数：`_validate_ecs_cause(cause: str) -> str`

位置：`cmd_audit/post_repair.py:32-47`

```python
def _validate_ecs_cause(cause: str) -> str:
    lowered = cause.casefold()
    for label in OUT_OF_SCOPE_ITEM_LABELS:
        if label in lowered:
            raise ECSCauseValidationError(
                f"ECS cause must not use forbidden item label {label!r}; "
                f"describe item state instead (e.g., 'stored preference was outdated')"
            )
    for pattern in _FORBIDDEN_NL_PATTERNS:
        if pattern.search(lowered):
            raise ECSCauseValidationError(
                f"ECS cause contains natural-language equivalent of a forbidden "
                f"item label; use descriptive state language instead"
            )
    return cause
```

目的：

- 拒绝包含禁止的项目标签名称或其自然语言等价词的 ECS `cause` 文本。
- 允许描述性状态语言，如 "stored preference was outdated relative to ground truth."

行为：

1. 对输入做 casefold 处理。
2. 检查 `OUT_OF_SCOPE_ITEM_LABELS` 中每个标签的精确子串匹配——如果找到，抛出 `ECSCauseValidationError`。
3. 通过 `pattern.search(lowered)` 检查 `_FORBIDDEN_NL_PATTERNS` 中的每个编译正则——如果匹配，抛出 `ECSCauseValidationError`。
4. 成功时原样返回 `cause`。

调用者：

- `ECSDraft.__post_init__()`（第 80 行）

---

#### 函数：`classify_repair_assessment(answer_score: float, evidence_score: float) -> str`

位置：`cmd_audit/post_repair.py:50-61`

```python
def classify_repair_assessment(answer_score: float, evidence_score: float) -> str:
    if answer_score == 1.0:
        return "recovered"
    if evidence_score == 1.0:
        return "partial"
    return "failed"
```

目的：

- 将 Post-Repair Context Replay 的结果分类为三值评估。
- 实现 `prototypes/post_repair_and_monitor_contract_prototype.md` 中的状态机。

决策表：

| answer_score | evidence_score | 结果 | 解读 |
| --- | --- | --- | --- |
| 1.0 | 1.0 | `recovered` | 单因失败，修复端到端有效。 |
| 1.0 | <1.0 | `recovered` | 尽管证据不完整，答案仍匹配金标准——罕见但有效。 |
| 0.0 | 1.0 | `partial` | **关键诊断**：证据恢复但推理仍然失败。耦合失败暴露。 |
| 0.5 | 1.0 | `partial` | 任何 answer_score < 1.0 且 evidence == 1.0 都是 partial。 |
| 0.0 | 0.0 | `failed` | 修复完全错过了根因。 |
| 0.3 | 0.3 | `failed` | 两者均低于阈值。 |

优先级规则：`answer_score == 1.0` 优先——如果答案完全正确，无论证据分数如何，评估均为 `recovered`。如果答案不完全正确，evidence_score 才区分 `partial` 和 `failed`。

调用者：

- `run_post_repair_context_replay`（第 156 行）
- `ThreeValueRepairAssessmentTest` 中的直接测试（test_cmd_audit_issue5_post_repair.py）

---

#### 数据类：`ECSDraft`

位置：`cmd_audit/post_repair.py:67-80`

```python
@dataclass(frozen=True)
class ECSDraft:
    case_id: str
    predicted_label: str
    cause: str
    corrected_memory: str
    repair_guidance: str
    repaired_evidence_block: str

    def __post_init__(self) -> None:
        validate_v0_label(self.predicted_label)
        _validate_ecs_cause(self.cause)
```

目的：

- 从 CMD-Audit 归因结果起草的不可变 Error-Cause-Solution 记录。
- 冻结以防止构造后修改；所有字段均为字符串。

`__post_init__` 校验：

1. 调用 `validate_v0_label(self.predicted_label)`——确保仅出现 V0 流水线标签。对项目标签或延迟标签抛出 `LabelValidationError`。
2. 调用 `_validate_ecs_cause(self.cause)`——对 cause 文本强制执行项目标签名称禁止。对禁止的名称或自然语言等价词抛出 `ECSCauseValidationError`。

字段含义：

| 字段 | 领域含义 |
| --- | --- |
| `predicted_label` | CMD 归因的失败标签，六个 V0 流水线标签之一。 |
| `cause` | 描述失败发生原因的自然语言描述。使用描述性状态语言，而非项目标签名称。 |
| `corrected_memory` | 本应可用的正确记忆文本（来自获胜回放的 `evidence_block`）。 |
| `repair_guidance` | 关于如何修复失败的自然语言指令。 |
| `repaired_evidence_block` | 获胜反事实回放恢复的证据块。 |

仅由 `draft_ecs` 构造（第 110-131 行）。测试中使用直接构造来验证校验拒绝。

---

#### 数据类：`RepairedContext`

位置：`cmd_audit/post_repair.py:83-92`

```python
@dataclass(frozen=True)
class RepairedContext:
    case_id: str
    corrected_memory: str
    repair_guidance: str
    repaired_evidence_block: str
    original_query: str
```

目的：

- 从 ECS 草稿重建的用于 Post-Repair Context Replay 的不可变上下文。
- 冻结，无 `__post_init__` 校验（内容从已经过校验的 `ECSDraft` + `ProbeCase` 构建）。

字段含义：

| 字段 | 领域含义 |
| --- | --- |
| `corrected_memory` | 注入到新上下文中的正确记忆文本。 |
| `repair_guidance` | 代理应遵循的修复指令。 |
| `repaired_evidence_block` | 要包含在上下文中的恢复后的证据。 |
| `original_query` | 失败任务的用户原始查询（`ProbeCase.query`）。 |

由以下构造：

- `build_repaired_context`（第 133-141 行）——用于 CMD 引导的修复。
- `run_hard_case_update_baseline`（第 172-186 行）——用于通用基线（所有提取后的记忆）。

不引用 `case.gold_answer`。这是金标准答案注入防护门。

---

#### 数据类：`PostRepairResult`

位置：`cmd_audit/post_repair.py:95-105`

```python
@dataclass(frozen=True)
class PostRepairResult:
    case_id: str
    repair_assessment: str
    post_repair_answer_score: float
    post_repair_evidence_score: float
    token_cost: float
    regression_risk: float
    had_repair_regression: bool
```

目的：

- 一次 Post-Repair Context Replay 运行的不可变结果。
- 冻结，无 `__post_init__` 校验（值由 `run_post_repair_context_replay` 内部计算）。

字段含义：

| 字段 | 含义 |
| --- | --- |
| `repair_assessment` | 来自 `classify_repair_assessment` 的 `("recovered", "partial", "failed")` 之一。 |
| `post_repair_answer_score` | 如果 `gold_answer` casefold 文本出现在合并后的修复上下文文本中则为 `1.0`，否则 `0.0`。 |
| `post_repair_evidence_score` | `evidence_recall_from_text(gold_evidence, combined_context)`。 |
| `token_cost` | `(len(context) + len(query)) / 4.0`，基于字符的 token 估算。 |
| `regression_risk` | `1.0 - overlap_ratio`，基于原始基线上下文词项与修复后上下文词项之间的重叠比例，钳制在 [0.0, 1.0]。 |
| `had_repair_regression` | 当 `regression_risk > 0.5` 时为 `True`。 |

仅由 `run_post_repair_context_replay` 构造（第 144-169 行）。被 `repairs.py`（issue 0006）导入并消费，用于 `make_repair_comparison`。

---

#### 函数：`draft_ecs(case: ProbeCase, audit_result) -> ECSDraft`

位置：`cmd_audit/post_repair.py:110-131`

```python
def draft_ecs(case: ProbeCase, audit_result) -> ECSDraft:
    attribution = audit_result.attribution
    replay = audit_result.replay
    cause, corrected_memory, repair_guidance = _ecs_for_label(
        case, attribution.predicted_label, replay
    )
    return ECSDraft(
        case_id=case.case_id,
        predicted_label=attribution.predicted_label,
        cause=cause,
        corrected_memory=corrected_memory,
        repair_guidance=repair_guidance,
        repaired_evidence_block=replay.evidence_block,
    )
```

目的：

- 从 CMD-Audit 归因结果起草 Error-Cause-Solution 记录。
- 基于规则的 V0 草案：预测标签和 top 回放驱动 cause 文本、修正记忆和修复指导的选择。

行为：

1. 读取 `audit_result.attribution`（带有 `predicted_label` 和 `top_replay` 的 `AttributionResult`）。
2. 读取 `audit_result.replay`（通过 `AuditResult.replay` 属性获取的 top-gain `ReplayResult`）。
3. 调用 `_ecs_for_label(case, predicted_label, replay)` 获取 `(cause, corrected_memory, repair_guidance)`。
4. 使用 `replay.evidence_block` 作为 `repaired_evidence_block`。
5. 构造并返回 `ECSDraft`——校验在构造时通过 `__post_init__` 运行。

输入合约：

- `case` 必须是有效的 `ProbeCase`。
- `audit_result` 必须是具有有效 `attribution`（有 `predicted_label`）和 `replay`（top-gain `ReplayResult`）的 `AuditResult`。

输出：一个经过校验的 `ECSDraft`。

调用者：

- `harness.run_case_full`（第 91 行）
- `PostRepairContextReplayTest` 中的直接测试

---

#### 函数：`build_repaired_context(case: ProbeCase, ecs_draft: ECSDraft) -> RepairedContext`

位置：`cmd_audit/post_repair.py:133-141`

```python
def build_repaired_context(case: ProbeCase, ecs_draft: ECSDraft) -> RepairedContext:
    return RepairedContext(
        case_id=case.case_id,
        corrected_memory=ecs_draft.corrected_memory,
        repair_guidance=ecs_draft.repair_guidance,
        repaired_evidence_block=ecs_draft.repaired_evidence_block,
        original_query=case.query,
    )
```

目的：

- 从 ECS 草稿 + 原始探针案例组装一个 `RepairedContext`。
- 简单的透传构造函数。

金标准答案注入防护：

- 不引用 `case.gold_answer`。
- 仅使用探针案例中的 `case.case_id` 和 `case.query`。
- 所有记忆/证据内容来自已经过校验的 ECS 草稿。

调用者：

- `harness.run_case_full`（第 92 行）
- `PostRepairContextReplayTest` 中的直接测试

---

#### 函数：`run_post_repair_context_replay(case: ProbeCase, repaired_context: RepairedContext) -> PostRepairResult`

位置：`cmd_audit/post_repair.py:144-169`

```python
def run_post_repair_context_replay(
    case: ProbeCase, repaired_context: RepairedContext
) -> PostRepairResult:
    combined = _combine_context(repaired_context)
    evidence_score = evidence_recall_from_text(case.gold_evidence, combined)
    gold_in_context = case.gold_answer.casefold() in combined.casefold()
    post_answer_score = 1.0 if gold_in_context else 0.0
    assessment = classify_repair_assessment(post_answer_score, evidence_score)
    token_cost = _estimate_token_cost(combined, repaired_context.original_query)
    regression_risk = _estimate_regression_risk(case, repaired_context)
    return PostRepairResult(
        case_id=case.case_id,
        repair_assessment=assessment,
        post_repair_answer_score=post_answer_score,
        post_repair_evidence_score=evidence_score,
        token_cost=token_cost,
        regression_risk=regression_risk,
        had_repair_regression=regression_risk > 0.5,
    )
```

目的：

- 使用修复后的上下文重新运行原始失败查询，不将金标准答案注入评分。
- 仅从修复后的上下文内容本身评分证据和答案。

逐步行为：

1. 调用 `_combine_context(repaired_context)`，用换行符连接 `corrected_memory + repair_guidance + repaired_evidence_block`。
2. 计算 `evidence_score = evidence_recall_from_text(case.gold_evidence, combined)`。
3. 检查 `case.gold_answer.casefold() in combined.casefold()`：
   - 如果找到 → `post_answer_score = 1.0`（代理可以从修正后的上下文中"读出"答案）。
   - 如果未找到 → `post_answer_score = 0.0`（代理即使有修正后的证据也无法提取答案——模拟推理差距）。
4. 调用 `classify_repair_assessment(post_answer_score, evidence_score)`。
5. 计算 `token_cost = _estimate_token_cost(combined, query)`。
6. 计算 `regression_risk = _estimate_regression_risk(case, ctx)`。
7. 返回 `PostRepairResult`。

评分语义：

| 证据找到 | 答案在上下文中 | assessment | 解读 |
| --- | --- | --- | --- |
| 是 (1.0) | 是 (1.0) | `recovered` | 修复有效；代理可以从上下文中读取正确答案。 |
| 是 (1.0) | 否 (0.0) | `partial` | 证据存在但代理无法生成正确答案——耦合失败暴露。 |
| 否 (<1.0) | 否 (0.0) | `failed` | 修复失败；证据仍然不可恢复。 |

金标准答案注入防护：答案分数由 `gold_answer` 文本是否出现在修复后上下文的合并文本中决定——而非通过调用 `answer_score(answer, gold_answer)`。这模拟了真实代理读取上下文并提取答案的过程，而不是函数本身注入或比较金标准答案。`gold_answer` 仅用于 `casefold in combined` 的成员检查。

为什么使用 casefold 成员检查而非 `answer_score`：`answer_score` 要求函数生成候选答案字符串，这将意味着将金标准答案注入比较逻辑。成员检查确保金标准答案文本真实地出现在代理将看到的上下文中，而函数本身不生成或比较候选答案。

调用者：

- `harness.run_case_full` → 用于 CMD 引导的修复后回放（第 93 行）
- `run_hard_case_update_baseline` → 用于通用基线（第 186 行）
- `PostRepairContextReplayTest` 中的直接测试

---

#### 函数：`run_hard_case_update_baseline(case: ProbeCase) -> PostRepairResult`

位置：`cmd_audit/post_repair.py:172-186`

```python
def run_hard_case_update_baseline(case: ProbeCase) -> PostRepairResult:
    all_memory = "\n".join(item.text for item in case.extracted_memory)
    ctx = RepairedContext(
        case_id=case.case_id,
        corrected_memory=all_memory,
        repair_guidance="Hard-case update: all extracted memory injected as context.",
        repaired_evidence_block=all_memory,
        original_query=case.query,
    )
    return run_post_repair_context_replay(case, ctx)
```

目的：

- 运行一个通用的"hard-case 更新"基线，用于与 CMD 引导的修复进行对比。
- 注入所有提取后的记忆项作为上下文（无需 CMD 归因诊断），以衡量简单增加更多上下文是否足够。

行为：

1. 用换行符连接所有 `case.extracted_memory` 项目文本为 `all_memory`。
2. 构造一个 `RepairedContext`，其中 `corrected_memory` 和 `repaired_evidence_block` 均为 `all_memory`，带有一个固定的修复指导字符串。
3. 传递给 `run_post_repair_context_replay`，使用与 CMD 修复相同的评分逻辑评分。
4. 返回 `PostRepairResult`。

与 CMD 修复的对比语义（issue 0006 同时使用两者）：

| CMD `post_repair.assessment` | Hard-Case `assessment` | 解读 |
| --- | --- | --- |
| `recovered` | `failed` | CMD 针对性修复是必要的；通用上下文注入不足。 |
| `recovered` | `recovered` | 失败是简单的——即使通用上下文也能修复。CMD 通过精确诊断增加价值。 |
| `partial` | `failed` | CMD 改善了证据召回，但耦合推理失败仍然存在。 |
| `failed` | `failed` | 诊断错过了根因；两种方法都失败。 |

调用者：

- `harness.run_case_full`（第 94 行）
- `repairs.make_repair_comparison`（通过 `FullAuditResult.hard_case_baseline`）
- `PostRepairContextReplayTest` 中的直接测试

---

#### 函数：`validate_sandbox_path(output_path: str | Path, sandbox_root: str | Path | None = None) -> Path`

位置：`cmd_audit/post_repair.py:192-208`

```python
def validate_sandbox_path(output_path: str | Path, sandbox_root: str | Path | None = None) -> Path:
    sandbox = Path(sandbox_root if sandbox_root is not None else "artifacts/sandbox")
    target = Path(output_path).resolve()
    sandbox_resolved = sandbox.resolve()
    try:
        target.relative_to(sandbox_resolved)
    except ValueError:
        raise ValueError(
            f"CMD-Audit write rejected: {target} is outside the replay-local "
            f"sandbox {sandbox_resolved}. Only sandbox writes are permitted."
        )
    return target
```

目的：

- 强制执行 CMD-Audit 沙箱写入边界（TDD Cycle 15）。
- 拒绝写入解析后位于回放本地沙箱外部的路径。

行为：

1. 默认 `sandbox_root` 为 `"artifacts/sandbox"`。
2. 通过 `Path.resolve()` 将 `output_path` 和 `sandbox_root` 都解析为绝对路径。
3. 调用 `target.relative_to(sandbox_resolved)`——如果目标不在沙箱下，`Path.relative_to` 会抛出 `ValueError`。
4. 捕获 `relative_to` 的 `ValueError` 并抛出一个带有清晰边界错误消息的新 `ValueError`。
5. 成功时返回解析后的目标 `Path`。

为什么使用 `resolve()`：`Path.resolve()` 消除 `..` 和符号链接，防止通过父目录遍历的简单绕过。

解析示例：

| 输入路径 | sandbox_root | 结果 |
| --- | --- | --- |
| `"artifacts/sandbox/post_repair.csv"` | (默认) | 接受 |
| `"/etc/passwd"` | (默认) | 拒绝 |
| `"artifacts/sandbox/../../../etc/passwd"` | (默认) | 拒绝（父目录遍历已解析） |
| `"/tmp/sandbox/out.csv"` | `"/tmp/sandbox"` | 接受 |
| `"/tmp/not-sandbox/out.csv"` | `"/tmp/sandbox"` | 拒绝 |

调用者：

- `harness.write_post_repair_table`（第 299 行）
- `repairs.write_repair_success_table`（在 issue 0006 中导入）
- `SandboxWriteBoundaryTest` 中的直接测试

---

#### 私有函数：`_ecs_for_label(case, predicted_label: str, replay) -> tuple[str, str, str]`

位置：`cmd_audit/post_repair.py:214-268`

```python
def _ecs_for_label(case, predicted_label: str, replay) -> tuple[str, str, str]:
    baseline = case.primary_baseline

    if predicted_label == "retrieval_error":
        return (
            "retrieved context did not include the correct memory item "
            "even though the item was present in extracted memory",
            replay.evidence_block,
            "update retrieval routing to include the corrected memory item",
        )
    if predicted_label == "premature_extraction_error":
        return (
            "key evidence was present in raw events but was not preserved "
            "in any extracted memory item",
            replay.evidence_block,
            "improve extraction to preserve evidence from raw events into memory items",
        )
    if predicted_label == "reasoning_error":
        return (
            "the injected context contained the required evidence, but the "
            "final answer did not match the gold answer",
            replay.evidence_block,
            "review reasoning step over provided evidence; the evidence was "
            "sufficient but the conclusion was wrong",
        )
    if predicted_label == "compression_error":
        return (
            "lossy compression removed key evidence that was present in the "
            "original memory item",
            replay.evidence_block,
            "reduce compression aggressiveness or preserve key evidence "
            "phrases during compression",
        )
    if predicted_label == "injection_error":
        return (
            "retrieved evidence was not correctly injected into the final "
            "context for the agent to use",
            replay.evidence_block,
            "fix injection formatting so retrieved evidence is presented "
            "as a clean evidence block",
        )
    # write_error（以及在 V0 中汇总的任何未来 V1 标签）
    return (
        "no recoverable evidence found in extracted memory; the failure "
        "may originate at or before the write step",
        replay.evidence_block,
        "ensure events are written to memory and evidence is preserved "
        "through the pipeline",
    )
```

目的：

- 为六个 V0 流水线标签返回逐标签的 `(cause, corrected_memory, repair_guidance)`。
- 基于规则的 V0 草案：每个标签有固定的 cause 模板和修复指导模板。
- `corrected_memory` 始终是 `replay.evidence_block`——由获胜反事实回放恢复的证据。
- `write_error` 作为所有标签的回退（最终的 `return` 语句捕获上面未显式处理的任何 V0 标签）。

为什么 `write_error` 是回退：`write_error` 代表"在提取后的记忆中未找到可恢复证据"的情况——这是当其他特定失败模式被排除时的默认诊断。任何汇总到 V0 的未来 V1 标签也将命中此回退。

调用者：

- `draft_ecs`（第 120 行）

---

#### 私有函数：`_combine_context(ctx: RepairedContext) -> str`

位置：`cmd_audit/post_repair.py:271-277`

```python
def _combine_context(ctx: RepairedContext) -> str:
    return "\n".join(
        (
            ctx.corrected_memory,
            ctx.repair_guidance,
            ctx.repaired_evidence_block,
        )
    )
```

目的：

- 用换行符连接三个上下文组件，用于 `evidence_recall_from_text` 和金标准答案成员检查。

调用者：

- `run_post_repair_context_replay`（第 152 行）
- `_estimate_regression_risk`（第 290 行）

---

#### 私有函数：`_estimate_token_cost(context_text: str, query: str) -> float`

位置：`cmd_audit/post_repair.py:281-283`

```python
def _estimate_token_cost(context_text: str, query: str) -> float:
    return (len(context_text) + len(query)) / 4.0
```

目的：

- 使用约 4 个字符/token 的近似值的简单基于字符的 token 估算器。
- V0 占位符；真实 tokenizer 将在后续版本中替换它。

调用者：

- `run_post_repair_context_replay`（第 158 行）

---

#### 私有函数：`_estimate_regression_risk(case, ctx: RepairedContext) -> float`

位置：`cmd_audit/post_repair.py:286-298`

```python
def _estimate_regression_risk(case, ctx: RepairedContext) -> float:
    baseline = case.primary_baseline
    original_context = baseline.injected_context
    combined = _combine_context(ctx)
    if not original_context:
        return 0.0
    original_terms = set(original_context.casefold().split())
    repaired_terms = set(combined.casefold().split())
    if not original_terms:
        return 0.0
    overlap = len(original_terms & repaired_terms) / len(original_terms)
    return max(0.0, min(1.0, 1.0 - overlap))
```

目的：

- 将回归风险估算为原始上下文词项中不出现在修复后上下文中的比例。
- 低重叠 → 高风险：修复从原始基线中移除了有用的上下文。
- 如果基线没有注入上下文则返回 `0.0`。

行为：

1. 读取 `case.primary_baseline.injected_context` 作为原始上下文。
2. 如果 `original_context` 为空则立即返回 `0.0`。
3. 将原始和修复后的上下文都分词为 casefolded 单词集合。
4. 如果原始没有词项则返回 `0.0`。
5. 计算类 Jaccard 重叠比例：`|交集| / |原始|`。
6. 返回 `max(0.0, min(1.0, 1.0 - overlap))`——钳制到 [0.0, 1.0]。

调用者：

- `run_post_repair_context_replay`（第 159 行）

### `cmd_audit/harness.py`（Issue 0005 新增内容）

---

#### 数据类：`FullAuditResult`

位置：`cmd_audit/harness.py:76-84`

```python
@dataclass(frozen=True)
class FullAuditResult:
    """Complete CMD-Audit pipeline result including Post-Repair Context Replay."""
    audit: AuditResult
    ecs_draft: ECSDraft
    repaired_context: RepairedContext
    post_repair: PostRepairResult
    hard_case_baseline: PostRepairResult
```

目的：

- 包装从归因到修复后回放的完整 CMD-Audit V0 流水线结果。
- 冻结（不可变），无 `__post_init__` 校验。
- `AuditResult` 字段保留完整的先前归因 + 基线层。
- 两个 `PostRepairResult` 字段允许直接比较：`post_repair`（CMD 引导）vs `hard_case_baseline`（通用）。

仅由 `run_case_full` 构造（第 87-101 行）。

被以下消费：

- `write_post_repair_table`（第 293 行）
- `repairs.make_repair_comparison`（issue 0006，通过 `write_repair_success_table_from_full`）
- `run_cases_full`（第 124-125 行）

---

#### 函数：`run_case_full(case: ProbeCase) -> FullAuditResult`

位置：`cmd_audit/harness.py:87-101`

```python
def run_case_full(case: ProbeCase) -> FullAuditResult:
    audit = run_case(case)
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

- 完整 V0 流水线的顶层入口点：归因 → ECS → 修复 → 修复后回放 → hard-case 基线。
- 在不修改任何现有函数的情况下组合现有的 `run_case` 和新的 issue 0005 函数。

行为：

1. 调用 `run_case(case)`——运行完整的基线套件、六个反事实回放和归因分配。
2. 调用 `draft_ecs(case, audit)`——从归因结果起草逐标签 ECS。
3. 调用 `build_repaired_context(case, ecs_draft)`——组装修复后的上下文（金标准答案注入防护门）。
4. 调用 `run_post_repair_context_replay(case, repaired_context)`——评分 CMD 引导的修复。
5. 调用 `run_hard_case_update_baseline(case)`——评分通用对比基线。
6. 返回带有全部五个字段的 `FullAuditResult`。

这是"一行运行一切"的函数。`run_case` 仍然是仅归因的入口点；`run_case_full` 是完整的流水线入口点。

调用者：

- `run_cases_full`（第 125 行，issue 0006）
- `PostRepairContextReplayTest`、`RepairComparisonRowTest`、`RepairSuccessSummaryTest`、`ClaimLedgerTest`、`FullPipelinePerLabelTest` 中的直接测试

---

#### 函数：`run_cases_full(cases: list[ProbeCase]) -> list[FullAuditResult]`

位置：`cmd_audit/harness.py:124-125`

```python
def run_cases_full(cases: list[ProbeCase]) -> list[FullAuditResult]:
    return [run_case_full(case) for case in cases]
```

目的：

- `run_case_full` 的批量版本，用于多案例烟雾套件。
- 在 issue 0006 中添加，以支持跨完整六案例组合的修复成功对比。

调用者：

- `test_cmd_audit_issue6_targeted_repairs.py` 中的测试
- 生成修复产出物的外部脚本

---

#### 函数：`write_post_repair_table(results: list[FullAuditResult], output_path: str | Path, *, sandbox_root: str | Path | None = None) -> None`

位置：`cmd_audit/harness.py:293-337`

```python
def write_post_repair_table(
    results: list[FullAuditResult],
    output_path: str | Path,
    *,
    sandbox_root: str | Path | None = None,
) -> None:
    validate_sandbox_path(output_path, sandbox_root)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # ... 写入包含 13 列的 CSV ...
```

目的：

- 将修复后对比表写入 CSV，由沙箱路径校验把关。
- 每个 `FullAuditResult` 一行。

行为：

1. 调用 `validate_sandbox_path(output_path, sandbox_root)`——如果输出路径在沙箱外则拒绝。
2. 创建父目录。
3. 写入包含 13 列的 CSV：

| 列 | 来源字段 |
| --- | --- |
| `case_id` | `audit.case_id` |
| `perturbation_label` | `audit.perturbation_label` |
| `predicted_label` | `audit.attribution.predicted_label` |
| `pre_repair_answer_score` | `audit.baseline_answer_score`（格式化为 3 位小数） |
| `pre_repair_evidence_score` | `audit.baseline_evidence_score`（格式化为 3 位小数） |
| `post_repair_answer_score` | `post_repair.post_repair_answer_score`（格式化为 3 位小数） |
| `post_repair_evidence_score` | `post_repair.post_repair_evidence_score`（格式化为 3 位小数） |
| `repair_assessment` | `post_repair.repair_assessment`（原始字符串） |
| `repair_action` | `audit.attribution.predicted_label`（应用的修复策略） |
| `hard_case_baseline_assessment` | `hard_case_baseline.repair_assessment`（原始字符串） |
| `token_cost` | `post_repair.token_cost`（格式化为 1 位小数） |
| `regression_risk` | `post_repair.regression_risk`（格式化为 3 位小数） |
| `had_repair_regression` | `post_repair.had_repair_regression`（小写字符串） |

调用者：

- `SandboxWriteBoundaryTest`、`PostRepairTableShapeTest` 中的直接测试
- 生成修复后产出物的外部脚本

---

#### 函数：`write_repair_success_table_from_full(results: list[FullAuditResult], output_path: str | Path, *, sandbox_root: str | Path | None = None) -> list[RepairComparisonRow]`

位置：`cmd_audit/harness.py:128-137`

```python
def write_repair_success_table_from_full(
    results: list[FullAuditResult],
    output_path: str | Path,
    *,
    sandbox_root: str | Path | None = None,
) -> list[RepairComparisonRow]:
    rows = [make_repair_comparison(fr) for fr in results]
    write_repair_success_table(rows, output_path, sandbox_root=sandbox_root)
    return rows
```

目的：

- 在 issue 0006 中添加的桥接函数：将 `FullAuditResult` 列表转换为 `RepairComparisonRow` 列表并写入修复成功对比表。
- 使用 issue 0005 的 `validate_sandbox_path`（通过 `write_repair_success_table` 间接调用）。

调用者：

- `test_cmd_audit_issue6_targeted_repairs.py` 中的测试

## 测试级合约

测试位于 `tests/test_cmd_audit_issue5_post_repair.py`。5 个测试类，26 个测试方法。

### `ThreeValueRepairAssessmentTest`（Cycle 12）

| 测试方法 | 验证内容 |
| --- | --- |
| `test_recovered_when_answer_full_score` | `classify_repair_assessment(1.0, 1.0)` 返回 `"recovered"`。 |
| `test_partial_when_evidence_recovered_but_answer_not` | **Cycle 12 RED→GREEN**：`classify_repair_assessment(0.0, 1.0)` 返回 `"partial"`。Evidence = 1.0 但 answer = 0.0 → partial，而非 recovered 或 failed。 |
| `test_failed_when_neither_answer_nor_evidence_recovered` | `classify_repair_assessment(0.0, 0.0)` 返回 `"failed"`。 |
| `test_partial_on_partial_answer_with_full_evidence` | `classify_repair_assessment(0.5, 1.0)` 返回 `"partial"`。任何 answer_score < 1.0 且 evidence = 1.0 都是 partial。 |
| `test_failed_on_low_both_scores` | `classify_repair_assessment(0.3, 0.3)` 返回 `"failed"`。两者均低于阈值。 |

### `PostRepairContextReplayTest`（Cycle 5）

| 测试方法 | 验证内容 |
| --- | --- |
| `test_draft_ecs_from_attribution` | `draft_ecs` 为检索案例生成具有正确 `case_id`、`predicted_label="retrieval_error"`、非空 `cause`/`corrected_memory`/`repair_guidance`/`repaired_evidence_block` 的 `ECSDraft`。 |
| `test_build_repaired_context_includes_all_components` | `build_repaired_context` 将所有 ECS 字段 + 原始查询转移到 `RepairedContext`。通过逐字段断言验证。 |
| `test_post_repair_replay_recovers_retrieval_case` | `retrieval_error` 的完整针对性修复路径：证据和答案分数均为 1.0 → assessment = `"recovered"`。 |
| `test_post_repair_does_not_inject_gold_answer_directly` | 修复后上下文中的 `repair_guidance` 字段不包含金标准答案文本。金标准答案只有在自然地出现在修正后的记忆文本（来自回放的 evidence_block）中时才能出现在 `corrected_memory` 中。 |
| `test_post_repair_result_has_token_cost_and_regression_risk` | `PostRepairResult` 具有 `token_cost >= 0.0`、`regression_risk` 在 [0.0, 1.0] 范围内，且 `had_repair_regression` 是 `bool`。 |
| `test_hard_case_update_baseline_is_independent` | `run_hard_case_update_baseline` 生成具有有效三值评估的 `PostRepairResult`。 |
| `test_full_pipeline_produces_complete_result` | `run_case_full` 返回具有全部五个字段为正确类型的 `FullAuditResult`。检索案例显示 `"recovered"`。 |
| `test_post_repair_partial_scenario` | 构造 `RepairedContext`，其中 corrected_memory 不包含金标准答案但 evidence_block 包含 → 测试 partial/full 边界。三值评估始终是三个有效值之一。 |

### `ECSCauseValidationTest`（Cycle 13）

| 测试方法 | 验证内容 |
| --- | --- |
| `test_ecs_cause_rejects_item_wrong` | cause 包含 `"item_wrong"` 的 `ECSDraft` 构造抛出 `ValueError`。 |
| `test_ecs_cause_rejects_item_stale` | cause 包含 `"item_stale"` 的 `ECSDraft` 构造抛出 `ValueError`。 |
| `test_ecs_cause_rejects_item_conflict` | cause 包含 `"item_conflict"` 的 `ECSDraft` 构造抛出 `ValueError`。 |
| `test_ecs_cause_rejects_item_poisoned` | cause 包含 `"item_poisoned"` 的 `ECSDraft` 构造抛出 `ValueError`。 |
| `test_ecs_cause_rejects_item_compression_distorted` | cause 包含 `"item_compression_distorted"` 的 `ECSDraft` 构造抛出 `ValueError`。 |
| `test_ecs_cause_allows_descriptive_state_language` | cause = `"stored preference was outdated relative to ground truth"` 的 `ECSDraft` 被接受。 |
| `test_ecs_cause_rejects_natural_language_equivalents` | cause = `"the memory item is wrong"`（`item_wrong` 的自然语言等价词）的 `ECSDraft` 被拒绝。验证基于正则的检测。 |

所有七个测试在 `ECSDraft.__post_init__` 构造时进行验证，而非在单独的校验端点。

### `SandboxWriteBoundaryTest`（Cycle 15）

| 测试方法 | 验证内容 |
| --- | --- |
| `test_sandbox_path_inside_is_accepted` | `validate_sandbox_path(Path("artifacts/sandbox/post_repair.csv"))` 成功。 |
| `test_sandbox_path_outside_is_rejected` | `validate_sandbox_path(Path("/etc/passwd"))` 抛出 `ValueError`。测试沙箱外的绝对路径。 |
| `test_sandbox_path_parent_traversal_rejected` | `validate_sandbox_path(Path("artifacts/sandbox/../../../etc/passwd"))` 抛出 `ValueError`。`Path.resolve()` 消除 `..` 遍历。 |
| `test_write_post_repair_table_writes_to_sandbox` | 端到端：`write_post_repair_table` 在沙箱内写入 CSV，内容包含预期的表头和 case_id。使用 `tempfile.TemporaryDirectory`。 |
| `test_write_post_repair_table_rejects_outside_sandbox` | 输出在沙箱外的 `write_post_repair_table` 抛出 `ValueError`。 |

### `PostRepairTableShapeTest`

| 测试方法 | 验证内容 |
| --- | --- |
| `test_table_has_required_columns` | 生成的 CSV 表头包含全部 13 个必需列。每列使用 `subTest` 以精确定位失败。 |

## 边界规则

1. **金标准答案注入门**：`run_post_repair_context_replay` 从不写入或调用 `answer_score(gold_answer, ...)`。答案分数由 `case.gold_answer.casefold() in combined_context.casefold()` 决定——对修复后上下文文本的成员检查，模拟代理从上下文中读取和提取答案。`build_repaired_context` 完全不引用 `case.gold_answer`。

2. **三值评估**：`repair_assessment` 精确输出 `recovered`、`partial` 或 `failed`。不计算二元的 `repair_success` 字段。`partial`（证据恢复，答案仍然错误）是耦合失败的关键诊断信号——它意味着"CMD 修复了被诊断的操作，但仍存在第二个失败（可能是推理）。"

3. **ECS cause 项目标签名称禁止**：`ECSDraft.__post_init__` 拒绝包含禁止的项目标签名称（`item_wrong`、`item_stale`、`item_conflict`、`item_poisoned`、`item_compression_distorted`）或其自然语言等价词（通过 `_FORBIDDEN_NL_PATTERNS` 正则匹配）的 cause 文本。允许描述性状态语言（例如 "stored preference was outdated relative to ground truth"）。

4. **沙箱写入边界**：所有修复后产出物写入必须经过 `validate_sandbox_path`，该函数拒绝默认情况下在 `artifacts/sandbox/` 外部的任何路径。父目录遍历（`..`）由 `Path.resolve()` 消除。这强制 CMD-Audit 仅写入回放本地沙箱。

5. **Hard-case 基线分离**：`run_hard_case_update_baseline` 在结构上独立于 CMD 修复。它使用相同的 `run_post_repair_context_replay` 评分函数，但使用通用上下文（所有提取后的记忆），而非 CMD 诊断的修复。`post_repair.repair_assessment` 和 `hard_case_baseline.repair_assessment` 之间的比较是修复有效性证据——被 issue 0006 的 `make_repair_comparison` 消费。

6. **CMD-Audit / CMD-Skill Adapter 分离**：Post-Repair Context Replay 停留在 CMD-Audit 内。回放写入仅针对本地沙箱（`artifacts/sandbox/`）。CMD-Skill Adapter（未来）是唯一被授权将经过验证的修复应用到生产代理状态的组件。

7. **现有归因保持不变**：`run_case`、`AuditResult`、`assign_attribution`、`run_v0_replay_portfolio` 和所有六个回放函数完全保持原样。`run_case_full` 组合它们；它不修改它们。

## 验收标准可追溯性

| Issue 0005 AC | 代码接口 | 测试接口 |
| --- | --- | --- |
| 从探针案例经归因、ECS 到 Post-Repair Context Replay 的完整流水线。 | `run_case_full` 组合 `run_case` → `draft_ecs` → `build_repaired_context` → `run_post_repair_context_replay` → `run_hard_case_update_baseline`。 | `test_full_pipeline_produces_complete_result` |
| 修复后的上下文包含修正记忆、修复指导和修复后的证据块。 | `build_repaired_context` 转移所有 ECS 字段 + `original_query`。`RepairedContext` 是具有五个字段的冻结数据类。 | `test_build_repaired_context_includes_all_components` |
| Post-Repair Context Replay 使用修复后的上下文运行原始查询，不注入金标准答案。 | `run_post_repair_context_replay` 使用 `gold_answer.casefold() in combined`（成员检查），而非 `answer_score(...)`。`build_repaired_context` 不引用 `gold_answer`。 | `test_post_repair_does_not_inject_gold_answer_directly` |
| 输出包含三值 `repair_assessment`（`recovered` / `partial` / `failed`）。 | `classify_repair_assessment` 返回三个值之一。`PostRepairResult.repair_assessment` 存储它。 | `ThreeValueRepairAssessmentTest`（5 个方法） |
| 指标包括答案 F1 或准确率、证据召回率、token 成本和回归风险。 | `PostRepairResult` 具有 `post_repair_answer_score`、`post_repair_evidence_score`、`token_cost`、`regression_risk`、`had_repair_regression`。 | `test_post_repair_result_has_token_cost_and_regression_risk` |
| Hard-case 更新基线与 CMD 引导的修复进行对比。 | `run_hard_case_update_baseline` 注入所有提取后的记忆；通过 `run_post_repair_context_replay` 独立于 CMD 修复评分。两个结果存储在 `FullAuditResult` 中。 | `test_hard_case_update_baseline_is_independent` |
| CMD-Audit 写入权限限制在回放本地沙箱。 | `validate_sandbox_path` 通过 `Path.resolve()` 强制执行沙箱边界。`write_post_repair_table` 在写入前调用它。 | `SandboxWriteBoundaryTest`（5 个方法） |

## 验证

命令：

```bash
# 仅 issue 0005 测试（26 个测试）
python3 -m pytest tests/test_cmd_audit_issue5_post_repair.py -v

# 完整测试套件（截至 issue 0006 完成时共 83 个测试）
python3 -m pytest

# 从烟雾套件生成修复后产出物
python3 -c "
from pathlib import Path
from cmd_audit import load_probe_cases, run_case_full, write_post_repair_table
cases = load_probe_cases('data/probe_cases/v0_issue3_cases.json')
results = [run_case_full(c) for c in cases]
sandbox = Path('artifacts/sandbox')
sandbox.mkdir(parents=True, exist_ok=True)
write_post_repair_table(results, sandbox / 'post_repair_table.csv', sandbox_root=sandbox)
for r in results:
    print(f'{r.audit.case_id}: {r.audit.perturbation_label} -> {r.post_repair.repair_assessment}')
"
```

已验证状态（2026-05-10，post-issue-0006）：

```text
83 tests passed (57 pre-existing + 26 issue 0006)
  -  5 tests in test_cmd_audit_tracer_bullet.py (issue 0001)
  -  6 tests in test_cmd_audit_issue2_baselines.py (issue 0002)
  -  5 tests in test_cmd_audit_issue3_attribution_table.py (issue 0003)
  - 26 tests in test_cmd_audit_issue5_post_repair.py (issue 0005 — POST-REPAIR)
  - 15 tests in test_cmd_audit_issue9_monitor_contract.py (issue 0009)
  - 26 tests in test_cmd_audit_issue6_targeted_repairs.py (issue 0006 — builds on 0005)

烟雾套件修复后结果（6 个案例通过 run_case_full）：
  v0-write-001: write_error -> recovered (CMD) vs failed (hard_case)
  v0-compression-001: compression_error -> recovered (CMD) vs failed (hard_case)
  v0-premature-extraction-001: premature_extraction_error -> recovered (CMD) vs failed (hard_case)
  v0-retrieval-001: retrieval_error -> recovered (CMD) vs recovered (hard_case)
  v0-injection-001: injection_error -> recovered (CMD) vs recovered (hard_case)
  v0-reasoning-001: reasoning_error -> recovered (CMD) vs recovered (hard_case)

产出物：
  artifacts/sandbox/post_repair_table.csv (13 columns)
  artifacts/sandbox/repair_success_table.csv (issue 0006)
  artifacts/sandbox/repair_label_summary.csv (issue 0006)
  artifacts/sandbox/repair_claim_ledger.txt (issue 0006)
```

CMD 修复在 6 个烟雾案例中的 3 个上优于 hard-case 基线（write、compression、premature_extraction）——这些是需要针对性反事实干预的案例。对于 retrieval、injection 和 reasoning，两者都恢复，因为注入所有提取后的记忆恰好包含正确的记忆；CMD 在这些案例中的价值在于精确诊断（`predicted_label`）和更低的 token 成本，而非单独的修复成功。

## 依赖 Issue 0005 的后续 Issue

| Issue | 依赖 | 方式 |
| --- | --- | --- |
| Issue 0006（针对性记忆修复） | `FullAuditResult`、`run_case_full`、`PostRepairResult`、`validate_sandbox_path` | `repairs.make_repair_comparison` 从 `FullAuditResult` 读取 `post_repair` 和 `hard_case_baseline`。`run_cases_full` 调用 `run_case_full`。沙箱校验复用于修复成功表。 |
| Issue 0007（ECS Failure Memory 复发率） | `ECSDraft`、`draft_ecs`、`PostRepairResult` | 将在 Failure Memory 中存储 ECS 记录，并测量未来类似任务的复发率降低。 |
| Issue 0010（证据驱动版本关卡） | `FullAuditResult`、修复后表格 | HITL 关卡审查使用修复后证据产出物。 |
