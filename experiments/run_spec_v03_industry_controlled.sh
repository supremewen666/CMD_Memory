#!/usr/bin/env bash
set -euo pipefail

: "${RUN_ROOT:?set RUN_ROOT to the frozen experiment root}"
: "${INDUSTRY_ADAPTERS_CONFIG:?set INDUSTRY_ADAPTERS_CONFIG to the closed adapter JSON}"

SEED="${SEED:-20260827}"
SYSTEM_MAX_LLM_CALLS="${SYSTEM_MAX_LLM_CALLS:-20}"
SYSTEM_MAX_INPUT_TOKENS="${SYSTEM_MAX_INPUT_TOKENS:-100000}"
SYSTEM_MAX_OUTPUT_TOKENS="${SYSTEM_MAX_OUTPUT_TOKENS:-4096}"
SYSTEM_MAX_WALL_SECONDS="${SYSTEM_MAX_WALL_SECONDS:-300}"
SYSTEM_MAX_GPU_SECONDS="${SYSTEM_MAX_GPU_SECONDS:-300}"

for DATASET in halumem memfail memtracebench; do
  for SCHEDULE in stationary abrupt_process_state_poison; do
    STREAM="${DATASET}_${SCHEDULE}"
    DATA="$RUN_ROOT/data/$STREAM"
    OUT="$RUN_ROOT/industry_controlled/$STREAM"
    REPORT="$OUT/report.json"
    mkdir -p "$OUT"

    if jq -e '
      ([.results.stage9[] | select(.system_id == "lightmem" or .system_id == "lycheemem" or .system_id == "mem0")] | length > 0) and
      ([.results.stage9[] | select(.system_id == "lightmem" or .system_id == "lycheemem" or .system_id == "mem0")] | all(.adapter_status == "OK"))
    ' "$REPORT" >/dev/null 2>&1; then
      echo "[SKIP] $STREAM"
      continue
    fi

    echo "[RUN] $STREAM"
    python experiments/spec_v03_stage5_9.py \
      --runtime-cases "$DATA/runtime_cases.json" \
      --event-order "$DATA/event_order_manifest.json" \
      --split-manifest "$DATA/split_manifest.json" \
      --include-split T_final \
      --split-audit-output "$OUT/split_audit.json" \
      --output "$REPORT" \
      --run-id "industry-controlled-$STREAM-$SEED" \
      --model-id shared-controlled-repair-head \
      --seed "$SEED" \
      --track controlled_a1 \
      --stage stage9 \
      --industry-adapters-config "$INDUSTRY_ADAPTERS_CONFIG" \
      --system-max-llm-calls "$SYSTEM_MAX_LLM_CALLS" \
      --system-max-input-tokens "$SYSTEM_MAX_INPUT_TOKENS" \
      --system-max-output-tokens "$SYSTEM_MAX_OUTPUT_TOKENS" \
      --system-max-wall-seconds "$SYSTEM_MAX_WALL_SECONDS" \
      --system-max-gpu-seconds "$SYSTEM_MAX_GPU_SECONDS"
  done
done

echo "[COMPLETE] controlled industry systems"
