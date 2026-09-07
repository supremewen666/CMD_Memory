"""Frozen deployment-observable evaluator for GHOST skill feedback.

The evaluator is a compact parametric memory over registered, runtime-visible
features.  Fitting is allowed only by the experiment protocol on development
labels.  Scoring never accepts a case/family identifier, gold value, recovery
gain, answer, or free text.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Mapping, Sequence

from cmd_audit.counterfactual.relation_graph import FrozenRelationGraph
from cmd_audit.repair.parametric_policy import PolicyContext, RepairIntent


EVALUATOR_SCHEMA_VERSION = "cmd-ghost-deployment-evaluator-v1"
FEATURE_SCHEMA_VERSION = "cmd-ghost-deployment-evaluator-features-v1"
DEFAULT_HASH_BUCKETS = 512
_FORBIDDEN = (
    "case", "family", "gold", "answer", "label", "recovery", "outcome",
    "utility", "intent_id", "item_id", "text",
)
_NUMERIC_FEATURES = (
    "annotate_relations",
    "destructive_relations",
    "positive_relations",
    "relation_edges",
    "retrieved_items",
    "uncertain_relations",
    "changed_item_count",
    "locality_cost",
    "valid",
    "rolled_back",
    "target_matches_actionability",
    "effect_matches_actionability_mode",
    "graph_edge_count",
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _bucket(value: str, buckets: int) -> int:
    return int(hashlib.sha256(value.encode()).hexdigest(), 16) % buckets


def observable_features(
    *,
    context: PolicyContext,
    graph: FrozenRelationGraph,
    intent: RepairIntent,
    telemetry: object,
) -> dict[str, object]:
    """Build a closed feature record without touching shadow-only attributes."""
    edge = next((row for row in graph.edges if row.edge_id == intent.relation_edge_id), None)
    if edge is None:
        raise ValueError("repair intent is absent from the frozen relation graph")
    changed = getattr(telemetry, "changed_item_count")
    if isinstance(changed, bool) or not isinstance(changed, int) or changed < 0:
        raise ValueError("changed_item_count must be a non-negative integer")
    locality = _finite(getattr(telemetry, "locality_cost"), "locality_cost")
    if locality < 0.0:
        raise ValueError("locality_cost must be non-negative")
    valid = getattr(telemetry, "valid")
    rolled_back = getattr(telemetry, "rolled_back")
    if not isinstance(valid, bool) or not isinstance(rolled_back, bool):
        raise TypeError("valid and rolled_back must be booleans")
    actionability = edge.actionability
    destructive = intent.effect in {"demote", "suppress", "replace"}
    target_match = bool(
        destructive
        and actionability.mode.value == "destructive"
        and intent.target_item_id == actionability.target_item_id
    )
    mode_match = bool(
        (destructive and actionability.mode.value == "destructive")
        or (
            intent.effect == "annotate_conflict"
            and actionability.mode.value == "annotate_only"
        )
        or (
            intent.effect in {"verify", "abstain"}
            and actionability.mode.value != "destructive"
        )
    )
    numeric = {
        name: _finite(context.features.get(name, 0.0), name)
        for name in _NUMERIC_FEATURES[:6]
    }
    numeric.update(
        {
            "changed_item_count": float(changed),
            "locality_cost": locality,
            "valid": float(valid),
            "rolled_back": float(rolled_back),
            "target_matches_actionability": float(target_match),
            "effect_matches_actionability_mode": float(mode_match),
            "graph_edge_count": float(len(graph.edges)),
        }
    )
    categorical = {
        "effect": intent.effect,
        "actionability_mode": actionability.mode.value,
        "semantic_cluster": context.semantic_cluster,
        "signal_signature": context.signal_signature,
        "domain": context.domain,
        "proposer": intent.proposer_id,
        "effect_x_mode": f"{intent.effect}|{actionability.mode.value}",
        "effect_x_semantic": f"{intent.effect}|{context.semantic_cluster}",
    }
    for key, value in (*numeric.items(), *categorical.items()):
        lowered = key.casefold()
        if any(marker in lowered for marker in _FORBIDDEN):
            raise ValueError(f"forbidden evaluator feature: {key}")
        if isinstance(value, str) and not value:
            raise ValueError(f"empty evaluator categorical feature: {key}")
    payload: dict[str, object] = {
        "schema_version": FEATURE_SCHEMA_VERSION,
        "numeric": dict(sorted(numeric.items())),
        "categorical": dict(sorted(categorical.items())),
    }
    return {**payload, "feature_sha256": _sha256(payload)}


def pre_action_features(
    *,
    context: PolicyContext,
    graph: FrozenRelationGraph,
    intent: RepairIntent,
) -> dict[str, object]:
    """Build evaluator features available before a candidate is executed.

    The V1 vector layout is retained for snapshot compatibility, but every
    post-action telemetry coordinate is fixed to its registered unknown/neutral
    value.  No candidate outcome object is accepted at this seam.
    """

    class _PreActionTelemetry:
        changed_item_count = 0
        locality_cost = 0.0
        valid = False
        rolled_back = False

    return observable_features(
        context=context,
        graph=graph,
        intent=intent,
        telemetry=_PreActionTelemetry(),
    )


def _vector(features: Mapping[str, object], buckets: int) -> dict[int, float]:
    if set(features) != {"schema_version", "numeric", "categorical", "feature_sha256"}:
        raise ValueError("observable evaluator feature record is not closed")
    payload = {key: features[key] for key in ("schema_version", "numeric", "categorical")}
    if (
        features["schema_version"] != FEATURE_SCHEMA_VERSION
        or features["feature_sha256"] != _sha256(payload)
    ):
        raise ValueError("observable evaluator feature hash/schema mismatch")
    numeric = features["numeric"]
    categorical = features["categorical"]
    if not isinstance(numeric, Mapping) or set(numeric) != set(_NUMERIC_FEATURES):
        raise ValueError("observable evaluator numeric schema mismatch")
    if not isinstance(categorical, Mapping):
        raise ValueError("observable evaluator categorical features must be a mapping")
    rows = {0: 1.0}
    for index, name in enumerate(_NUMERIC_FEATURES, 1):
        rows[index] = _finite(numeric[name], name)
    offset = 1 + len(_NUMERIC_FEATURES)
    effect = categorical.get("effect")
    if not isinstance(effect, str) or not effect:
        raise ValueError("observable evaluator effect is missing")
    for key, value in sorted(categorical.items()):
        if not isinstance(key, str) or not isinstance(value, str) or not value:
            raise ValueError("observable evaluator categorical feature is invalid")
        index = offset + _bucket(f"{key}={value}", buckets)
        rows[index] = rows.get(index, 0.0) + 1.0
    for key, value in sorted(numeric.items()):
        index = offset + _bucket(f"interaction:{effect}|{key}", buckets)
        rows[index] = rows.get(index, 0.0) + _finite(value, key)
    return rows


@dataclass(frozen=True)
class EvaluatorTrainingRow:
    features: Mapping[str, object]
    target: float

    def __post_init__(self) -> None:
        _finite(self.target, "evaluator training target")


@dataclass(frozen=True)
class FrozenDeploymentEvaluator:
    weights: tuple[float, ...]
    hash_buckets: int
    ridge: float
    training_feature_set_sha256: str
    training_provenance: str
    snapshot_sha256: str
    schema_version: str = EVALUATOR_SCHEMA_VERSION

    @classmethod
    def fit(
        cls,
        rows: Sequence[EvaluatorTrainingRow],
        *,
        ridge: float = 1.0,
        hash_buckets: int = DEFAULT_HASH_BUCKETS,
        training_provenance: str,
        max_iterations: int = 200,
        tolerance: float = 1e-10,
    ) -> "FrozenDeploymentEvaluator":
        if not rows:
            raise ValueError("deployment evaluator requires training rows")
        if ridge <= 0.0 or hash_buckets < 32:
            raise ValueError("invalid deployment evaluator regularization/dimension")
        if training_provenance != "ghost_dev_shadow_labels_only":
            raise ValueError("deployment evaluator may only fit the registered dev source")
        sparse = [(_vector(row.features, hash_buckets), float(row.target)) for row in rows]
        dimension = 1 + len(_NUMERIC_FEATURES) + hash_buckets
        right = [0.0] * dimension
        for vector, target in sparse:
            for index, value in vector.items():
                right[index] += value * target

        def multiply(vector: Sequence[float]) -> list[float]:
            result = [ridge * value for value in vector]
            for features, _target in sparse:
                projection = sum(vector[index] * value for index, value in features.items())
                for index, value in features.items():
                    result[index] += value * projection
            return result

        weights = [0.0] * dimension
        residual = right.copy()
        direction = residual.copy()
        residual_sq = sum(value * value for value in residual)
        initial_sq = residual_sq
        for _ in range(max_iterations):
            if residual_sq <= tolerance * tolerance * max(1.0, initial_sq):
                break
            product = multiply(direction)
            denominator = sum(x * y for x, y in zip(direction, product, strict=True))
            if denominator <= 0.0:
                raise ValueError("deployment evaluator normal matrix is not positive definite")
            alpha = residual_sq / denominator
            weights = [x + alpha * y for x, y in zip(weights, direction, strict=True)]
            next_residual = [x - alpha * y for x, y in zip(residual, product, strict=True)]
            next_sq = sum(value * value for value in next_residual)
            beta = next_sq / residual_sq
            direction = [
                x + beta * y for x, y in zip(next_residual, direction, strict=True)
            ]
            residual = next_residual
            residual_sq = next_sq
        if not all(math.isfinite(value) for value in weights):
            raise ValueError("deployment evaluator fit produced non-finite weights")
        training_hash = _sha256(
            sorted(str(row.features["feature_sha256"]) for row in rows)
        )
        body = {
            "schema_version": EVALUATOR_SCHEMA_VERSION,
            "weights": weights,
            "hash_buckets": hash_buckets,
            "ridge": float(ridge),
            "training_feature_set_sha256": training_hash,
            "training_provenance": training_provenance,
        }
        return cls(
            tuple(weights), hash_buckets, float(ridge), training_hash,
            training_provenance, _sha256(body),
        )

    def __post_init__(self) -> None:
        expected_dimension = 1 + len(_NUMERIC_FEATURES) + self.hash_buckets
        if self.schema_version != EVALUATOR_SCHEMA_VERSION or len(self.weights) != expected_dimension:
            raise ValueError("deployment evaluator snapshot schema/dimension mismatch")
        body = {
            "schema_version": self.schema_version,
            "weights": list(self.weights),
            "hash_buckets": self.hash_buckets,
            "ridge": self.ridge,
            "training_feature_set_sha256": self.training_feature_set_sha256,
            "training_provenance": self.training_provenance,
        }
        if self.snapshot_sha256 != _sha256(body):
            raise ValueError("deployment evaluator snapshot hash mismatch")

    def score(self, features: Mapping[str, object]) -> float:
        vector = _vector(features, self.hash_buckets)
        value = sum(self.weights[index] * feature for index, feature in vector.items())
        return max(-1.0, min(1.0, value))

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "weights": list(self.weights),
            "hash_buckets": self.hash_buckets,
            "ridge": self.ridge,
            "training_feature_set_sha256": self.training_feature_set_sha256,
            "training_provenance": self.training_provenance,
            "snapshot_sha256": self.snapshot_sha256,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "FrozenDeploymentEvaluator":
        expected = {
            "schema_version", "weights", "hash_buckets", "ridge",
            "training_feature_set_sha256", "training_provenance", "snapshot_sha256",
        }
        if set(value) != expected:
            raise ValueError("deployment evaluator snapshot is not closed")
        return cls(
            tuple(float(row) for row in value["weights"]),
            int(value["hash_buckets"]), float(value["ridge"]),
            str(value["training_feature_set_sha256"]),
            str(value["training_provenance"]), str(value["snapshot_sha256"]),
            str(value["schema_version"]),
        )


__all__ = [
    "DEFAULT_HASH_BUCKETS",
    "EVALUATOR_SCHEMA_VERSION",
    "EvaluatorTrainingRow",
    "FEATURE_SCHEMA_VERSION",
    "FrozenDeploymentEvaluator",
    "observable_features",
    "pre_action_features",
]
