"""Zero-call counterfactual influence proxy — task.md 3.3 (C2 method core).

MemAudit (2605.23723, Eq. 7) scores a memory's causal contribution by removing
it and replaying the agent:

    CMIS(m_i) = h(q*, y*) - h(q*, f(q*, R(q*, M \\ {m_i})))

with ``h`` a harm scorer and ``f`` the agent.  Algorithm 1 runs that inner
replay once per retrieved memory per harmful event, so the estimator costs
``O(|R*|)`` agent invocations — every one an LLM call — and it needs the harmful
event ``(q*, y*, R*)`` handed to it from outside.

This module computes the same *ranking* target from typed telemetry that a
repair executor already emits, at **zero LLM calls**:

    proxy(i) = success(effect_i, t_i) - locality_cost_i - execution_cost(t_i)

where ``t_i`` is the four-channel record ``(valid, rolled_back,
changed_item_count, locality_cost)``.  Nothing here calls a model, and nothing
reads ``recovery_gain`` or any ``gold_*`` field.

What is being claimed is deliberately narrow.  A proxy that reproduced CMIS
pointwise would be a stronger and less believable claim than the one the paper
needs; what a repair loop actually consumes is an *ordering* over candidate
repairs.  So :func:`measure_telemetry_cmis_gap` reports both a scale gap and
rank agreement, and the rank column is the headline.

The reference channel (``recovery_gain``, materialized offline) is read **only**
by this measurement, and only to quantify the gap.  It never flows back into
the proxy — that separation is what the permutation control in
``experiments/ghost_ecology_zero_call.py`` exists to falsify.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import fmean
from typing import Callable, Mapping, Sequence


TELEMETRY_CMIS_SCHEMA_VERSION = "cmd-telemetry-cmis-proxy-v1"
GAP_SCHEMA_VERSION = "cmd-telemetry-cmis-gap-v1"
CLAIM_REGISTRY_SCHEMA_VERSION = "cmd-router-claim-registry-v1"

# Mutating effects must move at least one item to count as executed; no-op
# effects must move none.  Mirrors REGISTERED_PROBES in the zero-call runner.
_NO_MUTATION_EFFECTS = frozenset({"verify", "abstain"})

# Cost per changed item, capped at 1.0.  Shared with deployment_reward so the
# proxy and the online reward agree on what a repair costs.
_EXECUTION_COST_PER_ITEM = 0.05


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{name} must be finite")
    return converted


@dataclass(frozen=True)
class TelemetryChannels:
    """The four typed channels a repair executor emits at zero LLM cost."""

    valid: bool
    rolled_back: bool
    changed_item_count: int
    locality_cost: float

    def __post_init__(self) -> None:
        if not isinstance(self.valid, bool) or not isinstance(self.rolled_back, bool):
            raise ValueError("valid and rolled_back must be booleans")
        if (
            isinstance(self.changed_item_count, bool)
            or not isinstance(self.changed_item_count, int)
            or self.changed_item_count < 0
        ):
            raise ValueError("changed_item_count must be a non-negative integer")
        locality = _finite(self.locality_cost, "locality_cost")
        if locality < 0.0:
            raise ValueError("locality_cost must be non-negative")
        object.__setattr__(self, "locality_cost", locality)

    @classmethod
    def from_outcome(cls, outcome: object) -> "TelemetryChannels":
        """Read the four channels off a ``V4CandidateOutcome``-shaped record.

        Attributes are pulled by name rather than by isinstance so a frozen
        materialized outcome, a live executor result, and a test double all
        work.  ``recovery_gain`` is deliberately not among them.
        """
        return cls(
            valid=bool(getattr(outcome, "valid")),
            rolled_back=bool(getattr(outcome, "rolled_back")),
            changed_item_count=int(getattr(outcome, "changed_item_count")),
            locality_cost=float(getattr(outcome, "locality_cost")),
        )


def telemetry_cmis_proxy(effect: str, channels: TelemetryChannels) -> float:
    """Zero-call stand-in for ``CMIS(m_i)``, in [-1, 1].

    A guard failure or rollback is the telemetry signature of a repair that
    should never be ranked above an executed one, so it floors at -1.0 rather
    than being scored on locality.
    """
    if not effect:
        raise ValueError("effect is required")
    if not channels.valid or channels.rolled_back:
        return -1.0
    if effect in _NO_MUTATION_EFFECTS:
        success = float(channels.changed_item_count == 0)
    else:
        success = float(channels.changed_item_count > 0)
    execution_cost = min(1.0, _EXECUTION_COST_PER_ITEM * channels.changed_item_count)
    return max(-1.0, min(1.0, success - channels.locality_cost - execution_cost))


def replay_cmis(
    *,
    harm_before: float,
    harm_after: float,
) -> float:
    """MemAudit Eq. 7 for one memory, given the two harm scores.

    Kept explicit so the E2 table can show the replay estimator it is compared
    against.  Each ``harm_after`` costs one agent replay upstream; this function
    only does the subtraction.
    """
    return _finite(harm_before, "harm_before") - _finite(harm_after, "harm_after")


@dataclass(frozen=True)
class ProxyRow:
    """One candidate scored by both estimators."""

    intent_id: str
    effect: str
    proxy_score: float
    reference_score: float
    model_calls: int = 0


def _spearman(left: Sequence[float], right: Sequence[float]) -> float:
    """Rank correlation with midranks for ties.

    Ties are the common case here: telemetry takes few distinct values, so a
    tie-blind implementation would understate agreement.
    """
    if len(left) != len(right) or len(left) < 2:
        return 0.0

    def ranks(values: Sequence[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        result = [0.0] * len(values)
        index = 0
        while index < len(order):
            stop = index
            while (
                stop + 1 < len(order)
                and values[order[stop + 1]] == values[order[index]]
            ):
                stop += 1
            midrank = (index + stop) / 2.0 + 1.0
            for position in range(index, stop + 1):
                result[order[position]] = midrank
            index = stop + 1
        return result

    left_ranks = ranks(left)
    right_ranks = ranks(right)
    left_mean = fmean(left_ranks)
    right_mean = fmean(right_ranks)
    left_ss = sum((value - left_mean) ** 2 for value in left_ranks)
    right_ss = sum((value - right_mean) ** 2 for value in right_ranks)
    if left_ss <= 0.0 or right_ss <= 0.0:
        return 0.0
    cross = sum(
        (x - left_mean) * (y - right_mean)
        for x, y in zip(left_ranks, right_ranks, strict=True)
    )
    return cross / math.sqrt(left_ss * right_ss)


def _pairwise_concordance(rows: Sequence[ProxyRow]) -> tuple[int, int]:
    """Count comparable and concordant within-group pairs.

    This is the quantity a repair loop actually depends on: given two candidate
    repairs, does the zero-call channel order them the way the reference does?
    Pairs where either estimator is indifferent are not comparable.
    """
    comparable = concordant = 0
    for index, current in enumerate(rows):
        for previous in rows[:index]:
            proxy_delta = current.proxy_score - previous.proxy_score
            reference_delta = current.reference_score - previous.reference_score
            if proxy_delta == 0.0 or reference_delta == 0.0:
                continue
            comparable += 1
            concordant += int(proxy_delta * reference_delta > 0.0)
    return comparable, concordant


def build_proxy_rows(
    intents: Sequence[object],
    outcomes: Mapping[str, object],
    *,
    reference: Callable[[object], float],
) -> tuple[ProxyRow, ...]:
    """Score every intent with the proxy and with the reference estimator.

    ``reference`` is the only place a materialized signal is read; passing it in
    keeps the audit boundary visible at the call site instead of buried here.
    """
    rows: list[ProxyRow] = []
    for intent in intents:
        intent_id = str(getattr(intent, "intent_id"))
        outcome = outcomes.get(intent_id)
        if outcome is None:
            raise ValueError(f"no outcome for intent {intent_id}")
        effect = str(getattr(intent, "effect"))
        rows.append(
            ProxyRow(
                intent_id=intent_id,
                effect=effect,
                proxy_score=telemetry_cmis_proxy(
                    effect, TelemetryChannels.from_outcome(outcome)
                ),
                reference_score=_finite(reference(outcome), "reference score"),
            )
        )
    return tuple(rows)


def measure_telemetry_cmis_gap(
    groups: Mapping[str, Sequence[ProxyRow]],
) -> dict[str, object]:
    """Aggregate the surrogate gap between the zero-call and reference scores.

    ``groups`` maps a grouping key (a case, or a family) to its candidate rows.
    Ranking quality is measured *within* group because that is where a repair
    loop chooses; the scale gap is reported globally because it is a statement
    about the estimators, not about any one choice.
    """
    if not groups:
        raise ValueError("gap measurement requires at least one group")
    flat = [row for rows in groups.values() for row in rows]
    if not flat:
        raise ValueError("gap measurement requires at least one candidate row")

    comparable_total = concordant_total = 0
    per_group_spearman: list[float] = []
    for rows in groups.values():
        comparable, concordant = _pairwise_concordance(tuple(rows))
        comparable_total += comparable
        concordant_total += concordant
        if len(rows) >= 2:
            per_group_spearman.append(
                _spearman(
                    [row.proxy_score for row in rows],
                    [row.reference_score for row in rows],
                )
            )

    absolute_gaps = [
        abs(row.proxy_score - row.reference_score) for row in flat
    ]
    signed_gaps = [row.proxy_score - row.reference_score for row in flat]
    return {
        "schema_version": GAP_SCHEMA_VERSION,
        "proxy_schema_version": TELEMETRY_CMIS_SCHEMA_VERSION,
        "model_calls": 0,
        "proxy_reads_reference_channel": False,
        "group_count": len(groups),
        "candidate_count": len(flat),
        "mean_absolute_gap": fmean(absolute_gaps),
        "max_absolute_gap": max(absolute_gaps),
        "mean_signed_gap": fmean(signed_gaps),
        "global_spearman": _spearman(
            [row.proxy_score for row in flat],
            [row.reference_score for row in flat],
        ),
        "mean_within_group_spearman": (
            fmean(per_group_spearman) if per_group_spearman else 0.0
        ),
        "within_group_pairwise_concordance": (
            0.0 if not comparable_total else concordant_total / comparable_total
        ),
        "comparable_pair_count": comparable_total,
        "replay_calls_avoided": len(flat),
    }


def measure_domain_failure_gaps(
    groups: Mapping[tuple[str, str], Sequence[ProxyRow]],
    *, thresholds: Mapping[str, float],
) -> dict[str, object]:
    """Aggregate telemetry-vs-replay evidence without a cross-domain headline."""
    cells: list[dict[str, object]] = []
    for (domain, failure_type), rows in sorted(groups.items()):
        report = measure_telemetry_cmis_gap({f"{domain}:{failure_type}": rows})
        tau = float(thresholds.get(domain, float("nan")))
        rank = float(report["within_group_pairwise_concordance"])
        status = "pass" if math.isfinite(tau) and float(report["mean_absolute_gap"]) <= tau and rank > 0.5 else "conditional"
        cells.append({"domain": domain, "failure_type": failure_type,
                      "gap": report["mean_absolute_gap"], "rank": rank,
                      "ci_lower_95": report["mean_signed_gap"], "tau_gap": tau,
                      "claim_status": status, "model_calls": 0})
    claims: dict[str, str] = {}
    for domain in sorted({str(cell["domain"]) for cell in cells}):
        own = [cell for cell in cells if cell["domain"] == domain]
        tau = float(thresholds.get(domain, float("nan")))
        claims[domain] = "pass" if math.isfinite(tau) and own and all(cell["claim_status"] == "pass" for cell in own) else "UNVERIFIED"
    return {"schema_version": CLAIM_REGISTRY_SCHEMA_VERSION, "cells": cells,
            "claims": claims, "router_claim_requires_registered_tau": True}


__all__ = [
    "GAP_SCHEMA_VERSION",
    "TELEMETRY_CMIS_SCHEMA_VERSION",
    "ProxyRow",
    "TelemetryChannels",
    "build_proxy_rows",
    "measure_telemetry_cmis_gap",
    "measure_domain_failure_gaps",
    "CLAIM_REGISTRY_SCHEMA_VERSION",
    "replay_cmis",
    "telemetry_cmis_proxy",
]
