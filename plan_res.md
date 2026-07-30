# Implementation Plan — Self-Evolving Memory Repair Ecologies

## Dataset

### Primary recurrent protocol

- `data/probe_cases/real_recurrent_cases.json`: 600 repository cases, 120 recurrence families × 5 variants. Variants 0–2 are update-producing; variants 3–4 are held-out within-family probes.
- Immutable family split: represented/unseen at family or user boundary, never case boundary. Family metadata is evaluation-only and must not enter runtime retrieval, mutation, selection, or context construction.
- Offline checkpoints: `L0` before updates, `L1/L2/L3` after represented variants 0/1/2.
- Prequential order: evaluate case `t` against library `L_t`, irreversibly record its outcome, then allow its post-outcome event to affect `L_(t+1)`.

### External protocol

- `data/probe_cases/memtrace_kp_cases.json`: 2,047 MemTrace-B protocol-reproduction cases, 182 families. It is a protocol reproduction over HaluMem-derived data, not the authors’ artifact.
- `data/probe_cases/memfail_cases.json` when available: 692 MemFail cases.
- Repository smoke validation may use a deterministic small subset only. Full external evaluation remains an A100 task.

### Integrity and leakage constraints

- Preserve `data/probe_cases/CHECKSUMS.sha256`; do not edit probe data.
- Repaired context must remain a pure function of `(recall_set, OperatorSpec)` and may not read `case.gold_*`.
- Gold-supervised scores exist only in a shadow analysis record after all gold-free candidate contexts/rank signals have been constructed.
- Candidate identity, ties, missing scores, NaN values, abstentions, seeds, family split, checkpoint, arm, scorer identity, and effective-after case must all be serialized.
- `no-update` receives the same candidate evaluation/discovery budget as treatment but never commits revisions or population changes. Arm state is isolated; no mutable store is shared.

## Model (System Components)

The repository root is the project; no detached toy `project/` is created.

### Existing Lamarckian layer

- `cmd_audit/repair/operator_library.py`
  - immutable `OperatorSpecRecord`, `SkillRevisionRecord`, evidence, lifecycle, library-version, and append-only store;
  - canonical JSON/spec hashes are genotype identities.
- `cmd_audit/repair/evolution.py`
  - revision retrieval, conservative Beta weight, promotion, anchor replay, soft retirement, and `EvolutionCoordinator`;
  - this remains the Lamarckian/versioning baseline and must not be renamed Darwinian.
- `experiments/evolution_runner_common.py`
  - offline and prequential execution, experience tapes, arm isolation, artifacts.

### P0 Gold-free identifiability

New `cmd_audit/eval/gold_free_identifiability.py`:

- input unit: one `(case_id, failure_type)` with the same set of per-skill `gold_free_gain` and shadow `gold_supervised_gain`;
- deterministic ranking: finite scores descending, then lexical `skill_id`; ties are explicit and do not silently become agreements;
- output:
  - top-1 agreement and tie-aware agreement;
  - best-vs-second gold-free margin;
  - by-failure counts/rates;
  - coverage/agreement abstention curve over frozen thresholds;
  - supervised regret of the gold-free choice;
  - hard cases with reasons (`disagreement`, `tie`, `missing`, `nonfinite`, `abstained`).

Formulas:

```text
s_gf(c)  = argmax_s Δ_gf(c,s)
s_gold(c)= argmax_s Δ_gold(c,s)
agree(c) = 1[s_gf(c)=s_gold(c)]
margin(c)= Δ_gf(c,s_(1)) - Δ_gf(c,s_(2))
coverage(τ)= |{c: margin(c) >= τ}| / |C_eligible|
agreement(τ)= mean(agree(c) | margin(c) >= τ)
regret(c)= max_s Δ_gold(c,s) - Δ_gold(c,s_gf(c))
```

This is empirical identifiability, not a conformal or theoretical guarantee.

### P0 Skill ecology

New `cmd_audit/repair/skill_ecology.py`:

- `SkillExecution`: skill/revision/spec identity, repaired context hash, finite gain or missing reason, cost, success.
- `CompetitiveResult`: all independent executions, winner/losers, runner-up, margin, tie/abstain/all-failed semantics.
- `CompetitiveExecutor`: consumes already retrieved candidates and a callback that materializes/evaluates each candidate independently from the same base recall snapshot.
- `CompetitionEvent` / `EcologyTracker`: append-only observational events by checkpoint/round.
- Snapshot outputs:
  - skill × failure attempt/win matrix;
  - win-rate entropy and specialization index;
  - pairwise niche-overlap cosine;
  - overall winner entropy;
  - adjacent-checkpoint Jensen–Shannon divergence.

Metrics:

```text
win_rate(s,f) = wins(s,f) / attempts(s,f)
p_s(f) = win_rate(s,f) / Σ_f' win_rate(s,f')
specialization(s) = 1 - H(p_s) / log(|support(p_s)|)
overlap(a,b) = <v_a,v_b> / (||v_a|| ||v_b||)
JSD(P,Q) = 0.5 KL(P||M) + 0.5 KL(Q||M), M=(P+Q)/2
```

Empty vectors, singleton supports, ties, missing gains and abstentions have explicit deterministic values/statuses.

### P0 Darwinian operator population

New `cmd_audit/repair/darwinian.py`, layered beside—not inside—the Lamarckian coordinator:

- immutable `OperatorIndividual` with individual ID, spec hash, generation, parent IDs, birth operation, niche, fitness observations;
- `mutate_operator`: seeded typed edits over valid `OperatorSpec` fields; parent remains unchanged; no empty pipeline;
- `crossover_operators`: seeded one-point/copy-safe crossover; normalize/validate child; reject empty/no-op/duplicate offspring with an auditable reason;
- `DarwinianPopulation`: fixed capacity, deterministic seeded reproduction, genotype deduplication and genealogy;
- selection modes:
  - `global_truncation`: highest finite aggregate fitness, then lower cost, then lexical identity;
  - `niche_elite`: retain best individual per failure/operator niche before filling remaining slots globally.

Invalid or nonfinite individuals cannot win selection. Empty population and zero-valid-offspring are valid observable states, not silent fallbacks.

### P1 Exp24 integration and arms

Incrementally extend `experiments/evolution_runner_common.py` or add a narrowly scoped ecology runner:

- `no_update`
- `random_skill`
- `fixed_library`
- `single_top1`
- `competitive_topk`
- `lamarckian`
- `darwinian_global`
- `darwinian_niche`

All arms share case order, candidate manifest, scorer, success threshold (`0.1`), execution budget and seed manifest. State/store instances are per arm.

### P1 Chain and conflict support

Only implement when the existing `OperatorSpec` executor can apply `B` to the concrete output of `A`:

```text
chain_gain(A→B) = Δ(A→B) - max(Δ(A), Δ(B))
```

Static concatenation without executing the intermediate context is not a chain evaluation. Conflict detection may report incompatible action families/targets, but no meta-reasoner is added unless the DSL exposes reliable target identity. Chain deposition remains gated by repeated independent benefit and held-out validation.

## Training (Evolution Protocol)

There is no gradient training in CMD. “Training” means append-only skill/population evolution.

### Lamarckian baseline

1. Retrieve active revisions under the frozen pattern matcher/quota.
2. Execute/evaluate against the current case.
3. Record outcome.
4. After outcome, add the top-3 successful experience event (`Δ >= 0.1`).
5. Activate from the next case only; promote/retire using existing cross-family and anchor gates.

### Darwinian treatment

Configuration defaults for deterministic smoke/unit validation:

```text
population_size = 10
survivor_fraction = 0.5
mutation_rate = 1.0 for replacement slots
crossover_rate = 0.5
success_threshold = 0.1
seed = explicitly supplied
selection = global_truncation | niche_elite
```

Per generation:

1. Evaluate each valid individual on the same case/family batch.
2. Aggregate finite fitness only; record missing/nonfinite observations separately.
3. Select survivors with the frozen selection rule.
4. Fill capacity via seeded mutation/crossover from survivors.
5. Validate/deduplicate offspring; record rejected offspring and genealogy.
6. Publish the new population only after the producing cases are closed.

### Randomness and reproducibility

- Use local `random.Random(seed)` instances; do not use process-global RNG.
- Same input population + observations + seed must produce byte-identical canonical specs, IDs, genealogy, and selected order.
- Different seeds may alter offspring but never mutate parents or prior generations.
- Tie-breaking is lexical after frozen metric keys.

## Testing

### Unit acceptance

- Gold-free:
  - agreement/disagreement;
  - tied top scores;
  - missing and NaN scores;
  - empty/one-skill cases;
  - abstention coverage monotonicity;
  - shadow-gold provenance cannot enter runtime selection.
- Ecology:
  - independent candidate execution from the same snapshot;
  - deterministic winner/loser events;
  - tie and all-failed abstention;
  - correct matrix, entropy, specialization, overlap;
  - JSD is zero for identical rounds and finite for sparse distributions.
- Darwinian:
  - empty population;
  - same-seed reproducibility;
  - parent immutability;
  - child validity;
  - invalid/no-op/duplicate crossover rejection;
  - global and niche-preserving selection;
  - nonfinite fitness cannot win.
- Integration:
  - no-update store/population remains unchanged;
  - arm state isolation;
  - effective-after boundary;
  - existing Exp24 controls still run.

### Verification sequence

1. Run focused new tests plus existing operator-library/evolution-gate/Exp24 runner tests.
2. Run broader `python -m pytest tests/ -q`.
3. Run a deterministic repository probe smoke that emits `[RESULT]` lines.
4. Record exact commands, pass/fail counts, elapsed time, device and smoke metrics in `ml_res.md`.

### Claim acceptance

- No headline percentage is accepted from unit fixtures or smoke data.
- Competitive recovery, specialization, Darwinian-vs-Lamarckian superiority and >90% gold-free agreement remain `UNVERIFIED` until full multi-seed A100 experiments.
- The implementation is complete when all deterministic semantics above are tested, existing tests do not newly regress, and remaining GPU-only claims are explicitly separated.

