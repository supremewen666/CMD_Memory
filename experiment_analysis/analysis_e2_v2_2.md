# E2-v2.2 analysis round 2 — protocol-consistency seeds

Seeds 24–28 each produced an independent v2.2 coverage-gated artifact:

- `artifacts/phase5_e2_v2_2_coverage_gated_20260820_seed24.json`
- `artifacts/phase5_e2_v2_2_coverage_gated_20260820_seed25.json`
- `artifacts/phase5_e2_v2_2_coverage_gated_20260820_seed26.json`
- `artifacts/phase5_e2_v2_2_coverage_gated_20260820_seed27.json`
- `artifacts/phase5_e2_v2_2_coverage_gated_20260820_seed28.json`

Every artifact has schema
`cmd-ghost-ecology-identifiability-v2.2-typed-wired-coverage-gated`, feedback
schema `cmd-ghost-skill-conditioned-feedback-v2.2-typed-wired-coverage-gated`,
manifest hash `2ea9d12669609ec78f14ef92c395d1e81bc8cbc1daad14d92362e5aeeef14bdf`,
decision `BLOCKED_TYPED_EVIDENCE_UNAVAILABLE`, observed coverage 0/12,400,
unknown coverage 12,400/12,400, pairwise coverage 0.0, null estimator metrics,
and controls `NOT_RUN_COVERAGE_BLOCKED`. All made zero model calls.

Since the coverage gate blocks estimation before bootstrap, seed differences are
only protocol-consistency checks. They do not establish estimator robustness or
any correlation result. The evidence boundary remains data availability
negative / estimator quality unmeasured; fresh live typed execution evidence is
required.
