# CMD 研究进度报告

**项目**: Counterfactual Memory Debugger (CMD) — 面向 LLM Agent 记忆的反事实记忆调试器
**日期**: 2026-05-24
**状态**: V0 完成且锁定。V1 实现层完成 (issues 0011-0021, 803 tests pass)。V0→V1 / V1→V2 gate 已通过 mechanics validation。**2026-05-23/24 grilling (Decision 34 R1-R11)** 识别 596-case Macro F1 = 1.000 为 phrase-match shortcut 产物, 非 paper-grade 归因证据。**当前阻塞 paper headline**: LLM eval infra 未接通, 130-case researcher adjudication (LLM-A + spot-check) 未启动。**V1.0/V1.1 双发布**: V1.0 arxiv preprint target 2026-06-10 (596-case dataset state); V1.1 venue submission post-issue-0035 (full-corpus dataset state)。Critical path V1.0: LLM eval infra → 596 re-test → 130 adjudication → Experiment 2 → arxiv preprint。Hook (Decision 33 / issue 0021) 已实现并降级为 supplementary。CMD vs Rewind head-to-head dropped, 替换为 related-work layered positioning。详见 REPAIR.md + issues 0022-0036。

---

## 1. 项目概述

CMD 是一个反事实回放框架，用于诊断 LLM Agent 的记忆失败究竟来自哪个 memory operation（写入、压缩、抽取、检索、注入、推理），而非仅看最终答案对错。诊断后生成 Error-Cause-Solution (ECS) 记录，指导定向修复，并将修复经验沉淀为 Failure Memory 供未来相似任务检索。

CMD 占据一个已验证的空白：截至 2026-05-14，65+ 篇论文和 14+ 个 GitHub 项目中，**无一实现自动化的反事实记忆操作级归因**。CMD 的 Failure Memory 闭环被四个独立来源验证（skill-everything, ErrorProbe, SQLFixAgent），其中 Post-Repair Context Replay 是唯一全自动化的语义质量把关。Day 4 新发现：consolidation fragility 被受控因果实验确认（2605.12978），MEMOREPAIR cascade repair 可直接集成，detection→diagnosis→repair 鸿沟被 6 个仅覆盖单环节的系统进一步证实。

---

## 2. 整体进度

```
V0 (CMD-Audit standalone)          V1 (Skill Adapter + 11-label)       V2 (Runtime repair loop)
████████████████████████████████    ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░    ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
        已完成                              已规划                           已规划
```


| 阶段             | 状态               | 产出                                                                                      |
| -------------- | ---------------- | --------------------------------------------------------------------------------------- |
| **V0**         | 完成且锁定            | 6-label 归因 pipeline、ECS、Post-Repair Replay、Failure Memory、version gates                 |
| **V0→V1 Gate** | HITL approved      | V0 LOCKED；需 probe suite 扩展到 50-100+ 个用于 paper-claim 证据                                             |
| **V1**         | 进行中 (6/8 issues, 0017 complete) | 11-label pipeline、10-replay V1 portfolio、coupled-failure recalibration、memory-probe baseline、mem0 adapter、Letta adapter、V1→V2 gate、Pre-CMD Gate 架构 (设计中, Decision 31)、RPE prefilter (issue 0017-1, code + spec)、provenance tracking (issue 0017-2, ✅ implementation complete)、PrefixGuard (issue 0017-1 Tier-1, code + spec) |
| **V2**         | 已规划              | 运行时修复闭环、contrastive context mode、Failure Memory skill evolution                         |


---

## 3. V0 完成清单

### 3.1 Issues（全部完成）


| Issue | 内容                                                                                                                               | 测试  |
| ----- | -------------------------------------------------------------------------------------------------------------------------------- | --- |
| 0001  | Probe dataset + gold evidence contract + Oracle Retrieval tracer bullet                                                          | ✅   |
| 0002  | Fixed-summary/vector baselines + evidence-recall/subagent-judge/random comparators + leak-safe monitor                           | ✅   |
| 0003  | 6-replay V0 attribution table（Oracle Write/Compression/Retrieval + Verbatim Event + Injection-Oracle + Evidence-Given Reasoning） | ✅   |
| 0004  | Taxonomy boundary review — V0 6-label 确认                                                                                         | ✅   |
| 0005  | Post-Repair Context Replay（3-value `repair_assessment`: recovered/partial/failed）                                                | ✅   |
| 0006  | Targeted memory fixes（6 per-label repair actions + repair comparison table）                                                      | ✅   |
| 0007  | ECS Failure Memory recurrence（3-mode comparison: none/full_trace/corrected_guidance）                                             | ✅   |
| 0008  | V0.5 retrieval baseline strengthening（BM25 only, 6 hard negatives; HybridRerank removed）                                           | ✅   |
| 0009  | Subagent Judge Monitor contract hardening（enum-locked `anomaly_reason`）                                                          | ✅   |
| 0010  | Evidence-driven version gates（V0→V1 4-criteria check, V1→V2 stub, HITL pipeline）                                                 | ✅   |
| 0011  | `ingestion_error` + `route_error` V1 标签扩展（8-label pipeline, 7-replay portfolio, 44 tests）                                        | ✅   |
| 0012  | `granularity_error` + `graph_error` + `safety_error` 标签扩展（11-label pipeline, 10-replay portfolio, 81 tests）                       | ✅   |
| 0013  | 11-label coupled-failure recalibration + memory-probe baseline（`top_k` 参数、`close_deltas` 透明暴露、3x2 grid comparator、42 tests）        | ✅   |
| 0014  | mem0 adapter integration（`Mem0Adapter` 2-cut-point interception, recorded-trace mode, sandbox checksum, adapter-label parity, 30 tests） | ✅   |
| 0015  | Letta adapter + V1→V2 gate（`LettaAdapter` 3-cut-point interception, tripartite memory model, cross-agent non-regression, V1→V2 gate pass, 44 tests） | ✅   |


**总测试**: 645 tests pass。Issues 0001-0017 done。Issue 0019 Phase A complete。Pre-CMD Gate 架构设计中（2026-05-21, Decision 31）：两层自监督门控（Layer 0: PrefixGuard + RPE at `post_retrieve`, Layer 1: Post-Answer Gate at `post_reason`），ECC per-adapter 模式，代码尚未实现。Issue 0017-1 (RPE prefilter + PrefixGuard) implementation details spec complete (838 lines, `issues/0017-1-rpe-prefilter-implementation-details.md`)。Issue 0017-2 (Provenance tracking) implementation complete (78 tests, detail map: `issues/0017-provenance-tracking-execution-lineage-dag-implementation-details.md`)。Issue 0019 (Subagent scoring) preparation — A/B split decided + grill-with-docs design refinements (2026-05-21): Decision A = EvidenceVerifier active, AnswerVerifier deferred to Decision B; new modules: `llm_client.py` (provider-agnostic), `subagent_runner.py` (Claude Code-style isolated subagent), `llm_judge.py`, `llm_scoring.py`, `hooks.py`. Spec: `issues/0019-subagent-scoring-context-isolation.md`。

### 3.2 Evidence Artifacts


| Artifact                           | 用途                         | 状态  |
| ---------------------------------- | -------------------------- | --- |
| `attribution_table.csv`            | 6-replay 归因表               | ✅   |
| `comparison_metrics.csv`           | CMD vs baselines 对比        | ✅   |
| `attribution_confusion_matrix.csv` | 混淆矩阵                       | ✅   |
| `post_repair_table.csv`            | Post-Repair Context Replay | ✅   |
| `repair_success_table.csv`         | 定向修复效果                     | ✅   |
| `repair_claim_ledger.txt`          | 修复声明账本                     | ✅   |
| `recurrence_comparison.csv`        | Failure Memory 复发对比        | ✅   |
| `V0V1_gate_status.txt`             | V0→V1 gate 检查              | ✅   |


### 3.3 V0→V1 Gate 状态


| 准则                                  | 阈值                                            | 当前值 (6 smoke cases)            | 结果   |
| ----------------------------------- | --------------------------------------------- | ------------------------------ | ---- |
| Macro F1 > baselines                | CMD > evidence_recall, subagent_judge, random | CMD=1.000, baselines≤0.833     | PASS |
| Confusion matrix diagonal dominance | diagonal > off-diagonal per label             | 6/6 diagonal=1, off-diagonal=0 | PASS |
| Attribution + top-2 accuracy        | CMD > all baselines                           | CMD=1.000, best baseline=0.833 | PASS |
| Repair assessment distribution      | recovered ≥ 50%, recovered+partial > failed   | 6/6 recovered                  | PASS |


**注意**: 以上全部在 6 个 smoke case（每 label 1 个）上通过。扩展到 50-100 case 后预计会回落，需 HITL review 确认阈值仍然成立。

---

## 4. V1 规划（2026-05-11 完成规划）

### 4.1 V1 Issues


| Issue | 内容                                                             | 依赖            | 状态     |
| ----- | -------------------------------------------------------------- | ------------- | ------ |
| 0011  | `ingestion_error` + `route_error` 标签扩展                         | —             | ✅ done |
| 0012  | `granularity_error` + `graph_error` + `safety_error` 标签扩展      | probe scaling | ✅ done |
| 0013  | 11-label coupled-failure recalibration + memory-probe baseline | 0012          | ✅ done |
| 0014  | mem0 adapter 集成（第一个真实 agent 目标）                                | 0013          | ✅ done |
| 0015  | Letta adapter 集成 + V1→V2 gate                                  | 0014          | ✅ done |
| 0016  | RPE pre-filter（evidence-surprise scoring + top-k replay selection） | 0015          | CODE EXISTS (275行 untracked) |
| 0017  | Provenance tracking（Execution Lineage DAG + trace-mem citation, 9 ACs, 78 tests）  | 0015          | ✅ done |
| 0017b | PrefixGuard（Tier-1 rule-based anomaly detection） | 0015          | CODE EXISTS (93行 untracked) |
| 0018  | Real data integration（596 cleaned + 3×200 real probe cases）    | 0012          | AFK    |
| 0019  | Subagent-based LLM scoring (Phase A ✅: `llm_client.py` + `llm_judge.py` + llm_judge comparator, 32 tests; Phase B: `subagent_runner.py` + `llm_scoring.py` + `hooks.py`) | — | Phase A done |
| —     | Paper limitations（`cmd_innovation_core/plans/limitations.md`，8 limitations + Decision 29） | —             | ✅ done |


### 4.2 V1 关键设计决策


| 决策             | 内容                                                                                                                                         |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| 标签扩展顺序         | pipeline labels 优先: `ingestion_error` → `route_error` → `granularity_error` → `graph_error` → `safety_error`。bad memory item labels 延后到 V2 |
| 第一个 Adapter 目标 | **mem0** (55k stars, YC S24)，最简洁 memory API (`add()`/`search()`)，SOTA on LoCoMo/LongMemEval                                                |
| 第二个 Adapter 目标 | **Letta** (22.6k stars)，core/archival/recall tiering，可测试 mem0 扁平存储无法覆盖的 `route_error`                                                      |
| 论文范围           | V0 + V1 + V2 共同构成一篇论文，V2 为最终 module/skill                                                                                                  |
| RPE pre-filter | V1 后期优化，非 gate 前提                                                                                                                          |
| 真实数据           | LoCoMo/LongMemEval 混入 V1 probe，数据构建由研究者负责                                                                                                  |


---

## 5. Prototype 状态


| Prototype                                 | 语言      | 状态  |
| ----------------------------------------- | ------- | --- |
| CMD Probe Logic                           | EN + ZH | ✅   |
| Post-Repair Assessment & Monitor Contract | EN + ZH | ✅   |
| RPE Monitor Pre-Filter                    | EN + ZH | ✅   |
| mem0 Adapter Interface                    | EN + ZH | ✅   |


---

## 6. 当前阻塞项


| 阻塞项                                  | 状态 | 下一步                                |
| ------------------------------------ | ------ | ---------------------------------- |
| ~~**Probe suite scaling** (6 → 50-100)~~ | ✅ 已解除 (2026-05-19) | 596 cleaned cases + 596 real probe cases 确认可用 |
| **HITL gate review**                 | 等待中 | 在 596 cases 上验证 V0→V1 gate 四准则 |


---

## 7. 两个待执行实验


| 实验                     | 目的                                                                    | 数据集                                                                                     | 样本量                       | 依赖          |
| ---------------------- | --------------------------------------------------------------------- | --------------------------------------------------------------------------------------- | ------------------------- | ----------- |
| **实验二: CMD 归因有效性**     | CMD 能否在已知 perturbation 下正确识别失败的 memory operation                      | Probe Case（含注入的 `perturbation_type` + `expected_behavior`）                              | 50-100 (V0), 100-150 (V1) | 无           |
| **实验一: Context 拼接有效性** | `wrong_memory + cause + corrected_memory` 是否比纯 `corrected_memory` 更有效 | 4-Mode Context Case（同一 query × 4 种 context: none/full_trace/corrected_only/contrastive） | 15-40                     | 实验二的 ECS 产物 |


**构建顺序约束**: 实验二 → 实验一（Context Case 的 `wrong_memory`/`cause`/`corrected_memory`/`repair_guidance` 均来自 CMD 对 Probe Case 产出的 ECS）。

**关键发现**: 40+ 篇论文中无一篇提供 4-mode context construction 对照实验。实验一无论结果是正是负，本身构成 novelty contribution。

---

## 8. Metabolism 状态


| Day   | 日期         | 新论文                                                                                                                                                                                                                                       | 新假设                                              |
| ----- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| Day 0 | 2026-05-10 | 27 papers, 10 repos (基线构建)                                                                                                                                                                                                                | hyp-001 ~ hyp-012                                |
| Day 1 | 2026-05-11 | 9 papers, 1 repo (MemFlow, ErrorProbe, MemEvoBench, Memora 等)                                                                                                                                                                             | —                                                |
| Day 2 | 2026-05-12 | 4 papers (PrefixGuard, MAGE, MemORAI, Trojan Hippo)                                                                                                                                                                                       | hyp-013 (PrefixGuard-CMD 两层架构)                   |
| Day 3 | 2026-05-13 | 3 papers (Intent Gap, Skill as Memory, Agent Skills Survey) + 4 repos (skill-everything, MemoryOS, memory-poisoning-demo, portable-agent-memory)                                                                                          | hyp-014 (Failure Memory 四源收敛 + Quality Gate 差异化) |
| Day 4 | 2026-05-14 | 18 papers (Memory Becomes Faulty, MEMOREPAIR, Decision-Centric Memory, Spurious Correlations, Scale-Conditioned Eval, Cognifold, LongMemEval-V2, Nautilus Compass, ShadowMerge, Storage→Experience Survey, SAGE, HAGE + 6 lower-priority) | hyp-015 (Episodic-Trace Anchored Cascade Repair) |
| Day 5 | 2026-05-15 | 24 papers (HolisticEval, LIFE Survey, Counterfactual Responsibility, ConformalAttr, MASPrism, MemLineage, EvolveMem, Traversal Context, PACT, MEMSAD, PROBE, AgentForesight, MemQ, Memory Curse, TRACER, Execution Lineage, PYTHALAB-MERA, FATE, PRISM, GroupMemBench, MemEye + 3 lower-priority) | hyp-016 (Operation-Level Counterfactual Attribution as Differentiator) |
| Day 6 | 2026-05-18 | 1 paper (Premature Closure) + 1 benchmark (LOBSTER-Bench) + 1 peripheral (IBTS)                                                                                                                                                           | —                                                |
| Day 7 | 2026-05-19 | 4 papers (VerifyMAS, MemRepair, SE-GA, DiagEval) + 5 repos (TraceAudit, trace-mem, RecMem, DebugMind)                                                                                                                        | hyp-017 (Multi-Resolution Counterfactual Attribution) |
| Day 8 | 2026-05-19 | Issue 0013 complete: coupled-failure recalibration + memory-probe baseline. 387 tests pass.                                                                                                                                  | —                                                |
| Day 9 | 2026-05-20 | 4 papers (STAR, MemMA, Memory Worth, MemReader) + 5 repos (Culpa, Rewind, TraceForge, CausalOS, AgentRx). Key: **counterfactual tooling explosion** — 3+ tools converging on record→replay→fork→measure.              | hyp-018 (Counterfactual Replay as Standard Debugging Primitive) |
| Day 10 | 2026-05-20 | Decision 30 resolved: accelerate to 2026-06-10, memory-diagnostic-layer positioning, 5-dimension depth differentiation vs Rewind, repair depth metric (Level 0→3), head-to-head comparison required at the time (superseded by Decision 34). | — |
| Day 13 | 2026-05-23 | Decision 34 grilling start: R1-R7 — phrase-match shortcut identified, headline binds to 130 researcher-adjudicated cases, Post-Repair gets agent_generate + AnswerVerifier, hook → supplementary, Rewind head-to-head dropped, Experiment 1 hardened to 80 × 5 modes. | — |
| Day 14 | 2026-05-24 | Decision 34 grilling close: R8-R11 — Q11 FM recurrence collapsed into Experiment 1, Q12 repair-depth as design claim, Q13 AttributionFailed as principled abstention, V1.0/V1.1 dual-release pattern, LLM-A = llama-3.3-70b-instruct for adjudication, bootstrap CIs everywhere, cost/latency in headline. 13 issues (0022-0034) + 2 supplementary (0035/0036) written. | — |


**当前文献库**: 124 processed_ids，覆盖 agent memory、failure diagnosis、memory security、retrieval evaluation、context construction、skill lifecycle、consolidation fragility、cascade repair、graph memory、counterfactual auditing、verifiable memory、counterfactual debugging tooling 等领域。

**Day 3 关键发现**: "再也不会犯同一个错误"（never make the same mistake twice）闭环被四个独立来源验证，CMD 不应声称发明该闭环。CMD 的差异化在于闭环中两个关键环节的自动化：

**Day 4 关键发现**:

1. **Consolidation fragility 因果确认**: 2605.12978 受控实验证明 LLM consolidation 系统性退化记忆——GPT-5.4 用 ground-truth solution 做 consolidation 后，对曾解出的问题失败 54%。三种失败模式 (misclassification/interference/overfit) 映射到 CMD labels。
2. **Cascade repair 形式化**: MEMOREPAIR barrier-first 合约 + s-t min-cut 优化，无效记忆暴露降至 0%。CMD 归因 + MEMOREPAIR cascade 处理 = 完整 detection→diagnosis→repair→validation 链路。
3. **Detection→Diagnosis→Repair 鸿沟**: 5 个 detection-only + 1 个 repair-only 系统，无一覆盖全链路。CMD 是唯一横跨三层的架构。
4. **Episodic trace anchoring**: 3 篇独立论文在不同角度收敛于"原始 episodic traces 应作为记忆操作 ground truth"。


| 对比    | ErrorProbe          | skill-everything | CMD                                   |
| ----- | ------------------- | ---------------- | ------------------------------------- |
| 证据类型  | 观测性（backward trace） | 人类判断             | 反事实（counterfactual replay）            |
| 诊断目标  | Multi-agent step    | 自由文本描述           | Memory pipeline operation (6 labels)  |
| 质量把关  | 可执行证据（pattern 被确认过） | 人类 PR review     | Post-Repair Context Replay（重跑原 query） |
| 自动化程度 | 诊断自动化，验证半自动         | 全人工作业            | 诊断+验证全自动                              |


CMD 的 paper claim 应聚焦：(1) 自动化反事实归因（vs 人类诊断/观测性 trace），(2) Post-Repair Context Replay 作为自动化语义 quality gate（vs 人类 PR review/可执行证据/语法校验）。

**Day 5 关键发现**:

1. **Attribution subfield emergence**: 5+ distinct methods in a single week. Shapley-value (2605.13077) closest formal work. CMD differentiated at operation-level granularity.
2. **Provenance convergence**: 5 papers, provenance becomes V1 requirement.
3. **hyp-016**: Operation-Level Counterfactual Attribution as Differentiator in Crowding Subfield.

**Day 6 关键发现**:

1. **LOBSTER-Bench**: First long-lived agent observability benchmark, real 21-agent/7-day data, emergent cascade failure. Validates CMD premise.
2. **2605.15000 Premature Closure**: 55-81% false-action rate, maps to `reasoning_error`.

**Day 7 关键发现**:

1. **Counterfactual convergence across granularities**: 3 counterfactual attribution systems (TraceAudit chunk-level, VerifyMAS agent-level, CMD operation-level) independently developed within 2-week window. Counterfactual is converging as THE standard methodology for agent failure attribution.
2. **TraceAudit (github, AAAI 2027)**: ⚠️ Most direct counterfactual competitor. Chunk-level counterfactual auditing of RAG with pre-registered hypotheses. Differentiated from CMD by granularity (chunk vs operation), purpose (audit vs diagnosis+repair), and target (external RAG vs own memory pipeline).
3. **VerifyMAS (2605.17467)**: Agent-level hypothesis verification for MAS failure attribution. Error-first approach. Differentiated from CMD by evidence type (observational verification vs causal replay).
4. **RecMem (ACL 2026 Findings)**: Recurrence-based consolidation independently validates `compression_error` and `premature_extraction_error`.
5. **trace-mem (github)**: Counterfactual ingestion gate ("admit only if accuracy improves") — preventive counterfactual, complements CMD's retrospective counterfactual.
6. **hyp-017**: Multi-Resolution Counterfactual Attribution — the subfield is self-organizing into a granularity spectrum with counterfactual as the common thread.

---

## 9. 下一步行动 (V1.0 06-10 + V1.1 post-corpus, Decision 34 R10)

参见 `TASK.md` § "Next Steps (ordered by dependency)" 16 项 forward-only 列表。Critical path V1.0: LLM eval infra → 596 re-test → 130 adjudication → Experiment 2 → arxiv preprint. V1.1 trigger: issue 0035 corpus migration cutover.

**Paper claim 绑定 V1.0**:
- 主结论 (Experiment 2): 130 researcher-adjudicated cases 经 LLM-A 辅助 + 20-case 盲测试，CMD Macro F1 + bootstrap CI vs llm_judge / evidence_recall / random
- 第二结论 (Experiment 1): 80 cases × 5 modes 上下文构建 + token-control + McNemar's Δ_1 + Δ_2
- Scale sanity: 596 cases vs deepseek-v4-pro-max annotator agreement + Cohen's κ
- Online deployment: ~9/11 label coverage + retention% on 4 gold-dependent labels (50-case hold-out)
- Cost/latency: 头条表内列, agent + scorer + verifier 分项
- Supplementary 1: hook efficacy (recall + cost reduction)
- Supplementary 2: coupled-failure 30-50 near-tie cases + post-hoc tie_margin
- Supplementary 3: surrogate-gap retention % (issue 0036)
- Cross-dataset claim: V1.0 coverage only (per-source heatmap with bootstrap CIs)
- Related work: layered-stack positioning, no head-to-head benchmark

**V1.1 expansion** (post-issue-0035): cross-dataset claim → explicit generalization; same metrics on full-corpus N.
