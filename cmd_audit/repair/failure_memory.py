"""ECS Failure Memory storage and retrieval — Issue 0007."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..core.labels import (
    ITEM_LABELS,
    PIPELINE_STEP_ACTIONS,
    validate_diagnosis_label,
    validate_label,
)
from ..core.models import MemoryItem, ProbeCase
from ..counterfactual import OperatorSpec, PipelineAction
from .ecs import ECSDraft
from .governance import GovernanceDecision, OperatorGovernance
from .operator_library import PatternRecord, canonical_json, content_id, hash_text
from ..scoring import evidence_recall_from_text
from ..eval.writers import write_csv_table, write_text_artifact

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


# Structural scaffold words shared by every multihop chain (role names, hop
# markers). They carry no chain-specific identity, so they must be stripped
# before fingerprinting or they inflate cross-family similarity (the 27.6%
# floor measured during fingerprint validation).
_FINGERPRINT_SCAFFOLD = frozenset(
    {
        "bridge",
        "key",
        "chain",
        "hop",
        "first",
        "second",
        "third",
        "gold",
        "distractor",
        "session",
        "event",
        "memory",
        "item",
        "store",
        "resolve",
        "two",
    }
)


def _memory_fingerprint(texts: tuple[str, ...], *, top_k: int = 12) -> str:
    """Content fingerprint of the memory items a failure hinged on.

    The recurrence identity of a memory failure is the *content* of the items
    that were (mis)retrieved, not the query wording: paraphrased queries in the
    same failure family hit the same items, so a fingerprint over item text is
    paraphrase-invariant where a query-keyword signature is not (validated:
    91.6% intra-family vs 27.6% cross-family similarity).

    Returns a space-joined string of the ``top_k`` most frequent content words
    so the existing Jaccard similarity (``_query_signature_similarity``) applies
    unchanged. Scaffold words shared by all chains are stripped first.
    """
    words: list[str] = []
    for text in texts:
        for word in re.findall(r"[a-z0-9][\w-]{2,}", text.casefold()):
            if word in _STOP_WORDS or word in _FINGERPRINT_SCAFFOLD:
                continue
            words.append(word)
    if not words:
        return ""
    ranked = [word for word, _count in Counter(words).most_common(top_k)]
    return " ".join(sorted(ranked))


def memory_fingerprint_for_items(
    items: tuple[MemoryItem, ...],
    *,
    fingerprint_mode: str = "content",
    top_k: int = 12,
) -> str:
    """Build the stable content key, optionally augmented with item structure.

    ``content`` preserves the validated step-layer key. ``hybrid`` appends
    coarse, gold-free timestamp and recall-shape buckets so item-layer
    retrieval can distinguish otherwise text-similar stale/conflict states.
    """
    content = _memory_fingerprint(
        tuple(item.text for item in items),
        top_k=top_k,
    )
    if fingerprint_mode == "content":
        return content
    if fingerprint_mode != "hybrid":
        raise ValueError("fingerprint_mode must be 'content' or 'hybrid'")

    timestamps = sorted(
        timestamp
        for item in items
        for timestamp in [_timestamp_seconds(item.store)]
        if timestamp is not None
    )
    if len(timestamps) < 2:
        time_bucket = "ts:none"
    else:
        span_days = (timestamps[-1] - timestamps[0]) / (24 * 60 * 60)
        if span_days <= 7:
            time_bucket = "ts:same_period"
        elif span_days <= 30:
            time_bucket = "ts:weeks"
        else:
            time_bucket = "ts:months_plus"
    source_shapes = sorted(
        {
            "atomic" if len(item.source_event_ids) <= 1 else "compressed"
            for item in items
        }
    )
    shape_bucket = f"shape:{'+'.join(source_shapes) or 'unknown'}"
    count_bucket = f"count:{min(len(items), 5)}"
    return " ".join(
        part for part in (content, time_bucket, shape_bucket, count_bucket) if part
    )


def _timestamp_seconds(value: str) -> float | None:
    from datetime import datetime

    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _signature_from(query: str, memory_texts: tuple[str, ...]) -> str:
    """Retrieval signature for a step-level key.

    Prefers the paraphrase-invariant content fingerprint of the recall set when
    ``memory_texts`` is supplied; falls back to the query-keyword signature
    otherwise, preserving the original behaviour for callers that pass no recall
    content (unit tests, legacy online paths).
    """
    if memory_texts:
        fingerprint = _memory_fingerprint(memory_texts)
        if fingerprint:
            return fingerprint
    return " ".join(_extract_keywords(query)[:10])


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
        validate_diagnosis_label(self.error_type)

    @classmethod
    def from_ecs_draft(cls, ecs: ECSDraft, case: ProbeCase) -> "FailureMemoryRecord":
        baseline = case.primary_baseline
        recovered_action = ecs.recovered_action or ecs.predicted_label
        return cls(
            error_type=recovered_action,
            wrong_memory=baseline.injected_context,
            original_evidence=" | ".join(ev.text for ev in case.gold_evidence),
            cause=ecs.cause,
            corrected_memory=ecs.corrected_memory,
            repair_action=recovered_action,
            repair_guidance=ecs.repair_guidance,
            trigger_signature=_build_trigger_signature(case.query, recovered_action),
            memory_top_terms=compute_memory_top_terms(case.extracted_memory),
        )


@dataclass(frozen=True)
class _FailureMemoryStoreV0:
    """V0 keyword-only store — internal, used by recurrence comparison."""

    records: tuple[FailureMemoryRecord, ...] = ()

    def add(self, record: FailureMemoryRecord) -> "_FailureMemoryStoreV0":
        return _FailureMemoryStoreV0(records=self.records + (record,))

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


def _build_failure_memory_context_v0(
    records: tuple[FailureMemoryRecord, ...],
    mode: str,
) -> str:
    """V0 three-mode context builder — internal, used by recurrence comparison."""
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
    fm_store: _FailureMemoryStoreV0,
) -> RecurrenceComparisonRow:
    """Compare three Failure Memory context modes for a future similar task."""
    records = fm_store.retrieve(case.query)

    no_fm_ans, no_fm_ev, no_fm_cost = _score_context(
        case.gold_evidence, case.gold_answer, "", case.query
    )

    full_trace_ctx = _build_failure_memory_context_v0(records, "full_trace")
    ft_ans, ft_ev, ft_cost = _score_context(
        case.gold_evidence, case.gold_answer, full_trace_ctx, case.query
    )

    corrected_ctx = _build_failure_memory_context_v0(records, "corrected_guidance")
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
    fm_store: _FailureMemoryStoreV0,
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
        "CMD ECS Failure Memory Recurrence Summary",
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
        "Claim: CMD Failure Memory operator/corrected-content repairs",
        "improve future similar tasks over no-FM baseline without injecting",
        "full failed traces or answer-time guidance.",
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


_FM_CONTEXT_HEADER = (
    "[Failure Memory Diagnostic Context]\n"
    "The following shows a past error pattern similar to the current situation.\n"
    "It contains the incorrect memory content and the evidence of why it was wrong.\n"
)


def build_failure_memory_context(
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


# ── Markdown Failure Memory skill contract ──────────────────────────────


@dataclass(frozen=True)
class FailureMemoryDiagnosis:
    """Diagnosis fields needed to write a case record."""

    query: str
    label: str
    cause: str
    corrected_memory: str
    repair_guidance: str
    retrieved_items: tuple[str, ...] = ()
    signature: str = ""
    problem_item: str = ""
    pattern: str = ""
    operator_spec: OperatorSpec | None = None

    def __post_init__(self) -> None:
        validate_diagnosis_label(self.label)


@dataclass(frozen=True)
class FailureMemoryOutcome:
    """Outcome fields recorded after repair validation."""

    assessment: str
    recovered: bool
    recovery_gain: float = 0.0
    notes: str = ""


@dataclass(frozen=True)
class PatternValidationResult:
    """Self-check result for a pattern against its source cases."""

    valid: bool
    inconsistencies: tuple[str, ...] = ()
    suggested_update: str = ""


class FailureMemorySkill:
    """Stateless methods for case-first Failure Memory reuse.

    The skill does not own storage. Agents pass a markdown memory path and an
    LLM client when they want online reuse or abstraction.
    """

    def hook(self, query: str, retrieved_items: tuple) -> object:
        from ..core.models import RetrievedItem
        from ..hook import post_retrieve_hook

        normalized = tuple(
            item
            if isinstance(item, RetrievedItem)
            else RetrievedItem(
                memory_id=getattr(item, "memory_id", str(index)),
                text=getattr(item, "text", str(item)),
            )
            for index, item in enumerate(retrieved_items)
        )
        return post_retrieve_hook(query, normalized)

    def diagnose(
        self,
        context: str,
        memory_path: str | Path | None = None,
        *,
        llm_client=None,
    ):
        prompt = self._diagnose_prompt(context, memory_path)
        if llm_client is None:
            return {"prompt": prompt, "requires_llm": True}
        return llm_client.generate(prompt)

    def repair(self, diagnosis: FailureMemoryDiagnosis | str, *, llm_client=None):
        if isinstance(diagnosis, FailureMemoryDiagnosis):
            return diagnosis.repair_guidance
        if llm_client is None:
            return {"diagnosis": diagnosis, "requires_llm": True}
        return llm_client.generate(
            "Given this CMD diagnosis, propose the repair action and guidance.\n\n"
            f"{diagnosis}"
        )

    def format_case(
        self,
        diagnosis: FailureMemoryDiagnosis,
        outcome: FailureMemoryOutcome,
    ) -> str:
        signature = diagnosis.signature or _build_trigger_signature(
            diagnosis.query, diagnosis.label
        )
        retrieved = "\n".join(
            f"- {item}" for item in diagnosis.retrieved_items
        ) or "- None recorded"
        pattern = diagnosis.pattern or "None"
        return "\n".join(
            [
                f"# Case: {signature}",
                "",
                f"**Query**: {diagnosis.query}",
                f"**Signature**: {signature}",
                "",
                "## Retrieved Items",
                retrieved,
                "",
                "## Diagnosis",
                f"- **Label**: {diagnosis.label}",
                f"- **Problem Item**: {diagnosis.problem_item or 'Not specified'}",
                f"- **Root Cause**: {diagnosis.cause}",
                "",
                "## Repair",
                "### Operator Spec",
                (
                    diagnosis.operator_spec.to_markdown_block()
                    if diagnosis.operator_spec is not None
                    else "None recorded"
                ),
                "",
                f"- **Guidance**: {diagnosis.repair_guidance}",
                f"- **Corrected Memory**: {diagnosis.corrected_memory}",
                "",
                "## Outcome",
                f"- **Assessment**: {outcome.assessment}",
                f"- **Recovered**: {str(outcome.recovered).lower()}",
                f"- **Recovery Gain**: {outcome.recovery_gain:.3f}",
                f"- **Pattern**: {pattern}",
                "",
            ]
        )

    def format_pattern(
        self,
        cases: tuple[str, ...],
        *,
        trigger_fingerprint: str = "",
        source_case_ids: tuple[str, ...] = (),
        operator_spec: OperatorSpec | None = None,
        recovery_track: dict[str, float | int] | None = None,
        llm_client=None,
    ) -> str:
        if llm_client is not None:
            return llm_client.generate(
                "Abstract a reusable CMD Failure Memory pattern from these "
                "source cases. The output must include an executable operator "
                "spec, trigger fingerprint, recovery track record, and source "
                "case citations. Do not write answer-time guidance as the repair "
                "mechanism.\n\n"
                + "\n\n---\n\n".join(cases)
            )
        labels = sorted(
            {
                match.group(1)
                for case in cases
                for match in [re.search(r"\*\*Label\*\*:\s*([a-z_]+)", case)]
                if match
            }
        )
        diagnosis = labels[0] if len(labels) == 1 else "review_required"
        if operator_spec is None and diagnosis in PIPELINE_STEP_ACTIONS:
            operator_spec = OperatorSpec.single(0, PipelineAction(diagnosis))
        track = recovery_track or {}
        recovered = int(track.get("recovered", len(cases)))
        total = int(track.get("total", len(cases)))
        avg_gain = float(track.get("avg_recovery_gain", 0.0))
        operator_block = (
            operator_spec.to_markdown_block()
            if operator_spec is not None
            else "```operator-spec\n - review_required\n```"
        )
        source_ids = source_case_ids or tuple(
            f"case_{i + 1}" for i in range(len(cases))
        )
        return "\n".join(
            [
                f"# Pattern: {diagnosis}",
                "",
                f"**Trigger Fingerprint**: {trigger_fingerprint or 'review_required'}",
                "",
                "**Trigger Conditions**:",
                "- Recall-content fingerprint matches the source case cluster.",
                "- Retrieved memory shape is compatible with the operator spec.",
                "",
                f"**Diagnosis**: {diagnosis}",
                "",
                "## Operator Spec",
                operator_block,
                "",
                "## Recovery Track Record",
                f"- Recovered source cases: {recovered}/{total}",
                f"- Average recovery gain: {avg_gain:.3f}",
                "- Acceptance gate: execute the operator and keep it only if recovery improves.",
                "",
                "**Source Cases**:",
                *[f"- {case_id}" for case_id in source_ids],
                "",
                "**Validation Status**: review_required",
                "",
            ]
        )

    def validate_pattern(
        self,
        pattern: str,
        cases: tuple[str, ...],
        *,
        llm_client=None,
    ) -> PatternValidationResult:
        if llm_client is not None:
            response = llm_client.generate(
                "Validate whether this Failure Memory pattern is consistent "
                "with the concrete source cases. Return valid/invalid, "
                "inconsistencies, and suggested update.\n\n"
                f"PATTERN:\n{pattern}\n\nCASES:\n"
                + "\n\n---\n\n".join(cases)
            )
            return PatternValidationResult(
                valid="invalid" not in response.casefold(),
                suggested_update=response,
            )

        pattern_label = _extract_pattern_label(pattern)
        case_labels = tuple(
            label
            for case in cases
            for label in [_extract_case_label(case)]
            if label
        )
        inconsistencies = []
        if pattern_label and case_labels:
            mismatched = tuple(label for label in case_labels if label != pattern_label)
            if mismatched:
                inconsistencies.append(
                    "pattern label does not match all source case labels"
                )
        if "Source Cases" not in pattern:
            inconsistencies.append("pattern does not cite source cases")
        return PatternValidationResult(
            valid=not inconsistencies,
            inconsistencies=tuple(inconsistencies),
            suggested_update=(
                "" if not inconsistencies else "Use the concrete case record as source of truth."
            ),
        )

    def _diagnose_prompt(self, context: str, memory_path: str | Path | None) -> str:
        memory_hint = (
            f"Failure Memory path: {memory_path}" if memory_path else "No path provided"
        )
        return (
            "Diagnose this CMD memory failure. First decide whether to inspect "
            "Failure Memory. If you inspect it, read the index, then concrete "
            "cases, then patterns; validate any pattern against its cases before "
            "using it. Return label, cause, corrected_memory, and repair_guidance.\n\n"
            f"{memory_hint}\n\nCONTEXT:\n{context}"
        )


class MarkdownFailureMemoryStore:
    """Three-layer markdown storage for agent-owned Failure Memory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.index_path = self.root / "FAILURE_MEMORY.md"
        self.cases_dir = self.root / "cases"
        self.patterns_dir = self.root / "patterns"

    def ensure(self) -> "MarkdownFailureMemoryStore":
        self.cases_dir.mkdir(parents=True, exist_ok=True)
        self.patterns_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self.index_path.write_text(
                "# Failure Memory\n\n## Cases\n\n## Patterns\n",
                encoding="utf-8",
            )
        return self

    def write_case(
        self,
        case_id: str,
        markdown: str,
        *,
        summary: str,
    ) -> Path:
        self.ensure()
        path = self.cases_dir / f"{case_id}.md"
        path.write_text(markdown, encoding="utf-8")
        self._upsert_index_line(
            "## Cases",
            f"- [{case_id}](cases/{case_id}.md) - {summary}",
        )
        return path

    def write_pattern(
        self,
        pattern_id: str,
        markdown: str,
        *,
        summary: str,
    ) -> Path:
        self.ensure()
        path = self.patterns_dir / f"{pattern_id}.md"
        path.write_text(markdown, encoding="utf-8")
        self._upsert_index_line(
            "## Patterns",
            f"- [{pattern_id}](patterns/{pattern_id}.md) - {summary}",
        )
        return path

    def read_index(self) -> str:
        self.ensure()
        return self.index_path.read_text(encoding="utf-8")

    def read_case(self, case_id: str) -> str:
        return (self.cases_dir / f"{case_id}.md").read_text(encoding="utf-8")

    def read_pattern(self, pattern_id: str) -> str:
        return (self.patterns_dir / f"{pattern_id}.md").read_text(encoding="utf-8")

    def list_pattern_ids(self) -> tuple[str, ...]:
        """Return stored pattern ids in deterministic order."""
        self.ensure()
        return tuple(sorted(path.stem for path in self.patterns_dir.glob("*.md")))

    def iter_pattern_markdowns(self) -> tuple[tuple[str, str], ...]:
        """Return ``(pattern_id, markdown)`` pairs for stored patterns."""
        return tuple(
            (
                pattern_id,
                self.read_pattern(pattern_id),
            )
            for pattern_id in self.list_pattern_ids()
        )

    def read_pattern_records(self) -> tuple["FailureMemoryPatternRecord", ...]:
        """Parse executable pattern records from markdown skill files.

        Patterns marked for review, missing a parseable operator spec, or using
        non-live labels are skipped. Concrete case files remain the source of
        truth; this method only reloads already-distilled executable skills.
        """
        records: list[FailureMemoryPatternRecord] = []
        for pattern_id, markdown in self.iter_pattern_markdowns():
            record = _pattern_record_from_markdown(pattern_id, markdown)
            if record is not None:
                records.append(record)
        return tuple(records)

    def retrieve_operator_specs(
        self,
        query: str,
        *,
        max_depth: int,
        top_k: int = 2,
        memory_texts: tuple[str, ...] = (),
    ) -> tuple[list[OperatorSpec], int]:
        """Retrieve executable operator specs from persisted markdown skills."""
        signature = _memory_fingerprint(memory_texts or (query,))
        scored: list[tuple[float, FailureMemoryPatternRecord]] = []
        for record in self.read_pattern_records():
            if record.review_required or record.operator_spec is None:
                continue
            score = _query_signature_similarity(record.query_signature, signature)
            if score > 0.0:
                scored.append((score, record))
        scored.sort(key=lambda item: item[0], reverse=True)

        specs: list[OperatorSpec] = []
        seen = set()
        for _score, record in scored:
            spec = record.operator_spec
            if not _operator_within_depth(spec, max_depth):
                continue
            key = spec.format()
            if key in seen:
                continue
            specs.append(spec)
            seen.add(key)
            if len(specs) >= top_k:
                break
        return specs, len(scored)

    def reuse_prompt(
        self,
        *,
        query: str,
        retrieved_items: tuple[str, ...],
        failure_signal: str,
    ) -> str:
        index = self.read_index()
        retrieved = "\n".join(f"- {item}" for item in retrieved_items)
        return (
            "Decide whether Failure Memory contains a relevant prior case. "
            "Do not use patterns until you have selected and checked concrete "
            "cases from the index. If a pattern contradicts its source case, "
            "prefer the case.\n\n"
            f"INDEX:\n{index}\n\n"
            f"QUERY:\n{query}\n\nRETRIEVED ITEMS:\n{retrieved}\n\n"
            f"FAILURE SIGNAL:\n{failure_signal}"
        )

    def _upsert_index_line(self, section: str, line: str) -> None:
        text = self.read_index()
        if line in text:
            return
        marker = f"{section}\n"
        if marker not in text:
            text = f"{text.rstrip()}\n\n{section}\n"
            marker = f"{section}\n"
        insert_at = text.index(marker) + len(marker)
        text = f"{text[:insert_at]}{line}\n{text[insert_at:]}"
        self.index_path.write_text(text, encoding="utf-8")


@dataclass(frozen=True)
class FailureMemoryPatternRecord:
    """Reusable second-tier pattern distilled from recovered step-action cases."""

    pattern_id: str
    label: str
    hop_index: int
    source_case_ids: tuple[str, ...]
    query_signature: str
    valid: bool
    review_required: bool
    operator_spec: OperatorSpec | None = None
    recovery_count: int = 0
    source_count: int = 0
    avg_recovery_gain: float = 0.0

    def __post_init__(self) -> None:
        validate_label(self.label)


class FailureMemorySkillLoop:
    """Case ledger -> reusable pattern loop for two-tier Failure Memory.

    The loop stores concrete recovered cases first, clustering them online by
    the content fingerprint of the memory items each failure hinged on. Once a
    cluster holds enough cases it formats and validates a markdown pattern, then
    exposes the pattern as a ``(generation_point, action)`` seed hint. Clustering
    by content (not by action label) keeps each pattern's signature short and
    chain-specific, so a later paraphrased query in the same failure family
    actually matches it.
    """

    def __init__(
        self,
        markdown_store: MarkdownFailureMemoryStore,
        *,
        threshold: int = 3,
        cluster_threshold: float = 0.5,
        skill: FailureMemorySkill | None = None,
    ) -> None:
        if threshold < 1:
            raise ValueError("threshold must be >= 1")
        if not 0.0 <= cluster_threshold <= 1.0:
            raise ValueError("cluster_threshold must be in [0, 1]")
        self.markdown_store = markdown_store
        self.threshold = threshold
        self.cluster_threshold = cluster_threshold
        self.skill = skill or FailureMemorySkill()
        self._clusters: list[dict] = []
        self._patterns: dict[str, FailureMemoryPatternRecord] = {}

    @property
    def pattern_count(self) -> int:
        return len(self._patterns)

    def load_patterns_from_disk(self) -> int:
        """Load executable markdown pattern specs into the in-memory index."""
        loaded = 0
        for record in self.markdown_store.read_pattern_records():
            self._patterns[record.pattern_id] = record
            loaded += 1
        return loaded

    def record_recovered_case(
        self,
        *,
        case_id: str,
        query: str,
        hop_index: int,
        label: str,
        cause: str,
        corrected_memory: str,
        repair_guidance: str,
        retrieved_items: tuple[str, ...] = (),
        memory_texts: tuple[str, ...] = (),
        recovery_gain: float = 0.0,
        operator_spec: OperatorSpec | None = None,
        llm_client=None,
    ) -> FailureMemoryPatternRecord | None:
        """Write a recovered case and update its content-cluster pattern.

        ``memory_texts`` are the texts of the memory items the failure hinged on;
        their content fingerprint is the recurrence identity used to cluster
        cases into reusable patterns. When omitted (e.g. unit tests), the query
        text is used as the fingerprint source so the call still works.
        """
        validate_label(label)
        diagnosis = FailureMemoryDiagnosis(
            query=query,
            label=label,
            cause=cause,
            corrected_memory=corrected_memory,
            repair_guidance=repair_guidance,
            retrieved_items=retrieved_items,
            operator_spec=operator_spec or OperatorSpec.single(
                hop_index - 1,
                PipelineAction(label),
            ),
        )
        outcome = FailureMemoryOutcome(
            assessment="recovered",
            recovered=True,
            recovery_gain=recovery_gain,
        )
        case_markdown = self.skill.format_case(diagnosis, outcome)
        self.markdown_store.write_case(
            case_id,
            case_markdown,
            summary=f"{label} at hop {hop_index}",
        )

        fingerprint = _memory_fingerprint(memory_texts or (query,))
        cluster = self._assign_cluster(label, fingerprint)
        cluster["cases"].append((
            case_id,
            case_markdown,
            hop_index,
            diagnosis.operator_spec,
            recovery_gain,
        ))
        cluster["texts"].extend(memory_texts or (query,))
        # Recompute the cluster signature from pooled text, capped at top_k so a
        # growing cluster does not blow the signature up into a near-universal
        # word set (the failure mode of the old per-label bucketing).
        cluster["fingerprint"] = _memory_fingerprint(tuple(cluster["texts"]))
        if len(cluster["cases"]) < self.threshold:
            return None
        return self._write_pattern(label, cluster, llm_client=llm_client)

    def _assign_cluster(self, label: str, fingerprint: str) -> dict:
        """Find the best same-label content cluster, or open a new one.

        A cluster is a failure family discovered online: cases whose memory
        fingerprints are similar (Jaccard >= ``cluster_threshold``) share a
        cluster and thus a pattern. This is what makes a pattern's signature
        short and chain-specific instead of a union over every same-action case.
        """
        best: dict | None = None
        best_sim = 0.0
        for cluster in self._clusters:
            if cluster["label"] != label:
                continue
            sim = _query_signature_similarity(cluster["fingerprint"], fingerprint)
            if sim > best_sim:
                best_sim = sim
                best = cluster
        if best is not None and best_sim >= self.cluster_threshold:
            return best
        cluster = {
            "label": label,
            "fingerprint": fingerprint,
            "cases": [],
            "texts": [],
            "pattern_id": f"pattern_{label}_{len(self._clusters)}",
        }
        self._clusters.append(cluster)
        return cluster

    def retrieve_seed_pairs(
        self,
        query: str,
        *,
        max_depth: int,
        top_k: int = 2,
        memory_texts: tuple[str, ...] = (),
    ) -> tuple[list[tuple[int, str]], int]:
        """Return pattern-derived ``(generation_point, action)`` seed pairs.

        Matching is by memory content fingerprint (paraphrase-invariant), with
        the query text used as the fingerprint source when ``memory_texts`` is
        omitted.
        """
        signature = _memory_fingerprint(memory_texts or (query,))
        scored: list[tuple[float, FailureMemoryPatternRecord]] = []
        for record in self._patterns.values():
            if record.review_required:
                continue
            score = _query_signature_similarity(record.query_signature, signature)
            if score > 0.0:
                scored.append((score, record))
        scored.sort(key=lambda item: item[0], reverse=True)

        pairs: list[tuple[int, str]] = []
        seen = set()
        for _score, record in scored:
            gen_point = record.hop_index - 1
            if not (0 <= gen_point < max_depth):
                continue
            pair = (gen_point, record.label)
            if pair in seen:
                continue
            pairs.append(pair)
            seen.add(pair)
            if len(pairs) >= top_k:
                break
        return pairs, len(scored)

    def retrieve_operator_specs(
        self,
        query: str,
        *,
        max_depth: int,
        top_k: int = 2,
        memory_texts: tuple[str, ...] = (),
    ) -> tuple[list[OperatorSpec], int]:
        """Return executable operator specs from matching skill patterns."""
        signature = _memory_fingerprint(memory_texts or (query,))
        scored: list[tuple[float, FailureMemoryPatternRecord]] = []
        for record in self._patterns.values():
            if record.review_required or record.operator_spec is None:
                continue
            score = _query_signature_similarity(record.query_signature, signature)
            if score > 0.0:
                scored.append((score, record))
        scored.sort(key=lambda item: item[0], reverse=True)

        specs: list[OperatorSpec] = []
        seen = set()
        for _score, record in scored:
            spec = record.operator_spec
            if not _operator_within_depth(spec, max_depth):
                continue
            key = spec.format()
            if key in seen:
                continue
            specs.append(spec)
            seen.add(key)
            if len(specs) >= top_k:
                break
        return specs, len(scored)

    def _write_pattern(
        self,
        label: str,
        cluster: dict,
        *,
        llm_client=None,
    ) -> FailureMemoryPatternRecord:
        bucket = cluster["cases"]
        case_ids = tuple(case_id for case_id, _case_md, _hop, _op, _gain in bucket)
        case_markdowns = tuple(case_md for _case_id, case_md, _hop, _op, _gain in bucket)
        hop_index = Counter(
            hop for _case_id, _case_md, hop, _op, _gain in bucket
        ).most_common(1)[0][0]
        operator_spec = _most_common_operator_spec(
            tuple(op for _case_id, _case_md, _hop, op, _gain in bucket)
        ) or OperatorSpec.single(hop_index - 1, PipelineAction(label))
        gains = [float(gain) for _case_id, _case_md, _hop, _op, gain in bucket]

        pattern_markdown = self.skill.format_pattern(
            case_markdowns,
            trigger_fingerprint=cluster["fingerprint"],
            source_case_ids=case_ids,
            operator_spec=operator_spec,
            recovery_track={
                "recovered": len(bucket),
                "total": len(bucket),
                "avg_recovery_gain": sum(gains) / len(gains) if gains else 0.0,
            },
            llm_client=llm_client,
        )
        validation = self.skill.validate_pattern(
            pattern_markdown,
            case_markdowns,
            llm_client=llm_client,
        )
        if not validation.valid:
            notes = "\n".join(f"- {item}" for item in validation.inconsistencies)
            pattern_markdown = "\n".join((
                pattern_markdown.rstrip(),
                "",
                "## Review Required",
                notes or validation.suggested_update or "- Pattern validation failed.",
                "",
            ))

        pattern_id = cluster["pattern_id"]
        self.markdown_store.write_pattern(
            pattern_id,
            pattern_markdown,
            summary=(
                f"{label}, {len(bucket)} source cases, "
                f"valid={str(validation.valid).lower()}"
            ),
        )
        record = FailureMemoryPatternRecord(
            pattern_id=pattern_id,
            label=label,
            hop_index=hop_index,
            source_case_ids=case_ids,
            query_signature=cluster["fingerprint"],
            valid=validation.valid,
            review_required=not validation.valid,
            operator_spec=operator_spec,
            recovery_count=len(bucket),
            source_count=len(bucket),
            avg_recovery_gain=sum(gains) / len(gains) if gains else 0.0,
        )
        self._patterns[pattern_id] = record
        return record


def _extract_case_label(case_markdown: str) -> str:
    match = re.search(r"\*\*Label\*\*:\s*([a-z_]+)", case_markdown)
    return match.group(1) if match else ""


def _pattern_record_from_markdown(
    pattern_id: str,
    pattern_markdown: str,
) -> FailureMemoryPatternRecord | None:
    label = _extract_pattern_label(pattern_markdown)
    if not label:
        return None
    try:
        validate_label(label)
    except ValueError:
        return None

    operator_spec = _operator_spec_from_markdown(pattern_markdown)
    if operator_spec is None:
        return None

    source_case_ids = _extract_source_case_ids(pattern_markdown)
    recovery_count, source_count = _extract_recovery_counts(
        pattern_markdown,
        default_total=len(source_case_ids),
    )
    avg_gain = _extract_average_recovery_gain(pattern_markdown)
    review_required = "## Review Required" in pattern_markdown
    first_step = operator_spec.steps[0] if operator_spec.steps else None
    hop_index = first_step.generation_point + 1 if first_step is not None else 1
    return FailureMemoryPatternRecord(
        pattern_id=pattern_id,
        label=label,
        hop_index=hop_index,
        source_case_ids=source_case_ids,
        query_signature=_extract_trigger_fingerprint(pattern_markdown),
        valid=not review_required,
        review_required=review_required,
        operator_spec=operator_spec,
        recovery_count=recovery_count,
        source_count=source_count,
        avg_recovery_gain=avg_gain,
    )


def _operator_spec_from_markdown(pattern_markdown: str) -> OperatorSpec | None:
    match = re.search(
        r"```operator-spec\s*(?P<body>.*?)```",
        pattern_markdown,
        flags=re.DOTALL,
    )
    if not match:
        return None
    body = match.group("body")
    steps: list[dict[str, object]] = []
    hints: dict[str, float] = {}

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("-"):
            line = line[1:].strip()
        if line.startswith("params.item_signal_hints="):
            hints.update(_parse_item_signal_hints(line.split("=", 1)[1]))
            continue
        fields = dict(re.findall(r"([A-Za-z_.]+)=([^\s]+)", line))
        if not fields or "action" not in fields:
            continue
        try:
            step = {
                "hop_index": int(fields.get("hop", fields.get("hop_index", "1"))),
                "action": fields["action"],
                "select": fields.get("select", fields.get("selector", "")),
                "transform": fields.get("transform", ""),
            }
            OperatorSpec.from_dict({"steps": (step,)})
        except (ValueError, TypeError):
            return None
        steps.append(step)

    if not steps:
        return None
    try:
        return OperatorSpec.from_dict(
            {
                "steps": steps,
                "params": {"item_signal_hints": hints},
            }
        )
    except (ValueError, TypeError):
        return None


def _parse_item_signal_hints(raw: str) -> dict[str, float]:
    hints: dict[str, float] = {}
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        if ":" in item:
            memory_id, weight = item.split(":", 1)
        elif "=" in item:
            memory_id, weight = item.split("=", 1)
        else:
            continue
        memory_id = memory_id.strip()
        if not memory_id:
            continue
        try:
            hints[memory_id] = float(weight)
        except ValueError:
            continue
    return hints


def _extract_trigger_fingerprint(pattern_markdown: str) -> str:
    match = re.search(
        r"\*\*Trigger Fingerprint\*\*:\s*(.+)",
        pattern_markdown,
    )
    return match.group(1).strip() if match else ""


def _extract_source_case_ids(pattern_markdown: str) -> tuple[str, ...]:
    match = re.search(
        r"\*\*Source Cases\*\*:\s*(?P<body>(?:\n- .+)+)",
        pattern_markdown,
    )
    if not match:
        return ()
    return tuple(
        line.strip()[2:].strip()
        for line in match.group("body").splitlines()
        if line.strip().startswith("- ")
    )


def _extract_recovery_counts(
    pattern_markdown: str,
    *,
    default_total: int,
) -> tuple[int, int]:
    match = re.search(
        r"Recovered source cases:\s*(\d+)\s*/\s*(\d+)",
        pattern_markdown,
    )
    if not match:
        return default_total, default_total
    return int(match.group(1)), int(match.group(2))


def _extract_average_recovery_gain(pattern_markdown: str) -> float:
    match = re.search(
        r"Average recovery gain:\s*([-+]?\d+(?:\.\d+)?)",
        pattern_markdown,
    )
    if not match:
        return 0.0
    return float(match.group(1))


def _extract_pattern_label(pattern_markdown: str) -> str:
    match = re.search(r"\*\*Diagnosis\*\*:\s*([a-z_]+)", pattern_markdown)
    return match.group(1) if match else ""


def _operator_within_depth(operator_spec: OperatorSpec, max_depth: int) -> bool:
    return all(0 <= step.generation_point < max_depth for step in operator_spec.steps)


def _most_common_operator_spec(
    operator_specs: tuple[OperatorSpec | None, ...],
) -> OperatorSpec | None:
    counts: Counter[str] = Counter(
        spec.format() for spec in operator_specs if spec is not None
    )
    if not counts:
        return None
    selected = counts.most_common(1)[0][0]
    for spec in operator_specs:
        if spec is not None and spec.format() == selected:
            return spec
    return None


# ── Step-Level Failure Memory (TASK.md #6) ─────────────────────────────────


@dataclass(frozen=True)
class StepLevelKey:
    """Composite retrieval key for step-level transfer: (query_signature, hop_index, label)."""

    query_signature: str
    hop_index: int
    label: str

    def __post_init__(self) -> None:
        validate_label(self.label)

    @classmethod
    def from_components(
        cls,
        query: str,
        hop_index: int,
        label: str,
        *,
        memory_texts: tuple[str, ...] = (),
    ) -> "StepLevelKey":
        return cls(
            query_signature=_signature_from(query, memory_texts),
            hop_index=hop_index,
            label=label,
        )

    def similarity(self, other: "StepLevelKey") -> float:
        """Compute similarity score between two keys."""
        # Hop distance penalty
        hop_penalty = 1.0 / (1.0 + abs(self.hop_index - other.hop_index))

        # Keyword overlap
        self_kw = set(self.query_signature.split())
        other_kw = set(other.query_signature.split())
        if not self_kw or not other_kw:
            return 0.0
        overlap = len(self_kw & other_kw) / max(len(self_kw), len(other_kw))

        score = overlap * hop_penalty
        if self.label == other.label:
            score *= 1.25
        return score


@dataclass(frozen=True)
class StepLevelRecord:
    """Failure memory record with step-level key for attribution experience transfer."""

    key: StepLevelKey
    error_type: str
    cause: str
    corrected_memory: str
    repair_guidance: str
    recovery_success: bool
    recovery_gain: float
    operator_spec: OperatorSpec | None = None

    @classmethod
    def from_mcts_result(
        cls,
        query: str,
        hop_index: int,
        label: str,
        cause: str,
        corrected_memory: str,
        repair_guidance: str,
        recovery_success: bool,
        recovery_gain: float,
        *,
        memory_texts: tuple[str, ...] = (),
        operator_spec: OperatorSpec | None = None,
    ) -> "StepLevelRecord":
        if operator_spec is None:
            operator_spec = OperatorSpec.single(hop_index - 1, PipelineAction(label))
        key = StepLevelKey.from_components(
            query, hop_index, label, memory_texts=memory_texts
        )
        return cls(
            key=key,
            error_type=label,
            cause=cause,
            corrected_memory=corrected_memory,
            repair_guidance=repair_guidance,
            recovery_success=recovery_success,
            recovery_gain=recovery_gain,
            operator_spec=operator_spec,
        )


def step_level_record_from_mcts_result(
    query: str,
    mcts_result,
    *,
    recovery_success_threshold: float = 0.0,
    memory_texts: tuple[str, ...] = (),
) -> StepLevelRecord | None:
    """Convert a SearchResult into a step-level Failure Memory record."""
    culprit = getattr(mcts_result, "main_culprit", None)
    if culprit is None:
        return None

    generation_point, action, credit = culprit
    label = action.value if hasattr(action, "value") else str(action)
    if label == "identity":
        return None
    hop_index = int(generation_point) + 1
    recovery_gain = float(credit)
    return StepLevelRecord.from_mcts_result(
        query=query,
        hop_index=int(hop_index),
        label=label,
        cause=f"Single-point attribution recovered with {label} at hop {hop_index}.",
        corrected_memory="",
        repair_guidance=(
            f"Prioritize {label} repairs when a similar query reaches hop {hop_index}."
        ),
        recovery_success=recovery_gain > recovery_success_threshold,
        recovery_gain=recovery_gain,
        operator_spec=OperatorSpec.single(int(generation_point), PipelineAction(label)),
        memory_texts=memory_texts,
    )


class FailureMemoryStore:
    """Step-level failure memory store for experience reuse.

    Retrieval key: (query_signature, hop_index, label)
    Used by:
    - Hook: similar signature history → adjust confidence
    - Attribution: historical (signature → label) success rate → action prior
    - LOO: historical item label hints → priority ordering
    """

    def __init__(self) -> None:
        self._records: list[StepLevelRecord | FailureMemoryRecord] = []
        self._label_success_rates: dict[str, tuple[int, int]] = {}  # label -> (success, total)
        self._governance = OperatorGovernance()

    def add(self, record: StepLevelRecord | FailureMemoryRecord) -> "FailureMemoryStore":
        """Add a record and update success rate statistics."""
        self._records.append(record)

        label = record.error_type
        if isinstance(record, StepLevelRecord):
            success, total = self._label_success_rates.get(label, (0, 0))
            if record.recovery_success:
                success += 1
            total += 1
            self._label_success_rates[label] = (success, total)
        return self

    def add_if_recovered(
        self,
        record: StepLevelRecord | FailureMemoryRecord,
        assessment: str,
    ) -> "FailureMemoryStore":
        """Store only records whose repair outcome recovered the task."""
        if assessment == "recovered":
            return self.add(record)
        return self

    def add_mcts_result(
        self,
        query: str,
        mcts_result,
        *,
        recovery_success_threshold: float = 0.0,
        memory_texts: tuple[str, ...] = (),
    ) -> StepLevelRecord | None:
        """Persist step-level attribution experience for future reuse."""
        record = step_level_record_from_mcts_result(
            query,
            mcts_result,
            recovery_success_threshold=recovery_success_threshold,
            memory_texts=memory_texts,
        )
        if record is None:
            return None
        if not record.recovery_success:
            return None
        self.add(record)
        return record

    def add_attribution_result(
        self,
        query: str,
        attribution_result,
        *,
        recovery_success_threshold: float = 0.0,
        memory_texts: tuple[str, ...] = (),
    ) -> StepLevelRecord | None:
        """Compatibility alias for storing live attribution experience."""
        return self.add_mcts_result(
            query,
            attribution_result,
            recovery_success_threshold=recovery_success_threshold,
            memory_texts=memory_texts,
        )

    def retrieve(
        self,
        query: str,
        hop_index: int | str | None = None,
        label: str | None = None,
        top_k: int = 3,
        *,
        memory_texts: tuple[str, ...] = (),
    ) -> list[StepLevelRecord | FailureMemoryRecord]:
        """Retrieve similar records by step-level key.

        ``memory_texts`` keys similarity by the recall content fingerprint
        instead of query keywords when supplied.
        """
        if isinstance(hop_index, str) and label is None:
            label = hop_index
            hop_index = None

        if hop_index is None:
            probe_signature = _signature_from(query, memory_texts)
            scored: list[tuple[float, StepLevelRecord | FailureMemoryRecord]] = []
            for record in self._records:
                if isinstance(record, FailureMemoryRecord):
                    score = float(_score_composite_key(record, query, label or ""))
                else:
                    probe_keywords = set(probe_signature.split())
                    record_keywords = set(record.key.query_signature.split())
                    score = (
                        len(probe_keywords & record_keywords)
                        / max(len(probe_keywords), len(record_keywords))
                        if probe_keywords and record_keywords
                        else 0.0
                    )
                    if label and record.error_type == label:
                        score += 1.0
                if score > 0:
                    scored.append((score, record))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [record for _, record in scored[:top_k]]

        if label:
            probe_key = StepLevelKey.from_components(
                query, hop_index, label, memory_texts=memory_texts
            )
            scored = [
                (probe_key.similarity(r.key), r)
                for r in self._records
                if isinstance(r, StepLevelRecord)
            ]
        else:
            scored = [
                (
                    _step_record_similarity(
                        r, query, int(hop_index), memory_texts=memory_texts
                    ),
                    r,
                )
                for r in self._records
                if isinstance(r, StepLevelRecord)
            ]

        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:top_k] if _ > 0]

    def get_label_prior(
        self,
        label: str,
        *,
        query: str | None = None,
        hop_index: int | None = None,
        memory_texts: tuple[str, ...] = (),
    ) -> float:
        """Get historical success rate for a label.

        With no query, this returns the global success rate used by older
        callers. With a query/hop, it returns the similar-case weighted prior
        used by online action ordering. ``memory_texts`` keys similarity by the
        recall content fingerprint instead of query keywords when supplied.
        """
        if query is not None:
            weighted_success = 0.0
            total_weight = 0.0
            for record in self._records:
                if not isinstance(record, StepLevelRecord):
                    continue
                if record.error_type != label:
                    continue
                weight = _step_record_similarity(
                    record, query, hop_index, memory_texts=memory_texts
                )
                if weight <= 0.0:
                    continue
                weighted_success += weight * (1.0 if record.recovery_success else 0.0)
                total_weight += weight
            if total_weight > 0.0:
                return weighted_success / total_weight
            return 0.5

        success, total = self._label_success_rates.get(label, (0, 0))
        if total == 0:
            return 0.5  # Neutral prior
        return success / total

    def get_label_priors(self) -> dict[str, float]:
        """Get all label priors for step-level attribution."""
        return {label: self.get_label_prior(label) for label in self._label_success_rates}

    def get_mcts_action_priors(
        self,
        query: str,
        *,
        hop_index: int | None = None,
        labels: tuple[str, ...] = PIPELINE_STEP_ACTIONS,
        memory_texts: tuple[str, ...] = (),
    ) -> dict[str, float]:
        """Return query-aware action priors for step-level attribution.

        Priors are soft hints only: callers should use them for ordering/bonus,
        never as hard pruning. ``memory_texts`` keys similarity by the recall
        content fingerprint instead of query keywords when supplied.
        """
        return {
            label: self.get_label_prior(
                label, query=query, hop_index=hop_index, memory_texts=memory_texts
            )
            for label in labels
        }

    def retrieve_operator_specs(
        self,
        query: str,
        *,
        max_depth: int,
        top_k: int = 2,
        memory_texts: tuple[str, ...] = (),
    ) -> tuple[list[OperatorSpec], int]:
        """Return executable operator specs from similar recovered records."""
        records = self.retrieve(
            query=query,
            top_k=max(top_k * 3, top_k),
            memory_texts=memory_texts,
        )
        fingerprint = _signature_from(query, memory_texts)
        specs: list[OperatorSpec] = list(
            self._governance.active_operators(fingerprint)
        )
        seen = {spec.content_hash() for spec in specs}
        for record in records:
            if not isinstance(record, StepLevelRecord):
                continue
            if not record.recovery_success:
                continue
            spec = record.operator_spec or OperatorSpec.single(
                record.key.hop_index - 1,
                PipelineAction(record.error_type),
            )
            if not _operator_within_depth(spec, max_depth):
                continue
            key = spec.content_hash()
            if key in seen:
                continue
            specs.append(spec)
            seen.add(key)
            if len(specs) >= top_k:
                break
        return specs[:top_k], len(records)

    def admit_with_cluster_replay(
        self,
        query: str,
        operator_spec: OperatorSpec,
        replay_gains: tuple[float, ...],
        *,
        memory_texts: tuple[str, ...] = (),
        generation: int = 0,
    ) -> GovernanceDecision:
        """Run the A4 replay/CI/dedup/cap gate for one operator."""
        fingerprint = _signature_from(query, memory_texts)
        return self._governance.admit_with_cluster_replay(
            fingerprint,
            operator_spec,
            replay_gains,
            generation=generation,
        )

    def record_operator_outcome(
        self,
        query: str,
        operator_spec: OperatorSpec,
        *,
        succeeded: bool,
        generation: int,
        memory_texts: tuple[str, ...] = (),
    ):
        """Update the governed operator evidence ledger after live use."""
        fingerprint = _signature_from(query, memory_texts)
        return self._governance.record_application(
            fingerprint,
            operator_spec.content_hash(),
            succeeded=succeeded,
            generation=generation,
        )

    @property
    def governance(self) -> OperatorGovernance:
        """Expose the auditable A4 ledger without leaking mutable records."""
        return self._governance

    def get_hook_confidence_bonus(
        self,
        query: str,
        *,
        max_bonus: float = 0.15,
    ) -> float:
        """Return a small confidence bonus from similar recovered diagnoses."""
        records = self.retrieve(query=query, top_k=5)
        if not records:
            return 0.0

        weighted_success = 0.0
        total_weight = 0.0
        for record in records:
            weight = _diagnosis_record_similarity(record, query)
            if weight <= 0.0:
                continue
            if isinstance(record, StepLevelRecord):
                success = 1.0 if record.recovery_success else 0.0
            else:
                # FailureMemoryRecord is only added to the online store after a
                # repair path has been accepted by the caller.
                success = 1.0
            weighted_success += weight * success
            total_weight += weight

        if total_weight <= 0.0:
            return 0.0
        return max(0.0, min(max_bonus, max_bonus * (weighted_success / total_weight)))

    def score_item_priority(self, query: str, item) -> float:
        """Score a recalled item for item-gate/LOO priority from past item records."""
        item_keywords = set(_extract_keywords(getattr(item, "text", str(item))))
        if not item_keywords:
            return 0.0

        best_score = 0.0
        for record in self._records:
            if record.error_type not in ITEM_LABELS:
                continue
            query_score = _diagnosis_record_similarity(record, query)
            memory_terms = set(getattr(record, "memory_top_terms", ()) or ())
            if not memory_terms and isinstance(record, FailureMemoryRecord):
                memory_terms = set(_extract_keywords(record.wrong_memory))
            if not memory_terms:
                continue
            item_score = len(item_keywords & memory_terms) / max(
                len(item_keywords), len(memory_terms)
            )
            best_score = max(best_score, query_score + item_score)
        return best_score

    def get_repair_guidance(
        self,
        query: str,
        label: str,
        *,
        hop_index: int | None = None,
    ) -> str:
        """Retrieve reusable repair guidance for ECS-S assembly."""
        records = self.retrieve(query=query, hop_index=hop_index, label=label, top_k=5)
        best_guidance = ""
        best_score = 0.0
        for record in records:
            if record.error_type != label:
                continue
            guidance = getattr(record, "repair_guidance", "")
            if not guidance:
                continue
            if isinstance(record, StepLevelRecord):
                score = _step_record_similarity(record, query, hop_index)
                if record.recovery_success:
                    score += 1.0
            else:
                score = _diagnosis_record_similarity(record, query) + 1.0
            if score > best_score:
                best_score = score
                best_guidance = guidance
        return best_guidance

    def __len__(self) -> int:
        return len(self._records)

    def __bool__(self) -> bool:
        return len(self._records) > 0


def _query_signature_similarity(left: str, right: str) -> float:
    left_kw = set(left.split())
    right_kw = set(right.split())
    if not left_kw or not right_kw:
        return 0.0
    return len(left_kw & right_kw) / max(len(left_kw), len(right_kw))


@dataclass(frozen=True)
class FrozenPatternCatalog:
    """Gold-free Pattern prototypes frozen before the evolution run."""

    catalog_version: str
    catalog_hash: str
    patterns: tuple[PatternRecord, ...]

    def match(self, fingerprint: str, *, top_k: int = 5) -> tuple[PatternRecord, ...]:
        """Return deterministic top-k prototype matches without audit metadata."""
        if top_k < 1:
            return ()
        scored = sorted(
            (
                (_query_signature_similarity(fingerprint, item.canonical_fingerprint), item)
                for item in self.patterns
            ),
            key=lambda value: (
                -value[0],
                value[1].pattern_id,
            ),
        )
        return tuple(item for score, item in scored[:top_k] if score > 0.0)

    def to_json(self) -> dict[str, Any]:
        return {
            "catalog_version": self.catalog_version,
            "catalog_hash": self.catalog_hash,
            "patterns": [
                {
                    "pattern_id": item.pattern_id,
                    "prototype_hash": item.prototype_hash,
                    "canonical_fingerprint": item.canonical_fingerprint,
                    "gold_free_feature_hash": item.gold_free_feature_hash,
                    "catalog_version": item.catalog_version,
                    "linked_family_ids": list(item.linked_family_ids),
                    "audit_counters": dict(item.audit_counters),
                }
                for item in self.patterns
            ],
        }


def bootstrap_frozen_pattern_catalog(
    rows: Iterable[Mapping[str, Any]],
    *,
    catalog_version: str = "pattern-catalog-v1",
) -> FrozenPatternCatalog:
    """Bootstrap prototypes from represented-family variant zero only.

    Gold, injected labels, and family identifiers are used only to select the
    preregistered split/variant.  They never enter a prototype or feature hash.
    """
    fingerprints: dict[str, dict[str, Any]] = {}
    for row in rows:
        family_id = str(row.get("recurrent_family_id") or "")
        if not family_id:
            raise ValueError("pattern bootstrap requires recurrent_family_id")
        try:
            variant_index = int(row["recurrent_variant_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid recurrent_variant_index") from exc
        bucket = int(hashlib.sha256(family_id.encode("utf-8")).hexdigest(), 16) % 5
        if bucket == 0 or variant_index != 0:
            continue
        items = row.get("extracted_memory") or ()
        texts = tuple(
            str(item.get("text") or "")
            for item in items
            if isinstance(item, Mapping)
        )
        fingerprint = _memory_fingerprint(texts)
        if not fingerprint:
            continue
        feature_payload = {
            "fingerprint": fingerprint,
            "recall_size": len(texts),
            "trajectory_kind": str(row.get("trajectory_kind") or ""),
            "source": str(row.get("source") or ""),
        }
        fingerprints.setdefault(fingerprint, feature_payload)
    patterns: list[PatternRecord] = []
    for fingerprint in sorted(fingerprints):
        feature_payload = fingerprints[fingerprint]
        prototype_hash = hash_text(fingerprint)
        pattern_id = content_id(
            "pattern",
            [catalog_version, prototype_hash, feature_payload],
        )
        patterns.append(
            PatternRecord(
                pattern_id=pattern_id,
                prototype_hash=prototype_hash,
                canonical_fingerprint=fingerprint,
                gold_free_feature_hash=hash_text(canonical_json(feature_payload)),
                catalog_version=catalog_version,
            )
        )
    payload = [
        {
            "pattern_id": item.pattern_id,
            "prototype_hash": item.prototype_hash,
            "canonical_fingerprint": item.canonical_fingerprint,
            "gold_free_feature_hash": item.gold_free_feature_hash,
            "catalog_version": item.catalog_version,
        }
        for item in patterns
    ]
    return FrozenPatternCatalog(
        catalog_version=catalog_version,
        catalog_hash=hash_text(canonical_json(payload)),
        patterns=tuple(patterns),
    )


def _step_record_similarity(
    record: StepLevelRecord,
    query: str,
    hop_index: int | None = None,
    *,
    memory_texts: tuple[str, ...] = (),
) -> float:
    probe_signature = _signature_from(query, memory_texts)
    query_score = _query_signature_similarity(record.key.query_signature, probe_signature)
    if hop_index is None:
        return query_score
    hop_score = 1.0 / (1.0 + abs(record.key.hop_index - hop_index))
    return query_score * hop_score


def _diagnosis_record_similarity(
    record: StepLevelRecord | FailureMemoryRecord,
    query: str,
) -> float:
    if isinstance(record, StepLevelRecord):
        return _step_record_similarity(record, query)
    query_keywords = set(_extract_keywords(query))
    sig_keywords = set(record.trigger_signature.casefold().split())
    if not query_keywords or not sig_keywords:
        return 0.0
    return len(query_keywords & sig_keywords) / max(len(query_keywords), len(sig_keywords))
