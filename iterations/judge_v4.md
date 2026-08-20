# Formal live-materialization execution gate — narrow review v4

verdict: BLOCKED

本轮只做正式调用前窄审查，未发起模型/API/network 请求，未运行正式 materialization，未删除 artifact、reset、checkout 或 commit。

## Findings

### P0-1 — materialized recovery/gold shadow 曾反流到 runtime update（已修复，本轮验证）

`experiments/v4_live_materialization.py:347-356` 正确地把 `recovery` 放在 candidate outcome 中作为 shadow measurement；原先 `experiments/v4_prequential_runner.py:715-742` 在 `ghost_dev` 更新时却直接把 `selected.recovery_gain` 构造为 `OutcomeObservation.recovery_gain`。本轮已改为 `_typed_runtime_feedback`：只由 target/actionability/follow-up typed evidence 和执行 telemetry 生成 reward；unknown evidence abstains，不更新。`recovery_gain` 仍仅用于结果/离线 reference。回归 fixture 已补充 observed target binding/match，验证正常 typed update。

### P1-2 — changed-item 旧实现曾用 trace matched IDs（已修复，已补测）

原 `_changed_item_ids` 使用 `RepairTraceEvent.matched_item_ids`，会把 no-op match 当成实际变化。现已改为比较 initial/executed `RepairStateItem.as_mapping()` 的 canonical SHA-256，包含 added/removed IDs；provenance 改为 `initial_executed_item_content_hash_diff`，并补充 no-op vs disposition-change 回归测试。

### P1-3 — checkpoint/resume 未实现

`experiments/v4_materialization.py:116-129` 只要 output 或 progress 存在就拒绝，并没有 `--resume` 或从已完成 case checkpoint 继续的路径。失败后只能留下 partial output/progress，无法按任务要求 checkpoint/resume；当前只能 fail closed 重开一个全新 output。

### P1-4 — manifest 的 model-call accounting 是预估值，不是实际计数

`experiments/v4_materialization.py:337-358` 按 `len(intents)+len(chain_pairs)` 预填 answer/judge 次数；backend 没有调用计数接口，`V4LiveMaterializer` 也没有把 answer/verifier retry/timeout/attempt 数回传给 manifest。`score_answer_with_verifier` 可能包含实际 judge attempts，因此 manifest 不能证明真实 model-call accounting、timeout 或 truncation。

### P1-5 — 当前正式模板命令不可执行

`ml_res.md:217-223` 使用 `--input`，但实际 CLI 要求 `materialize --source ... --lane ... --output ... --progress ... --backend ...`（`experiments/v4_materialization.py:312-359`）。因此冻结文档中的“唯一正式命令”当前是错误命令，不能作为 execution gate 的 PASS 证据。

### P1-6 — 当前输入/来源与 confirmatory live 资格冲突，且模型运行配置缺失

已定位实际可解析的 prepared input：

- 输入：`/Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/ghost_public_call_v1/prepared_cases.jsonl`
- schema：`cmd-v4-live-materialization-input-v1`
- rows：543；families：131；represented/unseen：383/160；candidate budget：4
- SHA-256：`0b1b13ac255382433c37711585760e7d7842b3fe03b5fbe9124fa6f12bb9a94e`
- validation：`artifacts/ghost_public_call_v1/prepared_cases.validation.json`，SHA-256 `35e9c641dd206a9add712305195b030d0847c99fd44b981760d3b2a7620559a8`

但 `data/ghost_live_v2/source_provenance.json` 明确写明 `independent_source=false`；而 `artifacts/ghost_public_call_v1/ghost_live_v2/source_attestation.json` 另行声称 true，且 notes 仍是模板文字。两者不能作为无歧义的 confirmatory source binding。另有既存 materialized output，不能复用为正式输入结果：`artifacts/ghost_public_call_v1/runs/ghost-live-20260814T075634Z/materialized/single_gpu.jsonl`（schema v1，非本轮 typed v2）。

已定位静态 model manifest：

- `artifacts/ghost_public_call_v1/ghost_live_v2/model_manifest.json`
- file SHA-256：`9ce25a03b52446047d17c67b10e4b03e66642388b0da808fece1e1424fc8de8c`
- answer：`meta-llama/Llama-3.1-8B-Instruct`, hash `3824b3c15203c06ab9bac34f83a16fe6afccaae77f694c248c5d40f4bbd55735`
- judge：`Qwen/Qwen2.5-7B-Instruct`, hash `9d3ee7df2021febc32b41abc32cd1a5bea67f21cd6c3cfed94d9a1dc23c118d2`

但当前环境没有显式 `LLM_BASE_URL`、`LLM_MODEL`、`LLM_JUDGE_BASE_URL`、`LLM_JUDGE_MODEL`；本窄审查也没有发现本地 snapshot 文件可核验上述 model hashes。`V4LiveMaterializer.__init__` 会按 `assert_live_llm_env_configured()` fail closed，这一阻塞不能用默认 localhost/Ollama 或静态 manifest 猜测绕过。

## 已修改内容

- `experiments/v4_live_materialization.py`：changed IDs 改为 initial/executed item content hash diff，并更新 provenance source/algorithm。
- `experiments/v4_prequential_runner.py`：runtime update 改为 typed-feedback-only，unknown follow-up/target evidence 不更新。
- `tests/experiments/test_v4_live_wiring.py`：新增 no-op trace match 不计为 changed、真实 disposition change 计入 changed 的测试。
- `tests/experiments/test_v4_prequential_runner.py`：fixture 明确提供 target binding/match typed evidence，避免测试依赖 shadow recovery。

未修复 checkpoint/resume 或 accounting，因为这些仍需正式协议接线；本轮 execution gate 仍必须阻断。

## Follow-up / delayed evidence 审查

- `FollowupBranchTracker` enforcing branch ID and `selected_event_index < observed_at_event_index`；`reject_cross_family` 存在，现有单测覆盖 branch/family 拒绝。
- live materializer 没有伪造 annotation consumption、delayed confirmation、no-regression；当前输入没有稳定 next-event retrieval/use linkage，typed fields 保持 `None`，这是正确的 unknown 行为。
- chain attempts 不是后续真实事件，不能被当作 delayed confirmation；当前代码未将其写入这些 typed follow-up fields。

## 唯一正式命令（仅在阻塞项解除后执行；本轮未运行）

输出路径在审查时不存在：
`/Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/live_materialization_formal_20260820/single_gpu.jsonl`

```bash
cd /Users/supremewen/CMD_Counterfactual_Memory_Debugger && \
python -m experiments.v4_materialization materialize \
  --source /Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/ghost_public_call_v1/prepared_cases.jsonl \
  --lane single_gpu \
  --output /Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/live_materialization_formal_20260820/single_gpu.jsonl \
  --progress /Users/supremewen/CMD_Counterfactual_Memory_Debugger/artifacts/live_materialization_formal_20260820/typed-progress.jsonl \
  --backend experiments.v4_live_materialization:live_backend
```

输入行数统计：2172 candidate intents + 1086 chain pairs = 3258 scored states；静态模板因此预期至少 3258 answer-generation 与 3258 judge-scoring logical states，但由于当前 accounting 非实际计数，不能把它当作已确认 call 总数。任何 formal execution 前必须修复 accounting、确认模型 endpoint/snapshot hashes，并解决 source attestation 冲突。

## 定向验证

```text
python -m pytest -q \
  tests/experiments/test_v4_live_wiring.py \
  tests/eval/test_live_followup.py \
  tests/experiments/test_v4_materialization.py \
  tests/experiments/test_v4_prequential_runner.py \
  tests/experiments/test_ghost_live_protocol.py \
  tests/experiments/test_run_memory_evolution_v4.py \
  tests/eval/test_telemetry_cmis.py \
  tests/experiments/test_ghost_ecology_zero_call.py
```

结果：`37 passed in 0.28s`。

- `python -m compileall -q cmd_audit experiments`：通过。
- `git diff --check`：通过。
- `tests/experiments/test_v4_live_materialization.py`：仓库不存在该文件，不能虚报为已执行；live materializer 覆盖来自 `test_v4_materialization.py` 中的既有路径以及新增 helper 回归测试。
