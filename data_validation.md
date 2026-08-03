# Data Validation

## Dataset Identity

- dataset:
  - MemTrace-B protocol-reproduction arena:
    `data/probe_cases/memtrace_kp_cases.json`
  - MemFail external-validity arena:
    `data/probe_cases/memfail_cases.json`
  - STALE item-layer arena:
    `data/probe_cases/stale_item_cases.json`
- source:
  - MemTrace-B contains 2,047 cases derived from public HaluMem data. It is a
    protocol reimplementation because the MemTrace-B authors did not release
    their artifact; it must not be described as the authors' dataset.
  - MemFail contains 692 cases converted from the upstream MemFail repository
    at revision `61ab3028a6dc4a67b222f0d3a37a62e1d479ade9`.
  - STALE contains 1,200 deliberately constructed item-stale/item-conflict
    perturbations over queries from the repository's approved raw sources.
- expected split:
  - These are immutable observational streams, not gradient-training datasets.
    There is no train/validation/test split.
  - `--seed` freezes stream order and `--limit` freezes the selected prefix.
    Arena manifests now bind both source bytes and the ordered selected stream.

## Reality Check

- files present: yes; all three default arena inputs load successfully.
- real or mock:
  - MemFail is an external released benchmark conversion.
  - MemTrace-B is benchmark-derived real source data under a reproduced
    protocol, not an author-artifact replication.
  - STALE uses real source queries with synthetic, balanced defect injection.
    The injected labels are constructed and must be disclosed; they are not
    naturally observed production failures.
- evidence:
  - JSON row counts are 2,047 (MemTrace-B), 692 (MemFail), and 1,200 (STALE).
  - Every file has one consistent top-level case schema, unique `case_id`
    values, non-empty queries and gold answers, at least one baseline, at least
    one memory item, and object-valued scoring metadata.
  - Exact source SHA-256:
    - MemTrace-B:
      `df655c77b3626f9a2cb5b6c4783e2db06c1bba6d12e9ee2192206cd1b2b44eda`
    - MemFail:
      `65f0d873be65ebaac796d4b1a62669d2db88a31e1d5b69666858e6e5dd62ab50`
    - STALE:
      `1068e8185530aabd0e799eb633b81cf3bc197543d6c1e2e01ddf12613f914612`
  - The checked-in `CHECKSUMS.sha256` agrees for MemTrace-B and STALE.
    The combined MemFail probe file is not listed there, so each arena artifact
    records its exact source hash directly rather than relying on that registry.

## Split Integrity

- train split: not applicable; the arena performs no parameter training.
- val split: not applicable; `--limit 50` is a deterministic execution smoke,
  not a validation set for choosing a model or threshold.
- test split: the full immutable arena stream is the reporting population.
- leakage risk:
  - Family/case order is deterministic, and selected case IDs are serialized
    in order through `selected_case_ids_sha256`.
  - Gold answers and evidence exist for shadow evaluation, but runtime
    construction and selection declare `runtime_uses_gold = false`; analysis
    rejects any manifest that does not.
  - The main remaining leakage risk is procedural: tuning prompts, thresholds,
    or result wording after reading full shadow outcomes. Freeze those choices
    before the full run.

## Label / Target Health

- label format: nullable `perturbation_label` strings mapped to the zero-call
  hook's explicit `Fill` or `Fix` runtime branch.
- distribution or range:
  - MemTrace-B 50-case seed-24 smoke: 37 null, 8 safety, 2 item-conflict,
    1 item-stale, 1 retrieval, 1 granularity; 8 Fill / 42 Fix.
  - MemFail full stream: 235 retrieval, 200 granularity, 157 safety,
    100 item-conflict; 114 Fill / 578 Fix.
  - STALE full stream: 600 item-stale and 600 item-conflict;
    103 Fill / 1,097 Fix.
- obvious anomalies:
  - No duplicate case IDs, empty required text fields, missing baselines,
    missing memory arrays, or schema variants were observed.
  - Fill is non-trivial in every arena and must remain an explicit routed
    abstention rather than enter Fix-arm denominators.

## Preprocessing Check

- expected preprocessing:
  - Load immutable probe cases, apply deterministic seed/limit selection,
    derive `ArenaCase`, route Fill/Fix without model calls, and keep shadow gold
    out of runtime candidate construction and selection.
- observed preprocessing:
  - The three `--validate-only` entry points load successfully and emit source,
    ordered-case-ID, and full-selected-stream hashes.
  - `arena_manifest` now stores fingerprint schema version, resolved source
    path, source byte size and SHA-256, ordered case-ID SHA-256, and derived
    selected-case SHA-256.
  - Analysis rejects missing/unsupported fingerprints, non-file sources,
    malformed hashes, case-count/case-ID mismatches, and changed source bytes
    when the recorded source path remains mounted.
- mismatch:
  - Static dataset/provenance checks found no blocking mismatch.
  - Live `budget_aligned` behavior cannot be established by `--validate-only`;
    it requires the real answerer and selection-judge endpoints. Unit coverage
    now exercises the production backend-counter branch rather than only the
    fixture's logical-budget fallback.

## Verdict

- PASS

## Next Step

- recommended command:
  `./run_remaining_experiments.sh --role gpu0 --smoke`
- reason:
  Run the 50-case real-endpoint preflight, then require the log to report
  `cmd_budget_source_distribution=backend_call_counters:<n>` and inspect
  `arm_comparison_coverage_rate`, `fix_cases_without_arm_comparison`,
  `budget_aligned_rate`, `budget_aligned_pairs`, failures, and abstentions
  before authorizing the full arena. This is an execution-budget gate, not a
  substitute for the dataset-quality PASS above.
