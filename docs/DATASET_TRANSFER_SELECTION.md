# Dataset transfer selection

This repository transfers only the small, source-visible fixtures needed to
reproduce the runtime protocol. The P4C addition in this change is
`experiments/fixtures/p4c_zero_call_v1.jsonl`: a closed three-mechanism scenario
overlay with no benchmark gold or label fields.

Already tracked probe fixtures, checksums, and frozen V4 packages under
`data/probe_cases` and `data/ghost_live_v2` remain unchanged.

The following stay local and are not transferred:

- `data/external/**`, including acquired LongMemEval, MemFail, and Evo-Bench files;
- LongMemEval oracle and any sealed evaluator sidecar;
- `artifacts/**`, logs, checkpoints, SQLite databases, caches, and temporary locks;
- API keys and local LLM configuration files.

External sources must be reacquired with `experiments.download_datasets` and
verified against their acquisition manifests. Runtime incident selection,
repair commit, and router evolution receive only `EccSyndrome` and
`EccRepairReceipt`; offline labels never enter that path.
