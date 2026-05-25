"""Offline-calibrated constants for the issue 0021 Pre-CMD Hook.

Defaults are placeholders before running ``scripts/calibrate_hook.py``.
Online inference reads only these constants and performs no LLM calls.
"""

from __future__ import annotations

V1_REPLAY_NAME_ORDER: tuple[str, ...] = (
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

TOP_K: int = 3
FALLBACK_THRESHOLD: float = 0.35

RPE_JUDGE_WEIGHTS: tuple[float, ...] = (
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
)
RPE_JUDGE_INTERCEPT: float = 0.0

