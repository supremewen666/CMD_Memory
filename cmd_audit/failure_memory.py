"""ECS Failure Memory storage and retrieval — Issue 0007."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .labels import validate_v0_label, validate_v1_label
from .models import ProbeCase
from .post_repair import ECSDraft
from .scoring import evidence_recall_from_text
from .writers import write_csv_table, write_text_artifact

_STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "was",
        "are",
        "were",
        "be",
        "been",
        "for",
        "of",
        "in",
        "to",
        "with",
        "on",
        "at",
        "by",
        "from",
        "which",
        "what",
        "who",
        "whom",
        "whose",
        "where",
        "when",
        "did",
        "do",
        "does",
        "has",
        "have",
        "had",
        "this",
        "that",
        "and",
        "or",
        "not",
        "but",
        "if",
        "then",
        "else",
        "about",
        "city",
        "chose",
        "choose",
        "selected",
        "select",
    }
)

_CONTEXT_MODE_VALUES = ("none", "full_trace", "corrected_guidance")


def _extract_keywords(text: str) -> tuple[str, ...]:
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text.casefold())
    return tuple(sorted(set(w for w in words if w not in _STOP_WORDS)))


def _build_trigger_signature(query: str, label: str) -> str:
    keywords = _extract_keywords(query)
    return f"{label}|{' '.join(keywords)}"


# ── Data types ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FailureMemoryRecord:
    """ECS record stored as Failure Memory with trigger signature for retrieval."""

    error_type: str
    wrong_memory: str
    original_evidence: str
    cause: str
    corrected_memory: str
    repair_action: str
    repair_guidance: str
    trigger_signature: str
    memory_top_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_v1_label(self.error_type)

    @classmethod
    def from_ecs_draft(cls, ecs: ECSDraft, case: ProbeCase) -> "FailureMemoryRecord":
        baseline = case.primary_baseline
        return cls(
            error_type=ecs.predicted_label,
            wrong_memory=baseline.injected_context,
            original_evidence=" | ".join(ev.text for ev in case.gold_evidence),
            cause=ecs.cause,
            corrected_memory=ecs.corrected_memory,
            repair_action=ecs.predicted_label,
            repair_guidance=ecs.repair_guidance,
            trigger_signature=_build_trigger_signature(case.query, ecs.predicted_label),
            memory_top_terms=compute_memory_top_terms(case.extracted_memory),
        )


# V0 baseline — kept for paper comparison.
@dataclass(frozen=True)
class FailureMemoryStore:
    """Immutable store of Failure Memory records with keyword-based retrieval."""

    records: tuple[FailureMemoryRecord, ...] = ()

    def add(self, record: FailureMemoryRecord) -> "FailureMemoryStore":
        return FailureMemoryStore(records=self.records + (record,))

    def retrieve(self, query: str, top_k: int = 3) -> tuple[FailureMemoryRecord, ...]:
        query_keywords = set(_extract_keywords(query))
        if not query_keywords:
            return ()
        scored: list[tuple[int, FailureMemoryRecord]] = []
        for record in self.records:
            sig_keywords = set(record.trigger_signature.casefold().split())
            overlap = len(query_keywords & sig_keywords)
            if overlap > 0:
                scored.append((overlap, record))
        scored.sort(key=lambda x: x[0], reverse=True)
        return tuple(record for _, record in scored[:top_k])

    def __len__(self) -> int:
        return len(self.records)

    def __bool__(self) -> bool:
        return len(self.records) > 0


def build_failure_memory_context(
    records: tuple[FailureMemoryRecord, ...],
    mode: str,
) -> str:
    """Build context text from retrieved Failure Memory records.

    Modes:
      - ``"none"``: empty context
      - ``"full_trace"``: inject wrong_memory from past failures (anti-pattern)
      - ``"corrected_guidance"``: inject corrected_memory + repair_guidance (CMD pattern)
    """
    if mode not in _CONTEXT_MODE_VALUES:
        raise ValueError(
            f"Unknown Failure Memory context mode: {mode!r}; "
            f"must be one of {_CONTEXT_MODE_VALUES}"
        )
    if mode == "none" or not records:
        return ""

    if mode == "full_trace":
        parts: list[str] = []
        for i, r in enumerate(records, start=1):
            parts.append(f"[Past Failure Trace {i}]\n{r.wrong_memory}")
        return "\n\n".join(parts)

    # corrected_guidance
    parts = []
    for i, r in enumerate(records, start=1):
        parts.append(
            f"[Failure Memory Guidance {i}]\n"
            f"Corrected: {r.corrected_memory}\n"
            f"Guidance: {r.repair_guidance}"
        )
    return "\n\n".join(parts)


# ── Recurrence comparison ───────────────────────────────────────────────


def _score_context(
    gold_evidence, gold_answer: str, fm_context: str, query: str
) -> tuple[float, float, float]:
    """Return (answer_score, evidence_score, token_cost) for a context."""
    combined = f"{fm_context}\n\nQuery: {query}" if fm_context else f"Query: {query}"
    ev_score = evidence_recall_from_text(gold_evidence, combined)
    ans_score = 1.0 if gold_answer.casefold() in combined.casefold() else 0.0
    token_cost = len(combined) / 4.0
    return ans_score, ev_score, token_cost


@dataclass(frozen=True)
class RecurrenceComparisonRow:
    """One row comparing recurrence outcomes across three Failure Memory modes."""

    case_id: str
    perturbation_label: str
    no_fm_answer_score: float
    no_fm_evidence_score: float
    full_trace_answer_score: float
    full_trace_evidence_score: float
    corrected_guidance_answer_score: float
    corrected_guidance_evidence_score: float
    no_fm_token_cost: float
    full_trace_token_cost: float
    corrected_guidance_token_cost: float
    full_trace_pollution_risk: float
    corrected_guidance_better_than_none: bool
    corrected_guidance_better_than_full_trace: bool
    failure_memory_useful: bool

    @property
    def any_fm_improvement(self) -> bool:
        return self.corrected_guidance_better_than_none

    @property
    def full_trace_causes_regression(self) -> bool:
        return (
            self.full_trace_evidence_score < self.no_fm_evidence_score
            or self.full_trace_answer_score < self.no_fm_answer_score
        )


def run_recurrence_comparison(
    case: ProbeCase,
    fm_store: FailureMemoryStore,
) -> RecurrenceComparisonRow:
    """Compare three Failure Memory context modes for a future similar task."""
    records = fm_store.retrieve(case.query)

    no_fm_ans, no_fm_ev, no_fm_cost = _score_context(
        case.gold_evidence, case.gold_answer, "", case.query
    )

    full_trace_ctx = build_failure_memory_context(records, "full_trace")
    ft_ans, ft_ev, ft_cost = _score_context(
        case.gold_evidence, case.gold_answer, full_trace_ctx, case.query
    )

    corrected_ctx = build_failure_memory_context(records, "corrected_guidance")
    cg_ans, cg_ev, cg_cost = _score_context(
        case.gold_evidence, case.gold_answer, corrected_ctx, case.query
    )

    full_trace_has_evidence = evidence_recall_from_text(
        case.gold_evidence, full_trace_ctx
    )
    pollution_risk = 1.0 - full_trace_has_evidence

    cg_better_none = cg_ev > no_fm_ev or (cg_ev == no_fm_ev and cg_ans > no_fm_ans)
    cg_better_ft = cg_ev > ft_ev or (cg_ev == ft_ev and cg_ans >= ft_ans)
    fm_useful = cg_better_none

    return RecurrenceComparisonRow(
        case_id=case.case_id,
        perturbation_label=case.perturbation_label,
        no_fm_answer_score=no_fm_ans,
        no_fm_evidence_score=no_fm_ev,
        full_trace_answer_score=ft_ans,
        full_trace_evidence_score=ft_ev,
        corrected_guidance_answer_score=cg_ans,
        corrected_guidance_evidence_score=cg_ev,
        no_fm_token_cost=no_fm_cost,
        full_trace_token_cost=ft_cost,
        corrected_guidance_token_cost=cg_cost,
        full_trace_pollution_risk=pollution_risk,
        corrected_guidance_better_than_none=cg_better_none,
        corrected_guidance_better_than_full_trace=cg_better_ft,
        failure_memory_useful=fm_useful,
    )


def run_recurrence_comparisons(
    cases: list[ProbeCase],
    fm_store: FailureMemoryStore,
) -> list[RecurrenceComparisonRow]:
    return [run_recurrence_comparison(case, fm_store) for case in cases]


# ── Aggregation ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RecurrenceSummary:
    """Aggregated Failure Memory recurrence metrics."""

    total_cases: int
    fm_useful_count: int
    fm_useful_rate: float
    avg_evidence_gain_vs_none: float
    avg_evidence_gain_vs_full_trace: float
    avg_full_trace_pollution_risk: float
    avg_token_cost_none: float
    avg_token_cost_full_trace: float
    avg_token_cost_corrected_guidance: float
    failure_memory_worth_keeping: bool


def compute_recurrence_summary(
    rows: list[RecurrenceComparisonRow],
) -> RecurrenceSummary:
    if not rows:
        return RecurrenceSummary(
            total_cases=0,
            fm_useful_count=0,
            fm_useful_rate=0.0,
            avg_evidence_gain_vs_none=0.0,
            avg_evidence_gain_vs_full_trace=0.0,
            avg_full_trace_pollution_risk=0.0,
            avg_token_cost_none=0.0,
            avg_token_cost_full_trace=0.0,
            avg_token_cost_corrected_guidance=0.0,
            failure_memory_worth_keeping=False,
        )

    n = len(rows)
    useful = sum(1 for r in rows if r.failure_memory_useful)
    ev_gain_none = sum(
        r.corrected_guidance_evidence_score - r.no_fm_evidence_score for r in rows
    )
    ev_gain_ft = sum(
        r.corrected_guidance_evidence_score - r.full_trace_evidence_score for r in rows
    )
    pollution = sum(r.full_trace_pollution_risk for r in rows)
    cost_none = sum(r.no_fm_token_cost for r in rows)
    cost_ft = sum(r.full_trace_token_cost for r in rows)
    cost_cg = sum(r.corrected_guidance_token_cost for r in rows)

    return RecurrenceSummary(
        total_cases=n,
        fm_useful_count=useful,
        fm_useful_rate=useful / n,
        avg_evidence_gain_vs_none=ev_gain_none / n,
        avg_evidence_gain_vs_full_trace=ev_gain_ft / n,
        avg_full_trace_pollution_risk=pollution / n,
        avg_token_cost_none=cost_none / n,
        avg_token_cost_full_trace=cost_ft / n,
        avg_token_cost_corrected_guidance=cost_cg / n,
        failure_memory_worth_keeping=useful / n >= 0.5,
    )


# ── Table output ────────────────────────────────────────────────────────


def write_recurrence_comparison_table(
    rows: list[RecurrenceComparisonRow],
    output_path: str | Path,
    *,
    sandbox_root: str | Path | None = None,
) -> Path:
    """Write the recurrence comparison table and summary to the sandbox."""
    path = Path(output_path)
    fieldnames = [
        "case_id",
        "perturbation_label",
        "no_fm_answer_score",
        "no_fm_evidence_score",
        "full_trace_answer_score",
        "full_trace_evidence_score",
        "corrected_guidance_answer_score",
        "corrected_guidance_evidence_score",
        "no_fm_token_cost",
        "full_trace_token_cost",
        "corrected_guidance_token_cost",
        "full_trace_pollution_risk",
        "corrected_guidance_better_than_none",
        "corrected_guidance_better_than_full_trace",
        "failure_memory_useful",
    ]
    row_dicts = [
        {
            "case_id": row.case_id,
            "perturbation_label": row.perturbation_label,
            "no_fm_answer_score": f"{row.no_fm_answer_score:.3f}",
            "no_fm_evidence_score": f"{row.no_fm_evidence_score:.3f}",
            "full_trace_answer_score": f"{row.full_trace_answer_score:.3f}",
            "full_trace_evidence_score": f"{row.full_trace_evidence_score:.3f}",
            "corrected_guidance_answer_score": f"{row.corrected_guidance_answer_score:.3f}",
            "corrected_guidance_evidence_score": f"{row.corrected_guidance_evidence_score:.3f}",
            "no_fm_token_cost": f"{row.no_fm_token_cost:.1f}",
            "full_trace_token_cost": f"{row.full_trace_token_cost:.1f}",
            "corrected_guidance_token_cost": f"{row.corrected_guidance_token_cost:.1f}",
            "full_trace_pollution_risk": f"{row.full_trace_pollution_risk:.3f}",
            "corrected_guidance_better_than_none": str(
                row.corrected_guidance_better_than_none
            ).lower(),
            "corrected_guidance_better_than_full_trace": str(
                row.corrected_guidance_better_than_full_trace
            ).lower(),
            "failure_memory_useful": str(row.failure_memory_useful).lower(),
        }
        for row in rows
    ]
    write_csv_table(path, fieldnames, row_dicts, sandbox_root=sandbox_root)

    _write_recurrence_summary(rows, path.parent / "recurrence_summary.txt")
    return path


def _write_recurrence_summary(
    rows: list[RecurrenceComparisonRow],
    path: Path,
) -> None:
    summary = compute_recurrence_summary(rows)

    lines = [
        "CMD V0 ECS Failure Memory Recurrence Summary — Issue 0007",
        "=" * 60,
        "",
        f"Total future-task cases: {summary.total_cases}",
        "",
        "Failure Memory Utility:",
        f"  Cases where FM improved outcome: {summary.fm_useful_count}/{summary.total_cases}",
        f"  FM useful rate: {summary.fm_useful_rate:.3f}",
        "",
        "Evidence Gain (corrected_guidance vs none):",
        f"  Average: {summary.avg_evidence_gain_vs_none:+.3f}",
        "",
        "Evidence Gain (corrected_guidance vs full_trace):",
        f"  Average: {summary.avg_evidence_gain_vs_full_trace:+.3f}",
        "",
        "Full Trace Pollution Risk:",
        f"  Average: {summary.avg_full_trace_pollution_risk:.3f}",
        "",
        "Token Cost (average per case):",
        f"  No FM:           {summary.avg_token_cost_none:.1f}",
        f"  Full trace:      {summary.avg_token_cost_full_trace:.1f}",
        f"  Corrected guide: {summary.avg_token_cost_corrected_guidance:.1f}",
        "",
        f"Failure Memory worth keeping in scope: {summary.failure_memory_worth_keeping}",
        "",
        "Claim: CMD Failure Memory (corrected_memory + repair_guidance)",
        "improves future similar tasks over no-FM baseline without the",
        "pollution risk of full failed traces.",
        "",
        "Evidence threshold: FM useful rate >= 0.5",
        "",
        "-" * 60,
        "Per-case detail:",
    ]
    for row in rows:
        lines.append(
            f"  {row.case_id} ({row.perturbation_label}): "
            f"FM useful={row.failure_memory_useful}, "
            f"ev gain vs none={row.corrected_guidance_evidence_score - row.no_fm_evidence_score:+.3f}, "
            f"pollution risk={row.full_trace_pollution_risk:.3f}"
        )

    write_text_artifact(path, lines)


# ── Issue 0020-D: Failure Memory Upgrade ────────────────────────────────


def compute_memory_top_terms(retrieved_items: tuple, top_n: int = 5) -> tuple[str, ...]:
    """Extract top-N terms from retrieved items using simple frequency scoring.

    Used as the third dimension of the composite FM retrieval key.
    """
    from collections import Counter

    if not retrieved_items:
        return ()
    all_text = " ".join(
        getattr(item, "text", str(item)) for item in retrieved_items
    )
    words = re.findall(r"\b[a-zA-Z]{4,}\b", all_text.casefold())
    filtered = [w for w in words if w not in _STOP_WORDS]
    counts = Counter(filtered)
    return tuple(word for word, _ in counts.most_common(top_n))


def _score_composite_key(
    record: FailureMemoryRecord,
    query: str,
    label: str,
) -> int:
    """Score a record against a composite key (label + query + memory_terms).

    Returns integer score: label_match (2) + query_overlap + stored memory-term overlap.
    """
    score = 0
    if record.error_type == label:
        score += 2

    query_keywords = set(_extract_keywords(query))
    sig_keywords = set(record.trigger_signature.casefold().split())
    query_overlap = len(query_keywords & sig_keywords)
    score += query_overlap

    if record.memory_top_terms:
        mem_overlap = len(set(record.memory_top_terms) & query_keywords)
        score += mem_overlap

    return score


@dataclass(frozen=True)
class FailureMemoryStoreV1:
    """Upgraded Failure Memory store with composite-key retrieval."""

    records: tuple[FailureMemoryRecord, ...] = ()

    def add(self, record: FailureMemoryRecord) -> "FailureMemoryStoreV1":
        return FailureMemoryStoreV1(records=self.records + (record,))

    def add_if_recovered(
        self, record: FailureMemoryRecord, assessment: str
    ) -> "FailureMemoryStoreV1":
        """Store only recovered ECS records (Decision 32, Point 9).

        Partial and failed repairs are discarded. Per-agent persistence
        stores as FAILURE_MEMORY.md alongside agent's MEMORY.md.
        """
        if assessment == "recovered":
            return self.add(record)
        return self

    def retrieve(
        self,
        query: str,
        label: str = "",
        top_k: int = 3,
    ) -> tuple[FailureMemoryRecord, ...]:
        """Composite-key retrieval: label + query_keywords + memory_top_terms."""
        scored: list[tuple[int, FailureMemoryRecord]] = []
        for record in self.records:
            score = _score_composite_key(record, query, label)
            if score > 0:
                scored.append((score, record))
        scored.sort(key=lambda x: x[0], reverse=True)
        return tuple(record for _, record in scored[:top_k])

    def __len__(self) -> int:
        return len(self.records)

    def __bool__(self) -> bool:
        return len(self.records) > 0


_FM_CONTEXT_HEADER = (
    "[Failure Memory Diagnostic Context]\n"
    "The following shows a past error pattern similar to the current situation.\n"
    "It contains the incorrect memory content and the evidence of why it was wrong.\n"
)


def build_failure_memory_context_v1(
    records: tuple[FailureMemoryRecord, ...],
) -> str:
    """Build fm_context = wrong_memory + original_evidence (diagnostic signal).

    Complements corrected_memory (repair signal: "what it should be").
    """
    if not records:
        return ""
    parts: list[str] = [_FM_CONTEXT_HEADER]
    for i, record in enumerate(records, start=1):
        parts.append(
            f"[Past Error {i} — {record.error_type}]\n"
            f"Wrong memory content: {record.wrong_memory}\n"
            f"Evidence of error: {record.original_evidence}"
        )
    return "\n\n".join(parts)


def build_repair_context(
    baseline_context: str,
    label: str,
    evidence_block: str,
    fm_context: str,
) -> str:
    """Build the full repair context: baseline + label + evidence + fm_context.

    Injected at ECS stage (downstream of attribution, preserves causal purity).
    """
    parts = [baseline_context]
    if label:
        parts.append(f"[Diagnosis: {label}]")
    if evidence_block:
        parts.append(f"[Corrected Evidence]\n{evidence_block}")
    if fm_context:
        parts.append(fm_context)
    return "\n\n".join(parts)
