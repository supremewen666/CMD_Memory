"""Outcome-blind gates that must pass before successor-v3 policy search.

The predecessor reached E0 with a dead predicate and interpreted an instrument
reading as a search-space reading.  These gates make that ordering impossible:
relation validity, destructive-target actionability, predicate activity, and
runtime-field shortcut audits are materialized before any headroom result is
authorized.  Thresholds are deliberately supplied by a preregistration
manifest; this module contains no scientifically meaningful default.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import math
import random
from typing import Callable, Literal, Mapping, Sequence

__all__ = [
    "ActionabilityObservation",
    "FieldShortcutMeasurement",
    "GateDecision",
    "GateThresholds",
    "PredicateActivity",
    "RelationObservation",
    "ShortcutAuditDecision",
    "ShortcutItem",
    "audit_item_field_shortcuts",
    "evaluate_actionability_gate",
    "evaluate_predicate_activity_gate",
    "evaluate_relation_gate",
]

RelationLane = Literal["calibration", "permutation", "canary"]
REGISTERED_PREDICATES = frozenset(
    {"divergent_pair_member", "superseded_item"}
)


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _bootstrap_bounds(
    rows: Sequence[object],
    *,
    stratum: Callable[[object], object],
    metrics: Callable[[Sequence[object]], Mapping[str, float]],
    iterations: int,
    seed: int,
    confidence_level: float,
) -> dict[str, tuple[float, float]]:
    """Deterministic family-block bootstrap, stratified by registered lane/label."""
    strata: dict[object, dict[str, list[object]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        family_id = getattr(row, "family_id")
        strata[stratum(row)][family_id].append(row)
    rng = random.Random(seed)
    samples: dict[str, list[float]] = defaultdict(list)
    for _ in range(iterations):
        draw: list[object] = []
        for families in strata.values():
            family_ids = sorted(families)
            for _ in family_ids:
                draw.extend(families[rng.choice(family_ids)])
        for name, value in metrics(draw).items():
            samples[name].append(float(value))
    tail = (1.0 - confidence_level) / 2.0
    result: dict[str, tuple[float, float]] = {}
    for name, values in samples.items():
        ordered = sorted(values)
        lower_index = min(len(ordered) - 1, int(tail * len(ordered)))
        upper_index = min(
            len(ordered) - 1,
            max(0, math.ceil((1.0 - tail) * len(ordered)) - 1),
        )
        result[name] = (ordered[lower_index], ordered[upper_index])
    return result


@dataclass(frozen=True)
class GateThresholds:
    """All successor-v3 thresholds, frozen before observations are read."""

    min_relation_precision: float
    min_relation_recall: float
    max_permutation_false_positive_rate: float
    min_canary_recall: float
    max_relation_abstention_rate: float
    relation_confidence_level: float
    relation_bootstrap_iterations: int
    relation_bootstrap_seed: int
    min_relation_pairs: int
    min_positive_pairs: int
    min_negative_pairs: int
    min_relation_families: int
    min_target_precision: float
    min_target_recall: float
    min_ordering_coverage: float
    min_destructive_coverage: float
    max_unknown_rate: float
    max_conflict_rate: float
    actionability_confidence_level: float
    actionability_bootstrap_iterations: int
    actionability_bootstrap_seed: int
    min_actionability_pairs: int
    min_directional_pairs: int
    min_actionability_families: int
    min_predicate_fires: int
    min_predicate_families: int
    max_null_false_fire_rate: float
    max_shortcut_alignment: float
    max_shortcut_nmi: float
    max_permutation_target_precision: float
    max_shortcut_unique_ratio: float

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if name.endswith("bootstrap_iterations"):
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ValueError(f"{name} must be an integer >= 1")
                continue
            if name.endswith("bootstrap_seed"):
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError(f"{name} must be an integer")
                continue
            if name.startswith("min_") and name.endswith(
                ("_pairs", "_families", "_fires")
            ):
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ValueError(f"{name} must be an integer >= 1")
                continue
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")

    @classmethod
    def from_f1_manifest(cls, manifest: Mapping[str, object]) -> "GateThresholds":
        """Extract the exact registered point/support thresholds from F1 v2."""
        if (
            manifest.get("schema_version")
            != "route-a-successor-v3-freeze-schema-v2"
            or manifest.get("protocol_id")
            != "route-a-successor-semantic-actionability-v3"
            or manifest.get("freeze_stage") != "F1"
        ):
            raise ValueError("gate command requires a successor-v3 F1 manifest")
        gates = manifest.get("gates")
        if not isinstance(gates, Mapping):
            raise ValueError("F1 manifest lacks gates")
        g0, g1, g2 = (gates.get(name) for name in ("g0", "g1", "g2"))
        if not all(isinstance(gate, Mapping) for gate in (g0, g1, g2)):
            raise ValueError("F1 manifest lacks g0/g1/g2")
        assert isinstance(g0, Mapping)
        assert isinstance(g1, Mapping)
        assert isinstance(g2, Mapping)
        try:
            return cls(
                min_relation_precision=g0["relation_precision_min"],
                min_relation_recall=g0["relation_recall_min"],
                max_permutation_false_positive_rate=g0["permutation_fpr_max"],
                min_canary_recall=g0["canary_recall_min"],
                max_relation_abstention_rate=g0["abstention_rate_max"],
                relation_confidence_level=g0["confidence_level"],
                relation_bootstrap_iterations=g0["bootstrap_iterations"],
                relation_bootstrap_seed=g0["bootstrap_seed"],
                min_relation_pairs=g0["min_pairs"],
                min_positive_pairs=g0["min_positive_pairs"],
                min_negative_pairs=g0["min_negative_pairs"],
                min_relation_families=g0["min_families"],
                min_target_precision=g1["target_precision_min"],
                min_target_recall=g1["target_recall_min"],
                min_ordering_coverage=g1["ordering_coverage_min"],
                min_destructive_coverage=g1["destructive_coverage_min"],
                max_unknown_rate=g1["unknown_rate_max"],
                max_conflict_rate=g1["conflict_rate_max"],
                actionability_confidence_level=g1["confidence_level"],
                actionability_bootstrap_iterations=g1["bootstrap_iterations"],
                actionability_bootstrap_seed=g1["bootstrap_seed"],
                min_actionability_pairs=g1["min_pairs"],
                min_directional_pairs=g1["min_directional_pairs"],
                min_actionability_families=g1["min_families"],
                min_predicate_fires=g2["min_firing_cases"],
                min_predicate_families=g2["min_firing_families"],
                max_null_false_fire_rate=g2["null_false_fire_max"],
                max_shortcut_alignment=g2["field_alignment_max"],
                max_shortcut_nmi=g2["nmi_alarm_max"],
                max_permutation_target_precision=g2[
                    "permutation_target_precision_max"
                ],
                max_shortcut_unique_ratio=g2[
                    "reusable_value_unique_ratio_max"
                ],
            )
        except (KeyError, TypeError) as error:
            raise ValueError("F1 gate thresholds are incomplete") from error


@dataclass(frozen=True)
class RelationObservation:
    family_id: str
    expected_positive: bool
    predicted_positive: bool | None
    lane: RelationLane


@dataclass(frozen=True)
class ActionabilityObservation:
    family_id: str
    expected_target_id: str
    predicted_target_id: str | None
    destructive_authorized: bool
    ordering_state: Literal["resolved", "unknown", "conflicting"] = "unknown"
    evidence_deployment_visible: bool = False
    evidence_trusted: bool = False


@dataclass(frozen=True)
class PredicateActivity:
    predicate: str
    fires: int
    families: int
    null_case_fires: int = 0
    null_cases: int = 0

    def __post_init__(self) -> None:
        if self.predicate not in REGISTERED_PREDICATES:
            raise ValueError(f"unregistered v3 predicate {self.predicate!r}")
        if (
            isinstance(self.fires, bool)
            or isinstance(self.families, bool)
            or not isinstance(self.fires, int)
            or not isinstance(self.families, int)
            or isinstance(self.null_case_fires, bool)
            or not isinstance(self.null_case_fires, int)
            or isinstance(self.null_cases, bool)
            or not isinstance(self.null_cases, int)
            or self.fires < 0
            or self.families < 0
            or self.null_case_fires < 0
            or self.null_cases < 0
            or self.families > self.fires
            or self.null_case_fires > self.null_cases
        ):
            raise ValueError("predicate activity counts are invalid")


@dataclass(frozen=True)
class ShortcutItem:
    """One runtime item joined to hidden role only inside the offline audit."""

    case_id: str
    item_id: str
    is_target: bool
    fields: Mapping[str, object]
    permutation_predicted_target: bool | None = None


@dataclass(frozen=True)
class GateDecision:
    passed: bool
    measurements: Mapping[str, float]
    failures: tuple[str, ...]


@dataclass(frozen=True)
class FieldShortcutMeasurement:
    field: str
    alignment: float
    normalized_mutual_information: float
    unique_ratio: float
    observations: int
    eligible: bool


@dataclass(frozen=True)
class ShortcutAuditDecision:
    passed: bool
    measurements: tuple[FieldShortcutMeasurement, ...]
    flagged_fields: tuple[str, ...]
    high_cardinality_fields: tuple[str, ...]
    permutation_target_precision: float
    permutation_evidence_complete: bool


def _relation_rate_vector(rows: Sequence[object]) -> Mapping[str, float]:
    calibration = [row for row in rows if getattr(row, "lane") == "calibration"]
    permutation = [row for row in rows if getattr(row, "lane") == "permutation"]
    canaries = [row for row in rows if getattr(row, "lane") == "canary"]
    tp = sum(
        getattr(row, "expected_positive")
        and getattr(row, "predicted_positive") is True
        for row in calibration
    )
    fp = sum(
        not getattr(row, "expected_positive")
        and getattr(row, "predicted_positive") is True
        for row in calibration
    )
    fn = sum(
        getattr(row, "expected_positive")
        and getattr(row, "predicted_positive") is not True
        for row in calibration
    )
    canary_positives = sum(getattr(row, "expected_positive") for row in canaries)
    return {
        "relation_precision": _rate(tp, tp + fp),
        "relation_recall": _rate(tp, tp + fn),
        "permutation_false_positive_rate": _rate(
            sum(getattr(row, "predicted_positive") is True for row in permutation),
            len(permutation),
        ),
        "canary_recall": _rate(
            sum(
                getattr(row, "expected_positive")
                and getattr(row, "predicted_positive") is True
                for row in canaries
            ),
            canary_positives,
        ),
        "abstention_rate": _rate(
            sum(getattr(row, "predicted_positive") is None for row in rows),
            len(rows),
        ),
    }


def _actionability_rate_vector(rows: Sequence[object]) -> Mapping[str, float]:
    authorized = [row for row in rows if getattr(row, "destructive_authorized")]
    predictions = [
        row for row in authorized if getattr(row, "predicted_target_id") is not None
    ]
    correct = sum(
        getattr(row, "predicted_target_id") == getattr(row, "expected_target_id")
        for row in predictions
    )
    directional = [
        row
        for row in rows
        if getattr(row, "ordering_state") == "resolved"
        and getattr(row, "evidence_deployment_visible")
        and getattr(row, "evidence_trusted")
    ]
    return {
        "target_precision": _rate(correct, len(predictions)),
        "target_recall": _rate(correct, len(rows)),
        "ordering_coverage": _rate(len(directional), len(rows)),
        "destructive_coverage": _rate(len(predictions), len(rows)),
        "unknown_rate": _rate(
            sum(getattr(row, "ordering_state") == "unknown" for row in rows),
            len(rows),
        ),
        "conflict_rate": _rate(
            sum(getattr(row, "ordering_state") == "conflicting" for row in rows),
            len(rows),
        ),
    }


def evaluate_relation_gate(
    observations: Sequence[RelationObservation],
    *,
    thresholds: GateThresholds,
) -> GateDecision:
    calibration = [row for row in observations if row.lane == "calibration"]
    permutation = [row for row in observations if row.lane == "permutation"]
    canaries = [row for row in observations if row.lane == "canary"]
    failures: list[str] = []

    tp = sum(
        row.expected_positive and row.predicted_positive is True
        for row in calibration
    )
    fp = sum(
        not row.expected_positive and row.predicted_positive is True
        for row in calibration
    )
    fn = sum(
        row.expected_positive and row.predicted_positive is not True
        for row in calibration
    )
    precision = _rate(tp, tp + fp)
    recall = _rate(tp, tp + fn)
    permutation_fpr = _rate(
        sum(row.predicted_positive is True for row in permutation), len(permutation)
    )
    canary_positives = sum(row.expected_positive for row in canaries)
    canary_recall = _rate(
        sum(
            row.expected_positive and row.predicted_positive is True
            for row in canaries
        ),
        canary_positives,
    )
    abstention_rate = _rate(
        sum(row.predicted_positive is None for row in observations),
        len(observations),
    )
    calibration_positives = sum(row.expected_positive for row in calibration)
    calibration_negatives = len(calibration) - calibration_positives
    calibration_families = len({row.family_id for row in calibration})
    bounds = _bootstrap_bounds(
        observations,
        stratum=lambda row: (
            getattr(row, "lane"),
            getattr(row, "expected_positive"),
        ),
        metrics=_relation_rate_vector,
        iterations=thresholds.relation_bootstrap_iterations,
        seed=thresholds.relation_bootstrap_seed,
        confidence_level=thresholds.relation_confidence_level,
    )

    if not calibration:
        failures.append("missing_calibration_lane")
    if not permutation:
        failures.append("missing_permutation_lane")
    if not canaries or not canary_positives:
        failures.append("missing_canary_lane")
    if bounds["relation_precision"][0] < thresholds.min_relation_precision:
        failures.append("relation_precision")
    if bounds["relation_recall"][0] < thresholds.min_relation_recall:
        failures.append("relation_recall")
    if (
        bounds["permutation_false_positive_rate"][1]
        > thresholds.max_permutation_false_positive_rate
    ):
        failures.append("permutation_false_positive_rate")
    if bounds["canary_recall"][0] < thresholds.min_canary_recall:
        failures.append("canary_recall")
    if bounds["abstention_rate"][1] > thresholds.max_relation_abstention_rate:
        failures.append("relation_abstention_rate")
    if len(calibration) < thresholds.min_relation_pairs:
        failures.append("relation_min_pairs")
    if calibration_positives < thresholds.min_positive_pairs:
        failures.append("relation_min_positive_pairs")
    if calibration_negatives < thresholds.min_negative_pairs:
        failures.append("relation_min_negative_pairs")
    if calibration_families < thresholds.min_relation_families:
        failures.append("relation_min_families")

    return GateDecision(
        passed=not failures,
        measurements={
            "relation_precision": precision,
            "relation_recall": recall,
            "permutation_false_positive_rate": permutation_fpr,
            "canary_recall": canary_recall,
            "abstention_rate": abstention_rate,
            "calibration_count": float(len(calibration)),
            "calibration_positive_count": float(calibration_positives),
            "calibration_negative_count": float(calibration_negatives),
            "calibration_family_count": float(calibration_families),
            "permutation_count": float(len(permutation)),
            "canary_count": float(len(canaries)),
            **{
                f"{name}_ci_lower": interval[0]
                for name, interval in bounds.items()
            },
            **{
                f"{name}_ci_upper": interval[1]
                for name, interval in bounds.items()
            },
        },
        failures=tuple(failures),
    )


def evaluate_actionability_gate(
    observations: Sequence[ActionabilityObservation],
    *,
    thresholds: GateThresholds,
) -> GateDecision:
    authorized = [row for row in observations if row.destructive_authorized]
    predictions = [row for row in authorized if row.predicted_target_id is not None]
    correct = sum(
        row.predicted_target_id == row.expected_target_id for row in predictions
    )
    precision = _rate(correct, len(predictions))
    coverage = _rate(len(predictions), len(observations))
    target_recall = _rate(correct, len(observations))
    directional = [
        row
        for row in observations
        if row.ordering_state == "resolved"
        and row.evidence_deployment_visible
        and row.evidence_trusted
    ]
    ordering_coverage = _rate(len(directional), len(observations))
    unknown_rate = _rate(
        sum(row.ordering_state == "unknown" for row in observations),
        len(observations),
    )
    conflict_rate = _rate(
        sum(row.ordering_state == "conflicting" for row in observations),
        len(observations),
    )
    bounds = _bootstrap_bounds(
        observations,
        stratum=lambda _row: "all",
        metrics=_actionability_rate_vector,
        iterations=thresholds.actionability_bootstrap_iterations,
        seed=thresholds.actionability_bootstrap_seed,
        confidence_level=thresholds.actionability_confidence_level,
    )
    unsafe_targets = [
        row
        for row in observations
        if row.predicted_target_id is not None
        and (
            not row.destructive_authorized
            or row.ordering_state != "resolved"
            or not row.evidence_deployment_visible
            or not row.evidence_trusted
        )
    ]
    unsafe_destructive = [
        row
        for row in authorized
        if (
            row.predicted_target_id is None
            or row.ordering_state != "resolved"
            or not row.evidence_deployment_visible
            or not row.evidence_trusted
        )
    ]
    failures: list[str] = []
    if not observations:
        failures.append("missing_actionability_observations")
    if bounds["target_precision"][0] < thresholds.min_target_precision:
        failures.append("target_precision")
    if bounds["destructive_coverage"][0] < thresholds.min_destructive_coverage:
        failures.append("destructive_coverage")
    if bounds["target_recall"][0] < thresholds.min_target_recall:
        failures.append("target_recall")
    if bounds["ordering_coverage"][0] < thresholds.min_ordering_coverage:
        failures.append("ordering_coverage")
    if bounds["unknown_rate"][1] > thresholds.max_unknown_rate:
        failures.append("unknown_rate")
    if bounds["conflict_rate"][1] > thresholds.max_conflict_rate:
        failures.append("conflict_rate")
    if len(observations) < thresholds.min_actionability_pairs:
        failures.append("actionability_min_pairs")
    if len(directional) < thresholds.min_directional_pairs:
        failures.append("actionability_min_directional_pairs")
    if len({row.family_id for row in observations}) < thresholds.min_actionability_families:
        failures.append("actionability_min_families")
    if unsafe_targets:
        failures.append("unsafe_target_emission")
    if unsafe_destructive:
        failures.append("unsafe_destructive_authorization")
    return GateDecision(
        passed=not failures,
        measurements={
            "target_precision": precision,
            "target_recall": target_recall,
            "destructive_coverage": coverage,
            "ordering_coverage": ordering_coverage,
            "unknown_rate": unknown_rate,
            "conflict_rate": conflict_rate,
            "unsafe_target_emission_rate": _rate(len(unsafe_targets), len(observations)),
            "unknown_conflicting_destructive_rate": _rate(
                len(unsafe_destructive), len(observations)
            ),
            "observation_count": float(len(observations)),
            "destructive_count": float(len(predictions)),
            **{
                f"{name}_ci_lower": interval[0]
                for name, interval in bounds.items()
            },
            **{
                f"{name}_ci_upper": interval[1]
                for name, interval in bounds.items()
            },
        },
        failures=tuple(failures),
    )


def evaluate_predicate_activity_gate(
    activities: Sequence[PredicateActivity],
    *,
    thresholds: GateThresholds,
) -> GateDecision:
    failures: list[str] = []
    names = [row.predicate for row in activities]
    missing = REGISTERED_PREDICATES - set(names)
    duplicates = {name for name in names if names.count(name) > 1}
    for name in sorted(missing):
        failures.append(f"{name}:missing")
    for name in sorted(duplicates):
        failures.append(f"{name}:duplicate")
    measurements: dict[str, float] = {}
    for row in activities:
        measurements[f"{row.predicate}:fires"] = float(row.fires)
        measurements[f"{row.predicate}:families"] = float(row.families)
        measurements[f"{row.predicate}:null_case_fires"] = float(
            row.null_case_fires
        )
        null_false_fire_rate = _rate(row.null_case_fires, row.null_cases)
        measurements[f"{row.predicate}:null_false_fire_rate"] = (
            null_false_fire_rate
        )
        if row.fires < thresholds.min_predicate_fires:
            failures.append(f"{row.predicate}:fires")
        if row.families < thresholds.min_predicate_families:
            failures.append(f"{row.predicate}:families")
        if null_false_fire_rate > thresholds.max_null_false_fire_rate:
            failures.append(f"{row.predicate}:null_false_fire_rate")
    return GateDecision(not failures, measurements, tuple(failures))


def _stable_field_value(value: object) -> str:
    """A deterministic audit representation; never executed or shown to policy."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return f"<UNSERIALIZABLE:{type(value).__name__}>"


def _normalized_mutual_information(values: Sequence[tuple[str, bool]]) -> float:
    joint = Counter(values)
    value_counts = Counter(value for value, _ in values)
    role_counts = Counter(role for _, role in values)
    total = len(values)
    if not total:
        return 0.0
    mutual_information = 0.0
    for (value, role), count in joint.items():
        probability = count / total
        mutual_information += probability * math.log(
            probability
            / ((value_counts[value] / total) * (role_counts[role] / total))
        )
    value_entropy = -sum(
        (count / total) * math.log(count / total)
        for count in value_counts.values()
    )
    role_entropy = -sum(
        (count / total) * math.log(count / total)
        for count in role_counts.values()
    )
    denominator = value_entropy + role_entropy
    return 0.0 if denominator == 0.0 else 2.0 * mutual_information / denominator


def audit_item_field_shortcuts(
    rows: Sequence[ShortcutItem],
    *,
    thresholds: GateThresholds,
) -> ShortcutAuditDecision:
    """Find runtime fields aligned with hidden target identity.

    A value is scored by the majority hidden role among all items carrying it.
    High-cardinality fields cannot establish reusable alignment from this audit,
    but neither are they silently safe: they remain unresolved and block the
    gate until a registered field-permutation audit supplies independent
    evidence.  Text style is handled by the relation permutation lane instead
    of being passed as an ordering field.
    """
    field_values: dict[str, list[tuple[str, bool]]] = defaultdict(list)
    exposed_fields = {
        str(field_name) for row in rows for field_name in row.fields
    }
    for row in rows:
        for field_name in exposed_fields:
            value = (
                row.fields[field_name]
                if field_name in row.fields
                else f"<MISSING:{field_name}>"
            )
            field_values[field_name].append(
                (_stable_field_value(value), bool(row.is_target))
            )

    measurements: list[FieldShortcutMeasurement] = []
    flagged: list[str] = []
    high_cardinality: list[str] = []
    for field_name in sorted(field_values):
        values = field_values[field_name]
        counts: dict[str, Counter[bool]] = defaultdict(Counter)
        for value, role in values:
            counts[value][role] += 1
        unique_ratio = _rate(len(counts), len(values))
        alignment = _rate(
            sum(max(role_counts.values()) for role_counts in counts.values()),
            len(values),
        )
        nmi = _normalized_mutual_information(values)
        eligible = unique_ratio <= thresholds.max_shortcut_unique_ratio
        measurements.append(
            FieldShortcutMeasurement(
                field=field_name,
                alignment=alignment,
                normalized_mutual_information=nmi,
                unique_ratio=unique_ratio,
                observations=len(values),
                eligible=eligible,
            )
        )
        if not eligible:
            high_cardinality.append(field_name)
        elif (
            alignment > thresholds.max_shortcut_alignment
            or nmi > thresholds.max_shortcut_nmi
        ):
            flagged.append(field_name)

    permutation_evidence_complete = bool(rows) and all(
        row.permutation_predicted_target is not None for row in rows
    )
    permutation_predictions = [
        row for row in rows if row.permutation_predicted_target is True
    ]
    permutation_target_precision = _rate(
        sum(row.is_target for row in permutation_predictions),
        len(permutation_predictions),
    )

    return ShortcutAuditDecision(
        passed=(
            not flagged
            and not high_cardinality
            and permutation_evidence_complete
            and permutation_target_precision
            <= thresholds.max_permutation_target_precision
        ),
        measurements=tuple(measurements),
        flagged_fields=tuple(flagged),
        high_cardinality_fields=tuple(high_cardinality),
        permutation_target_precision=permutation_target_precision,
        permutation_evidence_complete=permutation_evidence_complete,
    )
