#!/usr/bin/env bash
set -euo pipefail

: "${RUN_ROOT:?set RUN_ROOT}"
: "${MODERN_PY:?set MODERN_PY to the Mem0/embedding environment Python}"
: "${EMBED_MODEL:?set EMBED_MODEL}"

CMD_PYTHON="${CMD_PYTHON:-python}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
RUNTIME_ROOT="${INDUSTRY_RUNTIME_ROOT:-$RUN_ROOT/industry_runtime_v1}"
LOG_ROOT="$RUN_ROOT/logs"
SERVICE_HOST="${SERVICE_HOST:-127.0.0.1}"
EMBED_PORT="${EMBED_PORT:-8003}"
METERING_PORT="${METERING_PORT:-9100}"
MODEL_UPSTREAM="${MODEL_UPSTREAM:-http://127.0.0.1:8001}"
SYSTEM_MAX_LLM_CALLS="${SYSTEM_MAX_LLM_CALLS:-20}"
SYSTEM_MAX_INPUT_TOKENS="${SYSTEM_MAX_INPUT_TOKENS:-100000}"
SYSTEM_MAX_OUTPUT_TOKENS="${SYSTEM_MAX_OUTPUT_TOKENS:-4096}"
SYSTEM_MAX_GPU_SECONDS="${SYSTEM_MAX_GPU_SECONDS:-300}"
mkdir -p "$RUNTIME_ROOT/usage" "$LOG_ROOT"

"$MODERN_PY" "$REPO_ROOT/experiments/spec_v03_embedding_server.py" \
  --model-path "$EMBED_MODEL" --host "$SERVICE_HOST" --port "$EMBED_PORT" \
  > "$LOG_ROOT/industry-embedding.log" 2>&1 &
EMBED_PID=$!

"$CMD_PYTHON" "$REPO_ROOT/experiments/spec_v03_metering_proxy.py" \
  --host "$SERVICE_HOST" --port "$METERING_PORT" --upstream "$MODEL_UPSTREAM" \
  --receipt-root "$RUNTIME_ROOT/usage" \
  --max-llm-calls "$SYSTEM_MAX_LLM_CALLS" \
  --max-input-tokens "$SYSTEM_MAX_INPUT_TOKENS" \
  --max-output-tokens "$SYSTEM_MAX_OUTPUT_TOKENS" \
  --max-gpu-seconds "$SYSTEM_MAX_GPU_SECONDS" \
  > "$LOG_ROOT/industry-metering-proxy.log" 2>&1 &
METER_PID=$!

cleanup() {
  kill "$METER_PID" "$EMBED_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for URL in "http://$SERVICE_HOST:$EMBED_PORT/health" "http://$SERVICE_HOST:$METERING_PORT/health"; do
  for _ in $(seq 1 120); do
    curl -sf "$URL" >/dev/null && break
    sleep 1
  done
  curl -sf "$URL" >/dev/null
  echo "[READY] $URL"
done

echo "[READY] controlled industry services"
wait -n "$EMBED_PID" "$METER_PID"
