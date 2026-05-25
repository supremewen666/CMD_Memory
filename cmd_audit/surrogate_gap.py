"""Self-Supervision Surrogate Gap Measurement — Issue 0020-E / 0021 Step 2.

Measures the gap between gold-evidence recovery gain and surrogate-evidence
recovery gain on a subset of cases. Produces paper gap data; does NOT train.

Gold-dependent labels (4/11): write_error, compression_error,
premature_extraction_error, injection_error.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import ProbeCase
from .replays import AgentGenerate, EvidenceScorer
from .scoring import evidence_recall_from_text


GOLD_DEPENDENT_LABELS = (
    "write_error",
    "compression_error",
    "premature_extraction_error",
    "injection_error",
)


@dataclass(frozen=True)
class SurrogateGapRow:
    """One row measuring surrogate vs gold gap for a single case."""

    case_id: str
    label: str
    gold_recovery_gain: float
    surrogate_recovery_gain: float
    gap: float
    surrogate_found: bool


@dataclass(frozen=True)
class SurrogateGapSummary:
    """Aggregated surrogate gap statistics."""

    total_cases: int
    gold_dependent_cases: int
    cases_with_surrogate: int
    avg_gap: float
    median_gap: float
    max_gap: float
    min_gap: float
    pct_surrogate_found: float


def _find_surrogate_evidence(case: ProbeCase) -> tuple[str, ...]:
    """Find surrogate evidence from success-trace memory items via BM25.

    Uses retrieval_baselines.compute_bm25_scores for proper BM25 ranking.
    In production, the doc pool would be a sliding window of same-agent-session
    items; here we use the case's own extracted_memory as the pool.
    """
    if not case.extracted_memory:
        return ()

    from .retrieval_baselines import compute_bm25_scores, tokenize

    query_tokens = tokenize(case.query)
    doc_tokens_list = [tokenize(item.text) for item in case.extracted_memory]

    if not query_tokens or not any(doc_tokens_list):
        return ()

    scores = compute_bm25_scores(query_tokens, doc_tokens_list)
    indexed = [(score, item.text) for score, item in zip(scores, case.extracted_memory)]
    indexed.sort(key=lambda x: x[0], reverse=True)

    # Return top-3 items with positive scores
    return tuple(text for score, text in indexed[:3] if score > 0)


def _compute_recovery_gain(
    case: ProbeCase,
    evidence_texts: tuple[str, ...],
    *,
    evidence_block_name: str = "SURROGATE EVIDENCE BLOCK",
    agent_generate: AgentGenerate | None = None,
    scorer: EvidenceScorer | None = None,
    baseline_evidence_score: float | None = None,
) -> float:
    """Compute recovery gain using given evidence texts (gold or surrogate).

    With agent_generate + scorer, this follows the Decision 34 LLM stack by
    making the agent answer from baseline context plus the candidate evidence
    block, then scoring the answer. Without them, it preserves the legacy
    phrase-match surrogate measurement for existing tests and dry runs.
    """
    if not evidence_texts:
        return 0.0
    combined = " | ".join(evidence_texts)
    if agent_generate is not None:
        context = _evidence_agent_context(case, combined, evidence_block_name)
        answer = agent_generate(case.query, context)
        if scorer is not None:
            score = scorer(case.gold_evidence, answer)
        else:
            score = evidence_recall_from_text(case.gold_evidence, answer)
    else:
        score = evidence_recall_from_text(case.gold_evidence, combined)
    baseline = (
        baseline_evidence_score
        if baseline_evidence_score is not None
        else case.primary_baseline.evidence_score
    )
    return max(0.0, score - baseline)


def measure_surrogate_gap(
    case: ProbeCase,
    *,
    agent_generate: AgentGenerate | None = None,
    scorer: EvidenceScorer | None = None,
) -> SurrogateGapRow | None:
    """Measure surrogate vs gold gap for a single case.

    Returns None if the case's label is not gold-dependent.
    """
    label = case.perturbation_label
    if label not in GOLD_DEPENDENT_LABELS:
        return None

    baseline_evidence_score = None
    if agent_generate is not None:
        baseline_answer = agent_generate(case.query, _baseline_agent_context(case))
        if scorer is not None:
            baseline_evidence_score = scorer(case.gold_evidence, baseline_answer)
        else:
            baseline_evidence_score = evidence_recall_from_text(
                case.gold_evidence,
                baseline_answer,
            )

    # Gold path: use actual gold evidence
    gold_texts = tuple(ev.text for ev in case.gold_evidence)
    gold_gain = _compute_recovery_gain(
        case,
        gold_texts,
        evidence_block_name="GOLD EVIDENCE BLOCK",
        agent_generate=agent_generate,
        scorer=scorer,
        baseline_evidence_score=baseline_evidence_score,
    )

    # Surrogate path: use BM25-retrieved success-trace items
    surrogate_texts = _find_surrogate_evidence(case)
    surrogate_gain = _compute_recovery_gain(
        case,
        surrogate_texts,
        evidence_block_name="SURROGATE EVIDENCE BLOCK",
        agent_generate=agent_generate,
        scorer=scorer,
        baseline_evidence_score=baseline_evidence_score,
    )

    return SurrogateGapRow(
        case_id=case.case_id,
        label=label,
        gold_recovery_gain=gold_gain,
        surrogate_recovery_gain=surrogate_gain,
        gap=gold_gain - surrogate_gain,
        surrogate_found=len(surrogate_texts) > 0,
    )


def measure_surrogate_gaps(
    cases: list[ProbeCase],
    *,
    agent_generate: AgentGenerate | None = None,
    scorer: EvidenceScorer | None = None,
) -> tuple[SurrogateGapRow, ...]:
    """Measure surrogate gaps for all gold-dependent cases in the list."""
    rows: list[SurrogateGapRow] = []
    for case in cases:
        row = measure_surrogate_gap(
            case,
            agent_generate=agent_generate,
            scorer=scorer,
        )
        if row is not None:
            rows.append(row)
    return tuple(rows)


def _baseline_agent_context(case: ProbeCase) -> str:
    baseline_context = case.primary_baseline.injected_context
    if baseline_context:
        return baseline_context
    memory_by_id = {item.memory_id: item for item in case.extracted_memory}
    return "\n".join(
        memory_by_id[mid].text
        for mid in case.primary_baseline.retrieved_memory_ids
        if mid in memory_by_id
    )


def _evidence_agent_context(
    case: ProbeCase,
    evidence_block: str,
    evidence_block_name: str,
) -> str:
    return "\n\n".join(
        (
            "BASELINE CONTEXT:\n" + _baseline_agent_context(case),
            f"{evidence_block_name}:\n" + evidence_block,
        )
    )


def compute_surrogate_gap_summary(
    rows: tuple[SurrogateGapRow, ...],
) -> SurrogateGapSummary:
    """Aggregate gap statistics from SurrogateGapRows."""
    if not rows:
        return SurrogateGapSummary(
            total_cases=0,
            gold_dependent_cases=0,
            cases_with_surrogate=0,
            avg_gap=0.0,
            median_gap=0.0,
            max_gap=0.0,
            min_gap=0.0,
            pct_surrogate_found=0.0,
        )

    gaps = [r.gap for r in rows]
    found = sum(1 for r in rows if r.surrogate_found)
    n = len(rows)

    sorted_gaps = sorted(gaps)
    median = sorted_gaps[n // 2] if n % 2 == 1 else (sorted_gaps[n // 2 - 1] + sorted_gaps[n // 2]) / 2

    return SurrogateGapSummary(
        total_cases=n,
        gold_dependent_cases=n,
        cases_with_surrogate=found,
        avg_gap=sum(gaps) / n,
        median_gap=median,
        max_gap=max(gaps),
        min_gap=min(gaps),
        pct_surrogate_found=found / n,
    )
