#!/usr/bin/env bash
# run_remaining_experiments.sh — CMD 未完成实验的分布式后台跑批(2026-07-19)
#
# 覆盖(与 IMPROVEMENT_SPEC_A/B、plan_res.md 对齐):
#   lane 0(主端点,本地 qwen):
#     Exp21 ×3 (churn 复测) -> Exp22 ×3 (bank=同 run 的 Exp21 输出)
#     Exp23a single-param 补充臂 -> Exp23b ×3
#     Exp18 ×3 (B-full: fingerprint 键下的轨迹曲线; 固定输出路径, 加全局锁)
#     Exp24 (总闸门, runner 未建 -> 存在性门控, 见 SPEC_A §2)
#     Exp25 durability (runner 未建 -> 门控, 见 SPEC_A §5)
#   lane 1..N(每 GPU 一个 vLLM 端点, 换 answering 模型):
#     Exp14 ×3 (C4 headline) -> Exp21 ×3 -> Exp22 ×3
#     [PROVISIONAL] judge/answerer 拆分未落地(SPEC_A §3), 目前为整栈换端点;
#     judge 冻结前这些数字仅作趋势参考, 不进正文。
#
# 用法:
#   export CMD_ENDPOINTS="http://localhost:8000/v1|http://localhost:8001/v1"
#   export CMD_MODELS="qwen2.5-7b-instruct|llama-3.1-8b-instruct"
#   nohup ./run_remaining_experiments.sh > /dev/null 2>&1 &     # 全后台
#   ./run_remaining_experiments.sh --wait                        # 前台等完
#   ./run_remaining_experiments.sh --dry-run                     # 只打印命令
#   CMD_ONLY="exp21,exp22" ./run_remaining_experiments.sh        # 只跑部分
#
# 约束(勿删):
#   - LLM_TIMEOUT=120 必须; endpoint 必须支持 top_logprobs(G0 门先验)。
#   - rollout.py 超时静默当 0 的 bug 未修前(SPEC_A §1), 远端/慢端点数字有假阴性风险。
#   - Exp14/18 输出路径硬编码 artifacts/sandbox/(无 --out), 跨 lane 用全局锁串行。

set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

TS="$(date +%Y%m%d_%H%M%S)"
RUN_ROOT="$ROOT/artifacts/exp_runs/$TS"
mkdir -p "$RUN_ROOT"

DRY_RUN=0
WAIT=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --wait) WAIT=1 ;;
  esac
done

IFS='|' read -r -a ENDPOINTS <<< "${CMD_ENDPOINTS:-http://localhost:8000/v1}"
IFS='|' read -r -a MODELS <<< "${CMD_MODELS:-qwen2.5-7b-instruct}"
RUNS="${CMD_RUNS:-3}"
ONLY="${CMD_ONLY:-}"

log() { echo "[$(date +%H:%M:%S)] $*"; }

should_run() {
  [ -z "$ONLY" ] && return 0
  case ",$ONLY," in *",$1,"*) return 0 ;; *) return 1 ;; esac
}

# 单个实验 job:失败不终止 lane, 记 failures.log
run_job() {
  local name="$1"; shift
  should_run "$name" || { log "SKIP  $name (CMD_ONLY)"; return 0; }
  log "START $name"
  if [ "$DRY_RUN" = "1" ]; then
    printf '  [dry-run]'; printf ' %q' "$@"; echo
    return 0
  fi
  if "$@"; then
    log "DONE  $name"
  else
    local rc=$?
    log "FAIL  $name (exit $rc)"
    echo "$name" >> "$RUN_ROOT/failures.log"
  fi
}

# 固定输出路径实验(Exp14/18)的全局互斥锁: 锁内跑 + 立刻把产物拷进 run 目录
FIXED_OUT_LOCK="$RUN_ROOT/.fixed_out.lock"
run_fixed_out_job() {
  local name="$1" dest="$2" glob="$3"; shift 3
  should_run "$name" || { log "SKIP  $name (CMD_ONLY)"; return 0; }
  if [ "$DRY_RUN" = "1" ]; then run_job "$name" "$@"; return 0; fi
  while ! mkdir "$FIXED_OUT_LOCK" 2>/dev/null; do sleep 30; done
  run_job "$name" "$@"
  mkdir -p "$dest"
  # shellcheck disable=SC2086
  cp $glob "$dest"/ 2>/dev/null || true
  rmdir "$FIXED_OUT_LOCK"
}

# G0: judge 端点必须能返回可解析的 G-Eval logprobs
g0_gate() {
  [ "$DRY_RUN" = "1" ] && { log "G0 gate skipped (dry-run)"; return 0; }
  python - <<'PY'
import sys
sys.path.insert(0, ".")
from experiments.experiment_runner_common import assert_g_eval_available
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
assert_g_eval_available(LLMClient(LLMClientConfig()), role="preflight")
print("G0 logprob gate: OK")
PY
}

lane_env() {  # $1=endpoint $2=model
  export LLM_BASE_URL="$1" LLM_MODEL="$2" LLM_API_KEY="dummy" LLM_TIMEOUT=120
  export NO_PROXY="localhost,127.0.0.1" no_proxy="localhost,127.0.0.1"
}

# ---------- lane 0: 主端点 ----------
lane_local() {
  lane_env "${ENDPOINTS[0]}" "${MODELS[0]}"
  local D="$RUN_ROOT/lane0_${MODELS[0]}"
  mkdir -p "$D"
  g0_gate || { log "lane0: G0 gate FAILED — 端点无 top_logprobs, lane 终止"; return 1; }

  for r in $(seq 1 "$RUNS"); do
    run_job "exp21" python experiments/run_experiment_21_operator_headroom.py \
      --no-ec-test --out "$D/operator_headroom_detail_run${r}.csv"
    run_job "exp22" python experiments/run_experiment_22_operator_transfer.py \
      --operator-bank "$D/operator_headroom_detail_run${r}.csv" \
      --neighbors 10 --topn 5 --random-seed $((22 + (r - 1) * 100)) \
      --out "$D/operator_transfer_detail_run${r}.csv"
  done

  # Exp23a 补充臂: single-param(STALE 主协议之外的 item_signal_hints 家族)
  run_job "exp23a" python experiments/run_experiment_23_item_headroom.py \
    --operator-classes single-param \
    --out "$D/item_operator_headroom_singleparam.csv"

  # Exp23b ×3: bank 用 canonical(不存在则先补一发 single 臂生成)
  local BANK="$ROOT/artifacts/sandbox/item_operator_headroom_detail.csv"
  if [ ! -f "$BANK" ] && [ "$DRY_RUN" != "1" ]; then
    run_job "exp23a" python experiments/run_experiment_23_item_headroom.py \
      --operator-classes single --out "$BANK"
  fi
  for r in $(seq 1 "$RUNS"); do
    run_job "exp23b" python experiments/run_experiment_23_item_transfer.py \
      --operator-bank "$BANK" --neighbors 10 --topn 5 \
      --random-seed $((23 + (r - 1) * 100)) \
      --out "$D/item_operator_transfer_run${r}.csv"
  done

  # Exp18 B-full: fingerprint 键已在代码, 重跑出轨迹曲线; 输出路径固定 -> 锁
  for r in $(seq 1 "$RUNS"); do
    run_fixed_out_job "exp18" "$D/exp18_run${r}" \
      "$ROOT/artifacts/sandbox/failure_memory_trajectory_*.csv" \
      python experiments/run_experiment_18_failure_memory_trajectory.py \
      --seed $((42 + (r - 1) * 100))
  done

  # Exp24 总闸门 / Exp25 durability: runner 未建, 存在性门控(不静默造数)
  if [ -f experiments/run_experiment_24_operator_trajectory.py ]; then
    for r in $(seq 1 "$RUNS"); do
      run_job "exp24" python experiments/run_experiment_24_operator_trajectory.py \
        --seed $((24 + (r - 1) * 100)) --out "$D/operator_trajectory_run${r}.csv"
    done
  else
    log "GATED exp24: runner 不存在 — 先按 IMPROVEMENT_SPEC_A §2 建 runner, 再 CMD_ONLY=exp24 重跑"
  fi
  if [ -f experiments/run_experiment_25_repair_durability.py ]; then
    run_job "exp25" python experiments/run_experiment_25_repair_durability.py \
      --out "$D/repair_durability_detail.csv"
  else
    log "GATED exp25: runner 不存在 — 见 IMPROVEMENT_SPEC_A §5"
  fi

  run_job "significance" python experiments/analyze_significance.py
}

# ---------- lane i>=1: 多模型(PROVISIONAL) ----------
lane_model() {  # $1=endpoint $2=model $3=lane_idx
  lane_env "$1" "$2"
  local D="$RUN_ROOT/lane$3_$2"
  mkdir -p "$D"
  log "lane$3 [$2] PROVISIONAL: judge/answerer 拆分未落地(SPEC_A §3), 整栈换端点, 数字不进正文"
  g0_gate || { log "lane$3: G0 gate FAILED — $2 端点无 top_logprobs, 只能当 answerer 不能当 judge; lane 终止"; return 1; }

  for r in $(seq 1 "$RUNS"); do
    run_fixed_out_job "exp14" "$D/exp14_run${r}" \
      "$ROOT/artifacts/sandbox/repair_efficacy_*.csv" \
      python experiments/run_experiment_14_repair_efficacy.py \
      --cmd-attribution exhaustive --limit 0
    run_job "exp21" python experiments/run_experiment_21_operator_headroom.py \
      --no-ec-test --out "$D/operator_headroom_detail_run${r}.csv"
    run_job "exp22" python experiments/run_experiment_22_operator_transfer.py \
      --operator-bank "$D/operator_headroom_detail_run${r}.csv" \
      --neighbors 10 --topn 5 --random-seed $((22 + (r - 1) * 100)) \
      --out "$D/operator_transfer_detail_run${r}.csv"
  done
}

# ---------- 启动 ----------
{
  echo "commit: $(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "endpoints: ${ENDPOINTS[*]}"
  echo "models: ${MODELS[*]}"
  echo "runs_per_exp: $RUNS  only: ${ONLY:-<all>}"
} > "$RUN_ROOT/MANIFEST.txt"

if ! grep -q "LLMTimeoutError" cmd_audit/counterfactual/rollout.py 2>/dev/null; then
  log "WARN: rollout.py 仍将超时静默计为 recovery_gain=0.0(SPEC_A §1 未修)— 慢端点 lane 有假阴性风险"
fi

if [ "$DRY_RUN" = "1" ]; then
  lane_local
  for ((i = 1; i < ${#ENDPOINTS[@]}; i++)); do
    lane_model "${ENDPOINTS[$i]}" "${MODELS[$i]:-${MODELS[0]}}" "$i"
  done
  log "dry-run complete (no jobs launched)"
  exit 0
fi

PIDS=()
lane_local > "$RUN_ROOT/lane0.log" 2>&1 &
PIDS+=($!)
for ((i = 1; i < ${#ENDPOINTS[@]}; i++)); do
  lane_model "${ENDPOINTS[$i]}" "${MODELS[$i]:-${MODELS[0]}}" "$i" > "$RUN_ROOT/lane$i.log" 2>&1 &
  PIDS+=($!)
done
printf '%s\n' "${PIDS[@]}" > "$RUN_ROOT/pids.txt"
log "launched ${#PIDS[@]} lane(s); logs: $RUN_ROOT/lane*.log; pids: $RUN_ROOT/pids.txt"

if [ "$WAIT" = "1" ]; then
  wait
  log "all lanes finished; failures: $( [ -f "$RUN_ROOT/failures.log" ] && wc -l < "$RUN_ROOT/failures.log" || echo 0 )"
fi
