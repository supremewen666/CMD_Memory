#!/usr/bin/env bash
# Safe entrypoint for the remaining call-required confirmation experiment.

set -euo pipefail
export PYTHONUNBUFFERED=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
exec python -B -m experiments.run_remaining_live_experiment "$@"
