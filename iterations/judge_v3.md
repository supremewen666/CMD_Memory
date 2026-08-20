# Phase 4 Review v3 — B 方案

verdict: PASS

本轮重新读取 `task.md`、`plan_res.md`、`survey_res.md`，并对 v2 的四个阻塞逐项追踪实际调用路径；保留用户既有未提交改动，未执行 reset/checkout/commit。

## 修复与调用路径证据

1. Zero-call ecology 已接入 `audit_identifiability` event loop：每个 case 构造仅含 typed telemetry 的 `NicheObservation`，调用 `record_zero_call_ecology_window`，写入 `niche_snapshot`、合法状态变化（状态确实变化时）和触发条件满足的 `discovery_pressure`。事件索引按窗口预留 3 个位置，ledger 仍严格 hash-chain；`evaluation_only` 的拒写纪律未改动。证据：`experiments/ghost_ecology_zero_call.py` 的 `ecology = GhostEcology(...)` 与逐 case loop；集成测试验证报告中的 window count 和 ledger event types。
2. semantic vocabulary 在 runner 首个 dev-prefix（前 20% case）冻结，所有后续 case 调用 `vocabulary.validate`，未知 cluster 直接拒绝；manifest 中写出 source、tokens、schema/vocabulary SHA-256。没有读取 outcome/gold 扩词。证据：同一文件 `semantic_cluster_vocabulary` report 字段和 `test_audit_runner_executes_ecology_loop_and_manifest`。
3. 新增 `IncidentTriageStores.apply` 薄适配：`process_fault` 唯一写 `FailureMemoryStore` 并记录 pipeline patch；`state_drift` 唯一写 `LineageLog` supersession；`adversarial_poison` 唯一写 quarantine IDs + audit record。跨型参数在落库前拒绝，避免部分 audit mutation。证据：`cmd_audit/repair/incident_triage.py` 及三分支互斥测试。
4. surrogate registry 改为 `(domain, failure_type)` pair key。缺少预注册 pair 的 claim 显式 `UNVERIFIED`；缺阈值的 gap 不再因 `tau=inf` 误报 pass。rank/CI gate 保持原有条件。证据：`cmd_audit/eval/surrogate_gap.py` 与 pair-key registry 测试。
5. v2 已修复的 E4b runtime mutation 泄漏保留：archive validation、成功筛选、proposal admission 全部只读取 `gold_free_gain`，不读取 `shadow_gain`；回归测试继续通过。

## 关键不变量复核

- `case.gold_*`、`recovery_gain`、post-outcome labels：仅在 shadow/offline audit reference；runtime selection/context/mutation/vocabulary 均不读取。新增 ecology loop 使用 `valid/rolled_back/changed_item_count/locality_cost` 派生信号。
- descriptor/random/unkeyed：同一 runner、同一 candidate budget、seeded random mapping；existing niche gate 继续要求 all_frozen/unkeyed paired contrasts。headline 映射保持 descriptor/random/unkeyed 唯一定义。
- vocabulary：只从 dev-prefix 冻结，未来未知 token fail closed；manifest hash 可复核。
- ITEM_STALE / metadata-decoupling：zero-call substrate validator 拒绝；正常带真实 observed timestamp 的系统路径仍可使用 ITEM_STALE，未误伤正常能力。
- niche transitions：显式合法表拒绝非法跳转；zero-call loop 使用 previous snapshot/state，事件索引单调递增。
- triage：三机制互斥、provenance/audit 落点明确，禁止跨型 fallback。

## 修改文件

- `experiments/ghost_ecology_zero_call.py`
- `experiments/niche_evolution_runner.py`
- `cmd_audit/repair/incident_triage.py`
- `cmd_audit/eval/surrogate_gap.py`
- `tests/experiments/test_niche_evolution_runner.py`
- `tests/experiments/test_phase4_wiring.py`

## 测试证据

定向 Phase-4 集合：`90 passed in 0.19s`。

```text
python -m pytest -q \
  tests/experiments/test_phase4_wiring.py \
  tests/experiments/test_niche_evolution_runner.py \
  tests/repair/test_ghost_ecology.py \
  tests/experiments/test_ghost_ecology_decoupling.py \
  tests/eval/test_telemetry_cmis.py \
  tests/eval/test_niche_gates.py \
  tests/repair/test_niche_archive.py \
  tests/adapters/test_session_log_adapter.py \
  tests/eval/test_anchor_discipline.py
```

`git diff --check`：通过。
