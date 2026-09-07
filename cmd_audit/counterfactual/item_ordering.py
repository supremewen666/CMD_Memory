"""Deployment-visible ordering evidence for semantic memory relations.

This module deliberately has no text, store, or recall-rank input.  A relation
sensor may establish that two memories disagree, but destructive repair needs
an independently supplied ordering sidecar.  Missing, hidden, untrusted, or
internally inconsistent evidence is therefore ``UNKNOWN``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

__all__ = [
    "EvidenceReliability", "OrderingRelation", "OrderingEvidence",
    "REGISTERED_SOURCE_SEMANTICS", "OrderingPolicy", "SourceComparison",
    "OrderingVerdict", "compare_ordering_sources", "resolve_ordering",
]


class EvidenceReliability(str, Enum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"
    UNKNOWN = "unknown"


class OrderingRelation(str, Enum):
    LEFT_TARGET_RIGHT_SURVIVES = "left_target_right_survives"
    RIGHT_TARGET_LEFT_SURVIVES = "right_target_left_survives"
    UNKNOWN = "unknown"
    CONFLICTING = "conflicting"


_ORDERING_SOURCES = frozenset({"observed_at", "event_sequence", "source_priority"})
REGISTERED_SOURCE_SEMANTICS = {
    "observed_at": "chronology_lower_target",
    "event_sequence": "chronology_lower_target",
    "source_priority": "higher_wins",
}


@dataclass(frozen=True)
class OrderingPolicy:
    """A preregistered allowlist; evidence existence does not imply authority."""

    policy_version: str
    accepted_sources: tuple[str, ...]
    source_semantics: tuple[tuple[str, str], ...]
    require_agreement: bool = True

    def __post_init__(self) -> None:
        if not self.policy_version:
            raise ValueError("ordering policy needs a version")
        if not self.accepted_sources:
            raise ValueError("ordering policy needs at least one accepted source")
        unknown = set(self.accepted_sources) - _ORDERING_SOURCES
        if unknown:
            raise ValueError(f"unregistered ordering sources: {sorted(unknown)}")
        if len(set(self.accepted_sources)) != len(self.accepted_sources):
            raise ValueError("ordering policy sources must be unique")
        if not self.require_agreement:
            raise ValueError("v3 has no precedence fallback; agreement is required")
        semantics = dict(self.source_semantics)
        if len(semantics) != len(self.source_semantics):
            raise ValueError("ordering policy source semantics must be unique")
        if set(semantics) != set(self.accepted_sources):
            raise ValueError("every accepted source needs exactly one frozen semantic")
        for source, semantic in semantics.items():
            if semantic != REGISTERED_SOURCE_SEMANTICS[source]:
                raise ValueError(
                    f"unsupported semantic {semantic!r} for ordering source {source!r}"
                )


@dataclass(frozen=True)
class OrderingEvidence:
    """One item's sidecar, supplied by the deployment rather than inferred.

    Higher ``event_sequence`` and higher ``source_priority`` win.  ``observed_at``
    is chronological.  Every value is ignored unless the sidecar is visible at
    deployment and marked trusted.
    """

    item_id: str
    observed_at: datetime | None = None
    observed_at_domain: str | None = None
    event_sequence: int | None = None
    event_stream_id: str | None = None
    source_priority: int | None = None
    source_priority_domain: str | None = None
    provenance: str = ""
    audit_version: str = ""
    deployment_visible: bool = False
    reliability: EvidenceReliability = EvidenceReliability.UNKNOWN

    def __post_init__(self) -> None:
        if not self.item_id:
            raise ValueError("ordering evidence needs an item ID")
        if self.observed_at is not None and self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.event_sequence is not None and (
            isinstance(self.event_sequence, bool)
            or not isinstance(self.event_sequence, int)
            or self.event_sequence < 0
        ):
            raise ValueError("event_sequence must be a non-negative integer")
        if self.source_priority is not None and (
            isinstance(self.source_priority, bool)
            or not isinstance(self.source_priority, int)
        ):
            raise ValueError("source_priority must be an integer")
        for value, domain, name in (
            (self.observed_at, self.observed_at_domain, "observed_at_domain"),
            (self.event_sequence, self.event_stream_id, "event_stream_id"),
            (
                self.source_priority,
                self.source_priority_domain,
                "source_priority_domain",
            ),
        ):
            if value is None and domain is not None:
                raise ValueError(f"{name} cannot exist without its value")

    @property
    def usable(self) -> bool:
        return (
            self.deployment_visible
            and self.reliability is EvidenceReliability.TRUSTED
            and bool(self.provenance)
            and self.provenance != "none"
            and bool(self.audit_version)
        )


@dataclass(frozen=True)
class OrderingVerdict:
    relation: OrderingRelation
    agreeing_sources: tuple[str, ...]
    reason_code: str
    policy_version: str
    conflicting_sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.reason_code or not self.policy_version:
            raise ValueError("ordering verdict needs reason and policy version")
        directional = self.relation in {
            OrderingRelation.LEFT_TARGET_RIGHT_SURVIVES,
            OrderingRelation.RIGHT_TARGET_LEFT_SURVIVES,
        }
        if directional != bool(self.agreeing_sources):
            raise ValueError(
                "directional verdicts require agreeing sources and refusals cannot claim them"
            )
        if (self.relation is OrderingRelation.CONFLICTING) != bool(
            self.conflicting_sources
        ):
            raise ValueError(
                "only conflicting verdicts require conflicting sources"
            )
        if self.agreeing_sources and self.conflicting_sources:
            raise ValueError("ordering sources cannot agree and conflict")


@dataclass(frozen=True)
class SourceComparison:
    source: str
    semantic: str
    comparable_domain: str | None
    left_value: object | None
    right_value: object | None
    comparable: bool
    outcome: str
    reason_code: str

    def __post_init__(self) -> None:
        if self.source not in _ORDERING_SOURCES:
            raise ValueError("unregistered comparison source")
        if self.semantic != REGISTERED_SOURCE_SEMANTICS[self.source]:
            raise ValueError("source comparison semantic drift")
        if self.outcome not in {
            "left_target_right_survives",
            "right_target_left_survives",
            "equal",
            "incomplete",
            "incomparable_domain",
        }:
            raise ValueError("unregistered source comparison outcome")
        if not self.reason_code:
            raise ValueError("source comparison reason is required")


def compare_ordering_sources(
    left: OrderingEvidence,
    right: OrderingEvidence,
    *,
    policy: OrderingPolicy,
) -> tuple[SourceComparison, ...]:
    """Materialize every accepted-source comparison in frozen policy order."""
    values = {
        "observed_at": (
            left.observed_at,
            right.observed_at,
            left.observed_at_domain,
            right.observed_at_domain,
        ),
        "event_sequence": (
            left.event_sequence,
            right.event_sequence,
            left.event_stream_id,
            right.event_stream_id,
        ),
        "source_priority": (
            left.source_priority,
            right.source_priority,
            left.source_priority_domain,
            right.source_priority_domain,
        ),
    }
    rows: list[SourceComparison] = []
    for source in policy.accepted_sources:
        left_value, right_value, left_domain, right_domain = values[source]
        common_domain = left_domain if left_domain and left_domain == right_domain else None
        if left_value is None and right_value is None:
            outcome, reason, comparable = "equal", "source_unavailable", False
        elif left_value is None or right_value is None:
            outcome, reason, comparable = "incomplete", f"incomplete_{source}_sidecar", False
        elif common_domain is None:
            outcome, reason, comparable = "incomparable_domain", f"incomparable_{source}_domain", False
        elif left_value == right_value:
            outcome, reason, comparable = "equal", "equal_nondirectional", True
        elif left_value < right_value:
            outcome, reason, comparable = (
                "left_target_right_survives",
                "directional",
                True,
            )
        else:
            outcome, reason, comparable = (
                "right_target_left_survives",
                "directional",
                True,
            )
        rows.append(
            SourceComparison(
                source=source,
                semantic=REGISTERED_SOURCE_SEMANTICS[source],
                comparable_domain=common_domain,
                left_value=left_value,
                right_value=right_value,
                comparable=comparable,
                outcome=outcome,
                reason_code=reason,
            )
        )
    return tuple(rows)


def _compare(left: object, right: object) -> OrderingRelation:
    if left == right:
        return OrderingRelation.UNKNOWN
    return (
        OrderingRelation.LEFT_TARGET_RIGHT_SURVIVES
        if left < right
        else OrderingRelation.RIGHT_TARGET_LEFT_SURVIVES
    )


def resolve_ordering(
    left: OrderingEvidence,
    right: OrderingEvidence,
    *,
    policy: OrderingPolicy,
) -> OrderingVerdict:
    """Resolve direction only from mutually available trusted sidecars.

    Signals must agree.  This conservative rule prevents a source-priority
    convention from silently overriding an observed event chronology.
    """
    if left.item_id == right.item_id:
        return OrderingVerdict(
            OrderingRelation.UNKNOWN, (), "same_item", policy.policy_version
        )
    if not left.usable or not right.usable:
        return OrderingVerdict(
            OrderingRelation.UNKNOWN,
            (),
            "sidecar_not_audited_trusted_and_visible",
            policy.policy_version,
        )
    comparisons = compare_ordering_sources(left, right, policy=policy)
    signals: list[tuple[str, OrderingRelation]] = []
    for row in comparisons:
        if row.outcome == "incomplete":
            return OrderingVerdict(
                OrderingRelation.UNKNOWN,
                (),
                row.reason_code,
                policy.policy_version,
            )
        if row.outcome == "incomparable_domain":
            return OrderingVerdict(
                OrderingRelation.UNKNOWN,
                (),
                row.reason_code,
                policy.policy_version,
            )
        if row.outcome == "left_target_right_survives":
            signals.append((row.source, OrderingRelation.LEFT_TARGET_RIGHT_SURVIVES))
        elif row.outcome == "right_target_left_survives":
            signals.append((row.source, OrderingRelation.RIGHT_TARGET_LEFT_SURVIVES))
    if not signals:
        return OrderingVerdict(
            OrderingRelation.UNKNOWN,
            (),
            "no_comparable_accepted_sidecar",
            policy.policy_version,
        )
    relations = {relation for _, relation in signals}
    if len(relations) != 1:
        return OrderingVerdict(
            OrderingRelation.CONFLICTING,
            (),
            "conflicting_sidecars",
            policy.policy_version,
            tuple(source for source, _ in signals),
        )
    relation = signals[0][1]
    sources = tuple(source for source, _ in signals)
    return OrderingVerdict(
        relation,
        sources,
        "trusted_deployment_sidecar",
        policy.policy_version,
    )
