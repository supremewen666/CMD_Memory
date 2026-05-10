# Direction 01 完整研究计划：Counterfactual Memory Debugger

方法暂名：**CMD: Counterfactual Memory Debugger for LLM Agent Memory**

## 1. 研究问题

LLM Agent 越来越依赖长期记忆，但当一个 memory-augmented agent 在多跳推理或长上下文任务中出现幻觉、遗漏、冲突或误用记忆时，现有系统通常只能告诉我们“最终答案错了”，很少能解释**是哪条记忆、哪次检索或哪段推理导致了偏差**。

一个失败样例可能来自：

- 该写入的事实没有进入记忆；
- 记忆被压缩时丢掉了关键实体、时间或关系；
- 使用了错误的记忆粒度，比如 session summary 太粗；
- 查询被路由到了错误的 store 或预算 tier；
- 图记忆扩展到了错误实体或干扰节点；
- 正确记忆存在但没有被检索到；
- safety filter 错误过滤了有用证据，或放入了不安全记忆；
- 正确证据已经取出，但最终推理失败。

核心问题：

> 能否通过 counterfactual replay，把 agent-memory failure 归因到具体记忆操作，而不是只看最终答案对错？

进一步的问题是：

> 能否把“错误—原因—修正方法”沉淀成可复用的 Failure Memory，让系统在后续相似任务中检索并注入修复策略，从而降低由记忆污染、记忆冲突、错误图关联和检索偏差引发的幻觉？

## 2. 背景

近期 agent memory 研究开始从“单一向量库检索”转向模块化记忆系统：

- **MemSkill** (arXiv:2602.02474) 把 memory extraction / consolidation / pruning 视为可学习、可演化的技能，并从 hard cases 中更新 skill bank。
- **Omni-SimpleMem** (arXiv:2604.01007) 用 failure-driven autoresearch 搜索更好的记忆系统设计，说明失败样例可以驱动系统改进。
- **AgeMem** (arXiv:2601.01885) 把 summarize、retrieve、add、update、delete 等记忆操作暴露为 agent policy 的工具动作。
- **SimpleMem** (arXiv:2601.02553) 说明 memory unit 构造、语义压缩和 intent-aware retrieval 会显著影响长期记忆效率。
- **BudgetMem** (arXiv:2602.06025) 说明 query-aware routing 和预算选择本身就是 memory decision。
- **RepoAudit** (arXiv:2501.18160) 在代码审计 agent 中使用 validator 降低幻觉结论，提供了“重放/验证中间事实”的工程启发。

这些工作都在改进 memory system 的局部模块，但缺少一个通用诊断层来回答：

> 这个失败到底是 write failure、retrieval failure，还是 reasoning failure？

因此，memory 方法之间的比较仍然偏黑盒。我们知道某个方法最终分数更高，却不知道它解决了哪类 memory failure，也不知道下一个改进应该优先动哪个模块。

## 3. 灵感来源

CMD 的灵感来自三条线索：

1. **MemSkill 的 hard-case evolution**：hard cases 很有价值，但如果不知道 hard 在哪里，skill evolution 仍然容易盲目。
2. **Omni-SimpleMem 的 failure-driven search**：失败驱动的系统搜索有效，但失败类型应进一步细化到 memory operation 层。
3. **RepoAudit 的 validator/replay 思路**：复杂 agent workflow 可以通过验证中间事实和替代路径来减少错误结论。

CMD 把这三点合成一个更聚焦的问题：对每个失败样例，系统性重放一组 counterfactual memory pipelines，观察哪一种干预最能恢复答案，从而推断主要失败原因。

本轮 metabolism 后，CMD 进一步升级为一个闭环机制：它不仅诊断失败，还生成 **Error-Cause-Solution (ECS)** 记录，并把 ECS 写入 Failure Memory。后续相似任务不再检索完整失败轨迹，而只检索 `corrected_memory + repair_guidance`。

## 4. 核心假设

记忆调试可以被建模为一个 counterfactual attribution 问题。

给定查询 \(q\)、历史 \(H\)、记忆状态 \(M\)、agent 输出 \(\hat{y}\)、标准答案 \(y\)，构造一组 counterfactual interventions：

\[
CF=\{cf_{write}, cf_{compress}, cf_{granularity}, cf_{route}, cf_{retrieve}, cf_{graph}, cf_{safety}, cf_{reason}\}
\]

每个 counterfactual 生成一个新输出：

\[
\hat{y}_k = Agent(q, Intervention_k(M,H))
\]

恢复增益定义为：

\[
\Delta_k = Metric(\hat{y}_k,y)-Metric(\hat{y},y)
\]

主要失败原因：

\[
c^*=\arg\max_k \Delta_k
\]

如果多个 \(\Delta_k\) 接近，则输出 top-2 或 multi-label attribution。

## 5. 错误类型定义

CMD 采用两层错误分类。

### 5.1 Memory Item 是否坏了

这一层判断“这条记忆内容本身是否有问题”。即使后续检索和推理流程正确，只要这条记忆本身坏了，注入上下文后也会制造偏差。

| Label | 含义 | 示例 |
|-------|------|------|
| `item_wrong` | 记忆内容本身错误 | 用户实际住上海，记忆写成北京 |
| `item_stale` | 记忆已经过期 | 用户旧偏好仍被当作当前偏好 |
| `item_conflict` | 与其他记忆冲突 | 两条记忆给出不同生日/项目状态 |
| `item_poisoned` | 记忆被污染或注入攻击影响 | 外部网页指令被写入长期记忆 |
| `item_compression_distorted` | 压缩导致语义失真 | summary 改变因果关系或省略限定条件 |

### 5.2 Memory Pipeline 是否失败

这一层判断“记忆本身可能是对的，但系统在写入、检索、路由、注入或推理使用时失败了”。

| Label | 含义 | 示例 |
|-------|------|------|
| `write_error` | 关键信息没有写入记忆 | 用户偏好没有被保存 |
| `compression_error` | 写入了但压缩丢失关键细节 | summary 丢掉日期或人物 |
| `premature_extraction_error` | 原始事件里有证据，但抽取后不可恢复 | ingestion 阶段把未来问题需要的限定条件丢掉 |
| `granularity_error` | 使用了错误粒度 | session summary 太粗，raw span 太碎 |
| `route_error` | 路由到了错误 store/tier | 需要 episodic trace 却查 semantic store |
| `retrieval_error` | 正确记忆存在但未被召回 | embedding 近邻检索 miss |
| `injection_error` | 正确记忆被错误注入上下文 | evidence 顺序、格式或解释提示导致误用 |
| `graph_error` | 图扩展引入干扰关联 | 错误实体被联想进来 |
| `safety_error` | safety gate 误杀或漏放 | 有用证据被过滤，或 poisoned memory 被放入 |
| `reasoning_error` | 证据正确但最终推理失败 | 多跳组合、时间推理失败 |

## 6. 方法设计

### 6.1 基础流程

先运行一个普通 memory-augmented agent：

1. 从历史中构造 memory units；
2. 压缩或汇总记忆；
3. 按 query 路由到某个 memory store / retrieval method；
4. 检索证据；
5. 生成答案；
6. 计算 answer score 和 evidence score。

### 6.2 动态错误记忆检测与修正闭环

每次任务运行时加入轻量监控与失败诊断：

```text
每次任务
  ↓
轻量 Memory Monitor
  ↓
是否发现异常？
  ├── 否：正常执行
  └── 是：启动 Failure Diagnoser
          ↓
     读取错误 memory、原始证据、执行 trace
          ↓
     生成 Error-Cause-Solution
          ↓
     当前修复上下文中注入：
     wrong_memory + cause + corrected_memory + repair_action
          ↓
     修正 User Memory
          ↓
     将 Error-Cause-Solution 写入 Failure Memory
          ↓
     后续相似任务只检索：
     corrected_memory + 简短 repair guidance
```

Memory Monitor 是轻量触发器，不在每一步都运行昂贵 replay。它只检测异常信号，例如答案与检索证据冲突、同一实体存在冲突记忆、检索证据置信度低、图扩展引入高相似但低一致性节点、或最终答案无法由 evidence 支撑。

### 6.3 Counterfactual Replays

对失败样例运行以下重放：

| Replay | 干预方式 | 诊断目标 |
|--------|----------|----------|
| Oracle Write | 直接把 gold evidence 注入 memory | `write_error` |
| Oracle Compression | 用保留 gold evidence 的 memory 替代压缩记忆 | `compression_error` |
| Oracle Granularity | 在 raw/event/session/persona/procedure/graph 粒度中取最佳 | `granularity_error` |
| Oracle Route | 穷举 store/tier，选择最优 route | `route_error` |
| Oracle Retrieval | 直接提供 memory 中的 gold evidence | `retrieval_error` |
| Verbatim Event Oracle | 从原始事件/会话 trace 恢复证据 | 写入、压缩或过早抽取导致的不可恢复信息损失 |
| Injection-Oracle | 以规范 evidence block 注入正确记忆 | `injection_error` |
| Graph-Off / Graph-Only | 比较关闭图扩展和仅图扩展 | `graph_error` |
| Safety-Off / Safety-Oracle | 比较关闭 safety gate 和 oracle safety decision | `safety_error` |
| Evidence-Given Reasoning | 直接给 gold evidence，让模型回答 | `reasoning_error` |

### 6.4 Attribution Rule

第一版 CMD 使用规则归因：

\[
label = \arg\max_k \Delta_k
\]

第二版可以训练轻量分类器来 amortize replay cost：

\[
x=[\Delta_{write},\Delta_{compress},\Delta_{granularity},\Delta_{route},\Delta_{retrieve},\Delta_{raw\_event},\Delta_{inject},\Delta_{graph},\Delta_{safety},cost,evidence\_recall]
\]

\[
p(c|x)=softmax(Wx+b)
\]

规则版负责解释性，学习版负责降低诊断成本。

### 6.5 Error-Cause-Solution Memory

CMD 的输出不是普通日志，而是可复用经验记忆：

```json
{
  "error_type": "item_conflict | retrieval_error | reasoning_error",
  "wrong_memory": "...",
  "original_evidence": "...",
  "cause": "...",
  "corrected_memory": "...",
  "repair_action": "delete | update | reroute | reformat_injection | add_disambiguation",
  "repair_guidance": "...",
  "trigger_signature": {
    "task_type": "multi-hop QA",
    "entities": ["..."],
    "memory_store": "episodic | semantic | graph"
  }
}
```

后续相似任务检索 Failure Memory 时，只注入简短的 `corrected_memory + repair_guidance`，避免把完整错误轨迹再次污染上下文。

### 6.5.1 Post-Repair Context Replay

ECS 生成后必须立即做一次修复后上下文复测，而不是直接跳到未来任务。

```text
AttributionAssigned
  -> ECSDrafted
  -> RepairedContextBuilt
  -> PostRepairRetested
  -> RepairValidated / RepairFailed
  -> FutureCaseGuided
```

复测方式：

1. 根据 attribution label 生成 context patch；
2. 拼接 `corrected_memory + repair_guidance + repaired_evidence_block`；
3. 禁止直接注入 gold answer；
4. 对同一个原始 query 重新运行 agent；
5. 记录 answer score、evidence score、repair_success、regression risk；
6. 与 generic hard-case update 对比。

不同错误类型对应不同修复上下文：

| Label | 修复上下文 |
|-------|------------|
| `write_error` | 加入缺失 corrected memory |
| `compression_error` | 替换压缩失真的 memory |
| `premature_extraction_error` | 从 raw event 恢复最小证据 |
| `retrieval_error` | 将 corrected memory 放入 retrieved evidence set |
| `injection_error` | 修正 evidence block 格式、顺序和边界 |
| `reasoning_error` | 保持证据不变，加入简短 reasoning repair guidance |

这个步骤是 V0 必选 gate：CMD 不能只证明“归因对”，还要证明“修复后原失败样例能恢复”。

### 6.6 审计模块还是 Skill？

建议主方法做成 **审计模块**，再提供 **skill 接口**。

原因是 CMD 的核心贡献是“干预式归因”：它要重放、替换、关闭或增强某个 memory operation，然后看答案是否恢复。这更像一个外部 auditor/debugger，而不是 agent 内部的一条普通 skill。

| 形态 | 作用 | 优点 | 风险 |
|------|------|------|------|
| 审计模块 | 外部诊断 memory failure | 能做 counterfactual replay、benchmark、跨系统比较、生成 hard-case labels | 成本更高 |
| Skill | agent 内部可调用的修复行为 | 易部署，可把 ECS 变成 repair guidance，适合 MemSkill-style evolution | 容易被理解成 prompt/procedure，弱化方法新颖性 |

因此论文中应把 CMD 定义为 **Counterfactual Memory Auditing Module**：

```text
Memory Monitor
Trace Collector
Counterfactual Replay Engine
Attribution Layer
ECS Generator
```

然后把 skill 作为部署适配层：

```text
CMD-Skill Adapter
  -> 决定何时调用审计模块
  -> 将 ECS 转成 repair guidance
  -> 注入 corrected_memory + repair_guidance
  -> 将 hard-case labels 反馈给 skill evolution
```

这样既保留研究上的可验证性，又能连接到 MemSkill-style 的工程价值。

### 6.7 V0 Open Decisions Resolution

第一版 probe 不覆盖全部 pipeline label，而是故意从小标签集合开始：

```text
V0 labels:
write_error
compression_error
premature_extraction_error
retrieval_error
injection_error
reasoning_error
```

暂缓到 V1/V2 的标签：

```text
granularity_error
route_error
graph_error
safety_error
```

原因：V0 的目标是证明 counterfactual attribution 是否成立，而不是一次性覆盖完整 taxonomy。标签过多会让 50-100 个 probe 样例变稀，难以判断 CMD 是否真的优于 heuristic / subagent judge。

`premature_extraction_error` 应提升为一级 pipeline label。它不是普通 compression error，也不是 retrieval error：原始事件中存在证据，但 ingestion/extraction 后系统已经无法恢复。这个标签由 Verbatim Event Oracle 诊断。

Subagent Judge 在 V0 中承担两个角色：

1. baseline：用同一条 trace 生成自由归因解释；
2. cheap monitor：以高召回方式判断是否值得启动昂贵 replay。

它不应直接写最终 attribution label 或 ECS，除非读取了 CMD replay deltas。

第一版实现目标是 **standalone research harness**，而不是先插入现有 memory agent。V0 harness 需要保证 perturbation label、replay delta、baseline 对照和 attribution metric 可复现。代码结构可以预留 adapter interface，但实际集成放到 V1。

## 7. 数据计划

### 7.1 主数据

- **LoCoMo** (arXiv:2402.17753)：长期对话、persona/event/temporal memory。
- **LongMemEval**：适合测试长上下文记忆、压缩与检索失败。
- **HotpotQA-memory variant**：适合区分 retrieval failure 和 reasoning failure。

### 7.2 合成扰动数据

从有 gold evidence 的样例构造受控失败：

1. 删除应写入的 gold evidence；
2. 压缩时删除关键实体/时间/关系；
3. 把正确证据放在错误粒度；
4. 强制路由到错误 store；
5. 添加相似实体 distractor；
6. 添加错误图边；
7. 让 safety gate 误过滤；
8. 给正确证据但弱化 reasoning prompt。
9. 注入过期、冲突、污染或压缩失真的 memory item；
10. 使用错误 evidence injection 格式让模型误读正确记忆。
11. 写入或抽取阶段过早抽象，导致未来查询所需原始证据不可恢复。

这样可以得到已知标签，用于评估 CMD 的 attribution accuracy。

### 7.3 样例格式

```json
{
  "id": "locomo_case_001",
  "query": "...",
  "history": ["..."],
  "memory_units": ["..."],
  "gold_answer": "...",
  "gold_evidence": ["..."],
  "perturbation_type": "compression_error",
  "base_output": "...",
  "counterfactual_scores": {},
  "failure_label": "compression_error",
  "error_cause_solution": {
    "wrong_memory": "...",
    "cause": "...",
    "corrected_memory": "...",
    "repair_action": "...",
    "repair_guidance": "..."
  }
}
```

## 8. 实验设计

### 实验 1：CMD 能否恢复注入的失败标签？

目标：验证 CMD 在合成扰动上的归因能力。

Baselines：

- random label；
- evidence recall heuristic；
- LLM-as-judge failure explanation；
- subagent judge over execution trace；
- single oracle retrieval comparison。

指标：

- failure attribution accuracy；
- macro F1；
- top-2 attribution accuracy；
- actionability rating；
- 按建议修复后的 fix-success rate；
- prompt/order 扰动下的稳定性；
- cost per diagnosis。

### 实验 1b：为什么 subagent judge 不等价于 CMD？

subagent judge 应作为强 baseline，而不是主方法。原因是它只能读取最终答案、检索结果和执行 trace，并给出后验解释；它没有真正执行 oracle write、oracle compression、route oracle、Verbatim Event Oracle、Injection-Oracle 或 evidence-given reasoning，因此无法回答“如果换掉某个 memory operation，错误是否恢复”。

CMD 的研究对象不是“让另一个 agent 判断谁错了”，而是构造一套可复现的 counterfactual interventions，让失败归因从自然语言猜测变成可测量的恢复增益：

\[
\Delta_k = Metric(\hat{y}_k,y)-Metric(\hat{y},y)
\]

因此 subagent judge 可以做三件事：

1. 作为 baseline，检验 CMD 是否真的优于自由解释；
2. 作为人类可读解释器，读取 CMD 的 replay deltas 后生成说明；
3. 作为轻量 monitor 的一部分，触发是否需要昂贵 replay。

但它不应替代 CMD 主框架，因为它缺少干预证据、稳定标签和可直接绑定 repair action 的操作级归因。

### 实验 2：CMD 标签是否能指导 memory system 改进？

流程：

1. 运行 baseline memory system；
2. 用 CMD 标注失败；
3. 按标签进行 targeted fix：
   - `write_error`: 改 write/admission；
   - `compression_error`: 改 memory unit schema；
   - `route_error`: 训练 route/store selector；
   - `retrieval_error`: 调 retriever；
   - `reasoning_error`: 只改 answer synthesis；
4. 与不区分错误类型的 hard-case update 对比。

指标：

- answer F1 / accuracy；
- evidence recall；
- token cost；
- fixed failures 数量；
- label-specific improvement。

### 实验 2a：修复后上下文重拼是否让原失败任务恢复？

流程：

1. 对失败样例生成 attribution 与 ECS；
2. 构造 repaired context；
3. 禁止注入 gold answer；
4. 用同一原始 query 重新运行；
5. 对比原 baseline、generic hard-case update、CMD repaired context。

指标：

- post-repair answer score；
- post-repair evidence score；
- repair_success rate；
- regression risk；
- token cost。

### 实验 2b：Failure Memory 是否降低后续相似任务幻觉？

流程：

1. 第一轮任务中诱发 memory item 错误或 pipeline failure；
2. CMD 生成 ECS，并写入 Failure Memory；
3. 后续相似任务只检索 `corrected_memory + repair_guidance`；
4. 对比无 Failure Memory、检索完整失败轨迹、检索 ECS guidance 三种设置。

指标：

- hallucination rate；
- conflict recurrence rate；
- memory pollution recurrence；
- answer F1 / evidence recall；
- additional token cost。

### 实验 3：CMD 是否跨 memory 方法泛化？

测试对象：

- fixed summary memory；
- SimpleMem-style compressed memory；
- graph memory；
- retrieve-all vector memory；
- budget/store routing memory。

指标：

- attribution accuracy per method；
- 不同方法的 failure distribution；
- failure profile 是否解释方法间性能差异。

### 实验 4：人工可解释性检查

抽样 50 个失败样例，让人工判断 CMD 归因是否合理、是否可行动。

指标：

- human agreement；
- actionability rating；
- disagreement categories。

## 9. 预期贡献

1. **问题定义**：把 agent memory failure diagnosis 定义为 counterfactual attribution 问题。
2. **失败 taxonomy**：提出 memory item 是否坏了 与 memory pipeline 是否失败 的两层分类，并细分 write/compression/premature extraction/granularity/route/retrieval/injection/graph/safety/reasoning。
3. **方法**：提出 CMD，用 counterfactual replay 诊断 memory failure，并生成 Error-Cause-Solution。
4. **Benchmark**：构建可控扰动数据，用于评估 memory failure attribution。
5. **分析价值**：解释不同 memory 方法到底失败在哪里。
6. **工程价值**：为 MemSkill-style skill evolution、store routing、memory unit redesign 提供 hard-case labels。
7. **经验记忆价值**：将错误、原因、修正方法沉淀为 Failure Memory，在后续相似任务中注入修复策略。
8. **部署形态**：提出 audit-module-first、skill-adapter-second 的实现路径，使 CMD 既能作为独立审计器评估不同 memory 系统，也能作为 agent skill 注入修复策略。

## 10. 为什么这是新方向

大多数 agent memory 论文问的是：

> 如何构建更强的记忆系统？

CMD 问的是：

> 当记忆系统失败时，我们如何知道该改哪个记忆操作？

进一步地，它还问：

> 当一次记忆错误被修复后，系统能否把这次修复沉淀成可复用经验，避免后续相似任务再次犯同类错误？

这不是又一个 memory architecture，而是一个诊断与修复经验沉淀工具。它可以服务于后续多个方向：Memory Firewall、Granularity Switching、Store Routing、Procedural Memory 都能用 CMD 产生的 failure labels 和 ECS memories 来定位改进点。

## 11. 风险与缓解

| 风险 | 缓解 |
|------|------|
| counterfactual replay 成本高 | 先做小规模 probe，再训练 learned CMD amortize 成本 |
| 失败原因多因素耦合 | 输出 top-2 / multi-label attribution |
| gold evidence 不总是可得 | 先用合成扰动和 evidence-annotated 数据 |
| LLM judge 噪声 | 优先用 exact/F1/evidence recall，开放回答再用 judge |
| 合成扰动太简单 | synthetic validation 后加入自然失败样例 |
| Failure Memory 反过来污染上下文 | 后续只检索 corrected_memory + repair_guidance，不注入完整错误轨迹 |
| 变成工程项目而非研究 | 第一篇聚焦 formulation、taxonomy、diagnostic value |

## 12. 实施路线

### Week 1：构造 probe 数据

- 定义 example schema；
- 从 LoCoMo / HotpotQA-memory 构造 50-100 个扰动样例；
- 实现 fixed-summary 和 vector-memory baseline。

### Week 2：实现 counterfactual replay engine

- 实现 oracle write、oracle retrieval、no compression、high-budget route、verbatim raw-event replay、evidence-given reasoning；
- 计算 \(\Delta_k\)；
- 输出第一版 attribution table。

### Week 3：评估归因能力

- 对比 random、heuristic、LLM explanation baselines；
- 计算 macro F1 / top-2 accuracy；
- 人工检查失败样例。

### Week 4：验证下游价值

- 用 CMD labels 做 targeted fixes；
- 与 undifferentiated hard-case update 对比；
- 决定是否扩展到 LongMemEval / MemoryCD。

### Week 5：加入 Failure Memory 闭环

- 定义 ECS schema；
- 实现 Failure Memory 写入与相似任务检索；
- 验证 ECS guidance 是否降低相似任务中的 hallucination / conflict recurrence。

## 13. 成功标准

进入完整实现/论文阶段的条件：

- attribution macro F1 明显超过 heuristic 和 LLM-explanation baselines；
- top-2 attribution accuracy 足够支持人工调试；
- CMD-guided fixes 优于不区分错误类型的 hard-case update；
- Failure Memory 中的 ECS guidance 能降低后续相似任务的同类错误复发；
- failure profile 至少揭示一个非显然洞察，例如“很多看似 retrieval failure 的样例其实是 compression failure”。

## 14. 摘要草稿

长期记忆可以增强 LLM Agent，但当 memory-augmented agent 在多跳推理或长上下文任务中出现幻觉、遗漏、冲突或误用记忆时，我们通常无法判断错误来自记忆内容本身，还是写入、压缩、路由、检索、图关联、安全过滤、注入格式或最终推理。本文提出 **Counterfactual Memory Debugger (CMD)**，一种通过 counterfactual replay 诊断 agent memory failure 并沉淀修复经验的框架。对于每个失败查询，CMD 运行 oracle write、oracle compression、oracle route、oracle retrieval、verbatim raw-event replay、graph intervention、safety intervention、injection intervention 和 evidence-given reasoning 等干预，并根据答案恢复增益进行失败归因。随后，CMD 生成 Error-Cause-Solution 记录，修正 User Memory，并将可复用的 `corrected_memory + repair_guidance` 写入 Failure Memory，以在后续相似任务中降低由记忆污染、冲突、错误图关联和检索偏差引发的幻觉。
