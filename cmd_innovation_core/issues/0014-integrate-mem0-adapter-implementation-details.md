# Issue 0014 实现细节：mem0 Adapter 集成

## 目的

本文档是 issue 0014《Integrate mem0 as First CMD-Skill Adapter Target》的全局实现地图。它映射每个函数、数据类、异常和常量到其确切的源码位置、签名、行为、调用者和领域含义。

Issue 0014 构建了 `Mem0Adapter`，这是第一个 CMD-Skill Adapter 目标，在 mem0 的 `add()` 和 `search()` 操作上设置两个拦截切点，使 CMD-Audit 的 counterfactual replay 组合能够针对模拟的 mem0 支持代理运行。它验证了无论记忆操作来自夹具控制的 ProbeCase 数据还是被拦截的 mem0 调用，CMD 的归因逻辑都能产生完全相同的结果。

```text
Standalone Harness（现有，未更改）:
  ProbeCase → run_v0_replay_portfolio / run_v1_replay_portfolio → assign_attribution → AuditResult

mem0 Adapter Path（issue 0014，新增）:
  ProbeCase + Mem0Trace → Mem0Adapter(intercept_add, intercept_search)
    → run_mem0_replay_portfolio (6 intercepted + 4 V1 passthrough)
    → assign_attribution_v1 → AuditResult
```

核心架构决策：**V1 使用录制轨迹集成模式**——mem0 操作是预先录制的，而非实时的。实时 mem0 集成是 V2 的范围。这避免了对运行中 mem0 实例的需求，同时验证了切点拦截语义。

Issue 0013（耦合失败重新校准）已在此工作开始前完成（387 项测试通过），解除了阻塞。V1 依赖链为：0013 → 0014 → 0015（Letta 适配器 + V1→V2 关卡）。

## 源需求

本实现遵循以下本地文档。

| 来源 | 在 Issue 0014 中应用的需求 |
| --- | --- |
| `TASK.md` | V1 适配器集成：mem0 作为第一个 CMD-Skill Adapter 目标。"两个切点"架构：`intercept_add()` 用于写入侧回放（oracle_write、oracle_compression、verbatim_event_oracle、injection_oracle），`intercept_search()` 用于检索侧回放（oracle_retrieval、evidence_given_reasoning）。V1 使用录制轨迹模式。沙箱保证：SHA-256 校验和验证，在任何回放后永不改变原始存储。Adapter-label 完全一致：6 个 V0 烟雾案例必须通过独立工具和适配器路径产生相同的 `predicted_label`。 |
| `CLAUDE.md` | V1 问题 0014 状态：AFK，被 0013 阻塞（现已解除）。适配器层（`cmd_audit/adapters/`）预留了 V2 实时集成扩展点。将 CMD-Audit 与 CMD-Skill Adapter 分开：CMD-Audit 写入限制在回放本地沙箱内。 |
| `cmd_innovation_core/issues/README.md` | Issue 0014 是 V1 依赖图中的适配器集成层，位于 0013（耦合故障）和 0015（Letta）之间。30 项行为级测试，adapter-label 一致性已确认。 |
| `cmd_innovation_core/issues/0014-integrate-mem0-adapter.md` | 七个验收标准：Mem0Trace 加载与验证、`intercept_add` 路由正确性、`intercept_search` 路由正确性、沙箱校验和不变性、adapter-label 完全一致（全部 6 个 V0 案例）、端到端流水线、V0/V1 边界尊重。 |
| `knowledge/current-memory.md` | mem0 adapter（issue 0014）：`Mem0Adapter` 在 `add()` 和 `search()` 两个切点拦截 mem0 操作，支持 6 个 V0 counterfactual replay 通过 adapter 路径运行。使用录制轨迹模式（V1），沙箱校验和验证（永不修改原始存储），adapter-label 在 V0 smoke suite 上与 standalone harness 完全一致。 |

## 领域边界

Issue 0014 在现有 V0/V1 回放组合、归因层和 ECS 流水线之上构建适配器拦截层。它不更改任何现有的回放逻辑、归因逻辑或修复流水线。它添加了一个新的输入源（被拦截的 mem0 操作），重用现有的回放组合和归因引擎。

```text
ReplayEngine, Attribution, ECS layers（现有，未更改）
  → 只有输入源改变：从夹具控制的记忆操作变为被拦截的 mem0 操作

mem0 Adapter Path（issue 0014，新增）
  → run_mem0_replay_portfolio(case, adapter) → 10 个回放结果
  → run_case_with_mem0(case, trace) → AuditResult
  → run_cases_with_mem0(cases, traces) → [AuditResult]
```

Issue 0014 拥有的内容：

- `ReplayName` 类型别名（6 个 V0 回放名称的字面量联合类型）。
- `StoreChecksum` 冻结数据类（`checksum: str`、`item_count: int`），用于沙箱验证。
- `SandboxViolationError(RuntimeError)`：当适配器回放尝试改变支持存储时抛出。
- `Mem0Trace` 冻结数据类：一个探针案例的预录制 mem0 操作轨迹。
- `Mem0Trace.from_dict(d) -> Mem0Trace`：从 JSON 兼容字典反序列化的类方法。
- `load_mem0_traces(path) -> dict[str, Mem0Trace]`：从 JSON 文件加载按 case_id 键控的轨迹。
- `Mem0Adapter` 类：核心两个切点拦截器。
- `Mem0Adapter.intercept_add(case_id, original_facts, replay) -> list[str]`：写入侧回放路由。
- `Mem0Adapter.intercept_search(case_id, original_query, original_results, replay) -> list[MemoryItem]`：检索侧回放路由。
- `Mem0Adapter.original_add_inputs`（属性）：原始 `add()` 输入事实。
- `Mem0Adapter.original_search_query`（属性）：原始 `search()` 查询字符串。
- `Mem0Adapter.original_search_results`（属性）：原始 `search()` 结果。
- `Mem0Adapter.get_store_snapshot() -> StoreChecksum`：当前沙箱存储校验和。
- `Mem0Adapter.verify_sandbox() -> None`：如果校验和与预计算值偏离则抛出。
- `run_mem0_replay_portfolio(case, adapter) -> tuple[ReplayResult, ...]`：10 回放组合（6 个被拦截 + 4 个 V1 直通）。
- `run_case_with_mem0(case, trace, *, top_k) -> AuditResult`：通过 mem0 适配器路径的 V1 流水线入口点。
- `run_cases_with_mem0(cases, traces, *, top_k) -> list[AuditResult]`：批量运行器。
- `check_v1_to_v2_gate(mem0_integrated=True)`：更新后的 V1→V2 关卡，承认 mem0 集成。
- 6 个轨迹条目的 JSON 轨迹夹具文件和 6 个测试类包含 30 个测试方法。

Issue 0014 不拥有的内容（属于其他 issue）：

- 更改 V0 或 V1 回放组合或归因阈值（issues 0001、0003、0011、0012）。
- 更改基线套件（issue 0002）。
- 更改 ECS Failure Memory 逻辑（issue 0007）。
- 添加新的管道标签（issues 0011、0012）。
- 实时 mem0 集成（V2 范围）。
- Letta 适配器（issue 0015）。
- RPE 预过滤器（issue 0016）。
- 来源追踪（issue 0017）。

## 当前代码产出物

| 产出物 | 在 Issue 0014 中的角色 |
| --- | --- |
| `cmd_audit/adapters/__init__.py` | 包初始化，导出 8 个公共适配器符号。 |
| `cmd_audit/adapters/base.py` | 共享适配器类型：`ReplayName`、`StoreChecksum`、`SandboxViolationError`、`Mem0Trace`、`load_mem0_traces`。 |
| `cmd_audit/adapters/mem0_adapter.py` | 核心 `Mem0Adapter` 类，包含 `intercept_add`、`intercept_search`、`get_store_snapshot`、`verify_sandbox`、公共属性。 |
| `cmd_audit/adapters/mem0_replays.py` | 适配器感知回放函数：`run_mem0_replay_portfolio` 和 6 个私有辅助函数，使用 `Mem0Adapter` 拦截。 |
| `cmd_audit/adapters/harness.py` | 适配器工具入口点：`run_case_with_mem0`、`run_cases_with_mem0`。 |
| `cmd_audit/replays.py` | 三个公共别名（`score_recovered_evidence`、`recover_extracted_gold_evidence`、`recover_raw_event_only_gold_evidence`），暴露给适配器层。 |
| `cmd_audit/version_gates.py` | `check_v1_to_v2_gate` 现在接受 `mem0_integrated` 参数，报告 1 个适配器集成。 |
| `cmd_audit/__init__.py` | 从 `cmd_audit/adapters/` 新增 8 个公共导出。 |
| `data/probe_cases/mem0_v0_smoke_traces.json` | 6 个预录制 mem0 轨迹，每个 V0 烟雾案例一个。 |
| `tests/test_cmd_audit_issue14_mem0_adapter.py` | 6 个测试类，30 个测试方法，90 个子测试。 |

## 模块地图

| 模块 | Issue 0014 角色 |
| --- | --- |
| `cmd_audit/adapters/__init__.py` | **新增。** 包初始化。导出全部 8 个公共适配器符号。 |
| `cmd_audit/adapters/base.py` | **新增。** 共享适配器类型。拥有 `ReplayName`、`StoreChecksum`、`SandboxViolationError`、`Mem0Trace`、`load_mem0_traces`。这些类型是无依赖的，将在 issue 0015（Letta）、issue 0016（RPE）及后续问题中被未来的适配器目标重用。 |
| `cmd_audit/adapters/mem0_adapter.py` | **新增。** 核心适配器类。拥有 `Mem0Adapter` 类，包含 `intercept_add`、`intercept_search`、`get_store_snapshot`、`verify_sandbox` 以及三个公共属性（`original_add_inputs`、`original_search_query`、`original_search_results`）。 |
| `cmd_audit/adapters/mem0_replays.py` | **新增。** 适配器感知回放函数。拥有 `run_mem0_replay_portfolio`（10 个回放）和 6 个私有 `_run_mem0_*` 辅助函数。V1 回放（`oracle_route`、`oracle_granularity`、`graph_off`、`safety_off`）从 `cmd_audit.replays` 直接导入并原样运行。 |
| `cmd_audit/adapters/harness.py` | **新增。** 适配器工具入口点。拥有 `run_case_with_mem0` 和 `run_cases_with_mem0`。这些镜像了 `cmd_audit/harness.py` 中的 `run_case_v1` / `run_cases_v1`，但用 `run_mem0_replay_portfolio` 替代了 `run_v1_replay_portfolio`。 |
| `cmd_audit/replays.py` | **已修改。** 在 `_recover_extracted_gold_evidence` 和 `_recover_raw_event_only_gold_evidence` 之后添加了三个模块级公共别名（第 229-231 行）：`score_recovered_evidence`、`recover_extracted_gold_evidence`、`recover_raw_event_only_gold_evidence`。现有的 10 个 V0/V1 回放保持不变。 |
| `cmd_audit/version_gates.py` | **已修改。** `check_v1_to_v2_gate`（第 116 行）现在接受可选的 `mem0_integrated: bool = False` 参数。当为 `True` 时，报告 1 个适配器集成并注明 Letta 仍需。 |
| `cmd_audit/__init__.py` | **已修改。** 从 `cmd_audit.adapters` 导入并重新导出 8 个符号：`Mem0Adapter`、`Mem0Trace`、`SandboxViolationError`、`StoreChecksum`、`load_mem0_traces`、`run_case_with_mem0`、`run_cases_with_mem0`、`run_mem0_replay_portfolio`。 |
| `tests/test_cmd_audit_issue14_mem0_adapter.py` | 6 个测试类，30 个测试方法，90 个子测试。 |

## 调用图

### mem0 Adapter 归因流水线（issue 0014）

```text
cmd_audit/adapters/harness.py
  → harness.run_case_with_mem0(ProbeCase, Mem0Trace, top_k=2)
      → Mem0Adapter(trace, case.gold_evidence, case.extracted_memory, case.raw_events)
          → 存储 _pre_checksum = trace.store_checksum
      → baselines.run_baseline_suite(ProbeCase)              （现有，未更改）
      → mem0_replays.run_mem0_replay_portfolio(ProbeCase, Mem0Adapter)
          → _run_mem0_oracle_write(case, adapter)
              → adapter.original_add_inputs                   （公共属性）
              → adapter.intercept_add(case_id, facts, "oracle_write")
                  → 返回金标证据文本，其中 source_memory_id is None 且 source_event_id is None
              → replays._score_recovered_evidence(case, "oracle_write", evidence_block)
          → _run_mem0_oracle_compression(case, adapter)
              → adapter.original_add_inputs
              → adapter.intercept_add(case_id, facts, "oracle_compression")
                  → 对于每个金标证据：如果 source_memory_id 存在且 memory 文本的证据召回率 < 1.0，恢复 evidence.text
              → replays._score_recovered_evidence(case, "oracle_compression", evidence_block)
          → _run_mem0_verbatim_event_oracle(case, adapter)
              → adapter.intercept_add(case_id, adapter.original_add_inputs, "verbatim_event_oracle")
                  → 返回 []（绕过标志——调用者在上下文输入中处理原始事件注入）
              → replays.recover_raw_event_only_gold_evidence(case)
                  → 来自 replays.py 的共享辅助函数
              → replays._score_recovered_evidence(case, "verbatim_event_oracle", evidence_block)
          → _run_mem0_oracle_retrieval(case, adapter)
              → adapter.original_search_results
              → adapter.original_search_query
              → adapter.intercept_search(case_id, query, results, "oracle_retrieval")
                  → 返回尚未在 original_results 中的金标证据 MemoryItem
              → replays._score_recovered_evidence(case, "oracle_retrieval", evidence_block)
          → _run_mem0_injection_oracle(case, adapter)
              → 如果 baseline.evidence_score >= 1.0：返回空结果
              → adapter.original_add_inputs
              → adapter.intercept_add(case_id, facts, "injection_oracle")
                  → 返回在 search_results 中被检索到的金标证据的 MemoryItem.text 值
              → replays._score_recovered_evidence(case, "injection_oracle", evidence_block)
          → _run_mem0_evidence_given_reasoning(case, adapter)
              → 如果 baseline.evidence_score >= 1.0 且 baseline.answer_score < 1.0：
                  → adapter.original_search_results, adapter.original_search_query
                  → adapter.intercept_search(case_id, query, results, "evidence_given_reasoning")
                      → 返回 original_results + 金标证据 MemoryItem（已去重）
              → 否则：evidence_block = ""
              → replays._score_recovered_evidence(case, "evidence_given_reasoning", evidence_block)
          → replays.run_oracle_route(case)                     （V1 直通，未更改）
          → replays.run_oracle_granularity(case)               （V1 直通，未更改）
          → replays.run_graph_off(case)                        （V1 直通，未更改）
          → replays.run_safety_off(case)                       （V1 直通，未更改）
          → adapter.verify_sandbox()
              → adapter.get_store_snapshot()
                  → SHA-256(sorted(add_inputs).join("|"))
              → 如果 current.checksum != _pre_checksum：抛出 SandboxViolationError
      → attribution.assign_attribution_v1(replays, has_ingestion_trace=..., top_k=top_k)
          （现有 V1 归因，未更改）
      → AuditResult(...)                                       （相同输出形状）
```

### Standalone 现有流水线（未更改，供参考）

```text
cmd_audit/harness.py
  → harness.run_case_v1(ProbeCase)
      → baselines.run_baseline_suite(ProbeCase)
      → replays.run_v1_replay_portfolio(ProbeCase)             （10 个回放）
      → attribution.assign_attribution_v1(replays, ...)        （相同归因引擎）
      → AuditResult(...)
```

### 行为测试路径

```text
tests/test_cmd_audit_issue14_mem0_adapter.py
  → base.load_mem0_traces(path)                               （Mem0TraceValidationTest）
  → Mem0Trace.from_dict(d)                                    （Mem0TraceValidationTest）
  → Mem0Adapter(trace, gold_evidence, extracted_memory, raw_events)
      → Mem0AdapterInterceptionTest, Mem0AdapterSandboxTest
  → adapter.intercept_add(case_id, facts, replay)              （Mem0AdapterInterceptionTest）
  → adapter.intercept_search(case_id, query, results, replay)  （Mem0AdapterInterceptionTest）
  → adapter.get_store_snapshot()                               （Mem0AdapterSandboxTest）
  → adapter.verify_sandbox()                                   （Mem0AdapterSandboxTest）
  → harness.run_case_with_mem0(case, trace)                    （AdapterLabelParityTest, Mem0AdapterEndToEndTest）
  → harness.run_mem0_replay_portfolio(case, adapter)           （Mem0AdapterEndToEndTest）
  → labels.validate_v0_label / validate_v1_label               （Mem0AdapterV0V1BoundaryTest）
```

## 数据流

### 输入夹具

```text
data/probe_cases/v0_issue3_cases.json                         # 六案例 V0 烟雾套件（独立工具基线）
data/probe_cases/mem0_v0_smoke_traces.json                    # 六个预录制 mem0 轨迹（适配器输入）
```

### mem0_v0_smoke_traces.json 的结构

每个条目映射 `case_id` → mem0 操作的预录制轨迹：

| 字段 | 类型 | 目的 |
| --- | --- | --- |
| `case_id` | `str` | 与相应的 V0 烟雾案例夹具匹配的唯一案例标识符 |
| `add_inputs` | `[str, ...]` | mem0 在原始失败运行期间传递给其 `add()` 调用的事实（提取并存储的文本） |
| `search_query` | `str` | 传递给 mem0 的 `search()` 的查询字符串 |
| `search_results` | `[{memory_id, text, ...}, ...]` | mem0 的 `search()` 返回的 MemoryItem 对象——基线检索输出 |
| `store_checksum` | `str` | 由所有存储事实的排序、管道连接的 SHA-256 组成的确定性哈希 |

### 适配器拦截路由表

六个 V0 回放通过 `Mem0Adapter` 在两个切点拦截。路由逻辑镜像了独立的回放函数。

#### Cut Point A：`intercept_add` 路由

| 回放名称 | `intercept_add` 返回 | 领域含义 |
| --- | --- | --- |
| `oracle_write` | 金标证据文本，其中 `source_memory_id is None` 且 `source_event_id is None` | 证据从未进入记忆系统——将其作为新记忆提供 |
| `oracle_compression` | 金标证据文本，其中源记忆项的证据召回率 < 1.0 | 压缩后的记忆项丢失了细节——提供未压缩的原始文本 |
| `verbatim_event_oracle` | `[]`（空列表） | 绕过标志：调用者处理原始事件注入到上下文中 |
| `injection_oracle` | 从检索到的记忆项中恢复的 MemoryItem 文本 | 正确格式化的证据本应被注入 |
| 所有其他（直通） | 原样返回 `original_facts` | 此回放不操作写入侧 |

#### Cut Point B：`intercept_search` 路由

| 回放名称 | `intercept_search` 返回 | 领域含义 |
| --- | --- | --- |
| `oracle_retrieval` | 具有可恢复证据的记忆项，这些证据**不在** `original_results` 中 | 存在于存储中但基线检索遗漏的记忆项 |
| `evidence_given_reasoning` | `original_results` + 去重后附加的金标证据 MemoryItem | 所有证据在上下文中——推理步骤现在应该能产生正确答案 |
| 所有其他（直通） | 原样返回 `original_results` | 此回放不操作检索侧 |

### 数据流步骤示例：v0-retrieval-001 通过适配器

此案例诊断 `retrieval_error`：正确的记忆项存在但未被基线检索返回。

1. `load_probe_cases` 加载案例夹具：`extracted_memory[0]`（mem-301："Mira chose Lisbon for the Q3 offsite"），`extracted_memory[1]`（mem-302："Porto was considered for the Q3 offsite but rejected."）。`gold_evidence[0].source_memory_id = "mem-301"`。`baseline_outputs[0].retrieved_memory_ids = ["mem-302"]`（仅检索到干扰项）。
2. `load_mem0_traces` 加载轨迹：`add_inputs = ["Mira chose Lisbon for the Q3 offsite.", "Porto was considered..."]`，`search_results = [mem-302]`（基线仅返回干扰项）。
3. `Mem0Adapter` 构造时存储 `_pre_checksum = trace.store_checksum`。
4. `_run_mem0_oracle_retrieval` 调用 `adapter.original_search_results` → `[mem-302]`。调用 `adapter.intercept_search("v0-retrieval-001", query, [mem-302], "oracle_retrieval")`。
5. `intercept_search` 构建 `original_ids = {"mem-302"}`。遍历 `gold_evidence`：`evidence.source_memory_id = "mem-301"` 不在 `original_ids` 中。查找 `memory_by_id["mem-301"]` → mem-301。`evidence_recall_from_text((evidence,), mem-301.text) >= 1.0` → 是。将 mem-301 追加到 `recovered`。
6. 返回 `[mem-301]`。证据块 = "Mira chose Lisbon for the Q3 offsite."。
7. `_score_recovered_evidence` 找到所有必需的短语（"Mira"、"Lisbon"、"Q3 offsite"）→ `evidence_score = 1.0`。answer = "Lisbon"（与金标准匹配）→ `answer_score = 1.0`。`recovery_gain = 1.0 - 0.0 = 1.0`。
8. `assign_attribution_v1` 将 `oracle_retrieval` 排名为具有最高 recovery_gain → `predicted_label = "retrieval_error"`。
9. 独立工具对 `v0-retrieval-001` 也产生 `retrieval_error`。**Adapter-label parity 已确认。**

### 中间类型

**Mem0Trace**（来自 `cmd_audit/adapters/base.py:42-74`）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `case_id` | `str` | 此轨迹对应的探针案例 ID |
| `add_inputs` | `tuple[str, ...]` | 原始运行期间传递给 mem0 `add()` 的事实 |
| `search_query` | `str` | 原始运行期间传递给 mem0 `search()` 的查询 |
| `search_results` | `tuple[MemoryItem, ...]` | mem0 `search()` 返回的记忆项 |
| `store_checksum` | `str` | 预计算存储校验和（SHA-256），用于沙箱验证 |

**StoreChecksum**（来自 `cmd_audit/adapters/base.py:26-29`）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `checksum` | `str` | 排序事实的 SHA-256 十六进制摘要 |
| `item_count` | `int` | 存储中事实的数量 |

**ReplayName**（来自 `cmd_audit/adapters/base.py:14-21`）：

6 个 V0 回放名称的字面量联合类型：`"oracle_write"`、`"oracle_compression"`、`"verbatim_event_oracle"`、`"oracle_retrieval"`、`"injection_oracle"`、`"evidence_given_reasoning"`。

### 输出产出物

适配器路径运行生成与独立工具相同类型的产出物：

```text
artifacts/sandbox/                          # 适配器路径运行将写入：
  attribution_table.csv                     # 6 标签归因（与独立工具相同）
  comparison_metrics.csv                    # CMD-adapter vs 基线
  attribution_confusion_matrix.csv          # 6x6 混淆矩阵
  post_repair_table.csv                     # 修复后回放结果
  repair_success_table.csv                  # 修复成功对比
```

## 函数级合约

### `cmd_audit/adapters/base.py`

文件：`cmd_audit/adapters/base.py`（82 行）。包含 1 个类型别名、1 个冻结数据类、1 个异常类、1 个轨迹数据类（带类方法）、1 个公共加载器函数。

---

#### 类型别名：`ReplayName`

位置：`cmd_audit/adapters/base.py:14-21`

```python
ReplayName = Literal[
    "oracle_write",
    "oracle_compression",
    "verbatim_event_oracle",
    "oracle_retrieval",
    "injection_oracle",
    "evidence_given_reasoning",
]
```

目的：

- 定义适配器处理的 6 个 V0 回放名称的确切集合。
- 由 `Mem0Adapter.intercept_add` 和 `Mem0Adapter.intercept_search` 用于对 `replay` 参数进行类型检查。
- V1 回放（`oracle_route`、`oracle_granularity`、`graph_off`、`safety_off`）不在此 Literal 中——它们绕过适配器，直接通过（`mem0_replays.py` 将它们作为独立函数调用）。

调用者：

- `Mem0Adapter.intercept_add`（`mem0_adapter.py:48`）——`replay: ReplayName` 参数
- `Mem0Adapter.intercept_search`（`mem0_adapter.py:93`）——`replay: ReplayName` 参数

---

#### 数据类：`StoreChecksum`

位置：`cmd_audit/adapters/base.py:26-29`

```python
@dataclass(frozen=True)
class StoreChecksum:
    checksum: str
    item_count: int
```

目的：

- 在某个时间点捕获存储状态的不可变快照。
- `checksum` 是所有存储事实的排序、管道连接的 SHA-256 哈希。
- `item_count` 是哈希事实的数量。

调用者：

- `Mem0Adapter.get_store_snapshot()`（`mem0_adapter.py:132`）——返回一个 `StoreChecksum` 实例
- `Mem0Adapter.verify_sandbox()`（`mem0_adapter.py:141`）——将当前快照与 `_pre_checksum` 进行比较
- `Mem0AdapterSandboxTest.test_get_store_snapshot_returns_correct_data`——检查字段类型
- `Mem0AdapterSandboxTest.test_store_checksum_unchanged_*`——比较前后快照

---

#### 异常：`SandboxViolationError`

位置：`cmd_audit/adapters/base.py:35-36`

```python
class SandboxViolationError(RuntimeError):
    """Raised when an adapter replay attempts to mutate the backing store."""
```

目的：

- 当存储校验和在回放组合运行后发生变化时抛出，表明适配器意外改变了支持存储。
- 继承自 `RuntimeError`：它表示一个程序逻辑错误，而非预期的失败模式。

引发者：

- `Mem0Adapter.verify_sandbox()`（`mem0_adapter.py:142-145`）

调用者：

- `run_mem0_replay_portfolio()`（`mem0_replays.py:35`）——在所有 10 次回放后调用 `adapter.verify_sandbox()`
- 由 `Mem0AdapterSandboxTest.test_verify_sandbox_detects_checksum_mismatch` 断言

---

#### 数据类：`Mem0Trace`

位置：`cmd_audit/adapters/base.py:42-74`

```python
@dataclass(frozen=True)
class Mem0Trace:
    """Pre-recorded mem0 operations for one probe case."""
    case_id: str
    add_inputs: tuple[str, ...]
    search_query: str
    search_results: tuple[MemoryItem, ...]
    store_checksum: str
```

目的：

- 为单个探针案例存储完整的预录制 mem0 操作。
- 五个字段捕获原始失败运行的全部内容：哪些事实被写入、哪些内容被查询以及返回了哪些内容。
- 冻结（`frozen=True`）以防止适配器代码意外改变轨迹。

##### 类方法：`Mem0Trace.from_dict(d: dict) -> Mem0Trace`

位置：`cmd_audit/adapters/base.py:56-74`

```python
@classmethod
def from_dict(cls, d: dict) -> "Mem0Trace":
    """Build a Mem0Trace from a JSON-compatible dict."""
    return cls(
        case_id=d["case_id"],
        add_inputs=tuple(d["add_inputs"]),
        search_query=d["search_query"],
        search_results=tuple(
            MemoryItem(
                memory_id=item["memory_id"],
                text=item["text"],
                source_event_ids=tuple(item.get("source_event_ids", ())),
                store=item.get("store", "default"),
                is_graph_expanded=item.get("is_graph_expanded", False),
            )
            for item in d["search_results"]
        ),
        store_checksum=d["store_checksum"],
    )
```

目的：

- 从 JSON 反序列化字典构造一个 `Mem0Trace`。
- `add_inputs` 转换为不可变元组。
- `search_results` 中的每个条目转换为完整的 `MemoryItem` 实例（处理 `source_event_ids`、`store`、`is_graph_expanded` 的所有默认值）。
- 在单个位置处理所有 JSON→Python 转换，将 JSON 模式细节与适配器逻辑隔离。

字段映射：

| JSON 键 | Python 类型 | 默认值 | 备注 |
| --- | --- | --- | --- |
| `case_id` | `str` | 必需 | 必须与探针案例夹具中的 `case_id` 匹配 |
| `add_inputs` | `list[str]` → `tuple[str, ...]` | 必需 | 为不可变性转换为元组 |
| `search_query` | `str` | 必需 | 原始 mem0 的 `search()` 调用使用的查询 |
| `search_results` | `list[dict]` → `tuple[MemoryItem, ...]` | 必需 | 每个项目映射转换为 `MemoryItem` |
| `store_checksum` | `str` | 必需 | 64 字符 SHA-256 十六进制摘要 |

调用者：

- `load_mem0_traces()`（`base.py:80`）

---

#### 函数：`load_mem0_traces(path: str | Path) -> dict[str, Mem0Trace]`

位置：`cmd_audit/adapters/base.py:77-81`

```python
def load_mem0_traces(path: str | Path) -> dict[str, Mem0Trace]:
    """Load mem0 traces from a JSON file, keyed by case_id."""
    with open(path, "r") as fh:
        raw = json.load(fh)
    return {item["case_id"]: Mem0Trace.from_dict(item) for item in raw}
```

目的：

- 从 JSON 文件加载所有 mem0 轨迹并将它们按 `case_id` 键控。
- JSON 文件是一个轨迹对象数组；此函数按 `case_id` 索引它们以进行 O(1) 查找。
- 对 `Mem0Trace.from_dict` 的单个调用点——所有反序列化都通过类方法。

调用者：

- `Mem0TraceValidationTest.setUpClass`
- `Mem0AdapterInterceptionTest.setUpClass`
- `Mem0AdapterSandboxTest.setUpClass`
- `AdapterLabelParityTest.setUpClass`
- `Mem0AdapterEndToEndTest.setUpClass`
- `Mem0AdapterV0V1BoundaryTest`

---

### `cmd_audit/adapters/mem0_adapter.py`

文件：`cmd_audit/adapters/mem0_adapter.py`（146 行）。包含 `Mem0Adapter` 类（1 个构造函数、3 个公共属性、2 个拦截方法、2 个沙箱方法）。

---

#### 类：`Mem0Adapter`

位置：`cmd_audit/adapters/mem0_adapter.py:11-145`

```python
class Mem0Adapter:
    """Intercepts mem0 ``add()`` and ``search()`` at two cut points.

    All mutations are in-memory sandboxed variants.  The original store is
    never written to — verifiable via ``get_store_snapshot()`` checksum
    comparison.
    """

    def __init__(self, trace, gold_evidence, extracted_memory, raw_events):
        self._trace = trace
        self._gold_evidence = gold_evidence
        self._extracted_memory = extracted_memory
        self._raw_events = raw_events
        self._pre_checksum = trace.store_checksum
```

目的：

- 核心适配器拦截器，在 mem0 的 `add()` 和 `search()` 操作上设置两个切点，以进行 counterfactual replay。
- 构造时接收四个输入：预录制轨迹（`Mem0Trace`）、金标证据（来自 `ProbeCase`）、提取的记忆项（来自 `ProbeCase`）和原始事件（来自 `ProbeCase`）。
- 存储 `_pre_checksum` 用于沙箱验证——此值在构造后永不改变。

内部状态：

| 字段 | 类型 | 设置者 | 目的 |
| --- | --- | --- | --- |
| `_trace` | `Mem0Trace` | `__init__`（来自参数） | 预录制 mem0 操作 |
| `_gold_evidence` | `tuple[GoldEvidence, ...]` | `__init__`（来自 `case.gold_evidence`） | 正确行为所需的金标证据 |
| `_extracted_memory` | `tuple[MemoryItem, ...]` | `__init__`（来自 `case.extracted_memory`） | 所有记忆项（用于通过 ID 查找） |
| `_raw_events` | `tuple[RawEvent, ...]` | `__init__`（来自 `case.raw_events`） | 原始事件（保留给未来的 V2 验证使用） |
| `_pre_checksum` | `str` | `__init__`（来自 `trace.store_checksum`） | 不可变预回放校验和 |

##### `__init__` 参数：

| 参数 | 类型 | 目的 |
| --- | --- | --- |
| `trace` | `Mem0Trace` | 预录制轨迹（`add_inputs`、`search_query`、`search_results`、`store_checksum`） |
| `gold_evidence` | `tuple[GoldEvidence, ...]` | 来自 `ProbeCase.gold_evidence`——本应存在于记忆中的内容 |
| `extracted_memory` | `tuple[MemoryItem, ...]` | 来自 `ProbeCase.extracted_memory`——完整记忆项集合 |
| `raw_events` | `tuple[RawEvent, ...]` | 来自 `ProbeCase.raw_events`——原始对话事件 |

调用者：

- `run_case_with_mem0()`（`harness.py:19-21`）
- `Mem0AdapterInterceptionTest._adapter_for()`
- `Mem0AdapterSandboxTest._adapter_for()`
- `Mem0AdapterEndToEndTest`（直接构造）

---

##### 属性：`original_add_inputs`

位置：`cmd_audit/adapters/mem0_adapter.py:28-30`

```python
@property
def original_add_inputs(self) -> list[str]:
    """Facts that mem0's ``add()`` was originally called with."""
    return list(self._trace.add_inputs)
```

目的：

- 公共只读访问原始 `add()` 输入事实。
- 返回一个可变列表（从不可变元组复制），因此调用者可以在将其传递给 `intercept_add` 之前自由修改。

调用者：

- `_run_mem0_oracle_write`（`mem0_replays.py:43`）
- `_run_mem0_oracle_compression`（`mem0_replays.py:54`）
- `_run_mem0_verbatim_event_oracle`（`mem0_replays.py:66`）
- `_run_mem0_injection_oracle`（`mem0_replays.py:78`）

---

##### 属性：`original_search_query`

位置：`cmd_audit/adapters/mem0_adapter.py:33-35`

```python
@property
def original_search_query(self) -> str:
    """Query that mem0's ``search()`` was originally called with."""
    return self._trace.search_query
```

目的：

- 公共只读访问原始 `search()` 查询字符串。

调用者：

- `_run_mem0_oracle_retrieval`（`mem0_replays.py:94`）
- `_run_mem0_evidence_given_reasoning`（`mem0_replays.py:109`）

---

##### 属性：`original_search_results`

位置：`cmd_audit/adapters/mem0_adapter.py:38-40`

```python
@property
def original_search_results(self) -> list[MemoryItem]:
    """MemoryItems that mem0's ``search()`` originally returned."""
    return list(self._trace.search_results)
```

目的：

- 公共只读访问原始 `search()` 结果。
- 返回一个可变列表（从不可变元组复制），因此调用者可以在将其传递给 `intercept_search` 之前自由修改。

调用者：

- `_run_mem0_oracle_retrieval`（`mem0_replays.py:91`）
- `_run_mem0_evidence_given_reasoning`（`mem0_replays.py:107`）

---

##### 方法：`intercept_add(case_id, original_facts, replay) -> list[str]`

位置：`cmd_audit/adapters/mem0_adapter.py:45-84`

```python
def intercept_add(
    self, case_id: str, original_facts: list[str], replay: ReplayName
) -> list[str]:
    """Return oracle facts for *replay*, or *original_facts* for passthrough."""
    if replay == "oracle_write":
        return [
            e.text
            for e in self._gold_evidence
            if e.source_memory_id is None and e.source_event_id is None
        ]

    if replay == "oracle_compression":
        memory_by_id = {item.memory_id: item for item in self._extracted_memory}
        recovered = []
        for evidence in self._gold_evidence:
            if not evidence.source_memory_id:
                continue
            memory = memory_by_id.get(evidence.source_memory_id)
            if memory is None:
                continue
            if evidence_recall_from_text((evidence,), memory.text) < 1.0:
                recovered.append(evidence.text)
        return recovered

    if replay == "verbatim_event_oracle":
        return []

    if replay == "injection_oracle":
        retrieved_ids = {item.memory_id for item in self._trace.search_results}
        memory_by_id = {item.memory_id: item for item in self._extracted_memory}
        recovered = []
        for evidence in self._gold_evidence:
            if evidence.source_memory_id not in retrieved_ids:
                continue
            memory = memory_by_id.get(evidence.source_memory_id or "")
            if memory and evidence_recall_from_text((evidence,), memory.text) >= 1.0:
                recovered.append(memory.text)
        return recovered

    return list(original_facts)
```

目的：

- 写入侧切点：拦截 mem0 的 `add()` 以注入 oracle 事实或对未处理的回放直通原始事实。
- 每个 `if` 分支处理一个回放类型。最后一个分支（`return list(original_facts)`）处理所有未处理的回放，包括 V1 回放和未知名称。

路由逻辑（逐 replay）：

**`oracle_write`（第 49-54 行）：**
- 返回 `gold_evidence` 文本，其中 `source_memory_id is None` 且 `source_event_id is None`。
- 这些是无法追溯到任何存储的记忆项或原始事件的证据片段——它们从未进入系统。
- 镜像了 `run_oracle_write`（`replays.py:55-59`）。

**`oracle_compression`（第 56-67 行）：**
- 对于具有 `source_memory_id` 的每个金标证据，按 ID 查找记忆项，检查其当前文本是否仍包含证据短语（`evidence_recall_from_text >= 1.0`）。
- 当召回率 < 1.0 时，压缩是有损的——返回原始证据文本。
- 镜像了 `run_oracle_compression`（`replays.py:63-76`）。

**`verbatim_event_oracle`（第 69-70 行）：**
- 返回空列表 `[]` 作为绕过信号。
- 调用者（`_run_mem0_verbatim_event_oracle`）通过 `recover_raw_event_only_gold_evidence(case)` 处理原始事件注入。
- 在 `add()` 级别没有要注入的事实——证据来自原始事件，而非记忆项。

**`injection_oracle`（第 72-82 行）：**
- 对于每个金标证据，检查其 `source_memory_id` 是否存在于基线检索结果（`self._trace.search_results`）中。
- 如果是，且记忆项文本包含证据短语（召回率 >= 1.0），恢复文本。
- 限制为仅已检索的 ID 至关重要：只有在基线检索中存在的记忆项才有资格进行注入修复。
- 镜像了 `run_injection_oracle`（`replays.py:102-121`）。

**直通（第 84 行）：**
- 返回 `list(original_facts)`——未更改。
- 处理 `oracle_retrieval`、`evidence_given_reasoning`（检索侧操作，不调用 `intercept_add`）、`oracle_route`、`oracle_granularity`、`graph_off`、`safety_off` 以及任何未知名称。

调用者：

- `_run_mem0_oracle_write`（`mem0_replays.py:44-45`）
- `_run_mem0_oracle_compression`（`mem0_replays.py:55-56`）
- `_run_mem0_verbatim_event_oracle`（`mem0_replays.py:65-66`）
- `_run_mem0_injection_oracle`（`mem0_replays.py:78-79`）
- `Mem0AdapterInterceptionTest.test_intercept_add_*`

---

##### 方法：`intercept_search(case_id, original_query, original_results, replay) -> list[MemoryItem]`

位置：`cmd_audit/adapters/mem0_adapter.py:88-122`

```python
def intercept_search(
    self,
    case_id: str,
    original_query: str,
    original_results: list[MemoryItem],
    replay: ReplayName,
) -> list[MemoryItem]:
    """Return oracle results for *replay*, or *original_results* for passthrough."""
    if replay == "oracle_retrieval":
        original_ids = {item.memory_id for item in original_results}
        memory_by_id = {item.memory_id: item for item in self._extracted_memory}
        recovered: list[MemoryItem] = []
        for evidence in self._gold_evidence:
            if not evidence.source_memory_id:
                continue
            if evidence.source_memory_id in original_ids:
                continue
            memory = memory_by_id.get(evidence.source_memory_id)
            if memory and evidence_recall_from_text((evidence,), memory.text) >= 1.0:
                recovered.append(memory)
        return recovered

    if replay == "evidence_given_reasoning":
        memory_by_id = {item.memory_id: item for item in self._extracted_memory}
        augmented = list(original_results)
        for evidence in self._gold_evidence:
            if not evidence.source_memory_id:
                continue
            memory = memory_by_id.get(evidence.source_memory_id)
            if memory and evidence_recall_from_text((evidence,), memory.text) >= 1.0:
                if memory not in augmented:
                    augmented.append(memory)
        return augmented

    return list(original_results)
```

目的：

- 检索侧切点：拦截 mem0 的 `search()` 以注入 oracle 结果或对未处理的回放直通原始结果。
- 返回的是 `MemoryItem` 对象（而非字符串），因为这是 `search()` 的返回类型。

路由逻辑（逐 replay）：

**`oracle_retrieval`（第 96-108 行）：**
- 构建已检索记忆项 ID 的集合（`original_ids`）。
- 对于每个金标证据：仅当证据的 `source_memory_id` **不在** `original_ids` 中时才包含——防止重复。
- 仅当记忆项文本包含证据短语（召回率 >= 1.0）时才返回记忆项。
- 镜像了 `_recover_extracted_gold_evidence`（`replays.py:206-218`）和 `run_oracle_retrieval`（`replays.py:79-87`）。

**`evidence_given_reasoning`（第 110-120 行）：**
- 从 `original_results` 的副本开始。
- 对于每个金标证据：如果其源记忆项包含证据短语（召回率 >= 1.0）且尚未在 `augmented` 中，则附加。
- `if memory not in augmented` 守卫防止重复注入。
- 镜像了 `run_evidence_given_reasoning`（`replays.py:124-132`）——但仅当 `baseline.evidence_score >= 1.0 and baseline.answer_score < 1.0` 时才适用。该条件守卫由调用者（`_run_mem0_evidence_given_reasoning`）处理。

**直通（第 122 行）：**
- 返回 `list(original_results)`——未更改。
- 处理 `oracle_write`、`oracle_compression`、`verbatim_event_oracle`、`injection_oracle`（写入侧操作）、V1 回放以及任何未知名称。

调用者：

- `_run_mem0_oracle_retrieval`（`mem0_replays.py:93-96`）
- `_run_mem0_evidence_given_reasoning`（`mem0_replays.py:108-112`）
- `Mem0AdapterInterceptionTest.test_intercept_search_*`

---

##### 方法：`get_store_snapshot() -> StoreChecksum`

位置：`cmd_audit/adapters/mem0_adapter.py:126-136`

```python
def get_store_snapshot(self) -> StoreChecksum:
    import hashlib

    sorted_facts = sorted(self._trace.add_inputs)
    current = hashlib.sha256(
        "|".join(sorted_facts).encode()
    ).hexdigest()
    return StoreChecksum(
        checksum=current,
        item_count=len(self._trace.add_inputs),
    )
```

目的：

- 通过独立哈希存储事实来计算当前存储校验和。
- 排序确保确定性（无论内部顺序如何，相同的多事实集产生相同的哈希）。
- 使用管道作为分隔符（`"|".join(...)`）以防止连接歧义（例如，`["a", "bc"]` 与 `["ab", "c"]` 产生不同的哈希）。

设计说明：

- 独立计算 `checksum`（而非仅读取 `_pre_checksum`）是沙箱保证的关键。如果两个值都源自相同的存储字段，则检查 `current.checksum != _pre_checksum` 永远不会检测到突变。
- `hashlib` 导入是方法局部的，而非模块级的——这是有意为之，以避免在构造从不调用 `get_store_snapshot` 的适配器时支付导入成本。

调用者：

- `verify_sandbox()`（`mem0_adapter.py:140`）
- `Mem0AdapterSandboxTest.test_get_store_snapshot_returns_correct_data`
- `Mem0AdapterSandboxTest.test_store_checksum_unchanged_*`
- `Mem0AdapterEndToEndTest.test_sandbox_verified_after_full_portfolio`

---

##### 方法：`verify_sandbox() -> None`

位置：`cmd_audit/adapters/mem0_adapter.py:138-145`

```python
def verify_sandbox(self) -> None:
    """Raise ``SandboxViolationError`` if the store checksum changed."""
    current = self.get_store_snapshot()
    if current.checksum != self._pre_checksum:
        raise SandboxViolationError(
            f"Store checksum mismatch for case {self._trace.case_id!r}: "
            f"pre={self._pre_checksum!r} post={current.checksum!r}"
        )
```

目的：

- 适配器回放后的最终沙箱检查。
- 比较独立计算的校验和与预计算值。任何差异都表明回放逻辑意外修改了支持存储。
- 在所有 10 次回放后，由 `run_mem0_replay_portfolio` 调用——而非在每次单独回放后调用。

调用者：

- `run_mem0_replay_portfolio`（`mem0_replays.py:35`）
- `Mem0AdapterSandboxTest.test_verify_sandbox_passes_when_no_mutation`
- `Mem0AdapterSandboxTest.test_verify_sandbox_detects_checksum_mismatch`

---

### `cmd_audit/adapters/mem0_replays.py`

文件：`cmd_audit/adapters/mem0_replays.py`（120 行）。包含 1 个公共组合函数、6 个私有回放函数。

---

#### 函数：`run_mem0_replay_portfolio(case, adapter) -> tuple[ReplayResult, ...]`

位置：`cmd_audit/adapters/mem0_replays.py:19-36`

```python
def run_mem0_replay_portfolio(
    case: ProbeCase, adapter: Mem0Adapter
) -> tuple[ReplayResult, ...]:
    """Run 6 adapter-intercepted replays + 4 V1 passthrough replays."""
    results = (
        _run_mem0_oracle_write(case, adapter),
        _run_mem0_oracle_compression(case, adapter),
        _run_mem0_verbatim_event_oracle(case, adapter),
        _run_mem0_oracle_retrieval(case, adapter),
        _run_mem0_injection_oracle(case, adapter),
        _run_mem0_evidence_given_reasoning(case, adapter),
        run_oracle_route(case),
        run_oracle_granularity(case),
        run_graph_off(case),
        run_safety_off(case),
    )
    adapter.verify_sandbox()
    return results
```

目的：

- 运行完整的 10 回放组合（6 个适配器拦截的 V0 回放 + 4 个 V1 直通回放）。
- 4 个 V1 回放（`run_oracle_route`、`run_oracle_granularity`、`run_graph_off`、`run_safety_off`）是从 `cmd_audit.replays` 导入并原样调用的——它们不映射到 `add()`/`search()` 操作。
- 在所有回放完成后调用 `adapter.verify_sandbox()` 以确认存储未被改变。

回放顺序：

| 位置 | 回放 | 类型 | 拦截切点 |
| --- | --- | --- | --- |
| 1 | `_run_mem0_oracle_write` | 适配器拦截 | `intercept_add("oracle_write")` |
| 2 | `_run_mem0_oracle_compression` | 适配器拦截 | `intercept_add("oracle_compression")` |
| 3 | `_run_mem0_verbatim_event_oracle` | 适配器拦截 | `intercept_add("verbatim_event_oracle")` + 原始事件恢复 |
| 4 | `_run_mem0_oracle_retrieval` | 适配器拦截 | `intercept_search("oracle_retrieval")` |
| 5 | `_run_mem0_injection_oracle` | 适配器拦截 | `intercept_add("injection_oracle")` |
| 6 | `_run_mem0_evidence_given_reasoning` | 适配器拦截 | `intercept_search("evidence_given_reasoning")` |
| 7 | `run_oracle_route` | V1 直通 | 无（直接调用） |
| 8 | `run_oracle_granularity` | V1 直通 | 无（直接调用） |
| 9 | `run_graph_off` | V1 直通 | 无（直接调用） |
| 10 | `run_safety_off` | V1 直通 | 无（直接调用） |

调用者：

- `run_case_with_mem0`（`harness.py:23`）
- `Mem0AdapterEndToEndTest.test_run_mem0_replay_portfolio_runs_10_replays`
- `Mem0AdapterEndToEndTest.test_sandbox_verified_after_full_portfolio`

---

#### 私有函数：`_run_mem0_oracle_write(case, adapter) -> ReplayResult`

位置：`cmd_audit/adapters/mem0_replays.py:42-48`

```python
def _run_mem0_oracle_write(case: ProbeCase, adapter: Mem0Adapter) -> ReplayResult:
    original_facts = adapter.original_add_inputs
    oracle_facts = adapter.intercept_add(
        case.case_id, original_facts, "oracle_write"
    )
    evidence_block = "\n".join(oracle_facts)
    return _score_recovered_evidence(case, "oracle_write", evidence_block)
```

目的：

- 通过适配器写入切点运行 oracle_write counterfactual replay。
- 从 `adapter.original_add_inputs` 检索原始事实，将其传递给 `intercept_add` 并指定 replay="oracle_write"，连接 oracle 事实为证据块，通过 `_score_recovered_evidence` 评分。

调用者：

- `run_mem0_replay_portfolio`（`mem0_replays.py:24`）

---

#### 私有函数：`_run_mem0_oracle_compression(case, adapter) -> ReplayResult`

位置：`cmd_audit/adapters/mem0_replays.py:51-59`

```python
def _run_mem0_oracle_compression(
    case: ProbeCase, adapter: Mem0Adapter
) -> ReplayResult:
    original_facts = adapter.original_add_inputs
    oracle_facts = adapter.intercept_add(
        case.case_id, original_facts, "oracle_compression"
    )
    evidence_block = "\n".join(oracle_facts)
    return _score_recovered_evidence(case, "oracle_compression", evidence_block)
```

目的：

- 通过适配器写入切点运行 oracle_compression replay。
- 与 `_run_mem0_oracle_write` 结构相同，replay 名称和结果标签不同。

调用者：

- `run_mem0_replay_portfolio`（`mem0_replays.py:25`）

---

#### 私有函数：`_run_mem0_verbatim_event_oracle(case, adapter) -> ReplayResult`

位置：`cmd_audit/adapters/mem0_replays.py:62-69`

```python
def _run_mem0_verbatim_event_oracle(
    case: ProbeCase, adapter: Mem0Adapter
) -> ReplayResult:
    adapter.intercept_add(
        case.case_id, adapter.original_add_inputs, "verbatim_event_oracle"
    )
    evidence_block = recover_raw_event_only_gold_evidence(case)
    return _score_recovered_evidence(case, "verbatim_event_oracle", evidence_block)
```

目的：

- 通过适配器写入切点运行 verbatim_event_oracle replay。
- 调用 `intercept_add("verbatim_event_oracle")` 以获取绕过信号（`[]`），但**丢弃结果**——此回放有意绕开 `add()`，直接从原始事件输入证据。
- 证据通过共享的 `recover_raw_event_only_gold_evidence(case)` 辅助函数（来自 `replays.py`）恢复，该函数遍历 `gold_evidence`，按 `source_event_id` 查找 `raw_events` 并收集匹配的事件文本。

关键设计说明：

- `intercept_add` 调用保留用于协议一致性（适配器记录已请求回放），但返回的空列表是有意丢弃的。
- 实际证据块完全来自 `recover_raw_event_only_gold_evidence(case)`，后者在其 `source_memory_id is None` 且其 `source_event_id` 存在于原始事件中时恢复金标证据文本。

调用者：

- `run_mem0_replay_portfolio`（`mem0_replays.py:26`）

---

#### 私有函数：`_run_mem0_injection_oracle(case, adapter) -> ReplayResult`

位置：`cmd_audit/adapters/mem0_replays.py:72-82`

```python
def _run_mem0_injection_oracle(
    case: ProbeCase, adapter: Mem0Adapter
) -> ReplayResult:
    baseline = case.primary_baseline
    if baseline.evidence_score >= 1.0:
        return _score_recovered_evidence(case, "injection_oracle", "")
    oracle_facts = adapter.intercept_add(
        case.case_id, adapter.original_add_inputs, "injection_oracle"
    )
    evidence_block = "\n".join(oracle_facts)
    return _score_recovered_evidence(case, "injection_oracle", evidence_block)
```

目的：

- 通过适配器写入切点运行 injection_oracle replay。
- 当基线已经具有 `evidence_score >= 1.0`（证据已经在上下文中）时提前返回空结果——注入格式不是问题。
- 此守卫至关重要：如果没有它，`injection_oracle` 将对证据已在上下文中但答案仍然错误的案例产生错误的 recovery_gain，错误标记为 `injection_error` 而非 `reasoning_error`。

边缘情况：

- 当 `baseline.evidence_score >= 1.0` 时：返回 evidence_block="" → `_score_recovered_evidence` 产生 `evidence_score=0.0`、`answer=""` → `recovery_gain = 0.0`。归因层正确地未选择此回放。
- 当 `baseline.evidence_score < 1.0` 时：调用 `intercept_add("injection_oracle")` 并从检索到的记忆项中恢复正确格式化的证据。

调用者：

- `run_mem0_replay_portfolio`（`mem0_replays.py:28`）

---

#### 私有函数：`_run_mem0_oracle_retrieval(case, adapter) -> ReplayResult`

位置：`cmd_audit/adapters/mem0_replays.py:88-99`

```python
def _run_mem0_oracle_retrieval(
    case: ProbeCase, adapter: Mem0Adapter
) -> ReplayResult:
    original_results = adapter.original_search_results
    oracle_results = adapter.intercept_search(
        case.case_id,
        adapter.original_search_query,
        original_results,
        "oracle_retrieval",
    )
    evidence_block = "\n".join(item.text for item in oracle_results)
    return _score_recovered_evidence(case, "oracle_retrieval", evidence_block)
```

目的：

- 通过适配器检索切点运行 oracle_retrieval replay。
- 使用 `adapter.original_search_results`（基线已检索的内容），以便 `intercept_search` 可以过滤掉已检索的记忆项并仅返回遗漏的项。
- `intercept_search` 返回的是 `MemoryItem` 对象，因此证据块通过连接 `item.text` 构建。

调用者：

- `run_mem0_replay_portfolio`（`mem0_replays.py:27`）

---

#### 私有函数：`_run_mem0_evidence_given_reasoning(case, adapter) -> ReplayResult`

位置：`cmd_audit/adapters/mem0_replays.py:102-119`

```python
def _run_mem0_evidence_given_reasoning(
    case: ProbeCase, adapter: Mem0Adapter
) -> ReplayResult:
    baseline = case.primary_baseline
    if baseline.evidence_score >= 1.0 and baseline.answer_score < 1.0:
        original_results = adapter.original_search_results
        oracle_results = adapter.intercept_search(
            case.case_id,
            adapter.original_search_query,
            original_results,
            "evidence_given_reasoning",
        )
        evidence_block = "\n".join(item.text for item in oracle_results)
    else:
        evidence_block = ""
    return _score_recovered_evidence(
        case, "evidence_given_reasoning", evidence_block
    )
```

目的：

- 通过适配器检索切点运行 evidence_given_reasoning replay。
- 条件守卫 `baseline.evidence_score >= 1.0 and baseline.answer_score < 1.0` 确保此回放仅在证据存在但推理失败时才激活——这是 `reasoning_error` 的诊断签名。
- 当证据缺失或答案已经正确时，有效地跳过，返回 recovery_gain 为零。

边缘情况：

- `baseline.evidence_score < 1.0`：证据缺失——推理不是瓶颈。返回空。恢复增益 = 0.0。
- `baseline.answer_score >= 1.0`：答案已经正确。无需恢复。返回空。
- `baseline.evidence_score >= 1.0 and baseline.answer_score < 1.0`：证据存在，答案错误 → reasoning_error。`intercept_search` 返回增强结果。恢复增益 > 0。

调用者：

- `run_mem0_replay_portfolio`（`mem0_replays.py:29`）

---

### `cmd_audit/adapters/harness.py`

文件：`cmd_audit/adapters/harness.py`（53 行）。包含 2 个公共入口点函数。

---

#### 函数：`run_case_with_mem0(case, trace, *, top_k) -> AuditResult`

位置：`cmd_audit/adapters/harness.py:15-39`

```python
def run_case_with_mem0(
    case: ProbeCase, trace: Mem0Trace, *, top_k: int = 2
) -> AuditResult:
    """Run V1 pipeline through the mem0 adapter path."""
    adapter = Mem0Adapter(
        trace, case.gold_evidence, case.extracted_memory, case.raw_events
    )
    baseline_suite = run_baseline_suite(case)
    replays = run_mem0_replay_portfolio(case, adapter)
    attribution = assign_attribution_v1(
        replays,
        has_ingestion_trace=case.has_ingestion_trace,
        top_k=top_k,
    )
    baseline = case.primary_baseline
    return AuditResult(
        case_id=case.case_id,
        perturbation_label=case.perturbation_label,
        baseline_name=baseline.baseline_name,
        baseline_answer_score=baseline.answer_score,
        baseline_evidence_score=baseline.evidence_score,
        replays=replays,
        attribution=attribution,
        baseline_suite=baseline_suite,
    )
```

目的：

- 单个探针案例的 V1 流水线入口点，通过 mem0 适配器路径运行。
- 镜像 `run_case_v1`（`cmd_audit/harness.py`）的结构，关键区别：步骤 2 使用 `run_mem0_replay_portfolio(case, adapter)` 而非 `run_v1_replay_portfolio(case)`。

与 `run_case_v1` 的差异：

| 步骤 | `run_case_v1` | `run_case_with_mem0` |
| --- | --- | --- |
| 适配器构造 | 不适用 | `Mem0Adapter(trace, case.gold_evidence, case.extracted_memory, case.raw_events)` |
| 回放 | `run_v1_replay_portfolio(case)`（独立，10 个回放） | `run_mem0_replay_portfolio(case, adapter)`（适配器拦截，10 个回放） |
| 归因 | `assign_attribution_v1(replays, has_ingestion_trace=..., top_k=...)` | 相同 |
| 输出 | `AuditResult(...)` | 相同 |

调用者：

- `run_cases_with_mem0()`（`harness.py:50`）
- `AdapterLabelParityTest.test_each_case_label_matches_standalone`
- `AdapterLabelParityTest.test_macro_f1_matches_standalone`
- `AdapterLabelParityTest.test_recovery_gains_close_to_standalone`
- `AdapterLabelParityTest.test_attribution_correct_for_all_v0_labels`
- `Mem0AdapterEndToEndTest.test_run_case_with_mem0_produces_audit_result`
- `Mem0AdapterEndToEndTest.test_all_six_cases_pass_attribution`
- `Mem0AdapterEndToEndTest.test_ecs_draft_works_through_adapter_path`
- `Mem0AdapterV0V1BoundaryTest.test_adapter_label_is_valid_v0_label`

---

#### 函数：`run_cases_with_mem0(cases, traces, *, top_k) -> list[AuditResult]`

位置：`cmd_audit/adapters/harness.py:42-52`

```python
def run_cases_with_mem0(
    cases: list[ProbeCase],
    traces: dict[str, Mem0Trace],
    *,
    top_k: int = 2,
) -> list[AuditResult]:
    """Run V1 pipeline through mem0 adapter path for multiple cases."""
    return [
        run_case_with_mem0(case, traces[case.case_id], top_k=top_k)
        for case in cases
    ]
```

目的：

- `run_case_with_mem0` 的批量版本。
- 通过 `traces[case.case_id]` 将案例与其轨迹关联——期望每个案例都有匹配的轨迹条目。

调用者：

- 适配器层外的批量流水线运行器（future issues）。

---

### `cmd_audit/replays.py`（Issue 0014 修改）

文件：`cmd_audit/replays.py`（336 行）。Issue 0014 添加了三个公共别名以便适配器层复用。

---

#### 别名：`score_recovered_evidence`

位置：`cmd_audit/replays.py:229`

```python
score_recovered_evidence = _score_recovered_evidence
```

目的：

- `_score_recovered_evidence` 的公共别名，暴露给适配器回放模块（`mem0_replays.py`）。
- 适配器层需要它来为被拦截的回放构造 `ReplayResult`。
- 私有函数（`_score_recovered_evidence`）保持不变——别名仅提供一个公共名称，而不更改内部实现。

---

#### 别名：`recover_extracted_gold_evidence`

位置：`cmd_audit/replays.py:230`

```python
recover_extracted_gold_evidence = _recover_extracted_gold_evidence
```

目的：

- `_recover_extracted_gold_evidence` 的公共别名。
- 当适配器需要诊断 `retrieval_error` 时，从 `extracted_memory`（排除已检索项）恢复金标证据文本。
- 由 `intercept_search("oracle_retrieval")` 使用的相同逻辑。

---

#### 别名：`recover_raw_event_only_gold_evidence`

位置：`cmd_audit/replays.py:231`

```python
recover_raw_event_only_gold_evidence = _recover_raw_event_only_gold_evidence
```

目的：

- `_recover_raw_event_only_gold_evidence` 的公共别名。
- 由 `_run_mem0_verbatim_event_oracle`（`mem0_replays.py:68`）调用，以在适配器路径中复用原始事件证据恢复逻辑。
- 消除先前内联在 `mem0_replays.py` 中的重复 7 行实现（重构前）。

---

### `cmd_audit/version_gates.py`（Issue 0014 修改）

---

#### 函数：`check_v1_to_v2_gate(mem0_integrated=False) -> GateResult`

位置：`cmd_audit/version_gates.py:116-145`

```python
def check_v1_to_v2_gate(*, mem0_integrated: bool = False) -> GateResult:
```

Issue 0014 修改：

- 新增关键字参数 `mem0_integrated: bool = False`。
- 当为 `True` 时：`adapter_count` 设置为 1，evidence 报告 `"1 adapter integration(s): mem0 (Issue 0014). Letta adapter (Issue 0015) required for gate."`。
- 当为 `False` 时（默认）：与之前行为相同（0 个适配器，关卡未通过）。
- V1→V2 关卡要求 `adapter_count >= 2`，因此仅 mem0 是不够的——issue 0015（Letta）是必需的。

## 测试结构

文件：`tests/test_cmd_audit_issue14_mem0_adapter.py`。6 个测试类，30 个测试方法，90 个子测试。

| 测试类 | 测试方法数 | 子测试 | 覆盖的 TDD 行为 |
| --- | --- | --- | --- |
| `Mem0TraceValidationTest` | 5 | 30 | 加载 6 个轨迹；验证 case_id 键；检查必需字段存在及类型；验证 SHA-256 校验和格式（64 十六进制字符）；确认 search_results 是 MemoryItem 实例 |
| `Mem0AdapterInterceptionTest` | 8 | 0 | `intercept_add("oracle_write")` 返回金标证据文本；`intercept_add("oracle_compression")` 返回未压缩文本；`intercept_add("verbatim_event_oracle")` 返回空列表；`intercept_add("injection_oracle")` 返回格式化文本；`intercept_add` 直通返回原始内容不变；`intercept_search("oracle_retrieval")` 返回遗漏的 MemoryItem；`intercept_search("evidence_given_reasoning")` 增强结果；`intercept_search` 直通返回原始内容不变 |
| `Mem0AdapterSandboxTest` | 5 | 0 | 存储校验和在 `intercept_add` 后未改变；存储校验和在 `intercept_search` 后未改变；`get_store_snapshot` 返回正确的 item_count 和校验和长度；`verify_sandbox` 在无突变时通过；`verify_sandbox` 在校验和不匹配时引发 `SandboxViolationError` |
| `AdapterLabelParityTest` | 4 | 6 | 每个 V0 案例的独立工具标签 == 适配器标签（6 个子测试）；独立工具 Macro F1 == 适配器 Macro F1（均 == 1.000）；recovery_gain 在 4 位小数内匹配（每个 replay 子测试）；所有 6 个案例的适配器归因均正确 |
| `Mem0AdapterEndToEndTest` | 5 | 6 | `run_case_with_mem0` 产生 AuditResult；组合运行 10 个回放（全部为 ReplayResult 实例）；所有 6 个案例通过归因（子测试）；完整组合运行后沙箱校验和保持不变；ECS 草拟通过适配器路径工作 |
| `Mem0AdapterV0V1BoundaryTest` | 3 | 6 | 适配器标签是有效的 V0 标签（6 个子测试）；所有 V0 标签都被 `validate_v1_label` 接受；`load_mem0_traces` 返回正确的 Mem0Trace 对象 |

## 非回归分析

对 V0 烟雾套件（`data/probe_cases/v0_issue3_cases.json`，6 个案例）运行适配器路径产生与独立工具完全相同的预测标签：

| 案例 ID | Standalone 标签 | Adapter 标签 | 匹配？ | 备注 |
| --- | --- | --- | --- | --- |
| v0-write-001 | `write_error` | `write_error` | ✓ | `intercept_add("oracle_write")` 返回从未写入的金标证据 |
| v0-compression-001 | `compression_error` | `compression_error` | ✓ | `intercept_add("oracle_compression")` 返回未压缩证据文本 |
| v0-premature-extraction-001 | `premature_extraction_error` | `premature_extraction_error` | ✓ | `intercept_add` 返回 `[]` + 原始事件恢复产生证据块 |
| v0-retrieval-001 | `retrieval_error` | `retrieval_error` | ✓ | `intercept_search("oracle_retrieval")` 发现遗漏的记忆项 |
| v0-injection-001 | `injection_error` | `injection_error` | ✓ | 基线 evidence_score < 1.0 → `intercept_add("injection_oracle")` 恢复格式化证据 |
| v0-reasoning-001 | `reasoning_error` | `reasoning_error` | ✓ | 基线 evidence_score >= 1.0 且 answer_score < 1.0 → `intercept_search("evidence_given_reasoning")` 增强 |

所有 6 个标签与独立工具匹配（Macro F1 = 1.000）。Recovery_gain 值在 4 位小数内匹配所有回放。沙箱校验和在所有 10 次回放后对所有 6 个案例均未改变。

## 设计不变量（Issue 0014）

1. **双切点架构**：`intercept_add` 处理写入侧回放；`intercept_search` 处理检索侧回放。未处理的 replay="..." 值在任一方法中直通。
2. **录制轨迹模式（V1）**：预录制轨迹中的 `Mem0Trace` 字段不可变（`frozen=True`，`tuple` 类型）。实时 mem0 是 V2 范围。
3. **沙箱保证**：`get_store_snapshot()` 独立计算校验和（而非读取 `_pre_checksum`）。`verify_sandbox()` 比较当前值与预计算值。所有突变仅在内存中。
4. **公共属性封装**：三个 `@property` 访问器（`original_add_inputs`、`original_search_query`、`original_search_results`）向外部调用者暴露轨迹数据。永远不要从类外部直接访问 `_trace`。
5. **共享辅助函数复用**：适配器回放复用来自 `replays.py` 的 `_score_recovered_evidence` 和 `recover_raw_event_only_gold_evidence`——不重复进行证据恢复或评分。
6. **Adapter-Label Parity**：在 V0 烟雾套件上，`standalone_label == adapter_label`。Macro F1 通过两条路径均为 1.000。这是适配器正确性的主要验收标准。
7. **V1 直通**：4 个 V1 回放运行时不加修改。它们不映射到 `add()`/`search()` 操作，通过适配器不做更改。
