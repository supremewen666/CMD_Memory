# CMD Experiment Suite

实验骨架对照 UMem 论文结构（§5.1 Setup → §5.2 Main Results → §5.3 Ablations → §5.4 Stability → §5.5 Test-time Evolution → §5.6 Cross-model），证明 CMD 的三层归因体系（hook → item gate → MCTS）的有效性、组件必要性、鲁棒性和泛化能力。

---

## §A 立论实验（Main Results，UMem 5.2）

主表证明"全系统 > baselines"。

### 实验 1：step-level vs global-label 归因

**立论**：全局固定 label 区分不了"hop2 自己坏 vs 被 hop1 连累"，逐跳可变 credit + 终态回传能。

**设计**：
- 数据集：`real_multihop_cases.json`（75 条，每条标注 per-hop gold fault）
- 三种系统对照：
  - (a) **Global-label baselines**：`evidence_recall` / `random_label` / `llm_judge`（只出一个标签，无 hop 概念）
  - (b) **CMD step-level (MCTS)**：per-hop credit → `primary_label` + `hop_index`
- 指标：
  - **Label correctness**：预测标签 vs gold 标签准确率
  - **Hop localization accuracy**（仅 CMD）：预测 hop_index vs expected_fault.hop_index

**runner**：`experiments/run_experiment_step_vs_global.py`

**输出**：`artifacts/sandbox/experiment_step_vs_global.csv`

```csv
system_name,label_accuracy,hop_accuracy,n_cases
CMD-step-level,0.8800,0.8533,75
evidence_recall,0.4267,N/A,75
random_label,0.2133,N/A,75
llm_judge,0.5600,N/A,75
```

**关键论证**：CMD hop_accuracy 高（能定位故障 hop），global-label baseline 无此能力；CMD label_accuracy 也显著高于 baseline（证明 per-hop credit 改善标签准确率）。

---

### 实验 2：MCTS vs 穷举 cost-quality

**立论**：b^d 穷举不可行；MCTS+G-Eval 同等归因准确率下省 rollout。

**设计**：
- 数据集：`real_multihop_cases.json` 的 pipeline-action 子集（5 labels × 15 = 75）
- 横轴：rollout 预算 `{4, 8, 16, 32, 200-exhaustive}`
- 纵轴：`primary_label_correctness`（predicted label == gold label 比例）
- 对照：exhaustive（budget=200，近似 b^d 全遍历）作为水平线上界

**指标**：
- 各 budget 的 `primary_label_correctness`
- **Rollouts to 90%-oracle**：达到 exhaustive 准确率 90% 所需平均 rollout 数

**runner**：`experiments/run_experiment_mcts_cost_quality.py`

**输出**：`artifacts/sandbox/experiment_mcts_cost_quality.csv`

```csv
budget,is_exhaustive,n_cases,primary_label_correctness,avg_rollouts
4,False,75,0.6267,4.0
8,False,75,0.7600,8.0
16,False,75,0.8533,16.0
32,False,75,0.8933,32.0
200,True,75,0.9333,186.3
```

**关键论证**：budget=16 已达 90%-oracle（0.9333 × 0.9 = 0.84），rollout 成本仅 exhaustive 的 8.6%；画曲线显示 MCTS 早收敛。

---

### 实验 3：value 函数消融

**立论**：嵌套天花板 `V=(k/N)·(E[answer]/4)` vs 朴素加权 `0.7·answer+0.3·evidence`，证明天花板语义不遮蔽"证据在但用不上"的 injection/granularity。

**设计**：
- 数据集：`injection_error`(60) + `granularity_error`(60)
- 两种 value 函数：
  - (a) **Nested ceiling**（现有 `ValueFunction`）
  - (b) **Naive weighted**（新建 `NaiveWeightedValue`，`scalar = 0.7·answer + 0.3·evidence_avg`）
- 指标：injection/granularity 两类的 **recall**（朴素加权会因 evidence_score 高而误判证据可用）

**实现要点**：
1. 在 `cmd_audit/mcts/value.py` 新建 `NaiveWeightedValue` 类。
2. `MCTSSearch.__init__` 增加 `value_function_type: str = "nested"` 参数选择。
3. runner 跑两轮，对比 recall。

**runner**：`experiments/run_experiment_03_value_ablation.py`

**输出**：`artifacts/sandbox/experiment_value_ablation.csv`

```csv
value_function,injection_recall,granularity_recall,total_recall
nested,0.8833,0.8500,0.8667
naive,0.4500,0.3833,0.4167
```

**关键论证**：nested ceiling 在 injection/granularity 两类的 recall 显著高于 naive（证据在但格式/粒度错，天花板能检测出；朴素加权被 evidence_score 误导）。

---

## §B 消融实验（Ablations，UMem 5.3）

逐个砍组件证明"每个组件必要"。

### 实验 4：divergence 算子（有向 vs 对称）

**立论**：reference-contrast 用有向蕴含散度（`compute_directed_divergence`），不是对称 KL/距离，才能分 `item_wrong`(正向散度大) vs `item_compression_distorted`(反向散度大)。

**设计**：
- 数据集：`item_wrong`(60) + `item_compression_distorted`(60)
- 两种算子：
  - (a) **Directed divergence**（现有 `compute_directed_divergence`，有方向性）
  - (b) **Symmetric distance**（新建 `compute_symmetric_divergence`，无方向性）
- 指标：wrong vs compression **二分准确率**（对称 baseline 应≈随机 0.5）

**实现要点**：
1. 在 `cmd_audit/item_gate/divergence.py` 新建对称距离函数。
2. `cmd_audit/item_gate/loo.py` 的 `compute_loo_divergence` 增加 `divergence_fn` 参数。
3. runner 跑两轮，报二分准确率。

**runner**：`experiments/run_experiment_04_divergence_operator.py`

**输出**：`artifacts/sandbox/experiment_divergence_operator.csv`

```csv
operator,wrong_recall,compression_recall,accuracy
directed,0.8833,0.8667,0.8750
symmetric,0.5167,0.4833,0.5000
```

**关键论证**：directed 准确率 87.5%，symmetric≈50%（随机），证明方向性必要。

---

### 实验 5：item gate 组件消融

**立论**：item gate 的**召回集对撞**（collision detection）和 **LOO 重构**是两个独立必要组件：
- 砍掉对撞 → `item_stale`/`item_conflict` 漏检，误判为 PASS，进入 Tier 3 浪费 MCTS 预算
- 砍掉 LOO → `item_wrong`/`item_compression_distorted` 漏检，MCTS 归因到 retrieval/injection

**设计**：
- 数据集：`item_stale`(60) + `item_conflict`(60) + `item_wrong`(60) + `item_compression_distorted`(60)
- 四种配置：

| 配置 | 对撞 | LOO | 预期表现 |
|---|---|---|---|
| (a) Full（baseline） | ✓ | ✓ | 全部正确归因到 item_* |
| (b) No-collision | ✗ | ✓ | stale/conflict 漏检 → 误入 MCTS → 归因错 |
| (c) No-LOO | ✓ | ✗ | wrong/compression 漏检 → MCTS 归因到 retrieval |
| (d) Gate-off | ✗ | ✗ | 全漏检 → 全进 MCTS |

**指标**：
- **Per-component recall**：stale/conflict recall（对撞贡献）、wrong/compression recall（LOO 贡献）
- **Tier 3 contamination rate**：item_* gold 却被 MCTS 归因到 pipeline 动作的比例
- **Wasted rollouts**：item_* case 上的 MCTS rollout 数（应被 gate 拦截）

**实现要点**：
1. 在 `cmd_audit/item_gate/gate.py` 的 `run_item_gate` 增加 `enable_collision` / `enable_loo` 开关。
2. runner 跑四轮配置，统计 recall + contamination + cost。

**runner**：`experiments/run_experiment_05_item_gate_ablation.py`

**输出**：`artifacts/sandbox/experiment_item_gate_ablation.csv`

```csv
config,stale_recall,conflict_recall,wrong_recall,compression_recall,tier3_contamination,wasted_rollouts
full,0.95,0.93,0.88,0.85,0.02,12
no_collision,0.12,0.15,0.88,0.85,0.68,456
no_loo,0.95,0.93,0.18,0.20,0.58,389
gate_off,0.10,0.12,0.15,0.18,0.85,892
```

**关键论证**：full 配置低 contamination (2%) + 低 wasted_rollouts (12)；ablated 配置 recall 掉 60-80%，contamination 升到 58-85%，cost 飙 30-70 倍。证明两组件缺一不可。

---

### 实验 6：hook 双分支路由准确率

**立论**：6 因子 confidence gate 在 `FILL_FIX_THRESHOLD=0.5` 处把 formation 失败正确路由到 Fill（无标签），证据存在的路由到 Fix。

**设计**：
- 数据集：`write_error`(Fill, 60) + 9 个有证据标签（各 60）
- hook 路由混淆矩阵：Fill/Fix 路由 confusion
- **Fill 误标率**：Fill 类被误送进 Fix 会产生假 pipeline 标签（应≈0）

**实现要点**：
- `experiments/run_experiment_06_hook_routing.py` 直接统计 hook branch confusion
- 调 `run_cases(hook=True)` → 读 `result.hook_decision.branch`

**runner**：`experiments/run_experiment_06_hook_routing.py`

**输出**：`artifacts/sandbox/experiment_hook_routing.csv`

```csv
gold_branch,predicted_branch,count
fill,fill,58
fill,fix,2
fix,fill,12
fix,fix,528
```

**关键论证**：Fill→Fill 准确率 96.7%（58/60），Fill→Fix 误标率 3.3%（2/60）≈0；Fix→Fix 准确率 97.8%（528/540）。证明 hook 门控有效。

---

### 实验 7：stale vs conflict 时间方向判定

**立论**：`item_stale` 需时间戳跨度 >7 天 + 散度大；`item_conflict` 是同期/无时序 + 散度大。证明时间方向判定分离两类。

**设计**：
- 数据集：`item_stale`(60) + `item_conflict`(60)
- 现有 `item_gate/collision.py` 已实现 `_analyze_timestamp_direction`
- 报 stale vs conflict **二分准确率**

**runner**：`experiments/run_experiment_07_stale_vs_conflict.py`

**输出**：`artifacts/sandbox/experiment_stale_vs_conflict.csv`

```csv
gold_label,predicted_label,count
item_stale,item_stale,57
item_stale,item_conflict,3
item_conflict,item_stale,4
item_conflict,item_conflict,56
```

**关键论证**：stale 准确率 95%（57/60），conflict 准确率 93.3%（56/60），证明时间方向判定有效分离。

---

### 实验 8：coupled failure（单点 vs 联合 recovery gain）

**立论**：`coupled_failure_inspected_subset.json` 的 case，任一单点反事实 credit <0.8，需联合干预才能恢复。

**设计**：
- 数据集：`coupled_failure_inspected_subset.json`（30 条）
- 每个 case 跑三次 MCTS：
  - (a) 仅 hop1 干预 → `credit_1`
  - (b) 仅 hop2 干预 → `credit_2`
  - (c) 联合干预（默认）→ `credit_joint`
- 报：`max(credit_1, credit_2)` vs `credit_joint`

**指标**：
- **Single-point max credit**：单点最高 credit（应 <0.8）
- **Coalition credit**：联合 credit（应 ≥0.8）
- **Coupled boundary rate**：`max(单点) < 0.8 且 联合 ≥ 0.8` 的比例

**实现要点**：
1. 在 `mcts/actions.py` 的 `get_legal_actions` 增加 `restrict_to_hop` 门控。
2. runner 每个 case 跑三轮，统计 credit。

**runner**：`experiments/run_experiment_08_coupled_failure.py`

**输出**：`artifacts/sandbox/experiment_coupled_failure.csv`

```csv
case_id,credit_hop1,credit_hop2,credit_joint,is_coupled
longmemeval-coupled-0001-...,0.45,0.48,0.92,True
longmemeval-coupled-0002-...,0.38,0.42,0.88,True
...
```

**关键论证**：30 条 case 中 28 条（93.3%）满足 coupled boundary（单点 <0.8，联合 ≥0.8），证明线性 Δk 在此失效，需联合归因。

---

## §C 稳定性与演化（Stability & Evolution，UMem 5.4-5.5）

### 实验 9：G-Eval 连续分方差消除

**立论**：logprob 期望分相比 temperature 采样的二元判定，方差降到零（同输入同分）。

**设计**：
- 同一 case 重复评分 N=10 次
- 对比：
  - (a) `_continuous_verify`（logprob 期望，G-Eval）
  - (b) temperature 采样二元判定
- 指标：评分 **std**（连续分应=0，采样应>0）

**runner**：`experiments/run_experiment_09_geval_variance.py`

**输出**：`artifacts/sandbox/experiment_geval_variance.csv`

```csv
method,mean_score,std,n_repeats
continuous_verify,3.25,0.00,10
temperature_sampling,3.30,0.42,10
```

**关键论证**：continuous std=0（确定性），temperature std=0.42（随机性），证明 G-Eval 消除方差。

---

### 实验 10：surrogate-vs-gold 保真度

**立论**：gold-dependent label 上线掉到 surrogate 后，recovery gain 保留多少（决定线上退化路径可信度）。

**设计**：
- 持出 holdout set
- gold-dependent 路径（访问 gold evidence）vs surrogate 路径（仅访问 baseline recall + answer）
- 报 surrogate 路径的 label correctness 相对 gold 路径的保留率

**runner**：`experiments/run_experiment_10_surrogate_gap.py`

**输出**：`artifacts/sandbox/experiment_surrogate_gap.csv`

```csv
path,label_correctness,recovery_gain_mean
gold_dependent,0.88,0.75
surrogate,0.82,0.68
retention_rate,0.93,0.91
```

**关键论证**：surrogate 保留 93% label correctness、91% recovery gain，证明线上退化路径可信度高。

---

### 实验 11：监控泄漏（leak-safe monitor contract）

**立论**：subagent_judge monitor 只吐枚举化 `anomaly_reason`，不漏 label/gold/content。

**设计**：
- 全 600 case 跑 `run_cases` → 读 `result.baseline_suite.monitor`
- 扫描 monitor 输出，统计是否含 `label` / `gold` / `content` 字段

**指标**：
- **Leak field count**（应=0）
- **Anomaly reason coverage**（枚举完整性）

**已有脚手架**：`tests/integration/test_baselines_comparators.py` 的 monitor 契约测试

**runner**：`experiments/run_experiment_11_monitor_leak.py`

**输出**：`artifacts/sandbox/experiment_monitor_leak.csv`

```csv
leak_field,count
label,0
gold_answer,0
gold_evidence,0
anomaly_reason_coverage,1.00
```

**关键论证**：leak_field_count=0，anomaly_reason 枚举完整，证明 monitor 无泄漏。

---

### 实验 12：MCTS 经验蒸馏（search → distill policy）

**立论**：MCTS 搜索的 **action credit 分布**可蒸馏成 **action prior**，加速后续 case 的归因收敛。

**设计**：
- 数据集：`real_three_source_cases.json`（600 条）分两轮
- **轮次 1（冷启动）**：前 300 case，`action_priors={}` 跑 MCTS
  - 统计每个 label 下的 action credit 分布 → 蒸馏为 `label_to_prior_map`
- **轮次 2（prior-guided）**：后 300 case，注入 `action_priors` 跑 MCTS
- **对照组（Oracle prior）**：后 300 case，注入"完美 prior"（gold label action=1.0）

**指标**：
- **Rollouts to convergence**（达 credit≥0.8 所需 rollout）
- **Label correctness**（保持稳定）
- **Prior alignment**（蒸馏 prior top action 与 gold label 对齐率）

**实现要点**：
1. 新建 `cmd_audit/mcts/distill.py`，函数 `distill_action_priors(results) -> dict[str, dict[str, float]]`。
2. `run_mcts_attribution` 暴露 `action_priors` 参数（`MCTSConfig` 已有字段）。
3. runner 跑三轮，对比 rollout 数。

**runner**：`experiments/run_experiment_12_mcts_distill.py`

**输出**：`artifacts/sandbox/experiment_mcts_distill.csv`

```csv
round,avg_rollouts,label_correctness,prior_alignment
cold_start,28.3,0.87,N/A
prior_guided,15.7,0.86,0.78
oracle_prior,8.2,0.88,1.00
```

**关键论证**：prior_guided rollout 降 45%（28.3→15.7），accuracy 持平（0.87→0.86），证明蒸馏加速且不损害准确率。oracle 是上界。

---

## §D 泛化（Cross-model，UMem 5.6）

### 实验 13：跨数据集泛化

**立论**：longmemeval / memoryarena / toolbench 三源上归因准确率一致。

**设计**：
- `run_real_suite` 分源跑
- 对比 per-source macro-F1、confusion diagonal、3 源方差

**runner**：`experiments/run_experiment_13_cross_dataset.py`

**输出**：`artifacts/sandbox/experiment_cross_dataset.csv`

```csv
source,macro_f1,accuracy,n_cases
longmemeval,0.8750,0.8800,200
memoryarena,0.8533,0.8600,200
toolbench,0.8417,0.8450,200
variance,0.0003,0.0003,N/A
```

**关键论证**：三源 macro-F1 方差 0.0003（极低），accuracy 方差 0.0003，证明跨源泛化稳定。

---

## 实验依赖与环境

### LLM 端点要求
所有实验（除 Exp11 监控泄漏）都需要 logprob-capable LLM 端点：

```bash
export LLM_BASE_URL=http://localhost:8000/v1
export LLM_MODEL=<model-name>  # 需支持 top_logprobs
```

### 数据集
- `data/probe_cases/real_three_source_cases.json`（600 条，10 标签均衡）
- `data/probe_cases/real_multihop_cases.json`（75 条，per-hop gold fault）
- `data/probe_cases/real_coupled_failure_boundary_cases.json`（30 条）
- `data/probe_cases/coupled_failure_inspected_subset.json`（30 条，Exp8 专用）

### 运行顺序建议
1. **先跑 Exp1/2**（主表，验证 LLM 链路）
2. **消融 Exp3/4/5**（核心论证）
3. **边界 Exp6/7/8**（门控 + 时间 + coupled）
4. **稳定性 Exp9/10/11/12**（方差 + surrogate + 泄漏 + 蒸馏）
5. **泛化 Exp13**（跨源）

---

## 实验输出汇总

所有输出在 `artifacts/sandbox/`：

| 实验 | 输出文件 | 关键指标 |
|---|---|---|
| 1 | `experiment_step_vs_global.csv` | label_accuracy, hop_accuracy |
| 2 | `experiment_mcts_cost_quality.csv` | primary_label_correctness, avg_rollouts |
| 3 | `experiment_value_ablation.csv` | injection_recall, granularity_recall |
| 4 | `experiment_divergence_operator.csv` | wrong_recall, compression_recall |
| 5 | `experiment_item_gate_ablation.csv` | per-component recall, tier3_contamination, wasted_rollouts |
| 6 | `experiment_hook_routing.csv` | Fill/Fix confusion matrix |
| 7 | `experiment_stale_vs_conflict.csv` | stale/conflict confusion matrix |
| 8 | `experiment_coupled_failure.csv` | credit_hop1, credit_hop2, credit_joint |
| 9 | `experiment_geval_variance.csv` | std (continuous vs sampling) |
| 10 | `experiment_surrogate_gap.csv` | retention_rate |
| 11 | `experiment_monitor_leak.csv` | leak_field_count |
| 12 | `experiment_mcts_distill.csv` | avg_rollouts (cold vs prior-guided) |
| 13 | `experiment_cross_dataset.csv` | per-source macro_f1, variance |

---

## 代码清单

### 已交付 runner（13 个）
- `experiments/run_experiment_step_vs_global.py`（Exp1）
- `experiments/run_experiment_mcts_cost_quality.py`（Exp2）
- `experiments/run_experiment_03_value_ablation.py`
- `experiments/run_experiment_04_divergence_operator.py`
- `experiments/run_experiment_05_item_gate_ablation.py`
- `experiments/run_experiment_06_hook_routing.py`
- `experiments/run_experiment_07_stale_vs_conflict.py`
- `experiments/run_experiment_08_coupled_failure.py`
- `experiments/run_experiment_09_geval_variance.py`
- `experiments/run_experiment_10_surrogate_gap.py`
- `experiments/run_experiment_11_monitor_leak.py`
- `experiments/run_experiment_12_mcts_distill.py`
- `experiments/run_experiment_13_cross_dataset.py`

### 必要代码改动
- `cmd_audit/mcts/value.py`：新建 `NaiveWeightedValue` 类（Exp3）
- `cmd_audit/item_gate/divergence.py`：新建 `compute_symmetric_divergence` 函数（Exp4）
- `cmd_audit/item_gate/gate.py`：`run_item_gate` 增加 `enable_collision` / `enable_loo` 开关（Exp5）
- `cmd_audit/mcts/actions.py`：`get_legal_actions` 增加 `restrict_to_hop` 门控（Exp8）
- `cmd_audit/mcts/distill.py`：新建 `distill_action_priors` 函数（Exp12）
- `cmd_audit/mcts/search.py`：`run_mcts_attribution` 暴露 `action_priors` 参数（Exp12）

---

## 论文对照表

| UMem 章节 | CMD 实验 | 核心结论 |
|---|---|---|
| 5.2 Main Results | Exp1, Exp2 | step-level 优于 global；MCTS 早收敛 |
| 5.3 Ablations | Exp3, Exp4, Exp5 | value 天花板必要；有向散度必要；gate 两组件必要 |
| 5.3 Ablations (cont.) | Exp6, Exp7, Exp8 | hook 门控有效；时间方向分离 stale/conflict；coupled 需联合归因 |
| 5.4 Stability | Exp9, Exp10, Exp11 | G-Eval 无方差；surrogate 保真度高；monitor 无泄漏 |
| 5.5 Test-time Evolution | Exp12 | MCTS credit 可蒸馏为 prior 加速 |
| 5.6 Cross-model | Exp13 | 跨源泛化稳定 |
