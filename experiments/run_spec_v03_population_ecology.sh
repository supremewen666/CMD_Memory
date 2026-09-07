#!/usr/bin/env bash
set -euo pipefail

: "${RUN_ROOT:?set RUN_ROOT}"
: "${SKILL_LIB:?set SKILL_LIB}"

SEED="${SEED:-20260827}"
STREAMS="${STREAMS:-halumem_stationary halumem_abrupt_process_state_poison memfail_stationary memfail_abrupt_process_state_poison memtracebench_stationary memtracebench_abrupt_process_state_poison}"

for STREAM in $STREAMS; do
  DATA="$RUN_ROOT/data/$STREAM"
  OUT="$RUN_ROOT/population_ecology/$STREAM"
  mkdir -p "$OUT"
  if jq -e '
    [.results.stage6[] | select(.status == "READY_NO_MODEL_RESULTS")] | length == 8
  ' "$OUT/report.json" >/dev/null 2>&1; then
    echo "[SKIP] $STREAM"
    continue
  fi
  echo "[RUN] $STREAM"
  python experiments/spec_v03_stage5_9.py \
    --runtime-cases "$DATA/runtime_cases.json" \
    --event-order "$DATA/event_order_manifest.json" \
    --split-manifest "$DATA/split_manifest.json" \
    --include-split D_lifecycle \
    --split-audit-output "$OUT/split-audit.json" \
    --output "$OUT/report.json" \
    --run-id "population-ecology-$STREAM" \
    --model-id Qwen2.5-14B-Instruct:frozen-replay \
    --stage stage6 \
    --candidate-provider frozen-replay \
    --skill-library "$SKILL_LIB" \
    --seed "$SEED" \
    --track controlled_a1
done

echo "[COMPLETE] population ecology frozen replay"
