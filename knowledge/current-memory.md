# Current Compressed Memory

更新日期: 2026-05-23

## 当前状态

方向筛选完成。选用 **CMD: Counterfactual Memory Debugger for LLM Agent Memory** 作为主研究方向。V0 完成且锁定，V1 进行中（issues 0011-0021 完成，803 tests pass）。V0→V1 gate HITL approved；V1→V2 gate 技术通过（mem0 + Letta 双 adapter，Macro F1 1.000）。Issue 0019 Phase A+B 完成。Issue 0020 Decision 32 post-gate pipeline 全部完成（91 tests, 0 regressions）。2026-05-23/24 grilling identified phrase-match-shortcut + LLM-annotator-circularity + tie_margin issues; Decision 34 R1-R11 captures the V1.0/V1.1 re-test plan.

## Issue 0019: Subagent Scoring (2026-05-21)

`_score_recovered_evidence` 有两个独立短路：(A) phrase-match 评分，(B) `answer = case.gold_answer` 替代。A/B 拆分：

- **Decision A（issue 0019 scope）**：用 `SubagentScorer` 替换 phrase-matching。
- **Decision B（deferred）**：Agent 重跑 + 真实 LLM 输出。

### Phase A 完成 — LLM-as-Judge Baseline

```
llm_client.py          ← provider-agnostic LLM API（Phase A/B 共享）✅
llm_judge.py           ← prompt构建 + 输出解析（仅可观测 artifacts，无 gold 泄露）✅
baselines.py           ← llm_judge 作为第4个 comparator，run_llm_judge_baseline with fallback ✅
```

32 tests, 8 classes. llm_judge 自动出现在 comparison_metrics.csv（零 harness.py 改动）。
Detail map: `issues/0019-phase-a-llm-judge-baseline-implementation-details.md`.

### Phase B 完成 — Subagent Scoring (2026-05-21)

**文件清单:**
```
llm_client.py           ← generate(prompt, *, system=None) 新增 system 参数 ✅
llm_scoring.py          ← EvidenceVerifier + AnswerVerifier + SubagentScorer ✅
replays.py              ← _score_recovered_evidence 新增 scorer 参数 ✅
__init__.py             ← 5 个新符号导出 ✅
tests/test_*_phaseb_*   ← 39 tests, 8 classes ✅
```

**未创建:** `subagent_runner.py`（合并入 llm_scoring.py）、`hooks.py`（合并入 llm_scoring.py 内部校验函数）。

**实现架构:**
- `SubagentScorer.__call__(gold_evidence, text) -> float` — scorer 契约，对齐 `evidence_recall_from_text` 签名
- `EvidenceVerifier.verify(fact, text) -> str` — 原子二元判定（PRESENT|ABSENT），per-fact 隔离
- `AnswerVerifier.verify(answer, gold_answer) -> str` — 语义等价判定（EQUIVALENT|NOT_EQUIVALENT），Decision B 待接入
- 并行策略：`ThreadPoolExecutor`，replay 内 evidence 并行，max_workers 可配置
- Fallback 链：`llm_client=None` → phrase fallback；parse fail → retry with firm prompt → ABSENT/NOT_EQUIVALENT
- Context 隔离：`_validate_context_isolation` 在 EvidenceVerifier.verify() 内部强制执行，外部不可绕过
- 输出校验：`_validate_output_format` 内部调用，非二元输出重试一次后降级

### 关键设计决策（Phase B 实施中确认）

1. **无需 `subagent_runner.py`**：隔离由 `EvidenceVerifier` 内部 prompt 构建 + `_validate_context_isolation` 保证，无需额外进程隔离层。

2. **无需 `hooks.py`**：`validate_context_isolation` + `validate_output_format` 作为 `llm_scoring.py` 内部私有函数，在 SubagentScorer 内部强制执行，外部调用方不可绕过。

3. **`scorer` 参数契约**：`Callable[[tuple[GoldEvidence, ...], str], float]` — 对齐 `evidence_recall_from_text` 签名。`SubagentScorer.__call__` 直接实现此契约，`_score_recovered_evidence` 只改一行。

4. **逐层降级**：`llm_client=None` → 直接走 phrase fallback。格式错误 → 加强 prompt 重试一次 → 仍失败则该 evidence 返回 ABSENT。整体并行执行异常 → phrase fallback。

## Issue 0020: Decision 32 Post-Gate Pipeline (2026-05-23, complete)

All 8 subtasks implemented with TDD. New modules: `repair_executor.py`, `repair_orchestrator.py`, `surrogate_gap.py`.

| 子任务 | 内容 | 测试数 |
|--------|------|--------|
| 0020-H | ECSDraft.cascade_candidates (V1 空列表预埋) | 7 |
| 0020-B | RepairAction (5 action_types) + adapter.apply_repair | 19 |
| 0020-D | FailureMemoryStoreV1 (复合 key) + fm_context | 18 |
| 0020-A | RepairExecutor (stateless 单次) + RepairOrchestrator (迭代) | 15 |
| 0020-G | draft_ecs_for_label (任意 label ECS) | 9 |
| 0020-F | AuditResult.hook_stage/selected_replays/fallback_triggered | 6 |
| 0020-C | run_case_v1_with_hook_and_repair (hook+修复集成) | 6 |
| 0020-E | Self-supervision surrogate (surrogate vs gold gap) | 11 |

**RepairAction**: `action_type` ∈ {append, replace, relocate, update_routing, update_template}, LLM tool definition JSON schema, adapter 声明 `supported_actions`（mem0: append/replace, Letta: append/replace/relocate/update_routing）。

**RepairExecutor**: `(ECS draft, adapter, case, fm_context) → RepairExecutorResult`。Heuristic action type selection 基于 label → action 映射，fallback 到 adapter.supported_actions[0]。

**RepairOrchestrator**: 遍历 `attribution.close_deltas`，对每个 label 调用 `draft_ecs_for_label → RepairExecutor.run`，停在第一个 `recovered` 或 exhaust。

**Failure Memory v1**: 三维复合检索 key (label + query_keywords + memory_top_terms)，`fm_context = wrong_memory + original_evidence`（诊断信号），与 `corrected_memory`（修复信号）互补。

**Surrogate gap**: 4 gold-dependent labels (write/compression/premature_extraction/injection) 的 surrogate vs gold recovery gain 测量。`measure_surrogate_gap(case) → SurrogateGapRow`。产出 paper gap 数据，不训练。

## Decision 33: Hook 重设计 — 两阶段顺序门控 + RPE Judge (2026-05-22, updated 2026-05-23 grilling)

将 Decision 31 的五分支并行 OR 重设计为两阶段顺序架构(PrefixGuard 独立阶段于 2026-05-23 移除;9 项 build-detail 决议于 2026-05-23 grilling session 资料化):

```
empty_ctx 硬短路 (len(items)==0 → 全量触发,stage="empty_ctx")
  → RPE Judge (16 维 LR + replay_type one-hot, per-replay p 分 → top-k=3 选择, stage="rpe_top_k")
    → fallback (max(p) < FALLBACK_THRESHOLD → 跳过, stage="rpe_below_threshold")
```

**关键设计**:RPE judge 共享 LR 模型(16 系数:6 全局 + 10 replay_type one-hot,逻辑回归,零 LLM 推理)。**特征收窄到 16 维**(grilling 决议 #4):删 safety_filter_blocked / is_graph_expanded / store_count 三个 metadata 信号(避免 train/serve skew,replay_type one-hot 已表达);`item_count` cap+归一 `min(x,10)/10`。**训练 label 用 SubagentScorer**(qwen2.5-7b ollama,offline LLM,~5-15 min;cache + 失败 fallback to phrase-match;deployment hook 仍零 LLM)。离线校准三步(权重训练 zero-LLM-inference → surrogate gap 测量需 LLM → **全局阈值校准** zero LLM,2 阈值 84 网格点 joint grid search,F2;per-agent 推迟 V2)。Offline 训练用 596 cases 拆 train(546) / hold-out(50)。**Hook 子包**:`hook/rpe_judge.py` + `hook/post_retrieve_hook.py` + `hook/constants.py`。`PreCmdDecision` 三 stage 值 `"empty_ctx" | "rpe_top_k" | "rpe_below_threshold"`,无 `fallback_triggered`(改由 `stage` 推导)。`mode="online"|"offline"` flag:online 路径 empty_ctx 用 sentinel ReplayScore (`is_sentinel=True, p_score=-1.0`),offline 路径全跑 RPE Judge(供 paper hook 有效性分析)。**两 PR 切分**:PR1 (issue 0021) 新增 hook/ + 删 issue18 tests + 部分删 issue16 tests;PR2 (issue 0021-cleanup) 删 5 个旧模块 + `run_case_v1_with_prefilter` + CLI flag rename + AuditResult.fallback_triggered 字段移除。Issue 0021。设计文档: `issues/0021-hook-redesign-three-stage-rpe-judge.md`。

## Pre-CMD Hook 实现完成 (2026-05-22, Issue 0018, Decision 31) — 将被 0021 替代

单一 `post_retrieve_hook(query, retrieved_items) -> PreCmdDecision`，零 gold 在线门控。PrefixGuard 4 个确定性信号 + RPE BM25 surprise × utility。五分支优先门控：empty_ctx → utility_override → rpe_above_threshold → anomaly_above_threshold → clean。Issue 0021 将重写为两阶段顺序架构（empty_ctx 硬短路 → RPE Judge top-p）。

### 文件清单

| 文件 | 内容 | 状态 |
|------|------|------|
| `hook_constants.py` | 离线校准常数（默认预校准值） | ✅ |
| `prefix_guard.py` | PrefixGuardResult, PrefixGuardSignals, compute_prefix_guard_signals, run_prefix_guard | ✅ |
| `rpe_prefilter.py` | RPEPrefilterResult, RPESignals, ReplayRPEScore, compute_rpe_signals, run_rpe_prefilter | ✅ |
| `replay_ordering.py` | compute_per_replay_ordering（gold-dependent，离线研究用） | ✅ |
| `post_retrieve_hook.py` | PreCmdDecision, post_retrieve_hook（纯 orchestration/decision layer） | ✅ |
| `harness.py` | run_case_v1_with_hook（在线路径）, run_case_v1_with_prefilter（离线路径，rewired） | ✅ |
| `models.py` | RetrievedItem（轻量在线 contract） | ✅ |
| `__init__.py` | 155 公共导出 | ✅ |
| `tests/test_cmd_audit_issue18_pre_cmd_hook.py` | 88 tests, 10 classes | ✅ |
| `scripts/calibrate_hook.py` | 六步离线校准骨架 | ✅ |
| `tests/test_cmd_audit_issue16_rpe_prefilter.py` | 重写为 zero-gold API | ✅ |

88 tests, 10 classes. 760 total (0 regressions). 设计文档: `issues/0018-pre-cmd-hook-design.md`.

## 核心问题

LLM Agent 使用长期记忆后，失败时通常只能看到最终答案错了，但不知道失败来自哪个 memory operation。

CMD 要回答：当 memory-augmented agent 失败时，能否通过 counterfactual replay 判断失败来自写入、压缩、抽取、路由、检索、注入、图关联、安全过滤，还是最终推理？

CMD 完整闭环：**诊断 → 修正 → 经验记忆沉淀**。识别哪条记忆/哪次检索/哪段推理造成偏差，将"错误—原因—修正方法"沉淀为可复用 Failure Memory。

## 核心假设与方法

对失败样例运行 counterfactual interventions，用 Recovery Gain 归因：
- Δk = Metric(ŷ_k, y) - Metric(ŷ, y)
- c* = argmax_k Δk
- top-2 或 multi-label attribution when gains are close

## 失败类型

两层错误：Memory item（内容错误/过期/冲突/污染/失真）vs Memory pipeline（操作环节失败）。

V0 labels: `write_error`, `compression_error`, `premature_extraction_error`, `retrieval_error`, `injection_error`, `reasoning_error`。
V1 active (issues 0011-0012 done): `ingestion_error`, `route_error`, `granularity_error`, `graph_error`, `safety_error`（共 11 个 pipeline labels）。
V2 deferred: `item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`。

## Counterfactual Replays (V0-V1)

Oracle Write, Oracle Compression, Oracle Retrieval, Verbatim Event Oracle, Injection-Oracle, Evidence-Given Reasoning, Oracle Route (V1), Oracle Granularity (V1), Oracle Graph (V1), Oracle Safety (V1)。

V1 耦合失败重新校准（issue 0013）：`top_k` 参数（默认 2，可配置至 3+），`close_deltas` 透明暴露所有 tie_margin 内的 (label, delta) 对。Memory-Probe 3x2 网格基线（3 write strategies × 2 retrieval methods: cosine + BM25; dense retrieval 推迟到 V1 adapter 层 per issue 0008）作为聚合诊断对比器。

mem0 adapter（issue 0014）：`Mem0Adapter` 在 `add()` 和 `search()` 两个切点拦截 mem0 操作，支持 6 个 V0 counterfactual replay 通过 adapter 路径运行。使用录制轨迹模式（V1），沙箱校验和验证（永不修改原始存储），adapter-label 在 V0 smoke suite 上与 standalone harness 完全一致。`cmd_audit/adapters/` 包为 V2 live 集成预留了扩展点。

Letta adapter（issue 0015）：`LettaAdapter` 三切点拦截（`intercept_core_write`/`intercept_archival_store`/`intercept_recall`），tripartite memory model（core + archival + recall），录制轨迹模式，沙箱校验和验证，10-replay adapter portfolio，adapter-label parity on V0 smoke suite，cross-agent non-regression（mem0 + Letta 独立），V1→V2 gate passes。

V1 hook modules: issue 0021 PR2 removed the legacy `cmd_audit/rpe_prefilter.py`, `cmd_audit/prefix_guard.py`, root `cmd_audit/post_retrieve_hook.py`, `cmd_audit/replay_ordering.py`, and `cmd_audit/hook_constants.py`. Current implementation lives in `cmd_audit/hook/` with two-stage `empty_ctx` + RPE Judge top-k gating.

## Issue 0016: Real Data Integration (2026-05-22)

**数据文件** (on disk):
- `data/probe_cases/real_longmemeval_cases.json` — 200 cases (V0 labels)
- `data/probe_cases/real_memoryarena_cases.json` — 198 cases (V0 labels)
- `data/probe_cases/real_toolbench_cases.json` — 198 cases (V1 labels)
- `data/probe_cases/v1_null_label_cases.json` — 5 null-label cases
- `data/cleaned_cases/cleaned_cases.json` — 596 raw haystack-format cases
- `data/cleaned_cases/context_cases.json` — 596 context cases (4-Mode)

**新增 API:**
- `load_all_real_cases(base_dir=None) -> list[ProbeCase]` — 统一加载全部 601 cases
- `load_real_cases_by_source(base_dir=None) -> dict[str, list[ProbeCase]]` — 按 source 分组加载
- `run_full_real_suite(out_dir, use_hook=True) -> list[AuditResult]` — 一键运行全量套件并产出 artifacts
- CLI: `python -m cmd_audit run-v1 --real-data [--no-hook] [--out-dir ...]` (`--no-prefilter` remains a deprecated alias)

**Null-label handling**: `perturbation_label: str | None`, `attribution_correct` returns `None`, excluded from macro F1, included in qualitative output. Memory-probe 3×2 grid runs on mixed suite. 38 tests (1993 subtests).

**标注分布** (601 cases): retrieval_error=132, injection_error=111, reasoning_error=83, premature_extraction_error=83, compression_error=54, route_error=50, ingestion_error=50, write_error=33, null=5。

## 数据与实验

主数据: LoCoMo, LongMemEval, HotpotQA-memory variant。
核心实验: (1) CMD 恢复注入失败标签; (2) targeted fixes 优于 hard-case update; (3) Post-Repair Context Replay 验证修复; (4) 跨 memory system 泛化; (5) Failure Memory 降低复发。

成功标准: CMD macro F1 > baselines; top-2 accuracy 支持调试; CMD-guided fixes > undifferentiated update; failure profiles 揭示非显然洞察。

## 关键论文来源

详见 `knowledge/_index.md`（47+ reference notes）。核心来源：Agent Memory Survey (2603.07670), D-MEM (2603.14597), Memory-Probe (2603.02473), ErrorProbe (2604.17658), MEMAUDIT (2605.02199), PrefixGuard (2605.06455), MemORAI (2605.01386), Skill as Memory (Sarkar).

## 关键设计压缩 (V0 invariants)

- V0 label set = 6 pipeline labels; item labels + deferred pipeline labels excluded.
- Subagent Judge Monitor leak-safe: enum-locked `anomaly_reason`, opaque evidence IDs, no labels/ECS/writes/gold/traces.
- CMD-Audit writes limited to replay-local sandbox; CMD-Skill Adapter applies validated repairs.
- Verbatim Event Oracle boundary: raw events contain evidence, extracted memory cannot → `premature_extraction_error`. Hard boundary: `evidence_recall_from_text(gold_evidence, memory_item.text)`.
- ECS `cause` may describe item state but must not use V0-forbidden item label names.
- Post-Repair Context Replay: 3-value `repair_assessment` (recovered/partial/failed). Not binary gate.
- Failure Memory: corrected-only mode (`corrected_memory + repair_guidance`). Contrastive mode is V2.
- Version gates: V0→V1 (4 evidence artifacts), V1→V2 (≥2 agents, no macro F1 regression).
- CMD does not claim invention of error→fix→reuse loop (4-source convergence). Claims: automated counterfactual attribution + automated semantic quality gate.

## Metabolism 增量历史

### 2026-05-09 (pre-Day 0)

7 papers: STALE, MEMAUDIT, BeliefMem, TreeMem, MemReranker, SafeHarbor/Agent Worms, Cross-Component Interference. Key outcome: V0 invariants hardened (monitor leak-safety, V0 label exclusion, Post-Repair boundary, 12 specific invariants documented).

### 2026-05-10 (Day 0 — baseline)

27 papers + 10 repos. Key outcomes: CMD's unique positioning confirmed (no existing counterfactual attribution framework). Failure Memory pattern independently validated by A-MemGuard + SQLFixAgent + Reflection-Driven Control. Competitive landscape table established. RPE pre-filter identified (hyp-011).

### 2026-05-10 (Issue 0008)

Design decisions: (A) BM25 only (1 retriever; HybridRerank removed — sparse+sparse hybrid cannot deliver semantic recovery; strong retrieval deferred to V1 with mem0/Letta adapters). (B) `evidence_recall_from_text` phrase matching is necessary but not sufficient — semantic upgrade belongs to V1. (C) Agentic search deferred to V1.

### 2026-05-11 (Day 1)

9 papers + 1 repo (MemFlow, ErrorProbe, MemEvoBench, Memora, Dual-Stream Reconciliation, SCG-MEM, MemoScope). Key outcomes: "Verified Episodic Memory" convergence (CMD + ErrorProbe + A-MemGuard → hyp-012). `premature_extraction_error` validated by 13.6% error cascade measurement. `route_error` validated by MemFlow Router.

V1 planning: 5 pipeline labels in priority order; mem0 as first adapter target (55k stars); Letta as second; single-paper scope (V0+V1+V2). Memory-Probe (2603.02473) as closest adjacent work.

### 2026-05-12 (Day 2)

4 papers (PrefixGuard, MAGE, MemORAI, Trojan Hippo). Key outcomes: PrefixGuard-CMD complementary architecture (hyp-013). MAGE + Trojan Hippo define agent memory security frontier. MemORAI as V1 SOTA baseline.

Decisions: (17) Context construction — V0/V1 corrected-only, contrastive mode is V2. (18) Dataset build order — Experiment 2 → Experiment 1. 4-Mode Context Experiment pre-V2 validation available.

Two-experiment dataset design finalized. No existing 4-mode comparison in 40+ papers.

### 2026-05-13 (Day 3)

3 papers + 4 repos (Intent Gap, Skill as Memory, Agent Skills Survey, skill-everything, MemoryOS, memory-poisoning-demo, portable-agent-memory). Key outcomes: "Never Make the Same Mistake Twice" 4-source convergence (CMD + skill-everything + ErrorProbe + SQLFixAgent). hyp-014: Post-Repair Context Replay as only fully automated semantic quality gate.

Decision 19: CMD's paper claims focus on (1) automated counterfactual attribution, (2) Post-Repair Context Replay as automated quality gate.

CMD vs ErrorProbe: observational backward trace vs counterfactual replay; multi-agent steps vs memory pipeline operations.
CMD vs skill-everything: shared loop, opposite automation ends; complementary (CMD auto-diagnoses, could feed PRs into skill-everything catalog).

### 2026-05-14 (Day 4)

18 papers (2605.12978 Memory Becomes Faulty, 2605.07242 MEMOREPAIR, 2605.10870 Decision-Centric Memory, 2605.09330 Spurious Correlations, 2605.07313 Scale-Conditioned Eval, 2605.13438 Cognifold, 2605.12493 LongMemEval-V2, 2605.09863 Nautilus Compass, 2605.09033 ShadowMerge, 2605.06716 Storage→Experience Survey, 2605.12061 SAGE, 2605.09942 HAGE, +6 lower-priority). Key outcomes:

**Consolidation fragility confirmed**: 2605.12978 provides controlled causal evidence that consolidation systematically degrades memory — GPT-5.4 fails on 54% of problems it solved without memory. Three failure modes (misclassification, interference, overfit). Episodic traces as first-class evidence.

**Cascade repair formalized**: 2605.07242 MEMOREPAIR introduces barrier-first cascade repair with s-t min-cut optimization. 0% invalidated-memory exposure with complete influence provenance. CMD attribution + MEMOREPAIR cascade handling is a natural integration.

**Detection→Diagnosis→Repair gap widening**: 5 detection-only systems (Nautilus Compass, CAMEL, Cognifold, PrefixGuard, scale-conditioned eval) + 1 repair-only system (MEMOREPAIR) — none span detection+attribution+repair. CMD remains uniquely positioned across all three.

**Episodic trace anchoring consensus**: 2605.12978 (episodic traces as first-class evidence) + 2605.10870 (memory must preserve decision-relevant distinctions) + MEMOREPAIR (retained support) independently converge on raw episodic traces as ground truth.

**Graph memory frontier**: SAGE + HAGE + Cognifold (capability) vs ShadowMerge (vulnerability, 93.8% ASR). Both promise and attack surface growing.

hyp-015: Episodic-Trace Anchored Cascade Repair — CMD's counterfactual replay over episodic traces can serve as both attribution engine (which operation degraded evidence) and cascade repair validation gate (do repaired descendants preserve decision-relevant distinctions).

### 2026-05-15 (Day 5)

24 papers (2605.14865 HolisticEval, 2605.14892 LIFE Survey, 2605.13077 Counterfactual Responsibility, 2605.06788 ConformalAttr, 2605.07509 MASPrism, 2605.14421 MemLineage, 2605.13941 EvolveMem, 2605.15109 Traversal Context, 2605.11039 PACT, 2605.03482 MEMSAD, 2605.08717 PROBE, 2605.08715 AgentForesight, 2605.08374 MemQ, 2605.08060 Memory Curse, 2605.09934 TRACER, 2605.06365 Execution Lineage, 2605.08468 PYTHALAB-MERA, 2605.11882 FATE, 2605.12260 PRISM, 2605.14498 GroupMemBench, 2605.15128 MemEye, +3 lower-priority). Key outcomes:

**Failure attribution subfield emergence**: 5+ distinct attribution methods published this week — span-level (14865), Shapley-value counterfactual (13077), conformal (06788), signal-based (07509), taxonomy survey (14892). CMD's differentiators hold: operation-level granularity, counterfactual intervention evidence, automated quality gate, full detect→diagnose→repair→store loop. But the space is crowding — must articulate why Recovery Gain produces more actionable diagnoses than lighter alternatives.

**2605.13077 Counterfactual Responsibility Attribution**: Closest formal work to CMD. Uses Shapley value for multi-agent responsibility allocation. Key difference: Shapley (coalitional, all-agent marginal contributions) vs CMD's Recovery Gain (single-operation intervention, pipeline-specific). Shapley could complement CMD for multi-agent extension. Validates counterfactual as the right foundation.

**Provenance infrastructure convergence**: 5 provenance papers (MemLineage, TRACER, PACT, Execution Lineage, MemQ). Provenance tracking is becoming fundamental infrastructure for memory trust/security/reproducibility. CMD lacks provenance; integrating lineage DAGs could fill the MEMOREPAIR cascade repair gap.

**Diagnosis-driven self-evolution**: EvolveMem (diagnosis→config adjustment) + MemQ (credit propagation→Q-updates) + FATE (failure→repair supervision) + CMD (detect→diagnose→store→reuse) = four independent systems. CMD's differentiation: operation-level granularity + automated semantic quality gate (others lack repair validation).

**Memory externalities**: Memory Curse (2605.08060): expanded recall degrades cooperation in 18/28 settings. MEMSAD (2605.03482): gradient coupling theorem for anomaly detection. CMD's repair should consider multi-dimensional impact.

**Competitive risk**: Attribution is the hottest subtopic in agent memory (5 papers this week alone). CMD's window for claiming counterfactual operation-level attribution as novel is narrowing. Paper must ship with clear differentiation from 13077 (Shapley vs Recovery Gain) and 14865 (span-level vs operation-level).

### 2026-05-18 (Day 6)

Low-volume weekend window (05-13~05-18). 1 key paper + 2 peripherally relevant. arxiv API down; discovery via OpenAlex + page probing.

**LOBSTER-Bench (Zenodo, 2026-05-16)**: First benchmark for long-lived agent observability. 6 dimensions (temporal persistence, cognitive telemetry, relational observability, collective task assay, cognitive load management, governance & auditability). Real data from 21-agent system over 7 days — 27,788 telemetry rows, emergent cascade of "two correct subsystems → ten degraded agents." Directly validates CMD's premise: persistent agents need memory observability. Governance dimension = CMD's Post-Repair Context Replay as audit trail.

**2605.15000 Premature Closure**: LLMs commit to conclusions without sufficient information (55-81% false-action rate). Maps to `reasoning_error`. CMD's Evidence-Given Reasoning replay can detect premature closure.

**2605.15400 IBTS**: Multi-agent team steering with influence shaping. Some relevance to multi-agent CMD extension.

No new hypotheses generated (weekend low volume). Knowledge updates are validation-focused.

### 2026-05-19 (Day 7+8)

High-volume post-weekend window. 4 papers + 5 repos. Key theme: **counterfactual convergence across granularity levels**.

**VerifyMAS (2605.17467)**: Hypothesis verification for failure attribution in LLM multi-agent systems. Agent-level attribution via error-first verification against full trajectories, validated on Aegis-Bench and Who&When. Directly competitive with CMD in "automated failure attribution" space — but at agent-level granularity (which agent) vs CMD's operation-level (which memory operation). Verification-based (observational) vs CMD's replay-based (causal). Both independently converge on structured error taxonomies.

**MemRepair (2605.17444)**: Three-layer hierarchical memory (History-Fix, Security-Pattern, Refinement-Trajectory) for code vulnerability repair. Failure-to-success trajectory reuse validates CMD's ECS store-and-retrieve paradigm. SOTA on SEC-Bench, PatchEval, Multi-SWE-bench. Architectural reference for multi-layer CMD Failure Memory.

**SE-GA (2605.16883, ICML 2026)**: Hierarchical episodic/semantic/experiential memory + self-evolution for GUI agents. ScreenSpot 89.0%, AndroidControl-High 75.8%. Memory architecture reference for CMD V2 skill evolution.

**DiagEval (2605.17439)**: Trajectory-conditioned diagnosis reuses failure trajectories with targeted diagnostic probes. Methodological parallel to CMD's counterfactual replay: both are "failure trajectory → targeted probes → attribution." Different domain (GUI eval vs memory ops).

**TraceAudit (github, AAAI 2027 target)**: ⚠️ MOST DIRECT COUNTERFACTUAL COMPETITOR. Chunk-level counterfactual auditing of agentic RAG. Three intervention modes (ablation/rollout/truncation), three operators (remove/paraphrase/distract). URR metric. $2000 budget. Pre-registered hypotheses. Multi-phase plan through August 2026. Key differences: chunk-level (not operation-level), audit-oriented (not diagnosis+repair), external RAG (not own memory pipeline). Validation that counterfactual is converging as the right foundation.

**trace-mem (github)**: Verifiable memory with counterfactual admission gate ("admit only if probe accuracy improves"). Preventive counterfactual gating at ingestion complements CMD's retrospective counterfactual replay at diagnosis. Full prevention→diagnosis spectrum coverage possible.

**RecMem (github, ACL 2026 Findings)**: Recurrence-based consolidation — defers LLM extraction until semantic recurrence indicates value. Directly validates `compression_error` (eager consolidation waste) and `premature_extraction_error` (single-facet information loss).

**DebugMind (github)**: Experiential memory for code bug diagnosis — engineering analogue of CMD Failure Memory. Less structured, no automated attribution.

**hyp-017**: Multi-Resolution Counterfactual Attribution — the attribution subfield is self-organizing into a granularity spectrum: chunk (TraceAudit) → agent (VerifyMAS) → operation (CMD). Counterfactual intervention is the common methodological thread across independently developed systems. CMD occupies the operation-level niche, which no other system targets.

**Key competitive update**: Two new counterfactual attribution systems found in one day (TraceAudit + VerifyMAS). The space is crowding faster than anticipated. CMD's paper window is narrowing. Must ship with clear differentiation along: (1) granularity level (operation is unique), (2) evidence type (causal replay vs observational verification vs chunk ablation), (3) full loop (diagnosis→repair→validate→store — none of the competitors do all four).

## 已知限制（详见 cmd_innovation_core/plans/limitations.md）

### 金标证据依赖（Gold Evidence Dependency）

4/11 标签（`write_error`, `compression_error`, `premature_extraction_error`, `injection_error`）需要 gold_evidence 才能构造反事实记忆项。这是信息论边界（检测缺失的内容需要知道应该存在什么），不是方法论缺陷。

防御策略：
- **CMD-Audit（离线）**：全量 11/11 标签，金标证据完整。证明反事实方法有效——**科研贡献**。
- **CMD-Skill Adapter（在线）**：7/11 基础 + 自监督增强至 ~9/11。仍优于现状（零自动化归因）——**部署贡献**。
- **自监督路径**：成功 trace 的记忆项分布 → 代理证据候选 → 反事实回放因果验证。将"证据在哪"转化为"哪些成功 trace 项因果改善答案"。

其余 7 项限制涵盖：确定性评分、合成扰动、单 Agent 范围、操作级粒度、短语匹配证据召回、评估范围、闭世界标签体系。

### 论文定位更新

CMD 声明不得暗示金标证据在线可用。声明应聚焦：
1. 反事实回放在有金标证据时产生有效的操作级归因
2. 不需要金标证据的标签子集在在线场景仍有效
3. 自监督缩小但不消除差距

对比基准是 CMD（在线，降级标签集）vs 现状（零自动化归因），而非有金标 vs 无金标的 CMD。

## 下一步（V1.0 06-10 + V1.1 post-corpus，Decision 34 R10）

1. **issue 0033 — deepseek labeling provenance recovery** — provenance recovered 2026-05-25：重构 `scripts/annotate_perturbation_labels.py`，在 `data/cleaned_cases/cleaning_report.txt` 和 researcher subset annotator metadata 中引用；完整 596-case DeepSeek API rerun 仍需 credentials 后替换 `artifacts/deepseek_label_reproducibility.txt`。
2. **issue 0022 — LLM eval infrastructure wiring** — target 05-25~28。接通 `agent_generate`、独立 scorer、Post-Repair `AnswerVerifier`、label-strip、on-the-fly baseline rescore、headline hook bypass。代码编辑严格按 `REPAIR.md` §11。
3. **issue 0032 — Test suite migration** — target 05-25~28。conftest warning filter、label-leak invariant rewrite、adapter parity under LLM stack。
4. **issue 0031 — Artifact archive + manifests** — target 05-25。pre-D34 artifacts → `legacy_phrase_match_2026_05_22/`。
5. **issue 0023 — At-scale LLM re-test V1.0** — target 05-28~30。596 × 10 replays，qwen2.5-7b agent + independent evaluator，`tie_margin=0.0`。
6. **issue 0028 — Hook calibration V1.0** — target 05-30。从 re-test output 训练 hook，不另跑 SubagentScorer pass。
7. **issue 0024 — Researcher 130-case adjudication V1.0** — target 05-30~01。LLM-A (`llama-3.3-70b-instruct`) + 20-case blind spot-check，~5 hr。
8. **issue 0025 — Researcher 80-ECS inspection V1.0** — target 06-01~03。Experiment 1 输入 ECS 手动检视/修正。
9. **issue 0026 — Experiment 2 V1.0 headline** — target 06-03。130 adjudicated cases，no hook，all 10 replays，bootstrap CI + cost/latency。
10. **issue 0036 — Surrogate-gap LLM rerun** — target 06-04。50-case hold-out，retention%。
11. **issue 0029 — Coupled-failure subset report** — target 06-04。30-50 near-tie cases，manual inspect，post-hoc tie_margin calibration。
12. **issue 0027 — Experiment 1 V1.0 hardened** — target 06-06。80 cases × 5 modes，含 `corrected_only_padded` token-control 和 3-trial `none` pre-check。
13. **issue 0030 — Layered positioning** — target 06-07。Runtime / Memory pipeline / Item content 分层定位，CMD vs Rewind head-to-head dropped。
14. **V1.0 arxiv preprint** — target 2026-06-08~10。所有数字声明绑定到 headline/sanity/supplementary artifact；cross-dataset claim = coverage only。
15. **issue 0035 — Corpus migration cutover** — post-V1.0。触发 0023/0024/0026/0027/0028/0029/0031/0036 的 V1.1 重跑。
16. **V1.1 venue submission** — post-0035。full-corpus N 支撑 explicit generalization claim。

### 2026-05-20 (Day 9)

High-volume day. 3 papers + 5 repos + 1 hypothesis. Key theme: **counterfactual replay exploding as engineering tooling**.

**STAR (2605.15581)**: Stage-attributed counterfactual repair for RCA agents. Architectural convergence with CMD's decompose→attribute→patch→replay→store loop. Different domain (RCA) and granularity (4 stages vs 11 operations).

**Counterfactual tooling explosion**: 3 independent open-source tools implementing record→replay→fork→counterfactual within a 2-month window: Culpa (deterministic replay + forking), Rewind (`rewind fix` — one command from broken to proven fix), TraceForge (counterfactual attribution with sensitivity scoring).

**CausalOS**: Causal memory layer. Action-outcome chains → CausalGuard blocks risky actions. Engineering implementation of CMD's Failure Memory.

**AgentRx (Microsoft)**: Constraint-based agent failure diagnosis. 10-category taxonomy, invariant-based. Validates problem space from Microsoft.

**Memory Worth (2604.12007)**: Two-counter memory governance signal. Two-tier architecture: MW monitors (free), CMD replays on MW-triggered anomalies (precise).

**MemMA (2603.18718)**: In-situ self-evolving memory. Probe→verify→repair before commit.

**MemReader (2604.07877)**: Active extraction paradigm. Validates `write_error`/`compression_error`.

**hyp-018**: Counterfactual Replay as Standard Agent Debugging Primitive. Simultaneous emergence from tooling (bottom-up) and academic (top-down) signals "record→replay→fork→measure" is the `git bisect` of the agent era. CMD's remaining differentiators: (1) memory-pipeline-specific operation granularity, (2) automated ECS repair, (3) Failure Memory for recurrence prevention.

**⚠️ Competitive update**: Counterfactual replay is becoming table stakes. 3 tooling + 3 academic systems converging on the same primitive. Paper must emphasize memory-pipeline-specific granularity and automated repair loop.

### 2026-05-20 (Day 10 — Decision 30)

Decision 30 resolved with three-point user directive:

1. **加速时间线**: Paper deadline from 2026-06-15 → **2026-06-10**. Counterfactual replay commoditizing from both tooling and academic directions.
2. **记忆模块诊断层定位**: CMD 不是与通用 replay 工具竞争——它们在基础设施层互补。CMD 位于 replay 后端之上，提供 memory-specific 智能。
3. **深度超越 Rewind**: Rewind 诊断粒度在 agent-step（"步骤5用了过期数据"）；CMD 在 memory-operation（"`compression_error`: session 3→4 摘要过程中丢失了短语 'Messi GOAT'"）。

**CMD vs Rewind 五维差异**:

| 维度 | Rewind (agent-step) | CMD (memory-operation) |
|------|--------------------|------------------------|
| 粒度 | "Step 5 used stale data" | "`compression_error`: evidence lost during summarization" |
| 诊断 | LLM guesses failed step | Counterfactual replay: Recovery Gain over 11 ops |
| 修复 | Retry step with different model/prompt | Rewrite specific memory item to restore lost evidence |
| 验证 | LLM-as-judge score comparison | Post-Repair Context Replay: rerun original query |
| 学习 | One-shot fix, no retention | ECS → Failure Memory → recurrence prevention |

**修复深度指标** (proposed):
- Level 0 (Symptom): Retry final step (Rewind)
- Level 1 (Pipeline): Fix specific memory operation (CMD V1)
- Level 2 (Item): Fix specific memory item content (CMD V1, targeted repair)
- Level 3 (Cascade): Fix item + all downstream affected (CMD V2, MEMOREPAIR)

CMD V1 → Level 1-2. Rewind → Level 0.

**Original paper requirements (superseded by Decision 34)**:
- At the time: CMD operation-level repair vs Rewind-style step-level retry on same failure cases
- Hypothesis at the time: memory-specific repair produces higher recovery rates + lower recurrence
- Integration stance: standalone harness, acknowledge Culpa/Rewind as complementary infra, cite convergence as validation

**Superseded 2026-05-23 by Decision 34 R6**: head-to-head benchmark dropped; keep layered positioning and qualitative boundary examples only.

### 2026-05-20 (Day 10 — Subagent scoring design)

**Context isolation + binary atomic judgment replacing phrase-matching.** Full design spec: `cmd_innovation_core/issues/0019-subagent-scoring-context-isolation.md`.

**核心发现**:

1. **CMD 的 oracle replay 当前是短路评估** (`replays.py:189`): `_score_recovered_evidence` 在 `evidence_score == 1.0` 时返回 `case.gold_answer` 作为答案，而不是让 Agent 用修复后的 context 重新运行。正确路径应为：CMD 改变 context → Agent 重跑 → Subagent 评估。

2. **gold_evidence ≠ gold_answer**: Subagent 评分只需要 gold_evidence（事实命题），不需要 gold_answer（特定措辞）。证据评分因此摆脱了对 gold_answer 的依赖：`subagent(FACT=ev, TEXT=answer) → PRESENT/ABSENT`，聚合后 `evidence_score = ΣPRESENT/n`。

3. **二元原子判定 + 聚合层产生连续信号**: 每个 subagent 只看一个 (FACT, TEXT) 对，输出单一二元判定；连续值 `[0,1]` 在聚合层自然产生。这消除了 "部分匹配" 的边界模糊问题。

4. **四种上下文约束**: 原子性（一次一个判定）、自包含（判定所需全部信息，不多不少）、审计可追溯（来源可验）、无泄漏（无 gold_label/cross-case 数据/full traces）。

**两个 LLM 角色**:
| 角色 | 用途 | 上下文 |
|------|------|--------|
| LLM-as-Judge baseline | 第3个对照基线（observational post-hoc 是否已解决归因） | 全量 trace |
| Subagent-based scoring | 替代 phrase-matching 评分 | 每个 subagent 仅含一个 (FACT, TEXT) 对 |

**与文献的差异**: 无 surveyed paper 同时组合"subagent 上下文隔离 + 原子二元判定 + skill/hook 审计边界"。最接近的 G-Eval 使用 Likert rubric + chain-of-thought（高方差），Rewind 使用全量 trace + 4 维 LLM-as-judge（无上下文隔离）。

**Subagent 上下文模板**:

EvidenceVerifier:
```
TASK: 验证事实是否存在于给定文本
FACT: <gold_evidence.text>
TEXT: <replay 产出的 evidence_block>
STANDARD: 核心命题内容出现 = 存在。允许改写/缩写/嵌入。禁止矛盾/相关但不同/缺失。
OUTPUT: PRESENT | ABSENT
```

AnswerVerifier (仅 Post-Repair 验证，不参与归因循环):
```
TASK: 验证两个答案是否语义等价
ANSWER: <Agent 修复后输出>
GOLD ANSWER: <ground truth>
STANDARD: 相同事实信息 = 等价。允许措辞差异/不矛盾额外细节/不同顺序。禁止矛盾/遗漏/矛盾信息。
OUTPUT: EQUIVALENT | NOT_EQUIVALENT
```

**实现分为三个阶段**:
- Phase A: `llm_client.py` + `llm_judge.py`（LLM-as-Judge 基线，第3个对照，qwen2.5-7b 本地）；关键测试：如果 LLM-as-Judge ≈ CMD 准确率 → counterfactual replay 不必要（证伪测试）
- Phase B: `subagent_runner.py` + `llm_scoring.py` + `hooks.py`（SubagentScorer，EvidenceVerifier 活跃替代 phrase-matching，AnswerVerifier 已实现待 Decision B 接入）
- Phase C: 方差研究（100 cases, 3 runs, Group A 开放式 vs Group B subagent+隔离，测量标准差降低）

**文件创建顺序（依赖优先）**: `llm_client.py` → `llm_judge.py` → `subagent_runner.py` → `llm_scoring.py` + `hooks.py`。

**模型选择**: qwen2.5-7b 为主力（ollama 本地，Apache 2.0，可复现），gpt-4o-mini 为校准上限。所有调用 temperature=0。

### 2026-05-23/24 (Day 13/14 — Decision 34 paper claim integrity grilling)

`grill-with-docs` session "discussion 3" with the user produced 11 binding resolutions (R1-R11) bundled as Decision 34 in `cmd_open_decisions.md`. Full paste-ready edit plan: `REPAIR.md` at repo root. 13 issue files written: `issues/0022-0034`.

**Headline finding**: 596-case Macro F1 = 1.000 reported in `V0V1_gate_status.md` 2026-05-22 is mechanical, not paper-grade. Three reasons:
1. `replays.py:477` shortcut means `recovery_gain ∈ {0.0, 1.0}` — perfect identity matrix tests replay-portfolio wiring, not diagnostic power.
2. `perturbation_label`s for 596 cases assigned by deepseek-v4-pro-max → CMD-vs-LLM-annotator agreement, not ground-truth.
3. `tie_margin = 0.05` is a magic number from binary-scoring era; under continuous LLM scoring it has no calibration.

**Resolutions (R1-R11, all approved by user 2026-05-23/24)**:

- **R1 At-scale LLM re-test**: 596 cases × 10 replays under qwen2.5-7b agent + independent LLM evaluator (≠ qwen, ≠ llama-A, ≠ deepseek). On-the-fly LLM rescore of `vector_memory` baseline. Drop `CMD ATTRIBUTION LABEL` line from `_build_replay_agent_context`.
- **R2 Post-Repair must run agent**: Wire `agent_generate` + `AnswerVerifier == EQUIVALENT` for `recovered`. τ=0.5 default partial threshold.
- **R3 Headline tie_margin = 0.0**: Zero-free-parameter argmax. Coupled-failure analysis = separate post-hoc subset report (issue 0029).
- **R4 Researcher-adjudicated 130-case headline + LLM-A assist**: ~16 × 8 active labels stratified, `random_state=42`. LLM-A (`llama-3.3-70b-instruct`) candidate suggestion + accept/reject + 20-case blind spot-check. The 596-case suite reframes as "CMD reproduces deepseek-v4-pro-max labels at Macro F1 = Y" scale sanity check. Cohen's κ vs deepseek as methods artifact. **R4-prov**: deepseek labeling prompt + script must be checked into `scripts/annotate_perturbation_labels.py`.
- **R5 Hook → supplementary**: Decision 33's two-stage hook stays implemented. Experiment 2 bypasses (`run_case_full_v1`, all 10 replays). Hook calibration runs *after* re-test, consumes re-test outputs as free training labels (no separate qwen pass). Hook efficacy = one supplementary table.
- **R6 Drop CMD vs Rewind head-to-head**: Different layers, different input modalities. Replaced with layered-stack positioning + reframed 5-dim table.
- **R7 Experiment 1 hardened**: 80 cases (20 × 4 labels), 5 modes (add `corrected_only_padded` token-control), 3-trial `none`-mode pre-check (lenient), researcher inspects 80 ECS records.
- **R8 Paper claim binding**: Q11 standalone FM recurrence collapsed into Experiment 1; Q12 repair depth as design claim ("Level 2 capability is design property of RepairAction emission"); Q13 AttributionFailed as principled abstention (two-tier headline coverage% + Macro F1 on attributed) + 8-label headline + 11-label supplementary completeness note.
- **R9 Artifact regeneration**: All 11 types regenerated under LLM stack on 596 (V1.0); per-source split preserved; pre-D34 artifacts archived; recurrence_comparison.csv dropped; semantic-shift annotations in MANIFEST.
- **R10 V1.0/V1.1 dual-release + paper-craft**: V1.0 arxiv 06-10 → V1.1 venue post-issue-0035. Cross-dataset claim version-gated: V1.0 coverage only, V1.1 explicit generalization. ~9/11 deployment claim with retention% backing (issue 0036). Two-evaluator robustness on 130-case headline only. Bootstrap CIs everywhere meaningful. Cost/latency in headline column.
- **R11 LLM-A = `llama-3.3-70b-instruct`**: Different family from deepseek/qwen/evaluator. Open weights, reproducible. Spot-check protocol: 20-case blind labeling first, then assisted; κ(blind, assisted) reports automation bias.

**Sequencing impact**: TASK.md updated (V1.0/V1.1 dual schedule). Critical path V1.0: LLM eval infra → re-test → adjudication → Experiment 2 → arxiv. Critical path V1.1: issue 0035 corpus availability → all V1.0 issues re-run → venue submission. Hook calibration and Rewind benchmark off both critical paths.

**Open dependencies V1.0**: deepseek labeling prompt + script recovery (R4-prov, was off-tree); evaluator model selection (specific gpt-4o-mini class candidate, decision deferred — but family must differ from deepseek/qwen/llama-A).

**Open dependencies V1.1**: issue 0035 (full-corpus build + re-annotation + cleaning_report regeneration).

**Files affected**: `cmd_open_decisions.md` (Decision 34 R1-R11 + Decision 30 addendum), `V0V1_gate_status.md`, `V1V2_gate_status.md`, `TASK.md` (V1.0/V1.1 timeline), `current-memory.md` (this section), `cmd_progress_report.md`, `plans/limitations.md`, `plans/experiment_01_context_construction.md`, `plans/experiment_02_cmd_attribution.md`, `replays.py`, `post_repair.py`, `scripts/calibrate_hook.py`, `scripts/annotate_perturbation_labels.py`, plus 13 new issue files (0022-0034) and supplementary issues (0035 corpus migration, 0036 surrogate-gap rerun) to be drafted.
