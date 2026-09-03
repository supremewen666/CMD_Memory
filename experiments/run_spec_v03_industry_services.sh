#!/usr/bin/env bash
set -euo pipefail

: "${RUN_ROOT:?set RUN_ROOT}"
: "${MODERN_PY:?set MODERN_PY to the Lychee/Mem0 environment Python}"
: "${LYCHEE_REPO:?set LYCHEE_REPO}"
: "${LYCHEE_COMMIT:?set LYCHEE_COMMIT}"
: "${EMBED_MODEL:?set EMBED_MODEL}"

CMD_PYTHON="${CMD_PYTHON:-python}"
RUNTIME_ROOT="${INDUSTRY_RUNTIME_ROOT:-$RUN_ROOT/industry_runtime_v1}"
LOG_ROOT="$RUN_ROOT/logs"
mkdir -p "$RUNTIME_ROOT/usage" "$RUNTIME_ROOT/lychee-instances" \
  "$RUNTIME_ROOT/lychee-receipts" "$LOG_ROOT"

"$MODERN_PY" experiments/spec_v03_embedding_server.py \
  --model-path "$EMBED_MODEL" --host 127.0.0.1 --port 8003 \
  > "$LOG_ROOT/industry-embedding.log" 2>&1 &
EMBED_PID=$!

"$CMD_PYTHON" experiments/spec_v03_metering_proxy.py \
  --host 127.0.0.1 --port 9100 --upstream http://127.0.0.1:8001 \
  --receipt-root "$RUNTIME_ROOT/usage" \
  --max-llm-calls 20 --max-input-tokens 100000 \
  --max-output-tokens 4096 --max-gpu-seconds 300 \
  > "$LOG_ROOT/industry-metering-proxy.log" 2>&1 &
METER_PID=$!

"$CMD_PYTHON" experiments/spec_v03_lychee_instance_manager.py \
  --host 127.0.0.1 --port 9000 --repository "$LYCHEE_REPO" \
  --python "$MODERN_PY" --official-commit "$LYCHEE_COMMIT" \
  --instance-root "$RUNTIME_ROOT/lychee-instances" \
  --receipt-root "$RUNTIME_ROOT/lychee-receipts" \
  --public-base-url http://127.0.0.1:9000 \
  --llm-proxy-base-url http://127.0.0.1:9100 \
  --embedding-base-url http://127.0.0.1:8003 \
  > "$LOG_ROOT/industry-lychee-manager.log" 2>&1 &
LYCHEE_PID=$!

cleanup() {
  kill "$LYCHEE_PID" "$METER_PID" "$EMBED_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for URL in http://127.0.0.1:8003/health http://127.0.0.1:9100/health http://127.0.0.1:9000/health; do
  for _ in $(seq 1 120); do
    curl -sf "$URL" >/dev/null && break
    sleep 1
  done
  curl -sf "$URL" >/dev/null
  echo "[READY] $URL"
done

echo "[READY] controlled industry services"
wait -n "$EMBED_PID" "$METER_PID" "$LYCHEE_PID"
