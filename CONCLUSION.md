# Agent 记忆系统综述：存储、注入与纠错机制

**覆盖范围**：学术前沿（2024–2026）+ 工业系统（Claude Code、Codex CLI、ChatGPT、OpenClaw/Hermes、MemGPT/Letta）
**研究方法**：5 角度并行搜索 → 23 篇来源 → 104 条声明 → 3-vote 对抗验证

---

## 一、为什么记忆是 Agent 的核心组件

**已确认（3-0 票）**：记忆是区分 LLM-based agent 与无状态基础模型的关键结构组件，是支撑 agent-environment 长期交互的必要条件。

> "The key component to support agent-environment interactions is the memory of the agents."
> — arxiv 2404.13501（专项记忆综述，2024 年 4 月）

无记忆的 LLM 每次 turn 都从零开始，无法跨 session 积累知识、无法在多步任务中保持状态。这一点在 2404.13501、2508.19828、2602.08563、2603.07670 等多篇独立论文中均有明确表述。

---

## 二、记忆存储机制

### 2.1 存储形态分类

当前主流存储形态可分为三类：

| 形态 | 代表系统 | 特点 |
|------|----------|------|
| 平坦键值 / 文本列表（memoize 缓存） | Claude Code 记忆系统 | agent 主动写入，per-turn 从缓存注入，compaction 时重新读盘 |
| SQLite + Markdown（git-backed） | Codex CLI 记忆系统 | 两阶段 LLM 提炼，per-turn 注入，有 staleness 检测 |
| 向量数据库 + RAG | ChatGPT Memory、mem0、Letta | 语义检索，自动写入，支持大规模 |
| 结构化属性对 | MemInsight（arxiv 2503.21760） | `{m_i: ⟨a_1,v_1⟩,...,⟨a_n,v_n⟩}`，可过滤 |

### 2.2 工业系统实现

**Claude Code**：`CLAUDE.md`（全局、用户级、项目级）是**项目指令文件**，不是记忆系统。Claude Code 的实际记忆系统以 Markdown 文件形式存储在 `~/.claude/projects/.../memory/` 下，由 agent 在对话中主动写入，通过 `MEMORY.md` 索引，跨 session 持久化。源码（`src/utils/claudemd.ts`、`src/utils/api.ts`、`src/context.ts`）揭示的实际注入机制如下：

- **存储**：Markdown 文件（`MEMORY.md` 索引 + 按主题拆分的 topic 文件），无数据库，无向量检索
- **写入时机**：agent 在 session 内主动决策触发（调用 Write 工具），不是 per-turn 自动写入；写入后立即落盘，但 memoize 缓存不会自动失效，需显式调用 `resetGetMemoryFilesCache()` 才会在下一 turn 生效
- **注入时机**：**per-turn，但从内存缓存读取，不是每次重新读盘**。`getUserContext()` 每 turn 调用，通过 `prependUserContext()` 将记忆包裹在 `<system-reminder>` 用户消息中前置到每次 API 请求。首次调用时从磁盘读取并 memoize，后续 turn 命中缓存（无磁盘 I/O）
- **缓存失效时机**：compaction（`/compact`）、worktree 切换、settings 同步时调用 `resetGetMemoryFilesCache()`，下一 turn 重新读盘。这意味着 **session 内 agent 新写入的记忆文件，在下一次 compaction 或显式重置前不会反映到注入内容中**
- **纠错/staleness**：无自动机制；`MEMORY.md` 超过 200 行时静默截断（数据丢失无警告）；用户通过 `/memory` 命令手动审计

**Codex CLI**：`AGENTS.md` 是项目指令文件，不是记忆系统。Codex 的实际记忆系统是一套两阶段异步流水线（源码：`codex_memories_write` / `codex_memories_read` crate）：

- **存储**：SQLite（`~/.codex/memories_1.sqlite`，表 `stage1_outputs`）+ Markdown 文件（`~/.codex/memories/raw_memories.md`），Phase 2 使用 git-backed workspace 管理合并
- **写入时机**：session 启动时异步触发（`start_memories_startup_task()`），不是 turn 结束触发。Phase 1 用 GPT-5.4-mini（低推理）从历史 rollout 提炼原始记忆并写入 SQLite；Phase 2 用 GPT-5.4（中推理）做全局合并，更新 `raw_memories.md`。两阶段均有 job lease 防并发、token quota 限流、失败重试
- **注入时机**：**per-turn**，每次 turn 前通过 `ContextManager`（`codex_core/src/context_manager/history.rs`）将 `~/.codex/memories/*.md` 预置到 context，并追踪 citation
- **纠错/staleness**：对比 `source_updated_at` vs 线程 `updated_at_ms` 检测过期记忆，自动 pruning（可配置 `max_unused_days`），Phase 2 用 git diff 检测实际变化，无变化则跳过模型调用

**ChatGPT Memory**：OpenAI 于 2024 年推出的持久记忆功能。采用向量存储 + 自动提取机制，在对话中自动识别值得记住的信息并写入记忆库，下次 session 时检索注入。用户可查看和删除记忆条目。无公开论文，机制来自官方博客和逆向工程分析。

**OpenClaw / Hermes**：mem0.ai 的对比分析显示，两者均基于向量数据库实现跨 session 记忆，支持自动提取和语义检索。Hermes（NousResearch）的记忆模块文档显示其支持结构化记忆存储和检索，但无独立学术论文。

**MemGPT / Letta**：MemGPT（arxiv 2310.08560）是最早系统性解决 LLM 上下文窗口限制的工作之一，其继任者 Letta 已开源。**注意**：工作流验证发现，广泛引用的"三层记忆架构（main context / searchable message DB / vector document store）"描述在验证中被推翻（0-3 票），说明二手来源对 MemGPT 架构存在误描述，应直接阅读原始论文和 Letta 源码。

### 2.3 存储粒度：turn-level vs. session-level

**已确认（3-0 票）**：MemInsight（arxiv 2503.21760，EMNLP 2025）明确定义了两种存储粒度：

- **Turn-level**：每个对话轮次的细粒度上下文属性，保留细节
- **Session-level**：整个对话的高抽象摘要，减少噪声

两种粒度并存，检索时根据查询类型选择合适粒度。这是目前学术界对注入粒度最清晰的形式化描述。

---

## 三、记忆注入机制：如何将记忆放入当前 session/turn

### 3.1 注入时机的三种模式

验证结果揭示了一个重要发现：**注入时机没有统一标准**，不同系统差异显著。

**Session 启动时整体注入**（无典型代表——此模式已被更精确描述取代）：
早期文档描述的"session 启动一次性注入"是不准确的简化。Claude Code 实际是 per-turn 注入（从缓存），Codex 也是 per-turn 注入（从缓存/DB）。真正"session 启动一次性注入且 session 内不更新"的系统在主流实现中并不存在。

**Per-turn 注入（缓存驱动）**（Claude Code，源码 `src/utils/api.ts`）：
每 turn 调用 `prependUserContext()`，将记忆包裹为 `<system-reminder>` 用户消息前置到 API 请求。内容来自 `getMemoryFiles()` 的 memoize 缓存——session 内磁盘文件变化不会自动反映，只有 compaction 或显式 `resetGetMemoryFilesCache()` 才触发重新读盘。

**Per-turn 注入（DB 驱动）**（Codex CLI，源码 `codex_core/src/context_manager/history.rs`）：
每 turn 前通过 `ContextManager` 将 `~/.codex/memories/*.md` 预置到 context。记忆内容由 session 启动时的异步流水线（Phase 1/2）更新写入磁盘，注入每 turn 发生，因此流水线完成后后续 turn 即可看到新记忆。

**Session 边界摘要注入**（HAT，arxiv 2406.06124，2-1 票确认）：
在每个 session 结束时，将当前 session 历史 `H_s^e` 与上一个 session 的记忆 `M_{s-1}^e` 合并，构建新的跨 session 记忆 `M_s^e`。下一个 session 开始时注入这个合并记忆，而非原始历史。这避免了将完整历史传递给下一个 session，实现了有损但高效的跨 session 延续。

**Per-turn 检索注入**（RAG 类系统）：
每个 turn 根据当前输入 `o_t` 从记忆库检索相关记忆 `m_t = Retrieve(M, o_t)`，然后将 `m_t` 注入当前 turn 的 prompt。**注意**：工作流对"per-timestep 注入是标准模式"这一声明的验证结果为 1-2（被推翻），说明这种模式并非普遍标准，各系统实现差异较大。

### 3.2 注入内容的形式

- **原始文本**：直接将记忆条目拼接到 prompt（最简单，最常见）
- **结构化属性对**：MemInsight 将记忆表示为 `{m_i: ⟨a_1,v_1⟩,...,⟨a_n,v_n⟩}`，注入时可按属性过滤
- **运行时对象**（CaveAgent，arxiv 2601.01569，3-0 票确认）：通过持久化 IPython kernel，将 DataFrame、规划树等 Python 对象直接保留在 kernel 命名空间中，跨 turn 无需序列化即可访问。这是一种非传统的"记忆注入"——对象本身就在运行时，不需要注入到 prompt。

### 3.3 Generative Agents 的评分注入

广泛引用的 Generative Agents（Park et al., 2023）中"按 recency + relevance + importance 加权评分后选择性注入"的描述，在本次验证中得到 1-2 票（被推翻）。这意味着该机制的具体实现细节在二手来源中存在误传，应直接阅读原始论文确认。

---

## 四、记忆纠错机制

这是当前研究最薄弱、也最关键的环节。

### 4.1 核心诊断：无限制记忆增长有害

**已确认（2-1 票，两篇独立论文佐证）**：

> "The unrestricted expansion of memory is detrimental to the performance of LLM agents."
> "Errors propagate within the system for memory and contaminate the efficacy of learning."
> — arxiv 2605.06716

arxiv 2505.16067 独立验证：在 add-all 策略下，记忆条目超过 2400 条后准确率显著下降。arxiv 2509.26354 进一步显示，自我进化 agent 的安全对齐随记忆积累而退化。三篇独立来源收敛于同一诊断：**纠错/剪枝是架构必需，不是可选优化**。

### 4.2 Reflection 作为语义过滤器

**已确认（3-0 票）**：

Reflection 机制将原始轨迹中的有价值逻辑提取出来，过滤掉轨迹特定的噪声，写回记忆库。这是目前最主流的"软纠错"方式。

但同一论文（arxiv 2605.06716）也指出了 reflection 的根本局限：

> "Reflected memories are frequently fragmented and exhibit a high degree of dependence on context."
> "Corrected trajectories devoid of abstraction may still induce errors resulting from minor shifts in context."

即：**纠错而不抽象，仍然会在轻微上下文偏移时引发错误**。Reflection 解决了"错误传播"问题，但没有解决"上下文脆弱性"问题。

### 4.3 当前系统普遍缺乏显式纠错层

**已确认（3-0 票）**：

MemInsight（EMNLP 2025）在 Limitations 中明确承认：系统依赖 LLM 生成的标注质量，对幻觉没有防护。实验中 Llama v3 生成的属性对包含明显幻觉（论文图 9、10 中红色标注）。arxiv 2511.03506（HaluMem）独立证实：缺乏纠错层的记忆系统会积累并传播幻觉。

这意味着：**几乎所有当前主流记忆系统（包括工业产品）都没有部署可靠的纠错层**。

### 4.4 BeliefMem：概率记忆纠错的尝试

arxiv 2605.05583 提出 BeliefMem，用概率分布而非确定性结论存储记忆，理论上可以在新证据到来时更新信念而非覆盖。但本次验证对其定量声明（纠错率是确定性基线的两倍、平均 4.75 步收敛等）全部推翻（0-3 票），说明这些数字不可靠，该方向的实际效果尚未得到独立验证。

### 4.5 Staleness 检测：未解决的开放问题

arxiv 2605.06527（STALE benchmark）声称当前最好的 LLM agent 在检测过期记忆上只能达到 55.2% 准确率，但该声明在验证中得到 1-2 票（被推翻）。这不意味着 staleness 检测已经解决——而是说明该 benchmark 本身的可靠性存疑，**staleness 检测目前没有可信的基准**。

---

## 五、安全威胁：记忆投毒

**已确认（2-1 票）**：

AgentPoison（arxiv 2407.12784，NeurIPS 2024）证明：污染 RAG 知识库中不到 0.1% 的条目，即可在多种真实 agent 类型上实现超过 80% 的攻击成功率，且对正常性能影响不足 1%。arxiv 2507.06850 对 18 个 LLM 的测试中，83.3% 对 RAG 后门攻击存在漏洞。

这意味着：**任何使用向量数据库作为记忆后端的系统，在没有写入验证机制的情况下，都面临严重的投毒风险**。

---

## 六、各系统横向对比

| 系统 | 存储形态 | 注入时机 | 纠错机制 | 论文/来源 |
|------|----------|----------|----------|-----------|
| Claude Code | Markdown 文件（agent 主动写入） | Per-turn 注入（memoize 缓存，compaction 时重新读盘） | 无自动纠错；200 行静默截断 | github.com/sanbuphy/claude-code-source-code |
| Codex CLI | SQLite + Markdown（git-backed） | Session 启动异步提炼；per-turn 注入 | Staleness 检测 + 自动 pruning | github.com/openai/codex |
| ChatGPT Memory | 向量存储 | Session 启动检索注入 | 用户手动删除 | OpenAI 博客 |
| MemGPT/Letta | 分层存储（细节待核实） | 动态分页 | 无显式纠错 | arxiv 2310.08560 |
| MemInsight | 结构化属性对（双粒度） | Per-turn 属性过滤注入 | 无显式纠错 | arxiv 2503.21760 |
| HAT | 树形历史 | Session 边界摘要注入 | 无显式纠错 | arxiv 2406.06124 |
| CaveAgent | IPython kernel 对象 | 运行时持久化（无注入） | 无显式纠错 | arxiv 2601.01569 |
| Generative Agents | 向量存储 | 评分选择性注入 | Reflection（软纠错） | Park et al. 2023 |
| BeliefMem | 概率分布 | 信念更新 | 概率纠错（效果未验证） | arxiv 2605.05583 |

---

## 七、开放问题与研究空白

验证过程中，以下问题被明确标记为**尚无可靠答案**：

1. **检索 vs. 写入策略的相对贡献**：有声明称检索策略的影响（14-23 分）远大于写入策略（3-8 分），但被 0-3 推翻。这个对比实验在已确认文献中不存在。

2. **商业系统的内部机制**：Claude Code、ChatGPT、Codex 的 session/turn 级注入机制对外不透明，所有已确认发现均来自学术论文和开源系统。

3. **BeliefMem 类概率纠错的实际效果**：理论上有吸引力，但定量结果全部被推翻，需要独立复现。

4. **Staleness 检测的最小可行机制**：没有可信基准，没有已部署的生产方案。

5. **注入时机的最优粒度**：per-turn、session-boundary、session-start 三种模式各有权衡，没有受控对比实验。

---

## 八、对 CMD 项目的启示

结合本项目（Counterfactual Memory Debugger）的定位：

- **记忆纠错是架构必需**这一点已被多篇独立论文确认，为 CMD 的核心假设提供了直接支撑。
- **Reflection 的上下文脆弱性**（arxiv 2605.06716）与 CMD 的 counterfactual replay 设计高度相关：replay 本质上是在测试记忆在轻微上下文偏移下的稳定性。
- **Staleness 检测无可信基准**意味着 CMD 的 STALE-类评估如果能提供可靠基准，本身就是一个贡献点。
- **投毒漏洞**（AgentPoison）提示 CMD 的 failure memory 写入路径需要考虑写入验证，否则 failure memory 本身可能成为攻击面。

---

## 参考文献（已验证来源）

- [arxiv 2605.06716](https://arxiv.org/abs/2605.06716) — 记忆增长有害性 + Reflection 语义过滤
- [arxiv 2404.13501](https://arxiv.org/abs/2404.13501) — LLM Agent 记忆综述
- [arxiv 2503.21760](https://arxiv.org/html/2503.21760v1) — MemInsight，双粒度结构化记忆（EMNLP 2025）
- [arxiv 2406.06124](https://arxiv.org/html/2406.06124v1) — HAT，Session 边界记忆构建
- [arxiv 2601.01569](https://arxiv.org/html/2601.01569v1) — CaveAgent，IPython kernel 持久化
- [arxiv 2407.12784](https://arxiv.org/abs/2407.12784) — AgentPoison，记忆投毒攻击（NeurIPS 2024）
- [arxiv 2511.03506](https://arxiv.org/abs/2511.03506) — HaluMem，记忆幻觉积累
- [arxiv 2605.05583](https://arxiv.org/html/2605.05583v1) — BeliefMem，概率记忆纠错（定量结果未验证）
- [arxiv 2605.06527](https://arxiv.org/abs/2605.06527) — STALE benchmark（可靠性存疑）
- [arxiv 2310.08560](https://arxiv.org/abs/2310.08560) — MemGPT 原始论文
- [Letta GitHub](https://github.com/letta-ai/letta) — MemGPT 继任者开源实现
