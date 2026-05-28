"""Public API for cmd_audit.replays."""

from ._result import (
    AgentGenerate,
    EvidenceScorer,
    ReplayResult,
    V1_REPLAY_NAMES,
)
from ._scoring_bridge import (
    _build_replay_agent_context,
    _recover_extracted_gold_evidence,
    _recover_raw_event_only_gold_evidence,
    _score_recovered_evidence,
    _score_replay_answer,
    _warn_phrase_match_shortcut_once,
    recover_extracted_gold_evidence,
    recover_raw_event_only_gold_evidence,
    score_recovered_evidence,
)
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
from .portfolio import (
    run_v0_replay_portfolio,
    run_v1_passthrough_replays,
    run_v1_replay_portfolio,
    run_v1_replay_portfolio_subset,
)

__all__ = [
    "AgentGenerate",
    "EvidenceScorer",
    "ReplayResult",
    "V1_REPLAY_NAMES",
    "recover_extracted_gold_evidence",
    "recover_raw_event_only_gold_evidence",
    "run_evidence_given_reasoning",
    "run_graph_off",
    "run_injection_oracle",
    "run_oracle_compression",
    "run_oracle_granularity",
    "run_oracle_retrieval",
    "run_oracle_route",
    "run_oracle_write",
    "run_safety_off",
    "run_v0_replay_portfolio",
    "run_v1_passthrough_replays",
    "run_v1_replay_portfolio",
    "run_v1_replay_portfolio_subset",
    "run_verbatim_event_oracle",
    "score_recovered_evidence",
]
