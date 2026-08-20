# CMD 冻结任务单：B 方案（治理 × 生态位可识别性 × 参数化路由）

## 0. 论文定位与硬边界

本论文的承重结构是三个相互约束的贡献：

1. **治理**：gold-free 反事实修复事件进入可验证、append-only、hash-chained 账本；`evaluation_only=True` 封存与违反即作废纪律保证结论可审计。
2. **生态位演化/可识别性（E4b）**：在同一 prequential、同一预算下，比较 descriptor key、random key 与 unkeyed key，检验生态位是否携带可复用的行为结构，而非仅增加随机分桶。
3. **参数化路由**：将现有 typed telemetry、descriptor/niche archive、failure memory 与路由器接线形成 recovery-gated 路由；不声称新演化算法或新数据集。

三分诊必须互斥且可审计：`process_fault → FailureMemory / pipeline patch`；`state_drift → supersede + lineage`；`adversarial_poison → quarantine + audit`。project memory 只能作背景，不是 ground truth；gold 只进入 shadow measurement，不能进入 runtime retrieval、mutation、selection 或 context construction。

砍单：不做 trace 治理分层、新机制开发、新数据集构建、guidance skill 复活。新增内容只能是现有机制的接线、声明、消融或零调用实验。时间不足时按 E5 → E4b → E3 砍；E1 封存确证与 E2 按域 surrogate gap 不可砍。

## 1. Phase 1 — 调研（已完成）

`survey_res.md` 已确认竞品反馈来源、CMIS 与 Double Ratchet anchor 协议，并确认 gold-free × 质量故障 × 反事实归因修复 × 可审计账本的交集空位。其结论只用于 claim boundary，不把竞品或 project memory 当标签来源。

## 2. Phase 2 — 冻结计划与协议（本轮产出 `plan_res.md`）

| 项目 | 冻结要求 | 验收 |
|---|---|---|
| 定义 | arm-paired prequential；机制臂须同时对 `random_k` 与 `identity` 分离；E4b 另以 descriptor/random/unkeyed 为唯一 headline | 只引用现有 runner/arm |
| 治理 | ledger 事件、previous hash、封存 anchor、case/family/provenance/seed 先于实验登记 | 任意违反协议即作废 |
| 零调用 | typed telemetry (`valid`, `rolled_back`, `changed_item_count`, `locality_cost`) 只用于候选排序；与 replay-CMIS 的差距按域报告 | 不读 `gold_*`；permutation 解耦后正对照应崩 |
| E4b | 复用 zero-call substrate，接通 `derive_discovery_pressure`、niche snapshot/transition 记账；比较三种 key | 三种 key 的映射、控制、预算一致 |
| 前瞻纪律 | `semantic_cluster` 词表在 dev-prefix 上冻结并记录 hash；不能用全流 outcome 反推词表 | 新 case 不可改变词表/schema |
| 构造基底 | 禁用 `ITEM_STALE` 与 metadata-decoupling；后者只能是 falsification 臂 | 运行时拒绝违规 action/metadata |
| surrogate | gap 按域/故障类型分报，并以阈值条件化 router claim；无跨域 headline | 无域证据则该域 `UNVERIFIED` |
| triage | 三类 incident 互斥，映射到既有 store/lineage/quarantine/audit | 单测覆盖互斥与落点 |

## 3. Phase 3 — 实现工作包（只接线，不发明机制）

| # | 工作包 | 应改文件 | 测试/验收 |
|---|---|---|---|
| 3.1 | zero-call 流写 discovery pressure、niche snapshot/transition | `experiments/ghost_ecology_zero_call.py`, `cmd_audit/repair/ghost_ecology.py` | `tests/experiments/test_ghost_ecology_zero_call.py`, `tests/repair/test_ghost_ecology.py` |
| 3.2 | 冻结 semantic-cluster dev-prefix/schema hash，拒绝新增词表或 post-outcome label | zero-call runner 与 descriptor adapter | `tests/experiments/test_descriptor_policy_value.py` + 轻量协议测试 |
| 3.3 | 统一 descriptor/random/unkeyed headline 映射，复用 all_frozen/unkeyed 安全门 | `experiments/niche_evolution_runner.py`, `cmd_audit/eval/niche_gates.py` | `tests/experiments/test_niche_evolution_runner.py`, `tests/eval/test_niche_gates.py` |
| 3.4 | surrogate gap 按 domain × failure type 输出；仅过预注册 τ 的域允许 router claim | `cmd_audit/eval/surrogate_gap.py`, `cmd_audit/eval/telemetry_cmis.py` | surrogate-gap 轻量对拍测试 |
| 3.5 | 构造基底拒绝 `ITEM_STALE`/metadata-decoupling；保留 permutation/placebo falsification | zero-call runner/substrate loader | `tests/experiments/test_ghost_ecology_decoupling.py` |
| 3.6 | 三分诊接到既有 stores，禁止跨型 fallback | `cmd_audit/repair/incident_triage.py`, `failure_memory.py`, `governance.py` | 既有 repair/governance/failure-memory tests + 互斥测试 |

不得修改数据集；不得把 `case.gold_*`、构造信号、post-outcome semantic cluster 或 project memory 写入 runtime 路由。

## 4. Phase 4–6 — 审查、实验、写作

- 审查 gold 泄漏、anchor 永不读、账本链完整性、triage 互斥和 arm isolation。
- 实验 E1 封存确证；E2 按域 telemetry-CMIS/replay-CMIS gap 与 permutation 正对照；E3 投毒密度；E4 Mix GHOST 通道消融；**E4b 生态位可识别性**；E5 竞品对比。所有结果写 provenance、seed、timeout/truncation。
- 写作贡献列表只写治理、生态位可识别性、参数化路由。若某域 gap 未过 τ，只写“该域未验证/条件化支持”，不得泛化为全域 router 优势。

## 5. 依赖与降级

`Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6`。E4b 依赖既有 niche archive、niche gates 与 zero-call substrate；E2 依赖 telemetry/replay 对拍；E1 依赖 anchor discipline。所有新实验必须先有冻结协议、key 映射、词表 hash、checksum 与 claim registry。
