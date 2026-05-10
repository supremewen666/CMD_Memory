"""Counterfactual replay primitives for CMD-Audit V0."""

from __future__ import annotations

from dataclasses import dataclass

from .models import ProbeCase
from .scoring import answer_score, evidence_recall_from_text


@dataclass(frozen=True)
class ReplayResult:
    replay_name: str
    answer: str
    answer_score: float
    evidence_score: float
    evidence_block: str
    recovery_gain: float
    cost_units: float = 1.0


def run_v0_replay_portfolio(case: ProbeCase) -> tuple[ReplayResult, ...]:
    """Run the currently implemented V0 replay portfolio for one case."""

    return (
        run_oracle_write(case),
        run_oracle_compression(case),
        run_verbatim_event_oracle(case),
        run_oracle_retrieval(case),
        run_injection_oracle(case),
        run_evidence_given_reasoning(case),
    )


def run_oracle_write(case: ProbeCase) -> ReplayResult:
    """Replay with gold evidence injected as newly written memory."""

    evidence_block = "\n".join(
        evidence.text
        for evidence in case.gold_evidence
        if evidence.source_memory_id is None and evidence.source_event_id is None
    )
    return _score_recovered_evidence(case, "oracle_write", evidence_block)


def run_oracle_compression(case: ProbeCase) -> ReplayResult:
    """Replay with a corrected memory representation after lossy compression."""

    memory_by_id = {item.memory_id: item for item in case.extracted_memory}
    recovered = []
    for evidence in case.gold_evidence:
        if not evidence.source_memory_id:
            continue
        memory = memory_by_id.get(evidence.source_memory_id)
        if memory is None:
            continue
        if evidence_recall_from_text((evidence,), memory.text) < 1.0:
            recovered.append(evidence.text)
    return _score_recovered_evidence(case, "oracle_compression", "\n".join(recovered))


def run_oracle_retrieval(case: ProbeCase) -> ReplayResult:
    """Replay with gold evidence recovered from extracted memory.

    This intervention diagnoses `retrieval_error`: the evidence survived extraction
    and storage, but the baseline retrieval path did not return it.
    """

    evidence_block = _recover_extracted_gold_evidence(case)
    return _score_recovered_evidence(case, "oracle_retrieval", evidence_block)


def run_verbatim_event_oracle(case: ProbeCase) -> ReplayResult:
    """Replay with raw-event evidence before memory extraction.

    This intervention diagnoses `premature_extraction_error`: raw events contain
    required evidence, but extracted memory no longer preserves a recoverable
    Memory Item for that evidence.
    """

    evidence_block = _recover_raw_event_only_gold_evidence(case)
    return _score_recovered_evidence(case, "verbatim_event_oracle", evidence_block)


def run_injection_oracle(case: ProbeCase) -> ReplayResult:
    """Replay with retrieved memory injected as a clean evidence block."""

    baseline = case.primary_baseline
    if evidence_recall_from_text(case.gold_evidence, baseline.injected_context) >= 1.0:
        return _score_recovered_evidence(case, "injection_oracle", "")

    retrieved_ids = set(baseline.retrieved_memory_ids)
    memory_by_id = {item.memory_id: item for item in case.extracted_memory}
    recovered = []
    for evidence in case.gold_evidence:
        if evidence.source_memory_id not in retrieved_ids:
            continue
        memory = memory_by_id.get(evidence.source_memory_id or "")
        if memory and evidence_recall_from_text((evidence,), memory.text) >= 1.0:
            recovered.append(memory.text)
    return _score_recovered_evidence(case, "injection_oracle", "\n".join(recovered))


def run_evidence_given_reasoning(case: ProbeCase) -> ReplayResult:
    """Replay final reasoning with already-injected supporting evidence."""

    baseline = case.primary_baseline
    if (
        evidence_recall_from_text(case.gold_evidence, baseline.injected_context) >= 1.0
        and baseline.answer_score < 1.0
    ):
        evidence_block = baseline.injected_context
    else:
        evidence_block = ""
    return _score_recovered_evidence(case, "evidence_given_reasoning", evidence_block)


def _score_recovered_evidence(
    case: ProbeCase,
    replay_name: str,
    evidence_block: str,
) -> ReplayResult:
    baseline = case.primary_baseline
    evidence_score = evidence_recall_from_text(case.gold_evidence, evidence_block)
    answer = case.gold_answer if evidence_score == 1.0 else ""
    recovered_answer_score = answer_score(answer, case.gold_answer)
    return ReplayResult(
        replay_name=replay_name,
        answer=answer,
        answer_score=recovered_answer_score,
        evidence_score=evidence_score,
        evidence_block=evidence_block,
        recovery_gain=recovered_answer_score - baseline.answer_score,
    )


def _recover_extracted_gold_evidence(case: ProbeCase) -> str:
    memory_by_id = {item.memory_id: item for item in case.extracted_memory}
    baseline_retrieved_ids = set(case.primary_baseline.retrieved_memory_ids)
    recovered = []
    for evidence in case.gold_evidence:
        if not evidence.source_memory_id:
            continue
        if evidence.source_memory_id in baseline_retrieved_ids:
            continue
        memory = memory_by_id.get(evidence.source_memory_id)
        if memory and evidence_recall_from_text((evidence,), memory.text) >= 1.0:
            recovered.append(memory.text)
    return "\n".join(recovered)


def _recover_raw_event_only_gold_evidence(case: ProbeCase) -> str:
    event_by_id = {event.event_id: event for event in case.raw_events}
    recovered = []
    for evidence in case.gold_evidence:
        if evidence.source_memory_id is not None:
            continue
        if evidence.source_event_id and evidence.source_event_id in event_by_id:
            recovered.append(event_by_id[evidence.source_event_id].text)
    return "\n".join(recovered)
