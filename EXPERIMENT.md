# CMD Experiment Suite

本文件是 pivot 后的实验主线。实验不再按 UMem `§5.x` 的分类器模板组织，也不再把 `label macro-F1` 当作交付指标。CMD 论文现在按一条 claim 链展开：隐性故障难以靠 LLM 自诊断定位，必须用反事实修复搜索和 G-Eval 验证，把可恢复性作为 fitness。

当前主线：**修复有效性 + 在线进化轨迹**。分类指标只作为内部诊断或机制证据出现。

## Claim 链

| Claim | 论文问题 | 实验证据 | 状态 |
|---|---|---|---|
| C1 | 隐性故障存在：内容在 recall 中，但 injection / granularity / safety 让模型用不上；LLM 自诊断在这些类型上接近随机。 | Exp14 per-label 表；`llm_judge` 在隐性故障上低恢复。 | 已有 |
| C2 | 反事实单点修复搜索 + G-Eval 验证可以作为 gold-free repair fitness。 | Exp9 方差、Exp3 nested ceiling、Exp14 CMD arm。 | 已跑；Exp9 方差为 0 |
| C3 | CMD-repair 明显优于 no-repair。 | Exp14: `cmd=0.5067` vs `no_repair=0.0000`。 | 已跑 |
| C4 | CMD-select 明显优于 random / LLM 自定位。 | Exp14: `cmd=0.5067` vs `random=0.1867` vs `llm_judge=0.1867`。这是 headline。 | 已跑；**p=8e-6, CI[+.20,+.44], 27/3**（显著性表） |
| C5 | recovering `(gen_point, action)` 在 fault type / query-neighbor 内可迁移，可蒸馏为在线 top-2 定向种子。 | `probe_exhaustive.py` + Exp15 prior transfer。 | 已跑；**迁移 vs oracle 无显著差异**（显著性表） |
| C6 | MCTS 只在耦合 `b^d` 残差上保留；若耦合残差不 substantial，MCTS 从论文主线删除。 | Exp16 coupled exhaustive: TRUE_COUPLED 1/30。 | 已裁决：MCTS 不进正文主线 |
| C7 | FailureMemory 形成 skill 进化轨迹：case 流入 -> prior 沉淀 -> 后续 seed-hit / recovery 提升，warm-up 后总 rollout 成本下降。 | Exp18 online FM trajectory。 | ⚠ **增量进化未被数据支持**（seed-hit 对 prior 数量持平 0.42，fallback 成本恒定），待裁决重写或补实验 |
| C8 | ECS 结构化呈现帮助 agent 使用同一份修复内容，但只是机制证据。 | Exp17 ECS structure ablation。 | ⚠ **full_ecs vs solution 不显著（p=0.625, 3/1）**；强显著的是内容 ≫ 纯解释 |

论文两支柱：

1. **可修复**：头条是 C4 selection efficacy，`cmd 0.5067 > random 0.1867 == llm_judge 0.1867`。`injection_error` / `granularity_error` / `safety_error` 是 per-label 纹理，说明隐性故障下内容在 recall 中但模型用不上；不要把 `injection_error` 单拎成独立头条。
2. **可进化**：头条是 C7 online FailureMemory trajectory。Exp15 只证明静态 prior transfer；title-level self-evolution 由 case stream 中 seed-hit / recovery 随 recovered prior 积累改善支撑。`avg_to_rec` 有噪声，不写成严格单调下降。

ECS 是机制层，不是 Exp14 的证据路径。Exp14 只使用 `run_single_repair -> apply_pipeline_action -> rollout_to_terminal` 的 gold-free executor，不经过 `draft_ecs`、Post-Repair Context Replay 或 FailureMemory。若要声称 ECS 呈现有效，必须引用 Exp17，而不是引用 Exp14。

论文头条是 C4：**LLM 命名故障并自定位 hop 后，恢复率仍等于随机；只有反事实验证能把恢复率抬到 0.5067。** 这回答“为什么需要复杂归因”：不是归因本身要复杂，而是隐性故障下 LLM 自选修复点不可靠。

## 运行顺序

Exp16 已裁决：`TRUE_COUPLED = 1/30 (0.033)`，MCTS 不进入正文主线；论文主线采用 offline exhaustive single-point oracle + online top-2 directed seed。Exp17/Exp18 已完成，可作为机制证据和可进化证据写入结果。

```bash
# 1. headline four-arm repair efficacy
python -m experiments.run_experiment_14_repair_efficacy \
  --cmd-attribution exhaustive \
  --limit 0

# 2. offline single-point oracle and transfer prior bank
python -m experiments.probe_exhaustive \
  --limit 0 \
  --aggregate \
  --min-credit 0.05 \
  --out artifacts/sandbox/exhaustive_detail_mincredit05.csv

# 3. leave-one-out online seed transfer
python -m experiments.run_experiment_15_prior_transfer \
  --prior-bank artifacts/sandbox/exhaustive_detail_mincredit05.csv \
  --mode both

# 4. MCTS existence decider; completed: TRUE_COUPLED=1/30, cut MCTS from headline
# python -m experiments.run_experiment_16_coupled_exhaustive

# 5. online FailureMemory trajectory
python -m experiments.run_experiment_18_failure_memory_trajectory

# 6. ECS structure ablation
python -m experiments.run_experiment_17_ecs_structure_ablation
```

LLM endpoint:

```bash
export LLM_BASE_URL=http://localhost:8000/v1
export LLM_MODEL=qwen2.5-7b-instruct
export LLM_API_KEY=dummy
export LLM_TIMEOUT=120
export NO_PROXY=localhost,127.0.0.1
```

The endpoint must support `top_logprobs`; Exp9 / G-Eval / repair scoring depend on it. `LLM_API_KEY=dummy` and `NO_PROXY=localhost,127.0.0.1` are required for remote runs that otherwise route localhost traffic through a proxy.

## Active Experiments

### Exp14: Four-arm Repair Efficacy

Runner: `experiments/run_experiment_14_repair_efficacy.py`

Purpose: prove C3 and C4 under one gold-free repair executor. Arms differ only in repair-point selection:

| Arm | Selection policy | Role |
|---|---|---|
| `no_repair` | identity | floor |
| `random` | case-id deterministic random legal `(gp, action)` | chance floor |
| `llm_judge` | LLM names fault and localizes hop | strongest simple baseline |
| `cmd` | counterfactual recovery-gain selection | CMD |

Current result over 75 cases, remote vLLM + Qwen2.5-7B-Instruct:

| Arm | Recovery rate |
|---|---:|
| `no_repair` | 0.000 |
| `random` | 0.187 |
| `llm_judge` | 0.187 |
| `cmd` | 0.5067 |

Per-label headline:

| Label | CMD | LLM judge | Interpretation |
|---|---:|---:|---|
| `injection_error` | 0.800 | 0.067 | Content present but unusable; LLM self-diagnosis nearly fails. |
| `granularity_error` | 0.467 | 0.000 | Nested ceiling / counterfactual repair matters. |
| `safety_error` | 0.600 | 0.067 | Repair verification improves hidden policy-blocked cases. |
| `retrieval_error` | 0.600 | 0.800 | Visible missing-retrieval failures are not CMD's strongest case. |
| `graph_error` | 0.067 | 0.000 | Treat as floor / data issue, not as a headline claim. |

Output:

- `artifacts/sandbox/repair_efficacy_detail.csv`
- `artifacts/sandbox/repair_efficacy_summary.csv`

### Paired Significance (post-processing, no LLM)

Runner: `experiments/analyze_significance.py`

Purpose: every headline number above is a recovery RATE, but the per-case detail CSVs are paired on `case_id` (the same case is repaired under each arm), so the right test is paired, not a two-sample rate diff. This step reads the existing Exp14 / Exp15 / Exp17 detail CSVs and reports, for each comparison, a paired bootstrap 95% CI on the rate difference (B=10000, seed=0) and an exact two-sided McNemar p-value over the discordant pairs `(b, c)`. No vLLM needed — it is pure post-processing over committed artifacts.

Result:

| Claim | Comparison | n | rate_a | rate_b | diff [95% CI] | b/c | McNemar p | verdict |
|---|---|--:|--:|--:|---|--:|--:|---|
| C4 | cmd vs llm_judge | 75 | 0.507 | 0.187 | +0.320 [+0.20,+0.44] | 27/3 | 8.4e-6 | **significant** |
| C4 | cmd vs random | 75 | 0.507 | 0.187 | +0.320 [+0.20,+0.44] | 26/2 | 3.0e-6 | **significant** |
| C4 | cmd vs no_repair | 75 | 0.507 | 0.000 | +0.507 [+0.39,+0.61] | 38/0 | 7.3e-12 | **significant** |
| C5 | bm25 vs no_repair | 75 | 0.427 | 0.000 | +0.427 [+0.31,+0.53] | 32/0 | 4.7e-10 | **significant** |
| C5 | global vs no_repair | 75 | 0.400 | 0.000 | +0.400 [+0.29,+0.52] | 30/0 | 1.9e-9 | **significant** |
| C5 | bm25 vs oracle | 75 | 0.427 | 0.467 | -0.040 [-0.17,+0.09] | 12/15 | 0.701 | n.s. (ties oracle) |
| C5 | global vs oracle | 75 | 0.400 | 0.467 | -0.067 [-0.19,+0.05] | 9/14 | 0.405 | n.s. (ties oracle) |
| C8 | full_ecs vs solution | 35 | 0.343 | 0.286 | +0.057 [-0.06,+0.17] | 3/1 | 0.625 | **n.s.** |
| C8 | full_ecs vs raw_corrected | 35 | 0.343 | 0.257 | +0.086 [-0.06,+0.23] | 5/2 | 0.453 | **n.s.** |
| C8 | solution vs cause_only | 35 | 0.286 | 0.000 | +0.286 [+0.14,+0.43] | 10/0 | 0.002 | **significant** |
| C8 | full_ecs vs cause_only | 35 | 0.343 | 0.000 | +0.343 [+0.20,+0.51] | 12/0 | 0.0005 | **significant** |

Interpretation:

- **C4 is the strongest table in the paper.** cmd beats llm_judge by a 27/3 discordant split (cmd uniquely recovers 27 cases, llm_judge uniquely recovers 3), p=8.4e-6. Cite the discordant counts, not just the rate gap — they show cmd is decisively right where the two disagree, not merely better on average.
- **C5 transfer ties the oracle.** bm25 / global vs oracle are both n.s. (p=0.70 / 0.40, CI spans 0), so write "top-2 transfer priors are statistically indistinguishable from each case's own single-point oracle," which is stronger than "0.91× oracle."
- **C8 must be re-scoped.** `full_ecs > solution` is NOT significant (p=0.625, only 4 discordant cases) — do not claim contrastive ECS structure helps. What IS strongly significant is any corrected-content arm ≫ `cause_only` (p<0.002): explanation alone is not a repair, the corrected content is. Re-headline C8 as content-vs-explanation, not structure.

Output:

- `artifacts/sandbox/significance_summary.csv`

### Probe: Exhaustive Single-point Oracle

Runner: `experiments/probe_exhaustive.py`

Purpose: remove MCTS/UCB search coverage as a confound. It scans every single `(gen_point, action)` along the identity backbone, then commits only if best credit exceeds `--min-credit`.

Use it for:

- C2: validate that recovery gain is a usable repair signal.
- C5: build the offline prior bank.
- A2 diagnosis: distinguish signal quality from MCTS coverage failure.

Known result:

- Total `label_acc=0.4800`, `decided_acc=0.6792`, `hop_acc=0.5333`, abstentions 22/75.
- Decided label accuracy is strong for `injection_error` (1.000), `safety_error` (0.8571), `granularity_error` (0.8000), and usable for `retrieval_error` (0.6429). `graph_error` is a floor/data issue.
- Modal `(gen_point, action)` concentration: granularity 0.800, graph 0.667, injection 0.846, retrieval 0.643, safety 0.857.
- This supports top-2 prior transfer, not a pure label classifier story.

Output:

- `artifacts/sandbox/exhaustive_detail_mincredit05.csv`

### Exp15: Prior Transfer

Runner: `experiments/run_experiment_15_prior_transfer.py`

Purpose: test whether offline exhaustive culprits transfer to held-out cases as online top-K seeds. This is C5's online landing evidence.

Arms:

| Arm | Meaning |
|---|---|
| `no_repair` | identity floor |
| `oracle` | held-out case's own exhaustive culprit; upper bound |
| `global` | top-K global modal `(gp, action)` from other cases |
| `bm25` | top-K modal `(gp, action)` among query-nearest other cases |

Acceptance gate: `bm25` should recover a high fraction of `oracle` while never reading the held-out row's own culprit.

Current result over 75 cases:

| Arm | Recovered | Rate | vs oracle |
|---|---:|---:|---:|
| `no_repair` | 0/75 | 0.0000 | - |
| `oracle` | 35/75 | 0.4667 | 1.00 |
| `global` | 30/75 | 0.4000 | 0.86 |
| `bm25` | 32/75 | 0.4267 | 0.91 |

Interpretation: query-nearest top-2 seeds recover 91% of the single-point oracle's recoverable mass. The global prior is already strong because generic injection-like repairs transfer broadly, so write this as targeted prior transfer rather than query-specific retrieval alone.

Output:

- `artifacts/sandbox/prior_transfer_detail.csv`

### Exp16: Coupled Exhaustive

Runner: `experiments/run_experiment_16_coupled_exhaustive.py`

Purpose: decide whether MCTS remains in the paper. Exp8 used inconsistent rulers: single-point legs used defective UCB credit, while joint used tree max. Exp16 uses full coverage and the same terminal recovery scale for both legs.

Result over 30 coupled-boundary cases:

| Verdict | Count | Rate |
|---|---:|---:|
| `single_ok` | 6/30 | 0.200 |
| `TRUE_COUPLED` | 1/30 | 0.033 |
| `neither` | 23/30 | 0.767 |

Interpretation: TRUE coupled residual is too small to justify MCTS in the paper body. Most boundary cases are neither single-point nor pair recoverable, so the residual is a data/model/threshold floor rather than a search win.

Verdicts:

| Verdict | Meaning | Paper consequence |
|---|---|---|
| `TRUE_COUPLED` | `best_single < threshold` and `best_combo >= threshold` | Keep MCTS only if substantial; observed 1/30, not substantial. |
| `single_ok` | single-point full coverage recovers | Supports exhaustive oracle + top-2 online seed. |
| `neither` | neither single nor pair recovers | Treat as data/model floor, not MCTS win. |

Output:

- `artifacts/sandbox/coupled_exhaustive_detail.csv`

### Exp17: ECS Structure Ablation

Runner: `experiments/run_experiment_17_ecs_structure_ablation.py`

Purpose: isolate whether ECS-style structure helps the agent use the same repaired content. This is a mechanism experiment for C8, not the Exp14 headline path and not a FailureMemory reuse test.

Scope:

- Input cases: `real_multihop_cases.json`.
- Include only the subset where exhaustive single-point repair recovers. Exclude unrecoverable rows with `exclude_reason=no_recoverable_single_point`.
- Fix the repair point and corrected evidence across all arms. Arms differ only in context presentation.
- Do not mix in FailureMemory retrieval; FM online reuse belongs to Exp18.

Arms:

| Arm | Context | Role |
|---|---|---|
| `raw_corrected` | baseline + corrected evidence block | Direct evidence-injection baseline. |
| `corrected_only` | corrected_memory | Content-only repair signal. |
| `solution` | corrected_memory + repair_guidance | Production default. |
| `full_ecs` | wrong/cause + corrected_memory + repair_guidance | Contrastive ECS; measures help vs pollution risk. |
| `cause_only` | wrong/cause without corrected_memory | Negative control. |

Primary metric: Post-Repair recovery rate. Secondary metrics: answer score, evidence score, token cost, regression risk.

Current result on the 35 recoverable single-point cases:

| Arm | Recovered | Rate | Ans | Ev |
|---|---:|---:|---:|---:|
| `raw_corrected` | 9/35 | 0.2571 | 0.6247 | 0.2560 |
| `corrected_only` | 10/35 | 0.2857 | 0.6191 | 0.2479 |
| `solution` | 10/35 | 0.2857 | 0.6688 | 0.2821 |
| `full_ecs` | 12/35 | 0.3429 | 0.6570 | 0.3917 |
| `cause_only` | 0/35 | 0.0000 | 0.1028 | 0.2725 |

Interpretation: `full_ecs` gives the best recovery and evidence score, so ECS can be kept as mechanism evidence. The effect is modest; do not make it a headline result. `cause_only=0` is the clean negative control showing that explanation alone is not a repair.

Reading of the current result:

- `full_ecs > solution`: contrastive ECS helps modestly over solution-only guidance.
- `solution` has the highest answer score but not the highest recovery rate; do not optimize only for answer score.
- `cause_only=0`: explanation alone is not a repair.

Output:

- `artifacts/sandbox/ecs_structure_ablation_detail.csv`
- `artifacts/sandbox/ecs_structure_ablation_summary.csv`

### Exp18: FailureMemory Online Trajectory

Runner: `experiments/run_experiment_18_failure_memory_trajectory.py`

Purpose: prove C7: FailureMemory is not only a static prior bank but an online evolution loop. The main claim is that recovered records become reusable priors, improving seed-hit / recovery after warm-up while keeping total rollout cost bounded. Do not claim strict monotonic `avg_to_rec`; it is noisy.

Protocol:

```text
fixed random prequential stream, seed=42
for each case:
  1. retrieve active prior from previous recovered records only
  2. try top-K seed repairs, K=2
  3. if seeds do not recover, run exhaustive fallback to learn
  4. write full case ledger for all outcomes
  5. if recovered, add record to active prior bank
```

Memory layers:

| Layer | Stores | Used for automatic future seed? |
|---|---|---|
| Case ledger | all cases: wrong context, cause, corrected memory, guidance, assessment | No |
| Active prior bank | recovered cases only: `(query_signature, hop, action)` + recovery gain | Yes |

The ledger remembers how the system was wrong; the active prior is recovered-only to avoid turning failed repairs into reusable guidance.

Main metric: seed recovery and total recovery improve over stream prefixes as prior count grows. Secondary metric: total rollouts per case after warm-up.

Current result over 75 streamed cases, seed=42, top-K=2:

| Bin | Recovery | Seed recovery | Avg total rollouts | Avg to recovery | Priors written |
|---|---:|---:|---:|---:|---:|
| 1-15 | 0.4667 | 0.4000 | 5.6667 | 1.8571 | 7 |
| 16-30 | 0.5333 | 0.2667 | 7.2667 | 4.8750 | 8 |
| 31-45 | 0.7333 | 0.5333 | 5.0000 | 3.1818 | 11 |
| 46-60 | 0.6667 | 0.4000 | 6.3333 | 4.6000 | 10 |
| 61-75 | 0.6667 | 0.4667 | 6.1333 | 4.1000 | 10 |

Derived summary:

- Overall recovery: 46/75 = 0.6133.
- Overall seed recovery: 31/75 = 0.4133, close to Exp15 BM25 static prior (0.4267).
- First 30 vs last 45: recovery improves 0.5000 -> 0.6889; seed recovery improves 0.3333 -> 0.4667; avg total rollouts improves 6.4667 -> 5.8222.
- `avg_to_rec` is not monotonic, so the paper should say "online memory improves seed-hit and recovery; rollout cost is lower after warm-up" rather than "every prefix recovers faster."

Required columns:

| Field | Meaning |
|---|---|
| `case_index` | stream position |
| `case_id` | held-out case |
| `prior_source_count` | previous active-prior records used |
| `seed_choices` | top-2 `(gp, action)` seeds |
| `seed_rank_recovered` | 0 if no seed recovered, otherwise 1 or 2 |
| `seed_rollouts_used` | seed-stage cost |
| `fallback_rollouts_used` | exhaustive fallback cost |
| `total_rollouts_used` | total cost for the case |
| `best_net_gain` | recovery over no-repair |
| `recovered` | thresholded net recovery |
| `ledger_written` | whether full case ledger was written |
| `active_prior_written` | whether recovered case entered active prior bank |

Output:

- `artifacts/sandbox/failure_memory_trajectory_detail.csv`
- `artifacts/sandbox/failure_memory_trajectory_summary.csv`

## Supporting Experiments

These experiments remain useful, but they no longer define the paper skeleton.

| Experiment | Runner | New role |
|---|---|---|
| Exp6 hook routing | `experiments/run_experiment_06_hook_routing.py` | Mechanism evidence for Fill/Fix routing upstream of repair. Not headline. |
| Exp9 G-Eval variance | `experiments/run_experiment_09_geval_variance.py` | Foundation for using recovery gain as deterministic fitness; completed with zero observed variance. |
| Exp10 surrogate gap | `experiments/run_experiment_10_surrogate_gap.py` | Gold-free online path credibility; retention-rate proxy keeps most recovery signal. |
| Exp13 cross-dataset | `experiments/run_experiment_13_cross_dataset.py` | Cross-source recovery / seed-transfer stability; completed. |
| Exp17 ECS structure | `experiments/run_experiment_17_ecs_structure_ablation.py` | Mechanism evidence for ECS presentation; fixed repair content, no FM reuse. |

### Exp9: G-Eval Variance

Result:

| Method | Mean score | Std | Repeats |
|---|---:|---:|---:|
| `continuous_verify` | 3.0000 | 0.0000 | 10 |
| `temperature_sampling` | 3.0000 | 0.0000 | 10 |

Interpretation: the verifier is stable enough for use as repair fitness in these runs. Since scores saturate at 3.0, this supports repeatability, not broad calibration.

Output:

- `artifacts/sandbox/experiment_geval_variance.csv`

### Exp10: Surrogate Gap

Result over 26 gold-dependent cases:

| Path | Label correctness | Recovery gain mean |
|---|---:|---:|
| `gold_dependent` | 0.1923 | 0.5113 |
| `surrogate` | 0.0769 | 0.2305 |
| `retention_rate` | 0.4000 | 0.4507 |

Interpretation: raw surrogate labels are weak, but the gold-free `retention_rate` proxy preserves about 88% of the gold-dependent recovery gain (`0.4507 / 0.5113`). This strengthens the repair-fitness story and again argues against using label correctness as the primary metric.

Output:

- `artifacts/sandbox/experiment_surrogate_gap.csv`

### Exp13: Cross-source Recovery Transfer

Purpose: test whether the repair prior transfers beyond the 75-case multihop suite across LongMemEval, MemoryArena, and ToolBench-derived cases. This is a generalization check for C5, not a label macro-F1 experiment.

Current result over 300 pipeline-step cases, 100 per source:

| Source | Arm | Recovered | Rate | Avg net | vs oracle |
|---|---|---:|---:|---:|---:|
| longmemeval | oracle | 55/100 | 0.5500 | 0.4264 | 1.00 |
| longmemeval | global_xsource | 41/100 | 0.4100 | 0.2970 | 0.75 |
| longmemeval | bm25_xsource | 40/100 | 0.4000 | 0.2940 | 0.73 |
| memoryarena | oracle | 32/100 | 0.3200 | 0.1731 | 1.00 |
| memoryarena | global_xsource | 31/100 | 0.3100 | 0.1410 | 0.97 |
| memoryarena | bm25_xsource | 29/100 | 0.2900 | 0.1332 | 0.91 |
| toolbench | oracle | 34/100 | 0.3400 | 0.1110 | 1.00 |
| toolbench | global_xsource | 39/100 | 0.3900 | 0.0993 | 1.15 |
| toolbench | bm25_xsource | 27/100 | 0.2700 | 0.0759 | 0.79 |
| ALL | oracle | 121/300 | 0.4033 | 0.2368 | 1.00 |
| ALL | global_all | 109/300 | 0.3633 | 0.1783 | 0.90 |
| ALL | bm25_all | 102/300 | 0.3400 | 0.1590 | 0.84 |
| ALL | global_xsource | 111/300 | 0.3700 | 0.1791 | 0.92 |
| ALL | bm25_xsource | 96/300 | 0.3200 | 0.1677 | 0.79 |

Interpretation:

- Cross-source prior transfer is real: `global_xsource` recovers 111/300, 92% of the single-seed oracle rate.
- BM25 transfer is useful but not the most stable arm: `bm25_all` reaches 84% of oracle, while `bm25_xsource` reaches 79% overall and varies by source.
- The strongest generalization wording is "source-level repair priors transfer across datasets"; do not claim query-nearest BM25 is uniformly best.
- `vs_oracle > 1` can occur because `oracle` is one own-case exhaustive culprit seed, while transfer arms test top-K priors. Treat oracle as the single-point self-prior reference, not a strict upper bound over all top-K choices.

Output:

- `artifacts/sandbox/experiment_cross_dataset.csv`
- `artifacts/sandbox/experiment_cross_dataset_detail.csv`

## Parked or Deleted Experiments

| Old experiment | Decision | Reason |
|---|---|---|
| Exp1 step-vs-global | Internal diagnostic | `label_acc` is no longer a target. Hop localization becomes repair-point selection evidence inside Exp14. |
| Exp2 MCTS vs exhaustive | Conditional rewrite | Old “8.6% beats exhaustive” claim is killed. If Exp16 keeps MCTS, rerun as rollout budget vs recovery rate with a top-2 seed line. If Exp16 kills MCTS, delete. |
| Exp4 divergence operator | Park | Tier-2 item gate mechanism; outside current repair-efficacy defense. |
| Exp5 item gate ablation | Park | Same Tier-2 mechanism; not headline. |
| Exp7 stale vs conflict | Park | Same Tier-2 mechanism. |
| Exp8 coupled failure | Replaced by Exp16 | Exp8 mixed UCB single-point credit with max-tree joint credit and was confounded by A2. |
| Exp11 monitor leak | Contract test | Move evidence to `tests/`; not a paper experiment. |
| Exp12 MCTS distill | Absorbed by Exp15 | If MCTS dies, old distillation loses its base. Exp15's exhaustive -> prior -> seed path is cleaner. |

## Exp16 Branch Policy

Exp16 determines the final architecture and paper text.

### Branch A: TRUE_COUPLED is substantial

Keep Tier-3 MCTS, but only as the coupled-residual solver.

Required follow-ups:

- Rewrite Exp2 as recovery-rate vs rollout-budget curve.
- Add a top-2 directed seed line to the curve.
- Rewrite Exp12 as “search distillation on coupled residuals.”
- Keep architecture figure with Tier-3 MCTS.

### Branch B: single-point full coverage recovers most cases

Cut MCTS from the headline.

Required follow-ups:

- Delete Exp2 / Exp8 / Exp12 from the paper body.
- Remove Tier-3 MCTS from the main architecture figure.
- Present deliverable as offline exhaustive oracle + online top-2 directed seed.
- Keep MCTS only as future work or appendix, if at all.

## Remaining Experimental Work

The main paper evidence is complete for the two-pillar story:

- Repairability: Exp14 + exhaustive probe + Exp15 + Exp9/Exp10.
- Evolvability: Exp18.
- Mechanism support: Exp17.
- Architecture pruning: Exp16.
- Cross-source generalization: Exp13.

Exp13 launch:

```bash
# Build the three-source single-point prior bank first.
python -m experiments.probe_exhaustive \
  --cases data/probe_cases/real_three_source_cases.json \
  --limit 0 \
  --aggregate \
  --min-credit 0.05 \
  --out artifacts/sandbox/exhaustive_three_source_detail_mincredit05.csv

# Then evaluate per-source and cross-source transfer.
python -m experiments.run_experiment_13_cross_dataset \
  --cases data/probe_cases/real_three_source_cases.json \
  --prior-bank artifacts/sandbox/exhaustive_three_source_detail_mincredit05.csv \
  --source-mode both
```

For a runner smoke test after the full prior bank exists, add `--limit-per-source 3` only to `run_experiment_13_cross_dataset`. Do not add it to `probe_exhaustive`; that script only supports a global `--limit`.

No additional headline experiment is required for the current paper. Non-experiment work still needed before writing tables:

- ~~paired deltas / bootstrap intervals for Exp14, Exp15, Exp17, Exp18, and Exp13~~ — DONE for Exp14/15/17 (`analyze_significance.py` -> `significance_summary.csv`); Exp18/Exp13 paired deltas still open (see below);
- figure-ready CSV aggregation for the main table and trajectory plot;
- artifact checksum / command log for reproducibility.

## Comparison vs UMem-style classifier baseline

The original UMem-style protocol (the `§5.x` per-label classifier template this suite pivoted away from) treats memory-failure diagnosis as a multi-class labeling task and reports label macro-F1 against a gold fault type. CMD deliberately does NOT compete on that axis. The contrast that justifies the pivot:

| Axis | UMem-style classifier | CMD |
|---|---|---|
| Deliverable | predicted fault label per case | repaired context + recovery gain Δk |
| Metric | label macro-F1 vs gold | recovery rate (gold-free construction, gold-scored) |
| 5 labels | prediction targets | internal action space for repair search |
| Hidden failures (injection/granularity/safety) | scored as labels; content "present" so a classifier sees no miss | surfaced as low LLM-judge recovery vs high CMD recovery (C1/C4) |
| Self-evolution | none | FailureMemory prior transfer (C5/C7) |

The head-to-head that lands this contrast is already in Exp14: the `llm_judge` arm IS a UMem-style self-classifier (it names the fault label and localizes the hop). Its recovery is statistically tied to random (0.187 vs 0.187; C1) and is beaten by cmd with p=8.4e-6 (C4). The diagnostic detail behind that number — `llm_judge` collapses to predicting `retrieval_error` on 73/75 cases and only "recovers" when gold happens to be retrieval_error — is the evidence that a label-classifier cannot see hidden failures. That collapse is not yet pulled into a figure (see below).

## Experiments still to supplement

Ordered by threat to the claim chain.

1. **C7 self-evolution is not yet supported by the data (highest priority).** Exp18's single seed=42 stream shows seed-hit flat against prior count (0.42 at 1-5 priors, 0.42 at 6+) and constant fallback cost — the runner's BM25 seed retrieval degenerates to a global mode when this suite's cross-fault queries are dissimilar, and `_fallback_rollout_budget` is a constant, so "cost down after warm-up" cannot hold by construction. Either: (a) re-scope C7 to "a small static prior bank suffices; incremental accumulation does not add" (merges with C5/C13, zero new runs), or (b) supplement Exp18 with ≥5 shuffle seeds + a prior-capacity ablation K∈{1,2,5,all} to test whether accumulation ever helps under any ordering. Prediction from source: still no incremental gain.
2. **C8 re-headline (no new run needed, just rewrite).** Significance already shows `full_ecs > solution` is n.s. (p=0.625). Additionally the `full_ecs` arm re-injects `[Wrong Context] base_context` that `solution` omits (461 vs 297 tokens), so the arms confound structure with information volume. Re-scope the claim to "corrected content ≫ explanation-only (`cause_only`)" which is strongly significant (p<0.002), and drop the structure-helps wording.
3. **Exp18 / Exp13 paired significance (zero LLM cost).** Extend `analyze_significance.py` to cover the FM-trajectory recovery prefixes and the cross-source transfer arms so every headline table ships with a CI and a p-value, matching Exp14/15/17.
4. **C1 label-collapse figure (zero LLM cost).** Turn the `llm_judge` 73/75 → retrieval_error confusion into a one-panel figure. This upgrades C1 from a tied scalar into the visual that motivates the whole pivot away from UMem-style classification.

## Output Registry

| Artifact | Producer | Claim |
|---|---|---|
| `repair_efficacy_detail.csv` | Exp14 | C1-C4 |
| `repair_efficacy_summary.csv` | Exp14 | C3-C4 |
| `exhaustive_detail_mincredit05.csv` | exhaustive probe | C2-C5 |
| `prior_transfer_detail.csv` | Exp15 | C5 |
| `coupled_exhaustive_detail.csv` | Exp16 | C6 |
| `ecs_structure_ablation_detail.csv` | Exp17 | C8 |
| `ecs_structure_ablation_summary.csv` | Exp17 | C8 |
| `failure_memory_trajectory_detail.csv` | Exp18 | C7 |
| `failure_memory_trajectory_summary.csv` | Exp18 | C7 |
| `experiment_geval_variance.csv` | Exp9 | C2 |
| `experiment_surrogate_gap.csv` | Exp10 | C2 / C5 |
| `experiment_cross_dataset.csv` | Exp13 | C5 / generalization |
| `experiment_cross_dataset_detail.csv` | Exp13 | C5 / generalization |
| `significance_summary.csv` | analyze_significance | C4 / C5 / C8 paired CI + McNemar |

Do not add placeholder CSV numbers to the paper. Only cite files that exist under `artifacts/sandbox/` from a completed run.

## Data

Known probe inputs:

- `data/probe_cases/real_multihop_cases.json`: 75 pipeline-step cases for Exp14 / exhaustive / Exp15.
- `data/probe_cases/real_coupled_failure_boundary_cases.json`: coupled-boundary cases for Exp16.
- `data/probe_cases/real_three_source_cases.json`: larger cross-source suite for Exp13 and older diagnostics.
