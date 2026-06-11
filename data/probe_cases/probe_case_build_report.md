# Probe Case Build Report

Builder: `experiments/build_probe_cases.py`

Total automatic cases: 600
Total HITL poisoned cases: 9
Total multi-hop cases: 75
Total coupled boundary cases: 30

LOO support policy: item_wrong and item_compression_distorted support items use split fragments plus an assembly rule; no support item carries the full gold answer sentence.

## Automatic Label Counts
- `fill_null_after_load`: 60
- `granularity_error`: 60
- `graph_error`: 60
- `injection_error`: 60
- `item_compression_distorted`: 60
- `item_conflict`: 60
- `item_stale`: 60
- `item_wrong`: 60
- `retrieval_error`: 60
- `safety_error`: 60

## Multi-Hop Label Counts
- `granularity_error`: 15
- `graph_error`: 15
- `injection_error`: 15
- `retrieval_error`: 15
- `safety_error`: 15

## Coupled Pair Counts
- `granularity_error+safety_error`: 6
- `graph_error+safety_error`: 6
- `injection_error+graph_error`: 6
- `retrieval_error+granularity_error`: 6
- `retrieval_error+injection_error`: 6

## Sources
- `longmemeval`: raw=1000, auto=200, hitl_poisoned=3, multihop=25, coupled_boundary=10
- `memoryarena`: raw=2564, auto=200, hitl_poisoned=3, multihop=25, coupled_boundary=10
- `toolbench`: raw=500, auto=200, hitl_poisoned=3, multihop=25, coupled_boundary=10
