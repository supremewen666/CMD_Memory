# CMD v0.3 Development Experiment Report

DEVELOPMENT_STRUCTURAL_ONLY: safety values are structural proxies, not sealed confirmatory truth.

## All-Arms Ranking
| model_id | arm | stream_macro_utility | event_weighted_utility | mean_pseudo_regret | mean_locality_cost |
| --- | --- | --- | --- | --- | --- |
| Meta-Llama-3.1-8B-Instruct | mix_ghost | 0.722197 | 0.714136 | 0.036279 | 2.767442 |
| Meta-Llama-3.1-8B-Instruct | best_global | 0.669493 | 0.655075 | 0.095341 | 3.41196 |
| Meta-Llama-3.1-8B-Instruct | ghost_hierarchy | 0.635174 | 0.613563 | 0.136852 | 2.299003 |
| Meta-Llama-3.1-8B-Instruct | global_thompson | 0.605589 | 0.59936 | 0.151055 | 2.481728 |
| Meta-Llama-3.1-8B-Instruct | contextual_bandit | 0.596168 | 0.570424 | 0.179992 | 2.112957 |
| Meta-Llama-3.1-8B-Instruct | niche_thompson | 0.566234 | 0.590091 | 0.160324 | 2.41196 |
| Meta-Llama-3.1-8B-Instruct | random_legal | 0.543564 | 0.54799 | 0.202425 | 2.152824 |
| Qwen3-14B | mix_ghost | 0.722028 | 0.718505 | 0.031462 | 2.956811 |
| Qwen3-14B | best_global | 0.669493 | 0.655075 | 0.094892 | 3.41196 |
| Qwen3-14B | ghost_hierarchy | 0.644061 | 0.650839 | 0.099128 | 2.820598 |
| Qwen3-14B | global_thompson | 0.605589 | 0.59936 | 0.150606 | 2.481728 |
| Qwen3-14B | contextual_bandit | 0.596168 | 0.570424 | 0.179543 | 2.112957 |
| Qwen3-14B | niche_thompson | 0.566234 | 0.590091 | 0.159875 | 2.41196 |
| Qwen3-14B | random_legal | 0.543564 | 0.54799 | 0.201977 | 2.152824 |

## All-Arms Family-Blocked Pairwise Effects
| model_id | comparison | family_macro_mean | ci95 | positive_family_rate | inference |
| --- | --- | --- | --- | --- | --- |
| Meta-Llama-3.1-8B-Instruct | mix_ghost>best_global | 0.058434 | 0.043681, 0.073342 | 0.9183673469387755 | POSITIVE |
| Meta-Llama-3.1-8B-Instruct | mix_ghost>contextual_bandit | 0.140376 | 0.110702, 0.17028 | 0.8775510204081632 | POSITIVE |
| Meta-Llama-3.1-8B-Instruct | mix_ghost>ghost_hierarchy | 0.107573 | 0.08567, 0.132101 | 0.9591836734693877 | POSITIVE |
| Meta-Llama-3.1-8B-Instruct | mix_ghost>global_thompson | 0.119168 | 0.092902, 0.147159 | 0.9591836734693877 | POSITIVE |
| Meta-Llama-3.1-8B-Instruct | mix_ghost>niche_thompson | 0.139027 | 0.107025, 0.171858 | 0.8367346938775511 | POSITIVE |
| Meta-Llama-3.1-8B-Instruct | mix_ghost>random_legal | 0.172867 | 0.149006, 0.197996 | 0.9591836734693877 | POSITIVE |
| Qwen3-14B | mix_ghost>best_global | 0.06096 | 0.043652, 0.07854 | 0.8367346938775511 | POSITIVE |
| Qwen3-14B | mix_ghost>contextual_bandit | 0.142902 | 0.113904, 0.17336 | 0.9183673469387755 | POSITIVE |
| Qwen3-14B | mix_ghost>ghost_hierarchy | 0.085952 | 0.055414, 0.117949 | 0.6530612244897959 | POSITIVE |
| Qwen3-14B | mix_ghost>global_thompson | 0.121694 | 0.093788, 0.151139 | 0.8979591836734694 | POSITIVE |
| Qwen3-14B | mix_ghost>niche_thompson | 0.141552 | 0.109094, 0.173269 | 0.8571428571428571 | POSITIVE |
| Qwen3-14B | mix_ghost>random_legal | 0.175392 | 0.149717, 0.201764 | 0.9591836734693877 | POSITIVE |

## ABA Family-Blocked Effects
| phase | comparison | family_macro_mean | ci95 | positive_family_rate | inference |
| --- | --- | --- | --- | --- | --- |
| recurring_a_stationary | mix_ghost>ghost_hierarchy | 0.0 | -0.010086, 0.010086 | 0.034482758620689655 | MIXED_OR_NULL |
| recurring_b_abrupt | mix_ghost>ghost_hierarchy | 0.000139 | -0.075556, 0.063333 | 0.1111111111111111 | MIXED_OR_NULL |
| recurring_a_return_stationary | mix_ghost>ghost_hierarchy | 0.009279 | -0.001875, 0.0225 | 0.11538461538461539 | MIXED_OR_NULL |

## ABA Operator Strategies
| arm | phase | operator_family | strategy_id | count | mean_utility | mean_locality_cost |
| --- | --- | --- | --- | --- | --- | --- |
| ghost_hierarchy | recurring_a_return_stationary | process_restore | rebuild | 22 | 0.685 | 4.0 |
| ghost_hierarchy | recurring_a_return_stationary | process_restore | targeted | 12 | 0.867292 | 1.0 |
| ghost_hierarchy | recurring_a_stationary | process_restore | rebuild | 22 | 0.685 | 4.0 |
| ghost_hierarchy | recurring_a_stationary | process_restore | targeted | 18 | 0.864722 | 1.0 |
| ghost_hierarchy | recurring_b_abrupt | poison_quarantine | quarantine_and_rebuild | 31 | 0.7 | 3.0 |
| ghost_hierarchy | recurring_b_abrupt | poison_quarantine | quarantine_only | 5 | 0.0175 | 1.0 |
| ghost_hierarchy | recurring_b_abrupt | state_supersede | cascade | 13 | 0.6925 | 3.0 |
| ghost_hierarchy | recurring_b_abrupt | state_supersede | targeted | 5 | 0.0175 | 2.0 |
| mix_ghost | recurring_a_return_stationary | process_restore | rebuild | 20 | 0.685 | 4.0 |
| mix_ghost | recurring_a_return_stationary | process_restore | targeted | 14 | 0.861786 | 1.0 |
| mix_ghost | recurring_a_stationary | process_restore | rebuild | 22 | 0.685 | 4.0 |
| mix_ghost | recurring_a_stationary | process_restore | targeted | 18 | 0.864722 | 1.0 |
| mix_ghost | recurring_b_abrupt | poison_quarantine | quarantine_and_rebuild | 32 | 0.7 | 3.0 |
| mix_ghost | recurring_b_abrupt | poison_quarantine | quarantine_only | 4 | 0.0175 | 1.0 |
| mix_ghost | recurring_b_abrupt | state_supersede | cascade | 12 | 0.6925 | 3.0 |
| mix_ghost | recurring_b_abrupt | state_supersede | targeted | 6 | 0.0175 | 2.0 |

## Incident Metrics
| model_id | incident_type | receipt_count | mean_utility | safe_repair_success_proxy | rollback_rate | mean_locality_cost |
| --- | --- | --- | --- | --- | --- | --- |
| Meta-Llama-3.1-8B-Instruct | poison | 107 | 0.716332 | 1.0 | 0.0 | 2.308411 |
| Meta-Llama-3.1-8B-Instruct | process_fault | 159 | 0.709403 | 1.0 | 0.0 | 3.132075 |
| Meta-Llama-3.1-8B-Instruct | state_drift | 35 | 0.728929 | 1.0 | 0.0 | 2.514286 |
| Qwen3-14B | poison | 107 | 0.7075 | 1.0 | 0.0 | 2.570093 |
| Qwen3-14B | process_fault | 159 | 0.72456 | 1.0 | 0.0 | 3.301887 |
| Qwen3-14B | state_drift | 35 | 0.724643 | 1.0 | 0.0 | 2.571429 |

## Source-Target Overlap Audit
| model_id | seed | stream | source_case_count | target_case_count | overlap_case_count | target_overlap_rate | held_out_status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Meta-Llama-3.1-8B-Instruct | 20260827 | halumem_abrupt_process_state_poison | 58 | 58 | 58 | 1.0 | OVERLAP_PRESENT |
| Meta-Llama-3.1-8B-Instruct | 20260827 | halumem_stationary | 58 | 58 | 58 | 1.0 | OVERLAP_PRESENT |
| Meta-Llama-3.1-8B-Instruct | 20260827 | memfail_abrupt_process_state_poison | 29 | 29 | 29 | 1.0 | OVERLAP_PRESENT |
| Meta-Llama-3.1-8B-Instruct | 20260827 | memfail_stationary | 29 | 29 | 29 | 1.0 | OVERLAP_PRESENT |
| Meta-Llama-3.1-8B-Instruct | 20260827 | memtracebench_abrupt_process_state_poison | 49 | 49 | 49 | 1.0 | OVERLAP_PRESENT |
| Meta-Llama-3.1-8B-Instruct | 20260827 | memtracebench_stationary | 49 | 49 | 49 | 1.0 | OVERLAP_PRESENT |
| Meta-Llama-3.1-8B-Instruct | 20260829 | halumem_abrupt_process_state_poison | 58 | 58 | 58 | 1.0 | OVERLAP_PRESENT |
| Meta-Llama-3.1-8B-Instruct | 20260829 | halumem_stationary | 58 | 58 | 58 | 1.0 | OVERLAP_PRESENT |
| Meta-Llama-3.1-8B-Instruct | 20260829 | memfail_abrupt_process_state_poison | 29 | 29 | 29 | 1.0 | OVERLAP_PRESENT |
| Meta-Llama-3.1-8B-Instruct | 20260829 | memfail_stationary | 29 | 29 | 29 | 1.0 | OVERLAP_PRESENT |
| Meta-Llama-3.1-8B-Instruct | 20260829 | memtracebench_abrupt_process_state_poison | 49 | 49 | 49 | 1.0 | OVERLAP_PRESENT |
| Meta-Llama-3.1-8B-Instruct | 20260829 | memtracebench_stationary | 49 | 49 | 49 | 1.0 | OVERLAP_PRESENT |
| Qwen3-14B | 20260827 | halumem_abrupt_process_state_poison | 58 | 58 | 58 | 1.0 | OVERLAP_PRESENT |
| Qwen3-14B | 20260827 | halumem_stationary | 58 | 58 | 58 | 1.0 | OVERLAP_PRESENT |
| Qwen3-14B | 20260827 | memfail_abrupt_process_state_poison | 29 | 29 | 29 | 1.0 | OVERLAP_PRESENT |
| Qwen3-14B | 20260827 | memfail_stationary | 29 | 29 | 29 | 1.0 | OVERLAP_PRESENT |
| Qwen3-14B | 20260827 | memtracebench_abrupt_process_state_poison | 49 | 49 | 49 | 1.0 | OVERLAP_PRESENT |
| Qwen3-14B | 20260827 | memtracebench_stationary | 49 | 49 | 49 | 1.0 | OVERLAP_PRESENT |
| Qwen3-14B | 20260829 | halumem_abrupt_process_state_poison | 58 | 58 | 58 | 1.0 | OVERLAP_PRESENT |
| Qwen3-14B | 20260829 | halumem_stationary | 58 | 58 | 58 | 1.0 | OVERLAP_PRESENT |
| Qwen3-14B | 20260829 | memfail_abrupt_process_state_poison | 29 | 29 | 29 | 1.0 | OVERLAP_PRESENT |
| Qwen3-14B | 20260829 | memfail_stationary | 29 | 29 | 29 | 1.0 | OVERLAP_PRESENT |
| Qwen3-14B | 20260829 | memtracebench_abrupt_process_state_poison | 49 | 49 | 49 | 1.0 | OVERLAP_PRESENT |
| Qwen3-14B | 20260829 | memtracebench_stationary | 49 | 49 | 49 | 1.0 | OVERLAP_PRESENT |

## Claim Ledger
| claim | status | boundary |
| --- | --- | --- |
| Matched ecological posterior improves over reset across Qwen3 and Llama. | SUPPORTED_DEVELOPMENT | Family-blocked confidence intervals are positive; inspect overlap audit before claiming held-out generalization. |
| Mix GHOST is the strongest matched router among the seven tested arms. | SUPPORTED_DEVELOPMENT | Use all-arms pairwise family bootstrap for uncertainty and do not claim per-stream dominance. |
| Mix GHOST preserves ecological memory across A-B-A recurrence. | SUPPORTED_DESCRIPTIVE | Phase-specific family bootstrap determines whether the return-phase advantage excludes zero. |
| Transferred posterior generalizes to unseen families. | NOT_ESTABLISHED | Family-blocked resampling does not repair source-target case overlap. |
| CMD provides confirmatory safe memory repair. | NOT_ESTABLISHED | Current feedback is development-structural; sealed receipts and real false-commit evaluation are absent. |
| CMD outperforms deployed memory systems. | NOT_ESTABLISHED | Requires controlled LightMem, LycheeMem, and Mem0 result tables. |
| The conclusions extend to an external frontier model. | NOT_ESTABLISHED | GPT-4o key-condition evaluation is absent. |
| The compact experiment matrix is complete. | COMPLETE | Cross is supplemental; ABA, Mix-only, all-arms, and family bootstrap are independently tracked. |
