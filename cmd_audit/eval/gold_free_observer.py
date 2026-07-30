"""Append-only observations of gold-free and shadow-gold repair signals.

The observer is deliberately downstream of execution.  It accepts two score
vectors that have already been produced, validates that they describe the same
candidate set, and records descriptive statistics.  It never returns a skill
choice to the runtime path.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Mapping, Sequence

from ..core.math_utils import is_finite_number, mean_finite
from .gold_free_identifiability import (
    CandidateScore,
    CaseRankingInput,
    RuntimeSelectionProvenance,
    analyze_gold_free_agreement,
)


NULL_FAILURE_TYPE = "null"


@dataclass(frozen=True)
class ProbeCoordinates:
    age_sessions: int | None = None
    question_type: str = ""
    evidence_condition: str = ""


@dataclass(frozen=True)
class GoldFreeObservation:
    arena_id: str
    case_id: str
    family_id: str
    failure_type: str
    coordinates: ProbeCoordinates
    selected_skill_id: str | None
    oracle_skill_id: str | None
    runtime_abstained: bool
    top1_agreement: bool | None
    spearman_rho: float | None
    oracle_rank_of_selected: float | None
    shadow_regret: float | None
    gold_free_margin: float | None
    shadow_gold_margin: float | None
    selected_shadow_gain: float | None
    oracle_shadow_gain: float | None
    null_false_positive: bool
    gold_free_scores: tuple[tuple[str, float | None], ...]
    shadow_gold_scores: tuple[tuple[str, float | None], ...]

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["record_type"] = "gold_free_observation"
        return value


@dataclass(frozen=True)
class SignalSlice:
    slice_key: str
    case_count: int
    ranked_case_count: int
    mean_spearman_rho: float | None
    top1_agreement_rate: float | None
    abstention_rate: float
    null_false_positive_rate: float | None
    mean_shadow_regret: float | None


class GoldFreeObserver:
    """Append-only recorder for experiments A1--A4."""

    def __init__(
        self,
        *,
        arena_id: str,
        null_effect_tolerance: float = 0.01,
        tie_tolerance: float = 1e-12,
    ) -> None:
        if null_effect_tolerance < 0:
            raise ValueError("null_effect_tolerance must be >= 0")
        if tie_tolerance < 0:
            raise ValueError("tie_tolerance must be >= 0")
        self.arena_id = str(arena_id)
        self.null_effect_tolerance = float(null_effect_tolerance)
        self.tie_tolerance = float(tie_tolerance)
        self._observations: list[GoldFreeObservation] = []

    @property
    def observations(self) -> tuple[GoldFreeObservation, ...]:
        return tuple(self._observations)

    def record(
        self,
        *,
        case_id: str,
        family_id: str,
        failure_type: str | None,
        gold_free_scores: Mapping[str, float | None],
        shadow_gold_scores: Mapping[str, float | None],
        runtime_abstained: bool,
        coordinates: ProbeCoordinates = ProbeCoordinates(),
        runtime_provenance: RuntimeSelectionProvenance = (
            RuntimeSelectionProvenance()
        ),
    ) -> GoldFreeObservation:
        normalized_failure = str(failure_type or NULL_FAILURE_TYPE)
        ranking = CaseRankingInput(
            case_id=str(case_id),
            failure_type=normalized_failure,
            gold_free_scores=_candidate_scores(gold_free_scores),
            shadow_gold_scores=_candidate_scores(shadow_gold_scores),
            runtime_provenance=runtime_provenance,
        )
        rows, _report = analyze_gold_free_agreement(
            (ranking,),
            tie_tolerance=self.tie_tolerance,
        )
        row = rows[0]
        selected = None if runtime_abstained else row.gold_free_top_skill
        shadow_finite = _finite_scores(shadow_gold_scores)
        selected_shadow = (
            shadow_finite.get(selected) if selected is not None else None
        )
        oracle_shadow = (
            max(shadow_finite.values()) if shadow_finite else None
        )
        null_false_positive = (
            normalized_failure == NULL_FAILURE_TYPE
            and selected is not None
            and (
                selected_shadow is None
                or selected_shadow <= self.null_effect_tolerance
            )
        )
        observation = GoldFreeObservation(
            arena_id=self.arena_id,
            case_id=str(case_id),
            family_id=str(family_id),
            failure_type=normalized_failure,
            coordinates=coordinates,
            selected_skill_id=selected,
            oracle_skill_id=row.gold_supervised_top_skill,
            runtime_abstained=bool(runtime_abstained),
            top1_agreement=(
                None
                if selected is None or row.gold_supervised_top_skill is None
                else selected == row.gold_supervised_top_skill
            ),
            spearman_rho=spearman_score_correlation(
                gold_free_scores,
                shadow_gold_scores,
            ),
            oracle_rank_of_selected=_descending_average_rank(
                shadow_finite,
                selected,
            ),
            shadow_regret=(
                oracle_shadow - selected_shadow
                if oracle_shadow is not None and selected_shadow is not None
                else None
            ),
            gold_free_margin=row.gold_free_margin,
            shadow_gold_margin=row.gold_supervised_margin,
            selected_shadow_gain=selected_shadow,
            oracle_shadow_gain=oracle_shadow,
            null_false_positive=null_false_positive,
            gold_free_scores=tuple(sorted(gold_free_scores.items())),
            shadow_gold_scores=tuple(sorted(shadow_gold_scores.items())),
        )
        self._observations.append(observation)
        return observation

    def summarize(
        self,
        *,
        slice_by: str = "failure_type",
    ) -> tuple[SignalSlice, ...]:
        supported = {
            "failure_type",
            "age_sessions",
            "question_type",
            "evidence_condition",
            "arena_id",
        }
        if slice_by not in supported:
            raise ValueError(f"unsupported signal slice: {slice_by}")
        groups: dict[str, list[GoldFreeObservation]] = {}
        for row in self._observations:
            if slice_by == "failure_type":
                key = row.failure_type
            elif slice_by == "arena_id":
                key = row.arena_id
            else:
                raw = getattr(row.coordinates, slice_by)
                key = "<missing>" if raw in (None, "") else str(raw)
            groups.setdefault(key, []).append(row)
        return tuple(
            _summarize_slice(key, groups[key])
            for key in sorted(groups)
        )


def spearman_score_correlation(
    gold_free_scores: Mapping[str, float | None],
    shadow_gold_scores: Mapping[str, float | None],
) -> float | None:
    """Spearman rho over candidates with two finite scores, with average ties."""
    if set(gold_free_scores) != set(shadow_gold_scores):
        raise ValueError("gold-free and shadow-gold candidate sets differ")
    paired_ids = [
        skill_id
        for skill_id in sorted(gold_free_scores)
        if is_finite_number(gold_free_scores[skill_id])
        and is_finite_number(shadow_gold_scores[skill_id])
    ]
    if len(paired_ids) < 2:
        return None
    left = {
        skill_id: float(gold_free_scores[skill_id])
        for skill_id in paired_ids
    }
    right = {
        skill_id: float(shadow_gold_scores[skill_id])
        for skill_id in paired_ids
    }
    left_ranks = _average_ranks(left)
    right_ranks = _average_ranks(right)
    left_values = [left_ranks[key] for key in paired_ids]
    right_values = [right_ranks[key] for key in paired_ids]
    left_mean = sum(left_values) / len(left_values)
    right_mean = sum(right_values) / len(right_values)
    numerator = sum(
        (a - left_mean) * (b - right_mean)
        for a, b in zip(left_values, right_values)
    )
    left_norm = math.sqrt(sum((a - left_mean) ** 2 for a in left_values))
    right_norm = math.sqrt(sum((b - right_mean) ** 2 for b in right_values))
    if left_norm == 0 or right_norm == 0:
        return None
    return numerator / (left_norm * right_norm)


def _candidate_scores(
    scores: Mapping[str, float | None],
) -> tuple[CandidateScore, ...]:
    return tuple(
        CandidateScore(str(skill_id), gain)
        for skill_id, gain in sorted(scores.items())
    )


def _finite_scores(
    scores: Mapping[str, float | None],
) -> dict[str, float]:
    return {
        str(skill_id): float(value)
        for skill_id, value in scores.items()
        if is_finite_number(value)
    }


def _average_ranks(scores: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    ranks: dict[str, float] = {}
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        average = ((index + 1) + end) / 2.0
        for skill_id, _score in ordered[index:end]:
            ranks[skill_id] = average
        index = end
    return ranks


def _descending_average_rank(
    scores: Mapping[str, float],
    selected_skill_id: str | None,
) -> float | None:
    if selected_skill_id is None or selected_skill_id not in scores:
        return None
    return _average_ranks(scores)[selected_skill_id]


def _summarize_slice(
    key: str,
    rows: Sequence[GoldFreeObservation],
) -> SignalSlice:
    agreements = [row.top1_agreement for row in rows if row.top1_agreement is not None]
    null_rows = [row for row in rows if row.failure_type == NULL_FAILURE_TYPE]
    return SignalSlice(
        slice_key=key,
        case_count=len(rows),
        ranked_case_count=sum(row.spearman_rho is not None for row in rows),
        mean_spearman_rho=mean_finite(row.spearman_rho for row in rows),
        top1_agreement_rate=(
            sum(value is True for value in agreements) / len(agreements)
            if agreements
            else None
        ),
        abstention_rate=(
            sum(row.runtime_abstained for row in rows) / len(rows)
            if rows
            else 0.0
        ),
        null_false_positive_rate=(
            sum(row.null_false_positive for row in null_rows) / len(null_rows)
            if null_rows
            else None
        ),
        mean_shadow_regret=mean_finite(row.shadow_regret for row in rows),
    )
