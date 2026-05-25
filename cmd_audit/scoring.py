"""Small deterministic scorers for synthetic CMD probes."""

from __future__ import annotations

import re

from .models import GoldEvidence, ProbeCase


def answer_score(answer: str, gold_answer: str) -> float:
    """Casefolded exact match with punctuation trimming."""

    return 1.0 if _normalize(answer) == _normalize(gold_answer) else 0.0


def evidence_recall_from_memory_ids(
    case: ProbeCase, memory_ids: tuple[str, ...]
) -> float:
    required = {
        evidence.source_memory_id
        for evidence in case.gold_evidence
        if evidence.source_memory_id is not None
    }
    if not required:
        return 0.0
    return len(required.intersection(memory_ids)) / len(required)


def evidence_recall_from_text(
    gold_evidence: tuple[GoldEvidence, ...], text: str
) -> float:
    if not gold_evidence:
        return 0.0

    normalized_text = text.casefold()
    matched = 0
    for evidence in gold_evidence:
        phrases = evidence.required_phrases or (evidence.text,)
        if all(phrase.casefold() in normalized_text for phrase in phrases):
            matched += 1
    return matched / len(gold_evidence)


def _normalize(value: str) -> str:
    lowered = value.casefold().strip()
    lowered = re.sub(r"^[^\w]+|[^\w]+$", "", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered
