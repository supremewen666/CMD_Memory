#!/usr/bin/env bash
# CMD 观测式实验 — 双卡并行（每卡双端点：Llama answer/selection + frozen Qwen evaluation）
#
# Usage:
#   SSH 1 (GPU 0):  ./run_remaining_experiments.sh --role gpu0
#   SSH 2 (GPU 1):  ./run_remaining_experiments.sh --role gpu1
#   后台运行:        ./run_remaining_experiments.sh --role gpu0 --detach
#   任一台:         ./run_remaining_experiments.sh --role analyze   （先 scp 汇聚 JSONL）
#   冒烟:           ./run_remaining_experiments.sh --role gpu0 --smoke
#   单 Arena 调试:  ./run_remaining_experiments.sh --role gpu0 --only memtrace_seed24
#
# GPU 0: MemTrace-B seed 24 → seed 124 → MemFail
# GPU 1: MemTrace-B seed 224 → STALE → replicate seed 24
#
# 产出：artifacts/arena/*.jsonl，分析后 artifacts/arena/analysis/*.csv

set -euo pipefail
export PYTHONUNBUFFERED=1

# ── 路径 ────────────────────────────────────────────────────────────────────
CMD_ROOT="$HOME/wsy/CMD_Memory"
CMD_VLLM_OVERLAY="$HOME/wsy/runtime/vllm085-transformers451"
ARTIFACTS="$CMD_ROOT/artifacts/arena"
QWVLLM_PORT=8000
LLAMA_JUDGE_PORT=8000
LLAMA_ANSWER_PORT=8001
PID_FILE="/tmp/vllm_shared.pid"
LOG_FILE="/tmp/vllm_shared.log"
JUDGE_BASE_URL="${JUDGE_BASE_URL:-http://localhost:${LLAMA_JUDGE_PORT}/v1}"
JUDGE_MODEL="${JUDGE_MODEL:-qwen2.5-7b-instruct}"
JUDGE_API_KEY="${JUDGE_API_KEY:-dummy}"
CMD_CASE_WORKERS="${CMD_CASE_WORKERS:-32}"
VLLM_READY_TIMEOUT_SECONDS="${VLLM_READY_TIMEOUT_SECONDS:-300}"

# ── 参数 ────────────────────────────────────────────────────────────────────
ROLE=""
SMOKE=false
ONLY=""
DETACH=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --role)
      ROLE="$2"; shift 2 ;;
    --smoke)
      SMOKE=true; shift ;;
    --only)
      ONLY="$2"; shift 2 ;;
    --detach)
      DETACH=true; shift ;;
    --help|-h)
      echo "Usage: $0 --role gpu0|gpu1|analyze [--smoke] [--only NAME] [--detach]"
      echo ""
      echo "  GPU 0 (~3.5h): MemTrace-B seeds 24+124 → MemFail"
      echo "  GPU 1 (~3.5h): MemTrace-B seed 224 → STALE → replicate seed 24"
      echo "  analyze:        unified analysis (run after scp-ing results to one machine)"
      echo "  --detach:       run in a new session; logs to artifacts/arena/run_ROLE_TIMESTAMP.log"
      echo ""
      echo "  --only:  skip straight to one named phase (memtrace_seed24, memtrace_seed124,"
      echo "           memtrace_seed224, memfail, stale, memtrace_llama)"
      exit 0 ;;
    *)
      echo "Unknown: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$ROLE" ]]; then
  echo "ERROR: --role is required (gpu0 | gpu1 | analyze)" >&2
  exit 1
fi

if $DETACH && [[ -z "${CMD_EXPERIMENTS_DETACHED:-}" ]]; then
  mkdir -p "$ARTIFACTS"
  detach_log="${ARTIFACTS}/run_${ROLE}_$(date +%Y%m%d_%H%M%S).log"
  detach_args=(--role "$ROLE")
  $SMOKE && detach_args+=(--smoke)
  [[ -n "$ONLY" ]] && detach_args+=(--only "$ONLY")
  nohup setsid env CMD_EXPERIMENTS_DETACHED=1 "$0" "${detach_args[@]}" \
    > "$detach_log" 2>&1 < /dev/null &
  detach_pid=$!
  echo "[detach] started PID ${detach_pid} (log: ${detach_log})"
  echo "[detach] follow: tail -f ${detach_log}"
  exit 0
fi

cd "$CMD_ROOT"

# ══════════════════════════════════════════════════════════════════════════════
# vLLM lifecycle
# ══════════════════════════════════════════════════════════════════════════════

resolve_qwen_dir() {
  env PYTHONPATH="$CMD_VLLM_OVERLAY" HF_HUB_OFFLINE=1 python - <<'PY'
from huggingface_hub import snapshot_download
print(snapshot_download("Qwen/Qwen2.5-7B-Instruct", local_files_only=True))
PY
}

resolve_llama_dir() {
  env PYTHONPATH="$CMD_VLLM_OVERLAY" HF_HUB_OFFLINE=1 python - <<'PY'
from huggingface_hub import snapshot_download
print(snapshot_download("meta-llama/Llama-3.1-8B-Instruct", local_files_only=True))
PY
}

start_qwen_vllm() {
  if curl --noproxy '*' -s "localhost:${QWVLLM_PORT}/v1/models" >/dev/null 2>&1; then
    echo "[vLLM] Qwen port ${QWVLLM_PORT} already up, reusing"
    return 0
  fi

  CMD_QWEN_MODEL_DIR="$(resolve_qwen_dir)"
  echo "[vLLM] Qwen model dir: $CMD_QWEN_MODEL_DIR"

  nohup env \
    PYTHONPATH="$CMD_VLLM_OVERLAY" \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    CUDA_VISIBLE_DEVICES=0 \
    vllm serve "$CMD_QWEN_MODEL_DIR" \
    --host 127.0.0.1 \
    --served-model-name qwen2.5-7b-instruct \
    --port "$QWVLLM_PORT" \
    --gpu-memory-utilization 0.80 \
    --max-model-len 8192 \
    --max-num-seqs 64 \
    --enable-prefix-caching \
    > "$LOG_FILE" 2>&1 &

  echo $! > "$PID_FILE"
  echo "[vLLM] Qwen pid $(cat "$PID_FILE")"

  echo -n "[vLLM] waiting for port ${QWVLLM_PORT} …"
  for i in $(seq 1 "$VLLM_READY_TIMEOUT_SECONDS"); do
    if curl --noproxy '*' -s "localhost:${QWVLLM_PORT}/v1/models" >/dev/null 2>&1; then
      echo " ready (${i}s)"
      return 0
    fi
    sleep 1
    echo -n "."
  done
  echo " TIMEOUT"
  return 1
}

stop_qwen_vllm() {
  if [[ ! -f "$PID_FILE" ]]; then
    return 0
  fi
  local pid
  pid=$(cat "$PID_FILE")
  if kill -0 "$pid" 2>/dev/null; then
    echo "[vLLM] stopping Qwen pid $pid"
    kill "$pid" || true
    sleep 2
  fi
  rm -f "$PID_FILE"
}

start_llama_dual_vllm() {
  # Qwen judge (frozen) on port 8000 + Llama answerer on port 8001
  local qwen_up llma_up
  qwen_up=false
  llma_up=false

  curl --noproxy '*' -s "localhost:${LLAMA_JUDGE_PORT}/v1/models" >/dev/null 2>&1 && qwen_up=true
  curl --noproxy '*' -s "localhost:${LLAMA_ANSWER_PORT}/v1/models" >/dev/null 2>&1 && llma_up=true

  if $qwen_up && $llma_up; then
    echo "[vLLM] dual endpoints already up, reusing"
    return 0
  fi

  CMD_QWEN_MODEL_DIR="$(resolve_qwen_dir)"
  CMD_LLAMA_MODEL_DIR="$(resolve_llama_dir)"
  echo "[vLLM] Qwen dir:  $CMD_QWEN_MODEL_DIR"
  echo "[vLLM] Llama dir: $CMD_LLAMA_MODEL_DIR"

  if ! $qwen_up; then
    nohup env \
      PYTHONPATH="$CMD_VLLM_OVERLAY" \
      HF_HUB_OFFLINE=1 \
      TRANSFORMERS_OFFLINE=1 \
      CUDA_VISIBLE_DEVICES=0 \
      vllm serve "$CMD_QWEN_MODEL_DIR" \
      --host 127.0.0.1 \
      --served-model-name qwen2.5-7b-instruct \
      --port "$LLAMA_JUDGE_PORT" \
      --gpu-memory-utilization 0.40 \
      --max-model-len 8192 \
      --max-num-seqs 32 \
      --enable-prefix-caching \
      > /tmp/vllm_qwen_judge.log 2>&1 &
    echo "[vLLM] Qwen judge pid $!"
  fi

  if ! $llma_up; then
    nohup env \
      PYTHONPATH="$CMD_VLLM_OVERLAY" \
      HF_HUB_OFFLINE=1 \
      TRANSFORMERS_OFFLINE=1 \
      CUDA_VISIBLE_DEVICES=0 \
      vllm serve "$CMD_LLAMA_MODEL_DIR" \
      --host 127.0.0.1 \
      --served-model-name llama-3.1-8b-instruct \
      --port "$LLAMA_ANSWER_PORT" \
      --gpu-memory-utilization 0.40 \
      --max-model-len 8192 \
      --max-num-seqs 32 \
      --enable-prefix-caching \
      > /tmp/vllm_llama_answer.log 2>&1 &
    echo "[vLLM] Llama answerer pid $!"
  fi

  echo -n "[vLLM] waiting for dual endpoints …"
  for i in $(seq 1 "$VLLM_READY_TIMEOUT_SECONDS"); do
    local ok=true
    curl --noproxy '*' -s "localhost:${LLAMA_JUDGE_PORT}/v1/models" >/dev/null 2>&1 || ok=false
    curl --noproxy '*' -s "localhost:${LLAMA_ANSWER_PORT}/v1/models" >/dev/null 2>&1 || ok=false
    if $ok; then
      echo " ready (${i}s)"
      return 0
    fi
    sleep 1
    echo -n "."
  done
  echo " TIMEOUT"
  return 1
}

stop_llama_dual_vllm() {
  for port in "$LLAMA_JUDGE_PORT" "$LLAMA_ANSWER_PORT"; do
    local pid
    pid=$(lsof -ti ":$port" 2>/dev/null || true)
    if [[ -n "$pid" ]]; then
      echo "[vLLM] stopping port $port pid $pid"
      kill "$pid" || true
    fi
  done
  sleep 2
}

# ══════════════════════════════════════════════════════════════════════════════
# Env & gates
# ══════════════════════════════════════════════════════════════════════════════

lane_env() {
  local answer_base_url="$1"
  local answer_model="$2"
  export LLM_BASE_URL="$answer_base_url"
  export LLM_MODEL="$answer_model"
  export LLM_JUDGE_BASE_URL="$JUDGE_BASE_URL"
  export LLM_JUDGE_MODEL="$JUDGE_MODEL"
  export LLM_API_KEY="${ANSWER_API_KEY:-dummy}"
  export LLM_JUDGE_API_KEY="$JUDGE_API_KEY"
}

qwen_env() {
  lane_env \
    "http://localhost:${QWVLLM_PORT}/v1" \
    "qwen2.5-7b-instruct"
  export LLM_TIMEOUT=120
  export NO_PROXY="localhost,127.0.0.1"
  export no_proxy="localhost,127.0.0.1"
}

llama_dual_env() {
  lane_env \
    "http://localhost:${LLAMA_ANSWER_PORT}/v1" \
    "llama-3.1-8b-instruct"
  export LLM_TIMEOUT=120
  export NO_PROXY="localhost,127.0.0.1"
  export no_proxy="localhost,127.0.0.1"
}

gate_g0() {
  echo "[gate] G0: judge logprob availability"
  python - <<'PY'
import sys; sys.path.insert(0, ".")
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from experiments.experiment_runner_common import assert_g_eval_available
assert_g_eval_available(LLMClient(LLMClientConfig.for_role("judge")), role="preflight-judge")
print("[gate] G0 judge logprob gate: OK")
PY
}

# ══════════════════════════════════════════════════════════════════════════════
# Arena runners
# ══════════════════════════════════════════════════════════════════════════════

run_memtrace() {
  local label="$1"; shift
  local extra=("$@")
  local output="${ARTIFACTS}/memtrace_${label}.jsonl"

  if [[ -s "$output" ]]; then
    echo "[skip] ${output} exists and is non-empty; remove it to rerun this phase"
    return 0
  fi

  echo "===== Arena: MemTrace-B (${label}) ====="

  if $SMOKE; then
    python -m experiments.run_arena_memtrace \
      --backend-factory experiments.arena_backends:create_vllm_backend \
      --limit 50 --chains --case-workers "$CMD_CASE_WORKERS" --best-of-n-control \
      "${extra[@]}" \
      --output "$output" \
      2>&1 | tee "${ARTIFACTS}/memtrace_${label}.log"
  else
    python -m experiments.run_arena_memtrace \
      --backend-factory experiments.arena_backends:create_vllm_backend \
      --chains --case-workers 1 --best-of-n-control --deposit-after 0.5 --deposit-min-benefit 0.05 --deposit-min-support 3 \
      "${extra[@]}" \
      --output "$output" \
      2>&1 | tee "${ARTIFACTS}/memtrace_${label}.log"
  fi
  echo "[done] MemTrace-B ${label} → ${output}"
}

run_memfail() {
  local output="${ARTIFACTS}/memfail_observations.jsonl"
  if [[ -s "$output" ]]; then
    echo "[skip] ${output} exists and is non-empty; remove it to rerun this phase"
    return 0
  fi

  echo "===== Arena: MemFail ====="
  python -m experiments.run_arena_memfail \
    --backend-factory experiments.arena_backends:create_vllm_backend \
    --no-chains --case-workers "$CMD_CASE_WORKERS" --best-of-n-control \
    --output "$output" \
    2>&1 | tee "${ARTIFACTS}/memfail_run.log"
  echo "[done] MemFail → ${ARTIFACTS}/memfail_observations.jsonl"
}

run_stale() {
  local output="${ARTIFACTS}/stale_observations.jsonl"
  if [[ -s "$output" ]]; then
    echo "[skip] ${output} exists and is non-empty; remove it to rerun this phase"
    return 0
  fi

  echo "===== Arena: STALE ====="
  python -m experiments.run_arena_stale \
    --backend-factory experiments.arena_backends:create_vllm_backend \
    --no-chains --case-workers "$CMD_CASE_WORKERS" --best-of-n-control \
    --output "$output" \
    2>&1 | tee "${ARTIFACTS}/stale_run.log"
  echo "[done] STALE → ${ARTIFACTS}/stale_observations.jsonl"
}

run_analysis() {
  echo "===== Unified Analysis ====="
  mkdir -p "${ARTIFACTS}/analysis"
  python -m experiments.analyze_arena_results \
    --inputs \
      "${ARTIFACTS}/memtrace_seed24.jsonl" \
      "${ARTIFACTS}/memtrace_seed124.jsonl" \
      "${ARTIFACTS}/memtrace_seed224.jsonl" \
      "${ARTIFACTS}/memfail_observations.jsonl" \
      "${ARTIFACTS}/stale_observations.jsonl" \
      "${ARTIFACTS}/memtrace_llama.jsonl" \
    --output-dir "${ARTIFACTS}/analysis"
  echo "[done] Analysis → ${ARTIFACTS}/analysis/"
}

# ══════════════════════════════════════════════════════════════════════════════
# GPU 0 main: MemTrace-B seed 24 → seed 124 → MemFail
# ══════════════════════════════════════════════════════════════════════════════

main_gpu0() {
  mkdir -p "$ARTIFACTS"
  start_llama_dual_vllm
  llama_dual_env
  gate_g0

  case "$ONLY" in
    memtrace_seed24)
      run_memtrace "seed24" --seed 24
      ;;
    memtrace_seed124)
      run_memtrace "seed124" --seed 124
      ;;
    memfail)
      run_memfail
      ;;
    "")
      if $SMOKE; then
        run_memtrace "smoke" --seed 24
        echo "[smoke] done — check ${ARTIFACTS}/memtrace_smoke.jsonl"
        stop_llama_dual_vllm
        return 0
      fi
      run_memtrace "seed24" --seed 24
      run_memtrace "seed124" --seed 124
      # 别名：seed 24 作为主 Qwen MemTrace-B 输出
      cp "${ARTIFACTS}/memtrace_seed24.jsonl" "${ARTIFACTS}/memtrace_observations.jsonl"
      run_memfail
      ;;
    *)
      echo "ERROR: unknown --only target: $ONLY" >&2
      echo "  valid: memtrace_seed24, memtrace_seed124, memfail" >&2
      exit 1
      ;;
  esac

  echo ""
  echo "===== GPU 0 DONE ====="
  stop_llama_dual_vllm
}

# ══════════════════════════════════════════════════════════════════════════════
# GPU 1 main: MemTrace-B seed 224 → STALE → MemTrace-B Llama
# ══════════════════════════════════════════════════════════════════════════════

main_gpu1() {
  mkdir -p "$ARTIFACTS"
  start_llama_dual_vllm
  llama_dual_env
  gate_g0

  case "$ONLY" in
    memtrace_seed224)
      run_memtrace "seed224" --seed 224
      stop_llama_dual_vllm
      return 0
      ;;
    stale)
      run_stale
      stop_llama_dual_vllm
      return 0
      ;;
    memtrace_llama)
      run_memtrace "llama" --seed 24
      stop_llama_dual_vllm
      return 0
      ;;
    "")
      if $SMOKE; then
        run_memtrace "smoke" --seed 224
        echo "[smoke] done — check ${ARTIFACTS}/memtrace_smoke.jsonl"
        stop_llama_dual_vllm
        return 0
      fi
      run_memtrace "seed224" --seed 224
      run_stale
      run_memtrace "llama" --seed 24
      ;;
    *)
      echo "ERROR: unknown --only target: $ONLY" >&2
      echo "  valid: memtrace_seed224, stale, memtrace_llama" >&2
      exit 1
      ;;
  esac

  echo ""
  echo "===== GPU 1 DONE ====="
  stop_llama_dual_vllm
}

# ══════════════════════════════════════════════════════════════════════════════
# Analyze: run after scp-ing all JSONL to one machine
# ══════════════════════════════════════════════════════════════════════════════

main_analyze() {
  run_analysis
  echo ""
  echo "===== ANALYSIS DONE ====="
}

# ══════════════════════════════════════════════════════════════════════════════
# Dispatch
# ══════════════════════════════════════════════════════════════════════════════

case "$ROLE" in
  gpu0)     main_gpu0 ;;
  gpu1)     main_gpu1 ;;
  analyze)  main_analyze ;;
  *)
    echo "ERROR: --role must be gpu0, gpu1, or analyze" >&2
    exit 1 ;;
esac
