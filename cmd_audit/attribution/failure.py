"""Zero/negative-gain abstention handling (Decision 35 R1)."""

from __future__ import annotations

from dataclasses import dataclass


# Failure reason enum for principled abstention (Decision 35 R1).
FAILURE_REASON_ZERO_GAIN = "zero_gain"
FAILURE_REASON_NEGATIVE_GAIN = "negative_gain"


@dataclass(frozen=True)
class AttributionResult:
    """Result of operation-level attribution from replay recovery gains."""

    predicted_label: str
    top_replay: str
    recovery_gain: float
    top2_labels: tuple[str, ...]
    is_ambiguous: bool
    top_k_labels: tuple[str, ...] = ()
    close_deltas: tuple[tuple[str, float], ...] = ()
    distractor_provenance_ids: tuple[str, ...] = ()
    distractor_provenance_edges: tuple = ()
    shadow_replay_resolution: str | None = None
    attribution_failed: bool = False
    failure_reason: str | None = None


def build_abstain_result(
    top_recovery_gain: float,
    *,
    distractor_edges: tuple = (),
) -> AttributionResult:
    """Construct a principled-abstention AttributionResult.

    Decision 35 R1: zero/negative-gain returns ``attribution_failed=True``
    instead of raising. Downstream callers check the flag and decide whether
    to skip post-repair / log abstention coverage.
    """
    failure_reason = (
        FAILURE_REASON_NEGATIVE_GAIN
        if top_recovery_gain < 0.0
        else FAILURE_REASON_ZERO_GAIN
    )
    return AttributionResult(
        predicted_label="",
        top_replay="",
        recovery_gain=top_recovery_gain,
        top2_labels=(),
        is_ambiguous=False,
        top_k_labels=(),
        close_deltas=(),
        distractor_provenance_ids=tuple(e.source_id for e in distractor_edges),
        distractor_provenance_edges=tuple(distractor_edges),
        shadow_replay_resolution=None,
        attribution_failed=True,
        failure_reason=failure_reason,
    )
