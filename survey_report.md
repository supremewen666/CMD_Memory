# Self-Evolving Memory Repair Ecologies：定向文献收集报告

## 1. 调研范围与方法

本轮调研服务于题目 **“Self-Evolving Memory Repair Ecologies: Competitive Multi-Skill Interaction without Gold Supervision”**，目标不是穷举 agent memory 文献，而是核查最直接支撑或挑战以下四条叙事的工作：

1. memory optimization / failure attribution / repair baseline；
2. population evolution、quality-diversity 与 niche specialization；
3. gold-free surrogate selection、margin-based abstention 与风险控制；
4. 多策略竞争、技能分化和 repair chaining。

共使用 8 组定向检索表达：

- `UMEM memory optimizer GRPO agent memory`
- `MemTrace error attribution LLM memory systems`
- `Evo-Memory ReMem self-evolving memory`
- `A-MEM agentic memory evolution`
- `MAP-Elites quality diversity population`
- `Promptbreeder prompt evolution population mutation`
- `conformal risk control abstention selective prediction`
- `unsupervised skill discovery specialization DIAYN`

筛选规则：优先 arXiv / OpenReview / 作者 GitHub；只保留直接 baseline 或机制可迁移的工作；不把关键词相似但任务无关的论文纳入 corpus。最终保留 **8 篇论文，分为 4 个方向**。八篇均已保存 arXiv 源码包；因此当前 corpus 不依赖第三方摘要或只有 PDF 的替代版本。

> 证据边界：本报告可以说明“在上述检索范围内未找到直接先例”，不能证明全领域不存在先例；任何 novelty claim 仍须在投稿前进行更系统的数据库检索和人工 related-work 审核。

## 2. 结论先行

- **最强直接 baseline 是 MemTrace、UMEM 和 Evo-Memory/ReMem。** MemTrace处理 operation-level tracing/attribution 并用归因信号闭环优化；UMEM用 GRPO 学习单个 Mem-Optimizer，联合优化抽取与管理；ReMem在流式任务中交织 Think、Act、Refine Memory。当前 CMD 叙事必须明确不是“首次 memory evolution”，而是 **gold-free、structured-operator population、per-case competition 与生态指标** 的组合。
- **“达尔文式进化”本身不是足够的 novelty。** Promptbreeder 已展示 LLM 相关对象的 population、mutation 与 fitness selection；MAP-Elites 更早就以“多样且高性能的解”而非单一最优为目标。CMD 应把 Darwinian loop 作为机制，把可复现的 operator genealogy、niche-conditioned fitness 和与 Lamarckian versioning 的受控比较作为实证贡献。
- **生态学叙事与 quality-diversity 高度相邻。** MAP-Elites 提醒我们：如果 failure type 已知，就应把 failure descriptors 作为 behavior space，比较全局 truncation selection 与按 niche 保留 elite 的方案。否则普通 selection 很可能让一个泛化 skill 占满种群，反而抹去 specialization。
- **DIAYN 只提供“无外部任务奖励也能产生技能分化”的相邻证据。** 它不是 memory repair、不是 competitive execution，也没有 winner/loser repair matrix；不能用来声称多修复技能自然会分化。
- **Gold-free agreement + abstention 目前应定位为经验性 identifiability analysis。** Conformal Risk Control 要求带标签的 calibration sample、单调 loss 和 exchangeability 等条件。仅画 margin/coverage 曲线不产生形式化风险保证；若论文要用“guaranteed”或“identifiable”措辞，需要额外定理或 calibration protocol。
- **未找到直接研究 top-K memory-repair policies 独立竞争、生态位重叠、跨轮次稳定性以及成功 repair chain 沉积的论文。** 这是一个有价值的检索缺口，但不是已证明的 novelty。

## 3. Top papers

### 3.1 Memory evolution、failure attribution 与 repair baselines

#### 1. MemTrace: Tracing and Attributing Errors in Large Language Model Memory Systems

- ID：`arXiv:2605.28732`（v3，2026-07-16）
- 作者：Xinle Deng 等
- 官方页面：https://arxiv.org/abs/2605.28732
- 本地源：`papers/memory_evolution/2605.28732/`
- 核心内容：把 memory pipeline 表示为 operation-variable execution graph，构建带人工 faulty-operation 标注的 MemTraceBench，并沿 operation subgraph 做错误归因；论文还用归因信号做闭环 prompt optimization。
- 对当前项目的直接关系：
  - 是 failure taxonomy、operation-level attribution 与 downstream repair 的最近邻 baseline。
  - CMD 应比较 attribution/repair 的监督来源：MemTraceBench 含 ground-truth error labels、faulty operations 和 explanations；CMD 的执行路径声称不读取 gold，需要用 provenance audit 和 gold-free/gold-supervised shadow evaluation 来证明边界。
  - “MemTrace applies fixed repair rules”不是由该论文摘要直接支持的安全概括；更准确的描述是它以 attribution 驱动 downstream prompt optimization。
- 挑战：如果 CMD 只做“检测 failure → 更新一个规则”，与 MemTrace 的闭环优化区分不够；竞争 population、winner/loser evidence 和 causal no-gold audit 必须成为主实验。

#### 2. UMEM: Unified Memory Extraction and Management Framework for Generalizable Memory

- ID：`arXiv:2602.10652`（2026-02-11）
- 作者：Yongshi Ye 等
- 官方页面：https://arxiv.org/abs/2602.10652
- 本地源：`papers/memory_evolution/2602.10652/`
- 核心内容：用一个 learned Mem-Optimizer 联合执行 memory extraction 和 management；以 semantic neighborhood marginal utility reward 通过 GRPO 优化，并在训练中在线更新 memory bank。
- 对当前项目的直接关系：
  - 是“单一 learned memory optimizer policy”的强 baseline，而不是简单的静态 heuristic。
  - CMD 的 within-family held-out probes 与 UMEM 的 semantic-neighborhood generalization 目标直接可比；应报告相同 case budget 下的 recovery、泛化和计算成本，而不能只作范式性描述。
  - CMD 的 population evolution 必须与单-policy optimization 在训练监督、LLM calls、A100-hours 和 inference cost 上公平比较。
- 访问限制：论文页面写明代码和模型“will be publicly released”；本轮未确认到作者正式代码仓库，因此没有克隆非官方实现。

#### 3. Evo-Memory: Benchmarking LLM Agent Test-time Learning with Self-Evolving Memory

- ID：`arXiv:2511.20857`（v2，2026-05-18）
- 作者：Tianxin Wei 等
- 官方页面：https://arxiv.org/abs/2511.20857
- 本地源：`papers/memory_evolution/2511.20857/`
- 官方代码：https://github.com/zhaosnw/evo_mem
- 核心内容：把 10 个数据集组织为顺序 task streams，统一多种 memory modules；ExpRAG 复用历史经验，ReMem 采用 action–think–memory refine pipeline 持续更新记忆。
- 对当前项目的直接关系：
  - 是 streaming/test-time evolution protocol 和 ReMem baseline 的直接来源。
  - CMD 的 Exp24 prequential arm 应对齐其“先预测后更新”语义，并明确 case order、effective-after boundary 与跨 case 泄漏检查。
  - ReMem 并非简单的“离散固定操作库”：它包含 LLM 驱动的 memory refine。CMD 的优势应聚焦 deterministic structured operators、可审计 lineage 与 multi-skill competition。

#### 4. A-MEM: Agentic Memory for LLM Agents

- ID：`arXiv:2502.12110`（2025-02-17）
- 作者：Wujiang Xu、Zujie Liang、Kai Mei、Hang Gao、Juntao Tan、Yongfeng Zhang
- 官方页面：https://arxiv.org/abs/2502.12110
- 本地源：`papers/memory_evolution/2502.12110/`
- 官方实现：https://github.com/agiresearch/A-mem
- 评测代码：https://github.com/WujiangXu/AgenticMemory
- 核心内容：受 Zettelkasten 启发，把 memories 组织为带 context、keywords、tags 和 links 的动态网络；新 memory 可触发已有 memory 表征的更新。
- 对当前项目的直接关系：
  - 是“memory evolution”术语和动态组织机制的先例，但其演化是 agentic organization，不是 operator population 的 mutation/crossover/selection。
  - 可作为 fixed architecture / single memory-system baseline；不能直接作为 repair-policy ecology baseline。

### 3.2 Darwinian evolution 与 quality-diversity

#### 5. Illuminating Search Spaces by Mapping Elites

- ID：`arXiv:1504.04909`
- 作者：Jean-Baptiste Mouret、Jeff Clune
- 官方页面：https://arxiv.org/abs/1504.04909
- 本地源：`papers/quality_diversity/1504.04909/`
- 核心内容：MAP-Elites 在用户定义的 behavior dimensions 上保留每个 cell 的高性能 elite，目标是得到多样且高性能的解集并“照亮”fitness landscape。
- 对当前项目的直接关系：
  - failure type、memory store、query domain 和 operator family 可以定义 behavior descriptors；skill × failure heatmap 本质上接近 illumination map。
  - 普通 top-half truncation selection 只优化全局 fitness，并不保证生态位保留。若 CMD 声称 ecology，至少应加入 niche-preserving 选择或解释为何只做观测性 ecology。
  - UMAP + gain contour 只是可视化；除非 descriptor 和 neighborhood 定义预注册且有统计检验，不能把图本身当作进化理论贡献。

#### 6. Promptbreeder: Self-Referential Self-Improvement Via Prompt Evolution

- ID：`arXiv:2309.16797`
- 作者：Chrisantha Fernando、Dylan Banarse、Henryk Michalewski、Simon Osindero、Tim Rocktäschel
- 官方页面：https://arxiv.org/abs/2309.16797
- 本地源：`papers/quality_diversity/2309.16797/`
- 核心内容：维护 task-prompt population，以 LLM 生成的 mutation-prompts 产生变异并按 task fitness 选择，同时让 mutation-prompts 自身演化。
- 对当前项目的直接关系：
  - 直接挑战“首次把进化算法用于 LLM 相关自改进即可构成 novelty”的论证。
  - CMD 的差异是演化对象为可执行、类型化、可 canonicalize 的 repair OperatorSpec，适应度为 case-level recovery gain，并要求跨 failure niches 的 population diversity。
  - 必须报告 invalid/no-op mutation rate、parent/child lineage、重复 genotype 比例和随机种子稳定性；否则 mutation/crossover 可能只是表面机制。

### 3.3 Gold-free surrogate、abstention 与风险控制

#### 7. Conformal Risk Control

- ID：`arXiv:2208.02814`（v4，2025-06-13）
- 作者：Anastasios N. Angelopoulos、Stephen Bates、Adam Fisch、Lihua Lei、Tal Schuster
- 官方页面：https://arxiv.org/abs/2208.02814
- 本地源：`papers/selective_risk/2208.02814/`
- 官方代码：https://github.com/aangelopoulos/conformal-risk
- 核心内容：对任意单调 loss 的期望值做 conformal risk control，并给出有限样本量级的校准误差；包含 NLP/open-world QA 示例。
- 对当前项目的直接关系：
  - CMD 的 margin threshold → coverage/agreement curve 与 selective prediction 相邻，但当前 gold-free analyzer 若仅事后使用全体 gold 计算曲线，不具备 conformal guarantee。
  - 可行的严格扩展：在独立 calibration families 上选择 margin threshold，以 disagreement indicator 作为 loss，然后在 untouched test families 上报告 coverage 和 risk；需要明确 exchangeability / shift 假设。
  - 在没有上述协议前，应使用“empirical agreement under abstention”，不要使用“risk-controlled gold-free selection”。

### 3.4 多技能分化与组合的相邻证据

#### 8. Diversity is All You Need: Learning Skills without a Reward Function

- ID：`arXiv:1802.06070`（v6）
- 作者：Benjamin Eysenbach、Abhishek Gupta、Julian Ibarz、Sergey Levine
- 官方页面：https://arxiv.org/abs/1802.06070
- 本地源：`papers/skill_specialization/1802.06070/`
- 核心内容：DIAYN 用互信息目标与 maximum-entropy policy，在没有下游 task reward 的情况下发现多样技能，并展示层次组合的可能性。
- 对当前项目的直接关系：
  - 提供“弱/非任务监督下可出现技能分化”的机制先例。
  - 它不涉及 memory repair、operator competition、winner/loser 记录或 chain deposition；只能放在 broader skill discovery related work，不能当作直接 baseline。
  - CMD 的 specialization_index、niche overlap 和 stability 需要由 repair outcomes 定义，并用 permutation/null baseline 验证不是 failure-frequency imbalance 造成的伪分化。

## 4. 与当前论文三条贡献的证据映射

| 当前主张 | 最近邻工作 | 仍需证明的独特部分 | 容易越界的措辞 |
|---|---|---|---|
| Competitive Skill Ecology | MAP-Elites；DIAYN；MemTrace taxonomy | 同一 failure 上 top-K 独立执行；winner/loser matrix；跨 failure niche specialization；round-to-round stability；相对 single/fixed/random controls 的 recovery 增益 | “首个生态系统”“spontaneous specialization”在无 null test 时过强 |
| Darwinian Operator Evolution | Promptbreeder；MAP-Elites；UMEM | typed OperatorSpec 的 mutation/crossover validity；population selection；genealogy；niche-conditioned fitness；对 Lamarckian versioning 的严格对照 | “memory evolution 首次使用进化”与既有 self-evolving memory 文献冲突 |
| Gold-Free Identifiability | MemTrace 的 supervised attribution；Conformal Risk Control | no-gold execution provenance；shadow gold scoring；按 failure type agreement；margin/abstention；held-out-family calibration；hard-case taxonomy | “sufficient”“identifiable”“guaranteed”需要定理或独立校准，不可只靠 >90% 经验值 |
| Repair Chaining | DIAYN 的层次技能组合（仅相邻） | A→B 的真实顺序执行；相对 max(A,B) 的 chain benefit；operator-family 约束；重复发现与沉积；冲突检测 | “生态自然产生组合链”在只枚举 pair 时更像搜索，不是 emergence |

## 5. 建议的实验与叙事修正

### 5.1 Baseline taxonomy

不要把所有相关方法都描述成“单一固定策略”：

- UMEM：learned single Mem-Optimizer，joint extraction-management，GRPO；
- ReMem：LLM-driven Think–Act–Refine pipeline；
- MemTrace：operation-graph attribution + attribution-guided prompt optimization；
- A-MEM：动态 memory network organization；
- CMD：population of deterministic structured repair operators，per-case competitive execution，gold-free runtime signal，explicit lineage。

这一表述能把差异放在 **optimization unit、supervision、interaction topology 和 observability** 上，而非弱化 baseline。

### 5.2 Ecology needs null models

至少增加：

1. 保持 failure-type frequency 不变、随机置换 winner label 的 specialization null；
2. 对 skill identity 随机置换的 niche-overlap null；
3. bootstrap family-level confidence intervals，而不是把 variants 当独立样本；
4. round-to-round Jensen–Shannon divergence 与随机种子重复；
5. global truncation selection 对比 niche-preserving selection，检验生态多样性是否真有功能收益。

### 5.3 Gold-free analysis should separate discovery and confirmation

- discovery/calibration families：选择 margin threshold 或拟合 abstention policy；
- confirmatory held-out families：只评估已冻结 threshold；
- 同时报告 coverage、top-1 agreement、expected supervised regret、ties、hard cases；
- 按 failure type 报告样本数与 family-level interval；
- gold 只进入 shadow evaluator，不进入 repair execution、retrieval、mutation 或 selection state。

### 5.4 Darwinian comparison should be mechanism-faithful

- `no-update`：种群固定且不提交新 revision；
- `random-skill`：从同一 candidate set 随机选；
- `fixed-library/single-skill`：固定 canonical OperatorSpec；
- `Lamarckian versioning`：成功 case 直接沉积 revision，无 mutation/crossover population；
- `Darwinian global`：mutation/crossover + global selection；
- `Darwinian QD`（推荐）：按 failure/operator descriptors 保留 elites。

所有臂应共享 candidate execution budget、case order、evaluator 和随机种子集合。

## 6. 参考仓库

本轮按“官方/作者源、直接可参考”筛选。遵照收敛要求，只将最直接的 MemTrace 仓库浅克隆到本地，其余保留 verified URL，避免继续扩大下载面。

| 仓库 | 状态 | 用途 |
|---|---|---|
| https://github.com/zjunlp/MemTrace | 已浅克隆至 `repos/MemTrace/` | failure taxonomy、trace schema、attribution/optimization pipeline |
| https://github.com/zhaosnw/evo_mem | 已验证 URL，未克隆 | streaming protocol、ReMem/ExpRAG baseline |
| https://github.com/agiresearch/A-mem | 已验证 URL，未克隆 | agentic memory organization |
| https://github.com/WujiangXu/AgenticMemory | 已验证 URL，未克隆 | A-MEM paper evaluation |
| https://github.com/aangelopoulos/conformal-risk | 已验证 URL，未克隆 | calibration/risk-control implementation |

仓库边界：

- `zjunlp/MemTrace` 当前仓库可访问，但论文仍标注为 ongoing work；应记录使用的 commit 后再做复现。
- UMEM arXiv 页面仅承诺未来公开代码/模型，本轮未确认正式仓库。
- MAP-Elites 有多种第三方实现；为避免把非作者实现误标成官方，本轮未克隆。

## 7. 建议阅读顺序

1. **MemTrace**：先统一 failure、trace、attribution 与 repair 的问题定义。
2. **Evo-Memory/ReMem**：理解流式 test-time evolution protocol 和最接近的 memory-evolution baseline。
3. **UMEM**：校准“单 policy joint optimization”这一强对手及计算预算差异。
4. **MAP-Elites**：决定 CMD 是只做 ecological measurement，还是加入 niche-preserving population algorithm。
5. **Promptbreeder**：设计 mutation/crossover/selection、lineage 和无效变异审计。
6. **Conformal Risk Control**：把事后 abstention curve 升级为独立 calibration/test protocol。
7. **A-MEM**：补齐 memory evolution/organization related work。
8. **DIAYN**：谨慎使用其无监督技能分化与组合概念，明确任务差异。

## 8. Corpus 校验

- 论文目录：8
- 分类目录：4
- arXiv 源码：8/8 成功解包
- 论文文件总数（收敛检查时）：250
- 本地参考仓库：1（shallow clone）
- 核心源码与测试：本阶段未修改

本地目录：

- `papers/memory_evolution/`
- `papers/quality_diversity/`
- `papers/selective_risk/`
- `papers/skill_specialization/`
- `repos/MemTrace/`

