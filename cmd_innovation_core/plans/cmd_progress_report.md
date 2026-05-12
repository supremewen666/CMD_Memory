# CMD 研究进度报告

**项目**: Counterfactual Memory Debugger (CMD) — 面向 LLM Agent 记忆的反事实记忆调试器
**日期**: 2026-05-12
**状态**: V0 完成，V0→V1 gate 等待 probe suite scaling

---

## 1. 项目概述

CMD 是一个反事实回放框架，用于诊断 LLM Agent 的记忆失败究竟来自哪个 memory operation（写入、压缩、抽取、检索、注入、推理），而非仅看最终答案对错。诊断后生成 Error-Cause-Solution (ECS) 记录，指导定向修复，并将修复经验沉淀为 Failure Memory 供未来相似任务检索。

CMD 占据一个已验证的空白：截至 2026-05-12，40+ 篇论文和 10+ 个 GitHub 项目中，**无一实现自动化的反事实记忆操作级归因**。

---

## 2. 整体进度

```
V0 (CMD-Audit standalone)          V1 (Skill Adapter + 11-label)       V2 (Runtime repair loop)
████████████████████████████████    ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░    ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
        已完成                              已规划                           已规划
```

| 阶段 | 状态 | 产出 |
|------|------|------|
| **V0** | 完成 | 6-label 归因 pipeline、ECS、Post-Repair Replay、Failure Memory、version gates |
| **V0→V1 Gate** | 等待中 | 需 probe suite 从 6 个 smoke case 扩展到 50-100 个 |
| **V1** | 已规划 (7 issues) | 5 个新 label、mem0/Letta adapter、真实数据、RPE pre-filter |
| **V2** | 已规划 | 运行时修复闭环、contrastive context mode、Failure Memory skill evolution |

---

## 3. V0 完成清单

### 3.1 Issues（全部完成）

| Issue | 内容 | 测试 |
|-------|------|------|
| 0001 | Probe dataset + gold evidence contract + Oracle Retrieval tracer bullet | ✅ |
| 0002 | Fixed-summary/vector baselines + evidence-recall/subagent-judge/random comparators + leak-safe monitor | ✅ |
| 0003 | 6-replay V0 attribution table（Oracle Write/Compression/Retrieval + Verbatim Event + Injection-Oracle + Evidence-Given Reasoning） | ✅ |
| 0004 | Taxonomy boundary review — V0 6-label 确认 | ✅ |
| 0005 | Post-Repair Context Replay（3-value `repair_assessment`: recovered/partial/failed） | ✅ |
| 0006 | Targeted memory fixes（6 per-label repair actions + repair comparison table） | ✅ |
| 0007 | ECS Failure Memory recurrence（3-mode comparison: none/full_trace/corrected_guidance） | ✅ |
| 0008 | V0.5 retrieval baseline strengthening（BM25 + HybridRerank, 6 hard negatives） | ✅ |
| 0009 | Subagent Judge Monitor contract hardening（enum-locked `anomaly_reason`） | ✅ |
| 0010 | Evidence-driven version gates（V0→V1 4-criteria check, V1→V2 stub, HITL pipeline） | ✅ |

**总测试**: 218 tests pass

### 3.2 Evidence Artifacts

| Artifact | 用途 | 状态 |
|----------|------|------|
| `attribution_table.csv` | 6-replay 归因表 | ✅ |
| `comparison_metrics.csv` | CMD vs baselines 对比 | ✅ |
| `attribution_confusion_matrix.csv` | 混淆矩阵 | ✅ |
| `post_repair_table.csv` | Post-Repair Context Replay | ✅ |
| `repair_success_table.csv` | 定向修复效果 | ✅ |
| `repair_claim_ledger.txt` | 修复声明账本 | ✅ |
| `recurrence_comparison.csv` | Failure Memory 复发对比 | ✅ |
| `V0V1_gate_status.txt` | V0→V1 gate 检查 | ✅ |

### 3.3 V0→V1 Gate 状态

| 准则 | 阈值 | 当前值 (6 smoke cases) | 结果 |
|------|------|----------------------|------|
| Macro F1 > baselines | CMD > evidence_recall, subagent_judge, random | CMD=1.000, baselines≤0.833 | PASS |
| Confusion matrix diagonal dominance | diagonal > off-diagonal per label | 6/6 diagonal=1, off-diagonal=0 | PASS |
| Attribution + top-2 accuracy | CMD > all baselines | CMD=1.000, best baseline=0.833 | PASS |
| Repair assessment distribution | recovered ≥ 50%, recovered+partial > failed | 6/6 recovered | PASS |

**注意**: 以上全部在 6 个 smoke case（每 label 1 个）上通过。扩展到 50-100 case 后预计会回落，需 HITL review 确认阈值仍然成立。

---

## 4. V1 规划（2026-05-11 完成规划）

### 4.1 V1 Issues

| Issue | 内容 | 依赖 | 状态 |
|-------|------|------|------|
| 0011 | `ingestion_error` + `route_error` 标签扩展 | 0010 + probe scaling | AFK |
| 0012 | `granularity_error` + `graph_error` + `safety_error` 标签扩展 | 0011 | AFK |
| 0013 | 11-label coupled-failure recalibration + memory-probe baseline | 0012 | AFK |
| 0014 | mem0 adapter 集成（第一个真实 agent 目标） | 0013 | AFK |
| 0015 | Letta adapter 集成 + V1→V2 gate | 0014 | AFK |
| 0016 | LoCoMo/LongMemEval 真实数据 probe cases | 0012 | AFK |
| 0017 | RPE pre-filter 优化 | 0015 | AFK |

### 4.2 V1 关键设计决策

| 决策 | 内容 |
|------|------|
| 标签扩展顺序 | pipeline labels 优先: `ingestion_error` → `route_error` → `granularity_error` → `graph_error` → `safety_error`。bad memory item labels 延后到 V2 |
| 第一个 Adapter 目标 | **mem0** (55k stars, YC S24)，最简洁 memory API (`add()`/`search()`)，SOTA on LoCoMo/LongMemEval |
| 第二个 Adapter 目标 | **Letta** (22.6k stars)，core/archival/recall tiering，可测试 mem0 扁平存储无法覆盖的 `route_error` |
| 论文范围 | V0 + V1 + V2 共同构成一篇论文，V2 为最终 module/skill |
| RPE pre-filter | V1 后期优化，非 gate 前提 |
| 真实数据 | LoCoMo/LongMemEval 混入 V1 probe，数据构建由研究者负责 |

---

## 5. Prototype 状态

| Prototype | 语言 | 状态 |
|-----------|------|------|
| CMD Probe Logic | EN + ZH | ✅ |
| Post-Repair Assessment & Monitor Contract | EN + ZH | ✅ |
| RPE Monitor Pre-Filter | EN + ZH | ✅ |
| mem0 Adapter Interface | EN + ZH | ✅ |

---

## 6. 当前阻塞项

| 阻塞项 | 影响 | 下一步 |
|--------|------|--------|
| **Probe suite scaling** (6 → 50-100) | V0→V1 gate 无法通过；所有 V1 issues (0011-0017) 无法启动 | 构建 10-case 最小模板 → 扩展到 50 → 扩展到 100 |
| **HITL gate review** | V0→V1 gate 未完成人工确认 | probe suite 扩展后进行 |

---

## 7. 两个待执行实验

| 实验 | 目的 | 数据集 | 样本量 | 依赖 |
|------|------|--------|--------|------|
| **实验二: CMD 归因有效性** | CMD 能否在已知 perturbation 下正确识别失败的 memory operation | Probe Case（含注入的 `perturbation_type` + `expected_behavior`） | 50-100 (V0), 100-150 (V1) | 无 |
| **实验一: Context 拼接有效性** | `wrong_memory + cause + corrected_memory` 是否比纯 `corrected_memory` 更有效 | 4-Mode Context Case（同一 query × 4 种 context: none/full_trace/corrected_only/contrastive） | 15-40 | 实验二的 ECS 产物 |

**构建顺序约束**: 实验二 → 实验一（Context Case 的 `wrong_memory`/`cause`/`corrected_memory`/`repair_guidance` 均来自 CMD 对 Probe Case 产出的 ECS）。

**关键发现**: 40+ 篇论文中无一篇提供 4-mode context construction 对照实验。实验一无论结果是正是负，本身构成 novelty contribution。

---

## 8. Metabolism 状态

| Day | 日期 | 新论文 | 新假设 |
|-----|------|--------|--------|
| Day 0 | 2026-05-10 | 27 papers, 10 repos (基线构建) | hyp-001 ~ hyp-012 |
| Day 1 | 2026-05-11 | 9 papers, 1 repo (MemFlow, ErrorProbe, MemEvoBench, Memora 等) | — |
| Day 2 | 2026-05-12 | 4 papers (PrefixGuard, MAGE, MemORAI, Trojan Hippo) | hyp-013 (PrefixGuard-CMD 两层架构) |

**当前文献库**: 40+ reference notes，覆盖 agent memory、failure diagnosis、memory security、retrieval evaluation 等领域。

---

## 9. 下一步行动

1. **Probe suite scaling**: 构建 10-case 最小模板（覆盖 2-3 个 label，每个含多种变体）→ 同时服务实验一和实验二
2. **实验二执行**: 10 cases → CMD pipeline → 产出 ECS + 归因指标
3. **实验一执行**: ECS review → 构造 4-Mode Context Cases → 真实 LLM 评估
4. **HITL gate review**: probe suite 扩展后人工确认 V0→V1 gate
5. **V1 启动**: issue 0011 (`ingestion_error` + `route_error` + mem0 adapter)
