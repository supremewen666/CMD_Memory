# Issue 0017 实现细节：Provenance Tracking — Execution Lineage DAG + trace-mem Citation

> **0021 PR2 note (2026-05-23).** Any `run_case_v1_with_prefilter` references
> in this historical implementation map are superseded. Provenance integration
> now uses `run_case_v1_with_hook`, and legacy prefilter modules were removed.

## 目的

本文档是 issue 0017《Implement Provenance Tracking — Execution Lineage DAG + trace-mem Citation》的全局实现地图。它映射每个函数、数据类、辅助函数和模块到其确切的源码位置、签名、行为、调用者和领域含义。

Issue 0017 为 CMD-Audit V1 添加影响溯源（influence provenance）：每个在反事实回放期间产生或修改的 `MemoryItem` 都会记录其入边推导（in-edge derivation），通过 Execution Lineage DAG 结构。每条边携带 trace-mem HMAC 引用（trajectory turn、character span、content hash）用于篡改检测。`graph_error` 归因会引用特定的溯源边，标识哪些图-干扰项（graph-distractor items）影响了失败的回答。

```text
ProvenanceTracker（可变收集器）
  -> record_edge() 在每次回放中累积 ProvenanceEdge
  -> get_edges() 返回不可变元组
  -> annotate_item() 将边烘焙到冻结的 MemoryItem 副本中

每个回放路径记录溯源：
  oracle_write        -> operation="write"
  oracle_compression  -> operation="compress"
  verbatim_event_oracle -> operation="extract"
  oracle_retrieval    -> operation="retrieve"
  injection_oracle    -> operation="inject"
  evidence_given_reasoning -> operation="reason"
  oracle_route        -> operation="route"
  oracle_granularity  -> operation="extract"（粒度调整 = 重新提取）
  graph_off           -> operation="retrieve"（无图扩展的直接检索）
  safety_off          -> operation="inject"（未阻塞的证据注入）

graph_error 干扰项检测：
  get_graph_distractor_edges()
    -> 比较 baseline.retrieved_memory_ids 与 is_graph_expanded 项
    -> 仅通过图扩展出现的项被记录为干扰项溯源边
    -> 流入 AttributionResult.distractor_provenance_ids/edges
```

该切片交付 TDD Cycle 23：Provenance Tracking。

| 周期 | 标题 | 状态 |
| --- | --- | --- |
| Cycle 23 | Provenance Tracking (Execution Lineage DAG + trace-mem Citation) | 绿色 |

Issue 0017 在 issue 0011（V1 标签）、issue 0012（V1 回放）、issue 0013（耦合失败重新校准）、issue 0014（mem0 适配器）和 issue 0015（Letta 适配器）之上构建溯源层。它是 V2 MemQ TD(λ) 级联修复的前置条件。

## 源需求

本实现遵循以下本地文档。

| 来源 | 在 Issue 0017 中应用的需求 |
| --- | --- |
| `TASK.md` | V1 基础设施：Provenance Tracking (US44-US47, AC16)。Execution Lineage DAG + trace-mem Citation (Decision 28)。`graph_error` 干扰项标识。 |
| `CLAUDE.md` | Decision 28 — Provenance: Execution Lineage DAG 结构 + trace-mem HMAC 引用格式。Phase 1 (V1): 每个 MemoryItem 的入边追踪。Phase 2 (V2): 在溯源 DAG 上的 MemQ TD(λ) 级联修复。不可变溯源边（仅追加）。向后兼容：所有新字段默认为空元组，所有新参数为仅关键字可选参数。 |
| `cmd_innovation_core/CONTEXT.md` | V1 Essentials：溯源追踪是 V1 需求（Day 5 溯源收敛发现后新增）。`graph_error` 归因必须引用特定的溯源边。 |
| `cmd_innovation_core/prd/cmd_minimal_probe_prd.md` | User Story 44-47：溯源数据模型、每个回放类型的溯源记录、溯源完整性指标、HMAC 篡改检测。AC16.1-AC16.9。 |
| `cmd_innovation_core/issues/0017-provenance-tracking-execution-lineage-dag.md` | 九个验收标准：数据模型、每种回放类型的记录、完整性指标、HMAC 篡改检测、graph_error 干扰项边、向后兼容性、适配器兼容性、CSV 输出、论文面向指标。 |

## 领域边界

Issue 0017 在现有的 V1 标签层（issue 0011-0012）、回放层（issue 0001-0003, 0012）、归因层（issue 0011）、适配器层（issue 0014-0015）和预过滤层（issue 0016）之上构建溯源层。它不更改任何现有的回放逻辑、归因逻辑或标签注册表。它向所有回放函数添加可选的仅关键字 `tracker` 参数，并新增 `provenance.py` 核心模块。

```text
独立 V1 流水线（issue 0017，新增 tracker 参数和干扰项逻辑）
  run_case_v1(ProbeCase)
    -> ProvenanceTracker(case.case_id)            （新增）
    -> run_v1_replay_portfolio(case, tracker=tracker)  （tracker 转发）
    -> get_graph_distractor_edges(case, graph_off)     （新增）
    -> assign_attribution_v1(..., distractor_edges=...) （distractor_edges 参数新增）
    -> AuditResult(..., replays 包含 provenance_edges)

预过滤 V1 流水线（issue 0017，相同的 tracker + 干扰项模式）
  run_case_v1_with_prefilter(ProbeCase)
    -> ProvenanceTracker(case.case_id)
    -> run_v1_replay_portfolio_subset(case, selected, tracker=tracker)
    -> get_graph_distractor_edges（如果 graph_off 在子集中）
    -> assign_attribution_v1(..., distractor_edges=...)

适配器流水线（issue 0017，通过适配器 harness）
  _run_case_with_adapter(case, adapter, run_portfolio)
    -> ProvenanceTracker(case.case_id)
    -> run_portfolio(case, adapter, tracker=tracker)
    -> get_graph_distractor_edges
    -> assign_attribution_v1(..., distractor_edges=...)
```

Issue 0017 拥有的内容：

- `Citation` 冻结数据类（`models.py:70-74`）：trajectory_turn, char_span, content_hash。
- `ProvenanceEdge` 冻结数据类（`models.py:78-85`）：source_id, target_id, operation, citation, timestamp, tamper_detected=False。
- `MemoryItem.provenance: tuple = ()` 字段（`models.py:36`）：不可变默认值确保向后兼容。
- `MemoryItem.from_mapping()` 中可选的 `provenance` JSON 解析（`models.py:40-58`）。
- `provenance.py` 整个模块（164 行）：`_compute_hmac`、`ProvenanceTracker` 类、`record_provenance_edge` 便利包装器、`detect_tamper`、`compute_provenance_completeness`、`get_graph_distractor_edges`。
- `ReplayResult.provenance_edges: tuple = ()` 字段（`replays.py:20`）。
- `_score_recovered_evidence` 中的 tracker 参数（`replays.py:325-343`）。
- 所有 10 个回放函数中的 `*, tracker: object | None = None` 参数和溯源记录逻辑：
  - `run_oracle_write`（`replays.py:70-91`）
  - `run_oracle_compression`（`replays.py:94-126`）
  - `run_oracle_retrieval`（`replays.py:129-160`）
  - `run_verbatim_event_oracle`（`replays.py:163-190`）
  - `run_injection_oracle`（`replays.py:193-229`）
  - `run_evidence_given_reasoning`（`replays.py:232-253`）
  - `run_oracle_route`（`replays.py:256-296`）
  - `run_oracle_granularity`（`replays.py:380-437`）
  - `run_graph_off`（`replays.py:440-472`）
  - `run_safety_off`（`replays.py:475-497`）
- 组合函数中的 `*, tracker` 参数：`run_v0_replay_portfolio`（`replays.py:36-48`）、`run_v1_replay_portfolio`（`replays.py:51-67`）、`run_v1_replay_portfolio_subset`（`replays.py:556-567`）。
- `AttributionResult.distractor_provenance_ids: tuple[str, ...] = ()`（`attribution.py:25`）。
- `AttributionResult.distractor_provenance_edges: tuple = ()`（`attribution.py:26`）。
- `assign_attribution_v1` 中的 `distractor_edges: tuple = ()` 参数（`attribution.py:70`）。
- `run_case_v1` 中的 `ProvenanceTracker` 集成和干扰项逻辑（`harness.py:240-271`）。
- `run_case_v1_with_prefilter` 中的 `ProvenanceTracker` 集成（`harness.py:303-371`）。
- `write_comparison_metrics_table` 中的 `provenance_completeness` 行（`harness.py:209-234`）。
- `write_attribution_table` 中的 `distractor_provenance_ids` 列（`writers.py:98-106`）。
- 适配器骨架中所有 6 个拦截回放函数的 `*, tracker` 参数（`adapters/_replay_skeleton.py`）。
- `run_adapter_replay_portfolio` 中的 `*, tracker` 参数（`adapters/_replay_skeleton.py:22-39`）。
- `run_mem0_replay_portfolio` 和 `run_letta_replay_portfolio` 中的 `*, tracker` 参数（`adapters/mem0_replays.py:12-16`, `adapters/letta_replays.py`）。
- `_run_case_with_adapter` 中的 `ProvenanceTracker` 集成（`adapters/harness.py:21-55`）。
- 从 `cmd_audit/__init__.py` 导出的 7 个新公共符号。
- 12 个测试类，78 个测试方法，覆盖 Cycle 23。

Issue 0017 不拥有的内容（属于其他 issue）：

- 更改归因阈值或标签映射（issue 0001、0011）。
- 更改基线套件或对比器逻辑（issue 0002）。
- 更改 Post-Repair Context Replay 评分（issue 0005）。
- 更改 ECS Failure Memory 逻辑（issue 0007）。
- 添加 V2 MemQ TD(λ) 级联修复（V2）。
- 完整的加密溯源（MemLineage 风格，推迟到 V2）。
- 真实数据集成（issue 0018）。

## 当前代码产出物

| 产出物 | 在 Issue 0017 中的角色 |
| --- | --- |
| `cmd_audit/models.py` | `Citation` 和 `ProvenanceEdge` 冻结数据类；`MemoryItem` 上的 `provenance: tuple = ()` 字段；`from_mapping()` 中可选的 provenance JSON 解析。 |
| `cmd_audit/provenance.py` | **新。** `ProvenanceTracker` 可变收集器类；`_compute_hmac` 内部 HMAC-SHA256；`record_provenance_edge` 模块级便利包装器；`detect_tamper` HMAC 验证；`compute_provenance_completeness` 指标；`get_graph_distractor_edges` 干扰项标识。 |
| `cmd_audit/replays.py` | `ReplayResult.provenance_edges` 字段；所有 10 个回放函数中的 `*, tracker` 参数和溯源记录逻辑；`_score_recovered_evidence` 中的 tracker 转发；3 个组合函数中的 `*, tracker` 参数；`run_v1_replay_portfolio_subset` 中的 tracker 转发。 |
| `cmd_audit/attribution.py` | `AttributionResult.distractor_provenance_ids` 和 `distractor_provenance_edges` 字段；`assign_attribution_v1` 中的 `distractor_edges` 参数；从干扰项边填充溯源 ID。 |
| `cmd_audit/harness.py` | `run_case_v1` 中的 `ProvenanceTracker` 创建、tracker 转发到组合、graph_off 干扰项检测、`distractor_edges` 转发到归因；`run_case_v1_with_prefilter` 中相同的模式；`write_comparison_metrics_table` 中的 `provenance_completeness` 行。 |
| `cmd_audit/writers.py` | `write_attribution_table` 中的 `distractor_provenance_ids` 列（字段名和行字典）。 |
| `cmd_audit/adapters/_replay_skeleton.py` | 所有 6 个拦截回放函数中的 `*, tracker` 参数和溯源记录；`run_adapter_replay_portfolio` 中的 `*, tracker` 参数。 |
| `cmd_audit/adapters/mem0_replays.py` | `run_mem0_replay_portfolio` 中的 `*, tracker` 参数，转发到骨架。 |
| `cmd_audit/adapters/letta_replays.py` | `run_letta_replay_portfolio` 中的 `*, tracker` 参数，转发到骨架。 |
| `cmd_audit/adapters/harness.py` | `_run_case_with_adapter` 中的 `ProvenanceTracker` 创建、干扰项检测和 `distractor_edges` 转发。 |
| `cmd_audit/__init__.py` | 导出 7 个新符号：`Citation`、`ProvenanceEdge`、`ProvenanceTracker`、`compute_provenance_completeness`、`detect_tamper`、`get_graph_distractor_edges`、`record_provenance_edge`。 |
| `tests/test_cmd_audit_issue17_provenance.py` | **新。** 12 个测试类，78 个测试方法，覆盖 AC16.1-AC16.9。 |

## 模块地图

| 模块 | Issue 0017 角色 |
| --- | --- |
| `cmd_audit/models.py` | **已更新。** 拥有 `Citation`（第 70-74 行）和 `ProvenanceEdge`（第 78-85 行）冻结数据类。`MemoryItem` 获得 `provenance: tuple = ()`（第 36 行）。`from_mapping()` 获得可选的 `provenance` JSON 解析（第 40-58 行）。现有的所有字段、`RawEvent`、`GoldEvidence`、`BaselineOutput`、`ProbeCase`、加载器和校验器保持不变。 |
| `cmd_audit/provenance.py` | **新。** 主溯源模块（164 行）。拥有 `_compute_hmac` 内部函数、`ProvenanceTracker` 可变收集器类、`record_provenance_edge` 模块级便利包装器、`detect_tamper` HMAC 验证函数、`compute_provenance_completeness` 指标函数和 `get_graph_distractor_edges` 干扰项标识函数。 |
| `cmd_audit/replays.py` | **已更新。** `ReplayResult` 获得 `provenance_edges: tuple = ()`（第 20 行）。所有 10 个回放函数获得 `*, tracker: object | None = None` 参数和溯源记录逻辑块。`_score_recovered_evidence` 获得 `tracker` 参数，将 `tracker.get_edges()` 烘焙到 `ReplayResult.provenance_edges` 中（第 325-343 行）。3 个组合函数获得 `*, tracker` 参数，转发到每个回放。新增 `run_v1_replay_portfolio_subset`（第 556-567 行），接受 tracker 并通过 `_V1_REPLAY_DISPATCH` 转发。 |
| `cmd_audit/attribution.py` | **已更新。** `AttributionResult` 获得 `distractor_provenance_ids: tuple[str, ...] = ()`（第 25 行）和 `distractor_provenance_edges: tuple = ()`（第 26 行）。`assign_attribution_v1` 获得 `distractor_edges: tuple = ()` 参数（第 70 行）；当提供时，提取 `e.source_id` 填充 `distractor_provenance_ids`（第 118-121 行）。现有的 `assign_attribution` 和 `_label_for_replay` 保持不变。 |
| `cmd_audit/harness.py` | **已更新。** `run_case_v1`（第 240-271 行）：创建 `ProvenanceTracker`，转发到 `run_v1_replay_portfolio`，查找 `graph_off` 回放，调用 `get_graph_distractor_edges`，转发 `distractor_edges` 到 `assign_attribution_v1`。`run_case_v1_with_prefilter`（第 303-371 行）：相同的 tracker + 干扰项模式，使用 `run_v1_replay_portfolio_subset`，仅在 `graph_off` 在选中子集中时计算干扰项。`write_comparison_metrics_table`（第 171-234 行）：添加 `provenance_completeness` 行（第 209-234 行），包含 `provenance_completeness` 字段名和值（带分母/分子）。 |
| `cmd_audit/writers.py` | **已更新。** `write_attribution_table`（第 73-149 行）：在 `fieldnames` 中添加 `"distractor_provenance_ids"`（第 103 行），在行字典中添加 `"distractor_provenance_ids": "|".join(result.attribution.distractor_provenance_ids)`（第 131 行）。 |
| `cmd_audit/adapters/_replay_skeleton.py` | **已更新。** `run_adapter_replay_portfolio`（第 22-39 行）：获得 `*, tracker` 参数，转发到所有 10 个回放（6 个拦截 + 4 个 V1 透传）。所有 6 个拦截回放函数（`_run_oracle_write`、`_run_oracle_compression`、`_run_verbatim_event_oracle`、`_run_injection_oracle`、`_run_oracle_retrieval`、`_run_evidence_given_reasoning`）：获得 `*, tracker` 参数，通过适配器输入记录溯源边。 |
| `cmd_audit/adapters/mem0_replays.py` | **已更新。** `run_mem0_replay_portfolio`（第 12-16 行）：获得 `*, tracker` 参数，转发到 `run_adapter_replay_portfolio`。 |
| `cmd_audit/adapters/letta_replays.py` | **已更新。** `run_letta_replay_portfolio`：获得 `*, tracker` 参数，转发到 `run_adapter_replay_portfolio`。（与 mem0_replays.py 结构相同。） |
| `cmd_audit/adapters/harness.py` | **已更新。** `_run_case_with_adapter`（第 21-55 行）：创建 `ProvenanceTracker`，转发到 `run_portfolio`，查找 `graph_off` 回放，调用 `get_graph_distractor_edges`，转发 `distractor_edges` 到 `assign_attribution_v1`。`run_case_with_mem0` 和 `run_case_with_letta` 通过此共享运行器保持不变。 |
| `cmd_audit/__init__.py` | **已更新。** 从 `.models` 导入 `Citation`、`ProvenanceEdge`；从 `.provenance` 导入 `ProvenanceTracker`、`compute_provenance_completeness`、`detect_tamper`、`get_graph_distractor_edges`、`record_provenance_edge`。全部 7 个添加到 `__all__`。 |
| `tests/test_cmd_audit_issue17_provenance.py` | 12 个测试类，78 个测试方法，覆盖 Cycle 23。 |

## 调用图

### V1 溯源流水线（issue 0017）

```text
cmd_audit/__init__.py
  -> harness.run_case_v1(ProbeCase)
      -> baselines.run_baseline_suite(ProbeCase)
      -> provenance.ProvenanceTracker(case.case_id)       （新增：创建追踪器）
          -> hashlib.sha256(case_id) -> session_key
      -> replays.run_v1_replay_portfolio(case, tracker=tracker)  （新增：tracker 转发）
          -> replays.run_oracle_write(case, tracker=tracker)
              -> 对于 gold_evidence 中 source_memory_id=None 且 source_event_id=None 的项:
                  -> tracker.record_edge(source_id=event_ids, target_id, "write", evidence.text)
              -> replays._score_recovered_evidence(case, "oracle_write", block, tracker)
                  -> ReplayResult(..., provenance_edges=tracker.get_edges() if tracker else ())
          -> replays.run_oracle_compression(case, tracker=tracker)
              -> 对于压缩丢失证据的 gold_evidence 项:
                  -> tracker.record_edge(source_id=source_memory_id, target_id, "compress", memory.text)
          -> replays.run_verbatim_event_oracle(case, tracker=tracker)
              -> 对于仅原始事件的 gold_evidence 项:
                  -> tracker.record_edge(source_id=source_event_id, target_id, "extract", event.text)
          -> replays.run_oracle_retrieval(case, tracker=tracker)
              -> 对于未在基线检索到的 gold_evidence 项:
                  -> tracker.record_edge(source_id=source_memory_id, target_id, "retrieve", memory.text)
          -> replays.run_injection_oracle(case, tracker=tracker)
              -> 对于已检索但注入错误的 gold_evidence 项:
                  -> tracker.record_edge(source_id=source_memory_id, target_id, "inject", memory.text)
          -> replays.run_evidence_given_reasoning(case, tracker=tracker)
              -> 如果 evidence_block 非空:
                  -> tracker.record_edge(source_id=retrieved_ids, target_id, "reason", evidence_block)
          -> replays.run_oracle_route(case, tracker=tracker)
              -> 对于最佳存储中的 gold_evidence 项:
                  -> tracker.record_edge(source_id=source_memory_id, target_id, "route", memory.text)
          -> replays.run_oracle_granularity(case, tracker=tracker)
              -> 对于最佳粒度级别的 gold_evidence 项:
                  -> tracker.record_edge(source_id=source_memory_id, target_id, "extract", memory.text)
          -> replays.run_graph_off(case, tracker=tracker)
              -> 对于非图扩展的 gold_evidence 项:
                  -> tracker.record_edge(source_id=source_memory_id, target_id, "retrieve", memory.text)
          -> replays.run_safety_off(case, tracker=tracker)
              -> 对于 safety_filter_blocked=True 时的 gold_evidence 项:
                  -> tracker.record_edge(source_id=evidence_id, target_id, "inject", evidence.text)
      -> 对于 replays 中的每个回放（查找 graph_off）:
          -> 如果 r.replay_name == "graph_off": graph_off_replay = r
      -> provenance.get_graph_distractor_edges(case, graph_off_replay)  （新增）
          -> 如果 recovery_gain <= 0: 返回 ()
          -> 对于 baseline.retrieved_memory_ids 中的每个 mid:
              -> 查找 case.extracted_memory 中的项
              -> 如果 item.is_graph_expanded:
                  -> 创建带有 HMAC 引用的 ProvenanceEdge(source_id=mid, target_id="{case_id}__answer", operation="retrieve")
          -> 返回 tuple[ProvenanceEdge, ...]
      -> attribution.assign_attribution_v1(replays, has_ingestion_trace=..., distractor_edges=distractor_edges)
          -> 按 recovery_gain 降序排列 replays
          -> top = ranked[0]
          -> attribution._v1_label_for_replay(top.replay_name, has_ingestion_trace=...)
          -> labels.validate_v1_label(predicted_label)
          -> AttributionResult(
              ...,
              distractor_provenance_ids=tuple(e.source_id for e in distractor_edges),
              distractor_provenance_edges=tuple(distractor_edges),
            )
      -> AuditResult(..., replays=replays（包含 provenance_edges）, attribution=attribution)

  -> harness.run_case_full_v1(ProbeCase)
      -> harness.run_case_v1(ProbeCase)           （V1 溯源归因）
      -> post_repair.draft_ecs(case, audit)
      -> post_repair.build_repaired_context(case, ecs_draft)
      -> post_repair.run_post_repair_context_replay(case, repaired_context)
      -> post_repair.run_hard_case_update_baseline(case)
      -> FullAuditResult(audit, ecs_draft, repaired_context, post_repair, hard_case_baseline)
```

### V1 预过滤溯源流水线（issue 0017）

```text
cmd_audit/__init__.py
  -> harness.run_case_v1_with_prefilter(ProbeCase, top_k=2, ...)
      -> baselines.run_baseline_suite(ProbeCase)
      -> prefix_guard.run_prefix_guard(case, baseline)        （Tier 1，除非 skip_prefix_guard）
      -> rpe_prefilter.run_rpe_prefilter(case, baseline, ...) （Tier 2）
      -> 如果 rpe_result.gate_decision: selected = rpe_result.selected_replays
         否则: selected = per_replay_ordering 中的前 2 个
      -> provenance.ProvenanceTracker(case.case_id)
      -> replays.run_v1_replay_portfolio_subset(case, selected, tracker=tracker)
          -> 对于 V1_REPLAY_NAMES 中在 selected 中的每个 name:
              -> _V1_REPLAY_DISPATCH[name](case, tracker=tracker)
      -> 如果 "graph_off" 在 selected 中:
          -> provenance.get_graph_distractor_edges(case, graph_off_replay)
      -> attribution.assign_attribution_v1(replays, ..., distractor_edges=..., top_k=min(top_k, len(replays)))
      -> AuditResult(...)
```

### 适配器溯源流水线（issue 0017）

```text
cmd_audit/adapters/harness.py
  -> _run_case_with_adapter(case, adapter, run_portfolio)
      -> baselines.run_baseline_suite(ProbeCase)
      -> provenance.ProvenanceTracker(case.case_id)
      -> run_portfolio(case, adapter, tracker=tracker)
          -> adapters._replay_skeleton.run_adapter_replay_portfolio(case, adapter, tracker=tracker)
              -> _run_oracle_write(case, adapter, tracker=tracker)
                  -> adapter.intercept_write(...) -> oracle texts
                  -> tracker.record_edge(source_id=f"adapter_input_{i}", target_id, "write", text)
              -> _run_oracle_compression(case, adapter, tracker=tracker)
                  -> tracker.record_edge(source_id=f"adapter_input_{i}", target_id, "compress", text)
              -> _run_verbatim_event_oracle(case, adapter, tracker=tracker)
                  -> tracker.record_edge(source_id=source_event_id, target_id, "extract", event.text)
              -> _run_oracle_retrieval(case, adapter, tracker=tracker)
                  -> adapter.intercept_search(...) -> oracle results
                  -> tracker.record_edge(source_id=memory_id, target_id, "retrieve", item.text)
              -> _run_injection_oracle(case, adapter, tracker=tracker)
                  -> tracker.record_edge(source_id=f"adapter_input_{i}", target_id, "inject", text)
              -> _run_evidence_given_reasoning(case, adapter, tracker=tracker)
                  -> tracker.record_edge(source_id=retrieved_ids, target_id, "reason", evidence_block)
              -> run_oracle_route(case, tracker=tracker)         （V1 透传）
              -> run_oracle_granularity(case, tracker=tracker)   （V1 透传）
              -> run_graph_off(case, tracker=tracker)            （V1 透传）
              -> run_safety_off(case, tracker=tracker)           （V1 透传）
      -> 查找 graph_off 回放
      -> provenance.get_graph_distractor_edges(case, graph_off_replay)
      -> attribution.assign_attribution_v1(replays, ..., distractor_edges=...)
      -> AuditResult(...)
```

### 行为测试路径

```text
tests/test_cmd_audit_issue17_provenance.py
  -> models.Citation(...)                                      （DataModelTest, 9 个测试）
  -> models.ProvenanceEdge(...)                                （DataModelTest）
  -> models.MemoryItem.from_mapping(raw)                       （DataModelTest）
  -> provenance.ProvenanceTracker(case_id)                     （ProvenanceTrackerTest, 12 个测试）
  -> provenance.record_provenance_edge(tracker, ...)           （ConvenienceWrapperTest, 1 个测试）
  -> replays.run_oracle_write(case, tracker=tracker)           （ProvenanceRecordingTest, 14 个测试）
  -> replays.run_v1_replay_portfolio(case, tracker=tracker)    （ProvenanceRecordingTest）
  -> provenance.compute_provenance_completeness(items)          （ProvenanceCompletenessTest, 5 个测试）
  -> provenance.detect_tamper(edge, source_text, session_key)   （TamperDetectionTest, 5 个测试）
  -> provenance.get_graph_distractor_edges(case, result)        （GraphErrorProvenanceTest, 5 个测试）
  -> replays.run_v0_replay_portfolio(case, tracker=None)       （BackwardCompatibilityTest, 9 个测试）
  -> harness.run_case(case)                                    （BackwardCompatibilityTest）
  -> harness.run_case_v1(case)                                 （BackwardCompatibilityTest, HarnessIntegrationTest）
  -> adapters.run_mem0_replay_portfolio(case, adapter, tracker=tracker)  （AdapterProvenanceTest, 6 个测试）
  -> adapters.run_letta_replay_portfolio(case, adapter, tracker=tracker) （AdapterProvenanceTest）
  -> adapters.run_case_with_mem0(case, trace)                  （AdapterProvenanceTest）
  -> writers.write_attribution_table(results, path)            （CSVOutputTest, 2 个测试）
  -> harness.write_comparison_metrics_table(results, path)     （CSVOutputTest）
  -> provenance._compute_hmac(content, session_key)            （HMACComputationTest, 4 个测试）
```

## 数据流

### 输入夹具

```text
data/probe_cases/v0_issue3_cases.json                       # 六案例 V0 烟雾套件（非回归，基础溯源）
data/probe_cases/v1_graph_error_case.json                    # 单一 graph_error 探针夹具（干扰项边）
data/probe_cases/v1_granularity_error_case.json              # 单一 granularity_error 探针夹具（粒度溯源）
data/probe_cases/v1_safety_error_case.json                   # 单一 safety_error 探针夹具（安全溯源）
data/probe_cases/v1_route_error_case.json                    # 单一 route_error 探针夹具（路由溯源）
data/probe_cases/v1_ingestion_error_case.json                # 单一 ingestion_error 探针夹具（摄入溯源）
data/probe_cases/mem0_v0_smoke_traces.json                   # mem0 适配器烟雾追踪（适配器溯源）
data/probe_cases/letta_v0_smoke_traces.json                  # Letta 适配器烟雾追踪（适配器溯源）
```

### v1_graph_error_case.json 的溯源数据流

此夹具演示了图扩展引入干扰项导致检索错误的情况。

| 字段 | 值 | 目的 |
| --- | --- | --- |
| `case_id` | `"v1-graph-001"` | 唯一案例标识符 |
| `perturbation_label` | `"graph_error"` | V1 标签 |
| `extracted_memory` 中的项 | 部分 `is_graph_expanded: true` | 图扩展引入的干扰项 |
| `baseline_outputs[0].retrieved_memory_ids` | 包含图扩展和非图扩展项 | 基线检索包含干扰项 |

溯源数据流步骤：
1. `run_case_v1` 创建 `ProvenanceTracker("v1-graph-001")` → session_key = sha256("v1-graph-001")
2. `run_graph_off(case, tracker=tracker)`：过滤掉 `is_graph_expanded=True` 的项，仅从直接记忆项恢复证据。对于每个成功恢复的非图扩展 gold_evidence 项 → `tracker.record_edge(source_id=memory_id, target_id="v1-graph-001__graph_off", operation="retrieve", source_text=memory.text)`
3. `_score_recovered_evidence` 返回带有 `provenance_edges=tracker.get_edges()` 的 `ReplayResult`（即非图扩展项的边）
4. `get_graph_distractor_edges(case, graph_off_result)`：
   - `graph_off_result.recovery_gain > 0` → 继续
   - `baseline_ids = set(baseline.retrieved_memory_ids)` — 基线检索到的所有 ID
   - 对于 baseline_ids 中的每个 mid：如果 `item.is_graph_expanded` → 创建 `ProvenanceEdge(source_id=mid, target_id="v1-graph-001__answer", operation="retrieve", citation=HMAC(item.text))`
   - 返回这些干扰项边的元组
5. `assign_attribution_v1` 接收 `distractor_edges` → `distractor_provenance_ids = tuple(e.source_id for e in distractor_edges)`
6. `AttributionResult` 携带 `distractor_provenance_ids`（干扰项 memory_id）和 `distractor_provenance_edges`（完整边）

### 中间类型

**Citation** 数据类（来自 `cmd_audit/models.py:70-74`）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `trajectory_turn` | `int` | 对话中的源轮次编号 |
| `char_span` | `tuple[int, int]` | 源文本中的 (start, end) 字符位置 |
| `content_hash` | `str` | 源文本的 HMAC-SHA256 内容哈希（64 字符十六进制） |

**ProvenanceEdge** 数据类（来自 `cmd_audit/models.py:78-85`）：

| 字段 | 类型 | 默认值 | 含义 |
| --- | --- | --- | --- |
| `source_id` | `str` | （必需） | 上游 MemoryItem id 或事件 id |
| `target_id` | `str` | （必需） | 此 MemoryItem 的 id（或合成目标 id） |
| `operation` | `str` | （必需） | write\|compress\|extract\|inject\|retrieve\|route\|reason |
| `citation` | `Citation` | （必需） | 具有轨迹位置和内容哈希的 HMAC 引用 |
| `timestamp` | `float` | （必需） | 记录时的 Unix 时间戳 |
| `tamper_detected` | `bool` | `False` | 当稍后重新计算的哈希不匹配时标记 |

**MemoryItem** 新增字段（来自 `cmd_audit/models.py:36`）：

| 字段 | 类型 | 默认值 | 含义 |
| --- | --- | --- | --- |
| `provenance` | `tuple` | `()` | 此 MemoryItem 的不可变入边溯源边。默认为空元组以确保向后兼容。仅在回放期间通过 `ProvenanceTracker.annotate_item()` 填充。 |

**ReplayResult** 新增字段（来自 `cmd_audit/replays.py:20`）：

| 字段 | 类型 | 默认值 | 含义 |
| --- | --- | --- | --- |
| `provenance_edges` | `tuple` | `()` | 此回放期间记录的所有溯源边。当 `tracker=None` 时为空。当 tracker 被提供时由 `_score_recovered_evidence` 从 `tracker.get_edges()` 填充。 |

**AttributionResult** 新增字段（来自 `cmd_audit/attribution.py:25-26`）：

| 字段 | 类型 | 默认值 | 含义 |
| --- | --- | --- | --- |
| `distractor_provenance_ids` | `tuple[str, ...]` | `()` | 被识别为干扰项的图扩展项的 source_id。从 `distractor_edges` 中提取。 |
| `distractor_provenance_edges` | `tuple` | `()` | 图扩展干扰项的完整 `ProvenanceEdge` 记录。从 `get_graph_distractor_edges` 传递过来。 |

### 输出产出物

溯源列出现在现有的 CSV 产出物中：

```text
artifacts/sandbox/
  attribution_table.csv            # 新增 distractor_provenance_ids 列（管道分隔的 memory_id）
  comparison_metrics.csv           # 新增 provenance_completeness 行（分数 + 分母/分子）
```

## 函数级合约

### `cmd_audit/models.py`

这是溯源数据模型模块。新增 2 个数据类，MemoryItem 上新增 1 个字段，`from_mapping()` 中新增解析逻辑。

---

#### 数据类：`Citation`

位置：`cmd_audit/models.py:70-74`

```python
@dataclass(frozen=True)
class Citation:
    """trace-mem HMAC citation to originating trajectory evidence."""
    trajectory_turn: int
    char_span: tuple[int, int]
    content_hash: str
```

目的：

- 为溯源边提供不可变的、加密可验证的引用，指回原始轨迹证据。
- `trajectory_turn` 定位对话中的源轮次。
- `char_span` 给出源文本中的字符精确位置。
- `content_hash` 存储源内容的 HMAC-SHA256，使篡改检测无需存储原始文本。

行为：

- 冻结数据类：字段创建后不可变（由 `frozen=True` 强制执行）。
- 所有三个字段都是必需的——没有默认值。
- `char_span` 是 `tuple[int, int]`，不是列表——符合不可变语义。
- 与 trace-mem 引用格式对齐（共享基础设施，用于未来与 trace-mem 反事实门的互操作）。

调用者：

- `ProvenanceTracker.record_edge`（`provenance.py:46-51`）
- `get_graph_distractor_edges`（`provenance.py:149-153`）
- `MemoryItem.from_mapping` 中的 provenance JSON 解析（`models.py:47-51`）
- `DataModelTest` 中的所有 9 个测试

---

#### 数据类：`ProvenanceEdge`

位置：`cmd_audit/models.py:78-85`

```python
@dataclass(frozen=True)
class ProvenanceEdge:
    """In-edge derivation record: which item+operation influenced this item."""
    source_id: str
    target_id: str
    operation: str  # write|compress|extract|inject|retrieve|route|reason
    citation: Citation
    timestamp: float
    tamper_detected: bool = False
```

目的：

- 代表 Execution Lineage DAG 中的一条有向边：`source_id --(operation)--> target_id`。
- `source_id` 标识上游项（MemoryItem 或事件）。
- `target_id` 标识下游项（MemoryItem 或合成 id，如 `"{case_id}__answer"`）。
- `operation` 是产生此影响的记忆操作（7 种操作类型）。
- `citation` 提供加密可验证的引用。
- `timestamp` 记录边创建的时间。
- `tamper_detected` 标记篡改（在使用 `detect_tamper` 进行后期验证时）。

行为：

- 冻结数据类：创建后不可变。
- `source_id`、`target_id`、`operation`、`citation`、`timestamp` 是必需的。
- `tamper_detected` 默认为 `False`——稍后通过 `detect_tamper()` 设置（但需要一个新的 `ProvenanceEdge` 实例，因为它是冻结的；在实践中，tamper 在验证时返回布尔值，而边本身保持不变）。
- 操作值约定：`write`、`compress`、`extract`、`inject`、`retrieve`、`route`、`reason`。没有正式的枚举验证——由 `record_edge` 调用者约定俗成。

调用者：

- `ProvenanceTracker.record_edge`（`provenance.py:52-58`）
- `get_graph_distractor_edges`（`provenance.py:154-159`）
- `MemoryItem.from_mapping` 中的 provenance JSON 解析（`models.py:43-54`）
- `DataModelTest` 中的所有测试
- `ProvenanceRecordingTest` 中的所有测试

---

#### 字段：`MemoryItem.provenance`

位置：`cmd_audit/models.py:36`

```python
provenance: tuple = ()
```

目的：

- 存储此 MemoryItem 的入边溯源边。
- 默认空元组 `()` 确保向后兼容：所有现有的 MemoryItem 构造函数无需更改即可工作。
- 使用 `tuple`（不可变）而非 `list` 以保持 dataclass 的 `frozen=True` 语义。
- 仅由 `ProvenanceTracker.annotate_item()` 填充——标准 MemoryItem 构造从不直接设置此字段。

行为：

- 默认值 `()` 意味着所有没有溯源创建的现有项具有 `provenance=()`。
- `compute_provenance_completeness` 将空元组视为"无溯源"（`if item.provenance` 对空元组为 False）。
- 当在 JSON 中序列化时，provenance 作为 `ProvenanceEdge` 映射列表出现。

调用者：

- `ProvenanceTracker.annotate_item`（`provenance.py:74-80`）——唯一写入者
- `compute_provenance_completeness`（`provenance.py:118`）——读取者
- `MemoryItem.from_mapping`（`models.py:40-58`）——从 JSON 构造

---

#### 方法：`MemoryItem.from_mapping`（已更新）

位置：`cmd_audit/models.py:39-66`

```python
@classmethod
def from_mapping(cls, value: dict[str, Any]) -> "MemoryItem":
    provenance_raw = value.get("provenance")
    if provenance_raw is not None:
        provenance = tuple(
            ProvenanceEdge(
                source_id=_required_str(e, "source_id"),
                target_id=_required_str(e, "target_id"),
                operation=_required_str(e, "operation"),
                citation=Citation(
                    trajectory_turn=int(e["citation"]["trajectory_turn"]),
                    char_span=tuple(e["citation"]["char_span"]),
                    content_hash=_required_str(e["citation"], "content_hash"),
                ),
                timestamp=float(e["timestamp"]),
                tamper_detected=bool(e.get("tamper_detected", False)),
            )
            for e in provenance_raw
        )
    else:
        provenance = ()
    return cls(
        memory_id=_required_str(value, "memory_id"),
        text=_required_str(value, "text"),
        source_event_ids=tuple(value.get("source_event_ids", ())),
        store=str(value.get("store", "default")),
        is_graph_expanded=bool(value.get("is_graph_expanded", False)),
        provenance=provenance,
    )
```

目的：

- 从 JSON 字典解析 MemoryItem，现支持可选的 `provenance` 字段。
- 向后兼容：当 JSON 中缺少 `provenance` 或为 `None` 时，默认为 `()`。

行为：

1. 检查 `value.get("provenance")`——如果为 `None` 或缺失，`provenance = ()`。
2. 如果存在，遍历每个边映射，构造 `ProvenanceEdge`（包含嵌套的 `Citation`）。
3. `char_span` 从 JSON 数组转换为元组：`tuple(e["citation"]["char_span"])`。
4. `tamper_detected` 是可选的，默认为 `False`。
5. `trajectory_turn` 和 `timestamp` 从 JSON 数字转换。
6. 所有字符串字段通过 `_required_str` 进行非空验证。

与 V0 的关系：

- V0 JSON 没有 `provenance` → 默认 `()` → 与之前的行为相同。
- `MemoryItem` 构造函数保持不变（仅添加了 `provenance` 关键字，默认 `()`）。

调用者：

- `ProbeCase.from_mapping`（`models.py:184-186`）
- `ProbeCase.from_mapping_v1`（`models.py:224-226`）
- `DataModelTest` 中的测试

---

### `cmd_audit/provenance.py`

这是 issue 0017 的核心新模块。文件：`cmd_audit/provenance.py`（164 行）。包含 1 个内部函数、1 个类、4 个公共函数。

---

#### 内部函数：`_compute_hmac(content: str, session_key: str) -> str`

位置：`cmd_audit/provenance.py:13-16`

```python
def _compute_hmac(content: str, session_key: str) -> str:
    return hmac.new(
        session_key.encode(), content.encode(), hashlib.sha256
    ).hexdigest()
```

目的：

- 使用案例特定的会话密钥计算源文本的 HMAC-SHA256 内容哈希。
- 与 trace-mem 引用格式对齐。
- 内部实现细节——不导出。

行为：

1. 将 `session_key`（64 字符十六进制字符串，来自 sha256(case_id)）编码为字节。
2. 将 `content`（源文本）编码为字节。
3. 使用 SHA256 作为哈希算法计算 HMAC。
4. 返回 64 字符十六进制摘要字符串。

确定性保证：

- 相同的 content + session_key → 始终相同的哈希。
- 不同的 content → 不同的哈希。
- 不同的 session_key → 不同的哈希。

调用者：

- `ProvenanceTracker.record_edge`（`provenance.py:46`）
- `get_graph_distractor_edges`（`provenance.py:152`）
- `detect_tamper`（`provenance.py:108`）
- `HMACComputationTest` 中的所有 4 个测试

---

#### 类：`ProvenanceTracker`

位置：`cmd_audit/provenance.py:19-81`

```python
class ProvenanceTracker:
    """Mutable collector for provenance edges recorded during counterfactual replay."""

    def __init__(self, case_id: str):
        self.case_id = case_id
        self._session_key = hashlib.sha256(case_id.encode()).hexdigest()
        self._edges: list[ProvenanceEdge] = []
        self._item_provenance: dict[str, list[ProvenanceEdge]] = {}

    @property
    def session_key(self) -> str:
        return self._session_key
```

目的：

- 可变收集器模式：在回放期间累积 `ProvenanceEdge` 记录，然后将其烘焙到不可变的 `MemoryItem` 副本中。
- 独立于 `MemoryItem`（冻结）追踪边，避免可变性问题。
- `_item_provenance` 字典按 target_id 索引边，以实现高效的 `annotate_item` 查找。
- `session_key` 派生自 `sha256(case_id)` 以实现每个案例的隔离——不同案例获得不同的密钥。

行为：

- `__init__(case_id)`：存储 case_id，推导 session_key = sha256(case_id)，初始化空的 _edges 列表和 _item_provenance 字典。
- `session_key` 属性：暴露 64 字符十六进制会话密钥。
- 使用 `object | None` 类型（而非 `ProvenanceTracker | None`）在回放函数签名中，以避免回放和溯源模块之间的循环导入。

调用者：

- `run_case_v1`（`harness.py:243`）
- `run_case_v1_with_prefilter`（`harness.py:345`）
- `_run_case_with_adapter`（`adapters/harness.py:30`）
- 所有 `ProvenanceRecordingTest` 测试
- 所有 `AdapterProvenanceTest` 测试

---

#### 方法：`ProvenanceTracker.record_edge`

位置：`cmd_audit/provenance.py:37-61`

```python
def record_edge(
    self,
    source_id: str,
    target_id: str,
    operation: str,
    source_text: str,
    char_span: tuple[int, int] = (0, 0),
    trajectory_turn: int = 0,
) -> ProvenanceEdge:
    content_hash = _compute_hmac(source_text, self._session_key)
    citation = Citation(
        trajectory_turn=trajectory_turn,
        char_span=char_span,
        content_hash=content_hash,
    )
    edge = ProvenanceEdge(
        source_id=source_id,
        target_id=target_id,
        operation=operation,
        citation=citation,
        timestamp=time.time(),
    )
    self._edges.append(edge)
    self._item_provenance.setdefault(target_id, []).append(edge)
    return edge
```

目的：

- 主要记录方法：从参数创建 `ProvenanceEdge`，计算 HMAC 内容哈希，附加到内部列表，按 target_id 索引，返回边。
- 每个回放函数在找到可恢复的证据时调用此方法。

行为：

1. 使用 `_compute_hmac(source_text, self._session_key)` 计算 `content_hash`。
2. 从参数 + 计算出的哈希构造 `Citation`。
3. 从 source_id、target_id、operation、citation 和 `time.time()` 构造 `ProvenanceEdge`。
4. 附加到 `self._edges`（按时间顺序的完整列表）。
5. 附加到 `self._item_provenance[target_id]`（按目标索引以实现高效检索）。
6. 返回边以供可选使用。

`char_span` 和 `trajectory_turn` 参数默认为 `(0, 0)` 和 `0`——在大多数回放路径中，精确的轨迹定位不可用，因此使用零默认值。当信息可用时，便利包装器 `record_provenance_edge` 允许调用者指定它们。

调用者：

- `record_provenance_edge`（`provenance.py:94`）——模块级便利包装器
- 10 个回放函数（`replays.py`）——每个都在其溯源记录块中调用 `tracker.record_edge(...)`
- 6 个适配器拦截回放函数（`adapters/_replay_skeleton.py`）
- `ProvenanceTrackerTest` 中的所有 12 个测试

---

#### 方法：`ProvenanceTracker.get_edges`

位置：`cmd_audit/provenance.py:63-64`

```python
def get_edges(self) -> tuple[ProvenanceEdge, ...]:
    return tuple(self._edges)
```

目的：

- 返回所有记录的边的不可变快照。
- 由 `_score_recovered_evidence` 调用以填充 `ReplayResult.provenance_edges`。

行为：

- 将内部列表转换为元组（不可变）。
- 返回空元组如果没有记录边。
- 幂等：多次调用返回相同的边。

调用者：

- `_score_recovered_evidence`（`replays.py:342`）
- `ProvenanceTrackerTest.test_get_edges_*` 测试

---

#### 方法：`ProvenanceTracker.annotate_item`

位置：`cmd_audit/provenance.py:66-81`

```python
def annotate_item(
    self, item: MemoryItem, target_id: str | None = None
) -> MemoryItem:
    """Return a new MemoryItem with recorded provenance edges attached."""
    tid = target_id or item.memory_id
    edges = tuple(self._item_provenance.get(tid, ()))
    if not edges:
        return item
    return MemoryItem(
        memory_id=item.memory_id,
        text=item.text,
        source_event_ids=item.source_event_ids,
        store=item.store,
        is_graph_expanded=item.is_graph_expanded,
        provenance=edges,
    )
```

目的：

- 通过将累积的溯源边烘焙到新副本中，弥合可变追踪器和不可变 MemoryItem 之间的差距。
- 如果未记录边，返回相同的项（性能优化，避免不必要的复制）。
- 接受可选的显式 `target_id` 以按非 memory_id 目标（如 `"{case_id}__oracle_write"`）查找。

行为：

1. 如果未提供 `target_id`，使用 `item.memory_id`。
2. 从 `_item_provenance` 字典中查找该 target_id 的边。
3. 如果未找到边：返回相同的项实例（身份保留）。
4. 如果找到边：使用原始字段 + `provenance=edges` 创建新的 `MemoryItem`。
5. 返回的项保留原始项的所有字段（memory_id、text、source_event_ids、store、is_graph_expanded）。

调用者：

- 当前在 V1 独立流水线中未直接调用——边通过 `ReplayResult.provenance_edges` 和 `AttributionResult.distractor_provenance_edges` 暴露。`annotate_item` 设计用于未来使用，当项需要带烘焙边的持久化时（V2 MemQ 集成）。
- `ProvenanceTrackerTest.test_annotate_item_*` 测试

---

#### 函数：`record_provenance_edge`

位置：`cmd_audit/provenance.py:84-101`

```python
def record_provenance_edge(
    tracker: ProvenanceTracker,
    source_id: str,
    target_id: str,
    operation: str,
    source_text: str,
    char_span: tuple[int, int] = (0, 0),
    trajectory_turn: int = 0,
) -> ProvenanceEdge:
    """Convenience wrapper around ``ProvenanceTracker.record_edge``."""
    return tracker.record_edge(
        source_id=source_id,
        target_id=target_id,
        operation=operation,
        source_text=source_text,
        char_span=char_span,
        trajectory_turn=trajectory_turn,
    )
```

目的：

- 模块级便利包装器，暴露完整的参数集。
- 当调用者需要指定 `char_span` 和 `trajectory_turn`（超出 `tracker.record_edge` 默认值）时使用。

行为：

- 直接委托给 `tracker.record_edge(...)`，转发所有参数。

调用者：

- `ConvenienceWrapperTest.test_wrapper_delegates_to_tracker`

---

#### 函数：`detect_tamper`

位置：`cmd_audit/provenance.py:104-109`

```python
def detect_tamper(
    edge: ProvenanceEdge, source_text: str, session_key: str
) -> bool:
    """Return True when the recomputed HMAC differs from the stored citation hash."""
    recomputed = _compute_hmac(source_text, session_key)
    return recomputed != edge.citation.content_hash
```

目的：

- 通过重新计算源文本的 HMAC 并与存储的哈希比较来检测溯源边引用的内容是否已被篡改。

行为：

1. 使用提供的 `source_text` 和 `session_key` 调用 `_compute_hmac`。
2. 将重新计算的哈希与 `edge.citation.content_hash` 比较。
3. 返回 `True` 如果不匹配（检测到篡改），`False` 如果匹配（内容完整）。

用例：

- 当回放读取源项时，重新计算哈希并与存储的引用比较——不匹配 → 标记 `tamper_detected=True`。
- 在 V1 中，这是一个独立检查。在 V2 中，它可以集成到自动篡改响应中。

调用者：

- `TamperDetectionTest` 中的所有 5 个测试

---

#### 函数：`compute_provenance_completeness`

位置：`cmd_audit/provenance.py:112-119`

```python
def compute_provenance_completeness(
    memory_items: tuple[MemoryItem, ...],
) -> float:
    """Fraction of MemoryItems with non-empty provenance edges."""
    if not memory_items:
        return 0.0
    num_with_prov = sum(1 for item in memory_items if item.provenance)
    return num_with_prov / len(memory_items)
```

目的：

- 计算具有非空溯源边的记忆项的分数。
- 论文面向的指标：跟踪整个案例套件的溯源覆盖率。
- 目标：≥ 80%（某些在回放之外创建的项可能具有空溯源）。

行为：

1. 如果 `memory_items` 为空元组：返回 `0.0`（避免除零）。
2. 统计 `item.provenance` 为真（非空元组）的项。
3. 返回 count / total 作为浮点数。

调用者：

- `harness.py` 中的 `write_comparison_metrics_table`（间接地，通过从回放项计算）
- `ProvenanceCompletenessTest` 中的所有 5 个测试

---

#### 函数：`get_graph_distractor_edges`

位置：`cmd_audit/provenance.py:122-163`

```python
def get_graph_distractor_edges(
    case: ProbeCase, graph_off_result: ReplayResult
) -> tuple[ProvenanceEdge, ...]:
    """Identify graph-expanded items in baseline retrieval that acted as distractors."""
    if graph_off_result.recovery_gain <= 0:
        return ()

    baseline = case.primary_baseline
    baseline_ids = set(baseline.retrieved_memory_ids)
    memory_by_id = {item.memory_id: item for item in case.extracted_memory}

    session_key = hashlib.sha256(case.case_id.encode()).hexdigest()
    now = time.time()
    edges: list[ProvenanceEdge] = []

    for mid in baseline_ids:
        item = memory_by_id.get(mid)
        if item is None:
            continue
        if not item.is_graph_expanded:
            continue
        citation = Citation(
            trajectory_turn=0,
            char_span=(0, len(item.text)),
            content_hash=_compute_hmac(item.text, session_key),
        )
        edge = ProvenanceEdge(
            source_id=mid,
            target_id=f"{case.case_id}__answer",
            operation="retrieve",
            citation=citation,
            timestamp=now,
        )
        edges.append(edge)

    return tuple(edges)
```

目的：

- 图错误归因的核心：标识基线检索中哪些图扩展记忆项充当了干扰项。
- 将基线 `retrieved_memory_ids` 与 `is_graph_expanded` 项比较——仅通过图扩展出现的项是干扰项。

行为：

1. 守卫：如果 `graph_off_result.recovery_gain <= 0`，graph_off 回放没有产生恢复，因此没有干扰项可报告 → 返回 `()`。
2. 从 `case.primary_baseline.retrieved_memory_ids` 获取基线检索到的 ID。
3. 按 memory_id 索引 `case.extracted_memory` 以进行 O(1) 查找。
4. 为案例推导 `session_key`（与 `ProvenanceTracker` 相同的方式，以确保一致的哈希）。
5. 对于基线检索到的每个记忆项：
   - 如果项缺失 → 跳过。
   - 如果项不是 `is_graph_expanded` → 跳过（它不是图干扰项）。
   - 为该项创建 `ProvenanceEdge`，其中 `target_id="{case_id}__answer"`，`operation="retrieve"`。
   - `char_span=(0, len(item.text))` —— 引用整个项文本。
6. 返回干扰项边的元组。

集成流程：

1. `run_case_v1` 调用 `get_graph_distractor_edges(case, graph_off_replay)`。
2. 结果通过 `distractor_edges` 参数传递给 `assign_attribution_v1`。
3. `assign_attribution_v1` 提取 `distractor_provenance_ids = tuple(e.source_id for e in distractor_edges)`。
4. 归因表显示哪些记忆 ID 是干扰项。

调用者：

- `run_case_v1`（`harness.py:253`）
- `run_case_v1_with_prefilter`（`harness.py:352`，仅在 graph_off 在子集中时）
- `_run_case_with_adapter`（`adapters/harness.py:36`）
- `GraphErrorProvenanceTest` 中的所有 5 个测试

---

### `cmd_audit/replays.py`

文件：`cmd_audit/replays.py`（568 行）。Issue 0017 新增：`ReplayResult` 上的 1 个字段，`_score_recovered_evidence` 中的 tracker 参数，所有 10 个回放中的 `*, tracker` 参数和溯源记录块，组合函数中的 tracker 转发，子集组合函数中的 tracker 转发。

---

#### 字段：`ReplayResult.provenance_edges`

位置：`cmd_audit/replays.py:20`

```python
provenance_edges: tuple = ()
```

目的：

- 在此回放期间记录的所有 `ProvenanceEdge` 记录。
- 当 `tracker=None` 时为空（向后兼容）。
- 当提供 tracker 时由 `_score_recovered_evidence` 填充。

---

#### 函数：`_score_recovered_evidence`（已更新）

位置：`cmd_audit/replays.py:325-343`

```python
def _score_recovered_evidence(
    case: ProbeCase,
    replay_name: str,
    evidence_block: str,
    tracker: object | None = None,
) -> ReplayResult:
    baseline = case.primary_baseline
    evidence_score = evidence_recall_from_text(case.gold_evidence, evidence_block)
    answer = case.gold_answer if evidence_score == 1.0 else ""
    recovered_answer_score = answer_score(answer, case.gold_answer)
    return ReplayResult(
        replay_name=replay_name,
        answer=answer,
        answer_score=recovered_answer_score,
        evidence_score=evidence_score,
        evidence_block=evidence_block,
        recovery_gain=recovered_answer_score - baseline.answer_score,
        provenance_edges=tracker.get_edges() if tracker else (),
    )
```

目的：

- 共享的评分和 ReplayResult 构造函数，现在也烘焙溯源边。
- 当 `tracker` 不为 None 时调用 `tracker.get_edges()` 以获取所有记录的边的不可变快照。

行为：

- `tracker: object | None = None` —— 使用 `object` 类型以避免循环导入。（如果使用 `ProvenanceTracker | None`，replays.py 需要从 provenance.py 导入，而 provenance.py 已经导入 ReplayResult，创建循环。）
- 当 `tracker` 为 None 时，`provenance_edges=()`（向后兼容，无溯源）。
- 当 tracker 被提供时，`provenance_edges=tracker.get_edges()`（包含来自此回放的所有边）。

调用者：

- 所有 10 个回放函数（每个都以 `_score_recovered_evidence(case, replay_name, block, tracker)` 结尾）

---

#### 函数：`run_oracle_write`（已更新）

位置：`cmd_audit/replays.py:70-91`

```python
def run_oracle_write(
    case: ProbeCase, *, tracker: object | None = None
) -> ReplayResult:
```

溯源记录逻辑（第 80-90 行）：对于每个 `source_memory_id is None and source_event_id is None` 的 gold_evidence 项，使用 `operation="write"`、`source_id=event_ids`（管道分隔）或 `evidence_id`、`target_id="{case_id}__oracle_write"`、`source_text=evidence.text` 记录边。这捕获了"证据从未被写入，但应在摄入时被写入"的语义。

---

#### 函数：`run_oracle_compression`（已更新）

位置：`cmd_audit/replays.py:94-126`

溯源记录逻辑（第 109-123 行）：对于每个具有 `source_memory_id` 且 `evidence_recall_from_text < 1.0` 的 gold_evidence 项（即，压缩丢失了证据），使用 `operation="compress"`、`source_id=evidence.source_memory_id`、`target_id="{case_id}__oracle_compression"`、`source_text=memory.text` 记录边。

---

#### 函数：`run_oracle_retrieval`（已更新）

位置：`cmd_audit/replays.py:129-160`

溯源记录逻辑（第 139-157 行）：对于每个具有 `source_memory_id` 且不在 `baseline_retrieved_ids` 中、且在默认存储中、且具有完全证据召回的 gold_evidence 项，使用 `operation="retrieve"`、`source_id=evidence.source_memory_id`、`target_id="{case_id}__oracle_retrieval"`、`source_text=memory.text` 记录边。

---

#### 函数：`run_verbatim_event_oracle`（已更新）

位置：`cmd_audit/replays.py:163-190`

溯源记录逻辑（第 174-187 行）：对于每个没有 `source_memory_id` 但有 `source_event_id` 的 gold_evidence 项，使用 `operation="extract"`、`source_id=evidence.source_event_id`、`target_id="{case_id}__verbatim_event"`、`source_text=event.text` 记录边。

---

#### 函数：`run_injection_oracle`（已更新）

位置：`cmd_audit/replays.py:193-229`

溯源记录逻辑（第 214-226 行）：对于每个 `source_memory_id` 在 `retrieved_ids` 中且具有完全证据召回的 gold_evidence 项，使用 `operation="inject"`、`source_id=evidence.source_memory_id`、`target_id="{case_id}__injection"`、`source_text=memory.text` 记录边。

---

#### 函数：`run_evidence_given_reasoning`（已更新）

位置：`cmd_audit/replays.py:232-253`

溯源记录逻辑（第 242-250 行）：当 `evidence_block` 非空时，使用 `operation="reason"`、`source_id=baseline.retrieved_memory_ids`（管道分隔）、`target_id="{case_id}__reasoning"`、`source_text=evidence_block` 记录边。

---

#### 函数：`run_oracle_route`（已更新）

位置：`cmd_audit/replays.py:256-296`

溯源记录逻辑（第 278-295 行）：对于每个具有 `source_memory_id`、memory.store 匹配 `best_store`、且具有完全证据召回的 gold_evidence 项，使用 `operation="route"`、`source_id=evidence.source_memory_id`、`target_id="{case_id}__route"`、`source_text=memory.text` 记录边。

---

#### 函数：`run_oracle_granularity`（已更新）

位置：`cmd_audit/replays.py:380-437`

溯源记录逻辑（第 414-434 行）：当找到比当前级别更好的粒度级别时，对于每个具有 `source_memory_id`、`granularity_level` 匹配最佳级别或为 None、且具有完全证据召回的 gold_evidence 项，使用 `operation="extract"`、`source_id=evidence.source_memory_id`、`target_id="{case_id}__granularity"`、`source_text=memory.text` 记录边。

---

#### 函数：`run_graph_off`（已更新）

位置：`cmd_audit/replays.py:440-472`

溯源记录逻辑（第 454-471 行）：对于每个具有 `source_memory_id`、`is_graph_expanded=False`、且具有完全证据召回的 gold_evidence 项，使用 `operation="retrieve"`、`source_id=evidence.source_memory_id`、`target_id="{case_id}__graph_off"`、`source_text=memory.text` 记录边。

---

#### 函数：`run_safety_off`（已更新）

位置：`cmd_audit/replays.py:475-497`

溯源记录逻辑（第 488-496 行）：当 `case.safety_filter_blocked=True` 时，对于每个 gold_evidence 项，使用 `operation="inject"`、`source_id=evidence.evidence_id`、`target_id="{case_id}__safety_off"`、`source_text=evidence.text` 记录边。

---

#### 函数：`run_v0_replay_portfolio`（已更新）

位置：`cmd_audit/replays.py:36-48`

```python
def run_v0_replay_portfolio(
    case: ProbeCase, *, tracker: object | None = None
) -> tuple[ReplayResult, ...]:
```

目的：运行 6 回放 V0 组合，将 tracker 转发到每个回放。向后兼容：现有调用者省略 tracker → 所有回放获得 `tracker=None` → 所有 `provenance_edges=()`。

---

#### 函数：`run_v1_replay_portfolio`（已更新）

位置：`cmd_audit/replays.py:51-67`

目的：运行 10 回放 V1 组合，将 tracker 转发到每个回放。与 V0 版本相同的模式，增加了 4 个 V1 回放（oracle_route、oracle_granularity、graph_off、safety_off）。

---

#### 函数：`run_v1_replay_portfolio_subset`

位置：`cmd_audit/replays.py:556-567`

```python
def run_v1_replay_portfolio_subset(
    case: ProbeCase,
    replay_names: tuple[str, ...],
    *,
    tracker: object | None = None,
) -> tuple[ReplayResult, ...]:
```

目的：

- 仅运行命名的 V1 回放子集，按组合顺序排列。
- 由 `run_case_v1_with_prefilter` 使用，以运行 RPE 预过滤器选择的 2-4 个回放。
- 通过 `_V1_REPLAY_DISPATCH` 字典查找回放函数。

行为：

1. 遍历 `V1_REPLAY_NAMES`（保持组合顺序）。
2. 对于每个在 `replay_names` 中的名称，调用 `_V1_REPLAY_DISPATCH[name](case, tracker=tracker)`。
3. 返回仅包含选中回放的元组。

`_V1_REPLAY_DISPATCH` 字典（第 542-553 行）将回放名称映射到函数引用，以避免大型 if-elif 链。

---

### `cmd_audit/attribution.py`

文件：`cmd_audit/attribution.py`（139 行）。Issue 0017 新增：`AttributionResult` 上的 2 个字段，`assign_attribution_v1` 中的 1 个参数。

---

#### 字段：`AttributionResult.distractor_provenance_ids`

位置：`cmd_audit/attribution.py:25`

```python
distractor_provenance_ids: tuple[str, ...] = ()
```

目的：来自图扩展干扰项的 source_id（memory_id），在 CSV 中报告为管道分隔列表。

---

#### 字段：`AttributionResult.distractor_provenance_edges`

位置：`cmd_audit/attribution.py:26`

```python
distractor_provenance_edges: tuple = ()
```

目的：图扩展干扰项的完整 `ProvenanceEdge` 记录。默认 `()`（`tuple`，非类型化默认值以匹配 `tuple = ()` 模式）。

---

#### 函数：`assign_attribution_v1`（已更新）

位置：`cmd_audit/attribution.py:63-122`

新增参数：

```python
def assign_attribution_v1(
    replay_results: tuple[ReplayResult, ...],
    *,
    has_ingestion_trace: bool = True,
    positive_gain_threshold: float = 0.0,
    tie_margin: float = 0.05,
    top_k: int = 2,
    distractor_edges: tuple = (),       # ← 新增
) -> AttributionResult:
```

新增逻辑（第 118-121 行）：

```python
distractor_provenance_ids=tuple(
    e.source_id for e in distractor_edges
),
distractor_provenance_edges=tuple(distractor_edges),
```

目的：将干扰项溯源信息从流水线传输到归因结果。当 `distractor_edges=()`（默认值）时，两个字段保持为空元组——向后兼容。

---

### `cmd_audit/harness.py`

文件：`cmd_audit/harness.py`（379 行）。Issue 0017 新增：`run_case_v1` 中的 `ProvenanceTracker` 集成，`write_comparison_metrics_table` 中的 `provenance_completeness` 行。

---

#### 函数：`run_case_v1`（已更新）

位置：`cmd_audit/harness.py:240-271`

关键溯源新增内容：

```python
def run_case_v1(case: ProbeCase, *, top_k: int = 2) -> AuditResult:
    baseline_suite = run_baseline_suite(case)
    tracker = ProvenanceTracker(case.case_id)             # ← 新增
    replays = run_v1_replay_portfolio(case, tracker=tracker)  # ← tracker 转发

    graph_off_replay = None
    for r in replays:                                      # ← 新增块
        if r.replay_name == "graph_off":
            graph_off_replay = r
            break
    distractor_edges = ()
    if graph_off_replay is not None:
        distractor_edges = get_graph_distractor_edges(case, graph_off_replay)

    attribution = assign_attribution_v1(
        replays,
        has_ingestion_trace=case.has_ingestion_trace,
        top_k=top_k,
        distractor_edges=distractor_edges,                 # ← 新增参数
    )
    ...
```

目的：

- V1 流水线现在自动创建 `ProvenanceTracker`，将其转发到所有回放，检测 graph_off 干扰项，并将干扰项边传递给归因。
- 干扰项检测仅在 `graph_off` 回放存在于组合中时发生（在完整的 10 回放 V1 组合中始终存在）。

---

#### 函数：`run_case_v1_with_prefilter`（已更新）

位置：`cmd_audit/harness.py:303-371`

相同的 tracker + 干扰项模式，使用 `run_v1_replay_portfolio_subset`：

```python
tracker = ProvenanceTracker(case.case_id)
replays = run_v1_replay_portfolio_subset(case, selected, tracker=tracker)

distractor_edges = ()
if "graph_off" in selected:                               # ← 守卫
    for r in replays:
        if r.replay_name == "graph_off":
            distractor_edges = get_graph_distractor_edges(case, r)
            break
```

目的：与 `run_case_v1` 相同的溯源集成，但仅在 RPE 预过滤器选择 graph_off 回放时计算干扰项（当案例可能涉及图扩展干扰时）。

---

#### 函数：`write_comparison_metrics_table`（已更新）

位置：`cmd_audit/harness.py:171-234`

新增的溯源完整性块（第 209-234 行）：

```python
total_replays = sum(len(result.replays) for result in results)
replays_with_prov = sum(
    sum(1 for replay in result.replays if replay.provenance_edges)
    for result in results
)
provenance_completeness = (
    replays_with_prov / total_replays if total_replays > 0 else 0.0
)
fieldnames.append("provenance_completeness")
rows.append(
    {
        "system_name": "CMD-Audit",
        ...
        "provenance_completeness": f"{replays_with_prov}/{total_replays}",
    }
)
```

目的：

- 计算并报告具有非空溯源边的回放分数。
- 在最后一行显示为"CMD-Audit"的 `provenance_completeness`。
- `macro_f1` 列携带实际的完整性分数值；`provenance_completeness` 列携带 `"{numerator}/{denominator}"` 格式。

---

### `cmd_audit/writers.py`

---

#### 函数：`write_attribution_table`（已更新）

位置：`cmd_audit/writers.py:73-149`

新增列：

在 `fieldnames` 中（第 103 行）：`"distractor_provenance_ids"`

在行字典中（第 131 行）：

```python
"distractor_provenance_ids": "|".join(
    result.attribution.distractor_provenance_ids
),
```

目的：在归因表中将干扰项溯源 ID 显示为管道分隔列表。当无干扰项时为空字符串。

---

### `cmd_audit/adapters/_replay_skeleton.py`

所有 6 个拦截回放函数遵循与独立回放相同的模式，但使用适配器输入记录溯源。

---

#### 函数：`run_adapter_replay_portfolio`（已更新）

位置：`cmd_audit/adapters/_replay_skeleton.py:22-39`

```python
def run_adapter_replay_portfolio(
    case: ProbeCase, adapter, *, tracker: object | None = None
) -> tuple[ReplayResult, ...]:
```

将 tracker 转发到所有 10 个回放（6 个拦截 + 4 个 V1 透传）。

---

#### 函数：`_run_oracle_write`（已更新）

位置：`cmd_audit/adapters/_replay_skeleton.py:45-59`

```python
def _run_oracle_write(
    case: ProbeCase, adapter, *, tracker: object | None = None
) -> ReplayResult:
```

溯源记录：对于每个由 `adapter.intercept_write` 返回的 oracle 文本，使用 `operation="write"`、`source_id=f"adapter_input_{i}"`、`target_id="{case_id}__oracle_write"`、`source_text=text` 记录边。

---

#### 函数：`_run_oracle_compression`（已更新）

位置：`cmd_audit/adapters/_replay_skeleton.py:62-78`

溯源记录：对于每个 oracle 文本，使用 `operation="compress"`、`source_id=f"adapter_input_{i}"`、`target_id="{case_id}__oracle_compression"`、`source_text=text` 记录边。

---

#### 函数：`_run_verbatim_event_oracle`（已更新）

位置：`cmd_audit/adapters/_replay_skeleton.py:81-103`

溯源记录：对于每个仅原始事件的 gold_evidence 项，使用 `operation="extract"`、`source_id=evidence.source_event_id`、`target_id="{case_id}__verbatim_event"`、`source_text=event.text` 记录边。

---

#### 函数：`_run_injection_oracle`（已更新）

位置：`cmd_audit/adapters/_replay_skeleton.py:106-126`

溯源记录：对于每个 oracle 文本，使用 `operation="inject"`、`source_id=f"adapter_input_{i}"`、`target_id="{case_id}__injection"`、`source_text=text` 记录边。

---

#### 函数：`_run_oracle_retrieval`（已更新）

位置：`cmd_audit/adapters/_replay_skeleton.py:132-154`

溯源记录：对于每个 oracle 结果项，使用 `operation="retrieve"`、`source_id=item.memory_id`、`target_id="{case_id}__oracle_retrieval"`、`source_text=item.text` 记录边。

---

#### 函数：`_run_evidence_given_reasoning`（已更新）

位置：`cmd_audit/adapters/_replay_skeleton.py:157-182`

溯源记录：当 `evidence_block` 非空时，使用 `operation="reason"`、`source_id=baseline.retrieved_memory_ids`（管道分隔）、`target_id="{case_id}__reasoning"`、`source_text=evidence_block` 记录边。

---

### `cmd_audit/adapters/harness.py`

---

#### 函数：`_run_case_with_adapter`（已更新）

位置：`cmd_audit/adapters/harness.py:21-55`

关键溯源新增内容：

```python
tracker = ProvenanceTracker(case.case_id)
replays = run_portfolio(case, adapter, tracker=tracker)

distractor_edges = ()
for r in replays:
    if r.replay_name == "graph_off":
        distractor_edges = get_graph_distractor_edges(case, r)
        break

attribution = assign_attribution_v1(
    replays,
    has_ingestion_trace=case.has_ingestion_trace,
    top_k=top_k,
    distractor_edges=distractor_edges,
)
```

目的：适配器路径自动获得与独立 V1 流水线相同的溯源集成。

---

## 测试结构

`tests/test_cmd_audit_issue17_provenance.py` — 12 个测试类，78 个测试方法。

| 测试类 | 验收标准 | 测试数量 | 覆盖内容 |
| --- | --- | --- | --- |
| `DataModelTest` | AC16.1 | 9 | Citation 和 ProvenanceEdge 字段冻结、必需字段、tamper_detected 默认值、MemoryItem.provenance 默认为空元组、带边的 provenance、from_mapping 解析 provenance（单个/多个/缺失）、冻结禁止变更 |
| `ProvenanceTrackerTest` | (基础设施) | 12 | case_id 存储、session_key 确定性/隔离、record_edge 返回 ProvenanceEdge、HMAC 计算、get_edges 空/满、annotate_item 无边的身份保留、annotate_item 烘焙 provenance、显式 target_id、多条边的累积 |
| `ConvenienceWrapperTest` | (基础设施) | 1 | record_provenance_edge 委托给 tracker，包括 char_span 和 trajectory_turn |
| `ProvenanceRecordingTest` | AC16.2 | 14 | 10 个独立回放测试（每种回放类型一个）+ ReplayResult 携带边 + V0 组合转发 + V1 组合转发 + 适配器组合转发 |
| `ProvenanceCompletenessTest` | AC16.3 | 5 | 所有/部分/无/空/单个项的完整性分数 |
| `TamperDetectionTest` | AC16.4 | 5 | 有效内容通过、修改内容检测、错误密钥检测、空内容检测、确定性 |
| `GraphErrorProvenanceTest` | AC16.5 | 5 | 无图扩展项时无边、无恢复时无边、边是 ProvenanceEdge 且 operation="retrieve"、distractor_ids 流入归因、图案例产生干扰项边 |
| `BackwardCompatibilityTest` | AC16.6 | 9 | 无 provenance 的旧 MemoryItem、无 tracker 的回放返回空 provenance、V0/V1 组合无 tracker、AttributionResult 默认值、assign_attribution_v1 默认值、run_case_v0 仍然工作、run_case_v1 产生 provenance、ReplayResult 默认构造函数无 provenance |
| `AdapterProvenanceTest` | AC16.7 | 6 | mem0 适配器组合记录 provenance、Letta 适配器组合记录 provenance、mem0 案例运行器集成 provenance、Letta 案例运行器集成 provenance、mem0 边有效（操作、引用）、Letta 边有效（操作、引用） |
| `CSVOutputTest` | AC16.8 | 2 | 归因表具有 distractor_provenance_ids 列、比较指标表具有 provenance_completeness 行 |
| `HarnessIntegrationTest` | AC16.9 | 6 | run_case_v1 所有回放具有 provenance 字段、图案例获得干扰项、graph_off 回放产生恢复、预过滤器包含 provenance、边具有 64 字符十六进制 HMAC 哈希、所有 10 个回放名称可用 |
| `HMACComputationTest` | (基础设施) | 4 | HMAC 确定性、内容差异、密钥差异、64 字符十六进制格式 |

## 非回归分析

### 受影响的现有测试

Issue 0017 以两种方式修改了现有的测试表面：

1. **`ReplayResult` 构造函数获得新字段 `provenance_edges: tuple = ()`**：任何显式构造 `ReplayResult(...)` 的现有测试无需更改，因为默认值 `()` 在省略时应用。所有辅助函数如 `_make_replay(...)` 继续工作。

2. **`write_comparison_metrics_table` 获得最后一个 `provenance_completeness` 行**：读取比较指标 CSV 并断言所有行具有 `memory_probe_best_accuracy` 值的现有测试（如 `ComparisonMetricsWithMemoryProbeTest`）现在在 provenance_completeness 行中看到相同的格式值（`f"{memory_probe_best_accuracy:.3f}"`）而非空字符串。这在实现过程中得到了修复。

### 向后兼容性保证

所有新增内容使用默认参数值，确保以下代码模式无需修改即可继续工作：

| 模式 | 兼容性机制 |
| --- | --- |
| `MemoryItem(memory_id="m1", text="text")` | `provenance: tuple = ()` 默认值 |
| `MemoryItem.from_mapping({"memory_id": "m1", "text": "text"})` | `value.get("provenance")` 对缺失键返回 `None` → `provenance = ()` |
| `ReplayResult(replay_name="r", answer="", answer_score=0.0, evidence_score=0.0, evidence_block="", recovery_gain=0.0)` | `provenance_edges: tuple = ()` 默认值 |
| `run_oracle_write(case)` — 无 tracker | `tracker: object \| None = None` 默认值 → 无溯源记录 → `provenance_edges=()` |
| `run_v0_replay_portfolio(case)` — 无 tracker | tracker 默认为 None，转发到所有回放 |
| `run_v1_replay_portfolio(case)` — 无 tracker | tracker 默认为 None，转发到所有回放 |
| `assign_attribution(replays)` | `AttributionResult` 新字段默认为 `()` |
| `assign_attribution_v1(replays)` | `distractor_edges: tuple = ()` 默认值 |
| `run_case(case)` — V0 流水线 | 未更改：不使用 tracker，不使用干扰项检测 |
| `write_attribution_table(results, path)` | `distractor_provenance_ids` 列添加，默认为空字符串 |
| `write_comparison_metrics_table(results, path)` | `provenance_completeness` 行添加；当 `memory_probe_best_accuracy` 为 None 时跳过 |

### 与所有现有测试的验证

```bash
python -m pytest tests/ -v
# 613 个测试通过，2,705 个子测试通过（包括 78 个新 provenance 测试）
```

### 类型注解说明

回放函数签名使用 `tracker: object | None = None` 而非 `tracker: ProvenanceTracker | None = None`。这是有意的：`replays.py` 和 `provenance.py` 之间的循环导入约束（provenance.py 导入 `ReplayResult` 和 `ProbeCase`，replays.py 不能导入 `ProvenanceTracker`）。`object` 类型注解在语义上是正确的（tracker 可以是任何对象或 None），并避免了循环导入，同时保持了 `tracker.get_edges()` 的鸭子类型行为。

### 适配器标签奇偶性

mem0 和 Letta 适配器路径均通过共享的 `_run_case_with_adapter` 运行器接收完整的溯源集成。适配器的 6 个拦截回放函数记录与独立回放函数相同操作的边（write、compress、extract、retrieve、inject、reason），加上 4 个 V1 透传回放（route、granularity、graph_off、safety_off）记录各自的操作。适配器标签奇偶性得以保持。

### Paper 面向指标

`provenance_completeness` 指标在比较指标 CSV 的最后一行中报告：

- **分数**：`macro_f1` 列包含浮点分数（例如 `0.850`）。
- **分母/分子**：`provenance_completeness` 列包含格式 `"{numerator}/{denominator}"`（例如 `"17/20"`），显示带有非空溯源边的回放数量 / 总回放数量。
- **计算**：在所有结果的回放中，`sum(1 for replay in result.replays if replay.provenance_edges)` / `sum(len(result.replays) for result in results)`。
