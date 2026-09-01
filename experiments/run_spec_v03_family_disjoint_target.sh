#!/usr/bin/env bash
set -euo pipefail

: "${RUN_ROOT:?set RUN_ROOT}"
: "${MODEL_KEY:?set MODEL_KEY, for example qwen3 or llama31}"
: "${MODEL_ID:?set MODEL_ID}"
: "${MODEL_SNAPSHOT:?set MODEL_SNAPSHOT}"
: "${ENDPOINT:?set ENDPOINT}"

SEED="${SEED:-20260827}"
OUT_ROOT="$RUN_ROOT/family_disjoint"
SOURCE_LIB="$OUT_ROOT/source_skill_library.json"
test -s "$SOURCE_LIB"

for DATASET in halumem memfail memtracebench; do
  for SCHEDULE in stationary abrupt_process_state_poison; do
    STREAM="${DATASET}_${SCHEDULE}"
    DATA="$RUN_ROOT/data/$STREAM"
    SOURCE_SNAPSHOT="$OUT_ROOT/source/$STREAM/router_snapshot.json"
    test -s "$SOURCE_SNAPSHOT"
    for CONDITION in base_reset skills_reset skills_posterior; do
      OUT="$OUT_ROOT/target/$MODEL_KEY/$STREAM/$CONDITION"
      mkdir -p "$OUT"
      if jq -e '.results.stage5.arms[] | select(.arm == "mix_ghost" and .status == "COMPLETE")' \
          "$OUT/report.json" >/dev/null 2>&1; then
        echo "[SKIP] $MODEL_KEY $STREAM $CONDITION"
        continue
      fi
      EXTRA=()
      if [[ "$CONDITION" != "base_reset" ]]; then
        EXTRA+=(--skill-library "$SOURCE_LIB")
      fi
      if [[ "$CONDITION" == "skills_posterior" ]]; then
        EXTRA+=(--initial-router-snapshot "$SOURCE_SNAPSHOT")
      fi
      echo "[RUN] $MODEL_KEY $STREAM $CONDITION"
      python experiments/spec_v03_stage5_9.py \
        --runtime-cases "$DATA/runtime_cases.json" \
        --event-order "$DATA/event_order_manifest.json" \
        --split-manifest "$DATA/split_manifest.json" \
        --include-split T_online \
        --include-split T_anchor \
        --include-split T_final \
        --split-audit-output "$OUT/split-audit.json" \
        --output "$OUT/report.json" \
        --run-id "family-disjoint-$MODEL_KEY-$STREAM-$CONDITION" \
        --model-id "$MODEL_ID" \
        --model-snapshot "$MODEL_SNAPSHOT" \
        --endpoint "$ENDPOINT" \
        --stage stage5 \
        --stage5-arm mix_ghost \
        --backbone-provider vllm \
        --feedback-provider development-structural \
        --seed "$SEED" \
        --track controlled_a1 \
        --max-requests 1000 \
        --max-total-tokens 10000000 \
        --max-output-tokens 1024 \
        --temperature 0 \
        "${EXTRA[@]}"
    done
  done
done

echo "[COMPLETE] $MODEL_KEY family-disjoint target matrix"
