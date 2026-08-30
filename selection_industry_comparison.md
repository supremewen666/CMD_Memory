# Algorithm Selection

## Project Goal

- task: 在不修改官方记忆系统内部算法的前提下，评估 CMD 的质量故障归因与修复能力，并同时证明结果不是由更强的 reader/action head、模型端点或预算造成的。
- main metric: controlled track 的 post-seal repair success / recovery gain；同时报告合法 operator 命中、abstain、budget exhaustion、LLM/token/latency 用量。native track 单独报告官方原生回答或记忆结果。
- key constraints: 固定官方仓库 commit、独立虚拟环境、统一 Qwen3 reader/action head 与预算、runtime 不读 gold/正确 operator/未来事件、append-only 审计。仓库没有 `SOUL.md`，因此以冻结任务单 `task.md` 作为项目方向来源。

## Decision Criteria

- causal fairness: 能否把“记忆后端差异”与“CMD 修复机制差异”分开解释。
- ecological validity: 是否真正调用官方源码库/服务，而不是自制近似 baseline。
- claim safety: 是否避免把带 shared repair head 的增强系统误称为官方原生能力。
- implementation cost: 是否能复用现有 `PinnedJsonSubprocessAdapter`、Stage 9 预算与封存 scorer。

## Candidate Options

| Route | Core idea | Strengths | Risks | Cost | Basis |
|---|---|---|---|---|---|
| A. 裸 native 横比 | CMD 直接对 LightMem、LycheeMem、Mem0 的官方原生输出 | 最贴近真实产品使用 | 任务接口、能力与输出类型不同；原生系统不输出 CMD operator，无法做公平的 operator 比较 | medium | `survey_res.md` §1.1 表明竞品主要优化检索/能力，CMD 优化质量故障归因与修复 |
| B. 仅 controlled shared-head | 三个官方系统都接同一 Qwen3 repair head，与 CMD 在同预算下比较 | 因果解释最干净；可复用现有 Stage 9 ABI | 只能证明“官方后端 + shared head”，不能代表各系统完整原生能力 | medium | `survey_res.md` §1.1/§1.2 强调反馈信号、修复对象与动作空间必须分开比较 |
| C. 分层双轨 | controlled track 做主实验，native track 做独立外部有效性实验；两张表、两套指标、禁止混排 | 同时获得公平性和真实性；claim 边界最清楚 | 实现与运行成本最高；native 指标不能直接支撑 operator 优势 | high | `task.md` 的同预算、gold-free、封存纪律；`survey_res.md` 的对象/信号边界 |

## Chosen Route

- route: C. 分层双轨，但以 controlled track 为论文主表，native track 为次级结果。
- why this route: controlled track 回答“在相同模型、检索预算和 legal operator 空间下，CMD 的修复机制是否更好”；native track 回答“官方系统在其自然配置下整体表现如何”。两个问题都重要，但不能在同一排名表中回答。
- what to implement first:
  1. 补齐 Stage 9 的 adapter config、非零系统预算与三个 thin wrapper。
  2. 先跑四个 controlled arms：`LightMem + shared repair head`、`LycheeMem + shared repair head`、`Mem0 OSS + shared repair head`、`CMD`。
  3. 给每个后端增加 `no-repair/abstain-only` 基线，并增加 `CMD - typed telemetry` 消融，用于区分后端质量和修复机制增益。
  4. controlled track 封存后再实现 `NativeMemoryResponse`，native 结果另表报告。

## Rejected Routes

- route: A. 裸 native 横比
  - why not now: 它把不同问题、不同输出契约和不同系统能力混成一个数字，不能支撑 CMD repair operator 的因果声明。
- route: B. 仅 controlled shared-head
  - why not as final design: 它适合作为主实验，但单独使用会被质疑没有覆盖官方系统的原生 consolidation、offline update、MCP/Cloud 等实际能力。

## Fallback Route

- route: B. 仅 controlled shared-head。
- when to switch: 若 native track 需要无法冻结的 Cloud 能力、服务端状态或超出预算的部署工作，则保留 controlled 主表，把 native track 明确列为 future work；不要降低 controlled track 的公平性来换取 native 覆盖。

## Next Step

- recommended command: 完成 CLI/wrapper 接线后运行 `pytest -q tests/spec_v03/test_industry_adapters.py tests/spec_v03/test_stage59_runner.py tests/experiments/test_e5_competitor_matrix.py`。
- expected output: adapter 合同、预算传播、非法 operator 拒绝、四臂矩阵与 post-seal scorer 全部通过；随后再进行固定 commit 的 pilot。
