"""Operation-level attribution from replay deltas.

Subpackage structure (Decision 35 Day 3):
- ``ranking.py`` — unified ``assign_attribution`` with V0/V1 merge
- ``shadow.py`` — route/retrieval shadow disambiguation
- ``failure.py`` — zero/negative-gain abstention (D35 R1)
"""

from .failure import (
    FAILURE_REASON_NEGATIVE_GAIN,
    FAILURE_REASON_ZERO_GAIN,
    AttributionResult,
    build_abstain_result,
)
from .ranking import assign_attribution
from .shadow import disambiguate_route_retrieval_shadow


__all__ = [
    "AttributionResult",
    "assign_attribution",
    "build_abstain_result",
    "disambiguate_route_retrieval_shadow",
    "_disambiguate_route_retrieval_shadow",  # exposed for tests
    "FAILURE_REASON_ZERO_GAIN",
    "FAILURE_REASON_NEGATIVE_GAIN",
]


# Alias for backward compatibility with tests
_disambiguate_route_retrieval_shadow = disambiguate_route_retrieval_shadow
