# 双卡 A100 观测式实验执行手册（2026-07-31）

状态：observational arena 框架已完成，467 tests pass。两张 A100 通过不同 SSH 连接，
并行执行全部实验。

---

## 核心架构：两张卡的分工

```
GPU 0 (SSH 1)                          GPU 1 (SSH 2)
─────────────────────────────────      ─────────────────────────────────
Qwen judge :8000 + Llama :8001         Qwen judge :8000 + Llama :8001
MemTrace-B seed 24                     MemTrace-B seed 224
MemTrace-B seed 124                    STALE
MemFail                                MemTrace-B replicate seed 24
─────────────────────────────────      ─────────────────────────────────
wall-clock: 取决于实际吞吐               wall-clock: 取决于实际吞吐

汇聚：scp GPU1 artifacts/arena/*.jsonl → GPU0
分析：./run_remaining_experiments.sh --role analyze  (~5min CPU)
```

Judge 全程冻结为 Qwen2.5-7B（包括 Llama 复现阶段），保证跨模型可比性。

---

## 启动前检查（两台都做）

```bash
cd ~/wsy/CMD_Memory
git status          # 确认在 main，无未提交改动
python -m pytest tests/ -q   # 预期 467 passed
```

### 本地模型目录与缓存回退

脚本优先直接使用 `~/pretrained_lms` 中的本地权重：

- `~/pretrained_lms/Qwen2.5-7B-Instruct`
- `~/pretrained_lms/Meta-Llama-3.1-8B-Instruct`

因此本机已有这两个目录时，下面的命令无需额外环境变量：

```bash
./run_remaining_experiments.sh --role gpu0 --smoke
```

可用 `CMD_PRETRAINED_LMS_ROOT` 覆盖该父目录；若本地目录不存在，脚本才通过 Hugging Face 的当前离线缓存查找，且不会联网下载。若仍报出 `LocalEntryNotFoundError`，先在 GPU 机器上寻找此前下载的 snapshot：

```bash
find "$HOME/wsy" -type d -path '*/models--Qwen--Qwen2.5-7B-Instruct/snapshots/*' 2>/dev/null
find "$HOME/wsy" -type d -path '*/models--meta-llama--Llama-3.1-8B-Instruct/snapshots/*' 2>/dev/null
```

将找到的两个 snapshot 目录显式传给脚本即可，无需重新下载：

```bash
CMD_QWEN_MODEL_DIR=/path/to/models--Qwen--Qwen2.5-7B-Instruct/snapshots/<revision> \
CMD_LLAMA_MODEL_DIR=/path/to/models--meta-llama--Llama-3.1-8B-Instruct/snapshots/<revision> \
./run_remaining_experiments.sh --role gpu0 --smoke
```

---

## GPU 0（SSH 1）— MemTrace-B ×2 + MemFail

```bash
cd ~/wsy/CMD_Memory

# 冒烟（50 case，确认连通，~2min）
./run_remaining_experiments.sh --role gpu0 --smoke

# 正式运行（断开 SSH 仍继续；PID 和 tail 命令会打印到终端）
./run_remaining_experiments.sh --role gpu0 --detach
```

产出（`artifacts/arena/`）：

| 文件 | 内容 |
|------|------|
| `memtrace_seed24.jsonl` | MemTrace-B, seed 24（Llama answerer + Qwen judge，+ chains + deposit） |
| `memtrace_seed124.jsonl` | MemTrace-B, seed 124（Llama answerer + Qwen judge，+ chains + deposit） |
| `memtrace_observations.jsonl` | seed 24 别名（统一分析入口） |
| `memfail_observations.jsonl` | MemFail 跨环境复现（no chains） |

---

## GPU 1（SSH 2）— MemTrace-B seed 224 + STALE + replicate seed 24

```bash
cd ~/wsy/CMD_Memory

# 冒烟（50 case，确认连通，~2min）
./run_remaining_experiments.sh --role gpu1 --smoke

# 正式运行（断开 SSH 仍继续）
./run_remaining_experiments.sh --role gpu1 --detach
```

三个 phase 均使用双端点：Qwen judge `:8000` 冻结用于评估，Llama answerer `:8001` 用于生成与选择。为避免同卡显存与 KV-cache 初始化竞争，脚本会先等待 Qwen ready，再启动 Llama；默认显存配额分别为 `0.25` 和 `0.50`，可通过 `VLLM_QWEN_GPU_MEMORY_UTILIZATION`、`VLLM_LLAMA_GPU_MEMORY_UTILIZATION` 覆盖。Qwen 的 KV 预算较小，但本次带 `--deposit-after` 的 MemTrace 固定使用单 case worker，足以满足跑批需求。

产出（`artifacts/arena/`）：

| 文件 | 内容 |
|------|------|
| `memtrace_seed224.jsonl` | MemTrace-B, seed 224（Llama answerer + Qwen judge，+ chains + deposit） |
| `stale_observations.jsonl` | STALE 邻接生态位竞争（no chains） |
| `memtrace_llama.jsonl` | MemTrace-B replicate seed 24（Llama answerer + Qwen judge，+ chains + deposit） |

---

## 断线存活、日志与断点续跑

正式运行使用 `--detach`。它会以 `nohup + setsid` 启动独立会话，主日志写入
`artifacts/arena/run_<role>_<timestamp>.log`；SSH 断开不会终止 arena 进程。启动后按脚本打印的路径查看进度，或使用：

```bash
tail -f artifacts/arena/run_gpu0_<timestamp>.log
```

Python 已启用无缓冲输出，phase 日志（`memtrace_*.log`、`memfail_run.log`、`stale_run.log`）和主日志均会持续更新。vLLM 冷启动就绪探测默认最多等待 300 秒。

每个 Arena phase 在输出 JSONL 已存在且非空时会自动跳过。因此断线或前序 phase 完成后，直接重新执行同一条正式命令即可继续；如需强制重跑某个 phase，先删除它的 JSONL，再运行：

```bash
# GPU 0 强制重跑 MemTrace-B seed 124
rm artifacts/arena/memtrace_seed124.jsonl
./run_remaining_experiments.sh --role gpu0 --only memtrace_seed124 --detach

# GPU 0 只跑 MemFail
./run_remaining_experiments.sh --role gpu0 --only memfail --detach

# GPU 1 只跑 STALE
./run_remaining_experiments.sh --role gpu1 --only stale --detach

# GPU 1 只跑 Llama（自动起双端点）
./run_remaining_experiments.sh --role gpu1 --only memtrace_llama --detach
```

## 汇聚 + 统一分析

GPU 1 完成后，把 JSONL 拉到 GPU 0：

```bash
# 在 GPU 0 上执行
scp user@gpu1:~/wsy/CMD_Memory/artifacts/arena/memtrace_seed224.jsonl \
       artifacts/arena/
scp user@gpu1:~/wsy/CMD_Memory/artifacts/arena/stale_observations.jsonl \
       artifacts/arena/
scp user@gpu1:~/wsy/CMD_Memory/artifacts/arena/memtrace_llama.jsonl \
       artifacts/arena/

# 统一分析（无需 GPU）
./run_remaining_experiments.sh --role analyze
```

分析产出（`artifacts/arena/analysis/`）：

| 文件 | 内容 |
|------|------|
| `signal_by_failure.csv` | gold-free/shadow 排序保真度按 failure_type |
| `signal_by_probe_coordinates.csv` | 信号沿 age × question_type × evidence_condition 漂移 |
| `niche_profiles.csv` | 每个 skill × failure_type × checkpoint 的 win rate |
| `niche_overlap.csv` | skill 对余弦相似度 + 竞争标记 |
| `succession.csv` | 各 skill 在各 checkpoint 的 winner share + diversity + JSD |
| `cross_arena_niche_reproducibility.csv` | 同一 skill 在 memtrace/memfail/stale 上的 niche 余弦相似度 |
| `chain_benefit_spectrum.csv` | 链增益分布（nonpositive/weak/meaningful） |
| `chain_directionality.csv` | A→B vs B→A 平均增益差异 |
| `coactivation_edges.csv` | 每 checkpoint 共激活网络边 |
| `depositions.csv` | 沉积 composite skill |

---

## 关键数据事实

| 数据集 | cases | families | 运行位置 | chains |
|--------|-------|----------|----------|--------|
| MemTrace-B | 2047 | 182 | GPU 0 (×2 seeds) + GPU 1 (×1 seed) | yes |
| MemTrace-B replicate seed 24 | 2047 | 182 | GPU 1 | yes |
| MemFail | 692 | — | GPU 0 | no |
| STALE | 1200 | ~400 | GPU 1 | no |

## 吞吐估算

| Arena | cases | LLM calls ~ | 单卡时间 |
|-------|-------|------------|---------|
| MemTrace-B (with chains) | 2047 | ~30K | ~1.5h |
| MemFail (no chains) | 692 | ~8K | ~20min |
| STALE (no chains) | 1200 | ~14K | ~25min |
| MemTrace-B replicate seed 24 | 2047 | ~30K | 取决于实际吞吐 |

`--deposit-after` 会将 MemTrace 限制为 `--case-workers 1`，实际墙钟时间应以主日志为准，不应把旧的 3.5h 估算当作上限。

## 双端点评估设计

所有 GPU phase 使用双端点：

| 端点 | 端口 | 模型 | 用途 |
|------|------|------|------|
| Judge | 8000 | Qwen2.5-7B-Instruct | **冻结**，reference-free + shadow G-Eval |
| Answerer | 8001 | Llama-3.1-8B-Instruct | 生成修复后回答 |

Judge 冻结保证了跨模型可比性——gold-free 排名和 shadow 评分使用同一把尺子，
只有 answerer 换了。这是审稿人认可的跨模型复现标准做法。

## 模型选型说明

| 模型 | 定位 | 理由 |
|------|------|------|
| Qwen2.5-7B-Instruct | 冻结 judge | 提供跨 phase 一致的选择与评估标尺 |
| Llama-3.1-8B-Instruct | 跨家族复现 | 不同 pretrain corpus/tokenizer，真正测方法泛化 |

不推荐的选择：
- **Mistral-7B**：2023 年模型，现在显过时
- **同家族换尺寸**（Qwen2.5-14B）：测的是 scaling 而非泛化，易被审稿人追问
- **DeepSeek-R1**：reasoning-token 风格差异是额外变量，复杂化归因

## 架构速查

```
case stream
  → VLLMDualScoreArenaBackend
    ├─ candidates()      gold-free: structural activation 排序
    ├─ evaluate()        runtime: reference-free grounded-answer rubric
    │                    shadow: G-Eval answer-rubric (读 gold_answer)
    │                    两条评分路径隔离：runtime 物化后才算 shadow
    └─ deposit_composite()  链沉积入候选池

  → CompetitiveExecutor (top-3, winner-take-all via gold-free Δk)

  → 三个 Observer (并行, append-only, 不改执行):
    ├─ GoldFreeObserver   A: 排序保真度, Spearman ρ, oracle rank, shadow regret
    ├─ EcologyObserver    B: win-rate niche, overlap, succession, diversity, JSD
    └─ ChainObserver      C: co-activation, chain benefit spectrum, directionality

  → write_arena_artifacts  →  JSONL (每行一个 record, allow_nan=False)
  → analyze_arena_results  →  CSVs (纯描述性, hypothesis_tests_run=false)
```

---

## 旧干预式实验（Exp14-25）保留路径

已完成的自建数据实验作用：

- **论文主体**：观测式 Arena（MemTrace-B / MemFail / STALE）
- **Appendix 补充证据**：Exp24 多臂对照 + Exp22 operator transfer + Exp25 durability
- **不必重跑**：除非审稿人要求额外的干预式对照
