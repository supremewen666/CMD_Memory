#!/usr/bin/env bash
# Full CMD-Audit experiment suite. Requires an OpenAI-compatible endpoint that
# returns logprobs (G-Eval). Set LLM_BASE_URL / LLM_MODEL / LLM_API_KEY first:
#
#   export LLM_BASE_URL=http://localhost:8000/v1
#   export LLM_MODEL=qwen2.5-7b
#   export LLM_API_KEY=dummy
#   bash run_all.sh
#
# Dependency order matters: prior banks (#1, #7a) must precede the transfer /
# cross-source runs that consume them. Fails fast on any error.
set -euo pipefail

cd "$(dirname "$0")"
OUT=artifacts/sandbox
mkdir -p "$OUT"

# ---------------------------------------------------------------- preflight
echo "=== preflight: endpoint G-Eval (logprobs) check ==="
: "${LLM_BASE_URL:?set LLM_BASE_URL (e.g. http://localhost:8000/v1)}"
: "${LLM_MODEL:?set LLM_MODEL (must match the endpoint model id)}"
echo "  LLM_BASE_URL=$LLM_BASE_URL"
echo "  LLM_MODEL=$LLM_MODEL"
python3 - <<'PY'
# Mirror the assert_g_eval_available check: a usable endpoint must return
# parseable logprobs, not just any 200. Refuse early otherwise.
import sys
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.scoring.llm import _continuous_verify
client = LLMClient(LLMClientConfig())
if _continuous_verify(client, "Paris is in France.", "Paris is in France.") is None:
    sys.exit("ENDPOINT REJECTED: no parseable G-Eval logprobs. "
             "Use a logprob-capable server (e.g. vLLM); Ollama may not qualify.")
print("  endpoint OK: G-Eval logprobs parseable")
PY

run() { echo; echo "######## $* ########"; time "$@"; }

# ---------------------------------------------------------------- 1. prior bank (multihop)
run python -m experiments.probe_exhaustive \
  --limit 0 --aggregate --min-credit 0.05 \
  --out "$OUT/exhaustive_detail_mincredit05.csv"

# ---------------------------------------------------------------- 2. headline repair efficacy (C3/C4)
run python -m experiments.run_experiment_14_repair_efficacy \
  --cmd-attribution exhaustive --limit 0

# ---------------------------------------------------------------- 3. LOO prior transfer (C5) -- needs #1
run python -m experiments.run_experiment_15_prior_transfer \
  --prior-bank "$OUT/exhaustive_detail_mincredit05.csv" \
  --mode both

# ---------------------------------------------------------------- 4. ECS structure ablation (C8)
run python -m experiments.run_experiment_17_ecs_structure_ablation --limit 0

# ---------------------------------------------------------------- 5. FailureMemory trajectory (C7, recurrent) -- tier-1 fingerprint key
run python -m experiments.run_experiment_18_failure_memory_trajectory \
  --cases data/probe_cases/real_recurrent_cases.json

# ---------------------------------------------------------------- 6. skill abstraction two-tier (C7, recurrent)
run python -m experiments.run_experiment_19_skill_abstraction \
  --cases data/probe_cases/real_recurrent_cases.json

# ---------------------------------------------------------------- 7. cross-source transfer (C5) -- own prior bank first, then #13
run python -m experiments.probe_exhaustive \
  --cases data/probe_cases/real_three_source_cases.json \
  --limit 0 --aggregate --min-credit 0.05 \
  --out "$OUT/exhaustive_three_source_detail_mincredit05.csv"
run python -m experiments.run_experiment_13_cross_dataset \
  --cases data/probe_cases/real_three_source_cases.json \
  --prior-bank "$OUT/exhaustive_three_source_detail_mincredit05.csv" \
  --source-mode both

# ---------------------------------------------------------------- 8. supporting (no extra data deps)
run python -m experiments.run_experiment_09_geval_variance
run python -m experiments.run_experiment_10_surrogate_gap

# ---------------------------------------------------------------- 9. significance post-processing (reads detail CSVs)
run python -m experiments.analyze_significance

echo; echo "=== ALL EXPERIMENTS DONE -> $OUT ==="
