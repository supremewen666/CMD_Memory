# Baseline Results — P4A no-Mem0 retrieval confirmation

## 2026-08-24 real benchmark baseline contract

The active end-to-end matrix is now BM25 control vs CMD vs full context under
the same answer model, prompt, dataset order and scoring code. LoCoMo includes
all five official categories and reports per-category scores; LongMemEval uses
the official `{question_id,hypothesis}` evaluator and reports the five ability
types. The live prediction environment is not configured in this workspace, so
no answer-quality number is claimed here yet. The completed retrieval-only
numbers below remain valid diagnostics but are not substitutes for that matrix.

| Baseline | LoCoMo | LongMemEval | Answer model | Scorer | Status |
|---|---|---|---|---|---|
| BM25 top-5 | full 1,986 QA | full 500 | frozen/shared | official post-seal | ready, not run |
| CMD, BM25 pool top-10 | full 1,986 QA | full 500 | frozen/shared | official post-seal | ready, not run |
| Full context | full 1,986 QA | S only when context fits | frozen/shared | official post-seal | ready, not run |

Prediction and selection are gold-free; reference answers are opened only by
`run_official_memory_scoring` after `prediction_seal.json` is validated.

## Evaluation Contract

- dataset/workload: LongMemEval-S (500 questions) and the official local
  MemFail expansion (492 physical rows, 692 scored prompts).
- metric: scorer-only Recall@5, MRR, scorable/unscorable count and retention;
  LongMemEval is further stratified by `question_type`, while MemFail is
  stratified by family/subtype/hop/conditional/persona proxy in each manifest.
- protocol: vanilla retrieval only. Each strategy receives the same
  deployment-visible query and `MemoryRecord` content, stable chronology,
  isolated namespace, `top_k=5`, and no answerer/judge/API/Mem0 calls. Gold is
  opened only after ranking by the dataset sidecar scorer. Thus these numbers
  are retrieval evidence, not answer quality or CMD/GHOST repair efficacy.

## Baseline Matrix

| Baseline | Source | Metric | Protocol | Status | Notes |
|---|---|---|---|---|---|
| Deterministic lexical | P3 audited in-memory lexical ordering | Recall@5, MRR | frozen P4A ABI | ran | lightweight current control |
| Okapi BM25 | stdlib Robertson/Sparck Jones, `k1=1.2,b=0.75` | Recall@5, MRR | frozen P4A ABI | ran | deterministic `memory_id` tie-break |
| all-MiniLM-L6-v2 | A-MEM documented embedding reference | Recall@5, MRR | local-only P4A ABI | unavailable | `sentence_transformers` absent; no download/substitute |
| Oracle evidence ceiling | offline scorer diagnostic | Recall@5, MRR | no retrieval context | ran | upper bound only; cannot feed P3C/router/writer |

## Baselines Attempted

| Baseline | Status | Result | Evidence Source | Notes |
|---|---|---|---|---|
| Lexical / LongMemEval-S | ran | n=500; scorable=379; Recall@5=0.844327; MRR=0.676737; retention=1.0; strategy latency=5905.668 ms; calls=0 | `artifacts/experiments/p4a_baseline_confirmation/longmemeval_s_lexical/manifest.json` | Full S, top-k 5 |
| BM25 / LongMemEval-S | ran | n=500; scorable=379; Recall@5=0.941953; MRR=0.790501; retention=1.0; strategy latency=7572.648 ms; calls=0 | `artifacts/experiments/p4a_baseline_confirmation/longmemeval_s_bm25/manifest.json` | Full S, top-k 5 |
| Lexical / LongMemEval-M | ran | n=500; scorable=379; Recall@5=0.620053; MRR=0.482982; retention=1.0; strategy latency=57495.768 ms; calls=0 | `artifacts/experiments/p4a_baseline_confirmation/longmemeval_m_lexical_full/manifest.json` | Full M, top-k 5; root-bound receipt and append-only gold-free rankings retained |
| BM25 / LongMemEval-M | ran | n=500; scorable=379; Recall@5=0.820580; MRR=0.673307; retention=1.0; strategy latency=74826.488 ms; calls=0 | `artifacts/experiments/p4a_baseline_confirmation/longmemeval_m_bm25_full/manifest.json` | Full M, top-k 5; root-bound receipt and append-only gold-free rankings retained |
| Lexical / MemFail | ran | n=692; scorable=692; Recall@5=0.536127; MRR=0.373748; retention=1.0; strategy latency=18.976 ms; calls=0 | `artifacts/experiments/p4a_baseline_confirmation/memfail_lexical/manifest.json` | Full official prompt expansion, top-k 5 |
| BM25 / MemFail | ran | n=692; scorable=692; Recall@5=0.547688; MRR=0.414427; retention=1.0; strategy latency=42.596 ms; calls=0 | `artifacts/experiments/p4a_baseline_confirmation/memfail_bm25/manifest.json` | Full official prompt expansion, top-k 5 |
| Oracle ceiling / LongMemEval-S | ran | n=500; scorable=379; Recall@5=1.0; MRR=1.0 | `artifacts/experiments/p4a_baseline_confirmation/longmemeval_s_oracle_ceiling/manifest.json` | `offline_upper_bound=true`, `prediction_context=false` |
| Oracle ceiling / MemFail | ran | n=692; scorable=692; Recall@5=1.0; MRR=1.0 | `artifacts/experiments/p4a_baseline_confirmation/memfail_oracle_ceiling/manifest.json` | scorer-only ceiling |
| MiniLM / LongMemEval-S smoke | unavailable | N/A | `artifacts/experiments/p4a_baseline_confirmation/smoke_lme_minilm/manifest.json` | exact cause: `sentence_transformers is not installed` |

Exact successful commands (run from repository root):

```bash
python -m experiments.baselines.retrieval_confirmation --dataset longmemeval-s --strategy lexical --limit 0 --top-k 5 --output artifacts/experiments/p4a_baseline_confirmation/longmemeval_s_lexical
python -m experiments.baselines.retrieval_confirmation --dataset longmemeval-s --strategy bm25 --limit 0 --top-k 5 --output artifacts/experiments/p4a_baseline_confirmation/longmemeval_s_bm25
python -m experiments.baselines.retrieval_confirmation --dataset memfail --strategy lexical --limit 0 --top-k 5 --output artifacts/experiments/p4a_baseline_confirmation/memfail_lexical
python -m experiments.baselines.retrieval_confirmation --dataset memfail --strategy bm25 --limit 0 --top-k 5 --output artifacts/experiments/p4a_baseline_confirmation/memfail_bm25
```

## Most Comparable Baseline

- baseline: stdlib Okapi BM25.
- why this is the main comparison: it uses exactly the same P3 content/time/
  namespace/top-k and has no external model, extractor, service or vector-store
  capability confound. It is stronger than the existing lexical control on both
  completed datasets, but it is still a retrieval-only vanilla baseline.

## Gaps

- LongMemEval-M is now complete for lexical/BM25. The measured strategy-only
  latency is 57.496 s / 74.826 s over 500 cases; it excludes input parsing,
  durable receipt I/O and scorer-side JSON parsing. The corresponding index
  byte totals are 2,523,268,845 / 6,122,602,637. `run_receipt.json` binds the
  dataset/oracle roots and `rankings.jsonl` is an append-only, gold-free
  exactly-once resume record. These are not end-to-end answer latency numbers.
- MiniLM: unavailable locally. To close, install the already-approved local
  dependency and place the exact 384-dimensional checkpoint in the local model
  cache, then rerun the emitted command. P4A must not download it or replace it
  with hash/TF-IDF.
- This does not compare static/CMD/GHOST: their P3 retrieval path is identical
  before repair. They belong in the later repair comparison, not duplicated
  baseline shadows.
