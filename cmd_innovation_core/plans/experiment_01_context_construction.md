# EXPERIMENT 1: Context Construction 5-Mode Comparison

**日期**: 2026-05-23 (Decision 34 R7 修订)
**状态**: 实验设计完成；样本量提升至 80 cases；新增 `corrected_only_padded` token-control；待 80 ECS researcher inspection 后执行
**论文角色**: C2 类证据（上下文拼接有效性），如显著则构成独立 novelty

---

## 1. 实验目标

回答一个问题：**Future Failure Memory 检索时，注入 `wrong_memory + cause + corrected_memory + repair_guidance`（对比模式）是否比仅注入 `corrected_memory + repair_guidance`（纠正模式）更能提升 LLM 的任务表现？**

当前 V0/V1 采用纠正模式。如果对比模式更好，V2 应将其加入 Failure Memory context 构建；如果无差异或更差，V0/V1 的纠正模式已足够。

## 2. 实验设计

### 2.1 被试内设计

每个 Case = 同一 `query` × 5 种 `context` mode。5 次 LLM 调用仅在 context 块不同，其余完全一致。

### 2.2 五种 Context Mode (Decision 34 R7)

| Mode | 注入内容 | 角色 |
|------|---------|------|
| `none` | 无 Failure Memory，仅 query | 基线（验证该 case 确实需要 FM） |
| `full_trace` | `wrong_memory`（baseline 的错误上下文） | 反模式对照（验证注入错误记忆有害） |
| `corrected_only` | `corrected_memory + repair_guidance` | CMD V0/V1 当前策略 |
| `corrected_only_padded` | `corrected_memory + repair_guidance` + neutral filler tokens（padding 至 contrastive 字符长度） | **Token-control** — 排除 "更多 tokens 自然提升 attention" 解释 |
| `contrastive` | `wrong_memory + cause + corrected_memory + repair_guidance` | V2 候选策略 |

Neutral filler 内容: 通用且与 query 无关的 placeholder 段落（如 "[The following is unrelated reference material that does not affect the answer.] ..." 重复至目标字符数）。在 §4 prompt 模板中 mode 替换时直接拼接。

### 2.3 Case 准入条件 (Decision 34 R7)

**`none` 模式必须可靠失败。** 单次 LLM 调用因 temperature=0 variance 仍可能不可靠。
- 用所选 LLM + `none` mode context 跑 **3 次独立调用**（fixed `seed`, identical prompt; ollama / API 重启 process 之间隔 10s 防止 KV cache 复用）。
- 若 ≥1 次调用 produces correct answer → 排除该 case (lenient 排除，宁可丢弃 borderline cases 也不留高方差 noise)。
- 若 3 次全部 fail → 该 case 进入实验。

3-trial pre-check 平均增加 240 LLM 调用（80 cases × 3）至总成本，仍低于 paper sprint 的成本预算。

## 3. 数据集格式

### 3.1 5-Mode Context Case Schema

```json
{
  "case_id": "ctx_v0_retrieval_001",
  "query": "Which city was chosen for the Q3 offsite meeting?",
  "gold_answer": "Lisbon",
  "gold_evidence_phrases": ["Lisbon", "Q3 offsite", "chosen by the committee"],
  "failure_type": "retrieval_error",
  
  "wrong_memory": "The Q3 budget was approved for marketing initiatives across Europe by the finance committee.",
  "cause": "retrieved context did not include the correct memory item even though the item was present in extracted memory",
  "corrected_memory": "Lisbon was selected for the Q3 offsite meeting by the planning committee.",
  "repair_guidance": "update retrieval routing to include the corrected memory item",
  
  "contexts": {
    "none": "Query: Which city was chosen for the Q3 offsite meeting?",
    "full_trace": "[Past Failure Trace 1]\nThe Q3 budget was approved for marketing initiatives across Europe by the finance committee.\n\nQuery: Which city was chosen for the Q3 offsite meeting?",
    "corrected_only": "[Failure Memory Guidance 1]\nCorrected: Lisbon was selected for the Q3 offsite meeting by the planning committee.\nGuidance: update retrieval routing to include the corrected memory item\n\nQuery: Which city was chosen for the Q3 offsite meeting?",
    "corrected_only_padded": "[Failure Memory Guidance 1]\nCorrected: Lisbon was selected for the Q3 offsite meeting by the planning committee.\nGuidance: update retrieval routing to include the corrected memory item\n[The following is unrelated reference material that does not affect the answer.] ...\n\nQuery: Which city was chosen for the Q3 offsite meeting?",
    "contrastive": "[Failure Memory Guidance 1]\nPreviously wrong: The Q3 budget was approved for marketing initiatives across Europe by the finance committee.\nCause: retrieved context did not include the correct memory item even though the item was present in extracted memory.\nCorrected: Lisbon was selected for the Q3 offsite meeting by the planning committee.\nGuidance: update retrieval routing to include the corrected memory item\n\nQuery: Which city was chosen for the Q3 offsite meeting?"
  }
}
```

### 3.2 Case 构造来源

```
CMD Probe Case (experiment 2 产物)
  → CMD-Audit pipeline
  → ECS record
  → Review ECS quality (cause/corrected_memory/repair_guidance 是否准确)
  → Extract: wrong_memory (from case.baseline.injected_context)
             cause (from ecs.cause)
             corrected_memory (from ecs.corrected_memory)
             repair_guidance (from ecs.repair_guidance)
  → Render 5 个 context prompt 字符串
  → 构建 5-Mode Context Case
```

### 3.3 样本量 (Decision 34 R7 + R10)

| 层级 | V1.0 (596 corpus state) | V1.1 (full corpus, post-0035) | 说明 |
|------|--------|--------|------|
| Error type 覆盖 | 4 种固定 | 同 | `retrieval_error`, `compression_error`, `premature_extraction_error`, `reasoning_error` |
| 每 type case 数 | 20 | 20 (re-sampled from full pool) | McNemar's test detectable effect ≈ 15pp at α=0.05, β=0.2 per label |
| 总计 | **80** | **80 (re-sampled)** | 4-label 合并后 N=80 reduces detectable effect to ~10pp |
| 模式数 | **5** (新增 `corrected_only_padded`) | 同 | — |
| LLM 调用 | 80×5 + 80×3 + 80×5 ≈ 1040 | 同 | 总 ~$3 在 gpt-4o-mini 价位 |

**ECS records**: 80 cases 的 ECS records (`cause`, `corrected_memory`, `repair_guidance`) 在 mode 渲染前由 researcher 手动检视/编辑 (Decision 34 R7)。检视记录: `data/probe_cases/experiment_01_inspected_ecs.json`，per-case `(case_id, original_ecs, edited_ecs, edit_reason)`。研究人员预计 ~5 hours per V1.0 / V1.1 round.

## 4. LLM 选择与协议

### 4.1 LLM 选择

| 要求 | 说明 |
|------|------|
| 主流模型 | 至少 1 个，理想 2 个（如 GPT-4o + Claude Sonnet 4.6），用于验证跨模型一致性 |
| 版本锁定 | 全实验过程固定 model version，不升级 |
| 上下文窗口 | 所有 5-mode context 的最长者（contrastive 或 padded）必须 < context window。实测后检查：`max(len(tok(ctx)) for ctx in case.contexts.values())` < `LLM.context_window` |
| API | 统一用各 provider 的 chat completions API |

### 4.2 调用参数

| 参数 | 值 | 理由 |
|------|-----|------|
| `temperature` | `0` | 确保可复现 |
| `max_tokens` | `100` | 答案应为短句，限制防止冗长解释 |
| `top_p` | `1` | 不采样变体 |
| `seed` | 固定值（如 `42`） | 跨运行可复现 |

### 4.3 System Prompt

全实验统一：

```
Answer the question based ONLY on the provided context. Be concise. Answer in one short phrase or sentence. Do not explain your answer.
```

### 4.4 单次 LLM 调用的完整 Prompt 模板

```
[System: Answer the question based ONLY on the provided context. Be concise. Answer in one short phrase or sentence. Do not explain your answer.]

{contexts[mode]}

Answer:
```

5 次调用仅替换 `{contexts[mode]}` 为 `none`/`full_trace`/`corrected_only`/`corrected_only_padded`/`contrastive` 对应的预渲染字符串。

## 5. 执行流程

### 5.1 单 Case 流程

```
For each case in ContextCases:
  1. Pre-check: run LLM with contexts["none"] 3 times → if any answer == gold_answer → exclude case
  2. For each mode in [none, full_trace, corrected_only, corrected_only_padded, contrastive]:
     a. Build prompt from template
     b. Call LLM → record answer + token usage
  3. Evaluate each answer vs gold_answer
  4. Record row in results table
```

### 5.2 调用量估算

```
80 cases × 5 modes = 400 LLM 调用（答案生成）
额外：80 cases × 3 `none` pre-check = 240 调用
额外：每 case × mode 可选 1 次 LLM judge（语义匹配） = 400 调用
总计: ~640 核心调用，~1040 含 optional judge

成本（GPT-4o，~300 tokens/调用，2026年5月价格）:
  仍为低成本实验；具体成本以最终 evaluator model 为准
```

## 6. 评估指标

### 6.1 主指标

| 指标 | 测量方法 | 聚合方式 |
|------|---------|---------|
| **Exact Match** | `llm_answer.strip().casefold() == gold_answer.strip().casefold()` | 每 mode 的 EM rate |
| **Contains Gold** | `gold_answer.casefold() in llm_answer.casefold()` | 每 mode 的包含率 |
| **LLM Judge Semantic Match** | `"Is '{llm_answer}' semantically equivalent to '{gold_answer}'?" → yes/no`（独立 LLM 调用） | 每 mode 的语义匹配率 |

### 6.2 辅助指标

| 指标 | 测量方法 |
|------|---------|
| **Token Cost** | API response `usage.total_tokens`，每 mode 总 token 量 |
| **Hallucination Rate** | 答案中的实体/事实短语是否出现在 injected context 中（`evidence_recall_from_text` 反向检查） |
| **Refusal Rate** | 答案是否为 "I don't know" / "not provided" 等拒绝模式 |

### 6.3 对比指标 (Decision 34 R7)

```
主要对比 1 (token-uncontrolled, 与文献对照):
  Δ_1 = EM(contrastive) - EM(corrected_only)
主要对比 2 (token-controlled, 因果分离):
  Δ_2 = EM(contrastive) - EM(corrected_only_padded)
  
辅助对比:
  Δ_pad = EM(corrected_only_padded) - EM(corrected_only)
```

McNemar's test on Δ_1 和 Δ_2 各跑一次。Bootstrap CI (1000-iter case-level) on EM rates per mode.

**结果判读规则**:

| Δ_1 | Δ_2 | 结论 |
|-----|-----|------|
| > 0 显著 | > 0 显著 | 对比模式有效，且效果不来自 token 数 → V2 加入 + paper claim |
| > 0 显著 | ≈ 0 不显著 | "改进"是 token 数自然结果 → V2 不加入；paper 报告该发现 (warning to community) |
| ≈ 0 | ≈ 0 | 对比模式无增益 → V0/V1 corrected_only 已足够 + paper claim |
| < 0 | — | 对比模式有害 → V2 不加入 + negative result paper claim |

Per-label 分析: 在 4 labels 上分别报告 Δ_1 + Δ_2 + bootstrap CI。

## 7. 结果分析

### 7.1 输出表结构

```csv
case_id,failure_type,none_em,full_trace_em,corrected_only_em,corrected_only_padded_em,contrastive_em,none_tokens,full_trace_tokens,corrected_only_tokens,corrected_only_padded_tokens,contrastive_tokens,delta_1,delta_2,delta_pad
```

### 7.2 统计检验

| 检验 | 问的问题 |
|------|---------|
| McNemar's test | Δ_1 (contrastive vs corrected_only) 和 Δ_2 (contrastive vs corrected_only_padded) 的 EM 差异是否显著 |
| Per-label 均值比较 | 哪种 error type 从对比模式中获益最多 |
| Token cost comparison | 对比模式比纠正模式多花多少 token |

### 7.3 Novelty 定位

40+ 篇论文中无一篇提供 `wrong_memory + cause + corrected_memory` vs `corrected_memory only` 的对照实验。无论结果方向如何，实验一本身就是新贡献：

- 正结果：对比模式更优 → V2 加入 + paper claim
- 负结果：纠正模式已足够 → V0/V1 设计得到实证验证 + paper claim（"context construction 无需复杂度"）

## 8. 风险与缓解

| 风险 | 缓解 |
|------|------|
| LLM 答案格式不稳定（多出解释） | output format prompt 约束 + post-processing 截取首句 |
| 小样本无法得出结论 | 先跑 10-case 最小模板，看趋势方向，再决定是否扩展 |
| contrastive context 超出 context window | 构建前计算 contrastive 模式的 token 数，排除超长 case |
| none 模式不失败 | 预检查排除，替换同类型其他 case |
| 不同 LLM 结论不一致 | 这是有效发现而非 bug——论文中报告跨模型差异，增强生态有效性 |

## 9. Decision 34 R7 Compliance Checklist

每次 experiment 跑完前确认:

- [ ] 80 ECS records 已 researcher inspected, edits in `data/probe_cases/experiment_01_inspected_ecs.json`
- [ ] 5 modes rendered, `corrected_only_padded` token 数 == `contrastive` token 数 (±5 token tolerance)
- [ ] 80 cases 全部通过 3-trial `none`-mode pre-check
- [ ] LLM model + version locked, `temperature=0`, `seed=42`
- [ ] Bootstrap CI (1000-iter) 在 EM rates / Δ_1 / Δ_2 上报告
- [ ] McNemar's test on Δ_1 + Δ_2 均报告
- [ ] 4-label per-error-type 分析报告
- [ ] 输出 `artifacts/experiment_01_results.csv` schema includes `mode_token_count`, `corrected_only_padded_em`, `mcnemar_p_delta_1`, `mcnemar_p_delta_2`, bootstrap CI columns
- [ ] V1.1 trigger: re-run after issue 0035 with full-corpus 80-case sample
