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

## Legacy runners

Older runners remain in the package for diagnostics and appendix evidence. They should not be cited as headline classification experiments unless `EXPERIMENT.md` explicitly promotes them back into the claim chain.
