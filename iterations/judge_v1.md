# Review v1 — GHOST Router V1

verdict: PASS

## Scope and claim boundary

This review checks the construction and the completed zero-model-call replay against
`BUILD_SPEC_GHOST_ROUTER_V1.md`. It does **not** promote the replay to a sealed
prospective or end-to-end gold-free claim. The inspected replay uses previously
materialized shadow-gold utility and is correctly labelled
`selector_screen_not_end_to_end_gold_free`.

`plan_res.md` and `survey_res.md` describe the broader memory-ecology program rather
than this later GHOST build. Their still-applicable constraints—stdlib-only runtime,
no runtime gold/case/family selection features, local deterministic randomness,
evaluate-then-update chronology, and append-only provenance—remain satisfied.

## Atomic concept checklist

| Atomic concept | Frozen formula/contract | Code evidence | Result |
|---|---|---|---|
| Closed public schemas | Exact fields plus canonical hash validation | `cmd_audit/repair/ghost_router.py:88-356`, `:445-529`; hostile extra/corrupt mappings covered in `tests/repair/test_ghost_router.py:38-51`, `:224-246` | PASS |
| Stable typed motif | `motif_id = SHA256(closed graph-semantic mapping)` after compile | `ghost_router.py:100-157`, `:716-727`; `compile_intent` precedes extraction from the frozen edge | PASS |
| Forbidden posterior identity | No case/family/gold/outcome/intent/item/free-text parameter key | Closed feature-key grammar at `ghost_router.py:88-125`, graph-identity refusal at `:729-760`, hashed compatibility bucket at `:696-704`; adversarial restore test at `test_ghost_router.py:101-102` | PASS |
| Action-dependent features only | effect, motif, proposer, roles, semantic×effect, graph-number×effect | `ghost_router.py:762-783`; graph number transform is exactly `v/(1+abs(v))` | PASS |
| Global posterior update | `p_j += phi_j^2`, `eta_j += phi_j*r` | `ghost_router.py:556-559`; all values are finite and reward is clipped at `:1051-1054` | PASS |
| Recursive per-feature shrinkage | `alpha=q/(q+kappa)` and child mixes with live parent | `ghost_router.py:808-838`; semantic then signal at `:895-906` | PASS |
| Exact cold backoff | `q=0 => child == live parent`, weight `0` | `ghost_router.py:819-828`; public telemetry test `test_ghost_router.py:121-128` | PASS |
| Shared hierarchy draws | One draw per `(decision index, level, niche, feature)`, reused across candidates | Content-addressed draw at `ghost_router.py:785-806`; samples computed once before candidate scoring at `:891-914` | PASS |
| Known-zero abstention | score exactly `0`, no Thompson noise | `ghost_router.py:907-914`; test `test_ghost_router.py:131-152` | PASS |
| Only-selected learning | Exactly one observation matching committed selected intent; all levels get same direct reward | `ghost_router.py:1024-1054`, `:1060-1084`; plain/forged decisions refused at `:1005-1016`; tests `test_ghost_router.py:54-82`, `:155-212` | PASS |
| Live/shadow separation | deployment mode rejects `ShadowOutcomeObservation` | `ghost_router.py:1032-1049`; test `test_ghost_router.py:155-168` | PASS |
| Evaluation-only boundary | unseen/test observation cannot update posterior | `ghost_router.py:1058-1060`; test `test_ghost_router.py:215-221` | PASS |
| Content-addressed replay | closed snapshot, parent hash, canonical SHA-256; same future is byte-identical | `ghost_router.py:205-356`, `:648-683`, `:1100-1113`; restoration/repeated-selection test `test_ghost_router.py:224-246` | PASS |
| Four append-only events | `typed_expert_motif`, `ghost_selection`, `ghost_observation`, `ghost_snapshot` | `cmd_audit/repair/evolution_repository.py:168-295`; closed schema before ID derivation at `:401-415`; immutable/idempotent insert at `:417-441` | PASS |
| Event binding | parent snapshot, graph, candidate set, versions, selected intent/motif, provenance, RNG index | selection schema `evolution_repository.py:168-197`; observation binding `:199-240`; test `test_ghost_router.py:249-267` | PASS |
| Lifecycle separation | router never queries species/lifecycle state | No lifecycle dependency in `ghost_router.py`; repository lifecycle materialization remains a separate API | PASS |
| Core-router experiment integration | replay instantiates `cmd_audit.repair.ghost_router.GHOSTRouter` | import `experiments/baselines/v4_zero_call_replay.py:22-30`; core arms `:582-621`; runtime dispatch `:750-826` | PASS |
| Registered GHOST ablations | global, hierarchy, no-semantic, no-signal, no-motif, shuffled feedback | `v4_zero_call_replay.py:49-66`, `:595-620`, `:801-824` | PASS |
| Zero model calls | no model client on replay path; report records `model_calls=0` | Replay artifact reports for seeds 24–28 each contain `model_calls=0`; 3,100 GHOST rows per seed were counted | PASS |

## Issues found and fixed during review

1. Repository closed-schema validation originally ran after content-ID derivation,
   so an unknown GHOST event field failed for the wrong reason. Fixed with
   `_prepare_closed`, which validates the exact allowed field set before hashing.
2. Selection and observation events did not both explicitly bind feature/config
   schema versions. Added the versions to `GhostSelectionDecision` and verified
   them against the observation event.
3. Snapshot restoration accepted any string with a recognized prefix, including a
   forged key such as `effect:gold_label`. Replaced prefix-only acceptance with a
   closed grammar for every feature family and added an adversarial regression.
4. `observe` accepted the base `SelectionDecision` type. It now requires a
   hash-bound `GhostSelectionDecision` and verifies that its selected action equals
   the router's pending winner.
5. GHOST selection `event_index` is now persisted and a conflicting decision at the
   same chronological position fails closed.

## Zero-call replay evidence and metric audit

Inspected artifact:
`artifacts/neuro_symbolic_evolution_v4/replays/ghost-router-v1-core-multiseed-summary.json`.

- Cases: 3,100; seeds: 24, 25, 26, 27, 28; candidate budget: 4.
- Each seed has exactly 3,100 `ghost` rows (31,000 total arm rows).
- Each source report's embedded `report_sha256` was recomputed from its canonical
  payload and matched.
- The aggregate embedded `summary_sha256` was independently recomputed and matched:
  `aff95f7f778384934495e988bfd59a89f1660ccced276bfc062545213590cf74`.
- GHOST mean utility: `0.23349019798418227`; Full V4: `0.18455403238812873`;
  relative difference: `+26.515901583303105%`.
- Represented utility: `0.2429568467011734`; Full represented: `0.18611707944128272`.
- Unseen utility: `0.19531624439164003`; Full unseen: `0.17825109589456625`.
- Represented family-macro difference: `+0.04735900899785982`; worst-seed
  one-sided 95% lower bound: `+0.036190879433578294`.
- Harm: `0.16238709677419355`; Full harm: `0.2135483870967742`.

These values pass the development-screen gates versus Full V4, global diagonal
Thompson, and online linear SGD. CVaR is not present in the GHOST multi-seed
aggregate, so no independent confirmatory safety claim is inferred from it; this is
acceptable only because the artifact remains explicitly development-screening.

## Verification commands

- `python -m pytest tests/repair/test_ghost_router.py tests/experiments/test_ghost_router.py tests/experiments/test_v4_zero_call_replay.py -q`
  - `22 passed in 0.19s`
- `python -m pytest tests/repair tests/counterfactual tests/experiments -q`
  - `859 passed, 1 skipped, 1 warning, 58232 subtests passed in 27.08s`
  - Warning is the existing `deposit_best` deprecation warning.
- `ruff check cmd_audit/repair/ghost_router.py cmd_audit/repair/evolution_repository.py experiments/baselines/v4_zero_call_replay.py tests/repair/test_ghost_router.py tests/experiments/test_v4_zero_call_replay.py`
  - `All checks passed!`
- `git diff --check`
  - passed.
- Canonical payload hash audit for five `report.json` files and the multiseed
  summary: all six embedded hashes matched recomputation.

## Final assessment

The GHOST Router V1 construction and the completed zero-call shadow-screening
experiment pass the audited implementation boundary. No real model or API call was
made in this review. The next prerequisite remains the spec's deployment-feedback
identifiability replay; live/model-calling experiments must not be interpreted as a
substitute for that feedback-channel test.
