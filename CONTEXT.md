# CMD Domain Context

Domain language for Counterfactual Memory Debugger research. This file defines terminology, boundaries, and rules shared across PRDs, issues, prototypes, and tests.

## Core Language

**CMD** — Counterfactual Memory Debugger: diagnoses memory-augmented agent failures by replaying controlled memory-operation interventions and measuring recovery gain.
_Avoid_: generic memory architecture, generic debugger

**Memory-Augmented Agent** — An agent whose answer depends on persistent memory from prior interactions.
_Avoid_: plain LLM, chatbot

**Memory Failure** — A failed task where memory content, the pipeline, or reasoning over memory plausibly causes hallucination, omission, conflict, or misuse.
_Avoid_: generic model error

**Memory Item** — A stored memory unit independently assessable as wrong, stale, conflicting, poisoned, or compression-distorted.
_Avoid_: document chunk, vector row

**Memory Pipeline** — The process that writes, compresses, routes, retrieves, injects, and reasons over memory.
_Avoid_: retrieval stack

**Counterfactual Replay** — Re-running the agent with a controlled memory intervention and measuring recovery gain: Δk = Metric(ŷ_k, y) - Metric(ŷ, y).
_Avoid_: ablation, A/B test

**Recovery Gain** — Δk: the score improvement from a replay intervention over the baseline. Attribution label = argmax(Δk).
_Avoid_: accuracy delta

**Operation-Level Attribution** — Assigning a failure to a specific pipeline operation (write, compress, extract, retrieve, inject, reason) rather than a free-form explanation.
_Avoid_: blame assignment, root-cause analysis

**Error-Cause-Solution (ECS)** — A structured record: what failed, which operation caused it, what corrected memory should replace it, and what repair guidance to apply. Stored as compact Failure Memory.
_Avoid_: debug log, error report

**Failure Memory** — A store of ECS records, retrieved by keyword match on current task, injected as `corrected_memory + repair_guidance`. V0/V1 uses corrected-only mode; contrastive mode (`wrong_memory + cause + corrected_memory + repair_guidance`) is a V2 experiment.
_Avoid_: full trace archive, lesson bank

**Post-Repair Context Replay** — After ECS, rebuild context with the repair and re-run the original query. Outputs three-value `repair_assessment`: `recovered` / `partial` / `failed`. This is CMD's automated quality gate.
_Avoid_: binary pass/fail, regression test

**Subagent Judge Monitor** — A leak-safe trigger: may fire replay but must NOT emit final labels, ECS, memory writes, gold answers, or full failed traces. `anomaly_reason` is enum-locked; evidence pointers are opaque IDs only, never content text.
_Avoid_: LLM-as-judge, free-form explanation

**Subagent Judge Baseline** — A comparator over the failed trace that guesses the label. Tests whether post-hoc trace explanation already solves attribution (if so, counterfactual replay adds no value).
_Avoid_: CMD replacement

**Subagent EvidenceVerifier** — A subagent that receives atomic context {FACT, TEXT, STANDARD} and outputs a single binary judgment PRESENT | ABSENT. One subagent per gold_evidence item. The continuous evidence_score emerges from aggregation: count(PRESENT) / total. Replaces phrase-matching in `_score_recovered_evidence`.
_Avoid_: multi-fact evaluator, rubric generator

**Subagent AnswerVerifier** — A subagent that receives atomic context {ANSWER, GOLD_ANSWER, STANDARD} and outputs EQUIVALENT | NOT_EQUIVALENT. Used only at Post-Repair Context Replay validation step, not in the attribution loop. Removable after repair validation.
_Avoid_: attribution scorer

**Subagent Context Isolation** — Each subagent sees only the information needed for one atomic binary judgment. Four constraints: atomicity (one judgment per call), self-contained (all needed info, nothing more), auditability (traceable sources), leak-free (no gold_label, no cross-case data, no full traces).
_Avoid_: full-trace context, shared context window

**Binary Atomic Judgment + Aggregation** — Subagents make binary decisions (PRESENT/ABSENT, EQUIVALENT/NOT_EQUIVALENT). Continuous scores [0,1] emerge from aggregation (ΣPRESENT/n), not from multi-level rubrics within a single LLM call. This shrinks the choice space from continuous to discrete, eliminating scale drift while preserving intermediate signal.
_Avoid_: Likert scales within subagent, multi-level rubrics at subagent level

**Pre-CMD Hook** — A single `post_retrieve_hook(query, retrieved_items, *, mode="online") -> PreCmdDecision` function that gates CMD counterfactual replay. Two-stage sequential architecture (Decision 33/Issue 0021): Stage 1 — `empty_ctx` hard short-circuit (`len(items)==0 → trigger all 10 replays`); Stage 2 — RPE Judge per-replay p-score → fixed **top-k** (TOP_K=3) selection → fallback (`max(p) < FALLBACK_THRESHOLD → skip CMD`). Online: zero gold, zero LLM (logistic regression inference only). Offline: 3-step calibration on 596 cases (subagent labels for training, global threshold grid search). `mode` flag: `"online"` (default, deployment) skips RPE Judge inference on empty_ctx path with sentinel ReplayScores; `"offline"` (calibration script) runs RPE Judge unconditionally for paper audit data.
_Avoid_: multi-event ECC gate system, gold-dependent online gate, embedding models at prefilter stage, top-p (term reserved for nucleus sampling)

**PrefixGuard 信号处置（Decision 33）** — 原 PrefixGuard 4 信号拆分：`empty_ctx` 作为 Stage 1 唯一硬短路条件；`near_duplicate`、`low_count` 并入 RPE Judge 全局特征；`truncation` 11 pattern 移除（线上检索记忆为纯文本，结构性零召回）。PrefixGuard 独立 stage 已取消。
_Avoid_: PrefixGuard 独立 gate 阶段

**RPE Judge（Decision 33）** — 共享逻辑回归模型 + replay_type one-hot,**16 维特征**(6 全局 + 10 replay_type 指示器)。全局 6 维: `bm25_max`, `bm25_mean`, `bm25_std`, `item_count` (cap+归一 `min(x,10)/10`), `near_duplicate`, `low_count`。已删除 `safety_filter_blocked`/`is_graph_expanded`/`store_count` 三个 metadata 信号(避免 train/serve skew,replay_type one-hot 已表达等价信息)。Label = recovery_gain > 0,**SubagentScorer 计算**(qwen2.5-7b ollama,offline LLM use,deployment hook 仍零 LLM)。推理:sigmoid(dot(weights, features) + intercept) → per-replay p-score,`(-p, V1_REPLAY_NAME_ORDER 索引)` 复合 key 排序破平,取 **top-k** (TOP_K=3)。离线训 16 系数 + intercept,在线仅推理。
_Avoid_: embedding-based semantic surprise, gold-dependent utility, 动态阈值, 19 维特征(已收窄到 16), top-p 命名(已统一 top-k)

**PreCmdDecision（0021）** — `trigger_cmd: bool`, `stage: str` ∈ `{"empty_ctx", "rpe_top_k", "rpe_below_threshold"}`, `per_replay_scores: tuple[ReplayScore, ...]`(永远 10 元),`selected_replays: tuple[str, ...]`(empty_ctx: 全 10;rpe_top_k: top-3 子集;rpe_below_threshold: 空)。**已移除字段**: `reason`, `reason_codes`, `anomaly_score`, `surprise_score`, `utility_score`, `rpe`, `prefix_guard_signals`(Decision 31 旧字段)、`fallback_triggered`(改由 `stage == "rpe_below_threshold"` 推导)。

**ReplayScore（0021）** — `replay_name: str`, `p_score: float ∈ [0,1] ∪ {-1.0}`, `selected: bool`, `is_sentinel: bool = False`。`is_sentinel=True ⇔ p_score=-1.0`,仅在 `mode="online"` 且 `stage="empty_ctx"` 时为真。`offline` mode 下 empty_ctx 路径仍跑 RPE Judge,所有 ReplayScore 为真实值(供 paper hook 有效性分析)。

**CMD Diagnosis Layer** — The heavy counterfactual replay + attribution + ECS + repair pipeline. Only invoked when Pre-CMD Hook triggers. Unchanged from current V1 implementation.
_Avoid_: conflating with gate layer

**Offline Calibration** — 三步(0021):Step 1 — RPE judge 权重训练(596 cases × 10 replays = 5960 对,**train 546 + hold-out 50** 划分,**SubagentScorer 计算 label**(qwen2.5-7b ollama,~5-15 min,cache + 失败 fallback to phrase-match),`class_weight='balanced'` + `random_state=42` 训 LR,持久化 `artifacts/hook_calibration/training_set_subagent.npz`)。Step 2 — Surrogate path 质量测量(50 hold-out 子集,SubagentScorer 对比 surrogate vs gold gain,需 LLM,产出 paper gap,不训练)。Step 3 — **全局阈值校准**(50 hold-out,`TOP_K ∈ {2,3,4,5} × FALLBACK_THRESHOLD ∈ [0,1] step 0.05` = 84 网格点,F2 recall-priority,跨 adapter 共用一组常数)。**Per-agent 阈值推迟到 V2**(V1 数据量不足支持 per-adapter calibration)。
_Avoid_: manual weight tuning, single-threshold guesswork, 六步旧流程, 19 维特征(已 16), per-agent V1 calibration(V2 才做)

**Failure Memory Context (fm_context)** — 诊断信号，由 `wrong_memory + original_evidence` 组成（错误记忆内容 + 为什么是错的证据）。与 `corrected_memory`（修复信号："应该是什么样"）互补。检索：Pre-CMD Hook 阶段通过 `label + query_keywords + memory_top_terms` 复合 key 查 FM 存储。注入时机：ECS 阶段下游（非归因 replay 前，保证因果纯度）。
_Avoid_: 与 corrected_memory 混淆, wrong_memory 原文注入归因阶段, full failed trace

**RepairAction** — A concrete repair operation emitted as strict JSON by `RepairExecutor` from an ECS draft, executed by the adapter: `action_type` in {append, replace, relocate, update_routing, update_template}, plus `target_item_id`, `target_store`, `content`, `label`. Adapter interprets the action against its specific API semantics.
_Avoid_: generic write, adapter-guessing from ECS fields

**RepairExecutor** — Stateless function: `(ECS draft, adapter) -> RepairAction -> adapter.apply_repair() -> re-search -> online validation`. Single repair execution unit. Does not loop.
_Avoid_: orchestrator logic inside executor

**RepairOrchestrator** — Iteration controller: walks `attribution.close_deltas` (top-k labels), calls `RepairExecutor` for each, stops at first `recovered` or exhausts the list. Offline: validates with Post-Repair Context Replay (gold). Online: trusts offline-calibrated attribution, executes repairs without re-validation, records partial outcomes to FM.
_Avoid_: harness-level loop logic

**Self-Supervision Surrogate** — Online mitigation for 4 gold-dependent labels: BM25-retrieves success-trace memory items from the same agent session (sliding window, O(1)), injects as surrogate evidence, validates via counterfactual replay. Candidates with recovery gain > 0 become `corrected_memory`. Transforms "where is the gold?" into "which success-trace items causally improve the answer?" (Decision 29). Target: ~9/11 online label coverage.
_Avoid_: LLM-generated corrected memory, cross-agent surrogate

**Online Post-Repair Validation** — Simplified from offline: offline evaluation with high correctness rates justifies trusting the offline-calibrated attribution. Online apply-repair flow skips validation and trusts the repair; records outcomes for monitoring. No gold-dependent scoring in online path.
_Avoid_: gold answer scoring online, blocking on validation

**Iterative Repair** — When Post-Repair returns `partial` (evidence recovered but answer still wrong), `RepairOrchestrator` iterates to the next `close_deltas` label, drafts a new ECS, and repairs again. Stops at `recovered` or exhausts top-k. Exposes coupled failures (multiple pipeline errors in one case).
_Avoid_: single-pass repair, ignoring partial signals

**Failure Memory Storage** — Only `recovered` ECS records are stored. `partial` and `failed` are discarded. Per-agent persistence: stored as `FAILURE_MEMORY.md` alongside the agent's MEMORY.md, or merged into the original MEMORY with a `failure` label for the agent to learn from.
_Avoid_: storing unrecovered repairs, cross-agent FM contamination

**Cascade Candidates** — V2 pre-burial: `ECSDraft.cascade_candidates` — item IDs downstream of repaired items in the provenance DAG. V1 always empty. V2: LLM retrieves these candidates and self-modifies, rather than algorithmic MemQ TD(lambda) propagation.
_Avoid_: V1 cascade logic, algorithmic cascade repair

**AttributionFailed** — Sentinel state when all replays (7 online + surrogate) produce zero recovery gain. Records feedback for hook threshold recalibration. Does not store to Failure Memory. Adapter may log as observation.
_Avoid_: silently dropping None attribution

**CMD-Audit vs CMD-Skill Adapter** — CMD-Audit is the research harness (standalone, deterministic, synthetic data). CMD-Skill Adapter is the deployment layer (connects to real agent memory APIs). Audit writes are limited to replay-local sandbox; only Skill Adapter applies validated repairs to production state.
_Avoid_: conflating the two

## Label Taxonomy

### V0 (6 pipeline labels)

The foundational label set. V0 pipeline functions (`run_case`, `load_probe_cases`, `validate_v0_label`) reject V1 labels.

| Label | Definition | Key Oracle |
|-------|-----------|------------|
| `write_error` | Evidence never written to memory | Oracle Write |
| `compression_error` | Memory compressed such that evidence lost | Oracle Compression |
| `premature_extraction_error` | Evidence lost during extraction before retrieval; raw events still contain it | Verbatim Event Oracle |
| `retrieval_error` | Correct memory exists but not retrieved | Oracle Retrieval |
| `injection_error` | Memory retrieved but injected with format/order errors | Injection-Oracle |
| `reasoning_error` | Evidence correctly injected but reasoning over it failed | Evidence-Given Reasoning |

### V1 (5 additional pipeline labels, issues 0011-0012)

Extends V0 to 11 active pipeline labels. V1 pipeline functions (`run_case_v1`, `validate_v1_label`) accept all 11 labels. `ingestion_error` is a label split from `write_error` via `has_ingestion_trace` boolean — not a new replay.

| Label | Definition | Key Oracle |
|-------|-----------|------------|
| `ingestion_error` | Evidence never reached the agent; ingestion pipeline failed before `add()` | Oracle Write (`has_ingestion_trace=false`) |
| `route_error` | Correct memory stored in wrong tier/store, baseline retrieval missed it | Oracle Route |
| `granularity_error` | Memory expressed at sub-optimal granularity level, obscuring evidence | Oracle Granularity |
| `graph_error` | Graph expansion introduced distractors that masked correct evidence | Graph-Off |
| `safety_error` | Safety filter blocked valid evidence | Safety-Off |

Bad memory item labels (`item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`) are excluded from V0/V1 attribution and deferred to V2.

## Replay Portfolio

### V0 (6 replays)

Each applies a controlled change to memory/context and re-runs the query. Attribution = argmax(Recovery Gain). Tie-breaking: if max(Δk) ≤ 0 → `None` (unrecoverable). If top-2 Δk gap < 0.05 → output top-2 (coupled failure).

### V1 (4 additional replays, issue 0012)

Total portfolio: 10 replays. V1 replays are intervention-mode (off/bypass/enumerate) rather than evidence-injection mode.

| Replay | Intervention | Diagnoses |
|--------|-------------|-----------|
| `oracle_route` | Enumerate all memory stores/tiers, select best evidence recovery | `route_error` |
| `oracle_granularity` | Re-express memory at each granularity level, select best recovery | `granularity_error` |
| `graph_off` | Disable graph expansion, test direct evidence recovery | `graph_error` |
| `safety_off` | Bypass safety filter, provide blocked evidence directly | `safety_error` |

## Key Boundaries

1. **Leak-safe monitor**: enum-locked `anomaly_reason` only; opaque evidence IDs; no content text, no labels, no gold answers.
2. **Sandbox write boundary**: CMD-Audit writes only to replay-local sandbox; never to production agent memory.
3. **Verbatim Event Oracle boundary**: when raw events contain evidence but extracted memory cannot recover it → `premature_extraction_error`, not `retrieval_error`. Hard boundary: `evidence_recall_from_text(gold_evidence, memory_item.text)` — if Memory Item text lacks evidence phrases, label can never be `retrieval_error`.
4. **ECS cause constraint**: may describe item state in natural language but must not use forbidden item label names.
5. **Context construction mode**: V0/V1 Failure Memory injects only `corrected_memory + repair_guidance`. Contrastive mode is a V2 deliverable. Pre-V2 validation via 4-Mode Context Experiment (none / full_trace / corrected_only / contrastive) using real LLM.
6. **Perturbation type**: Probe Case `perturbation_type` is an injected ground-truth label, never guessed.
7. **Dataset build order**: Probe Cases → CMD pipeline → ECS records → 4-Mode Context Cases. Experiment 2 must precede Experiment 1.
8. **Version gates**: V0→V1 requires four evidence artifacts passing paper-claim thresholds; V1→V2 requires ≥2 distinct memory agents integrated without macro F1 regression.
9. **Quality gate**: Post-Repair Context Replay is CMD's automated quality gate — re-runs the original query with repaired context. Differentiator from human PR review (skill-everything), executable evidence (ErrorProbe), schema validation (Skill as Memory).
10. **Recovery Gain vs Shapley value**: CMD's Recovery Gain is operation-sequential (linear in pipeline length, single-operation Δk). Shapley-value counterfactual responsibility (2605.13077) is coalitional (exponential in agent count, average marginal contribution across all subsets). They are complementary, not competing: CMD targets within-agent pipeline attribution; Shapley targets across-agent responsibility allocation. Multi-agent CMD extension could compose Shapley for inter-agent + Recovery Gain for intra-agent attribution.
11. **Provenance tracking**: Phase 1 (V1) complete — Execution Lineage DAG recording in-edge derivation per MemoryItem across all 10 replays (7 operation types), HMAC tamper detection, `graph_error` distractor identification. Phase 2 (V2): cascade repair via MemQ TD(λ) on provenance DAG, full cryptographic provenance (MemLineage 2605.14421 pattern).

## V1 Architecture

- **Adapter pattern**: CMD-Skill Adapter connects replay harness to real memory agent APIs. Two intercept points per agent: write-side (`add()`) and retrieval-side (`search()`). First target mem0 (55k stars) — done, adapter-label parity verified. Second target Letta (22.6k stars, core/archival/recall tiering) — done, adapter-label parity verified, tripartite memory model, cross-agent non-regression. V1→V2 gate passes with both `mem0_integrated=True` and `letta_integrated=True`. `cmd_audit/adapters/` package with replay recording mode and sandbox checksum validation.
- **Pre-CMD Hook architecture** (Decision 33/Issue 0021, implemented 2026-05-23): Single `post_retrieve_hook(query, items, *, mode="online"|"offline")` function — two-stage sequential: empty_ctx hard short-circuit + RPE Judge per-replay 16-feature logistic regression → fixed top-k (TOP_K=3) selection → fallback skip. Online: zero gold, zero LLM (sigmoid + dot product only). Offline: 3-step calibration (subagent-labeled training on 546 cases + surrogate gap measurement on 50 hold-out + global threshold grid search on 50 hold-out). `hook/` sub-package: `rpe_judge.py` + `post_retrieve_hook.py` + `constants.py`. Legacy PrefixGuard/RPE prefilter modules and `run_case_v1_with_prefilter` were removed in 0021 PR2. CMD Diagnosis Layer (replay→attribution→ECS→repair) only invoked when hook triggers.
- **Repair layering** (Decision 32, 2026-05-22): Four-tier separation — Adapter (声明 `supported_actions`，`apply_repair(RepairAction)` 执行)，RepairExecutor（stateless 单次修复：`ECS → LLM 选 action → apply_repair → 构建 repaired_context(baseline+label+evidence_block+fm_context) → Post-Repair Context Replay → recovered/partial/failed`），RepairOrchestrator（`partial` 时取 close_deltas 下一 label → 新 ECS → 循环至 recovered 或 exhaust），Harness/Online Pipeline（触发 orchestrator；CMD 归因失败 → AttributionFailed，不进入修复）。
- **RepairAction** (Decision 32): 5 种 action_type（`append`, `replace`, `relocate`, `update_routing`, `update_template`），作为 JSON-only LLM tool 输出——LLM 看到 label + evidence_block + fm_context + adapter.supported_actions，自主选择并填参。不硬编码 label→action 映射；启发式只作为无 LLM 离线 fallback。
- **归因 Subagent Loop Context** (Decision 32): 每个 replay 给 `agent.generate()` 的 context = baseline + label + evidence_block。fm_context 在 ECS 阶段注入（非 replay 前，保证因果纯度）。Recovery gain 基于 evidence_score（subagent 连续[0,1]），answer_score 仅离线辅助。
- **Iterative repair** (Decision 32): `partial` Post-Repair → iterate next `close_deltas` label（gain > threshold）→ new ECS → repair again. Stops at `recovered` or exhausts.
- **Self-supervision surrogate** (0021 Step 2, 2026-05-23): 4 gold-dependent labels 在线走 BM25 success-trace → replay → gain。Offline 50 case 子集 SubagentScorer 测量 surrogate vs gold gap，产出 paper gap 数据。不训练，只测量。
- **Failure Memory lifecycle** (Decision 32): 仅 `recovered` ECS 存储。复合检索 key: `label + query_keywords + memory_top_terms`。fm_context = `wrong_memory + original_evidence`（诊断信号），与 `corrected_memory`（修复信号）互补。注入时机：ECS 阶段下游。
- **Provenance tracking** (Decision 28): Execution Lineage DAG per MemoryItem — Phase 1 (V1) complete: in-edge tracking across all 10 replays, HMAC tamper detection, `get_graph_distractor_edges()`, 78 tests. Phase 2 (V2): cascade repair via MemQ TD(λ) on provenance DAG.
- **Coupled-failure handling**: Configurable `top_k` (default 2), transparent `close_deltas` exposing all (label, delta) pairs within tie margin. Memory-Probe 3×2 grid baseline (3 write × 2 retrieval: cosine + BM25; dense retrieval deferred to V1 adapter layer per issue 0008) as aggregate comparator.
- **Comparator baselines** (`baselines/` package): Non-CMD comparison systems separated from CMD's core diagnosis pipeline. Contains `comparators.py` (evidence_recall_heuristic, subagent_judge, random_label, llm_judge, monitor) and `memory_probe.py` (3×2 grid aggregate). CMD-Audit runs baselines alongside its pipeline for paper comparison tables; baselines are not part of CMD's diagnosis logic.
- **Version gates**: V0→V1 requires 4 evidence artifacts passing paper-claim thresholds (PASS on smoke suite, needs scaling). V1→V2 requires ≥2 distinct memory agents integrated without macro F1 regression.
- **Scope**: V0 + V1 + V2 = one paper. ⚠️ Target 2026-06-10 draft (accelerated from 2026-06-15 per Decision 30) — counterfactual tooling explosion (3+ tools + 3+ academic systems) demands urgency. Decision 34 drops CMD vs Rewind head-to-head and replaces it with layered runtime/memory-pipeline/item-content positioning.

## External References (see reference_notes/ for details)

- **2605.13077 Counterfactual Responsibility Attribution (2026-05-13)**: Shapley-value counterfactual reasoning for multi-agent responsibility; closest formal work to CMD; complementary (agent-level vs operation-level).
- **2605.14865 Holistic Eval & Failure Diagnosis (2026-05-14)**: span-level diagnosis with per-span rationales; SOTA on TRAIL; observational (not counterfactual); coarser granularity than CMD.
- **2605.14421 MemLineage (2026-05-14)**: cryptographic provenance + derivation lineage; provenance infrastructure CMD currently lacks; integration candidate for influence tracking.
- **2605.13941 EvolveMem (2026-05-13)**: self-evolving memory with LLM diagnosis module; structurally closest to CMD's diagnosis loop; diagnoses retrieval config (CMD: pipeline operations).
- **PrefixGuard (2605.06455)**: trace→monitor framework with StepView induction + supervised training on terminal outcomes. Validates rule-based monitor over LLM judge. CMD's deterministic-rule PrefixGuard is a simplified reproduction; online Pre-CMD Gate uses self-supervised signals to eliminate gold dependency. Complementary to CMD (detection vs attribution).
- **MAGE (2605.03228)**: shadow memory safety guardrails; validates `safety_error` V1 label.
- **D-MEM (2603.14597)**: dopamine-gated memory with Critic Router self-supervised Surprise/Utility scoring. CMD's RPE prefilter adapts this concept. Current CMD implementation uses gold-dependent simplified reproduction; online Pre-CMD Gate RPE uses embedding-based self-supervised Surprise (1 - cosine_sim(embed(query), embed(memory))) + agent confidence Utility — matching D-MEM's original self-supervised design. Complementary: D-MEM detects surprise but does not attribute to memory operations.
- **MemORAI (2605.01386)**: SOTA graph memory on LOCOMO/LongMemEval; V1 baseline.
- **Skill as Memory (Sarkar, 2026-05-11)**: database-native skill storage; validates CMD's Failure Memory as structured records.
- **LOBSTER-Bench (Zenodo, 2026-05-16)**: first long-lived agent observability benchmark; 6 dimensions including temporal persistence and governance; validates CMD's premise with real 21-agent cascade failure data.
- **2605.15000 Premature Closure (2026-05-16)**: LLMs commit prematurely (55-81% false-action rate); maps to `reasoning_error`. CMD's Evidence-Given Reasoning replay can detect this.
- **TraceAudit (github, AAAI 2027 target)**: chunk-level counterfactual auditing of agentic RAG; most direct counterfactual competitor. Differentiated by granularity (chunk vs operation) and purpose (audit vs diagnosis+repair).
- **VerifyMAS (2605.17467)**: agent-level hypothesis verification for MAS failure attribution. Observational evidence (vs CMD's causal replay). Validates counterfactual convergence at different granularity.
- **MemRepair (2605.17444)**: 3-layer hierarchical repair memory; failure-to-success trajectory reuse validates CMD's ECS paradigm.
- **SE-GA (2605.16883, ICML 2026)**: hierarchical memory + self-evolution for GUI agents; architecture reference for CMD V2.
- **DiagEval (2605.17439)**: trajectory-conditioned diagnosis; methodological parallel to CMD's replay approach.
- **trace-mem (github)**: counterfactual ingestion gate (preventive) complements CMD's retrospective replay; full prevention→diagnosis spectrum coverage possible.
- **RecMem (github, ACL 2026 Findings)**: recurrence-based consolidation; validates `compression_error` and `premature_extraction_error`.
- **STAR (2605.15581)**: stage-attributed counterfactual repair for RCA agents; architectural convergence with CMD's decompose→attribute→patch→replay→store loop. Different domain (microservice RCA), coarser granularity (4 stages).
- **Culpa (github)**: deterministic replay + counterfactual forking for AI agents. Validates counterfactual replay as engineering tooling.
- **Rewind (github)**: time-travel debugger; `rewind fix` — automated diagnosis→fix→verify at agent-step level. Closest engineering analogue to CMD's full loop but at coarser runtime granularity. Decision 30 (2026-05-20) kept five-dimension differentiation; Decision 34 (2026-05-23) drops head-to-head metrics because Rewind and CMD operate on different layers and do not share an input modality. Paper uses layered positioning and qualitative boundary examples instead.
- **CausalOS (github)**: causal memory layer preventing recurrence via action-outcome chains. Engineering implementation of Failure Memory concept.
- **Counterfactual Tooling Explosion (hyp-018)**: 3+ tools (Culpa, Rewind, TraceForge) + 3+ academic systems (STAR, TraceAudit, CMD) converged on "record→replay→fork→measure" within 2 months. Counterfactual replay becoming standard engineering primitive. CMD's remaining differentiators: memory-pipeline-specific granularity, automated ECS repair, Failure Memory.
- **Counterfactual Convergence (hyp-017)**: 3 independent counterfactual attribution systems at different granularities (TraceAudit chunk-level, VerifyMAS agent-level, CMD operation-level) within 2-week window. Attribution subfield self-organizing into granularity spectrum. CMD's paper must differentiate along: (1) operation-level granularity (unique), (2) causal replay evidence (vs observational/ablation), (3) full diagnosis→repair→validate→store loop (vs audit/attribution-only).
- **"Never Make the Same Mistake Twice" convergence**: CMD, skill-everything, ErrorProbe, SQLFixAgent converge on detect→diagnose→store→reuse loop. CMD's differentiator: automated counterfactual attribution + Post-Repair Context Replay as automated semantic quality gate.

## Key Relationships

- **CMD** diagnoses **Memory Failures** via **Counterfactual Replays** → **Recovery Gain** → **Operation-Level Attribution**.
- **CMD-Audit** owns attribution, replay deltas, repair validation; **CMD-Skill Adapter** is deferred deployment layer (first target mem0 done, issue 0014).
- **V0 Core Label Set** = six pipeline labels; V1 adds five (11 active); bad item labels deferred to V2.
- **Verbatim Event Oracle** diagnoses **Premature Extraction Error** before assigning retrieval blame.
- **Post-Repair Context Replay** follows **ECS** and serves as automated quality gate.
- **Failure Memory** stores `wrong_memory + original_evidence + corrected_memory + repair_guidance` per ECS record; `fm_context` = diagnosis signal, `corrected_memory` = repair signal.
- **Pre-CMD Hook** (0021 two-stage: empty_ctx → RPE Judge top-k) gates CMD diagnosis. fm_context retrieved at hook time but injected at ECS stage (not before replays). Hook trusts offline F2 calibration.
- **CMD Diagnosis Layer** (replay→attribution→ECS→repair) only invoked when hook triggers.
- **RepairOrchestrator** iterates `close_deltas` via **RepairExecutor** → LLM 选 **RepairAction** → **Adapter.apply_repair** → Post-Repair Context Replay.
- **Self-Supervision Surrogate** (0021 Step 2): 4 gold-dependent labels online via BM25 success-trace + replay; offline 50 case 测量 surrogate vs gold gap（不训练）.
- **Probe Case** → CMD pipeline → **ECS record** → **4-Mode Context Case** (build order constrained).
- **Quality Gate** in CMD = Post-Repair Context Replay. Other approaches: human PR review (skill-everything), executable evidence (ErrorProbe), execution success (SQLFixAgent).
- **Counterfactual Convergence**: CMD occupies the operation-level niche in a self-organizing granularity spectrum (chunk → agent → operation). Decision 30: accelerate timeline to 2026-06-10. CMD differentiates through memory-pipeline-specific depth (11 labels, ECS, Failure Memory, Post-Repair Context Replay), not generic replay capability.
- **CMD vs Generic Agent Debugger (Rewind)**: Rewind does step-level retry; CMD does memory-pipeline-operation-level diagnosis+repair. Five-dimension differentiation remains, but Decision 34 reframes it as layered positioning rather than a head-to-head benchmark.

## Limitations (formal: cmd_innovation_core/plans/limitations.md)

CMD has 9 documented limitations across methodological, implementation, and evaluation categories. Decision 34 resolves deterministic/phrase-match scoring for the paper eval path and adds evaluator-annotator circularity as a named limitation. See `cmd_innovation_core/plans/limitations.md` for full treatment.

Primary limitation: **gold evidence dependency**—4/11 pipeline labels (`write_error`, `compression_error`, `premature_extraction_error`, `injection_error`) require gold evidence text to fabricate counterfactual memory items. This is an information-theoretic bound (detecting missing content requires knowing what content should exist), not a methodological flaw. Two-tier deployment: CMD-Audit (offline, full 11/11) → CMD-Skill Adapter (online, 7/11 + self-supervision surrogate augmentation to ~9/11). Surrogate source: BM25 retrieval from same-agent-session success-trace items, counterfactual replay validated.

## Flagged Core Ambiguities

- `retrieval_error` requires correct memory in recoverable form. If raw events hold evidence but extracted memory cannot recover it → `premature_extraction_error`.
- V0 Replay Portfolio is the six replays above; V1 adds four intervention-mode replays (10 total).
- Subagent Judge Monitor is leak-safe: triggers replay, emits no final labels/ECS/writes/gold answers/full traces.
- V0/V1 exclude bad memory item labels from attribution; all 11 pipeline labels are active in V1.
- ECS `cause` may describe item state but must not use or re-declare forbidden item label names.
- Failure Memory is not a raw log archive. Don't re-inject complete failed traces.
- CMD-Audit writes limited to replay-local sandbox. Only CMD-Skill Adapter writes production state.
- Perturbation type is injected ground truth. Never use LLM-guessed or post-hoc labels.
- 4-Mode Context Case `none` mode must fail; exclude cases where baseline already succeeds.
- CMD does not claim invention of the error→fix→reuse loop (four-source convergence). Claims focus on automated counterfactual attribution and automated semantic quality gate.
