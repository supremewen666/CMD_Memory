# P4C experiment program

The paper's primary claim is **gold-free memory fault correction and
evolution**. `run_remaining_experiment.sh` now defaults to a zero-call mainline
plan containing P4C-1, P4C-3, and P4C-4/5. P4C-2/P4C-6 are supplementary
answer confirmation. The old four-arm LongMemEval answer pipeline is legacy and
can only be selected explicitly.

```bash
./run_remaining_experiment.sh
./run_remaining_experiment.sh --plan
```

List the available stages:

```bash
./run_remaining_experiment.sh --stages
```

| Stage | Paper role | Purpose | Calls by default |
|---|---|---:|---:|
| `mainline` | mainline | Plan/verify primary evidence | no |
| `p4c1` | mainline | Gold-free real-source correction receipts | no |
| `p4c3` | mainline | Native detection, abstention, false-repair audit | no |
| `p4c45` | mainline | GHOST/ECC evolution, ablation, robustness | no |
| `p4c45-v1` | superseded | Original 30-scenario metric protocol | no |
| `p4c2` | supplementary | Paired repaired-state answer confirmation | no |
| `p4c6` | supplementary | Independent paired answer evaluation | exact: no |
| `legacy-answer` | legacy | Degraded four-arm answer wiring pipeline | no |

The plural `run_remaining_experiments.sh` remains the legacy Route A/V4/GPU
launcher and is not part of this P4C program.

## Mainline sequence

Use a new P4C-1 output directory because a fresh run refuses to overwrite old
evidence. This run also writes `visible_telemetry.jsonl` directly from live
state; the detector never reconstructs telemetry from overlay labels. The v2
source projection admits only nested `role/content`; fields such as
`has_answer` never enter memory content hashes or state decisions.

The earlier `--limit-per-source 5` command was a smoke protocol: it produced
15 repair cases (5 per source), then P4C-3 paired them with 15 clean controls.
It was never a 500-case LongMemEval run. The formal source counts are now
independent because LongMemEval has 500 eligible rows while the available
MemFail long-hop file has 92 rows. Poison rows are explicitly reported as
parameterized structural variants, not independent real-source examples.

```bash
./run_remaining_experiment.sh p4c1 \
  --longmemeval data/external/longmemeval/input/longmemeval_s_cleaned.json \
  --memfail-root data/external/memfail/datasets \
  --longmemeval-limit 500 \
  --memfail-limit 92 \
  --poison-case-count 92 \
  --poison-recall-size 10 \
  --poison-counts 1,3,5,8 \
  --output-dir artifacts/experiments/p4c1_real_sources_full_v2
```

This produces 684 P4C-1 repair cases: 500 LongMemEval projections, 92 MemFail
cases, and 92 poison variants. P4C-3 consumes the resulting 684 fault rows plus
684 post-repair clean controls, for 1,368 detection decisions.

Run native detection, then create and open its sidecar only after the runtime
manifest says `prediction_sealed`:

```bash
./run_remaining_experiment.sh p4c3 \
  --mode runtime \
  --visible-telemetry artifacts/experiments/p4c1_real_sources_full_v2/visible_telemetry.jsonl \
  --output-dir artifacts/experiments/p4c3_native_detection_full_v2

./run_remaining_experiment.sh p4c3 \
  --mode prepare-sidecar \
  --incident-overlay artifacts/experiments/p4c1_real_sources_full_v2/detection_audit_overlay.jsonl \
  --sealed-sidecar artifacts/sealed/p4c3_detection_sidecar_full_v2.jsonl \
  --output-dir artifacts/experiments/p4c3_native_detection_full_v2

./run_remaining_experiment.sh p4c3 \
  --mode audit \
  --sealed-sidecar artifacts/sealed/p4c3_detection_sidecar_full_v2.jsonl \
  --output-dir artifacts/experiments/p4c3_native_detection_full_v2
```

Run the corrected P4C-4/5 v2 zero-call matrix. It contains 600 structural
scenario variants and 4,800 outcomes (600 × 8 arms), split into 120
calibration, 240 adaptation, and 240 sealed-holdout cases. These are robustness
variants derived from three base templates, not 600 independent real-source
cases. Adaptive arms update only through `observe_receipt(EccRepairReceipt)`;
the holdout never updates the router.

```bash
./run_remaining_experiment.sh p4c45 \
  --overlay experiments/fixtures/p4c_zero_call_v1.jsonl \
  --config experiments/fixtures/p4c45_prequential_v2.json \
  --output-dir artifacts/experiments/p4c45_prequential_v2 \
  --run-mode fresh
```

Verify the completed primary-evidence bundle. This only reads manifests and
binds their roots; it performs no model calls.

```bash
./run_remaining_experiment.sh --verify \
  --p4c1-run artifacts/experiments/p4c1_real_sources_full_v2 \
  --p4c3-run artifacts/experiments/p4c3_native_detection_full_v2 \
  --p4c45-run artifacts/experiments/p4c45_prequential_v2
```

## Supplementary answer confirmation

P4C-2/P4C-6 do not authorize commit, do not update GHOST, and do not support
the primary claim. P4C-1 currently has a natural answer-query contract for
LongMemEval only; MemFail and poison fail closed until separate contracts exist.

```bash
./run_remaining_experiment.sh p4c2 --prepare \
  --p4c1-run artifacts/experiments/p4c1_real_sources_full_v2 \
  --longmemeval-data data/external/longmemeval/input/longmemeval_s_cleaned.json \
  --inputs artifacts/experiments/p4c2_answer_inputs_v1.jsonl \
  --limit 5

./run_remaining_experiment.sh p4c2 --preflight \
  --p4c1-run artifacts/experiments/p4c1_real_sources_full_v2 \
  --inputs artifacts/experiments/p4c2_answer_inputs_v1.jsonl \
  --limit 5
```

The first command that performs P4C paired answer calls is the following. Use
`--execute-fake` first if only a zero-call wiring check is desired.

```bash
./run_remaining_experiment.sh p4c2 --execute-live \
  --p4c1-run artifacts/experiments/p4c1_real_sources_full_v2 \
  --inputs artifacts/experiments/p4c2_answer_inputs_v1.jsonl \
  --llm-config ~/.config/cmd-memory/live-llm.json \
  --limit 5 \
  --output artifacts/experiments/p4c2_live_efficacy_v1
```

Finally, create the P4C-6 reference sidecar after verifying the P4C-2 seal, run
the zero-call exact diagnostic, and optionally run a separately configured
semantic judge. The evaluator writes outside P4C-2 and never updates the router.

```bash
./run_remaining_experiment.sh p4c6 --prepare-sidecar \
  --p4c2-run artifacts/experiments/p4c2_live_efficacy_v1 \
  --longmemeval-data data/external/longmemeval/input/longmemeval_s_cleaned.json \
  --sidecar artifacts/sealed/p4c6_longmemeval_sidecar_v1.jsonl

./run_remaining_experiment.sh p4c6 --preflight \
  --p4c2-run artifacts/experiments/p4c2_live_efficacy_v1 \
  --sidecar artifacts/sealed/p4c6_longmemeval_sidecar_v1.jsonl

./run_remaining_experiment.sh p4c6 --evaluate --backend exact \
  --p4c2-run artifacts/experiments/p4c2_live_efficacy_v1 \
  --sidecar artifacts/sealed/p4c6_longmemeval_sidecar_v1.jsonl \
  --output artifacts/experiments/p4c6_exact_v1

./run_remaining_experiment.sh p4c6 --evaluate \
  --backend openai-compatible \
  --llm-config ~/.config/cmd-memory/live-judge.json \
  --p4c2-run artifacts/experiments/p4c2_live_efficacy_v1 \
  --sidecar artifacts/sealed/p4c6_longmemeval_sidecar_v1.jsonl \
  --output artifacts/experiments/p4c6_semantic_v1
```

Every resumable stage requires unchanged roots and parameters. Use
`--run-mode resume` only with the same output directory and frozen inputs.

## Legacy four-arm answer stage

The ECC memory loop remains unchanged and zero-call:

```text
syndrome -> mutually exclusive incident -> GHOST selection
  -> copy-on-write repair -> ECC check -> commit/rollback
  -> EccRepairReceipt -> GHOST update
```

The call-required stage starts only after that control plane is ready. It sends
deployment-visible LongMemEval questions and already-frozen retrieval memories
to one OpenAI-compatible answer model for four arms: `vanilla`, `static`, `cmd`,
and `ghost`. Predictions are then sealed. The launcher does not open reference
answers, call a live judge, update GHOST from answer quality, or replay a trace
after deleting memory.

This path is scientifically degraded because its historical context composer
used character budgets, could truncate canonical session JSON inside a record,
and forwarded nested message metadata such as `has_answer`. Existing prediction
seals remain useful as wiring, firewall, and context-pathology evidence only.
They do not support answer-quality, arm-ranking, or repair-efficacy claims.

## Prerequisites

The preflight requires these completed artifacts and inputs:

- `artifacts/experiments/p4c1_real_sources_v1/p4c1_manifest.json`
- `artifacts/experiments/p4c_zero_call_prior_calibration_v1/prior_calibration_manifest.json`
- `artifacts/experiments/longmemeval_m0_r1_s5_live_ready_v1/manifest.json`
- `data/external/longmemeval/input/longmemeval_s_cleaned.json`

It verifies that P4C-1 is successful, zero-call, gold-free, label-free, and
receipt-only; that mixed-GHOST prior support is ready; that the retrieval
manifest has the frozen schema; and that the LongMemEval file hash is exactly
the source root bound by P4C-1. It also checks that every selected question has
an identity-bound retrieval snapshot in all four arms. A manifest without those
prediction records is rejected before any paid call.

If the live-ready retrieval artifact has not yet been materialized, create it
with the zero-call P3A runner. The oracle is opened only after each question's
retrieval snapshots have been written, for its separate offline retrieval
score; it is never copied into a snapshot or prediction input.

```bash
python -B -m experiments.run_longmemeval_m0_r1 \
  --data data/external/longmemeval/input/longmemeval_s_cleaned.json \
  --oracle data/external/longmemeval/oracle/longmemeval_oracle.json \
  --backend in-memory --arms vanilla,static,cmd,ghost \
  --limit 5 --top-k 5 \
  --output artifacts/experiments/longmemeval_m0_r1_s5_live_ready_v1
```

Create a credential file outside version control and restrict its permissions:

```json
{
  "base_url": "https://provider.example/v1",
  "api_key": "replace-with-a-real-key",
  "model": "frozen-answer-model-id"
}
```

```bash
chmod 600 /absolute/path/to/cmd-live-llm.json
```

Only `base_url`, `api_key`, and `model` are accepted. The file is parsed as JSON,
never sourced as shell code. The key is neither printed nor copied into an
artifact.

## Run sequence

1. Inspect the plan. This performs no writes and no calls. `--limit` is the
   number of questions, so the maximum answer-call budget is `4 × limit`.

```bash
./run_remaining_experiment.sh legacy-answer --plan --limit 5
```

2. Run the root/config preflight. This also performs no calls and does not create
   the experiment output directory.

```bash
./run_remaining_experiment.sh legacy-answer --preflight \
  --llm-config /absolute/path/to/cmd-live-llm.json \
  --limit 5
```

3. Explicitly authorize the live run. Start with the five-question bounded run.

```bash
./run_remaining_experiment.sh legacy-answer --execute \
  --llm-config /absolute/path/to/cmd-live-llm.json \
  --limit 5 \
  --output artifacts/experiments/remaining_live_confirmation_v1
```

The first two commands never contact the configured endpoint. Calls begin only
after the third command enters `--execute` and the preflight passes.

The five-question command is a bounded live confirmation, not the full headline
run. For a preregistered size `N`, first materialize a separate retrieval run
with `--limit N`, then pass that directory through `--retrieval-run` and use the
same `--limit N` for plan, preflight, execute, and resume. The planned answer
budget is exactly `4N`; freeze `N`, model ID, prompt, temperature, context
budget, and `top-k` before the first call. Preflight refuses `N` when any of its
four-arm snapshots is absent.

## Resume after interruption

Use the identical data, retrieval run, model, prompt, temperature, context
budget, limit, and output directory:

```bash
./run_remaining_experiment.sh legacy-answer --execute \
  --llm-config /absolute/path/to/cmd-live-llm.json \
  --limit 5 \
  --run-mode resume \
  --output artifacts/experiments/remaining_live_confirmation_v1
```

Resume validates the frozen binding and completed prediction prefix before it
skips work. Do not change the model ID or experiment parameters during resume.

## Outputs and acceptance checks

The output directory contains:

- `predictions/{vanilla,static,cmd,ghost}.jsonl`: runtime predictions;
- `prediction_outcomes.jsonl`: append-only completion outcomes;
- `prediction_seal.json`: roots of every prediction file and the sealed count;
- `remaining_live_manifest.json`: P4C-1, prior, retrieval, data, model, and seal
  bindings with credentials redacted;
- checkpoint files used for exact resume.

After execution, verify the manifest without opening dataset labels:

```bash
python -m json.tool \
  artifacts/experiments/remaining_live_confirmation_v1/remaining_live_manifest.json
```

Accept the runtime stage only when `status` is `prediction_sealed`,
`sealed_score_opened` is `false`, `router_updated_from_predictions` is `false`,
and `prediction_count` equals `4 × limit`.

The prediction files already use the official export shape
`question_id,hypothesis`. Give the sealed prediction bundle to the offline
evaluator only after runtime completion. Any later accuracy, judge score, or
error analysis must bind `prediction_seal_sha256` and must never be passed to
`P4cGhostRouter.observe_receipt()`.

## Failure meanings

- `P4C-1 live-ABI prerequisite is not ready`: rerun or audit P4C-1; do not bypass
  the gate.
- `mixed-GHOST prior support is not ready`: the prior-calibration receipt is
  missing or incomplete.
- `LongMemEval data root differs`: the raw input is not the immutable source
  used by P4C-1.
- endpoint/HTTP error during `--execute`: keep the output directory and resume;
  do not start a fresh run over a partial directory.
- prediction-seal or prefix mismatch: treat the run as non-resumable evidence
  until the artifact discrepancy is diagnosed.
