# Phase 4 Review v2 — B 方案

verdict: BLOCKED

本轮按冻结的 `task.md` / `plan_res.md` 复核了实际 diff、所有新增未跟踪的 Phase-3 相关文件、调用路径及定向测试；没有把 Phase-3 摘要或“测试全绿”当作接线证据。

## Findings

### BLOCKER-1 — E4b runner 原先用 shadow/recovery label 做 runtime mutation（已修复）

- 位置：`experiments/niche_evolution_runner.py:343-400`。
- 原实现的 `_update_archive` 用 `NicheDualExecution.shadow_gain` 进行 `record_validation`、成功候选筛选和 proposal admission。该字段是 materialized post-outcome reference，违反 `task.md` 的 gold/recovery_gain 不得进入 runtime mutation 纪律。
- 修复：上述三处统一改为 `gold_free_gain`，并新增回归断言，确保与 shadow 值冲突时 archive evidence 仍来自 gold-free 通道：`tests/experiments/test_niche_evolution_runner.py:75-89`。
- 状态：已修复并通过定向测试。

### BLOCKER-2 — zero-call ecology 记账 API 未接入实际 runner

- 位置：API 仅定义在 `experiments/ghost_ecology_zero_call.py:105-144`。
- 全仓调用搜索显示 `record_zero_call_ecology_window`、`record_niche_snapshot`、`record_niche_transition`、`record_discovery_pressure` 除定义/单测外，没有被 `audit_identifiability`、zero-call 流或 `niche_evolution_runner` 调用。故 discovery pressure、snapshot、transition 没有进入实际实验 ledger；这是“未接线 API”，不能支持计划中的 E4b 承诺。
- 结论：当前没有在本轮强行设计跨协议转换（`_Observation` 也不是 `NicheObservation`，且需要合法 event-index/previous-state 语义）；必须将生产 runner 接线并补集成测试后才能 PASS。

### BLOCKER-3 — semantic vocabulary freeze 只有工具函数，没有运行时强制调用

- 位置：`experiments/ghost_ecology_zero_call.py:92-94`；`SemanticClusterVocabulary` 的拒绝逻辑在 `cmd_audit/repair/niche_archive.py`。
- `freeze_semantic_cluster_vocabulary` 无生产调用；`NicheEvolutionCase` 直接接收任意已构造的 `BehaviorDescriptor`。因此 dev-prefix vocabulary/schema hash 没有绑定到 runner manifest，也没有证明新 case 只能通过冻结词表进入 descriptor。现有 descriptor 的 label-like token 检查是局部防线，不等于“词表只由 dev-prefix 冻结”的端到端协议。

### BLOCKER-4 — 三分诊仍是 record/dataclass 层，未接既有 stores

- 位置：`cmd_audit/repair/incident_triage.py:248-308, 311-354`。
- `triage_incident` 只返回 `TriageDecision`；`IncidentAuditRecord` 和 `LineageLog` 只由调用者手动实例化。全仓调用搜索未发现它接入 `FailureMemoryStore.add`/`add_if_recovered`、现有 lineage/supersede store 或 quarantine+audit writer。因而 `process_fault → FailureMemory`、`state_drift → supersede + lineage`、`adversarial_poison → quarantine + audit` 仍未成为真实运行路径，不能声称机制互斥且落点可审计。

### HIGH-5 — claim registry 不是 domain × failure_type 阈值注册

- 位置：`cmd_audit/eval/surrogate_gap.py:91-127`。
- `thresholds` 类型和查找均只按 `domain`（`thresholds.get(domain)`），而不是冻结协议要求的 domain × failure type；缺 threshold 的分组在 `measure_domain_failure_gaps` 中先得到 `tau=inf` 并可能 `claim_status="pass"`，之后 registry 才可能变成 conditional/UNVERIFIED。该语义不能作为严格预注册阈值 gate，也没有对“无该域/故障类型证据”的 registry entry 建立 UNVERIFIED 条目。

### MEDIUM-6 — ITEM_STALE / metadata-decoupling 禁止仅是可选调用参数

- 位置：`experiments/ghost_ecology_zero_call.py:74-81`、`cmd_audit/counterfactual/actions.py:660-683`。
- 构造基底校验函数存在，但未被 zero-call runner 调用；`get_legal_actions` 只有在调用方显式传入 `include_item_actions=True` 和对应 config 时才拒绝。默认路径仍允许 `ITEM_STALE`，这不会误伤真实时间戳 trace，但不能证明构造 substrate 的所有入口都拒绝该 action/metadata-decoupling。

## 已核对的不变量

- `case.gold_*` / `recovery_gain`：zero-call telemetry proxy 本身只取四类 typed channel；修复后 E4b archive mutation 只用 `gold_free_gain`。shadow 仍在 offline audit reference 路径中使用，未进入 selection/context。
- descriptor/random/unkeyed：runner 映射存在，random 使用 seeded hash，三 arm 在同一 runner 和 candidate budget 中执行；但没有看到端到端 `all_frozen`/`unkeyed` gate 被 runner 实际调用，且 `map_elites_no_edges` 与 `map_elites_edges` 都映射为同一 headline `descriptor`，需要由上层确认 graph 增量不被误合并。
- niche transition 表：`ghost_ecology.py` 新增显式合法转移集合并拒绝非法跳转；单测覆盖了非法 lifecycle move。但这只证明 API，未证明 zero-call 流实际产生 transition 事件。
- vocabulary label 防线：`BehaviorDescriptor`/`SemanticClusterVocabulary` 拒绝 label-like token 和 post-outcome token；缺 runner 绑定和 schema hash manifest，故只能算局部通过。
- 账本/evaluation-only：`GhostEcology` 的 append-only/hash-chain 与 sealed mutation checks 保留；未发现本轮改坏，但 ecology window 未接线使其不构成实验证据。

## 修复摘要

- 修改 `experiments/niche_evolution_runner.py`：archive validation、成功筛选及 proposal 排序不再读取 `shadow_gain`，统一使用 `gold_free_gain`。
- 修改 `tests/experiments/test_niche_evolution_runner.py`：新增 shadow/gold-free 不一致时的 mutation 回归断言。
- 未修改用户既有的其他未提交改动，未 reset/checkout/commit。

## 测试证据

- 定向 Phase-4 相关测试：`76 passed`。
- 命令：
  `python -m pytest -q tests/experiments/test_niche_evolution_runner.py tests/repair/test_ghost_ecology.py tests/experiments/test_ghost_ecology_decoupling.py tests/eval/test_telemetry_cmis.py tests/eval/test_niche_gates.py tests/repair/test_niche_archive.py tests/adapters/test_session_log_adapter.py`
- `git diff --check`：通过。
- 另一次包含新增 Phase-3 相关测试的定向集合：`90 passed`；其中首次错误地引用了不存在的 `tests/eval/test_descriptor_policy_value.py`，该路径错误已剔除，未将其计入通过证据。

## 原子协议概念核对结论

治理 ledger/hash-chain、evaluation-only、descriptor label rejection、random seeded mapping、unkeyed single-pool mapping、ITEM_STALE/metadata-decoupling guards、typed telemetry proxy、domain/failure grouping、三分诊枚举均有局部实现或测试；但 ecology window runtime wiring、dev-prefix vocabulary end-to-end binding、三分诊既有 store 落点以及严格 domain×failure_type claim gate 缺失。因此关键承诺仍未形成可审计调用链，最终判定 BLOCKED。
