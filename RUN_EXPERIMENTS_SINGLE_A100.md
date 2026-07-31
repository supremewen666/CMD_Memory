# 双卡 A100 观测式实验执行手册（2026-07-31）

状态：observational arena 框架已完成，467 tests pass。两张 A100 通过不同 SSH 连接，
并行执行全部实验。

---

## 核心架构：两张卡的分工

```
GPU 0 (SSH 1)                          GPU 1 (SSH 2)
─────────────────────────────────      ─────────────────────────────────
Qwen vLLM :8000                        Qwen vLLM :8000
MemTrace-B seed 24  (~1.5h)            MemTrace-B seed 224 (~1.5h)
MemTrace-B seed 124 (~1.5h)            STALE             (~25min)
MemFail              (~20min)          ↓ 停 Qwen，起双端点
                                       Qwen judge :8000 + Llama answerer :8001
                                       MemTrace-B Llama   (~1.5h)
─────────────────────────────────      ─────────────────────────────────
wall-clock ~3.5h                       wall-clock ~3.5h

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

---

## GPU 0（SSH 1）— Qwen MemTrace-B ×2 + MemFail

```bash
cd ~/wsy/CMD_Memory

# 冒烟（50 case，确认连通，~2min）
./run_remaining_experiments.sh --role gpu0 --smoke

# 正式运行（~3.5h）
./run_remaining_experiments.sh --role gpu0
```

产出（`artifacts/arena/`）：

| 文件 | 内容 |
|------|------|
| `memtrace_seed24.jsonl` | Qwen MemTrace-B, seed 24（+ chains + deposit） |
| `memtrace_seed124.jsonl` | Qwen MemTrace-B, seed 124（+ chains + deposit） |
| `memtrace_observations.jsonl` | seed 24 别名（统一分析入口） |
| `memfail_observations.jsonl` | MemFail 跨环境复现（no chains） |

---

## GPU 1（SSH 2）— Qwen MemTrace-B seed 224 + STALE + Llama

```bash
cd ~/wsy/CMD_Memory

# 冒烟（50 case，确认连通，~2min）
./run_remaining_experiments.sh --role gpu1 --smoke

# 正式运行（~3.5h）
./run_remaining_experiments.sh --role gpu1
```

分两阶段自动执行：

**Phase 1 — Qwen 单端点（~2h）**

| 步骤 | 时长 |
|------|------|
| MemTrace-B seed 224（+ chains + deposit） | ~1.5h |
| STALE（no chains） | ~25min |

**Phase 2 — Llama 双端点（~1.5h）**

脚本自动停 Qwen vLLM，起双端点（Qwen judge :8000 + Llama answerer :8001），然后跑 MemTrace-B。

产出（`artifacts/arena/`）：

| 文件 | 内容 |
|------|------|
| `memtrace_seed224.jsonl` | Qwen MemTrace-B, seed 224（+ chains + deposit） |
| `stale_observations.jsonl` | STALE 邻接生态位竞争（no chains） |
| `memtrace_llama.jsonl` | Llama-3.1-8B MemTrace-B（Qwen judge 冻结，+ chains + deposit） |

---

## 断点续跑

每个 Arena 独立，出错可以单独重跑：

```bash
# GPU 0 只跑 MemTrace-B seed 124（跳过已完成的 seed 24）
./run_remaining_experiments.sh --role gpu0 --only memtrace_seed124

# GPU 0 只跑 MemFail
./run_remaining_experiments.sh --role gpu0 --only memfail

# GPU 1 只跑 STALE
./run_remaining_experiments.sh --role gpu1 --only stale

# GPU 1 只跑 Llama（自动起双端点）
./run_remaining_experiments.sh --role gpu1 --only memtrace_llama
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
| MemTrace-B Llama | 2047 | 182 | GPU 1 | yes |
| MemFail | 692 | — | GPU 0 | no |
| STALE | 1200 | ~400 | GPU 1 | no |

## 吞吐估算

| Arena | cases | LLM calls ~ | 单卡时间 |
|-------|-------|------------|---------|
| MemTrace-B (with chains) | 2047 | ~30K | ~1.5h |
| MemFail (no chains) | 692 | ~8K | ~20min |
| STALE (no chains) | 1200 | ~14K | ~25min |
| MemTrace-B Llama | 2047 | ~30K | ~1.5h |

**双卡并行 wall-clock：~3.5h**（vs 单卡串行 ~5h）

## 多模型复现设计

Llama 阶段使用双端点：

| 端点 | 端口 | 模型 | 用途 |
|------|------|------|------|
| Judge | 8000 | Qwen2.5-7B-Instruct | **冻结**，reference-free + shadow G-Eval |
| Answerer | 8001 | Llama-3.1-8B-Instruct | 生成修复后回答 |

Judge 冻结保证了跨模型可比性——gold-free 排名和 shadow 评分使用同一把尺子，
只有 answerer 换了。这是审稿人认可的跨模型复现标准做法。

## 模型选型说明

| 模型 | 定位 | 理由 |
|------|------|------|
| Qwen2.5-7B-Instruct | 主力 | 2025 年开源 agent 论文最常见选择 |
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
