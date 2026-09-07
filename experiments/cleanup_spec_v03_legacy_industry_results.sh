#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  cleanup_spec_v03_legacy_industry_results.sh --run-root PATH
  cleanup_spec_v03_legacy_industry_results.sh --run-root PATH \
    --execute --confirm DELETE_LEGACY_INDUSTRY_RESULTS

The default is a dry run. Only legacy LightMem/LycheeMem experiment outputs,
their versioned runtime directories, and matching logs are selected. Current
industry_real results and frozen MemSkill/ERSkill artifacts are never selected.
EOF
}

RUN_ROOT=""
EXECUTE=0
CONFIRM=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-root) RUN_ROOT="${2:-}"; shift 2 ;;
    --execute) EXECUTE=1; shift ;;
    --confirm) CONFIRM="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[ERROR] unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$RUN_ROOT" ]] || { echo "[ERROR] --run-root is required" >&2; exit 2; }
[[ -d "$RUN_ROOT" ]] || { echo "[ERROR] run root does not exist: $RUN_ROOT" >&2; exit 2; }
RUN_ROOT="$(cd "$RUN_ROOT" && pwd -P)"
[[ "$RUN_ROOT" != "/" ]] || { echo "[ERROR] refusing filesystem root" >&2; exit 2; }

declare -a candidates=()
while IFS= read -r -d '' path; do
  candidates+=("$path")
done < <(find "$RUN_ROOT" -mindepth 1 -maxdepth 1 -type d \
  \( -name 'industry_controlled_failed_*' \
     -o -name 'industry_smoke_v*' \
     -o -name 'industry_runtime_v*' \
     -o -name 'industry_lychee_v*' \) -print0)

legacy_controlled="$RUN_ROOT/industry_controlled"
if [[ -d "$legacy_controlled" ]] && grep -RqsE \
  '"system_id"[[:space:]]*:[[:space:]]*"(lightmem|lycheemem)"' \
  "$legacy_controlled"; then
  candidates+=("$legacy_controlled")
fi

while IFS= read -r -d '' path; do
  candidates+=("$path")
done < <(find "$RUN_ROOT/logs" -maxdepth 1 -type f \
  \( -iname '*lightmem*' -o -iname '*lychee*' \
     -o -iname 'industry-wrapper-errors-v*.jsonl' \
     -o -iname 'industry-controlled-v*.log' \) -print0 2>/dev/null || true)

if [[ ${#candidates[@]} -eq 0 ]]; then
  echo "[COMPLETE] no legacy LightMem/LycheeMem results found"
  exit 0
fi

printf '[CANDIDATE] %s\n' "${candidates[@]}"
du -sch "${candidates[@]}" 2>/dev/null | tail -n 1 || true

if [[ "$EXECUTE" -eq 0 ]]; then
  echo "[DRY RUN] nothing deleted"
  exit 0
fi
[[ "$CONFIRM" == "DELETE_LEGACY_INDUSTRY_RESULTS" ]] || {
  echo "[ERROR] execution requires --confirm DELETE_LEGACY_INDUSTRY_RESULTS" >&2
  exit 2
}

for path in "${candidates[@]}"; do
  case "$path" in
    "$RUN_ROOT"/*) rm -rf -- "$path" ;;
    *) echo "[ERROR] refusing path outside run root: $path" >&2; exit 2 ;;
  esac
done
echo "[COMPLETE] deleted ${#candidates[@]} legacy paths"
