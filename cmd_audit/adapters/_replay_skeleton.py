"""Shared replay portfolio skeleton — adapter-agnostic replay functions.

Uses the standard adapter interface (original_inputs, intercept_write,
original_query, original_results, intercept_search) so each adapter only
provides its domain-specific operation mapping.
"""

from __future__ import annotations

from cmd_audit.core.models import ProbeCase
from cmd_audit.replays import (
    AgentGenerate,
    EvidenceScorer,
    ReplayResult,
    _score_recovered_evidence,
    recover_raw_event_only_gold_evidence,
    run_v1_passthrough_replays,
)


def run_adapter_replay_portfolio(
    case: ProbeCase,
    adapter,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
) -> tuple[ReplayResult, ...]:
    """Run 6 adapter-intercepted replays + 4 V1 passthrough replays."""
    results = (
        _run_oracle_write(
            case,
            adapter,
            tracker=tracker,
            scorer=scorer,
            agent_generate=agent_generate,
        ),
        _run_oracle_compression(
            case,
            adapter,
            tracker=tracker,
            scorer=scorer,
            agent_generate=agent_generate,
        ),
        _run_verbatim_event_oracle(
            case,
            adapter,
            tracker=tracker,
            scorer=scorer,
            agent_generate=agent_generate,
        ),
        _run_oracle_retrieval(
            case,
            adapter,
            tracker=tracker,
            scorer=scorer,
            agent_generate=agent_generate,
        ),
        _run_injection_oracle(
            case,
            adapter,
            tracker=tracker,
            scorer=scorer,
            agent_generate=agent_generate,
        ),
        _run_evidence_given_reasoning(
            case,
            adapter,
            tracker=tracker,
            scorer=scorer,
            agent_generate=agent_generate,
        ),
        *run_v1_passthrough_replays(
            case,
            adapter,
            tracker=tracker,
            scorer=scorer,
            agent_generate=agent_generate,
        ),
    )
    adapter.verify_sandbox()
    return results


# ── Write-side replays ──────────────────────────────────────────────────


def _run_oracle_write(
    case: ProbeCase,
    adapter,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
) -> ReplayResult:
    original = adapter.original_inputs
    oracle = adapter.intercept_write(case.case_id, original, "oracle_write")
    if tracker is not None:
        target_id = f"{case.case_id}__oracle_write"
        for i, text in enumerate(oracle):
            tracker.record_edge(
                source_id=f"adapter_input_{i}",
                target_id=target_id,
                operation="write",
                source_text=text,
            )
    return _score_recovered_evidence(
        case,
        "oracle_write",
        "\n".join(oracle),
        tracker,
        scorer=scorer,
        agent_generate=agent_generate,
    )


def _run_oracle_compression(
    case: ProbeCase,
    adapter,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
) -> ReplayResult:
    original = adapter.original_inputs
    oracle = adapter.intercept_write(case.case_id, original, "oracle_compression")
    if tracker is not None:
        target_id = f"{case.case_id}__oracle_compression"
        for i, text in enumerate(oracle):
            tracker.record_edge(
                source_id=f"adapter_input_{i}",
                target_id=target_id,
                operation="compress",
                source_text=text,
            )
    return _score_recovered_evidence(
        case,
        "oracle_compression",
        "\n".join(oracle),
        tracker,
        scorer=scorer,
        agent_generate=agent_generate,
    )


def _run_verbatim_event_oracle(
    case: ProbeCase,
    adapter,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
) -> ReplayResult:
    adapter.intercept_write(
        case.case_id, adapter.original_inputs, "verbatim_event_oracle"
    )
    evidence_block = recover_raw_event_only_gold_evidence(case)
    if tracker is not None:
        target_id = f"{case.case_id}__verbatim_event"
        event_by_id = {event.event_id: event for event in case.raw_events}
        for evidence in case.gold_evidence:
            if evidence.source_memory_id is not None:
                continue
            if evidence.source_event_id and evidence.source_event_id in event_by_id:
                tracker.record_edge(
                    source_id=evidence.source_event_id,
                    target_id=target_id,
                    operation="extract",
                    source_text=event_by_id[evidence.source_event_id].text,
                )
    return _score_recovered_evidence(
        case,
        "verbatim_event_oracle",
        evidence_block,
        tracker,
        scorer=scorer,
        agent_generate=agent_generate,
    )


def _run_injection_oracle(
    case: ProbeCase,
    adapter,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
) -> ReplayResult:
    baseline = case.primary_baseline
    if baseline.evidence_score >= 1.0:
        return _score_recovered_evidence(
            case,
            "injection_oracle",
            "",
            tracker,
            scorer=scorer,
            agent_generate=agent_generate,
        )
    oracle = adapter.intercept_write(
        case.case_id, adapter.original_inputs, "injection_oracle"
    )
    if tracker is not None:
        target_id = f"{case.case_id}__injection"
        for i, text in enumerate(oracle):
            tracker.record_edge(
                source_id=f"adapter_input_{i}",
                target_id=target_id,
                operation="inject",
                source_text=text,
            )
    return _score_recovered_evidence(
        case,
        "injection_oracle",
        "\n".join(oracle),
        tracker,
        scorer=scorer,
        agent_generate=agent_generate,
    )


# ── Retrieval-side replays ──────────────────────────────────────────────


def _run_oracle_retrieval(
    case: ProbeCase,
    adapter,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
) -> ReplayResult:
    original_results = adapter.original_results
    oracle_results = adapter.intercept_search(
        case.case_id,
        adapter.original_query,
        original_results,
        "oracle_retrieval",
    )
    evidence_block = "\n".join(item.text for item in oracle_results)
    if tracker is not None:
        target_id = f"{case.case_id}__oracle_retrieval"
        for i, item in enumerate(oracle_results):
            tracker.record_edge(
                source_id=f"adapter_input_{i}",
                target_id=target_id,
                operation="retrieve",
                source_text=item.text,
            )
    return _score_recovered_evidence(
        case,
        "oracle_retrieval",
        evidence_block,
        tracker,
        scorer=scorer,
        agent_generate=agent_generate,
    )


def _run_evidence_given_reasoning(
    case: ProbeCase,
    adapter,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
) -> ReplayResult:
    baseline = case.primary_baseline
    if baseline.evidence_score >= 1.0 and baseline.answer_score < 1.0:
        original_results = adapter.original_results
        oracle_results = adapter.intercept_search(
            case.case_id,
            adapter.original_query,
            original_results,
            "evidence_given_reasoning",
        )
        evidence_block = "\n".join(item.text for item in oracle_results)
    else:
        evidence_block = ""
    if tracker is not None and evidence_block:
        target_id = f"{case.case_id}__reasoning"
        for i, item in enumerate(oracle_results):
            tracker.record_edge(
                source_id=f"adapter_input_{i}",
                target_id=target_id,
                operation="reason",
                source_text=item.text,
            )
    return _score_recovered_evidence(
        case,
        "evidence_given_reasoning",
        evidence_block,
        tracker,
        scorer=scorer,
        agent_generate=agent_generate,
    )
