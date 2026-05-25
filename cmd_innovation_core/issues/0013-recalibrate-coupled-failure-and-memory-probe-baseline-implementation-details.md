# Issue 0013 实现细节：耦合失败重新校准与 Memory-Probe 基线

## 目的

本文档是 issue 0013《耦合失败重新校准与 Memory-Probe 基线》的全局实现地图。它映射每个函数、数据类、辅助函数和常量到其确切的源码位置、签名、行为、调用者和领域含义。

Issue 0013 完成 V1 管道在 11 标签空间中的最后两项扩展：

```text
Part A — 耦合失败重新校准 (Cycle 18)
  V1 Pipeline
    run_case_v1(top_k=3) -> assign_attribution_v1(top_k=3)
      -> AttributionResult(top_k_labels, close_deltas)
      -> top_k_labels capped at 3, close_deltas unbounded

Part B — Memory-Probe 3x2 网格基线 (Cycle 19)
  ProbeCase -> run_memory_probe_case(case)
    -> 3 write_strategies x 2 retrieval_methods = 6 cells (dense retrieval deferred to V1 per issue 0008)
    -> best cell = max(answer_score)
  -> run_memory_probe_baselines(cases)
    -> aggregate best-cell accuracy by (write, retrieve) pair
    -> MemoryProbeBaselineResult -> comparison_metrics.csv

V0 管道（未更改）
  assign_attribution -> top_k_labels = top2_labels, close_deltas = ()
```

该切片交付 PDR 中的 User Story 42 以及 TDD Cycle 18 和 19：

| 周期 | 标题 | 状态 |
| --- | --- | --- |
| Cycle 18 | Coupled-Failure Recalibration (top_k, close_deltas) | 绿色 |
| Cycle 19 | Memory-Probe 3x2 Grid Baseline | 绿色 |

Issue 0013 是 V1 标签扩展（issues 0011-0012）之上的最后一层归因改进和基线对比。后续 issue 0014（mem0 适配器）和 0015（Letta 适配器）在此基础上构建。

## 源需求

本实现遵循以下本地文档。

| 来源 | 在 Issue 0013 中应用的需求 |
| --- | --- |
| `TASK.md` | V1 耦合失败重新校准：`top_k` 参数支持 3+ 标签归因。Memory-Probe 基线：3 种写入策略 x 2 种检索方法（cosine + BM25; dense 推迟到 V1 per issue 0008）的聚合诊断对比器。非回归：6 标签 V0 烟雾套件在所有默认参数下保持标签不变。 |
| `CLAUDE.md` | V1 活跃标签共 11 个（V0 6 + issue 0011 的 2 个 + issue 0012 的 3 个）。`AttributionResult` 扩展需保留向后兼容默认值。Memory-Probe 是聚合诊断而非案例级对比器，与现有 `ComparatorResult` 模型根本不同。 |
| `cmd_innovation_core/CONTEXT.md` | 耦合失败定义：多个回放的 recovery_gain 差值在 `tie_margin` 内。V1 11 标签空间中，回放 delta 更密集，需要可配置的 `top_k` 和透明的 `close_deltas` 暴露。 |
| `cmd_innovation_core/prd/cmd_minimal_probe_prd.md` | User Story 42：耦合失败重新校准 + Memory-Probe 基线。V1 Scope：11 标签归因对比全部基线。 |
| `cmd_innovation_core/issues/0013-recalibrate-coupled-failure-and-memory-probe-baseline.md` | 8 个验收标准：`top_k` 参数、多标签输出、close_deltas 透明暴露、V0 非回归、Memory-Probe 3x2 网格、`comparison_metrics.csv` 新列、行为级测试。 |
| `cmd_innovation_core/tdd/cmd_tracer_bullets.md` | Cycle 18 RED/GREEN：耦合失败重新校准。Cycle 19 RED/GREEN：Memory-Probe 基线。 |

## 领域边界

Issue 0013 在现有 V1 标签基础设施（issues 0011-0012）之上构建两层独立改进。

```text
Part A: Coupled-Failure Recalibration（归因层修改）
  assign_attribution（V0，未更改）
    -> top_k_labels = top2_labels（仅 2 个）
    -> close_deltas = ()（空）

  assign_attribution_v1（V1，已扩展）
    + top_k: int = 2 参数
    -> top_k_labels: 前 top_k 个（可超过 2）
    -> close_deltas: 所有 tie_margin 内的 (label, delta) 对（无上限）
    -> top2_labels: 始终前 2 个（向后兼容）

Part B: Memory-Probe Baseline（新模块，独立于归因）
  cmd_audit/memory_probe.py（全新 235 行）
    -> run_memory_probe_case -> 6 cells (3 write x 2 retrieve) -> best_cell
    -> run_memory_probe_baselines -> aggregate accuracy -> comparison_metrics.csv

harness.py + writers.py（plumbing 修改）
  run_case_v1(top_k=3) -> assign_attribution_v1(top_k=3)
  write_attribution_table -> 新增 top_k_labels, close_deltas 列
  write_comparison_metrics_table -> 可选 memory_probe_best_accuracy 列
```

Issue 0013 拥有的内容：

- `AttributionResult` 上的 `top_k_labels: tuple[str, ...]` 和 `close_deltas: tuple[tuple[str, float], ...]` 字段（带向后兼容默认值）。
- `assign_attribution_v1` 上的 `top_k: int = 2` 参数。
- `assign_attribution`（V0）填充新字段：`top_k_labels` 镜像 `top2_labels`，`close_deltas` 为空。
- `assign_attribution_v1`（V1）无上限计算 `all_close`，将 `top_k_labels` 截断至 `top_k`。
- `run_case_v1`、`run_cases_v1`、`run_case_full_v1`、`run_cases_full_v1` 上的 `top_k` 参数。
- `write_attribution_table` 中的 `top_k_labels` 和 `close_deltas` CSV 列。
- `write_comparison_metrics_table` 中的可选 `memory_probe_best_accuracy` CSV 列（`keyword-only`，默认 `None`）。
- 新模块：`cmd_audit/memory_probe.py`（235 行），包含 3 个数据类、6 个私有辅助函数、2 个公共入口点。
- `retrieval_baselines.py` 中的 4 个内部辅助函数重命名为公共函数：`tokenize`、`compute_bm25_scores`、`build_tfidf_vectors`、`cosine_similarity`。
- `__init__.py` 中的新导出符号：9 个 Memory-Probe 符号 + 4 个检索辅助符号。

Issue 0013 不拥有的内容（属于其他 issue）：

- 更改 V0 或 V1 回放组合逻辑（issues 0001、0003、0011、0012）。
- 更改基线套件或对比器逻辑（issue 0002）。
- 更改 Post-Repair Context Replay 评分或 ECS 草拟（issue 0005）。
- 更改 ECS Failure Memory 逻辑（issue 0007）。
- 添加新管道标签（issues 0011、0012——0013 在 11 标签之上操作）。
- mem0/Letta 适配器集成（issues 0014、0015）。
- 真实数据探针案例（issue 0016）。

## 当前代码产出物

| 产出物 | 在 Issue 0013 中的角色 |
| --- | --- |
| `cmd_audit/attribution.py` | `AttributionResult` 新增 2 个字段（`top_k_labels`、`close_deltas`）。`assign_attribution` 填充默认值。`assign_attribution_v1` 新增 `top_k` 参数和完整 delta 计算。 |
| `cmd_audit/memory_probe.py` | **全新**。3x2 网格对比器基线：3 个数据类、3 个写入策略、2 个检索辅助函数（cosine + BM25；dense 推迟到 V1 per issue 0008）、网格运行器、聚合函数。 |
| `cmd_audit/harness.py` | 4 个 V1 管道函数新增 `top_k` 参数。`write_comparison_metrics_table` 新增可选 `memory_probe_best_accuracy` 列。 |
| `cmd_audit/retrieval_baselines.py` | 4 个内部辅助函数从下划线前缀改为公共名称，供 `memory_probe.py` 复用。 |
| `cmd_audit/writers.py` | `write_attribution_table` 新增 `top_k_labels` 和 `close_deltas` 列。 |
| `cmd_audit/__init__.py` | 导出 13 个新符号（4 个 Memory-Probe 数据类/函数 + 5 个 Memory-Probe 相关导出 + 4 个检索辅助函数）。 |
| `tests/test_cmd_audit_issue13_coupled_failure_and_memory_probe.py` | **全新**。10 个测试类，42 个测试方法。 |
| `artifacts/comparison_metrics.csv` | 运行时产出物：包含 `memory_probe_best_accuracy` 列的对比指标表。 |

## 模块地图

| 模块 | Issue 0013 角色 |
| --- | --- |
| `cmd_audit/attribution.py` | **已更新。** `AttributionResult` 新增 `top_k_labels`（第 18 行）和 `close_deltas`（第 19 行）字段，均带默认值。`assign_attribution`（V0）显式填充 `top_k_labels=close_capped`、`close_deltas=()`（第 49-50 行）。`assign_attribution_v1`（V1）新增 `top_k: int = 2` 参数（第 60 行），计算无上限的 `all_close`，将 `top_k_labels` 截断至 `top_k`（第 93 行），将 `close_deltas` 设置为所有对（第 103 行）。 |
| `cmd_audit/memory_probe.py` | **全新（235 行）。**拥有完整的 Memory-Probe 基线：3 个数据类（`MemoryProbeCellResult`、`MemoryProbeCaseResult`、`MemoryProbeBaselineResult`），2 个公共常量（`WRITE_STRATEGIES`、`RETRIEVAL_METHODS`），3 个写入策略函数，1 个句子分割辅助函数，2 个检索辅助函数（cosine + BM25；dense 推迟到 V1 per issue 0008），网格运行器 `run_memory_probe_case`（6 cells），聚合函数 `run_memory_probe_baselines`。 |
| `cmd_audit/harness.py` | **已更新。**`run_case_v1`（第 204 行）新增 `top_k: int = 2` 参数，传递给 `assign_attribution_v1`。`run_cases_v1`、`run_case_full_v1`、`run_cases_full_v1` 全部新增 `top_k` 参数。`write_comparison_metrics_table`（第 159-198 行）新增 `memory_probe_best_accuracy: float \| None = None` 关键字参数，条件性添加列。 |
| `cmd_audit/retrieval_baselines.py` | **已更新。**4 个函数从下划线前缀重命名为公共名称：`_tokenize` → `tokenize`（第 115 行）、`_compute_bm25_scores` → `compute_bm25_scores`（第 125 行）、`_build_tfidf_vectors` → `build_tfidf_vectors`（第 207 行）、`_cosine_similarity` → `cosine_similarity`（第 248 行）。所有内部调用点已更新（8 处 `tokenize`、2 处 `compute_bm25_scores`、1 处 `build_tfidf_vectors`、1 处 `cosine_similarity`）。 |
| `cmd_audit/writers.py` | **已更新。**`write_attribution_table`（第 73-144 行）在 `is_ambiguous` 之后新增两个字段：`top_k_labels`（管道连接标签，第 123 行）和 `close_deltas`（`label:delta` 对，精确到 4 位小数，第 125-127 行）。 |
| `cmd_audit/__init__.py` | **已更新。**导出 `MemoryProbeBaselineResult`、`MemoryProbeCaseResult`、`MemoryProbeCellResult`（第 91-97 行）、`run_memory_probe_baselines`、`run_memory_probe_case`（第 98-100 行）、`build_tfidf_vectors`、`compute_bm25_scores`、`cosine_similarity`、`tokenize`（第 103-106 行）。 |
| `tests/test_cmd_audit_issue13_coupled_failure_and_memory_probe.py` | 10 个测试类，42 个测试方法，覆盖 Cycle 18 和 Cycle 19。 |

## 调用图

### Part A：耦合失败归因流水线（issue 0013）

```text
cmd_audit/__init__.py
  -> harness.run_case_v1(ProbeCase, top_k=3)
      -> baselines.run_baseline_suite(ProbeCase)
      -> replays.run_v1_replay_portfolio(ProbeCase)
          -> 10 个回放：oracle_write, oracle_compression, verbatim_event_oracle,
             oracle_retrieval, injection_oracle, evidence_given_reasoning,
             oracle_route, oracle_granularity, oracle_graph, oracle_safety
      -> attribution.assign_attribution_v1(replays, has_ingestion_trace=..., top_k=3)
          -> ranked = sorted(replays, key=recovery_gain, reverse=True)
          -> top = ranked[0]
          -> for result in ranked:
              -> delta = top.recovery_gain - result.recovery_gain
              -> if delta <= tie_margin (0.05):
                  -> label = _v1_label_for_replay(result.replay_name, ...)
                  -> validate_v1_label(label)
                  -> all_close.append((label, delta))
          -> top_k_labels = all_close[:top_k] (capped at 3)
          -> top2_labels = all_close[:2] (always 2)
          -> close_deltas = all_close (unbounded)
          -> AttributionResult(
              predicted_label=all_close[0][0],
              top2_labels=(2 labels),
              is_ambiguous=len(all_close) > 1,
              top_k_labels=(up to top_k labels),
              close_deltas=(all pairs),
            )
      -> AuditResult(...)

  -> harness.run_case_v1(case)  — 默认 top_k=2（向后兼容）
      -> same flow, top_k_labels capped at 2

V0 管道（供参考）：
  -> harness.run_case(ProbeCase)
      -> attribution.assign_attribution(replays)
          -> top_k_labels = close[:2] (same as top2_labels)
          -> close_deltas = () (empty)
```

### Part B：Memory-Probe 基线流水线（issue 0013）

```text
cmd_audit/__init__.py
  -> memory_probe.run_memory_probe_baselines([ProbeCase, ...])
      -> for each case: memory_probe.run_memory_probe_case(case)
          -> for each write_strategy in (fact_extraction, summarization, raw_chunks):
              -> memory_items = write_fn(case)
                  -> _write_fact_extraction:
                      -> for each raw_event:
                          -> _split_sentences(event.text)
                              -> re.split(r"[.;\n]+", text)
                              -> filter: >= 2 words per sentence
                          -> MemoryItem(memory_id=f"fact_{case_id}_{idx}", ...)
                  -> _write_summarization:
                      -> for each raw_event:
                          -> filter lines: >= 3 words
                          -> MemoryItem(memory_id=f"summary_{event_id}", ...)
                  -> _write_raw_chunks:
                      -> for each raw_event:
                          -> MemoryItem(memory_id=f"raw_{event_id}", text=event.text)
              -> for each retrieval_method in (cosine, bm25):  # hybrid_rerank removed per issue 0008
                  -> (_idx, top_text) = retrieve_fn(memory_items, case.query)
                      -> _retrieve_top1_cosine:
                          -> build_tfidf_vectors(items, query)
                          -> cosine_similarity(query_vec, doc_vec)
                          -> argmax -> (index, text)
                      -> _retrieve_top1_bm25:
                          -> tokenize(query), tokenize(doc texts)
                          -> compute_bm25_scores(query_tokens, doc_tokens)
                          -> argmax -> (index, text)
                      -> _retrieve_top1_hybrid:
                          -> bm25_scores + tfidf_cosine_scores
                          -> minmax normalize each
                          -> 0.4 * bm25_norm + 0.6 * vector_norm
                          -> argmax -> (index, text)
                  -> answer_score(top_text, case.gold_answer)
                  -> evidence_recall_from_text(case.gold_evidence, top_text)
                  -> MemoryProbeCellResult(ws, rm, ans_score, ev_score, top_text)
              -> best_cell = max(cell_results, key=answer_score)
          -> MemoryProbeCaseResult(case_id, cell_results, best_cell)
      -> for each (ws, rm) pair:
          -> accuracy = count(answer_score==1.0) / total_cases
      -> best_pair = argmax accuracy
      -> MemoryProbeBaselineResult(
          case_results, best_cell_accuracy, best_write_strategy, best_retrieval_method
        )

  -> harness.write_comparison_metrics_table(results, path, memory_probe_best_accuracy=0.750)
      -> fieldnames.append("memory_probe_best_accuracy")
      -> each row gets same memory_probe_best_accuracy value
```

### 行为测试路径

```text
tests/test_cmd_audit_issue13_coupled_failure_and_memory_probe.py
  -> AttributionResult 模式测试                                    (AttributionResultSchemaTest)
  -> top_k=3 归因：3/2/1 close deltas, 默认 top_k=2               (TopKAttributionTest)
  -> 边缘案例：4+ deltas capped, close_deltas pairs, 阈值外排除   (CoupledFailureEdgeCaseTest)
  -> V0 向后兼容：top_k_labels=top2, V0 cases 通过 V1              (V0BackwardCompatTest)
  -> Memory-Probe 写入策略：每种产生正确记忆项                     (MemoryProbeWriteStrategiesTest)
  -> Memory-Probe 网格：6 cells (3x2), 有效分数, best cell          (MemoryProbeGridTest)
  -> Memory-Probe 基线聚合：accuracy, best策略, 案例数              (MemoryProbeBaselineTest)
  -> Comparison Metrics CSV 列                                     (ComparisonMetricsWithMemoryProbeTest)
  -> Attribution Table 新列                                        (AttributionTableNewColumnsTest)
  -> 检索辅助函数公开可调用                                        (RetrievedHelpersPublicTest)
```

## 数据流

### 输入夹具

```text
data/probe_cases/v0_issue3_cases.json        # V0 6 案例烟雾套件（非回归）
data/probe_cases/v1_granularity_error_case.json  # V1 11 标签案例（网格测试和归因测试）
```

### 中间类型

**AttributionResult** 新增字段（来自 `cmd_audit/attribution.py:11-19`）：

| 字段 | 类型 | 默认值 | 含义 |
| --- | --- | --- | --- |
| `top_k_labels` | `tuple[str, ...]` | `()` | 前 `top_k` 个最接近的标签，按 recovery_gain 降序排列。在 V0 中镜像 `top2_labels`。在 V1 中可扩展到 top-3+。 |
| `close_deltas` | `tuple[tuple[str, float], ...]` | `()` | 所有在 `tie_margin` 内的 `(label, delta)` 对，其中 `delta = top.recovery_gain - result.recovery_gain`。无上限——完全透明。在 V0 中为空元组。 |

**V0 与 V1 的填充对比：**

| 字段 | V0 (`assign_attribution`) | V1 (`assign_attribution_v1`) |
| --- | --- | --- |
| `top_k_labels` | `("write_error", "compression_error")`（前 2 个） | `("retrieval_error", "compression_error", "write_error")`（前 `top_k` 个） |
| `close_deltas` | `()`（空） | `(("retrieval_error", 0.0), ("compression_error", 0.02), ("write_error", 0.03), ("route_error", 0.04))`（全部在 margin 内） |
| `top2_labels` | 前 2 个 | 前 2 个（不变——向后兼容） |
| `is_ambiguous` | `len(close) > 1` | `len(all_close) > 1`（阈值相同） |

**top_k 参数与 close_deltas 的语义：**

```text
assume: tie_margin=0.05, top_k=3

ranked replays:
  1. oracle_retrieval:     recovery_gain=1.00  delta=0.00  -> all_close[0]
  2. oracle_compression:   recovery_gain=0.98  delta=0.02  -> all_close[1]
  3. oracle_write:         recovery_gain=0.97  delta=0.03  -> all_close[2]
  4. oracle_route:         recovery_gain=0.96  delta=0.04  -> all_close[3]
  5. oracle_granularity:   recovery_gain=0.50  delta=0.50  -> excluded (> 0.05)

result:
  top_k_labels = ("retrieval_error", "compression_error", "write_error")  # 3 entries, capped
  top2_labels  = ("retrieval_error", "compression_error")                  # 2 entries, always
  close_deltas = (("retrieval_error", 0.0), ("compression_error", 0.02),
                  ("write_error", 0.03), ("route_error", 0.04))           # 4 entries, unbounded
  is_ambiguous = True                                                      # 4 > 1
```

**MemoryProbeCellResult**（来自 `cmd_audit/memory_probe.py:28-35`）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `write_strategy` | `str` | `"fact_extraction"`、`"summarization"` 或 `"raw_chunks"` |
| `retrieval_method` | `str` | `"cosine"` 或 `"bm25"`（`hybrid_rerank` 已在 issue 0008 中移除；dense retrieval 推迟到 V1） |
| `answer_score` | `float` | top-1 检索文本与 gold_answer 的精确匹配分数 |
| `evidence_score` | `float` | 检索文本中 gold_evidence 短语的证据召回分数 |
| `top_item_text` | `str` | 排名最高的记忆项的文本内容 |

**MemoryProbeCaseResult**（来自 `cmd_audit/memory_probe.py:38-43`）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `case_id` | `str` | 源探针案例标识符 |
| `cell_results` | `tuple[MemoryProbeCellResult, ...]` | 全部 9 个（写入 x 检索）单元结果 |
| `best_cell` | `MemoryProbeCellResult` | 按 `answer_score` 最高的单元 |

**MemoryProbeBaselineResult**（来自 `cmd_audit/memory_probe.py:46-52`）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `case_results` | `tuple[MemoryProbeCaseResult, ...]` | 每个输入案例一个结果 |
| `best_cell_accuracy` | `float` | 最佳（写入，检索）对在整个数据集上的聚合准确率 |
| `best_write_strategy` | `str` | 实现最高聚合准确率的写入策略 |
| `best_retrieval_method` | `str` | 实现最高聚合准确率的检索方法 |

**WRITE_STRATEGIES** 与 **RETRIEVAL_METHODS** 常量（来自 `cmd_audit/memory_probe.py:21-22`）：

| 常量 | 值 | 含义 |
| --- | --- | --- |
| `WRITE_STRATEGIES` | `("fact_extraction", "summarization", "raw_chunks")` | 3 种写入策略的规范顺序 |
| `RETRIEVAL_METHODS` | `("cosine", "bm25")` | 2 种检索方法的规范顺序（`hybrid_rerank` 已在 issue 0008 中移除） |

**4 个公开重命名的检索辅助函数**（来自 `cmd_audit/retrieval_baselines.py`）：

| 旧名称 | 新名称 | 位置 | 签名 |
| --- | --- | --- | --- |
| `_tokenize` | `tokenize` | 第 115 行 | `(text: str) -> list[str]` |
| `_compute_bm25_scores` | `compute_bm25_scores` | 第 125 行 | `(query_tokens: list[str], doc_tokens_list: list[list[str]], k1: float = 1.2, b: float = 0.75) -> list[float]` |
| `_build_tfidf_vectors` | `build_tfidf_vectors` | 第 207 行 | `(memory_items: list, query: str) -> tuple[dict[str, float], list[dict[str, float]]]` |
| `_cosine_similarity` | `cosine_similarity` | 第 248 行 | `(vec_a: dict[str, float], vec_b: dict[str, float]) -> float` |

### 输出产出物

V1 管道运行现在生成带有扩展列的归因和对比表：

```text
artifacts/sandbox/                          # V1 流水线运行产出物：
  attribution_table.csv                     # 11 标签归因 + top_k_labels + close_deltas 列
  comparison_metrics.csv                    # CMD-V1 vs 基线 + memory_probe_best_accuracy 列
  attribution_confusion_matrix.csv          # 11x11 混淆矩阵
  post_repair_table.csv                     # 修复后回放结果
  repair_success_table.csv                  # 修复成功对比
  repair_label_summary.csv                  # 逐标签修复汇总
  repair_claim_ledger.txt                   # 修复声明账本
```

## 函数级合约

### `cmd_audit/attribution.py`

文件：`cmd_audit/attribution.py`（121 行）。Issue 0013 变更：`AttributionResult` 新增 2 个字段，`assign_attribution` 填充默认值，`assign_attribution_v1` 新增 `top_k` 参数和完整 delta 计算。

---

#### 数据类：`AttributionResult`（已更新）

位置：`cmd_audit/attribution.py:11-19`

```python
@dataclass(frozen=True)
class AttributionResult:
    predicted_label: str
    top_replay: str
    recovery_gain: float
    top2_labels: tuple[str, ...]
    is_ambiguous: bool
    top_k_labels: tuple[str, ...] = ()           # ← 新增
    close_deltas: tuple[tuple[str, float], ...] = ()  # ← 新增
```

新增字段：

| 字段 | 默认值 | 目的 |
| --- | --- | --- |
| `top_k_labels` | `()` | 前 `top_k` 个最接近的标签（默认最多 2 个，可配置至 3+）。在 V0 中与 `top2_labels` 相同。在 V1 中，当 `top_k > 2` 时包含额外的紧密标签。 |
| `close_deltas` | `()` | 所有在 `tie_margin` 内的 `(label, delta)` 对（无上限）。在 V0 中为空——空元组意味着"V0 不暴露 delta 分布"。在 V1 中提供完全透明：下游可以检查 4+ 个紧密标签而不受 `top_k` 截断。 |

向后兼容性：

- 两个字段都有默认值，因此 `AttributionResult(predicted_label=..., top_replay=..., ...)`（不带新字段）仍然可以无错误地构造。所有现有的仅使用位置参数的构造点（`attribution.py` 中的 2 处）已更新为显式传递新字段，但默认值确保未来的调用者不会出现意外破坏。
- `assign_attribution`（V0）显式设置 `top_k_labels=tuple(close_capped)`（与 `top2_labels` 相同）和 `close_deltas=()`（空）。
- `assign_attribution_v1`（V1）显式设置 `top_k_labels=tuple(label for label, _ in all_close[:top_k])` 和 `close_deltas=tuple(all_close)`。

调用者：
- `assign_attribution`（`attribution.py:43-51`）
- `assign_attribution_v1`（`attribution.py:96-104`）
- `write_attribution_table`（`writers.py:123, 125-127`）
- `AttributionResultSchemaTest`（直接测试）

---

#### 函数：`assign_attribution`（已更新——填充新字段）

位置：`cmd_audit/attribution.py:22-51`

```python
def assign_attribution(
    replay_results: tuple[ReplayResult, ...],
    *,
    positive_gain_threshold: float = 0.0,
    tie_margin: float = 0.05,
) -> AttributionResult:
```

行为变更（仅字段填充）：

- 第 42 行：`close_capped = close[:2]`（V0 始终截断至前 2 个）。
- 第 49 行：`top_k_labels=tuple(close_capped)`——在 V0 中镜像 `top2_labels`（始终最多 2 个标签）。
- 第 50 行：`close_deltas=()`——V0 不暴露 delta 分布（发出空元组）。
- 归因逻辑、标签解析或 `is_ambiguous` 阈值**无变化**。

向后兼容性验证：

```text
V0 案例 (v0-write-001):
  top = oracle_write (recovery_gain=1.0)
  close = ["write_error"] (唯一正增益)
  close_capped = ["write_error"]
  
  result:
    predicted_label = "write_error"
    top2_labels = ("write_error",)
    is_ambiguous = False
    top_k_labels = ("write_error",)    # 与 top2_labels 相同
    close_deltas = ()                  # 空
```

调用者：
- `run_case`（`harness.py:100`）
- `V0BackwardCompatTest`（直接测试）

---

#### 函数：`assign_attribution_v1`（已更新——新增 `top_k` 参数）

位置：`cmd_audit/attribution.py:54-104`

```python
def assign_attribution_v1(
    replay_results: tuple[ReplayResult, ...],
    *,
    has_ingestion_trace: bool = True,
    positive_gain_threshold: float = 0.0,
    tie_margin: float = 0.05,
    top_k: int = 2,                    # ← 新增
) -> AttributionResult:
```

目的：

- 执行带有可配置 `top_k` 的 V1 操作级归因，支持 11 个标签。
- 核心变更：无上限计算 `all_close`，然后从同一源列表派生出 `top_k_labels`（截断至 `top_k`）、`top2_labels`（截断至 2）和 `close_deltas`（无上限）。
- 默认 `top_k=2` 保持与 V0 行为完全向后兼容。

新增逻辑（第 84-103 行）：

```python
all_close: list[tuple[str, float]] = []
for result in ranked:
    delta = top.recovery_gain - result.recovery_gain
    if delta <= tie_margin:
        label = validate_v1_label(
            _v1_label_for_replay(result.replay_name, has_ingestion_trace=has_ingestion_trace)
        )
        all_close.append((label, delta))

top_k_labels = tuple(label for label, _ in all_close[:top_k])
top2_labels = tuple(label for label, _ in all_close[:2])

return AttributionResult(
    ...
    top2_labels=top2_labels,
    is_ambiguous=len(all_close) > 1,
    top_k_labels=top_k_labels,
    close_deltas=tuple(all_close),
)
```

关键设计决策：

1. **`all_close` 在截断之前计算**：这确保 `close_deltas` 反映完整分布，即使 `top_k` 很小。
2. **`top2_labels` 始终从 `all_close[:2]` 派生**：保持与 V0 的语义向后兼容性。
3. **`is_ambiguous` 使用 `len(all_close)`，而非 `len(top_k_labels)`**：如果 4 个回放接近但 `top_k=3`，案例仍然被标记为模糊（`is_ambiguous=True`），下游可以检查 `close_deltas` 了解完整情况。
4. **默认 `top_k=2, tie_margin=0.05` 保留确切的现有行为**：所有 387 个测试通过这些默认值通过。

V0 与 V1 对比：

| 方面 | `assign_attribution` | `assign_attribution_v1`（默认） | `assign_attribution_v1`（top_k=3） |
| --- | --- | --- | --- |
| close 计算 | `close = [labels]`，截断 `[:2]` | `all_close = [(label, delta)]`，无上限 | `all_close = [(label, delta)]`，无上限 |
| `top_k_labels` | `close[:2]` | `all_close[:2]` | `all_close[:3]` |
| `top2_labels` | `close[:2]` | `all_close[:2]` | `all_close[:2]` |
| `close_deltas` | `()` | `tuple(all_close)` | `tuple(all_close)` |
| 标签校验 | `validate_v0_label` | `validate_v1_label` | `validate_v1_label` |

调用者：
- `run_case_v1`（`harness.py:208-209`）
- `TopKAttributionTest`（直接测试）
- `CoupledFailureEdgeCaseTest`（直接测试）
- `V0BackwardCompatTest`（通过 `run_case_v1` 间接测试）

---

#### 私有函数：`_label_for_replay`（未更改）

位置：`cmd_audit/attribution.py:107-111`

未更改。仍然使用 `REPLAY_TO_LABEL`（仅 V0，6 个条目）。

---

#### 私有函数：`_v1_label_for_replay`（未更改）

位置：`cmd_audit/attribution.py:114-120`

未更改。仍然通过 `V1_REPLAY_TO_LABEL` 处理 `oracle_write` → `ingestion_error` 覆盖和路由。

### `cmd_audit/memory_probe.py`

文件：`cmd_audit/memory_probe.py`（235 行）。全新模块。包含 2 个公共常量、3 个数据类、6 个私有函数、1 个句子分割辅助函数、2 个公共入口点。

---

#### 常量：`WRITE_STRATEGIES`

位置：`cmd_audit/memory_probe.py:21`

```python
WRITE_STRATEGIES = ("fact_extraction", "summarization", "raw_chunks")
```

目的：

- 定义 3x2 网格的 3 种写入策略变体的规范顺序。
- 在 `run_memory_probe_baselines` 中用于迭代所有（写入，检索）对。
- 在测试中用于验证策略名称和完整性。

调用者：
- `run_memory_probe_baselines`（`memory_probe.py:217-218`）
- `MemoryProbeGridTest.test_each_cell_has_valid_strategy_and_method`
- `MemoryProbeBaselineTest.test_best_strategy_and_method_are_valid`

---

#### 常量：`RETRIEVAL_METHODS`

位置：`cmd_audit/memory_probe.py:22`

```python
RETRIEVAL_METHODS = ("cosine", "bm25")  # hybrid_rerank removed per issue 0008
```

目的：

- 定义 3x2 网格的 2 种检索方法变体的规范顺序（cosine + BM25；dense 推迟到 V1 per issue 0008）。
- 在 `run_memory_probe_baselines` 中用于迭代所有（写入，检索）对。

调用者：
- `run_memory_probe_baselines`（`memory_probe.py:217-218`）
- `MemoryProbeGridTest.test_each_cell_has_valid_strategy_and_method`
- `MemoryProbeBaselineTest.test_best_strategy_and_method_are_valid`

---

#### 数据类：`MemoryProbeCellResult`

位置：`cmd_audit/memory_probe.py:28-35`

```python
@dataclass(frozen=True)
class MemoryProbeCellResult:
    """Result of a single (write_strategy, retrieval_method) cell."""
    write_strategy: str
    retrieval_method: str
    answer_score: float
    evidence_score: float
    top_item_text: str
```

目的：

- 表示 3x2 网格中一个单元的结果。
- `answer_score` 和 `evidence_score` 都与 CMD-Audit 的 `AuditResult` 共享相同的评分函数（`answer_score` 来自 `scoring.py`，`evidence_recall_from_text` 来自 `scoring.py`），确保跨系统的公平比较。
- `top_item_text` 保留检索到的最佳文本以供调试/检查。

调用者：
- `run_memory_probe_case`（`memory_probe.py:191-197`）——唯一构造点
- `MemoryProbeGridTest`（字段访问 + 验证）

---

#### 数据类：`MemoryProbeCaseResult`

位置：`cmd_audit/memory_probe.py:38-43`

```python
@dataclass(frozen=True)
class MemoryProbeCaseResult:
    """All 6 cell results for a single probe case plus the best cell."""
    case_id: str
    cell_results: tuple[MemoryProbeCellResult, ...]
    best_cell: MemoryProbeCellResult
```

目的：

- 聚合单个探针案例的所有 9 个单元结果。
- `best_cell` 按 `answer_score` 选择（`max(cell_results, key=lambda c: c.answer_score)`）。
- 注意：`best_cell` 是按 `answer_score` 选择的，而非 `evidence_score`，因为 Memory-Probe 的主要目标是在没有 CMD 反事实干预的情况下最大化诊断准确率。

调用者：
- `run_memory_probe_case`（`memory_probe.py:200-204`）——唯一构造点
- `run_memory_probe_baselines`（`memory_probe.py:221-225`）——遍历 `cell_results`
- `MemoryProbeGridTest`（字段验证）

---

#### 数据类：`MemoryProbeBaselineResult`

位置：`cmd_audit/memory_probe.py:46-52`

```python
@dataclass(frozen=True)
class MemoryProbeBaselineResult:
    """Aggregate memory-probe results across all probe cases."""
    case_results: tuple[MemoryProbeCaseResult, ...]
    best_cell_accuracy: float
    best_write_strategy: str
    best_retrieval_method: str
```

目的：

- 所有探针案例的聚合 Memory-Probe 结果。
- `best_cell_accuracy` 是数据集级别的最佳（写入，检索）对准确率。这是插入 `comparison_metrics.csv` 的值。
- 与 CMD 的案例级归因准确率根本不同：CMD 诊断单个案例；Memory-Probe 找到数据集上最强的（写入，检索）组合。这种区别意味着比较必须理解为"CMD 逐案例准确率 vs Memory-Probe 聚合上限"。

调用者：
- `run_memory_probe_baselines`（`memory_probe.py:229-234`）——唯一构造点
- `MemoryProbeBaselineTest`（字段验证）
- `write_comparison_metrics_table`（通过 `memory_probe_best_accuracy` 参数）——仅使用 `best_cell_accuracy`

---

#### 私有函数：`_write_fact_extraction(case: ProbeCase) -> tuple[MemoryItem, ...]`

位置：`cmd_audit/memory_probe.py:58-71`

```python
def _write_fact_extraction(case: ProbeCase) -> tuple[MemoryItem, ...]:
    """Mem0-style: split raw event text into atomic fact claims."""
    items: list[MemoryItem] = []
    idx = 0
    for event in case.raw_events:
        for sentence in _split_sentences(event.text):
            items.append(MemoryItem(
                memory_id=f"fact_{case.case_id}_{idx}",
                text=sentence,
                source_event_ids=(event.event_id,),
                store="episodic",
            ))
            idx += 1
    return tuple(items)
```

目的：

- 实现 Mem0 风格的写入策略：将每个原始事件文本分割成原子事实声明（句子），并将每个句子存储为单独的 `MemoryItem`。
- Memory ID 格式：`fact_{case_id}_{idx}`（全局唯一，递增索引）。

行为：
1. 对于每个 `raw_event`：使用正则表达式 `[.;\n]+` 分割 `event.text` 为句子。
2. 过滤句子：仅保留至少 2 个词的句子（`_split_sentences` 处理）。
3. 为每个幸存句子创建一个 `MemoryItem`，`source_event_ids` 指向父事件。

领域注释：

- 这是 3 种写入策略中**最细粒度**的——它最大化召回率但向检索器引入更多噪声。在使用 `gold_answer` 进行精确匹配评分时表现良好。
- 在 `store="episodic"` 下总是创建项目，因为本基线中的存储路由是概念性的。

调用者：
- `run_memory_probe_case`（`memory_probe.py:174`——通过 `write_strategies` 字典）
- `MemoryProbeWriteStrategiesTest.test_fact_extraction_produces_items`

---

#### 私有函数：`_write_summarization(case: ProbeCase) -> tuple[MemoryItem, ...]`

位置：`cmd_audit/memory_probe.py:74-87`

```python
def _write_summarization(case: ProbeCase) -> tuple[MemoryItem, ...]:
    """MemGPT-style: one memory item per raw event, noise-filtered."""
    items: list[MemoryItem] = []
    for event in case.raw_events:
        lines = [line.strip() for line in event.text.split("\n")
                 if len(line.split()) >= 3]
        if lines:
            items.append(MemoryItem(
                memory_id=f"summary_{event.event_id}",
                text=" ".join(lines),
                source_event_ids=(event.event_id,),
                store="episodic",
            ))
    return tuple(items)
```

目的：

- 实现 MemGPT 风格的写入策略：每个原始事件一个记忆项，过滤掉短行/噪声行。
- Memory ID 格式：`summary_{event_id}`（每个事件一个项目）。

行为：
1. 对于每个 `raw_event`：按换行符分割文本为行。
2. 过滤行：仅保留至少 3 个词的行（噪声过滤）。
3. 将幸存的行用空格连接起来。
4. 如果连接后的文本非空：创建一个 `MemoryItem`。

领域注释：

- 这是**中等粒度**：噪声过滤可以防止短行污染检索，但如果关键证据位于短片段中可能丢失证据。
- 产生的项目比 `_write_fact_extraction` 少，减少了检索的计算成本。
- 如果没有行通过过滤器（全部 < 3 个词），该事件不会产生 `MemoryItem`。

调用者：
- `run_memory_probe_case`（`memory_probe.py:175`——通过 `write_strategies` 字典）
- `MemoryProbeWriteStrategiesTest.test_summarization_produces_items`

---

#### 私有函数：`_write_raw_chunks(case: ProbeCase) -> tuple[MemoryItem, ...]`

位置：`cmd_audit/memory_probe.py:90-100`

```python
def _write_raw_chunks(case: ProbeCase) -> tuple[MemoryItem, ...]:
    """Raw chunks: one memory item per raw event, text unchanged."""
    return tuple(
        MemoryItem(
            memory_id=f"raw_{event.event_id}",
            text=event.text,
            source_event_ids=(event.event_id,),
            store="episodic",
        )
        for event in case.raw_events
    )
```

目的：

- 实现原始块写入策略：每个原始事件一个记忆项，文本完全不变。
- Memory ID 格式：`raw_{event_id}`。
- 最简单的策略——无过滤、无分割、无摘要。充当 3 种策略对比的控制条件。

行为：
1. 对于每个 `raw_event`：创建一个文本与事件文本相同的 `MemoryItem`。
2. 项目数量始终等于 `len(case.raw_events)`。

领域注释：

- 这是**最粗粒度**——如果事件文本包含多个无关句子，可能向检索器引入噪声，但如果证据是单个连贯段落则保留完整的上下文。
- 在比较中：如果 `raw_chunks` 优于 `fact_extraction`，则表明原子事实分割将证据分割得太细，无法进行有意义的检索。

调用者：
- `run_memory_probe_case`（`memory_probe.py:176`——通过 `write_strategies` 字典）
- `MemoryProbeWriteStrategiesTest.test_raw_chunks_produces_one_per_event`

---

#### 私有函数：`_split_sentences(text: str) -> list[str]`

位置：`cmd_audit/memory_probe.py:103-105`

```python
def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"[.;\n]+", text)
    return [p.strip() for p in parts if len(p.split()) >= 2]
```

目的：

- 将文本分割成类似句子的片段，用于 `_write_fact_extraction`。
- 使用正则表达式 `[.;\n]+` 按句点、分号或换行符分割。
- 过滤掉短于 2 个词的片段（噪声过滤）。

调用者：
- `_write_fact_extraction`（`memory_probe.py:63`）

---

#### 私有函数：`_retrieve_top1_cosine(memory_items, query) -> tuple[int, str]`

位置：`cmd_audit/memory_probe.py:111-121`

```python
def _retrieve_top1_cosine(
    memory_items: tuple[MemoryItem, ...], query: str,
) -> tuple[int, str]:
    """Return (index, text) of top-1 item by TF-IDF cosine similarity."""
    if not memory_items:
        return -1, ""
    items_list = list(memory_items)
    query_vec, doc_vecs = build_tfidf_vectors(items_list, query)
    scores = [cosine_similarity(query_vec, dv) for dv in doc_vecs]
    best_idx = max(range(len(scores)), key=lambda i: scores[i])
    return best_idx, items_list[best_idx].text
```

目的：

- 通过 TF-IDF 余弦相似度检索排名最高的记忆项。
- 重用来自 `retrieval_baselines.py` 的公共辅助函数 `build_tfidf_vectors` 和 `cosine_similarity`。
- 返回 `(index, text)` 以便调用者可以计算答案和证据分数。

行为：
1. 空记忆项防护：如果 `memory_items` 为空则返回 `(-1, "")`。
2. 将记忆项元组转换为列表（`build_tfidf_vectors` 需要一个列表）。
3. 为查询和文档构建 TF-IDF 向量。
4. 计算所有文档的余弦相似度。
5. 返回 `argmax` 索引和对应文本。

调用者：
- `run_memory_probe_case`（`memory_probe.py:179`——通过 `retrieval_methods` 字典）

---

#### 私有函数：`_retrieve_top1_bm25(memory_items, query) -> tuple[int, str]`

位置：`cmd_audit/memory_probe.py:124-136`

```python
def _retrieve_top1_bm25(
    memory_items: tuple[MemoryItem, ...], query: str,
) -> tuple[int, str]:
    """Return (index, text) of top-1 item by BM25."""
    if not memory_items:
        return -1, ""
    query_tokens = tokenize(query)
    if not query_tokens:
        return 0, memory_items[0].text
    doc_tokens_list = [tokenize(item.text) for item in memory_items]
    scores = compute_bm25_scores(query_tokens, doc_tokens_list)
    best_idx = max(range(len(scores)), key=lambda i: scores[i])
    return best_idx, memory_items[best_idx].text
```

目的：

- 通过 BM25 关键词匹配检索排名最高的记忆项。
- 重用来自 `retrieval_baselines.py` 的公共辅助函数 `tokenize` 和 `compute_bm25_scores`。
- BM25 擅长精确关键词匹配但在释义或语义相似性方面较弱——与余弦和混合方法形成对比。

行为：
1. 空记忆项防护：如果 `memory_items` 为空则返回 `(-1, "")`。
2. 分词查询。如果查询分词后为空，回退到第一个记忆项。
3. 分词所有记忆项文本。
4. 计算 BM25 分数（默认参数 `k1=1.2, b=0.75`）。
5. 返回 `argmax` 索引和文本。

调用者：
- `run_memory_probe_case`（`memory_probe.py:180`——通过 `retrieval_methods` 字典）

---

#### 私有函数：`_retrieve_top1_hybrid(memory_items, query) -> tuple[int, str]`

位置：`cmd_audit/memory_probe.py:139-165`

```python
def _retrieve_top1_hybrid(
    memory_items: tuple[MemoryItem, ...], query: str,
) -> tuple[int, str]:
    """Return (index, text) of top-1 item by hybrid BM25+TF-IDF rerank."""
    if not memory_items:
        return -1, ""
    query_tokens = tokenize(query)
    if not query_tokens:
        return 0, memory_items[0].text

    items_list = list(memory_items)
    doc_tokens_list = [tokenize(item.text) for item in items_list]
    bm25_scores = compute_bm25_scores(query_tokens, doc_tokens_list)
    query_vec, doc_vecs = build_tfidf_vectors(items_list, query)
    vector_scores = [cosine_similarity(query_vec, dv) for dv in doc_vecs]

    def _minmax(values):
        vmin, vmax = min(values), max(values)
        if vmax == vmin:
            return [0.5] * len(values)
        return [(v - vmin) / (vmax - vmin) for v in values]

    norm_bm25 = _minmax(bm25_scores)
    norm_vector = _minmax(vector_scores)
    hybrid_scores = [0.4 * nb + 0.6 * nv for nb, nv in zip(norm_bm25, norm_vector)]
    best_idx = max(range(len(hybrid_scores)), key=lambda i: hybrid_scores[i])
    return best_idx, items_list[best_idx].text
```

目的：

- **已移除（issue 0008）**：通过混合 BM25+TF-IDF 重排序检索排名最高的记忆项——BM25（精确关键词匹配）和 TF-IDF 余弦（语义相似性）的加权组合。两个模型均为稀疏词袋模型，无法实现真正的语义恢复；强（dense/hybrid）检索推迟到 V1 adapter 层。

行为：
1. 空记忆项/查询防护：与 `_retrieve_top1_bm25` 相同。
2. 独立计算 BM25 分数和 TF-IDF 余弦分数。
3. 使用最小-最大归一化将两组分数归一化到 `[0, 1]` 范围。如果所有分数相等（`vmax == vmin`），分配中立分数 `0.5`。
4. 组合：`0.4 * BM25_normalized + 0.6 * Vector_normalized`（对语义信号略微加权）。
5. 返回最高混合分数的 `argmax` 索引和文本。

权重理由（0.4/0.6）：

- 在 Memory-Probe 文献（2603.02473）中，混合重排序始终优于纯 BM25 或纯余弦。0.6 对向量语义的权重反映了该领域的发现，即语义匹配在记忆检索中比精确术语匹配更有价值。
- 权重在此基线中是固定的（不可配置），因为目标是作为 CMD 比较的稳定参考点。

调用者：
- `run_memory_probe_case`（`memory_probe.py:181`——通过 `retrieval_methods` 字典）

---

#### 函数：`run_memory_probe_case(case: ProbeCase) -> MemoryProbeCaseResult`

位置：`cmd_audit/memory_probe.py:171-204`

```python
def run_memory_probe_case(case: ProbeCase) -> MemoryProbeCaseResult:
    """Run all 9 (write_strategy x retrieval_method) cells for one case."""
```

目的：

- 3x2 网格运行器：对于每个写入策略，执行 2 种检索方法（cosine + BM25），记录答案和证据分数。
- 这是 Memory-Probe 的核心工作单元——与 CMD 的 `run_case` 并行，但使用纯写入/检索组合而非反事实回放。

行为：
1. 通过 `write_strategies` 字典将写入策略名称映射到其函数。
2. 通过 `retrieval_methods` 字典将检索方法名称映射到其函数。
3. 对于每个写入策略：调用 `ws_fn(case)` 产生 `tuple[MemoryItem, ...]`。
4. 对于每个检索方法：调用 `rm_fn(memory_items, case.query)` 获得 `(index, top_text)`。
5. 使用 `answer_score(top_text, case.gold_answer)`（精确匹配）和 `evidence_recall_from_text(case.gold_evidence, top_text)` 评分检索到的文本。
6. 创建一个 `MemoryProbeCellResult` 包含所有 5 个字段。
7. 在所有 9 个单元中选择 `best_cell = max(cell_results, key=lambda c: c.answer_score)`。
8. 返回包含全部 9 个单元和最佳单元的 `MemoryProbeCaseResult`。

重要提示：

- `best_cell` 是通过 `answer_score`（精确匹配）而非 `evidence_score` 选择的，因为 Memory-Probe 的诊断目标是对齐到答案准确率，CMD 也是如此（`recovery_gain` 由恢复的答案分数驱动）。
- 编写和检索字典使用与 `WRITE_STRATEGIES` / `RETRIEVAL_METHODS` 常量相同的名称，但它们是独立的映射以确保正确的函数绑定。

调用者：
- `run_memory_probe_baselines`（`memory_probe.py:214`）
- `MemoryProbeGridTest`（直接测试）

---

#### 函数：`run_memory_probe_baselines(cases: list[ProbeCase]) -> MemoryProbeBaselineResult`

位置：`cmd_audit/memory_probe.py:210-234`

```python
def run_memory_probe_baselines(
    cases: list[ProbeCase],
) -> MemoryProbeBaselineResult:
    """Run memory-probe 3x2 grid across all cases, compute aggregate best-cell accuracy."""
```

目的：

- 聚合数据集级别的 Memory-Probe 评估。在所有探针案例上运行 3x2 网格，计算每个（写入，检索）对在数据集上的聚合准确率，并找到最佳对（共 6 对：3 write × 2 retrieve）。
- 关键输出是 `best_cell_accuracy`——最强（写入，检索）组合在整个数据集上的准确率。这是插入 `comparison_metrics.csv` 的值。

行为：
1. 为每个输入案例调用 `run_memory_probe_case`（产生 `tuple[MemoryProbeCaseResult, ...]`）。
2. 对于每个（写入策略，检索方法）对：计算 `answer_score == 1.0` 的案例比例（精确匹配准确率）。
3. 在 9 对中找到最高准确率。
4. 返回 `MemoryProbeBaselineResult`，包含所有案例结果、最佳准确率以及最佳写入/检索名称。

准确率计算细节：

```python
correct = sum(
    1 for cr in case_results
    for cell in cr.cell_results
    if cell.write_strategy == ws and cell.retrieval_method == rm
    and cell.answer_score == 1.0
)
cell_accuracy[(ws, rm)] = correct / total if total else 0.0
```

- 准确率 = 所有案例中 `answer_score == 1.0` 的比例（精确匹配金标准答案）。
- 边界条件：如果 `total == 0`（空案例列表），准确率为 `0.0`。

与 CMD 对比指标的关系：

- CMD 准确率：逐案例（每个案例一个预测标签）。
- Memory-Probe 准确率：聚合（跨数据集的最佳（写入，检索）对）。
- 在 `comparison_metrics.csv` 中，`memory_probe_best_accuracy` 是一个单一值，插入到每个系统行中。这不是错误——该列的目的是展示 Memory-Probe 作为参考线的上限，而不是逐系统比较。

调用者：
- `ComparisonMetricsWithMemoryProbeTest`（直接测试）
- `MemoryProbeBaselineTest`（直接测试）
- 最终用户通过 `write_comparison_metrics_table` 管道

### `cmd_audit/harness.py`

文件：`cmd_audit/harness.py`（246 行）。Issue 0013 变更：4 个 V1 管道函数新增 `top_k` 参数，`write_comparison_metrics_table` 新增可选 Memory-Probe 列。

---

#### 函数：`run_case_v1`（已更新——新增 `top_k`）

位置：`cmd_audit/harness.py:204-221`

```python
def run_case_v1(case: ProbeCase, *, top_k: int = 2) -> AuditResult:
    """Run the V1 pipeline: 10-replay portfolio + V1 attribution."""
    baseline_suite = run_baseline_suite(case)
    replays = run_v1_replay_portfolio(case)
    attribution = assign_attribution_v1(
        replays, has_ingestion_trace=case.has_ingestion_trace, top_k=top_k,
    )
    ...
```

行为变更：

- 新增 `top_k: int = 2` 关键字参数（第 204 行）。
- 传递给 `assign_attribution_v1`（第 209 行）。
- 默认值 `2` 保留与 issue 0011 的精确向后兼容性。
- 其他方面不变。

调用者：
- `run_cases_v1`（`harness.py:224`）
- `run_case_full_v1`（`harness.py:229`）
- `TopKAttributionTest`（通过 `run_case_v1` 间接测试）
- `V0BackwardCompatTest`（通过 `run_case_v1` 间接测试）

---

#### 函数：`run_cases_v1`（已更新——新增 `top_k`）

位置：`cmd_audit/harness.py:224-225`

```python
def run_cases_v1(cases: list[ProbeCase], *, top_k: int = 2) -> list[AuditResult]:
    return [run_case_v1(case, top_k=top_k) for case in cases]
```

行为变更：新增 `top_k` 参数，传递给 `run_case_v1`。

---

#### 函数：`run_case_full_v1`（已更新——新增 `top_k`）

位置：`cmd_audit/harness.py:228-241`

```python
def run_case_full_v1(case: ProbeCase, *, top_k: int = 2) -> FullAuditResult:
    """Run the complete V1 pipeline: attribution -> ECS -> repair -> post-repair replay."""
    audit = run_case_v1(case, top_k=top_k)
    ...
```

行为变更：新增 `top_k` 参数，传递给 `run_case_v1`。ECS/修复阶段不变。

---

#### 函数：`run_cases_full_v1`（已更新——新增 `top_k`）

位置：`cmd_audit/harness.py:244-245`

```python
def run_cases_full_v1(cases: list[ProbeCase], *, top_k: int = 2) -> list[FullAuditResult]:
    return [run_case_full_v1(case, top_k=top_k) for case in cases]
```

行为变更：新增 `top_k` 参数，传递给 `run_case_full_v1`。

---

#### 函数：`write_comparison_metrics_table`（已更新——可选 Memory-Probe 列）

位置：`cmd_audit/harness.py:159-198`

```python
def write_comparison_metrics_table(
    results: list[AuditResult],
    output_path: str | Path,
    *,
    memory_probe_best_accuracy: float | None = None,    # ← 新增
) -> None:
```

行为变更：

1. 新增 `memory_probe_best_accuracy: float | None = None` 关键字参数（第 163 行）。
2. 条件字段名扩展（第 179-180 行）：
   ```python
   if memory_probe_best_accuracy is not None:
       fieldnames.append("memory_probe_best_accuracy")
   ```
3. 条件行值扩展（第 189-193 行）：
   ```python
   **(
       {"memory_probe_best_accuracy": f"{memory_probe_best_accuracy:.3f}"}
       if memory_probe_best_accuracy is not None
       else {}
   ),
   ```

设计原理：

- 该列在**每个**系统行中显示相同的值，无论系统名称如何。这是设计如此：Memory-Probe 是一个聚合基线，而非逐系统对比器。该列以 CMD 及其对比器作为参考点展示 Memory-Probe 的数据集级别上限。
- 当 `memory_probe_best_accuracy` 为 `None` 时（默认），列完全省略——向后兼容现有的调用者。

调用者：
- `ComparisonMetricsWithMemoryProbeTest`（直接测试）
- 最终用户管道脚本

### `cmd_audit/writers.py`

文件：`cmd_audit/writers.py`（289 行）。Issue 0013 变更：`write_attribution_table` 新增 2 个列。

---

#### 函数：`write_attribution_table`（已更新——新增列）

位置：`cmd_audit/writers.py:73-144`

新增列（第 122-127 行）：

```python
"top_k_labels": "|".join(result.attribution.top_k_labels),
"close_deltas": "|".join(
    f"{label}:{delta:.4f}" for label, delta in result.attribution.close_deltas
),
```

列位置：

- `top_k_labels` 插入在 `is_ambiguous` 之后。
- `close_deltas` 插入在 `top_k_labels` 之后。
- 两者都在 `diagnosis_cost` 之前。

列格式：

| 列 | V0 输出 | V1 输出（3 个紧密 deltas） | V1 输出（4+ 紧密 deltas） |
| --- | --- | --- | --- |
| `top_k_labels` | `write_error` | `retrieval_error\|compression_error\|write_error` | `retrieval_error\|compression_error\|write_error`（截断至 3） |
| `close_deltas` | `""`（空） | `retrieval_error:0.0000\|compression_error:0.0200\|write_error:0.0300` | `retrieval_error:0.0000\|compression_error:0.0200\|write_error:0.0300\|route_error:0.0400`（全部 4 个） |

格式细节：

- `top_k_labels`：标签按 recovery_gain 降序排列，由 `|` 连接（与 `top2_labels` 格式匹配）。
- `close_deltas`：每个对格式为 `label:delta`，delta 精确到 4 位小数（例如 `retrieval_error:0.0000`），对由 `|` 连接。空元组产生空字符串 `""`。

调用者：
- `run_case` / `run_case_v1` 管道（间接，通过 `write_attribution_table` 调用）
- `AttributionTableNewColumnsTest`（直接测试 CSV 输出）

### `cmd_audit/retrieval_baselines.py`

文件：`cmd_audit/retrieval_baselines.py`（400+ 行）。Issue 0013 变更：4 个函数从下划线前缀重命名为公共名称，供 `memory_probe.py` 复用。无逻辑变更。

---

#### 函数：`tokenize(text: str) -> list[str]`

位置：`cmd_audit/retrieval_baselines.py:115-117`

```python
def tokenize(text: str) -> list[str]:
    """Lowercase, extract alphanumeric runs, drop tokens shorter than 2 chars."""
    return [t for t in re.findall(r"[a-z0-9]{2,}", text.casefold())]
```

目的：

- 为 BM25 和 TF-IDF 将文本拆分为小写字母数字标记。
- 过滤短于 2 个字符的标记（噪声过滤）。
- 原名 `_tokenize`；重命名为 `tokenize` 以供 `memory_probe.py` 中的 `_retrieve_top1_bm25` 和 `_retrieve_top1_hybrid` 公共访问。

调用者（文件内）：
- `_prepare_bm25_index`（第 155-209 行，8 处调用）
- `compute_bm25_scores` 的调用者

调用者（跨模块）：
- `memory_probe._retrieve_top1_bm25`（`memory_probe.py:130`）
- `memory_probe._retrieve_top1_hybrid`（`memory_probe.py:145`）
- `memory_probe.build_tfidf_vectors`（通过内部使用）

---

#### 函数：`compute_bm25_scores(query_tokens, doc_tokens_list, k1=1.2, b=0.75) -> list[float]`

位置：`cmd_audit/retrieval_baselines.py:125-163`

```python
def compute_bm25_scores(
    query_tokens: list[str],
    doc_tokens_list: list[list[str]],
    k1: float = 1.2,
    b: float = 0.75,
) -> list[float]:
```

目的：

- 为所有文档计算标准 BM25 相关性分数。
- 原名 `_compute_bm25_scores`；重命名为 `compute_bm25_scores` 以供公共访问。
- 逻辑未更改。

调用者（文件内）：
- BM25 检索器（2 处调用）

调用者（跨模块）：
- `memory_probe._retrieve_top1_bm25`（`memory_probe.py:134`）
- `memory_probe._retrieve_top1_hybrid`（`memory_probe.py:151`）

---

#### 函数：`build_tfidf_vectors(memory_items, query) -> tuple[dict[str, float], list[dict[str, float]]]`

位置：`cmd_audit/retrieval_baselines.py:207-245`

```python
def build_tfidf_vectors(
    memory_items: list, query: str
) -> tuple[dict[str, float], list[dict[str, float]]]:
```

目的：

- 为查询和所有文档构建 TF-IDF 加权稀疏向量。
- 原名 `_build_tfidf_vectors`；重命名为 `build_tfidf_vectors` 以供公共访问。
- 逻辑未更改。

调用者（文件内）：
- 混合重排序检索器（1 处调用）

调用者（跨模块）：
- `memory_probe._retrieve_top1_cosine`（`memory_probe.py:118`）
- `memory_probe._retrieve_top1_hybrid`（`memory_probe.py:152`）

---

#### 函数：`cosine_similarity(vec_a, vec_b) -> float`

位置：`cmd_audit/retrieval_baselines.py:248-261`

```python
def cosine_similarity(
    vec_a: dict[str, float], vec_b: dict[str, float]
) -> float:
```

目的：

- 计算两个稀疏向量之间的余弦相似度。
- 原名 `_cosine_similarity`；重命名为 `cosine_similarity` 以供公共访问。
- 逻辑未更改。

调用者（文件内）：
- 混合重排序检索器（1 处调用）

调用者（跨模块）：
- `memory_probe._retrieve_top1_cosine`（`memory_probe.py:119`）
- `memory_probe._retrieve_top1_hybrid`（`memory_probe.py:153`）

## 测试结构

文件：`tests/test_cmd_audit_issue13_coupled_failure_and_memory_probe.py`。10 个测试类，42 个测试方法。

| 测试类 | 测试方法数 | 覆盖的 TDD 行为 |
| --- | --- | --- |
| `AttributionResultSchemaTest` | 4 | 默认字段存在且正确；`top_k_labels` 是字符串元组；`close_deltas` 是成对元组；`has_ingestion_trace` 拆分尊重新字段 |
| `TopKAttributionTest` | 4 | `top_k=3` 产生 3 个标签且有 3 个紧密 deltas；2 个紧密 deltas 时产生 2 个标签；1 个主导回放时产生 1 个标签；默认 `top_k=2` 截断至 2 |
| `CoupledFailureEdgeCaseTest` | 4 | 4 个紧密 deltas 截断至 `top_k=3`，但 `close_deltas` 暴露全部 4 个；成对正确性；阈值外 delta 被排除；所有标签对 V1 有效 |
| `V0BackwardCompatTest` | 4 | V0 `top_k_labels` 匹配 `top2_labels`；V0 案例通过带默认 `top_k=2` 的 V1 保留标签；`close_deltas` 在 V0 中为空；新字段存在于旧 V0 案例上 |
| `MemoryProbeWriteStrategiesTest` | 4 | `_write_fact_extraction` 产生 `fact_` 前缀项目；`_write_summarization` 产生 `summary_` 前缀项目；`_write_raw_chunks` 每个事件产生一个项目；所有 3 种策略返回非空元组 |
| `MemoryProbeGridTest` | 6 | 每个案例 9 个单元；每个单元具有有效的策略/方法；分数在 `[0, 1]` 内；最佳单元是单元之一；case_id 匹配；所有 9 对唯一 |
| `MemoryProbeBaselineTest` | 5 | `best_cell_accuracy` 在 `[0, 1]` 内；最佳策略和方法有效；案例计数匹配输入；每个案例结果有 9 个单元；数据类是不可变的 |
| `ComparisonMetricsWithMemoryProbeTest` | 4 | 提供值时列存在；`None` 时列缺失；值在每一行中；值可解析为浮点数 |
| `AttributionTableNewColumnsTest` | 4 | `top_k_labels` 和 `close_deltas` 在 CSV 标题中；V0 `top_k_labels` 匹配 `top2_labels`；V0 `close_deltas` 为空字符串；V1 `close_deltas` 具有有效的 `label:delta` 格式 |
| `RetrievedHelpersPublicTest` | 4 | `tokenize` 返回 `list[str]`；`compute_bm25_scores` 返回 `list[float]` 且查询词排名更高；`build_tfidf_vectors` 返回 `(dict, list[dict])`；`cosine_similarity` 对相同向量 > 0.9，对不相交向量 = 0.0 |

## 非回归分析

### V0 向后兼容性

对 V0 烟雾套件（`data/probe_cases/v0_issue3_cases.json`，6 个案例）运行带有默认参数的 V1 管道会产生：

| 案例 ID | V0 标签 (`run_case`) | V1 标签 (`run_case_v1`, top_k=2) | 匹配？ | 备注 |
| --- | --- | --- | --- | --- |
| v0-write-001 | `write_error` | `write_error` | ✓ | `has_ingestion_trace=True`（默认）——不拆分 |
| v0-compression-001 | `compression_error` | `compression_error` | ✓ | top_k 截断至 2；`close_deltas` 暴露额外的紧密标签（存在时） |
| v0-premature-extraction-001 | `premature_extraction_error` | `premature_extraction_error` | ✓ | 无变更 |
| v0-retrieval-001 | `retrieval_error` | `retrieval_error` | ✓ | `oracle_route` 可能会增加 `close_deltas` 条目（平局），但预测标签保持不变 |
| v0-injection-001 | `injection_error` | `injection_error` | ✓ | 无变更 |
| v0-reasoning-001 | `reasoning_error` | `reasoning_error` | ✓ | 无变更 |

所有 6 个 V0 标签保持不变。没有案例在默认参数下翻转其预测标签。`top_k_labels` 始终与 `top2_labels` 匹配（当 `top_k=2` 时）。V0 `close_deltas` 保持为空元组。V1 `close_deltas` 可能会显示额外的紧密标签（来自 `oracle_route` 等），但 `predicted_label` 不会改变——额外的紧密标签仅通过 `top_k_labels` 和 `close_deltas` 字段可见。

### 现有测试套件非回归

变更前的全部 345 个测试在变更后仍然通过。运行时之前为 387（issue 0011 的 262 + issue 0012 的 83 + 现有的 issue 0001-0010 = 345），现在为 387（345 个现有 + 42 个 issue 0013 新增）。

非回归检查清单：

- [x] 所有 345 个现有测试在带有新字段默认值的 `AttributionResult` 下通过。
- [x] `assign_attribution`（V0）为 `top_k_labels` 和 `close_deltas` 产生正确的默认值。
- [x] `assign_attribution_v1` 使用默认 `top_k=2` 产生与之前相同的结果。
- [x] 4 个重命名的检索辅助函数通过它们现有的测试（文件内和跨模块）。
- [x] `write_attribution_table` 为所有现有调用者输出向后兼容的 CSV。
- [x] 当 `memory_probe_best_accuracy` 未被传递时，`write_comparison_metrics_table` 不包含该列。
- [x] `__init__.py` 导出不会产生名称冲突（Memory-Probe 符号和检索辅助符号）。
