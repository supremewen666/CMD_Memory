# GHOST V3 需调用实验运行手册

本手册对应 `cmd-ghost-prospective-freeze-v2`。正式实验由两个阶段组成：

1. 模型调用 materialization：冻结候选后执行 answer generation 与 shadow judge，
   同时由 GHOST 产生 `ghost_selections.jsonl`；正式模式不使用 shadow outcome 更新
   GHOST。
2. 延迟部署反馈：等待预注册窗口成熟，采集目标解除、锚点非回归、复发和
   annotation 消费，再运行 identifiability audit。未成熟记录是 right-censored，
   不能当失败。

旧 `ghost_live_v1/protocol.json`、原 3,100-case dev/cal replay、零调用
materialization 均不能充当新的 confirmatory test。

## 1. 准备全新的输入和四分区清单

建立一个新的工作目录；不要复用旧 `RUN_ID`：

```bash
cd /Users/supremewen/CMD_Counterfactual_Memory_Debugger
export RUN_ID=ghost-live-$(date -u +%Y%m%dT%H%M%SZ)
export LIVE_ROOT="$PWD/artifacts/neuro_symbolic_evolution_v4/ghost_live_v2"
mkdir -p "$LIVE_ROOT"
```

由独立采集流程提供：

```text
$LIVE_ROOT/fresh_prepared_cases.jsonl
$LIVE_ROOT/ghost_dev.txt
$LIVE_ROOT/ghost_cal.txt
$LIVE_ROOT/ghost_test_rep.txt
$LIVE_ROOT/ghost_test_new.txt
```

约束：四份清单均非空且精确覆盖 case stream；dev/cal family-disjoint；
`test_rep` 只能来自 dev 已见 family 的后续新 case；`test_new` family 不得出现在
其他三个分区。冻结程序会 fail closed 检查这些条件。

独立来源证明采用以下闭合 JSON：

```json
{
  "schema_version": "cmd-ghost-independent-source-attestation-v1",
  "independent_source": true,
  "source_id": "由采集方填写",
  "collector": "由独立采集方填写",
  "collected_at_utc": "ISO-8601 UTC 时间",
  "cases_file_sha256": "fresh_prepared_cases.jsonl 的 SHA-256",
  "partition_file_sha256": {
    "ghost_dev": "ghost_dev.txt 的 SHA-256",
    "ghost_cal": "ghost_cal.txt 的 SHA-256",
    "ghost_test_rep": "ghost_test_rep.txt 的 SHA-256",
    "ghost_test_new": "ghost_test_new.txt 的 SHA-256"
  },
  "notes": "采集来源、时间窗和去重方法"
}
```

保存为 `$LIVE_ROOT/source_attestation.json`。

## 2. 冻结真实模型身份

`model_sha256` 必须是实际模型快照/权重清单的内容哈希，不能填模型名称哈希或占位
值。保存 `$LIVE_ROOT/model_manifest.json`：

```json
{
  "schema_version": "cmd-ghost-model-manifest-v1",
  "models": [
    {
      "role": "relation_instrument",
      "model_id": "实际 relation instrument model ID/revision",
      "model_sha256": "实际快照 SHA-256"
    },
    {
      "role": "intent_proposer",
      "model_id": "实际 intent proposer model ID/revision",
      "model_sha256": "实际快照 SHA-256"
    },
    {
      "role": "answer",
      "model_id": "实际 answer model ID/revision",
      "model_sha256": "实际快照 SHA-256"
    },
    {
      "role": "judge",
      "model_id": "实际 judge model ID/revision",
      "model_sha256": "实际快照 SHA-256"
    }
  ]
}
```

## 3. 冻结协议并授权唯一 RUN_ID

```bash
export GHOST_EVALUATOR="$PWD/artifacts/neuro_symbolic_evolution_v4/ghost_ecology_v3/deployment-evaluator-preaction-v1-seed24.json"

python -m experiments.ghost_live_protocol freeze \
  --root "$PWD" \
  --cases "$LIVE_ROOT/fresh_prepared_cases.jsonl" \
  --ghost-dev "$LIVE_ROOT/ghost_dev.txt" \
  --ghost-cal "$LIVE_ROOT/ghost_cal.txt" \
  --ghost-test-rep "$LIVE_ROOT/ghost_test_rep.txt" \
  --ghost-test-new "$LIVE_ROOT/ghost_test_new.txt" \
  --attestation "$LIVE_ROOT/source_attestation.json" \
  --model-manifest "$LIVE_ROOT/model_manifest.json" \
  --evaluator "$GHOST_EVALUATOR" \
  --preparation-manifest "$LIVE_ROOT/preparation_manifest.json" \
  --candidate-budget 4 \
  --output "$LIVE_ROOT/protocol.json" \
  --access-ledger "$LIVE_ROOT/access.jsonl"

python -m experiments.ghost_live_protocol authorize-test \
  --protocol "$LIVE_ROOT/protocol.json" \
  --access-ledger "$LIVE_ROOT/access.jsonl" \
  --authorizer "实际治理者身份" \
  --run-id "$RUN_ID" \
  --output "$LIVE_ROOT/first_test_authorization.json"
```

授权绑定唯一 `RUN_ID`；不能复制给下一次运行。代码、数据、四分区、模型、evaluator
或候选预算任一改变，都必须生成全新的协议和授权。

## 4. 单张 A100：nohup 启动需调用实验

`v4_single_gpu` 会在一张 A100 上处理全部冻结 case，不做双卡 hash 分片。下面命令
同时使用 `nohup` 和仓库内 supervisor；外层 shell 退出后，状态仍写入不可复用的
JSONL 控制目录。

```bash
export CMD_V4_SOURCE_CASES="$LIVE_ROOT/fresh_prepared_cases.jsonl"
export CMD_V4_PREPARATION_MANIFEST="$LIVE_ROOT/preparation_manifest.json"
export CMD_V4_GHOST_EVALUATOR="$GHOST_EVALUATOR"
export CMD_V4_GHOST_PROTOCOL="$LIVE_ROOT/protocol.json"
export CMD_V4_GHOST_AUTHORIZATION="$LIVE_ROOT/first_test_authorization.json"
export CMD_V4_GHOST_ACCESS_LEDGER="$LIVE_ROOT/access.jsonl"
export CMD_V4_MODEL_MANIFEST="$LIVE_ROOT/model_manifest.json"
export CMD_V4_CANDIDATE_BUDGET=4
export CMD_GPU0_PORT_BASE=8000

nohup ./run_remaining_experiments.sh \
  --role v4_single_gpu --run-id "$RUN_ID" --detach \
  >"$LIVE_ROOT/${RUN_ID}.single-gpu.launch.log" 2>&1 &
```

JSONL 流式监控：

```bash
./run_remaining_experiments.sh --role monitor --run-id "$RUN_ID"

# 一次性 JSON snapshot
./run_remaining_experiments.sh --role monitor --run-id "$RUN_ID" --once
```

`v4_single_gpu` 为 `completed` 后启动 merge/eight-arm prequential：

```bash
nohup ./run_remaining_experiments.sh \
  --role v4_merge --run-id "$RUN_ID" --detach \
  >"$LIVE_ROOT/${RUN_ID}.merge.launch.log" 2>&1 &
```

主要输出：

```text
artifacts/neuro_symbolic_evolution_v4/runs/$RUN_ID/materialized/*.jsonl
artifacts/neuro_symbolic_evolution_v4/runs/$RUN_ID/cases.merged.jsonl
artifacts/neuro_symbolic_evolution_v4/runs/$RUN_ID/prequential/arm_outcomes.jsonl
artifacts/neuro_symbolic_evolution_v4/runs/$RUN_ID/prequential/ghost_selections.jsonl
artifacts/neuro_symbolic_evolution_v4/runs/$RUN_ID/prequential/progress.jsonl
artifacts/neuro_symbolic_evolution_v4/runs/$RUN_ID/prequential/report.json
```

最终 report 分开记录：

- `model_calls` / `upstream_materialization_model_calls`：answer + judge 的逻辑调用数；
- `runner_model_calls=0`：CPU prequential runner 本身不调模型；
- `ghost_pending_feedback_count`：尚待真实延迟结果的 selection 数。

## 5. 采集并验证真实延迟反馈

反馈 JSONL 必须逐行绑定 `ghost_selections.jsonl` 的 selection、intent、skill、probe、
repair effect 和 pre-action prior。每个 skill 只填写其注册 probe 适用的信号；不适用
信号必须为 `null`。窗口未结束时设置 `matured=false` 且四个结果信号全为 `null`。

窗口成熟后执行：

```bash
python -m experiments.ghost_live_protocol audit-feedback \
  --input "$LIVE_ROOT/${RUN_ID}.delayed_feedback.jsonl" \
  --protocol "$LIVE_ROOT/protocol.json" \
  --selections "$PWD/artifacts/neuro_symbolic_evolution_v4/runs/$RUN_ID/prequential/ghost_selections.jsonl" \
  --bootstrap-samples 10000 \
  --seed 24 \
  --output "$LIVE_ROOT/${RUN_ID}.feedback_audit.json"
```

门槛：至少 30 条成熟反馈、10 个 family；family correlation ≥ 0.20，单侧 bootstrap
下界 ≥ 0.10，pairwise concordance ≥ 0.55。未达到样本量或门槛时状态保持
`BLOCKED_*`，不得把 shadow/materialized outcome 补入 live feedback。
