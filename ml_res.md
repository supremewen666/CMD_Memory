# Implementation Results — Self-Evolving Memory Repair Ecologies

## Outcome

Implemented and locally verified the deterministic CPU mechanics needed for:

1. gold-free candidate selection with shadow-gold agreement analysis;
2. competitive multi-skill execution and observational ecology metrics;
3. seeded Darwinian mutation, crossover, selection, genealogy, and audit;
4. isolated experiment arms with post-case-only updates;
5. real sequential `A -> B` chain execution and typed conflict reporting.

This is an implementation result, not a paper-result claim. No full A100
experiment or multi-model replication was run in this task.

## Files

### Planning and research translation

- `plan_res.md`
- `survey_res.md`

These translate the three supplied design notes, existing project documents,
and `survey_report.md` into data boundaries, runtime rules, formulas, arms,
tests, and acceptance criteria.

### Runtime and evaluation

- `cmd_audit/eval/gold_free_identifiability.py`
  - keeps gold-free scores and shadow-gold scores in separate typed fields;
  - validates provenance asserting runtime context/selection is gold-free;
  - handles duplicate candidates, ties, missing/NaN scores, abstention,
    supervised regret, per-failure agreement, and margin/coverage curves.
- `cmd_audit/repair/skill_ecology.py`
  - executes every candidate independently from the same base context;
  - records winner, runner-up, losers, margin, tie/all-failed abstention;
  - computes skill × failure attempts/wins, specialization entropy,
    pairwise cosine niche overlap, winner entropy, and checkpoint JSD;
  - evaluates actual sequential skill chains and typed conflicts.
- `cmd_audit/repair/darwinian.py`
  - immutable typed individuals over `OperatorSpec`;
  - seeded mutation and crossover using local `random.Random`;
  - validation, genotype deduplication, parent immutability, genealogy;
  - global truncation and niche-elite selection;
  - observable empty/no-finite/invalid-offspring states.
- `experiments/ecology_runner_common.py`
  - arms: no-repair, no-update, random, fixed, top-1, competitive top-K,
    Lamarckian, Darwinian global, Darwinian niche;
  - evaluates all arms for a case before any mutable-arm update;
  - never sends frozen arms to the updater;
  - supports state fingerprints and deterministic per-case random choice.
- `experiments/run_skill_ecology_smoke.py`
  - CPU-only structural smoke over real records from
    `data/probe_cases/real_recurrent_cases.json`;
  - deliberately labels its applicability score as structural, not recovery.

### Tests

- `tests/eval/test_gold_free_identifiability.py`
- `tests/repair/test_skill_ecology.py`
- `tests/repair/test_darwinian_evolution.py`
- `tests/experiments/test_ecology_runner_common.py`

Package exports were updated in `cmd_audit/eval/__init__.py` and
`cmd_audit/repair/__init__.py`.

## Verification

### P0 focused suite

Command:

```bash
python -m pytest \
  tests/eval/test_gold_free_identifiability.py \
  tests/repair/test_skill_ecology.py \
  tests/repair/test_darwinian_evolution.py \
  tests/experiments/test_ecology_runner_common.py -q
```

Observed:

```text
[RESULT] p0_tests_passed=18
[RESULT] p0_tests_failed=0
[RESULT] p0_test_elapsed_seconds=0.03
```

### Existing evolution/control regression suite

Command:

```bash
python -m pytest \
  tests/repair/test_operator_library_evolution.py \
  tests/eval/test_evolution_gates.py \
  tests/experiments/test_exp24_evolution_runners.py \
  tests/experiments/test_exp24_control_arms.py \
  tests/experiments/test_exp24_operator_trajectory.py -q
```

Observed:

```text
[RESULT] related_regression_tests_passed=43
[RESULT] related_regression_tests_failed=0
[RESULT] related_regression_elapsed_seconds=0.64
```

### Full repository suite

Command:

```bash
git diff --check
python -m pytest tests/ -q
```

Observed on the final run:

```text
[RESULT] diff_check=pass
[RESULT] repository_tests_passed=467
[RESULT] repository_subtests_passed=9
[RESULT] repository_tests_failed=0
[RESULT] repository_test_elapsed_seconds=8.04
```

### Repository-data structural smoke

Command:

```bash
python experiments/run_skill_ecology_smoke.py --limit 12 --seed 24
```

Observed:

```text
[RESULT] smoke_kind=structural_not_recovery
[RESULT] repository_cases=12
[RESULT] candidate_executions=168
[RESULT] deterministic_replay=1
[RESULT] mutable_update_boundary=1
[RESULT] frozen_arm_isolation=1
[RESULT] repeated_execution_count_match=1
[RESULT] elapsed_seconds=0.018763
[RESULT] device=cpu
```

The smoke uses real repository contexts and memory items, but only checks
whether a typed operator structurally changes the context. It does not use
that surrogate as a recovery or quality result.

## Claim Status

The following narrative claims remain **UNVERIFIED**:

- competitive execution improves recovery over a single skill by `X%`;
- skills spontaneously specialize below niche-overlap threshold `Y`;
- Darwinian populations outperform Lamarckian versioning;
- gold-free selection agrees with gold-supervised selection above 90% for
  structurally identifiable failures;
- Qwen-to-Llama replication.

They require the planned full protocol, frozen scorer, multiple seeds, held-out
family probes, confidence intervals, and A100 execution. Unit fixtures and the
structural smoke must not be cited as evidence for these claims.

## Deviations and Risks

- The generic research-implementation template refers to training loss.
  CMD has no gradient-training loop, so no `train_loss` was invented.
  Evolution state transitions and deterministic tests are reported instead.
- The new common runner establishes arm ordering and isolation but does not
  launch the full expensive model-backed protocol.
- `OperatorSpec` can describe multiple generation points, while the current
  live project evaluator is still centered on a single generation point.
  Darwinian code validates and evolves typed specs, but full live fitness for
  arbitrary multi-point offspring requires an executor extension.
- Conflict reporting is deliberately structural. It does not claim semantic
  conflict resolution or add an unvalidated meta-reasoner.
- The repository has a `.codegraph/` index, but no callable `codegraph_*` MCP
  tools were exposed in this session. Narrow literal reads/searches were used;
  the index was not rebuilt or modified.
- The working tree already contained extensive unrelated changes. They were
  preserved; no reset, rollback, or destructive cleanup was performed.

## GPU Work Remaining

```text
[RESULT] gpu_experiment_run=0
[RESULT] headline_claims_verified=0
```

Run the full external protocol only after binding the common runner to the
existing live evaluator and frozen judge:

1. 120 recurrent families × 5 variants with within-family held-out probes;
2. MemTrace-B protocol reproduction (2,047 cases);
3. MemFail (692 cases);
4. all control/treatment arms under equal candidate and scorer budgets;
5. multiple seeds and Qwen → Llama replication;
6. gold-free/shadow-gold agreement, coverage, regret, hard-case, ecology, and
   population-lineage artifacts.

## Observational Arena Update

The external-data experiment path was restructured as one execution stream
with append-only observers:

- `cmd_audit/eval/gold_free_observer.py`: per-case rank fidelity, Spearman
  correlation, oracle rank, shadow regret, null false positives, and 3D probe
  slices;
- `cmd_audit/repair/skill_ecology.py`: checkpointed ecology observer and
  perturbation-response recorder; winnerless windows are recorded as collapse,
  not ecological recovery;
- `cmd_audit/repair/chain_dynamics.py`: co-activation snapshots, directed chain
  attempts, benefit spectrum, directionality, and supported deposition events;
- `cmd_audit/repair/operator_library.py`: ordered `CompositeOperatorSpec` and
  `merge_operators()` without flattening same-generation-point stages;
- `experiments/arena_runner_common.py`: immutable single-path runner with
  explicit gold-free/shadow-gold backend contract;
- `experiments/run_arena_{memtrace,memfail,stale}.py`: dataset-specific stream
  entry points;
- `experiments/analyze_arena_results.py`: descriptive unified analysis.

The runner rejects a backend with `runtime_uses_gold=True`. The existing
`LiveEvolutionBackend` remains gold-supervised because its net-gain path reads
`gold_answer`; it was not relabeled or silently reused as the runtime signal.

Validated real stream structure:

```text
[RESULT] memtrace_cases=2047
[RESULT] memtrace_families=182
[RESULT] memtrace_null_cases=539
[RESULT] memfail_cases=692
[RESULT] memfail_normalized_subsets=4
[RESULT] stale_cases=1200
[RESULT] stale_families=400
```

Final local verification for this update:

```text
[RESULT] observational_focused_tests_passed=27
[RESULT] observational_focused_tests_failed=0
[RESULT] repository_tests_passed=467
[RESULT] repository_subtests_passed=9
[RESULT] repository_tests_failed=0
[RESULT] diff_check=pass
[RESULT] compile_check=pass
```

No GPU arena was run:

```text
[RESULT] observational_gpu_run=0
[RESULT] observational_headline_claims_verified=0
```

### Concrete backend and perturbation follow-up

`experiments/arena_backends.py` now provides the default
`VLLMDualScoreArenaBackend`:

- answer generation uses the configured answerer endpoint;
- runtime gain uses a reference-free grounded-answer rubric over only
  query/context/answer;
- shadow gain uses the frozen gold-aware answer rubric after the runtime score
  has been materialized;
- a shadow-scoring failure preserves the runtime gain;
- candidate retrieval uses structural applicability only;
- deposited staged composites enter subsequent candidate retrieval.

Static inspection and integration fixtures confirm `gold_answer` is read only
inside `_shadow_score()`. A real endpoint run was not started in this task, so
paper results remain unverified.

The arena runner now supports one perturbation treatment via
`--perturb-after` and `--perturb-strategy`. The selected keystone/specialist is
excluded before top-K truncation on every subsequent case, winner observations
feed `PerturbationProbe`, and the final event is written to JSONL and the
unified analysis tables.

The chain deposition remains intentionally one-shot: it is one treatment time
for a natural experiment, not a periodic online learning policy.
