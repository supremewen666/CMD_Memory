# Experiment Runners

This package contains the runnable evidence for `EXPERIMENT.md`. The active paper path is claim-centric, not numbered-section-centric.

## Frontline runners

| Claim | Runner | Use |
|---|---|---|
| C3-C4 | `python -m experiments.run_experiment_14_repair_efficacy` | Four-arm repair efficacy: no-repair / random / LLM judge / CMD. |
| C2-C5 | `python -m experiments.probe_exhaustive` | Full single-point oracle; produces the prior bank for transfer. |
| C5 | `python -m experiments.run_experiment_15_prior_transfer` | Leave-one-out prior transfer from offline culprits to online top-K seeds. |
| C6 | `python -m experiments.run_experiment_16_coupled_exhaustive` | Existence decider for coupled `b^d` residuals: does a two-point repair ever beat the best single point? (Verdict C6: 1/30 — no.) |
| C8 | `python -m experiments.run_experiment_17_ecs_structure_ablation` | ECS structure ablation with fixed repair content. |
| C7 | `python -m experiments.run_experiment_18_failure_memory_trajectory` | Online FailureMemory trajectory: recovered priors reduce cost-to-recovery. |
| C7/P5 | `python -m experiments.run_experiment_19_skill_abstraction` | Two-tier FailureMemory skill abstraction: recovered cases -> validated patterns -> pattern seeds. |
| Operator evolution | `python -m experiments.run_experiment_21_operator_headroom` | Composite/parameterized operator headroom over single-point residuals. |
| Operator transfer | `python -m experiments.run_experiment_22_operator_transfer` | Leave-one-out operator-skill transfer with fingerprint retrieval and same-budget random control. |
| Skill evolution A | `python -m experiments.run_experiment_24a_offline_evolution` | Family-split offline closed loop with shared verified experience tape and two read-only probe sets. |
| Skill evolution B | `python -m experiments.run_experiment_24b_prequential_evolution` | Gate-controlled evaluate-before-update simulation with stream-order permutation null. |
| Arena A+B+C | `python -m experiments.run_arena_memtrace` | One-path MemTrace-B stream with gold-free, ecology, and chain observers. |
| Arena A+B replication | `python -m experiments.run_arena_memfail` | MemFail cross-environment signal/ecology replication. |
| Arena B adjacent niches | `python -m experiments.run_arena_stale` | STALE stale-vs-conflict niche observations. |

Recommended sequence:

```bash
python -m experiments.run_experiment_14_repair_efficacy --cmd-attribution exhaustive --limit 0
python -m experiments.probe_exhaustive --limit 0 --aggregate --min-credit 0.05 --out artifacts/sandbox/exhaustive_detail_mincredit05.csv
python -m experiments.run_experiment_15_prior_transfer --prior-bank artifacts/sandbox/exhaustive_detail_mincredit05.csv --mode both
python -m experiments.run_experiment_16_coupled_exhaustive --limit 3
python -m experiments.run_experiment_16_coupled_exhaustive
python -m experiments.run_experiment_18_failure_memory_trajectory
python -m experiments.run_experiment_19_skill_abstraction
python -m experiments.run_experiment_17_ecs_structure_ablation
python -m experiments.run_experiment_21_operator_headroom --ecs-detail artifacts/ecs_structure_ablation_detail.csv --out artifacts/sandbox/operator_headroom_detail.csv
python -m experiments.run_experiment_22_operator_transfer --operator-bank artifacts/sandbox/operator_headroom_detail.csv --out artifacts/sandbox/operator_transfer_detail_run1.csv
python -m experiments.run_experiment_22_operator_transfer --operator-bank artifacts/sandbox/operator_headroom_detail.csv --out artifacts/sandbox/operator_transfer_detail_run2.csv --random-seed 23
python -m experiments.run_experiment_22_operator_transfer --operator-bank artifacts/sandbox/operator_headroom_detail.csv --out artifacts/sandbox/operator_transfer_detail_run3.csv --random-seed 24
python -m experiments.analyze_operator_transfer --csv artifacts/sandbox/operator_transfer_detail_run1.csv artifacts/sandbox/operator_transfer_detail_run2.csv artifacts/sandbox/operator_transfer_detail_run3.csv
```

## Observational arenas

Validate all three immutable streams without model calls:

```bash
python -m experiments.run_arena_memtrace --validate-only
python -m experiments.run_arena_memfail --validate-only
python -m experiments.run_arena_stale --validate-only
```

Live execution uses the concrete vLLM/OpenAI-compatible dual-score backend:

```bash
export LLM_BASE_URL=http://localhost:8001/v1
export LLM_MODEL=llama-3.1-8b-instruct
export LLM_JUDGE_BASE_URL=http://localhost:8000/v1
export LLM_JUDGE_MODEL=qwen2.5-7b-instruct
python -m experiments.run_arena_memtrace --case-workers 32
```

`experiments.arena_backends:create_vllm_backend` is the default factory. Its
runtime selection signal is scored by the answerer endpoint; the frozen judge
endpoint is reserved for shadow evaluation. Production validation rejects a
shared selection/evaluation client or the same configured model identity,
because that would optimize and evaluate the same judge. Each generated answer
is scored for grounding,
relevance, completeness, and internal consistency using only query + candidate
context + answer. Arena v2 requests score-token logprobs and uses the rubric
expectation on a continuous `[0, 1]` scale; endpoints that strip logprobs fall
back to the discrete JSON score. Every legal operator in the four physical
action families plus the two gated item-level families is evaluated. Finite
positive gains are retained in descending order until their additive sum
reaches `--saturation-threshold` (default `0.8`); zero, negative, and non-finite
gains are never retained. `--candidate-limit` exists only as an explicit
diagnostic cap and defaults to no cap.

The zero-call hook is evaluated while cases are loaded. `Fill` cases are
serialized as explicit routed abstentions and excluded from CMD selection-rate
denominators; they do not silently become failed no-repair cases. Measure the
split before a GPU run with:

```bash
python -m experiments.run_arena_memtrace --limit 50 --validate-only
```

Validation prints the exact source-file SHA-256 plus hashes of the ordered
selected case IDs and the full derived `ArenaCase` stream. The same values,
source byte size, resolved source path, and fingerprint schema version are
stored in `arena_manifest`. Analysis rejects artifacts without this provenance,
rejects an artifact whose serialized case IDs do not match its manifest, and,
when the source path is still mounted, re-hashes the current source bytes.

`--case-workers` (or `CMD_CASE_WORKERS`) enables cross-case concurrency for
stateless runs while preserving input order in artifacts. Values above one are
rejected with `--deposit-after` or `--perturb-after`, since those interventions
change the candidate set seen by later cases.

`--best-of-n-control` adds the compute-aligned structural control. For each Fix
case it sets `N` to CMD's actual non-baseline answer attempts after successful
cache reuse, as observed by backend counters, and gives the generic arm the
same `N` answer calls and `N` reference-free selection calls, and exposes the
**information superset** `origin_context + all candidate_items` without hook
routing, physical action operators, or item-gate labels. CMD sees an
action-specific repaired context instead. Thus the control is not
information-starved: it has at least the raw information available to any CMD
candidate and matched selection compute. A shared cached baseline answer and
baseline score are excluded from both budgets: `N` counts only actual
non-baseline CMD candidate answer attempts, so the control receives exactly
`N` candidate answer attempts and `N` selection-score attempts. The frozen
evaluation judge scores only after selection.
`arena_arm_comparison_event` records both budgets and
`experiments.analyze_arena_results` writes `cmd_vs_best_of_n.csv` plus
`cmd_vs_best_of_n_by_budget.csv`. The headline paired shadow-gain delta includes
only finite, budget-aligned pairs; the table also reports `n_paired`,
control failures, CMD/control abstentions, budget mismatches, and the `N`
distribution. `N=1` is marked as a non-selection stratum and must not be pooled
silently with `N>=2`. Additive saturation remains a separate ecology diagnostic
and is not used as the arm outcome.

The live CLI prints `arm_comparison_coverage_rate`,
`fix_cases_without_arm_comparison`, `budget_aligned_rate`,
`budget_aligned_pairs`, and `cmd_budget_source_distribution`. The 50-case
endpoint smoke is accepted only when the source distribution is
`backend_call_counters` and the alignment rate is reported alongside pair
coverage; fixture-only `logical_fallback` results cannot satisfy this
preflight.

The two arms intentionally pass different contexts to the reference-free
selection scorer (clean repaired context versus the control's larger
information superset), so grounding scores are not directly comparable across
arms. They are used only for within-arm choice. Cross-arm claims use the frozen
shadow evaluator, which neutralizes that selection-rubric context asymmetry.

Interpretation is frozen before live results: if CMD has a positive paired
shadow-gain delta over best-of-N, the supported claim is “directed structure
helps under matched selection compute.” A statistical tie or practically
negligible delta means “directed repair is approximately equal to undirected
search” and does **not** support a structural-superiority claim. A negative
delta means the structure hurts. No interpretation may omit pair coverage,
failure/abstention counts, budget strata, or the `N=1` share.

The shadow scorer then compares the same answers with `gold_answer`; shadow
values are materialized after runtime values and never enter candidate
selection. The additive sum is an independent-gain coverage diagnostic, not a
claim that the selected operators have already been composed. Joint effects
still require the chain path and held-out validation.

An alternative `--backend-factory module:factory` may be supplied. The factory
receives `cases=<tuple[ArenaCase, ...]>` and `args=<Namespace>` and must return
a `DualScoreArenaBackend` with:

- `runtime_uses_gold = False`;
- a named gold-free runtime signal;
- a separately named shadow-gold signal;
- `candidates(case)` and `evaluate(case, candidate, input_context,
  origin_context)` methods;
- `deposit_composite(event)` when `--deposit-after` is enabled.

The runner still refuses the existing `LiveEvolutionBackend` as-is because
that backend's net-gain calculation reads `gold_answer`. Re-labeling that score
as gold-free would invalidate experiment A.

Run the separate perturbation observation after the baseline arena:

```bash
python -m experiments.run_arena_memtrace \
  --output artifacts/arena/memtrace_keystone_removal.jsonl \
  --perturb-after 0.25 \
  --perturb-strategy keystone
```

The selected keystone skill is removed only after the trigger case. Recovery
uses the leading retained contributor as the perturbation stream and requires
two adjacent non-empty windows below the configured JSD threshold; windows
without a positive contributor are recorded as collapse, not recovery.

`--deposit-after 0.5` is deliberately one-shot. It materializes one supported
chain and calls `deposit_composite(event)` so the staged composite can enter
subsequent retrieval. Periodic deposition would be a different intervention
and is left for future work. A composite is classified by its terminal repair
family, making replacement versus complementarity with the terminal skill
directly observable.

After the arena files exist:

```bash
python -m experiments.analyze_arena_results \
  --inputs \
    artifacts/arena/memtrace_observations.jsonl \
    artifacts/arena/memfail_observations.jsonl \
    artifacts/arena/stale_observations.jsonl
```

This produces descriptive signal, saturation, per-skill contribution, niche,
succession, co-activation, chain spectrum, directionality, and cross-arena
reproducibility tables. It performs no hypothesis tests and does not turn
structural smoke scores into recovery claims.

## Legacy runners

Older runners remain in the package for diagnostics and appendix evidence. They should not be cited as headline classification experiments unless `EXPERIMENT.md` explicitly promotes them back into the claim chain.
