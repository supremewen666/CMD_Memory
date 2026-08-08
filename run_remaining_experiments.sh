#!/usr/bin/env bash
# CMD 观测式实验 — 双卡并行（每卡双端点：Llama answer/selection + frozen Qwen evaluation）
#
# Usage:
#   SSH 1 (GPU 0):  ./run_remaining_experiments.sh --role gpu0
#   SSH 2 (GPU 1):  ./run_remaining_experiments.sh --role gpu1
#   后台运行:        ./run_remaining_experiments.sh --role gpu0 --detach
#   任一台:         ./run_remaining_experiments.sh --role analyze   （先 scp 汇聚 JSONL）
#   Phase 1 GPU 0:  ./run_remaining_experiments.sh --role phase1_gpu0 --detach
#   Phase 1 GPU 1:  ./run_remaining_experiments.sh --role phase1_gpu1 --detach
#   Phase 1 汇聚:   ./run_remaining_experiments.sh --role phase1_analyze
#   SIGIL Stage 1 GPU 0: ./run_remaining_experiments.sh --role sigil_gpu0 --detach
#   SIGIL Stage 1 GPU 1: ./run_remaining_experiments.sh --role sigil_gpu1 --detach
#   SIGIL Stage 1 审计:  ./run_remaining_experiments.sh --role sigil_analyze
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
EVOLUTION_ARTIFACTS="$CMD_ROOT/artifacts/evolution_governance"
SIGIL_ARTIFACTS="$CMD_ROOT/artifacts/sigil_qd"
QWVLLM_PORT=8000
LLAMA_JUDGE_PORT=8000
LLAMA_ANSWER_PORT=8001
# Cross-judge lane: a third model serving only the *evaluation* judge, so the
# answerer and selection judge stay byte-identical to the headline arm and the
# judge is the single thing that changes. Reusing either existing model would
# not work — selection is already Llama, so an evaluation judge on Llama trips
# the same-model guard in _assert_distinct_judge_identities, and Qwen is the
# headline evaluation judge, which is what we are trying to vary.
CROSSJUDGE_PORT="${CROSSJUDGE_PORT:-8002}"
CROSSJUDGE_MODEL="${CROSSJUDGE_MODEL:-mistral-7b-instruct-v0.3}"
CROSSJUDGE_MODEL_REPO="${CROSSJUDGE_MODEL_REPO:-mistralai/Mistral-7B-Instruct-v0.3}"
CROSSJUDGE_MODEL_DIRNAME="${CROSSJUDGE_MODEL_DIRNAME:-Mistral-7B-Instruct-v0.3}"
PID_FILE="/tmp/vllm_shared.pid"
LOG_FILE="/tmp/vllm_shared.log"
JUDGE_BASE_URL="${JUDGE_BASE_URL:-http://localhost:${LLAMA_JUDGE_PORT}/v1}"
JUDGE_MODEL="${JUDGE_MODEL:-qwen2.5-7b-instruct}"
JUDGE_API_KEY="${JUDGE_API_KEY:-dummy}"
CMD_CASE_WORKERS="${CMD_CASE_WORKERS:-32}"
VLLM_READY_TIMEOUT_SECONDS="${VLLM_READY_TIMEOUT_SECONDS:-300}"
CMD_PRETRAINED_LMS_ROOT="${CMD_PRETRAINED_LMS_ROOT:-$HOME/pretrained_lms}"
VLLM_QWEN_GPU_MEMORY_UTILIZATION="${VLLM_QWEN_GPU_MEMORY_UTILIZATION:-0.25}"
VLLM_CROSSJUDGE_GPU_MEMORY_UTILIZATION="${VLLM_CROSSJUDGE_GPU_MEMORY_UTILIZATION:-0.22}"
VLLM_LLAMA_GPU_MEMORY_UTILIZATION="${VLLM_LLAMA_GPU_MEMORY_UTILIZATION:-0.50}"

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
      echo "Usage: $0 --role gpu0|gpu1|analyze|phase1_gpu0|phase1_gpu1|phase1_analyze|sigil_gpu0|sigil_gpu1|sigil_analyze|route_a [--smoke] [--only NAME] [--detach]"
      echo ""
      echo "  GPU 0 (~3.5h): MemTrace-B seeds 24+124 → MemFail"
      echo "  GPU 1 (~3.5h): MemTrace-B seed 224 → STALE → replicate seed 24"
      echo "  analyze:        unified analysis (run after scp-ing results to one machine)"
      echo "  phase1_gpu0:    evolution-on vs frozen on MemTrace (same GPU/endpoints)"
      echo "  phase1_gpu1:    evolution-on vs frozen on STALE (same GPU/endpoints)"
      echo "  phase1_analyze: merge the two per-GPU Phase 1 summaries (zero calls)"
      echo "  sigil_gpu0:     Stage 1 live item-gate shadow on MemTrace + MemFail"
      echo "  sigil_gpu1:     Stage 1 live item-gate shadow on STALE"
      echo "  sigil_analyze:  repair-validity audit + scope ledger (zero calls)"
      echo "  route_a:        Route A §15 chain; every stage gated, E0 STOP refuses downstream"
      echo "  --detach:       run in a new session; logs to the role-specific artifacts directory"
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
  echo "ERROR: --role is required (gpu0 | gpu1 | analyze | phase1_gpu0 | phase1_gpu1 | phase1_analyze | sigil_gpu0 | sigil_gpu1 | sigil_analyze | route_a)" >&2
  exit 1
fi

if $DETACH && [[ -z "${CMD_EXPERIMENTS_DETACHED:-}" ]]; then
  detach_log_root="$ARTIFACTS"
  if [[ "$ROLE" == phase1_* ]]; then
    detach_log_root="${EVOLUTION_ARTIFACTS}/phase1/logs"
  elif [[ "$ROLE" == sigil_* ]]; then
    detach_log_root="${SIGIL_ARTIFACTS}/logs"
  elif [[ "$ROLE" == route_a ]]; then
    detach_log_root="${CMD_ROOT}/artifacts/route_a/logs"
  fi
  mkdir -p "$detach_log_root"
  detach_log="${detach_log_root}/run_${ROLE}_$(date +%Y%m%d_%H%M%S).log"
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
      CUDA_VISIBLE_DEVICES=0 \
      vllm serve "$CMD_QWEN_MODEL_DIR" \
      --host 127.0.0.1 \
      --served-model-name qwen2.5-7b-instruct \
      --port "$LLAMA_JUDGE_PORT" \
      --gpu-memory-utilization "$VLLM_QWEN_GPU_MEMORY_UTILIZATION" \
      --max-model-len 8192 \
      --max-num-seqs 32 \
      --enable-prefix-caching \
      > /tmp/vllm_qwen_judge.log 2>&1 &
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
      CUDA_VISIBLE_DEVICES=0 \
      vllm serve "$CMD_LLAMA_MODEL_DIR" \
      --host 127.0.0.1 \
      --served-model-name llama-3.1-8b-instruct \
      --port "$LLAMA_ANSWER_PORT" \
      --gpu-memory-utilization "$VLLM_LLAMA_GPU_MEMORY_UTILIZATION" \
      --max-model-len 8192 \
      --max-num-seqs 32 \
      --enable-prefix-caching \
      > /tmp/vllm_llama_answer.log 2>&1 &
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
    CUDA_VISIBLE_DEVICES=0 \
    vllm serve "$model_dir" \
    --host 127.0.0.1 \
    --served-model-name "$CROSSJUDGE_MODEL" \
    --port "$CROSSJUDGE_PORT" \
    --gpu-memory-utilization "$VLLM_CROSSJUDGE_GPU_MEMORY_UTILIZATION" \
    --max-model-len 8192 \
    --max-num-seqs 32 \
    --enable-prefix-caching \
    > /tmp/vllm_crossjudge.log 2>&1 &
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
  route_a_step e3 "E3 open operator synthesis" \
    python -m experiments.run_open_operator_synthesis
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
  *)
    echo "ERROR: invalid --role; see --help" >&2
    exit 1 ;;
esac
