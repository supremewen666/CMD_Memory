# Routing mechanism ablation

This run reuses the frozen Qwen2.5 skill library and the matched per-stream
source posterior. It does not compile another dataset and does not make six
times as many model calls: the six routing arms share one backbone prediction
cache inside each report.

## Matrix

- models: Qwen3-14B and Meta-Llama-3.1-8B-Instruct;
- streams: three datasets by stationary/abrupt schedule;
- event-order seeds: `20260827`, `20260829`;
- arms: frozen backbone, global, global+pattern, global+pattern+local,
  full without support gate, and full Mix GHOST.

Total: 24 reports.

## Start both model jobs

Run from the repository after both vLLM endpoints are ready.

```bash
export RUN_ROOT="runs/strategy_transfer_20260828_032855"
export SKILL_LIB="$RUN_ROOT/frozen/qwen25_skill_library.json"
export Q3_SNAPSHOT="$(sha256sum ~/pretrained_lms/Qwen3-14B/config.json | awk '{print $1}')"
export LLAMA_SNAPSHOT="$(sha256sum ~/pretrained_lms/Meta-Llama-3.1-8B-Instruct/config.json | awk '{print $1}')"

mkdir -p "$RUN_ROOT/logs"

nohup setsid env \
  RUN_ROOT="$RUN_ROOT" SKILL_LIB="$SKILL_LIB" \
  MODEL_KEY=qwen3 MODEL_ID=Qwen3-14B MODEL_SNAPSHOT="$Q3_SNAPSHOT" \
  ENDPOINT=http://127.0.0.1:8001/v1 \
  bash experiments/run_spec_v03_routing_ablation.sh \
  </dev/null > "$RUN_ROOT/logs/routing-ablation-qwen3.log" 2>&1 &
echo $! > "$RUN_ROOT/routing-ablation-qwen3.pid"

nohup setsid env \
  RUN_ROOT="$RUN_ROOT" SKILL_LIB="$SKILL_LIB" \
  MODEL_KEY=llama31 MODEL_ID=Meta-Llama-3.1-8B-Instruct \
  MODEL_SNAPSHOT="$LLAMA_SNAPSHOT" ENDPOINT=http://127.0.0.1:8002/v1 \
  bash experiments/run_spec_v03_routing_ablation.sh \
  </dev/null > "$RUN_ROOT/logs/routing-ablation-llama31.log" 2>&1 &
echo $! > "$RUN_ROOT/routing-ablation-llama31.pid"
```

## Monitor and analyze

```bash
watch -n 30 '
  RUN_ROOT="runs/strategy_transfer_20260828_032855"
  echo "Qwen3 $(find "$RUN_ROOT/routing_ablation/qwen3" -name report.json 2>/dev/null | wc -l)/12"
  echo "Llama  $(find "$RUN_ROOT/routing_ablation/llama31" -name report.json 2>/dev/null | wc -l)/12"
  pgrep -af "spec_v03_stage5_9.py.*routing_ablation" || true
'

python experiments/spec_v03_analyze_routing_ablation.py \
  --input-root "$RUN_ROOT/routing_ablation" \
  --output "$RUN_ROOT/analysis/routing-ablation.json" \
  --markdown-output "$RUN_ROOT/analysis/routing-ablation.md"

python experiments/spec_v03_routing_bootstrap.py \
  --input-root "$RUN_ROOT/routing_ablation" \
  --data-root "$RUN_ROOT/data" \
  --iterations 10000 \
  --output "$RUN_ROOT/analysis/routing-ablation-family-bootstrap.json"
```

The analyzer reports paired utility, empirical regret, override rate, negative
override rate, structural safety proxy, and cost. The `support_gate` contrast
is exactly full Mix GHOST minus the otherwise full no-support-gate profile.
