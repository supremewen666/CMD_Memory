#!/usr/bin/env bash
# Safe entrypoint for the P4C experiment program.

set -euo pipefail
export PYTHONUNBUFFERED=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

stage="${1:-calibration}"
case "$stage" in
  calibration)
    if [[ "${1:-}" == "calibration" ]]; then shift; fi
    exec python -B -m experiments.run_remaining_live_experiment "$@"
    ;;
  p4c1)
    shift
    exec python -B -m experiments.run_p4c1_real_sources "$@"
    ;;
  p4c2)
    shift
    exec python -B -m experiments.run_p4c2_live_efficacy "$@"
    ;;
  p4c3)
    shift
    exec python -B -m experiments.run_p4c3_native_detection "$@"
    ;;
  p4c45)
    shift
    exec python -B -m experiments.run_p4c45_zero_call "$@"
    ;;
  p4c6)
    shift
    exec python -B -m experiments.run_p4c6_sealed_evaluation "$@"
    ;;
  --stages)
    printf '%s\n' calibration p4c1 p4c2 p4c3 p4c45 p4c6
    ;;
  *)
    # Backward compatible: historical mode flags still select calibration.
    exec python -B -m experiments.run_remaining_live_experiment "$@"
    ;;
esac
