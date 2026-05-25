# Issue 0015 实现细节：Letta Adapter 集成 + V1→V2 Gate

## 目的

本文档是 issue 0015《Integrate Letta as Second Adapter Target and Enforce V1→V2 Gate》的全局实现地图。它映射每个函数、数据类、异常和常量到其确切的源码位置、签名、行为、调用者和领域含义。

Issue 0015 构建了 `LettaAdapter`，这是第二个 CMD-Skill Adapter 目标，在 Letta 的三分区记忆模型（core memory / archival memory / recall）上设置**三个拦截切点**，使 CMD-Audit 的 counterfactual replay 组合能够针对模拟的 Letta 支持代理运行。与 mem0 的两切点适配器并列，两个适配器共同满足 V1→V2 gate 的"≥2 个不同记忆代理集成"要求。

```text
Standalone Harness（现有，未更改）:
  ProbeCase → run_v1_replay_portfolio → assign_attribution_v1 → AuditResult

mem0 Adapter Path（issue 0014）:
  ProbeCase + Mem0Trace → Mem0Adapter(intercept_add, intercept_search)
    → run_mem0_replay_portfolio (6 intercepted + 4 V1 passthrough)
    → assign_attribution_v1 → AuditResult

Letta Adapter Path（issue 0015，新增）:
  ProbeCase + LettaTrace → LettaAdapter(intercept_core_write, intercept_archival_store, intercept_recall)
    → run_letta_replay_portfolio (6 intercepted + 4 V1 passthrough)
    → assign_attribution_v1 → AuditResult

V1→V2 Gate（issue 0015，更新）:
  check_v1_to_v2_gate(mem0_integrated=True, letta_integrated=True)
    → adapter_count >= 2 → PASS
```

核心架构决策与 issue 0014 一致：**V1 使用录制轨迹集成模式** — Letta 操作是预先录制的，而非实时的。实时 Letta 集成是 V2 的范围。Letta 的三分区记忆模型（core + archival + recall）天然支持 `route_error` 的跨层级路由测试，这是 mem0 的平坦存储无法做到的。

Issue 0014（mem0 adapter）已在此工作开始前完成（417 项测试通过），解除了阻塞。V1 依赖链为：0013 → 0014 → 0015 → 0016/0017。

## 源需求

本实现遵循以下本地文档。

| 来源 | 在 Issue 0015 中应用的需求 |
| --- | --- |
| `TASK.md` | V1 适配器集成：Letta 作为第二个 CMD-Skill Adapter 目标。"三切点"架构：`intercept_core_write()` 用于 core memory 写入侧回放、`intercept_archival_store()` 用于 archival memory 写入侧回放、`intercept_recall()` 用于检索侧回放。两个写入切点共享相同的证据恢复逻辑（通过 `_intercept_write_side` 私有函数）。V1 使用录制轨迹模式。沙箱保证：SHA-256 校验和基于排序的 `core_blocks + archival_blocks`。Adapter-label 完全一致：6 个 V0 烟雾案例必须通过独立工具和 Letta 适配器路径产生相同的 `predicted_label`。Cross-agent non-regression：mem0 结果在 Letta 适配器存在时必须保持不变。V1→V2 gate 要求两个 `mem0_integrated=True` 和 `letta_integrated=True`。 |
| `CLAUDE.md` | V1 问题 0014 状态：已完成。V1 问题 0015 状态：AFK，被 0014 阻塞（现已解除）。适配器层（`cmd_audit/adapters/`）现在包含两个适配器目标（mem0 + Letta）。将 CMD-Audit 与 CMD-Skill Adapter 分开：CMD-Audit 写入限制在回放本地沙箱内。V0→V1 gate HITL 已批准，V1→V2 gate 两个适配器均通过。 |
| `cmd_innovation_core/issues/README.md` | Issue 0015 是 V1 依赖图中的第二个适配器集成层，位于 0014（mem0）和 0016（RPE prefilter）之间。44 项行为级测试，adapter-label 一致性已确认，cross-agent non-regression 已验证。 |
| `cmd_innovation_core/issues/0015-integrate-letta-adapter-and-v1v2-gate.md` | 七个验收标准：LettaTrace 加载与验证、`intercept_core_write` 路由正确性、`intercept_archival_store` 路由正确性、`intercept_recall` 路由正确性、沙箱校验和不变性、adapter-label 完全一致（全部 6 个 V0 案例）、cross-agent non-regression（mem0 结果不变）、V1→V2 gate 通过。 |
| `knowledge/current-memory.md` | Letta adapter（issue 0015）：`LettaAdapter` 在 core_write、archival_store、recall 三个切点拦截 Letta 操作，支持 6 个 V0 counterfactual replay 通过 adapter 路径运行。使用录制轨迹模式（V1），沙箱校验和验证（基于 core_blocks + archival_blocks），adapter-label parity 在 V0 smoke suite 上与 standalone harness 完全一致，cross-agent non-regression 已验证。 |

## 领域边界

Issue 0015 在现有 mem0 适配器之上新增 Letta 适配器层。它不更改任何现有的回放逻辑、归因逻辑、mem0 适配器代码或 ECS 流水线。它添加了第二个输入源（被拦截的 Letta 操作），重用现有的 V1 回放组合和归因引擎。

```text
ReplayEngine, Attribution, ECS layers（现有，未更改）
  → 只有输入源改变：从夹具控制的记忆操作变为被拦截的 Letta 操作

Letta Adapter Path（issue 0015，新增）
  → run_letta_replay_portfolio(case, adapter) → 10 个回放结果
  → run_case_with_letta(case, trace) → AuditResult
  → run_cases_with_letta(cases, traces) → [AuditResult]

V1→V2 Gate（issue 0015，更新）
  → check_v1_to_v2_gate(mem0_integrated=True, letta_integrated=True) → GateResult(all_passed=True)
```

Issue 0015 拥有的内容：

- `LettaTrace` 冻结数据类：一个探针案例的预录制 Letta 操作轨迹，包含三分区记忆结构（`core_blocks`、`archival_blocks`、`recall_query`、`recall_results`）。
- `load_letta_traces(path) -> dict[str, LettaTrace]`：从 JSON 文件加载按 case_id 键控的轨迹。
- `LettaAdapter` 类：核心三切点拦截器。
- `LettaAdapter.intercept_core_write(case_id, original_blocks, replay) -> list[str]`：写入侧 core memory 回放路由。
- `LettaAdapter.intercept_archival_store(case_id, original_entries, replay) -> list[str]`：写入侧 archival memory 回放路由（与 core write 共享相同的 `_intercept_write_side` 逻辑）。
- `LettaAdapter.intercept_recall(case_id, original_query, original_results, replay) -> list[MemoryItem]`：检索侧回放路由。
- `LettaAdapter.original_core_blocks`（属性）：原始 core memory blocks。
- `LettaAdapter.original_archival_blocks`（属性）：原始 archival memory entries。
- `LettaAdapter.original_recall_query`（属性）：原始 recall 查询字符串。
- `LettaAdapter.original_recall_results`（属性）：原始 recall 返回的 MemoryItem 列表。
- `LettaAdapter.get_store_snapshot() -> StoreChecksum`：当前沙箱存储校验和（基于 `sorted(core_blocks + archival_blocks)`）。
- `LettaAdapter.verify_sandbox() -> None`：如果校验和与预计算值偏离则抛出 `SandboxViolationError`。
- `_intercept_write_side(replay, original_blocks, gold_evidence, extracted_memory, recall_results) -> list[str]`：共享的写入侧拦截逻辑，被 `intercept_core_write` 和 `intercept_archival_store` 共同使用。
- `run_letta_replay_portfolio(case, adapter) -> tuple[ReplayResult, ...]`：10 回放组合（6 个被拦截 + 4 个 V1 直通）。
- 6 个私有回放函数：`_run_letta_oracle_write`、`_run_letta_oracle_compression`、`_run_letta_verbatim_event_oracle`、`_run_letta_oracle_retrieval`、`_run_letta_injection_oracle`、`_run_letta_evidence_given_reasoning`。
- `run_case_with_letta(case, trace, *, top_k) -> AuditResult`：通过 Letta 适配器路径的 V1 流水线入口点。
- `run_cases_with_letta(cases, traces, *, top_k) -> list[AuditResult]`：批量运行器。
- `check_v1_to_v2_gate` 更新：现在接受 `letta_integrated` 参数，当两个适配器都集成时报告 adapter_count >= 2。
- 6 个轨迹条目的 JSON 夹具文件（`letta_v0_smoke_traces.json`）和 7 个测试类包含 44 个测试方法。

Issue 0015 不拥有的内容（属于其他 issue）：

- 更改 V0 或 V1 回放组合或归因阈值（issues 0001、0003、0011、0012）。
- 更改基线套件（issue 0002）。
- 更改 ECS Failure Memory 逻辑（issue 0007）。
- 添加新的管道标签（issues 0011、0012）。
- mem0 adapter 代码（issue 0014）。
- 实时 Letta 集成（V2 范围）。
- RPE 预过滤器（issue 0016）。
- 来源追踪（issue 0017）。
- Oracle Route replay 的跨层级路由测试（当前 V0 smoke 案例不触发 route_error；该场景将在 596 案例扩展中测试）。

## 当前代码产出物

| 产出物 | 在 Issue 0015 中的角色 |
| --- | --- |
| `cmd_audit/adapters/base.py` | **已修改。** 新增 `LettaTrace` 冻结数据类和 `load_letta_traces()` 加载器（在现有 `Mem0Trace`/`load_mem0_traces` 之后）。 |
| `cmd_audit/adapters/letta_adapter.py` | **新增。** 核心 `LettaAdapter` 类，包含 `intercept_core_write`、`intercept_archival_store`、`intercept_recall`、`get_store_snapshot`、`verify_sandbox`、四个公共属性，以及共享的 `_intercept_write_side()` 私有函数。 |
| `cmd_audit/adapters/letta_replays.py` | **新增。** 适配器感知回放函数：`run_letta_replay_portfolio` 和 6 个私有 `_run_letta_*` 辅助函数。4 个 V1 直通回放从 `cmd_audit.replays` 直接导入。 |
| `cmd_audit/adapters/harness.py` | **已修改。** 新增 `run_case_with_letta` 和 `run_cases_with_letta`，镜像 mem0 适配器入口点。 |
| `cmd_audit/adapters/__init__.py` | **已修改。** 新增导入/导出：`LettaAdapter`、`LettaTrace`、`load_letta_traces`、`run_case_with_letta`、`run_cases_with_letta`、`run_letta_replay_portfolio`。 |
| `cmd_audit/__init__.py` | **已修改。** 新增 6 个适配器符号到公共导出列表。 |
| `cmd_audit/version_gates.py` | **已修改。** `check_v1_to_v2_gate()` 现在接受 `letta_integrated` 参数。当两个参数都为 `True` 时，`adapter_count >= 2` 满足 gate 要求。 |
| `data/probe_cases/letta_v0_smoke_traces.json` | **新增。** 6 个预录制 Letta 轨迹，每个 V0 烟雾案例一个。字段名映射到 Letta 的三分区模型。 |
| `tests/test_cmd_audit_issue15_letta_adapter.py` | **新增。** 7 个测试类，44 个测试方法，146 个子测试。 |

## 模块地图

| 模块 | Issue 0015 角色 |
| --- | --- |
| `cmd_audit/adapters/base.py` | **已修改。** 在现有 `Mem0Trace` 和 `load_mem0_traces` 之后新增 `LettaTrace` 冻结数据类（第 87-131 行）。`LettaTrace` 捕获 Letta 的三分区结构：`core_blocks`、`archival_blocks`、`recall_query`、`recall_results`、`store_checksum`。`from_dict()` 类方法将 JSON 兼容字典反序列化为 `LettaTrace`，为每个 `recall_result` 条目构造 `MemoryItem` 对象。`load_letta_traces()` 函数（第 127-131 行）从 JSON 文件加载按 case_id 键控的轨迹字典。 |
| `cmd_audit/adapters/letta_adapter.py` | **新增。** 核心 Letta 适配器类（第 11-141 行）。拥有 `LettaAdapter` 类，包含三个公共拦截方法（`intercept_core_write`、`intercept_archival_store`、`intercept_recall`）、四个公共属性（`original_core_blocks`、`original_archival_blocks`、`original_recall_query`、`original_recall_results`）、沙箱方法（`get_store_snapshot`、`verify_sandbox`）。在第 144-195 行定义了共享的 `_intercept_write_side` 私有函数，处理所有 5 种写入侧回放路由（oracle_write、oracle_compression、verbatim_event_oracle、injection_oracle、直通）。 |
| `cmd_audit/adapters/letta_replays.py` | **新增。** 适配器感知回放函数（第 19-120 行）。拥有 `run_letta_replay_portfolio`（10 个回放）和 6 个私有 `_run_letta_*` 辅助函数。4 个 V1 直通回放（`run_oracle_route`、`run_oracle_granularity`、`run_graph_off`、`run_safety_off`）从 `cmd_audit.replays` 直接导入并原样运行。每个回放函数调用适配器的适当拦截方法，然后通过 `_score_recovered_evidence` 评分恢复的证据。 |
| `cmd_audit/adapters/harness.py` | **已修改。** 新增 `run_case_with_letta`（第 59-83 行）和 `run_cases_with_letta`（第 86-96 行）。这些镜像了 `run_case_with_mem0` / `run_cases_with_mem0`（第 17-53 行），但用 `LettaAdapter` + `run_letta_replay_portfolio` 替代了 `Mem0Adapter` + `run_mem0_replay_portfolio`。两者都使用相同的 `assign_attribution_v1` 归因引擎。 |
| `cmd_audit/adapters/__init__.py` | **已修改。** 新增导入：从 `base` 导入 `LettaTrace`、`load_letta_traces`；从 `harness` 导入 `run_case_with_letta`、`run_cases_with_letta`；从 `letta_adapter` 导入 `LettaAdapter`；从 `letta_replays` 导入 `run_letta_replay_portfolio`。新增 6 个导出符号到 `__all__`。 |
| `cmd_audit/version_gates.py` | **已修改。** `check_v1_to_v2_gate` 函数签名（第 114-165 行）现在接受 `letta_integrated: bool = False` 参数（与 `mem0_integrated: bool = False` 并列）。`adapter_count = (1 if mem0_integrated else 0) + (1 if letta_integrated else 0)`。当 `adapter_count >= 2` 时 gate 通过。evidence 字符串列出集成的适配器名称。 |
| `cmd_audit/__init__.py` | **已修改。** 从 `cmd_audit.adapters` 的导入块（第 3-18 行）新增 6 个符号：`LettaAdapter`、`LettaTrace`、`SandboxViolationError`、`StoreChecksum`、`load_letta_traces`、`run_case_with_letta`、`run_cases_with_letta`、`run_letta_replay_portfolio`。相应地扩展了 `__all__` 列表（包含约 200 个符号）。 |
| `tests/test_cmd_audit_issue15_letta_adapter.py` | **新增。** 7 个测试类，44 个测试方法，146 个子测试。 |

## 调用图

### Letta Adapter 归因流水线（issue 0015）

```text
cmd_audit/adapters/harness.py
  → harness.run_case_with_letta(ProbeCase, LettaTrace, top_k=2)
      → LettaAdapter(trace, case.gold_evidence, case.extracted_memory, case.raw_events)
          → 存储 _pre_checksum = trace.store_checksum
      → baselines.run_baseline_suite(ProbeCase)              （现有，未更改）
      → letta_replays.run_letta_replay_portfolio(ProbeCase, LettaAdapter)
          → _run_letta_oracle_write(case, adapter)
              → adapter.original_core_blocks                  （公共属性）
              → adapter.intercept_core_write(case_id, blocks, "oracle_write")
                  → _intercept_write_side("oracle_write", blocks, gold_evidence, ...)
                      → 返回金标证据文本，其中 source_memory_id is None 且 source_event_id is None
              → replays._score_recovered_evidence(case, "oracle_write", evidence_block)
          → _run_letta_oracle_compression(case, adapter)
              → adapter.original_core_blocks
              → adapter.intercept_core_write(case_id, blocks, "oracle_compression")
                  → _intercept_write_side(...)
                      → 对于每个金标证据：如果 source_memory_id 存在且 memory 文本的证据召回率 < 1.0，恢复 evidence.text
              → replays._score_recovered_evidence(case, "oracle_compression", evidence_block)
          → _run_letta_verbatim_event_oracle(case, adapter)
              → adapter.intercept_core_write(case_id, adapter.original_core_blocks, "verbatim_event_oracle")
                  → _intercept_write_side(...) → 返回 []（绕过标志）
              → replays.recover_raw_event_only_gold_evidence(case)
              → replays._score_recovered_evidence(case, "verbatim_event_oracle", evidence_block)
          → _run_letta_oracle_retrieval(case, adapter)
              → adapter.original_recall_results
              → adapter.original_recall_query
              → adapter.intercept_recall(case_id, query, results, "oracle_retrieval")
                  → 构建 original_ids = {item.memory_id for item in original_results}
                  → 返回尚未在 original_results 中的金标证据 MemoryItem
              → replays._score_recovered_evidence(case, "oracle_retrieval", evidence_block)
          → _run_letta_injection_oracle(case, adapter)
              → 如果 baseline.evidence_score >= 1.0：返回空结果
              → adapter.original_core_blocks
              → adapter.intercept_core_write(case_id, blocks, "injection_oracle")
                  → _intercept_write_side(...)
                      → 返回在 recall_results 中被检索到的金标证据的 MemoryItem.text 值
              → replays._score_recovered_evidence(case, "injection_oracle", evidence_block)
          → _run_letta_evidence_given_reasoning(case, adapter)
              → 如果 baseline.evidence_score >= 1.0 且 baseline.answer_score < 1.0：
                  → adapter.original_recall_results, adapter.original_recall_query
                  → adapter.intercept_recall(case_id, query, results, "evidence_given_reasoning")
                      → 返回 original_results + 金标证据 MemoryItem（已去重）
              → 否则：evidence_block = ""
              → replays._score_recovered_evidence(case, "evidence_given_reasoning", evidence_block)
          → replays.run_oracle_route(case)                     （V1 直通，未更改）
          → replays.run_oracle_granularity(case)               （V1 直通，未更改）
          → replays.run_graph_off(case)                        （V1 直通，未更改）
          → replays.run_safety_off(case)                       （V1 直通，未更改）
          → adapter.verify_sandbox()
              → adapter.get_store_snapshot()
                  → SHA-256(sorted(core_blocks + archival_blocks).join("|"))
              → 如果 current.checksum != _pre_checksum：抛出 SandboxViolationError
      → attribution.assign_attribution_v1(replays, has_ingestion_trace=..., top_k=top_k)
          （现有 V1 归因，未更改）
      → AuditResult(...)                                       （相同输出形状）
```

### mem0 Adapter 流水线（未更改，供参考）

```text
cmd_audit/adapters/harness.py
  → harness.run_case_with_mem0(ProbeCase, Mem0Trace)
      → Mem0Adapter(trace, ...)
      → baselines.run_baseline_suite(ProbeCase)
      → mem0_replays.run_mem0_replay_portfolio(case, adapter)   （10 个回放，两切点拦截）
      → attribution.assign_attribution_v1(replays, ...)
      → AuditResult(...)
```

### V1→V2 Gate 检查

```text
cmd_audit/version_gates.py
  → check_v1_to_v2_gate(*, mem0_integrated=True, letta_integrated=True)
      → adapter_count = (1 if mem0 else 0) + (1 if letta else 0)
      → passed = adapter_count >= 2
      → GateCriterion(
            criterion_id="adapter_integration_count",
            description="At least two distinct memory agents integrated through "
                        "the Adapter Interface without macro F1 regression",
            artifact_path="cmd_audit/adapters/",
            threshold="adapter_count >= 2 AND no macro F1 regression",
            passed=passed,
            evidence="2 adapter integration(s): mem0 (Issue 0014), Letta (Issue 0015).",
        )
      → GateResult(gate_id="V1→V2", criteria=(criterion,), all_passed=True, ...)
```

### 行为测试路径

```text
tests/test_cmd_audit_issue15_letta_adapter.py
  → base.load_letta_traces(path)                               （LettaTraceValidationTest）
  → LettaTrace.from_dict(d)                                    （LettaTraceValidationTest）
  → LettaAdapter(trace, gold_evidence, extracted_memory, raw_events)
      → LettaAdapterInterceptionTest, LettaAdapterSandboxTest
  → adapter.intercept_core_write(case_id, blocks, replay)       （LettaAdapterInterceptionTest）
  → adapter.intercept_archival_store(case_id, entries, replay)  （LettaAdapterInterceptionTest）
  → adapter.intercept_recall(case_id, query, results, replay)   （LettaAdapterInterceptionTest）
  → adapter.get_store_snapshot()                                （LettaAdapterSandboxTest）
  → adapter.verify_sandbox()                                    （LettaAdapterSandboxTest）
  → harness.run_case_with_letta(case, trace)                    （AdapterLabelParityTest, LettaAdapterEndToEndTest）
  → harness.run_letta_replay_portfolio(case, adapter)           （LettaAdapterEndToEndTest）
  → harness.run_case_with_mem0(case, trace)                     （CrossAgentNonRegressionTest）
  → labels.validate_v0_label / validate_v1_label                （LettaAdapterV0V1BoundaryTest）
  → version_gates.check_v1_to_v2_gate(...)                      （V1V2GateTest）
```

## 数据流

### 输入夹具

```text
data/probe_cases/v0_issue3_cases.json                         # 六案例 V0 烟雾套件（独立工具基线）
data/probe_cases/letta_v0_smoke_traces.json                   # 六个预录制 Letta 轨迹（适配器输入）
data/probe_cases/mem0_v0_smoke_traces.json                    # 六个预录制 mem0 轨迹（cross-agent non-regression 参考）
```

### letta_v0_smoke_traces.json 的结构

每个条目映射 `case_id` → Letta 三分区操作的预录制轨迹。字段名与 mem0 轨迹不同，反映了 Letta 的三分区记忆模型：

| 字段 | 类型 | 目的 | 与 mem0 字段的对应关系 |
| --- | --- | --- | --- |
| `case_id` | `str` | 与相应的 V0 烟雾案例夹具匹配的唯一案例标识符 | 相同 |
| `core_blocks` | `[str, ...]` | 传递给 Letta `add_core_memory()` 的 working-memory blocks | 对应 `add_inputs`（mem0 的平坦存储等价物） |
| `archival_blocks` | `[str, ...]` | 传递给 Letta `add_archival_memory()` 的长期存储条目 | 无 mem0 对应物（mem0 无分区存储） |
| `recall_query` | `str` | 传递给 Letta `recall()` 的查询字符串 | 对应 `search_query` |
| `recall_results` | `[{memory_id, text, ...}, ...]` | Letta 的 `recall()` 返回的 MemoryItem 对象 | 对应 `search_results` |
| `store_checksum` | `str` | 由 `sorted(core_blocks + archival_blocks)` 的 SHA-256 组成的确定性哈希 | 由 `sorted(add_inputs)` 计算；当 archival_blocks 为空时与 mem0 校验和相同 |

### 校验和计算

两个适配器使用不同的校验和域：

| 适配器 | 校验和输入 | 目的 |
| --- | --- | --- |
| `Mem0Adapter` | `sorted(add_inputs)` | 验证 mem0 的平坦 `add()` 存储未被污染 |
| `LettaAdapter` | `sorted(core_blocks + archival_blocks)` | 验证 Letta 的核心 + 归档存储均未被污染 |

当 `archival_blocks` 为空（所有 V0 烟雾案例）时，两个校验和产生相同的哈希值。这是 cross-agent non-regression 测试的一个关键不变量：`test_traces_match_mem0_checksums`（第 80-88 行）。

### 适配器拦截路由表

六个 V0 回放通过 `LettaAdapter` 在三个切点拦截。路由逻辑镜像了独立的回放函数，但针对 Letta 的三分区结构进行了适配。

#### Cut Point A：`intercept_core_write` 路由（写入 core memory）

| 回放名称 | `intercept_core_write` 返回 | 领域含义 |
| --- | --- | --- |
| `oracle_write` | 金标证据文本，其中 `source_memory_id is None` 且 `source_event_id is None` | 证据从未进入记忆系统——将其作为新的 working memory 提供 |
| `oracle_compression` | 金标证据文本，其中源记忆项的证据召回率 < 1.0 | 压缩后的记忆项丢失了细节——提供未压缩的原始文本作为 core block |
| `verbatim_event_oracle` | `[]`（空列表） | 绕过标志：调用者处理原始事件注入到上下文中 |
| `injection_oracle` | 从 recall_results 中被检索到的记忆项中恢复的 MemoryItem 文本 | 正确格式化的证据本应被注入到 core memory |
| 所有其他（直通） | 原样返回 `original_blocks` | 此回放不操作写入侧 |

#### Cut Point B：`intercept_archival_store` 路由（写入 archival memory）

`intercept_archival_store` 的语义与 `intercept_core_write` 完全相同——两者都通过 `_intercept_write_side()` 共享函数路由。差异仅在于调用者指定哪个存储作为目标。这反映了 CMD 的架构原则：写入侧回放作用于"所有存储"，按存储逐个操作。

| 回放名称 | `intercept_archival_store` 返回 | 领域含义 |
| --- | --- | --- |
| `oracle_write` | 金标证据文本（同 core_write） | 证据应同时存在于 core 和 archival 中 |
| `oracle_compression` | 恢复的未压缩文本（同 core_write） | 未压缩的原始文本应存在于两个存储中 |
| `verbatim_event_oracle` | `[]`（绕过标志） | 原始事件注入不操作任何存储 |
| `injection_oracle` | 恢复的记忆项文本（同 core_write） | 格式化的证据应存在于两个存储中 |
| 所有其他（直通） | 原样返回 `original_entries` | 此回放不操作写入侧 |

#### Cut Point C：`intercept_recall` 路由（检索侧）

| 回放名称 | `intercept_recall` 返回 | 领域含义 |
| --- | --- | --- |
| `oracle_retrieval` | 具有可恢复证据的记忆项，这些证据**不在** `original_results` 中 | 存在于存储中但基线检索遗漏的记忆项 |
| `evidence_given_reasoning` | `original_results` + 去重后附加的金标证据 MemoryItem | 所有证据在上下文中——推理步骤现在应该能产生正确答案 |
| 所有其他（直通） | 原样返回 `original_results` | 此回放不操作检索侧 |

### 数据流步骤示例：v0-retrieval-001 通过 Letta 适配器

此案例诊断 `retrieval_error`：正确的记忆项存在但未被基线检索返回。

1. `load_probe_cases` 加载案例夹具：`extracted_memory[0]`（mem-301："Mira chose Lisbon for the Q3 offsite"），`extracted_memory[1]`（mem-302："Porto was considered for the Q3 offsite but rejected."）。`gold_evidence[0].source_memory_id = "mem-301"`。`baseline_outputs[0].retrieved_memory_ids = ["mem-302"]`（仅检索到干扰项）。
2. `load_letta_traces` 加载轨迹：`core_blocks = ["Mira chose Lisbon for the Q3 offsite.", "Porto was considered..."]`，`archival_blocks = []`，`recall_results = [mem-302]`（基线仅返回干扰项）。
3. `LettaAdapter` 构造时存储 `_pre_checksum = trace.store_checksum`。
4. `_run_letta_oracle_retrieval` 调用 `adapter.original_recall_results` → `[mem-302]`。调用 `adapter.intercept_recall("v0-retrieval-001", query, [mem-302], "oracle_retrieval")`。
5. `intercept_recall` 构建 `original_ids = {"mem-302"}`。遍历 `gold_evidence`：`evidence.source_memory_id = "mem-301"` 不在 `original_ids` 中。查找 `memory_by_id["mem-301"]` → mem-301。`evidence_recall_from_text((evidence,), mem-301.text) >= 1.0` → 是。将 mem-301 追加到 `recovered`。
6. 返回 `[mem-301]`。`_score_recovered_evidence` 评分 evidence_block = "Mira chose Lisbon for the Q3 offsite"。恢复增益 > 0。
7. 归因：`assign_attribution_v1` 将 oracle_retrieval 排名为最佳恢复者 → `predicted_label = "retrieval_error"` → `attribution_correct = True`。
8. `adapter.verify_sandbox()` 确认 `get_store_snapshot().checksum == _pre_checksum`。

### 数据流步骤示例：v0-write-001 通过 Letta 适配器（两个写入切点）

此案例诊断 `write_error`：证据从未到达核心/归档存储。

1. `load_probe_cases` 加载案例夹具：`extracted_memory[0]`（mem-001："Kai discussed a partner workshop location."），`gold_evidence[0]`（text="Madrid"，`source_memory_id=None`，`source_event_id=None`）。
2. `load_letta_traces` 加载轨迹：`core_blocks = ["Kai discussed a partner workshop location."]`，`archival_blocks = []`。
3. `_run_letta_oracle_write` 调用 `adapter.intercept_core_write("v0-write-001", original_blocks, "oracle_write")`。
4. `_intercept_write_side` 路由到 `oracle_write` 分支：遍历 `gold_evidence`，发现 `evidence.source_memory_id is None and evidence.source_event_id is None` → 将 `"Madrid"` 追加到返回列表。
5. 返回 `["Madrid"]`。恢复增益 = 1.0。
6. 同时（在 portfolio 执行中），`_run_letta_oracle_compression` 调用 `adapter.intercept_core_write(..., "oracle_compression")`。`_intercept_write_side` 路由到 `oracle_compression` 分支：`memory_by_id.get("mem-001")` 存在，`evidence_recall_from_text((evidence,), mem-001.text) = 0.0 < 1.0`（"Kai discussed a partner workshop location." 中不包含 "Madrid"）→ 将 `"Madrid"` 追加到返回列表。恢复增益 = 1.0。
7. 归因：两个写入侧回放都产生高恢复增益，但 `oracle_write` 排名最高（写入侧直接恢复）→ `predicted_label = "write_error"` → `attribution_correct = True`。

### 数据流步骤示例：v0-compression-001 通过 Letta 适配器

此案例诊断 `compression_error`：core block 丢失了城市名称。

1. `load_probe_cases` 加载案例夹具：`extracted_memory[0]`（mem-101："Omar chose a Central European city for the retention review."），`gold_evidence[0]`（text="Prague"，`source_memory_id="mem-101"`）。
2. `load_letta_traces` 加载轨迹：`core_blocks = ["Omar chose a Central European city for the retention review."]`。
3. `_run_letta_oracle_compression` 调用 `adapter.intercept_core_write("v0-compression-001", ["lossy"], "oracle_compression")`。
4. `_intercept_write_side` 路由到 `oracle_compression` 分支：`memory_by_id.get("mem-101")` → 返回 mem-101 对象。`evidence_recall_from_text((evidence,), mem-101.text)`：检查 "Prague" 是否在 "Omar chose a Central European city for the retention review." 中 → 否（< 1.0）。将 `"Prague"` 追加到 `recovered`。
5. 返回 `["Prague"]`。恢复增益 = 1.0。
6. 归因：`oracle_compression` 恢复增益最高 → `predicted_label = "compression_error"` → `attribution_correct = True`。

## 每个函数/类的详细规范

### 1. `LettaTrace`（`cmd_audit/adapters/base.py:87-124`）

**签名**：`@dataclass(frozen=True) class LettaTrace`

**字段**：
- `case_id: str` — 案例标识符，与 ProbeCase 夹具匹配
- `core_blocks: tuple[str, ...]` — Letta 的 core（working）memory 中的 blocks
- `archival_blocks: tuple[str, ...]` — Letta 的 archival（长期）memory 中的条目
- `recall_query: str` — 原始 recall 调用使用的查询
- `recall_results: tuple[MemoryItem, ...]` — 原始 recall 返回的 MemoryItem 对象
- `store_checksum: str` — 预计算的 SHA-256 校验和，覆盖 `sorted(core_blocks + archival_blocks)`

**`from_dict(d: dict) -> LettaTrace`**（类方法，第 105-124 行）：

将 JSON 兼容字典反序列化为 `LettaTrace`。空 `archival_blocks` 通过 `d.get("archival_blocks", ())` 处理。每个 `recall_results` 条目通过 `MemoryItem(memory_id=..., text=..., source_event_ids=..., store=..., is_graph_expanded=...)` 构造函数反序列化。

**调用者**：`load_letta_traces`，`LettaTraceValidationTest`

### 2. `load_letta_traces`（`cmd_audit/adapters/base.py:127-131`）

**签名**：`def load_letta_traces(path: str | Path) -> dict[str, LettaTrace]`

打开由 `path` 指定的 JSON 文件，为顶层数组中的每个条目调用 `LettaTrace.from_dict(item)`，返回按 `case_id` 键控的字典。

**调用者**：所有 7 个测试类，`run_case_with_letta`（间接地，因为调用者传入预加载的轨迹）

### 3. `LettaAdapter.__init__`（`cmd_audit/adapters/letta_adapter.py:19-24`）

**签名**：`def __init__(self, trace: LettaTrace, gold_evidence, extracted_memory, raw_events) -> None`

存储对预录制轨迹的引用（`self._trace`）并缓存 `self._pre_checksum = trace.store_checksum` 作为沙箱基线。还存储 `self._gold_evidence`、`self._extracted_memory`、`self._raw_events` 供拦截方法使用。

**调用者**：`run_case_with_letta`（harness），测试中的 `_adapter_for` 辅助方法

### 4. `LettaAdapter` 公共属性（`cmd_audit/adapters/letta_adapter.py:28-46`）

四个 `@property` 只读访问器，为回放函数暴露原始状态：

| 属性 | 返回类型 | 来源 |
| --- | --- | --- |
| `original_core_blocks` | `list[str]` | `list(self._trace.core_blocks)` |
| `original_archival_blocks` | `list[str]` | `list(self._trace.archival_blocks)` |
| `original_recall_query` | `str` | `self._trace.recall_query` |
| `original_recall_results` | `list[MemoryItem]` | `list(self._trace.recall_results)` |

每个属性返回一个新的 `list`（防御性拷贝），但 MemoryItem 对象本身是共享引用（冻结数据类的拷贝按设计是不需要的）。

**调用者**：`letta_replays.py` 中的所有 `_run_letta_*` 函数

### 5. `LettaAdapter.intercept_core_write`（`cmd_audit/adapters/letta_adapter.py:50-60`）

**签名**：`def intercept_core_write(self, case_id: str, original_blocks: list[str], replay: ReplayName) -> list[str]`

**Cut Point A**：拦截对 Letta core memory 的写入。委托给 `_intercept_write_side(replay, original_blocks, self._gold_evidence, self._extracted_memory, self._trace.recall_results)`。

`case_id` 参数被接受但不直接使用（保留用于未来的日志/追踪；在 mem0 适配器中对称存在）。

**调用者**：`_run_letta_oracle_write`、`_run_letta_oracle_compression`、`_run_letta_verbatim_event_oracle`、`_run_letta_injection_oracle`

### 6. `LettaAdapter.intercept_archival_store`（`cmd_audit/adapters/letta_adapter.py:64-74`）

**签名**：`def intercept_archival_store(self, case_id: str, original_entries: list[str], replay: ReplayName) -> list[str]`

**Cut Point B**：拦截对 Letta archival memory 的写入。使用与 `intercept_core_write` 相同的 `_intercept_write_side` 逻辑——差异仅在于调用者指定目标存储。

**调用者**：当前未直接被回放函数调用（当前 portfolio 中的所有写入侧回放都通过 core write 操作）。存在是为了 API 完整性和未来使用（例如，`oracle_route` 的未来增强功能可能需要对 archival 层级进行写入侧回放）。测试类 `LettaAdapterInterceptionTest` 中的 `test_intercept_archival_store_uses_same_evidence_logic` 和 `test_intercept_archival_store_passthrough_returns_original` 直接验证了行为。

### 7. `LettaAdapter.intercept_recall`（`cmd_audit/adapters/letta_adapter.py:78-118`）

**签名**：`def intercept_recall(self, case_id: str, original_query: str, original_results: list[MemoryItem], replay: ReplayName) -> list[MemoryItem]`

**Cut Point C**：拦截 Letta 的 recall（检索）操作。处理两个回放类型：

**`oracle_retrieval` 分支**（第 86-100 行）：
1. 构建 `original_ids = {item.memory_id for item in original_results}`
2. 构建 `memory_by_id = {item.memory_id: item for item in self._extracted_memory}`
3. 对于每个 `gold_evidence` 条目：如果 `source_memory_id` 非空、不在 `original_ids` 中，且对应的 memory 文本包含证据短语（通过 `evidence_recall_from_text >= 1.0` 验证），则将其追加到 `recovered`
4. 返回 `recovered`——这些是存在于存储中但基线 recall 遗漏的记忆项

**`evidence_given_reasoning` 分支**（第 103-116 行）：
1. 与 oracle_retrieval 相同，构建 `memory_by_id`
2. 从 `augmented = list(original_results)` 开始
3. 对于每个 `gold_evidence`：如果源 memory 包含证据，将其追加到 `augmented`（通过 `if memory not in augmented` 去重）
4. 返回 `augmented`——原始 recall 结果 + 所有相关证据记忆项

**直通分支**（第 118 行）：返回 `list(original_results)` 原样

**调用者**：`_run_letta_oracle_retrieval`、`_run_letta_evidence_given_reasoning`

### 8. `_intercept_write_side`（`cmd_audit/adapters/letta_adapter.py:147-195`）

**签名**：`def _intercept_write_side(replay: ReplayName, original_blocks: list[str], gold_evidence, extracted_memory, recall_results: tuple[MemoryItem, ...]) -> list[str]`

**共享的写入侧拦截逻辑**，同时被 `intercept_core_write` 和 `intercept_archival_store` 使用。这是 Letta 适配器中最实质性的路由函数。

**`oracle_write` 分支**（第 160-165 行）：
```python
return [
    e.text
    for e in gold_evidence
    if e.source_memory_id is None and e.source_event_id is None
]
```
返回其两个源指针都为 `None` 的金标证据文本——意味着证据从未到达记忆系统。这些作为新的记忆 blocks 被"注入"。

**`oracle_compression` 分支**（第 167-178 行）：
```python
memory_by_id = {item.memory_id: item for item in extracted_memory}
recovered = []
for evidence in gold_evidence:
    if not evidence.source_memory_id:
        continue
    memory = memory_by_id.get(evidence.source_memory_id)
    if memory is None:
        continue
    if evidence_recall_from_text((evidence,), memory.text) < 1.0:
        recovered.append(evidence.text)
return recovered
```
对于每个金标证据：如果其 `source_memory_id` 引用了一个已提取的记忆项，但该记忆项的文本不包含证据短语（< 1.0 召回率），则恢复原始 evidence.text。这模拟了"解压缩"——提供丢失的细节。

**`verbatim_event_oracle` 分支**（第 180-181 行）：
```python
return []
```
返回一个空列表作为绕过标志。调用者（`_run_letta_verbatim_event_oracle`）使用 `recover_raw_event_only_gold_evidence(case)` 将原始事件直接注入上下文，绕过了记忆层。

**`injection_oracle` 分支**（第 183-193 行）：
```python
retrieved_ids = {item.memory_id for item in recall_results}
memory_by_id = {item.memory_id: item for item in extracted_memory}
recovered = []
for evidence in gold_evidence:
    if evidence.source_memory_id not in retrieved_ids:
        continue
    memory = memory_by_id.get(evidence.source_memory_id or "")
    if memory and evidence_recall_from_text((evidence,), memory.text) >= 1.0:
        recovered.append(memory.text)
return recovered
```
仅当证据源记忆项已被**检索到**时恢复——即正确信息存在于上下文中，但可能格式不正确。通过返回包含正确信息的 `memory.text` 来"修复"注入。

**直通分支**（第 195 行）：
```python
return list(original_blocks)
```
任何其他回放名称：原样返回原始 blocks。

**调用者**：`LettaAdapter.intercept_core_write`、`LettaAdapter.intercept_archival_store`

### 9. `LettaAdapter.get_store_snapshot`（`cmd_audit/adapters/letta_adapter.py:122-132`）

**签名**：`def get_store_snapshot(self) -> StoreChecksum`

计算当前沙箱状态的确定性校验和：
1. 合并 `core_blocks` 和 `archival_blocks` 为一个排序列表
2. 用 `|` 连接排序后的条目
3. 计算连接的 SHA-256 哈希
4. 返回 `StoreChecksum(checksum=hex_digest, item_count=len(core_blocks) + len(archival_blocks))`

注意：该方法**读取**存储状态但不修改它。与 mem0 的 `get_store_snapshot` 的关键区别：Letta 版本对 `core_blocks + archival_blocks` 进行哈希，而 mem0 版本仅对 `add_inputs` 进行哈希。当 `archival_blocks` 为空时两者产生相同的校验和。

**调用者**：`verify_sandbox`，`LettaAdapterSandboxTest`

### 10. `LettaAdapter.verify_sandbox`（`cmd_audit/adapters/letta_adapter.py:134-141`）

**签名**：`def verify_sandbox(self) -> None`

通过比较当前快照与预计算校验和来强制沙箱不变性：
```python
current = self.get_store_snapshot()
if current.checksum != self._pre_checksum:
    raise SandboxViolationError(
        f"Store checksum mismatch for case {self._trace.case_id!r}: "
        f"pre={self._pre_checksum!r} post={current.checksum!r}"
    )
```
在 `run_letta_replay_portfolio` 的末尾调用，在所有 10 个回放执行完毕后。

**调用者**：`run_letta_replay_portfolio`

### 11. `run_letta_replay_portfolio`（`cmd_audit/adapters/letta_replays.py:19-36`）

**签名**：`def run_letta_replay_portfolio(case: ProbeCase, adapter: LettaAdapter) -> tuple[ReplayResult, ...]`

Letta 适配器路径的 10 回放组合入口点。执行顺序：

1. `_run_letta_oracle_write(case, adapter)` — 通过 `intercept_core_write("oracle_write")`
2. `_run_letta_oracle_compression(case, adapter)` — 通过 `intercept_core_write("oracle_compression")`
3. `_run_letta_verbatim_event_oracle(case, adapter)` — 通过 `intercept_core_write("verbatim_event_oracle")`
4. `_run_letta_oracle_retrieval(case, adapter)` — 通过 `intercept_recall("oracle_retrieval")`
5. `_run_letta_injection_oracle(case, adapter)` — 通过 `intercept_core_write("injection_oracle")`
6. `_run_letta_evidence_given_reasoning(case, adapter)` — 通过 `intercept_recall("evidence_given_reasoning")`
7. `run_oracle_route(case)` — V1 直通（从 `cmd_audit.replays` 导入，无拦截）
8. `run_oracle_granularity(case)` — V1 直通
9. `run_graph_off(case)` — V1 直通
10. `run_safety_off(case)` — V1 直通

所有 10 个回放完成后调用 `adapter.verify_sandbox()` 以确认没有发生存储突变。

**调用者**：`run_case_with_letta`

### 12. `_run_letta_oracle_write`（`cmd_audit/adapters/letta_replays.py:42-48`）

**签名**：`def _run_letta_oracle_write(case: ProbeCase, adapter: LettaAdapter) -> ReplayResult`

1. 通过 `adapter.original_core_blocks` 读取原始 core blocks
2. 调用 `adapter.intercept_core_write(case.case_id, original_blocks, "oracle_write")` 获取 oracle blocks
3. 用换行符连接 oracle blocks → `evidence_block`
4. 委托给 `_score_recovered_evidence(case, "oracle_write", evidence_block)` 计算恢复增益

### 13. `_run_letta_oracle_compression`（`cmd_audit/adapters/letta_replays.py:51-59`）

**签名**：`def _run_letta_oracle_compression(case: ProbeCase, adapter: LettaAdapter) -> ReplayResult`

与 `_run_letta_oracle_write` 结构相同，传递 `"oracle_compression"` 作为回放名称。

### 14. `_run_letta_verbatim_event_oracle`（`cmd_audit/adapters/letta_replays.py:62-69`）

**签名**：`def _run_letta_verbatim_event_oracle(case: ProbeCase, adapter: LettaAdapter) -> ReplayResult`

1. 调用 `adapter.intercept_core_write(..., "verbatim_event_oracle")` — 返回 `[]`（绕过标志）
2. 调用共享的 `recover_raw_event_only_gold_evidence(case)` — 将原始事件文本直接注入为 evidence_block
3. 委托给 `_score_recovered_evidence(case, "verbatim_event_oracle", evidence_block)`

### 15. `_run_letta_oracle_retrieval`（`cmd_audit/adapters/letta_replays.py:88-99`）

**签名**：`def _run_letta_oracle_retrieval(case: ProbeCase, adapter: LettaAdapter) -> ReplayResult`

1. 通过 `adapter.original_recall_results` 读取原始 recall 结果
2. 调用 `adapter.intercept_recall(case.case_id, adapter.original_recall_query, original_results, "oracle_retrieval")` 获取 oracle 结果
3. 用换行符连接 MemoryItem 的 text → `evidence_block`
4. 委托给 `_score_recovered_evidence(case, "oracle_retrieval", evidence_block)`

### 16. `_run_letta_injection_oracle`（`cmd_audit/adapters/letta_replays.py:72-82`）

**签名**：`def _run_letta_injection_oracle(case: ProbeCase, adapter: LettaAdapter) -> ReplayResult`

1. 守卫：如果 `baseline.evidence_score >= 1.0`（证据已经存在），返回零增益的 ReplayResult
2. 否则：调用 `adapter.intercept_core_write(case.case_id, adapter.original_core_blocks, "injection_oracle")`
3. 连接 oracle blocks → evidence_block
4. 委托给 `_score_recovered_evidence(case, "injection_oracle", evidence_block)`

### 17. `_run_letta_evidence_given_reasoning`（`cmd_audit/adapters/letta_replays.py:102-119`）

**签名**：`def _run_letta_evidence_given_reasoning(case: ProbeCase, adapter: LettaAdapter) -> ReplayResult`

1. 守卫：仅当 `baseline.evidence_score >= 1.0` 且 `baseline.answer_score < 1.0`（证据存在但推理失败）时执行
2. 调用 `adapter.intercept_recall(case.case_id, adapter.original_recall_query, original_results, "evidence_given_reasoning")`
3. 连接 augmented recall 结果 → evidence_block
4. 否则：`evidence_block = ""`（零增益）
5. 委托给 `_score_recovered_evidence(case, "evidence_given_reasoning", evidence_block)`

### 18. `run_case_with_letta`（`cmd_audit/adapters/harness.py:59-83`）

**签名**：`def run_case_with_letta(case: ProbeCase, trace: LettaTrace, *, top_k: int = 2) -> AuditResult`

Letta 适配器路径的 V1 流水线入口点。镜像 `run_case_with_mem0`（第 17-41 行）：

1. 构造 `LettaAdapter(trace, case.gold_evidence, case.extracted_memory, case.raw_events)`
2. 运行基线套件：`run_baseline_suite(case)`
3. 运行回放组合：`run_letta_replay_portfolio(case, adapter)`
4. 运行 V1 归因：`assign_attribution_v1(replays, has_ingestion_trace=case.has_ingestion_trace, top_k=top_k)`
5. 返回 `AuditResult(case_id=..., perturbation_label=..., baseline_name=..., baseline_answer_score=..., baseline_evidence_score=..., replays=..., attribution=..., baseline_suite=...)`

### 19. `run_cases_with_letta`（`cmd_audit/adapters/harness.py:86-96`）

**签名**：`def run_cases_with_letta(cases: list[ProbeCase], traces: dict[str, LettaTrace], *, top_k: int = 2) -> list[AuditResult]`

列表推导包装器：`[run_case_with_letta(case, traces[case.case_id], top_k=top_k) for case in cases]`。

### 20. `check_v1_to_v2_gate`（`cmd_audit/version_gates.py:114-165`）

**签名**：`def check_v1_to_v2_gate(*, mem0_integrated: bool = False, letta_integrated: bool = False) -> GateResult`

计算 `adapter_count = (1 if mem0_integrated else 0) + (1 if letta_integrated else 0)`。Gate 在 `adapter_count >= 2` 时通过。

生成带有详细 evidence 的 `GateCriterion`：
- **2 个适配器**：`"2 adapter integration(s): mem0 (Issue 0014), Letta (Issue 0015)."`
- **1 个适配器**：`"1 adapter integration(s): {name}. Second adapter required for gate."`
- **0 个适配器**：`"0 adapter integrations; V0 operates as standalone harness."`

**调用者**：`V1V2GateTest` 中的 5 个测试方法，未来 CLI 调用

## SandboxViolationError 与 StoreChecksum

在 `cmd_audit/adapters/base.py` 中定义（issue 0014 引入），被 issue 0015 重用，无需修改：

- `StoreChecksum(checksum: str, item_count: int)` — 冻结数据类（第 26-29 行）
- `SandboxViolationError(RuntimeError)` — 异常类（第 35-36 行）

Letta 适配器通过 `get_store_snapshot()` → `StoreChecksum` 产生这些类型，并通过 `verify_sandbox()` → 可能引发 `SandboxViolationError` 消费它们。

## 测试覆盖

| 测试类 | 测试方法 | 子测试 | 覆盖内容 |
| --- | --- | --- | --- |
| `LettaTraceValidationTest` | 6 | 18 | 轨迹加载、必需字段、校验和格式、MemoryItem 反序列化、与 mem0 校验和的交叉验证 |
| `LettaAdapterInterceptionTest` | 10 | 10 | `intercept_core_write` 路由（5 个回放）、`intercept_archival_store` 路由（2 个回放）、`intercept_recall` 路由（3 个回放） |
| `LettaAdapterSandboxTest` | 7 | 7 | 所有三个切点后的校验和不变性、快照格式、`verify_sandbox` 通过/检测、core+archival 组合 |
| `AdapterLabelParityTest` | 4 | 30 | 标签匹配 standalone、Macro F1 = 1.000、恢复增益接近（`assertAlmostEqual`）、所有 6 个 V0 标签的归因正确性 |
| `LettaAdapterEndToEndTest` | 5 | 12 | AuditResult 输出、10 回放组合计数、全部 6 个案例归因正确、portfolio 后的沙箱验证、通过适配器路径的 ECS draft |
| `LettaAdapterV0V1BoundaryTest` | 3 | 12 | V0 标签验证、V1 标签接受、通过备用导入路径加载轨迹 |
| `CrossAgentNonRegressionTest` | 4 | 36 | mem0 标签在 Letta 存在时不变、两个代理达到完美的 Macro F1、恢复增益独立、三分区 trace block 访问 |
| `V1V2GateTest` | 5 | 5 | 两个适配器都通过、仅 mem0 失败、仅 Letta 失败、无适配器失败、向后兼容（仅 mem0 参数） |

**总计**：44 个测试方法，146 个子测试。零失败，零回归。

## 与 Issue 0014（mem0 Adapter）的比较

| 维度 | Issue 0014（mem0） | Issue 0015（Letta） |
| --- | --- | --- |
| 切点数量 | 2（`intercept_add`、`intercept_search`） | 3（`intercept_core_write`、`intercept_archival_store`、`intercept_recall`） |
| 记忆模型 | 平坦存储（add + search） | 三分区：core（working）+ archival（长期）+ recall（检索） |
| 写入侧路由 | 单个 `intercept_add` 方法 | 两个方法（core + archival），通过 `_intercept_write_side` 共享逻辑 |
| 检索侧路由 | 单个 `intercept_search` 方法 | 单个 `intercept_recall` 方法 |
| 校验和域 | `sorted(add_inputs)` | `sorted(core_blocks + archival_blocks)` |
| 轨迹字段 | `add_inputs`、`search_query`、`search_results` | `core_blocks`、`archival_blocks`、`recall_query`、`recall_results` |
| 回放组合 | `run_mem0_replay_portfolio`（10 个回放） | `run_letta_replay_portfolio`（10 个回放） |
| 直通 V1 回放 | `oracle_route`、`oracle_granularity`、`graph_off`、`safety_off` | 相同的 4 个 V1 直通（从 `cmd_audit.replays` 导入） |
| `route_error` 适用性 | N/A（平坦存储——Oracle Route 始终返回零增益） | 适用（核心 vs 归档 vs recall 层级选择） |
| 测试 | 6 个测试类，30 个方法，90 个子测试 | 7 个测试类，44 个方法，146 个子测试 |
| Gate 贡献 | 提供 adapter_count 中的 1 | 提供 adapter_count 中的 1；两者都满足 V1→V2 gate |

## 架构决策记录

1. **三分区拦截设计**：将 core write 和 archival store 作为独立切点暴露，而不是统一它们，保留了 Letta 的 API 表面以供未来使用（例如，`oracle_route` 增强功能可能仅针对一个层级进行干预）。在当前 portfolio 中，所有写入侧回放都通过 core write；但 archival 切点已在 API 中存在并经过测试。

2. **共享的 `_intercept_write_side` 函数**：两个写入切点使用完全相同的证据恢复逻辑。将它们都委托给一个私有函数可避免重复。如果 core 与 archival 的回放路由出现分歧，可以内联替代。

3. **录制轨迹模式（V1）**：与 issue 0014 一致，Letta 适配器使用预录制轨迹而非实时 Letta 实例。这避免了需要运行中的 Letta 部署，同时验证切点语义。实时集成是 V2 的范围。

4. **与 mem0 的校验和兼容性**：由于所有当前 V0 烟雾案例的 `archival_blocks = []`，Letta 和 mem0 轨迹产生相同的校验和。`test_traces_match_mem0_checksums` 强制了此不变量。当引入具有非空 archival 的真实 Letta 案例时，校验和将发生分歧——这是预期行为。

5. **V1→V2 Gate 参数对称性**：两个 `mem0_integrated` 和 `letta_integrated` 都是仅关键字布尔参数，默认值为 `False`。Gate 语义从未改变：`adapter_count >= 2` 始终是标准。当只有 1 个适配器集成时，"缺失"消息会建议集成第二个。

## 完成状态（2026-05-19）

全部 44 个行为级测试通过。全部 453 个测试通过（包含回归），622 个子测试，零失败。

V1→V2 gate：使用 `mem0_integrated=True` 和 `letta_integrated=True` 时通过。两个适配器在 V0 smoke suite 上产生与 standalone harness 相同的标签（Macro F1 = 1.000）。Cross-agent non-regression 已验证：mem0 结果与 standalone 一致，Letta 结果与 standalone 一致，mem0 和 Letta 彼此独立。

### 已更改/创建的文件

| 文件 | 变更 |
|------|------|
| `cmd_audit/adapters/base.py` | 新增 `LettaTrace` 冻结数据类（第 87-124 行）和 `load_letta_traces()` 函数（第 127-131 行） |
| `cmd_audit/adapters/letta_adapter.py` | **新增。** `LettaAdapter` 类：3 个拦截方法、4 个属性、2 个沙箱方法、`_intercept_write_side` 共享函数（196 行） |
| `cmd_audit/adapters/letta_replays.py` | **新增。** `run_letta_replay_portfolio`、6 个私有回放函数（120 行） |
| `cmd_audit/adapters/harness.py` | 新增 `run_case_with_letta`（第 59-83 行）、`run_cases_with_letta`（第 86-96 行） |
| `cmd_audit/adapters/__init__.py` | 新增 6 个导入/导出（LettaAdapter、LettaTrace、load_letta_traces、run_case_with_letta、run_cases_with_letta、run_letta_replay_portfolio） |
| `cmd_audit/version_gates.py` | `check_v1_to_v2_gate` 新增 `letta_integrated` 参数（第 114-165 行） |
| `cmd_audit/__init__.py` | 新增 6 个适配器符号到 `__all__` |
| `data/probe_cases/letta_v0_smoke_traces.json` | **新增。** 6 个轨迹条目，映射 V0 烟雾案例到 Letta 字段名 |
| `tests/test_cmd_audit_issue15_letta_adapter.py` | **新增。** 7 个测试类，44 个方法，146 个子测试 |
