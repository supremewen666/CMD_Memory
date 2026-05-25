# EXPERIMENT.md — V1 代码 + LLM (qwen ollama) 实验规划清单

**生成日期**: 2026-05-25
**会话**: improve-codebase-v1
**适用范围**: V1 现有代码（cmd_audit/ 9117 行 / 113 文件 / 803 tests 绿）+ ollama 本地 LLM (qwen2.5:7b 默认) 能跑的全部实验
**前置阅读**: TASK.md（V1.0/V1.1 milestone）、CONTEXT.md（domain language）、FUTURE.md（V2-readiness refactor 计划）

---

## 实验总览（26 个实验，四类，三阶段）

### 第一类：参数扫描（P1-P6）

| 编号 | 实验 | 入口 | 参数空间 | 数据 | 测量 | paper 章节 | TASK.md |
|------|------|------|------|------|------|------|------|
| P1 | tie_margin 校准 | `scripts/calibrate_tie_margin.py` | {0.0, 0.05, 0.10, 0.15, 0.20, 0.25} | 130+596 | Macro F1, Top-2 accuracy, close_deltas 命中, coupled-failure 识别率 | 附录 "tie margin sensitivity" | issue 0029 |
| P2 | top_k 扫描 | attribution + hook | top_k∈{1,2,3,4,5}, TOP_K∈{2,3,4,5} | 130+596 | top-K accuracy 曲线, cost vs accuracy | main body "ablation: replay portfolio depth" | issue 0026/0028 |
| P3 | positive_gain_threshold 扫描 | — | {-0.05, -0.01, 0.0, 0.01, 0.05, 0.1} | 596 | false-positive 率, LLM vs phrase 敏感性差异 | 附录 "scoring sensitivity" | issue 0023 附加 |
| P4 | partial_threshold 扫描 | Post-Repair | {0.3, 0.4, 0.5, 0.6, 0.7, 0.8} | — | recovered/partial/failed 三值分布 | 附录 "partial assessment threshold" | issue 0027 附加 |
| P5 | Hook 网格校准 | `scripts/calibrate_hook.py` | TOP_K × FALLBACK_THRESHOLD = 84 网格点 | — | recall priority (F2), cost reduction + recall 留存 | 附录 hook supplementary | issue 0028 |
| P6 | SubagentScorer 并行/retry | — | max_workers∈{1,4,8,16}, max_retries∈{0,1,3} | — | wall-clock, 失败率, output_format_error, score 一致性 | reproducibility 附录 | issue 0023 run_meta |

### 第二类：设计选择对比（D1-D9）

| 编号 | 实验 | 对照 | 数据 | 测量 | 核心命题 | paper 章节 | TASK.md |
|------|------|------|------|------|------|------|------|
| D1 | repair_guidance 形态对比 | R1 skill-template vs R2 LLM-paraphrase vs R3 LLM-from-scratch vs R4 skill-启发-LLM-自查 | 80+30 ECS 标注 | recovered rate, 人工眼审 5 档, cost, 新颖修复召回 | LLM 推理 repair 是否优于查表；自查 vs 直接推理 | V1.0 独立 section + V2 主 paper 引用 | **issue 0040（新）** |
| D2 | agent_generate vs phrase-match | LLM 评分 vs phrase-match | 596 | 每 label attribution 分布偏移, top-2 重叠度, recovery_gain 分布 | phrase-match shortcut 把 attribution 推向了哪里 | **main paper** | issue 0023 副产物 |
| D3 | SubagentScorer vs evidence_recall | LLM vs phrase | 130+50 hold-out | evidence_score 散点图, Pearson, Kendall τ | phrase 在多少 case 上低估/高估 evidence_score | **main paper validity** | issue 0024/0026 |
| D4 | AnswerVerifier vs casefold substring | LLM vs substring | — | answer_score 差异 | — | 附录 | issue 0024 副产物 |
| D5 | rule-based vs LLM attribution | recovery_gain 排序 vs LLM prompt | 100 pilot + 全 596 | Macro F1, 混淆矩阵差异, LLM 优于 rule-based 的 label | V2 "LLM attribution > rule-based" 是否成立 | V1.0 main body + V2 headline ablation | **issue 0037（新）** |
| D6 | iterative repair vs single-pass | 穷尽 close_deltas vs 只跑 top-1 | 80+596 | 多救回 case 数, 平均尝试次数, cost 增加比例 | iterative 是否值得 cost | **main body 必备 ablation** | issue 0027 副产物 |
| D7 | FM 注入 vs 不注入 | FM 启用 vs 关闭 | 分阶段 100/200/300/596 | recovered rate 学习曲线 | full loop paper claim 实证 | **main body** (claim 3) | issue 0023/0027 副产物 |
| D8 | Hook 启用 vs 关闭 | use_hook=True vs False | — | cost reduction vs missed-detection rate | — | 附录 hook supplementary | issue 0028 |
| D9 | passthrough replay 逐个移除 | 移除 oracle_route / safety_off / oracle_granularity / graph_off 各一次 | — | 移除后哪些 label attribution 失败 | 每个 V1 新增 replay 对哪个 label 必要 | **main body ablation** | issue 0023 附加 |

### 第三类：鲁棒性对比（R1-R6）

| 编号 | 实验 | 变量 | 数据 | 测量 | 核心命题 | paper 章节 | TASK.md |
|------|------|------|------|------|------|------|------|
| R1 | agent 模型规模 4 档 | agent∈{qwen2.5:1.5b,7b,14b,32b}, scorer 固定 7b | 596（或 100 subset） | Macro F1, cost, wall-clock | CMD attribution 是 operation-level invariant | **main body** | issue 0023 AC |
| R2 | scorer 家族鲁棒性 | scorer∈{qwen2.5:7b,llama3.1:8b,phi3:14b,mistral:7b}, agent 固定 | 130 | bootstrap CI 重叠, Pearson, κ | 评估器鲁棒性 (issue 0026 AC11) | main body | issue 0026 AC11 |
| R3 | agent×scorer 同/异家族 | (agent∈{qwen,llama}) × (scorer∈{qwen,llama}) 2×2 | 130 | — | 反驳 "同家族幻觉一致 inflate score" | **关键附录** | issue 0026 附加 |
| R4 | seed 鲁棒性 | temp=0 同 model 跑两次 | 596 | 逐 case attribution 一致性 | 复现性论据 | reproducibility | issue 0026 附加 |
| R5 | 跨 dataset coverage | MemoryArena / LongMemEval / ToolBench | — | 跨 dataset Macro F1 差异, 混淆矩阵差异 | V1.0 coverage, V1.1 generalization | main body cross-dataset | issue 0023/0035 |
| R6 | perturbation 类型鲁棒性 | 按 perturbation_label 切片 | 596 | 每切片 attribution accuracy | CMD 弱项 (limitations) | limitations | issue 0023 副产物 |

### 第四类：V2 主 paper 预演 Pilot（Pilot1-5）

| 编号 | 实验 | 做法 | 数据 | 测量 | 决策意义 | paper 章节 | TASK.md |
|------|------|------|------|------|------|------|------|
| Pilot 1 | LLM attribution vs rule-based (=D5) | 同 D5 | 100+596 | Macro F1, 混淆矩阵 | V2 headline ablation 提前验证 | V2 主 paper | **issue 0037（新）** |
| Pilot 2 | no-gold ValidatorSubagent 可行性 | AnswerVerifier 改造为 pairwise judgment | 80+30 眼审 | no-gold vs gold κ, 正确率 | κ≥0.6→pairwise; κ<0.4→需兜底 | V2 主 paper | **issue 0038（新）** |
| Pilot 3 | CauseFinder+RepairPlanner 三段格式 | 一次性 prompt 出 cause/corrected_memory/repair_guidance | 30 案例 | 人工 5 档质量, parser 错误率 | 单 prompt 还是拆两个 subagent | V2 主 paper | **issue 0039（新）** |
| Pilot 4 | surrogate trace 在线无 gold 路径 | surrogate_gap.py + SubagentScorer | 50 hold-out | retention% (4 个 gold-dependent label) | 高→直接 surrogate; 低→必须 fallback | V1.0 关键附录 + V2 引用 | issue 0036 |
| Pilot 5 | repair_guidance R4 在 V2 延伸 | D1 的 R4 workflow 扩展 | — | — | V2 "主动选择何时 LLM、何时 skill"论述 | V2 主 paper repair backend | issue 0040 |

---

### 分阶段执行

| 阶段 | 时间 | 内容 | 新增工作量 |
|------|------|------|------|
| 阶段 A | V1.0 arxiv 06-10 ship 前 (critical path) | 只跑已规划 issue（0022/0023/0024/0026/0027/0028/0036），不新增工作 | 0 |
| 阶段 B | V1.0 ship 后 ~2 周 | 新增 issue 0037/0038/0039/0040 + R1 | ~3 工作日 |
| 阶段 C | V1.1 venue submission | issue 0035 corpus 后重跑全部 | — |

### paper 三大 claim 实证映射

| Claim | 支撑实验 |
|------|------|
| (1) automated counterfactual attribution at operation-level granularity | Headline=issue 0026/130-case; ablation=D5/D6/D9; robustness=R1/R2/R3 |
| (2) Post-Repair Context Replay as automated semantic quality gate | Headline=issue 0027/80-case 5-mode; ablation=D6/D8/D1; parameter=P4 |
| (3) full detection→diagnosis→repair→validate→store loop | Headline=D7 FM on/off+学习曲线 + 端到端示例 |

---

## 0. 文档定位

### 0.1 V1 论文实验前提

**V1 论文实验全部 LLM 评分。phrase-match 永久退场（D34 / issue 0031 已把对应 artifact 移到 `legacy_phrase_match_2026_05_22/`）。**

V1 已经 LLM 化的节点：② Hook RPE label / ③ replay 每个的 answer + evidence_score + answer_score / ③ self-supervision 评分 / ④ Post-Repair 评分 / ④ RepairExecutor action 选（可选）。

V1 仍 rule-based、V2 主 paper 才 LLM 化的节点：③ assign_attribution（recovery_gain 排序）/ ④ draft_ecs_for_label（查表）/ ④ online_post_repair_validate（用 gold）/ ⑤ FM retrieve（composite key）。

V1 与 V2 的 LLM 形态差异：V1 = `LLMClient.generate()` 单 prompt 调用；V2 = 显式 Claude Code 式 subagent 协作架构（多 role / coordinator / 跨 subagent trace 共享）。

### 0.2 LLM 基础设施现状

`cmd_audit/llm_client.py:36-54`：

```python
class LLMClientConfig:
    base_url = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")
    model = os.environ.get("LLM_MODEL", "qwen2.5:7b")
    timeout_seconds = float(os.environ.get("LLM_TIMEOUT", "60"))
    max_retries = 1
    temperature = 0.0
```

OpenAI-compatible `/v1/chat/completions` → ollama-native，**无"接通 ollama"工作**，只有"启动服务 + 拉模型"运维步骤。

### 0.3 实验分类原则

按"目的"四类组织（不是按"用不用 LLM"）：

- **第一类 参数扫描**（P1-P6）：V1 已暴露的可扫参数
- **第二类 设计选择对比**（D1-D9）：V1 内部"两条路"的对照
- **第三类 鲁棒性对比**（R1-R6）：多模型 / 多 dataset / 多种子
- **第四类 V2 主 paper 预演 pilot**（Pilot1-5）：V1 期间为 V2 收集证据

每条实验都标：入口 / 数据 / 测量 / 能验证的命题 / paper 章节候选 / TASK.md 落地。

---

## 1. 第一类：参数扫描（V1 既有参数）

### P1：tie_margin 校准

| 项 | 值 |
|---|---|
| 入口 | `scripts/calibrate_tie_margin.py` 已实现 |
| 参数空间 | `tie_margin ∈ {0.0, 0.05, 0.10, 0.15, 0.20, 0.25}` |
| LLM 调用 | qwen2.5:7b 全栈评分 (~6000 调用 / 596 案例) |
| 数据 | 130 案例 + 596 案例 |
| 测量 | Macro F1、Top-2 accuracy、close_deltas 命中比例、coupled-failure 识别率 |
| 能验证的命题 | V1 默认 `tie_margin=0.0` 是否最优；tie_margin > 0 在哪些 label 上提升 Top-2 但损 Top-1；coupled-failure 识别最佳阈值 |
| paper 章节 | 附录 "tie margin sensitivity" |
| TASK.md 落地 | issue 0029（Coupled-failure subset post-hoc）|

### P2：top_k 扫描

| 项 | 值 |
|---|---|
| 参数空间 | attribution `top_k ∈ {1, 2, 3, 4, 5}`；hook `TOP_K ∈ {2, 3, 4, 5}` |
| 数据 | 130 + 596 |
| 测量 | top-K accuracy 曲线、cost(LLM 调用) vs accuracy 折线 |
| 能验证 | 归因 top-2 是否显著优于 top-1（justify "close_deltas as paper artifact"）；hook top-3 vs top-5 cost-accuracy 折点 |
| paper 章节 | main body "ablation: replay portfolio depth" |
| TASK.md 落地 | issue 0026 附加 + issue 0028 |

### P3：positive_gain_threshold 扫描

| 项 | 值 |
|---|---|
| 参数空间 | `{-0.05, -0.01, 0.0, 0.01, 0.05, 0.1}` |
| 数据 | 596 |
| 测量 | 阈值越严，false-positive attribution 越少但漏召；LLM 评分 vs phrase-match 评分对该阈值敏感性差异 |
| 能验证 | LLM 评分降低 false-positive 多少（D34 R1 实证依据之一）|
| paper 章节 | 附录 "scoring sensitivity" |
| TASK.md 落地 | 加 issue 0023 附加 |

### P4：partial_threshold 扫描（Post-Repair）

| 项 | 值 |
|---|---|
| 现状 | `run_post_repair_context_replay(partial_threshold=0.5)` 默认 |
| 参数空间 | `{0.3, 0.4, 0.5, 0.6, 0.7, 0.8}` |
| 测量 | 三值分布 (recovered/partial/failed) 对该阈值敏感性 |
| 能验证 | partial 区间宽度对 iterative repair 触发率的影响 |
| paper 章节 | 附录 "partial assessment threshold" |
| TASK.md 落地 | 加 issue 0027 附加 |

### P5：Hook 网格校准

| 项 | 值 |
|---|---|
| 入口 | `scripts/calibrate_hook.py` 已实现 |
| 参数空间 | `TOP_K × FALLBACK_THRESHOLD` = 84 网格点（issue 0028 已规划）|
| F2 优化 | recall priority |
| 能验证 | calibrated hook vs uncalibrated 在 596 案例上的 cost reduction + recall 留存 |
| paper 章节 | 附录 hook supplementary |
| TASK.md 落地 | issue 0028 |

### P6：SubagentScorer 并行 / retry 策略

| 项 | 值 |
|---|---|
| 现状 | `SubagentScorer(max_workers=5, max_retries=1)` |
| 参数空间 | `max_workers ∈ {1, 4, 8, 16}`；`max_retries ∈ {0, 1, 3}` |
| 测量 | wall-clock、LLM 调用失败率、output_format_error 率、最终 score 一致性 |
| 能验证 | retry 是否真改善 score 质量；并行度 cost-quality 边界 |
| paper 章节 | reproducibility 附录或运维知识 |
| TASK.md 落地 | 加 issue 0023 run_meta 扩展 |

---

## 2. 第二类：设计选择对比（V1 内部"两条路"）

### D1：repair_guidance 形态对比（V2 主 paper 核心 ablation 预演）

V1 现有 `repair_guidance` 来自 `repairs.py:get_targeted_repair_action_v1(label)` 查表。**这是开放设计点**（FUTURE.md §7），不是已定方案。

四种候选形态：

| 形态 | 实现 | LLM 调用 |
|---|---|---|
| **R1 skill-template** | V1 现有 rule-based 查表 | 0 |
| **R2 LLM-paraphrase** | template 喂 LLM，按 case 上下文重写 | 1/case |
| **R3 LLM-from-scratch** | 不用 template，纯 LLM 推理 | 1/case (不同 prompt) |
| **R4 skill-启发-LLM-自查** | template 给 workflow（"找 wrong memory item → 比较 → 改写"），LLM 按步自查 | 多调用/case |

**实验设计**：

- 数据：80 案例 Experiment 1 + 30 案例 ECS 人工标注子集
- 测量：
  - Post-Repair recovered rate（每形态）
  - 人工眼审 repair_guidance 质量评分（5 档 / 30 案例）
  - cost（LLM 调用次数 + token）
  - 召回新颖修复方案能力（R1 永远只能给 6 个 fixed action；R3/R4 能否产生超出 6 个 action 的 valid repair）

**核心命题**：

- R1 vs R3：用 LLM 推理 repair_guidance 是否优于 skill-template
- R3 vs R4：让 LLM 自查（workflow 启发）vs 直接给（一次推理）哪个更好
- R2 vs R3：基于 template 的 LLM 改写 vs 从零推理
- 是否有 label 类别上 R1 已经够用

**paper 章节**：**V1.0 paper 独立 section**（直接回答 reviewer "为什么要用 LLM 重写一个能查表搞定的事"）；**同时是 V2 主 paper repair backend 设计章节的实证依据**。

**TASK.md 落地**：**新增 issue 0040 — repair_guidance 形态对比**

### D2：agent_generate 评分 vs phrase-match 评分（D34 R1 实证依据）

| 项 | 值 |
|---|---|
| 现状 | `run_case_v1` 接受 `agent_generate=None` → fallback phrase-match。论文绝不能用 phrase-match |
| 对照 | `agent_generate=qwen2.5:7b ollama` vs `agent_generate=None` 在 596 案例上的归因差异 |
| 测量 | 每 label 的 attribution 分布偏移、close_deltas top-2 重叠度、recovery_gain 分布 |
| 能验证 | phrase-match shortcut 在哪些 label 上把 attribution 推向了哪里 |
| paper 章节 | **main paper "phrase-match shortcut artifact" 论证** |
| TASK.md 落地 | issue 0023（at-scale LLM re-test）副产物 |

### D3：SubagentScorer vs evidence_recall_from_text

| 项 | 值 |
|---|---|
| 数据 | 130 案例 + 50 案例 hold-out |
| 测量 | 每 case 的 evidence_score 散点图（LLM X 轴 vs phrase Y 轴）+ Pearson + Kendall τ |
| 能验证 | phrase-match 评分在多少比例 case 上低估或高估 evidence_score |
| paper 章节 | **main paper validity argument** |
| TASK.md 落地 | issue 0024（researcher adjudication）+ issue 0026 |

### D4：AnswerVerifier vs casefold substring

| 项 | 值 |
|---|---|
| 同 D3 思路但目标是 answer_score | |
| paper 章节 | 附录 |
| TASK.md 落地 | issue 0024 副产物 |

### D5：CMD assign_attribution_v1 rule-based vs LLM attribution（**V2 主 paper headline ablation 预演**）

| 项 | 值 |
|---|---|
| 现状 | V1 attribution 是 recovery_gain 排序纯函数 |
| 对照 | 一次性 prompt：把 10 replay 结构化结果（name + recovery_gain + evidence_score + 截断 evidence_block）喂 qwen，让它直接出 predicted_label |
| 数据 | 100 案例 pilot + 全 596 案例（如果时间允许） |
| 测量 | rule-based vs LLM 的 Macro F1、混淆矩阵差异、哪些 label 上 LLM 推理优于 rule-based |
| 能验证 | V2 主 paper "LLM attribution > rule-based" 是否真成立（**不能假设，必须实证**）；哪些 label 是 "rule-based 已经够用" |
| paper 章节 | V1.0 paper 进 main body 作 "V1 attribution upper bound check"；**V2 主 paper headline ablation** |
| TASK.md 落地 | **新增 issue 0037 — V2 attribution 可行性 pilot** |

### D6：iterative repair vs single-pass

| 项 | 值 |
|---|---|
| 现状 | `RepairOrchestrator` 已实现 close_deltas 迭代 |
| 对照 | 80 案例 + 596 案例上，iterative（穷尽 close_deltas）vs single-pass（只跑 top-1）的 Post-Repair 比例 |
| 测量 | iterative 多救回多少 case、平均尝试次数、cost 增加比例 |
| 能验证 | iterative repair 是否值得 cost |
| paper 章节 | **main body 必备 ablation** |
| TASK.md 落地 | issue 0027 副产物 |

### D7：fm_context 注入 vs 不注入

| 项 | 值 |
|---|---|
| 现状 | `RepairOrchestrator.run(fm_context="")` 默认无；启用时通过 `FailureMemoryStoreV1.retrieve` 取 |
| 对照 | FM 启用 vs 关闭对 Post-Repair recovered rate 影响 |
| 测量 | 分阶段（前 100/200/300/596 cases）的 recovered rate 曲线（FM 学习曲线）|
| 能验证 | "full detection→...→store loop" 论文 claim 之一的实证支撑 |
| paper 章节 | **main body**（paper claim 3） |
| TASK.md 落地 | issue 0023 + issue 0027 副产物 |

### D8：Hook 启用 vs 关闭

| 项 | 值 |
|---|---|
| 入口 | `run_full_real_suite(use_hook=True/False)` |
| 对照 | cost reduction vs missed-detection rate |
| paper 章节 | 附录 hook supplementary |
| TASK.md 落地 | issue 0028 |

### D9：V1 passthrough replay 逐个移除

| 项 | 值 |
|---|---|
| 做法 | 从 portfolio 移除 `oracle_route` / `safety_off` / `oracle_granularity` / `graph_off` 四个 V1 新增 replay 各一次（`run_v1_replay_portfolio_subset` 已支持） |
| 对照 | 移除后哪些 label attribution 失败 |
| 能验证 | 每个 V1 新增 replay 对哪个 label 是必要的（label-replay 因果链验证）|
| paper 章节 | **main body ablation** |
| TASK.md 落地 | 加 issue 0023 附加 ablation |

---

## 3. 第三类：鲁棒性对比（多模型 / 多 dataset / 多种子）

### R1：agent 模型规模鲁棒性（4 档）

| 项 | 值 |
|---|---|
| 参数 | `agent_generate ∈ {qwen2.5:1.5b, 7b, 14b, 32b}`；scorer 固定 qwen2.5:7b |
| 数据 | 596 案例（或缩 100 案例 subset 减时） |
| 测量 | 每尺寸 Macro F1、cost、wall-clock |
| 能验证 | **CMD attribution 是 operation-level invariant**（核心论据）|
| paper 章节 | **main body** |
| TASK.md 落地 | 加 issue 0023 AC |

### R2：scorer 家族鲁棒性

| 项 | 值 |
|---|---|
| 参数 | `scorer ∈ {qwen2.5:7b, llama3.1:8b, phi3:14b, mistral:7b}`；agent 固定 qwen2.5:7b |
| 数据 | 130 案例 |
| 测量 | bootstrap CI 重叠 + Pearson + κ |
| 能验证 | issue 0026 AC11 评估器鲁棒性 |
| paper 章节 | main body |
| TASK.md 落地 | issue 0026 AC11 已要求 |

### R3：agent×scorer 同家族 vs 异家族

| 项 | 值 |
|---|---|
| 2×2 | (agent ∈ {qwen, llama}) × (scorer ∈ {qwen, llama}) |
| 数据 | 130 案例 |
| 能验证 | 反驳 "同家族幻觉一致 inflate score" |
| paper 章节 | **关键附录** |
| TASK.md 落地 | issue 0026 附加表 |

### R4：seed 鲁棒性

| 项 | 值 |
|---|---|
| 做法 | temperature=0 + 同 model hash 跑两次 596 案例，逐 case attribution 一致性 |
| 能验证 | 复现性论据（API 模式下即使 temp=0 也有 sampling 漂移；ollama 本地完全可控）|
| paper 章节 | reproducibility 章节 |
| TASK.md 落地 | issue 0026 附加 |

### R5：跨 dataset coverage（V1.0）/ generalization（V1.1）

| 项 | 值 |
|---|---|
| 现状 | 3 dataset（MemoryArena / LongMemEval / ToolBench）已实现 |
| 测量 | 跨 dataset Macro F1 差异、混淆矩阵差异 |
| V1.0 claim | **coverage 而非 generalization**（N 不足支撑 generalization）|
| V1.1 claim | **generalization**（issue 0035 corpus 后 N 支撑）|
| paper 章节 | main body cross-dataset 章节 |
| TASK.md 落地 | issue 0023 + issue 0035 |

### R6：dataset 内 perturbation 类型分布鲁棒性

| 项 | 值 |
|---|---|
| 做法 | 按 perturbation_label 切片 596 案例，每片测 attribution accuracy |
| 能验证 | 哪些 label 类型 CMD 偏弱（论文 limitations 章节材料）|
| paper 章节 | limitations |
| TASK.md 落地 | issue 0023 副产物 |

---

## 4. 第四类：V2 主 paper 预演 pilot

V2 是完整独立论文（FUTURE.md §1）。下列 pilot 在 V1 期间用 ollama 跑出小规模数据，**降低 V2 实施风险**。如果 pilot 显示 V2 某组件不可行，V2 设计要调整。

### Pilot 1：LLM attribution vs rule-based attribution

同 D5。100 案例 pilot + 596 案例完整跑。**V2 主 paper 必做对照**，V1 期间提前跑能让 V2 主 paper 直接引用。

### Pilot 2：online no-gold ValidatorSubagent 形态可行性

| 项 | 值 |
|---|---|
| 做法 | 现有 `AnswerVerifier(answer, gold_answer)` 改造一次性脚本 — prompt 改成 (original_answer, repaired_answer) pairwise judgment |
| 数据 | 80 案例 Experiment 1 子集（已有 gold-based 评分）|
| 测量 | no-gold pairwise verdict vs gold-based assessment 的 κ + 人工眼审 30 案例正确率 |
| 能验证 | online 无 gold 评分是否可信（V2 主 paper online runtime 的可行性根基）|
| 决策意义 | κ ≥ 0.6 → V2 ValidatorSubagent 可走简单 pairwise；κ < 0.4 → V2 必须设计兜底（cross-check 多 subagent / FM 校验等）|
| paper 章节 | V2 主 paper headline ablation 预演；V1.0 paper 不进 |
| TASK.md 落地 | **新增 issue 0038 — V2 no-gold validator 可行性 pilot** |

### Pilot 3：CauseFinder + RepairPlanner 三段输出格式可行性

| 项 | 值 |
|---|---|
| 做法 | 30 案例上一次性 prompt — 让 qwen 输出 cause / corrected_memory / repair_guidance 三段 |
| 测量 | 人工眼审 30 案例 5 档质量评分 + 与 V1 rule-based 输出对比 + parser 错误率 |
| 能验证 | V2 ④.a 节点格式可行性 |
| 决策意义 | 决定 V2 主 paper 这一步是单 prompt 还是拆 cause finder + repair planner 两个 subagent |
| paper 章节 | V2 主 paper repair backend 设计章节预演 |
| TASK.md 落地 | **新增 issue 0039 — V2 CauseFinder/RepairPlanner 格式 pilot** |

### Pilot 4：surrogate trace 在线无 gold 路径质量

| 项 | 值 |
|---|---|
| 做法 | 现有 `surrogate_gap.py` 已接 SubagentScorer，跑 50 案例 hold-out 取 retention% |
| 能验证 | 4 个 gold-dependent label 在线无 gold 时 recovery_gain 保留率 |
| 决策意义 | retention 高 → V2 在线路径直接走 surrogate；低 → V2 必须 fallback |
| paper 章节 | **V1.0 关键附录** + V2 主 paper 引用 |
| TASK.md 落地 | issue 0036（已规划）|

### Pilot 5：repair_guidance 形态对比（D1 在 V2 主 paper 的延伸）

D1 在 V1.0 paper 是独立 section；同时 **V2 主 paper 可以扩展这个对比** — 把 R4（skill 启发 LLM 自查 workflow）放进 V2 主 paper 的 repair backend 设计章节，让 V2 不只是"全 LLM 化"，而是**主动选择何时 LLM、何时 skill** 的论文论述。

---

## 5. V1 不能跑的命题（V2 主 paper 边界）

明示给未来 you 看，避免 V1.0 paper 误声称已验证：

| 命题 | 为什么 V1 跑不出 | V2 哪个组件验证 |
|---|---|---|
| Online no-gold runtime loop 整体可用性 | `run_post_repair_context_replay` 用 gold；ValidatorSubagent 不存在 | runtime/subagents/validator.py |
| Live store mutation 后 re-search 能否恢复 | recorded trace replay 物理上跑不出；需 mem0/letta live mode | adapter live mode |
| Subagent attribution 因果解释质量 | `attribution.py` rule-based 排序，无自然语言解释输出 | runtime/subagents/attribution.py |
| Subagent ECS 草拟（CauseFinder + RepairPlanner）质量 | `_ecs_for_label` 查表，无 LLM 推理 | runtime/subagents/{cause_finder, repair_planner}.py |
| Session 级 FM lifecycle 长期效应 | `FailureMemoryStoreV1` per-call 检索，无 session 状态 | runtime/state.py |
| 跨 case drift signal | 无 SessionState | runtime/state.py |
| 多 subagent 协作开销 vs 单 subagent | Coordinator 不存在 | runtime/subagents/coordinator.py |
| Subagent 显式 trace 共享 / cache hit rate | V1 每 LLM 调用独立 | runtime/subagents/coordinator.py |

**注**：Pilot 1-5 用 V1 + ollama 做单点小规模 pilot，**不能验证整体闭环** — 整体闭环必须 V2 实施完才能验证。Pilot 是"启动 V2 之前的可行性证据"，不是"V2 已完成"。

---

## 6. V1.0 paper 决策矩阵

按 paper 位置归类全部实验：

### 6.1 MAIN PAPER HEADLINE

| 实验 | TASK.md 落地 |
|---|---|
| 130 案例 LLM-stack adjudicated headline + bootstrap CI | issue 0026 |

### 6.2 main body

| 实验 | TASK.md 落地 |
|---|---|
| 80 案例 5-mode Experiment 1 | issue 0027 |
| FM 注入 vs 不注入 + 学习曲线（D7） | issue 0023 副产物 |
| iterative repair vs single-pass（D6） | issue 0027 副产物 |
| V1 passthrough replay 逐个移除（D9） | issue 0023 附加 |
| Adapter parity（含 LLM stack 下） | issue 0032 |
| **repair_guidance 形态对比（D1）** | **issue 0040（新）** |
| agent 模型规模鲁棒性（R1） | issue 0023 附加 |
| scorer 家族鲁棒性（R2） | issue 0026 AC11 |
| agent×scorer 解耦（R3） | issue 0026 附加 |
| 跨 dataset coverage（R5）| issue 0023 |
| LLM attribution vs rule-based pilot（D5 / Pilot 1） | **issue 0037（新）** |

### 6.3 关键附录

| 实验 | TASK.md 落地 |
|---|---|
| Surrogate gap LLM-stack retention（Pilot 4） | issue 0036 |
| SubagentScorer vs phrase（D3）| issue 0024 + issue 0026 |
| phrase-match shortcut artifact 论证（D2）| issue 0023 副产物 |
| seed reproducibility（R4） | issue 0026 附加 |
| Cohen's κ vs 人工 | issue 0024 |
| Hook 校准 5960 pair（P5） | issue 0028 |

### 6.4 普通附录

| 实验 | TASK.md 落地 |
|---|---|
| 596 LLM-stack at-scale re-test | issue 0023 |
| Coupled-failure subset（P1 tie_margin）| issue 0029 |
| Hook 启用 vs 关闭（D8）| issue 0028 |
| AnswerVerifier vs substring（D4）| issue 0024 副产物 |
| top_k 扫描（P2） | issue 0026/0028 |
| positive_gain_threshold（P3）| issue 0023 附加 |
| partial_threshold（P4）| issue 0027 附加 |
| SubagentScorer 并行/retry（P6）| issue 0023 run_meta |
| 小模型可行性（agent qwen2.5:1.5b）| 附录 deployment cost 表 |
| Cost wall-clock | issue 0023 run_meta |
| perturbation 切片鲁棒（R6）| issue 0023 副产物（limitations）|

### 6.5 V1.0 不进、V2 主 paper 引用

| 实验 | TASK.md 落地 |
|---|---|
| LLM attribution vs rule-based 完整对比（Pilot 1 升级）| V2 主 paper |
| no-gold validator pilot（Pilot 2）| **issue 0038（新）**；V2 主 paper |
| CauseFinder/RepairPlanner 格式 pilot（Pilot 3）| **issue 0039（新）**；V2 主 paper |
| repair_guidance R4 skill-workflow（Pilot 5）| issue 0040；V2 主 paper |

---

## 7. 关键判断

### 7.1 V1 + LLM 已能跑出 V1.0 paper 全部 headline + ablation + robustness

phrase-match 永久退场不是"加 LLM 才能跑"，而是"V1 论文本来就用 LLM 评分跑"。issue 0022 LLM eval wiring 完成后，全部 V1.0 paper 实验立刻可执行。

### 7.2 repair_guidance 形态对比（D1 / R1-R4）值得 V1.0 paper 独立 section

直接回答 reviewer "为什么 LLM 重写一个能查表搞定的事"，并把 skill-启发-LLM-自查（R4）放进比较，**让 V2 主 paper 的 repair backend 设计有实证依据**。

### 7.3 V2 主 paper 预演 pilot（D5/Pilot1, Pilot2, Pilot3, Pilot5）值得在 V1 期间跑

V2 是完整独立论文。Pilot 在 V1 期间跑能让 V2 主 paper kickoff 时就有"已 validated 的可行性证据"。三个 pilot 总工作量 < 1.5 工作日。

### 7.4 新增工作集中在 ~3 工作日，不进 critical path

阶段 A（V1.0 ship 前）只跑已规划 issue。阶段 B（V1.0 ship 后到 V1.1 corpus migration）执行新增工作。

### 7.5 paper 章节大致映射

- **main body**: D1/D5/D6/D7/D9/R1/R2/R3/R5 + 130 headline + 80 5-mode + adapter parity
- **关键附录**: R4/D2/D3/Pilot4 + κ + Hook 校准
- **普通附录**: P1-P6 参数扫描 + R6 + 小模型 + cost
- **V2 主 paper 预演**: Pilot1/Pilot2/Pilot3/Pilot5

---

## 8. 实验规划（按时间窗 + 优先级）

### 8.1 阶段 A：V1.0 arxiv 06-10 ship 之前（critical path）

**不做 EXPERIMENT.md 任何新增工作**。只跑已规划 issue（0022/0023/0024/0026/0027/0028/0036），它们自带产出已覆盖：

| 自带产出 | 覆盖的实验编号 |
|---|---|
| issue 0022 LLM stack wiring | 启用所有 LLM 评分实验 |
| issue 0023 596 at-scale | D2 / D7 / D9 / R5 / R6 / P3 / P6 / cost |
| issue 0024 130 adjudication | D3 / D4 / κ |
| issue 0026 130 headline | **headline** + R2 / R3 / R4 / P2 |
| issue 0027 80 5-mode | **5-mode** + D6 / P4 |
| issue 0028 hook 校准 | P5 / D8 |
| issue 0036 surrogate retention | Pilot 4 |
| issue 0029 coupled-failure | P1 tie_margin |

### 8.2 阶段 B：V1.0 ship 后到 V1.1 corpus migration（~2 周）

挂在已规划 issue 之外，**全部新增工作量 ~3 工作日**：

| 实验 | 新增 issue | 工作量 |
|---|---|---|
| **D1 repair_guidance 形态对比 R1/R2/R3/R4** | **新增 issue 0040** | 1 工作日（80 案例 × 3 形态 + 30 案例眼审）|
| **D5 / Pilot 1 LLM attribution** | **新增 issue 0037** | 0.5 工作日（100 案例 pilot + 50-100 行脚本）|
| **Pilot 2 no-gold validator** | **新增 issue 0038** | 0.5 工作日（50 案例 + 30-50 行脚本）|
| **Pilot 3 CauseFinder 格式** | **新增 issue 0039** | 0.5 工作日（30 案例 + 50-100 行 + 1h 眼审）|
| **R1 agent 模型规模 4 档** | 加 issue 0023 AC | 0.5 工作日（4× ollama 跑 596 或 100 subset）|

### 8.3 阶段 C：V1.1 venue submission（issue 0035 corpus 后）

阶段 B 全部完成后，V1.1 venue paper 直接引用所有 ablation + robustness 数据。issue 0035 corpus 重跑 V1.0 已规划 issue 即可（issue 0023/0024/0026/0027/0028/0029/0031/0036 + issue 0040）。

---

## 9. 对 V1.0 paper 三大 claim 的实证支撑映射

paper 三大 claim（CLAUDE.md / TASK.md "Paper claims focus"）：

| Claim | 支撑实验 |
|---|---|
| (1) automated counterfactual attribution at operation-level granularity | **Headline = issue 0026 / 130-case adjudicated**；ablation = D5/D6/D9；robustness = R1/R2/R3 |
| (2) Post-Repair Context Replay as automated semantic quality gate | **Headline = issue 0027 / 80-case 5-mode**；ablation = D6 iterative vs single-pass / D8 hook on/off / **D1 repair_guidance 形态对比**；parameter = P4 partial_threshold |
| (3) full detection → diagnosis → repair → validate → store loop | **Headline = D7 FM on/off + 学习曲线**；端到端示例 = `run_case_v1_with_hook_and_repair`（dict 升 dataclass 后） |

---

## 10. 文档维护

- 实验执行后在对应章节标注 ✅ 完成日期 + artifact 路径
- 实验结果若与命题不符，**保留原命题与反例数据，不删除** — 作为论文负面结果或边界条件论据
- 阶段 B 新增 issue (0037/0038/0039/0040) 完成后回填到 §6 决策矩阵
- V2 主 paper 形状若变化，更新 §5 V1 不能跑的命题表
- 新发现的可跑实验直接增到 §1-§4 对应类别，更新 §6 决策矩阵
