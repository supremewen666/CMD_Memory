#!/usr/bin/env bash
# Shell-level contract test: help/discovery only; no formal experiment stage.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT/run_remaining_experiments.sh"
bash -n "$SCRIPT"
HELP="$("$SCRIPT" --help)"
for role in b_materialization_merge b_preflight b_e1_seal b_e1_verify b_e1_audit \
  v4_lineage_plan v4_followup_capture v4_lineage_project v4_lineage_merge \
  b_e2 b_e3 b_e4 b_e4b b_e5; do
  grep -Fq "$role" <<<"$HELP"
done
grep -Fq 'B stage order:' <<<"$HELP"
grep -Fq 'CMD_B_SOURCE_EXPORT_SCHEMA' <<<"$HELP"
grep -Fq 'CMD_B_SOURCE_EXPORT_SHA256' <<<"$HELP"
grep -Fq -- '--selections "$B_SELECTIONS"' "$SCRIPT"
grep -Fq -- '--cases "$B_TYPED_CASES" --output-dir "$B_ROOT/E2"' "$SCRIPT"
grep -Fq -- 'experiments.v4_prequential_runner' "$SCRIPT"
grep -Fq -- '--materialization-manifest "$B_TYPED_MANIFEST"' "$SCRIPT"
grep -Fq -- '--ecology-window-size "${CMD_B_E4B_ECOLOGY_WINDOW_SIZE:-50}"' "$SCRIPT"
grep -Fq 'B LIVE MATERIALIZATION MERGED (no replay)' "$SCRIPT"
if grep -Fq 'experiments.v4_followup_capture:main' "$SCRIPT"; then
  echo 'capture runner must not be used as the capture backend' >&2
  exit 1
fi
printf '%s\n' 'B role shell contract: PASS (help-only; no experiment executed)'
