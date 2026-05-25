# Issue 0010 实现细节：证据驱动版本关卡

## 目的

本文档是 issue 0010《强制执行证据驱动版本关卡》的全局实现地图。Issue 0010 通过定义、检查和追踪以可信度证据（而非功能堆叠）驱动的版本关卡，闭合 V0 治理循环：

```text
四个 V0 证据产出物
  -> 每个产出物一个 GateCriterion
  -> check_v0_to_v1_gate()
      -> _check_macro_f1 (comparison_metrics.csv)
      -> _check_confusion_diagonal (attribution_confusion_matrix.csv)
      -> _check_accuracy_top2 (comparison_metrics.csv)
      -> _check_repair_distribution (post_repair_table.csv)
  -> GateResult（逐标准 pass/fail）
  -> write_gate_status -> V0V1_gate_status.txt（沙箱产出物）
  -> GateReview（HITL 决策：approved/deferred/rejected）
  -> write_gate_review -> V0V1_gate_review.txt（带日期的审核记录）
```

V1→V2 关卡以存根定义：至少两个不同的记忆代理通过 Adapter Interface 集成，且无 macro F1 退化。该关卡在 V0 中始终返回 not-met，因为不存在任何适配器集成。

最终关卡决策为 HITL（人机协同）：代码检查阈值并报告证据；人类审核并签字。

## 源需求

本实现遵循以下本地规划文件：

| 来源 | 在 Issue 0010 中应用的需求 |
| --- | --- |
| `TASK.md` | 定义 V0→V1 和 V1→V2 证据关卡；关卡状态记录在文档中而非代码中；需要 HITL 签字。 |
| `CLAUDE.md` | 版本关卡 V0→V1→V2 是证据驱动的：V0→V1 要求四个 V0 证据产出物通过论文声明的阈值；V1→V2 要求至少两个不同的记忆代理集成且无 macro F1 退化。 |
| `cmd_innovation_core/CONTEXT.md` | **版本关卡**是证据驱动的，非功能堆叠。**CMD-Audit** 仅写入沙箱。 |
| `cmd_innovation_core/prd/cmd_minimal_probe_prd.md` | AC10：V0→V1 要求四个 V0 证据产出物通过论文声明阈值；V1→V2 要求适配器集成数量和 macro F1 非退化。User Story 32：版本关卡由证据阈值驱动，非功能完成度。 |
| `cmd_innovation_core/issues/0010-enforce-evidence-driven-version-gates.md` | 四个 V0→V1 标准：macro F1、confusion diagonal、accuracy+top-2、repair distribution。V1→V2 存根。带日期审核记录的关卡追踪文档。关卡不阻塞实现——仅锁定版本声明。 |
| `cmd_innovation_core/tdd/cmd_tracer_bullets.md` | 关卡检查通过公共接口验证；沙箱写入边界强制；HITL 审核流水线可独立测试。 |

## 领域边界

Issue 0010 位于治理层，读取早期 issue 产生的证据产出物并产出关卡状态输出：

```text
Issue 0002/0003 产出物
  -> comparison_metrics.csv ─────────────┐
  -> attribution_confusion_matrix.csv ───┼──> check_v0_to_v1_gate()
Issue 0005 产出物                          │
  -> post_repair_table.csv ──────────────┘
      -> GateCriterion x4
      -> GateResult
      -> artifacts/sandbox/V0V1_gate_status.txt
      -> GateReview (HITL)
      -> artifacts/sandbox/V0V1_gate_review.txt
```

Issue 0010 拥有的内容：

- 定义带特定阈值的 V0→V1 关卡标准；
- 读取证据产出物并评估每个标准；
- 产生 GateResult，包含逐标准 pass/fail 和聚合 `all_passed`；
- 定义 V1→V2 关卡标准（适配器集成数量）；
- 将关卡状态文档写入回放本地沙箱；
- 写入带日期的 HITL 审核记录；
- 暴露关卡检查 API 供编程调用。

Issue 0010 不拥有的内容（属于其他 issue）：

- 产生证据产出物本身（issues 0002、0003、0005、0006、0007）；
- 做出最终版本锁定决策（HITL）；
- 阻塞实现工作（关卡仅锁定版本声明，不锁定开发）；
- 与生产 CI/CD 或远程追踪集成；
- 扩展 V0 标签集或归因分类法。

## 代码产物

| 产物 | 在 Issue 0010 中的角色 |
| --- | --- |
| `cmd_audit/version_gates.py` | 核心模块：数据类型（GateCriterion、GateResult、GateReview）、V0→V1 关卡检查、V1→V2 存根、关卡状态与审核文档写入器、内部 CSV 读取器和标准检查器。451 行。 |
| `cmd_audit/labels.py` | 提供 `V0_PIPELINE_LABEL_ORDER`（六个 V0 标签），由 `_check_confusion_diagonal` 使用。 |
| `cmd_audit/writers.py` | 提供 `write_text_artifact`（第 55-67 行），由 `write_gate_status` 和 `write_gate_review` 使用以写入带沙箱校验的文本产出物。 |
| `cmd_audit/post_repair.py` | 提供 `validate_sandbox_path`，`write_text_artifact` 通过它强制沙箱写入边界。 |
| `cmd_audit/__init__.py` | 导出 7 个新符号：GateCriterion、GateResult、GateReview、check_v0_to_v1_gate、check_v1_to_v2_gate、write_gate_status、write_gate_review。 |
| `cmd_innovation_core/gates/V0V1_gate_status.md` | 人类可读的关卡追踪文档，包含逐标准证据和 HITL 审核日志。 |
| `artifacts/sandbox/V0V1_gate_status.txt` | 由 `write_gate_status` 生成的关卡状态产出物。 |
| `artifacts/sandbox/V0V1_gate_review.txt` | 由 `write_gate_review` 生成的 HITL 审核产出物。 |
| `tests/test_cmd_audit_issue10_version_gates.py` | 14 个测试类中的 48 个行为级测试。 |

## 模块地图

| 模块 | Issue 0010 角色 |
| --- | --- |
| `cmd_audit/version_gates.py` | 拥有关卡数据类型、标准检查、关卡检查函数和输出写入器。 |
| `cmd_audit/labels.py` | 提供 `V0_PIPELINE_LABEL_ORDER`，由 `_check_confusion_diagonal` 用于遍历所有六个 V0 标签。 |
| `cmd_audit/writers.py` | 提供 `write_text_artifact`（第 55-67 行）：通用文本产出物写入器，可选沙箱校验。由 `write_gate_status` 和 `write_gate_review` 调用以写入关卡文本文件。 |
| `cmd_audit/post_repair.py` | 提供 `validate_sandbox_path`，由 `write_text_artifact` 使用以强制沙箱写入边界。 |
| `cmd_audit/__init__.py` | 为调用者和测试导出公共接口。 |

Issue 0010 不依赖 `harness.py`、`baselines.py`、`replays.py`、`attribution.py`、`repairs.py`、`failure_memory.py` 或 `models.py`。它直接读取 CSV 产出物，不通过 harness。

## 调用图

主关卡检查路径：

```text
tests/test_cmd_audit_issue10_version_gates.py
  -> check_v0_to_v1_gate()
      -> _check_macro_f1(comparison_metrics.csv)
          -> _read_comparison_csv
      -> _check_confusion_diagonal(attribution_confusion_matrix.csv)
          -> _read_confusion_csv
          -> labels.V0_PIPELINE_LABEL_ORDER
      -> _check_accuracy_top2(comparison_metrics.csv)
          -> _read_comparison_csv
      -> _check_repair_distribution(post_repair_table.csv)
          -> _read_repair_csv
  -> check_v1_to_v2_gate()  [存根，始终 not-met]
  -> write_gate_status(result, path, sandbox_root)
      -> writers.write_text_artifact
          -> post_repair.validate_sandbox_path
  -> write_gate_review(review, path, sandbox_root)
      -> writers.write_text_artifact
          -> post_repair.validate_sandbox_path
```

产出物生成路径：

```text
python3 -c "..."
  -> check_v0_to_v1_gate()
      -> [_check_macro_f1, _check_confusion_diagonal, _check_accuracy_top2, _check_repair_distribution]
  -> write_gate_status(result, "artifacts/sandbox/V0V1_gate_status.txt")
  -> GateReview(...)
  -> write_gate_review(review, "artifacts/sandbox/V0V1_gate_review.txt")
```

## 数据流

输入产出物（由关卡检查读取）：

```text
artifacts/comparison_metrics.csv
  -> macro_f1 列（标准 1）
  -> attribution_accuracy、top2_accuracy 列（标准 3）

artifacts/attribution_confusion_matrix.csv
  -> 逐标签行计数（标准 2）

artifacts/sandbox/post_repair_table.csv
  -> repair_assessment 列（标准 4）
```

输出：

```text
GateCriterion（7 个字段）
  criterion_id, description, artifact_path, threshold, passed, evidence, missing

GateResult（4 个字段）
  gate_id: "V0→V1" | "V1→V2"
  criteria: tuple[GateCriterion, ...]
  all_passed: bool
  checked_at: str（ISO 时间戳）

GateReview（6 个字段）
  gate_id, reviewer, decision（"approved"|"deferred"|"rejected"）,
  rationale, missing_evidence, reviewed_at
```

产出物输出：

```text
artifacts/sandbox/V0V1_gate_status.txt
artifacts/sandbox/V0V1_gate_review.txt
```

## 函数级合约

### `cmd_audit/version_gates.py`

此模块拥有 issue 0010 的全部版本关卡接口。它是一个新模块，依赖 `labels.py`（获取 `V0_PIPELINE_LABEL_ORDER`）、`writers.py`（获取 `write_text_artifact`）和 `post_repair.py`（间接通过 `writers.write_text_artifact` → `validate_sandbox_path`）。不依赖 harness、baselines、replays 或 models。

---

#### 常量：`V0V1_CRITERION_IDS`

位置：`cmd_audit/version_gates.py:15-20`

```python
V0V1_CRITERION_IDS = (
    "macro_f1_exceeds_baselines",
    "confusion_diagonal_dominance",
    "accuracy_top2_exceeds_baselines",
    "repair_assessment_distribution",
)
```

目的：

- 记录定义 V0→V1 关卡的四个标准 ID。
- 作为参考常量使用；不在程序中强制（四个标准在 `check_v0_to_v1_gate` 中硬编码以保持显式排序）。

---

#### 常量：`_GATE_DECISION_VALUES`

位置：`cmd_audit/version_gates.py:13`

```python
_GATE_DECISION_VALUES = ("approved", "deferred", "rejected")
```

目的：

- 定义三个有效的 HITL 审核决策。
- 由 `GateReview.__post_init__` 用于校验。

---

#### 数据类：`GateCriterion`

位置：`cmd_audit/version_gates.py:26-37`

```python
@dataclass(frozen=True)
class GateCriterion:
    criterion_id: str
    description: str
    artifact_path: str
    threshold: str
    passed: bool
    evidence: str
    missing: str
```

目的：

- 不可变的单个标准检查结果。
- 记录标准是否通过、观察到什么证据，以及失败时缺失什么。

字段含义：

| 字段 | 领域含义 |
| --- | --- |
| `criterion_id` | 简短机器可读标识符（如 `"macro_f1_exceeds_baselines"`）。 |
| `description` | 人类可读的一句话描述，说明此标准检查什么。 |
| `artifact_path` | 被读取的证据产出物的路径（字符串形式，用于文档记录）。 |
| `threshold` | 人类可读的阈值描述（如 `"CMD-Audit macro_f1 > evidence_recall AND subagent_judge AND random_label"`）。 |
| `passed` | 标准是否满足。 |
| `evidence` | 观察值的简明摘要（如 `"CMD-Audit macro_f1=1.000; evidence_recall=0.778"`）。 |
| `missing` | 通过时为空字符串；失败时描述缺失内容。 |

---

#### 数据类：`GateResult`

位置：`cmd_audit/version_gates.py:39-47`

```python
@dataclass(frozen=True)
class GateResult:
    gate_id: str
    criteria: tuple[GateCriterion, ...]
    all_passed: bool
    checked_at: str
```

目的：

- 检查版本关卡所有标准后的不可变聚合结果。
- `all_passed` 仅当 `criteria` 中每个标准的 `passed=True` 时才为 `True`。

---

#### 数据类：`GateReview`

位置：`cmd_audit/version_gates.py:49-65`

```python
@dataclass(frozen=True)
class GateReview:
    gate_id: str
    reviewer: str
    decision: str
    rationale: str
    missing_evidence: str
    reviewed_at: str

    def __post_init__(self) -> None:
        if self.decision not in _GATE_DECISION_VALUES:
            raise ValueError(...)
```

目的：

- 版本关卡的不可变 HITL 审核决策。
- `decision` 字段被校验为 `("approved", "deferred", "rejected")` 之一。

`__post_init__` 行为：

- 若 `decision` 不在 `_GATE_DECISION_VALUES` 中则抛出 `ValueError`。

为什么重要：

- 防止歧义或未定义的审核决策进入关卡追踪文档。
- 三个值映射为：approved（关卡通过，版本锁定）、deferred（证据不足，需要更多数据）、rejected（关卡失败，不能声明版本）。

---

#### 函数：`check_v0_to_v1_gate(artifacts_dir=None, sandbox_dir=None) -> GateResult`

位置：`cmd_audit/version_gates.py:71-110`

```python
def check_v0_to_v1_gate(
    artifacts_dir: Path | None = None,
    sandbox_dir: Path | None = None,
) -> GateResult:
```

目的：

- 根据当前产出物检查所有四个 V0→V1 证据关卡标准。

参数：

- `artifacts_dir`：产出物目录路径（默认 `Path("artifacts")`）。
- `sandbox_dir`：沙箱目录路径（默认 `Path("artifacts/sandbox")`）。

行为：

1. 解析产出物和沙箱目录路径（默认使用项目相对路径）。
2. 按顺序运行四个标准检查：
   - `_check_macro_f1(artifacts_dir / "comparison_metrics.csv")`
   - `_check_confusion_diagonal(artifacts_dir / "attribution_confusion_matrix.csv")`
   - `_check_accuracy_top2(artifacts_dir / "comparison_metrics.csv")`
   - `_check_repair_distribution(sandbox_dir / "post_repair_table.csv")`
3. 设置 `all_passed = all(c.passed for c in criteria)`。
4. 记录当前 UTC 时间戳为 `checked_at`。
5. 返回 `GateResult(gate_id="V0→V1", criteria=..., all_passed=..., checked_at=...)`。

边界条件：

- 若产出物文件缺失，对应标准返回 `passed=False`，`missing` 描述缺失文件。
- 若产出物格式错误（如缺少预期列），标准返回 `passed=False`，`missing` 包含异常消息。

领域边界：

- 该函数不修改任何产出物或写入磁盘。
- 它直接读取 CSV 文件；不使用 CMD harness。
- `all_passed` 字段是程序性摘要，而非最终关卡决策。最终决策为 HITL。

调用者：

- 测试（`V0V1GateCheckWithRealArtifactsTest`、`V0V1GateCheckWithTempArtifactsTest`、`GatesDoNotBlockImplementationTest`）。
- 产出物生成脚本。
- 从 `cmd_audit` 导入的外部用户。

---

#### 函数：`check_v1_to_v2_gate() -> GateResult`

位置：`cmd_audit/version_gates.py:116-142`

```python
def check_v1_to_v2_gate() -> GateResult:
```

目的：

- 返回存根 V1→V2 关卡结果，表明该关卡尚不可评估。

行为：

1. 创建单个 `GateCriterion`：
   - `criterion_id="adapter_integration_count"`
   - `description="At least two distinct memory agents integrated through the Adapter Interface without macro F1 regression"`
   - `artifact_path="(none — adapter integrations do not exist in V0)"`
   - `threshold="adapter_count >= 2 AND no macro F1 regression"`
   - `passed=False`
   - `evidence="0 adapter integrations; V0 operates as standalone harness."`
   - `missing="No Adapter Interface integrations exist. V1 must integrate at least two distinct memory agents before V1→V2 gate review."`
2. 记录当前 UTC 时间戳为 `checked_at`。
3. 返回 `GateResult(gate_id="V1→V2", criteria=(criterion,), all_passed=False, checked_at=...)`。

领域含义：

- V1→V2 关卡已定义但在 V0 中始终返回 not-met。它作为 V1 路线图的前向引用。
- 关卡标准故意为单一标准（适配器数量 + 非退化）。随着 V1 推进，可能添加额外标准。

---

#### 函数：`write_gate_status(result, output_path, sandbox_root=None) -> Path`

位置：`cmd_audit/version_gates.py:148-181`

```python
def write_gate_status(
    result: GateResult,
    output_path: Path,
    sandbox_root: str | Path | None = None,
) -> Path:
```

目的：

- 将人类可读的关卡状态文档写入沙箱。

参数：

- `result`：来自 `check_v0_to_v1_gate` 或 `check_v1_to_v2_gate` 的 `GateResult`。
- `output_path`：要写入的文件路径（必须在沙箱内）。
- `sandbox_root`：可选的沙箱根路径，用于路径校验。

行为：

1. 通过 `write_text_artifact` → `validate_sandbox_path(output_path, sandbox_root)` 验证 `output_path` 在沙箱内。
2. 按需创建父目录。
3. 写入结构化文本文档，包含：
   - 标题：关卡 ID、日期、分隔线。
   - `All criteria passed: True/False`。
   - 逐标准块：标准编号、ID、PASS/FAIL 状态、描述、产出物路径、阈值、证据、缺失（如有）。
   - 页脚：`"Final decision: HITL review required."`、检查时间戳。
4. 返回写入的路径。

输出格式：

```text
CMD V0→V1 Gate Status — 2026-05-10
============================================================

All criteria passed: True

Criterion 1: macro_f1_exceeds_baselines [PASS]
  Description: CMD macro F1 exceeds all comparator baselines
  Artifact:    artifacts/comparison_metrics.csv
  Threshold:   CMD-Audit macro_f1 > evidence_recall AND subagent_judge AND random_label
  Evidence:    CMD-Audit macro_f1=1.000; evidence_recall=0.778; subagent_judge=0.778; random_label=0.167

...

---
Final decision: HITL review required.
Checked at: 2026-05-10T...
```

调用者：

- 产出物生成脚本。
- 测试（`GateStatusWriteTest`）。

---

#### 函数：`write_gate_review(review, output_path, sandbox_root=None) -> Path`

位置：`cmd_audit/version_gates.py:184-209`

```python
def write_gate_review(
    review: GateReview,
    output_path: Path,
    sandbox_root: str | Path | None = None,
) -> Path:
```

目的：

- 将带日期的 HITL 关卡审核记录写入沙箱。

参数：

- `review`：包含 HITL 决策的 `GateReview`。
- `output_path`：要写入的文件路径（必须在沙箱内）。
- `sandbox_root`：可选的沙箱根路径，用于路径校验。

行为：

1. 通过 `write_text_artifact` → `validate_sandbox_path(output_path, sandbox_root)` 验证 `output_path` 在沙箱内。
2. 按需创建父目录。
3. 写入结构化文本文档，包含：
   - 标题：关卡 ID、日期。
   - Reviewer、decision、reviewed 时间戳。
   - Rationale 部分。
   - Missing evidence 部分（如非空）。
4. 返回写入的路径。

输出格式：

```text
CMD V0→V1 Gate Review — 2026-05-10
============================================================

Reviewer:   HITL
Decision:   deferred
Reviewed:   2026-05-10T12:00:00Z

Rationale:
  All four criteria pass on 6-case smoke suite. ...

Missing evidence:
  Smoke suite (6 cases) is insufficient for paper claims. ...
```

调用者：

- 产出物生成脚本。
- 测试（`GateReviewWriteTest`）。

---

#### 内部函数：`_read_comparison_csv(path) -> dict[str, dict[str, float]]`

位置：`cmd_audit/version_gates.py:215-227`

```python
def _read_comparison_csv(path: Path) -> dict[str, dict[str, float]]:
```

目的：

- 读取 `comparison_metrics.csv` 并返回嵌套字典：`{system_name: {column_name: value}}`。

行为：

1. 若文件不存在则抛出 `FileNotFoundError`。
2. 使用 `csv.DictReader` 解析 CSV。
3. 将所有非 `system_name` 列转换为 `float`。
4. 返回 `{row["system_name"]: {col: float(val), ...}, ...}`。

被以下使用：`_check_macro_f1`、`_check_accuracy_top2`。

---

#### 内部函数：`_read_confusion_csv(path) -> dict[str, dict[str, int]]`

位置：`cmd_audit/version_gates.py:230-240`

```python
def _read_confusion_csv(path: Path) -> dict[str, dict[str, int]]:
```

目的：

- 读取 `attribution_confusion_matrix.csv` 并返回嵌套字典：`{gold_label: {pred_label: count}}`。

行为：

1. 若文件不存在则抛出 `FileNotFoundError`。
2. 使用 `csv.DictReader` 解析 CSV。
3. 将所有非 `gold_label` 列转换为 `int`。
4. 返回 `{row["gold_label"]: {col: int(val), ...}, ...}`。

被以下使用：`_check_confusion_diagonal`。

---

#### 内部函数：`_read_repair_csv(path) -> list[str]`

位置：`cmd_audit/version_gates.py:243-252`

```python
def _read_repair_csv(path: Path) -> list[str]:
```

目的：

- 读取 `post_repair_table.csv` 并返回 `repair_assessment` 值列表。

行为：

1. 若文件不存在则抛出 `FileNotFoundError`。
2. 使用 `csv.DictReader` 解析 CSV。
3. 返回每个数据行的 `[row["repair_assessment"], ...]`。

被以下使用：`_check_repair_distribution`。

---

#### 内部函数：`_check_macro_f1(comparison_path) -> GateCriterion`

位置：`cmd_audit/version_gates.py:255-293`

```python
def _check_macro_f1(comparison_path: Path) -> GateCriterion:
```

目的：

- 检查 CMD-Audit macro F1 是否超过全部三个比较基线。

行为：

1. 通过 `_read_comparison_csv` 读取 `comparison_metrics.csv`。
2. 提取 CMD-Audit、evidence_recall、subagent_judge 和 random_label 的 `macro_f1`。
3. `passed = cmd_macro_f1 > max(evidence_recall, subagent_judge, random_label)`。
4. 若任何基线具有相等或更高的 macro F1，则 `passed=False`，`missing` 记录差距。
5. 在 `FileNotFoundError` 或 `KeyError` 时：返回 `passed=False`，异常信息放在 `evidence` 和 `missing` 中。

阈值：

```text
CMD-Audit macro_f1 > evidence_recall AND subagent_judge AND random_label
```

领域含义：

- Macro F1 是归因质量的主要论文声明指标。
- CMD 必须严格超过（非等于）所有基线。相等无法证明改进。

调用者：`check_v0_to_v1_gate`。

---

#### 内部函数：`_check_confusion_diagonal(confusion_path) -> GateCriterion`

位置：`cmd_audit/version_gates.py:296-339`

```python
def _check_confusion_diagonal(confusion_path: Path) -> GateCriterion:
```

目的：

- 检查混淆矩阵在所有六个 V0 标签上具有对角线主导。

行为：

1. 通过 `_read_confusion_csv` 读取 `attribution_confusion_matrix.csv`。
2. 遍历 `V0_PIPELINE_LABEL_ORDER` 中的所有六个标签。
3. 对每个标签行：计算 `diagonal`（自预测计数）和 `off_diagonal_sum`（所有其他列的总和）。
4. `passed = diagonal > off_diagonal_sum` 对每个标签都成立。
5. 收集任何 `diagonal <= off_diagonal_sum` 标签的违规字符串。
6. 成功时：evidence = `"All 6 V0 labels have diagonal > off-diagonal sum"`。
7. 失败时：missing = 拼接的违规字符串。
8. 在 `FileNotFoundError` 或 `KeyError` 时：返回 `passed=False`，异常信息放在 `evidence` 和 `missing` 中。

阈值：

```text
对每个 V0 标签行：diagonal > 非对角线项之和
```

领域含义：

- 对角线主导意味着每个标签的正确率高于与任何其他标签混淆的概率。
- 每个标签 1 个案例时，diagonal=1 和 off-diagonal=0 是平凡的。随着探针套件扩展，非对角线项将出现，此标准将变得有区分力。

调用者：`check_v0_to_v1_gate`。

---

#### 内部函数：`_check_accuracy_top2(comparison_path) -> GateCriterion`

位置：`cmd_audit/version_gates.py:342-398`

```python
def _check_accuracy_top2(comparison_path: Path) -> GateCriterion:
```

目的：

- 检查 CMD-Audit 在归因准确率和 top-2 准确率上均优于所有基线。

行为：

1. 通过 `_read_comparison_csv` 读取 `comparison_metrics.csv`。
2. 提取 CMD-Audit 和全部三个基线的 `attribution_accuracy` 和 `top2_accuracy`。
3. `acc_ok = cmd_attribution_accuracy > max(baseline_attribution_accuracy)`。
4. `top2_ok = cmd_top2_accuracy > max(baseline_top2_accuracy)`。
5. `passed = acc_ok AND top2_ok`。
6. 失败时：`missing` 记录哪个子标准失败及具体差距。
7. 在 `FileNotFoundError` 或 `KeyError` 时：返回 `passed=False`，异常信息放在 `evidence` 和 `missing` 中。

阈值：

```text
CMD-Audit attribution_accuracy > all baselines AND CMD-Audit top2_accuracy > all baselines
```

领域含义：

- 归因准确率衡量精确匹配正确性；top-2 准确率衡量正确标签是否在前两位。
- 两者都必须超过所有基线。这比仅要求其中一个更严格。
- `random_label` 作为合理性检查基线包含在内，但不是有区分力的比较者。

调用者：`check_v0_to_v1_gate`。

---

#### 内部函数：`_check_repair_distribution(repair_path) -> GateCriterion`

位置：`cmd_audit/version_gates.py:400-450`

```python
def _check_repair_distribution(repair_path: Path) -> GateCriterion:
```

目的：

- 检查修复后评估分布是否支持修复有效性声明。

行为：

1. 通过 `_read_repair_csv` 读取 `post_repair_table.csv`。
2. 统计 `recovered`、`partial` 和 `failed` 评估数量。
3. 计算 `recovered_rate = recovered / total`。
4. 计算 `majority_improves = (recovered + partial) > failed`。
5. `passed = recovered_rate >= 0.5 AND majority_improves`。
6. 成功时：evidence 包含计数和比例。
7. 失败时：`missing` 记录哪个子标准失败。
8. 空表（0 行）：`passed=False`，`"Post-repair table is empty"`。
9. 在 `FileNotFoundError` 或 `KeyError` 时：返回 `passed=False`，异常信息放在 `evidence` 和 `missing` 中。

阈值：

```text
recovered_rate >= 0.5 AND recovered + partial > failed
```

为什么两个子标准：

- `recovered_rate >= 0.5`：大多数修复评估应为完全恢复。
- `recovered + partial > failed`：即使恢复率低于 0.5，改进案例总数（recovered + partial）应超过失败案例。此子标准单独不充分——两者必须同时成立。

领域含义：

- `partial` 案例（证据恢复，答案仍错误）是正向诊断信号——它们暴露耦合失败。
- 阈值要求 BOTH 恢复率过半 AND 改进多于失败。
- 此两部分测试防止取巧：不能通过 10% recovered + 41% partial（rec+partial > failed 但 recovered_rate < 0.5），也不能通过 50% recovered + 0% partial + 50% failed（rec+partial == failed）。

调用者：`check_v0_to_v1_gate`。

---

### `cmd_audit/writers.py`（Issue 0010 相关部分）

#### 函数：`write_text_artifact(path, lines, sandbox_root=None) -> Path`

位置：`cmd_audit/writers.py:55-67`

```python
def write_text_artifact(
    path: str | Path,
    lines: Iterable[str],
    *,
    sandbox_root: str | Path | None = None,
) -> Path:
```

目的：

- 通用文本产出物写入器，可选沙箱校验。被 `write_gate_status` 和 `write_gate_review` 使用。
- 若 `sandbox_root` 不为 None，通过 `validate_sandbox_path` 校验路径在沙箱内。
- 按需创建父目录，写入 `"\n".join(lines) + "\n"`。

调用者：`write_gate_status`（第 181 行）、`write_gate_review`（第 209 行）。

## `cmd_audit/__init__.py` 公共接口

Issue 0010 导出：

- `GateCriterion`
- `GateResult`
- `GateReview`
- `check_v0_to_v1_gate`
- `check_v1_to_v2_gate`
- `write_gate_status`
- `write_gate_review`

为什么导出：

- 测试可通过公共接口导入和验证关卡行为。
- 未来的 issue（如 CI 集成、自动关卡检查）可使用稳定的 API。
- HITL 审核流水线可从脚本调用，无需导入内部 helper。

## 测试级合约

测试文件：`tests/test_cmd_audit_issue10_version_gates.py`。14 个测试类，48 个测试方法。

### `GateCriterionCreationTest`（3 个测试）

| 测试方法 | 验证内容 |
| --- | --- |
| `test_passed_criterion` | `passed=True` 的 GateCriterion 具有正确的字段值；`missing` 为空。 |
| `test_failed_criterion_with_missing` | `passed=False` 的 GateCriterion 有非空 `missing`；`evidence` 即使在失败时也记录观察值。 |
| `test_criterion_immutable` | GateCriterion 是冻结的；字段赋值抛出异常。 |

### `GateResultCreationTest`（3 个测试）

| 测试方法 | 验证内容 |
| --- | --- |
| `test_result_all_passed_true` | 所有标准通过 → `all_passed=True`。 |
| `test_result_all_passed_false` | 任一标准失败 → `all_passed=False`。 |
| `test_result_immutable` | GateResult 是冻结的；字段赋值抛出异常。 |

### `GateReviewCreationTest`（3 个测试）

| 测试方法 | 验证内容 |
| --- | --- |
| `test_valid_review` | `decision="approved"` 的 GateReview 创建成功。 |
| `test_review_rejects_invalid_decision` | `decision="maybe_later"` 抛出 `ValueError`。 |
| `test_deferred_review_with_missing` | deferred 审核的 `missing_evidence` 正确存储。 |

### `ComparisonCSVReaderTest`（2 个测试）

| 测试方法 | 验证内容 |
| --- | --- |
| `test_reads_all_systems` | `_read_comparison_csv` 正确解析以 system_name 为键的行和浮点数值。 |
| `test_missing_file_raises` | 缺失文件抛出 `FileNotFoundError`。 |

### `ConfusionCSVReaderTest`（2 个测试）

| 测试方法 | 验证内容 |
| --- | --- |
| `test_reads_matrix` | `_read_confusion_csv` 正确解析以 gold_label 为键的行和整数计数。 |
| `test_missing_file_raises` | 缺失文件抛出 `FileNotFoundError`。 |

### `RepairCSVReaderTest`（1 个测试）

| 测试方法 | 验证内容 |
| --- | --- |
| `test_reads_assessments` | `_read_repair_csv` 按顺序返回 `repair_assessment` 字符串列表。 |

### `MacroF1CheckTest`（4 个测试）

| 测试方法 | 验证内容 |
| --- | --- |
| `test_passes_when_cmd_beats_all_baselines` | CMD macro_f1=0.92 > 所有基线（0.78, 0.80, 0.17）→ 通过。 |
| `test_fails_when_baseline_beats_cmd` | CMD macro_f1=0.70 < evidence_recall=0.85 → 失败，missing 包含 "0.70"。 |
| `test_fails_when_cmd_missing_from_csv` | CSV 没有 CMD-Audit 行 → 失败。 |
| `test_fails_when_artifact_missing` | 文件不存在 → 失败，missing 包含 "not found"。 |

### `ConfusionDiagonalCheckTest`（2 个测试）

| 测试方法 | 验证内容 |
| --- | --- |
| `test_passes_with_perfect_diagonal` | 所有 6 个标签的单位矩阵（diagonal=1, off-diagonal=0）→ 通过。 |
| `test_fails_with_off_diagonal` | write_error 行有 2 个非对角线项 → 失败，missing 包含 "write_error"。 |

### `AccuracyTop2CheckTest`（3 个测试）

| 测试方法 | 验证内容 |
| --- | --- |
| `test_passes_when_cmd_beats_all` | CMD accuracy=0.95 且 top2=1.0 > 所有基线 → 通过。 |
| `test_fails_when_accuracy_lower` | CMD accuracy=0.70 < evidence_recall=0.83 → 失败。 |
| `test_fails_when_top2_lower` | CMD top2=0.65 < evidence_recall=0.83 → 失败。 |

### `RepairDistributionCheckTest`（5 个测试）

| 测试方法 | 验证内容 |
| --- | --- |
| `test_passes_with_high_recovery` | 3 recovered、1 partial、1 failed → recovered_rate=0.6, rec+partial(4) > failed(1) → 通过。 |
| `test_fails_when_below_recovery_threshold` | 1 recovered、2 partial、1 failed → recovered_rate=0.25 < 0.5 → 失败（rec+partial > failed 单独不充分）。 |
| `test_fails_when_recovery_rate_low` | 1 recovered、4 failed → recovered_rate=0.2 < 0.5 AND rec+partial(1) < failed(4) → 失败。 |
| `test_fails_when_failed_dominates` | 1 recovered、2 failed → rec+partial(1) <= failed(2) → 失败。 |
| `test_fails_with_empty_table` | 仅含表头的 CSV，无数据行 → 失败，"Post-repair table is empty"。 |

### `V0V1GateCheckWithRealArtifactsTest`（4 个测试）

| 测试方法 | 验证内容 |
| --- | --- |
| `test_all_criteria_pass_with_current_artifacts` | 针对真实项目产出物的 `check_v0_to_v1_gate()` 返回 `all_passed=True`，含 4 个标准。 |
| `test_criterion_ids_match_spec` | 四个标准 ID 按预期顺序精确匹配规格。 |
| `test_result_is_immutable` | 返回的 GateResult 是冻结的。 |
| `test_each_criterion_has_evidence` | 所有标准有非空 `evidence` 字符串。 |

### `V0V1GateCheckWithTempArtifactsTest`（5 个测试）

| 测试方法 | 验证内容 |
| --- | --- |
| `test_all_pass_with_passing_artifacts` | 端到端：通过临时产出物 → `all_passed=True`。 |
| `test_fails_when_comparison_missing` | 删除 `comparison_metrics.csv` → 2 个标准失败（macro_f1 和 accuracy_top2 都依赖它）。 |
| `test_fails_when_confusion_missing` | 删除 `attribution_confusion_matrix.csv` → confusion_diagonal_dominance 失败。 |
| `test_fails_when_repair_missing` | 删除 `post_repair_table.csv` → repair_assessment_distribution 失败。 |
| `test_fails_when_macro_f1_insufficient` | 覆盖 comparison_metrics，CMD macro_f1=0.50 < baselines → macro_f1 标准失败。 |

### `V1V2GateCheckTest`（2 个测试）

| 测试方法 | 验证内容 |
| --- | --- |
| `test_returns_not_met_stub` | `check_v1_to_v2_gate()` 返回 `all_passed=False`，包含一个标准 `adapter_integration_count`。Evidence 包含 "0 adapter integrations"。 |
| `test_result_has_timestamp` | 结果有包含 "T" 分隔符的 ISO 时间戳。 |

### `GateStatusWriteTest`（4 个测试）

| 测试方法 | 验证内容 |
| --- | --- |
| `test_writes_status_file` | `write_gate_status` 写入包含 "V0→V1" 和 "PASS" 的文件。 |
| `test_output_contains_all_criteria` | 输出包含通过和失败标准的 ID、PASS/FAIL 状态标签和 missing 文本。 |
| `test_sandbox_path_enforced` | 写入沙箱外部路径抛出 `ValueError`。 |
| `test_creates_parent_directories` | 写入沙箱内深层嵌套路径创建父目录并成功。 |

### `GateReviewWriteTest`（3 个测试）

| 测试方法 | 验证内容 |
| --- | --- |
| `test_writes_review_file` | `write_gate_review` 写入包含关卡 ID、decision 和 reviewer 的文件。 |
| `test_dated_review_format` | 审核文件包含日期、deferred 决策、rationale 和 missing evidence 文本。 |
| `test_sandbox_path_enforced` | 写入沙箱外部路径抛出 `ValueError`。 |

### `GatesDoNotBlockImplementationTest`（2 个测试）

| 测试方法 | 验证内容 |
| --- | --- |
| `test_gate_check_runs_independently` | `check_v0_to_v1_gate()` 运行时不导入 harness、baselines 或其他实现模块。函数本身不写入磁盘。 |
| `test_v1_v2_stub_does_not_crash` | `check_v1_to_v2_gate()` 返回有效 GateResult 而不崩溃。 |

## 产出物合约

### `artifacts/sandbox/V0V1_gate_status.txt`

由 `write_gate_status` 写入的结构化文本文档。内容：关卡 ID、检查日期、`All criteria passed` 标志、四个逐标准块（编号、ID、PASS/FAIL 状态）、描述、产出物路径、阈值、证据和缺失内容。页脚包含 "Final decision: HITL review required." 和 ISO 检查时间戳。

### `artifacts/sandbox/V0V1_gate_review.txt`

由 `write_gate_review` 写入的带日期 HITL 审核记录。内容：关卡 ID、日期、reviewer、decision（approved/deferred/rejected）、reviewed 时间戳、rationale 和 missing evidence 部分。

## 边界规则

1. **关卡检查只读**：`check_v0_to_v1_gate` 和 `check_v1_to_v2_gate` 不修改任何产出物或写入磁盘。它们是纯评估函数。

2. **沙箱写入边界**：`write_gate_status` 和 `write_gate_review` 通过 `write_text_artifact` → `validate_sandbox_path` 强制沙箱写入边界。写入沙箱外部路径抛出 `ValueError`。

3. **HITL 最终决策**：`GateResult.all_passed` 是程序性摘要，不是最终关卡决策。最终决策由 `GateReview.decision`（approved/deferred/rejected）通过 HITL 审核确定。

4. **关卡不阻塞实现**：关卡检查函数与其他模块无依赖（不导入 harness、baselines、replays、models）。它们仅锁定版本声明，不阻塞开发工作。

5. **V1→V2 存根**：V1→V2 关卡检查始终返回 not-met。它作为 V1 路线图的前向引用，不作为可测试的关卡。0 个适配器集成存在于 V0。

6. **直接 CSV 读取**：关卡检查直接读取 CSV 文件，不通过 CMD harness。这确保治理层独立于归因/回放/修复层。

7. **双输出格式**：关卡状态同时存在于 (a) `artifacts/sandbox/V0V1_gate_status.txt`（临时程序输出）和 (b) `cmd_innovation_core/gates/V0V1_gate_status.md`（持久 HITL 记录）。两者互补，各有价值——.txt 文件自动生成，.md 文件人工维护。

## 验收标准可追溯性

| Issue 0010 AC | 代码接口 | 测试接口 |
| --- | --- | --- |
| V0→V1 关卡定义四个标准。 | `check_v0_to_v1_gate` 运行四个标准检查；`V0V1_CRITERION_IDS` 记录四个 ID。 | `test_criterion_ids_match_spec`、`test_all_criteria_pass_with_current_artifacts` |
| V1→V2 关卡定义适配器集成标准。 | `check_v1_to_v2_gate` 返回含 `adapter_integration_count` 的单标准 GateResult。 | `test_returns_not_met_stub` |
| 关卡状态在专用文档中追踪，非代码中。 | `cmd_innovation_core/gates/V0V1_gate_status.md` 是追踪文档。`write_gate_status` 产生沙箱产出物。 | `test_writes_status_file` |
| 每次关卡检查记录带日期审核记录。 | `GateReview` 记录 gate_id、reviewer、decision、rationale、missing_evidence 和 reviewed_at 时间戳。`write_gate_review` 产生带日期的审核产出物。 | `test_dated_review_format`、`test_writes_review_file` |
| 若关卡未通过，记录具体缺失证据。 | `GateCriterion.missing` 记录缺失内容；`GateReview.missing_evidence` 记录需要解决的问题。 | `test_failed_criterion_with_missing`、`test_deferred_review_with_missing` |
| 关卡不阻塞进行中的实现。 | `check_v0_to_v1_gate` 是只读函数，不写入磁盘或影响其他模块。`version_gates.py` 不从 harness 或 baselines 导入。 | `test_gate_check_runs_independently` |

## 当前产出物语义

当前 `artifacts/sandbox/V0V1_gate_status.txt`：

```text
CMD V0→V1 Gate Status — 2026-05-10
============================================================

All criteria passed: True

Criterion 1: macro_f1_exceeds_baselines [PASS]
  ...
  Evidence:    CMD-Audit macro_f1=1.000; evidence_recall=0.778; subagent_judge=0.778; random_label=0.167

Criterion 2: confusion_diagonal_dominance [PASS]
  ...
  Evidence:    All 6 V0 labels have diagonal > off-diagonal sum

Criterion 3: accuracy_top2_exceeds_baselines [PASS]
  ...
  Evidence:    CMD-Audit attribution_accuracy=1.000 (best baseline=0.833); CMD-Audit top2_accuracy=1.000 (best baseline=0.833)

Criterion 4: repair_assessment_distribution [PASS]
  ...
  Evidence:    6 cases: recovered=6, partial=0, failed=0 (recovered_rate=1.000)
```

当前 `artifacts/sandbox/V0V1_gate_review.txt`：

```text
CMD V0→V1 Gate Review — 2026-05-10
============================================================

Reviewer:   HITL
Decision:   deferred
...

Rationale:
  All four criteria pass on 6-case smoke suite. However, the PRD targets 50-100 probe cases...
```

解读：

- 全部四个标准在 6 案例烟雾套件上通过，因为套件足够小以至于产生天花板效应（完美 macro F1、完美混淆矩阵、100% 恢复率）。
- HITL 审核被推迟，因为 6 个案例（每标签 1 个）不足以支撑论文声明。PRD 目标为 50-100 案例。
- 关卡检查基础设施已可运行。随着探针套件扩展，它将产生有区分力的结果：
  - Macro F1 将从 1.000 退化到现实值。
  - 当多个案例共享同一标签时，非对角线混淆项将出现。
  - 更复杂的案例将出现 `partial` 和 `failed` 修复评估。
- V0→V1 关卡尚未锁定。版本保持未锁定直到 HITL 批准关卡审核。

## 验证

命令：

```bash
# 仅 issue 0010 测试（48 个测试）
python3 -m pytest tests/test_cmd_audit_issue10_version_gates.py -v

# 全量测试套件
python3 -m pytest tests/ -q

# 编译检查
python3 -m compileall cmd_audit tests
```

预期状态（2026-05-14）：

- 全部 48 个 issue 0010 测试通过。
- 全部 218 个测试通过（含 178 个子测试）。
- `artifacts/sandbox/V0V1_gate_status.txt` 已生成。
- `artifacts/sandbox/V0V1_gate_review.txt` 已生成。
- `cmd_innovation_core/gates/V0V1_gate_status.md` 是关卡追踪文档。

## Non-Goals 保留

- 无生产 CI/CD 集成（关卡在本地检查，非流水线中）。
- 无自动版本锁定（需要 HITL 决策）。
- 无阻塞实现工作（关卡仅锁定版本声明，非开发）。
- 无 V0 标签集或归因分类法扩展。
- 无证据产出物修改（关卡检查只读）。
- 无对 CMD harness、baselines、replays 或 models 的依赖（直接读取 CSV 文件）。
- 无远程关卡状态追踪（仅本地文档）。

## 后续技术步骤

Issue 0010 完成了 V0 CMD-Audit 治理层。V0 证据链现已结构完整：

1. `attribution_table.csv` + `comparison_metrics.csv` + `attribution_confusion_matrix.csv` — issues 0002/0003
2. Post-Repair Context Replay 表 — issue 0005
3. 针对性修复成功表 + 声明账本 — issue 0006
4. ECS Failure Memory 复发率对比 — issue 0007
5. 检索基线与证据评分强化（V0.5）— issue 0008
6. 证据驱动版本关卡 — issue 0010

全部四个 V0→V1 关卡标准在 6 案例烟雾套件上通过。HITL 审核因探针套件扩展而推迟。

V0 CMD-Audit 证据链通过 issues 0001-0010 闭合（218 个测试通过）。下一个关键工作：将探针套件从 6 个扩展到 50-100 个案例，为 V0→V1 关卡审核产生有区分力的证据。并行工作：推进 V1 标签扩展（issues 0011-0013）和适配器集成（issues 0014-0015）。

## 后续依赖此 Issue 的问题

| Issue | 依赖 | 方式 |
| --- | --- | --- |
| Issues 0011-0017（V1 标签和适配器） | V0→V1 关卡提供证据基线 | V1 工作完成后，重新运行 V0→V1 关卡以验证无退化。 |
| Issue 0015（Letta 适配器 + V1→V2 关卡） | V1→V2 存根定义 | `check_v1_to_v2_gate` 扩展为实际检查，要求 ≥2 适配器集成且无 macro F1 退化。 |
| 探针套件扩展 | V0→V1 关卡审核 | 50-100 案例套件是 HITL 从 "deferred" 到 "approved" 的前提条件。 |
