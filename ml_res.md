# GHOST Router V1 Implementation Report

## ECC answer causal contrast v2 (2026-08-24)

- Replaced the confounded BM25-versus-committed-state answer rendering with
  receipt-bound `faulted_before` versus `repaired_after` arms.
- State roots now include an explicit `memory_order`; runtime exports both
  before/after states and rejects old v1 artifacts.
- Added distinct preregistered retrieval, injection, granularity and safety
  answer-time semantics under one shared prompt/budget/generation scaffold.
- Verification: 18 focused tests passed; full suite observed
  `1470 passed, 1 skipped, 1 warning, 58246 subtests passed in 33.11s`.
- No model/API call or official scorer was run. The controlled stress track
  remains non-native; answer recovery is unverified.
- Full implementation record:
  `artifacts/design/ecc_answer_causal_contrast_fix_20260824.md`.

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

## E2 v2 Implementation Status (2026-08-20)

- Added versioned typed probe schema `cmd-ghost-skill-conditioned-feedback-v2-typed`.
- Added explicit unknown/fail-closed handling for target binding, actionability
  mode, annotation consumption, downstream confirmation, delayed confirmation,
  and no-regression signals. The historical v1 changed-count path is unchanged.
- Added preregistered protocol manifest `cmd-ghost-ecology-identifiability-v2-typed`
  with pairwise comparable coverage threshold 0.50 and manifest hash generated by
  `v2_protocol_manifest()`.
- Added backward-compatible v1 outcome parsing; fields absent from old artifacts
  remain unknown rather than being reconstructed.
- Validation executed: `31 passed`, `python -m compileall -q cmd_audit experiments`,
  and `git diff --check` passed. Final E2 has not been rerun in this phase.

## E2 v2 Wiring Correction

The previously produced five `phase5_e2_v2` artifacts were generated by the
legacy v1 statistics path with typed coverage computed only as a side
diagnostic. They are not evidence for the typed-v2 estimator. The new explicit
`audit_identifiability_v2` entrypoint uses schema
`cmd-ghost-ecology-identifiability-v2.1-typed-wired`, runs
`_typed_statistics` and typed permutation/placebo controls, and blocks with
`BLOCKED_TYPED_EVIDENCE_UNAVAILABLE` when preregistered coverage is absent.
Formal E2 seeds have not been rerun.

Coverage-gated correction: typed-v2 now uses Kleene three-valued evidence
(`True`/`False`/`None`), with absent actual actionability/target evidence staying
unknown.  New reports use
`cmd-ghost-ecology-identifiability-v2.2-typed-wired`; when any preregistered
coverage gate fails, claim-bearing correlations and concordance are JSON null.
The explicitly invalid v2/v2.1 artifacts were removed as user-authorized,
regenerable outputs; the E2-v1 path remains untouched.

## Live typed materialization audit (implementation only)

The frozen live input schema currently exposes runtime items/raw events as
`event_id` plus text, but does not provide a stable next-event retrieval/store
lineage for every candidate branch. Therefore changed item IDs, actionability
mode, target binding, and target match can be materialized from the executed
state trace, while annotation consumption, delayed confirmation, and
no-regression remain `None` unless a real later event is supplied. No
chain-attempt, recovery, gold, or shadow value is used as confirmation.

Added case schema `cmd-v4-prequential-case-v2-typed-evidence` with explicit
legacy-v1 adaptation, changed-ID tuples, actionability/target evidence, and
hash-bound provenance. Added `cmd-live-followup-evidence-v1` branch-isolated
follow-up tracker enforcing effective-after event indices and family boundaries.
The shard manifest schema is `cmd-v4-materialized-shard-v2-typed-evidence`.
Current frozen inputs do not expose a stable per-branch next-event retrieval/use
linkage, so annotation consumption, delayed confirmation, and no-regression
coverage remain unavailable until a live event source is provided.
Formal materialization was not run. Template:

```bash
python -m experiments.v4_materialization --input <frozen-live-input.jsonl> \
  --output <new-typed-cases.jsonl> --lane single_gpu \
  --progress <typed-progress.jsonl> --backend experiments.v4_live_materialization:live_backend
```

The command requires the configured live executor/verifier and must target new
non-existing output paths; model calls and source event linkage must be recorded
in the resulting manifest before any E2/E4 run.

## Zero-call typed enrichment preflight

Added CLI: `python -m experiments.zero_call_typed_enrichment` with explicit
prepared/legacy inputs, new output/manifest paths, optional `--limit`, and no
model-client construction. A 10-case temporary preflight processed 10 cases /
40 intents with 0 new model calls, 0 mismatch cases, changed IDs for 40/40
intents, target binding/match observed for 2/40 intents, and annotation
consumption `None` for 40/40. The legacy manifest reported historical upstream
calls as `{answer_generation: 3258, shadow_judge: 3258}`; these are provenance
only and are not fresh replay evidence. The preflight output was temporary and
was not retained as an artifact.

Formal command template:

```bash
python -m experiments.zero_call_typed_enrichment \
  --prepared artifacts/ghost_public_call_v1/prepared_cases.jsonl \
  --legacy artifacts/ghost_public_call_v1/runs/ghost-live-20260814T075634Z/materialized/single_gpu.jsonl \
  --legacy-manifest artifacts/ghost_public_call_v1/runs/ghost-live-20260814T075634Z/materialized/single_gpu.jsonl.manifest.json \
  --output <new-typed-enrichment.jsonl> --manifest <new-typed-enrichment.manifest.json>
```

This remains development-only: legacy recovery fields are isolated shadow
reference, no prepared source contains real follow-up branch lineage, and
annotation/delayed/no-regression fields therefore remain unknown. It is not
fresh live or confirmatory evidence.

## Session lineage v2 implementation

Added `cmd-session-lineage-v2` normalized structured events and a
`cmd_audit.adapters.session_lineage_cli` selection-driven export seam. Stable
session/stream/branch/event IDs, parent/state hashes, request/response chains,
branch-local unique tool bindings, explicit annotation-to-item bindings, typed
execution evidence, source schema and payload hashes are required; free text is
never parsed for IDs. Legacy v1 loader/API remains unchanged. Unknown/legacy
source versions fail closed and real claude-tap coverage is `UNVERIFIED` without
a stable normalized export sample. A separate
`cmd-session-lineage-selection-v1` JSONL binds session/family/branch/repair
intent, selected/effective-after indices and an exposure window. Without that
selection source the exporter produces an explicit empty coverage audit; with it
the exporter projects three-valued annotation/delayed/no-regression evidence and
records source refs, unknown reasons, schema versions, hashes, and zero model/
network calls. A narrow merge seam maps one identity-bound selection into the
existing V4 typed fields while retaining branch provenance; it rejects future,
cross-family, cross-candidate, and schema-mismatched merges.

Example command (fixture or exported normalized claude-tap JSONL only):

```bash
python -m cmd_audit.adapters.session_lineage_cli \
  --source <normalized-claude-tap-export.jsonl> \
  --selections <selection-spec.jsonl> \
  --output <new-lineage.jsonl> --manifest <new-lineage.manifest.json>
```

[RESULT] session_lineage_review_tests=103 passed
[RESULT] compileall=passed
[RESULT] git_diff_check=passed
