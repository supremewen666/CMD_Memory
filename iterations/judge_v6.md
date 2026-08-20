# Session-lineage v2 / normalized claude-tap seam review — v6

本轮按 `research-review` skill 独立复核了冻结协议、实际调用路径、脏 worktree 与新增测试；没有信任 `ml_res.md` 的测试计数。未运行正式 E2/E3/E4，未联网、未调用模型，也没有声称存在真实 `.ctap`/normalized claude-tap 样本。

## Verdict

`PASS_ZERO_CALL_AUDIT_ONLY`

代码层面没有剩余必须先修的阻塞。允许启动：

- E2/E4 的 zero-call、coverage-gated audit（coverage 不足仍必须输出 null claim stats / NOT_RUN controls）；
- E3 独立 deterministic poison-density zero-call experiment。

真实 claude-tap follow-up claim 仍是 `UNVERIFIED/BLOCKED`：仓库没有真实 normalized export、稳定 `.ctap` 样本或 selection/exposure source，不能用 fixture、trace 数或 legacy shadow fields 冒充 coverage。

## Findings and fixes

### P1 — 原始 lineage event contract 类型边界不足，已修复

`cmd_audit/adapters/session_log.py:142-200` 现在对 source schema 采用 closed allowlist；core IDs、optional IDs、bool/int/finite numeric 字段、ID 序列、registered confirmation signal 和未知字段均严格校验。每个 event 必须有 state hash；parent 必须有更早同链 parent state hash。raw payload hash 使用 canonical sorted JSON，未将 gold/recovery/post-outcome 字段纳入可接受输入。

测试覆盖 bool-as-int、非字符串 ID、空/未知 signal、缺 state hash、未知 schema、gold/recovery 字段（`tests/adapters/test_session_lineage_v2.py:32-55,152-160`）。

### P1 — branch/fork/配对链约束，已确认并补测

`cmd_audit/adapters/session_log.py:212-279` 强制唯一 root、事件顺序、parent/state continuity；跨 branch 只允许显式 fork root；tool result 必须绑定更早同 branch tool call，tool use/result IDs 不得重复；response/request IDs 不得重复，`previous_response_id` 必须指向更早同 branch response。没有可配对的 result 或 response 不会被静默计为 observed typed evidence。

### P1 — follow-up projection 的前瞻/跨 intent 风险，已修复

`cmd_audit/adapters/session_log.py:353-409`：selection 必须绑定真实 branch-local selected event 与 repair intent；exposure window 必须是 selection 声明窗口、严格晚于 effective-after；读取只限同 branch、窗口内、generic 或同 intent 的事件。annotation consumption 只能走 `created_annotation_ids → annotation_item_bindings → later retrieved/context IDs`，绑定必须在 effective-after 前已创建，annotation ID 不会当作 item ID。future/other-intent 事件不再贡献 evidence。

delayed confirmation 只接受 registered typed signals；无 signal 为 `None`，rollback/target-loss/guard failure 为 `False`。no-regression 只有 registered usage opportunities 且全窗口 guard passed 才为 `True`；无 exposure、unknown guard 或 exposure end 超过 branch trace 末端均保持 `None`。

### P1 — sidecar/manifest 统计与写入纪律，已修复

`cmd_audit/adapters/session_lineage_cli.py:29-180` 提供 selection-driven `--selections` 输入；无 selections 时显式 `coverage_audit_empty=true`，不把 trace 数当 selection coverage。manifest 记录 source/selection/output SHA-256、独立 schema versions、selection/evidence/unknown/confirmed counts、candidate/family/pairwise coverage 的真实 observed/unknown 语义、`real_claude_tap_coverage=UNVERIFIED`、`model_calls=0` 与 `network_calls=0`。

output 和 manifest 先写同目录临时文件，再用 no-overwrite publish；若 staging/publish 失败，不留下误导性正式半成品。refuse-overwrite、hash、empty audit 与 write failure 均有测试（`tests/adapters/test_session_lineage_v2.py:163-196`）。

### P1 — V4 merge seam，已确认 fail-closed

`cmd_audit/adapters/session_lineage_cli.py:184-246` 只按 case/session、family、selected pre-action event、effective-after、branch 与 candidate repair intent 合并到已有三值 typed fields；selection/evidence mapping closed，schema、candidate identity、future event、跨 family/candidate 均拒绝。`confirmed` 只接受 `bool | None`，不会 bool 强转；gold/recovery/shadow 不被读取为 runtime feedback，provenance 只保存 lineage refs。V4 case 重新经过 `V4PrequentialCase.from_mapping()`。

E2 fixture 有两个不同 candidate effects 的同-case comparable observations（`tests/experiments/test_ghost_ecology_zero_call.py:108-132`）；lineage CLI fixture 同时覆盖两个 branch/intent selection。

### P0 — 真实 trace source 缺失（预期的外部边界，未伪造）

当前没有真实 normalized claude-tap export 或稳定 `.ctap` 样本。因此本轮只验证项目自有 normalized contract，不宣称 claude-tap 原格式已解析；任何真实 follow-up claim 仍不得启动。legacy v1 session API 保持不变，并由 `tests/adapters/test_session_log_adapter.py` 回归覆盖。

## Evidence and input binding

已有 zero-call substrate（仅供后续 coverage-gated audit，不在本轮运行）：

- prepared input：`/Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/ghost_public_call_v1/prepared_cases.jsonl`
  - 543 rows
  - SHA-256 `0b1b13ac255382433c37711585760e7d7842b3fe03b5fbe9124fa6f12bb9a94e`
- legacy materialized shadow reference：`/Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/ghost_public_call_v1/runs/ghost-live-20260814T075634Z/materialized/single_gpu.jsonl`
  - 543 rows
  - SHA-256 `2866229eeb9dc1224caa4bbc9e7197ff8a209bb2c169663c1c57dddb9e512f2e`
  - historical answer/shadow calls remain provenance only; they are not new calls and never flow into typed runtime feedback.

本轮确认新 v6 output 目录均不存在，未生成正式 artifact 或临时 preflight 文件。

## Exact startup boundary for experiment agent

以下 output 路径必须保持不存在，命令只能按既有 runner 执行；本审查未执行它们：

```bash
cd /Users/supremewen/CMD_Counterfactual_Memory_Debugger && \
python -m experiments.zero_call_typed_enrichment \
  --prepared /Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/ghost_public_call_v1/prepared_cases.jsonl \
  --legacy /Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/ghost_public_call_v1/runs/ghost-live-20260814T075634Z/materialized/single_gpu.jsonl \
  --legacy-manifest /Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/ghost_public_call_v1/runs/ghost-live-20260814T075634Z/materialized/single_gpu.jsonl.manifest.json \
  --output /Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/ghost_public_call_v1/runs/zero-call-typed-enrichment-20260820-v6/enriched.jsonl \
  --manifest /Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/ghost_public_call_v1/runs/zero-call-typed-enrichment-20260820-v6/enriched.manifest.json

python -m experiments.ghost_ecology_zero_call \
  --cases /Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/ghost_public_call_v1/runs/zero-call-typed-enrichment-20260820-v6/enriched.jsonl \
  --output /Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/ghost_public_call_v1/runs/zero-call-typed-enrichment-20260820-v6/e2-typed.json \
  --bootstrap-samples 10000 --seed 24 --decoupling-seed 91 --feedback-version typed-v2
```

Expected E2 behavior is enrichment `model_calls_new=0`, followed by coverage-gated `BLOCKED_TYPED_EVIDENCE_UNAVAILABLE`, null claim statistics and `NOT_RUN_COVERAGE_BLOCKED` controls. The preregistered 0.50 gate must not be relaxed.

E3 remains independently permitted:

```bash
cd /Users/supremewen/CMD_Counterfactual_Memory_Debugger && \
python -m experiments.poison_density_sweep \
  --output /Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/ghost_public_call_v1/runs/poison-density-sweep-20260820-v6/report.json \
  --recall-size 10 --max-density 0.9 --threshold 0.6 --cases-per-cell 5
```

E4 may run only as zero-call `prospective_deployment` development-only evaluation with the existing `v4_prequential_runner`; it is not evidence for the blocked real typed follow-up claim. No command is authorized for a real follow-up claim until a real source and hash-bound selection/exposure records are supplied.

## Tests

Exact command run:

```bash
python -m pytest -q \
  tests/adapters/test_session_lineage_v2.py \
  tests/adapters/test_session_log_adapter.py \
  tests/eval/test_live_followup.py \
  tests/experiments/test_ghost_ecology_zero_call.py \
  tests/experiments/test_zero_call_typed_enrichment.py \
  tests/experiments/test_v4_live_wiring.py \
  tests/experiments/test_v4_materialization.py \
  tests/experiments/test_v4_prequential_runner.py \
  tests/experiments/test_ghost_ecology_decoupling.py \
  tests/eval/test_telemetry_cmis.py \
  tests/repair/test_niche_archive.py \
  tests/eval/test_niche_gates.py \
  tests/experiments/test_niche_evolution_runner.py \
  tests/experiments/test_poison_density_sweep.py \
  tests/experiments/test_phase4_wiring.py
```

Result: **103 passed in 0.70s**.

- `python -m compileall -q cmd_audit/adapters cmd_audit/eval experiments tests`: passed
- `git diff --check`: passed
- no formal E2/E3/E4, model, network, or `.ctap` calls
- no temporary preflight artifact remains; v6 output directories remain absent

## Modified files

- `cmd_audit/adapters/session_log.py`
- `cmd_audit/adapters/session_lineage_cli.py`
- `tests/adapters/test_session_lineage_v2.py`
- `tests/experiments/test_ghost_ecology_zero_call.py`
- `ml_res.md` (corrected review count to 103; no experiment result fabricated)

下一步只允许 coverage-gated E2/E4 audit 与独立 E3 zero-call 实验；真实 claude-tap follow-up claim 继续保持 `UNVERIFIED/BLOCKED`。

verdict: PASS_ZERO_CALL_AUDIT_ONLY
