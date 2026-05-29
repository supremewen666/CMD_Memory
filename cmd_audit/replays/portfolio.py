"""Portfolio orchestrators: V0/V1 replay batches + adapter passthroughs."""

from __future__ import annotations

from ..core.models import ProbeCase
from ._result import AgentGenerate, EvidenceScorer, ReplayResult, V1_REPLAY_NAMES
from ._scoring_bridge import _score_recovered_evidence
from .interventions import (
    run_evidence_given_reasoning,
    run_graph_off,
    run_injection_oracle,
    run_oracle_compression,
    run_oracle_granularity,
    run_oracle_retrieval,
    run_oracle_route,
    run_oracle_write,
    run_safety_off,
    run_verbatim_event_oracle,
)


def _run_v0_replay_portfolio(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: object | None = None,
) -> tuple[ReplayResult, ...]:
    """Run the currently implemented V0 replay portfolio for one case."""

    return (
        run_oracle_write(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate, answer_verifier=answer_verifier
        ),
        run_oracle_compression(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate, answer_verifier=answer_verifier
        ),
        run_verbatim_event_oracle(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate, answer_verifier=answer_verifier
        ),
        run_oracle_retrieval(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate, answer_verifier=answer_verifier
        ),
        run_injection_oracle(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate, answer_verifier=answer_verifier
        ),
        run_evidence_given_reasoning(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate, answer_verifier=answer_verifier
        ),
    )


def run_replay_portfolio(
    case: ProbeCase,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: object | None = None,
) -> tuple[ReplayResult, ...]:
    """Run the 10-replay portfolio for one case."""

    return (
        run_oracle_write(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate, answer_verifier=answer_verifier
        ),
        run_oracle_compression(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate, answer_verifier=answer_verifier
        ),
        run_verbatim_event_oracle(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate, answer_verifier=answer_verifier
        ),
        run_oracle_retrieval(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate, answer_verifier=answer_verifier
        ),
        run_injection_oracle(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate, answer_verifier=answer_verifier
        ),
        run_evidence_given_reasoning(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate, answer_verifier=answer_verifier
        ),
        *_run_v1_passthrough_replays(
            case, tracker=tracker, scorer=scorer, agent_generate=agent_generate, answer_verifier=answer_verifier
        ),
    )


def _run_v1_passthrough_replays(
    case: ProbeCase,
    adapter=None,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: object | None = None,
) -> tuple[ReplayResult, ...]:
    """Run the four V1 replay extensions, optionally through an adapter snapshot."""
    if adapter is None:
        return (
            run_oracle_route(
                case, tracker=tracker, scorer=scorer, agent_generate=agent_generate, answer_verifier=answer_verifier
            ),
            run_oracle_granularity(
                case, tracker=tracker, scorer=scorer, agent_generate=agent_generate, answer_verifier=answer_verifier
            ),
            run_graph_off(
                case, tracker=tracker, scorer=scorer, agent_generate=agent_generate, answer_verifier=answer_verifier
            ),
            run_safety_off(
                case, tracker=tracker, scorer=scorer, agent_generate=agent_generate, answer_verifier=answer_verifier
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
            answer_verifier=answer_verifier,
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
            answer_verifier=answer_verifier,
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
            answer_verifier=answer_verifier,
        ),
        _run_adapter_safety_off(
            case,
            adapter,
            tracker=tracker,
            scorer=scorer,
            agent_generate=agent_generate,
            answer_verifier=answer_verifier,
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
    answer_verifier: object | None = None,
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
        answer_verifier=answer_verifier,
    )


def _run_adapter_safety_off(
    case: ProbeCase,
    adapter,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: object | None = None,
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
        answer_verifier=answer_verifier,
    )

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


def run_replay_portfolio_subset(
    case: ProbeCase,
    replay_names: tuple[str, ...],
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: object | None = None,
) -> tuple[ReplayResult, ...]:
    """Run only the named replays, in portfolio order."""
    return tuple(
        _V1_REPLAY_DISPATCH[name](
            case,
            tracker=tracker,
            scorer=scorer,
            agent_generate=agent_generate,
            answer_verifier=answer_verifier,
        )
        for name in V1_REPLAY_NAMES
        if name in replay_names
    )
