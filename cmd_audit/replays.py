"""Counterfactual replay primitives for CMD-Audit V0."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Callable

from .models import GoldEvidence, ProbeCase
from .scoring import answer_score, evidence_recall_from_text
from .warnings import PhraseMatchShortcutWarning

EvidenceScorer = Callable[[tuple[GoldEvidence, ...], str], float]
AgentGenerate = Callable[[str, str], str]

_PHRASE_MATCH_SHORTCUT_WARNED = False


@dataclass(frozen=True)
class ReplayResult:
    replay_name: str
    answer: str
    answer_score: float
    evidence_score: float
    evidence_block: str
    recovery_gain: float
    cost_units: float = 1.0
    provenance_edges: tuple = ()


V1_REPLAY_NAMES = (
    "oracle_write",
    "oracle_compression",
    "verbatim_event_oracle",
    "oracle_retrieval",
    "injection_oracle",
    "evidence_given_reasoning",
    "oracle_route",
    "oracle_granularity",
    "graph_off",
    "safety_off",
)

def run_v0_replay_portfolio(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
) -> tuple[ReplayResult, ...]:
    """Run the currently implemented V0 replay portfolio for one case."""

    return (
        run_oracle_write(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate
        ),
        run_oracle_compression(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate
        ),
        run_verbatim_event_oracle(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate
        ),
        run_oracle_retrieval(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate
        ),
        run_injection_oracle(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate
        ),
        run_evidence_given_reasoning(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate
        ),
    )


def run_v1_replay_portfolio(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
) -> tuple[ReplayResult, ...]:
    """Run the V1 replay portfolio (10 replays) for one case."""

    return (
        run_oracle_write(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate
        ),
        run_oracle_compression(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate
        ),
        run_verbatim_event_oracle(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate
        ),
        run_oracle_retrieval(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate
        ),
        run_injection_oracle(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate
        ),
        run_evidence_given_reasoning(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate
        ),
        *run_v1_passthrough_replays(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate
        ),
    )


def run_v1_passthrough_replays(
    case: ProbeCase,
    adapter=None,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
) -> tuple[ReplayResult, ...]:
    """Run the four V1 replay extensions, optionally through an adapter snapshot."""
    if adapter is None:
        return (
            run_oracle_route(
                case, tracker=tracker, scorer=scorer, agent_generate=agent_generate
            ),
            run_oracle_granularity(
                case, tracker=tracker, scorer=scorer, agent_generate=agent_generate
            ),
            run_graph_off(
                case, tracker=tracker, scorer=scorer, agent_generate=agent_generate
            ),
            run_safety_off(
                case, tracker=tracker, scorer=scorer, agent_generate=agent_generate
            ),
        )

    return (
        _run_adapter_search_replay(
            case,
            adapter,
            "oracle_route",
            target_suffix="route",
            operation="route",
            tracker=tracker,
            scorer=scorer,
            agent_generate=agent_generate,
        ),
        _run_adapter_search_replay(
            case,
            adapter,
            "oracle_granularity",
            target_suffix="granularity",
            operation="extract",
            tracker=tracker,
            scorer=scorer,
            agent_generate=agent_generate,
        ),
        _run_adapter_search_replay(
            case,
            adapter,
            "graph_off",
            target_suffix="graph_off",
            operation="retrieve",
            tracker=tracker,
            scorer=scorer,
            agent_generate=agent_generate,
        ),
        _run_adapter_safety_off(
            case,
            adapter,
            tracker=tracker,
            scorer=scorer,
            agent_generate=agent_generate,
        ),
    )


def _run_adapter_search_replay(
    case: ProbeCase,
    adapter,
    replay_name: str,
    *,
    target_suffix: str,
    operation: str,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
) -> ReplayResult:
    oracle_results = adapter.intercept_search(
        case.case_id,
        adapter.original_query,
        adapter.original_results,
        replay_name,
    )
    evidence_block = "\n".join(item.text for item in oracle_results)
    if tracker is not None:
        target_id = f"{case.case_id}__{target_suffix}"
        for i, item in enumerate(oracle_results):
            tracker.record_edge(
                source_id=f"adapter_input_{i}",
                target_id=target_id,
                operation=operation,
                source_text=item.text,
            )
    return _score_recovered_evidence(
        case,
        replay_name,
        evidence_block,
        tracker,
        scorer=scorer,
        agent_generate=agent_generate,
    )


def _run_adapter_safety_off(
    case: ProbeCase,
    adapter,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
) -> ReplayResult:
    if case.safety_filter_blocked:
        oracle = adapter.intercept_write(
            case.case_id, adapter.original_inputs, "safety_off"
        )
        evidence_block = "\n".join(oracle)
    else:
        oracle = []
        evidence_block = ""
    if tracker is not None and evidence_block:
        target_id = f"{case.case_id}__safety_off"
        for i, text in enumerate(oracle):
            tracker.record_edge(
                source_id=f"adapter_input_{i}",
                target_id=target_id,
                operation="inject",
                source_text=text,
            )
    return _score_recovered_evidence(
        case,
        "safety_off",
        evidence_block,
        tracker,
        scorer=scorer,
        agent_generate=agent_generate,
    )


def run_oracle_write(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
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
    )


def run_oracle_compression(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
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
    )


def run_oracle_retrieval(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
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
    )


def run_verbatim_event_oracle(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
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
    )


def run_injection_oracle(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
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
    )


def run_evidence_given_reasoning(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
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
    )


def run_oracle_route(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
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


def _score_recovered_evidence(
    case: ProbeCase,
    replay_name: str,
    evidence_block: str,
    tracker: object | None = None,
    *,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
) -> ReplayResult:
    baseline = case.primary_baseline
    if agent_generate is not None:
        replay_context = _build_replay_agent_context(case, replay_name, evidence_block)
        answer = agent_generate(case.query, replay_context)
        if scorer is not None:
            evidence_score = scorer(case.gold_evidence, answer)
        else:
            evidence_score = evidence_recall_from_text(case.gold_evidence, answer)
        recovered_answer_score = answer_score(answer, case.gold_answer)
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
    recovered_answer_score = answer_score(answer, case.gold_answer)
    return ReplayResult(
        replay_name=replay_name,
        answer=answer,
        answer_score=recovered_answer_score,
        evidence_score=evidence_score,
        evidence_block=evidence_block,
        recovery_gain=recovered_answer_score - baseline.answer_score,
        provenance_edges=tracker.get_edges() if tracker else (),
    )


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


# ── V1 issue 0012 replays ──────────────────────────────────────────────


def run_oracle_granularity(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
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
    )


def run_graph_off(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
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
    )


def run_safety_off(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
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


_V1_REPLAY_DISPATCH = {
    "oracle_write": run_oracle_write,
    "oracle_compression": run_oracle_compression,
    "verbatim_event_oracle": run_verbatim_event_oracle,
    "oracle_retrieval": run_oracle_retrieval,
    "injection_oracle": run_injection_oracle,
    "evidence_given_reasoning": run_evidence_given_reasoning,
    "oracle_route": run_oracle_route,
    "oracle_granularity": run_oracle_granularity,
    "graph_off": run_graph_off,
    "safety_off": run_safety_off,
}


def run_v1_replay_portfolio_subset(
    case: ProbeCase,
    replay_names: tuple[str, ...],
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
) -> tuple[ReplayResult, ...]:
    """Run only the named V1 replays, in portfolio order."""
    return tuple(
        _V1_REPLAY_DISPATCH[name](
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate
        )
        for name in V1_REPLAY_NAMES
        if name in replay_names
    )
