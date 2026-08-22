# Remaining call-required experiment

`run_remaining_experiment.sh` is the safe entrypoint for the first experiment
that needs external model calls after P4C-0 and P4C-1. With no mode flag it only
prints a plan. It cannot spend an API call unless `--execute` is present.

This is intentionally different from the older plural
`run_remaining_experiments.sh`, which controls legacy Route A/V4/GPU jobs. Do
not use the plural script for this P4C follow-up.

## What the script runs

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

Consequently, this run establishes live answer-generation evidence over frozen
retrieval. It is not by itself a P4C repair-efficacy result. P4C commit authority
still comes only from ECC receipts; benchmark accuracy belongs to a later
sealed evaluator.

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
./run_remaining_experiment.sh --plan --limit 5
```

2. Run the root/config preflight. This also performs no calls and does not create
   the experiment output directory.

```bash
./run_remaining_experiment.sh --preflight \
  --llm-config /absolute/path/to/cmd-live-llm.json \
  --limit 5
```

3. Explicitly authorize the live run. Start with the five-question bounded run.

```bash
./run_remaining_experiment.sh --execute \
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
./run_remaining_experiment.sh --execute \
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
