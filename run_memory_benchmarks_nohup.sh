#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$ROOT_DIR/artifacts/runtime"
LOG_DIR="$ROOT_DIR/artifacts/logs"
SERVER_PID_FILE="$RUNTIME_DIR/vllm.pid"
BENCH_PID_FILE="$RUNTIME_DIR/memory_benchmarks.pid"
BENCH_INFO_FILE="$RUNTIME_DIR/memory_benchmarks.info"
ECC_BUILD_PID_FILE="$RUNTIME_DIR/ecc_runtime_build.pid"
MODEL_PATH_FILE="$RUNTIME_DIR/vllm_model_path"
MODEL_LEN_FILE="$RUNTIME_DIR/vllm_max_model_len"
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
    "  $0 server-smoke" \
    "  $0 build-runtimes [LIMIT]" \
    "  $0 runtime-build-status" \
    "  $0 run LONGMEMEVAL_ECC_RUNTIME LOCOMO_ECC_RUNTIME [TOKENIZER_PATH] [MAX_MODEL_LEN]" \
    "  $0 run-legacy" \
    "  $0 benchmark-status" \
    "  $0 stop-server" \
    "" \
    "Defaults: VLLM_PORT=8000, MAX_MODEL_LEN=32768, LLM_MAX_TOKENS=512."
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
    printf '%s\n' "$model_path" >"$MODEL_PATH_FILE"
    printf '%s\n' "$max_model_len" >"$MODEL_LEN_FILE"
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
  server-smoke)
    smoke_body="$RUNTIME_DIR/vllm_http_smoke.json"
    http_status="$(curl -sS -o "$smoke_body" -w '%{http_code}' \
      -H 'Content-Type: application/json' \
      -d "{\"model\":\"$SERVED_MODEL_NAME\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with OK.\"}],\"temperature\":0,\"max_tokens\":8}" \
      "$BASE_URL/chat/completions")"
    cat "$smoke_body"
    printf '\nhttp_status=%s\n' "$http_status"
    if [[ "$http_status" != "200" ]]; then
      exit 1
    fi
    ;;
  build-runtimes)
    limit="${2:-0}"
    build_log="$LOG_DIR/ecc_runtime_build.log"
    nohup bash "$ROOT_DIR/$(basename "$0")" build-runtimes-worker "$limit" \
      >"$build_log" 2>&1 &
    printf '%s\n' "$!" >"$ECC_BUILD_PID_FILE"
    printf 'ECC harness/runtime build queued: pid=%s log=%s\n' "$!" "$build_log"
    printf 'watch progress: tail -f %q\n' "$build_log"
    ;;
  build-runtimes-worker)
    limit="${2:-0}"
    cd "$ROOT_DIR"
    python -m experiments.materialize_ecc_memory_benchmark_harness \
      --benchmark longmemeval --limit "$limit" \
      --output artifacts/harness/longmemeval_ecc_causal_v2
    python -m experiments.run_ecc_memory_runtime \
      --cases artifacts/harness/longmemeval_ecc_causal_v2/memaudit_cases.jsonl \
      --bindings artifacts/harness/longmemeval_ecc_causal_v2/ghost_bindings.jsonl \
      --states artifacts/harness/longmemeval_ecc_causal_v2/shadow_states.jsonl \
      --ecology-ledger artifacts/harness/longmemeval_ecc_causal_v2/frozen_ecology.jsonl \
      --output artifacts/runtime/longmemeval_ecc_causal_v2
    python -m experiments.materialize_ecc_memory_benchmark_harness \
      --benchmark locomo --limit "$limit" \
      --output artifacts/harness/locomo_ecc_causal_v2
    python -m experiments.run_ecc_memory_runtime \
      --cases artifacts/harness/locomo_ecc_causal_v2/memaudit_cases.jsonl \
      --bindings artifacts/harness/locomo_ecc_causal_v2/ghost_bindings.jsonl \
      --states artifacts/harness/locomo_ecc_causal_v2/shadow_states.jsonl \
      --ecology-ledger artifacts/harness/locomo_ecc_causal_v2/frozen_ecology.jsonl \
      --output artifacts/runtime/locomo_ecc_causal_v2
    ;;
  runtime-build-status)
    if [[ -f "$ECC_BUILD_PID_FILE" ]] && kill -0 "$(<"$ECC_BUILD_PID_FILE")" 2>/dev/null; then
      printf 'runtime_build_running=1 coordinator_pid=%s\n' "$(<"$ECC_BUILD_PID_FILE")"
    else
      printf 'runtime_build_running=0\n'
    fi
    tail -n 30 "$LOG_DIR/ecc_runtime_build.log" 2>/dev/null || true
    ;;
  run)
    curl -fsS "$BASE_URL/models" >/dev/null
    long_runtime="${2:-${CMD_ECC_RUNTIME_LONGMEMEVAL:-}}"
    locomo_runtime="${3:-${CMD_ECC_RUNTIME_LOCOMO:-}}"
    tokenizer_arg="${4:-}"
    max_model_len_arg="${5:-}"
    if [[ -z "$long_runtime" || -z "$locomo_runtime" ]]; then
      printf '%s\n' \
        'ECC runtime directories are required.' \
        'Usage: run_memory_benchmarks_nohup.sh run LONGMEMEVAL_ECC_RUNTIME LOCOMO_ECC_RUNTIME [TOKENIZER_PATH] [MAX_MODEL_LEN]' >&2
      exit 2
    fi
    if [[ -z "$tokenizer_arg" && -z "${LLM_TOKENIZER_PATH:-}" && ! -f "$MODEL_PATH_FILE" ]]; then
      printf '%s\n' \
        'Tokenizer path is required because vLLM was started outside this script.' \
        'Pass it as the fourth argument or export LLM_TOKENIZER_PATH.' >&2
      exit 2
    fi
    if [[ -z "$max_model_len_arg" && -z "${LLM_MAX_MODEL_LEN:-}" && ! -f "$MODEL_LEN_FILE" ]]; then
      printf '%s\n' \
        'Maximum model length is required because vLLM was started outside this script.' \
        'Pass it as the fifth argument or export LLM_MAX_MODEL_LEN.' >&2
      exit 2
    fi
    if [[ -n "$tokenizer_arg" ]]; then
      tokenizer_path="$tokenizer_arg"
    elif [[ -n "${LLM_TOKENIZER_PATH:-}" ]]; then
      tokenizer_path="$LLM_TOKENIZER_PATH"
    else
      tokenizer_path="$(<"$MODEL_PATH_FILE")"
    fi
    if [[ ! -d "$tokenizer_path" ]]; then
      printf 'tokenizer/model directory does not exist: %s\n' "$tokenizer_path" >&2
      exit 2
    fi
    if [[ -n "$max_model_len_arg" ]]; then
      max_model_len="$max_model_len_arg"
    elif [[ -n "${LLM_MAX_MODEL_LEN:-}" ]]; then
      max_model_len="$LLM_MAX_MODEL_LEN"
    else
      max_model_len="$(<"$MODEL_LEN_FILE")"
    fi
    bench_log="$LOG_DIR/memory_benchmarks.log"
    printf 'launcher_mode=ecc\nentrypoint=experiments.run_ecc_sealed_memory_benchmark\n' \
      >"$bench_log"
    nohup env \
      LLM_BASE_URL="$BASE_URL" \
      LLM_MODEL="$SERVED_MODEL_NAME" \
      LLM_TOKENIZER_PATH="$tokenizer_path" \
      LLM_MAX_MODEL_LEN="$max_model_len" \
      LLM_MAX_TOKENS="${LLM_MAX_TOKENS:-512}" \
      bash "$ROOT_DIR/$(basename "$0")" run-ecc-worker "$long_runtime" "$locomo_runtime" \
      >>"$bench_log" 2>&1 &
    printf '%s\n' "$!" >"$BENCH_PID_FILE"
    printf 'mode=ecc\npid=%s\nqueued_at=%s\nlog=%s\n' \
      "$!" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$bench_log" >"$BENCH_INFO_FILE"
    printf 'benchmarks queued sequentially: pid=%s log=%s\n' "$!" "$bench_log"
    printf 'watch progress: tail -f %q\n' "$bench_log"
    ;;
  run-ecc-worker)
    long_runtime="${2:?LongMemEval ECC runtime directory is required}"
    locomo_runtime="${3:?LoCoMo ECC runtime directory is required}"
    cd "$ROOT_DIR"
    printf 'worker_mode=ecc\nworker_pid=%s\n' "$$"
    python -m experiments.run_ecc_sealed_memory_benchmark \
      --benchmark longmemeval \
      --runtime-dir "$long_runtime" \
      --output artifacts/experiments/longmemeval_ecc_causal_v2_sealed
    python -m experiments.run_ecc_sealed_memory_benchmark \
      --benchmark locomo \
      --runtime-dir "$locomo_runtime" \
      --output artifacts/experiments/locomo_ecc_causal_v2_sealed
    ;;
  run-legacy)
    curl -fsS "$BASE_URL/models" >/dev/null
    bench_log="$LOG_DIR/memory_benchmarks_legacy.log"
    printf 'launcher_mode=legacy\nentrypoint=experiments.run_sealed_memory_benchmark\n' \
      >"$bench_log"
    nohup env \
      LLM_BASE_URL="$BASE_URL" \
      LLM_MODEL="$SERVED_MODEL_NAME" \
      bash "$ROOT_DIR/$(basename "$0")" run-legacy-worker --no-full-context \
      >>"$bench_log" 2>&1 &
    printf '%s\n' "$!" >"$BENCH_PID_FILE"
    printf 'mode=legacy\npid=%s\nqueued_at=%s\nlog=%s\n' \
      "$!" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$bench_log" >"$BENCH_INFO_FILE"
    printf 'legacy static-action baseline queued: pid=%s log=%s\n' "$!" "$bench_log"
    ;;
  run-legacy-worker)
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
    if [[ -f "$BENCH_INFO_FILE" ]]; then
      cat "$BENCH_INFO_FILE"
      current_log="$(sed -n 's/^log=//p' "$BENCH_INFO_FILE" | tail -n 1)"
      if [[ -n "$current_log" ]]; then
        tail -n 30 "$current_log" 2>/dev/null || true
      fi
    else
      printf 'no benchmark metadata for this script version; old log is stale and was not displayed\n'
    fi
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
