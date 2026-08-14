# GHOST Router V1 Implementation Report

## Prospective live experiment wiring (2026-08-14)

- Added a fail-closed V2 prospective freeze for non-empty `ghost_dev`, `ghost_cal`,
  `ghost_test_rep`, and `ghost_test_new`, including independent-source attestation,
  preparation/config hash, four model snapshot hashes, evaluator hash, candidate
  budget, and current live-code hashes.
- Added single-run test authorization backed by an append-only hash-chained access
  ledger; copied or cross-run authorization is refused.
- The live runner now emits `ghost_selections.jsonl` and uses
  `prospective_deployment` mode: no materialized shadow outcome updates GHOST.
- Added skill-conditioned delayed feedback validation, right-censoring, temporal
  maturity checks, selection binding, and family-bootstrap identifiability gates.
- Fixed the shell's missing evaluator/protocol arguments, added GHOST preflight
  before model start and merge, and retained nohup-compatible detached JSONL
  lifecycle/progress monitoring.
- Added end-to-end logical model-call accounting: answer and judge calls are counted
  in GPU shard manifests and merged into the report; CPU runner calls remain zero.
- Verification: 866 passed, 1 skipped, 58,232 subtests; focused 16 passed; Ruff,
  shell syntax, and diff checks PASS. No model/API/network call was made during this
  implementation turn.
- Boundary: the machinery is ready, but a confirmatory run remains fail-closed until
  genuinely new test cases, source attestation, and real model hashes are supplied.

## GHOST Ecology V3 industry-aligned optimization (2026-08-14)

- Added a pre-action-only evaluator seam; action-result telemetry cannot enter
  selection.
- Added closed delayed-outcome feedback with temporal binding and explicit
  development-proxy provenance. GHOST updates `y_delayed_proxy - q_pre_action`.
- Added `full_v4_observable`, which receives the same evaluator prior and selected
  residual; retained original Full V4 only as a shadow-gold oracle ceiling.
- Replaced instance-fragmented expert identity with 12 preregistered parameterized
  `(effect, typed motif)` repair species. Pattern/local residual effects require
  support 2/3 and otherwise back off to the parent.
- Changed repository hashing to a byte-identical streaming canonical hash; its
  equality with the previous materialized hash is tested.
- Zero-call evaluator: Pearson `0.6393220035`, LB `0.5290066352`, concordance
  `0.7056096430` — PASS.
- Five-seed zero-call replay: GHOST minus same-feedback Full V4 mean family delta
  `+0.0872389171`, worst seed LB `+0.0542759323` — PASS.
- Verification: 862 passed, 1 skipped, 58,232 subtests; Ruff/diff/hash/raw-row
  audits PASS. Model/API/network calls: 0.
- Boundary: the delayed outcome is an existing development proxy. Sealed test and
  real deployment claims remain unavailable until new test data/outcomes mature.

## GHOST deployment evaluator repair (2026-08-13)

- Added a stdlib-only frozen parameterized evaluator with closed deployment feature
  grammar, content-addressed snapshot, fixed 512 buckets and ridge regularization.
- Added family-disjoint `ghost_dev` fit / `ghost_cal` identifiability audit.
- Registered `ghost_hierarchy_v1` as the seventh `v4_prequential_runner` arm.
- Expert species are reusable strategy/effect/proposer tuples; case-bound targets are
  runtime parameters rather than enumerated Experts.
- The runner requires evaluator and protocol files, binds their hashes, updates GHOST
  only on `ghost_dev`, and treats cal/test partitions as evaluation-only.
- Zero-call identifiability: Pearson `0.6374268463`, bootstrap LB `0.5272669761`,
  pairwise concordance `0.7032916087` — PASS.
- Final fair prequential on `ghost_cal`: GHOST-global family delta
  `+0.0628574068`, lower bound `+0.0327165307` — PASS; GHOST-Full family delta
  `-0.0072636236`, lower bound `-0.0309097982` — FAIL.
- Verification: 859 passed, 1 skipped, 58,232 subtests; focused 27 passed;
  Ruff and diff check PASS.
- Scope: evaluator implementation accepted, integrated router still blocked; both
  sealed GHOST test partitions remain empty.

## Scope and Claim Boundary

- Implemented the authorized construction route in `BUILD_SPEC_GHOST_ROUTER_V1.md`.
- The repository root remains the project; no detached toy project was created.
- This run used a frozen 3,100-case, fully materialized outcome stream and made
  exactly zero model/API calls.
- Feedback in this replay is previously materialized shadow-gold utility. Results
  are an offline selector screen, not an end-to-end gold-free or sealed prospective
  claim.
- Existing V1--V4 artifacts were not rewritten. New outputs use the
  `ghost-router-v1-core-*` prefix.

## Components Implemented

- `cmd_audit/repair/ghost_router.py`
  - closed, content-addressed `TypedExpertMotif` identity derived only after typed
    compilation against `FrozenRelationGraph`;
  - registered action-dependent feature contract without case/family/intent/gold
    posterior keys;
  - global, semantic, and signal diagonal sufficient-statistic posteriors;
  - recursive per-feature evidence shrinkage with exact cold-child backoff;
  - known-zero abstention without Thompson noise;
  - selected-action-only `DeploymentObservation` and explicitly separated
    `ShadowOutcomeObservation` schemas;
  - live-mode shadow feedback refusal and evaluation-only update suppression;
  - content-addressed counter-based Gaussian draws and exact snapshot restore/replay;
  - closed `to_mapping/from_mapping` schemas with corruption refusal.
- `cmd_audit/repair/evolution_repository.py`
  - append-only, idempotent `typed_expert_motif`, `ghost_selection`,
    `ghost_observation`, and `ghost_snapshot` event types.
- `experiments/baselines/v4_zero_call_replay.py`
  - the registered `ghost` arm now uses the core router through the existing
    outcome firewall and explicitly opts into `shadow_screening` mode.
- `tests/repair/test_ghost_router.py`
  - public-seam coverage for motif/schema/hash contracts, posterior arithmetic,
    cold/warm shrinkage, abstention, feedback refusal, evaluation-only boundaries,
    deterministic restoration, and repository sedimentation.

## Zero-Call Replay

Command shape (repeated for seeds 24--28):

```bash
python -m experiments.baselines.v4_zero_call_replay \
  --cases artifacts/neuro_symbolic_evolution_v4/neuro_symbolic_evolution_v4/runs/v7-001/cases.merged.jsonl \
  --reference-outcomes artifacts/neuro_symbolic_evolution_v4/neuro_symbolic_evolution_v4/prequential/arm_outcomes.jsonl \
  --output-dir artifacts/neuro_symbolic_evolution_v4/replays/ghost-router-v1-core-seed24 \
  --seed 24 --bootstrap-samples 10000
```

Observed aggregate results from
`artifacts/neuro_symbolic_evolution_v4/replays/ghost-router-v1-core-multiseed-summary.json`:

- cases: 3,100
- seeds: 24, 25, 26, 27, 28
- model calls: 0
- GHOST mean utility: 0.23349019798418227
- GHOST seed sample SD: 0.002956436942714653
- Full V4 mean utility: 0.18455403238812873
- relative to Full V4: +26.515901583303105%
- GHOST harm rate: 0.16238709677419355
- Full V4 harm rate: 0.2135483870967742
- represented utility: 0.2429568467011734
- unseen utility: 0.19531624439164003
- Full V4 unseen utility: 0.17825109589456625
- represented family-macro difference vs Full V4: 0.04735900899785982
- worst-seed one-sided 95% lower bound: 0.036190879433578294
- summary SHA-256:
  `aff95f7f778384934495e988bfd59a89f1660ccced276bfc062545213590cf74`

These figures are copied from the executed, hash-validated summary. No missing
metric is inferred.

## Verification Results

- Focused core/repository: `12 passed`.
- Focused GHOST/zero-call integration: `22 passed`.
- Broad required suites:
  - `python -m pytest tests/repair tests/counterfactual tests/experiments -q`
  - `859 passed, 1 skipped, 1 warning, 58232 subtests passed in 26.93s`.
  - The warning is an existing `deposit_best` deprecation warning.
- Ruff on all changed Python implementation/test files: `All checks passed!`.
- `git diff --check`: passed.

## Deviations and Remaining Risks

- The public `select` signature follows the build spec and requires `graph=` and
  `intents=` keyword arguments. The experiment protocol adapts this explicitly.
- Species lifecycle governance remains orthogonal in the existing repository; V1
  posterior readiness does not read lifecycle state. No posterior niche is enabled
  or disabled by lifecycle transitions.
- The current data stream was already used for algorithm/hyperparameter selection,
  so this implementation replay cannot authorize a confirmatory claim.
- The required next zero-call prerequisite is deployment-feedback identifiability:
  replace shadow-gold utility with a prospectively frozen deployment-observable
  feedback signal. If that fails, model-calling experiments cannot repair the
  feedback-channel identification problem.
