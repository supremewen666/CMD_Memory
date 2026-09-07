# CMD Phase 1 深度调研：直接竞品精读与四维交集判定

调研日期：2026-08-17
调研对象：5 篇直接竞品（正文精读）+ 8 篇外围竞品（一句话定位）
目标：为总声明 C1（gold-free 记忆修复进化的**可识别性**）建立差异化定位

## 0. 结论摘要

**四维交集（gold-free × 质量故障 × 反事实归因+修复 × 可审计账本）未被任何一篇占满。**

最接近的是 MemAudit（2605.23723）——它同时具备"反事实归因 + 修复"和"免标签"，是唯一在归因维度上与 CMD 正面相交的工作。但它在另外三维全部缺位：故障类型是安全攻击（投毒）而非质量故障；免的是*投毒标签*而非 gold（其 CMIS 需要外部给定 harmful event 且需要 harm scorer `h(·)`）；修复动作空间只有"删除"一种；无任何账本/封存治理机制。

**"ledger" 一词在五篇正文中出现 0 次**（逐篇 grep 确认）。可审计账本维度是完全空白的——这是 CMD 最干净的差异点，但也意味着它是**审稿人最陌生、最需要论证其必要性**的维度。

证据来源等级：
- **A 级（本地 PDF 正文全文提取）**：2605.23723 MemAudit、2607.16621 MSCE、2608.12720 ERSkill、2608.01739 CoEvo-Mem
- **B 级（本地 PDF 损坏，arXiv HTML 全文补齐）**：2607.12790 Double Ratchet。本地 `paper.pdf` 的 xref 表损坏（`pdftotext` 与 ghostscript 均无法解析，报 `/undefined in --runpdf--`），已从 `arxiv.org/pdf/2607.12790v1` 抓取全文正文。所含 Method / Problem Setup / Results 节完整，公式为 LaTeX 源码形式，可信度等同正文。

---

## 1.1 核心方法对比表

### 验收判据落位

- ✅ **每篇"反馈信号从哪来"** → 见下表第 3 行，逐篇独立成行
- ✅ **CMIS 公式（数学形式，MemAudit 正文原文）** → 见 §1.1.1
- ✅ **Double Ratchet anchor 协议细节（集大小 / consensus 正则 / held-out audit 执行方式）** → 见 §1.1.2

### 主表

| 维度 | MemAudit (2605.23723) | Double Ratchet (2607.12790) | MSCE (2607.16621) | ERSkill (2608.12720) | CoEvo-Mem (2608.01739) | **CMD（本项目）** |
|---|---|---|---|---|---|---|
| **一句话定位** | post-hoc 投毒记忆审计：反事实归因 + 结构异常检测融合排序后删除 | metric 与 skill 库共进化：把评价指标本身当作可进化对象 | training-free 记忆→skill 共进化治理，三级记忆 L1/L2/L3 | 检索行为即可执行 skill，router 选 skill，双前沿治理 | 检索策略与记忆图闭环共进化，frozen LLM 出先验 + residual router 纠偏 | gold-free 记忆修复进化的**可识别性**：单通道不可识别（负结果）+ 多通道 typed telemetry 构造性可识别（正结果） |
| **优化对象** | 记忆存储 M 的子集 S（删除集） | 评价指标 M（表达式树）+ skill 库，二者交替 | 三级记忆 + skill 库（不动基座 LLM） | skill 集（原语序列）+ router 参数 θ | router 参数 θ + Typed Relational Memory Graph | 记忆项本身（6 个 effect：replace/demote/suppress/annotate_conflict/verify/abstain） |
| **★ 反馈信号从哪来** | **外部给定的 harmful event `e=(q*,y*,R*)` + harm scoring function `h(·)`**。信号 = 逐条记忆 replay 删除后的 harm 下降量。免投毒标签，但不免 harm 判定器与失败事件的外部输入 | **10 条 golden reference 的 dev 集（唯一监督信号）+ 大规模无标注 train 集上的 detector 多数投票 consensus**。原文：*"a teacher compares candidate outputs against it to emit soft pass/fail labels, the only supervised signal any loop reads"* | **稀疏终局反馈 `R_i`（quantitative human feedback / environmental feedback）+ 稠密 step-level self-reflection**，经 reflection-weighted value backfilling 回填 | **task 分数 `r(q,κ)∈[0,1]`，实例化为 LLM-as-a-Judge accuracy**。原文：*"we measure performance by a score r(q,κ)∈[0,1], instantiated as LLM-as-a-Judge accuracy in our experiments"* | **task reward `R_t = Eval(y_t; Y_t) ∈ [0,1]`**，由 task-specific evaluator 给出，经 score-function estimator 穿过不可微检索过程 | **recovery gain Δk**（修复后答案质量提升）+ **零 LLM 调用 typed telemetry 四通道**：`valid` / `rolled_back` / `changed_item_count` / `locality_cost` |
| **信号获取成本** | `O(|R*|)` 次 LLM replay，每次一轮完整 agent 重放（Algorithm 1 内层循环 `for each memory m_i ∈ R*_t`） | metric 侧 op 分三档：static（解析）/ execution（沙箱或数仓）/ judge（1 次窄问题 LLM 调用）；op 结果缓存，表达式求值是查表 | 每 episode 一次 reflection scorer prompt `Π_reflexion_score` + 终局反馈 | 训练期每个 skill × 每个 query 全量 rollout（`executes every skill in C_t on every query`），judge 打分 | 每次 interaction 一次生成 + 一次 Eval | **typed telemetry 四通道 0 次 LLM 调用**；surrogate gap 用于度量代理与 gold 的偏差 |
| **归因机制** | **CMIS：逐条记忆 leave-one-out replay 反事实**（真反事实，但需 replay 接口）+ CAS 结构异常图（DeBERTa-v3 NLI 判矛盾） | 无记忆归因。归因发生在 metric 层：per-op fitness = incumbent 表达式的 leave-one-out marginal | **不做干预反事实**。信用分配靠 self-reflection 加权：`V(f)=α·R_i+(1−α)γ·V(f_{t+1})`，α 由 LLM 打分 | 无归因。靠 oracle 覆盖 `g_K(q)=max_κ r(q,κ)` 的前沿重算 | 无记忆归因。节点存 `Q_t(m)` = 折扣下游检索效用，由 outcome-conditioned credit 更新 | Tier 3 单点反事实扫描（穷举 legal step actions）+ typed telemetry 逼近 replay-CMIS |
| **修复动作空间** | **仅删除**（`min_S HarmAfter(M\S)`，输出 removal set S） | 不修复记忆；修复的是 metric（加 detector）与 skill 库 | 记忆写入/策略 induction/skill 晶化；无"纠错"动作 | 无修复；只有 skill 增删（前沿保留/淘汰） | 无修复；记忆图节点效用更新 + 边演化 | **6 个 effect**：replace / demote / suppress / annotate_conflict / verify / abstain |
| **故障类型** | 安全攻击（MINJA 交互式记忆投毒） | 任务失败（代码/SQL/报告质量） | 能力缺失（长程任务不会做） | 检索能力不足 | 检索路由次优 | **质量故障**（过时 / 冲突 / 粒度 / 检索错） |
| **治理 / 审计机制** | 无。batch 审计（避免顺序效应）不等于账本 | **anchor discipline + outer audit**：locked test 集永不被任何 loop 读取；reference-free 任务外挂独立 LLM judge 双向位置交换配对比较。**无 ledger、无 append-only、无 tamper 检测** | skill lifecycle：probationary → active → archived | double frontier：capability frontier（探索）与 deploy frontier（部署）解耦 | 交替固定一侧（router phase / memory phase）缓解耦合非平稳 | **append-only 哈希链 `EcologyLedger`（`previous_event_sha256` 链式）+ `GhostEcology(evaluation_only=True)` 封存评估模式**（读 discovery 即 `PermissionError`） |
| **基座模型** | GPT-4o / GPT-4o-mini / DeepSeek | frozen solver；composer/synthesizer 用 LLM | frozen LLM（GPT-5.2-nano、GPT-4o-mini） | frozen；Qwen3-Next-80B-A3B-Instruct、GPT-5.4-nano，judge 用 GPT-4o-mini | **frozen answering LLM F**；只训 residual router | frozen；零调用通道无需模型 |
| **评测域 / 数据** | MINJA 两设定（QA / RAP），三模型 | MBPP+、Spider 2.0-Snow、reference-free report generation | EvoAgentBench（5 域）、LoCoMo | LoCoMo（+ 跨域直接复用 router） | 7 benchmark：LLAB(OS)、GPQA Diamond、BFCL、LoCoMo、LongMemEval 等 | 记忆质量故障 probe 数据集 + STALE item 层 |
| **头条数字** | QA ASR 70%→0%；RAP ASR 83.3%→0% | 保留 oracle lift 的 **106% (MBPP+) / 110% (Spider) / 88% (Report)** | EvoAgentBench Pass@1 领先；LoCoMo | 总均值 **+31.3%**(Qwen3-Next-80B) / **+28.1%**(GPT-5.4-nano) | 7 benchmark SOTA，最高 **+7.50pp**（GPQA Diamond） | Mix GHOST 零调用可识别性正结果 |
| **自述关键局限** | 后验非预防；依赖可观测失败信号与 replay 质量；**dense poisoning 下失效**（"Once poisoned memories begin to reinforce one another during retrieval, suspicious entries become harder to isolate through targeted removal alone"）；仅 MINJA 两设定 | 3 seed + 40–48 项 held-out"只支持粗略结论"；Spider 上 metric 与 gold 仅 0.500±0.026 一致度 | 反思信号可能不忠实；gain `G` 是启发式非因果效应估计（原文明说）；rollout/update 靠外部 LLM | 训练期全量 rollout 成本；router 需训练 | router 需训练；交替更新引入 phase 调度 | 单通道 gold-free 不可识别（已确证负结果） |

### 1.1.1 CMIS 公式（MemAudit 正文 §4，Eq. 7–10 原文抄录）

记忆存储 `M = {m_1, m_2, ..., m_n}`；检索 `R(q,M) ⊆ M`；输出 `y = f(q, R(q,M))`。
harmful event 形式化为 `e = (q*, y*, R*)`（Eq. 4），其中 `q*` 是触发 query，`y*` 是观测到的有害输出，`R*` 是被检索的记忆上下文。

**Eq. 7（counterfactual memory influence score，即 CMIS）：**

```
CMIS(m_i) = h(q*, y*) − h( q*, f(q*, R(q*, M \ {m_i})) )
```

其中 `h(·)` 是 harm scoring function。原文解释：*"A larger CMIS value indicates that removing m_i leads to a greater reduction in harmful behavior, suggesting that this memory plays a stronger causal role in the observed failure."*

**Eq. 8（structural anomaly score，CAS）：**

```
CAS(m_i) = Σ_{m_j ∈ N(m_i)}  w(m_i, m_j) · sim(m_i, m_j)
```

`w` 捕捉矛盾/不一致（DeBERTa-v3 NLI 实例化），`sim` 是语义相似度。异常判定用 `CAS(m_i) > μ + 2σ`（Eq. 9）。

**Eq. 10（融合 detoxification score）：**

```
DS(m_i) = α · CMIS_norm(m_i) + (1 − α) · CAS_norm(m_i)
```

**对 CMD 的三个直接含义：**

1. **CMIS 是 CMD 零调用代理的被逼近对象**，且成本可直接对比：Algorithm 1 内层 `for each memory m_i ∈ R*_t: Replay the agent with M\{m_i}` 是 `O(|R*|)` 次完整 agent replay。CMD 的 typed telemetry 是 0 次调用。这是一个**量级差而非常数差**的对比，可直接进正文成本表。
2. **CMIS 的 `h(·)` 与 `e=(q*,y*,R*)` 都是外生输入**。MemAudit 的"免标签"是免*投毒标签*，不是免*评判器*。CMD 的 hook 门自触发（`valid`/`rolled_back` 内生）在这一点上更强，但 CMD 的 `_rollout_score` 仍读 gold（见 §1.2 诚实性标注）。
3. **CAS 的少数派假设是 dense poisoning 失效的根因**：CAS 假设*"benign memories typically lie in semantically coherent neighborhoods, whereas poisoned memories are more likely to appear weakly connected or inconsistent"*。CMD 的 item_gate 走对比散度而非少数派假设，这是一个可实证的边界优势点。

### 1.1.2 Double Ratchet anchor 协议细节（正文 Problem Setup / The Metric Loop）

**三分割协议（每一分割各带一个"twist"）：**

| 分割 | 规模 | 角色 | 谁能读 |
|---|---|---|---|
| **train** | large, **unlabeled** | 暴露候选 metric 在哪分歧或弃权；共进化时演化后的 metric 在这里给 skill loop 打分 | metric loop + skill loop |
| **dev（anchor）** | **ten items in all experiments** | 每个 task 带 golden reference，teacher 对比候选输出发 soft pass/fail 标签 | 唯一被读的监督信号 |
| **test（locked）** | held out，40–48 项 | 最强 reference（MBPP+ 单测 / Spider 官方执行比对 / 报告 rubric 分） | **no loop ever reads it** |

**anchor 集大小 = 10**，原文明确：*"dev, tiny and anchored (ten items in all experiments)"*，并给出设计理由：*"The dev set is deliberately small: sparse anchoring is the realistic regime, since fresh labels in deployed systems arrive as a trickle of demos and incident reviews."*

**anchor 的关键限定（避免被误读为 metric）**：*"The references are not themselves a metric: they grade only the ten tasks that carry them, while the evolved metric must grade arbitrary outputs on tasks that have none."*

**consensus 正则（Eq. 2 选择目标）：**

```
S(e) = A_dev(e) · A_train(e)^w − λ · C(e)
```

- `A_dev(e)`：与 dev soft labels 的一致度
- `A_train(e)`：与无标注 train 输出上 **opining pool ops 多数投票 verdict** 的一致度 ← **这就是 consensus 正则项**
- `C(e)`：表达式规模；`w, λ` 为小常数

正则作用原文：*"a metric that memorizes ten dev items but behaves erratically on the broad distribution loses, and a degenerate always-fail metric loses on dev."*

**两个 guard：**
1. **fail-closed selection**：在 dev 上没有可用意见的候选不可被选中
2. **validity gate**：在所有样本上全 pass、全 fail 或全 abstain 的候选被丢弃

**held-out audit 怎么执行：**
- Algorithm 1 第 12–13 步：*"Audit (measurement only, never a training signal) / report agreement against the locked test set"*
- 共进化时：*"The skill loop's held-out evaluation and rollback anchor stay pinned to the locked test set, which the metric never reads, so a corrupted metric can degrade skill learning but cannot corrupt the measurement."*
- reference-free 任务额外外挂：更强 LLM 对每个最终输出与 pre-evolution baseline 做**配对比较，位置交换判两次，只有两个顺序都一致才算一次 win**。原文定位：*"The judge is an audit, not a training signal."*

**op 生命周期（与 anchor 挂钩的准入纪律）：**
- birth gate：新 op 须在其 cluster 上**至少触发一半**且在 known-good 输出上保持 clean
- **anchored 出身的 op 直接 active（可被选入表达式）；只由无标注 gap 出身的 op 进入 shadow**（跑并记录 verdict 但不可被选中），因为*"nothing yet shows they track quality rather than consensus noise"*；shadow 提升为 active 需其 verdict 可证地提高 dev 一致度
- per-op fitness = incumbent 表达式的 leave-one-out marginal；非正贡献的 op 过宽限期后退休

**摘要要点修正（1 处）：**
> 已知摘要要点写"去掉 anchor guard 则 metric 退化为 vacuous detector（去掉 lifecycle 则不会）"——**正文核实为真**，且补充了一个反直觉细节：anchor-guards-off 消融（`co naive`）的**任务分数反而更高**（MBPP+ 0.742 vs co 0.717，Spider 0.500 vs 0.458，Report 0.841 vs 0.812）。原文对此的解释是 *"whose metric is vacuous (†; Table 3), so its higher score reflects unfiltered practice, not a working grader"*。
> **对 CMD 的直接警示**：一个 gold-free 闭环"分数变高"本身不能证明机制在工作。CMD 的 D1 全零负结果与这条同源。CMD 若要主张 telemetry 通道有效，必须像 Double Ratchet 一样**在 metric 有效性（而非任务分数）上做消融**——这是可以直接借用的实验设计纪律。

---

## 1.2 三行差异表

### 验收判据落位

✅ 每个竞品在三行中至少一行与 CMD 不同，且引用其原文句子（引号 + 节号）。

### 行 1：信号

| 竞品 | 其信号 | 原文句子（节号） | 与 CMD 是否不同 |
|---|---|---|---|
| MemAudit | replay（`O(|R|)` 次 LLM 重放） | *"we estimate the contribution of each retrieved memory by removing it from the memory store and replaying the event"*（§4 Methodology） | **不同**：CMD 用 0 次调用 typed telemetry 逼近同一量 |
| Double Ratchet | anchor（10 条 golden reference） | *"each task carries a golden reference, and a teacher compares candidate outputs against it to emit soft pass/fail labels, the only supervised signal any loop reads"*（§3 Problem Setup） | **不同**：gold-sparse 而非 gold-free；CMD 无 anchor 集 |
| MSCE | self-reflection backfill | *"MSCE couples these signals by reflection-weighted value backfilling"*；`V(f_{i,t}) = α_{i,t} R_i + (1−α_{i,t}) γ V(f_{i,t+1})`（§4.4 Eq. 2） | **不同**：CMD 用干预反事实，不用反思加权。且 MSCE 自述 gain 是启发式：*"We use G as a heuristic signal rather than a causal effect estimate"*（§4.2） |
| ERSkill | task reward（LLM-as-a-Judge） | *"we measure performance by a score r(q,κ)∈[0,1], instantiated as LLM-as-a-Judge accuracy in our experiments"*（§2.4 Capability frontier update） | **不同**：CMD 的 Δk 是修复前后差值，非绝对任务分 |
| CoEvo-Mem | task reward | *"a task-specific evaluator returns R_t = Eval(y_t; Y_t) ∈ [0,1]"*（§ Preliminary, Eq. 1 后） | **不同**：同上 |

**CMD 侧诚实性标注（必须写进正文，否则这一行站不住）：**
CMD 的 Δk **构造是 gold-free**（修复上下文是 `(recall_set, pipeline_action)` 的纯函数，从不读 `case.gold_*`），但**选择不是 gold-free**——`_rollout_score → rollout_to_terminal(..., gold_answer, answer_verifier)` 按 `score_answer_with_verifier(answer, gold_answer)` 排序动作，且 `_compute_recovery_gain` 在 gold 为假值时短路返回 `0.0`。
因此与 Double Ratchet 的对比必须精确表述为：**Double Ratchet 是 gold-sparse 的 metric 演化（10 条 anchor），CMD 是 gold-free 的 telemetry 通道 + 需注入 reference-free verifier 的选择**。若把 CMD 笼统说成"全流程 gold-free"，Double Ratchet 的 §3 三分割协议会成为审稿人手里最锋利的反例——他们已经把"唯一监督信号在哪"写得比 CMD 现状更清楚。

### 行 2：对象

| 竞品 | 其对象 | 原文句子（节号） | 与 CMD 是否不同 |
|---|---|---|---|
| MemAudit | **删投毒**（仅删除） | 目标函数 `min_S HarmAfter(M\S)`（§3 Preliminary, Eq. 6）；*"post-hoc memory repair as a ranking problem over candidate memories"*（§4 末） | **不同**：CMD 有 6 个 effect（replace/demote/suppress/annotate_conflict/verify/abstain），删除只是其中 suppress 一种 |
| Double Ratchet | 完成任务（+ 演化 metric） | *"the evolved metric grades train attempts, and failed attempts become capsules whose error text feeds skill synthesis"*（§4.4 Co-Evolution） | **不同**：不修记忆项 |
| MSCE | 完成任务（能力获取） | *"MSCE crystallizes evidence-backed L2 policies with positive estimated gain into skills"*（§4.1 Overview） | **不同**：晶化新技能 ≠ 纠正错误记忆项 |
| ERSkill | 完成任务（检索能力） | *"ERSkill enables adaptive query-time memory access through skill selection and execution"*（§2.3 末） | **不同**：改的是*怎么取*，不改*取到的内容对不对* |
| CoEvo-Mem | 完成任务（路由 + 图效用） | *"a node m ∈ V_t stores an experience and an estimate Q_t(m) of its discounted downstream retrieval utility"*（§ Preliminary） | **不同**：更新的是节点效用估计与边，不改记忆内容正确性 |

**这一行是 CMD 最强的差异行**：五篇里只有 MemAudit 碰"修记忆"，而它只有一个动作（删除）。ERSkill/CoEvo-Mem 改检索路径，MSCE/Double Ratchet 加技能——都不回答"这条记忆本身错了怎么办"。

### 行 3：治理

| 竞品 | 其治理 | 原文句子（节号） | 与 CMD 是否不同 |
|---|---|---|---|
| MemAudit | **无**（batch 审计不是账本） | *"Memory is not updated during auditing. All harmful events are analyzed against the same underlying memory state"*（§4 末）——这是避免顺序效应的批处理，非可审计账本。全文 "ledger" 0 次 | **不同**：CMD 有哈希链 append-only ledger |
| Double Ratchet | **anchor discipline + outer audit**（最接近，但非账本） | *"a locked set is never read by any loop and purely reports transfer"*（§1 Introduction）；*"The judge is an audit, not a training signal"*（§4.6） | **部分不同**：封存思想同源（locked set 永不被读 ≈ CMD `evaluation_only=True`），但**无 append-only、无哈希链、无 tamper 检测**。全文 "ledger"/"append-only"/"tamper" 均 0 次 |
| MSCE | skill lifecycle 三态 | *"Lifecycle management: probationary → active → archived"*（Figure 1） | **不同**：生命周期状态机 ≠ 不可篡改事件账本 |
| ERSkill | double frontier | *"This separation allows ERSkill to explore new retrieval capabilities while exposing only stable skills for deployment"*（§2.4） | **不同**：探索/部署解耦 ≈ CMD frozen registry 的一半，但无账本 |
| CoEvo-Mem | 交替固定一侧 | *"alternating their updates while holding the other component fixed"*（§ Preliminary 末） | **不同**：非平稳性缓解手段，非治理审计 |

**治理维度的战略含义（两面）：**
- **正面**：这一维完全空白，CMD 独占。
- **负面**：正因为无人做，审稿人会问"为什么需要账本"。Double Ratchet 用一个**真实发生的 Goodhart 事件**（evolved skills 把 rubric 的 tag 计数器玩坏，约 30% 的 tag 没有数值，独立 judge 在 88% 的判定对里更偏好 baseline）来论证其 audit 层的必要性。**CMD 应当照抄这个论证结构**：用 STALE=Goodhart 机制这个已确证的负结果作为账本必要性的证据，而不是把账本当作工程洁癖来陈述。

---

## 1.3 交集空位结论段

### 验收判据落位

✅ 逐竞品列出其未覆盖的维度（四维一一对应）。

四维定义：
- **D1 gold-free**：反馈信号不依赖 per-case ground-truth 答案或人工标注
- **D2 质量故障**：目标故障是过时/冲突/粒度/检索错，非安全攻击、非能力缺失
- **D3 反事实归因 + 修复**：用干预式反事实定位责任，并对被定位对象施加修复动作
- **D4 可审计账本**：不可篡改的事件记录 + 封存评估模式

### 逐竞品维度覆盖

| 竞品 | D1 gold-free | D2 质量故障 | D3 反事实归因+修复 | D4 可审计账本 | **空缺维度** |
|---|---|---|---|---|---|
| **MemAudit** | **部分**：免投毒标签，但需外生 `e=(q*,y*,R*)` 与 harm scorer `h(·)` | **✗** 安全攻击（MINJA 投毒） | **✓** 唯一全覆盖者：CMIS 真反事实 + 删除修复 | **✗** 全文无 ledger | **D2、D4 空缺；D1 部分** |
| **Double Ratchet** | **✗** gold-sparse：10 条 golden reference 是唯一监督信号 | **✗** 代码/SQL/报告任务质量，非记忆故障 | **✗** 无记忆归因；仅 metric 层 LOO marginal，且不修记忆 | **部分**：locked set 封存 + outer audit，但无 append-only / 哈希链 / tamper 检测 | **D1、D2、D3 空缺；D4 部分** |
| **MSCE** | **✗** 需 quantitative human feedback / environmental feedback `R_i` | **✗** 能力获取（长程任务不会做） | **✗** 自述非因果：*"We use G as a heuristic signal rather than a causal effect estimate"*（§4.2） | **✗** lifecycle 状态机非账本 | **D1、D2、D3、D4 全空缺** |
| **ERSkill** | **✗** 依赖 LLM-as-a-Judge task 分数 | **✗** 检索能力不足 | **✗** 无归因；无记忆修复 | **✗** double frontier 非账本 | **D1、D2、D3、D4 全空缺（D4 有 frozen/deploy 分离的形似物）** |
| **CoEvo-Mem** | **✗** 依赖 `R_t = Eval(y_t; Y_t)` | **✗** 检索路由次优 | **✗** 无归因；`Q_t(m)` 是效用估计非反事实 | **✗** 交替更新非账本 | **D1、D2、D3、D4 全空缺** |
| **CMD** | **部分**：构造 gold-free + telemetry 零调用，但**选择读 gold**（须注入 reference-free verifier） | **✓** 过时/冲突/粒度/检索错 | **✓** 单点反事实扫描 + 6 effect 修复 | **✓** 哈希链 append-only `EcologyLedger` + `evaluation_only=True` 封存 | **D1 需加固（见下）** |

### 判定

**四维交集未被占满。**

- **无一篇覆盖 ≥3 维**。最高覆盖是 MemAudit 的 1.5 维（D3 满 + D1 半）和 Double Ratchet 的 0.5 维（D4 半）。
- **D2（质量故障）在五篇中 0 覆盖**——这是最干净的空位。MemAudit 做安全，其余四篇做任务能力/检索，没有一篇把"记忆内容本身在质量上出错"作为一等公民。
- **D4（可审计账本）在五篇中 0 覆盖**，Double Ratchet 的 locked-set 封存是唯一形似物。
- **D3 只有 MemAudit 覆盖**，但被 D2 错位（安全域）和单一动作（仅删除）双重限制。

### CMD 的真实风险（不在竞品覆盖上，在自身 D1 上）

四维交集虽空，但 **CMD 自己在 D1 上不满**。这是本次调研发现的最大投稿风险，且**风险来自 Double Ratchet 而非 MemAudit**：

Double Ratchet 已经把"无可靠 verifier 时怎么选"作为**已发表的解法**占位，并且它的 §3 把监督信号的位置写得极其干净（三分割 + "the only supervised signal any loop reads"）。CMD 当前 `_rollout_score` 读 `gold_answer` 排序动作，若正文含糊表述为"gold-free"，审稿人只需引用 Double Ratchet 的协议表就能指出 CMD 的监督信号位置没交代清楚。

**建议的表述加固**（三点，均可在不改代码的前提下完成）：
1. 把声明精确化为：**construction 是 gold-free（可验证：纯函数不读 `case.gold_*`）；selection 需要一个 ranking 信号，CMD 的贡献是证明 typed telemetry 四通道可以零调用地承担这个角色**。
2. 显式对标 Double Ratchet 的 anchor 光谱：Double Ratchet = 10 条 anchor 的 gold-sparse；CMD = 0 条 anchor 但需 reference-free verifier。**把二者放进同一张"监督信号预算"表**，这比回避更有说服力。
3. 借用 Double Ratchet 的消融纪律：**在通道有效性（而非任务分数）上做消融**。`co naive` 分数更高但 metric vacuous 这个结果，正是 CMD D1 全零负结果的同构外部佐证——可以引用它把 CMD 的负结果从"我们失败了"重新表述为"这是该领域已知的失效模式，我们给出了通道条件"。

---

## 2. 外围竞品（一句话定位，未精读）

来源：`logs/2026-08-17.md` 的 7–8 月扫描记录。以下仅作领地占位记录，未核实正文。

| arXiv ID | 名称 | 一句话定位 | 与 CMD 的关系 |
|---|---|---|---|
| 2607.05297 | MetaSkill-Evolve | 两时间尺度递归自改进（task skill 快环 + meta-skill 慢环，同管线自作用）；OfficeQA/SealQA/ALFWorld +23.5/+16.1/+1.9 | 占"递归性"名分；无故障归因 |
| 2607.22529 | Skill Self-Play | self-play 出题–解题–记忆循环 | 无归因、无记忆修复 |
| 2607.29468 | Self-Play Meets Skill Evolution | 同上，self-play 驱动技能演化 | 同上 |
| 2608.07449 | SkillProx | proximal textual gradient descent 演化 skill | 演化机制层，非记忆域 |
| 2608.12486 | DIVE | 冻结模型上多样性驱动 skill 进化 | 多样性目标 ≠ recovery 目标 |
| 2608.13120 | SkillEvo | 多轮交互反馈自更新进化梯度 | 依赖交互反馈信号 |
| 2608.01234 | （未命名） | 记忆 vs 参数内化的自适应协调 | 记忆/参数分工，非故障修复 |

**外围竞品的整体含义**：7–8 月这批工作把"skill 演化"的**机制空间**几乎填满（self-play、textual gradient、diversity-driven、two-timescale）。CMD 不应在"演化机制新颖性"上竞争——该赛道已拥挤。CMD 的可辩护点是**被演化的对象**（记忆修复算子）与**信号的成本结构**（零调用 typed telemetry），而非演化算法本身。这与 `logs/2026-08-17.md` 的结论一致。

---

## 3. 回答三个交付问题

### (1) 四维交集是否被占满

**未被占满。** 无一篇竞品覆盖 ≥3 维（满分 4 维）。D2（质量故障）与 D4（可审计账本）在五篇中零覆盖；D3 仅 MemAudit 覆盖且被安全域错位与单一删除动作限制。

**但真实风险在 CMD 自身的 D1**：Double Ratchet 已发表 gold-sparse 解法并把监督信号位置写得比 CMD 现状更清楚，而 CMD 的 `_rollout_score` 仍读 `gold_answer`。投稿成立的前提是把声明精确切分为"construction gold-free / selection 用零调用 telemetry 承担 ranking"，而不是笼统主张全流程 gold-free。

### (2) 表格里最关键的三条差异

1. **信号成本是量级差，不是常数差。** MemAudit 的 CMIS 需 `O(|R*|)` 次完整 agent replay（Algorithm 1 内层循环），CMD 的 typed telemetry 四通道是 0 次 LLM 调用。CMIS 公式（Eq. 7）已抄录在案，这是可直接进正文成本表的硬对比，也是 C2（零调用反事实影响代理）最强的一条外部锚点。

2. **修复动作空间 1 vs 6。** 五篇中唯一修记忆的 MemAudit 只有"删除"（`min_S HarmAfter(M\S)`），CMD 有 replace/demote/suppress/annotate_conflict/verify/abstain 六个 effect。"这条记忆错了但不该删"（过时需 replace、冲突需 annotate_conflict）在竞品里无法表达——这是 D2+D3 交叉处最实的差异，且已在代码中验证（`cmd_audit/repair/parametric_policy.py:35`）。

3. **治理维度是 0 覆盖的独占，但也是最需要论证必要性的一维。** 五篇正文 "ledger" 出现 0 次；Double Ratchet 的 locked-set 是唯一形似物且无 append-only/哈希链/tamper 检测。CMD 的 `EcologyLedger`（`previous_event_sha256` 链式）+ `evaluation_only=True` 封存独占此维。论证方式应照抄 Double Ratchet 的做法：用**真实发生的 Goodhart 事件**证明审计层必要（他们用 tag 计数被玩坏 + 88% baseline 偏好；CMD 用已确证的 STALE=Goodhart 机制）。

### (3) 与已知摘要要点矛盾的事实

共 3 处修正、1 处证据等级说明：

**修正 1 — Double Ratchet 的 anchor-guards-off 消融，任务分数反而更高（摘要要点未提，会误导实验设计）。**
`co naive`（anchor guards 关闭）在三个任务上分数全面高于正常 co：MBPP+ 0.742 vs 0.717、Spider 0.500 vs 0.458、Report 0.841 vs 0.812。原文定性为 *"whose metric is vacuous (†; Table 3), so its higher score reflects unfiltered practice, not a working grader"*。
**影响**：这直接改变 CMD 的消融设计要求——一个 gold-free 闭环"分数变高"不能证明机制在工作，必须在**通道有效性**而非任务分数上做消融。同时它是 CMD D1 全零负结果的同构外部佐证，可用于把负结果重新表述为领域已知失效模式。

**修正 2 — MSCE 自述其 gain 不是因果效应估计（摘要要点的"共进化治理"表述掩盖了这一点）。**
原文 §4.2 明说：*"We use G as a heuristic signal rather than a causal effect estimate: it determines whether a policy remains active, becomes eligible for skill crystallization, or is retired."*
**影响**：MSCE 与 CMD 在归因维度（D3）上的距离比"措辞层面正面撞车"的判断更远。CMD 的干预反事实 vs MSCE 的启发式 gain 是可清晰划界的，`logs/2026-08-17.md` 里"与二级 skill 库正面撞车"的威胁评估可以下调。

**修正 3 — MemAudit 的"免标签"范围被摘要要点高估。**
免的是*投毒标签*（原文 §3：*"The auditor does not have access to ground-truth poison labels or attacker objectives"*），但 Algorithm 1 的输入显式包含 `harm scorer h`，且 harmful event `e=(q*,y*,R*)` 是外生给定。
**影响**：MemAudit 在 D1 上是"部分覆盖"而非"覆盖"。CMD 的 hook 门自触发在这一点上确实更强，可作为差异点；但同理，CMD 的 selection 读 gold 也必须同等诚实地披露，否则双标会被抓。

**证据等级说明 — Double Ratchet 为 B 级。**
本地 `papers/direct_competitors/2607.12790/paper.pdf`（1.3MB）的 xref 表损坏：Read 工具报 "PDF file is corrupted or invalid"，`pdftotext` 失败，ghostscript 报 `Error: /undefined in --runpdf--`。已从 `arxiv.org/pdf/2607.12790v1` 抓取全文正文补齐，Problem Setup / Metric Loop / Co-Evolution / Ablations / Results 各节完整，公式为 LaTeX 源码形式。所有 Double Ratchet 引用句均出自该全文。**建议重新下载该 PDF 以便后续引用页码。**
