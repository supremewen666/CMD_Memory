# Issue 0020-D: Failure Memory Upgrade — Composite Key + fm_context Injection

**Status**: design
**Date**: 2026-05-23
**Decision**: 32 point 7 (fm_context 组成), point 8 (FM retrieval key), point 9 (FM storage), point 13 (injection timing)
**Parent**: Decision 32 Post-Gate Pipeline
**Blocked by**: None — independent from other 0020 issues

## 目的

升级 Failure Memory 从当前 V1 简单 keyword-based 检索到三维复合 key 检索（label + query_keywords + memory_top_terms），定义 `fm_context` 的正确组成（wrong_memory + original_evidence），并确定 FM 注入时机（ECS 阶段下游，在 hook 时刻检索但注入在 ECS 构建后）。

## 源需求

| 来源 | 应用的需求 |
| --- | --- |
| `cmd_open_decisions.md` Decision 32 point 7 | fm_context 组成: `wrong_memory + original_evidence`（错误块 + 证据），不是 `corrected_memory` 的重复。fm_context = 诊断信号（为什么错了），corrected_memory = 修复信号（应该是什么样）。完整 repair context = baseline + label + evidence_block + fm_context。 |
| `cmd_open_decisions.md` Decision 32 point 8 | FM 复合检索 key: `label + query_keywords + memory_top_terms`（BM25 三维检索）。升级当前 `label|query_keywords` 纯 keyword overlap。`memory_top_terms` = BM25 从 current retrieved_items 提取 top N 词。 |
| `cmd_open_decisions.md` Decision 32 point 9 | 仅 `recovered` ECS 存储。Per-agent 持久化: `FAILURE_MEMORY.md` 或与 agent MEMORY.md 合并。 |
| `cmd_open_decisions.md` Decision 32 point 13 | fm_context 注入时机: ECS 阶段下游（非 replay 前，保证因果纯度）。FM 检索可在 hook 时刻触发，但 fm_context 在 ECS 构建后才注入 repair context。 |
| `TASK.md` | Issue 0020-D — 独立。Composite key: label+query_keywords+memory_top_terms。fm_context = wrong_memory + original_evidence，ECS 阶段注入。 |

## 领域边界

Issue 0020-D 升级 Failure Memory 检索和存储机制。它不定义 ECS 构建逻辑（0020-G），不定义迭代修复（0020-A），不定义 hook 集成（0020-C）。

```text
FailureMemoryStore（cmd_audit/failure_memory.py 升级）
  → trigger_signature: "label|query_keywords"（旧, 保留兼容）
  → trigger_signature_v2: "label|query_keywords|memory_top_terms"（新, 三维复合 key）
  → _extract_memory_top_terms: BM25 从 retrieved_items 提取 top N 词
  → query(label, query_keywords, memory_top_terms) → 三维 BM25 检索
  → FailureMemoryRecord 新增:
      - wrong_memory: str（错误记忆内容）
      - original_evidence: str（证据/为什么是错的）
      - fm_context: property → wrong_memory + original_evidence
  → 仅 recovered ECS 存储
```

Issue 0020-D 拥有的内容：
- `FailureMemoryStore._extract_memory_top_terms` 新方法
- `FailureMemoryStore.query` 升级为三维 BM25 检索
- `FailureMemoryRecord` 新增 `wrong_memory`、`original_evidence`、`fm_context` property、`memory_top_terms`
- `trigger_signature` 升级逻辑（保留旧格式兼容）

## 计划代码产出物

| 产出物 | 角色 |
| --- | --- |
| `cmd_audit/failure_memory.py` | 升级 `FailureMemoryStore`（三维检索、`query` 升级、`_extract_memory_top_terms`）、`FailureMemoryRecord`（新增 4 字段 + `fm_context` property） |
| `cmd_audit/retrieval_baselines.py` | 复用 `compute_bm25_scores` 做 memory_top_terms 提取 |
| `tests/test_cmd_audit_issue20_D_failure_memory.py` | 测试文件 |

## 模块地图

| 模块 | Issue 0020-D 角色 |
| --- | --- |
| `cmd_audit/failure_memory.py` | **已更新**。`FailureMemoryStore` 升级 `query` 为三维 BM25 检索；`FailureMemoryRecord` 新增 `wrong_memory`, `original_evidence`, `memory_top_terms`, `trigger_signature_v2`, `fm_context` property；新增 `_extract_memory_top_terms` |
| `cmd_audit/retrieval_baselines.py` | 复用 `compute_bm25_scores`（query vs item.text），提取 top N 词 |

## 调用图（计划）

```text
cmd_audit/failure_memory.py

  → _extract_memory_top_terms(query, retrieved_items, top_n=10) -> str
    → 对每个 item ∈ retrieved_items:
        → bm25_score = compute_bm25(query, item.text)
    → 取 top-N BM25 scores 的 item
    → 提取这些 item 的高频词（复用 _extract_keywords 逻辑）
    → 返回 "term1 term2 term3 ..."（空格分隔）

  → FailureMemoryStore.query(label, query_keywords, memory_top_terms, top_k=3)
    → 对每条 FM record:
        → score_label = 1.0 if record.label == label else 0.0
        → score_query = BM25(query_keywords, record.query_keywords)
        → score_memory = BM25(memory_top_terms, record.memory_top_terms)
        → combined_score = w1*score_label + w2*score_query + w3*score_memory
    → 按 combined_score 降序
    → 返回 top_k records

  → store(case_id, label, ecs, query_keywords, memory_top_terms)
    → 仅 recovered ECS 时调用
    → trigger_signature_v2 = f"{label}|{query_keywords}|{memory_top_terms}"
    → 保留 trigger_signature = f"{label}|{query_keywords}"（旧）
```

## 数据流

### FailureMemoryRecord 扩展

```python
@dataclass(frozen=True)
class FailureMemoryRecord:
    # 现有字段（不变）
    case_id: str
    label: str
    trigger_signature: str         # "label|query_keywords"（旧，保留）
    corrected_memory: str          # 修复信号
    repair_guidance: str

    # 新字段（0020-D）
    wrong_memory: str = ""         # 错误时的记忆内容（诊断信号）
    original_evidence: str = ""    # 为什么这是错的（诊断信号）
    trigger_signature_v2: str = "" # "label|query_keywords|memory_top_terms"（新 key）
    memory_top_terms: str = ""     # 当前案例 retrieved_items 的 top terms

    @property
    def fm_context(self) -> str:
        """诊断信号: 错误块 + 证据。与 corrected_memory（修复信号）互补。"""
        parts = []
        if self.wrong_memory:
            parts.append(f"WRONG MEMORY:\n{self.wrong_memory}")
        if self.original_evidence:
            parts.append(f"EVIDENCE:\n{self.original_evidence}")
        return "\n\n".join(parts)
```

### fm_context vs corrected_memory

| 字段 | 类型 | 含义 | 注入阶段 |
| --- | --- | --- | --- |
| `wrong_memory` | 诊断信号 | 过去的错误记忆内容 | ECS 阶段下游 |
| `original_evidence` | 诊断信号 | 为什么被判定为错误 | ECS 阶段下游 |
| `fm_context` | 诊断信号（computed） | wrong_memory + original_evidence | ECS 阶段下游 |
| `corrected_memory` | 修复信号 | 修改后的正确记忆 | Post-Repair 阶段 |

### 存储条件

```text
仅 recovered ECS → 存储 FM record
partial/failed ECS → 不存储
AttributionFailed → 不存储
```

## 函数级合约（计划）

### `FailureMemoryStore._extract_memory_top_terms`

```python
def _extract_memory_top_terms(
    self, query: str, retrieved_items: tuple[MemoryItem, ...], top_n: int = 10
) -> str:
    """从 current retrieved_items 提取与 query 最相关的 top-N 项的代表词。
    对每个 item 计算 BM25(query, item.text)，取得分最高 top_n 项，提取高频词。
    返回空格分隔的 term 字符串。
    """
```

### `FailureMemoryStore.query`（升级）

```python
def query(
    self, label: str, query_keywords: str = "",
    memory_top_terms: str = "", top_k: int = 3
) -> tuple[FailureMemoryRecord, ...]:
    """三维 BM25 复合检索。
    combined = w1*label_match + w2*BM25(query_kw) + w3*BM25(memory_terms)
    权重默认: w1=0.5, w2=0.3, w3=0.2（离线可校准）
    空 memory_top_terms 自动降级为二维检索。
    """
```

## 测试结构（计划）

| 测试类 | 验收标准 | 测试数量 | 覆盖内容 |
| --- | --- | --- |
| `FMContextDataModelTest` | fm_context 数据模型 | ~6 | wrong_memory/original_evidence 字段、fm_context property 拼接、空字段处理、fm_context ≠ corrected_memory |
| `MemoryTopTermsExtractionTest` | top terms 提取 | ~5 | BM25 top-N item 选择、term 提取正确性、empty items、单 item、top_n 控制 |
| `CompositeKeyRetrievalTest` | 三维检索 | ~8 | 精确 label match、query BM25 匹配、memory top terms BM25 匹配、combined 排序、top_k 控制、降级为二维、空参数处理 |
| `StorageConditionTest` | 存储条件 | ~5 | recovered 存储、partial/failed 不存储、AttributionFailed 不存储、trigger_signature_v2 正确 |
| `FMContextInjectionTimingTest` | 注入时机 | ~4 | hook 时刻检索、ECS 阶段注入（非 replay 前）、replay context 不含 fm_context、ECS context 含 fm_context |
| `BackwardCompatibilityTest` | 向后兼容 | ~4 | 旧 trigger_signature 仍可用、新字段默认空、旧 FM record 加载不报错、旧 query 接口仍工作 |

## 非回归分析

### 现有模块变更
- `failure_memory.py`: `FailureMemoryRecord` 新增 4 字段（均有默认值，向后兼容）。
- `failure_memory.py`: `_extract_keywords` 保留不变，`_extract_memory_top_terms` 是新增方法。
- `failure_memory.py`: `store` 方法签名新增 `memory_top_terms` 可选参数。

### 向后兼容性
- 旧 `FailureMemoryRecord` JSON 缺少新字段 → 默认空字符串。
- 旧 `query(label, query_keywords)` 调用 → `memory_top_terms=""`，自动降级为二维检索。
- 现有 44 个 FM 测试不受影响。
