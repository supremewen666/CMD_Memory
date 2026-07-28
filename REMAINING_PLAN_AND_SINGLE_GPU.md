# 剩余工作盘点 + 单卡 A100 重排（2026-07-27）

本文件回答两件事：(1) 相对最终计划还缺什么（含你未列出的项）；(2) 原本按 4 卡设计的实验如何在 1 张 A100 上跑完。

依据：逐文件核实 `AUDIT_DONE_UNDONE.md` / `IMPROVEMENT_SPEC_A|B` / `EXPERIMENT.md` claim 链 + 实测 `data/probe_cases/` 与 `artifacts/sandbox/` 的行数比对 + runner 并发结构核实。

---

## 第 0 部分：两个未列出的发现（优先级高于你列的四项）

### 0.1 【阻断级】C7 与 C10 的头条数字，其数据集不在仓库里

实测 case_id 集合比对：

| 工件 | 行数 | 声称数据源 | 磁盘实际 | case_id 重叠 |
|---|---|---|---|---|
| `failure_memory_trajectory_detail.csv`（Exp18 → C7） | 600 | `real_recurrent_cases.json`（EXPERIMENT.md 称 600） | **120**（24 family × 5 variant） | **0** |
| `item_operator_headroom_single.csv`（Exp23a → C10） | 1200（stale 600 + conflict 600） | `real_item_layer_cases.json` | **210**（5 label × 42） | **0** |
| `operator_headroom_detail.csv`（Exp21 → C9） | 115 | `real_multihop_cases.json` | 240 | **115 ✓** |

即：**C9（算子进化主线）可复现；C7 和 C10 的头条数字对应的数据集已被覆盖**。`build_probe_cases.py` 默认值是
`recurrent_families_per_source=8`（→ 8×3×5 = 120）、item 层 210，与 `dataset_expansion_spec.md` §2 推荐的
`recurrent_families_per_source=40`（→ 600）不一致。可推断：跑完 Exp18/Exp23 后有人用默认参数重建了 probe cases，
把 600/1200 规模的集合覆盖成了 120/210。

后果：审稿人拿到复现包，跑 Exp18 得到的 n 与论文的 600 对不上；Exp23 得到 210 而非 1200。这不是"待做实验"，
是**已有结论的证据链断裂**。

修复（廉价，纯参数，无新代码）：
```bash
python -m experiments.build_probe_cases \
  --target-per-source 50 --multihop-per-source 80 \
  --recurrent-families-per-source 40 --recurrent-variants-per-family 5
```
然后按 `dataset_expansion_spec.md` §5 的 6 条 gate 校验，并**在 git 里锁住 case_id 清单 + sha256**，
避免再次静默覆盖。注意：重建后 case_id 大概率仍与旧工件不同（哈希后缀依赖 raw 行选取），
所以 Exp18/Exp23 **需要重跑**，不能只补数据集。这笔账要计入下面的算力预算。

### 0.2 【定性级】MemTrace 有两篇同名论文，其中一篇是 CMD 的正面竞品

你说"更新数据集 MemFail 和 MemTrace"——需要先分清楚，这三者定位完全不同：

| 来源 | 身份 | 对 CMD 的意义 |
|---|---|---|
| **MemFail**（arXiv 2605.26667，Garg/Song/Zhao，UC Berkeley；HF `ishirgarg/MemFail`） | 诊断性 **benchmark**，5 数据集 4 任务，按 summarization/storage/retrieval 三操作切故障 | **数据源**。它的 Long-Hop / Coexisting-Facts / Conditional-Facts 与 CMD 的 4 step action 有天然映射，是理想的第三方 case 来源 |
| **MemTrace-A**（arXiv 2605.28732，zjunlp；`MemTraceBench` MIT 许可） | **执行图归因方法 + benchmark**，160 人工标注故障案例，且**做了闭环修复**（归因信号 → prompt 优化 → 端任务 +7.62%） | **正面竞品，威胁最大**。它和 CMD 同域（记忆系统）、同任务（故障归因）、同闭环（归因→修复→涨点）。CMD 的"记忆域无人做"护城河（见 `project_submission_positioning_competitors` 记忆）**已被这篇占掉一部分** |
| **MemTrace-B**（arXiv 2606.17328，Long/Tang，MSU） | 以 knowledge point 为单位的**评测协议**，核心结论："瓶颈是 evidence use 而非 retrieval，失败时证据可达的概率是缺失的 10 倍" | **叙事外援**。这句话正是 CMD 隐性故障假设（内容在 recall 里但模型用不上）的独立第三方佐证，应直接引用支撑 C1 |

行动含义（这条改变定位，不只是加数据）：
- **必须做 MemTrace-A 的正面对照**，而不是只在 related work 里提一句。二者可直接比较的点：CMD 用**反事实
  recovery** 做 gold-free 归因，MemTrace-A 用**执行图 + LLM 迭代追溯**做归因且需要人工标注的 faulty operation。
  CMD 的差异化主张应收缩为："**无需执行图插桩、无需人工故障标注的 gold-free 修复搜索**"——这是真差异，
  而"记忆域首创"已经不能讲了。
- MemTrace-A 的 ETA/OIA 指标（error type accuracy / operation identification accuracy）是 **label 预测口径**，
  与 CMD 的 recovery-gain 口径不可直接比。要做 head-to-head，只能在**共同的下游指标**上比：
  他们报"prompt 优化后端任务 +7.62%"，CMD 报 recovery gain——这是唯一可对齐的轴。
- MemTrace-B 的 "evidence use ≫ retrieval" 结论建议进 intro 第一段当动机引用。

---

## 第 1 部分：完整缺口清单

按"是否阻断投稿"分三级。你列出的四项标注为 ★。

### P0 — 阻断级（不做则结论不成立或不可复现）

| # | 项 | 现状 | 出处 |
|---|---|---|---|
| P0-1 | **数据集断链修复 + Exp18/Exp23 重跑** | 见 §0.1 | 本次审计新发现 |
| P0-2 | **A1：rollout 超时静默计为 recovery_gain=0.0** | `counterfactual/rollout.py` L115/120/188 catch-all。污染一切 recovery 数字；已实测残差上跨跑翻转 37% | Spec A §1 |
| P0-3 | **A2：Exp24 online operator trajectory（总闸门）** | runner 不存在。"逐代进化"目前是 untested 而非 refuted；它裁决 item 层进不进正文 | Spec A §2 ★（你说的"验证算子可进化"） |
| P0-4 | **A3：judge/answerer 客户端拆分** | 全库单 `LLMClient`，换端点 = 整栈换。judge 不冻结则跨臂不可比 + 自评循环 | Spec A §3 ★（你说的"更新模型"的前置） |
| P0-5 | **≥3 runs churn 复测** | 残差级 churn 37%，单 run 结论不可下 | 脚本 lane 0 已备 |

### P1 — 投稿硬要求（不做则被审稿人直接质疑）

| # | 项 | 现状 | 出处 |
|---|---|---|---|
| P1-1 | **MemTrace-A 正面横向对比** | 未做，且定位需重写 | §0.2 ★（你说的"竞品横向对比"） |
| P1-2 | **MemFail 数据接入** | 未做。需新 adapter（它的 API 是 store_conversation/retrieve_memories/get_all_memories） | ★（你说的"更新数据集"） |
| P1-3 | **多模型正式数字（≥2 answering，judge 冻结）** | 脚本已备但标 PROVISIONAL，依赖 P0-4 | ★ |
| P1-4 | **G-Eval 人工一致性小样（50–100 case，报 Spearman/Kappa）** | 未动。全套打分建在 G-Eval 上，没有人工锚点 = 循环论证 | Spec B §6.3 |
| P1-5 | **STALE 口径对齐** | 现 ~65% 是 recovery-gain，STALE 论文 55.2% 是自然准确率，**不可比**。需补"无注入自然基线"重打 | EXPERIMENT.md C10 |
| P1-6 | **`SUBMISSION_TASK_SPEC.md` 重建** | 已丢失 | Spec B §6.2 |
| P1-7 | **工件 checksum + 命令日志** | 未做。§0.1 的事故正是因为没有这个 | Spec B §6.1 |

### P2 — 加分 / 可降级（camera-ready 前补）

| # | 项 | 出处 |
|---|---|---|
| P2-1 | A4 库治理五件套（准入回放/账本退休/active-cap/hash 去重/CI-gated Δk） | Spec A §4 |
| P2-2 | A5 Exp25 durability（read-time vs write-back） | Spec A §5 |
| P2-3 | B1 `repair-store` CLI 六件胶水 + B7 demo artifact | Spec B §1/§7 |
| P2-4 | B2 freshness arbitration 确定性算子（零 LLM 成本，文献 +10.8pp） | Spec B §2 |
| P2-5 | B3 hybrid fingerprint（排除/坐实 Exp23b ns） | Spec B §3 |
| P2-6 | B4 RETIRE 收尾（`replays/` 10 处 import、`get_repair_guidance` 旧键、`--confusion-out`） | Spec B §4 |
| P2-7 | B5 STALE 反向 adapter（"挂最佳系统涨点"） | Spec B §5 |
| P2-8 | C1 塌缩图 | Spec B §6 |

---

## 第 2 部分：单卡 A100 重排

### 2.1 关键认识：瓶颈从来不是卡数

核实 `run_remaining_experiments.sh`：所谓"4 卡"**不是张量并行训练**，而是
**每张卡起一个 vLLM 端点 = 一条 lane，用来并行跑不同 answering 模型的臂**。CMD 全程零训练、零反向传播，
只有推理。所以卡数只影响**吞吐**，不影响任何实验的可行性。

更关键的实测结果——**所有 runner 都是串行 for 循环**：

```
run_experiment_21_operator_headroom.py:168:  for i, case in enumerate(residual, start=1):
run_experiment_22_operator_transfer.py:212:  for i, case in enumerate(residual, start=1):
run_experiment_18_failure_memory_trajectory.py:71:  for case_index, case in enumerate(cases, start=1):
run_experiment_14_repair_efficacy.py:237:    for i, case in enumerate(cases):
probe_exhaustive.py:102:                     for i, case in enumerate(cases):
run_experiment_23_item_headroom.py:220:      for i, case in enumerate(all_cases, start=1):
```

且 `build_answer_verifier` 里 `del max_workers`，`AnswerRubricScorer` 是单线程。
**即当前实现同一时刻只向端点发 1 个请求。** 一张 A100 跑 vLLM 的连续批处理能吃 32–64 并发，
现在只喂 1 —— GPU 利用率极低。

**结论：把 case 级并发从 1 提到 32，单张 A100 的有效吞吐就超过原来 4 张卡各跑串行 lane 的总和。**
不需要削减任何实验。

### 2.2 成本模型（实测代码结构推导）

每次算子评估 = `d` 次 `_step_context`（每次 1 次 LLM prefix 重生成）+ 1 次终局作答 + 1 次 G-Eval 打分。
`d = max_depth ≤ 3`，故 ≈ **5 次 LLM 调用/算子评估**。

Exp21 每 case 的算子评估数（`d=3, A=4 动作, 3 个可参数化 item`）：

```
single       = d × A            = 12
double       = C(3,2) × A²      = 48
param        = items × 2        =  6
double_param = items × 2        =  6
                          合计   = 72 次评估 = 360 次 LLM 调用/case
```

| 实验 | n | 调用/case | 单 run 总调用 |
|---|---|---|---|
| Exp21 operator headroom | 115 残差 | 360 | 41,400 |
| Exp22 operator transfer | 115 | ~150 | 17,250 |
| Exp24 operator trajectory（新） | 240 | ~85 | 20,400 |
| Exp25 durability（新） | 120×3 臂 | ~30 | 10,800 |
| Exp18 重跑（600 重建后） | 600 | ~60 | 36,000 |
| Exp23a/b 重跑（1200 重建后） | 1200 | ~150 | 180,000 |

全套 ×3 runs + 2 个 answering 模型 ≈ **60–70 万次调用**。

### 2.3 吞吐对照（Qwen2.5-7B，1×A100 80GB，vLLM，~1.5k in / 128 out）

| 并发 | 调用/秒 | 调用/小时 | Exp21 单 run 耗时 |
|---|---|---|---|
| **1（当前）** | ~0.7 | ~2,500 | **~16.5 小时** |
| 8 | ~3 | ~11,000 | ~3.8 小时 |
| **32（建议）** | ~10 | ~36,000 | **~1.2 小时** |
| 64 | ~15 | ~54,000 | ~0.8 小时 |

70 万次调用 @ 36,000/小时 ≈ **20 小时纯 GPU 时间**。一张 A100 一个周末跑完全套 ×3 runs + 双模型。

### 2.4 具体改造（按性价比排序）

**改造 1：case 级线程池（最高杠杆，~15 行/runner，20× 加速）**

每个 runner 的 `for case in ...` 换成：

```python
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=int(os.environ.get("CMD_CASE_WORKERS", "16"))) as pool:
    results = list(pool.map(evaluate_one_case, cases))
```

注意事项：
- case 之间**必须无共享可变状态**才能这样改。Exp21/22/23 是纯 LOO，安全。
- **Exp18/Exp24 不能这样改** —— 它们测的是"库随流增厚"的在线轨迹，case 顺序是实验语义本身。
  这两个只能在**算子评估层**并发（同一 case 内的 top-K 候选并行），或跨 seed 并行（3 个 run 同时跑）。
- 输出写入要加锁或改为收集后统一写。

**改造 2：同卡双模型（解锁 A3，无需第二张卡）**

7B fp16 权重 ≈ 14GB。A100 80GB 可同时驻留 judge + answerer 两个 7B：

```bash
# judge（冻结，全程不变，必须支持 top_logprobs）
CUDA_VISIBLE_DEVICES=0 vllm serve Qwen/Qwen2.5-7B-Instruct \
  --port 8000 --gpu-memory-utilization 0.42 --max-model-len 8192

# answerer（可换模型的那个）
CUDA_VISIBLE_DEVICES=0 vllm serve meta-llama/Llama-3.1-8B-Instruct \
  --port 8001 --gpu-memory-utilization 0.42 --max-model-len 8192
```

配合 Spec A §3 的角色化 env（`LLM_JUDGE_BASE_URL` / `LLM_BASE_URL`），
**多模型 lane 从"并行 4 卡"变成"串行换 answerer 端点"**——只花时间，不花卡。

若是 **A100 40GB**：改用 AWQ/GPTQ 4-bit（7B ≈ 5GB），双模型 + KV cache 仍宽裕；
或单模型驻留、两个 answering 模型**分时跑**（judge 常驻，answerer 轮换）。

**改造 3：prefix 复用（省 27% 调用，可选）**

实测 Exp21 的 72 条算子链共享大量前缀：`_step_context` 调用去重后 216 → 120（省 44%），
整体调用 360 → 264（省 27%）。实现方式是对 `(父上下文哈希, action, gp)` 做 memo。
**但代码里目前没有任何缓存层**（已 grep 确认无 lru_cache / diskcache / sqlite）。
这条属于锦上添花，改造 1 做完后收益相对不大，建议排在最后，且要注意别破坏
"跨跑非确定性"的诚实记录（缓存会人为消除 churn，反而掩盖 §P0-2 的问题）。

**改造 4：vLLM 服务端参数**

```bash
--max-num-seqs 64            # 允许的并发序列数，配合改造 1
--enable-prefix-caching      # 自动前缀缓存，与改造 3 部分重叠且零代码改动
--max-model-len 8192         # 别开太大，KV cache 换并发
```
`--enable-prefix-caching` 是**零代码版的改造 3**，建议先开这个，测完再决定要不要写应用层 memo。

### 2.5 单卡执行顺序（判决树驱动，非全量跑）

不要一次跑全套。按判决树串行，每步的结果决定下一步：

```
第 1 步（~1 天，无 GPU）：P0-1 数据集重建 + checksum 锁 + P0-2 超时修复 + 单测
第 2 步（~2 小时 GPU）：Exp21 --limit 20 冒烟，确认改造 1 未破坏数字
第 3 步（~4 小时 GPU）：P0-4 judge/answerer 拆分 + 双端点同卡冒烟
第 4 步（~6 小时 GPU）：建 Exp24 runner，×3 runs        ← 总闸门
        ├─ recovery 爬升 → step-only 叙事，item 层留附录（省掉 Exp23 全部重跑 = 省 18 万次调用）
        └─ recovery 平   → item 层转正文，必须重跑 Exp23a/b ×3（+18 万次调用 ≈ +5 小时）
第 5 步（~8 小时 GPU）：Exp21/22 ×3 churn 复测 + Exp18 重跑 ×3
第 6 步（~6 小时 GPU）：多模型第二 answerer 复测 C4 / Exp22
第 7 步（无 GPU）：P1-4 人工一致性小样、P1-1 MemTrace 对比撰写、P1-6 spec 重建
```

**Exp24 先跑是关键的省算力决策**：它判平才需要 Exp23 重跑（最贵的一项，18 万次调用）。
先跑总闸门可能直接省掉全套 item 层重跑。

### 2.6 需要修改的文件

| 文件 | 改动 |
|---|---|
| `run_remaining_experiments.sh` | lane 模型从"每 GPU 一 lane 并行"改为"单卡串行换 answerer 端点"；`CMD_ENDPOINTS` 语义改为分时而非并行 |
| `experiments/experiment_runner_common.py` | 加 `build_clients() -> (answer_client, judge_client)`（Spec A §3）；`max_workers` 不再 `del` |
| `experiments/run_experiment_{21,22,23}_*.py` | case 级 ThreadPoolExecutor（安全，纯 LOO） |
| `experiments/run_experiment_{18,24}_*.py` | **不做 case 级并发**（在线轨迹语义）；改为跨 seed 并行 |
| `cmd_audit/core/llm_client.py` | 角色化 env 读取 + `LLMTimeoutError` 上抛（P0-2） |
| `cmd_audit/counterfactual/rollout.py` | 三处 catch-all 区分 timeout / error，不计入 recovery 分母（P0-2） |
| `cmd_audit/eval/writers.py` | recovery 表加 `timeout_count` 列 |

---

## 附：你列的四项 → 本盘点的对应

| 你的说法 | 对应 | 备注 |
|---|---|---|
| 更新数据集 MemFail / MemTrace | P1-2（MemFail 作数据源）+ P1-1（MemTrace 作竞品） | **MemTrace 不是数据集，是竞品方法**；且有两篇同名论文 |
| 更新模型 | P1-3，前置 P0-4 | 单卡可做，同卡双端点 |
| 验证算子可进化 | P0-3（Exp24，总闸门） | 最高优先，且先跑能省算力 |
| 竞品横向对比 | P1-1 | 需要重写定位，不只是加一张表 |
| （未列出） | **P0-1 数据集断链** | 阻断级，本次审计新发现 |
| （未列出） | **P0-2 超时静默归零** | 阻断级，污染一切 recovery 数字 |
| （未列出） | P1-4 G-Eval 人工一致性 | 投稿硬要求，无人工锚点 = 循环论证 |
