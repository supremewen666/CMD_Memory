#!/usr/bin/env bash
set -euo pipefail

: "${RUN_ROOT:?set RUN_ROOT}"
: "${MODEL_KEY:?set MODEL_KEY}"
: "${SKILL_LIB:?set SKILL_LIB}"

SEED="${SEED:-20260827}"
STREAMS="${STREAMS:-halumem_stationary halumem_abrupt_process_state_poison memfail_stationary memfail_abrupt_process_state_poison memtracebench_stationary memtracebench_abrupt_process_state_poison}"

for STREAM in $STREAMS; do
  DATA="$RUN_ROOT/data/$STREAM"
  SELECTION="$RUN_ROOT/matched_allarms/$MODEL_KEY/seed_${SEED}/$STREAM/report.json"
  OUT="$RUN_ROOT/sealed_governance/$MODEL_KEY/$STREAM/report.json"
  if jq -e '.variant_metrics | length == 7' "$OUT" >/dev/null 2>&1; then
    echo "[SKIP] $MODEL_KEY $STREAM"
    continue
  fi
  test -s "$SELECTION"
  mkdir -p "$(dirname "$OUT")"
  echo "[RUN] $MODEL_KEY $STREAM"
  python experiments/spec_v03_sealed_governance.py \
    --runtime-cases "$DATA/runtime_cases.json" \
    --event-order "$DATA/event_order_manifest.json" \
    --split-manifest "$DATA/split_manifest.json" \
    --include-split T_anchor \
    --include-split T_final \
    --sealed-sidecar "$DATA/sealed_evaluator_sidecar.json" \
    --selection-report "$SELECTION" \
    --skill-library "$SKILL_LIB" \
    --run-id "sealed-governance-$MODEL_KEY-$STREAM" \
    --output "$OUT"
done

echo "[COMPLETE] $MODEL_KEY sealed governance"
