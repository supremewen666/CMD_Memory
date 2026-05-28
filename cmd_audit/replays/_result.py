"""ReplayResult dataclass + replay name registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..core.models import GoldEvidence

EvidenceScorer = Callable[[tuple[GoldEvidence, ...], str], float]
AgentGenerate = Callable[[str, str], str]


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
