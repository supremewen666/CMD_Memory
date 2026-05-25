---
title: Strengthen retrieval baselines and evidence scoring
labels:
  - needs-triage
type: AFK
blocked_by:
  - 0003-generate-counterfactual-attribution-table
status: ✅ done (HybridRerank removed 2026-05-19 — BM25 only; see modify.md)
user_stories:
  - 3
  - 4
  - 7
  - 10
  - 15
---

# Strengthen retrieval baselines and evidence scoring

## Parent

`prd/cmd_minimal_probe_prd.md`

## What to Build

Extend CMD-Audit after the smoke attribution path so retrieval failures are tested against real baseline retrievers, ranked evidence traces, and hard negatives. This slice is a V0.5 follow-up: it strengthens the research claim but should not block issue 0003's first six-replay attribution table.

The current baselines (`fixed_summary`, `vector_memory`) are synthetic: they read pre-computed `BaselineOutput` objects from probe case JSON with no actual retrieval logic. Issue 0008 replaces this with two real retrieval strategies (BM25 as weak baseline, HybridRerank as strong baseline) that search over `case.extracted_memory`. The contrast is the point: hard negatives verify that the stronger retriever recovers evidence the weaker retriever missed.

## Design Decisions

### Retriever count: two, not four

Only two retrievers are needed to demonstrate the core claim:

| Retriever | Role | Why |
|-----------|------|-----|
| **BM25** | Weak baseline / most interpretable | Pure keyword matching. Fails on paraphrase, synonym, same-entity confusion. Everyone understands why. |
| **HybridRerank** | Strong baseline / most interpretable strong retriever | BM25 + TF-IDF cosine hybrid retrieval, then evidence-phrase-match reranking over top-k candidates. Improves on BM25's weaknesses in a transparent way. |

Dropped: vector-alone (intermediate tier, limited analytical value), hybrid-without-rerank (transition state, no independent claim).

### Agentic search: deferred to V1

Agentic search (query rewriting, iterative refinement, tool-use-based retrieval) is explicitly out of scope for V0.5. Reasons:
- V0 is a standalone deterministic harness; agentic search requires LLM calls and introduces non-determinism.
- The retrieval baselines are comparators for CMD attribution, not the research contribution itself.
- Agentic search failures (wrong query rewrite, bad tool selection) are different failure modes that need their own taxonomy review.
- Noted as V1 candidate alongside `ingestion_error` and real memory-agent integration.

### Scoring limitations: accepted (Plan A)

`evidence_recall_from_text` uses phrase matching -- a necessary but not sufficient condition for semantic correctness. A memory item containing "Messi" can match `required_phrases: ["Messi"]` regardless of whether the semantic content is "Messi is GOAT" (correct) or "Messi is a father" (wrong). This is a known V0 limitation accepted for two reasons:

1. **Mitigated by phrase granularity**: `required_phrases` are constructed to include distinguishing terms (e.g., `["Messi", "GOAT"]`), so false-positive matches require the probe designer to deliberately weaken the phrases.
2. **Semantic evaluation belongs to V1**: When V1 integrates real LLM agents, answer scoring will be replaced by LLM-judge evaluation, and evidence scoring can be upgraded to entailment-based checks. Issue 0008's hard negatives are designed to expose this boundary as valuable experimental evidence, not to hide it.

This is recorded as **Decision 12** in `plans/cmd_open_decisions.md` and as a known V0→V1 upgrade point in `current-memory.md`.

### Scope boundary

This issue implements retrieval baselines as **baseline systems** (comparators), NOT as CMD counterfactual interventions. The existing V0 replay portfolio (Oracle Retrieval, etc.) continues to use oracle access to gold evidence. The new retrievers are blind to gold evidence during ranking; gold is used only for post-hoc trace annotation.

## Acceptance Criteria

- [ ] BM25 (weak) and HybridRerank (strong) retrieval baselines are represented as baseline systems, not as CMD counterfactual interventions. The contrast between the two demonstrates that stronger retrievers recover evidence weaker retrievers miss.
- [ ] Ranked retrieval traces include `case_id`, `run_id`, `retriever_name`, `memory_id`, `rank`, `score`, `token_cost`, `retrieved_text`, `matched_gold_evidence_units`, `is_gold_support`, and `is_distractor`.
- [ ] Retrieval metrics include Recall@k, MRR, nDCG, Precision@k, context noise ratio, and answer accuracy or F1.
- [ ] Hard negatives cover same-entity confusion, temporal conflict, paraphrase, multi-hop evidence, stale memory, and compression-loss.
- [ ] The attribution confusion matrix reports `retrieval_error` separately from `premature_extraction_error`.
- [ ] Stronger retrievers reduce genuine retrieval misses without relabeling raw-event-only recoverable cases as `retrieval_error`.
- [ ] Hard boundary enforced at `evidence_recall_from_text(gold_evidence, memory_item.text)`: stronger retrieval may flip the label to `retrieval_error` only when the Memory Item text contains the evidence and a weak retriever simply missed it. When the Memory Item text does not contain the evidence phrases at all (extraction already lost them), the label stays `premature_extraction_error`.
- [ ] V0 attribution still excludes bad-memory-item labels and deferred pipeline labels.
- [ ] All existing 175 tests continue to pass without modification.

## Implementation Plan

### New module: `cmd_audit/retrieval_baselines.py`

Two retrieval strategies forming a weak-to-strong contrast, both deterministic, both blind to gold evidence during ranking:

| Retriever | Algorithm | Role |
|-----------|-----------|------|
| `bm25` | BM25 (k1=1.2, b=0.75) over tokenized text | Weak baseline: keyword-only, fails on paraphrase and entity confusion |
| `hybrid_rerank` | BM25 + TF-IDF cosine hybrid retrieval, then evidence-phrase-match reranking over top-k candidates | Strong baseline: semantic + precise evidence alignment |

Shared tokenizer `_tokenize(text)` that lowercases, splits on non-alphanumeric, and drops tokens < 2 chars. Pure Python, no external deps.

### Data classes (frozen dataclasses)

- `RankedRetrievalTrace` -- per (retriever, rank) row
- `RetrievalMetrics` -- aggregate metrics for one retriever on one case
- `RetrievalBaselineResult` -- wraps traces + metrics + best_answer for one retriever
- `RetrievalBaselineSuiteResult` -- wraps both retriever results for one case

### Evidence boundary functions

- `enforce_retrieval_error_boundary(case, memory_item_text)` -> bool
- `compute_evidence_boundary_audit(case)` -> dict[memory_id, can_flip]

### Orchestration

- `run_retrieval_baseline_suite(case)` -> `RetrievalBaselineSuiteResult`

### New fixture: `data/probe_cases/v0_issue8_hard_negatives.json`

Six hard negative cases, all `perturbation_label: "retrieval_error"`:

| Case ID | Hard negative type | Challenge |
|---------|-------------------|-----------|
| `v0-hn-entity-001` | same-entity confusion | Multiple cities for same person, different events |
| `v0-hn-temporal-001` | temporal conflict | Original plan vs updated plan |
| `v0-hn-paraphrase-001` | paraphrase | Same fact in different wording |
| `v0-hn-multihop-001` | multi-hop evidence | Two memory items needed for full evidence |
| `v0-hn-stale-001` | stale memory | Last year vs this year |
| `v0-hn-compress-001` | compression-loss | Full detail vs lossy summary of same event |

### CSV writers (in `cmd_audit/harness.py`)

- `write_retrieval_trace_table(suite_results, output_path)` -- full ranked trace
- `write_retrieval_metrics_table(suite_results, output_path)` -- metrics comparison

### New test file: `tests/test_cmd_audit_issue8_retrieval_baselines.py`

~25 behavior-level tests across 10 test classes, following existing `unittest.TestCase` patterns.

### Public API exports (in `cmd_audit/__init__.py`)

All new data classes, retriever functions, metrics functions, evidence boundary functions, and CSV writers.

## Blocked By

- `0003-generate-counterfactual-attribution-table`

## Non-goals

- No agentic search (query rewriting, iterative refinement, tool-use retrieval) -- deferred to V1
- No external dependencies (numpy, scipy, sklearn) -- pure Python only
- No LLM-based retrieval or reranking
- No modification to existing `baselines.py`, `replays.py`, `attribution.py`, `models.py`, or `scoring.py`
- No changes to existing probe case JSON files

## Source Notes

This issue formalizes `cmd_innovation_core/strengthen_retrieval_baselines_and_evidence_scoring.md` inside the local markdown issue tracker.

## Detail Map

Implementation details recorded in `cmd_innovation_core/issues/0008-retrieval-baseline-implementation-details.md`.
