#!/usr/bin/env python3
"""Zero-call deployment-feedback identifiability audit for GHOST Ecology V2.

The selected-skill feedback passed to GHOST is derived only from typed executor
and deployment-guard telemetry.  Previously materialized recovery gain is read
only by the audit side to test whether that feedback channel can identify useful
repairs; it is never returned by :func:`deployment_feedback`.

A positive correlation alone would not settle the question.  On a frozen
materialized stream the telemetry channels are emitted by the same constructor
that produced the candidates, so a channel that merely echoed that construction
would look identifiable for a circular reason.  The audit therefore also runs
two de-shortcutting controls (task.md 2.4 / 3.1):

* ``telemetry_permutation`` — a derangement repoints each candidate's telemetry
  to a different candidate while its audit reference stays put;
* ``telemetry_placebo`` — every candidate reports identical channels.

Both must collapse below the identifiability thresholds.  A control that
survives means the signal did not depend on telemetry describing *this*
candidate, and the audit reports
``BLOCKED_TELEMETRY_SHORTCUT_SUSPECTED`` rather than passing the true arm.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path
import random
from statistics import fmean
from typing import Mapping, Sequence

from cmd_audit.repair.ghost_ecology import (
    content_sha256,
    DiscoveryPressure,
    GhostEcology,
    NicheObservation,
    NicheObserver,
    NicheSnapshot,
    EcologyLedger,
    derive_discovery_pressure,
)
from cmd_audit.repair.niche_archive import SemanticClusterVocabulary
from cmd_audit.counterfactual.actions import PipelineAction
from experiments.v4_prequential_runner import V4CandidateOutcome, load_cases


FEEDBACK_SCHEMA_VERSION = "cmd-ghost-skill-conditioned-feedback-v2"
FEEDBACK_V2_SCHEMA_VERSION = "cmd-ghost-skill-conditioned-feedback-v2.2-typed-wired-coverage-gated"
REPORT_SCHEMA_VERSION = "cmd-ghost-ecology-identifiability-v2"
REPORT_V2_SCHEMA_VERSION = "cmd-ghost-ecology-identifiability-v2.2-typed-wired-coverage-gated"
DECOUPLING_ARMS: tuple[str, ...] = ("telemetry_permutation", "telemetry_placebo")
REGISTERED_PROBES: Mapping[str, str] = {
    "verify": "guard_pass_and_no_mutation",
    "abstain": "guard_pass_and_no_immediate_mutation",
    "annotate_conflict": "annotation_commit_observed",
    "replace": "target_mutation_commit_observed",
    "demote": "target_mutation_commit_observed",
    "suppress": "target_mutation_commit_observed",
}

FORBIDDEN_RUNTIME_FIELDS = frozenset({
    "gold_evidence", "gold_answer", "gold_recovery_gain", "recovery_gain",
    "perturbation_label", "project_memory", "post_outcome_semantic_cluster",
})
ZERO_CALL_SUBSTRATE_CONFIG = {
    "store_timestamps_are_observed": False,
    "metadata_decoupling": False,
}


def validate_zero_call_substrate(*, action: PipelineAction | str | None = None,
                                 metadata_decoupling: bool = False) -> dict[str, object]:
    """Validate the constructed substrate contract used by zero-call arms."""
    if action is not None and PipelineAction(action) is PipelineAction.ITEM_STALE:
        raise ValueError("ITEM_STALE is forbidden on the constructed zero-call substrate")
    if metadata_decoupling:
        raise ValueError("metadata-decoupling is a falsification arm, not substrate data")
    return dict(ZERO_CALL_SUBSTRATE_CONFIG)


def assert_zero_call_fields(value: object) -> None:
    """Reject materialised labels from a zero-call runtime record."""
    names = set(vars(value)) if hasattr(value, "__dict__") else set()
    forbidden = names & FORBIDDEN_RUNTIME_FIELDS
    if forbidden:
        raise ValueError(f"forbidden zero-call fields: {sorted(forbidden)}")


def freeze_semantic_cluster_vocabulary(dev_prefix: Sequence[str]) -> SemanticClusterVocabulary:
    """Create the sole runtime vocabulary from the development prefix."""
    return SemanticClusterVocabulary(dev_prefix).freeze()


@dataclass(frozen=True)
class SemanticClusterObservation:
    token: str
    source: str
    missing: bool


def _semantic_cluster_observation(case: object) -> SemanticClusterObservation:
    """Read semantic cluster once, with explicit legacy compatibility."""
    nested_present = False
    nested_value: object = None
    context = getattr(case, "context", None)
    if isinstance(context, Mapping):
        nested_present = "semantic_cluster" in context
        nested_value = context.get("semantic_cluster")
    elif context is not None:
        nested_present = hasattr(context, "semantic_cluster")
        nested_value = getattr(context, "semantic_cluster", None)
    top_present = hasattr(case, "semantic_cluster")
    top_value = getattr(case, "semantic_cluster", None)
    if nested_present and top_present and nested_value != top_value:
        raise ValueError("nested and legacy semantic_cluster disagree")
    value = nested_value if nested_present else top_value if top_present else "unknown"
    if not isinstance(value, str) or not value.strip():
        if nested_present or top_present:
            raise ValueError("semantic_cluster must be a non-empty string")
        return SemanticClusterObservation("unknown", "missing", True)
    source = "context" if nested_present else "legacy_top_level" if top_present else "missing"
    return SemanticClusterObservation(value.strip(), source, source == "missing")


def read_semantic_cluster(case: object) -> str:
    """Public single adapter used by vocabulary freeze and runtime validation."""
    return _semantic_cluster_observation(case).token


@dataclass(frozen=True)
class ZeroCallEcologyWindow:
    snapshot: NicheSnapshot
    pressure: DiscoveryPressure | None
    snapshot_event_id: str
    pressure_event_id: str | None


def record_zero_call_ecology_window(
    ecology: GhostEcology,
    *,
    pattern_revision_id: str,
    observations: Sequence[NicheObservation],
    window_start: int,
    window_end: int,
    event_index: int,
    previous_snapshot: NicheSnapshot | None = None,
    previous_state: str = "latent",
    unmatched_responsibilities: Sequence[float] = (),
    abstentions: Sequence[bool] = (),
    prediction_residuals: Sequence[float] = (),
) -> ZeroCallEcologyWindow:
    """Record the existing niche metrics in the append-only ecology ledger.

    This is wiring only: pressure is a proposal trigger and never mutates
    patterns, skills, or the router.  The caller controls event indices so a
    single ledger remains strictly hash chained.
    """
    for row in observations:
        assert_zero_call_fields(row)
    snapshot = NicheObserver().snapshot(
        pattern_revision_id=pattern_revision_id, observations=observations,
        window_start=window_start, window_end=window_end, previous_state=previous_state,
    )
    snapshot_id = ecology.record_niche_snapshot(snapshot, event_index=event_index)
    if previous_snapshot is not None:
        ecology.record_niche_transition(previous_snapshot, snapshot, event_index=event_index + 1)
    pressure = derive_discovery_pressure(
        niche_id=snapshot.niche_id, window_start=window_start, window_end=window_end,
        unmatched_responsibilities=unmatched_responsibilities,
        abstentions=abstentions, prediction_residuals=prediction_residuals,
    )
    pressure_id = None
    if pressure is not None:
        pressure_id = ecology.record_discovery_pressure(
            pressure, event_index=event_index + (2 if previous_snapshot is not None else 1)
        )
    return ZeroCallEcologyWindow(snapshot, pressure, snapshot_id, pressure_id)


def deployment_feedback(effect: str, outcome: object) -> dict[str, object]:
    """Return the registered immediate probe without reading shadow/gold fields."""
    if effect not in REGISTERED_PROBES:
        raise ValueError(f"unregistered repair effect: {effect}")
    valid = bool(getattr(outcome, "valid"))
    rolled_back = bool(getattr(outcome, "rolled_back"))
    changed = int(getattr(outcome, "changed_item_count"))
    locality = float(getattr(outcome, "locality_cost"))
    if changed < 0 or locality < 0.0 or not math.isfinite(locality):
        raise ValueError("deployment telemetry must be finite and non-negative")
    if effect in {"verify", "abstain"}:
        success = float(valid and not rolled_back and changed == 0)
    else:
        success = float(valid and not rolled_back and changed > 0)
    values: dict[str, object] = {
        "schema_version": FEEDBACK_SCHEMA_VERSION,
        "probe_id": REGISTERED_PROBES[effect],
        "effect": effect,
        "success": success,
        "locality_cost": locality,
        "execution_cost": min(1.0, 0.05 * changed),
        "valid": valid,
        "rolled_back": rolled_back,
        "gold_derived": False,
        "provenance": "typed-executor+deployment-guard-v2",
    }
    return {**values, "feedback_sha256": content_sha256(values)}


def deployment_feedback_v2(
    effect: str,
    outcome: object,
    *,
    actionability_mode: str | None = None,
    expected_actionability_mode: str | None = None,
) -> dict[str, object]:
    """Typed immediate probe with explicit unknown/fail-closed semantics.

    This is versioned separately from :func:`deployment_feedback`; v1 remains
    the historical changed-count proxy.  No shadow/recovery attribute is read.
    """
    if effect not in REGISTERED_PROBES:
        raise ValueError(f"unregistered repair effect: {effect}")
    valid = getattr(outcome, "valid", None)
    rolled_back = getattr(outcome, "rolled_back", None)
    changed = getattr(outcome, "changed_item_count", None)
    if valid is None or rolled_back is None:
        success: bool | None = None
        status = "unknown"
    elif valid is False or rolled_back is True:
        success = False
        status = "observed_failure"
    else:
        # The development enrichment records the mode proved by the frozen
        # graph as ``actionability_mode_observed``.  Prefer that typed field
        # over the legacy/shadow ``actionability_mode`` when no explicit
        # caller override is supplied; otherwise E2 would silently turn every
        # enriched destructive probe into UNKNOWN.
        mode = actionability_mode
        if mode is None:
            mode = getattr(outcome, "actionability_mode_observed", None)
        if mode is None:
            mode = getattr(outcome, "actionability_mode", None)
        expected = expected_actionability_mode
        mode_match: bool | None = (True if expected is None else None if mode is None else mode == expected)
        if effect in {"replace", "demote", "suppress"}:
            binding = getattr(outcome, "target_binding_observed", None)
            match = getattr(outcome, "target_match_observed", None)
            mutation: bool | None = None if changed is None else changed > 0
            evidence = (mutation, mode_match, binding, match)
            success = True if all(value is True for value in evidence) else False if any(value is False for value in evidence) else None
            status = "observed" if success is True else ("observed_failure" if success is False else "unknown")
        elif effect == "annotate_conflict":
            consumed = getattr(outcome, "annotation_consumed", None)
            downstream = getattr(outcome, "downstream_confirmation", None)
            success = True if consumed is True or downstream is True else (False if consumed is False and downstream is False else None)
            status = "observed" if success is True else ("observed_failure" if success is False else "unknown")
        else:
            delayed = getattr(outcome, "delayed_confirmation", None)
            no_regression = getattr(outcome, "no_regression_observed", None)
            success = True if delayed is True or no_regression is True else (False if delayed is False and no_regression is False else None)
            status = "observed" if success is True else ("observed_failure" if success is False else "unknown")
    locality = getattr(outcome, "locality_cost", None)
    values = {
        "schema_version": FEEDBACK_V2_SCHEMA_VERSION,
        "probe_id": REGISTERED_PROBES[effect], "effect": effect,
        "success": success, "status": status,
        "observed_fields": tuple(sorted(name for name in (
            "valid", "rolled_back", "changed_item_count", "actionability_mode_observed",
            "target_binding_observed", "target_match_observed", "annotation_consumed",
            "downstream_confirmation", "delayed_confirmation", "no_regression_observed",
        ) if getattr(outcome, name, None) is not None)),
        "coverage": success is not None,
        "valid": valid, "rolled_back": rolled_back,
        "locality_cost": locality, "gold_derived": False,
    }
    return {**values, "feedback_sha256": content_sha256(values)}


def deployment_reward_v2(
    effect: str, outcome: object, *, actionability_mode: str | None = None,
    expected_actionability_mode: str | None = None,
) -> float | None:
    """Return a typed-v2 reward, or ``None`` when the probe is unknown."""
    feedback = deployment_feedback_v2(
        effect, outcome, actionability_mode=actionability_mode,
        expected_actionability_mode=expected_actionability_mode,
    )
    success = feedback["success"]
    if success is None:
        return None
    locality = feedback["locality_cost"]
    if isinstance(locality, bool) or not isinstance(locality, (int, float)) or not math.isfinite(float(locality)) or float(locality) < 0:
        raise ValueError("typed-v2 locality_cost must be finite and non-negative")
    changed = getattr(outcome, "changed_item_count", None)
    if isinstance(changed, bool) or not isinstance(changed, int) or changed < 0:
        raise ValueError("typed-v2 changed_item_count must be a non-negative integer")
    return max(-1.0, min(1.0, float(success) - float(locality) - min(1.0, 0.05 * changed)))


def pairwise_comparable_coverage(rows: Sequence[tuple[float | None, float | None]]) -> dict[str, object]:
    """Coverage denominator excludes pairs tied on either non-reference proxy."""
    total = comparable = 0
    for index, (proxy, reference) in enumerate(rows):
        for prior_proxy, prior_reference in rows[:index]:
            if proxy is None or prior_proxy is None or reference is None or prior_reference is None:
                continue
            if reference == prior_reference:
                continue
            total += 1
            if proxy != prior_proxy:
                comparable += 1
    return {"comparable": comparable, "total_nonreference_tied_candidate_pairs": total,
            "value": 0.0 if total == 0 else comparable / total}


V2_COVERAGE_THRESHOLD = 0.50
V2_CANDIDATE_COVERAGE_THRESHOLD = 0.50
V2_FAMILY_COVERAGE_THRESHOLD = 0.50


def v2_protocol_manifest(
    *,
    coverage_threshold: float = V2_COVERAGE_THRESHOLD,
    reference_is_fresh_replay: bool = False,
) -> dict[str, object]:
    if coverage_threshold != V2_COVERAGE_THRESHOLD:
        raise ValueError("v2 coverage threshold is preregistered and immutable")
    if not isinstance(reference_is_fresh_replay, bool):
        raise ValueError("fresh-replay protocol flag must be boolean")
    payload = {"schema_version": REPORT_V2_SCHEMA_VERSION,
               "thresholds": {"min_family_correlation": 0.20, "min_bootstrap_lower": 0.10,
                               "min_pairwise_concordance": 0.55,
                               "min_pairwise_comparable_coverage": coverage_threshold},
               "reference": "materialized_recovery_gain_shadow_artifact",
               "reference_is_fresh_replay": reference_is_fresh_replay}
    return {**payload, "manifest_sha256": content_sha256(payload)}


def deployment_reward(effect: str, outcome: object) -> float:
    feedback = deployment_feedback(effect, outcome)
    if not feedback["valid"] or feedback["rolled_back"]:
        return -1.0
    return max(
        -1.0,
        min(
            1.0,
            float(feedback["success"])
            - float(feedback["locality_cost"])
            - float(feedback["execution_cost"]),
        ),
    )


def _shadow_utility(outcome: V4CandidateOutcome) -> float:
    if not outcome.valid or outcome.rolled_back:
        return 0.0
    return (
        outcome.recovery_gain
        - outcome.locality_cost
        - 0.05 * outcome.changed_item_count
    )


def _pearson(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        return 0.0
    left_mean = fmean(left)
    right_mean = fmean(right)
    left_ss = sum((value - left_mean) ** 2 for value in left)
    right_ss = sum((value - right_mean) ** 2 for value in right)
    if left_ss <= 0.0 or right_ss <= 0.0:
        return 0.0
    cross = sum(
        (x - left_mean) * (y - right_mean)
        for x, y in zip(left, right, strict=True)
    )
    return cross / math.sqrt(left_ss * right_ss)


def _lower_one_sided(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return float(ordered[max(0, math.ceil(0.05 * len(ordered)) - 1)])


@dataclass(frozen=True)
class _Observation:
    """One candidate, with its telemetry and its audit reference kept apart.

    Splitting the two lets a control arm repoint ``telemetry`` while leaving
    ``reference`` anchored to the candidate it actually belongs to.  Permuting
    the whole outcome record would move both sides together and preserve the
    correlation, which is the mistake this separation prevents.
    """

    case_id: str
    family_id: str
    effect: str
    telemetry: object
    reference: V4CandidateOutcome


@dataclass(frozen=True)
class _TypedObservation:
    case_id: str
    family_id: str
    effect: str
    telemetry: object
    reference: V4CandidateOutcome
    expected_actionability_mode: str | None = None


def _typed_observations(cases: Sequence[object]) -> tuple[_TypedObservation, ...]:
    rows: list[_TypedObservation] = []
    for case in cases:
        outcomes = {row.intent_id: row for row in case.candidate_outcomes}
        graph = getattr(case, "graph", None)
        for intent in case.intents:
            outcome = outcomes[intent.intent_id]
            mode = None
            if graph is not None:
                edge = next((edge for edge in getattr(graph, "edges", ()) if edge.edge_id == intent.relation_edge_id), None)
                if edge is not None:
                    mode = edge.actionability.mode.value
            rows.append(_TypedObservation(case.case_id, case.family_id, intent.effect, outcome, outcome, mode))
    return tuple(rows)


def _typed_statistics(observations: Sequence[_TypedObservation], *, bootstrap_samples: int = 1000, bootstrap_seed: int = 0) -> dict[str, object]:
    proxy: list[float] = []
    reference: list[float] = []
    observed_by_case: dict[str, list[tuple[float | None, float]]] = defaultdict(list)
    observed_families: set[str] = set()
    all_families = {row.family_id for row in observations}
    by_family: dict[str, list[tuple[float, float]]] = defaultdict(list)
    unknown_by_effect: Counter[str] = Counter()
    observed_by_effect: Counter[str] = Counter()
    for row in observations:
        score = deployment_reward_v2(row.effect, row.telemetry, expected_actionability_mode=row.expected_actionability_mode)
        ref = _shadow_utility(row.reference)
        observed_by_case[row.case_id].append((score, ref))
        if score is None:
            unknown_by_effect[row.effect] += 1
            continue
        observed_by_effect[row.effect] += 1
        proxy.append(score); reference.append(ref); observed_families.add(row.family_id)
        by_family[row.family_id].append((score, ref))
    family_pairs = {family: (fmean([x for x, _ in vals]), fmean([y for _, y in vals])) for family, vals in by_family.items()}
    family_corr = _pearson([v[0] for v in family_pairs.values()], [v[1] for v in family_pairs.values()]) if len(family_pairs) >= 2 else None
    family_lower = None
    if len(family_pairs) >= 2:
        rng = random.Random(bootstrap_seed)
        keys = tuple(sorted(family_pairs))
        draws = []
        for _ in range(max(100, bootstrap_samples)):
            chosen = [rng.choice(keys) for _ in keys]
            draws.append(_pearson([family_pairs[key][0] for key in chosen], [family_pairs[key][1] for key in chosen]))
        draws.sort()
        family_lower = draws[max(0, math.ceil(.05 * len(draws)) - 1)]
    candidate_corr = _pearson(proxy, reference) if len(proxy) >= 2 else None
    denominator = numerator = concordant = 0
    for pairs in observed_by_case.values():
        for index, (left_proxy, left_ref) in enumerate(pairs):
            for right_proxy, right_ref in pairs[:index]:
                if left_ref == right_ref:
                    continue
                denominator += 1
                if left_proxy is None or right_proxy is None or left_proxy == right_proxy:
                    continue
                numerator += 1
                concordant += int((left_proxy - right_proxy) * (left_ref - right_ref) > 0)
    total = len(observations)
    observed_count = len(proxy)
    return {
        "candidate_observation_count": total,
        "observed_candidate_count": observed_count,
        "unknown_candidate_count": total - observed_count,
        "candidate_observed_coverage": observed_count / total if total else 0.0,
        "observed_family_count": len(observed_families),
        "family_count": len(all_families),
        "family_observed_coverage": len(observed_families) / len(all_families) if all_families else 0.0,
        "candidate_level_pearson": candidate_corr,
        "family_macro_pearson": family_corr,
        "family_bootstrap_lower_95_one_sided": family_lower,
        "pairwise_comparable_coverage": {"comparable": numerator, "total_nonreference_tied_candidate_pairs": denominator, "value": numerator / denominator if denominator else None},
        "within_case_pairwise_concordance": concordant / numerator if numerator else None,
        "comparable_pair_count": numerator,
        "effect_coverage": {effect: {"observed": observed_by_effect[effect], "unknown": unknown_by_effect[effect], "coverage": observed_by_effect[effect] / (observed_by_effect[effect] + unknown_by_effect[effect])} for effect in sorted(set(observed_by_effect) | set(unknown_by_effect))},
    }


@dataclass(frozen=True)
class _PlaceboTelemetry:
    """Constant channels: every candidate reports the same execution record."""

    valid: bool = True
    rolled_back: bool = False
    changed_item_count: int = 1
    locality_cost: float = 0.0


def _collect_observations(cases: Sequence[object]) -> tuple[_Observation, ...]:
    rows: list[_Observation] = []
    for case in cases:
        outcomes = {row.intent_id: row for row in case.candidate_outcomes}
        for intent in case.intents:
            outcome = outcomes[intent.intent_id]
            rows.append(
                _Observation(
                    case_id=case.case_id,
                    family_id=case.family_id,
                    effect=intent.effect,
                    telemetry=outcome,
                    reference=outcome,
                )
            )
    return tuple(rows)


def _derange(count: int, *, seed: int) -> tuple[int, ...]:
    """A seeded permutation with no fixed point.

    Fixed points would leave some candidates correctly paired, weakening the
    control exactly where it needs to be strongest, so any that survive the
    shuffle are swapped out.
    """
    if count < 2:
        raise ValueError("telemetry decoupling requires at least two candidates")
    indices = list(range(count))
    random.Random(seed).shuffle(indices)
    for position in range(count):
        if indices[position] == position:
            partner = (position + 1) % count
            indices[position], indices[partner] = indices[partner], indices[position]
    if any(index == position for position, index in enumerate(indices)):
        raise ValueError("failed to construct a telemetry derangement")
    return tuple(indices)


def _decouple(
    observations: Sequence[_Observation], *, arm: str, seed: int
) -> tuple[_Observation, ...]:
    """Build a control arm whose telemetry no longer describes its candidate."""
    if arm == "telemetry_permutation":
        order = _derange(len(observations), seed=seed)
        return tuple(
            replace(row, telemetry=observations[order[position]].telemetry)
            for position, row in enumerate(observations)
        )
    if arm == "telemetry_placebo":
        placebo = _PlaceboTelemetry()
        return tuple(replace(row, telemetry=placebo) for row in observations)
    raise ValueError(f"unknown decoupling arm: {arm}")


def _statistics(
    observations: Sequence[_Observation],
    *,
    bootstrap_samples: int,
    bootstrap_seed: int,
) -> dict[str, object]:
    """Identifiability statistics for one arm.

    Reward comes from ``telemetry``; the audit reference comes from
    ``reference``.  A control arm therefore runs through exactly this code with
    only the pairing changed, so a collapse cannot be an artifact of measuring
    the arms differently.
    """
    direct: list[float] = []
    shadow: list[float] = []
    by_family: dict[str, list[tuple[float, float]]] = defaultdict(list)
    by_case: dict[str, list[tuple[float, float]]] = defaultdict(list)

    for row in observations:
        left = deployment_reward(row.effect, row.telemetry)
        right = _shadow_utility(row.reference)
        direct.append(left)
        shadow.append(right)
        by_family[row.family_id].append((left, right))
        by_case[row.case_id].append((left, right))

    comparable = concordant = 0
    for pairs in by_case.values():
        for index, current in enumerate(pairs):
            for previous in pairs[:index]:
                direct_delta = current[0] - previous[0]
                shadow_delta = current[1] - previous[1]
                if direct_delta == 0.0 or shadow_delta == 0.0:
                    continue
                comparable += 1
                concordant += int(direct_delta * shadow_delta > 0.0)

    family_pairs = {
        family: (
            fmean(row[0] for row in values),
            fmean(row[1] for row in values),
        )
        for family, values in by_family.items()
    }
    family_keys = tuple(sorted(family_pairs))
    family_correlation = _pearson(
        [family_pairs[key][0] for key in family_keys],
        [family_pairs[key][1] for key in family_keys],
    )
    rng = random.Random(bootstrap_seed)
    draws = []
    for _ in range(bootstrap_samples):
        chosen = [family_keys[rng.randrange(len(family_keys))] for _ in family_keys]
        draws.append(
            _pearson(
                [family_pairs[key][0] for key in chosen],
                [family_pairs[key][1] for key in chosen],
            )
        )
    return {
        "candidate_observation_count": len(direct),
        "family_count": len(family_pairs),
        "candidate_level_pearson": _pearson(direct, shadow),
        "family_macro_pearson": family_correlation,
        "family_bootstrap_lower_95_one_sided": _lower_one_sided(draws),
        "within_case_pairwise_concordance": (
            0.0 if not comparable else concordant / comparable
        ),
        "comparable_pair_count": comparable,
    }


def audit_identifiability(
    *,
    cases_path: Path,
    output: Path,
    bootstrap_samples: int = 10_000,
    bootstrap_seed: int = 24,
    decoupling_seed: int = 91,
    min_family_correlation: float = 0.2,
    min_bootstrap_lower: float = 0.1,
    min_pairwise_concordance: float = 0.55,
) -> dict[str, object]:
    if bootstrap_samples < 10_000:
        raise ValueError("identifiability audit requires at least 10000 bootstrap draws")
    if output.exists():
        raise ValueError(f"refusing to overwrite identifiability report: {output}")
    cases = load_cases(cases_path)
    # Freeze descriptor vocabulary before the first scored case.  Every later
    # case is validated against this dev-prefix-only artifact; no outcome or
    # future case can expand it.
    dev_size = max(1, len(cases) // 5)
    semantic_observations = tuple(_semantic_cluster_observation(case) for case in cases)
    dev_tokens = tuple(row.token for row in semantic_observations[:dev_size])
    vocabulary = freeze_semantic_cluster_vocabulary(dev_tokens)
    for row in semantic_observations:
        vocabulary.validate(row.token)
    ecology = GhostEcology(
        EcologyLedger(output.with_suffix(".ecology.jsonl")),
        discovery_authorized=True,
    )
    previous_snapshot: NicheSnapshot | None = None
    ecology_windows = 0
    observations_rows = _collect_observations(cases)
    probe_counts: Counter[str] = Counter()
    probe_successes: Counter[str] = Counter()
    valid_count = rollback_count = zero_change_count = one_change_count = 0

    for row in observations_rows:
        feedback = deployment_feedback(row.effect, row.telemetry)
        probe = str(feedback["probe_id"])
        probe_counts[probe] += 1
        probe_successes[probe] += int(float(feedback["success"]) > 0.0)
        outcome = row.reference
        valid_count += int(outcome.valid)
        rollback_count += int(outcome.rolled_back)
        zero_change_count += int(outcome.changed_item_count == 0)
        one_change_count += int(outcome.changed_item_count == 1)

    # The zero-call event loop records one ecology window per case.  It uses
    # only typed execution channels and derived residual/abstention statistics;
    # the shadow reference remains confined to the audit statistics above.
    for case_index, case in enumerate(cases):
        case_rows = tuple(row for row in observations_rows if row.case_id == case.case_id)
        niche_observations = tuple(
            NicheObservation(
                failure_id=f"case:{row.case_id}:{row.effect}",
                pattern_revision_id="zero-call-pattern-v2",
                skill_revision_id=f"probe:{row.effect}",
                responsibility=1.0 / max(1, len(case_rows)),
                selected=True,
                success=float(deployment_feedback(row.effect, row.telemetry)["success"]),
                resolved=bool(getattr(row.telemetry, "valid")) and not bool(getattr(row.telemetry, "rolled_back")),
            )
            for row in case_rows
        )
        current = record_zero_call_ecology_window(
            ecology,
            pattern_revision_id="zero-call-pattern-v2",
            observations=niche_observations,
            window_start=case_index,
            window_end=case_index,
            event_index=case_index * 3,
            previous_snapshot=previous_snapshot,
            previous_state="latent" if previous_snapshot is None else previous_snapshot.state,
            unmatched_responsibilities=tuple(
                1.0 - float(obs.success) for obs in niche_observations
            ),
            abstentions=tuple(not obs.selected for obs in niche_observations),
            prediction_residuals=tuple(1.0 - float(obs.success) for obs in niche_observations),
        )
        previous_snapshot = current.snapshot
        ecology_windows += 1

    true_arm = _statistics(
        observations_rows,
        bootstrap_samples=bootstrap_samples,
        bootstrap_seed=bootstrap_seed,
    )
    family_correlation = float(true_arm["family_macro_pearson"])
    lower = float(true_arm["family_bootstrap_lower_95_one_sided"])
    concordance = float(true_arm["within_case_pairwise_concordance"])
    passed = (
        family_correlation >= min_family_correlation
        and lower >= min_bootstrap_lower
        and concordance >= min_pairwise_concordance
    )

    # task.md 2.4 / 3.1: the de-shortcutting controls.  If the telemetry channel
    # were merely echoing the constructor's own output, breaking the pairing
    # between a candidate and its telemetry would leave identifiability intact.
    # Each control therefore has to collapse; a surviving control is the kill
    # condition, and it voids the true arm rather than being reported alongside
    # it.
    controls: dict[str, object] = {}
    control_violations: list[str] = []
    for arm in DECOUPLING_ARMS:
        control_stats = _statistics(
            _decouple(observations_rows, arm=arm, seed=decoupling_seed),
            bootstrap_samples=bootstrap_samples,
            bootstrap_seed=bootstrap_seed,
        )
        collapsed = (
            float(control_stats["family_macro_pearson"]) < min_family_correlation
            or float(control_stats["within_case_pairwise_concordance"])
            < min_pairwise_concordance
        )
        control_stats["collapsed_as_required"] = collapsed
        controls[arm] = control_stats
        if not collapsed:
            control_violations.append(arm)

    if control_violations:
        decision = "BLOCKED_TELEMETRY_SHORTCUT_SUSPECTED"
    elif passed:
        decision = "PASS"
    else:
        decision = "BLOCKED_FEEDBACK_NOT_IDENTIFIABLE"

    observations = int(true_arm["candidate_observation_count"])
    payload: dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "decision": decision,
        "model_calls": 0,
        "cases_file_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "case_count": len(cases),
        "family_count": true_arm["family_count"],
        "candidate_observation_count": observations,
        "feedback_schema_version": FEEDBACK_SCHEMA_VERSION,
        "feedback_uses_gold": False,
        "semantic_cluster_vocabulary": vocabulary.to_manifest(),
        "semantic_cluster_context_coverage": {
            "count": len(semantic_observations),
            "missing_count": sum(row.missing for row in semantic_observations),
            "missing_rate": (sum(row.missing for row in semantic_observations) / len(semantic_observations) if semantic_observations else 0.0),
            "source_counts": dict(sorted(Counter(row.source for row in semantic_observations).items())),
        },
        "semantic_cluster_missing_count": sum(row.missing for row in semantic_observations),
        "semantic_cluster_missing_rate": (sum(row.missing for row in semantic_observations) / len(semantic_observations) if semantic_observations else 0.0),
        "semantic_cluster_source_coverage": dict(sorted(Counter(row.source for row in semantic_observations).items())),
        "ecology_window_count": ecology_windows,
        "ecology_ledger_event_types": sorted(
            {str(event["event_type"]) for event in ecology.ledger.events}
        ),
        "shadow_used_for_audit_only": True,
        "candidate_level_pearson": true_arm["candidate_level_pearson"],
        "family_macro_pearson": family_correlation,
        "family_bootstrap_lower_95_one_sided": lower,
        "within_case_pairwise_concordance": concordance,
        "comparable_pair_count": true_arm["comparable_pair_count"],
        "thresholds": {
            "min_family_correlation": min_family_correlation,
            "min_bootstrap_lower": min_bootstrap_lower,
            "min_pairwise_concordance": min_pairwise_concordance,
        },
        "bootstrap_samples": bootstrap_samples,
        "bootstrap_seed": bootstrap_seed,
        "decoupling_seed": decoupling_seed,
        "decoupling_controls": controls,
        "decoupling_controls_all_collapsed": not control_violations,
        "surviving_decoupling_controls": control_violations,
        "probe_observation_counts": dict(sorted(probe_counts.items())),
        "probe_success_rates": {
            key: probe_successes[key] / count
            for key, count in sorted(probe_counts.items())
        },
        "telemetry_degeneracy": {
            "valid_rate": valid_count / observations,
            "rollback_rate": rollback_count / observations,
            "zero_change_rate": zero_change_count / observations,
            "exactly_one_change_rate": one_change_count / observations,
            "delayed_regression_observed": False,
            "target_resolution_observed": False,
            "annotation_consumption_observed": False,
        },
        "scope": "development_identifiability_audit_not_router_performance",
    }
    report = {**payload, "report_sha256": content_sha256(payload)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def audit_identifiability_v2(
    *, cases_path: Path, output: Path, bootstrap_samples: int = 10_000,
    bootstrap_seed: int = 24, decoupling_seed: int = 91,
    reference_is_fresh_replay: bool = False,
) -> dict[str, object]:
    """Typed-v2 audit entrypoint; never calls the v1 estimator or reward."""
    if bootstrap_samples < 100:
        raise ValueError("typed-v2 audit requires at least 100 bootstrap draws")
    if not isinstance(reference_is_fresh_replay, bool):
        raise ValueError("reference_is_fresh_replay must be boolean")
    if output.exists():
        raise ValueError(f"refusing to overwrite identifiability report: {output}")
    cases = load_cases(cases_path)
    semantic_rows = tuple(_semantic_cluster_observation(case) for case in cases)
    dev_size = max(1, len(cases) // 5)
    vocabulary = freeze_semantic_cluster_vocabulary(tuple(row.token for row in semantic_rows[:dev_size]))
    for row in semantic_rows:
        vocabulary.validate(row.token)
    observations = _typed_observations(cases)
    stats = _typed_statistics(observations, bootstrap_samples=bootstrap_samples, bootstrap_seed=bootstrap_seed)
    coverage = stats["candidate_observed_coverage"] >= V2_CANDIDATE_COVERAGE_THRESHOLD and stats["family_observed_coverage"] >= V2_FAMILY_COVERAGE_THRESHOLD and stats["pairwise_comparable_coverage"]["value"] is not None and stats["pairwise_comparable_coverage"]["value"] >= V2_COVERAGE_THRESHOLD
    claim_stats = dict(stats)
    partial_stats = None
    if not coverage:
        partial_stats = dict(stats)
        for key in ("candidate_level_pearson", "family_macro_pearson", "family_bootstrap_lower_95_one_sided", "within_case_pairwise_concordance", "comparable_pair_count"):
            claim_stats[key] = None
        partial_stats["eligible_for_claim"] = False
    controls: dict[str, object] = {}
    if not coverage:
        decision = "BLOCKED_TYPED_EVIDENCE_UNAVAILABLE"
        controls = {arm: {"status": "NOT_RUN_COVERAGE_BLOCKED"} for arm in DECOUPLING_ARMS}
    else:
        violations = []
        for arm in DECOUPLING_ARMS:
            decoupled = tuple(replace(row, telemetry=obs.telemetry) for row, obs in zip(observations, _decouple(tuple(_Observation(r.case_id, r.family_id, r.effect, r.telemetry, r.reference) for r in observations), arm=arm, seed=decoupling_seed), strict=True))
            control_rows = tuple(replace(row, telemetry=decoupled[index].telemetry) for index, row in enumerate(observations))
            control_stats = _typed_statistics(control_rows, bootstrap_samples=bootstrap_samples, bootstrap_seed=bootstrap_seed)
            collapsed = control_stats["pairwise_comparable_coverage"]["value"] is None or (control_stats["within_case_pairwise_concordance"] is None or control_stats["within_case_pairwise_concordance"] < 0.55)
            controls[arm] = {"status": "COLLAPSED" if collapsed else "SURVIVED", "statistics": control_stats}
            if not collapsed:
                violations.append(arm)
        if violations:
            decision = "BLOCKED_TELEMETRY_SHORTCUT_SUSPECTED"
        elif stats["candidate_level_pearson"] is not None and stats["family_macro_pearson"] is not None and stats["family_bootstrap_lower_95_one_sided"] is not None and stats["within_case_pairwise_concordance"] is not None and stats["family_macro_pearson"] >= .20 and stats["family_bootstrap_lower_95_one_sided"] >= .10 and stats["within_case_pairwise_concordance"] >= .55:
            decision = "PASS"
        else:
            decision = "BLOCKED_FEEDBACK_NOT_IDENTIFIABLE"
    manifest = v2_protocol_manifest(
        reference_is_fresh_replay=reference_is_fresh_replay
    )
    missing = sum(row.missing for row in semantic_rows)
    payload = {"schema_version": REPORT_V2_SCHEMA_VERSION, "feedback_schema_version": FEEDBACK_V2_SCHEMA_VERSION,
               "decision": decision, "model_calls": 0,
               "reference_is_fresh_replay": reference_is_fresh_replay,
               "reference": "materialized_recovery_gain_shadow_artifact", "protocol_manifest": manifest,
               "semantic_cluster_context_coverage": {"count": len(semantic_rows), "missing_count": missing, "missing_rate": missing / len(semantic_rows) if semantic_rows else 0.0, "source_counts": dict(sorted(Counter(row.source for row in semantic_rows).items()))},
               "semantic_cluster_vocabulary": vocabulary.to_manifest(), "typed_coverage": claim_stats,
               "thresholds": manifest["thresholds"], "decoupling_controls": controls,
               "shadow_used_for_audit_only": True}
    if partial_stats is not None:
        payload["non_claim_partial_diagnostics"] = partial_stats
    report = {**payload, "report_sha256": content_sha256(payload)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=24)
    parser.add_argument("--decoupling-seed", type=int, default=91)
    parser.add_argument("--feedback-version", choices=("v1", "typed-v2"), default="v1")
    args = parser.parse_args(argv)
    runner = audit_identifiability_v2 if args.feedback_version == "typed-v2" else audit_identifiability
    report = runner(cases_path=args.cases, output=args.output,
                    bootstrap_samples=args.bootstrap_samples,
                    bootstrap_seed=args.seed,
                    decoupling_seed=args.decoupling_seed)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["decision"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DECOUPLING_ARMS",
    "FEEDBACK_SCHEMA_VERSION",
    "FEEDBACK_V2_SCHEMA_VERSION",
    "REGISTERED_PROBES",
    "REPORT_SCHEMA_VERSION",
    "REPORT_V2_SCHEMA_VERSION",
    "V2_COVERAGE_THRESHOLD",
    "V2_CANDIDATE_COVERAGE_THRESHOLD",
    "V2_FAMILY_COVERAGE_THRESHOLD",
    "FORBIDDEN_RUNTIME_FIELDS",
    "ZERO_CALL_SUBSTRATE_CONFIG",
    "ZeroCallEcologyWindow",
    "SemanticClusterObservation",
    "assert_zero_call_fields",
    "freeze_semantic_cluster_vocabulary",
    "read_semantic_cluster",
    "record_zero_call_ecology_window",
    "validate_zero_call_substrate",
    "audit_identifiability",
    "audit_identifiability_v2",
    "deployment_feedback",
    "deployment_feedback_v2",
    "deployment_reward",
    "deployment_reward_v2",
    "pairwise_comparable_coverage",
    "v2_protocol_manifest",
    "main",
]
