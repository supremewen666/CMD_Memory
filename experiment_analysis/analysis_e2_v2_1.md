# E2-v2.2 analysis round 1 — coverage gate

The former v2/v2.1 artifacts were invalid as typed-v2 evidence and have been
removed; this file contains no links to them. E2-v1 remains in
`experiment_res.md` as the historical real negative result.

The active manifest is `cmd-ghost-ecology-identifiability-v2.2-typed-wired-coverage-gated`
with SHA-256 `2ea9d12669609ec78f14ef92c395d1e81bc8cbc1daad14d92362e5aeeef14bdf`.
Thresholds are family correlation 0.20, bootstrap lower bound 0.10, pairwise
concordance 0.55, and pairwise comparable coverage 0.50. The reference is the
materialized recovery_gain shadow artifact, not fresh replay-CMIS.

On the frozen 3,100-case stream (SHA-256
`52569b23e71fe1750a0b3e037670587b0f485df2fa250bd04a24537d61f3d522`), typed
coverage is 0/12,400 observed and 12,400/12,400 unknown. Pairwise coverage is
0.0. Semantic context coverage is 3,100/3,100. Therefore the decision is
`BLOCKED_TYPED_EVIDENCE_UNAVAILABLE`, typed estimator quality is `UNMEASURED`,
and controls are `NOT_RUN_COVERAGE_BLOCKED`.

No Pearson, bootstrap, concordance, or comparable-pair statistic is a claim;
all are JSON null in the v2.2 artifacts. Failure-type coverage is unavailable
and no labels were imputed. The next valid action is fresh live materialization
of typed execution evidence.
