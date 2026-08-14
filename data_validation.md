# Data Validation — GHOST Public Four-Partition Dataset

## Dataset Identity

- dataset: `data/ghost_live_v2`
- upstream sources:
  - LoCoMo `data/locomo10.json`, fixed Git revision
    `3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376`.
  - Mem2ActBench `Mem2ActBench/qa_dataset.jsonl`, fixed Git revision
    `b00726940b5abbe9bd324bdd7a2cb272f5c62a29`.
- transformation: public benchmark content plus one deterministic, explicitly
  disclosed stale/conflicting memory per case. This is a benchmark adaptation,
  not a sample of naturally observed production failures.
- expected split: `ghost_dev`, `ghost_cal`, `ghost_test_rep`, and
  `ghost_test_new`, frozen before relation measurement or intent proposal.

## Reality Check

- files present: yes. The byte-preserved source files, selected ProbeCase
  streams, V4 CPU bundle, partition files, source provenance, build report and
  validation report are all present.
- real or mock:
  - upstream conversations, questions and tool tasks are real public benchmark
    records;
  - the competing memory is synthetic and is declared by
    `synthetic_conflict_injection=true`;
  - no row is claimed to be a delayed deployment observation;
  - `source_provenance.json` sets `independent_source=false` and
    `confirmatory_attestation_eligible=false`.
- evidence:
  - LoCoMo raw SHA-256:
    `79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4`.
  - Mem2ActBench QA raw SHA-256:
    `24fc8692567e5fc16457a7e6c9ec12d68ba06245d53b315c7ced5f76a84fd039`.
  - dataset SHA-256:
    `5603d48f40f7f03b2b100a79a9ec220eeb4981fe7ca64392dc917b38927564b5`.
  - 543 unique cases, 131 families and 543 runtime relation requests.
  - builder model/API calls: 0.
- licensing:
  - LoCoMo is recorded as CC-BY-NC-4.0.
  - the pinned Mem2ActBench repository contains no license file. Its rows must
    remain research-only until the upstream author supplies explicit terms.

## Split Integrity

- `ghost_dev`: 181 cases / 36 families.
- `ghost_cal`: 134 cases / 25 families.
- `ghost_test_rep`: 68 cases / the same 36 represented dev families, with
  distinct case IDs and source records.
- `ghost_test_new`: 160 cases / 70 families absent from every other partition.
- case overlap: 0.
- dev/cal family overlap: 0.
- test-represented families outside dev: 0.
- test-new family overlap: 0.
- dependency-group split violations: 0.
- exact normalized-query recurrence: one group, intentionally confined to one
  dev/test-rep family. The two rows bind different user IDs, source evidence
  and gold tool arguments, so the repeated wording does not reveal the answer.
- leakage risk:
  - family IDs and partition membership are evaluation-only and do not enter
    runtime selection features;
  - runtime rows contain no gold/label/family fields;
  - shadow rows retain gold and the synthetic label for post-selection scoring;
  - the data were selected by this project, so they cannot satisfy the live
    protocol's independent-curator attestation.

## Label / Target Health

- label format: closed CMD `ProbeCase` with one `item_conflict` perturbation,
  a current protected memory, a prior conflicting memory, gold evidence, and a
  recorded zero-score conflict baseline.
- distribution: 543 `item_conflict` cases. This is deliberate specialization,
  not a balanced estimate over all GHOST repair effects.
- domains:
  - `locomo_factual`: 260 cases;
  - `locomo_inferential`: 100 cases;
  - `mem2act_action`: 183 cases.
- shape health:
  - 543/543 unique case IDs;
  - 543/543 constructible hidden intents;
  - every case has two retrieved memories and exactly one relation request;
  - no empty required text, missing evidence, missing baseline, NaN, or schema
    variant was accepted.
- claim limitation: the dataset can evaluate conflict routing and repair. It
  cannot by itself support claims about retrieval misses, safety blocking,
  annotation consumption, natural recurrence or delayed deployment utility.

## Preprocessing Check

- expected preprocessing:
  - fix upstream revisions and source hashes;
  - convert source records into closed ProbeCase rows;
  - inject and disclose one deterministic conflict;
  - group before splitting and keep dev/cal/new family boundaries closed;
  - project gold-free runtime rows and sealed shadow rows;
  - stop before any semantic relation or intent model call.
- observed preprocessing: matches the expected pipeline.
- V4 validation:
  - decision: `PASS`;
  - reasons: none;
  - intent constructibility: 1.00;
  - runtime template marker count: 0;
  - validation report SHA-256:
    `96b09ea21955dcd9ba7f916b0cd056194baeae23b1763d0f9b0d05bd2267a744`.
- old cache cleanup:
  - removed `data/raw_cases` (approximately 296 MB of obsolete downloads);
  - retained tracked `data/probe_cases` and `data/evolution_v4` because current
    tests and CLI defaults still depend on them; they are regression fixtures,
    not disposable caches.

## Verdict

- `NEEDS_REVISION` for a confirmatory live/deployment experiment.
- The public benchmark CPU package itself is valid and reproducible (`PASS`),
  but it is not independently collected, contains synthetic conflicts, has one
  repair label only, and Mem2ActBench redistribution terms are unresolved.

## Next Step

- For a public model-calling benchmark, run the frozen relation instrument and
  intent proposer over `data/ghost_live_v2/cpu_dataset`; use
  `data/ghost_live_v2/partitions` unchanged.
- For a confirmatory deployment claim, obtain a separate post-freeze curator
  stream with source attestation, genuine baseline failures, and matured
  delayed outcomes. Do not set `independent_source=true` for this public bundle.
