"""Experiment 01 data loader: construct 5-mode Context Cases from probe cases.

Pipeline (per experiment_01_context_construction.md):

    ProbeCase -> CMD-Audit V1 pipeline -> ECSDraft
        -> extract wrong_memory, cause, corrected_memory, repair_guidance
        -> render 5 context prompt strings
           (none/full_trace/corrected_only/corrected_only_padded/contrastive)
        -> ContextCase

Pre-check (none mode must fail) is left to the experiment runner since it
requires actual LLM calls.  Cases whose baseline already scores 1.0 are
flagged via `baseline_already_correct`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from math import comb
from pathlib import Path
from typing import Any

from cmd_audit.core.models import ProbeCase
from cmd_audit.data_io import load_probe_cases_v1


EXPERIMENT_01_CONTEXT_MODES = (
    "none",
    "full_trace",
    "corrected_only",
    "corrected_only_padded",
    "contrastive",
)


# ── Data type ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ContextCase:
    """One 5-mode Context Case ready for LLM evaluation.

    The pre-rendered context strings in ``contexts`` are designed to be
    dropped into the experiment's fixed prompt template:

        [System: Answer the question based ONLY on the provided context...]

        {contexts[mode]}

        Answer:
    """

    case_id: str
    query: str
    gold_answer: str
    gold_evidence_phrases: tuple[str, ...]
    failure_type: str
    wrong_memory: str
    cause: str
    corrected_memory: str
    repair_guidance: str
    contexts: dict[str, str] = field(default_factory=dict)
    baseline_already_correct: bool = False

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "ContextCase":
        return cls(
            case_id=_require_str(value, "case_id"),
            query=_require_str(value, "query"),
            gold_answer=_require_str(value, "gold_answer"),
            gold_evidence_phrases=tuple(value.get("gold_evidence_phrases", ())),
            failure_type=_require_str(value, "failure_type"),
            wrong_memory=_require_str(value, "wrong_memory"),
            cause=_require_str(value, "cause"),
            corrected_memory=_require_str(value, "corrected_memory"),
            repair_guidance=_require_str(value, "repair_guidance"),
            contexts={str(k): str(v) for k, v in value.get("contexts", {}).items()},
            baseline_already_correct=bool(value.get("baseline_already_correct", False)),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "query": self.query,
            "gold_answer": self.gold_answer,
            "gold_evidence_phrases": list(self.gold_evidence_phrases),
            "failure_type": self.failure_type,
            "wrong_memory": self.wrong_memory,
            "cause": self.cause,
            "corrected_memory": self.corrected_memory,
            "repair_guidance": self.repair_guidance,
            "contexts": dict(self.contexts),
            "baseline_already_correct": self.baseline_already_correct,
        }


# ── Context string renderers ─────────────────────────────────────────────


def _render_none(query: str) -> str:
    return f"Query: {query}"


def _render_full_trace(wrong_memory: str, query: str) -> str:
    return f"[Past Failure Trace 1]\n{wrong_memory}\n\nQuery: {query}"


def _render_corrected_only(
    corrected_memory: str, repair_guidance: str, query: str
) -> str:
    return (
        f"[Failure Memory Guidance 1]\n"
        f"Corrected: {corrected_memory}\n"
        f"Guidance: {repair_guidance}\n\n"
        f"Query: {query}"
    )


def _render_corrected_only_padded(
    corrected_memory: str,
    repair_guidance: str,
    query: str,
    *,
    target_text: str,
) -> str:
    base = _render_corrected_only(corrected_memory, repair_guidance, query)
    return _pad_to_token_count(base, _token_count(target_text))


def _render_contrastive(
    wrong_memory: str,
    cause: str,
    corrected_memory: str,
    repair_guidance: str,
    query: str,
) -> str:
    return (
        f"[Failure Memory Guidance 1]\n"
        f"Previously wrong: {wrong_memory}\n"
        f"Cause: {cause}\n"
        f"Corrected: {corrected_memory}\n"
        f"Guidance: {repair_guidance}\n\n"
        f"Query: {query}"
    )


def _render_contexts(
    query: str,
    wrong_memory: str,
    cause: str,
    corrected_memory: str,
    repair_guidance: str,
) -> dict[str, str]:
    contrastive = _render_contrastive(
        wrong_memory, cause, corrected_memory, repair_guidance, query
    )
    return {
        "none": _render_none(query),
        "full_trace": _render_full_trace(wrong_memory, query),
        "corrected_only": _render_corrected_only(
            corrected_memory, repair_guidance, query
        ),
        "corrected_only_padded": _render_corrected_only_padded(
            corrected_memory,
            repair_guidance,
            query,
            target_text=contrastive,
        ),
        "contrastive": contrastive,
    }


def _token_count(text: str) -> int:
    return len(text.split())


def _pad_to_token_count(text: str, target_tokens: int) -> str:
    current = _token_count(text)
    if current >= target_tokens:
        return text
    filler_tokens = "neutral padding token".split()
    needed = target_tokens - current
    repeated = (filler_tokens * ((needed // len(filler_tokens)) + 1))[:needed]
    return text + "\n\n[Neutral Padding]\n" + " ".join(repeated)


# ── Construction ─────────────────────────────────────────────────────────


def build_context_cases(
    probe_case_paths: list[str | Path],
    *,
    require_none_fails: bool = False,
) -> list[ContextCase]:
    """Build 5-mode Context Cases from probe case JSON files.

    Each probe case is run through the CMD-Audit V1 pipeline to produce an
    ECS draft, then the five Decision 34 context modes are rendered.

    If *require_none_fails* is True, cases whose primary baseline already
    scores answer_score == 1.0 are excluded (they do not need Failure Memory).
    The default (False) keeps all cases and flags them with
    ``baseline_already_correct`` instead.
    """
    cases: list[ContextCase] = []

    for path in probe_case_paths:
        probe_cases = load_probe_cases_v1(Path(path))
        for pc in probe_cases:
            ctx_case = _build_one(pc)
            if require_none_fails and ctx_case.baseline_already_correct:
                continue
            cases.append(ctx_case)

    return cases


def _build_one(pc: ProbeCase) -> ContextCase:
    full = run_case(pc, post_repair=True)
    ecs = full.ecs_draft
    baseline = pc.primary_baseline

    wrong_memory = baseline.injected_context
    cause = ecs.cause
    corrected_memory = ecs.corrected_memory
    repair_guidance = ecs.repair_guidance

    gold_evidence_phrases: tuple[str, ...] = ()
    for evidence in pc.gold_evidence:
        gold_evidence_phrases = gold_evidence_phrases + (
            tuple(evidence.required_phrases)
            if evidence.required_phrases
            else (evidence.text,)
        )

    contexts = _render_contexts(
        query=pc.query,
        wrong_memory=wrong_memory,
        cause=cause,
        corrected_memory=corrected_memory,
        repair_guidance=repair_guidance,
    )

    return ContextCase(
        case_id=pc.case_id,
        query=pc.query,
        gold_answer=pc.gold_answer,
        gold_evidence_phrases=gold_evidence_phrases,
        failure_type=pc.perturbation_label,
        wrong_memory=wrong_memory,
        cause=cause,
        corrected_memory=corrected_memory,
        repair_guidance=repair_guidance,
        contexts=contexts,
        baseline_already_correct=baseline.answer_score == 1.0,
    )


# ── I/O ──────────────────────────────────────────────────────────────────


def save_context_cases(cases: list[ContextCase], path: str | Path) -> None:
    """Write ContextCase list to a JSON file."""
    output = Path(path)
    output.write_text(
        json.dumps(
            [c.to_mapping() for c in cases],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def load_context_cases(path: str | Path) -> list[ContextCase]:
    """Load ContextCase list from a JSON file."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        items = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError("ContextCase JSON must contain an object or a list of objects")
    return [ContextCase.from_mapping(item) for item in items]


def _require_str(value: dict[str, Any], key: str) -> str:
    raw = value[key]
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"field {key!r} must be a non-empty string")
    return raw


# ── Experiment 1 statistics ─────────────────────────────────────────────


@dataclass(frozen=True)
class McNemarResult:
    mode_a: str
    mode_b: str
    n01: int
    n10: int
    statistic: float
    p_value: float


def compute_mcnemar(
    mode_a_correct: list[bool],
    mode_b_correct: list[bool],
    *,
    mode_a: str,
    mode_b: str,
) -> McNemarResult:
    """Compute exact two-sided McNemar test for paired binary outcomes.

    ``n01`` counts cases where mode A is wrong and mode B is correct.
    ``n10`` counts cases where mode A is correct and mode B is wrong.
    """
    if len(mode_a_correct) != len(mode_b_correct):
        raise ValueError("McNemar inputs must have equal length")
    n01 = sum((not a) and b for a, b in zip(mode_a_correct, mode_b_correct))
    n10 = sum(a and (not b) for a, b in zip(mode_a_correct, mode_b_correct))
    discordant = n01 + n10
    if discordant == 0:
        return McNemarResult(mode_a, mode_b, n01, n10, 0.0, 1.0)
    statistic = ((abs(n01 - n10) - 1) ** 2) / discordant
    tail = sum(
        comb(discordant, k) * (0.5**discordant)
        for k in range(0, min(n01, n10) + 1)
    )
    p_value = min(1.0, 2.0 * tail)
    return McNemarResult(mode_a, mode_b, n01, n10, statistic, p_value)
