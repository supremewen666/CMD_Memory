"""Attribution result types and offline replay-baseline helpers.

Subpackage structure:
- ``ranking.py`` — offline replay-baseline attribution for current step actions
- ``shadow.py`` — route/retrieval shadow disambiguation
- ``failure.py`` — zero/negative-gain abstention (D35 R1)
"""

from .failure import (
    FAILURE_REASON_NEGATIVE_GAIN,
    FAILURE_REASON_OUT_OF_SCOPE_REPLAY,
    FAILURE_REASON_ZERO_GAIN,
    AttributionResult,
    build_abstain_result,
)
from .ranking import assign_attribution, assign_replay_baseline_attribution
from .shadow import disambiguate_route_retrieval_shadow


__all__ = [
    "AttributionResult",
    "assign_attribution",
    "assign_replay_baseline_attribution",
    "build_abstain_result",
    "disambiguate_route_retrieval_shadow",
    "_disambiguate_route_retrieval_shadow",  # exposed for tests
    "FAILURE_REASON_ZERO_GAIN",
    "FAILURE_REASON_NEGATIVE_GAIN",
    "FAILURE_REASON_OUT_OF_SCOPE_REPLAY",
]


# Alias for backward compatibility with tests
_disambiguate_route_retrieval_shadow = disambiguate_route_retrieval_shadow
