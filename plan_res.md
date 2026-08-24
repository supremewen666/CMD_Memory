# Implementation Plan — B 方案：治理、生态位可识别性与参数化路由

> 2026-08-24 supersession note: this zero-call plan is retained as the
> governance/identifiability diagnostic track only. It is no longer the paper's
> answer-quality mainline. The active mainline is the sealed real-model
> LoCoMo/LongMemEval protocol in `docs/OFFICIAL_BENCHMARK_PROTOCOLS.md`; formal
> Evo-Bench execution uses the upstream adapter.

本计划只接线现有实现，不新增机制或数据集。证据文件：`cmd_audit/repair/ghost_ecology.py`（hash-chain ledger、discovery pressure、niche snapshot/transition）、`cmd_audit/eval/telemetry_cmis.py`（zero-call typed telemetry）、`experiments/niche_evolution_runner.py`、`cmd_audit/eval/niche_gates.py`、`cmd_audit/eval/descriptor_policy_value.py` 及对应 tests。`plan_res_c1c2c3.md` 中“niche 仅 appendix”的 Plan A 已废止。

## 1. Dataset / substrate

- 主 protocol：`data/probe_cases/real_recurrent_cases.json`，按 family/user 边界切分；prequential 顺序固定，variant 3–4 只作 held-out probe。
- 外部协议：已有 `memtrace_kp_cases.json`、`memfail_cases.json`（若可用）；不构建新数据集。
- zero-call substrate：复用 `V4CandidateOutcome` 形状与 `experiments/ghost_ecology_zero_call.py`。构造基底禁用 `PipelineAction.ITEM_STALE` 与 metadata-decoupling；后者只能作为 permutation/placebo falsification 臂。
- `semantic_cluster` 词表只从预注册 dev-prefix 生成并记录 schema/vocabulary hash；冻结后不得扩词，不得读 failure label、outcome、gold 或 post-outcome cluster。
- 运行时禁止读取 `case.gold_*`；family、gold、project memory 仅可在 shadow/audit record 中出现。

## 2. Estimator / model wiring

### 2.1 Typed zero-call estimator

现有 `TelemetryChannels(valid, rolled_back, changed_item_count, locality_cost)` 产生零调用 proxy；它只负责候选 repair ordering，不宣称点对点复现 CMIS。与 replay reference 的 gap 和 pairwise/rank agreement 使用 `cmd_audit/eval/telemetry_cmis.py`；gold/recovery 只在离线 shadow measurement 读取。

按域/故障类型报告：

```text
gap_d = mean_i(reference_score_i - proxy_score_i | domain=d)
rank_d = pairwise_concordance(proxy, reference | domain=d)
```

router claim 仅在预注册域阈值 `gap_d <= τ_gap` 且 rank/CI 门通过时成立；其余域写 `UNVERIFIED`，不汇总成全域优势。

### 2.2 唯一 niche-key headline 定义与映射（E4b）

| headline key | 唯一含义 | 映射 |
|---|---|---|
| `descriptor` | 行为 descriptor key | `BehaviorDescriptor.niche_id`，由 cluster + signal signature + runtime surface + version 的 canonical hash 得到；禁止 label-like token |
| `random` | 同分布/预算的随机 key | 对 descriptor key 做 seeded、预注册、case-order-independent permutation；seed 写入 manifest |
| `unkeyed` | 无 niche 信息的单池 | 所有候选映射到同一个 pool；不是随机 key，也不是 unknown descriptor |

`all_frozen` 只作为已有冻结策略的安全对照，不与三臂 headline 混称。主判据复用 `niche_gates.py`：descriptor（或预注册 g*）须同时对 `all_frozen` 与 `unkeyed` 通过 paired family bootstrap；`random` 检验优势是否只是随机分桶。预算、case order、候选 manifest、seed、effective-after 边界完全相同。

### 2.3 生态位记账与治理

在 zero-call 流每个 checkpoint/round 接线现有 `derive_discovery_pressure`、`record_niche_snapshot`、`record_niche_transition`。只允许 `ghost_ecology.py` 已声明的 niche 合法转移表；非法跳转必须拒绝。账本记录 event index、previous hash、payload、provenance；封存审计使用 `evaluation_only=True` 与 held-out anchor 永不读断言。

### 2.4 Incident triage boundary

三型互斥：`process_fault` 只写 `FailureMemory` 并产生 pipeline patch；`state_drift` 只做 supersede 并保留 lineage；`adversarial_poison` 只做 quarantine + audit。不能跨型沉淀或 fallback；不确定时记录 abstention，不改变类型。

## 3. Protocol

1. 预注册 checksum、family split、case order、arms/key mapping、candidate budget、τ_gap、bootstrap seed、anchor IDs、semantic-cluster vocabulary hash。
2. 每 case 从同一 recall snapshot 独立执行候选；先记录 outcome，再让 revision 对下一 case 生效；arm store 隔离。
3. 机制 identifiability 是 paired separation：对 `identity` 与 `random_k` 均需 Δ>0 且 one-sided CI lower bound>0；不能只报告 beat identity。
4. E4b 使用 descriptor/random/unkeyed headline；报告 niche occupancy、transition counts、discovery pressure 与合法转移率。若 descriptor 不胜 unkeyed，生态位 claim 为 negative/partial。
5. E2 先做 permutation/placebo：解耦后可识别性/排序应崩；若不崩，说明 telemetry shortcut，zero-call claim 失败。
6. 违反 gold-free、anchor、词表冻结、合法转移、预算对齐或 hash-chain 的运行标记 `protocol_invalid`，不得纳入 headline。

## 4. Testing / Falsification

- Dataset/protocol：checksum、family split、effective-after、arm isolation、no-update 不变。
- Zero-call：proxy 不读 `recovery_gain`/`gold_*`；telemetry/replay 小样本对拍；输出 domain × failure type 的 gap、rank、CI。
- Niche：三 key 唯一映射；descriptor 禁 label metadata；非法 transition 拒绝；snapshot/pressure 进入 ledger；`niche_gates` 的 all-frozen/unkeyed 安全门通过或明确失败。
- Falsification：permutation/placebo 后优势崩溃；ITEM_STALE/metadata-decoupling 被构造基底拒绝；semantic vocabulary 在 dev-prefix 后不可变；三型 incident 不可交叉落库。
- 轻量验收：`tests/repair/test_ghost_ecology.py`、`tests/repair/test_niche_archive.py`、`tests/eval/test_niche_gates.py`、`tests/experiments/test_niche_evolution_runner.py`、`tests/experiments/test_ghost_ecology_decoupling.py`、`tests/experiments/test_descriptor_policy_value.py` 与既有 failure-memory/governance tests 全绿。fixture/smoke 数字不得作 headline。

## 5. Phase 3 precise work package

1. `experiments/ghost_ecology_zero_call.py`：接 discovery pressure 与 snapshot/transition 事件；增加 schema/hash 与 forbidden-field assertions。
2. `cmd_audit/repair/ghost_ecology.py`：仅复用/暴露既有合法 niche transition 与 ledger replay，不改变状态语义。
3. `experiments/niche_evolution_runner.py`：冻结三 key 映射和同预算执行；输出 descriptor/random/unkeyed paired rows。
4. `cmd_audit/eval/niche_gates.py`、`descriptor_policy_value.py`：把 headline 判据与按域 safety gate 接入现有统计，不新增 selection rule。
5. `cmd_audit/eval/surrogate_gap.py`、`telemetry_cmis.py`：按域/故障类型聚合 gap，并生成 claim registry（pass/conditional/UNVERIFIED）。
6. `cmd_audit/repair/incident_triage.py` 及既有 stores：补齐三型互斥断言与 provenance/audit 落点。

## 6. Claim boundary and deliverables

可声称：治理账本的可审计性；在预注册 key/预算/域阈值下的生态位可识别性；现有参数化路由的条件化恢复排序。不可声称：project memory 是 ground truth、zero-call proxy 等价于 replay-CMIS、跨域 surrogate gap 泛化、descriptor 必然优于 unkeyed、或新演化机制/新数据集贡献。

最终交付：`experiment_res.md` 的 E1/E2/E3/E4/E4b/E5 结果与 provenance；论文 contribution、方法、消融和附录均引用同一 headline key 定义与 claim registry。
