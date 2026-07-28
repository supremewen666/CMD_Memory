# Submission Task Specification

This file is the reproducibility index for paper-facing claims. A claim is
`ready` only when its runner, detail artifact, paired analysis, and scope note
all exist. `implemented` means the code path is complete but the required GPU
run has not yet produced a final artifact.

| Claim | Experiment / runner | Required artifact | Statistical check | Status / scope |
|---|---|---|---|---|
| C1 | Exp14 `run_experiment_14_repair_efficacy.py` | `artifacts/sandbox/repair_efficacy_detail.csv` | Per-label recovery and label-collapse table | ready; hidden step failures only |
| C2 | Exp3/Exp9/Exp14 | Exp14 detail plus variance artifacts | Recovery fitness stability | ready; construction is gold-free, offline verification uses gold |
| C3 | Exp14 | `repair_efficacy_detail.csv` | CMD vs no-repair paired McNemar/bootstrap | ready |
| C4 | Exp14 | `repair_efficacy_detail.csv` | CMD vs random and LLM-judge paired tests | ready; headline selection claim |
| C5 | Exp15 | `prior_transfer_detail.csv` | Transfer arms vs oracle/no-repair | ready; global transfer only |
| C6 | Exp16 | `coupled_exhaustive_detail.csv` | Coupled-residual count | retired from main story |
| C7 | Exp18 | `failure_memory_trajectory_detail.csv` | Ordered trend and warm/cold cost | narrowed to warm-up reuse, not monotonic self-improvement |
| C8 | Exp17 | `ecs_structure_ablation_detail.csv` | Paired structure ablation | mechanism evidence only |
| C9 | Exp21/22/24 | operator headroom/transfer/trajectory detail CSVs | Paired headroom plus three-seed trajectory verdict | Exp21/22 ready; Exp24 GPU runs pending |
| C10 | Exp23 + STALE reverse adapter | item headroom/transfer detail and STALE pilot report | Net three-dimensional accuracy, including successful-case regressions | implementation ready; A100 item-layer revision must be synced before final run |
| C11 | Exp25 `run_experiment_25_repair_durability.py` | `repair_durability_detail.csv` and summary | write-back vs read-time/no-repair paired tests; other-family sentinel net | implemented; formal GPU run pending |

## Reproduction gates

1. Install with `python -m pip install -e '.[dev]'`.
2. Verify `python -m pytest tests/ -q`.
3. Verify dataset checksum manifests before any GPU run.
4. Freeze judge endpoint/model/rubric across arms and answering models.
5. Preserve the command line, random seed, input checksum, output checksum,
   judge identity, and model build for every paper-facing run.
6. Run Exp24 with at least three shuffle seeds.
7. Treat Exp25's `oracle_gold_cluster_replay` admission as an offline upper
   bound. It is not a deployable gold-free selector.

## C11 acceptance criteria

- Durable state must change retrieval/pipeline materialization, not append a
  textual repair marker.
- Candidate actions must pass structural legality checks.
- Admission must report replay sample count, CI/low-evidence state, operator
  hash, and dedup/cap outcome.
- The store must restore to its byte-comparable snapshot after each family.
- Cross-family sentinel measurements must report helped, hurt, and net counts.
- `analyze_significance.py` must emit paired C11 comparisons.
