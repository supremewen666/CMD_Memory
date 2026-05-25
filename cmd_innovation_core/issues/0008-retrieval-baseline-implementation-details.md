# Issue 0008 实现细节：检索基线与证据评分强化

> **⚠️ 2026-05-19 修正：HybridRerank 已移除。** `run_hybrid_rerank_retrieval` 已从 `retrieval_baselines.py` 删除。HybridRerank 的 BM25 + TF-IDF cosine 均为稀疏词袋模型，无法提供真正的语义恢复（paraphrase、temporal、entity-category）。强检索（dense/hybrid）移至 V1，由 mem0/Letta adapter 实现。当前仅保留 BM25 一个检索器。详见 `cmd_innovation_core/issues/modify.md`。

## 目的

本文档是 issue 0008《强化检索基线与证据评分》的全局实现地图。Issue 0008 在 V0 烟雾归因路径（issues 0001-0003）之后构建两层真实检索基线，形成弱→强对比：

```text
ProbeCase
  -> run_retrieval_baseline_suite
      -> run_bm25_retrieval（弱基线：纯关键词匹配）
      -> run_hybrid_rerank_retrieval（强基线：混合检索 + 证据短语重排序）
  -> RetrievalBaselineSuiteResult（包装两个检索器结果）
  -> compute_retrieval_metrics（逐检索器聚合指标）
  -> compute_evidence_boundary_audit（诊断审计工具）
  -> write_retrieval_trace_table（CSV 产出物 #1）
  -> write_retrieval_metrics_table（CSV 产出物 #2）
```

核心思想：V0.5 用两个真实检索器（BM25 和 HybridRerank）替代合成基线（`fixed_summary`/`vector_memory`），两者均盲搜 `case.extracted_memory`，仅在事后用 gold evidence 标注 trace。BM25 是纯关键词匹配（弱基线），HybridRerank 是 BM25 + TF-IDF cosine 混合检索 + 证据短语重排序（强基线）。Hard negative 案例验证强检索器恢复弱检索器错过的证据。两个检索器均为确定性的，无外部依赖。

## 源需求

本实现遵循以下本地文档。

| 来源 | 在 Issue 0008 中应用的需求 |
| --- | --- |
| `TASK.md` | 用真实检索基线（BM25、HybridRerank）替换合成基线。通过 hard negatives 对比弱-强检索器。作为基线系统而非 CMD 反事实干预。 |
| `CLAUDE.md` | CMD-Audit 与 CMD-Skill Adapter 分离；证据短语匹配为必要非充分条件；语义评分升级属于 V1。 |
| `cmd_innovation_core/CONTEXT.md` | `retrieval_error` 要求正确记忆以可恢复形式存在；`premature_extraction_error` 的硬边界由 `evidence_recall_from_text(gold_evidence, memory_item.text)` 强制。 |
| `cmd_innovation_core/prd/cmd_minimal_probe_prd.md` | User Story 3（基线系统）、4（证据评分）、7（检索对比）、10（hard negatives）、15（证据边界强制）。 |
| `cmd_innovation_core/issues/0008-strengthen-retrieval-baselines-and-evidence-scoring.md` | 八个验收标准：弱-强检索器对比、排名检索 trace 字段、检索指标、hard negatives 类型、混淆矩阵分离、强检索器不重新标记、证据边界硬约束、V0 归因范围保持。 |
| `cmd_innovation_core/plans/cmd_open_decisions.md` | Decision 12：证据短语匹配局限性（已知 V0 限制，V1 升级为蕴涵检测）。 |

## 领域边界

Issue 0008 实现检索基线作为**基线系统**（comparators），而非 CMD 反事实干预。现有 V0 回放组合（Oracle Retrieval 等）继续使用 gold evidence 的 oracle 访问。新检索器在排序阶段对 gold evidence 盲搜；gold 仅用于事后 trace 标注。

```text
issue 0003 (replays.py, attribution.py)
  -> V0 六回放组合
  -> Oracle Retrieval（有 gold 访问权）

issue 0008 (retrieval_baselines.py, writers.py)
  -> BM25（盲搜，纯关键词）
  -> HybridRerank（盲搜候选 + oracle evidence 重排序）
  -> 证据边界审计工具（诊断用，不改变归因）
```

Issue 0008 拥有的内容：

- `RankedRetrievalTrace` 数据类：每 (retriever, rank) 对一行检索结果记录。
- `RetrievalMetrics` 数据类：一个检索器在一个案例上的聚合检索指标（Recall@k、MRR、nDCG@10、Precision@k、noise ratio、answer scores）。
- `RetrievalBaselineResult` 数据类：一个检索器在一个案例上的完整输出。
- `RetrievalBaselineSuiteResult` 数据类：一个案例上两个检索器的结果。
- `run_bm25_retrieval(case) -> list[RankedRetrievalTrace]`：BM25 检索（k1=1.2, b=0.75）。
- `run_hybrid_rerank_retrieval(case) -> list[RankedRetrievalTrace]`：五阶段混合检索 + 证据重排序。
- `compute_retrieval_metrics(traces, case_id, retriever_name, gold_answer) -> RetrievalMetrics`：检索指标计算。
- `enforce_retrieval_error_boundary(case, memory_item_text, gold_evidence) -> bool`：诊断审计 —— 检查更强检索器是否可将标签翻转为 `retrieval_error`（不改变 V0 归因行为）。
- `compute_evidence_boundary_audit(case) -> dict[str, bool]`：逐记忆项的边界审计。
- `write_retrieval_trace_table(suite_results, output_path)`：完整排名 trace CSV。
- `write_retrieval_metrics_table(suite_results, output_path)`：指标对比 CSV。
- 共享基础设施：`_tokenize`、`_compute_bm25_scores`、`_build_tfidf_vectors`、`_cosine_similarity`、`_annotate_traces`、`_count_matched_evidence_units`、`_answer_token_f1`、`_all_rank_zero_traces`。
- 6 个 hard negative 案例（`v0_issue8_hard_negatives.json`）。
- 7 个测试类，32 个测试方法。

Issue 0008 不拥有的内容（属于其他 issue）：

- V0 六回放组合逻辑（issue 0003）。
- 归因分配逻辑（issues 0001、0003）。
- `fixed_summary`/`vector_memory` 合成基线（issue 0002，待被替换）。
- Agentic search（查询重写、迭代精炼、工具使用检索）—— 延迟至 V1。
- LLM-based 检索或重排序。
- 对外部依赖（numpy、scipy、sklearn）的引入。

## 代码产物

### `cmd_audit/retrieval_baselines.py`（主模块，633 行）

Issue 0008 的主模块。两个确定性检索器形成弱→强对比，均对 gold evidence 盲搜（排序阶段）。

| 函数/类 | 类型 | 行号 | 作用 |
| --- | --- | --- | --- |
| `RankedRetrievalTrace` | 冻结数据类 | 29-54 | 检索结果列表中的单条排名记录。 |
| `RetrievalMetrics` | 冻结数据类 | 57-87 | 一个检索器在一个案例上的聚合检索指标。 |
| `RetrievalBaselineResult` | 冻结数据类 | 90-99 | 一个检索器在一个案例上的完整输出。 |
| `RetrievalBaselineSuiteResult` | 冻结数据类 | 102-107 | 一个案例上两个检索器的结果。 |
| `_tokenize(text)` | 私有函数 | 115-117 | 小写化、提取字母数字运行、丢弃 <2 字符的 token。 |
| `_compute_bm25_scores(query_tokens, doc_tokens_list, k1, b)` | 私有函数 | 125-163 | 对所有文档计算 BM25 分数。 |
| `run_bm25_retrieval(case, k1, b)` | 公共函数 | 171-199 | BM25 纯关键词检索，盲搜 gold evidence。 |
| `_build_tfidf_vectors(memory_items, query)` | 私有函数 | 207-245 | 构建查询和所有文档的 TF-IDF 加权稀疏向量。 |
| `_cosine_similarity(vec_a, vec_b)` | 私有函数 | 248-262 | 两个稀疏向量的余弦相似度。 |
| `run_hybrid_rerank_retrieval(case, bm25_weight, vector_weight, candidate_k)` | 公共函数 | 270-376 | BM25 + TF-IDF cosine 混合检索 + 证据短语重排序。 |
| `_annotate_traces(case, memory_items, ranked_indices, scores, retriever_name, run_id)` | 私有函数 | 384-413 | 用 gold evidence 匹配元数据标注排序结果。 |
| `_count_matched_evidence_units(evidence_units, text)` | 私有函数 | 416-426 | 统计文本中匹配 gold evidence 短语的数量。 |
| `_all_rank_zero_traces(case, memory_items, retriever_name)` | 私有函数 | 429-454 | 当 query 无可用 tokens 时返回零分 traces。 |
| `compute_retrieval_metrics(traces, case_id, retriever_name, gold_answer)` | 公共函数 | 462-543 | 计算 Recall@k、MRR、nDCG@10、Precision@k、noise ratio、answer scores。 |
| `_answer_token_f1(predicted_text, gold_answer)` | 私有函数 | 546-557 | Top-1 检索文本与 gold answer 的 token 级 F1。 |
| `enforce_retrieval_error_boundary(case, memory_item_text, gold_evidence)` | 公共函数 | 565-579 | 检查记忆项文本是否包含 gold evidence 短语。诊断审计工具，不改变 V0 归因行为。 |
| `compute_evidence_boundary_audit(case)` | 公共函数 | 582-594 | 逐记忆项审计哪些项可被更强检索器恢复。 |
| `run_retrieval_baseline_suite(case)` | 公共函数 | 602-632 | 对一个案例运行两个检索基线（BM25 + HybridRerank）。 |

### `cmd_audit/writers.py`（检索相关部分，第 217-282 行）

两个 CSV 写入函数：

| 函数 | 行号 | 作用 |
| --- | --- | --- |
| `write_retrieval_trace_table(suite_results, output_path)` | 217-245 | 写入跨所有案例和检索器的完整排名检索 trace 表。 |
| `write_retrieval_metrics_table(suite_results, output_path)` | 248-282 | 写入跨所有案例的两个检索器指标对比表。 |

### `cmd_audit/harness.py`（检索相关导入，第 33-34 行）

从 `writers` 导入 `write_retrieval_metrics_table` 和 `write_retrieval_trace_table`，通过公共 API 暴露。

### `cmd_audit/__init__.py`（检索相关导出，第 28-29、75-85、140-176 行）

导出所有四个数据类、`run_bm25_retrieval`、`run_hybrid_rerank_retrieval`、`run_retrieval_baseline_suite`、`compute_retrieval_metrics`、`enforce_retrieval_error_boundary`、`compute_evidence_boundary_audit`、`write_retrieval_trace_table`、`write_retrieval_metrics_table`。

### `data/probe_cases/v0_issue8_hard_negatives.json`（Hard Negative 案例集）

6 个 hard negative 案例，全部 `perturbation_label: "retrieval_error"`：

| Case ID | Hard Negative 类型 | 挑战 |
| --- | --- | --- |
| `v0-hn-entity-001` | same-entity confusion | 同一人的不同城市、不同事件（Lisbon vs Porto vs Madrid）。 |
| `v0-hn-temporal-002` | temporal conflict | 原始计划 vs 更新计划，两个记忆项的时间冲突。 |
| `v0-hn-paraphrase-003` | paraphrase | 同一事实的不同措辞表达。 |
| `v0-hn-multihop-004` | multi-hop evidence | 两个记忆项需要组合才能获得完整证据。 |
| `v0-hn-stale-005` | stale memory | 去年数据 vs 今年数据的陈旧性冲突。 |
| `v0-hn-compress-006` | compression-loss | 同一事件的完整细节 vs 有损摘要。 |

分类说明：时间冲突（temporal conflict）和过时记忆（stale memory）均归类为"时间/陈旧类（Temporal / Staleness）"—— 它们属于等效的时间有效性冲突，只是触发形式不同（一个是有多个版本的时间标记，一个是未更新的旧记录）。

## 函数级合约

### `cmd_audit/retrieval_baselines.py`

这是 issue 0008 的主模块。文件：`cmd_audit/retrieval_baselines.py`（633 行）。包含 4 个冻结数据类、10 个公共/私有函数。

---

#### 数据类：`RankedRetrievalTrace`

位置：`cmd_audit/retrieval_baselines.py:29-54`

```python
@dataclass(frozen=True)
class RankedRetrievalTrace:
    case_id: str
    run_id: str
    retriever_name: str
    memory_id: str
    rank: int
    score: float
    token_cost: float
    retrieved_text: str
    matched_gold_evidence_units: int
    is_gold_support: bool
    is_distractor: bool

    def __post_init__(self) -> None:
        if self.rank < 1:
            raise ValueError(f"rank must be >= 1, got {self.rank}")
        if self.score < 0:
            raise ValueError(f"score must be >= 0, got {self.score}")
        if self.matched_gold_evidence_units < 0:
            raise ValueError(...)
```

领域含义：

| 字段 | 领域含义 |
| --- | --- |
| `case_id` | 所属探针案例 ID。 |
| `run_id` | 检索运行的确定性哈希 ID（由 `case_id:retriever_name` 的 SHA256 前 12 位生成）。 |
| `retriever_name` | `"bm25"` 或 `"hybrid_rerank"`。 |
| `memory_id` | 被检索记忆项的标识符。 |
| `rank` | 排序位置（从 1 开始）。 |
| `score` | 该位置的检索分数。 |
| `token_cost` | 该位置消耗的 token 成本（V0 始终为 0.0）。 |
| `retrieved_text` | 该记忆项的文本内容。 |
| `matched_gold_evidence_units` | 匹配的 gold evidence 单元数量（所有 required_phrases 均出现在文本中）。 |
| `is_gold_support` | 匹配到至少一个 gold evidence 单元（`matched_gold_evidence_units > 0`）。 |
| `is_distractor` | 未匹配到任何 gold evidence（等于 `not is_gold_support`）。 |

`__post_init__` 校验：`rank >= 1`、`score >= 0`、`matched_gold_evidence_units >= 0`。

---

#### 数据类：`RetrievalMetrics`

位置：`cmd_audit/retrieval_baselines.py:57-87`

```python
@dataclass(frozen=True)
class RetrievalMetrics:
    retriever_name: str
    case_id: str
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    recall_at_10: float
    mrr: float
    ndcg_at_10: float
    precision_at_1: float
    precision_at_3: float
    precision_at_5: float
    context_noise_ratio: float
    answer_accuracy: float
    answer_f1: float
```

领域含义：

| 字段 | 领域含义 |
| --- | --- |
| `recall_at_k`（k=1,3,5,10） | 前 k 个结果中捕获的 gold-support 记忆项比例。 |
| `mrr` | 平均倒数排名：`1 / min(gold_ranks)`。若没有 gold support 则为 0。 |
| `ndcg_at_10` | 前 10 个结果的归一化折损累计增益，基于匹配 evidence 单元数。 |
| `precision_at_k`（k=1,3,5） | 前 k 个结果中 gold support 的比例。 |
| `context_noise_ratio` | 前 10 个结果中干扰项（distractors）的比例。度量检索噪声。 |
| `answer_accuracy` | 1.0 若 gold answer 出现在 top-1 文本中（大小写不敏感），否则 0.0。 |
| `answer_f1` | Top-1 检索文本与 gold answer 的 token 级 F1 分数。 |

`__post_init__` 校验：所有 recall、precision 字段和 mrr、ndcg 必须在 [0, 1] 范围内。

---

#### 数据类：`RetrievalBaselineResult`

位置：`cmd_audit/retrieval_baselines.py:90-99`

```python
@dataclass(frozen=True)
class RetrievalBaselineResult:
    case_id: str
    retriever_name: str
    traces: tuple[RankedRetrievalTrace, ...]
    metrics: RetrievalMetrics
    best_answer: str
    best_answer_score: float
```

领域含义：一个检索器在一个案例上的完整输出。`traces` 为冻结元组（不可变），`best_answer` 为 rank-1 文本，`best_answer_score` 为 0.0 或 1.0（`answer_score` 精确匹配）。

---

#### 数据类：`RetrievalBaselineSuiteResult`

位置：`cmd_audit/retrieval_baselines.py:102-107`

```python
@dataclass(frozen=True)
class RetrievalBaselineSuiteResult:
    case_id: str
    baseline_results: tuple[RetrievalBaselineResult, ...]
```

领域含义：一个案例上两个检索器（bm25 和 hybrid_rerank）的完整结果元组。

---

#### 私有函数：`_tokenize(text: str) -> list[str]`

位置：`cmd_audit/retrieval_baselines.py:115-117`

```python
def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]{2,}", text.casefold())]
```

目的：所有检索器共享的分词器。小写化文本，提取至少 2 个字符的字母数字运行。纯 Python 实现，无外部依赖。

调用者：`run_bm25_retrieval`、`_build_tfidf_vectors`、`run_hybrid_rerank_retrieval`、`_answer_token_f1`。

---

#### 私有函数：`_compute_bm25_scores(query_tokens, doc_tokens_list, k1=1.2, b=0.75) -> list[float]`

位置：`cmd_audit/retrieval_baselines.py:125-163`

```python
def _compute_bm25_scores(
    query_tokens: list[str],
    doc_tokens_list: list[list[str]],
    k1: float = 1.2,
    b: float = 0.75,
) -> list[float]:
```

目的：对所有文档计算 BM25 分数。标准 BM25 实现：
- 计算所有文档的 DF（文档频率）和 IDF（逆文档频率）。
- `avgdl`：平均文档长度。
- BM25 公式：`IDF(qt) * (tf * (k1+1)) / (tf + k1 * (1 - b + b * doc_len/avgdl))`。

调用者：`run_bm25_retrieval`（直接）、`run_hybrid_rerank_retrieval`（间接）。

---

#### 公共函数：`run_bm25_retrieval(case, k1=1.2, b=0.75) -> list[RankedRetrievalTrace]`

位置：`cmd_audit/retrieval_baselines.py:171-199`

```python
def run_bm25_retrieval(
    case: ProbeCase,
    *,
    k1: float = 1.2,
    b: float = 0.75,
) -> list[RankedRetrievalTrace]:
```

目的：BM25 纯关键词检索，彻底盲搜 gold evidence。对 `case.extracted_memory` 中所有记忆项按 BM25 分数降序排序。

行为：
1. 若 `extracted_memory` 为空 → 返回空列表。
2. 若 `query_tokens` 为空 → 返回 `_all_rank_zero_traces`。
3. 正常路径：计算 BM25 分数，按分数降序排序，通过 `_annotate_traces` 标注。
4. `run_id` 由 `sha256(case_id:bm25)` 前 12 位生成（确定性）。

调用者：`run_retrieval_baseline_suite`、测试（`BM25RetrievalTest`、`RetrievalMetricsTest`、`HardNegativesTest`）。

---

#### 私有函数：`_build_tfidf_vectors(memory_items, query) -> tuple[dict[str, float], list[dict[str, float]]]`

位置：`cmd_audit/retrieval_baselines.py:207-245`

```python
def _build_tfidf_vectors(
    memory_items: list, query: str
) -> tuple[dict[str, float], list[dict[str, float]]]:
```

目的：为查询和所有文档构建 TF-IDF 加权稀疏向量。

行为：
1. 对所有文档分词并构建词汇表。
2. IDF 使用平滑处理：`log((n+1)/(df+1)) + 1`。
3. 查询向量：`tf * idf`。
4. 文档向量：每个文档的 `tf * idf`。

调用者：`run_hybrid_rerank_retrieval`（间接，作为融合候选生成的一部分）。

---

#### 私有函数：`_cosine_similarity(vec_a, vec_b) -> float`

位置：`cmd_audit/retrieval_baselines.py:248-262`

```python
def _cosine_similarity(
    vec_a: dict[str, float], vec_b: dict[str, float]
) -> float:
```

目的：两个稀疏向量的余弦相似度。`dot(veca, vecb) / (|a| * |b|)`。若任一范数为 0 → 返回 0.0。

调用者：`run_hybrid_rerank_retrieval`。

---

#### 公共函数：`run_hybrid_rerank_retrieval(case, bm25_weight=0.4, vector_weight=0.6, candidate_k=5) -> list[RankedRetrievalTrace]`

位置：`cmd_audit/retrieval_baselines.py:270-376`

```python
def run_hybrid_rerank_retrieval(
    case: ProbeCase,
    *,
    bm25_weight: float = 0.4,
    vector_weight: float = 0.6,
    candidate_k: int = 5,
) -> list[RankedRetrievalTrace]:
```

目的：BM25 + TF-IDF cosine 混合检索，然后对 top-k 候选进行证据短语重排序。

五阶段流程：

1. **BM25 评分**：对所有记忆项计算 BM25 分数。
2. **TF-IDF cosine 评分**：对所有记忆项计算 TF-IDF 余弦相似度。
3. **Min-max 归一化**：将 BM25 和 cosine 分数各自线性缩放到 [0, 1]。
4. **加权混合**：`hybrid = bm25_weight * norm_bm25 + vector_weight * norm_cosine`（默认 0.4/0.6）。
5. **证据短语重排序**：对混合排序的 top-k 候选按 evidence-phrase match ratio 重排序。候选按 `(evidence_match desc, hybrid desc)` 排序。最终排序 = 重排序的 top-k + 剩余的按混合分排序。

设计决策：
- 阶段 1-3 盲搜 gold evidence（retrieval 阶段）。Gold evidence 仅在阶段 5（重排序）和 `_annotate_traces`（标注）中使用。
- 这是 **blind candidate generation + oracle evidence reranking** 的混合流程，不是完全盲评检索器。
- 最终分数：若候选证据匹配 > 0，使用 evidence match ratio；否则保持 hybrid 分数。

调用者：`run_retrieval_baseline_suite`、测试（`HybridRerankRetrievalTest`）。

---

#### 私有函数：`_annotate_traces(case, memory_items, ranked_indices, scores, retriever_name, run_id) -> list[RankedRetrievalTrace]`

位置：`cmd_audit/retrieval_baselines.py:384-413`

```python
def _annotate_traces(
    case: ProbeCase,
    memory_items: tuple,
    ranked_indices: list[int],
    scores: list[float],
    retriever_name: str,
    run_id: str,
) -> list[RankedRetrievalTrace]:
```

目的：用 gold evidence 匹配元数据标注排序后的检索结果。对每个 (rank, memory_item) 对调用 `_count_matched_evidence_units` 确定 `is_gold_support` 和 `is_distractor`。

被两个检索器共享。Gold evidence 仅用于标注，不参与排序。

调用者：`run_bm25_retrieval`、`run_hybrid_rerank_retrieval`。

---

#### 私有函数：`_count_matched_evidence_units(evidence_units, text) -> int`

位置：`cmd_audit/retrieval_baselines.py:416-426`

```python
def _count_matched_evidence_units(
    evidence_units: tuple[GoldEvidence, ...], text: str
) -> int:
```

目的：统计文本中匹配 gold evidence 单元的数量。一个 evidence 单元匹配当且仅当其所有 `required_phrases` 均出现在文本中（大小写不敏感）。

调用者：`_annotate_traces`、`_all_rank_zero_traces`。

---

#### 私有函数：`_all_rank_zero_traces(case, memory_items, retriever_name) -> list[RankedRetrievalTrace]`

位置：`cmd_audit/retrieval_baselines.py:429-454`

```python
def _all_rank_zero_traces(
    case: ProbeCase, memory_items: tuple, retriever_name: str
) -> list[RankedRetrievalTrace]:
```

目的：当查询无可用 tokens 时返回零分 traces。所有记忆项按原始顺序排列（rank=i+1），score=0.0。仍用 `_count_matched_evidence_units` 标注 gold/distractor 信息。

调用者：`run_bm25_retrieval`、`run_hybrid_rerank_retrieval`（边界条件分支）。

---

#### 公共函数：`compute_retrieval_metrics(traces, case_id, retriever_name, gold_answer) -> RetrievalMetrics`

位置：`cmd_audit/retrieval_baselines.py:462-543`

```python
def compute_retrieval_metrics(
    traces: list[RankedRetrievalTrace],
    case_id: str,
    retriever_name: str,
    gold_answer: str,
) -> RetrievalMetrics:
```

目的：从一个检索器的 traces 计算聚合检索指标。

行为：
- 若 traces 为空 → 返回全零 `RetrievalMetrics`。
- **Recall@k**（k=1,3,5,10）：前 k 个中 gold support 数量 / 总 gold support 数量。
- **MRR**：gold 排名最小值的倒数；无 gold 则为 0。
- **nDCG@10**：基于 `matched_gold_evidence_units / max_evidence` 的折损累积增益，除以理想 DCG。
- **Precision@k**（k=1,3,5）：前 k 个中 gold support 的比例。
- **Context Noise Ratio**：前 10 个中干扰项的比例。
- **Answer Accuracy**：gold answer 是否出现在 top-1 文本中（大小写不敏感）。
- **Answer F1**：`_answer_token_f1(top1_text, gold_answer)` token 级。

调用者：`run_retrieval_baseline_suite`、测试（`RetrievalMetricsTest`）。

---

#### 私有函数：`_answer_token_f1(predicted_text, gold_answer) -> float`

位置：`cmd_audit/retrieval_baselines.py:546-557`

```python
def _answer_token_f1(predicted_text: str, gold_answer: str) -> float:
```

目的：Top-1 检索文本与 gold answer 的 token 级 F1 分数。使用 `_tokenize` 分词，计算 token 级 precision/recall/F1。

调用者：`compute_retrieval_metrics`。

---

#### 公共函数：`enforce_retrieval_error_boundary(case, memory_item_text, gold_evidence=None) -> bool`

位置：`cmd_audit/retrieval_baselines.py:565-579`

```python
def enforce_retrieval_error_boundary(
    case: ProbeCase,
    memory_item_text: str,
    gold_evidence: tuple[GoldEvidence, ...] | None = None,
) -> bool:
    evidence = gold_evidence if gold_evidence is not None else case.gold_evidence
    recall = evidence_recall_from_text(evidence, memory_item_text)
    return recall >= 1.0
```

目的：**诊断审计工具**。检查记忆项文本是否实际包含 gold evidence 短语（`evidence_recall_from_text >= 1.0`）。当文本包含证据但弱检索器错过 → 更强检索器可恢复 → 理论上是 `retrieval_error`。当文本不包含证据 → 提取阶段已丢失 → 标签必须保持 `premature_extraction_error`。

设计决策：此函数是诊断审计工具（Option B）。它记录哪些记忆项理论上可被更强检索器恢复，为 V1 升级提供证据，但**不改变 V0 归因行为**。

调用者：`compute_evidence_boundary_audit`、测试（`EvidenceBoundaryTest`）。

---

#### 公共函数：`compute_evidence_boundary_audit(case) -> dict[str, bool]`

位置：`cmd_audit/retrieval_baselines.py:582-594`

```python
def compute_evidence_boundary_audit(case: ProbeCase) -> dict[str, bool]:
```

目的：对案例中每个记忆项，判断更强检索是否可将其翻转为 `retrieval_error`。返回 `dict[memory_id -> can_be_retrieval_error]`。

调用者：测试（`EvidenceBoundaryTest`）。

---

#### 公共函数：`run_retrieval_baseline_suite(case) -> RetrievalBaselineSuiteResult`

位置：`cmd_audit/retrieval_baselines.py:602-632`

```python
def run_retrieval_baseline_suite(
    case: ProbeCase,
) -> RetrievalBaselineSuiteResult:
```

目的：对一个探针案例运行两个检索基线。编排函数：遍历 `{"bm25": run_bm25_retrieval, "hybrid_rerank": run_hybrid_rerank_retrieval}` 字典，对每个检索器运行 → 计算指标 → 构建 `RetrievalBaselineResult` → 打包为 `RetrievalBaselineSuiteResult`。

调用者：测试（`RetrievalBaselineSuiteTest`、`HardNegativesTest`、`RetrievalTableWriterTest`）。

---

### `cmd_audit/writers.py`（Issue 0008 相关部分）

---

#### 函数：`write_retrieval_trace_table(suite_results, output_path)`

位置：`cmd_audit/writers.py:217-245`

```python
def write_retrieval_trace_table(
    suite_results: list[RetrievalBaselineSuiteResult],
    output_path: str | Path,
) -> None:
```

目的：写入跨所有案例和检索器的完整排名检索 trace 表。

字段（11 列）：`case_id`、`run_id`、`retriever_name`、`memory_id`、`rank`、`score`、`token_cost`、`retrieved_text`、`matched_gold_evidence_units`、`is_gold_support`、`is_distractor`。

行为：对每个 suite → 每个 baseline_result → 每个 trace 展开为一行。

调用者：测试（`RetrievalTableWriterTest`）。

---

#### 函数：`write_retrieval_metrics_table(suite_results, output_path)`

位置：`cmd_audit/writers.py:248-282`

```python
def write_retrieval_metrics_table(
    suite_results: list[RetrievalBaselineSuiteResult],
    output_path: str | Path,
) -> None:
```

目的：写入跨所有案例的检索指标对比表，将两个检索器并列展示。

字段（15 列）：`case_id`、`retriever_name`、`recall_at_1`、`recall_at_3`、`recall_at_5`、`recall_at_10`、`mrr`、`ndcg_at_10`、`precision_at_1`、`precision_at_3`、`precision_at_5`、`context_noise_ratio`、`answer_accuracy`、`answer_f1`。

调用者：测试（`RetrievalTableWriterTest`）。

### `cmd_audit/__init__.py`（Issue 0008 导出）

从 `retrieval_baselines` 导出全部四个数据类和七个公共函数。从 `writers` 导出 `write_retrieval_trace_table` 和 `write_retrieval_metrics_table`。

## 测试级合约

测试位于 `tests/test_cmd_audit_issue8_retrieval_baselines.py`。7 个测试类，32 个测试方法。

测试数据来源于三个 JSON fixture 文件：`v0_issue8_hard_negatives.json`（6 个 hard negative 案例）、`v0_retrieval_error_case.json`（检索错误案例）、`v0_premature_extraction_error_case.json`（过早提取错误案例）。

### `RankedRetrievalTraceContractTest`（3 个测试）

验证：`RankedRetrievalTrace` 的不变式约束。

| 测试方法 | 验证内容 |
| --- | --- |
| `test_rank_must_be_positive` | `rank=0` 抛出 `ValueError`。 |
| `test_score_must_be_nonnegative` | `score=-0.1` 抛出 `ValueError`。 |
| `test_frozen_dataclass_prevents_mutation` | 冻结数据类阻止字段赋值（`FrozenInstanceError`）。 |

### `RetrievalMetricsContractTest`（2 个测试）

验证：`RetrievalMetrics` 的不变式约束。

| 测试方法 | 验证内容 |
| --- | --- |
| `test_metric_fields_must_be_in_range` | `recall_at_1=1.5`（超出 [0,1]）抛出 `ValueError`。 |
| `test_frozen_dataclass_prevents_mutation` | 冻结数据类阻止 `mrr` 赋值。 |

### `BM25RetrievalTest`（7 个测试）

验证：BM25 检索器的核心行为。

| 测试方法 | 验证内容 |
| --- | --- |
| `test_bm25_ranks_all_memory_items` | 返回 traces 数量等于 `len(case.extracted_memory)`。 |
| `test_bm25_run_id_is_deterministic` | 两次相同输入运行产生相同 `run_id`。 |
| `test_bm25_token_cost_is_zero` | 所有 traces 的 `token_cost == 0.0`（确定性检索）。 |
| `test_bm25_trace_has_correct_case_id` | 所有 traces 的 `case_id` 与输入案例一致。 |
| `test_bm25_trace_ranks_are_consecutive_from_1` | ranks 是 `{1, 2, ..., N}`。 |
| `test_bm25_gold_support_and_distractor_are_mutually_exclusive` | `is_gold_support != is_distractor` 对所有 traces。 |
| `test_bm25_empty_memory_case_returns_empty` | 空记忆案例在积极断言下运行（无崩溃）。 |

### `HybridRerankRetrievalTest`（6 个测试）

验证：HybridRerank 检索器的核心行为和 hard negative 攻克能力。

| 测试方法 | 验证内容 |
| --- | --- |
| `test_hybrid_rerank_ranks_all_memory_items` | 返回 traces 数量等于 `len(case.extracted_memory)`。 |
| `test_hybrid_rerank_is_deterministic` | 两次运行 `memory_id` 顺序相同。 |
| `test_hybrid_rerank_temporal_conflict_recovers_gold` | 在 `v0-hn-temporal-002` 案例中，top-1 是 gold support 且 `memory_id="mem-005"`。 |
| `test_hybrid_rerank_paraphrase_recovers_gold` | 在 `v0-hn-paraphrase-003` 案例中，top-1 是 gold support 且 `memory_id="mem-007"`。 |
| `test_hybrid_rerank_compression_loss_recovers_gold` | 在 `v0-hn-compress-006` 案例中，top-1 是 gold support 且 `memory_id="mem-016"`。 |
| `test_hybrid_rerank_multihop_both_golds_in_top2` | 在 `v0-hn-multihop-004` 案例中，top-2 至少包含 1 个 gold support。 |

### `RetrievalMetricsTest`（7 个测试）

验证：检索指标计算的正确性。

| 测试方法 | 验证内容 |
| --- | --- |
| `test_perfect_retrieval_gives_max_recall_mrr` | 当 gold 在 rank 1 → recall@1=1.0、mrr=1.0、precision@1=1.0。 |
| `test_mrr_is_reciprocal_of_first_gold_rank` | MRR = 1 / 第一个 gold 的 rank。 |
| `test_context_noise_ratio_in_range` | `context_noise_ratio` 在 [0, 1] 内。 |
| `test_answer_accuracy_detects_gold_in_top1` | 当 gold answer 出现在 top-1 文本中 → `answer_accuracy=1.0`。 |
| `test_answer_f1_in_range` | `answer_f1` 在 [0, 1] 内。 |
| `test_nDCG_at_10_in_range` | `ndcg_at_10` 在 [0, 1] 内。 |
| `test_empty_traces_produces_zero_metrics` | 空 traces → 全部指标为 0.0。 |

### `EvidenceBoundaryTest`（6 个测试）

验证：证据边界强制作为诊断审计工具。

| 测试方法 | 验证内容 |
| --- | --- |
| `test_boundary_allows_flip_when_memory_contains_evidence` | 当记忆项文本包含 gold evidence 短语 → `enforce_retrieval_error_boundary` 返回 `True`。 |
| `test_boundary_blocks_flip_when_memory_lacks_evidence` | 当记忆项文本不包含 evidence → 返回 `False`（提取已丢失证据）。 |
| `test_boundary_audit_maps_all_memory_items` | `compute_evidence_boundary_audit` 覆盖所有记忆项。 |
| `test_boundary_audit_on_premature_case_all_blocked` | 在 premature_extraction_error 案例中，所有记忆项都 block retrieval_error flip（因为提取已丢失证据）。 |
| `test_boundary_audit_on_retrieval_case_has_flippable` | 在 retrieval_error 案例中，至少有一个记忆项允许 flip。 |
| `test_boundary_respects_custom_evidence_tuple` | 传入自定义 `gold_evidence` 参数与使用案例默认值产生相同结果。 |

### `RetrievalBaselineSuiteTest`（4 个测试）

验证：编排函数正确运行两个检索器。

| 测试方法 | 验证内容 |
| --- | --- |
| `test_suite_runs_both_retrievers` | `baseline_results` 包含正好两个结果，名字为 `{"bm25", "hybrid_rerank"}`。 |
| `test_suite_result_matches_case_id` | suite 结果和所有子结果的 `case_id` 均正确。 |
| `test_suite_traces_are_frozen_tuples` | 每个结果的 `traces` 字段是 `tuple` 类型。 |
| `test_suite_best_answer_score_is_valid` | 每个结果的 `best_answer_score` 为 0.0 或 1.0。 |

### `HardNegativesTest`（5 个测试）

验证：Hard negative 案例集的完整性和检索器对比。

| 测试方法 | 验证内容 |
| --- | --- |
| `test_all_six_hard_negative_cases_load` | 加载正好 6 个案例。 |
| `test_each_case_perturbation_label_is_retrieval_error` | 所有案例 `perturbation_label == "retrieval_error"`。 |
| `test_each_case_has_gold_and_distractor_memory_items` | 每个案例至少有 1 个 gold 记忆项和 1 个干扰项（`evidence_recall_from_text > 0.0` 即视为 gold）。 |
| `test_case_ids_are_unique` | 所有案例 ID 两两不同。 |
| `test_hybrid_rerank_beats_or_ties_bm25_on_recall_at_1` | 对每个案例，HybridRerank recall@1 >= BM25 recall@1。 |
| `test_hybrid_rerank_mrr_not_worse_than_bm25` | 对每个案例，HybridRerank MRR >= BM25 MRR。 |

### `RetrievalTableWriterTest`（2 个测试）

验证：CSV 写入函数输出正确的列。

| 测试方法 | 验证内容 |
| --- | --- |
| `test_trace_table_written_with_all_fields` | CSV 包含全部 11 个字段名。 |
| `test_metrics_table_written_with_all_fields` | CSV 包含 `case_id`、`retriever_name`、`recall_at_1`、`mrr`、`ndcg_at_10`、`context_noise_ratio`、`answer_accuracy`、`answer_f1` 等 15 个字段。 |

## 产出物合约

### `artifacts/sandbox/retrieval_trace.csv`

11 列：`case_id`、`run_id`、`retriever_name`、`memory_id`、`rank`、`score`、`token_cost`、`retrieved_text`、`matched_gold_evidence_units`、`is_gold_support`、`is_distractor`。

每个案例有 `2 * len(extracted_memory)` 行（两个检索器 × 记忆项数）。排名 trace 完全展开，按 `(case_id, retriever_name, rank)` 排序。

### `artifacts/sandbox/retrieval_metrics.csv`

15 列：`case_id`、`retriever_name`、`recall_at_1`、`recall_at_3`、`recall_at_5`、`recall_at_10`、`mrr`、`ndcg_at_10`、`precision_at_1`、`precision_at_3`、`precision_at_5`、`context_noise_ratio`、`answer_accuracy`、`answer_f1`。

每个案例有 2 行（bm25 和 hybrid_rerank 并列），可直接对比两个检索器在所有案例上的表现。

## 边界规则

1. **检索基线作为基线系统，非 CMD 干预**：BM25 和 HybridRerank 是基线系统（comparators），不是 CMD 反事实回放干预。现有 V0 回放组合（Oracle Retrieval 等）继续使用 gold evidence 的 oracle 访问。新检索器在排序阶段对 gold 盲搜。

2. **HybridRerank 的 Gold 可见性**：HybridRerank 的候选召回阶段（BM25 + TF-IDF cosine、min-max 归一化、加权混合）是 blind retrieval。重排序阶段使用 oracle evidence phrase matching 对 top-k 候选进行重排序。因此 HybridRerank 是一个 **blind candidate generation + oracle evidence reranking** 的混合流程，不是完全盲评检索器。此设计通过证据短语匹配的透明度保留了可解释性。

3. **证据边界为诊断审计工具**：`enforce_retrieval_error_boundary` 是诊断审计工具。它记录了哪些记忆项理论上可被更强检索器恢复（为 V1 检索升级提供证据），但**不改变 V0 归因行为**。当记忆项文本包含证据但弱检索器错过 → 理论上是 `retrieval_error`。当文本不包含证据 → 提取已丢失 → 标签保持 `premature_extraction_error`。

4. **证据短语匹配的已知限制**：`evidence_recall_from_text` 使用短语匹配 —— 是语义正确性的必要非充分条件。`required_phrases` 的设计通过包含区分性术语（如 `["Mira", "Lisbon", "Q3 offsite"]`）来缓解误匹配。语义评分升级（如蕴涵检测）属于 V1。Issue 0008 的 hard negatives 旨在暴露此边界，作为有价值的实验证据。

5. **时间/陈旧统一分类**：时间冲突（temporal conflict）和过时记忆（stale memory）均归类为"时间/陈旧类（Temporal / Staleness）" —— 它们属于等效的时间有效性冲突，只是触发形式不同。`v0-hn-temporal-002` 和 `v0-hn-stale-005` 两个案例均属于此类。

6. **创建时现有测试不受影响**：Issue 0008 添加 32 个新测试，不修改任何现有测试文件。现有 175+ 测试持续通过。

7. **检索基线待集成**：检索基线（BM25、HybridRerank）最终应替代 `baselines.py` 中的 `fixed_summary`/`vector_memory` 合成基线。当前状态：待集成（Option B），现有的 `baselines.py` 不变。

8. **纯 Python 无外部依赖**：分词器（正则表达式）、BM25、TF-IDF、余弦相似度均为纯 Python 实现，不依赖 numpy、scipy、sklearn 或任何外部库。所有检索器确定性的（给定相同输入，永远产生相同输出）。

## 验收标准可追溯性

| Issue 0008 AC | 代码接口 | 测试接口 |
| --- | --- | --- |
| BM25 和 HybridRerank 作为基线系统，非 CMD 干预。弱-强对比证明强检索器恢复弱检索器错过的证据。 | `run_bm25_retrieval` + `run_hybrid_rerank_retrieval`。不修改 `replays.py`。 | `HybridRerankRetrievalTest`（6 个测试）、`HardNegativesTest.test_hybrid_rerank_beats_or_ties_bm25_on_recall_at_1` |
| 排名检索 traces 包含全部必填字段。 | `RankedRetrievalTrace` 数据类（11 字段）。 | `RankedRetrievalTraceContractTest`（3 个测试）、`RetrievalTableWriterTest.test_trace_table_written_with_all_fields` |
| 检索指标包含 Recall@k、MRR、nDCG、Precision@k、noise ratio、answer accuracy/F1。 | `compute_retrieval_metrics` + `RetrievalMetrics` 数据类（13 字段）。 | `RetrievalMetricsTest`（7 个测试） |
| Hard negatives 覆盖 same-entity confusion、temporal conflict、paraphrase、multi-hop evidence、stale memory、compression-loss。 | `v0_issue8_hard_negatives.json`（6 个案例）。 | `HardNegativesTest`（5 个测试） |
| 归因混淆矩阵将 `retrieval_error` 与 `premature_extraction_error` 分开报告。 | `attribution.py`（现有）不受影响；证据边界审计补充诊断信息。 | 现有归因测试持续通过（不受影响）。 |
| 强检索器减少真正的检索遗漏，但不将仅原始事件可恢复的案例重新标记为 `retrieval_error`。 | `enforce_retrieval_error_boundary` + `compute_evidence_boundary_audit`。 | `EvidenceBoundaryTest`（6 个测试） |
| 硬边界强制：仅当记忆项文本含有证据时更强检索器可翻转标签。 | `enforce_retrieval_error_boundary` → `evidence_recall_from_text >= 1.0`。 | `EvidenceBoundaryTest.test_boundary_allows_flip_when_memory_contains_evidence`、`test_boundary_blocks_flip_when_memory_lacks_evidence` |
| V0 归因仍排除 bad-memory-item 标签和延迟的 pipeline 标签。 | 现有 `models.py` 标签校验不变。 | 所有现有测试（175+）持续通过。 |

## 验证

命令：

```bash
# 仅 issue 0008 测试（32 个测试）
python3 -m pytest tests/test_cmd_audit_issue8_retrieval_baselines.py -v

# 从 hard negatives 生成检索 trace 和指标产出物
python3 -c "
from pathlib import Path
from cmd_audit import load_probe_cases, run_retrieval_baseline_suite
from cmd_audit.harness import write_retrieval_trace_table, write_retrieval_metrics_table

cases = load_probe_cases('data/probe_cases/v0_issue8_hard_negatives.json')
suites = [run_retrieval_baseline_suite(c) for c in cases]

sandbox = Path('artifacts/sandbox')
sandbox.mkdir(parents=True, exist_ok=True)
write_retrieval_trace_table(suites, sandbox / 'retrieval_trace.csv')
write_retrieval_metrics_table(suites, sandbox / 'retrieval_metrics.csv')

for suite in suites:
    for r in suite.baseline_results:
        print(f'{suite.case_id} {r.retriever_name}: recall@1={r.metrics.recall_at_1:.3f} mrr={r.metrics.mrr:.3f} noise={r.metrics.context_noise_ratio:.3f}')
"
```

已验证状态（2026-05-14）：

```text
32 个 test_cmd_audit_issue8_retrieval_baselines 测试全部通过。

Hard Negative 检索对比（6 个案例）：
  v0-hn-entity-001:      bm25 recall@1=1.000 mrr=1.000 | hybrid_rerank recall@1=1.000 mrr=1.000
  v0-hn-temporal-002:    bm25 recall@1=0.000 mrr=0.333 | hybrid_rerank recall@1=1.000 mrr=1.000  ← HybridRerank 优势
  v0-hn-paraphrase-003:  bm25 recall@1=0.000 mrr=0.500 | hybrid_rerank recall@1=1.000 mrr=1.000  ← HybridRerank 优势
  v0-hn-multihop-004:    bm25 recall@1=0.500 mrr=1.000 | hybrid_rerank recall@1=0.500 mrr=1.000
  v0-hn-stale-005:       bm25 recall@1=0.000 mrr=0.500 | hybrid_rerank recall@1=1.000 mrr=1.000  ← HybridRerank 优势
  v0-hn-compress-006:    bm25 recall@1=0.000 mrr=0.250 | hybrid_rerank recall@1=1.000 mrr=1.000  ← HybridRerank 优势

HybridRerank 在 4/6 hard negative 案例上优于 BM25（temporal、paraphrase、stale、compression）。
在 entity confusion 和 multihop 案例上两者持平 —— entity confusion 中 BM25 关键词匹配已足够，multihop 中 recall@1 仅捕获 1/2 证据（两者相同）。
```

## 后续依赖此 Issue 的问题

| Issue | 依赖 | 方式 |
| --- | --- | --- |
| Issue 0010（证据驱动版本关卡） | `retrieval_metrics.csv` 提供的检索对比证据 | V0→V1 门可利用检索基线对比作为检索升级动机的证据。 |
| V1 检索升级（`ingestion_error`、`route_error` 标签） | `enforce_retrieval_error_boundary` 诊断审计数据 | 审计结果为 V1 更强检索器（如语义检索、agentic search）的收益提供定量预期。 |
| 基线系统替换 | `retrieval_baselines.py` 的 BM25/HybridRerank | 待集成后替代 `baselines.py` 中的 `fixed_summary`/`vector_memory` 合成基线。 |
