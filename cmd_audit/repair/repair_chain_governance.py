"""Durable lifecycle governance for ordered, typed repair chains.

This module deliberately wraps :class:`ChainObserver` rather than replacing it.
The observer remains the append-only statistical source; this governor adds the
fail-closed lifecycle decisions that make a useful chain a reusable species.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from statistics import fmean
from typing import Literal, Mapping

from .chain_dynamics import ChainObserver
from .skill_ecology import ChainExecution


ChainLifecycle = Literal["candidate", "probation", "stable", "blocked", "retired"]


def _canonical_payload(value: Mapping[str, object]) -> tuple[dict[str, object], str]:
    """Return a JSON-safe canonical payload and its content identifier."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return json.loads(encoded), hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _chain_id(first_strategy_id: str, second_strategy_id: str) -> str:
    digest = hashlib.sha256(
        f"{first_strategy_id}\x00{second_strategy_id}".encode("utf-8")
    ).hexdigest()
    return f"chain:{digest}"


@dataclass(frozen=True)
class ChainAttemptInput:
    """One deployment-visible attempt of a directed repair chain.

    Strategy identifiers are reusable species IDs, never case-bound intent IDs.
    ``materialized_intermediate`` is the proof that B read A's actual output,
    rather than merely being evaluated alongside A.
    """

    case_id: str
    family_id: str
    event_index: int
    first_strategy_id: str
    second_strategy_id: str
    first_utility: float
    second_utility: float
    chain_utility: float
    materialized_intermediate: bool
    changed_item_count: int
    locality_cost: float
    valid: bool
    rolled_back: bool
    typed_conflict: bool
    anchor_regression: bool
    first_intent_id: str = ""
    second_intent_id: str = ""

    def __post_init__(self) -> None:
        if not self.case_id or not self.family_id:
            raise ValueError("case_id and family_id are required")
        if self.event_index < 0:
            raise ValueError("event_index must be non-negative")
        if not self.first_strategy_id or not self.second_strategy_id:
            raise ValueError("strategy ids are required")
        if self.first_strategy_id == self.second_strategy_id:
            raise ValueError("a repair chain requires two distinct strategies")
        if self.changed_item_count < 0:
            raise ValueError("changed_item_count must be non-negative")
        for name in ("first_utility", "second_utility", "chain_utility", "locality_cost"):
            if not math.isfinite(float(getattr(self, name))):
                raise ValueError(f"{name} must be finite")

    @property
    def chain_benefit(self) -> float:
        return float(self.chain_utility - max(self.first_utility, self.second_utility))


RepairChainAttempt = ChainAttemptInput


@dataclass(frozen=True)
class ChainGovernanceDecision:
    """Immutable, canonical lifecycle decision suitable for durable storage."""

    chain_id: str
    first_strategy_id: str
    second_strategy_id: str
    lifecycle: ChainLifecycle
    reason: str
    event_index: int
    support_count: int
    family_count: int
    chain_benefit: float | None
    conservative_benefit: float | None
    reverse_mean_benefit: float | None
    anti_pattern: bool
    payload: Mapping[str, object]
    decision_sha256: str

    def recomputed_sha256(self) -> str:
        _, digest = _canonical_payload(dict(self.payload))
        return digest

    def to_dict(self) -> dict[str, object]:
        return dict(self.payload)

    def repository_row(self) -> dict[str, object]:
        return {
            "record_type": "repair_chain_governance_decision",
            "chain_id": self.chain_id,
            "event_index": self.event_index,
            "payload_json": json.dumps(self.payload, sort_keys=True, separators=(",", ":")),
            "payload_sha256": self.decision_sha256,
        }


@dataclass
class _ChainRecord:
    chain_id: str
    first_strategy_id: str
    second_strategy_id: str
    created_event_index: int
    lifecycle: ChainLifecycle = "candidate"
    attempts: list[ChainAttemptInput] | None = None
    decisions: list[ChainGovernanceDecision] | None = None

    def __post_init__(self) -> None:
        self.attempts = [] if self.attempts is None else self.attempts
        self.decisions = [] if self.decisions is None else self.decisions


class RepairChainGovernor:
    """Govern ordered repair-chain species with fail-closed deposition rules."""

    def __init__(
        self,
        *,
        arena_id: str = "cmd-v4",
        min_support: int = 3,
        min_families: int = 2,
        min_conservative_benefit: float = 0.05,
        reverse_margin: float = 0.0,
        max_changed_item_count: int = 4,
        max_locality_cost: float = 0.20,
        observer: ChainObserver | None = None,
    ) -> None:
        if min_support < 1 or min_families < 2:
            raise ValueError("support must be positive and family minimum at least two")
        if min_conservative_benefit < 0.0 or reverse_margin < 0.0:
            raise ValueError("benefit margins must be non-negative")
        if max_changed_item_count < 0 or max_locality_cost < 0.0:
            raise ValueError("locality budgets must be non-negative")
        self.arena_id = str(arena_id)
        self.min_support = int(min_support)
        self.min_families = int(min_families)
        self.min_conservative_benefit = float(min_conservative_benefit)
        self.reverse_margin = float(reverse_margin)
        self.max_changed_item_count = int(max_changed_item_count)
        self.max_locality_cost = float(max_locality_cost)
        self.observer = observer or ChainObserver(arena_id=self.arena_id)
        self._admitted: set[str] = set()
        self._records: dict[tuple[str, str], _ChainRecord] = {}
        self._last_event_index = -1

    def admit_strategy(self, strategy_id: str) -> None:
        if not strategy_id or "case" in strategy_id.lower():
            raise ValueError("strategy_id must be a reusable, non-case-bound id")
        self._admitted.add(str(strategy_id))

    def record_attempt(self, attempt: ChainAttemptInput) -> ChainGovernanceDecision:
        """Append one attempt, then return its lifecycle decision.

        The event stream is globally ordered.  Rejected attempts are preserved
        as anti-pattern evidence but are never sent to ``ChainObserver`` as
        valid statistical support.
        """
        if attempt.event_index <= self._last_event_index:
            raise ValueError("repair-chain event indexes must be strictly increasing")
        self._last_event_index = attempt.event_index
        key = (attempt.first_strategy_id, attempt.second_strategy_id)
        record = self._records.setdefault(
            key,
            _ChainRecord(
                chain_id=_chain_id(*key),
                first_strategy_id=key[0],
                second_strategy_id=key[1],
                created_event_index=attempt.event_index,
            ),
        )
        record.attempts.append(attempt)

        rejection = self._rejection_reason(attempt)
        if rejection is not None:
            record.lifecycle = "blocked" if rejection == "component_not_admitted" else "retired"
            return self._decision(record, attempt, rejection, anti_pattern=record.lifecycle == "retired")

        self.observer.record_case(
            case_id=attempt.case_id,
            family_id=attempt.family_id,
            failure_type="repair_chain",
            stream_position=attempt.event_index,
            activated_skill_ids=key,
            chain_executions=(
                ChainExecution(
                    first_skill_id=key[0], second_skill_id=key[1],
                    chained_context="materialized", chained_gain=attempt.chain_utility,
                    standalone_max=max(attempt.first_utility, attempt.second_utility),
                    chain_benefit=attempt.chain_benefit,
                    beneficial=attempt.chain_benefit > 0.0, execution_cost=0.0, status="ok",
                ),
            ),
            changed_item_counts={key: attempt.changed_item_count},
        )
        if attempt.event_index == record.created_event_index:
            return self._decision(record, attempt, "seed_evidence_not_self_validating")
        return self._evaluate_record(record, attempt)

    def evaluate(self, first_strategy_id: str, second_strategy_id: str) -> ChainGovernanceDecision:
        """Re-evaluate a known direction after evidence for either order arrives."""
        key = (str(first_strategy_id), str(second_strategy_id))
        record = self._records.get(key)
        if record is None or not record.attempts:
            raise KeyError("unknown repair chain")
        return self._evaluate_record(record, record.attempts[-1])

    @property
    def decisions(self) -> tuple[ChainGovernanceDecision, ...]:
        return tuple(decision for record in self._records.values() for decision in record.decisions)

    def _rejection_reason(self, attempt: ChainAttemptInput) -> str | None:
        if attempt.first_strategy_id not in self._admitted or attempt.second_strategy_id not in self._admitted:
            return "component_not_admitted"
        if not attempt.materialized_intermediate:
            return "missing_materialized_intermediate"
        if attempt.typed_conflict:
            return "typed_effect_conflict"
        if attempt.changed_item_count > self.max_changed_item_count:
            return "changed_item_budget_exceeded"
        if attempt.locality_cost > self.max_locality_cost:
            return "locality_budget_exceeded"
        if attempt.rolled_back:
            return "rolled_back"
        if attempt.anchor_regression:
            return "anchor_regression"
        if not attempt.valid:
            return "invalid_attempt"
        return None

    def _eligible(self, record: _ChainRecord) -> list[ChainAttemptInput]:
        return [row for row in record.attempts if row.event_index > record.created_event_index and self._rejection_reason(row) is None]

    def _evaluate_record(self, record: _ChainRecord, attempt: ChainAttemptInput) -> ChainGovernanceDecision:
        if record.lifecycle in {"blocked", "retired"}:
            return self._decision(record, attempt, "chain_already_closed", anti_pattern=record.lifecycle == "retired")
        eligible = self._eligible(record)
        benefits = [row.chain_benefit for row in eligible]
        families = {row.family_id for row in eligible}
        reverse = self._records.get((record.second_strategy_id, record.first_strategy_id))
        reverse_benefits = [row.chain_benefit for row in self._eligible(reverse)] if reverse else []
        mean_benefit = fmean(benefits) if benefits else None
        reverse_mean = fmean(reverse_benefits) if reverse_benefits else None
        conservative = min(benefits) if benefits else None
        if mean_benefit is not None and mean_benefit < 0.0:
            record.lifecycle = "retired"
            return self._decision(record, attempt, "negative_marginal_benefit", anti_pattern=True, benefits=benefits, reverse_mean=reverse_mean)
        if reverse_mean is not None and (
            mean_benefit is None
            or reverse_mean > mean_benefit + self.reverse_margin
        ):
            record.lifecycle = "retired"
            return self._decision(record, attempt, "reverse_direction_dominates", anti_pattern=True, benefits=benefits, reverse_mean=reverse_mean)
        if len(eligible) < self.min_support:
            record.lifecycle = "probation" if eligible else "candidate"
            return self._decision(record, attempt, "insufficient_later_support", benefits=benefits, reverse_mean=reverse_mean)
        if len(families) < self.min_families:
            record.lifecycle = "probation"
            return self._decision(record, attempt, "insufficient_family_diversity", benefits=benefits, reverse_mean=reverse_mean)
        if conservative is None or conservative <= self.min_conservative_benefit:
            record.lifecycle = "probation"
            return self._decision(record, attempt, "conservative_benefit_not_positive", benefits=benefits, reverse_mean=reverse_mean)
        record.lifecycle = "stable"
        return self._decision(record, attempt, "promoted", benefits=benefits, reverse_mean=reverse_mean)

    def _decision(self, record: _ChainRecord, attempt: ChainAttemptInput, reason: str, *, anti_pattern: bool = False, benefits: list[float] | None = None, reverse_mean: float | None = None) -> ChainGovernanceDecision:
        eligible = self._eligible(record)
        values = benefits if benefits is not None else [row.chain_benefit for row in eligible]
        payload_input = {
            "record_type": "repair_chain_governance_decision", "protocol": "cmd-neuro-symbolic-memory-evolution-v4",
            "chain_id": record.chain_id, "first_strategy_id": record.first_strategy_id, "second_strategy_id": record.second_strategy_id,
            "lifecycle": record.lifecycle, "reason": reason, "event_index": attempt.event_index,
            "support_count": len(eligible), "family_count": len({row.family_id for row in eligible}),
            "chain_benefit": attempt.chain_benefit, "conservative_benefit": min(values) if values else None,
            "reverse_mean_benefit": reverse_mean, "anti_pattern": anti_pattern,
            "attempt": asdict(attempt),
        }
        payload, digest = _canonical_payload(payload_input)
        decision = ChainGovernanceDecision(
            chain_id=record.chain_id, first_strategy_id=record.first_strategy_id, second_strategy_id=record.second_strategy_id,
            lifecycle=record.lifecycle, reason=reason, event_index=attempt.event_index,
            support_count=len(eligible), family_count=len({row.family_id for row in eligible}), chain_benefit=attempt.chain_benefit,
            conservative_benefit=min(values) if values else None, reverse_mean_benefit=reverse_mean,
            anti_pattern=anti_pattern, payload=payload, decision_sha256=digest,
        )
        record.decisions.append(decision)
        return decision


ChainGovernor = RepairChainGovernor
