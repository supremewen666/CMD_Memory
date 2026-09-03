# Routing Mechanism Ablation

Empirical regret is relative to the best realized arm on each paired event; safety is a structural proxy unless reports use a sealed feedback provider.

## Meta-Llama-3.1-8B-Instruct

| Arm | Utility | Regret | Override | Negative override |
|---|---:|---:|---:|---:|
| routing_frozen_backbone | 0.651146 | 0.091055 | 0.0000 | NA |
| routing_global | 0.689086 | 0.053115 | 0.4419 | 0.1352 |
| routing_global_pattern | 0.692616 | 0.049585 | 0.4120 | 0.0861 |
| routing_global_pattern_local | 0.703297 | 0.038904 | 0.4319 | 0.0746 |
| routing_full_no_support_gate | 0.683679 | 0.058522 | 0.6910 | 0.1453 |
| mix_ghost | 0.714136 | 0.028065 | 0.4585 | 0.0313 |

| Mechanism | Event-weighted utility delta |
|---|---:|
| global_residual | 0.037940 |
| pattern_residual | 0.003530 |
| local_residual | 0.010681 |
| support_gate | 0.030457 |
| full_router | 0.062990 |

## Qwen3-14B

| Arm | Utility | Regret | Override | Negative override |
|---|---:|---:|---:|---:|
| routing_frozen_backbone | 0.639576 | 0.086379 | 0.0000 | NA |
| routing_global | 0.716553 | 0.009402 | 0.5183 | 0.0611 |
| routing_global_pattern | 0.716694 | 0.009261 | 0.3455 | 0.0852 |
| routing_global_pattern_local | 0.720997 | 0.004958 | 0.3688 | 0.0644 |
| routing_full_no_support_gate | 0.715025 | 0.010930 | 0.5282 | 0.0588 |
| mix_ghost | 0.720772 | 0.005183 | 0.4718 | 0.0482 |

| Mechanism | Event-weighted utility delta |
|---|---:|
| global_residual | 0.076977 |
| pattern_residual | 0.000141 |
| local_residual | 0.004302 |
| support_gate | 0.005748 |
| full_router | 0.081196 |
