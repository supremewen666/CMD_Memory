#!/usr/bin/env bash
set -euo pipefail

: "${RUN_ROOT:?set RUN_ROOT to the existing strategy-transfer run root}"
: "${SKILL_LIB:?set SKILL_LIB to the frozen Qwen2.5 skill library}"
: "${MODEL_KEY:?set MODEL_KEY, for example qwen3 or llama31}"
: "${MODEL_ID:?set MODEL_ID to the served model name}"
: "${MODEL_SNAPSHOT:?set MODEL_SNAPSHOT to the pinned model manifest digest}"
: "${ENDPOINT:?set ENDPOINT to the OpenAI-compatible /v1 endpoint}"

ROUTING_SEEDS="${ROUTING_SEEDS:-20260827 20260829}"
DATASETS="${DATASETS:-halumem memfail memtracebench}"
SCHEDULES="${SCHEDULES:-stationary abrupt_process_state_poison}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$RUN_ROOT/routing_ablation/$MODEL_KEY}"

mkdir -p "$OUTPUT_ROOT"

for seed in $ROUTING_SEEDS; do
  for dataset in $DATASETS; do
    for schedule in $SCHEDULES; do
      stream="${dataset}_${schedule}"
      runtime_cases="$RUN_ROOT/data/$stream/runtime_cases.json"
      event_order="$RUN_ROOT/v2_orders/seed_${seed}/$stream/event_order_manifest.json"
      source_router="$RUN_ROOT/source/$stream/router_snapshot.json"
      output_dir="$OUTPUT_ROOT/seed_${seed}/$stream"
      report="$output_dir/report.json"

      if jq -e '
        [.results.stage5.arms[]
         | select(.status == "COMPLETE")
         | .arm]
        | unique
        | length == 6
      ' "$report" >/dev/null 2>&1; then
        echo "[SKIP] $MODEL_KEY seed=$seed stream=$stream"
        continue
      fi

      for required in "$runtime_cases" "$event_order" "$source_router" "$SKILL_LIB"; do
        if [[ ! -s "$required" ]]; then
          echo "[ERROR] missing required input: $required" >&2
          exit 2
        fi
      done

      mkdir -p "$output_dir"
      echo "[RUN] $MODEL_KEY seed=$seed stream=$stream"
      python experiments/spec_v03_stage5_9.py \
        --runtime-cases "$runtime_cases" \
        --event-order "$event_order" \
        --output "$report" \
        --router-snapshot-output "$output_dir/router_snapshot.json" \
        --run-id "routing-ablation-${MODEL_KEY}-${seed}-${stream}" \
        --model-id "$MODEL_ID" \
        --model-snapshot "$MODEL_SNAPSHOT" \
        --endpoint "$ENDPOINT" \
        --stage stage5 \
        --backbone-provider vllm \
        --feedback-provider development-structural \
        --skill-library "$SKILL_LIB" \
        --initial-router-snapshot "$source_router" \
        --seed "$seed" \
        --track controlled_a1 \
        --max-requests 1000 \
        --max-total-tokens 10000000 \
        --max-output-tokens 1024 \
        --temperature 0 \
        --stage5-arm routing_frozen_backbone \
        --stage5-arm routing_global \
        --stage5-arm routing_global_pattern \
        --stage5-arm routing_global_pattern_local \
        --stage5-arm routing_full_no_support_gate \
        --stage5-arm mix_ghost
    done
  done
done

echo "[COMPLETE] $MODEL_KEY routing ablation"
