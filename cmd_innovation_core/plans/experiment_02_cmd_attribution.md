# EXPERIMENT 2: CMD Attribution Effectiveness

**日期**: 2026-05-24 (Decision 34 R1-R11 修订)
**状态**: 设计完成，data 准备 60% — 596 LLM-annotated cases on disk; 130 researcher-adjudicated subset 待生成 (target 05-30~01, LLM-A 辅助); LLM eval infra 待接通 (target 05-25~28); 无 hook 路径 confirmed.
**论文角色**: 主结论 — V1.0 arxiv preprint 头条 Macro F1 + bootstrap CI 报告基于 130 researcher-adjudicated cases。596-case 作为 scale sanity check。V1.1 venue submission 全量数据集重跑 (issue 0035 trigger)。

---

## 1. 实验目标

回答一个问题：**CMD 能否在已知 ground-truth perturbation label 的情况下，通过 counterfactual replay 正确识别出是哪个 memory operation 导致的失败？**

核心指标：CMD 的归因 macro F1 是否显著优于 evidence-recall heuristic、subagent_judge、LLM-as-judge、random label 四个 baseline。

## 1.5 Evaluation Set Definition (Decision 34 R4+R11)

**Two-tier evaluation, not single-set**:

| Tier | Set | Size | Label Source | Role | Headline Eligible |
|------|-----|------|--------------|------|--------------------|
| Headline | researcher-adjudicated subset | 130 (V1.0) / 130 re-sampled (V1.1) | researcher hand-labeled with LLM-A (`llama-3.3-70b-instruct`) candidate suggestion + accept/reject + 20-case blind spot-check | "CMD Macro F1 = X [95% CI ...]" main claim | YES — high+medium confidence subset |
| Scale | full real-data suite | 596 (V1.0) / full corpus (V1.1, post-0035) | deepseek-v4-pro-max LLM annotator | "CMD reproduces annotator labels at Macro F1 = Y across N" sanity | NO — supplementary only |

**Stratified sampling for headline (130 cases)**:
- 8 active labels: write_error, compression_error, premature_extraction_error, retrieval_error, injection_error, reasoning_error, route_error, ingestion_error.
- ~16 per label = 128 + 2 spare = 130 target.
- `random_state=42`. Persist sampled `case_id`s to `data/probe_cases/researcher_labeled_subset.json`.

**LLM-A-assisted labeling protocol (Decision 34 R11)**:
1. LLM-A (`llama-3.3-70b-instruct`) emits `(suggested_label, rationale)` per case.
2. Researcher reads case + suggestion + rationale; assigns `final_label`, `confidence`.
3. Records: `(case_id, deepseek_label, llm_a_suggestion, llm_a_rationale, researcher_label, confidence, disagreement_with_deepseek, disagreement_with_llm_a, researcher_notes)`.
4. **Spot-check**: First 20 cases labeled blind (no LLM-A). Same 20 re-labeled with LLM-A after assisted pass. κ(researcher_blind, researcher_assisted) reported as automation-bias measurement.
5. If κ < 0.7, redo entire pass without LLM-A.

**Three-independent-LLMs rule**: deepseek-v4-pro-max (annotator) ≠ qwen2.5-7b (agent_generate) ≠ llama-3.3-70b-instruct (LLM-A) ≠ evaluator scorer (TBD, family-disjoint).

**Confidence scale**:
- **high**: clear from trace which label applies; headline.
- **medium**: probable, weak alternative support; headline + sensitivity analysis.
- **low**: ambiguous; appendix only.

**Headline reporting**:
- Primary: high+medium subset (≈ N=110-115 expected) Macro F1 + bootstrap CI [95%].
- Sensitivity: same metrics on all N=130 (low included).
- Per-label F1 with bootstrap CI per cell.
- Per-source split via heatmap (LongMemEval / MemoryArena / ToolBench × 8 labels).
- AttributionFailed coverage% reported alongside Macro F1 on attributed (two-tier framing per R8/Q13).
- Cohen's κ between researcher and deepseek labels reported as methods artifact with bootstrap CI.

**deepseek labeling provenance (R4-prov)**: prompt + script must be checked into `scripts/annotate_perturbation_labels.py` and referenced in `data/cleaned_cases/cleaning_report.txt` before headline runs.

## 2. 实验装置总览

```
┌─────────────────────────────────────────────────────┐
│                  Probe Case                         │
│  query + extracted_memory + gold_answer             │
│  + gold_evidence + perturbation_type (注入)          │
│  + expected_behavior                                │
└──────────────────┬──────────────────────────────────┘
                   ↓
┌──────────────────────────────────────────────────────┐
│            Baseline Memory System                    │
│  (fixed-summary → 或 vector → BM25 retrieval)        │
│  → baseline_answer + baseline_evidence_score         │
└──────────────────┬───────────────────────────────────┘
                   ↓
┌──────────────────────────────────────────────────────┐
│             4 Comparator Baselines                   │
│  Random Label / Evidence-Recall Heuristic            │
│  / Subagent Judge / LLM-as-Judge                    │
│  → 各自输出 predicted_label                          │
└──────────────────┬───────────────────────────────────┘
                   ↓
┌──────────────────────────────────────────────────────┐
│              CMD-Audit Harness                        │
│                                                      │
│  1. Subagent Judge Monitor (anomaly detection)       │
│  2. V0 Replay Portfolio (6 interventions):           │
│     a. Oracle Write                                  │
│     b. Oracle Compression                            │
│     c. Verbatim Event Oracle                         │
│     d. Oracle Retrieval                              │
│     e. Injection-Oracle                              │
│     f. Evidence-Given Reasoning                      │
│  3. Recovery Gain Δk = score(ŷ_k, gold) - baseline   │
│  4. Attribution: label = argmax(Δk)                  │
│  5. Top-2: second largest Δk label                   │
└──────────────────┬───────────────────────────────────┘
                   ↓
┌──────────────────────────────────────────────────────┐
│            Evaluation                                │
│  predicted_label vs perturbation_type (ground truth) │
│  → Confusion Matrix                                  │
│  → Macro F1, Top-2 Accuracy                          │
│  → CMD vs 4 baselines comparison                     │
│  → Diagnosis Cost (replays triggered)                │
└──────────────────────────────────────────────────────┘
```

**Decision 34 changes to this装置 (2026-05-23/24)**:
- Probe Case row: drawn from 130 researcher-adjudicated subset (LLM-A assisted + 20-case blind spot-check), not 596.
- Baseline row: `agent_generate` runs on `(query, vector_memory.injected_context)` per case at re-test time; same independent scorer. Pre-baked `baseline.evidence_score` bypassed.
- 4 Comparator Baselines: random / evidence_recall / subagent_judge / llm_judge (issue 0019 Phase A).
- CMD-Audit Harness:
  - Subagent Judge Monitor: bypassed for headline; trigger replays for all 130 cases unconditionally.
  - V0 Replay Portfolio extended to V1 10-replay portfolio (`run_case_full_v1`); hook bypassed (R5).
  - Recovery Gain: `Δk = scorer(gold_evidence, agent_generate(query, baseline + evidence_block)) - scorer(gold_evidence, baseline_answer_llm)` — both terms LLM-scored.
  - Attribution: `tie_margin = 0.0` for headline argmax (R3).
  - RepairAction emission (`repairs.py:563-672`) records per-case `(action_type, target_item_id, target_store)` for repair-depth descriptive measurement (R8/Q12).
- Evaluation:
  - Confusion matrix vs researcher labels (high+medium confidence) with bootstrap CI per cell.
  - Macro F1 + Top-2 Accuracy + per-label F1, all with bootstrap [95% CI].
  - CMD vs 4 baselines (cost/latency in headline column per R10).
  - Per-source heatmap with CIs.
  - Two-evaluator robustness on 130 (R10/Q19): per-evaluator Macro F1 + agreement.
  - Cohen's κ (researcher↔deepseek, researcher_blind↔researcher_assisted) as methods artifacts.
  - Two-tier coverage% + Macro F1 on attributed (R8/Q13).
  - 11-label supplementary architecture-completeness note (synthetic granularity/graph/safety probe cases acknowledged but not quantitatively claimed).

## 3. Probe Case 数据集

### 3.1 Schema

```json
{
  "case_id": "cmd_probe_001",
  "query": "Which city was chosen for the Q3 offsite meeting?",
  "history": [
    "Event 1: The planning committee discussed several locations for the Q3 offsite, including Barcelona, Lisbon, and Prague.",
    "Event 2: After deliberation, Lisbon was selected by a 4-2 vote."
  ],
  "extracted_memory": [
    {
      "memory_id": "mem_001",
      "text": "Q3 offsite locations were discussed. Barcelona, Lisbon, and Prague were considered.",
      "source_event_ids": ["evt_001"]
    },
    {
      "memory_id": "mem_002",
      "text": "Budget for Q3 was approved at €50K.",
      "source_event_ids": ["evt_003"]
    }
  ],
  "gold_answer": "Lisbon",
  "gold_evidence_units": [
    {
      "evidence_id": "ev_001",
      "text": "Lisbon was selected for the Q3 offsite meeting by the planning committee.",
      "source": "raw_event",
      "source_event_id": "evt_002",
      "required_phrases": ["Lisbon", "selected", "Q3 offsite"]
    }
  ],
  "perturbation_type": "compression_error",
  "expected_behavior": {
    "baseline_fails": true,
    "oracle_write_recovers": true,
    "oracle_compression_recovers": true,
    "oracle_retrieval_does_not_recover": true,
    "verbatim_event_oracle_recovers": true,
    "injection_oracle_does_not_recover": false,
    "evidence_given_reasoning_recovers": false
  }
}
```

### 3.2 关键设计原则

**`perturbation_type` 是注入的 ground truth，不是猜测。**

构造流程：
```
1. 创建干净的原始事件 + 正确的 memory extraction
2. 选择一个 target label（如 compression_error）
3. 对目标操作进行有控制的破坏：
   - compression_error: 对 mem_001 进行模拟压缩，删除关键 evidence 短语
   - retrieval_error: mem_001 含正确内容，但 BM25 检索返回了 mem_002
   - write_error: 从 extracted_memory 中完全删除 mem_001
   - premature_extraction_error: 在 raw events 中保留证据，但在 extraction 中删除
   - injection_error: mem_001 正确检索但注入格式损坏
   - reasoning_error: 证据正确注入但推理 prompt 故意弱化
4. 设置 expected_behavior：哪些 replay 应该恢复，哪些不应该
5. 验证：用 expected_behavior 跑基线，确保 baseline 确实失败
```

### 3.3 每条 Label 的变体设计

同一 label 需要多种失败变体，防止模型记忆 pattern：

| Label | 变体 A | 变体 B | 变体 C |
|-------|--------|--------|--------|
| `write_error` | 证据条目从未写入 | 写入被截断 | 证据从源事件遗漏 |
| `compression_error` | 实体名丢失 | 关系丢失 | 时间信息丢失 |
| `premature_extraction_error` | 抽取丢弃了关键参与者 | 抽取丢弃了决策结果 | 抽取保留表面信息丢失因果链 |
| `retrieval_error` | BM25 漏检 | 语义相近干扰 | 多跳证据分散在不同条目 |
| `injection_error` | 证据位置不对 | 格式破坏 | 证据被噪声埋没 |
| `reasoning_error` | 结论选错证据条目 | 时间顺序推错 | 忽略了一条关键约束 |

### 3.4 样本量 (Decision 34 R4+R10)

| 阶段 | 总量 | 每 label | 用途 |
|------|------|---------|------|
| Smoke (V0 已完成) | 6 | 1 | Pipeline 验证，仅 mechanics |
| Headline adjudicated | 130 (V1.0) / 130 re-sampled (V1.1) | ~16 × 8 active labels | Paper 主实验；researcher labels high+medium confidence + LLM-A blind spot-check |
| Scale sanity | 596 (V1.0) / full corpus (V1.1) | deepseek distribution | Supplementary sanity check；不作为 headline |

The 596 cases remain valuable for stress-testing the full pipeline, hook calibration labels, and scale sanity reporting. They do not define the headline Macro F1 claim unless the relevant subset is researcher-adjudicated.

## 4. Baseline Memory Systems

### 4.1 V0 Baselines

CMD 实验不比较不同 memory system——它比较不同**归因方法**。baseline memory system 是固定的：

| Baseline | 实现 | 用处 |
|----------|------|------|
| **Fixed-Summary Memory** | 对 raw events 进行固定长度摘要 → 存储为 memory unit → 用简单关键词检索 | 提供 baseline 答案和 evidence score |
| **Vector Memory** | raw events → embedding → 存储为 memory unit → 用 cosine similarity 检索 top-k | 第二个 memory 实现，验证跨 memory 一致性 |

两个都已在 V0 issue 0002 中实现。

### 4.2 V0.5 Retrievers (issue 0008)

| Retriever | 类型 | 特点 |
|-----------|------|------|
| **BM25** | 词法 | 弱 retriever，易漏检，用于测试 `retrieval_error` |
| ~~HybridRerank~~ | ~~混合~~ | ~~已移除：BM25 + TF-IDF cosine 均为稀疏词袋模型，无法提供真正的语义恢复。强检索移至 V1。~~ |

两个都是确定性、纯 Python 实现（BM25），排序时盲于 gold evidence。

### 4.3 V1 Adapter 集成目标

V1 不是把 mem0/Letta 当作 baseline，而是把 CMD-Audit **嫁接到真实 agent 的 memory API 上**，用 recorded-trace integration mode：

```
mem0.add(messages, user_id=uid)
  → CMD intercept: 记录 raw_messages, extracted_memory
  → 如果 add 时证据丢失 → Oracle Write replay → write_error

mem0.search(query, user_id=uid)
  → CMD intercept: 记录 retrieved_memory_items + search params
  → 如果 search 未召回 → Oracle Retrieval replay → retrieval_error
```

| 系统 | 集成点 | 对 CMD 的价值 |
|------|--------|-------------|
| **mem0** (55k stars) | `add()` 拦截 → write-side replays；`search()` 拦截 → retrieval-side replays | V1 第一目标。扁平存储，覆盖 6-8 label。不测试 `route_error` |
| **Letta** (22.6k stars) | core/archival/recall tier interception | V1 第二目标。tiered storage 可测试 `route_error`。V1→V2 gate 需要 ≥2 agents |
| **MemOS** | 候选验证点 | 如有特殊 write/compress 机制可额外验证 |
| **EveryMem** | 候选验证点 | 如有特殊 retrieval/injection 机制可额外验证 |

**mem0 CMD Replay 映射**:

| mem0 操作 | CMD Counterfactual Replay | 诊断 Label |
|-----------|--------------------------|-----------|
| `add()` 返回成功但证据未存储 | Oracle Write | `write_error` |
| ADD 提取过程丢失关键细节 | Oracle Compression | `compression_error` |
| 原始 messages 含证据但 ADD 未提取 | Verbatim Event Oracle | `premature_extraction_error` |
| `search()` 未召回正确记忆 | Oracle Retrieval | `retrieval_error` |
| 记忆被检索但注入格式/顺序错误 | Injection-Oracle | `injection_error` |
| 记忆正确注入但 agent 最终推理错误 | Evidence-Given Reasoning | `reasoning_error` |

## 5. Comparator Baselines（归因方法对比）

CMD 的 baseline 是**不同的归因方法**，不是不同的 memory system：

### 5.1 Random Label

```python
def random_label_baseline():
    return random.choice(V0_PIPELINE_LABEL_ORDER)
```

用途：信息论下界。任何归因方法必须显著优于随机。

### 5.2 Evidence-Recall Heuristic

```python
def evidence_recall_heuristic(case):
    # 检查 extracted_memory 中哪个条目含 gold evidence 短语
    for mem_item in case.extracted_memory:
        if evidence_recall_from_text(case.gold_evidence, mem_item.text) > 0:
            # 有至少一个 memory item 含证据
            return "retrieval_error"  # 证据存在但 baseline 没用到
    # 所有 memory items 都不含证据
    return "write_error"  # 证据从未写入
```

用途：简单规则基线。如果 CMD 不能显著优于这个简单规则，反事实回放就没有价值。

### 5.3 LLM-as-Judge

```python
def llm_judge_baseline(case, baseline_output):
    # LLMJudgeBaseline sees ONLY observable post-hoc trace artifacts.
    # NO gold_answer, gold_label, ptype, gold_evidence, or oracle information.
    prompt = f"""A memory-augmented agent was asked: "{case.query}"

The agent's memory contained:
{format_memory_items(case.extracted_memory)}

The agent retrieved:
{format_memory_items([m for m in case.extracted_memory if m.memory_id in baseline_output.retrieved_memory_ids])}

The agent's answer was: "{baseline_output.answer}"

Which memory pipeline operation most likely failed?
Options: write_error, compression_error, premature_extraction_error,
         retrieval_error, injection_error, reasoning_error

Answer with only one label. Do not explain."""
    
    return llm_call(prompt, temperature=0)
```

用途：测试"看 trace + 答案"是否已经能解决归因问题。CMD 必须证明 counterfactual replay 比 post-hoc trace 解释更准确。PrefixGuard (2605.06455) 已验证 LLM judge 在 online warning 场景下弱于训练后的 monitor——CMD 在归因场景下也需要类似的对照。

## 6. CMD-Audit 执行流程

### 6.1 单 Case 完整流程

```
1. LOAD: ProbeCase (query, memory, gold_answer, gold_evidence, perturbation)
2. BASELINE:
   a. Run baseline memory → retrieve context
   b. agent_generate(query, baseline_context) → baseline_answer_llm
   c. evidence_scorer(gold_evidence, baseline_answer_llm) → baseline_evidence_score_llm
3. HEADLINE BYPASS:
   a. Ignore hook / Subagent Judge Monitor for the headline run
   b. Run all 10 V1 replays for every adjudicated case
4. COUNTERFACTUAL REPLAY (10 replays):
   For each V1 replay:
     a. Apply intervention to memory/context
     b. agent_generate(query, baseline + evidence_block)
     c. evidence_scorer(gold_evidence, replay_answer)
     d. Compute Δk = replay_evidence_score - baseline_evidence_score_llm
5. ATTRIBUTION:
   a. label = argmax(Δk) over all replays with Δk > 0 and tie_margin=0.0
   b. top2_label = second largest Δk (if exists)
   c. If all Δk <= 0 → label = None (failure not attributable to pipeline)
6. COMPARISON:
   a. Run Random Label → predicted_label
   b. Run Evidence-Recall Heuristic → predicted_label
   c. Run Subagent Judge → predicted_label
   d. Run LLM-as-Judge → predicted_label
7. ECS:
   a. Generate ECS draft from predicted_label + replay evidence
8. POST-REPAIR CONTEXT REPLAY:
   a. Build repaired context from ECS
   b. Re-run original query → post_repair_answer_score
   c. Output repair_assessment: recovered / partial / failed
9. OUTPUT: AuditResult (attribution, deltas, comparators, ECS, repair)
```

### 6.2 Recovery Gain 计算

```
Δk = Metric(ŷ_k, y) - Metric(ŷ, y)

其中:
  ŷ_k = Agent(query, Intervention_k(Memory, History))  反事实答案
  ŷ   = Agent(query, Baseline(Memory, History))        原始失败答案
  y   = gold_evidence facts                              金标准证据

Metric = independent evidence scorer over the agent answer
```

### 6.3 Attribution 规则

```
label = argmax_k(Δk)

tie-breaking:
  - 如果 max(Δk) <= 0: label = None (无可恢复的失败)
  - headline argmax uses `tie_margin = 0.0`
  - top-2 / close_deltas recorded for analysis only
  - coupled-failure tie_margin calibration moves to a 30-50 case supplementary subset
```

## 7. 评估指标

### 7.1 归因指标

| 指标 | 计算 | 含义 |
|------|------|------|
| **Macro F1** | per-label F1 均值 + bootstrap CI | 主指标：8-label headline；11-label supplementary architecture-completeness note |
| **Attribution Accuracy** | 正确归因的 case / 总 case | 简单准确率 |
| **Top-2 Accuracy** | 正确 label 在 top-2 中的比例 | 耦合失败的容错指标 |
| **Confusion Matrix** | predicted vs actual 的 8×8 headline 或 11×11 supplementary | 诊断哪些 label 对易混淆 |
| **Diagnosis Cost** | tokens / wallclock_sec / usd per case, agent + scorer + verifier subtotals | headline 表内效率指标；hook cost reduction 另作 supplementary |

### 7.2 对比指标

```
CMD vs baseline 必须满足:
  CMD macro_f1 > evidence_recall_heuristic
  CMD macro_f1 > subagent_judge
  CMD macro_f1 > llm_judge
  CMD macro_f1 > random_label
  CMD top2_accuracy > all baselines
  CMD attribution_accuracy > all baselines
```

### 7.3 证据链指标

| 指标 | 来源 | 用途 |
|------|------|------|
| Recovered Rate | Post-Repair Context Replay | CMD 归因 → 修复是否有效 |
| Partial Rate | Post-Repair Context Replay | 耦合失败的暴露程度 |
| Repair Regression Rate | Post-Repair Context Replay | 修复是否引入新错误 |

## 8. 实验规模与成本

### 8.1 Headline adjudicated run (Decision 34 R4+R10)

```
Case 数: 130 researcher-adjudicated (V1.0) / 130 re-sampled from full corpus (V1.1)
Per case: 1 baseline LLM rescore + 10 replays + 4 comparators
Hook: bypassed for headline; all 10 replays run
Scoring: qwen2.5-7b agent + independent evaluator scorer
Attribution: tie_margin=0.0 headline argmax
Uncertainty: bootstrap CI (1000-iter case-level resample) on Macro F1 + top-2 + per-label F1 + κ
```

### 8.2 Scale sanity run (Decision 34 R4+R10)

```
Case 数: 596 LLM-annotated real-data cases (V1.0) / full corpus (V1.1, post-0035)
Per case: all 10 replays under same scorer/agent stack as headline
Role: "CMD reproduces deepseek-v4-pro-max labels at Macro F1 = Y"
Not headline eligible: labels are LLM-generated, not researcher-adjudicated
```

## 9. 结果产出

### 9.1 Artifacts

| Artifact | 内容 |
|----------|------|
| `artifacts/headline_130/attribution_table.csv` | case_id, researcher_label/deepseek_label, predicted_label, top2_label, 10×Δk, comparator_outputs, cost/latency |
| `artifacts/headline_130/attribution_confusion_matrix.csv` | 8×8 headline 混淆矩阵 + bootstrap CI cells |
| `artifacts/headline_130/comparison_metrics.csv` | CMD vs 4 baselines: macro_f1, attribution_accuracy, top2_accuracy, bootstrap CIs |
| `artifacts/headline_130/post_repair_table.csv` | case_id, repair_assessment, pre/post answer/evidence scores, AnswerVerifier verdict |
| `artifacts/at_scale_llm_retest.csv` | 596 V1.0 / full-corpus V1.1 scale sanity raw rows |

### 9.2 Paper Claims

| Claim | 指标 | 阈值 |
|-------|------|------|
| C1: CMD 归因优于启发式方法 | Macro F1 | CMD > baselines on 130 researcher-adjudicated cases |
| C2: 定向修复优于无差别更新 | Recovered rate | targeted > hard_case on adjudicated or inspected repair subset |
| C3: 不同 source 覆盖与失败画像 | per-source heatmap + CI | V1.0 = coverage claim only; V1.1 = explicit generalization |
| C6: Verbatim Event Oracle 减少标签错误 | 混淆矩阵前/后对比 | 加 VEO 后 retrieval_error 的 false positive 降低 |

## 10. 风险与缓解

| 风险 | 缓解 |
|------|------|
| Pre-D34 596-case Macro F1 = 1.000 不可引用为 paper headline | Decision 34: V1.0 596 是 phrase-match + LLM-label sanity snapshot；headline 改用 130 researcher-adjudicated cases；V1.1 post-0035 full corpus 重跑 |
| 某些 label 对高混淆（如 compression vs premature_extraction） | 输出 top-2 + confusion matrix 分析混淆模式。如果混淆率高，考虑合并或重新定义 label 边界 |
| V1 mem0 归因准确率低于 standalone | 检查 adapter 切点是否正确映射。可能是 recorded trace 未捕获关键信号 |
| Letta tiering 使 route_error 诊断复杂 | 先验证 mem0（扁平存储），Letta 作为 harder test case |
| LLM judge baseline 不稳定 | 固定 temperature=0 + 固定 seed + 多次 run 取多数投票 |
