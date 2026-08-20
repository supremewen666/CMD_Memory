# Zero-call E2/E3/E4 analysis — v6 (2026-08-20)

## Protocol and tests

`iterations/judge_v6.md` was verified as `PASS_ZERO_CALL_AUDIT_ONLY`. The frozen
pytest collection passed **103 tests**; `compileall` and `git diff --check` passed.
No network, model, or API calls were made.

Frozen inputs were checked before execution: prepared cases (543 rows), SHA-256
`0b1b13ac255382433c37711585760e7d7842b3fe03b5fbe9124fa6f12bb9a94e`; legacy
materialization (543 rows), SHA-256
`2866229eeb9dc1224caa4bbc9e7197ff8a209bb2c169663c1c57dddb9e512f2e`; legacy
manifest SHA-256 `1ad801da1a4e34cebe6512fef6ac587fae64e0e081be151b255ff46d739554e1`.

## E2 — typed enrichment and coverage-gated audit

Commands:

```text
python -m experiments.zero_call_typed_enrichment --prepared artifacts/ghost_public_call_v1/prepared_cases.jsonl --legacy artifacts/ghost_public_call_v1/runs/ghost-live-20260814T075634Z/materialized/single_gpu.jsonl --legacy-manifest artifacts/ghost_public_call_v1/runs/ghost-live-20260814T075634Z/materialized/single_gpu.jsonl.manifest.json --output artifacts/ghost_public_call_v1/runs/zero-call-typed-enrichment-20260820-v6/enriched.jsonl --manifest artifacts/ghost_public_call_v1/runs/zero-call-typed-enrichment-20260820-v6/enriched.manifest.json
python -m experiments.ghost_ecology_zero_call --cases .../enriched.jsonl --output .../e2-typed-seed{24..28}.json --bootstrap-samples 10000 --seed {24..28} --decoupling-seed 91 --feedback-version typed-v2
```

Enrichment produced 543 rows / 2,172 intents, `mismatch_case_count=0`,
`model_calls_new=0`; enriched JSONL SHA-256 is
`cd8b483029b2df845935a7d122cdb5bdb3c3d94a67496ff418d0bf2aa481e97e`.
All seeds 24–28 independently produced the same coverage-gated decision:
`BLOCKED_TYPED_EVIDENCE_UNAVAILABLE`. Candidate observed coverage was 372/2,172
(0.1712707182), unknown 1,800/2,172; pairwise comparable coverage was 0/2,235.
All claim-bearing Pearson/bootstrap/pairwise values are JSON `null`, and both
controls are `NOT_RUN_COVERAGE_BLOCKED`. Every artifact records `model_calls=0`.
The semantic vocabulary hash is
`dd7115bebaa008007905290a6af597ef023ea06ec609fb33c5943960e7e25533`.

## E3 — deterministic poison-density sweep

```text
python -m experiments.poison_density_sweep --output artifacts/ghost_public_call_v1/runs/poison-density-sweep-20260820-v6/report.json --recall-size 10 --max-density 0.9 --threshold 0.6 --cases-per-cell 5
```

The report SHA-256 is `161adf9a968aa713b7a886c83e5c24ec12d90d1f4aea98cf8f6303e374982da5`.
The full 10-cell density grid ran with 5 cases/cell and `model_calls=0`.
`anchored_contrast` retained F1=1.0 at every positive density; both
`minority_vote` and `loo_reconstruction` first inverted at density 0.6 and had
F1=0.0 from 0.6–0.9. This is the deterministic lexical-agreement oracle scope,
not a judge-in-loop detector claim.

## E4 — prospective deployment zero-call evaluation

```text
python -m experiments.v4_prequential_runner --cases .../zero-call-typed-enrichment-20260820-v6/enriched.jsonl --output-dir .../e4-prospective-deployment-20260820-v6 --candidate-budget 4 --ghost-evaluator artifacts/neuro_symbolic_evolution_v4/ghost_ecology_v3/deployment-evaluator-preaction-v1-seed24.json --ghost-protocol artifacts/ghost_public_call_v1/ghost_live_v2/protocol.json --ghost-feedback-mode prospective_deployment --bootstrap-samples 10000 --bootstrap-seed 24
```

This completed locally with 543 cases, 9 arms, and zero model calls. Output
report SHA-256 is `309a45c75528b3c39bb79a4bcd58105c161fdade272fd3904e2711965c4c42d5`;
run manifest SHA-256 is
`34ab067f483b1b214daeddf22880885505b7f08cc911ffe344ed546424dcfca`.
The prospective mode kept 543 delayed feedback items pending and made no runtime
shadow/recovery update. The development gate failed (`primary_passed=false`),
and both prospective ghost calibration gates failed. Partition utility values
are audit-only diagnostics; no real follow-up lineage exists, so observable /
actionability, annotation/delayed/no-regression, fallback/abstention and router
reward typed claims remain `UNVERIFIED/BLOCKED`, not live evidence. E4b
descriptor/random/unkeyed was not run because the existing runner has no such
claim-bearing CLI.

## Final boundary

E2 and E4 do not establish real normalized claude-tap follow-up coverage.
E3 is the only formal positive-scope zero-call experiment here; it supports the
stated density boundary only.
