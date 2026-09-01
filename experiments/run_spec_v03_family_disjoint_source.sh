#!/usr/bin/env bash
set -euo pipefail

: "${RUN_ROOT:?set RUN_ROOT}"
: "${SKILL_LIB:?set SKILL_LIB to the frozen Qwen2.5 discovery library}"
: "${Q25_SNAPSHOT:?set Q25_SNAPSHOT}"

ENDPOINT="${Q25_ENDPOINT:-http://127.0.0.1:8000/v1}"
SEED="${SEED:-20260827}"
OUT_ROOT="$RUN_ROOT/family_disjoint"
SOURCE_LIB="$OUT_ROOT/source_skill_library.json"
mkdir -p "$OUT_ROOT/source" "$OUT_ROOT/audits"

if ! test -s "$SOURCE_LIB"; then
  python experiments/spec_v03_filter_skill_library.py \
    --skill-library "$SKILL_LIB" \
    --runtime-cases "$RUN_ROOT/data/halumem_stationary/runtime_cases.json" \
    --split-manifest "$RUN_ROOT/data/halumem_stationary/split_manifest.json" \
    --runtime-cases "$RUN_ROOT/data/memfail_stationary/runtime_cases.json" \
    --split-manifest "$RUN_ROOT/data/memfail_stationary/split_manifest.json" \
    --runtime-cases "$RUN_ROOT/data/memtracebench_stationary/runtime_cases.json" \
    --split-manifest "$RUN_ROOT/data/memtracebench_stationary/split_manifest.json" \
    --include-split D_skill \
    --output "$SOURCE_LIB" \
    --audit-output "$OUT_ROOT/audits/source-skill-library.json"
fi

for DATASET in halumem memfail memtracebench; do
  for SCHEDULE in stationary abrupt_process_state_poison; do
    STREAM="${DATASET}_${SCHEDULE}"
    DATA="$RUN_ROOT/data/$STREAM"
    OUT="$OUT_ROOT/source/$STREAM"
    mkdir -p "$OUT"
    if jq -e '.router_snapshots.mix_ghost' "$OUT/router_snapshot.json" >/dev/null 2>&1; then
      echo "[SKIP] source $STREAM"
      continue
    fi
    echo "[RUN] source $STREAM"
    python experiments/spec_v03_stage5_9.py \
      --runtime-cases "$DATA/runtime_cases.json" \
      --event-order "$DATA/event_order_manifest.json" \
      --split-manifest "$DATA/split_manifest.json" \
      --include-split D_router \
      --split-audit-output "$OUT/split-audit.json" \
      --output "$OUT/report.json" \
      --router-snapshot-output "$OUT/router_snapshot.json" \
      --run-id "family-disjoint-source-$STREAM" \
      --model-id Qwen2.5-14B-Instruct \
      --model-snapshot "$Q25_SNAPSHOT" \
      --endpoint "$ENDPOINT" \
      --stage stage5 \
      --stage5-arm mix_ghost \
      --backbone-provider vllm \
      --feedback-provider development-structural \
      --skill-library "$SOURCE_LIB" \
      --seed "$SEED" \
      --track controlled_a1 \
      --max-requests 1000 \
      --max-total-tokens 10000000 \
      --max-output-tokens 1024 \
      --temperature 0
  done
done

echo "[COMPLETE] family-disjoint source library and six source posteriors"
