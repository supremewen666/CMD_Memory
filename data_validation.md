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
  - The legacy arena reads these as immutable observational streams. It has no
    gradient-training split; `--seed` and `--limit` freeze the selected stream.
  - The V4 evolution protocol is different: it must use family/dependency-group
    blocked prequential splits even though it still performs no gradient
    training. Earlier members may update the repair policy only after their
    outcomes close; later and unseen-family members remain evaluation-only.

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
      `f30bcd2c47b6ec2d28502654d7d2936843ed4c052827835ec4a05d8b65161864`
    - STALE:
      `1068e8185530aabd0e799eb633b81cf3bc197543d6c1e2e01ddf12613f914612`
  - The checked-in `CHECKSUMS.sha256` agrees for MemTrace-B and STALE, and
    `MEMFAIL_CHECKSUMS.sha256` agrees for the combined MemFail probe file.

## V4 Evolution Construction Audit

- source suitability: all three files are valid V4 source datasets; they are
  not interchangeable strata.
- audited hidden-intent construction:
  - MemTrace-B: 2,047/2,047 cases, 182 recurrence families, 100% constructible.
  - STALE: 1,200/1,200 cases, 400 source families, 100% constructible.
  - MemFail: 692/692 cases, 492 source/task families, 100% constructible.
- materialized CPU package:
  - `data/evolution_v4/` contains all 3,939 cases, 1,074 evaluation families,
    912 dependency groups, and 14,164 runtime-only relation requests.
  - dataset manifest SHA-256:
    `9d7c67bf3058407f1cb3fde2fdfad21f2ad83670c9a466c484cad0ca2fedbc84`.
  - validation report decision: `PASS`; report SHA-256:
    `d8a9b670c6fb5f411b4a6c181ef4fb161d348b78e77f17fd0ad5075dffc53e2e`.
- intended ecological roles:
  - MemTrace-B is the primary recurrent evolution stream. Its existing family
    builder groups by user and knowledge point, keeps 32 families unseen, and
    yields 473 earlier update cases plus 1,196 later held-out cases from 150
    represented families.
  - STALE is the direct typed supersession/actionability stratum. Every case
    has one state perturbation and deployment-visible old/new timestamps.
  - MemFail is primarily external transfer and safety coverage. Its retrieval,
    granularity, and safety cases must remain separate niches rather than being
    mislabeled as supersession. Only its 100 item-conflict cases currently have
    one explicit competing-item state target.
- action-shape audit:
  - MemTrace-B: 253 item-stale, 253 item-conflict, and 253 granularity cases
    have one explicit state perturbation; 253 retrieval cases need a retrieval
    action; 496 safety cases and 539 clean cases are null/abstention controls.
  - STALE: all 1,200 cases have one competing-item state target.
  - MemFail: 100 item-conflict cases have one competing-item state target; the
    remaining 592 cases exercise retrieval, granularity, or safety behavior.
- resolved preprocessing issue:
  - All 1,200 source STALE rows contain literal `M_old:`/`M_new:` injector
    markers. The frozen symmetric normalizer removes them from runtime,
    relation-request, and normalized shadow text while retaining the original
    source-case and source-file hashes. Full validation found zero surviving
    runtime template markers.
- final-package status:
  - Runtime rows, sealed shadow intents, dependency split, relation requests,
    source manifest, dataset manifest, and validation report are materialized.
  - `artifacts/neuro_symbolic_evolution_v4/prepared_cases.jsonl` is a later GPU
    materialization input and remains intentionally absent: it still requires
    frozen text-only relation verdicts and complete intent proposals. The
    builder refuses to fabricate either from `perturbation_label` or gold.

## Split Integrity

- gradient-train split: not applicable; neither the arena nor V4 edits model
  parameters.
- V4 update split: earlier members of represented recurrence families only;
  outcomes become effective strictly after the producing event closes.
- V4 validation split: later members of represented families, blocked from
  their update siblings by event order and never used to retroactively select
  the producing action.
- V4 external/safety split: unseen MemTrace users/families plus frozen MemFail
  transfer strata. STALE remains synthetic calibration/controlled evaluation,
  not an external-validity claim.
- `--limit 50` remains an execution smoke, never a model/threshold validation
  population.
- leakage risk:
  - MemTrace splitting must remain user-keyed, not merely case- or family-keyed,
    because one user spans multiple knowledge-point families.
  - STALE `-dimN` siblings and MemFail `-qN` siblings must remain in one split.
  - Family/case order must be deterministic, and selected case IDs must be
    serialized in order through a manifest hash.
  - Gold answers and evidence exist for shadow evaluation, but runtime
    construction and selection declare `runtime_uses_gold = false`; analysis
    rejects any manifest that does not.
  - Injector labels, gold target IDs, and STALE old/new prefixes may construct
    sealed evaluator intents, but may not enter runtime policy inputs.
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
  - Load immutable probe cases and bind exact source hashes.
  - Derive dependency-safe families and deterministic prequential order.
  - Normalize only preregistered surface markers, build gold-free runtime
    cases, and serialize gold/labels/targets into a separately sealed shadow
    manifest.
  - Build and freeze typed semantic relation graphs from runtime-visible text
    and ordering evidence; never infer a destructive edge from gold labels.
- observed preprocessing:
  - The three `--validate-only` entry points load successfully and emit source,
    ordered-case-ID, and full-selected-stream hashes.
  - `arena_manifest` now stores fingerprint schema version, resolved source
    path, source byte size and SHA-256, ordered case-ID SHA-256, and derived
    selected-case SHA-256.
  - Analysis rejects missing/unsupported fingerprints, non-file sources,
    malformed hashes, case-count/case-ID mismatches, and changed source bytes
    when the recorded source path remains mounted.
  - Existing code already declares `memtrace_kp`, `stale_item`, and `memfail`
    as the default domains for hidden-intent construction and closed-grammar
    evaluation; the audited constructibility rate is 1.00 in every domain.
  - `experiments.build_v4_evolution_dataset` emits five content-bound files;
    `experiments.validate_v4_evolution_dataset` re-reads all three sources,
    verifies every source/case/output hash, replays every hidden intent, checks
    exact retrieved-pair coverage, and rejects runtime gold/template leakage.
- mismatch:
  - No source, runtime, shadow, or family-split mismatch remains in the CPU
    dataset package.
  - Frozen semantic verdicts, graph/cache bindings, and model-proposed complete
    intents remain a separate pre-materialization gate, not a data-quality PASS.
  - Live `budget_aligned` behavior cannot be established by `--validate-only`;
    it requires the real answerer and selection-judge endpoints. Unit coverage
    now exercises the production backend-counter branch rather than only the
    fixture's logical-budget fallback.

## Verdict

- PASS for the three immutable source datasets and the V4 CPU dataset package.
- NEEDS_REVISION for GPU-ready `prepared_cases.jsonl` until the frozen relation
  instrument and intent proposer artifacts are supplied.

## Next Step

- Reproduce or validate the CPU package with:
  `python -m experiments.validate_v4_evolution_dataset --dataset-dir data/evolution_v4 --output data/evolution_v4/validation_report.json`.
- Run the frozen text-only relation instrument over
  `data/evolution_v4/relation_requests.jsonl.gz`, bind the resulting cache and
  complete proposer intents into `prepared_cases.jsonl`, and only then launch
  the two-GPU V4 materialization roles. Gold/labels may never fill that gap.
