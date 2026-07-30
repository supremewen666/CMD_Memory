#!/usr/bin/env bash
# CMD 观测式实验最短路径（单卡 A100，Qwen2.5-7B）
#
# Usage:
#   chmod +x run_remaining_experiments.sh
#   ./run_remaining_experiments.sh              # 全套（~5h）
#   ./run_remaining_experiments.sh --smoke      # 冒烟（50 case，~2min）
#   ./run_remaining_experiments.sh --no-chains  # 跳过链探测（省 ~30% 调用）
#
# 产出放在 artifacts/arena/ 下。

set -euo pipefail

# ── 路径 ────────────────────────────────────────────────────────────────────
CMD_ROOT="$HOME/wsy/CMD_Memory"
CMD_VLLM_OVERLAY="$HOME/wsy/runtime/vllm085-transformers451"
ARTIFACTS="$CMD_ROOT/artifacts/arena"
SHARED_PORT=8000
PID_FILE="/tmp/vllm_shared.pid"
LOG_FILE="/tmp/vllm_shared.log"

# ── 参数 ────────────────────────────────────────────────────────────────────
SMOKE=false
CHAINS="--chains"
DEPOSIT="--deposit-after 0.5 --deposit-min-benefit 0.05 --deposit-min-support 3"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --smoke)
      SMOKE=true
      shift ;;
    --no-chains)
      CHAINS="--no-chains"
      DEPOSIT=""
      shift ;;
    --no-deposit)
      DEPOSIT=""
      shift ;;
    --seed)
      EXTRA_ARGS+=("--seed" "$2")
      shift 2 ;;
    --help|-h)
      echo "Usage: $0 [--smoke] [--no-chains] [--no-deposit] [--seed N]"
      exit 0 ;;
    *)
      echo "Unknown: $1" >&2; exit 1 ;;
  esac
done

cd "$CMD_ROOT"

# ── 0. vLLM：单共享端点（judge + answerer 共用） ───────────────────────────
start_vllm() {
  if curl -s "localhost:${SHARED_PORT}/v1/models" >/dev/null 2>&1; then
    echo "[vLLM] port ${SHARED_PORT} already up, reusing"
    return 0
  fi

  echo "[vLLM] resolving Qwen model dir …"
  CMD_QWEN_MODEL_DIR="$(
    env PYTHONPATH="$CMD_VLLM_OVERLAY" HF_HUB_OFFLINE=1 python - <<'PY'
from huggingface_hub import snapshot_download
print(snapshot_download("Qwen/Qwen2.5-7B-Instruct", local_files_only=True))
PY
  )"
  echo "[vLLM] model dir: $CMD_QWEN_MODEL_DIR"

  nohup env \
    PYTHONPATH="$CMD_VLLM_OVERLAY" \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    CUDA_VISIBLE_DEVICES=0 \
    vllm serve "$CMD_QWEN_MODEL_DIR" \
    --host 127.0.0.1 \
    --served-model-name qwen2.5-7b-instruct \
    --port "$SHARED_PORT" \
    --gpu-memory-utilization 0.80 \
    --max-model-len 8192 \
    --max-num-seqs 64 \
    --enable-prefix-caching \
    > "$LOG_FILE" 2>&1 &

  echo $! > "$PID_FILE"
  echo "[vLLM] pid $(cat "$PID_FILE")"

  # 等待端点就绪
  echo -n "[vLLM] waiting for port ${SHARED_PORT} …"
  for i in $(seq 1 120); do
    if curl -s "localhost:${SHARED_PORT}/v1/models" >/dev/null 2>&1; then
      echo " ready (${i}s)"
      return 0
    fi
    sleep 1
    echo -n "."
  done
  echo " TIMEOUT"
  return 1
}

stop_vllm() {
  if [[ ! -f "$PID_FILE" ]]; then
    return 0
  fi
  local pid
  pid=$(cat "$PID_FILE")
  if kill -0 "$pid" 2>/dev/null; then
    echo "[vLLM] stopping pid $pid"
    kill "$pid" || true
    sleep 2
  fi
  rm -f "$PID_FILE"
}

# ── 1. 环境变量（单端点模式） ──────────────────────────────────────────────
setup_env() {
  export LLM_BASE_URL="http://localhost:${SHARED_PORT}/v1"
  export LLM_MODEL="qwen2.5-7b-instruct"
  export LLM_JUDGE_BASE_URL="http://localhost:${SHARED_PORT}/v1"
  export LLM_JUDGE_MODEL="qwen2.5-7b-instruct"
  export LLM_API_KEY="dummy"
  export LLM_JUDGE_API_KEY="dummy"
  export LLM_TIMEOUT=120
  export NO_PROXY="localhost,127.0.0.1"
  export no_proxy="localhost,127.0.0.1"

  echo "[env] LLM_BASE_URL=$LLM_BASE_URL"
  echo "[env] LLM_MODEL=$LLM_MODEL"
}

# ── 2. G0 门 ────────────────────────────────────────────────────────────────
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

# ── 3. 流验证 ──────────────────────────────────────────────────────────────
validate_streams() {
  echo "===== Stream validation ====="
  for arena in memtrace memfail stale; do
    python -m "experiments.run_arena_${arena}" --validate-only
  done
}

# ── 4. MemTrace-B Arena（主实验） ──────────────────────────────────────────
run_memtrace() {
  local label="$1"; shift
  echo "===== Arena: MemTrace-B ($label) ====="

  if $SMOKE; then
    python -m experiments.run_arena_memtrace \
      --backend-factory experiments.arena_backends:create_vllm_backend \
      --limit 50 ${CHAINS} \
      "${EXTRA_ARGS[@]}" "$@" \
      --output "${ARTIFACTS}/memtrace_${label}.jsonl" \
      2>&1 | tee "${ARTIFACTS}/memtrace_${label}.log"
  else
    python -m experiments.run_arena_memtrace \
      --backend-factory experiments.arena_backends:create_vllm_backend \
      ${CHAINS} ${DEPOSIT} \
      "${EXTRA_ARGS[@]}" "$@" \
      --output "${ARTIFACTS}/memtrace_${label}.jsonl" \
      2>&1 | tee "${ARTIFACTS}/memtrace_${label}.log"
  fi
}

# ── 5. MemFail Arena（跨环境复现） ─────────────────────────────────────────
run_memfail() {
  echo "===== Arena: MemFail ====="
  python -m experiments.run_arena_memfail \
    --backend-factory experiments.arena_backends:create_vllm_backend \
    --no-chains \
    "${EXTRA_ARGS[@]}" \
    --output "${ARTIFACTS}/memfail_observations.jsonl" \
    2>&1 | tee "${ARTIFACTS}/memfail_run.log"
}

# ── 6. STALE Arena ─────────────────────────────────────────────────────────
run_stale() {
  echo "===== Arena: STALE ====="
  python -m experiments.run_arena_stale \
    --backend-factory experiments.arena_backends:create_vllm_backend \
    --no-chains \
    "${EXTRA_ARGS[@]}" \
    --output "${ARTIFACTS}/stale_observations.jsonl" \
    2>&1 | tee "${ARTIFACTS}/stale_run.log"
}

# ── 7. 统一分析 ────────────────────────────────────────────────────────────
run_analysis() {
  echo "===== Analysis ====="
  python -m experiments.analyze_arena_results \
    --inputs \
      "${ARTIFACTS}/memtrace_observations.jsonl" \
      "${ARTIFACTS}/memfail_observations.jsonl" \
      "${ARTIFACTS}/stale_observations.jsonl" \
    --output-dir "${ARTIFACTS}/analysis"
}

# ── 8. Llama 复现（可选） ──────────────────────────────────────────────────
run_llama() {
  echo "===== Arena: MemTrace-B (Llama-3.1-8B) ====="
  export LLM_MODEL="llama-3.1-8b-instruct"
  # LLM_JUDGE_* 不变
  python -m experiments.run_arena_memtrace \
    --backend-factory experiments.arena_backends:create_vllm_backend \
    ${CHAINS} \
    "${EXTRA_ARGS[@]}" \
    --output "${ARTIFACTS}/memtrace_llama_observations.jsonl" \
    2>&1 | tee "${ARTIFACTS}/memtrace_llama_run.log"
}

# ── main ────────────────────────────────────────────────────────────────────
main() {
  mkdir -p "$ARTIFACTS"

  start_vllm
  setup_env
  gate_g0

  if $SMOKE; then
    run_memtrace "smoke"
    echo ""
    echo "[smoke] done — check ${ARTIFACTS}/memtrace_smoke.jsonl"
    stop_vllm
    exit 0
  fi

  validate_streams

  # 3 seeds MemTrace-B（跨 seed 生态位稳定性）
  for s in 24 124 224; do
    EXTRA_ARGS=("--seed" "$s")
    run_memtrace "seed${s}"
  done
  # 主输出用 seed 24 的别名
  cp "${ARTIFACTS}/memtrace_seed24.jsonl" "${ARTIFACTS}/memtrace_observations.jsonl"

  run_memfail
  run_stale
  run_analysis

  echo ""
  echo "===== ALL DONE ====="
  echo "Observations in ${ARTIFACTS}/"
  echo "Analysis CSVs in ${ARTIFACTS}/analysis/"
  stop_vllm
}

main
