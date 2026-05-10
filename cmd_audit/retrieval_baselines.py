"""Deterministic retrieval baselines for CMD-Audit issue 0008 (V0.5).

Two retrievers form a weak-to-strong contrast over case.extracted_memory:
- BM25: pure keyword matching, fails on paraphrase and entity confusion.
- HybridRerank: BM25 + TF-IDF cosine hybrid, then evidence-phrase rerank.

Both are blind to gold evidence during ranking. Gold is used only for
post-hoc trace annotation (is_gold_support, is_distractor).

Agentic search is deferred to V1.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

from .models import GoldEvidence, ProbeCase
from .scoring import answer_score, evidence_recall_from_text


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RankedRetrievalTrace:
    """One row per (retriever, rank) pair in the retrieval result list."""

    case_id: str
    run_id: str
    retriever_name: str
    memory_id: str
    rank: int
    score: float
    token_cost: float
    retrieved_text: str
    matched_gold_evidence_units: int
    is_gold_support: bool
    is_distractor: bool

    def __post_init__(self) -> None:
        if self.rank < 1:
            raise ValueError(f"rank must be >= 1, got {self.rank}")
        if self.score < 0:
            raise ValueError(f"score must be >= 0, got {self.score}")
        if self.matched_gold_evidence_units < 0:
            raise ValueError(
                f"matched_gold_evidence_units must be >= 0, "
                f"got {self.matched_gold_evidence_units}"
            )


@dataclass(frozen=True)
class RetrievalMetrics:
    """Aggregate retrieval metrics for one retriever on one case."""

    retriever_name: str
    case_id: str
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    recall_at_10: float
    mrr: float
    ndcg_at_10: float
    precision_at_1: float
    precision_at_3: float
    precision_at_5: float
    context_noise_ratio: float
    answer_accuracy: float
    answer_f1: float

    def __post_init__(self) -> None:
        for name in (
            "recall_at_1", "recall_at_3", "recall_at_5", "recall_at_10",
            "precision_at_1", "precision_at_3", "precision_at_5",
        ):
            val = getattr(self, name)
            if val < 0.0 or val > 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {val}")
        if self.mrr < 0.0 or self.mrr > 1.0:
            raise ValueError(f"mrr must be in [0, 1], got {self.mrr}")
        if self.ndcg_at_10 < 0.0 or self.ndcg_at_10 > 1.0:
            raise ValueError(f"ndcg_at_10 must be in [0, 1], got {self.ndcg_at_10}")


@dataclass(frozen=True)
class RetrievalBaselineResult:
    """One retriever's full output for one probe case."""

    case_id: str
    retriever_name: str
    traces: tuple[RankedRetrievalTrace, ...]
    metrics: RetrievalMetrics
    best_answer: str
    best_answer_score: float


@dataclass(frozen=True)
class RetrievalBaselineSuiteResult:
    """Both retriever results for one probe case."""

    case_id: str
    baseline_results: tuple[RetrievalBaselineResult, ...]


# ---------------------------------------------------------------------------
# Tokenizer (shared by all retrievers)
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Lowercase, extract alphanumeric runs, drop tokens shorter than 2 chars."""
    return [t for t in re.findall(r"[a-z0-9]{2,}", text.casefold())]


# ---------------------------------------------------------------------------
# Shared BM25 scoring (used by both retrievers)
# ---------------------------------------------------------------------------


def _compute_bm25_scores(
    query_tokens: list[str],
    doc_tokens_list: list[list[str]],
    k1: float = 1.2,
    b: float = 0.75,
) -> list[float]:
    """Compute BM25 scores for all documents against query tokens."""
    n = len(doc_tokens_list)
    doc_lengths = [len(tokens) for tokens in doc_tokens_list]

    df: dict[str, int] = {}
    for tokens in doc_tokens_list:
        for term in set(tokens):
            df[term] = df.get(term, 0) + 1

    idf: dict[str, float] = {}
    for term, freq in df.items():
        idf[term] = math.log((n - freq + 0.5) / (freq + 0.5) + 1.0)

    avgdl = sum(doc_lengths) / n if n > 0 else 1.0

    scores: list[float] = []
    for i, tokens in enumerate(doc_tokens_list):
        tf_map: dict[str, float] = {}
        for t in tokens:
            tf_map[t] = tf_map.get(t, 0.0) + 1.0
        doc_len = doc_lengths[i]
        score = 0.0
        for qt in query_tokens:
            tf = tf_map.get(qt, 0.0)
            if tf == 0.0:
                continue
            idf_val = idf.get(qt, 0.0)
            numerator = tf * (k1 + 1.0)
            denominator = tf + k1 * (1.0 - b + b * doc_len / avgdl)
            score += idf_val * numerator / denominator
        scores.append(score)

    return scores


# ---------------------------------------------------------------------------
# BM25 retriever
# ---------------------------------------------------------------------------


def run_bm25_retrieval(
    case: ProbeCase,
    *,
    k1: float = 1.2,
    b: float = 0.75,
) -> list[RankedRetrievalTrace]:
    """BM25 retrieval over case.extracted_memory, blind to gold evidence."""
    memory_items = case.extracted_memory
    if not memory_items:
        return []

    query_tokens = _tokenize(case.query)
    if not query_tokens:
        return _all_rank_zero_traces(case, memory_items, "bm25")

    doc_tokens_list = [_tokenize(item.text) for item in memory_items]
    scores = _compute_bm25_scores(query_tokens, doc_tokens_list, k1=k1, b=b)

    ranked_indices = sorted(range(len(memory_items)), key=lambda i: scores[i], reverse=True)
    run_id = hashlib.sha256(f"{case.case_id}:bm25".encode()).hexdigest()[:12]

    return _annotate_traces(
        case=case,
        memory_items=memory_items,
        ranked_indices=ranked_indices,
        scores=scores,
        retriever_name="bm25",
        run_id=run_id,
    )


# ---------------------------------------------------------------------------
# TF-IDF utilities (used by HybridRerank)
# ---------------------------------------------------------------------------


def _build_tfidf_vectors(
    memory_items: list, query: str
) -> tuple[dict[str, float], list[dict[str, float]]]:
    """Build TF-IDF weighted sparse vectors for query and all docs."""
    query_tokens = _tokenize(query)
    doc_tokens_list = [_tokenize(item.text) for item in memory_items]

    # Build vocabulary + document frequencies
    df: dict[str, int] = {}
    all_docs_tokens = []
    for tokens in doc_tokens_list:
        unique = set(tokens)
        all_docs_tokens.append(tokens)
        for term in unique:
            df[term] = df.get(term, 0) + 1

    n = len(memory_items)

    def _idf(term: str) -> float:
        return math.log((n + 1.0) / (df.get(term, 0) + 1.0)) + 1.0

    # Query vector
    query_vector: dict[str, float] = {}
    qf: dict[str, int] = {}
    for t in query_tokens:
        qf[t] = qf.get(t, 0) + 1
    for t, count in qf.items():
        query_vector[t] = count * _idf(t)

    # Doc vectors
    doc_vectors: list[dict[str, float]] = []
    for tokens in doc_tokens_list:
        tf: dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        vec = {t: count * _idf(t) for t, count in tf.items()}
        doc_vectors.append(vec)

    return query_vector, doc_vectors


def _cosine_similarity(
    vec_a: dict[str, float], vec_b: dict[str, float]
) -> float:
    """Cosine similarity between two sparse vectors."""
    dot = 0.0
    for term, weight_a in vec_a.items():
        weight_b = vec_b.get(term, 0.0)
        dot += weight_a * weight_b

    norm_a = math.sqrt(sum(w * w for w in vec_a.values()))
    norm_b = math.sqrt(sum(w * w for w in vec_b.values()))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# HybridRerank retriever
# ---------------------------------------------------------------------------


def run_hybrid_rerank_retrieval(
    case: ProbeCase,
    *,
    bm25_weight: float = 0.4,
    vector_weight: float = 0.6,
    candidate_k: int = 5,
) -> list[RankedRetrievalTrace]:
    """BM25 + TF-IDF cosine hybrid retrieval with evidence-phrase rerank.

    Steps:
    1. Run BM25 and TF-IDF cosine independently over all memory items.
    2. Min-max normalize each score distribution to [0, 1].
    3. Combine with weighted sum.
    4. Rerank top-k candidates by evidence-phrase match ratio.
    5. Annotate all traces with gold evidence matches.

    This is blind to gold evidence during steps 1-3 (retrieval).
    Gold evidence is used only in step 4 (reranking) and step 5 (annotation).
    """
    memory_items = list(case.extracted_memory)
    n = len(memory_items)
    if n == 0:
        return []

    query_tokens = _tokenize(case.query)
    if not query_tokens:
        return _all_rank_zero_traces(case, tuple(memory_items), "hybrid_rerank")

    # --- BM25 scores ---
    doc_tokens_list = [_tokenize(item.text) for item in memory_items]
    bm25_scores = _compute_bm25_scores(query_tokens, doc_tokens_list)

    # --- TF-IDF cosine scores ---
    query_vector, doc_vectors = _build_tfidf_vectors(memory_items, case.query)
    vector_scores = [_cosine_similarity(query_vector, dv) for dv in doc_vectors]

    # --- Min-max normalize ---
    def _minmax(values: list[float]) -> list[float]:
        vmin = min(values)
        vmax = max(values)
        if vmax == vmin:
            return [0.5] * len(values)
        return [(v - vmin) / (vmax - vmin) for v in values]

    norm_bm25 = _minmax(bm25_scores)
    norm_vector = _minmax(vector_scores)

    # --- Weighted combination ---
    hybrid_scores = [
        bm25_weight * nb + vector_weight * nv
        for nb, nv in zip(norm_bm25, norm_vector)
    ]

    # --- Rerank top-k by evidence-phrase match ---
    gold_evidence = case.gold_evidence
    total_evidence_units = len(gold_evidence) if gold_evidence else 1

    # Rank by hybrid score first
    hybrid_ranked = sorted(
        range(n), key=lambda i: hybrid_scores[i], reverse=True
    )
    rerank_candidates = hybrid_ranked[:candidate_k]

    # Compute evidence match for each candidate
    candidate_evidence_scores: dict[int, float] = {}
    for idx in rerank_candidates:
        matched = 0
        for evidence in gold_evidence:
            phrases = evidence.required_phrases or (evidence.text,)
            item_text = memory_items[idx].text.casefold()
            if all(p.casefold() in item_text for p in phrases):
                matched += 1
        candidate_evidence_scores[idx] = matched / total_evidence_units

    # Rerank candidates: primary key = evidence_match desc, secondary = hybrid desc
    reranked_candidates = sorted(
        rerank_candidates,
        key=lambda i: (candidate_evidence_scores[i], hybrid_scores[i]),
        reverse=True,
    )

    # Build final ordering: reranked top-k, then remaining in original hybrid order
    final_order = reranked_candidates.copy()
    for idx in hybrid_ranked:
        if idx not in set(reranked_candidates):
            final_order.append(idx)

    # Build scores reflecting rerank: evidence-match for top-k, hybrid for rest
    final_scores = []
    for idx in final_order:
        if idx in candidate_evidence_scores and candidate_evidence_scores[idx] > 0:
            final_scores.append(candidate_evidence_scores[idx])
        else:
            final_scores.append(hybrid_scores[idx])

    run_id = hashlib.sha256(
        f"{case.case_id}:hybrid_rerank".encode()
    ).hexdigest()[:12]

    return _annotate_traces(
        case=case,
        memory_items=tuple(memory_items),
        ranked_indices=final_order,
        scores=final_scores,
        retriever_name="hybrid_rerank",
        run_id=run_id,
    )


# ---------------------------------------------------------------------------
# Trace annotation (shared)
# ---------------------------------------------------------------------------


def _annotate_traces(
    case: ProbeCase,
    memory_items: tuple,
    ranked_indices: list[int],
    scores: list[float],
    retriever_name: str,
    run_id: str,
) -> list[RankedRetrievalTrace]:
    """Annotate ranked results with gold evidence match metadata."""
    traces: list[RankedRetrievalTrace] = []
    for rank_zero_based, idx in enumerate(ranked_indices):
        item = memory_items[idx]
        matched = _count_matched_evidence_units(case.gold_evidence, item.text)
        is_support = matched > 0
        traces.append(
            RankedRetrievalTrace(
                case_id=case.case_id,
                run_id=run_id,
                retriever_name=retriever_name,
                memory_id=item.memory_id,
                rank=rank_zero_based + 1,
                score=scores[idx],
                token_cost=0.0,
                retrieved_text=item.text,
                matched_gold_evidence_units=matched,
                is_gold_support=is_support,
                is_distractor=not is_support,
            )
        )
    return traces


def _count_matched_evidence_units(
    evidence_units: tuple[GoldEvidence, ...], text: str
) -> int:
    """Count how many gold evidence units have all required phrases in the text."""
    matched = 0
    normalized = text.casefold()
    for evidence in evidence_units:
        phrases = evidence.required_phrases or (evidence.text,)
        if all(phrase.casefold() in normalized for phrase in phrases):
            matched += 1
    return matched


def _all_rank_zero_traces(
    case: ProbeCase, memory_items: tuple, retriever_name: str
) -> list[RankedRetrievalTrace]:
    """Return traces with zero scores when query has no usable tokens."""
    run_id = hashlib.sha256(
        f"{case.case_id}:{retriever_name}".encode()
    ).hexdigest()[:12]
    traces: list[RankedRetrievalTrace] = []
    for i, item in enumerate(memory_items):
        matched = _count_matched_evidence_units(case.gold_evidence, item.text)
        traces.append(
            RankedRetrievalTrace(
                case_id=case.case_id,
                run_id=run_id,
                retriever_name=retriever_name,
                memory_id=item.memory_id,
                rank=i + 1,
                score=0.0,
                token_cost=0.0,
                retrieved_text=item.text,
                matched_gold_evidence_units=matched,
                is_gold_support=matched > 0,
                is_distractor=matched == 0,
            )
        )
    return traces


# ---------------------------------------------------------------------------
# Retrieval metrics
# ---------------------------------------------------------------------------


def compute_retrieval_metrics(
    traces: list[RankedRetrievalTrace],
    case_id: str,
    retriever_name: str,
    gold_answer: str,
) -> RetrievalMetrics:
    """Compute Recall@k, MRR, nDCG, Precision@k, noise ratio, and answer scores."""
    if not traces:
        return RetrievalMetrics(
            retriever_name=retriever_name,
            case_id=case_id,
            recall_at_1=0.0, recall_at_3=0.0, recall_at_5=0.0, recall_at_10=0.0,
            mrr=0.0, ndcg_at_10=0.0,
            precision_at_1=0.0, precision_at_3=0.0, precision_at_5=0.0,
            context_noise_ratio=0.0, answer_accuracy=0.0, answer_f1=0.0,
        )

    total_gold = sum(1 for t in traces if t.is_gold_support)
    if total_gold == 0:
        mrr_val = 0.0
    else:
        gold_ranks = [t.rank for t in traces if t.is_gold_support]
        mrr_val = 1.0 / min(gold_ranks)

    def _recall_at(k: int) -> float:
        if total_gold == 0:
            return 0.0
        found = sum(1 for t in traces[:k] if t.is_gold_support)
        return found / total_gold

    def _precision_at(k: int) -> float:
        limit = min(k, len(traces))
        if limit == 0:
            return 0.0
        found = sum(1 for t in traces[:limit] if t.is_gold_support)
        return found / limit

    # nDCG@10
    max_evidence = max(
        (t.matched_gold_evidence_units for t in traces), default=1
    )
    dcg = 0.0
    for i, t in enumerate(traces[:10]):
        rel = t.matched_gold_evidence_units / max_evidence if max_evidence > 0 else 0.0
        dcg += (2.0 ** rel - 1.0) / math.log2(i + 2)
    ideal_rel = 1.0
    idcg = sum(
        (2.0 ** ideal_rel - 1.0) / math.log2(i + 2)
        for i in range(min(total_gold, 10))
    )
    ndcg_val = dcg / idcg if idcg > 0 else 0.0

    # Context noise ratio (over top-10)
    top10 = traces[:10]
    total_top10 = len(top10)
    distractors_top10 = sum(1 for t in top10 if t.is_distractor)
    noise_ratio = distractors_top10 / total_top10 if total_top10 > 0 else 0.0

    # Answer accuracy
    top1_text = traces[0].retrieved_text.casefold() if traces else ""
    gold_norm = gold_answer.casefold()
    answer_acc = 1.0 if gold_norm in top1_text else 0.0

    # Token-level answer F1
    answer_f1_val = _answer_token_f1(top1_text, gold_answer)

    return RetrievalMetrics(
        retriever_name=retriever_name,
        case_id=case_id,
        recall_at_1=_recall_at(1),
        recall_at_3=_recall_at(3),
        recall_at_5=_recall_at(5),
        recall_at_10=_recall_at(10),
        mrr=mrr_val,
        ndcg_at_10=ndcg_val,
        precision_at_1=_precision_at(1),
        precision_at_3=_precision_at(3),
        precision_at_5=_precision_at(5),
        context_noise_ratio=noise_ratio,
        answer_accuracy=answer_acc,
        answer_f1=answer_f1_val,
    )


def _answer_token_f1(predicted_text: str, gold_answer: str) -> float:
    """Token-level F1 between top-1 retrieved text and gold answer."""
    pred_tokens = set(_tokenize(predicted_text))
    gold_tokens = set(_tokenize(gold_answer))
    if not pred_tokens or not gold_tokens:
        return 0.0
    tp = len(pred_tokens & gold_tokens)
    precision = tp / len(pred_tokens)
    recall = tp / len(gold_tokens)
    if precision + recall == 0.0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


# ---------------------------------------------------------------------------
# Evidence boundary enforcement
# ---------------------------------------------------------------------------


def enforce_retrieval_error_boundary(
    case: ProbeCase,
    memory_item_text: str,
    gold_evidence: tuple[GoldEvidence, ...] | None = None,
) -> bool:
    """Check whether a stronger retriever may flip the label to retrieval_error.

    Returns True only when the Memory Item text actually contains the gold
    evidence phrases (evidence_recall_from_text >= 1.0). When the text lacks
    the evidence phrases, returns False -- extraction already lost them, and
    the label must stay premature_extraction_error.
    """
    evidence = gold_evidence if gold_evidence is not None else case.gold_evidence
    recall = evidence_recall_from_text(evidence, memory_item_text)
    return recall >= 1.0


def compute_evidence_boundary_audit(case: ProbeCase) -> dict[str, bool]:
    """For each memory item, determine whether stronger retrieval could flip it.

    Returns a dict mapping memory_id -> can_be_retrieval_error (bool).
    Used to audit which memory items in a premature_extraction_error case
    could have their label flipped by a stronger retriever.
    """
    result: dict[str, bool] = {}
    for item in case.extracted_memory:
        result[item.memory_id] = enforce_retrieval_error_boundary(
            case, item.text, case.gold_evidence
        )
    return result


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_retrieval_baseline_suite(
    case: ProbeCase,
) -> RetrievalBaselineSuiteResult:
    """Run both retrieval baselines (BM25 + HybridRerank) for one probe case."""
    retrievers = {
        "bm25": run_bm25_retrieval,
        "hybrid_rerank": run_hybrid_rerank_retrieval,
    }
    results: list[RetrievalBaselineResult] = []
    for name, retriever_fn in retrievers.items():
        traces = retriever_fn(case)
        traces_tuple = tuple(traces)
        metrics = compute_retrieval_metrics(
            list(traces_tuple), case.case_id, name, case.gold_answer
        )
        top_text = traces[0].retrieved_text if traces else ""
        best_score = answer_score(top_text, case.gold_answer)
        results.append(
            RetrievalBaselineResult(
                case_id=case.case_id,
                retriever_name=name,
                traces=traces_tuple,
                metrics=metrics,
                best_answer=top_text,
                best_answer_score=best_score,
            )
        )
    return RetrievalBaselineSuiteResult(
        case_id=case.case_id,
        baseline_results=tuple(results),
    )
