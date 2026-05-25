# Issue 0006 实现细节：针对性记忆修复

## 目的

本文档是 issue 0006《验证针对性记忆修复》的全局实现地图。Issue 0006 在 issue 0005 的 Post-Repair Context Replay 流水线之上构建修复-对比层：

```text
ProbeCase
  -> run_case_full (issue 0005)
      -> run_case (现有：归因 + 回放)
      -> draft_ecs (ECS 草案)
      -> build_repaired_context (修复后上下文)
      -> run_post_repair_context_replay (CMD 引导的修复后回放)
      -> run_hard_case_update_baseline (通用 hard-case 基线)
  -> FullAuditResult
  -> make_repair_comparison (issue 0006)
      -> get_targeted_repair_action (按标签的针对性修复操作)
      -> _is_targeted_better (CMD vs hard-case 对比判定)
  -> RepairComparisonRow
  -> compute_repair_success_summary (逐标签聚合)
  -> build_repair_claim_ledger (声明账本)
  -> write_repair_success_table (三个沙箱产出物)
```

核心思想：CMD 归因标签驱动**针对性修复操作**（每个 V0 标签一个），而非将"所有提取后的记忆注入上下文"的通用 hard-case 更新。通过对比 CMD 引导的修复结果与通用基线结果，验证针对性修复是否确实更有效。

## 源需求

本实现遵循以下本地文档。

| 来源 | 在 Issue 0006 中应用的需求 |
| --- | --- |
| `TASK.md` | 使用 CMD 归因标签选择针对性记忆修复，通过 Post-Repair Context Replay 对比这些修复与无差别 hard-case 更新。按标签测量修复成功并在声明就绪的结果表中汇总。 |
| `CLAUDE.md` | 将 CMD-Audit 与 CMD-Skill Adapter 分开；CMD-Audit 写入仅限沙箱；三值 `repair_assessment`；ECS `cause` 项目标签名称禁止。 |
| `cmd_innovation_core/CONTEXT.md` | **Post-Repair Context Replay** 输出三值评估；**ECS** 存储 `corrected_memory + repair_guidance`；**Failure Memory** 是 ECS 记录的存储。 |
| `cmd_innovation_core/prd/cmd_minimal_probe_prd.md` | User Story 16（针对性修复）、User Story 20（声明账本）。 |
| `cmd_innovation_core/issues/0006-validate-targeted-memory-fixes.md` | 六个验收标准：标签到修复映射、CMD 引导 vs 硬案例对比、修复成功基于 Post-Repair Context Replay、指标（答案 F1/准确率、证据召回、token 成本）、逐标签分解、声明账本。 |
| `cmd_innovation_core/tdd/cmd_tracer_bullets.md` | 修复成功对比和声明账本的 TDD 周期。 |

## 领域边界

Issue 0006 在 issue 0005 的完整流水线（`run_case_full` → `FullAuditResult`）之上构建修复对比层。它不更改任何现有的归因、回放或基线逻辑。

```text
issue 0005 (post_repair.py, harness.py)
  -> run_case_full
  -> FullAuditResult
  -> write_post_repair_table

issue 0006 (repairs.py, harness.py 扩展)
  -> make_repair_comparison(FullAuditResult) -> RepairComparisonRow
  -> compute_repair_success_summary(rows) -> dict[label, RepairSuccessLabelSummary]
  -> build_repair_claim_ledger(summaries) -> RepairClaimLedger
  -> write_repair_success_table(rows, path)
  -> harness.write_repair_success_table_from_full(results, path)
```

Issue 0006 拥有的内容：

- `REPAIR_ACTION_BY_LABEL` 字典：全部六个 V0 标签的针对性修复操作定义。
- `TargetedRepairAction` 数据类：修复操作的不可变描述，包含 cause 和 repair_guidance。
- `get_targeted_repair_action(label) -> TargetedRepairAction`：按标签查找修复操作。
- `RepairComparisonRow` 数据类：CMD 引导 vs hard-case 更新的逐案例对比。
- `make_repair_comparison(full_result) -> RepairComparisonRow`：从 `FullAuditResult` 构建对比行。
- `RepairSuccessLabelSummary` 数据类：按标签聚合的修复结果。
- `compute_repair_success_summary(rows) -> dict`：聚合逐标签修复结果。
- `RepairClaimLedger` 数据类：跨标签的声明级证据汇总。
- `build_repair_claim_ledger(summaries) -> RepairClaimLedger`：构建声明账本。
- `write_repair_success_table(rows, path)`：写入三项沙箱产出物。
- `_is_targeted_better(targeted, hard_case) -> bool`：优先级 `recovered > partial > failed`，平局时比较 token 成本。
- `harness.py` 中的 `write_repair_success_table_from_full`：桥接函数，将 `FullAuditResult` 转为对比行并写入。
- `harness.py` 中的 `run_cases_full`：批量运行完整流水线。
- 6 个测试类，26 个测试方法。

Issue 0006 不拥有的内容（属于其他 issue）：

- ECS 草案构建逻辑（issue 0005）。
- Post-Repair Context Replay 评分逻辑（issue 0005）。
- 回放组合或归因分配（issues 0001、0003）。
- ECS Failure Memory 存储与检索（issue 0007）。
- 版本关卡门控（issue 0010）。
- 实际记忆代理集成（CMD-Skill Adapter，V1）。

## 当前代码产出物

| 产出物 | 在 issue 0006 中的角色 |
| --- | --- |
| `cmd_audit/repairs.py` | 主修复模块：修复操作定义、对比逻辑、逐标签汇总、声明账本、CSV 写入器。 |
| `cmd_audit/post_repair.py` | `_ecs_for_label` 从 `repairs.py` 延迟导入以获取 cause/repair_guidance（第 214-219 行）。 |
| `cmd_audit/harness.py` | `FullAuditResult`、`run_case_full`、`run_cases_full`、`write_repair_success_table_from_full`。 |
| `cmd_audit/__init__.py` | 导出全部修复相关公共 API。 |
| `data/probe_cases/v0_issue3_cases.json` | 六案例烟雾套件，用于修复成功对比。 |
| `tests/test_cmd_audit_issue6_targeted_repairs.py` | 6 个测试类，26 个测试方法。 |
| `artifacts/sandbox/repair_success_table.csv` | 生成的逐案例修复对比表。 |
| `artifacts/sandbox/repair_label_summary.csv` | 生成的逐标签修复汇总表。 |
| `artifacts/sandbox/repair_claim_ledger.txt` | 生成的修复声明账本。 |

## 全局模块地图

```text
cmd_audit/__init__.py
  -> harness.run_cases_full([ProbeCase, ...])
      -> harness.run_case_full(case)
          -> harness.run_case(case)
              -> baselines.run_baseline_suite(case)
              -> replays.run_v0_replay_portfolio(case)
              -> attribution.assign_attribution(replays)
          -> post_repair.draft_ecs(case, audit)
              -> post_repair._ecs_for_label(case, label, replay)
                  -> repairs.get_targeted_repair_action(label)  [延迟导入]
          -> post_repair.build_repaired_context(case, ecs_draft)
          -> post_repair.run_post_repair_context_replay(case, ctx)
              -> post_repair.classify_repair_assessment(...)
          -> post_repair.run_hard_case_update_baseline(case)
      -> FullAuditResult
  -> harness.write_repair_success_table_from_full(results, path)
      -> repairs.make_repair_comparison(fr)
          -> repairs.get_targeted_repair_action(label)
          -> repairs._is_targeted_better(targeted, hard_case)
      -> repairs.write_repair_success_table(rows, path)
          -> repairs._write_comparison_csv(rows, path)
          -> repairs._write_label_summary_csv(rows, path)
              -> repairs.compute_repair_success_summary(rows)
          -> repairs._write_claim_ledger(rows, path)
              -> repairs.compute_repair_success_summary(rows)
              -> repairs.build_repair_claim_ledger(summaries)
```

领域解读：

- `repairs.py` 是 issue 0006 的主模块：拥有 **TargetedRepairAction** 作为 cause/repair_guidance 的权威源。
- `post_repair.py:_ecs_for_label` 从 `repairs.py` 延迟导入 `get_targeted_repair_action`，以避免循环依赖。ECS 草案的 cause 和 repair_guidance 统一来源于 `TargetedRepairAction`，而非内联模板。
- `harness.py` 拥有 `FullAuditResult` 和 `run_case_full`，将完整流水线结果提供给修复对比。
- `write_repair_success_table_from_full` 是面向 CLI/脚本的主要入口点。

### 跨模块循环依赖

`post_repair.py` 中的 `_ecs_for_label` 使用延迟导入（函数内 `from .repairs import get_targeted_repair_action`）以避免与 `repairs.py`（它从 `post_repair.py` 导入 `PostRepairResult`）的循环依赖。这使 `RepairActionByLabel` 成为所有 cause/repair_guidance 模板的权威源——ECS 草案和修复对比逻辑都从同一字典读取，保证一致性。

## 数据流

### 输入

```text
data/probe_cases/v0_issue3_cases.json  # 六案例烟雾套件（issue 0003）
```

### 中间类型

**TargetedRepairAction**（`repairs.py:13-24`，冻结数据类）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `label` | `str` | 关联的 V0 流水线标签。 |
| `action_name` | `str` | 面向用户的修复名称。 |
| `description` | `str` | 修复操作的一句话描述。 |
| `intervention_summary` | `str` | 该修复对应回放干预的简要说明。 |
| `cause` | `str` | 英文描述的失败原因。由 `draft_ecs` 消费以构建 ECS 记录。 |
| `repair_guidance` | `str` | 给代理的修复指令。由 `draft_ecs` 消费以构建 ECS 记录。 |

**RepairComparisonRow**（`repairs.py:94-112`，冻结数据类）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `case_id` | `str` | 案例标识符。 |
| `perturbation_label` | `str` | 已知的合成失败原因。 |
| `predicted_label` | `str` | CMD 归因的标签。 |
| `repair_action` | `str` | 应用的修复操作名称。 |
| `pre_repair_answer_score` | `float` | 修复前的答案分数。 |
| `pre_repair_evidence_score` | `float` | 修复前的证据分数。 |
| `targeted_assessment` | `str` | CMD 引导修复的三值评估。 |
| `targeted_answer_score` | `float` | CMD 修复后的答案分数。 |
| `targeted_evidence_score` | `float` | CMD 修复后的证据分数。 |
| `targeted_token_cost` | `float` | CMD 修复上下文的 token 成本。 |
| `hard_case_assessment` | `str` | Hard-case 基线的三值评估。 |
| `hard_case_answer_score` | `float` | Hard-case 基线后的答案分数。 |
| `hard_case_evidence_score` | `float` | Hard-case 基线后的证据分数。 |
| `hard_case_token_cost` | `float` | Hard-case 基线上下文的 token 成本。 |
| `targeted_better` | `bool` | CMD 的针对性修复是否优于 hard-case。 |

**RepairSuccessLabelSummary**（`repairs.py:143-174`，冻结数据类）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `label` | `str` | 归因标签。 |
| `total_cases` | `int` | 该标签的案例总数。 |
| `targeted_recovered` / `targeted_partial` / `targeted_failed` | `int` | CMD 引导修复的评估计数。 |
| `hard_case_recovered` / `hard_case_partial` / `hard_case_failed` | `int` | Hard-case 基线的评估计数。 |
| `targeted_better_count` | `int` | CMD 严格更好的案例数。 |
| `hard_case_better_count` | `int` | Hard-case 基线更好的案例数。 |
| `same_outcome_count` | `int` | 同等结果的案例数。 |
| `avg_targeted_token_cost` | `float` | CMD 修复的平均 token 成本。 |
| `avg_hard_case_token_cost` | `float` | Hard-case 基线的平均 token 成本。 |

派生属性：`targeted_recovery_rate`、`hard_case_recovery_rate`、`targeted_any_recovery_rate`、`hard_case_any_recovery_rate`。

**RepairClaimLedger**（`repairs.py:220-230`，冻结数据类）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `total_cases` | `int` | 汇总的案例总数。 |
| `targeted_recovery_rate` | `float` | CMD 完全恢复率。 |
| `hard_case_recovery_rate` | `float` | Hard-case 完全恢复率。 |
| `targeted_full_plus_partial_rate` | `float` | CMD 宽松恢复率（recovered + partial）。 |
| `hard_case_full_plus_partial_rate` | `float` | Hard-case 宽松恢复率。 |
| `targeted_better_pct` | `float` | CMD 严格更好的案例比例。 |
| `avg_targeted_token_saving_pct` | `float` | CMD 相对 hard-case 的平均 token 节省比例。 |
| `claim_supported` | `bool` | 声明是否被证据支持。 |

### 输出产出物

```text
artifacts/sandbox/repair_success_table.csv      # 15 列逐案例对比
artifacts/sandbox/repair_label_summary.csv       # 15 列逐标签汇总
artifacts/sandbox/repair_claim_ledger.txt        # 人类可读的声明账本
```

### 逐标签修复操作定义

| 标签 | action_name | 核心干预 |
| --- | --- | --- |
| `write_error` | "Oracle Write Repair" | 将从未写入的金标准证据直接注入记忆。 |
| `compression_error` | "Oracle Compression Repair" | 用保留证据的表示替换有损压缩的记忆。 |
| `premature_extraction_error` | "Verbatim Event Repair" | 在原始事件证据被抽象掉之前提取到新记忆项。 |
| `retrieval_error` | "Oracle Retrieval Repair" | 确保正确的记忆项被检索并在上下文中呈现。 |
| `injection_error` | "Injection Oracle Repair" | 将检索到的证据重新格式化为干净、结构化的证据块。 |
| `reasoning_error` | "Evidence-Given Reasoning Repair" | 以带明确推理指导的结构化块呈现证据。 |

所有六个修复操作的 `cause` 和 `repair_guidance` 文本来源于 `REPAIR_ACTION_BY_LABEL` 字典（第 26-85 行），该字典是 cause/repair_guidance 的权威源，同时被 `post_repair.py:_ecs_for_label` 和 `repairs.py:make_repair_comparison` 消费。

## 函数级合约

### `cmd_audit/repairs.py`

这是 issue 0006 创建的主模块。文件：`cmd_audit/repairs.py`（446 行）。包含 4 个公共函数、3 个私有辅助函数、4 个冻结数据类、1 个字典常量。

---

#### 常量：`REPAIR_ACTION_BY_LABEL`

位置：`cmd_audit/repairs.py:26-85`

```python
REPAIR_ACTION_BY_LABEL: dict[str, TargetedRepairAction] = { ... }
```

目的：

- 将全部六个 V0 流水线标签映射到其针对性修复操作。
- 是 cause 文本和 repair_guidance 文本的**权威源**——同时被 `post_repair.py:_ecs_for_label`（通过延迟导入）和 `repairs.py:make_repair_comparison` 消费。

每个条目包含：`label`、`action_name`、`description`、`intervention_summary`、`cause`、`repair_guidance`。

`__post_init__` 校验：每个 `TargetedRepairAction` 构造时通过 `validate_v0_label(label)` 校验标签。

被以下使用：

- `get_targeted_repair_action(label)`（第 88-91 行）
- `post_repair._ecs_for_label`（第 216-219 行，延迟导入）
- 测试：`LabelToRepairMappingTest`、`RepairComparisonRowTest`

---

#### 数据类：`TargetedRepairAction`

位置：`cmd_audit/repairs.py:13-24`

```python
@dataclass(frozen=True)
class TargetedRepairAction:
    label: str
    action_name: str
    description: str
    intervention_summary: str
    cause: str = ""
    repair_guidance: str = ""

    def __post_init__(self) -> None:
        validate_v0_label(self.label)
```

目的：

- 定义一个与单个 V0 标签绑定的针对性修复操作的不可变描述。
- `cause` 和 `repair_guidance` 存储英文描述文本，供 ECS 草案构建和修复对比使用。

字段含义：

| 字段 | 领域含义 |
| --- | --- |
| `label` | 该修复操作所针对的 V0 流水线标签。 |
| `action_name` | 面向用户的修复名称（例如 `"Oracle Write Repair"`）。 |
| `description` | 修复功能的一句话描述。 |
| `intervention_summary` | 与该修复对应的反事实回放干预的简要说明。 |
| `cause` | 失败原因的自然语言描述。由 `draft_ecs` 用于填充 `ECSDraft.cause`。 |
| `repair_guidance` | 修复指令。由 `draft_ecs` 用于填充 `ECSDraft.repair_guidance`。 |

---

#### 函数：`get_targeted_repair_action(label: str) -> TargetedRepairAction`

位置：`cmd_audit/repairs.py:88-91`

```python
def get_targeted_repair_action(label: str) -> TargetedRepairAction:
    validate_v0_label(label)
    return REPAIR_ACTION_BY_LABEL[label]
```

目的：

- 按 V0 标签查找针对性修复操作。
- 先校验标签，再返回对应操作。

调用者：

- `post_repair._ecs_for_label`（延迟导入）——获取 cause 和 repair_guidance。
- `repairs.make_repair_comparison`——获取 `action_name` 用于对比行。
- 测试：全部六个标签的映射验证。

---

#### 数据类：`RepairComparisonRow`

位置：`cmd_audit/repairs.py:94-112`

```python
@dataclass(frozen=True)
class RepairComparisonRow:
    case_id: str
    perturbation_label: str
    predicted_label: str
    repair_action: str
    pre_repair_answer_score: float
    pre_repair_evidence_score: float
    targeted_assessment: str
    targeted_answer_score: float
    targeted_evidence_score: float
    targeted_token_cost: float
    hard_case_assessment: str
    hard_case_answer_score: float
    hard_case_evidence_score: float
    hard_case_token_cost: float
    targeted_better: bool
```

目的：

- 单个案例中 CMD 引导的针对性修复结果 vs 无差别 hard-case 更新结果的不可变对比行。
- 这是修复对比表（`repair_success_table.csv`）的行级记录。

`targeted_better` 由 `_is_targeted_better` 根据评估排名（`recovered > partial > failed`）和 token 成本（平局时更低的胜出）设置。

仅由 `make_repair_comparison` 构造（第 115-140 行）。

---

#### 函数：`make_repair_comparison(full_result) -> RepairComparisonRow`

位置：`cmd_audit/repairs.py:115-140`

```python
def make_repair_comparison(full_result) -> RepairComparisonRow:
    audit = full_result.audit
    targeted = full_result.post_repair
    hard_case = full_result.hard_case_baseline
    repair_action = get_targeted_repair_action(audit.attribution.predicted_label)
    targeted_better = _is_targeted_better(targeted, hard_case)
    return RepairComparisonRow(...)
```

目的：

- 从 `FullAuditResult` 构建一个 `RepairComparisonRow`。
- 从 `audit.attribution.predicted_label` 获取修复操作名称。
- 通过 `_is_targeted_better` 判定 CMD 是否优于 hard-case。

行为：

1. 从 `full_result` 提取 `audit`、`post_repair`（CMD 引导）、`hard_case_baseline`（通用基线）。
2. 调用 `get_targeted_repair_action(audit.attribution.predicted_label)` 获取修复操作名称。
3. 调用 `_is_targeted_better(targeted, hard_case)` 判定优劣。
4. 返回 `RepairComparisonRow`，包含全部 15 个字段。

调用者：

- `harness.write_repair_success_table_from_full`（第 129 行）
- 测试：`RepairComparisonRowTest`、`RepairSuccessSummaryTest`、`ClaimLedgerTest`

---

#### 数据类：`RepairSuccessLabelSummary`

位置：`cmd_audit/repairs.py:143-174`

```python
@dataclass(frozen=True)
class RepairSuccessLabelSummary:
    label: str
    total_cases: int
    targeted_recovered: int
    targeted_partial: int
    targeted_failed: int
    hard_case_recovered: int
    hard_case_partial: int
    hard_case_failed: int
    targeted_better_count: int
    hard_case_better_count: int
    same_outcome_count: int
    avg_targeted_token_cost: float
    avg_hard_case_token_cost: float
```

目的：

- 单个 V0 标签的按标签聚合修复结果。
- 包含计数（recovered/partial/failed）、优劣统计和平均 token 成本。

派生属性（第 160-174 行）：

| 属性 | 计算方式 | 含义 |
| --- | --- | --- |
| `targeted_recovery_rate` | `recovered / total` | CMD 完全恢复率。 |
| `hard_case_recovery_rate` | `recovered / total` | Hard-case 完全恢复率。 |
| `targeted_any_recovery_rate` | `(recovered + partial) / total` | CMD 宽松恢复率。 |
| `hard_case_any_recovery_rate` | `(recovered + partial) / total` | Hard-case 宽松恢复率。 |

仅由 `compute_repair_success_summary` 构造（第 177-217 行）。

---

#### 函数：`compute_repair_success_summary(rows: list[RepairComparisonRow]) -> dict[str, RepairSuccessLabelSummary]`

位置：`cmd_audit/repairs.py:177-217`

```python
def compute_repair_success_summary(rows: list[RepairComparisonRow]) -> dict[str, RepairSuccessLabelSummary]:
```

目的：

- 按 `perturbation_label` 聚合 `RepairComparisonRow` 列表，生成每个标签的汇总统计。

行为：

1. 按 `V0_PIPELINE_LABEL_ORDER` 顺序初始化空白分组。
2. 按 `perturbation_label` 分组行数据。
3. 对每个有案例的标签，统计 recovered/partial/failed 的针对性结果和 hard-case 结果。
4. 统计 `targeted_better_count`、`hard_case_better_count`、`same_outcome_count`。
5. 计算平均 token 成本。
6. 返回标签到 `RepairSuccessLabelSummary` 的字典（跳过零案例的标签）。

调用者：

- `_write_label_summary_csv`、`_write_claim_ledger`（`repairs.py` 内部）
- 测试：`RepairSuccessSummaryTest`、`ClaimLedgerTest`

---

#### 数据类：`RepairClaimLedger`

位置：`cmd_audit/repairs.py:220-230`

```python
@dataclass(frozen=True)
class RepairClaimLedger:
    total_cases: int
    targeted_recovery_rate: float
    hard_case_recovery_rate: float
    targeted_full_plus_partial_rate: float
    hard_case_full_plus_partial_rate: float
    targeted_better_pct: float
    avg_targeted_token_saving_pct: float
    claim_supported: bool
```

目的：

- 跨所有标签汇总的顶层声明证据。回答"CMD 引导的针对性修复是否比无差别 hard-case 更新更好？"

---

#### 函数：`build_repair_claim_ledger(summaries: dict[str, RepairSuccessLabelSummary]) -> RepairClaimLedger`

位置：`cmd_audit/repairs.py:233-282`

```python
def build_repair_claim_ledger(summaries: dict[str, RepairSuccessLabelSummary]) -> RepairClaimLedger:
```

目的：

- 从逐标签汇总构建聚合声明账本。

行为：

1. 如果输入为空，返回全零的 `RepairClaimLedger`，`claim_supported=False`。
2. 汇总所有标签的 recovered 计数、宽松恢复计数、better 计数和加权 token 成本。
3. 计算所有速率和百分比。
4. 应用声明支持判据（第 269-271 行）：

```python
claim_supported = (
    t_any_rate >= h_any_rate and token_saving > 0.0
) or t_rate > h_rate
```

**声明支持判据**（成本敏感的优势支持）：CMD 只有在"完全恢复率严格更高"，或者"宽松恢复率不低且 token 成本更低"时，声明才被视为得到支持。这意味着：
- 如果 CMD 宽松恢复率（recovered+partial）不低于 hard-case 且使用了更少的 token → 支持。
- 如果 CMD 完全恢复率（recovered 单独）严格高于 hard-case → 支持（无论 token 成本如何）。
- 如果宽松恢复率更高但 token 成本也更高 → 不支持。

调用者：

- `_write_claim_ledger`（`repairs.py` 内部）
- 测试：`ClaimLedgerTest`

---

#### 函数：`write_repair_success_table(rows, output_path, *, sandbox_root=None) -> None`

位置：`cmd_audit/repairs.py:285-295`

```python
def write_repair_success_table(
    rows: list[RepairComparisonRow],
    output_path: str | Path,
    *,
    sandbox_root: str | Path | None = None,
) -> None:
```

目的：

- 写入三项沙箱产出物：逐案例对比 CSV、逐标签汇总 CSV 和人类可读声明账本。

行为：

1. 调用 `_write_comparison_csv(rows, output, sandbox_root)` 写入主对比表。
2. 调用 `_write_label_summary_csv(rows, output.parent / "repair_label_summary.csv")` 写入逐标签汇总。
3. 调用 `_write_claim_ledger(rows, output.parent / "repair_claim_ledger.txt")` 写入声明账本。

调用者：

- `harness.write_repair_success_table_from_full`（第 130 行）
- 测试：`RepairSuccessTableTest`

---

#### 私有函数：`_write_comparison_csv(rows, path, *, sandbox_root=None) -> None`

位置：`cmd_audit/repairs.py:298-341`

目的：

- 将 `RepairComparisonRow` 字段写入 15 列 CSV。数值分数格式化为 3 位小数（token 成本为 1 位小数）。`targeted_better` 写入为小写布尔字符串。

CSV 列：`case_id`、`perturbation_label`、`predicted_label`、`repair_action`、`pre_repair_answer_score`、`pre_repair_evidence_score`、`targeted_assessment`、`targeted_answer_score`、`targeted_evidence_score`、`targeted_token_cost`、`hard_case_assessment`、`hard_case_answer_score`、`hard_case_evidence_score`、`hard_case_token_cost`、`targeted_better`。

---

#### 私有函数：`_write_label_summary_csv(rows, path) -> None`

位置：`cmd_audit/repairs.py:344-385`

目的：

- 调用 `compute_repair_success_summary(rows)`，按 `V0_PIPELINE_LABEL_ORDER` 顺序写入 15 列逐标签汇总 CSV。

---

#### 私有函数：`_write_claim_ledger(rows, path) -> None`

位置：`cmd_audit/repairs.py:388-429`

目的：

- 调用 `compute_repair_success_summary(rows)` 然后 `build_repair_claim_ledger(summaries)`。
- 写入人类可读文本文件，包含标题 `"CMD V0 Repair Claim Ledger — Issue 0006"`、聚合指标和每个标签的详细信息。

---

#### 私有函数：`_is_targeted_better(targeted: PostRepairResult, hard_case: PostRepairResult) -> bool`

位置：`cmd_audit/repairs.py:432-445`

```python
def _is_targeted_better(targeted: PostRepairResult, hard_case: PostRepairResult) -> bool:
    order = {"recovered": 0, "partial": 1, "failed": 2}
    t_rank = order[targeted.repair_assessment]
    h_rank = order[hard_case.repair_assessment]
    if t_rank < h_rank:
        return True
    if t_rank > h_rank:
        return False
    return targeted.token_cost < hard_case.token_cost
```

目的：

- 判定 CMD 引导的针对性修复是否优于无差别 hard-case 更新。

行为：

1. 映射评估值为排名：`recovered=0 > partial=1 > failed=2`。
2. 如果 CMD 排名更低（更好）→ `True`。
3. 如果 hard-case 排名更低 → `False`。
4. 排名相同时：CMD token 成本更低 → `True`。

调用者：

- `make_repair_comparison`（第 122 行）

### `cmd_audit/harness.py`（Issue 0006 新增/相关部分）

---

#### 数据类：`FullAuditResult`

位置：`cmd_audit/harness.py:70-78`

```python
@dataclass(frozen=True)
class FullAuditResult:
    audit: AuditResult
    ecs_draft: ECSDraft
    repaired_context: RepairedContext
    post_repair: PostRepairResult
    hard_case_baseline: PostRepairResult
```

目的：

- 包装完整 CMD-Audit V0 流水线结果，从归因到修复后回放。
- 两个 `PostRepairResult` 字段（`post_repair` = CMD 引导，`hard_case_baseline` = 通用）是 `make_repair_comparison` 的输入。

仅由 `run_case_full` 构造（第 81-94 行）。被 `make_repair_comparison` 和 `write_repair_success_table_from_full` 消费。

---

#### 函数：`run_case_full(case: ProbeCase) -> FullAuditResult`

位置：`cmd_audit/harness.py:81-94`

目的：

- 完整 V0 流水线的顶层入口点：归因 → ECS → 修复 → 修复后回放 → hard-case 基线。
- 在不修改任何现有函数的情况下组合 `run_case` 和 issue 0005 的函数。

行为：

1. `run_case(case)` → `AuditResult`
2. `draft_ecs(case, audit)` → `ECSDraft`
3. `build_repaired_context(case, ecs_draft)` → `RepairedContext`
4. `run_post_repair_context_replay(case, ctx)` → `PostRepairResult`（CMD 引导）
5. `run_hard_case_update_baseline(case)` → `PostRepairResult`（通用基线）
6. 返回 `FullAuditResult`

---

#### 函数：`run_cases_full(cases: list[ProbeCase]) -> list[FullAuditResult]`

位置：`cmd_audit/harness.py:118-119`

目的：

- `run_case_full` 的批量版本，用于多案例烟雾套件。

调用者：测试和外部脚本。

---

#### 函数：`write_repair_success_table_from_full(results, output_path, *, sandbox_root=None) -> list[RepairComparisonRow]`

位置：`cmd_audit/harness.py:122-131`

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

- 桥接函数：将 `FullAuditResult` 列表转换为 `RepairComparisonRow` 列表并写入修复成功对比表。
- 这是面向 CLI/脚本的主要入口点。
- 通过 `write_repair_success_table` 间接使用 issue 0005 的 `validate_sandbox_path` 进行沙箱边界校验。

调用者：

- 测试：`RepairSuccessTableTest`
- 外部脚本和 CLI

### `cmd_audit/post_repair.py`（Issue 0006 相关部分）

---

#### 函数：`_ecs_for_label(case, predicted_label, replay) -> tuple[str, str, str]`

位置：`cmd_audit/post_repair.py:214-219`

```python
def _ecs_for_label(case, predicted_label: str, replay) -> tuple[str, str, str]:
    from .repairs import get_targeted_repair_action  # lazy import
    action = get_targeted_repair_action(predicted_label)
    return (action.cause, replay.evidence_block, action.repair_guidance)
```

目的：

- 返回逐标签的 `(cause, corrected_memory, repair_guidance)`。
- 使用延迟导入从 `repairs.py` 获取 `get_targeted_repair_action` 以避免循环依赖。

设计决策：`TargetedRepairAction` 是 cause 和 repair_guidance 的权威源。`_ecs_for_label` 不再有内联模板——所有 cause/repair_guidance 文本统一来源于 `REPAIR_ACTION_BY_LABEL` 字典。这确保 ECS 草案构建和修复对比逻辑引用相同的文本。

调用者：

- `draft_ecs`（`post_repair.py:110-131`）

---

### `cmd_audit/__init__.py`（Issue 0006 导出）

从 `repairs` 导出：`RepairClaimLedger`、`RepairComparisonRow`、`TargetedRepairAction`、`build_repair_claim_ledger`、`compute_repair_success_summary`、`get_targeted_repair_action`、`make_repair_comparison`、`write_repair_success_table`。

从 `harness` 导出：`FullAuditResult`、`run_case_full`、`run_cases_full`、`write_repair_success_table_from_full`。

## 测试级合约

测试位于 `tests/test_cmd_audit_issue6_targeted_repairs.py`。6 个测试类，26 个测试方法。测试数据来自 `data/probe_cases/v0_issue3_cases.json`。

所有测试类在 `setUpClass` 中通过 `run_cases_full(cls.cases)` 构建完整流水线结果，以确保端到端覆盖。

### `LabelToRepairMappingTest`（4 个测试）

验证：每个主要归因标签映射到一个修复操作。

| 测试方法 | 验证内容 |
| --- | --- |
| `test_all_six_v0_labels_have_targeted_repair` | `V0_PIPELINE_LABEL_ORDER` 中每个标签都有带有非空 `action_name`、`description` 和 `intervention_summary` 的 `TargetedRepairAction`。 |
| `test_targeted_repair_actions_are_distinct` | 全部六个 `action_name` 均不同。 |
| `test_get_targeted_repair_rejects_invalid_label` | `"item_wrong"`（超出范围的项标签）抛出异常。 |
| `test_each_repair_action_describes_targeted_intervention` | 没有任何描述包含 `"all extracted memory"`（hard-case 的标识）。 |

### `RepairComparisonRowTest`（5 个测试）

验证：CMD 引导的修复与无差别 hard-case 更新进行对比。

| 测试方法 | 验证内容 |
| --- | --- |
| `test_one_row_per_case` | `run_cases_full(6 cases)` 产生正好 6 行。 |
| `test_each_row_has_required_fields` | 检查类型、字符串字段和 `targeted_assessment`/`hard_case_assessment` 是有效三值集合的成员。 |
| `test_repair_action_matches_predicted_label` | `row.repair_action` 等于 `predicted_label` 的 `TargetedRepairAction.action_name`。 |
| `test_targeted_and_hard_case_have_independent_results` | Token 成本均为 `float` 且 `>= 0`。 |
| `test_targeted_better_flag_is_consistent` | `targeted_better` 布尔值与评估排名一致：更好的排名 → True，更差的排名 → False。 |

### `RepairSuccessSummaryTest`（4 个测试）

验证：修复结果按标签聚合。

| 测试方法 | 验证内容 |
| --- | --- |
| `test_summary_covers_all_perturbation_labels` | 摘要字典键与案例的扰动标签集合匹配。 |
| `test_each_summary_has_balanced_counts` | 针对性计数总和、hard-case 计数总和和 better/same 计数总和均等于 `total_cases`。 |
| `test_recovery_rates_are_valid` | 所有恢复率在 `[0.0, 1.0]` 内。 |
| `test_token_costs_are_positive` | 两个平均 token 成本均 `> 0.0`。 |

### `ClaimLedgerTest`（4 个测试）

验证：声明账本记录针对性修复是否确实更好。

| 测试方法 | 验证内容 |
| --- | --- |
| `test_ledger_is_complete` | 实例类型正确，`total_cases=6`，`claim_supported` 是 `bool`。 |
| `test_ledger_rates_in_range` | 全部六个速率/百分比在 `[0.0, 1.0]` 内。 |
| `test_ledger_with_empty_data` | 空摘要 → `total_cases=0`、`targeted_recovery_rate=0.0`、`claim_supported=False`。 |
| `test_ledger_claim_is_evidence_based` | 从数据独立计算期望的 `claim_supported` 并与 `build_repair_claim_ledger()` 的输出进行对比。 |

### `RepairSuccessTableTest`（4 个测试）

验证：修复成功表基于 Post-Repair Context Replay 结果。

| 测试方法 | 验证内容 |
| --- | --- |
| `test_repair_success_table_writes_comparison_csv` | `repair_success_table.csv` 存在，表头包含全部必填列。 |
| `test_repair_success_table_writes_label_summary_csv` | `repair_label_summary.csv` 存在，包含 `"label"` 和 `"targeted_recovery_rate"`。 |
| `test_repair_success_table_writes_claim_ledger` | `repair_claim_ledger.txt` 存在，包含 `"CMD V0 Repair Claim Ledger"` 和 `"Claim supported:"`。 |
| `test_repair_success_table_rejects_outside_sandbox` | 写入沙箱外部抛出 `ValueError`。 |

### `FullPipelinePerLabelTest`（5 个测试）

验证：完整的 CMD 流水线为每个标签生成有效的修复结果。

| 测试方法 | 验证内容 |
| --- | --- |
| `test_all_six_cases_produce_full_results` | 6 个 `FullAuditResult` 对象，每个都通过实例检查。 |
| `test_all_cases_have_valid_attribution` | 每个案例 `perturbation_label == attribution.predicted_label`。 |
| `test_targeted_repair_differs_from_hard_case_baseline` | ECS `repair_guidance` 不包含 `"all extracted memory"`，修复后上下文不包含 `"Hard-case update"`。 |
| `test_partial_assessments_exist_where_expected` | `"recovered"` 存在于评估结果中；`partial` 可能存在也可能不（两者在烟雾套件中均有效）。 |
| `test_hard_case_baseline_differs_from_targeted` | Hard-case 基线具有有效的三值评估字符串且 token 成本 `>= 0`。 |

## 产出物合约

### `artifacts/sandbox/repair_success_table.csv`

15 列：`case_id`、`perturbation_label`、`predicted_label`、`repair_action`、`pre_repair_answer_score`、`pre_repair_evidence_score`、`targeted_assessment`、`targeted_answer_score`、`targeted_evidence_score`、`targeted_token_cost`、`hard_case_assessment`、`hard_case_answer_score`、`hard_case_evidence_score`、`hard_case_token_cost`、`targeted_better`。

当前烟雾数据：6 行。CMD 修复全部 `targeted_assessment="recovered"`。Hard-case 基线在前 3 个标签（write、compression、premature_extraction）上为 `"failed"`，后 3 个为 `"recovered"`。

### `artifacts/sandbox/repair_label_summary.csv`

15 列：`label`、`total_cases`、`targeted_recovered`、`targeted_partial`、`targeted_failed`、`targeted_recovery_rate`、`hard_case_recovered`、`hard_case_partial`、`hard_case_failed`、`hard_case_recovery_rate`、`targeted_better_count`、`hard_case_better_count`、`same_outcome_count`、`avg_targeted_token_cost`、`avg_hard_case_token_cost`。

当前烟雾数据：每个标签 1 行，`targeted_recovered=1`（全部），`hard_case_recovered` 在 write/compression/premature_extraction 上为 0，其他为 1。

### `artifacts/sandbox/repair_claim_ledger.txt`

人类可读文本文件，包含标题 `"CMD V0 Repair Claim Ledger — Issue 0006"`、完全恢复率、宽松恢复率、targeted-better 百分比、平均 token 节省和每标签明细。

当前烟雾数据：`targeted_recovery_rate=1.000` vs `hard_case=0.500`，`claim_supported=True`。

## 边界规则

1. **ECS cause 来源**：`TargetedRepairAction`（`repairs.py:REPAIR_ACTION_BY_LABEL`）是所有 cause 和 repair_guidance 文本的权威源。`post_repair.py:_ecs_for_label` 通过延迟导入从同一字典获取，确保 ECS 草案和修复对比使用相同的文本。不再有内联模板。

2. **成本敏感优势判据**：`_is_targeted_better` 使用优先级链：`recovered > partial > failed`，平局时比较 token 成本。`build_repair_claim_ledger` 的声明支持判据要求：(a) CMD 宽松恢复率不低于 hard-case 且 token 更低，或 (b) CMD 完全恢复率严格更高。

3. **Hard-case 基线分离**：`run_hard_case_update_baseline` 在结构上独立于 CMD 修复。它使用相同的 `run_post_repair_context_replay` 评分函数，但使用通用上下文（所有提取后的记忆）。CMD 的针对性修复和 hard-case 基线之间的对比是修复有效性的证据。

4. **沙箱写入边界**：所有修复产出物写入必须通过 `validate_sandbox_path`，该函数强制执行 `artifacts/sandbox/` 边界。

5. **现有归因和修复流水线不变**：Issue 0006 在 `FullAuditResult` 和 `run_case_full` 之上构建对比逻辑。它不修改 `run_case`、`AuditResult`、`draft_ecs`、`run_post_repair_context_replay` 或任何回放函数。

6. **CMD-Audit / CMD-Skill Adapter 分离**：修复成功对比停留在 CMD-Audit 内。只有 CMD-Skill Adapter（V1）可以将经过验证的修复应用到生产代理状态。

## 验收标准可追溯性

| Issue 0006 AC | 代码接口 | 测试接口 |
| --- | --- | --- |
| 每个主要归因标签映射到一个修复操作。 | `REPAIR_ACTION_BY_LABEL` + `get_targeted_repair_action`。 | `LabelToRepairMappingTest`（4 个测试） |
| CMD 引导的修复与无差别 hard-case 更新进行对比。 | `make_repair_comparison` 构建 `RepairComparisonRow`，包含针对性结果和 hard-case 结果。 | `RepairComparisonRowTest`（5 个测试） |
| 修复成功基于 Post-Repair Context Replay，而不仅基于 ECS 文本质量。 | `RepairComparisonRow` 从 `PostRepairResult` 读取 `targeted_assessment` 和 `hard_case_assessment`。 | `RepairSuccessTableTest`（4 个测试） |
| 指标包括答案 F1/准确率、证据召回、token 成本。 | `RepairComparisonRow` 包含 `targeted_answer_score`、`targeted_evidence_score`、`targeted_token_cost` 和 hard-case 对应的等价字段。 | `RepairComparisonRowTest.test_each_row_has_required_fields` |
| 结果按归因标签分解。 | `compute_repair_success_summary` 按 `perturbation_label` 聚合。 | `RepairSuccessSummaryTest`（4 个测试） |
| 声明账本记录针对性修复是否确实更好。 | `build_repair_claim_ledger` + `_write_claim_ledger`。 | `ClaimLedgerTest`（4 个测试） |

## 验证

命令：

```bash
# 仅 issue 0006 测试（26 个测试）
python3 -m pytest tests/test_cmd_audit_issue6_targeted_repairs.py -v

# 从烟雾套件生成修复成功产出物
python3 -c "
from pathlib import Path
from cmd_audit import load_probe_cases, run_cases_full, write_repair_success_table_from_full
cases = load_probe_cases('data/probe_cases/v0_issue3_cases.json')
results = run_cases_full(cases)
sandbox = Path('artifacts/sandbox')
sandbox.mkdir(parents=True, exist_ok=True)
write_repair_success_table_from_full(results, sandbox / 'repair_success_table.csv', sandbox_root=sandbox)
for r in results:
    print(f'{r.audit.case_id}: {r.audit.perturbation_label} -> CMD={r.post_repair.repair_assessment} vs hard={r.hard_case_baseline.repair_assessment}')
"
```

已验证状态（2026-05-10）：

```text
烟雾套件修复结果（6 个案例通过 run_case_full）：
  v0-write-001: write_error -> recovered (CMD) vs failed (hard_case)
  v0-compression-001: compression_error -> recovered (CMD) vs failed (hard_case)
  v0-premature-extraction-001: premature_extraction_error -> recovered (CMD) vs failed (hard_case)
  v0-retrieval-001: retrieval_error -> recovered (CMD) vs recovered (hard_case)
  v0-injection-001: injection_error -> recovered (CMD) vs recovered (hard_case)
  v0-reasoning-001: reasoning_error -> recovered (CMD) vs recovered (hard_case)

CMD 修复在 3/6 烟雾案例上优于 hard-case 基线（write、compression、premature_extraction）。
对于 retrieval、injection 和 reasoning，两者都恢复——注入所有提取后的记忆恰好包含正确的记忆。
CMD 在这些案例中的价值在于精确诊断（predicted_label）和更低的 token 成本。

声明账本：targeted_recovery_rate=1.000 vs hard_case=0.500，claim_supported=True。
```

## 后续依赖此 Issue 的问题

| Issue | 依赖 | 方式 |
| --- | --- | --- |
| Issue 0010（证据驱动版本关卡） | `repair_success_table.csv`、`repair_label_summary.csv` | V0→V1 门第四关（修复评估分布）从修复成功表读取。 |
| Issue 0007（ECS Failure Memory 复发率） | `TargetedRepairAction`、`ECSDraft` | Failure Memory 存储 ECS 记录用于复发率测量。 |
