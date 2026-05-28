"""10 counterfactual replay implementations."""

from __future__ import annotations

from ..core.models import ProbeCase
from ..scoring import evidence_recall_from_text
from ._result import AgentGenerate, EvidenceScorer, ReplayResult
from ._scoring_bridge import (
    _recover_extracted_gold_evidence,
    _recover_raw_event_only_gold_evidence,
    _score_recovered_evidence,
)


def run_oracle_write(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: object | None = None,
) -> ReplayResult:
    """Replay with gold evidence injected as newly written memory."""

    evidence_block = "\n".join(
        evidence.text
        for evidence in case.gold_evidence
        if evidence.source_memory_id is None and evidence.source_event_id is None
    )
    if tracker is not None:
        target_id = f"{case.case_id}__oracle_write"
        event_ids = [e.event_id for e in case.raw_events]
        for evidence in case.gold_evidence:
            if evidence.source_memory_id is None and evidence.source_event_id is None:
                tracker.record_edge(
                    source_id="|".join(event_ids) if event_ids else evidence.evidence_id,
                    target_id=target_id,
                    operation="write",
                    source_text=evidence.text,
                )
    return _score_recovered_evidence(
        case,
        "oracle_write",
        evidence_block,
        tracker,
        scorer=scorer,
        agent_generate=agent_generate,
        answer_verifier=answer_verifier,
    )


def run_oracle_compression(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: object | None = None,
) -> ReplayResult:
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
    if tracker is not None:
        target_id = f"{case.case_id}__oracle_compression"
        for evidence in case.gold_evidence:
            if not evidence.source_memory_id:
                continue
            memory = memory_by_id.get(evidence.source_memory_id)
            if memory is None:
                continue
            if evidence_recall_from_text((evidence,), memory.text) < 1.0:
                tracker.record_edge(
                    source_id=evidence.source_memory_id,
                    target_id=target_id,
                    operation="compress",
                    source_text=memory.text,
                )
    return _score_recovered_evidence(
        case,
        "oracle_compression",
        "\n".join(recovered),
        tracker,
        scorer=scorer,
        agent_generate=agent_generate,
        answer_verifier=answer_verifier,
    )


def run_oracle_retrieval(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: object | None = None,
) -> ReplayResult:
    """Replay with gold evidence recovered from extracted memory.

    This intervention diagnoses `retrieval_error`: the evidence survived extraction
    and storage, but the baseline retrieval path did not return it.
    """

    evidence_block = _recover_extracted_gold_evidence(case)
    if tracker is not None:
        target_id = f"{case.case_id}__oracle_retrieval"
        memory_by_id = {item.memory_id: item for item in case.extracted_memory}
        baseline_retrieved_ids = set(case.primary_baseline.retrieved_memory_ids)
        for evidence in case.gold_evidence:
            if not evidence.source_memory_id:
                continue
            if evidence.source_memory_id in baseline_retrieved_ids:
                continue
            memory = memory_by_id.get(evidence.source_memory_id)
            if memory and memory.store in (
                case.default_store, "default"
            ) and evidence_recall_from_text((evidence,), memory.text) >= 1.0:
                tracker.record_edge(
                    source_id=evidence.source_memory_id,
                    target_id=target_id,
                    operation="retrieve",
                    source_text=memory.text,
                )
    return _score_recovered_evidence(
        case,
        "oracle_retrieval",
        evidence_block,
        tracker,
        scorer=scorer,
        agent_generate=agent_generate,
        answer_verifier=answer_verifier,
    )


def run_verbatim_event_oracle(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: object | None = None,
) -> ReplayResult:
    """Replay with raw-event evidence before memory extraction.

    This intervention diagnoses `premature_extraction_error`: raw events contain
    required evidence, but extracted memory no longer preserves a recoverable
    Memory Item for that evidence.
    """

    evidence_block = _recover_raw_event_only_gold_evidence(case)
    if tracker is not None:
        target_id = f"{case.case_id}__verbatim_event"
        event_by_id = {event.event_id: event for event in case.raw_events}
        for evidence in case.gold_evidence:
            if evidence.source_memory_id is not None:
                continue
            if evidence.source_event_id and evidence.source_event_id in event_by_id:
                event = event_by_id[evidence.source_event_id]
                tracker.record_edge(
                    source_id=evidence.source_event_id,
                    target_id=target_id,
                    operation="extract",
                    source_text=event.text,
                )
    return _score_recovered_evidence(
        case,
        "verbatim_event_oracle",
        evidence_block,
        tracker,
        scorer=scorer,
        agent_generate=agent_generate,
        answer_verifier=answer_verifier,
    )


def run_injection_oracle(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: object | None = None,
) -> ReplayResult:
    """Replay with retrieved memory injected as a clean evidence block."""

    baseline = case.primary_baseline
    # Use stored evidence_score (effective injection quality) rather than raw
    # text presence.  When evidence text is present but poorly formatted the
    # stored score stays 0.0 while runtime text-match would return 1.0.
    if baseline.evidence_score >= 1.0:
        return _score_recovered_evidence(
            case,
            "injection_oracle",
            "",
            tracker,
            scorer=scorer,
            agent_generate=agent_generate,
            answer_verifier=answer_verifier,
        )

    retrieved_ids = set(baseline.retrieved_memory_ids)
    memory_by_id = {item.memory_id: item for item in case.extracted_memory}
    recovered = []
    for evidence in case.gold_evidence:
        if evidence.source_memory_id not in retrieved_ids:
            continue
        memory = memory_by_id.get(evidence.source_memory_id or "")
        if memory and evidence_recall_from_text((evidence,), memory.text) >= 1.0:
            recovered.append(memory.text)
    if tracker is not None:
        target_id = f"{case.case_id}__injection"
        for evidence in case.gold_evidence:
            if evidence.source_memory_id not in retrieved_ids:
                continue
            memory = memory_by_id.get(evidence.source_memory_id or "")
            if memory and evidence_recall_from_text((evidence,), memory.text) >= 1.0:
                tracker.record_edge(
                    source_id=evidence.source_memory_id,
                    target_id=target_id,
                    operation="inject",
                    source_text=memory.text,
                )
    return _score_recovered_evidence(
        case,
        "injection_oracle",
        "\n".join(recovered),
        tracker,
        scorer=scorer,
        agent_generate=agent_generate,
        answer_verifier=answer_verifier,
    )


def run_evidence_given_reasoning(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: object | None = None,
) -> ReplayResult:
    """Replay final reasoning with already-injected supporting evidence."""

    baseline = case.primary_baseline
    if baseline.evidence_score >= 1.0 and baseline.answer_score < 1.0:
        evidence_block = baseline.injected_context
    else:
        evidence_block = ""
    if tracker is not None and evidence_block:
        target_id = f"{case.case_id}__reasoning"
        source_id = "|".join(baseline.retrieved_memory_ids)
        tracker.record_edge(
            source_id=source_id,
            target_id=target_id,
            operation="reason",
            source_text=evidence_block,
        )
    return _score_recovered_evidence(
        case,
        "evidence_given_reasoning",
        evidence_block,
        tracker,
        scorer=scorer,
        agent_generate=agent_generate,
        answer_verifier=answer_verifier,
    )


def run_oracle_route(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: object | None = None,
) -> ReplayResult:
    """Replay by testing retrieval from each available store/tier.

    This intervention diagnoses `route_error`: correct memory exists but was stored
    in a store/tier the baseline retrieval did not access. Enumerates all stores,
    picks the one with the best evidence recovery.
    """
    stores = _collect_stores(case)
    best_score = -1.0
    best_block = ""
    best_store = ""

    for store in stores:
        evidence_block = _recover_from_store(case, store)
        score = evidence_recall_from_text(case.gold_evidence, evidence_block)
        if score > best_score:
            best_score = score
            best_block = evidence_block
            best_store = store

    if tracker is not None and best_store:
        target_id = f"{case.case_id}__route"
        memory_by_id = {item.memory_id: item for item in case.extracted_memory}
        for evidence in case.gold_evidence:
            if not evidence.source_memory_id:
                continue
            memory = memory_by_id.get(evidence.source_memory_id)
            if memory is None:
                continue
            if memory.store != best_store:
                continue
            if evidence_recall_from_text((evidence,), memory.text) >= 1.0:
                tracker.record_edge(
                    source_id=evidence.source_memory_id,
                    target_id=target_id,
                    operation="route",
                    source_text=memory.text,
                )
    return _score_recovered_evidence(
        case,
        "oracle_route",
        best_block,
        tracker,
        scorer=scorer,
        agent_generate=agent_generate,
        answer_verifier=answer_verifier,
    )


def _collect_stores(case: ProbeCase) -> list[str]:
    stores: list[str] = []
    seen: set[str] = set()
    for item in case.extracted_memory:
        if item.store not in seen:
            seen.add(item.store)
            stores.append(item.store)
    return stores


def _recover_from_store(case: ProbeCase, store: str) -> str:
    memory_by_id = {item.memory_id: item for item in case.extracted_memory}
    recovered = []
    for evidence in case.gold_evidence:
        if not evidence.source_memory_id:
            continue
        memory = memory_by_id.get(evidence.source_memory_id)
        if memory is None:
            continue
        if memory.store != store:
            continue
        if evidence_recall_from_text((evidence,), memory.text) >= 1.0:
            recovered.append(memory.text)
    return "\n".join(recovered)

def run_oracle_granularity(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: object | None = None,
) -> ReplayResult:
    """Replay by re-expressing memory at each granularity level.

    This intervention diagnoses ``granularity_error``: the baseline used a
    sub-optimal granularity level for memory expression. Enumerates configured
    granularity levels, picks the one with the best evidence recovery.

    Only produces a positive recovery gain when a granularity level *different
    from the current one* yields higher evidence recall. When all levels
    produce the same evidence (no granularity effect), the replay returns
    zero gain so it does not interfere with other attribution labels.
    """
    current_block = _recover_at_granularity(case, case.current_granularity)
    current_score = evidence_recall_from_text(case.gold_evidence, current_block)

    best_score = current_score
    best_block = current_block
    best_level = case.current_granularity

    for level in case.granularity_levels:
        if level == case.current_granularity:
            continue
        evidence_block = _recover_at_granularity(case, level)
        score = evidence_recall_from_text(case.gold_evidence, evidence_block)
        if score > best_score:
            best_score = score
            best_block = evidence_block
            best_level = level

    if best_score <= current_score:
        return _score_recovered_evidence(
            case,
            "oracle_granularity",
            "",
            tracker,
            scorer=scorer,
            agent_generate=agent_generate,
            answer_verifier=answer_verifier,
        )

    if tracker is not None and best_level != case.current_granularity:
        target_id = f"{case.case_id}__granularity"
        memory_by_id = {item.memory_id: item for item in case.extracted_memory}
        for evidence in case.gold_evidence:
            if (
                evidence.granularity_level is not None
                and evidence.granularity_level != best_level
            ):
                continue
            if not evidence.source_memory_id:
                continue
            memory = memory_by_id.get(evidence.source_memory_id)
            if memory is None:
                continue
            if evidence_recall_from_text((evidence,), memory.text) >= 1.0:
                tracker.record_edge(
                    source_id=evidence.source_memory_id,
                    target_id=target_id,
                    operation="extract",
                    source_text=memory.text,
                )
    return _score_recovered_evidence(
        case,
        "oracle_granularity",
        best_block,
        tracker,
        scorer=scorer,
        agent_generate=agent_generate,
        answer_verifier=answer_verifier,
    )


def run_graph_off(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: object | None = None,
) -> ReplayResult:
    """Replay with graph expansion disabled.

    This intervention diagnoses ``graph_error``: graph expansion introduced
    distractors that masked correct evidence. Filters to non-graph-expanded
    memory items and checks whether direct evidence alone recovers.
    """
    has_expanded = any(item.is_graph_expanded for item in case.extracted_memory)
    if not has_expanded:
        return _score_recovered_evidence(
            case,
            "graph_off",
            "",
            tracker,
            scorer=scorer,
            agent_generate=agent_generate,
            answer_verifier=answer_verifier,
        )

    evidence_block = _recover_without_graph_expansion(case)
    if tracker is not None:
        target_id = f"{case.case_id}__graph_off"
        memory_by_id = {item.memory_id: item for item in case.extracted_memory}
        for evidence in case.gold_evidence:
            if not evidence.source_memory_id:
                continue
            memory = memory_by_id.get(evidence.source_memory_id)
            if memory is None:
                continue
            if memory.is_graph_expanded:
                continue
            if evidence_recall_from_text((evidence,), memory.text) >= 1.0:
                tracker.record_edge(
                    source_id=evidence.source_memory_id,
                    target_id=target_id,
                    operation="retrieve",
                    source_text=memory.text,
                )
    return _score_recovered_evidence(
        case,
        "graph_off",
        evidence_block,
        tracker,
        scorer=scorer,
        agent_generate=agent_generate,
        answer_verifier=answer_verifier,
    )


def run_safety_off(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: object | None = None,
) -> ReplayResult:
    """Replay with safety filter bypassed.

    This intervention diagnoses ``safety_error``: the safety filter blocked
    evidence that was otherwise valid. When ``safety_filter_blocked`` is True,
    the blocked evidence is provided directly.
    """
    if not case.safety_filter_blocked:
        return _score_recovered_evidence(
            case,
            "safety_off",
            "",
            tracker,
            scorer=scorer,
            agent_generate=agent_generate,
            answer_verifier=answer_verifier,
        )

    evidence_block = "\n".join(evidence.text for evidence in case.gold_evidence)
    if tracker is not None:
        target_id = f"{case.case_id}__safety_off"
        for evidence in case.gold_evidence:
            tracker.record_edge(
                source_id=evidence.evidence_id,
                target_id=target_id,
                operation="inject",
                source_text=evidence.text,
            )
    return _score_recovered_evidence(
        case,
        "safety_off",
        evidence_block,
        tracker,
        scorer=scorer,
        agent_generate=agent_generate,
        answer_verifier=answer_verifier,
    )

def _recover_at_granularity(case: ProbeCase, level: str) -> str:
    """Collect gold evidence recoverable at a given granularity level.

    Evidence with ``granularity_level`` set to *level* or ``None``
    (available at all levels) is included when its source memory item
    contains the required phrases.
    """
    memory_by_id = {item.memory_id: item for item in case.extracted_memory}
    recovered = []
    for evidence in case.gold_evidence:
        if (
            evidence.granularity_level is not None
            and evidence.granularity_level != level
        ):
            continue
        if not evidence.source_memory_id:
            continue
        memory = memory_by_id.get(evidence.source_memory_id)
        if memory is None:
            continue
        if evidence_recall_from_text((evidence,), memory.text) >= 1.0:
            recovered.append(memory.text)
    return "\n".join(recovered)


def _recover_without_graph_expansion(case: ProbeCase) -> str:
    """Collect evidence from memory items not retrieved via graph expansion."""
    memory_by_id = {item.memory_id: item for item in case.extracted_memory}
    recovered = []
    for evidence in case.gold_evidence:
        if not evidence.source_memory_id:
            continue
        memory = memory_by_id.get(evidence.source_memory_id)
        if memory is None:
            continue
        if memory.is_graph_expanded:
            continue
        if evidence_recall_from_text((evidence,), memory.text) >= 1.0:
            recovered.append(memory.text)
    return "\n".join(recovered)
