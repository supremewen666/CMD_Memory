#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CMD_PYTHON="${CMD_PYTHON:-python}"
STREAMS=(
  halumem_stationary
  halumem_abrupt_process_state_poison
  memfail_stationary
  memfail_abrupt_process_state_poison
  memtracebench_stationary
  memtracebench_abrupt_process_state_poison
)
SPLITS=(D_skill D_router D_cal D_lifecycle T_final)

usage() {
  cat <<'EOF'
Usage: run_spec_v03_real_systems.sh PHASE

Phases:
  prepare    Export serving-visible development and T_final producer inputs.
  freeze     Freeze producer JSONL records into closed evidence artifacts.
  configure  Bind artifacts, Mem0, the shared head, and adapter commits.
  services   Run the embedding service and enforcing model-usage proxy.
  run        Evaluate MemSkill + head, ERSkill + head, and Mem0 on six streams.
  status     Print report completion and per-system adapter status.

Common required environment:
  RUN_ROOT             New output root for this experiment.
  DATA_ROOT            Root containing the six compiled stream directories.

freeze additionally requires:
  MEMSKILL_RECORDS, MEMSKILL_REVISION, MEMSKILL_REPOSITORY, MEMSKILL_COMMIT
  ERSKILL_RECORDS, ERSKILL_REVISION, ERSKILL_REPOSITORY, ERSKILL_COMMIT
Optional: ERSKILL_IMPLEMENTATION (default: paper_faithful_erskill_reimplementation)

configure additionally requires:
  MEM0_REPOSITORY, MEM0_PYTHON, MEM0_CONFIG_TEMPLATE
  HEAD_ENDPOINT, HEAD_MODEL_ID, HEAD_MODEL_SNAPSHOT
Optional: HEAD_API_KEY_ENV, METERING_URL, ADAPTER_TIMEOUT_SECONDS

services additionally requires:
  MODERN_PY, EMBED_MODEL, MODEL_UPSTREAM
Optional: SERVICE_HOST, EMBED_PORT, METERING_PORT and SYSTEM_MAX_* budgets.
EOF
}

require() {
  local name
  for name in "$@"; do
    if [[ -z "${!name:-}" ]]; then
      echo "[ERROR] required environment variable is unset: $name" >&2
      exit 2
    fi
  done
}

require_file() {
  [[ -f "$1" ]] || { echo "[ERROR] file not found: $1" >&2; exit 2; }
}

prepare() {
  require RUN_ROOT DATA_ROOT
  mkdir -p "$RUN_ROOT/industry/inputs"
  local stream split data
  for stream in "${STREAMS[@]}"; do
    data="$DATA_ROOT/$stream"
    require_file "$data/runtime_cases.json"
    require_file "$data/event_order_manifest.json"
    require_file "$data/split_manifest.json"
    for split in "${SPLITS[@]}"; do
      mkdir -p "$RUN_ROOT/industry/inputs/$split"
      echo "[EXPORT] $stream split=$split"
      "$CMD_PYTHON" "$REPO_ROOT/experiments/spec_v03_export_skill_competitor_inputs.py" \
        --runtime-cases "$data/runtime_cases.json" \
        --event-order "$data/event_order_manifest.json" \
        --split-manifest "$data/split_manifest.json" \
        --include-split "$split" \
        --output "$RUN_ROOT/industry/inputs/$split/$stream.json"
    done
  done
  echo "[COMPLETE] producer inputs: $RUN_ROOT/industry/inputs"
}

freeze() {
  require RUN_ROOT \
    MEMSKILL_RECORDS MEMSKILL_REVISION MEMSKILL_REPOSITORY MEMSKILL_COMMIT \
    ERSKILL_RECORDS ERSKILL_REVISION ERSKILL_REPOSITORY ERSKILL_COMMIT
  require_file "$MEMSKILL_RECORDS"
  require_file "$ERSKILL_RECORDS"
  local implementation="${ERSKILL_IMPLEMENTATION:-paper_faithful_erskill_reimplementation}"
  mkdir -p "$RUN_ROOT/industry/frozen"
  "$CMD_PYTHON" "$REPO_ROOT/experiments/spec_v03_freeze_skill_evidence.py" \
    --system-id memskill \
    --implementation official_memskill_checkpoint_export \
    --artifact-revision "$MEMSKILL_REVISION" \
    --producer-repository "$MEMSKILL_REPOSITORY" \
    --producer-commit "$MEMSKILL_COMMIT" \
    --training-split D_skill --training-split D_router \
    --records "$MEMSKILL_RECORDS" \
    --output "$RUN_ROOT/industry/frozen/memskill.json"
  "$CMD_PYTHON" "$REPO_ROOT/experiments/spec_v03_freeze_skill_evidence.py" \
    --system-id erskill \
    --implementation "$implementation" \
    --artifact-revision "$ERSKILL_REVISION" \
    --producer-repository "$ERSKILL_REPOSITORY" \
    --producer-commit "$ERSKILL_COMMIT" \
    --training-split D_skill --training-split D_router \
    --records "$ERSKILL_RECORDS" \
    --output "$RUN_ROOT/industry/frozen/erskill.json"
  echo "[COMPLETE] frozen evidence: $RUN_ROOT/industry/frozen"
}

configure() {
  require RUN_ROOT MEM0_REPOSITORY MEM0_PYTHON MEM0_CONFIG_TEMPLATE \
    HEAD_ENDPOINT HEAD_MODEL_ID HEAD_MODEL_SNAPSHOT
  require_file "$MEM0_CONFIG_TEMPLATE"
  require_file "$RUN_ROOT/industry/frozen/memskill.json"
  require_file "$RUN_ROOT/industry/frozen/erskill.json"
  local protocol="$RUN_ROOT/industry/controlled_memory_protocol.json"
  local mem0_config="$RUN_ROOT/industry/configs/mem0-controlled.json"
  local adapters="$RUN_ROOT/industry/industry_adapters.json"
  local metering_url="${METERING_URL:-http://127.0.0.1:9100}"
  mkdir -p "$RUN_ROOT/industry/configs" "$RUN_ROOT/industry_runtime/usage"
  cp "$REPO_ROOT/protocol/controlled_memory_protocol.example.json" "$protocol"
  cp "$MEM0_CONFIG_TEMPLATE" "$mem0_config"
  "$CMD_PYTHON" "$REPO_ROOT/experiments/spec_v03_configure_industry_runtime.py" \
    --protocol "$protocol" \
    --mem0-config "$mem0_config" \
    --memskill-artifact "$RUN_ROOT/industry/frozen/memskill.json" \
    --erskill-artifact "$RUN_ROOT/industry/frozen/erskill.json" \
    --erskill-implementation "${ERSKILL_IMPLEMENTATION:-paper_faithful_erskill_reimplementation}" \
    --usage-root "$RUN_ROOT/industry_runtime/usage" \
    --metering-url "$metering_url" \
    --head-endpoint "$HEAD_ENDPOINT" \
    --head-model-id "$HEAD_MODEL_ID" \
    --head-model-snapshot "$HEAD_MODEL_SNAPSHOT" \
    --head-api-key-env "${HEAD_API_KEY_ENV:-MODEL_API_KEY}"
  "$CMD_PYTHON" "$REPO_ROOT/experiments/spec_v03_configure_industry_adapters.py" \
    --output "$adapters" \
    --protocol "$protocol" \
    --cmd-repository "$REPO_ROOT" \
    --cmd-python "$(command -v "$CMD_PYTHON")" \
    --mem0-repository "$MEM0_REPOSITORY" \
    --mem0-python "$MEM0_PYTHON" \
    --timeout-seconds "${ADAPTER_TIMEOUT_SECONDS:-1200}"
  if grep -RqsE '/ABS/PATH|REPLACE_WITH|0000000000000000000000000000000000000000' \
    "$protocol" "$adapters"; then
    echo "[ERROR] generated runtime configuration still contains placeholders" >&2
    exit 2
  fi
  echo "[COMPLETE] runtime config: $RUN_ROOT/industry"
}

services() {
  require RUN_ROOT MODERN_PY EMBED_MODEL MODEL_UPSTREAM
  curl -sf "${MODEL_UPSTREAM%/}/v1/models" >/dev/null || {
    echo "[ERROR] model upstream is not OpenAI-compatible or is unavailable: $MODEL_UPSTREAM" >&2
    exit 2
  }
  export INDUSTRY_RUNTIME_ROOT="${INDUSTRY_RUNTIME_ROOT:-$RUN_ROOT/industry_runtime}"
  exec "$REPO_ROOT/experiments/run_spec_v03_industry_services.sh"
}

run() {
  require RUN_ROOT DATA_ROOT
  local adapters="$RUN_ROOT/industry/industry_adapters.json"
  require_file "$adapters"
  export INDUSTRY_ADAPTERS_CONFIG="$adapters"
  export OUTPUT_ROOT="${OUTPUT_ROOT:-$RUN_ROOT/industry_real}"
  export DATA_ROOT CMD_PYTHON
  "$REPO_ROOT/experiments/run_spec_v03_industry_controlled.sh"
}

status() {
  require RUN_ROOT
  local root="${OUTPUT_ROOT:-$RUN_ROOT/industry_real}"
  local count
  count="$(find "$root" -name report.json -type f 2>/dev/null | wc -l | tr -d ' ')"
  echo "reports=$count/6"
  if [[ "$count" -gt 0 ]]; then
    find "$root" -name report.json -type f -print0 | xargs -0 jq -s '
      [.[] | .results.stage9[] | select(.system_id | IN("memskill","erskill","mem0"))]
      | group_by(.system_id)
      | map({system:.[0].system_id,total:length,
          ok:(map(select(.adapter_status=="OK"))|length),
          failed:(map(select(.adapter_status=="FAILED"))|length),
          unsupported:(map(select(.adapter_status=="UNSUPPORTED"))|length)})'
  fi
}

phase="${1:-}"
case "$phase" in
  prepare) prepare ;;
  freeze) freeze ;;
  configure) configure ;;
  services) services ;;
  run) run ;;
  status) status ;;
  -h|--help|help|"") usage ;;
  *) echo "[ERROR] unknown phase: $phase" >&2; usage >&2; exit 2 ;;
esac
