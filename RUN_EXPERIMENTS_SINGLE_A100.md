# 单卡 A100 观测式实验执行手册（2026-07-30）

状态：observational arena 框架已完成，467 tests pass。三个 Arena runner + 真实 backend
（reference-free runtime judge / shadow gold-supervised judge 隔离）就绪，可直接执行。

本文件覆盖新的观测式实验主线（Gold-Free 信号台 + 技能生态学 + 链动力学），旧干预式
实验（Exp14-25）的判决树保留在底部作为补充证据路径。

---

## 阶段 0：环境（一次性，~10 分钟）

### 0.1 起 vLLM 双端点（同一张卡）

7B fp16 权重约 14GB，A100 80GB 可同驻 judge + answerer 两个端点。
**judge 全程冻结不换**，只换 answerer —— 这是跨环境可比的前提。

```bash
# judge（冻结；必须支持 top_logprobs，shadow G-Eval 依赖它）
CUDA_VISIBLE_DEVICES=0 nohup vllm serve Qwen/Qwen2.5-7B-Instruct \
  --port 8000 --gpu-memory-utilization 0.42 --max-model-len 8192 \
  --max-num-seqs 64 --enable-prefix-caching \
  > /tmp/vllm_judge.log 2>&1 &

# answerer（生成修复后回答，不参与 gold-free/supervised 评分）
CUDA_VISIBLE_DEVICES=0 nohup vllm serve Qwen/Qwen2.5-7B-Instruct \
  --port 8001 --gpu-memory-utilization 0.42 --max-model-len 8192 \
  --max-num-seqs 64 --enable-prefix-caching \
  > /tmp/vllm_answer.log 2>&1 &

# 等两个端点就绪
until curl -s localhost:8000/v1/models >/dev/null && curl -s localhost:8001/v1/models >/dev/null; do sleep 5; done
echo "both endpoints up"
```

40GB 卡：改用 AWQ/GPTQ 4-bit 权重（7B≈5GB），双端点 + KV cache 仍宽裕。

`--enable-prefix-caching` 是零代码前缀复用。Arena 的 reference-free judge 对每个 case
调用多次相同 prompt 前缀（query + context），实测去重可省 ~27% 调用。

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

### 0.3 G0 门：judge 端点必须返回可解析 logprobs

```bash
python - <<'PY'
import sys; sys.path.insert(0, ".")
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from experiments.experiment_runner_common import assert_g_eval_available
assert_g_eval_available(LLMClient(LLMClientConfig.for_role("judge")), role="preflight-judge")
print("G0 judge logprob gate: OK")
PY
```

**不过门就停**。shadow gold-supervised score 建在 G-Eval 上，judge 不返回 logprobs 则
shadow 评分无效（不影响 gold-free runtime score，但分析无法做）。

### 0.4 完整测试套确认（无需 GPU）

```bash
python -m pytest tests/ -q
# 预期：467 passed
```

---

## 阶段 1：Arena 流验证（无需 GPU，< 1 分钟）

三个 Arena 的 stream loader 验证（family 结构、坐标恢复、subset 分布）：

```bash
python -m experiments.run_arena_memtrace --validate-only
python -m experiments.run_arena_memfail --validate-only
python -m experiments.run_arena_stale --validate-only
```

预期输出：

```
[RESULT] arena_id=memtrace
[RESULT] validated_cases=2047
[RESULT] families=182
[RESULT] failure_distribution=granularity_error:253,item_conflict:253,item_stale:253,null:539,retrieval_error:253,safety_error:496
[RESULT] subset_distribution=memtrace_kp:2047

[RESULT] arena_id=memfail
[RESULT] validated_cases=692
[RESULT] families=N
[RESULT] failure_distribution=granularity_error:200,item_conflict:100,retrieval_error:235,safety_error:157

[RESULT] arena_id=stale
[RESULT] validated_cases=1200
[RESULT] families=400
[RESULT] failure_distribution=item_conflict:600,item_stale:600
```

---

## 阶段 2：MemTrace-B 观测式 Arena（主实验，~1.5 小时 GPU）

**这是论文的核心实验。一次运行产出全部三个实验（A: Gold-Free 信号台, B: 技能生态学,
C: 链动力学）的原始观测数据——三个 Observer 在同一个 stream 上并行记录。**

### 2.1 冒烟（前 50 case，确认 backend 连通，~2 分钟）

```bash
python -m experiments.run_arena_memtrace \
  --backend-factory experiments.arena_backends:create_vllm_backend \
  --limit 50 --chains \
  --output artifacts/arena/memtrace_smoke.jsonl
```

检查输出：
```bash
python -c "
import json
lines = [json.loads(l) for l in open('artifacts/arena/memtrace_smoke.jsonl')]
types = {}
for r in lines: types[r['record_type']] = types.get(r['record_type'], 0) + 1
for k, v in sorted(types.items()): print(f'{k}: {v}')
"
```

预期：`arena_manifest: 1, gold_free_observation: 50, competition_event: 50, chain_attempt: >=200`

### 2.2 正式运行（2047 cases, ~1.5 小时）

```bash
mkdir -p artifacts/arena
python -m experiments.run_arena_memtrace \
  --backend-factory experiments.arena_backends:create_vllm_backend \
  --chains --deposit-after 0.5 --deposit-min-benefit 0.05 --deposit-min-support 3 \
  --output artifacts/arena/memtrace_observations.jsonl \
  2>&1 | tee artifacts/arena/memtrace_run.log
```

**参数说明：**

| 参数 | 值 | 含义 |
|------|-----|------|
| `--chains` | on | 每 case 对不同 family 的 skill 对执行 A→B 和 B→A 链探测 |
| `--deposit-after 0.5` | 50% stream 位置 | 在 1023 个 case 后触发一次链沉积（自然实验） |
| `--top-k 3` | 默认 | 每个 case 激活 top-3 skill 竞争 |
| `--recovery-threshold 0.1` | 默认 | Δk ≥ 0.1 计入 recovery |

**LLM 调用估算（per case）：**
- 1 baseline answer + 1 baseline runtime score + 1 baseline shadow score = 3 calls
- per candidate (≤3): 1 repaired answer + 1 runtime score + 1 shadow score = up to 9 calls
- chain pairs (~4): 1 chained answer + 1 runtime score = up to 8 calls
- 合计约 20 calls/case × 2047 ≈ 41K calls
- 缓存（相同 context 的 answer/score 复用）实际约 30K unique calls
- 在 vLLM `--max-num-seqs 64` 下 ≈ 1–1.5 小时

**产出（JSONL，一行一个 record）：**

| record_type | 数量 | 内容 |
|---|---|---|
| `arena_manifest` | 1 | 参数、signal 名称、seed |
| `gold_free_observation` | 2047 | 每个 case 的 gold-free/shadow 排序保真度、abstention、Spearman ρ |
| `competition_event` | 2047 | 每 case winner/loser/tie/abstention |
| `ecology_snapshot` | 4 | 25/50/75/100% checkpoint 的 niche profile、overlap、diversity、JSD |
| `chain_attempt` | ~8000 | 每个尝试的链对及其 gain |
| `coactivation_snapshot` | 4 | 共激活网络边 |
| `chain_deposition_event` | 0–1 | 沉积的 composite skill |

---

## 阶段 3：MemFail Arena（跨环境复现，~20 分钟 GPU）

```bash
python -m experiments.run_arena_memfail \
  --backend-factory experiments.arena_backends:create_vllm_backend \
  --no-chains \
  --output artifacts/arena/memfail_observations.jsonl \
  2>&1 | tee artifacts/arena/memfail_run.log
```

MemFail 无 recurrent family 结构，链探测不适用（`--no-chains`）。
692 cases × ~12 calls ≈ 8K calls → ~15–20 分钟。

---

## 阶段 4：STALE Arena（邻接生态位竞争，~25 分钟 GPU）

```bash
python -m experiments.run_arena_stale \
  --backend-factory experiments.arena_backends:create_vllm_backend \
  --no-chains \
  --output artifacts/arena/stale_observations.jsonl \
  2>&1 | tee artifacts/arena/stale_run.log
```

STALE 只有两个 label（item_stale / item_conflict），天然适合观测两个技能在相邻生态位上的竞争。
1200 cases × ~12 calls ≈ 14K calls → ~25 分钟。

---

## 阶段 5：扰动实验（手动触发）

PerturbationProbe（`skill_ecology.py:604`）已实现但 Arena CLI 暂未直接支持。
在基线 Arena 运行后，用分析脚本决定移除对象，然后手动执行扰动 run：

### 5.1 确定扰动目标

```bash
python -c "
import json
# 读 MemTrace-B 基线 ecology snapshot
lines = [json.loads(l) for l in open('artifacts/arena/memtrace_observations.jsonl')]
niches = [r for r in lines if r['record_type'] == 'ecology_snapshot']
# 找 50% checkpoint 的 dominant skill
for n in niches:
    if '/1024/' in n['checkpoint'] or '512/' in n['checkpoint']:
        for p in n['niches']:
            if p['total_wins'] > 0:
                print(f'{p[\"skill_id\"]}: wins={p[\"total_wins\"]}, '
                      f'niche={p[\"dominant_niche\"]}, spec={p[\"specialization_index\"]:.3f}')
"
```

### 5.2 跑扰动 run

```bash
# 假设要移除 'seed:retrieval_error'（当前 win-rate 最高的 skill）
python -m experiments.run_arena_memtrace \
  --backend-factory experiments.arena_backends:create_vllm_backend \
  --no-chains \
  --perturb-remove seed:retrieval_error --perturb-after 0.25 \
  --output artifacts/arena/memtrace_perturb_keystone.jsonl
```

注意：`--perturb-remove` 和 `--perturb-after` 需要添加到 `arena_cli.py`。
如 CLI 尚未支持，可以改 `ObservationalArenaRunner.run()` 在 stream 位置 25% 后
从 `candidates()` 中排除指定 skill，并在 results 中包含 `PerturbationEvent`。

---

## 阶段 6：多 seed 复现（~4.5 小时 GPU）

生态位结构的跨 seed 稳定性验证：

```bash
for s in 24 124 224; do
  python -m experiments.run_arena_memtrace \
    --backend-factory experiments.arena_backends:create_vllm_backend \
    --seed $s --chains --deposit-after 0.5 \
    --output artifacts/arena/memtrace_seed${s}.jsonl \
    2>&1 | tee artifacts/arena/memtrace_seed${s}.log
done
```

---

## 阶段 7：统一分析（无需 GPU，~5 分钟）

```bash
mkdir -p artifacts/arena/analysis
python -m experiments.analyze_arena_results \
  --inputs \
    artifacts/arena/memtrace_observations.jsonl \
    artifacts/arena/memfail_observations.jsonl \
    artifacts/arena/stale_observations.jsonl \
  --output-dir artifacts/arena/analysis
```

**产出文件：**

| 文件 | 对应实验 | 内容 |
|---|---|---|
| `signal_by_failure.csv` | A1 | gold-free/shadow 排序保真度按 arena × failure_type |
| `signal_by_probe_coordinates.csv` | A3 | 信号沿 age/question_type/evidence_condition 的漂移 |
| `niche_profiles.csv` | B1 | 每个 skill × failure_type × checkpoint 的 win rate |
| `niche_overlap.csv` | B1 | skill 对余弦相似度 + 竞争标记 |
| `succession.csv` | B2 | 各 skill 在各 checkpoint 的 winner share + diversity + JSD |
| `cross_arena_niche_reproducibility.csv` | B1 复现 | 同一 skill 在 memtrace vs memfail vs stale 上的 niche vector 余弦相似度 |
| `chain_benefit_spectrum.csv` | C2 | 每个 arena 的链增益分布（nonpositive/weak/meaningful） |
| `chain_directionality.csv` | C3 | 每对 skill 的 A→B vs B→A 平均增益差异 |
| `coactivation_edges.csv` | C1 | 每个 checkpoint 的共激活网络边 |
| `depositions.csv` | C5 | 沉积事件的 composite skill + 支持 case |

`analysis_manifest.json` 记录 `"hypothesis_tests_run": false`——这保证了分析是
纯描述性的。

---

## 阶段 8：多模型复测（~2 小时 GPU）

换 answerer，**judge 端口 8000 不动**：

```bash
# 停旧 answerer，起新
pkill -f "port 8001"
CUDA_VISIBLE_DEVICES=0 nohup vllm serve meta-llama/Llama-3.1-8B-Instruct \
  --port 8001 --gpu-memory-utilization 0.42 --max-model-len 8192 \
  --max-num-seqs 64 --enable-prefix-caching > /tmp/vllm_answer2.log 2>&1 &

export LLM_MODEL=llama-3.1-8b-instruct
# LLM_JUDGE_* 保持不变

# 只在 MemTrace-B 上复现（最重要）
python -m experiments.run_arena_memtrace \
  --backend-factory experiments.arena_backends:create_vllm_backend \
  --chains \
  --output artifacts/arena/memtrace_llama_observations.jsonl \
  2>&1 | tee artifacts/arena/memtrace_llama_run.log

# 分析时包含两个模型：
python -m experiments.analyze_arena_results \
  --inputs \
    artifacts/arena/memtrace_observations.jsonl \
    artifacts/arena/memtrace_llama_observations.jsonl \
  --output-dir artifacts/arena/analysis_multimodel
```

---

## 阶段 9：无 GPU 收尾

- G-Eval 人工一致性小样（50–100 case，报 Spearman/Kappa）
- MemTrace-A（arXiv 2605.28732，zjunlp）正面对比——唯一可对齐的轴是下游端任务增益
- STALE 口径对齐：补"无注入自然基线绝对准确率"
- 生态位结构可视化脚本（niche heatmap、succession stacked area、co-activation network）

---

## 关键数据事实（备忘）

来自 `data/probe_cases/`：

| 文件 | cases | families | labels |
|------|-------|----------|--------|
| `memtrace_kp_cases.json` | 2047 | 182 | retrieval 253, stale 253, granularity 253, conflict 253, safety 496, null 539 |
| `memfail_cases.json` | 692 | — | retrieval 235, granularity 200, safety 157, conflict 100 |
| `stale_item_cases.json` | 1200 | ~400 | item_stale 600, item_conflict 600 |

MemTrace-B 内-family memory scale drift 为零（120 numeric + 62 slug families 的
`len(extracted_memory)` 在 min(c) 和 max(c) 间中位数相等），这是 within-family
marginal utility confound-free 的前提。

---

## 吞吐参考（Qwen2.5-7B，1×A100 80GB）

| Arena | cases | LLM calls | 预计时间 |
|-------|-------|-----------|---------|
| MemTrace-B (with chains) | 2047 | ~30K | ~1.0–1.5h |
| MemFail (no chains) | 692 | ~8K | ~15–20m |
| STALE (no chains) | 1200 | ~14K | ~20–25m |
| 3 seeds MemTrace-B | 6141 | ~90K | ~3–4.5h |
| Llama 复现 | 2047 | ~30K | ~1–1.5h |
| **全套（Qwen ×3 + Llama ×1）** | | | **~5–6h** |

---

## 旧干预式实验（Exp14-25）保留路径

已完成的自建数据实验（Exp14-22, Exp24, Exp25）的作用：

- **论文主体**：观测式 Arena（MemTrace-B / MemFail / STALE）
- **Appendix 补充证据**：Exp24 多臂对照 + Exp22 operator transfer + Exp25 durability
- **不必重跑**：除非审稿人要求额外的干预式对照

---

## 架构速查

```
case stream
  → VLLMDualScoreArenaBackend
    ├─ candidates()      gold-free: structural activation 排序
    ├─ evaluate()        runtime: reference-free grounded-answer rubric
    │                    shadow: G-Eval answer-rubric (读 gold_answer)
    │                    两个评分路径隔离：runtime 物化后才算 shadow
    └─ deposit_composite()  链沉积入候选池

  → CompetitiveExecutor (top-3, winner-take-all via gold-free Δk)

  → 三个 Observer (并行, append-only, 不改执行):
    ├─ GoldFreeObserver   A: 排序保真度, Spearman ρ, oracle rank, shadow regret
    ├─ EcologyObserver    B: win-rate niche, overlap, succession, diversity, JSD
    └─ ChainObserver      C: co-activation, chain benefit spectrum, directionality

  → write_arena_artifacts  →  JSONL (每行一个 record, allow_nan=False)

  → analyze_arena_results  →  CSVs (纯描述性, hypothesis_tests_run=false)
```
