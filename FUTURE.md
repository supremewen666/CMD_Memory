# FUTURE.md — CMD V2-Readiness Refactor Plan

**生成日期**: 2026-05-25 (首版) / 2026-05-25 修订 (V2 完整论文澄清 + repair_guidance 开放设计)
**会话**: improve-codebase-v1
**适用**: V1.0 arxiv 06-10 ship 之后到 V2 主 paper 实施期间的代码演进路线
**前置阅读**: TASK.md / CLAUDE.md / CONTEXT.md / knowledge/current-memory.md / EXPERIMENT.md

---

## 0. 文档定位

本文档是 V1.0 ship 之后的代码架构演进决策记录，与 TASK.md 的 V1.0/V1.1 paper milestone 平行存在。V1.0 paper critical path（→ 06-10）期间**禁止执行本文档任何动作**。

## 1. V2 Paper 形状（澄清后，2026-05-25 会话）

| 维度 | 初版误判 | 澄清后正确判断 |
|---|---|---|
| V2 论文数量 | 主 paper + 补充 paper | **V2 是完整独立论文**。V1.0/V1.1 是独立论文；V2 是后续独立论文 |
| V2 主 paper 主题 | 端到端 runtime repair loop | **V2 全身 = subagent loop 实现**（图 ①→⑥ 所有 LLM 决策点为显式 subagent 协作架构）|
| V1 与 V2 的关系 | V1 作为 baseline 被 V2 引用 | **V1 和 V2 是两篇独立论文**。V2 引用 V1 作为 prior work / baseline |
| Live mem0/Letta | 不发论文，工程设施 | 不变 |
| V1 rule-based 代码地位 | V1 paper 化石，不进 V2 runtime | 不变 |
| V1 LLM 使用形态 | — | **V1 已经全部 LLM 评分**（`LLMClient.generate()` 单 prompt 调用），**不是**"先 phrase-match 后加 LLM"。非显式 subagent 框架 |
| V2 LLM 使用形态 | — | **显式 Claude Code 式 subagent 协作架构**（多 role、coordinator、跨 subagent trace 共享）|
| repair_guidance 形态 | — | **开放设计点**，是 LLM 推理 vs skill-式 workflow 的对比实验维度（见 EXPERIMENT.md D1）|

**核心演变**：V2 主 paper 的 novelty 是**把 V1 的单 prompt LLM 调用升级为显式 subagent 协作架构**，同时把 V1 中仍然 rule-based 的 ③ attribution 和 ④ ECS 草拟两步升级为 LLM 推理。不是"从 0 到 LLM"，是"从单 prompt 到 subagent 协作 + closure of 2 remaining rule-based gaps"。

## 2. V1 LLM 使用形态（非显式 subagent）— 精确现状

V1 代码已存在的 LLM 调用点（`LLMClient.generate()` 单 prompt）：

| 节点 | V1 实现 | LLM 调用方式 |
|---|---|---|
| ② Hook RPE Judge label | `SubagentScorer` (offline calibration) | `LLMClient.generate()` binary verify |
| ③ 每个 replay 的 answer | `agent_generate(query, evidence_block)` callable | `LLMClient.generate()` 单调用 |
| ③ 每个 replay 的 evidence_score | `SubagentScorer(gold_evidence, answer)` | `LLMClient.generate()` binary verify per evidence item |
| ③ 每个 replay 的 answer_score | `AnswerVerifier(answer, gold_answer)` | `LLMClient.generate()` binary verify |
| ③ self-supervision surrogate 评分 | 同上 `SubagentScorer` | `LLMClient.generate()` |
| ④ RepairExecutor action 选 | 可选 LLM (`require_llm_action=True`) | `LLMClient.generate()` 单调用 |
| ④ Post-Repair Context Replay | `agent_generate` + `evidence_scorer` + `answer_verifier` | `LLMClient.generate()` |

V1 仍为 **rule-based** 的节点（V2 主 paper 目标）：

| 节点 | V1 实现 | V2 目标 |
|---|---|---|
| ③ `assign_attribution_v1` → close_deltas | recovery_gain 排序纯函数 | **AttributionSubagent**：LLM 给 close_deltas + 因果解释 |
| ④ `draft_ecs_for_label` | 查表 `_ecs_for_label` → `get_targeted_repair_action_v1` | **CauseFinderSubagent + RepairPlannerSubagent**：LLM 推理 |
| ④ online_post_repair_validate | gold-based (`case.gold_evidence` + `case.gold_answer`) | **ValidatorSubagent**：无 gold pairwise original vs repaired |
| ⑤ FM retrieve | composite key BM25 检索 | **FMRetrieverSubagent**（可选）：LLM 语义检索 |

## 3. Runtime Loop 架构图（用户原图，权威）

```
query arrives
  ↓
① adapter.search(query) → retrieved items
  ↓
② hook
  │ empty-context judge
  │   ├─ true  → skip RPE → trigger CMD
  │   └─ false → RPE judge
  │              ├─ true  → top_k → trigger CMD
  │              └─ false → skip CMD
  ↓
③ CMD:
  │ run_adapter_replay_portfolio(case, adapter.supported_replays)
  │   └─ n/10 replays (adapter 能力决定)
  │   └─ 每个 replay 内: agent_generate(answer) + SubagentScorer(evidence_score) + AnswerVerifier(answer_score)
  ├─ if self_supervision_exists:
  │   └─ self-supervision: success-trace surrogate + counterfactual replay
  └─ assign_attribution_v1(replays) → close_deltas
  ↓
④ RepairOrchestrator(close_deltas, case, adapter, fm_context)
  │ for label in close_deltas:
  │   ecs = draft_ecs_for_label(case, label, replay)
  │   result = RepairExecutor(ecs, adapter).execute()
  │     ├─ adapter.apply_repair(ecs)
  │     ├─ adapter.search(query) → repaired_items
  │     └─ online_post_repair_validate(original, repaired)
  │        → recovered | partial | failed
  │   if recovered: break
  ↓
⑤ if recovered:
  │ fm_store.add(ecs)   # label + query_keywords + memory_top_terms
  │ return hook for validation
  ↓
⑥ return ecs memory
```

V1 阶段：②/③(评分部分)/④(repair action 可选) 已 LLM 化，③(attribution)/④(ECS draft)/④(validate online) 仍 rule-based。
V2 阶段：9-10 个显式 Subagent role 全覆盖 ②-⑥，加 Coordinator 做协作/缓存/cost。

## 4. Subagent Role 清单（9-10 个，V2 主 paper 显式架构）

| # | Role | V1 现状 | V2 升级 |
|---|---|---|---|
| 1 | **HookSubagent** (empty + RPE) | V1 hook/ 规则判定 | LLM judge |
| 2 | **AnswerSubagent** | V1 `agent_generate` callable | 显式 Subagent 类 + cache |
| 3 | **ScorerSubagent** | V1 `SubagentScorer/EvidenceVerifier/AnswerVerifier` | 合并为显式 Subagent 类 |
| 4 | **SelfSupervisionSubagent** | V1 `surrogate_gap.py` (离线 measure) | 在线版 |
| 5 | **AttributionSubagent** | V1 rule-based 排序 | **核心 V2 新作**：LLM close_deltas + 解释 |
| 6 | **CauseFinderSubagent** | V1 查表 | **核心 V2 新作**：LLM 推理 cause |
| 7 | **RepairPlannerSubagent** | V1 查表 (repair_guidance 固定文本) | **核心 V2 新作**：LLM 或 skill-workflow 推理 repair |
| 8 | **ValidatorSubagent** | V1 gold-based | **核心 V2 新作**：online no-gold pairwise |
| 9 | **FMRetrieverSubagent** (可选) | V1 composite key | LLM 语义检索 |
| 10 | **CoordinatorSubagent** | 无 | 跨 subagent 协作 / cache / cost |

7 是开放设计点：对比 **LLM-from-scratch** vs **skill-workflow-启发-LLM-自查**（见 EXPERIMENT.md D1/R1-R4）。

## 5. 三阶段修改建议

### 5.1 阶段 0：今天 → 06-10 V1.0 arxiv ship（16 天）

**完全不动结构。**

- critical path issue（0022 LLM eval wiring / 0023 at-scale re-test / 0024 130-case adjudication / 0026 Experiment 2 headline）一律不做附带 refactor
- 803 tests 维持绿
- 注意力守在 LLM 评分 stack 切换 + V1.0 paper artifacts 上

### 5.2 阶段 1A-min：V1.0 ship 后（半天）

**只做 1 件事**：AuditResult 谱系化（~60 行）

1. `harness.py:543` 的 `run_case_v1_with_hook_and_repair` 返回 `dict` 升级为 `RepairAuditResult` dataclass
2. 新增 `SessionAuditResult` stub（不暴露公开 API）

### 5.3 阶段 1A-full：V2 主 paper kickoff 锁定后（~2 周）

**新增 `cmd_audit/runtime/` 包**，结构跟随第 3 节图 ①→⑥：

```
cmd_audit/runtime/
  __init__.py
  session.py                 # RuntimeSession.handle_query 串 ①→⑥
  state.py                   # SessionState (fm_store, history, drift signals)

  hook/                      # 现有 hook/ 迁入；HookSubagent 包装

  subagents/                 # V2 全部 LLM 决策点
    base.py                  # Subagent ABC（此时抽出，容纳 10 role 真实形状）
    hook.py                  # HookSubagent (empty + RPE)
    answer.py                # AnswerSubagent
    scorer.py                # ScorerSubagent
    self_supervision.py      # SelfSupervisionSubagent
    attribution.py           # AttributionSubagent
    cause_finder.py          # CauseFinderSubagent
    repair_planner.py        # RepairPlannerSubagent (含 LLM vs skill-workflow 对比)
    validator.py             # ValidatorSubagent
    fm_retriever.py          # FMRetrieverSubagent (可选)
    coordinator.py           # 跨 subagent 协调 / 共享 trace / 缓存 / cost

  attribution.py             # 调 AttributionSubagent + adapter.replays
  ecs.py                     # draft_ecs_for_label runtime/subagent 版
  orchestrator.py            # RepairOrchestrator runtime 版（图 ④ 循环）
  validate.py                # online_post_repair_validate（图 ④ 末尾）
  fm.py                      # session 级 fm_store 包装（图 ⑤）
```

**约束**：

- V1 现有 `cmd_audit/{attribution, post_repair, repair_executor, repair_orchestrator, repairs}` **原地不动作 V1 paper 化石**
- runtime 不复用、不继承、不 backend 抽象 V1 实现
- Adapter 单类双 mode：`apply_repair(..., mode='sandbox'|'live')`
  - sandbox：保留 checksum 断言（V1 paper 复现 + V2 实验复现）
  - live：跳过 checksum 直接 mutate，**不进 paper main body**
- `Subagent` ABC 此时抽出，能容纳 10 role 的混合输出（结构化 + 自由文本 + cost + cache）
- `cmd_audit/__init__.py` 顺手从 ~200 符号瘦到 ~30 个稳定 V1 公共 API

### 5.4 阶段 2：V2 主 paper 实施期

```
experiments/
  run_v2_main.py              # V2 主 paper headline：端到端 runtime loop
  run_v2_ablation.py          # ablation: subagent role 数量 / coordinator 开关等
```

## 6. Adapter 双 mode 设计

V1 现有 `Mem0Adapter.apply_repair` 无条件 sandbox checksum。V2 扩展为双 mode：

- `mode='sandbox'`：保留现有 checksum 断言（V2 实验复现全部走此）
- `mode='live'`：跳过 checksum，直接 mutate `mem0.add/update/delete` + `mem0.search`（demo/dogfooding 用）

一个类，两条路径，无 mixin / ABC / 类型分裂。paper reviewer 看到的全为 sandbox 确定性可复现路径。

## 7. repair_guidance 开放设计点（V2 主 paper 核心对比）

`repair_guidance` 当前来自 `repairs.py:get_targeted_repair_action_v1(label)` 查表。V2 四种候选形态：

| 形态 | 实现 | LLM 调用 |
|---|---|---|
| R1 skill-template | V1 现有 rule-based 查表 | 0 |
| R2 LLM-paraphrase | template 喂 LLM，case 上下文重写 | 1/case |
| R3 LLM-from-scratch | 不用 template，纯 LLM 推理 | 1/case |
| R4 skill-启发-LLM-自查 | template 给 workflow，LLM 按步自查+改写 | 多调用/case |

**对比实验**见 EXPERIMENT.md D1 节。V2 主 paper 可扩展为"何时 LLM 推理 vs 何时 skill template"的设计选择章节。

## 7A. 冷启动 limitation（2026-05-25 决策）

V2 主 paper 的 online 置信度判定机制**只 claim 在成熟期（N > k）成立**，冷启动期 N=0..k 写入 limitations 章节，不在主 contribution 覆盖范围内。

### 7A.1 决策依据

线上无 gold 时检测低置信"错记忆"的"和之前记忆比对"思路至少分四种机制：

| 机制 | 比对对象 | 冷启动可用 |
|---|---|---|
| (a) cross-query consistency | 同 session 内更早 query 的 retrieval 结果 | ❌ N=0 时无对照 |
| (b) success-trace anchor | 同 session 内已成功的记忆 | ❌ success-trace 为空 |
| (c) FailureMemory pattern 匹配 | 已知失败 pattern (label + query_keywords + memory_top_terms) | ❌ FM 为空 |
| (d) intra-retrieval consistency | 单次 retrieval 内 items 互相对比 + items↔query | ✅ 不依赖历史 |

**(a)(b)(c) 在冷启动期不可用。(d) 是 V1 RPE Judge 16 维特征已经在做的事**（bm25_max/mean/std + item_count + near_duplicate + low_count + replay_type one-hot）。

### 7A.2 V2 冷启动期行为

冷启动期 V2 完全等于 V1 hook + V1 attribution + V1 ECS draft + ValidatorSubagent。即 N ≤ k 时：

```
hook = V1 RPE Judge (无 anchor)
attribution = V1 rule-based (recovery_gain 排序)
ecs draft = V1 rule-based (查表)
validator = ValidatorSubagent (online no-gold pairwise)  ← 唯一的 V2 升级
trigger threshold = conservative (宁多触发 CMD)
```

成熟期（N > k）才启用：

```
hook = V2 multi-source (RPE + cross-query + success-trace + FM pattern)
attribution = AttributionSubagent (LLM)
ecs draft = CauseFinderSubagent + RepairPlannerSubagent (LLM)
validator = ValidatorSubagent
```

**ValidatorSubagent 在冷启动期保留**：online no-gold validation 不依赖 anchor 历史，单 case 就能跑。

### 7A.3 k 怎么定

k 不是拍脑袋数字，是实验估的。V2 主 paper 跑 N-vs-accuracy 曲线实验，找出 cross-query consistency 与 ground truth 收敛的 N 值。这本身就是 limitations 章节的一张 figure。

预估 k≈5-10（基于 LongMemEval session ~50 turns 的 10-20%），但具体值留给 V2 实验确定。

### 7A.4 V2 paper limitations 章节叙事

> Our online confidence assessment relies on cross-query and success-trace anchors that accumulate over a session. For the cold-start period (first k queries), the system falls back to per-retrieval-internal signals only (intra-retrieval consistency, query-item alignment), with a conservative trigger threshold. Empirically we find k≈[实验值] across our three datasets; smaller-context deployments may experience degraded confidence assessment in early queries.

这是 session-level system 的共同 limitation，不是 V2 独有，reviewer 通常接受。

### 7A.5 V1.0 paper 不涉及

冷启动是 V2 online runtime 的问题。V1.0 paper 是 offline counterfactual attribution，每个 case 独立，无 session 概念，冷启动不存在。**这一节仅约束 V2。**

## 8. 触发信号与执行入口

| 触发信号 | 执行 |
|---|---|
| V1.0 arxiv 已 ship (06-10 之后) | 起半天 issue：`AuditResult dataclass 谱系化`（阶段 1A-min） |
| V2 主 paper kickoff 时间锁定 | 起 meta issue：`runtime/ 包新增 + 9-10 subagent role`（阶段 1A-full） |
| V2 主 paper 实验启动 | 写 `experiments/run_v2_main.py`（阶段 2） |

阶段间不允许跳跃执行。

## 9. V1 → V2 缺口分析

### 9.1 校准结论

| 判断 | 结论 |
|---|---|
| V1 代码构建完成 | ✅ 真。V1 paper 工件完整，803 tests 绿 |
| "只需补充少量环节"就能完成 V2 | ❌ V2 需 ~4370 LOC（~V1 总量的 48%），3-4 个核心 subagent 从零 LLM 实现 |
| V1 为 V2 做了什么 | V1 把**所有非 LLM 周边**（replay 框架 / FM / retrieval baseline / metrics / provenance / adapter cut points / test infra / ablation 工具）都建好了 |

### 9.2 缺口归类

**几乎无缺口（直接复用）**：③ replay portfolio / ⑤ FM / ⑥ return

**小缺口（挪位置 + 加 cost/cache）**：② HookSubagent 化 / ③ Answer+Scorer Subagent / ④ RepairExecutor LLM action 默认

**中等缺口（框架在语义重做）**：③ self-supervision 在线版

**大缺口（rule-based → LLM 从零实现）**：③ AttributionSubagent / ④ CauseFinderSubagent + RepairPlannerSubagent / ④ ValidatorSubagent (online no-gold) / ①+④ adapter live mode

### 9.3 LOC 估计

| 项目 | LOC |
|---|---|
| `runtime/subagents/` 10 个 role (含 base ABC) | ~1510 |
| `runtime/` 编排层 (session/state/attribution/ecs/orchestrator/validate/fm) | ~600 |
| Adapter live mode (mem0 + letta) | ~300 |
| 实验脚本 | ~400 |
| 测试 | ~1500 |
| **总计** | **~4370** |

## 10. 非显然结论

### 10.1 V1 已经是 LLM-driven 评分

V1 阶段 `agent_generate` + `SubagentScorer` 已经是 LLM 评分。phrase-match 不走 paper。V1 的 novelty 不是"没用 LLM → 用了 LLM"，是"rule-based counterfactual attribution 可以通过 replay portfolio + LLM 评分实现"。

V2 的 novelty 是：把单 prompt LLM 调用升级为显式 subagent 协作架构，并 closure of 2 remaining rule-based gaps (attribution + ECS draft) + online no-gold validation。

### 10.2 不进 paper 的组件不获得 paper-grade 抽象

live adapter 不发论文 → 不抽 mixin / ABC / 类型分裂。一个 mode kwarg 解决。

### 10.3 ABC 抽出时机等多态需求落地

`Subagent` ABC 必须等到 V2 真实 10 role 落地时再抽（阶段 1A-full）。三个 V1 LLM 调用类形态不足以定 ABC 形状。

### 10.4 repair_guidance 是实验维度，不是已定方案

不要预设"LLM 推理一定比 skill template 好"。V1 阶段（EXPERIMENT.md D1）和 V2 阶段都保留 R1-R4 对比。论文论述可以走"主动选择何时 LLM、何时 skill"路线。

## 11. 文档维护

- 随 V2 timeline 决策驱动更新
- 阶段 1A-min/full 执行完成后标注 ✅ 完成日期，不删除内容（保留决策追溯）
- subagent role 清单若增减，更新第 4 节
- V2 paper 形状若进一步确定，更新第 1 节
