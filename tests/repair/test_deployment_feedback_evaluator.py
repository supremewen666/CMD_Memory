from __future__ import annotations

from dataclasses import dataclass

import pytest

from cmd_audit.repair.deployment_feedback_evaluator import (
    EvaluatorTrainingRow,
    FrozenDeploymentEvaluator,
    observable_features,
)
from tests.experiments.test_v4_prequential_runner import _case


@dataclass
class _Telemetry:
    changed_item_count: int
    locality_cost: float
    valid: bool = True
    rolled_back: bool = False

    @property
    def recovery_gain(self) -> float:
        raise AssertionError("observable evaluator read recovery_gain")


def test_observable_features_do_not_read_shadow_and_snapshot_replays() -> None:
    case = _case(0, probe_set="represented", family="f0")
    context, graph, intents = case.context, case.graph, case.intents
    first = observable_features(
        context=context,
        graph=graph,
        intent=intents[0],
        telemetry=_Telemetry(1, 0.1),
    )
    second = observable_features(
        context=context,
        graph=graph,
        intent=intents[0],
        telemetry=_Telemetry(0, 0.0),
    )
    evaluator = FrozenDeploymentEvaluator.fit(
        (EvaluatorTrainingRow(first, 0.8), EvaluatorTrainingRow(second, 0.1)),
        training_provenance="ghost_dev_shadow_labels_only",
    )
    restored = FrozenDeploymentEvaluator.from_mapping(evaluator.to_mapping())

    assert restored.score(first) == evaluator.score(first)
    assert restored.score(second) == evaluator.score(second)
    assert restored.snapshot_sha256 == evaluator.snapshot_sha256


def test_evaluator_fit_refuses_unregistered_training_provenance() -> None:
    case = _case(0, probe_set="represented", family="f0")
    context, graph, intents = case.context, case.graph, case.intents
    features = observable_features(
        context=context,
        graph=graph,
        intent=intents[0],
        telemetry=_Telemetry(1, 0.0),
    )
    with pytest.raises(ValueError, match="registered dev source"):
        FrozenDeploymentEvaluator.fit(
            (EvaluatorTrainingRow(features, 1.0),),
            training_provenance="ghost_cal",
        )
