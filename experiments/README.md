# Experiment Runners

This package contains the runnable evidence for `EXPERIMENT.md`. The active paper path is claim-centric, not numbered-section-centric.

## Frontline runners

| Claim | Runner | Use |
|---|---|---|
| C3-C4 | `python -m experiments.run_experiment_14_repair_efficacy` | Four-arm repair efficacy: no-repair / random / LLM judge / CMD. |
| C2-C5 | `python -m experiments.probe_exhaustive` | Full single-point oracle; produces the prior bank for transfer. |
| C5 | `python -m experiments.run_experiment_15_prior_transfer` | Leave-one-out prior transfer from offline culprits to online top-K seeds. |
| C6 | `python -m experiments.run_experiment_16_coupled_exhaustive` | MCTS existence decider for coupled `b^d` residuals. |
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
export LLM_MODEL=qwen2.5-7b-instruct
export LLM_JUDGE_BASE_URL=http://localhost:8000/v1
export LLM_JUDGE_MODEL=qwen2.5-7b-instruct
python -m experiments.run_arena_memtrace
```

`experiments.arena_backends:create_vllm_backend` is the default factory. Its
runtime signal independently scores each generated answer for grounding,
relevance, completeness, and internal consistency using only query + candidate
context + answer. The shadow scorer then compares that same answer with
`gold_answer`; shadow values are materialized after runtime values and never
enter candidate selection.

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

The selected skill is removed only after the trigger case. Recovery requires
two adjacent non-empty winner windows below the configured JSD threshold;
all-abstain windows are recorded as collapse, not recovery.

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

This produces descriptive signal, niche, succession, co-activation, chain
spectrum, directionality, and cross-arena reproducibility tables. It performs
no hypothesis tests and does not turn structural smoke scores into recovery
claims.

## Legacy runners

Older runners remain in the package for diagnostics and appendix evidence. They should not be cited as headline classification experiments unless `EXPERIMENT.md` explicitly promotes them back into the claim chain.
