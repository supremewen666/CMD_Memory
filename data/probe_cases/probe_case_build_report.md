# Probe Case Build Report

Builder: `experiments/build_probe_cases.py`

Total automatic cases: 150
Total HITL poisoned cases: 9
Total multi-hop cases: 240
Total coupled boundary cases: 30

LOO support policy: item_wrong and item_compression_distorted support items use split fragments plus an assembly rule; no support item carries the full gold answer sentence.

## Automatic Label Counts
- `fill_null_after_load`: 15
- `granularity_error`: 18
- `injection_error`: 18
- `item_compression_distorted`: 15
- `item_conflict`: 15
- `item_stale`: 18
- `item_wrong`: 15
- `retrieval_error`: 18
- `safety_error`: 18

## Multi-Hop Label Counts
- `granularity_error`: 60
- `injection_error`: 60
- `retrieval_error`: 60
- `safety_error`: 60

## Coupled Pair Counts
- `granularity_error+safety_error`: 9
- `retrieval_error+granularity_error`: 9
- `retrieval_error+injection_error`: 12

## Sources
- `longmemeval`: raw=1000, auto=50, hitl_poisoned=3, multihop=80, coupled_boundary=10
- `memoryarena`: raw=2564, auto=50, hitl_poisoned=3, multihop=80, coupled_boundary=10
- `toolbench`: raw=500, auto=50, hitl_poisoned=3, multihop=80, coupled_boundary=10
