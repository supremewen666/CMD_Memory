# Current Compressed Memory

更新日期: 2026-05-10

## 当前状态

当前已从 agent memory 方向的多篇论文与 GitHub 检索中，完成方向筛选。现阶段不再推进大一统 memory architecture，而是选用 **D1 Counterfactual Memory Debugger** 作为主研究方向。

完整计划文件:

- `plans/direction_01_research_plan.md`

## 核心问题

LLM Agent 使用长期记忆后，失败时通常只能看到最终答案错了，但不知道失败来自哪个 memory operation。

CMD 要回答：

> 当 memory-augmented agent 失败时，能否通过 counterfactual replay 判断失败来自写入、压缩、粒度、路由、检索、图关联、安全过滤，还是最终推理？

本轮 metabolism 后，CMD 进一步扩展为“诊断 + 修正 + 经验记忆沉淀”的闭环：

> 当 Agent 出现幻觉、遗漏、冲突或误用记忆时，识别是哪条记忆、哪次检索或哪段推理造成偏差，并将“错误—原因—修正方法”沉淀为可复用 Failure Memory。

## 方法名

**CMD: Counterfactual Memory Debugger for LLM Agent Memory**

## 核心假设

对失败样例运行一组 counterfactual interventions，并用恢复增益做归因：

\[
\Delta_k = Metric(\hat{y}_k,y)-Metric(\hat{y},y)
\]

\[
c^*=\arg\max_k \Delta_k
\]

如果多个干预恢复增益接近，则输出 top-2 或 multi-label attribution。

## 失败类型

两层错误定义：

1. **Memory item 是否坏了**：记忆内容本身是否错误、过期、冲突、污染、压缩失真。
2. **Memory pipeline 是否失败**：记忆本身是对的，但写入、检索、路由、注入、推理使用环节出了问题。

具体标签：

- `item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`
- `write_error`, `compression_error`, `premature_extraction_error`, `granularity_error`, `route_error`, `retrieval_error`, `injection_error`, `graph_error`, `safety_error`, `reasoning_error`

## Counterfactual Replays

- Oracle Write: 注入 gold evidence，诊断 `write_error`。
- Oracle Compression: 替换为保留 gold evidence 的记忆，诊断 `compression_error`。
- Oracle Granularity: 穷举 raw/event/session/persona/procedure/graph 粒度，诊断 `granularity_error`。
- Oracle Route: 穷举 store/tier，诊断 `route_error`。
- Oracle Retrieval: 直接提供 memory 中 gold evidence，诊断 `retrieval_error`。
- Verbatim Event Oracle: 直接从原始事件/会话 trace 中恢复证据，诊断写入或抽取阶段的不可恢复信息损失。
- Injection-Oracle: 以规范 evidence block 注入正确记忆，诊断 `injection_error`。
- Graph-Off / Graph-Only: 比较图扩展影响，诊断 `graph_error`。
- Safety-Off / Safety-Oracle: 比较 safety gate 影响，诊断 `safety_error`。
- Evidence-Given Reasoning: 直接给 gold evidence，诊断 `reasoning_error`。

## 关键论文来源

- MemSkill, arXiv:2602.02474: hard cases 可用于 memory skill evolution，但需要知道 hard 在哪里。
- Omni-SimpleMem, arXiv:2604.01007: failure-driven search 可改进 memory systems。
- RepoAudit, arXiv:2501.18160: validator/replay 可减少复杂 agent workflow 的错误结论。
- AgeMem, arXiv:2601.01885: memory operations 可作为 agent policy actions。
- SimpleMem, arXiv:2601.02553: memory unit 构造与压缩会显著影响长期记忆。
- BudgetMem, arXiv:2602.06025: query-aware routing / budget 是一等 memory decision。
- Storage Is Not Memory, arXiv:2605.04897: retrieval-centered recall 提醒 CMD 区分 retrieval failure 与 ingestion-time abstraction loss。
- What Happens Inside Agent Memory, arXiv:2605.03354: internal circuit signals 暗示 operation-level diagnosis 可能存在模型内部证据。
- MEMTIER, arXiv:2605.03675: tiered memory 暴露 route/retrieval/consolidation 的长程瓶颈。
- Governed Collaborative Memory, arXiv:2605.04264: correction pathway、provenance、version lineage 应进入 Failure Memory 设计。
- Agent Memory Survey, arXiv:2603.07670: write-manage-read loop 形式化，确认 causally grounded retrieval 是开放挑战，无现有反事实归因框架。
- D-MEM, arXiv:2603.14597: RPE-gated Fast/Slow 路由，80% token 节省；Critic Router 可作 CMD 廉价预过滤器。
- A-MemGuard, arXiv:2510.02373: dual-memory + lesson store，生产验证 Failure Memory 模式。
- Trajectory-Informed Memory, arXiv:2603.10600: Decision Attribution Analyzer，最接近 CMD 但属观测性归因。
- RSCB-MC, arXiv:2604.27283: 风险敏感检索，abstention 作为一等安全动作，false-positive penalty。
- SQLFixAgent, arXiv:2406.13408: failure memory reflection 在 SQL 修复领域的实例验证。
- Reflection-Driven Control, arXiv:2512.21354: 演化反思记忆，修复示例检索。
- MedEinst, arXiv:2601.06636: 三级因果推理 (association/intervention/counterfactual) 验证 CMD 方法。
- Thought-Retriever, arXiv:2604.12231: 中间推理作为自演化长期记忆。
- MemGPT, arXiv:2310.08560: 基础性虚拟上下文管理，OS 风格 memory tiering。
- MemArchitect, arXiv:2603.18330: 策略驱动记忆治理，CMD ECS 修复指导的生产目标。
- Library Theorem, arXiv:2603.21272: O(log N) 索引记忆的形式化检索边界。
- ByteRover, arXiv:2604.01599: agent-native 记忆，同一 LLM 管理写入和检索——CMD 要解耦的失败模式。
- MemMachine, arXiv:2604.04853: 真值保留三-tier 记忆，93% LongMemEval，验证 compression_error 标签。
- GRAVITY, arXiv:2605.01688: 结构化锚定对话记忆。
- ADAM, arXiv:2604.09747: agent memory 隐私攻击面，验证 Subagent Judge Monitor 防泄漏边界。
- GitHub: agent_debugger (Peaky Peek) — 最接近 CMD 的开源工具（交互式 checkpoint replay），但非自动化反事实归因。
- GitHub: agentlens — memory attribution 工具，验证市场需求。

## 数据与实验

主数据:

- LoCoMo: 长期对话、persona/event/temporal memory。
- LongMemEval: 长上下文记忆评估。
- HotpotQA-memory variant: 区分 retrieval failure 和 reasoning failure。

合成扰动:

- 删除 gold evidence。
- 压缩丢关键实体/时间/关系。
- 错误粒度存储。
- 错误 store/tier 路由。
- 添加 distractor 或错误图边。
- safety gate 误过滤。
- 给正确证据但弱化 reasoning prompt。
- 写入或抽取阶段过早抽象，导致未来查询所需原始证据不可恢复。

核心实验:

1. CMD 是否能恢复注入的失败标签。
2. CMD labels 是否能指导 targeted memory fixes。
3. Post-Repair Context Replay 是否能让原失败 query 在修复后上下文中恢复。
4. CMD 是否跨 fixed summary / compressed memory / graph memory / retrieve-all / routed memory 泛化。
5. CMD 归因是否具有人类可解释性和可行动性。
6. Failure Memory 中的 Error-Cause-Solution 是否能降低后续相似任务中的幻觉、冲突复发和记忆污染复发。

## 成功标准

- Attribution macro F1 明显超过 heuristic 和 LLM-explanation baselines。
- Top-2 attribution accuracy 能支持人工调试。
- CMD-guided fixes 优于 undifferentiated hard-case update。
- Failure profile 揭示非显然洞察，例如许多看似 retrieval failure 的样例其实是 compression failure。

## 下一步

1. Probe suite scaling: 从 6 个 smoke case 扩展到 50-100 个带标签 case（PRD 目标，V0→V1 gate 前提）。
2. HITL gate review: probe suite 规模化后执行人工 V0→V1 gate sign-off。
3. V1 准备：语义证据召回（替换 phrase matching）、RPE 预过滤器、Failure Memory 语义检索。

## 关键设计压缩

- `Storage Is Not Memory` 与 MEMAUDIT 强化了 `premature_extraction_error` 和 write-time audit：CMD 不能只在 retrieval 后归因。
- Subagent Judge 是 baseline + 高召回 monitor，不是最终归因源；monitor 必须 leak-safe。
- 方法形态：`CMD-Audit` 是研究对象，`CMD-Skill Adapter` 是部署接口。
- V0 label set 只含 pipeline labels：`write_error`, `compression_error`, `premature_extraction_error`, `retrieval_error`, `injection_error`, `reasoning_error`。
- V0 明确排除 bad memory item labels：`item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`。
- V0 必选 gate：Post-Repair Context Replay，即 ECS 后重拼上下文、重跑原失败 query，不注入 gold answer，并记录 `repair_success`。
- Retrieval baseline strengthening 是 V0.5 issue，不阻塞最小 V0；但后续必须加入 BM25/vector/hybrid/rerank、Recall@k/MRR/nDCG/Precision@k/context noise ratio，以及 hard negatives。

## 2026-05-09 增量结论

新增论文信号：

- STALE: stale / implicit conflict 是 V1 bad-memory-item 方向，说明 V0 排除 item labels 需要写硬。
- MEMAUDIT: package-oracle 支持 CMD-Audit 作为写入/压缩质量的审计 harness。
- BeliefMem: uncertain memory 和 candidate beliefs 支持 V1 state adjudication。
- TreeMem: tree credit assignment 可作为未来 learned CMD / operation credit 的依据。
- MemReranker: 强化 retrieval baseline，要求 CMD 区分 reranking failure 和 extraction/injection/reasoning failure。
- SafeHarbor 与 Agent Worms: 强化 Subagent Judge Monitor 防泄漏、typed promotion、no full failed trace re-entry。
- Cross-Component Interference: 支持 V0 小标签、standalone harness，避免过早集成太多组件。

MVP 新增不变量：

1. `CMD-Audit` / `CMD-Skill Adapter` 命名进入领域语言；
2. Subagent Judge Monitor 只能输出触发信号和简短原因，不能输出最终 label、ECS、gold answer、完整 trace 或写 memory；
3. V0 attribution 明确不评估 bad memory item labels；
4. Post-Repair Context Replay 仍是 V0 repair-validity gate。
5. raw-event oracle 能恢复但 extracted-memory oracle 不能恢复时，仍优先标 `premature_extraction_error`，不要被更强 retriever 误归成 `retrieval_error`。
6. 当 Verbatim Event Oracle 和 Oracle Retrieval 都能恢复证据时，gain 排序自然选出更高者，不需要特殊 tie-breaker——这不是 extraction 丢失，是 raw event 恰好部分覆盖了 retrieval 缺陷。
7. 当 Verbatim Event Oracle 和 Oracle Retrieval 都失败但 Oracle Compression 成功时，这是合法 `compression_error`——两次非恢复 replay 的诊断成本是设计预期的，不是 bug。额外 replay 成本是 taxonomy boundary case 的设计内开销，在 smoke suite 内 bounded 即可。
8. Post-Repair Context Replay 继续归 CMD-Audit。Audit 的写入权限限于 replay-local sandbox（可修改 in-memory probe state、构造 repaired context），不可写入真实 agent 的 persistent memory。CMD-Skill Adapter 负责将 validated repair 应用到真实 agent / skill / memory pipeline。
9. Subagent Judge Monitor 的 `anomaly_reason` 强制锁定为预定义枚举（`answer_vs_evidence_mismatch`、`retrieved_context_incomplete`、`evidence_recall_low`、`confidence_anomaly`），禁止自由文本。Evidence pointer 仅允许 opaque ID，禁止内容文本。
10. ECS `cause` 可以描述 item 状态（如 "stored preference was outdated relative to ground truth"），但不能使用 V0 禁用的 item label 名称，也不能用自然语言变相声明 item label（如 "the memory item is stale"）。
11. Post-Repair Context Replay 输出分层分数（`post_repair_answer_score`、`post_repair_evidence_score`）和三值 `repair_assessment`（`recovered` / `partial` / `failed`），不做 binary gate。`partial`（evidence 恢复但 answer 仍错）暴露被 pipeline error 遮蔽的耦合 failure，是诊断深度的正面信号。
12. V0.5 stronger retriever 可以改变 `retrieval_error` 归因，但不能改变 `premature_extraction_error`。硬分界线是 `evidence_recall_from_text(gold_evidence, memory_item.text)`：Memory Item 文本包含证据但弱 retriever 没召到 → 可翻为 `retrieval_error`；Memory Item 文本不包含证据（extraction 已丢失）→ 永不可翻为 `retrieval_error`。
13. 版本升级由可信度证据驱动，不是功能堆叠驱动。V0→V1 要求四项 V0 evidence artifact 通过 paper claim 阈值；V1→V2 要求至少两个不同 memory agent 通过 Adapter Interface 集成且 macro F1 不退化。证据和功能都满足后才锁定为最终版 module/skill。
14. Issue 0005 (Post-Repair Context Replay) 实现完成：完整 pipeline 从 `run_case_full` 入口（attribution → draft_ecs → build_repaired_context → run_post_repair_context_replay + run_hard_case_update_baseline），三值 `repair_assessment`（recovered/partial/failed），ECS cause 验证禁止 item label 名称，sandbox write boundary 限制 Audit 写入路径。26 个新 behavior-level 测试全部通过。Post-repair replay 不注入 gold answer：证据评分来自 evidence_recall_from_text，答案评分来自 gold_answer 在 repaired context 中的出现情况，模拟 agent 能从 corrected context 中提取答案但不保证推理正确的场景。

## 2026-05-10 Metabolism Day 0 增量

Broad survey across arxiv + openalex + GitHub (27 papers, 10 repos). Key conclusions:

1. **CMD 的独特定位已确认**：无现有论文或开源项目做自动化 counterfactual memory replay for operation-level attribution。最接近的 Trajectory-Informed Memory（观测性决策归因）和 Peaky Peek（交互式 checkpoint replay）分别缺反事实证据和自动化。

2. **Failure Memory 模式被独立验证**：A-MemGuard (dual-memory + lesson store)、SQLFixAgent (failure memory reflection)、Reflection-Driven Control (evolving reflective memory) 三个独立工作在不同领域收敛于同一模式。

3. **竞争定位表已建立**（见 `topic-cmd-memory-failure.md` 和 PRD）：CMD 是唯一同时满足 counterfactual evidence + operation-level granularity + automated 的方案。

4. **RPE 预过滤值得后续探索**：D-MEM 的 Critic Router 用 Surprise/Utility 评分门控昂贵操作（80% token 节省），可为 CMD Subagent Judge Monitor 提供廉价预过滤器。已记录为 hyp-011 和 open_decisions Decision 11。

5. **RSCB-MC 的 abstention action** 提示 `retrieval_error` 归因应区分 "retrieved wrong memory" 和 "retrieved right memory but injected unsafely"。

6. **工程生态系统信号**：LangSmith、OpenTelemetry、Sentry 覆盖通用可观测性，但都不做 memory replay。Peaky Peek 和 AgentLens 是最近的开源记忆调试工具，验证了需求但方法不同。

## 2026-05-10 Issue 0008 设计决策

Issue 0008 (V0.5 retrieval baseline strengthening) 激活前做出三项设计决策：

### 决策 A：只保留两个 retriever（BM25 + HybridRerank）

四个 retriever 过度工程化。核心论点是"弱 retriever 遗漏的证据，强 retriever 能找回"。两个足够：

- **BM25**（弱 baseline）：纯关键词匹配，最可解释。弱点在 paraphrase、同义替换、实体混淆。
- **HybridRerank**（强 baseline）：BM25 + TF-IDF cosine 混合检索，再对 top-k 候选做 evidence-phrase-match 重排序。每一步的改进原因都透明。

去掉 vector-alone（中间档位，分析增量有限）和 hybrid-without-rerank（过渡态，无独立主张）。

### 决策 B：`evidence_recall_from_text` 的已知局限（Plan A）

`evidence_recall_from_text` 使用短语匹配作为证据评分——这是必要条件而非充分条件。一个包含 "Messi" 的记忆条可以匹配 `required_phrases: ["Messi"]`，但语义可能是 "Messi is GOAT"（正确）或 "Messi is a father"（无关）。这是已知 V0 局限：

1. **通过 phrase 粒度缓解**：`required_phrases` 被设计为包含区分性词汇（如 `["Messi", "GOAT"]`），假阳性匹配需要 probe 设计者刻意弱化短语。
2. **语义评估归属 V1**：V1 集成真实 LLM agent 后，answer scoring 将被替换为 LLM-judge 评估，evidence scoring 可升级为 entailment-based 检查。Issue 0008 的 hard negatives 刻意暴露这个边界，本身就是有价值的实验证据。

### 决策 C：Agentic search deferred to V1

Agentic search（query rewriting、iterative refinement、tool-use retrieval）明确排除在 V0.5 之外：
- V0 是 deterministic standalone harness；agentic search 需要 LLM 调用，引入非确定性。
- Agentic search 的失败模式（query 改写错误、tool 选择错误）需要独立的 taxonomy review。
- 与 `ingestion_error` 和真实 memory-agent 集成一起列为 V1 候选。

以上三项决策分别记录在 issue 0008、`plans/cmd_open_decisions.md`（Decision 12）、CLAUDE.md 和 CONTEXT.md 的相关条目中。

## 2026-05-11 Metabolism Day 1 增量

Incremental 调研（滑动窗口 2026-05-06 ~ 2026-05-11），9 篇新论文 + 1 个 GitHub repo，6 篇直接 CMD 相关。

### 关键发现

1. **Failure Attribution 正在成为独立研究方向**：三篇独立工作（2604.25161 capability-level attribution for VLN, 2604.17658 step-level error diagnosis for MAS, MemEvoBench mis-evolution detection）从不同粒度解决 agent failure diagnosis，但都使用观测性证据而非反事实干预。CMD 的 operation-level counterfactual attribution 仍是唯一方案。

2. **"Verified Episodic Memory" 模式跨领域收敛**：ErrorProbe (2604.17658) 的 verified episodic memory（仅存储被 executable evidence 确认的 error pattern）+ CMD 的 Post-Repair Context Replay（仅存储被 replay 验证的 ECS）+ A-MemGuard 的 consensus-based lesson store（仅存储达成共识的修复）→ 三个独立工作在不同领域收敛于同一模式：memory repair store 应只持久化被验证的修正。已记录为 hyp-012。

3. **Memory Security Survey 强化 Monitor 边界**：2604.16548 的 mnemonic sovereignty 框架（6-phase lifecycle × 4 security objectives）将 Write/Retrieve/Execute/Share/Forget 阶段的完整性攻击作为核心威胁模型，直接验证 Subagent Judge Monitor 的 leak-safety 约束和 opaque evidence pointer 设计。

4. **`premature_extraction_error` 获得独立经验证据**：2604.27045 的 dual-stream reconciliation 在健康教练 agent 中测量到 13.6% error cascade 来自 "clinical details lost during memory extraction from unstructured conversation"，为 extraction-stage loss 提供了领域外经验验证。

5. **`route_error` V1 标签被 MemFlow 验证**：2605.03312 的 MemFlow 通过 Router 显式分类 query intent 并分发到不同 memory tier（Profile Lookup / Targeted Retrieval / Deep Reasoning），验证了 "mismatched memory operations" 是真实 failure mode。Router 是 Oracle Route replay 的自然靶点。

6. **MemoScope 验证工具需求但确认 CMD 的独特定位**：eth-jashan/MemoScope 是专用 memory debugger（capture → visualize → diff），但纯观测性，无 counterfactual replay、无 automated attribution。CMD-Audit 可作为 MemScope 的 attribution plugin 集成——这是 V1 Adapter Interface 的潜在实现路径。

7. **SCG-MEM 暴露新 failure mode**：2604.20117 的 "structural hallucination"（模型生成不存在的 memory key）是 CMD V0 label set 未覆盖的失败模式，可作为 V2 的 `reasoning_error` 子类型或独立 label。

### 竞争定位更新

CMD 的独特定位在 Day 1 增量调研后进一步加强：

| 新增方法 | Evidence Type | Attribution Granularity | Automated |
|----------|--------------|------------------------|-----------|
| Capability-Attribution (2604.25161) | observational (capability oracle) | capability-level | yes |
| ErrorProbe (2604.17658) | observational (backward trace + tool execution) | step-level | yes |
| MemoScope (GitHub) | observational (event capture + visualization) | retrieval-level (score/rank) | yes (no attribution) |
| MemEvoBench (2604.15774) | benchmark detection | item-level (contamination) | yes (no attribution) |
| **CMD** | **counterfactual (replay intervention)** | **operation-level (6 labels)** | **yes** |

新增工作进一步填充了 granurality 谱系（item → retrieval → step → capability → operation），CMD 的 operation-level granularity 依然是最细粒度的自动化方案。
