# Zero-call typed enrichment — independent narrow review v5

verdict: PASS

本轮只批准 development-only zero-call typed enrichment 的启动，不批准 fresh-live materialization，也不解除 `iterations/judge_v4.md` 的正式 live blocker。未发起模型/API/network 请求，未运行正式 E2/E3/E4，未修改正式 artifact，未执行 reset/checkout/commit。

## Findings

### P1-1 — E2 未消费 enrichment 的 observed actionability mode（已修复）

`experiments/zero_call_typed_enrichment.py:102` 写入的是 graph-proven `actionability_mode_observed`，而 `experiments/ghost_ecology_zero_call.py:239-247` 原先只读旧的 `actionability_mode`，会把所有 typed probe 误报成 UNKNOWN。现已优先消费 observed 字段，并保留旧字段仅作兼容 fallback；`tests/experiments/test_ghost_ecology_zero_call.py:test_typed_feedback_consumes_observed_actionability_mode` 覆盖了旧字段错误、observed 字段正确的情形。

### P1-2 — legacy candidate duplicate 曾可被 dict 静默覆盖（已修复）

`experiments/zero_call_typed_enrichment.py:56-70` 现同时要求 candidate outcome 数量、唯一 ID 数量和 intent ID 集合完全一致；重复、缺失或多余 outcome 都 fail closed。`tests/experiments/test_zero_call_typed_enrichment.py:test_legacy_candidate_duplicate_is_rejected` 已覆盖。

### P1-3 — typed runtime flags 曾直接复制 legacy（已修复）

`experiments/zero_call_typed_enrichment.py:76-85,101` 现从本地 executed state 的 token budget 重算 `valid/rolled_back`；legacy 对应值只用于 mismatch/audit，`recovery_gain` 仍只保留在 shadow/reference 字段。篡改 legacy `valid/rolled_back/recovery_gain` 的回归测试确认 typed fields 不变；typed feedback 使用 raising `recovery_gain` property 的 telemetry 也已通过。

未发现新的 zero-call enrichment blocker。下方列出一个有意保留的边界：V4 runner 的旧 `ghost_feedback_mode=development_proxy` 在 `experiments/v4_prequential_runner.py:819-910,1135-1144` 是显式 shadow development proxy，仍会使用 shadow utility；它不属于本轮 typed zero-call claim。若启动 E4，必须使用 `prospective_deployment`，或另行修复该旧 proxy，不能把它当作 typed runtime evidence。

## 实际输入与严格绑定

- prepared input：`/Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/ghost_public_call_v1/prepared_cases.jsonl`
  - schema：`cmd-v4-live-materialization-input-v1`
  - 543 rows、131 families、represented/unseen = 383/160、4 intents/case、1086 chain pairs
  - SHA-256：`0b1b13ac255382433c37711585760e7d7842b3fe03b5fbe9124fa6f12bb9a94e`
  - preparation manifest 的 `prepared_stream_sha256` 与 `file_sha256.prepared_cases.jsonl` 同值；`runtime_uses_gold=false`
- legacy materialized reference：`/Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/ghost_public_call_v1/runs/ghost-live-20260814T075634Z/materialized/single_gpu.jsonl`
  - 543 rows、旧 `cmd-v4-prequential-case-v1` materialization
  - SHA-256：`2866229eeb9dc1224caa4bbc9e7197ff8a209bb2c169663c1c57dddb9e512f2e`
- legacy manifest：`.../single_gpu.jsonl.manifest.json`
  - manifest SHA-256：`1ad801da1a4e34cebe6512fef6ac587fae64e0e081be151b255ff46d739554e1`
  - `output_sha256` 与 legacy stream 相等，`case_count=543`
  - historical accounting：`answer_generation=3258`、`shadow_judge=3258`；没有被计入 new calls

`enrich_files` 绑定 prepared/legacy 的 case、family、probe set、legacy intent、排序后的 intent IDs、intent payload hash、context hash、graph hash；全流 case ID 必须一一对应，candidate outcome 也必须闭合覆盖 intents。prepared/legacy source hash、543-row count、legacy manifest hash conflict、missing manifest、duplicate case/outcome、existing output/manifest 均 fail closed。没有 resume 伪装：本实现是单次确定性 pass，已有 output/manifest 一律拒绝覆盖，需新路径重跑。

## typed evidence 与隔离不变量

- changed IDs 在 `experiments/zero_call_typed_enrichment.py:65-73` 由 `initial_state_from_runtime_case` 与本地 `execute_program` 后的 canonical item mapping SHA-256 diff 得到；不读取 trace matched IDs。全量 preflight 得到 726 个 changed item IDs，372 个 destructive candidate 均 target-bound 且 target match 为 true。
- target binding 只比较 frozen graph edge target 与 intent target；target match 只检查 actual changed IDs。非 destructive intent 的 binding/match 保持 `None`；缺 target 时 binding/match 也保持 unknown。
- annotation consumption、downstream confirmation、delayed confirmation、no-regression 在无真实下一事件时全为 `None`，没有默认填 `False/True`。
- `execute_program` 接收的只是 closed runtime case，并执行 runtime-only separation；enrichment 不构造 answer client、judge/verifier 或 materializer。543-case enrichment manifest 报告 `model_calls_new=0`；E2 preflight 报告 `model_calls=0`。
- `recovery_gain` 只在 `V4CandidateOutcome` shadow/reference seam 与 E2 `_shadow_utility` 使用；`deployment_feedback_v2/deployment_reward_v2`、typed E4 `_typed_runtime_feedback` 不读它。gold/post-outcome labels 未写入 enriched row；row 仍是 closed `V4PrequentialCase`，enrichment bookkeeping 只在 sidecar manifest。
- semantic vocabulary 在 E2 runner 首个 dev-prefix（前 20% case）冻结，manifest 给出 schema/hash；本次真实流 report：source=`dev-prefix`，tokens=`directed_semantic_update, semantic_uncertainty`，vocabulary SHA-256=`dd7115bebaa008007905290a6af597ef023ea06ec609fb33c5943960e7e25533`。未来 unknown token 与 post-outcome label fail closed，不扩词。
- prepared source 的 `source_attestation.json` 声称 `independent_source=true`，但 `data/ghost_live_v2/source_provenance.json` 明确为 `independent_source=false` 且说明是 benchmark transformation；因此本轮只保留 development-only/non-confirmatory 资格，不作独立 confirmatory 解释。

## 543-case read-only preflight

完整 zero-call enrichment 使用临时目录运行（临时目录不在正式 artifacts 下），结果：

- `case_count=543`、`intent_count=2172`、`mismatch_case_count=0`、`model_calls_new=0`
- historical upstream calls 明确隔离为 `3258 + 3258`，不是本次调用
- effect counts：abstain 362、annotate_conflict 539、demote 187、replace 181、suppress 4、verify 899
- target typed evidence：372/2172 candidate observed（17.127%），1800 unknown；67/131 families observed（51.145%）
- pairwise comparable coverage：0/2235 = 0.0；这是 coverage gate 的失败，而非把 unknown 当作 negative
- 临时 E2 typed-v2 gate 以 `bootstrap_samples=100` 做 preflight，退出码 2，decision=`BLOCKED_TYPED_EVIDENCE_UNAVAILABLE`；permutation/placebo 都是 `NOT_RUN_COVERAGE_BLOCKED`。candidate 0.171<0.50、pairwise 0.0<0.50，claim-bearing correlation/rank 全部保持 JSON null。

因此：E2 不值得以 claim-bearing 方式启动；可保留该 coverage audit。E3 poison-density sweep 与 enrichment 无调用路径耦合，适合独立启动。E4 当前无 follow-up/confirmation evidence，不值得以 typed claim-bearing 方式启动；若只做 development proxy，必须明确其 shadow-only 边界并使用 `prospective_deployment` 规避旧 recovery proxy 更新。

## 可执行的唯一 zero-call development command

以下输出和 manifest 在审查时均不存在；该命令不发模型请求，预期本地执行 2172 个 frozen intents，new model calls=0，无 checkpoint/resume；中断后必须换新输出路径，已有路径会 fail closed：

```bash
cd /Users/supremewen/CMD_Counterfactual_Memory_Debugger && \
python -m experiments.zero_call_typed_enrichment \
  --prepared /Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/ghost_public_call_v1/prepared_cases.jsonl \
  --legacy /Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/ghost_public_call_v1/runs/ghost-live-20260814T075634Z/materialized/single_gpu.jsonl \
  --legacy-manifest /Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/ghost_public_call_v1/runs/ghost-live-20260814T075634Z/materialized/single_gpu.jsonl.manifest.json \
  --output /Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/ghost_public_call_v1/runs/zero-call-typed-enrichment-20260820/enriched.jsonl \
  --manifest /Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/ghost_public_call_v1/runs/zero-call-typed-enrichment-20260820/enriched.manifest.json
```

该 zero-call command 没有 answer/verifier/model binding；历史 reference 的静态 model manifest 是 `artifacts/ghost_public_call_v1/ghost_live_v2/model_manifest.json`（file SHA-256 `9ce25a03b52446047d17c67b10e4b03e66642388b0da808fece1e1424fc8de8c`），仅记录旧 materialization：answer Llama hash `3824b3c15203c06ab9bac34f83a16fe6afccaae77f694c248c5d40f4bbd55735`，judge Qwen hash `9d3ee7df2021febc32b41abc32cd1a5bea67f21cd6c3cfed94d9a1dc23c118d2`。

## 修改文件

- `experiments/zero_call_typed_enrichment.py`
- `experiments/ghost_ecology_zero_call.py`
- `tests/experiments/test_zero_call_typed_enrichment.py`
- `tests/experiments/test_ghost_ecology_zero_call.py`

## 测试证据

- `python -m pytest -q tests/experiments/test_zero_call_typed_enrichment.py tests/experiments/test_ghost_ecology_zero_call.py tests/eval/test_live_followup.py tests/experiments/test_v4_live_wiring.py tests/experiments/test_v4_materialization.py tests/experiments/test_v4_prequential_runner.py`：**25 passed**
- `python -m pytest -q tests/experiments/test_ghost_ecology_decoupling.py tests/eval/test_telemetry_cmis.py tests/repair/test_niche_archive.py tests/eval/test_niche_gates.py tests/experiments/test_niche_evolution_runner.py`：**29 passed**
- `python -m pytest -q tests/experiments/test_poison_density_sweep.py`：**8 passed**
- `python -m pytest -q tests/experiments/test_phase4_wiring.py`：**5 passed**
- `python -m compileall -q cmd_audit experiments tests`：通过
- `git diff --check`：通过

结论：`verdict: PASS` 仅表示上述 zero-call development-only enrichment 已真实接线、可执行且会在 typed evidence 不足时阻断；不表示 E2 claim gate 已通过，也不表示 fresh-live materialization 已解除 `judge_v4` 阻塞。
