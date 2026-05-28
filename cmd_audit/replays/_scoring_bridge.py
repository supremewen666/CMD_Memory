"""Scoring helpers + recovery utilities shared by all replays."""

from __future__ import annotations

import warnings

from ..core import PhraseMatchShortcutWarning
from ..core.models import ProbeCase
from ..scoring import evidence_recall_from_text
from ._result import AgentGenerate, EvidenceScorer, ReplayResult

_PHRASE_MATCH_SHORTCUT_WARNED = False


def _score_recovered_evidence(
    case: ProbeCase,
    replay_name: str,
    evidence_block: str,
    tracker: object | None = None,
    *,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: object | None = None,
) -> ReplayResult:
    baseline = case.primary_baseline
    if agent_generate is not None:
        replay_context = _build_replay_agent_context(case, replay_name, evidence_block)
        answer = agent_generate(case.query, replay_context)
        if scorer is not None:
            evidence_score = scorer(case.gold_evidence, answer)
        else:
            evidence_score = evidence_recall_from_text(case.gold_evidence, answer)
        recovered_answer_score = _score_replay_answer(
            answer_verifier, answer, case.gold_answer
        )
        recovery_gain = evidence_score - baseline.evidence_score
        return ReplayResult(
            replay_name=replay_name,
            answer=answer,
            answer_score=recovered_answer_score,
            evidence_score=evidence_score,
            evidence_block=evidence_block,
            recovery_gain=recovery_gain,
            provenance_edges=tracker.get_edges() if tracker else (),
        )

    if scorer is not None:
        evidence_score = scorer(case.gold_evidence, evidence_block)
        return ReplayResult(
            replay_name=replay_name,
            answer="",
            answer_score=0.0,
            evidence_score=evidence_score,
            evidence_block=evidence_block,
            recovery_gain=evidence_score - baseline.evidence_score,
            provenance_edges=tracker.get_edges() if tracker else (),
        )

    _warn_phrase_match_shortcut_once()
    evidence_score = evidence_recall_from_text(case.gold_evidence, evidence_block)
    answer = case.gold_answer if evidence_score == 1.0 else ""
    recovered_answer_score = _score_replay_answer(
        answer_verifier, answer, case.gold_answer
    )
    return ReplayResult(
        replay_name=replay_name,
        answer=answer,
        answer_score=recovered_answer_score,
        evidence_score=evidence_score,
        evidence_block=evidence_block,
        recovery_gain=recovered_answer_score - baseline.answer_score,
        provenance_edges=tracker.get_edges() if tracker else (),
    )


def _score_replay_answer(
    answer_verifier: object | None, answer: str, gold_answer: str
) -> float:
    """Use the LLM verifier when provided, else fall back to substring.

    Shared by every replay so the answer-axis recovery_gain stays symmetric
    with the baseline path (which goes through the same helper via
    :func:`cmd_audit.llm_scoring.score_answer_with_verifier`).
    """
    from ..scoring import score_answer_with_verifier

    return score_answer_with_verifier(answer_verifier, answer, gold_answer)


def _build_replay_agent_context(
    case: ProbeCase, replay_name: str, evidence_block: str
) -> str:
    """Build the real agent replay context for Decision B subagent loop."""
    del replay_name

    baseline_context = case.primary_baseline.injected_context
    if not baseline_context:
        memory_by_id = {item.memory_id: item for item in case.extracted_memory}
        baseline_context = "\n".join(
            memory_by_id[mid].text
            for mid in case.primary_baseline.retrieved_memory_ids
            if mid in memory_by_id
        )

    return "\n\n".join(
        (
            "BASELINE CONTEXT:\n" + baseline_context,
            "COUNTERFACTUAL EVIDENCE BLOCK:\n" + evidence_block,
        )
    )


def _warn_phrase_match_shortcut_once() -> None:
    global _PHRASE_MATCH_SHORTCUT_WARNED
    if _PHRASE_MATCH_SHORTCUT_WARNED:
        return
    warnings.warn(
        "phrase-match shortcut active; recovery_gain is mechanical, not "
        "LLM-evaluated. Use agent_generate + scorer for paper claims.",
        PhraseMatchShortcutWarning,
        stacklevel=3,
    )
    _PHRASE_MATCH_SHORTCUT_WARNED = True


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
        if memory and memory.store in (case.default_store, "default") and evidence_recall_from_text((evidence,), memory.text) >= 1.0:
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


score_recovered_evidence = _score_recovered_evidence
recover_extracted_gold_evidence = _recover_extracted_gold_evidence
recover_raw_event_only_gold_evidence = _recover_raw_event_only_gold_evidence
