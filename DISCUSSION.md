# DISCUSSION.md — 设计讨论记录

本文件记录跨会话的设计讨论，供后续决策参考。每节标注日期和状态。

---

## G-Eval 双轴评分机制设计（2026-05-29，进行中）

### 背景

D37 R4 将离线评分机制重新开放：当前 evidence axis 用 `g-eval-hybrid`（logprob + rubric fallback），answer axis 用二元 `AnswerVerifier`（EQUIVALENT/NOT_EQUIVALENT），整体过粗，无法支撑论文 headline 的有效性证明。本次讨论目标是为两个轴设计低方差、粒度合适的 G-Eval 评分机制。

### LLM 评分方差来源

LLM 评分方差有两个独立来源：

1. **采样噪声**：temperature > 0 时同一输入产生不同 token，binary PRESENT/ABSENT 每次调用都可能翻转。
2. **边界模糊**：rubric 0-4 的离散边界本身是歧义区，模型在 2/3 边界上的判断不稳定。

G-Eval（Liu et al. 2023）的解法：不从分布里采样一个 token，而是读 top_logprobs 的概率分布算期望值 E[score] = Σ p(token_i) × score_i。同一输入永远返回同一浮点数，方差降到零（给定相同 endpoint 权重）。

### Evidence Axis 设计

**评分框架：Atomic-Claim Coverage（分步执行）**

不在单次 LLM 调用里做"分解 + 判断"（会引入额外方差），而是：

1. gold_evidence 已经是预分解的 atomic facts（每个 `GoldEvidence` item 是一个原子命题）
2. 每个 fact 单独调用一次 subagent，判断是否在 text 中出现
3. 聚合：mean(per-fact score)

**评分方向确认**：evidence axis 是 recall 方向——gold evidence 的每个 fact 是否在 replay output 中被回收。这是 CMD attribution 的核心度量（"counterfactual replay 是否恢复了缺失的证据"）。

**G-Eval 机制**：对每个 fact-text pair，用 `_continuous_verify` 读 score token 的 logprob 分布，计算 E[score] ∈ [0, 4]，归一化到 [0, 1]。比 binary PRESENT/ABSENT 更细粒度，比 rubric 离散采样方差更低。

**现有实现**：`_RUBRIC_SYSTEM_PROMPT`（0-4 rubric）+ `_continuous_verify`（logprob G-Eval）已在 `cmd_audit/scoring/llm.py` 实现，`RubricScorer.score_continuous` 是当前 evidence axis G-Eval 路径。

**待讨论**：`_RUBRIC_SYSTEM_PROMPT` 的 guidance 用 subject/verb/object triple 作为评估单元，适合结构化单命题 fact。对于时序型（"事件发生在周二"）或关系型（"Alice 是 Bob 的上级"）的 evidence，SVO triple 是否足够覆盖——待 W2 实测验证。

### Answer Axis 设计

**适用范围澄清（重要）**

Answer axis 在 CMD 中只在两处有意义：

1. **离线 at-scale re-test**（`run_at_scale_llm_retest.py`）：`agent_generate` + `answer_verifier` 同时传入，`evidence_given_reasoning` replay 的 recovery_gain 用 answer axis 计算（`_apply_dual_axis_recovery_gain` 中唯一走 answer axis 的 replay）。
2. **Post-Repair Context Replay**：repair 后 agent 生成答案，`AnswerVerifier` 判断 `recovered`/`partial`/`failed`。

**在线 hook 路径不存在 answer axis**：hook 在 retrieval 之后、generation 之前触发，此时 LLM 尚未生成答案，无法评分。10 个 replay 的 recovery_gain 全部走 evidence axis。

**G-Eval 机制**：answer axis 的 G-Eval 目标是判断 agent 生成的答案与 gold_answer 的语义等价程度。输入为 `(agent_answer, gold_answer)`，用 `_ANSWER_RUBRIC_SYSTEM_PROMPT`（0-4 rubric）+ logprob G-Eval 路径，返回连续 [0, 1] 分数。

`**_ANSWER_RUBRIC_SYSTEM_PROMPT` 已写入**（2026-05-29，`cmd_audit/scoring/llm.py`），rubric anchors：

- 0 = 错误或无关：答案与 gold_answer 矛盾，或回答了完全不同的问题
- 1 = 模糊：方向正确但不包含任何具体事实内容
- 2 = 部分：包含部分关键事实，但缺失重要信息或有重大错误
- 3 = 基本正确：核心事实内容保留，有轻微遗漏或不同措辞
- 4 = 正确：与 gold_answer 事实等价，措辞可不同，额外非矛盾细节不扣分

Guidance 强调：ANSWER 中的矛盾会降分，即使其他事实正确；额外非矛盾细节不降分；不确定时保守取低分。

**待实现**：`_continuous_verify_answer` 函数 + `AnswerRubricScorer` 类（使用 `ANSWER/GOLD ANSWER` 用户消息格式，与现有 `AnswerVerifier` 格式一致）。

### 接口设计决策

`**AnswerRubricScorer.verify` 返回 float**

`score_answer_with_verifier` 已处理 `isinstance(verdict, (int, float))` 分支，直接返回 float [0, 1] 可无缝接入现有路径，无需修改 `_scoring_bridge.py`。

`**build_answer_verifier` 模式扩展**

现有 `rubric` 模式是用 evidence scorer 包装 gold_answer 的 hack（`_build_rubric_answer_callable`）。新增 `"answer-rubric"` 模式直接返回 `AnswerRubricScorer`，替代 hack 路径。旧模式保留向后兼容。

**Fallback 链**

logprobs 不可用时（endpoint 不支持）→ discrete rubric（`_ANSWER_RUBRIC_SYSTEM_PROMPT` + `_parse_rubric_output`）→ 失败返回 0（保守 tie-break，与 evidence axis 策略一致）。

### 尚未实现的部分

- `_continuous_verify_answer(client, answer, gold_answer)` 函数
- `AnswerRubricScorer` 类（`verify` 方法返回 float [0, 1]）
- `scoring/__init__.py` 导出 `AnswerRubricScorer`
- `build_answer_verifier` 新增 `"answer-rubric"` 模式

### 相关决策

- D37 R4：离线评分机制重新开放，g-eval-hybrid 为临时默认，不锁定论文 headline
- D34 R2：Post-Repair Context Replay 必须跑 agent，`AnswerVerifier` 接入 `recovered` 判定
- D36：evaluator model = mistral-7b-instruct-v0.3
- Issue 0019 Phase B/C：SubagentScorer + RubricScorer 现有实现基础

---

## Step-Level 反事实归因 + 单玩家 MCTS（2026-05-31，进行中）

### 缘起：业界记忆机制调研 → item-wrong 缺环

读 Claude Code / Codex 源码后确认（见 CONCLUSION.md）：记忆注入是 per-turn 从缓存读取，
一个 turn（含多跳）内 system prompt 与注入记忆稳定不变，变的只是每跳把 tool/检索结果拼回上下文。

由此暴露 CMD 当前的缺环。现有流程只诊断**检索决策**（post-retrieval：拿了哪条记忆），
不诊断**记忆内容本身是否正确**（item-level）。若记忆内容本身错误，10 个 replay 全部基于错误前提，
attribution 与 repair 都是垃圾。

### 决策点 A：item-wrong 是前置 gate，不是后置 fallback

确定：item-wrong **不是**某个 wrong label 之后的兜底，而是独立的**前置根因层**。

理由：无论哪个 wrong label，在 item-wrong 前提下所有 replay 都错，最终都要 fallback 到 item-wrong。
若不预先判断 item 再并行判断 wrong label，cost 爆炸且逻辑倒置。

正确流程：

```
检索记忆 → [item-wrong gate] →
  wrong：先修正记忆内容，再跑 replay
  correct：直接跑 replay → attribution
```

item-wrong 的判断机制（Defender/Challenger 语言博弈 or 任务结果反推）**待后续讨论**，本节先定 MCTS。

### 决策点 B：这是单玩家 MCTS，不是双人博弈

AlphaGo 的"双方"来自围棋零和对抗。CMD 没有对手——任务固定，是在一棵推理路径树上**单方搜索**
（解谜/规划类）。强行设博弈双方会带偏设计。AlphaGo 真正可迁移的是三件事，不是"博弈"：

- **policy（走子先验）** → 在每个节点决定优先展开哪个分支的启发式
- **value（局面评估）** → G-Eval 对中间局面的廉价打分
- **search + distill** → 离线用搜索生成高质量轨迹，验证后蒸馏成"不再评分、只全量生成"的策略

第三点正是之前说的"离线五模式实验效果好就不再评分"——同一机制的两面。

### 决策点 C：label 每步可变（真 MCTS），不是 label 全程固定（穷举升级）

关键澄清解开了一个 category error：**label 不在 system prompt 里。** 上下文有两个不同的槽：


| 槽位              | 谁住在这                | 跨 hop 变不变           |
| --------------- | ------------------- | ------------------- |
| system prompt 槽 | 被测 agent 的指令 + 注入记忆 | **不变**（业界共识，已由源码确认） |
| 每跳拼接槽           | 该跳 tool/检索/记忆注入结果   | **每跳都在变**           |


label 是 **CMD debugger 的干预变量**，描述"这一跳拼进来的那块内容被反事实改成了什么"，
它操作的是**第二个槽**。所以"system prompt 一 turn 不变"与"label 每步可变"不冲突——它们在两个槽里。

确定方案 A（label 是动作，不是事后注解）：每个拼接点先选一个 label 干预，再在该干预下生成这一跳。
分支因子 = 合法 label 数。理由：动作空间是固定有限有语义的词表，跨 case 可累积访问统计 → 可蒸馏成
policy prior → 经验可复用。方案 B（自由生成后事后打 label）无共享结构，无法蒸馏，降不了本，弃用。

### 决策点 D：树的结构（每跳反事实推演）

一句话定义：**在每个拼接点上，固定前面所有跳不变，只反事实替换这一跳的 label，看终态分数怎么变；
差值就是这一跳、这个 label 的因果贡献。**

- **根节点** = (memory + system prompt + 问题, 尚未推理)
- **深度方向** = 跳序（hop index）。深度 = 拼接事件数，由任务跳数决定（2-hop 深 2），不是无限长
- **宽度方向** = 该拼接点上的合法 label
- **父子关系**：子 = 父的部分轨迹 + 再走一跳。父节点累积状态（memory + 至今推理）即子节点这一跳的
生成上下文；子继承父的一切，**包括父的 label 干预后果**（hop1 选 retrieval_gap 得"德国"，
其整个子树都活在"德国"错误世界里）
- **兄弟关系**：同一前缀、仅这一跳 label 不同，是该跳的反事实对照。两兄弟终态分数之差 =
给定相同历史、这一跳选 A vs B 的边际因果效应（反事实归因从整条轨迹缩小到单跳）
- **叶子** = 完整答案 → SubagentScorer 给 recovery gain

2-hop 例子（"埃菲尔铁塔所在国家的首都？"）：hop1 施加 retrieval_gap → "德国"；其下 hop2 即使施加
identity（完全干净）→ "柏林"，答案仍错。**全局 label 区分不了"hop2 自己坏" vs "hop2 被 hop1 连累"；
逐跳可变 + 终态回传可以——干净 hop2 在"德国"下失败、在"法国"下成功 → 根因在 hop1。**
这就是 step-level 强于全局 label 的全部意义。

### 决策点 E：只在"拼接点"分叉，纯推理跳 pass-through

多跳里大量 hop 只是把 tool 结果拼回上下文。只有**有新内容拼进来**的跳（检索/工具/记忆注入）
才值得施加 label、才分叉；中间纯推理、没拉新内容的 hop 无可干预对象，不分叉（pass-through）。
所以深度是"拼接事件数"而非"token 步数"，树进一步瘦身。
推论：**label 施加在拼接点上，拼接点类型决定哪些 label 合法**（→ 动作表，下一步）。

### 为什么必须 MCTS 而非穷举

宽 b、深 d，全展开 b^d（10 label × 4-hop ≈ 一万叶子）。正因如此不能穷举：
G-Eval（value）给中间节点廉价估计，早剪坏分支，只在有希望路径上 rollout 到叶子拿真实 recovery gain。
这正是"MCTS + G-Eval 解决 cost 爆炸"要解决的 b^d。

### 决策点 F：动作表（已收敛 2026-06-01）

**前置筛子**：generation 点（送回 API、LLM 真正推理）才分叉；tool call / 纯推理 hop /
上下文累积 = pass-through。深度单位 = generation 点数，非 tool call 数、非 hop 数。
（修正决策点 E 的"拼接事件"措辞：真正的 step 是 generation 点，一个 turn 几十次 tool call
可能只有 2-3 个 generation 点。）

**每个 generation 点的 5 个动作：**


| 动作                  | 合法性                       | step-level 语义                         |
| ------------------- | ------------------------- | ------------------------------------- |
| `retrieval_error`   | 恒合法                       | 该检索的没检索对（并入 route：跨 tier 找错，无子动作）     |
| `injection_error`   | 恒合法                       | 注入格式/顺序错 **或** 上下文管理把已注入证据挤掉（两义合一）    |
| `granularity_error` | 恒合法                       | 粒度遮蔽证据（改写项本身，靠 value 剪无效分支，非 flag 门控） |
| `graph_error`       | 门控 `is_graph_expanded`    | 图扩展引入干扰                               |
| `safety_error`      | 门控 `passed_safety_filter` | 安全层拦了证据                               |


3 恒合法 + 2 门控。`injection_error` 吸收第二义："上下文管理策略（full-retain vs keep-last）
在某 generation 点把已注入的记忆证据挤出窗口"——b 义在论文单列一节说明，代码/label 不增冗余。
门控 flag = 从 RPE 摘掉的 metadata（`is_graph_expanded` / `passed_safety_filter`），离线可靠、
线上退化为仅 base 动作（同 gold-dependent pattern，线上靠 HITL 补）。

### 决策点 #2：value 函数（已收敛 2026-06-01，嵌套式，无自由权重）

中间节点是 hop h 的半截 context，用 G-Eval 廉价估值做软剪枝。value 嵌套两分量：

```
k       = #{ atom_i : rubric_B(ctx_h, atom_i) ≥ τ }   τ≈0.5    # evidence 硬计数
ceiling = k / N                                                 # 整数位：接地天花板
V_scalar = ceiling · ( E[score_answer] / 4 )                    # 小数位：answer 在 [0,ceiling] 连续
V_vector = ( E[score_answer], [rubric_B 原始连续分 × N] )       # 存节点，喂 credit/repair
```

- **嵌套取代加权**：原 `0.7·answer + 0.3·evidence` 的手调 w 被消掉。证据计数 k/N 定区间，
answer logprob 在区间内连续输出。
- **天花板语义（区间取 [0, k/N]，非档内插值）**：证据全齐但 answer 低 → value 可落到底 →
injection/granularity 这种"证据在但用不上"的真 memory 失败不被遮蔽；reasoning 那种非 memory 错
由 back-prop 兜底（任何干预都救不回 → Δ≈0 → UCT 放弃），不靠 value。
- **G-Eval 机制**：单前向 judge-as-distribution，读分数 token top_logprobs 算 `E=Σp(k)·k`，
**不是**先离散判档再读那档 logprob。
- **两套 rubric 都"测当前前缀"，不预测未来**（未来交 back-prop）：
  - `rubric_A′`（answer 分量）：测"当前前缀已蕴含 gold answer 的程度"（非"未来会否答对"），
  新增，与 5/29 终态 `_ANSWER_RUBRIC`（问"答对没"）并存，不复用。0-4。
  - `rubric_B`（evidence 分量）：per-atom，SVO 三角色槽（时序型时间状语当宾语、关系型关系当谓词），
  沿用 5/29 收紧。0-4。
- evidence 阈值化成计数定区间，但 per-atom 原始连续分不浪费，留 V_vector 给"主凶那跳补哪条 atom"。

### 决策点 #3 / G：credit assignment + back-prop（已收敛 2026-06-01）

MCTS + UCT 保留（**未降级为线性**）；per-step replay 是节点估值/rollout 算子，不是 MCTS 替代。

```
Selection :  child* = argmax_c [ Qmax(c) + C·√( ln N(parent)/n(c) ) ]
Expansion :  摊开一个未试合法动作；新节点 Qmax 初始化 = V_scalar（#2，软剪枝在此）
Rollout   :  剩余 hop 设 identity → 完整 re-run 到终态 → 叶子真实 Δ
Back-prop :  Qmax(a) ← max( Qmax(a), Δ )  ;  n(a) += 1     # max-backup，沿路径所有祖先
```

- **叶子 Δ = 终态 AnswerVerifier(leaf_answer, gold_answer)**（5/29 终态 answer-axis，问"答对没"），
**不用** V_scalar（V_scalar 是"测当下"的导航分；叶子答案已生成，用终态 verifier）。
- **max-backup 非 mean**：归因问"这前缀下存不存在一个补全能恢复"（存在性）；CMD rollout 近确定
（固定 oracle + logprob 杀方差），max 与 mean 本就贴近，裸 max 干净，不上 SP-MCTS 方差项。
- **节点条件式（非隔离式）**：rollout 沿路径施加 hop 1..h-1 全部干预再加 hop h，后续 identity。
节点 value 依赖路径 → 树有意义、UCT 真在搜干预序列。隔离式会让树退化成线性。
- **最浅恢复深度停止律**：depth-1 单点干预 rollout 已恢复 → 该跳主凶，不再往深展开  
（同 RepairOrchestrator "first recovered or exhaust"，前移到搜索阶段）。满深度 d 仅最坏情况触达。改为credit>=0.8
- **#3 credit 从回传后 Qmax 直接读**（无额外机制）：
`credit(hop h) = Qmax(prefix<h + h:best_label) − Qmax(prefix<h + h:identity)`，
最大那跳 = 主凶，差≈0 = 连累/无辜（决策点 D 兄弟反事实对照）。
- **已知边界**：耦合失败（两 step 各自独立坏、单点 replay 都不恢复）落在 CMD single-operation
线性 Δk 边界之外（与 Shapley coalitional 分工，CONTEXT.md 边界条款已声明），设计内 out-of-scope。

### 决策点 #4：item-wrong 判断机制（已收敛 2026-06-02，reference-contrast 散度）

> **术语正名**：本节"散度"指**有向蕴含散度（directed entailment divergence）**——judge-as-distribution
> 读"m̂_i 蕴含/矛盾 m_i 的程度"score-token 分布取期望得到的有向对照标量分，**非信息论 KL 散度**。
> 不用 KL 的理由：KL 要求两个同 support 的概率分布，而 m_i/m̂_i 是文本（点），自造分布会引入自由度
> （违背 #2 消权重哲学）、或拿语言分布顶替（把措辞差异误读成事实差异）。有向性是不对称算子共有的
> （两向 judge → wrong/compression），不必借 KL 的壳；满足 d(x,x)=0、不对称、不要求三角不等式。

item 大类（5 个 OUT_OF_SCOPE_ITEM_LABELS）与 pipeline 大类治理**分开**：item gate 在 retrieval 之后、
generation 之前独立触发（与 hook 同位），item-wrong → item 治理并跳过 Tier3；item-correct → 进 Tier3。
分开的理由：并行处理会让 item-wrong 时 pipeline 永远低分陪跑 = 不必要 cost。

**统一母题：reference-contrast 散度 = item 侧 recovery gain。** CMD 全程只有一个原语——
"扰动后对最强可得参照物测有向响应"。三级参照按可得性排（gold / surrogate-of-gold / reconstruction）：
pipeline 站顶层（任务自带 gold y，recovery gain = Metric(ŷ_k,y)−Metric(ŷ,y)）；gold-dependent label
上线掉到 surrogate（仍 gold-anchored，可校准）；item 一出生就在底层（一条记忆无任务结果、源 θ 已删），
只能从重构造参照。**法则单向：有更强参照绝不下降，被逼到没有才落到散度。** 这解释了为何 item 是
d=1 无树、pipeline 是 d>1 有 MCTS——同一原语吃到参照阶梯不同级。

**散度定义（为何是散度而非误差/方差）**

- **散度是误差的唯一可算代理**。误差 = |m_i − θ|（θ = 这条记忆本应固化的真值）；无源 = θ 够不着，
误差永远算不出。LOO 从一条不含 m_i 的独立路径再估一次 θ：m̂_i = Reconstruct(storem_i}, query)，
散度 = |m_i − m̂_i|。统计内核同 bootstrap/jackknife：同一隐变量两次独立抽样之差 = 误差的估计量。
重构误差 |m̂_i − θ| 够小时，散度即 |m_i − θ| 的代理。
- **散度是有向的（不是距离）**。蕴含/矛盾不对称，方向即 typing 载体：m̂_i 更具体→wrong、m_i 更旧→stale。
- **散度而非方差**：误差分解 MSE = bias² + variance。方差/标准差只看 variance（模型对自身输出的散布），
而 item 故障几乎全是 bias——stale/wrong"自信且一致地错"时 variance≈0、bias 大，方差对其结构性失明
（同 SelfCheckGPT 盲区）。散度能碰 bias，因 m̂_i 来自结构不同的信息路径（storem_i}），两路差含
bias 信息（两路 bias 同向时失效 = 信息论地板）。另：无源场景对固定文本无法重采样，方差字面不可用；
且方差是标量，杀掉 typing。

**乙：LOO 重构对照为统一引擎**。散度算子 Contrast(m_i, m̂_i) 复用 #2 的 `_continuous_verify` logprob
引擎——读"m̂_i 蕴含/矛盾 m_i 的程度"score-token 分布取期望，作连续散度，零方差、零新增代码。
散度同时是 gate（标量过阈 τ）与 router（向量结构定 label）。**护栏**：不可 blind replace m̂_i——
库稀薄时重构是贫化版本，m̂_i 不一定优于 m_i；τ 编码"第二意见强到何种程度才有资格推翻在任者"。

**item 内部成本阶梯（参照物获取成本升序，重构是兜底非主干）**

```
① 时间戳   现成、确定、0 LLM   → 只产"旧"标记喂②，不单独判决（旧 ≠ 错 ≠ 相关 ≠ 无新版本）
② 召回集对撞  现成参照(库内兄弟)、≤C(5,2) 次对照、0 生成  → stale / conflict
③ LOO 重构   需造参照、1 次生成 + 对照  → wrong / compression（仅此两类无现成参照，必须造）
末端 HITL   ③ 散度边缘判不下来 + item_poisoned（甲，无源不可检，信息论地板）
```

理由：conflict 的参照（库内兄弟）与 stale 的参照（时间锚）本就躺在库里，不必生成；只有
wrong/compression 的"本应是什么"库里不存在，才点燃重构。重构是末位手段。

**对撞范围 = 本次检索召回集（per-task scoping）**

② 的两两对撞只在**本次检索召回回来的 similar item**（TOP_K≈5）内进行，不是全库。理由：item gate 在
本次任务 retrieval 之后触发，能影响本次答案的只有被召回进上下文的那几条；召回集天然已是"同簇"
（BM25/向量检索的相关结果），不必额外算簇，O(N²) 从全库塌成 ≤C(5,2)=10。边界：库里"存在与 m_i
矛盾但本次未召回"的潜在 conflict 抓不到——但它没进上下文，本次故障归因里不是因子；跨任务全库一致性
是另一件离线 memory hygiene 事，不在 per-task item gate 职责内（与 pipeline 只对召回上下文做反事实同构）。

**① 与"找新版本"合并进 ②**：既然对撞范围已收成召回集，"m_i 有没有更新版本"就等价于"召回集里有没有
一条同主题但更新、且与 m_i 矛盾的条目"——即 ② 的对撞 + 时间戳方向判断，不单设"找新版本"步。① 退化成
纯方向信号，stale 与 conflict 在 ② 同一次对撞里分叉：


| ② 对撞结果    | 时间戳方向（①给）   | 判定                                  |
| --------- | ----------- | ----------------------------------- |
| 有向散度大（矛盾） | 一方有可靠时序且更新  | `item_stale`（旧条被推翻）→ 可自动 update 为新条 |
| 有向散度大（矛盾） | 同期 / 无可靠时间戳 | `item_conflict`（并存矛盾，需人/规则裁）        |
| 散度小（一致）   | 任意          | 放行（含"旧但未被推翻"）                       |


"一方更新"不设硬阈：只要有可靠时序且方向一致即判 stale——stale 判决主力是"矛盾"（散度），时间戳只
定方向（谁推翻谁）不定量。stale 通道整体形态：① 触发 → ② 对撞判决 → 末端 HITL（库稀薄 + 无新版本 +
散度边缘的小撮才上人），不是"有不确定性就上人"，否则光"旧"就会淹没 HITL。

**结构定位**：item gate = d=1 无树（单点属性），在 Tier3 之前独立 gate，与 pipeline 治理分开。全程一条
`_continuous_verify`（②对照与③散度同一路径）。共病代价（stale ∧ wrong 会在 ② 先判 stale 不进 ③）是
CMD single-operation 归因的一致取舍，落在 Shapley coalitional out-of-scope 边界外，非新债；类别正交度高
反而降低共病概率。

### 决策点 #5：hook 形态重构（已收敛 2026-06-02，置信度门 + subagent loop）

旧 hook = two-stage(empty_ctx 短路 → RPE 16 因子 → top-k replay 选择)，为"10 个扁平全局 replay"设计。
step-level 后 replay 对象消失(换成 generation 点上的 MCTS 动作)，hook 需重新定义身份。

**新定位：hook = 纯置信度门，不诊断、不分类。**

```
检索召回 → hook(6因子 → evidence in recall?)
              │
              ├─ NO (evidence 缺失) → FILL 分支
              │     先送 API generate（这轮先答）
              │     → 异步触发补记忆（re-extract/ask/HITL）
              │     → 无诊断，无标签
              │
              └─ YES (evidence 存在) → FIX 分支
                    hook 轻量修正（去冲突条目/re-rank）
                    → 修完再送 API generate
                    → generate 完进 subagent loop（Tier2 item gate → Tier3 pipeline MCTS）
```

**两分支时序不同（关键）**：

- Fill：evidence 缺失是硬缺陷，pre-generate 窗口内修不了（根源在记忆库）；先 generate 让这轮有个答案，
异步补记忆下轮受益。同步 block 等补记忆只伤延迟，不改这轮输出质量。
- Fix：evidence 存在，context 内部有可修复性（去冲突/re-rank 同步可完成）；修完再 generate 这轮就受益，
不必等下轮。generate 后再进诊断 cascade 拿细粒度 label。

hook 只回答"要不要诊断"(二分类)，不回答"哪错了"。诊断(哪条 item 错、哪条 pipeline 操作错)全部下沉到
Tier2-3。这避免旧 hook 的尴尬：RPE top-k 预测的 replay 和真正 attribution 后的 label 经常不一致(hook 先猜
一遍、后面推翻)。收窄成置信度门，hook 只说"我信/我不信"，不预判"错在哪"，逻辑正交。

**empty_ctx 废弃（理解 A）**：召回为空 / 全低分 = 缺记忆，需新萃取，不在 hook 诊断职责内。hook 输入假设
"已有召回"，空/低分情况由上游(对话级 memory 增删)兜走。hook 不设"召回实质性"前置检测。

**乙冷启动因子（6 个，精简自旧 16）→ 丙数据蒸馏终态**

冷启动手工因子，目标 = 单标量置信度"这批召回能否支撑答对"：


| 因子                        | 信号                        | 来源            |
| ------------------------- | ------------------------- | ------------- |
| `retrieval_score_max`     | 召回最高分(检索器原生 score)        | 检索器           |
| `retrieval_score_entropy` | 召回 score 分布的熵(越低=越集中=越可信) | 检索器           |
| `evidence_coverage`       | 召回内容覆盖 query 关键实体的比例      | NER/关键词匹配     |
| `memory_recency_min`      | 召回中最新条目的时间戳(越新越可信)        | 记忆元数据         |
| `memory_recency_spread`   | 召回条目时间戳的跨度(跨度大=可能含 stale) | 记忆元数据         |
| `conflict_signal`         | 召回条目间是否有显式矛盾(布尔或程度)       | #4 ② 副产物，可提前算 |


6 因子全是"能不能信这批召回"相关，不涉及"哪个 replay 能修"。`conflict_signal` 复用 #4 召回集对撞——
若 ② 发现矛盾，此因子直接拉低置信度，触发进 loop。

**丙终态**：用 Tier2-3 诊断后的"确实需要诊断 vs 白跑了"作为 label，训练置信度模型，蒸馏替代手工因子。
冷启动阶段无数据，先用乙；迭代一轮后数据驱动。路径同"HITL 冷启动 → 静态表 → 数据蒸馏"。

**在线形态选择：乙(预算封顶微搜索)，甲(蒸馏策略无树)作为后续发展方向。**
在线仍跑浅树/UCT 预算封顶若干次 rollout，实时搜。理由：保留在线诊断能力，为论文提供完整 V2 subagent loop
演示；甲(离线搜索 + 在线蒸馏策略)是延迟敏感场景的降级路径，不在 V2 paper scope 内。

**与 #4 的串联**：hook 置信度 < τ → 进 subagent loop → 先跑 Tier2 item gate(成本阶梯 ①②③)→ item-correct
才进 Tier3 pipeline MCTS。item gate 与 hook 同位(retrieval 之后、generation 之前)，但 item gate 在 loop 内部、
hook 在 loop 入口，两者串联非并联。

确认：A 前置 gate · B 单玩家 MCTS(+UCT，保留未降级) · C label 每步可变·方案A · D 每跳反事实树 ·
E 仅 generation 点分叉 · F 动作表 · #2 value(嵌套式天花板) · #3/G credit(max-backup) ·
#4 item-wrong(reference-contrast 散度 + 成本阶梯) · #5 hook(置信度门 + subagent loop) ·
HITL 冷启动喂养静态 gating 表（HITL 与静态表同一张表两阶段，非二选一）

待定：

1. ✅ **动作表** —— 决策点 F
2. ✅ **value 函数** —— 决策点 #2（嵌套式 ceiling·answer，天花板语义）
3. ✅ **credit assignment** —— 决策点 #3/G（max-backup + 最浅恢复深度停止）
4. ✅ **item-wrong 判断机制** —— 决策点 #4（reference-contrast 散度 = item 侧 recovery gain；
  ①时间戳触发 → ②召回集对撞 stale/conflict → ③LOO 重构 wrong/compression → HITL 兜底）
5. ✅ **hook 形态重构** —— 决策点 #5（置信度门 + subagent loop；empty_ctx 废弃；乙 6 因子冷启动 → 丙蒸馏终态；
  在线乙微搜索、甲蒸馏策略为后续方向）

---

## 端到端流程图（2026-06-02，更新：缺就补、不缺就修）

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              QUERY + MEMORY RETRIEVAL                           │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  HOOK (Confidence Gate) — "缺就补,不缺就修"                                      │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  6 factors → scalar confidence score                                     │   │
│  │  • retrieval_score_max      • memory_recency_min                        │   │
│  │  • retrieval_score_entropy  • memory_recency_spread                     │   │
│  │  • evidence_coverage        • conflict_signal (from Tier2 ② preview)    │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                        │                                        │
│                    evidence in recall? │ (soft check via coverage/score)        │
│                         ┌──────────────┴──────────────┐                         │
│                         │ NO (missing)                │ YES (present)           │
│                         ▼                             ▼                         │
│              ┌──────────────────────┐      ┌──────────────────────┐             │
│              │  FILL BRANCH         │      │  FIX BRANCH          │             │
│              │  Re-extract / ask    │      │  Enter diagnostic    │             │
│              │  this turn           │      │  cascade             │             │
│              │  No diagnosis,       │      └──────────────────────┘             │
│              │  no label            │                 │                         │
│              └──────────────────────┘                 │                         │
│                         │                             │                         │
│                         ▼                             │                         │
│              ┌──────────────────────┐                 │                         │
│              │  → Generation        │                 │                         │
│              │  (with filled memory)│                 │                         │
│              └──────────────────────┘                 │                         │
└─────────────────────────────────────────────────────────────────────────────────┘
                                                        │
                         ┌──────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  TIER 2: Item Gate (Reference-Contrast Divergence)                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  Cost ladder (reference acquisition cost ↑):                             │   │
│  │                                                                          │   │
│  │  ① Timestamp ──► "old" flag only, no verdict                            │   │
│  │        │                                                                 │   │
│  │        ▼                                                                 │   │
│  │  ② Recall-set collision (≤C(5,2) G-Eval contrasts, 0 generation)        │   │
│  │     ┌────────────────────┬─────────────────┬──────────────────┐         │   │
│  │     │ Divergence large   │ Divergence large│ Divergence small │         │   │
│  │     │ + one newer        │ + same period   │ (consistent)     │         │   │
│  │     ├────────────────────┼─────────────────┼──────────────────┤         │   │
│  │     │ item_stale         │ item_conflict   │ PASS             │         │   │
│  │     │ (auto-update)      │ (needs arbiter) │                  │         │   │
│  │     └────────────────────┴─────────────────┴──────────────────┘         │   │
│  │        │ (if no collision verdict)                                       │   │
│  │        ▼                                                                 │   │
│  │  ③ LOO Reconstruction (1 generation + contrast)                         │   │
│  │     m̂_i = Reconstruct(store \ {m_i}, query)                             │   │
│  │     Directed entailment divergence via _continuous_verify                │   │
│  │     ┌────────────────────┬──────────────────────────┐                   │   │
│  │     │ Forward div large  │ Reverse div large        │                   │   │
│  │     ├────────────────────┼──────────────────────────┤                   │   │
│  │     │ item_wrong         │ item_compression_distort │                   │   │
│  │     └────────────────────┴──────────────────────────┘                   │   │
│  │        │ (if ③ at threshold edge)                                        │   │
│  │        ▼                                                                 │   │
│  │  Terminal HITL: edge cases + item_poisoned (info-theoretic floor)       │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                        │                                        │
│                         item verdict?  │                                        │
│                         ┌──────────────┴──────────────┐                         │
│                         │ WRONG                       │ CORRECT                 │
│                         ▼                             ▼                         │
│           ┌─────────────────────────┐      ┌─────────────────────┐              │
│           │ Item treatment          │      │ Continue to Tier 3  │              │
│           │ (repair/update/HITL)    │      └─────────────────────┘              │
│           │ Skip Tier 3             │                                           │
│           └─────────────────────────┘                                           │
└─────────────────────────────────────────────────────────────────────────────────┘
                                                        │
                                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  TIER 3: Pipeline MCTS (Step-Level Attribution)                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  Single-player MCTS + UCT over generation points                         │   │
│  │                                                                          │   │
│  │  Action table per generation point:                                      │   │
│  │  ┌──────────────────┬──────────────────┬──────────────────────────┐     │   │
│  │  │ Always legal     │ Always legal     │ Gated                    │     │   │
│  │  ├──────────────────┼──────────────────┼──────────────────────────┤     │   │
│  │  │ retrieval_error  │ injection_error  │ graph_error              │     │   │
│  │  │ granularity_error│                  │ (is_graph_expanded)      │     │   │
│  │  │                  │                  │ safety_error             │     │   │
│  │  │                  │                  │ (passed_safety_filter)   │     │   │
│  │  └──────────────────┴──────────────────┴──────────────────────────┘     │   │
│  │                                                                          │   │
│  │  Tree: depth = generation points; width = legal actions                  │   │
│  │                                                                          │   │
│  │  ┌─────────────────────────────────────────────────────────────────┐    │   │
│  │  │                          ROOT                                    │    │   │
│  │  │              (memory + system prompt + query)                    │    │   │
│  │  └─────────────────────────┬───────────────────────────────────────┘    │   │
│  │                            │                                             │   │
│  │              ┌─────────────┼─────────────┬─────────────┐                │   │
│  │              ▼             ▼             ▼             ▼                │   │
│  │         [retrieval]   [injection]  [granularity]  [identity]            │   │
│  │              │             │             │             │                │   │
│  │              ▼             ▼             ▼             ▼                │   │
│  │           Gen Pt 2     Gen Pt 2     Gen Pt 2     Gen Pt 2   ...        │   │
│  │              │             │             │             │                │   │
│  │              ▼             ▼             ▼             ▼                │   │
│  │           [LEAF]       [LEAF]       [LEAF]       [LEAF]                │   │
│  │         AnswerVerifier → Δ_leaf, back-prop max-backup                  │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  Value function (#2): V = ceiling(k/N) · E[score_answer]/4                     │
│  Credit (#3): credit(h) = Qmax(prefix + h:best) − Qmax(prefix + h:identity)    │
│  Stop rule: shallowest-recovery-depth (first Δ crosses threshold → main culprit)│
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  ATTRIBUTION OUTPUT                                                             │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  • Primary label = argmax(credit) across all hops                        │   │
│  │  • Per-hop credit scores                                                 │   │
│  │  • close_deltas (top-k labels for repair iteration)                      │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  ECS DRAFT                                                                      │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  Error: what failed                                                      │   │
│  │  Cause: which operation (from attribution)                               │   │
│  │  Solution: corrected_memory + repair_guidance                            │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  REPAIR ORCHESTRATOR                                                            │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  for label in close_deltas:                                              │   │
│  │      action = RepairExecutor(ECS, label) → RepairAction                  │   │
│  │      adapter.apply_repair(action)                                        │   │
│  │      result = PostRepairContextReplay(query, repaired_context)           │   │
│  │      if result == recovered: break                                       │   │
│  │  # "first recovered or exhaust"                                          │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  RepairAction types: append | replace | relocate | update_routing | update_tpl │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  POST-REPAIR CONTEXT REPLAY (Quality Gate)                                      │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  Re-run original query with repaired context                             │   │
│  │  AnswerVerifier → recovered | partial | failed                           │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  FAILURE MEMORY                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  Store ECS record:                                                       │   │
│  │  { wrong_memory, original_evidence, corrected_memory, repair_guidance }  │   │
│  │                                                                          │   │
│  │  Retrieval key: (query_signature, hop_index, label)                      │   │
│  │  Future similar failures → retrieve & inject corrected_memory            │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**图例说明（更新）**:

- **Hook 两分支**: Fill(缺→补,无诊断无标签) vs Fix(不缺→修,进诊断级联)
- **Tier 2-3**: Fix 分支内的两级级联;item-wrong 跳过 Tier3
- **Formation 标签**: 离线归因专属(需 gold),运行时 Fill 分支不产标签
- **MCTS 树**: 深度 = generation 点数,宽度 = 合法动作数;叶子 Δ 由 AnswerVerifier 给出,max-backup 回传
- **Repair 循环**: 按 close_deltas 顺序尝试,first recovered or exhaust
- **Failure Memory**: step-level 迁移键 (query, hop, label),供未来同类故障复用
- `core/labels.py`：`PIPELINE_LABEL_ORDER`（11，W2 后narrow到10去掉 reasoning_error）、
`REPLAY_TO_LABEL`（10 replay↔label 映射）、`OUT_OF_SCOPE_ITEM_LABELS`（含 `item_wrong` 等 5 个 item-level 标签，当前 out-of-scope）
- `replays/`：`run_v1_replay_portfolio`（当前 10 个原子 replay，待拆成 step-level）
- `repair/orchestrator.py` + `repair/actions.py`：`TargetedRepairAction` 对应"逐跳定向修复"
- `repair/failure_memory.py`：composite retrieval key，将下沉为 (key, hop, label) 的 step-level 迁移键

---

## 经验复用架构 + Skill 封装设计（2026-06-08，已收敛）

### 背景

MCTS/LOO/hook 系统本身较重（10 replay + iterative repair = O(10) LLM call per case）。用户目标：

1. **Training-free** — 不微调模型，纯 prompt/context 操作
2. **MCP/Skill online** — 封装成可在线调用的 tool
3. **Not heavy, high robustness** — 轻量、稳定
4. **Lessons learned & reused** — 诊断经验能积累，未来同类失败能复用

核心问题：**如何通过经验复用降低在线复杂度？**

### 决策点 A：经验复用的三个作用点


| 作用点                 | 机制                                                            | 效果                |
| ------------------- | ------------------------------------------------------------- | ----------------- |
| **Hook**            | 相似 signature 历史上 trigger_cmd=True 且 recovered → 提高 confidence | 减少 rpe_judge 评分开销 |
| **LOO (Item Gate)** | 历史指向某类 item 是问题源 → 优先查该类                                      | O(1) ~ O(n)，期望低   |
| **MCTS**            | 历史 (signature → label) 成功率 → 作为 UCB prior                     | 优先展开高胜率分支，减少宽度+深度 |


### 决策点 B：经验索引方式 — LLM-first

**不硬编码检索 key**，让 LLM 自己决定查什么、用什么。

流程：

```
触发 CMD skill
  → LLM 拿到 (query, retrieved_items, failure_signal)
  → LLM 自己决定：
      1. "这个看起来像之前遇到过的 X 类问题，让我查一下 failure memory"
      2. 读取相关 memory records
      3. "历史上这类问题 label 是 Y，repair 用了 Z，我先试这个路径"
  → 如果直接命中 → 跳过大部分 MCTS 展开
  → 如果没命中 → 正常展开，结果写回 memory
```

理由：LLM-first + context-first 哲学，不需要硬编码 `(key → value)` 检索逻辑。

### 决策点 C：三层存储结构

```
Layer 1: 案例索引 (FAILURE_MEMORY.md)
    ↓ 指向
Layer 2: 具体 Case (cases/*.md)
    ↓ 抽象出
Layer 3: 行为模式 (patterns/*.md)
```


| 层           | 内容                                          | 作用                             |
| ----------- | ------------------------------------------- | ------------------------------ |
| **Layer 1** | `- [case_xxx](cases/xxx.md) — 日程冲突，两事件时间重叠` | 快速 scan 找相关 case               |
| **Layer 2** | 完整的 signature / label / repair / outcome    | 单个失败的完整诊断记录，**是 ground truth** |
| **Layer 3** | 从多个相似 case 抽象的 **可复用行为规则**                  | "当 X 时，做 Y" 的 pattern          |


**关键设计：case 优先，pattern 必须与 case 一致。** 这解决了 mem0 的核心问题（只存抽象 → 抽象漂移 → 错误积累 → 无法自纠）。

### 决策点 D：自校验机制

LLM 复用时的流程：

```
1. 读 Layer 1 索引 → 找到相关 cases
2. 读 Layer 2 具体 case → 确认相关性
3. 读 Layer 3 该 case 对应的 pattern
4. 对比：pattern 和 case 是否一致？
   - 一致 → 用 pattern 的 repair guide + 这个 case 作为 ECS example
   - 不一致 → 更新/删除 pattern，用 case 本身
```

**先找 case 再看 pattern**，不是反过来。Pattern 必须能被 case 验证，否则就是错的。

### 决策点 E：在线全生命周期管理


| 操作               | 触发时机                | LLM 判断什么                        |
| ---------------- | ------------------- | ------------------------------- |
| **萃取 (Extract)** | repair recovered    | "这个 case 的诊断路径值得记住吗？"           |
| **更新 (Update)**  | 萃取时发现相似 pattern 已存在 | "新实例 vs 需要修正 pattern？"          |
| **复用 (Reuse)**   | Hook 触发后、MCTS 展开前   | "有没有相关 pattern 能指导？"            |
| **删除 (Delete)**  | 复用后 repair 失败       | "pattern 是否过时/错误？标记 deprecated" |


### 决策点 F：Case 保留策略

**全留**。理由：失败诊断本身是低频事件，不会爆炸。

### 决策点 G：Pattern 粒度

**中等**。例："同一 entity 的属性值冲突 → item_conflict → clarify"

不要太粗（"memory 有冲突 → item_conflict"，误匹配多），不要太细（"日程类 query + 时间冲突"，复用率低）。

### 决策点 H：MCTS 经验加速机制

**预算池 + prior bonus**，不硬剪枝。

```
正常 MCTS：
  每个分支展开到固定深度 d，back-prop 选最优

经验加速 MCTS：
  1. 第一层全展开（5 个 action）
  2. 经验命中的分支：prior bonus 高 → UCB 高 → 优先选
  3. 总共 N 次 rollout 预算池
  4. 每次选 UCB 最高的分支继续
  5. 经验对 → 自然收敛快，用不完预算
  6. 经验错 → bonus 被实际 value 覆盖，其他分支自动获得机会
```

理由：经验不直接剪枝，而是**影响探索顺序 + 动态调整深度预算**。避免过早确定一条分支导致失误率高。

### 决策点 I：LOO 经验加速机制

**优先序（策略 B）**：

- 经验指定先查哪类 item → 找到就停
- 经验对 → 第一个就命中，O(1)
- 经验错 → 继续查，不会比无经验更差

### 决策点 J：ECS 从"生成"变成"组装"


| 组件                               | 来源                          |
| -------------------------------- | --------------------------- |
| **E** (Error type / label)       | 诊断生成                        |
| **C** (Cause + Corrected memory) | 诊断生成                        |
| **S** (Suggested repair guide)   | **检索 pattern**，fallback 才生成 |


**repair_guide 的演化路径**：

```
首次遇到新问题 → fallback 生成 S → 成功 → 存 case
                                        ↓
积累 n 个相似 case → LLM 抽象出 pattern（含 S）
                                        ↓
后续命中 → 直接用 pattern 的 S → 成功 → 强化
                               → 失败 → 标记 review → 可能更新/删除 pattern
```

**S 只在两个地方产生**：

1. Fallback 生成（冷启动）
2. Pattern 抽象（从多个成功 case 总结）

### 决策点 K：Turn 结束时更新


| 场景                     | 更新什么                                 |
| ---------------------- | ------------------------------------ |
| **诊断成功 + 有匹配 pattern** | 更新 pattern 的 "验证状态" + 可能加新 case 作为实例 |
| **诊断成功 + 无匹配 pattern** | 创建新 case，判断是否可抽象 pattern             |
| **诊断失败 + 有匹配 pattern** | 标记 pattern 待 review                  |
| **诊断失败 + 无匹配 pattern** | 记录失败 case，分析原因                       |


### 决策点 L：Skill 定位 — 方法论提供者

**Skill 不持有状态**，只教 Agent 怎么做。

- Skill 提供：hook / diagnose / repair 的方法 + 经验格式规范 + 三层结构模板
- Agent 持有：自己的 `FAILURE_MEMORY/`（和 `MEMORY.md` 同级）

每个 Agent 有自己的诊断经验积累，而不是一个中心化的 failure-memory store。

### 决策点 M：Skill 接口


| 接口                                 | 输入                              | 输出                                   |
| ---------------------------------- | ------------------------------- | ------------------------------------ |
| `hook(query, retrieved_items)`     | query + 召回内容                    | trigger_cmd: bool, stage, confidence |
| `diagnose(context, memory_path?)`  | 上下文 + Agent 的 failure-memory 路径 | label, ECS draft (E+C), 建议记录         |
| `repair(diagnosis)`                | 诊断结果                            | repair action + guidance             |
| `format_case(diagnosis, outcome)`  | 诊断 + 最终结果                       | markdown case 文本                     |
| `format_pattern(cases)`            | 多个相似 cases                      | markdown pattern 文本                  |
| `validate_pattern(pattern, cases)` | pattern + 对应 cases              | valid: bool, 不一致点, 建议修改              |


### 决策点 N：存储层级

和 Claude Code 的 memory 机制一致，**用户决定**：

```
~/.claude/FAILURE_MEMORY/              # 全局级（跨项目）
~/.claude/projects/{project}/FAILURE_MEMORY/  # 项目级
```

Skill 不管用户选哪个，只提供方法。

### Markdown 格式模板

**Layer 1: 案例索引** (`FAILURE_MEMORY.md`)

```markdown
# Failure Memory

## Cases
- [case_001](cases/001_schedule_conflict.md) — 日程 query，两事件时间重叠，item_conflict
- [case_002](cases/002_stale_address.md) — 地址 query，旧地址未删除，item_stale

## Patterns
- [pattern_temporal_conflict](patterns/temporal_conflict.md) — 同 entity 时间属性冲突
- [pattern_stale_overwrite](patterns/stale_overwrite.md) — 新值写入但旧值未清除
```

**Layer 2: 具体 Case** (`cases/001_schedule_conflict.md`)

```markdown
# Case 001: Schedule Conflict

**Date**: 2024-12-15
**Query**: "我明天下午有什么安排？"
**Signature**: 日程类 query + memory 有两个时间重叠的事件

## Retrieved Items
- item_1: "明天 14:00 开会"
- item_2: "明天 14:30 看牙医"

## Diagnosis
- **Label**: item_conflict
- **Problem Item**: 两个都是真实的，但冲突
- **Root Cause**: 用户写入时没检查冲突

## Repair
- **Action**: clarify_with_user
- **Guidance**: 列出冲突事件，问用户优先级

## Outcome
- **Assessment**: recovered
- **Pattern**: [[pattern_temporal_conflict]]
```

**Layer 3: 抽象 Pattern** (`patterns/temporal_conflict.md`)

```markdown
# Pattern: Temporal Conflict

**触发条件**: 
- Query 涉及时间安排
- Memory 中 2+ items 对同一时间段有不同事件

**诊断**: item_conflict

**Repair Guide**:
1. 不要随意选一个
2. 列出冲突项，让 agent 向用户 clarify
3. 如果有时间戳，可提示 "最近添加的是 X"

**来源 Cases**: [[case_001]], [[case_017]], [[case_042]]

**验证状态**: ✓ valid (last checked: 2024-12-20)
```

### 完整流程（更新）

```
1. Hook 判断触发
2. 诊断 → 得到 E + C
3. 查经验：
   - 读 Layer 1 索引 → 找相关 case
   - 读 Layer 2 case → 确认相关
   - 读 Layer 3 pattern → 验证与 case 一致？
     - 一致 → 拿 pattern 的 S
     - 不一致 → 更新/删除 pattern，fallback 生成 S
4. 组装 ECS → repair → post-repair
5. Turn 结束更新：
   - 成功 + 有 pattern → 更新验证状态 / 加 case 实例
   - 成功 + 无 pattern → 创建 case，判断是否可抽象 pattern
   - 失败 + 有 pattern → 标记待 review
   - 失败 + 无 pattern → 记录失败 case
```

### 待后续讨论

- [ ] Layer 2/3 的详细内容字段（需结合实际场景细化）
- [ ] 写一个 context 教 LLM 怎么构建三层结构

---

