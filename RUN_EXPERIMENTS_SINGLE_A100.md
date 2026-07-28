# 单卡 A100 实验执行手册（2026-07-27）

配套 `REMAINING_PLAN_AND_SINGLE_GPU.md`（缺口盘点与算力推算）。
本文件只给**可直接粘贴执行**的命令，按判决树排序 —— 每一步的结果决定下一步是否需要跑。

前提：代码层已完成（317 测试通过）。判决树驱动，**不要一次跑全套**。

---

## 阶段 0：环境（一次性，~10 分钟）

### 0.1 起 vLLM 双端点（同一张卡）

7B fp16 权重约 14GB，A100 80GB 可同驻 judge + answerer 两个端点。
**judge 全程冻结不换**，只换 answerer —— 这是跨臂可比的前提（SPEC_A §3）。

```bash
# judge（冻结；必须支持 top_logprobs，G-Eval 依赖它）
CUDA_VISIBLE_DEVICES=0 nohup vllm serve Qwen/Qwen2.5-7B-Instruct \
  --port 8000 --gpu-memory-utilization 0.42 --max-model-len 8192 \
  --max-num-seqs 64 --enable-prefix-caching \
  > /tmp/vllm_judge.log 2>&1 &

# answerer（可换的那个）
CUDA_VISIBLE_DEVICES=0 nohup vllm serve Qwen/Qwen2.5-7B-Instruct \
  --port 8001 --gpu-memory-utilization 0.42 --max-model-len 8192 \
  --max-num-seqs 64 --enable-prefix-caching \
  > /tmp/vllm_answer.log 2>&1 &

# 等两个端点就绪
until curl -s localhost:8000/v1/models >/dev/null && curl -s localhost:8001/v1/models >/dev/null; do sleep 5; done
echo "both endpoints up"
```

40GB 卡：改用 AWQ/GPTQ 4-bit 权重（7B≈5GB），双端点 + KV cache 仍宽裕。

`--enable-prefix-caching` 是**零代码版的前缀复用**，Exp21 的 72 条算子链共享大量前缀，实测去重可省 27% 调用。

### 0.2 环境变量

```bash
export LLM_BASE_URL=http://localhost:8001/v1      # answerer
export LLM_MODEL=qwen2.5-7b-instruct
export LLM_JUDGE_BASE_URL=http://localhost:8000/v1  # judge（冻结）
export LLM_JUDGE_MODEL=qwen2.5-7b-instruct
export LLM_API_KEY=dummy
export LLM_JUDGE_API_KEY=dummy
export LLM_TIMEOUT=120
export NO_PROXY=localhost,127.0.0.1
export no_proxy=localhost,127.0.0.1
```

`LLM_TIMEOUT=120` 必须设。超时现在记为 NaN 并排除（不再静默算 0），但仍要给足时间。

### 0.3 G0 门：judge 端点必须能返回可解析 logprobs

```bash
python - <<'PY'
import sys; sys.path.insert(0, ".")
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from experiments.experiment_runner_common import assert_g_eval_available
assert_g_eval_available(LLMClient(LLMClientConfig.for_role("judge")), role="preflight-judge")
print("G0 judge logprob gate: OK")
PY
```

**不过门就停**。整套打分建在 G-Eval 上，judge 端点不返回 logprobs 则所有数字无效。

### 0.4 冒烟（确认端点接得通，~5 分钟）

```bash
python -m experiments.probe_exhaustive --limit 3
```

---

## 阶段 1：Exp24 总闸门（最高优先，~6 小时）

**先跑这个，因为它能省掉最贵的一项。** 判"爬升"→ step-only 叙事，item 层留附录，
直接免掉 Exp23 全套重跑（约 18 万次 LLM 调用）；判"平"→ item 层转正文，才需要重跑。

### 1.1 冒烟

```bash
python -m experiments.run_experiment_24_operator_trajectory \
  --limit 8 --fallback-classes single
```

### 1.2 正式跑 ×3 seeds（churn 37%，单 run 不可下结论）

```bash
mkdir -p artifacts/exp_runs/exp24
for s in 24 124 224; do
  python -m experiments.run_experiment_24_operator_trajectory \
    --seed $s --bin-size 15 --topn 5 --fallback-classes all \
    --controls on \
    --out artifacts/exp_runs/exp24/operator_trajectory_seed${s}.csv \
    2>&1 | tee artifacts/exp_runs/exp24/exp24_seed${s}.log
done
```

`--controls on`（默认）跑两个对照臂：`fixed-library`（暖机后冻结不生长）和
`random-variation`（同实测预算、随机抽形状、排除本 case 自身形状）。

**为什么随机臂必须有**：Exp22 实测 fp_topN **70/115 (0.609)** vs random **52/115 (0.452)**，
配对差异 26/8，p=2.9e-3 —— 指纹显著胜出，且中位成本 2 vs 5。所以"形状随便选也行"不成立，
这个对照有真实区分力。（注意 oracle 是 62/115，弱于 fp_topN，因为它只迁移单个形状；
把随机说成"0.84×oracle"会高估它，对真正活臂的比是 52/70 = 0.74。）

**退化护栏**：Exp24 的库从空生长，所以流早期 `预算 ≥ 池大小`，随机臂实际在**穷举整个库**——
穷举强于任何 top-N 排序，那不是同强度对照。detail CSV 记录 `random_pool_size` /
`random_coverage`，分析器自动剔除 `coverage=1.0` 的 case 并报告剔除数。
不剔除会让对照的区分力沿流单调上升，把**池增长**伪装成**活臂逐代拉开差距**。
实测这个护栏能翻转结论：关闭时 `p=0.4545 not significant`，打开时 `p=0.0312 live wins`。

**第三个臂 `random-order`**（同候选集、打乱顺序）：分离"选对子集"与"排对顺序"两个能力，
且**天然免疫覆盖率退化**（候选集与活臂完全相同）。注意它的性质——
两臂遍历同一集合且都"首个过阈即停"，所以**恢复率恒等 by construction**，
差异只在第几次找到。因此它按**成本轴**用精确符号检验比较 `library_rank` vs
`random_order_rank`，绝不报成恢复率胜出（那是同义反复）。
实现上复用缓存全集得分，所以这个臂**零额外 LLM 开销**。

流的顺序就是实验语义，**不要并行、不要改顺序**。三个 seed 之间可以串行跑。

### 1.3 判决

```bash
python -m experiments.analyze_significance
```

判据（写死在 EXPERIMENT.md，不许事后挪门）：

| 结果 | 含义 | 下一步 |
|---|---|---|
| 趋势 p<0.05 **且**末代显著高于首代 **且**配对 McNemar 胜过 fixed-library 和 random-variation **两个**对照 | 逐代进化成立 | step-only 叙事；**跳过阶段 4**（省 18 万调用） |
| 平（与 Exp18 同型） | 进化叙事收缩为 reuse/efficiency | 必须跑阶段 4，item 层转正文 |
| recovery 平但**成本下降** | "暖机后廉价复用" | 这是可发表的特定结论，不是 null |

分析器会直接打印判据结论，包括：
- 分代 Cochran-Armitage 趋势 + 首末代 Wilson 区间与两比例 z
- 库厚度-recovery 相关系数
- 成本轴（首末代平均 rollout）
- **对每个对照臂的配对 McNemar**（同一批 case，故用配对而非非配对率，免疫 37% churn）
- 对照臂未跑时显示 `UNAVAILABLE`，**绝不**把空白当成"对照得零分"

**三个 seed 必须同向**才算结论；seed 少于 3 时分析器打印 `NOT SETTLED`。

---

## 阶段 2：churn 复测（~8 小时）

```bash
export CMD_ENDPOINTS="http://localhost:8001/v1"
export CMD_MODELS="qwen2.5-7b-instruct"
export CMD_RUNS=3
CMD_ONLY="exp21,exp22" ./run_remaining_experiments.sh --wait
```

跑批脚本已自动导出 `LLM_JUDGE_*` 钉住冻结 judge，产出 CSV 带 judge 身份 provenance。

---

## 阶段 3：外部有效性臂（新增，~4 小时）

这是挡"是不是只有你自己造的故障才修得好"这一刀的关键。近 4000 个第三方 case。

### 3.1 构建 MemFail cases（692 条，无需 GPU）

先取 CSV（HuggingFace 与 raw.githubusercontent 从本机不通，走 GitHub API）：

```bash
mkdir -p data/raw_cases/memfail
python - <<'PY'
import base64, json, urllib.request, pathlib
BLOBS = {
  "long_hop_chains.csv": "f0cecf362120ef53a68a9c4370cac1cb7c53f0c6",
  "coexisting_facts_dataset.csv": "9546e0556cda9d22fbdef65bf70b5e8b886d6691",
  "conditional_facts_dataset_easy.csv": "720461946af4a696574190c022d5b2a35c36a97c",
  "persona_dataset.csv": "0aa9f4bdfcf46b648fde72cc4448ce49a8c8d6c4",
}
out = pathlib.Path("data/raw_cases/memfail"); out.mkdir(parents=True, exist_ok=True)
for name, sha in BLOBS.items():
    url = f"https://api.github.com/repos/ishirgarg/MemFail/git/blobs/{sha}"
    with urllib.request.urlopen(url, timeout=60) as r:
        data = json.load(r)
    (out / name).write_bytes(base64.b64decode(data["content"]))
    print("ok", name, (out / name).stat().st_size, "bytes")
PY

python -m experiments.build_memfail_cases --csv-dir data/raw_cases/memfail
```

conditional_hard 的 sha 需从 contents API 取（见 `build_memfail_cases.py --help`）。

### 3.2 构建 MemTrace-B cases（2047 条，无需 GPU）

MemTrace-B（arXiv 2606.17328）**无公开数据发布**，是从 HaluMem-Medium 派生的协议。
本套是**协议复现**（同上游数据重新实现其协议），不是复制其 artifact —— 论文里必须这样写。

HaluMem 源数据若已在 `data/raw_cases/halumem_stage4_1_events2memories.jsonl` 则直接用：

```bash
python -m experiments.build_memtrace_kp_cases \
  --users 20 --checkpoints 8 --kps-per-user 6
```

### 3.3 在外部数据上跑修复

```bash
# MemFail：step 层四标签
python -m experiments.run_experiment_14_repair_efficacy \
  --cases data/probe_cases/memfail_cases.json \
  --cmd-attribution exhaustive --limit 0

# MemTrace-B：step 层 1002 条（539 条 label=None 是天然负对照，
# 现有 runner 按 PIPELINE_STEP_ACTIONS 自动过滤）
python -m experiments.run_experiment_14_repair_efficacy \
  --cases data/probe_cases/memtrace_kp_cases.json \
  --cmd-attribution exhaustive --limit 0
```

**分维度分析必须 join 边车 CSV**：`ProbeCase` 是 frozen dataclass 无扩展字段，
memory age / question type / evidence condition 三维坐标存在
`data/probe_cases/memtrace_kp_cases_dimensions.csv`，只读 JSON 拿不到。

---

## 阶段 3.5：Exp25 durability（部署侧独立 claim，~3 小时）

read-time（每次召回都修，store 不变）vs write-back（首修成功后写回，后续同家族直接读修后 store）
vs no-repair 对照。数据现成：`real_recurrent_cases.json` 600 条 = 120 family × 5 variant。

```bash
python -m experiments.run_experiment_25_repair_durability \
  --out artifacts/exp_runs/exp25/repair_durability_detail.csv
```

三个必看数字：
- **relapse rate** —— 首修成功后，同家族后续 variant 仍失败的比例（严格按 family 内计算）
- **摊销成本** —— 按 `variant_index` 的 rollout 数；write-back 臂应逐 variant 下降
- **net regression** —— write-back 对**其他家族**的净影响。必须报 net：
  一个修好自己家族却拖坏别人的 write-back 是净亏，只看 repair-on-failure 看不出来

与判决树解耦，可与阶段 1–3 任意顺序。

---

## 阶段 4：item 层重跑（**仅当阶段 1 判平**，~5 小时）

```bash
CMD_ONLY="exp23a,exp23b" CMD_RUNS=3 ./run_remaining_experiments.sh --wait
```

STALE 数据在远程主机，本机不需重建。

---

## 阶段 5：多模型复测（~6 小时）

换 answerer，**judge 端口 8000 不动**：

```bash
CUDA_VISIBLE_DEVICES=0 nohup vllm serve meta-llama/Llama-3.1-8B-Instruct \
  --port 8002 --gpu-memory-utilization 0.35 --max-model-len 8192 \
  --max-num-seqs 64 --enable-prefix-caching > /tmp/vllm_answer2.log 2>&1 &

export LLM_BASE_URL=http://localhost:8002/v1
export LLM_MODEL=llama-3.1-8b-instruct
# LLM_JUDGE_* 保持不变 —— 这正是拆分的意义

CMD_ONLY="exp14,exp21,exp22" CMD_RUNS=3 ./run_remaining_experiments.sh --wait
```

同卡三端点若显存吃紧，先停 8001。

---

## 阶段 6：无 GPU 收尾

- G-Eval 人工一致性小样（50–100 case，报 Spearman/Kappa）—— 投稿硬要求，
  没有人工锚点等于循环论证
- MemTrace-A（arXiv 2605.28732，zjunlp）正面对比 —— 唯一可对齐的轴是**下游端任务增益**
  （他们 +7.62% vs CMD 的 recovery gain）；他们的 ETA/OIA 是 label 预测口径，不可直接比
- STALE 口径对齐：补"无注入自然基线绝对准确率"才能与其 55.2% head-to-head
- `SUBMISSION_TASK_SPEC.md` 重建
- `logs/` 目录恢复（`git checkout logs/`；多份 spec 引用它当决策依据）

---

## 已解决：recurrent 断链（2026-07-27）

`real_recurrent_cases.json` 曾只有 120 条而 Exp18 工件是 600 条。已用
`--only recurrent` 选择性重建修复：

```bash
python -m experiments.build_probe_cases --only recurrent \
  --target-per-source 50 --multihop-per-source 80 \
  --recurrent-families-per-source 40 --recurrent-variants-per-family 5
```

结果：**600 条 / 120 family × 5 variant / 四标签各 150 / 0 重复 / 0 graph_error**，
且与 Exp18 工件 **600/600 case_id 完全重叠** —— 所以 **Exp18 无需重跑**。

原因是 `case_id = hash(query + label + bridge_key)` 不含索引，而 `_select_rows` 是确定性
top-N，上游参数（`target=50`/`multihop=80`/`coupled=10`）不变时取的是同一批 raw 行，
扩大 family 数是纯追加。

主线四文件（multihop / item_layer / three_source / coupled）实测 md5 前后不变，
C8/C9 证据链完好（Exp21 的 115 条残差 id 仍 115/115 命中）。

`data/probe_cases/CHECKSUMS.sha256` 已锁 11 个文件 —— 以后静默覆盖会被 checksum 抓到。

---

## 吞吐参考（Qwen2.5-7B，1×A100 80GB）

| 并发 | 调用/小时 | Exp21 单 run |
|---|---|---|
| 1（改造前） | ~2,500 | ~16.5 小时 |
| **32（已启用 max-num-seqs 64）** | **~36,000** | **~1.2 小时** |

全套约 70 万次调用 ≈ 20 小时纯 GPU 时间。

注意：runner 内部仍是串行 `for case in ...`（Exp18/24 的顺序是实验语义，必须串行；
Exp21/22/23 是纯 LOO，理论上可加 case 级线程池，但尚未实现）。
当前吞吐提升主要来自 vLLM 服务端的 `--max-num-seqs` + `--enable-prefix-caching`。
