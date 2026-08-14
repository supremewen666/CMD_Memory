#!/usr/bin/env bash
# CMD experiments: legacy arenas plus V4 single/dual-GPU materialization and replay.

set -euo pipefail
export PYTHONUNBUFFERED=1

# ── Paths ───────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PATH="${SCRIPT_DIR}/$(basename "${BASH_SOURCE[0]}")"
CMD_ROOT="${CMD_ROOT:-$SCRIPT_DIR}"
CMD_VLLM_OVERLAY="${CMD_VLLM_OVERLAY:-$HOME/wsy/runtime/vllm085-transformers451}"
ARTIFACTS="$CMD_ROOT/artifacts/arena"
EVOLUTION_ARTIFACTS="$CMD_ROOT/artifacts/evolution_governance"
SIGIL_ARTIFACTS="$CMD_ROOT/artifacts/sigil_qd"
RUNS_ROOT="${CMD_RUNS_ROOT:-$CMD_ROOT/artifacts/run_control}"
V4_ARTIFACTS="${CMD_V4_ARTIFACTS:-$CMD_ROOT/artifacts/neuro_symbolic_evolution_v4}"
V4_DATASET_DIR="${CMD_V4_DATASET_DIR:-$CMD_ROOT/data/evolution_v4}"
V4_SOURCE_CASES_OVERRIDE="${CMD_V4_SOURCE_CASES:-}"
V4_SOURCE_CASES="${CMD_V4_SOURCE_CASES:-$V4_ARTIFACTS/prepared_cases.jsonl}"
V4_MATERIALIZER_BACKEND="${CMD_V4_MATERIALIZER_BACKEND:-experiments.v4_live_materialization:live_backend}"
V4_CANDIDATE_BUDGET="${CMD_V4_CANDIDATE_BUDGET:-4}"
V4_GHOST_EVALUATOR="${CMD_V4_GHOST_EVALUATOR:-$V4_ARTIFACTS/ghost_ecology_v3/deployment-evaluator-preaction-v1-seed24.json}"
V4_GHOST_PROTOCOL="${CMD_V4_GHOST_PROTOCOL:-$V4_ARTIFACTS/ghost_live_v2/protocol.json}"
V4_GHOST_AUTHORIZATION="${CMD_V4_GHOST_AUTHORIZATION:-$V4_ARTIFACTS/ghost_live_v2/first_test_authorization.json}"
V4_GHOST_ACCESS_LEDGER="${CMD_V4_GHOST_ACCESS_LEDGER:-$V4_ARTIFACTS/ghost_live_v2/access.jsonl}"
V4_MODEL_MANIFEST="${CMD_V4_MODEL_MANIFEST:-$V4_ARTIFACTS/ghost_live_v2/model_manifest.json}"
CROSSJUDGE_MODEL="${CROSSJUDGE_MODEL:-mistral-7b-instruct-v0.3}"
CROSSJUDGE_MODEL_REPO="${CROSSJUDGE_MODEL_REPO:-mistralai/Mistral-7B-Instruct-v0.3}"
CROSSJUDGE_MODEL_DIRNAME="${CROSSJUDGE_MODEL_DIRNAME:-Mistral-7B-Instruct-v0.3}"
JUDGE_MODEL="${JUDGE_MODEL:-qwen2.5-7b-instruct}"
JUDGE_API_KEY="${JUDGE_API_KEY:-dummy}"
CMD_CASE_WORKERS="${CMD_CASE_WORKERS:-32}"
VLLM_READY_TIMEOUT_SECONDS="${VLLM_READY_TIMEOUT_SECONDS:-300}"
CMD_PRETRAINED_LMS_ROOT="${CMD_PRETRAINED_LMS_ROOT:-$HOME/pretrained_lms}"
VLLM_QWEN_GPU_MEMORY_UTILIZATION="${VLLM_QWEN_GPU_MEMORY_UTILIZATION:-0.25}"
VLLM_CROSSJUDGE_GPU_MEMORY_UTILIZATION="${VLLM_CROSSJUDGE_GPU_MEMORY_UTILIZATION:-0.22}"
VLLM_LLAMA_GPU_MEMORY_UTILIZATION="${VLLM_LLAMA_GPU_MEMORY_UTILIZATION:-0.50}"

# ── Arguments ────────────────────────────────────────────────────────────────
ROLE=""
SMOKE=false
ONLY=""
DETACH=false
RUN_ID=""
TARGET_ROLE=""
MONITOR_FOLLOW=true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --role)
      ROLE="$2"; shift 2 ;;
    --smoke)
      SMOKE=true; shift ;;
    --only)
      ONLY="$2"; shift 2 ;;
    --run-id)
      RUN_ID="$2"; shift 2 ;;
    --target-role)
      TARGET_ROLE="$2"; shift 2 ;;
    --once)
      MONITOR_FOLLOW=false; shift ;;
    --detach)
      DETACH=true; shift ;;
    --help|-h)
      echo "Usage: $0 --role ROLE [--run-id ID] [--smoke] [--only NAME] [--detach]"
      echo ""
      echo "V4 roles:"
      echo "  v4_prepare:    build if absent, then validate the CPU dataset package"
      echo "  v4_prepare_inputs: freeze relation cache/graphs/intents on GPU 0"
      echo "  v4_single_gpu: materialize all frozen cases on one A100 (recommended)"
      echo "  v4_gpu0:       materialize SHA256(case_id)%2 == 0 on GPU 0"
      echo "  v4_gpu1:       materialize SHA256(case_id)%2 == 1 on GPU 1"
      echo "  v4_merge:      preflight, verify single/dual shards, then canonical eight-arm replay"
      echo "  monitor:       stream lifecycle + experiment JSONL for --run-id"
      echo "  status|stop:   inspect/terminate --target-role under --run-id"
      echo ""
      echo "GPU 0: physical id 0, ports 8000/8001 (override CMD_GPU0_ID/CMD_GPU0_PORT_BASE)"
      echo "GPU 1: physical id 1, ports 8000/8001 (override CMD_GPU1_ID/CMD_GPU1_PORT_BASE)"
      echo "single A100: physical id 0, ports 8000/8001 (uses v4_single_gpu)"
      echo ""
      echo "Legacy roles: gpu0, gpu1, analyze, phase1_gpu0, phase1_gpu1,"
      echo "              phase1_analyze, sigil_gpu0, sigil_gpu1, sigil_analyze, route_a"
      echo ""
      echo "--detach creates launch.json, run.pid, run.log, and status.jsonl."
      echo "Use the same explicit --run-id for v4_single_gpu/dual lanes, monitor, and v4_merge."
      echo ""
      echo "  GPU 0 legacy: MemTrace-B seeds 24+124 → MemFail"
      echo "  GPU 1 legacy: MemTrace-B seed 224 → STALE → replicate seed 24"
      echo "  analyze:        unified analysis (run after scp-ing results to one machine)"
      echo "  phase1_gpu0:    evolution-on vs frozen on MemTrace (same GPU/endpoints)"
      echo "  phase1_gpu1:    evolution-on vs frozen on STALE (same GPU/endpoints)"
      echo "  phase1_analyze: merge the two per-GPU Phase 1 summaries (zero calls)"
      echo "  sigil_gpu0:     Stage 1 live item-gate shadow on MemTrace + MemFail"
      echo "  sigil_gpu1:     Stage 1 live item-gate shadow on STALE"
      echo "  sigil_analyze:  repair-validity audit + scope ledger (zero calls)"
      echo "  route_a:        Route A §15 chain; every stage gated, E0 STOP refuses downstream"
      echo "  --detach:       supervised background run with machine-readable lifecycle"
      echo ""
      echo "  --only:  skip straight to one named phase (memtrace_seed24, memtrace_seed124,"
      echo "           memtrace_seed224, memfail, stale, memtrace_llama,"
      echo "           memtrace_crossjudge, memtrace_stuffing; SIGIL roles:"
      echo "           memtrace, memfail, stale; route_a: e0, bridge, power,"
      echo "           validate, e0b, e3, e4, e5)"
      exit 0 ;;
    *)
      echo "Unknown: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$ROLE" ]]; then
  echo "ERROR: --role is required; see --help" >&2
  exit 1
fi

if $SMOKE && [[ -z "$V4_SOURCE_CASES_OVERRIDE" ]]; then
  V4_SOURCE_CASES="$V4_ARTIFACTS/prepared_cases.smoke.jsonl"
fi

if [[ -z "$RUN_ID" ]]; then
  RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
fi
if [[ ! "$RUN_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "ERROR: --run-id may contain only letters, digits, dot, underscore, hyphen" >&2
  exit 1
fi

LANE_GPU_ID=""
LANE_LABEL="cpu"
LANE_PORT_BASE=8000
case "$ROLE" in
  gpu0|phase1_gpu0|sigil_gpu0|v4_prepare_inputs|v4_single_gpu|v4_gpu0)
    LANE_GPU_ID="${CMD_GPU0_ID:-0}"
    LANE_LABEL="gpu0"
    LANE_PORT_BASE="${CMD_GPU0_PORT_BASE:-8000}"
    ;;
  gpu1|phase1_gpu1|sigil_gpu1|v4_gpu1)
    LANE_GPU_ID="${CMD_GPU1_ID:-1}"
    LANE_LABEL="gpu1"
    LANE_PORT_BASE="${CMD_GPU1_PORT_BASE:-8000}"
    ;;
esac

QWVLLM_PORT="${CMD_QWVLLM_PORT:-$LANE_PORT_BASE}"
LLAMA_JUDGE_PORT="${CMD_LLAMA_JUDGE_PORT:-$LANE_PORT_BASE}"
LLAMA_ANSWER_PORT="${CMD_LLAMA_ANSWER_PORT:-$((LANE_PORT_BASE + 1))}"
CROSSJUDGE_PORT="${CROSSJUDGE_PORT:-$((LANE_PORT_BASE + 2))}"
PID_FILE="${CMD_VLLM_PID_FILE:-/tmp/vllm_shared_${LANE_LABEL}.pid}"
LOG_FILE="${CMD_VLLM_LOG_FILE:-/tmp/vllm_shared_${LANE_LABEL}.log}"
JUDGE_BASE_URL="${JUDGE_BASE_URL:-http://localhost:${LLAMA_JUDGE_PORT}/v1}"
V4_RUN_ROOT="${V4_ARTIFACTS}/runs/${RUN_ID}"
CMD_RUN_DIR="${CMD_RUN_DIR:-${RUNS_ROOT}/${RUN_ID}/${ROLE}}"

if $DETACH; then
  case "$ROLE" in
    monitor|status|stop)
      echo "ERROR: control roles cannot be detached" >&2
      exit 1 ;;
  esac
  detach_args=("$SCRIPT_PATH" --role "$ROLE" --run-id "$RUN_ID")
  $SMOKE && detach_args+=(--smoke)
  [[ -n "$ONLY" ]] && detach_args+=(--only "$ONLY")
  [[ -n "$TARGET_ROLE" ]] && detach_args+=(--target-role "$TARGET_ROLE")
  gpu_args=()
  [[ -n "$LANE_GPU_ID" ]] && gpu_args=(--gpu-id "$LANE_GPU_ID")
  cd "$CMD_ROOT"
  python -m experiments.detached_run launch \
    --run-dir "$CMD_RUN_DIR" \
    --run-id "$RUN_ID" \
    --role "$ROLE" \
    "${gpu_args[@]}" \
    -- "${detach_args[@]}"
  echo "[detach] control dir: ${CMD_RUN_DIR}"
  echo "[detach] monitor: ${SCRIPT_PATH} --role monitor --run-id ${RUN_ID}"
  echo "[detach] status:  ${SCRIPT_PATH} --role status --run-id ${RUN_ID} --target-role ${ROLE}"
  exit 0
fi

mkdir -p "$CMD_RUN_DIR"
cd "$CMD_ROOT"

# ══════════════════════════════════════════════════════════════════════════════
# vLLM lifecycle
# ══════════════════════════════════════════════════════════════════════════════

resolve_qwen_dir() {
  if [[ -n "${CMD_QWEN_MODEL_DIR:-}" ]]; then
    if [[ ! -d "$CMD_QWEN_MODEL_DIR" ]]; then
      echo "ERROR: CMD_QWEN_MODEL_DIR is not a directory: $CMD_QWEN_MODEL_DIR" >&2
      return 1
    fi
    printf '%s\n' "$CMD_QWEN_MODEL_DIR"
    return 0
  fi

  local pretrained_dir="${CMD_PRETRAINED_LMS_ROOT}/Qwen2.5-7B-Instruct"
  if [[ -d "$pretrained_dir" ]]; then
    printf '%s\n' "$pretrained_dir"
    return 0
  fi

  env PYTHONPATH="$CMD_VLLM_OVERLAY" HF_HUB_OFFLINE=1 python - <<'PY'
from huggingface_hub import snapshot_download
print(snapshot_download("Qwen/Qwen2.5-7B-Instruct", local_files_only=True))
PY
}

resolve_crossjudge_dir() {
  if [[ -n "${CMD_CROSSJUDGE_MODEL_DIR:-}" ]]; then
    if [[ ! -d "$CMD_CROSSJUDGE_MODEL_DIR" ]]; then
      echo "ERROR: CMD_CROSSJUDGE_MODEL_DIR is not a directory: $CMD_CROSSJUDGE_MODEL_DIR" >&2
      return 1
    fi
    printf '%s\n' "$CMD_CROSSJUDGE_MODEL_DIR"
    return 0
  fi

  local pretrained_dir="${CMD_PRETRAINED_LMS_ROOT}/${CROSSJUDGE_MODEL_DIRNAME}"
  if [[ -d "$pretrained_dir" ]]; then
    printf '%s\n' "$pretrained_dir"
    return 0
  fi

  env PYTHONPATH="$CMD_VLLM_OVERLAY" HF_HUB_OFFLINE=1 \
    CROSSJUDGE_MODEL_REPO="$CROSSJUDGE_MODEL_REPO" python - <<'PY'
import os
from huggingface_hub import snapshot_download
print(snapshot_download(os.environ["CROSSJUDGE_MODEL_REPO"], local_files_only=True))
PY
}

resolve_llama_dir() {
  if [[ -n "${CMD_LLAMA_MODEL_DIR:-}" ]]; then
    if [[ ! -d "$CMD_LLAMA_MODEL_DIR" ]]; then
      echo "ERROR: CMD_LLAMA_MODEL_DIR is not a directory: $CMD_LLAMA_MODEL_DIR" >&2
      return 1
    fi
    printf '%s\n' "$CMD_LLAMA_MODEL_DIR"
    return 0
  fi

  local pretrained_dir="${CMD_PRETRAINED_LMS_ROOT}/Meta-Llama-3.1-8B-Instruct"
  if [[ -d "$pretrained_dir" ]]; then
    printf '%s\n' "$pretrained_dir"
    return 0
  fi

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
    CUDA_VISIBLE_DEVICES="${LANE_GPU_ID:-${CUDA_VISIBLE_DEVICES:-0}}" \
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

wait_for_vllm_endpoint() {
  local port="$1"
  local label="$2"

  echo -n "[vLLM] waiting for ${label} on port ${port} …"
  for i in $(seq 1 "$VLLM_READY_TIMEOUT_SECONDS"); do
    if curl --noproxy '*' -s "localhost:${port}/v1/models" >/dev/null 2>&1; then
      echo " ready (${i}s)"
      return 0
    fi
    sleep 1
    echo -n "."
  done
  echo " TIMEOUT"
  return 1
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
      CUDA_VISIBLE_DEVICES="${LANE_GPU_ID:-${CUDA_VISIBLE_DEVICES:-0}}" \
      vllm serve "$CMD_QWEN_MODEL_DIR" \
      --host 127.0.0.1 \
      --served-model-name qwen2.5-7b-instruct \
      --port "$LLAMA_JUDGE_PORT" \
      --gpu-memory-utilization "$VLLM_QWEN_GPU_MEMORY_UTILIZATION" \
      --max-model-len 8192 \
      --max-num-seqs 32 \
      --enable-prefix-caching \
      > "/tmp/vllm_qwen_judge_${LANE_LABEL}.log" 2>&1 &
    echo "[vLLM] Qwen judge pid $!"
    # Do not load both 8B models concurrently on one GPU: simultaneous KV-cache
    # profiling can leave either engine with no usable cache blocks.
    wait_for_vllm_endpoint "$LLAMA_JUDGE_PORT" "Qwen judge"
  fi

  if ! $llma_up; then
    # The second engine starts after Qwen and needs a larger share of the
    # remaining GPU-memory budget to reserve its KV cache.
    nohup env \
      PYTHONPATH="$CMD_VLLM_OVERLAY" \
      HF_HUB_OFFLINE=1 \
      TRANSFORMERS_OFFLINE=1 \
      CUDA_VISIBLE_DEVICES="${LANE_GPU_ID:-${CUDA_VISIBLE_DEVICES:-0}}" \
      vllm serve "$CMD_LLAMA_MODEL_DIR" \
      --host 127.0.0.1 \
      --served-model-name llama-3.1-8b-instruct \
      --port "$LLAMA_ANSWER_PORT" \
      --gpu-memory-utilization "$VLLM_LLAMA_GPU_MEMORY_UTILIZATION" \
      --max-model-len 8192 \
      --max-num-seqs 32 \
      --enable-prefix-caching \
      > "/tmp/vllm_llama_answer_${LANE_LABEL}.log" 2>&1 &
    echo "[vLLM] Llama answerer pid $!"
    wait_for_vllm_endpoint "$LLAMA_ANSWER_PORT" "Llama answerer"
  fi
}

start_crossjudge_vllm() {
  # Third engine, evaluation judge only. Started alongside the dual pair, so
  # three 8B engines share the GPU; give this one an explicit small share.
  if curl --noproxy '*' -s "localhost:${CROSSJUDGE_PORT}/v1/models" >/dev/null 2>&1; then
    echo "[vLLM] cross-judge endpoint already up on ${CROSSJUDGE_PORT}, reusing"
    return 0
  fi

  local model_dir
  model_dir="$(resolve_crossjudge_dir)"
  echo "[vLLM] cross-judge dir: $model_dir"

  nohup env \
    PYTHONPATH="$CMD_VLLM_OVERLAY" \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    CUDA_VISIBLE_DEVICES="${LANE_GPU_ID:-${CUDA_VISIBLE_DEVICES:-0}}" \
    vllm serve "$model_dir" \
    --host 127.0.0.1 \
    --served-model-name "$CROSSJUDGE_MODEL" \
    --port "$CROSSJUDGE_PORT" \
    --gpu-memory-utilization "$VLLM_CROSSJUDGE_GPU_MEMORY_UTILIZATION" \
    --max-model-len 8192 \
    --max-num-seqs 32 \
    --enable-prefix-caching \
    > "/tmp/vllm_crossjudge_${LANE_LABEL}.log" 2>&1 &
  echo "[vLLM] cross-judge pid $!"
  wait_for_vllm_endpoint "$CROSSJUDGE_PORT" "cross-judge"
}

stop_crossjudge_vllm() {
  local pid
  pid=$(lsof -ti ":$CROSSJUDGE_PORT" 2>/dev/null || true)
  if [[ -n "$pid" ]]; then
    echo "[vLLM] stopping cross-judge port $CROSSJUDGE_PORT pid $pid"
    kill "$pid" || true
  fi
  sleep 2
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

crossjudge_env() {
  # Same answerer and selection judge as llama_dual_env; only the evaluation
  # (shadow) judge moves. That is what makes the rerun a judge-robustness
  # check rather than a different experiment: if the headline survives here,
  # it is not an artifact of the Qwen evaluation judge specifically.
  llama_dual_env
  export LLM_JUDGE_BASE_URL="http://localhost:${CROSSJUDGE_PORT}/v1"
  export LLM_JUDGE_MODEL="$CROSSJUDGE_MODEL"
  export LLM_JUDGE_API_KEY="${CROSSJUDGE_API_KEY:-dummy}"
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
# Route A (BUILD SPEC §15 chain, §12.3 gating)
#
# 每条 Route A 命令都必须先过 §15 的门。门是累积的：E0 STOP 会一路拒到 E5，
# 即使 E0b 自身的工件齐备也一样 —— E3 是花掉 §16 的 3x150 提案预算的阶段。
# 判决逻辑在 experiments/check_route_a_gates.py（可测、零 LLM 调用），
# 这里只负责调用它并在拒绝时停下。
#
# E-1 套件的通过与否不由门自己发现（它不 shell out 到 pytest），由
# route_a_gate 先跑一遍再作为 --tests-pass 传进去。
# ══════════════════════════════════════════════════════════════════════════════

ROUTE_A_E_MINUS_1_TESTS=(
  tests/counterfactual/
  tests/experiments/test_tier3_power.py
  tests/experiments/test_route_a_gates.py
)

route_a_e_minus_1_tests() {
  echo "[route-a] E-1 suite"
  python -m pytest "${ROUTE_A_E_MINUS_1_TESTS[@]}" -q
}

# route_a_gate <stage> — 通过则返回 0，拒绝则打印整条链并返回 1
route_a_gate() {
  local stage="$1"
  local tests_flag=()
  if route_a_e_minus_1_tests; then
    tests_flag=(--tests-pass)
  else
    echo "[route-a] E-1 suite failed; every stage is refused" >&2
  fi
  python -m experiments.check_route_a_gates --stage "$stage" "${tests_flag[@]}"
}

# route_a_step <stage> <label> <命令...> — 过门才执行
route_a_step() {
  local stage="$1"; shift
  local label="$1"; shift
  if ! route_a_gate "$stage"; then
    echo "[route-a] SKIP ${label}: §15 gate refused stage ${stage}"
    return 1
  fi
  echo "===== Route A: ${label} ====="
  "$@"
}

run_route_a_e0() {
  route_a_step e0 "E0 closed-grammar enumeration" \
    python -m experiments.run_closed_grammar_enumeration
}

run_route_a_bridge() {
  route_a_step bridge "E1.5 state-answer bridge" \
    python -m experiments.run_state_answer_bridge
}

run_route_a_power() {
  # §4 用 D_dev 方差定 n_tier3，必须在任何工件存在之前跑，所以它挂在 E0 那道门上
  # 而不是 tier-3 冻结那道门上 —— 反过来会让 n 依赖它将要检验的东西。
  route_a_step e0 "§4 mechanical tier-3 sample size" \
    python -m experiments.compute_tier3_power
}

run_route_a_validate() {
  # §5.4 是 tier-3 冻结的前置条件，而 tier-3 冻结又是 e0b 那道门的一个合取项，
  # 所以它必须挂在 e0 门上而不是 e0b 门上 —— 挂在后者会让审计等待它自己的产出。
  route_a_step e0 "§5.4 tier-3 dataset validity audit" \
    python -m experiments.validate_tier3_dataset
}

run_route_a_e0b() {
  route_a_step e0b "E0b pre-search envelope" \
    python -m experiments.run_shallow_ir_enumeration
}

run_route_a_e3() {
  echo "[route-a] RETIRED E3 open grammar synthesis; use run_memory_evolution_v4" >&2
  return 1
}

run_route_a_e4() {
  route_a_step e4 "E4 artifact selection and freeze" \
    python -m experiments.select_route_a_artifact
}

run_route_a_e5() {
  # §16: 恰好一次确证读取，且失败后不救。门里 MAX_PRIOR_E5_RUNS = 0 是唯一
  # 能挡住第二次读的东西 —— 确证命令自己不记得跑过。
  route_a_step e5 "E5 one-shot confirmation" \
    python -m experiments.run_route_a_confirmation
}

main_route_a() {
  mkdir -p artifacts/route_a
  case "$ONLY" in
    e0)     run_route_a_e0 ;;
    bridge) run_route_a_bridge ;;
    power)  run_route_a_power ;;
    validate) run_route_a_validate ;;
    e0b)    run_route_a_e0b ;;
    e3)     run_route_a_e3 ;;
    e4)     run_route_a_e4 ;;
    e5)     run_route_a_e5 ;;
    "")
      # 整链顺跑。每步自己过门，所以第一道拒绝之后后面全部 SKIP，
      # 而不是靠 set -e 中断 —— 被门拒绝是预期结果，不是脚本错误。
      run_route_a_e0     || true
      run_route_a_bridge || true
      run_route_a_power  || true
      run_route_a_validate || true
      run_route_a_e0b    || true
      run_route_a_e3     || true
      run_route_a_e4     || true
      run_route_a_e5     || true
      ;;
    *)
      echo "ERROR: Route A --only must be one of e0|bridge|power|validate|e0b|e3|e4|e5" >&2
      return 1 ;;
  esac
  echo ""
  echo "===== ROUTE A DONE ====="
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
      --chains --case-workers 1 --best-of-n-control --deposit-after 0.5 --deposit-min-benefit 0.05 --deposit-min-support 10 \
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
      $([[ -s "${ARTIFACTS}/memtrace_crossjudge.jsonl" ]] \
        && printf '%s' "${ARTIFACTS}/memtrace_crossjudge.jsonl") \
      $([[ -s "${ARTIFACTS}/memtrace_stuffing.jsonl" ]] \
        && printf '%s' "${ARTIFACTS}/memtrace_stuffing.jsonl") \
    --output-dir "${ARTIFACTS}/analysis"
  echo "[done] Analysis → ${ARTIFACTS}/analysis/"
}

require_phase0_summary() {
  local summary="${EVOLUTION_ARTIFACTS}/phase0/phase0_summary.json"
  if [[ ! -s "$summary" ]]; then
    echo "ERROR: Phase 0 summary is missing: ${summary}" >&2
    echo "Run Phase 0 after gathering the four MemTrace JSONLs, or copy the" >&2
    echo "same phase0_summary.json to this GPU host before Phase 1." >&2
    return 1
  fi
  python - "$summary" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
if payload.get("phase1_gate_passed") is not True:
    raise SystemExit(f"Phase 0 gate did not authorize Phase 1: {path}")
print(f"[gate] Phase 0 authorized Phase 1: {path}")
PY
}

run_phase1_arena() {
  local arena="$1"
  local lane="$2"
  local output_dir="${EVOLUTION_ARTIFACTS}/phase1/${lane}"
  local summary="${output_dir}/phase1_summary.json"
  local phase0_summary="${EVOLUTION_ARTIFACTS}/phase0/phase0_summary.json"

  if [[ -s "$summary" ]] && ! $SMOKE; then
    echo "[skip] ${summary} exists and is non-empty; archive it to rerun"
    return 0
  fi
  if $SMOKE; then
    output_dir="${EVOLUTION_ARTIFACTS}/phase1/${lane}_smoke"
  fi
  mkdir -p "$output_dir"
  echo "===== Phase 1: ${arena} evolution-on vs all-frozen ====="
  local phase1_args=(
    --phase0-summary "$phase0_summary"
    --arena "$arena"
    --output-dir "$output_dir"
    --candidate-budget 2
    --live
  )
  $SMOKE && phase1_args+=(--limit 50 --permutations 199 --bootstrap-samples 500)
  python -m experiments.run_evolution_governance_phase1 \
    "${phase1_args[@]}" \
    2>&1 | tee "${output_dir}/phase1_${arena}.log"
  echo "[done] Phase 1 ${arena} → ${output_dir}"
}

run_phase1_analysis() {
  local gpu0_summary="${EVOLUTION_ARTIFACTS}/phase1/gpu0/phase1_summary.json"
  local gpu1_summary="${EVOLUTION_ARTIFACTS}/phase1/gpu1/phase1_summary.json"
  local output_dir="${EVOLUTION_ARTIFACTS}/phase1/combined"
  if [[ ! -s "$gpu0_summary" || ! -s "$gpu1_summary" ]]; then
    echo "ERROR: both per-GPU Phase 1 summaries are required:" >&2
    echo "  ${gpu0_summary}" >&2
    echo "  ${gpu1_summary}" >&2
    return 1
  fi
  python -m experiments.run_evolution_governance_phase1 \
    --merge-summaries "$gpu0_summary" "$gpu1_summary" \
    --output-dir "$output_dir"
  echo "[done] Phase 1 combined analysis → ${output_dir}"
}

# ══════════════════════════════════════════════════════════════════════════════
# SIGIL-QD Stage 1: live item-gate shadow channel and audited scope ledger
# ══════════════════════════════════════════════════════════════════════════════

run_sigil_shadow_arena() {
  local arena="$1"
  local lane="$2"
  local seed="$3"
  local module=""
  case "$arena" in
    memtrace) module="experiments.run_arena_memtrace" ;;
    memfail)  module="experiments.run_arena_memfail" ;;
    stale)    module="experiments.run_arena_stale" ;;
    *)
      echo "ERROR: unsupported SIGIL arena: ${arena}" >&2
      return 1
      ;;
  esac

  local output_dir="${SIGIL_ARTIFACTS}/stage1/${lane}"
  local suffix=""
  local smoke_args=()
  if $SMOKE; then
    suffix="_smoke"
    smoke_args=(--limit 20)
  fi
  local output="${output_dir}/${arena}_live_item_gate${suffix}.jsonl"
  local log="${output_dir}/${arena}_live_item_gate${suffix}.log"
  mkdir -p "$output_dir"
  if [[ -s "$output" ]]; then
    echo "[skip] ${output} exists and is non-empty; archive it to rerun"
    return 0
  fi

  echo "===== SIGIL Stage 1: ${arena} live item-gate shadow ====="
  python -m "$module" \
    --backend-factory experiments.arena_backends:create_vllm_backend \
    --seed "$seed" \
    --no-chains \
    --case-workers "$CMD_CASE_WORKERS" \
    --live-item-gate \
    "${smoke_args[@]}" \
    --output "$output" \
    2>&1 | tee "$log"
  echo "[done] SIGIL Stage 1 ${arena} → ${output}"
}

run_sigil_stage1_analysis() {
  local suffix=""
  local audit_dir="${SIGIL_ARTIFACTS}/stage1/audit"
  local audit_args=(
    --n-min 30
    --validity-threshold 0.8
    --bootstrap-samples 10000
    --bootstrap-seed 24
  )
  if $SMOKE; then
    suffix="_smoke"
    audit_dir="${SIGIL_ARTIFACTS}/stage1/audit_smoke"
    audit_args=(
      --n-min 1
      --validity-threshold 0.8
      --bootstrap-samples 500
      --bootstrap-seed 24
    )
  fi
  local inputs=(
    "${SIGIL_ARTIFACTS}/stage1/gpu0/memtrace_live_item_gate${suffix}.jsonl"
    "${SIGIL_ARTIFACTS}/stage1/gpu0/memfail_live_item_gate${suffix}.jsonl"
    "${SIGIL_ARTIFACTS}/stage1/gpu1/stale_live_item_gate${suffix}.jsonl"
  )
  local input
  for input in "${inputs[@]}"; do
    if [[ ! -s "$input" ]]; then
      echo "ERROR: missing SIGIL Stage 1 artifact: ${input}" >&2
      echo "Run --role sigil_gpu0 and --role sigil_gpu1, then scp both lanes here." >&2
      return 1
    fi
  done

  mkdir -p "$audit_dir"
  echo "===== SIGIL Stage 1: repair-validity scope audit ====="
  python -m experiments.analyze_item_gate_scope \
    --inputs "${inputs[@]}" \
    --output-dir "$audit_dir" \
    "${audit_args[@]}" \
    2>&1 | tee "${audit_dir}/stage1_audit.log"
  python - "${audit_dir}/stage1_summary.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
if payload.get("stage1_gate_passed") is True:
    print(f"[gate] SIGIL Stage 1 GO; Stage 2 is authorized by {path}")
else:
    print(f"[gate] SIGIL Stage 1 NO-GO; Stage 2/3 remain stopped by {path}")
PY
  echo "[done] SIGIL Stage 1 audit → ${audit_dir}"
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
    memtrace_crossjudge)
      # Seed 24 so the rows pair case-for-case against the seed-24 headline.
      start_crossjudge_vllm
      crossjudge_env
      gate_g0
      run_memtrace "crossjudge" --seed 24
      stop_crossjudge_vllm
      stop_llama_dual_vllm
      return 0
      ;;
    memtrace_stuffing)
      # Seed 24 again, so stuffing pairs against the same headline cases.
      run_memtrace "stuffing" --seed 24 --context-stuffing-control
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
      echo "  valid: memtrace_seed224, stale, memtrace_llama, memtrace_crossjudge," >&2
      echo "         memtrace_stuffing" >&2
      exit 1
      ;;
  esac

  echo ""
  echo "===== GPU 1 DONE ====="
  stop_llama_dual_vllm
}

# ══════════════════════════════════════════════════════════════════════════════
# Phase 1: keep both arms of one arena on the same GPU/model endpoints
# ══════════════════════════════════════════════════════════════════════════════

main_phase1_gpu0() {
  require_phase0_summary
  start_llama_dual_vllm
  llama_dual_env
  gate_g0
  run_phase1_arena "memtrace" "gpu0"
  stop_llama_dual_vllm
  echo "===== PHASE 1 GPU 0 DONE ====="
}

main_phase1_gpu1() {
  require_phase0_summary
  start_llama_dual_vllm
  llama_dual_env
  gate_g0
  run_phase1_arena "stale" "gpu1"
  stop_llama_dual_vllm
  echo "===== PHASE 1 GPU 1 DONE ====="
}

# ══════════════════════════════════════════════════════════════════════════════
# Analyze: run after scp-ing all JSONL to one machine
# ══════════════════════════════════════════════════════════════════════════════

main_analyze() {
  run_analysis
  echo ""
  echo "===== ANALYSIS DONE ====="
}

main_phase1_analyze() {
  run_phase1_analysis
  echo ""
  echo "===== PHASE 1 ANALYSIS DONE ====="
}

main_sigil_gpu0() {
  mkdir -p "${SIGIL_ARTIFACTS}/stage1/gpu0"
  start_llama_dual_vllm
  llama_dual_env
  gate_g0
  case "$ONLY" in
    memtrace)
      run_sigil_shadow_arena "memtrace" "gpu0" 24
      ;;
    memfail)
      run_sigil_shadow_arena "memfail" "gpu0" 24
      ;;
    "")
      run_sigil_shadow_arena "memtrace" "gpu0" 24
      run_sigil_shadow_arena "memfail" "gpu0" 24
      ;;
    *)
      echo "ERROR: SIGIL GPU 0 --only must be memtrace or memfail" >&2
      return 1
      ;;
  esac
  stop_llama_dual_vllm
  echo "===== SIGIL STAGE 1 GPU 0 DONE ====="
}

main_sigil_gpu1() {
  mkdir -p "${SIGIL_ARTIFACTS}/stage1/gpu1"
  start_llama_dual_vllm
  llama_dual_env
  gate_g0
  case "$ONLY" in
    stale|"")
      run_sigil_shadow_arena "stale" "gpu1" 24
      ;;
    *)
      echo "ERROR: SIGIL GPU 1 --only must be stale" >&2
      return 1
      ;;
  esac
  stop_llama_dual_vllm
  echo "===== SIGIL STAGE 1 GPU 1 DONE ====="
}

main_sigil_analyze() {
  run_sigil_stage1_analysis
  echo ""
  echo "===== SIGIL STAGE 1 ANALYSIS DONE ====="
}

# ══════════════════════════════════════════════════════════════════════════════
# V4: parallel materialization, single canonical prequential replay
# ══════════════════════════════════════════════════════════════════════════════

main_v4_prepare() {
  local manifest="${V4_DATASET_DIR}/dataset_manifest.json"
  local validation="${V4_DATASET_DIR}/validation_report.json"
  if [[ ! -f "$manifest" ]]; then
    python -m experiments.build_v4_evolution_dataset \
      --output-dir "$V4_DATASET_DIR"
  fi
  python -m experiments.validate_v4_evolution_dataset \
    --dataset-dir "$V4_DATASET_DIR" \
    --output "$validation"
  echo "===== V4 CPU DATASET PASS: ${manifest} ====="
}

main_v4_prepare_inputs() {
  local flavor="full"
  local prepared="${V4_ARTIFACTS}/prepared_cases.jsonl"
  local preparation_dir="${V4_ARTIFACTS}/preparation/full"
  local validation="${V4_ARTIFACTS}/prepared_cases.validation.json"
  local attempt_manifest="${V4_ARTIFACTS}/preparation/full/preparation_attempt_manifest.json"
  local limit_args=()
  if $SMOKE; then
    flavor="smoke"
    prepared="${V4_ARTIFACTS}/prepared_cases.smoke.jsonl"
    preparation_dir="${V4_ARTIFACTS}/preparation/smoke"
    validation="${V4_ARTIFACTS}/prepared_cases.smoke.validation.json"
    attempt_manifest="${V4_ARTIFACTS}/preparation/smoke/preparation_attempt_manifest.json"
    limit_args=(--limit "${CMD_V4_SMOKE_CASES:-20}")
  fi
  local manifest="${preparation_dir}/preparation_manifest.json"
  local cache="${V4_ARTIFACTS}/preparation/relation_cache.sqlite"
  if [[ -f "$prepared" || -f "$manifest" ]]; then
    if [[ ! -f "$prepared" || ! -f "$manifest" ]]; then
      echo "ERROR: incomplete immutable V4 preparation artifacts for ${flavor}" >&2
      return 1
    fi
    python -m experiments.validate_v4_prepared_cases \
      --dataset-dir "$V4_DATASET_DIR" \
      --prepared "$prepared" \
      --manifest "$manifest" \
      --output "$validation"
    echo "===== V4 ${flavor} GPU INPUT ALREADY VALID: ${prepared} ====="
    return 0
  fi

  local started_vllm=false
  if ! start_llama_dual_vllm; then
    stop_llama_dual_vllm
    return 1
  fi
  started_vllm=true
  llama_dual_env
  local code=0
  python -m experiments.prepare_v4_live_cases \
    --dataset-dir "$V4_DATASET_DIR" \
    --output "$prepared" \
    --artifacts-dir "$preparation_dir" \
    --cache "$cache" \
    --progress "${CMD_RUN_DIR}/progress.jsonl" \
    --candidate-budget "$V4_CANDIDATE_BUDGET" \
    --max-uncertain-rate "${CMD_V4_MAX_UNCERTAIN_RATE:-0.05}" \
    --max-relation-attempts "${CMD_V4_MAX_RELATION_ATTEMPTS:-3}" \
    --max-proposer-retries "${CMD_V4_MAX_PROPOSER_RETRIES:-2}" \
    --collect-proposer-failures \
    "${limit_args[@]}" || code=$?
  if $started_vllm; then
    stop_llama_dual_vllm
  fi
  if [[ $code -ne 0 ]]; then
    return "$code"
  fi
  if [[ ! -f "$prepared" || ! -f "$manifest" ]]; then
    if [[ -f "$attempt_manifest" ]]; then
      local attempt_status
      attempt_status="$(python -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["build_status"])' "$attempt_manifest")"
      if [[ "$attempt_status" == "repair_required" ]]; then
        python -c 'import json,sys; value=json.load(open(sys.argv[1], encoding="utf-8")); print(json.dumps({key:value[key] for key in ("build_status","selected_case_count","prepared_case_count","quarantined_case_count","quarantined_case_ids","attempt_manifest_sha256")}, ensure_ascii=False, sort_keys=True))' "$attempt_manifest"
        echo "===== V4 ${flavor} INPUT COLLECTION COMPLETE; REPAIR REQUIRED: ${attempt_manifest} ====="
        return 0
      fi
    fi
    echo "ERROR: V4 input preparation returned without an authorized final bundle" >&2
    return 1
  fi
  python -m experiments.validate_v4_prepared_cases \
    --dataset-dir "$V4_DATASET_DIR" \
    --prepared "$prepared" \
    --manifest "$manifest" \
    --output "$validation"
  echo "===== V4 ${flavor} GPU INPUT PASS: ${prepared} ====="
}

main_v4_materialize() {
  local lane="$1"
  local output_dir="${V4_RUN_ROOT}/materialized"
  local output="${output_dir}/${lane}.jsonl"
  local progress="${CMD_RUN_DIR}/progress.jsonl"
  local preparation_manifest="${CMD_V4_PREPARATION_MANIFEST:-$V4_ARTIFACTS/preparation/full/preparation_manifest.json}"
  local input_validation="${CMD_RUN_DIR}/prepared_input_validation.json"
  local live_preflight="${CMD_RUN_DIR}/ghost_live_preflight.json"
  local limit_args=()
  if $SMOKE && [[ -z "${CMD_V4_PREPARATION_MANIFEST:-}" ]]; then
    preparation_manifest="$V4_ARTIFACTS/preparation/smoke/preparation_manifest.json"
  fi
  if [[ -n "$V4_SOURCE_CASES_OVERRIDE" && -z "${CMD_V4_PREPARATION_MANIFEST:-}" ]]; then
    echo "ERROR: CMD_V4_SOURCE_CASES override requires CMD_V4_PREPARATION_MANIFEST" >&2
    return 1
  fi
  mkdir -p "$output_dir"
  if [[ ! -f "$V4_SOURCE_CASES" ]]; then
    echo "ERROR: V4 source cases not found: ${V4_SOURCE_CASES}" >&2
    echo "Set CMD_V4_SOURCE_CASES to frozen prepared/materialized case JSONL." >&2
    return 1
  fi
  if [[ ! -f "$preparation_manifest" ]]; then
    echo "ERROR: V4 preparation manifest not found: ${preparation_manifest}" >&2
    echo "Run --role v4_prepare_inputs before starting either V4 GPU lane." >&2
    return 1
  fi
  for required in "$V4_GHOST_PROTOCOL" "$V4_GHOST_AUTHORIZATION" \
                  "$V4_GHOST_ACCESS_LEDGER" \
                  "$V4_MODEL_MANIFEST" "$V4_GHOST_EVALUATOR"; do
    if [[ ! -f "$required" ]]; then
      echo "ERROR: frozen GHOST live prerequisite not found: ${required}" >&2
      return 1
    fi
  done
  if ! python -m experiments.ghost_live_protocol validate-run \
    --root "$CMD_ROOT" \
    --cases "$V4_SOURCE_CASES" \
    --protocol "$V4_GHOST_PROTOCOL" \
    --authorization "$V4_GHOST_AUTHORIZATION" \
    --access-ledger "$V4_GHOST_ACCESS_LEDGER" \
    --model-manifest "$V4_MODEL_MANIFEST" \
    --evaluator "$V4_GHOST_EVALUATOR" \
    --preparation-manifest "$preparation_manifest" \
    --candidate-budget "$V4_CANDIDATE_BUDGET" \
    --run-id "$RUN_ID" \
    --output "$live_preflight"; then
    echo "ERROR: GHOST live freeze/access gate refused this run" >&2
    return 1
  fi
  if ! python -m experiments.validate_v4_prepared_cases \
    --dataset-dir "$V4_DATASET_DIR" \
    --prepared "$V4_SOURCE_CASES" \
    --manifest "$preparation_manifest" \
    --output "$input_validation"; then
    echo "ERROR: V4 prepared-input hash/leakage gate refused ${V4_SOURCE_CASES}" >&2
    return 1
  fi
  $SMOKE && limit_args=(--limit "${CMD_V4_SMOKE_CASES:-20}")

  local started_vllm=false
  if [[ "$V4_MATERIALIZER_BACKEND" != "experiments.v4_materialization:passthrough_backend" ]]; then
    if ! start_llama_dual_vllm; then
      stop_llama_dual_vllm
      return 1
    fi
    started_vllm=true
    llama_dual_env
    if ! gate_g0; then
      stop_llama_dual_vllm
      return 1
    fi
  fi
  local code=0
  python -m experiments.v4_materialization materialize \
    --source "$V4_SOURCE_CASES" \
    --lane "$lane" \
    --backend "$V4_MATERIALIZER_BACKEND" \
    --output "$output" \
    --progress "$progress" \
    "${limit_args[@]}" || code=$?
  if $started_vllm; then
    stop_llama_dual_vllm
  fi
  if [[ $code -ne 0 ]]; then
    return "$code"
  fi
  echo "===== V4 ${lane} MATERIALIZATION DONE: ${output} ====="
}

main_v4_gpu0() {
  if [[ "$LANE_GPU_ID" != "${CMD_GPU0_ID:-0}" ]]; then
    echo "ERROR: v4_gpu0 lane was not bound to CMD_GPU0_ID" >&2
    return 1
  fi
  main_v4_materialize gpu0
}

main_v4_single_gpu() {
  if [[ "$LANE_GPU_ID" != "${CMD_GPU0_ID:-0}" ]]; then
    echo "ERROR: v4_single_gpu lane was not bound to CMD_GPU0_ID" >&2
    return 1
  fi
  main_v4_materialize single_gpu
}

main_v4_gpu1() {
  if [[ "$LANE_GPU_ID" != "${CMD_GPU1_ID:-1}" ]]; then
    echo "ERROR: v4_gpu1 lane was not bound to CMD_GPU1_ID" >&2
    return 1
  fi
  main_v4_materialize gpu1
}

main_v4_merge() {
  local materialized="${V4_RUN_ROOT}/materialized"
  local merged="${V4_RUN_ROOT}/cases.merged.jsonl"
  local replay="${V4_RUN_ROOT}/prequential"
  local bootstrap_samples="${CMD_V4_BOOTSTRAP_SAMPLES:-10000}"
  local expected_args=(--expected-source "$V4_SOURCE_CASES")
  $SMOKE && bootstrap_samples=100
  $SMOKE && expected_args=()
  local single_shard="${materialized}/single_gpu.jsonl"
  local gpu0_shard="${materialized}/gpu0.jsonl"
  local gpu1_shard="${materialized}/gpu1.jsonl"
  local shard_args=()
  if [[ -f "$single_shard" ]]; then
    if [[ -f "$gpu0_shard" || -f "$gpu1_shard" ]]; then
      echo "ERROR: refusing ambiguous single/dual materialization shards" >&2
      return 1
    fi
    shard_args=(--shard "$single_shard")
  else
    for shard in "$gpu0_shard" "$gpu1_shard"; do
      if [[ ! -f "$shard" ]]; then
        echo "ERROR: missing materialization shard: ${shard}" >&2
        echo "Run v4_single_gpu on one A100, or both v4_gpu0 and v4_gpu1." >&2
        return 1
      fi
    done
    shard_args=(--shard "$gpu0_shard" --shard "$gpu1_shard")
  fi
  for required in "$V4_GHOST_PROTOCOL" "$V4_GHOST_AUTHORIZATION" \
                  "$V4_GHOST_ACCESS_LEDGER" \
                  "$V4_MODEL_MANIFEST" "$V4_GHOST_EVALUATOR"; do
    if [[ ! -f "$required" ]]; then
      echo "ERROR: frozen GHOST live prerequisite not found: ${required}" >&2
      return 1
    fi
  done
  python -m experiments.ghost_live_protocol validate-run \
    --root "$CMD_ROOT" \
    --cases "$V4_SOURCE_CASES" \
    --protocol "$V4_GHOST_PROTOCOL" \
    --authorization "$V4_GHOST_AUTHORIZATION" \
    --access-ledger "$V4_GHOST_ACCESS_LEDGER" \
    --model-manifest "$V4_MODEL_MANIFEST" \
    --evaluator "$V4_GHOST_EVALUATOR" \
    --preparation-manifest "${CMD_V4_PREPARATION_MANIFEST:-$V4_ARTIFACTS/preparation/full/preparation_manifest.json}" \
    --candidate-budget "$V4_CANDIDATE_BUDGET" \
    --run-id "$RUN_ID" \
    --output "${CMD_RUN_DIR}/ghost_live_preflight.json"
  python -m experiments.v4_materialization merge \
    "${shard_args[@]}" \
    "${expected_args[@]}" \
    --output "$merged"
  python -m experiments.v4_prequential_runner \
    --cases "$merged" \
    --materialization-manifest "${merged}.manifest.json" \
    --output-dir "$replay" \
    --candidate-budget "$V4_CANDIDATE_BUDGET" \
    --ghost-evaluator "$V4_GHOST_EVALUATOR" \
    --ghost-protocol "$V4_GHOST_PROTOCOL" \
    --ghost-feedback-mode prospective_deployment \
    --bootstrap-samples "$bootstrap_samples" \
    --bootstrap-seed "${CMD_V4_BOOTSTRAP_SEED:-24}" \
    --primary-baseline "${CMD_V4_PRIMARY_BASELINE:-global_policy}"
  echo "===== V4 CANONICAL REPLAY DONE: ${replay}/report.json ====="
}

main_control_status() {
  if [[ -z "$TARGET_ROLE" ]]; then
    echo "ERROR: status/stop requires --target-role" >&2
    return 1
  fi
  python -m experiments.detached_run "$ROLE" \
    --run-dir "${RUNS_ROOT}/${RUN_ID}/${TARGET_ROLE}"
}

main_monitor() {
  local monitor_args=()
  local path
  for path in "${RUNS_ROOT}/${RUN_ID}"/*/status.jsonl \
              "${RUNS_ROOT}/${RUN_ID}"/*/progress.jsonl \
              "${V4_RUN_ROOT}"/prequential/progress.jsonl; do
    [[ -f "$path" ]] && monitor_args+=(--input "$path")
  done
  if [[ ${#monitor_args[@]} -eq 0 ]]; then
    echo "ERROR: no JSONL streams found for run-id ${RUN_ID}" >&2
    return 1
  fi
  if $MONITOR_FOLLOW; then
    python -m experiments.jsonl_monitor "${monitor_args[@]}" \
      --follow --exit-when-terminal
  else
    python -m experiments.jsonl_monitor "${monitor_args[@]}"
  fi
}

# ══════════════════════════════════════════════════════════════════════════════
# Dispatch
# ══════════════════════════════════════════════════════════════════════════════

case "$ROLE" in
  gpu0)           main_gpu0 ;;
  gpu1)           main_gpu1 ;;
  analyze)        main_analyze ;;
  phase1_gpu0)    main_phase1_gpu0 ;;
  phase1_gpu1)    main_phase1_gpu1 ;;
  phase1_analyze) main_phase1_analyze ;;
  sigil_gpu0)     main_sigil_gpu0 ;;
  sigil_gpu1)     main_sigil_gpu1 ;;
  sigil_analyze)  main_sigil_analyze ;;
  route_a)        main_route_a ;;
  v4_prepare)     main_v4_prepare ;;
  v4_prepare_inputs) main_v4_prepare_inputs ;;
  v4_single_gpu)    main_v4_single_gpu ;;
  v4_gpu0)        main_v4_gpu0 ;;
  v4_gpu1)        main_v4_gpu1 ;;
  v4_merge)       main_v4_merge ;;
  monitor)        main_monitor ;;
  status|stop)    main_control_status ;;
  *)
    echo "ERROR: invalid --role; see --help" >&2
    exit 1 ;;
esac
