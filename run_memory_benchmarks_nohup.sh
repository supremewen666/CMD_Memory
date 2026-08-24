#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$ROOT_DIR/artifacts/runtime"
LOG_DIR="$ROOT_DIR/artifacts/logs"
SERVER_PID_FILE="$RUNTIME_DIR/vllm.pid"
BENCH_PID_FILE="$RUNTIME_DIR/memory_benchmarks.pid"
PORT="${VLLM_PORT:-8000}"
SERVED_MODEL_NAME="${VLLM_SERVED_MODEL_NAME:-qwen2.5-7b-instruct}"
BASE_URL="http://127.0.0.1:${PORT}/v1"

mkdir -p "$RUNTIME_DIR" "$LOG_DIR"

is_vllm_pid() {
  local candidate_pid="$1"
  kill -0 "$candidate_pid" 2>/dev/null \
    && ps -p "$candidate_pid" -o command= 2>/dev/null \
      | grep -Eq '(^|[ /])vllm( |$)|vllm\.entrypoints\.openai\.api_server'
}

usage() {
  printf '%s\n' \
    "Usage:" \
    "  $0 start-server MODEL_PATH [MAX_MODEL_LEN]" \
    "  $0 server-status" \
    "  $0 run" \
    "  $0 benchmark-status" \
    "  $0 stop-server" \
    "" \
    "Defaults: VLLM_PORT=8000, MAX_MODEL_LEN=32768, full-context disabled." \
    "Set CMD_FULL_CONTEXT=1 only with a server that can accommodate 128K prompts."
}

case "${1:-}" in
  start-server)
    model_path="${2:?MODEL_PATH is required}"
    max_model_len="${3:-32768}"
    if [[ ! -d "$model_path" ]]; then
      printf 'model directory does not exist: %s\n' "$model_path" >&2
      exit 2
    fi
    if [[ -f "$SERVER_PID_FILE" ]] && is_vllm_pid "$(<"$SERVER_PID_FILE")"; then
      printf 'vLLM is already running with pid %s\n' "$(<"$SERVER_PID_FILE")"
      exit 0
    fi
    server_log="$LOG_DIR/vllm_${PORT}.log"
    if command -v vllm >/dev/null 2>&1; then
      nohup vllm serve "$model_path" \
        --host 127.0.0.1 \
        --port "$PORT" \
        --served-model-name "$SERVED_MODEL_NAME" \
        --trust-remote-code \
        --dtype auto \
        --tensor-parallel-size "${VLLM_TP_SIZE:-1}" \
        --gpu-memory-utilization "${VLLM_GPU_MEMORY_UTILIZATION:-0.90}" \
        --max-model-len "$max_model_len" \
        >"$server_log" 2>&1 &
    else
      nohup python -m vllm.entrypoints.openai.api_server \
        --model "$model_path" \
        --host 127.0.0.1 \
        --port "$PORT" \
        --served-model-name "$SERVED_MODEL_NAME" \
        --trust-remote-code \
        --dtype auto \
        --tensor-parallel-size "${VLLM_TP_SIZE:-1}" \
        --gpu-memory-utilization "${VLLM_GPU_MEMORY_UTILIZATION:-0.90}" \
        --max-model-len "$max_model_len" \
        >"$server_log" 2>&1 &
    fi
    printf '%s\n' "$!" >"$SERVER_PID_FILE"
    printf 'vLLM starting: pid=%s log=%s model=%s\n' "$!" "$server_log" "$SERVED_MODEL_NAME"
    printf 'watch startup: tail -f %q\n' "$server_log"
    ;;
  server-status)
    if curl -fsS "$BASE_URL/models"; then
      printf '\nserver_ready=1 base_url=%s model=%s\n' "$BASE_URL" "$SERVED_MODEL_NAME"
    else
      printf 'server_ready=0; inspect %s\n' "$LOG_DIR/vllm_${PORT}.log" >&2
      exit 1
    fi
    ;;
  run)
    curl -fsS "$BASE_URL/models" >/dev/null
    full_context_flag="--no-full-context"
    if [[ "${CMD_FULL_CONTEXT:-0}" == "1" ]]; then
      full_context_flag="--full-context"
    fi
    bench_log="$LOG_DIR/memory_benchmarks.log"
    nohup env \
      LLM_BASE_URL="$BASE_URL" \
      LLM_MODEL="$SERVED_MODEL_NAME" \
      "$0" run-worker "$full_context_flag" \
      >"$bench_log" 2>&1 &
    printf '%s\n' "$!" >"$BENCH_PID_FILE"
    printf 'benchmarks queued sequentially: pid=%s log=%s\n' "$!" "$bench_log"
    printf 'watch progress: tail -f %q\n' "$bench_log"
    ;;
  run-worker)
    full_context_flag="${2:---no-full-context}"
    cd "$ROOT_DIR"
    python -m experiments.run_sealed_memory_benchmark \
      --benchmark longmemeval \
      "$full_context_flag" \
      --output artifacts/experiments/longmemeval_sealed
    python -m experiments.run_sealed_memory_benchmark \
      --benchmark locomo \
      "$full_context_flag" \
      --output artifacts/experiments/locomo_sealed
    ;;
  benchmark-status)
    if [[ -f "$BENCH_PID_FILE" ]] && kill -0 "$(<"$BENCH_PID_FILE")" 2>/dev/null; then
      printf 'benchmark_running=1 coordinator_pid=%s\n' "$(<"$BENCH_PID_FILE")"
    else
      printf 'benchmark_running=0\n'
    fi
    tail -n 30 "$LOG_DIR/memory_benchmarks.log" 2>/dev/null || true
    ;;
  stop-server)
    if [[ ! -f "$SERVER_PID_FILE" ]]; then
      printf 'no server pid file\n'
      exit 0
    fi
    server_pid="$(<"$SERVER_PID_FILE")"
    if ! is_vllm_pid "$server_pid"; then
      printf 'refusing to stop pid=%s because it is not a vLLM process\n' "$server_pid" >&2
      exit 1
    fi
    kill "$server_pid"
    printf 'sent SIGTERM to vLLM pid=%s\n' "$server_pid"
    ;;
  *)
    usage
    exit 2
    ;;
esac
