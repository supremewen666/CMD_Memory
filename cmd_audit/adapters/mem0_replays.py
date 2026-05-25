"""Adapter-aware replay functions that use ``Mem0Adapter`` interception."""

from __future__ import annotations

from cmd_audit.models import ProbeCase
from cmd_audit.replays import AgentGenerate, EvidenceScorer, ReplayResult

from ._replay_skeleton import run_adapter_replay_portfolio
from .mem0_adapter import Mem0Adapter


def run_mem0_replay_portfolio(
    case: ProbeCase,
    adapter: Mem0Adapter,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
) -> tuple[ReplayResult, ...]:
    """Run 6 adapter-intercepted replays + 4 V1 passthrough replays."""
    return run_adapter_replay_portfolio(
        case,
        adapter,
        tracker=tracker,
        scorer=scorer,
        agent_generate=agent_generate,
    )
