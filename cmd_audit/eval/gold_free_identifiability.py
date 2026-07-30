"""Empirical gold-free versus shadow-gold repair-selection analysis.

Runtime selection and shadow evaluation are deliberately separate inputs.  The
gold-free ranker never receives the shadow scores; this module joins the two
rankings only after both have been materialized for analysis.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True)
class CandidateScore:
    skill_id: str
    gain: float | None


@dataclass(frozen=True)
class RuntimeSelectionProvenance:
    """Structural audit flags produced by the runtime path."""

    context_constructed_without_gold: bool = True
    selection_used_gold: bool = False
    shadow_scores_isolated: bool = True

    def validate(self) -> None:
        if not self.context_constructed_without_gold:
            raise ValueError("runtime context construction used gold")
        if self.selection_used_gold:
            raise ValueError("runtime selection used gold")
        if not self.shadow_scores_isolated:
            raise ValueError("shadow gold scores were not isolated")


@dataclass(frozen=True)
class CaseRankingInput:
    case_id: str
    failure_type: str
    gold_free_scores: tuple[CandidateScore, ...]
    shadow_gold_scores: tuple[CandidateScore, ...]
    runtime_provenance: RuntimeSelectionProvenance = RuntimeSelectionProvenance()


@dataclass(frozen=True)
class GoldFreeSelection:
    skill_id: str | None
    tied_skill_ids: tuple[str, ...]
    margin: float | None
    finite_candidate_count: int
    missing_or_nonfinite_skill_ids: tuple[str, ...]

    @property
    def tied(self) -> bool:
        return len(self.tied_skill_ids) > 1


@dataclass(frozen=True)
class GoldFreeAgreement:
    case_id: str
    failure_type: str
    gold_free_top_skill: str | None
    gold_supervised_top_skill: str | None
    agreement: bool | None
    tie_aware_agreement: bool | None
    gold_free_margin: float | None
    gold_supervised_margin: float | None
    supervised_regret: float | None
    gold_free_tied_skills: tuple[str, ...]
    gold_supervised_tied_skills: tuple[str, ...]
    missing_or_nonfinite_skill_ids: tuple[str, ...]
    hard_reason: str


@dataclass(frozen=True)
class FailureTypeAgreement:
    failure_type: str
    eligible_cases: int
    agreements: int
    agreement_rate: float | None
    tied_cases: int
    excluded_cases: int


@dataclass(frozen=True)
class AbstentionPoint:
    threshold: float
    retained_cases: int
    eligible_cases: int
    coverage: float
    agreements: int
    selective_agreement: float | None
    mean_supervised_regret: float | None


@dataclass(frozen=True)
class IdentifiabilityReport:
    total_cases: int
    eligible_cases: int
    agreements: int
    overall_agreement: float | None
    tie_aware_agreements: int
    overall_tie_aware_agreement: float | None
    by_failure_type: tuple[FailureTypeAgreement, ...]
    abstention_curve: tuple[AbstentionPoint, ...]
    hard_cases: tuple[GoldFreeAgreement, ...]


def rank_gold_free(
    scores: Sequence[CandidateScore],
    *,
    tie_tolerance: float = 1e-12,
) -> GoldFreeSelection:
    """Rank candidates using only gold-free scores."""
    return _rank_scores(scores, tie_tolerance=tie_tolerance)


def analyze_gold_free_agreement(
    cases: Iterable[CaseRankingInput],
    *,
    abstention_thresholds: Sequence[float] = (0.0, 0.01, 0.05, 0.1, 0.2),
    tie_tolerance: float = 1e-12,
) -> tuple[tuple[GoldFreeAgreement, ...], IdentifiabilityReport]:
    """Compare runtime gold-free selection with isolated shadow-gold ranking."""
    rows: list[GoldFreeAgreement] = []
    for case in cases:
        case.runtime_provenance.validate()
        _validate_candidate_sets(case)
        gold_free = rank_gold_free(
            case.gold_free_scores,
            tie_tolerance=tie_tolerance,
        )
        shadow = _rank_scores(
            case.shadow_gold_scores,
            tie_tolerance=tie_tolerance,
        )
        agreement: bool | None = None
        tie_aware: bool | None = None
        regret: float | None = None
        if gold_free.skill_id is not None and shadow.skill_id is not None:
            agreement = gold_free.skill_id == shadow.skill_id
            tie_aware = (
                set(gold_free.tied_skill_ids) == set(shadow.tied_skill_ids)
                if gold_free.tied or shadow.tied
                else agreement
            )
            shadow_map = _finite_score_map(case.shadow_gold_scores)
            chosen = shadow_map.get(gold_free.skill_id)
            if chosen is not None and shadow_map:
                regret = max(shadow_map.values()) - chosen

        missing = tuple(
            sorted(
                set(gold_free.missing_or_nonfinite_skill_ids)
                | set(shadow.missing_or_nonfinite_skill_ids)
            )
        )
        hard_reason = _hard_reason(
            agreement=agreement,
            tie_aware_agreement=tie_aware,
            missing=missing,
        )
        rows.append(
            GoldFreeAgreement(
                case_id=case.case_id,
                failure_type=case.failure_type,
                gold_free_top_skill=gold_free.skill_id,
                gold_supervised_top_skill=shadow.skill_id,
                agreement=agreement,
                tie_aware_agreement=tie_aware,
                gold_free_margin=gold_free.margin,
                gold_supervised_margin=shadow.margin,
                supervised_regret=regret,
                gold_free_tied_skills=gold_free.tied_skill_ids,
                gold_supervised_tied_skills=shadow.tied_skill_ids,
                missing_or_nonfinite_skill_ids=missing,
                hard_reason=hard_reason,
            )
        )

    ordered_rows = tuple(sorted(rows, key=lambda item: item.case_id))
    eligible = tuple(item for item in ordered_rows if item.agreement is not None)
    agreements = sum(item.agreement is True for item in eligible)
    tie_aware_agreements = sum(
        item.tie_aware_agreement is True for item in eligible
    )
    report = IdentifiabilityReport(
        total_cases=len(ordered_rows),
        eligible_cases=len(eligible),
        agreements=agreements,
        overall_agreement=_rate(agreements, len(eligible)),
        tie_aware_agreements=tie_aware_agreements,
        overall_tie_aware_agreement=_rate(
            tie_aware_agreements,
            len(eligible),
        ),
        by_failure_type=_by_failure_type(ordered_rows),
        abstention_curve=_abstention_curve(
            eligible,
            abstention_thresholds,
        ),
        hard_cases=tuple(
            item for item in ordered_rows if item.hard_reason
        ),
    )
    return ordered_rows, report


def _validate_candidate_sets(case: CaseRankingInput) -> None:
    gold_free_ids = [str(item.skill_id) for item in case.gold_free_scores]
    shadow_ids = [str(item.skill_id) for item in case.shadow_gold_scores]
    if len(gold_free_ids) != len(set(gold_free_ids)):
        raise ValueError(
            f"duplicate gold-free candidate for case {case.case_id}"
        )
    if len(shadow_ids) != len(set(shadow_ids)):
        raise ValueError(
            f"duplicate shadow-gold candidate for case {case.case_id}"
        )
    if set(gold_free_ids) != set(shadow_ids):
        raise ValueError(
            "gold-free and shadow-gold candidate sets differ for case "
            f"{case.case_id}"
        )


def case_ranking_from_mappings(
    *,
    case_id: str,
    failure_type: str,
    gold_free_scores: Mapping[str, float | None],
    shadow_gold_scores: Mapping[str, float | None],
    runtime_provenance: RuntimeSelectionProvenance | None = None,
) -> CaseRankingInput:
    """Build a deterministic input from artifact dictionaries."""
    return CaseRankingInput(
        case_id=str(case_id),
        failure_type=str(failure_type),
        gold_free_scores=tuple(
            CandidateScore(str(skill_id), gain)
            for skill_id, gain in sorted(gold_free_scores.items())
        ),
        shadow_gold_scores=tuple(
            CandidateScore(str(skill_id), gain)
            for skill_id, gain in sorted(shadow_gold_scores.items())
        ),
        runtime_provenance=(
            runtime_provenance or RuntimeSelectionProvenance()
        ),
    )


def _rank_scores(
    scores: Sequence[CandidateScore],
    *,
    tie_tolerance: float,
) -> GoldFreeSelection:
    if tie_tolerance < 0:
        raise ValueError("tie_tolerance must be >= 0")
    seen: set[str] = set()
    finite: list[tuple[str, float]] = []
    missing: list[str] = []
    for score in scores:
        skill_id = str(score.skill_id)
        if skill_id in seen:
            raise ValueError(f"duplicate skill_id: {skill_id}")
        seen.add(skill_id)
        try:
            value = float(score.gain) if score.gain is not None else math.nan
        except (TypeError, ValueError):
            value = math.nan
        if math.isfinite(value):
            finite.append((skill_id, value))
        else:
            missing.append(skill_id)
    finite.sort(key=lambda item: (-item[1], item[0]))
    if not finite:
        return GoldFreeSelection(None, (), None, 0, tuple(sorted(missing)))
    best_value = finite[0][1]
    tied = tuple(
        skill_id
        for skill_id, value in finite
        if abs(best_value - value) <= tie_tolerance
    )
    margin = None
    if len(finite) >= 2:
        margin = best_value - finite[1][1]
    return GoldFreeSelection(
        skill_id=finite[0][0],
        tied_skill_ids=tied,
        margin=margin,
        finite_candidate_count=len(finite),
        missing_or_nonfinite_skill_ids=tuple(sorted(missing)),
    )


def _finite_score_map(
    scores: Sequence[CandidateScore],
) -> dict[str, float]:
    result: dict[str, float] = {}
    for score in scores:
        try:
            value = float(score.gain) if score.gain is not None else math.nan
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            result[str(score.skill_id)] = value
    return result


def _hard_reason(
    *,
    agreement: bool | None,
    tie_aware_agreement: bool | None,
    missing: tuple[str, ...],
) -> str:
    reasons: list[str] = []
    if agreement is None:
        reasons.append("insufficient_finite_scores")
    elif not agreement:
        reasons.append("top1_disagreement")
    if tie_aware_agreement is False:
        reasons.append("tie_set_disagreement")
    if missing:
        reasons.append("missing_or_nonfinite")
    return ",".join(reasons)


def _by_failure_type(
    rows: Sequence[GoldFreeAgreement],
) -> tuple[FailureTypeAgreement, ...]:
    result: list[FailureTypeAgreement] = []
    for failure_type in sorted({item.failure_type for item in rows}):
        members = [item for item in rows if item.failure_type == failure_type]
        eligible = [item for item in members if item.agreement is not None]
        agreements = sum(item.agreement is True for item in eligible)
        result.append(
            FailureTypeAgreement(
                failure_type=failure_type,
                eligible_cases=len(eligible),
                agreements=agreements,
                agreement_rate=_rate(agreements, len(eligible)),
                tied_cases=sum(
                    len(item.gold_free_tied_skills) > 1
                    or len(item.gold_supervised_tied_skills) > 1
                    for item in members
                ),
                excluded_cases=len(members) - len(eligible),
            )
        )
    return tuple(result)


def _abstention_curve(
    eligible: Sequence[GoldFreeAgreement],
    thresholds: Sequence[float],
) -> tuple[AbstentionPoint, ...]:
    result: list[AbstentionPoint] = []
    for threshold in sorted({float(value) for value in thresholds}):
        if not math.isfinite(threshold) or threshold < 0:
            raise ValueError("abstention thresholds must be finite and >= 0")
        retained = [
            item
            for item in eligible
            if item.gold_free_margin is not None
            and item.gold_free_margin >= threshold
            and len(item.gold_free_tied_skills) <= 1
        ]
        agreements = sum(item.agreement is True for item in retained)
        regrets = [
            item.supervised_regret
            for item in retained
            if item.supervised_regret is not None
            and math.isfinite(item.supervised_regret)
        ]
        result.append(
            AbstentionPoint(
                threshold=threshold,
                retained_cases=len(retained),
                eligible_cases=len(eligible),
                coverage=(
                    len(retained) / len(eligible) if eligible else 0.0
                ),
                agreements=agreements,
                selective_agreement=_rate(agreements, len(retained)),
                mean_supervised_regret=(
                    sum(regrets) / len(regrets) if regrets else None
                ),
            )
        )
    return tuple(result)


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None
